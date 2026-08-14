#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/metrics.sh — per-iteration and per-task observability emission.
#
# Sourced at SOURCE time by agentware.sh after args.sh/preflight.sh (METRICS_LOG and
# SNAPSHOT_FILE are assigned from $KDIR / $DOCS_DIR / $STATE_DIR here) and BEFORE
# outcome.sh, whose terminal event appends to the same sink. Ends with `:` — see the
# note in args.sh.
#
# ---- LOOP METRICS EMISSION (best-effort, opt-out, NEVER blocks) ----
#
# Append ONE structured JSON event per phase/iteration to <kdir>/logs/metrics.jsonl
# (feature 260626-observability-suite, Task 6). This makes the LOOP ITSELF
# observable — agentware IS the loop. The channel is READ-ONLY consumed by
# `scripts/agentware metrics` (_read_metrics_jsonl / derive_iteration_costs /
# derive_outcome). Design invariants:
#   - BEST-EFFORT: every write is guarded (`>> … 2>/dev/null || true`); a missing
#     dir, full disk, or any failure NEVER blocks or fails the loop.
#   - OPT-OUT: AGENTWARE_METRICS_EMIT=0 disables emission (default ON, mirroring
#     KB autocommit's opt-in/opt-out posture).
#   - ADDITIVE + gitignored: logs/ is already excluded; no existing telemetry is
#     touched. Field names lean on OpenTelemetry GenAI conventions (gen_ai.*)
#     where natural so the channel is later exportable.
#
# Path: lives under the knowledge dir's logs/ (the SAME tree `metrics` reads, i.e.
# os.path.join(kdir, "logs", "metrics.jsonl")). Falls back to the in-repo docs
# dir's .loop/ when no knowledge dir is configured (agentware self-development).
if [[ -n "$KDIR" ]]; then
  METRICS_LOG="$KDIR/logs/metrics.jsonl"
else
  METRICS_LOG="$DOCS_DIR/.loop/metrics.jsonl"
fi

# A small persisted snapshot records the last-seen state of each task id so
# transitions are diffed deterministically across iterations.
SNAPSHOT_FILE="$STATE_DIR/.task_states"

metrics_emit_enabled() {
  # Opt-in/opt-out, default ON (mirrors KB autocommit). Per-run env override
  # AGENTWARE_METRICS_EMIT=0 disables emission. Best-effort observability only.
  [[ "${AGENTWARE_METRICS_EMIT:-1}" != "0" ]]
}

# emit_metric <phase> <iteration> <max> <tasks_remaining> <tasks_done_delta> \
#             <promise_status> <result> <phase_wall_s> <self_heal_count>
# Appends ONE JSON line built by jq (a hard preflight dep) so every value is
# correctly typed + escaped. Best-effort: any failure is swallowed (|| true) so
# emission never becomes the loop's concern.
emit_metric() {
  metrics_emit_enabled || return 0
  local phase="$1" iteration="$2" max="$3" tasks_remaining="$4" \
        tasks_done_delta="$5" promise_status="$6" result="$7" \
        phase_wall_s="$8" self_heal_count="$9"
  local ts tasks_total done_n
  ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  # Total plan tasks (any status) = open (the canonical open_markers count, the
  # SOLE ⬜|🟡 grep — see the loop<->linter byte-identity contract) + done (a
  # ✅-only count). Deriving total this way keeps emit_metric off that contract.
  done_n=$(grep -cE '^[[:space:]]*-[[:space:]]*✅[[:space:]]*\*\*[0-9]' "$DOCS_DIR/plan.md" 2>/dev/null || true)
  tasks_total=$(( $(open_markers) + ${done_n:-0} ))
  mkdir -p "$(dirname "$METRICS_LOG")" 2>/dev/null || true
  jq -cn \
    --arg ts "$ts" \
    --arg feature "$FEATURE_NAME" \
    --arg stage "loop-$phase" \
    --arg phase "$phase" \
    --arg promise_status "$promise_status" \
    --arg result "$result" \
    --argjson iteration "${iteration:-0}" \
    --argjson max "${max:-0}" \
    --argjson tasks_total "${tasks_total:-0}" \
    --argjson tasks_remaining "${tasks_remaining:-0}" \
    --argjson tasks_done_delta "${tasks_done_delta:-0}" \
    --argjson phase_wall_s "${phase_wall_s:-0}" \
    --argjson self_heal_count "${self_heal_count:-0}" \
    '{ts:$ts, feature:$feature, stage:$stage, phase:$phase,
      iteration:$iteration, max:$max, tasks_total:$tasks_total,
      tasks_remaining:$tasks_remaining, tasks_done_delta:$tasks_done_delta,
      promise_status:$promise_status, result:$result,
      phase_wall_s:$phase_wall_s, self_heal_count:$self_heal_count,
      "gen_ai.operation.name":"agentware.loop", "gen_ai.system":"agentware"}' \
    >> "$METRICS_LOG" 2>/dev/null || true
}

