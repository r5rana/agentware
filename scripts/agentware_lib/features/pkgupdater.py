"""agentware_lib.features.pkgupdater -- WHEN to update, and self-healing (layer: features).

Owns: the cadence and health surface around the update channel -- the operator
setting and its parser (UPDATER_KEY / UPDATER_DEFAULT / _parse_updater /
resolve_updater), the due-check (UPDATER_CHECK_INTERVAL_SEC / _pkg_iso_to_epoch /
_pkg_updater_due), the tick entry point (_pkg_updater_run), the lock-status word
(_pkg_lock_status_word), the SELF-HEAL path (_pkg_self_heal), the staleness
pre-hook and its bound (PKG_STALENESS_MAX_AGE_SEC / _pkg_staleness_prehook) and the
heartbeat emitter (_pkg_emit_heartbeat).

WHY CADENCE IS SEPARATE FROM APPLY
"Should we look for an update now?" and "apply this verified tag" fail in different
ways and are asked from different places -- the loop's tick, a session pre-hook, an
explicit `update --check`. Keeping the decision here means the apply path has no
opinion about time, and this module has none about git.

SELF-HEAL is the recovery entry point the loop calls when the package looks wrong:
it reconciles pending state, clears a stale lock left by a killed process, and
reports what it fixed rather than repairing silently. STALENESS is the inverse
signal -- an updater that has not successfully checked in for
PKG_STALENESS_MAX_AGE_SEC is itself a fault, because a silent updater and a healthy
one look identical from outside.

Does NOT own: applying or rolling back -> features.pkgapply; crash recovery ->
features.pkgreconcile; tag verification -> core.pkgchannel; state and lock ->
core.pkgstate; the `update` CLI -> features.updatecmd.
Layer deps: features -> core (config, dates, gitio, pkgstate, pkgchannel, pkghome)
and same-layer features (pkgapply, pkgreconcile, dreamgate). All DOWNWARD or
same-layer.
Fan-in: 1 at landing (see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all.
"""

import datetime
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
from agentware_lib.core.dates import _now_iso
from agentware_lib.core.gitio import (
    _git,
    _git_head,
    _git_noninteractive_env,
    _git_timed,
    git_is_work_tree,
)
from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.core.pkghome import _PkgStateError, _pkg_home_dir
from agentware_lib.core.pkgchannel import PKG_FETCH_TIMEOUT
from agentware_lib.core.pkgstate import (
    UPDATER_LOCK_REL,
    _pkg_state_lock_path,
    _pkg_state_read,
    _pkg_state_update,
)
from agentware_lib.features.pkgapply import _pkg_run_update, _pkg_version
from agentware_lib.features.pkgreconcile import _pkg_reconcile_pending
import agentware_lib.features.migratecmd as _kbmigrate
from agentware_lib.features.dreamgate import (
    _dream_lock_path,
    _dream_read_lock,
    dream_acquire_lock,
    dream_lock_is_stale,
    dream_release_lock,
    is_session_active,
)


UPDATER_CHECK_INTERVAL_SEC = 6 * 3600   # due-gate: the tick checks ~every 6h


# --- Task 8 + 14: the dedicated-updater tick job (due-gated, locked, guarded) -
UPDATER_KEY = "AGENTWARE_UPDATE"


UPDATER_DEFAULT = "0"       # opt-in + OFF, like dream (consequential automation)


def _parse_updater(raw):
    """Lenient on/off parse for the updater flag ('1'/'0'/None-if-unknown)."""
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


def resolve_updater():
    """Effective updater setting '1' (on) / '0' (off). Precedence:
    AGENTWARE_UPDATE env -> config.env -> default OFF. Read per-run; the tick job
    and the audit check gate on it (a runtime kill switch)."""
    env = _parse_updater(os.environ.get(UPDATER_KEY))
    if env is not None:
        return env
    cfg = _parse_updater(_read_config_key(UPDATER_KEY))
    if cfg is not None:
        return cfg
    return UPDATER_DEFAULT


def _pkg_iso_to_epoch(ts):
    """Epoch seconds from an ISO 'Z' timestamp, or None if unparseable."""
    try:
        dt = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _pkg_updater_due(kdir, now):
    """Due when >= UPDATER_CHECK_INTERVAL_SEC (~6h) since last_check_ts. A crashed
    prior apply (pending_apply marker / needs_manual_recovery) is ALWAYS due so the
    reconcile / escalation runs promptly. Sleep-proof: keyed off wall-clock, not
    the scheduler cadence."""
    state = _pkg_state_read()
    if state.get("pending_apply") or state.get("needs_manual_recovery"):
        return True
    last = _pkg_iso_to_epoch(state.get("last_check_ts"))
    if last is None:
        return True
    return (now - last) >= UPDATER_CHECK_INTERVAL_SEC


def _pkg_updater_run(kdir, now, target=None):
    """The dedicated-updater tick job. Defers when a loop session is active (never
    lands an apply mid-loop), records last_check_ts on EVERY attempt (anti-thrash),
    then delegates to `_pkg_run_update`, which — UNDER the single-writer lock —
    reconciles a crashed prior apply FIRST and then runs the normal apply. SMOKE
    only (never eval --record --gate)."""
    target = target or REPO_ROOT
    if is_session_active(kdir):
        return {"status": "deferred",
                "detail": "loop session active — deferring apply to a later tick"}
    now_iso = _now_iso()
    try:
        _pkg_state_update(last_check_ts=now_iso)   # every attempt, even on failure
    except (OSError, _PkgStateError):
        pass
    return _pkg_run_update(target, kdir, now_iso=now_iso)


