"""Shared tick daemon tests (feature 260712, Task 7).

Covers the StartInterval installer (launchd StartInterval=1800 / cron */30, DISTINCT
label + log from dream), the no-job-enabled install refusal, the crontab
cross-preservation filter (installing the tick never strips the dream line and vice
versa), and the job-agnostic dispatcher (enabled/not-due/ran/error isolation).
Hermetic: patched HOME + throwaway KB, AGENTWARE_NESTED_UNITTEST guards activation
(no launchctl/crontab side effects). R-LOC-03.

Runner: python3 -m unittest tests.unit.ops.test_tick_schedule -v
"""

import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests._fixtures import load_cli
except ImportError:
    from _fixtures import load_cli


def _job(key, enabled=True, due=True, run_result=None, raise_on=None):
    def run(kdir, now):
        if raise_on == "run":
            raise RuntimeError("boom")
        return run_result or {"detail": "%s ran" % key}

    def due_fn(kdir, now):
        if raise_on == "due":
            raise RuntimeError("due boom")
        return due

    return {"key": key, "enabled": lambda: enabled, "due": due_fn, "run": run}


class TickScheduleTestCase(unittest.TestCase):
    _KEYS = ("HOME", "AGENTWARE_KNOWLEDGE_DIR", "AGENTWARE_NESTED_UNITTEST")

    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-tick-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        self.kdir = os.path.join(self.base, "kb")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.kdir, "logs"))
        self._env = {k: os.environ.get(k) for k in self._KEYS}
        self.addCleanup(self._restore)
        os.environ["HOME"] = self.home
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"   # skip launchctl/crontab

    def _restore(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # --- install guard ------------------------------------------------------
    def test_install_refused_when_no_job_enabled(self):
        rc = self.mod._tick_install_schedule(self.kdir, as_json=False,
                                             jobs=[_job("x", enabled=False)])
        self.assertEqual(rc, 2)
        if sys.platform == "darwin":
            self.assertFalse(os.path.exists(self.mod._tick_launchd_plist_path()))

    def test_install_with_enabled_job(self):
        rc = self.mod._tick_install_schedule(self.kdir, as_json=False,
                                             jobs=[_job("updater", enabled=True)])
        self.assertEqual(rc, 0)
        if sys.platform == "darwin":
            path = self.mod._tick_launchd_plist_path()
            self.assertTrue(os.path.exists(path))
            with open(path) as f:
                content = f.read()
            # StartInterval, NOT StartCalendarInterval; distinct label; own log.
            self.assertIn("<key>StartInterval</key>", content)
            self.assertIn("<integer>1800</integer>", content)
            self.assertNotIn("StartCalendarInterval", content)
            self.assertIn("com.agentware.tick", content)
            self.assertNotEqual(self.mod.TICK_LAUNCHD_LABEL,
                                self.mod.DREAM_LAUNCHD_LABEL)
            self.assertIn("tick-scheduler.log", content)

    def test_uninstall_removes_schedule(self):
        self.mod._tick_install_schedule(self.kdir, as_json=False,
                                        jobs=[_job("updater")])
        rc = self.mod._tick_uninstall_schedule(as_json=False)
        self.assertEqual(rc, 0)
        if sys.platform == "darwin":
            self.assertFalse(os.path.exists(self.mod._tick_launchd_plist_path()))

    def test_scheduler_log_distinct_from_dream(self):
        self.assertNotEqual(self.mod._tick_scheduler_log_path(),
                            self.mod._dream_scheduler_log_path())

    # --- crontab cross-preservation ----------------------------------------
    def test_crontab_filter_preserves_other_marker(self):
        dream_line = "0 3 * * * nice -n 10 /x/scripts/agentware dream >> /l 2>&1\n"
        tick_line = "*/30 * * * * nice -n 10 /x/scripts/agentware tick >> /l 2>&1\n"
        other = "0 0 * * * /usr/bin/backup\n"
        crontab = dream_line + tick_line + other
        # Installing the tick strips ONLY the tick line; dream + other survive.
        after_tick = self.mod._crontab_filter(crontab, ("agentware", "tick"))
        self.assertIn(dream_line, after_tick)
        self.assertNotIn(tick_line, after_tick)
        self.assertIn(other, after_tick)
        # Installing dream strips ONLY the dream line; tick + other survive.
        after_dream = self.mod._crontab_filter(crontab, ("agentware", "dream"))
        self.assertIn(tick_line, after_dream)
        self.assertNotIn(dream_line, after_dream)

    # --- dispatcher ---------------------------------------------------------
    def test_dispatch_runs_enabled_due_job(self):
        res = self.mod._tick_dispatch(self.kdir, now=1000, jobs=[_job("updater")])
        self.assertEqual(res[0]["status"], "ran")
        self.assertEqual(res[0]["result"]["detail"], "updater ran")

    def test_dispatch_skips_disabled_and_not_due(self):
        res = self.mod._tick_dispatch(self.kdir, now=1000, jobs=[
            _job("a", enabled=False), _job("b", due=False)])
        by = {r["job"]: r["status"] for r in res}
        self.assertEqual(by["a"], "disabled")
        self.assertEqual(by["b"], "not-due")

    def test_dispatch_isolates_raising_job(self):
        res = self.mod._tick_dispatch(self.kdir, now=1000, jobs=[
            _job("bad", raise_on="run"), _job("good")])
        by = {r["job"]: r["status"] for r in res}
        self.assertEqual(by["bad"], "error")     # isolated, not fatal
        self.assertEqual(by["good"], "ran")       # the tick continues

    def test_default_registry_is_a_list(self):
        # The real registry is job-agnostic (Task 8 appends the updater job).
        self.assertIsInstance(self.mod._tick_jobs(), list)


if __name__ == "__main__":
    unittest.main()
