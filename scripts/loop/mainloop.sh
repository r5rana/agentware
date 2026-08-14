#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/mainloop.sh — the MAIN phase: the iteration ladder, the bounded
# promote self-heal, and the completion contract.
#
# Unlike the bounded pre/post phases (scripts/loop/phase.sh), the main phase owns
# three things no simple spawn-until-promise driver can express: the
# zero-knowledge-loss completion gate, the PROMOTE re-engagement ladder, and the
# no-progress circuit breaker. Sourced by agentware.sh after safety.sh, markers.sh,
# metrics.sh and spawn.sh. Function definitions only, so sourcing standalone under
# `set -e` exits 0.
#
# DELIBERATELY NOT `local`: promote_retries, noprogress_streak, CURRENT_PROMPT and
# prev_remaining are plain globals. `promote_retries` in particular is read by
# emit_terminal_metric (scripts/loop/outcome.sh) from the EXIT trap — i.e. AFTER
# this function has returned — so making it local would silently zero the
# self_heal_count on every terminal event.

# assert_open_markers — refuse to start the main phase when plan.md has no open
# work, disambiguating the three reasons that can be true.
assert_open_markers() {
  [[ "$(open_markers)" -eq 0 ]] || return 0

  # Disambiguate "zero open markers": this means EITHER every task is ✅ (done)
  # OR the plan has no parseable task markers at all (malformed). Tell them apart
  # so a one-character marker typo fails LOUDLY instead of reading as success.
  local done_markers
  done_markers=$(grep -cE '^[[:space:]]*-[[:space:]]*✅[[:space:]]*\*\*[0-9]' "$DOCS_DIR/plan.md" 2>/dev/null || true)
  if [[ "${done_markers:-0}" -gt 0 ]]; then
    log "✓ No open (⬜/🟡) task markers in plan.md — all ${done_markers} task(s) ✅ (nothing to do)."
    exit 1
  fi
  # No well-formed markers of ANY status: look for task-LIKE-but-unparseable lines
  # (GitHub `- [ ]` checkboxes or `- **<letter>` headings) — the marker-typo class
  # of bug this feature exists to catch.
  if grep -qE '^[[:space:]]*-[[:space:]]*\[[ xX]?\]|^[[:space:]]*-[[:space:]]*\*\*[A-Za-z]' "$DOCS_DIR/plan.md" 2>/dev/null; then
    log "✗ Malformed plan.md: task-like lines present but ZERO canonical markers."
    log "  Expected markers like: - ⬜ **N** …  (emoji ⬜/🟡/✅ + **<digit>**)."
    log "  Diagnose: scripts/agentware plan lint --path \"$DOCS_DIR/plan.md\" --strict"
    exit 1
  fi
  log "⚠ No (⬜/🟡) task markers in plan.md — nothing to do."
  exit 1
}

