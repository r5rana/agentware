#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/kbsync.sh — commit + push the knowledge base as the LAST step of the
# loop, AFTER the post phase (feature 260625-autocommit-post-phase-fix, Phase 1).
#
# This was previously the tail of run_post_hooks, which executes BEFORE
# `run_phase "post"`; the post phase writes MORE KB files (assessment.md,
# benchmarks/history.jsonl + SCORECARD.md rows, .loop/ state), so committing inside
# run_post_hooks always left an uncommitted tail. Running it here captures the
# assessment + its ledger rows in the SAME commit, on BOTH the post-ran and
# --skip-post paths (C-1/C-2).
#
# Sourced by agentware.sh AFTER hooks.sh (kb_autocommit_enabled / kb_push_enabled)
# and spawn.sh (kb_sync_push calls run_agent). Function definitions only, so
# sourcing standalone under `set -e` exits 0.
#
# KB commit discipline (feature 260625-kb-git-sync, Task 2.1; default-ON flip
# 260625-kb-autocommit-default, Task 3.1). ON BY DEFAULT — gated on the resolved
# setting (env → config → default ON; operator consent captured at onboarding
# satisfies R-GIT-01, and the per-run env override C-3 is the escape hatch). The
# zero-knowledge-loss gate in run_post_hooks already passed, so nothing commits
# until every LEARNED: marker is promoted and the index is valid. Scope is the KB
# repo ONLY (C-1) — the CLI refuses to stage the project/package. No-ops on a
# non-tracked KB or no upstream (C-2) or a clean tree. logs/ and .loop/ are
# gitignored (C-3/C-4) so transcripts and ephemeral loop state are never staged.
# The commit-message scope tag is the feature name with its leading YYMMDD- date
# stripped.

# emit_fleet_line — write+STAGE the ONE committed per-USER terminal event (R-ORG-06).
#
# The local twin is emit_terminal_metric (outcome.sh), which appends the same
# terminal facts to $METRICS_LOG. That log is gitignored and machine-local, so it
# answers "how did MY runs go" and can never answer the question fleet
# observability exists for — "how is the fleet going" — which needs the event to
# survive into the shared history other clones can read. Hence a SECOND sink that
# is committed, sharded per USER (never per team) so two engineers' lines cannot
# textually collide on rebase, and stamped with steering_sha as the causal join key.
#
# Best-effort by construction: an unserved verb, a flat KB or any failure returns 0.
# Telemetry may never be the reason a run fails. `fleet-emit` itself no-ops on a
# non-org knowledge dir, so a single-operator install is byte-identical (R-ORG-04);
# the --help probe is what keeps this a no-op on a build that never ported the verb.
#
# Does NOT commit — the caller commits, so the line rides an EXISTING commit rather
# than making one of its own on the healthy path.
emit_fleet_line() {
  [[ -x scripts/agentware ]] || return 0
  scripts/agentware fleet-emit --help >/dev/null 2>&1 || return 0
  local done_n open_n total promise
  open_n="$(open_markers 2>/dev/null || echo 0)"
  done_n=$(grep -cE '^[[:space:]]*-[[:space:]]*✅[[:space:]]*\*\*[0-9]' "$DOCS_DIR/plan.md" 2>/dev/null || echo 0)
  total=$(( ${open_n:-0} + ${done_n:-0} ))
  if [[ -f "$STATE_DIR/.done" ]] || [[ "${open_n:-1}" -eq 0 ]]; then
    promise="signalled"
  else
    promise="pending"
  fi
  scripts/agentware fleet-emit \
    --outcome "$LOOP_OUTCOME" --feature "$FEATURE_NAME" \
    --ts "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --iterations-used "${LOOP_ITERATIONS_USED:-0}" --max "${MAX_ITERATIONS:-0}" \
    --tasks-total "${total:-0}" --tasks-done "${done_n:-0}" \
    --promise-status "$promise" >/dev/null 2>&1 || true
}

