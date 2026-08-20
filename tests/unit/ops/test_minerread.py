"""`minerread`'s own unit file — task 99 wave 3, landed with the module.

`agentware_lib.ops.sessionparse.minerread` is the half of `minerscan` that runs
OUTSIDE the queue lock: it reads the corpus, advances nothing, and returns one
window for the caller to fold. The split is the lock, and this file exists
because that half has its own failure domain — a source that cannot be read must
degrade WITHOUT moving its watermark, and nothing about the queue is involved.

Everything here was previously covered only through `_transcript_mine`, which
takes a lock, mutates three ledgers and returns an int. A read that silently
skipped bytes and a read that found nothing both show up there as 0.

The properties that matter, and how each can be got wrong:

  SINCE/NONE   — an unreadable source returns `(None, stored_offset)`, so the
                 watermark stays where it was and the NEXT window retries the
                 same bytes. Returning the advanced offset would skip a window
                 of the operator's prompts permanently and silently.
  SINCE/RESET  — an offset past EOF (log rotation or truncation) resets to 0
                 rather than reading nothing forever. Re-reading is safe because
                 every append downstream is fp-deduped; NOT re-reading is not.
  SINCE/FWD    — a normal read starts at the stored offset and returns
                 `start + len(read)`, so the watermark is what was ACTUALLY read,
                 not the file size at some other moment.
  PROMPTS/HUMAN— only `origin=human` turns are recurrence material. A loop- or
                 hook-authored turn is the machine talking to itself, and mining
                 it would propose the agent's own output back to the operator.
  SELECT/DISJOINT — incremental takes `on_disk - processed - pending`, backfill
                 takes `pending & on_disk`. The two modes must not overlap, or a
                 backfill drain would re-scan what incremental already settled.
  SELECT/CAP   — the slice is bounded, and the two levels mean DIFFERENT things
                 by a literal 0: `_mine_select_sessions` takes a resolved value,
                 so 0 selects nothing, while `_transcript_mine_read` resolves
                 `cap or MINER_SESSION_CAP`, so 0 is the default bound. Both are
                 pinned, because reading the second as the first is how a caller
                 passing a computed budget silently disables a detector.
  MERGE/ORDER  — a session's records are the hook's live log UNION the
                 reconstructed ERRs, stable-sorted by ts so a reconstruction
                 lands AFTER a same-second hook record (the conservative
                 tie-break: an ERR and its fix are never the same second).

Hermetic: tempdirs and a scratch HOME; no CLI, no network, stdlib unittest only.
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

from agentware_lib.ops.sessionparse.minerread import (  # noqa: E402
    _mine_read_metrics,
    _mine_read_prompts,
    _mine_read_since,
    _mine_select_sessions,
    _transcript_mine_read,
    _transcript_read_session,
)
from agentware_lib.ops.sessionparse.minerstate import (  # noqa: E402
    MINER_SESSION_CAP,
)


def _prompt(sid, origin, turn, body, cwd="/repo"):
    return ("[2026-08-20T00:00:00Z] [session %s] [cwd %s] [origin=%s] "
            "[turn=%s]\n%s\n\n" % (sid, cwd, origin, turn, body))


class _KbBase(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_mread_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.logs = os.path.join(self.kdir, "logs")
        os.makedirs(os.path.join(self.logs, "sessions"), exist_ok=True)
        # the reconstruction reads HOME; point it at an EMPTY scratch home so a
        # synthetic sid can never resolve to the operator's real transcripts.
        self.home = tempfile.mkdtemp(prefix="aw_mread_home_")
        self.addCleanup(shutil.rmtree, self.home, True)
        p = mock.patch.dict(os.environ, {"HOME": self.home})
        p.start()
        self.addCleanup(p.stop)

    def _write(self, rel, text):
        path = os.path.join(self.logs, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def _session(self, sid, calls, settled=True):
        d = os.path.join(self.logs, "sessions", sid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "live.jsonl"), "w", encoding="utf-8") as f:
            for c in calls:
                f.write(json.dumps(c) + "\n")
        if settled:
            with open(os.path.join(d, "main.jsonl"), "w", encoding="utf-8") as f:
                f.write('{"type":"user"}\n')


class MineReadSinceTest(_KbBase):
    def test_an_unreadable_source_keeps_its_stored_watermark(self):
        """SINCE/NONE: advancing here would skip a window of prompts forever."""
        missing = os.path.join(self.logs, "nope.log")
        self.assertEqual(_mine_read_since(missing, {"k": 42}, "k"), (None, 42))
        self.assertEqual(_mine_read_since(self.logs, {"k": 7}, "k"), (None, 7))

    def test_a_forward_read_returns_the_bytes_and_the_new_offset(self):
        """SINCE/FWD."""
        path = self._write("a.log", "hello world")
        self.assertEqual(_mine_read_since(path, {}, "k"), ("hello world", 11))
        self.assertEqual(_mine_read_since(path, {"k": 6}, "k"), ("world", 11))

    def test_an_offset_past_eof_resets_to_zero(self):
        """SINCE/RESET: a rotated or truncated log must not park the miner at an
        offset the file will never reach again."""
        path = self._write("a.log", "abc")
        self.assertEqual(_mine_read_since(path, {"k": 10 ** 9}, "k"), ("abc", 3))

    def test_a_negative_offset_resets_to_zero(self):
        path = self._write("a.log", "abc")
        self.assertEqual(_mine_read_since(path, {"k": -5}, "k"), ("abc", 3))

    def test_an_offset_exactly_at_eof_reads_nothing_and_stays(self):
        path = self._write("a.log", "abc")
        self.assertEqual(_mine_read_since(path, {"k": 3}, "k"), ("", 3))

    def test_a_missing_or_null_offset_reads_from_the_start(self):
        path = self._write("a.log", "abc")
        for offs in ({}, {"k": None}, {"k": 0}, {"k": "0"}):
            self.assertEqual(_mine_read_since(path, offs, "k"), ("abc", 3))

    def test_undecodable_bytes_are_replaced_rather_than_raising(self):
        path = os.path.join(self.logs, "b.log")
        with open(path, "wb") as f:
            f.write(b"ok \xff\xfe end")
        text, off = _mine_read_since(path, {}, "k")
        self.assertTrue(text.startswith("ok "))
        self.assertEqual(off, 9)


class MineReadPromptsTest(_KbBase):
    def test_only_human_turns_with_a_body_survive(self):
        """PROMPTS/HUMAN."""
        self._write("prompts.log",
                    _prompt("s1", "human", "t1", "always pin versions")
                    + _prompt("s1", "loop", "t2", "machine turn")
                    + _prompt("s1", "human", "t3", "   ")
                    + _prompt("s2", "human", "t1", "never edit by hand"))
        prompts, _off = _mine_read_prompts(self.logs, {})
        self.assertEqual([(p[0], p[1], p[2]) for p in prompts],
                         [("s1", "t1", "always pin versions"),
                          ("s2", "t1", "never edit by hand")])

    def test_the_cwd_travels_with_the_turn(self):
        self._write("prompts.log",
                    _prompt("s1", "human", "t1", "body", cwd="/checkout"))
        prompts, _off = _mine_read_prompts(self.logs, {})
        self.assertEqual(prompts[0][3], "/checkout")

    def test_an_absent_log_yields_nothing_and_keeps_the_watermark(self):
        self.assertEqual(_mine_read_prompts(self.logs, {"prompts": 99}),
                         ([], 99))

    def test_a_second_read_from_the_new_watermark_sees_only_new_turns(self):
        self._write("prompts.log", _prompt("s1", "human", "t1", "first"))
        first, off = _mine_read_prompts(self.logs, {})
        with open(os.path.join(self.logs, "prompts.log"), "a",
                  encoding="utf-8") as f:
            f.write(_prompt("s2", "human", "t1", "second"))
        second, off2 = _mine_read_prompts(self.logs, {"prompts": off})
        self.assertEqual([p[2] for p in first], ["first"])
        self.assertEqual([p[2] for p in second], ["second"])
        self.assertGreater(off2, off)


class MineReadMetricsTest(_KbBase):
    def test_it_keeps_objects_and_skips_everything_else(self):
        self._write("metrics.jsonl",
                    json.dumps({"event": "loop_end"}) + "\n"
                    + "{broken\n" + "[1,2]\n" + "3\n" + "\n"
                    + json.dumps({"event": "loop_start"}) + "\n")
        events, _off = _mine_read_metrics(self.logs, {})
        self.assertEqual(events, [{"event": "loop_end"},
                                  {"event": "loop_start"}])

    def test_an_absent_log_yields_nothing_and_keeps_the_watermark(self):
        self.assertEqual(_mine_read_metrics(self.logs, {"metrics": 12}),
                         ([], 12))


class SelectSessionsTest(_KbBase):
    def _state(self, processed=(), pending=()):
        return {"processed_sessions": list(processed),
                "backfill_pending": list(pending)}

    def test_the_two_modes_select_disjoint_sets(self):
        """SELECT/DISJOINT."""
        for sid in ("s1", "s2", "s3"):
            self._session(sid, [])
        state = self._state(processed=["s1"], pending=["s2"])
        inc = _mine_select_sessions(self.kdir, self.logs, state, False, 10)
        back = _mine_select_sessions(self.kdir, self.logs, state, True, 10)
        self.assertEqual([r[0] for r in inc], ["s3"])
        self.assertEqual([r[0] for r in back], ["s2"])

    def test_backfill_never_reaches_a_pending_sid_that_left_the_disk(self):
        self._session("s1", [])
        state = self._state(pending=["s1", "ghost"])
        back = _mine_select_sessions(self.kdir, self.logs, state, True, 10)
        self.assertEqual([r[0] for r in back], ["s1"])

    def test_the_selection_is_capped_and_a_cap_of_zero_selects_nothing(self):
        """SELECT/CAP."""
        for sid in ("s1", "s2", "s3"):
            self._session(sid, [])
        state = self._state()
        self.assertEqual(
            [r[0] for r in _mine_select_sessions(self.kdir, self.logs, state,
                                                 False, 2)], ["s1", "s2"])
        self.assertEqual(
            _mine_select_sessions(self.kdir, self.logs, state, False, 0), [])

    def test_each_row_carries_the_settled_flag(self):
        self._session("s1", [], settled=True)
        self._session("s2", [], settled=False)
        rows = _mine_select_sessions(self.kdir, self.logs, self._state(),
                                     False, 10)
        self.assertEqual([(r[0], r[2]) for r in rows],
                         [("s1", True), ("s2", False)])


class ReadSessionTest(_KbBase):
    def test_with_no_reconstruction_the_live_log_is_returned_unchanged(self):
        recs = [{"ts": "2026-08-01T00:00:01Z", "tool": "Edit", "status": "ok",
                 "input": "{}", "response": ""}]
        self._session("s1", recs)
        got = _transcript_read_session(os.path.join(self.logs, "sessions", "s1"),
                                       "s1")
        self.assertEqual([r["tool"] for r in got], ["Edit"])

    def test_reconstructed_errs_are_merged_and_sorted_by_timestamp(self):
        """MERGE/ORDER."""
        self._session("s1", [
            {"ts": "2026-08-01T00:00:05Z", "tool": "Edit", "status": "ok",
             "input": json.dumps({"file_path": "/a.py"}), "response": ""},
        ])
        pdir = os.path.join(self.home, ".claude", "projects", "-p")
        os.makedirs(pdir, exist_ok=True)
        with open(os.path.join(pdir, "s1.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "assistant", "timestamp": "2026-08-01T00:00:01Z",
                "message": {"content": [{"type": "tool_use", "id": "t1",
                                         "name": "Edit",
                                         "input": {"file_path": "/a.py"}}]}})
                    + "\n")
            f.write(json.dumps({
                "type": "user", "timestamp": "2026-08-01T00:00:02Z",
                "message": {"content": [{"type": "tool_result",
                                         "tool_use_id": "t1", "is_error": True,
                                         "content": "error: refused"}]}}) + "\n")
        got = _transcript_read_session(os.path.join(self.logs, "sessions", "s1"),
                                       "s1")
        self.assertEqual([r["status"] for r in got], ["ERR", "ok"])


class TranscriptMineReadTest(_KbBase):
    def test_it_composes_the_three_sources_into_one_window(self):
        self._write("prompts.log", _prompt("s1", "human", "t1", "a body"))
        self._write("metrics.jsonl", json.dumps({"event": "e"}) + "\n")
        self._session("s1", [])
        read = _transcript_mine_read(self.kdir, {"offsets": {}}, False, None)
        self.assertEqual([p[2] for p in read["prompts"]], ["a body"])
        self.assertEqual(read["metrics"], [{"event": "e"}])
        self.assertEqual([r[0] for r in read["scanned"]], ["s1"])
        self.assertEqual(read["mode"], "incremental")
        self.assertEqual(set(read["new_offsets"]), {"prompts", "metrics"})

    def test_the_mode_label_follows_the_backfill_flag(self):
        self.assertEqual(_transcript_mine_read(self.kdir, {}, True,
                                               None)["mode"], "backfill")

    def test_a_state_with_no_offsets_key_reads_from_the_start(self):
        self._write("prompts.log", _prompt("s1", "human", "t1", "a body"))
        for state in ({}, {"offsets": None}, {"offsets": {}}):
            read = _transcript_mine_read(self.kdir, state, False, None)
            self.assertEqual([p[2] for p in read["prompts"]], ["a body"])

    def test_a_falsy_cap_falls_back_to_the_module_bound(self):
        """SELECT/CAP at the composing pass: `session_cap or MINER_SESSION_CAP`,
        so BOTH None and a literal 0 mean "the default bound" here — a 0 that
        meant "scan nothing" would silently disable the error-recovery detector
        for any caller passing a computed remaining-budget of zero. The
        select-nothing behaviour lives one level down, where the value is already
        resolved (see SelectSessionsTest)."""
        for sid in ("s1", "s2"):
            self._session(sid, [])
        for cap in (None, 0, MINER_SESSION_CAP):
            read = _transcript_mine_read(self.kdir, {}, False, cap)
            self.assertEqual(len(read["scanned"]), 2, cap)

    def test_a_negative_cap_clamps_to_zero_and_selects_nothing(self):
        for sid in ("s1", "s2"):
            self._session(sid, [])
        read = _transcript_mine_read(self.kdir, {}, False, -1)
        self.assertEqual(read["scanned"], [])

    def test_one_dead_source_degrades_alone(self):
        """The three readers are independent: an absent prompts.log must not
        cost the caller its metrics or its sessions."""
        self._write("metrics.jsonl", json.dumps({"event": "e"}) + "\n")
        self._session("s1", [])
        read = _transcript_mine_read(self.kdir, {"offsets": {"prompts": 88}},
                                     False, None)
        self.assertEqual(read["prompts"], [])
        self.assertEqual(read["new_offsets"]["prompts"], 88)
        self.assertEqual(read["metrics"], [{"event": "e"}])
        self.assertEqual([r[0] for r in read["scanned"]], ["s1"])

    def test_it_advances_nothing_on_disk(self):
        """The whole point of running outside the lock: this pass is a pure read,
        so calling it twice returns the same window."""
        self._write("prompts.log", _prompt("s1", "human", "t1", "a body"))
        state = {"offsets": {"prompts": 0, "metrics": 0}}
        first = _transcript_mine_read(self.kdir, state, False, None)
        second = _transcript_mine_read(self.kdir, state, False, None)
        self.assertEqual(first["prompts"], second["prompts"])
        self.assertEqual(state["offsets"], {"prompts": 0, "metrics": 0})


if __name__ == "__main__":
    unittest.main()
