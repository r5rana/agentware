#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/routing.sh — per-phase runtime routing, resolved ONCE at run start.
#
# Sourced at SOURCE time by agentware.sh, after args.sh (so a --help/bad-invocation
# exit costs no config subprocesses) and before preflight.sh (whose PATH gate reads
# PRE_CLI/MAIN_CLI/POST_CLI). Ends with `:` — see the note in args.sh.

# The CLI runtime used to spawn agents. Resolved env -> config.env -> default
# `claude` (the single source of truth `scripts/agentware config --cli-only`
# reports; onboarding records it via --set-cli). Override per-run with
# AGENTWARE_CLI. AGENTWARE_MODEL optionally overrides the model passed to each
# spawn (otherwise the subagent's own `model:` frontmatter applies).
CLI="${AGENTWARE_CLI:-$(scripts/agentware config --cli-only 2>/dev/null || echo claude)}"
MODEL="${AGENTWARE_MODEL:-}"

# claude's autonomy flag, defined ONCE so the literal appears a single time in
# the loop (run_agent spawns with it; the --dry-run argv printer echoes it).
# The codex faithful analog is --dangerously-bypass-approvals-and-sandbox.
CLAUDE_SKIP_PERMS="--dangerously-skip-permissions"

# ---- PER-PHASE RUNTIME ROUTING (Task 3) ----
# Resolve each loop role's (pre|main|post) runtime axes — CLI + MODEL + LOCAL
# provider — exactly ONCE here at run start into immutable bash vars. run_agent
# and show_spawn_argv read these vars and make ZERO per-iteration config
# subprocess calls, and the resolution is fixed for the whole run (config is
# immutable mid-loop; a routing change takes effect only on the NEXT run).
# scripts/agentware is the single source of truth: each --<phase>-<axis>-only
# reader already applies the precedence (phase env -> phase config.env -> global
# AGENTWARE_CLI/AGENTWARE_MODEL -> default claude), so with NO per-phase overrides
# every role resolves to exactly the global value and the run stays byte-identical
# to today's all-cloud default. The `|| echo "$CLI"/"$MODEL"` fallbacks keep the
# loop working against an older scripts/agentware that lacks the per-phase readers.
PRE_CLI="$(scripts/agentware config --pre-cli-only 2>/dev/null || echo "$CLI")"
PRE_MODEL="$(scripts/agentware config --pre-model-only 2>/dev/null || echo "$MODEL")"
PRE_LOCAL="$(scripts/agentware config --pre-local-only 2>/dev/null || echo "")"
MAIN_CLI="$(scripts/agentware config --main-cli-only 2>/dev/null || echo "$CLI")"
MAIN_MODEL="$(scripts/agentware config --main-model-only 2>/dev/null || echo "$MODEL")"
MAIN_LOCAL="$(scripts/agentware config --main-local-only 2>/dev/null || echo "")"
POST_CLI="$(scripts/agentware config --post-cli-only 2>/dev/null || echo "$CLI")"
POST_MODEL="$(scripts/agentware config --post-model-only 2>/dev/null || echo "$MODEL")"
POST_LOCAL="$(scripts/agentware config --post-local-only 2>/dev/null || echo "")"

:
