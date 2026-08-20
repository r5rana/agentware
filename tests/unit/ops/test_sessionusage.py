"""`sessionusage`'s own unit file — task 99 wave 3.

`agentware_lib.ops.sessionparse.sessionusage` had no owning unit file, and
`aggregate_jsonl` — 157 lines and 48 branches, the single widest body in the lib
— had NO direct test anywhere in the corpus. It was exercised only transitively,
through `parse_session` -> `collect_sessions` -> the `metrics` verb, which can
observe the SUM of its fourteen output keys and none of the decisions that
produced them. That is how a body this wide stayed unowned: every one of its
branches is reachable, and none of them was named.

Wave 3 split it into `_new_usage_agg` plus five folds (`_scan_line_markers`,
`_track_timestamps`, `_accumulate_turn_usage`, `_scan_message_content`,
`_scan_edit_target`) and two arithmetic helpers (`_usage_int`,
`_turn_input_total`). Each is asserted here on concrete values, because the
sharing is only safe if each half is pinned on its own.

The properties that matter, and how each can be got wrong:

  AGG/FRESH     — every aggregate is a FRESH object all the way down. A shared
                  nested `usage`/`models` dict would make two sessions' totals
                  contaminate each other, invisibly and only under a second call.
  MARKERS/RAW   — the four line markers are read PRE-parse, so a malformed line
                  still contributes them, and `loop_stage` is FIRST-WINS: a
                  session's stage is what the loop exported at spawn, and a later
                  mention of another stage must not relabel it.
  TS/WINDOW     — the window is ordered by the PARSED datetime and reported as
                  the RAW string. Ordering on the string is wrong across offsets;
                  reporting the datetime loses the transcript's own text.
  INT/BOOL      — `True` is an `int` in Python. A `"input_tokens": true` that
                  passed an isinstance check would add 1 to the operator's bill.
  USAGE/SUM     — the per-model split must SUM to the total, so a turn with no
                  usable `model` is booked to "unknown" rather than dropped.
  PEAK          — peak_input_tokens is the MAX single-turn input side, not the
                  last and not the total: it is the context-fill signal.
  EDIT/CLASS    — a Write of plan.md is AUTHORSHIP, an Edit of it is neither, and
                  any non-planning write is EXECUTION. Collapsing the first two
                  would relabel every status-marker edit as plan authoring.
  PROMISE       — the completion promise is read ONLY from the agent's own `text`
                  block, never from a `thinking` block or a tool result.
  ACTION/INPUT  — action attribution reads the tool INPUT only, which is what
                  makes it immune to the injected context that the passive
                  MENTION count deliberately picks up.

Hermetic: tempdirs and in-memory dicts; no CLI, no network, stdlib unittest only.
"""

from tests._paths import TESTS_DIR

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

from agentware_lib.ops.sessionparse.sessionusage import (  # noqa: E402
    _accumulate_turn_usage,
    _cache_read_ratio,
    _empty_usage,
    _new_usage_agg,
    _scan_edit_target,
    _scan_line_markers,
    _scan_message_content,
    _scan_tool_use,
    _track_timestamps,
    _turn_input_total,
    _usage_int,
    aggregate_jsonl,
)


class NewUsageAggTest(unittest.TestCase):
    def test_it_returns_a_fresh_object_all_the_way_down(self):
        """AGG/FRESH: two aggregates share no mutable sub-object, so one
        session's totals can never contaminate another's."""
        a, b = _new_usage_agg(), _new_usage_agg()
        a["usage"]["input_tokens"] += 5
        a["models"]["m"] = {}
        a["features"]["f"] = 1
        a["tools"]["Bash"] = 1
        self.assertEqual(b["usage"]["input_tokens"], 0)
        self.assertEqual(b["models"], {})
        self.assertEqual(b["features"], {})
        self.assertEqual(b["tools"], {})

    def test_it_starts_every_counter_at_a_falsy_zero_value(self):
        agg = _new_usage_agg()
        for k in ("turns", "peak_input_tokens", "code_edits", "stage_plan",
                  "stage_work", "tool_calls", "lines", "malformed"):
            self.assertEqual(agg[k], 0, k)
        for k in ("planner_spawned", "executor_spawned", "promise_emitted"):
            self.assertIs(agg[k], False, k)
        for k in ("loop_stage", "ts_first", "ts_last", "_first_dt", "_last_dt"):
            self.assertIsNone(agg[k], k)
        self.assertEqual(agg["usage"], _empty_usage())


