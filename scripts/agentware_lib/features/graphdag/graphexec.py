"""agentware_lib.features.graphdag.graphexec -- the TICK LOOP that actually runs the DAG (layer: features).

Owns: `_graph_execute`, the driver that ticks the graph to completion, and
`_graph_finish`, the terminal bookkeeping.
Does NOT own: the vocabulary -> graphdag.graphconst; the manifest ->
graphdag.graphmanifest; the ready set and conflict edges -> graphdag.graphtopo;
node state -> graphdag.graphstate; lane spawn and merge -> graphdag.graphlane;
the ledger, status and reports -> graphdag.graphledger; the verbs ->
graphdag.graphrun.

ONE TICK: DERIVE, DISPATCH, WAIT, RECONCILE
-------------------------------------------
Each tick recomputes every node's state from on-disk evidence
(`graphstate._graph_node_status`), recomputes the ready frontier
(`graphtopo._graph_ready_set`), dispatches what fits under the concurrency bound,
and reconciles what finished. Nothing is carried in memory between ticks that
could not be rebuilt from disk -- which is precisely why `graph resume` after a
crash is the same code path as a normal tick rather than a special one.

EVERY LOOP HAS A BOUND
----------------------
`GRAPH_TICK_CEILING` bounds the number of ticks, `GRAPH_NODE_TIMEOUT_HOURS`
bounds a single node, and the concurrency window bounds how much runs at once.
This driver spawns real agent processes against a real repository, so an
unbounded loop does not fail loudly -- it burns quota overnight while reporting
progress. Each bound turns a hang into a reported, terminal outcome someone can
act on.

`GRAPH_DEADLINE_HOURS` is the whole-run wall-clock bound and is the one bound
that is CONDITIONAL, so this paragraph says so rather than listing it beside the
unconditional three. It is enforced by `_graph_deadline_exceeded` against the
INJECTABLE `clock` seam, and it is therefore inert -- deterministically, by
design -- whenever no clock is injected, which is every caller today. Before task
99 wave 6 the guard's body was literally `pass`: the bound did not exist at all,
while `--deadline` parsed and threaded a value all the way to it. Wiring it and
saying plainly where it stops are the same change; the remaining half (a shipped
caller that injects a clock) is a behaviour change to a live scheduler and
carries its own decision.

A TICK THAT RE-ENTERS MUST MAKE PROGRESS
----------------------------------------
Two arms (`finalize`, `merge`) mutate state and restart the tick WITHOUT
advancing `waves`, so `GRAPH_TICK_CEILING` cannot bound them. Each therefore owns
its own progress guarantee: the finalize arm fails CLOSED on a refusal, and the
merge arm relies on `_graph_merge_lane` marking a lane merged before every
non-HALT return. `_graph_tick_finalize_or_merge`'s docstring carries the detail;
it is recorded here too because the property belongs to the LOOP, and a future
arm added beside them inherits the obligation.

A HALT STOPS A SUBTREE, NOT THE WORLD
-------------------------------------
When a load-bearing node fails, its transitive successors are blocked -- not the
whole run. Unrelated lanes keep going and finish. Blocking everything on any
failure would make a twenty-node graph as fragile as a shell script with `set -e`,
which defeats the reason for a DAG; containing NOTHING would let dependents build
on a broken foundation. `graphstate._graph_node_is_load_bearing` decides which,
and `graphtopo._graph_transitive_successors` says how far it reaches.

Layer deps: features -> features (the rest of the graphdag package), features ->
core. This is the only module in the package that both reads state and causes
side effects, and it is the only one that loops.
Determinism: the tick sequence is deterministic given the on-disk evidence; the
agents it spawns are not, which is why every conclusion is re-derived from disk
rather than remembered.
"""

