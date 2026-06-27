#!/usr/bin/env python3
"""codex-stream.py — reproduce the Claude `.claude/*` log sinks from a codex
`codex exec --json` JSONL event stream.

Codex fires NO `.claude/*` hooks, so the rich logging that `log-prompt.sh`,
`log-tool.sh`, and `log-stop.sh` produce for a Claude spawn is otherwise lost
under `AGENTWARE_CLI=codex`. `run_agent()`'s codex branch pipes
`codex exec --json …` through THIS renderer, which consumes the event stream on
stdin and writes the SAME sinks the Claude hooks write, per event:

  logs/prompts.log                       — the run's initial prompt (log-prompt.sh)
  logs/sessions/<sid>/live.jsonl         — one machine record per tool call (log-tool.sh)
  logs/sessions/<sid>/live.md            — one human line per tool call (log-tool.sh)
  logs/sessions/<sid>/main.jsonl         — lossless event transcript (log-stop.sh)
  logs/activity.log                      — one append-only one-liner per tool call
  $AGENTWARE_LIVE_LOG (if set)           — run-scoped sink for the terminal auto-stream

CRUCIAL invariants (work/260627-codex-runtime-adapter/plan.md, Task 6):
  (i)   the final assistant message MUST reach THIS process's stdout so the
        `<promise>…</promise>` grep in run_phase keeps working — every
        `agent_message` text is echoed to stdout; human action lines go to
        stderr (the live terminal VIEW).
  (ii)  sink writes are NEVER gated by `--no-stream` (that flag only disables the
        `tail -F` follower VIEW in agentware.sh, exactly as for claude) — this
        renderer always writes the durable sinks.
  (iii) a malformed/partial JSON line is SKIPPED, never aborts the run; every
        sink write is best-effort (logging is a VIEW, never a gate).

Large tool I/O is bounded to ~1500 chars (mirrors render-transcript.py `_short`
and log-tool.sh `trunc`). Stdlib only (`json`) — no new dependency.

codex `--json` event schema (codex-cli 0.142.x — thread/item events):
  {"type":"thread.started","thread_id":"…"}        — session id (=> <sid>)
  {"type":"turn.started"}
  {"type":"item.started","item":{id,type,…}}       — tool kicked off (no sink record)
  {"type":"item.completed","item":{id,type,…}}     — agent_message | command_execution | …
  {"type":"turn.completed","usage":{…}}
Item types: `agent_message` (assistant text), `reasoning` (thinking),
`command_execution` (shell), `file_change`/`patch`, `mcp_tool_call`,
`web_search`, … — anything that is NOT agent_message/reasoning is treated as a
tool call and gets a live.jsonl/live.md record.
"""

import argparse
import json
import os
import sys
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


