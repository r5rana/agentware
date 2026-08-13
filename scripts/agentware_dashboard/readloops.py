"""agentware_dashboard.readloops — the LOOP read model.

Owns: the payloads for the loop pillar — per-feature loop state and the active
run (`/api/loop`), the planner/worker activity views (`/api/agents`), the
first-class loop analytics (`/api/loop-analytics`), and the runaway-detection
badge in both its fleet and per-run forms (`/api/loop-health`,
`/api/loop-health/<feature>`).
Does NOT own: which features exist (`workfeatures`), cost/token aggregation
(`readcost`), or the per-run drill-downs of trace, tasks, failures and outcomes
(`readruns`). Every analytic itself belongs to `agentware_lib.ops.*`.

READ-ONLY: the panel state is built entirely from PERSISTED data (the `.loop`
counters, the plan markers, the metrics emission), so it is idle-resilient — a
loop that is not running still renders its last state instead of an empty page.
"""

import os
import urllib.parse
from http import HTTPStatus

from agentware_lib.ops import derive, loopanalytics, loophealth
from agentware_lib.ops.metrics import metricsemit

from agentware_dashboard.reqparams import NO_KDIR, kdir_of, qp, qp_int
from agentware_dashboard.workfeatures import list_work_features


def loop_feature_state(kdir, feature):
    """Per-feature loop state: persisted `.loop` counters + plan marker burndown
    + terminal outcome + the tail of its metrics.jsonl emission. Read-only; works
    fully from PERSISTED data so the panel is idle-resilient."""
    fdir = os.path.join(kdir, "work", feature)
    loop_dir = os.path.join(fdir, ".loop")
    iteration = None
    try:
        with open(os.path.join(loop_dir, ".iteration"),
                  "r", encoding="utf-8", errors="replace") as f:
            iteration = int((f.read().strip() or "0"))
    except (OSError, ValueError):
        iteration = None
    done = os.path.isfile(os.path.join(loop_dir, ".done"))
    open_n, done_n = derive._plan_marker_status_counts(
        os.path.join(fdir, "plan.md"))
    events = metricsemit._read_metrics_jsonl(kdir, feature=feature)
    outcome = derive.derive_outcome(kdir, feature, events=events)
    last_event = events[-1] if events else None
    last_ts = (last_event or {}).get("ts")
    return {
        "feature": feature,
        "iteration": iteration,
        "done": done,
        "tasks_open": open_n,
        "tasks_done": done_n,
        "tasks_total": open_n + done_n,
        "outcome": outcome,
        "event_count": len(events),
        "last_event": last_event,
        "last_ts": last_ts,
    }


def _active_feature(features):
    """The active run: the most-recently-emitting feature that is not done."""
    active = None
    best_ts = None
    for f in features:
        if f["done"] or not f["last_ts"]:
            continue
        if best_ts is None or str(f["last_ts"]) > str(best_ts):
            best_ts, active = f["last_ts"], f["feature"]
    return active


def api_loop(request):
    """`/api/loop` — every feature's loop state, the active run, and an event tail.

    Optional, contract-preserving pagination of the event tail. With NO params
    the response is byte-identical to before (most-recent 50, chronological).
    `?feature=` scopes the tail to one run; `?limit=N` caps it (most-recent N,
    still returned chronologically). The `features` list is always the full set.
    """
    kdir = kdir_of(request)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, features=[], active=None,
                                   recent_events=[])
    features = [loop_feature_state(kdir, f) for f in list_work_features(kdir)]
    feature_q = qp(request, "feature")
    limit_q = qp_int(request, "limit")
    recent = metricsemit._read_metrics_jsonl(kdir, feature=feature_q) \
        if feature_q else metricsemit._read_metrics_jsonl(kdir)
    tail = limit_q if limit_q is not None else 50
    return HTTPStatus.OK, {
        "features": features,
        "active": _active_feature(features),
        "recent_events": recent[-tail:] if tail else [],
    }


def api_agents(request):
    """Per-agent activity for the PLAN_AW / WORK_AW sidebar views: the active
    planner/worker (most-recent session + active flag) plus the full persisted
    history, cost/token aggregates and per-day/per-feature breakdowns. Polled
    like /api/loop so a freshly-started planner or worker shows up live."""
    kdir = kdir_of(request)
    if not kdir:
        empty = {"kind": None, "active": False, "active_session": None,
                 "session_count": 0, "attributed_count": 0, "incomplete_count": 0,
                 "aggregate": {}, "by_day": {}, "by_feature": {}, "sessions": [],
                 "features": []}
        return HTTPStatus.OK, dict(
            NO_KDIR, plan=dict(empty, kind="plan", plans=[]),
            work=dict(empty, kind="work"))
    return HTTPStatus.OK, derive.derive_agents(kdir)


def api_loop_analytics(request):
    """First-class loop analytics: per-feature per-phase wall/token split,
    tasks-remaining burndown, iteration efficiency, self-heal count,
    max-iteration utilization, pre/post-hook gate outcomes, promise latency, and
    loop throughput over time. Delegates to the toolkit's read-only derivation."""
    kdir = kdir_of(request)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, features=[], throughput={})
    return HTTPStatus.OK, loopanalytics.derive_loop_analytics(kdir)


def api_loop_health(request):
    """Loop-health & runaway detection: per-run duplicate-tool-call /
    no-progress / token-burn / context-window checks folded into an OK/at-risk/
    critical badge naming the offending tool/iteration. Delegates to the
    toolkit's read-only derivation; surfaced on the Overview + Loops sections."""
    kdir = kdir_of(request)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, features=[],
                                   summary={"ok": 0, "at_risk": 0,
                                            "critical": 0}, status="ok")
    return HTTPStatus.OK, loophealth.derive_loop_health(kdir)


def api_loop_health_feature(request, feature):
    """`/api/loop-health/<feature>` — the same badge for ONE run."""
    kdir = kdir_of(request)
    feature = urllib.parse.unquote(feature)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, feature=feature, status="ok",
                                   checks={})
    return HTTPStatus.OK, loophealth.derive_loop_health(kdir, feature=feature)
