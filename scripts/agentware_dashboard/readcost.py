"""agentware_dashboard.readcost — the COST and AUTHORING read model.

Owns: the payloads built from collected session transcripts — the cost/token
aggregate with pricing applied (`/api/cost`), the authoring split
(`/api/authoring`), and the context-tax view (`/api/context-tax`).
Does NOT own: session COLLECTION (`agentware_lib.ops.sessionparse`), the
aggregate and pricing tables (`agentware_lib.ops.metrics.*`), or any derive_*
function (`agentware_lib.ops.derive`). This module composes them in the same
order `agentware metrics` does and adds no arithmetic of its own.

READ-ONLY: no writer; the price table is the toolkit's default, read live so a
pricing change reaches the dashboard and the CLI at once.
"""

from http import HTTPStatus

from agentware_lib.ops import derive
from agentware_lib.ops.metrics import metricsaggregate, metricsemit, metricspricing
from agentware_lib.ops.sessionparse import sessionread

from agentware_dashboard.reqparams import NO_KDIR, kdir_of


def metrics_aggregate(kdir, feature=None):
    """Mirror `cmd_metrics`' read-only aggregate build (cost, cache ratio, per-model
    /feature/day rollups, anomaly flag, authoring, context-tax, phase costs). When
    a feature is in scope, also attach per-iteration + terminal outcome. Returns
    (rows, agg)."""
    rows = sessionread.collect_sessions(kdir, feature=feature)
    agg = metricsaggregate.aggregate_metrics(rows)
    metricspricing.apply_pricing(rows, agg, metricspricing._DEFAULT_PRICES)
    agg["authoring"] = derive.derive_authoring(rows)
    agg["context_tax"] = derive.derive_context_tax(rows, kdir)
    agg["phase_costs"] = derive.derive_phase_costs(rows)
    if feature:
        events = metricsemit._read_metrics_jsonl(kdir, feature=feature)
        agg["iterations"] = derive.derive_iteration_costs(events)
        agg["outcome"] = derive.derive_outcome(kdir, feature, events=events)
    return rows, agg


def api_cost(request):
    """`/api/cost` — every collected session plus the priced aggregate."""
    kdir = kdir_of(request)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, session_count=0, sessions=[],
                                   aggregate={})
    rows, agg = metrics_aggregate(kdir)
    return HTTPStatus.OK, {
        "session_count": len(rows),
        "sessions": rows,
        "aggregate": agg,
    }


def api_authoring(request):
    """`/api/authoring` — the authoring split over every collected session."""
    kdir = kdir_of(request)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, authoring={}, session_count=0)
    rows = sessionread.collect_sessions(kdir)
    return HTTPStatus.OK, {
        "authoring": derive.derive_authoring(rows),
        "session_count": len(rows),
    }


def api_context_tax(request):
    """`/api/context-tax` — what the always-on context costs per session."""
    kdir = kdir_of(request)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, context_tax={}, session_count=0)
    rows = sessionread.collect_sessions(kdir)
    return HTTPStatus.OK, {
        "context_tax": derive.derive_context_tax(rows, kdir),
        "session_count": len(rows),
    }
