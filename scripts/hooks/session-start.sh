#!/bin/bash
# SessionStart hook — inject the agentware status + external MAIN.md into context.
#
# Reads the hook JSON on stdin
# (unused) and emits a SessionStart hookSpecificOutput with `additionalContext`
# that Claude Code adds to the model's context before the first prompt.

# shellcheck source=common.sh
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"

_hook_input="$(cat 2>/dev/null || true)"   # SessionStart JSON (carries session_id)

KDIR="$(aw_knowledge_dir)"

# Session id (feature 260713, Task 6): used to SEED this session's prefer-queue
# watermark so the first Stop does not re-parse the whole cross-session
# prompts.log. Best-effort; empty when absent/unparseable. This hook's jq ->
# python3 ladder is now the ladder EVERY hook uses.
_sid="$(aw_json_field "$_hook_input" .session_id)"

# Resolve the invocation checkout ONCE (feature invocation-cwd-context): reused
# by BOTH the per-session preference digest (feature 260712, Task 3) and the
# AGENTWARE_INVOKED_FROM banner below, so the (~15s-budgeted) whereami resolver
# runs at most once. Byte-identical no-op when the var is unset or nothing
# resolves: _proj_name stays empty.
_proj_name=""; _proj_dir=""; _repo_root=""
if [[ -n "${AGENTWARE_INVOKED_FROM:-}" ]]; then
  _whereami_json="$("$AW_REPO_ROOT/scripts/agentware" whereami --dir "$AGENTWARE_INVOKED_FROM" --format json 2>/dev/null || true)"
  if [[ -n "$_whereami_json" ]]; then
    _proj_name="$(aw_json_field "$_whereami_json" .project_name)"
    if [[ -n "$_proj_name" ]]; then
      _proj_dir="$(aw_json_field "$_whereami_json" .project_dir)"
      _repo_root="$(aw_json_field "$_whereami_json" .repo_root)"
    fi
  fi
fi

if [[ -z "$KDIR" ]] || [[ ! -f "$KDIR/.initialized" ]]; then
  CTX="AGENTWARE_STATUS: FIRST_RUN — this workspace is not yet initialized. Before any other work, run the onboarding skill in .claude/skills/onboarding/SKILL.md: it asks where to store your EXTERNAL knowledge base, runs 'scripts/agentware init', and writes the .initialized sentinel."
else
  CTX="AGENTWARE_STATUS: initialized (knowledge dir: $KDIR)"
  # Non-blocking, read-only, NO-NETWORK package self-update surfacing (feature
  # 260712, Task 14). Prints only when a prior apply is stranded/unfinalized;
  # silent (suppressed) when up-to-date. Never blocks session start.
  _upd_line="$("$AW_REPO_ROOT/scripts/agentware" update --status-line 2>/dev/null || true)"
  if [[ -n "$_upd_line" ]]; then
    CTX="$CTX
$_upd_line"
  fi
  # Periodic self-exam surfacing (feature 260713-p5-checkup). Prints ONE line only
  # when checkup is ON AND a fresh, unacknowledged report exists; byte-identical
  # no-op (no output, no side-effect write) when OFF / no fresh report. Mirrors the
  # `update --status-line` read-only nudge above. Never blocks session start.
  _checkup_line="$("$AW_REPO_ROOT/scripts/agentware" checkup --status-line 2>/dev/null || true)"
  if [[ -n "$_checkup_line" ]]; then
    CTX="$CTX
$_checkup_line"
  fi
  if [[ -f "$KDIR/MAIN.md" ]]; then
    CTX="$CTX
----- knowledge/MAIN.md (operator profile + active work) -----
$(cat "$KDIR/MAIN.md")"
  fi
  # Per-user profile overlay: inject profiles/<handle>.md when the handle is set
  # AND the file exists. Power-user mode (handle unset / file absent) is a clean
  # no-op — byte-identical to the pre-overlay behavior.
  USER_HANDLE="$("$AW_REPO_ROOT/scripts/agentware" config --user-handle-only 2>/dev/null || true)"
  if [[ -n "$USER_HANDLE" ]] && [[ -f "$KDIR/profiles/${USER_HANDLE}.md" ]]; then
    CTX="$CTX
