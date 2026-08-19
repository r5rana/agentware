"""Tests for the read-time trust SURFACE (feature 260713-p3-trust-staleness, Task 2).

Stdlib `unittest` only, driven against a synthetic temp KB at a PINNED `as_of`
so every assertion is deterministic (INV-1). Asserts:

  - `trust_signals` classifies: a DECLARED-superseded entry -> trust "superseded"
    with the correct `superseded_by`; a stale volatile entry -> "stale"; a fresh
    entry -> "current"; superseded OUTRANKS stale while the `stale` bit survives.
  - The documented edge cases hold: self-supersede ignored, out-of-vocab relation
    type ignored, non-volatile entry never "stale", undated entry not stale,
    strict/exclusive window boundary.
  - `as_of` is REQUIRED and validated -> no hidden wall-clock can leak into a
    ranking signal (the trap `stale_entries`' advisory `today or _today()` sets).
  - The surface rides EVERY strategy the recall payload can resolve (`bm25`,
    `bm25+acr`, `bm25+embed`) -- `tag` is NOT a recall strategy (eval-only).
  - The surface is ADDITIVE and ORDERING-NEUTRAL: payload result order matches
    the untouched `retrieve_bm25` / `retrieve_bm25_acr` ranking exactly, and the
    `overflow` pointer contract keeps its EXACT pre-existing key set.
  - Everything is read-only: recall never mutates index.json (INV-2).
"""

from tests._paths import TESTS_DIR

import json
import os

try:
    from tests._fixtures import (SyntheticKBTestCase, build_synthetic_kb,
                                 load_cli, run_cli)
except ImportError:  # allow `python3 -m unittest tests.unit.features.test_trust_surface`
    from _fixtures import (SyntheticKBTestCase, build_synthetic_kb,
                           load_cli, run_cli)

CLI = load_cli()

# Pinned so freshness/staleness never depend on the wall clock (INV-1).
AS_OF = "2026-06-25"

# Shared vocabulary: every entry matches the SAME query, so the trust signal is
# the only thing that distinguishes them (ranking stays a controlled variable).
_BODY = ("The cache invalidation race served a stale value because the "
         "invalidation hook fired before the database write committed.\n")


def _entry(eid, name, created, category="learnings", **extra):
    e = {
        "id": eid,
        "title": name,
        "category": category,
        "path": "%s/%s.md" % (category, name),
        "tags": ["cache", "race"],
        "created": created,
        "last_verified": created,
        "summary": "cache invalidation race stale value",
        "body": "# %s\n\n%s" % (name, _BODY),
    }
    e.update(extra)
    return e


# fresh + stale (far beyond the 120d window) + a DECLARED supersede pair.
_ENTRIES = [
    _entry("learn-cache-fresh", "fresh", "2026-06-10"),
    _entry("learn-cache-stale", "stale", "2024-01-01"),
    _entry("learn-cache-old", "old", "2026-06-01"),
    _entry("learn-cache-new", "new", "2026-06-05",
           relates=["supersedes:learn-cache-old"]),
]

QUERY = "cache invalidation race stale value"


