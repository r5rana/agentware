"""agentware_lib.ops.loophealth -- loop-health runaway-detection CHECK PRIMITIVES (CLEAN subset).

Owns: the pure, read-only runaway-detection primitives (INV-2) --
  * the severity ordering + worst-status fold (_LOOP_HEALTH_SEVERITY / _worst_status);
  * the four tunable thresholds (_LOOP_HEALTH_DUP_THRESHOLD / _NOPROGRESS_K /
    _MAXITER_WARN / _CONTEXT_WARN);
  * the four per-signal checks -- duplicate tool calls (_loop_health_dup_check),
    no-progress (_longest_flat_run + _loop_health_progress_check), token burn
    (_loop_health_burn_check), context-window (_loop_health_context_check);
  * the per-feature aggregator + its dashboard deriver (_loop_health_for_feature ->
    derive_loop_health), moved in wave-5 seam 10 once ops.loopanalytics owned
    _loop_analytics_for_feature.

Does NOT own: loop feature discovery/throughput + per-feature analytics
(ops.loopanalytics), the trace family (ops.looptrace), the metrics COMPUTE layer
(ops.metrics), the derive_* reporting family (ops.derive), alerts (ops.alerts), or the
failure-ladder (ops.failureladder).

Layer deps: ops.loophealth -> ops.sessionparse (_sessionparse: parse_session/
collect_sessions) + ops.looptrace (_looptrace: derive_trace) + ops.derive (_derive:
derive_context_tax) + ops.loopanalytics (_loopanalytics: _loop_analytics_for_feature/
list_loop_features, NEW in wave-5 seam 10) + ops.metrics (_metrics: _group_events_by_feature/
_read_metrics_jsonl/build_session_index + _TRUNCATION_RISK_THRESHOLD by name, NEW in
wave-5 seam 10) -- same-layer, downward in the acyclic ops DAG. No knowledge/features/
cli import. Nothing imports loophealth (it is a consumer leaf, fan-in 0).

Determinism: read-only (INV-2). Every check degrades to an OK/empty result when its
source channel (live.jsonl trace / metrics.jsonl analytics / session rows) is absent.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so resident callers (the
alerts channel _alerts_loop -> derive_loop_health, cmd_metrics/cmd_dashboard, the
dashboard) resolve them as bare names -- INCLUDING the thresholds read as DEFAULT-ARG
values, evaluated at def time (a lazy import cannot cover them) -- and load_cli tests
reach them as CLI.<name>. ops.sessionparse/ops.looptrace/ops.derive/ops.loopanalytics/
ops.metrics are bound as _sessionparse/_looptrace/_derive/_loopanalytics/_metrics on the
facade so the facade-held functions resolve <alias>.<fn> there (_loopanalytics/_metrics
NEW in wave-5 seam 10). None of the 13 is monkeypatched by any test (grep-verified; the
one ref, test_loop_perf cli._worst_status(...), is a by-name CALL).
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import agentware_lib.ops.derive as _derive
import agentware_lib.ops.looptrace as _looptrace
import agentware_lib.ops.sessionparse as _sessionparse
import agentware_lib.ops.loopanalytics as _loopanalytics
import agentware_lib.ops.metrics as _metrics
from agentware_lib.ops.metrics import _TRUNCATION_RISK_THRESHOLD


# The most-cited autonomous-agent failure modes (dead loops, runaway token burn,
# context-window overflow, no-progress) mapped to deterministic thresholds; all are
# configurable per call. DUP: same (tool, args) repeated >= this many times is a
# dead-loop signal (R-FAIL-04); the at-risk warn tier is derived as threshold//2+1.
# NOPROGRESS_K: tasks_remaining flat (and > 0) across >= this many consecutive MAIN
# iterations is a stuck-loop signal. MAXITER_WARN: iterations-used/max past this
# fraction (run not completed) is at-risk; >= 1.0 is critical. CONTEXT_WARN: context-
# window utilization past this fraction is at-risk; truncation_risk is critical.
_LOOP_HEALTH_DUP_THRESHOLD = 12
_LOOP_HEALTH_NOPROGRESS_K = 3
_LOOP_HEALTH_MAXITER_WARN = 0.8
_LOOP_HEALTH_CONTEXT_WARN = 0.75


_LOOP_HEALTH_SEVERITY = {"ok": 0, "at_risk": 1, "critical": 2}


def _worst_status(statuses):
    """The most-severe status among an iterable of {ok,at_risk,critical}."""
    worst = "ok"
    for s in statuses:
        if _LOOP_HEALTH_SEVERITY.get(s, 0) > _LOOP_HEALTH_SEVERITY[worst]:
            worst = s
    return worst


def _dup_scope_sessions(sess_dirs, feature):
    """Narrow a shared session index to the sessions this feature OWNS.

    AUTHORITATIVE attribution: count duplicate tool calls only from sessions whose
    DOMINANT feature is this one. A broad substring match folds the single global
    outlier session (e.g. "Read repeated 77×" while it discussed many features)
    into EVERY feature's dup count, making 100% of runs read 'critical'.

    `None` means "no index supplied" and is passed through untouched, because the
    trace resolves its own scope in that case. An index that filters EMPTY is
    returned empty rather than widened back: an empty scope reports `ok`, and
    silently re-widening is how the false-critical returned."""
    if sess_dirs is None:
        return None
    return [(sid, sdir) for (sid, sdir) in sess_dirs
            if (_sessionparse.parse_session(sdir) or {}).get("feature") == feature]


def _dup_signature_counts(tr):
    """Count repeats per (session, iteration, tool, args) signature over a trace.

    A genuine dead-loop is the SAME (tool, args) repeated within ONE session (or
    iteration window). Counting across a feature's whole multi-session history
    inflates normal re-reads/edits into a false 'critical', so the signature is
    scoped by session (and iteration when the emission channel provides one).
    This makes 'critical' actually mean "stuck repeating one call", not
    "long-lived feature".

    Returns the counts alone. The iteration the badge points at is READ OFF the
    signature (`key[1]`) rather than tracked beside it: the iteration is PART of
    the key, so every step folded into one signature shares it by construction
    and a first-seen map could only ever hold that same value. It carried one
    until task 99 wave 4, where the sweep proved the guard unkillable — see
    `_loop_health_dup_check`. If the iteration ever LEAVES the signature, the
    map has to come back, because then the value stops being derivable."""
    counts = {}
    for grp in tr.get("iterations", []):
        for st in grp.get("steps", []):
            key = (st.get("session_id"), st.get("iteration"),
                   st.get("tool") or "<unknown>", st.get("args") or "")
            counts[key] = counts.get(key, 0) + 1
    return counts


def _dup_worst_offender(counts):
    """The (max_count, signature) with the most repeats — (0, None) when empty.

    STRICTLY greater, so the first signature to reach a count keeps the title:
    the scan walks the trace in order, which makes the winner the EARLIEST of any
    tied offenders and the report reproducible across runs."""
    max_count = 0
    offender = None
    for key, n in counts.items():
        if n > max_count:
            max_count, offender = n, key
    return max_count, offender


def _dup_status(max_count, threshold, warn):
    """Map a repeat count onto the three-tier health status.

    `warn` is the at-risk tier and is derived from the threshold by the caller,
    so the two can never be configured into disagreeing. The tiers are ordered
    and exhaustive: at-or-past the threshold is critical, at-or-past warn is
    at-risk, anything else is ok."""
    if max_count >= threshold:
        return "critical"
    if max_count >= warn:
        return "at_risk"
    return "ok"


def _loop_health_dup_check(kdir, feature, threshold, sess_dirs=None,
                           events=None):
    """Duplicate/repeated tool-call detection — a dead-loop signal (R-FAIL-04).

    Folds the read-only trace steps (live.jsonl per-action stream) into a count
    per (tool, args) signature; the worst offender drives the status. `flagged`
    when the same call repeats >= `threshold` times. Names the offending tool +
    the iteration it recurs in so the badge can point at the stuck step. Optional
    `sess_dirs`/`events` come from the shared single-pass index (avoid re-scan)."""
    sess_dirs = _dup_scope_sessions(sess_dirs, feature)
    tr = _looptrace.derive_trace(kdir, feature=feature, sess_dirs=sess_dirs,
                                 events=events)
    counts = _dup_signature_counts(tr)
    max_count, offender = _dup_worst_offender(counts)
    # offender is (session_id, iteration, tool, args); expose tool/iteration.
    # Both come straight off the signature: the iteration is a COMPONENT of it,
    # so the reported iteration is the one every folded step shared, and there is
    # no second source that could disagree with the count beside it.
    offender_tool = offender[2] if offender else None
    offender_iter = offender[1] if offender else None
    warn = max(2, threshold // 2 + 1)
    return {
        "name": "duplicate_tool_calls",
        "status": _dup_status(max_count, threshold, warn),
        "flagged": max_count >= threshold,
        "max_repeat": max_count,
        "threshold": threshold,
        "tool": offender_tool,
        "iteration": offender_iter,
        "detail": (("tool %r repeated %d times in one session"
                    % (offender_tool, max_count))
                   if (offender and max_count >= warn) else None),
    }


def _longest_flat_run(burndown):
    """Longest run of consecutive MAIN iterations with the SAME (and > 0)
    tasks_remaining — the no-progress signal. Returns (run_len, iteration_at_end)
    where the iteration marks where the flat streak ends. None values break a run.
    """
    best_len = 0
    best_iter = None
    cur_len = 0
    prev = None
    for b in burndown:
        rem = b.get("tasks_remaining")
        if rem is None or rem <= 0:
            cur_len, prev = 0, None
            continue
        if prev is not None and rem == prev:
            cur_len += 1
        else:
            cur_len = 1
        if cur_len > best_len:
            best_len, best_iter = cur_len, b.get("iteration")
        prev = rem
    return best_len, best_iter


def _loop_health_progress_check(la, k, maxiter_warn):
    """No-progress / nearing-max detection from the loop-analytics burndown.

    Critical when the run hit (or is at/over) the iteration cap without
    completing; at-risk when tasks_remaining is flat across >= `k` MAIN
    iterations OR utilization crosses `maxiter_warn` while still running."""
    burndown = la.get("burndown") or []
    outcome = la.get("outcome")
    flat_len, flat_iter = _longest_flat_run(burndown)
    util = la.get("max_iteration_utilization")
    completed = outcome == "completed"
    hit_max = (outcome == "hit_max_iterations"
               or (isinstance(util, (int, float)) and util >= 1.0
                   and not completed))
    flat_stuck = flat_len >= k
    nearing = (isinstance(util, (int, float)) and util >= maxiter_warn
               and not completed)
    if hit_max:
        status = "critical"
    elif flat_stuck or nearing:
        status = "at_risk"
    else:
        status = "ok"
    detail = None
    if flat_stuck:
        detail = ("tasks_remaining flat across %d iterations (at iteration %s)"
                  % (flat_len, flat_iter))
    elif hit_max:
        detail = "run reached the iteration cap without completing"
    elif nearing:
        detail = "nearing the iteration cap (%.0f%% used)" % (util * 100.0)
    return {
        "name": "no_progress",
        "status": status,
        "flagged": status != "ok",
        "flat_run": flat_len,
        "flat_iteration": flat_iter,
        "max_iteration_utilization": util,
        "outcome": outcome,
        "iteration": flat_iter,
        "detail": detail,
    }


def _loop_health_burn_check(kdir, feature, la, budget=None):
    """Token burn-rate per MAIN iteration + a projection to the iteration cap.

    rate = main-phase tokens / iterations-to-completion; projection = rate ×
    max_iterations. When a `budget` is supplied, `flagged`/at-risk fires if the
    projection would exceed it. Read-only (delegates to the loop-analytics phase
    tokens). No budget -> reported but never flagged."""
    main_tokens = (la.get("phase_tokens") or {}).get("main", 0) or 0
    n_iters = la.get("iterations_to_completion") or 0
    rate = round(main_tokens / n_iters, 1) if n_iters else None
    max_iter = la.get("max_iterations")
    projected = (round(rate * max_iter)
                 if (rate is not None and isinstance(max_iter, int)
                     and not isinstance(max_iter, bool)) else None)
    status = "ok"
    detail = None
    if budget and projected is not None:
        if projected >= budget:
            status = "critical"
            detail = ("projected %d tokens exceeds budget %d"
                      % (projected, budget))
        elif projected >= budget * _LOOP_HEALTH_MAXITER_WARN:
            status = "at_risk"
            detail = ("projected %d tokens nearing budget %d"
                      % (projected, budget))
    return {
        "name": "token_burn",
        "status": status,
        "flagged": status != "ok",
        "tokens_per_iteration": rate,
        "main_tokens": main_tokens,
        "iterations": n_iters,
        "projected_total": projected,
        "budget": budget,
        "detail": detail,
    }


def _loop_health_context_check(kdir, feature, window, threshold,
                               sessions_rows=None):
    """Context-window utilization + truncation-risk for a feature's sessions.

    Delegates to the read-only context-tax derivation (peak input-side tokens vs
    a configurable `window`). Critical at truncation_risk (>= `threshold`),
    at-risk past _LOOP_HEALTH_CONTEXT_WARN — the context-overflow failure mode.
    `sessions_rows` (shared single-pass index) avoids the per-feature re-scan."""
    rows = (sessions_rows if sessions_rows is not None
            else _sessionparse.collect_sessions(kdir, feature=feature))
    # AUTHORITATIVE attribution: a session counts toward THIS feature's context
    # health only when the feature is its DOMINANT feature (parse_session.feature),
    # not merely a substring of its transcript. Without this, the single global-max
    # session (which mentions many features) leaks its peak into every feature and
    # makes 100% of runs read 'critical'. Fall back to the broad set if the strict
    # filter is empty (so a feature with only substring matches still reports).
    rows = [r for r in rows if r.get("feature") == feature]
    ct = _derive.derive_context_tax(rows, kdir, window=window, threshold=threshold)
    pct = ct.get("context_window_pct", 0.0) or 0.0
    risk = bool(ct.get("truncation_risk"))
    if risk:
        status = "critical"
    elif pct >= _LOOP_HEALTH_CONTEXT_WARN:
        status = "at_risk"
    else:
        status = "ok"
    return {
        "name": "context_window",
        "status": status,
        "flagged": risk,
        "context_window_pct": pct,
        "peak_input_tokens": ct.get("peak_input_tokens", 0),
        "context_window": ct.get("context_window", window),
        "truncation_risk": risk,
        "truncation_threshold": ct.get("truncation_threshold", threshold),
        "detail": (("peak input %d tokens = %.0f%% of the %d window"
                    % (ct.get("peak_input_tokens", 0), pct * 100.0,
                       ct.get("context_window", window)))
                   if status != "ok" else None),
    }


def _loop_health_for_feature(kdir, feature, window=None,
                             threshold=_TRUNCATION_RISK_THRESHOLD,
                             dup_threshold=_LOOP_HEALTH_DUP_THRESHOLD,
                             noprogress_k=_LOOP_HEALTH_NOPROGRESS_K,
                             budget=None, index=None, events=None):
    """Per-run loop-health for ONE feature (pure, read-only, INV-2).

    Runs the four runaway-detection checks (duplicate tool calls, no-progress,
    token burn-rate, context-window utilization) and folds them into an overall
    OK/at-risk/critical badge that NAMES the offending tool/iteration. Every
    check degrades to OK when its source channel is absent. A shared single-pass
    `index` (build_session_index) + pre-grouped `events` let the bulk caller
    avoid re-scanning every session 3× per feature; both are optional."""
    # window=None => the context check auto-detects the real model ceiling (1M for
    # Claude 4) rather than forcing a 200k window that mis-reads big sessions.
    sessions_rows = index["rows"].get(feature) if index is not None else None
    sess_dirs = index["dirs"].get(feature) if index is not None else None
    la = _loopanalytics._loop_analytics_for_feature(kdir, feature, events=events,
                                     sessions_rows=sessions_rows)
    dup = _loop_health_dup_check(kdir, feature, dup_threshold,
                                 sess_dirs=sess_dirs, events=events)
    progress = _loop_health_progress_check(la, noprogress_k,
                                           _LOOP_HEALTH_MAXITER_WARN)
    burn = _loop_health_burn_check(kdir, feature, la, budget=budget)
    context = _loop_health_context_check(kdir, feature, window, threshold,
                                         sessions_rows=sessions_rows)
    checks = {c["name"]: c for c in (dup, progress, burn, context)}
    status = _worst_status(c["status"] for c in checks.values())
    # The offender that drives the badge: the worst flagged check naming a tool/
    # iteration (dup first — it points at a concrete stuck step), else progress.
    offender = None
    for c in (dup, progress, context, burn):
        if c["flagged"]:
            offender = {"check": c["name"], "tool": c.get("tool"),
                        "iteration": c.get("iteration"), "detail": c.get("detail")}
            break
    flagged = [c["name"] for c in checks.values() if c["flagged"]]
    return {
        "feature": feature,
        "status": status,
        "checks": checks,
        "flagged_checks": flagged,
        "offender": offender,
        "outcome": la.get("outcome"),
    }


def derive_loop_health(kdir, feature=None, window=None,
                       threshold=_TRUNCATION_RISK_THRESHOLD,
                       dup_threshold=_LOOP_HEALTH_DUP_THRESHOLD,
                       noprogress_k=_LOOP_HEALTH_NOPROGRESS_K, budget=None):
    """Loop-health & runaway detection for the Loops + Overview sections (pure,
    read-only, INV-2).

    With a `feature`, returns that single run's health dict (status + per-check
    detail + the offending tool/iteration). Without one, returns
    {features: [...], summary: {ok, at_risk, critical}} across every work feature.
    Never writes; degrades to OK/empty when the emission/log channels are absent.
    """
    if feature:
        return _loop_health_for_feature(
            kdir, feature, window=window, threshold=threshold,
            dup_threshold=dup_threshold, noprogress_k=noprogress_k,
            budget=budget)
    features = _loopanalytics.list_loop_features(kdir)
    by_feat = _metrics._group_events_by_feature(_metrics._read_metrics_jsonl(kdir))
    index = _metrics.build_session_index(kdir, features)
    feats = [_loop_health_for_feature(
        kdir, f, window=window, threshold=threshold,
        dup_threshold=dup_threshold, noprogress_k=noprogress_k, budget=budget,
        index=index, events=by_feat.get(f, []))
        for f in features]
    summary = {"ok": 0, "at_risk": 0, "critical": 0}
    for f in feats:
        summary[f["status"]] = summary.get(f["status"], 0) + 1
    worst = _worst_status(f["status"] for f in feats)
    return {"features": feats, "summary": summary, "status": worst}

_EXPORTS = [
    '_LOOP_HEALTH_DUP_THRESHOLD',
    '_LOOP_HEALTH_NOPROGRESS_K',
    '_LOOP_HEALTH_MAXITER_WARN',
    '_LOOP_HEALTH_CONTEXT_WARN',
    '_LOOP_HEALTH_SEVERITY',
    '_worst_status',
    '_dup_scope_sessions',
    '_dup_signature_counts',
    '_dup_worst_offender',
    '_dup_status',
    '_loop_health_dup_check',
    '_longest_flat_run',
    '_loop_health_progress_check',
    '_loop_health_burn_check',
    '_loop_health_context_check',
    '_loop_health_for_feature',
    'derive_loop_health',
]
