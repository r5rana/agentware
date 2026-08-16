"""agentware_lib.core.config.configresolve — env->config.env->default resolution (layer: core).

Owns: the ONE resolution precedence every layer shares (env -> config.env
HOME->REPO -> default) and the readers built on it: the raw config.env key read
(_read_config_key), the shared env-then-config key resolver
(_resolve_key_env_then_config), resolve_knowledge_dir + require_knowledge_dir
(the knowledge-dir pointer, its org-mode fallback and the uniform "not
configured" error), and the per-setting resolvers resolve_kb_autocommit /
resolve_kb_push / resolve_retrieval_mode / resolve_kb_mode / resolve_cli /
resolve_skill_propose_threshold.
Does NOT own: the key/default constants it resolves (agentware_lib.core.config.
configkeys); the token PARSERS it calls (…config.configvalidate); the config.env
WRITERS (…config.configwrite); the org-root/team/project resolvers and the
org-identity guard it composes with (…config.orgresolve); the per-phase/dream/
embedder resolvers that route these settings (agentware_lib.core.routing).
Layer deps: core -> core only (imports its configkeys sibling + the aggregator package; nothing above).
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

LATE BINDING (load-bearing): every reference to another config symbol is resolved
through the aggregator package (`config.<name>`) rather than a local copy, because
the suite monkeypatches the MUTABLE globals HOME_CONFIG/CONFIG_PATHS — and the
resolvers themselves — on `agentware_lib.core.config.<name>`. Reading them through
the package keeps the aggregator namespace behaving exactly like the pre-split
single module, and keeps this submodule free of a module-level sibling edge (no
import cycle with orgresolve, which calls back into _resolve_key_env_then_config).
"""

import os
import sys

import agentware_lib.core.config as config

from .configkeys import (
    AUTOCOMMIT_DEFAULT,
    AUTOCOMMIT_KEY,
    CLI_DEFAULT,
    CLI_KEY,
    KB_MODE_DEFAULT,
    KB_MODE_KEY,
    PUSH_DEFAULT,
    PUSH_KEY,
    RETRIEVAL_MODE_DEFAULT,
    RETRIEVAL_MODE_KEY,
    SKILL_PROMOTE_CAP_DEFAULT,
    SKILL_PROMOTE_CAP_KEY,
    SKILL_PROMOTE_DEFAULT,
    SKILL_PROMOTE_KEY,
    SKILL_PROPOSE_THRESHOLD_DEFAULT,
    SKILL_PROPOSE_THRESHOLD_KEY,
)


# --- Knowledge-dir resolution ------------------------------------------------
def _resolve_key_env_then_config(key):
    """Value for KEY from env first, then config.env (HOME→REPO). None if unset.

    Shared resolver used by the org-root/team keys so their precedence matches
    the knowledge-dir key exactly. Empty/whitespace values are treated as unset.
    """
    env = os.environ.get(key)
    if env and env.strip():
        return env.strip()
    return config._read_config_key(key)


def resolve_knowledge_dir():
    """Resolve the knowledge directory, or return None if unconfigured.

    Precedence (backward-compatible; INV-1):
      1. AGENTWARE_KNOWLEDGE_DIR (env, then config.env) — WINS, byte-identical to
         today when set.
      2. ELSE AGENTWARE_ORG_ROOT + AGENTWARE_TEAM → <org-root>/teams/<team>.
      3. ELSE None.
    Always returns an absolute, user-expanded path.
    """
    env = os.environ.get(config.CONFIG_KEY)
    if env and env.strip():
        return os.path.abspath(os.path.expanduser(env.strip()))
    for cfg in config.CONFIG_PATHS:
        if not os.path.isfile(cfg):
            continue
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    if key.strip() == config.CONFIG_KEY:
                        val = val.strip().strip('"').strip("'")
                        if val:
                            return os.path.abspath(os.path.expanduser(val))
        except OSError:
            continue
    # Fallback: org-root + team compose the knowledge dir (org mode). Only reached
    # when the direct pointer is unset, so single-team installs are unaffected.
    return config.resolve_org_team_dir()


