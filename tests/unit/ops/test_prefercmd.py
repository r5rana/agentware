"""The `prefer` verb surface's own unit file — task 99 wave 2.

`agentware_lib.ops.prefercmd` had no owning unit file. `test_prefer_review.py`
drives `prefer review` through the CLI over a synthetic KB, which is the right
test for "does the digest reach the operator" and the wrong one for "what does
the default scope select" — a CLI test asserts printed text, so a selection bug
and a rendering bug are the same red.

Wave 2 split `cmd_prefer_review` (24 branches) into scope selection, per-record
item construction and the prose renderer, so the three are separately checkable.

The properties that matter, and how each can be got wrong:

  DEFAULT     — the default scope is the OPEN set (`proposed`) only. Widening it
                by accident turns "what needs me?" into a dump nobody reads.
  ADDITIVE    — `--rejected`/`--approved` ADD to the set rather than replace it,
                so `--rejected` still shows the open items it is a superset of.
  REPORT≠HIDE — a missing workorder directory is REPORTED, never filtered on.
                Filtering would silently shrink the open set, which is the exact
                failure this verb exists to prevent.
  UNTRUSTED   — candidate text is collapsed onto one capped line, so an embedded
                newline cannot fake a report line or a shell prompt (R-SEC-02).
  SCOPE NAMED — the empty case names the scope it looked at, so "nothing" is
                distinguishable from "nothing OF THIS KIND".

Hermetic: pure functions over in-memory records, stdout captured in-process.
"""

from tests._paths import TESTS_DIR

import argparse
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

from agentware_lib.ops.prefercmd import (  # noqa: E402
    PREFER_QUEUE_PRODUCER,
    _prefer_review_item,
    _prefer_review_print,
    _prefer_review_want,
)


def _args(**kw):
    base = {"rejected": False, "approved": False, "all": False,
            "format": "text"}
    base.update(kw)
    return argparse.Namespace(**base)


def _printed(items, show_all, want):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _prefer_review_print(items, show_all, want)
    return rc, buf.getvalue()


class PreferReviewWantTest(unittest.TestCase):
    def test_the_default_scope_is_the_open_set_only(self):
        self.assertEqual(_prefer_review_want(_args()), {"proposed"})

    def test_rejected_widens_the_scope_additively(self):
        self.assertEqual(_prefer_review_want(_args(rejected=True)),
                         {"proposed", "rejected"})

    def test_approved_widens_the_scope_additively(self):
        self.assertEqual(_prefer_review_want(_args(approved=True)),
                         {"proposed", "approved"})

    def test_both_flags_compose(self):
        self.assertEqual(_prefer_review_want(_args(rejected=True,
                                                   approved=True)),
                         {"proposed", "approved", "rejected"})


class PreferReviewItemTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_pcmd_item_")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def _mk_workorder(self, wo):
        os.makedirs(os.path.join(self.kdir, "work", wo), exist_ok=True)

    def test_a_proposed_item_carries_its_approve_command(self):
        self._mk_workorder("auto/pref-a")
        item = _prefer_review_item(self.kdir, {
            "status": "proposed", "workorder": "auto/pref-a",
            "text": "always pin", "project": "tokto-io", "ts": "T",
            "producer": PREFER_QUEUE_PRODUCER, "sid": "s1"})
        self.assertEqual(item["approve"],
                         "scripts/agentware prefer approve auto/pref-a")
        self.assertTrue(item["workorder_exists"])
        self.assertEqual(item["signal"], "regex")
        self.assertEqual(item["sessions"], 1)

    def test_a_missing_workorder_dir_is_reported_not_filtered(self):
        """The item survives with a false flag — hiding it would shrink the open
        set the operator is being asked to resolve."""
        item = _prefer_review_item(self.kdir, {
            "status": "proposed", "workorder": "auto/pref-gone",
            "text": "always pin", "sid": "s1"})
        self.assertFalse(item["workorder_exists"])
        self.assertIn("approve", item)

    def test_a_non_proposed_item_carries_no_approve_command(self):
        self._mk_workorder("auto/pref-a")
        item = _prefer_review_item(self.kdir, {
            "status": "rejected", "workorder": "auto/pref-a", "text": "x",
            "sid": "s1"})
        self.assertNotIn("approve", item)

    def test_a_record_with_no_workorder_reports_no_existence_flag(self):
        item = _prefer_review_item(self.kdir, {"status": "queued",
                                               "text": "always x", "sid": "s1"})
        self.assertIsNone(item["workorder"])
        self.assertNotIn("workorder_exists", item)

    def test_a_missing_status_reads_as_unknown_rather_than_empty(self):
        item = _prefer_review_item(self.kdir, {"text": "x", "sid": "s1"})
        self.assertEqual(item["status"], "unknown")

    def test_non_string_text_is_normalised_to_an_empty_string(self):
        """Both surfaces render `text`; a non-str would raise in one of them."""
        item = _prefer_review_item(self.kdir, {"status": "queued",
                                               "text": {"not": "a string"},
                                               "sid": "s1"})
        self.assertEqual(item["text"], "")


class PreferReviewPrintTest(unittest.TestCase):
    def _item(self, **kw):
        base = {"workorder": "auto/pref-a", "status": "proposed",
                "signal": "regex", "sessions": 2, "project": None, "ts": "T",
                "text": "always pin versions", "workorder_exists": True,
                "approve": "scripts/agentware prefer approve auto/pref-a"}
        base.update(kw)
        return base

    def test_the_empty_case_names_the_scope_it_looked_at(self):
        rc, out = _printed([], False, {"proposed"})
        self.assertEqual(rc, 0)
        self.assertIn("no proposed candidate(s)", out)

    def test_the_empty_all_case_says_any_state(self):
        _rc, out = _printed([], True, {"proposed"})
        self.assertIn("any state", out)

    def test_the_open_set_gets_the_awaiting_approve_label(self):
        _rc, out = _printed([self._item()], False, {"proposed"})
        self.assertIn("1 open item(s) awaiting `prefer approve`", out)
        self.assertIn("approve:   scripts/agentware prefer approve auto/pref-a",
                      out)

    def test_a_widened_scope_gets_the_neutral_label(self):
        _rc, out = _printed([self._item()], False, {"proposed", "rejected"})
        self.assertIn("1 candidate(s)", out)

    def test_a_project_scope_is_shown_when_present(self):
        _rc, out = _printed([self._item(project="tokto-io")], False,
                            {"proposed"})
        self.assertIn("project:   tokto-io", out)

    def test_a_missing_workorder_dir_prints_the_note(self):
        _rc, out = _printed([self._item(workorder_exists=False)], False,
                            {"proposed"})
        self.assertIn("workorder dir is MISSING", out)

    def test_a_record_with_no_workorder_is_headed_by_its_status(self):
        _rc, out = _printed([self._item(workorder=None, status="queued",
                                        workorder_exists=None)],
                            True, {"proposed"})
        self.assertIn("(no workorder — queued)", out)

    def test_untrusted_candidate_text_is_collapsed_onto_one_quoted_line(self):
        """An embedded newline must not be able to fake a report line."""
        item = self._item(text="always pin\n- fake: entry\n  approve:   nope")
        _rc, out = _printed([item], False, {"proposed"})
        body = [l for l in out.splitlines() if l.startswith("  candidate:")]
        self.assertEqual(len(body), 1)
        self.assertIn("always pin - fake: entry approve: nope", body[0])


if __name__ == "__main__":
    unittest.main()
