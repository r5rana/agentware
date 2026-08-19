"""Tests for the `eval` engine + ranking metric math (Phase 3.1).

Two layers:
  1. Pure metric math (recall@k, precision@k, nDCG@k, MRR) against hand-computed
     values — exact, no I/O.
  2. The `tag` baseline strategy end-to-end via the CLI over a synthetic KB +
     synthetic gold set, asserting the reported aggregate metrics.

Stdlib `unittest` only (no pytest, no new deps). Never touches the real KB.
"""

import json
import math
import os

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli, run_cli
except ImportError:  # allow `python3 -m unittest tests.unit.retrieval.test_eval`
    from _fixtures import SyntheticKBTestCase, load_cli, run_cli


CLI = load_cli()


class MetricMathTest(SyntheticKBTestCase):
    """Hand-computed ground truth for each metric function."""

    def test_recall_precision_ndcg_mrr_case_a(self):
        ranked = ["a", "b", "c", "d", "e"]
        relevant = {"a", "c"}
        k = 3
        # recall@3 = |{a,c}| / 2 = 1.0
        self.assertAlmostEqual(CLI.recall_at_k(ranked, relevant, k), 1.0)
        # precision@3 = 2 hits / 3 = 0.6667
        self.assertAlmostEqual(CLI.precision_at_k(ranked, relevant, k), 2.0 / 3.0)
        # dcg = 1/log2(2) + 1/log2(4) = 1.0 + 0.5 = 1.5
        # idcg = 1/log2(2) + 1/log2(3) = 1.0 + 0.6309297536 = 1.6309297536
        expected_ndcg = 1.5 / (1.0 + 1.0 / math.log2(3))
        self.assertAlmostEqual(CLI.ndcg_at_k(ranked, relevant, k), expected_ndcg)
        # first relevant ('a') at position 1 -> rr = 1.0
        self.assertAlmostEqual(CLI.reciprocal_rank(ranked, relevant), 1.0)

    def test_recall_precision_ndcg_mrr_case_b(self):
        ranked = ["x", "a", "y"]
        relevant = {"a"}
        k = 2
        self.assertAlmostEqual(CLI.recall_at_k(ranked, relevant, k), 1.0)
        self.assertAlmostEqual(CLI.precision_at_k(ranked, relevant, k), 0.5)
        # dcg = 0 + 1/log2(3); idcg = 1/log2(2) = 1.0
        self.assertAlmostEqual(CLI.ndcg_at_k(ranked, relevant, k), 1.0 / math.log2(3))
        # first relevant at position 2 -> rr = 0.5
        self.assertAlmostEqual(CLI.reciprocal_rank(ranked, relevant), 0.5)

    def test_no_relevant_or_miss(self):
        ranked = ["a", "b", "c"]
        self.assertEqual(CLI.recall_at_k(ranked, set(), 3), 0.0)
        self.assertEqual(CLI.ndcg_at_k(ranked, set(), 3), 0.0)
        self.assertEqual(CLI.reciprocal_rank(ranked, {"z"}), 0.0)
        self.assertEqual(CLI.recall_at_k(ranked, {"z"}, 3), 0.0)
        self.assertEqual(CLI.precision_at_k(ranked, {"a"}, 0), 0.0)

    def test_tokenizer_is_lowercase_alnum_split(self):
        self.assertEqual(CLI.tokenize("Geofence, iOS! reminders"),
                         ["geofence", "ios", "reminders"])

    def test_median_p50(self):
        self.assertEqual(CLI._median([3, 1, 2]), 2.0)        # odd -> middle
        self.assertEqual(CLI._median([1, 2, 3, 4]), 2.5)     # even -> mean of two
        self.assertEqual(CLI._median([]), 0.0)


