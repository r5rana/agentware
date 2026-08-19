#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/markers.sh — plan.md task-marker readers and the zero-knowledge-loss
# check that gate loop completion.
#
# Sourced by agentware.sh; function definitions only, so sourcing standalone under
# `set -e` exits 0. `open_markers` has 10 call sites and is the single ⬜|🟡 grep in
# the tree — see the byte-identity contract on it below.

# Count remaining open task markers (⬜ not-started, 🟡 in-progress) in plan.md.
# CONTRACT: the grep -cE pattern below is the canonical task-marker regex. It MUST
# stay byte-identical to PLAN_TASK_MARKER_RE in scripts/agentware (the linter derives
# its Python form from those same POSIX-ERE bytes). The byte-identity is pinned by the
# contract test in tests/unit/ops/test_plan_lint.py (locates this line by the `⬜|🟡` token).
open_markers() {
  local n
  n=$(grep -cE '^[[:space:]]*-[[:space:]]*(⬜|🟡)[[:space:]]*\*\*[0-9]' "$DOCS_DIR/plan.md" 2>/dev/null || true)
  echo "${n:-0}"
}

# snapshot_task_states — print "<task_id> <state>" lines for EVERY plan.md task
# marker, where state ∈ {open(⬜), started(🟡), done(✅)}. Reuses the SAME canonical
# marker shape as open_markers / the linter (a `- <emoji> **<id>**` line) so the id
# set stays consistent. Pure read-only; empty output on an unreadable plan.
snapshot_task_states() {
  grep -E '^[[:space:]]*-[[:space:]]*(⬜|🟡|✅)[[:space:]]*\*\*[0-9]' "$DOCS_DIR/plan.md" 2>/dev/null \
    | sed -E 's/^[[:space:]]*-[[:space:]]*(⬜|🟡|✅)[[:space:]]*\*\*([0-9][0-9.]*)\*\*.*/\2 \1/' \
    | sed -e 's/ ⬜$/ open/' -e 's/ 🟡$/ started/' -e 's/ ✅$/ done/' \
    || true
}

# Returns 0 if every '> LEARNED:' marker in the worklog is promoted (zero knowledge
# loss), non-zero if unpromoted markers remain. This REUSES the deterministic
# `worklog scan` gate — it does NOT reimplement detection. No-op PASS when the
# toolkit/worklog is absent or the workspace is not yet initialized (init state is
# re-resolved live, since onboarding may have run during the main phase).
#
# NOTE the `-x` guard: it FAILS OPEN by design (an absent toolkit cannot gate), which
# is why plan task 25 makes `chmod +x scripts/agentware` an invariant — a 0644
# entrypoint turns this gate into an unconditional PASS with nothing turning red.
learnings_promoted() {
  [[ -x scripts/agentware ]] || return 0
  local kdir
  kdir="$(scripts/agentware config --knowledge-dir-only 2>/dev/null || true)"
  [[ -n "$kdir" && -f "$kdir/.initialized" ]] || return 0
  # Worklog-presence gate (Task 4, 260710-learning-repair-p1). A loop that
  # ACTUALLY RAN a task this session (LOOP_ITERATIONS_USED >= 1) MUST have left a
  # non-empty worklog (MAIN_PROMPT step 7) — the zero-knowledge-loss scan below
  # has nothing to gate on otherwise, and a silent no-op PASS is how capture
  # collapsed unnoticed. `-s` is false for BOTH a missing and an empty file.
  # Idleness stays valid: with 0 iterations (onboarding-only run) an absent
  # worklog keeps the lenient path.
  if [[ "${LOOP_ITERATIONS_USED:-0}" -ge 1 ]]; then
    if [[ ! -s "$DOCS_DIR/worklog.md" ]]; then
      echo "Error: worklog.md is missing or empty at $DOCS_DIR/worklog.md after ${LOOP_ITERATIONS_USED} main iteration(s) ran — a real run must leave a non-empty worklog (MAIN_PROMPT step 7)." >&2
      return 1
    fi
  else
    [[ -f "$DOCS_DIR/worklog.md" ]] || return 0
  fi
  scripts/agentware worklog scan --path "$DOCS_DIR/worklog.md" >/dev/null 2>&1
}
