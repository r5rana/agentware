"""agentware_lib.core.pkgstate -- updater STATE and its cross-process lock (layer: core).

Owns: the machine-local updater state file under `~/.agentware` and the lock that
serialises access to it -- the paths (UPDATE_STATE_NAME / UPDATE_STATE_LOCK_NAME /
UPDATER_LOCK_REL / _pkg_state_path / _pkg_state_lock_path), the reentrant
cross-process lock (_pkg_state_lock and its two guard globals), the read / write /
read-modify-write helpers (_pkg_state_read / _pkg_state_write / _pkg_state_update)
and the quarantine list (_pkg_quarantine_tag / _pkg_is_quarantined /
MAX_QUARANTINED_TAGS).

Quarantine is why this is state rather than a cache: a tag that failed to apply is
recorded so the updater does not retry it forever, re-breaking the tree on every
tick. The list is CAPPED, because an unbounded quarantine is itself a way to grow
a state file without limit.

Every read and write goes through the pkghome security assertion first, so a
tampered or world-writable state file is refused rather than trusted -- the same
fail-closed rule the trust anchor uses, applied to the file that records WHICH
version we are on.

Does NOT own: the filesystem primitives it asserts through -> core.pkghome; the
signing-key anchor -> core.pkgtrust; tag resolution and verification ->
core.pkgchannel; applying an update -> features.pkgapply; scheduling one ->
features.pkgupdater.
Layer deps: core -> core only (core.pkghome, core.dates) plus stdlib.
Fan-in: 3 at landing (see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all.
"""

import contextlib
import json
import os
import threading
import time

# POSIX advisory locking, absent on some platforms: the lock degrades to the
# in-process guard rather than failing to import (the monolith's own contract).
try:
    import fcntl as _fcntl
except ImportError:            # pragma: no cover - non-POSIX
    _fcntl = None

from agentware_lib.core.dates import _now_iso
from agentware_lib.core.pkghome import (
    _PkgStateError,
    _pkg_assert_secure_path,
    _pkg_home_dir,
    _pkg_secure_atomic_write,
    _pkg_secure_home,
)


# HOME-local, gitignored, machine-local state + trust anchor. Both DERIVE from
# HOME_CONFIG's directory so a test that patches HOME_CONFIG isolates them
# hermetically (R-LOC-03) and they never land in the KB or the package tree.
UPDATE_STATE_NAME = "update-state.json"


UPDATE_STATE_LOCK_NAME = "update-state.lock"   # sidecar RMW flock (audit B2)


# Single-writer updater lock — DISTINCT from DREAM_LOCK_REL so the updater and a
# nightly dream can never interleave git mutations on the same work tree.
UPDATER_LOCK_REL = os.path.join(".cache", "updater.lock")


MAX_QUARANTINED_TAGS = 50     # bounded quarantine ledger (anti-thrash, not per-tick spam)


def _pkg_state_path():
    return os.path.join(_pkg_home_dir(), UPDATE_STATE_NAME)


# --- update-state sidecar single-writer lock (cross-process flock + RLock) -----
# _pkg_secure_atomic_write is atomic PER WRITE, but atomicity is NOT mutual
# exclusion: two writers that each read -> merge -> write LOSE an update (the second
# clobbers the first). The updater lock (.cache/updater.lock) serializes apply-vs-
# apply, but genuinely-UNLOCKED writers also touch the sidecar — `update --check`
# via _pkg_resolve_verified_tag, the tick's last_check_ts stamp written BEFORE the
# lock is acquired, and the layer-2 staleness pre-hook — and could clobber a locked
# apply's pending_apply / last_good_sha (a narrow but real first-apply stranding,
# audit B2). So EVERY sidecar read-modify-write takes THIS HOME-scoped lock,
# independent of the updater lock. Reentrant + thread-safe + cross-process,
# degrading BEST-EFFORT (never raises for a lock problem) exactly like _index_lock.
_PKG_STATE_LOCK_GUARD = threading.RLock()


_PKG_STATE_LOCK_STATE = {}  # abs lockpath -> {"file": <fileobj|None>, "depth": int}


def _pkg_state_lock_path():
    return os.path.join(_pkg_home_dir(), UPDATE_STATE_LOCK_NAME)


