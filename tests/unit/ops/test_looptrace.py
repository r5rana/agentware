"""`looptrace`'s own unit file — task 99 wave 4, landed with the paydown.

`agentware_lib.ops.looptrace` derives the step-level run trace: what tools ran,
in what order, with what arguments and results, bucketed into loop iterations.
Until this file it had no owning test — the trace was exercised only through
`test_metrics_health.TraceTest`, which drives `derive_trace` end to end and so
observes the SUM of nine stages. A stage that silently returned nothing and a
stage that returned the right thing look the same there whenever another stage
happens to compensate.

The module's three widest bodies were split in this wave and the seams are the
properties below. Each names how it can be got wrong, because a test that only
records the current output is a change detector, not a contract.

  DECODE/SHARED  — the main.jsonl decode is ONE generator now. It was two
                   near-verbatim copies (token shares, step fallback) with the
                   same eight guards in the same order, and two copies of a
                   tolerant parser is how the two come to disagree about a
                   malformed line without anyone noticing.
  DECODE/ACCESS  — an absent or unopenable transcript yields nothing at all
                   rather than raising. The trace is rendered for a live run, so
                   a session directory being written into is the NORMAL case.
  SHARES/EVEN    — a turn's output_tokens are split EVENLY across the tool_use
                   items of that turn, and a `True` output_tokens is rejected
                   before `isinstance(x, int)` can accept it as 1.
  LIVE/PRIMARY   — live.jsonl wins when it has any rows; main.jsonl is the
                   FALLBACK, and a fallback step has no result and no status
                   because the transcript never recorded one.
  LIVE/ACCESS    — an OSError part-way through the live read discards the
                   PARTIAL result. A prefix would be indistinguishable from a
                   short session and would suppress the main.jsonl fallback.
  TOKENS/ALIGN   — a share is credited to a step only when index AND name agree.
                   Drift between the two recordings must yield None, not a
                   plausible number taken from the wrong call.
  DURATION/NEXT  — a step's duration is the NEXT step's ts minus its own, so the
                   last step has none; a NEGATIVE delta is discarded because two
                   unsynchronised clocks can order two steps backwards.
  GROUP/FEATURE  — a session-scoped trace recovers its grouping feature from the
                   session's own parsed row; that is the only way a single
                   session can be bucketed into iterations at all.
  GROUP/BUCKETS  — a group is created on FIRST reference by EITHER stream, so an
                   iteration with only a transition still appears; a transition
                   whose iteration is `True` is filed under the unknown bucket
                   rather than fabricating iteration 1.
  STEPS/COPY     — the cached step list is handed out as a DEEP COPY, because
                   `derive_trace` stamps `iteration`/`session_id` onto what it
                   returns and a shared list would leak one feature's grouping
                   into the next caller's.

Hermetic: tempdirs only; no CLI, no network, stdlib unittest only.
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

from agentware_lib.ops.looptrace import (  # noqa: E402
    _attach_step_durations,
    _attach_step_tokens,
    _collect_session_steps,
    _group_steps_by_iteration,
    _iter_main_assistant_tooluse,
    _iter_main_jsonl_objects,
    _iteration_for_ts,
    _main_tooluse_token_shares_uncached,
    _read_live_rows,
    _resolve_group_feature,
    _steps_from_live_rows,
    _steps_from_main_transcript,
    _trace_io,
    _trace_iteration_windows,
    _trace_session_dirs,
    _trace_steps_for_session,
    _trace_steps_for_session_uncached,
    derive_trace,
)


def _turn(ts, tools, out=None, model="m"):
    """One assistant turn emitting `tools` (a list of (name, input) pairs)."""
    msg = {"role": "assistant", "model": model,
           "content": [{"type": "tool_use", "name": n, "input": i}
                       for n, i in tools]}
    if out is not None:
        msg["usage"] = {"output_tokens": out}
    line = {"type": "assistant", "message": msg}
    if ts is not None:
        line["timestamp"] = ts
    return line


class _TraceBase(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_ltrace_")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def sdir(self, sid):
        d = os.path.join(self.kdir, "logs", "sessions", sid)
        os.makedirs(d, exist_ok=True)
        return d

    def write(self, sid, name, lines):
        path = os.path.join(self.sdir(sid), name)
        with open(path, "w", encoding="utf-8") as f:
            for ln in lines:
                f.write((ln if isinstance(ln, str) else json.dumps(ln)) + "\n")
        return path

    def metrics(self, lines):
        path = os.path.join(self.kdir, "logs", "metrics.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for ln in lines:
                f.write(json.dumps(ln) + "\n")
        return path


class TraceIoTest(unittest.TestCase):
    def test_json_is_reserialised_with_sorted_keys(self):
        """Two runs must render the same argument string, or the trace cannot be
        diffed against itself."""
        self.assertEqual(_trace_io('{"b": 2, "a": 1}')[0], '{"a": 1, "b": 2}')
        self.assertEqual(_trace_io({"b": 2, "a": 1})[0], '{"a": 1, "b": 2}')

    def test_unparseable_text_is_kept_verbatim(self):
        self.assertEqual(_trace_io("not json"), ("not json", False))

    def test_none_is_the_empty_string_and_not_truncated(self):
        self.assertEqual(_trace_io(None), ("", False))

    def test_over_length_is_truncated_and_says_so(self):
        text, cut = _trace_io("x" * 40, limit=10)
        self.assertTrue(cut)
        self.assertTrue(text.startswith("x" * 10))
        self.assertIn("truncated", text)


class MainJsonlDecodeTest(_TraceBase):
    def test_only_mappings_survive_the_line_walk(self):
        """DECODE/SHARED: blanks, broken JSON and non-mappings are all dropped by
        the ONE reader both callers now use."""
        p = self.write("s", "main.jsonl",
                       [{"a": 1}, "", "{ broken", json.dumps([1, 2]), {"b": 2}])
        self.assertEqual(list(_iter_main_jsonl_objects(p)), [{"a": 1}, {"b": 2}])

    def test_an_absent_or_unopenable_path_yields_nothing(self):
        """DECODE/ACCESS."""
        self.assertEqual(list(_iter_main_jsonl_objects(
            os.path.join(self.kdir, "nope.jsonl"))), [])
        self.assertEqual(list(_iter_main_jsonl_objects(self.sdir("s"))), [])

    def test_only_assistant_turns_carrying_tool_use_are_yielded(self):
        p = self.write("s", "main.jsonl", [
            {"type": "user", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {}}]}},
            _turn("t0", []),                       # assistant, no tool_use
            {"type": "assistant", "message": {"content": "a string"}},
            {"type": "assistant", "message": None},
            _turn("t1", [("Bash", {})]),
        ])
        got = list(_iter_main_assistant_tooluse(p))
        self.assertEqual([ts for ts, _m, _i in got], ["t1"])
        self.assertEqual([i[0]["name"] for _ts, _m, i in got], ["Bash"])

    def test_a_non_string_timestamp_is_normalised_to_none(self):
        p = self.write("s", "main.jsonl", [_turn(12345, [("Bash", {})])])
        self.assertEqual([ts for ts, _m, _i in
                          _iter_main_assistant_tooluse(p)], [None])

    def test_a_non_dict_content_block_is_not_a_tool_use(self):
        """The item filter is isinstance-guarded, so a bare string inside the
        content list cannot be read as a call."""
        p = self.write("s", "main.jsonl", [
            {"type": "assistant", "timestamp": "t",
             "message": {"content": ["just text",
                                     {"type": "text", "text": "hi"}]}}])
        self.assertEqual(list(_iter_main_assistant_tooluse(p)), [])


class TokenSharesTest(_TraceBase):
    def test_a_turns_output_is_split_evenly_across_its_tool_uses(self):
        """SHARES/EVEN."""
        p = self.write("s", "main.jsonl", [
            _turn("t0", [("Bash", {})], out=10),
            _turn("t1", [("Read", {}), ("Edit", {})], out=21),
        ])
        self.assertEqual(_main_tooluse_token_shares_uncached(p),
                         [("Bash", 10), ("Read", 10), ("Edit", 10)])

    def test_a_boolean_output_token_count_is_rejected(self):
        """SHARES/EVEN: `True` passes isinstance(x, int); reading it as 1 would
        credit a fabricated token to every step of that turn."""
        p = self.write("s", "main.jsonl", [_turn("t", [("Bash", {})])])
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "assistant", "timestamp": "t",
                                "message": {"usage": {"output_tokens": True},
                                            "content": [
                                                {"type": "tool_use",
                                                 "name": "Bash",
                                                 "input": {}}]}}) + "\n")
        self.assertEqual(_main_tooluse_token_shares_uncached(p), [("Bash", 0)])

    def test_a_nameless_tool_use_is_attributed_to_unknown(self):
        p = self.write("s", "main.jsonl", [
            {"type": "assistant", "timestamp": "t",
             "message": {"usage": {"output_tokens": 4},
                         "content": [{"type": "tool_use", "input": {}}]}}])
        self.assertEqual(_main_tooluse_token_shares_uncached(p),
                         [("<unknown>", 4)])

    def test_a_missing_usage_block_yields_a_zero_share(self):
        p = self.write("s", "main.jsonl", [_turn("t", [("Bash", {})])])
        self.assertEqual(_main_tooluse_token_shares_uncached(p), [("Bash", 0)])

    def test_an_absent_transcript_yields_no_shares(self):
        self.assertEqual(_main_tooluse_token_shares_uncached(
            os.path.join(self.kdir, "nope.jsonl")), [])


class LiveRowsTest(_TraceBase):
    def test_content_is_tolerated_and_non_mappings_dropped(self):
        p = self.write("s", "live.jsonl",
                       [{"tool": "Bash"}, "", "{ nope", json.dumps("str")])
        self.assertEqual(_read_live_rows(p), [{"tool": "Bash"}])

    def test_an_unreadable_stream_yields_an_empty_list(self):
        """LIVE/ACCESS: emptiness is what selects the main.jsonl fallback, so a
        partial read here would suppress the fallback with half a session."""
        self.assertEqual(_read_live_rows(self.sdir("s")), [])
        self.assertEqual(_read_live_rows(os.path.join(self.kdir, "no.jsonl")), [])

    def test_an_error_partway_through_discards_the_rows_already_read(self):
        """LIVE/ACCESS, the half the two cases above cannot reach: they fail at
        OPEN, where `rows` is empty and returning it is indistinguishable from
        discarding it. Only a mid-stream OSError separates the two, and it is the
        case the guard is for — a prefix of a per-action stream reads as a
        session that stopped early, and its non-emptiness suppresses the
        main.jsonl fallback that would have described the run properly."""
        class _HalfBrokenFile:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def __iter__(self_inner):
                yield json.dumps({"tool": "Bash"})
                raise OSError("input/output error")

        p = self.write("s", "live.jsonl", [{"tool": "Bash"}])
        with mock.patch("builtins.open",
                        return_value=_HalfBrokenFile()) as opened:
            self.assertEqual(_read_live_rows(p), [])
        self.assertTrue(opened.called)

    def test_a_row_missing_tool_or_status_gets_the_neutral_value(self):
        steps = _steps_from_live_rows([{"ts": "t", "input": None,
                                        "response": None}])
        self.assertEqual(steps[0]["tool"], "<unknown>")
        self.assertEqual(steps[0]["status"], "ok")

    def test_a_non_string_ts_becomes_none_rather_than_a_number(self):
        steps = _steps_from_live_rows([{"ts": 7, "tool": "Bash"}])
        self.assertIsNone(steps[0]["ts"])

    def test_input_and_response_are_both_rendered_and_flagged(self):
        steps = _steps_from_live_rows([
            {"ts": "t", "tool": "Bash", "status": "ERR",
             "input": json.dumps({"command": "ls"}),
             "response": "x" * 5000}])
        self.assertEqual(steps[0]["args"], '{"command": "ls"}')
        self.assertFalse(steps[0]["args_truncated"])
        self.assertTrue(steps[0]["result_truncated"])
        self.assertEqual(steps[0]["status"], "ERR")


class MainFallbackStepsTest(_TraceBase):
    def test_every_tool_use_of_a_turn_becomes_its_own_step(self):
        p = self.write("s", "main.jsonl",
                       [_turn("t0", [("Bash", {"c": 1}), ("Read", {"c": 2})])])
        steps = _steps_from_main_transcript(p)
        self.assertEqual([s["tool"] for s in steps], ["Bash", "Read"])
        self.assertEqual([s["ts"] for s in steps], ["t0", "t0"])

    def test_a_fallback_step_has_no_result_and_a_neutral_status(self):
        """LIVE/PRIMARY: the transcript records what was CALLED and nothing about
        how it went, so inventing a status here would report success it never
        observed."""
        p = self.write("s", "main.jsonl", [_turn("t0", [("Bash", {})])])
        step = _steps_from_main_transcript(p)[0]
        self.assertEqual(step["result"], "")
        self.assertFalse(step["result_truncated"])
        self.assertEqual(step["status"], "ok")


class StepTokenAlignmentTest(unittest.TestCase):
    def test_a_share_is_credited_only_when_index_and_name_agree(self):
        """TOKENS/ALIGN."""
        steps = [{"tool": "Bash"}, {"tool": "Read"}]
        _attach_step_tokens(steps, [("Bash", 5), ("Edit", 9)])
        self.assertEqual([s["tokens"] for s in steps], [5, None])

    def test_more_steps_than_shares_leaves_the_tail_unknown(self):
        steps = [{"tool": "Bash"}, {"tool": "Bash"}]
        _attach_step_tokens(steps, [("Bash", 5)])
        self.assertEqual([s["tokens"] for s in steps], [5, None])


class StepDurationTest(unittest.TestCase):
    def test_duration_is_the_next_steps_start_and_the_last_has_none(self):
        """DURATION/NEXT."""
        steps = [{"ts": "2026-07-01T09:00:00Z"},
                 {"ts": "2026-07-01T09:00:30Z"},
                 {"ts": "2026-07-01T09:01:00Z"}]
        _attach_step_durations(steps)
        self.assertEqual([s["duration_s"] for s in steps], [30.0, 30.0, None])

    def test_a_negative_delta_is_discarded_rather_than_reported(self):
        """DURATION/NEXT: unsynchronised clocks can order two steps backwards,
        and a negative duration is a measurement error, not a fast call."""
        steps = [{"ts": "2026-07-01T09:01:00Z"},
                 {"ts": "2026-07-01T09:00:00Z"}]
        _attach_step_durations(steps)
        self.assertIsNone(steps[0]["duration_s"])

    def test_an_unparseable_or_absent_stamp_yields_no_duration(self):
        steps = [{"ts": None}, {"ts": "not-a-date"}, {"ts": "x"}]
        _attach_step_durations(steps)
        self.assertEqual([s["duration_s"] for s in steps], [None, None, None])


class SessionStepsTest(_TraceBase):
    def test_live_is_primary_and_main_is_only_the_fallback(self):
        """LIVE/PRIMARY: the fixture puts DIFFERENT tools in the two sources, so
        a build that read main.jsonl while live.jsonl had rows is visible."""
        self.write("s", "live.jsonl", [{"ts": "t", "tool": "Bash"}])
        self.write("s", "main.jsonl", [_turn("t", [("Read", {})], out=4)])
        steps = _trace_steps_for_session_uncached(self.sdir("s"))
        self.assertEqual([x["tool"] for x in steps], ["Bash"])

    def test_with_no_live_stream_the_transcript_supplies_the_steps(self):
        self.write("s", "main.jsonl", [_turn("t", [("Read", {})], out=4)])
        steps = _trace_steps_for_session_uncached(self.sdir("s"))
        self.assertEqual([x["tool"] for x in steps], ["Read"])
        self.assertEqual(steps[0]["tokens"], 4)

    def test_a_session_with_neither_source_has_no_steps(self):
        self.assertEqual(_trace_steps_for_session_uncached(self.sdir("s")), [])

    def test_the_cached_wrapper_hands_out_a_deep_copy(self):
        """STEPS/COPY: `derive_trace` stamps iteration/session_id onto the steps
        it returns, so a shared canonical list would leak one feature's grouping
        into the next caller's."""
        self.write("s", "live.jsonl", [{"ts": "t", "tool": "Bash"}])
        first = _trace_steps_for_session(self.sdir("s"))
        first[0]["tool"] = "MUTATED"
        self.assertEqual(_trace_steps_for_session(self.sdir("s"))[0]["tool"],
                         "Bash")


