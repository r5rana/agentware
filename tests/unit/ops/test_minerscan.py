"""`minerscan`'s own unit file — task 99 wave 3.

`agentware_lib.ops.sessionparse.minerscan` had no owning unit file. Its behaviour
was covered by the five `test_transcript_miner*` files, which are named for the
MINER's end-to-end behaviour and drive it through `_transcript_mine` over a
scratch KB. Those are the right tests for "does a nightly mine enqueue this
candidate once"; they are the wrong ones for "does the state advance
monotonically", because `_transcript_mine` returns an int and swallows every
fault — a fold that stopped working and a corpus with nothing in it are the same
observation from outside.

Wave 3 split the four passes into their detection halves (`_recurrence_fold`,
`_recurrence_latest`, `_errrec_pairs`) and the persistence halves
(`_mine_new_lines`, `_mine_advance_state`), moved the corpus READ into
`minerread`, and put `_transcript_mine` on the queue's OWN `_prefer_queue_lock`.
Each is asserted here on concrete values.

The properties that matter, and how each can be got wrong:

  FOLD/DISTINCT — the tally counts DISTINCT SESSIONS, not occurrences: a body
                  asked twice in one session is one session (F4). Counting
                  occurrences would let a single session reach the recurrence
                  threshold on its own.
  FOLD/LRU      — a touched key is re-inserted at the END of the tally, because
                  the prune evicts from the front. Folding without re-inserting
                  would make the most-recently-seen key the first evicted.
  FOLD/LATEST   — `latest` is overwritten each fold, so the emitted candidate's
                  identity is the MOST RECENT occurrence and a re-mine of the
                  same window yields the same fingerprint.
  LATEST/COERCE — every field coerces to its empty default, because the record is
                  APPENDED to a producer-agnostic queue and a None there becomes
                  a null in another reader.
  PAIRS/BARE    — a bare error with no later success yields NOTHING. This is the
                  conservative half of the detector: "it failed" is not a
                  learning, "it failed and this fixed it" is.
  PAIRS/FIRST   — the FIRST error signature per target is kept. A target that
                  failed twice before recovering is one recovery, signed by the
                  failure that started it.
  PAIRS/ONCE    — one pair per (tool, target) per session, so a flapping target
                  cannot flood the queue.
  LINES/BATCH   — the fp belt dedups WITHIN one batch too, not only against the
                  queue: the two detectors run over the same window and can
                  independently derive the same (sid, turn, text).
  STATE/MAX     — offsets advance by max(), never by assignment, so two racing
                  mines cannot rewind the watermark.
  STATE/SETTLED — only a SETTLED session is marked processed; an in-flight one
                  stays pending so a later window re-scans it.
  MINE/LOCK     — the miner takes the QUEUE's lock, shared with the Stop-hook
                  producer. A different lock on the same file is no lock at all,
                  and a lock that cannot be taken is "queued nothing", never a
                  raise into the offline cycle.

Hermetic: tempdirs and in-memory dicts; no CLI, no network, stdlib unittest only.
"""

from tests._paths import TESTS_DIR

import contextlib
import json
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

import agentware_lib.ops.sessionparse.minerscan as minerscan  # noqa: E402
from agentware_lib.ops.sessionparse.minerscan import (  # noqa: E402
    _errrec_pairs,
    _mine_advance_state,
    _mine_new_lines,
    _recurrence_fold,
    _recurrence_latest,
    _transcript_build_record,
    _transcript_mine,
    _transcript_queue_fps,
)


def _call(tool, status, target, response="", ts="2026-08-01T00:00:00Z"):
    return {"ts": ts, "tool": tool, "status": status,
            "input": json.dumps({"file_path": target}) if target else "{}",
            "response": response}


