#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/safety.sh — the circuit breakers that stop a run from silently
# burning its budget or reading a half-updated package.
#
# Sourced by agentware.sh; function definitions plus two constants, and it ends
# with `:` — see the note in args.sh. Three independent guards live here:
#   1. the no-progress circuit breaker (progress_signature + check_no_progress)
#   2. the blocker-halt seam (AW_BLOCKER_HALT + check_blocker_halt)
#   3. the reverse loop-guard against a live updater/tick apply (run_updater_lock_guard)

# ---- SAFETY: no-progress circuit breaker signal (Task 4) ----
# Portable stdin hasher (shasum on macOS, falls back to cksum elsewhere). Only used
# for equality comparison across iterations, so the specific algorithm is irrelevant.
_hash_stdin() { if command -v shasum >/dev/null 2>&1; then shasum; else cksum; fi; }

# progress_signature — a SINGLE signature over the three independent "did this main
# iteration make progress?" signals. The circuit breaker trips only when the
# signature is byte-identical across an iteration, which is EXACTLY the AND of all
# three being unchanged — "no git diff AND no valid tool call AND no plan-status
# change" (the plan's stall definition):
#   1. git working-tree state (status --porcelain + diff) — did the executor edit code?
#   2. plan.md task markers (⬜/🟡/✅ lines)               — did a task's status advance?
#   3. live action-stream line count ($LIVE_LOG)           — did the executor make ANY tool call?
# Each component degrades safely: a missing git repo / plan / live log contributes a
# constant, so the breaker leans on whichever signals ARE present and never false-trips
# while real work happens — ANY one component moving changes the signature and resets
# the streak. The live-stream sink is written by BOTH the claude PostToolUse hook and
# the codex-stream renderer, so the tool-call signal works for cloud AND local mains.
progress_signature() {
  {
    git status --porcelain 2>/dev/null
    git diff 2>/dev/null
    grep -nE '^[[:space:]]*-[[:space:]]*(⬜|🟡|✅)[[:space:]]*\*\*[0-9]' "$DOCS_DIR/plan.md" 2>/dev/null
    wc -l < "$LIVE_LOG" 2>/dev/null
  } | _hash_stdin | awk '{print $1}'
}

# ---- SAFETY: no-progress circuit breaker + opt-in cloud fallback (Task 4) ----
# A weak/local main executor can stall — hallucinate, loop, or fail to edit — and
# silently burn the whole iteration budget. Guard against that WITHOUT ever risking a
# false-trip on a slow-but-working run:
#   - NOPROGRESS_LIMIT (env AGENTWARE_NOPROGRESS_LIMIT, default 3): after K CONSECUTIVE
#     main iterations whose progress_signature is unchanged, emit the fixed greppable
#     token AW_NOPROGRESS_ABORT and exit cleanly. Any real progress resets the streak.
#   - AGENTWARE_MAIN_FALLBACK=claude: opt-in recovery — when a main iteration stalls on
#     a NON-claude (e.g. local/codex) executor, re-run that SAME iteration once on cloud
#     Claude before counting the strike, so a transient local stall self-recovers.
# pre+post phases stay cloud Claude by default, so completion/assessment is never
# judged by the weak main executor. Revert routing with one command (effective next
# run):  scripts/agentware config --set-main-cli claude
NOPROGRESS_LIMIT="${AGENTWARE_NOPROGRESS_LIMIT:-3}"

# ---- SAFETY: blocker-halt seam (invocation-cwd-context feature) ----
# When an agent emits AW_BLOCKER_HALT (e.g. because it cannot resolve which
# checkout to target and is in loop mode), the loop exits cleanly with a
# distinct terminal outcome (blocked_on_ambiguity) BEFORE re-spawning. The
# agent writes `> BLOCKER:` to the worklog and emits this token on stdout.
AW_BLOCKER_HALT="AW_BLOCKER_HALT"

# check_blocker_halt <iteration-output>
# Exits the loop cleanly (0) when the agent signalled it is blocked on ambiguity.
check_blocker_halt() {
  echo "$1" | grep -q "$AW_BLOCKER_HALT" || return 0
  log "⚠ Agent emitted AW_BLOCKER_HALT — blocked on ambiguity. Exiting cleanly."
  LOOP_OUTCOME="blocked_on_ambiguity"
  notify "blocked on ambiguity (AW_BLOCKER_HALT)"
  exit 0
}