from agentware_lib.features.graphdag.graphconst import (
    GRAPH_BLOCK_SUBTREE,
    GRAPH_CONCURRENCY_MAX,
    GRAPH_CONCURRENCY_MIN,
    GRAPH_CONTINUE,
    GRAPH_DEADLINE_HOURS,
    GRAPH_DEFAULT_CONCURRENCY,
    GRAPH_DETAIL_AWAITING_MERGE,
    GRAPH_DETAIL_DONE_UNFINALIZED,
    GRAPH_HALT,
    GRAPH_NODE_BLOCKED,
    GRAPH_NODE_DONE,
    GRAPH_NODE_FAILED,
    GRAPH_NODE_HALT,
    GRAPH_NODE_PENDING,
    GRAPH_NODE_RUNNING,
    GRAPH_PAUSE,
    GRAPH_TICK_CEILING,
)
from agentware_lib.core.config import resolve_knowledge_dir
from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.features.fanout import load_lanes
from agentware_lib.features.graphdag.graphlane import (
    _graph_bootstrap_integration,
    _graph_drive_lane,
    _graph_merge_gates,
    _graph_merge_lane,
    _graph_orchestrator_env_blockers,
    _graph_prepare_lane,
    _graph_select_dispatch,
)
from agentware_lib.features.graphdag.graphledger import (
    _graph_emit_metric,
    _graph_ledger_append,
    _graph_result,
)
from agentware_lib.features.graphdag.graphmanifest import (
    _graph_build,
    _graph_node_self_ext,
)
from agentware_lib.features.graphdag.graphstate import (
    _graph_disposition,
    _graph_finalize_unfinalized,
    _graph_node_status,
)
from agentware_lib.features.graphdag.graphtopo import (
    _graph_derive_resource_edges,
    _graph_manifest_write_sets,
    _graph_ready_set,
    topo_order,
)
from agentware_lib.ops.metrics.metricsemit import _read_metrics_jsonl


def _graph_run_result(state, status, reason, **extra):
    """A `_graph_result` envelope stamped with the run counters every arm reports.

    The three counters live in ONE dict threaded through the tick helpers rather
    than in closure variables, because each arm is a separate function now and a
    helper that returned an envelope WITHOUT them would look right and quietly
    report a completed run as having dispatched nothing."""
    return _graph_result(status, reason, waves=state["waves"],
                         dispatched=state["dispatched"],
                         merged=state["merged"], **extra)


def _graph_execute_seams(spawn, merge, prepare, tick_ceiling):
    """Resolve the four injectable knobs to their shipped defaults.

    `tick_ceiling` is resolved with `is None` and NOT with `or`, because 0 is a
    legal ceiling (it means "do not tick at all") and `or` would silently promote
    it to GRAPH_TICK_CEILING — turning a caller's request for zero waves into a
    request for the maximum."""
    return (spawn or _graph_drive_lane,
            merge or _graph_merge_lane,
            prepare or _graph_prepare_lane,
            GRAPH_TICK_CEILING if tick_ceiling is None else tick_ceiling)


def _graph_deadline_start(clock):
    """The run's origin reading, or None when the deadline guard is disabled."""
    return None if clock is None else clock()


def _graph_deadline_exceeded(clock, deadline_hours, started):
    """True iff the INJECTED clock says the run has outlived `deadline_hours`.

    `clock` is a zero-argument callable returning SECONDS (e.g. `time.monotonic`).
    The guard is disabled — and the whole executor therefore deterministic —
    whenever `clock` or `deadline_hours` is None, which is the documented seam and
    is the case in every test.

    Task 99 wave 6 WIRED this. Its body was previously `pass`, so the whole-run
    wall-clock bound the module docstring advertises did not exist: `--deadline`
    parsed, defaulted to GRAPH_DEADLINE_HOURS, threaded all the way here and was
    discarded. It is now enforced on the seam the executor's own docstring
    describes. What is STILL not closed, and is recorded as this wave's decision
    rather than quietly fixed here: no shipped caller injects a clock, so
    `--deadline` remains inert in production until one does. Making the real
    clock the default is a behaviour change to a live scheduler, not a paydown."""
    if clock is None or deadline_hours is None or started is None:
        return False
    return (clock() - started) >= float(deadline_hours) * 3600.0


