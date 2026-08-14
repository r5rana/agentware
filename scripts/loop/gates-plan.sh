#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/gates-plan.sh — the three plan-shaped pre-hook gates: structural
# lint, target-package existence, and premise grounding.
#
# Split out of run_pre_hooks so hooks.sh stays inside the 250-line ceiling with room
# for the org-lint and retrieval-quality call sites. All three run only when plan.md
# exists (onboarding-only runs skip cleanly). Function definitions only, so sourcing
# standalone under `set -e` exits 0.

# run_plan_gates — called by run_pre_hooks with the workspace already initialized.
# Returns 0 to proceed; exits 1 on any gate failure.
run_plan_gates() {
  [[ -f "$DOCS_DIR/plan.md" ]] || return 0

  # Plan-format gate (feature 260625-plan-lint-gate, Task 5). Assert the plan's
  # STRUCTURAL contract (markers, numbering, per-task verify, sections, promise,
  # autonomy) BEFORE the pre phase so a malformed plan fails loudly and specifically
  # instead of silently no-op'ing at the zero-markers guard. --strict makes the R9
  # autonomy rule a hard failure so a non-autonomous plan is caught before the run.
  log "[pre-hook] scripts/agentware plan lint --strict"
  if ! scripts/agentware plan lint --path "$DOCS_DIR/plan.md" --strict; then
    echo "Error: [pre-hook] plan lint failed — plan.md violates the structural contract (markers must be '- ⬜ **N** …'). Fix it (see the rule output above) before the run."
    exit 1
  fi

  # Target-packages existence gate (R11 contract enforcement).
  # Parse declared target dirs from the plan and assert each exists on disk.
  # Hard-abort before phase 1 when any is missing (pre_hook_abort outcome).
  # Kill-switch: AGENTWARE_DISABLE_TARGET_GATE=1 skips the check entirely.
  if [[ -n "${AGENTWARE_DISABLE_TARGET_GATE:-}" ]]; then
    log "[pre-hook] AGENTWARE_DISABLE_TARGET_GATE set — skipping target-packages existence gate."
  else
    local target_dirs
    target_dirs=$(scripts/agentware plan target-dirs --path "$DOCS_DIR/plan.md" 2>/dev/null || true)
    if [[ -n "$target_dirs" ]]; then
      local missing=()
      while IFS= read -r dir; do
        if [[ ! -d "$dir" ]]; then
          missing+=("$dir")
        fi
      done <<< "$target_dirs"
      if [[ ${#missing[@]} -gt 0 ]]; then
        echo "Error: [pre-hook] target-packages existence gate FAILED."
        echo "  The plan declares target directories that do NOT exist:"
        for m in "${missing[@]}"; do
          echo "    ✗ $m"
          # Suggest nearby candidates
          local pkg_name
          pkg_name=$(basename "$m")
          local candidates
          candidates=$(find ~/workspace -maxdepth 5 -name "$pkg_name" -type d 2>/dev/null | head -5)
          if [[ -n "$candidates" ]]; then
            echo "      Nearby candidates:"
            while IFS= read -r c; do
              echo "        → $c"
            done <<< "$candidates"
          fi
        done
        echo "  Fix the plan's '## Target packages' section or cd to the correct workspace."
        exit 1
      fi
    fi
  fi

  # Premise-grounding gate (feature 260717-premise-grounding-gate). Re-verify the
  # plan's declared `## Premises` against LIVE state and HARD-ABORT before phase 1
  # on a REPRODUCED falsification — converting a multi-hour wrong-premise run into
  # a ~10-second stop (the 260714-miner RCA's highest-leverage fix). The verb is
  # read-only + deterministic; the halt authority lives ENTIRELY here in the
  # deterministic pre-hook, never the LLM pre-phase — so PRE_PROMPT is left
  # byte-identical (RCA F3 fix). Distinct terminal outcome premise_falsified (D3).
  # Kill-switch AGENTWARE_DISABLE_PREMISE_GATE (mirrors AGENTWARE_DISABLE_TARGET_
  # GATE): a bypass RECORDS a > DECISION: and logs loudly — never a silent skip.
  if [[ -n "${AGENTWARE_DISABLE_PREMISE_GATE:-}" ]]; then
    log "⚠ [pre-hook] AGENTWARE_DISABLE_PREMISE_GATE set — BYPASSING the premise-grounding gate (never silent); recording a > DECISION:."
    # Record the audit trail unconditionally. `>>` CREATES worklog.md if absent
    # (the common first-bypass case, when `plan new` has written only plan.md) —
    # the earlier `-f` guard silently dropped the mandated > DECISION: there.
    _pg_wl="$DOCS_DIR/worklog.md"
    mkdir -p "$DOCS_DIR" 2>/dev/null || true
    printf '\n> DECISION: premise-grounding gate BYPASSED via AGENTWARE_DISABLE_PREMISE_GATE (%s). Options: run the gate (default) | operator bypass (chosen). Rationale: explicit operator override, recorded per R-AUTO-03. Reversibility: high — unset the env var and re-run.\n' \
      "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$_pg_wl" 2>/dev/null || true
  else
    log "[pre-hook] scripts/agentware plan verify-premises --strict"
    # Capture output + exit code WITHOUT tripping `set -e` (an `if` consumes the
    # non-zero; a bare `_x=$(cmd)` assignment would abort the script instead).
    if _pg_out="$(scripts/agentware plan verify-premises --path "$DOCS_DIR/plan.md" --strict 2>&1)"; then
      _pg_rc=0
    else
      _pg_rc=$?
    fi
    if [[ $_pg_rc -ne 0 ]]; then
      echo "Error: [pre-hook] premise-grounding gate FAILED — a load-bearing premise does not hold against live state."
      echo "$_pg_out"
      echo "  This plan rests on a premise its own ## Premises block could not reproduce against live state."
      echo "  Re-ground the falsified premise in the plan, or set AGENTWARE_DISABLE_PREMISE_GATE=1 to override (records a > DECISION:)."
      LOOP_OUTCOME="premise_falsified"
      notify "premise falsified (pre-hook halt)"
      exit 1
    fi
  fi
}
