"""`derive`'s own unit file — task 99 wave 4, landed with the paydown.

`agentware_lib.ops.derive` is the `derive_*` reporting family: authoring signals,
the context tax, per-phase and per-iteration cost attribution, and the plan-file
derivations. Until this file it had no owning test — its members were reached
through `test_metrics*`, which drives the `metrics` CLI and reads a handful of
top-level numbers.

This wave carved the per-agent ACTIVITY rollup out of this module (see
`ops.agentactivity`), so what remains is exactly the family described above, and
this file asserts THAT surface. The properties that carry weight, and how each
can be got wrong:

  PHASE/PARTITION — the stage→phase map is a PARTITION: every session lands in
                    exactly one bucket, so the per-phase totals SUM to the
                    aggregate. A stage the map does not know must fall to
                    `unknown`, never be dropped and never be counted twice —
                    either would silently break that sum.
  TAX/PEAK        — window utilisation is measured against the PEAK single-turn
                    input, not the session total. Truncation is a per-turn event;
                    a session that never had a big turn cannot truncate however
                    much it spent in aggregate.
  TAX/ANNOTATE    — the derivation ANNOTATES each row in place (that is how the
                    CLI's per-session view gets its numbers) while remaining
                    additive: it must not disturb keys it did not compute.
  ITER/DISCRIMINATE — `task_transition` events also carry an integer iteration,
                    and terminal events carry none. Only the per-iteration events
                    fold, or a task's lifecycle becomes loop structure.
  ITER/BOOL       — `isinstance(True, int)` is True, so a boolean iteration must
                    be rejected explicitly or it fabricates iteration 1.
  MARKERS/OPEN    — ⬜ and 🟡 are BOTH open; only ✅ is done. Reading 🟡 as done
                    is how a half-finished plan reports complete.
  OUTCOME/LEDGER  — the terminal event wins verbatim, and only over the CLOSED
                    outcome vocabulary; the artifact heuristic asserts `completed`
                    alone and leaves everything else `unknown` rather than
                    guessing a failure mode the artifacts cannot distinguish.
  AGENTS/ONEREAD  — `derive_agents` reads the sessions ONCE and hands the same
                    rows to both slices; two reads would double the cost of the
                    dashboard's hottest endpoint.

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

from agentware_lib.ops.derive import (  # noqa: E402
    _STAGE_TO_PHASE,
    _TERMINAL_OUTCOMES,
    _cache_read_per_turn,
    _phase_for_stage,
    _plan_marker_status_counts,
    derive_agents,
    derive_authoring,
    derive_context_tax,
    derive_iteration_costs,
    derive_outcome,
    derive_phase_costs,
    derive_plan_authoring,
    derive_plan_size,
)


def _row(sid, stage="work", tokens=None, **over):
    tk = {"input_tokens": 60, "output_tokens": 40,
          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    tk.update(tokens or {})
    row = {"session_id": sid, "stage": stage, "turns": 2, "tool_calls": 1,
           "subagent_count": 0, "total_tokens": sum(tk.values()),
           "duration_seconds": 60.0, "date": "2026-07-01", "tokens": tk,
           "tools": {}, "models": {}, "cost_usd": 0.0,
           "peak_input_tokens": 60,
           "start": "2026-07-01T09:00:00Z", "end": "2026-07-01T09:01:00Z"}
    row.update(over)
    return row


PLAN_MD = """# Plan — Demo

## Tasks

- ✅ **1** Done.
  *Verify:* it works.
- 🟡 **2** In progress.
  *Verify:* it works.
- ⬜ **3** Open.
  *Verify:* it works.

## Acceptance criteria

- [ ] One.
- [ ] Two.

