#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/common.sh — the three primitives every other loop lib calls.
#
# Sourced FIRST by agentware.sh; never executed directly. `log` alone has 68 call
# sites across the split, so it must be defined before anything that logs. Every
# statement here is a function definition (so sourcing standalone under `set -e`
# exits 0), and each function reads its state ($STATE_DIR, $TAIL_PID,
# $FEATURE_NAME) at CALL time — which is why this lib can be sourced before
# preflight has resolved any of them.

log() { echo "[$(date '+%H:%M:%S')] $1"; }

notify() {
  command -v notify-send >/dev/null 2>&1 && notify-send "agentware: $FEATURE_NAME" "$1" 2>/dev/null || true
  command -v osascript   >/dev/null 2>&1 && osascript -e "display notification \"$1\" with title \"agentware: $FEATURE_NAME\"" 2>/dev/null || true
}

# cleanup — the INT/TERM handler. Reaps the live-stream follower and exits 130.
# The EXIT handler is separate (loop_on_exit, scripts/loop/outcome.sh) because it
# must also fire on NORMAL exits to emit the terminal outcome event.
cleanup() {
  local iter
  iter=$(cat "$STATE_DIR/.iteration" 2>/dev/null || echo 0)
  # Reap the live-stream follower (harmless if unset / already gone).
  [[ -n "${TAIL_PID:-}" ]] && kill "$TAIL_PID" 2>/dev/null || true
  log "Stopped at iteration $iter"
  notify "stopped"
  exit 130
}
