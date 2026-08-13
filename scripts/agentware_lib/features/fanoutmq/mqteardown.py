"""agentware_lib.features.fanoutmq.mqteardown -- the lane TEARDOWN path + the `fanout` parser registrar: safe removal of merged lanes and the argparse wiring for all five sub-verbs (layer: features).

Owns: the teardown SAFETY gate (`_teardown_blocker`, the refusal reason for an
unmerged or dirty lane), the worktree remover (`_remove_worktree`, which prunes
stale metadata when the directory is already gone), the `fanout teardown` command
(`cmd_fanout_teardown` — worktrees BEFORE branches, `branch -D` only on a
confirmed-merged lane or under an explicit --force, never a `reset --hard` or a
force-push), and the `fanout` parser registrar (`register_fanout`), which wires
ALL FIVE sub-verbs: spin-up/list/merge-policy -> the base handlers in
features.fanout, merge-queue -> fanoutmq.mqintegrate, teardown -> here.

Does NOT own: the branch/ancestry/lane-repo predicates it is built on
(`_is_ancestor` / `_lane_repo_args` and their siblings) -> fanoutmq.mqbranch,
reached here as `mqbranch.<name>` so monkeypatching the OWNER propagates; the
merge-queue INTEGRATION engine + the mq constants (`_mq_integrate_package` /
`_mq_integrate_kb` / `_mq_emit` / `cmd_fanout_merge_queue` /
`FANOUT_APPEND_ONLY_PATHS` / `FANOUT_DEFAULT_GATES`) -> fanoutmq.mqintegrate,
reached as `mqintegrate.<name>`; the fanout LANES/BASE half (`load_lanes` /
`save_lanes` / `_branch_exists` / the three base `cmd_fanout_*` handlers) ->
agentware_lib.features.fanout; the low-level git runner (`_git`) -> core.gitio;
the repo root (`REPO_ROOT`) -> core.paths; config resolution
(`resolve_knowledge_dir`) -> core.config; the git work-tree STATE booleans
(`git_is_work_tree` / `git_is_clean`) -> ops.kbgit.

Layer deps: features.fanoutmq.mqteardown -> core (config/gitio/paths) + ops
(kbgit) + same-layer edges to features.fanout and the two fanoutmq siblings
(mqbranch, mqintegrate) -- ALL DOWNWARD or same-layer. No cli, no retrieval, no
knowledge, no upward edge. It is the package's TOP: nothing in the package
imports it, so the internal graph stays acyclic.

Determinism: pure orchestration. Teardown is REFUSAL-first (R-GIT-02): without
--force a lane is removed ONLY when it is fully merged into the integration
branch AND its worktrees are clean; otherwise it exits non-zero with an
actionable message (an R-EXEC-06 operator handoff, never an interactive prompt).
`register_fanout` is pure argparse assembly, so the rendered `fanout --help`
surface is byte-stable. INV-1 deterministic.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os
import subprocess  # noqa: F401 -- resolves by-name gitio._git's LOAD_GLOBAL 'subprocess' (global-resolve gate)
import sys

import agentware_lib.core.config as config
import agentware_lib.features.fanout as fanout
import agentware_lib.ops.kbgit as kbgit
from agentware_lib.core.gitio import _git
from agentware_lib.core.paths import REPO_ROOT

from . import mqbranch
from . import mqintegrate


def _teardown_blocker(rec, repo, kb_repo, integration, have_kb):
    """Return a refusal reason for an unmerged/dirty lane, else None."""
    for label, path in (("package worktree", rec.get("worktree", "")),
                        ("KB worktree", rec.get("kb_path", ""))):
        if os.path.isdir(path) and kbgit.git_is_work_tree(path) and not kbgit.git_is_clean(path):
            return "%s has uncommitted changes" % label
    if not fanout._branch_exists(repo, integration):
        return ("integration branch '%s' does not exist (nothing merged yet)"
                % integration)
    if not mqbranch._is_ancestor(repo, rec["feat_branch"], integration):
        return "%s is not merged into %s" % (rec["feat_branch"], integration)
    if have_kb and fanout._branch_exists(kb_repo, integration):
        if not mqbranch._is_ancestor(kb_repo, rec["kb_branch"], integration):
            return "%s is not merged into %s" % (rec["kb_branch"], integration)
    return None


def _remove_worktree(repo, path, force):
    """Remove a worktree (prune stale metadata if its dir is already gone)."""
    if not path or not os.path.exists(path):
        _git(repo, "worktree", "prune")
        return True, None
    cmd = ["worktree", "remove"]
    if force:
        cmd.append("--force")
    cmd.append(path)
    cp = _git(repo, *cmd)
    if cp.returncode != 0:
        return False, cp.stderr.strip()
    return True, None


def cmd_fanout_teardown(args):
    """Remove merged lanes' worktrees + branches; refuse unmerged/dirty lanes.

    Safety (R-GIT-02): without --force a lane is removed ONLY if it is fully merged
    into the integration branch AND its worktrees are clean; otherwise it is
    REFUSED deterministically (non-zero exit + message — an R-EXEC-06 operator
    handoff, never an interactive prompt). Worktrees are removed BEFORE their
    branches (a branch checked out by a live worktree cannot be deleted). NO
    `reset --hard` / force-push is ever run; `branch -D` runs only on a
    confirmed-merged lane (the safety gate proved no commits are lost) or under an
    explicit --force.
    """
    fmt_json = getattr(args, "format", "text") == "json"
    dry = getattr(args, "dry_run", False)
    force = getattr(args, "force", False)
    integration = getattr(args, "integration_branch", None) or "integration"

    reg, err = fanout.load_lanes()
    if err:
        sys.stderr.write("fanout teardown: %s\n" % err)
        return 1

    results, rc = [], 0
    for feature in args.features:
        rec = reg["lanes"].get(feature)
        if not rec:
            sys.stderr.write("fanout teardown: unknown lane: %s\n" % feature)
            rc = 1
            results.append({"feature": feature, "status": "error",
                            "detail": "unknown lane"})
            continue

        # Registry is authoritative for this lane's repos (recorded at spin-up);
        # --repo/--kb-repo override only when explicitly passed.
        repo, kb_repo = mqbranch._lane_repo_args(rec, args, REPO_ROOT,
                                        config.resolve_knowledge_dir())
        have_kb = bool(kb_repo) and kbgit.git_is_work_tree(kb_repo)

        if not force:
            reason = _teardown_blocker(rec, repo, kb_repo, integration, have_kb)
            if reason:
                sys.stderr.write("fanout teardown: refusing %s — %s "
                                 "(use --force to override).\n"
                                 % (feature, reason))
                rc = 1
                results.append({"feature": feature, "status": "refused",
                                "detail": reason})
                continue

        if dry:
            results.append({"feature": feature, "status": "would-remove"})
            continue

        ok, derr = _remove_worktree(repo, rec.get("worktree"), force)
        if not ok:
            sys.stderr.write("fanout teardown: %s: package worktree remove "
                             "failed: %s\n" % (feature, derr))
            rc = 1
            results.append({"feature": feature, "status": "error", "detail": derr})
            continue
        if have_kb:
            ok, derr = _remove_worktree(kb_repo, rec.get("kb_path"), force)
            if not ok:
                sys.stderr.write("fanout teardown: %s: KB worktree remove "
                                 "failed: %s\n" % (feature, derr))
                rc = 1
                results.append({"feature": feature, "status": "error",
                                "detail": derr})
                continue

        # Worktrees gone -> the branches are free to delete.
        _git(repo, "branch", "-D", rec["feat_branch"])
        if have_kb:
            _git(kb_repo, "branch", "-D", rec["kb_branch"])

        try:
            os.remove(rec.get("env_file", ""))
        except OSError:
            pass
        del reg["lanes"][feature]
        fanout.save_lanes(reg)
        results.append({"feature": feature, "status": "removed"})

    if fmt_json:
        json.dump({"dry_run": dry, "lanes": results}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        for r in results:
            if r["status"] in ("error", "refused"):
                continue
            print("fanout teardown: %s %s" % (r["feature"], r["status"]))
    return rc


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("fanout", 3500, "register_fanout"),
)


def register_fanout(sub, fmt_parent):
    # fanout -----------------------------------------------------------------
    p_fanout = sub.add_parser(
        "fanout",
        help="parallel-lane orchestration (isolated worktrees + gated merge queue)")
    fanout_sub = p_fanout.add_subparsers(
        dest="fanout_command", metavar="<subcommand>")
    fanout_sub.required = True

    p_fan_spin = fanout_sub.add_parser(
        "spin-up", parents=[fmt_parent],
        help="provision N isolated lanes (package + KB worktrees + env file)")
    p_fan_spin.add_argument("features", nargs="+", help="feature slug(s)")
    p_fan_spin.add_argument("--base", default=None,
                            help="package base ref for feat/<f> (default: HEAD)")
    p_fan_spin.add_argument("--kb-base", default=None, dest="kb_base",
                            help="KB base ref for kb/<f> (default: --base)")
    p_fan_spin.add_argument("--repo", default=None,
                            help="package repo (default: this agentware repo)")
    p_fan_spin.add_argument("--kb-repo", default=None, dest="kb_repo",
                            help="KB repo (default: the resolved knowledge dir)")
    p_fan_spin.add_argument("--dry-run", action="store_true", dest="dry_run",
                            help="print the plan; write nothing")
    p_fan_spin.set_defaults(func=fanout.cmd_fanout_spin_up)

    p_fan_list = fanout_sub.add_parser(
        "list", parents=[fmt_parent],
        help="report each active lane's branch, KB path, and loop outcome")
    p_fan_list.set_defaults(func=fanout.cmd_fanout_list)

    p_fan_mp = fanout_sub.add_parser(
        "merge-policy", parents=[fmt_parent],
        help="install the idempotent merge=union .gitattributes in the KB repo")
    p_fan_mp.add_argument("--kb", default=None,
                          help="KB repo dir (default: the resolved knowledge dir)")
    p_fan_mp.set_defaults(func=fanout.cmd_fanout_merge_policy)

    p_fan_mq = fanout_sub.add_parser(
        "merge-queue", parents=[fmt_parent],
        help="serially integrate finished lanes onto an integration branch (gated)")
    p_fan_mq.add_argument("features", nargs="*",
                          help="lanes to integrate (default: all registered lanes)")
    p_fan_mq.add_argument("--repo", default=None,
                          help="package repo (default: this agentware repo)")
    p_fan_mq.add_argument("--kb-repo", default=None, dest="kb_repo",
                          help="integration KB repo (default: resolved knowledge dir)")
    p_fan_mq.add_argument("--integration-branch", default=None,
                          dest="integration_branch",
                          help="integration branch name (default: integration)")
    p_fan_mq.add_argument("--base", default=None,
                          help="base ref to create the package integration branch")
    p_fan_mq.add_argument("--kb-base", default=None, dest="kb_base",
                          help="base ref to create the KB integration branch")
    p_fan_mq.add_argument("--gate", action="append", default=None,
                          help="post-merge gate command (repeatable; overrides "
                               "the default steering+eval+test gates)")
    p_fan_mq.add_argument("--no-gates", action="store_true", dest="no_gates",
                          help="skip the post-merge gates")
    p_fan_mq.add_argument("--dry-run", action="store_true", dest="dry_run",
                          help="print the plan; integrate nothing")
    p_fan_mq.set_defaults(func=mqintegrate.cmd_fanout_merge_queue)

    p_fan_td = fanout_sub.add_parser(
        "teardown", parents=[fmt_parent],
        help="remove merged lanes' worktrees + branches (refuses unmerged/dirty)")
    p_fan_td.add_argument("features", nargs="+", help="lane(s) to tear down")
    p_fan_td.add_argument("--repo", default=None,
                          help="package repo (default: this agentware repo)")
    p_fan_td.add_argument("--kb-repo", default=None, dest="kb_repo",
                          help="KB repo (default: the resolved knowledge dir)")
    p_fan_td.add_argument("--integration-branch", default=None,
                          dest="integration_branch",
                          help="integration branch for the merged check (default: integration)")
    p_fan_td.add_argument("--force", action="store_true",
                          help="remove even unmerged/dirty lanes (R-GIT-02 override)")
    p_fan_td.add_argument("--dry-run", action="store_true", dest="dry_run",
                          help="print what would be removed; remove nothing")
    p_fan_td.set_defaults(func=cmd_fanout_teardown)


_EXPORTS = [
    "_teardown_blocker",
    "_remove_worktree",
    "cmd_fanout_teardown",
    "register_fanout",
]
