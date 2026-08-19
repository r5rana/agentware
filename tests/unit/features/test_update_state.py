"""HOME-local update-state sidecar tests (feature 260712-scheduler-updater, Task 1).

Covers the machine-local ~/.agentware/update-state.json persistence foundation:
atomic write, tolerance of a missing/corrupt file, the pending_apply crash-window
marker, the bounded quarantine ledger, write-failure SURFACING (fail-closed on a
read-only HOME), and the fail-closed integrity guards (symlink / wrong-owner /
group-world-writable). Hermetic: HOME_CONFIG is patched onto a throwaway tempdir
so the real operator ~/.agentware is NEVER touched (R-LOC-03).

Runner: python3 -m unittest tests.unit.features.test_update_state -v
"""

import os
import shutil
import stat
import tempfile
import unittest

try:
    from tests._fixtures import load_cli
except ImportError:
    from _fixtures import load_cli


class UpdateStateTestCase(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.home = tempfile.mkdtemp(prefix="agentware-updstate-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        # Redirect the module's HOME_CONFIG so _pkg_home_dir() resolves under our
        # throwaway home (the real ~/.agentware is never touched).
        self._saved = self.mod.HOME_CONFIG
        self.addCleanup(self._restore)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")

    def _restore(self):
        self.mod.HOME_CONFIG = self._saved

    def state_path(self):
        return self.mod._pkg_state_path()

    # --- basics -------------------------------------------------------------
    def test_home_dir_derives_from_home_config(self):
        self.assertEqual(self.mod._pkg_home_dir(),
                         os.path.join(self.home, ".agentware"))
        self.assertTrue(self.state_path().endswith(
            os.path.join(".agentware", "update-state.json")))

    def test_missing_file_reads_as_empty(self):
        self.assertFalse(os.path.exists(self.state_path()))
        self.assertEqual(self.mod._pkg_state_read(), {})

    def test_round_trip_write_read(self):
        st = {"last_good_sha": "abc123", "last_good_tag": "v2.0.0",
              "last_applied_version": "2.0.0", "last_check_ts": "2026-07-12T00:00:00Z"}
        self.mod._pkg_state_write(st)
        self.assertEqual(self.mod._pkg_state_read(), st)

    def test_corrupt_json_reads_as_empty(self):
        self.mod._pkg_secure_home()
        with open(self.state_path(), "w", encoding="utf-8") as f:
            f.write("{not valid json ::::")
        self.assertEqual(self.mod._pkg_state_read(), {})

    def test_non_dict_json_reads_as_empty(self):
        self.mod._pkg_secure_home()
        with open(self.state_path(), "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")
        self.assertEqual(self.mod._pkg_state_read(), {})

    def test_update_merges_fields(self):
        self.mod._pkg_state_write({"a": 1, "b": 2})
        self.mod._pkg_state_update(b=3, c=4)
        self.assertEqual(self.mod._pkg_state_read(), {"a": 1, "b": 3, "c": 4})

    def test_pending_apply_marker_round_trips(self):
        marker = {"applying_tag": "v2.0.0", "applying_sha": "deadbeef",
                  "migrated": False}
        self.mod._pkg_state_update(pending_apply=marker)
        self.assertEqual(self.mod._pkg_state_read()["pending_apply"], marker)
        # Clearing it (finalize) removes the crash-window marker.
        self.mod._pkg_state_update(pending_apply=None)
        self.assertIsNone(self.mod._pkg_state_read()["pending_apply"])

    # --- permissions --------------------------------------------------------
    def test_home_dir_created_0700(self):
        self.mod._pkg_state_write({"x": 1})
        mode = stat.S_IMODE(os.lstat(self.mod._pkg_home_dir()).st_mode)
        self.assertEqual(mode, 0o700)

    def test_sidecar_created_0600(self):
        self.mod._pkg_state_write({"x": 1})
        mode = stat.S_IMODE(os.lstat(self.state_path()).st_mode)
        self.assertEqual(mode, 0o600)

    def test_writes_only_under_home_never_kb_or_pkg(self):
        self.mod._pkg_state_write({"x": 1})
        # The only file created is under our throwaway home.
        self.assertTrue(self.state_path().startswith(self.home))
        self.assertTrue(os.path.isfile(self.state_path()))

    # --- fail-closed integrity guards --------------------------------------
    def test_symlinked_sidecar_refused_on_read(self):
        self.mod._pkg_secure_home()
        victim = os.path.join(self.home, "victim.json")
        with open(victim, "w") as f:
            f.write('{"evil": true}')
        os.symlink(victim, self.state_path())
        with self.assertRaises(self.mod._PkgStateError):
            self.mod._pkg_state_read()

    def test_symlinked_sidecar_refused_on_write(self):
        self.mod._pkg_secure_home()
        victim = os.path.join(self.home, "victim.json")
        with open(victim, "w") as f:
            f.write("{}")
        os.symlink(victim, self.state_path())
        with self.assertRaises(self.mod._PkgStateError):
            self.mod._pkg_state_write({"x": 1})

    def test_group_writable_sidecar_refused(self):
        self.mod._pkg_state_write({"x": 1})
        os.chmod(self.state_path(), 0o660)
        with self.assertRaises(self.mod._PkgStateError):
            self.mod._pkg_state_read()

    def test_symlinked_home_dir_refused(self):
        real = os.path.join(self.home, "real-agentware")
        os.makedirs(real, mode=0o700)
        os.symlink(real, os.path.join(self.home, ".agentware"))
        with self.assertRaises(self.mod._PkgStateError):
            self.mod._pkg_state_write({"x": 1})

    # --- write-failure SURFACING (fail-closed on read-only HOME) -----------
    @unittest.skipIf(os.geteuid() == 0 if hasattr(os, "geteuid") else False,
                     "root bypasses directory write perms")
    def test_write_failure_on_readonly_home_surfaces(self):
        # Point ~/.agentware under a READ-ONLY parent so the dir cannot be
        # created and the write cannot be swallowed (directory-write permission is
        # enforced even for the owner, unlike chmod). Fail-closed: MUST raise.
        ro_parent = os.path.join(self.home, "readonly")
        os.makedirs(ro_parent, mode=0o500)
        self.addCleanup(os.chmod, ro_parent, 0o700)
        self.mod.HOME_CONFIG = os.path.join(ro_parent, ".agentware", "config.env")
        with self.assertRaises((OSError, self.mod._PkgStateError)):
            self.mod._pkg_state_write({"y": 2})

    # --- quarantine ledger --------------------------------------------------
    def test_quarantine_dedup_and_bounded(self):
        self.mod._pkg_quarantine_tag("v2.0.1", "unsigned")
        self.mod._pkg_quarantine_tag("v2.0.1", "again")   # dedup: still one entry
        q = self.mod._pkg_state_read()["quarantined_tags"]
        self.assertEqual(len([e for e in q if e["tag"] == "v2.0.1"]), 1)
        self.assertTrue(self.mod._pkg_is_quarantined("v2.0.1"))
        self.assertFalse(self.mod._pkg_is_quarantined("v9.9.9"))
        # Cap: push well past MAX and confirm the ledger stays bounded.
        for i in range(self.mod.MAX_QUARANTINED_TAGS + 20):
            self.mod._pkg_quarantine_tag("v3.0.%d" % i, "smoke-fail")
        q = self.mod._pkg_state_read()["quarantined_tags"]
        self.assertLessEqual(len(q), self.mod.MAX_QUARANTINED_TAGS)


if __name__ == "__main__":
    unittest.main()
