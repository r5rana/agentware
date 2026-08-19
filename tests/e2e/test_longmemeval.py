"""Tests for the `eval --suite longmemeval` public benchmark scorer (Task 1.2).

The scorer is STRATEGY-AGNOSTIC and SESSION-LEVEL. These tests pin its math
against hand-computed ground truth on a tiny synthetic LongMemEval-shaped
fixture (so they are exact, fast, and never touch the 277 MB real dataset), and
assert the three validity fixes:
  1. retrieval unit = SESSION (one corpus entry per haystack session),
  2. headline k = 5,
  3. abstention (`_abs` suffix) is EXCLUDED from the recall aggregate and
     reported SEPARATELY (never crashed on, never silently mis-scored).

Also asserts byte-identical ordering across runs (determinism, INV-1) and that a
second strategy (`bm25+acr`) scores too (proving strategy-agnosticism for the
later `bm25+embed`). Stdlib `unittest` only.
"""

import json
import math
import os
import tempfile

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # allow `python3 -m unittest tests.e2e.test_longmemeval`
    from _fixtures import SyntheticKBTestCase, load_cli


CLI = load_cli()


def _turn(role, content):
    return {"role": role, "content": content}


# A tiny LongMemEval-S-shaped dataset. Distinctive vocabulary per session so the
# correct (gold) session ranks by BM25 and distractors (no shared tokens) score
# 0 and drop out.
_DATASET = [
    {
        # Answerable, single gold session. Only s1 shares query tokens.
        "question_id": "q1",
        "question_type": "single-session-user",
        "question": "how do I move the fox chicken and grain across the river",
        "answer": "two trips with the boat",
        "answer_session_ids": ["s1"],
        "haystack_session_ids": ["s1", "s2", "s3"],
        "haystack_sessions": [
            [_turn("user", "the fox chicken and grain river boat puzzle"),
             _turn("assistant", "take the chicken across the river first")],
            [_turn("user", "python programming language code review"),
             _turn("assistant", "use argparse for the command dispatch")],
            [_turn("user", "cooking pasta recipe dinner tonight"),
             _turn("assistant", "boil water and add salt")],
        ],
    },
    {
        # Answerable, TWO gold sessions but only s4 shares query tokens; s6 (the
        # other gold) shares none -> BM25 scores it 0 -> partial recall = 1/2.
        "question_id": "q2",
        "question_type": "multi-session",
        "question": "which database postgres project",
        "answer": "postgres",
        "answer_session_ids": ["s4", "s6"],
        "haystack_session_ids": ["s4", "s5", "s6"],
        "haystack_sessions": [
            [_turn("user", "we chose postgres database for project")],
            [_turn("user", "weather today sunny warm outside")],
            [_turn("user", "migration finished without errors")],
        ],
    },
    {
        # Abstention (official `_abs` suffix): planted distractor gold; Recall@k
        # is undefined -> MUST be excluded from the aggregate, counted separately.
        "question_id": "q3_abs",
        "question_type": "temporal-reasoning",
        "question": "what time did the meeting start last tuesday",
        "answer": "no information available",
        "answer_session_ids": ["s7_abs"],
        "haystack_session_ids": ["s7_abs", "s8"],
        "haystack_sessions": [
            [_turn("user", "completely unrelated planted distractor content")],
            [_turn("user", "another irrelevant filler session here")],
        ],
    },
]