def _read_config_key(key):
    """Read KEY=VALUE from the first config.env that defines it (None if absent).

    Mirrors resolve_knowledge_dir's file precedence (HOME then REPO). Values may
    be quoted; surrounding quotes are stripped.
    """
    for cfg in config.CONFIG_PATHS:
        if not os.path.isfile(cfg):
            continue
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("#") or "=" not in s:
                        continue
                    k, _, val = s.partition("=")
                    if k.strip() == key:
                        return val.strip().strip('"').strip("'")
        except OSError:
            continue
    return None


def resolve_kb_autocommit():
    """Resolve the effective KB-autocommit setting as '1' (on) or '0' (off).

    Precedence: AGENTWARE_KB_AUTOCOMMIT env var -> config.env -> default ON.
    This is the SINGLE source of truth read by agentware.sh
    (`config --kb-autocommit-only`).
    """
    env = config._parse_autocommit(os.environ.get(AUTOCOMMIT_KEY))
    if env is not None:
        return env
    cfg = config._parse_autocommit(config._read_config_key(AUTOCOMMIT_KEY))
    if cfg is not None:
        return cfg
    return AUTOCOMMIT_DEFAULT


def resolve_kb_push():
    """Resolve the effective KB-push setting as '1' (on) or '0' (off).

    Precedence: AGENTWARE_KB_PUSH env var -> config.env -> default ON.
    This is the SINGLE source of truth read by agentware.sh
    (`config --kb-push-only`). Push is independent of commit but gated under
    it — push only matters when autocommit is ON; when autocommit is OFF, push
    is moot (nothing to push). Reuses the same token parsers as autocommit.
    """
    env = config._parse_autocommit(os.environ.get(PUSH_KEY))
    if env is not None:
        return env
    cfg = config._parse_autocommit(config._read_config_key(PUSH_KEY))
    if cfg is not None:
        return cfg
    return PUSH_DEFAULT


def resolve_retrieval_mode():
    """Resolve the CONFIGURED retrieval mode ('deterministic' | 'semantic').

    Precedence: AGENTWARE_RETRIEVAL_MODE env -> config.env -> default
    'deterministic'. This is what the operator CHOSE. The EFFECTIVE mode (what
    actually runs) additionally requires a local model when 'semantic' — see
    resolve_effective_retrieval_mode().
    """
    env = config._parse_retrieval_mode(os.environ.get(RETRIEVAL_MODE_KEY))
    if env is not None:
        return env
    cfg = config._parse_retrieval_mode(config._read_config_key(RETRIEVAL_MODE_KEY))
    if cfg is not None:
        return cfg
    return RETRIEVAL_MODE_DEFAULT


def resolve_kb_mode():
    """Resolve the configured workspace KB mode ('power' | 'team').

    Precedence: AGENTWARE_KB_MODE env -> config.env -> default 'power'. Steers
    onboarding only; never touches ranking (INV-1).
    """
    env = config._parse_kb_mode(os.environ.get(KB_MODE_KEY))
    if env is not None:
        return env
    cfg = config._parse_kb_mode(config._read_config_key(KB_MODE_KEY))
    if cfg is not None:
        return cfg
    return KB_MODE_DEFAULT


def resolve_cli():
    """Resolve the configured runtime CLI ('claude' | 'codex').

    Precedence: AGENTWARE_CLI env -> config.env -> default 'claude'. This is the
    single source of truth the loop's run_agent() adapter and onboarding read.
    """
    env = config._parse_cli(os.environ.get(CLI_KEY))
    if env is not None:
        return env
    cfg = config._parse_cli(config._read_config_key(CLI_KEY))
    if cfg is not None:
        return cfg
    return CLI_DEFAULT


