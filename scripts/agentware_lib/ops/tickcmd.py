"""agentware_lib.ops.tickcmd -- the TICK scheduler and its reverse-guard (layer: ops).

Owns: the periodic `tick` surface -- the schedule identity (TICK_LAUNCHD_LABEL /
TICK_CRON_REL / TICK_INTERVAL_SEC), the launchd plist and crontab line builders,
the job registry (_tick_jobs / _TICK_JOB_REGISTRY / _tick_any_job_enabled), the
install and uninstall paths, the dispatcher (_tick_dispatch) and the `cmd_tick`
handler with its `--lock-status` REVERSE-GUARD.

WHY A REVERSE-GUARD
The tick and a foreground apply share ONE updater lock, so the loop needs to tell
"an apply is legitimately running" apart from "a killed apply left a stale lock".
`--lock-status` answers that WITHOUT taking the lock: it reports the holder and
whether the lock is stale, so the loop can self-heal a crashed apply rather than
blocking forever behind a dead pid. A guard that acquired the lock to inspect it
would be the deadlock it exists to prevent.

INSTALL IS OPT-IN, ALWAYS. Nothing here installs a schedule as a side effect of
running the CLI: `--install-schedule` is an explicit operator action, and the job
registry is consulted first so a tick with no enabled job installs nothing.
`_tick_uninstall_schedule` is the exact inverse and is safe to run when nothing is
installed -- `_crontab_filter` rewrites the crontab by REMOVING this package's
managed line and leaves every other entry byte-identical, so an uninstall can
never eat an operator's unrelated cron jobs.

Does NOT own: what the tick RUNS (the updater cadence, self-heal and heartbeat)
-> features.pkgupdater; the DREAM schedule, which is a separate verb with its own
launchd label and cron line -> features.dreamsched; the update apply path ->
features.pkgapply.
Layer deps: ops -> core (config, paths) and ops -> features (dreamsched for the
shared nice-level, pkgupdater for the heartbeat and the lock-status word this
guard reports). The ops->features direction is the SAME edge the monolith had --
`tick` schedules what `update` does -- and is declared rather than hidden.
Fan-in: 0 (see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all.
"""

import json
import os
import plistlib
import subprocess
import sys
import time

from agentware_lib.core.config import require_knowledge_dir, resolve_knowledge_dir
from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.features.dreamsched import DREAM_NICE
from agentware_lib.features.pkgupdater import (
    _pkg_emit_heartbeat,
    _pkg_lock_status_word,
)


# --- Task 7: shared 30-min tick daemon (StartInterval; job-agnostic dispatch) -
# A single sleep-proof scheduler that dispatches DUE-gated jobs from a job-agnostic
# registry. The updater is the first job; the P2c 6h dream-cadence bump is a purely
# ADDITIVE second job. The scheduler cadence is only a wake-inclusive NUDGE — each
# job computes its real due-ness IN-BAND from wall-clock-since-last-check, so a
# slept-through machine self-corrects on the next post-wake tick.
TICK_LAUNCHD_LABEL = "com.agentware.tick"      # DISTINCT from DREAM_LAUNCHD_LABEL


TICK_CRON_REL = os.path.join(".agentware", "tick.cron")


TICK_INTERVAL_SEC = 1800                        # 30-min StartInterval / cron */30


def _crontab_filter(text, marker_tokens):
    """Pure crontab write-filter: DROP a line iff it contains ALL of
    `marker_tokens`, else KEEP it. Parametrizing the marker means installing the
    tick (tokens agentware+tick) never strips the dream line (agentware+dream) and
    vice versa."""
    out = []
    for line in (text or "").splitlines(keepends=True):
        if all(tok in line for tok in marker_tokens):
            continue
        out.append(line)
    return "".join(out)


def _tick_cli_path():
    return os.path.join(REPO_ROOT, "scripts", "agentware")


def _tick_scheduler_log_path():
    """Gitignored tick scheduler log (DISTINCT from dream's), under the KB's
    logs/ (already gitignored) or ~/.agentware/logs/ when unconfigured."""
    kdir = resolve_knowledge_dir()
    base = kdir if kdir else os.path.join(os.path.expanduser("~"), ".agentware")
    return os.path.join(base, "logs", "tick-scheduler.log")


