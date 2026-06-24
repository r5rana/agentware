"""Tests for the deterministic BM25 recall engine (Phase 1.1).

Exercises `retrieve_bm25` / `bm25_scores` directly against the synthetic KB:
  - a query whose terms appear only in entry X ranks X first;
  - scoring is deterministic (byte-identical) across repeated runs;
  - identical-score ties break deterministically (created desc -> id asc);
  - zero-overlap docs are excluded (score 0.0);
  - the `bm25` strategy dispatches through `retrieve()`.

Stdlib `unittest` only (no pytest, no new deps). Never touches the real KB.
"""

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli, build_synthetic_kb
except ImportError:  # allow `python3 -m unittest tests.test_recall`
    from _fixtures import SyntheticKBTestCase, load_cli, build_synthetic_kb


CLI = load_cli()


class Bm25RankingTest(SyntheticKBTestCase):
    """BM25 ranking behavior over the synthetic KB."""

    def test_distinctive_query_ranks_owning_entry_first(self):
        # 'geofence arrive' vocabulary lives only in the geofence learning.
        ranked = CLI.retrieve_bm25(self.kdir, self.index_data,
                                   "geofence arrive reminders")
        self.assertEqual(ranked[0], "learn-geofence-reminders")

    def test_timeout_query_ranks_macos_entry_first(self):
        ranked = CLI.retrieve_bm25(self.kdir, self.index_data,
                                   "gtimeout coreutils command")
        self.assertEqual(ranked[0], "learn-macos-no-timeout")

    def test_bm25_query_ranks_reference_first(self):
        ranked = CLI.retrieve_bm25(self.kdir, self.index_data,
                                   "saturation inverse document frequency")
        self.assertEqual(ranked[0], "ref-bm25-ranking")

    def test_zero_overlap_query_returns_nothing(self):
        # 'kangaroo' appears in no entry -> no positive score -> empty ranking.
        ranked = CLI.retrieve_bm25(self.kdir, self.index_data, "kangaroo")
        self.assertEqual(ranked, [])

    def test_ranking_is_deterministic_across_runs(self):
        r1 = CLI.retrieve_bm25(self.kdir, self.index_data, "python stdlib ranking")
        r2 = CLI.retrieve_bm25(self.kdir, self.index_data, "python stdlib ranking")
        self.assertEqual(r1, r2)

    def test_scores_are_byte_identical_across_runs(self):
        corpus = CLI.build_corpus(self.kdir, self.index_data)
        toks = CLI.tokenize("python runtime dependency")
        s1 = [(e.get("id"), s) for (e, s) in CLI.bm25_scores(corpus, toks)]
        s2 = [(e.get("id"), s) for (e, s) in CLI.bm25_scores(corpus, toks)]
        self.assertEqual(s1, s2)

    def test_top_k_truncates(self):
        ranked_all = CLI.retrieve_bm25(self.kdir, self.index_data, "python ranking")
        ranked_1 = CLI.retrieve_bm25(self.kdir, self.index_data,
                                     "python ranking", top_k=1)
        self.assertLessEqual(len(ranked_1), 1)
        if ranked_all:
            self.assertEqual(ranked_1, ranked_all[:1])

    def test_dispatch_via_retrieve_uses_bm25(self):
        ranked = CLI.retrieve(self.kdir, self.index_data,
                              "geofence arrive reminders", "bm25")
        self.assertEqual(ranked[0], "learn-geofence-reminders")