def require_knowledge_dir():
    """Resolve the knowledge dir or print a uniform error and return None."""
    kdir = config.resolve_knowledge_dir()
    if not kdir:
        sys.stderr.write(
            "agentware: knowledge directory is not configured.\n"
            "  Run: scripts/agentware init --knowledge-dir <path>\n"
            "  Or set the AGENTWARE_KNOWLEDGE_DIR environment variable.\n")
        return None
    if not os.path.isdir(kdir):
        sys.stderr.write(
            "agentware: configured knowledge directory does not exist: %s\n"
            "  Run: scripts/agentware init --knowledge-dir %s\n" % (kdir, kdir))
        return None
    # Source-of-truth guard (only when an org-project key is set): refuse a
    # resolved org root whose standard project markers do NOT declare that
    # project — so a stray path (e.g. a leftover /tmp scratch in config) fails
    # loudly instead of silently becoming the live KB. No-op when unset.
    ident_err = config.org_project_identity_error(config.resolve_org_root())
    if ident_err:
        sys.stderr.write("agentware: %s\n" % ident_err)
        return None
    return kdir


def resolve_skill_propose_threshold():
    """Resolve the "ripe" threshold for skill proposals (default 5).

    Precedence: AGENTWARE_SKILL_PROPOSE_THRESHOLD env -> config.env -> 5. A
    proposal is "ripe" (surfaced for human-gated promotion) when its unique-ref
    proposal_count >= this threshold. The `config --set-skill-propose-threshold`
    / `--skill-propose-threshold-only` accessor pair persists + reads it (Task 8).
    """
    default = SKILL_PROPOSE_THRESHOLD_DEFAULT
    raw = os.environ.get(SKILL_PROPOSE_THRESHOLD_KEY)
    if raw is None:
        raw = config._read_config_key(SKILL_PROPOSE_THRESHOLD_KEY)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        val = int(str(raw).strip())
    except (ValueError, TypeError):
        return default
    return val if val > 0 else default



# --- Skill-promotion settings (feature 260722-auto-skill-promotion) ----------
# NOTE ON THE ONE DELIBERATE DIVERGENCE FROM THE PRE-MOVE BODIES: the monolith
# called `_parse_*` and `_read_config_key` by BARE NAME, because every symbol
# shared one module namespace there. Here they are reached through the `config`
# aggregator alias, exactly as every resolver already in this module does. That
# is load-bearing, not cosmetic — the suite monkeypatches these on
# `agentware_lib.core.config.<name>`, and a bare name would bind the local
# definition at call time and silently ignore the patch.
def resolve_skill_promote():
    """Resolve the effective skill-promotion mode ('propose'|'off').

    Precedence: AGENTWARE_SKILL_PROMOTE env -> config.env -> default 'propose'.
    Read per-run (no daemon); the dream step + the `skill propose` verb consult it.
    """
    env = config._parse_skill_promote(os.environ.get(SKILL_PROMOTE_KEY))
    if env is not None:
        return env
    cfg = config._parse_skill_promote(config._read_config_key(SKILL_PROMOTE_KEY))
    if cfg is not None:
        return cfg
    return SKILL_PROMOTE_DEFAULT


def resolve_skill_promote_cap():
    """Resolve the per-run skill-candidate emission cap (>=0; 0 = report-only).

    Precedence: AGENTWARE_SKILL_PROMOTE_CAP env -> config.env -> default. Read
    per-run; a hand-edited typo degrades to the default rather than 0/unbounded.
    """
    env = config._parse_skill_promote_cap(os.environ.get(SKILL_PROMOTE_CAP_KEY))
    if env is not None:
        return env
    cfg = config._parse_skill_promote_cap(
        config._read_config_key(SKILL_PROMOTE_CAP_KEY))
    if cfg is not None:
        return cfg
    return SKILL_PROMOTE_CAP_DEFAULT


def _resolve_unit_threshold(key, default):
    """env -> config.env -> default resolution for a [0,1] float SETTINGS_AW key."""
    env = config._parse_unit_threshold(os.environ.get(key))
    if env is not None:
        return env
    cfg = config._parse_unit_threshold(config._read_config_key(key))
    if cfg is not None:
        return cfg
    return default



# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "_resolve_key_env_then_config",
    "resolve_knowledge_dir",
    "_read_config_key",
    "resolve_kb_autocommit",
    "resolve_kb_push",
    "resolve_retrieval_mode",
    "resolve_kb_mode",
    "resolve_cli",
    "require_knowledge_dir",
    "resolve_skill_propose_threshold",
    "resolve_skill_promote",
    "resolve_skill_promote_cap",
    "_resolve_unit_threshold",
]