class LongMemEvalScorerTest(SyntheticKBTestCase):
    """evaluate_longmemeval math + validity fixes on the synthetic fixture."""

    def _write_dataset(self, items=None):
        path = os.path.join(self.kdir, "lme.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_DATASET if items is None else items, f)
        return path

    def test_session_text_and_corpus_builder(self):
        text = CLI.longmemeval_session_text(
            [{"role": "user", "content": "alpha"},
             "not-a-dict", {"role": "assistant", "content": "beta"},
             {"role": "user", "content": ""}])
        self.assertEqual(text, "alpha\nbeta")
        data = CLI.longmemeval_corpus_data(["a", "b"], [[], []])
        self.assertEqual([e["id"] for e in data["entries"]], ["a", "b"])

    def test_abstention_detection(self):
        self.assertTrue(CLI.is_longmemeval_abstention("q3_abs"))
        self.assertFalse(CLI.is_longmemeval_abstention("q1"))

    def test_aggregate_math_matches_hand_computed(self):
        path = self._write_dataset()
        res = CLI.evaluate_longmemeval(self.kdir, path, "bm25", 5,
                                       as_of="2026-06-25")
        # Only the two answerable questions enter the aggregate.
        self.assertEqual(res["num_queries"], 2)
        # Abstention excluded but reported separately.
        self.assertEqual(res["abstention"]["count"], 1)
        self.assertEqual(res["abstention"]["by_category"],
                         {"temporal-reasoning": 1})

        # q1: ranked=[s1] -> recall=1/1, prec=1/5, ndcg=1.0, rr=1.0
        # q2: ranked=[s4] (s6 scores 0) -> recall=1/2, prec=1/5,
        #     ndcg = (1/log2(2)) / (1/log2(2)+1/log2(3)) = 1/(1+1/log2(3)),
        #     rr=1.0
        q2_ndcg = 1.0 / (1.0 + 1.0 / math.log2(3))
        m = res["metrics"]
        self.assertAlmostEqual(m["recall_at_k"], (1.0 + 0.5) / 2.0)
        self.assertAlmostEqual(m["precision_at_k"], (0.2 + 0.2) / 2.0)
        self.assertAlmostEqual(m["ndcg_at_k"], (1.0 + q2_ndcg) / 2.0)
        self.assertAlmostEqual(m["mrr"], 1.0)
        self.assertTrue(res["determinism_ok"])

    def test_per_category_breakdown(self):
        path = self._write_dataset()
        res = CLI.evaluate_longmemeval(self.kdir, path, "bm25", 5,
                                       as_of="2026-06-25")
        pc = res["per_category"]
        self.assertEqual(pc["single-session-user"]["num_queries"], 1)
        self.assertAlmostEqual(pc["single-session-user"]["recall_at_k"], 1.0)
        self.assertEqual(pc["multi-session"]["num_queries"], 1)
        self.assertAlmostEqual(pc["multi-session"]["recall_at_k"], 0.5)
        # Abstention category never appears in the recall per-category block.
        self.assertNotIn("temporal-reasoning", pc)

    def test_byte_identical_across_runs(self):
        path = self._write_dataset()
        a = CLI.evaluate_longmemeval(self.kdir, path, "bm25", 5,
                                     as_of="2026-06-25")
        b = CLI.evaluate_longmemeval(self.kdir, path, "bm25", 5,
                                     as_of="2026-06-25")
        # Latency is the only volatile field; drop it before comparing.
        for r in (a, b):
            r["metrics"].pop("latency_ms_mean", None)
            r["metrics"].pop("latency_ms_p50", None)
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))

    def test_strategy_agnostic_bm25_acr_also_scores(self):
        path = self._write_dataset()
        base = CLI.evaluate_longmemeval(self.kdir, path, "bm25", 5,
                                        as_of="2026-06-25")
        acr = CLI.evaluate_longmemeval(self.kdir, path, "bm25+acr", 5,
                                       as_of="2026-06-25")
        # bm25+acr runs without error and surfaces the SAME set (synthetic
        # sessions carry no provenance/dates -> acr is a uniform multiplier),
        # proving the scorer is strategy-agnostic for the later bm25+embed.
        self.assertEqual(acr["strategy"], "bm25+acr")
        self.assertAlmostEqual(acr["metrics"]["recall_at_k"],
                               base["metrics"]["recall_at_k"])

    def test_abstention_only_dataset_does_not_crash(self):
        path = self._write_dataset([_DATASET[2]])
        res = CLI.evaluate_longmemeval(self.kdir, path, "bm25", 5,
                                       as_of="2026-06-25")
        self.assertEqual(res["num_queries"], 0)
        self.assertEqual(res["abstention"]["count"], 1)
        self.assertAlmostEqual(res["metrics"]["recall_at_k"], 0.0)


class LongMemEvalCLITest(SyntheticKBTestCase):
    """End-to-end via the real CLI dispatch (`eval --suite longmemeval`)."""

    def _write_dataset(self):
        path = os.path.join(self.kdir, "lme.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_DATASET, f)
        return path

    def test_cli_json_report(self):
        path = self._write_dataset()
        code, out, err = self.run_cli(
            ["eval", "--suite", "longmemeval", "--dataset", path,
             "--strategy", "bm25", "--top-k", "5", "--as-of", "2026-06-25",
             "--format", "json"])
        self.assertEqual(code, 0, err)
        res = json.loads(out)
        self.assertEqual(res["suite"], "longmemeval")
        self.assertEqual(res["num_queries"], 2)
        self.assertEqual(res["abstention"]["count"], 1)
        self.assertAlmostEqual(res["metrics"]["recall_at_k"], 0.75)

    def test_cli_missing_dataset_errors_cleanly(self):
        code, out, err = self.run_cli(
            ["eval", "--suite", "longmemeval", "--dataset",
             os.path.join(self.kdir, "nope.json")])
        self.assertEqual(code, 1)
        self.assertIn("dataset not found", err)

    def test_cli_record_appends_suite_row(self):
        path = self._write_dataset()
        code, out, err = self.run_cli(
            ["eval", "--suite", "longmemeval", "--dataset", path,
             "--strategy", "bm25", "--top-k", "5", "--as-of", "2026-06-25",
             "--record", "--format", "json"])
        self.assertEqual(code, 0, err)
        ledger = os.path.join(self.kdir, "benchmarks", "history.jsonl")
        with open(ledger, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        self.assertTrue(rows)
        last = rows[-1]
        self.assertEqual(last["suite"], "longmemeval")
        self.assertEqual(last["strategy"], "bm25")
        self.assertAlmostEqual(last["metrics"]["recall_at_k"], 0.75)


if __name__ == "__main__":
    import unittest
    unittest.main()
