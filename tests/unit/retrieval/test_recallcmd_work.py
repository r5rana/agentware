"""The `--scope work` arm of `agentware_lib.retrieval.recallcmd` — wave 9.

Part two of the owning suite for the module (see `_recallcmd_support` for why it
is split, and `test_recallcmd` for the curated half). What lives here is the arm
that was a SECOND COMMAND inlined into `cmd_recall`: ten of that function's
nineteen branches, sharing only the argument validation with the curated path
and none of its ranking, its payload shape or its renderer.

Its promises are its own:

  * EPHEMERAL. The corpus is built from `work/*.md` on demand and `work/` is
    never indexed — plans and worklogs are the operator's in-flight record, not
    curated knowledge.
  * PLAN STATE RANKS. A superseded feature's documents are downweighted, because
    BM25 alone cannot tell a superseded plan from the plan that replaced it.
  * SKIP, NEVER BREAK — the same INV-5 walk over a different record shape.
  * THINNER ON PURPOSE. No age, no trust, no staleness footer: those describe a
    curated entry's provenance, and a plan document has none to report.
"""

from tests._paths import TESTS_DIR

import json
import os
import sys
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

try:
    from tests.unit.retrieval._recallcmd_support import (
        RecallCmdTestCase, _Args, _capture, recallcmd, run_cli)
except ImportError:  # direct invocation
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    from _recallcmd_support import (  # noqa: F401
        RecallCmdTestCase, _Args, _capture, recallcmd, run_cli)

class WorkScopeRankingTest(RecallCmdTestCase):
    """`_work_scope_ranked` — BM25 over an ephemeral corpus, plan-weighted."""

    def corpus(self):
        return [
            ({"id": "live", "created": "2026-01-01", "status_mult": 1.0},
             "geofence geofence"),
            ({"id": "dead", "created": "2026-01-02", "status_mult": 0.3},
             "geofence geofence"),
        ]

    def test_recallcmd_a_superseded_feature_ranks_below_an_equal_live_one(self):
        """The whole point of the multiplier: BM25 alone cannot tell a
        superseded plan from the plan that replaced it, so it competes on equal
        terms forever."""
        ranked = recallcmd._work_scope_ranked(self.corpus(), "geofence", 0)
        self.assertEqual([e["id"] for (e, _s) in ranked], ["live", "dead"])

    def test_recallcmd_a_missing_multiplier_is_neutral(self):
        corpus = [({"id": "x", "created": "2026-01-01"}, "geofence")]
        ranked = recallcmd._work_scope_ranked(corpus, "geofence", 0)
        self.assertEqual(len(ranked), 1)

    def test_recallcmd_zero_scoring_documents_are_dropped(self):
        ranked = recallcmd._work_scope_ranked(
            self.corpus(), "nothingmatchesthisterm", 0)
        self.assertEqual(ranked, [])

    def test_recallcmd_a_positive_weighted_score_survives(self):
        """The drop happens AFTER weighting, and 0.3 of something is still
        something: a downweighted live document must not vanish."""
        corpus = [({"id": "dead", "created": "2026-01-01",
                    "status_mult": 0.3}, "geofence")]
        self.assertEqual(
            len(recallcmd._work_scope_ranked(corpus, "geofence", 0)), 1)

    def test_recallcmd_top_k_truncates_after_ranking(self):
        ranked = recallcmd._work_scope_ranked(self.corpus(), "geofence", 1)
        self.assertEqual([e["id"] for (e, _s) in ranked], ["live"])

    def test_recallcmd_top_k_zero_means_no_truncation(self):
        """`--top-k 0` is falsy, and the work arm treats it as "no limit" — the
        curated arm's own zero handling is separate and stays where it is."""
        self.assertEqual(
            len(recallcmd._work_scope_ranked(self.corpus(), "geofence", 0)), 2)

    def test_recallcmd_equal_scores_break_by_created_descending(self):
        """Newer work first: two documents that match a query equally well are
        not equally useful, and the more recent one is the live one."""
        corpus = [({"id": "b", "created": "2026-01-01", "status_mult": 1.0},
                   "geofence"),
                  ({"id": "a", "created": "2026-01-02", "status_mult": 1.0},
                   "geofence")]
        ranked = recallcmd._work_scope_ranked(corpus, "geofence", 0)
        self.assertEqual([e["id"] for (e, _s) in ranked], ["a", "b"])

    def test_recallcmd_equal_scores_and_dates_break_by_id_ascending(self):
        """THE row the id key exists for. With `created` equal it is the only
        thing left deciding, so dropping that sort leaves the order to whatever
        the corpus walk produced — deterministic-looking and arbitrary. A
        fixture whose dates differ never reaches this key at all."""
        corpus = [({"id": "b", "created": "2026-01-01", "status_mult": 1.0},
                   "geofence"),
                  ({"id": "a", "created": "2026-01-01", "status_mult": 1.0},
                   "geofence")]
        ranked = recallcmd._work_scope_ranked(corpus, "geofence", 0)
        self.assertEqual([e["id"] for (e, _s) in ranked], ["a", "b"])


