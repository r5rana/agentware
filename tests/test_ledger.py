"""Tests for the benchmark ledger (Phase 3B.1).

`eval --record` appends ONE immutable, commit + UTC-date-stamped row to
`<kdir>/benchmarks/history.jsonl`. These tests cover three layers:

  1. Pure helpers — `compute_reliability` (documented formula) and
     `build_ledger_row` (deterministic given injected volatile state).
  2. Append-only / immutability — `append_ledger_row` only ever grows the file;
     re-recording leaves earlier rows byte-identical.
  3. End-to-end — a real `eval --record` over a synthetic KB + gold set produces
     a valid line with the full schema (commit + dates + metrics + reliability),
     tagged with the current HEAD SHA.

Stdlib `unittest` only (no pytest, no new deps). Never touches the real KB. The
nested-unittest recursion guard is set so `--record` does NOT re-spawn the suite.
"""

import json
import os

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # allow `python3 -m unittest tests.test_ledger`
    from _fixtures import SyntheticKBTestCase, load_cli


CLI = load_cli()

# Headline metric block shared by the synthetic results below.
_METRICS = {
    "recall_at_k": 0.90,
    "precision_at_k": 0.20,
    "ndcg_at_k": 0.85,
    "mrr": 0.80,
    "latency_ms_mean": 1.23,
    "latency_ms_p50": 1.10,
    "context_tokens_mean": 512.0,
}

_GIT = {"commit": "abc1234", "subject": "test subject",
        "committed": "2026-06-24T00:00:00Z", "dirty": True}


class ReliabilityFormulaTest(SyntheticKBTestCase):
    """Hand-computed ground truth for the documented composite formula."""

    def test_all_components_present(self):
        # 0.40*0.9 + 0.30*1.0 + 0.15*1.0 + 0.15*1.0 = 0.36+0.30+0.15+0.15 = 0.96
        score = CLI.compute_reliability(0.9, 1.0, True, True)
        self.assertAlmostEqual(score, 96.0)

    def test_index_and_determinism_failures_drop_score(self):
        # 0.40*0.9 + 0.30*1.0 + 0 + 0 = 0.66 -> 66.0
        score = CLI.compute_reliability(0.9, 1.0, False, False)
        self.assertAlmostEqual(score, 66.0)

    def test_none_passrate_renormalizes(self):
        # tests dropped: weights 0.40+0.15+0.15=0.70; value 0.40*0.9+0.15+0.15
        # = 0.36+0.30 = 0.66; 0.66/0.70 = 0.942857... -> 94.29
        score = CLI.compute_reliability(0.9, None, True, True)
        self.assertAlmostEqual(score, 94.29)

    def test_clamps_into_range(self):
        self.assertEqual(CLI.compute_reliability(5.0, 5.0, True, True), 100.0)
        self.assertEqual(CLI.compute_reliability(-1.0, -1.0, False, False), 0.0)


