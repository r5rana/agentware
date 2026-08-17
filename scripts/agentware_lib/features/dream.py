"""agentware_lib.features.dream -- the dream nightly-loop DRIVER + heartbeat/audit-health slice (layer: features).

Owns: (1) the dream CYCLE DRIVER cmd_dream -- the Phase-1 orchestrator that runs
one deterministic, idle-gated KB-maintenance cycle in fixed step order, honoring
--dry-run / --steps / --force + the concurrency lock + the wall-clock guard -- and
its `dream` verb parser registrar register_dream; (2) the dream last-cycle
heartbeat closure the audit check needs -- the DREAM_STALE_HOURS budget, the
metrics.jsonl last-`dream`-record readers (_dream_last_cycle / _dream_cycle_outcome
/ _dream_age_hours), the timestamp helper _dream_last_cycle_ts, and the Rule-7
dream-health audit check (_audit_dream_health_check) that `audit` surfaces. Opened
EARLY in wave 5 for the heartbeat slice (NO-FORWARD-REFERENCE clause (i)); the loop
DRIVER (cmd_dream/register_dream/_dream_last_cycle_ts) arrived in wave-6 seam 31 --
the LAST dream cut -- so the whole dream verb-domain now lives under features/dream*.
Does NOT own: the cycle's collaborators, all reached DOWNWARD/SIDEWAYS via module
aliases -- the lock + idle-gate -> features.dreamgate; the journal/metric/output ->
features.dreamjournal; the maintenance STEP family + DREAM_STEP_FUNCS + --steps
parser -> features.dreamsteps; the
scheduler installer + DREAM_NICE -> features.dreamsched; the audit command + the
other _audit_* checks -> ops.audit. See catalog/modules.json.
Layer deps: core (config, dates, routing) DOWNWARD + FIVE features SIBLINGS
(dreamgate, dreamjournal, dreamsched, dreamsteps) -- acyclic: each is
a pure dream LEAF that references nothing back in the dream domain (dreamsteps
imports dreamsched only), so the DRIVER->collaborator edges are one-directional.
Nothing above (cli) is imported at module level; cmd_audit in ops.audit reaches
_audit_dream_health_check via a FUNCTION-LOCAL import (the audit<->dream cycle),
keeping module-level acyclicity.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: cmd_dream reaches cross-module FUNCTIONS by MODULE-ALIAS attribute (so
the global-resolve gate never cascades into their globals) and cross-module
CONSTANT DREAM_NICE by name. DREAM_STEP_FUNCS is deliberately NOT among the by-name
imports: it is the step REGISTRY, which `dreamsteps` documents as the override point
("every existing monkeypatch target `<consumer>.dreamsteps.<name>` keeps working"),
and the monolith read it late-bound off the module. Importing it by name froze it at
import time, so overriding the registry silently did nothing -- the cycle ran the
twelve real steps while the caller believed it had substituted its own. A cycle is idempotent + idle-gated + lock-guarded
(INV-2); --dry-run mutates NOTHING; the heartbeat readers are read-only over
logs/metrics.jsonl with no clock (_dream_age_hours reads the wall clock only for an
ADVISORY age, never feeding ranking, INV-1); non-interactive (R-SHELL-01); stdlib +
downward lib deps only (INV-6).
"""

import datetime
import json
import os
import sys
import time

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.core.routing as routing

import agentware_lib.features.dreamgate as dreamgate
import agentware_lib.features.dreamjournal as dreamjournal
import agentware_lib.features.dreamsched as dreamsched
# The ISO->epoch helper is the package-updater's; shared rather than
# re-spelt so the two cadence gates can never disagree about what a
# timestamp means.
from agentware_lib.features.pkgupdater import _pkg_iso_to_epoch
import agentware_lib.features.dreamsteps as dreamsteps

from agentware_lib.features.dreamsched import DREAM_NICE


DREAM_STALE_HOURS = 48           # dream_health warns when last dream older than this

