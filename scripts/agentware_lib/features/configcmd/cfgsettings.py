"""agentware_lib.features.configcmd.cfgsettings — the PORTED-setting handlers.

Owns: the `config` handlers behind the flags `cfgports` registers — the
      signed-channel updater (--set-updater / --updater-only), the periodic
      self-exam (--set-checkup / --checkup-only), the auto skill-promotion family
      (--set-skill-promote, --set-skill-promote-cap, the three threshold setters,
      --skill-promote-only) and the KB git guards (--install-git-guards /
      --uninstall-git-guards / --repo).
Does NOT own: the flag DECLARATIONS (`cfgports`); the token PARSERS and *_KEY
      constants (`core.config.configvalidate` / `configkeys`, plus
      `ops.checkupconfig` and `features.pkgupdater` for their own settings); the
      guard-hook machinery (`ops.gitguards`); the flag->handler ORDER
      (`cfgroutes`).

WHY THIS MODULE EXISTS
----------------------
`cfgports` registered these flags so `config --set-checkup off` would stop
answering `unrecognized arguments`. It registered the SURFACE only. The handlers
stayed in the preserved monolith, so once `config` routed to the modular core
every one of these flags would have parsed, exited 0 and done NOTHING — an
operator who runs `config --install-git-guards` believes the guards are installed.
A flag that is accepted and ignored is worse than a missing one: the missing flag
tells you, and the ignored flag does not.

Each handler is the monolith's block verbatim: the same strict parse, the same
`_set_config_value`, the same text/JSON echo. Every setter is knowledge-dir FREE
(they persist to ~/.agentware/config.env), and every `*_only` reader prints one
bare token for shell consumers.

Layer deps: features.configcmd.cfgsettings -> core (config), ops (checkupconfig,
      gitguards), features (pkgupdater) -- all DOWNWARD or same-layer.
"""

import json
import sys

import agentware_lib.core.config as config
from agentware_lib.core.config.configvalidate import (
    _parse_skill_promote_cap, _parse_skill_promote_strict, _parse_unit_threshold)
from agentware_lib.ops.checkupconfig import (
    CHECKUP_KEY, _parse_checkup, resolve_checkup)
from agentware_lib.ops.gitguards import (
    _install_git_guards, _uninstall_git_guards)
from agentware_lib.features.pkgupdater import (
    UPDATER_KEY, _parse_updater, resolve_updater)


