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


def _main_tooluse_token_shares_uncached(main_path):
    """Ordered (tool_name, token_share) for each main-agent tool_use in main.jsonl.

    A turn's output_tokens are split EVENLY across the tool_use items it emitted
    (best-effort per-step token attribution). Read-only; [] when the transcript
    is absent/unreadable/malformed."""
    shares = []
    try:
        f = open(main_path, "r", encoding="utf-8", errors="replace")
    except (FileNotFoundError, IsADirectoryError, OSError):
        return shares
    with f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict) or obj.get("type") != "assistant":
                continue
            msg = obj.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            names = [it.get("name") or "<unknown>" for it in content
                     if isinstance(it, dict) and it.get("type") == "tool_use"]
            if not names:
                continue
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


def _trace_steps_for_session_uncached(sdir):
    """Build a session's ordered tool-call steps (read-only).

    Primary source: live.jsonl (the per-action stream carrying name/args/result/
    status/ts). Falls back to main.jsonl tool_use items when no live stream
    exists (no result/status then). Per-step duration = the next step's ts minus
    this ts (within the session); per-step tokens align best-effort to the
    main.jsonl turn output tokens by ordered name match."""
    live_path = os.path.join(sdir, "live.jsonl")
    main_path = os.path.join(sdir, "main.jsonl")
    shares = _main_tooluse_token_shares(main_path)
    steps = []
    live_rows = []
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
                    live_rows.append(obj)
    except OSError:
        live_rows = []
    if live_rows:
        for obj in live_rows:
            args, at = _trace_io(obj.get("input"))
            res, rt = _trace_io(obj.get("response"))
            steps.append({
                "ts": obj.get("ts") if isinstance(obj.get("ts"), str) else None,
                "tool": obj.get("tool") or "<unknown>",
                "status": obj.get("status") or "ok",
                "args": args, "args_truncated": at,
                "result": res, "result_truncated": rt,
            })
    else:
        # Fallback: main.jsonl tool_use items (no result/status streamed).
        try:
            f = open(main_path, "r", encoding="utf-8", errors="replace")
        except (FileNotFoundError, IsADirectoryError, OSError):
            f = None
        if f is not None:
            with f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(obj, dict) or \
                            obj.get("type") != "assistant":
                        continue
                    msg = obj.get("message")
                    ts = obj.get("timestamp")
                    ts = ts if isinstance(ts, str) else None
                    content = msg.get("content") if isinstance(msg, dict) else None
                    if not isinstance(content, list):
                        continue
                    for it in content:
                        if isinstance(it, dict) and it.get("type") == "tool_use":
                            args, at = _trace_io(it.get("input"))
                            steps.append({
                                "ts": ts, "tool": it.get("name") or "<unknown>",
                                "status": "ok", "args": args,
                                "args_truncated": at, "result": "",
                                "result_truncated": False,
                            })
    # Per-step tokens: best-effort index+name alignment with main-agent turns.
    for i, st in enumerate(steps):
        st["tokens"] = (shares[i][1]
                        if i < len(shares) and shares[i][0] == st["tool"]
                        else None)
    # Per-step duration within the session (next.ts - this.ts).
    for i, st in enumerate(steps):
        dur = None
        if st["ts"] is not None and i + 1 < len(steps):
            t0 = _sessionparse._parse_iso(st["ts"])
            t1 = _sessionparse._parse_iso(steps[i + 1]["ts"])
            if t0 is not None and t1 is not None:
                d = round((t1 - t0).total_seconds(), 3)
                dur = d if d >= 0 else None
        st["duration_s"] = dur
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
    # Resolve the feature used for iteration/transition grouping.
    grp_feature = feature
    if grp_feature is None and session is not None and sess_dirs:
        row = _sessionparse.parse_session(sess_dirs[0][1])
        if row:
            grp_feature = row.get("feature")
    if events is None:
        events = (_metrics._read_metrics_jsonl(kdir, feature=grp_feature)
                  if grp_feature else [])
    windows = _trace_iteration_windows(events)
    transitions = [e for e in events if e.get("event") == "task_transition"]

    steps = []
    sessions_meta = []
    for sid, sdir in sess_dirs:
        s_steps = _trace_steps_for_session(sdir)
        for st in s_steps:
            st["session_id"] = sid
        sessions_meta.append({"session_id": sid, "step_count": len(s_steps)})
        steps.extend(s_steps)
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
    '_main_tooluse_token_shares',
    '_main_tooluse_token_shares_uncached',
    '_trace_steps_for_session',
    '_trace_steps_for_session_uncached',
    '_trace_session_dirs',
    '_trace_iteration_windows',
    '_iteration_for_ts',
    'derive_trace',
]
