"""agentware_lib.features.fanoutmq.mqintegrate -- the merge-queue INTEGRATION engine: the serial package/KB integrators, the mq constants, the outcome renderer, and the `merge-queue` verb (layer: features).

Owns: the mq-only constants (`FANOUT_APPEND_ONLY_PATHS` — the derived ledgers a
conflict may be UNION-resolved on; `FANOUT_DEFAULT_GATES` — the post-merge gate
list when `--gate` is unset), the PACKAGE integrator (`_mq_integrate_package`, a
serial `merge --no-ff` into the integration branch that aborts and halts on a
monolith conflict rather than blanket-`-X`-ing a subcommand away), the KB
integrator (`_mq_integrate_kb`, which UNION-resolves only the append-only
ledgers, re-derives the index from the frontmatter union, runs the nothing-lost
ID-superset gate BEFORE committing, and HALTS on a same-entry prose conflict),
the outcome renderer (`_mq_emit`, text/json + the process exit code) and the
`fanout merge-queue` command (`cmd_fanout_merge_queue`, the resumable serial
queue: integrate package -> integrate KB -> run gates -> mark merged).

Does NOT own: the branch/ancestry/lane-repo predicates it is built on
(`_is_ancestor` / `_ensure_integration_branch` / `_merge_in_progress` /
`_union_resolve_conflict` / `_lane_repo_args` / `_lanes_common_repo`) ->
fanoutmq.mqbranch, reached here as `mqbranch.<name>` so monkeypatching the OWNER
propagates; the lane TEARDOWN path + the `fanout` parser registrar ->
fanoutmq.mqteardown; the fanout LANES/BASE half (`load_lanes` / `save_lanes` /
`_resolve_repo_arg` / `_lane_outcome` / `_run_gate`) ->
agentware_lib.features.fanout; the git runners (`_git` / `_git_noninteractive_env`)
-> core.gitio; the repo root (`REPO_ROOT`) -> core.paths; config resolution
(`resolve_knowledge_dir`) -> core.config; the frontmatter->index rebuild
(`rebuild_kb`) -> knowledge.index; the git conflict-introspection + derived-path
helpers (`git_unmerged_paths` / `_is_derived_rel`) -> ops.staging; the work-tree
STATE booleans (`git_is_work_tree` / `git_is_clean`) -> ops.kbgit; the
nothing-lost superset gate (`kb_nothing_lost_gate`) -> ops.kbsync.

Layer deps: features.fanoutmq.mqintegrate -> core (config/gitio/paths) +
knowledge (index) + ops (kbgit/kbsync/staging) + same-layer edges to
features.fanout and the fanoutmq.mqbranch sibling -- ALL DOWNWARD or same-layer.
No cli, no retrieval, no upward edge.

Determinism: pure orchestration — drives git worktrees serially (the monolith is
the embraced serial chokepoint), halts deterministically on a package/prose
conflict rather than a blanket `-X ours/theirs` that would silently drop a
subcommand, UNION-resolves only the append-only derived ledgers, and runs the
EXISTING gates. NEVER edits KB entry content, NEVER touches ranking/retrieval
(the moat is untouched). INV-1 deterministic.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os
import subprocess  # noqa: F401 -- resolves by-name gitio._git's LOAD_GLOBAL 'subprocess' (global-resolve gate)
import sys

import agentware_lib.core.config as config
import agentware_lib.knowledge.index as index
import agentware_lib.features.fanout as fanout
import agentware_lib.ops.kbgit as kbgit
import agentware_lib.ops.kbsync as kbsync
import agentware_lib.ops.staging as staging
from agentware_lib.core.gitio import _git, _git_noninteractive_env
from agentware_lib.core.paths import REPO_ROOT

from . import mqbranch


FANOUT_APPEND_ONLY_PATHS = ("benchmarks/history.jsonl", "benchmarks/SCORECARD.md")


FANOUT_DEFAULT_GATES = (
    "scripts/agentware steering lint",
    "scripts/agentware eval --record --gate",
    "python3 -m unittest discover -s tests",
)


def _mq_integrate_package(repo, integration, feat_branch):
    """Serially integrate a lane's feat branch into the package integration branch.

    Implemented as a serial `merge --no-ff` INTO integration (NOT a rebase of the
    lane branch): feat/<f> is checked out in the lane's worktree and so cannot be
    rebased from the main repo, and a merge gives the IDENTICAL never-drop +
    halt-on-monolith-conflict semantics WITHOUT a blanket `-X ours/theirs` (the
    exact mode that silently drops a subcommand). Returns (status, detail);
    status ∈ {merged, skipped, conflict, error}. On conflict/error the in-progress
    merge is aborted so the repo is left CLEAN for the operator (R-EXEC-06 handoff).
    """
    if mqbranch._is_ancestor(repo, feat_branch, integration):
        return "skipped", "already merged"
    co = _git(repo, "checkout", integration)
    if co.returncode != 0:
        return "error", "checkout %s failed: %s" % (integration, co.stderr.strip())
    env = _git_noninteractive_env()
    mg = _git(repo, "merge", "--no-ff", "--no-commit", feat_branch, env=env)
    unmerged = staging.git_unmerged_paths(repo)
    if unmerged:
        _git(repo, "merge", "--abort")
        return "conflict", "conflict in: %s" % ", ".join(unmerged)
    if not mqbranch._merge_in_progress(repo):
        # No MERGE_HEAD: either "Already up to date" (rc 0 — nothing to do) or a
        # merge REFUSED before it started (rc != 0, e.g. a dirty tree — fail loud).
        if mg.returncode == 0:
            return "skipped", "already up to date"
        return "error", (mg.stderr.strip() or mg.stdout.strip() or "merge refused")
    cm = _git(repo, "commit", "--no-edit",
              "-m", "fanout: integrate %s" % feat_branch, env=env)
    if cm.returncode != 0:
        _git(repo, "merge", "--abort")
        return "error", "commit failed: %s" % cm.stderr.strip()
    return "merged", "ok"


def _mq_integrate_kb(kb_repo, integration, kb_branch):
    """Integrate a lane's KB branch into the KB integration branch.

    Derived/append-only conflicts resolve DETERMINISTICALLY (the merge=union
    `.gitattributes` policy + `index rebuild`, which regenerates index.json /
    rosters / FEATURES.md from the frontmatter UNION — so a union'd-but-stale
    derived file is overwritten with the canonical union). A same-entry PROSE
    conflict (a NON-derived unmerged path) HALTS deterministically — it is
    reserved for the curated prose-merge path and is NEVER auto-resolved here.
    The nothing-lost ID-superset gate runs BEFORE the commit, so a lossy merge is
    rejected. Returns (status, detail); status ∈ {merged, skipped, prose-conflict,
    error}. On halt the in-progress merge is aborted (repo left clean).
    """
    if mqbranch._is_ancestor(kb_repo, kb_branch, integration):
        return "skipped", "already merged"
    co = _git(kb_repo, "checkout", integration)
    if co.returncode != 0:
        return "error", "checkout %s failed: %s" % (integration, co.stderr.strip())
    parent_integration = _git(kb_repo, "rev-parse", "HEAD").stdout.strip()
    env = _git_noninteractive_env()
    mg = _git(kb_repo, "merge", "--no-ff", "--no-commit", kb_branch, env=env)
    if not mqbranch._merge_in_progress(kb_repo):
        # No MERGE_HEAD: "Already up to date" (rc 0) OR a merge REFUSED before it
        # started (rc != 0, e.g. a dirty tree). NEVER fall through to commit — that
        # would record a non-merge as "merged" and SILENTLY DROP the lane's KB work.
        if mg.returncode == 0:
            return "skipped", "already up to date"
        _git(kb_repo, "merge", "--abort")
        return "error", (mg.stderr.strip() or mg.stdout.strip() or "merge refused")
    # Deterministically UNION the append-only ledgers (history.jsonl / SCORECARD.md),
    # independent of whether the merge=union `.gitattributes` policy was installed.
    for p in staging.git_unmerged_paths(kb_repo):
        if p in FANOUT_APPEND_ONLY_PATHS:
            if mqbranch._union_resolve_conflict(os.path.join(kb_repo, p)):
                _git(kb_repo, "add", "--", p)
    # Any remaining NON-derived unmerged path is a same-entry PROSE conflict — halt
    # for the curated prose-merge path (never auto-resolved here).
    non_derived = [p for p in staging.git_unmerged_paths(kb_repo) if not staging._is_derived_rel(p)]
    if non_derived:
        _git(kb_repo, "merge", "--abort")
        return "prose-conflict", ("same-entry conflict in: %s "
                                  "(reserved for the curated prose-merge path)"
                                  % ", ".join(non_derived))
    data, _rosters, errors = index.rebuild_kb(kb_repo)
    if errors:
        _git(kb_repo, "merge", "--abort")
        return "error", "index rebuild failed: %s" % "; ".join(errors)
    ok, note = kbsync.kb_nothing_lost_gate(kb_repo, [parent_integration, kb_branch])
    if not ok:
        _git(kb_repo, "merge", "--abort")
        return "error", note
    staging.kb_git_stage_scoped(kb_repo, allow_derived=True)
    cm = _git(kb_repo, "commit", "--no-edit",
              "-m", "fanout: integrate %s" % kb_branch, env=env)
    if cm.returncode != 0:
        _git(kb_repo, "merge", "--abort")
        return "error", "commit failed: %s" % cm.stderr.strip()
    return "merged", note


def _mq_emit(results, fmt_json, halt, msg):
    """Render the merge-queue outcome and return the process exit code."""
    if fmt_json:
        json.dump({"halted": halt, "lanes": results}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        for r in results:
            print("fanout merge-queue: %-26s %-14s %s"
                  % (r["feature"], r["status"], r.get("detail", "")))
    if halt and msg:
        sys.stderr.write(msg + "\n")
    return 1 if halt else 0


def cmd_fanout_merge_queue(args):
    """Serially integrate finished lanes onto an `integration` branch, gated.

    The monolith (`scripts/agentware`) is the serial chokepoint, embraced: lanes
    integrate ONE AT A TIME; on a package conflict the queue HALTS deterministically
    (non-zero + an actionable message naming the lane/branch/region) rather than a
    blanket `-X` that silently drops a feature. After EACH lane's package + KB
    merges succeed it runs the gates (steering lint + eval --record --gate + the
    test suite, by default) against the integration tree; any gate FAIL halts the
    queue. Resumable: already-merged lanes are skipped on re-run.
    """
    fmt_json = getattr(args, "format", "text") == "json"
    dry = getattr(args, "dry_run", False)
    integration = getattr(args, "integration_branch", None) or "integration"
    base = getattr(args, "base", None)
    kb_base = getattr(args, "kb_base", None)
    no_gates = getattr(args, "no_gates", False)
    gates = ([] if no_gates
             else list(getattr(args, "gate", None) or []) or list(FANOUT_DEFAULT_GATES))

    reg, err = fanout.load_lanes()
    if err:
        sys.stderr.write("fanout merge-queue: %s\n" % err)
        return 1

    requested = list(getattr(args, "features", None) or [])
    order = requested or sorted(reg["lanes"])
    missing = [f for f in order if f not in reg["lanes"]]
    if missing:
        sys.stderr.write("fanout merge-queue: unknown lane(s): %s\n"
                         % ", ".join(missing))
        return 1

    # Registry is authoritative: prefer the lanes' stored repo/kb_repo (recorded at
    # spin-up) over the global default, unless --repo/--kb-repo explicitly override.
    repo = fanout._resolve_repo_arg(getattr(args, "repo", None),
                             mqbranch._lanes_common_repo(reg, order, "repo", REPO_ROOT))
    kb_repo = fanout._resolve_repo_arg(getattr(args, "kb_repo", None),
                                mqbranch._lanes_common_repo(reg, order, "kb_repo",
                                                   config.resolve_knowledge_dir()))
    if not kbgit.git_is_work_tree(repo):
        sys.stderr.write("fanout merge-queue: package repo is not a git work "
                         "tree: %s\n" % repo)
        return 1
    have_kb = bool(kb_repo) and kbgit.git_is_work_tree(kb_repo)

    if dry:
        plan = [{"feature": f, "outcome": fanout._lane_outcome(reg["lanes"][f])}
                for f in order]
        if fmt_json:
            json.dump({"dry_run": True, "integration": integration,
                       "lanes": plan, "gates": gates},
                      sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            print("fanout merge-queue [dry-run]: integration=%s" % integration)
            for p in plan:
                print("  %s (%s)" % (p["feature"], p["outcome"]))
            for g in gates:
                print("  gate: %s" % g)
        return 0

    ok, ierr = mqbranch._ensure_integration_branch(repo, integration, base)
    if not ok:
        sys.stderr.write("fanout merge-queue: %s\n" % ierr)
        return 1
    if have_kb:
        ok, ierr = mqbranch._ensure_integration_branch(kb_repo, integration, kb_base)
        if not ok:
            sys.stderr.write("fanout merge-queue: %s\n" % ierr)
            return 1

    results = []
    for feature in order:
        rec = reg["lanes"][feature]
        # Skip ONLY fully-integrated lanes (package + KB + gates all passed in a
        # prior run). Do NOT short-circuit on package ancestry alone — a lane whose
        # package merged but whose KB/gate step halted must be RE-processable; the
        # per-step _is_ancestor guards make the package step an idempotent no-op on
        # resume while the KB step retries and the gates re-run.
        if rec.get("merged"):
            results.append({"feature": feature, "status": "skipped",
                            "detail": "already merged"})
            continue
        outcome = fanout._lane_outcome(rec)
        if outcome != "completed":
            results.append({"feature": feature, "status": "skipped",
                            "detail": "not finished (outcome=%s)" % outcome})
            continue

        # 1) Package monolith — the serial chokepoint.
        pstatus, pdetail = _mq_integrate_package(repo, integration,
                                                 rec["feat_branch"])
        if pstatus in ("conflict", "error"):
            results.append({"feature": feature, "status": "halt-package",
                            "detail": pdetail})
            return _mq_emit(
                results, fmt_json, True,
                "fanout merge-queue HALTED on lane '%s' — package merge %s "
                "(%s).\nResolve the monolith conflict by hand and re-run; the "
                "queue skips already-merged lanes. (NO blanket -X ours/theirs — "
                "that silently drops a subcommand.)" % (feature, pstatus, pdetail))

        # 2) KB — deterministic derived merge + nothing-lost gate.
        if have_kb:
            kstatus, kdetail = _mq_integrate_kb(kb_repo, integration,
                                                rec["kb_branch"])
            if kstatus in ("prose-conflict", "error"):
                results.append({"feature": feature, "status": "halt-kb",
                                "detail": kdetail})
                return _mq_emit(
                    results, fmt_json, True,
                    "fanout merge-queue HALTED on lane '%s' — KB merge %s (%s)."
                    % (feature, kstatus, kdetail))

        # 3) Package gates (R-PKG-04/05 + the test suite) on the integrated tree.
        gate_fail = None
        for g in gates:
            rc, output = fanout._run_gate(g, repo, kb_repo if have_kb else repo)
            if rc != 0:
                gate_fail = (g, rc, output)
                break
        if gate_fail:
            g, grc, output = gate_fail
            tail = "\n".join(output.strip().splitlines()[-8:])
            results.append({"feature": feature, "status": "halt-gate",
                            "detail": "gate %r exited %d" % (g, grc)})
            return _mq_emit(
                results, fmt_json, True,
                "fanout merge-queue HALTED after merging lane '%s' — gate "
                "FAILED:\n  $ %s\n  (exit %d)\n%s\nFix and re-run; already-merged "
                "lanes are skipped." % (feature, g, grc, tail))

        # A gate (e.g. `eval --record`) may have written ledger rows into the
        # integration KB, leaving its tree dirty. Commit those artifacts now so the
        # NEXT lane merges onto a CLEAN tree — a dirty tree would otherwise refuse
        # the next merge (which the silent-drop guard turns into a halt) and the
        # final lane's rows would never be committed.
        if have_kb and kbgit.git_is_work_tree(kb_repo) and not kbgit.git_is_clean(kb_repo):
            staging.kb_git_stage_scoped(kb_repo, allow_derived=True)
            _git(kb_repo, "commit", "--no-edit",
                 "-m", "fanout: gate artifacts for %s" % feature,
                 env=_git_noninteractive_env())

        rec["merged"] = True
        fanout.save_lanes(reg)
        results.append({"feature": feature, "status": "merged", "detail": "ok"})

    return _mq_emit(results, fmt_json, False, None)


_EXPORTS = [
    "FANOUT_APPEND_ONLY_PATHS",
    "FANOUT_DEFAULT_GATES",
    "_mq_integrate_package",
    "_mq_integrate_kb",
    "_mq_emit",
    "cmd_fanout_merge_queue",
]