class RecurrenceFoldTest(unittest.TestCase):
    def test_a_body_asked_twice_in_one_session_counts_as_one_session(self):
        """FOLD/DISTINCT."""
        tally = {}
        _recurrence_fold([("s1", "t1", "always pin versions", ""),
                          ("s1", "t2", "always pin versions", "")], tally)
        entry = list(tally.values())[0]
        self.assertEqual(entry["sessions"], ["s1"])

    def test_distinct_sessions_accumulate_across_calls(self):
        tally = {}
        _recurrence_fold([("s1", "t1", "always pin versions", "")], tally)
        _recurrence_fold([("s2", "t1", "always pin versions", "")], tally)
        self.assertEqual(list(tally.values())[0]["sessions"], ["s1", "s2"])

    def test_a_touched_key_moves_to_the_end_of_the_tally(self):
        """FOLD/LRU: the prune evicts from the front."""
        tally = {}
        _recurrence_fold([("s1", "t1", "always pin versions", ""),
                          ("s1", "t2", "never edit the kernel by hand", "")],
                         tally)
        first = list(tally)[0]
        _recurrence_fold([("s2", "t3", "always pin versions", "")], tally)
        self.assertEqual(list(tally)[-1], first)

    def test_latest_is_overwritten_by_the_most_recent_occurrence(self):
        """FOLD/LATEST."""
        tally = {}
        _recurrence_fold([("s1", "t1", "always pin versions", "/a"),
                          ("s2", "t9", "always  pin   versions", "/b")], tally)
        latest = list(tally.values())[0]["latest"]
        self.assertEqual((latest["sid"], latest["turn_id"], latest["cwd"]),
                         ("s2", "t9", "/b"))

    def test_it_returns_touched_keys_once_in_first_touch_order(self):
        tally = {}
        touched = _recurrence_fold([
            ("s1", "t1", "always pin versions", ""),
            ("s1", "t2", "never edit the kernel by hand", ""),
            ("s1", "t3", "always pin versions", ""),
        ], tally)
        self.assertEqual(touched, ["always pin versions",
                                   "never edit the kernel by hand"])
        # first-touch order is NOT tally order: the re-fold at t3 LRU-moved the
        # first key to the end, which is exactly the eviction protection above.
        self.assertEqual(list(tally)[-1], touched[0])

    def test_a_body_that_normalises_to_nothing_is_skipped(self):
        tally = {}
        self.assertEqual(_recurrence_fold([("s1", "t1", "", ""),
                                           ("s1", "t2", "   ", "")], tally), [])
        self.assertEqual(tally, {})

    def test_a_corrupt_entry_at_a_folded_key_is_replaced_rather_than_crashing(self):
        """The tally is persisted JSON an operator can hand-edit, so a non-dict
        at a live key must be rebuilt rather than crash the mine."""
        tally = {}
        _recurrence_fold([("s1", "t1", "always pin versions", "")], tally)
        key = list(tally)[0]
        tally[key] = "not a dict"
        _recurrence_fold([("s2", "t1", "always pin versions", "")], tally)
        self.assertIsInstance(tally[key], dict)
        self.assertEqual(tally[key]["sessions"], ["s2"])


class RecurrenceLatestTest(unittest.TestCase):
    def test_every_field_coerces_to_its_empty_default(self):
        """LATEST/COERCE: a None here becomes a null in another producer's
        reader, because the queue is shared and producer-agnostic."""
        self.assertEqual(_recurrence_latest({}),
                         {"sid": "", "turn_id": "", "text": "", "cwd": "",
                          "source_sessions": []})
        self.assertEqual(_recurrence_latest({"latest": None,
                                             "sessions": None}),
                         {"sid": "", "turn_id": "", "text": "", "cwd": "",
                          "source_sessions": []})
        self.assertEqual(
            _recurrence_latest({"latest": {"sid": None, "turn_id": None,
                                           "text": None, "cwd": None}}),
            {"sid": "", "turn_id": "", "text": "", "cwd": "",
             "source_sessions": []})

    def test_a_populated_entry_passes_through(self):
        got = _recurrence_latest({"latest": {"sid": "s1", "turn_id": "t1",
                                             "text": "body", "cwd": "/c"},
                                  "sessions": ["s1", "s2"]})
        self.assertEqual(got, {"sid": "s1", "turn_id": "t1", "text": "body",
                               "cwd": "/c", "source_sessions": ["s1", "s2"]})


