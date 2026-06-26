"""Phase 4.1 — `metrics` transcript parser tests.

Builds synthetic session transcripts (logs/sessions/<sid>/main.jsonl plus
subagents/*.jsonl) with KNOWN token usage, timestamps and tool calls, then
asserts the parser yields exactly the expected totals, duration and counts.
Also covers filters (--session/--feature/--since), malformed-line tolerance,
determinism, and read-only behaviour (INV-2). Stdlib unittest only.
"""

import json
import os
import unittest

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # allow `python3 -m unittest tests.test_metrics`
    from _fixtures import SyntheticKBTestCase, load_cli


CLI = load_cli()


def _assistant(ts, usage=None, tools=None, thinking=True, model=None):
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": "..."})
    for name in (tools or []):
        content.append({"type": "tool_use", "name": name, "input": {}})
    msg = {"role": "assistant", "content": content}
    if model is not None:
        msg["model"] = model
    if usage is not None:
        msg["usage"] = usage
    line = {"type": "assistant", "message": msg}
    if ts is not None:
        line["timestamp"] = ts
    return line


def _user(ts, text="hi"):
    line = {"type": "user", "message": {"role": "user", "content": text}}
    if ts is not None:
        line["timestamp"] = ts
    return line


def _control(typ, ts=None):
    line = {"type": typ}
    if ts is not None:
        line["timestamp"] = ts
    return line


def _usage(i=0, o=0, cc=0, cr=0):
    return {
        "input_tokens": i,
        "output_tokens": o,
        "cache_creation_input_tokens": cc,
        "cache_read_input_tokens": cr,
    }


