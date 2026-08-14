"""agentware_lib.features.configcmd.cfgdream — dream-mode + runtime-CLI settings.

Owns: the `config` handlers for the DREAM + RUNTIME-CLI family — --set-cli /
      --cli-only (260627-codex-runtime-adapter) and the three dream settings
      --set-dream / --dream-only, --set-dream-schedule / --dream-schedule-only
      and --set-dream-max-runtime / --dream-max-runtime-only (260627-dream-mode).
Does NOT own: the PER-PHASE cli/model/local overrides (--set-<phase>-<axis> /
      --<phase>-<axis>-only) -> the sibling
      agentware_lib.features.configcmd.cfgphases; the dream cycle itself ->
      agentware_lib.features.dream + agentware_lib.features.dreamsteps; the
      scheduler installer -> agentware_lib.features.dreamsched; the strict token
      PARSERS + RESOLVERS -> agentware_lib.core.config +
      agentware_lib.core.routing; the flag->handler ORDER ->
      agentware_lib.features.configcmd.cfgroutes.
Layer deps: features.configcmd.cfgdream -> core (config, routing) -- DOWNWARD for
      the features layer.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Every handler here takes ``(args, as_json)`` and returns the process exit code,
byte-identical to the corresponding branch of the pre-split 622-line cmd_config.
--set-cli is the single source of truth the loop's run_agent() adapter reads;
the dream setters are the ones the scheduler installer and the `dream` idle-gate
read back. Every setter is fully offline — no network/ping anywhere (Task 7). The
BRANCH ORDER is not encoded here — it is the cfgroutes table's single job.
"""

import json
import sys

import agentware_lib.core.config as config
import agentware_lib.core.routing as routing


def _set_cli(args, as_json):
    """--set-cli claude|codex: persist the runtime CLI (260627-codex-runtime-adapter).

    Written to ~/.agentware/config.env; this is the single source of truth the
    loop's run_agent() adapter reads.
    """
    cli_val = getattr(args, "set_cli", None)
    norm = config._parse_cli_strict(cli_val)
    if norm is None:
        sys.stderr.write(
            "agentware: invalid --set-cli value %r (use claude|codex)\n"
            % cli_val)
        return 2
    config._set_config_value(config.CLI_KEY, norm)
    if as_json:
        json.dump({"ok": True, "cli": norm, "config": config.HOME_CONFIG},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Set %s=%s in %s (runtime CLI %s)"
              % (config.CLI_KEY, norm, config.HOME_CONFIG, norm))
    return 0


def _set_dream(args, as_json):
    """--set-dream on|off: persist the dream-mode opt-in (260627-dream-mode).

    Written to ~/.agentware/config.env. Default OFF; env overrides per-run.
    """
    dream_val = getattr(args, "set_dream", None)
    norm = routing._parse_dream_strict(dream_val)
    if norm is None:
        sys.stderr.write(
            "agentware: invalid --set-dream value %r (use on|off|1|0|yes|no)\n"
            % dream_val)
        return 2
    config._set_config_value(config.DREAM_KEY, norm)
    if as_json:
        json.dump({"ok": True, "dream": norm, "config": config.HOME_CONFIG},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Set %s=%s in %s (dream mode %s)"
              % (config.DREAM_KEY, norm, config.HOME_CONFIG,
                 "ON" if norm == "1" else "OFF"))
    return 0


def _set_dream_schedule(args, as_json):
    """--set-dream-schedule <HH:MM|cron>: persist the nightly dream schedule token.

    The token the installer wires into launchd/cron. Invalid tokens are rejected.
    """
    dream_sched_val = getattr(args, "set_dream_schedule", None)
    norm = routing._parse_dream_schedule_strict(dream_sched_val)
    if norm is None:
        sys.stderr.write(
            "agentware: invalid --set-dream-schedule value %r (use a nightly "
            "HH:MM 24h token or a 5-field cron expression)\n" % dream_sched_val)
        return 2
    config._set_config_value(config.DREAM_SCHEDULE_KEY, norm)
    if as_json:
        json.dump({"ok": True, "dream_schedule": norm, "config": config.HOME_CONFIG},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Set %s=%s in %s (dream schedule)"
              % (config.DREAM_SCHEDULE_KEY, norm, config.HOME_CONFIG))
    return 0


def _set_dream_max_runtime(args, as_json):
    """--set-dream-max-runtime <Ns|Nm|Nh|off>: persist the wall-clock cap (seconds).

    Best-effort cap for a dream cycle. 0/off = disabled (no cap). Invalid tokens
    are rejected. Fully offline — no network/ping anywhere (Task 7).
    """
    dream_max_val = getattr(args, "set_dream_max_runtime", None)
    secs = routing._parse_dream_max_runtime(dream_max_val)
    if secs is None:
        sys.stderr.write(
            "agentware: invalid --set-dream-max-runtime value %r (use seconds "
            "or N[s|m|h], e.g. 1800 / 30m / 1h, or off to disable)\n"
            % dream_max_val)
        return 2
    config._set_config_value(config.DREAM_MAX_RUNTIME_KEY, str(secs))
    if as_json:
        json.dump({"ok": True, "dream_max_runtime": secs,
                   "config": config.HOME_CONFIG},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Set %s=%s in %s (dream max-runtime %s)"
              % (config.DREAM_MAX_RUNTIME_KEY, secs, config.HOME_CONFIG,
                 ("%ss" % secs) if secs else "disabled"))
    return 0


def _cli_only(args, as_json):
    """--cli-only: bare resolved runtime CLI (claude|codex) for shell consumers.

    The loop's run_agent() adapter + onboarding alias generation.
    """
    print(config.resolve_cli())
    return 0


def _dream_only(args, as_json):
    """--dream-only: bare resolved dream setting (on/off) for shell consumers.

    The scheduler installer + the `dream` idle-gate. Default OFF.
    """
    print("on" if routing.resolve_dream() == "1" else "off")
    return 0


def _dream_schedule_only(args, as_json):
    """--dream-schedule-only: bare resolved dream schedule token (empty if unset)."""
    sched = routing.resolve_dream_schedule()
    if sched:
        print(sched)
    return 0


def _dream_max_runtime_only(args, as_json):
    """--dream-max-runtime-only: bare resolved max-runtime cap in seconds.

    0 = disabled, for shell consumers.
    """
    print(routing.resolve_dream_max_runtime())
    return 0


_EXPORTS = []

__all__ = list(_EXPORTS)
