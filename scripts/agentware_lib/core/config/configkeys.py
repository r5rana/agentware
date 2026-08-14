"""agentware_lib.core.config.configkeys — config.env locations + every SETTINGS_AW
key/default/token-set (layer: core).

Owns: the config.env file locations (_CONFIG_HOME/HOME_CONFIG/REPO_CONFIG/
CONFIG_PATHS/CONFIG_KEY) and every runtime SETTINGS_AW key, default and token set
(KB autocommit+push, retrieval mode, KB mode, user handle, runtime CLI, per-phase
axes, embedder backend/model, dream knobs, skill-propose threshold). Pure data:
no resolution, no parsing, no writing.
Does NOT own: the resolvers that READ these keys (agentware_lib.core.config.
configresolve); the lenient/strict token PARSERS (…config.configvalidate); the
config.env WRITERS (…config.configwrite); the org-mode key NAMES + layout
constants (agentware_lib.core.orgkeys); REPO_ROOT (agentware_lib.core.paths,
imported here).
Layer deps: core -> core only (imports agentware_lib.core.paths; nothing above).
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

These constants are a WIRE CONTRACT: agentware.sh's bash twin (scripts/
aw-knowledge-dir) and config.env both read the exact key strings, so the literals
here are byte-frozen by tests/unit/core/test_configkeys.py. HOME_CONFIG and
CONFIG_PATHS are the two MUTABLE globals the suite monkeypatches — always on the
aggregator package (agentware_lib.core.config.<name>), which is why every reader
in the sibling submodules resolves them late-bound through that package rather
than importing the value.
"""

import os

from agentware_lib.core import paths


# The config pointer lives in the user's HOME by default so the orchestrator
# package itself stays pristine (read-only) across projects. A repo-local config
# is honoured as a legacy/override fallback. `init` always writes HOME_CONFIG.
#
# TEST/CI ISOLATION (safety): AGENTWARE_CONFIG_HOME overrides the config DIRECTORY,
# so a test — including a SUBPROCESS test that runs `scripts/agentware config
# --set-*` — can redirect every config read/write to a scratch dir and NEVER
# clobber the operator's real ~/.agentware/config.env. Unset ⇒ byte-identical to
# today (real $HOME). This is the source-level guard that patching the module's
# HOME_CONFIG in-process could not give a spawned subprocess.
_CONFIG_HOME = os.environ.get("AGENTWARE_CONFIG_HOME", "").strip() \
    or os.path.join(os.path.expanduser("~"), ".agentware")
HOME_CONFIG = os.path.join(_CONFIG_HOME, "config.env")
REPO_CONFIG = os.path.join(paths.REPO_ROOT, ".agentware", "config.env")
CONFIG_PATHS = (HOME_CONFIG, REPO_CONFIG)
CONFIG_KEY = "AGENTWARE_KNOWLEDGE_DIR"


# KB auto-commit+push setting (feature 260625-kb-autocommit-default). Resolution
# precedence: the AGENTWARE_KB_AUTOCOMMIT env var -> config.env -> default ON.
# The resolved value is '1' (on) or '0' (off); the loop still only ACTS when the
# KB is a git work tree with an upstream (a non-tracked KB stays a clean no-op).
AUTOCOMMIT_KEY = "AGENTWARE_KB_AUTOCOMMIT"
AUTOCOMMIT_DEFAULT = "1"
_AUTOCOMMIT_OFF_TOKENS = ("0", "off", "no", "false", "disable", "disabled")
_AUTOCOMMIT_ON_TOKENS = ("1", "on", "yes", "true", "enable", "enabled")

# KB push setting (feature 260630-kb-push-flag). Independent of autocommit: when
# autocommit is ON, this flag controls whether committed KB changes are also pushed
# to the remote. Resolution precedence: AGENTWARE_KB_PUSH env -> config.env ->
# default ON (byte-identical to today when unset). Push only matters when autocommit
# is ON (push is gated under commit); when autocommit is OFF, push is moot.
# Reuses the same _parse_autocommit/_parse_autocommit_strict token parsers.
PUSH_KEY = "AGENTWARE_KB_PUSH"
PUSH_DEFAULT = "1"