class SessionDirsTest(_TraceBase):
    def test_a_named_session_is_returned_alone_and_a_missing_one_empty(self):
        self.sdir("a")
        self.assertEqual([s for s, _d in
                          _trace_session_dirs(self.kdir, session="a")], ["a"])
        self.assertEqual(_trace_session_dirs(self.kdir, session="nope"), [])

    def test_an_absent_logs_root_yields_nothing(self):
        self.assertEqual(_trace_session_dirs(
            os.path.join(self.kdir, "elsewhere")), [])

    def test_sessions_are_returned_in_sorted_id_order(self):
        for sid in ("c", "a", "b"):
            self.sdir(sid)
        self.assertEqual([s for s, _d in _trace_session_dirs(self.kdir)],
                         ["a", "b", "c"])


class IterationWindowTest(unittest.TestCase):
    def test_only_main_phase_events_open_a_window(self):
        wins = _trace_iteration_windows([
            {"ts": "t1", "phase": "main", "iteration": 1},
            {"ts": "t2", "stage": "loop-pre", "iteration": 9},
            {"ts": "t3", "phase": "post", "iteration": 9},
            {"event": "terminal", "ts": "t4", "phase": "main", "iteration": 9},
            {"event": "task_transition", "ts": "t5", "phase": "main",
             "iteration": 9},
            {"ts": "t6", "stage": "loop-main", "iteration": 2},
        ])
        self.assertEqual(wins, [("t1", 1), ("t6", 2)])

    def test_a_boolean_iteration_cannot_open_a_window(self):
        self.assertEqual(_trace_iteration_windows(
            [{"ts": "t", "phase": "main", "iteration": True}]), [])

    def test_windows_are_sorted_by_timestamp_not_arrival(self):
        wins = _trace_iteration_windows([
            {"ts": "t9", "phase": "main", "iteration": 2},
            {"ts": "t1", "phase": "main", "iteration": 1}])
        self.assertEqual(wins, [("t1", 1), ("t9", 2)])

    def test_a_step_takes_the_latest_window_at_or_before_it(self):
        wins = [("t1", 1), ("t5", 2)]
        self.assertIsNone(_iteration_for_ts(wins, "t0"))
        self.assertEqual(_iteration_for_ts(wins, "t1"), 1)
        self.assertEqual(_iteration_for_ts(wins, "t4"), 1)
        self.assertEqual(_iteration_for_ts(wins, "t9"), 2)

    def test_a_step_with_no_timestamp_has_no_iteration(self):
        self.assertIsNone(_iteration_for_ts([("t1", 1)], None))
        self.assertIsNone(_iteration_for_ts([("t1", 1)], 7))


