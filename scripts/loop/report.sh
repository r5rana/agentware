#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/report.sh — the adversarial-review CALL-OUT surface (feature 260713,
# Task 6). This tree's capability; the donor has no equivalent.
#
# Kept in its OWN lib rather than folded into review.sh because it is the one thing
# in the gate chain that must NEVER fail the gate: every path in run_selfheal_review
# — clean, blocked, remediated, audit-incomplete, diff-vanished — routes through
# here on its way out, so a failure to WRITE the report must not change the
# ship/block decision that was already made. Six call sites; sourced by agentware.sh
# BEFORE review.sh, which calls it. Function definition only, so sourcing standalone
# under `set -e` exits 0.
#
# Consolidates every round's audit + remediation JSON into
# <feature>/adversarial-review.md, appends a self-closing `> DECISION:` worklog
# call-out per material finding, and files a follow-up workorder per low finding
# (SANITIZED `--context` seam).
emit_review_report() {
  local outcome="${1:-}"
  [[ -x scripts/agentware ]] || return 0
  scripts/agentware review-report \
    --state-dir "$STATE_DIR" \
    --feature-dir "$DOCS_DIR" \
    --worklog "$DOCS_DIR/worklog.md" \
    --feature "$FEATURE" \
    --outcome "$outcome" \
    >>"$STATE_DIR/adversarial-review.log" 2>&1 || true
}
