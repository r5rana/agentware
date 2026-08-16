"""agentware_lib.features.graphdag.graphstate -- what a node's state IS, derived from the lane's evidence (layer: features).

Owns: the load-bearing test (`_graph_node_is_load_bearing`), the checkpoint
verdict -> disposition mapping (`_graph_disposition`), the raw terminal-outcome
read (`_graph_raw_terminal_outcome`), the lane record lookup
(`_graph_lane_record`), the state derivation itself (`_graph_node_status`) and
the reconciliation pass for nodes that finished without being finalized
(`_graph_finalize_unfinalized`).
Does NOT own: the state VOCABULARY -> graphdag.graphconst; the manifest ->
graphdag.graphmanifest; the ready set -> graphdag.graphtopo; lane lifecycle ->
graphdag.graphlane; the driver -> graphdag.graphexec.

STATE IS DERIVED, NOT STORED
----------------------------
`_graph_node_status` recomputes a node's state from the evidence a lane left
behind -- its terminal outcome, its plan, its worklog, its wall-clock age --
rather than trusting a status field somebody remembered to write. That is
deliberate. The alternative fails in exactly the way an orchestrator must not: a
process that is killed between finishing work and recording it leaves a stored
status of "running" forever, and the scheduler either hangs on it or, worse,
dispatches it a second time.

Deriving means a crash costs nothing: `graph resume` re-reads the same evidence
and reaches the same conclusion the original run would have.

THE FOUR WAYS A NODE LOOKS DONE WITHOUT BEING DONE
--------------------------------------------------
The detail codes exist because these are genuinely different situations and
collapsing them loses the operator's next action:

  * DONE_UNFINALIZED — the work finished, the bookkeeping did not.
    `_graph_finalize_unfinalized` reconciles these rather than re-running them;
    re-running finished work is how a scheduler destroys it.
  * FAILED_WATCHDOG — no terminal outcome and past `GRAPH_NODE_TIMEOUT_HOURS`.
    A hung lane must become FAILED, because a scheduler that waits forever on a
    dead process is indistinguishable from one that has stopped.
  * DEGENERATE — the lane produced nothing usable at all.
  * PLAN_MISSING / AWAITING_MERGE — the lane ran but its output is not yet where
    the next node needs it.

`_graph_node_is_load_bearing` decides whether a failure HALTS the subtree or is
contained to the node. Getting that backwards in either direction is expensive:
containing a load-bearing failure lets dependents run on a broken foundation, and
halting on a contained one stops a run that should have continued.

Layer deps: features -> features (`graphconst` for states/dispositions/detail
codes, `graphmanifest._graph_dir`, `fanout.load_lanes`/`_lane_outcome` -- the
lane records ARE the evidence this module derives from), features -> ops
(`plangraph` for the plan status vocabulary, `planauthor.planstate` for the claim
and tolerant plan read, `metrics.metricsemit._read_metrics_jsonl`). Reads lane artifacts; writes nothing except
through the finalize pass.
Determinism: given the same on-disk evidence and the same `now`, the derived
state is identical -- the wall clock enters ONLY through the watchdog comparison,
which takes its `now` as a parameter so tests inject rather than sleep.
"""

import argparse
import json
import os

from agentware_lib.features.graphdag.graphconst import (
    GRAPH_BLOCK_SUBTREE,
    GRAPH_CONTINUE,
    GRAPH_DETAIL_AWAITING_MERGE,
    GRAPH_DETAIL_DEGENERATE,
    GRAPH_DETAIL_DONE_UNFINALIZED,
    GRAPH_DETAIL_FAILED_WATCHDOG,
    GRAPH_DETAIL_PLAN_MISSING,
    GRAPH_HALT,
    GRAPH_NODE_BLOCKED,
    GRAPH_NODE_DONE,
    GRAPH_NODE_FAILED,
    GRAPH_NODE_HALT,
    GRAPH_NODE_PENDING,
    GRAPH_NODE_RUNNING,
    GRAPH_NODE_TIMEOUT_HOURS,
    GRAPH_PAUSE,
    _GRAPH_CONTAINED_FAIL_OUTCOMES,
    _GRAPH_DISPOSITION_TABLE,
)
from agentware_lib.features.fanout import _lane_outcome, load_lanes
from agentware_lib.features.graphdag.graphmanifest import _graph_dir
from agentware_lib.ops.metrics.metricsemit import _read_metrics_jsonl
from agentware_lib.ops.plangraph import _PLAN_STATES, _read_plan_status
from agentware_lib.ops.planauthor.planstate import (
    _claim_age_hours,
    _load_plan_tolerant,
    _parse_claim,
    cmd_plan_set_state,
)


