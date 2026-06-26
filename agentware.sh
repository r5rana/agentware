#!/bin/bash
# agentware.sh - Autonomous task loop for an agentware workspace.
#
# agentware is a clone-and-go steering framework for AI agents. The repo holds
# ONLY generic steering (methodology, agents, skills, this loop, the toolkit).
# All knowledge/learnings live in an EXTERNAL directory the operator chooses at
# first run (see .claude/skills/onboarding/SKILL.md). Nothing personal is committed.
#
# This script drives the 3-phase loop (pre / main / post) over a feature plan in
# <knowledge-dir>/work/<feature>/plan.md. Each phase runs until it emits its promise
# marker (or hits the iteration cap), then the next phase begins.
#
# Usage:
#   ./agentware.sh <feature-name> [--max-iterations N] [--agent AGENT]
#                                 [--skip-pre] [--skip-post]
#                                 [--pre-prompt "extra"]
#                                 [--main-prompt "extra"]
#                                 [--post-prompt "extra"]
#                                 [--dry-run] [--validate] [--no-stream]

set -e

# The CLI runtime used to spawn agents. Defaults to Claude Code (`claude`).
# Override with AGENTWARE_CLI if your runtime binary differs. AGENTWARE_MODEL
# optionally overrides the model passed to each spawn (otherwise the subagent's
# own `model:` frontmatter applies).
CLI="${AGENTWARE_CLI:-claude}"
MODEL="${AGENTWARE_MODEL:-}"

usage() {
  echo "Usage: ./agentware.sh <feature-name> [--max-iterations N] [--agent AGENT]"
  echo "                                     [--skip-pre] [--skip-post]"
  echo "                                     [--pre-prompt \"extra\"]"
  echo "                                     [--main-prompt \"extra\"]"
  echo "                                     [--post-prompt \"extra\"]"
  echo "                                     [--dry-run] [--validate] [--no-stream]"
  echo ""
  echo "Flags:"
  echo "  --max-iterations N   Cap main-phase iterations (default 100)"
  echo "  --agent AGENT        Override the agent (default agentware-execution)"
  echo "  --skip-pre           Skip the pre (plan-review) phase"
  echo "  --skip-post          Skip the post (assessment) phase"
  echo "  --dry-run            Print the phase prompts + iteration plan; do NOT spawn the CLI"
  echo "  --validate           Run 'scripts/agentware audit' as a preflight gate"
  echo "  --no-stream          Disable the live PostToolUse terminal auto-stream follower"
  echo ""
  echo "Env:"
  echo "  AGENTWARE_CLI              agent runtime binary (default: claude)"
  echo "  AGENTWARE_MODEL            model passed to each spawn (default: subagent's own)"
  echo "  AGENTWARE_KNOWLEDGE_DIR    override the external knowledge dir"
  echo "  AGENTWARE_NO_STREAM        set to disable the live terminal auto-stream (= --no-stream)"
}

