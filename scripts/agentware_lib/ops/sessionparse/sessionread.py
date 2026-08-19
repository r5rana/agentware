"""agentware_lib.ops.sessionparse.sessionread -- per-SESSION parse + collection (layer: ops).

Owns: the sessions-dir resolver (LOGS_SESSIONS_REL/logs_sessions_dir), the pure
stage label (classify_stage), the dominant-feature pick (_dominant_feature), the
memoized per-session parser (parse_session) and its uncached core
(_parse_session_uncached) which folds a session's main.jsonl plus every
subagents/*.jsonl into one metric row, the deterministic transcript feature match
(_session_feature_match) and the ordered, filtered collector (collect_sessions).

⚠️ MONKEYPATCH SEAM: parse_session resolves _parse_session_uncached in THIS
module's OWN globals, so the spy in tests/integration/test_loop_perf.py MUST be installed on
THIS module (sessionread._parse_session_uncached), never on the aggregator package
or the entrypoint facade -- either of those holds a re-exported COPY of the
function object and patching it is a silent no-op. The two functions are kept in
ONE module precisely so that seam stays a single hop.

Does NOT own: the (mtime_ns,size)-keyed single-instance LRU read cache, the file
identity keys and the two time helpers ->
agentware_lib.ops.sessionparse.sessioncache; the ONE-file JSONL aggregation, the
transcript regexes, the usage-key tuples and the merge helpers ->
agentware_lib.ops.sessionparse.sessionusage; cross-session aggregation, pricing and
the derive_* reporting family -> agentware_lib.ops.metrics / agentware_lib.ops.derive.
Layer deps: ops.sessionparse.sessionread -> stdlib (copy, os) + the two
SAME-PACKAGE siblings agentware_lib.ops.sessionparse.sessioncache and
agentware_lib.ops.sessionparse.sessionusage, both reached via a module ALIAS by
ATTRIBUTE so the read cache stays ONE instance and a monkeypatch of an owner
propagates. Neither sibling imports this module, so the package graph is acyclic.
No core/knowledge/retrieval/features/cli import.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Determinism: read-only (INV-2). _parse_session_uncached is a pure function of the
transcript bytes; parse_session returns a DEEP COPY of the cached row so callers
that mutate it (apply_pricing adds cost fields in place) can never corrupt the
canonical cached row; the cache auto-invalidates on any file change (mtime_ns/size
key), and collect_sessions sorts by (start, session_id) so output is byte-stable.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so resident callers
resolve these as bare names and load_cli tests + the dashboard reach them as
CLI.<name>.
"""
import copy
import os

import agentware_lib.ops.sessionparse.sessioncache as sessioncache
import agentware_lib.ops.sessionparse.sessionusage as sessionusage


LOGS_SESSIONS_REL = os.path.join("logs", "sessions")


def classify_stage(loop_stage, plan_hits, work_hits):
    """Pure stage label from the accumulated signals (documented heuristic).

    The loop's own AGENTWARE_STAGE marker is authoritative. Otherwise EXECUTION
    wins ties and presence: a session that emitted ANY execution verb (completion
    promise, `plan set-status`, worklog scan, a `learn` write) actually executed,
    so it is "work" even if the planner skill name also appears (that name recurs
    in the available-skills reminder on many lines and must never, on its own,
    label a long execution session as "plan"). Only a session with planner signals
    and NO execution signal is "plan"."""
    if loop_stage:
        return loop_stage
    if work_hits > 0:
        return "work"
    if plan_hits > 0:
        return "plan"
    return "unknown"


