"""Hermetic tests for the `remediate` AUTO-FIX pass (task 4).

NEVER spawns a real Opus or a real gate: both the fixer subprocess seam
(`_review_invoke_agent`) and the gate-chain seam (`_review_run_gate_chain`) are
monkeypatched with stubs. Asserts:
  - only CONFIRMED-blocking findings are remediated (verdict==confirmed, high/med);
  - the fixer runs on the agentware-execution persona;
  - a fix WITHOUT a regression test is REFUSED (unverified fix -> unfixable);
  - the gate chain is re-run ONLY when every confirmed finding was fixed, and a
    gate-chain failure blocks all_fixed;
  - the regression tests the fixer names flow into the gate-chain call;
  - malformed / missing fixer payloads are reported, never crashed on;
  - the CLI dispatch (`main(['remediate', ...])`) wires exit codes.
Stdlib unittest only.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import types
import unittest
from contextlib import redirect_stdout

from tests._fixtures import load_cli


class RemediateTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self._orig_invoke = self.mod._review_invoke_agent
        self._orig_gate = self.mod._review_run_gate_chain
        self.cfg = {"model": "opus", "effort": "max",
                    "fanout": len(self.mod.REVIEW_DIMENSION_KEYS),
                    "max_rounds": 3}

    def tearDown(self):
        self.mod._review_invoke_agent = self._orig_invoke
        self.mod._review_run_gate_chain = self._orig_gate

    # --- helpers ------------------------------------------------------------
    def _confirmed_high(self, dim="correctness", summary="off-by-one"):
        return {"dimension": dim, "severity": "high", "file": "scripts/agentware",
                "line": 10, "summary": summary,
                "failure_scenario": "n=0 -> IndexError", "verdict": "confirmed"}

    def _install_fixer(self, fixes_payload):
        """Stub the fixer seam. Records calls; returns `fixes_payload` JSON for the
        'fixer' role. Any other role returns ''."""
        calls = []

        def fake(role, persona, prompt, cfg, timeout=None, cwd=None, **kwargs):
            calls.append({"role": role, "persona": persona, "prompt": prompt})
            if role == "fixer":
                return json.dumps(fixes_payload)
            return ""

        self.mod._review_invoke_agent = fake
        return calls

    def _install_gate(self, ok=True):
        gate_calls = []

        def fake_gate(regression_tests=None, cwd=None):
            gate_calls.append(list(regression_tests or []))
            return {"ok": ok, "steps": [
                {"name": "tests", "ok": ok, "code": 0 if ok else 1},
                {"name": "steering-lint", "ok": True, "code": 0},
                {"name": "gate-release", "ok": ok, "code": 0 if ok else 1}]}

        self.mod._review_run_gate_chain = fake_gate
        return gate_calls

    # --- happy path ---------------------------------------------------------
    def test_confirmed_finding_is_fixed_and_gate_rerun(self):
        calls = self._install_fixer({"fixes": [
            {"index": 0, "status": "fixed",
             "regression_test": "tests/unit/ops/test_remediate.py::test_x",
             "notes": "guarded empty input"}]})
        gate_calls = self._install_gate(ok=True)
        result = self.mod._review_run_remediation(
            [self._confirmed_high()], self.cfg, "DIFF", "CRITERIA",
            diff_range="HEAD~1..HEAD")
        self.assertEqual(result["requested"], 1)
        self.assertTrue(result["all_fixed"])
        self.assertEqual(result["fixes"][0]["status"], "fixed")
        self.assertEqual(result["new_diff_range"], "HEAD~1..HEAD")
        # fixer ran on the execution persona (fixer != reviewer)
        fixer_calls = [c for c in calls if c["role"] == "fixer"]
        self.assertEqual(len(fixer_calls), 1)
        self.assertEqual(fixer_calls[0]["persona"], "agentware-execution")
        # gate chain was re-run exactly once with the named regression test
        self.assertEqual(len(gate_calls), 1)
        self.assertIn("tests/unit/ops/test_remediate.py::test_x", gate_calls[0])

    # --- the core discipline: no regression test => refused -----------------
    def test_fix_without_regression_test_is_refused(self):
        self._install_fixer({"fixes": [
            {"index": 0, "status": "fixed", "regression_test": "",
             "notes": "trust me"}]})
        gate_calls = self._install_gate(ok=True)
        result = self.mod._review_run_remediation(
            [self._confirmed_high()], self.cfg)
        # unverified fix downgraded to unfixable; NOT applied; gate NOT run
        self.assertEqual(result["fixes"][0]["status"], "unfixable")
        self.assertFalse(result["all_fixed"])
        self.assertEqual(len(gate_calls), 0)
        self.assertIn("regression test", result["fixes"][0]["notes"].lower())

    def test_normalize_fix_refuses_fixed_without_test(self):
        fix = self.mod._review_normalize_fix(
            {"index": 0, "status": "fixed", "regression_test": "  "})
        self.assertEqual(fix["status"], "unfixable")

    def test_normalize_fix_accepts_fixed_with_test(self):
        fix = self.mod._review_normalize_fix(
            {"index": 1, "status": "FIXED",
             "regression_test": "tests.unit.ops.test_remediate::test_y"})
        self.assertEqual(fix["status"], "fixed")
        self.assertEqual(fix["index"], 1)

    # --- unfixable / deferred blocks the clean proceed ----------------------
    def test_unfixable_status_blocks_all_fixed(self):
        self._install_fixer({"fixes": [
            {"index": 0, "status": "unfixable", "notes": "needs redesign"}]})
        gate_calls = self._install_gate(ok=True)
        result = self.mod._review_run_remediation(
            [self._confirmed_high()], self.cfg)
        self.assertEqual(result["fixes"][0]["status"], "unfixable")
        self.assertFalse(result["all_fixed"])
        self.assertEqual(len(gate_calls), 0)

    # --- gate-chain failure blocks even a claimed fix -----------------------
    def test_gate_chain_failure_blocks(self):
        self._install_fixer({"fixes": [
            {"index": 0, "status": "fixed",
             "regression_test": "tests/unit/ops/test_remediate.py::test_x"}]})
        self._install_gate(ok=False)
        result = self.mod._review_run_remediation(
            [self._confirmed_high()], self.cfg)
        self.assertEqual(result["fixes"][0]["status"], "fixed")
        self.assertFalse(result["all_fixed"])
        self.assertFalse(result["gate_chain"]["ok"])

    # --- only confirmed-blocking findings are remediated --------------------
    def test_only_confirmed_blocking_are_remediated(self):
        calls = self._install_fixer({"fixes": [
            {"index": 0, "status": "fixed",
             "regression_test": "tests/unit/ops/test_remediate.py::test_x"}]})
        self._install_gate(ok=True)
        refuted_high = dict(self._confirmed_high(summary="refuted"),
                            verdict="refuted")
        confirmed_low = dict(self._confirmed_high(summary="low"), severity="low")
        result = self.mod._review_run_remediation(
            [self._confirmed_high(), refuted_high, confirmed_low], self.cfg)
        self.assertEqual(result["requested"], 1)
        # the fixer only saw the single confirmed-high finding
        fixer_prompt = [c for c in calls if c["role"] == "fixer"][0]["prompt"]
        self.assertIn("off-by-one", fixer_prompt)
        self.assertNotIn("refuted", fixer_prompt)

    def test_no_confirmed_findings_is_noop(self):
        calls = self._install_fixer({"fixes": []})
        gate_calls = self._install_gate(ok=True)
        result = self.mod._review_run_remediation(
            [dict(self._confirmed_high(), verdict="refuted")], self.cfg)
        self.assertEqual(result["requested"], 0)
        self.assertTrue(result["all_fixed"])
        self.assertEqual(result["fixes"], [])
        # no fixer, no gate chain when there is nothing to remediate
        self.assertEqual([c for c in calls if c["role"] == "fixer"], [])
        self.assertEqual(len(gate_calls), 0)

    # --- robustness (novel-input hardening) ---------------------------------
    def test_malformed_fixer_payload_is_not_crashed(self):
        def fake(role, persona, prompt, cfg, timeout=None, cwd=None, **kwargs):
            return "sorry, no JSON here" if role == "fixer" else ""
        self.mod._review_invoke_agent = fake
        gate_calls = self._install_gate(ok=True)
        result = self.mod._review_run_remediation(
            [self._confirmed_high()], self.cfg)
        self.assertEqual(result["fixes"][0]["status"], "unfixable")
        self.assertFalse(result["all_fixed"])
        self.assertEqual(len(gate_calls), 0)

    def test_missing_fix_entry_for_a_finding_is_unfixable(self):
        # fixer reports a fix for finding 0 but not for finding 1
        self._install_fixer({"fixes": [
            {"index": 0, "status": "fixed",
             "regression_test": "tests/unit/ops/test_remediate.py::test_x"}]})
        self._install_gate(ok=True)
        result = self.mod._review_run_remediation(
            [self._confirmed_high("correctness"),
             self._confirmed_high("security-governance", summary="authz gap")],
            self.cfg)
        self.assertEqual(result["requested"], 2)
        self.assertEqual(result["fixes"][0]["status"], "fixed")
        self.assertEqual(result["fixes"][1]["status"], "unfixable")
        self.assertFalse(result["all_fixed"])

    # --- fixer prompt fences finding text (R-SEC-02) ------------------------
    def test_fixer_prompt_fences_finding_text(self):
        prompt = self.mod._review_build_fixer_prompt(
            [self._confirmed_high()], "DIFFBODY", "CRIT", self.cfg)
        self.assertIn("CONFIRMED_FINDINGS", prompt)
        self.assertIn("untrusted data", prompt)
        self.assertIn("DIFFBODY", prompt)

    # --- gate-chain helper: test-ref normalization --------------------------
    def test_test_module_normalization(self):
        self.assertEqual(
            self.mod._review_test_module("tests/unit/ops/test_remediate.py::test_x"),
            "tests.unit.ops.test_remediate")
        self.assertEqual(
            self.mod._review_test_module("tests.unit.ops.test_remediate::test_y"),
            "tests.unit.ops.test_remediate")
        self.assertEqual(self.mod._review_test_module("  "), "")
        self.assertEqual(self.mod._review_test_module(None), "")

    def test_gate_chain_deduplicates_modules_and_runs_lint_and_release(self):
        seen = []
        self.mod._review_subprocess_rc = lambda cmd, cwd=None, timeout=None: (
            seen.append(cmd) or 0)
        try:
            res = self.mod._review_run_gate_chain(
                ["tests/unit/ops/test_remediate.py::a", "tests/unit/ops/test_remediate.py::b"])
        finally:
            # restore is via module reload cache; explicit del of override
            del self.mod._review_subprocess_rc
        self.assertTrue(res["ok"])
        names = [s["name"] for s in res["steps"]]
        self.assertEqual(names, ["tests", "steering-lint", "gate-release"])
        # two tests in one module -> a single deduped unittest invocation
        unittest_cmds = [c for c in seen if "unittest" in c]
        self.assertEqual(len(unittest_cmds), 1)
        self.assertEqual(
            [x for x in unittest_cmds[0] if x.startswith("tests.")],
            ["tests.unit.ops.test_remediate"])

    # --- CLI dispatch (argparse wiring) -------------------------------------
    def test_cli_returns_zero_when_all_fixed(self):
        self._install_fixer({"fixes": [
            {"index": 0, "status": "fixed",
             "regression_test": "tests/unit/ops/test_remediate.py::test_x"}]})
        self._install_gate(ok=True)
        with tempfile.TemporaryDirectory() as d:
            fpath = os.path.join(d, "findings.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump({"findings": [self._confirmed_high()]}, f)
            args = types.SimpleNamespace(
                findings_file=fpath, diff_range=None, diff_file=None,
                acceptance_file=None, no_gate_chain=False, format="json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = self.mod.cmd_remediate(args)
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["all_fixed"])

    def test_cli_returns_nonzero_on_unfixable(self):
        self._install_fixer({"fixes": [
            {"index": 0, "status": "unfixable", "notes": "hard"}]})
        self._install_gate(ok=True)
        with tempfile.TemporaryDirectory() as d:
            fpath = os.path.join(d, "findings.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump({"findings": [self._confirmed_high()]}, f)
            args = types.SimpleNamespace(
                findings_file=fpath, diff_range=None, diff_file=None,
                acceptance_file=None, no_gate_chain=False, format="json")
            with redirect_stdout(io.StringIO()):
                code = self.mod.cmd_remediate(args)
        self.assertEqual(code, 1)

    def test_main_dispatch_wires_remediate_subcommand(self):
        self._install_fixer({"fixes": []})
        self._install_gate(ok=True)
        with tempfile.TemporaryDirectory() as d:
            fpath = os.path.join(d, "findings.json")
            with open(fpath, "w", encoding="utf-8") as f:
                # a refuted finding -> nothing to remediate -> all_fixed -> 0
                json.dump({"findings": [
                    dict(self._confirmed_high(), verdict="refuted")]}, f)
            with redirect_stdout(io.StringIO()):
                code = self.mod.main(
                    ["remediate", "--findings-file", fpath, "--format", "json"])
        self.assertEqual(code, 0)

    def test_load_findings_accepts_bare_array(self):
        with tempfile.TemporaryDirectory() as d:
            fpath = os.path.join(d, "findings.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump([self._confirmed_high()], f)
            args = types.SimpleNamespace(findings_file=fpath)
            findings = self.mod._review_load_findings(args)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["verdict"], "confirmed")

    # --- latin-1 / non-UTF-8 input tolerance (remediation round 7) ----------
    # test-quality: the tolerant errors='replace' decode was regression-locked
    # only at cmd_review --diff-file and the _git seam; the PARALLEL sites on
    # the remediation pass — cmd_remediate --diff-file / --acceptance-file and
    # _review_load_findings --findings-file — had no test, so reverting any of
    # them to strict UTF-8 shipped green and then CRASHED the self-heal loop's
    # remediation pass (UnicodeDecodeError, exit 1 with a traceback the bash
    # gate misreads) over the SAME latin-1-carrying diff the audit survived.

    def test_remediate_latin1_diff_and_acceptance_files_never_crash(self):
        self._install_fixer({"fixes": [
            {"index": 0, "status": "fixed",
             "regression_test": "tests/unit/ops/test_remediate.py::test_x",
             "notes": "closed"}]})
        self._install_gate(ok=True)
        with tempfile.TemporaryDirectory() as d:
            fpath = os.path.join(d, "findings.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump({"findings": [self._confirmed_high()]}, f)
            dpath = os.path.join(d, "diff.patch")
            with open(dpath, "wb") as f:
                # The exact byte shape the audit tolerates (latin-1 0xE9/0xFF,
                # no NUL): the remediation pass over the SAME diff must not
                # crash with a strict decode.
                f.write(b"diff --git a/x b/x\n+caf\xe9 after \xff\n")
            apath = os.path.join(d, "criteria.md")
            with open(apath, "wb") as f:
                f.write(b"# Crit\xe8res\n- caf\xe9 must survive \xff\n")
            args = types.SimpleNamespace(
                findings_file=fpath, diff_range=None, diff_file=dpath,
                acceptance_file=apath, no_gate_chain=False, format="json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = self.mod.cmd_remediate(args)
        self.assertEqual(code, 0,
                         "latin-1 bytes in --diff-file/--acceptance-file must "
                         "decode tolerantly, never crash the remediation pass")
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["all_fixed"])

    def test_load_findings_tolerates_latin1_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            fpath = os.path.join(d, "findings.json")
            with open(fpath, "wb") as f:
                # A findings file that picked up a raw latin-1 byte inside a
                # string value (e.g. a summary quoting the diff under audit):
                # errors='replace' degrades it to U+FFFD and the JSON still
                # parses; a strict decode raises UnicodeDecodeError instead.
                f.write(b'{"findings": [{"dimension": "correctness", '
                        b'"severity": "high", "verdict": "confirmed", '
                        b'"file": "x", "line": 1, "summary": "caf\xe9", '
                        b'"failure_scenario": "y"}]}')
            args = types.SimpleNamespace(findings_file=fpath)
            findings = self.mod._review_load_findings(args)
        self.assertEqual(len(findings), 1,
                         "a latin-1 byte must not crash --findings-file "
                         "loading")
        self.assertEqual(findings[0]["summary"], "caf�")

    # --- unresolvable --diff-range degrade branch (remediation round 8) -----
    # test-quality: the None -> '' coercion + the 'proceeding with an empty
    # diff context' LOUD warning in cmd_remediate had zero regression lock —
    # deleting the guard (e.g. a refactor consolidating the two
    # _review_resolve_diff call sites with cmd_review's fail-closed one) left
    # the suite 100% green while the fixer silently remediated with a None
    # diff and NO stderr trace — the exact silent-degradation shape the
    # round-7 hardening forbids.

    def test_unresolvable_diff_range_degrades_loudly_with_empty_diff(self):
        calls = self._install_fixer({"fixes": [
            {"index": 0, "status": "fixed",
             "regression_test": "tests/unit/ops/test_remediate.py::test_x",
             "notes": "closed"}]})
        self._install_gate(ok=True)
        tmp = tempfile.mkdtemp(prefix="aw-remdr-")
        self.addCleanup(shutil.rmtree, tmp, True)
        # Pin REPO_ROOT to a NON-git dir so `git -C <root> diff <range>` FAILS
        # (rc != 0 -> _review_resolve_diff returns None) — the gc'd-base-ref /
        # shallow-clone shape, hermetic (never the operator's checkout).
        orig_root = self.mod.REPO_ROOT
        self.mod.REPO_ROOT = tmp
        try:
            fpath = os.path.join(tmp, "findings.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump({"findings": [self._confirmed_high()]}, f)
            args = types.SimpleNamespace(
                findings_file=fpath, diff_range="deadbeef123456..HEAD",
                diff_file=None, acceptance_file=None, no_gate_chain=False,
                format="json")
            buf_out, buf_err = io.StringIO(), io.StringIO()
            with redirect_stdout(buf_out), \
                    contextlib.redirect_stderr(buf_err):
                code = self.mod.cmd_remediate(args)
        finally:
            self.mod.REPO_ROOT = orig_root
        # Remediate is NOT the gate: it DEGRADES (rc by fix outcome, not 3)...
        self.assertEqual(code, 0, buf_err.getvalue())
        payload = json.loads(buf_out.getvalue())
        self.assertTrue(payload["all_fixed"])
        # ...but LOUDLY, never silently: the degradation names the range.
        err = buf_err.getvalue()
        self.assertIn("cannot resolve --diff-range", err)
        self.assertIn("deadbeef123456..HEAD", err)
        self.assertIn("proceeding with an empty diff context", err)
        # The fixer prompt got the explicit EMPTY diff fence — None never
        # leaked into _review_fence / the prompt builder.
        fixer_prompts = [c["prompt"] for c in calls if c["role"] == "fixer"]
        self.assertEqual(len(fixer_prompts), 1)
        self.assertIn("<<<DIFF", fixer_prompts[0])
        self.assertIn("(empty)", fixer_prompts[0])


if __name__ == "__main__":
    unittest.main()
