"""The classify drain's own unit file — task 99 wave 2.

`agentware_lib.ops.preferclassify` had no owning unit file. `test_prefer_classify.py`
drives the whole drain over a synthetic KB, which is the right test for "does a
tier-2 candidate become a proposed workorder" and the wrong one for "what does
the drain do with a record whose emitter refused" — the whole-drain view reports
counts, so DEFER and DROP look the same from outside unless the queue is read
back and compared record by record.

Wave 2 split `_prefer_classify_drain` (24 branches / 125 lines) into a per-record
router and a persist step, and folded the report's four copy-pasted sections into
one `_classify_section`.

The properties that matter, and how each can be got wrong:

  DEFER≠DROP  — a draft-capped or emitter-refused candidate stays `queued` and is
                counted as deferred. Marking it anything else loses the candidate
                permanently; the record is the only copy.
  NO RESURRECT— an existing TERMINAL workorder means the candidate was already
                disposed of, so a re-queued copy is REJECTED rather than
                re-proposed into an approve-but-unrunnable dead end.
  CHANGED     — only a decided record makes the queue dirty. Rewriting the file
                for a purely deferred run would rewrite a file nothing changed
                in, under a lock, on every cycle.
  SECTIONS    — every report section renders heading, count, rows or `_none_`,
                and a trailing blank — identically, because four hand-written
                copies is how one of them quietly starts sorting differently.
  NONE        — an EMPTY section prints `_none_` rather than disappearing: a
                section that vanishes reads as "not measured".

Hermetic: pure functions over in-memory records and tempdirs; no CLI.
"""

from tests._paths import TESTS_DIR

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.ops.preferclassify as classify  # noqa: E402
from agentware_lib.ops.preferclassify import (  # noqa: E402
    _classify_section,
    _drain_classify_record,
    _drain_persist,
    _render_prefer_classify_report,
)


def _rec(text, **kw):
    base = {"sid": "s1", "turn_id": "t1", "text": text, "status": "queued"}
    base.update(kw)
    return base


class DrainClassifyRecordTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_pc_drain_")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def test_a_kernel_path_candidate_is_rejected_as_tier_three(self):
        rec = _rec("always edit scripts/agentware directly")
        self.assertEqual(_drain_classify_record(self.kdir, rec), "rejected")
        self.assertEqual((rec["status"], rec["tier"]), ("rejected", 3))

    def test_an_inert_candidate_is_journaled_as_tier_one(self):
        rec = _rec("always")
        self.assertEqual(_drain_classify_record(self.kdir, rec), "journaled")
        self.assertEqual((rec["status"], rec["tier"]), ("journaled", 1))

    def test_a_tier_two_candidate_becomes_a_proposed_workorder(self):
        rec = _rec("always pin dependency versions in this repo")
        with mock.patch.object(classify, "_prefer_emit_workorder",
                               return_value=("auto/pref-x-1", None)):
            self.assertEqual(_drain_classify_record(self.kdir, rec), "proposed")
        self.assertEqual(rec["status"], "proposed")
        self.assertEqual(rec["tier"], 2)
        self.assertEqual(rec["workorder"], "auto/pref-x-1")

    def test_a_draft_capped_candidate_is_deferred_and_left_queued(self):
        """DEFER IS NOT DROP: the record is the only copy of the candidate, so a
        capped cycle must leave it exactly as it found it."""
        rec = _rec("always pin dependency versions in this repo")
        with mock.patch.object(classify, "_count_auto_open_workorders",
                               return_value=classify.WORKORDER_DRAFT_CAP):
            self.assertEqual(_drain_classify_record(self.kdir, rec), "deferred")
        self.assertEqual(rec["status"], "queued")
        self.assertNotIn("tier", rec)

    def test_an_emitter_refusal_defers_rather_than_dropping(self):
        rec = _rec("always pin dependency versions in this repo")
        with mock.patch.object(classify, "_prefer_emit_workorder",
                               return_value=(None, "emitter refused (rc=1)")):
            self.assertEqual(_drain_classify_record(self.kdir, rec), "deferred")
        self.assertEqual(rec["status"], "queued")

    def test_a_terminal_workorder_rejects_the_candidate_instead_of_reproposing(self):
        """The resurrection guard: an already-cancelled candidate that got
        re-queued must not come back as `proposed`."""
        rec = _rec("always pin dependency versions in this repo")
        with mock.patch.object(classify, "_prefer_emit_workorder",
                               return_value=(None, "resolved:cancelled")):
            self.assertEqual(_drain_classify_record(self.kdir, rec), "rejected")
        self.assertEqual(rec["status"], "rejected")
        self.assertEqual(rec["tier"], 2)
        self.assertEqual(rec["rejected_reason"], "workorder already cancelled")


class DrainPersistTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_pc_persist_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.qpath = os.path.join(self.kdir, "queue.jsonl")
        with open(self.qpath, "w", encoding="utf-8") as f:
            f.write("ORIGINAL\n")

    def test_an_unchanged_run_leaves_the_queue_file_untouched(self):
        """Nothing decided means nothing to write: a rewrite here would churn a
        file under a lock on every idle cycle."""
        rel = _drain_persist(self.kdir, self.qpath, [_rec("x")], False, "body\n")
        with open(self.qpath, encoding="utf-8") as f:
            self.assertEqual(f.read(), "ORIGINAL\n")
        self.assertEqual(rel, classify.PREFER_CLASSIFY_REPORT_REL)

    def test_a_changed_run_rewrites_the_queue_from_the_records(self):
        recs = [_rec("a", status="rejected"), _rec("b", status="journaled")]
        _drain_persist(self.kdir, self.qpath, recs, True, "body\n")
        with open(self.qpath, encoding="utf-8") as f:
            lines = [l for l in f.read().split("\n") if l]
        self.assertEqual(len(lines), 2)
        self.assertIn('"status": "rejected"', lines[0])

    def test_it_writes_the_report_and_returns_its_relative_path(self):
        rel = _drain_persist(self.kdir, self.qpath, [], False, "REPORT BODY\n")
        with open(os.path.join(self.kdir, rel), encoding="utf-8") as f:
            self.assertEqual(f.read(), "REPORT BODY\n")

    def test_an_unwritable_report_degrades_to_none_rather_than_failing(self):
        """Losing a rendered report is recoverable next cycle; raising here
        would make the offline dream cycle red for a file nobody read yet."""
        with mock.patch("agentware_lib.ops.preferclassify.os.makedirs",
                        side_effect=OSError("read-only")):
            rel = _drain_persist(self.kdir, self.qpath, [], False, "body\n")
        self.assertIsNone(rel)

    def test_rotation_archives_the_overflow_and_keeps_the_live_queue_bounded(self):
        cap = classify._PREFER_QUEUE_CAP
        recs = ([_rec("t%d" % i, status="rejected") for i in range(cap)] +
                [_rec("live", status="queued")])
        _drain_persist(self.kdir, self.qpath, recs, True, "body\n")
        with open(self.qpath, encoding="utf-8") as f:
            kept = [l for l in f.read().split("\n") if l]
        self.assertEqual(len(kept), cap)
        apath = os.path.join(self.kdir, classify.PREFER_QUEUE_ARCHIVE_REL)
        with open(apath, encoding="utf-8") as f:
            archived = [l for l in f.read().split("\n") if l]
        self.assertEqual(len(archived), 1)
        self.assertIn('"text": "t0"', archived[0])   # oldest terminal first


class ClassifySectionTest(unittest.TestCase):
    def test_a_populated_section_is_heading_blank_rows_blank(self):
        lines = []
        _classify_section(lines, "Title", [{"t": "b"}, {"t": "a"}],
                          lambda r: r["t"], lambda r: ["- %s" % r["t"]])
        self.assertEqual(lines, ["## Title (2)", "", "- a", "- b", ""])

    def test_an_empty_section_prints_none_rather_than_disappearing(self):
        lines = []
        _classify_section(lines, "Title", [], lambda r: "", lambda r: [])
        self.assertEqual(lines, ["## Title (0)", "", "_none_", ""])

    def test_the_formatter_may_emit_several_lines_per_record(self):
        lines = []
        _classify_section(lines, "T", [{"t": "x"}], lambda r: r["t"],
                          lambda r: ["- %s" % r["t"], "  more"])
        self.assertEqual(lines, ["## T (1)", "", "- x", "  more", ""])


class RenderClassifyReportTest(unittest.TestCase):
    def test_every_tier_gets_its_own_section_with_a_count(self):
        recs = [{"status": "proposed", "workorder": "auto/pref-a",
                 "text": "always a"},
                {"status": "rejected", "text": "always b"},
                {"status": "journaled", "text": "always c"},
                {"status": "queued", "text": "always d"}]
        body = _render_prefer_classify_report(recs, 1, 1, 1, 1)
        for heading in ("## Proposed steering prefs awaiting approve (1)",
                        "## Rejected — tier-3 kernel-path / gate-loosening (1)",
                        "## Journaled — tier-0/1 inert (1)",
                        "## Deferred — still queued (draft cap) (1)"):
            self.assertIn(heading, body)
        self.assertIn("this run: proposed=1 rejected=1 journaled=1 deferred=1",
                      body)
        self.assertIn("approve: `scripts/agentware prefer approve auto/pref-a`",
                      body)

    def test_a_non_object_queue_line_cannot_crash_the_render(self):
        body = _render_prefer_classify_report([[1, 2, 3], "str", None], 0, 0,
                                              0, 0)
        self.assertEqual(body.count("_none_"), 4)

    def test_an_all_empty_report_still_names_all_four_sections(self):
        body = _render_prefer_classify_report([], 0, 0, 0, 0)
        self.assertEqual(body.count("## "), 4)
        self.assertTrue(body.endswith("_none_\n"))


if __name__ == "__main__":
    unittest.main()