class ScanLineMarkersTest(unittest.TestCase):
    def setUp(self):
        self.agg = _new_usage_agg()

    def test_it_counts_every_feature_mention_on_the_line(self):
        _scan_line_markers(self.agg, "work/260101-alpha and work/260101-alpha "
                                     "and work/260202-beta")
        self.assertEqual(self.agg["features"],
                         {"260101-alpha": 2, "260202-beta": 1})

    def test_the_loop_stage_is_first_wins(self):
        """MARKERS/RAW: the stage is what the loop exported at spawn; a later
        mention of another stage must not relabel the session."""
        _scan_line_markers(self.agg, "AGENTWARE_STAGE=loop-pre")
        _scan_line_markers(self.agg, "AGENTWARE_STAGE=loop-post")
        self.assertEqual(self.agg["loop_stage"], "loop-pre")

    def test_an_unknown_stage_value_sets_nothing(self):
        _scan_line_markers(self.agg, "AGENTWARE_STAGE=loop-sideways")
        self.assertIsNone(self.agg["loop_stage"])

    def test_a_line_counts_at_most_once_per_signal_class(self):
        _scan_line_markers(self.agg, "/agentware-plan then plan new then "
                                     "plan add-task")
        _scan_line_markers(self.agg, "plan set-status and worklog scan and "
                                     "</promise>")
        self.assertEqual(self.agg["stage_plan"], 1)
        self.assertEqual(self.agg["stage_work"], 1)

    def test_markers_are_read_off_a_line_that_is_not_json_at_all(self):
        """MARKERS/RAW: the markers are pre-parse on purpose — a truncated or
        malformed transcript line still carries the session's stage."""
        _scan_line_markers(self.agg, "{broken AGENTWARE_STAGE=loop-main "
                                     "work/260303-c")
        self.assertEqual(self.agg["loop_stage"], "loop-main")
        self.assertEqual(self.agg["features"], {"260303-c": 1})


class TrackTimestampsTest(unittest.TestCase):
    def setUp(self):
        self.agg = _new_usage_agg()

    def test_the_window_widens_in_both_directions_regardless_of_arrival(self):
        for ts in ("2026-08-02T10:00:00Z", "2026-08-01T10:00:00Z",
                   "2026-08-03T10:00:00Z", "2026-08-02T23:00:00Z"):
            _track_timestamps(self.agg, {"timestamp": ts})
        self.assertEqual(self.agg["ts_first"], "2026-08-01T10:00:00Z")
        self.assertEqual(self.agg["ts_last"], "2026-08-03T10:00:00Z")

    def test_it_reports_the_raw_string_and_orders_by_the_parsed_value(self):
        """TS/WINDOW: ordering happens on the datetime, reporting on the text."""
        _track_timestamps(self.agg, {"timestamp": "2026-08-02T10:00:00Z"})
        self.assertIsNotNone(self.agg["_first_dt"])
        self.assertNotEqual(self.agg["ts_first"], self.agg["_first_dt"])
        self.assertIsInstance(self.agg["ts_first"], str)

    def test_a_record_with_no_parseable_timestamp_widens_nothing(self):
        _track_timestamps(self.agg, {"timestamp": "2026-08-02T10:00:00Z"})
        for obj in ({}, {"timestamp": None}, {"timestamp": "not-a-date"},
                    {"timestamp": 17}):
            _track_timestamps(self.agg, obj)
        self.assertEqual(self.agg["ts_first"], "2026-08-02T10:00:00Z")
        self.assertEqual(self.agg["ts_last"], "2026-08-02T10:00:00Z")


