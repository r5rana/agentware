"""Owning unit file for `agentware_lib.ops.planauthor.workstamp` — task 99 w16.

The module carried a `test-ownership` row. Its behaviour is reachable through
`work stamp`, and the pre-existing coverage drives that verb — which observes
three counters and a stamped file, and so reddens when the FEATURE breaks. It
cannot redden for the insertion-index arithmetic, which is where every
interesting decision in this module lives and which no CLI invocation can
address directly.

What is asserted here is the module's PROMISE, not its shape:

  * THE INSERTION INDEX IS THREE CLAUSES, NOT "AFTER THE H1". Skip a COMPLETE
    frontmatter block; look for an H1 in the next FIVE lines ONLY; skip exactly
    ONE blank after it. Four artifacts separate those clauses, including an H1
    on the window's last line and one a single line past it.
  * AN INCOMPLETE BLOCK IS NOT A BLOCK. A fence that is never closed leaves
    `body_start` at 0, so the search runs over the `---` line itself and — with
    no H1 in the window — the stamp lands ABOVE it. That is the shipped
    behaviour, and it is PINNED rather than repaired, because repairing it is a
    behaviour change and wave 16 is a refactor.
  * THE STALE-TEAM REWRITE IS BY OFFSET, NOT BY LINE. The splice preserves the
    inline `· Team: x · Author: y` layout exactly, including the content AFTER
    the team token that a line rewrite would eat.
  * IDEMPOTENCE IS A PROPERTY OF BOTH WRITERS. Every case is run TWICE and the
    second pass must report False and leave the bytes alone — a stamper that is
    not idempotent slowly rewrites the operator's whole work tree.
  * THE FRONTMATTER WRITER NORMALISES ITS FENCES; THE PROSE WRITER DOES NOT.
    That asymmetry is real, and it is the one place in the fence family where a
    fence is not passed through verbatim, so it is asserted rather than assumed.

This file owns the per-ARTIFACT writers. The TREE walk and the handler
(`_stamp_work_tree` / `cmd_work_stamp`) live in the sibling
`test_workstamp_tree.py`, split off when this file crossed the 600-line
`tests/**` cap in the commit paying five ceiling rows down; the shared fixture
plumbing is in `_workstamp_support.py`.

Hermetic: every artifact is written into a fresh mkdtemp; no live KB, no git.
"""

from tests._paths import TESTS_DIR

import os
import sys
import shutil
import tempfile
import unittest

from tests.unit.ops._workstamp_support import (   # noqa: E402
    TEAM, _read, _write,
)

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.ops.planauthor.workstamp as workstamp      # noqa: E402


# --------------------------------------------------------------------------
# _prose_stamp_insert_index — pure arithmetic, scalar in / scalar out
# --------------------------------------------------------------------------

class ProseStampInsertIndexTest(unittest.TestCase):

    def _idx(self, text, body_start=0):
        return workstamp._prose_stamp_insert_index(text.split("\n"), body_start)

    def test_an_h1_on_the_first_line_puts_the_stamp_after_it(self):
        self.assertEqual(self._idx("# Plan\nbody\n"), 1)

    def test_exactly_one_blank_after_the_h1_is_skipped(self):
        self.assertEqual(self._idx("# Plan\n\nbody\n"), 2)

    def test_a_second_blank_is_not_skipped(self):
        # It survives BELOW the stamp, which is what keeps a re-stamped artifact
        # from slowly losing its paragraph breaks.
        self.assertEqual(self._idx("# Plan\n\n\nbody\n"), 2)

    def test_an_h1_on_the_windows_last_line_is_still_found(self):
        self.assertEqual(self._idx("a\nb\nc\nd\n# Plan\nbody\n"), 5)

    def test_an_h1_one_line_past_the_window_is_not_found(self):
        # The whole point of the window: an artifact with a long preamble is
        # stamped at the top of its body, never in the middle of its prose.
        self.assertEqual(self._idx("a\nb\nc\nd\ne\n# Plan\nbody\n"), 0)

    def test_the_window_is_measured_from_body_start_not_from_zero(self):
        # The H1 must sit at an index >= 5 for the two to differ at all: with it
        # at index 3 the window [3, 8) and the mutant window [3, 5) both contain
        # it, and the first draft of this test passed with `body_start +`
        # deleted. Found by the wave's mutation sweep.
        lines = "x\nx\nx\ny\ny\ny\n# Plan\nbody\n"
        self.assertEqual(self._idx(lines, 3), 7)
        # ... and the same document from 0 does NOT find it, which is what
        # makes the offset load-bearing rather than incidental.
        self.assertEqual(self._idx(lines, 0), 0)

    def test_a_document_with_no_h1_returns_body_start(self):
        self.assertEqual(self._idx("just prose\nand more\n"), 0)
        self.assertEqual(self._idx("just prose\nand more\n", 1), 1)

    def test_a_hash_without_a_space_is_not_an_h1(self):
        # `startswith("# ")` — `#Plan` and `## Sub` are both refused.
        self.assertEqual(self._idx("#Plan\nbody\n"), 0)
        self.assertEqual(self._idx("## Sub\nbody\n"), 0)

    def test_an_h1_as_the_last_line_does_not_index_past_the_end(self):
        self.assertEqual(self._idx("# Plan"), 1)

    def test_a_body_start_past_the_end_returns_itself(self):
        self.assertEqual(workstamp._prose_stamp_insert_index([], 0), 0)
        self.assertEqual(workstamp._prose_stamp_insert_index(["a"], 9), 9)


