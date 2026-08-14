"""agentware_lib.features.configcmd.cfgkbgit — KB autocommit + push settings.

Owns: the `config` handlers for the KB-GIT WRITE-BACK family — --set-autocommit /
      --kb-autocommit-only and --set-push / --kb-push-only. Both setters are
      knowledge-dir FREE: they never require a configured KB.
Does NOT own: the git work-tree operations themselves -> agentware_lib.ops.kbgit /
      agentware_lib.ops.kbsync; the on|off token PARSER (_parse_autocommit_strict)
      and the *_KEY constants -> agentware_lib.core.config; the flag->handler
      ORDER -> agentware_lib.features.configcmd.cfgroutes.
Layer deps: features.configcmd.cfgkbgit -> core (config) -- DOWNWARD for the
      features layer.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Both setters share the EXACT strict-token-parse + _set_config_value + echo shape
the whole settings surface uses, so a future on|off flag slots in as one more
handler plus one more cfgroutes row.
"""

import json
import sys

import agentware_lib.core.config as config


def _set_autocommit(args, as_json):
    """--set-autocommit on|off: persist to ~/.agentware/config.env, then report."""
    set_val = getattr(args, "set_autocommit", None)
    norm = config._parse_autocommit_strict(set_val)
    if norm is None:
        sys.stderr.write(
            "agentware: invalid --set-autocommit value %r (use on|off|1|0|yes|no)\n"
            % set_val)
        return 2
    config._set_config_value(config.AUTOCOMMIT_KEY, norm)
    if as_json:
        json.dump({"ok": True, "kb_autocommit": norm, "config": config.HOME_CONFIG},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Set %s=%s in %s (KB autocommit %s)"
              % (config.AUTOCOMMIT_KEY, norm, config.HOME_CONFIG,
                 "ON" if norm == "1" else "OFF"))
    return 0


def _set_push(args, as_json):
    """--set-push on|off: persist to ~/.agentware/config.env, then report."""
    set_push_val = getattr(args, "set_push", None)
    norm = config._parse_autocommit_strict(set_push_val)
    if norm is None:
        sys.stderr.write(
            "agentware: invalid --set-push value %r (use on|off|1|0|yes|no)\n"
            % set_push_val)
        return 2
    config._set_config_value(config.PUSH_KEY, norm)
    if as_json:
        json.dump({"ok": True, "kb_push": norm, "config": config.HOME_CONFIG},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Set %s=%s in %s (KB push %s)"
              % (config.PUSH_KEY, norm, config.HOME_CONFIG,
                 "ON" if norm == "1" else "OFF"))
    return 0


def _kb_autocommit_only(args, as_json):
    """--kb-autocommit-only: bare resolved setting for shell consumers (the loop)."""
    print(config.resolve_kb_autocommit())
    return 0


def _kb_push_only(args, as_json):
    """--kb-push-only: bare resolved setting for shell consumers (the loop)."""
    print(config.resolve_kb_push())
    return 0


_EXPORTS = []

__all__ = list(_EXPORTS)
