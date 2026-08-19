"""Dedicated updater apply-path tests (feature 260712, Task 6).

Drives the strict, non-reorderable sequence end-to-end with REAL git + real SSH
signing + the real resolver/rollback, mocking only `_pkg_smoke` and
`_pkg_migrate_after_apply` to steer their outcomes: record-last_good-before-advance,
pending_apply marker lifecycle, ff-only apply to the verified concrete SHA,
HEAD==SHA, smoke-then-rollback, migrate triage (ok / restructuring-workorder /
older-CLI-error), the pre-advance-write hard fail-closed precondition, dirty-tree
skip, and single-writer lock serialization. Hermetic: throwaway signed repo +
patched HOME (R-LOC-03); skips when git SSH signing is unavailable.

Runner: python3 -m unittest tests.unit.features.test_pkg_updater -v
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

try:
    from tests._fixtures import load_cli
except ImportError:
    from _fixtures import load_cli

DEVNULL = subprocess.DEVNULL


def _have_signing():
    if not os.path.exists("/usr/bin/ssh-keygen"):
        return False
    base = tempfile.mkdtemp()
    try:
        key = os.path.join(base, "k")
        if subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key, "-q"],
                          check=False).returncode != 0:
            return False
        repo = os.path.join(base, "r")
        os.makedirs(repo)
        for a in (["init", "-q", "-b", "main"], ["config", "user.email", "p@t"],
                  ["config", "user.name", "P"], ["config", "gpg.format", "ssh"],
                  ["config", "user.signingkey", key + ".pub"]):
            subprocess.run(["git", "-C", repo] + a, check=False, stdout=DEVNULL, stderr=DEVNULL)
        with open(os.path.join(repo, "f"), "w") as f:
            f.write("x")
        subprocess.run(["git", "-C", repo, "add", "-A"], check=False, stdout=DEVNULL, stderr=DEVNULL)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "s"], check=False, stdout=DEVNULL, stderr=DEVNULL)
        return subprocess.run(["git", "-C", repo, "tag", "-s", "-m", "t", "v0.0.1"],
                              check=False, stdout=DEVNULL, stderr=DEVNULL).returncode == 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


HAVE_SIGNING = _have_signing()


@unittest.skipUnless(HAVE_SIGNING, "git SSH tag signing unavailable")
class UpdaterApplyTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-upd-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.kdir = os.path.join(self.base, "kb")
        os.makedirs(self.kdir)
        self._saved = self.mod.HOME_CONFIG
        self.addCleanup(setattr, self.mod, "HOME_CONFIG", self._saved)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")
        self._build_repo()
        # Default: smoke passes + migrate ok (isolate the sequence control flow).
        self._smoke_p = mock.patch.object(
            self.mod, "_pkg_smoke",
            return_value={"ok": True, "steps": [], "detail": "PASS"})
        self.smoke = self._smoke_p.start()
        self._migrate_p = mock.patch.object(
            self.mod, "_pkg_migrate_after_apply", return_value=("ok", {}))
        self.migrate = self._migrate_p.start()
        self.addCleanup(mock.patch.stopall)

    def _g(self, *a):
        return subprocess.run(["git", "-C", self.repo] + list(a), check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _build_repo(self):
        bare = os.path.join(self.base, "remote.git")
        subprocess.run(["git", "init", "--bare", "-q", bare], check=True)
        self.repo = os.path.join(self.base, "repo")
        subprocess.run(["git", "clone", "-q", bare, self.repo], check=True,
                       stdout=DEVNULL, stderr=DEVNULL)
        self.key = os.path.join(self.base, "signer")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", self.key,
                        "-C", "release@agentware.test", "-q"], check=True)
        with open(self.key + ".pub") as f:
            keydata = f.read().split()[1]
        for a in (("config", "user.email", "release@agentware.test"),
                  ("config", "user.name", "Agentware Release"),
                  ("config", "gpg.format", "ssh"),
                  ("config", "user.signingkey", self.key + ".pub")):
            self._g(*a)
        with open(os.path.join(self.repo, "f"), "w") as f:
            f.write("v1")
        self._g("add", "-A")
        self._g("commit", "-qm", "c0")
        self.sha0 = self._g("rev-parse", "HEAD").stdout.strip()
        self._g("push", "-q", "origin", "main")
        # A NEWER commit + signed tag AHEAD of HEAD, then rewind HEAD to c0 so the
        # tag is a fast-forward target.
        with open(os.path.join(self.repo, "f"), "w") as f:
            f.write("v2")
        self._g("add", "-A")
        self._g("commit", "-qm", "c1")
        self._g("tag", "-s", "-m", "release v2.0.0", "v2.0.0")
        # The verified-tag COMMIT SHA (annotated tag deref) — what ff-apply lands on.
        self.sha1 = self._g("rev-list", "-n", "1", "v2.0.0").stdout.strip()
        self._g("reset", "--hard", self.sha0)
        self.mod._pkg_pin_publisher_key("release@agentware.test", "ssh-ed25519", keydata)

    def _head(self):
        return self._g("rev-parse", "HEAD").stdout.strip()

    # --- happy path ---------------------------------------------------------
    def test_apply_full_sequence(self):
        r = self.mod._pkg_run_update(self.repo, self.kdir)
        self.assertEqual(r["status"], "applied", r)
        self.assertEqual(r["tag"], "v2.0.0")
        self.assertEqual(r["version"], "2.0.0")
        self.assertEqual(self._head(), self.sha1)          # ff-advanced to verified SHA
        st = self.mod._pkg_state_read()
        self.assertEqual(st["last_good_sha"], self.sha1)   # finalized last_good = new
        self.assertEqual(st["last_good_tag"], "v2.0.0")
        self.assertEqual(st["last_applied_version"], "2.0.0")
        self.assertIsNone(st.get("pending_apply"))         # marker cleared LAST
        self.migrate.assert_called_once()                  # migrate ran after smoke

    def test_last_good_recorded_before_advance(self):
        # Capture the pending_apply/last_good written BEFORE the ff-advance by
        # asserting smoke sees HEAD already at the new SHA but last_good == sha0.
        seen = {}

        def smoke_spy(kdir, timeout=None, target=None):
            seen["head"] = self._head()
            seen["last_good"] = self.mod._pkg_state_read().get("last_good_sha")
            seen["pending"] = self.mod._pkg_state_read().get("pending_apply")
            return {"ok": True, "steps": [], "detail": "PASS"}

        self.smoke.side_effect = smoke_spy
        self.mod._pkg_run_update(self.repo, self.kdir)
        self.assertEqual(seen["head"], self.sha1)          # advanced before smoke
        self.assertEqual(seen["last_good"], self.sha0)     # rollback target = pre-advance
        self.assertEqual(seen["pending"]["applying_sha"], self.sha1)
        self.assertFalse(seen["pending"]["migrated"])      # smoke precedes migrate

    # --- smoke fail -> rollback --------------------------------------------
    def test_smoke_fail_rolls_back_and_quarantines(self):
        self.smoke.return_value = {"ok": False, "steps": [], "detail": "FAIL: syntax"}
        r = self.mod._pkg_run_update(self.repo, self.kdir)
        self.assertEqual(r["status"], "rolled-back")
        self.assertEqual(self._head(), self.sha0)          # restored
        self.assertTrue(self.mod._pkg_is_quarantined("v2.0.0"))
        self.migrate.assert_not_called()                   # migrate never ran

    # --- migrate triage -----------------------------------------------------
    def test_migrate_restructuring_workorder_keeps_package(self):
        self.migrate.return_value = ("refused",
                                     {"refused": {"version": 2, "name": "big-restructure"}})
        r = self.mod._pkg_run_update(self.repo, self.kdir)
        self.assertEqual(r["status"], "migrate-workorder")
        self.assertEqual(self._head(), self.sha1)          # package KEPT (not rolled back)
        st = self.mod._pkg_state_read()
        self.assertEqual(st["last_applied_version"], "2.0.0")
        self.assertIsNone(st.get("pending_apply"))

    def test_migrate_older_cli_error_surfaced_keeps_package(self):
        self.migrate.return_value = ("error", {"detail": "older CLI on newer KB"})
        r = self.mod._pkg_run_update(self.repo, self.kdir)
        self.assertEqual(r["status"], "migrate-error")
        self.assertEqual(self._head(), self.sha1)          # kept + surfaced (not rolled back)

    # --- fail-closed pre-advance write -------------------------------------
    def test_pre_advance_write_failure_blocks_apply(self):
        real = self.mod._pkg_state_write

        def failing(state):
            pa = state.get("pending_apply")
            if isinstance(pa, dict) and pa.get("applying_tag"):
                raise OSError("disk full")
            return real(state)

        with mock.patch.object(self.mod, "_pkg_state_write", side_effect=failing):
            r = self.mod._pkg_run_update(self.repo, self.kdir)
        self.assertEqual(r["status"], "error")
        self.assertIn("fail-closed", r["detail"])
        self.assertEqual(self._head(), self.sha0)          # NEVER advanced (no rollback target)

    # --- graceful skips -----------------------------------------------------
    def test_dirty_tree_skips(self):
        with open(os.path.join(self.repo, "f"), "w") as f:
            f.write("uncommitted operator edit")
        r = self.mod._pkg_run_update(self.repo, self.kdir)
        self.assertEqual(r["status"], "dirty")
        self.assertEqual(self._head(), self.sha0)

    def test_lock_held_skips(self):
        self.assertTrue(self.mod.dream_acquire_lock(
            self.kdir, pid=1, lock_rel=self.mod.UPDATER_LOCK_REL))   # foreign live lock
        r = self.mod._pkg_run_update(self.repo, self.kdir)
        self.assertEqual(r["status"], "locked")
        self.assertEqual(self._head(), self.sha0)

    def test_no_trust_when_unpinned(self):
        os.remove(self.mod._pkg_allowed_signers_path())
        r = self.mod._pkg_run_update(self.repo, self.kdir)
        self.assertEqual(r["status"], "no-trust")
        self.assertEqual(self._head(), self.sha0)

    def test_migrate_after_apply_parses_refused_despite_trailing_line(self):
        # `migrate --apply --format json` prints the JSON THEN a human summary line;
        # the leading-JSON parse must still triage a restructuring-refusal as
        # 'refused' (regression: a plain json.loads failed -> mis-triaged 'error').
        self._migrate_p.stop()   # use the REAL _pkg_migrate_after_apply
        stdout = ('{\n  "ok": false,\n  "applied": [],\n  "schema": 1,\n'
                  '  "pending": 1,\n  "refused": {"version": 2, "name": "big-restructure",'
                  ' "tier": "restructuring"}\n}\n'
                  'migrate: refusing to auto-apply RESTRUCTURING migration 2 (big-restructure)\n')
        fake = subprocess.CompletedProcess(["cli"], 1, stdout, "")
        with mock.patch("subprocess.run", return_value=fake):
            status, payload = self.mod._pkg_migrate_after_apply(self.repo, self.kdir)
        self.assertEqual(status, "refused")
        self.assertEqual(payload["refused"]["name"], "big-restructure")

    def test_lock_acquire_no_double_grant(self):
        # Atomic acquire: a fresh lock held by a LIVE foreign owner is never
        # double-granted (regression: read-then-write let both acquirers win).
        rel = self.mod.UPDATER_LOCK_REL
        self.assertTrue(self.mod.dream_acquire_lock(self.kdir, pid=1, lock_rel=rel))
        self.assertFalse(self.mod.dream_acquire_lock(
            self.kdir, pid=os.getpid(), lock_rel=rel))
        # A stale (dead-pid) lock IS reclaimable.
        self.mod.dream_release_lock(self.kdir, pid=1, lock_rel=rel)
        self.assertTrue(self.mod.dream_acquire_lock(self.kdir, pid=424242, lock_rel=rel))
        self.assertTrue(self.mod.dream_acquire_lock(self.kdir, pid=1, lock_rel=rel))


if __name__ == "__main__":
    unittest.main()