# Retrieval mode setting (feature 260625-semantic-retrieval-benchmark, Phase 3).
# Resolution precedence: AGENTWARE_RETRIEVAL_MODE env -> config.env -> default
# 'deterministic' (Mode A: pure-stdlib BM25 (+ACR), zero-install, byte-identical
# forever). 'semantic' (Mode B) adds a LOCAL embedding model (hybrid BM25+embed
# via RRF). Mode B is OPT-IN and requires a local model; if that model is absent
# the EFFECTIVE mode gracefully falls back to 'deterministic' with a notice
# (never crash, never silently mislead). The CONFIGURED value is what the
# operator chose; the EFFECTIVE value is what actually runs given model presence.
RETRIEVAL_MODE_KEY = "AGENTWARE_RETRIEVAL_MODE"
RETRIEVAL_MODE_DETERMINISTIC = "deterministic"
RETRIEVAL_MODE_SEMANTIC = "semantic"
RETRIEVAL_MODE_DEFAULT = RETRIEVAL_MODE_DETERMINISTIC
# Accept friendly aliases (A/B etc.) so the onboarding prompt + CLI are forgiving.
_RETRIEVAL_MODE_DETERMINISTIC_TOKENS = (
    "deterministic", "a", "bm25", "default", "det", "modea", "mode-a")
_RETRIEVAL_MODE_SEMANTIC_TOKENS = (
    "semantic", "b", "embed", "hybrid", "local", "modeb", "mode-b")

# Workspace KB mode (feature 260625-team-mode-onboarding-fork). DISTINCT from the
# retrieval mode above: this records whether the operator runs a SOLO local KB
# ('power') or a SHARED git KB with per-user provenance ('team'). Named
# AGENTWARE_KB_MODE / --set-kb-mode / --kb-mode-only to avoid colliding with the
# pre-existing retrieval `--set-mode` (deterministic|semantic). Resolution
# precedence: AGENTWARE_KB_MODE env -> config.env -> default 'power' (today's
# solo behavior, byte-unchanged). This setting steers ONBOARDING only; it changes
# no ranking math (INV-1).
KB_MODE_KEY = "AGENTWARE_KB_MODE"
KB_MODE_POWER = "power"
KB_MODE_TEAM = "team"
KB_MODE_DEFAULT = KB_MODE_POWER
_KB_MODE_POWER_TOKENS = ("power", "p", "solo", "local", "personal", "default")
_KB_MODE_TEAM_TOKENS = ("team", "t", "shared", "git", "group")

# Per-user provenance handle (feature 260625-team-mode-onboarding-fork). In a
# SHARED (team) KB many humans write to one index, so each writer stamps their
# OWN handle as the entry `author` — distinct from the KB-wide `**Handle**:` in
# MAIN.md (the _operator_handle default). Resolution precedence:
# AGENTWARE_USER_HANDLE env -> config.env -> None (fall back to _operator_handle).
# Stored ONLY in ~/.agentware/config.env + external-KB frontmatter, NEVER in the
# package (R-LOC-03).
USER_HANDLE_KEY = "AGENTWARE_USER_HANDLE"

# Runtime CLI (feature 260627-codex-runtime-adapter). agentware is runtime-
# agnostic: the loop spawns an autonomous coding agent per phase. This records
# WHICH backend that is — 'claude' (Claude Code) or 'codex' (OpenAI Codex). It is
# the single source of truth the loop's run_agent() adapter and onboarding both
# read. Resolution precedence: AGENTWARE_CLI env -> config.env -> default
# 'claude' (today's behavior, byte-unchanged for existing users). This setting
# only steers WHICH argv the loop composes; it changes no ranking math (INV-1).
CLI_KEY = "AGENTWARE_CLI"
CLI_CLAUDE = "claude"
CLI_CODEX = "codex"
CLI_DEFAULT = CLI_CLAUDE
_CLI_CLAUDE_TOKENS = ("claude", "claude-code", "claudecode", "cc", "default")
_CLI_CODEX_TOKENS = ("codex", "openai-codex", "openai", "codex-cli")