----- knowledge/profiles/${USER_HANDLE}.md (this operator's machine profile) -----
$(cat "$KDIR/profiles/${USER_HANDLE}.md")"
  fi
  # EXECUTOR identity guard (anti-impersonation): when a per-user handle is set,
  # state the executor authoritatively so the LLM never adopts another member's
  # identity from a plan's author field. Power-user mode: no banner, no change.
  if [[ -n "$USER_HANDLE" ]]; then
    CTX="$CTX
EXECUTOR: ${USER_HANDLE} — your environment/paths come from profiles/${USER_HANDLE}.md. Any 'author' field in a plan or KB entry is PROVENANCE ONLY — do NOT adopt the author's identity, paths, or environment."
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
  # Per-machine VETTED execution-cache skills roster (feature 260713-p4): the
  # installed + pending-approval skills from the machine-local lockfile. Fixed-
  # format, sanitized (counts + validated names only, NO author/raw metadata).
  # Silent (empty output) when the lockfile is empty/absent — byte-identical
  # no-op for power-users. Mirrors the `update --status-line` read-only nudge.
  _skill_line="$("$AW_REPO_ROOT/scripts/agentware" skill sync --status-line 2>/dev/null || true)"
  if [[ -n "$_skill_line" ]]; then
    CTX="$CTX
$_skill_line"
  fi
  # Active steering preferences (feature 260712, Task 3): render a compact,
  # capped, scope-filtered digest of the operator's `prefer`-captured settings so
  # a preference stated once is in force EVERY future session. Global prefs
  # always inject; project-scoped prefs inject only in their resolved checkout
  # (the canonical-slug normalizer maps $_proj_name -> KB slug); global-only when
  # there is no project signal (never leak another project's prefs). A DERIVED
  # artifact regenerated each session from the index — no MAIN.md bloat. Clean
  # no-op (empty output) when there are no active captures.
  _pref_digest="$("$AW_REPO_ROOT/scripts/agentware" prefer digest --project "$_proj_name" 2>/dev/null || true)"
  if [[ -n "$_pref_digest" ]]; then
    CTX="$CTX
$_pref_digest"
  fi
  # Seed this session's prefer-queue watermark to the CURRENT prompts.log size so
  # the first Stop scans only THIS session's turns, not the whole cross-session
  # log (feature 260713, Task 6). Idempotent + best-effort; stdout MUST stay the
  # hook payload, so all output is discarded.
  if [[ -n "$_sid" ]]; then
    "$AW_REPO_ROOT/scripts/agentware" prefer seed-cursor --sid "$_sid" \
      >/dev/null 2>&1 || true
  fi
fi

# Invocation-CWD context (feature invocation-cwd-context): when the operator
# launched via an alias that sets AGENTWARE_INVOKED_FROM, inject the enclosing
# project/repo (resolved once above) so agents know WHICH checkout the operator
# was working in. Byte-identical no-op when the var is unset or nothing resolves
# (_proj_name stays empty).
if [[ -n "${AGENTWARE_INVOKED_FROM:-}" ]] && [[ -n "$_proj_name" ]]; then
  CTX="$CTX
AGENTWARE_INVOKED_FROM: project_name=$_proj_name project_dir=$_proj_dir repo_root=$_repo_root invoked_from=$AGENTWARE_INVOKED_FROM"
fi

if command -v jq >/dev/null 2>&1; then
  jq -n --arg ctx "$CTX" \
    '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
else
  # Fallback: plain text on stdout is still surfaced by Claude Code.
  printf '%s\n' "$CTX"
fi
exit 0
