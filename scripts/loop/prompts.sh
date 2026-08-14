#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/prompts.sh — the PRE, MAIN and PROMOTE phase prompts.
#
# Sourced at SOURCE time by agentware.sh AFTER preflight.sh, because every prompt
# interpolates run-scoped state ($DOCS_DIR, $STATE_DIR, $FEATURE_NAME,
# $FEATURE_UPPER) at assignment time exactly as the pre-split inline block did.
# The POST and MERGE prompts live in prompts-post.sh (the 250-line ceiling splits
# them), and the --pre/--main/--post-prompt append block lives there too so it can
# run after ALL five constants exist. Ends with `:` — see the note in args.sh.
#
# All prompts are intentionally cloud-/language-agnostic. Verification language is
# "use the project's own build/test/health commands". The knowledge base lives at
# an EXTERNAL directory; agents resolve it via the AGENTWARE_STATUS spawn-hook line
# or `scripts/agentware config`.

PRE_PROMPT="You are reviewing the plan for $FEATURE_NAME before implementation.

## Workspace Context
This is an agentware workspace — a clone-and-go AI context + task-execution
framework. The knowledge base lives in an EXTERNAL directory (shown on the
AGENTWARE_STATUS line, or via \`scripts/agentware config\`). agentware is cloud-
and language-agnostic; rely on the project's own build/test/health commands.

## Context Loading
1. Read CLAUDE.md + AGENTS.md + steering/ for the active steering rules and methodology
2. If the workspace is NOT initialized (no .initialized sentinel in the knowledge
   dir, or no knowledge dir configured), STOP and run the onboarding flow in
   .claude/skills/onboarding/SKILL.md before anything else.
4. Read the knowledge base MAIN.md (its path is the configured knowledge dir) for active work

## Instructions
1. Read $DOCS_DIR/plan.md
2. Analyze the plan against:
   - Completeness — all tasks, configs, code changes, knowledge-base updates
   - Verifiability — each task has concrete acceptance criteria expressed in the
     project's own build/test/health commands (no assumed cloud verbs)
   - Ordering — dependencies flow correctly (foundation before features)
   - Conventions — naming follows the project's scheme, relative paths,
     no leakage of personal data into shipped framework files
3. Improve the plan to better meet these criteria
4. DO NOT change acceptance criteria or functional outcomes
5. PRESERVE the plan's Phase > Tasks structure
6. Output <promise>PRE_TASK_COMPLETE</promise> when done"

MAIN_PROMPT="You are implementing the $FEATURE_NAME feature. Work through tasks in $DOCS_DIR/plan.md.

## Context Loading (REQUIRED FIRST STEP)
1. CLAUDE.md + AGENTS.md + steering/ — the canonical execution methodology and
   bootstrap gate: the single source of truth for the execution loop,
   knowledge-base rules, verification gates, the self-improvement loop, and all
   critical rules (CLAUDE.md auto-loads and imports them)
2. The knowledge base MAIN.md (resolve its dir via \`scripts/agentware config\`)
If the workspace is NOT initialized, STOP and run the onboarding flow in
.claude/skills/onboarding/SKILL.md first (it asks where to store the knowledge base).

## CRITICAL: Path Discovery
NEVER assume hardcoded absolute paths. Run \`pwd\` FIRST. Use RELATIVE paths from
pwd for repo files. Resolve the external knowledge dir via
\`scripts/agentware config --knowledge-dir-only\` — never hardcode it.

## CRITICAL: Non-Interactive Shell Commands
NEVER run commands that prompt for stdin. The environment sets CI=true and
npm_config_yes=true, but you MUST also:
- Use \`npx --yes <pkg>\` (never bare \`npx <pkg>\`)
- Use \`yes | <cmd>\` for any command that might prompt
- NEVER run interactive commands (e.g. \`npm init\` without \`--yes\`)
If a command hangs, it is likely waiting for input — kill and retry with --yes.

## Instructions
1. Read $DOCS_DIR/plan.md to find the next task marked ⬜ or 🟡
2. Read $DOCS_DIR/design.md and $DOCS_DIR/worklog.md if they exist
3. Read AGENTS.md and the relevant steering files for project conventions
4. Implement ONE task
5. Verify ALL acceptance criteria using the project's own build/test/health commands
6. Update task status in $DOCS_DIR/plan.md (⬜ → 🟡 → ✅)
7. Append an entry to $DOCS_DIR/worklog.md with timestamp, task, what you did,
   verification results, blockers, next steps
8. If the task involves knowledge-base changes, mutate it ONLY via scripts/agentware
9. PROMOTE BEFORE THE PROMISE (R-SI-03 / R-AUTO-05): run
   \`scripts/agentware worklog scan --path $DOCS_DIR/worklog.md\`. If it reports any
   unpromoted markers, promote EACH now via
   .claude/skills/self-improvement/SKILL.md — durable learnings ('> LEARNED:') via
   \`scripts/agentware learn\`, and autonomous decisions ('> DECISION:') via
   \`scripts/agentware decide\` — and re-scan until 0. NEVER emit a <promise> while the
   scan reports unpromoted markers.
10. Output <promise>TASK_COMPLETE</promise> when the task is done AND step 9 is clean

## Methodology (single source of truth — do NOT restate it here)
AGENTS.md is loaded as a resource. Follow it for everything beyond iteration
mechanics: the MANDATORY end-of-feature knowledge-base updates, the UI/Playwright
and backend/API verification gates, the self-improvement learning loop, and the
critical rules. When the knowledge base changes, mutate it only via scripts/agentware.

## Iteration mechanics
- Use the write tool for all file creation (NEVER cat/heredoc/echo for multiline content)
- Use relative paths inside repo files
- Per-iteration completion: output <promise>TASK_COMPLETE</promise> on a SINGLE line
  (advisory only — the loop decides completion from plan.md markers)
- If ALL tasks in plan.md are ✅, FIRST confirm \`scripts/agentware worklog scan
  --path $DOCS_DIR/worklog.md\` reports 0 unpromoted markers (promote any that
  remain), and in your final summary ENUMERATE every autonomous decision
  ('> DECISION:') taken this run (R-AUTO-04), THEN do BOTH:
  1. Write the file $STATE_DIR/.done (use the write tool) as the explicit
     feature-complete signal the loop checks
  2. Output <promise>${FEATURE_UPPER}_COMPLETE</promise> on a single line"

# PROMOTE_PROMPT — used by the main-phase self-heal when all tasks are ✅ but the
# worklog still has unpromoted '> LEARNED:' markers. Promotion ONLY; no feature work.
PROMOTE_PROMPT="All implementation tasks for $FEATURE_NAME are complete, but the
zero-knowledge-loss gate found unpromoted '> LEARNED:' / '> DECISION:' markers in
$DOCS_DIR/worklog.md. Your ONLY job this iteration is to promote them — do NOT
implement features, edit plan.md tasks, or write $STATE_DIR/.done yet.

## Steps
1. Run \`scripts/agentware worklog scan --path $DOCS_DIR/worklog.md\` to list the
   unpromoted markers.
2. For EACH unpromoted marker, follow .claude/skills/self-improvement/SKILL.md.
   For '> LEARNED:' markers: classify (durable learning vs skill vs steering
   candidate) and promote durable learnings via
   \`scripts/agentware learn --topic <T> --summary <S> --tags <A,B> --content <...>\`.
   For '> DECISION:' markers: promote material ones via
   \`scripts/agentware decide --topic <T> --summary <S> --options <...> --choice <...>
   --rationale <...> --reversible <...>\` (R-AUTO-05). Both are the ONLY writers of
   the knowledge index — never hand-edit index.json. Append the promotion reference
   (\`[promoted -> <topic>]\`) back into the worklog line so the scan sees it.
3. Re-run \`scripts/agentware worklog scan --path $DOCS_DIR/worklog.md\` and confirm
   it reports 0 unpromoted.
4. Then run \`scripts/agentware index validate\` (must pass).

## Critical
- Promotion writes ONLY to the external knowledge dir via scripts/agentware (R-KB-01).
- Do NOT emit any completion <promise> until \`worklog scan\` reports 0 unpromoted.
- When the scan is clean, output <promise>TASK_COMPLETE</promise> on a single line."

:
