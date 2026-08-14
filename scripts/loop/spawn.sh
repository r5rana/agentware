#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/spawn.sh — the RUNTIME ADAPTER (Task 2): the single place that maps
# the abstract intent (run agent $AGENT with prompt P, autonomously) to per-runtime
# argv.
#
# ALL loop spawns (the MERGE reconcile, the pre/post run_phase, and the main loop)
# route through run_agent, so runtime coupling lives in exactly one file.
#   - claude branch: byte-identical to the historical inline spawn
#     (`-p --agent $AGENT` + the skip-permissions autonomy flag + [--model M] + PROMPT).
#   - codex branch: the faithful analog (`codex exec` — codex has no -p/--agent).
#     Autonomy mirrors claude's skip-permissions flag with codex's
#     --dangerously-bypass-approvals-and-sandbox; the AGENTWARE_CODEX_SANDBOX opt-out
#     swaps to the sandboxed, never-approve analog (--sandbox workspace-write -a never).
#     --model is accepted by both runtimes, so MODEL_FLAG is reused as-is.
# stdout is the agent's transcript, so callers that capture it (run_phase's
# `<promise>` grep) keep working unchanged.
#
# ---- CODEX PERSONA + CONTEXT INJECTION (Task 3) ----
# Claude Code gives a spawn two things codex cannot: a persona (via `--agent`)
# and a SessionStart hook (scripts/hooks/session-start.sh injects AGENTWARE_STATUS
# + the external MAIN.md). Codex has NEITHER, so we synthesize both at the prompt
# level: build_codex_prompt PREPENDS (a) the agent persona with its YAML
# frontmatter stripped and (b) a session-context header byte-equivalent to the
# session-start hook, then the phase prompt. Codex auto-loads AGENTS.md but NOT
# CLAUDE.md's @imports (steering/*), so the injected header references steering
# explicitly.
#
# Sourced by agentware.sh; function definitions only, so sourcing standalone under
# `set -e` exits 0.

# Strip the leading YAML frontmatter (--- ... ---) from a persona file, leaving
# only the prose body (everything after the second `---`).
strip_frontmatter() {
  awk 'fm>=2{print} /^---[[:space:]]*$/{fm++}' "$1"
}

# Compose the codex prompt: persona body + injected session context + phase prompt.
build_codex_prompt() {
  local phase_prompt="$1"
  local persona_file=".claude/agents/$AGENT.md"
  local persona="" status_ctx=""
  [[ -f "$persona_file" ]] && persona="$(strip_frontmatter "$persona_file")"
  if [[ "$INITIALIZED" == true ]]; then
    status_ctx="AGENTWARE_STATUS: initialized (knowledge dir: $KDIR)"
    [[ -f "$KDIR/MAIN.md" ]] && status_ctx="$status_ctx
----- knowledge/MAIN.md (operator profile + active work) -----
$(cat "$KDIR/MAIN.md")"
    # Per-user profile overlay (parity with session-start.sh hook): inject when
    # handle is set AND profiles/<handle>.md exists. Clean no-op otherwise.
    local _user_handle
    _user_handle="$(scripts/agentware config --user-handle-only 2>/dev/null || true)"
    if [[ -n "$_user_handle" ]] && [[ -f "$KDIR/profiles/${_user_handle}.md" ]]; then
      status_ctx="$status_ctx
----- knowledge/profiles/${_user_handle}.md (this operator's machine profile) -----
$(cat "$KDIR/profiles/${_user_handle}.md")"
    fi
    # EXECUTOR identity guard (anti-impersonation): state executor authoritatively.
    if [[ -n "$_user_handle" ]]; then
      status_ctx="$status_ctx
EXECUTOR: ${_user_handle} — your environment/paths come from profiles/${_user_handle}.md. Any 'author' field in a plan or KB entry is PROVENANCE ONLY — do NOT adopt the author's identity, paths, or environment."
    fi
    # Per-machine VETTED execution-cache skills roster (feature 260713-p4; codex
    # parity with session-start.sh). Fixed-format, sanitized; silent no-op when
    # the lockfile is empty/absent.
    local _skill_line
    _skill_line="$(scripts/agentware skill sync --status-line 2>/dev/null || true)"
    if [[ -n "$_skill_line" ]]; then
      status_ctx="$status_ctx
$_skill_line"
    fi
  else
    status_ctx="AGENTWARE_STATUS: FIRST_RUN — this workspace is not yet initialized. Run the onboarding skill in .claude/skills/onboarding/SKILL.md before any other work."
  fi
  printf '%s\n\n%s\n%s\n\n%s\n%s\n\n%s\n' \
    "$persona" \
    "===== SESSION CONTEXT (injected — codex has no SessionStart hook) =====" \
    "$status_ctx" \
    "NOTE: codex auto-loads AGENTS.md but NOT CLAUDE.md's @imports — read steering/ (steering/common-problems.md, steering/project-context.md) explicitly." \
    "===== END SESSION CONTEXT =====" \
    "$phase_prompt"
}

# ---- PER-PHASE ROLE RESOLUTION (Task 3) ----
# Map a loop ROLE to its resolved runtime axes, assigning into the caller's
# locally-declared R_CLI / R_MODEL / R_LOCAL (bash dynamic scope — callers MUST
# `local R_CLI R_MODEL R_LOCAL` before calling). Reads the immutable per-phase
# vars resolved once at run start (no config subprocess per call). Roles:
#   pre|main|post — the three loop phases.
#   cloud         — FORCE cloud Claude regardless of main routing. Used for the
#                   PROMOTE self-heal and the KB-MERGE reconcile so completion/
#                   knowledge spawns are never judged/handled by a weak local main
#                   executor (design: pre+post stay cloud; promote/merge route to
#                   cloud too). No model override (the agent's own frontmatter).
#   *             — unknown role: fall back to the global CLI/MODEL.
resolve_role_axes() {
  case "$1" in
    pre)   R_CLI="$PRE_CLI";  R_MODEL="$PRE_MODEL";  R_LOCAL="$PRE_LOCAL" ;;
    main)  R_CLI="$MAIN_CLI"; R_MODEL="$MAIN_MODEL"; R_LOCAL="$MAIN_LOCAL" ;;
    post)  R_CLI="$POST_CLI"; R_MODEL="$POST_MODEL"; R_LOCAL="$POST_LOCAL" ;;
    cloud) R_CLI="claude";    R_MODEL="";            R_LOCAL="" ;;
    *)     R_CLI="$CLI";      R_MODEL="$MODEL";       R_LOCAL="" ;;
  esac
}