if [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
  usage
  exit 0
fi

if [[ -z "$1" ]] || [[ "$1" == --* ]]; then
  usage
  exit 1
fi

FEATURE="$1"
shift

# Validate the feature name: alphanumerics, dashes, underscores only.
if [[ ! "$FEATURE" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo "Error: invalid feature name '$FEATURE'"
  echo "Feature names may contain only letters, digits, dashes, and underscores ([a-zA-Z0-9_-])."
  exit 1
fi

# Resolve the EXTERNAL knowledge dir up front — feature work artifacts
# (plan/worklog/state) live there, NOT in the orchestrator package, so the
# package stays read-only across projects.
KDIR="$(scripts/agentware config --knowledge-dir-only 2>/dev/null || true)"

# Find the feature's work directory. Preference order:
#   1. <knowledge-dir>/work/<feature>   (the normal location for project work)
#   2. docs/design/<feature>            (in-repo, for developing agentware itself)
if [[ -n "$KDIR" ]] && [[ -d "$KDIR/work/$FEATURE" ]]; then
  DOCS_DIR="$KDIR/work/$FEATURE"
elif [[ -d "docs/design/$FEATURE" ]]; then
  DOCS_DIR="docs/design/$FEATURE"
else
  echo "Error: Could not find a work directory for feature '$FEATURE'"
  if [[ -n "$KDIR" ]]; then
    echo "Searched: $KDIR/work/$FEATURE, docs/design/$FEATURE"
    echo "Create a plan first (the agentware-planner writes to $KDIR/work/$FEATURE/plan.md)."
  else
    echo "Searched: docs/design/$FEATURE"
    echo "The knowledge dir is not configured — run onboarding first (scripts/agentware config)."
  fi
  exit 1
fi

MAX_ITERATIONS=100
# Bounded self-heal: how many times the main phase may re-engage the agent to
# promote unpromoted '> LEARNED:' markers before failing loud (zero-knowledge-loss).
MAX_PROMOTE_RETRIES=3
STATE_DIR="$DOCS_DIR/.loop"
AGENT="agentware-execution"
SKIP_PRE=false
SKIP_POST=false
DRY_RUN=false
VALIDATE=false
NO_STREAM=false
EXTRA_PRE_PROMPT=""
EXTRA_MAIN_PROMPT=""
EXTRA_POST_PROMPT=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --max-iterations) MAX_ITERATIONS="$2"; shift 2 ;;
    --agent) AGENT="$2"; shift 2 ;;
    --skip-pre) SKIP_PRE=true; shift ;;
    --skip-post) SKIP_POST=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    --validate) VALIDATE=true; shift ;;
    --no-stream) NO_STREAM=true; shift ;;
    --pre-prompt) EXTRA_PRE_PROMPT="$2"; shift 2 ;;
    --main-prompt) EXTRA_MAIN_PROMPT="$2"; shift 2 ;;
    --post-prompt) EXTRA_POST_PROMPT="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# Env opt-out for the live terminal auto-stream (equivalent to --no-stream).
[[ -n "${AGENTWARE_NO_STREAM:-}" ]] && NO_STREAM=true

# ---- PREFLIGHT GATES ----

# jq is always required; the CLI runtime is required only when we actually spawn.
if ! command -v jq >/dev/null 2>&1; then
  echo "Error: required dependency 'jq' not found on PATH."
  echo "Install jq (e.g. 'brew install jq') and re-run."
  exit 1
fi
if [[ "$DRY_RUN" != true ]] && ! command -v "$CLI" >/dev/null 2>&1; then
  echo "Error: required agent runtime '$CLI' not found on PATH."
  echo "Install it, set AGENTWARE_CLI=<your-cli>, or run with --dry-run."
  exit 1
fi

# plan.md must exist and be non-empty before the main phase.
if [[ ! -s "$DOCS_DIR/plan.md" ]]; then
  echo "Error: plan file not found or empty: $DOCS_DIR/plan.md"
  exit 1
fi

# Initialized state (KDIR was resolved up front). When the workspace is not yet
# initialized, KDIR-dependent gates are skipped and the agent's first-run gate
# runs onboarding before any task work.
INITIALIZED=false
if [[ -n "$KDIR" ]] && [[ -f "$KDIR/.initialized" ]]; then
  INITIALIZED=true
fi

# --validate — run the deterministic audit as a preflight gate.
if [[ "$VALIDATE" == true ]]; then
  if [[ "$INITIALIZED" != true ]]; then
    echo "[preflight] workspace not initialized — skipping 'audit' (onboarding runs first)."
  else
    echo "[preflight] Running 'scripts/agentware audit'..."
    if ! scripts/agentware audit; then
      echo "Error: preflight 'scripts/agentware audit' failed. Fix the reported issues first."
      exit 1
    fi
    echo "[preflight] audit passed."
  fi
fi

mkdir -p "$STATE_DIR"