def _graph_node_is_load_bearing(node):
    """A node is LOAD-BEARING when a false premise threatens the WHOLE graph: a
    self-extension node, or one explicitly flagged `load_bearing`. Pure."""
    if not isinstance(node, dict):
        return False
    return bool(node.get("self_extension") or node.get("load_bearing"))


def _graph_disposition(outcome, node=None, manifest=None):
    """Map a node's terminal signal (a loop LOOP_OUTCOME, a merge-queue verdict,
    or a synthetic scheduler signal) to a graph disposition — HALT / BLOCK_SUBTREE
    / CONTINUE / PAUSE. FAIL-CLOSED: any unknown/ambiguous signal -> HALT.

    `premise_falsified` splits on load-bearingness: a load-bearing shared-premise
    falsification is a systemic HALT (R-GND-01); a node-local premise falsification
    blocks only the transitive subtree. Pure; no clock, no RNG."""
    if outcome == "premise_falsified":
        return GRAPH_HALT if _graph_node_is_load_bearing(node) else GRAPH_BLOCK_SUBTREE
    return _GRAPH_DISPOSITION_TABLE.get(outcome, GRAPH_HALT)


def _graph_raw_terminal_outcome(kdir, feature, events=None):
    """The RAW `outcome` of the LAST meaningful terminal event in metrics.jsonl,
    preserved BY NAME (never mapped through _TERMINAL_OUTCOMES). Returns the string
    (`adversarial_review_blocked`, `blocked_on_ambiguity`, `premise_falsified`,
    `completed`, `quota_throttled`, ...) or None when no terminal row has arrived.

    This is the Task-4 correction to derive_outcome, which filters to
    _TERMINAL_OUTCOMES and so collapses the R-EXEC-06 hard-blocker outcomes to
    `unknown`, killing the HALT signal. The default LOOP_OUTCOME `unknown` is
    skipped (it asserts nothing — the shell consumer ignores it too), so the LAST
    DEFINITE outcome wins even if a later run exited before setting one. Read-only."""
    if events is None:
        events = _read_metrics_jsonl(kdir, feature=feature)
    outcome = None
    for e in events:
        if (e.get("event") == "terminal" and e.get("feature") == feature
                and isinstance(e.get("outcome"), str)
                and e.get("outcome") != "unknown"):
            outcome = e["outcome"]
    return outcome


def _graph_lane_record(feature, lanes=None):
    """The fanout lane registry record for `feature` (read-only), or None when the
    node has no lane yet. `lanes` is an already-loaded registry (so the scheduler
    reads the registry once per tick, not once per node); when None it is loaded."""
    if lanes is None:
        lanes, _err = load_lanes()
        if lanes is None:
            return None
    return (lanes.get("lanes") or {}).get(feature)