def _graph_execute_preflight(manifest, concurrency, environ, check_env):
    """The two whole-run refusals that precede every disk read: `(cc, halt)`.

    FAIL CLOSED on an orchestrator-exported kill switch, then RESOLVE and VALIDATE
    concurrency. The concurrency check REFUSES rather than clamps, so an operator
    who asked for 40 lanes confronts the cost instead of silently getting 16; and
    it rejects `bool` explicitly, because `True` is an `int` in Python and would
    otherwise pass as a concurrency of one."""
    if check_env:
        blk = _graph_orchestrator_env_blockers(environ)
        if blk:
            return None, _graph_result("halt", "orchestrator-kill-switch",
                                       offenders=blk)
    cc = manifest.get("concurrency", GRAPH_DEFAULT_CONCURRENCY)
    if concurrency is not None:
        cc = concurrency
    if isinstance(cc, bool) or not isinstance(cc, int) \
            or cc < GRAPH_CONCURRENCY_MIN or cc > GRAPH_CONCURRENCY_MAX:
        return None, _graph_result("halt", "bad-concurrency:%r" % cc)
    return cc, None


def _graph_execute_topology(manifest):
    """Derive resource edges ONCE and build the combined `logical ∪ resource`
    graph plus a topo rank for stable dispatch: `(topology, halt)`.

    Derived once because the manifest is immutable within a run — re-deriving per
    tick would let a hand-edit mid-run change the dispatch order underneath a
    graph the operator already approved by hash. The two ValueErrors are DISTINCT
    reasons on purpose: `resource-derive-cycle` means the write-set derivation
    contradicted itself, `cycle` means the operator's own declared edges do."""
    try:
        derived = _graph_derive_resource_edges(manifest)
    except ValueError as exc:
        return None, _graph_result("halt", "resource-derive-cycle:%s" % exc)
    full = {"nodes": manifest.get("nodes") or [],
            "edges": list(manifest.get("edges") or []) + derived}
    nodes, forward, reverse = _graph_build(full)
    try:
        order = topo_order(forward, nodes)
    except ValueError as exc:
        return None, _graph_result("halt", "cycle:%s" % exc)
    return {"graph": (nodes, forward, reverse), "nodes": nodes, "order": order,
            "rank": {feat: i for i, feat in enumerate(order)},
            "write_sets": _graph_manifest_write_sets(manifest)}, None


def _graph_execute_selfext(kdir, manifest):
    """Re-stamp every node's `self_extension` from its LIVE plan.md and return
    `(node_by_feat, self_ext)`.

    COMPUTED at execution time, never trusted from the (operator-editable,
    sha-pinned-only-as-graph.json) manifest field. The in-memory node dict is
    re-stamped so EVERY downstream consumer — the `self_ext` dispatch dict (the
    mandatory-manual-checkpoint HALT gate), `_graph_node_is_load_bearing`, and
    `_graph_merge_gates` (the `gate release --full` + `verify-premises`
    augmentation) — reads the computed value. Without this a plan.md that drifted
    to self-extension after `add-node`, or a hand-edited graph.json carrying
    `self_extension:false` on a genuinely self-ext plan, would be autonomously
    spawned with weakened gates (audit HIGH). Run/resume already refuse a
    plan-missing/unreadable node via `graph_manifest_errors(kdir=...)`, so the
    stored fallback is unreachable here."""
    node_by_feat = {n["feature"]: n for n in (manifest.get("nodes") or [])
                    if isinstance(n, dict) and isinstance(n.get("feature"), str)}
    self_ext = {}
    for f, n in node_by_feat.items():
        computed, _se_err = _graph_node_self_ext(
            kdir, f, stored=n.get("self_extension"))
        n["self_extension"] = computed
        self_ext[f] = computed
    return node_by_feat, self_ext


