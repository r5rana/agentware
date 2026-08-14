#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/orglint.sh — the ORG-INVARIANT gate (donor capability, plan task 33).
#
# A NEW lib rather than another block inside hooks.sh: the plan requires the
# re-injected wiring to land in new files rather than the already-large hooks lib,
# and this gate has two call sites (pre-hook and post-hook) that must stay in
# lockstep, which is exactly what a shared function buys.
#
# Enforces the org invariants O1..O13 (origin/team stamping, id collisions, the team
# registry, shared content, fleet-event schema, cross-team isolation) BEFORE any
# spawn and again AFTER the run, so a write that violated one fails loud before the
# KB commit rather than after it.
#
# ---- THREE NO-OPS, IN PRECEDENCE ORDER — each one load-bearing ----
#
#  1. KILL SWITCH. AGENTWARE_DISABLE_ORG_LINT skips the gate, logged, never silent.
#
#  2. FLAT KB. With AGENTWARE_ORG_ROOT unset the org keys do not exist and there is
#     nothing to lint, so the gate returns without spawning a subprocess and a
#     single-operator install stays byte-identical to today (R-ORG-04).
#
#  3. VERB NOT SERVED. `org` is one of the six DONOR-ONLY verbs; on this branch it
#     is not in the strangler allowlist yet, so `scripts/agentware org lint` exits
#     non-zero with `invalid choice: 'org'`. The donor's own copy of this gate calls
#     it UNCONDITIONALLY behind `if ! …; then exit 1`, so porting that verbatim
#     would turn an argparse error into a pre-hook abort and HARD-BLOCK every run on
#     this tree until the port wave lands. The probe below distinguishes "the verb
#     rejected the tree" (a real violation — fail loud) from "the verb does not
#     exist yet" (nothing to enforce — no-op), which is the same class of mistake
#     the plan flags for the donor's O12 outcome check.
#
# Sourced by agentware.sh before hooks.sh (which calls it). Function definitions
# only, so sourcing standalone under `set -e` exits 0.

# _org_lint_available — 0 when `org lint` resolves in THIS build, 1 otherwise.
# Memoized in _ORG_LINT_AVAILABLE so the two call sites cost one probe per run.
# `--help` on a served verb exits 0 without doing any work; on an unserved one
# argparse exits non-zero. Output is discarded either way.
_org_lint_available() {
  if [[ -z "${_ORG_LINT_AVAILABLE:-}" ]]; then
    if scripts/agentware org lint --help >/dev/null 2>&1; then
      _ORG_LINT_AVAILABLE=yes
    else
      _ORG_LINT_AVAILABLE=no
    fi
  fi
  [[ "$_ORG_LINT_AVAILABLE" == yes ]]
}

# run_org_lint_gate <pre-hook|post-hook>
# Returns 0 to proceed; exits 1 on a real org invariant violation.
run_org_lint_gate() {
  local phase="${1:-hook}"

  [[ -x scripts/agentware ]] || return 0

  if [[ -n "${AGENTWARE_DISABLE_ORG_LINT:-}" ]]; then
    log "[$phase] AGENTWARE_DISABLE_ORG_LINT set — skipping the org-lint gate."
    return 0
  fi

  # Flat single-operator install: no org keys, nothing to lint (R-ORG-04).
  [[ -n "${AGENTWARE_ORG_ROOT:-}" ]] || return 0

  if ! _org_lint_available; then
    log "[$phase] org lint not served by this build (donor-only verb, not yet ported) — org-lint gate skipped."
    return 0
  fi

  log "[$phase] scripts/agentware org lint"
  if ! scripts/agentware org lint; then
    echo "Error: [$phase] org lint failed — an org invariant (O1..O13) is violated (see above)."
    if [[ "$phase" == post-hook ]]; then
      echo "An org invariant drifted DURING the run; nothing is committed until it is fixed."
    else
      echo "Fix it before the run."
    fi
    exit 1
  fi
}