def _pkg_lock_status_word(kdir):
    """Read-only updater/tick lock state: 'free' | 'held' | 'stale'. Modeled on
    dream_acquire_lock's read-before-write — mutates NOTHING. A LIVE-pid lock is
    NEVER 'stale' regardless of mtime (an apply may run > ttl; audit C16), so the
    reverse-guard never self-heals a live apply out from under itself."""
    rec = _dream_read_lock(_dream_lock_path(kdir, UPDATER_LOCK_REL))
    if rec is None:
        return "free"
    return "stale" if dream_lock_is_stale(rec, pid_alive_never_stale=True) else "held"


def _pkg_self_heal(kdir, target=None):
    """One-shot self-heal for a crashed prior apply (stale updater lock): reclaim the
    stale lock and reconcile (re-smoke -> finalize / rollback / strand) under the
    updater lock. Mirrors Task 8's loop-start reconcile.

    Reclaim is delegated to dream_acquire_lock's ATOMIC rename-steal (no pre-remove
    TOCTOU, audit B11) with `pid_alive_never_stale=True` so a LIVE apply's lock is
    NEVER reclaimed into a concurrent `git reset --hard` (audit C16). A lock held by
    a live process returns status 'locked' WITHOUT reconciling."""
    target = target or REPO_ROOT
    if not dream_acquire_lock(kdir, lock_rel=UPDATER_LOCK_REL,
                              pid_alive_never_stale=True):
        return {"status": "locked",
                "detail": "updater lock held by a LIVE process — not healing"}
    try:
        r = _pkg_reconcile_pending(target, kdir)
        return r or {"status": "clean", "detail": "no crashed apply to heal"}
    finally:
        dream_release_lock(kdir, lock_rel=UPDATER_LOCK_REL)


# --- Task 10: Layer-2 on-use package-staleness pre-hook (non-blocking) --------
# For scheduler-OFF but active machines: if the last signed-channel fetch is >24h
# old, do a READ-ONLY tag fetch + WARN so an active user cannot stay stale. NEVER
# applies executable code inline (that is the dedicated updater's job) and ALWAYS
# returns 0 (a bare non-zero exit in a pre-hook is emitted as a pre_hook_abort).
PKG_STALENESS_MAX_AGE_SEC = 24 * 3600


def _pkg_staleness_prehook(kdir=None, target=None, max_age_sec=None, now=None):
    """Layer-2 staleness nudge. Returns 0 on EVERY path (fresh/stale/offline/
    error) — the caller wraps it with `|| true` and it NEVER calls exit."""
    target = target or REPO_ROOT
    max_age_sec = max_age_sec if max_age_sec is not None else PKG_STALENESS_MAX_AGE_SEC
    now = now if now is not None else time.time()
    if not git_is_work_tree(target):
        return 0
    try:
        state = _pkg_state_read()
    except _PkgStateError:
        return 0   # tampered sidecar — never block the loop on it here
    last = _pkg_iso_to_epoch(state.get("last_fetch_ts"))
    if last is not None and (now - last) < max_age_sec:
        return 0   # fresh — silent no-op
    # Stale or never fetched: read-only tag fetch (remote-tracking refs only).
    env = _git_noninteractive_env()
    fetch = _git_timed(target, "fetch", "--tags", "--quiet", env=env,
                       timeout=PKG_FETCH_TIMEOUT)
    now_iso = _now_iso()
    try:
        _pkg_state_update(last_fetch_ts=now_iso, last_check_ts=now_iso)
    except (OSError, _PkgStateError):
        pass   # never block on a state-write failure
    if fetch.returncode != 0:
        print("[pkg-staleness] package channel fetch skipped (offline/timeout) — "
              "last fetch >24h ago; not blocking.")
        return 0
    msg = ("[pkg-staleness] refreshed the signed update channel "
           "(>24h since last fetch).")
    if resolve_updater() != "1":
        msg += (" Automatic updates are OFF — run `scripts/agentware update` or "
                "enable with `scripts/agentware config --set-updater on`.")
    print(msg)
    return 0


def _pkg_emit_heartbeat(kdir):
    """Append ONE pkg_version heartbeat to logs/metrics.jsonl (ts, pkg_version,
    schema, updater). Best-effort + read-only w.r.t. the KB/index; never raises
    (mirrors _dream_emit_metric)."""
    try:
        rec = {"event": "pkg_version", "ts": _now_iso(),
               "pkg_version": _pkg_version(),
               "schema": _kbmigrate._kb_schema_read(kdir),
               "updater": resolve_updater()}
        logs = os.path.join(kdir, "logs")
        os.makedirs(logs, exist_ok=True)
        with open(os.path.join(logs, "metrics.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    except (OSError, _PkgStateError):
        # _pkg_version() falls back to _pkg_state_read().get("last_applied_version"),
        # so a tampered HOME sidecar raises _PkgStateError here — honor the
        # never-raises contract (a scheduled tick must not crash), audit A4.
        pass


_EXPORTS = [
    "UPDATER_KEY",
    "UPDATER_DEFAULT",
    "UPDATER_CHECK_INTERVAL_SEC",
    "PKG_STALENESS_MAX_AGE_SEC",
    "_parse_updater",
    "resolve_updater",
    "_pkg_iso_to_epoch",
    "_pkg_updater_due",
    "_pkg_updater_run",
    "_pkg_lock_status_word",
    "_pkg_self_heal",
    "_pkg_staleness_prehook",
    "_pkg_emit_heartbeat",
]

__all__ = list(_EXPORTS)
