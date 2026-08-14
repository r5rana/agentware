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

# shellcheck source=common.sh
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
RENDER="$AW_HOOK_DIR/render-transcript.py"

KDIR="$(aw_knowledge_dir)"
LOG_DIR="$(aw_log_dir)"

input="$(cat)"
sid="$(aw_json_field "$input" .session_id unknown)"
tpath="$(aw_json_field "$input" .transcript_path)"
cwd="$(aw_json_field "$input" .cwd)"

ts="$(aw_ts)"

# Layer-2 steering-capture producer (feature 260712-steering-capture, Task 5):
# a CHEAP regex scan of this session's NEW origin=human turns in prompts.log,
# appending inert candidates to logs/steering/prefer-queue.jsonl for the OFFLINE
# dream drain to classify. Watermark-idempotent; best-effort so it can NEVER
# fail the Stop hook (no stdout, errors swallowed). Skipped pre-onboarding
# (no external KB).
if [[ -n "$KDIR" && "$sid" != "unknown" ]]; then
  "$AW_REPO_ROOT/scripts/agentware" prefer scan-queue \
    --sid "$sid" --cwd "$cwd" >/dev/null 2>&1 || true
fi

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
  # Recurse so NESTED workflow subagents are captured, not only direct children
  # (Task 7c, 260710-learning-repair-p1). Preserve the nested path so distinct
  # agents never collide on basename.
  while IFS= read -r sj; do
    [[ -e "$sj" ]] || continue
    rel="${sj#"$SUB_SRC"/}"; rel="${rel%.jsonl}"
    dst="$SESS/subagents/$rel"
    mkdir -p "$(dirname "$dst")" 2>/dev/null || true
    cp -f "$sj" "$dst.jsonl" 2>/dev/null || true
    render "$dst.jsonl" "$dst.md"
    sub_count=$((sub_count + 1))
  done < <(find "$SUB_SRC" -type f -name '*.jsonl' 2>/dev/null)
fi

# Assemble full.md: the main transcript with every subagent appended at the end.
{
  cat "$SESS/main.md" 2>/dev/null
  if [[ "$sub_count" -gt 0 ]]; then
    printf '\n\n---\n\n# Spawned subagents (%d)\n' "$sub_count"
    while IFS= read -r m; do
      [[ -e "$m" ]] || continue
      rel="${m#"$SESS"/subagents/}"; rel="${rel%.md}"
      printf '\n---\n\n## ⤷ Subagent: %s\n\n' "$rel"
      cat "$m" 2>/dev/null
    done < <(find "$SESS/subagents" -type f -name '*.md' 2>/dev/null | sort)
  fi
} > "$SESS/full.md" 2>/dev/null || true

printf '[%s] [stop] session %s -> sessions/%s/ (main + %d subagent(s); see full.md)\n' \
  "$ts" "$sid" "$sid" "$sub_count" >> "$LOG_DIR/activity.log"
exit 0
