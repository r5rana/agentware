"""DreamFailureArtifactTests, DreamNoNetworkTests, DreamObservabilityE2ETests, DreamPlanOnlyDetectTests -- split from `test_dream` (task 58).

See `tests.unit.features._dream_support` for the
shared header.
"""

from tests.unit.features._dream_support import (  # noqa: F401
    _GuardedEnv, _migrate_kb, _seed_gold, _steps_mod, build_synthetic_kb,
    json, load_cli, os, run_cli, shutil, tempfile, time, unittest)



class DreamFailureArtifactTests(unittest.TestCase, _GuardedEnv):
    """Tasks 2+3 — on a failing step the orchestrator persists the full capture to
    logs/dream-failures/<started>.log and the journal + metric carry the failure
    fields. Driven end-to-end by injecting a failing step into DREAM_STEP_FUNCS."""

    def setUp(self):
        self._save_env()
        self.addCleanup(self._restore_env)
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-fail-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = self._mod  # _GuardedEnv loaded + isolated the CLI module

    def test_failure_log_rel_strips_colons(self):
        self.assertEqual(
            self.mod._dream_failure_log_rel("2026-06-28T03:58:49Z"),
            os.path.join("logs", "dream-failures", "2026-06-28T035849Z.log"))

    def test_write_failure_capture_persists_full_text(self):
        rel = self.mod._dream_write_failure_capture(
            self.kdir, "2026-06-28T00:00:00Z", "c",
            "FAIL: tests.test_x (m.C)\nfull audit output\n")
        self.assertIsNotNone(rel)
        body = open(os.path.join(self.kdir, rel), encoding="utf-8").read()
        self.assertIn("FAIL: tests.test_x (m.C)", body)
        self.assertIn("step c", body)

    def test_metric_step_copies_only_present_failure_fields(self):
        # Step c carries failure fields; a plain step stays minimal (byte-stable).
        c = self.mod._dream_metric_step(
            {"step": "c", "status": "fail", "duration_s": 0.1,
             "failed_checks": "unittest", "failed_tests": "tests.test_x",
             "tests_failed": 1, "tests_ran": 791})
        self.assertEqual(c["failed_tests"], "tests.test_x")
        self.assertEqual(c["tests_ran"], 791)
        plain = self.mod._dream_metric_step(
            {"step": "a", "status": "ok", "duration_s": 0.2})
        self.assertEqual(set(plain), {"step", "status", "duration_s"})

    def test_full_failure_lands_on_disk_journal_and_metric(self):
        mod = self.mod
        cap = ("===== audit =====\nFAIL: tests.test_x (m.C)\n"
               "ERROR: tests.test_y (m.C)\nfull captured stderr\n")

        def fake_c(kdir, dry_run):
            if dry_run:
                return {"status": "planned"}
            return {"status": "fail", "failed_checks": "unittest",
                    "tests_ran": 791, "tests_failed": 2,
                    "failed_tests": "tests.test_x,tests.test_y",
                    "_failure_capture": cap}

        saved = _steps_mod(mod).DREAM_STEP_FUNCS
        _steps_mod(mod).DREAM_STEP_FUNCS = (("c", "audit", fake_c),)
        try:
            code, out, err = run_cli(
                ["dream", "--steps", "c", "--force", "--format", "json"],
                self.kdir)
        finally:
            _steps_mod(mod).DREAM_STEP_FUNCS = saved
        self.assertEqual(code, 1, err)  # a failed step -> non-zero exit
        step = json.loads(out)["steps"][0]
        # (1) The per-cycle triage artifact is written + referenced; the private
        #     capture key never leaks into the step dict.
        self.assertNotIn("_failure_capture", step)
        self.assertIn("triage_log", step)
        art = os.path.join(self.kdir, step["triage_log"])
        self.assertTrue(os.path.isfile(art))
        body = open(art, encoding="utf-8").read()
        self.assertIn("FAIL: tests.test_x (m.C)", body)
        self.assertIn("ERROR: tests.test_y (m.C)", body)
        # (2) The journal entry carries the failure fields.
        journal = open(os.path.join(self.kdir, mod.DREAM_JOURNAL_REL),
                       encoding="utf-8").read()
        self.assertIn("failed_tests=tests.test_x,tests.test_y", journal)
        self.assertIn("tests_failed=2", journal)
        self.assertIn("failed_checks=unittest", journal)
        # (3) The metric event's per-step record carries them too.
        metrics = os.path.join(self.kdir, "logs", "metrics.jsonl")
        dreams = [json.loads(l) for l in open(metrics)
                  if l.strip() and json.loads(l).get("event") == "dream"]
        c = dreams[-1]["steps"][0]
        self.assertEqual(c["failed_tests"], "tests.test_x,tests.test_y")
        self.assertEqual(c["failed_checks"], "unittest")
        self.assertEqual(c["tests_failed"], 2)
        self.assertEqual(c["tests_ran"], 791)

