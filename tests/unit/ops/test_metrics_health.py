"""TraceTest, LoopHealthTest -- split from `test_metrics` (task 58).

See `tests.unit.ops._metrics_support` for the
shared header.
"""

from tests.unit.ops._metrics_support import (  # noqa: F401
    CLI, MetricsTestCase, _assistant, _usage, _user, _write_jsonl, json,
    os)


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