class BuildRowTest(SyntheticKBTestCase):
    """build_ledger_row is pure + deterministic for fixed volatile inputs."""

    def _checks(self, **over):
        c = {"test_pass_rate": 1.0, "tests_ran": 40, "tests_failed": 0,
             "index_validate_ok": True, "determinism_ok": True}
        c.update(over)
        return c

    def test_eval_mode_row_schema(self):
        result = {"strategy": "bm25", "top_k": 5, "num_queries": 3,
                  "gold_path": "/x/gold.json", "metrics": dict(_METRICS)}
        row = CLI.build_ledger_row(result, _GIT, "2026-06-24T12:00:00Z",
                                   self._checks())
        for key in ("schema", "run", "commit", "committed", "subject", "dirty",
                    "mode", "strategy", "top_k", "num_queries", "gold_path",
                    "metrics", "ablation", "checks", "reliability"):
            self.assertIn(key, row)
        self.assertEqual(row["schema"], CLI.LEDGER_SCHEMA)
        self.assertEqual(row["commit"], "abc1234")
        self.assertEqual(row["run"], "2026-06-24T12:00:00Z")
        self.assertEqual(row["committed"], "2026-06-24T00:00:00Z")
        self.assertEqual(row["mode"], "eval")
        self.assertEqual(row["strategy"], "bm25")
        self.assertIsNone(row["ablation"])
        self.assertAlmostEqual(row["reliability"], 96.0)

    def test_ablate_mode_uses_treatment_metrics_and_records_delta(self):
        result = {
            "mode": "ablate", "top_k": 5, "num_queries": 3,
            "gold_path": "/x/gold.json", "baseline": "tag", "treatment": "bm25",
            "strategies": {"tag": {"recall_at_k": 0.5}, "bm25": dict(_METRICS)},
            "delta": {"recall_at_k": 0.40},
        }
        row = CLI.build_ledger_row(result, _GIT, "2026-06-24T12:00:00Z",
                                   self._checks())
        self.assertEqual(row["mode"], "ablate")
        self.assertEqual(row["strategy"], "bm25")          # treatment is headline
        self.assertEqual(row["metrics"]["recall_at_k"], 0.90)
        self.assertEqual(row["ablation"]["baseline"], "tag")
        self.assertEqual(row["ablation"]["delta"]["recall_at_k"], 0.40)

    def test_corpus_size_passes_through_both_builders(self):
        # Task 10: corpus_size is an additive seam mirroring corpus_fingerprint —
        # both row builders surface result["corpus_size"] so /api/scaling can plot
        # Recall@k vs corpus size; legacy rows lacking it report None (N unknown).
        eval_result = {"strategy": "bm25", "top_k": 5, "num_queries": 3,
                       "gold_path": "/x/gold.json", "corpus_size": 42,
                       "metrics": dict(_METRICS)}
        eval_row = CLI.build_ledger_row(eval_result, _GIT,
                                        "2026-06-24T12:00:00Z", self._checks())
        self.assertEqual(eval_row["corpus_size"], 42)

        acr_result = {
            "top_k": 5, "num_queries": 3, "gold_path": "/x/gold.json",
            "corpus_size": 7, "baseline": "bm25", "treatment": "bm25+acr",
            "strategies": {"bm25": {"recall_at_k": 0.5},
                           "bm25+acr": dict(_METRICS)},
            "delta": {"recall_at_k": 0.40},
        }
        acr_row = CLI.build_acr_gate_row(acr_result, {"passed": True}, _GIT,
                                         "2026-06-24T12:00:00Z", self._checks())
        self.assertEqual(acr_row["corpus_size"], 7)

        # Legacy result (no corpus_size key) → None, not a KeyError.
        legacy = CLI.build_ledger_row(
            {"strategy": "bm25", "metrics": dict(_METRICS)}, _GIT,
            "2026-06-24T12:00:00Z", self._checks())
        self.assertIsNone(legacy["corpus_size"])

    def test_row_serializes_byte_identically_for_fixed_inputs(self):
        result = {"strategy": "bm25", "top_k": 5, "num_queries": 3,
                  "gold_path": "/x/gold.json", "metrics": dict(_METRICS)}
        a = CLI.build_ledger_row(result, _GIT, "2026-06-24T12:00:00Z",
                                 self._checks())
        b = CLI.build_ledger_row(result, _GIT, "2026-06-24T12:00:00Z",
                                 self._checks())
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))


class AppendOnlyTest(SyntheticKBTestCase):
    """append_ledger_row only ever grows the file; old rows are immutable."""

    def test_second_append_leaves_first_row_byte_identical(self):
        path = os.path.join(self.kdir, "benchmarks", "history.jsonl")
        row1 = {"commit": "aaa", "run": "2026-06-24T10:00:00Z", "metrics": {}}
        row2 = {"commit": "bbb", "run": "2026-06-24T11:00:00Z", "metrics": {}}

        CLI.append_ledger_row(path, row1)
        with open(path, "rb") as f:
            after_first = f.read()
        self.assertEqual(after_first.count(b"\n"), 1)

        CLI.append_ledger_row(path, row2)
        with open(path, "rb") as f:
            after_second = f.read()
        # Strictly grew, and the original bytes are an exact prefix (immutable).
        self.assertTrue(after_second.startswith(after_first))
        self.assertGreater(len(after_second), len(after_first))
        self.assertEqual(after_second.count(b"\n"), 2)

        lines = after_second.decode("utf-8").splitlines()
        self.assertEqual(json.loads(lines[0])["commit"], "aaa")
        self.assertEqual(json.loads(lines[1])["commit"], "bbb")

    def test_creates_parent_dir(self):
        path = os.path.join(self.kdir, "benchmarks", "history.jsonl")
        self.assertFalse(os.path.exists(os.path.dirname(path)))
        CLI.append_ledger_row(path, {"commit": "x", "run": "t"})
        self.assertTrue(os.path.isfile(path))


