"""Tests for the bounded `staleness_factor` downweight in the bm25+acr prior
(feature 260713-p3-trust-staleness, Task 3).

Stdlib `unittest` only, synthetic temp KB, PINNED `as_of` (INV-1). Asserts:

  - `staleness_factor` is the documented bounded (0,1] multiplier: superseded 0.3
    (DOMINATES), stale-volatile 0.5, fresh/non-superseded EXACTLY 1.0; never >1.0
    nor <=0; `as_of` required + validated.
  - Under `bm25+acr` a superseded entry ranks BELOW an otherwise-identical fresh
    peer, and a stale volatile entry ranks below an equally-old NON-volatile peer
    — each isolated so ONLY the staleness factor can explain the flip.
  - Plain `bm25` ordering is BYTE-IDENTICAL to before (the factor is never even
    called on the bm25 path — proven by monkeypatching it to explode).
  - A zero-BM25 entry is never surfaced (the bounded factor can't lift it, C-3).
  - `superseded_ids` reads DECLARED reverse edges only (self/out-of-vocab excluded).

Per the P2c testing gotcha (learn-recall-scope-work-status-multipliers): assert
result ORDER / the factor field, NEVER absolute BM25 scores (which shift with tiny
doc-length differences).
"""

import os

try:
    from tests._fixtures import (SyntheticKBTestCase, build_synthetic_kb,
                                 load_cli, run_cli)
except ImportError:  # allow `python3 -m unittest tests.unit.knowledge.test_staleness_downweight`
    from _fixtures import (SyntheticKBTestCase, build_synthetic_kb,
                           load_cli, run_cli)

CLI = load_cli()
AS_OF = "2026-06-25"
OLD = "2024-01-01"  # far past the 120d window at AS_OF

# One shared query vocabulary so ranking is a controlled variable across entries.
_BODY = ("The alpha beta gamma delta retrieval note describes the alpha beta "
         "gamma delta ranking behavior in detail.\n")
QUERY = "alpha beta gamma delta"


def _e(eid, name, category, created, last_verified, **extra):
    e = {
        "id": eid, "title": name, "category": category,
        "path": "%s/%s.md" % (category, name), "tags": ["alpha", "beta"],
        "created": created, "last_verified": last_verified,
        "summary": "alpha beta gamma delta note",
        "body": "# %s\n\n%s" % (name, _BODY),
    }
    e.update(extra)
    return e


class StalenessFactorUnitTest(SyntheticKBTestCase):
    """Pure-function behavior of `staleness_factor` + `superseded_ids`."""

    def _sup(self, entries):
        return CLI.superseded_ids({"entries": entries})

    def test_fresh_non_superseded_is_exactly_one(self):
        e = {"id": "f", "category": "learnings", "last_verified": AS_OF}
        # EXACTLY 1.0 so acr stays byte-identical (x * 1.0 == x).
        self.assertEqual(CLI.staleness_factor(e, set(), AS_OF), 1.0)

    def test_superseded_multiplier(self):
        e = {"id": "x", "category": "learnings", "last_verified": AS_OF}
        self.assertEqual(CLI.staleness_factor(e, {"x"}, AS_OF),
                         CLI.STALENESS_SUPERSEDED_MULT)

    def test_stale_volatile_multiplier(self):
        e = {"id": "s", "category": "learnings", "last_verified": OLD}
        self.assertEqual(CLI.staleness_factor(e, set(), AS_OF),
                         CLI.STALENESS_STALE_MULT)

    def test_superseded_dominates_stale(self):
        # A superseded AND stale entry gets the superseded multiplier.
        e = {"id": "x", "category": "learnings", "last_verified": OLD}
        self.assertEqual(CLI.staleness_factor(e, {"x"}, AS_OF),
                         CLI.STALENESS_SUPERSEDED_MULT)

    def test_non_volatile_old_entry_is_not_downweighted(self):
        e = {"id": "r", "category": "references", "last_verified": OLD}
        self.assertEqual(CLI.staleness_factor(e, set(), AS_OF), 1.0)

    def test_undated_entry_is_not_downweighted(self):
        e = {"id": "u", "category": "learnings"}
        self.assertEqual(CLI.staleness_factor(e, set(), AS_OF), 1.0)

    def test_bounded_in_zero_one_for_every_shape(self):
        shapes = [
            {"id": "a", "category": "learnings", "last_verified": AS_OF},
            {"id": "b", "category": "learnings", "last_verified": OLD},
            {"id": "c", "category": "references", "last_verified": OLD},
            {"id": "d", "category": "learnings"},
            {"id": "e", "category": "configurations", "last_verified": OLD},
            {"id": "f"},
        ]
        for e in shapes:
            for sup in (set(), {e.get("id")}):
                f = CLI.staleness_factor(e, sup, AS_OF)
                self.assertGreater(f, 0.0, e)
                self.assertLessEqual(f, 1.0, e)

    def test_as_of_is_required_and_validated(self):
        e = {"id": "f", "category": "learnings", "last_verified": AS_OF}
        for bad in (None, "", "2026-6-25", "nope"):
            with self.assertRaises(ValueError):
                CLI.staleness_factor(e, set(), bad)

    def test_max_age_sentinel_resolves_to_shared_constant(self):
        e = {"id": "s", "category": "learnings", "last_verified": OLD}
        self.assertEqual(CLI.staleness_factor(e, set(), AS_OF),
                         CLI.staleness_factor(e, set(), AS_OF,
                                              max_age_days=CLI.STALE_MAX_AGE_DAYS))
        # A wide explicit window un-stales it (the window is a real parameter).
        self.assertEqual(
            CLI.staleness_factor(e, set(), AS_OF, max_age_days=100000), 1.0)

    def test_multiplier_magnitudes_and_ordering(self):
        # Pin the chosen magnitudes AND the semantic contract the constants
        # encode: superseded is penalized HARDER than stale, and the stale
        # penalty is a REAL one (strictly below the gentle ACR freshness floor,
        # so it bites past what acr alone would do). Asserting by-name elsewhere
        # lets a 0.3<->0.5 swap slip through; this locks both.
        self.assertEqual(CLI.STALENESS_SUPERSEDED_MULT, 0.3)
        self.assertEqual(CLI.STALENESS_STALE_MULT, 0.5)
        self.assertLess(CLI.STALENESS_SUPERSEDED_MULT, CLI.STALENESS_STALE_MULT)
        self.assertLess(CLI.STALENESS_STALE_MULT, CLI.ACR_FRESHNESS_FLOOR)

    def test_superseded_ids_reads_declared_reverse_edges_only(self):
        entries = [
            {"id": "x", "category": "learnings", "last_verified": AS_OF},
            {"id": "y", "category": "learnings", "last_verified": AS_OF,
             "relates": ["supersedes:x"]},
            {"id": "self", "category": "learnings", "last_verified": AS_OF,
             "relates": ["supersedes:self"]},           # self-supersede ignored
            {"id": "z", "category": "learnings", "last_verified": AS_OF,
             "relates": ["Supersedes:x"]},               # out-of-vocab ignored
            {"id": "d", "category": "learnings", "last_verified": AS_OF,
             "relates": ["supersedes:ghost"]},           # dangling not invented
        ]
        self.assertEqual(self._sup(entries), {"x"})

    def test_stale_predicate_shared_with_stale_entries(self):
        # Q4 by construction: staleness_factor's stale decision == stale_entries
        # membership (both via _stale_age).
        data = {"entries": [
            {"id": "s", "category": "learnings", "last_verified": OLD},
            {"id": "f", "category": "learnings", "last_verified": AS_OF},
            {"id": "r", "category": "references", "last_verified": OLD},
        ]}
        reported = {e.get("id") for (e, _a)
                    in CLI.stale_entries(data, CLI.STALE_MAX_AGE_DAYS, today=AS_OF)}
        downweighted = {e["id"] for e in data["entries"]
                        if CLI.staleness_factor(e, set(), AS_OF)
                        == CLI.STALENESS_STALE_MULT}
        self.assertEqual(reported, downweighted)


class DownweightRankingTest(SyntheticKBTestCase):
    """Integration: the factor actually reorders under bm25+acr, and never bm25."""

    def _rank(self, entries, strategy):
        super().setUp()  # fresh kdir
        self.index_data = build_synthetic_kb(self.kdir, entries=entries)
        if strategy == "bm25":
            return CLI.retrieve_bm25(self.kdir, self.index_data, QUERY)
        return CLI.retrieve_bm25_acr(self.kdir, self.index_data, QUERY, AS_OF)

    def test_superseded_ranks_below_fresh_peer_only_under_acr(self):
        # keep vs super are IDENTICAL except super has a newer `created` (so plain
        # bm25 tie-break puts super FIRST) and a declared superseder. Both share
        # last_verified=AS_OF, so acr is identical -> ONLY the supersede factor can
        # move them. Plain bm25: [super, keep]; bm25+acr flips to [keep, super].
        entries = [
            _e("keep", "keep", "references", "2026-05-01", AS_OF),
            _e("super", "super", "references", "2026-06-01", AS_OF),
            _e("newer", "newer", "references", "2026-06-10", AS_OF,
               relates=["supersedes:super"]),
        ]
        plain = self._rank(entries, "bm25")
        acr = self._rank(entries, "bm25+acr")
        self.assertLess(plain.index("super"), plain.index("keep"),
                        "plain bm25 tie-break should put the newer-created first")
        self.assertLess(acr.index("keep"), acr.index("super"),
                        "the supersede downweight must sink 'super' below 'keep'")

    def test_stale_volatile_ranks_below_equally_old_nonvolatile_peer(self):
        # vol (learnings) and nonvol (references) share an OLD last_verified -> acr
        # freshness is identical (both floored). vol has newer `created` so plain
        # bm25 puts it FIRST; under bm25+acr only vol is stale-downweighted (x0.5),
        # so nonvol overtakes it. Isolates STALENESS_STALE_MULT from acr-freshness.
        entries = [
            _e("nonvol", "nonvol", "references", "2026-05-01", OLD),
            _e("vol", "vol", "learnings", "2026-06-01", OLD),
        ]
        plain = self._rank(entries, "bm25")
        acr = self._rank(entries, "bm25+acr")
        self.assertLess(plain.index("vol"), plain.index("nonvol"))
        self.assertLess(acr.index("nonvol"), acr.index("vol"),
                        "the stale volatile entry must sink below its fresh peer")

    def test_superseded_ranks_below_merely_stale_head_to_head(self):
        # both and merely are IDENTICAL old learnings (same bm25, same acr since
        # same last_verified) except `both` is ALSO superseded. `both` has a newer
        # created so plain bm25 puts it FIRST; under bm25+acr the superseded x0.3
        # must sink it BELOW the merely-stale x0.5. This is the head-to-head that
        # a 0.3<->0.5 swap would invert — the ranking-flip tests above can't see it
        # because they only need factor < 1.0.
        entries = [
            _e("merely", "merely", "learnings", "2026-05-01", OLD),
            _e("both", "both", "learnings", "2026-06-01", OLD),
            _e("sup", "sup", "references", "2026-06-05", AS_OF,
               relates=["supersedes:both"]),
        ]
        plain = self._rank(entries, "bm25")
        acr = self._rank(entries, "bm25+acr")
        self.assertLess(plain.index("both"), plain.index("merely"),
                        "plain bm25 tie-break puts the newer-created first")
        self.assertLess(acr.index("merely"), acr.index("both"),
                        "superseded (0.3) must sink below merely-stale (0.5)")

    def test_plain_bm25_never_calls_staleness_factor(self):
        # Structural proof of the gold-fixture invariant: sabotage the factor to
        # raise, and plain bm25 must still work byte-identically while bm25+acr
        # would blow up. (Restored in finally.)
        entries = [
            _e("a", "a", "learnings", "2026-06-01", OLD),
            _e("b", "b", "learnings", "2026-05-01", AS_OF),
        ]
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=entries)
        baseline = CLI.retrieve_bm25(self.kdir, self.index_data, QUERY)
        orig = CLI.staleness_factor

        def boom(*a, **k):
            raise AssertionError("staleness_factor must NOT be on the bm25 path")

        CLI.staleness_factor = boom
        try:
            got = CLI.retrieve_bm25(self.kdir, self.index_data, QUERY)
            self.assertEqual(got, baseline)  # plain bm25 unaffected
            with self.assertRaises(AssertionError):
                CLI.retrieve_bm25_acr(self.kdir, self.index_data, QUERY, AS_OF)
        finally:
            CLI.staleness_factor = orig

    def test_zero_bm25_entry_never_surfaced(self):
        entries = [_e("s", "s", "learnings", "2026-06-01", OLD)]
        ranked = self._rank(entries, "bm25+acr")
        # A query term in no entry -> empty; the bounded factor can't lift 0.
        empty = CLI.retrieve_bm25_acr(self.kdir, self.index_data,
                                      "zzzznotpresent", AS_OF)
        self.assertEqual(empty, [])
        self.assertIn("s", ranked)  # sanity: the real query does surface it

    def test_fresh_only_kb_bm25_acr_order_equals_plain(self):
        # With no stale/superseded entries every factor is 1.0, so bm25+acr differs
        # from plain bm25 ONLY by acr provenance/freshness — never by staleness.
        entries = [
            _e("p", "p", "references", "2026-06-01", AS_OF, source="user"),
            _e("q", "q", "references", "2026-06-01", AS_OF, source="user"),
        ]
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=entries)
        for e in self.index_data["entries"]:
            self.assertEqual(
                CLI.staleness_factor(e, CLI.superseded_ids(self.index_data),
                                     AS_OF), 1.0)

    def test_recall_ranked_bm25_acr_matches_retrieve_bm25_acr(self):
        # The two downweight sites must agree (same superseded set, same factor).
        entries = [
            _e("keep", "keep", "references", "2026-05-01", AS_OF),
            _e("super", "super", "references", "2026-06-01", AS_OF),
            _e("newer", "newer", "references", "2026-06-10", AS_OF,
               relates=["supersedes:super"]),
        ]
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=entries)
        via_fn = CLI.retrieve_bm25_acr(self.kdir, self.index_data, QUERY, AS_OF)
        via_ranked = [e.get("id") for (e, _s, _t) in CLI.recall_ranked(
            self.kdir, self.index_data, QUERY, strategy="bm25+acr", as_of=AS_OF)]
        self.assertEqual(via_fn, via_ranked)


if __name__ == "__main__":
    import unittest
    unittest.main()