def _dream_last_cycle(kdir):
    """Most recent `dream` event RECORD from logs/metrics.jsonl, or None.
    Read-only; tolerant of a missing/garbled log. The full record (not just the
    timestamp) lets the heartbeat derive last-run AGE + OUTCOME in one pass."""
    path = os.path.join(kdir, "logs", "metrics.jsonl")
    last = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or '"event": "dream"' not in line and \
                        '"event":"dream"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("event") == "dream" and rec.get("ts"):
                    last = rec
    except OSError:
        return None
    return last

def _dream_cycle_outcome(rec):
    """Classify a dream metric event as 'ok' | 'partial' | 'fail' from its
    per-step statuses (skips are not failures, mirroring _dream_output). 'ok' =
    no step failed; 'fail' = every non-skipped step failed; 'partial' = a mix of
    passed and failed steps. Returns 'ok' for a record with no steps."""
    steps = rec.get("steps") or []
    failed = [s for s in steps
              if s.get("status") not in ("ok", "skipped", "planned")]
    if not failed:
        return "ok"
    passed = [s for s in steps if s.get("status") == "ok"]
    return "partial" if passed else "fail"

def _dream_age_hours(ts):
    """Hours between an ISO 'Z' timestamp and now (UTC), or None if unparseable."""
    try:
        when = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        return None
    return (datetime.datetime.now(datetime.timezone.utc) - when).total_seconds() / 3600.0

def _audit_dream_health_check(kdir):
    """Rule-7 last-dream heartbeat. INERT when dream is OFF (the opt-in default);
    when ON it reports the last cycle's AGE (hours) + OUTCOME (ok|fail|partial)
    parsed from logs/metrics.jsonl and FAILs if no cycle ran yet, the last one is
    stale (> DREAM_STALE_HOURS), OR the last cycle did not finish clean
    (fail/partial). The structured `last_run`/`age_hours`/`outcome` fields are
    surfaced on the dashboard's dream_health panel. Read-only (reads
    logs/metrics.jsonl only)."""
    if routing.resolve_dream() != "1":
        return {"name": "dream_health", "ok": True,
                "details": ["inert: dream mode OFF (opt-in; enable with "
                            "`scripts/agentware config --set-dream on`)"]}
    rec = _dream_last_cycle(kdir)
    if rec is None:
        return {"name": "dream_health", "ok": False, "last_run": None,
                "age_hours": None, "outcome": None,
                "details": ["dream is ON but no cycle has run yet — run "
                            "`scripts/agentware dream` or install the schedule"]}
    last = rec.get("ts")
    age = _dream_age_hours(last)
    outcome = _dream_cycle_outcome(rec)
    age_str = ("%.1fh" % age) if age is not None else "unknown age"
    base = {"name": "dream_health", "last_run": last,
            "age_hours": round(age, 1) if age is not None else None,
            "outcome": outcome}
    if age is not None and age > DREAM_STALE_HOURS:
        return dict(base, ok=False,
                    details=["last dream %s is stale (%.1fh > %dh budget); "
                             "outcome=%s" % (last, age, DREAM_STALE_HOURS,
                                             outcome)])
    if outcome != "ok":
        return dict(base, ok=False,
                    details=["last dream %s did not finish clean (outcome=%s, "
                             "age=%s) — see logs/dream-failures/ + "
                             "logs/dream-journal.md" % (last, outcome, age_str)])
    return dict(base, ok=True,
                details=["fresh: last dream %s (age=%s, outcome=ok)"
                         % (last, age_str)])


# --- P2c (260713): dream cadence + affordability guards ---------------------
# A shared 6h floor: the min-interval gate (Task 7) makes the 30-min tick express
# a ~6h EFFECTIVE cadence, and the dream-cadence tick job (Task 9) is due on the
# same interval. These are TIME-based control-plane gates (a cadence is inherently
# clock-driven) — the INV-1 "no clock in a decision" constraint is scoped to the
# MINER's mined decisions, never these gates.
DREAM_CADENCE_INTERVAL_SEC = 6 * 3600