class UsageIntTest(unittest.TestCase):
    def test_a_bool_is_refused_even_though_it_is_an_int(self):
        """INT/BOOL: `true` reaching the totals as 1 would bill the operator for
        a token that was never spent."""
        self.assertIsNone(_usage_int({"k": True}, "k"))
        self.assertIsNone(_usage_int({"k": False}, "k"))

    def test_a_float_is_truncated_and_a_zero_survives(self):
        self.assertEqual(_usage_int({"k": 2.9}, "k"), 2)
        self.assertEqual(_usage_int({"k": 0}, "k"), 0)

    def test_a_numeric_string_and_a_missing_key_are_both_none(self):
        self.assertIsNone(_usage_int({"k": "5"}, "k"))
        self.assertIsNone(_usage_int({}, "k"))
        self.assertIsNone(_usage_int({"k": None}, "k"))

    def test_the_turn_input_total_excludes_the_output_side(self):
        total = _turn_input_total({"input_tokens": 10,
                                   "cache_creation_input_tokens": 5,
                                   "cache_read_input_tokens": 100,
                                   "output_tokens": 9999})
        self.assertEqual(total, 115)


class AccumulateTurnUsageTest(unittest.TestCase):
    def setUp(self):
        self.agg = _new_usage_agg()

    def test_a_message_with_no_usage_dict_contributes_nothing(self):
        for msg in ({}, {"usage": None}, {"usage": [1, 2]}, {"usage": "x"}):
            _accumulate_turn_usage(self.agg, msg)
        self.assertEqual(self.agg["usage"], _empty_usage())
        self.assertEqual(self.agg["models"], {})

    def test_the_per_model_split_sums_to_the_total(self):
        """USAGE/SUM: a cost report that under-counts is worse than one that
        cannot attribute, so every unusable model label books to 'unknown'."""
        for model in ("m1", "", 7, None):
            _accumulate_turn_usage(self.agg, {"model": model,
                                              "usage": {"input_tokens": 10}})
        self.assertEqual(self.agg["usage"]["input_tokens"], 40)
        self.assertEqual(sum(m["input_tokens"]
                             for m in self.agg["models"].values()), 40)
        self.assertEqual(sorted(self.agg["models"]), ["m1", "unknown"])
        self.assertEqual(self.agg["models"]["unknown"]["input_tokens"], 30)

    def test_the_peak_is_the_max_single_turn_input_side_not_the_last(self):
        """PEAK: the context-fill signal is the widest single turn."""
        _accumulate_turn_usage(self.agg, {"model": "m",
                                          "usage": {"input_tokens": 100,
                                                    "cache_read_input_tokens": 50}})
        _accumulate_turn_usage(self.agg, {"model": "m",
                                          "usage": {"input_tokens": 1}})
        self.assertEqual(self.agg["peak_input_tokens"], 150)
        self.assertEqual(self.agg["usage"]["input_tokens"], 101)

    def test_output_tokens_never_reach_the_peak(self):
        _accumulate_turn_usage(self.agg, {"model": "m",
                                          "usage": {"input_tokens": 3,
                                                    "output_tokens": 9000}})
        self.assertEqual(self.agg["peak_input_tokens"], 3)