def _graph_execute_setup(kdir, manifest, *, repo, kb_repo, concurrency,
                         integration, bootstrap, check_env, environ):
    """Everything that happens ONCE per run, before the first tick: `(ctx, halt)`.

    The ORDER of the five stages is the contract, not an implementation detail,
    and it runs cheapest-and-most-refusable first: the in-process kill-switch and
    concurrency checks touch no disk; the `integration` bootstrap is the first
    step that WRITES (two git branches), so nothing may refuse after it that could
    have refused before it; the topology derivation is pure; the self-extension
    re-stamp reads every node's plan; and the lane registry read is last because
    it is the only one whose failure is about the fanout tree rather than about
    this graph."""
    cc, halt = _graph_execute_preflight(manifest, concurrency, environ, check_env)
    if halt is not None:
        return None, halt
    if repo is None:
        repo = REPO_ROOT
    if kb_repo is None:
        kb_repo = resolve_knowledge_dir()
    if bootstrap:
        ok, berr = _graph_bootstrap_integration(repo, kb_repo, integration)
        if not ok:
            return None, _graph_result("halt", "bootstrap-failed:%s" % berr)
    topology, halt = _graph_execute_topology(manifest)
    if halt is not None:
        return None, halt
    node_by_feat, self_ext = _graph_execute_selfext(kdir, manifest)
    reg, rerr = load_lanes()
    if rerr:
        return None, _graph_result("halt", "lane-registry:%s" % rerr)
    ctx = dict(topology)
    ctx.update({"repo": repo, "kb_repo": kb_repo, "cc": cc, "reg": reg,
                "integration": integration, "node_by_feat": node_by_feat,
                "self_ext": self_ext})
    return ctx, None


def _graph_tick_statuses(kdir, ctx, now, node_timeout_hours):
    """Recompute EVERY node's live status from plan header + raw metrics + lane
    registry: `(records, statuses)`.

    ONE metrics read per tick, shared by every node — the file is append-only and
    grows with the run, so a per-node read would make a tick quadratic in a graph
    whose whole point is to be wide. Nothing is carried between ticks, which is
    exactly why `graph resume` after a crash is this same code path."""
    events = _read_metrics_jsonl(kdir)
    records, statuses = {}, {}
    for f in ctx["nodes"]:
        r = _graph_node_status(kdir, ctx["node_by_feat"].get(f, {"feature": f}),
                               now, node_timeout_hours=node_timeout_hours,
                               lanes=ctx["reg"], events=events)
        records[f] = r
        statuses[f] = r["status"]
    return records, statuses


def _graph_tick_halt_or_pause(state, order, records, statuses):
    """The two WHOLE-RUN stops, in priority order. Returns an envelope or None.

    HALT outranks PAUSE, and the order is load-bearing rather than incidental: a
    tick holding both a systemically-halted node and a quota-paused one must
    report the HALT, because `graph resume` is the documented answer to a pause
    and resuming into an unaddressed halt would loop the operator. Both scan
    `order` (the topo linearization) rather than a set, so the node NAMED in the
    envelope is deterministic when several qualify."""
    halted = [f for f in order if statuses[f] == GRAPH_NODE_HALT]
    if halted:
        f = halted[0]
        return _graph_run_result(state, "halt",
                                 records[f].get("detail") or "node-halt", node=f)
    # Quota throttle is NOT a failure: a paused node checkpoints gracefully and is
    # resumable. PAUSE the whole run (never HALT, never burn against a wall) so
    # `graph resume` picks up from the exact frontier in the morning.
    paused = [f for f in order
              if statuses[f] == GRAPH_NODE_RUNNING
              and records[f].get("disposition") == GRAPH_PAUSE]
    if paused:
        f = paused[0]
        return _graph_run_result(state, "paused", "quota-throttle:%s" % f, node=f,
                                 pending=[g for g in order
                                          if statuses[g] != GRAPH_NODE_DONE])
    return None