def _append(path, data):
    """Best-effort append (logging is a VIEW, never a gate — never raises)."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(data)
    except Exception:
        pass


def _resolve_log_dir(arg_log_dir):
    if arg_log_dir:
        return arg_log_dir
    # Mirror the hooks' resolution: AGENTWARE_KNOWLEDGE_DIR / aw-knowledge-dir,
    # else a repo-local fallback (pre-onboarding).
    kdir = os.environ.get("AGENTWARE_KNOWLEDGE_DIR", "").strip()
    if not kdir:
        try:
            import subprocess
            here = os.path.dirname(os.path.abspath(__file__))
            repo = os.path.dirname(os.path.dirname(here))
            kdir = subprocess.run(
                [os.path.join(repo, "scripts", "aw-knowledge-dir")],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
        except Exception:
            kdir = ""
    if kdir:
        return os.path.join(kdir, "logs")
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))
    return os.path.join(repo, ".agentware-logs")


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
    return tool, _short(inp), _short(resp), status


def main(argv):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--log-dir", default=None)
    ap.add_argument("--feature", default="codex")
    ap.add_argument("--fallback-sid", default=None)
    ap.add_argument("--iteration", default="0")
    ap.add_argument("--prompt", default=None)
    ap.add_argument("--prompt-file", default=None)
    args = ap.parse_args(argv)

    log_dir = _resolve_log_dir(args.log_dir)
    fallback_sid = args.fallback_sid or "codex-%s-%d-%d" % (
        args.feature, os.getpid(), int(time.time()))

    cwd = os.getcwd()
    prompt_text = ""
    if args.prompt is not None:
        prompt_text = args.prompt
    elif args.prompt_file:
        try:
            with open(args.prompt_file, "r", encoding="utf-8") as fh:
                prompt_text = fh.read()
        except Exception:
            prompt_text = ""

    state = {"sid": None, "prompt_logged": False}
    live_log = os.environ.get("AGENTWARE_LIVE_LOG", "").strip()

    def sess_dir():
        sid = state["sid"] or fallback_sid
        state["sid"] = sid
        return os.path.join(log_dir, "sessions", sid)

    def log_prompt_once():
        if state["prompt_logged"]:
            return
        state["prompt_logged"] = True
        sid = state["sid"] or fallback_sid
        _append(os.path.join(log_dir, "prompts.log"),
                "[%s] [session %s] [cwd %s]\n%s\n\n" % (_now(), sid, cwd, prompt_text))

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        # (iii) malformed/partial line: skip, never abort.
        try:
            event = json.loads(raw)
        except Exception:
            continue
        if not isinstance(event, dict):
            continue

        etype = event.get("type")

        # Resolve the session id from codex's thread id (else the fallback).
        if etype == "thread.started" and event.get("thread_id"):
            state["sid"] = event["thread_id"]

        # Lossless transcript: every valid event line (sink resolves sid lazily).
        _append(os.path.join(sess_dir(), "main.jsonl"), raw + "\n")
        # The prompt is known up front; log it as soon as we have a session id.
        log_prompt_once()

        if etype == "item.started":
            item = event.get("item") or {}
            itype = item.get("type")
            if itype not in NON_TOOL_ITEMS:
                # Terminal progress only — NO sink record (1 record per tool call,
                # written on completion below).
                sys.stderr.write("[%s] ▶ %s …\n" % (_now(), itype or "tool"))
                sys.stderr.flush()
            continue

        if etype == "item.completed":
            item = event.get("item") or {}
            itype = item.get("type")
            if itype == "agent_message":
                text = item.get("text", "")
                # (i) the assistant message MUST reach stdout so the <promise> grep
                # in run_phase still works.
                sys.stdout.write(text + "\n")
                sys.stdout.flush()
                sys.stderr.write("[%s] 💬 %s\n" % (_now(), _short(text, 280)))
                sys.stderr.flush()
                continue
            if itype == "reasoning":
                txt = item.get("text", item.get("summary", ""))
                sys.stderr.write("[%s] 🧠 %s\n" % (_now(), _short(txt, 280)))
                sys.stderr.flush()
                continue

            # Any other completed item is a tool call → write the live sinks.
            tool, inp_t, resp_t, status = _tool_fields(item)
            ts = _now()
            sd = sess_dir()
            record = json.dumps(
                {"ts": ts, "tool": tool, "status": status,
                 "input": inp_t, "response": resp_t},
                ensure_ascii=False)
            _append(os.path.join(sd, "live.jsonl"), record + "\n")
            human = "[%s] 🔧 %s %s → %s" % (ts, tool, inp_t, status)
            _append(os.path.join(sd, "live.md"), human + "\n")
            _append(os.path.join(log_dir, "activity.log"),
                    "[%s] [tool] %s %s %s\n" % (ts, state["sid"] or fallback_sid, tool, status))
            if live_log:
                _append(live_log, human + "\n")
            sys.stderr.write(human + "\n")
            sys.stderr.flush()
            continue

        # turn.started / turn.completed / errors / unknown: kept in main.jsonl
        # (lossless) above; surface a terse note for the terminal.
        if etype == "turn.completed":
            usage = event.get("usage") or {}
            sys.stderr.write("[%s] ✓ turn complete %s\n" % (_now(), json.dumps(usage)))
            sys.stderr.flush()
        elif etype and etype.startswith("error"):
            sys.stderr.write("[%s] ✗ %s\n" % (_now(), _short(json.dumps(event), 280)))
            sys.stderr.flush()

    # Ensure prompts.log lands even for a degenerate (no-event) stream.
    log_prompt_once()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
