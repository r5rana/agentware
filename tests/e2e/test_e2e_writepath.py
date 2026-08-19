"""Task 7 (P1.7) — end-to-end: the whole write-path invariant set holds TOGETHER.

One >=2,000-entry scratch KB (git-tracked, with a work feature), driven through the
six fixes in sequence to prove they compose:
  1. a warm freshness check reads ZERO entry bodies AND ranking is byte-identical
     cache-on vs cache-off (INV-1);
  2. a full rebuild is sort-once (single sort pass) and entries byte-identical
     across rebuilds (mod the generation field);
  3. deleting index.json → the next command auto-rebuilds + succeeds (clone not
     bricked) and the derived files are gitignored/untracked;
  4. two concurrent load->add->save PROCESSES both survive (no lost update);
  5. one poison file is quarantined yet the rest rebuilds (nothing-lost gate intact);
  6. a warm --scope work recall reads ZERO work bodies.

NEVER the operator KB (AGENTWARE_KNOWLEDGE_DIR override, R-LOC-03). Stdlib unittest.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, run_cli, CLI_PATH
except ImportError:
    from _fixtures import load_cli, run_cli, CLI_PATH


CLI = load_cli()
N_ENTRIES = 2000
HAVE_GIT = shutil.which("git") is not None


def _git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=False)


def _build_kb(root, n=N_ENTRIES):
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
            "summary": "Synthetic entry %d token qx%d." % (i, i),
            "author": "test", "source": "agent",
            "last_verified": "2026-02-%02d" % ((i % 28) + 1),
        }
        with open(os.path.join(root, cat, "%s.md" % eid), "w",
                  encoding="utf-8") as f:
            f.write(CLI.render_frontmatter(fields)
                    + "# %s\n\nbody scale retrieval token qx%d.\n" % (eid, i))
    # A work feature (episodic, unindexed) for check 6.
    wd = os.path.join(root, "work", "260712-e2e")
    os.makedirs(wd, exist_ok=True)
    with open(os.path.join(wd, "plan.md"), "w", encoding="utf-8") as f:
        f.write("# E2E Plan\n\n> Status: ready\n\nWORKTOKEN scale body\n")


@unittest.skipUnless(HAVE_GIT, "git not available")
class E2EWritePathTest(unittest.TestCase):
    def test_e2e_write_path_invariants_hold_together(self):
        kdir = tempfile.mkdtemp(prefix="aw-e2e-writepath-")
        self.addCleanup(shutil.rmtree, kdir, True)
        _build_kb(kdir)
        _git(kdir, "init", "-q", "-b", "main")
        _git(kdir, "config", "user.email", "t@e.com")
        _git(kdir, "config", "user.name", "T")

        # Rebuild → index.json (with generation) + postings cache + gitignore.
        code, _o, err = run_cli(["index", "rebuild"], kdir)
        self.assertEqual(code, 0, err)

        # (1) freshness reads ZERO entry bodies + ranking byte-identical on/off.
        data, lerr = CLI.load_index(kdir)
        self.assertIsNone(lerr, lerr)
        cache = CLI.load_bm25_cache(kdir)
        self.assertIsNotNone(cache)
        calls = [0]
        real_rb = CLI._read_entry_body
        CLI._read_entry_body = lambda k, e: (calls.__setitem__(0, calls[0] + 1)
                                             or real_rb(k, e))
        try:
            self.assertTrue(CLI.bm25_cache_is_fresh(cache, data, kdir))
        finally:
            CLI._read_entry_body = real_rb
        self.assertEqual(calls[0], 0, "freshness read entry bodies (B1)")
        _c, out_on, _e = run_cli(["recall", "scale retrieval qx7",
                                  "--format", "json"], kdir)
        shutil.rmtree(os.path.join(kdir, ".cache"), ignore_errors=True)
        _c, out_off, _e = run_cli(["recall", "scale retrieval qx7",
                                   "--format", "json"], kdir)
        self.assertEqual(out_on, out_off, "cache-on != cache-off (INV-1)")
        run_cli(["index", "rebuild"], kdir)  # restore cache

        # (2) rebuild is sort-once + entries byte-identical across rebuilds.
        d1, p1 = CLI.build_index_from_frontmatter(kdir)
        d2, p2 = CLI.build_index_from_frontmatter(kdir)
        self.assertEqual(p1, [])
        self.assertEqual(d1["entries"], d2["entries"])
        keys = [(e["category"], e["id"]) for e in d1["entries"]]
        self.assertEqual(keys, sorted(keys))
        key_calls = [0]
        real_key = CLI._entry_key
        CLI._entry_key = lambda e: (key_calls.__setitem__(0, key_calls[0] + 1)
                                    or real_key(e))
        try:
            CLI.build_index_from_frontmatter(kdir)
        finally:
            CLI._entry_key = real_key
        self.assertEqual(key_calls[0], N_ENTRIES, "not a single sort pass (B4)")

        # (3) delete index.json → next command auto-rebuilds + succeeds; derived
        #     files are gitignored/untracked.
        os.remove(os.path.join(kdir, "index.json"))
        code, _o, err = run_cli(["recall", "qx123", "--format", "json"], kdir)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(os.path.join(kdir, "index.json")))
        for rel in CLI.KB_DERIVED_GITIGNORE_RELS:
            self.assertEqual(_git(kdir, "check-ignore", "-q", rel).returncode, 0,
                             "derived file not gitignored: %s" % rel)

        # (4) two concurrent load->add->save PROCESSES both survive.
        env = dict(os.environ, AGENTWARE_KNOWLEDGE_DIR=kdir)
        env.pop("AGENTWARE_DREAM", None)
        procs = [subprocess.Popen(
            [sys.executable, CLI_PATH, "learn", "--topic", "e2e-conc-%d" % i,
             "--summary", "c%d" % i, "--tags", "c",
             "--content", "concurrent e2e body %d wvz%d" % (i, i), "--force"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for i in range(4)]
        for p in procs:
            p.communicate()
            self.assertEqual(p.returncode, 0)
        ids = {e["id"] for e in CLI.load_index(kdir)[0]["entries"]}
        for i in range(4):
            self.assertIn("learn-e2e-conc-%d" % i, ids, "lost a concurrent add")

        # (5) one poison file quarantined; the rest still rebuilds.
        with open(os.path.join(kdir, "learnings", "poison.md"), "w",
                  encoding="utf-8") as f:
            f.write("---\ntitle: no id\ncategory: learnings\n---\nbody\n")
        code, _o, err = run_cli(["index", "rebuild"], kdir)
        self.assertEqual(code, 0, err)
        self.assertIn("QUARANTINED", err)
        ids = {e["id"] for e in CLI.load_index(kdir)[0]["entries"]}
        self.assertIn("lear-e00000", ids)          # a normal entry survives
        self.assertIn("learn-e2e-conc-0", ids)      # the concurrent add survives

        # (6) warm --scope work recall reads ZERO work bodies.
        CLI.build_work_corpus(kdir)  # cold populate
        wcalls = [0]
        real_wt = CLI._read_work_text
        CLI._read_work_text = lambda p: (wcalls.__setitem__(0, wcalls[0] + 1)
                                         or real_wt(p))
        try:
            corpus = CLI.build_work_corpus(kdir)
        finally:
            CLI._read_work_text = real_wt
        self.assertEqual(wcalls[0], 0, "warm --scope work read bodies (B8)")
        self.assertTrue(corpus)


if __name__ == "__main__":
    unittest.main()
