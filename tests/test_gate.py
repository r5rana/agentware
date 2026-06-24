"""Tests for the regression gate (Phase 3B.2).

`eval --gate [--tolerance T]` compares the current run to the BEST prior
comparable ledger row (same strategy) and exits non-zero if a headline quality
metric or the reliability score regresses beyond tolerance. A first-ever run
(no comparable history) passes and seeds the baseline. The gate ALWAYS records
(append-only), reading prior rows BEFORE appending so a fresh row never compares
against itself.

Three layers, mirroring the ledger tests:
  1. Pure helpers — gate_baseline (best-per-metric over comparable rows) and
     evaluate_gate (pass/fail + regression list vs tolerance).
  2. End-to-end CLI — a prior-better row makes `--gate` fail (non-zero); an
     equal/better run passes; a first-ever run passes + seeds; the gate appends
     a row without mutating prior rows (append-only preserved).

Stdlib `unittest` only. Never touches the real KB. Sets the nested-unittest
recursion guard so `--gate` (which records) does NOT re-spawn the suite.
"""

import json
import os

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # allow `python3 -m unittest tests.test_gate`
    from _fixtures import SyntheticKBTestCase, load_cli


CLI = load_cli()


def _row(strategy, recall, reliability, **metric_over):
    """A minimal ledger row with the fields the gate reads."""
    metrics = {"recall_at_k": recall, "precision_at_k": 0.2,
               "ndcg_at_k": recall, "mrr": recall}
    metrics.update(metric_over)
    return {"strategy": strategy, "metrics": metrics, "reliability": reliability}


class GateBaselineTest(SyntheticKBTestCase):
    """gate_baseline picks the best per-metric value among comparable rows."""

    def test_no_history_returns_none(self):
        self.assertIsNone(CLI.gate_baseline([], "bm25"))

    def test_filters_by_strategy(self):
        rows = [_row("tag", 0.9, 90.0)]
        # No bm25 row -> not comparable -> None (first run for this strategy).
        self.assertIsNone(CLI.gate_baseline(rows, "bm25"))

    def test_takes_max_across_comparable_rows(self):
        rows = [_row("bm25", 0.5, 60.0), _row("bm25", 0.8, 75.0),
                _row("tag", 0.99, 99.0)]
        base = CLI.gate_baseline(rows, "bm25")
        self.assertAlmostEqual(base["recall_at_k"], 0.8)   # max over bm25 only
        self.assertAlmostEqual(base["reliability"], 75.0)

    def test_ignores_non_numeric_values(self):
        rows = [{"strategy": "bm25", "metrics": {"recall_at_k": None},
                 "reliability": "n/a"},
                _row("bm25", 0.7, 70.0)]
        base = CLI.gate_baseline(rows, "bm25")
        self.assertAlmostEqual(base["recall_at_k"], 0.7)
        self.assertAlmostEqual(base["reliability"], 70.0)


class EvaluateGateTest(SyntheticKBTestCase):
    """evaluate_gate decides pass/fail and lists regressions vs tolerance."""

    def test_none_baseline_passes(self):
        passed, regs = CLI.evaluate_gate(
            {"recall_at_k": 0.1}, 10.0, None, 0.02)
        self.assertTrue(passed)
        self.assertEqual(regs, [])

    def test_equal_or_better_passes(self):
        base = {"recall_at_k": 0.8, "reliability": 80.0}
        metrics = {"recall_at_k": 0.8, "precision_at_k": 0.2,
                   "ndcg_at_k": 0.8, "mrr": 0.8}
        passed, regs = CLI.evaluate_gate(metrics, 85.0, base, 0.02)
        self.assertTrue(passed)
        self.assertEqual(regs, [])

    def test_within_tolerance_passes(self):
        base = {"recall_at_k": 0.80}
        metrics = {"recall_at_k": 0.79}  # drop 0.01 <= tolerance 0.02
        passed, regs = CLI.evaluate_gate(metrics, 100.0, base, 0.02)
        self.assertTrue(passed)

    def test_beyond_tolerance_fails(self):
        base = {"recall_at_k": 0.80}
        metrics = {"recall_at_k": 0.70}  # drop 0.10 > tolerance 0.02
        passed, regs = CLI.evaluate_gate(metrics, 100.0, base, 0.02)
        self.assertFalse(passed)
        self.assertEqual(len(regs), 1)
        self.assertEqual(regs[0][0], "recall_at_k")

    def test_reliability_uses_point_scale(self):
        # tolerance 0.02 -> 2.0 reliability points of allowance.
        base = {"reliability": 90.0}
        metrics = {}
        # drop 1.5 points -> within 2.0 -> pass
        self.assertTrue(CLI.evaluate_gate(metrics, 88.5, base, 0.02)[0])
        # drop 3.0 points -> beyond 2.0 -> fail
        self.assertFalse(CLI.evaluate_gate(metrics, 87.0, base, 0.02)[0])