def _tick_launchd_plist(cli_path, log_path=None, interval=TICK_INTERVAL_SEC):
    """LaunchAgent plist using StartInterval (NOT StartCalendarInterval) so the
    shared tick wakes ~every `interval` seconds. Runs at low priority; captures
    stdout+stderr to the gitignored tick scheduler log."""
    log = log_path or _tick_scheduler_log_path()
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
        '    <string>tick</string>\n'
        '  </array>\n'
        '  <key>StartInterval</key>\n  <integer>%d</integer>\n'
        '  <key>StandardOutPath</key>\n  <string>%s</string>\n'
        '  <key>StandardErrorPath</key>\n  <string>%s</string>\n'
        '  <key>RunAtLoad</key>\n  <false/>\n'
        '</dict>\n</plist>\n'
        % (TICK_LAUNCHD_LABEL, DREAM_NICE, cli_path, interval, log, log))


def _tick_cron_line(cli_path, log_path=None):
    """crontab line running the tick every 30 minutes at low priority."""
    log = log_path or _tick_scheduler_log_path()
    return "*/30 * * * * nice -n %d %s tick >> %s 2>&1\n" % (
        DREAM_NICE, cli_path, log)


def _tick_launchd_plist_path():
    return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents",
                        "%s.plist" % TICK_LAUNCHD_LABEL)


def _tick_jobs():
    """Job-agnostic tick registry. Each job is a dict:
      {key, enabled: ()->bool, due: (kdir, now)->bool, run: (kdir, now)->dict}.
    Task 8 registers the dedicated-updater job; the P2c dream-cadence job is purely
    additive (append another entry — the dispatcher makes NO per-job assumptions)."""
    if not _TICK_JOB_REGISTRY:
        register_builtin_tick_jobs()
    return list(_TICK_JOB_REGISTRY)


_TICK_JOB_REGISTRY = []      # Task 8 appends the updater job here at import time


def _tick_register_job(job):
    """Register (or REPLACE) a tick job, keyed on `job["key"]`.

    Idempotent by construction, and that is load-bearing rather than tidy. This
    registry is MUTABLE module-level state that moved into the lib at task 43,
    while the code that populates it still lives in the monolith — and the monolith
    is `exec`d into a fresh module namespace every time something loads the CLI
    facade (the test harness, the dashboard sidecar, a fresh-clone probe). In the
    monolith each exec built its OWN list; here the list is imported once and
    cached, so a plain `.append` accumulates a duplicate job on every exec.

    Duplicates are not merely untidy: each carries its own closure over the module
    dict of the exec that created it, so the dispatcher runs a job bound to a DEAD
    namespace. A test patching the live namespace then watches the stale copy run
    unpatched — the failure looks like "the patch did not take" and points nowhere
    near the registry. Replacing by key keeps exactly one live entry per job.
    """
    for i, existing in enumerate(_TICK_JOB_REGISTRY):
        if existing.get("key") == job.get("key"):
            _TICK_JOB_REGISTRY[i] = job
            return
    _TICK_JOB_REGISTRY.append(job)


def _tick_any_job_enabled(jobs=None):
    """True iff at least one registered job is enabled (install guard)."""
    for job in (jobs if jobs is not None else _tick_jobs()):
        try:
            if job["enabled"]():
                return True
        except Exception:
            continue
    return False