# ---- LIVE ACTION STREAM (PostToolUse → this terminal) ----
# Per-run sink the PostToolUse hook (scripts/hooks/log-tool.sh) appends one human
# line to per tool call. Exported as AGENTWARE_LIVE_LOG so every spawned
# `claude -p` (pre, main, post) and its PostToolUse hook inherit it. A background
# `tail -F` follower then echoes those lines into THIS terminal as the agent works
# — so progress streams live instead of only landing at Stop-time. It is a VIEW,
# never a gate: a missing/empty sink, a killed tail, --no-stream, or --dry-run
# must never break the run. Truncate the sink at run start so a prior run's
# actions don't replay; export BEFORE run_pre_hooks/the phase loops so all spawns
# inherit it.
LIVE_LOG="$STATE_DIR/live-stream.log"
TAIL_PID=""
export AGENTWARE_LIVE_LOG="$LIVE_LOG"
: > "$LIVE_LOG" 2>/dev/null || true

# Start the follower unless opted out (--no-stream / AGENTWARE_NO_STREAM) or this
# is a --dry-run (no CLI is spawned, so nothing would ever write the sink).
# `-n0` ignores pre-existing lines; `-F` retries/follows by name even though the
# file may have no lines yet.
if [[ "$NO_STREAM" != true ]] && [[ "$DRY_RUN" != true ]]; then
  tail -n0 -F "$LIVE_LOG" 2>/dev/null & TAIL_PID=$!
fi

FEATURE_NAME=$(basename "$DOCS_DIR")
FEATURE_UPPER=$(echo "$FEATURE_NAME" | tr '[:lower:]' '[:upper:]' | tr '-' '_')

log() { echo "[$(date '+%H:%M:%S')] $1"; }

notify() {
  command -v notify-send >/dev/null 2>&1 && notify-send "agentware: $FEATURE_NAME" "$1" 2>/dev/null || true
  command -v osascript   >/dev/null 2>&1 && osascript -e "display notification \"$1\" with title \"agentware: $FEATURE_NAME\"" 2>/dev/null || true
}

cleanup() {
  local iter
  iter=$(cat "$STATE_DIR/.iteration" 2>/dev/null || echo 0)
  # Reap the live-stream follower (harmless if unset / already gone).
  [[ -n "${TAIL_PID:-}" ]] && kill "$TAIL_PID" 2>/dev/null || true
  log "Stopped at iteration $iter"
  notify "stopped"
  exit 130
}

trap cleanup INT TERM
# Separate trap slot for the normal-completion path: reap the follower on EXIT so
# no orphan `tail -F` survives the run. Coexists with the INT/TERM trap above.
trap '[[ -n "${TAIL_PID:-}" ]] && kill "$TAIL_PID" 2>/dev/null || true' EXIT

log "Starting agentware loop for '$FEATURE_NAME'"
log "Docs: $DOCS_DIR"
[[ -n "$KDIR" ]] && log "Knowledge dir: $KDIR (initialized: $INITIALIZED)"
notify "started"

# ---- PROMPTS ----
#
# All three prompts are intentionally cloud-/language-agnostic. Verification
# language is "use the project's own build/test/health commands". The knowledge
# base lives at an EXTERNAL directory; agents resolve it via the AGENTWARE_STATUS
# spawn-hook line or `scripts/agentware config`.

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

POST_PROMPT="You are assessing the completed implementation of $FEATURE_NAME.

