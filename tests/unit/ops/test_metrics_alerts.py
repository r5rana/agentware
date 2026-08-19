"""AlertsTest, FailuresTest -- split from `test_metrics` (task 58).

See `tests.unit.ops._metrics_support` for the
shared header.
"""

from tests.unit.ops._metrics_support import (  # noqa: F401
    CLI, MetricsTestCase, _assistant, _usage, _user, _write_jsonl, json,
    os)


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
