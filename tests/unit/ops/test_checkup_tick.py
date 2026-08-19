"""Additive opt-in checkup tick job (Task 8, 260713-p5-checkup).

Runner: python3 -m unittest tests.unit.ops.test_checkup_tick -v

checkup is registered THIRD in _TICK_JOB_REGISTRY (purely additive to updater +
dream-cadence). Proven here: the registry dispatches all three jobs; checkup's
`due` is computed off its OWN logs/checkup/.last-run wall-clock (never updater/
dream state); with checkup OFF the job is `disabled` and cmd_checkup is NEVER
invoked; ON + never-run is due; ON + <7d not-due; ON + >=7d due; a scheduler that
slept through ticks self-corrects; `_checkup_run` dispatches cmd_checkup; and
_tick_dispatch isolates a raising checkup job. Hermetic: patched config + a mocked
cmd_checkup so no real exam runs.
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from tests._fixtures import load_cli, build_synthetic_kb
except ImportError:
    from _fixtures import load_cli, build_synthetic_kb

_NOW = 1_000_000_000.0
_WEEK = 7 * 24 * 3600


class CheckupTickTests(unittest.TestCase):
    _KEYS = ("AGENTWARE_CHECKUP", "AGENTWARE_DREAM", "AGENTWARE_UPDATE",
             "AGENTWARE_CHECKUP_LLM")

    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="aw_ck_tick_")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.kdir = os.path.join(self.base, "kb")
        build_synthetic_kb(self.kdir)
        self._saved = (self.mod.HOME_CONFIG, self.mod.CONFIG_PATHS)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")
        self.mod.CONFIG_PATHS = (self.mod.HOME_CONFIG,)
        self._env = {k: os.environ.get(k) for k in self._KEYS}
        for k in self._KEYS:
            os.environ.pop(k, None)
        self.addCleanup(self._restore)

    def _restore(self):
        self.mod.HOME_CONFIG, self.mod.CONFIG_PATHS = self._saved
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _stamp(self, epoch):
        import datetime
        iso = datetime.datetime.fromtimestamp(
            epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        p = os.path.join(self.kdir, self.mod.CHECKUP_LASTRUN_REL)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(iso + "\n")

    def _dispatch(self):
        return {r["job"]: r for r in self.mod._tick_dispatch(self.kdir, now=_NOW)}

    # --- registry composition ------------------------------------------------
    def test_registry_has_checkup_third(self):
        keys = [j["key"] for j in self.mod._tick_jobs()]
        self.assertEqual(keys[:3], ["updater", "dream-cadence", "checkup"],
                         "checkup is registered THIRD (purely additive)")

    def test_dispatch_reports_checkup(self):
        self.assertIn("checkup", self._dispatch())

    # --- disabled when OFF ----------------------------------------------------
    def test_disabled_when_off_never_invokes_cmd_checkup(self):
        os.environ["AGENTWARE_CHECKUP"] = "0"
        with mock.patch.object(self.mod, "cmd_checkup", return_value=0) as cc:
            by = self._dispatch()
        self.assertEqual(by["checkup"]["status"], "disabled")
        cc.assert_not_called()

    # --- due-gate off checkup's OWN last-run ---------------------------------
    def test_never_run_is_due(self):
        os.environ["AGENTWARE_CHECKUP"] = "1"
        self.assertTrue(self.mod._checkup_due(self.kdir, _NOW))

    def test_runs_when_on_and_over_a_week(self):
        os.environ["AGENTWARE_CHECKUP"] = "1"
        self._stamp(_NOW - _WEEK - 3600)
        with mock.patch.object(self.mod, "cmd_checkup", return_value=0) as cc:
            by = self._dispatch()
        self.assertEqual(by["checkup"]["status"], "ran")
        cc.assert_called_once()

    def test_not_due_when_on_and_under_a_week(self):
        os.environ["AGENTWARE_CHECKUP"] = "1"
        self._stamp(_NOW - 2 * 24 * 3600)
        with mock.patch.object(self.mod, "cmd_checkup", return_value=0) as cc:
            by = self._dispatch()
        self.assertEqual(by["checkup"]["status"], "not-due")
        cc.assert_not_called()

    def test_slept_through_ticks_self_corrects(self):
        os.environ["AGENTWARE_CHECKUP"] = "1"
        self._stamp(_NOW - 30 * 24 * 3600)
        self.assertTrue(self.mod._checkup_due(self.kdir, _NOW))

    def test_due_independent_of_dream_and_updater(self):
        os.environ["AGENTWARE_CHECKUP"] = "1"
        self._stamp(_NOW - 2 * 24 * 3600)                 # checkup not due
        # Seed a stale dream cycle + updater check — must NOT move checkup's due.
        metrics = os.path.join(self.kdir, "logs", "metrics.jsonl")
        os.makedirs(os.path.dirname(metrics), exist_ok=True)
        import datetime
        import json
        old = datetime.datetime.fromtimestamp(
            _NOW - 40 * 24 * 3600, datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        with open(metrics, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "dream", "ts": old, "steps": []}) + "\n")
        self.assertFalse(self.mod._checkup_due(self.kdir, _NOW))

    def test_checkup_run_dispatches_cmd_checkup(self):
        os.environ["AGENTWARE_CHECKUP"] = "1"
        captured = {}

        def fake(ns):
            captured["status_line"] = getattr(ns, "status_line", None)
            captured["format"] = getattr(ns, "format", None)
            return 0

        with mock.patch.object(self.mod, "cmd_checkup", side_effect=fake):
            r = self.mod._checkup_run(self.kdir, _NOW)
        self.assertEqual(r["status"], "ok")
        self.assertIs(captured["status_line"], False)

    def test_raising_checkup_job_is_isolated(self):
        # never-run -> due; a raising cmd_checkup (resolved at call time inside the
        # original registered _checkup_run) is isolated by _tick_dispatch.
        os.environ["AGENTWARE_CHECKUP"] = "1"
        with mock.patch.object(self.mod, "cmd_checkup",
                               side_effect=RuntimeError("boom")):
            by = self._dispatch()
        self.assertEqual(by["checkup"]["status"], "error")
        # the other jobs still dispatched (isolation)
        self.assertIn("updater", by)


if __name__ == "__main__":
    unittest.main()
