"""Owning unit file for `workstamp`'s TREE half — task 99 w16.

The sibling of `test_workstamp.py`, split off when that file crossed the 600-line
`tests/**` cap in the commit paying five ceiling rows down. The seam is the
module's own: this file owns the two surfaces that walk or dispatch —
`_stamp_work_tree` (the walk, its three counters and its two exclusions) and
`cmd_work_stamp` (the team inference, the author precedence and the two reports)
— while the per-ARTIFACT writers stay with their arithmetic next door.

What is asserted here is the module's PROMISE, not its shape:

  * THREE COUNTERS ARE THREE DIFFERENT NUMBERS. The tree is built so scanned,
    stamped and frontmatter_added all differ, which is the only shape in which
    swapping two of them is observable — the first draft had two of them equal.
  * A DERIVED ROSTER IS DETECTED BY CONTENT, NOT BY FILENAME. An authored
    `index.md` with real frontmatter is stamped like any other artifact.
  * THE TWO WRITERS COMMUTE. The order `--frontmatter` runs in is a READING
    choice, not a correctness one, and the docstring claim that reversing it
    would corrupt a fresh artifact was false — measured over 540 exhaustive and
    20,000 random artifacts, zero differ.
  * A FLAT KB IS A NO-OP THAT WRITES NOTHING, not merely one that prints so.

Hermetic: every artifact is written into a fresh mkdtemp; no live KB, no git.
"""

from tests._paths import TESTS_DIR

import json
import os
import shutil
import sys
import tempfile
import unittest

from tests.unit.ops._workstamp_support import (   # noqa: E402
    Args, TEAM, _build_tree, _captured, _read, _write,
)

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.core.config as config                      # noqa: E402
import agentware_lib.ops.planauthor.workstamp as workstamp      # noqa: E402


# --------------------------------------------------------------------------
# _stamp_work_tree and cmd_work_stamp
# --------------------------------------------------------------------------

class StampWorkTreeTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-t99w16-tree-")
        self.work = _build_tree(os.path.join(self.root, "work"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_the_three_counters_are_three_different_numbers(self):
        scanned, stamped, fm = workstamp._stamp_work_tree(
            self.work, TEAM, "auth", True)
        # 5 markdown artifacts scanned (the derived roster is skipped), 4 of
        # them gain or fix a prose stamp, and 3 gain a frontmatter block (the
        # authored index and design.md already carry a correct one). All three
        # DISTINCT on purpose: a fixture where two coincide cannot see them
        # swapped, and the first draft of this one had stamped == fm.
        self.assertEqual((scanned, stamped, fm), (5, 4, 3))
        self.assertEqual(len({scanned, stamped, fm}), 3)

    def test_a_derived_roster_is_skipped_by_content(self):
        workstamp._stamp_work_tree(self.work, TEAM, "auth", False)
        self.assertNotIn("> Team:", _read(os.path.join(self.work, "index.md")))

    def test_an_authored_index_is_stamped_like_any_other_artifact(self):
        workstamp._stamp_work_tree(self.work, TEAM, "auth", False)
        self.assertIn("> Team: %s" % TEAM,
                      _read(os.path.join(self.work, "nested", "index.md")))

    def test_a_non_markdown_file_is_untouched(self):
        workstamp._stamp_work_tree(self.work, TEAM, "auth", True)
        self.assertEqual(_read(os.path.join(self.work, "notes.txt")),
                         "not markdown\n")

    def test_the_two_writers_commute_so_the_order_cannot_corrupt(self):
        # The docstring used to claim reversing them would put the `> Team:`
        # line above the block. That is FALSE and unfalsifiable: the prepend and
        # the body_start shift are the same number of lines, so the two orders
        # produce identical bytes. Measured at wave 16 over 540 exhaustive and
        # 20,000 random artifacts, 0 differ. What IS assertable is the
        # commutation itself, so that is what is asserted — on the artifact
        # shapes most likely to break it if a future writer stops prepending.
        for text in ("# Plan\n\nbody\n",
                     "just prose\n",
                     "---\nteam: other\n---\n# Plan\n\nb\n",
                     "---\nteam: old\n\n\n\n\n# Plan\n",
                     "a\nb\nc\nd\ne\n# Plan\n"):
            a = _write(os.path.join(self.root, "cm-a.md"), text)
            workstamp._ensure_work_frontmatter(a, TEAM)
            workstamp._stamp_work_artifact(a, TEAM, "auth")
            b = _write(os.path.join(self.root, "cm-b.md"), text)
            workstamp._stamp_work_artifact(b, TEAM, "auth")
            workstamp._ensure_work_frontmatter(b, TEAM)
            self.assertEqual(_read(a), _read(b), text)
            # Non-vacuity in the same fixture: something actually happened.
            self.assertNotEqual(_read(a), text, text)

    def test_the_prose_stamp_lands_below_the_machine_block(self):
        workstamp._stamp_work_tree(self.work, TEAM, "auth", True)
        text = _read(os.path.join(self.work, "plan.md"))
        self.assertTrue(text.startswith("---\n"))
        self.assertLess(text.index("---\n"), text.index("> Team:"))

    def test_a_second_pass_changes_nothing(self):
        workstamp._stamp_work_tree(self.work, TEAM, "auth", True)
        before = {p: _read(os.path.join(self.work, p))
                  for p in ("plan.md", "worklog.md", "done.md")}
        scanned, stamped, fm = workstamp._stamp_work_tree(
            self.work, TEAM, "auth", True)
        self.assertEqual((stamped, fm), (0, 0))
        self.assertEqual(scanned, 5)
        for p, text in before.items():
            self.assertEqual(_read(os.path.join(self.work, p)), text, p)

    def test_an_absent_work_dir_is_zeros_not_an_error(self):
        # The `isdir` test behind this is a FAST PATH and CANNOT be observed:
        # `os.walk` on a missing path, and on a plain file, yields nothing and
        # raises nothing. Measured at wave 16. The answer is asserted; the
        # branch is not claimed to be guarded, because it is not.
        self.assertEqual(
            workstamp._stamp_work_tree(os.path.join(self.root, "nope"),
                                       TEAM, "auth", True),
            (0, 0, 0))
        afile = _write(os.path.join(self.root, "afile"), "x")
        self.assertEqual(
            workstamp._stamp_work_tree(afile, TEAM, "auth", True), (0, 0, 0))

    def test_without_the_flag_no_frontmatter_is_added(self):
        _scanned, _stamped, fm = workstamp._stamp_work_tree(
            self.work, TEAM, "auth", False)
        self.assertEqual(fm, 0)
        self.assertFalse(_read(os.path.join(self.work, "plan.md")).startswith("---"))


class CmdWorkStampTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-t99w16-cmd-")
        self._real = config.require_knowledge_dir

    def tearDown(self):
        config.require_knowledge_dir = self._real
        shutil.rmtree(self.root, ignore_errors=True)

    def _kdir(self, name, team=True):
        kdir = (os.path.join(self.root, name, "teams", TEAM) if team
                else os.path.join(self.root, name, "flat"))
        _build_tree(os.path.join(kdir, "work", "feat"))
        config.require_knowledge_dir = lambda: kdir
        return kdir

    def test_a_flat_kb_is_a_no_op_at_rc_zero(self):
        self._kdir("f", team=False)
        with _captured() as (out, _e):
            rc = workstamp.cmd_work_stamp(Args(format="text"))
        self.assertEqual(rc, 0)
        self.assertIn("no-op (flat KB", out.getvalue())

    def test_a_flat_kb_no_op_writes_nothing(self):
        kdir = self._kdir("f2", team=False)
        plan = os.path.join(kdir, "work", "feat", "plan.md")
        with _captured():
            workstamp.cmd_work_stamp(Args(format="json"))
        self.assertEqual(_read(plan), "# Plan\n\nbody\n")

    def test_an_unresolvable_kdir_exits_one(self):
        config.require_knowledge_dir = lambda: None
        with _captured():
            self.assertEqual(workstamp.cmd_work_stamp(Args(format="text")), 1)

    def test_the_team_is_inferred_from_the_kdir_path(self):
        self._kdir("t")
        with _captured() as (out, _e):
            rc = workstamp.cmd_work_stamp(Args(format="json", author="auth"))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())["team"], TEAM)

    def test_the_json_report_carries_all_four_keys(self):
        self._kdir("t2")
        with _captured() as (out, _e):
            workstamp.cmd_work_stamp(Args(format="json", author="auth",
                                          frontmatter=True))
        doc = json.loads(out.getvalue())
        self.assertEqual(sorted(doc), ["frontmatter_added", "scanned",
                                       "stamped", "team"])
        self.assertEqual((doc["scanned"], doc["stamped"],
                          doc["frontmatter_added"]), (5, 4, 3))

    def test_the_text_report_names_all_three_counters(self):
        self._kdir("t3")
        with _captured() as (out, _e):
            workstamp.cmd_work_stamp(Args(format="text", author="auth"))
        line = out.getvalue().strip()
        self.assertIn("team=%s" % TEAM, line)
        self.assertIn("scanned=5", line)
        self.assertIn("prose-stamped=4", line)
        self.assertIn("frontmatter=0", line)

    def test_the_author_flag_overrides_the_resolved_handle(self):
        kdir = self._kdir("t4")
        with _captured():
            workstamp.cmd_work_stamp(Args(format="json", author="explicit-one"))
        self.assertIn("> Author: explicit-one",
                      _read(os.path.join(kdir, "work", "feat", "plan.md")))

    def test_a_shard_with_no_work_dir_still_reports(self):
        kdir = os.path.join(self.root, "bare", "teams", TEAM)
        os.makedirs(kdir)
        config.require_knowledge_dir = lambda: kdir
        with _captured() as (out, _e):
            rc = workstamp.cmd_work_stamp(Args(format="json"))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())["scanned"], 0)


class ExportSurfaceTest(unittest.TestCase):
    """The wave's new helpers are deliberately ABSENT from the facade surface.

    `_EXPORTS` here IS load-bearing — the parser-golden dispatch contract needs
    `cmd_work_stamp` as an attribute of the entrypoint namespace — so the list
    is asserted exactly, and the two new helpers are asserted OUT of it.
    """

    def test_exports_are_exactly_the_five_shipped_names(self):
        self.assertEqual(workstamp._EXPORTS, [
            "_WORK_TEAM_STAMP_RE", "_is_derived_roster", "_stamp_work_artifact",
            "_ensure_work_frontmatter", "cmd_work_stamp"])
        self.assertEqual(sorted(workstamp.__all__),
                         sorted(workstamp._EXPORTS))

    def test_the_new_helpers_exist_but_are_not_exported(self):
        for name in ("_prose_stamp_insert_index", "_stamp_work_tree"):
            self.assertTrue(hasattr(workstamp, name), name)
            self.assertNotIn(name, workstamp._EXPORTS)


if __name__ == "__main__":
    unittest.main()