class GroupFeatureTest(_TraceBase):
    def test_an_explicit_feature_wins_over_the_session_row(self):
        """GROUP/FEATURE."""
        self.assertEqual(_resolve_group_feature("F", "s", [("s", "/d")]), "F")

    def test_a_feature_scoped_call_never_reads_a_session(self):
        self.assertEqual(_resolve_group_feature(None, None, []), None)

    def test_a_session_scoped_call_recovers_the_feature_from_its_row(self):
        self.write("s", "main.jsonl", [
            {"type": "user", "timestamp": "2026-07-01T09:00:00Z",
             "message": {"role": "user",
                         "content": "work on work/260701-alpha"}},
            _turn("2026-07-01T09:01:00Z", [("Bash", {})], out=2)])
        self.assertEqual(
            _resolve_group_feature(None, "s", [("s", self.sdir("s"))]),
            "260701-alpha")

    def test_an_unparseable_session_leaves_the_feature_unresolved(self):
        self.assertIsNone(_resolve_group_feature(
            None, "s", [("s", self.sdir("s"))]))


class GroupingTest(unittest.TestCase):
    def test_a_transition_only_iteration_still_gets_a_group(self):
        """GROUP/BUCKETS."""
        groups = _group_steps_by_iteration(
            [], [{"iteration": 3, "task": "1", "ts": "t"}])
        self.assertEqual([g["iteration"] for g in groups], [3])
        self.assertEqual(groups[0]["step_count"], 0)
        self.assertEqual(groups[0]["transitions"][0]["task"], "1")

    def test_a_boolean_transition_iteration_lands_in_the_unknown_bucket(self):
        """GROUP/BUCKETS: `True` would otherwise fabricate iteration 1."""
        groups = _group_steps_by_iteration(
            [{"iteration": 1}], [{"iteration": True}])
        by_it = {g["iteration"]: g for g in groups}
        self.assertEqual(sorted(by_it, key=lambda k: (k is None, k)),
                         [1, None])
        self.assertEqual(by_it[1]["transitions"], [])
        self.assertEqual(len(by_it[None]["transitions"]), 1)

    def test_the_unknown_bucket_sorts_last(self):
        groups = _group_steps_by_iteration(
            [{"iteration": None}, {"iteration": 2}, {"iteration": 1}], [])
        self.assertEqual([g["iteration"] for g in groups], [1, 2, None])

    def test_only_the_five_transition_fields_are_republished(self):
        groups = _group_steps_by_iteration([], [{
            "iteration": 1, "ts": "t", "task": "1", "from": "a", "to": "b",
            "approx": True, "secret": "should not appear"}])
        self.assertEqual(set(groups[0]["transitions"][0]),
                         {"ts", "task", "from", "to", "approx"})


