"""DreamFailingTestParseTests, DreamDetectReportRenderTests, DreamReportNoClockTests, DreamCycleOutcomeTests, DreamHealthAgeOutcomeTests -- split from `test_dream` (task 58).

See `tests.unit.features._dream_support` for the
shared header.
"""

from tests.unit.features._dream_support import (  # noqa: F401
    _GuardedEnv, build_synthetic_kb, json, load_cli, os, shutil, tempfile,
    unittest)


class DreamFailingTestParseTests(unittest.TestCase):
    """Task 1 — recover *which* tests failed from unittest's stderr header lines."""

    def setUp(self):
        self.mod = load_cli()

    def test_parses_fail_and_error_ids(self):
        err = (
            "FAIL: test_alpha (tests.test_mod.Case)\n"
            "Traceback (most recent call last):\n  ...\n"
            "ERROR: test_beta (tests.test_mod.Case.test_beta)\n"
            "  raise RuntimeError\n"
            "Ran 3 tests in 0.01s\nFAILED (failures=1, errors=1)\n")
        self.assertEqual(
            self.mod._parse_failing_test_ids(err),
            ["test_alpha (tests.test_mod.Case)",
             "test_beta (tests.test_mod.Case.test_beta)"])

    def test_dedupes_preserving_first_seen_order(self):
        err = ("FAIL: test_b (m.C)\nFAIL: test_a (m.C)\nFAIL: test_b (m.C)\n")
        self.assertEqual(self.mod._parse_failing_test_ids(err),
                         ["test_b (m.C)", "test_a (m.C)"])

    def test_ignores_noise_and_empty(self):
        self.assertEqual(self.mod._parse_failing_test_ids(""), [])
        self.assertEqual(self.mod._parse_failing_test_ids(None), [])
        self.assertEqual(
            self.mod._parse_failing_test_ids("OK\nRan 5 tests\n  some FAIL: x\n"),
            [], "only header lines (after strip) count, not substrings mid-line")

    def test_record_passrate_detail_nested_guard_returns_4tuple(self):
        # We ARE inside a suite run (AGENTWARE_NESTED_UNITTEST is set by the
        # runner / the verify cmd), so the recursion guard returns the empty
        # 4-tuple instead of re-spawning the suite.
        prev = os.environ.get("AGENTWARE_NESTED_UNITTEST")
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"
        try:
            self.assertEqual(self.mod._record_test_passrate_detail(),
                             (None, 0, 0, []))
            # The back-compatible 3-tuple wrapper still works for old call sites.
            self.assertEqual(self.mod._record_test_passrate(), (None, 0, 0))
        finally:
            if prev is None:
                os.environ.pop("AGENTWARE_NESTED_UNITTEST", None)
            else:
                os.environ["AGENTWARE_NESTED_UNITTEST"] = prev

    def test_audit_tests_check_threads_sorted_failed_test_ids(self):
        # `_audit_tests_check` shells the suite for real, so we stub subprocess.run
        # (NEVER call it live from inside the suite — that recurses). The contract:
        # it parses the FAIL:/ERROR: ids and threads them, sorted (INV-1), into the
        # check payload that `audit --with-tests` (dream step c) consumes.
        mod = self.mod

        class _Proc:
            returncode = 1
            stdout = ""
            stderr = ("FAIL: test_b (m.C)\nTraceback...\n"
                      "ERROR: test_a (m.C)\nTraceback...\n"
                      "Ran 2 tests in 0.0s\n\nFAILED (failures=1, errors=1)\n")

        saved = mod.subprocess.run
        mod.subprocess.run = lambda *a, **k: _Proc()
        try:
            chk = mod._audit_tests_check()
        finally:
            mod.subprocess.run = saved
        self.assertEqual(chk["name"], "unittest")
        self.assertFalse(chk["ok"])
        self.assertEqual(chk["failed_tests"], ["test_a (m.C)", "test_b (m.C)"],
                         "failed_tests must be sorted (byte-stable, INV-1)")

class DreamDetectReportRenderTests(unittest.TestCase):
    """Task 5 — actionable, byte-stable, report-only detect report (INV-1/INV-2)."""

    def setUp(self):
        self.mod = load_cli()
        self.report = {
            "stale": [
                {"id": "learn-z", "category": "learnings",
                 "last_verified": "2025-01-01", "age_days": 400,
                 "path": "learnings/z.md"},
                {"id": "learn-a", "category": "references",
                 "last_verified": "2025-02-01", "age_days": 300,
                 "path": "references/a.md"},
            ],
            "conflicts": [
                {"a": "id-b", "b": "id-a", "category": "references",
                 "jaccard": 0.6537},
            ],
            "max_age_days": 120,
            "volatile_categories": ["configurations", "references"],
        }
        self.markers = [
            {"feature": "260628-x", "rel": "work/260628-x/worklog.md",
             "line": 12, "kind": "LEARNED", "text": "a discovery"},
            {"feature": "260628-x", "rel": "work/260628-x/worklog.md",
             "line": 3, "kind": "DECISION", "text": "a choice"},
        ]

    def test_plan_only_section_renders_sorted_when_present(self):
        plan_only = [
            {"feature": "260101-b", "rel": "work/260101-b/plan.md",
             "age_hours": 72.0},
            {"feature": "260101-a", "rel": "work/260101-a/plan.md",
             "age_hours": 96.5},
        ]
        body = self.mod._dream_render_detect_report(
            self.report, self.markers, plan_only)
        self.assertIn("## Plan-only work dirs (2)", body)
        self.assertLess(body.index("work/260101-a/plan.md"),
                        body.index("work/260101-b/plan.md"),
                        "plan-only rows sort by rel")
        self.assertIn("work/260101-a/plan.md age=96.5h", body)

    def test_plan_only_section_zero_by_default_two_arg(self):
        # Back-compatible 2-arg call renders the section with count 0 + _none_.
        body = self.mod._dream_render_detect_report(self.report, self.markers)
        self.assertIn("## Plan-only work dirs (0)", body)

    def test_plan_only_render_is_byte_stable(self):
        plan_only = [{"feature": "260101-a", "rel": "work/260101-a/plan.md",
                      "age_hours": 50.0}]
        a = self.mod._dream_render_detect_report(
            self.report, self.markers, plan_only)
        b = self.mod._dream_render_detect_report(
            self.report, self.markers, plan_only)
        self.assertEqual(a, b)

    def test_report_enumerates_each_finding(self):
        body = self.mod._dream_render_detect_report(self.report, self.markers)
        # stale: sorted by id (learn-a before learn-z)
        self.assertLess(body.index("learn-a"), body.index("learn-z"))
        self.assertIn("learn-z [learnings] last_verified=2025-01-01 age=400d", body)
        # duplicate pair with the jaccard
        self.assertIn("id-b <-> id-a [references] jaccard=0.6537", body)
        # markers grouped by feature, sorted by line (3 before 12), with kind+text
        self.assertIn("work/260628-x/worklog.md:3 [DECISION] a choice", body)
        self.assertIn("work/260628-x/worklog.md:12 [LEARNED] a discovery", body)
        self.assertLess(body.index("worklog.md:3"), body.index("worklog.md:12"))
        # section headers carry the counts
        self.assertIn("## Stale entries (2)", body)
        self.assertIn("## Possible duplicate / conflict pairs (1)", body)
        self.assertIn("## Unpromoted worklog markers (2)", body)

    def test_render_is_byte_stable(self):
        a = self.mod._dream_render_detect_report(self.report, self.markers)
        b = self.mod._dream_render_detect_report(self.report, self.markers)
        self.assertEqual(a, b, "report body must be byte-stable for unchanged inputs")

    def test_write_report_overwrites_byte_stable(self):
        kdir = tempfile.mkdtemp(prefix="agentware-dream-report-")
        self.addCleanup(shutil.rmtree, kdir, True)
        body = self.mod._dream_render_detect_report(self.report, self.markers)
        rel1 = self.mod._dream_write_detect_report(kdir, body)
        self.assertEqual(rel1, self.mod.DREAM_REPORT_REL)
        path = os.path.join(kdir, rel1)
        first = open(path, "rb").read()
        # Overwrite (not append) with the same body -> byte-identical file.
        self.mod._dream_write_detect_report(kdir, body)
        self.assertEqual(open(path, "rb").read(), first)

