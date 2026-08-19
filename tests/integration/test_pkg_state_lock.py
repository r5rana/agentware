"""Atomic update-state sidecar RMW + atomic lock acquire/reclaim
(feature 260712-p2b-hardening-followups, Task 2 — audit B2/B9/B11/B14, C16).

  B2/B14 — a REAL multi-process contention test: N processes racing the updater
           lock yield exactly ONE holder, and N processes each merging a distinct
           key into the sidecar lose NO update (the flock serializes the RMW).
  B9     — an EMPTY/unparseable lockfile with a fresh mtime is treated as
           freshly-held (a concurrent O_EXCL acquirer mid-write), NOT reclaimed —
           closing the create/write double-grant window. Reclaimable once its own
           file mtime is older than ttl.
  B11    — a stale lock is reclaimed via an ATOMIC rename-steal (only one racer
           wins), never remove-then-recreate.
  C16    — a LIVE-pid lock is NEVER stale under pid_alive_never_stale (an apply may
           run > ttl), so it is never reclaimed into a concurrent apply.

Hermetic: the updater lock lives under a throwaway kdir; the sidecar under a
throwaway HOME (HOME_CONFIG patched, and RE-SET inside each worker process since
spawn re-imports with the real HOME). R-LOC-03: the real ~/.agentware is untouched.

Runner: python3 -m unittest tests.integration.test_pkg_state_lock -v
"""

import multiprocessing as mp
import os
import shutil
import tempfile
import time
import unittest

try:
    from tests._fixtures import load_cli
except ImportError:
    from _fixtures import load_cli


# --- module-level workers (must be picklable for the 'spawn' start method) ------
def _mp_acquire(payload):
    """Worker: race dream_acquire_lock on a shared kdir with THIS process's own live
    pid. Returns True iff it won the single-writer lock."""
    kdir, lock_rel, barrier = payload
    try:
        from tests._fixtures import load_cli as _lc
    except ImportError:
        from _fixtures import load_cli as _lc
    mod = _lc()
    try:
        barrier.wait(timeout=30)   # maximize real contention
    except Exception:
        pass
    return bool(mod.dream_acquire_lock(kdir, lock_rel=lock_rel))


def _mp_state_update(payload):
    """Worker: merge a UNIQUE key into the sidecar under the throwaway HOME. The
    sidecar flock serializes the read-modify-write; without it a concurrent writer
    would clobber (lose) another worker's key."""
    home, kdir, key, barrier = payload
    try:
        from tests._fixtures import load_cli as _lc
    except ImportError:
        from _fixtures import load_cli as _lc
    mod = _lc()
    mod.HOME_CONFIG = os.path.join(home, ".agentware", "config.env")
    try:
        barrier.wait(timeout=30)
    except Exception:
        pass
    mod._pkg_state_update(**{key: 1})
    return key


try:
    import fcntl as _HAVE_FCNTL  # noqa: F401
    _FLOCK = True
except ImportError:
    _FLOCK = False