# check_no_progress <iteration> <signature-before>
# Compare the post-spawn progress signature against the pre-spawn one, apply the
# opt-in cloud fallback, and trip the breaker after NOPROGRESS_LIMIT consecutive
# stalls. Reads/writes the caller's `noprogress_streak` (bash dynamic scope).
check_no_progress() {
  local i="$1" before="$2" after
  after="$(progress_signature)"
  if [[ "$after" == "$before" ]]; then
    # (b) Opt-in cloud fallback — re-run this SAME iteration once on cloud Claude
    # when the main executor is non-claude, giving a transient local stall a chance
    # to recover before the strike is counted.
    if [[ "${AGENTWARE_MAIN_FALLBACK:-}" == claude && "$MAIN_CLI" != claude ]]; then
      log "⚠ Stalled main iteration $i (no progress) — AGENTWARE_MAIN_FALLBACK=claude: re-running on cloud Claude."
      run_agent cloud "$CURRENT_PROMPT" || true
      after="$(progress_signature)"
    fi
  fi
  if [[ "$after" == "$before" ]]; then
    noprogress_streak=$((noprogress_streak + 1))
    log "⚠ No-progress (stall) main iteration: $noprogress_streak/$NOPROGRESS_LIMIT (no git diff, no tool call, no plan-status change)."
    if [[ $noprogress_streak -ge $NOPROGRESS_LIMIT ]]; then
      echo "AW_NOPROGRESS_ABORT: $noprogress_streak consecutive main iterations made no progress (no git diff AND no valid tool call AND no plan-status change)."
      echo "The main executor appears stalled; aborting cleanly so the iteration budget is not silently burned."
      echo "Recover by re-running with AGENTWARE_MAIN_FALLBACK=claude, or revert routing with: scripts/agentware config --set-main-cli claude (effective next run)."
      notify "no-progress circuit breaker tripped (AW_NOPROGRESS_ABORT)"
      exit 1
    fi
  else
    noprogress_streak=0   # any real progress resets the consecutive-stall streak
  fi
}

# ---- REVERSE LOOP-GUARD (feature 260712-scheduler-updater, Task 9;
#      hardened by 260712-p2b-hardening-followups, Task 3 / audit C3+C7) ----
# Read the updater/tick lock word, retrying a TRANSIENT CLI error up to 3 times
# before defaulting to `free`. A single transient error must NOT fail the loop open
# blindly, and a persistent error must NOT blanket-refuse every loop start (that
# would DoS the loop) — bounded retry then fail-open is the balance (C7).
_aw_lock_status() {
  local _w _i
  for _i in 1 2 3; do
    if _w="$(scripts/agentware tick --lock-status 2>/dev/null)" && [[ -n "$_w" ]]; then
      printf '%s\n' "$_w"
      return 0
    fi
  done
  printf 'free\n'
  return 0
}

# run_updater_lock_guard — refuse/defer starting a loop while a live updater/tick
# apply holds the lock, so a loop never reads a HALF-UPDATED package mid-apply.
# Checks the updater/tick lock SPECIFICALLY (never dream.lock — no contention/
# deadlock). Mirrors the plan-claim refusal shape (LOOP_OUTCOME=pre_hook_abort +
# exit 1). On a STALE lock (crashed prior apply) it neither silently allows nor
# permanently refuses: it runs a one-shot self-heal (reconcile to last_good), then
# RE-CHECKS the lock — a live apply that acquired the lock during the self-heal
# window makes self-heal return `locked` WITHOUT reconciling, so the loop must
# refuse rather than proceed onto a half-updated tree (C3).
run_updater_lock_guard() {
  [[ -x scripts/agentware ]] || return 0
  [[ "$INITIALIZED" == true ]] || return 0
  [[ -n "${KDIR:-}" ]] || return 0

  local aw_lock_state
  aw_lock_state="$(_aw_lock_status)"
  case "$aw_lock_state" in
    held)
      echo "Error: [guard] a live agentware updater/tick apply holds the lock — refusing to start a loop (never read a half-updated package mid-apply). Retry in a moment."
      LOOP_OUTCOME="pre_hook_abort"
      exit 1
      ;;
    stale)
      log "[guard] stale updater/tick lock (crashed prior apply) — running one-shot self-heal before starting."
      # A non-zero self-heal means it could NOT reconcile: the package is STRANDED
      # (needs_manual_recovery) or migrate errored — refuse rather than start a loop
      # onto a half-updated tree (audit follow-up: a bare `|| true` swallowed this and
      # let the loop proceed onto the stranded package).
      if ! scripts/agentware update --self-heal; then
        echo "Error: [guard] self-heal could NOT reconcile a crashed apply (package STRANDED / needs_manual_recovery) — refusing to start a loop. Inspect ~/.agentware/update-state.json, then re-run \`scripts/agentware update --self-heal\`."
        LOOP_OUTCOME="pre_hook_abort"
        exit 1
      fi
      # RE-CHECK: proceed ONLY when the lock is now fully FREE. Anything else — a LIVE
      # apply re-acquired it (held), or a fresh crash left another stale lock — means
      # the tree is not safe to read yet (C3, broadened from held-only).
      aw_lock_state="$(_aw_lock_status)"
      if [[ "$aw_lock_state" != "free" ]]; then
        echo "Error: [guard] updater lock is still '$aw_lock_state' after self-heal — refusing to start a loop (never read a half-updated package mid-apply). Retry in a moment."
        LOOP_OUTCOME="pre_hook_abort"
        exit 1
      fi
      ;;
    *)
      : ;;  # free — proceed
  esac
}

:
