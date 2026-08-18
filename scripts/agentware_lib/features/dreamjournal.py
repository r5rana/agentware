"""agentware_lib.features.dreamjournal -- the dream cycle journal/metric/output family (layer: features).

Owns: the dream cycle's DETERMINISTIC OBSERVABILITY surface -- the
journal-file path constant (DREAM_JOURNAL_REL), the human-readable journal
rendering (_dream_step_summary / _dream_render_journal_entry /
_dream_write_journal), the per-cycle failure-triage log capture
(_dream_failure_log_rel / _dream_write_failure_capture), the Rule-7 metric
event emitter for logs/metrics.jsonl (_DREAM_METRIC_STEP_FIELDS /
_dream_metric_step / _dream_emit_metric), and the terminal cycle output
renderers (_dream_output for a real cycle, _dream_skip_output for an
idle-gate/lock skip). A pure LEAF the dream cycle imports downward: it
references nothing else in the dream domain (no locks, no gate, no steps, no
DREAM_STEP_FUNCS), so the dream<->dreamjournal edge stays one-directional
(loop->dreamjournal) and acyclic.
Does NOT own: the maintenance STEPS + DREAM_STEP_FUNCS + _dream_parse_steps
(parse_steps reads the resident DREAM_STEP_FUNCS table so it travels with the
steps family), the KB-repoint family, and cmd_dream + register_dream -- still
resident in the entrypoint, arriving in later wave-6 seams at
agentware_lib/features/dream.py (+ dreamsteps); the scheduler installer ->
agentware_lib.features.dreamsched (wave-6 seam 26); the lock/idle-gate ->
agentware_lib.features.dreamgate (wave-6 seam 27); the dream-health heartbeat
-> agentware_lib.features.dream (see catalog/modules.json).
Layer deps: features.dreamjournal -> core (dates) only -- all DOWNWARD; nothing above (ops/cli) is imported.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: journal render uses fixed field order, metric event is
sorted-keys-appended (INV-1: byte-stable for fixed inputs); every write is
best-effort (OSError swallowed) so a full disk never aborts a dream cycle
(INV-2: append-only). stdlib-only (INV-6).
"""

import json
import os
import sys

import agentware_lib.core.dates as dates


DREAM_JOURNAL_REL = os.path.join("logs", "dream-journal.md")


def _dream_step_summary(r):
    """Stable ' k=v k=v' for the journal/text output (sorted keys; metadata out)."""
    skip = {"step", "label", "status"}
    parts = ["%s=%s" % (k, r[k]) for k in sorted(r) if k not in skip]
    return ("  " + " ".join(parts)) if parts else ""


def _dream_render_journal_entry(cycle):
    """Deterministic, diff-friendly journal entry (fixed field order)."""
    lines = ["## dream %s" % cycle["started"], "",
             "- finished: %s" % cycle["finished"],
             "- duration_s: %s" % cycle["duration_s"],
             "- force: %s" % cycle["force"]]
    if cycle.get("timed_out"):
        lines.append("- timed_out: %s" % cycle["timeout_reason"])
    for r in cycle["steps"]:
        lines.append("- step %s (%s): %s%s"
                     % (r["step"], r["label"], r["status"], _dream_step_summary(r)))
    lines.append("")
    return "\n".join(lines) + "\n"


def _dream_failure_log_rel(started):
    """Relative path of the per-cycle triage artifact for a cycle `started` ts.
    Colons are stripped so the name is filesystem-safe; deterministic in the
    cycle timestamp (one log per cycle, gitignored under logs/)."""
    slug = started.replace(":", "")
    return os.path.join("logs", "dream-failures", slug + ".log")