# emit_done_stamp_failed <stderr>
# Append ONE `done_stamp_failed` event to metrics.jsonl (best-effort, typed via
# jq) when the terminal running->done stamp is REFUSED. Deliberately carries NO
# integer `iteration` field — so derive_iteration_costs (which folds only events
# bearing an int iteration, and also skips the `done_stamp_failed` discriminator)
# never mistakes it for a phantom iteration-0 record. Same best-effort/opt-out
# posture as emit_metric: never blocks or fails the loop.
emit_done_stamp_failed() {
  metrics_emit_enabled || return 0
  local detail="$1" ts
  ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  mkdir -p "$(dirname "$METRICS_LOG")" 2>/dev/null || true
  jq -cn \
    --arg ts "$ts" \
    --arg feature "$FEATURE_NAME" \
    --arg stage "loop-done-stamp" \
    --arg detail "$detail" \
    '{event:"done_stamp_failed", ts:$ts, feature:$feature, stage:$stage,
      detail:$detail,
      "gen_ai.operation.name":"agentware.loop.done_stamp",
      "gen_ai.system":"agentware"}' \
    >> "$METRICS_LOG" 2>/dev/null || true
}

# emit_task_transition <stage> <iteration> <task> <from> <to> <approx>
# Appends ONE `task_transition` JSON event (best-effort, typed via jq).
emit_task_transition() {
  local stage="$1" iteration="$2" task="$3" from="$4" to="$5" approx="$6"
  local ts
  ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  mkdir -p "$(dirname "$METRICS_LOG")" 2>/dev/null || true
  jq -cn \
    --arg ts "$ts" \
    --arg feature "$FEATURE_NAME" \
    --arg stage "$stage" \
    --arg task "$task" \
    --arg from "$from" \
    --arg to "$to" \
    --argjson iteration "${iteration:-0}" \
    --argjson approx "${approx:-false}" \
    '{event:"task_transition", ts:$ts, feature:$feature, stage:$stage,
      iteration:$iteration, task:$task, from:$from, to:$to, approx:$approx,
      "gen_ai.operation.name":"agentware.loop.task", "gen_ai.system":"agentware"}' \
    >> "$METRICS_LOG" 2>/dev/null || true
}

# emit_task_transitions <stage> <iteration>
# Diff the CURRENT plan.md marker states against the persisted snapshot and emit a
# transition for each task whose state changed (⬜->🟡 start, 🟡->✅ / ⬜->✅ complete).
# An ⬜->✅ jump (the start was never observed in a snapshot) is flagged `approx:true`.
# Tasks absent from the prior snapshot (newly added mid-run) are recorded SILENTLY —
# no absent->X noise event. Then the snapshot is refreshed. Best-effort throughout.
emit_task_transitions() {
  metrics_emit_enabled || return 0
  local stage="$1" iteration="$2"
  local cur id state prev_state id_re approx
  cur="$(snapshot_task_states)"
  while IFS=' ' read -r id state; do
    [[ -z "$id" ]] && continue
    prev_state=""
    if [[ -f "$SNAPSHOT_FILE" ]]; then
      # Anchor on "<id> " (dots in phased ids escaped) so id 1 never matches 1.1.
      id_re="^$(printf '%s' "$id" | sed 's/[.]/\\./g') "
      prev_state="$(grep -E "$id_re" "$SNAPSHOT_FILE" 2>/dev/null | head -1 | awk '{print $2}' || true)"
    fi
    [[ -z "$prev_state" ]] && continue          # new/absent task: record silently
    [[ "$prev_state" == "$state" ]] && continue # unchanged
    approx="false"
    [[ "$prev_state" == "open" && "$state" == "done" ]] && approx="true"
    emit_task_transition "$stage" "$iteration" "$id" "$prev_state" "$state" "$approx"
  done <<< "$cur"
  printf '%s\n' "$cur" > "$SNAPSHOT_FILE" 2>/dev/null || true
}

:
