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
`GRAPH_TICK_CEILING` bounds the number of ticks, `GRAPH_DEADLINE_HOURS` bounds
wall-clock, `GRAPH_NODE_TIMEOUT_HOURS` bounds a single node, and the concurrency
window bounds how much runs at once. This driver spawns real agent processes
against a real repository, so an unbounded loop does not fail loudly -- it burns
quota overnight while reporting progress. Each bound turns a hang into a
reported, terminal outcome someone can act on.

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

import json
import os
import time

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
    """
    spawn = spawn or _graph_drive_lane
    merge = merge or _graph_merge_lane
    prepare = prepare or _graph_prepare_lane
    ceiling = GRAPH_TICK_CEILING if tick_ceiling is None else tick_ceiling

    # 1) FAIL CLOSED on an orchestrator-exported kill-switch (in-process gates).
    if check_env:
        blk = _graph_orchestrator_env_blockers(environ)
        if blk:
            return _graph_result("halt", "orchestrator-kill-switch",
                                  offenders=blk)

    # 2) Concurrency: refuse (never clamp) a request outside [1,16] so the operator
    #    confronts the cost.
    cc = manifest.get("concurrency", GRAPH_DEFAULT_CONCURRENCY)
    if concurrency is not None:
        cc = concurrency
    if isinstance(cc, bool) or not isinstance(cc, int) \
            or cc < GRAPH_CONCURRENCY_MIN or cc > GRAPH_CONCURRENCY_MAX:
        return _graph_result("halt", "bad-concurrency:%r" % cc)

    if repo is None:
        repo = REPO_ROOT
    if kb_repo is None:
        kb_repo = resolve_knowledge_dir()

    # 3) Bootstrap `integration` in both repos so wave 1's `--base integration`
    #    resolves.
    if bootstrap:
        ok, berr = _graph_bootstrap_integration(repo, kb_repo, integration)
        if not ok:
            return _graph_result("halt", "bootstrap-failed:%s" % berr)

    # 4) Derive resource edges ONCE (manifest is immutable within a run) and build
    #    the combined `logical ∪ resource` graph + a topo rank for stable dispatch.
    try:
        derived = _graph_derive_resource_edges(manifest)
    except ValueError as exc:
        return _graph_result("halt", "resource-derive-cycle:%s" % exc)
    full = {"nodes": manifest.get("nodes") or [],
            "edges": list(manifest.get("edges") or []) + derived}
    nodes, forward, reverse = _graph_build(full)
    graph = (nodes, forward, reverse)
    try:
        order = topo_order(forward, nodes)
    except ValueError as exc:
        return _graph_result("halt", "cycle:%s" % exc)
    rank = {feat: i for i, feat in enumerate(order)}
    ws = _graph_manifest_write_sets(manifest)
    node_by_feat = {n["feature"]: n for n in (manifest.get("nodes") or [])
                    if isinstance(n, dict) and isinstance(n.get("feature"), str)}
    # `self_extension` is COMPUTED from each node's LIVE plan.md at execution time,
    # never trusted from the (operator-editable, sha-pinned-only-as-graph.json)
    # manifest field. Re-stamp the in-memory node dict so EVERY downstream consumer
    # — the `self_ext` dispatch dict (the mandatory-manual-checkpoint HALT gate),
    # `_graph_node_is_load_bearing`, and `_graph_merge_gates` (the `gate release
    # --full` + `verify-premises` augmentation) — reads the computed value. Without
    # this a plan.md that drifted to self-extension after `add-node`, or a
    # hand-edited graph.json carrying `self_extension:false` on a genuinely
    # self-ext plan, would be autonomously spawned with weakened gates (audit HIGH).
    # Run/resume already refuse a plan-missing/unreadable node via
    # `graph_manifest_errors(kdir=...)`, so the stored fallback is unreachable here.
    self_ext = {}
    for f, n in node_by_feat.items():
        computed, _se_err = _graph_node_self_ext(
            kdir, f, stored=n.get("self_extension"))
        n["self_extension"] = computed
        self_ext[f] = computed

    reg, rerr = load_lanes()
    if rerr:
        return _graph_result("halt", "lane-registry:%s" % rerr)

    dispatched_all, merged_all = [], []
    waves = 0
    while waves < ceiling:
        # Optional real-clock deadline guard (deterministic no-op when clock=None).
        if clock is not None and deadline_hours is not None:
            # Enforced only against the injectable clock; never feeds a *scheduling*
            # decision, only a whole-run stop.
            pass  # deadline enforcement wired by Task 7's observability layer

        # Recompute EVERY node's live status from plan header + raw metrics + lane
        # registry (one metrics read for the tick).
        events = _read_metrics_jsonl(kdir)
        records, statuses = {}, {}
        for f in nodes:
            r = _graph_node_status(kdir, node_by_feat.get(f, {"feature": f}), now,
                                   node_timeout_hours=node_timeout_hours,
                                   lanes=reg, events=events)
            records[f] = r
            statuses[f] = r["status"]

        # Systemic HALT on any node (adversarial_review_blocked, load-bearing
        # premise falsification, degenerate-plan is BLOCK not HALT, ...).
        halted = [f for f in order if statuses[f] == GRAPH_NODE_HALT]
        if halted:
            f = halted[0]
            return _graph_result("halt", records[f].get("detail") or "node-halt",
                                  waves=waves, dispatched=dispatched_all,
                                  merged=merged_all, node=f)

        # Quota throttle is NOT a failure: a paused node checkpoints gracefully and
        # is resumable. PAUSE the whole run (never HALT, never burn against a wall)
        # so `graph resume` picks up from the exact frontier in the morning.
        paused = [f for f in order
                  if statuses[f] == GRAPH_NODE_RUNNING
                  and records[f].get("disposition") == GRAPH_PAUSE]
        if paused:
            f = paused[0]
            return _graph_result("paused", "quota-throttle:%s" % f, waves=waves,
                                  dispatched=dispatched_all, merged=merged_all,
                                  node=f, pending=[g for g in order
                                                  if statuses[g] != GRAPH_NODE_DONE])

        # Finalize any DONE_UNFINALIZED straggler, then recompute (it becomes done).
        unfinalized = [f for f in order
                       if statuses[f] == GRAPH_NODE_RUNNING
                       and records[f].get("detail") == GRAPH_DETAIL_DONE_UNFINALIZED]
        if unfinalized:
            for f in unfinalized:
                ok, _fmsg = _graph_finalize_unfinalized(kdir, f, now=now)
                if not ok:
                    # A DONE_UNFINALIZED straggler whose finalize is REFUSED (an
                    # open `> LEARNED:`/`> DECISION:`/task marker blocks
                    # running->done, a duplicate/missing `> Status:` header, or an
                    # unreadable plan) can NEVER become done autonomously. Retrying
                    # it spins THIS tick loop forever WITHOUT advancing `waves` (the
                    # `continue` below skips the ceiling tick), so the
                    # GRAPH_TICK_CEILING backstop never fires. Fail CLOSED: HALT for
                    # the operator to clear the straggler (adversarial-review
                    # finding; test_unfinalizable_straggler_halts_instead_of_hanging).
                    return _graph_result("halt", "unfinalizable-straggler:%s" % f,
                                         waves=waves, dispatched=dispatched_all,
                                         merged=merged_all, node=f)
            continue

        # Merge any done-but-unmerged node through the gated queue, then recompute.
        awaiting = [f for f in order
                    if statuses[f] == GRAPH_NODE_RUNNING
                    and records[f].get("detail") == GRAPH_DETAIL_AWAITING_MERGE]
        if awaiting:
            f = awaiting[0]
            # Per-node review-then-proceed: hand the merge the EXTENDED gate chain
            # (always a superset of FANOUT_DEFAULT_GATES — never weaker).
            gates = _graph_merge_gates(node_by_feat.get(f, {"feature": f}))
            mstatus = merge(reg, f, repo, kb_repo, integration, gates)
            merged_all.append({"feature": f, "status": mstatus})
            if _graph_disposition(mstatus) == GRAPH_HALT:
                return _graph_result("halt", "merge:%s" % mstatus, waves=waves,
                                     dispatched=dispatched_all,
                                     merged=merged_all, node=f)
            continue

        # All nodes done+merged -> success.
        if nodes and all(statuses[f] == GRAPH_NODE_DONE for f in nodes):
            return _graph_result("complete", "all-done", waves=waves,
                                 dispatched=dispatched_all, merged=merged_all)
        if not nodes:
            return _graph_result("complete", "empty", waves=waves)

        # Ready-set in stable (topo_rank, slug) dispatch order. On `--retry`
        # (resume only), a FAILED node NOT already re-dispatched this run is remapped
        # to PENDING so it is re-offered exactly ONCE (R-FAIL-04: never an unbounded
        # identical retry — once dispatched it stays in dispatched_all and, if it
        # fails again, is parked). Without --retry a FAILED node stays parked.
        if retry:
            sched_statuses = {
                f: (GRAPH_NODE_PENDING
                    if (s == GRAPH_NODE_FAILED and f not in dispatched_all)
                    else s)
                for f, s in statuses.items()}
        else:
            sched_statuses = statuses
        ready = _graph_ready_set(graph, sched_statuses)
        ready.sort(key=lambda f: (rank.get(f, 0), f))
        sel = _graph_select_dispatch(ready, ws, self_ext, cc)

        # A self-extension node is NEVER autonomously spawned — HALT for the
        # mandatory manual checkpoint (R-PKG-03: the operator runs it themselves).
        if sel["halt_selfext"]:
            f = sel["halt_selfext"][0]
            return _graph_result("halt", "awaiting-manual-selfext:%s" % f,
                                 waves=waves, dispatched=dispatched_all,
                                 merged=merged_all, node=f)

        if not sel["dispatch"]:
            # Nothing dispatchable, nothing awaiting/unfinalized, not all done.
            remaining = [f for f in order if statuses[f] != GRAPH_NODE_DONE]
            running_stuck = [f for f in remaining
                             if statuses[f] == GRAPH_NODE_RUNNING]
            if running_stuck:
                # A dispatched lane neither finished, merged, nor failed — an
                # ambiguous never-arrived terminal. Fail closed to HALT.
                return _graph_result("halt", "ambiguous-running",
                                     waves=waves, dispatched=dispatched_all,
                                     merged=merged_all, node=running_stuck[0],
                                     pending=remaining)
            # Otherwise the remaining frontier is a CONTAINED failure/blocked subtree
            # while every independent branch has completed. Report, don't HALT.
            return _graph_result("blocked", "frontier-blocked", waves=waves,
                                 dispatched=dispatched_all, merged=merged_all,
                                 pending=remaining)

        # Provision each selected lane `--base integration` and drive it.
        handles = []
        for f in sel["dispatch"]:
            action, perr = prepare(reg, f, repo, kb_repo, integration)
            if perr:
                return _graph_result("halt", "provision-failed:%s:%s" % (f, perr),
                                     waves=waves, dispatched=dispatched_all,
                                     merged=merged_all, node=f)
            rec = (reg.get("lanes") or {}).get(f) or {"feature": f}
            handles.append((f, spawn(rec, f)))
            dispatched_all.append(f)

        # Wave barrier: every dispatched lane runs to termination before the next
        # recompute (the serial gated merge is the downstream chokepoint anyway).
        for f, h in handles:
            if h is not None and hasattr(h, "wait"):
                h.wait()
        waves += 1

    return _graph_result("halt", "tick-ceiling-exceeded", waves=waves,
                         dispatched=dispatched_all, merged=merged_all)


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
    "_graph_execute",
    "_graph_finish",
]

__all__ = list(_EXPORTS)