class CollectStepsTest(_TraceBase):
    def test_every_step_is_stamped_with_the_session_it_came_from(self):
        self.write("a", "live.jsonl", [{"ts": "t1", "tool": "Bash"}])
        self.write("b", "live.jsonl", [{"ts": "t2", "tool": "Read"}])
        steps, meta = _collect_session_steps(
            [("a", self.sdir("a")), ("b", self.sdir("b"))])
        self.assertEqual([s["session_id"] for s in steps], ["a", "b"])
        self.assertEqual(meta, [{"session_id": "a", "step_count": 1},
                                {"session_id": "b", "step_count": 1}])


class DeriveTraceTest(_TraceBase):
    def _seed(self):
        self.write("s1", "main.jsonl", [
            {"type": "user", "timestamp": "2026-07-01T09:00:00Z",
             "message": {"role": "user",
                         "content": "work on work/260701-alpha"}},
            _turn("2026-07-01T09:00:30Z", [("Bash", {})], out=10)])
        self.write("s1", "live.jsonl", [
            {"ts": "2026-07-01T09:00:30Z", "tool": "Bash", "status": "ok"},
            {"ts": "2026-07-01T09:02:30Z", "tool": "Read", "status": "ERR"}])
        self.metrics([
            {"ts": "2026-07-01T09:00:00Z", "feature": "260701-alpha",
             "phase": "main", "iteration": 1},
            {"ts": "2026-07-01T09:02:00Z", "feature": "260701-alpha",
             "phase": "main", "iteration": 2},
            {"event": "task_transition", "ts": "2026-07-01T09:02:40Z",
             "feature": "260701-alpha", "iteration": 2, "task": "1"}])

    def test_steps_are_bucketed_by_the_iteration_window_they_fall_in(self):
        self._seed()
        tr = derive_trace(self.kdir, feature="260701-alpha")
        self.assertEqual(tr["step_count"], 2)
        self.assertEqual(tr["err_count"], 1)
        self.assertEqual(tr["tool_summary"], {"Bash": 1, "Read": 1})
        self.assertEqual([(g["iteration"], g["step_count"])
                          for g in tr["iterations"]], [(1, 1), (2, 1)])
        self.assertEqual(len(tr["iterations"][1]["transitions"]), 1)

    def test_a_session_scope_reports_itself_and_recovers_its_feature(self):
        self._seed()
        tr = derive_trace(self.kdir, session="s1")
        self.assertEqual(tr["scope"], "session")
        self.assertEqual(tr["session"], "s1")
        self.assertEqual(tr["feature"], "260701-alpha")

    def test_truncation_is_reported_rather_than_silent(self):
        self._seed()
        tr = derive_trace(self.kdir, feature="260701-alpha", max_steps=1)
        self.assertTrue(tr["truncated"])
        self.assertEqual(tr["step_count"], 1)

    def test_an_empty_knowledge_dir_yields_an_empty_trace(self):
        tr = derive_trace(self.kdir, feature="nope")
        self.assertEqual(tr["step_count"], 0)
        self.assertEqual(tr["iterations"], [])
        self.assertFalse(tr["truncated"])

    def test_supplied_events_suppress_the_channel_read(self):
        """The bulk caller passes its own single-pass index; an implementation
        that re-read the channel anyway would ignore the empty list here."""
        self._seed()
        tr = derive_trace(self.kdir, feature="260701-alpha", events=[])
        self.assertEqual([g["iteration"] for g in tr["iterations"]], [None])


if __name__ == "__main__":
    unittest.main()
