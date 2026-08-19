"""Watcher-watching (dream itself) — the load-bearing check (Task 4).

Runner: python3 -m unittest tests.unit.ops.test_checkup_watcher -v

Proven here: the journal parser captures ONLY numeric space-free fields (booleans/
dash/spaced values are skipped, audit L4-2); the "130-for-13-nights" frozen check
flags an ALLOWLISTED metric held within tolerance across >= MIN_FROZEN_CYCLES
PRESENT cycles, but NOT with fewer present samples, NOT when the values differ, and
NEVER for a non-allowlisted steady-state constant (an excluded count is not even
considered); a change-gate-skipped step is a GAP (absent), not a repeated value
(audit L4-1/L4-5); and the watcher REUSES _audit_dream_health_check for the single
last-cycle verdict while owning the window-level missed-cycle count (gated on dream
ON). Hermetic: a synthetic dream-journal, no real dream cycle.
"""

import os
import shutil
import tempfile
import time
import unittest

try:
    from tests._fixtures import load_cli
except ImportError:
    from _fixtures import load_cli


def _iso(ep):
    import datetime
    return datetime.datetime.fromtimestamp(
        ep, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.m = load_cli()
        self.kb = tempfile.mkdtemp(prefix="aw_ck_watch_")
        self.addCleanup(shutil.rmtree, self.kb, True)
        os.makedirs(os.path.join(self.kb, "logs"))
        self.now = time.time()
        # Isolate config so resolve_dream() reads env -> empty temp config ->
        # default (never the operator's real ~/.agentware/config.env).
        self._cfg = (self.m.HOME_CONFIG, self.m.CONFIG_PATHS)
        self.m.HOME_CONFIG = os.path.join(self.kb, ".agentware", "config.env")
        self.m.CONFIG_PATHS = (self.m.HOME_CONFIG,)
        self.addCleanup(self._restore_cfg)
        self._dream_env = os.environ.get("AGENTWARE_DREAM")
        os.environ.pop("AGENTWARE_DREAM", None)

    def _restore_cfg(self):
        self.m.HOME_CONFIG, self.m.CONFIG_PATHS = self._cfg
        if self._dream_env is None:
            os.environ.pop("AGENTWARE_DREAM", None)
        else:
            os.environ["AGENTWARE_DREAM"] = self._dream_env

    def _journal(self, cycles):
        """cycles = list of (age_days, {step.field: rendered_value})."""
        path = os.path.join(self.kb, self.m.DREAM_JOURNAL_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Dream Journal\n\n")
            for age_d, fields in cycles:
                ts = _iso(self.now - age_d * 86400)
                f.write("## dream %s\n\n- force: False\n" % ts)
                by_step = {}
                for k, v in fields.items():
                    step, field = k.split(".", 1)
                    by_step.setdefault(step, []).append("%s=%s" % (field, v))
                for step, kvs in by_step.items():
                    f.write("- step %s (x): ok  %s\n" % (step, " ".join(kvs)))
                f.write("\n")

    def _cycles(self):
        return [c for c in self.m._checkup_parse_journal(self.kb)]

    # --- journal parsing -----------------------------------------------------
    def test_parser_keeps_only_numeric_space_free(self):
        self._journal([(1, {"d.reliability": "0.96", "c.tests_ran": "142"})])
        # inject a boolean + a dashed + a spaced value manually
        path = os.path.join(self.kb, self.m.DREAM_JOURNAL_REL)
        with open(path, "a", encoding="utf-8") as f:
            f.write("- step z (x): ok  flag=True missing=- failed_tests=a, b\n")
        cyc = self._cycles()
        self.assertEqual(cyc[0]["metrics"].get("d.reliability"), 0.96)
        self.assertEqual(cyc[0]["metrics"].get("c.tests_ran"), 142.0)
        self.assertNotIn("z.flag", cyc[0]["metrics"])     # boolean skipped
        self.assertNotIn("z.missing", cyc[0]["metrics"])  # dash skipped

    # --- the 130-for-13-nights frozen check ----------------------------------
    def test_frozen_metric_flagged_across_min_cycles(self):
        self._journal([(k + 1, {"d.reliability": "0.96"})
                       for k in range(self.m.MIN_FROZEN_CYCLES)])
        frozen = self.m._checkup_frozen_metrics(self._cycles())
        self.assertEqual([f["metric"] for f in frozen], ["d.reliability"])
        self.assertEqual(frozen[0]["present_cycles"], self.m.MIN_FROZEN_CYCLES)

    def test_fewer_than_min_present_not_flagged(self):
        self._journal([(k + 1, {"d.reliability": "0.96"})
                       for k in range(self.m.MIN_FROZEN_CYCLES - 1)])
        self.assertEqual(self.m._checkup_frozen_metrics(self._cycles()), [])

    def test_differing_values_not_flagged(self):
        self._journal([(1, {"d.reliability": "0.90"}),
                       (2, {"d.reliability": "0.95"}),
                       (3, {"d.reliability": "0.99"})])
        self.assertEqual(self.m._checkup_frozen_metrics(self._cycles()), [])

    def test_excluded_constant_not_flagged(self):
        # unpromoted_markers=0 held forever is HEALTHY, not in the allowlist -> never
        # considered as frozen.
        self._journal([(k + 1, {"e.unpromoted_markers": "0", "e.stale": "0"})
                       for k in range(5)])
        self.assertEqual(self.m._checkup_frozen_metrics(self._cycles()), [])

    def test_change_gate_gap_is_not_a_repeat(self):
        # step d PRESENT in only 2 cycles (the others were change-gate-skipped ->
        # ABSENT). 2 present samples < MIN -> NOT frozen (a gap is not a value).
        self._journal([(1, {"d.reliability": "0.96"}),
                       (2, {"c.tests_ran": "10"}),        # d absent (skipped)
                       (3, {"c.tests_ran": "10"}),        # d absent (skipped)
                       (4, {"d.reliability": "0.96"})])
        frozen = self.m._checkup_frozen_metrics(self._cycles())
        self.assertNotIn("d.reliability", [f["metric"] for f in frozen])

    # --- watch_dream integration ---------------------------------------------
    def test_watch_reuses_dream_health_and_counts_missed(self):
        os.environ["AGENTWARE_DREAM"] = "1"
        self.addCleanup(os.environ.pop, "AGENTWARE_DREAM", None)
        # cycles present but the last one is old + a big gap -> missed_cycles > 0
        self._journal([(0.1, {"d.reliability": "0.96"}),
                       (5, {"d.reliability": "0.96"})])     # 5-day gap > 48h
        cyc = self._cycles()
        w = self.m._checkup_watch_dream(self.kb, cyc, self.now)
        self.assertIn("verdict", w)
        self.assertGreaterEqual(w["missed_cycles"], 1)

    def test_missed_cycles_inert_when_dream_off(self):
        # dream OFF -> the window-level missed-cycle finding does not fire.
        self._journal([(5, {"d.reliability": "0.96"})])
        w = self.m._checkup_watch_dream(self.kb, self._cycles(), self.now)
        self.assertEqual(w["missed_cycles"], 0)

    def test_empty_journal_insufficient(self):
        w = self.m._checkup_watch_dream(self.kb, [], self.now)
        self.assertTrue(w["insufficient_data"])
        self.assertEqual(w["frozen_metrics"], [])


if __name__ == "__main__":
    unittest.main()
