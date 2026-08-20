"""`minererrors`' own unit file — task 99 wave 3.

`agentware_lib.ops.sessionparse.minererrors` had no owning unit file. Its
behaviour was covered by `test_transcript_miner_reconcile.py`, which is named for
the MINER's behaviour and drives the whole reconstruction through
`_transcript_reconcile_errors` over a scratch HOME. That is the right test for
"does a mine see a failure the hook never wrote"; it is the wrong one for "what
does the reader do with a `content` that is a list of blocks", because a
whole-pass test can only observe the record that came out, and a wrong `content`
decoding produces a record that LOOKS right and carries a collapsed signature.

Wave 3 split `_transcript_reconcile_errors` (32 branches) into three phases with
no shared state — `_transcript_scan_tool_pairs` (+ `_transcript_collect_blocks`),
`_transcript_live_err_keys` and `_transcript_result_text` — leaving the pairing,
which is the only part that needs both halves at once, in the parent.

The properties that matter, and how each can be got wrong:

  SCAN/NONE   — an unreadable transcript returns None and an empty one returns
                `({}, [])`. Collapsing them would make "I could not read this
                session" indistinguishable from "this session had no failures",
                and the caller degrades differently for each.
  SCAN/ID     — pairing is by `tool_use_id`, never by position: a tool_use and
                its result sit in two different records with others between them.
                A non-string id is DROPPED rather than keyed on a placeholder, or
                two unrelated calls would pair with each other.
  SCAN/ERR    — only `is_error: true` results are collected. A successful call is
                not a failure the hook failed to record.
  TEXT/NEWLINE— a list-of-text-blocks `content` decodes to REAL newlines. This is
                the load-bearing one: `json.dumps` would escape a traceback's
                newlines to literal "\\n", the signature builder would see ONE
                line, and two genuinely-distinct deep failures would collapse
                onto the constant JSON preamble -> one permanent F-E ledger key
                that suppresses every later distinct failure forever.
  LIVE/TS     — the suppression key carries the timestamp, so a genuine SECOND
                failure of the same target is still reconstructed.
  PAIR/UNPAIRED — a result with no matching use has no tool and no target, so it
                is dropped rather than emitted as an untargeted ERR.

Hermetic: a tempdir standing in for HOME; no CLI, no network, stdlib unittest.
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

from agentware_lib.ops.sessionparse.minererrors import (  # noqa: E402
    _transcript_collect_blocks,
    _transcript_err_signature,
    _transcript_live_err_keys,
    _transcript_reconcile_errors,
    _transcript_result_text,
    _transcript_scan_tool_pairs,
)


def _use(tuid, name, inp, ts="2026-08-01T00:00:00Z"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"content": [{"type": "tool_use", "id": tuid,
                                     "name": name, "input": inp}]}}


def _result(tuid, is_err, content, ts="2026-08-01T00:00:01Z"):
    return {"type": "user", "timestamp": ts,
            "message": {"content": [{"type": "tool_result",
                                     "tool_use_id": tuid,
                                     "is_error": is_err,
                                     "content": content}]}}


class _TranscriptBase(unittest.TestCase):
    """Writes durable transcripts under a scratch HOME, so the reconstruction can
    never reach the operator's real ~/.claude/projects (R-LOC-03)."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="aw_errs_home_")
        self.addCleanup(shutil.rmtree, self.home, True)
        p = mock.patch.dict(os.environ, {"HOME": self.home})
        p.start()
        self.addCleanup(p.stop)
        self.pdir = os.path.join(self.home, ".claude", "projects", "-scratch")
        os.makedirs(self.pdir, exist_ok=True)

    def _transcript(self, sid, records):
        path = os.path.join(self.pdir, sid + ".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
        return path


class ScanToolPairsTest(_TranscriptBase):
    def test_an_unreadable_transcript_is_none_and_an_empty_one_is_a_pair(self):
        """SCAN/NONE: two different facts, and the caller degrades differently
        for each."""
        self.assertIsNone(_transcript_scan_tool_pairs(
            os.path.join(self.pdir, "no-such.jsonl")))
        self.assertIsNone(_transcript_scan_tool_pairs(self.pdir))
        self.assertEqual(_transcript_scan_tool_pairs(
            self._transcript("empty", [])), ({}, []))

    def test_it_pairs_by_id_across_intervening_records(self):
        """SCAN/ID: the use and its result are two records apart."""
        path = self._transcript("s", [
            _use("t1", "Bash", {"command": "make"}),
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "thinking about it"}]}},
            _result("t1", True, "boom"),
        ])
        uses, results = _transcript_scan_tool_pairs(path)
        self.assertEqual(uses, {"t1": ("Bash", {"command": "make"})})
        self.assertEqual([r[0] for r in results], ["t1"])

    def test_only_error_results_are_collected(self):
        """SCAN/ERR: this pass reconstructs the FAILURES the hook missed."""
        path = self._transcript("s", [
            _use("t1", "Bash", {}), _result("t1", False, "fine"),
            _use("t2", "Bash", {}), _result("t2", None, "unknown"),
            _use("t3", "Bash", {}), _result("t3", "true", "a string true"),
            _use("t4", "Bash", {}), _result("t4", True, "boom"),
        ])
        _uses, results = _transcript_scan_tool_pairs(path)
        self.assertEqual([r[0] for r in results], ["t4"])

    def test_a_non_string_or_empty_id_is_dropped_rather_than_keyed(self):
        """SCAN/ID: keying a placeholder would pair unrelated calls."""
        path = self._transcript("s", [
            _use(5, "Bash", {}), _use("", "Bash", {}), _use(None, "Bash", {}),
        ])
        uses, _results = _transcript_scan_tool_pairs(path)
        self.assertEqual(uses, {})

    def test_it_tolerates_every_shape_of_malformed_line(self):
        path = self._transcript("s", [
            "", "   ", "{broken", "3", "null", '"a string"',
            json.dumps({"message": "not a dict"}),
            json.dumps({"message": {"content": "not a list"}}),
            json.dumps({"message": {"content": [1, "x", None]}}),
            json.dumps({"no": "message"}),
        ])
        self.assertEqual(_transcript_scan_tool_pairs(path), ({}, []))

    def test_the_timestamp_travels_with_the_result_not_the_use(self):
        path = self._transcript("s", [
            _use("t1", "Bash", {}, ts="2026-08-01T00:00:00Z"),
            _result("t1", True, "boom", ts="2026-08-01T00:00:09Z"),
        ])
        _uses, results = _transcript_scan_tool_pairs(path)
        self.assertEqual(results[0][2], "2026-08-01T00:00:09Z")