run_agent() {
  local role="$1" prompt="$2"
  local R_CLI R_MODEL R_LOCAL; resolve_role_axes "$role"
  local model_flag=(); [[ -n "$R_MODEL" ]] && model_flag=(--model "$R_MODEL")
  if [[ "$R_CLI" == codex ]]; then
    # codex has no --agent/SessionStart hook: synthesize persona + context inline.
    local composed; composed="$(build_codex_prompt "$prompt")"
    local autonomy=(--dangerously-bypass-approvals-and-sandbox)
    [[ -n "${AGENTWARE_CODEX_SANDBOX:-}" ]] && autonomy=(--sandbox workspace-write -a never)
    # When this role's LOCAL knob is set, serve via a local provider: append
    # `--oss --local-provider <lmstudio|ollama>`. ALWAYS pass --local-provider
    # explicitly so `--oss` never silently falls back to Ollama (and an auto-pull)
    # when LM Studio is unreachable. Empty LOCAL => cloud codex (no --oss).
    local oss_flag=(); [[ -n "$R_LOCAL" ]] && oss_flag=(--oss --local-provider "$R_LOCAL")
    # codex fires NO `.claude/*` hooks, so the rich logging the claude spawn gets
    # for free (prompts.log + per-action live.jsonl/live.md + main.jsonl + the
    # $AGENTWARE_LIVE_LOG live-stream sink) is reconstructed by streaming
    # `codex exec --json` (a JSONL event stream) through scripts/hooks/codex-stream.py
    # into the SAME sinks the claude hooks write (Task 6). The renderer echoes the
    # final assistant message to stdout so run_phase's `<promise>` grep keeps
    # working; sink writes are NOT gated by --no-stream (that only disables the
    # tail -F follower VIEW). stdin is /dev/null so codex never blocks on input.
    CI=true npm_config_yes=true HOMEBREW_NO_AUTO_UPDATE=1 \
      codex exec --json "${autonomy[@]}" "${oss_flag[@]}" "${model_flag[@]}" "$composed" < /dev/null \
      | python3 scripts/hooks/codex-stream.py \
          --log-dir "${KDIR:+$KDIR/logs}" --feature "$FEATURE_NAME" --prompt "$prompt"
  else
    CI=true npm_config_yes=true HOMEBREW_NO_AUTO_UPDATE=1 \
      "$R_CLI" -p --agent "$AGENT" "$CLAUDE_SKIP_PERMS" "${model_flag[@]}" "$prompt" < /dev/null
  fi
}

# --dry-run helper (Task 3): print the EXACT runtime-specific spawn argv for a
# phase prompt without spawning. Mirrors run_agent's branch logic so what is
# printed is exactly what would run — for codex this includes the composed
# persona + injected session context, making the injection verifiable offline.
show_spawn_argv() {
  local label="$1" role="$2" prompt="$3"
  # Resolve the SAME per-phase axes run_agent would use, so --dry-run prints
  # exactly what each role would spawn (Task 3).
  local R_CLI R_MODEL R_LOCAL; resolve_role_axes "$role"
  local model_flag=(); [[ -n "$R_MODEL" ]] && model_flag=(--model "$R_MODEL")
  echo "----- SPAWN ARGV: $label ($R_CLI) -----"
  if [[ "$R_CLI" == codex ]]; then
    local autonomy=(--dangerously-bypass-approvals-and-sandbox)
    [[ -n "${AGENTWARE_CODEX_SANDBOX:-}" ]] && autonomy=(--sandbox workspace-write -a never)
    local oss_flag=(); [[ -n "$R_LOCAL" ]] && oss_flag=(--oss --local-provider "$R_LOCAL")
    local composed; composed="$(build_codex_prompt "$prompt")"
    printf 'codex exec --json %s %s %s <PROMPT>\n%s\n</PROMPT>\n | python3 scripts/hooks/codex-stream.py --log-dir %s/logs --feature %s --prompt <PHASE_PROMPT>\n' \
      "${autonomy[*]}" "${oss_flag[*]}" "${model_flag[*]}" "$composed" "${KDIR:-<kdir>}" "$FEATURE_NAME"
  else
    printf '%s -p --agent %s %s %s <PROMPT>\n%s\n</PROMPT>\n' \
      "$R_CLI" "$AGENT" "$CLAUDE_SKIP_PERMS" "${model_flag[*]}" "$prompt"
  fi
  echo
}