class GateEndToEndTest(SyntheticKBTestCase):
    """A real `eval --gate` over the synthetic KB enforces the regression rule."""

    def setUp(self):
        super().setUp()
        # Avoid the nested-unittest subprocess that --record spawns.
        self._prev = os.environ.get("AGENTWARE_NESTED_UNITTEST")
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"
        self.addCleanup(self._restore)

    def _restore(self):
        if self._prev is None:
            os.environ.pop("AGENTWARE_NESTED_UNITTEST", None)
        else:
            os.environ["AGENTWARE_NESTED_UNITTEST"] = self._prev

    def _write_gold(self, rows):
        path = os.path.join(self.kdir, "recall-gold.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f)
        return path

    def _ledger_path(self):
        return os.path.join(self.kdir, "benchmarks", "history.jsonl")

    def _seed_ledger(self, *rows):
        path = self._ledger_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        return path

    def test_first_run_passes_and_seeds(self):
        gold = self._write_gold([
            {"query": "geofence reminders never fired arrive",
             "expected_ids": ["learn-geofence-reminders"]},
        ])
        code, out, err = self.run_cli(
            ["eval", "--gold", gold, "--strategy", "bm25", "--gate",
             "--format", "json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["gate"]["seeded"])
        self.assertTrue(payload["gate"]["passed"])
        # Gate implies record: the ledger now exists with exactly one row.
        with open(self._ledger_path(), encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 1)

    def test_prior_better_row_fails_gate(self):
        # Prior bm25 row with an impossible-to-match recall + reliability.
        self._seed_ledger(_row("bm25", 0.99, 99.0))
        with open(self._ledger_path(), "rb") as f:
            before = f.read()
        # top_k=1 + a query whose top bm25 hit is NOT the expected id => recall 0.
        gold = self._write_gold([
            {"query": "python runtime stdlib dependency",
             "expected_ids": ["learn-geofence-reminders"]},
        ])
        code, out, err = self.run_cli(
            ["eval", "--gold", gold, "--strategy", "bm25", "--gate",
             "--top-k", "1", "--format", "json"])
        self.assertEqual(code, 1, "expected non-zero exit on regression")
        payload = json.loads(out)
        self.assertFalse(payload["gate"]["passed"])
        self.assertTrue(payload["gate"]["regressions"])
        # Append-only preserved even on failure: prior bytes are an exact prefix.
        with open(self._ledger_path(), "rb") as f:
            after = f.read()
        self.assertTrue(after.startswith(before))
        self.assertEqual(after.count(b"\n"), 2)

    def test_equal_or_better_run_passes(self):
        # Prior bm25 row with a low bar the current run clears.
        self._seed_ledger(_row("bm25", 0.0, 0.0))
        gold = self._write_gold([
            {"query": "geofence reminders never fired arrive",
             "expected_ids": ["learn-geofence-reminders"]},
        ])
        code, out, err = self.run_cli(
            ["eval", "--gold", gold, "--strategy", "bm25", "--gate",
             "--format", "json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertFalse(payload["gate"]["seeded"])
        self.assertTrue(payload["gate"]["passed"])

    def test_text_format_returns_gate_exit_code(self):
        self._seed_ledger(_row("bm25", 0.99, 99.0))
        gold = self._write_gold([
            {"query": "python runtime stdlib dependency",
             "expected_ids": ["learn-geofence-reminders"]},
        ])
        code, out, err = self.run_cli(
            ["eval", "--gold", gold, "--strategy", "bm25", "--gate",
             "--top-k", "1"])
        self.assertEqual(code, 1, err)
        self.assertIn("gate: FAIL", out)
