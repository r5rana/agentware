#!/bin/bash
# UserPromptSubmit hook — durably record EVERY user prompt so nothing is ever
# lost. Appends to the EXTERNAL knowledge base (logs/prompts.log) when one is
# configured, else falls back to a repo-local log (pre-onboarding only). Writes
# nothing to stdout, so no extra context is injected.

# shellcheck source=common.sh
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"

LOG_DIR="$(aw_log_dir)"
mkdir -p "$LOG_DIR"

input="$(cat)"
# Read through the shared ladder. The old jq-ONLY read left every field empty on
# a machine without jq, so this hook — whose entire job is that nothing is ever
# lost — appended an empty prompt under an empty session id, silently.
sid="$(aw_json_field "$input" .session_id unknown)"
prompt="$(aw_json_field "$input" .prompt)"
cwd="$(aw_json_field "$input" .cwd)"

ts="$(aw_ts)"

# Provenance origin (Task 7a, 260710-learning-repair-p1): loop (agentware.sh
# exported AGENTWARE_ORIGIN) / system (machine-injection heuristic) / human
# (default operator prompt). Additive: appended to the header, existing parsers
# that read the first three bracket groups are unaffected.
origin="human"
if [[ -n "${AGENTWARE_ORIGIN:-}" ]]; then
  origin="loop"
elif printf '%s' "$prompt" | grep -qiE '<task-notification>|<system-reminder>|<persona-preamble>|^You are (a subagent|Claude Code|the agentware)'; then
  origin="system"
fi

# Turn id (Task 7b): content hash joining this record to its rendered turn in
# sessions/<sid>/main.md (render-transcript.py computes the SAME
# sha1(text.strip())[:12]). Best-effort (needs python3).
turn=""
if command -v python3 >/dev/null 2>&1; then
  turn="$(printf '%s' "$prompt" | python3 -c 'import hashlib,sys; print(hashlib.sha1(sys.stdin.read().strip().encode("utf-8")).hexdigest()[:12])' 2>/dev/null)"
fi

{
  printf '[%s] [session %s] [cwd %s] [origin=%s] [turn=%s]\n' "$ts" "$sid" "$cwd" "$origin" "$turn"
  printf '%s\n\n' "$prompt"
} >> "$LOG_DIR/prompts.log"
exit 0
