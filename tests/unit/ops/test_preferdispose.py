"""The disposition verbs' own unit file — task 99 wave 2.

`agentware_lib.ops.preferdispose` had no owning unit file. `test_prefer_reject.py`
covers the REJECT path end to end through the CLI over a synthetic KB, which is
the right test for "does rejecting cancel the workorder" and the wrong one for
"which line of the queue does the scan return" — a whole-verb test can only
observe the rewritten file, so an off-by-one in the raw-line index and a correct
index that happens to hit the same line are indistinguishable.

Wave 2 split `cmd_prefer_approve` (14 branches) and `cmd_prefer_reject` (20) into
three helpers plus the queue family's shared lock and reader. Each helper is
asserted here on concrete values.

The properties that matter, and how each can be got wrong:

  NAMESPACE   — the resolved workorder feeds `cmd_plan_set_state`'s feature path,
                so a traversal or a foreign namespace must be refused BEFORE any
                path join. A guard that normalised first and checked second would
                accept `../escape` as `auto/../escape`.
  RAW INDEX   — the target is located by its index into the ORIGINAL `split("\\n")`
                list, because every other line is rewritten byte-for-byte. An
                index into a FILTERED list would rewrite the wrong record.
  PRESERVE    — a line the verb cannot parse is skipped, never dropped: the queue
                is producer-agnostic and this verb rewrites the whole file.
  ONLY OPEN   — only a `proposed` record is a valid target. Matching any status
                would let an already-rejected candidate be rejected again and its
                workorder cancelled twice.
  REPORT      — the prose arm names the workorder's fate; the json arm carries
                both the disposition and whether a cancel actually happened.

Hermetic: pure functions over in-memory lines, stdout captured in-process.
"""

from tests._paths import TESTS_DIR

import argparse
import contextlib
import io
import json
import os
import sys
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

from agentware_lib.ops.preferdispose import (  # noqa: E402
    _reject_find_target,
    _reject_report,
    _reject_workorder_id,
)


def _line(**kw):
    return json.dumps(kw, ensure_ascii=False)


class RejectWorkorderIdTest(unittest.TestCase):
    def test_a_bare_slug_is_normalised_into_the_auto_namespace(self):
        wo, err = _reject_workorder_id("pref-always-pin-abc123")
        self.assertEqual(wo, "auto/pref-always-pin-abc123")
        self.assertIsNone(err)

    def test_a_fully_qualified_workorder_passes_through(self):
        wo, err = _reject_workorder_id("auto/pref-x-1")
        self.assertEqual(wo, "auto/pref-x-1")
        self.assertIsNone(err)

    def test_a_traversal_is_refused(self):
        """The id reaches a feature PATH, so `../` never gets to be normalised
        into something that looks legitimate."""
        wo, err = _reject_workorder_id("../escape")
        self.assertIsNone(wo)
        self.assertIn("only targets an auto/pref-*", err)

    def test_a_nested_path_inside_the_namespace_is_refused(self):
        wo, err = _reject_workorder_id("auto/pref-x/../../etc")
        self.assertIsNone(wo)
        self.assertIn("only targets an auto/pref-*", err)

    def test_a_non_pref_workorder_is_refused(self):
        """`prefer reject` disposes of PREFERENCE candidates only — it must not
        be able to cancel an unrelated feature's plan."""
        wo, err = _reject_workorder_id("auto/some-other-feature")
        self.assertIsNone(wo)
        self.assertIn("only targets an auto/pref-*", err)


class RejectFindTargetTest(unittest.TestCase):
    def test_it_returns_the_index_into_the_original_line_list(self):
        raw = ["", _line(status="rejected", workorder="auto/pref-a"),
               "not json",
               _line(status="proposed", workorder="auto/pref-b"), ""]
        idx, rec = _reject_find_target(raw, "auto/pref-b")
        self.assertEqual(idx, 3)
        self.assertEqual(rec["workorder"], "auto/pref-b")

    def test_only_a_proposed_record_is_a_target(self):
        raw = [_line(status="approved", workorder="auto/pref-a"),
               _line(status="rejected", workorder="auto/pref-a")]
        self.assertEqual(_reject_find_target(raw, "auto/pref-a"), (None, None))

    def test_an_unknown_workorder_finds_nothing(self):
        raw = [_line(status="proposed", workorder="auto/pref-a")]
        self.assertEqual(_reject_find_target(raw, "auto/pref-zz"), (None, None))

    def test_unparseable_and_non_object_lines_are_skipped_not_dropped(self):
        """The caller rewrites the file, so a line this scan cannot read must
        still be there afterwards — it is skipped for MATCHING only."""
        raw = ["garbage", "[1,2,3]", '"str"',
               _line(status="proposed", workorder="auto/pref-c")]
        idx, rec = _reject_find_target(raw, "auto/pref-c")
        self.assertEqual(idx, 3)
        self.assertIsNotNone(rec)

    def test_the_first_matching_record_wins(self):
        raw = [_line(status="proposed", workorder="auto/pref-d", n=1),
               _line(status="proposed", workorder="auto/pref-d", n=2)]
        idx, rec = _reject_find_target(raw, "auto/pref-d")
        self.assertEqual((idx, rec["n"]), (0, 1))


class RejectReportTest(unittest.TestCase):
    def _run(self, fmt, cancelled, disp):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _reject_report(argparse.Namespace(format=fmt), "auto/pref-x",
                           cancelled, disp)
        return buf.getvalue()

    def test_the_json_arm_carries_the_disposition_and_the_cancel_flag(self):
        payload = json.loads(self._run("json", True, "cancelled"))
        self.assertEqual(payload, {"ok": True, "workorder": "auto/pref-x",
                                   "status": "rejected",
                                   "workorder_cancelled": True,
                                   "workorder_state": "cancelled"})

    def test_the_json_arm_reports_a_reject_with_no_workorder_on_disk(self):
        payload = json.loads(self._run("json", False, "absent"))
        self.assertFalse(payload["workorder_cancelled"])
        self.assertEqual(payload["workorder_state"], "absent")

    def test_the_prose_arm_names_what_happened_to_the_workorder(self):
        self.assertIn("workorder cancelled", self._run("text", True,
                                                       "cancelled"))
        self.assertIn("no workorder on disk", self._run("text", False,
                                                        "absent"))
        self.assertIn("workorder already terminal",
                      self._run("text", False, "terminal"))

    def test_an_unmapped_disposition_is_printed_verbatim_rather_than_hidden(self):
        """An unknown state is reported as itself: a report that silently
        rendered nothing would hide the one case worth reading."""
        self.assertIn("something-new", self._run("text", False, "something-new"))

    def test_the_prose_arm_says_the_plan_was_kept(self):
        """The plan.md is the drain's re-emit sentinel, so its survival is
        deliberate and the operator is told so."""
        self.assertIn("plan.md kept", self._run("text", True, "cancelled"))


if __name__ == "__main__":
    unittest.main()