def _graph_node_status(kdir, node, now, node_timeout_hours=None, lanes=None,
                       events=None):
    """Resolve a graph node's LIVE status from (plan header, raw terminal outcome,
    lane outcome, claim-age watchdog) — the record the scheduler's ready-set +
    disposition consume. PURE + READ-ONLY; `now` (ISO) is injectable.

    `node` is a manifest node dict (carrying `feature`, and `self_extension`/
    `load_bearing` which feed the load-bearing HALT-vs-BLOCK split via
    _graph_disposition). Returns:
        {feature, status, plan_state, raw_outcome, lane_outcome, merged,
         disposition, detail, claim, age_hours}
    where `status` ∈ GRAPH_NODE_* (ONLY done+merged -> GRAPH_NODE_DONE satisfies a
    dependent), `disposition` ∈ GRAPH_* (or None when the node has produced no
    graph-level signal yet), and `detail` is a precise GRAPH_DETAIL_*/raw-outcome
    tag or None.

    Fail-closed (R-AUTO default-HALT): a missing/unreadable plan, an unknown/
    invalid plan state, or an unrecognized terminal outcome resolves to HALT —
    never silently `running`."""
    feature = node.get("feature") if isinstance(node, dict) else node
    timeout = (node_timeout_hours if node_timeout_hours is not None
               else GRAPH_NODE_TIMEOUT_HOURS)
    rec = {
        "feature": feature, "status": GRAPH_NODE_PENDING, "plan_state": None,
        "raw_outcome": None, "lane_outcome": None, "merged": False,
        "disposition": None, "detail": None, "claim": None, "age_hours": None,
    }

    # --- plan header (single-writer six-state machine) -------------------------
    text, err = _load_plan_tolerant(kdir, feature)
    if err is not None:
        rec["status"] = GRAPH_NODE_HALT
        rec["plan_state"] = "missing"
        rec["disposition"] = GRAPH_HALT
        rec["detail"] = GRAPH_DETAIL_PLAN_MISSING
        return rec
    plan_state, count = _read_plan_status(text)
    rec["plan_state"] = plan_state
    if count > 1:
        # Duplicate `> Status:` header — the plan is wedged (hand-edit / merge
        # residue). DEGENERATE: block THIS node's subtree, let independent branches
        # continue (never a whole-graph HALT).
        rec["status"] = GRAPH_NODE_BLOCKED
        rec["plan_state"] = "degenerate"
        rec["disposition"] = GRAPH_BLOCK_SUBTREE
        rec["detail"] = GRAPH_DETAIL_DEGENERATE
        return rec
    if plan_state not in _PLAN_STATES:
        # An invalid/foreign state string in the header — fail closed.
        rec["status"] = GRAPH_NODE_HALT
        rec["disposition"] = GRAPH_HALT
        rec["detail"] = "unknown_plan_state:%s" % plan_state
        return rec

    # --- lane outcome (filesystem-inferred) + merged flag ----------------------
    lane_rec = _graph_lane_record(feature, lanes=lanes)
    if lane_rec is not None:
        rec["lane_outcome"] = _lane_outcome(lane_rec)
        rec["merged"] = bool(lane_rec.get("merged"))

    # --- raw terminal outcome (BY NAME, never via derive_outcome) --------------
    raw = _graph_raw_terminal_outcome(kdir, feature, events=events)
    rec["raw_outcome"] = raw

    # --- claim + per-node wall-clock watchdog ----------------------------------
    claim = _parse_claim(text)
    rec["claim"] = claim
    age = _claim_age_hours(claim["ts"], now) if claim else None
    rec["age_hours"] = age

    # --- terminal plan states --------------------------------------------------
    if plan_state == "done":
        node_self_ext = bool(isinstance(node, dict) and node.get("self_extension"))
        if rec["merged"]:
            rec["status"] = GRAPH_NODE_DONE          # done + merged — dep-satisfying
            rec["disposition"] = GRAPH_CONTINUE
        elif lane_rec is None and node_self_ext:
            # A self-extension node is run by the OPERATOR directly in the main
            # checkout (R-PKG-03) — it is NEVER dispatched into a fanout lane, so
            # there is no lane to merge and its edit is ALREADY in the tree. Treat
            # done + no-lane as dep-satisfying; otherwise the merge-queue would be
            # handed a lane-less node, return 'halt-package', and HALT the graph
            # forever after every operator-run self-extension node (audit finding).
            rec["status"] = GRAPH_NODE_DONE
            rec["disposition"] = GRAPH_CONTINUE
            rec["detail"] = "selfext-operator-run"
        else:
            # Finalized but not yet integrated: the scheduler runs the merge-queue.
            # NOT dep-satisfying until the lane is merged onto `integration`.
            rec["status"] = GRAPH_NODE_RUNNING
            rec["detail"] = GRAPH_DETAIL_AWAITING_MERGE
        return rec
    if plan_state == "superseded":
        rec["status"] = GRAPH_NODE_BLOCKED           # does NOT satisfy dependents
        rec["disposition"] = GRAPH_BLOCK_SUBTREE
        rec["detail"] = "superseded"
        return rec
    if plan_state == "cancelled":
        rec["status"] = GRAPH_NODE_BLOCKED
        rec["disposition"] = GRAPH_BLOCK_SUBTREE
        rec["detail"] = "cancelled"
        return rec
    if plan_state in ("draft", "ready"):
        rec["status"] = GRAPH_NODE_PENDING           # not yet started — dispatchable
        return rec

    # --- plan_state == "running": the loop is (or was) in flight ---------------
    # Failure is NEVER written as a plan state — a failed loop honestly STAYS
    # `running`; its verdict lives ONLY in the metrics terminal event.
    if raw == "completed":
        # Straggler: loop finished cleanly but running->done didn't land. Finalize
        # idempotently (see _graph_finalize_unfinalized). NOT dep-satisfying until
        # finalized AND merged.
        rec["status"] = GRAPH_NODE_RUNNING
        rec["detail"] = GRAPH_DETAIL_DONE_UNFINALIZED
        rec["disposition"] = GRAPH_CONTINUE
        return rec
    if raw is not None:
        # A definite non-`completed` terminal outcome: map it through the shared
        # disposition table (fail-closed HALT on any unknown). Preserve the raw
        # NAME in `detail` so the R-EXEC-06 blockers survive by name (not `unknown`).
        disp = _graph_disposition(raw, node=node)
        rec["disposition"] = disp
        rec["detail"] = raw
        if disp == GRAPH_HALT:
            rec["status"] = GRAPH_NODE_HALT
        elif disp == GRAPH_PAUSE:
            rec["status"] = GRAPH_NODE_RUNNING       # quota pause — resumable, not failed
        elif raw in _GRAPH_CONTAINED_FAIL_OUTCOMES:
            rec["status"] = GRAPH_NODE_FAILED        # contained — block subtree only
        else:
            # BLOCK_SUBTREE from a non-contained source (e.g. node-local
            # premise_falsified) — leave the node blocked.
            rec["status"] = GRAPH_NODE_BLOCKED
        return rec

    # No terminal row yet — the lane is genuinely running. Apply the per-node
    # wall-clock watchdog: a claim older than the timeout with no terminal is a
    # hung lane (FAILED_WATCHDOG), distinct from the 72h stale-claim recovery.
    if age is not None and timeout is not None and age >= timeout:
        rec["status"] = GRAPH_NODE_FAILED
        rec["disposition"] = GRAPH_BLOCK_SUBTREE
        rec["detail"] = GRAPH_DETAIL_FAILED_WATCHDOG
        return rec
    rec["status"] = GRAPH_NODE_RUNNING
    return rec