# --------------------------------------------------------------------------
# _stamp_work_artifact
# --------------------------------------------------------------------------

class StampWorkArtifactTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-t99w16-stamp-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _stamp(self, name, text, team=TEAM, author="auth"):
        path = _write(os.path.join(self.root, name + ".md"), text)
        rc = workstamp._stamp_work_artifact(path, team, author)
        return rc, _read(path), path

    def test_a_plain_artifact_gains_both_lines_after_its_h1(self):
        rc, out, _p = self._stamp("plain", "# Plan\n\nbody\n")
        self.assertTrue(rc)
        self.assertEqual(out.split("\n")[2], "> Team: %s" % TEAM)
        self.assertEqual(out.split("\n")[3], "> Author: auth")

    def test_a_complete_block_is_skipped_so_the_stamp_lands_in_the_body(self):
        rc, out, _p = self._stamp("fenced", "---\nteam: old\n---\n# Plan\n\nbody\n")
        self.assertTrue(rc)
        lines = out.split("\n")
        self.assertEqual(lines[:3], ["---", "team: old", "---"])
        self.assertTrue(lines[5].startswith("> Team:"))

    def test_an_unclosed_fence_with_no_h1_in_the_window_stamps_above_it(self):
        # PINNED, not repaired. `_frontmatter_block` returns None for a fence
        # that is never closed, so body_start stays 0; with no H1 in the window
        # the stamp lands at line 0, above the `---`. A refactor may not
        # silently change this — that would be a behaviour change.
        rc, out, _p = self._stamp("runaway",
                                  "---\nteam: old\n\n\n\n\n# Plan\n\nbody\n")
        self.assertTrue(rc)
        self.assertTrue(out.startswith("> Team: %s\n> Author: auth\n---\n" % TEAM))

    def test_body_start_is_the_line_after_the_close_not_the_close_itself(self):
        # An off-by-one here is invisible on an ordinary artifact — the H1 is
        # inside both windows — and shows only when the extra line at the front
        # pushes the H1 out of the back. Found by the wave's mutation sweep.
        # Correct: body_start 3, window [3, 8), the H1 at 7 is found.
        # Off by one: body_start 2, window [2, 7), it is NOT, and the stamp
        # lands at index 2 — ON the closing fence.
        rc, out, _p = self._stamp("offby",
                                  "---\na: 1\n---\n\n\n\n\n# Plan\n\nbody\n")
        self.assertTrue(rc)
        lines = out.split("\n")
        self.assertEqual(lines[:3], ["---", "a: 1", "---"])
        self.assertEqual(lines[7], "# Plan")
        self.assertEqual(lines[8], "")          # the ONE blank, skipped
        self.assertTrue(lines[9].startswith("> Team:"))

    def test_an_unclosed_fence_with_an_h1_in_the_window_stamps_after_the_h1(self):
        rc, out, _p = self._stamp("runaway2", "---\nteam: old\n\n# Plan\n\nbody\n")
        self.assertTrue(rc)
        self.assertEqual(out.split("\n")[3], "# Plan")
        self.assertTrue(out.split("\n")[5].startswith("> Team:"))

    def test_an_already_correct_line_start_stamp_is_a_no_op(self):
        rc, out, _p = self._stamp("ok1", "# Plan\n> Team: %s\n\nbody\n" % TEAM)
        self.assertFalse(rc)
        self.assertEqual(out, "# Plan\n> Team: %s\n\nbody\n" % TEAM)

    def test_an_already_correct_inline_stamp_is_a_no_op(self):
        text = "# Plan\n> Created: 2026-01-01 · Team: %s · Author: x\n" % TEAM
        rc, out, _p = self._stamp("ok2", text)
        self.assertFalse(rc)
        self.assertEqual(out, text)

    def test_a_stale_line_start_team_is_rewritten_in_place(self):
        rc, out, _p = self._stamp("stale1", "# Plan\n> Team: other\n\nbody\n")
        self.assertTrue(rc)
        self.assertEqual(out, "# Plan\n> Team: %s\n\nbody\n" % TEAM)

    def test_a_stale_inline_team_keeps_everything_around_it(self):
        # The splice is by OFFSET: a line rewrite would eat the Author suffix.
        rc, out, _p = self._stamp(
            "stale2", "# Plan\n> Created: 2026-01-01 · Team: other · Author: x\n")
        self.assertTrue(rc)
        self.assertIn("· Team: %s · Author: x" % TEAM, out)
        self.assertTrue(out.startswith("# Plan\n> Created: 2026-01-01 · "))

    def test_a_stale_rewrite_never_adds_an_author_line(self):
        rc, out, _p = self._stamp("stale3", "# Plan\n> Team: other\n")
        self.assertTrue(rc)
        self.assertNotIn("> Author:", out)

    def test_an_existing_author_line_is_not_duplicated(self):
        _rc, out, _p = self._stamp("hasauth", "# Plan\n> Author: someone\n\nb\n")
        self.assertEqual(out.count("> Author:"), 1)

    def test_no_author_means_no_author_line(self):
        _rc, out, _p = self._stamp("noauth", "# Plan\n\nbody\n", author=None)
        self.assertIn("> Team:", out)
        self.assertNotIn("> Author:", out)

    def test_no_team_refuses_and_leaves_a_real_file_untouched(self):
        # The fixture must be a file that WOULD have been stamped: a missing
        # path reaches False down the OSError arm whether or not the team guard
        # exists, and the first draft of this test used one. Found by the
        # wave's mutation sweep.
        path = _write(os.path.join(self.root, "noteam.md"), "# Plan\n\nbody\n")
        self.assertFalse(workstamp._stamp_work_artifact(path, "", "auth"))
        self.assertEqual(_read(path), "# Plan\n\nbody\n")
        # Non-vacuity in the SAME fixture: with a team it IS stamped.
        self.assertTrue(workstamp._stamp_work_artifact(path, TEAM, "auth"))

    def test_an_unreadable_path_reports_false_without_raising(self):
        self.assertFalse(workstamp._stamp_work_artifact(
            os.path.join(self.root, "gone.md"), TEAM, "auth"))

    def test_every_case_is_idempotent(self):
        cases = {
            "i-plain": "# Plan\n\nbody\n",
            "i-fenced": "---\nteam: old\n---\n# Plan\n\nbody\n",
            "i-stale": "# Plan\n> Team: other\n\nbody\n",
            "i-inline": "# Plan\n> Created: c · Team: other · Author: x\n",
            "i-noh1": "just prose\n",
            "i-runaway": "---\nteam: old\n\n\n\n\n# Plan\n",
            "i-empty": "",
        }
        for name, text in cases.items():
            _rc, first, path = self._stamp(name, text)
            self.assertFalse(workstamp._stamp_work_artifact(path, TEAM, "auth"),
                             name)
            self.assertEqual(_read(path), first, name)


