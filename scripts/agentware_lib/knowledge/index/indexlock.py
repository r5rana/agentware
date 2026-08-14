"""agentware_lib.knowledge.index.indexlock — the same-host index mutation lock (layer: knowledge).

Owns: the gitignored lockfile path helper (_index_lock_path) and the reentrant,
thread-safe, cross-process critical section (_index_lock) that every mutating
verb wraps around its whole `load_index -> mutate -> save_index`, plus the
process-wide state it needs (_INDEX_LOCK_GUARD / _INDEX_LOCK_STATE) and the
guarded `fcntl` import (_fcntl) that lets the whole thing degrade best-effort
where flock is unavailable.
Does NOT own: the atomic index writer (save_index) ->
agentware_lib.knowledge.index.indexwrite; the READ primitives + the lockfile's
relative name (KB_INDEX_LOCK_REL lives with the rest of the KB layout in
agentware_lib.core.kblayout, which also gitignores it) -> indexread / core;
the rebuild orchestrators that TAKE this lock -> indexrebuild.
Layer deps: knowledge -> core (module-level core.kblayout for KB_INDEX_LOCK_REL),
all DOWNWARD. This module is a SINK of the index package: it imports no sibling,
so it can never participate in an index-internal cycle.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

## Why a lock exists at all, when the writer is already atomic

`save_index` is ATOMIC (tempfile + fsync + os.replace), but atomicity alone does
NOT prevent a LOST UPDATE: two same-host agents that each `load_index -> mutate
-> save_index` will have the SECOND silently overwrite the FIRST (this project
runs 2+ execution agents concurrently, plus a nightly dream and a self-updater).
Atomicity protects a READER from seeing a half-written file; it does nothing for
two WRITERS. The lost-update window opens at the READ, which is why the lock must
live in the CALLER, wrapping the whole read-modify-write, and NOT inside
`save_index` — by the time `save_index` is entered the stale read already
happened.

## The three properties, and why each is separately necessary

  * cross-PROCESS via `fcntl.flock` — two agent processes on one host serialize;
  * cross-THREAD via a per-knowledge-dir `threading.RLock` — `flock` is per-OPEN-
    FILE-DESCRIPTION, so two threads in ONE process holding two different fds on
    the same file BOTH get the lock and neither blocks. The workflow harness and
    the test suite both run writers as threads, so flock alone is not enough. The
    RLock is keyed by knowledge dir rather than global so a thread writing team
    A's KB does not block a thread writing team B's;
  * RE-ENTRANT within one call stack — a holder that calls `rebuild_kb` (itself
    lock-wrapped) through `load_index`'s auto-rebuild would take a second LOCK_EX
    on a NEW fd and block on itself forever. A depth counter takes the flock only
    on the OUTERMOST acquisition.

Degrades BEST-EFFORT: where `fcntl` is unavailable (Windows / a rare FS) or the
lockfile cannot be opened, the RLock still serializes threads and nothing raises.
A lock-acquisition problem must never be the reason a knowledge write fails. The
cross-HOST serialization point remains `git push` (out of scope here).

Stdlib only, no network.
"""

import contextlib
import os
import threading

from agentware_lib.core.kblayout import KB_INDEX_LOCK_REL

# Guarded so a non-POSIX host still imports the package. `None` is the documented
# degraded mode, checked explicitly at the one acquisition site below.
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX (Windows/rare FS)
    _fcntl = None

# Process-wide, module-level state. `_INDEX_LOCK_GUARD` is a SHORT-HELD mutex over
# the state MAP alone (get-or-create must be atomic, or two threads racing on a
# never-before-seen knowledge dir would each build a record and then serialize
# against nothing). The RLock that actually guards a critical section is PER
# KNOWLEDGE DIR, inside the record — a single global one would make a thread
# writing team A's KB block a thread writing team B's, which is pure lost liveness
# for zero safety (they contend for nothing; their lockfiles differ).
_INDEX_LOCK_GUARD = threading.RLock()
# abs lockpath -> {"rlock": <RLock>, "file": <fileobj|None>, "depth": int}
_INDEX_LOCK_STATE = {}


def _index_lock_path(kdir):
    """Absolute path to the gitignored same-host index mutation lockfile.

    ABSOLUTE and normalized: two callers reaching one knowledge dir by different
    relative paths must map to the SAME state-map key, or they would each take a
    private lock and serialize against nothing.
    """
    return os.path.abspath(os.path.join(kdir, KB_INDEX_LOCK_REL))


def _lock_state(lockpath):
    """Get-or-create the per-knowledge-dir lock record, atomically."""
    with _INDEX_LOCK_GUARD:
        st = _INDEX_LOCK_STATE.get(lockpath)
        if st is None:
            st = {"rlock": threading.RLock(), "file": None, "depth": 0}
            _INDEX_LOCK_STATE[lockpath] = st
        return st


def _acquire_flock(lockpath):
    """Open + LOCK_EX the sidecar lockfile. Returns the file object, or None.

    Split out of `_index_lock` so the context manager stays within the per-function
    ceiling and so the best-effort degrade has exactly ONE definition. Never
    raises: on any OSError (missing dir, read-only FS, a filesystem with no flock)
    the fd is closed and None is returned, leaving the caller's RLock as the only
    — still correct, just process-local — serialization.
    """
    if _fcntl is None:
        return None
    f = None
    try:
        d = os.path.dirname(lockpath) or "."
        os.makedirs(d, exist_ok=True)
        f = open(lockpath, "a+")
        _fcntl.flock(f.fileno(), _fcntl.LOCK_EX)
        return f
    except OSError:
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
        return None


def _release_flock(f):
    """LOCK_UN + close, best-effort. The mirror of `_acquire_flock`."""
    if f is None:
        return
    try:
        _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        f.close()
    except OSError:
        pass


@contextlib.contextmanager
def _index_lock(kdir):
    """Serialize a `load_index -> mutate -> save_index` critical section.

    Re-entrant + thread-safe + cross-process (see the module note above). Acquire
    it around the WHOLE read-modify-write of a mutating verb — a lock only INSIDE
    `save_index` would NOT prevent a lost update, because the read already
    happened. Always paired via the context manager so an exception or an early
    return releases it. Never raises for a lock-acquisition problem (best-effort
    degrade).
    """
    lockpath = _index_lock_path(kdir)
    st = _lock_state(lockpath)
    st["rlock"].acquire()  # RLock: reentrant per-thread, serializes threads
    try:
        # Take the cross-process flock only on the OUTERMOST acquisition; a nested
        # (reentrant) acquisition just bumps the depth, so we never re-flock a fd
        # this process already holds — which would self-deadlock. Depth is only
        # ever read or written by the thread holding this record's RLock.
        if st["depth"] == 0:
            st["file"] = _acquire_flock(lockpath)
        st["depth"] += 1
        try:
            yield
        finally:
            st["depth"] -= 1
            if st["depth"] == 0:
                _release_flock(st.get("file"))
                st["file"] = None
    finally:
        st["rlock"].release()


# Explicit facade export list (public AND underscore-private) — the entrypoint
# binds these onto its own namespace via the re-export loop so external consumers
# (tests/_fixtures.load_cli, the dashboard sidecar) keep reaching them as CLI.<name>.
_EXPORTS = [
    "_index_lock",
    "_index_lock_path",
    "_lock_state",
]
