#!/bin/bash
# retrieval-state.sh — PreToolUse hook enforcing the R-RET staged retrieval ladder.
#
# Tracks per-session retrieval state and warns on out-of-order tool calls.
# Ships WARN-ONLY (never blocks); honors AGENTWARE_DISABLE_RETRIEVAL_LINT.
#
# State machine: UNSTARTED -> RECALL_S1 -> RECALL_S2 -> QUERY -> WORK ->
#                GREP_WS -> CODE -> MCP -> WEB
#
# Detection: recall/query/grep are NOT distinct tools — recall/query are
# `scripts/agentware <sub>` invoked via Bash, workspace-grep is Bash grep/rg.
# The hook keys on tool_name + regex over tool_input.command.

set -e

# shellcheck source=common.sh
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"

# Kill-switch: disable entirely
if [[ -n "${AGENTWARE_DISABLE_RETRIEVAL_LINT:-}" ]]; then
  exit 0
fi

# Mode: warn (default) or block
MODE="${AGENTWARE_RETRIEVAL_LINT_MODE:-warn}"

# Graceful no-op when toolkit unavailable. ANCHORED to the repo root: the bare
# relative `scripts/agentware` this used to test resolved only when the agent's
# cwd happened to BE the checkout, so the ladder enforcement was silently off for
# every session started anywhere else — the same class of defect as the dead
# CLAUDE_TOOL_* read below, and invisible for the same reason (the hook exits 0).
if [[ ! -x "$AW_REPO_ROOT/scripts/agentware" ]]; then
  exit 0
fi

# Parse the tool event from the hook payload on STDIN (Claude Code passes JSON
# with session_id/tool_name/tool_input). The CLAUDE_TOOL_* env vars this hook
# used to read are NEVER set by Claude Code — the bug that left this enforcement
# dead (0 state files against 14k+ logged tool events), 260710-learning-repair-p1
# task 9. Mirrors scripts/hooks/log-tool.sh's stdin parse.
INPUT_JSON="$(cat 2>/dev/null || true)"
SID="$(aw_json_field "$INPUT_JSON" .session_id unknown)"
TOOL_NAME="$(aw_json_field "$INPUT_JSON" .tool_name)"
TOOL_INPUT="$(aw_json_compact "$INPUT_JSON" .tool_input '{}')"

# State file (PER-SESSION — the old single global current.state raced across
# concurrent sessions/subagents). Under logs/ if a knowledge dir is configured.
# Resolved through the shared helper: this hook used to call the RELATIVE
# `scripts/agentware config --knowledge-dir-only`, the one copy of the resolution
# that disagreed with the other five.
KDIR="$(aw_knowledge_dir)"
if [[ -z "$KDIR" ]]; then
  exit 0
fi
STATE_DIR="$KDIR/logs/.retrieval-state"
mkdir -p "$STATE_DIR" 2>/dev/null || true
# Sanitize the session id for use as a filename (defensive; ids are uuid-like).
SID_SAFE="$(printf '%s' "$SID" | tr -c 'A-Za-z0-9._-' '_')"
STATE_FILE="$STATE_DIR/$SID_SAFE.state"

# Read current state
CURRENT_STATE="UNSTARTED"
if [[ -f "$STATE_FILE" ]]; then
  CURRENT_STATE=$(cat "$STATE_FILE" 2>/dev/null || echo "UNSTARTED")
fi

# Classify the tool event into a retrieval stage
classify_event() {
  local name="$1"
  local input="$2"

  # Bash tool with agentware recall -> RECALL_S1 or RECALL_S2
  if [[ "$name" == "Bash" ]] && echo "$input" | grep -q "agentware recall"; then
    if echo "$input" | grep -q "\-\-top-k 10\|\-\-token-budget 3000"; then
      echo "RECALL_S2"
    else
      echo "RECALL_S1"
    fi
    return
  fi

  # Bash tool with agentware query -> QUERY
  if [[ "$name" == "Bash" ]] && echo "$input" | grep -q "agentware query"; then
    echo "QUERY"
    return
  fi

  # Bash tool with agentware recall --scope work -> WORK
  if [[ "$name" == "Bash" ]] && echo "$input" | grep -q "scope work"; then
    echo "WORK"
    return
  fi

  # Grep tool or Bash grep/rg over workspace -> GREP_WS
  if [[ "$name" == "Grep" ]]; then
    echo "GREP_WS"
    return
  fi
  # Any Bash grep/rg/ripgrep is a workspace grep (STAGE 3). recall/query/scope-work
  # are matched ABOVE and returned first, so reaching here means a genuine grep.
  # (The old inner workspace/src restriction made most greps invisible to the
  # ladder — the R-RET-06 targeted grep is still a grep regardless of path.)
  if [[ "$name" == "Bash" ]] && echo "$input" | grep -qE "grep|ripgrep|(^| )rg "; then
    echo "GREP_WS"
    return
  fi

  # Read tool -> CODE
  if [[ "$name" == "Read" ]]; then
    echo "CODE"
    return
  fi

  # WebFetch/WebSearch -> WEB
  if [[ "$name" == "WebFetch" ]] || [[ "$name" == "WebSearch" ]]; then
    echo "WEB"
    return
  fi

  # MCP tools -> MCP
  if echo "$name" | grep -q "^mcp__"; then
    echo "MCP"
    return
  fi

  echo "OTHER"
}

EVENT=$(classify_event "$TOOL_NAME" "$TOOL_INPUT")

# Skip non-retrieval events
if [[ "$EVENT" == "OTHER" ]]; then
  exit 0
fi

# State transition check
# Hard violations: GREP_WS or WEB while UNSTARTED (grep/web before ANY recall)
check_violation() {
  local state="$1"
  local event="$2"

  if [[ "$state" == "UNSTARTED" ]]; then
    case "$event" in
      GREP_WS|WEB)
        echo "WARN: $event before any recall (state=$state). Run recall first per R-RET-03."
        return 1
        ;;
    esac
  fi
  return 0
}

VIOLATION_MSG=$(check_violation "$CURRENT_STATE" "$EVENT" 2>&1) || true

if [[ -n "$VIOLATION_MSG" ]]; then
  if [[ "$MODE" == "warn" ]]; then
    echo "$VIOLATION_MSG" >&2
    # In warn mode, NEVER block — just warn
  fi
  # In block mode (future): would exit 1 here
fi

# Update state (advance to the new stage)
case "$EVENT" in
  RECALL_S1|RECALL_S2|QUERY|WORK|GREP_WS|CODE|MCP|WEB)
    echo "$EVENT" > "$STATE_FILE"
    ;;
esac

exit 0
