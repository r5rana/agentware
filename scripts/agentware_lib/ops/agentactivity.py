"""agentware_lib.ops.agentactivity — the per-agent ACTIVITY rollup (layer: ops).

Owns: the agent pillar's rollup — selecting the sessions belonging to ONE resolved
agent identity (`plan` / `work` / `loop`), deciding whether that agent is ACTIVE
right now, folding its per-day and per-feature breakdowns, and assembling the
payload the Agents panel reads: `_select_agent_sessions`, `_active_marker`,
`_agent_status`, `_activity_breakdowns` and `derive_agent_activity`.

Does NOT own: the identity MODEL itself (`resolve_session_identity` /
`_agent_session_view` / `_is_noise_session` / `aggregate_metrics` / `apply_pricing`
live in `ops.metrics`, imported DOWN as `_metrics`); transcript parse and the read
cache (`ops.sessionparse`, as `_sessionparse`); and the rest of the `derive_*`
reporting family — authoring, context tax, phase/iteration costs and the plan-file
derivations — which stay in `ops.derive`. `ops.derive.derive_agents` is the one
caller: it reads the sessions ONCE and asks for two slices.

Why it is a module and not a section of `ops.derive`: every other `derive_*` is a
pure fold over already-parsed rows, with no notion of WHO ran them and no
reference instant. This one carries both — an identity taxonomy and a wall-clock
`now` against which "active" is decided — which is a second obligation, and
`ops.derive` had reached 587 of its 600-line cap holding them together (plan task
99, wave 4). The split is at the obligation, not at the midpoint: nothing here
reads a plan file, and nothing in `ops.derive` resolves an identity.

Layer deps: ops.agentactivity -> ops.metrics + ops.sessionparse (same layer, both
DOWNWARD -- neither imports this) + stdlib datetime. No knowledge/features/cli
import. `ops.derive` imports THIS, so the ops DAG stays acyclic.

Determinism: read-only (INV-2). `now` is a parameter precisely so the ACTIVE
decision is testable; it falls back to the clock only when the caller declines to
pin it. Selected rows are COPIED before the identity is stamped on them, so the
shared parse cache is never mutated.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import datetime

import agentware_lib.ops.metrics as _metrics
import agentware_lib.ops.sessionparse as _sessionparse
from agentware_lib.ops.metrics import (
    _AGENT_ACTIVE_WINDOW_S, _DEFAULT_PRICES, _UNATTRIBUTED)


def _select_agent_sessions(rows, kind):
    """The rows whose RESOLVED identity is `kind`, each carrying its identity.

    Selection is on the resolved identity and never on the passive `stage` label
    — that label is inferred from prose and a session that merely discussed
    planning would otherwise be counted as a planner. Noise (empty, zero-turn)
    sessions are dropped before anything else, so they cannot inflate a count or
    become the "most recent" row and fake an active agent.

    Each selected row is a shallow COPY: `_identity` is stamped onto it and the
    pricing pass writes `cost_usd` onto it, and both would otherwise mutate the
    rows held in the shared session cache."""
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
    return sel


def _active_marker(ordered, now):
    """Is this agent running right now, and in which session? -> (active, sid).

    The newest session is the only candidate — an older one cannot be live while
    a newer one exists — and it counts as active when it was last seen inside
    `_AGENT_ACTIVE_WINDOW_S`. `end` is preferred over `start` because a session
    still being written has no end yet, and falling back to `start` is what keeps
    a long-running live session from ageing out mid-run. An unparseable stamp is
    NOT active: an idle agent read as running is the failure mode this window
    exists to avoid."""
    ref = now if now is not None else \
        datetime.datetime.now(datetime.timezone.utc)
    if not ordered:
        return False, None
    last_dt = _sessionparse._parse_iso(ordered[0].get("end")
                                       or ordered[0].get("start"))
    if last_dt is None or \
            (ref - last_dt).total_seconds() > _AGENT_ACTIVE_WINDOW_S:
        return False, None
    return True, ordered[0].get("session_id")


def _agent_status(r, active_sid):
    """One session's display status: active | complete | incomplete | ended.

    The live session wins outright; every other session reports the terminal its
    identity resolved to (complete = the run signalled done, incomplete = a
    planner that produced no plan, ended = neither, which is neutral rather than
    a failure)."""
    if r.get("session_id") == active_sid:
        return "active"
    # complete (done signal) | incomplete (planner w/o plan) | ended (neutral).
    return r["_identity"].get("terminal", "ended")


def _activity_breakdowns(sel):
    """Per-day and per-feature rollups over the selected sessions -> (by_day,
    by_feature).

    A session with no date is left out of `by_day` entirely rather than filed
    under a placeholder key, because the day series is plotted and an invented
    bucket would draw. A session with no feature IS counted, under
    `_UNATTRIBUTED`: unattributed work is real spend and dropping it would make
    the per-feature costs silently fail to sum to the total. Costs are rounded at
    each accumulation to keep the fold associative in float."""
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
    return by_day, by_feature


def derive_agent_activity(kdir, kind, rows=None, now=None):
    """Per-agent (plan / work / loop) activity rollup (pure, read-only, INV-2).

    Selects sessions whose resolved IDENTITY (resolve_session_identity) is `kind`
    — NOT a passive stage label — and folds them into: an ACTIVE marker (the
    most-recent session within the active window), a chronological history (each
    session carrying its honest name + complete/stopped/active status), cost/token
    /turn aggregates, and per-day + per-feature breakdowns. Loop runs are excluded
    from the two agent panels here (they own the LOOP pillar). Noise (empty
    0-turn) sessions are dropped. Idle-resilient: full history shows with no live
    run."""
    if rows is None:
        rows = _sessionparse.collect_sessions(kdir)

    sel = _select_agent_sessions(rows, kind)
    agg = _metrics.aggregate_metrics(sel)
    _metrics.apply_pricing(sel, agg, _DEFAULT_PRICES)  # adds cost_usd onto the COPIES + agg

    ordered = sorted(sel, key=lambda r: (r.get("start") or "",
                                         r.get("session_id") or ""),
                     reverse=True)
    active, active_sid = _active_marker(ordered, now)
    by_day, by_feature = _activity_breakdowns(sel)

    incomplete = sum(1 for r in sel
                     if _agent_status(r, active_sid) == "incomplete")
    attributed = sum(1 for r in sel if r["_identity"]["feature"])
    latest = ordered[0] if ordered else None

    return {
        "kind": kind,
        "active": active,
        "active_session": (_metrics._agent_session_view(
                               latest, latest["_identity"],
                               _agent_status(latest, active_sid))
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
        "sessions": [_metrics._agent_session_view(
                         r, r["_identity"], _agent_status(r, active_sid))
                     for r in ordered],
        "features": sorted(k for k in by_feature if k != _UNATTRIBUTED),
    }


_EXPORTS = [
    '_select_agent_sessions',
    '_active_marker',
    '_agent_status',
    '_activity_breakdowns',
    'derive_agent_activity',
]

__all__ = list(_EXPORTS)