# commit_fleet_sink_only — micro-commit JUST the staged fleet line on a FAILURE
# exit path. Called from loop_on_exit (outcome.sh) for the outcomes that exit
# BEFORE run_kb_sync ever runs.
#
# Those are exactly the outcomes fleet observability is FOR. A run that completes
# gets its line for free on the main KB commit; a run that hit the iteration cap or
# failed its post-hook exits first, so without this path the sink would record only
# successes and every aggregate over it would be a survivorship-biased flat line
# that looks healthy. It deliberately does NOT re-run the worklog-scan gate: a
# failed run legitimately has open markers, and blocking its terminal event on the
# gate it just failed is how the failure signal would be lost.
#
# Path-scoped to fleet/ so no other pending KB edit rides this micro-commit, and
# org-KB-only (a flat KB stages nothing, so the diff --cached probe returns early).
commit_fleet_sink_only() {
  [[ -x scripts/agentware ]] || return 0
  local kdir
  kdir="$(scripts/agentware config --knowledge-dir-only 2>/dev/null || true)"
  [[ -z "$kdir" || ! -f "$kdir/.initialized" ]] && return 0
  emit_fleet_line
  if git -C "$kdir" diff --cached --quiet -- "fleet/" 2>/dev/null; then
    return 0  # nothing staged (flat KB / emit no-op)
  fi
  git -C "$kdir" -c user.name="${GIT_AUTHOR_NAME:-agentware}" \
      -c user.email="${GIT_AUTHOR_EMAIL:-agentware@local}" \
      commit -q -m "chore(fleet): terminal $LOOP_OUTCOME for $FEATURE_NAME" \
      -- "fleet/" >/dev/null 2>&1 || true
  FLEET_SINK_COMMITTED=true
}

