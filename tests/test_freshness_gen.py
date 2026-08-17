"""Task 4 (P1.7) — O(1) recall freshness via a monotonic `generation` token (B1).

`bm25_cache_is_fresh` compares an in-memory `generation` integer instead of
re-deriving the whole-corpus fingerprint (which read EVERY entry body on EVERY
recall, even warm). Proves:
  (a) a warm freshness check over a >=2,000-entry KB reads ZERO entry bodies;
  (b) a save_index mutation (learn) bumps generation → the next recall detects the
      stale cache and falls back to the live scan (and `index rebuild` re-freshens);
  (c) recall RANKING is byte-identical cache-on (fresh) vs cache-off (live), INV-1.

Stdlib-only unittest; throwaway temp KB (R-LOC-03).
"""

import json
import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, run_cli
except ImportError:
    from _fixtures import load_cli, run_cli


CLI = load_cli()

N_ENTRIES = 2000


def _build_large_fm_kb(root, n=N_ENTRIES):
    cats = ("learnings", "references", "configurations", "projects")
    for sub in cats:
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    for i in range(n):
        cat = cats[i % len(cats)]
        eid = "%s-e%05d" % (cat[:4], i)
        fields = {
            "id": eid, "title": "Entry %d" % i, "category": cat,
            "tags": ["scale", cat, "t%d" % (i % 9)],
            "created": "2026-01-%02d" % ((i % 28) + 1),
            "summary": "Synthetic entry %d covering token qx%d." % (i, i),
            "author": "test", "source": "agent",
            "last_verified": "2026-02-%02d" % ((i % 28) + 1),
        }
        with open(os.path.join(root, cat, "%s.md" % eid), "w",
                  encoding="utf-8") as f:
            f.write(CLI.render_frontmatter(fields)
                    + "# %s\n\nbody about scale retrieval token qx%d.\n" % (eid, i))


class FreshnessGenerationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kdir = tempfile.mkdtemp(prefix="aw-freshgen-")
        _build_large_fm_kb(cls.kdir)
        # index rebuild builds index.json (with generation) + the postings cache
        # (stamped at that generation).
        code, _o, err = run_cli(["index", "rebuild"], cls.kdir)
        assert code == 0, err

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.kdir, ignore_errors=True)

    def _load(self):
        data, err = CLI.load_index(self.kdir)
        self.assertIsNone(err, err)
        cache = CLI.load_bm25_cache(self.kdir)
        self.assertIsNotNone(cache, "cache should be present after rebuild")
        return data, cache

    def test_freshness_warm_check_reads_zero_entry_bodies(self):
        data, cache = self._load()
        self.assertTrue(CLI.bm25_cache_is_fresh(cache, data, self.kdir))
        calls = [0]
        real = CLI._read_entry_body

        def counting(kdir, entry):
            calls[0] += 1
            return real(kdir, entry)

        CLI._read_entry_body = counting
        try:
            for _ in range(5):  # repeated warm freshness checks
                self.assertTrue(CLI.bm25_cache_is_fresh(cache, data, self.kdir))
        finally:
            CLI._read_entry_body = real
        self.assertEqual(calls[0], 0,
                         "freshness read %d entry bodies (must be 0, B1)" % calls[0])

    def test_freshness_cache_header_has_generation_matching_index(self):
    # The name below is FROZEN. `tests/fixtures/nodeids-local.json` records the
    # pre-merge node ids as a preservation floor that may only ever grow, and the
    # gate reports a vanished id as "LOST node id" with no alias mechanism on
    # purpose: a renamed test and a deleted test are indistinguishable to anything
    # downstream holding the old name. So the clearer name above is the real test
    # and this one delegates to it, which keeps the floor intact AND leaves the
    # rename visible instead of trading one for the other.
        self.test_freshness_cache_header_carries_the_content_authority()

    def test_freshness_cache_header_carries_the_content_authority(self):
        # The header stamps the CONTENT keys the merged reader consults, not the
        # retired `generation` counter: the whole-corpus fingerprint (tier 1/3) and
        # the zero-body-read stat proxy (tier 2), both computed in the same call so
        # they can never describe different states of the tree. The version must be
        # the merged v3 — v1 (donor) and v2 (this tree's counter) are both burned at
        # this same cache path, so reusing either would let a foreign schema load.
        data, cache = self._load()
        self.assertIn("fingerprint", cache)
        self.assertTrue(cache["fingerprint"])
        self.assertIn("stat_signature", cache)
        self.assertNotIn("generation", cache)
        self.assertEqual(cache["version"], CLI.BM25_CACHE_VERSION)
        self.assertTrue(CLI.bm25_cache_is_fresh(cache, data, self.kdir))

    def test_freshness_learn_bump_detects_staleness_then_rebuild_refreshens(self):
        data, cache = self._load()
        self.assertTrue(CLI.bm25_cache_is_fresh(cache, data, self.kdir))
        # A learn bumps the index generation but does NOT rebuild the cache.
        code, _o, err = run_cli(
            ["learn", "--topic", "freshgen-newentry", "--summary", "s",
             "--tags", "t", "--content", "unique zqwx body for freshness test",
             "--force"], self.kdir)
        self.assertEqual(code, 0, err)
        data2, err = CLI.load_index(self.kdir)
        self.assertIsNone(err, err)
        cache2 = CLI.load_bm25_cache(self.kdir)  # still stamped at the old gen
        self.assertFalse(CLI.bm25_cache_is_fresh(cache2, data2, self.kdir),
                         "generation bump must mark the cache stale")
        # recall falls back to the live scan → surfaces the new entry.
        _c, out, _e = run_cli(["recall", "zqwx", "--format", "json"], self.kdir)
        ids = [r.get("id") for r in json.loads(out).get("results", [])]
        self.assertIn("learn-freshgen-newentry", ids)
        # `index rebuild` re-stamps the cache at the current generation → fresh.
        code, _o, err = run_cli(["index", "rebuild"], self.kdir)
        self.assertEqual(code, 0, err)
        data3, _e = CLI.load_index(self.kdir)
        cache3 = CLI.load_bm25_cache(self.kdir)
        self.assertTrue(CLI.bm25_cache_is_fresh(cache3, data3, self.kdir))

    def test_freshness_ranking_byte_identical_cache_on_vs_off(self):
        # INV-1: fresh cache-backed recall == cache-less live scan, byte-for-byte.
        # (Rebuild first so the cache is fresh regardless of test ordering.)
        code, _o, err = run_cli(["index", "rebuild"], self.kdir)
        self.assertEqual(code, 0, err)
        for q in ("scale retrieval token", "qx7", "entry synthetic", "qx123"):
            _c, out_on, _e = run_cli(["recall", q, "--format", "json"], self.kdir)
            self.assertEqual(_c, 0, _e)
            shutil.rmtree(os.path.join(self.kdir, ".cache"), ignore_errors=True)
            _c, out_off, _e = run_cli(["recall", q, "--format", "json"], self.kdir)
            self.assertEqual(out_on, out_off, "cache-on != cache-off for %r" % q)
            # Restore the cache for the next iteration.
            run_cli(["index", "rebuild"], self.kdir)


class FreshnessMonotonicTest(unittest.TestCase):
    """Regression: a rebuild must never let a PRIOR-rebuild cache false-match
    (adversarial-review finding). Originally a rebuild reset the monotonic
    `generation` to 1, so a stale cache also stamped 1 matched a freshly-rebuilt
    index and recall silently served stale postings.

    Task 24 replaced that counter with the whole-corpus CONTENT fingerprint, which
    has the property natively and more strongly: different content is a different
    fingerprint, with no sequence to reset. So the regression is asserted against
    the live mechanism — the invariant it protects is unchanged, and the counter it
    was written for no longer exists to assert."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-freshmono-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        _build_large_fm_kb(self.kdir, n=40)

    def _fp(self):
        """The stored whole-corpus fingerprint — the freshness authority."""
        data, _e = CLI.load_index(self.kdir)
        return data.get("corpus_fingerprint")

    def _add_entry(self, eid, token):
        fields = {"id": eid, "title": eid, "category": "learnings",
                  "tags": ["mono"], "created": "2026-03-01",
                  "summary": "s", "author": "t", "source": "agent",
                  "last_verified": "2026-03-01"}
        with open(os.path.join(self.kdir, "learnings", "%s.md" % eid), "w",
                  encoding="utf-8") as f:
            f.write(CLI.render_frontmatter(fields) + "# %s\n\n%s body\n"
                    % (eid, token))

    def test_freshness_generation_strictly_monotonic_across_rebuilds(self):
        # FROZEN NAME. `tests/fixtures/nodeids-local.json` records the pre-merge
        # node ids as a preservation floor with no alias mechanism, so the name
        # stays and delegates to the clearly-named test below.
        self.test_freshness_rebuild_never_lets_a_prior_cache_false_match()

    def test_freshness_rebuild_never_lets_a_prior_cache_false_match(self):
        run_cli(["index", "rebuild"], self.kdir)
        f1, cache1 = self._fp(), CLI.load_bm25_cache(self.kdir)
        self._add_entry("learn-mono-a", "monotoken1")
        run_cli(["index", "rebuild"], self.kdir)
        f2 = self._fp()
        self._add_entry("learn-mono-b", "monotoken2")
        run_cli(["index", "rebuild"], self.kdir)
        f3 = self._fp()
        # Every rebuild over CHANGED content yields a different authority value.
        self.assertTrue(f1 and f2 and f3)
        self.assertNotEqual(f2, f1, "rebuild reused the prior corpus fingerprint")
        self.assertNotEqual(f3, f2, "rebuild reused the prior corpus fingerprint")
        self.assertNotEqual(f3, f1)
        # And the point of all of it: the FIRST rebuild's cache must not read as
        # fresh against the third rebuild's index. Under the retired counter this
        # is what failed, because a reset put both back at 1.
        index3, _e = CLI.load_index(self.kdir)
        self.assertFalse(CLI.bm25_cache_is_fresh(cache1, index3, self.kdir),
                         "a prior-rebuild cache false-matched a rebuilt index")

    def test_freshness_stale_cache_after_failed_rebuild_write_is_detected(self):
        # Rebuild → cache gen g1, content C1.
        run_cli(["index", "rebuild"], self.kdir)
        old_cache = CLI.load_bm25_cache(self.kdir)
        self.assertIsNotNone(old_cache)
        # Add an entry, then rebuild with the cache WRITE forced to fail: index
        # bumps + the OLD cache survives (atomic write → os.replace never happens).
        self._add_entry("learn-failwrite", "zephyrtoken")
        real = CLI.write_bm25_cache
        CLI.write_bm25_cache = lambda k, c: (_ for _ in ()).throw(OSError("boom"))
        try:
            run_cli(["index", "rebuild"], self.kdir)
        finally:
            CLI.write_bm25_cache = real
        new_index, _e = CLI.load_index(self.kdir)
        surviving = CLI.load_bm25_cache(self.kdir)
        self.assertIsNotNone(surviving, "atomic write should leave the OLD cache")
        self.assertEqual(surviving["fingerprint"], old_cache["fingerprint"])
        # The surviving stale cache MUST be detected as stale — NOT false-fresh — so
        # recall falls back to the live scan. The cache that survived the failed
        # write describes the corpus BEFORE the new entry, so its fingerprint cannot
        # match the rebuilt one; this is the assertion that makes the atomic write
        # safe rather than merely tidy.
        self.assertFalse(CLI.bm25_cache_is_fresh(surviving, new_index, self.kdir),
                         "stale cache after a failed rebuild-write must NOT be fresh")
        # And recall surfaces the new entry (live fallback), never silently drops it.
        _c, out, _e = run_cli(["recall", "zephyrtoken", "--format", "json"],
                              self.kdir)
        ids = [r.get("id") for r in json.loads(out).get("results", [])]
        self.assertIn("learn-failwrite", ids)


if __name__ == "__main__":
    unittest.main()
