"""agentware_lib.features.fanoutmq.mqbranch -- the merge-queue's git BRANCH + lane-repo primitives: ancestry, integration-branch creation, merge-progress detection, append-only union resolution, and per-lane repo resolution (layer: features).

Owns: the small, side-effect-bounded predicates and resolvers every merge-queue /
teardown step is built on -- `_is_ancestor` (the already-merged check),
`_ensure_integration_branch` (create the integration branch from a base ref when
absent), `_merge_in_progress` (the MERGE_HEAD guard that distinguishes a merge
that STARTED then conflicted from one REFUSED before starting -- the silent-drop
guard), `_union_resolve_conflict` (the deterministic append-only UNION that keeps
both sides and drops only the conflict markers), `_lane_repo_args` (one lane's
effective repo/kb_repo: explicit flag > stored lane record > global default) and
`_lanes_common_repo` (the single stored repo across the in-scope lanes, else a
fallback). This is the package's deepest LEAF: it imports no sibling.

Does NOT own: the integration ENGINE that calls these predicates
(`_mq_integrate_package` / `_mq_integrate_kb` / `_mq_emit` /
`cmd_fanout_merge_queue`) and the mq constants -> fanoutmq.mqintegrate; the lane
TEARDOWN path (`_teardown_blocker` / `_remove_worktree` / `cmd_fanout_teardown`)
and the `fanout` parser registrar (`register_fanout`) -> fanoutmq.mqteardown; the
fanout LANES/BASE half (lane-registry I/O, provisioning, merge-policy installer,
list, spin-up, `_run_gate`) -> agentware_lib.features.fanout, reached here as
`fanout.<name>`; the low-level git runner (`_git`) -> core.gitio.

Layer deps: features.fanoutmq.mqbranch -> core (gitio, BY NAME) + a same-layer
sibling edge to features.fanout -- DOWNWARD/same-layer only. No cli, no
retrieval, no ops, no knowledge, and no intra-package sibling import.

Determinism: pure git-predicate orchestration. `_union_resolve_conflict` is the
only writer and it is byte-deterministic (marker-stripped concatenation, both
sides kept in on-disk order); the `=======` separator is dropped ONLY inside a
conflict hunk so a legitimate `=======` line in normal content survives. INV-1.
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import os
import subprocess  # noqa: F401 -- resolves by-name gitio._git's LOAD_GLOBAL 'subprocess' (global-resolve gate)

import agentware_lib.features.fanout as fanout
from agentware_lib.core.gitio import _git


def _is_ancestor(repo, ancestor, descendant):
    """True iff `ancestor` is an ancestor of `descendant` (already-merged check)."""
    return _git(repo, "merge-base", "--is-ancestor",
                ancestor, descendant).returncode == 0


def _ensure_integration_branch(repo, branch, base):
    """Create `branch` from `base` (default HEAD) if absent. Returns (ok, error)."""
    if fanout._branch_exists(repo, branch):
        return True, None
    base_ref = base or "HEAD"
    cp = _git(repo, "branch", branch, base_ref)
    if cp.returncode != 0:
        return False, ("could not create %s from %s in %s: %s"
                       % (branch, base_ref, repo, cp.stderr.strip()))
    return True, None


def _merge_in_progress(repo):
    """True iff a merge is paused/in progress (MERGE_HEAD exists) in `repo`.

    Distinguishes a merge that STARTED then conflicted (MERGE_HEAD set) from one
    REFUSED before starting (e.g. a dirty work tree → no MERGE_HEAD), so the KB
    integrator never mistakes a refused merge for a clean one (silent-drop guard).
    """
    return _git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0


def _union_resolve_conflict(path):
    """Resolve a conflicted append-only file by UNION — keep BOTH sides, drop the
    git conflict markers. The `=======` separator is dropped only INSIDE a conflict
    hunk, so a legitimate `=======` line in normal content is preserved."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return False
    out, in_conflict = [], False
    for ln in lines:
        s = ln.rstrip("\r\n")
        if s.startswith("<<<<<<<"):
            in_conflict = True
            continue
        if in_conflict and s.startswith("======="):
            continue
        if s.startswith(">>>>>>>"):
            in_conflict = False
            continue
        out.append(ln)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out)
    return True


def _lane_repo_args(rec, args, default_repo, default_kb):
    """Effective (repo, kb_repo) for ONE lane: explicit CLI flag wins, else the
    value stored in the lane record at spin-up, else the global default. Makes the
    registry authoritative so a non-default-repo lane works without re-passing the
    flags on every subcommand."""
    repo = fanout._resolve_repo_arg(getattr(args, "repo", None),
                             rec.get("repo") or default_repo)
    kb_repo = fanout._resolve_repo_arg(getattr(args, "kb_repo", None),
                                rec.get("kb_repo") or default_kb)
    return repo, kb_repo


def _lanes_common_repo(reg, order, key, fallback):
    """The single stored repo across the in-scope lanes for `key` (repo|kb_repo),
    or `fallback` when none is stored or they disagree. merge-queue integrates onto
    ONE repo, so a consistent stored value lets the operator omit --repo/--kb-repo."""
    vals = set()
    for f in order:
        v = reg["lanes"].get(f, {}).get(key)
        if v:
            vals.add(os.path.abspath(os.path.expanduser(v)))
    return vals.pop() if len(vals) == 1 else fallback


_EXPORTS = [
    "_is_ancestor",
    "_ensure_integration_branch",
    "_merge_in_progress",
    "_union_resolve_conflict",
    "_lane_repo_args",
    "_lanes_common_repo",
]
