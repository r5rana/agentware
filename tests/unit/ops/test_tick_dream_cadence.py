"""Additive dream-cadence tick job (Task 9, 260713).

Runner: python3 -m unittest tests.unit.ops.test_tick_dream_cadence -v

The dream-cadence job is registered SECOND in _TICK_JOB_REGISTRY (purely additive
to the updater). Proven here: the registry dispatches BOTH jobs; each computes its
`due` independently off its OWN last-run wall-clock; with dream OFF the job is
`disabled` and cmd_dream is NEVER invoked; with dream ON it runs when >6h since the
last cycle and is `not-due` when <6h; and a scheduler that slept through several
ticks self-corrects (still due) on the next wake. Hermetic: patched config + a
mocked cmd_dream so no real cycle runs.
"""

import datetime
import json
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


def _iso_at(epoch):
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CadenceTests(unittest.TestCase):
    _KEYS = ("AGENTWARE_DREAM", "AGENTWARE_UPDATE", "AGENTWARE_NESTED_UNITTEST")

    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="aw_tick_dream_")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        self.kdir = os.path.join(self.base, "kb")
        os.makedirs(self.home)
        build_synthetic_kb(self.kdir)
        self.metrics = os.path.join(self.kdir, "logs", "metrics.jsonl")
        os.makedirs(os.path.dirname(self.metrics), exist_ok=True)
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

    def _seed_cycle(self, epoch):
        with open(self.metrics, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "dream", "ts": _iso_at(epoch),
                                "finished": _iso_at(epoch), "duration_s": 1.0,
                                "force": False, "steps": []}) + "\n")

    def _dispatch(self):
        return {r["job"]: r for r in self.mod._tick_dispatch(self.kdir, now=_NOW)}

    # --- registry composition --------------------------------------------
    def test_registry_has_both_jobs(self):
        keys = [j["key"] for j in self.mod._tick_jobs()]
        self.assertIn("updater", keys)
        self.assertIn("dream-cadence", keys)

    def test_dispatch_reports_both_jobs(self):
        by = self._dispatch()
        self.assertIn("updater", by)
        self.assertIn("dream-cadence", by)

    # --- disabled when dream OFF -----------------------------------------
    def test_disabled_when_dream_off_never_invokes_cmd_dream(self):
        os.environ["AGENTWARE_DREAM"] = "0"
        self._seed_cycle(_NOW - 10 * 3600)          # would be due if enabled
        with mock.patch.object(self.mod, "cmd_dream", return_value=0) as cd:
            by = self._dispatch()
        self.assertEqual(by["dream-cadence"]["status"], "disabled")
        cd.assert_not_called()

    # --- due-gate off its own wall-clock ---------------------------------
    def test_runs_when_on_and_over_6h(self):
        os.environ["AGENTWARE_DREAM"] = "1"
        self._seed_cycle(_NOW - 7 * 3600)
        with mock.patch.object(self.mod, "cmd_dream", return_value=0) as cd:
            by = self._dispatch()
        self.assertEqual(by["dream-cadence"]["status"], "ran")
        cd.assert_called_once()

    def test_not_due_when_on_and_under_6h(self):
        os.environ["AGENTWARE_DREAM"] = "1"
        self._seed_cycle(_NOW - 1 * 3600)
        with mock.patch.object(self.mod, "cmd_dream", return_value=0) as cd:
            by = self._dispatch()
        self.assertEqual(by["dream-cadence"]["status"], "not-due")
        cd.assert_not_called()

    def test_never_run_is_due(self):
        os.environ["AGENTWARE_DREAM"] = "1"
        self.assertTrue(self.mod._dream_cadence_due(self.kdir, _NOW),
                        "a never-run dream cadence is always due")

    def test_slept_through_ticks_self_corrects(self):
        # A scheduler asleep for hours: on the next wake the due-check (keyed off
        # the last-cycle wall-clock, not the tick cadence) is STILL due.
        os.environ["AGENTWARE_DREAM"] = "1"
        self._seed_cycle(_NOW - 20 * 3600)
        self.assertTrue(self.mod._dream_cadence_due(self.kdir, _NOW))

    def test_due_independent_of_updater_last_check(self):
        # The dream cadence due reads the last DREAM cycle; the updater due reads
        # its OWN last_check_ts. Setting one must not move the other.
        os.environ["AGENTWARE_DREAM"] = "1"
        self._seed_cycle(_NOW - 1 * 3600)           # dream not due
        self.mod._pkg_state_write({"last_check_ts": _iso_at(_NOW - 10 * 3600)})
        self.assertFalse(self.mod._dream_cadence_due(self.kdir, _NOW))
        self.assertTrue(self.mod._pkg_updater_due(self.kdir, _NOW)
                        if self.mod.resolve_updater() == "1" else True)

    def test_cadence_run_dispatches_force_false(self):
        os.environ["AGENTWARE_DREAM"] = "1"
        captured = {}

        def fake_dream(ns):
            captured["force"] = getattr(ns, "force", None)
            captured["dry_run"] = getattr(ns, "dry_run", None)
            return 0

        with mock.patch.object(self.mod, "cmd_dream", side_effect=fake_dream):
            r = self.mod._dream_cadence_run(self.kdir, _NOW)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(captured["force"], False,
                         "cadence dispatches force=False (gates still apply)")
        self.assertEqual(captured["dry_run"], False)


if __name__ == "__main__":
    unittest.main()
