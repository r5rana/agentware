"""`loopanalytics`'s own unit file — task 99 wave 4, landed with the paydown.

`agentware_lib.ops.loopanalytics` folds the loop emission channel
(`logs/metrics.jsonl`) into one run's shape: how many iterations it took, what it
closed each iteration, where its wall time went, whether its gates passed and how
long the whole thing ran. Until this file the module had no owning test — the
fold was reached only through `test_metrics_loop.LoopAnalyticsTest`, which
asserts a handful of top-level fields of `derive_loop_analytics`. A 43-branch
body observed through six of its own outputs is a body whose other decisions are
unguarded, and the ones that matter most are the ones that reject bad input.

The properties this file owns, and how each can be got wrong:

  PHASES/OVERLAP  — the three loop phases run their OWN iteration counters, so
                    pre 1 and main 1 are DIFFERENT iterations. The burndown folds
                    main-phase events only; folding all three merges a pre-hook
                    run into iteration 1 of the work.
  FOLD/UNTRUSTED  — every isinstance guard in the fold is a coercion of a line
                    written by a shell script: a non-numeric `phase_wall_s`
                    contributes zero rather than raising, and a `True` iteration
                    is rejected before it can masquerade as iteration 1.
  FOLD/LASTWINS   — `tasks_remaining` is a repeated OBSERVATION of one quantity
                    (last wins); `tasks_done_delta` is an INCREMENT (summed).
                    Swapping the two rules silently rewrites every burndown.
  METRICS/TERMINAL— the terminal event is authoritative for iterations, cap and
                    self-heal count, and the FOLD is the fallback. A run that was
                    killed has no terminal, and every number still has to be
                    answerable from the events that did land.
  METRICS/ZERO    — a non-positive `iterations_used` is rejected, because a zero
                    would turn the efficiency ratio into a division by zero
                    rather than an honest count.
  SELFHEAL/MAX    — recovered from the iterations as the MAX, not the sum: each
                    event reports the RUNNING TOTAL, so summing multiplies one
                    heal by the number of iterations that saw it. A terminal ZERO
                    still wins, which is why the fallback keys on `is None`.
  LATENCY/BOTH    — first event to terminal; both ends must exist and parse, or
                    the answer is None rather than a number from a guess.
  FEATURES/AUTO   — `work/auto/` is a CONTAINER of workorders, not a feature, so
                    discovery descends one level and namespaces the id. Without
                    the descent every workorder is invisible to the fold.

Hermetic: tempdirs only; no CLI, no network, stdlib unittest only.
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

from agentware_lib.ops.loopanalytics import (  # noqa: E402
    _fold_loop_events,
    _fold_main_event,
    _iteration_metrics,
    _loop_analytics_for_feature,
    _normalize_phase,
    _self_heal_count,
    _terminal_latency_s,
    derive_loop_analytics,
    derive_loop_throughput,
    list_loop_features,
)


class NormalizePhaseTest(unittest.TestCase):
    def test_the_loop_stage_tag_is_reduced_to_its_bare_phase(self):
        self.assertEqual(_normalize_phase("loop-pre"), "pre")
        self.assertEqual(_normalize_phase("loop-main"), "main")
        self.assertEqual(_normalize_phase("loop-post"), "post")

    def test_anything_unlabelled_defaults_to_main(self):
        """An event with no phase came from the work loop; defaulting elsewhere
        would move its wall time out of the phase that spent it."""
        self.assertEqual(_normalize_phase(None), "main")
        self.assertEqual(_normalize_phase(""), "main")

    def test_a_bare_phase_name_is_returned_untouched(self):
        self.assertEqual(_normalize_phase("post"), "post")


class FoldMainEventTest(unittest.TestCase):
    def setUp(self):
        self.by_it, self.order = {}, []

    def fold(self, e):
        _fold_main_event(e, self.by_it, self.order)

    def test_an_event_with_no_usable_iteration_is_dropped(self):
        """FOLD/UNTRUSTED: attributing work to the wrong iteration is worse than
        not counting it, and `True` would otherwise become iteration 1."""
        self.fold({"tasks_done_delta": 5})
        self.fold({"iteration": True, "tasks_done_delta": 5})
        self.fold({"iteration": "1", "tasks_done_delta": 5})
        self.assertEqual(self.by_it, {})
        self.assertEqual(self.order, [])

    def test_tasks_remaining_is_last_wins_and_delta_is_summed(self):
        """FOLD/LASTWINS."""
        self.fold({"iteration": 1, "tasks_remaining": 5, "tasks_done_delta": 1})
        self.fold({"iteration": 1, "tasks_remaining": 3, "tasks_done_delta": 2})
        self.assertEqual(self.by_it[1]["tasks_remaining"], 3)
        self.assertEqual(self.by_it[1]["tasks_done_delta"], 3)

    def test_an_explicit_none_remaining_does_not_erase_the_last_reading(self):
        self.fold({"iteration": 1, "tasks_remaining": 4})
        self.fold({"iteration": 1})
        self.assertEqual(self.by_it[1]["tasks_remaining"], 4)

    def test_a_boolean_delta_or_self_heal_is_rejected(self):
        self.fold({"iteration": 1, "tasks_done_delta": True,
                   "self_heal_count": True})
        self.assertEqual(self.by_it[1]["tasks_done_delta"], 0)
        self.assertNotIn("self_heal_count", self.by_it[1])

    def test_first_reference_order_is_recorded_separately_from_the_map(self):
        self.fold({"iteration": 3})
        self.fold({"iteration": 1})
        self.fold({"iteration": 3})
        self.assertEqual(self.order, [3, 1])


class FoldLoopEventsTest(unittest.TestCase):
    EVENTS = [
        {"ts": "t0", "stage": "loop-pre", "iteration": 1, "result": "complete",
         "phase_wall_s": 4.5},
        {"ts": "t1", "stage": "loop-pre", "iteration": 1,
         "promise_status": "signalled", "phase_wall_s": "2.5"},
        {"ts": "t2", "phase": "main", "iteration": 1, "tasks_remaining": 3,
         "tasks_done_delta": 1, "phase_wall_s": 60.0, "max": 8},
        {"ts": "t3", "phase": "main", "iteration": 2, "tasks_remaining": 2,
         "phase_wall_s": "nonsense", "max": 4},
        {"ts": "t4", "stage": "loop-post", "iteration": 1,
         "result": "incomplete", "phase_wall_s": 3.0},
        {"event": "terminal", "ts": "t5", "phase": "main", "phase_wall_s": 999},
        {"event": "task_transition", "ts": "t6", "phase": "main",
         "phase_wall_s": 999},
        # A phase OUTSIDE the closed {pre, main, post} set, carrying an
        # iteration. Nothing in the loop emits one today, but it is the only
        # input that separates "fold MAIN into the burndown" from "fold
        # everything the pre/post arm did not catch" -- with pre/main/post
        # alone the two are the same function.
        {"ts": "t7", "phase": "cleanup", "iteration": 9,
         "tasks_remaining": 77, "tasks_done_delta": 5, "phase_wall_s": 1.0},
    ]

    def test_only_main_phase_events_reach_the_burndown(self):
        """PHASES/OVERLAP: pre and main both carry iteration 1 here, so a fold
        that ignored the phase would merge the pre-hook into the work."""
        fold = _fold_loop_events(self.EVENTS)
        self.assertEqual([r["iteration"] for r in fold["iterations"]], [1, 2])
        self.assertEqual(fold["iterations"][0]["tasks_done_delta"], 1)
        # ... and iteration 9, which only the `cleanup` event carries, is absent
        # rather than folded in as a burndown point nobody emitted.
        self.assertNotIn(9, [r["iteration"] for r in fold["iterations"]])

    def test_terminal_and_transition_events_are_not_folded(self):
        """Their wall time is 999 each; either one leaking in is visible."""
        fold = _fold_loop_events(self.EVENTS)
        self.assertEqual(fold["phase_wall"],
                         {"pre": 7.0, "main": 60.0, "post": 3.0,
                          "cleanup": 1.0})

    def test_a_non_numeric_wall_time_contributes_zero_rather_than_raising(self):
        """FOLD/UNTRUSTED: `"2.5"` is coerced, `"nonsense"` is zero."""
        fold = _fold_loop_events(self.EVENTS)
        self.assertEqual(fold["phase_wall"]["main"], 60.0)

    def test_gate_outcomes_are_recorded_for_pre_and_post_only(self):
        gates = _fold_loop_events(self.EVENTS)["gates"]
        self.assertEqual(len(gates["pre"]), 2)
        self.assertEqual(len(gates["post"]), 1)
        self.assertEqual([g["ok"] for g in gates["pre"]], [True, True])
        self.assertEqual([g["ok"] for g in gates["post"]], [False])

    def test_the_iteration_cap_is_the_largest_seen_not_the_last(self):
        self.assertEqual(_fold_loop_events(self.EVENTS)["max_seen"], 8)

    def test_a_boolean_cap_is_rejected(self):
        self.assertIsNone(_fold_loop_events(
            [{"ts": "t", "phase": "main", "max": True}])["max_seen"])

    def test_only_string_timestamps_enter_the_latency_window(self):
        fold = _fold_loop_events([{"ts": 7, "phase": "main"},
                                  {"ts": "t1", "phase": "main"}])
        self.assertEqual(fold["iter_ts"], ["t1"])

    def test_an_empty_channel_folds_to_empty_accumulators(self):
        fold = _fold_loop_events([])
        self.assertEqual(fold["phase_wall"], {})
        self.assertEqual(fold["gates"], {"pre": [], "post": []})
        self.assertEqual(fold["iterations"], [])
        self.assertIsNone(fold["max_seen"])


class IterationMetricsTest(unittest.TestCase):
    ITERS = [{"iteration": 1, "tasks_done_delta": 2},
             {"iteration": 2, "tasks_done_delta": 1}]

    def test_the_terminal_count_wins_over_the_folded_one(self):
        """METRICS/TERMINAL."""
        m = _iteration_metrics(self.ITERS, {"iterations_used": 5, "max": 8}, 3)
        self.assertEqual(m["n_iters"], 5)
        self.assertEqual(m["max_iter"], 8)

    def test_without_a_terminal_the_fold_supplies_both(self):
        """METRICS/TERMINAL: a killed run has no terminal event."""
        m = _iteration_metrics(self.ITERS, {}, 6)
        self.assertEqual(m["n_iters"], 2)
        self.assertEqual(m["max_iter"], 6)

    def test_a_non_positive_or_boolean_terminal_count_is_rejected(self):
        """METRICS/ZERO: a zero here would divide by zero downstream."""
        for bad in (0, -3, True):
            self.assertEqual(
                _iteration_metrics(self.ITERS, {"iterations_used": bad}, None)
                ["n_iters"], 2)

    def test_efficiency_is_tasks_closed_per_iteration(self):
        m = _iteration_metrics(self.ITERS, {"iterations_used": 3}, None)
        self.assertEqual(m["tasks_closed"], 3)
        self.assertEqual(m["iteration_efficiency"], 1.0)

    def test_with_no_iterations_efficiency_is_unknown_not_zero(self):
        m = _iteration_metrics([], {}, None)
        self.assertIsNone(m["iteration_efficiency"])
        self.assertIsNone(m["max_iteration_utilization"])

    def test_utilization_is_iterations_over_the_cap(self):
        m = _iteration_metrics(self.ITERS, {"iterations_used": 4, "max": 8},
                               None)
        self.assertEqual(m["max_iteration_utilization"], 0.5)

    def test_a_zero_cap_yields_no_utilization_rather_than_a_division(self):
        m = _iteration_metrics(self.ITERS, {"max": 0}, 0)
        self.assertIsNone(m["max_iteration_utilization"])


class SelfHealTest(unittest.TestCase):
    def test_a_terminal_zero_wins_over_the_iterations(self):
        """SELFHEAL/MAX: the fallback keys on `is None`, so a terminal 0 is an
        answer and not an absence."""
        self.assertEqual(_self_heal_count(
            {"self_heal_count": 0}, [{"self_heal_count": 9}]), 0)

    def test_without_a_terminal_the_running_totals_are_maxed_not_summed(self):
        """SELFHEAL/MAX: summing would report one heal seen three times as 3."""
        self.assertEqual(_self_heal_count(
            {}, [{"self_heal_count": 1}, {"self_heal_count": 1},
                 {"self_heal_count": 1}]), 1)

    def test_a_boolean_observation_is_not_a_count(self):
        self.assertEqual(_self_heal_count({}, [{"self_heal_count": True}]), 0)

    def test_no_observations_at_all_is_zero(self):
        self.assertEqual(_self_heal_count({}, []), 0)


class TerminalLatencyTest(unittest.TestCase):
    def test_latency_spans_the_first_event_to_the_terminal(self):
        self.assertEqual(_terminal_latency_s(
            {"ts": "2026-07-01T09:10:00Z"},
            ["2026-07-01T09:05:00Z", "2026-07-01T09:00:00Z"]), 600.0)

    def test_a_run_with_no_terminal_has_no_latency(self):
        """LATENCY/BOTH, the terminal half — in BOTH spellings of "absent".

        `{}` alone does not exercise the guard: it has no `ts`, so the parse
        below would reject it anyway and the whole `not terminal` clause could be
        deleted with this assertion still passing (measured — task 99 wave 4's
        sweep reported the mutation GREEN). `None` is the other spelling of "this
        run has no terminal", and it is the one the guard is actually for: the
        `.get` under it raises on `None`, so without the clause an unfinished run
        crashes the analytics fold instead of reporting no latency."""
        self.assertIsNone(_terminal_latency_s({}, ["2026-07-01T09:00:00Z"]))
        self.assertIsNone(_terminal_latency_s(None, ["2026-07-01T09:00:00Z"]))

    def test_a_run_with_no_events_has_no_latency(self):
        """LATENCY/BOTH, the half that IS a decision: `min([])` raises, so
        dropping this guard turns an unfinished run into a crash."""
        self.assertIsNone(_terminal_latency_s({"ts": "2026-07-01T09:00:00Z"},
                                              []))

    def test_an_unparseable_stamp_yields_none_rather_than_a_guess(self):
        self.assertIsNone(_terminal_latency_s({"ts": "nope"},
                                              ["2026-07-01T09:00:00Z"]))
        self.assertIsNone(_terminal_latency_s({"ts": "2026-07-01T09:00:00Z"},
                                              ["nope"]))


class _KbBase(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_lanalytics_")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def feature_dir(self, feat, plan=True, loop=False):
        d = os.path.join(self.kdir, "work", feat)
        os.makedirs(d, exist_ok=True)
        if plan:
            with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as f:
                f.write("# plan\n")
        if loop:
            os.makedirs(os.path.join(d, ".loop"), exist_ok=True)
        return d

    def metrics(self, events):
        path = os.path.join(self.kdir, "logs", "metrics.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")


class ListFeaturesTest(_KbBase):
    def test_a_dir_with_neither_a_plan_nor_a_loop_is_not_a_feature(self):
        os.makedirs(os.path.join(self.kdir, "work", "notafeature"))
        self.feature_dir("260701-a")
        self.feature_dir("260702-b", plan=False, loop=True)
        self.assertEqual(list_loop_features(self.kdir),
                         ["260701-a", "260702-b"])

    def test_the_auto_lane_is_descended_and_namespaced(self):
        """FEATURES/AUTO: `work/auto/` holds workorders, so it is a container;
        listing it as one feature hides every workorder in it."""
        self.feature_dir(os.path.join("auto", "omega"))
        os.makedirs(os.path.join(self.kdir, "work", "auto", "empty"))
        self.assertEqual(list_loop_features(self.kdir), ["auto/omega"])

    def test_a_file_beside_the_auto_workorders_is_skipped(self):
        self.feature_dir(os.path.join("auto", "omega"))
        with open(os.path.join(self.kdir, "work", "auto", "README"), "w",
                  encoding="utf-8") as f:
            f.write("not a workorder\n")
        self.assertEqual(list_loop_features(self.kdir), ["auto/omega"])

    def test_an_absent_work_dir_yields_no_features(self):
        self.assertEqual(list_loop_features(
            os.path.join(self.kdir, "elsewhere")), [])


class ThroughputTest(_KbBase):
    def test_only_completed_terminals_count_toward_throughput(self):
        self.metrics([
            {"event": "terminal", "outcome": "completed",
             "ts": "2026-07-01T09:00:00Z"},
            {"event": "terminal", "outcome": "stopped",
             "ts": "2026-07-01T10:00:00Z"},
            {"event": "terminal", "outcome": "completed", "ts": 7},
            {"phase": "main", "ts": "2026-07-01T11:00:00Z"},
        ])
        out = derive_loop_throughput(self.kdir)
        self.assertEqual(out["by_day"], {"2026-07-01": 1})
        self.assertEqual(out["completed_total"], 1)
        self.assertEqual(list(out["by_week"]), ["2026-W27"])

    def test_an_absent_channel_yields_empty_maps(self):
        out = derive_loop_throughput(self.kdir)
        self.assertEqual(out, {"by_day": {}, "by_week": {},
                               "completed_total": 0})


class AnalyticsForFeatureTest(_KbBase):
    def _seed(self):
        self.feature_dir("260701-alpha")
        self.metrics([
            {"ts": "2026-07-01T09:00:00Z", "feature": "260701-alpha",
             "stage": "loop-pre", "iteration": 1, "result": "complete",
             "phase_wall_s": 5.0},
            {"ts": "2026-07-01T09:01:00Z", "feature": "260701-alpha",
             "phase": "main", "iteration": 1, "tasks_remaining": 2,
             "tasks_done_delta": 1, "phase_wall_s": 60.0, "max": 8},
            {"ts": "2026-07-01T09:02:00Z", "feature": "260701-alpha",
             "phase": "main", "iteration": 2, "tasks_remaining": 1,
             "tasks_done_delta": 1, "phase_wall_s": 30.0, "max": 8},
            {"event": "terminal", "ts": "2026-07-01T09:10:00Z",
             "feature": "260701-alpha", "outcome": "completed",
             "iterations_used": 2, "max": 8, "tasks_total": 2, "tasks_done": 2,
             "promise_status": "signalled"},
            {"ts": "2026-07-01T09:00:00Z", "feature": "260702-other",
             "phase": "main", "iteration": 1, "tasks_remaining": 99},
        ])

    def test_the_fold_is_scoped_to_the_feature_asked_for(self):
        self._seed()
        out = _loop_analytics_for_feature(self.kdir, "260701-alpha")
        self.assertEqual([b["tasks_remaining"] for b in out["burndown"]],
                         [2, 1])
        self.assertEqual(out["event_count"], 4)

    def test_the_reported_run_shape_comes_from_the_terminal(self):
        self._seed()
        out = _loop_analytics_for_feature(self.kdir, "260701-alpha")
        self.assertEqual(out["outcome"], "completed")
        self.assertEqual(out["iterations_to_completion"], 2)
        self.assertEqual(out["max_iterations"], 8)
        self.assertEqual(out["max_iteration_utilization"], 0.25)
        self.assertEqual(out["tasks_closed"], 2)
        self.assertEqual(out["promise_status"], "signalled")
        self.assertEqual(out["latency_s"], 600.0)

    def test_phase_wall_time_is_split_by_phase(self):
        self._seed()
        out = _loop_analytics_for_feature(self.kdir, "260701-alpha")
        self.assertEqual(out["phase_wall_s"], {"pre": 5.0, "main": 90.0})
        self.assertEqual([g["ok"] for g in out["gates"]["pre"]], [True])

    def test_a_feature_with_no_events_reports_safe_zero_values(self):
        out = _loop_analytics_for_feature(self.kdir, "260799-nothing")
        self.assertEqual(out["feature"], "260799-nothing")
        self.assertEqual(out["burndown"], [])
        self.assertEqual(out["tasks_closed"], 0)
        self.assertEqual(out["event_count"], 0)
        self.assertIsNone(out["latency_s"])
        self.assertIsNone(out["max_iteration_utilization"])

    def test_supplied_events_suppress_the_channel_read(self):
        self._seed()
        out = _loop_analytics_for_feature(self.kdir, "260701-alpha", events=[])
        self.assertEqual(out["event_count"], 0)
        self.assertEqual(out["burndown"], [])


class DeriveLoopAnalyticsTest(_KbBase):
    def test_one_feature_returns_that_runs_dict_directly(self):
        self.feature_dir("260701-alpha")
        self.metrics([{"ts": "2026-07-01T09:00:00Z", "feature": "260701-alpha",
                       "phase": "main", "iteration": 1}])
        out = derive_loop_analytics(self.kdir, feature="260701-alpha")
        self.assertEqual(out["feature"], "260701-alpha")

    def test_no_feature_returns_every_feature_plus_throughput(self):
        self.feature_dir("260701-alpha")
        self.feature_dir("260702-beta")
        self.metrics([{"event": "terminal", "outcome": "completed",
                       "ts": "2026-07-01T09:00:00Z",
                       "feature": "260701-alpha"}])
        out = derive_loop_analytics(self.kdir)
        self.assertEqual([f["feature"] for f in out["features"]],
                         ["260701-alpha", "260702-beta"])
        self.assertEqual(out["throughput"]["completed_total"], 1)


if __name__ == "__main__":
    unittest.main()
