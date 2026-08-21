"""agentware_lib.ops.looptrace -- transcript-step trace + main-phase tool-use token shares.

Owns: the deterministic, read-only per-session trace derivation —
  * raw-IO trimming (_trace_io);
  * main-phase tool-use token shares, cached (_main_tooluse_token_shares +
    _main_tooluse_token_shares_uncached);
  * per-session step extraction, cached (_trace_steps_for_session +
    _trace_steps_for_session_uncached);
  * session-dir discovery + per-iteration windowing (_trace_session_dirs /
    _trace_iteration_windows / _iteration_for_ts);
  * the trace report (derive_trace).

Does NOT own: loop feature discovery / phase normalization (ops.loopanalytics --
imported DOWN as _loopanalytics for _normalize_phase); the transcript PARSE + read
cache (ops.sessionparse); the loop-emission channel reader (ops.metrics
_read_metrics_jsonl); loop-health/alerts/failure-ladder (later map modules).

Layer deps: ops.looptrace -> ops.loopanalytics (_loopanalytics) + ops.sessionparse
(_sessionparse) + ops.metrics (_metrics) -- same-layer, downward in the acyclic ops
DAG (nothing imports looptrace). No knowledge/features/cli import.

Determinism: read-only (INV-2). parse_session returns a deep copy, so trace never
mutates a canonical cached row; the two _uncached parsers are wrapped by the
single-instance read cache via _sessionparse (one LRU).

_EXPORTS is imported BY NAME onto the scripts/agentware facade so resident callers
(derive_trace's consumers, cmd_metrics/cmd_dashboard, the dashboard) resolve them as
bare names and load_cli tests reach them as CLI.<name>. ops.loopanalytics/
ops.sessionparse/ops.metrics are bound as _loopanalytics/_sessionparse/_metrics on
the facade so the facade-held trace functions resolve <alias>.<fn> there. None of the
9 is monkeypatched by any test (grep-verified).
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import copy
import json
import os

import agentware_lib.ops.loopanalytics as _loopanalytics
import agentware_lib.ops.metrics as _metrics
import agentware_lib.ops.sessionparse as _sessionparse


def _trace_io(raw, limit=2000):
    """Normalize a tool I/O field to a bounded STRING for the trace payload.

    The live.jsonl hook stores tool_input/tool_response as JSON STRINGS (already
    truncated to ~1500 chars). Re-serialize parseable JSON deterministically
    (sorted keys) and bound length, returning (text, truncated_bool). Strings
    keep the UI contract simple — the step detail shows truncated tool I/O."""
    if raw is None:
        return "", False
    if isinstance(raw, str):
        try:
            s = json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True)
        except ValueError:
            s = raw
    else:
        s = json.dumps(raw, ensure_ascii=False, sort_keys=True)
    if len(s) > limit:
        return s[:limit] + " …[truncated]", True
    return s, False


def _main_tooluse_token_shares(main_path):
    """Memoized wrapper (mtime/size-keyed) over the per-step token attribution —
    read once per request instead of once per feature's trace derivation."""
    return _sessionparse._cached_by_key(("tooluse_shares", main_path), _sessionparse._file_key(main_path),
                          lambda: _main_tooluse_token_shares_uncached(main_path))


def _iter_main_jsonl_objects(path):
    """Yield each JSON OBJECT on a transcript's lines, in file order.

    The line-level half of the main.jsonl read: open, skip blanks, decode, drop
    anything that is not a mapping. Every one of those is a DEGRADATION rule
    rather than a parse detail — a transcript is written by a live process and
    can be truncated mid-line at any moment — so the reader is tolerant of
    CONTENT and returns nothing at all when the path cannot be opened.

    Read-only and lazy: it never materialises the file, which is what lets a
    6 MB transcript be walked twice (shares, then steps) without being held."""
    try:
        f = open(path, "r", encoding="utf-8", errors="replace")
    except (FileNotFoundError, IsADirectoryError, OSError):
        return
    with f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


def _iter_main_assistant_tooluse(main_path):
    """Yield `(ts, message, tool_use_items)` per main.jsonl ASSISTANT turn that
    emitted at least one tool_use block.

    This is the ONE main.jsonl decode in this module. It existed twice — once
    for the token shares and once for the step fallback — with the same guards in
    the same order, which is how the two came to disagree about nothing yet had
    to be read twice to know that. A turn with no tool_use item is not yielded:
    both callers already discarded it (the shares path by `if not names`, the
    step path by iterating an empty list), so skipping it here is the SAME
    behaviour written once.

    `ts` is normalised to a string or None because a transcript may carry a
    non-string timestamp; `message` is handed back raw because only the shares
    caller reads its usage block."""
    for obj in _iter_main_jsonl_objects(main_path):
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        items = [it for it in content
                 if isinstance(it, dict) and it.get("type") == "tool_use"]
        if not items:
            continue
        ts = obj.get("timestamp")
        yield (ts if isinstance(ts, str) else None), msg, items


