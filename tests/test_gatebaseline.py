"""Release-gate armament tests (feature 260711-emitter-gate-hardening, T2).

In P1.5 the gates passed VACUOUSLY: no comparable gold-fixture baseline row was
ever seeded (the only ledger writer is `eval … --record`, whose --strategy
defaults to `tag`, while BOTH Tier-A gates read `bm25`), so `gate release`
reported "first run (no baseline) — seeding and passing". This feature seeds the
correct row (`eval --suite gold-fixture --strategy bm25 --record`).

These tests PROVE the gate is not merely green-because-empty by unit-testing the
decision function `evaluate_gate` directly — the frozen fixture is not
repointable without corpus surgery, so we assert the pass/FAIL LOGIC (pitfall e):
  - an INFLATED metric baseline -> regression -> (False, non-empty);
  - a reliability drop past epsilon -> regression;
  - a within-tolerance baseline passes;
  - baseline=None (first-ever run) passes (seeds).
Plus a wiring test: `gate_baseline` finds a comparable bm25/gold-fixture row
(non-vacuous) yet returns None when only `tag` rows exist (the exact P1.5 hole).

`test_gatebaseline_` is unique vs every existing module filename and method.
Runner: python3 -m unittest tests.test_gatebaseline -v
"""

import importlib.machinery
import importlib.util
import os
import unittest

try:
    from tests._fixtures import REPO_ROOT, attach_lib_resolver
except ImportError:
    from _fixtures import REPO_ROOT, attach_lib_resolver


def _load():
    ld = importlib.machinery.SourceFileLoader(
        "aw_gatebaseline", os.path.join(REPO_ROOT, "scripts", "agentware"))
    m = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("aw_gatebaseline", ld))
    ld.exec_module(m)
    return attach_lib_resolver(m)


class GateBaselineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load()

    # --- the gate CAN fail (proves non-vacuity of the pass verdict) ---------
    def test_gatebaseline_perturbed_fails(self):
        m = self.m
        metrics = {"recall_at_k": 1.0, "precision_at_k": 0.2,
                   "ndcg_at_k": 1.0, "mrr": 1.0}
        # An INFLATED baseline (recall 1.5, far above the current 1.0) MUST
        # register a regression -> the gate FAILS. If evaluate_gate could never
        # return False, every "PASS" would be meaningless.
        inflated = {"recall_at_k": 1.5, "precision_at_k": 0.2,
                    "ndcg_at_k": 1.0, "mrr": 1.0, "reliability": 100.0}
        passed, regs = m.evaluate_gate(metrics, 100.0, inflated, 0.02)
        self.assertFalse(passed, "an inflated baseline must FAIL the gate")
        self.assertTrue(regs, "a failing gate must report >=1 regression")
        self.assertIn("recall_at_k", [r[0] for r in regs])

    def test_gatebaseline_reliability_regression_fails(self):
        m = self.m
        passed, regs = m.evaluate_gate(
            {"recall_at_k": 1.0}, 50.0,
            {"recall_at_k": 1.0, "reliability": 100.0}, 0.02)  # 50 < 100 - 2
        self.assertFalse(passed)
        self.assertIn("reliability", [r[0] for r in regs])

    # --- the gate passes when it should (no false regressions) --------------
    def test_gatebaseline_within_tolerance_passes(self):
        # 0.99 vs 1.0 is within tolerance 0.02 -> no regression.
        passed, regs = self.m.evaluate_gate(
            {"recall_at_k": 0.99}, 100.0, {"recall_at_k": 1.0}, 0.02)
        self.assertTrue(passed)
        self.assertEqual(regs, [])

    def test_gatebaseline_none_is_first_run_seed(self):
        passed, regs = self.m.evaluate_gate(
            {"recall_at_k": 1.0}, 100.0, None, 0.02)
        self.assertTrue(passed)
        self.assertEqual(regs, [])

    # --- wiring: the seeded row arms; tag-only rows stay vacuous ------------
    def test_gatebaseline_comparable_row_arms_baseline(self):
        m = self.m
        fp = "deadbeefcafe"
        rows = [{"strategy": "bm25", "suite": m.GOLD_FIXTURE_SUITE,
                 "corpus_fingerprint": fp, "reliability": 100.0,
                 "metrics": {"recall_at_k": 1.0, "precision_at_k": 0.2,
                             "ndcg_at_k": 1.0, "mrr": 1.0}}]
        base = m.gate_baseline(rows, "bm25", m.GOLD_FIXTURE_SUITE, fp)
        self.assertIsNotNone(base, "a bm25/gold-fixture row must arm the baseline")
        self.assertEqual(base.get("recall_at_k"), 1.0)

    def test_gatebaseline_tag_only_rows_stay_vacuous(self):
        # The P1.5 hole reproduced: recording under the default --strategy tag
        # leaves the bm25 gate with NO comparable row -> None -> vacuous pass.
        m = self.m
        rows = [{"strategy": "tag", "suite": m.GOLD_FIXTURE_SUITE,
                 "corpus_fingerprint": "x", "metrics": {"recall_at_k": 1.0}}]
        self.assertIsNone(m.gate_baseline(rows, "bm25", m.GOLD_FIXTURE_SUITE, "x"))

    def test_gatebaseline_fingerprint_mismatch_not_comparable(self):
        # A row over a DIFFERENT corpus fingerprint is not comparable (repro
        # safety) -> None, so the gate seeds instead of false-failing on drift.
        m = self.m
        rows = [{"strategy": "bm25", "suite": m.GOLD_FIXTURE_SUITE,
                 "corpus_fingerprint": "old", "metrics": {"recall_at_k": 1.0}}]
        self.assertIsNone(m.gate_baseline(rows, "bm25", m.GOLD_FIXTURE_SUITE, "new"))


if __name__ == "__main__":
    unittest.main()