@unittest.skipIf(os.name != "posix", "POSIX flock/lock semantics only")
class StateLockTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-statelock-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.kdir = os.path.join(self.base, "kb")
        os.makedirs(self.kdir)
        self._saved = self.mod.HOME_CONFIG
        self.addCleanup(setattr, self.mod, "HOME_CONFIG", self._saved)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")
        self.mod._pkg_secure_home()
        self.rel = self.mod.UPDATER_LOCK_REL
        self.ttl = self.mod.DREAM_LOCK_TTL_SEC

    def _lock_path(self):
        return self.mod._dream_lock_path(self.kdir, self.rel)

    # --- B9: empty/unparseable lockfile is freshly-held, not double-granted ---
    def test_pkgstatelock_empty_fresh_lockfile_is_freshly_held(self):
        lp = self._lock_path()
        os.makedirs(os.path.dirname(lp), exist_ok=True)
        open(lp, "w").close()                      # empty + fresh mtime
        # A concurrent O_EXCL acquirer that has not yet written its pid — we must
        # NOT steal it (that is the double-grant), so acquire is refused.
        self.assertFalse(self.mod.dream_acquire_lock(self.kdir, lock_rel=self.rel))
        self.assertTrue(os.path.exists(lp))        # not removed

    def test_pkgstatelock_empty_stale_lockfile_reclaimable(self):
        lp = self._lock_path()
        os.makedirs(os.path.dirname(lp), exist_ok=True)
        open(lp, "w").close()
        old = time.time() - (self.ttl + 100)
        os.utime(lp, (old, old))                   # crashed mid-write long ago
        self.assertTrue(self.mod.dream_acquire_lock(self.kdir, lock_rel=self.rel))

    def test_pkgstatelock_acquire_writes_pid_and_fsyncs(self):
        self.assertTrue(self.mod.dream_acquire_lock(self.kdir, lock_rel=self.rel))
        rec = self.mod._dream_read_lock(self._lock_path())
        self.assertEqual(rec["pid"], os.getpid())  # pid durably written, not empty

    # --- B11: atomic rename-steal reclaim ----------------------------------
    def test_pkgstatelock_atomic_steal_wins_once(self):
        lp = self._lock_path()
        os.makedirs(os.path.dirname(lp), exist_ok=True)
        with open(lp, "w") as f:
            f.write("stale")
        self.assertTrue(self.mod._dream_atomic_steal_lock(lp, 111))
        self.assertFalse(os.path.exists(lp))       # source vanished (renamed away)
        # a second steal of the now-absent path loses (nothing to steal)
        self.assertFalse(self.mod._dream_atomic_steal_lock(lp, 222))

    def test_pkgstatelock_dead_pid_lock_reclaimed(self):
        self.assertTrue(self.mod.dream_acquire_lock(self.kdir, pid=424242,
                                                    lock_rel=self.rel))   # stale
        self.assertTrue(self.mod.dream_acquire_lock(self.kdir, lock_rel=self.rel))
        self.assertEqual(self.mod._dream_read_lock(self._lock_path())["pid"],
                         os.getpid())

    def test_pkgstatelock_live_foreign_lock_not_granted(self):
        self.assertTrue(self.mod.dream_acquire_lock(self.kdir, pid=1,
                                                    lock_rel=self.rel))   # foreign live
        self.assertFalse(self.mod.dream_acquire_lock(self.kdir, lock_rel=self.rel))

    # --- C16: a live-pid lock is never stale regardless of mtime ------------
    def test_pkgstatelock_live_pid_old_mtime_never_stale(self):
        old = time.time() - (self.ttl + 100)
        self.mod.dream_acquire_lock(self.kdir, pid=1, now=old, lock_rel=self.rel)
        rec = self.mod._dream_read_lock(self._lock_path())
        self.assertTrue(self.mod.dream_lock_is_stale(rec))               # age-only: stale
        self.assertFalse(self.mod.dream_lock_is_stale(
            rec, pid_alive_never_stale=True))                            # C16: NOT stale
        # so a fresh acquirer with the updater semantics is REFUSED (no reclaim
        # into a live apply).
        self.assertFalse(self.mod.dream_acquire_lock(
            self.kdir, lock_rel=self.rel, pid_alive_never_stale=True))

    # --- B14: REAL multi-process contention --------------------------------
    @unittest.skipUnless(_FLOCK, "fcntl.flock required for cross-process serialization")
    def test_pkgstatelock_contention_exactly_one_holder(self):
        n = 8
        mgr = mp.Manager()
        self.addCleanup(mgr.shutdown)
        barrier = mgr.Barrier(n)
        with mp.Pool(n) as pool:
            results = pool.map(
                _mp_acquire, [(self.kdir, self.rel, barrier)] * n)
        self.assertEqual(sum(1 for r in results if r), 1,
                         "exactly one process must win the lock, got %r" % results)

    @unittest.skipUnless(_FLOCK, "fcntl.flock required for cross-process serialization")
    def test_pkgstatelock_stale_reclaim_race_exactly_one_holder(self):
        # Pre-plant a STALE (dead-pid) lock so EVERY racer takes the reclaim path
        # (read-stale -> steal -> recreate). Without the meta-flock acquire critsec,
        # two reclaimers can BOTH win — a rename/remove steal is racy by PATH not
        # inode, so the loser steals the winner's freshly-recreated lock and both
        # believe they hold the single-writer lock (audit B11 follow-up). This must
        # yield exactly ONE holder.
        self.assertTrue(self.mod.dream_acquire_lock(
            self.kdir, pid=424242, lock_rel=self.rel))     # dead pid -> stale
        n = 8
        mgr = mp.Manager()
        self.addCleanup(mgr.shutdown)
        barrier = mgr.Barrier(n)
        with mp.Pool(n) as pool:
            results = pool.map(_mp_acquire, [(self.kdir, self.rel, barrier)] * n)
        self.assertEqual(sum(1 for r in results if r), 1,
                         "exactly one reclaimer must win the stale lock, got %r"
                         % results)

    @unittest.skipUnless(_FLOCK, "fcntl.flock required for cross-process serialization")
    def test_pkgstatelock_no_lost_update_under_contention(self):
        n = 12
        mgr = mp.Manager()
        self.addCleanup(mgr.shutdown)
        barrier = mgr.Barrier(n)
        keys = ["k%02d" % i for i in range(n)]
        with mp.Pool(n) as pool:
            pool.map(_mp_state_update,
                     [(self.home, self.kdir, k, barrier) for k in keys])
        final = self.mod._pkg_state_read()
        missing = [k for k in keys if final.get(k) != 1]
        self.assertEqual(missing, [],
                         "sidecar RMW lost update(s) %r under contention; got %r"
                         % (missing, final))


if __name__ == "__main__":
    unittest.main()