class ScanEditTargetTest(unittest.TestCase):
    def setUp(self):
        self.agg = _new_usage_agg()

    def test_a_full_write_of_a_plan_is_authorship_and_not_a_code_edit(self):
        _scan_edit_target(self.agg, "Write",
                          {"file_path": "/k/work/260101-a/plan.md"})
        self.assertEqual(self.agg["plan_authored"], {"260101-a": True})
        self.assertEqual(self.agg["code_edits"], 0)

    def test_an_edit_of_a_plan_is_neither_authorship_nor_execution(self):
        """EDIT/CLASS: status-marker maintenance is not plan authoring, and
        counting it as a code edit would relabel every planner as a worker."""
        _scan_edit_target(self.agg, "Edit",
                          {"file_path": "/k/work/260101-a/plan.md"})
        self.assertEqual(self.agg["plan_authored"], {})
        self.assertEqual(self.agg["code_edits"], 0)

    def test_a_write_of_a_design_doc_authors_no_plan(self):
        _scan_edit_target(self.agg, "Write",
                          {"file_path": "/k/work/260101-a/design.md"})
        self.assertEqual(self.agg["plan_authored"], {})
        self.assertEqual(self.agg["code_edits"], 0)

    def test_any_non_planning_write_family_call_is_execution(self):
        for name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            _scan_edit_target(self.agg, name, {"file_path": "/repo/a.py"})
        self.assertEqual(self.agg["code_edits"], 4)

    def test_a_read_that_names_a_file_classifies_as_nothing(self):
        _scan_edit_target(self.agg, "Read", {"file_path": "/repo/a.py"})
        _scan_edit_target(self.agg, "Bash", {"file_path": "/repo/a.py"})
        self.assertEqual(self.agg["code_edits"], 0)

    def test_a_non_dict_input_or_non_string_path_classifies_as_nothing(self):
        _scan_edit_target(self.agg, "Edit", "a string")
        _scan_edit_target(self.agg, "Edit", None)
        _scan_edit_target(self.agg, "Edit", {"file_path": 12})
        _scan_edit_target(self.agg, "Edit", {})
        self.assertEqual(self.agg["code_edits"], 0)


class ScanToolUseTest(unittest.TestCase):
    def setUp(self):
        self.agg = _new_usage_agg()

    def test_an_unnamed_tool_is_tallied_rather_than_dropped(self):
        _scan_tool_use(self.agg, {"type": "tool_use", "input": {}})
        self.assertEqual(self.agg["tools"], {"<unknown>": 1})
        self.assertEqual(self.agg["tool_calls"], 1)

    def test_action_features_come_from_the_tool_input_only(self):
        """ACTION/INPUT: this is what makes action attribution immune to the
        injected context the passive mention count deliberately picks up.

        The negative half is CONSTRUCTED, not implied: a feature named in a
        SIBLING field of the same block (harness blocks carry extra keys —
        cache_control, id, a citations envelope) must NOT be attributed, or the
        pillars would credit a session with work it never did. Asserting only
        the positive half passes with the whole item serialised, which is a
        mutation the sweep applies."""
        _scan_tool_use(self.agg, {"name": "Bash",
                                  "input": {"command": "ls work/260707-acted"},
                                  "cache_control": {"note": "work/260707-ghost"}})
        self.assertEqual(self.agg["action_features"], {"260707-acted": 1})
        self.assertEqual(self.agg["features"], {})

    def test_a_string_input_is_searched_without_being_json_encoded(self):
        _scan_tool_use(self.agg, {"name": "Bash",
                                  "input": "cd work/260808-str && ls"})
        self.assertEqual(self.agg["action_features"], {"260808-str": 1})

    def test_a_none_input_neither_raises_nor_attributes(self):
        _scan_tool_use(self.agg, {"name": "Bash", "input": None})
        self.assertEqual(self.agg["action_features"], {})
        self.assertEqual(self.agg["tool_calls"], 1)

    def test_only_the_two_agentware_subagent_types_set_a_flag(self):
        for sub in ("agentware-planner", "agentware-execution", "other", None):
            _scan_tool_use(self.agg, {"name": "Task",
                                      "input": {"subagent_type": sub}})
        self.assertIs(self.agg["planner_spawned"], True)
        self.assertIs(self.agg["executor_spawned"], True)
        self.assertEqual(self.agg["tools"], {"Task": 4})


