"""Owning unit file for `agentware_lib.retrieval.recallcmd` — task 99 wave 9.

The module had no owning file. The three `test_recall*.py` suites beside it are
RANKING suites: they ask what comes back first, which is the retriever's
question, not the verb's. What the verb owns is everything between a ranked list
and what the caller receives — the token budget, the record shape, the org scope
block, the ephemeral work corpus and the two renderers — and those were reachable
only as side effects of a ranking assertion.

What is asserted here is the verb's PROMISE:

  * SKIP, NEVER BREAK (INV-5). An entry that would exceed the budget is recorded
    in `overflow` and the walk CONTINUES, so a smaller lower-ranked entry still
    lands. A `break` passes every test whose oversized entry ranks last, so the
    fixtures put one in the MIDDLE.
  * OVERFLOW IS POINTERS-ONLY. id, path, category, score, size — never a body,
    never a judgment. A budget that leaked the content it refused would defeat
    itself.
  * ADDITIVE, NEVER SUBTRACTIVE. Provenance and trust keys appear only when
    present, and the org scope block only in org mode, so a flat-KB payload is
    byte-identical to what it was before either feature existed (INV-1).
  * READ-ONLY (INV-2). Nothing here writes to the knowledge base.
  * THE WORK SCOPE IS A DIFFERENT COMMAND. It ranks an EPHEMERAL corpus that is
    never indexed, weights it by each feature's plan state, and renders a
    deliberately thinner view.

Stdlib-only, hermetic: every KB is a fresh `mkdtemp`.
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


class BudgetWalkTest(RecallCmdTestCase):
    """`_budget_walk` — the greedy, graceful token walk."""

    def ranked(self):
        return [
            ({"id": "small1", "path": "p/1.md", "category": "learnings",
              "summary": "one"}, 9.0, "aa bb"),
            ({"id": "huge", "path": "p/2.md", "category": "learnings",
              "summary": "two"}, 8.0, "x" * 4000),
            ({"id": "small2", "path": "p/3.md", "category": "references",
              "summary": "three"}, 7.0, "cc dd"),
        ]

    def walk(self, budget):
        return recallcmd._budget_walk(
            self.ranked(), self.kdir, budget, "2026-02-01", {})

    def test_recallcmd_an_oversized_entry_is_skipped_not_terminal(self):
        """THE row that separates skip from break: the oversized entry sits in
        the MIDDLE, so a `break` returns one result and this returns two."""
        results, overflow, _used = self.walk(50)
        self.assertEqual([r["id"] for r in results], ["small1", "small2"])
        self.assertEqual([o["id"] for o in overflow], ["huge"])

    def test_recallcmd_rank_order_is_preserved_among_included_entries(self):
        results, _o, _u = self.walk(50)
        self.assertEqual([r["score"] for r in results],
                         sorted((r["score"] for r in results), reverse=True))

    def test_recallcmd_a_zero_budget_admits_nothing_and_names_everything(self):
        """count=0 must still be ACTIONABLE: the caller learns what it missed
        rather than being told the knowledge is absent."""
        results, overflow, used = self.walk(0)
        self.assertEqual(results, [])
        self.assertEqual(len(overflow), 3)
        self.assertEqual(used, 0)

    def test_recallcmd_used_tokens_count_only_included_entries(self):
        results, _o, used = self.walk(50)
        self.assertEqual(used, sum(r["estimated_tokens"] for r in results))

    def test_recallcmd_overflow_records_are_pointers_only(self):
        """Never a body and never a judgment — a budget that leaked the content
        it refused would defeat its own purpose."""
        _r, overflow, _u = self.walk(0)
        for o in overflow:
            self.assertEqual(sorted(o), ["category", "estimated_tokens", "id",
                                         "path", "score"])

    def test_recallcmd_result_records_carry_the_stable_schema(self):
        results, _o, _u = self.walk(4000)
        self.assertTrue(set(["id", "path", "category", "score", "summary",
                             "estimated_tokens", "abs_path", "created",
                             "last_verified", "age_days"]) <= set(results[0]))

    def test_recallcmd_scores_are_rounded_to_six_places(self):
        ranked = [({"id": "a", "category": "learnings"}, 1.0 / 3, "x")]
        results, _o, _u = recallcmd._budget_walk(
            ranked, self.kdir, 999, "2026-02-01", {})
        self.assertEqual(results[0]["score"], round(1.0 / 3, 6))

    def test_recallcmd_a_missing_summary_becomes_an_empty_string(self):
        """The record shape is a CONTRACT the loop parses; `None` where a string
        is promised is a schema break, not a missing value."""
        ranked = [({"id": "a", "category": "learnings"}, 1.0, "x")]
        results, _o, _u = recallcmd._budget_walk(
            ranked, self.kdir, 999, "2026-02-01", {})
        self.assertEqual(results[0]["summary"], "")

    def test_recallcmd_org_provenance_appears_only_when_present(self):
        """Additive-only: a flat-KB record must be byte-identical to what it was
        before org mode existed."""
        ranked = [({"id": "a", "category": "learnings", "origin": "o",
                    "team": "t", "author": ""}, 1.0, "x")]
        results, _o, _u = recallcmd._budget_walk(
            ranked, self.kdir, 999, "2026-02-01", {})
        self.assertEqual(results[0]["origin"], "o")
        self.assertEqual(results[0]["team"], "t")
        self.assertNotIn("author", results[0])

    def test_recallcmd_freshness_is_anchored_to_the_live_knowledge_dir(self):
        """A recall result is read by an agent that may not share this
        process's cwd, so the absolute path is the one that resolves for it
        (R-FAIL-08)."""
        ranked = [({"id": "a", "category": "learnings", "path": "x/y.md"},
                   1.0, "t")]
        results, _o, _u = recallcmd._budget_walk(
            ranked, self.kdir, 999, "2026-02-01", {})
        self.assertEqual(results[0]["abs_path"],
                         os.path.join(self.kdir, "x/y.md"))

    def test_recallcmd_an_entry_with_no_path_has_no_absolute_path(self):
        ranked = [({"id": "a", "category": "learnings"}, 1.0, "t")]
        results, _o, _u = recallcmd._budget_walk(
            ranked, self.kdir, 999, "2026-02-01", {})
        self.assertIsNone(results[0]["abs_path"])

    def test_recallcmd_an_empty_ranking_is_an_empty_walk(self):
        self.assertEqual(recallcmd._budget_walk([], self.kdir, 10, "d", {}),
                         ([], [], 0))


class TeamScopeBlockTest(RecallCmdTestCase):
    """`_team_scope_block` — the one definition of "org mode is in play"."""

    def test_recallcmd_a_flat_kb_publishes_no_scope_block(self):
        """Absent, not empty: a payload carrying a null scope block would differ from
        the pre-org bytes the gold-fixture gate holds."""
        self.assertIsNone(
            recallcmd._team_scope_block(None, None, False, False, None))

    def test_recallcmd_any_one_signal_publishes_the_block(self):
        for kw in ({"caller_team": "p"}, {"team": "p"}, {"all_teams": True},
                   {"include_shared": True}, {"team_scope": "shared"}):
            args = dict(caller_team=None, team=None, all_teams=False,
                        include_shared=False, team_scope=None)
            args.update(kw)
            self.assertIsNotNone(
                recallcmd._team_scope_block(**args), kw)

    def test_recallcmd_the_block_reports_every_input(self):
        block = recallcmd._team_scope_block("plat", "other", False, True,
                                            "shared")
        self.assertEqual(block, {"caller_team": "plat", "team": "other",
                                 "all_teams": False, "include_shared": True,
                                 "scope": "shared"})

    def test_recallcmd_the_flags_are_coerced_to_booleans(self):
        """The block is JSON the loop parses; a truthy string where a bool is
        promised is a schema break."""
        block = recallcmd._team_scope_block(None, None, 1, "yes", None)
        self.assertIs(block["all_teams"], True)
        self.assertIs(block["include_shared"], True)


class BuildRecallPayloadTest(RecallCmdTestCase):
    """`build_recall_payload` — the shared surface the MCP tool also calls."""

    def payload(self, **kw):
        kw.setdefault("as_of", "2026-02-01")
        query = kw.pop("query", "geofence reminders macos timeout python")
        return recallcmd.build_recall_payload(self.kdir, self.data, query, **kw)

    def test_recallcmd_payload_carries_the_documented_keys(self):
        payload, _n = self.payload()
        self.assertEqual(
            sorted(payload),
            sorted(["query", "strategy", "as_of", "top_k", "token_budget",
                    "category", "context_tokens", "count", "results",
                    "overflow", "overflow_count"]))

    def test_recallcmd_count_matches_the_results_it_reports(self):
        payload, _n = self.payload(top_k=6)
        self.assertEqual(payload["count"], len(payload["results"]))
        self.assertEqual(payload["overflow_count"], len(payload["overflow"]))

    def test_recallcmd_an_explicit_strategy_is_reported_verbatim(self):
        payload, notice = self.payload(strategy="bm25+acr")
        self.assertEqual(payload["strategy"], "bm25+acr")
        self.assertEqual(notice, "")

    def test_recallcmd_no_strategy_resolves_the_gated_default(self):
        payload, _n = self.payload()
        self.assertIn(payload["strategy"], ("bm25", "bm25+acr"))

    def test_recallcmd_the_pinned_as_of_is_echoed_not_replaced(self):
        """PIN it for reproducible runs: a payload that silently re-dated itself
        would make two runs of the same query incomparable."""
        payload, _n = self.payload(as_of="2026-03-04")
        self.assertEqual(payload["as_of"], "2026-03-04")

    def test_recallcmd_the_suppressed_key_is_absent_on_a_clean_corpus(self):
        """Reported, never silent — but only when there IS something to report,
        so a clean payload stays byte-identical to today."""
        payload, _n = self.payload()
        self.assertNotIn("suppressed", payload)

    def test_recallcmd_a_flat_kb_payload_has_no_team_scope(self):
        payload, _n = self.payload()
        self.assertNotIn("team_scope", payload)

    def test_recallcmd_a_scoping_flag_publishes_the_team_scope(self):
        payload, _n = self.payload(team="platform")
        self.assertEqual(payload["team_scope"]["team"], "platform")

    def test_recallcmd_the_budget_is_enforced_by_the_payload(self):
        payload, _n = self.payload(token_budget=0, top_k=6)
        self.assertEqual(payload["results"], [])
        self.assertTrue(payload["overflow"])

    def test_recallcmd_top_k_zero_returns_nothing(self):
        payload, _n = self.payload(top_k=0)
        self.assertEqual(payload["count"], 0)

    def test_recallcmd_a_category_filter_narrows_the_ranking(self):
        payload, _n = self.payload(category="learnings", top_k=6)
        self.assertTrue(payload["results"])
        for r in payload["results"]:
            self.assertEqual(r["category"], "learnings")

    def test_recallcmd_building_a_payload_never_writes_to_the_kb(self):
        """INV-2. The recall path is read-only, and the one thing that would
        make that claim false is a cache or ledger written on the way through."""
        before = sorted(os.listdir(self.kdir))
        self.payload()
        self.assertEqual(sorted(os.listdir(self.kdir)), before)

    def test_recallcmd_two_builds_of_one_query_are_identical(self):
        a, _n1 = self.payload(strategy="bm25")
        b, _n2 = self.payload(strategy="bm25")
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))


class ScopedCorpusTest(RecallCmdTestCase):
    """`_scoped_corpus` — the R-SEC-02 refusal, applied BEFORE ranking."""

    def test_recallcmd_a_clean_corpus_passes_through_unchanged(self):
        view, kept, suppressed = recallcmd._scoped_corpus(
            self.kdir, self.data, self.data["entries"])
        self.assertIs(view, self.data)
        self.assertEqual(len(kept), len(self.data["entries"]))
        self.assertEqual(suppressed, [])

    def test_recallcmd_the_refusal_runs_before_ranking(self):
        """Reporting happens when a human runs `org lint`; injection happens
        now — so a suppressed entry must never reach the ranker at all."""
        entries = list(self.data["entries"])
        poisoned = dict(entries[0])
        poisoned["id"] = "poisoned"
        poisoned["origin"] = "shared"
        poisoned["team"] = "other"
        view, kept, suppressed = recallcmd._scoped_corpus(
            self.kdir, self.data, entries + [poisoned])
        self.assertEqual(len(kept) + len(suppressed), len(entries) + 1)
        self.assertIsNotNone(view)


class CmdRecallTest(RecallCmdTestCase):
    """`cmd_recall` — validation, dispatch and the human renderer."""

    def test_recallcmd_a_negative_top_k_is_refused(self):
        code, _out, err = run_cli(["recall", "q", "--top-k", "-1"], self.kdir)
        self.assertEqual(code, 1)
        self.assertIn("--top-k must be >= 0", err)

    def test_recallcmd_a_negative_budget_is_refused(self):
        code, _out, err = run_cli(
            ["recall", "q", "--token-budget", "-1"], self.kdir)
        self.assertEqual(code, 1)
        self.assertIn("--token-budget must be >= 0", err)

    def test_recallcmd_a_malformed_as_of_is_refused(self):
        code, _out, err = run_cli(["recall", "q", "--as-of", "nope"], self.kdir)
        self.assertEqual(code, 1)
        self.assertIn("--as-of must be a YYYY-MM-DD date", err)

    def test_recallcmd_an_impossible_date_is_refused(self):
        """Shape is not validity: `2026-13-99` parses as a pattern and is still
        not a date, and an ACR decay computed from one is silently wrong."""
        code, _out, _err = run_cli(
            ["recall", "q", "--as-of", "2026-13-99"], self.kdir)
        self.assertEqual(code, 1)

    def test_recallcmd_the_validations_run_before_any_ranking(self):
        """Refusals are cheap and must stay cheap: a bad flag must not first
        read the whole corpus."""
        code, out, _err = run_cli(["recall", "q", "--top-k", "-1"], self.kdir)
        self.assertEqual((code, out), (1, ""))

    def test_recallcmd_json_output_parses_and_carries_the_query(self):
        code, out, _err = run_cli(
            ["recall", "geofence", "--format", "json"], self.kdir)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["query"], "geofence")

    def test_recallcmd_the_text_view_names_its_ranking_parameters(self):
        code, out, _err = run_cli(["recall", "geofence"], self.kdir)
        self.assertEqual(code, 0)
        self.assertIn("strategy=", out)
        self.assertIn("budget=", out)

    def test_recallcmd_the_text_view_ends_with_one_staleness_footer(self):
        """ONE per invocation, not per result: every value above it is
        point-in-time, and repeating the warning per row would train the reader
        to skip it (R-FAIL-08)."""
        _c, out, _e = run_cli(["recall", "geofence"], self.kdir)
        self.assertEqual(out.count("point-in-time values may be stale"), 1)

    def test_recallcmd_the_text_view_prints_the_live_anchored_path(self):
        _c, out, _e = run_cli(["recall", "geofence"], self.kdir)
        self.assertIn(self.kdir, out)

    def test_recallcmd_a_category_filter_is_echoed_in_the_text_view(self):
        _c, out, _e = run_cli(
            ["recall", "geofence", "--category", "learnings"], self.kdir)
        self.assertIn("category: learnings", out)

    def test_recallcmd_the_overflow_note_names_what_was_skipped(self):
        """A budget that silently drops ranked entries makes "no result" and
        "the result did not fit" the same output."""
        _c, out, _e = run_cli(
            ["recall", "geofence", "--token-budget", "0"], self.kdir)
        self.assertIn("exceeded the token budget", out)
        self.assertIn("agentware query --id", out)

    def test_recallcmd_no_overflow_means_no_note(self):
        _c, out, _e = run_cli(["recall", "geofence"], self.kdir)
        self.assertNotIn("exceeded the token budget", out)

    def test_recallcmd_the_overflow_note_is_singular_for_one_entry(self):
        overflow = [{"id": "a", "path": "p", "score": 1.0,
                     "estimated_tokens": 9}]
        _rv, out, _err = _capture(recallcmd._print_recall_overflow, overflow)
        self.assertIn("1 ranked entry", out)
        self.assertIn("was skipped", out)

    def test_recallcmd_the_overflow_note_is_plural_for_several(self):
        overflow = [{"id": "a", "path": "p", "score": 1.0,
                     "estimated_tokens": 9},
                    {"id": "b", "path": "p", "score": 0.5,
                     "estimated_tokens": 8}]
        _rv, out, _err = _capture(recallcmd._print_recall_overflow, overflow)
        self.assertIn("2 ranked entries", out)
        self.assertIn("were skipped", out)

    def test_recallcmd_an_empty_overflow_prints_nothing(self):
        _rv, out, _err = _capture(recallcmd._print_recall_overflow, [])
        self.assertEqual(out, "")

    def test_recallcmd_the_work_scope_is_dispatched_by_the_verb(self):
        code, out, _err = run_cli(
            ["recall", "geofence", "--scope", "work"], self.work_kb())
        self.assertEqual(code, 0)
        self.assertIn("recall (scope=work)", out)

    def test_recallcmd_the_default_scope_is_curated(self):
        code, out, _err = run_cli(["recall", "geofence"], self.work_kb())
        self.assertEqual(code, 0)
        self.assertNotIn("scope=work", out)

    def test_recallcmd_recall_writes_nothing_to_the_knowledge_base(self):
        before = sorted(os.listdir(self.kdir))
        run_cli(["recall", "geofence"], self.kdir)
        self.assertEqual(sorted(os.listdir(self.kdir)), before)


class RegistrarTest(RecallCmdTestCase):
    """The verb declaration — handler and registrar travel together."""

    def test_recallcmd_declares_its_verb_once_in_this_module(self):
        self.assertEqual([v for (v, _o, _r) in recallcmd.__verbs__], ["recall"])

    def test_recallcmd_the_declared_registrar_resolves_here(self):
        _verb, _order, name = recallcmd.__verbs__[0]
        self.assertTrue(callable(getattr(recallcmd, name)))

    def test_recallcmd_exports_the_shared_payload_builder(self):
        """The MCP recall tool reaches `build_recall_payload` as a bare name, so
        its absence from `_EXPORTS` would break that surface silently."""
        self.assertIn("build_recall_payload", recallcmd._EXPORTS)
        self.assertIn("cmd_recall", recallcmd._EXPORTS)

    def test_recallcmd_the_registrar_is_not_exported(self):
        """Deliberately excluded: it is resolved by importlib from `__verbs__`,
        and exporting it would put a second route to the same registration into
        the facade."""
        self.assertNotIn("register_recall", recallcmd._EXPORTS)


if __name__ == "__main__":
    unittest.main()
