"""agentware_lib.features.graphdag.graphlane -- the LANE lifecycle: env, spawn, dispatch, merge (layer: features).

Owns: the orchestrator's own kill-switch check (`_graph_orchestrator_env_blockers`),
the sanitized child environment (`_graph_lane_env`), the spawn
(`_graph_spawn`), the per-lane driver (`_graph_drive_lane`), the dispatch choice
(`_graph_select_dispatch`), the reprovision test (`_graph_should_reprovision`),
lane preparation (`_graph_prepare_lane`), and the merge path with its gates
(`_graph_merge_lane` / `_graph_merge_gates` / `_graph_bootstrap_integration`).
Does NOT own: the vocabulary -> graphdag.graphconst; the ready set and write-set
conflict edges -> graphdag.graphtopo; node state -> graphdag.graphstate; the tick
loop that calls this -> graphdag.graphexec.

THE ENV IS AN ALLOWLIST, AND THAT IS THE SECURITY BOUNDARY
----------------------------------------------------------
`_graph_lane_env` builds the child environment from `GRAPH_LANE_ENV_ALLOW` --
naming what may pass THROUGH rather than what must be stripped. A denylist is the
wrong shape here: the set of variables that must not reach a spawned agent grows
every time anything in the system gains a new setting, and the one nobody
remembers to add is the one that leaks. An allowlist fails closed on the variable
nobody thought about.

`_graph_orchestrator_env_blockers` is checked BEFORE anything spawns: if a
kill-switch variable is set, the run refuses to start rather than starting and
discovering it later. An off switch that is honoured after the first spawn is not
an off switch.

MERGING IS GATED, BECAUSE A LANE'S SUCCESS IS ITS OWN CLAIM
-----------------------------------------------------------
A lane reports its own outcome. `_graph_merge_gates` re-checks the conditions
that matter before that lane's work is integrated, and refuses the merge if they
do not hold. Same discipline as the review gate's regression chain: the actor
being verified does not get to be the verifier. A merge path that trusted the
lane's self-report would integrate broken work whenever a lane was confidently
wrong -- and a confidently wrong agent is the normal case, not the edge case.

`_graph_should_reprovision` distinguishes a lane that needs a fresh workspace
from one that can be resumed in place, which is what makes `graph resume` cheap
enough to actually use after a crash.

Layer deps: features -> features (`graphconst`, `graphstate`, `graphtopo`, and
`fanout` + `fanoutmq` for the lane primitives -- provisioning, branch handling,
gates, integration and teardown are REUSED here rather than reimplemented, so a
graph lane and a fanout lane are the same kind of thing on disk), features ->
core (`gitio` for the merge and the non-interactive env). Spawns subprocesses; every one of them
goes through `_graph_spawn` so there is exactly ONE place the child env, cwd and
argv are decided.
Determinism: dispatch selection sorts, so a given ready set produces the same
dispatch order every run (INV-1); the spawned agents are of course not
deterministic, which is why the merge is gated rather than assumed.
"""

import os
import subprocess

from agentware_lib.features.graphdag.graphconst import (
    GRAPH_KILL_SWITCH_VARS,
    GRAPH_LANE_ENV_ALLOW,
    GRAPH_NODE_PENDING,
    GRAPH_NODE_RUNNING,
)
from agentware_lib.core.gitio import (
    _git,
    _git_noninteractive_env,
    _is_ancestor,
    git_is_clean,
    git_is_work_tree,
)
from agentware_lib.features.fanout import (
    _branch_exists,
    _fanout_lane_paths,
    _fanout_provision_lane,
    _run_gate,
    save_lanes,
)
from agentware_lib.features.fanoutmq.mqbranch import _ensure_integration_branch
from agentware_lib.features.fanoutmq.mqintegrate import (
    FANOUT_DEFAULT_GATES,
    _mq_integrate_kb,
    _mq_integrate_package,
)
from agentware_lib.features.fanoutmq.mqteardown import _remove_worktree
from agentware_lib.features.graphdag.graphstate import _graph_node_is_load_bearing
from agentware_lib.features.graphdag.graphtopo import _graph_write_set_touches_kernel


def _graph_orchestrator_env_blockers(environ=None):
    """The sorted list of kill-switch vars present-and-truthy in the orchestrator
    env. Non-empty => `graph run` MUST fail closed before dispatching, because the
    integrated-tree merge gates run in THIS process and would honour them. Pure."""
    env = os.environ if environ is None else environ
    return sorted(v for v in GRAPH_KILL_SWITCH_VARS if (env.get(v) or "").strip())