class Bm25TieBreakTest(SyntheticKBTestCase):
    """Identical BM25 scores break deterministically: created desc -> id asc."""

    def setUp(self):
        # Two entries with IDENTICAL body vocabulary so a shared query term gives
        # them the SAME BM25 score; only created/id break the tie.
        body = (
            "# Tie\n\nalpha beta gamma alpha beta gamma identical vocabulary "
            "for a deterministic tie-break across both entries.\n"
        )
        entries = [
            {
                "id": "zzz-newer",          # later id, but NEWER created -> wins
                "title": "Tie A",
                "category": "references",
                "path": "references/tie-a.md",
                "tags": ["tie"],
                "created": "2026-02-02",
                "summary": "alpha beta gamma identical",
                "body": body,
            },
            {
                "id": "aaa-older",          # earlier id, but OLDER created -> loses
                "title": "Tie B",
                "category": "references",
                "path": "references/tie-b.md",
                "tags": ["tie"],
                "created": "2026-01-01",
                "summary": "alpha beta gamma identical",
                "body": body,
            },
        ]
        import tempfile, shutil
        self.kdir = tempfile.mkdtemp(prefix="agentware-test-tie-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.index_data = build_synthetic_kb(self.kdir, entries=entries)

    def test_identical_scores_break_by_created_then_id(self):
        ranked = CLI.retrieve_bm25(self.kdir, self.index_data, "alpha beta gamma")
        # Equal scores -> created desc wins: 2026-02-02 (zzz-newer) before older.
        self.assertEqual(ranked, ["zzz-newer", "aaa-older"])


class RecallCommandTest(SyntheticKBTestCase):
    """The `recall` command: ranking, token budgeting, and the JSON schema (1.2)."""

    def test_json_schema_and_top_ranked_entry(self):
        import json
        code, out, err = self.run_cli(
            ["recall", "geofence arrive reminders", "--format", "json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        # Stable top-level schema for the loop to consume.
        for key in ("query", "strategy", "top_k", "token_budget", "category",
                    "context_tokens", "count", "results"):
            self.assertIn(key, payload)
        self.assertEqual(payload["strategy"], "bm25")
        self.assertEqual(payload["count"], len(payload["results"]))
        self.assertTrue(payload["results"])
        first = payload["results"][0]
        for key in ("id", "path", "category", "score", "summary",
                    "estimated_tokens"):
            self.assertIn(key, first)
        # The geofence learning owns this vocabulary -> ranks first.
        self.assertEqual(first["id"], "learn-geofence-reminders")

    def test_budget_zero_returns_nothing(self):
        import json
        code, out, err = self.run_cli(
            ["recall", "geofence reminders", "--token-budget", "0",
             "--format", "json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["context_tokens"], 0)

    def test_small_budget_truncates_to_fewer_entries(self):
        import json
        full = json.loads(self.run_cli(
            ["recall", "python ranking stdlib", "--token-budget", "100000",
             "--format", "json"])[1])
        # A tiny budget must return strictly fewer (or equal) entries, never more,
        # and must respect the cumulative budget.
        small = json.loads(self.run_cli(
            ["recall", "python ranking stdlib", "--token-budget", "60",
             "--format", "json"])[1])
        self.assertLessEqual(small["count"], full["count"])
        self.assertLessEqual(small["context_tokens"], 60)

    def test_budget_is_cumulative_and_in_rank_order(self):
        import json
        payload = json.loads(self.run_cli(
            ["recall", "python ranking stdlib retrieval", "--token-budget", "1500",
             "--format", "json"])[1])
        # Cumulative footprint never exceeds the budget.
        self.assertLessEqual(payload["context_tokens"], 1500)
        self.assertEqual(
            payload["context_tokens"],
            sum(r["estimated_tokens"] for r in payload["results"]))
        # Scores are non-increasing (rank order preserved).
        scores = [r["score"] for r in payload["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_category_filter_restricts_results(self):
        import json
        payload = json.loads(self.run_cli(
            ["recall", "python ranking", "--category", "references",
             "--format", "json"])[1])
        self.assertTrue(payload["results"])
        self.assertTrue(all(r["category"] == "references"
                            for r in payload["results"]))

    def test_output_is_deterministic_across_runs(self):
        a = self.run_cli(["recall", "geofence reminders", "--format", "json"])[1]
        b = self.run_cli(["recall", "geofence reminders", "--format", "json"])[1]
        self.assertEqual(a, b)

    def test_text_format_runs(self):
        code, out, err = self.run_cli(["recall", "geofence reminders"])
        self.assertEqual(code, 0, err)
        self.assertIn("recall:", out)


if __name__ == "__main__":
    import unittest
    unittest.main()
