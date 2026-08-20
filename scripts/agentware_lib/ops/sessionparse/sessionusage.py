"""agentware_lib.ops.sessionparse.sessionusage -- ONE-transcript JSONL aggregation + token/tool merges (layer: ops).

Owns: the deterministic aggregation of ONE raw JSONL transcript file into a metric
fold (aggregate_jsonl) and everything that fold is built from -- the token-usage key
tuples (_USAGE_KEYS/_INPUT_SIDE_KEYS), the transcript regexes (_FEATURE_RE/
_PLAN_PATH_RE/_PLAN_DOC_RE/_PROMISE_RE/_STAGE_LOOP_RE) and stage signal tuples
(_STAGE_PLAN_SIGNALS/_STAGE_WORK_SIGNALS), the zero-usage constructor
(_empty_usage), the three fold helpers (_merge_usage/_merge_tools/_merge_models) and
the input-side cache-hit ratio (_cache_read_ratio).

Does NOT own: the (mtime_ns,size)-keyed LRU read cache, the file-identity keys and
the two time helpers (_parse_iso/_duration_seconds) ->
agentware_lib.ops.sessionparse.sessioncache; the per-SESSION parsers that fold a
main.jsonl plus its subagents into a row, the stage classifier and the sessions-dir
resolver -> agentware_lib.ops.sessionparse.sessionread; cross-session aggregation,
pricing and the derive_* reporting family -> agentware_lib.ops.metrics /
agentware_lib.ops.derive.
Layer deps: ops.sessionparse.sessionusage -> stdlib (json, re) + the SAME-PACKAGE
sink sibling agentware_lib.ops.sessionparse.sessioncache (via the `sessioncache`
module ALIAS, by attribute, so a monkeypatch of the owner propagates and the LRU
stays ONE instance). No core/knowledge/retrieval/features/cli import; sessioncache
does not import this module, so the package graph stays acyclic.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Determinism: read-only (INV-2). aggregate_jsonl is a pure function of the
transcript bytes -- it tolerates a missing file, malformed JSON, non-dict lines and
lines whose `type` is neither assistant nor user, so identical inputs produce
byte-identical outputs.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so resident callers
resolve these as bare names and load_cli tests + the dashboard reach them as
CLI.<name>.
"""
import json
import re

import agentware_lib.ops.sessionparse.sessioncache as sessioncache


_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


_INPUT_SIDE_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


_FEATURE_RE = re.compile(r"work/(\d{6}-[A-Za-z0-9][A-Za-z0-9_-]*)")


_PLAN_PATH_RE = re.compile(
    r"work/(\d{6}-[A-Za-z0-9][A-Za-z0-9_-]*)/plan\.md")


_PLAN_DOC_RE = re.compile(
    r"work/\d{6}-[A-Za-z0-9][A-Za-z0-9_-]*/(?:plan|design)\.md$")


_PROMISE_RE = re.compile(r"<promise>[^<]+</promise>")


_STAGE_LOOP_RE = re.compile(r"AGENTWARE_STAGE=(loop-(?:pre|main|post))")


_STAGE_PLAN_SIGNALS = ("/agentware-plan", "plan new", "plan add-task")


_STAGE_WORK_SIGNALS = ("plan set-status", "</promise>", "worklog scan",
                       "scripts/agentware learn")