class EvalCliTest(SyntheticKBTestCase):
    """End-to-end `eval --strategy tag` over the synthetic KB + gold set."""

    def _write_gold(self, rows):
        path = os.path.join(self.kdir, "recall-gold.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f)
        return path

    def test_tag_strategy_perfect_recall(self):
        gold = self._write_gold([
            # both tokens are exact tags of the geofence entry -> ranked first
            {"query": "geofence ios reminders",
             "expected_ids": ["learn-geofence-reminders"]},
            {"query": "bm25 ranking retrieval",
             "expected_ids": ["ref-bm25-ranking"]},
        ])
        code, out, err = self.run_cli(
            ["eval", "--gold", gold, "--strategy", "tag",
             "--top-k", "5", "--format", "json"])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["strategy"], "tag")
        self.assertEqual(result["num_queries"], 2)
        self.assertAlmostEqual(result["metrics"]["recall_at_k"], 1.0)
        self.assertAlmostEqual(result["metrics"]["mrr"], 1.0)
        # the top-ranked id for query 1 is the geofence learning
        self.assertEqual(result["per_query"][0]["ranked"][0],
                         "learn-geofence-reminders")
        # footprint is a positive token estimate (read-only body inclusion)
        self.assertGreater(result["metrics"]["context_tokens_mean"], 0)

    def test_tag_strategy_miss_scores_zero(self):
        gold = self._write_gold([
            # 'kangaroo' is not a tag of any entry -> empty ranking -> recall 0
            {"query": "kangaroo", "expected_ids": ["learn-geofence-reminders"]},
        ])
        code, out, err = self.run_cli(
            ["eval", "--gold", gold, "--strategy", "tag", "--format", "json"])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertAlmostEqual(result["metrics"]["recall_at_k"], 0.0)
        self.assertEqual(result["per_query"][0]["ranked"], [])

    def test_ranking_is_deterministic_across_runs(self):
        gold = self._write_gold([
            {"query": "python stdlib runtime",
             "expected_ids": ["config-python-runtime"]},
        ])
        argv = ["eval", "--gold", gold, "--strategy", "tag", "--format", "json"]
        r1 = json.loads(self.run_cli(argv)[1])["per_query"][0]["ranked"]
        r2 = json.loads(self.run_cli(argv)[1])["per_query"][0]["ranked"]
        self.assertEqual(r1, r2)  # ranking deterministic (latency excluded)

    def test_bm25_strategy_scores_the_gold_set(self):
        # Phase 1 landed retrieve_bm25, so --strategy bm25 now runs (no exit 2).
        gold = self._write_gold([
            {"query": "geofence arrive reminders",
             "expected_ids": ["learn-geofence-reminders"]},
        ])
        code, out, err = self.run_cli(
            ["eval", "--gold", gold, "--strategy", "bm25", "--format", "json"])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["strategy"], "bm25")
        self.assertAlmostEqual(result["metrics"]["recall_at_k"], 1.0)
        self.assertEqual(result["per_query"][0]["ranked"][0],
                         "learn-geofence-reminders")

    def test_missing_gold_set_errors(self):
        code, out, err = self.run_cli(
            ["eval", "--gold", os.path.join(self.kdir, "nope.json")])
        self.assertEqual(code, 1)
        self.assertIn("gold set not found", err)