def _emit_set(as_json, payload, text):
    """The shared echo: JSON body or one human line, then exit 0.

    Every setter in this family ends the same way, and saying so once keeps a new
    setting to one parse + one `_set_config_value` + one call here.
    """
    if as_json:
        json.dump(dict(payload, config=config.HOME_CONFIG),
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(text)
    return 0


def _set_updater(args, as_json):
    """--set-updater on|off: persist the signed-channel updater opt-in."""
    val = getattr(args, "set_updater", None)
    norm = _parse_updater(val)
    if norm is None:
        sys.stderr.write(
            "agentware: invalid --set-updater value %r (use on|off|1|0|yes|no)\n"
            % val)
        return 2
    config._set_config_value(UPDATER_KEY, norm)
    return _emit_set(
        as_json, {"ok": True, "updater": norm},
        "Set %s=%s in %s (automatic updates %s)"
        % (UPDATER_KEY, norm, config.HOME_CONFIG,
           "ON" if norm == "1" else "OFF"))


def _updater_only(args, as_json):
    """--updater-only: the bare resolved setting for shell consumers."""
    print("on" if resolve_updater() == "1" else "off")
    return 0


def _set_checkup(args, as_json):
    """--set-checkup on|off: persist the periodic self-exam opt-in.

    A runtime kill switch — the tick job and the status-line hook SKIP when OFF
    without uninstalling the shared tick.
    """
    val = getattr(args, "set_checkup", None)
    norm = _parse_checkup(val)
    if norm is None:
        sys.stderr.write(
            "agentware: invalid --set-checkup value %r (use on|off|1|0|yes|no)\n"
            % val)
        return 2
    config._set_config_value(CHECKUP_KEY, norm)
    return _emit_set(
        as_json, {"ok": True, "checkup": norm},
        "Set %s=%s in %s (periodic self-exam %s)"
        % (CHECKUP_KEY, norm, config.HOME_CONFIG,
           "ON" if norm == "1" else "OFF"))


def _checkup_only(args, as_json):
    """--checkup-only: the bare resolved setting for shell consumers."""
    print("on" if resolve_checkup() == "1" else "off")
    return 0


def _set_skill_promote(args, as_json):
    """--set-skill-promote off|propose: the emission mode."""
    val = getattr(args, "set_skill_promote", None)
    norm = _parse_skill_promote_strict(val)
    if norm is None:
        sys.stderr.write(
            "agentware: invalid --set-skill-promote value %r (use off|propose)\n"
            % val)
        return 2
    config._set_config_value(config.SKILL_PROMOTE_KEY, norm)
    return _emit_set(
        as_json, {"ok": True, "skill_promote": norm},
        "Set %s=%s in %s (skill promotion %s)"
        % (config.SKILL_PROMOTE_KEY, norm, config.HOME_CONFIG, norm))


def _set_skill_promote_cap(args, as_json):
    """--set-skill-promote-cap N: per-run emission cap (>=0; 0 = report-only)."""
    val = getattr(args, "set_skill_promote_cap", None)
    cap = _parse_skill_promote_cap(val)
    if cap is None:
        sys.stderr.write(
            "agentware: invalid --set-skill-promote-cap value %r (use a "
            "non-negative integer; 0 = detect/report only)\n" % val)
        return 2
    config._set_config_value(config.SKILL_PROMOTE_CAP_KEY, str(cap))
    return _emit_set(
        as_json, {"ok": True, "skill_promote_cap": cap},
        "Set %s=%s in %s (skill-promote per-run cap)"
        % (config.SKILL_PROMOTE_CAP_KEY, cap, config.HOME_CONFIG))


#: The three operator-tunable cluster thresholds, as (flag-suffix, dest, key,
#: label). One table rather than three near-identical handlers, because the only
#: thing that differs between them is which key a float lands in — and three
#: copies is three places for the [0.0, 1.0] validation to drift.
_THRESHOLDS = (
    ("edge-min", "set_skill_promote_edge_min",
     "SKILL_PROMOTE_EDGE_MIN_KEY", "edge-weight"),
    ("density-min", "set_skill_promote_density_min",
     "SKILL_PROMOTE_DENSITY_MIN_KEY", "density"),
    ("separation-min", "set_skill_promote_separation_min",
     "SKILL_PROMOTE_SEPARATION_MIN_KEY", "separation-margin"),
)


def _skill_promote_threshold_triggered(args):
    """True iff any of the three threshold setters was supplied."""
    return any(getattr(args, dest, None) is not None
               for _flag, dest, _key, _label in _THRESHOLDS)


def _set_skill_promote_threshold(args, as_json):
    """--set-skill-promote-{edge,density,separation}-min <0..1>."""
    for flag, dest, keyname, label in _THRESHOLDS:
        val = getattr(args, dest, None)
        if val is None:
            continue
        f = _parse_unit_threshold(val)
        if f is None:
            sys.stderr.write(
                "agentware: invalid --set-skill-promote-%s value %r (use a "
                "float in [0.0, 1.0])\n" % (flag, val))
            return 2
        key = getattr(config, keyname)
        config._set_config_value(key, repr(f))
        return _emit_set(
            as_json, {"ok": True, "key": key, "value": f},
            "Set %s=%s in %s (skill-promote %s threshold)"
            % (key, f, config.HOME_CONFIG, label))
    return 0


def _skill_promote_only(args, as_json):
    """--skill-promote-only: the bare resolved mode (off|propose)."""
    print(config.resolve_skill_promote())
    return 0


def _guards_repo(args):
    """The repository the guard install/uninstall targets.

    Defaults to the resolved knowledge dir — `resolve_knowledge_dir` and NOT
    `require_knowledge_dir`, because these two flags must stay usable against an
    explicit `--repo` on a machine with no KB configured yet.
    """
    return getattr(args, "git_guards_repo", None) or config.resolve_knowledge_dir()


def _install_guards(args, as_json):
    """--install-git-guards: install the KB git guard hooks into a repository."""
    return _install_git_guards(_guards_repo(args), as_json=as_json)


def _uninstall_guards(args, as_json):
    """--uninstall-git-guards: remove hooks this tool installed (marker-scoped)."""
    return _uninstall_git_guards(_guards_repo(args), as_json=as_json)


_EXPORTS = []

__all__ = list(_EXPORTS)
