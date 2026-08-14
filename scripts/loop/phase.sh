#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/phase.sh — run_phase: the bounded pre/post phase driver.
#
# The main phase is NOT driven by this function (it has its own self-heal ladder,
# circuit breaker and completion contract — see scripts/loop/mainloop.sh); this
# drives the two BOUNDED phases, which simply spawn until their promise marker
# appears or the iteration cap is reached. Sourced by agentware.sh after spawn.sh,
# markers.sh and metrics.sh. Function definition only, so sourcing standalone under
# `set -e` exits 0.
run_phase() {
  local phase_name="$1"
  local prompt="$2"
  local max_iter="$3"
  local completion_marker="$4"

  log "=== Starting $phase_name phase ($max_iter tasks) ==="

  # Stage propagation (Task 7): every CLI spawned in this phase inherits the loop
  # stage so its session is attributed to loop-pre / loop-post (the main loop sets
  # loop-main). Read by the metrics stage classifier (_STAGE_TO_PHASE).
  export AGENTWARE_STAGE="loop-$phase_name"
  # Provenance origin (Task 7a, 260710-learning-repair-p1): spawned CLIs inherit
  # this; log-prompt.sh tags their records origin=loop. run_phase runs only for
  # pre/post -> loop-pre / loop-post.
  export AGENTWARE_ORIGIN="loop-$phase_name"

  for i in $(seq 1 "$max_iter"); do
    echo "$i" > "$STATE_DIR/.${phase_name}_iteration"
    log "--- $phase_name task $i/$max_iter ---"

    local it_t0 it_wall rem
    it_t0=$(date +%s)
    # tee /dev/tty shows live output while capturing; skip when no TTY is
    # available (subprocess tests, CI) to avoid blocking the pipeline.
    # The -t 1 test checks if stdout is connected to a terminal — more reliable
    # than file-existence checks since /dev/tty can exist but be unopenable.
    if [[ -t 1 ]] || { : 2>/dev/null >/dev/tty; } 2>/dev/null; then
      output=$(run_agent "$phase_name" "$prompt" 2>&1 | tee /dev/tty) || true
    else
      output=$(run_agent "$phase_name" "$prompt" 2>&1) || true
    fi
    it_wall=$(( $(date +%s) - it_t0 ))
    rem=$(open_markers)
    # Per-task transition events (Task 7): diff plan.md marker states vs the prior
    # snapshot and emit one event per changed task (best-effort, never blocks).
    emit_task_transitions "loop-$phase_name" "$i"

    if echo "$output" | grep -q "<promise>$completion_marker</promise>"; then
      # Per-phase/iteration observability event (best-effort, never blocks).
      emit_metric "$phase_name" "$i" "$max_iter" "$rem" 0 "signalled" "complete" "$it_wall" 0
      log "✓ $phase_name complete at task $i"
      return 0
    fi
    emit_metric "$phase_name" "$i" "$max_iter" "$rem" 0 "pending" "continue" "$it_wall" 0
    sleep 2
  done

  log "✓ $phase_name phase finished ($max_iter tasks)"
  return 0
}