class DreamNoNetworkTests(unittest.TestCase):
    """Invariant — dream stays 100% OFFLINE: no outbound ping / dead-man's-switch
    integration anywhere in the dream code (Task 7, acceptance criterion)."""

    def setUp(self):
        self.mod = load_cli()

    def test_no_network_tokens_in_dream_source(self):
        import inspect
        names = [n for n in dir(self.mod)
                 if ("dream" in n.lower()) and callable(getattr(self.mod, n))]
        self.assertTrue(names, "no dream functions discovered")
        src = []
        for n in names:
            try:
                src.append(inspect.getsource(getattr(self.mod, n)))
            except (OSError, TypeError):
                pass
        blob = "\n".join(src)
        # Pure network-call tokens (NOT 'http', which legitimately appears in the
        # launchd plist DTD URL); their absence proves no off-box integration.
        for tok in ("urllib", "urlopen", "socket.", "http.client",
                    "requests.", "healthchecks", "DREAM_PING"):
            self.assertNotIn(tok, blob,
                             "dream code must make no network calls: found %r" % tok)

class DreamObservabilityE2ETests(unittest.TestCase, _GuardedEnv):
    """Task 10 [e2e] — prove the morning-after question is answerable FROM DISK.

    A forced FULL cycle (real steps a,b,d,e,f) with ONE injected failing audit
    check (step c carrying real failing-test names) must, with zero re-runs, land:
    (a) the failing TEST NAMES in logs/metrics.jsonl, the journal, AND
        logs/dream-failures/<ts>.log;
    (b) the duplicate pair + every unpromoted marker in logs/dream-report-latest.md;
    (c) scheduler output redirection in the generated launchd plist + cron line
        (no /dev/null);
    (d) the dream_health heartbeat reporting last-run AGE + OUTCOME.

    Hermetic: synthetic KB, isolated config (_GuardedEnv), injected failing step
    (the real audit shells unittest, which the nested guard disables — so real
    failing-test NAMES can only be produced by injecting step c, per
    learn-dream-cycle-test-monkeypatch-step-funcs). Step f degrades to a skipped
    no-op (autocommit OFF under the isolated config) so the cycle touches no git.
    """

    # Two near-identical `references` entries -> a real jaccard>=0.6 duplicate pair
    # that survives the step-a frontmatter rebuild. Distinct vocabulary from the
    # default entries so EXACTLY one pair is flagged.
    _DUP_BODY = (
        "Token bucket rate limiting refills tokens at a steady configured rate "
        "and permits short bursts up to the bucket capacity before throttling "
        "any further requests, smoothing spiky traffic deterministically.\n")
    _DUP_A = {
        "id": "ref-dup-alpha", "title": "Token Bucket Rate Limiting",
        "category": "references", "path": "references/dup-alpha.md",
        "tags": ["ratelimit", "tokenbucket", "throttle"],
        "created": "2026-01-06",
        "summary": "Token bucket rate limiting refills tokens at a steady rate.",
        "body": "# Token Bucket Rate Limiting\n\n" + _DUP_BODY,
    }
    _DUP_B = {
        "id": "ref-dup-beta", "title": "Token Bucket Throttling",
        "category": "references", "path": "references/dup-beta.md",
        "tags": ["ratelimit", "tokenbucket", "throttle"],
        "created": "2026-01-07",
        "summary": "Token bucket rate limiting refills tokens at a steady rate.",
        "body": "# Token Bucket Throttling\n\n" + _DUP_BODY,
    }

    def setUp(self):
        self._save_env()
        self.addCleanup(self._restore_env)
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-e2e-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.mod = self._mod  # _GuardedEnv loaded + isolated the CLI module
        from tests import _fixtures as fx
        entries = [dict(e) for e in fx._ENTRIES] + [self._DUP_A, self._DUP_B]
        build_synthetic_kb(self.kdir, entries=entries)
        # Frontmatter-complete so step a (index rebuild) is a clean no-op and the
        # two duplicate entries survive into the rebuilt index.
        run_cli(["index", "migrate-frontmatter"], self.kdir)
        run_cli(["index", "rebuild"], self.kdir)
        _seed_gold(self.kdir)
        # An unpromoted-marker worklog for step e to enumerate (text is unique, so
        # scan_worklog flags both markers as unpromoted).
        wdir = os.path.join(self.kdir, "work", "260628-e2e")
        os.makedirs(wdir, exist_ok=True)
        with open(os.path.join(wdir, "worklog.md"), "w", encoding="utf-8") as f:
            f.write("# Worklog — 260628-e2e\n\n"
                    "> LEARNED: e2e dream observability proof marker alpha\n"
                    "> DECISION: e2e injected a failing audit step for determinism\n")
        self._satisfy_health_gate(self.kdir)
        os.environ["AGENTWARE_DREAM"] = "1"  # ON so dream_health is live (not inert)

    def test_full_cycle_is_self_explaining_from_disk(self):
        mod = self.mod
        cap = ("===== audit --with-tests =====\n"
               "FAIL: tests.test_widget (m.WidgetCase.test_render)\n"
               "ERROR: tests.test_db (m.DbCase.test_commit)\n"
               "full captured audit + unittest stderr blob\n")

        def fake_c(kdir, dry_run):
            if dry_run:
                return {"status": "planned", "would": "audit --with-tests"}
            return {"status": "fail", "failed_checks": "unittest",
                    "tests_ran": 802, "tests_failed": 2,
                    "failed_tests": "tests.test_db,tests.test_widget",
                    "_failure_capture": cap}

        # Swap ONLY step c; every other step runs for real -> a genuine full cycle.
        saved = _steps_mod(mod).DREAM_STEP_FUNCS
        _steps_mod(mod).DREAM_STEP_FUNCS = tuple(
            (s, lbl, fake_c if s == "c" else fn) for (s, lbl, fn) in saved)
        try:
            code, out, err = run_cli(
                ["dream", "--force", "--format", "json"], self.kdir)
        finally:
            _steps_mod(mod).DREAM_STEP_FUNCS = saved

        self.assertEqual(code, 1, err)  # a failed step -> non-zero exit
        payload = json.loads(out)
        steps = {s["step"]: s for s in payload["steps"]}
        # The full a-f cycle ran (c failed but never aborted the rest).
        self.assertEqual([s["step"] for s in payload["steps"]],
                         ["1", "2", "a", "b", "c", "d", "e", "s", "k", "m", "g", "f"])
        self.assertEqual(steps["c"]["status"], "fail")
        self.assertEqual(steps["e"]["status"], "ok")

        names = ("tests.test_db", "tests.test_widget")

        # (a) The failing TEST NAMES land in all three artifacts.
        # (a.1) metrics.jsonl dream event, step-c record.
        metrics = os.path.join(self.kdir, "logs", "metrics.jsonl")
        dreams = [json.loads(l) for l in open(metrics, encoding="utf-8")
                  if l.strip() and json.loads(l).get("event") == "dream"]
        mc = {s["step"]: s for s in dreams[-1]["steps"]}["c"]
        self.assertEqual(mc["failed_tests"], "tests.test_db,tests.test_widget")
        self.assertEqual(mc["failed_checks"], "unittest")
        self.assertEqual(mc["tests_failed"], 2)
        self.assertEqual(mc["tests_ran"], 802)
        # (a.2) The journal.
        journal = open(os.path.join(self.kdir, mod.DREAM_JOURNAL_REL),
                       encoding="utf-8").read()
        self.assertIn("failed_tests=tests.test_db,tests.test_widget", journal)
        self.assertIn("tests_failed=2", journal)
        # (a.3) The per-cycle triage capture log (FULL captured output).
        self.assertIn("triage_log", steps["c"])
        self.assertNotIn("_failure_capture", steps["c"])  # never leaks
        triage = os.path.join(self.kdir, steps["c"]["triage_log"])
        self.assertTrue(os.path.isfile(triage))
        tbody = open(triage, encoding="utf-8").read()
        self.assertIn("FAIL: tests.test_widget (m.WidgetCase.test_render)", tbody)
        self.assertIn("ERROR: tests.test_db (m.DbCase.test_commit)", tbody)

        # (b) The actionable report enumerates the duplicate pair + every marker.
        report = os.path.join(self.kdir, mod.DREAM_REPORT_REL)
        self.assertTrue(os.path.isfile(report))
        rbody = open(report, encoding="utf-8").read()
        # the duplicate pair (both ids + a jaccard) -- direction is corpus order.
        self.assertIn("ref-dup-alpha <-> ref-dup-beta", rbody)
        self.assertIn("[references] jaccard=", rbody)
        # every unpromoted marker, with worklog.md:line + kind + text.
        self.assertIn("work/260628-e2e/worklog.md:", rbody)
        self.assertIn("[learned] e2e dream observability proof marker alpha", rbody)
        self.assertIn(
            "[decision] e2e injected a failing audit step for determinism", rbody)
        self.assertEqual(steps["e"].get("duplicates"), 1)
        self.assertEqual(steps["e"].get("unpromoted_markers"), 2)

        # (c) The generated scheduler artifacts REDIRECT output (no /dev/null).
        plist = mod._dream_launchd_plist("00:00", "/x/agentware")
        self.assertIn("StandardOutPath", plist)
        self.assertIn("StandardErrorPath", plist)
        cron = mod._dream_cron_line("00:00", "/x/agentware")
        self.assertNotIn("/dev/null", cron)
        self.assertIn(">>", cron)

        # (d) dream_health reports last-run AGE + OUTCOME (partial: c failed, rest ok).
        hc = mod._audit_dream_health_check(self.kdir)
        self.assertIsNotNone(hc["last_run"])
        self.assertIsNotNone(hc["age_hours"])
        self.assertLess(hc["age_hours"], 1.0)
        self.assertEqual(hc["outcome"], "partial")
        self.assertFalse(hc["ok"])  # warns: last cycle did not finish clean

