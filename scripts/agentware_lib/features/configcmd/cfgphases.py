"""agentware_lib.features.configcmd.cfgphases — per-phase routing settings.

Owns: the `config` handlers for the PER-PHASE ROUTING family
      (260628-hybrid-local-model) — the 3 phases (pre/main/post) x 3 axes
      (cli/model/local) grid of --set-<phase>-<axis> writers and
      --<phase>-<axis>-only readers, plus the group predicate the cfgroutes table
      fires them on.
Does NOT own: the GLOBAL --set-cli / --cli-only (the un-phased default) -> the
      sibling agentware_lib.features.configcmd.cfgdream; the phase NAME list
      (config.PHASES), the strict token PARSERS and the phase KEY composer
      (routing._phase_key) + the resolvers -> agentware_lib.core.config +
      agentware_lib.core.routing; the loop that CONSUMES the resolved phase
      routing -> agentware.sh / the run_agent adapter; the flag->handler ORDER ->
      agentware_lib.features.configcmd.cfgroutes.
Layer deps: features.configcmd.cfgphases -> core (config, routing) -- DOWNWARD
      for the features layer.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

The pre-split cmd_config handled these 18 flags GENERICALLY, in one nested loop
sitting between the --set-cli branch and the --set-dream branch, so this family
keeps a GROUP predicate (_phase_triggered) alongside one grouped handler rather
than 18 rows in the cfgroutes table — exactly the shape cfgorg uses for its own
multi-flag branch. _phase_triggered is True on precisely the (phase, axis) pairs
the loop returns on, so the grouped row fires iff the pre-split loop did.

Setters validate strictly and persist to ~/.agentware/config.env; readers print
the bare resolved value for shell consumers (the loop resolves each phase once).
Resolution precedence is phase env -> phase config -> global -> default (see
resolve_phase_*). The _PHASE_AXES table is built INSIDE the handler, exactly as
the pre-split branch built it, so the parser/resolver functions stay late-bound
(a module-level table would freeze them at import and defeat monkeypatching).
"""

import json
import sys

import agentware_lib.core.config as config
import agentware_lib.core.routing as routing

# Axis names only — no function objects, so this literal is safe at module level
# (the callables live in the per-call _PHASE_AXES table inside _handle_phase).
_PHASE_AXIS_NAMES = ("cli", "model", "local")


def _phase_triggered(args):
    """True iff any --set-<phase>-<axis> / --<phase>-<axis>-only flag was passed."""
    for phase in config.PHASES:
        for axis_lc in _PHASE_AXIS_NAMES:
            if getattr(args, "set_%s_%s" % (phase, axis_lc), None) is not None:
                return True
            if getattr(args, "%s_%s_only" % (phase, axis_lc), False):
                return True
    return False


def _handle_phase(args, as_json):
    """The per-phase routing setters/readers (3 phases x 3 axes).

    Generic handling for the 3 phases x 3 axes (CLI/MODEL/LOCAL). Setters
    validate strictly and persist to ~/.agentware/config.env; readers print the
    bare resolved value for shell consumers (the loop resolves each phase once).
    """
    _PHASE_AXES = (
        ("cli", "CLI", config._parse_cli_strict, "claude|codex", routing.resolve_phase_cli),
        ("model", "MODEL", routing._parse_model_strict,
         "a non-empty model id, no whitespace", routing.resolve_phase_model),
        ("local", "LOCAL", routing._parse_local_strict, "lmstudio|ollama",
         routing.resolve_phase_local),
    )
    for phase in config.PHASES:
        for axis_lc, axis_uc, parse_strict, hint, resolver in _PHASE_AXES:
            sval = getattr(args, "set_%s_%s" % (phase, axis_lc), None)
            if sval is not None:
                norm = parse_strict(sval)
                if norm is None:
                    sys.stderr.write(
                        "agentware: invalid --set-%s-%s value %r (use %s)\n"
                        % (phase, axis_lc, sval, hint))
                    return 2
                key = routing._phase_key(phase, axis_uc)
                config._set_config_value(key, norm)
                if as_json:
                    json.dump({"ok": True, "phase": phase, "axis": axis_lc,
                               "key": key, "value": norm, "config": config.HOME_CONFIG},
                              sys.stdout, ensure_ascii=False, indent=2)
                    sys.stdout.write("\n")
                else:
                    print("Set %s=%s in %s (phase %s %s)"
                          % (key, norm, config.HOME_CONFIG, phase, axis_lc))
                return 0
            if getattr(args, "%s_%s_only" % (phase, axis_lc), False):
                val = resolver(phase)
                if val:
                    print(val)
                return 0
    # Unreachable through the cfgroutes table: _phase_triggered is this handler's
    # guard and is True on exactly the pairs the loop above returns on. Kept as an
    # explicit exit code so a direct call never returns None.
    return 0


_EXPORTS = []

__all__ = list(_EXPORTS)
