"""agentware_lib.ops.checkupconfig -- checkup settings, paths and cadence (layer: ops).

Owns: everything the periodic self-exam needs BEFORE it examines anything -- the
operator settings and their parsers (CHECKUP_KEY / CHECKUP_LLM_KEY /
`_parse_checkup` / `resolve_checkup` / `resolve_checkup_llm`), the thresholds every
probe grades against, the report/ack/last-run paths, the report reader and content
hash, and the due-check (`_checkup_due`) plus its anchor epoch.

WHY THE THRESHOLDS LIVE HERE AND NOT BESIDE THEIR PROBES
Each threshold is the line between "healthy" and "worth telling the operator", and
they are read by DIFFERENT probes that must not drift apart in what they consider
stale. Collecting them makes the exam's opinion auditable in one place: an operator
asking "why did it flag this?" reads one file, and a change of policy is one diff
rather than a hunt.

CHECKUP_FROZEN_METRICS + MIN_FROZEN_CYCLES are the anti-thrash pair: a metric that
has not moved for N cycles is deliberately NOT re-reported, so the exam stays worth
reading. An exam that repeats itself is one nobody opens.

The anchor epoch makes the cadence deterministic rather than drifting: `_checkup_due`
measures from a fixed anchor, so a machine that was asleep does not silently skip a
cycle or fire twice on wake.

Does NOT own: the probes that grade against these thresholds (ops.checkupprobes,
ops.checkuphealth); the report renderer or the CLI (ops.checkupcmd); workorder
emission (ops.checkupemit).
Layer deps: ops -> core (config, dates) and ops -> features.pkgupdater for the
shared ISO->epoch helper the updater already owns. Reusing it rather than
re-deriving keeps ONE answer to 'when did this last happen'.
Fan-in: 4 at landing (regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all.
"""

import hashlib
import json
import os
import time

import agentware_lib.core.config as config
from agentware_lib.core.config import (
    _DREAM_OFF_TOKENS,
    _DREAM_ON_TOKENS,
    _read_config_key,
    resolve_knowledge_dir,
)
from agentware_lib.core.dates import _iso_to_epoch, _now_iso
from agentware_lib.features.pkgupdater import _pkg_iso_to_epoch


CHECKUP_KEY = "AGENTWARE_CHECKUP"


CHECKUP_DEFAULT = "0"       # opt-in + OFF, like dream/updater (consequential automation)


# One bounded LLM narrative pass, ON by default; a runtime kill switch so a
# hermetic/offline run (or an operator who wants deterministic-only) can disable
# the single subprocess without touching the deterministic signals (Q4).
CHECKUP_LLM_KEY = "AGENTWARE_CHECKUP_LLM"


CHECKUP_LLM_DEFAULT = "1"


CHECKUP_INTERVAL_SEC = 7 * 24 * 3600     # Q1: weekly cadence (in-band wall-clock)


CHECKUP_WINDOW_DAYS = 7                   # Q9: window = the cadence window


CHECKUP_LEARNING_FRESH_DAYS = 14         # Q9: learning-freshness N


CHECKUP_CORPUS_ENTRIES_MAX = 7000        # Q8: corpus-size structure trigger (~16x under solo)


CHECKUP_WORKTREE_MAX = 12                # Q8: git-worktree structure trigger


CHECKUP_RECALL_P50_MS_MAX = 1000         # Q8: recall-latency proxy (report-only, never fires)


CHECKUP_HEARTBEAT_STALE_H = 6            # tick-liveness: pkg_version heartbeat older than this


MIN_FROZEN_CYCLES = 3                    # "130-for-13-nights": min PRESENT samples (> 2)


CHECKUP_DIR_REL = os.path.join("logs", "checkup")


CHECKUP_REPORT_REL = os.path.join("logs", "checkup", "checkup-latest.md")


CHECKUP_ACK_REL = os.path.join("logs", "checkup", ".ack")


CHECKUP_LASTRUN_REL = os.path.join("logs", "checkup", ".last-run")


# Curated, TUNABLE allowlist of drift-EXPECTED numeric journal metrics for the
# "130-for-13-nights" frozen-metric check, keyed "<step>.<field>" as parsed from
# the dream-journal step lines. Each value is the tolerance BAND (a wobble within
# it is NOT "moved"). DELIBERATELY EXCLUDES boolean flags (force/with_tests/
# audit_ok) and steady-state finding-counts whose constant value is HEALTHY
# (stale/duplicates/unpromoted_markers/tests_failed/failed_checks, miner
# candidates) — those are constant BY DESIGN, not frozen (audit L4-3). A metric is
# frozen ONLY when it is in this allowlist AND held within tolerance across
# >= MIN_FROZEN_CYCLES cycles where it was actually PRESENT.
CHECKUP_FROZEN_METRICS = {
    "d.reliability": 0.005,   # eval reliability — SHOULD move as code changes
    "c.tests_ran": 0.0,       # test count — SHOULD grow as tests are added
    "d.rows_added": 0.0,      # eval rows appended — a permanently-flat 0 is suspect
}


