"""agentware_lib.ops.alerts -- symptom-based, severity-ranked ALERTS check primitives (CLEAN subset).

Owns: the pure, read-only (INV-2) alerts primitives derived from the SAME sources
the panels read -- the severity ordering + worst-severity fold (_ALERT_SEVERITY_RANK /
_worst_alert_severity), the per-class tunable thresholds (_ALERT_RELIABILITY_WARN/CRIT,
_ALERT_NDCG_WARN/CRIT, _ALERT_SCALING_SLOPE_THRESHOLD), the deep-link map
(_ALERT_DEEP_LINKS) + loop-status->severity map (_LOOP_STATUS_TO_ALERT), the alert-record
builder (_alert), the shared OLS slope (_ols_slope), and the three symptom checks that
depend only on their argument rows -- regression (_alerts_regression), scaling
(_alerts_scaling), and cost (_alerts_cost). ALSO owns (wave-6 seam-11) the
KB-source alert channels _alerts_loop (loop-health -> alerts via ops.loophealth),
_alerts_kb (stale/conflicting KB via ops.freshness), and _alerts_unpromoted
(unpromoted LEARNED/DECISION at finish via ops.loopanalytics + ops.derive +
knowledge.worklog). ALSO owns (wave-6 seam-15) the top-level AGGREGATOR derive_alerts,
which folds the three KB-source channels + the argument-only regression/scaling/cost
checks into the severity-ranked, deterministically-ordered alert set (reading the eval
ledger via retrieval.ledger, the session/cost rollups via ops.sessionparse + ops.metrics,
and the index via knowledge.index) and returns the per-run commit_markers for trend charts.

Does NOT own: loop feature discovery/throughput (ops.loopanalytics), the
trace family (ops.looptrace), loop-health (ops.loophealth), the metrics COMPUTE layer
(ops.metrics), session parsing (ops.sessionparse), the eval ledger (retrieval.ledger),
the index (knowledge.index), the derive_* reporting family (ops.derive), or the
failure-ladder (ops.failureladder). derive_alerts DEPENDS ON several of these but does
not re-implement them -- it reads them read-only via the module aliases below.

Layer deps: ops.alerts imports os (stdlib) + the SIBLINGS ops.loophealth (module alias
_loophealth), ops.freshness (_freshness), ops.loopanalytics (_loopanalytics), ops.derive
(_derive), ops.metrics (_metrics) and ops.sessionparse (_sessionparse) -- all ops->ops,
acyclic (none imports alerts, AST-confirmed) -- plus the DOWNWARD knowledge.worklog
(_worklog), knowledge.index (module alias index) (ops->knowledge) and retrieval.ledger
(_ledger) (ops->retrieval). No upward edge (nothing in features/cli). The fleet reporter
consumes _ols_slope via the facade today and will import it from ops.alerts (a legal
same-layer ops->ops edge) when the fleet domain moves.

Determinism: read-only (INV-2), pure functions of their argument rows/aggregates.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so the dashboard + the
load_cli tests reach them as CLI.<name>, and any resident consumer (e.g. cmd_fleet, which
uses _ols_slope) resolves them as bare names. derive_alerts now LIVES here (wave-6
seam-15); its _ALERT_SCALING_SLOPE_THRESHOLD DEFAULT-ARG is read at def time against THIS
module's namespace (a lazy import could not cover a default arg). The aggregator + channels
reference cross-module FUNCTIONS via module ALIASES (_loophealth/_freshness/_loopanalytics/
_derive/_metrics/_sessionparse/_ledger/index/_worklog), which are gate-safe (a module object
has no __code__ so the global-resolve gate never walks it). None of the 18 is monkeypatched
by any test (grep-verified).
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import os

import agentware_lib.ops.loophealth as _loophealth
import agentware_lib.ops.freshness as _freshness
import agentware_lib.ops.loopanalytics as _loopanalytics
import agentware_lib.ops.derive as _derive
import agentware_lib.ops.metrics as _metrics
import agentware_lib.ops.sessionparse as _sessionparse
import agentware_lib.retrieval.ledger as _ledger
import agentware_lib.knowledge.index as index
import agentware_lib.knowledge.worklog as _worklog


# --- Alerts surface (Task 31) ------------------------------------------------
# Symptom-based, severity-ranked alerts derived PURELY (INV-2, read-only) from
# the SAME sources the other panels read — the eval ledger, the cost rollups,
# loop-health, the staleness/conflict report, and the per-feature worklogs. No
# new collection; every alert deep-links to the panel/route that explains it, and
# the ledger SHAs are returned as commit markers so trend charts can correlate a
# metric shift with the commit that caused it (per SRE symptom-based alerting).

# severity -> rank (higher = worse); mirrors _LOOP_HEALTH_SEVERITY's intent but
# spans the three alert severities used across every class.
_ALERT_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}

# Reliability is a 0..100 score; a run-over-run DROP past these (percentage
# points) is a warning, then critical. nDCG is a 0..1 fraction.
_ALERT_RELIABILITY_WARN = 2.0
_ALERT_RELIABILITY_CRIT = 10.0
_ALERT_NDCG_WARN = 0.05
_ALERT_NDCG_CRIT = 0.10

# Retrieval scaling: recall@k SHOULD hold/improve as the corpus grows. A net
# NEGATIVE slope of recall-vs-corpus_size is the degradation symptom to flag.
_ALERT_SCALING_SLOPE_THRESHOLD = 0.0

# Loop-health status -> alert severity for the stuck-loop/runaway class.
_LOOP_STATUS_TO_ALERT = {"critical": "critical", "at_risk": "warning"}

# Frontend routes each alert class deep-links to (must match the panel registry).
_ALERT_DEEP_LINKS = {
    "regression": "/health/quality",
    "scaling": "/memory/scaling",
    "cost": "/cost",
    "loop": "/loops/health",
    "kb_stale": "/health",
    "kb_conflict": "/health",
    "unpromoted": "/loops/outcomes",
}


def _ols_slope(points):
    """Ordinary-least-squares slope over (x, y) numeric points; None if < 2 or
    degenerate. Mirrors the dashboard's /api/scaling slope so the alert and the
    plotted slope agree (read-only, pure)."""
    pts = [(x, y) for (x, y) in points
           if isinstance(x, (int, float)) and not isinstance(x, bool)
           and isinstance(y, (int, float)) and not isinstance(y, bool)]
    n = len(pts)
    if n < 2:
        return None
    sx = sum(x for x, _ in pts)
    sy = sum(y for _, y in pts)
    sxx = sum(x * x for x, _ in pts)
    sxy = sum(x * y for x, y in pts)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    return (n * sxy - sx * sy) / denom


def _worst_alert_severity(severities):
    worst = None
    for s in severities:
        if worst is None or \
                _ALERT_SEVERITY_RANK.get(s, 0) > _ALERT_SEVERITY_RANK[worst]:
            worst = s
    return worst


def _alert(category, severity, title, detail, feature=None, extra=None):
    """Build one alert record with a deterministic id + the class deep-link."""
    aid = category if feature is None else "%s:%s" % (category, feature)
    rec = {
        "id": aid,
        "category": category,
        "severity": severity,
        "title": title,
        "detail": detail,
        "feature": feature,
        "deep_link": _ALERT_DEEP_LINKS.get(category),
    }
    if extra:
        rec.update(extra)
    return rec


def _alerts_regression(rows):
    """Reliability + nDCG REGRESSION vs the prior ledger row (E5)."""
    out = []
    if len(rows) < 2:
        return out
    latest, prev = rows[-1], rows[-2]
    lr, pr = latest.get("reliability"), prev.get("reliability")
    if isinstance(lr, (int, float)) and isinstance(pr, (int, float)):
        drop = pr - lr
        if drop >= _ALERT_RELIABILITY_WARN:
            sev = "critical" if drop >= _ALERT_RELIABILITY_CRIT else "warning"
            out.append(_alert(
                "regression", sev, "Reliability regression",
                "reliability fell %.1f pts (%.1f -> %.1f) vs the prior eval"
                % (drop, pr, lr),
                extra={"metric": "reliability", "from": pr, "to": lr,
                       "commit": latest.get("commit"),
                       "prev_commit": prev.get("commit")}))
    ln = (latest.get("metrics") or {}).get("ndcg_at_k")
    pn = (prev.get("metrics") or {}).get("ndcg_at_k")
    if isinstance(ln, (int, float)) and isinstance(pn, (int, float)):
        ndrop = pn - ln
        if ndrop >= _ALERT_NDCG_WARN:
            sev = "critical" if ndrop >= _ALERT_NDCG_CRIT else "warning"
            out.append(_alert(
                "regression", sev, "nDCG regression",
                "nDCG@k fell %.3f (%.3f -> %.3f) vs the prior eval"
                % (ndrop, pn, ln),
                feature=None,
                extra={"metric": "ndcg_at_k", "from": pn, "to": ln,
                       "commit": latest.get("commit"),
                       "prev_commit": prev.get("commit"),
                       "id": "regression:ndcg"}))
    return out


def _alerts_scaling(rows, slope_threshold):
    """Retrieval scaling-slope crossing threshold — recall degrading as the
    corpus grows (E5). Reuses the same OLS the /api/scaling panel plots."""
    xy = []
    for r in rows:
        size = r.get("corpus_size")
        recall = (r.get("metrics") or {}).get("recall_at_k")
        if isinstance(size, (int, float)) and not isinstance(size, bool) \
                and isinstance(recall, (int, float)):
            xy.append((size, recall))
    slope = _ols_slope(xy)
    if slope is None or slope >= slope_threshold:
        return []
    return [_alert(
        "scaling", "warning", "Retrieval scaling degradation",
        "recall@k slope vs corpus size is %.6f (< %.6f) over %d measured runs"
        % (slope, slope_threshold, len(xy)),
        extra={"slope": slope, "measured": len(xy)})]


def _alerts_cost(agg):
    """COST spike vs the trailing median (E5) — reuses the cost-anomaly flag."""
    dates = agg.get("cost_anomaly_dates") or []
    if not dates:
        return []
    return [_alert(
        "cost", "warning", "Cost spike",
        "%d day(s) exceeded the trailing-median cost: %s"
        % (len(dates), ", ".join(dates)),
        extra={"dates": list(dates)})]


def _alerts_loop(kdir):
    """Stuck-loop / runaway-tokens from loop-health (E4 -> E5)."""
    out = []
    health = _loophealth.derive_loop_health(kdir)
    for f in health.get("features", []):
        sev = _LOOP_STATUS_TO_ALERT.get(f.get("status"))
        if not sev:
            continue
        off = f.get("offender") or {}
        detail = off.get("detail") or ", ".join(f.get("flagged_checks") or []) \
            or "loop-health flagged"
        out.append(_alert(
            "loop", sev, "Loop-health: %s" % f.get("status"),
            detail, feature=f.get("feature"),
            extra={"flagged_checks": f.get("flagged_checks") or [],
                   "offender": off or None}))
    return out


def _alerts_kb(kdir, data, max_age_days):
    """Stale + possibly-conflicting KB entries (E5) from the audit report."""
    out = []
    if data is None:
        return out
    report = _freshness.build_stale_report(kdir, data, max_age_days)
    stale = report.get("stale") or []
    if stale:
        out.append(_alert(
            "kb_stale", "info", "Stale knowledge entries",
            "%d entr%s past the %d-day freshness window"
            % (len(stale), "y" if len(stale) == 1 else "ies", max_age_days),
            extra={"count": len(stale),
                   "ids": [s.get("id") for s in stale]}))
    conflicts = report.get("conflicts") or []
    if conflicts:
        out.append(_alert(
            "kb_conflict", "warning", "Possible duplicate/conflicting entries",
            "%d high-overlap entry pair(s) may duplicate or conflict"
            % len(conflicts),
            extra={"count": len(conflicts), "pairs": conflicts}))
    return out


def _alerts_unpromoted(kdir, data):
    """Unpromoted `> LEARNED:` / `> DECISION:` markers in a FINISHED run's
    worklog (E5) — the completion-gate symptom (R-SI-03 / R-AUTO-05)."""
    out = []
    if data is None:
        return out
    for feature in _loopanalytics.list_loop_features(kdir):
        outcome = _derive.derive_outcome(kdir, feature) or {}
        if outcome.get("outcome") != "completed":
            continue
        wl = os.path.join(kdir, "work", feature, "worklog.md")
        if not os.path.isfile(wl):
            continue
        try:
            unpromoted, _total = _worklog.scan_worklog(wl, kdir, data)
        except OSError:
            continue
        if not unpromoted:
            continue
        out.append(_alert(
            "unpromoted", "warning", "Unpromoted learnings at finish",
            "%d marker(s) still unpromoted in a completed run" % len(unpromoted),
            feature=feature,
            extra={"count": len(unpromoted),
                   "markers": unpromoted}))
    return out


def derive_alerts(kdir, slope_threshold=_ALERT_SCALING_SLOPE_THRESHOLD,
                  max_age_days=120):
    """Severity-ranked, symptom-based alerts derived read-only (INV-2) from the
    existing telemetry (Task 31): reliability/nDCG regression, retrieval
    scaling-slope, cost spike, stuck-loop/runaway, stale/conflicting KB, and
    unpromoted LEARNED/DECISION at finish. Each alert deep-links to its panel;
    the ledger SHAs are returned as `commit_markers` so trend charts can place
    commit markers aligned to the runs. Never writes; degrades to empty when a
    source channel is absent."""
    rows = _ledger._read_ledger(os.path.join(kdir, _ledger.HISTORY_REL))
    rows_sessions = _sessionparse.collect_sessions(kdir)
    agg = _metrics.aggregate_metrics(rows_sessions)
    _metrics.apply_pricing(rows_sessions, agg, _metrics._DEFAULT_PRICES)
    data, _err = index.load_index(kdir)

    alerts = []
    alerts += _alerts_regression(rows)
    alerts += _alerts_scaling(rows, slope_threshold)
    alerts += _alerts_cost(agg)
    alerts += _alerts_loop(kdir)
    alerts += _alerts_kb(kdir, data, max_age_days)
    alerts += _alerts_unpromoted(kdir, data)

    # Stable, severity-ranked order (worst first), then by category/id so the
    # output is deterministic across runs (read-only repeatability, INV-2).
    alerts.sort(key=lambda a: (
        -_ALERT_SEVERITY_RANK.get(a["severity"], 0),
        a["category"], str(a["id"])))

    summary = {"critical": 0, "warning": 0, "info": 0}
    for a in alerts:
        summary[a["severity"]] = summary.get(a["severity"], 0) + 1
    worst = _worst_alert_severity(a["severity"] for a in alerts)

    # Commit/deployment markers for the trend charts: one per ledger run, newest
    # last (chronological), carrying the headline metrics for tooltip context.
    commit_markers = [{
        "commit": r.get("commit"),
        "run": r.get("run"),
        "strategy": r.get("strategy"),
        "reliability": r.get("reliability"),
        "recall_at_k": (r.get("metrics") or {}).get("recall_at_k"),
    } for r in rows if r.get("commit")]

    return {
        "alerts": alerts,
        "summary": summary,
        "open_count": len(alerts),
        "status": worst or "ok",
        "commit_markers": commit_markers,
    }


_EXPORTS = [
    '_ALERT_SEVERITY_RANK',
    '_ALERT_RELIABILITY_WARN',
    '_ALERT_RELIABILITY_CRIT',
    '_ALERT_NDCG_WARN',
    '_ALERT_NDCG_CRIT',
    '_ALERT_SCALING_SLOPE_THRESHOLD',
    '_LOOP_STATUS_TO_ALERT',
    '_ALERT_DEEP_LINKS',
    '_ols_slope',
    '_worst_alert_severity',
    '_alert',
    '_alerts_regression',
    '_alerts_scaling',
    '_alerts_cost',
    '_alerts_loop',
    '_alerts_kb',
    '_alerts_unpromoted',
    'derive_alerts',
]