class DreamPlanOnlyDetectTests(unittest.TestCase, _GuardedEnv):
    """260710-learning-repair-p1 task 4: step e counts plan-only work dirs
    (plan.md, no worklog.md, plan older than DREAM_PLAN_ONLY_MIN_AGE_HOURS=48h).
    READ-ONLY; the report enumerates each. Abandoned plans can no longer vanish
    silently."""

    def setUp(self):
        self._save_env()
        self.addCleanup(self._restore_env)
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-planonly-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        run_cli(["index", "migrate-frontmatter"], self.kdir)
        _seed_gold(self.kdir)
        _migrate_kb(self.kdir)
        self._satisfy_health_gate(self.kdir)

    def _mk(self, feat, plan_age_h=None, worklog=False):
        d = os.path.join(self.kdir, "work", feat)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "plan.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# Plan - %s\n" % feat)
        if plan_age_h is not None:
            t = time.time() - plan_age_h * 3600
            os.utime(p, (t, t))
        if worklog:
            with open(os.path.join(d, "worklog.md"), "w", encoding="utf-8") as f:
                f.write("# wl\n\n- did a thing\n")

    def _run_e(self):
        return run_cli(
            ["dream", "--steps", "e", "--force", "--format", "json"], self.kdir)

    def test_counts_only_stale_plan_only_dirs(self):
        self._mk("260101-old-planonly", plan_age_h=72)               # counted
        self._mk("260101-fresh-planonly", plan_age_h=1)              # too new
        self._mk("260101-has-worklog", plan_age_h=72, worklog=True)  # has worklog
        code, out, err = self._run_e()
        self.assertEqual(code, 0, err)
        e = json.loads(out)["steps"][0]
        self.assertEqual(e["plan_only_dirs"], 1, out)
        body = open(os.path.join(self.kdir, self._mod.DREAM_REPORT_REL),
                    encoding="utf-8").read()
        self.assertIn("## Plan-only work dirs (1)", body)
        self.assertIn("work/260101-old-planonly/plan.md", body)
        self.assertNotIn("260101-fresh-planonly", body)
        self.assertNotIn("260101-has-worklog/plan.md", body)

    def test_step_e_stays_read_only_for_plan_only(self):
        self._mk("260101-old-planonly", plan_age_h=72)
        plan = os.path.join(self.kdir, "work", "260101-old-planonly", "plan.md")
        before = open(plan, encoding="utf-8").read()
        code, _out, err = self._run_e()
        self.assertEqual(code, 0, err)
        self.assertEqual(open(plan, encoding="utf-8").read(), before,
                         "step e must not touch the plan.md it flags")
        self.assertFalse(os.path.isfile(os.path.join(
            self.kdir, "work", "260101-old-planonly", "worklog.md")),
            "step e must not create a worklog for a plan-only dir")