class CollectBlocksTest(unittest.TestCase):
    def test_a_record_with_no_content_list_folds_nothing(self):
        uses, results = {}, []
        for rec in ({}, {"message": None}, {"message": "s"},
                    {"message": {"content": "s"}}, {"message": {}}):
            _transcript_collect_blocks(rec, uses, results)
        self.assertEqual((uses, results), ({}, []))

    def test_both_block_kinds_fold_from_one_record(self):
        uses, results = {}, []
        _transcript_collect_blocks({"timestamp": "T", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "N", "input": {"k": 1}},
            {"type": "tool_result", "tool_use_id": "a", "is_error": True,
             "content": "x"},
        ]}}, uses, results)
        self.assertEqual(uses, {"a": ("N", {"k": 1})})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][2], "T")


class LiveErrKeysTest(unittest.TestCase):
    def test_only_err_records_produce_a_key(self):
        keys = _transcript_live_err_keys([
            {"tool": "Edit", "status": "ok",
             "input": json.dumps({"file_path": "/a.py"})},
            "not a dict", None,
            {"tool": "Edit", "status": "ERR", "ts": "2026-08-01T00:00:01Z",
             "input": json.dumps({"file_path": "/a.py"})},
        ])
        self.assertEqual(len(keys), 1)

    def test_the_key_carries_the_timestamp(self):
        """LIVE/TS: a genuine SECOND failure of the same target must still be
        reconstructed, so only the same failure at the same second is a dup."""
        recs = [{"tool": "Edit", "status": "ERR", "ts": ts,
                 "input": json.dumps({"file_path": "/a.py"})}
                for ts in ("2026-08-01T00:00:01Z", "2026-08-01T00:00:09Z")]
        self.assertEqual(len(_transcript_live_err_keys(recs)), 2)


