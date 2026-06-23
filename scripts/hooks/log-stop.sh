#!/bin/bash
# Stop hook — at the end of every assistant turn, persist a COMPLETE record of
# the session (main agent + every subagent it spawned) into the EXTERNAL
# knowledge base:
#   logs/sessions/<sid>/main.jsonl        — lossless main transcript
#   logs/sessions/<sid>/main.md           — readable render
#   logs/sessions/<sid>/subagents/<a>.jsonl + .md  — one per spawned subagent
#   logs/sessions/<sid>/full.md           — main + every subagent appended
#   logs/activity.log                     — one append-only line per turn
# Falls back to a repo-local log dir only pre-onboarding. No stdout.
#
# Claude Code stores subagent transcripts next to the main one, at
# <project>/<sid>/subagents/agent-*.jsonl, so we derive them from transcript_path.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RENDER="$SCRIPT_DIR/render-transcript.py"

KDIR="$("$REPO_ROOT/scripts/aw-knowledge-dir" 2>/dev/null || true)"
if [[ -n "$KDIR" ]]; then LOG_DIR="$KDIR/logs"; else LOG_DIR="$REPO_ROOT/.agentware-logs"; fi

input="$(cat)"
sid="unknown"; tpath=""
if command -v jq >/dev/null 2>&1; then
  sid="$(printf '%s' "$input" | jq -r '.session_id // "unknown"' 2>/dev/null)"
  tpath="$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null)"
fi

ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

render() {  # render() <src.jsonl> <dst.md>  (best-effort; jsonl is source of truth)
  if command -v python3 >/dev/null 2>&1; then
    python3 "$RENDER" "$1" > "$2" 2>/dev/null || true
  fi
}

if [[ -z "$tpath" || ! -f "$tpath" ]]; then
  printf '[%s] [stop] session %s (no transcript path)\n' "$ts" "$sid" >> "$LOG_DIR/activity.log"
  exit 0
fi

SESS="$LOG_DIR/sessions/$sid"
mkdir -p "$SESS/subagents"

# Main transcript (append-only on disk, so the latest copy is complete).
cp -f "$tpath" "$SESS/main.jsonl" 2>/dev/null || true
render "$SESS/main.jsonl" "$SESS/main.md"

# Subagent transcripts: <project>/<sid>/subagents/agent-*.jsonl
SUB_SRC="${tpath%.jsonl}/subagents"
sub_count=0
if [[ -d "$SUB_SRC" ]]; then
  for sj in "$SUB_SRC"/*.jsonl; do
    [[ -e "$sj" ]] || continue
    base="$(basename "$sj" .jsonl)"
    cp -f "$sj" "$SESS/subagents/$base.jsonl" 2>/dev/null || true
    render "$SESS/subagents/$base.jsonl" "$SESS/subagents/$base.md"
    sub_count=$((sub_count + 1))
  done
fi

# Assemble full.md: the main transcript with every subagent appended at the end.
{
  cat "$SESS/main.md" 2>/dev/null
  if [[ "$sub_count" -gt 0 ]]; then
    printf '\n\n---\n\n# Spawned subagents (%d)\n' "$sub_count"
    for m in "$SESS"/subagents/*.md; do
      [[ -e "$m" ]] || continue
      printf '\n---\n\n## ⤷ Subagent: %s\n\n' "$(basename "$m" .md)"
      cat "$m" 2>/dev/null
    done
  fi
} > "$SESS/full.md" 2>/dev/null || true

printf '[%s] [stop] session %s -> sessions/%s/ (main + %d subagent(s); see full.md)\n' \
  "$ts" "$sid" "$sid" "$sub_count" >> "$LOG_DIR/activity.log"
exit 0
