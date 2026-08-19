"""Task 6 (P1.7) — cache build_work_corpus (B8 remainder).

`build_work_corpus` gets a gitignored `.cache/` cache keyed on a (path,size,mtime)
stat digest over work/*.md. Proves:
  (a) a WARM `--scope work` recall re-reads ZERO work bodies (counter);
  (b) an in-place SAME-SIZE edit invalidates the cache (mtime moves) and the new
      content is recalled — proving (path,size) alone would be insufficient;
  (c) `--scope work` results are byte-identical cached vs uncached, and work/ stays
      UNINDEXED (episodic valve).

Stdlib-only unittest; throwaway temp KB (R-LOC-03).
"""

import json
import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, run_cli, build_synthetic_kb
except ImportError:
    from _fixtures import load_cli, run_cli, build_synthetic_kb


CLI = load_cli()


class WorkCacheTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-workcache-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)  # valid index (recall loads it first)
        self._mk_feature("260712-alpha",
                         "# Alpha Plan\n\n> Status: ready\n\nbody ALPHAWORD retrieval token\n")
        self._mk_feature("260712-beta",
                         "# Beta Plan\n\n> Status: draft\n\nbody BETAWORD retrieval token\n")

    def _mk_feature(self, name, plan_text):
        d = os.path.join(self.kdir, "work", name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as f:
            f.write(plan_text)

    def _cache_path(self):
        return os.path.join(self.kdir, CLI.WORK_CORPUS_CACHE_REL)

    def test_workcache_warm_call_reads_zero_work_bodies(self):
        CLI.build_work_corpus(self.kdir)  # cold: populates the cache
        self.assertTrue(os.path.exists(self._cache_path()))
        calls = [0]
        real = CLI._read_work_text

        def counting(p):
            calls[0] += 1
            return real(p)

        CLI._read_work_text = counting
        try:
            corpus = CLI.build_work_corpus(self.kdir)  # warm
        finally:
            CLI._read_work_text = real
        self.assertEqual(calls[0], 0, "warm --scope work read %d bodies (must be 0)"
                         % calls[0])
        self.assertTrue(corpus)

    def test_workcache_same_size_inplace_edit_invalidates(self):
        plan = os.path.join(self.kdir, "work", "260712-alpha", "plan.md")
        # Cold recall populates the cache and surfaces ALPHAWORD.
        _c, out1, _e = run_cli(
            ["recall", "ALPHAWORD", "--scope", "work", "--format", "json"],
            self.kdir)
        self.assertIn("260712-alpha", out1)
        with open(plan, encoding="utf-8") as f:
            orig = f.read()
        # SAME-SIZE replacement: "ALPHAWORD" (9) -> "ZULUWORD." (9).
        new = orig.replace("ALPHAWORD", "ZULUWORD.")
        self.assertEqual(len(new), len(orig))
        with open(plan, "w", encoding="utf-8") as f:
            f.write(new)
        # Move mtime forward so the (path,size,mtime) digest changes even though
        # size is identical (a (path,size)-only key would MISS this edit).
        st = os.stat(plan)
        os.utime(plan, (st.st_atime, st.st_mtime + 5))
        _c, out2, _e = run_cli(
            ["recall", "ZULUWORD", "--scope", "work", "--format", "json"],
            self.kdir)
        ids2 = [r.get("id") for r in json.loads(out2).get("results", [])]
        self.assertTrue(any("260712-alpha" in i for i in ids2),
                        "same-size edit was not picked up (cache not invalidated)")

    def test_workcache_results_identical_cached_vs_uncached(self):
        for q in ("ALPHAWORD retrieval", "BETAWORD", "retrieval token"):
            CLI.build_work_corpus(self.kdir)  # ensure cache present (warm)
            _c, out_on, _e = run_cli(
                ["recall", q, "--scope", "work", "--format", "json"], self.kdir)
            shutil.rmtree(os.path.join(self.kdir, ".cache"), ignore_errors=True)
            _c, out_off, _e = run_cli(
                ["recall", q, "--scope", "work", "--format", "json"], self.kdir)
            self.assertEqual(out_on, out_off, "cached != uncached for %r" % q)

    def test_workcache_work_stays_unindexed(self):
        run_cli(["recall", "retrieval", "--scope", "work", "--format", "json"],
                self.kdir)
        data, err = CLI.load_index(self.kdir)
        self.assertIsNone(err, err)
        for e in data["entries"]:
            self.assertNotEqual(e.get("category"), "work")
            self.assertFalse((e.get("path") or "").startswith("work/"))

    def test_workcache_cache_is_under_gitignored_cache_dir(self):
        CLI.build_work_corpus(self.kdir)
        self.assertTrue(CLI.WORK_CORPUS_CACHE_REL.startswith(".cache"))
        self.assertIn(".cache", CLI.KB_GITIGNORE_REQUIRED)
        self.assertTrue(os.path.exists(self._cache_path()))


if __name__ == "__main__":
    unittest.main()
