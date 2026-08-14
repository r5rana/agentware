#!/usr/bin/env python3
"""codex_items.py — the codex event-ITEM vocabulary: bounding and field mapping.

Split out of `codex-stream.py` by plan task 35, which modularises
`scripts/hooks/`. The renderer was 304 lines against the 250-line hook ceiling —
declared as shrink-only debt by task 33 and owned by this task.

The split is along a real seam rather than at a line count. Two responsibilities
lived in one file:

  * WHAT a codex item MEANS — how an `item.completed` payload becomes the
    (tool, input, response, status) tuple every sink records, and how a large
    value is bounded. Pure, total, and dependent on nothing but the item dict.
  * WHERE it GOES — stdin/stdout/stderr, the log dir, the append-only sinks, the
    `<promise>` echo contract. All I/O and process state.

This module is the first. It imports nothing but `json` and `time`, touches no
file, and every function here is a total function of its arguments, so the codex
item vocabulary can be tested without a subprocess, a temp dir, or a stream.

`codex-stream.py` carries a hyphen and so is not importable; this sibling is
(the renderer runs as a script, which puts `scripts/hooks` on `sys.path[0]`).

EVERY DEFINITION BELOW IS BYTE-IDENTICAL TO THE ONE IT MOVED FROM, and must stay
that way. This is a MOVE, not a rewrite. The first attempt at this split also
factored the repeated `json.dumps`-or-`str(...)` coercion out of `_short`,
`_short_tail` and `_tool_fields` into a shared helper — a change that reads as
obvious tidying and is invisible in every behavioural test, because behaviour is
genuinely identical. The task-30 body differ caught it anyway: `_tool_fields`
fell from 1693 to 1322 chars against an adjudicated floor of 1693. That gate
exists because ~200 of the 243 divergent definitions in this migration differ by
one tree holding a THINNER body, and it cannot distinguish a thinner body that
lost a branch from one that merely hoisted a helper. So under this migration a
move is a pure move: shrink nothing in transit.

Stdlib only, no network — required of every `scripts/hooks/*.py` by the
security-invariant corpus policy `agent-hooks`.
"""

import json
import time

MAXLEN = 1500
# Item types that are NOT tool calls (so they do not get a live.jsonl record).
NON_TOOL_ITEMS = {"agent_message", "reasoning"}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _short(text, n=MAXLEN):
    """Bound large strings; mirrors render-transcript.py _short / log-tool trunc."""
    if not isinstance(text, str):
        try:
            text = json.dumps(text, ensure_ascii=False)
        except Exception:
            text = str(text)
    return text if len(text) <= n else text[:n] + " …[truncated]"


def _short_tail(text, n=MAXLEN):
    """Bound a tool RESPONSE while PRESERVING its TAIL. A deep failure's
    discriminating error line (a traceback's exception, an `Exit code` detail)
    lives at the END, where head-only _short() would destroy it and collapse two
    genuinely-distinct deep failures onto one miner cross-session F-E key
    (adversarial-review). Mirrors scripts/agentware _transcript_resp_trunc
    (head + marker + tail around the SAME " …[truncated]" marker)."""
    if not isinstance(text, str):
        try:
            text = json.dumps(text, ensure_ascii=False)
        except Exception:
            text = str(text)
    if len(text) <= n:
        return text
    marker = " …[truncated]"
    room = n - len(marker)
    if room <= 1:
        return text[:n] + marker
    tail = room // 2
    head = room - tail
    return text[:head] + marker + text[-tail:]


def _tool_fields(item):
    """Map a codex item to (tool, input_str, response_str, status) — generic so
    unknown future tool item types still get a sensible record."""
    itype = item.get("type", "unknown")
    status = "ok"
    if itype == "command_execution":
        tool = "command_execution"
        inp = item.get("command", "")
        resp = item.get("aggregated_output", "")
        exit_code = item.get("exit_code")
        st = item.get("status")
        if (exit_code not in (0, None)) or (st in ("failed", "error")):
            status = "ERR"
    elif itype == "mcp_tool_call":
        server = item.get("server", "")
        name = item.get("tool", item.get("name", ""))
        tool = "mcp:%s.%s" % (server, name) if server else "mcp:%s" % name
        inp = item.get("arguments", item.get("input", ""))
        resp = item.get("result", item.get("output", ""))
        if item.get("status") in ("failed", "error"):
            status = "ERR"
    elif itype == "file_change":
        tool = "file_change"
        inp = item.get("changes", item.get("path", item))
        resp = item.get("status", "")
        if item.get("status") in ("failed", "error"):
            status = "ERR"
    elif itype == "web_search":
        tool = "web_search"
        inp = item.get("query", "")
        resp = item.get("results", "")
    else:
        # Unknown tool item: store everything but the id/type as input.
        tool = itype
        inp = {k: v for k, v in item.items() if k not in ("id", "type")}
        resp = ""
        if item.get("status") in ("failed", "error"):
            status = "ERR"
    if not isinstance(inp, str):
        try:
            inp = json.dumps(inp, ensure_ascii=False)
        except Exception:
            inp = str(inp)
    if not isinstance(resp, str):
        try:
            resp = json.dumps(resp, ensure_ascii=False)
        except Exception:
            resp = str(resp)
    return tool, _short(inp), _short_tail(resp), status
