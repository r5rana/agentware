"""agentware_lib.features.configcmd — the `config` settings command surface (layer: features).

AGGREGATOR ONLY. This package holds NO logic: it re-imports the public names of
its eight domain submodules so every pre-split call site — which reaches the
module under an alias (`from agentware_lib.features import configcmd`) and reads
`configcmd.cmd_config` — keeps working byte-identically after the
split, and so does every existing monkeypatch target (`configcmd.config.<name>`,
`configcmd._vectorcache.<name>`, `configcmd._metrics.<name>`). The submodules:

  * `cfgidentity`  — the knowledge-dir POINTER write (with the kb_pointable
                     target guard) + the provenance handle, ripeness threshold
                     and the onboarding metric.
  * `cfgorg`       — the org-mode keys (root/team/project + the org-mode switch),
                     applied as ONE grouped write, plus the workspace KB mode.
  * `cfgkbgit`     — the KB git write-back settings (autocommit + push).
  * `cfgretrieval` — the retrieval mode (--set-mode + its --set-retrieval alias)
                     and the SETTINGS_AW embedder/model settings.
  * `cfgdream`     — the global runtime CLI + the three dream settings.
  * `cfgphases`    — the 3-phase x 3-axis routing grid (18 flags, one group).
  * `cfgshow`      — the terminal resolved-config VIEW + its exit-code contract.
  * `cfgroutes`    — CONFIG_ROUTES, the ORDER-BEARING flag->handler table.
  * `cfgmain`      — the slim cmd_config route walker + register_config.

Owns: nothing of its own — only the re-export surface that keeps the pre-split
import path and every monkeypatch target resolving unchanged. It deliberately
carries NO `_EXPORTS`: the submodules carry theirs and are the entries the
entrypoint facade iterates, so restating them here would give one symbol two
owners.
Does NOT own: any behaviour — see the submodules above; the pure config
RESOLVERS/WRITERS (resolve_*, _set_config_value, _write_config, the *_KEY
constants) -> agentware_lib.core.config; the org key CONSTANTS ->
agentware_lib.core.orgkeys; the per-phase/dream/embedder RESOLVERS ->
agentware_lib.core.routing; the `init`/`profile` handlers -> the sibling
agentware_lib.features.setupcmd.
Layer deps: features.configcmd -> core (config, orgkeys, routing, kblayout
const), retrieval (vectorcache), ops (metrics) — all DOWNWARD for the features
layer, and all of them owned by the submodules; the aliases below are bound here
only so the pre-split namespace (and its monkeypatch targets) still resolves.
Nothing imports configcmd except cli/the entrypoint facade.
"""

# Namespace parity with the pre-split flat module: tests and callers reach these
# through THIS package (`mock.patch.object(configcmd.config, ...)`,
# `configcmd._vectorcache`, `configcmd.SENTINEL_REL`), so the aliases must keep
# resolving here even though every handler body now lives in a submodule.
import agentware_lib.core.config as config                # noqa: F401
import agentware_lib.core.orgkeys as orgkeys              # noqa: F401
import agentware_lib.core.routing as routing              # noqa: F401
import agentware_lib.ops.metrics as _metrics              # noqa: F401
import agentware_lib.retrieval.vectorcache as _vectorcache  # noqa: F401
from agentware_lib.core.kblayout import SENTINEL_REL      # noqa: F401

from .cfgmain import cmd_config, register_config  # noqa: F401