class ScanMessageContentTest(unittest.TestCase):
    def setUp(self):
        self.agg = _new_usage_agg()

    def test_a_non_list_content_is_skipped(self):
        for content in ("hello", None, {"type": "text"}, 7):
            _scan_message_content(self.agg, content)
        self.assertEqual(self.agg["tool_calls"], 0)
        self.assertIs(self.agg["promise_emitted"], False)

    def test_the_promise_is_read_from_a_text_block(self):
        _scan_message_content(self.agg, [{"type": "text",
                                          "text": "done <promise>OK</promise>"}])
        self.assertIs(self.agg["promise_emitted"], True)

    def test_a_promise_inside_any_other_block_type_is_not_the_agents_own(self):
        """PROMISE: only the agent's own output block is authoritative — a
        thinking block is a draft and a tool_result is someone else's text."""
        _scan_message_content(self.agg, [
            {"type": "thinking", "text": "<promise>NO</promise>"},
            {"type": "tool_result", "text": "<promise>NO</promise>"},
        ])
        self.assertIs(self.agg["promise_emitted"], False)

    def test_a_non_string_text_field_neither_raises_nor_promises(self):
        _scan_message_content(self.agg, [{"type": "text", "text": 5}])
        self.assertIs(self.agg["promise_emitted"], False)

    def test_non_dict_items_are_skipped_and_the_rest_still_fold(self):
        _scan_message_content(self.agg, [
            "str", 3, None,
            {"type": "tool_use", "name": "Bash", "input": {}},
        ])
        self.assertEqual(self.agg["tool_calls"], 1)


class AggregateJsonlTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aw_usage_")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _write(self, lines, name="t.jsonl"):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        return path

    def test_a_missing_file_and_a_directory_both_yield_the_zero_aggregate(self):
        for path in (os.path.join(self.dir, "nope.jsonl"), self.dir):
            self.assertEqual(aggregate_jsonl(path), _new_usage_agg())

    def test_blank_lines_are_not_counted_and_malformed_ones_are(self):
        agg = aggregate_jsonl(self._write(["", "   ", "{broken", "3", "null",
                                           json.dumps({"type": "user"})]))
        self.assertEqual(agg["lines"], 4)
        self.assertEqual(agg["malformed"], 3)   # {broken, 3, null
        self.assertEqual(agg["turns"], 0)

    def test_a_malformed_line_still_contributes_its_raw_markers(self):
        agg = aggregate_jsonl(self._write(["{broken work/260909-x"]))
        self.assertEqual(agg["malformed"], 1)
        self.assertEqual(agg["features"], {"260909-x": 1})

    def test_only_assistant_lines_are_turns_and_carry_usage(self):
        agg = aggregate_jsonl(self._write([
            json.dumps({"type": "user",
                        "message": {"usage": {"input_tokens": 999}}}),
            json.dumps({"type": "assistant",
                        "message": {"model": "m",
                                    "usage": {"input_tokens": 4}}}),
        ]))
        self.assertEqual(agg["turns"], 1)
        self.assertEqual(agg["usage"]["input_tokens"], 4)

    def test_an_assistant_line_whose_message_is_not_a_dict_is_still_a_turn(self):
        agg = aggregate_jsonl(self._write([
            json.dumps({"type": "assistant", "message": "s"}),
            json.dumps({"type": "assistant"}),
        ]))
        self.assertEqual(agg["turns"], 2)
        self.assertEqual(agg["usage"], _empty_usage())

    def test_the_timestamp_window_covers_non_assistant_lines_too(self):
        agg = aggregate_jsonl(self._write([
            json.dumps({"type": "user", "timestamp": "2026-08-01T00:00:00Z"}),
            json.dumps({"type": "assistant", "timestamp": "2026-08-01T01:00:00Z",
                        "message": {}}),
        ]))
        self.assertEqual(agg["ts_first"], "2026-08-01T00:00:00Z")
        self.assertEqual(agg["ts_last"], "2026-08-01T01:00:00Z")

    def test_one_realistic_transcript_folds_every_dimension_at_once(self):
        agg = aggregate_jsonl(self._write([
            json.dumps({"type": "user", "timestamp": "2026-08-05T09:00:00Z",
                        "message": {"content": "see work/260505-real"}}),
            "{broken",
            json.dumps({"type": "assistant", "timestamp": "2026-08-05T09:01:00Z",
                        "message": {
                            "model": "claude-x",
                            "usage": {"input_tokens": 20, "output_tokens": 5,
                                      "cache_read_input_tokens": 80},
                            "content": [
                                {"type": "text", "text": "ok"},
                                {"type": "tool_use", "name": "Write",
                                 "input": {"file_path":
                                           "/k/work/260505-real/plan.md"}}]}}),
            json.dumps({"type": "assistant", "timestamp": "2026-08-05T09:02:00Z",
                        "message": {
                            "model": "claude-x",
                            "usage": {"input_tokens": 30, "output_tokens": 6},
                            "content": [
                                {"type": "text",
                                 "text": "<promise>TASK_COMPLETE</promise>"},
                                {"type": "tool_use", "name": "Edit",
                                 "input": {"file_path": "/repo/a.py"}}]}}),
        ]))
        self.assertEqual(agg["lines"], 4)
        self.assertEqual(agg["malformed"], 1)
        self.assertEqual(agg["turns"], 2)
        self.assertEqual(agg["usage"], {"input_tokens": 50, "output_tokens": 11,
                                        "cache_creation_input_tokens": 0,
                                        "cache_read_input_tokens": 80})
        self.assertEqual(agg["peak_input_tokens"], 100)
        self.assertEqual(agg["models"]["claude-x"]["input_tokens"], 50)
        self.assertEqual(agg["tools"], {"Write": 1, "Edit": 1})
        self.assertEqual(agg["tool_calls"], 2)
        self.assertEqual(agg["plan_authored"], {"260505-real": True})
        self.assertEqual(agg["code_edits"], 1)
        self.assertIs(agg["promise_emitted"], True)
        self.assertEqual(agg["features"]["260505-real"], 2)
        self.assertEqual(agg["action_features"], {"260505-real": 1})
        self.assertEqual(agg["ts_first"], "2026-08-05T09:00:00Z")
        self.assertEqual(agg["ts_last"], "2026-08-05T09:02:00Z")

    def test_two_aggregations_of_the_same_file_are_independent(self):
        """AGG/FRESH, at the boundary: the factory's freshness is only worth
        anything if `aggregate_jsonl` itself never returns a shared object."""
        path = self._write([json.dumps({"type": "assistant", "message": {
            "model": "m", "usage": {"input_tokens": 7}}})])
        first, second = aggregate_jsonl(path), aggregate_jsonl(path)
        self.assertEqual(first, second)
        first["usage"]["input_tokens"] += 100
        first["models"]["m"]["input_tokens"] += 100
        self.assertEqual(second["usage"]["input_tokens"], 7)
        self.assertEqual(second["models"]["m"]["input_tokens"], 7)


class CacheReadRatioTest(unittest.TestCase):
    def test_it_never_divides_by_zero(self):
        self.assertEqual(_cache_read_ratio({}), 0.0)
        self.assertEqual(_cache_read_ratio({"input_tokens": 0}), 0.0)
        self.assertEqual(_cache_read_ratio({"input_tokens": -5,
                                            "cache_read_input_tokens": 1}), 0.0)

    def test_it_divides_by_the_input_side_only(self):
        self.assertEqual(_cache_read_ratio({"input_tokens": 1,
                                            "cache_read_input_tokens": 3,
                                            "output_tokens": 96}), 0.75)


if __name__ == "__main__":
    unittest.main()