## Instructions
1. Read $DOCS_DIR/plan.md to understand what was planned
2. Read $DOCS_DIR/worklog.md to understand what was done
3. For infra/config tasks: verify the resources/configs still exist and are healthy
4. For knowledge-base tasks: verify entries are correct and index.json is valid JSON
   (run \`scripts/agentware index validate\`)
5. Evaluate against:
   - Completeness — all planned tasks done, no partial work
   - Verification — all acceptance criteria actually verified
   - Documentation — knowledge base updated with what was built
   - Conventions — naming, relative paths, no leakage of personal data
6. Autonomous decisions (R-AUTO-04): scan $DOCS_DIR/worklog.md for every
   '> DECISION:' marker. Under an '## Autonomous Decisions' heading in the
   assessment, list each one and judge whether it respected R-AUTO-02 (i.e. it did
   NOT autonomously expand scope, change acceptance criteria, act destructively or
   irreversibly, weaken security, change dependencies, pivot the whole approach, or
   override a STOP gate). Flag any decision that overstepped as a FAIL.
7. Write a PASS/FAIL assessment to $DOCS_DIR/assessment.md
8. After writing the assessment, identify any new learnings or gotchas. For each,
   classify against the self-improvement decision tree
   (.claude/skills/self-improvement/SKILL.md) and note them under
   '## Extracted Knowledge' with: suggested ID, classification (learning /
   skill candidate / steering candidate), one-paragraph summary, suggested
   wiring location, and tags.
9. Output <promise>POST_COMPLETE</promise> when done"

# MERGE_PROMPT — Phase 5 of the KB git sync (feature 260625-kb-git-sync). Spawned
# (reusing the agentware-execution agent — NO new agent) ONLY for the rare case of
# a same-entry prose conflict: two agents/devs edited the SAME knowledge entry two
# different ways. The deterministic CLI already resolves all DERIVED-file conflicts
# by rebuilding (C-1); this agent reconciles ONLY the conflicted ENTRY markdown,
# preserving facts from BOTH sides, and NEVER touches derived files (they are
# rebuilt by `kb-git merge-continue` afterward). The <FILES> placeholder is
# substituted by kb_sync_push() with the newline-separated conflicted entry paths.
MERGE_PROMPT="A knowledge-base git rebase has PAUSED on a same-entry prose
conflict: the SAME knowledge entry was edited two different ways and git could not
merge it automatically. Your ONLY job is to reconcile the conflicted entry file(s)
below, preserving the facts from BOTH sides. A separate deterministic step rebuilds
all derived files and continues the rebase after you finish — so do NOT run any git
command and do NOT touch derived files.

## Conflicted entry file(s) (resolve EXACTLY these, nothing else)
<FILES>

## Steps
1. Open each conflicted file. Find the git conflict markers (\`<<<<<<<\`,
   \`=======\`, \`>>>>>>>\`) and resolve EVERY one of them.
2. Reconcile by UNION: keep every distinct fact/sentence/bullet from BOTH sides.
   When the two sides state the same thing, keep it once; never drop a fact that
   exists on only one side. Preserve the YAML frontmatter block at the top intact
   (do not duplicate or alter \`id\`, \`title\`, \`category\`, \`tags\`, etc.).
3. Remove ALL conflict markers so no \`<<<<<<<\`, \`=======\`, or \`>>>>>>>\` line
   remains. Leave the file as clean, readable markdown.

## Critical — do NOT cross these lines
- Edit ONLY the conflicted entry file(s) listed above.
- NEVER edit derived files (\`index.json\`, \`FEATURES.md\`, any \`<section>/index.md\`
   roster) — they are regenerated deterministically from frontmatter afterward.
- NEVER run git (no add/commit/rebase/push) — the loop continues the rebase for you.
- Do NOT delete or rename any entry; nothing-lost is enforced downstream.

When every listed file is conflict-marker-free with both sides' facts retained,
output <promise>MERGE_COMPLETE</promise> on a single line."

# Append extra prompts if provided.
[[ -n "$EXTRA_PRE_PROMPT" ]] && PRE_PROMPT="$PRE_PROMPT

## Additional Instructions
$EXTRA_PRE_PROMPT"

[[ -n "$EXTRA_MAIN_PROMPT" ]] && MAIN_PROMPT="$MAIN_PROMPT

## Additional Instructions
$EXTRA_MAIN_PROMPT"

[[ -n "$EXTRA_POST_PROMPT" ]] && POST_PROMPT="$POST_PROMPT

## Additional Instructions
$EXTRA_POST_PROMPT"

# Count remaining open task markers (⬜ not-started, 🟡 in-progress) in plan.md.
# CONTRACT: the grep -cE pattern below is the canonical task-marker regex. It MUST
# stay byte-identical to PLAN_TASK_MARKER_RE in scripts/agentware (the linter derives
# its Python form from those same POSIX-ERE bytes). The byte-identity is pinned by the
# contract test in tests/test_plan_lint.py (locates this line by the `⬜|🟡` token).
open_markers() {
  local n
  n=$(grep -cE '^[[:space:]]*-[[:space:]]*(⬜|🟡)[[:space:]]*\*\*[0-9]' "$DOCS_DIR/plan.md" 2>/dev/null || true)
  echo "${n:-0}"
}

# Returns 0 if every '> LEARNED:' marker in the worklog is promoted (zero knowledge
# loss), non-zero if unpromoted markers remain. This REUSES the deterministic
# `worklog scan` gate — it does NOT reimplement detection. No-op PASS when the
# toolkit/worklog is absent or the workspace is not yet initialized (init state is
# re-resolved live, since onboarding may have run during the main phase).
learnings_promoted() {
  [[ -x scripts/agentware ]] || return 0
  local kdir
  kdir="$(scripts/agentware config --knowledge-dir-only 2>/dev/null || true)"
  [[ -n "$kdir" && -f "$kdir/.initialized" ]] || return 0
  [[ -f "$DOCS_DIR/worklog.md" ]] || return 0
  scripts/agentware worklog scan --path "$DOCS_DIR/worklog.md" >/dev/null 2>&1
}

# ---- TOOLKIT HOOKS (deterministic gates around the main phase) ----
#
# All hooks no-op gracefully if scripts/agentware is absent. KDIR-dependent gates
# (index validate / worklog scan) are skipped until the workspace is initialized.

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

  log "[pre-hook] scripts/agentware index validate"
  if ! scripts/agentware index validate; then
    echo "Error: [pre-hook] index validation failed. Fix the knowledge index (via scripts/agentware) first."
    exit 1
  fi

  # Plan-format gate (feature 260625-plan-lint-gate, Task 5). Assert the plan's
  # STRUCTURAL contract (markers, numbering, per-task verify, sections, promise,
  # autonomy) BEFORE the pre phase so a malformed plan fails loudly and specifically
  # instead of silently no-op'ing at the zero-markers guard. --strict makes the R9
  # autonomy rule a hard failure so a non-autonomous plan is caught before the run.
  # Skip cleanly when plan.md is absent (e.g. onboarding-only runs).
  if [[ -f "$DOCS_DIR/plan.md" ]]; then
    log "[pre-hook] scripts/agentware plan lint --strict"
    if ! scripts/agentware plan lint --path "$DOCS_DIR/plan.md" --strict; then
      echo "Error: [pre-hook] plan lint failed — plan.md violates the structural contract (markers must be '- ⬜ **N** …'). Fix it (see the rule output above) before the run."
      exit 1
    fi
  fi

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

  log "[post-hook] scripts/agentware worklog scan (zero-knowledge-loss gate)"
  if ! scripts/agentware worklog scan --path "$DOCS_DIR/worklog.md"; then
    echo "Error: [post-hook] unpromoted '> LEARNED:' / '> DECISION:' markers remain in $DOCS_DIR/worklog.md."
    echo "Promote each via: scripts/agentware learn ... (LEARNED) or scripts/agentware decide ... (DECISION)."
    echo "Zero knowledge loss is enforced — the feature is NOT complete until every marker is promoted."
    exit 1
  fi
}

# run_kb_sync — commit + push the KB, run as the LAST step of the loop, AFTER the
# post phase (feature 260625-autocommit-post-phase-fix, Phase 1). It was previously
# the tail of run_post_hooks, which executes BEFORE `run_phase "post"`; the post
# phase writes MORE KB files (assessment.md, benchmarks/history.jsonl + SCORECARD.md
# rows, .loop/ state), so committing inside run_post_hooks always left an uncommitted
# tail. Relocating the commit/push here captures the assessment + its ledger rows in
# the SAME commit, on BOTH the post-ran and --skip-post paths (C-1/C-2).
#
# KB commit discipline (feature 260625-kb-git-sync, Task 2.1; default-ON flip
# 260625-kb-autocommit-default, Task 3.1). ON BY DEFAULT — gated on the resolved
# setting (env → config → default ON, via kb_autocommit_enabled; operator consent
# captured at onboarding satisfies R-GIT-01, and the per-run env override C-3 is
# the escape hatch). The zero-knowledge-loss gate in run_post_hooks already passed,
# so nothing commits until every LEARNED: marker is promoted and the index is valid.
# Scope is the KB repo ONLY (C-1) — the CLI refuses to stage the project/package.
# No-ops on a non-tracked KB or no upstream (C-2) or a clean tree. logs/ and .loop/
# are gitignored (C-3/C-4) so transcripts and ephemeral loop state are never staged.
# The commit-message scope tag is the feature name with its leading YYMMDD- date
# stripped.
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
    log "[kb-sync] kb_sync_push (push KB upstream — deterministic merge + nothing-lost gate)"
    if ! kb_sync_push; then
      echo "Error: [kb-sync] KB push failed — see the message above. Nothing was"
      echo "pushed (no silent loss); resolve the conflict/race and re-run."
      exit 1
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
# re-push retry live inside the CLI (`kb-git push` / `merge-continue`, Phase 6);
# this is wired into run_post_hooks AFTER the KB commit, behind the same opt-in.
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
  MODEL_FLAG=(); [[ -n "$MODEL" ]] && MODEL_FLAG=(--model "$MODEL")
  CI=true npm_config_yes=true HOMEBREW_NO_AUTO_UPDATE=1 "$CLI" -p --agent "$AGENT" \
    --dangerously-skip-permissions "${MODEL_FLAG[@]}" "$prompt" || true

  log "[sync] scripts/agentware kb-git merge-continue (rebuild derived + finish push)"
  scripts/agentware kb-git merge-continue
}

# --dry-run — print prompts + iteration plan, then exit WITHOUT spawning the CLI.
if [[ "$DRY_RUN" == true ]]; then
  echo "===== DRY RUN: $FEATURE_NAME ====="
  echo "Docs dir:        $DOCS_DIR"
  echo "Agent:           $AGENT"
  echo "Runtime:         $CLI"
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
  echo "===== DRY RUN complete — no agent was spawned ====="
  exit 0
fi

run_phase() {
  local phase_name="$1"
  local prompt="$2"
  local max_iter="$3"
  local completion_marker="$4"

  log "=== Starting $phase_name phase ($max_iter tasks) ==="

  for i in $(seq 1 "$max_iter"); do
    echo "$i" > "$STATE_DIR/.${phase_name}_iteration"
    log "--- $phase_name task $i/$max_iter ---"

    MODEL_FLAG=(); [[ -n "$MODEL" ]] && MODEL_FLAG=(--model "$MODEL")
    output=$(CI=true npm_config_yes=true HOMEBREW_NO_AUTO_UPDATE=1 "$CLI" -p --agent "$AGENT" --dangerously-skip-permissions "${MODEL_FLAG[@]}" "$prompt" 2>&1 | tee /dev/tty) || true

    if echo "$output" | grep -q "<promise>$completion_marker</promise>"; then
      log "✓ $phase_name complete at task $i"
      return 0
    fi
    sleep 2
  done

  log "✓ $phase_name phase finished ($max_iter tasks)"
  return 0
}

# ---- PRE-HOOK ----
run_pre_hooks

# Pre phase (3 tasks max).
if [[ "$SKIP_PRE" != true ]]; then
  run_phase "pre" "$PRE_PROMPT" 3 "PRE_TASK_COMPLETE"
fi

# Main phase.
log "=== Starting main phase ($MAX_ITERATIONS iterations max) ==="

if [[ "$(open_markers)" -eq 0 ]]; then
  # Disambiguate "zero open markers": this means EITHER every task is ✅ (done)
  # OR the plan has no parseable task markers at all (malformed). Tell them apart
  # so a one-character marker typo fails LOUDLY instead of reading as success.
  done_markers=$(grep -cE '^[[:space:]]*-[[:space:]]*✅[[:space:]]*\*\*[0-9]' "$DOCS_DIR/plan.md" 2>/dev/null || true)
  if [[ "${done_markers:-0}" -gt 0 ]]; then
    log "✓ No open (⬜/🟡) task markers in plan.md — all ${done_markers} task(s) ✅ (nothing to do)."
    exit 1
  fi
  # No well-formed markers of ANY status: look for task-LIKE-but-unparseable lines
  # (GitHub `- [ ]` checkboxes or `- **<letter>` headings) — the marker-typo class
  # of bug this feature exists to catch.
  if grep -qE '^[[:space:]]*-[[:space:]]*\[[ xX]?\]|^[[:space:]]*-[[:space:]]*\*\*[A-Za-z]' "$DOCS_DIR/plan.md" 2>/dev/null; then
    log "✗ Malformed plan.md: task-like lines present but ZERO canonical markers."
    log "  Expected markers like: - ⬜ **N** …  (emoji ⬜/🟡/✅ + **<digit>**)."
    log "  Diagnose: scripts/agentware plan lint --path \"$DOCS_DIR/plan.md\" --strict"
    exit 1
  fi
  log "⚠ No (⬜/🟡) task markers in plan.md — nothing to do."
  exit 1
fi

# Self-heal state: normally spawn with MAIN_PROMPT; when completion is signalled
# but learnings are unpromoted, switch to PROMOTE_PROMPT and count bounded retries.
CURRENT_PROMPT="$MAIN_PROMPT"
promote_retries=0

for i in $(seq 1 "$MAX_ITERATIONS"); do
  echo "$i" > "$STATE_DIR/.iteration"
  log "--- main iteration $i/$MAX_ITERATIONS ($(open_markers) task(s) remaining) ---"

  MODEL_FLAG=(); [[ -n "$MODEL" ]] && MODEL_FLAG=(--model "$MODEL")
  CI=true npm_config_yes=true HOMEBREW_NO_AUTO_UPDATE=1 "$CLI" -p --agent "$AGENT" --dangerously-skip-permissions "${MODEL_FLAG[@]}" "$CURRENT_PROMPT" || true

  # Completion is accepted ONLY when the feature signals done (.done or no open
  # markers) AND every '> LEARNED:' marker has been promoted (zero knowledge loss).
  if [[ -f "$STATE_DIR/.done" ]] || [[ "$(open_markers)" -eq 0 ]]; then
    if learnings_promoted; then
      log "✓ Main phase complete at iteration $i (open markers: $(open_markers); learnings promoted)"
      break
    fi

    # Completion signalled but learnings unpromoted — deterministic, bounded self-heal.
    promote_retries=$((promote_retries + 1))
    unpromoted=$(scripts/agentware worklog scan --path "$DOCS_DIR/worklog.md" 2>&1 | grep -oE '[0-9]+ of [0-9]+ LEARNED' | head -1 || true)
    log "⚠ Completion signalled but '> LEARNED:' markers are unpromoted (${unpromoted:-unpromoted markers remain}). Self-heal attempt $promote_retries/$MAX_PROMOTE_RETRIES — re-engaging to promote."
    rm -f "$STATE_DIR/.done"
    CURRENT_PROMPT="$PROMOTE_PROMPT"

    if [[ $promote_retries -ge $MAX_PROMOTE_RETRIES ]]; then
      echo "Error: AUTO-PROMOTE FAILED: '> LEARNED:' marker(s) still unpromoted after $MAX_PROMOTE_RETRIES self-heal attempt(s)."
      echo "The feature is NOT complete (zero knowledge loss is enforced)."
      echo "Promote each via: scripts/agentware learn --topic <T> --summary <S> --tags <A,B> --content <...>"
      notify "auto-promote failed after $MAX_PROMOTE_RETRIES attempts"
      exit 1
    fi
    sleep 2
    continue
  fi

  # Not complete yet — a fresh task is being executed; reset self-heal to normal.
  CURRENT_PROMPT="$MAIN_PROMPT"
  promote_retries=0

  if [[ $i -eq $MAX_ITERATIONS ]]; then
    log "⚠ Reached max iterations ($MAX_ITERATIONS) without completion"
    notify "max iterations reached"
    exit 1
  fi
  sleep 2
done

# ---- POST-HOOK ----
run_post_hooks

# Post phase (1 task).
if [[ "$SKIP_POST" != true ]]; then
  run_phase "post" "$POST_PROMPT" 1 "POST_COMPLETE"
fi

# ---- KB SYNC (commit + push) ----
# Runs LAST — AFTER the post phase — so the commit captures the assessment + its
# ledger rows (C-1). Fires on BOTH the post-ran and --skip-post paths (C-2).
run_kb_sync

log "✓ $FEATURE_NAME fully complete"
notify "complete!"