class ErrrecPairsTest(unittest.TestCase):
    def test_a_bare_error_with_no_later_success_yields_nothing(self):
        """PAIRS/BARE: the conservative half of the detector."""
        self.assertEqual(_errrec_pairs([_call("Edit", "ERR", "/a.py", "boom")]),
                         [])

    def test_a_hit_and_fixed_pair_is_detected(self):
        pairs = _errrec_pairs([_call("Edit", "ERR", "/a.py", "error: refused"),
                               _call("Edit", "ok", "/a.py")])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0], "Edit")
        self.assertIn("error: refused", pairs[0][2])

    def test_a_success_before_the_error_does_not_pair(self):
        self.assertEqual(_errrec_pairs([_call("Edit", "ok", "/a.py"),
                                        _call("Edit", "ERR", "/a.py", "boom")]),
                         [])

    def test_the_first_error_signature_is_the_one_kept(self):
        """PAIRS/FIRST."""
        pairs = _errrec_pairs([_call("Bash", "ERR", "/b.sh", "error: first"),
                               _call("Bash", "ERR", "/b.sh", "error: second"),
                               _call("Bash", "ok", "/b.sh")])
        self.assertIn("first", pairs[0][2])
        self.assertNotIn("second", pairs[0][2])

    def test_one_pair_per_tool_and_target_even_when_it_flaps(self):
        """PAIRS/ONCE."""
        pairs = _errrec_pairs([_call("Edit", "ERR", "/a.py", "boom"),
                               _call("Edit", "ok", "/a.py"),
                               _call("Edit", "ERR", "/a.py", "boom again"),
                               _call("Edit", "ok", "/a.py")])
        self.assertEqual(len(pairs), 1)

    def test_a_different_target_is_a_different_pair(self):
        pairs = _errrec_pairs([_call("Edit", "ERR", "/a.py", "boom"),
                               _call("Edit", "ERR", "/b.py", "boom"),
                               _call("Edit", "ok", "/a.py"),
                               _call("Edit", "ok", "/b.py")])
        self.assertEqual(len(pairs), 2)

    def test_an_error_with_no_resolvable_target_is_unpairable(self):
        self.assertEqual(_errrec_pairs([_call("Bash", "ERR", None, "boom"),
                                        _call("Bash", "ok", None)]), [])

    def test_non_dict_records_are_skipped(self):
        pairs = _errrec_pairs(["not a dict", None, 7,
                               _call("Edit", "ERR", "/a.py", "boom"),
                               _call("Edit", "ok", "/a.py")])
        self.assertEqual(len(pairs), 1)


class MineNewLinesTest(unittest.TestCase):
    def test_it_dedups_within_one_batch(self):
        """LINES/BATCH: the two detectors can independently derive the same
        (sid, turn, text) from one window."""
        rec = {"sid": "s1", "turn_id": "t1", "text": "same"}
        lines = _mine_new_lines([dict(rec), dict(rec)], set())
        self.assertEqual(len(lines), 1)

    def test_a_fingerprint_already_seen_is_suppressed_and_seen_grows(self):
        rec = {"sid": "s1", "turn_id": "t1", "text": "same"}
        seen = set()
        self.assertEqual(len(_mine_new_lines([dict(rec)], seen)), 1)
        self.assertEqual(len(seen), 1)
        self.assertEqual(_mine_new_lines([dict(rec)], seen), [])

    def test_each_line_is_one_json_object_with_unescaped_unicode(self):
        lines = _mine_new_lines([{"sid": "s1", "turn_id": "t1",
                                  "text": "naïve"}], set())
        self.assertIn("naïve", lines[0])
        self.assertEqual(json.loads(lines[0])["text"], "naïve")


class MineAdvanceStateTest(unittest.TestCase):
    def _state(self, prompts=0, metrics=0, processed=(), pending=()):
        return {"offsets": {"prompts": prompts, "metrics": metrics},
                "processed_sessions": list(processed),
                "backfill_pending": list(pending)}

    def _read(self, prompts, metrics, scanned):
        return {"new_offsets": {"prompts": prompts, "metrics": metrics},
                "scanned": scanned}

    def test_offsets_advance_but_never_rewind(self):
        """STATE/MAX: a racing mine must not be able to move the watermark
        backwards, which would re-mine an already-settled window forever."""
        state = self._state(prompts=500, metrics=90)
        _mine_advance_state(state, self._read(100, 900, []))
        self.assertEqual(state["offsets"], {"prompts": 500, "metrics": 900})

    def test_a_null_stored_offset_reads_as_zero(self):
        state = {"offsets": {"prompts": None, "metrics": None},
                 "processed_sessions": [], "backfill_pending": []}
        _mine_advance_state(state, self._read(10, 20, []))
        self.assertEqual(state["offsets"], {"prompts": 10, "metrics": 20})

    def test_only_a_settled_session_is_marked_processed(self):
        """STATE/SETTLED: an in-flight session stays pending so a later window
        re-scans it — its recovery has not necessarily happened yet."""
        state = self._state(pending=["s1", "s2"])
        _mine_advance_state(state, self._read(0, 0, [("s1", [], True),
                                                     ("s2", [], False)]))
        self.assertEqual(state["processed_sessions"], ["s1"])
        self.assertEqual(state["backfill_pending"], ["s2"])

    def test_the_session_sets_stay_sorted_and_deduped(self):
        state = self._state(processed=["s9", "s9", "s1"])
        _mine_advance_state(state, self._read(0, 0, [("s5", [], True)]))
        self.assertEqual(state["processed_sessions"], ["s1", "s5", "s9"])


class QueueFpsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aw_mscan_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.qpath = os.path.join(self.dir, "prefer-queue.jsonl")

    def test_an_absent_or_unreadable_queue_is_an_empty_belt(self):
        self.assertEqual(_transcript_queue_fps(self.qpath), set())
        self.assertEqual(_transcript_queue_fps(self.dir), set())

    def test_it_reads_objects_and_tolerates_everything_else(self):
        with open(self.qpath, "w", encoding="utf-8") as f:
            f.write(json.dumps({"sid": "s", "turn_id": "t", "text": "x"}) + "\n")
            f.write("\n{broken\n[1,2]\n3\n")
            f.write(json.dumps({"sid": "s2", "turn_id": "t", "text": "y"}) + "\n")
        self.assertEqual(len(_transcript_queue_fps(self.qpath)), 2)

    def test_the_belt_matches_the_fingerprint_a_record_would_produce(self):
        rec = {"sid": "s1", "turn_id": "t1", "text": "a body"}
        with open(self.qpath, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        self.assertEqual(_mine_new_lines([dict(rec)],
                                         _transcript_queue_fps(self.qpath)), [])


class BuildRecordTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_mscan_kb_")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def test_the_stored_text_is_redacted_at_the_boundary(self):
        """The record is COMMITTED downstream, so redaction has to happen where
        a raw transcript body becomes a persisted one."""
        rec = _transcript_build_record(
            self.kdir, "s1", "t1",
            "deploy with sk-live-AAAABBBBCCCCDDDDEEEEFFFF", "recurrence")
        self.assertNotIn("sk-live-AAAABBBBCCCCDDDDEEEEFFFF", rec["text"])

    def test_a_non_string_text_stores_the_empty_string(self):
        self.assertEqual(_transcript_build_record(self.kdir, "", "", 42,
                                                  "error-recovery")["text"], "")

    def test_source_sessions_are_deduped_sorted_and_capped(self):
        rec = _transcript_build_record(
            self.kdir, "s", "t", "body", "recurrence",
            source_sessions=["s2", "s1", "s1"])
        self.assertEqual(rec["source_sessions"], ["s1", "s2"])

    def test_the_optional_fields_are_absent_when_not_supplied(self):
        rec = _transcript_build_record(self.kdir, "s", "t", "body", "signal")
        self.assertNotIn("recurrence_count", rec)
        self.assertNotIn("source_sessions", rec)


class TranscriptMineLockTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_mscan_mine_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        os.makedirs(os.path.join(self.kdir, "logs", "sessions"), exist_ok=True)

    def test_an_empty_kdir_short_circuits(self):
        self.assertEqual(_transcript_mine(""), 0)
        self.assertEqual(_transcript_mine(None), 0)

    def test_a_lock_that_cannot_be_taken_queues_nothing_and_never_raises(self):
        """MINE/LOCK: this runs inside an offline maintenance cycle; a raise
        would abort the whole cycle over a contended file."""
        @contextlib.contextmanager
        def _no_lock(_qpath):
            yield None

        with mock.patch.object(minerscan, "_prefer_queue_lock", _no_lock):
            self.assertEqual(_transcript_mine(self.kdir), 0)
        qpath = os.path.join(self.kdir, "logs", "steering",
                             "prefer-queue.jsonl")
        self.assertFalse(os.path.exists(qpath))

    def test_it_takes_the_queues_own_lock_path(self):
        """MINE/LOCK: a lock on any other path is no lock at all — the Stop-hook
        producer contends on exactly this one."""
        seen = []

        @contextlib.contextmanager
        def _spy(qpath):
            seen.append(qpath)
            yield None

        with mock.patch.object(minerscan, "_prefer_queue_lock", _spy):
            _transcript_mine(self.kdir)
        self.assertEqual(seen, [os.path.join(self.kdir, "logs", "steering",
                                             "prefer-queue.jsonl")])

    def test_an_unwritable_queue_directory_degrades_to_zero(self):
        path = os.path.join(self.kdir, "logs", "steering")
        with open(path, "w", encoding="utf-8") as f:
            f.write("a file where the directory should be\n")
        self.assertEqual(_transcript_mine(self.kdir), 0)


if __name__ == "__main__":
    unittest.main()