def _cache_read_ratio(usage):
    """cache_read / total INPUT-side tokens (input + cache_creation + cache_read).

    Returns 0.0 when there is no input-side traffic (never divides by zero)."""
    total_in = (usage.get("input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0))
    if total_in <= 0:
        return 0.0
    return round(usage.get("cache_read_input_tokens", 0) / total_in, 6)


def _empty_usage():
    return {k: 0 for k in _USAGE_KEYS}


def _new_usage_agg():
    """The zero-valued aggregate ONE transcript folds into.

    A factory rather than a literal inside `aggregate_jsonl`, because the shape IS
    the contract this module publishes: fourteen keys with fourteen different
    meanings, six of which are annotated, and a reader who wants to know what a
    metric row can contain should not have to read a 150-line loop to find out.
    Every call returns a FRESH dict (the nested `usage`/`models`/`features` are
    rebuilt too), so two aggregations can never share a mutable sub-object.
    """
    return {
        "turns": 0,
        "usage": _empty_usage(),
        "peak_input_tokens": 0,  # max single-turn input-side total (context fill)
        "models": {},          # model_id -> usage dict (ALL usage attributed)
        "features": {},        # feature name -> MENTION count (work/<feature> anywhere)
        # ACTION attribution: features referenced inside a tool_use INPUT (a file
        # the agent edited, a command it ran). This is what the session actually
        # OPERATED on — immune to injected context (MAIN.md/FEATURES.md/recall
        # listings) that mention features the session never touched. The pillars
        # attribute by this, not by passive mentions.
        "action_features": {}, # feature name -> action count (tool inputs only)
        "plan_authored": {},   # feature -> True if a work/<f>/plan.md was written
        "code_edits": 0,       # Edit/Write to a NON-planning file = execution work
        "planner_spawned": False,  # a Task(subagent_type=agentware-planner) fired
        "executor_spawned": False, # a Task(subagent_type=agentware-execution) fired
        "promise_emitted": False,  # the agent emitted a <promise>…</promise> (done)
        "loop_stage": None,    # loop-{pre,main,post} from AGENTWARE_STAGE marker
        "stage_plan": 0,       # distinct plan-authoring signal lines
        "stage_work": 0,       # distinct execution signal lines
        "tools": {},
        "tool_calls": 0,
        "ts_first": None,
        "ts_last": None,
        "_first_dt": None,
        "_last_dt": None,
        "lines": 0,
        "malformed": 0,
    }


def _scan_line_markers(agg, raw):
    """Fold the markers read off the RAW line, before it is parsed as JSON.

    These four are deliberately pre-parse: a feature path, an AGENTWARE_STAGE
    marker or a stage signal is counted wherever it appears in the transcript
    (inside a nested string, a tool result, an injected reminder), and a line
    that fails to parse still carries them. The loop_stage is FIRST-WINS — a
    session's stage is set once, by the marker the loop exported at spawn, and a
    later mention of a different stage cannot relabel it.
    """
    for feat in _FEATURE_RE.findall(raw):
        agg["features"][feat] = agg["features"].get(feat, 0) + 1
    if agg["loop_stage"] is None:
        sm = _STAGE_LOOP_RE.search(raw)
        if sm:
            agg["loop_stage"] = sm.group(1)
    if any(sig in raw for sig in _STAGE_PLAN_SIGNALS):
        agg["stage_plan"] += 1
    if any(sig in raw for sig in _STAGE_WORK_SIGNALS):
        agg["stage_work"] += 1


def _track_timestamps(agg, obj):
    """Widen the [ts_first, ts_last] window with this record's timestamp.

    Keeps the RAW ISO string beside the parsed datetime: the datetime is what
    orders the window (string compare is wrong across offsets), the raw string is
    what every consumer displays. A record with no timestamp, or one that does not
    parse, widens nothing — the window covers the lines that HAVE a timestamp,
    which is why an unparseable line is not a gap in it.
    """
    dt = sessioncache._parse_iso(obj.get("timestamp"))
    if dt is None:
        return
    if agg["_first_dt"] is None or dt < agg["_first_dt"]:
        agg["_first_dt"] = dt
        agg["ts_first"] = obj.get("timestamp")
    if agg["_last_dt"] is None or dt > agg["_last_dt"]:
        agg["_last_dt"] = dt
        agg["ts_last"] = obj.get("timestamp")


def _usage_int(usage, key):
    """One token count, or None when the field is absent or not a real number.

    `bool` is excluded explicitly because it is an `int` in Python: a
    `"input_tokens": true` would otherwise silently add 1 to the operator's bill.
    A float is accepted and truncated, which is what the transcript occasionally
    carries; a numeric STRING is not, because coercing it would make a malformed
    transcript indistinguishable from a well-formed one.
    """
    v = usage.get(key)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    return None


def _turn_input_total(usage):
    """This turn's INPUT-side token total = what was loaded into the context
    window on this turn. The MAX across turns is the peak fill, the signal behind
    context-window-overflow / silent truncation."""
    total = 0
    for k in _INPUT_SIDE_KEYS:
        v = _usage_int(usage, k)
        if v is not None:
            total += v
    return total


def _accumulate_turn_usage(agg, message):
    """Fold ONE assistant turn's `usage` block into the totals and the per-model
    split, and re-measure the peak context fill.

    ALL usage is attributed to a model, so a turn whose `model` is absent, empty
    or not a string is booked to the literal "unknown" rather than dropped — the
    per-model split must sum to the total, or a cost report silently under-counts.
    A message with no usage dict contributes nothing (it is still a turn).
    """
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return
    model = message.get("model")
    if not isinstance(model, str) or not model:
        model = "unknown"
    per_model = agg["models"].get(model)
    if per_model is None:
        per_model = _empty_usage()
        agg["models"][model] = per_model
    for k in _USAGE_KEYS:
        v = _usage_int(usage, k)
        if v is None:
            continue
        agg["usage"][k] += v
        per_model[k] += v
    turn_in = _turn_input_total(usage)
    if turn_in > agg["peak_input_tokens"]:
        agg["peak_input_tokens"] = turn_in


def _scan_edit_target(agg, name, inp):
    """Classify ONE write-family tool call by the file it targeted.

    A FULL Write of `work/<f>/plan.md` is plan AUTHORSHIP; an Edit of it is
    status/marker maintenance and counts as NEITHER authoring nor execution;
    writing or editing any NON-planning file is EXECUTION. That last line is what
    separates a planner (writes a plan and hands off) from a worker (edits code)
    — even when the same session did both, heavy code editing makes it a worker.
    A non-write tool that merely names a file (a Read) classifies as nothing.
    """
    fp = inp.get("file_path") if isinstance(inp, dict) else None
    if not isinstance(fp, str) or name not in (
            "Write", "Edit", "MultiEdit", "NotebookEdit"):
        return
    if not _PLAN_DOC_RE.search(fp):
        agg["code_edits"] += 1
        return
    if name != "Write":
        return
    pm = _PLAN_PATH_RE.search(fp)
    if pm:
        agg["plan_authored"][pm.group(1)] = True


def _scan_tool_use(agg, item):
    """Fold ONE tool_use block: the per-tool tally, the ACTION feature
    attribution, the edit classification and the subagent-spawn flags.

    ACTION attribution reads the tool INPUT only (an edited file path, a command
    operating on `work/<f>`), which is what makes it immune to the injected
    context that `_scan_line_markers`' passive MENTION count deliberately picks
    up. A tool with no name is tallied under "<unknown>" rather than dropped, so
    `tool_calls` and the tally can never disagree.
    """
    name = item.get("name") or "<unknown>"
    agg["tools"][name] = agg["tools"].get(name, 0) + 1
    agg["tool_calls"] += 1
    inp = item.get("input")
    inp_text = inp if isinstance(inp, str) else json.dumps(inp)
    for feat in _FEATURE_RE.findall(inp_text or ""):
        agg["action_features"][feat] = agg["action_features"].get(feat, 0) + 1
    _scan_edit_target(agg, name, inp)
    sub = inp.get("subagent_type") if isinstance(inp, dict) else None
    if sub == "agentware-planner":
        agg["planner_spawned"] = True
    elif sub == "agentware-execution":
        agg["executor_spawned"] = True


def _scan_message_content(agg, content):
    """Walk an assistant message's content blocks.

    Only two block types carry signal. A `text` block can carry the agent's OWN
    completion promise, which is the authoritative "this run finished" marker —
    and it is read ONLY here, from the agent's own output, never from a `thinking`
    block or a quoted user line. A `tool_use` block is the unit of action. Any
    other block type, and any non-dict item, is skipped.
    """
    if not isinstance(content, list):
        return
    for item in content:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "text":
            txt = item.get("text")
            if isinstance(txt, str) and _PROMISE_RE.search(txt):
                agg["promise_emitted"] = True
            continue
        if itype != "tool_use":
            continue
        _scan_tool_use(agg, item)


def aggregate_jsonl(path):
    """Aggregate ONE JSONL transcript file (read-only).

    Returns a dict with: turns (count of assistant lines), usage (summed token
    keys), tools (tool_use name -> count), tool_calls (total), ts_first/ts_last
    (raw ISO strings among lines that HAVE a timestamp), lines (parsed),
    malformed (count of unparseable lines). Tolerates missing files, malformed
    JSON, and lines whose `type` is not assistant/user.

    The body is the READ and the four folds it dispatches, in the order the
    transcript licenses them: the raw-line markers survive a parse failure, the
    timestamp window covers every well-formed record, and the usage + content
    folds are assistant-only. Each fold is its own function because each answers a
    different question about the same line, and a fifty-branch loop cannot say
    which of its branches belongs to which question.
    """
    agg = _new_usage_agg()
    try:
        f = open(path, "r", encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, OSError):
        return agg
    with f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            agg["lines"] += 1
            _scan_line_markers(agg, raw)
            try:
                obj = json.loads(raw)
            except (ValueError, TypeError):
                agg["malformed"] += 1
                continue
            if not isinstance(obj, dict):
                agg["malformed"] += 1
                continue
            _track_timestamps(agg, obj)
            if obj.get("type") != "assistant":
                continue
            agg["turns"] += 1
            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            _accumulate_turn_usage(agg, message)
            _scan_message_content(agg, message.get("content"))
    return agg


def _merge_usage(into, other):
    for k in _USAGE_KEYS:
        into[k] += other.get(k, 0)


def _merge_tools(into, other):
    for name, count in other.items():
        into[name] = into.get(name, 0) + count


def _merge_models(into, other):
    """Fold a per-model usage dict (model_id -> usage) into another."""
    for model, usage in other.items():
        dest = into.get(model)
        if dest is None:
            dest = _empty_usage()
            into[model] = dest
        _merge_usage(dest, usage)


# Explicit facade export list (public AND underscore-private) — the entrypoint
# binds these onto its own namespace via the re-export loop so external consumers
# (tests/_fixtures.load_cli, the dashboard sidecar) keep reaching them as CLI.<name>.
_EXPORTS = [
    '_USAGE_KEYS',
    '_INPUT_SIDE_KEYS',
    '_FEATURE_RE',
    '_PLAN_PATH_RE',
    '_PLAN_DOC_RE',
    '_PROMISE_RE',
    '_STAGE_LOOP_RE',
    '_STAGE_PLAN_SIGNALS',
    '_STAGE_WORK_SIGNALS',
    '_cache_read_ratio',
    '_empty_usage',
    '_new_usage_agg',
    '_scan_line_markers',
    '_track_timestamps',
    '_usage_int',
    '_turn_input_total',
    '_accumulate_turn_usage',
    '_scan_edit_target',
    '_scan_tool_use',
    '_scan_message_content',
    'aggregate_jsonl',
    '_merge_usage',
    '_merge_tools',
    '_merge_models',
]
