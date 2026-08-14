"""agentware_lib.ops.metrics.metricsaggregate -- cross-session AGGREGATION + session IDENTITY.

Owns: the deterministic, read-only fold over parsed session rows and the identity
model laid on top of them --
  * cross-session aggregation: aggregate_metrics (per-session rows -> feature/global
    totals) + build_session_index (one pass over every transcript, fanned out to the
    features it references) and the feature-grouping helpers
    (_group_events_by_feature/_session_matched_features);
  * session identity: resolve_session_identity + _is_noise_session +
    _agent_session_view + the _AGENT_ACTIVE_WINDOW_S/_UNATTRIBUTED constants.

Does NOT own: pricing, the cost/day/feature rollups, the p50 helper _median and the
context-window sizing (the sibling agentware_lib.ops.metrics.metricspricing); the
logs/metrics.jsonl channel reader + the onboarding metric emitter (the sibling
agentware_lib.ops.metrics.metricsemit); the transcript PARSE + the single-instance
read cache (agentware_lib.ops.sessionparse -- imported DOWN, so the LRU stays one
instance); the derive_* reporting family (agentware_lib.ops.derive); loop
analytics/trace/health/alerts/failure-ladder
(agentware_lib.ops.loopanalytics/looptrace/loophealth/alerts/failureladder); the
cmd_metrics/cmd_dashboard CLI handlers (their own sibling ops cli module, whose name
this package never spells -- naming it as an import target would close the
ops.metrics<->ops.derive cycle); fleet/org/team
(agentware_lib.ops.fleet).

Layer deps: ops.metrics.metricsaggregate -> ops.sessionparse (same layer, downward to
the leaf) + stdlib (os). No core/knowledge/retrieval/features/cli import, and NO
sibling import inside the package -- this module is a leaf of its own package.

Determinism: read-only (INV-2). Pure functions of the parsed transcript rows;
parse_session (reached through the _sessionparse alias) returns a DEEP COPY, so no
caller can corrupt the canonical cached row. The (mtime,size)-keyed memo behind
_session_matched_features is the SINGLE shared cache owned by ops.sessionparse and is
reached ONLY as _sessionparse._cached_by_key/_cached_text/_file_key -- never rebound
here -- so _sessionparse._reset_read_cache() stays authoritative for this reader too.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so resident callers
(the derive_* family, cmd_metrics/cmd_dashboard, fleet) resolve these as bare names
and load_cli tests + the dashboard reach them as CLI.<name>. None is monkeypatched by
any test (grep-verified), so the by-name re-export is safe.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import os

import agentware_lib.ops.sessionparse as _sessionparse
from agentware_lib.ops.sessionparse import _USAGE_KEYS


def _group_events_by_feature(all_events):
    """{feature: [events]} from a single full metrics.jsonl read — the in-memory
    equivalent of N _read_metrics_jsonl(feature=f) filtered reads (same order)."""
    out = {}
    for e in all_events:
        out.setdefault(e.get("feature"), []).append(e)
    return out


def _session_matched_features(sdir, features, features_key):
    """Frozenset of `features` whose name is a substring of this session's
    transcript — IDENTICAL membership to _session_feature_match (since 'work/f'
    contains 'f', the bare-name test subsumes it). Memoized by (main.jsonl
    mtime/size, features identity) so the features×sessions substring scan is
    done ONCE per session per request instead of 3× per feature (analytics +
    context-check + dup-check each used to re-scan)."""
    main_path = os.path.join(sdir, "main.jsonl")
    fk = _sessionparse._file_key(main_path)

    def _compute():
        text = _sessionparse._cached_text(main_path)
        if text is None:
            return frozenset()
        return frozenset(f for f in features if f in text)
    return _sessionparse._cached_by_key(("matched_feats", os.path.abspath(sdir), features_key),
                          fk, _compute)


def build_session_index(kdir, features):
    """Read every session ONCE and group it under each feature it references,
    returning {'rows': {feature: [session rows]}, 'dirs': {feature: [(name, dir)]}}.

    Byte-identical to calling collect_sessions(feature=f) and _trace_session_dirs(
    feature=f) for each feature separately (same membership, same ordering), but
    collapses the O(features × sessions × file) hot path to O(sessions × file):
    each transcript is matched + parsed exactly once and fanned out to its
    features. Read-only; safe to share across the bulk loop derivations."""
    root = _sessionparse.logs_sessions_dir(kdir)
    features_key = hash(tuple(features))
    rows = {f: [] for f in features}
    dirs = {f: [] for f in features}
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            sdir = os.path.join(root, name)
            if not os.path.isdir(sdir):
                continue
            matched = _session_matched_features(sdir, features, features_key)
            if not matched:
                continue
            row = _sessionparse.parse_session(sdir)
            for f in matched:
                dirs[f].append((name, sdir))
                if row is not None:
                    rows[f].append(row)
    for f in features:
        rows[f].sort(key=lambda r: (r.get("start") is None, r.get("start") or "",
                                    r.get("session_id") or ""))
    return {"rows": rows, "dirs": dirs}


def aggregate_metrics(rows):
    """Aggregate per-session rows into feature/global totals."""
    usage = _sessionparse._empty_usage()
    tools = {}
    agg = {
        "session_count": len(rows),
        "turns": 0,
        "tool_calls": 0,
        "subagent_count": 0,
        "duration_seconds": 0.0,
        "tokens": usage,
        "total_tokens": 0,
        "tools": tools,
    }
    for r in rows:
        agg["turns"] += r["turns"]
        agg["tool_calls"] += r["tool_calls"]
        agg["subagent_count"] += r["subagent_count"]
        agg["duration_seconds"] += r["duration_seconds"]
        _sessionparse._merge_usage(usage, r["tokens"])
        _sessionparse._merge_tools(tools, r["tools"])
    agg["duration_seconds"] = round(agg["duration_seconds"], 3)
    agg["total_tokens"] = sum(usage[k] for k in _USAGE_KEYS)
    return agg


# A session counts as ACTIVE when its last activity is within this window — the
# "active planner/worker" signal the dashboard surfaces (best-effort, persisted).
_AGENT_ACTIVE_WINDOW_S = 12 * 60


# Group label for sessions whose feature can't yet be determined (anonymous
# planner before a plan exists, ad-hoc worker not yet tied to a feature).
_UNATTRIBUTED = "(unattributed)"


def _is_noise_session(r):
    """A trivial/empty session that should NOT count as planner/worker activity.

    SessionStart hooks create a session dir + a near-empty main.jsonl even when no
    real work happens; those showed up as bogus '0 turns / 0 tokens / 2s' rows.
    Drop a session with no assistant turn, or with neither tokens nor tool calls."""
    if (r.get("turns", 0) or 0) <= 0:
        return True
    if (r.get("total_tokens", 0) or 0) <= 0 and (r.get("tool_calls", 0) or 0) <= 0:
        return True
    return False


def resolve_session_identity(row):
    """Resolve a session's AGENT IDENTITY per the operator's model (read-only).

    Returns {kind, feature, name, confidence, complete}:
      - kind ∈ {plan, work, loop}.
      - feature: the work feature this session belongs to, or None when it cannot
        yet be determined (anonymous planner before a plan exists; ad-hoc worker
        not yet tied to a feature).
      - name: human display label (the feature, or 'planner <id>' / 'ad-hoc <id>').
      - confidence: 'high' (attributed from an ACTION — a file it edited / plan it
        wrote / loop it ran), 'provisional' (inferred), 'pending' (not determinable
        yet — backfills once an action reveals the feature).
      - complete: did the run reach its natural end? plan authored (planner) /
        promise emitted (worker/loop). False ⇒ stopped or in-progress.

    Identity comes from what the session DID, never from passive mentions of a
    feature in injected context (which is why the old model mislabeled every
    session as 'tokto')."""
    sid = row.get("session_id") or "????????"
    short = sid.split("-")[0][:8]
    stage = row.get("stage") or "unknown"
    af = row.get("action_feature")
    planned = row.get("plan_authored") or []
    planner = bool(row.get("planner_spawned"))
    promise = bool(row.get("promise_emitted"))
    # A session EXECUTED if it edited any non-planning file, spawned the executor,
    # or emitted a completion promise. This is the line between a PLANNER (writes a
    # plan and hands off) and a WORKER (builds the feature) — even when ONE session
    # did both, heavy code editing makes it a worker that ALSO authored its plan,
    # NOT a planner. (A 668-turn / 286M-token build that wrote its plan first is a
    # worker, not a planner.)
    executed = bool(row.get("code_edits", 0)) or \
        bool(row.get("executor_spawned")) or promise

    # 1) LOOP — the orchestrated loop exports AGENTWARE_STAGE=loop-*; a loop ALWAYS
    #    has a plan to follow, so its feature IS known (the feature it operated on).
    if stage.startswith("loop-"):
        feat = af or row.get("feature")
        return {"kind": "loop", "feature": feat,
                "name": feat or ("loop " + short),
                "confidence": "high" if af else "provisional",
                "complete": promise,
                "terminal": "complete" if promise else "ended"}

    # 2) PLANNER — a session that authored a plan (or ran the planner agent) and DID
    #    NOT execute. Authoritative action only, never the polluted classify_stage
    #    'plan' label. A planner with no plan produced yet is ANONYMOUS + INCOMPLETE
    #    (named by session id until a plan exists — exactly the operator's model).
    if not executed:
        if planned:
            return {"kind": "plan", "feature": planned[0], "name": planned[0],
                    "confidence": "high", "complete": True,
                    "terminal": "complete"}
        if planner:
            return {"kind": "plan", "feature": af, "name": af or
                    ("planner " + short),
                    "confidence": "provisional" if af else "pending",
                    "complete": False, "terminal": "incomplete"}

    # 3) WORKER — execution (NON-loop), including a session that authored its plan
    #    and then built it. Named by the feature it edited (the plan it wrote, or
    #    the dominant action feature); anonymous ad-hoc when none is determinable.
    #    'complete' only on an emitted promise, else neutral 'ended'.
    term = "complete" if promise else "ended"
    feat = af or (planned[0] if planned else None)
    if feat:
        return {"kind": "work", "feature": feat, "name": feat,
                "confidence": "high", "complete": promise, "terminal": term}
    return {"kind": "work", "feature": None, "name": "ad-hoc " + short,
            "confidence": "pending", "complete": promise, "terminal": term}


def _agent_session_view(r, identity=None, status=None):
    """Compact, render-ready projection of a session row for the agent panels,
    carrying the resolved identity (name / feature / kind / confidence / status)."""
    ident = identity or resolve_session_identity(r)
    return {
        "session_id": r.get("session_id"),
        "stage": r.get("stage"),
        "kind": ident["kind"],
        "name": ident["name"],
        "feature": ident["feature"],
        "confidence": ident["confidence"],
        "complete": ident["complete"],
        "status": status or ("complete" if ident["complete"] else "stopped"),
        "start": r.get("start"),
        "end": r.get("end"),
        "date": r.get("date"),
        "turns": r.get("turns", 0),
        "main_turns": r.get("main_turns", 0),
        "subagent_turns": r.get("subagent_turns", 0),
        "tool_calls": r.get("tool_calls", 0),
        "subagent_count": r.get("subagent_count", 0),
        "duration_seconds": r.get("duration_seconds", 0.0),
        "total_tokens": r.get("total_tokens", 0),
        "peak_input_tokens": r.get("peak_input_tokens", 0),
        "cache_read_ratio": r.get("cache_read_ratio", 0.0),
        "cost_usd": r.get("cost_usd", 0.0),
        "models": sorted((r.get("models") or {}).keys()),
    }


_EXPORTS = [
    '_group_events_by_feature',
    '_session_matched_features',
    'build_session_index',
    'aggregate_metrics',
    '_AGENT_ACTIVE_WINDOW_S',
    '_UNATTRIBUTED',
    '_is_noise_session',
    'resolve_session_identity',
    '_agent_session_view',
]
