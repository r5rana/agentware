"""PlanSizeDerivationTest, StageClassificationTest, ContextTaxDerivationTest, PhaseCostsAndOutcomeTest, LoopAnalyticsTest -- split from `test_metrics` (task 58).

See `tests.unit.ops._metrics_support` for the
shared header.
"""

from tests.unit.ops._metrics_support import (  # noqa: F401
    CLI, MetricsTestCase, _PLAN_FIXTURE, _assistant, _usage, _user, json,
    os)


class PlanSizeDerivationTest(MetricsTestCase):

    def _write_plan(self, feature, body):
        path = os.path.join(self.kdir, "work", feature, "plan.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def test_plan_size_fields_against_fixture(self):
        path = self._write_plan("260626-demo", _PLAN_FIXTURE)
        size = CLI.derive_plan_size(path)
        self.assertEqual(size["tasks"], 3)            # 1.1, 1.2, 2.1
        self.assertEqual(size["phases"], 2)           # majors 1 and 2
        self.assertEqual(size["accept"], 2)           # two `- [ ]` lines
        self.assertEqual(size["bytes"],
                         len(_PLAN_FIXTURE.encode("utf-8")))
        self.assertTrue(size["promise"])

    def test_flat_plan_reports_zero_phases(self):
        flat = ("## Tasks\n\n- ⬜ **1** A.\n- ⬜ **2** B.\n\n"
                "## Acceptance criteria\n\n- [ ] one.\n")
        path = self._write_plan("260626-flat", flat)
        size = CLI.derive_plan_size(path)
        self.assertEqual(size["tasks"], 2)
        self.assertEqual(size["phases"], 0)           # flat -> no phasing
        self.assertEqual(size["accept"], 1)
        self.assertFalse(size["promise"])

    def test_missing_plan_returns_none(self):
        self.assertIsNone(CLI.derive_plan_size(
            os.path.join(self.kdir, "work", "nope", "plan.md")))

    def test_metrics_json_emits_plan_block_for_feature(self):
        self._write_plan("260626-demo", _PLAN_FIXTURE)
        self._session("s-demo", [
            _user("2026-06-26T09:00:00.000Z",
                  text="work on work/260626-demo"),
            _assistant("2026-06-26T09:01:00.000Z", usage=_usage(i=5))])
        code, out, err = self.run_cli(
            ["metrics", "--feature", "260626-demo", "--format", "json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["plan"]["tasks"], 3)
        self.assertEqual(payload["plan"]["phases"], 2)

class StageClassificationTest(MetricsTestCase):

    def test_plan_vs_work_transcripts_classify(self):
        # Plan-authoring session: invokes the `plan new` CLI.
        self._session("s-plan", [
            _user("2026-06-26T08:00:00.000Z",
                  text="run plan new for the demo feature"),
            _assistant("2026-06-26T08:05:00.000Z", usage=_usage(i=10))])
        # Execution session: runs `plan set-status` and emits a promise.
        self._session("s-work", [
            _user("2026-06-26T09:00:00.000Z",
                  text="implement and plan set-status"),
            _assistant("2026-06-26T09:05:00.000Z", usage=_usage(i=20)),
            _user("2026-06-26T09:06:00.000Z",
                  text="<promise>X_COMPLETE</promise>")])
        rows = {r["session_id"]: r for r in self._agg()["sessions"]}
        self.assertEqual(rows["s-plan"]["stage"], "plan")
        self.assertEqual(rows["s-work"]["stage"], "work")

    def test_loop_stage_marker_wins(self):
        # Even with a work signal present, an explicit AGENTWARE_STAGE wins.
        self._session("s-loop", [
            _user("2026-06-26T10:00:00.000Z",
                  text="AGENTWARE_STAGE=loop-main running plan set-status"),
            _assistant("2026-06-26T10:05:00.000Z", usage=_usage(i=5))])
        rows = {r["session_id"]: r for r in self._agg()["sessions"]}
        self.assertEqual(rows["s-loop"]["stage"], "loop-main")

    def test_no_signals_is_unknown(self):
        self._session("s-quiet", [
            _assistant("2026-06-26T11:00:00.000Z", usage=_usage(i=1))])
        rows = {r["session_id"]: r for r in self._agg()["sessions"]}
        self.assertEqual(rows["s-quiet"]["stage"], "unknown")

    def test_authoring_attributes_plan_sessions_only(self):
        # Plan session: 300s wall, known tokens. Work session: excluded.
        self._session("s-plan", [
            _user("2026-06-26T08:00:00.000Z", text="plan new demo"),
            _assistant("2026-06-26T08:05:00.000Z", usage=_usage(i=10, o=5))])
        self._session("s-work", [
            _user("2026-06-26T09:00:00.000Z", text="plan set-status demo"),
            _assistant("2026-06-26T09:05:00.000Z", usage=_usage(i=99))])
        authoring = self._agg()["aggregate"]["authoring"]
        self.assertEqual(authoring["session_count"], 1)
        self.assertEqual(authoring["sessions"], ["s-plan"])
        self.assertEqual(authoring["wall_s"], 300.0)
        self.assertEqual(authoring["tokens"], 15)   # 10 + 5, work excluded

class ContextTaxDerivationTest(MetricsTestCase):
    """Task 4 — context-tax: cache-read/turn, injected MAIN.md footprint,
    context-window utilization % + truncation-risk (all derived, read-only)."""

    def _write_main_md(self, body):
        with open(os.path.join(self.kdir, "MAIN.md"), "w",
                  encoding="utf-8") as f:
            f.write(body)

    def test_cache_read_per_turn_and_injected_tokens_on_fixed_fixture(self):
        # MAIN.md of a KNOWN length: estimate_tokens = (len + 3) // 4.
        body = "x" * 400                       # (400 + 3)//4 = 100 tokens
        self._write_main_md(body)
        # Two assistant turns; cache_read totals 300 + 100 = 400 over 2 turns.
        self._session("s-ctx", [
            _assistant("2026-06-10T10:00:00.000Z", usage=_usage(i=10, cr=300)),
            _assistant("2026-06-10T10:00:05.000Z", usage=_usage(i=10, cr=100))])
        ct = self._agg()["aggregate"]["context_tax"]
        self.assertEqual(ct["injected_tokens"], 100)
        self.assertEqual(ct["main_md_bytes"], 400)
        # 400 cache_read / 2 turns = 200.0
        self.assertEqual(ct["cache_read_per_turn"], 200.0)

    def test_per_day_series_emitted(self):
        self._write_main_md("kb")
        self._session("s-d1", [
            _assistant("2026-06-01T10:00:00.000Z", usage=_usage(i=10, cr=100))])
        self._session("s-d2", [
            _assistant("2026-06-02T10:00:00.000Z", usage=_usage(i=10, cr=400)),
            _assistant("2026-06-02T10:00:05.000Z", usage=_usage(i=10, cr=0))])
        by_day = self._agg()["aggregate"]["context_tax"]["by_day"]
        self.assertEqual(set(by_day), {"2026-06-01", "2026-06-02"})
        self.assertEqual(by_day["2026-06-01"]["cache_read_per_turn"], 100.0)
        # day 2: 400 cache_read / 2 turns = 200.0
        self.assertEqual(by_day["2026-06-02"]["cache_read_per_turn"], 200.0)

    def test_context_window_utilization_and_truncation_risk(self):
        self._write_main_md("kb")
        # One turn whose input-side total = 500 + 200 + 250 = 950 tokens.
        self._session("s-fill", [
            _assistant("2026-06-10T10:00:00.000Z",
                       usage=_usage(i=500, cc=200, cr=250))])
        # window 1000 -> 950/1000 = 0.95 >= 0.9 default threshold -> risk.
        agg = self._agg(["--context-window", "1000"])["aggregate"]
        ct = agg["context_tax"]
        self.assertEqual(ct["context_window"], 1000)
        self.assertEqual(ct["peak_input_tokens"], 950)
        self.assertEqual(ct["context_window_pct"], 0.95)
        self.assertTrue(ct["truncation_risk"])
        row = self._agg(["--context-window", "1000"])["sessions"][0]
        self.assertEqual(row["context_window_pct"], 0.95)
        self.assertTrue(row["truncation_risk"])

    def test_no_truncation_risk_under_threshold(self):
        self._write_main_md("kb")
        self._session("s-ok", [
            _assistant("2026-06-10T10:00:00.000Z", usage=_usage(i=100, cr=50))])
        # 150 / 1000 = 0.15 -> well under threshold.
        ct = self._agg(["--context-window", "1000"])["aggregate"]["context_tax"]
        self.assertFalse(ct["truncation_risk"])
        self.assertEqual(ct["context_window_pct"], 0.15)

    def test_missing_main_md_injected_tokens_zero(self):
        # No MAIN.md written -> injected footprint is 0, never crashes.
        self._session("s-nomain", [
            _assistant("2026-06-10T10:00:00.000Z", usage=_usage(i=10, cr=10))])
        ct = self._agg()["aggregate"]["context_tax"]
        self.assertEqual(ct["injected_tokens"], 0)
        self.assertEqual(ct["main_md_bytes"], 0)
        self.assertEqual(ct["context_window"], 200000)  # default window

class PhaseCostsAndOutcomeTest(MetricsTestCase):
    """Task 5 — per-phase/per-iteration cost attribution + terminal run outcome
    (all derived read-only; no double counting; metrics.jsonl-authoritative)."""

    def _write_plan(self, feature, body):
        path = os.path.join(self.kdir, "work", feature, "plan.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def _write_metrics_jsonl(self, lines):
        path = os.path.join(self.kdir, "logs", "metrics.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for ln in lines:
                f.write(json.dumps(ln) + "\n")
        return path

    def test_per_phase_token_sums_equal_session_totals(self):
        # Three sessions across distinct loop phases (pre / main / post) via the
        # AGENTWARE_STAGE marker, so each lands in exactly one phase bucket.
        self._session("s-pre", [
            _user("2026-06-26T08:00:00.000Z", text="AGENTWARE_STAGE=loop-pre"),
            _assistant("2026-06-26T08:01:00.000Z", usage=_usage(i=10, o=2))])
        self._session("s-main", [
            _user("2026-06-26T09:00:00.000Z", text="AGENTWARE_STAGE=loop-main"),
            _assistant("2026-06-26T09:01:00.000Z", usage=_usage(i=100, o=20))])
        self._session("s-post", [
            _user("2026-06-26T10:00:00.000Z", text="AGENTWARE_STAGE=loop-post"),
            _assistant("2026-06-26T10:01:00.000Z", usage=_usage(i=5, o=1))])
        agg = self._agg()["aggregate"]
        pc = agg["phase_costs"]
        self.assertEqual(set(pc["by_phase"]), {"pre", "main", "post"})
        self.assertEqual(pc["by_phase"]["pre"]["total_tokens"], 12)
        self.assertEqual(pc["by_phase"]["main"]["total_tokens"], 120)
        self.assertEqual(pc["by_phase"]["post"]["total_tokens"], 6)
        # No double counting: per-phase sum == aggregate session total.
        phase_sum = sum(b["total_tokens"] for b in pc["by_phase"].values())
        self.assertEqual(phase_sum, agg["total_tokens"])
        self.assertEqual(pc["total_tokens"], agg["total_tokens"])
        # Per-phase cost sum == aggregate cost (no double counting).
        cost_sum = round(sum(b["cost_usd"] for b in pc["by_phase"].values()), 6)
        self.assertEqual(cost_sum, agg["cost_usd"])

    def test_outcome_completed_from_metrics_jsonl(self):
        self._write_metrics_jsonl([
            {"ts": "2026-06-26T09:00:00Z", "feature": "260626-demo",
             "iteration": 1, "phase": "main", "phase_wall_s": 12.0,
             "tasks_remaining": 1},
            {"ts": "2026-06-26T09:05:00Z", "feature": "260626-demo",
             "outcome": "completed", "iterations_used": 2,
             "self_heal_count": 0},
        ])
        self._session("s-x", [
            _user("2026-06-26T09:00:00.000Z", text="work/260626-demo"),
            _assistant("2026-06-26T09:01:00.000Z", usage=_usage(i=5))])
        agg = self._agg(["--feature", "260626-demo"])["aggregate"]
        self.assertEqual(agg["outcome"]["outcome"], "completed")
        self.assertEqual(agg["outcome"]["source"], "metrics.jsonl")
        self.assertEqual(agg["outcome"]["iterations_used"], 2)

    def test_outcome_each_terminal_state_from_metrics_jsonl(self):
        # The metrics.jsonl terminal event is authoritative for every state.
        for state in ("completed", "hit_max_iterations",
                      "post_hook_failure", "pre_hook_abort"):
            self._write_metrics_jsonl([
                {"ts": "2026-06-26T09:05:00Z", "feature": "260626-demo",
                 "outcome": state, "iterations_used": 3}])
            self._session("s-%s" % state, [
                _user("2026-06-26T09:00:00.000Z", text="work/260626-demo"),
                _assistant("2026-06-26T09:01:00.000Z", usage=_usage(i=1))])
            outcome = CLI.derive_outcome(self.kdir, "260626-demo")
            self.assertEqual(outcome["outcome"], state)
            self.assertEqual(outcome["source"], "metrics.jsonl")

    def test_outcome_last_terminal_event_wins(self):
        self._write_metrics_jsonl([
            {"feature": "260626-demo", "outcome": "hit_max_iterations"},
            {"feature": "260626-demo", "outcome": "completed"},
        ])
        outcome = CLI.derive_outcome(self.kdir, "260626-demo")
        self.assertEqual(outcome["outcome"], "completed")

    def test_outcome_completed_from_all_done_markers_artifact(self):
        # No metrics.jsonl: every task ✅ -> completed via the plan-marker fallback.
        self._write_plan("260626-done",
                         "## Tasks\n\n- ✅ **1** A.\n- ✅ **2** B.\n")
        outcome = CLI.derive_outcome(self.kdir, "260626-done")
        self.assertEqual(outcome["outcome"], "completed")
        self.assertEqual(outcome["source"], "plan-markers")

    def test_outcome_completed_from_loop_done_flag_artifact(self):
        loopdir = os.path.join(self.kdir, "work", "260626-flag", ".loop")
        os.makedirs(loopdir, exist_ok=True)
        with open(os.path.join(loopdir, ".done"), "w") as f:
            f.write("")
        outcome = CLI.derive_outcome(self.kdir, "260626-flag")
        self.assertEqual(outcome["outcome"], "completed")
        self.assertEqual(outcome["source"], "loop-done")

    def test_outcome_unknown_when_indeterminate_artifact(self):
        # Open markers remain, no .done, no metrics.jsonl -> never guess.
        self._write_plan("260626-wip",
                         "## Tasks\n\n- ✅ **1** A.\n- ⬜ **2** B.\n")
        outcome = CLI.derive_outcome(self.kdir, "260626-wip")
        self.assertEqual(outcome["outcome"], "unknown")
        self.assertEqual(outcome["source"], "artifacts")
        self.assertEqual(outcome["signals"]["open_markers"], 1)
        self.assertEqual(outcome["signals"]["done_markers"], 1)

    def test_iteration_series_from_metrics_jsonl(self):
        self._write_metrics_jsonl([
            {"feature": "260626-demo", "iteration": 1, "phase": "main",
             "phase_wall_s": 10.0, "tasks_remaining": 3, "tasks_done_delta": 1,
             "self_heal_count": 0},
            {"feature": "260626-demo", "iteration": 2, "phase": "main",
             "phase_wall_s": 20.0, "tasks_remaining": 2, "tasks_done_delta": 1,
             "self_heal_count": 1},
            {"feature": "OTHER", "iteration": 9, "phase": "main",
             "phase_wall_s": 99.0},
        ])
        self._session("s-i", [
            _user("2026-06-26T09:00:00.000Z", text="work/260626-demo"),
            _assistant("2026-06-26T09:01:00.000Z", usage=_usage(i=5))])
        its = self._agg(["--feature", "260626-demo"])["aggregate"]["iterations"]
        self.assertEqual([r["iteration"] for r in its], [1, 2])  # OTHER excluded
        self.assertEqual(its[0]["phase_wall_s"], 10.0)
        self.assertEqual(its[1]["tasks_remaining"], 2)
        self.assertEqual(its[1]["self_heal_count"], 1)

    def test_iterations_empty_without_metrics_jsonl(self):
        # No emission channel yet (Task 6/7) -> empty, never crashes.
        self._write_plan("260626-noemit", "## Tasks\n\n- ⬜ **1** A.\n")
        self._session("s-n", [
            _user("2026-06-26T09:00:00.000Z", text="work/260626-noemit"),
            _assistant("2026-06-26T09:01:00.000Z", usage=_usage(i=5))])
        agg = self._agg(["--feature", "260626-noemit"])["aggregate"]
        self.assertEqual(agg["iterations"], [])

    def test_phase_costs_present_without_feature_filter(self):
        self._session("s-quiet", [
            _assistant("2026-06-26T11:00:00.000Z", usage=_usage(i=7))])
        agg = self._agg()["aggregate"]
        self.assertIn("phase_costs", agg)
        # An unclassified session lands in the `unknown` phase bucket.
        self.assertEqual(agg["phase_costs"]["by_phase"]["unknown"]["total_tokens"], 7)
        # outcome/iterations are per-feature -> absent without a --feature filter.
        self.assertNotIn("outcome", agg)
        self.assertNotIn("iterations", agg)

class LoopAnalyticsTest(MetricsTestCase):
    """Task 28 — first-class loop analytics derived read-only from the
    metrics.jsonl emission channel (per-phase split, burndown, iteration
    efficiency, gate outcomes, max-iter utilization, throughput)."""

    def _write_metrics_jsonl(self, lines):
        path = os.path.join(self.kdir, "logs", "metrics.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for ln in lines:
                f.write(json.dumps(ln) + "\n")
        return path

    def _write_plan(self, feature, body):
        path = os.path.join(self.kdir, "work", feature, "plan.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def _seed_run(self, feature="260626-demo"):
        # A full 3-phase run: pre gate, two main iterations (burndown 3 -> 1),
        # post gate, terminal completed at iteration 2 of a max of 10.
        self._write_plan(feature, "## Tasks\n\n- ✅ **1** A.\n- ✅ **2** B.\n")
        self._write_metrics_jsonl([
            {"ts": "2026-06-26T08:00:00Z", "feature": feature, "phase": "pre",
             "iteration": 1, "max": 10, "tasks_remaining": 3,
             "promise_status": "signalled", "result": "complete",
             "phase_wall_s": 5.0},
            {"ts": "2026-06-26T08:01:00Z", "feature": feature, "phase": "main",
             "iteration": 1, "max": 10, "tasks_remaining": 3,
             "tasks_done_delta": 0, "phase_wall_s": 10.0, "self_heal_count": 0},
            {"ts": "2026-06-26T08:02:00Z", "feature": feature, "phase": "main",
             "iteration": 2, "max": 10, "tasks_remaining": 1,
             "tasks_done_delta": 2, "phase_wall_s": 20.0, "self_heal_count": 1},
            {"event": "task_transition", "ts": "2026-06-26T08:02:10Z",
             "feature": feature, "iteration": 2, "task": "1",
             "from": "open", "to": "done"},
            {"ts": "2026-06-26T08:03:00Z", "feature": feature, "phase": "post",
             "iteration": 1, "max": 10, "tasks_remaining": 0,
             "promise_status": "signalled", "result": "complete",
             "phase_wall_s": 4.0},
            {"event": "terminal", "ts": "2026-06-26T08:04:00Z",
             "feature": feature, "outcome": "completed", "iterations_used": 2,
             "max": 10, "self_heal_count": 1, "tasks_total": 3, "tasks_done": 3,
             "promise_status": "signalled"},
        ])

    def test_per_phase_split_burndown_efficiency_gates(self):
        self._seed_run()
        la = CLI.derive_loop_analytics(self.kdir, feature="260626-demo")
        # Per-phase WALL split: pre/main/post each attributed (main sums both its
        # iterations: 10 + 20).
        self.assertEqual(la["phase_wall_s"]["pre"], 5.0)
        self.assertEqual(la["phase_wall_s"]["main"], 30.0)
        self.assertEqual(la["phase_wall_s"]["post"], 4.0)
        # Burndown is the MAIN-loop curve only (pre/post share iteration ids but
        # must NOT bleed in): tasks_remaining 3 -> 1 across iterations 1, 2.
        self.assertEqual([(b["iteration"], b["tasks_remaining"])
                          for b in la["burndown"]], [(1, 3), (2, 1)])
        # Iteration efficiency = tasks closed (2) / iterations (2) = 1.0.
        self.assertEqual(la["iteration_efficiency"], 1.0)
        self.assertEqual(la["iterations_to_completion"], 2)
        # Max-iteration utilization = 2 / 10.
        self.assertEqual(la["max_iteration_utilization"], 0.2)
        self.assertEqual(la["self_heal_count"], 1)
        self.assertEqual(la["outcome"], "completed")
        # Pre + post hook gate outcomes are surfaced (both ok in this run).
        self.assertEqual(len(la["gates"]["pre"]), 1)
        self.assertEqual(len(la["gates"]["post"]), 1)
        self.assertTrue(la["gates"]["pre"][0]["ok"])
        self.assertTrue(la["gates"]["post"][0]["ok"])
        # promise/.done latency = first emission -> terminal (4 minutes).
        self.assertEqual(la["latency_s"], 240.0)

    def test_throughput_counts_completed_per_day_and_week(self):
        # Two completed features on distinct days within the same ISO week.
        self._write_metrics_jsonl([
            {"event": "terminal", "ts": "2026-06-22T08:00:00Z",
             "feature": "f-a", "outcome": "completed"},
            {"event": "terminal", "ts": "2026-06-23T08:00:00Z",
             "feature": "f-b", "outcome": "completed"},
            {"event": "terminal", "ts": "2026-06-23T09:00:00Z",
             "feature": "f-c", "outcome": "hit_max_iterations"},
        ])
        tp = CLI.derive_loop_throughput(self.kdir)
        self.assertEqual(tp["completed_total"], 2)  # the failed run excluded
        self.assertEqual(tp["by_day"]["2026-06-22"], 1)
        self.assertEqual(tp["by_day"]["2026-06-23"], 1)
        self.assertEqual(tp["by_week"]["2026-W26"], 2)

    def test_aggregate_lists_features_and_throughput(self):
        self._seed_run("260626-demo")
        out = CLI.derive_loop_analytics(self.kdir)
        feats = {f["feature"]: f for f in out["features"]}
        self.assertIn("260626-demo", feats)
        self.assertEqual(feats["260626-demo"]["outcome"], "completed")
        self.assertEqual(out["throughput"]["completed_total"], 1)

    def test_empty_channel_is_safe(self):
        # No emission yet -> burndown/gates empty, efficiency None, never crashes.
        self._write_plan("260626-noemit", "## Tasks\n\n- ⬜ **1** A.\n")
        la = CLI.derive_loop_analytics(self.kdir, feature="260626-noemit")
        self.assertEqual(la["burndown"], [])
        self.assertEqual(la["gates"], {"pre": [], "post": []})
        self.assertIsNone(la["iteration_efficiency"])
        self.assertEqual(la["self_heal_count"], 0)