def _write_jsonl(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(json.dumps(ln) + "\n")


class MetricsTestCase(SyntheticKBTestCase):

    def _session(self, sid, main_lines, subagents=None):
        sdir = os.path.join(self.kdir, "logs", "sessions", sid)
        _write_jsonl(os.path.join(sdir, "main.jsonl"), main_lines)
        for name, lines in (subagents or {}).items():
            _write_jsonl(os.path.join(sdir, "subagents", name), lines)
        return sdir

    def _agg(self, argv_extra=None):
        code, out, err = self.run_cli(
            ["metrics", "--format", "json"] + (argv_extra or []))
        self.assertEqual(code, 0, err)
        return json.loads(out)


class TokenAndCountTest(MetricsTestCase):

    def test_known_totals_duration_and_tool_counts(self):
        # Two assistant turns with known usage + tools, spanning 90 seconds.
        self._session("sid-a", [
            _control("agent-setting"),                       # no timestamp
            _user("2026-06-24T10:00:00.000Z"),
            _assistant("2026-06-24T10:00:30.000Z",
                       usage=_usage(i=100, o=10, cc=5, cr=200),
                       tools=["Bash", "Read"]),
            _assistant("2026-06-24T10:01:30.000Z",
                       usage=_usage(i=50, o=20, cc=0, cr=300),
                       tools=["Bash"]),
        ])
        code, out, err = self.run_cli(["metrics", "--format", "json"])
        self.assertEqual(code, 0, err)
        row = json.loads(out)["sessions"][0]
        self.assertEqual(row["session_id"], "sid-a")
        self.assertEqual(row["turns"], 2)
        self.assertEqual(row["tokens"], _usage(i=150, o=30, cc=5, cr=500))
        self.assertEqual(row["total_tokens"], 150 + 30 + 5 + 500)
        self.assertEqual(row["duration_seconds"], 90.0)
        self.assertEqual(row["tools"], {"Bash": 2, "Read": 1})
        self.assertEqual(row["tool_calls"], 3)
        self.assertEqual(row["subagent_count"], 0)

    def test_subagents_fold_into_tokens_and_counts(self):
        self._session(
            "sid-b",
            [_assistant("2026-06-24T10:00:00.000Z",
                        usage=_usage(i=100, o=10), tools=["Read"])],
            subagents={
                "agent-1.jsonl": [
                    _assistant("2026-06-24T10:00:05.000Z",
                               usage=_usage(i=40, o=5), tools=["Grep"])],
                "agent-2.jsonl": [
                    _assistant("2026-06-24T10:00:06.000Z",
                               usage=_usage(i=10, o=2), tools=["Read"])],
            })
        code, out, err = self.run_cli(["metrics", "--format", "json"])
        self.assertEqual(code, 0, err)
        row = json.loads(out)["sessions"][0]
        self.assertEqual(row["subagent_count"], 2)
        # tokens = main + both subagents
        self.assertEqual(row["tokens"]["input_tokens"], 150)
        self.assertEqual(row["tokens"]["output_tokens"], 17)
        self.assertEqual(row["subagent_tokens"]["input_tokens"], 50)
        self.assertEqual(row["tools"], {"Read": 2, "Grep": 1})
        self.assertEqual(row["turns"], 3)            # 1 main + 2 subagent
        self.assertEqual(row["main_turns"], 1)
        self.assertEqual(row["subagent_turns"], 2)

    def test_aggregate_sums_across_sessions(self):
        self._session("s1", [
            _assistant("2026-06-24T10:00:00.000Z",
                       usage=_usage(i=100), tools=["Bash"]),
            _assistant("2026-06-24T10:00:10.000Z", usage=_usage(o=50)),
        ])
        self._session("s2", [
            _assistant("2026-06-25T10:00:00.000Z",
                       usage=_usage(i=25), tools=["Bash", "Edit"]),
        ])
        code, out, err = self.run_cli(["metrics", "--format", "json"])
        self.assertEqual(code, 0, err)
        agg = json.loads(out)["aggregate"]
        self.assertEqual(agg["session_count"], 2)
        self.assertEqual(agg["turns"], 3)
        self.assertEqual(agg["tokens"]["input_tokens"], 125)
        self.assertEqual(agg["tokens"]["output_tokens"], 50)
        self.assertEqual(agg["tools"], {"Bash": 2, "Edit": 1})


class ToleranceTest(MetricsTestCase):

    def test_malformed_and_typeless_lines_are_tolerated(self):
        sdir = os.path.join(self.kdir, "logs", "sessions", "sid-bad")
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "main.jsonl"), "w", encoding="utf-8") as f:
            f.write("{ this is not json\n")                  # malformed
            f.write("\n")                                      # blank
            f.write(json.dumps([1, 2, 3]) + "\n")              # not a dict
            f.write(json.dumps(_control("queue-operation")) + "\n")
            f.write(json.dumps(_assistant(
                "2026-06-24T10:00:00.000Z", usage=_usage(i=7))) + "\n")
        code, out, err = self.run_cli(["metrics", "--format", "json"])
        self.assertEqual(code, 0, err)
        row = json.loads(out)["sessions"][0]
        self.assertEqual(row["turns"], 1)
        self.assertEqual(row["tokens"]["input_tokens"], 7)
        self.assertEqual(row["malformed_lines"], 2)           # bad json + list

    def test_assistant_without_usage_does_not_crash(self):
        self._session("sid-nousage", [
            _assistant("2026-06-24T10:00:00.000Z", usage=None, tools=["Bash"]),
        ])
        code, out, err = self.run_cli(["metrics", "--format", "json"])
        self.assertEqual(code, 0, err)
        row = json.loads(out)["sessions"][0]
        self.assertEqual(row["total_tokens"], 0)
        self.assertEqual(row["tool_calls"], 1)

    def test_no_sessions_returns_nonzero(self):
        # No logs dir at all.
        code, out, err = self.run_cli(["metrics", "--format", "json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["sessions"], [])


class FilterTest(MetricsTestCase):

    def setUp(self):
        super().setUp()
        self._session("sid-old", [
            _assistant("2026-06-20T10:00:00.000Z", usage=_usage(i=1))])
        self._session("sid-new", [
            _user("2026-06-24T09:00:00.000Z", text="work on 260624-foo feature"),
            _assistant("2026-06-24T10:00:00.000Z", usage=_usage(i=2))])

    def test_session_filter(self):
        code, out, _ = self.run_cli(
            ["metrics", "--session", "sid-old", "--format", "json"])
        self.assertEqual(code, 0)
        rows = json.loads(out)["sessions"]
        self.assertEqual([r["session_id"] for r in rows], ["sid-old"])

    def test_since_filter(self):
        code, out, _ = self.run_cli(
            ["metrics", "--since", "2026-06-24", "--format", "json"])
        self.assertEqual(code, 0)
        rows = json.loads(out)["sessions"]
        self.assertEqual([r["session_id"] for r in rows], ["sid-new"])

    def test_since_rejects_bad_date(self):
        code, _out, err = self.run_cli(
            ["metrics", "--since", "june", "--format", "json"])
        self.assertEqual(code, 1)
        self.assertIn("YYYY-MM-DD", err)

    def test_feature_filter_matches_transcript_mention(self):
        code, out, _ = self.run_cli(
            ["metrics", "--feature", "260624-foo", "--format", "json"])
        self.assertEqual(code, 0)
        rows = json.loads(out)["sessions"]
        self.assertEqual([r["session_id"] for r in rows], ["sid-new"])

    def test_ordering_is_by_start_then_id(self):
        code, out, _ = self.run_cli(["metrics", "--format", "json"])
        rows = json.loads(out)["sessions"]
        self.assertEqual([r["session_id"] for r in rows],
                         ["sid-old", "sid-new"])


class DeterminismAndReadOnlyTest(MetricsTestCase):

    def _index_bytes(self):
        with open(os.path.join(self.kdir, "index.json"), "rb") as f:
            return f.read()

    def test_byte_identical_across_runs(self):
        self._session("sid-x", [
            _assistant("2026-06-24T10:00:00.000Z",
                       usage=_usage(i=3, o=4), tools=["Bash"])])
        a = self.run_cli(["metrics", "--format", "json"])[1]
        b = self.run_cli(["metrics", "--format", "json"])[1]
        self.assertEqual(a, b)

    def test_metrics_does_not_mutate_index(self):
        self._session("sid-y", [
            _assistant("2026-06-24T10:00:00.000Z", usage=_usage(i=1))])
        before = self._index_bytes()
        self.run_cli(["metrics", "--format", "json"])
        self.run_cli(["metrics", "--format", "text"])
        self.assertEqual(before, self._index_bytes())

    def test_text_output_renders(self):
        self._session("sid-z", [
            _assistant("2026-06-24T10:00:00.000Z",
                       usage=_usage(i=10, o=5), tools=["Bash"])])
        code, out, err = self.run_cli(["metrics"])
        self.assertEqual(code, 0, err)
        self.assertIn("sid-z", out)
        self.assertIn("TOTAL", out)


class DerivedCostTest(MetricsTestCase):
    """Task 2 — derived PURE cost / cache-ratio / per-model / rollup fields."""

    def _agg(self, argv_extra=None):
        code, out, err = self.run_cli(
            ["metrics", "--format", "json"] + (argv_extra or []))
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_cost_and_cache_ratio_math_on_fixed_fixture(self):
        # opus default price: in=15, out=75, cache_read=1.5 ($ / 1M tokens).
        self._session("s-opus", [
            _assistant("2026-06-10T10:00:00.000Z",
                       model="claude-opus-4-20250514",
                       usage=_usage(i=100, o=10, cc=0, cr=300))])
        row = self._agg()["sessions"][0]
        # (100*15 + 10*75 + 300*1.5) / 1e6 = 2700 / 1e6
        self.assertEqual(row["cost_usd"], 0.0027)
        # cache_read / (input + cache_creation + cache_read) = 300/400
        self.assertEqual(row["cache_read_ratio"], 0.75)
        self.assertIn("claude-opus-4-20250514", row["models"])
        self.assertEqual(
            row["models"]["claude-opus-4-20250514"]["cost_usd"], 0.0027)

    def test_per_model_attribution_sums_to_total(self):
        self._session("s-multi", [
            _assistant("2026-06-10T10:00:00.000Z",
                       model="claude-opus-4-20250514", usage=_usage(i=100)),
            _assistant("2026-06-10T10:00:05.000Z",
                       model="claude-sonnet-4-20250514", usage=_usage(i=200))])
        row = self._agg()["sessions"][0]
        models = row["models"]
        self.assertEqual(set(models), {
            "claude-opus-4-20250514", "claude-sonnet-4-20250514"})
        # per-model input tokens sum to the session total (no double counting)
        summed = sum(m["tokens"]["input_tokens"] for m in models.values())
        self.assertEqual(summed, row["tokens"]["input_tokens"])
        self.assertEqual(summed, 300)
        # opus 100*15/1e6 + sonnet 200*3/1e6 = 0.0015 + 0.0006
        self.assertEqual(row["cost_usd"], 0.0021)

    def test_per_day_and_feature_rollups(self):
        self._session("s1", [
            _user("2026-06-01T09:00:00.000Z", text="work/260601-alpha kickoff"),
            _assistant("2026-06-01T10:00:00.000Z",
                       model="claude-sonnet-4", usage=_usage(o=100))])
        self._session("s2", [
            _user("2026-06-02T09:00:00.000Z", text="work/260601-alpha cont"),
            _assistant("2026-06-02T10:00:00.000Z",
                       model="claude-sonnet-4", usage=_usage(o=100))])
        agg = self._agg()["aggregate"]
        self.assertEqual(set(agg["by_day"]), {"2026-06-01", "2026-06-02"})
        self.assertEqual(agg["by_day"]["2026-06-01"]["session_count"], 1)
        self.assertIn("260601-alpha", agg["by_feature"])
        self.assertEqual(agg["by_feature"]["260601-alpha"]["session_count"], 2)
        self.assertIn("claude-sonnet-4", agg["by_model"])
        # sonnet output 100*15/1e6 = 0.0015 per session, 0.003 total
        self.assertEqual(agg["cost_usd"], 0.003)

    def test_cost_anomaly_flag_on_day_spike(self):
        for day, out_tokens in (("01", 100), ("02", 100), ("03", 100),
                                ("04", 500)):
            self._session("s-%s" % day, [
                _assistant("2026-06-%sT10:00:00.000Z" % day,
                           model="claude-sonnet-4", usage=_usage(o=out_tokens))])
        agg = self._agg()["aggregate"]
        self.assertEqual(agg["cost_anomaly_dates"], ["2026-06-04"])
        self.assertTrue(agg["by_day"]["2026-06-04"]["cost_anomaly"])
        self.assertFalse(agg["by_day"]["2026-06-01"]["cost_anomaly"])

    def test_custom_price_table_overrides_default(self):
        self._session("s-px", [
            _assistant("2026-06-10T10:00:00.000Z",
                       model="claude-sonnet-4", usage=_usage(i=1_000_000))])
        price_path = os.path.join(self.kdir, "prices.json")
        with open(price_path, "w", encoding="utf-8") as f:
            json.dump({"claude-sonnet-4": {
                "input": 9.0, "output": 0.0,
                "cache_creation_input_tokens": 0.0,
                "cache_read_input_tokens": 0.0}}, f)
        row = self._agg(["--prices", price_path])["sessions"][0]
        self.assertEqual(row["cost_usd"], 9.0)  # 1e6 * 9.0 / 1e6

    def test_unknown_model_priced_under_default(self):
        # no model field -> attributed to "unknown", priced via the default entry
        self._session("s-unk", [
            _assistant("2026-06-10T10:00:00.000Z", usage=_usage(o=1_000_000))])
        row = self._agg()["sessions"][0]
        self.assertIn("unknown", row["models"])
        self.assertEqual(row["cost_usd"], 15.0)  # default output 15/1M * 1M


_PLAN_FIXTURE = """# Plan — Demo

> Feature: `260626-demo`

## Tasks

- ⬜ **1.1** First task.
  *Verify:* it works.
- ✅ **1.2** Second task.
  *Verify:* it works.
- 🟡 **2.1** Third task.
  *Verify:* it works.

## Acceptance criteria

- [ ] First criterion.
- [ ] Second criterion.

<promise>260626_DEMO_COMPLETE</promise>
"""


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


class TraceTest(MetricsTestCase):
    """Task 29 — step-level run trace derived read-only from a session's
    live.jsonl (+ main.jsonl for tokens) grouped by loop iteration."""

    def _write_live(self, sid, rows):
        path = os.path.join(self.kdir, "logs", "sessions", sid, "live.jsonl")
        _write_jsonl(path, rows)
        return path

    def _write_metrics_jsonl(self, lines):
        path = os.path.join(self.kdir, "logs", "metrics.jsonl")
        _write_jsonl(path, lines)
        return path

    def _seed(self, feature="260626-trace", sid="sess-trace"):
        # main.jsonl: two assistant turns each emitting one tool_use, so the
        # ordered token shares are [Bash=10, Read=20]; references the feature so
        # the feature-scoped trace finds this session.
        self._session(sid, [
            _user("2026-06-26T09:00:00Z", text="work on work/%s" % feature),
            _assistant("2026-06-26T09:00:30Z",
                       usage=_usage(i=100, o=10), tools=["Bash"]),
            _assistant("2026-06-26T09:02:30Z",
                       usage=_usage(i=50, o=20), tools=["Read"]),
        ])
        # live.jsonl: the per-action stream with name/args/result/status/ts.
        self._write_live(sid, [
            {"ts": "2026-06-26T09:00:30Z", "tool": "Bash", "status": "ok",
             "input": json.dumps({"command": "ls"}),
             "response": json.dumps({"stdout": "a\nb"})},
            {"ts": "2026-06-26T09:02:30Z", "tool": "Read", "status": "ERR",
             "input": json.dumps({"file_path": "/x"}),
             "response": json.dumps({"error": "nope"})},
        ])
        # Two MAIN iterations bound the two steps (it1 @09:00, it2 @09:02) +
        # a task transition in iteration 2.
        self._write_metrics_jsonl([
            {"ts": "2026-06-26T09:00:00Z", "feature": feature, "phase": "main",
             "iteration": 1, "tasks_remaining": 2},
            {"ts": "2026-06-26T09:02:00Z", "feature": feature, "phase": "main",
             "iteration": 2, "tasks_remaining": 1},
            {"event": "task_transition", "ts": "2026-06-26T09:02:40Z",
             "feature": feature, "iteration": 2, "task": "1",
             "from": "open", "to": "done"},
        ])
        return feature, sid

    def test_session_trace_ordered_steps_io_timing_iteration(self):
        feature, sid = self._seed()
        tr = CLI.derive_trace(self.kdir, session=sid)
        self.assertEqual(tr["scope"], "session")
        self.assertEqual(tr["feature"], feature)  # inferred from the transcript
        self.assertEqual(tr["step_count"], 2)
        self.assertEqual(tr["err_count"], 1)
        self.assertEqual(tr["tool_summary"], {"Bash": 1, "Read": 1})
        # Grouped by loop iteration: step 1 -> it1, step 2 -> it2.
        its = {g["iteration"]: g for g in tr["iterations"]}
        self.assertIn(1, its)
        self.assertIn(2, its)
        s1 = its[1]["steps"][0]
        self.assertEqual(s1["tool"], "Bash")
        self.assertEqual(s1["index"], 0)
        self.assertIn("ls", s1["args"])           # tool input preserved
        self.assertIn("stdout", s1["result"])     # tool output preserved
        self.assertEqual(s1["status"], "ok")
        self.assertEqual(s1["tokens"], 10)        # main-turn token share
        self.assertEqual(s1["duration_s"], 120.0)  # 09:00:30 -> 09:02:30
        s2 = its[2]["steps"][0]
        self.assertEqual(s2["tool"], "Read")
        self.assertEqual(s2["status"], "ERR")
        self.assertEqual(s2["tokens"], 20)
        # Marker transition is attached to its iteration group.
        self.assertEqual(len(its[2]["transitions"]), 1)
        self.assertEqual(its[2]["transitions"][0]["task"], "1")

    def test_feature_trace_collects_matching_sessions(self):
        feature, sid = self._seed()
        tr = CLI.derive_trace(self.kdir, feature=feature)
        self.assertEqual(tr["scope"], "feature")
        self.assertEqual([s["session_id"] for s in tr["sessions"]], [sid])
        self.assertEqual(tr["step_count"], 2)

    def test_trace_main_jsonl_fallback_without_live(self):
        # No live.jsonl -> steps fall back to main.jsonl tool_use items.
        self._session("sess-nolive", [
            _assistant("2026-06-26T10:00:00Z",
                       usage=_usage(i=10, o=8), tools=["Grep"]),
        ])
        tr = CLI.derive_trace(self.kdir, session="sess-nolive")
        self.assertEqual(tr["step_count"], 1)
        st = tr["iterations"][0]["steps"][0]
        self.assertEqual(st["tool"], "Grep")
        self.assertEqual(st["result"], "")        # no streamed result
        self.assertEqual(st["tokens"], 8)

    def test_trace_empty_when_no_session(self):
        tr = CLI.derive_trace(self.kdir, session="does-not-exist")
        self.assertEqual(tr["step_count"], 0)
        self.assertEqual(tr["iterations"], [])
        self.assertEqual(tr["sessions"], [])


class LoopHealthTest(MetricsTestCase):
    """Task 30 — loop-health & runaway detection: duplicate tool calls (dead
    loop), no-progress (flat tasks_remaining), context-window overflow, and the
    overall OK/at-risk/critical badge — all derived read-only from the existing
    live.jsonl + metrics.jsonl + session-row channels."""

    def _write_live(self, sid, rows):
        _write_jsonl(os.path.join(self.kdir, "logs", "sessions", sid,
                                  "live.jsonl"), rows)

    def _write_metrics_jsonl(self, lines):
        _write_jsonl(os.path.join(self.kdir, "logs", "metrics.jsonl"), lines)

    def _write_plan(self, feature, body):
        path = os.path.join(self.kdir, "work", feature, "plan.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)

    def test_duplicate_tool_calls_flag_dead_loop(self):
        # A run that fires the SAME (tool, args) within ONE session past the
        # dead-loop threshold (12) -> dup-loop critical. (Normal iterative work
        # legitimately repeats a few times, so the bar is intentionally high.)
        feature, sid = "260626-dup", "sess-dup"
        self._write_plan(feature, "## Tasks\n\n- 🟡 **1** A.\n")
        self._session(sid, [
            _user("2026-06-26T09:00:00Z", text="work on work/%s" % feature),
        ])
        same = json.dumps({"command": "ls"})
        self._write_live(sid, [
            {"ts": "2026-06-26T09:00:%02dZ" % (10 + i), "tool": "Bash",
             "status": "ok", "input": same,
             "response": json.dumps({"stdout": "x"})}
            for i in range(12)
        ])
        self._write_metrics_jsonl([
            {"ts": "2026-06-26T09:00:05Z", "feature": feature, "phase": "main",
             "iteration": 1, "tasks_remaining": 1},
        ])
        h = CLI.derive_loop_health(self.kdir, feature=feature)
        dup = h["checks"]["duplicate_tool_calls"]
        self.assertTrue(dup["flagged"])
        self.assertEqual(dup["max_repeat"], 12)
        self.assertEqual(dup["tool"], "Bash")
        self.assertEqual(dup["iteration"], 1)
        self.assertEqual(h["status"], "critical")
        self.assertEqual(h["offender"]["check"], "duplicate_tool_calls")
        self.assertIn("duplicate_tool_calls", h["flagged_checks"])

    def test_no_progress_flat_tasks_remaining(self):
        # tasks_remaining stays at 2 across three MAIN iterations -> no-progress.
        feature = "260626-stuck"
        self._write_plan(feature, "## Tasks\n\n- 🟡 **1** A.\n- ⬜ **2** B.\n")
        self._write_metrics_jsonl([
            {"ts": "2026-06-26T08:00:00Z", "feature": feature, "phase": "main",
             "iteration": 1, "max": 20, "tasks_remaining": 2},
            {"ts": "2026-06-26T08:01:00Z", "feature": feature, "phase": "main",
             "iteration": 2, "max": 20, "tasks_remaining": 2},
            {"ts": "2026-06-26T08:02:00Z", "feature": feature, "phase": "main",
             "iteration": 3, "max": 20, "tasks_remaining": 2},
        ])
        h = CLI.derive_loop_health(self.kdir, feature=feature)
        prog = h["checks"]["no_progress"]
        self.assertTrue(prog["flagged"])
        self.assertEqual(prog["flat_run"], 3)
        self.assertEqual(prog["flat_iteration"], 3)
        self.assertIn(h["status"], ("at_risk", "critical"))

    def test_context_window_truncation_risk_configurable(self):
        # peak input-side tokens vs a SMALL configurable window -> truncation.
        feature, sid = "260626-ctx", "sess-ctx"
        self._write_plan(feature, "## Tasks\n\n- 🟡 **1** A.\n")
        self._session(sid, [
            _user("2026-06-26T09:00:00Z", text="work on work/%s" % feature),
            _assistant("2026-06-26T09:00:30Z",
                       usage=_usage(i=900, o=10, cr=100)),
        ])
        # window=1000 -> peak input-side = 900 + 100 = 1000 -> pct 1.0 >= 0.9.
        h = CLI.derive_loop_health(self.kdir, feature=feature, window=1000)
        ctx = h["checks"]["context_window"]
        self.assertTrue(ctx["truncation_risk"])
        self.assertGreaterEqual(ctx["context_window_pct"], 0.9)
        self.assertEqual(ctx["context_window"], 1000)
        self.assertEqual(h["status"], "critical")
        # A generous window clears the risk (configurable threshold proven).
        h2 = CLI.derive_loop_health(self.kdir, feature=feature, window=200000)
        self.assertFalse(h2["checks"]["context_window"]["truncation_risk"])

    def test_healthy_run_is_ok_and_aggregate_summary(self):
        # A clean completed run: no dups, burndown falls, small context -> ok.
        feature, sid = "260626-ok", "sess-ok"
        self._write_plan(feature, "## Tasks\n\n- ✅ **1** A.\n- ✅ **2** B.\n")
        self._session(sid, [
            _user("2026-06-26T09:00:00Z", text="work on work/%s" % feature),
            _assistant("2026-06-26T09:00:30Z", usage=_usage(i=100, o=10),
                       tools=["Read"]),
        ])
        self._write_live(sid, [
            {"ts": "2026-06-26T09:00:30Z", "tool": "Read", "status": "ok",
             "input": json.dumps({"file_path": "/a"}),
             "response": json.dumps({"content": "hi"})},
        ])
        self._write_metrics_jsonl([
            {"ts": "2026-06-26T09:00:00Z", "feature": feature, "phase": "main",
             "iteration": 1, "max": 10, "tasks_remaining": 2,
             "tasks_done_delta": 0},
            {"ts": "2026-06-26T09:01:00Z", "feature": feature, "phase": "main",
             "iteration": 2, "max": 10, "tasks_remaining": 0,
             "tasks_done_delta": 2},
            {"event": "terminal", "ts": "2026-06-26T09:02:00Z",
             "feature": feature, "outcome": "completed", "iterations_used": 2,
             "max": 10},
        ])
        h = CLI.derive_loop_health(self.kdir, feature=feature)
        self.assertEqual(h["status"], "ok")
        self.assertIsNone(h["offender"])
        self.assertEqual(h["flagged_checks"], [])
        # Aggregate across all work features carries a summary + worst status.
        agg = CLI.derive_loop_health(self.kdir)
        feats = {f["feature"]: f for f in agg["features"]}
        self.assertIn(feature, feats)
        self.assertIn("ok", agg["summary"])
        self.assertIn(agg["status"], ("ok", "at_risk", "critical"))

    def test_empty_channel_is_safe(self):
        # No emission/logs at all -> every check OK, never crashes.
        self._write_plan("260626-empty", "## Tasks\n\n- ⬜ **1** A.\n")
        h = CLI.derive_loop_health(self.kdir, feature="260626-empty")
        self.assertEqual(h["status"], "ok")
        self.assertFalse(any(c["flagged"] for c in h["checks"].values()))


class AlertsTest(MetricsTestCase):
    """Task 31 — symptom-based, severity-ranked alerts derived read-only from the
    existing telemetry: reliability/nDCG regression, retrieval scaling-slope,
    cost spike, stuck-loop/runaway, stale/conflicting KB, and unpromoted
    LEARNED/DECISION at finish — each deep-linking to its panel, plus ledger
    commit markers for the trend charts."""

    def _write_ledger(self, rows):
        _write_jsonl(os.path.join(self.kdir, "benchmarks", "history.jsonl"),
                     rows)

    def _write_plan(self, feature, body):
        path = os.path.join(self.kdir, "work", feature, "plan.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)

    def _cats(self, result):
        return {a["category"] for a in result["alerts"]}

    def _by_cat(self, result, category):
        return [a for a in result["alerts"] if a["category"] == category]

    def test_regression_and_scaling_from_ledger(self):
        # recall@k FALLS as corpus grows (negative slope) and the last two runs
        # drop reliability + nDCG -> a regression alert AND a scaling alert.
        self._write_ledger([
            {"run": "r0", "commit": "c0ffee0", "strategy": "bm25",
             "metrics": {"recall_at_k": 0.60, "ndcg_at_k": 0.70},
             "corpus_size": 10, "reliability": 90.0},
            {"run": "r1", "commit": "c0ffee1", "strategy": "bm25",
             "metrics": {"recall_at_k": 0.50, "ndcg_at_k": 0.60},
             "corpus_size": 20, "reliability": 85.0},
            {"run": "r2", "commit": "c0ffee2", "strategy": "bm25",
             "metrics": {"recall_at_k": 0.40, "ndcg_at_k": 0.50},
             "corpus_size": 30, "reliability": 70.0},
        ])
        res = CLI.derive_alerts(self.kdir, max_age_days=100000)
        cats = self._cats(res)
        self.assertIn("regression", cats)
        self.assertIn("scaling", cats)
        # Reliability fell 15 pts (85 -> 70) >= the critical threshold.
        reg = self._by_cat(res, "regression")
        rel = next(a for a in reg if a.get("metric") == "reliability")
        self.assertEqual(rel["severity"], "critical")
        self.assertEqual(rel["deep_link"], "/health/quality")
        # Scaling slope is negative -> a warning deep-linking to the scaling panel.
        scl = self._by_cat(res, "scaling")[0]
        self.assertEqual(scl["deep_link"], "/memory/scaling")
        self.assertLess(scl["slope"], 0.0)
        # Commit markers carry every ledger SHA (chronological).
        shas = [m["commit"] for m in res["commit_markers"]]
        self.assertEqual(shas, ["c0ffee0", "c0ffee1", "c0ffee2"])
        # Severity-ranked: the first alert is the worst (critical).
        self.assertEqual(res["alerts"][0]["severity"], "critical")
        self.assertEqual(res["status"], "critical")

    def test_no_regression_when_metrics_improve(self):
        self._write_ledger([
            {"run": "r0", "commit": "a", "strategy": "bm25",
             "metrics": {"recall_at_k": 0.40, "ndcg_at_k": 0.50},
             "corpus_size": 10, "reliability": 70.0},
            {"run": "r1", "commit": "b", "strategy": "bm25",
             "metrics": {"recall_at_k": 0.60, "ndcg_at_k": 0.70},
             "corpus_size": 20, "reliability": 88.0},
        ])
        res = CLI.derive_alerts(self.kdir, max_age_days=100000)
        self.assertNotIn("regression", self._cats(res))
        self.assertNotIn("scaling", self._cats(res))

    def test_cost_spike_alert(self):
        for day, out_tokens in (("01", 100), ("02", 100), ("03", 100),
                                ("04", 500)):
            self._session("s-%s" % day, [
                _assistant("2026-06-%sT10:00:00.000Z" % day,
                           model="claude-sonnet-4",
                           usage=_usage(o=out_tokens))])
        res = CLI.derive_alerts(self.kdir, max_age_days=100000)
        cost = self._by_cat(res, "cost")
        self.assertEqual(len(cost), 1)
        self.assertEqual(cost[0]["severity"], "warning")
        self.assertEqual(cost[0]["deep_link"], "/cost")
        self.assertIn("2026-06-04", cost[0]["dates"])

    def test_loop_runaway_alert(self):
        # Identical tool calls past the dead-loop threshold (12) within one
        # session -> loop-health critical -> a loop alert.
        feature, sid = "260626-dup", "sess-dup"
        self._write_plan(feature, "## Tasks\n\n- 🟡 **1** A.\n")
        self._session(sid, [
            _user("2026-06-26T09:00:00Z", text="work on work/%s" % feature),
        ])
        same = json.dumps({"command": "ls"})
        _write_jsonl(
            os.path.join(self.kdir, "logs", "sessions", sid, "live.jsonl"),
            [{"ts": "2026-06-26T09:00:%02dZ" % (10 + i), "tool": "Bash",
              "status": "ok", "input": same,
              "response": json.dumps({"stdout": "x"})} for i in range(12)])
        _write_jsonl(os.path.join(self.kdir, "logs", "metrics.jsonl"), [
            {"ts": "2026-06-26T09:00:05Z", "feature": feature, "phase": "main",
             "iteration": 1, "tasks_remaining": 1}])
        res = CLI.derive_alerts(self.kdir, max_age_days=100000)
        loop = self._by_cat(res, "loop")
        self.assertTrue(loop)
        a = next(x for x in loop if x["feature"] == feature)
        self.assertEqual(a["severity"], "critical")
        self.assertEqual(a["deep_link"], "/loops/health")
        self.assertIn("duplicate_tool_calls", a["flagged_checks"])

    def test_unpromoted_markers_at_finish(self):
        # A COMPLETED run (all tasks ✅) whose worklog still has an unpromoted
        # LEARNED marker -> an unpromoted alert (R-SI-03 symptom).
        feature = "260626-done"
        self._write_plan(feature, "## Tasks\n\n- ✅ **1** A.\n- ✅ **2** B.\n")
        wl = os.path.join(self.kdir, "work", feature, "worklog.md")
        with open(wl, "w", encoding="utf-8") as f:
            f.write("# Worklog\n\n> LEARNED: zzz unique never-promoted marker\n")
        res = CLI.derive_alerts(self.kdir, max_age_days=100000)
        unp = self._by_cat(res, "unpromoted")
        self.assertTrue(unp)
        a = unp[0]
        self.assertEqual(a["feature"], feature)
        self.assertEqual(a["severity"], "warning")
        self.assertEqual(a["deep_link"], "/loops/outcomes")
        self.assertGreaterEqual(a["count"], 1)

    def test_no_unpromoted_alert_for_unfinished_run(self):
        # Open tasks remain -> not completed -> no unpromoted alert even with a
        # dangling marker (only FINISHED runs gate at the alert surface).
        feature = "260626-wip"
        self._write_plan(feature, "## Tasks\n\n- ⬜ **1** A.\n")
        wl = os.path.join(self.kdir, "work", feature, "worklog.md")
        with open(wl, "w", encoding="utf-8") as f:
            f.write("# Worklog\n\n> LEARNED: zzz unique never-promoted marker\n")
        res = CLI.derive_alerts(self.kdir, max_age_days=100000)
        self.assertNotIn("unpromoted", self._cats(res))

    def test_kb_conflict_alert(self):
        # Two near-identical learnings (high Jaccard) -> a kb_conflict warning.
        dup_body = ("# Geofence reminders\n\nGeofence reminders fire when the "
                    "device enters a saved location radius using background "
                    "location updates and a local notification scheduler.\n")
        from tests._fixtures import _ENTRIES, build_synthetic_kb
        entries = list(_ENTRIES) + [
            {"id": "learn-geo-a", "title": "Geofence A",
             "category": "learnings", "path": "learnings/geo-a.md",
             "tags": ["geofence"], "created": "2026-06-01",
             "last_verified": "2026-06-20", "summary": "geofence reminders",
             "body": dup_body},
            {"id": "learn-geo-b", "title": "Geofence B",
             "category": "learnings", "path": "learnings/geo-b.md",
             "tags": ["geofence"], "created": "2026-06-01",
             "last_verified": "2026-06-20", "summary": "geofence reminders",
             "body": dup_body},
        ]
        build_synthetic_kb(self.kdir, entries=entries)
        res = CLI.derive_alerts(self.kdir, max_age_days=100000)
        conf = self._by_cat(res, "kb_conflict")
        self.assertTrue(conf)
        self.assertEqual(conf[0]["severity"], "warning")
        self.assertGreaterEqual(conf[0]["count"], 1)

    def test_kb_stale_alert(self):
        # An entry last_verified far in the past is stale for any test date.
        from tests._fixtures import _ENTRIES, build_synthetic_kb
        entries = list(_ENTRIES) + [
            {"id": "learn-ancient", "title": "Ancient learning",
             "category": "learnings", "path": "learnings/ancient.md",
             "tags": ["old"], "created": "2000-01-01",
             "last_verified": "2000-01-01", "summary": "very old",
             "body": "# Ancient\n\nA very old, never-reverified learning.\n"},
        ]
        build_synthetic_kb(self.kdir, entries=entries)
        res = CLI.derive_alerts(self.kdir, max_age_days=120)
        stale = self._by_cat(res, "kb_stale")
        self.assertTrue(stale)
        self.assertEqual(stale[0]["severity"], "info")
        self.assertIn("learn-ancient", stale[0]["ids"])

    def test_empty_is_clean_and_read_only(self):
        # No ledger / sessions / loops, fresh KB, generous freshness window ->
        # zero alerts, status ok.
        res = CLI.derive_alerts(self.kdir, max_age_days=100000)
        self.assertEqual(res["open_count"], 0)
        self.assertEqual(res["alerts"], [])
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["summary"],
                         {"critical": 0, "warning": 0, "info": 0})

    def test_deterministic_across_runs(self):
        self._write_ledger([
            {"run": "r0", "commit": "c0ffee0", "strategy": "bm25",
             "metrics": {"recall_at_k": 0.60, "ndcg_at_k": 0.70},
             "corpus_size": 10, "reliability": 90.0},
            {"run": "r1", "commit": "c0ffee1", "strategy": "bm25",
             "metrics": {"recall_at_k": 0.40, "ndcg_at_k": 0.50},
             "corpus_size": 20, "reliability": 70.0},
        ])
        a = CLI.derive_alerts(self.kdir, max_age_days=100000)
        b = CLI.derive_alerts(self.kdir, max_age_days=100000)
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))


class FailuresTest(MetricsTestCase):
    """Task 32 — failure-ladder & error-recovery: tool ERR rate (live.jsonl),
    R-FAIL ladder tier usage (kb->reasoning->inputs->switch->web), web-search
    escalations, self-heal re-engagements, and DECISION/LEARNED tallies — all
    derived read-only for ONE feature."""

    def _write_live(self, sid, rows):
        _write_jsonl(os.path.join(self.kdir, "logs", "sessions", sid,
                                  "live.jsonl"), rows)

    def _write_metrics_jsonl(self, lines):
        _write_jsonl(os.path.join(self.kdir, "logs", "metrics.jsonl"), lines)

    def _write_plan(self, feature, body):
        path = os.path.join(self.kdir, "work", feature, "plan.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)

    def _write_worklog(self, feature, body):
        path = os.path.join(self.kdir, "work", feature, "worklog.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)

    def _step(self, ts, tool, status, inp, resp="{}"):
        return {"ts": ts, "tool": tool, "status": status,
                "input": json.dumps(inp), "response": resp}

    def test_err_rate_ladder_websearch_and_marker_tallies(self):
        feature, sid = "260626-fail", "sess-fail"
        self._write_plan(feature, "## Tasks\n\n- 🟡 **1** A.\n")
        # The transcript must reference the feature so the feature-scoped trace
        # finds this session.
        self._session(sid, [
            _user("2026-06-26T09:00:00Z", text="work on work/%s" % feature)])
        # Six steps. Three ERRs, each recovered by a DIFFERENT ladder tier:
        #  1 Read ERR -> 2 agentware recall (KB lookup, tier "kb")
        #  3 Bash ERR -> 4 Bash CHANGED args (tier "inputs")
        #  5 Edit ERR -> 6 WebSearch (tier "web", also a web escalation)
        self._write_live(sid, [
            self._step("2026-06-26T09:00:01Z", "Read", "ERR",
                       {"file_path": "/x"}, json.dumps({"error": "nope"})),
            self._step("2026-06-26T09:00:02Z", "Bash", "ok",
                       {"command": "scripts/agentware recall \"x\""}),
            self._step("2026-06-26T09:00:03Z", "Bash", "ERR",
                       {"command": "npm test"}, json.dumps({"error": "1"})),
            self._step("2026-06-26T09:00:04Z", "Bash", "ok",
                       {"command": "npm test -- --runInBand"}),
            self._step("2026-06-26T09:00:05Z", "Edit", "ERR",
                       {"file_path": "/y"}, json.dumps({"error": "bad"})),
            self._step("2026-06-26T09:00:06Z", "WebSearch", "ok",
                       {"query": "how to fix"}),
        ])
        self._write_worklog(feature,
                            "> LEARNED: a thing about loops\n"
                            "> DECISION: chose option A over B\n"
                            "> LEARNED: another gotcha\n")

        f = CLI.derive_failures(self.kdir, feature)
        self.assertEqual(f["feature"], feature)
        self.assertEqual(f["step_count"], 6)
        self.assertEqual(f["err_count"], 3)
        self.assertAlmostEqual(f["err_rate"], 0.5)
        self.assertEqual(f["err_by_tool"], {"Read": 1, "Bash": 1, "Edit": 1})
        # One recovery per tier from the three ERRs.
        self.assertEqual(f["ladder"]["kb"], 1)
        self.assertEqual(f["ladder"]["inputs"], 1)
        self.assertEqual(f["ladder"]["web"], 1)
        self.assertEqual(f["ladder"]["reasoning"], 0)
        self.assertEqual(f["ladder"]["switch"], 0)
        self.assertEqual(f["ladder_order"],
                         ["kb", "reasoning", "inputs", "switch", "web"])
        self.assertEqual(f["web_search_count"], 1)
        self.assertEqual(f["kb_lookup_count"], 1)
        self.assertEqual(f["unrecovered"], 0)
        # Marker tallies (all unpromoted in this synthetic worklog).
        self.assertEqual(f["markers"]["learned"]["total"], 2)
        self.assertEqual(f["markers"]["decision"]["total"], 1)
        self.assertEqual(f["markers"]["learned"]["unpromoted"], 2)

    def test_switch_and_reasoning_and_unrecovered(self):
        feature, sid = "260626-fail2", "sess-fail2"
        self._write_plan(feature, "## Tasks\n\n- 🟡 **1** A.\n")
        self._session(sid, [
            _user("2026-06-26T09:00:00Z", text="work on work/%s" % feature)])
        # 1 Read ERR -> 2 Bash (different tool, "switch")
        # 3 Bash ERR -> 4 Bash SAME args (a reasoned retry, "reasoning")
        # 5 Edit ERR -> (no next step, "unrecovered")
        same = {"command": "make"}
        self._write_live(sid, [
            self._step("2026-06-26T09:00:01Z", "Read", "ERR",
                       {"file_path": "/x"}, json.dumps({"error": "no"})),
            self._step("2026-06-26T09:00:02Z", "Bash", "ok", same),
            self._step("2026-06-26T09:00:03Z", "Bash", "ERR", same,
                       json.dumps({"error": "fail"})),
            self._step("2026-06-26T09:00:04Z", "Bash", "ok", same),
            self._step("2026-06-26T09:00:05Z", "Edit", "ERR",
                       {"file_path": "/y"}, json.dumps({"error": "bad"})),
        ])
        f = CLI.derive_failures(self.kdir, feature)
        self.assertEqual(f["err_count"], 3)
        self.assertEqual(f["ladder"]["switch"], 1)
        self.assertEqual(f["ladder"]["reasoning"], 1)
        self.assertEqual(f["unrecovered"], 1)
        self.assertEqual(f["web_search_count"], 0)

    def test_self_heal_count_from_terminal(self):
        feature = "260626-fail3"
        self._write_plan(feature, "## Tasks\n\n- ✅ **1** A.\n")
        self._write_metrics_jsonl([
            {"event": "terminal", "ts": "2026-06-26T09:10:00Z",
             "feature": feature, "outcome": "completed", "iterations_used": 2,
             "max": 10, "self_heal_count": 3, "tasks_total": 1, "tasks_done": 1,
             "promise_status": "signalled"},
        ])
        f = CLI.derive_failures(self.kdir, feature)
        self.assertEqual(f["self_heal_count"], 3)

    def test_empty_when_no_logs(self):
        f = CLI.derive_failures(self.kdir, "260626-nothing")
        self.assertEqual(f["step_count"], 0)
        self.assertEqual(f["err_count"], 0)
        self.assertEqual(f["err_rate"], 0.0)
        self.assertEqual(f["ladder"],
                         {"kb": 0, "reasoning": 0, "inputs": 0,
                          "switch": 0, "web": 0})
        self.assertEqual(f["self_heal_count"], 0)
        self.assertEqual(f["markers"]["learned"]["total"], 0)

    def test_read_only_no_index_mutation(self):
        feature, sid = "260626-fail-ro", "sess-fail-ro"
        self._session(sid, [
            _user("2026-06-26T09:00:00Z", text="work on work/%s" % feature)])
        self._write_live(sid, [
            self._step("2026-06-26T09:00:01Z", "Read", "ERR",
                       {"file_path": "/x"}, json.dumps({"error": "no"}))])
        before = os.path.getmtime(os.path.join(self.kdir, "index.json"))
        CLI.derive_failures(self.kdir, feature)
        after = os.path.getmtime(os.path.join(self.kdir, "index.json"))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
