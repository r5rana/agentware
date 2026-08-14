"""agentware_lib.features.dreamgate -- the dream idle-gate + single-writer lock + live-session guard (layer: features).

Owns: the dream CYCLE's admission control -- the lock/session/load constants
(DREAM_LOCK_REL, DREAM_LOCK_TTL_SEC, DREAM_SESSION_TTL_SEC, DREAM_LOAD_RATIO), the
live-loop-session detector (is_session_active), the single-writer concurrency lock
(_dream_lock_path / _dream_read_lock / _dream_pid_alive / dream_lock_is_stale /
dream_acquire_lock / dream_release_lock), and the idle-gate (_dream_load_state /
dream_gate_reason) that decides WHETHER a cycle may run. A pure, self-contained LEAF
the dream loop imports downward; it references nothing else in the dream domain, so
the dream<->dreamgate edge stays one-directional (loop->dreamgate) and acyclic.
Does NOT own: the maintenance STEPS + DREAM_STEP_FUNCS, the journal/metric/output
family, the KB-repoint family, and cmd_dream + register_dream -- still resident in the
entrypoint, arriving in later wave-6 seams at agentware_lib/features/dream.py
(+ dreamsteps); the scheduler installer -> agentware_lib.features.dreamsched; the
dream-health heartbeat -> agentware_lib.features.dream (see catalog/modules.json).
Layer deps: features.dreamgate -> core (kblayout) only -- all DOWNWARD; nothing above (ops/cli) is imported.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: the lock/gate are read-mostly over KB run-state (INV-2); `now`/`load`
are injectable so tests are wall-clock-free; the lockfile lives under the already-
gitignored .cache/ (never staged, INV never touched). stdlib-only (INV-6).
"""

import json
import os
import time

import agentware_lib.core.kblayout as kblayout


DREAM_LOCK_REL = os.path.join(".cache", "dream.lock")


DREAM_LOCK_TTL_SEC = 3600        # a held lock older than this is reclaimable (stale)


DREAM_SESSION_TTL_SEC = 1800     # a .loop whose liveness file is fresher = active


DREAM_LOAD_RATIO = 4.0           # skip when 1-min loadavg > ratio * cpu_count


def is_session_active(kdir, now=None):
    """Return the feature name of a LIVE agentware loop session, or None.

    Loop state is PER-FEATURE: agentware.sh keeps `work/<feature>/.loop` with a
    `.done` sentinel + a `live-stream.log` it touches each tool call. A feature is
    LIVE when its `.loop` exists, `.done` is ABSENT, and a liveness file
    (live-stream.log, else .iteration) was modified within DREAM_SESSION_TTL_SEC.
    Read-only; `now` is overridable for deterministic tests. Returns the FIRST
    (sorted) live feature so the idle-gate can name it in the skip reason.
    """
    now = now if now is not None else time.time()
    work = os.path.join(kdir, "work")
    if not os.path.isdir(work):
        return None
    try:
        feats = sorted(os.listdir(work))
    except OSError:
        return None
    for feat in feats:
        loop = os.path.join(work, feat, ".loop")
        if not os.path.isdir(loop):
            continue
        if os.path.exists(os.path.join(loop, ".done")):
            continue
        mtime = None
        for name in ("live-stream.log", ".iteration"):
            try:
                mtime = os.path.getmtime(os.path.join(loop, name))
                break
            except OSError:
                continue
        if mtime is not None and (now - mtime) <= DREAM_SESSION_TTL_SEC:
            return feat
    return None


def _dream_lock_path(kdir):
    return os.path.join(kdir, DREAM_LOCK_REL)


def _dream_read_lock(path):
    """Parse the dream lockfile into a dict, or None if absent/unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) else None
    except (OSError, ValueError):
        return None


def _dream_pid_alive(pid):
    """True iff `pid` is a live process on this host (signal-0 probe, stdlib)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # exists but owned by another user
    except OSError:
        return False
    return True


def dream_lock_is_stale(rec, now=None, ttl=DREAM_LOCK_TTL_SEC):
    """A lock is STALE (reclaimable) when its pid is dead OR its mtime is older
    than `ttl`. Pure given `rec`; `now` overridable for tests."""
    now = now if now is not None else time.time()
    if not isinstance(rec, dict):
        return True
    if not _dream_pid_alive(rec.get("pid")):
        return True
    ts = rec.get("mtime")
    if not isinstance(ts, (int, float)) or (now - ts) > ttl:
        return True
    return False


def dream_acquire_lock(kdir, now=None, pid=None, ttl=DREAM_LOCK_TTL_SEC):
    """Acquire the single-writer dream lock under .cache/ (gitignored).

    Returns True on success, False when a FRESH lock is held by another owner.
    Reclaims a STALE lock (dead pid / expired mtime). Reads the existing lock
    BEFORE writing anything, so a held-lock refusal mutates NOTHING. `now`/`pid`
    are injectable for deterministic tests (no real processes needed).
    """
    now = now if now is not None else time.time()
    pid = pid if pid is not None else os.getpid()
    path = _dream_lock_path(kdir)
    existing = _dream_read_lock(path)
    if existing is not None and not dream_lock_is_stale(existing, now=now, ttl=ttl):
        return False
    rec = {"pid": pid, "mtime": now}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        kblayout._ensure_kb_gitignore(kdir)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, sort_keys=True)
    except OSError:
        return False
    return True


def dream_release_lock(kdir, pid=None):
    """Release the dream lock IFF `pid` (default: this process) owns it."""
    pid = pid if pid is not None else os.getpid()
    path = _dream_lock_path(kdir)
    rec = _dream_read_lock(path)
    if rec is not None and rec.get("pid") == pid:
        try:
            os.remove(path)
        except OSError:
            pass


def _dream_load_state(load=None):
    """Return (is_high, load1, threshold). `load` overridable for tests.

    threshold = DREAM_LOAD_RATIO * cpu_count. A platform without getloadavg
    (e.g. some Windows) reports (False, None, None) — load never blocks there.
    """
    if load is not None:
        return load
    try:
        load1 = os.getloadavg()[0]
    except (OSError, AttributeError):
        return (False, None, None)
    threshold = DREAM_LOAD_RATIO * (os.cpu_count() or 1)
    return (load1 > threshold, load1, threshold)


def dream_gate_reason(kdir, force=False, now=None, load=None):
    """Idle-gate decision: a SKIP reason string, or None to proceed.

    With `force=True` the idle signals (active session, high load) are bypassed
    for a manual run — the concurrency lock is enforced separately. `now`/`load`
    are injectable so the gate is testable without real processes/load.
    """
    if force:
        return None
    active = is_session_active(kdir, now=now)
    if active:
        return "an agentware loop session is active (%s)" % active
    is_high, load1, threshold = _dream_load_state(load)
    if is_high:
        return ("system load is high (loadavg1=%s > %s) — deferring"
                % (load1, threshold))
    return None


_EXPORTS = [
    "DREAM_LOCK_REL",
    "DREAM_LOCK_TTL_SEC",
    "DREAM_SESSION_TTL_SEC",
    "DREAM_LOAD_RATIO",
    "is_session_active",
    "_dream_lock_path",
    "_dream_read_lock",
    "_dream_pid_alive",
    "dream_lock_is_stale",
    "dream_acquire_lock",
    "dream_release_lock",
    "_dream_load_state",
    "dream_gate_reason",
]
