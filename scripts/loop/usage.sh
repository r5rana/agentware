#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/usage.sh — the CLI help surface for ./agentware.sh.
#
# Sourced by agentware.sh before argument parsing (scripts/loop/args.sh calls it
# for --help and for a malformed invocation). Kept in its OWN lib rather than the
# orchestrator so the flag/env documentation can grow without spending the
# orchestrator's 250-line budget. Function definition only — sourcing standalone
# under `set -e` exits 0.

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
