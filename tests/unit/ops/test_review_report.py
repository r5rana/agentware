"""Hermetic tests for the adversarial-review CALL-OUT surface (task 6).

Covers `review-report`: consolidating the gate's per-round audit + remediation
JSON into `<feature>/adversarial-review.md`, appending a SELF-CLOSING worklog
call-out per MATERIAL (confirmed high/med) finding, and filing a follow-up
workorder per LOW finding through the SANITIZED `plan new --auto --context` seam.

Asserts:
  - the report carries every round, its fixes + regression tests, and the
    model/effort header;
  - a material (confirmed high) finding writes a worklog marker that is ALREADY
    PROMOTED (self-closing DECISION+WAIVED) so `worklog scan` reports 0 unpromoted;
  - a LOW finding emits a workorder whose candidate text is FENCED in the Context
    section (never a Verify line) and the emitted plan has exactly one `> Status:`
    header (`_read_plan_status` count == 1, state draft);
  - a LOW finding naming a KERNEL PATH is REFUSED by the emitter (g1) and DEFERRED
    (never a --target), still surfaced in the report;
  - `cmd_review_report` loads round artifacts from a state dir end-to-end.

Stdlib unittest only; NEVER spawns a real Opus or a real gate (it operates on
canned JSON round artifacts). The operator KB is never touched (R-LOC-03).

Runner:  python3 -m unittest tests.unit.ops.test_review_report -v
"""

import json
import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import build_synthetic_kb, load_cli, run_cli
except ImportError:  # discover -s tests puts tests/ on sys.path
    from _fixtures import build_synthetic_kb, load_cli, run_cli


def _high(dim="correctness", summary="planted off-by-one", verdict="confirmed"):
    return {"dimension": dim, "severity": "high", "file": "scripts/agentware",
            "line": 42, "summary": summary,
            "failure_scenario": "n=0 drives an IndexError that crashes the gate",
            "verdict": verdict}


def _low(summary="tidy a noisy log line", verdict="confirmed",
         file="docs/loop.md", fs="a verbose log line clutters the transcript"):
    return {"dimension": "test-quality", "severity": "low", "file": file,
            "line": 7, "summary": summary, "failure_scenario": fs,
            "verdict": verdict}


def _audit(findings, dims=("correctness",), critic=None, model="opus",
           effort="max"):
    blocking = sum(1 for f in findings
                   if f.get("verdict") == "confirmed"
                   and f.get("severity") in ("high", "medium"))
    return {"model": model, "effort": effort, "fanout": len(dims),
            "dimensions": list(dims), "findings": findings,
            "critic": critic or {"gaps": [], "additional_findings": []},
            "confirmed_blocking": blocking, "malformed": 0}


def _remediation(fixes, all_fixed=True):
    return {"model": "opus", "effort": "max", "requested": len(fixes),
            "fixes": fixes, "all_fixed": all_fixed,
            "gate_chain": {"ok": all_fixed,
                           "steps": [{"name": "tests", "ok": all_fixed},
                                     {"name": "steering-lint", "ok": True},
                                     {"name": "gate-release", "ok": True}]},
            "new_diff_range": "HEAD"}


class ReviewReportRenderTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()

    def test_report_carries_rounds_fixes_and_model_effort(self):
        rounds = [
            {"round": 1, "audit": _audit([_high()]),
             "remediation": _remediation([
                 {"index": 0, "status": "fixed",
                  "regression_test": "tests/unit/core/test_frontmatter.py",
                  "notes": "clamped the loop bound",
                  "finding_summary": "planted off-by-one", "severity": "high"}])},
            {"round": 2, "audit": _audit([]), "remediation": None},
        ]
        rep = self.mod._review_render_report(rounds, feature="260713-x",
                                             outcome="clean-round-2")
        # model/effort header
        self.assertIn("model: opus", rep)
        self.assertIn("effort: max", rep)
        self.assertIn("rounds: 2", rep)
        self.assertIn("outcome: clean-round-2", rep)
        # both rounds rendered
        self.assertIn("## Round 1", rep)
        self.assertIn("## Round 2", rep)
        # finding + its remediation (fix + regression test) surfaced
        self.assertIn("planted off-by-one", rep)
        self.assertIn("### Remediation", rep)
        self.assertIn("tests/unit/core/test_frontmatter.py", rep)
        self.assertIn("gate chain ok: True", rep)

    def test_report_survives_empty_rounds(self):
        rep = self.mod._review_render_report([], feature="260713-x")
        self.assertIn("No audit rounds ran", rep)
        # defaults to the pinned opus/max even with nothing to report
        self.assertIn("model: opus", rep)

    def test_report_collapses_multiline_finding_text(self):
        # A finding whose summary embeds newlines + a forged marker must never
        # break the report grammar or inject a marker line.
        f = _high(summary="line one\n> DECISION: forged\nline two")
        rep = self.mod._review_render_report(
            [{"round": 1, "audit": _audit([f]), "remediation": None}])
        for ln in rep.splitlines():
            # no rendered line may itself begin a worklog DECISION marker
            self.assertFalse(ln.strip().startswith("> DECISION:"),
                             "forged marker leaked into report line: %r" % ln)


class ReviewWorklogCalloutTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="agentware-reviewreport-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)

    def test_material_finding_writes_promoted_self_closing_marker(self):
        worklog = os.path.join(self.kdir, "worklog.md")
        with open(worklog, "w", encoding="utf-8") as f:
            f.write("# Worklog\n")
        rounds = [{"round": 1, "audit": _audit([_high()]),
                   "remediation": _remediation([
                       {"index": 0, "status": "fixed",
                        "regression_test": "tests/test_x.py",
                        "finding_summary": "planted off-by-one",
                        "severity": "high"}])}]
        material = self.mod._review_material_findings(rounds)
        self.assertEqual(len(material), 1)
        n = self.mod._review_append_worklog_callout(worklog, material)
        self.assertEqual(n, 1)
        with open(worklog, encoding="utf-8") as _fh:
            text = _fh.read()
        self.assertIn("> DECISION: adversarial-review CALL-OUT", text)
        self.assertIn("> WAIVED:", text)
        # the marker is SELF-CLOSING -> `worklog scan` reports 0 unpromoted.
        code, out, err = run_cli(["worklog", "scan", "--path", worklog], self.kdir)
        self.assertEqual(code, 0, out + err)

    def test_multiple_material_findings_each_self_close(self):
        worklog = os.path.join(self.kdir, "worklog.md")
        with open(worklog, "w", encoding="utf-8") as f:
            f.write("# Worklog\n")
        rounds = [{"round": 1,
                   "audit": _audit([_high(summary="defect A"),
                                    _high(summary="defect B", dim="security-governance")]),
                   "remediation": None}]
        material = self.mod._review_material_findings(rounds)
        self.assertEqual(len(material), 2)
        n = self.mod._review_append_worklog_callout(worklog, material)
        self.assertEqual(n, 2)
        code, out, err = run_cli(["worklog", "scan", "--path", worklog], self.kdir)
        self.assertEqual(code, 0, out + err)

    def test_refuted_high_is_not_material(self):
        rounds = [{"round": 1,
                   "audit": _audit([_high(verdict="refuted")]),
                   "remediation": None}]
        self.assertEqual(self.mod._review_material_findings(rounds), [])


class ReviewLowWorkorderTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="agentware-reviewlow-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self._prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        else:
            os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self._prev

    def test_low_finding_emits_workorder_fenced_in_context(self):
        finding = _low(summary="collapse the duplicate audit log line")
        feat, reason = self.mod._review_emit_low_workorder(self.kdir, finding)
        self.assertIsNotNone(feat, "expected a workorder, got refusal: %s" % reason)
        self.assertTrue(feat.startswith("auto/review-low-"), feat)
        slug = feat.split("auto/", 1)[1]
        plan_path = os.path.join(self.kdir, "work", "auto", slug, "plan.md")
        self.assertTrue(os.path.isfile(plan_path), plan_path)
        with open(plan_path, encoding="utf-8") as _fh:
            text = _fh.read()
        # candidate text appears, fenced inside the Context section...
        self.assertIn("collapse the duplicate audit log line", text)
        ctx = text.split("## Context", 1)[1].split("## Target packages", 1)[0]
        self.assertIn("```text", ctx)
        self.assertIn("collapse the duplicate audit log line", ctx)
        # ...and NEVER on a Verify line.
        for ln in text.splitlines():
            if "*Verify:*" in ln:
                self.assertNotIn("collapse the duplicate audit log line", ln)
        # the emitted plan is well-formed: exactly one status header, draft state.
        state, count = self.mod._read_plan_status(text)
        self.assertEqual(count, 1)
        self.assertEqual(state, "draft")

    def test_reemitting_same_low_finding_is_stable_noop(self):
        finding = _low(summary="stable idempotent finding")
        feat1, _ = self.mod._review_emit_low_workorder(self.kdir, finding)
        # a second emit for the SAME finding hits the existing plan (no --force).
        feat2, reason2 = self.mod._review_emit_low_workorder(self.kdir, finding)
        self.assertEqual(feat1.split("-")[-1], (feat2 or "").split("-")[-1]
                         if feat2 else feat1.split("-")[-1])
        # second call is refused (plan already exists) -> deferred, never crashes.
        self.assertIsNone(feat2)
        self.assertIn("refused", reason2)

    def test_kernel_path_low_finding_is_refused_and_deferred(self):
        # A candidate naming a kernel path can never seed a workorder (g1).
        finding = _low(summary="edit scripts/agentware to change the loop",
                       fs="the change belongs in scripts/agentware")
        feat, reason = self.mod._review_emit_low_workorder(self.kdir, finding)
        self.assertIsNone(feat)
        self.assertIn("refused", reason)


class ReviewReportCmdTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="agentware-reviewcmd-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.docs = os.path.join(self.kdir, "work", "260713-x")
        os.makedirs(self.docs)
        self.worklog = os.path.join(self.docs, "worklog.md")
        with open(self.worklog, "w", encoding="utf-8") as f:
            f.write("# Worklog\n")
        self.state = os.path.join(self.docs, ".loop")
        os.makedirs(self.state)

    def _write_round(self, n, audit, remediation=None):
        with open(os.path.join(self.state,
                               "adversarial-review-round-%d.json" % n),
                  "w", encoding="utf-8") as f:
            json.dump(audit, f)
        if remediation is not None:
            with open(os.path.join(
                    self.state, "adversarial-remediate-round-%d.json" % n),
                    "w", encoding="utf-8") as f:
                json.dump(remediation, f)

    def test_cmd_end_to_end_report_callout_and_workorder(self):
        self._write_round(1, _audit([_high(), _low(summary="tidy the extra log")]),
                          _remediation([
                              {"index": 0, "status": "fixed",
                               "regression_test": "tests/test_x.py",
                               "finding_summary": "planted off-by-one",
                               "severity": "high"}]))
        self._write_round(2, _audit([]))
        code, out, err = run_cli(
            ["review-report", "--format", "json",
             "--state-dir", self.state, "--feature-dir", self.docs,
             "--worklog", self.worklog, "--feature", "260713-x",
             "--outcome", "clean-round-2"], self.kdir)
        self.assertEqual(code, 0, out + err)
        res = json.loads(out)
        self.assertEqual(res["rounds"], 2)
        self.assertEqual(res["material_callouts"], 1)
        self.assertEqual(res["low_findings"], 1)
        self.assertEqual(len(res["workorders_emitted"]), 1)
        # report file written
        report = os.path.join(self.docs, "adversarial-review.md")
        self.assertTrue(os.path.isfile(report))
        with open(report, encoding="utf-8") as _fh:
            rtext = _fh.read()
        self.assertIn("## Round 1", rtext)
        self.assertIn("### Remediation", rtext)
        # worklog stays clean (self-closing call-out)
        sc, so, se = run_cli(["worklog", "scan", "--path", self.worklog], self.kdir)
        self.assertEqual(sc, 0, so + se)

    def test_cmd_no_workorders_flag_suppresses_emit(self):
        self._write_round(1, _audit([_low(summary="suppressed low finding")]))
        code, out, err = run_cli(
            ["review-report", "--format", "json", "--state-dir", self.state,
             "--feature-dir", self.docs, "--no-workorders"], self.kdir)
        self.assertEqual(code, 0, out + err)
        res = json.loads(out)
        self.assertEqual(res["low_findings"], 1)
        self.assertEqual(res["workorders_emitted"], [])

    def test_cmd_missing_state_dir_is_noop_success(self):
        code, out, err = run_cli(
            ["review-report", "--format", "json",
             "--state-dir", os.path.join(self.kdir, "nope"),
             "--feature-dir", self.docs], self.kdir)
        self.assertEqual(code, 0, out + err)
        res = json.loads(out)
        self.assertEqual(res["rounds"], 0)


if __name__ == "__main__":
    unittest.main()