def _graph_tick_finalize_or_merge(kdir, state, ctx, order, records, statuses,
                                  now, merge):
    """The two arms that MUTATE and then re-derive: `(envelope, again)`.

    `again` True means "restart the tick WITHOUT advancing `waves`", because both
    arms change the very statuses the tick was computed from. That re-entry is
    also the hazard both arms are shaped around: a `continue` that does not
    advance `waves` skips the GRAPH_TICK_CEILING backstop, so an arm that can
    re-enter without making progress spins forever.

      * FINALIZE fails CLOSED for exactly that reason. A DONE_UNFINALIZED
        straggler whose finalize is REFUSED (an open `> LEARNED:`/`> DECISION:`/
        task marker blocks running->done, a duplicate/missing `> Status:` header,
        or an unreadable plan) can NEVER become done autonomously, so it HALTs for
        the operator instead of being retried (adversarial-review finding;
        test_unfinalizable_straggler_halts_instead_of_hanging).
      * MERGE has the same shape and is saved by a CROSS-MODULE invariant rather
        than by a guard here: `_graph_merge_lane` sets `rec["merged"]` before
        every non-HALT return, so the merged node's status advances and the arm
        cannot be re-entered on it. That invariant is pinned by its own test. A
        `merge` seam that returned a non-HALT status while leaving the lane
        unmerged WOULD spin this loop — which is why the seam is injectable for
        tests only and the shipped implementation is the one that must hold.

    FINALIZE outranks MERGE: a straggler is not yet eligible to merge, so running
    the merge arm first would hand the queue a node whose plan still says
    `running`."""
    unfinalized = [f for f in order
                   if statuses[f] == GRAPH_NODE_RUNNING
                   and records[f].get("detail") == GRAPH_DETAIL_DONE_UNFINALIZED]
    if unfinalized:
        for f in unfinalized:
            ok, _fmsg = _graph_finalize_unfinalized(kdir, f, now=now)
            if not ok:
                return _graph_run_result(
                    state, "halt", "unfinalizable-straggler:%s" % f, node=f), False
        return None, True
    awaiting = [f for f in order
                if statuses[f] == GRAPH_NODE_RUNNING
                and records[f].get("detail") == GRAPH_DETAIL_AWAITING_MERGE]
    if awaiting:
        f = awaiting[0]
        # Per-node review-then-proceed: hand the merge the EXTENDED gate chain
        # (always a superset of FANOUT_DEFAULT_GATES — never weaker).
        gates = _graph_merge_gates(ctx["node_by_feat"].get(f, {"feature": f}))
        mstatus = merge(ctx["reg"], f, ctx["repo"], ctx["kb_repo"],
                        ctx["integration"], gates)
        state["merged"].append({"feature": f, "status": mstatus})
        if _graph_disposition(mstatus) == GRAPH_HALT:
            return _graph_run_result(state, "halt", "merge:%s" % mstatus,
                                     node=f), False
        return None, True
    return None, False


def _graph_tick_terminal(state, nodes, statuses):
    """The two SUCCESS terminals, or None if the run continues.

    `empty` calls `_graph_result` directly rather than `_graph_run_result`. That
    is preserved verbatim from the pre-split body and is NOT a distinction: with
    no nodes nothing can have been dispatched or merged, so the two spellings
    produce the same envelope (`_graph_result` fills both keys with `[]` by
    default). Said explicitly because the asymmetry invites the opposite reading
    — that `empty` withholds the counters — and a comment claiming a difference
    that the envelope does not have is worse than no comment at all."""
    if nodes and all(statuses[f] == GRAPH_NODE_DONE for f in nodes):
        return _graph_run_result(state, "complete", "all-done")
    if not nodes:
        return _graph_result("complete", "empty", waves=state["waves"])
    return None


def _graph_tick_stalled(state, order, statuses):
    """Nothing dispatchable, nothing awaiting or unfinalized, not all done — the
    two ways a tick can be stuck, which must NOT be reported the same way.

    A node still RUNNING here neither finished, merged, nor failed: an ambiguous
    never-arrived terminal, so fail closed to HALT. Otherwise the remaining
    frontier is a CONTAINED failure/blocked subtree while every independent branch
    has completed — report `blocked`, do not HALT, because halting the world over
    a contained failure is what the subtree containment exists to prevent."""
    remaining = [f for f in order if statuses[f] != GRAPH_NODE_DONE]
    running_stuck = [f for f in remaining
                     if statuses[f] == GRAPH_NODE_RUNNING]
    if running_stuck:
        return _graph_run_result(state, "halt", "ambiguous-running",
                                 node=running_stuck[0], pending=remaining)
    return _graph_run_result(state, "blocked", "frontier-blocked",
                             pending=remaining)


