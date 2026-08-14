"""agentware_lib.core.config — config.env read/resolve + runtime settings (layer: core).

AGGREGATOR ONLY. This package holds NO logic: it re-imports the public names of
its five submodules so every existing call site (`config.HOME_CONFIG`,
`config.require_knowledge_dir()`, the entrypoint's `_MODULE_ALIASES["config"]`
row, and every monkeypatch target `agentware_lib.core.config.<name>`) keeps
working byte-identically after the split. The submodules:

  * configkeys     — the config.env locations + every SETTINGS_AW key/default/token set
  * configvalidate — the lenient + strict value parsers
  * configresolve  — the env -> config.env (HOME->REPO) -> default resolution
  * configwrite    — the config.env writers (_set/_unset/_write/_persisted)
  * orgresolve     — org root/team/project resolution + org path inference

Owns: nothing of its own. Does NOT own: any behaviour — see the five submodules
above; REPO_ROOT (agentware_lib.core.paths); the org-mode KEY NAMES
(agentware_lib.core.orgkeys); the project MARKER table (agentware_lib.core.
orgidentity); the per-phase/dream/embedder RESOLVERS (agentware_lib.core.routing);
the init/config/profile command HANDLERS (agentware_lib.features.configcmd).
Layer deps: core -> core only (its own submodules + paths/orgkeys/orgidentity; nothing above).
Fan-in: 36 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

This is the settings ROOT: a near-zero-outbound-edge core package. Every higher
layer that needs a setting reads it here, so the whole toolkit shares ONE
resolution precedence (env -> config.env HOME->REPO -> default), which is what
keeps INV-1 byte-identical. The mutable globals HOME_CONFIG/CONFIG_PATHS are
monkeypatched by tests via THIS package (agentware_lib.core.config.<name>), never
the entrypoint facade copy — which is why every submodule reader resolves them
(and its sibling functions) late-bound through this namespace.
"""

# `os`/`sys` are bound here because the bytecode global-resolve gate
# (tests/_global_resolve.py) checks every re-exported function's global loads
# against THIS namespace — the submodule bodies moved verbatim and still load
# `os`/`sys` as globals. `config` is this package itself: the submodules reach
# their siblings late-bound as `config.<name>` (see each submodule's docstring),
# and that alias must resolve here and in the entrypoint facade — where the
# pre-existing `_MODULE_ALIASES["config"]` row already binds it.
import os   # noqa: F401
import sys  # noqa: F401

from agentware_lib.core import orgidentity  # noqa: F401  (config.orgidentity compat)
from agentware_lib.core import orgkeys      # noqa: F401  (namespace parity)
from agentware_lib.core import paths        # noqa: F401  (namespace parity)

from .configkeys import (
    _AUTOCOMMIT_OFF_TOKENS,
    _AUTOCOMMIT_ON_TOKENS,
    _CLI_CLAUDE_TOKENS,
    _CLI_CODEX_TOKENS,
    _CONFIG_HOME,
    _DREAM_MAX_RUNTIME_OFF_TOKENS,
    _DREAM_OFF_TOKENS,
    _DREAM_ON_TOKENS,
    _KB_MODE_POWER_TOKENS,
    _KB_MODE_TEAM_TOKENS,
    _LOCAL_TOKENS,
    _RETRIEVAL_MODE_DETERMINISTIC_TOKENS,
    _RETRIEVAL_MODE_SEMANTIC_TOKENS,
    AUTOCOMMIT_DEFAULT,
    AUTOCOMMIT_KEY,
    CLI_CLAUDE,
    CLI_CODEX,
    CLI_DEFAULT,
    CLI_KEY,
    CONFIG_KEY,
    CONFIG_PATHS,
    DREAM_DEFAULT,
    DREAM_KEY,
    DREAM_MAX_RUNTIME_DEFAULT,
    DREAM_MAX_RUNTIME_KEY,
    DREAM_SCHEDULE_KEY,
    EMBED_MODEL_DEFAULT,
    EMBED_MODEL_KEY,
    EMBEDDER_BACKEND_KEY,
    HOME_CONFIG,
    KB_MODE_DEFAULT,
    KB_MODE_POWER,
    KB_MODE_TEAM,
    KB_MODE_KEY,
    MODEL_KEY,
    PHASES,
    PUSH_DEFAULT,
    PUSH_KEY,
    REPO_CONFIG,
    RETRIEVAL_MODE_DEFAULT,
    RETRIEVAL_MODE_DETERMINISTIC,
    RETRIEVAL_MODE_KEY,
    RETRIEVAL_MODE_SEMANTIC,
    SKILL_PROPOSE_THRESHOLD_DEFAULT,
    SKILL_PROPOSE_THRESHOLD_KEY,
    USER_HANDLE_KEY,
)
from .configvalidate import (
    _parse_autocommit,
    _parse_autocommit_strict,
    _parse_cli,
    _parse_cli_strict,
    _parse_embed_model_strict,
    _parse_embedder_backend_strict,
    _parse_kb_mode,
    _parse_kb_mode_strict,
    _parse_retrieval_mode,
    _parse_retrieval_mode_strict,
    _parse_skill_propose_threshold,
)
from .configresolve import (
    _read_config_key,
    _resolve_key_env_then_config,
    require_knowledge_dir,
    resolve_cli,
    resolve_kb_autocommit,
    resolve_kb_mode,
    resolve_kb_push,
    resolve_knowledge_dir,
    resolve_retrieval_mode,
    resolve_skill_propose_threshold,
)
from .configwrite import (
    _CONFIG_HEADER,
    _persisted_knowledge_dir,
    _set_config_value,
    _unset_config_value,
    _write_config,
)
from .orgresolve import (
    _entry_origin_team,
    _infer_team_from_kdir,
    _kdir_is_shared_root,
    _resolve_org_project,
    org_project_identity_error,
    resolve_org_project,
    resolve_org_root,
    resolve_org_team_dir,
    resolve_team,
)


# The UNION of the five submodules' _EXPORTS, in the pre-split order. The
# entrypoint facade re-exports the SUBMODULES (one owner per symbol, M10), so
# this list is not consumed by the facade loop; it is kept because the package
# namespace is the compatibility surface every caller and monkeypatch uses, and
# tests assert it enumerates exactly the names this namespace binds.
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
    "_resolve_key_env_then_config",
    "resolve_org_root",
    "resolve_team",
    "_resolve_org_project",
    "resolve_org_project",
    "org_project_identity_error",
    "resolve_org_team_dir",
    "resolve_knowledge_dir",
    "_read_config_key",
    "_parse_autocommit",
    "_parse_autocommit_strict",
    "resolve_kb_autocommit",
    "resolve_kb_push",
    "_parse_retrieval_mode",
    "_parse_retrieval_mode_strict",
    "_parse_embedder_backend_strict",
    "_parse_embed_model_strict",
    "resolve_retrieval_mode",
    "_parse_kb_mode",
    "_parse_kb_mode_strict",
    "resolve_kb_mode",
    "_parse_cli",
    "_parse_cli_strict",
    "resolve_cli",
    "require_knowledge_dir",
    "_CONFIG_HEADER",
    "_set_config_value",
    "_unset_config_value",
    "_write_config",
    "_persisted_knowledge_dir",
    "resolve_skill_propose_threshold",
    "_parse_skill_propose_threshold",
    "_kdir_is_shared_root",
    "_infer_team_from_kdir",
    "_entry_origin_team",
]
