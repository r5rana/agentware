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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# C-3: jq is required for this hook; no-op cleanly if it is absent.
command -v jq >/dev/null 2>&1 || exit 0

KDIR="$("$REPO_ROOT/scripts/aw-knowledge-dir" 2>/dev/null || true)"
if [[ -n "$KDIR" ]]; then LOG_DIR="$KDIR/logs"; else LOG_DIR="$REPO_ROOT/.agentware-logs"; fi

input="$(cat)"
sid="$(printf '%s' "$input"  | jq -r '.session_id // "unknown"' 2>/dev/null)"
tool="$(printf '%s' "$input" | jq -r '.tool_name // "unknown"' 2>/dev/null)"
# NB: do NOT use `// empty` here — jq's `//` treats boolean false as empty, which
# would mask failures. Read the raw value ("true" / "false" / "null").
success="$(printf '%s' "$input" | jq -r '.tool_response.success' 2>/dev/null)"
# Compact one-line JSON of the tool I/O (jq -c never emits embedded newlines).
input_c="$(printf '%s' "$input"  | jq -c '.tool_input // {}'    2>/dev/null)"
resp_c="$(printf '%s'  "$input"  | jq -c '.tool_response // {}' 2>/dev/null)"
[[ -z "$sid" ]] && sid="unknown"
[[ -z "$tool" ]] && tool="unknown"

ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# C-4: bound large tool I/O. Mirrors render-transcript.py's _short(..., 1500)
# (truncate to MAXLEN chars + the " …[truncated]" marker for parity).
MAXLEN=1500
trunc() {  # trunc <string> -> truncated string on stdout
  local s="$1"
  if [[ ${#s} -gt $MAXLEN ]]; then
    printf '%s …[truncated]' "${s:0:MAXLEN}"
  else
    printf '%s' "$s"
  fi
}
input_t="$(trunc "$input_c")"
resp_t="$(trunc "$resp_c")"

# Outcome: ERR only when the response explicitly reports success=false; absence
# of a success field (most tools) reads as ok.
status="ok"; [[ "$success" == "false" ]] && status="ERR"

SESS="$LOG_DIR/sessions/$sid"
mkdir -p "$SESS" 2>/dev/null || exit 0

# Machine-readable record (one JSON line). Truncated strings are stored as
# string fields so the line is always valid JSON regardless of tool I/O shape.
jq -nc \
  --arg ts "$ts" --arg tool "$tool" --arg status "$status" \
  --arg input "$input_t" --arg response "$resp_t" \
  '{ts:$ts, tool:$tool, status:$status, input:$input, response:$response}' \
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