def _graph_lane_env(kb_path, environ=None):
    """Build a lane subprocess env by ALLOW-LIST (never os.environ inheritance).

    None of GRAPH_KILL_SWITCH_VARS can appear (they are simply not on the
    allow-list), so a lane can never silently disable its own premise/target/
    release/adversarial gates. AGENTWARE_KNOWLEDGE_DIR is pinned to the lane KB so
    the lane loop reads/writes ONLY its isolated worktree. Pure."""
    src = os.environ if environ is None else environ
    env = {}
    for k in GRAPH_LANE_ENV_ALLOW:
        v = src.get(k)
        if v is not None:
            env[k] = v
    env["AGENTWARE_KNOWLEDGE_DIR"] = kb_path
    env["GIT_TERMINAL_PROMPT"] = "0"   # never prompt for creds (R-SHELL-01)
    env["CI"] = "true"                 # non-interactive lane loop
    env["npm_config_yes"] = "true"     # never block on an npm prompt
    return env


def _graph_spawn(argv, cwd, env):
    """The monkeypatchable spawn SEAM. Launches a lane loop as a detached-enough
    subprocess and returns its handle (a Popen). Tests replace this attribute so
    NO real `./agentware.sh` ever runs. Kept a one-liner so the seam is the only
    thing a test must stub."""
    return subprocess.Popen(argv, cwd=cwd, env=env)


def _graph_drive_lane(rec, feature=None):
    """Drive ONE provisioned lane: `./agentware.sh <feature>` in the lane's package
    worktree, with an ALLOW-LIST env pinning the lane KB. Returns the process
    handle from the `_graph_spawn` seam. This IS the missing driver the whole
    feature is built around."""
    feature = feature or rec.get("feature")
    argv = ["./agentware.sh", feature]
    env = _graph_lane_env(rec.get("kb_path", ""))
    return _graph_spawn(argv, rec.get("worktree"), env)


def _graph_select_dispatch(ready, write_sets, self_ext, concurrency,
                           running_kernel=False, running=0):
    """PURE cap + serialization decision for one wave.

    `ready` is the ready-set already in stable dispatch order. Returns
    {"dispatch":[...], "halt_selfext":[...], "deferred":[...]}:
      - a self-extension node is NEVER autonomously dispatched — it is surfaced in
        `halt_selfext` (the graph HALTS for the mandatory manual checkpoint);
      - at most ONE lane whose write-set touches `scripts/agentware` runs at a time
        (kernel serialization) — a second is deferred to a later wave;
      - total in-flight never exceeds `concurrency`.
    No clock, no RNG — a pure function of the inputs."""
    dispatch, halt_selfext, deferred = [], [], []
    kernel_taken = bool(running_kernel)
    inflight = int(running)
    for feat in ready:
        if self_ext.get(feat):
            halt_selfext.append(feat)
            continue
        if inflight >= concurrency:
            deferred.append(feat)
            continue
        if _graph_write_set_touches_kernel(write_sets.get(feat, [])):
            if kernel_taken:
                deferred.append(feat)
                continue
            kernel_taken = True
        dispatch.append(feat)
        inflight += 1
    return {"dispatch": dispatch, "halt_selfext": halt_selfext,
            "deferred": deferred}


def _graph_should_reprovision(exists, integration_is_ancestor):
    """A provisioned lane must be RE-CUT when it exists but `integration` is no
    longer an ancestor of feat/<f> — i.e. the lane was branched off a now-stale
    integration HEAD (a prior tick/crash). `_fanout_provision_lane` early-returns
    'exists' ignoring the base, which would re-introduce the same-file conflict the
    `--base integration` invariant exists to prevent (audit). Pure."""
    return bool(exists) and not bool(integration_is_ancestor)


def _graph_prepare_lane(reg, feature, repo, kb_repo, integration):
    """Ensure `feature` has a lane cut off the CURRENT `integration` HEAD.

    A fresh feature is provisioned `--base integration --kb-base integration` (the
    invariant that turns a would-be same-file conflict into a clean apply). An
    EXISTING lane branched off a stale integration HEAD is torn down (force) and
    re-provisioned so the successor branches off a base that already contains every
    merged sibling. Returns (action, error); action ∈ {created, exists,
    reprovisioned}."""
    lane = _fanout_lane_paths(feature, repo, kb_repo)
    exists = (feature in reg["lanes"]) or _branch_exists(repo, lane["feat_branch"])
    if exists:
        anc = _is_ancestor(repo, integration, lane["feat_branch"])
        if not _graph_should_reprovision(True, anc):
            return "exists", None
        # Stale base -> tear the lane down and re-cut it off integration.
        rec = reg["lanes"].get(feature, {})
        _remove_worktree(repo, rec.get("worktree") or lane["worktree"], True)
        if kb_repo:
            _remove_worktree(kb_repo, rec.get("kb_path") or lane["kb_path"], True)
        _git(repo, "branch", "-D", lane["feat_branch"])
        if kb_repo:
            _git(kb_repo, "branch", "-D", lane["kb_branch"])
        reg["lanes"].pop(feature, None)
        action, err = _fanout_provision_lane(reg, lane, integration, integration,
                                             False)
        if err:
            return None, err
        return "reprovisioned", None
    return _fanout_provision_lane(reg, lane, integration, integration, False)