def _main_tooluse_token_shares_uncached(main_path):
    """Ordered (tool_name, token_share) for each main-agent tool_use in main.jsonl.

    A turn's output_tokens are split EVENLY across the tool_use items it emitted
    (best-effort per-step token attribution). Read-only; [] when the transcript
    is absent/unreadable/malformed."""
    shares = []
    for _ts, msg, items in _iter_main_assistant_tooluse(main_path):
        names = [it.get("name") or "<unknown>" for it in items]
        usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
        out = usage.get("output_tokens")
        out = out if isinstance(out, int) and not isinstance(out, bool) else 0
        share = round(out / len(names))
        for nm in names:
            shares.append((nm, share))
    return shares


def _trace_steps_for_session(sdir):
    """Memoized wrapper (keyed on this session's live.jsonl+main.jsonl identity)
    over the step build. derive_trace MUTATES returned steps (sets iteration), so
    return a DEEP COPY to keep the cached canonical steps pristine across the
    per-feature trace calls that loop-health/alerts make repeatedly."""
    keyfiles = (os.path.join(sdir, "live.jsonl"), os.path.join(sdir, "main.jsonl"))
    steps = _sessionparse._cached_by_key(("trace_steps", os.path.abspath(sdir)),
                           _sessionparse._multi_file_key(keyfiles),
                           lambda: _trace_steps_for_session_uncached(sdir))
    return copy.deepcopy(steps)