<promise>DEMO_COMPLETE</promise>
"""


class _KbBase(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_derive_")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def write(self, rel, text):
        path = os.path.join(self.kdir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def metrics(self, events):
        self.write(os.path.join("logs", "metrics.jsonl"),
                   "".join(json.dumps(e) + "\n" for e in events))


class AuthoringTest(unittest.TestCase):
    def test_only_plan_stage_sessions_are_attributed_to_authoring(self):
        out = derive_authoring([_row("p", stage="plan"),
                                _row("w", stage="work"),
                                _row("l", stage="loop-main")])
        self.assertEqual(out["session_count"], 1)
        self.assertEqual(out["sessions"], ["p"])
        self.assertEqual(out["wall_s"], 60.0)
        self.assertEqual(out["tokens"], 100)

    def test_no_plan_sessions_yields_a_zeroed_block_not_an_absence(self):
        self.assertEqual(derive_authoring([]),
                         {"wall_s": 0.0, "tokens": 0, "session_count": 0,
                          "sessions": []})


class PhaseCostsTest(unittest.TestCase):
    def test_every_declared_stage_has_its_own_bucket(self):
        for stage, phase in _STAGE_TO_PHASE.items():
            self.assertEqual(_phase_for_stage(stage), phase)

    def test_an_unmapped_or_absent_stage_falls_to_unknown(self):
        """PHASE/PARTITION: dropping it instead would break the sum below."""
        self.assertEqual(_phase_for_stage("brand-new-stage"), "unknown")
        self.assertEqual(_phase_for_stage(None), "unknown")
        self.assertEqual(_phase_for_stage(""), "unknown")

    def test_the_per_phase_totals_sum_to_the_aggregate(self):
        """PHASE/PARTITION: this is the property the map exists to guarantee."""
        rows = [_row("a", stage="loop-pre"), _row("b", stage="loop-main"),
                _row("c", stage="work"), _row("d", stage="nonsense")]
        out = derive_phase_costs(rows)
        self.assertEqual(sorted(out["by_phase"]),
                         ["main", "pre", "unknown", "work"])
        self.assertEqual(
            sum(rec["total_tokens"] for rec in out["by_phase"].values()),
            out["total_tokens"])

    def test_each_bucket_names_the_sessions_it_holds(self):
        out = derive_phase_costs([_row("a"), _row("b")])
        self.assertEqual(out["by_phase"]["work"]["sessions"], ["a", "b"])
        self.assertEqual(out["by_phase"]["work"]["session_count"], 2)

    def test_a_missing_cost_is_treated_as_zero_rather_than_raising(self):
        row = _row("a")
        del row["cost_usd"]
        self.assertEqual(derive_phase_costs([row])["cost_usd"], 0.0)

    def test_no_rows_yields_no_buckets(self):
        self.assertEqual(derive_phase_costs([]),
                         {"by_phase": {}, "total_tokens": 0, "cost_usd": 0.0})


class ContextTaxTest(_KbBase):
    def test_cache_read_per_turn_is_zero_when_there_are_no_turns(self):
        self.assertEqual(_cache_read_per_turn(500, 0), 0.0)
        self.assertEqual(_cache_read_per_turn(500, -1), 0.0)
        self.assertEqual(_cache_read_per_turn(500, 4), 125.0)

    def test_utilization_is_measured_against_the_peak_turn(self):
        """TAX/PEAK: two rows with the SAME total but different peaks must not
        report the same utilization."""
        flat = derive_context_tax([_row("a", peak_input_tokens=100)],
                                  self.kdir, window=1000)
        spiky = derive_context_tax([_row("a", peak_input_tokens=900)],
                                   self.kdir, window=1000)
        self.assertEqual(flat["context_window_pct"], 0.1)
        self.assertEqual(spiky["context_window_pct"], 0.9)

    def test_truncation_risk_fires_at_or_past_the_threshold(self):
        below = derive_context_tax([_row("a", peak_input_tokens=799)],
                                   self.kdir, window=1000, threshold=0.8)
        at = derive_context_tax([_row("a", peak_input_tokens=800)],
                                self.kdir, window=1000, threshold=0.8)
        self.assertFalse(below["truncation_risk"])
        self.assertTrue(at["truncation_risk"])

    def test_each_row_is_annotated_additively(self):
        """TAX/ANNOTATE: the CLI's per-session view reads these keys off the row,
        and the row's own fields must survive the pass."""
        row = _row("a", peak_input_tokens=500,
                   tokens={"cache_read_input_tokens": 200})
        derive_context_tax([row], self.kdir, window=1000)
        self.assertEqual(row["context_window_pct"], 0.5)
        self.assertEqual(row["cache_read_per_turn"], 100.0)
        self.assertFalse(row["truncation_risk"])
        self.assertEqual(row["session_id"], "a")
        self.assertEqual(row["stage"], "work")

    def test_the_injected_footprint_is_the_current_main_md(self):
        self.write("MAIN.md", "# hello\n" * 100)
        out = derive_context_tax([], self.kdir, window=1000)
        self.assertGreater(out["injected_tokens"], 0)
        self.assertEqual(out["main_md_bytes"], len(("# hello\n" * 100).encode()))

    def test_an_absent_main_md_is_a_zero_footprint_not_a_crash(self):
        out = derive_context_tax([], self.kdir, window=1000)
        self.assertEqual(out["injected_tokens"], 0)
        self.assertEqual(out["main_md_bytes"], 0)

    def test_the_day_series_carries_the_days_own_peak(self):
        rows = [_row("a", date="2026-07-01", peak_input_tokens=100),
                _row("b", date="2026-07-01", peak_input_tokens=700),
                _row("c", date="2026-07-02", peak_input_tokens=200)]
        by_day = derive_context_tax(rows, self.kdir, window=1000)["by_day"]
        self.assertEqual(by_day["2026-07-01"]["peak_input_tokens"], 700)
        self.assertEqual(by_day["2026-07-02"]["context_window_pct"], 0.2)