def _dominant_feature(feature_counts):
    """The most-referenced feature in a transcript (tie-break: lexical)."""
    if not feature_counts:
        return None
    return sorted(feature_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def logs_sessions_dir(kdir):
    return os.path.join(kdir, LOGS_SESSIONS_REL)


def parse_session(session_dir):
    """Parse one session directory into a metrics row (read-only).

    Memoized by main.jsonl's (mtime_ns, size): re-parsing a 6 MB transcript once
    per feature is the loop endpoints' dominant cost. Returns a DEEP COPY so
    callers that mutate the row (e.g. apply_pricing adds cost fields in place)
    can never corrupt the cached canonical row. Live-run correct: main.jsonl
    changes every turn, which changes the key and invalidates the entry."""
    main_path = os.path.join(session_dir, "main.jsonl")
    if not os.path.isfile(main_path):
        return None
    fk = sessioncache._file_key(main_path)
    row = sessioncache._cached_by_key(
        ("parse_session", os.path.abspath(session_dir)), fk,
        lambda: _parse_session_uncached(session_dir))
    return copy.deepcopy(row) if row is not None else None


def _parse_session_uncached(session_dir):
    """Parse one session directory into a metrics row (read-only).

    Wall time is computed from main.jsonl's first->last timestamped line only.
    Token + tool totals fold in subagents/*.jsonl. subagent_count is the number
    of subagent transcript files. Returns None if there is no main.jsonl.
    """
    main_path = os.path.join(session_dir, "main.jsonl")
    if not os.path.isfile(main_path):
        return None
    main = sessionusage.aggregate_jsonl(main_path)

    usage = sessionusage._empty_usage()
    sessionusage._merge_usage(usage, main["usage"])
    models = {}
    sessionusage._merge_models(models, main["models"])
    tools = dict(main["tools"])
    tool_calls = main["tool_calls"]
    turns = main["turns"]
    # Peak single-turn context fill: max across the main loop AND each subagent
    # (every agent has its OWN window, so the peak is a max, never a sum).
    peak_input = main["peak_input_tokens"]

    sub_dir = os.path.join(session_dir, "subagents")
    subagent_files = []
    if os.path.isdir(sub_dir):
        subagent_files = sorted(
            n for n in os.listdir(sub_dir) if n.endswith(".jsonl"))
    sub_usage = sessionusage._empty_usage()
    sub_tools = {}
    sub_turns = 0
    # Identity signals fold the subagents in too (the planner/executor run AS
    # subagents, so their plan-authoring / action features live there).
    action_features = dict(main["action_features"])
    plan_authored = dict(main["plan_authored"])
    code_edits = main["code_edits"]
    planner_spawned = main["planner_spawned"]
    executor_spawned = main["executor_spawned"]
    promise_emitted = main["promise_emitted"]
    for name in subagent_files:
        sa = sessionusage.aggregate_jsonl(os.path.join(sub_dir, name))
        sessionusage._merge_usage(usage, sa["usage"])
        sessionusage._merge_usage(sub_usage, sa["usage"])
        sessionusage._merge_models(models, sa["models"])
        sessionusage._merge_tools(tools, sa["tools"])
        sessionusage._merge_tools(sub_tools, sa["tools"])
        tool_calls += sa["tool_calls"]
        turns += sa["turns"]
        sub_turns += sa["turns"]
        if sa["peak_input_tokens"] > peak_input:
            peak_input = sa["peak_input_tokens"]
        for f, c in sa["action_features"].items():
            action_features[f] = action_features.get(f, 0) + c
        plan_authored.update(sa["plan_authored"])
        code_edits += sa["code_edits"]
        planner_spawned = planner_spawned or sa["planner_spawned"]
        executor_spawned = executor_spawned or sa["executor_spawned"]
        promise_emitted = promise_emitted or sa["promise_emitted"]

    duration = sessioncache._duration_seconds(main["_first_dt"], main["_last_dt"])
    total_tokens = sum(usage[k] for k in sessionusage._USAGE_KEYS)
    start = main["ts_first"]
    # ACTION-based feature attribution (what the session actually edited/ran),
    # falling back to a mention only when the session took no feature-scoped
    # action. This is immune to the injected MAIN.md/FEATURES.md context that
    # names features the session never touched (the "everything is tokto" bug).
    action_feature = _dominant_feature(action_features)
    feature = action_feature or _dominant_feature(main["features"])
    # Stage uses main.jsonl signals only (subagents inherit the parent's stage).
    stage = classify_stage(main["loop_stage"], main["stage_plan"],
                           main["stage_work"])
    return {
        "session_id": os.path.basename(session_dir.rstrip(os.sep)),
        "turns": turns,
        "stage": stage,
        "main_turns": main["turns"],
        "subagent_turns": sub_turns,
        "start": start,
        "end": main["ts_last"],
        "date": (start[:10] if isinstance(start, str) and start else None),
        "feature": feature,
        "action_feature": action_feature,
        "duration_seconds": round(duration, 3),
        "tokens": usage,
        "total_tokens": total_tokens,
        "peak_input_tokens": peak_input,
        "subagent_tokens": sub_usage,
        "models": {m: dict(u) for m, u in models.items()},
        "cache_read_ratio": sessionusage._cache_read_ratio(usage),
        "tools": tools,
        "tool_calls": tool_calls,
        "subagent_count": len(subagent_files),
        "malformed_lines": main["malformed"],
        # --- identity signals (authoritative, action-based) -------------------
        "plan_authored": sorted(plan_authored.keys()),
        "code_edits": code_edits,
        "planner_spawned": planner_spawned,
        "executor_spawned": executor_spawned,
        "promise_emitted": promise_emitted,
    }


def _session_feature_match(session_dir, feature):
    """True iff the session transcript references the feature (deterministic).

    A session 'belongs to' a feature when its main.jsonl mentions the feature
    work dir (work/<feature>) or the bare feature name. Pure substring scan over
    the raw transcript text — no parsing, fully deterministic. The text read is
    memoized (mtime/size-keyed) so scanning N features re-reads each transcript
    from disk ONCE, not once per feature.
    """
    main_path = os.path.join(session_dir, "main.jsonl")
    text = sessioncache._cached_text(main_path)
    if text is None:
        return False
    needles = (os.path.join("work", feature), feature)
    return any(n in text for n in needles)


def collect_sessions(kdir, session=None, feature=None, since=None):
    """Collect per-session metric rows, deterministically ordered.

    Filters: --session (exact id), --feature (transcript references feature),
    --since (session start date >= since, YYYY-MM-DD). Ordering is by start
    timestamp then session_id (rows lacking a start sort last) so output is
    byte-stable across runs.
    """
    root = logs_sessions_dir(kdir)
    rows = []
    if not os.path.isdir(root):
        return rows
    for name in sorted(os.listdir(root)):
        sdir = os.path.join(root, name)
        if not os.path.isdir(sdir):
            continue
        if session is not None and name != session:
            continue
        if feature is not None and not _session_feature_match(sdir, feature):
            continue
        row = parse_session(sdir)
        if row is None:
            continue
        if since is not None:
            start = row.get("start") or ""
            # Compare the YYYY-MM-DD date prefix lexically (ISO sorts correctly).
            if start[:10] < since:
                continue
        rows.append(row)
    rows.sort(key=lambda r: (r.get("start") is None, r.get("start") or "",
                             r.get("session_id") or ""))
    return rows


# Explicit facade export list (public AND underscore-private) — the entrypoint
# binds these onto its own namespace via the re-export loop so external consumers
# (tests/_fixtures.load_cli, the dashboard sidecar) keep reaching them as CLI.<name>.
_EXPORTS = [
    'LOGS_SESSIONS_REL',
    'classify_stage',
    '_dominant_feature',
    'logs_sessions_dir',
    'parse_session',
    '_parse_session_uncached',
    '_session_feature_match',
    'collect_sessions',
]