def _graph_tick_select(state, ctx, order, statuses, retry):
    """Choose this wave's dispatch set: `(sel, envelope)`, exactly one non-None.

    On `--retry` (resume only) a FAILED node NOT already re-dispatched this run is
    remapped to PENDING so it is re-offered exactly ONCE (R-FAIL-04: never an
    unbounded identical retry — once dispatched it stays in `dispatched` and, if
    it fails again, is parked). Without `--retry` a FAILED node stays parked.

    A self-extension node is NEVER autonomously spawned — it HALTs for the
    mandatory manual checkpoint (R-PKG-03: the operator runs it themselves), and
    that HALT outranks the dispatch of everything else in the same wave, so a
    graph mixing a self-ext node with autonomous ones spawns nothing until the
    operator has dealt with it."""
    if retry:
        sched_statuses = {
            f: (GRAPH_NODE_PENDING
                if (s == GRAPH_NODE_FAILED and f not in state["dispatched"])
                else s)
            for f, s in statuses.items()}
    else:
        sched_statuses = statuses
    ready = _graph_ready_set(ctx["graph"], sched_statuses)
    ready.sort(key=lambda f: (ctx["rank"].get(f, 0), f))
    sel = _graph_select_dispatch(ready, ctx["write_sets"], ctx["self_ext"],
                                 ctx["cc"])
    if sel["halt_selfext"]:
        f = sel["halt_selfext"][0]
        return None, _graph_run_result(state, "halt",
                                       "awaiting-manual-selfext:%s" % f, node=f)
    if not sel["dispatch"]:
        return None, _graph_tick_stalled(state, order, statuses)
    return sel, None


def _graph_tick_spawn_wave(state, ctx, sel, prepare, spawn):
    """Provision each selected lane `--base integration`, drive it, then wait.

    Returns an envelope on a provisioning failure, else None. The wait is a WAVE
    BARRIER: every dispatched lane runs to termination before the next recompute,
    because the serial gated merge is the downstream chokepoint anyway and a
    partially-observed wave would make the next tick's status derivation race the
    lanes it is deriving from. A provisioning failure HALTs rather than skipping
    the node: the lanes already spawned in THIS wave are still recorded in
    `state["dispatched"]`, so the envelope names what is running."""
    handles = []
    for f in sel["dispatch"]:
        _action, perr = prepare(ctx["reg"], f, ctx["repo"], ctx["kb_repo"],
                                ctx["integration"])
        if perr:
            return _graph_run_result(state, "halt",
                                     "provision-failed:%s:%s" % (f, perr), node=f)
        rec = (ctx["reg"].get("lanes") or {}).get(f) or {"feature": f}
        handles.append((f, spawn(rec, f)))
        state["dispatched"].append(f)
    for _f, h in handles:
        if h is not None and hasattr(h, "wait"):
            h.wait()
    return None