@contextlib.contextmanager
def _pkg_state_lock():
    """Serialize a _pkg_state_read -> modify -> _pkg_state_write critical section
    across processes (flock) AND threads (RLock), reentrantly. The flock is taken
    only on the OUTERMOST acquisition; nested acquisitions bump a depth counter so a
    lock holder (e.g. an apply-path block that calls the already-locked
    _pkg_state_update) never self-deadlocks. NEVER raises for a lock-acquisition
    problem (best-effort degrade: the RLock still serializes same-process threads).
    Modeled byte-for-byte on _index_lock (the P1.7 index write lock)."""
    lockpath = _pkg_state_lock_path()
    _PKG_STATE_LOCK_GUARD.acquire()
    try:
        st = _PKG_STATE_LOCK_STATE.get(lockpath)
        if st is None:
            st = {"file": None, "depth": 0}
            _PKG_STATE_LOCK_STATE[lockpath] = st
        if st["depth"] == 0 and _fcntl is not None:
            f = None
            try:
                d = os.path.dirname(lockpath) or "."
                os.makedirs(d, exist_ok=True)
                f = open(lockpath, "a+")
                _fcntl.flock(f.fileno(), _fcntl.LOCK_EX)
            except OSError:
                if f is not None:
                    try:
                        f.close()
                    except OSError:
                        pass
                f = None  # best-effort: RLock still serializes same-process threads
            st["file"] = f
        st["depth"] += 1
        try:
            yield
        finally:
            st["depth"] -= 1
            if st["depth"] == 0:
                f = st.get("file")
                if f is not None:
                    try:
                        _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)
                    except OSError:
                        pass
                    try:
                        f.close()
                    except OSError:
                        pass
                st["file"] = None
    finally:
        _PKG_STATE_LOCK_GUARD.release()


def _pkg_state_read():
    """Read the HOME-local update-state sidecar as a dict.

    TOLERANT of a missing or corrupt/unparseable file (returns {} = empty). But
    FAIL-CLOSED (raises _PkgStateError) on a security-tampered sidecar — a
    symlink, wrong-owner, or group/world-writable file — so a caller never trusts
    a redirected or attacker-writable rollback target."""
    path = _pkg_state_path()
    if not os.path.lexists(path):
        return {}
    _pkg_assert_secure_path(path, is_dir=False)   # raises on tamper (fail-closed)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return {}
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _pkg_state_write(state):
    """Atomically persist the sidecar (mode 0600) under a secured ~/.agentware
    (0700). SURFACES any failure by raising — callers MUST fail closed on a full
    or read-only HOME (an update must never advance the tree with no rollback
    target). Never follows a symlink."""
    _pkg_secure_home()                    # raises on a tampered/symlinked home dir
    text = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _pkg_secure_atomic_write(_pkg_state_path(), text, mode=0o600)


def _pkg_state_update(**fields):
    """Read-modify-write convenience: merge `fields` into the sidecar and persist.
    Raises _PkgStateError / OSError on write failure (fail-closed by design). The
    whole RMW runs under _pkg_state_lock so a concurrent (possibly unlocked) writer
    cannot clobber a locked apply's fields (audit B2)."""
    with _pkg_state_lock():
        state = _pkg_state_read()
        state.update(fields)
        _pkg_state_write(state)
        return state


def _pkg_quarantine_tag(tag, reason=None):
    """Append `tag` to the bounded quarantined_tags ledger (dedup, capped).
    Persisted so a broken release is not re-attempted every tick (anti-thrash).
    RMW under _pkg_state_lock so a concurrent writer cannot lose the ledger append."""
    with _pkg_state_lock():
        state = _pkg_state_read()
        q = state.get("quarantined_tags")
        if not isinstance(q, list):
            q = []
        entry = {"tag": tag, "reason": (reason or "")[:200], "ts": _now_iso()}
        q = [e for e in q if not (isinstance(e, dict) and e.get("tag") == tag)]
        q.append(entry)
        if len(q) > MAX_QUARANTINED_TAGS:
            q = q[-MAX_QUARANTINED_TAGS:]
        state["quarantined_tags"] = q
        _pkg_state_write(state)
        return state


def _pkg_is_quarantined(tag):
    q = _pkg_state_read().get("quarantined_tags") or []
    return any(isinstance(e, dict) and e.get("tag") == tag for e in q)


_EXPORTS = [
    "UPDATE_STATE_NAME",
    "UPDATE_STATE_LOCK_NAME",
    "UPDATER_LOCK_REL",
    "MAX_QUARANTINED_TAGS",
    "_pkg_state_path",
    "_PKG_STATE_LOCK_GUARD",
    "_PKG_STATE_LOCK_STATE",
    "_pkg_state_lock_path",
    "_pkg_state_lock",
    "_pkg_state_read",
    "_pkg_state_write",
    "_pkg_state_update",
    "_pkg_quarantine_tag",
    "_pkg_is_quarantined",
]

__all__ = list(_EXPORTS)
