"""Dream plan-state DRIFT detection (feature 260710, Task 5d) — report-only.

Dream step e enumerates `stalled` (running claimed >72h ago) and `stale-draft`
(draft idle > lane TTL: 7d auto / 90d operator) plans; `cancelled` plans are
excluded; NOTHING is mutated (report-only). Also covers the work/auto/ walker
extension.

Test names are prefixed `test_drift_`.

Runner: python3 -m unittest tests.unit.features.test_drift_dream -v
"""

import os
import shutil
import tempfile
import time
import unittest

try:
    from tests._fixtures import build_synthetic_kb, load_cli
except ImportError:
    from _fixtures import build_synthetic_kb, load_cli


def _plan(feature, status, claimed_ts=None):
    hdr = ("# Plan — %s\n\n> Feature: `%s`\n> Created: 2026-01-01\n"
           "> Status: %s\n" % (feature, feature, status))
    if claimed_ts:
        hdr += "> Claimed-by: alice@boxA %s\n" % claimed_ts
    return (hdr + "> Type: feature work\n\n---\n\n## Tasks\n\n"
            "- ⬜ **1** t\n  *Verify:* ok\n\n---\n\n## Acceptance criteria\n\n"
            "- [ ] x\n\n<promise>X_COMPLETE</promise>\n")


class DriftTestCase(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-drift-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = load_cli()

    def make(self, feature, status, claimed_ts=None, age_days=None):
        d = os.path.join(self.kdir, "work", feature)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "plan.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(_plan(feature, status, claimed_ts))
        if age_days is not None:
            old = time.time() - age_days * 86400
            os.utime(p, (old, old))
        return p

    def run_step(self):
        return self.mod._dream_step_detect_report(self.kdir, dry_run=False)

    def report_body(self):
        with open(os.path.join(self.kdir, "logs",
                               "dream-report-latest.md")) as f:
            return f.read()

    def test_drift_stalled_running_flagged(self):
        # running, claimed in 2020 -> age >> 72h -> stalled
        self.make("260101-stall", "running", claimed_ts="2020-01-01T00:00:00Z")
        res = self.run_step()
        self.assertGreaterEqual(res["stalled"], 1)
        self.assertIn("[stalled]", self.report_body())

    def test_drift_fresh_running_not_flagged(self):
        now_iso = self.mod._now_iso()
        self.make("260101-fresh", "running", claimed_ts=now_iso)
        res = self.run_step()
        self.assertEqual(res["stalled"], 0)

    def test_drift_stale_draft_auto_lane_7d(self):
        # auto draft idle 10 days -> > 7d auto TTL -> stale-draft
        self.make(os.path.join("auto", "260101-sd"), "draft", age_days=10)
        res = self.run_step()
        self.assertGreaterEqual(res["stale_draft"], 1)
        self.assertIn("auto lane", self.report_body())

    def test_drift_operator_draft_under_90d_not_flagged(self):
        # operator draft idle 30 days -> < 90d operator TTL -> NOT flagged
        self.make("260101-op", "draft", age_days=30)
        res = self.run_step()
        self.assertEqual(res["stale_draft"], 0)

    def test_drift_operator_draft_over_90d_flagged(self):
        self.make("260101-old", "draft", age_days=120)
        res = self.run_step()
        self.assertGreaterEqual(res["stale_draft"], 1)

    def test_drift_cancelled_excluded(self):
        # cancelled + old claim + old mtime -> excluded from BOTH buckets
        self.make("260101-cancel", "cancelled",
                  claimed_ts="2020-01-01T00:00:00Z", age_days=120)
        res = self.run_step()
        self.assertEqual(res["stalled"], 0)
        self.assertEqual(res["stale_draft"], 0)

    def test_drift_report_only_no_mutation(self):
        p = self.make("260101-nm", "running", claimed_ts="2020-01-01T00:00:00Z")
        before = open(p, encoding="utf-8").read()
        self.run_step()
        self.assertEqual(open(p, encoding="utf-8").read(), before)

    def test_drift_walker_sees_auto_lane(self):
        # a stalled workorder under work/auto/ must be detected (walker extension)
        self.make(os.path.join("auto", "260101-wo"), "running",
                  claimed_ts="2020-01-01T00:00:00Z")
        res = self.run_step()
        self.assertGreaterEqual(res["stalled"], 1)
        self.assertIn("work/auto/260101-wo/plan.md", self.report_body())


if __name__ == "__main__":
    unittest.main()
