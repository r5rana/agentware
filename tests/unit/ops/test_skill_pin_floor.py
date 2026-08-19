"""Task 6 — skills two-tier: SKILL_PIN_FLOOR flip wired into the measured
retrieval core (retrieve_bm25 + both recall_ranked branches), learnings never
evicted (R4-3), and a SELF-CONTAINED, ledger-independent pin-regression gate
that is proven DISCRIMINATING (a broken floor drops gold Recall@5). Hermetic.
"""

import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import load_cli
except ImportError:  # pragma: no cover
    from _fixtures import load_cli


def _e(eid, cat, text, created="2026-01-01"):
    return {"id": eid, "category": cat, "title": eid, "summary": text,
            "tags": [], "created": created}


class SkillPinFloorTest(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        self.tmp = tempfile.mkdtemp(prefix="aw-pin-")
        self._prev_floor = self.cli.SKILL_PIN_FLOOR

    def tearDown(self):
        self.cli.SKILL_PIN_FLOOR = self._prev_floor
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- _apply_skill_pin mechanics --------------------------------------
    def test_pins_qualifying_skill(self):
        A = {"id": "a", "category": "learnings"}
        S = {"id": "s", "category": "skills"}
        scored = [(A, 1.0), (S, 0.9)]
        out = self.cli._apply_skill_pin(list(scored), 0.85)
        self.assertEqual(out[0][0]["id"], "s")

    def test_below_floor_not_pinned(self):
        A = {"id": "a", "category": "learnings"}
        S = {"id": "s", "category": "skills"}
        out = self.cli._apply_skill_pin([(A, 1.0), (S, 0.5)], 0.85)
        self.assertEqual(out[0][0]["id"], "a")

    def test_skill_already_top_noop(self):
        S = {"id": "s", "category": "skills"}
        A = {"id": "a", "category": "learnings"}
        out = self.cli._apply_skill_pin([(S, 1.0), (A, 0.9)], 0.85)
        self.assertEqual([x[0]["id"] for x in out], ["s", "a"])

    def test_none_floor_noop(self):
        A = {"id": "a", "category": None}
        S = {"id": "s", "category": "skills"}
        out = self.cli._apply_skill_pin([(A, 1.0), (S, 0.9)], None)
        self.assertEqual([x[0]["id"] for x in out], ["a", "s"])

    def test_learning_never_evicted_boundary(self):
        # top_k=3; skill at index 3 (outside window) qualifies; a learning sits at
        # rank top_k-1 (index 2). Pinning must keep the learning in the top-3 and
        # evict a NON-learning instead (R4-3).
        A = {"id": "a", "category": "decisions"}
        B = {"id": "b", "category": "references"}
        L = {"id": "L", "category": "learnings"}
        S = {"id": "s", "category": "skills"}
        X = {"id": "x", "category": "references"}
        scored = [(A, 1.0), (B, 0.95), (L, 0.90), (S, 0.88), (X, 0.80)]
        out = self.cli._apply_skill_pin(scored, 0.85, top_k=3)
        top3 = [e["id"] for (e, _s) in out[:3]]
        self.assertEqual(out[0][0]["id"], "s", "skill pinned to rank 0")
        self.assertIn("L", top3, "learning must survive in the top_k window")
        # A non-learning (B) is the one pushed out instead.
        self.assertEqual(out[3][0]["id"], "b")

    # --- pin fires in the retrieval paths --------------------------------
    def _data(self):
        # Two full-matching docs; the non-skill wins the tie-break (id asc), so the
        # skill is naturally rank 1 -> the pin must move it to rank 0.
        return {"entries": [
            _e("aaa-doc", None, "alpha beta gamma"),
            _e("zzz-skill", "skills", "alpha beta gamma"),
        ]}

    def test_pin_fires_in_retrieve_bm25(self):
        self.cli.SKILL_PIN_FLOOR = 0.85
        ids = self.cli.retrieve_bm25(self.tmp, self._data(), "alpha beta gamma", top_k=5)
        self.assertEqual(ids[0], "zzz-skill")
        # With the pin disabled the non-skill wins the tie-break.
        self.cli.SKILL_PIN_FLOOR = None
        ids2 = self.cli.retrieve_bm25(self.tmp, self._data(), "alpha beta gamma", top_k=5)
        self.assertEqual(ids2[0], "aaa-doc")

    def test_pin_fires_in_recall_ranked_bm25(self):
        self.cli.SKILL_PIN_FLOOR = 0.85
        out = self.cli.recall_ranked(self.tmp, self._data(), "alpha beta gamma",
                                     top_k=5, strategy="bm25")
        self.assertEqual(out[0][0]["id"], "zzz-skill")

    def test_pin_fires_in_recall_ranked_embed(self):
        # No local embedder in tests -> bm25+embed gracefully falls back to bm25,
        # then the pin (pre-trim) applies. Proves the Mode-B wiring (R2-7).
        self.cli.SKILL_PIN_FLOOR = 0.85
        out = self.cli.recall_ranked(self.tmp, self._data(), "alpha beta gamma",
                                     top_k=5, strategy="bm25+embed")
        self.assertEqual(out[0][0]["id"], "zzz-skill")

    # --- self-contained gate over the shipped fixture --------------------
    def test_gold_fixture_no_regression(self):
        fx = os.path.join(self.cli.REPO_ROOT, "tests", "fixtures", "gold_fixture_suite.json")
        self.cli.SKILL_PIN_FLOOR = None
        off = self.cli.evaluate_gold_fixture(self.tmp, fx, "bm25", 5)
        self.cli.SKILL_PIN_FLOOR = 0.85
        on = self.cli.evaluate_gold_fixture(self.tmp, fx, "bm25", 5)
        self.assertGreaterEqual(on["metrics"]["recall_at_k"], off["metrics"]["recall_at_k"],
                                "the 0.85 pin must NOT drop gold Recall@5")
        self.assertEqual(on["metrics"]["recall_at_k"], 1.0)

    # --- DISCRIMINATION: the check catches a bad pin ---------------------
    def test_broken_pin_drops_recall(self):
        # A corpus where the gold entry sits at rank 5 and an irrelevant skill at
        # rank 6. A broken (too-low) floor pins the skill, evicting the gold entry
        # from top-5 -> Recall@5 DROPS. This proves the self-contained gate is
        # discriminating (not vacuous) and would STOP a bad pin.
        data = {"entries": [
            _e("d1", None, "alpha beta gamma delta alpha beta gamma delta"),
            _e("d2", None, "alpha beta gamma delta alpha beta gamma"),
            _e("d3", None, "alpha beta gamma delta alpha beta"),
            _e("d4", None, "alpha beta gamma delta alpha"),
            _e("gold-x", None, "alpha beta gamma delta"),
            _e("skill-junk", "skills", "alpha"),
        ]}
        gold = [{"query": "alpha beta gamma delta", "expected_ids": ["gold-x"]}]

        self.cli.SKILL_PIN_FLOOR = None
        r_off = self.cli.evaluate(self.tmp, data, gold, "bm25", 5)
        self.assertEqual(r_off["metrics"]["recall_at_k"], 1.0, "gold-x should be in top-5")

        self.cli.SKILL_PIN_FLOOR = 0.01  # broken/over-aggressive floor
        r_bad = self.cli.evaluate(self.tmp, data, gold, "bm25", 5)
        self.assertLess(r_bad["metrics"]["recall_at_k"], 1.0,
                        "a broken pin MUST drop Recall@5 (the gate is discriminating)")

        self.cli.SKILL_PIN_FLOOR = 0.85  # correct floor: skill far below 85%*top
        r_ok = self.cli.evaluate(self.tmp, data, gold, "bm25", 5)
        self.assertEqual(r_ok["metrics"]["recall_at_k"], 1.0,
                         "the correct 0.85 floor must NOT evict the gold entry")


if __name__ == "__main__":
    unittest.main()
