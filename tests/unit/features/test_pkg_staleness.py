"""Layer-2 on-use package-staleness pre-hook tests (feature 260712, Task 10).

Covers the non-blocking staleness nudge: silent when fresh (<24h), read-only tag
fetch + WARN when stale/never-fetched, exit 0 on EVERY path (fresh/stale/offline/
non-repo/tampered-sidecar), the updater-OFF enable nudge, and that it NEVER applies
code. `_git_timed`/`git_is_work_tree` are mocked so no real remote is needed; a CLI
exit-0 check runs the real dispatch. The REAL agentware.sh subprocess path (exit 0
on an offline >24h fixture) is proven e2e in Task 16.

Runner: python3 -m unittest tests.unit.features.test_pkg_staleness -v
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

try:
    from tests._fixtures import load_cli, run_cli
except ImportError:
    from _fixtures import load_cli, run_cli


def _cp(rc):
    return subprocess.CompletedProcess(["git"], rc, "", "")


class StalenessTestCase(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-stale-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self._saved = self.mod.HOME_CONFIG
        self.addCleanup(setattr, self.mod, "HOME_CONFIG", self._saved)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")
        # Default: target IS a work tree (mocked); AGENTWARE_UPDATE unset (OFF).
        self._env = os.environ.get("AGENTWARE_UPDATE")
        os.environ.pop("AGENTWARE_UPDATE", None)
        self.addCleanup(self._restore_env)
        self.wt = mock.patch.object(self.mod, "git_is_work_tree", return_value=True).start()
        self.addCleanup(mock.patch.stopall)

    def _restore_env(self):
        if self._env is None:
            os.environ.pop("AGENTWARE_UPDATE", None)
        else:
            os.environ["AGENTWARE_UPDATE"] = self._env

    def _iso_ago(self, seconds):
        import datetime as dt
        t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds)
        return t.strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- fresh -> silent no-op ---------------------------------------------
    def test_fresh_is_silent_no_fetch(self):
        self.mod._pkg_state_write({"last_fetch_ts": self._iso_ago(3600)})  # 1h ago
        with mock.patch.object(self.mod, "_git_timed") as gt:
            rc = self.mod._pkg_staleness_prehook(target="/x")
        self.assertEqual(rc, 0)
        gt.assert_not_called()

    # --- stale -> read-only fetch + warn -----------------------------------
    def test_stale_fetches_and_warns(self):
        self.mod._pkg_state_write({"last_fetch_ts": self._iso_ago(30 * 3600)})  # 30h
        with mock.patch.object(self.mod, "_git_timed", return_value=_cp(0)) as gt:
            rc = self.mod._pkg_staleness_prehook(target="/x")
        self.assertEqual(rc, 0)
        gt.assert_called_once()
        # read-only tag fetch — never a merge/apply
        args = gt.call_args[0]
        self.assertIn("fetch", args)
        self.assertIn("--tags", args)
        self.assertNotIn("merge", args)
        self.assertIn("last_fetch_ts", self.mod._pkg_state_read())

    def test_missing_last_fetch_is_stale(self):
        self.mod._pkg_state_write({})
        with mock.patch.object(self.mod, "_git_timed", return_value=_cp(0)) as gt:
            rc = self.mod._pkg_staleness_prehook(target="/x")
        self.assertEqual(rc, 0)
        gt.assert_called_once()

    # --- offline / non-repo / tampered -> still exit 0 ----------------------
    def test_offline_fetch_still_exit_0(self):
        self.mod._pkg_state_write({})
        with mock.patch.object(self.mod, "_git_timed", return_value=_cp(1)):
            rc = self.mod._pkg_staleness_prehook(target="/x")
        self.assertEqual(rc, 0)

    def test_non_work_tree_exit_0(self):
        self.wt.return_value = False
        with mock.patch.object(self.mod, "_git_timed") as gt:
            rc = self.mod._pkg_staleness_prehook(target="/x")
        self.assertEqual(rc, 0)
        gt.assert_not_called()

    def test_tampered_sidecar_exit_0(self):
        # A symlinked sidecar makes _pkg_state_read raise -> prehook still exits 0.
        self.mod._pkg_secure_home()
        victim = os.path.join(self.home, "v")
        with open(victim, "w") as f:
            f.write("{}")
        os.symlink(victim, self.mod._pkg_state_path())
        rc = self.mod._pkg_staleness_prehook(target="/x")
        self.assertEqual(rc, 0)

    # --- updater-OFF nudge --------------------------------------------------
    def test_updater_off_nudge_present(self):
        self.mod._pkg_state_write({})
        import io
        buf = io.StringIO()
        with mock.patch.object(self.mod, "_git_timed", return_value=_cp(0)), \
                mock.patch("sys.stdout", buf):
            self.mod._pkg_staleness_prehook(target="/x")
        self.assertIn("Automatic updates are OFF", buf.getvalue())

    def test_updater_on_no_nudge(self):
        os.environ["AGENTWARE_UPDATE"] = "on"
        self.mod._pkg_state_write({})
        import io
        buf = io.StringIO()
        with mock.patch.object(self.mod, "_git_timed", return_value=_cp(0)), \
                mock.patch("sys.stdout", buf):
            self.mod._pkg_staleness_prehook(target="/x")
        self.assertNotIn("Automatic updates are OFF", buf.getvalue())

    # --- CLI dispatch exits 0 ----------------------------------------------
    def test_cli_staleness_check_exit_0(self):
        kdir = os.path.join(self.base, "kb")
        os.makedirs(kdir)
        with mock.patch.object(self.mod, "_git_timed", return_value=_cp(0)):
            code, _out, _e = run_cli(["update", "--staleness-check"], kdir)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