class DreamReportNoClockTests(unittest.TestCase):
    """The detect report carries NO clock/run-id (INV-1 determinism)."""

    def setUp(self):
        self.mod = load_cli()

    def test_no_timestamp_in_body(self):
        body = self.mod._dream_render_detect_report(
            {"stale": [], "conflicts": [], "max_age_days": 120,
             "volatile_categories": ["references"]}, [])
        # An ISO 'Z' timestamp (YYYY-MM-DDTHH:MM:SSZ) must never appear in the body.
        import re as _re
        self.assertIsNone(_re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", body),
                          "no clock may leak into the byte-stable report body")

class DreamCycleOutcomeTests(unittest.TestCase):
    """Task 6 — heartbeat outcome classifier (skip-tolerant, like _dream_output)."""

    def setUp(self):
        self.mod = load_cli()

    def _rec(self, *statuses):
        return {"event": "dream", "ts": "2026-06-28T00:00:00Z",
                "steps": [{"step": "x%d" % i, "status": s}
                          for i, s in enumerate(statuses)]}

    def test_ok_when_none_failed(self):
        self.assertEqual(
            self.mod._dream_cycle_outcome(self._rec("ok", "skipped", "ok")), "ok")

    def test_fail_when_all_nonskipped_failed(self):
        self.assertEqual(
            self.mod._dream_cycle_outcome(self._rec("fail", "skipped", "error")),
            "fail")

    def test_partial_when_mixed(self):
        self.assertEqual(
            self.mod._dream_cycle_outcome(self._rec("ok", "fail")), "partial")

    def test_skips_and_planned_are_not_failures(self):
        self.assertEqual(
            self.mod._dream_cycle_outcome(self._rec("skipped", "planned")), "ok")

    def test_empty_steps_is_ok(self):
        self.assertEqual(self.mod._dream_cycle_outcome({"steps": []}), "ok")

class DreamHealthAgeOutcomeTests(unittest.TestCase, _GuardedEnv):
    """Task 6 — dream_health reports last-run AGE + OUTCOME and warns on stale/failed."""

    def setUp(self):
        self._save_env()
        self.addCleanup(self._restore_env)
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-hb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = self._mod  # _GuardedEnv loaded + isolated the CLI module
        os.environ["AGENTWARE_DREAM"] = "1"  # ON (hermetic via _GuardedEnv config)

    def _write_dream_event(self, ts, statuses):
        logs = os.path.join(self.kdir, "logs")
        os.makedirs(logs, exist_ok=True)
        rec = {"event": "dream", "ts": ts,
               "steps": [{"step": "x%d" % i, "status": s}
                         for i, s in enumerate(statuses)]}
        with open(os.path.join(logs, "metrics.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    def test_fresh_ok_reports_age_and_outcome(self):
        self._write_dream_event(self.mod.utc_now_iso(), ["ok", "ok", "skipped"])
        c = self.mod._audit_dream_health_check(self.kdir)
        self.assertTrue(c["ok"], c["details"])
        self.assertEqual(c["outcome"], "ok")
        self.assertIsNotNone(c["age_hours"])
        self.assertLess(c["age_hours"], 1.0)
        self.assertIsNotNone(c["last_run"])

    def test_recent_but_failed_warns_with_outcome(self):
        self._write_dream_event(self.mod.utc_now_iso(), ["ok", "fail"])
        c = self.mod._audit_dream_health_check(self.kdir)
        self.assertFalse(c["ok"])
        self.assertEqual(c["outcome"], "partial")
        self.assertIn("did not finish clean", c["details"][0])

    def test_stale_cycle_warns_and_still_reports_outcome(self):
        # A cycle far in the past trips the staleness budget regardless of outcome.
        self._write_dream_event("2020-01-01T00:00:00Z", ["ok", "ok"])
        c = self.mod._audit_dream_health_check(self.kdir)
        self.assertFalse(c["ok"])
        self.assertGreater(c["age_hours"], self.mod.DREAM_STALE_HOURS)
        self.assertIn("stale", c["details"][0])
        self.assertEqual(c["outcome"], "ok")

    def test_inert_when_off(self):
        os.environ["AGENTWARE_DREAM"] = "0"
        c = self.mod._audit_dream_health_check(self.kdir)
        self.assertTrue(c["ok"])
        self.assertIn("inert", c["details"][0])