def _graph_finalize_unfinalized(kdir, feature, now=None, actor="loop"):
    """Idempotently finalize a DONE_UNFINALIZED straggler — a node whose loop
    emitted a `completed` terminal but whose plan header is still `running` — by
    taking the single legal running->done edge through the SOLE state writer
    (cmd_plan_set_state), gated on open-markers==0. The single-writer invariant is
    preserved: the `> Status:` header is NEVER touched directly.

    Idempotent: a plan already `done` (or not `running`) is a no-op success. The
    open-markers==0 gate is cmd_plan_set_state's own; if a marker is still open the
    finalize REFUSES (the loop did not truly finish). The write resolves the KB
    from AGENTWARE_KNOWLEDGE_DIR (require_knowledge_dir), which the scheduler pins
    to `kdir`. Returns (ok, message)."""
    text, err = _load_plan_tolerant(kdir, feature)
    if err is not None:
        return False, err
    state, count = _read_plan_status(text)
    if count > 1:
        return False, "duplicate `> Status:` header — cannot finalize"
    if state == "done":
        return True, "already done (no-op)"
    if state != "running":
        return False, "not a running straggler (state=%s)" % state
    ns = argparse.Namespace(
        feature=feature, state="done", actor=actor, reason=None,
        superseded_by=None, handle=None, host=None, now=now, format="text")
    rc = cmd_plan_set_state(ns)
    if rc == 0:
        return True, "finalized running -> done"
    return False, "set-state running -> done refused (open markers or gate)"


_EXPORTS = [
    "_graph_node_is_load_bearing",
    "_graph_disposition",
    "_graph_raw_terminal_outcome",
    "_graph_lane_record",
    "_graph_node_status",
    "_graph_finalize_unfinalized",
]

__all__ = list(_EXPORTS)
