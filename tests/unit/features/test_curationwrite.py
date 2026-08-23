"""The shared curation write chain's owning unit file — task 99 wave 13.

`agentware_lib.features.curationwrite` is new in this wave: the three steps
`cmd_learn` and `cmd_decide` had each written for themselves — refuse a
collision, register-or-roll-back, emit the success line. Both copies were the
same code with a noun swapped, which is why the ceiling rows for the two
handlers named the SAME two helpers as their seam.

A file that tests the two HANDLERS cannot own this module: it reaches these
three functions only through everything else the handlers do (a resolved
knowledge dir, a parsed topic, a BM25 near-duplicate probe, a rendered body),
so a defect here reddens as a `learn` failure and a reader has to guess. These
tests drive the three functions directly.

The properties that matter, and why each is a way the extraction can be wrong:

  ORDERED   — the three refusals are checked id, then path, then file, and one
              candidate can trip all three at once. Every ordering test uses a
              fixture that trips MORE THAN ONE, because a fixture that trips
              exactly one passes under any order.
  DISTINCT  — the three sentences differ, and the third carries the caller's
              noun. Collapsing any pair is invisible to a test that only checks
              "something was refused".
  ROLLBACK  — an add error must remove the markdown the caller already wrote.
              That is a claim about the DISK, so it is asserted on the disk;
              the returned error is a separate claim and is asserted separately.
  BEST-EFFORT — a rollback whose own unlink raises must still return the add
              error rather than propagating an OSError, because the caller is
              mid-refusal and the index is already untouched.
  NOT-SAVED — the index is saved on the success path ONLY. A save on the error
              path would commit a row whose file has just been deleted.
  EMIT      — the json envelope and the text line are the same emit
              parameterised by (key, label); both arms are pinned by bytes,
              because "it printed something" is satisfied by either.

Hermetic: a tempdir per test, no knowledge dir resolution, no CLI, stdlib
unittest only.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.features.curationwrite as curationwrite   # noqa: E402
import agentware_lib.knowledge.index as index                  # noqa: E402
from agentware_lib.features.curationwrite import (             # noqa: E402
    _emit_curation_entry,
    _finish_curation_write,
    _refuse_existing_entry,
)


def _data(*rows):
    return {"entries": list(rows), "tags": {}}


def _row(eid, path):
    return {"id": eid, "title": "T", "category": "learnings", "path": path,
            "tags": [], "created": "2026-01-01", "summary": "s"}


class RefuseExistingEntryTest(unittest.TestCase):
    """The three-way collision check, one sentence per collision."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="w13-refuse-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.abs = os.path.join(self.root, "learnings", "a.md")

    def _write_file(self):
        os.makedirs(os.path.dirname(self.abs), exist_ok=True)
        with open(self.abs, "w", encoding="utf-8") as f:
            f.write("x")

    def test_a_clean_candidate_is_not_refused(self):
        self.assertIsNone(_refuse_existing_entry(
            _data(_row("learn-b", "learnings/b.md")), "learn-a",
            "learnings/a.md", self.abs, "learning"))

    def test_an_index_with_no_entries_key_is_not_refused(self):
        """`data.get("entries") or []` — a fresh index has no list at all."""
        self.assertIsNone(_refuse_existing_entry(
            {}, "learn-a", "learnings/a.md", self.abs, "learning"))

    def test_a_duplicate_id_is_refused_by_id(self):
        msg = _refuse_existing_entry(
            _data(_row("learn-a", "learnings/other.md")), "learn-a",
            "learnings/a.md", self.abs, "learning")
        self.assertEqual(msg, "agentware: duplicate id 'learn-a'\n")

    def test_a_duplicate_path_is_refused_by_path(self):
        msg = _refuse_existing_entry(
            _data(_row("learn-other", "learnings/a.md")), "learn-a",
            "learnings/a.md", self.abs, "learning")
        self.assertEqual(msg,
                         "agentware: path already indexed: learnings/a.md\n")

    def test_a_file_on_disk_is_refused_by_file_and_names_the_noun(self):
        self._write_file()
        msg = _refuse_existing_entry(
            _data(), "learn-a", "learnings/a.md", self.abs, "learning")
        self.assertEqual(
            msg, "agentware: learning file already exists (refusing to "
                 "overwrite): %s\n" % self.abs)

    def test_the_noun_is_the_callers_and_nothing_else_changes(self):
        """`decide` gets its own word in the SAME sentence — the two handlers
        differ by this argument alone, so a hard-coded 'learning' would be
        invisible to any test that only drives `learn`."""
        self._write_file()
        msg = _refuse_existing_entry(
            _data(), "decide-a", "decisions/a.md", self.abs, "decision")
        self.assertEqual(
            msg, "agentware: decision file already exists (refusing to "
                 "overwrite): %s\n" % self.abs)

    def test_id_wins_over_path_when_both_collide(self):
        """A fixture that trips BOTH: one row carrying the id AND the path.
        A test whose fixture trips one check passes under any ordering."""
        msg = _refuse_existing_entry(
            _data(_row("learn-a", "learnings/a.md")), "learn-a",
            "learnings/a.md", self.abs, "learning")
        self.assertEqual(msg, "agentware: duplicate id 'learn-a'\n")

    def test_id_wins_over_the_file_when_both_collide(self):
        self._write_file()
        msg = _refuse_existing_entry(
            _data(_row("learn-a", "learnings/other.md")), "learn-a",
            "learnings/a.md", self.abs, "learning")
        self.assertEqual(msg, "agentware: duplicate id 'learn-a'\n")

    def test_path_wins_over_the_file_when_both_collide(self):
        self._write_file()
        msg = _refuse_existing_entry(
            _data(_row("learn-other", "learnings/a.md")), "learn-a",
            "learnings/a.md", self.abs, "learning")
        self.assertEqual(msg,
                         "agentware: path already indexed: learnings/a.md\n")

    def test_a_malformed_row_does_not_raise(self):
        """Index rows are read, not trusted: a row missing both keys must not
        take the whole refusal check down with an AttributeError."""
        self.assertIsNone(_refuse_existing_entry(
            _data({}), "learn-a", "learnings/a.md", self.abs, "learning"))