def _parse_checkup(raw):
    """Lenient on/off parse for the checkup flag ('1'/'0'/None-if-unknown).
    Mirrors _parse_updater: a typo returns None so the caller falls through to the
    safe OFF default — a typo never silently ENABLES the unattended exam."""
    if raw is None:
        return None
    v = str(raw).strip().strip('"').strip("'").lower()
    if v == "":
        return None
    if v in _DREAM_OFF_TOKENS:
        return "0"
    if v in _DREAM_ON_TOKENS:
        return "1"
    return None


def resolve_checkup():
    """Effective checkup setting '1' (on) / '0' (off). Precedence:
    AGENTWARE_CHECKUP env -> config.env -> default OFF. Read per-run; the tick job
    + the `checkup --status-line` hook gate on it (a runtime kill switch — no
    uninstall of the shared tick needed)."""
    env = _parse_checkup(os.environ.get(CHECKUP_KEY))
    if env is not None:
        return env
    cfg = _parse_checkup(_read_config_key(CHECKUP_KEY))
    if cfg is not None:
        return cfg
    return CHECKUP_DEFAULT


def resolve_checkup_llm():
    """Effective bounded-LLM-pass setting '1'/'0'. Precedence:
    AGENTWARE_CHECKUP_LLM env -> config.env -> default ON. Disabling it leaves the
    deterministic signals untouched (Q4) and is how a hermetic run avoids a real
    spawn without monkeypatching the seam."""
    env = _parse_checkup(os.environ.get(CHECKUP_LLM_KEY))
    if env is not None:
        return env
    cfg = _parse_checkup(_read_config_key(CHECKUP_LLM_KEY))
    if cfg is not None:
        return cfg
    return CHECKUP_LLM_DEFAULT


def _checkup_report_path(kdir):
    return os.path.join(kdir, CHECKUP_REPORT_REL)


def _checkup_read_report(kdir):
    """The current report body, or None (tolerant of a missing file)."""
    try:
        with open(_checkup_report_path(kdir), "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _checkup_content_hash(text):
    """Stable content hash for the `.ack` freshness watermark (audit L6-1)."""
    import hashlib
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _checkup_last_run_ts(kdir):
    """ISO 'Z' timestamp of checkup's own last run from logs/checkup/.last-run, or
    None. The due-gate keys off THIS (checkup-owned), never updater/dream state."""
    if not kdir:
        return None
    try:
        with open(os.path.join(kdir, CHECKUP_LASTRUN_REL), "r",
                  encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _checkup_write_last_run(kdir):
    """Stamp checkup's last-run watermark (best-effort; never raises)."""
    try:
        p = os.path.join(kdir, CHECKUP_LASTRUN_REL)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(_now_iso() + "\n")
    except OSError:
        pass


def _checkup_anchor_epoch(now):
    """STABLE window anchor = floor(now) to the cadence boundary — never raw now
    (INV-1, audit L6-2). Identical for every re-run within a 7-day bucket, so the
    set of in-window events (and thus the report) is byte-stable across idempotent
    re-runs. AGE thresholds (72h stall, 48h stale) still use live `now` but the
    report renders only the BOOLEAN crossing (no live age), so back-to-back re-runs
    stay byte-identical."""
    return int(now // CHECKUP_INTERVAL_SEC) * CHECKUP_INTERVAL_SEC


def _checkup_due(kdir, now):
    """Due when >= CHECKUP_INTERVAL_SEC (7d) since checkup's OWN last run. Always-
    due when never run — sleep-proof: keyed off checkup's last-run WALL-CLOCK, NOT
    the tick cadence and NOT updater/dream state (audit L5-3; mirrors
    _dream_cadence_due, never _pkg_updater_due)."""
    last = _pkg_iso_to_epoch(_checkup_last_run_ts(kdir))
    if last is None:
        return True
    return (now - last) >= CHECKUP_INTERVAL_SEC


_EXPORTS = [
    "CHECKUP_KEY",
    "CHECKUP_DEFAULT",
    "CHECKUP_LLM_KEY",
    "CHECKUP_LLM_DEFAULT",
    "CHECKUP_INTERVAL_SEC",
    "CHECKUP_WINDOW_DAYS",
    "CHECKUP_LEARNING_FRESH_DAYS",
    "CHECKUP_CORPUS_ENTRIES_MAX",
    "CHECKUP_WORKTREE_MAX",
    "CHECKUP_RECALL_P50_MS_MAX",
    "CHECKUP_HEARTBEAT_STALE_H",
    "MIN_FROZEN_CYCLES",
    "CHECKUP_DIR_REL",
    "CHECKUP_REPORT_REL",
    "CHECKUP_ACK_REL",
    "CHECKUP_LASTRUN_REL",
    "CHECKUP_FROZEN_METRICS",
    "_parse_checkup",
    "resolve_checkup",
    "resolve_checkup_llm",
    "_checkup_report_path",
    "_checkup_read_report",
    "_checkup_content_hash",
    "_checkup_last_run_ts",
    "_checkup_write_last_run",
    "_checkup_anchor_epoch",
    "_checkup_due",
]

__all__ = list(_EXPORTS)
