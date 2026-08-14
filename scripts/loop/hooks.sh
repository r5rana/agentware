#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/hooks.sh — the deterministic toolkit gates around the main phase.
#
# Sourced by agentware.sh AFTER gates-plan.sh, orglint.sh and retrieval.sh, whose
# functions it calls. All hooks no-op gracefully if scripts/agentware is absent, and
# KDIR-dependent gates skip until the workspace is initialized. Function definitions
# only, so sourcing standalone under `set -e` exits 0.
#
# The gate ORDER inside run_post_hooks is deliberate: every read-only check that can
# name a specific defect (features, index, steering, org, retrieval, release) runs
# BEFORE the zero-knowledge-loss scan, so a run that broke two things reports the
# specific breakage rather than only "markers unpromoted".

# kb_autocommit_enabled — resolve the single source of truth for KB autocommit
# (feature 260625-kb-autocommit-default, Task 3.1). The resolution precedence
# (per-run env AGENTWARE_KB_AUTOCOMMIT → ~/.agentware/config.env → default ON)
# lives entirely in `scripts/agentware config --kb-autocommit-only`, which prints
# 1/0. The per-run env override (C-3) is honored there. Returns 0 (enabled) iff
# the resolved setting != 0, and DEFAULTS TO ON if the CLI can't be read, matching
# the default-ON contract. The KB git work-tree/upstream preconditions (C-2) are
# enforced INSIDE the kb-git CLI (git_is_work_tree / git_has_upstream → graceful
# rc-0 no-op), so a non-tracked / offline KB stays a clean no-op regardless of the
# setting — the loop never commits into an untracked dir.
kb_autocommit_enabled() {
  [[ ! -x scripts/agentware ]] && return 1
  local resolved
  resolved="$(scripts/agentware config --kb-autocommit-only 2>/dev/null || echo 1)"
  [[ "$resolved" != "0" ]]
}

# kb_push_enabled — independent push gate (feature 260630-kb-push-flag, Task 3).
# Resolution precedence (per-run env AGENTWARE_KB_PUSH → ~/.agentware/config.env →
# default ON) lives entirely in `scripts/agentware config --kb-push-only`, which
# prints 1/0. Returns 0 (enabled) iff the resolved setting != 0, and DEFAULTS TO ON
# if the CLI can't be read, matching the default-ON contract. Push is independent of
# commit but only meaningful when autocommit is on — the loop calls this INSIDE the
# autocommit-enabled branch.
kb_push_enabled() {
  [[ ! -x scripts/agentware ]] && return 1
  local resolved
  resolved="$(scripts/agentware config --kb-push-only 2>/dev/null || echo 1)"
  [[ "$resolved" != "0" ]]
}

run_pre_hooks() {
  if [[ ! -x scripts/agentware ]]; then
    log "[pre-hook] scripts/agentware not found or not executable — skipping toolkit gates."
    return 0
  fi

  log "[pre-hook] scripts/agentware steering lint"
  if ! scripts/agentware steering lint; then
    echo "Error: [pre-hook] steering lint failed — always-loaded steering drifted out of DSF. Fix it first."
    exit 1
  fi

  if [[ "$INITIALIZED" != true ]]; then
    log "[pre-hook] workspace not initialized — skipping index/worklog gates (onboarding runs first)."
    return 0
  fi

  # KB schema migration (feature 260710): bring the KB to the package's schema
  # version before the other gates. Idempotent + best-effort; a restructuring
  # migration surfaces its own suggested-workorder message (non-fatal here).
  log "[pre-hook] scripts/agentware migrate --apply"
  scripts/agentware migrate --apply || true

  log "[pre-hook] scripts/agentware index validate"
  if ! scripts/agentware index validate; then
    echo "Error: [pre-hook] index validation failed. Fix the knowledge index (via scripts/agentware) first."
    exit 1
  fi

  # Org-invariant gate (donor capability) — O1..O13 before any spawn. True no-op on
  # a flat KB, on an unserved verb, or under its kill switch (scripts/loop/orglint.sh).
  run_org_lint_gate pre-hook

  # Plan lint + target-package existence + premise grounding (scripts/loop/gates-plan.sh).
  run_plan_gates

  # KB pull cadence (feature 260625-kb-git-sync, Task 3.1; default-ON flip
  # 260625-kb-autocommit-default, Task 3.1). Gated on the SAME resolved setting as
  # autocommit (env → config → default ON, via kb_autocommit_enabled). Fast-forward
  # the KB from upstream at agent start so the run builds on the latest shared
  # knowledge. Every precondition (work tree / upstream / clean tree) and an offline
  # fetch are graceful skips inside the CLI (rc 0) — pulling NEVER blocks the run.
  if kb_autocommit_enabled; then
    log "[pre-hook] scripts/agentware kb-git pull (KB sync — fast-forward from upstream)"
    scripts/agentware kb-git pull || true
  fi

  # Layer-2 on-use package-staleness nudge (feature 260712-scheduler-updater,
  # Task 10). Initialized-runs-only (after the INITIALIZED early-return above).
  # If the last signed-channel fetch is >24h old, do a READ-ONLY tag fetch + WARN
  # so a scheduler-OFF but active machine cannot stay stale. NEVER applies code;
  # exits 0 on every path; `|| true` at the call site is belt-and-suspenders.
  log "[pre-hook] scripts/agentware update --staleness-check (on-use package freshness)"
  scripts/agentware update --staleness-check || true

  if [[ -f "$DOCS_DIR/worklog.md" ]]; then
    log "[pre-hook] scripts/agentware worklog scan (crash-recovery orphan check)"
    if ! scripts/agentware worklog scan --path "$DOCS_DIR/worklog.md"; then
      log "⚠ [pre-hook] orphaned '> LEARNED:' / '> DECISION:' markers detected from a previous run. The post-hook enforces promotion."
    fi
  fi
}