class FinishCurationWriteTest(unittest.TestCase):
    """Register-or-roll-back: the disk claim and the index claim, separately."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="w13-finish-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.abs = os.path.join(self.root, "learnings", "a.md")
        os.makedirs(os.path.dirname(self.abs), exist_ok=True)
        with open(self.abs, "w", encoding="utf-8") as f:
            f.write("already written\n")
        self.calls = []
        self._saved = (index._do_add, index.save_index)
        index.save_index = lambda kdir, data: self.calls.append(("save", kdir))
        self.addCleanup(self._restore)

    def _restore(self):
        index._do_add, index.save_index = self._saved

    def _add_returns(self, entry, err):
        def _do_add(*a, **k):
            self.calls.append(("add", a[2]))
            return entry, err
        index._do_add = _do_add

    def _finish(self):
        return _finish_curation_write(
            self.root, {"entries": []}, "learn-a", "A", "learnings",
            "learnings/a.md", ["t"], "s", "2026-01-01", self.abs)

    def test_a_successful_add_returns_the_entry_and_saves(self):
        self._add_returns({"id": "learn-a"}, None)
        entry, err = self._finish()
        self.assertEqual((entry, err), ({"id": "learn-a"}, None))
        self.assertEqual(self.calls, [("add", "learn-a"), ("save", self.root)])

    def test_a_successful_add_leaves_the_file_in_place(self):
        self._add_returns({"id": "learn-a"}, None)
        self._finish()
        self.assertTrue(os.path.exists(self.abs))

    def test_an_add_error_removes_the_file_the_caller_wrote(self):
        self._add_returns(None, "index refused")
        _entry, err = self._finish()
        self.assertEqual(err, "index refused")
        self.assertFalse(os.path.exists(self.abs))

    def test_an_add_error_returns_no_entry(self):
        self._add_returns(None, "index refused")
        entry, _err = self._finish()
        self.assertIsNone(entry)

    def test_an_add_error_never_saves_the_index(self):
        """A save on the error path commits a row whose file was just deleted —
        the one state this function exists to make unreachable."""
        self._add_returns(None, "index refused")
        self._finish()
        self.assertEqual([c for c in self.calls if c[0] == "save"], [])

    def test_a_rollback_whose_unlink_fails_still_returns_the_add_error(self):
        self._add_returns(None, "index refused")
        real_remove = os.remove

        def _boom(path, *a, **k):
            raise OSError("unlink refused")

        os.remove = _boom
        try:
            _entry, err = self._finish()
        finally:
            os.remove = real_remove
        self.assertEqual(err, "index refused")
        self.assertTrue(os.path.exists(self.abs))

    def test_the_add_is_called_with_the_callers_field_order(self):
        """`_do_add`'s positional contract is nine arguments in one order; a
        transposition of title/category or path/tags writes a plausible-looking
        row and nothing else here would notice."""
        seen = {}

        def _do_add(*a, **k):
            seen["args"] = a
            return {"id": a[2]}, None

        index._do_add = _do_add
        self._finish()
        self.assertEqual(
            seen["args"],
            (self.root, {"entries": []}, "learn-a", "A", "learnings",
             "learnings/a.md", ["t"], "s", "2026-01-01"))

    def test_the_index_is_reached_through_the_module_alias(self):
        """Patching the OWNER propagates. If this module ever bound `_do_add`
        by name at import time, this test is the one that says so."""
        self._add_returns({"id": "learn-a"}, None)
        self._finish()
        self.assertEqual(self.calls[0], ("add", "learn-a"))


class EmitCurationEntryTest(unittest.TestCase):
    """One emit, two arms, both pinned by bytes."""

    def _emit(self, **kw):
        base = dict(entry={"id": "learn-a", "title": "A"}, eid="learn-a",
                    abs_path="/kb/learnings/a.md", fmt="text", key="learning",
                    label="LEARNED")
        base.update(kw)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            _emit_curation_entry(base["entry"], base["eid"], base["abs_path"],
                                 base["fmt"], base["key"], base["label"])
        return out.getvalue(), err.getvalue()

    def test_the_text_arm_is_byte_pinned(self):
        out, _ = self._emit()
        self.assertEqual(
            out, "LEARNED entry 'learn-a' -> /kb/learnings/a.md "
                 "(registered in index)\n")

    def test_the_label_is_the_callers(self):
        out, _ = self._emit(label="DECISION", eid="decide-a")
        self.assertTrue(out.startswith("DECISION entry 'decide-a' -> "))

    def test_the_json_arm_carries_ok_true_and_the_callers_key(self):
        out, _ = self._emit(fmt="json")
        self.assertEqual(json.loads(out),
                         {"ok": True, "learning": {"id": "learn-a",
                                                   "title": "A"}})

    def test_the_json_key_is_the_callers(self):
        out, _ = self._emit(fmt="json", key="decision")
        self.assertIn("decision", json.loads(out))

    def test_the_json_arm_ends_in_a_newline(self):
        """`json.dump` writes no trailing newline; the explicit write is the
        only reason a shell reading this stream is not left mid-line."""
        out, _ = self._emit(fmt="json")
        self.assertTrue(out.endswith("}\n"))

    def test_the_json_arm_is_indented_and_not_ascii_escaped(self):
        out, _ = self._emit(fmt="json",
                            entry={"id": "learn-a", "title": "Ünïcode"})
        self.assertIn("\n  ", out)
        self.assertIn("Ünïcode", out)

    def test_an_unknown_format_falls_through_to_text(self):
        """The arm is `== "json"`, not a lookup: anything else is text, which
        is what keeps a mistyped --format from printing nothing at all."""
        out, _ = self._emit(fmt="yaml")
        self.assertTrue(out.startswith("LEARNED entry "))

    def test_nothing_is_written_to_stderr(self):
        _out, err = self._emit()
        self.assertEqual(err, "")


class ModuleBoundaryTest(unittest.TestCase):
    """The module's own shape: what it exports and what it may import."""

    def test_the_exports_are_the_three_steps(self):
        self.assertEqual(curationwrite._EXPORTS,
                         ["_refuse_existing_entry", "_finish_curation_write",
                          "_emit_curation_entry"])

    def test_it_imports_nothing_above_its_layer(self):
        """features -> knowledge is downward; features -> ops/retrieval/cli
        would not be. This module's whole point is a narrow dependency set."""
        with open(curationwrite.__file__, encoding="utf-8") as f:
            src = f.read()
        for banned in ("agentware_lib.ops", "agentware_lib.retrieval",
                       "agentware_lib.cli", "agentware_lib.strangler"):
            self.assertNotIn(banned, src)

    def test_it_declares_no_verbs(self):
        """A shared step is not a command. A `__verbs__` here would register a
        verb nobody typed."""
        self.assertFalse(hasattr(curationwrite, "__verbs__"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