# Global model knob (no setter today — read straight from the env by agentware.sh
# as AGENTWARE_MODEL). Named here so the per-phase resolvers can fall back to it
# (env then config.env) for backward-compatibility. Default unset => the spawned
# subagent picks its own model (today's behavior, byte-unchanged).
MODEL_KEY = "AGENTWARE_MODEL"

# Per-phase runtime routing (feature 260628-hybrid-local-model). agentware's loop
# runs THREE phases — pre (plan), main (execute), post (assess). Each phase can be
# routed to its own runtime CLI + model + LOCAL serving provider, enabling the
# default HYBRID profile (plan/assess on cloud Claude, execute on a LOCAL model via
# the Codex --oss adapter). Three axes per phase: CLI (claude|codex), MODEL (free
# vendor id), LOCAL (lmstudio|ollama — which local OpenAI-compatible server Codex's
# --oss targets; unset => a cloud route).
#
# Resolution precedence per (phase, axis), STRICTLY backward-compatible:
#   phase env  ->  phase config.env  ->  GLOBAL (AGENTWARE_CLI/AGENTWARE_MODEL)  ->  default
# With NO per-phase keys set, every phase resolves to exactly the global value:
#   CLI -> resolve_cli() (default 'claude'); MODEL -> AGENTWARE_MODEL (default
#   unset); LOCAL -> unset (cloud). That is byte-identical to today. The LOCAL axis
#   has no global fallback (it is inherently per-phase) and defaults to unset. The
#   loop resolves each phase ONCE at run start and treats it as immutable mid-run.
PHASES = ("pre", "main", "post")
# Local OpenAI-compatible serving provider for Codex --oss. Strict token set.
_LOCAL_TOKENS = ("lmstudio", "ollama")

# Pluggable LOCAL embedder backend (feature 260625-semantic-retrieval-benchmark,
# Phase 3.2). Mode B (semantic) needs a LOCAL embedding model. The backend is
# OPTIONAL and NEVER hard-imported: it is resolved from a runtime config string
# and loaded via importlib (see load_embedder), so this script's static-import
# surface stays stdlib-only (C-dep guard / INV-6) and Mode A keeps running with
# nothing installed. AGENTWARE_EMBEDDER_BACKEND is either a dotted module name
# (importable on sys.path) or a filesystem path to a .py file exposing the
# contract `get_embedder(model=None) -> Embedder` where Embedder.embed(texts)
# returns fixed-dim deterministic vectors. Unset => no semantic backend (the
# zero-install default). AGENTWARE_EMBED_MODEL is the model id passed to the
# backend factory (e.g. the reference Ollama backend's `nomic-embed-text`).
EMBEDDER_BACKEND_KEY = "AGENTWARE_EMBEDDER_BACKEND"
EMBED_MODEL_KEY = "AGENTWARE_EMBED_MODEL"
EMBED_MODEL_DEFAULT = "nomic-embed-text"

# Dream mode (feature 260627-dream-mode, Phase 1): a scheduled, idle-gated,
# unattended DETERMINISTIC KB-maintenance cycle that moves all size-scaling
# maintenance OFF the interactive hot path. Resolution precedence mirrors the
# other SETTINGS_AW flags: AGENTWARE_DREAM env -> config.env -> default OFF.
# Dream is OPT-IN: it ships disabled and runs NOTHING until the operator enables
# it (default '0'). A typo never silently enables it (lenient parse => None =>
# fall through to the safe OFF default). The resolved value is '1' (on)/'0' (off).
DREAM_KEY = "AGENTWARE_DREAM"
DREAM_DEFAULT = "0"
_DREAM_OFF_TOKENS = ("0", "off", "no", "false", "disable", "disabled")
_DREAM_ON_TOKENS = ("1", "on", "yes", "true", "enable", "enabled")

# Dream schedule (feature 260627-dream-mode): the nightly run time the scheduler
# installer (Task 10) wires into launchd/cron. Either a nightly "HH:MM" 24h token
# or a 5-field cron expression. Default UNSET (None) — no schedule until the
# operator sets one. Resolution: AGENTWARE_DREAM_SCHEDULE env -> config.env -> None.
DREAM_SCHEDULE_KEY = "AGENTWARE_DREAM_SCHEDULE"

