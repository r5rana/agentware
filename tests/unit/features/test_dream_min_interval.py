"""Dream min-interval floor — affordability guard #1 (Task 7, 260713).

Runner: python3 -m unittest tests.unit.features.test_dream_min_interval -v

A 6h min-interval floor (DREAM_CADENCE_INTERVAL_SEC) consulted in cmd_dream right
after the idle-gate and before the lock makes the 30-min tick express a ~6h
EFFECTIVE cadence. Proven here: a too-soon cycle SKIPS with a min-interval reason
and mutates NOTHING (no lock/journal/metric); a cycle past the floor proceeds;
--force bypasses the floor; a missing/garbled last-cycle ts fails-open (proceeds).
Hermetic (temp KB), never the live KB (R-LOC-03).
"""

import datetime
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from tests._fixtures import load_cli, build_synthetic_kb, run_cli
except ImportError:
    from _fixtures import load_cli, build_synthetic_kb, run_cli


def _iso_hours_ago(h):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")


class MinIntervalTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="aw_dream_mini_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.metrics = os.path.join(self.kdir, "logs", "metrics.jsonl")
        os.makedirs(os.path.dirname(self.metrics), exist_ok=True)

    def _mlines(self):
        with open(self.metrics, encoding="utf-8") as f:
            return f.read().count("\n")

    def _seed_cycle(self, hours_ago, ts=None):
        rec = {"event": "dream", "ts": ts or _iso_hours_ago(hours_ago),
               "finished": ts or _iso_hours_ago(hours_ago), "duration_s": 1.0,
               "force": False, "steps": []}
        with open(self.metrics, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    # --- unit: the gate helper -------------------------------------------
    def test_within_floor_returns_skip_reason(self):
        self._seed_cycle(1)
        import time
        r = self.mod._dream_min_interval_skip(self.kdir, time.time(), force=False)
        self.assertIsNotNone(r)
        self.assertIn("min-interval", r)

    def test_past_floor_proceeds(self):
        self._seed_cycle(7)
        import time
        self.assertIsNone(
            self.mod._dream_min_interval_skip(self.kdir, time.time(), force=False))

    def test_force_bypasses_floor(self):
        self._seed_cycle(1)
        import time
        self.assertIsNone(
            self.mod._dream_min_interval_skip(self.kdir, time.time(), force=True))

    def test_missing_last_cycle_fails_open(self):
        import time
        self.assertIsNone(
            self.mod._dream_min_interval_skip(self.kdir, time.time(), force=False))

    def test_garbled_ts_fails_open(self):
        with open(self.metrics, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "dream", "ts": "not-a-date"}) + "\n")
        import time
        self.assertIsNone(
            self.mod._dream_min_interval_skip(self.kdir, time.time(), force=False))

    # --- integration: cmd_dream skips + mutates nothing ------------------
    def test_cmd_dream_skips_within_floor_and_mutates_nothing(self):
        self._seed_cycle(1)
        before_lines = self._mlines()
        journal = os.path.join(self.kdir, self.mod.DREAM_JOURNAL_REL)
        # Isolate the min-interval decision from the load-based idle-gate.
        with mock.patch.object(self.mod, "dream_gate_reason", return_value=None):
            code, out, err = run_cli(["dream", "--format", "json"], self.kdir)
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["skipped"])
        self.assertEqual(payload["skip_kind"], "min-interval")
        self.assertEqual(self._mlines(), before_lines,
                         "a min-interval skip must write no new metric")
        self.assertFalse(os.path.exists(journal),
                         "a min-interval skip must write no journal")

    def test_cmd_dream_force_bypasses_floor(self):
        self._seed_cycle(1)
        with mock.patch.object(self.mod, "dream_gate_reason", return_value=None):
            code, out, err = run_cli(
                ["dream", "--force", "--steps", "a", "--format", "json"], self.kdir)
        self.assertEqual(code, 0, err)
        self.assertFalse(json.loads(out)["skipped"],
                         "--force bypasses the min-interval floor")


if __name__ == "__main__":
    unittest.main()