class TrustSignalsUnitTest(SyntheticKBTestCase):
    """Pure-function behavior of `trust_signals` (no CLI, no filesystem)."""

    def _signals(self, entries, as_of=AS_OF, **kw):
        return CLI.trust_signals({"entries": entries}, as_of, **kw)

    def test_declared_supersede_marks_target_not_superseder(self):
        s = self._signals([
            {"id": "x", "category": "learnings", "last_verified": AS_OF},
            {"id": "y", "category": "learnings", "last_verified": AS_OF,
             "relates": ["supersedes:x"]},
        ])
        self.assertEqual(s["x"]["trust"], "superseded")
        self.assertEqual(s["x"]["superseded_by"], ["y"])
        # The SUPERSEDER itself stays current — direction must not invert.
        self.assertEqual(s["y"]["trust"], "current")
        self.assertEqual(s["y"]["superseded_by"], [])

    def test_stale_volatile_entry(self):
        s = self._signals([
            {"id": "old", "category": "learnings", "last_verified": "2024-01-01"},
        ])
        self.assertEqual(s["old"]["trust"], "stale")
        self.assertTrue(s["old"]["stale"])

    def test_fresh_entry_is_current(self):
        s = self._signals([
            {"id": "f", "category": "learnings", "last_verified": AS_OF},
        ])
        self.assertEqual(s["f"], {"stale": False, "superseded_by": [],
                                  "trust": "current"})

    def test_superseded_outranks_stale_but_stale_bit_survives(self):
        s = self._signals([
            {"id": "x", "category": "learnings", "last_verified": "2024-01-01"},
            {"id": "y", "category": "learnings", "last_verified": AS_OF,
             "relates": ["supersedes:x"]},
        ])
        self.assertEqual(s["x"]["trust"], "superseded")
        # A consumer reading `trust` alone would lose this bit; it must persist.
        self.assertTrue(s["x"]["stale"])

    def test_self_supersede_is_ignored(self):
        # An entry cannot replace itself; honoring the edge would bury it forever.
        s = self._signals([
            {"id": "x", "category": "learnings", "last_verified": AS_OF,
             "relates": ["supersedes:x"]},
        ])
        self.assertEqual(s["x"]["trust"], "current")
        self.assertEqual(s["x"]["superseded_by"], [])

    def test_mutual_supersede_marks_both(self):
        # Faithful to what the pair DECLARES; graph_integrity reports the cycle.
        # Must terminate (no traversal), not hang.
        s = self._signals([
            {"id": "x", "category": "learnings", "last_verified": AS_OF,
             "relates": ["supersedes:y"]},
            {"id": "y", "category": "learnings", "last_verified": AS_OF,
             "relates": ["supersedes:x"]},
        ])
        self.assertEqual(s["x"]["trust"], "superseded")
        self.assertEqual(s["y"]["trust"], "superseded")

    def test_out_of_vocab_relation_type_is_not_counted(self):
        # `_fm_parse_relate_token` KEEPS out-of-vocab types (graph_integrity
        # flags them); the ranking path must not guess the vocabulary.
        s = self._signals([
            {"id": "x", "category": "learnings", "last_verified": AS_OF},
            {"id": "y", "category": "learnings", "last_verified": AS_OF,
             "relates": ["Supersedes:x"]},
        ])
        self.assertEqual(s["x"]["trust"], "current")

    def test_non_volatile_entry_is_never_stale(self):
        # `stale: False` here means "window not applicable", NOT "verified fresh".
        s = self._signals([
            {"id": "r", "category": "references", "last_verified": "2019-01-01"},
        ])
        self.assertFalse(s["r"]["stale"])
        self.assertEqual(s["r"]["trust"], "current")

    def test_non_volatile_entry_can_still_be_superseded(self):
        # Supersession is category-independent — it is a DECLARED fact.
        s = self._signals([
            {"id": "r", "category": "references", "last_verified": AS_OF},
            {"id": "r2", "category": "references", "last_verified": AS_OF,
             "relates": ["supersedes:r"]},
        ])
        self.assertEqual(s["r"]["trust"], "superseded")

    def test_undated_entry_is_not_stale(self):
        s = self._signals([{"id": "u", "category": "learnings"}])
        self.assertEqual(s["u"]["trust"], "current")

    def test_window_boundary_is_strict(self):
        import datetime

        def stale_at(age):
            d = (datetime.date.fromisoformat(AS_OF)
                 - datetime.timedelta(days=age)).isoformat()
            s = self._signals([{"id": "e", "category": "learnings",
                                "last_verified": d}])
            return s["e"]["stale"]

        self.assertFalse(stale_at(CLI.STALE_MAX_AGE_DAYS - 1))
        self.assertFalse(stale_at(CLI.STALE_MAX_AGE_DAYS))    # exclusive
        self.assertTrue(stale_at(CLI.STALE_MAX_AGE_DAYS + 1))

    def test_as_of_is_required_and_validated(self):
        # The whole point: a ranking signal must never silently fall back to the
        # wall clock the way the advisory `stale_entries` report may.
        for bad in (None, "", "2026-6-25", "garbage", 20260625):
            with self.assertRaises(ValueError):
                CLI.trust_signals({"entries": []}, bad)

    def test_dangling_supersede_target_is_not_invented(self):
        s = self._signals([
            {"id": "y", "category": "learnings", "last_verified": AS_OF,
             "relates": ["supersedes:ghost"]},
        ])
        self.assertNotIn("ghost", s)

    def test_malformed_entries_are_skipped(self):
        s = self._signals(["not-a-dict", {"no": "id"},
                           {"id": "ok", "category": "learnings",
                            "last_verified": AS_OF}])
        self.assertEqual(list(s), ["ok"])

    def test_deterministic_at_fixed_as_of(self):
        entries = [
            {"id": "x", "category": "learnings", "last_verified": "2024-01-01"},
            {"id": "y", "category": "learnings", "last_verified": AS_OF,
             "relates": ["supersedes:x"]},
            {"id": "z", "category": "learnings", "last_verified": AS_OF,
             "relates": ["supersedes:x"]},
        ]
        a = json.dumps(self._signals(entries), sort_keys=True)
        b = json.dumps(self._signals(entries), sort_keys=True)
        self.assertEqual(a, b)
        # Multiple superseders are sorted for a stable list.
        self.assertEqual(self._signals(entries)["x"]["superseded_by"], ["y", "z"])

    def test_superseded_by_is_sorted_not_set_iteration_order(self):
        # `sorted()` is what makes `superseded_by` stable ACROSS processes. The
        # A==B check above CANNOT prove this: set order is stable WITHIN a
        # process for identical contents, so it passes even on an unsorted
        # implementation. Compare against a known sort instead, with enough ids
        # that incidental agreement is unlikely.
        ids = ["sup-q", "sup-a", "sup-z", "sup-m", "sup-b"]
        entries = [{"id": "target", "category": "learnings",
                    "last_verified": AS_OF}]
        entries += [{"id": i, "category": "learnings", "last_verified": AS_OF,
                     "relates": ["supersedes:target"]} for i in ids]
        got = self._signals(entries)["target"]["superseded_by"]
        self.assertEqual(got, sorted(ids))

    def test_deterministic_across_processes_with_differing_hash_seeds(self):
        # INV-1 is byte-identity ACROSS RUNS. Set/dict iteration order varies by
        # PYTHONHASHSEED between PROCESSES but is stable within one, so this is
        # the only place that class of nondeterminism can actually surface.
        import subprocess
        import sys as _sys
        here = TESTS_DIR
        script = (
            "import json, sys\n"
            "sys.path.insert(0, %r)\n"
            "from _fixtures import load_cli\n"
            "m = load_cli()\n"
            "ids = ['sup-q', 'sup-a', 'sup-z', 'sup-m', 'sup-b']\n"
            "e = [{'id': 'target', 'category': 'learnings', 'last_verified': %r}]\n"
            "e += [{'id': i, 'category': 'learnings', 'last_verified': %r,\n"
            "       'relates': ['supersedes:target']} for i in ids]\n"
            "e += [{'id': 'old', 'category': 'learnings', 'last_verified': '2024-01-01'}]\n"
            "print(json.dumps(m.trust_signals({'entries': e}, %r), sort_keys=True))\n"
            % (here, AS_OF, AS_OF, AS_OF))
        outs = []
        for seed in ("0", "1", "12345"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            r = subprocess.run([_sys.executable, "-c", script], env=env,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.append(r.stdout)
        self.assertEqual(len(set(outs)), 1,
                         "trust_signals output varies with PYTHONHASHSEED")

    def test_max_age_days_sentinel_resolves_to_shared_constant(self):
        entries = [{"id": "e", "category": "learnings",
                    "last_verified": "2024-01-01"}]
        self.assertEqual(self._signals(entries),
                         self._signals(entries,
                                       max_age_days=CLI.STALE_MAX_AGE_DAYS))
        # An explicit wide window un-stales it (the window is a real parameter).
        self.assertFalse(self._signals(entries, max_age_days=100000)["e"]["stale"])

    def test_never_mutates_input_entries(self):
        entries = [{"id": "x", "category": "learnings", "last_verified": "2024-01-01"},
                   {"id": "y", "category": "learnings", "last_verified": AS_OF,
                    "relates": ["supersedes:x"]}]
        before = json.dumps(entries, sort_keys=True)
        self._signals(entries)
        self.assertEqual(json.dumps(entries, sort_keys=True), before)

    def test_trust_unknown_is_a_fresh_dict_each_call(self):
        a, b = CLI._trust_unknown(), CLI._trust_unknown()
        self.assertIsNot(a, b)
        self.assertIsNot(a["superseded_by"], b["superseded_by"])

    def test_stale_definition_is_shared_with_audit_stale(self):
        # Q4: ONE definition of "stale" — the ranking signal and the advisory
        # report must read the same window + categories, never their own copies.
        data = {"entries": [
            {"id": "s", "category": "learnings", "last_verified": "2024-01-01"},
            {"id": "f", "category": "learnings", "last_verified": AS_OF},
        ]}
        reported = {e.get("id") for (e, _a) in CLI.stale_entries(
            data, CLI.STALE_MAX_AGE_DAYS, today=AS_OF)}
        signalled = {i for (i, s) in CLI.trust_signals(data, AS_OF).items()
                     if s["stale"]}
        self.assertEqual(reported, signalled)


class TrustSurfaceRecallTest(SyntheticKBTestCase):
    """The surface as it rides the real recall payload (CLI + MCP + strategies)."""

    def setUp(self):
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=_ENTRIES)

    def _payload(self, strategy):
        code, out, err = self.run_cli(
            ["recall", QUERY, "--strategy", strategy, "--as-of", AS_OF,
             "--token-budget", "100000", "--format", "json"])
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def _by_id(self, payload):
        return {r["id"]: r for r in payload["results"]}

    def test_surface_present_and_correct_under_bm25(self):
        r = self._by_id(self._payload("bm25"))
        self.assertEqual(r["learn-cache-fresh"]["trust"], "current")
        self.assertEqual(r["learn-cache-stale"]["trust"], "stale")
        self.assertTrue(r["learn-cache-stale"]["stale"])
        self.assertEqual(r["learn-cache-old"]["trust"], "superseded")
        self.assertEqual(r["learn-cache-old"]["superseded_by"],
                         ["learn-cache-new"])
        self.assertEqual(r["learn-cache-new"]["trust"], "current")

    def test_surface_present_under_bm25_acr(self):
        r = self._by_id(self._payload("bm25+acr"))
        self.assertEqual(r["learn-cache-old"]["trust"], "superseded")
        self.assertEqual(r["learn-cache-stale"]["trust"], "stale")
        self.assertEqual(r["learn-cache-fresh"]["trust"], "current")

    def test_every_result_carries_all_three_fields_every_strategy(self):
        # `bm25+embed` is the third strategy the payload builder can RESOLVE
        # (Mode B; it degrades to bm25 with no local embedder). `tag` is NOT a
        # recall strategy — it is eval-only and `recall_ranked` raises on it.
        for strategy in ("bm25", "bm25+acr", "bm25+embed"):
            payload, _notice = CLI.build_recall_payload(
                self.kdir, self.index_data, QUERY, top_k=10,
                token_budget=100000, strategy=strategy, as_of=AS_OF)
            self.assertTrue(payload["results"], strategy)
            for r in payload["results"]:
                for field in ("stale", "superseded_by", "trust"):
                    self.assertIn(field, r, "%s missing under %s"
                                  % (field, strategy))
                self.assertIn(r["trust"], ("current", "stale", "superseded"))
                self.assertIsInstance(r["stale"], bool)
                self.assertIsInstance(r["superseded_by"], list)

    def test_tag_is_not_a_recall_strategy(self):
        # Pins the corrected coverage claim: the surface cannot be asserted under
        # `tag` because `tag` never reaches the recall payload at all.
        with self.assertRaises(ValueError):
            CLI.recall_ranked(self.kdir, self.index_data, QUERY,
                              strategy="tag", as_of=AS_OF)

    def test_surface_is_ordering_neutral_vs_untouched_ranking(self):
        # The ranking functions are NOT touched by this feature, so payload order
        # must still equal their order exactly. If the merge (or the trust map
        # build) perturbed ranking, these would diverge.
        got = [r["id"] for r in self._payload("bm25")["results"]]
        self.assertEqual(got, CLI.retrieve_bm25(self.kdir, self.index_data,
                                                QUERY))
        got_acr = [r["id"] for r in self._payload("bm25+acr")["results"]]
        self.assertEqual(got_acr, CLI.retrieve_bm25_acr(
            self.kdir, self.index_data, QUERY, AS_OF))

    def test_surface_does_not_change_token_budget_walk(self):
        # The budget walk costs `doc_text`, never the record — so adding fields
        # must not change WHICH entries fit, the context_tokens, or the overflow
        # split. Pinned to measured literals: asserting
        # `context_tokens == sum(estimated_tokens)` instead would be an ARITHMETIC
        # IDENTITY (`used` accumulates the same `cost` stored per record) that
        # holds for any implementation, including a deleted surface.
        argv = ["recall", QUERY, "--as-of", AS_OF, "--token-budget", "60",
                "--format", "json"]
        p = json.loads(self.run_cli(argv)[1])
        self.assertEqual([r["id"] for r in p["results"]], ["learn-cache-stale"])
        self.assertEqual(p["context_tokens"], 46)
        self.assertEqual(len(p["overflow"]), 3)
        self.assertEqual(p["results"][0]["trust"], "stale")

    def test_trust_signals_is_computed_once_per_payload(self):
        # Pitfall (b) in the plan: the supersede reverse-map + stale window are
        # WHOLE-INDEX facts, so re-deriving them per result would re-walk every
        # edge N times (O(1) -> O(N) whole-index scans). Nothing else in the suite
        # can observe this, so assert the call count directly.
        calls = []
        orig = CLI.trust_signals

        def counting(*a, **kw):
            calls.append(1)
            return orig(*a, **kw)

        CLI.trust_signals = counting
        try:
            payload, _n = CLI.build_recall_payload(
                self.kdir, self.index_data, QUERY, top_k=10,
                token_budget=100000, strategy="bm25", as_of=AS_OF)
        finally:
            CLI.trust_signals = orig
        self.assertGreater(len(payload["results"]), 1, "need >1 result to matter")
        self.assertEqual(len(calls), 1,
                         "trust_signals must be computed ONCE per payload, not "
                         "once per result")

    def test_result_records_never_alias_the_signals_map(self):
        # `dict.update` copies key/value PAIRS, so a raw merge would alias the
        # map's `superseded_by` LIST into the result record. This must compare
        # against the map the payload builder ACTUALLY used — a fresh
        # `trust_signals(...)` call returns different objects, so asserting
        # against one would pass vacuously no matter what the code does.
        captured = {}
        orig = CLI.trust_signals

        def capturing(*a, **kw):
            captured["map"] = orig(*a, **kw)
            return captured["map"]

        CLI.trust_signals = capturing
        try:
            payload, _n = CLI.build_recall_payload(
                self.kdir, self.index_data, QUERY, top_k=10,
                token_budget=100000, strategy="bm25", as_of=AS_OF)
        finally:
            CLI.trust_signals = orig
        signals = captured["map"]
        superseded = [r for r in payload["results"] if r["superseded_by"]]
        self.assertTrue(superseded, "need a superseded result to be non-vacuous")
        for r in payload["results"]:
            self.assertIsNot(r["superseded_by"],
                             signals[r["id"]]["superseded_by"])
            self.assertEqual(r["superseded_by"],
                             signals[r["id"]]["superseded_by"])

    def test_overflow_stays_pointers_only(self):
        # Pre-existing contract: overflow records are pointers, never judgments.
        p = json.loads(self.run_cli(
            ["recall", QUERY, "--as-of", AS_OF, "--token-budget", "40",
             "--format", "json"])[1])
        self.assertTrue(p["overflow"], "expected overflow at a tiny budget")
        allowed = {"id", "path", "category", "score", "estimated_tokens"}
        for o in p["overflow"]:
            self.assertEqual(set(o.keys()), allowed)

    def test_text_output_prints_trust_line(self):
        code, out, err = self.run_cli(
            ["recall", QUERY, "--as-of", AS_OF, "--token-budget", "100000"])
        self.assertEqual(code, 0, err)
        self.assertIn("trust: current", out)
        self.assertIn("trust: stale", out)
        self.assertIn("trust: superseded (superseded by learn-cache-new)", out)

    def test_mcp_recall_inherits_the_surface(self):
        raw = CLI._mcp_tool_recall(
            self.kdir, self.index_data,
            {"query": QUERY, "top_k": 10, "token_budget": 100000,
             "as_of": AS_OF})
        payload = json.loads(raw)
        by_id = {r["id"]: r for r in payload["results"]}
        self.assertEqual(by_id["learn-cache-old"]["trust"], "superseded")
        self.assertEqual(by_id["learn-cache-stale"]["trust"], "stale")

    def test_recall_is_read_only(self):
        idx = os.path.join(self.kdir, "index.json")
        with open(idx, "rb") as f:
            before = f.read()
        self.run_cli(["recall", QUERY, "--as-of", AS_OF, "--format", "json"])
        self.run_cli(["recall", QUERY, "--strategy", "bm25+acr", "--as-of",
                      AS_OF, "--format", "json"])
        with open(idx, "rb") as f:
            after = f.read()
        self.assertEqual(before, after, "recall must not mutate index.json")

    def test_payload_is_byte_identical_across_runs(self):
        argv = ["recall", QUERY, "--strategy", "bm25+acr", "--as-of", AS_OF,
                "--token-budget", "100000", "--format", "json"]
        self.assertEqual(self.run_cli(argv)[1], self.run_cli(argv)[1])

    def test_index_still_validates_with_supersede_edges(self):
        code, out, err = self.run_cli(["index", "validate"])
        self.assertEqual(code, 0, err + out)


if __name__ == "__main__":
    import unittest
    unittest.main()