class EvalAblateTest(SyntheticKBTestCase):
    """`eval --ablate` reports both columns + the BM25-vs-tag per-metric lift."""

    def _write_gold(self, rows):
        path = os.path.join(self.kdir, "recall-gold.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f)
        return path

    def test_ablate_json_schema(self):
        gold = self._write_gold([
            {"query": "geofence ios reminders",
             "expected_ids": ["learn-geofence-reminders"]},
        ])
        code, out, err = self.run_cli(
            ["eval", "--gold", gold, "--ablate", "--format", "json"])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["mode"], "ablate")
        self.assertEqual(result["baseline"], "tag")
        self.assertEqual(result["treatment"], "bm25")
        self.assertIn("tag", result["strategies"])
        self.assertIn("bm25", result["strategies"])
        self.assertIn("delta", result)
        # delta == treatment - baseline for every headline metric
        for key in ("recall_at_k", "precision_at_k", "ndcg_at_k", "mrr"):
            expected = (result["strategies"]["bm25"][key]
                        - result["strategies"]["tag"][key])
            self.assertAlmostEqual(result["delta"][key], expected)

    def test_ablate_bm25_at_least_matches_tag(self):
        # A free-text query whose terms live in the geofence entry body but are
        # NOT exact tags: BM25 finds it, the exact-tag baseline cannot -> lift > 0.
        gold = self._write_gold([
            {"query": "arrive at location reminder did not fire",
             "expected_ids": ["learn-geofence-reminders"]},
        ])
        result = json.loads(self.run_cli(
            ["eval", "--gold", gold, "--ablate", "--format", "json"])[1])
        bm25 = result["strategies"]["bm25"]["recall_at_k"]
        tag = result["strategies"]["tag"]["recall_at_k"]
        self.assertGreaterEqual(bm25, tag)  # BM25 never worse than tag-only

    def test_ablate_ranking_is_deterministic(self):
        gold = self._write_gold([
            {"query": "python stdlib runtime",
             "expected_ids": ["config-python-runtime"]},
        ])
        argv = ["eval", "--gold", gold, "--ablate", "--format", "json"]
        r1 = json.loads(self.run_cli(argv)[1])
        r2 = json.loads(self.run_cli(argv)[1])
        # ranking (not latency) is byte-identical across runs (INV-1)
        self.assertEqual(r1["per_query"]["bm25"][0]["ranked"],
                         r2["per_query"]["bm25"][0]["ranked"])
        self.assertEqual(r1["per_query"]["tag"][0]["ranked"],
                         r2["per_query"]["tag"][0]["ranked"])

    def test_ablate_text_mode_runs(self):
        gold = self._write_gold([
            {"query": "geofence ios reminders",
             "expected_ids": ["learn-geofence-reminders"]},
        ])
        code, out, err = self.run_cli(["eval", "--gold", gold, "--ablate"])
        self.assertEqual(code, 0, err)
        self.assertIn("ablate", out)
        self.assertIn("delta", out)


class AnswerAgeHelperTest(SyntheticKBTestCase):
    """Pure date-math helpers that drive the OLD vs RECENT stratum (Phase 3.2)."""

    def test_date_age_days_hand_computed(self):
        import datetime
        # 2026-01-02 -> 2026-06-25 is a fixed, hand-checkable span.
        expected = (datetime.date(2026, 6, 25) - datetime.date(2026, 1, 2)).days
        self.assertEqual(CLI.date_age_days("2026-01-02", "2026-06-25"), expected)

    def test_date_age_days_future_clamps_to_zero(self):
        # answer dated AFTER as_of -> age clamps to 0 (never negative).
        self.assertEqual(CLI.date_age_days("2026-07-01", "2026-06-25"), 0)

    def test_date_age_days_invalid_is_none(self):
        self.assertIsNone(CLI.date_age_days("not-a-date", "2026-06-25"))
        self.assertIsNone(CLI.date_age_days("2026-01-02", "garbage"))
        self.assertIsNone(CLI.date_age_days(None, "2026-06-25"))

    def test_answer_age_takes_oldest_present(self):
        # Two correct answers of differing age -> representative = OLDEST (max).
        entries = {
            "fresh": {"id": "fresh", "last_verified": "2026-06-01"},
            "old": {"id": "old", "last_verified": "2026-01-02"},
        }
        as_of = "2026-06-25"
        oldest = CLI.date_age_days("2026-01-02", as_of)
        self.assertEqual(
            CLI.answer_age_days(entries, {"fresh", "old"}, as_of), oldest)

    def test_answer_age_falls_back_to_created(self):
        # No last_verified -> entry_last_verified falls back to created.
        entries = {"e": {"id": "e", "created": "2026-01-02"}}
        as_of = "2026-06-25"
        self.assertEqual(
            CLI.answer_age_days(entries, {"e"}, as_of),
            CLI.date_age_days("2026-01-02", as_of))

    def test_answer_age_none_when_unresolvable(self):
        self.assertIsNone(CLI.answer_age_days({}, {"missing"}, "2026-06-25"))


