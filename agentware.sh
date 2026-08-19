#!/bin/bash
# agentware.sh - Autonomous task loop for an agentware workspace.
#
# agentware is a clone-and-go steering framework for AI agents. The repo holds
# ONLY generic steering (methodology, agents, skills, this loop, the toolkit).
# All knowledge/learnings live in an EXTERNAL directory the operator chooses at
# first run (see .claude/skills/onboarding/SKILL.md). Nothing personal is committed.
#
# This script drives the 3-phase loop (pre / main / post) over a feature plan in
# <knowledge-dir>/work/<feature>/plan.md. Each phase runs until it emits its promise
# marker (or hits the iteration cap), then the next phase begins.
#
# Usage:
#   ./agentware.sh <feature-name> [--max-iterations N] [--agent AGENT]
#                                 [--skip-pre] [--skip-post]
#                                 [--pre-prompt "extra"]
#                                 [--main-prompt "extra"]
#                                 [--post-prompt "extra"]
#                                 [--dry-run] [--validate] [--no-stream]
#
# ---- THIS FILE IS THE ORCHESTRATOR ONLY (plan task 33) ----
# Every function and prompt lives in scripts/loop/*.sh; what remains here is the
# SEQUENCE — the order the phases and gates run in, and the terminal outcome each
# transition asserts. That sequence is the one thing worth reading top-to-bottom,
# so it is the one thing left.
#
# TWO RULES FOR EDITING THIS FILE
#
#  1. NEVER rewrite it in place while a loop is running. bash reads a script by
#     BYTE OFFSET as it executes, so truncating this file mid-run makes the live
#     interpreter resume at a stale offset in new text. Write a temp file and `mv`
#     it into place — the running process keeps its old inode and finishes cleanly.
#
#  2. SOURCE ORDER IS A CONTRACT, not a style. Libs that only DEFINE functions may
#     be sourced anywhere; libs that run at SOURCE time (args, routing, preflight,
#     prompts, metrics, outcome, dryrun) read state the previous ones set, and each
#     ends with an explicit `:` because `source` returns its last command's status
#     and this script runs under `set -e` — a lib ending on a false `[[ ]] && …`
#     idiom would abort the run having printed nothing.

set -e

# Resolve the sourced loop libraries relative to THIS script's own location, so the
# loop works from any working directory and from a symlinked checkout.
LOOP_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/loop"

# --- definition-only libs: the primitives every later lib calls ---------------
source "$LOOP_LIB_DIR/common.sh"      # log, notify, cleanup
source "$LOOP_LIB_DIR/usage.sh"       # usage

# --- source-time libs: parse the invocation, then gate it --------------------
# args.sh is sourced with NO arguments on purpose: a bare `source` shares this
# script's positional parameters, so its `shift`s consume OUR argv.
source "$LOOP_LIB_DIR/args.sh"        # FEATURE, KDIR, DOCS_DIR, STATE_DIR, flags
source "$LOOP_LIB_DIR/routing.sh"     # CLI/MODEL + per-phase runtime axes
source "$LOOP_LIB_DIR/preflight.sh"   # deps, plan.md, INITIALIZED, state dir, live stream

trap cleanup INT TERM

log "Starting agentware loop for '$FEATURE_NAME'"
log "Docs: $DOCS_DIR"
[[ -n "$KDIR" ]] && log "Knowledge dir: $KDIR (initialized: $INITIALIZED)"
notify "started"

# --- prompts + observability (source-time: they interpolate run state) --------
source "$LOOP_LIB_DIR/prompts.sh"      # PRE_PROMPT, MAIN_PROMPT, PROMOTE_PROMPT
source "$LOOP_LIB_DIR/prompts-post.sh" # POST_PROMPT (union), MERGE_PROMPT, extras
source "$LOOP_LIB_DIR/markers.sh"      # open_markers, snapshot_task_states, learnings_promoted
source "$LOOP_LIB_DIR/metrics.sh"      # METRICS_LOG + per-iteration/task emitters
source "$LOOP_LIB_DIR/outcome.sh"      # LOOP_OUTCOME state, terminal event, EXIT trap (+ fleet sink)

# --- gates and drivers -------------------------------------------------------
source "$LOOP_LIB_DIR/safety.sh"       # circuit breaker, blocker halt, updater lock guard
source "$LOOP_LIB_DIR/orglint.sh"      # run_org_lint_gate        (donor capability)
source "$LOOP_LIB_DIR/retrieval.sh"    # run_retrieval_quality_gate (donor capability)
source "$LOOP_LIB_DIR/gates-plan.sh"   # run_plan_gates
source "$LOOP_LIB_DIR/hooks.sh"        # run_pre_hooks, run_post_hooks, kb_* resolvers
source "$LOOP_LIB_DIR/report.sh"       # emit_review_report
source "$LOOP_LIB_DIR/review.sh"       # run_selfheal_review
source "$LOOP_LIB_DIR/spawn.sh"        # run_agent, show_spawn_argv, codex injection
source "$LOOP_LIB_DIR/kbsync.sh"       # run_kb_sync, kb_sync_push, emit_fleet_line
source "$LOOP_LIB_DIR/dryrun.sh"       # --dry-run report (exits 0 when set)
source "$LOOP_LIB_DIR/phase.sh"        # run_phase (pre/post)
source "$LOOP_LIB_DIR/mainloop.sh"     # run_main_loop

