"""Deterministic report renderer (Task 6).

Runner: python3 -m unittest tests.unit.ops.test_checkup_report -v

Proven here: the renderer is a PURE function returning a byte-stable string —
fixed section order, window aggregates only, NO clock/run-id/timestamp in the body
(INV-1, audit L6-2), so two renders of the same aggregate are byte-identical; a
bare aggregate renders clean "insufficient data" markers; and the narrative section
appears ONLY when a narrative is present (and is labeled report-only).
"""

import re
import unittest

try:
    from tests._fixtures import load_cli
except ImportError:
    from _fixtures import load_cli

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _bare_agg():
    return {"flywheel": {"read_through": {"insufficient_data": True},
                         "corpus_coverage": {"insufficient_data": True},
                         "error_recovery": {"insufficient_data": True},
                         "recurrence": {"insufficient_data": True},
                         "learning_freshness": {"insufficient_data": True}},
            "plan_health": {"insufficient_data": True, "stalled": []},
            "drift": [], "watcher": {"verdict": "inert", "missed_cycles": 0,
                                     "frozen_metrics": [], "insufficient_data": True},
            "triggers": [{"signal": "corpus-size", "value": 5, "threshold": 7000,
                          "fired": False, "fires_workorder": True}],
            "narrative": None, "proposed": [], "rejected": [], "deferred": []}


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.m = load_cli()

    def test_byte_stable_across_renders(self):
        agg = _bare_agg()
        self.assertEqual(self.m._checkup_render_report(agg),
                         self.m._checkup_render_report(agg))

    def test_no_clock_or_run_id_in_body(self):
        self.assertIsNone(_ISO.search(self.m._checkup_render_report(_bare_agg())),
                          "the report body must carry no ISO timestamp (INV-1)")

    def test_fixed_section_order(self):
        body = self.m._checkup_render_report(_bare_agg())
        order = ["## Flywheel signals", "## Plan health",
                 "## Drift (declared vs derived)", "## Watcher-watching",
                 "## Structure triggers", "## Proposed workorders",
                 "## Rejected (governance escape-valve"]
        idxs = [body.find(h) for h in order]
        self.assertTrue(all(i >= 0 for i in idxs), "all sections present")
        self.assertEqual(idxs, sorted(idxs), "sections in fixed order")

    def test_insufficient_data_markers(self):
        body = self.m._checkup_render_report(_bare_agg())
        self.assertIn("read-through: insufficient data", body)
        self.assertIn("- none", body)   # empty drift/proposed/rejected

    def test_narrative_present_only_when_present(self):
        agg = _bare_agg()
        self.assertNotIn("## LLM narrative", self.m._checkup_render_report(agg))
        agg["narrative"] = {"narrative": "Trends are flat.",
                            "observations": ["look at eval"]}
        body = self.m._checkup_render_report(agg)
        self.assertIn("## LLM narrative (report-only", body)
        self.assertIn("Trends are flat.", body)
        self.assertIn("- observation: look at eval", body)

    def test_frozen_and_proposed_and_rejected_render(self):
        agg = _bare_agg()
        agg["watcher"]["frozen_metrics"] = [
            {"metric": "d.reliability", "value": 0.96, "present_cycles": 4}]
        agg["watcher"]["insufficient_data"] = False
        agg["proposed"] = [{"feature": "auto/checkup-x", "text": "do a thing"}]
        agg["rejected"] = [{"reason": "names a kernel path", "text": "edit steering"}]
        body = self.m._checkup_render_report(agg)
        self.assertIn("FROZEN: d.reliability", body)
        self.assertIn("auto/checkup-x", body)
        self.assertIn("rejected: names a kernel path", body)


if __name__ == "__main__":
    unittest.main()
