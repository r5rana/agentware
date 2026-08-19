"""Scoped auto-rollback tests (feature 260712, Task 5).

Covers the authorized R-GIT-02 rollback exception: backward-only hard reset to the
PERSISTED last_good_sha, clean-tree guard, re-smoke confirmation, failed-tag
quarantine, the brick-ordering guard (forbidden post-migrate), stale-index.lock
recovery, and the un-resettable -> needs_manual_recovery escalation (last_good
preserved, never a silent skip). `_pkg_smoke` is mocked so the rollback control
flow is isolated from the smoke internals (the integrated path is proven e2e).
Hermetic: throwaway git repo + patched HOME (R-LOC-03); skips if git is absent.

Runner: python3 -m unittest tests.unit.features.test_pkg_rollback -v
"""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

try:
    from tests._fixtures import load_cli
except ImportError:
    from _fixtures import load_cli

DEVNULL = subprocess.DEVNULL


def _have_git():
    try:
        subprocess.run(["git", "--version"], stdout=DEVNULL, stderr=DEVNULL,
                       check=False)
        return True
    except FileNotFoundError:
        return False


@unittest.skipUnless(_have_git(), "git not available")
class RollbackTestCase(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-rb-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self._saved = self.mod.HOME_CONFIG
        self.addCleanup(setattr, self.mod, "HOME_CONFIG", self._saved)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")
        self.repo = os.path.join(self.base, "repo")
        self._init_repo()
        # Default: smoke passes (isolate rollback control flow from smoke internals).
        p = mock.patch.object(self.mod, "_pkg_smoke",
                              return_value={"ok": True, "steps": [], "detail": "PASS"})
        self.smoke = p.start()
        self.addCleanup(p.stop)

    def _g(self, *a):
        return subprocess.run(["git", "-C", self.repo] + list(a), check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _init_repo(self):
        os.makedirs(self.repo)
        self._g("init", "-q", "-b", "main")
        self._g("config", "user.email", "t@e")
        self._g("config", "user.name", "T")
        with open(os.path.join(self.repo, "f"), "w") as f:
            f.write("v1")
        self._g("add", "-A")
        self._g("commit", "-qm", "good")
        self.sha_good = self._g("rev-parse", "HEAD").stdout.strip()
        with open(os.path.join(self.repo, "f"), "w") as f:
            f.write("v2-BAD")
        self._g("add", "-A")
        self._g("commit", "-qm", "bad")
        self.sha_bad = self._g("rev-parse", "HEAD").stdout.strip()

    def _head(self):
        return self._g("rev-parse", "HEAD").stdout.strip()

    def _file(self):
        with open(os.path.join(self.repo, "f")) as f:
            return f.read()

    # --- happy path ---------------------------------------------------------
    def test_rollback_restores_last_good_and_quarantines(self):
        self.mod._pkg_state_write({"last_good_sha": self.sha_good})
        r = self.mod._pkg_rollback("v2.0.1", target=self.repo, kdir=self.base)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["restored"])
        self.assertEqual(self._head(), self.sha_good)     # backward reset happened
        self.assertEqual(self._file(), "v1")              # bad code discarded
        self.assertTrue(r["resmoke_ok"])
        self.smoke.assert_called()                        # re-smoke ran
        self.assertTrue(self.mod._pkg_is_quarantined("v2.0.1"))

    def test_rollback_clears_pending_apply(self):
        self.mod._pkg_state_write({"last_good_sha": self.sha_good,
                                   "pending_apply": {"applying_tag": "v2.0.1",
                                                     "migrated": False}})
        self.mod._pkg_rollback("v2.0.1", target=self.repo, kdir=self.base)
        self.assertIsNone(self.mod._pkg_state_read().get("pending_apply"))

    # --- escalation paths ---------------------------------------------------
    def test_no_last_good_escalates(self):
        self.mod._pkg_state_write({})
        r = self.mod._pkg_rollback("v2.0.1", target=self.repo, kdir=self.base)
        self.assertTrue(r["needs_manual_recovery"])
        self.assertFalse(r["restored"])
        self.assertEqual(self._head(), self.sha_bad)      # HEAD unchanged
        self.assertTrue(self.mod._pkg_state_read()["needs_manual_recovery"])

    def test_post_migrate_rollback_forbidden(self):
        self.mod._pkg_state_write({"last_good_sha": self.sha_good,
                                   "pending_apply": {"migrated": True}})
        r = self.mod._pkg_rollback("v2.0.1", target=self.repo, kdir=self.base)
        self.assertTrue(r["needs_manual_recovery"])
        self.assertIn("brick-ordering", r["detail"])
        self.assertEqual(self._head(), self.sha_bad)      # never reset post-migrate

    def test_unresettable_sha_escalates_preserving_last_good(self):
        self.mod._pkg_state_write({"last_good_sha": "deadbeef" * 5})  # nonexistent
        r = self.mod._pkg_rollback("v2.0.1", target=self.repo, kdir=self.base)
        self.assertTrue(r["needs_manual_recovery"])
        # last_good PRESERVED for a later manual retry (not wiped).
        self.assertEqual(self.mod._pkg_state_read()["last_good_sha"], "deadbeef" * 5)

    def test_dirty_tree_refused(self):
        self.mod._pkg_state_write({"last_good_sha": self.sha_good})
        with open(os.path.join(self.repo, "f"), "w") as f:
            f.write("operator local edit")     # uncommitted tracked change
        r = self.mod._pkg_rollback("v2.0.1", target=self.repo, kdir=self.base)
        self.assertTrue(r["needs_manual_recovery"])
        self.assertEqual(self._file(), "operator local edit")   # not eaten

    def test_resmoke_failure_escalates(self):
        self.mod._pkg_state_write({"last_good_sha": self.sha_good})
        self.smoke.return_value = {"ok": False, "steps": [], "detail": "FAIL"}
        r = self.mod._pkg_rollback("v2.0.1", target=self.repo, kdir=self.base)
        self.assertTrue(r["restored"])            # reset happened
        self.assertFalse(r["ok"])
        self.assertTrue(r["needs_manual_recovery"])  # known-good still fails smoke

    # --- stale index.lock recovery -----------------------------------------
    def test_stale_index_lock_cleared_then_rollback(self):
        self.mod._pkg_state_write({"last_good_sha": self.sha_good})
        lock = os.path.join(self.repo, ".git", "index.lock")
        with open(lock, "w") as f:
            f.write("")
        old = time.time() - 10_000
        os.utime(lock, (old, old))               # stale mtime
        r = self.mod._pkg_rollback("v2.0.1", target=self.repo, kdir=self.base)
        self.assertFalse(os.path.exists(lock))   # cleared
        self.assertTrue(r["ok"])
        self.assertEqual(self._head(), self.sha_good)

    def test_fresh_index_lock_not_cleared(self):
        lock = os.path.join(self.repo, ".git", "index.lock")
        with open(lock, "w") as f:
            f.write("")
        # fresh mtime (now) -> not stale -> not removed
        cleared = self.mod._pkg_clear_stale_index_lock(self.repo, max_age=600)
        self.assertFalse(cleared)
        self.assertTrue(os.path.exists(lock))


if __name__ == "__main__":
    unittest.main()
