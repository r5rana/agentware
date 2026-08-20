"""The prefer-queue's own unit file — task 99 wave 2.

`agentware_lib.ops.preferqueue` had no owning unit file. Its behaviour was
covered by `test_prefer_queue.py` and `test_prefer_queue_redaction.py`, which are
named for the PRODUCER's behaviour and drive it end to end through
`_prefer_queue_scan` over a synthetic KB. Those are the right tests for "does a
Stop hook queue this turn once"; they are the wrong ones for "what does the lock
do when flock fails", because a whole-producer test can only observe the producer
returning 0 — every degradation path inside it looks identical from outside.

Wave 2 split `_prefer_queue_scan` (27 branches / 111 lines) and
`_prefer_seed_cursor` (13 branches) into five helpers, three of them SHARED with
`preferdispose`, `preferclassify`, `prefercapture` and `prefercmd` — the flock
ladder and the JSONL reader were written out at five and six sites respectively.
Sharing them is only safe if each is pinned on its own, so each is asserted here
on concrete values and on the degradation the sharing now makes uniform.

The properties that matter, and how each can be got wrong:

  LOCK/NONE   — a lock that cannot be taken yields None rather than raising.
                Raising would take down a Stop hook; a truthy sentinel would let
                every caller proceed unlocked, which is worse than not locking.
  LOCK/FD     — a FAILED flock still closes the handle it opened. The two
                producers run on every session start and stop, so a leaked fd
                per failure is a leak per session.
  READ/OPEN   — the reader tolerates CONTENT (blank, non-JSON, non-object) and
                propagates ACCESS errors. Swallowing OSError would make "I could
                not read your queue" indistinguishable from "your queue is
                empty" at four call sites that report those differently.
  READ/KEEP   — `objects_only=False` keeps a non-object line, because the drain
                REWRITES what it reads and must not delete a line it cannot
                parse.
  ADMIT       — redaction happens INSIDE the admission chain, so the stored text,
                the cue and the fingerprint all derive from the same redacted
                string.
  LRU         — the advanced cursor re-inserts the current sid at the END and the
                prune can never evict it.

Hermetic: tempdirs and in-memory dicts; no CLI, no network, stdlib unittest only.
"""

from tests._paths import TESTS_DIR

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

from agentware_lib.ops.preferqueue import (  # noqa: E402
    PREFER_QUEUE_PRODUCER,
    _PREFER_CURSOR_CAP,
    _prefer_queue_lock,
    _prefer_queue_records,
    _queue_admitted_text,
    _queue_advance_cursor,
    _queue_load_cursors,
    _queue_record,
)


def _hdr(sid="s1", origin="human", turn="t1", cwd=""):
    return {"sid": sid, "origin": origin, "turn": turn, "cwd": cwd,
            "ts": "2026-08-20T00:00:00Z"}


class PreferQueueLockTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aw_pq_lock_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.qpath = os.path.join(self.dir, "prefer-queue.jsonl")

    def test_it_yields_a_usable_handle_and_releases_it_afterwards(self):
        """The happy path hands back the open lock file and closes it on exit."""
        with _prefer_queue_lock(self.qpath) as lf:
            self.assertIsNotNone(lf)
            self.assertFalse(lf.closed)
            held = lf
        self.assertTrue(held.closed)
        # Releasing means a second acquisition succeeds rather than blocking.
        with _prefer_queue_lock(self.qpath) as lf2:
            self.assertIsNotNone(lf2)

    def test_it_yields_none_when_the_lock_file_cannot_be_opened(self):
        """An unopenable lock path degrades to None — never an exception.

        The producers run inside the session-start and Stop hooks, so raising
        here would make steering capture able to fail the session itself.
        """
        missing = os.path.join(self.dir, "no-such-dir", "queue.jsonl")
        with _prefer_queue_lock(missing) as lf:
            self.assertIsNone(lf)

    def test_a_failed_flock_yields_none_and_closes_the_handle_it_opened(self):
        """The fd is not leaked when flock fails after open succeeded.

        This is the arm no whole-producer test can see: from outside, a scan that
        could not lock and a scan that locked and queued nothing both return 0.
        """
        opened = []
        real_open = open

        def _tracking_open(path, *a, **kw):
            handle = real_open(path, *a, **kw)
            opened.append(handle)
            return handle

        with mock.patch("builtins.open", _tracking_open), \
                mock.patch("fcntl.flock", side_effect=OSError("busy")):
            with _prefer_queue_lock(self.qpath) as lf:
                self.assertIsNone(lf)
        self.assertTrue(opened, "the lock file was never opened")
        self.assertTrue(all(h.closed for h in opened),
                        "a failed flock leaked its file handle")

    def test_it_unlocks_and_closes_even_when_the_body_raises(self):
        """An exception inside the block still releases the lock."""
        with self.assertRaises(ValueError):
            with _prefer_queue_lock(self.qpath) as lf:
                held = lf
                raise ValueError("boom")
        self.assertTrue(held.closed)
        with _prefer_queue_lock(self.qpath) as lf2:
            self.assertIsNotNone(lf2)


class PreferQueueRecordsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aw_pq_rec_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.qpath = os.path.join(self.dir, "q.jsonl")

    def _write(self, lines):
        with open(self.qpath, "w", encoding="utf-8") as f:
            f.write("".join(l + "\n" for l in lines))

    def test_it_skips_blank_unparseable_and_non_object_lines(self):
        """Content the queue's readers cannot use is skipped, not fatal."""
        self._write(['{"id": 1}', "", "   ", "not json at all", "[1, 2, 3]",
                     '"a string"', '{"id": 2}'])
        self.assertEqual([r["id"] for r in _prefer_queue_records(self.qpath)],
                         [1, 2])

    def test_objects_only_false_keeps_a_non_object_line(self):
        """The drain rewrites what it reads, so a line it cannot parse as a
        record must survive the round trip rather than be deleted."""
        self._write(['{"id": 1}', "[1, 2, 3]", '{"id": 2}'])
        kept = _prefer_queue_records(self.qpath, objects_only=False)
        self.assertEqual(kept, [{"id": 1}, [1, 2, 3], {"id": 2}])

    def test_an_unreadable_queue_raises_rather_than_reading_as_empty(self):
        """ACCESS failures propagate: 'cannot read' and 'is empty' are different
        facts, and the callers report them differently."""
        with self.assertRaises(OSError):
            _prefer_queue_records(os.path.join(self.dir, "absent.jsonl"))

    def test_it_preserves_file_order(self):
        self._write(['{"n": %d}' % i for i in range(5)])
        self.assertEqual([r["n"] for r in _prefer_queue_records(self.qpath)],
                         [0, 1, 2, 3, 4])


class QueueLoadCursorsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aw_pq_cur_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.cpath = os.path.join(self.dir, "cursor.json")

    def test_a_missing_file_is_an_empty_watermark_set(self):
        self.assertEqual(_queue_load_cursors(self.cpath), {})

    def test_a_corrupt_file_degrades_to_empty_rather_than_raising(self):
        """An interrupted write must not wedge the next session's start."""
        with open(self.cpath, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(_queue_load_cursors(self.cpath), {})

    def test_a_non_dict_payload_degrades_to_empty(self):
        with open(self.cpath, "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")
        self.assertEqual(_queue_load_cursors(self.cpath), {})

    def test_a_well_formed_watermark_dict_is_returned_verbatim(self):
        with open(self.cpath, "w", encoding="utf-8") as f:
            json.dump({"s1": 42, "s2": 7}, f)
        self.assertEqual(_queue_load_cursors(self.cpath), {"s1": 42, "s2": 7})


class QueueAdmittedTextTest(unittest.TestCase):
    def test_another_sessions_turn_is_refused(self):
        self.assertEqual(
            _queue_admitted_text(_hdr(sid="other"), "always pin versions", "s1"),
            (None, None))

    def test_a_non_human_turn_is_refused(self):
        self.assertEqual(
            _queue_admitted_text(_hdr(origin="loop"), "always pin versions",
                                 "s1"),
            (None, None))

    def test_an_empty_body_is_refused(self):
        self.assertEqual(_queue_admitted_text(_hdr(), "   \n  ", "s1"),
                         (None, None))

    def test_a_turn_with_no_preference_cue_is_refused(self):
        self.assertEqual(
            _queue_admitted_text(_hdr(), "just a status update", "s1"),
            (None, None))

    def test_a_cue_only_inside_quoted_back_agent_text_is_refused(self):
        """The operator-turn gate: a keyword the operator merely QUOTED is not
        the operator asking for it."""
        body = "> the agent said: always run the linter\nsounds good"
        self.assertEqual(_queue_admitted_text(_hdr(), body, "s1"),
                         (None, None))

    def test_an_admitted_turn_returns_its_text_and_the_matching_cue(self):
        text, cue = _queue_admitted_text(_hdr(), "Always pin versions", "s1")
        self.assertEqual(text, "Always pin versions")
        self.assertEqual(cue.group(0).lower(), "always")

    def test_the_returned_text_is_already_redacted(self):
        """Redaction is INSIDE the chain, so everything derived downstream — the
        stored text, `matched_pattern` and the fingerprint — derives from the
        redacted string rather than from the raw turn."""
        raw = ("from now on deploy with -H 'Authorization: Bearer "
               "sk-live-AAAABBBBCCCCDDDDEEEEFFFF'")
        text, cue = _queue_admitted_text(_hdr(), raw, "s1")
        self.assertIsNotNone(text)
        self.assertNotIn("sk-live-AAAABBBBCCCCDDDDEEEEFFFF", text)
        self.assertIn("from now on", text.lower())


class QueueRecordTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_pq_kdir_")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def _cue(self, text):
        _txt, cue = _queue_admitted_text(_hdr(), text, "s1")
        return cue

    def test_it_carries_the_shared_producer_schema(self):
        rec = _queue_record(self.kdir, _hdr(), "always pin versions",
                            self._cue("always pin versions"), "s1", "")
        self.assertEqual(
            sorted(rec),
            sorted(["ts", "sid", "cwd", "project", "turn_id", "text",
                    "matched_pattern", "status", "producer"]))
        self.assertEqual(rec["status"], "queued")
        self.assertEqual(rec["producer"], PREFER_QUEUE_PRODUCER)
        self.assertEqual(rec["text"], "always pin versions")
        self.assertEqual(rec["matched_pattern"], "always")

    def test_the_records_cwd_wins_over_the_callers_fallback(self):
        rec = _queue_record(self.kdir, _hdr(cwd="/from/header"), "always x",
                            self._cue("always x"), "s1", "/from/caller")
        self.assertEqual(rec["cwd"], "/from/header")

    def test_the_callers_cwd_is_the_fallback_when_the_record_omits_it(self):
        rec = _queue_record(self.kdir, _hdr(cwd=""), "always x",
                            self._cue("always x"), "s1", "/from/caller")
        self.assertEqual(rec["cwd"], "/from/caller")

    def test_an_unresolvable_checkout_scopes_the_candidate_globally(self):
        """Context resolution is best-effort: a checkout that raises must not
        fail the whole scan, it must leave the candidate global."""
        with mock.patch("agentware_lib.ops.preferqueue._resolve_context",
                        side_effect=RuntimeError("nope")):
            rec = _queue_record(self.kdir, _hdr(cwd="/gone"), "always x",
                                self._cue("always x"), "s1", "")
        self.assertIsNone(rec["project"])


class QueueAdvanceCursorTest(unittest.TestCase):
    def test_it_sets_the_watermark_for_the_current_session(self):
        self.assertEqual(_queue_advance_cursor({}, "s1", 99), {"s1": 99})

    def test_the_current_session_is_re_inserted_last_for_lru_order(self):
        """Dict order IS the recency order the prune reads, so an updated sid
        must MOVE rather than update in place."""
        cursors = _queue_advance_cursor({"s1": 1, "s2": 2}, "s1", 5)
        self.assertEqual(list(cursors), ["s2", "s1"])
        self.assertEqual(cursors["s1"], 5)

    def test_it_prunes_the_least_recently_active_sessions_past_the_cap(self):
        cursors = {"s%d" % i: i for i in range(_PREFER_CURSOR_CAP + 10)}
        out = _queue_advance_cursor(dict(cursors), "new-sid", 7)
        self.assertEqual(len(out), _PREFER_CURSOR_CAP)
        self.assertIn("new-sid", out)
        self.assertNotIn("s0", out)          # oldest evicted first

    def test_the_current_session_survives_a_prune_that_would_evict_it(self):
        """An ACTIVE session evicted from its own watermark would re-seed to EOF
        and skip its logged-but-unscanned turns — silent loss, not a slow path."""
        cursors = {"s%d" % i: i for i in range(_PREFER_CURSOR_CAP + 5)}
        cursors["victim"] = 3
        cursors = {k: v for k, v in cursors.items()}
        # Put `victim` at the FRONT so a naive tail-slice prune would drop it.
        reordered = {"victim": 3}
        reordered.update({k: v for k, v in cursors.items() if k != "victim"})
        out = _queue_advance_cursor(reordered, "victim", 11)
        self.assertIn("victim", out)
        self.assertEqual(out["victim"], 11)
        self.assertEqual(len(out), _PREFER_CURSOR_CAP)


if __name__ == "__main__":
    unittest.main()