def _read_live_rows(live_path):
    """Every JSON object on a session's live.jsonl, in stream order ([] when the
    file is absent or unreadable).

    Tolerant of CONTENT (a blank line, a half-written line, a non-mapping) and
    strict about ACCESS: an OSError anywhere in the walk discards the partial
    read entirely rather than handing back a prefix, because a prefix of a
    per-action stream is indistinguishable from a session that stopped early —
    and the caller uses emptiness to decide whether to fall back to main.jsonl."""
    rows = []
    try:
        with open(live_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError:
        return []
    return rows


def _steps_from_live_rows(rows):
    """Steps from the PRIMARY source: the live.jsonl per-action stream, which is
    the only one carrying a result and a status per call. A row missing either is
    given the neutral value ("<unknown>" tool, "ok" status) rather than dropped,
    so the step count always equals the action count."""
    steps = []
    for obj in rows:
        args, at = _trace_io(obj.get("input"))
        res, rt = _trace_io(obj.get("response"))
        steps.append({
            "ts": obj.get("ts") if isinstance(obj.get("ts"), str) else None,
            "tool": obj.get("tool") or "<unknown>",
            "status": obj.get("status") or "ok",
            "args": args, "args_truncated": at,
            "result": res, "result_truncated": rt,
        })
    return steps


def _steps_from_main_transcript(main_path):
    """Steps from the FALLBACK source: main.jsonl tool_use items, used when a
    session has no live stream at all (a run that predates the hook, or one whose
    stream was lost). The transcript records what was CALLED and never what came
    back, so every step here is status "ok" with an empty result — the honest
    shape of "we know this happened and nothing about how it went"."""
    steps = []
    for ts, _msg, items in _iter_main_assistant_tooluse(main_path):
        for it in items:
            args, at = _trace_io(it.get("input"))
            steps.append({
                "ts": ts, "tool": it.get("name") or "<unknown>",
                "status": "ok", "args": args,
                "args_truncated": at, "result": "",
                "result_truncated": False,
            })
    return steps


def _attach_step_tokens(steps, shares):
    """Stamp each step's `tokens` from the ordered main-agent token shares.

    Alignment is by INDEX and confirmed by NAME: share i is credited to step i
    only when the two agree on the tool. They can disagree — the live stream and
    the transcript are two different recordings of the same run and either can
    miss an entry — and a mismatch means the sequences have drifted, so the
    honest answer is None (unknown) rather than a plausible number from the wrong
    call. Mutates in place; returns nothing."""
    for i, st in enumerate(steps):
        st["tokens"] = (shares[i][1]
                        if i < len(shares) and shares[i][0] == st["tool"]
                        else None)


def _attach_step_durations(steps):
    """Stamp each step's `duration_s` as the NEXT step's ts minus this one's.

    A step's own end is never recorded, so the next step's start is the only
    available bound; the last step in a session therefore has no duration, and
    neither does one with no timestamp. A NEGATIVE delta is discarded rather than
    reported: two sources with unsynchronised clocks can order two steps
    backwards, and a negative duration is a measurement error, not a fast call.
    Mutates in place; returns nothing."""
    for i, st in enumerate(steps):
        dur = None
        if st["ts"] is not None and i + 1 < len(steps):
            t0 = _sessionparse._parse_iso(st["ts"])
            t1 = _sessionparse._parse_iso(steps[i + 1]["ts"])
            if t0 is not None and t1 is not None:
                d = round((t1 - t0).total_seconds(), 3)
                dur = d if d >= 0 else None
        st["duration_s"] = dur


def _trace_steps_for_session_uncached(sdir):
    """Build a session's ordered tool-call steps (read-only).

    Five sequential stages, and the only DECISION left in this body is the third:
    live.jsonl is the primary source and main.jsonl the fallback, chosen on
    whether the live read produced anything at all. The token shares are read
    FIRST, before the live stream, because that read order is what the shared
    read cache sees and reordering it would reorder the cache."""
    live_path = os.path.join(sdir, "live.jsonl")
    main_path = os.path.join(sdir, "main.jsonl")
    shares = _main_tooluse_token_shares(main_path)
    live_rows = _read_live_rows(live_path)
    steps = (_steps_from_live_rows(live_rows) if live_rows
             else _steps_from_main_transcript(main_path))
    _attach_step_tokens(steps, shares)
    _attach_step_durations(steps)
    return steps


def _trace_session_dirs(kdir, session=None, feature=None):
    """Resolve the session dirs in scope (read-only): a single `session` id, or
    every session whose transcript references `feature`. Sorted by session id."""
    root = _sessionparse.logs_sessions_dir(kdir)
    out = []
    if not os.path.isdir(root):
        return out
    if session is not None:
        sdir = os.path.join(root, session)
        if os.path.isdir(sdir):
            out.append((session, sdir))
        return out
    for name in sorted(os.listdir(root)):
        sdir = os.path.join(root, name)
        if not os.path.isdir(sdir):
            continue
        if feature is not None and not _sessionparse._session_feature_match(sdir, feature):
            continue
        out.append((name, sdir))
    return out


def _trace_iteration_windows(events):
    """Sorted [(ts, iteration)] boundaries from the metrics.jsonl MAIN-phase
    events, used to bucket trace steps into loop iterations by timestamp."""
    wins = []
    for e in events:
        if e.get("event") in ("task_transition", "terminal"):
            continue
        if _loopanalytics._normalize_phase(e.get("phase") or e.get("stage")) != "main":
            continue
        it = e.get("iteration")
        ts = e.get("ts")
        if isinstance(it, int) and not isinstance(it, bool) \
                and isinstance(ts, str):
            wins.append((ts, it))
    wins.sort(key=lambda w: w[0])
    return wins


def _iteration_for_ts(windows, ts):
    """The iteration of the latest window starting at-or-before ts (None if the
    step predates the first window or has no timestamp)."""
    if not isinstance(ts, str):
        return None
    found = None
    for wts, it in windows:
        if wts <= ts:
            found = it
        else:
            break
    return found


def _resolve_group_feature(feature, session, sess_dirs):
    """The feature whose iteration windows and transitions group this trace.

    An explicit `feature` is authoritative. A SESSION-scoped trace has none, so
    it is recovered from the session's own parsed row — which is the only way a
    single-session trace can be bucketed into loop iterations at all. A session
    that cannot be parsed, or that belongs to no feature, leaves it unresolved
    and the trace degrades to one ungrouped bucket rather than failing."""
    if feature is not None or session is None or not sess_dirs:
        return feature
    row = _sessionparse.parse_session(sess_dirs[0][1])
    return row.get("feature") if row else feature


def _collect_session_steps(sess_dirs):
    """Every in-scope session's steps, stamped with the session they came from,
    plus the per-session step-count meta. Returns (steps, sessions_meta).

    The stamp is what makes a multi-session feature trace attributable: once the
    steps are merged and re-sorted by timestamp, a step's origin is otherwise
    unrecoverable — and the dup-check downstream scopes its signatures BY
    session precisely so a long-lived feature is not read as a dead loop."""
    steps = []
    sessions_meta = []
    for sid, sdir in sess_dirs:
        s_steps = _trace_steps_for_session(sdir)
        for st in s_steps:
            st["session_id"] = sid
        sessions_meta.append({"session_id": sid, "step_count": len(s_steps)})
        steps.extend(s_steps)
    return steps, sessions_meta


def _group_steps_by_iteration(steps, transitions):
    """Fold the ordered steps and the task transitions into per-iteration groups.

    One group per distinct iteration, created on FIRST reference by either
    stream, so an iteration that produced only a transition still appears and an
    iteration with only steps does not need the emission channel to exist. A
    transition whose iteration is not an int (including a `True`, which would
    otherwise fabricate iteration 1) is filed under the unknown bucket rather
    than dropped. Ordering is by iteration with the unknown bucket LAST."""
    groups = {}
    order = []

    def _group(itv):
        if itv not in groups:
            groups[itv] = {"iteration": itv, "steps": [], "transitions": []}
            order.append(itv)
        return groups[itv]

    for st in steps:
        _group(st["iteration"])["steps"].append(st)
    for tr in transitions:
        itv = tr.get("iteration")
        if not isinstance(itv, int) or isinstance(itv, bool):
            itv = None
        _group(itv)["transitions"].append({
            "ts": tr.get("ts"), "task": tr.get("task"),
            "from": tr.get("from"), "to": tr.get("to"),
            "approx": tr.get("approx"),
        })

    iterations = [groups[k] for k in
                  sorted(order, key=lambda k: (k is None,
                                               k if isinstance(k, int) else 0))]
    for g in iterations:
        g["step_count"] = len(g["steps"])
    return iterations


def derive_trace(kdir, session=None, feature=None, max_steps=4000,
                 sess_dirs=None, events=None):
    """Step-level run trace for the Trace/Run-explorer (pure, read-only, INV-2).

    Scope is a single `session` id or a `feature` (all sessions whose transcript
    references it). Returns an ORDERED tool-call step timeline grouped by loop
    iteration, plus per-iteration marker/decision transitions from metrics.jsonl.
    Empty/missing logs yield empty groups (forward-compatible, never crashes).
    Optional `sess_dirs`/`events` (shared single-pass index) skip the per-feature
    session re-scan + metrics re-read when the bulk caller already has them."""
    if sess_dirs is None:
        sess_dirs = _trace_session_dirs(kdir, session=session, feature=feature)
    grp_feature = _resolve_group_feature(feature, session, sess_dirs)
    if events is None:
        events = (_metrics._read_metrics_jsonl(kdir, feature=grp_feature)
                  if grp_feature else [])
    windows = _trace_iteration_windows(events)
    transitions = [e for e in events if e.get("event") == "task_transition"]

    steps, sessions_meta = _collect_session_steps(sess_dirs)
    # Deterministic global order: by ts (None last), then session id, then arrival.
    steps.sort(key=lambda s: (s.get("ts") is None, s.get("ts") or "",
                              s.get("session_id") or ""))
    truncated = False
    if len(steps) > max_steps:
        steps = steps[:max_steps]
        truncated = True

    tool_summary = {}
    for i, st in enumerate(steps):
        st["index"] = i
        st["iteration"] = _iteration_for_ts(windows, st.get("ts"))
        tool_summary[st["tool"]] = tool_summary.get(st["tool"], 0) + 1

    iterations = _group_steps_by_iteration(steps, transitions)
    err_count = sum(1 for s in steps if s.get("status") == "ERR")
    return {
        "scope": "session" if session is not None else "feature",
        "session": session,
        "feature": grp_feature,
        "sessions": sessions_meta,
        "step_count": len(steps),
        "err_count": err_count,
        "tool_summary": tool_summary,
        "truncated": truncated,
        "iterations": iterations,
    }


_EXPORTS = [
    '_trace_io',
    '_iter_main_jsonl_objects',
    '_iter_main_assistant_tooluse',
    '_main_tooluse_token_shares',
    '_main_tooluse_token_shares_uncached',
    '_read_live_rows',
    '_steps_from_live_rows',
    '_steps_from_main_transcript',
    '_attach_step_tokens',
    '_attach_step_durations',
    '_trace_steps_for_session',
    '_trace_steps_for_session_uncached',
    '_trace_session_dirs',
    '_trace_iteration_windows',
    '_iteration_for_ts',
    '_resolve_group_feature',
    '_collect_session_steps',
    '_group_steps_by_iteration',
    'derive_trace',
]
