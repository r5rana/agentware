"""agentware_lib.ops.derive -- the derive_* reporting family (compute-only, incl. plan-file derivations).

Owns: the PLAN-FREE derive_* reporting family -- the deterministic report
derivations that depend ONLY on the already-extracted compute layer (ops.metrics),
parse layer (ops.sessionparse) and tokenizer (retrieval.corpus):
  * agent-activity rollup: derive_agent_activity;
  * authoring signals: derive_authoring;
  * context-tax report: _cache_read_per_turn + derive_context_tax (cache-read
    efficiency per turn + peak single-turn input / truncation-risk sizing);
  * per-phase cost attribution: the stage->phase PARTITION map _STAGE_TO_PHASE +
    _phase_for_stage + derive_phase_costs;
  * per-iteration cost attribution: derive_iteration_costs;
  * the PLAN-file derivations (wave-5 seam 9, unblocked once ops.planlint provided the
    plan regexes): _TERMINAL_OUTCOMES + _plan_marker_status_counts + derive_plan_size +
    derive_plan_authoring + derive_agents + derive_outcome. They read the plan-parsing
    regexes (_PLAN_TASK_LINE_RE/_PLAN_CHECKBOX_RE/_PLAN_HEADING_RE/_PLAN_PROMISE_RE by
    name + _planlint._parse_task_number) DOWN from ops.planlint; derive_outcome reads the
    metrics ledger via _metrics._read_metrics_jsonl and derive_agents the sessions via
    _sessionparse.collect_sessions.

Does NOT own: the plan-lint ENFORCEMENT regexes/linter themselves (-> ops.planlint,
imported DOWN), the metrics COMPUTE layer (ops.metrics), transcript parse + the read
cache (ops.sessionparse), the _read_metrics_jsonl ledger reader (-> ops.metrics with
loopview), loop analytics/trace/health/alerts/failure-ladder (ops.loopview), and
cmd_metrics/cmd_dashboard + their registrars (deferred). Detail:
  * the metrics COMPUTE layer -- aggregate_metrics/apply_pricing/resolve_session_identity/
    _agent_session_view/_cost_record/_finalize_record/_is_noise_session/
    _context_window_for_rows + the _DEFAULT_PRICES/_AGENT_ACTIVE_WINDOW_S/_UNATTRIBUTED/
    _TRUNCATION_RISK_THRESHOLD constants (agentware_lib.ops.metrics -- imported DOWN
    as _metrics; _TRUNCATION_RISK_THRESHOLD is a def-time default arg of derive_context_tax);
  * transcript parse + the single-instance read cache -- collect_sessions/_parse_iso/
    _empty_usage/_merge_usage + _USAGE_KEYS (agentware_lib.ops.sessionparse -- imported
    DOWN as _sessionparse, so the LRU stays one instance);
  * the metrics-ledger reader _read_metrics_jsonl (RESIDENT; co-moves to ops.metrics
    with loopview, whose derive_loop_* consume it);
  * loop analytics/trace/health/alerts/failure-ladder (agentware_lib.ops.loopview);
  * cmd_metrics/cmd_dashboard + the metrics/dashboard registrars (deferred: cmd_metrics
    free-references the FULL derive_* family -- now all owned here + resident-facade-
    reexported -- plus the resident _print_metrics_text/DASHBOARD_SERVER).

Layer deps: ops.derive -> ops.metrics + ops.sessionparse + ops.planlint (same layer,
downward) + retrieval.corpus (downward) + stdlib (datetime, os). No knowledge/features/
cli import.

Determinism: read-only (INV-2). Pure functions of the parsed transcript rows +
priced aggregate; no mutation of the canonical cached rows.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so resident callers
(cmd_metrics, derive_agents, the dashboard) resolve these as bare names and load_cli
tests reach them as CLI.<name>. None of the 14 is monkeypatched by any test
(grep-verified), so the by-name re-export is safe. ops.metrics/ops.sessionparse/
retrieval.corpus are ALSO bound as the module aliases _metrics/_sessionparse/_corpus
on the facade so the facade-held derive functions resolve _metrics.<fn> etc. against
the entrypoint namespace (the global-resolve gate walks them there).
Fan-in: 5 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import datetime
import os

import agentware_lib.ops.metrics as _metrics
import agentware_lib.ops.sessionparse as _sessionparse
import agentware_lib.retrieval.corpus as _corpus
from agentware_lib.ops.metrics import (
    _AGENT_ACTIVE_WINDOW_S, _DEFAULT_PRICES, _TRUNCATION_RISK_THRESHOLD,
    _UNATTRIBUTED)
from agentware_lib.ops.sessionparse import _USAGE_KEYS
import agentware_lib.ops.planlint as _planlint
from agentware_lib.ops.planlint import (
    _PLAN_TASK_LINE_RE, _PLAN_CHECKBOX_RE, _PLAN_HEADING_RE, _PLAN_PROMISE_RE)


def derive_agent_activity(kdir, kind, rows=None, now=None):
    """Per-agent (PLAN_AW / WORK_AW) activity rollup (pure, read-only, INV-2).

    Selects sessions whose resolved IDENTITY (resolve_session_identity) is `kind`
    — NOT a passive stage label — and folds them into: an ACTIVE marker (the
    most-recent session within the active window), a chronological history (each
    session carrying its honest name + complete/stopped/active status), cost/token
    /turn aggregates, and per-day + per-feature breakdowns. Loop runs are excluded
    here (they own the LOOP pillar). Noise (empty 0-turn) sessions are dropped.
    Idle-resilient: full history shows with no live run."""
    if rows is None:
        rows = _sessionparse.collect_sessions(kdir)

    sel = []
    for r in rows:
        if _metrics._is_noise_session(r):
            continue
        ident = _metrics.resolve_session_identity(r)
        if ident["kind"] != kind:
            continue
        r = dict(r)            # never mutate the cached/shared row
        r["_identity"] = ident
        sel.append(r)

    agg = _metrics.aggregate_metrics(sel)
    _metrics.apply_pricing(sel, agg, _DEFAULT_PRICES)  # adds cost_usd onto the COPIES + agg

    ordered = sorted(sel, key=lambda r: (r.get("start") or "",
                                         r.get("session_id") or ""),
                     reverse=True)
    ref = now if now is not None else \
        datetime.datetime.now(datetime.timezone.utc)
    active = False
    active_sid = None
    if ordered:
        last_dt = _sessionparse._parse_iso(ordered[0].get("end") or ordered[0].get("start"))
        if last_dt is not None and \
                (ref - last_dt).total_seconds() <= _AGENT_ACTIVE_WINDOW_S:
            active = True
            active_sid = ordered[0].get("session_id")

    def _status(r):
        if r.get("session_id") == active_sid:
            return "active"
        # complete (done signal) | incomplete (planner w/o plan) | ended (neutral).
        return r["_identity"].get("terminal", "ended")

    by_day = {}
    by_feature = {}
    for r in sel:
        d = r.get("date")
        if d:
            slot = by_day.setdefault(d, {"sessions": 0, "total_tokens": 0,
                                         "cost_usd": 0.0})
            slot["sessions"] += 1
            slot["total_tokens"] += r.get("total_tokens", 0)
            slot["cost_usd"] = round(slot["cost_usd"] + r.get("cost_usd", 0.0), 6)
        f = r["_identity"]["feature"] or _UNATTRIBUTED
        fr = by_feature.setdefault(f, {"sessions": 0, "total_tokens": 0,
                                       "cost_usd": 0.0})
        fr["sessions"] += 1
        fr["total_tokens"] += r.get("total_tokens", 0)
        fr["cost_usd"] = round(fr["cost_usd"] + r.get("cost_usd", 0.0), 6)

    incomplete = sum(1 for r in sel if _status(r) == "incomplete")
    attributed = sum(1 for r in sel if r["_identity"]["feature"])
    latest = ordered[0] if ordered else None

    return {
        "kind": kind,
        "active": active,
        "active_session": (_metrics._agent_session_view(latest, latest["_identity"],
                                               _status(latest))
                           if latest else None),
        "session_count": len(sel),
        "attributed_count": attributed,
        "incomplete_count": incomplete,
        "aggregate": {
            "total_tokens": agg.get("total_tokens", 0),
            "cost_usd": agg.get("cost_usd", 0.0),
            "turns": agg.get("turns", 0),
            "tool_calls": agg.get("tool_calls", 0),
            "subagent_count": agg.get("subagent_count", 0),
            "duration_seconds": agg.get("duration_seconds", 0.0),
            "cache_read_ratio": agg.get("cache_read_ratio", 0.0),
            "tokens": agg.get("tokens", _sessionparse._empty_usage()),
        },
        "by_day": by_day,
        "by_feature": by_feature,
        "sessions": [_metrics._agent_session_view(r, r["_identity"], _status(r))
                     for r in ordered],
        "features": sorted(k for k in by_feature if k != _UNATTRIBUTED),
    }


def derive_authoring(rows):
    """Attribute plan-authoring wall-time + tokens to plan-stage sessions.

    Sums wall time + total tokens over the rows classified `stage == 'plan'`
    (pure, derived from already-parsed session rows). Returns
    {wall_s, tokens, session_count, sessions}.
    """
    wall = 0.0
    tokens = 0
    sessions = []
    for r in rows:
        if r.get("stage") == "plan":
            wall += r.get("duration_seconds", 0.0)
            tokens += r.get("total_tokens", 0)
            sessions.append(r.get("session_id"))
    return {
        "wall_s": round(wall, 3),
        "tokens": tokens,
        "session_count": len(sessions),
        "sessions": sessions,
    }


def _cache_read_per_turn(cache_read, turns):
    """Avg cache-read (re-injected context) tokens per assistant turn (pure)."""
    if turns <= 0:
        return 0.0
    return round(cache_read / turns, 3)


def derive_context_tax(rows, kdir, window=None,
                       threshold=_TRUNCATION_RISK_THRESHOLD):
    """Read-only context-tax derivations (INV-2 — derives, never writes).

    The "context tax" is the silent overhead of re-injecting context every turn
    and the risk of overflowing the model window as the KB/loop grows:
      - cache_read_per_turn: cache_read_input_tokens / turns — the avg context
        re-read per assistant turn (overall + a per-day series).
      - injected_tokens: estimate_tokens over the CURRENT MAIN.md — the
        SessionStart-injected footprint that grows with the KB (snapshot value;
        absent MAIN.md -> 0).
      - context_window_pct / truncation_risk: the PEAK single-turn input-side
        token total vs a configurable model `window` size, flagged past
        `threshold` — the documented failure where a long loop fills the window
        and silently truncates.
    Annotates each row in `rows` with cache_read_per_turn + context_window_pct +
    truncation_risk, and returns the aggregate context_tax block (incl. by_day).
    """
    if not window or window <= 0:
        # Auto-detect the real ceiling from the models in scope (1M for Claude 4
        # family) so utilization isn't measured against a wrong 200k window.
        window = _metrics._context_window_for_rows(rows)

    # Current MAIN.md footprint (snapshot; missing/unreadable -> 0). Read-only.
    injected_tokens = 0
    main_md_bytes = 0
    try:
        with open(os.path.join(kdir, "MAIN.md"), "rb") as f:
            data = f.read()
        main_md_bytes = len(data)
        injected_tokens = _corpus.estimate_tokens(data.decode("utf-8", "replace"))
    except (OSError, ValueError):
        pass

    def _pct(peak):
        return round(peak / window, 6) if window else 0.0

    by_day = {}
    total_turns = 0
    total_cache_read = 0
    peak_overall = 0
    for r in rows:
        turns = r.get("turns", 0) or 0
        cr = r.get("tokens", {}).get("cache_read_input_tokens", 0)
        peak = r.get("peak_input_tokens", 0) or 0
        total_turns += turns
        total_cache_read += cr
        if peak > peak_overall:
            peak_overall = peak
        # Annotate the row (additive; existing keys untouched).
        r["cache_read_per_turn"] = _cache_read_per_turn(cr, turns)
        r["context_window_pct"] = _pct(peak)
        r["truncation_risk"] = bool(_pct(peak) >= threshold)
        date = r.get("date")
        if date:
            d = by_day.get(date)
            if d is None:
                d = {"turns": 0, "cache_read_input_tokens": 0,
                     "peak_input_tokens": 0}
                by_day[date] = d
            d["turns"] += turns
            d["cache_read_input_tokens"] += cr
            if peak > d["peak_input_tokens"]:
                d["peak_input_tokens"] = peak
    for d in by_day.values():
        d["cache_read_per_turn"] = _cache_read_per_turn(
            d["cache_read_input_tokens"], d["turns"])
        d["context_window_pct"] = _pct(d["peak_input_tokens"])
        d["truncation_risk"] = bool(d["context_window_pct"] >= threshold)

    return {
        "injected_tokens": injected_tokens,
        "main_md_bytes": main_md_bytes,
        "cache_read_per_turn": _cache_read_per_turn(total_cache_read,
                                                    total_turns),
        "context_window": window,
        "truncation_threshold": threshold,
        "peak_input_tokens": peak_overall,
        "context_window_pct": _pct(peak_overall),
        "truncation_risk": bool(_pct(peak_overall) >= threshold),
        "by_day": by_day,
    }


# Maps a session `stage` tag (Task 3 `classify_stage`) to the loop PHASE bucket it
# is attributed to. The loop runs pre -> main -> post; plan-authoring and ad-hoc
# interactive work get their own buckets; an unclassifiable session is `unknown`.
# This is a PARTITION: every session lands in EXACTLY one phase, so the per-phase
# `total_tokens` SUM reproduces the aggregate session total (no double counting).
_STAGE_TO_PHASE = {
    "loop-pre": "pre",
    "loop-main": "main",
    "loop-post": "post",
    "plan": "plan",
    "work": "work",
    "unknown": "unknown",
}


def _phase_for_stage(stage):
    return _STAGE_TO_PHASE.get(stage or "unknown", "unknown")


def derive_phase_costs(rows):
    """Attribute token usage + cost to each loop PHASE (pure, read-only, INV-2).

    Buckets every session row under the phase its `stage` maps to. Because the
    mapping is a partition (each session -> exactly one bucket), summing the
    per-phase `total_tokens` reproduces the aggregate session total EXACTLY — no
    double counting (asserted by tests). Expects `apply_pricing` to have annotated
    each row with `cost_usd` (treated as 0.0 when absent). Returns
    {by_phase: {phase: cost_record + sessions[]}, total_tokens, cost_usd}.
    """
    by_phase = {}
    total_tokens = 0
    total_cost = 0.0
    for r in rows:
        phase = _phase_for_stage(r.get("stage"))
        rec = by_phase.get(phase)
        if rec is None:
            rec = _metrics._cost_record(_sessionparse._empty_usage())
            rec["sessions"] = []
            by_phase[phase] = rec
        _sessionparse._merge_usage(rec["tokens"], r.get("tokens", {}))
        rec["total_tokens"] = sum(rec["tokens"][k] for k in _USAGE_KEYS)
        rec["cost_usd"] += r.get("cost_usd", 0.0)
        rec["session_count"] += 1
        sid = r.get("session_id")
        if sid is not None:
            rec["sessions"].append(sid)
        total_tokens += r.get("total_tokens", 0)
        total_cost += r.get("cost_usd", 0.0)
    for rec in by_phase.values():
        _metrics._finalize_record(rec)
    return {
        "by_phase": by_phase,
        "total_tokens": total_tokens,
        "cost_usd": round(total_cost, 6),
    }


def derive_iteration_costs(events):
    """Per-iteration loop structure from the metrics.jsonl emission (read-only).

    Groups the per-iteration loop events (those carrying an integer `iteration`)
    into an ordered series with per-phase wall-time + task-burndown signals. Each
    record: {iteration, phases: {phase: wall_s}, phase_wall_s, tasks_remaining,
    tasks_done_delta, self_heal_count}. Returns [] when the emission channel is
    absent (Task 6/7 add it) — forward-compatible, never fabricates per-iteration
    data that the loop has not yet emitted.
    """
    by_it = {}
    order = []
    for e in events:
        # Skip the non-iteration event types (Task 7): task_transition events also
        # carry an integer `iteration` but describe a single task's lifecycle, not
        # the per-iteration loop structure; terminal events have no iteration. Only
        # the Task-6 per-iteration/phase events (no `event` discriminator) fold here.
        if e.get("event") in ("task_transition", "terminal"):
            continue
        it = e.get("iteration")
        if not isinstance(it, int) or isinstance(it, bool):
            continue
        rec = by_it.get(it)
        if rec is None:
            rec = {
                "iteration": it,
                "phases": {},
                "phase_wall_s": 0.0,
                "tasks_remaining": e.get("tasks_remaining"),
                "tasks_done_delta": 0,
                "self_heal_count": e.get("self_heal_count"),
            }
            by_it[it] = rec
            order.append(it)
        phase = e.get("phase") or e.get("stage") or "main"
        wall = e.get("phase_wall_s") or 0.0
        try:
            wall = float(wall)
        except (TypeError, ValueError):
            wall = 0.0
        rec["phases"][phase] = round(rec["phases"].get(phase, 0.0) + wall, 3)
        rec["phase_wall_s"] = round(rec["phase_wall_s"] + wall, 3)
        if isinstance(e.get("tasks_done_delta"), int) and \
                not isinstance(e.get("tasks_done_delta"), bool):
            rec["tasks_done_delta"] += e["tasks_done_delta"]
        if e.get("tasks_remaining") is not None:
            rec["tasks_remaining"] = e.get("tasks_remaining")
        if e.get("self_heal_count") is not None:
            rec["self_heal_count"] = e.get("self_heal_count")
    return [by_it[i] for i in sorted(order)]


# The outcomes derive_outcome trusts VERBATIM off the metrics ledger. Includes
# `premise_falsified` (this tree's R-GND-01 halt, plan task 34's decided literal):
# the donor tuple omits it, which silently collapsed a premise-gate halt to
# `unknown` and made the gate's precision unmeasurable. Deliberately NARROWER than
# ops.fleet.LOOP_OUTCOME_ENUM: the R-EXEC-06 hard blockers
# (adversarial_review_blocked / blocked_on_ambiguity) are preserved BY NAME through
# _graph_raw_terminal_outcome instead, because mapping them here would collapse the
# graph HALT signal (recorded in the legacy module's own comment).
_TERMINAL_OUTCOMES = (
    "completed", "hit_max_iterations", "post_hook_failure", "pre_hook_abort",
    "premise_falsified",
)

def _plan_marker_status_counts(plan_path):
    """Count plan.md task markers by status -> (open, done); open = ⬜/🟡 (pure).

    Reads plan.md with the SAME `_PLAN_TASK_LINE_RE` the linter uses. Returns
    (0, 0) on an unreadable plan. Read-only.
    """
    open_n = 0
    done_n = 0
    try:
        with open(plan_path, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                m = _PLAN_TASK_LINE_RE.match(ln)
                if not m:
                    continue
                if m.group(1) == "✅":
                    done_n += 1
                else:
                    open_n += 1
    except OSError:
        return (0, 0)
    return (open_n, done_n)

def derive_plan_size(plan_path):
    """Read-only plan-authoring size metrics from a plan.md (pure parse).

    Returns {tasks, phases, accept, bytes, promise} or None when the plan is
    unreadable. NEVER writes — observation only (INV-2).
      - tasks:   well-formed task markers (`_PLAN_TASK_LINE_RE`, any status)
      - phases:  distinct MAJOR groups among phased `**N.M**` markers
                 (0 for a flat plan with no phasing)
      - accept:  `- [ ]` acceptance-criteria checkboxes under the
                 '## Acceptance criteria' heading
      - bytes:   plan.md size in bytes
      - promise: a `<promise>…</promise>` token is present
    """
    try:
        with open(plan_path, "rb") as f:
            data = f.read()
    except (OSError, ValueError):
        return None
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()
    tasks = 0
    majors = []
    for ln in lines:
        m = _PLAN_TASK_LINE_RE.match(ln)
        if not m:
            continue
        tasks += 1
        parts = _planlint._parse_task_number(m.group(2))
        if len(parts) > 1 and parts[0] not in majors:
            majors.append(parts[0])
    accept = 0
    in_accept = False
    for ln in lines:
        hm = _PLAN_HEADING_RE.match(ln)
        if hm:
            in_accept = "acceptance" in hm.group(1).lower()
            continue
        if in_accept and _PLAN_CHECKBOX_RE.match(ln):
            accept += 1
    return {
        "tasks": tasks,
        "phases": len(majors),
        "accept": accept,
        "bytes": len(data),
        "promise": bool(_PLAN_PROMISE_RE.search(text)),
    }

def derive_plan_authoring(kdir, rows):
    """The PLANNER's OUTPUT: every plan that was AUTHORED (a full Write of a
    work/<f>/plan.md), attributed by that authoring ACTION — independent of which
    session did it (an inline builder authors a plan inside a worker session, so
    'pure planner sessions' barely exist; the PLANS still do). Each entry carries
    the feature, when it was first authored, the authoring session, and the plan's
    size + completion read live from its plan.md markers. This is the honest
    'planner metrics' an inline-building operator actually has. Read-only."""
    first = {}
    for r in rows:
        sid = r.get("session_id")
        ts = r.get("start") or ""
        for f in (r.get("plan_authored") or []):
            cur = first.get(f)
            if cur is None or ts < cur["authored"]:
                first[f] = {"feature": f, "authored": ts,
                            "authored_session": sid}
    out = []
    for f, p in first.items():
        pm = os.path.join(kdir, "work", f, "plan.md")
        open_n, done_n = (_plan_marker_status_counts(pm)
                          if os.path.isfile(pm) else (0, 0))
        total = open_n + done_n
        if total > 0 and open_n == 0:
            status = "complete"
        elif done_n > 0:
            status = "in_progress"
        else:
            status = "open"
        out.append({
            "feature": f,
            "authored": p["authored"],
            "authored_session": p["authored_session"],
            "tasks_total": total,
            "tasks_done": done_n,
            "complete": total > 0 and open_n == 0,
            "status": status,
        })
    out.sort(key=lambda x: (x["authored"] or "", x["feature"]), reverse=True)
    return out

def derive_agents(kdir):
    """Both agent rollups (PLAN_AW + WORK_AW) from a SINGLE session read (the
    /api/agents payload). Read-only; the panels select their slice. The plan slice
    additionally carries `plans` — the authored-plan OUTPUT (see derive_plan_authoring)
    — which is the meaningful planner view for an operator who plans inline."""
    rows = _sessionparse.collect_sessions(kdir)
    plan = derive_agent_activity(kdir, "plan", rows=rows)
    plan["plans"] = derive_plan_authoring(kdir, rows)
    return {
        "plan": plan,
        "work": derive_agent_activity(kdir, "work", rows=rows),
    }

def derive_outcome(kdir, feature, events=None):
    """Derive a feature's terminal run outcome (read-only, NEVER writes — INV-2).

    Outcome ∈ _TERMINAL_OUTCOMES (plus `unknown` when the artifacts are
    indeterminate). Precedence:
      1. AUTHORITATIVE — the LAST terminal event in logs/metrics.jsonl (Task 7
         appends {feature, outcome, iterations_used, self_heal_count, ...} on loop
         exit). Used verbatim; it is the deterministic source for all four states.
      2. ARTIFACT heuristic over the persisted feature dir — only `completed` is
         asserted with confidence (a `.loop/.done` signal, OR every plan task ✅).
         Anything else stays `unknown` rather than GUESS a failure mode the
         artifacts cannot distinguish (R-FAIL — never guess).
    Returns {outcome, source, iterations_used?, self_heal_count?, signals} or None
    when no feature is in scope.
    """
    if not feature:
        return None
    if events is None:
        events = _metrics._read_metrics_jsonl(kdir, feature=feature)
    terminal = [e for e in events
                if e.get("feature") == feature
                and e.get("outcome") in _TERMINAL_OUTCOMES]
    if terminal:
        last = terminal[-1]
        return {
            "outcome": last["outcome"],
            "source": "metrics.jsonl",
            "iterations_used": last.get("iterations_used"),
            "self_heal_count": last.get("self_heal_count"),
            "signals": {"terminal_events": len(terminal)},
        }
    fdir = os.path.join(kdir, "work", feature)
    done_flag = os.path.isfile(os.path.join(fdir, ".loop", ".done"))
    assessment = os.path.isfile(os.path.join(fdir, "assessment.md"))
    open_n, done_n = _plan_marker_status_counts(os.path.join(fdir, "plan.md"))
    signals = {"loop_done": done_flag, "assessment": assessment,
               "open_markers": open_n, "done_markers": done_n}
    if done_flag:
        return {"outcome": "completed", "source": "loop-done", "signals": signals}
    if done_n > 0 and open_n == 0:
        return {"outcome": "completed",
                "source": "assessment" if assessment else "plan-markers",
                "signals": signals}
    return {"outcome": "unknown", "source": "artifacts", "signals": signals}


_EXPORTS = [
    'derive_agent_activity',
    'derive_authoring',
    '_cache_read_per_turn',
    'derive_context_tax',
    '_STAGE_TO_PHASE',
    '_phase_for_stage',
    'derive_phase_costs',
    'derive_iteration_costs',
    '_TERMINAL_OUTCOMES',
    '_plan_marker_status_counts',
    'derive_plan_size',
    'derive_plan_authoring',
    'derive_agents',
    'derive_outcome',
]
