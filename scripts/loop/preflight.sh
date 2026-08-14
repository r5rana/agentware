#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/preflight.sh — the gates that run BEFORE a run is allowed to begin,
# plus the run-scoped state every later lib reads.
#
# Sourced at SOURCE time by agentware.sh after routing.sh (the PATH gate below
# reads the resolved per-phase CLIs) and before prompts.sh (which interpolates
# FEATURE_NAME / FEATURE_UPPER / STATE_DIR, all set here). Every failure path exits
# non-zero BEFORE LOOP_STARTED is armed, so a preflight rejection never emits a
# terminal outcome event. Ends with `:` — see the note in args.sh.
#
# Sets: INITIALIZED, LIVE_LOG, TAIL_PID, FEATURE_NAME, FEATURE_UPPER,
#       AGENTWARE_LIVE_LOG (exported).

# ---- PREFLIGHT GATES ----

# jq is always required; the CLI runtime is required only when we actually spawn.
if ! command -v jq >/dev/null 2>&1; then
  echo "Error: required dependency 'jq' not found on PATH."
  echo "Install jq (e.g. 'brew install jq') and re-run."
  exit 1
fi
if [[ "$DRY_RUN" != true ]]; then
  # Per-phase routing (Task 3) can resolve different runtimes per role (e.g. a
  # cloud pre/post + a local-via-codex main), so every DISTINCT resolved CLI must
  # be on PATH. With no per-phase overrides all three collapse to the single
  # global $CLI, so the default behaviour is unchanged.
  for _phase_cli in "$PRE_CLI" "$MAIN_CLI" "$POST_CLI"; do
    if ! command -v "$_phase_cli" >/dev/null 2>&1; then
      echo "Error: required agent runtime '$_phase_cli' not found on PATH."
      echo "Install it, set AGENTWARE_CLI=<your-cli>, or run with --dry-run."
      exit 1
    fi
  done
fi

# plan.md must exist and be non-empty before the main phase.
if [[ ! -s "$DOCS_DIR/plan.md" ]]; then
  echo "Error: plan file not found or empty: $DOCS_DIR/plan.md"
  exit 1
fi

# Initialized state (KDIR was resolved in args.sh). When the workspace is not yet
# initialized, KDIR-dependent gates are skipped and the agent's first-run gate
# runs onboarding before any task work.
INITIALIZED=false
if [[ -n "$KDIR" ]] && [[ -f "$KDIR/.initialized" ]]; then
  INITIALIZED=true
fi

# --validate — run the deterministic audit as a preflight gate.
if [[ "$VALIDATE" == true ]]; then
  if [[ "$INITIALIZED" != true ]]; then
    echo "[preflight] workspace not initialized — skipping 'audit' (onboarding runs first)."
  else
    echo "[preflight] Running 'scripts/agentware audit'..."
    if ! scripts/agentware audit; then
      echo "Error: preflight 'scripts/agentware audit' failed. Fix the reported issues first."
      exit 1
    fi
    echo "[preflight] audit passed."
  fi
fi

mkdir -p "$STATE_DIR"

# ---- LIVE ACTION STREAM (PostToolUse → this terminal) ----
# Per-run sink the PostToolUse hook (scripts/hooks/log-tool.sh) appends one human
# line to per tool call. Exported as AGENTWARE_LIVE_LOG so every spawned
# `claude -p` (pre, main, post) and its PostToolUse hook inherit it. A background
# `tail -F` follower then echoes those lines into THIS terminal as the agent works
# — so progress streams live instead of only landing at Stop-time. It is a VIEW,
# never a gate: a missing/empty sink, a killed tail, --no-stream, or --dry-run
# must never break the run. Truncate the sink at run start so a prior run's
# actions don't replay; export BEFORE run_pre_hooks/the phase loops so all spawns
# inherit it.
LIVE_LOG="$STATE_DIR/live-stream.log"
TAIL_PID=""
export AGENTWARE_LIVE_LOG="$LIVE_LOG"
: > "$LIVE_LOG" 2>/dev/null || true

# Start the follower unless opted out (--no-stream / AGENTWARE_NO_STREAM) or this
# is a --dry-run (no CLI is spawned, so nothing would ever write the sink).
# `-n0` ignores pre-existing lines; `-F` retries/follows by name even though the
# file may have no lines yet.
if [[ "$NO_STREAM" != true ]] && [[ "$DRY_RUN" != true ]]; then
  tail -n0 -F "$LIVE_LOG" 2>/dev/null & TAIL_PID=$!
fi

FEATURE_NAME=$(basename "$DOCS_DIR")
FEATURE_UPPER=$(echo "$FEATURE_NAME" | tr '[:lower:]' '[:upper:]' | tr '-' '_')

:
