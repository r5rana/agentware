"""agentware_lib.features.dreamsched -- the OS-agnostic dream SCHEDULER installer (layer: features).

Owns: the opt-in nightly-schedule installer for dream mode -- the DREAM_NICE priority
constant + the launchd/cron generators (_dream_launchd_plist / _dream_cron_line and
their HH:MM/cron parser _dream_schedule_hm), the CLI-path + scheduler-log-path
resolvers (_dream_cli_path / _dream_scheduler_log_path), the plist path helper, and
the install/uninstall entry points (_dream_install_schedule / _dream_uninstall_schedule)
that write ONLY under the operator's HOME. DREAM_NICE is homed HERE (not in the dream
loop) so this module stays a self-contained LEAF the dream loop imports downward,
keeping the dream<->dreamsched split acyclic.
Does NOT own: the dream CYCLE machinery (locks, gate, steps, journal, repoint, cmd_dream)
-- still resident in the entrypoint, arrives in a later wave-6 seam at
agentware_lib/features/dream.py; the dream-health heartbeat -> agentware_lib.features.dream
(see catalog/modules.json).
Layer deps: features.dreamsched -> core (config, paths, routing) only -- all DOWNWARD; nothing above (ops/cli) is imported.
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: the generators are pure string builders (INV-1: byte-stable for fixed
inputs); install/uninstall write only under HOME and are skipped under
AGENTWARE_NESTED_UNITTEST. stdlib-only (INV-6).
"""

import json
import os
import re
import subprocess
import sys

import agentware_lib.core.config as config
import agentware_lib.core.paths as paths
import agentware_lib.core.routing as routing


DREAM_LAUNCHD_LABEL = "com.agentware.dream"


DREAM_CRON_REL = os.path.join(".agentware", "dream.cron")


DREAM_NICE = 10                  # run the cycle at low priority (never compete)


def _dream_cli_path():
    """Absolute path to this CLI (the schedule invokes it directly)."""
    return os.path.join(paths.REPO_ROOT, "scripts", "agentware")


def _dream_scheduler_log_path():
    """Absolute path to the gitignored scheduler stdout/stderr log.

    Defaults to `<knowledge-dir>/logs/dream-scheduler.log` (already gitignored
    via the `logs/` rule in `_ensure_kb_gitignore`). Falls back to
    `~/.agentware/logs/` only when the knowledge dir is unconfigured, so the
    generators are always deterministic and never emit a relative path."""
    kdir = config.resolve_knowledge_dir()
    base = kdir if kdir else os.path.join(os.path.expanduser("~"), ".agentware")
    return os.path.join(base, "logs", "dream-scheduler.log")


def _dream_schedule_hm(schedule):
    """(hour, minute) ints from an HH:MM or 5-field cron token, or None.

    For a cron expression, uses the first two fields when both are plain integers
    (minute, hour); a wildcard/range/list yields None (launchd needs concretes)."""
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", schedule or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    fields = (schedule or "").split()
    if len(fields) == 5 and fields[0].isdigit() and fields[1].isdigit():
        return int(fields[1]), int(fields[0])
    return None


def _dream_launchd_plist(schedule, cli_path, log_path=None):
    """Deterministic LaunchAgent plist string for a nightly low-priority dream.

    Captures the scheduled run's stdout+stderr via StandardOutPath/StandardErrorPath
    (defaulting to the gitignored scheduler log) so a failed cycle is diagnosable
    the morning after instead of vanishing into launchd's void."""
    hm = _dream_schedule_hm(schedule)
    hour, minute = hm if hm else (3, 0)
    log = log_path or _dream_scheduler_log_path()
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        '  <key>Label</key>\n  <string>%s</string>\n'
        '  <key>ProgramArguments</key>\n'
        '  <array>\n'
        '    <string>/usr/bin/nice</string>\n'
        '    <string>-n</string>\n    <string>%d</string>\n'
        '    <string>%s</string>\n'
        '    <string>dream</string>\n'
        '  </array>\n'
        '  <key>StartCalendarInterval</key>\n'
        '  <dict>\n'
        '    <key>Hour</key>\n    <integer>%d</integer>\n'
        '    <key>Minute</key>\n    <integer>%d</integer>\n'
        '  </dict>\n'
        '  <key>StandardOutPath</key>\n  <string>%s</string>\n'
        '  <key>StandardErrorPath</key>\n  <string>%s</string>\n'
        '  <key>RunAtLoad</key>\n  <false/>\n'
        '</dict>\n</plist>\n'
        % (DREAM_LAUNCHD_LABEL, DREAM_NICE, cli_path, hour, minute, log, log))


def _dream_cron_line(schedule, cli_path, log_path=None):
    """Deterministic crontab line for a nightly low-priority dream.

    Appends stdout+stderr to the gitignored scheduler log (`>> <log> 2>&1`)
    instead of discarding them to `/dev/null`, so a scheduled failure is
    diagnosable without re-running."""
    fields = (schedule or "").split()
    if len(fields) == 5:
        spec = " ".join(fields)
    else:
        hm = _dream_schedule_hm(schedule) or (0, 3)  # (minute_src) -> rebuild
        hour, minute = hm
        spec = "%d %d * * *" % (minute, hour)
    log = log_path or _dream_scheduler_log_path()
    return "%s nice -n %d %s dream >> %s 2>&1\n" % (
        spec, DREAM_NICE, cli_path, log)


