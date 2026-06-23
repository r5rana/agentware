#!/bin/bash
# SubagentStop hook — capture spawned subagents' transcripts in real time, so a
# session's subagent work is saved even if the parent is interrupted before its
# Stop hook runs. The parent Stop hook re-derives and assembles these into
# full.md; this is the resilience copy. No stdout.
#
# Claude Code stores subagent transcripts at:
#   <project>/<parent-session-id>/subagents/agent-<id>.jsonl
# The SubagentStop payload's transcript_path may be EITHER that subagent file OR
# the parent's main transcript (<project>/<parent-session-id>.jsonl), depending on
# version — so we handle both and always file under sessions/<parent-sid>/subagents/.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RENDER="$SCRIPT_DIR/render-transcript.py"

KDIR="$("$REPO_ROOT/scripts/aw-knowledge-dir" 2>/dev/null || true)"
if [[ -n "$KDIR" ]]; then LOG_DIR="$KDIR/logs"; else LOG_DIR="$REPO_ROOT/.agentware-logs"; fi

input="$(cat)"
tpath=""
if command -v jq >/dev/null 2>&1; then
  tpath="$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null)"
fi
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
[[ -z "$tpath" || ! -f "$tpath" ]] && exit 0

copy_one() {  # copy_one <src.jsonl> <parent-sid>
  local src="$1" psid="$2" base dst
  base="$(basename "$src" .jsonl)"
  dst="$LOG_DIR/sessions/$psid/subagents"
  mkdir -p "$dst"
  cp -f "$src" "$dst/$base.jsonl" 2>/dev/null || true
  command -v python3 >/dev/null 2>&1 && python3 "$RENDER" "$dst/$base.jsonl" > "$dst/$base.md" 2>/dev/null || true
  printf '[%s] [subagent] %s -> sessions/%s/subagents/%s\n' "$ts" "$base" "$psid" "$base" \
    >> "$LOG_DIR/activity.log"
}

case "$tpath" in
  */subagents/*.jsonl)
    # Payload gave the subagent transcript directly.
    psid="$(basename "$(dirname "$(dirname "$tpath")")")"
    copy_one "$tpath" "$psid"
    ;;
  *)
    # Payload gave the parent's main transcript: sync every subagent under it.
    psid="$(basename "$tpath" .jsonl)"
    sub_src="${tpath%.jsonl}/subagents"
    if [[ -d "$sub_src" ]]; then
      for sj in "$sub_src"/*.jsonl; do
        [[ -e "$sj" ]] || continue
        copy_one "$sj" "$psid"
      done
    fi
    ;;
esac
exit 0