class WorkScopePayloadTest(RecallCmdTestCase):
    """`_work_scope_budget_walk` / `_work_scope_payload` / the renderer."""

    def args(self, **kw):
        kw.setdefault("query", "geofence")
        kw.setdefault("top_k", 5)
        kw.setdefault("token_budget", 1500)
        kw.setdefault("format", "text")
        return _Args(**kw)

    def test_recallcmd_work_records_use_the_heading_as_their_summary(self):
        """A work document has no curated summary of its own; its heading stands
        in, and `category` is the literal "work" because it was never indexed
        under one."""
        corpus = [({"id": "f/plan", "path": "work/f/plan.md",
                    "title": "Plan Heading", "status_mult": 1.0}, "geofence")]
        scored = [(corpus[0][0], 1.0)]
        results, _o, _u = recallcmd._work_scope_budget_walk(scored, corpus, 999)
        self.assertEqual(results[0]["summary"], "Plan Heading")
        self.assertEqual(results[0]["category"], "work")

    def test_recallcmd_a_titleless_work_document_summarises_as_empty(self):
        corpus = [({"id": "f/notes", "path": "work/f/notes.md"}, "geofence")]
        results, _o, _u = recallcmd._work_scope_budget_walk(
            [(corpus[0][0], 1.0)], corpus, 999)
        self.assertEqual(results[0]["summary"], "")

    def test_recallcmd_the_work_walk_also_skips_rather_than_breaks(self):
        corpus = [({"id": "a", "path": "p"}, "aa"),
                  ({"id": "b", "path": "p"}, "x" * 4000),
                  ({"id": "c", "path": "p"}, "cc")]
        scored = [(corpus[0][0], 3.0), (corpus[1][0], 2.0), (corpus[2][0], 1.0)]
        results, overflow, _u = recallcmd._work_scope_budget_walk(
            scored, corpus, 50)
        self.assertEqual([r["id"] for r in results], ["a", "c"])
        self.assertEqual([o["id"] for o in overflow], ["b"])

    def test_recallcmd_a_document_missing_from_the_corpus_costs_nothing(self):
        """The lookup is a `next(..., "")`: a scored entry with no matching text
        must not raise inside a read-only ranking path."""
        results, _o, used = recallcmd._work_scope_budget_walk(
            [({"id": "ghost", "path": "p"}, 1.0)], [], 999)
        self.assertEqual(len(results), 1)
        self.assertEqual(used, results[0]["estimated_tokens"])

    def test_recallcmd_an_empty_work_tree_is_a_payload_not_an_error(self):
        """"No work documents matched" and "there is no work/ directory" are
        both answers a caller can act on; neither is a failure of the command."""
        payload = recallcmd._work_scope_payload(self.args(), self.kdir,
                                                "2026-02-01")
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["scope"], "work")

    def test_recallcmd_the_work_payload_never_reports_a_category(self):
        payload = recallcmd._work_scope_payload(self.args(), self.work_kb(),
                                                "2026-02-01")
        self.assertIsNone(payload["category"])
        self.assertEqual(payload["strategy"], "bm25")

    def test_recallcmd_the_work_payload_ranks_the_work_tree(self):
        payload = recallcmd._work_scope_payload(self.args(), self.work_kb(),
                                                "2026-02-01")
        self.assertTrue(payload["results"])
        for r in payload["results"]:
            self.assertTrue(r["path"].startswith("work/"))

    def test_recallcmd_the_work_scope_never_touches_the_index(self):
        """`work/` stays UNINDEXED: plans and worklogs are the operator's
        in-flight record, ranked on demand and never promoted."""
        kdir = self.work_kb()
        with open(os.path.join(kdir, "index.json"), "r", encoding="utf-8") as f:
            before = f.read()
        recallcmd._work_scope_payload(self.args(), kdir, "2026-02-01")
        with open(os.path.join(kdir, "index.json"), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), before)

    def test_recallcmd_the_work_json_view_is_the_payload_verbatim(self):
        rc, out, _err = _capture(
            recallcmd._run_work_scope_recall,
            self.args(format="json"), self.work_kb(), "2026-02-01")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["scope"], "work")

    def test_recallcmd_the_work_text_view_is_deliberately_thin(self):
        """No age, no trust, no staleness footer: those describe a CURATED
        entry's provenance, and a plan document has none to report."""
        rc, out, _err = _capture(
            recallcmd._run_work_scope_recall,
            self.args(), self.work_kb(), "2026-02-01")
        self.assertEqual(rc, 0)
        self.assertIn("recall (scope=work)", out)
        self.assertNotIn("age:", out)
        self.assertNotIn("trust:", out)


if __name__ == "__main__":
    unittest.main()
