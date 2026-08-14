#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/review.sh — the SELF-HEALING ADVERSARIAL-REVIEW GATE (feature
# 260713-adversarial-review-gate, Task 5). This tree's capability; the donor has no
# equivalent, so the split must carry it across intact.
#
# Fires at the END of a SELF-EXTENSION run — AFTER the post-phase assessment, BEFORE
# the done-stamp + kb-sync — and runs the bounded AUDIT -> CALL-OUT -> FIX ->
# RE-AUDIT loop. Returns 0 to PROCEED (a clean audit round, a non-self-extension
# diff, or a recorded escape-hatch skip) and NONZERO to BLOCK (a confirmed
# high/medium finding survives all rounds — the caller then SKIPS the done-stamp +
# kb-commit and escalates per R-EXEC-06). All Opus fan-out + fixing happens inside
# `scripts/agentware review`/`remediate`, which spawn the persona subprocesses; this
# bash only orchestrates the round loop + the STOP/escalate branch. Hermetically
# testable: the review/remediate commands resolve `claude` via AGENTWARE_CLI, so a
# fake agent binary drives their exit codes with no real Opus
# (tests/test_loop_selfheal_gate.py).
#
# Sourced by agentware.sh AFTER report.sh (emit_review_report, 6 call sites here).
# Function definition only, so sourcing standalone under `set -e` exits 0.
run_selfheal_review() {
  # Toolkit / init guards — a no-op (PROCEED) when the gate cannot run, mirroring
  # run_post_hooks' early returns so an uninitialized or non-KB run is unaffected.
  # NOTE the DOCS_DIR/KDIR equality guard: run_post_hooks re-resolves KDIR live, so
  # if the resolver ever moves mid-run while DOCS_DIR stays on the old path, this
  # gate silently returns PROCEED and never runs (plan task 4 records this).
  [[ -x scripts/agentware ]] || return 0
  [[ "$INITIALIZED" == true ]] || return 0
  [[ -n "$KDIR" ]] || return 0
  [[ "$DOCS_DIR" == "$KDIR/work/$FEATURE" ]] || return 0
  [[ -f "$DOCS_DIR/plan.md" ]] || return 0
  # Only a run that actually executed a task can have shipped a diff to audit.
  [[ "${LOOP_ITERATIONS_USED:-0}" -ge 1 ]] || return 0

  local plan="$DOCS_DIR/plan.md"
  local worklog="$DOCS_DIR/worklog.md"

  # Self-extension detection — the SAME "package files changed" predicate as
  # run_post_hooks' release gate. Non-self-extension diffs skip the gate entirely.
  # AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE injects the changed-file list for hermetic
  # tests (same input the predicate consumes from git), never used in production.
  local _pkg_diff=""
  if [[ -n "${AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE:-}" ]]; then
    _pkg_diff="$AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE"
  else
    _pkg_diff="$(git diff --name-only HEAD 2>/dev/null || true)"
    _pkg_diff="${_pkg_diff}$(git diff --name-only --cached 2>/dev/null || true)"
    _pkg_diff="${_pkg_diff}$(git status --porcelain 2>/dev/null | grep -E '^\s*[MADRCU]' | sed 's/^...//' || true)"
  fi
  if ! printf '%s\n' "$_pkg_diff" | grep -qE '(^|\s)(AGENTS\.md|scripts/|\.claude/|steering/|agentware\.sh)'; then
    log "[review] non-self-extension diff — adversarial-review gate skipped"
    return 0
  fi

  # Q8 escape hatch — honored ONLY for a TRUTHY value, matching the Python
  # `_review_truthy_env` ({1,true,yes,on}). An empty / false / 0 / no / off value
  # means the operator did NOT ask to skip, so the gate RUNS — a bare `-n`
  # (non-empty) test would BYPASS on `=false` (inverting intent) AND, because the
  # Python side only records the DECISION for a truthy value, ship silently with a
  # log line falsely claiming a decision was recorded. The two truthy checks MUST agree.
  local _skip_lc _skip_raw
  _skip_raw="${AGENTWARE_SKIP_ADVERSARIAL_REVIEW:-}"
  # Trim ONLY leading/trailing whitespace (mirror Python str.strip()); do NOT
  # collapse INTERNAL whitespace — `tr -d '[:space:]'` would fold "o n" -> "on"
  # (bash-truthy) while Python keeps "o n" (non-truthy), re-opening a bypass with
  # no recorded DECISION. The two truthy checks MUST see the same normalized token.
  _skip_raw="${_skip_raw#"${_skip_raw%%[![:space:]]*}"}"
  _skip_raw="${_skip_raw%"${_skip_raw##*[![:space:]]}"}"
  _skip_lc="$(printf '%s' "$_skip_raw" | tr '[:upper:]' '[:lower:]')"
  case "$_skip_lc" in
    1|true|yes|on)
      log "⚠ [review] AGENTWARE_SKIP_ADVERSARIAL_REVIEW is truthy — recording a > DECISION: skip and BYPASSING the gate (never silent)."
      scripts/agentware review --adversarial --worklog "$worklog" >/dev/null 2>&1 || true
      return 0 ;;
  esac

  # Resolve the bounded round count + diff range ONCE.
  # SINGLE-PASS default (operator-directed 2026-07-24): ONE audit per plan, then
  # FIX + ship-iff-self-verified — NOT an audit->fix->re-audit LOOP. The loop
  # failed to converge on large diffs (each remediation grew the diff, each fresh
  # Opus fan-out found new marginalia) and burned many Opus rounds. Override to
  # re-audit the fix N times with AGENTWARE_REVIEW_MAX_ROUNDS=<N>.
  local max_rounds="${AGENTWARE_REVIEW_MAX_ROUNDS:-1}"
  [[ "$max_rounds" =~ ^[0-9]+$ ]] && (( max_rounds >= 1 )) || max_rounds=1
  local diff_range="${AGENTWARE_REVIEW_DIFF_RANGE:-HEAD}"

  # Remediation round 9 (novel-input): capture the NON-EMPTY expectation the
  # gate is about to audit under. The rounds below re-resolve `git diff
  # $diff_range` FRESH each time, so a working tree that becomes clean MID-RUN
  # (operator commit/stash during a long remediation loop) would make a later
  # round resolve an EMPTY diff, audit NOTHING, and stamp the self-extension
  # CLEAN — shipping earlier rounds' confirmed findings with zero reviewers
  # having seen the final diff. Resolve the range ONCE here; a clean round may
  # only PROCEED if a non-empty entry diff is STILL non-empty (cross-checked
  # in the _rrc==0 branch). An entry range that resolves empty (or fails to
  # resolve — cmd_review fails closed on that itself) arms no expectation.
  local _entry_diff_files=""
  _entry_diff_files="$(git diff --name-only "$diff_range" -- 2>/dev/null || true)"

  log "[review] self-extension diff — running the adversarial-review gate (max_rounds=$max_rounds, diff-range=$diff_range)"
  local round=1
  while (( round <= max_rounds )); do
    local review_json="$STATE_DIR/adversarial-review-round-$round.json"
    log "[review] round $round/$max_rounds — scripts/agentware review --adversarial (Opus/max fan-out)"
    # review exit: 0 = CLEAN, 1 = confirmed blocking finding(s), >=3 = the audit
    # itself could NOT run. Exit >=3 covers TWO distinct failure classes the
    # console diagnosis below must not conflate: (a) the DIFF SOURCE failed
    # (unreadable --diff-file / unresolvable --diff-range — gc'd base ref,
    # shallow clone, git unavailable; cmd_review fails CLOSED before any
    # reviewer spawns), and (b) the agent runtime is unreachable / a dimension
    # produced no output. Per-round stderr is captured into its OWN file so the
    # class can be read back deterministically (then appended to the main log).
    # Capture the code without tripping `set -e`.
    local _rrc=0
    local review_errlog="$STATE_DIR/adversarial-review-round-$round.err"
    scripts/agentware review --adversarial --diff-range "$diff_range" \
         --acceptance-file "$plan" --worklog "$worklog" --format json \
         > "$review_json" 2>"$review_errlog" || _rrc=$?
    cat "$review_errlog" >> "$STATE_DIR/adversarial-review.log" 2>/dev/null || true
    if (( _rrc == 0 )); then
      # Remediation round 9 (novel-input): a CLEAN verdict only covers the
      # diff the reviewers actually SAW. If the gate fired on a NON-empty
      # entry diff but this round's range now resolves EMPTY (the working
      # tree was committed/stashed while the gate ran), the reviewers audited
      # NOTHING — proceeding would stamp the (now-invisible) self-extension
      # GREEN, including earlier rounds' confirmed findings whose remediation
      # was never re-audited. Fail CLOSED instead.
      if [[ -n "$_entry_diff_files" ]]; then
        local _live_diff_files=""
        _live_diff_files="$(git diff --name-only "$diff_range" -- 2>/dev/null || true)"
        if [[ -z "$_live_diff_files" ]]; then
          log "✖ [review] round $round: the SELF-EXTENSION DIFF VANISHED mid-run — the gate fired on a NON-empty '$diff_range' diff, but this round resolved an EMPTY one (working tree committed/stashed while the gate ran?), so this round's clean verdict covers NOTHING and does NOT clear the original diff. BLOCKING (fail-closed, nothing-skipped). Restore or re-point the diff (e.g. AGENTWARE_REVIEW_DIFF_RANGE=<base>..HEAD over the new commit) so the reviewers can actually see it, then re-run."
          emit_review_report "blocked-diff-vanished"
          return 1
        fi
      fi
      log "[review] round $round CLEAN — no confirmed high/medium findings; PROCEEDING to done-stamp"
      # CALL OUT (task 6): consolidate the report, surface material findings in the
      # worklog (self-closing markers), and file workorders for low findings.
      emit_review_report "clean-round-$round"
      return 0
    fi
    if (( _rrc >= 3 )); then
      # FAIL-CLOSED: the adversarial audit could not actually RUN — do NOT waste
      # remediation rounds (there are no findings to fix) and NEVER silently
      # proceed. Diagnose the REAL class from this round's captured stderr
      # (remediation round 8): a failed INPUT SOURCE (diff or acceptance
      # criteria) means NO reviewer ever ran over the intended input, so the
      # console must direct the operator to FIX the source and must NEVER
      # suggest the skip bypass — recording a skip there would ship the
      # self-extension with ZERO reviewers having seen the diff, the exact
      # fail-open this gate exists to block.
      #
      # ANCHORED classifier (remediation round 9): the errlog ALSO carries
      # RELAYED agent stdout/stderr snippets (the ⚠️-prefixed WARN diagnostics)
      # whose untrusted-data fence keeps words intact — so an UN-anchored grep
      # would let a soft-failing reviewer that merely QUOTES the gate's own
      # error strings (highly likely when the diff under audit touches
      # cmd_review, or when a hostile diff steers reviewer prose) misclassify
      # a runtime-class failure as a source failure, misdirecting the operator
      # to "fix" a healthy diff source and suppressing the sanctioned
      # recorded-skip recovery for class (b). Only cmd_review's OWN
      # operational lines start a physical line with 'review: ' — relayed
      # payloads are whitespace-collapsed + control-char-neutralized inside
      # one WARN line and can never start a new line — so the class predicate
      # MUST be line-anchored. NEVER consume the fenced payload as a predicate
      # (R-SEC-02).
      if grep -qE '^review: cannot (resolve --diff-range|read --diff-file)' "$review_errlog" 2>/dev/null; then
        log "✖ [review] round $round: the adversarial audit COULD NOT RUN — the DIFF SOURCE failed: --diff-range '$diff_range' is unresolvable (gc'd/absent base ref, shallow clone, or git unavailable) or the --diff-file is unreadable, so NO reviewer ever saw the diff. BLOCKING (fail-closed, nothing-skipped). Fix the diff source (fetch/unshallow the base ref or correct AGENTWARE_REVIEW_DIFF_RANGE) and re-run — do NOT bypass: the diff has NOT been audited. Details: $review_errlog"
      elif grep -qE '^review: cannot read --acceptance-file' "$review_errlog" 2>/dev/null; then
        # Remediation round 9: same fail-closed source class as the diff —
        # the plan's acceptance criteria are the SPEC half of the review
        # contract, and cmd_review now exits 3 (before any reviewer spawns)
        # when the requested criteria source cannot be read.
        log "✖ [review] round $round: the adversarial audit COULD NOT RUN — the ACCEPTANCE-CRITERIA SOURCE failed: --acceptance-file '$plan' is unreadable (deleted/renamed by a concurrent run?), so the diff could not be checked against the plan's criteria and NO reviewer ran. BLOCKING (fail-closed, nothing-skipped). Restore the plan file and re-run — do NOT bypass: the diff has NOT been audited. Details: $review_errlog"
      else
        log "✖ [review] round $round: the adversarial audit COULD NOT RUN (agent runtime unreachable / a dimension produced no output — per-agent reasons in $review_errlog). BLOCKING (fail-closed, nothing-skipped). Fix agent access or set AGENTWARE_SKIP_ADVERSARIAL_REVIEW=1 to bypass with a recorded DECISION."
      fi
      emit_review_report "blocked-audit-incomplete"
      return 1
    fi
    # _rrc == 1: confirmed blocking finding(s) — CALL OUT (task 6 fleshes the
    # report) and AUTO-REMEDIATE. `remediate` applies EVERY confirmed finding and
    # runs its OWN regression tests + gate chain, exiting 0 ONLY when all_fixed
    # AND that chain is green — so a zero exit is a SELF-VERIFIED fix and a
    # non-zero exit is a real, un-healed regression.
    log "⚠ [review] round $round: confirmed high/medium finding(s) — CALLING OUT + auto-remediating (see $review_json)"
    local remediate_json="$STATE_DIR/adversarial-remediate-round-$round.json"
    local _remediate_rc=0
    scripts/agentware remediate --findings-file "$review_json" --diff-range "$diff_range" \
         --acceptance-file "$plan" --format json \
         > "$remediate_json" 2>>"$STATE_DIR/adversarial-review.log" || _remediate_rc=$?
    if (( _remediate_rc != 0 )); then
      # A real regression the fixer could NOT make green — BLOCK now (never ship a
      # broken fix; do not spend a re-audit on a diff the fixer already failed).
      log "✖ [review] round $round: remediation could NOT fully fix every finding (its regression tests / gate chain did not pass) — a real regression survives. BLOCKING (fail-closed)."
      emit_review_report "blocked"
      return 1
    fi
    if (( round >= max_rounds )); then
      # SINGLE-PASS SHIP: the fix self-verified (all findings fixed + gate chain
      # green) and no re-audit round remains. PROCEED — the deterministic ship
      # gates still run after this (post-hooks: features/index/steering-lint/
      # gate-release/worklog-scan, then the plan done-stamp, then kb-sync), so
      # "ship iff ALL conditions met" is still enforced; what is intentionally
      # dropped is only the Opus CONFIRMING re-audit. Set
      # AGENTWARE_REVIEW_MAX_ROUNDS=<N> to re-audit the fix instead of shipping it.
      log "✓ [review] round $round: remediation applied ALL confirmed findings and its regression tests + gate chain PASSED — single-pass policy: PROCEEDING to ship (no confirming re-audit). Override with AGENTWARE_REVIEW_MAX_ROUNDS=<N>."
      emit_review_report "remediated-shipped-round-$round"
      return 0
    fi
    # More rounds allowed (operator raised max_rounds): RE-AUDIT the fix with a
    # fresh fan-out to CONFIRM it resolved the findings without introducing new ones.
    log "[review] round $round: remediation applied all confirmed findings — RE-AUDITING to confirm (round $((round + 1))/$max_rounds)"
    round=$((round + 1))
  done

  # Defensive backstop: with the decide-on-final-round logic above, every path
  # inside the loop returns explicitly, so this is normally unreachable. Falling
  # through would mean the loop exited without a decision — fail CLOSED.
  log "✖ [review] adversarial-review gate BLOCKED: the round loop exited without a ship/block decision (unexpected) — failing closed."
  emit_review_report "blocked"
  return 1
}