def _dream_launchd_plist_path():
    return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents",
                        "%s.plist" % DREAM_LAUNCHD_LABEL)


def _dream_install_schedule(kdir, as_json):
    """Install the nightly schedule into the operator's HOME (opt-in). Requires
    dream ON + a configured schedule. macOS -> launchd plist; else -> crontab
    fragment. Fully non-interactive; writes ONLY under HOME."""
    if routing.resolve_dream() != "1":
        sys.stderr.write("agentware: dream is OFF — enable it first "
                         "(`scripts/agentware config --set-dream on`).\n")
        return 2
    schedule = routing.resolve_dream_schedule()
    if not schedule:
        sys.stderr.write("agentware: no dream schedule configured "
                         "(`scripts/agentware config --set-dream-schedule HH:MM`).\n")
        return 2
    cli = _dream_cli_path()
    log_path = _dream_scheduler_log_path()
    if sys.platform == "darwin":
        path = _dream_launchd_plist_path()
        content = _dream_launchd_plist(schedule, cli, log_path)
        platform = "launchd"
    else:
        path = os.path.join(os.path.expanduser("~"), DREAM_CRON_REL)
        content = _dream_cron_line(schedule, cli, log_path)
        platform = "cron"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Create the gitignored scheduler-log dir so the scheduler's first run
        # has a valid StandardOutPath/redirection target (launchd won't create it).
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as exc:
        sys.stderr.write("agentware: could not write schedule %s: %s\n" % (path, exc))
        return 1
    # Best-effort activation (skipped under the test guard; never fatal).
    if os.environ.get("AGENTWARE_NESTED_UNITTEST") != "1":
        try:
            if platform == "launchd":
                subprocess.run(["launchctl", "load", "-w", path],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               check=False)
            else:
                existing = subprocess.run(["crontab", "-l"], stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, check=False)
                base = existing.stdout.decode("utf-8", "replace") \
                    if existing.returncode == 0 else ""
                base = "".join(l for l in base.splitlines(keepends=True)
                               if "agentware" not in l or "dream" not in l)
                subprocess.run(["crontab", "-"], input=(base + content).encode(),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               check=False)
        except (OSError, ValueError):
            pass
    if as_json:
        json.dump({"ok": True, "platform": platform, "path": path,
                   "schedule": schedule}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("DREAM schedule installed (%s): %s @ %s" % (platform, path, schedule))
    return 0


def _dream_uninstall_schedule(as_json):
    """Remove an installed schedule from HOME (idempotent; clean no-op if absent)."""
    if sys.platform == "darwin":
        path, platform = _dream_launchd_plist_path(), "launchd"
    else:
        path = os.path.join(os.path.expanduser("~"), DREAM_CRON_REL)
        platform = "cron"
    removed = False
    if os.path.isfile(path):
        if os.environ.get("AGENTWARE_NESTED_UNITTEST") != "1":
            try:
                if platform == "launchd":
                    subprocess.run(["launchctl", "unload", "-w", path],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   check=False)
            except OSError:
                pass
        try:
            os.remove(path)
            removed = True
        except OSError:
            pass
    if as_json:
        json.dump({"ok": True, "platform": platform, "path": path,
                   "removed": removed}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("DREAM schedule %s (%s): %s"
              % ("removed" if removed else "not present", platform, path))
    return 0


def _dream_cadence_due(kdir, now):
    """Due when >= DREAM_CADENCE_INTERVAL_SEC (~6h) since the last dream cycle.
    Always-due when never run — sleep-proof: keyed off the last-cycle WALL-CLOCK,
    NOT the tick cadence, so a scheduler that slept through several ticks
    self-corrects (still due) on the next wake (mirrors _pkg_updater_due)."""
    # Function-local: `features.dream` imports THIS module for the schedule
    # install/uninstall, so a module-scope edge back to it would close the cycle.
    from agentware_lib.features.dream import (
        DREAM_CADENCE_INTERVAL_SEC, _dream_last_cycle_ts)
    from agentware_lib.features.pkgupdater import _pkg_iso_to_epoch
    last = _pkg_iso_to_epoch(_dream_last_cycle_ts(kdir))
    if last is None:
        return True
    return (now - last) >= DREAM_CADENCE_INTERVAL_SEC


def _dream_cadence_run(kdir, now):
    """Dispatch ONE dream cycle at force=False (the in-CLI idle-gate + min-interval
    floor + concurrency lock decide whether it actually runs). Output captured via
    _dream_capture; returns a compact status dict for the tick result list."""
    import argparse
    from agentware_lib.core.capture import _dream_capture
    from agentware_lib.features.dream import cmd_dream
    ns = argparse.Namespace(format="json", dry_run=False, force=False, steps=None,
                            install_schedule=False, uninstall_schedule=False)
    rc, _out = _dream_capture(cmd_dream, ns)
    return {"status": "ok" if rc == 0 else "error", "rc": rc,
            "detail": "dream-cadence dispatched cmd_dream(force=False) rc=%d" % rc}


_EXPORTS = [
    "_dream_cadence_due", "_dream_cadence_run",
    "DREAM_LAUNCHD_LABEL",
    "DREAM_CRON_REL",
    "DREAM_NICE",
    "_dream_cli_path",
    "_dream_scheduler_log_path",
    "_dream_schedule_hm",
    "_dream_launchd_plist",
    "_dream_cron_line",
    "_dream_launchd_plist_path",
    "_dream_install_schedule",
    "_dream_uninstall_schedule",
]