def _dream_last_cycle_ts(kdir):
    """Timestamp (ISO 'Z') of the most recent dream from logs/metrics.jsonl, or
    None. Read-only; tolerant of a missing/garbled log."""
    rec = _dream_last_cycle(kdir)
    return rec.get("ts") if rec else None



def _dream_min_interval_skip(kdir, now, force):
    """Task 7 (affordability guard #1). Return a SKIP reason when the last recorded
    dream cycle is YOUNGER than the 6h floor, else None. `force` bypasses it
    (parity with the idle-gate) so manual debugging is unaffected. FAIL-OPEN: a
    missing / garbled last-cycle ts proceeds (returns None). Read-only — a caller
    that skips on this mutates NOTHING (no lock, no journal, no metric)."""
    if force:
        return None
    last = _pkg_iso_to_epoch(_dream_last_cycle_ts(kdir))
    if last is None:
        return None
    age = now - last
    if age < DREAM_CADENCE_INTERVAL_SEC:
        return ("last dream cycle was %.1fh ago (< %dh min-interval)"
                % (age / 3600.0, DREAM_CADENCE_INTERVAL_SEC // 3600))
    return None

def cmd_dream(args):
    """Run ONE deterministic, idle-gated KB-maintenance cycle (Phase 1).

    Orchestrates the sanctioned writers in fixed order a-f; each step is
    idempotent + skippable (--steps). --dry-run reports the plan and mutates
    NOTHING. The idle-gate skips (with a logged reason) when a loop session is
    active or load is high (bypass with --force); the concurrency lock is ALWAYS
    honored. Non-interactive (R-SHELL-01).
    """
    as_json = getattr(args, "format", "text") == "json"
    dry_run = getattr(args, "dry_run", False)

    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1

    if getattr(args, "install_schedule", False):
        return dreamsched._dream_install_schedule(kdir, as_json)
    if getattr(args, "uninstall_schedule", False):
        return dreamsched._dream_uninstall_schedule(as_json)

    force = getattr(args, "force", False)
    steps, err = dreamsteps._dream_parse_steps(getattr(args, "steps", None))
    if err:
        sys.stderr.write("agentware: %s\n" % err)
        return 2

    def _run_cycle():
        started, wall0 = dates.utc_now_iso(), dreamsteps._dream_perf()
        # Best-effort, FULLY OFFLINE wall-clock guard (Task 7). 0 = disabled.
        # Checked BETWEEN steps (no signals/threads — see worklog DECISION); it
        # can't interrupt a step already running, only stop the cycle before the
        # next one once the cap is exceeded.
        max_runtime = routing.resolve_dream_max_runtime()
        planned = [t for t in dreamsteps.DREAM_STEP_FUNCS if t[0] in steps]
        results = []
        timed_out, timeout_reason = False, None
        for i, (s, label, fn) in enumerate(planned):
            elapsed = dreamsteps._dream_perf() - wall0
            if max_runtime and elapsed > max_runtime:
                # Cap tripped: record a synthetic guard step (status error ->
                # non-zero exit), mark the remaining steps stopped, and stop.
                timed_out = True
                timeout_reason = ("max-runtime cap exceeded: %.1fs elapsed > %ds "
                                  "cap (AGENTWARE_DREAM_MAX_RUNTIME)"
                                  % (elapsed, max_runtime))
                results.append({"step": "guard", "label": "max-runtime",
                                "status": "error", "error": timeout_reason,
                                "duration_s": round(elapsed, 4)})
                for rs, rlabel, _fn in planned[i:]:
                    results.append({"step": rs, "label": rlabel,
                                    "status": "skipped",
                                    "skipped_reason": "not run: max-runtime cap "
                                                      "tripped",
                                    "duration_s": 0.0})
                break
            t0 = dreamsteps._dream_perf()
            try:
                r = fn(kdir, dry_run)
            except Exception as exc:   # one bad step never aborts the cycle
                r = {"status": "error", "error": "%s: %s" % (type(exc).__name__, exc)}
            r["step"], r["label"] = s, label
            r["duration_s"] = round(dreamsteps._dream_perf() - t0, 4)
            # A failed step may hand back the full captured output under the
            # private `_failure_capture` key — persist it to a per-cycle triage
            # artifact and replace the blob with its (diff-friendly) path so the
            # step dict stays small + byte-stable in the journal/metric (INV-1).
            cap = r.pop("_failure_capture", None)
            if cap is not None and not dry_run:
                log_rel = dreamjournal._dream_write_failure_capture(kdir, started, s, cap)
                if log_rel:
                    r["triage_log"] = log_rel
            results.append(r)
        cyc = {"event": "dream", "started": started, "finished": dates.utc_now_iso(),
               "duration_s": round(dreamsteps._dream_perf() - wall0, 4), "dry_run": dry_run,
               "force": force, "skipped": False, "steps": results}
        if timed_out:
            cyc["timed_out"] = True
            cyc["timeout_reason"] = timeout_reason
        return cyc

    # --dry-run: pure report — no lock, no idle-gate writes, no journal/metric.
    if dry_run:
        return dreamjournal._dream_output(_run_cycle(), as_json, True)

    # Idle-gate FIRST (reads only) so an active-session skip mutates nothing.
    skip = dreamgate.dream_gate_reason(kdir, force=force)
    if skip:
        return dreamjournal._dream_skip_output("idle-gate", skip, as_json)

    # Min-interval floor (Task 7): a too-soon cycle skips, mutating NOTHING (no
    # lock, no journal, no metric) — this makes the 30-min tick a ~6h effective
    # cadence. --force bypasses it (parity with the idle-gate).
    min_skip = _dream_min_interval_skip(kdir, time.time(), force)
    if min_skip:
        return dreamjournal._dream_skip_output("min-interval", min_skip, as_json)

    # Single-writer concurrency lock (honored even with --force).
    if not dreamgate.dream_acquire_lock(kdir):
        return dreamjournal._dream_skip_output(
            "lock", "another dream holds the lock (concurrency guard)", as_json)
    try:
        if os.environ.get("AGENTWARE_NESTED_UNITTEST") != "1":
            try:
                os.nice(DREAM_NICE)   # low priority; best-effort
            except (OSError, AttributeError):
                pass
        cycle = _run_cycle()
        dreamjournal._dream_write_journal(kdir, cycle)
        dreamjournal._dream_emit_metric(kdir, cycle)
        return dreamjournal._dream_output(cycle, as_json, False)
    finally:
        dreamgate.dream_release_lock(kdir)


def register_dream(sub, fmt_parent):
    # dream ------------------------------------------------------------------
    p_dream = sub.add_parser(
        "dream", parents=[fmt_parent],
        help="run ONE deterministic, idle-gated KB-maintenance cycle (Phase 1)")
    p_dream.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="report the ordered step plan; mutate NOTHING")
    p_dream.add_argument(
        "--steps", default=None, metavar="a,b,…",
        help="run only this comma-separated subset of steps a-f "
             "(a index-rebuild, b bench-redact, c audit, d eval-record, "
             "e detect-report, f kb-git-commit)")
    p_dream.add_argument(
        "--force", action="store_true",
        help="bypass the idle-gate for a manual run (the concurrency lock is "
             "STILL honored)")
    p_dream.add_argument(
        "--install-schedule", action="store_true", dest="install_schedule",
        help="install the nightly schedule into HOME (launchd/cron) — requires "
             "dream ON + a configured --set-dream-schedule")
    p_dream.add_argument(
        "--uninstall-schedule", action="store_true", dest="uninstall_schedule",
        help="remove an installed nightly schedule (idempotent)")
    p_dream.set_defaults(func=cmd_dream)


_EXPORTS = [
    "DREAM_STALE_HOURS",
    "_dream_last_cycle",
    "_dream_cycle_outcome",
    "_dream_age_hours",
    "_audit_dream_health_check",
    "_dream_last_cycle_ts",
    "cmd_dream",
]
