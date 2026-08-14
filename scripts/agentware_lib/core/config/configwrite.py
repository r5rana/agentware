"""agentware_lib.core.config.configwrite — the config.env writers (layer: core).

Owns: every mutation of ~/.agentware/config.env — the standard file header
(_CONFIG_HEADER), the upsert/remove primitives (_set_config_value /
_unset_config_value), the knowledge-dir pin (_write_config), and
_persisted_knowledge_dir: the HOME_CONFIG-ONLY pointer read that cmd_init's
clobber guard compares against (deliberately scoped to the exact file
_write_config overwrites, so it must NOT go through the general resolver).
Does NOT own: the file LOCATIONS it writes (agentware_lib.core.config.configkeys
— HOME_CONFIG/CONFIG_KEY are read late-bound through the aggregator); the
env->config.env->default READ precedence (…config.configresolve); the value
validators that gate a write (…config.configvalidate); the `config --set-*` /
`init` command handlers that call these (agentware_lib.features.configcmd and
agentware_lib.features.setupcmd).
Layer deps: core -> core only (imports the aggregator package; nothing above).
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Every writer targets HOME_CONFIG (never the repo config) so the orchestrator
package stays pristine, and every writer preserves the lines it does not own —
config.env is hand-editable and holds keys agentware never reads. HOME_CONFIG and
CONFIG_KEY are resolved late-bound through the aggregator package because the
suite repoints them at a temp file on `agentware_lib.core.config.<name>`.
"""

import os

import agentware_lib.core.config as config


_CONFIG_HEADER = (
    "# agentware config — machine-specific, points at this operator's",
    "# EXTERNAL knowledge base so the orchestrator package stays read-only.",
)


def _set_config_value(key, value):
    """Upsert KEY=VALUE in HOME_CONFIG, preserving every other line.

    Creates the file (with the standard header) if absent. Replaces the FIRST
    existing definition of KEY and drops any later duplicates, so the setting is
    written exactly once. Kept in HOME (not the repo) so the package stays pristine.
    """
    home_config = config.HOME_CONFIG
    os.makedirs(os.path.dirname(home_config), exist_ok=True)
    if os.path.isfile(home_config):
        with open(home_config, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    else:
        lines = list(_CONFIG_HEADER)
    out, replaced = [], False
    for line in lines:
        s = line.strip()
        if not s.startswith("#") and "=" in s and s.partition("=")[0].strip() == key:
            if not replaced:
                out.append("%s=%s" % (key, value))
                replaced = True
            # else: drop the duplicate definition
        else:
            out.append(line)
    if not replaced:
        out.append("%s=%s" % (key, value))
    with open(home_config, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def _unset_config_value(key):
    """Remove KEY from HOME_CONFIG entirely (preserve every other line). No-op if absent.

    Companion to _set_config_value — needed to switch an operator from the direct
    AGENTWARE_KNOWLEDGE_DIR pointer to ORG_ROOT+TEAM (the direct pointer WINS in the
    resolver, so it must be removed for org-mode precedence to take effect).
    """
    home_config = config.HOME_CONFIG
    if not os.path.isfile(home_config):
        return
    with open(home_config, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    out = [ln for ln in lines
           if ln.strip().startswith("#") or "=" not in ln
           or ln.partition("=")[0].strip() != key]
    with open(home_config, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + ("\n" if out else ""))


def _write_config(kdir):
    """Pin the knowledge dir in ~/.agentware/config.env (preserving other keys).

    Kept in HOME (not the repo) so the orchestrator package stays pristine.
    """
    config._set_config_value(config.CONFIG_KEY, kdir)


def _persisted_knowledge_dir():
    """Read AGENTWARE_KNOWLEDGE_DIR from HOME_CONFIG ONLY (never the env var or
    the legacy repo config), returning an abspath or None if unset/absent.

    Scoped deliberately to HOME_CONFIG because that is the exact file
    `_write_config` overwrites: cmd_init uses this to detect whether it is about
    to silently repoint an already-configured knowledge base.
    """
    home_config = config.HOME_CONFIG
    if not os.path.isfile(home_config):
        return None
    try:
        with open(home_config, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith("#") or "=" not in s:
                    continue
                k, _, val = s.partition("=")
                if k.strip() == config.CONFIG_KEY:
                    val = val.strip().strip('"').strip("'")
                    if val:
                        return os.path.abspath(os.path.expanduser(val))
    except OSError:
        return None


# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "_CONFIG_HEADER",
    "_set_config_value",
    "_unset_config_value",
    "_write_config",
    "_persisted_knowledge_dir",
]