class EvalAblateAcrTest(SyntheticKBTestCase):
    """`eval --ablate --baseline bm25 --treatment bm25+acr` at a PINNED as_of."""

    PINNED = "2026-06-25"

    def _write_gold(self, rows):
        path = os.path.join(self.kdir, "recall-gold.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f)
        return path

    def _ablate(self, gold):
        code, out, err = self.run_cli(
            ["eval", "--gold", gold, "--ablate",
             "--baseline", "bm25", "--treatment", "bm25+acr",
             "--as-of", self.PINNED, "--format", "json"])
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_ablate_bm25_vs_bm25acr_schema_and_delta(self):
        gold = self._write_gold([
            {"query": "geofence arrive reminders",
             "expected_ids": ["learn-geofence-reminders"]},
            {"query": "bm25 ranking retrieval",
             "expected_ids": ["ref-bm25-ranking"]},
        ])
        result = self._ablate(gold)
        self.assertEqual(result["mode"], "ablate")
        self.assertEqual(result["baseline"], "bm25")
        self.assertEqual(result["treatment"], "bm25+acr")
        self.assertEqual(result["as_of"], self.PINNED)
        self.assertIn("bm25", result["strategies"])
        self.assertIn("bm25+acr", result["strategies"])
        # delta == treatment - baseline for EVERY headline metric (hand math).
        for key in ("recall_at_k", "precision_at_k", "ndcg_at_k", "mrr"):
            expected = (result["strategies"]["bm25+acr"][key]
                        - result["strategies"]["bm25"][key])
            self.assertAlmostEqual(result["delta"][key], expected)
        # per-query nDCG for BOTH strategies (paired t-test needs the columns).
        self.assertIn("bm25", result["per_query"])
        self.assertIn("bm25+acr", result["per_query"])
        for strat in ("bm25", "bm25+acr"):
            for row in result["per_query"][strat]:
                self.assertIn("ndcg_at_k", row)

    def test_per_query_carries_answer_age(self):
        gold = self._write_gold([
            {"query": "geofence arrive reminders",
             "expected_ids": ["learn-geofence-reminders"]},
        ])
        result = self._ablate(gold)
        # geofence entry created 2026-01-02 (no last_verified -> fallback).
        expected_age = CLI.date_age_days("2026-01-02", self.PINNED)
        for strat in ("bm25", "bm25+acr"):
            row = result["per_query"][strat][0]
            self.assertEqual(row["answer_age_days"], expected_age)

    def test_acr_keeps_same_surfaced_set_as_bm25(self):
        # ACR multiplier is bounded (0,1] -> reorders within the BM25 set,
        # never adds a zero-relevance entry (recall must not drop).
        gold = self._write_gold([
            {"query": "geofence arrive reminders",
             "expected_ids": ["learn-geofence-reminders"]},
        ])
        result = self._ablate(gold)
        self.assertGreaterEqual(
            result["strategies"]["bm25+acr"]["recall_at_k"],
            result["strategies"]["bm25"]["recall_at_k"])

    def test_ablate_bm25acr_is_deterministic_at_fixed_as_of(self):
        gold = self._write_gold([
            {"query": "python stdlib runtime",
             "expected_ids": ["config-python-runtime"]},
        ])
        argv = ["eval", "--gold", gold, "--ablate",
                "--baseline", "bm25", "--treatment", "bm25+acr",
                "--as-of", self.PINNED, "--format", "json"]
        r1 = json.loads(self.run_cli(argv)[1])
        r2 = json.loads(self.run_cli(argv)[1])
        self.assertEqual(r1["per_query"]["bm25+acr"][0]["ranked"],
                         r2["per_query"]["bm25+acr"][0]["ranked"])
        self.assertEqual(r1["strategies"]["bm25+acr"]["ndcg_at_k"],
                         r2["strategies"]["bm25+acr"]["ndcg_at_k"])

    def test_baseline_equal_treatment_errors(self):
        gold = self._write_gold([
            {"query": "geofence", "expected_ids": ["learn-geofence-reminders"]},
        ])
        code, out, err = self.run_cli(
            ["eval", "--gold", gold, "--ablate",
             "--baseline", "bm25", "--treatment", "bm25", "--format", "json"])
        self.assertEqual(code, 1)
        self.assertIn("must differ", err)

    def test_default_ablate_is_still_tag_vs_bm25(self):
        # No --baseline/--treatment -> the original tag-vs-bm25 ablation (no
        # regression of the shipped default behaviour).
        gold = self._write_gold([
            {"query": "geofence ios reminders",
             "expected_ids": ["learn-geofence-reminders"]},
        ])
        result = json.loads(self.run_cli(
            ["eval", "--gold", gold, "--ablate", "--format", "json"])[1])
        self.assertEqual(result["baseline"], "tag")
        self.assertEqual(result["treatment"], "bm25")


if __name__ == "__main__":
    import unittest
    unittest.main()