# --------------------------------------------------------------------------
# _ensure_work_frontmatter
# --------------------------------------------------------------------------

class EnsureWorkFrontmatterTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-t99w16-fm-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _ensure(self, name, text, team=TEAM, origin="team"):
        path = _write(os.path.join(self.root, name + ".md"), text)
        rc = workstamp._ensure_work_frontmatter(path, team, origin)
        return rc, _read(path), path

    def test_an_artifact_with_no_block_gains_a_minimal_one(self):
        rc, out, _p = self._ensure("none", "# Plan\n\nbody\n")
        self.assertTrue(rc)
        self.assertEqual(out, "---\nteam: %s\norigin: team\n---\n# Plan\n\nbody\n"
                         % TEAM)

    def test_a_correct_block_is_a_no_op(self):
        text = "---\nteam: %s\norigin: team\n---\n\n# Plan\n" % TEAM
        rc, out, _p = self._ensure("ok", text)
        self.assertFalse(rc)
        self.assertEqual(out, text)

    def test_a_correct_team_with_no_origin_is_still_rewritten(self):
        # Both keys are the claim; a team without an origin is half a facet.
        rc, out, _p = self._ensure("noorigin",
                                   "---\nteam: %s\n---\n\n# Plan\n" % TEAM)
        self.assertTrue(rc)
        self.assertIn("origin: team", out)

    def test_a_stale_team_is_replaced_not_appended(self):
        rc, out, _p = self._ensure("stale",
                                   "---\nteam: other\norigin: team\n---\n\n# P\n")
        self.assertTrue(rc)
        self.assertEqual(out.count("team: "), 1)
        self.assertIn("team: %s" % TEAM, out)

    def test_unrelated_fields_survive_and_keep_their_order(self):
        rc, out, _p = self._ensure(
            "others", "---\nid: x\nteam: other\ntitle: T\n---\n\n# P\n")
        self.assertTrue(rc)
        block = out.split("---")[1].strip().split("\n")
        self.assertEqual(block[:2], ["id: x", "title: T"])
        self.assertEqual(block[2:], ["team: %s" % TEAM, "origin: team"])

    def test_the_body_survives_including_a_horizontal_rule(self):
        _rc, out, _p = self._ensure(
            "rule", "---\nteam: other\n---\n\n# P\n\n---\n\ntail\n")
        self.assertTrue(out.rstrip().endswith("tail"))
        self.assertIn("\n---\n\ntail", out)

    def test_the_fences_are_re_emitted_normalised(self):
        # The ONE place in the fence family where a fence is not passed through
        # verbatim: an indented open that `.strip()` accepted comes back as the
        # canonical `---`. Asserted rather than assumed, because the sibling
        # rewriters do the opposite.
        _rc, out, _p = self._ensure("indent",
                                    "  ---\nteam: other\n---   \n\n# P\n")
        self.assertTrue(out.startswith("---\n"))
        self.assertNotIn("---   ", out)

    def test_an_explicit_origin_is_honoured(self):
        _rc, out, _p = self._ensure("shared", "# P\n", origin="shared")
        self.assertIn("origin: shared", out)

    def test_no_team_refuses_and_leaves_a_real_file_untouched(self):
        # Same shape, same lesson as the prose writer's: a missing path proves
        # nothing about the team guard.
        path = _write(os.path.join(self.root, "noteam.md"), "# Plan\n")
        self.assertFalse(workstamp._ensure_work_frontmatter(path, ""))
        self.assertEqual(_read(path), "# Plan\n")
        self.assertTrue(workstamp._ensure_work_frontmatter(path, TEAM))

    def test_an_unreadable_path_reports_false(self):
        self.assertFalse(workstamp._ensure_work_frontmatter(
            os.path.join(self.root, "gone.md"), TEAM))

    def test_every_case_is_idempotent(self):
        cases = {
            "j-none": "# Plan\n",
            "j-stale": "---\nteam: other\norigin: team\n---\n\n# P\n",
            "j-others": "---\nid: x\nteam: other\n---\n\n# P\n",
            "j-empty-block": "---\n---\n\n# P\n",
            "j-unclosed": "---\nteam: other\n\n# P\n",
        }
        for name, text in cases.items():
            _rc, first, path = self._ensure(name, text)
            self.assertFalse(workstamp._ensure_work_frontmatter(path, TEAM),
                             name)
            self.assertEqual(_read(path), first, name)


class IsDerivedRosterTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-t99w16-roster-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_it_needs_BOTH_banner_halves(self):
        both = _write(os.path.join(self.root, "a.md"),
                      "# R\n\n> Auto-generated roster — DO NOT edit\n")
        one = _write(os.path.join(self.root, "b.md"),
                     "# R\n\n> Auto-generated roster\n")
        other = _write(os.path.join(self.root, "c.md"), "# R\n\n> DO NOT edit\n")
        self.assertTrue(workstamp._is_derived_roster(both))
        self.assertFalse(workstamp._is_derived_roster(one))
        self.assertFalse(workstamp._is_derived_roster(other))

    def test_an_authored_index_is_not_a_roster(self):
        p = _write(os.path.join(self.root, "d.md"),
                   "---\nid: x\ncategory: references\n---\n\n# Index\n")
        self.assertFalse(workstamp._is_derived_roster(p))

    def test_the_banner_must_be_in_the_first_600_bytes(self):
        # It reads a HEAD, not the file: a banner buried in a long body is not
        # a roster marker, and that bound is what keeps the walk cheap.
        p = _write(os.path.join(self.root, "e.md"),
                   "x" * 700 + "\nAuto-generated roster — DO NOT edit\n")
        self.assertFalse(workstamp._is_derived_roster(p))

    def test_a_missing_file_is_not_a_roster(self):
        self.assertFalse(workstamp._is_derived_roster(
            os.path.join(self.root, "gone.md")))


if __name__ == "__main__":
    unittest.main()