def _graph_merge_lane(reg, feature, repo, kb_repo, integration, gates=None):
    """Integrate ONE finished lane onto `integration` through the shipped serial
    gated merge-queue steps: package monolith merge (abort-on-conflict) -> KB
    derived merge (prose-conflict halt) -> gates on the INTEGRATED tree. Returns a
    status string the disposition table maps: merged | halt-package | halt-kb |
    halt-gate | skipped. `gates` defaults to FANOUT_DEFAULT_GATES; Task 6 EXTENDS
    (never weakens) it via `_graph_merge_gates`. This is the graph's per-node
    review-then-proceed, delegating to the same integrator `fanout merge-queue`
    uses."""
    rec = reg["lanes"].get(feature)
    if not rec:
        return "halt-package"
    if rec.get("merged"):
        return "skipped"
    glist = list(FANOUT_DEFAULT_GATES if gates is None else gates)
    have_kb = bool(kb_repo) and git_is_work_tree(kb_repo)

    pstatus, _pd = _mq_integrate_package(repo, integration, rec["feat_branch"])
    if pstatus in ("conflict", "error"):
        return "halt-package"
    if have_kb:
        kstatus, _kd = _mq_integrate_kb(kb_repo, integration, rec["kb_branch"])
        if kstatus in ("prose-conflict", "error"):
            return "halt-kb"
    for g in glist:
        grc, _out = _run_gate(g, repo, kb_repo if have_kb else repo)
        if grc != 0:
            return "halt-gate"
    # A gate (e.g. `eval --record`) may have dirtied the integration KB — commit
    # those artifacts so the NEXT lane merges onto a clean tree.
    if have_kb and git_is_work_tree(kb_repo) and not git_is_clean(kb_repo):
        _git(kb_repo, "add", "-A")
        _git(kb_repo, "commit", "--no-edit",
             "-m", "graph: gate artifacts for %s" % feature,
             env=_git_noninteractive_env())
    rec["merged"] = True
    save_lanes(reg)
    return "merged"


def _graph_merge_gates(node):
    """Build the per-node merge-gate list — EXTENDING (never weakening) the shipped
    default chain. Task 6 review-then-proceed: the existing gated merge-queue IS the
    automatic per-node review; this ADDS gates, removes none.

    Critically, `fanout merge-queue`'s `--gate` list REPLACES the defaults
    (`list(args.gate) or FANOUT_DEFAULT_GATES`), so a per-node list handed to the
    integrator MUST re-list `FANOUT_DEFAULT_GATES` VERBATIM (else passing an
    augmented list would silently DROP the defaults — the replace-not-append edge,
    audit). On top of the verbatim defaults:
      - a self-extension node (always a mandatory `manual` checkpoint) appends
        `gate release --full` (max-safety integrated-tree battery, R-PKG-06);
      - a LOAD-BEARING node (self-ext or explicitly `load_bearing`) appends a
        per-lane `plan verify-premises --strict` re-run AGAINST THE INTEGRATION
        TREE (`_run_gate` pins cwd=integration-repo + AGENTWARE_KNOWLEDGE_DIR=
        integration-KB, and the `$AGENTWARE_KNOWLEDGE_DIR`-rooted path resolves the
        plan in that KB) — catching a cross-lane premise falsification a sibling
        merge introduced (R-GND-01).
    Pure; no clock, no RNG. The result is always a SUPERSET of FANOUT_DEFAULT_GATES.
    """
    gates = list(FANOUT_DEFAULT_GATES)
    if not isinstance(node, dict):
        return gates
    if node.get("self_extension"):
        gates.append("scripts/agentware gate release --full")
    if _graph_node_is_load_bearing(node):
        feature = node.get("feature")
        gates.append(
            'scripts/agentware plan verify-premises '
            '--path "$AGENTWARE_KNOWLEDGE_DIR/work/%s/plan.md" --strict' % feature)
    return gates


def _graph_bootstrap_integration(repo, kb_repo, integration="integration"):
    """Create `integration` (from HEAD) in BOTH the package and KB repos BEFORE
    wave 1, so `--base integration` is a satisfiable ref on the opening wave.
    (`integration` is otherwise created lazily only INSIDE the merge-queue, i.e.
    after a lane finishes — the opening `git worktree add … integration` would fail
    with an invalid-ref; audit finding.) Returns (ok, error)."""
    if git_is_work_tree(repo):
        ok, err = _ensure_integration_branch(repo, integration, None)
        if not ok:
            return False, err
    if kb_repo and git_is_work_tree(kb_repo):
        ok, err = _ensure_integration_branch(kb_repo, integration, None)
        if not ok:
            return False, err
    return True, None


_EXPORTS = [
    "_graph_orchestrator_env_blockers",
    "_graph_lane_env",
    "_graph_spawn",
    "_graph_drive_lane",
    "_graph_select_dispatch",
    "_graph_should_reprovision",
    "_graph_prepare_lane",
    "_graph_merge_lane",
    "_graph_merge_gates",
    "_graph_bootstrap_integration",
]

__all__ = list(_EXPORTS)