# Dream max-runtime guard (feature 260628-dream-observability, Task 7): a
# best-effort, FULLY OFFLINE wall-clock cap (seconds) so a hung/runaway step can't
# let the cycle run unbounded. Checked BETWEEN steps (no signals/threads — see the
# DECISION in the feature worklog); on trip the remaining steps are stopped, a
# PARTIAL cycle + trip reason are recorded, and the process exits non-zero.
# Generous default (30m) so it only ever catches a genuine hang. 0 = disabled (no
# cap). Resolution mirrors the other SETTINGS_AW flags: env -> config.env ->
# default. NO outbound ping / external monitoring — dream stays 100% offline.
DREAM_MAX_RUNTIME_KEY = "AGENTWARE_DREAM_MAX_RUNTIME"
DREAM_MAX_RUNTIME_DEFAULT = 1800   # 30 minutes
_DREAM_MAX_RUNTIME_OFF_TOKENS = ("0", "off", "none", "disable", "disabled",
                                 "unlimited")

# Skill-proposal ripeness threshold (feature 260722-roster-field-mapping-lint,
# Part 2, Task 8): a proposal is "ripe" for human-gated promotion once its
# unique-ref proposal_count reaches this many DISTINCT features. Resolution
# mirrors the other SETTINGS_AW flags: AGENTWARE_SKILL_PROPOSE_THRESHOLD env ->
# config.env -> default (5). A non-positive / non-integer value falls through to
# the safe default so a typo never disables ripeness.
SKILL_PROPOSE_THRESHOLD_KEY = "AGENTWARE_SKILL_PROPOSE_THRESHOLD"
SKILL_PROPOSE_THRESHOLD_DEFAULT = 5


# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "_CONFIG_HOME",
    "HOME_CONFIG",
    "REPO_CONFIG",
    "CONFIG_PATHS",
    "CONFIG_KEY",
    "AUTOCOMMIT_KEY",
    "AUTOCOMMIT_DEFAULT",
    "_AUTOCOMMIT_OFF_TOKENS",
    "_AUTOCOMMIT_ON_TOKENS",
    "PUSH_KEY",
    "PUSH_DEFAULT",
    "RETRIEVAL_MODE_KEY",
    "RETRIEVAL_MODE_DETERMINISTIC",
    "RETRIEVAL_MODE_SEMANTIC",
    "RETRIEVAL_MODE_DEFAULT",
    "_RETRIEVAL_MODE_DETERMINISTIC_TOKENS",
    "_RETRIEVAL_MODE_SEMANTIC_TOKENS",
    "KB_MODE_KEY",
    "KB_MODE_POWER",
    "KB_MODE_TEAM",
    "KB_MODE_DEFAULT",
    "_KB_MODE_POWER_TOKENS",
    "_KB_MODE_TEAM_TOKENS",
    "USER_HANDLE_KEY",
    "CLI_KEY",
    "CLI_CLAUDE",
    "CLI_CODEX",
    "CLI_DEFAULT",
    "_CLI_CLAUDE_TOKENS",
    "_CLI_CODEX_TOKENS",
    "MODEL_KEY",
    "PHASES",
    "_LOCAL_TOKENS",
    "EMBEDDER_BACKEND_KEY",
    "EMBED_MODEL_KEY",
    "EMBED_MODEL_DEFAULT",
    "DREAM_KEY",
    "DREAM_DEFAULT",
    "_DREAM_OFF_TOKENS",
    "_DREAM_ON_TOKENS",
    "DREAM_SCHEDULE_KEY",
    "DREAM_MAX_RUNTIME_KEY",
    "DREAM_MAX_RUNTIME_DEFAULT",
    "_DREAM_MAX_RUNTIME_OFF_TOKENS",
    "SKILL_PROPOSE_THRESHOLD_KEY",
    "SKILL_PROPOSE_THRESHOLD_DEFAULT",
]