def _tick_install_schedule(kdir, as_json, jobs=None):
    """Install the shared 30-min tick into the operator's HOME (opt-in). macOS ->
    launchd StartInterval plist; else -> crontab */30 fragment. REFUSES when NO
    job is enabled (mirrors _dream_install_schedule). Fully non-interactive; writes
    ONLY under HOME; best-effort activation skipped under the test guard."""
    if not _tick_any_job_enabled(jobs):
        sys.stderr.write("agentware: no tick job is enabled — nothing to schedule "
                         "(enable one, e.g. `scripts/agentware config "
                         "--set-updater on`).\n")
        return 2
    cli = _tick_cli_path()
    log_path = _tick_scheduler_log_path()
    if sys.platform == "darwin":
        path = _tick_launchd_plist_path()
        content = _tick_launchd_plist(cli, log_path)
        platform = "launchd"
    else:
        path = os.path.join(os.path.expanduser("~"), TICK_CRON_REL)
        content = _tick_cron_line(cli, log_path)
        platform = "cron"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as exc:
        sys.stderr.write("agentware: could not write tick schedule %s: %s\n"
                         % (path, exc))
        return 1
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
                # OWN marker only — never strip the dream cron line.
                base = _crontab_filter(base, ("agentware", "tick"))
                subprocess.run(["crontab", "-"], input=(base + content).encode(),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               check=False)
        except (OSError, ValueError):
            pass
    if as_json:
        json.dump({"ok": True, "platform": platform, "path": path,
                   "interval_sec": TICK_INTERVAL_SEC}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("TICK schedule installed (%s): %s @ every %dm"
              % (platform, path, TICK_INTERVAL_SEC // 60))
    return 0


def _tick_uninstall_schedule(as_json):
    """Remove an installed tick schedule from HOME (idempotent; clean no-op if
    absent). Never touches the dream schedule."""
    if sys.platform == "darwin":
        path, platform = _tick_launchd_plist_path(), "launchd"
    else:
        path = os.path.join(os.path.expanduser("~"), TICK_CRON_REL)
        platform = "cron"
    removed = False
    if os.path.isfile(path):
        if os.environ.get("AGENTWARE_NESTED_UNITTEST") != "1":
            try:
                if platform == "launchd":
                    subprocess.run(["launchctl", "unload", "-w", path],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   check=False)
                else:
                    # Strip the LIVE crontab line (removing the fragment file alone
                    # would leave the tick running forever). OWN marker only — never
                    # touches the dream cron line.
                    existing = subprocess.run(["crontab", "-l"],
                                              stdout=subprocess.PIPE,
                                              stderr=subprocess.PIPE, check=False)
                    if existing.returncode == 0:
                        base = _crontab_filter(
                            existing.stdout.decode("utf-8", "replace"),
                            ("agentware", "tick"))
                        subprocess.run(["crontab", "-"], input=base.encode(),
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       check=False)
            except (OSError, ValueError):
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
        print("TICK schedule %s (%s): %s"
              % ("removed" if removed else "not present", platform, path))
    return 0


def register_builtin_tick_jobs():
    """Register the three shipped tick jobs, in their pre-split ORDER.

    EXPLICIT, and deliberately not an import side effect. The monolith registered
    these by executing three `_tick_register_job({...})` calls at module scope, so
    the registry was populated only because something happened to import the
    monolith — and every job's `enabled`/`due`/`run` closed over THAT exec's
    namespace. Deleting the shim (task 56) would therefore have silently
    unregistered the updater, the dream cadence and the checkup: three scheduled
    capabilities, gone, with no error and no failing test, because an empty
    registry dispatches cleanly and reports nothing to run.

    Called from `_tick_jobs()`, so the registry is populated on first read no
    matter which entry point got here. `_tick_register_job` replaces by key, so
    calling this repeatedly is a no-op rather than an accumulation.

    ORDER IS PRESERVED (updater, dream-cadence, checkup): `_tick_dispatch` makes
    no per-job assumptions, but the order is what the tick's output has always
    listed and a reordering would be a visible, unexplained change.
    """
    from agentware_lib.features.pkgupdater import (
        _pkg_updater_due, _pkg_updater_run, resolve_updater)
    from agentware_lib.features.dreamsched import (
        _dream_cadence_due, _dream_cadence_run)
    from agentware_lib.core.routing import resolve_dream as _resolve_dream
    from agentware_lib.ops.checkupconfig import _checkup_due, resolve_checkup
    from agentware_lib.ops.checkupcmd import _checkup_run

    # Gated on resolve_updater()==1 so it SKIPS when OFF — a runtime kill switch
    # that does NOT require uninstalling the shared tick.
    _tick_register_job({
        "key": "updater",
        "enabled": lambda: resolve_updater() == "1",
        "due": _pkg_updater_due,
        "run": _pkg_updater_run,
    })
    # Purely additive. Gated on resolve_dream()==1 so it is INERT until the
    # operator enables dream (config --set-dream on). run() dispatches
    # cmd_dream(force=False), so the idle-gate + min-interval floor + single-writer
    # lock all still apply (defence in depth).
    _tick_register_job({
        "key": "dream-cadence",
        "enabled": lambda: _resolve_dream() == "1",
        "due": _dream_cadence_due,
        "run": _dream_cadence_run,
    })
    # Gated on resolve_checkup()==1 so it is INERT until the operator enables it
    # (config --set-checkup on).
    _tick_register_job({
        "key": "checkup",
        "enabled": lambda: resolve_checkup() == "1",
        "due": _checkup_due,
        "run": _checkup_run,
    })


def _tick_dispatch(kdir, now=None, jobs=None):
    """Walk the job-agnostic registry and run each ENABLED + DUE job. Makes NO
    updater-only assumptions. Returns a per-job result list. A job that raises is
    isolated (recorded, never aborts the tick)."""
    now = now if now is not None else time.time()
    results = []
    for job in (jobs if jobs is not None else _tick_jobs()):
        key = job.get("key", "?")
        try:
            if not job["enabled"]():
                results.append({"job": key, "status": "disabled"})
                continue
            if not job["due"](kdir, now):
                results.append({"job": key, "status": "not-due"})
                continue
            r = job["run"](kdir, now)
            results.append({"job": key, "status": "ran", "result": r})
        except Exception as exc:   # noqa: BLE001 — isolate one job's failure
            results.append({"job": key, "status": "error", "detail": str(exc)})
    return results


def cmd_tick(args):
    """Shared 30-min tick: dispatch DUE jobs, or install/uninstall the schedule."""
    as_json = getattr(args, "format", "text") == "json"
    if getattr(args, "lock_status", False):
        kdir = resolve_knowledge_dir()
        print(_pkg_lock_status_word(kdir) if kdir else "free")
        return 0
    if getattr(args, "install_schedule", False):
        return _tick_install_schedule(require_knowledge_dir(), as_json)
    if getattr(args, "uninstall_schedule", False):
        return _tick_uninstall_schedule(as_json)
    kdir = resolve_knowledge_dir()
    if not kdir:
        if as_json:
            json.dump({"ok": True, "dispatched": [], "detail": "no KB configured"},
                      sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            print("tick: no knowledge dir configured — nothing to dispatch.")
        return 0
    results = _tick_dispatch(kdir)
    _pkg_emit_heartbeat(kdir)     # Layer-3 rollout-visibility signal (once per tick)
    if as_json:
        json.dump({"ok": True, "dispatched": results}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        for r in results:
            line = "tick: job %s -> %s" % (r["job"], r["status"])
            if r.get("result", {}).get("detail"):
                line += " (%s)" % r["result"]["detail"]
            elif r.get("detail"):
                line += " (%s)" % r["detail"]
            print(line)
        if not results:
            print("tick: no jobs registered.")
    return 0


def register_tick(sub, fmt_parent):
    """Declare the `tick` verb. Lifted verbatim from the monolith's build_parser.

    Both schedule flags are EXPLICIT operator actions — nothing here installs a
    schedule as a side effect of running the CLI — and `--lock-status` is
    read-only by construction, which is what lets the loop's reverse-guard call it
    while an apply is in flight.
    """
    p_tick = sub.add_parser(
        "tick", parents=[fmt_parent],
        help="shared 30-min tick: dispatch due jobs (updater now; P2c dream "
             "cadence additive), or install/uninstall the StartInterval schedule")
    p_tick.add_argument(
        "--install-schedule", action="store_true", dest="install_schedule",
        help="install the shared tick into HOME (launchd StartInterval=1800 / "
             "cron */30) — refuses when no job is enabled")
    p_tick.add_argument(
        "--uninstall-schedule", action="store_true", dest="uninstall_schedule",
        help="remove an installed tick schedule (idempotent; leaves dream alone)")
    p_tick.add_argument(
        "--lock-status", action="store_true", dest="lock_status",
        help="print the updater/tick lock state (free|held|stale) and exit "
             "(read-only; used by the loop reverse-guard)")
    p_tick.set_defaults(func=cmd_tick)


_EXPORTS = [
    "TICK_LAUNCHD_LABEL",
    "TICK_CRON_REL",
    "TICK_INTERVAL_SEC",
    "_crontab_filter",
    "_tick_cli_path",
    "_tick_scheduler_log_path",
    "_tick_launchd_plist",
    "_tick_cron_line",
    "_tick_launchd_plist_path",
    "_tick_jobs",
    "_TICK_JOB_REGISTRY",
    "_tick_register_job",
    "register_builtin_tick_jobs",
    "_tick_any_job_enabled",
    "_tick_install_schedule",
    "_tick_uninstall_schedule",
    "_tick_dispatch",
    "cmd_tick",
]

__all__ = list(_EXPORTS)
