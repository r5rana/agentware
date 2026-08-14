#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/args.sh — invocation contract: --help, the feature name, the work
# directory, and every flag default.
#
# Sourced by agentware.sh WITHOUT arguments, which is load-bearing: a bash
# `source` with no argument list shares the caller's positional parameters, so
# `$1` here IS the orchestrator's `$1` and the `shift`s below consume the
# orchestrator's own argv exactly as the pre-split inline block did. Passing
# arguments to the source would REBIND `$@` to those arguments instead and the
# flag loop would silently parse nothing.
#
# This lib runs at SOURCE time (it exits on a bad invocation) rather than
# defining functions, so it must be sourced after usage.sh and before routing.sh.
#
# Sets: FEATURE, KDIR, DOCS_DIR, STATE_DIR, MAX_ITERATIONS, MAX_PROMOTE_RETRIES,
#       AGENT, SKIP_PRE, SKIP_POST, DRY_RUN, VALIDATE, NO_STREAM,
#       EXTRA_{PRE,MAIN,POST}_PROMPT.

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

# Validate the feature name: alphanumerics, dashes, underscores — with at most
# ONE optional `auto/` prefix for workorders (work/auto/<slug>/, feature 260710).
if [[ ! "$FEATURE" =~ ^(auto/)?[a-zA-Z0-9_-]+$ ]]; then
  echo "Error: invalid feature name '$FEATURE'"
  echo "Feature names may contain only letters, digits, dashes, and underscores ([a-zA-Z0-9_-]), with at most one optional 'auto/' prefix for workorders."
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

# TERMINATE ON SUCCESS — load-bearing, not decoration. `source` returns the status
# of its LAST command, and the orchestrator runs under `set -e`, so a lib ending on
# a false `[[ ]] && ...` idiom (the line above, whenever AGENTWARE_NO_STREAM is
# unset — i.e. almost every run) makes `source args.sh` a failed simple command and
# the orchestrator exits 1 having printed NOTHING. Every source-time lib in this
# directory ends with this `:` for that reason.
:
