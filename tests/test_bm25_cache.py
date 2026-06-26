"""Tests for the derived BM25 postings cache (feature 260625-kb-cached-inverted-index).

Invariants under test:
  * INV-1 byte-identical ranking: the cache-backed recall path produces
    byte-for-byte identical `recall --format json` to the live scan over the
    synthetic KB PLUS adversarial queries (empty / single-term / many-term /
    unknown-term / high-df term). The cached scorer reproduces `bm25_scores`
    exactly (same idf, same k1/b length-norm, same summation) so rounded scores
    match with no tolerance.
  * INV-2 read-only: recall/eval NEVER write or mutate the cache; the sole writer
    is `index rebuild` (rebuild_kb).
  * Derived artifact (C-1/C-3): byte-stable build (sorted keys), regenerable from
    frontmatter+bodies, gitignored, never source of truth.
  * Staleness + graceful fallback: missing / corrupt / version- or
    fingerprint-mismatched cache => load None / not-fresh => live scan, never a
    crash, never a wrong answer.

Stdlib-only (no pytest). The cache is materialized by directly invoking the
builder (the same code `index rebuild` runs) so the tests need no embedder/network.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests._fixtures import (load_cli, run_cli, build_synthetic_kb,
                                 build_large_loop_kb)
except ImportError:  # direct invocation
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _fixtures import (load_cli, run_cli, build_synthetic_kb,
                           build_large_loop_kb)


CLI = load_cli()


def _cache_path(kdir):
    return CLI.bm25_cache_path(kdir)


def _materialize_cache(kdir, data):
    """Write the postings cache exactly as `index rebuild`/rebuild_kb does."""
    CLI.write_bm25_cache(kdir, CLI.build_bm25_cache(kdir, data))


class BM25CacheTestCase(unittest.TestCase):
    """Synthetic KB with the postings cache materialized (cache-present state)."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-bm25cache-")
        self.data = build_synthetic_kb(self.kdir)

    def tearDown(self):
        shutil.rmtree(self.kdir, ignore_errors=True)


# --- Task 1: constants + module surface --------------------------------------
class ConstantsTest(BM25CacheTestCase):
    def test_constants_exist(self):
        self.assertTrue(CLI.BM25_CACHE_REL.endswith("bm25-postings.json"))
        self.assertTrue(CLI.BM25_CACHE_REL.startswith(".cache"))
        self.assertIsInstance(CLI.BM25_CACHE_VERSION, int)
        # The full feature surface is importable.
        for name in ("build_bm25_cache", "write_bm25_cache", "load_bm25_cache",
                     "bm25_cache_is_fresh", "bm25_scores_cached",
                     "bm25_positive_scores"):
            self.assertTrue(hasattr(CLI, name), name)


# --- Task 2: deterministic byte-stable build ---------------------------------
class BuildDeterminismTest(BM25CacheTestCase):
    def test_build_is_byte_stable_across_runs(self):
        a = json.dumps(CLI.build_bm25_cache(self.kdir, self.data),
                       sort_keys=True, ensure_ascii=False)
        b = json.dumps(CLI.build_bm25_cache(self.kdir, self.data),
                       sort_keys=True, ensure_ascii=False)
        self.assertEqual(a, b)

    def test_cache_df_avgdl_n_match_bm25_index(self):
        corpus = CLI.build_corpus(self.kdir, self.data)
        docs, df, avgdl = CLI._bm25_index(corpus)
        cache = CLI.build_bm25_cache(self.kdir, self.data)
        self.assertEqual(cache["n"], len(docs))
        self.assertEqual(cache["avgdl"], avgdl)
        self.assertEqual(cache["df"], df)
        # Postings are ordered by doc_id for every term (byte-stable).
        for term, plist in cache["postings"].items():
            ids = [p[0] for p in plist]
            self.assertEqual(ids, sorted(ids), term)


# --- Task 3: load + freshness ------------------------------------------------
class LoadAndFreshnessTest(BM25CacheTestCase):
    def test_missing_returns_none(self):
        self.assertIsNone(CLI.load_bm25_cache(self.kdir))

    def test_corrupt_json_returns_none_no_raise(self):
        path = _cache_path(self.kdir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ this is : not json ]")
        self.assertIsNone(CLI.load_bm25_cache(self.kdir))

    def test_version_mismatch_returns_none(self):
        cache = CLI.build_bm25_cache(self.kdir, self.data)
        cache["version"] = CLI.BM25_CACHE_VERSION + 999
        path = _cache_path(self.kdir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        self.assertIsNone(CLI.load_bm25_cache(self.kdir))

    def test_unchanged_kb_is_fresh(self):
        cache = CLI.build_bm25_cache(self.kdir, self.data)
        self.assertTrue(CLI.bm25_cache_is_fresh(cache, self.data, self.kdir))

    def test_mutated_body_is_not_fresh(self):
        cache = CLI.build_bm25_cache(self.kdir, self.data)
        body = os.path.join(self.kdir, self.data["entries"][0]["path"])
        with open(body, "a", encoding="utf-8") as f:
            f.write("\nNEWWORD distinctmutationmarker\n")
        self.assertFalse(CLI.bm25_cache_is_fresh(cache, self.data, self.kdir))


# --- Task 4: cached scorer == live scorer, exactly ---------------------------
class ScorerParityTest(BM25CacheTestCase):
    QUERIES = [
        "geofence arrive reminders",
        "python stdlib runtime dependency",
        "bm25 saturation inverse document frequency",
        "macos timeout",
        "retrieval",                      # high-df-ish single term
        "unknownterm zzz nothingmatches",  # unknown terms
        "",                                # empty
    ]

    def test_cached_scores_equal_live_positive_subset(self):
        cache = CLI.build_bm25_cache(self.kdir, self.data)
        entries_by_id = {e.get("id"): e for e in self.data["entries"]}
        for q in self.QUERIES:
            corpus = CLI.build_corpus(self.kdir, self.data)
            live = {e.get("id"): round(s, 6)
                    for (e, s) in CLI.bm25_scores(corpus, CLI.tokenize(q))
                    if s > 0.0}
            cached = {e.get("id"): round(s, 6)
                      for (e, s) in CLI.bm25_scores_cached(
                          cache, CLI.tokenize(q), entries_by_id)}
            self.assertEqual(cached, live, "scorer mismatch for query %r" % q)


# --- Tasks 5/7/8: cache-on == cache-off recall (the headline guard) ----------
class CacheOnEqualsCacheOffTest(BM25CacheTestCase):
    """INV-1 headline guard: byte-identical `recall --format json` with the cache
    present (fresh) vs. absent (live scan)."""

    ADVERSARIAL = [
        "",                                  # empty query
        "geofence",                          # single term
        "geofence arrive reminders ios expo background location task",  # many term
        "zzz nonexistentterm qqqq",          # unknown terms
        "bm25",                              # high-df-ish term
        "python stdlib runtime dependency",  # normal
    ]

    def test_recall_byte_identical_cache_on_vs_off(self):
        for q in self.ADVERSARIAL:
            # Cache OFF (no cache file present) — the live scan.
            shutil.rmtree(os.path.join(self.kdir, ".cache"), ignore_errors=True)
            code_off, out_off, err_off = run_cli(
                ["recall", q, "--format", "json"], self.kdir)
            self.assertEqual(code_off, 0, err_off)
            # Cache ON (fresh postings cache present).
            _materialize_cache(self.kdir, self.data)
            self.assertTrue(CLI.bm25_cache_is_fresh(
                CLI.load_bm25_cache(self.kdir), self.data, self.kdir))
            code_on, out_on, err_on = run_cli(
                ["recall", q, "--format", "json"], self.kdir)
            self.assertEqual(code_on, 0, err_on)
            # BYTE-IDENTICAL stdout (INV-1).
            self.assertEqual(out_off, out_on,
                             "cache-on != cache-off for query %r" % q)

    def test_recall_acr_byte_identical_cache_on_vs_off(self):
        for q in ("geofence arrive reminders", "python stdlib runtime"):
            shutil.rmtree(os.path.join(self.kdir, ".cache"), ignore_errors=True)
            _, out_off, _ = run_cli(
                ["recall", q, "--strategy", "bm25+acr", "--as-of",
                 "2026-06-01", "--format", "json"], self.kdir)
            _materialize_cache(self.kdir, self.data)
            _, out_on, _ = run_cli(
                ["recall", q, "--strategy", "bm25+acr", "--as-of",
                 "2026-06-01", "--format", "json"], self.kdir)
            self.assertEqual(out_off, out_on, "acr cache parity for %r" % q)

    def test_category_scoped_recall_uses_live_and_matches(self):
        # The whole-corpus cache must NOT serve a category filter; recall stays
        # correct via the live scan and is identical with the cache present.
        _materialize_cache(self.kdir, self.data)
        _, out_on, _ = run_cli(
            ["recall", "ranking", "--category", "references",
             "--format", "json"], self.kdir)
        shutil.rmtree(os.path.join(self.kdir, ".cache"), ignore_errors=True)
        _, out_off, _ = run_cli(
            ["recall", "ranking", "--category", "references",
             "--format", "json"], self.kdir)
        self.assertEqual(out_on, out_off)


# --- Task 7: graceful fallback on a corrupt / stale cache --------------------
class FallbackGuardTest(BM25CacheTestCase):
    def test_corrupt_cache_falls_back_to_live(self):
        # Baseline: no cache (live scan).
        _, out_live, _ = run_cli(
            ["recall", "geofence reminders", "--format", "json"], self.kdir)
        # Corrupt cache present => silently ignored, identical output.
        path = _cache_path(self.kdir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("not json at all {{{")
        code, out_corrupt, err = run_cli(
            ["recall", "geofence reminders", "--format", "json"], self.kdir)
        self.assertEqual(code, 0, err)
        self.assertEqual(out_live, out_corrupt)

    def test_stale_cache_falls_back_to_live(self):
        # Fresh cache, then mutate a body so the cache is stale; recall must NOT
        # use the stale cache — output equals a clean live scan of the new state.
        _materialize_cache(self.kdir, self.data)
        body = os.path.join(self.kdir, self.data["entries"][0]["path"])
        with open(body, "a", encoding="utf-8") as f:
            f.write("\nfreshtoken uniquemutation\n")
        _, out_stale_present, _ = run_cli(
            ["recall", "freshtoken uniquemutation", "--format", "json"],
            self.kdir)
        shutil.rmtree(os.path.join(self.kdir, ".cache"), ignore_errors=True)
        _, out_no_cache, _ = run_cli(
            ["recall", "freshtoken uniquemutation", "--format", "json"],
            self.kdir)
        self.assertEqual(out_stale_present, out_no_cache)
        # And the new token IS surfaced (proves the live scan saw the mutation).
        self.assertTrue(json.loads(out_no_cache)["results"])


# --- INV-2: recall/eval never write the cache --------------------------------
class ReadOnlyTest(BM25CacheTestCase):
    def test_recall_does_not_create_or_mutate_cache(self):
        # No cache present: recall must NOT create one (sole writer is rebuild).
        run_cli(["recall", "geofence reminders", "--format", "json"], self.kdir)
        self.assertFalse(os.path.exists(_cache_path(self.kdir)),
                         "recall must not write the cache (INV-2)")
        # Cache present: recall must leave its bytes unchanged.
        _materialize_cache(self.kdir, self.data)
        with open(_cache_path(self.kdir), "rb") as f:
            before = f.read()
        run_cli(["recall", "python ranking", "--format", "json"], self.kdir)
        with open(_cache_path(self.kdir), "rb") as f:
            self.assertEqual(before, f.read())


# --- Task 9 (scale micro-benchmark): cache parity at K >> gold ---------------
class ScaleParityTest(unittest.TestCase):
    def test_recall_identical_at_scale_cache_on_vs_off(self):
        kdir = tempfile.mkdtemp(prefix="aw-bm25scale-")
        try:
            build_large_loop_kb(kdir, n_features=20)
            data, err = CLI.load_index(kdir)
            self.assertIsNone(err, err)
            q = "loop iteration task feature work"
            shutil.rmtree(os.path.join(kdir, ".cache"), ignore_errors=True)
            _, out_off, _ = run_cli(["recall", q, "--format", "json"], kdir)
            _materialize_cache(kdir, data)
            _, out_on, _ = run_cli(["recall", q, "--format", "json"], kdir)
            self.assertEqual(out_off, out_on)
        finally:
            shutil.rmtree(kdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