run_kb_sync() {
  if [[ ! -x scripts/agentware ]]; then
    return 0
  fi

  # Re-resolve init state — onboarding may have run during the main phase.
  local kdir
  kdir="$(scripts/agentware config --knowledge-dir-only 2>/dev/null || true)"
  if [[ -z "$kdir" ]] || [[ ! -f "$kdir/.initialized" ]]; then
    return 0
  fi

  if kb_autocommit_enabled; then
    # Promote-before-commit (feature 260625-autocommit-post-phase-fix, Task 1.2).
    # The post phase (run AFTER run_post_hooks' gate) may have appended new
    # `> LEARNED:` markers to the worklog (e.g. the self-assessment's findings).
    # Re-run the zero-knowledge-loss gate HERE so any post-phase marker is caught
    # and promoted before it gets committed. Fast no-op PASS when the worklog is
    # clean — it delegates to the same `worklog scan` detector as run_post_hooks.
    log "[kb-sync] scripts/agentware worklog scan (re-check — promote-before-commit)"
    if ! scripts/agentware worklog scan --path "$DOCS_DIR/worklog.md"; then
      echo "Error: [kb-sync] unpromoted '> LEARNED:' / '> DECISION:' markers remain in $DOCS_DIR/worklog.md"
      echo "(added after the post-hook gate — likely by the post phase)."
      echo "Promote each via: scripts/agentware learn ... (LEARNED) or scripts/agentware decide ... (DECISION)."
      echo "Nothing was committed — zero knowledge loss is enforced before the KB commit."
      exit 1
    fi

    local kb_tag="${FEATURE#[0-9][0-9][0-9][0-9][0-9][0-9]-}"

    # Meaningful commit subject (feature 260625-autocommit-message-fix, Task 1.1).
    # Instead of the generic "sync <dirs> (N files)" boilerplate, describe WHAT
    # feature was worked on using the plan's one-line title. Deterministic — read
    # a file, no LLM/network (preserves the moat). Robust fallback (C-2): if no
    # plan title is available, omit --message so the CLI builds its
    # changed-knowledge-entry / dir-list summary, and keep the default `chore`.
    local kb_msg_args=()
    local plan_title=""
    if [[ -f "$DOCS_DIR/plan.md" ]]; then
      # First "# Plan: <title>" line (any leading #'s); strip the marker, trim
      # surrounding whitespace, and truncate so the subject stays one sane line.
      plan_title="$(sed -n 's/^#*[[:space:]]*Plan:[[:space:]]*//p' "$DOCS_DIR/plan.md" | head -1)"
      plan_title="$(printf '%s' "$plan_title" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
      if [[ ${#plan_title} -gt 72 ]]; then
        plan_title="$(printf '%s' "${plan_title:0:69}" | sed -e 's/[[:space:]]*$//')..."
      fi
    fi
    if [[ -n "$plan_title" ]]; then
      kb_msg_args=(--type feat --message "$plan_title")
    fi

    # Committed fleet sink (R-ORG-06): write+STAGE the terminal event HERE, before
    # the commit, so it rides the SAME commit. NOT in the EXIT trap — that fires
    # after the push and would leave the tree permanently dirty. Mark it committed
    # so the trap's failure-path fallback cannot emit a second line for this run.
    emit_fleet_line
    FLEET_SINK_COMMITTED=true

    log "[kb-sync] scripts/agentware kb-git commit (KB autocommit, tag: $kb_tag)"
    if ! scripts/agentware kb-git commit --tag "$kb_tag" "${kb_msg_args[@]}"; then
      echo "Error: [kb-sync] kb-git commit failed — see the message above."
      exit 1
    fi

    # KB push (feature 260625-kb-git-sync, Phase 6). Runs AFTER the commit, behind
    # the SAME opt-in. Deterministic-first / agent-last with the nothing-lost gate
    # + bounded re-push retry inside the CLI: derived-file conflicts resolve with
    # no agent (C-1); a same-entry prose conflict pauses for the MERGE_PROMPT; a
    # lossy or invalid merge is REJECTED before it reaches the remote (C-2). A
    # non-tracked / offline KB is a graceful no-op (C-4) — kb_sync_push returns 0.
    # Push is independently gated on AGENTWARE_KB_PUSH (feature 260630-kb-push-flag,
    # Task 3) — commit-on/push-off commits locally without publishing.
    if kb_push_enabled; then
      log "[kb-sync] kb_sync_push (push KB upstream — deterministic merge + nothing-lost gate)"
      if ! kb_sync_push; then
        echo "Error: [kb-sync] KB push failed — see the message above. Nothing was"
        echo "pushed (no silent loss); resolve the conflict/race and re-run."
        exit 1
      fi
    else
      log "[kb-sync] KB push disabled (AGENTWARE_KB_PUSH=0) — committed locally, NOT pushed; push yourself with: git -C $kdir push"
    fi
  else
    log "[kb-sync] KB autocommit disabled (resolved AGENTWARE_KB_AUTOCOMMIT=0 — config opt-out or per-run env override) — leaving the KB uncommitted/unpushed."
  fi
}

# kb_sync_push — push the KB to upstream with the deterministic-first, agent-last
# conflict policy (feature 260625-kb-git-sync, Phase 5). Derived-file conflicts are
# resolved by the CLI with NO agent (C-1). A same-entry PROSE conflict pauses the
# rebase (rc 3); we reconcile ONLY the conflicted entry files by spawning
# agentware-execution with MERGE_PROMPT (no new agent), then run
# `kb-git merge-continue` to rebuild the derived files and finish the push.
# Returns the CLI's exit code. The nothing-lost ID-superset gate + bounded
# re-push retry live inside the CLI (`kb-git push` / `merge-continue`, Phase 6).
kb_sync_push() {
  local files rc
  # Capture stdout (= conflicted entry paths on a prose pause); stderr/notes flow
  # straight to the terminal.
  files=$(scripts/agentware kb-git push --on-prose-conflict pause); rc=$?
  [[ $rc -eq 0 ]] && return 0          # pushed or graceful skip (C-4)
  [[ $rc -ne 3 ]] && return $rc        # fail loud (non-prose error)

  # rc 3: same-entry prose conflict — reconcile the listed entry files via the
  # curated MERGE_PROMPT, then continue the rebase deterministically.
  log "[sync] prose conflict — reconciling entry file(s) via MERGE_PROMPT:"
  printf '%s\n' "$files"
  local prompt="${MERGE_PROMPT//<FILES>/$files}"
  # Route the KB-MERGE reconcile to cloud Claude regardless of main routing — the
  # knowledge base is never reconciled by a weak/local main executor (Task 3).
  run_agent cloud "$prompt" || true

  log "[sync] scripts/agentware kb-git merge-continue (rebuild derived + finish push)"
  scripts/agentware kb-git merge-continue
}