def _graph_execute(kdir, manifest, now, *, repo=None, kb_repo=None,
                   concurrency=None, node_timeout_hours=None,
                   deadline_hours=GRAPH_DEADLINE_HOURS, integration="integration",
                   spawn=None, merge=None, prepare=None, environ=None,
                   tick_ceiling=None, bootstrap=True, check_env=True, clock=None,
                   retry=False):
    """The deterministic foreground WAVE executor.

    Modulo the injected seams (`spawn`/`merge`/`prepare`), a pure function of
    (topology, live plan+metric+lane states, injected `now`): each wave recomputes
    every node's live status, HALTs on any systemic signal, finalizes stragglers,
    merges the awaiting node, computes the ready-set, applies the cap +
    kernel-serialization + self-ext-halt selection, then provisions each selected
    lane `--base integration` and drives it through the spawn seam. Returns a
    `_graph_result` envelope. No clock/RNG feeds a scheduling decision; the only
    real-clock read is the OPTIONAL `--deadline` guard via the injectable `clock`
    seam (disabled — hence deterministic — when `clock` is None, e.g. every test).

    THE ORDER OF THE CALLS IN THE LOOP BODY IS THE SCHEDULER'S PRIORITY. It is not
    a style choice and it is not recoverable from the helpers, each of which is
    correct in isolation at any position: stop the world (halt, then pause) before
    changing it; make pending state true (finalize, then merge) and re-derive
    before concluding anything from it; declare terminal outcomes before selecting
    new work; and select before spawning. A test in this module's owning unit file
    asserts the sequence directly, because every arm below is one function call
    and a transposition would be invisible in review."""
    spawn, merge, prepare, ceiling = _graph_execute_seams(
        spawn, merge, prepare, tick_ceiling)
    ctx, halt = _graph_execute_setup(
        kdir, manifest, repo=repo, kb_repo=kb_repo, concurrency=concurrency,
        integration=integration, bootstrap=bootstrap, check_env=check_env,
        environ=environ)
    if halt is not None:
        return halt

    state = {"waves": 0, "dispatched": [], "merged": []}
    order = ctx["order"]
    started = _graph_deadline_start(clock)
    while state["waves"] < ceiling:
        if _graph_deadline_exceeded(clock, deadline_hours, started):
            return _graph_run_result(state, "halt", "deadline-exceeded")

        records, statuses = _graph_tick_statuses(kdir, ctx, now,
                                                 node_timeout_hours)
        res = _graph_tick_halt_or_pause(state, order, records, statuses)
        if res is not None:
            return res
        res, again = _graph_tick_finalize_or_merge(
            kdir, state, ctx, order, records, statuses, now, merge)
        if res is not None:
            return res
        if again:
            continue
        res = _graph_tick_terminal(state, ctx["nodes"], statuses)
        if res is not None:
            return res
        sel, res = _graph_tick_select(state, ctx, order, statuses, retry)
        if res is not None:
            return res
        res = _graph_tick_spawn_wave(state, ctx, sel, prepare, spawn)
        if res is not None:
            return res
        state["waves"] += 1

    return _graph_run_result(state, "halt", "tick-ceiling-exceeded")


def _graph_finish(kdir, name, result, now):
    """Record a run outcome to the NON-authoritative ledger + the additive metrics
    channel (a run-level `graph` event plus a `graph_node` event per merged node).
    Observability only — never the source of truth for resume."""
    _graph_ledger_append(kdir, name, {
        "event": "result", "graph": name, "status": result["status"],
        "reason": result["reason"], "waves": result["waves"],
        "node": result.get("node"), "ts": now})
    _graph_emit_metric(kdir, {
        "event": "graph", "graph": name, "phase": "result",
        "status": result["status"], "reason": result["reason"],
        "waves": result["waves"], "ts": now})
    for m in (result.get("merged") or []):
        _graph_emit_metric(kdir, {
            "event": "graph_node", "graph": name, "feature": m.get("feature"),
            "merge_status": m.get("status"), "ts": now})


_EXPORTS = [
    "_graph_run_result",
    "_graph_execute_seams",
    "_graph_deadline_start",
    "_graph_deadline_exceeded",
    "_graph_execute_preflight",
    "_graph_execute_topology",
    "_graph_execute_selfext",
    "_graph_execute_setup",
    "_graph_tick_statuses",
    "_graph_tick_halt_or_pause",
    "_graph_tick_finalize_or_merge",
    "_graph_tick_terminal",
    "_graph_tick_stalled",
    "_graph_tick_select",
    "_graph_tick_spawn_wave",
    "_graph_execute",
    "_graph_finish",
]

__all__ = list(_EXPORTS)