# run_main_loop — drive the main phase to completion, max iterations, or a clean
# abort. Returns 0 when the feature completed; every other terminus exits directly.
run_main_loop() {
  log "=== Starting main phase ($MAX_ITERATIONS iterations max) ==="
  assert_open_markers

  # Self-heal state: normally spawn with MAIN_PROMPT; when completion is signalled
  # but learnings are unpromoted, switch to PROMOTE_PROMPT and count bounded retries.
  CURRENT_PROMPT="$MAIN_PROMPT"
  promote_retries=0
  # Track task burndown across iterations for the per-iteration emission (Task 6).
  prev_remaining=$(open_markers)
  noprogress_streak=0

  # Stage propagation (Task 7): main-loop spawns are attributed to loop-main.
  export AGENTWARE_STAGE="loop-main"
  export AGENTWARE_ORIGIN="loop-exec"  # Provenance origin (Task 7a): tag origin=loop

  local i main_role np_sig_before it_t0 it_wall iter_output
  local cur_remaining done_delta iter_promise unpromoted
  for i in $(seq 1 "$MAX_ITERATIONS"); do
    echo "$i" > "$STATE_DIR/.iteration"
    LOOP_ITERATIONS_USED="$i"   # tracked for the terminal outcome event (Task 7)
    log "--- main iteration $i/$MAX_ITERATIONS ($(open_markers) task(s) remaining) ---"

    # Normal task execution runs on the resolved main role; the PROMOTE self-heal
    # re-engagement is forced to cloud Claude (knowledge promotion is never left to
    # a weak/local main executor — Task 3).
    main_role=main
    [[ "$CURRENT_PROMPT" == "$PROMOTE_PROMPT" ]] && main_role=cloud
    # Snapshot the progress signals BEFORE spawning so the no-progress circuit
    # breaker (Task 4) can tell whether this iteration actually moved anything.
    np_sig_before="$(progress_signature)"
    it_t0=$(date +%s)
    # Capture iteration output to check for blocker-halt and progress signals.
    if [[ -t 1 ]] || { : 2>/dev/null >/dev/tty; } 2>/dev/null; then
      iter_output=$(run_agent "$main_role" "$CURRENT_PROMPT" 2>&1 | tee /dev/tty) || true
    else
      iter_output=$(run_agent "$main_role" "$CURRENT_PROMPT" 2>&1) || true
    fi
    it_wall=$(( $(date +%s) - it_t0 ))

    # SAFETY: blocker-halt — exit cleanly BEFORE re-spawning (scripts/loop/safety.sh).
    check_blocker_halt "$iter_output"

    # SAFETY: no-progress circuit breaker + opt-in cloud fallback. Only the normal
    # task-execution path is policed; the PROMOTE self-heal (main_role == cloud) has
    # its own bounded MAX_PROMOTE_RETRIES ladder and is exempt.
    if [[ "$main_role" == main ]]; then
      check_no_progress "$i" "$np_sig_before"
    fi

    # Per-task transition events (Task 7): diff plan.md marker states vs the prior
    # snapshot and emit one event per changed task (⬜->🟡 start, ->✅ complete).
    emit_task_transitions "loop-main" "$i"

    # Per-iteration observability event (best-effort, never blocks). Capture the
    # task burndown (delta vs the prior iteration), whether completion is signalled,
    # and the self-heal re-engagement count (promote_retries) so the LOOP itself is
    # fully observable. tasks_done_delta is clamped at >=0 (markers never un-flip).
    cur_remaining=$(open_markers)
    done_delta=$(( prev_remaining - cur_remaining )); [[ $done_delta -lt 0 ]] && done_delta=0
    if [[ -f "$STATE_DIR/.done" ]] || [[ "$cur_remaining" -eq 0 ]]; then
      iter_promise="signalled"
    else
      iter_promise="pending"
    fi
    emit_metric "main" "$i" "$MAX_ITERATIONS" "$cur_remaining" "$done_delta" "$iter_promise" "iteration" "$it_wall" "$promote_retries"
    prev_remaining="$cur_remaining"

    # Completion is accepted ONLY when the feature signals done (.done or no open
    # markers) AND every '> LEARNED:' marker has been promoted (zero knowledge loss).
    if [[ -f "$STATE_DIR/.done" ]] || [[ "$(open_markers)" -eq 0 ]]; then
      if learnings_promoted; then
        log "✓ Main phase complete at iteration $i (open markers: $(open_markers); learnings promoted)"
        LOOP_OUTCOME="completed"   # terminal outcome (Task 7); emitted by loop_on_exit
        return 0
      fi

      # Completion signalled but learnings unpromoted — deterministic, bounded self-heal.
      promote_retries=$((promote_retries + 1))
      unpromoted=$(scripts/agentware worklog scan --path "$DOCS_DIR/worklog.md" 2>&1 | grep -oE '[0-9]+ of [0-9]+ LEARNED' | head -1 || true)
      log "⚠ Completion signalled but '> LEARNED:' markers are unpromoted (${unpromoted:-unpromoted markers remain}). Self-heal attempt $promote_retries/$MAX_PROMOTE_RETRIES — re-engaging to promote."
      rm -f "$STATE_DIR/.done"
      CURRENT_PROMPT="$PROMOTE_PROMPT"

      if [[ $promote_retries -ge $MAX_PROMOTE_RETRIES ]]; then
        echo "Error: AUTO-PROMOTE FAILED: '> LEARNED:' marker(s) still unpromoted after $MAX_PROMOTE_RETRIES self-heal attempt(s)."
        echo "The feature is NOT complete (zero knowledge loss is enforced)."
        echo "Promote each via: scripts/agentware learn --topic <T> --summary <S> --tags <A,B> --content <...>"
        notify "auto-promote failed after $MAX_PROMOTE_RETRIES attempts"
        exit 1
      fi
      sleep 2
      continue
    fi

    # Not complete yet — a fresh task is being executed; reset self-heal to normal.
    CURRENT_PROMPT="$MAIN_PROMPT"
    promote_retries=0

    if [[ $i -eq $MAX_ITERATIONS ]]; then
      log "⚠ Reached max iterations ($MAX_ITERATIONS) without completion"
      LOOP_OUTCOME="hit_max_iterations"   # terminal outcome (Task 7)
      notify "max iterations reached"
      exit 1
    fi
    sleep 2
  done
}
