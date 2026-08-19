"""Reverse loop-guard tests (feature 260712, Task 9).

Covers the read-only lock-status query that backs the agentware.sh RUN-BEGINS
reverse-guard (free|held|stale on the UPDATER lock specifically, never dream.lock),
the `tick --lock-status` CLI, and the one-shot self-heal (clear a stale lock +
reconcile) including the LIVE-holder refusal. The full bash guard integration
(a second loop refused while the lock is held) is proven e2e in Task 16.

Runner: python3 -m unittest tests.integration.test_loop_guards -v
"""

from tests._paths import REPO_ROOT

import os
import shutil
import subprocess
import tempfile
import time
import unittest

try:
    from tests._fixtures import load_cli, run_cli
except ImportError:
    from _fixtures import load_cli, run_cli

CLI = os.path.join(REPO_ROOT, "scripts", "agentware")


class LockStatusTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-guard-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.kdir = os.path.join(self.base, "kb")
        os.makedirs(self.kdir)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self._saved = self.mod.HOME_CONFIG
        self.addCleanup(setattr, self.mod, "HOME_CONFIG", self._saved)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")
        self.rel = self.mod.UPDATER_LOCK_REL

    def test_free_when_no_lock(self):
        self.assertEqual(self.mod._pkg_lock_status_word(self.kdir), "free")

    def test_held_when_live_lock(self):
        # A fresh lock held by THIS (live) process -> held.
        self.assertTrue(self.mod.dream_acquire_lock(self.kdir, lock_rel=self.rel))
        self.assertEqual(self.mod._pkg_lock_status_word(self.kdir), "held")

    def test_stale_when_dead_pid(self):
        # A lock owned by a dead pid -> stale (reclaimable).
        self.assertTrue(self.mod.dream_acquire_lock(self.kdir, pid=424242,
                                                    lock_rel=self.rel))
        self.assertEqual(self.mod._pkg_lock_status_word(self.kdir), "stale")

    def test_lock_status_checks_updater_lock_not_dream(self):
        # A held DREAM lock must NOT make the updater guard report held (no
        # contention/deadlock between the two locks).
        self.assertTrue(self.mod.dream_acquire_lock(self.kdir))   # dream.lock
        self.assertEqual(self.mod._pkg_lock_status_word(self.kdir), "free")

    def test_cli_lock_status_word(self):
        code, out, _e = run_cli(["tick", "--lock-status"], self.kdir)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "free")
        self.mod.dream_acquire_lock(self.kdir, lock_rel=self.rel)
        code, out, _e = run_cli(["tick", "--lock-status"], self.kdir)
        self.assertEqual(out.strip(), "held")

    def test_cli_lock_status_subprocess_sees_held(self):
        # A separate process (like agentware.sh) must observe a live in-proc lock.
        self.mod.dream_acquire_lock(self.kdir, lock_rel=self.rel)
        env = dict(os.environ, AGENTWARE_KNOWLEDGE_DIR=self.kdir)
        cp = subprocess.run([CLI, "tick", "--lock-status"], env=env,
                            stdout=subprocess.PIPE, text=True, check=False)
        self.assertEqual(cp.stdout.strip(), "held")


class SelfHealTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-heal-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.kdir = os.path.join(self.base, "kb")
        os.makedirs(self.kdir)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self._saved = self.mod.HOME_CONFIG
        self.addCleanup(setattr, self.mod, "HOME_CONFIG", self._saved)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")
        self.rel = self.mod.UPDATER_LOCK_REL

    def test_self_heal_clears_stale_lock_and_reports_clean(self):
        self.mod.dream_acquire_lock(self.kdir, pid=424242, lock_rel=self.rel)  # stale
        self.mod._pkg_state_write({})                       # no crashed apply
        r = self.mod._pkg_self_heal(self.kdir, target=self.base)
        self.assertEqual(r["status"], "clean")
        # stale lock cleared (and the heal released its own lock)
        self.assertEqual(self.mod._pkg_lock_status_word(self.kdir), "free")

    def test_self_heal_refuses_when_live_holder(self):
        self.mod.dream_acquire_lock(self.kdir, lock_rel=self.rel)   # live (this pid)
        r = self.mod._pkg_self_heal(self.kdir, target=self.base)
        self.assertEqual(r["status"], "locked")

    def test_cli_self_heal_runs(self):
        code, out, _e = run_cli(["update", "--self-heal"], self.kdir)
        self.assertEqual(code, 0)
        self.assertIn("self-heal", out)


if __name__ == "__main__":
    unittest.main()