def _dream_write_failure_capture(kdir, started, step, text):
    """Persist the FULL captured audit/test output for a failed step to a
    gitignored per-cycle triage artifact so the morning-after "which tests
    failed?" needs no re-run. Best-effort: an OSError is swallowed and never
    aborts the cycle. Returns the relative log path on success, else None."""
    try:
        rel = _dream_failure_log_rel(started)
        path = os.path.join(kdir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write("===== dream %s step %s =====\n" % (started, step))
            f.write(text if text.endswith("\n") else text + "\n")
        return rel
    except OSError:
        return None


def _dream_write_journal(kdir, cycle):
    """Append one entry to logs/dream-journal.md (gitignored; best-effort)."""
    try:
        path = os.path.join(kdir, DREAM_JOURNAL_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            if new:
                f.write("# Dream Journal\n\n> Deterministic append-only "
                        "maintenance log (machine-local; gitignored via logs/). "
                        "One entry per cycle.\n\n")
            f.write(_dream_render_journal_entry(cycle))
    except OSError:
        pass


_DREAM_METRIC_STEP_FIELDS = ("failed_checks", "failed_tests", "tests_failed",
                             "tests_ran",
                             # stale-propose (step s): carry the emit outcome into
                             # the durable metric so a permanent `blocked` is not
                             # byte-identical to a clean no-op in logs/metrics.jsonl.
                             "proposed", "deferred", "blocked", "reason",
                             # skill-propose (step k): its emit counts (extended /
                             # clean) so the durable metric distinguishes an emit
                             # from an extend-note from a clean no-op.
                             "extended", "clean")


def _dream_metric_step(r):
    """One per-step record for the dream metric event: base fields plus any
    failure-detail fields present on the step dict (deterministic field set)."""
    m = {"step": r["step"], "status": r["status"], "duration_s": r["duration_s"]}
    for k in _DREAM_METRIC_STEP_FIELDS:
        if k in r:
            m[k] = r[k]
    return m


def _dream_emit_metric(kdir, cycle):
    """Rule-7 observability: ONE `dream` event to logs/metrics.jsonl. Best-effort
    + read-only w.r.t. the KB/index; a write failure never blocks (mirrors the
    ingestion-event shape: sorted-keys append). Per-step records carry the
    failure-detail fields (failed_checks / failed_tests / tests_*) so the event
    is self-explaining the morning after."""
    try:
        rec = {"event": "dream", "ts": cycle["started"],
               "finished": cycle["finished"], "duration_s": cycle["duration_s"],
               "force": cycle["force"],
               "steps": [_dream_metric_step(r) for r in cycle["steps"]]}
        # Surface the KB auto-repoint outcome so a team lead can SEE who migrated
        # (the migration is otherwise silent — audit observability finding).
        if cycle.get("kb_repoint"):
            rec["kb_repoint"] = cycle["kb_repoint"]
        if cycle.get("timed_out"):
            rec["timed_out"] = True
            rec["timeout_reason"] = cycle["timeout_reason"]
        # Stamp the committed-state change fingerprint (Task 8) so the NEXT
        # cycle's change-gate (steps c/d) can compare. Stamped EVEN on a dirty
        # run (records the committed fp). Best-effort: only when computable.
        # Without this the gate has nothing to compare against and can never
        # engage, so the two expensive steps it guards run on every cycle.
        from .dreamsteps.dreamcapture import _dream_change_fp
        fp = _dream_change_fp(kdir)
        if fp:
            rec["change_fp"] = fp
        logs = os.path.join(kdir, "logs")
        os.makedirs(logs, exist_ok=True)
        with open(os.path.join(logs, "metrics.jsonl"), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


def _dream_output(cycle, as_json, dry_run):
    """Render the cycle result; return the process exit code (0 unless a step
    failed/errored — a SKIP is not a failure)."""
    if as_json:
        json.dump(cycle, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print("DREAM %s%s" % ("(dry-run) " if dry_run else "", cycle["started"]))
        for r in cycle["steps"]:
            print("  [%s] %s (%s)%s" % (r["status"], r["step"], r["label"],
                                        _dream_step_summary(r)))
        print("  duration_s: %s" % cycle["duration_s"])
    ok = all(r["status"] in ("ok", "skipped", "planned") for r in cycle["steps"])
    return 0 if ok else 1


def _dream_skip_output(kind, reason, as_json):
    """Render a skipped cycle (idle-gate or lock). Mutates NOTHING. rc 0."""
    if as_json:
        json.dump({"event": "dream", "skipped": True, "skip_kind": kind,
                   "skip_reason": reason, "ts": dates.utc_now_iso()},
                  sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print("DREAM SKIPPED (%s): %s" % (kind, reason))
    return 0


_EXPORTS = [
    "DREAM_JOURNAL_REL",
    "_dream_step_summary",
    "_dream_render_journal_entry",
    "_dream_failure_log_rel",
    "_dream_write_failure_capture",
    "_dream_write_journal",
    "_DREAM_METRIC_STEP_FIELDS",
    "_dream_metric_step",
    "_dream_emit_metric",
    "_dream_output",
    "_dream_skip_output",
]