class RecordEndToEndTest(SyntheticKBTestCase):
    """A real `eval --record` over the synthetic KB writes a valid ledger line."""

    def setUp(self):
        super().setUp()
        # Avoid the nested-unittest subprocess: pretend we're inside the suite,
        # so --record records test_pass_rate as not-measured (None) instead of
        # re-spawning `python3 -m unittest` recursively.
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

    def test_record_appends_full_schema_row(self):
        gold = self._write_gold([
            {"query": "geofence reminders never fired arrive",
             "expected_ids": ["learn-geofence-reminders"]},
            {"query": "macos has no timeout command in shell",
             "expected_ids": ["learn-macos-no-timeout"]},
        ])
        code, out, err = self.run_cli(
            ["eval", "--gold", gold, "--strategy", "bm25", "--record",
             "--format", "json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertIn("recorded", payload)

        ledger = os.path.join(self.kdir, "benchmarks", "history.jsonl")
        self.assertTrue(os.path.isfile(ledger))
        with open(ledger, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        # Full schema incl. commit + dates.
        for key in ("schema", "run", "commit", "committed", "subject", "dirty",
                    "mode", "strategy", "metrics", "ablation", "checks",
                    "reliability"):
            self.assertIn(key, row)
        self.assertEqual(row["schema"], CLI.LEDGER_SCHEMA)
        self.assertTrue(row["run"].endswith("Z"))
        self.assertEqual(row["mode"], "eval")
        self.assertEqual(row["strategy"], "bm25")
        # Nested-guard path: tests not measured, but index + determinism scored.
        self.assertIsNone(row["checks"]["test_pass_rate"])
        self.assertTrue(row["checks"]["index_validate_ok"])
        self.assertTrue(row["checks"]["determinism_ok"])
        self.assertIsInstance(row["reliability"], (int, float))
        self.assertGreaterEqual(row["reliability"], 0.0)
        self.assertLessEqual(row["reliability"], 100.0)
        # Task 10: a newly recorded row carries a numeric corpus_size = the count
        # of scored corpus entries (> 0 for the synthetic KB), so /api/scaling's
        # slope becomes meaningful on live rows.
        self.assertIn("corpus_size", row)
        self.assertIsInstance(row["corpus_size"], int)
        self.assertGreater(row["corpus_size"], 0)

    def test_second_record_appends_without_mutating_first(self):
        gold = self._write_gold([
            {"query": "bm25 deterministic ranking lexical",
             "expected_ids": ["ref-bm25-ranking"]},
        ])
        argv = ["eval", "--gold", gold, "--strategy", "bm25", "--record",
                "--format", "json"]
        self.assertEqual(self.run_cli(argv)[0], 0)
        ledger = os.path.join(self.kdir, "benchmarks", "history.jsonl")
        with open(ledger, "rb") as f:
            after_first = f.read()
        self.assertEqual(self.run_cli(argv)[0], 0)
        with open(ledger, "rb") as f:
            after_second = f.read()
        # Append-only: first run's bytes are an exact, unchanged prefix.
        self.assertTrue(after_second.startswith(after_first))
        self.assertEqual(after_second.count(b"\n"), 2)

    def test_ablate_record_captures_delta(self):
        gold = self._write_gold([
            {"query": "geofence reminders never fired arrive",
             "expected_ids": ["learn-geofence-reminders"]},
        ])
        code, out, err = self.run_cli(
            ["eval", "--gold", gold, "--ablate", "--record", "--format", "json"])
        self.assertEqual(code, 0, err)
        ledger = os.path.join(self.kdir, "benchmarks", "history.jsonl")
        with open(ledger, encoding="utf-8") as f:
            row = json.loads(f.read().splitlines()[0])
        self.assertEqual(row["mode"], "ablate")
        self.assertIsNotNone(row["ablation"])
        self.assertIn("delta", row["ablation"])