class ResultTextTest(unittest.TestCase):
    def test_a_list_of_text_blocks_joins_with_real_newlines(self):
        """TEXT/NEWLINE: the whole point. `json.dumps` would escape these to
        literal backslash-n and the signature builder would see one line."""
        text = _transcript_result_text([
            {"type": "text", "text": "Traceback (most recent call last):"},
            {"type": "text", "text": "ValueError: bad thing"},
        ])
        self.assertIn("\n", text)
        self.assertNotIn("\\n", text)
        self.assertEqual(text.splitlines(),
                         ["Traceback (most recent call last):",
                          "ValueError: bad thing"])

    def test_the_real_newlines_are_what_let_the_signature_find_the_exception(self):
        """TEXT/NEWLINE, stated as the CONSEQUENCE: with the escaped encoding the
        frame stripper sees one line, cannot drop the Traceback header, and every
        distinct deep failure derives the SAME permanent F-E ledger key."""
        blocks = [{"type": "text",
                   "text": 'Traceback (most recent call last):\n'
                           '  File "a.py", line 2, in <module>\n'
                           'ValueError: bad thing'}]
        good = _transcript_err_signature(
            {"response": _transcript_result_text(blocks)})
        collapsed = _transcript_err_signature(
            {"response": json.dumps(blocks, ensure_ascii=False)})
        self.assertIn("ValueError: bad thing", good)
        self.assertNotIn("Traceback", good)
        self.assertNotEqual(good, collapsed)

    def test_a_non_text_block_is_serialised_and_a_bare_string_is_kept(self):
        text = _transcript_result_text([
            {"type": "image", "source": "x"}, "a bare string", 7,
            {"type": "text", "text": 5},
        ])
        self.assertIn("a bare string", text)
        self.assertIn('"type": "image"', text)

    def test_a_plain_string_body_passes_through_unchanged(self):
        self.assertEqual(_transcript_result_text("Error: no such file"),
                         "Error: no such file")

    def test_a_dict_body_is_serialised_and_an_unserialisable_one_is_empty(self):
        self.assertEqual(_transcript_result_text({"detail": "denied"}),
                         '{"detail": "denied"}')
        self.assertEqual(_transcript_result_text(object()), "")
        self.assertEqual(_transcript_result_text(None), "null")


class ReconcileErrorsTest(_TranscriptBase):
    def test_an_absent_transcript_yields_no_records(self):
        self.assertEqual(_transcript_reconcile_errors("no-such-sid", []), [])

    def test_a_paired_failure_becomes_the_record_the_hook_would_have_written(self):
        self._transcript("s1", [_use("t1", "Bash", {"command": "make"}),
                                _result("t1", True, "error: one")])
        recs = _transcript_reconcile_errors("s1", [])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["tool"], "Bash")
        self.assertEqual(recs[0]["status"], "ERR")
        self.assertEqual(recs[0]["agent"], "main")
        self.assertIn("make", recs[0]["input"])
        self.assertIn("error: one", recs[0]["response"])

    def test_an_unpaired_result_is_dropped(self):
        """PAIR/UNPAIRED: with no use there is no tool and no target."""
        self._transcript("s1", [_result("orphan", True, "boom")])
        self.assertEqual(_transcript_reconcile_errors("s1", []), [])

    def test_two_failures_of_the_same_target_at_different_times_both_survive(self):
        self._transcript("s1", [
            _use("t1", "Bash", {"command": "make"}),
            _result("t1", True, "error: one", ts="2026-08-01T00:00:01Z"),
            _use("t2", "Bash", {"command": "make"}),
            _result("t2", True, "error: two", ts="2026-08-01T00:00:06Z"),
        ])
        self.assertEqual(len(_transcript_reconcile_errors("s1", [])), 2)

    def test_a_failure_the_writer_already_recorded_is_suppressed(self):
        self._transcript("s1", [_use("t1", "Edit", {"file_path": "/a/b.py"}),
                                _result("t1", True, "error: refused",
                                        ts="2026-08-01T00:00:01Z")])
        live = [{"ts": "2026-08-01T00:00:01Z", "tool": "Edit", "status": "ERR",
                 "input": json.dumps({"file_path": "/a/b.py"}),
                 "response": "error: refused"}]
        self.assertEqual(_transcript_reconcile_errors("s1", live), [])
        # NON-VACUITY: the same transcript with an EMPTY live log does emit, so
        # the assertion above measures the suppression rather than a dead path.
        self.assertEqual(len(_transcript_reconcile_errors("s1", [])), 1)

    def test_a_live_log_of_non_dicts_and_successes_suppresses_nothing(self):
        self._transcript("s1", [_use("t1", "Edit", {"file_path": "/a/b.py"}),
                                _result("t1", True, "error: refused")])
        live = ["not a dict", {"tool": "Edit", "status": "ok"}]
        self.assertEqual(len(_transcript_reconcile_errors("s1", live)), 1)

    def test_a_list_content_reaches_the_record_with_its_newlines_intact(self):
        """TEXT/NEWLINE at the boundary the miner actually uses."""
        self._transcript("s1", [
            _use("t1", "Bash", {"command": "make"}),
            _result("t1", True, [
                {"type": "text",
                 "text": "Traceback (most recent call last):\n"
                         "ValueError: bad thing"}]),
        ])
        recs = _transcript_reconcile_errors("s1", [])
        self.assertIn("\n", recs[0]["response"])
        self.assertIn("ValueError: bad thing",
                      _transcript_err_signature(recs[0]))


if __name__ == "__main__":
    unittest.main()
