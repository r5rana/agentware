"""Tests for dream mode (feature 260627-dream-mode).

Phase 1 = deterministic, idle-gated, unattended KB maintenance. These tests use
SYNTHETIC fixtures + a patched HOME and NEVER touch the operator's real KB or
config (R-LOC-03). Deterministic + stdlib-only: wall-clock and pid are injected
(now=/pid=) so the idle-gate and lock logic are exercised without real processes
or sleeping.

Task 3 covers the idle-gate + concurrency-lock primitives. Later tasks extend
this module (the full cycle, --dry-run, journal, the destructive-op guard).
"""

import os
import subprocess
import tempfile
import unittest

from tests._fixtures import load_cli, build_synthetic_kb


def _touch(path, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8"):
        pass
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class DreamSessionGateTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-")
        self.addCleanup(__import__("shutil").rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.now = 1_000_000.0  # fixed synthetic clock

    def _loop_dir(self, feature):
        return os.path.join(self.kdir, "work", feature, ".loop")

    def test_no_work_dir_is_idle(self):
        self.assertIsNone(self.mod.is_session_active(self.kdir, now=self.now))

    def test_fresh_live_stream_is_active(self):
        d = self._loop_dir("260627-other")
        _touch(os.path.join(d, "live-stream.log"), mtime=self.now - 5)
        self.assertEqual(
            self.mod.is_session_active(self.kdir, now=self.now), "260627-other")

    def test_done_feature_is_not_active(self):
        d = self._loop_dir("260627-finished")
        _touch(os.path.join(d, "live-stream.log"), mtime=self.now - 5)
        _touch(os.path.join(d, ".done"))
        self.assertIsNone(self.mod.is_session_active(self.kdir, now=self.now))

    def test_stale_live_stream_is_not_active(self):
        d = self._loop_dir("260627-idle")
        old = self.now - self.mod.DREAM_SESSION_TTL_SEC - 60
        _touch(os.path.join(d, "live-stream.log"), mtime=old)
        self.assertIsNone(self.mod.is_session_active(self.kdir, now=self.now))

    def test_iteration_marker_also_counts(self):
        d = self._loop_dir("260627-iter")
        _touch(os.path.join(d, ".iteration"), mtime=self.now - 1)
        self.assertEqual(
            self.mod.is_session_active(self.kdir, now=self.now), "260627-iter")

    def test_gate_reason_skips_on_active_session(self):
        d = self._loop_dir("260627-busy")
        _touch(os.path.join(d, "live-stream.log"), mtime=self.now - 1)
        reason = self.mod.dream_gate_reason(self.kdir, now=self.now)
        self.assertIsNotNone(reason)
        self.assertIn("loop session is active", reason)

    def test_force_bypasses_idle_gate(self):
        d = self._loop_dir("260627-busy")
        _touch(os.path.join(d, "live-stream.log"), mtime=self.now - 1)
        # force ignores idle checks (manual operator run) but not the lock.
        self.assertIsNone(
            self.mod.dream_gate_reason(self.kdir, force=True, now=self.now))

    def test_gate_reason_skips_on_high_load(self):
        # Inject a high-load tuple so the test never depends on real machine load.
        reason = self.mod.dream_gate_reason(
            self.kdir, now=self.now, load=(True, 99.0, 8.0))
        self.assertIsNotNone(reason)
        self.assertIn("load is high", reason)

    def test_gate_reason_none_when_idle_and_low_load(self):
        reason = self.mod.dream_gate_reason(
            self.kdir, now=self.now, load=(False, 0.1, 8.0))
        self.assertIsNone(reason)


class DreamLockTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-")
        self.addCleanup(__import__("shutil").rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.now = 1_000_000.0

    def test_acquire_then_foreign_live_lock_fails(self):
        # PID 1 (init) is always alive and is not us => a fresh foreign lock blocks.
        self.assertTrue(
            self.mod.dream_acquire_lock(self.kdir, now=self.now, pid=1))
        self.assertFalse(
            self.mod.dream_acquire_lock(self.kdir, now=self.now, pid=424242))

    def test_reclaims_dead_pid_lock(self):
        # A very high pid is (essentially certainly) dead => reclaimable.
        dead = 2_000_000_000
        self.assertTrue(
            self.mod.dream_acquire_lock(self.kdir, now=self.now, pid=dead))
        # Even though a lock file exists, a different live pid reclaims it because
        # the recorded owner is dead.
        self.assertTrue(
            self.mod.dream_acquire_lock(self.kdir, now=self.now, pid=1))

    def test_reclaims_expired_mtime_lock(self):
        self.assertTrue(self.mod.dream_acquire_lock(self.kdir, now=self.now, pid=1))
        # Same live owner pid, but the timestamp is far in the past => stale => reclaim.
        later = self.now + self.mod.DREAM_LOCK_TTL_SEC + 60
        self.assertTrue(self.mod.dream_acquire_lock(self.kdir, now=later, pid=1))

    def test_release_only_when_owned(self):
        self.mod.dream_acquire_lock(self.kdir, now=self.now, pid=4242)
        # A foreign pid must NOT remove our lock.
        self.mod.dream_release_lock(self.kdir, pid=9999)
        self.assertTrue(os.path.isfile(self.mod._dream_lock_path(self.kdir)))
        # The owner releases it.
        self.mod.dream_release_lock(self.kdir, pid=4242)
        self.assertFalse(os.path.isfile(self.mod._dream_lock_path(self.kdir)))

    def test_lock_path_is_under_gitignored_cache(self):
        path = self.mod._dream_lock_path(self.kdir)
        self.assertIn(os.path.join(".cache", "dream.lock"), path)

    def test_lockfile_is_git_ignored_in_a_tracked_kb(self):
        # Scaffold the KB .gitignore exactly as `init` would, then confirm git
        # check-ignore matches the lock path (so step f never stages it).
        self.mod._ensure_kb_gitignore(self.kdir)
        subprocess.run(["git", "init", "-q"], cwd=self.kdir, check=True)
        self.mod.dream_acquire_lock(self.kdir, now=self.now, pid=1)
        rel = os.path.relpath(self.mod._dream_lock_path(self.kdir), self.kdir)
        r = subprocess.run(["git", "check-ignore", rel], cwd=self.kdir,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, "lock path should be gitignored: %r" % rel)


if __name__ == "__main__":
    unittest.main()
