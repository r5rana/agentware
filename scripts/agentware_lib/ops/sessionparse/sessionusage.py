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


def aggregate_jsonl(path):
    """Aggregate ONE JSONL transcript file (read-only).

    Returns a dict with: turns (count of assistant lines), usage (summed token
    keys), tools (tool_use name -> count), tool_calls (total), ts_first/ts_last
    (raw ISO strings among lines that HAVE a timestamp), lines (parsed),
    malformed (count of unparseable lines). Tolerates missing files, malformed
    JSON, and lines whose `type` is not assistant/user.
    """
    agg = {
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
            try:
                obj = json.loads(raw)
            except (ValueError, TypeError):
                agg["malformed"] += 1
                continue
            if not isinstance(obj, dict):
                agg["malformed"] += 1
                continue

            dt = sessioncache._parse_iso(obj.get("timestamp"))
            if dt is not None:
                if agg["_first_dt"] is None or dt < agg["_first_dt"]:
                    agg["_first_dt"] = dt
                    agg["ts_first"] = obj.get("timestamp")
                if agg["_last_dt"] is None or dt > agg["_last_dt"]:
                    agg["_last_dt"] = dt
                    agg["ts_last"] = obj.get("timestamp")

            if obj.get("type") != "assistant":
                continue
            agg["turns"] += 1
            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if isinstance(usage, dict):
                model = message.get("model")
                if not isinstance(model, str) or not model:
                    model = "unknown"
                per_model = agg["models"].get(model)
                if per_model is None:
                    per_model = _empty_usage()
                    agg["models"][model] = per_model
                for k in _USAGE_KEYS:
                    v = usage.get(k)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        agg["usage"][k] += int(v)
                        per_model[k] += int(v)
                # Per-turn input-side total = what was loaded into the context
                # window on this turn; the MAX across turns is the peak fill, the
                # signal behind context-window-overflow / silent truncation.
                turn_in = 0
                for k in _INPUT_SIDE_KEYS:
                    v = usage.get(k)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        turn_in += int(v)
                if turn_in > agg["peak_input_tokens"]:
                    agg["peak_input_tokens"] = turn_in
            content = message.get("content")
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    itype = item.get("type")
                    if itype == "text":
                        # The agent emitting a completion promise (its OWN output)
                        # is the authoritative "this run finished" signal.
                        txt = item.get("text")
                        if isinstance(txt, str) and _PROMISE_RE.search(txt):
                            agg["promise_emitted"] = True
                        continue
                    if itype != "tool_use":
                        continue
                    name = item.get("name") or "<unknown>"
                    agg["tools"][name] = agg["tools"].get(name, 0) + 1
                    agg["tool_calls"] += 1
                    inp = item.get("input")
                    inp_text = inp if isinstance(inp, str) else json.dumps(inp)
                    # ACTION feature attribution: features named inside a tool
                    # INPUT (an edited file path, a command operating on work/<f>).
                    for feat in _FEATURE_RE.findall(inp_text or ""):
                        agg["action_features"][feat] = \
                            agg["action_features"].get(feat, 0) + 1
                    fp = inp.get("file_path") if isinstance(inp, dict) else None
                    if isinstance(fp, str) and name in (
                            "Write", "Edit", "MultiEdit", "NotebookEdit"):
                        if _PLAN_DOC_RE.search(fp):
                            # A planning artifact (plan.md / design.md). A FULL Write
                            # of plan.md is plan AUTHORSHIP; Edits are status/marker
                            # maintenance and do NOT count as either authoring or
                            # execution. Either way it is NOT a code edit.
                            if name == "Write":
                                pm = _PLAN_PATH_RE.search(fp)
                                if pm:
                                    agg["plan_authored"][pm.group(1)] = True
                        else:
                            # Editing/writing ANY non-planning file is EXECUTION.
                            # This is what separates a planner (writes a plan and
                            # hands off) from a worker (edits code) — even when the
                            # same session did both, heavy code editing makes it a
                            # worker, not a planner.
                            agg["code_edits"] += 1
                    sub = inp.get("subagent_type") if isinstance(inp, dict) \
                        else None
                    if sub == "agentware-planner":
                        agg["planner_spawned"] = True
                    elif sub == "agentware-execution":
                        agg["executor_spawned"] = True
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
    'aggregate_jsonl',
    '_merge_usage',
    '_merge_tools',
    '_merge_models',
]
