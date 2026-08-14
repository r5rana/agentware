"""agentware_lib.features.updatecmd -- the `update` verb: fast-forward-only package self-update (layer: features).

Owns: the `update` command handler (cmd_update) + its authored-catalog diff helper
(_catalog_skill_names_at_ref) + the post-apply gate list (PKG_UPDATE_GATES) + the
argparse registrar (register_update) -- the user-initiated, fast-forward-only
self-update of the agentware PACKAGE repo (read-only fetch/status; apply re-runs
the R-PKG-04/05 package gates because it mutated package files).

Does NOT own: the low-level git runner (_git) -> agentware_lib.core.gitio; REPO_ROOT
-> agentware_lib.core.paths; require_knowledge_dir -> agentware_lib.core.config; the
git work-tree STATE helpers (git_is_work_tree / git_has_upstream / git_is_clean) ->
agentware_lib.ops.kbgit; the fetch / ff-pull runners (git_fetch / git_pull_ff) ->
agentware_lib.ops.staging; the package gate runner (_run_gate) ->
agentware_lib.features.fanout; the package pinned-ref reader (_pinned_ref) ->
agentware_lib.features.skills.

Layer deps: features.updatecmd -> core (config, gitio, paths) + ops (kbgit, staging)
DOWNWARD + features (fanout, skills) SAME-LAYER siblings (acyclic: neither imports
this). Homing this command in features.skills was rejected -- it would force a
skills->fanout sibling + two ops edges onto a deliberately-pure leaf module and
break its strict no-sibling BoundaryTest -- so the orchestrating command lives in
its own features command module (mirrors indexcmd / curationcmd / configcmd /
setupcmd).

A LEAF: nothing imports it at module level (fan-in 0); the scripts/agentware
entrypoint re-exports cmd_update + _catalog_skill_names_at_ref + PKG_UPDATE_GATES BY
NAME so the legacy load_cli oracle (CLI.cmd_update), the leaf-dispatch contract and
the global-resolve gate resolve them, and register_update is resolved by
agentware_lib.cli.assemble_parser via _PARSER_REGISTRATION importlib. The _run_gate /
_pinned_ref cross-module FUNCTIONS are reached via the fanout / _skills MODULE
aliases (never by-name imports, whose foreign __code__ the global-resolve gate would
walk against this namespace and FAIL).

Determinism: read-mostly. The ONLY mutation is the explicit `git pull --ff-only`
(never a merge commit, never destructive -- R-GIT-02), gated on a CLEAN work tree
plus a configured upstream; the fetch is read-only (remote-tracking refs only).
INV-1 deterministic (no ranking surface touched).
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import sys

import agentware_lib.core.config as config
import agentware_lib.core.gitio as gitio
import agentware_lib.core.paths as paths
import agentware_lib.ops.kbgit as kbgit
import agentware_lib.ops.staging as staging
import agentware_lib.features.fanout as fanout
import agentware_lib.features.skills as _skills


PKG_UPDATE_GATES = (
    "scripts/agentware steering lint",
    "scripts/agentware eval --record --gate",
)

def _catalog_skill_names_at_ref(ref):
    """Set of skill names in catalog/skills.json at a git ref, or None.

    Read-only (`git show <ref>:catalog/skills.json`, no checkout). Returns None
    when the blob is absent or unparseable so a `--catalog` diff can SKIP rather
    than mis-report. Pure git — no new dependency.
    """
    cp = gitio._git(paths.REPO_ROOT, "show", "%s:catalog/skills.json" % ref)
    if cp.returncode != 0:
        return None
    try:
        data = json.loads(cp.stdout)
    except (ValueError, TypeError):
        return None
    return {s.get("name") for s in data.get("skills", [])
            if isinstance(s, dict) and s.get("name")}

def cmd_update(args):
    """User-initiated, fast-forward-only self-update of the agentware PACKAGE.

    Modeled on `cmd_kb_git_pull` but targets REPO_ROOT (the package repo) and is
    explicit/manual, never an automatic background pull. Preconditions: a git
    work tree with a configured upstream. The fetch is READ-ONLY (touches only
    remote-tracking refs, never the work tree), so `--check` is safe for a
    SessionStart "updates available" nudge. Apply requires a CLEAN tree, runs
    `git pull --ff-only` (never a merge commit, never destructive — R-GIT-02),
    then re-runs the R-PKG-04 (steering lint) + R-PKG-05 (eval --record --gate)
    package gates because the apply mutated package files. `--catalog` adds a
    read-only diff of the authored skill catalog between HEAD and upstream so the
    operator sees which skills a pending update would add/remove.
    """
    target = paths.REPO_ROOT
    check_only = getattr(args, "check", False)
    show_catalog = getattr(args, "catalog", False)
    fmt = getattr(args, "format", "text")

    def emit(payload, lines, rc):
        if fmt == "json":
            json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            for line in lines:
                print(line)
        return rc

    if not kbgit.git_is_work_tree(target):
        return emit(
            {"ok": True, "status": "not-a-git-repo", "applied": False},
            ["update: package is not a git work tree — no self-update available."],
            0)

    if not kbgit.git_has_upstream(target):
        return emit(
            {"ok": True, "status": "no-upstream", "applied": False},
            ["update: no upstream tracking branch configured — skipping "
             "(set one with `git branch --set-upstream-to=origin/main`)."],
            0)

    # Read-only fetch: updates remote-tracking refs only; never the work tree.
    fetch = staging.git_fetch(target)
    if fetch.returncode != 0:
        detail = [l for l in (fetch.stderr or "").strip().splitlines() if l.strip()]
        first = detail[0] if detail else "fetch failed"
        return emit(
            {"ok": True, "status": "fetch-failed", "applied": False,
             "detail": first},
            ["update: fetch failed — %s (offline or no remote access; not "
             "blocking)." % first],
            0)

    upstream_ref = gitio._git(target, "rev-parse", "--abbrev-ref",
                        "--symbolic-full-name", "@{upstream}").stdout.strip()
    behind_s = gitio._git(target, "rev-list", "--count",
                    "HEAD..@{upstream}").stdout.strip()
    ahead_s = gitio._git(target, "rev-list", "--count",
                   "@{upstream}..HEAD").stdout.strip()
    behind = int(behind_s) if behind_s.isdigit() else 0
    ahead = int(ahead_s) if ahead_s.isdigit() else 0
    cur = _skills._pinned_ref()
    up_cp = gitio._git(target, "rev-parse", "--short", "@{upstream}")
    up = up_cp.stdout.strip() if up_cp.returncode == 0 else "unknown"

    changelog = []
    if behind:
        log = gitio._git(target, "log", "--oneline", "--no-decorate",
                   "HEAD..@{upstream}")
        if log.returncode == 0:
            changelog = [l for l in log.stdout.splitlines() if l.strip()]

    catalog_changes = None
    if show_catalog:
        up_names = _catalog_skill_names_at_ref("@{upstream}")
        if up_names is not None:
            cur_names = _catalog_skill_names_at_ref("HEAD") or set()
            catalog_changes = {
                "added": sorted(up_names - cur_names),
                "removed": sorted(cur_names - up_names),
            }

    lines = ["update: current %s  upstream %s (%s)" % (cur, up, upstream_ref)]
    if behind == 0:
        lines.append("update: already up to date (0 commit(s) behind upstream).")
    else:
        lines.append("update: %d commit(s) behind upstream:" % behind)
        for entry in changelog[:50]:
            lines.append("  %s" % entry)
        if len(changelog) > 50:
            lines.append("  … (%d more)" % (len(changelog) - 50))
    if ahead:
        lines.append("update: %d local commit(s) ahead of upstream "
                     "(blocks a fast-forward apply until pushed/rebased)." % ahead)
    if catalog_changes is not None:
        if catalog_changes["added"]:
            lines.append("update: catalog skills added upstream: %s"
                         % ", ".join(catalog_changes["added"]))
        if catalog_changes["removed"]:
            lines.append("update: catalog skills removed upstream: %s"
                         % ", ".join(catalog_changes["removed"]))
        if not catalog_changes["added"] and not catalog_changes["removed"]:
            lines.append("update: no catalog skill changes in this update.")

    payload = {
        "ok": True, "current": cur, "upstream": up, "upstream_ref": upstream_ref,
        "behind": behind, "ahead": ahead, "changelog": changelog,
        "catalog_changes": catalog_changes, "applied": False,
    }

    if check_only:
        payload["mode"] = "check"
        return emit(payload, lines, 0)

    payload["mode"] = "apply"
    if behind == 0:
        lines.append("update: nothing to apply.")
        return emit(payload, lines, 0)

    # Apply precondition: a CLEAN tree — never fast-forward onto uncommitted
    # package edits (R-GIT-02 non-destructive).
    if not kbgit.git_is_clean(target):
        payload["ok"] = False
        payload["error"] = "dirty-work-tree"
        lines.append("update: package work tree has uncommitted changes — "
                     "commit or stash them first (never applying onto "
                     "uncommitted edits).")
        return emit(payload, lines, 1)

    pull = staging.git_pull_ff(target)
    if pull.returncode != 0:
        detail = [l for l in (pull.stderr or "").strip().splitlines() if l.strip()]
        first = detail[0] if detail else "fast-forward refused"
        payload["ok"] = False
        payload["error"] = "ff-failed"
        lines.append("update: fast-forward apply failed — %s (diverged or "
                     "offline; nothing applied)." % first)
        return emit(payload, lines, 1)

    payload["applied"] = True
    lines.append("update: applied fast-forward to %s." % up)

    # R-PKG-04 / R-PKG-05: the apply mutated package files, so re-run the gates.
    gate_kdir = config.require_knowledge_dir() or target
    gate_results = []
    gate_failed = False
    for gate in PKG_UPDATE_GATES:
        rc, _out = fanout._run_gate(gate, target, gate_kdir)
        gate_results.append({"gate": gate, "rc": rc})
        lines.append("update: gate `%s` → %s"
                     % (gate, "PASS" if rc == 0 else "FAIL (rc %d)" % rc))
        if rc != 0:
            gate_failed = True
    payload["gates"] = gate_results
    if gate_failed:
        payload["ok"] = False
        payload["error"] = "gate-failed"
        lines.append("update: applied, but a post-update package gate FAILED — "
                     "review before relying on this package (R-PKG-04/05).")
        return emit(payload, lines, 1)
    lines.append("update: post-update gates passed "
                 "(R-PKG-04 steering lint, R-PKG-05 eval gate).")
    return emit(payload, lines, 0)

def register_update(sub, fmt_parent):
    # update -----------------------------------------------------------------
    # User-initiated, fast-forward-only self-update of the agentware PACKAGE
    # repo. Read-only fetch + status; apply re-runs the R-PKG-04/05 gates.
    p_update = sub.add_parser(
        "update", parents=[fmt_parent],
        help="self-update the agentware package (fast-forward-only; re-runs "
             "R-PKG-04/05 gates on apply)")
    p_update.add_argument(
        "--check", action="store_true",
        help="report current vs upstream + changelog, then STOP (read-only; "
             "safe for a SessionStart updates-available nudge)")
    p_update.add_argument(
        "--catalog", action="store_true",
        help="also show which authored skill-catalog entries the pending "
             "update would add/remove (read-only)")
    p_update.set_defaults(func=cmd_update)


_EXPORTS = [
    "PKG_UPDATE_GATES",
    "_catalog_skill_names_at_ref",
    "cmd_update",
]