# ---- RUN BEGINS (Task 7 terminal-outcome tracking) ----
# Arm terminal-event emission now that a real run is starting (gates loop_on_exit).
# If a pre-hook gate aborts below, the EXIT handler records `pre_hook_abort`.
# Seed the task-state snapshot from the INITIAL plan.md so the first observed
# transition diffs against the true starting state (no absent->open noise).
LOOP_STARTED=true
LOOP_OUTCOME="pre_hook_abort"
snapshot_task_states > "$SNAPSHOT_FILE" 2>/dev/null || true

# ---- REVERSE LOOP-GUARD ----
# Refuse/defer starting while a live updater/tick apply holds the lock, so a loop
# never reads a HALF-UPDATED package mid-apply (scripts/loop/safety.sh).
run_updater_lock_guard

# ---- PRE-HOOK ----
run_pre_hooks

# Pre-hook gates passed — no definite terminal yet (the consumer ignores "unknown"
# until an explicit set-point below asserts completed / hit_max_iterations / etc.).
LOOP_OUTCOME="unknown"

# ---- PLAN STATE: CLAIM running (feature 260710-plan-state-machine) ----
# Bring the plan to `running` per the state-machine policy (draft->ready->running /
# ready->running / running re-claim), or REFUSE a terminal (done/superseded/
# cancelled) plan with the create-a-new-feature instruction. Guarded to a
# KB-resident feature so non-KB / self-development runs are untouched. A failed or
# aborted run leaves the state at `running` (honest; dream flags it stalled later).
if [[ -x scripts/agentware ]] && [[ "$INITIALIZED" == true ]] && \
   [[ -n "$KDIR" ]] && [[ "$DOCS_DIR" == "$KDIR/work/$FEATURE" ]] && \
   [[ -f "$DOCS_DIR/plan.md" ]]; then
  log "[state] scripts/agentware plan claim $FEATURE --actor loop"
  if ! scripts/agentware plan claim "$FEATURE" --actor loop; then
    echo "Error: [state] refusing to start plan '$FEATURE' (see message above)."
    LOOP_OUTCOME="pre_hook_abort"
    exit 1
  fi
fi

# ---- PRE PHASE (3 tasks max) ----
if [[ "$SKIP_PRE" != true ]]; then
  run_phase "pre" "$PRE_PROMPT" 3 "PRE_TASK_COMPLETE"
fi

# ---- MAIN PHASE ----
run_main_loop

# ---- POST-HOOK ----
# A post-hook gate (features/index/lint/org/retrieval/release/scan) aborts via
# exit 1; record that the terminal outcome is post_hook_failure until they pass.
LOOP_OUTCOME="post_hook_failure"
run_post_hooks
LOOP_OUTCOME="completed"

# ---- POST PHASE (1 task) ----
if [[ "$SKIP_POST" != true ]]; then
  run_phase "post" "$POST_PROMPT" 1 "POST_COMPLETE"
fi

# ---- SELF-HEALING ADVERSARIAL-REVIEW GATE (feature 260713, Task 5) ----
# Fires at the END of a SELF-EXTENSION run, AFTER the post-phase assessment and
# BEFORE the done-stamp + kb-sync. On a self-extension diff it runs the bounded
# AUDIT -> CALL-OUT -> FIX -> RE-AUDIT loop (Opus/max). It PROCEEDS only on a clean
# re-audit round; if a confirmed high/medium survives every round the run is BLOCKED
# — the done-stamp AND the KB commit are SKIPPED and the operator is escalated
# (R-EXEC-06, never a silent ship). Non-self-extension diffs pass through untouched.
if ! run_selfheal_review; then
  LOOP_OUTCOME="adversarial_review_blocked"
  echo "Error: [review] the self-healing adversarial-review gate BLOCKED this run."
  echo "A confirmed high/medium finding survived every remediation round (AGENTWARE_REVIEW_MAX_ROUNDS)."
  echo "The run is NOT complete: the done-stamp and the KB commit are SKIPPED (R-EXEC-06 escalation)."
  echo "Review the report(s) under $STATE_DIR/ and the worklog call-out, then fix and re-run."
  notify "adversarial-review gate BLOCKED — escalated"
  exit 1
fi

# ---- PLAN STATE: DONE on success (feature 260710-plan-state-machine) ----
# The run completed (all markers ✅, learnings promoted). Stamp running->done so
# the terminal state is team-visible in plan.md. Best-effort: never fail an
# otherwise-complete run on the status flip. Guarded to a KB-resident feature.
if [[ -x scripts/agentware ]] && [[ "$INITIALIZED" == true ]] && \
   [[ -n "$KDIR" ]] && [[ "$DOCS_DIR" == "$KDIR/work/$FEATURE" ]] && \
   [[ -f "$DOCS_DIR/plan.md" ]]; then
  log "[state] scripts/agentware plan set-state $FEATURE done --actor loop"
  # Un-swallowed (feature 260711): a refused done-stamp used to vanish behind
  # `|| true`. Now WARN loudly (with set-state's stderr) and emit a
  # `done_stamp_failed` metric so nothing is silently discarded. The run STAYS
  # successful (honest-running) — the refusal is observability, not a hard fail.
  if ss_out="$(scripts/agentware plan set-state "$FEATURE" done --actor loop 2>&1)"; then
    [[ -n "$ss_out" ]] && log "$ss_out"
  else
    log "⚠ WARNING: done-stamp REFUSED — running->done not applied (run stays successful): ${ss_out:-<no message>}"
    emit_done_stamp_failed "$ss_out"
  fi
fi

# ---- KB SYNC (commit + push) ----
# Runs LAST — AFTER the post phase — so the commit captures the assessment + its
# ledger rows (C-1). Fires on BOTH the post-ran and --skip-post paths (C-2).
run_kb_sync

log "✓ $FEATURE_NAME fully complete"
notify "complete!"
