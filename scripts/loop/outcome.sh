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
# The loop can end in one of these terminal states (_TERMINAL_OUTCOMES, mirrored in
# scripts/agentware): completed / hit_max_iterations / post_hook_failure /
# pre_hook_abort / premise_falsified (feature 260717 — the premise-grounding gate
# halt). We track the current intent in LOOP_OUTCOME and emit ONE terminal
# event on loop exit (see emit_terminal_metric). LOOP_STARTED gates emission to
# real runs only (never on --help/--dry-run/preflight-dep failures, which exit
# before the run begins). LOOP_OUTCOME defaults to "unknown" — a value the consumer
# (derive_outcome) deliberately IGNORES — so an abnormal/interrupted exit never
# fabricates a definite outcome; only the explicit set-points assert one.
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
}
trap 'loop_on_exit' EXIT

:
