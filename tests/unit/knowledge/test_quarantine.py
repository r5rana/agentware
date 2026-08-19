"""Task 5 (P1.7) — fault-isolating rebuild (B9).

`rebuild_kb` QUARANTINES a malformed / duplicate-id file (skip + structured WARN,
rebuild the rest) instead of aborting the WHOLE rebuild (which wrote nothing and
staled the index for everyone). Proves:
  (a) a KB with one malformed-frontmatter file still rebuilds the rest and REPORTS
      the quarantined file (never silently dropped);
  (a2) a duplicate-id collision is de-duplicated (first kept) + reported;
  (b) the nothing-lost gate is UNCHANGED — it calls build_index_from_frontmatter
      directly and still treats a malformed/duplicate tree as fatal (quarantine is
      scoped to the normal `index rebuild` path, not the merge-gate path);
  (c) the existing WRITE-time duplicate-id rejection is intact.

Stdlib-only unittest; throwaway temp KB (R-LOC-03).
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, run_cli, build_synthetic_kb
except ImportError:
    from _fixtures import load_cli, run_cli, build_synthetic_kb


CLI = load_cli()
HAVE_GIT = shutil.which("git") is not None


def _git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=True)


def _write_collision(kdir, stem, eid):
    fields = {"id": eid, "title": stem, "category": "learnings",
              "tags": ["dup"], "created": "2026-07-12", "summary": "s",
              "author": "t", "source": "agent", "last_verified": "2026-07-12"}
    with open(os.path.join(kdir, "learnings", "%s.md" % stem), "w",
              encoding="utf-8") as f:
        f.write(CLI.render_frontmatter(fields) + "# %s\n\nbody\n" % stem)


def _write_raw(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class QuarantineRebuildTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-quarantine-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)  # 4 valid frontmatter entries

    def _index_ids(self):
        data, err = CLI.load_index(self.kdir)
        self.assertIsNone(err, err)
        return {e["id"] for e in data["entries"]}

    def test_quarantine_malformed_file_skipped_rest_rebuilt_and_reported(self):
        # A file with frontmatter but NO id is malformed.
        _write_raw(os.path.join(self.kdir, "learnings", "malformed.md"),
                   "---\ntitle: No Id Here\ncategory: learnings\n---\nbody\n")
        code, _out, err = run_cli(["index", "rebuild"], self.kdir)
        self.assertEqual(code, 0, err)  # rebuild SUCCEEDS (not aborted)
        # The offender is REPORTED (never silently dropped).
        self.assertIn("QUARANTINED", err)
        self.assertIn("malformed.md", err)
        # The valid entries all survive; the malformed file is NOT indexed.
        ids = self._index_ids()
        self.assertIn("learn-geofence-reminders", ids)
        self.assertIn("ref-bm25-ranking", ids)
        self.assertEqual(len(ids), 4)

    def test_quarantine_duplicate_id_deduped_and_reported(self):
        # Two files claim the SAME id; rebuild keeps the first (scan order),
        # skips + reports the duplicate, and rebuilds everything else.
        for stem in ("dup-a", "dup-b"):
            _write_raw(
                os.path.join(self.kdir, "learnings", "%s.md" % stem),
                "---\nid: learn-collide\ntitle: %s\ncategory: learnings\n"
                "tags: [dup]\ncreated: 2026-07-12\nsummary: s\n---\nbody %s\n"
                % (stem, stem))
        code, _out, err = run_cli(["index", "rebuild"], self.kdir)
        self.assertEqual(code, 0, err)
        self.assertIn("QUARANTINED", err)
        self.assertIn("duplicate id", err)
        data, lerr = CLI.load_index(self.kdir)
        self.assertIsNone(lerr, lerr)
        collide = [e for e in data["entries"] if e["id"] == "learn-collide"]
        self.assertEqual(len(collide), 1, "duplicate id must appear exactly once")
        # First scan-order file (dup-a.md) is the one kept.
        self.assertEqual(collide[0]["path"], "learnings/dup-a.md")

    def test_quarantine_build_reports_problems_for_gate_input(self):
        # The nothing-lost gate consumes build_index_from_frontmatter's problem
        # list; a malformed file MUST surface there (so the gate can fail).
        _write_raw(os.path.join(self.kdir, "learnings", "noid.md"),
                   "---\ntitle: x\ncategory: learnings\n---\nbody\n")
        _data, problems = CLI.build_index_from_frontmatter(self.kdir)
        self.assertTrue(problems)
        self.assertTrue(any("noid.md" in p for p in problems))


class NothingLostGateStrictTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-quarantine-gate-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)

    def test_quarantine_gate_still_fails_on_malformed_frontmatter(self):
        # The merge-gate path is NOT quarantined: a malformed file after a merge
        # still FAILS the nothing-lost gate (step-1 frontmatter check fires).
        _write_raw(os.path.join(self.kdir, "learnings", "broken.md"),
                   "---\ntitle: broken\ncategory: learnings\n---\nbody\n")
        ok, note = CLI.kb_nothing_lost_gate(self.kdir, [])
        self.assertFalse(ok, "gate must fail on a malformed tree (not quarantine)")
        self.assertIn("invalid frontmatter", note)

    def test_quarantine_gate_passes_clean_tree(self):
        ok, _note = CLI.kb_nothing_lost_gate(self.kdir, [])
        self.assertTrue(ok)


class WriteTimeDupRejectionTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-quarantine-writedup-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)

    def test_quarantine_write_time_duplicate_id_rejected(self):
        # First learn succeeds.
        code, _o, err = run_cli(
            ["learn", "--topic", "writedup", "--summary", "s", "--tags", "t",
             "--content", "first body zzz", "--force"], self.kdir)
        self.assertEqual(code, 0, err)
        # Second learn with the SAME topic (=> same id) is REJECTED at write time.
        code2, _o2, err2 = run_cli(
            ["learn", "--topic", "writedup", "--summary", "s2", "--tags", "t",
             "--content", "second body qqq", "--force"], self.kdir)
        self.assertNotEqual(code2, 0)
        self.assertTrue("already exists" in err2 or "duplicate" in err2, err2)


@unittest.skipUnless(HAVE_GIT, "git not available")
class PullReconcileNothingLostTest(unittest.TestCase):
    """Adversarial-review finding: the pull reconcile (_rebuild_kb_after_pull) has
    no nothing-lost gate (unlike push), so a same-id-different-file collision the
    pull introduces is quarantined out of the index — it must be REPORTED LOUDLY,
    never a silent success."""

    def test_quarantine_pull_reconcile_warns_on_dropped_collision(self):
        kdir = tempfile.mkdtemp(prefix="aw-quarantine-pull-")
        self.addCleanup(shutil.rmtree, kdir, True)
        build_synthetic_kb(kdir)
        _git(kdir, "init", "-q", "-b", "main")
        _git(kdir, "config", "user.email", "t@e.com")
        _git(kdir, "config", "user.name", "T")
        _write_collision(kdir, "collide-a", "learn-collide")
        _git(kdir, "add", "-A")
        _git(kdir, "commit", "-q", "-m", "before")
        before = _git(kdir, "rev-parse", "HEAD").stdout.strip()
        # A pulled commit introduces a SECOND file with the SAME id (cross-machine
        # collision that write-time rejection cannot prevent).
        _write_collision(kdir, "collide-b", "learn-collide")
        _git(kdir, "add", "-A")
        _git(kdir, "commit", "-q", "-m", "pulled collision")
        after = _git(kdir, "rev-parse", "HEAD").stdout.strip()

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rebuilt = CLI._rebuild_kb_after_pull(kdir, before, after)
        self.assertTrue(rebuilt)
        stderr = err.getvalue()
        self.assertIn("WARNING", stderr)
        self.assertIn("QUARANTINED", stderr)
        self.assertIn("learn-collide", stderr)
        # The kept entry is still recallable; nothing is lost from DISK.
        data, lerr = CLI.load_index(kdir)
        self.assertIsNone(lerr, lerr)
        collide = [e for e in data["entries"] if e["id"] == "learn-collide"]
        self.assertEqual(len(collide), 1)
        self.assertTrue(os.path.exists(os.path.join(kdir, "learnings",
                                                    "collide-b.md")))


if __name__ == "__main__":
    unittest.main()
