"""Tests for `prefer forget` safety guard + honest reversibility (Task 2,
260713-p2a-capture-hardening).

Runner:  python3 -m unittest tests.unit.ops.test_prefer_forget -v

`prefer forget <id>` revokes a CAPTURED preference. Audit MEDIUM: it previously
removed ANY index entry by id (no source/tag guard) and unlinked its `.md` with a
FALSE "reversible" promise. The guard now REQUIRES the target be a genuine capture
(`source=user` AND `tag=preference`); a non-preference id is refused (nonzero, no
`index remove`, no `.md` unlink). A real forget ARCHIVES the `.md` under
`logs/steering/forgotten/` first, so the revoke is genuinely reversible. The
`_prefer_capture` exact-key supersede path (guard OFF) is unaffected.
"""

import json
import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import run_cli, build_synthetic_kb, load_cli
except ImportError:
    from _fixtures import run_cli, build_synthetic_kb, load_cli


class TestPreferForget(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_prefer_forget_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        with open(os.path.join(self.kdir, ".initialized"), "w",
                  encoding="utf-8") as f:
            f.write("test\n")
        self.mod = load_cli()

    def _cli(self, argv):
        return run_cli(argv, self.kdir)

    def _index(self):
        with open(os.path.join(self.kdir, "index.json"), encoding="utf-8") as f:
            return json.load(f)

    def _entry(self, eid):
        return next((e for e in self._index()["entries"]
                     if e.get("id") == eid), None)

    # --- happy path: a genuine capture is forgotten AND archived -------------
    def test_forget_genuine_capture_removes_and_archives(self):
        self._cli(["prefer", "capture", "always pin dependency versions",
                   "--global"])
        eid = "learn-pref-global-always-pin-dependency-versions"
        e = self._entry(eid)
        self.assertIsNotNone(e)
        path = os.path.join(self.kdir, e["path"])
        self.assertTrue(os.path.exists(path))
        code, out, err = self._cli(["prefer", "forget", eid])
        self.assertEqual(code, 0, err)
        # Index entry gone + live .md unlinked...
        self.assertIsNone(self._entry(eid))
        self.assertFalse(os.path.exists(path), "live .md must be unlinked")
        # ...but genuinely reversible: the .md is archived out of the index tree.
        archived = os.path.join(self.kdir, "logs", "steering", "forgotten",
                                os.path.basename(e["path"]))
        self.assertTrue(os.path.exists(archived),
                        "a forgotten capture must be archived for reversibility")
        self.assertIn("archived", out)

    # --- guard: a NON-preference entry is refused (nothing touched) ----------
    def test_forget_non_preference_id_refused_no_delete(self):
        # A synthetic-KB learning: source != user, not preference-tagged.
        eid = "learn-macos-no-timeout"
        e = self._entry(eid)
        self.assertIsNotNone(e, "fixture entry present")
        path = os.path.join(self.kdir, e["path"])
        self.assertTrue(os.path.exists(path))
        code, _out, err = self._cli(["prefer", "forget", eid])
        self.assertNotEqual(code, 0, "must refuse a non-preference id")
        self.assertIsNotNone(self._entry(eid), "index entry must remain")
        self.assertTrue(os.path.exists(path), ".md must NOT be unlinked")
        self.assertIn("not a captured preference", err)

    def test_forget_preference_tagged_but_agent_source_refused(self):
        # Defense in depth: preference-tagged but source != user must ALSO be
        # refused — the guard requires BOTH tag=preference AND source=user.
        idx = self._index()
        mdrel = "learnings/agent-pref.md"
        os.makedirs(os.path.join(self.kdir, "learnings"), exist_ok=True)
        with open(os.path.join(self.kdir, mdrel), "w", encoding="utf-8") as f:
            f.write("---\nid: learn-agent-pref\nsource: agent\n---\nbody\n")
        idx["entries"].append({
            "id": "learn-agent-pref", "category": "learnings", "path": mdrel,
            "tags": ["preference"], "source": "agent", "created": "2026-07-13",
            "summary": "agent-authored pref",
        })
        with open(os.path.join(self.kdir, "index.json"), "w") as f:
            json.dump(idx, f)
        code, _out, err = self._cli(["prefer", "forget", "learn-agent-pref"])
        self.assertNotEqual(code, 0)
        self.assertIsNotNone(self._entry("learn-agent-pref"),
                             "agent-source pref must remain")
        self.assertTrue(os.path.exists(os.path.join(self.kdir, mdrel)))

    # --- unknown id is a clean "not found", not a crash ----------------------
    def test_forget_unknown_id_reports_not_found(self):
        code, _out, err = self._cli(
            ["prefer", "forget", "learn-pref-global-does-not-exist"])
        self.assertNotEqual(code, 0)
        self.assertIn("no captured preference", err)

    # --- orphan tolerance: a genuine capture with a MISSING .md is revocable --
    def test_forget_orphaned_capture_missing_md_succeeds(self):
        # Audit round-2 LOW: source=user lives only in the .md frontmatter, so an
        # orphaned capture (index entry present, .md gone) must stay revocable via
        # the capture-id prefix — the verb must handle its OWN captures.
        self._cli(["prefer", "capture", "always pin dependency versions",
                   "--global"])
        eid = "learn-pref-global-always-pin-dependency-versions"
        e = self._entry(eid)
        os.remove(os.path.join(self.kdir, e["path"]))   # orphan it
        code, out, err = self._cli(["prefer", "forget", eid])
        self.assertEqual(code, 0, err)
        self.assertIsNone(self._entry(eid),
                          "an orphaned capture must remain revocable")
        # HONEST ack: the message must not claim an unlink/archive that did not
        # happen (the .md was already gone).
        self.assertIn("already missing", out)
        self.assertNotIn("archived to", out)

    def test_forget_orphaned_non_capture_still_refused(self):
        # The orphan fallback must NOT weaken the guard: a NON-learn-pref id with a
        # missing .md is still refused.
        idx = self._index()
        idx["entries"].append({
            "id": "learn-random-thing", "category": "learnings",
            "path": "learnings/gone.md", "tags": ["preference"],
            "created": "2026-07-13", "summary": "x"})
        with open(os.path.join(self.kdir, "index.json"), "w") as f:
            json.dump(idx, f)
        code, _out, err = self._cli(["prefer", "forget", "learn-random-thing"])
        self.assertNotEqual(code, 0, "a non-capture-id orphan must still refuse")
        self.assertIsNotNone(self._entry("learn-random-thing"))

    # --- the capture supersede path (guard OFF) is unaffected ----------------
    def test_capture_supersede_still_replaces(self):
        # _prefer_capture calls _prefer_delete_entry with the guard OFF on its OWN
        # capture id — a contradicting re-capture must still supersede cleanly.
        self._cli(["prefer", "capture", "always use tabs", "--global",
                   "--key", "indent"])
        self._cli(["prefer", "capture", "always use spaces", "--global",
                   "--key", "indent"])
        eid = "learn-pref-global-indent"
        dupes = [e for e in self._index()["entries"] if e.get("id") == eid]
        self.assertEqual(len(dupes), 1, "supersede replaced, not stacked")
        self.assertEqual(self._entry(eid)["summary"], "always use spaces")


if __name__ == "__main__":
    unittest.main()
