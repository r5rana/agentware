"""Deterministic read-only window aggregator + flywheel signals (Task 2).

Runner: python3 -m unittest tests.unit.ops.test_checkup_aggregate -v

Proven here: read-through renders a rising/falling/flat trend (None-filtered) with
a sample_n; corpus-coverage is a point-in-time GAUGE with no direction; error-
recovery + recurrence are gauges (recurrence flagged low-precision, never a
workorder driver); learning-freshness is the surfaced-by-recall lower bound; EVERY
channel degrades to insufficient-data on an empty snapshot (Q10, never a crash or
a fabricated number); and the window anchor is a STABLE cadence-bucket epoch, never
raw now (INV-1). Hermetic: derive_retrieval + corpus metrics are monkeypatched, so
no session transcripts are needed.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

try:
    from tests._fixtures import load_cli
    from tests._operator_config import isolate_operator_config
except ImportError:
    from _fixtures import load_cli
    from _operator_config import isolate_operator_config


class FlywheelTests(unittest.TestCase):
    def setUp(self):
        self.m = load_cli()
        self.kb = tempfile.mkdtemp(prefix="aw_ck_agg_")
        self.addCleanup(shutil.rmtree, self.kb, True)
        # The exam reads MACHINE-level operator settings (kb mode, per-user
        # handle) that no fixture here writes, so without this the assertions
        # below are about the operator's laptop rather than about the bare KB.
        isolate_operator_config(self, self.m, self.kb)
        os.makedirs(os.path.join(self.kb, "learnings"))
        self._write_index([])
        self.anchor = self.m._checkup_anchor_epoch(time.time())

    def _write_index(self, entries):
        with open(os.path.join(self.kb, "index.json"), "w", encoding="utf-8") as f:
            json.dump({"entries": entries, "tags": {}}, f)

    def test_read_through_rising_and_gauges(self):
        ret = {"sessions": [
                   {"returned_ids": ["a"], "read_through": {"ratio": 0.2}},
                   {"returned_ids": ["b"], "read_through": {"ratio": 0.8}}],
               "corpus_coverage": {"ratio": 0.5, "corpus_size": 10}}
        with mock.patch.object(self.m, "derive_retrieval", return_value=ret), \
             mock.patch.object(self.m, "_premise_corpus_metric_value",
                               side_effect=lambda metric, kdir:
                               42 if metric == "claude-error-records" else 7):
            fw = self.m._checkup_flywheel(self.kb, 0, self.anchor)
        self.assertEqual(fw["read_through"]["direction"], "rising")
        self.assertEqual(fw["read_through"]["sample_n"], 2)
        self.assertTrue(fw["corpus_coverage"]["gauge"])
        self.assertEqual(fw["corpus_coverage"]["value"], 0.5)
        self.assertEqual(fw["error_recovery"]["value"], 42)
        self.assertTrue(fw["recurrence"]["low_precision"])
        self.assertEqual(fw["recurrence"]["value"], 7)

    def test_empty_channels_degrade_to_insufficient(self):
        ret = {"sessions": [], "corpus_coverage": {"ratio": None, "corpus_size": 0}}
        with mock.patch.object(self.m, "derive_retrieval", return_value=ret), \
             mock.patch.object(self.m, "_premise_corpus_metric_value",
                               return_value=None):
            fw = self.m._checkup_flywheel(self.kb, 0, self.anchor)
        for k in ("read_through", "corpus_coverage", "error_recovery",
                  "recurrence", "learning_freshness"):
            self.assertTrue(fw[k].get("insufficient_data"), "%s not insufficient" % k)

    def test_single_session_read_through_has_no_direction(self):
        ret = {"sessions": [{"returned_ids": [],
                             "read_through": {"ratio": 0.5}}],
               "corpus_coverage": {"ratio": None, "corpus_size": 0}}
        with mock.patch.object(self.m, "derive_retrieval", return_value=ret), \
             mock.patch.object(self.m, "_premise_corpus_metric_value",
                               return_value=None):
            fw = self.m._checkup_flywheel(self.kb, 0, self.anchor)
        self.assertIsNone(fw["read_through"]["direction"])
        self.assertEqual(fw["read_through"]["sample_n"], 1)

    def test_read_through_none_ratio_filtered(self):
        ret = {"sessions": [
                   {"returned_ids": [], "read_through": {"ratio": None}},
                   {"returned_ids": [], "read_through": {"ratio": 0.4}}],
               "corpus_coverage": {"ratio": None, "corpus_size": 0}}
        with mock.patch.object(self.m, "derive_retrieval", return_value=ret), \
             mock.patch.object(self.m, "_premise_corpus_metric_value",
                               return_value=None):
            fw = self.m._checkup_flywheel(self.kb, 0, self.anchor)
        # only the one non-None ratio survives -> single sample, no direction
        self.assertEqual(fw["read_through"]["sample_n"], 1)

    def test_learning_freshness_lower_bound(self):
        import datetime
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        self._write_index([
            {"id": "learn-fresh", "category": "learnings",
             "path": "learnings/fresh.md", "created": today},
            {"id": "learn-old", "category": "learnings",
             "path": "learnings/old.md", "created": "2020-01-01"}])
        ret = {"sessions": [{"returned_ids": ["learn-fresh"],
                             "read_through": {"ratio": None}}],
               "corpus_coverage": {"ratio": None, "corpus_size": 0}}
        lf = self.m._checkup_learning_freshness(self.kb, ret, self.anchor)
        self.assertEqual(lf["value"], 1)            # fresh surfaced by recall
        self.assertEqual(lf["fresh_total"], 1)      # old one is not fresh
        self.assertTrue(lf["lower_bound"])

    def test_anchor_epoch_is_stable_and_never_raw_now(self):
        now = 1_000_000_500.5
        a1 = self.m._checkup_anchor_epoch(now)
        a2 = self.m._checkup_anchor_epoch(now + 120)   # same 7-day bucket
        self.assertEqual(a1, a2)
        self.assertNotEqual(a1, now)
        self.assertEqual(a1 % self.m.CHECKUP_INTERVAL_SEC, 0)

    def test_aggregate_over_bare_kb_never_crashes(self):
        agg = self.m._checkup_aggregate(self.kb, time.time())
        self.assertIn("flywheel", agg)
        self.assertEqual(agg["proposed"], [])
        self.assertEqual(self.m._checkup_candidates(agg), [])

    def test_report_byte_stable_across_bucket_with_boundary_learning(self):
        # REGRESSION (adversarial-review finding 1): a learning near the 14-day
        # freshness boundary must NOT flip the rendered report between two runs in
        # the SAME cadence bucket — the cutoff keys off the stable anchor, not now.
        import datetime
        bucket = self.m.CHECKUP_INTERVAL_SEC
        now1 = self.anchor + 100.0                    # early in the bucket
        now2 = self.anchor + bucket - 100.0           # late in the SAME bucket
        created = datetime.datetime.fromtimestamp(
            self.anchor - 13 * 86400 + 100,
            datetime.timezone.utc).strftime("%Y-%m-%d")
        self._write_index([{"id": "learn-b", "category": "learnings",
                            "path": "learnings/b.md", "created": created}])
        r1 = self.m._checkup_render_report(self.m._checkup_aggregate(self.kb, now1))
        r2 = self.m._checkup_render_report(self.m._checkup_aggregate(self.kb, now2))
        self.assertEqual(r1, r2, "report byte-stable across the cadence bucket")

    def test_aggregate_does_not_rebuild_missing_index(self):
        # REGRESSION (adversarial-review finding 2): a missing index.json is a
        # normal fresh-clone state; the read-only exam must NEVER rebuild it.
        idx = os.path.join(self.kb, "index.json")
        os.remove(idx)
        with open(os.path.join(self.kb, "learnings", "x.md"), "w",
                  encoding="utf-8") as f:
            f.write("---\nid: learn-x\ncategory: learnings\ntitle: X\n"
                    "created: 2026-01-01\n---\nbody\n")
        self.m._checkup_aggregate(self.kb, time.time())
        self.assertFalse(os.path.exists(idx),
                         "checkup must not rebuild (write) index.json (Q7)")
        self.assertFalse(os.path.exists(os.path.join(self.kb, "FEATURES.md")))


if __name__ == "__main__":
    unittest.main()
