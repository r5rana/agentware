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

# shellcheck source=common.sh
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
RENDER="$AW_HOOK_DIR/render-transcript.py"

LOG_DIR="$(aw_log_dir)"

input="$(cat)"
tpath="$(aw_json_field "$input" .transcript_path)"
ts="$(aw_ts)"
[[ -z "$tpath" || ! -f "$tpath" ]] && exit 0

copy_one() {  # copy_one <src.jsonl> <parent-sid> [<rel-base-no-ext>]
  local src="$1" psid="$2" relbase="${3:-}" base dst
  [[ -n "$relbase" ]] && base="$relbase" || base="$(basename "$src" .jsonl)"
  dst="$LOG_DIR/sessions/$psid/subagents"
  mkdir -p "$(dirname "$dst/$base")" 2>/dev/null || true
  cp -f "$src" "$dst/$base.jsonl" 2>/dev/null || true
  command -v python3 >/dev/null 2>&1 && python3 "$RENDER" "$dst/$base.jsonl" > "$dst/$base.md" 2>/dev/null || true
  printf '[%s] [subagent] %s -> sessions/%s/subagents/%s\n' "$ts" "$base" "$psid" "$base" \
    >> "$LOG_DIR/activity.log"
}

case "$tpath" in
  */subagents/*.jsonl)
    # Subagent transcript directly (possibly nested): parent sid = component
    # before the FIRST /subagents/; preserve the nested path under it (Task 7c,
    # 260710-learning-repair-p1) so nested workflow agents never collide.
    psid="${tpath%%/subagents/*}"; psid="$(basename "$psid")"
    rel="${tpath#*/subagents/}"; rel="${rel%.jsonl}"
    copy_one "$tpath" "$psid" "$rel"
    ;;
  *)
    # Parent main transcript: sync every subagent RECURSIVELY (nested workflows).
    psid="$(basename "$tpath" .jsonl)"
    sub_src="${tpath%.jsonl}/subagents"
    if [[ -d "$sub_src" ]]; then
      while IFS= read -r sj; do
        [[ -e "$sj" ]] || continue
        rel="${sj#"$sub_src"/}"; rel="${rel%.jsonl}"
        copy_one "$sj" "$psid" "$rel"
      done < <(find "$sub_src" -type f -name '*.jsonl' 2>/dev/null)
    fi
    ;;
esac
exit 0
