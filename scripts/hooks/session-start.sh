#!/bin/bash
# SessionStart hook — inject the agentware status + external MAIN.md into context.
#
# Reads the hook JSON on stdin
# (unused) and emits a SessionStart hookSpecificOutput with `additionalContext`
# that Claude Code adds to the model's context before the first prompt.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cat >/dev/null 2>&1 || true   # consume stdin

KDIR="$("$REPO_ROOT/scripts/aw-knowledge-dir" 2>/dev/null || true)"

if [[ -z "$KDIR" ]] || [[ ! -f "$KDIR/.initialized" ]]; then
  CTX="AGENTWARE_STATUS: FIRST_RUN — this workspace is not yet initialized. Before any other work, run the onboarding skill in .claude/skills/onboarding/SKILL.md: it asks where to store your EXTERNAL knowledge base, runs 'scripts/agentware init', and writes the .initialized sentinel."
else
  CTX="AGENTWARE_STATUS: initialized (knowledge dir: $KDIR)"
  if [[ -f "$KDIR/MAIN.md" ]]; then
    CTX="$CTX
----- knowledge/MAIN.md (operator profile + active work) -----
$(cat "$KDIR/MAIN.md")"
  fi
  # Inject the operator skills roster AFTER MAIN.md, but only when it actually
  # lists entries. A fresh/placeholder roster (e.g. "_No entries yet._") has no
  # list items, so it is omitted to avoid noise. A list item is any line whose
  # first non-space char is a bullet ("-" or "*"). For non-Claude harnesses the
  # equivalent is documented in AGENTS.md (the harness reads it natively).
  if [[ -f "$KDIR/skills/index.md" ]] && grep -Eq '^[[:space:]]*[-*][[:space:]]+' "$KDIR/skills/index.md"; then
    CTX="$CTX
----- knowledge/skills/index.md (operator skills roster) -----
$(cat "$KDIR/skills/index.md")"
  fi
fi

if command -v jq >/dev/null 2>&1; then
  jq -n --arg ctx "$CTX" \
    '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
else
  # Fallback: plain text on stdout is still surfaced by Claude Code.
  printf '%s\n' "$CTX"
fi
exit 0
