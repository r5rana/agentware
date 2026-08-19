#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/outcome.sh — terminal run outcome: the tracked state, the ONE
# terminal event, and the EXIT trap that emits it.
#
# Sourced at SOURCE time by agentware.sh after metrics.sh (emit_terminal_metric
# appends to $METRICS_LOG and honours metrics_emit_enabled) and after common.sh.
# Registering the EXIT trap HERE rather than in the orchestrator is deliberate: the
# trap and the handler it names ship in one file, so the split cannot land the
# handler while dropping its registration. tests/_loop_wiring.py counts a `trap`
# handler as a call site for exactly that reason. Ends with `:` — see args.sh.

# ---- TERMINAL RUN OUTCOME (Task 7) ----
# Every value assigned to LOOP_OUTCOME anywhere in agentware.sh or scripts/loop/*.sh
# is a terminal this loop CAN emit. Today that set is:
#   completed / hit_max_iterations / post_hook_failure / pre_hook_abort /
#   premise_falsified (the R-GND-01 premise-grounding halt, gates-plan.sh) /
#   blocked_on_ambiguity + adversarial_review_blocked (the R-EXEC-06 hard blockers) /
#   unknown (the default — asserts nothing).
# TWO consumers read that vocabulary and they are DELIBERATELY different widths:
#   * `_TERMINAL_OUTCOMES` (agentware_lib.ops.derive) — the NARROW set derive_outcome
#     trusts verbatim. The hard blockers are excluded on purpose and preserved BY
#     NAME through _graph_raw_terminal_outcome, so the graph HALT signal survives.
#   * `LOOP_OUTCOME_ENUM` (agentware_lib.ops.fleet) — the WIDE set org lint O12
#     accepts in the committed fleet sink. It MUST stay a superset of the set above:
#     an emitted value the enum does not name fails O12, and the org-lint gate then
#     hard-blocks every subsequent run in team mode (plan task 34).
# Adding a set-point below therefore means widening LOOP_OUTCOME_ENUM in the same
# change; tests.integration.test_loop_emission re-derives this set from the shell sources and
# fails if it ever outgrows the enum.
#
# We track the current intent in LOOP_OUTCOME and emit ONE terminal event on loop
# exit (see emit_terminal_metric). LOOP_STARTED gates emission to real runs only
# (never on --help/--dry-run/preflight-dep failures, which exit before the run
# begins). LOOP_OUTCOME defaults to "unknown" — a value the consumer (derive_outcome)
# deliberately IGNORES — so an abnormal/interrupted exit never fabricates a definite
# outcome; only the explicit set-points assert one.
LOOP_STARTED=false
LOOP_OUTCOME="unknown"
LOOP_ITERATIONS_USED=0

# emit_terminal_metric — append ONE `terminal` outcome event on loop exit (called
# from loop_on_exit). Schema: {ts, feature, outcome, iterations_used, max,
# self_heal_count, tasks_total, tasks_done, promise_status}. outcome is the tracked
# LOOP_OUTCOME (the consumer ignores "unknown", so only the definite states ever
# assert an outcome). Read-only over plan.md for the task tallies. Best-effort.
emit_terminal_metric() {
  metrics_emit_enabled || return 0
  local ts open_n done_n tasks_total promise_status
  ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  open_n="$(open_markers)"
  done_n=$(grep -cE '^[[:space:]]*-[[:space:]]*✅[[:space:]]*\*\*[0-9]' "$DOCS_DIR/plan.md" 2>/dev/null || true)
  tasks_total=$(( open_n + ${done_n:-0} ))
  if [[ -f "$STATE_DIR/.done" ]] || [[ "$open_n" -eq 0 ]]; then
    promise_status="signalled"
  else
    promise_status="pending"
  fi
  mkdir -p "$(dirname "$METRICS_LOG")" 2>/dev/null || true
  jq -cn \
    --arg ts "$ts" \
    --arg feature "$FEATURE_NAME" \
    --arg outcome "$LOOP_OUTCOME" \
    --arg promise_status "$promise_status" \
    --argjson iterations_used "${LOOP_ITERATIONS_USED:-0}" \
    --argjson max "${MAX_ITERATIONS:-0}" \
    --argjson self_heal_count "${promote_retries:-0}" \
    --argjson tasks_total "${tasks_total:-0}" \
    --argjson tasks_done "${done_n:-0}" \
    '{event:"terminal", ts:$ts, feature:$feature, outcome:$outcome,
      iterations_used:$iterations_used, max:$max, self_heal_count:$self_heal_count,
      tasks_total:$tasks_total, tasks_done:$tasks_done, promise_status:$promise_status,
      "gen_ai.operation.name":"agentware.loop.terminal", "gen_ai.system":"agentware"}' \
    >> "$METRICS_LOG" 2>/dev/null || true
}

# Single EXIT handler: reap the live-stream follower AND, once a real run has begun,
# append the terminal outcome event. Best-effort — never blocks the exit path
# (emit_terminal_metric swallows every failure).
loop_on_exit() {
  [[ -n "${TAIL_PID:-}" ]] && kill "$TAIL_PID" 2>/dev/null || true
  [[ "$LOOP_STARTED" == true ]] && emit_terminal_metric || true
  # The COMMITTED org-fleet twin (R-ORG-06), failure paths only. A completed run
  # emits inside run_kb_sync and rides its commit; hit_max_iterations /
  # post_hook_failure / blocked_on_ambiguity / sync_failed all exit 1 BEFORE
  # run_kb_sync, so without this their terminal line never commits — and those are
  # precisely the events the fleet reports exist to count. Guarded on
  # FLEET_SINK_COMMITTED so exactly ONE line is committed per run, never two.
  # Best-effort: commit_fleet_sink_only swallows every failure and no-ops on a
  # flat KB, so this never blocks the exit path (kbsync.sh).
  if [[ "$LOOP_STARTED" == true ]] && [[ "${FLEET_SINK_COMMITTED:-false}" != true ]]; then
    case "$LOOP_OUTCOME" in
      hit_max_iterations|post_hook_failure|blocked_on_ambiguity|sync_failed)
        commit_fleet_sink_only || true ;;
    esac
  fi
}
trap 'loop_on_exit' EXIT

# Guard so the committed fleet line is written exactly once per run: run_kb_sync
# sets it true on the completed path, and the EXIT trap above covers the failure
# paths that never reach run_kb_sync.
FLEET_SINK_COMMITTED=false

:
