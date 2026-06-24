"""Tests for the benchmark scorecard (Phase 3B.3).

`bench scorecard` regenerates `<kdir>/benchmarks/SCORECARD.md` as a derived,
human-readable VIEW over the append-only ledger (`history.jsonl`). These tests
cover two layers:

  1. Pure rendering — `render_scorecard(rows)` is deterministic given its rows
     (no wall-clock, no git): empty ledger, newest-run-first ordering, the
     headline columns, and the latest ablation-delta block.
  2. End-to-end CLI — `bench scorecard` reads a synthetic ledger and writes a
     valid Markdown trend table; re-running over the same ledger is idempotent
     (byte-identical) and never mutates the ledger itself.

Stdlib `unittest` only (no pytest, no new deps). Never touches the real KB.
"""

import os

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # allow `python3 -m unittest tests.test_scorecard`
    from _fixtures import SyntheticKBTestCase, load_cli


CLI = load_cli()


def _row(commit, run, strategy="bm25", mode="eval", recall=0.9, ndcg=0.85,
         mrr=0.8, p50=1.1, ctx=512.0, reliability=96.0, ablation=None):
    return {
        "schema": CLI.LEDGER_SCHEMA,
        "run": run,
        "commit": commit,
        "committed": "2026-06-24T00:00:00Z",
        "subject": "s",
        "dirty": False,
        "mode": mode,
        "strategy": strategy,
        "top_k": 5,
        "num_queries": 3,
        "gold_path": "/x/gold.json",
        "metrics": {
            "recall_at_k": recall, "precision_at_k": 0.2, "ndcg_at_k": ndcg,
            "mrr": mrr, "latency_ms_mean": 1.2, "latency_ms_p50": p50,
            "context_tokens_mean": ctx,
        },
        "ablation": ablation,
        "checks": {},
        "reliability": reliability,
    }


class RenderScorecardTest(SyntheticKBTestCase):
    """render_scorecard is pure + deterministic given its rows."""

    def test_empty_ledger_renders_placeholder(self):
        text = CLI.render_scorecard([])
        self.assertIn("# agentware Recall / Benchmark Scorecard", text)
        self.assertIn("_No benchmark runs recorded yet._", text)
        # Identical inputs -> byte-identical output.
        self.assertEqual(text, CLI.render_scorecard([]))

    def test_newest_run_first_and_has_columns(self):
        rows = [
            _row("aaa111", "2026-06-24T10:00:00Z", recall=0.65),
            _row("bbb222", "2026-06-24T12:00:00Z", recall=0.98),
        ]
        text = CLI.render_scorecard(rows)
        # Header columns present.
        for col in ("Run (UTC)", "Commit", "Recall@5", "nDCG@5", "MRR",
                    "p50 ms", "ctx-tok", "Reliability"):
            self.assertIn(col, text)
        # Newest run (12:00 / bbb222) appears before the older one.
        self.assertLess(text.index("bbb222"), text.index("aaa111"))
        # Metric values rendered.
        self.assertIn("0.9800", text)
        self.assertIn("0.6500", text)

    def test_deterministic_byte_identical(self):
        rows = [_row("aaa111", "2026-06-24T10:00:00Z")]
        self.assertEqual(CLI.render_scorecard(rows), CLI.render_scorecard(rows))

    def test_latest_ablation_delta_block(self):
        ablation = {"baseline": "tag", "treatment": "bm25",
                    "delta": {"recall_at_k": 0.33, "precision_at_k": 0.01,
                              "ndcg_at_k": 0.37, "mrr": 0.37}}
        rows = [
            _row("old000", "2026-06-24T09:00:00Z", mode="eval"),
            _row("new999", "2026-06-24T13:00:00Z", mode="ablate",
                 ablation=ablation),
        ]
        text = CLI.render_scorecard(rows)
        self.assertIn("Latest ablation delta (bm25 vs tag)", text)
        self.assertIn("new999", text)
        self.assertIn("+0.3300", text)  # signed delta

    def test_missing_metrics_render_as_dash(self):
        row = _row("ccc333", "2026-06-24T10:00:00Z")
        row["metrics"] = {}            # no metric keys
        row["reliability"] = None
        text = CLI.render_scorecard([row])
        self.assertIn("—", text)       # em-dash for missing numerics


class BenchScorecardCliTest(SyntheticKBTestCase):
    """`bench scorecard` reads the ledger and writes SCORECARD.md."""

    def _write_ledger(self, rows):
        path = os.path.join(self.kdir, "benchmarks", "history.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import json
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        return path

    def test_writes_scorecard_from_ledger(self):
        self._write_ledger([
            _row("aaa111", "2026-06-24T10:00:00Z", recall=0.65),
            _row("bbb222", "2026-06-24T12:00:00Z", recall=0.98),
        ])
        code, out, err = self.run_cli(["bench", "scorecard"])
        self.assertEqual(code, 0, err)
        self.assertIn("SCORECARD.md", out)
        sc = os.path.join(self.kdir, "benchmarks", "SCORECARD.md")
        self.assertTrue(os.path.isfile(sc))
        with open(sc, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Recall@5", text)
        self.assertLess(text.index("bbb222"), text.index("aaa111"))

    def test_empty_ledger_renders_placeholder_file(self):
        self._write_ledger([])
        code, _, err = self.run_cli(["bench", "scorecard"])
        self.assertEqual(code, 0, err)
        sc = os.path.join(self.kdir, "benchmarks", "SCORECARD.md")
        with open(sc, encoding="utf-8") as f:
            self.assertIn("_No benchmark runs recorded yet._", f.read())

    def test_regeneration_is_idempotent_and_ledger_unchanged(self):
        ledger = self._write_ledger([
            _row("aaa111", "2026-06-24T10:00:00Z"),
        ])
        with open(ledger, "rb") as f:
            ledger_before = f.read()
        self.assertEqual(self.run_cli(["bench", "scorecard"])[0], 0)
        sc = os.path.join(self.kdir, "benchmarks", "SCORECARD.md")
        with open(sc, "rb") as f:
            first = f.read()
        self.assertEqual(self.run_cli(["bench", "scorecard"])[0], 0)
        with open(sc, "rb") as f:
            second = f.read()
        # Idempotent rendering; ledger never mutated by the view regen.
        self.assertEqual(first, second)
        with open(ledger, "rb") as f:
            self.assertEqual(f.read(), ledger_before)
