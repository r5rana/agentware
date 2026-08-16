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
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: the lock/gate are read-mostly over KB run-state (INV-2); `now`/`load`
are injectable so tests are wall-clock-free; the lockfile lives under the already-
gitignored .cache/ (never staged, INV never touched). stdlib-only (INV-6).
"""

import contextlib
import json
import os
import threading
import time

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX
    _fcntl = None

import agentware_lib.core.kblayout as kblayout
from agentware_lib.core.kblayout import _ensure_kb_gitignore


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


def _dream_lock_path(kdir, lock_rel=None):
    """Path to a single-writer lockfile under `kdir`. `lock_rel` defaults to the
    dream lock; the dedicated updater passes UPDATER_LOCK_REL so it serializes on a
    DISTINCT lock (never contends with dream)."""
    return os.path.join(kdir, lock_rel or DREAM_LOCK_REL)


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


def dream_lock_is_stale(rec, now=None, ttl=DREAM_LOCK_TTL_SEC,
                        pid_alive_never_stale=False):
    """A lock is STALE (reclaimable) when its pid is dead OR its mtime is older
    than `ttl`. Pure given `rec`; `now` overridable for tests.

    `pid_alive_never_stale=True` (the updater/self-heal path) makes a LIVE-pid lock
    NEVER stale regardless of mtime: the updater does not heartbeat its lock during
    an apply, and a legitimate apply (smoke + migrate) can run longer than `ttl`, so
    an age-only TTL would let self-heal reclaim a LIVE apply's lock straight into a
    concurrent `git reset --hard` (audit C16). Dream keeps the default (it heartbeats
    its own lock, so an age-expired dream lock is genuinely hung)."""
    now = now if now is not None else time.time()
    if not isinstance(rec, dict):
        return True
    if not _dream_pid_alive(rec.get("pid")):
        return True
    if pid_alive_never_stale:
        return False   # a LIVE holder is never stale (an apply may run > ttl)
    ts = rec.get("mtime")
    if not isinstance(ts, (int, float)) or (now - ts) > ttl:
        return True
    return False



# In-process guards for the critical section: one RLock protecting the registry,
# and one Lock per lockfile path. Moved with `_dream_acquire_critsec`, which is
# meaningless without them.
_DREAM_ACQ_GUARD = threading.RLock()
_DREAM_ACQ_LOCKS = {}  # abs "<lock>.acq" path -> threading.Lock


# The two ATOMIC primitives the multi-lock acquire path needs. Moved WITH the
# union (task 41): the donor's narrower acquire never had a critical section or
# a steal path, so importing the superset without these would be a NameError at
# the first contended acquire — the exact shape the port-time import check
# exists to catch.
def _dream_atomic_steal_lock(path, pid):
    """Atomically claim the right to reclaim a STALE lock at `path` via rename:
    only ONE racer's `os.rename(path -> unique)` can succeed — the source vanishes
    for every other racer, who then get FileNotFoundError. The winner removes the
    stolen file and returns True (to recreate the lock via O_CREAT|O_EXCL); losers
    return False. This replaces a remove-then-recreate reclaim, whose window let two
    racers both `os.remove` — the second deleting the FIRST's freshly-recreated lock
    (a double-grant TOCTOU, audit B11). O_EXCL still arbitrates the recreate, so even
    a fresh acquirer that slips in between the steal and the recreate is honored."""
    steal = "%s.stealing.%d" % (path, pid)
    try:
        os.rename(path, steal)
    except OSError:
        return False   # another racer already stole it (gone) — we lost
    try:
        os.remove(steal)
    except OSError:
        pass
    return True


@contextlib.contextmanager
def _dream_acquire_critsec(lock_path):
    """Serialize the WHOLE acquire-or-reclaim critical section of dream_acquire_lock
    across PROCESSES (an fcntl.flock on a sibling `<lock>.acq` meta-file) AND threads
    (a per-path threading.Lock), so two reclaimers can never both steal-and-recreate a
    stale lock. A rename/remove reclaim is racy by PATH, not inode: without this the
    loser can steal the WINNER's freshly-created lock and both believe they hold the
    single-writer lock (audit B11 follow-up — the rename-steal alone does NOT close
    it). Distinct locks (dream vs updater) use distinct `.acq` files, so they never
    contend. Best-effort: if fcntl is unavailable or the meta-file cannot be opened,
    the thread lock still serializes same-process callers and we NEVER crash (degrades
    to the prior sub-µs residual window on non-POSIX/rare-FS)."""
    acq_path = lock_path + ".acq"
    with _DREAM_ACQ_GUARD:
        tl = _DREAM_ACQ_LOCKS.get(acq_path)
        if tl is None:
            tl = threading.Lock()
            _DREAM_ACQ_LOCKS[acq_path] = tl
    tl.acquire()
    f = None
    try:
        if _fcntl is not None:
            try:
                os.makedirs(os.path.dirname(acq_path) or ".", exist_ok=True)
                f = open(acq_path, "a+")
                _fcntl.flock(f.fileno(), _fcntl.LOCK_EX)
            except OSError:
                if f is not None:
                    try:
                        f.close()
                    except OSError:
                        pass
                f = None
        yield
    finally:
        if f is not None:
            try:
                _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                f.close()
            except OSError:
                pass
        tl.release()


def dream_acquire_lock(kdir, now=None, pid=None, ttl=DREAM_LOCK_TTL_SEC,
                       lock_rel=None, pid_alive_never_stale=False):
    """Acquire a single-writer lock under .cache/ (gitignored). `lock_rel` selects
    WHICH lock (default: dream.lock; the updater/tick pass UPDATER_LOCK_REL).

    Returns True on success, False when a FRESH lock is held by another owner.
    Reclaims a STALE lock (dead pid / expired mtime). ATOMIC on both edges: the
    lockfile is created with O_CREAT|O_EXCL (two acquirers can never both win the
    create), its pid+mtime are fsync'd BEFORE the fd is released (so a racer never
    sees a durable empty file to steal), and a stale-lock reclaim is an atomic
    rename-steal (never remove-then-recreate). An EMPTY/unparseable existing lock is
    treated as freshly-held unless its file mtime is itself older than ttl — closing
    the create/write double-grant window (audit B9). `pid_alive_never_stale=True`
    (the updater/self-heal path) makes a pid-alive lock NEVER stale regardless of
    mtime (an apply may legitimately run > ttl, audit C16). `now`/`pid` injectable.
    """
    now = now if now is not None else time.time()
    pid = pid if pid is not None else os.getpid()
    path = _dream_lock_path(kdir, lock_rel)
    rec = json.dumps({"pid": pid, "mtime": now}, sort_keys=True)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _ensure_kb_gitignore(kdir)
    except OSError:
        return False
    # Serialize the read-stale -> steal -> recreate sequence cross-process + cross-
    # thread so two reclaimers cannot both win (audit B11 follow-up).
    with _dream_acquire_critsec(path):
        for _attempt in (0, 1):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                existing = _dream_read_lock(path)
                if existing is None:
                    # Empty/unparseable: EITHER a concurrent O_EXCL acquirer that has
                    # created the file but not yet written its pid (a LIVE racer we
                    # must NOT steal from — the double-grant, audit B9), OR a crashed
                    # mid-write / corrupt file. Reclaim ONLY when its FILE mtime is
                    # itself older than ttl; a fresh empty file is treated as held.
                    try:
                        file_age = now - os.path.getmtime(path)
                    except OSError:
                        return False
                    if file_age <= ttl:
                        return False
                elif not dream_lock_is_stale(existing, now=now, ttl=ttl,
                                             pid_alive_never_stale=pid_alive_never_stale):
                    return False   # a FRESH lock is held by another owner
                # Stale (dead pid / expired mtime / stale-empty) -> steal ATOMICALLY
                # (rename) then retry the O_EXCL create once. Safe under the critsec:
                # no concurrent reclaimer can recreate a lock we would then steal.
                if not _dream_atomic_steal_lock(path, pid):
                    return False   # nothing to steal (already reclaimed) — we lost
                continue
            except OSError:
                return False
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(rec)
                    f.flush()
                    os.fsync(f.fileno())
                return True
            except OSError:
                return False
    return False


def dream_release_lock(kdir, pid=None, lock_rel=None):
    """Release the lock IFF `pid` (default: this process) owns it. `lock_rel`
    selects which lock (default: dream.lock)."""
    pid = pid if pid is not None else os.getpid()
    path = _dream_lock_path(kdir, lock_rel)
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