class IterationCostsTest(unittest.TestCase):
    def test_transition_and_terminal_events_do_not_fold(self):
        """ITER/DISCRIMINATE: a transition carries an integer iteration too."""
        out = derive_iteration_costs([
            {"event": "task_transition", "iteration": 1, "phase_wall_s": 99},
            {"event": "terminal", "iteration": 1, "phase_wall_s": 99},
            {"iteration": 1, "phase": "main", "phase_wall_s": 5.0}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["phase_wall_s"], 5.0)

    def test_a_boolean_iteration_is_rejected(self):
        """ITER/BOOL."""
        self.assertEqual(derive_iteration_costs([{"iteration": True}]), [])

    def test_wall_time_accumulates_per_phase_and_in_total(self):
        out = derive_iteration_costs([
            {"iteration": 1, "phase": "pre", "phase_wall_s": 2.0},
            {"iteration": 1, "phase": "main", "phase_wall_s": 3.0},
            {"iteration": 1, "phase": "main", "phase_wall_s": "1.5"}])
        self.assertEqual(out[0]["phases"], {"pre": 2.0, "main": 4.5})
        self.assertEqual(out[0]["phase_wall_s"], 6.5)

    def test_a_non_numeric_wall_time_contributes_zero(self):
        out = derive_iteration_costs([
            {"iteration": 1, "phase_wall_s": "nonsense"}])
        self.assertEqual(out[0]["phase_wall_s"], 0.0)

    def test_records_come_back_in_iteration_order_not_arrival_order(self):
        out = derive_iteration_costs([{"iteration": 3}, {"iteration": 1},
                                      {"iteration": 2}])
        self.assertEqual([r["iteration"] for r in out], [1, 2, 3])

    def test_an_absent_channel_yields_no_records(self):
        self.assertEqual(derive_iteration_costs([]), [])


class PlanDerivationTest(_KbBase):
    def test_open_covers_both_unstarted_and_in_progress_markers(self):
        """MARKERS/OPEN: reading 🟡 as done is how a half-finished plan reports
        complete, which is what the loop's terminal heuristic keys on."""
        path = self.write("work/260701-a/plan.md", PLAN_MD)
        self.assertEqual(_plan_marker_status_counts(path), (2, 1))

    def test_an_unreadable_plan_counts_nothing_rather_than_raising(self):
        self.assertEqual(_plan_marker_status_counts(
            os.path.join(self.kdir, "nope.md")), (0, 0))

    def test_plan_size_reports_tasks_criteria_and_the_promise(self):
        path = self.write("work/260701-a/plan.md", PLAN_MD)
        size = derive_plan_size(path)
        self.assertEqual(size["tasks"], 3)
        self.assertEqual(size["accept"], 2)
        self.assertTrue(size["promise"])
        self.assertEqual(size["bytes"], len(PLAN_MD.encode("utf-8")))

    def test_an_absent_plan_has_no_size_at_all(self):
        self.assertIsNone(derive_plan_size(
            os.path.join(self.kdir, "work", "nope", "plan.md")))

    def test_authored_plans_are_reported_with_their_marker_counts(self):
        self.write("work/260701-a/plan.md", PLAN_MD)
        out = derive_plan_authoring(
            self.kdir, [_row("p", stage="plan", plan_authored=["260701-a"])])
        self.assertEqual([p["feature"] for p in out], ["260701-a"])
        self.assertEqual(out[0]["tasks_total"], 3)
        self.assertEqual(out[0]["tasks_done"], 1)
        self.assertFalse(out[0]["complete"])


class OutcomeTest(_KbBase):
    def test_no_feature_in_scope_has_no_outcome(self):
        self.assertIsNone(derive_outcome(self.kdir, None))

    def test_the_terminal_event_is_used_verbatim(self):
        """OUTCOME/LEDGER."""
        self.metrics([{"event": "terminal", "feature": "260701-a",
                       "outcome": "hit_max_iterations", "iterations_used": 8}])
        out = derive_outcome(self.kdir, "260701-a")
        self.assertEqual(out["outcome"], "hit_max_iterations")
        self.assertEqual(out["source"], "metrics.jsonl")
        self.assertEqual(out["iterations_used"], 8)

    def test_the_last_terminal_wins_over_an_earlier_one(self):
        self.metrics([
            {"event": "terminal", "feature": "260701-a", "outcome": "stopped"},
            {"event": "terminal", "feature": "260701-a",
             "outcome": "completed"}])
        self.assertEqual(derive_outcome(self.kdir, "260701-a")["outcome"],
                         "completed")

    def test_an_outcome_outside_the_closed_vocabulary_is_not_trusted(self):
        """OUTCOME/LEDGER: the ledger is written by a shell script, so an
        unrecognised label falls back to the artifacts rather than being
        republished as though the loop had declared it."""
        self.metrics([{"event": "terminal", "feature": "260701-a",
                       "outcome": "made-up"}])
        out = derive_outcome(self.kdir, "260701-a")
        self.assertNotEqual(out["source"], "metrics.jsonl")

    def test_the_closed_vocabulary_includes_the_premise_halt(self):
        """R-GND-01's halt is a first-class terminal here; omitting it silently
        collapsed a premise-gate halt to `unknown`."""
        self.assertIn("premise_falsified", _TERMINAL_OUTCOMES)

    def test_a_loop_done_signal_asserts_completed_from_the_artifacts(self):
        self.write("work/260701-a/.loop/.done", "")
        out = derive_outcome(self.kdir, "260701-a")
        self.assertEqual(out["outcome"], "completed")
        self.assertEqual(out["source"], "loop-done")

    def test_all_markers_done_asserts_completed_from_the_plan(self):
        self.write("work/260701-a/plan.md",
                   "## Tasks\n\n- ✅ **1** A.\n- ✅ **2** B.\n")
        out = derive_outcome(self.kdir, "260701-a")
        self.assertEqual(out["outcome"], "completed")
        self.assertEqual(out["source"], "plan-markers")

    def test_an_unfinished_plan_stays_unknown_rather_than_guessing(self):
        """OUTCOME/LEDGER: the artifacts cannot distinguish stopped from
        in-progress, so naming either would be a guess (R-FAIL)."""
        self.write("work/260701-a/plan.md",
                   "## Tasks\n\n- ✅ **1** A.\n- ⬜ **2** B.\n")
        out = derive_outcome(self.kdir, "260701-a")
        self.assertEqual(out["outcome"], "unknown")
        self.assertEqual(out["source"], "artifacts")


class DeriveAgentsTest(_KbBase):
    def _session(self, sid, lines):
        self.write(os.path.join("logs", "sessions", sid, "main.jsonl"),
                   "".join(json.dumps(ln) + "\n" for ln in lines))

    def test_both_slices_come_back_and_the_plan_slice_carries_its_plans(self):
        """AGENTS/ONEREAD: one session read, two slices, and the plan slice is
        the only one carrying the authored-plan output."""
        self.write("work/260701-a/plan.md", PLAN_MD)
        self._session("s-plan", [
            {"type": "user", "timestamp": "2026-07-01T08:00:00Z",
             "message": {"role": "user", "content": "draft it"}},
            {"type": "assistant", "timestamp": "2026-07-01T08:05:00Z",
             "message": {"role": "assistant", "model": "m",
                         "usage": {"input_tokens": 10, "output_tokens": 5},
                         "content": [{"type": "tool_use", "name": "Write",
                                      "input": {"file_path":
                                                "work/260701-a/plan.md"}}]}}])
        out = derive_agents(self.kdir)
        self.assertEqual(sorted(out), ["plan", "work"])
        self.assertEqual(out["plan"]["kind"], "plan")
        self.assertEqual(out["work"]["kind"], "work")
        self.assertEqual([p["feature"] for p in out["plan"]["plans"]],
                         ["260701-a"])
        self.assertNotIn("plans", out["work"])

    def test_an_empty_knowledge_dir_still_returns_both_slices(self):
        out = derive_agents(self.kdir)
        self.assertEqual(out["plan"]["session_count"], 0)
        self.assertEqual(out["work"]["session_count"], 0)


if __name__ == "__main__":
    unittest.main()
