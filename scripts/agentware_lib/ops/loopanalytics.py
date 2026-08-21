"""agentware_lib.ops.loopanalytics -- loop feature discovery + throughput (CLEAN leaf subset).

Owns: the loop-domain leaf primitives that nothing else loop-domain sits below —
  * the loop PHASE tuple + normalizer (_LOOP_PHASES / _normalize_phase);
  * feature discovery from the emission channel + work dir (list_loop_features);
  * per-feature throughput rollup over logs/metrics.jsonl (derive_loop_throughput);
  * the per-feature analytics fold + its dashboard deriver
    (_loop_analytics_for_feature -> derive_loop_analytics), moved in wave-5 seam 10
    once ops.derive owned derive_outcome/derive_phase_costs.

Does NOT own: the trace family (ops.looptrace), loop-health (ops.loophealth), alerts
(ops.alerts), failure-ladder (ops.failureladder), or the metrics COMPUTE layer
(ops.metrics) / derive_* reporting family (ops.derive) it reads DOWN into.

Layer deps: ops.loopanalytics -> ops.sessionparse (_sessionparse: _parse_iso/
collect_sessions) + ops.metrics (_metrics: _read_metrics_jsonl/_group_events_by_feature/
build_session_index) + ops.derive (_derive: derive_outcome/derive_phase_costs, NEW in
wave-5 seam 10) -- same-layer, downward in the acyclic ops DAG. No knowledge/features/
cli import. This is the foundational loop module: ops.looptrace and ops.loophealth import
_normalize_phase / analytics from here.

Determinism: read-only (INV-2). Pure over the emission channel + work dir.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so resident callers
(cmd_metrics/cmd_dashboard, ops.loophealth + ops.looptrace via the _loopanalytics alias)
resolve them as bare names and load_cli tests reach them as CLI.<name>. ops.sessionparse/
ops.metrics/ops.derive are bound as _sessionparse/_metrics/_derive on the facade so the
facade-held functions resolve <alias>.<fn> there (_derive NEW in wave-5 seam 10). None of
the 6 is monkeypatched by any test (grep-verified).
Fan-in: 3 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import os

import agentware_lib.ops.metrics as _metrics
import agentware_lib.ops.sessionparse as _sessionparse
import agentware_lib.ops.derive as _derive


_LOOP_PHASES = ("pre", "main", "post")


def _normalize_phase(phase):
    """`loop-pre`/`loop-main`/`loop-post` (the stage tag) -> bare phase name."""
    if isinstance(phase, str) and phase.startswith("loop-"):
        return phase[len("loop-"):]
    return phase or "main"


def list_loop_features(kdir):
    """Sorted feature dirs under <kdir>/work carrying a plan.md or a .loop/ — the
    unit the loop operates on (read-only directory listing; [] when absent).

    The `auto/` lane (feature 260710) is a CONTAINER, not a feature: workorders
    live at `work/auto/<slug>/`, so descend one level there and namespace the id
    as `auto/<slug>`. Without the descent every workorder is invisible to the
    loop-analytics fold, `ops.alerts` and `ops.loophealth` — an undercount that
    reports cleanly rather than failing.
    """
    work = os.path.join(kdir, "work")
    out = []

    def _is_feature(d):
        return os.path.isfile(os.path.join(d, "plan.md")) or \
            os.path.isdir(os.path.join(d, ".loop"))

    try:
        for name in sorted(os.listdir(work)):
            fdir = os.path.join(work, name)
            if not os.path.isdir(fdir):
                continue
            if name == "auto":
                try:
                    for sub in sorted(os.listdir(fdir)):
                        sp = os.path.join(fdir, sub)
                        if os.path.isdir(sp) and _is_feature(sp):
                            out.append("auto/" + sub)
                except OSError:
                    pass
                continue
            if _is_feature(fdir):
                out.append(name)
    except OSError:
        return []
    return out


def derive_loop_throughput(kdir, events=None):
    """Loop throughput over time: features COMPLETED per day + ISO week, derived
    from the terminal events (outcome == 'completed') across all features. Pure +
    read-only; an empty channel yields empty maps."""
    if events is None:
        events = _metrics._read_metrics_jsonl(kdir)
    by_day = {}
    by_week = {}
    for e in events:
        if e.get("event") != "terminal" or e.get("outcome") != "completed":
            continue
        ts = e.get("ts")
        dt = _sessionparse._parse_iso(ts)
        if dt is None or not isinstance(ts, str):
            continue
        day = ts[:10]
        by_day[day] = by_day.get(day, 0) + 1
        iso = dt.isocalendar()
        wk = "%04d-W%02d" % (iso[0], iso[1])
        by_week[wk] = by_week.get(wk, 0) + 1
    return {
        "by_day": by_day,
        "by_week": by_week,
        "completed_total": sum(by_day.values()),
    }


def _fold_loop_events(events):
    """Fold the emission channel for ONE feature into the four accumulators the
    analytics are computed from: `{phase_wall, gates, iter_ts, max_seen,
    iterations}`.

    The 3 phases each run their OWN iteration counter (pre 1..3, main 1..N,
    post 1..3), so their iteration numbers OVERLAP. The burndown is the MAIN
    loop's tasks-remaining curve, so it folds main-phase events ONLY (keyed by
    iteration: last remaining seen + summed done-delta); pre/post wall-time is
    still attributed to phase_wall, and pre/post events drive the gate outcomes.

    Every isinstance guard here is a coercion of an UNTRUSTED line: a non-numeric
    `phase_wall_s` contributes zero rather than raising, and a `True` iteration
    is rejected before it can masquerade as iteration 1. Terminal and transition
    events are not folded — they are read separately by the caller."""
    phase_wall = {}
    gates = {"pre": [], "post": []}
    iter_ts = []
    max_seen = None
    main_by_it = {}
    main_order = []
    for e in events:
        if e.get("event") in ("task_transition", "terminal"):
            continue
        phase = _normalize_phase(e.get("phase") or e.get("stage"))
        wall = e.get("phase_wall_s") or 0.0
        try:
            wall = float(wall)
        except (TypeError, ValueError):
            wall = 0.0
        phase_wall[phase] = round(phase_wall.get(phase, 0.0) + wall, 3)
        if isinstance(e.get("ts"), str):
            iter_ts.append(e["ts"])
        m = e.get("max")
        if isinstance(m, int) and not isinstance(m, bool):
            max_seen = m if max_seen is None else max(max_seen, m)
        if phase in ("pre", "post"):
            gates[phase].append({
                "iteration": e.get("iteration"),
                "result": e.get("result"),
                "promise_status": e.get("promise_status"),
                "ts": e.get("ts"),
                "ok": (e.get("promise_status") == "signalled"
                       or e.get("result") == "complete"),
            })
        elif phase == "main":
            _fold_main_event(e, main_by_it, main_order)
    return {"phase_wall": phase_wall, "gates": gates, "iter_ts": iter_ts,
            "max_seen": max_seen,
            "iterations": [main_by_it[i] for i in sorted(main_order)]}


def _fold_main_event(e, main_by_it, main_order):
    """Fold ONE main-phase event into its iteration record (in place).

    `tasks_remaining` is LAST-WINS because a later event in the same iteration is
    a later observation of the same quantity; `tasks_done_delta` is SUMMED
    because each event reports its own increment. An event with no usable
    iteration number is dropped entirely rather than filed under a default —
    attributing work to the wrong iteration is worse than not counting it."""
    it = e.get("iteration")
    if not isinstance(it, int) or isinstance(it, bool):
        return
    rec = main_by_it.get(it)
    if rec is None:
        rec = {"iteration": it, "tasks_remaining": None,
               "tasks_done_delta": 0}
        main_by_it[it] = rec
        main_order.append(it)
    if e.get("tasks_remaining") is not None:
        rec["tasks_remaining"] = e.get("tasks_remaining")
    d = e.get("tasks_done_delta")
    if isinstance(d, int) and not isinstance(d, bool):
        rec["tasks_done_delta"] += d
    sh = e.get("self_heal_count")
    if isinstance(sh, int) and not isinstance(sh, bool):
        rec["self_heal_count"] = sh


def _iteration_metrics(iterations, terminal, max_seen):
    """The four derived run-shape metrics: `{tasks_closed, n_iters, max_iter,
    iteration_efficiency, max_iteration_utilization, self_heal_count}`.

    Each has a TERMINAL-first, fold-second precedence, and the fallback is not a
    nicety: the terminal event is written by the loop as it exits, so a run that
    was killed has none, and every one of these numbers still has to be
    answerable from the events that did land. `n_iters` also rejects a
    non-positive terminal count, because a zero would make the efficiency ratio
    a division by zero rather than an honest 'no iterations'."""
    tasks_closed = sum((r.get("tasks_done_delta") or 0) for r in iterations)
    n_iters = terminal.get("iterations_used")
    if not isinstance(n_iters, int) or isinstance(n_iters, bool) or n_iters <= 0:
        n_iters = len(iterations)
    max_iter = terminal.get("max")
    if not isinstance(max_iter, int) or isinstance(max_iter, bool):
        max_iter = max_seen
    return {
        "tasks_closed": tasks_closed,
        "n_iters": n_iters,
        "max_iter": max_iter,
        "iteration_efficiency": (round(tasks_closed / n_iters, 3)
                                 if n_iters else None),
        "max_iteration_utilization": (round(n_iters / max_iter, 3)
                                      if (isinstance(max_iter, int)
                                          and max_iter and n_iters) else None),
        "self_heal_count": _self_heal_count(terminal, iterations),
    }


def _self_heal_count(terminal, iterations):
    """How many self-heals the run performed.

    The terminal event carries the authoritative running total, so it wins
    outright — INCLUDING a terminal zero, which is why the fallback keys on
    `is None` and not on falsiness. Without a terminal the count is recovered as
    the MAX over the per-iteration observations rather than their sum: each
    event reports the running total at that moment, so summing would multiply a
    single heal by the number of iterations that saw it."""
    self_heal = terminal.get("self_heal_count")
    if self_heal is not None:
        return self_heal
    sh = [r.get("self_heal_count") for r in iterations
          if isinstance(r.get("self_heal_count"), int)
          and not isinstance(r.get("self_heal_count"), bool)]
    return max(sh) if sh else 0


def _terminal_latency_s(terminal, iter_ts):
    """Wall-clock seconds from the run's FIRST emitted event to its terminal.

    Both ends must exist and both must parse: a run with no terminal has not
    finished and has no latency, and an unparseable stamp yields None rather than
    a number derived from a guess. "No terminal" is accepted in either spelling —
    `{}` (what the fold hands over) or `None` — and the falsy guard is there for
    the second one, since the `.get` below raises on it."""
    if not terminal or not iter_ts:
        return None
    t0 = _sessionparse._parse_iso(min(iter_ts))
    t1 = _sessionparse._parse_iso(terminal.get("ts"))
    if t0 is None or t1 is None:
        return None
    return round((t1 - t0).total_seconds(), 3)


def _loop_analytics_for_feature(kdir, feature, events=None, sessions_rows=None):
    """Per-run loop analytics for ONE feature (pure, read-only). Folds the
    metrics.jsonl emission into: per-phase wall split, iterations-to-completion,
    a tasks-remaining burndown, iteration efficiency, self-heal count,
    max-iteration utilization, pre/post-hook gate outcomes, promise/.done
    latency, and the terminal outcome. Empty channel -> safe zero/None values."""
    if events is None:
        events = _metrics._read_metrics_jsonl(kdir, feature=feature)
    outcome = _derive.derive_outcome(kdir, feature, events=events)
    terminals = [e for e in events if e.get("event") == "terminal"]
    terminal = terminals[-1] if terminals else {}

    fold = _fold_loop_events(events)
    iterations = fold["iterations"]
    metrics = _iteration_metrics(iterations, terminal, fold["max_seen"])

    # Per-phase TOKEN split from the already-parsed session rows (read-only): the
    # phase a session lands in is its AGENTWARE_STAGE (Task 3/5 classifier).
    # `sessions_rows` (from a shared single-pass index) avoids re-scanning every
    # session per feature; falls back to the per-feature scan when not supplied.
    rows = (sessions_rows if sessions_rows is not None
            else _sessionparse.collect_sessions(kdir, feature=feature))
    by_phase = _derive.derive_phase_costs(rows)["by_phase"]

    return {
        "feature": feature,
        "outcome": (outcome or {}).get("outcome", "unknown"),
        "iterations_to_completion": metrics["n_iters"],
        "max_iterations": metrics["max_iter"],
        "max_iteration_utilization": metrics["max_iteration_utilization"],
        "iteration_efficiency": metrics["iteration_efficiency"],
        "tasks_closed": metrics["tasks_closed"],
        "tasks_total": terminal.get("tasks_total"),
        "tasks_done": terminal.get("tasks_done"),
        "self_heal_count": metrics["self_heal_count"],
        "promise_status": terminal.get("promise_status"),
        "latency_s": _terminal_latency_s(terminal, fold["iter_ts"]),
        "burndown": [{
            "iteration": r["iteration"],
            "tasks_remaining": r.get("tasks_remaining"),
            "tasks_done_delta": r.get("tasks_done_delta", 0),
        } for r in iterations],
        "phase_wall_s": fold["phase_wall"],
        "phase_tokens": {ph: by_phase[ph]["total_tokens"] for ph in by_phase},
        "gates": fold["gates"],
        "event_count": len(events),
    }


def derive_loop_analytics(kdir, feature=None):
    """Loop analytics for the Loops dashboard section (pure, read-only, INV-2).

    With a `feature`, returns that single run's analytics dict. Without one,
    returns {features: [per-feature dicts], throughput: {by_day, by_week,
    completed_total}} across every work feature. Never writes; degrades to empty
    structures when the emission channel is absent (forward-compatible)."""
    if feature:
        return _loop_analytics_for_feature(kdir, feature)
    all_events = _metrics._read_metrics_jsonl(kdir)
    by_feat = _metrics._group_events_by_feature(all_events)
    features = list_loop_features(kdir)
    index = _metrics.build_session_index(kdir, features)
    feats = [_loop_analytics_for_feature(
                kdir, f, events=by_feat.get(f, []),
                sessions_rows=index["rows"].get(f, []))
             for f in features]
    return {
        "features": feats,
        "throughput": derive_loop_throughput(kdir, events=all_events),
    }

_EXPORTS = [
    '_LOOP_PHASES',
    '_normalize_phase',
    'list_loop_features',
    'derive_loop_throughput',
    '_fold_loop_events',
    '_fold_main_event',
    '_iteration_metrics',
    '_self_heal_count',
    '_terminal_latency_s',
    '_loop_analytics_for_feature',
    'derive_loop_analytics',
]
