#!/bin/bash
# PostToolUse hook — log-tool.sh — LIVE, per-action streaming log.
#
# Fires AFTER every single tool call (PostToolUse), so progress streams to disk
# in real time and is `tail -f`-able DURING a turn — complementing (not
# replacing) the Stop-time lossless snapshot written by log-stop.sh. Writes:
#   logs/sessions/<sid>/live.jsonl   — one machine-readable record per tool call
#   logs/sessions/<sid>/live.md      — one human line per tool call
#   logs/activity.log                — one append-only one-liner per tool call
#   $AGENTWARE_LIVE_LOG (if set)     — run-scoped sink for agentware.sh's
#                                      terminal auto-stream (same human line)
# Falls back to a repo-local log dir only pre-onboarding. No stdout.
#
# PostToolUse stdin payload:
#   { session_id, transcript_path, cwd, hook_event_name:"PostToolUse",
#     tool_name, tool_input{...}, tool_response{success,...} }
#
# Invariants (see work/260624-live-action-logging/plan.md):
#   C-1 Silent & non-blocking: NO stdout (stdout would be injected into model
#       context); `exit 0` unconditionally so a logging failure NEVER blocks the
#       agent; stays well under the hook timeout.
#   C-2 Append-only & cheap: O(1) append per call; never rewrites prior records.
#   C-3 No new dependency: bash + jq (+ optional python3 elsewhere) — as today.
#   C-4 No new exposure surface: records the SAME data class the existing
#       main.jsonl snapshot already stores, TRUNCATED to MAXLEN chars for size.
#       Does not log MORE than the transcript already does.
# Security: secrets are NEVER echoed to stdout (C-1 keeps stdout empty); all
#   tool I/O is passed via jq --arg / argv arrays, never via echoed shell vars
#   (R-SEC-01); untrusted tool I/O is treated as data only, never executed
#   (R-SEC-02).

# shellcheck source=common.sh
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"

# C-3: jq is required for this hook — not for READING the payload (the shared
# ladder falls back to python3) but for BUILDING the live.jsonl record below,
# where `jq -nc --arg` is what keeps arbitrary tool I/O from breaking the JSON.
# No-op cleanly if it is absent, exactly as before.
command -v jq >/dev/null 2>&1 || exit 0

LOG_DIR="$(aw_log_dir)"

input="$(cat)"
sid="$(aw_json_field "$input" .session_id unknown)"
tool="$(aw_json_field "$input" .tool_name unknown)"
# NB: the RAW read, not the defaulted one — jq's `//` treats boolean false as
# empty, which would mask failures. Keeps "true" / "false" / "null" distinct.
success="$(aw_json_raw "$input" .tool_response.success)"
# Compact one-line JSON of the tool I/O (never emits embedded newlines).
input_c="$(aw_json_compact "$input" .tool_input '{}')"
resp_c="$(aw_json_compact "$input" .tool_response '{}')"

# Attribution (Task 7d, 260710-learning-repair-p1): which agent produced this
# call, so a subagent's tool calls never conflate into the parent session.
# Derived from transcript_path (subagent paths live under .../subagents/...;
# mirrors log-subagent.sh). Additive field — the live.jsonl reader ignores it.
tpath="$(aw_json_field "$input" .transcript_path)"
agent="main"
case "$tpath" in
  */subagents/*) agent="$(basename "${tpath%.jsonl}")" ;;
esac

ts="$(aw_ts)"

# C-4: bound large tool I/O. Mirrors render-transcript.py's _short(..., 1500)
# (truncate to MAXLEN chars + the " …[truncated]" marker for parity).
MAXLEN=1500
trunc() {  # HEAD-only truncation — for the INPUT (the miner derives the target
           # from the command head, and _transcript_hook_trunc keeps byte parity).
  local s="$1"
  if [[ ${#s} -gt $MAXLEN ]]; then
    printf '%s …[truncated]' "${s:0:MAXLEN}"
  else
    printf '%s' "$s"
  fi
}
trunc_tail() {  # tail-PRESERVING truncation — for the RESPONSE. A deep failure's
  # discriminating error line (a traceback's exception, an `Exit code` detail)
  # lives at the END; head-only trunc() would destroy it and collapse two
  # genuinely-distinct deep failures onto ONE miner cross-session F-E key
  # (adversarial-review). Mirrors scripts/agentware _transcript_resp_trunc
  # (head + marker + tail around the SAME " …[truncated]" marker).
  local s="$1" marker=' …[truncated]'
  if [[ ${#s} -le $MAXLEN ]]; then printf '%s' "$s"; return; fi
  local room=$(( MAXLEN - ${#marker} ))
  if (( room <= 1 )); then printf '%s …[truncated]' "${s:0:MAXLEN}"; return; fi
  local tail=$(( room / 2 )) head=$(( room - room / 2 ))
  printf '%s%s%s' "${s:0:head}" "$marker" "${s: -tail}"
}
input_t="$(trunc "$input_c")"
resp_t="$(trunc_tail "$resp_c")"

# Outcome: ERR only when the response explicitly reports success=false; absence
# of a success field (most tools) reads as ok.
status="ok"; [[ "$success" == "false" ]] && status="ERR"

SESS="$LOG_DIR/sessions/$sid"
mkdir -p "$SESS" 2>/dev/null || exit 0

# Machine-readable record (one JSON line). Truncated strings are stored as
# string fields so the line is always valid JSON regardless of tool I/O shape.
jq -nc \
  --arg ts "$ts" --arg tool "$tool" --arg status "$status" \
  --arg input "$input_t" --arg response "$resp_t" --arg agent "$agent" \
  '{ts:$ts, tool:$tool, status:$status, agent:$agent, input:$input, response:$response}' \
  >> "$SESS/live.jsonl" 2>/dev/null || true

# Human one-line view.
human="[$ts] 🔧 $tool $input_t → $status"
printf '%s\n' "$human" >> "$SESS/live.md" 2>/dev/null || true

# Append-only activity one-liner.
printf '[%s] [tool] %s %s %s\n' "$ts" "$sid" "$tool" "$status" \
  >> "$LOG_DIR/activity.log" 2>/dev/null || true

# Run-scoped sink for agentware.sh's terminal auto-stream (exported by
# agentware.sh, inherited by the spawned `claude -p` and thus this hook).
if [[ -n "${AGENTWARE_LIVE_LOG:-}" ]]; then
  printf '%s\n' "$human" >> "$AGENTWARE_LIVE_LOG" 2>/dev/null || true
fi

exit 0