run_post_hooks() {
  if [[ ! -x scripts/agentware ]]; then
    log "[post-hook] scripts/agentware not found or not executable — skipping toolkit gates."
    return 0
  fi

  # Re-resolve init state — onboarding may have run during the main phase.
  KDIR="$(scripts/agentware config --knowledge-dir-only 2>/dev/null || true)"
  if [[ -z "$KDIR" ]] || [[ ! -f "$KDIR/.initialized" ]]; then
    log "[post-hook] workspace still not initialized — skipping knowledge gates."
    return 0
  fi

  log "[post-hook] scripts/agentware features (regenerate FEATURES.md)"
  if ! scripts/agentware features; then
    echo "Error: [post-hook] FEATURES.md regeneration failed."
    exit 1
  fi

  log "[post-hook] scripts/agentware index validate"
  if ! scripts/agentware index validate; then
    echo "Error: [post-hook] index validation failed after execution — the index drifted."
    exit 1
  fi

  log "[post-hook] scripts/agentware steering lint"
  if ! scripts/agentware steering lint; then
    echo "Error: [post-hook] steering lint failed after execution — steering drifted out of DSF."
    exit 1
  fi

  # Org-invariant gate (donor capability) — a cross-team write or a malformed fleet
  # line fails loud HERE, before the KB commit (scripts/loop/orglint.sh).
  run_org_lint_gate post-hook

  # Retrieval-quality gate (donor capability) — own-gold Recall@5 regression check.
  # No-op without a gold set or under its kill switch (scripts/loop/retrieval.sh).
  run_retrieval_quality_gate

  # Release gate (Tier-A): fires only when the run's diff touched package files.
  # Honors AGENTWARE_DISABLE_RELEASE_GATE kill-switch.
  if [[ -z "${AGENTWARE_DISABLE_RELEASE_GATE:-}" ]]; then
    local _pkg_diff=""
    _pkg_diff="$(git diff --name-only HEAD 2>/dev/null || true)"
    _pkg_diff="${_pkg_diff}$(git diff --name-only --cached 2>/dev/null || true)"
    _pkg_diff="${_pkg_diff}$(git status --porcelain 2>/dev/null | grep -E '^\s*[MADRCU]' | sed 's/^...//' || true)"
    if echo "$_pkg_diff" | grep -qE '(^|\s)(AGENTS\.md|scripts/|\.claude/|steering/|agentware\.sh)'; then
      log "[post-hook] scripts/agentware gate release (package files changed)"
      if ! scripts/agentware gate release; then
        echo "Error: [post-hook] release gate FAILED — package regression detected."
        exit 1
      fi
    fi
  fi

  # Worklog-presence backstop (Task 4, 260710-learning-repair-p1). The scan below
  # only fails on a MISSING worklog (with a misleading "unpromoted markers"
  # message) and PASSES an empty one outright. A loop that RAN a task
  # (LOOP_ITERATIONS_USED >= 1) must have a non-empty worklog — fail loudly and
  # specifically here first. Placed after the not-initialized early-return so an
  # uninitialized run never reaches it; idleness (0 iterations) is exempt.
  if [[ "${LOOP_ITERATIONS_USED:-0}" -ge 1 ]] && [[ ! -s "$DOCS_DIR/worklog.md" ]]; then
    echo "Error: [post-hook] worklog.md is missing or empty at $DOCS_DIR/worklog.md after ${LOOP_ITERATIONS_USED} main iteration(s) ran."
    echo "A real run must record a worklog entry per task (MAIN_PROMPT step 7); zero knowledge loss cannot be verified without it."
    exit 1
  fi

  log "[post-hook] scripts/agentware worklog scan (zero-knowledge-loss gate)"
  if ! scripts/agentware worklog scan --path "$DOCS_DIR/worklog.md"; then
    echo "Error: [post-hook] unpromoted '> LEARNED:' / '> DECISION:' markers remain in $DOCS_DIR/worklog.md."
    echo "Promote each via: scripts/agentware learn ... (LEARNED) or scripts/agentware decide ... (DECISION)."
    echo "Zero knowledge loss is enforced — the feature is NOT complete until every marker is promoted."
    exit 1
  fi
}
