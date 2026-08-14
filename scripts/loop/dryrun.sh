#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/dryrun.sh — the --dry-run report: print prompts + the iteration plan
# + the resolved per-phase spawn argv, then exit WITHOUT spawning the CLI.
#
# Sourced at SOURCE time by agentware.sh after prompts-post.sh (it echoes all three
# phase prompts) and spawn.sh (show_spawn_argv). Runs only under --dry-run and exits
# 0 from inside the source, which is intentional: an `exit` in a sourced file exits
# the whole script, so the orchestrator needs no post-condition. Ends with `:` — see
# the note in args.sh.

if [[ "$DRY_RUN" == true ]]; then
  echo "===== DRY RUN: $FEATURE_NAME ====="
  echo "Docs dir:        $DOCS_DIR"
  echo "Agent:           $AGENT"
  echo "Runtime (pre):   $PRE_CLI${PRE_MODEL:+ model=$PRE_MODEL}${PRE_LOCAL:+ local=$PRE_LOCAL}"
  echo "Runtime (main):  $MAIN_CLI${MAIN_MODEL:+ model=$MAIN_MODEL}${MAIN_LOCAL:+ local=$MAIN_LOCAL}"
  echo "Runtime (post):  $POST_CLI${POST_MODEL:+ model=$POST_MODEL}${POST_LOCAL:+ local=$POST_LOCAL}"
  echo "Knowledge dir:   ${KDIR:-<unconfigured>} (initialized: $INITIALIZED)"
  echo "Max iterations:  $MAX_ITERATIONS"
  echo "Skip pre:        $SKIP_PRE"
  echo "Skip post:       $SKIP_POST"
  echo "Open markers:    $(open_markers) task(s) remaining in plan.md"
  echo "Completion file: $STATE_DIR/.done"
  echo "Feature marker:  <promise>${FEATURE_UPPER}_COMPLETE</promise>"
  echo
  if [[ "$SKIP_PRE" != true ]]; then
    echo "----- PRE PHASE PROMPT (3 tasks max) -----"; echo "$PRE_PROMPT"; echo
  fi
  echo "----- MAIN PHASE PROMPT (up to $MAX_ITERATIONS iterations) -----"; echo "$MAIN_PROMPT"; echo
  if [[ "$SKIP_POST" != true ]]; then
    echo "----- POST PHASE PROMPT (1 task) -----"; echo "$POST_PROMPT"; echo
  fi
  echo "===== RESOLVED SPAWN ARGV (per-phase routing) ====="; echo
  [[ "$SKIP_PRE" != true ]] && show_spawn_argv "PRE" pre "$PRE_PROMPT"
  show_spawn_argv "MAIN" main "$MAIN_PROMPT"
  [[ "$SKIP_POST" != true ]] && show_spawn_argv "POST" post "$POST_PROMPT"
  echo "===== DRY RUN complete — no agent was spawned ====="
  exit 0
fi

:
