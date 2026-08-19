"""VerifyCriticSoftFailTests, SeverityEnumEnforcementTests, NonObjectFindingItemsTests -- split from `test_review_orchestrator` (task 58).

See `tests.unit.ops._review_orchestrator_support` for the
shared header.
"""

from tests.unit.ops._review_orchestrator_support import (  # noqa: F401
    _reviewer_out, argparse, contextlib, io, json, load_cli,
    pinned_review_env, unittest)


class VerifyCriticSoftFailTests(unittest.TestCase):
    """Remediation round 4 (correctness + test-quality) — the VERIFY and CRITIC
    seams get the same soft-fail treatment the reviewer seam already has:

      * an exit-0 HTTP-200 error envelope from a VERIFY agent ({"error": ...}
        — parses as a valid JSON dict but carries NO verdict) must fail CLOSED
        exactly like an unreachable verifier (verify_unavailable ->
        audit_incomplete -> cmd_review exit 3), never silently downgrade a
        high/medium finding to 'uncertain-and-pass' (the fail-OPEN hole);
      * every soft-failed verify/critic agent is NAMED on the review stream
        with its raw-output snippet — never only the vague aggregate
        INCOMPLETE banner."""

    ENVELOPE = '{"error": "rate limited"}'

    def setUp(self):
        self.mod = load_cli()
        self._orig_invoke = self.mod._review_invoke_agent
        self.cfg = {"model": "opus", "effort": "max",
                    "fanout": len(self.mod.REVIEW_DIMENSION_KEYS),
                    "max_rounds": 3}

    def tearDown(self):
        self.mod._review_invoke_agent = self._orig_invoke

    def _high_finding(self, severity="high"):
        return {"dimension": "correctness", "severity": severity,
                "file": "scripts/agentware", "line": 10,
                "summary": "off-by-one on empty input",
                "failure_scenario": "n=0 -> IndexError"}

    def _install(self, verify_out, critic_out=None, severity="high"):
        """Stub the seam: reviewer:correctness emits ONE finding of `severity`,
        other reviewers are clean, verify returns `verify_out`, critic returns
        `critic_out` (default clean)."""
        finding = self._high_finding(severity)
        critic_payload = critic_out if critic_out is not None \
            else json.dumps({"gaps": [], "additional_findings": []})

        def fake(role, persona, prompt, cfg, timeout=None, cwd=None, **kw):
            if role == "reviewer:correctness":
                return _reviewer_out("correctness", [finding])
            if role.startswith("reviewer:"):
                return _reviewer_out(role.split(":", 1)[1], [])
            if role == "verify":
                return verify_out
            return critic_payload

        self.mod._review_invoke_agent = fake

    def _audit(self):
        stream = io.StringIO()
        result = self.mod._review_run_audit("DIFF", "CRITERIA", self.cfg,
                                            stream=stream)
        return result, stream.getvalue()

    # --- the fail-OPEN hole: exit-0 error envelope at the VERIFY seam -------

    def test_exit0_error_envelope_at_verify_seam_fails_closed(self):
        self._install(verify_out=self.ENVELOPE)
        result, diag = self._audit()
        self.assertEqual(result["verify_unavailable"], 1,
                         "an envelope with no verdict is a verifier that did "
                         "NOT run — it must count unavailable, not fail open")
        self.assertTrue(result["audit_incomplete"])
        self.assertEqual(result["findings"][0]["verdict"], "uncertain")
        self.assertEqual(result["confirmed_blocking"], 0)
        # And it is NAMED with its snippet on the stream.
        self.assertIn("verify:correctness", diag)
        self.assertIn("rate limited", diag)

    def test_cmd_review_exits_3_on_verify_error_envelope(self):
        # The full gate: the exact shape that used to ship a green gate (rc 0)
        # over an UN-verified high finding must now BLOCK with exit 3.
        self._install(verify_out=self.ENVELOPE)
        args = argparse.Namespace(
            adversarial=True, worklog=None, diff_file=None, diff_range=None,
            acceptance_file=None, format="json")
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with pinned_review_env(), \
                contextlib.redirect_stdout(buf_out), \
                contextlib.redirect_stderr(buf_err):
            rc = self.mod.cmd_review(args)
        err = buf_err.getvalue()
        self.assertEqual(rc, 3, err)
        self.assertIn("INCOMPLETE", err)
        self.assertIn("verify:correctness", err)
        self.assertIn("rate limited", err)

    def test_verify_invalid_verdict_value_counts_unavailable(self):
        # {"verdict": "banana"} is equally unusable — no member of
        # REVIEW_VERDICTS was independently produced.
        self._install(verify_out=json.dumps({"verdict": "banana"}))
        result, diag = self._audit()
        self.assertEqual(result["verify_unavailable"], 1)
        self.assertTrue(result["audit_incomplete"])
        self.assertIn("verify:correctness", diag)

    def test_low_severity_envelope_is_named_but_does_not_block(self):
        # Scope guard: only BLOCKING severities gate the audit; a low-severity
        # finding with an unusable verdict degrades to uncertain (named, open).
        self._install(verify_out=self.ENVELOPE, severity="low")
        result, diag = self._audit()
        self.assertEqual(result["verify_unavailable"], 0)
        self.assertFalse(result["audit_incomplete"])
        self.assertEqual(result["findings"][0]["verdict"], "uncertain")
        self.assertIn("verify:correctness", diag)

    # --- 'medium' — the OTHER member of REVIEW_BLOCKING_SEVERITIES ----------
    # Remediation round 7 (test-quality): the fail-closed accounting was
    # locked only at 'high' (blocks) and 'low' (does not) — a regression
    # narrowing the blocking check to high-only (`== "high"`, or
    # REVIEW_BLOCKING_SEVERITIES losing 'medium') shipped a fail-OPEN hole
    # with the whole suite green: a medium finding whose verifier soft-failed
    # with the exit-0 error envelope silently degraded to uncertain-and-PASS.

    def test_medium_severity_envelope_fails_closed(self):
        self._install(verify_out=self.ENVELOPE, severity="medium")
        result, diag = self._audit()
        self.assertEqual(result["verify_unavailable"], 1,
                         "'medium' is a BLOCKING severity — an unverifiable "
                         "medium finding must count unavailable, not fail "
                         "open to uncertain-and-pass")
        self.assertTrue(result["audit_incomplete"])
        self.assertEqual(result["findings"][0]["verdict"], "uncertain")
        self.assertEqual(result["confirmed_blocking"], 0)
        self.assertIn("verify:correctness", diag)

    def test_cmd_review_exits_3_on_medium_verify_error_envelope(self):
        # The full gate at 'medium': the exact mutation shape (blocking check
        # narrowed to high-only) used to exit 0 here — GREEN over an
        # UN-verified medium finding.
        self._install(verify_out=self.ENVELOPE, severity="medium")
        args = argparse.Namespace(
            adversarial=True, worklog=None, diff_file=None, diff_range=None,
            acceptance_file=None, format="json")
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with pinned_review_env(), \
                contextlib.redirect_stdout(buf_out), \
                contextlib.redirect_stderr(buf_err):
            rc = self.mod.cmd_review(args)
        err = buf_err.getvalue()
        self.assertEqual(rc, 3, err)
        self.assertIn("INCOMPLETE", err)
        self.assertIn("verify:correctness", err)

    def test_confirmed_medium_finding_blocks(self):
        # The companion check on the same constant: a CONFIRMED medium
        # finding must count confirmed_blocking (`_review_is_blocking`).
        self._install(verify_out=json.dumps(
            {"verdict": "confirmed", "verdict_rationale": "reproduced"}),
            severity="medium")
        result, _diag = self._audit()
        self.assertEqual(result["confirmed_blocking"], 1,
                         "a confirmed 'medium' finding must BLOCK the gate")
        self.assertEqual(result["verify_unavailable"], 0)

    def test_blocking_severities_cover_high_and_medium(self):
        # Constant lock: both checks key on REVIEW_BLOCKING_SEVERITIES —
        # dropping 'medium' from it is the one-line fail-open mutation.
        self.assertEqual(tuple(self.mod.REVIEW_BLOCKING_SEVERITIES),
                         ("high", "medium"))
        self.assertTrue(self.mod._review_is_blocking(
            {"severity": "medium", "verdict": "confirmed"}))
        self.assertFalse(self.mod._review_is_blocking(
            {"severity": "low", "verdict": "confirmed"}))

    def test_valid_refuted_verdict_is_untouched_and_silent(self):
        # Guard against over-blocking: a genuine verdict keeps the pre-fix
        # behavior — no unavailable count, no soft-fail line for the verifier.
        self._install(verify_out=json.dumps(
            {"verdict": "refuted", "verdict_rationale": "not reproducible"}))
        result, diag = self._audit()
        self.assertEqual(result["verify_unavailable"], 0)
        self.assertFalse(result["audit_incomplete"])
        self.assertEqual(result["findings"][0]["verdict"], "refuted")
        self.assertNotIn("verify:correctness", diag)

    # --- verify/critic exit-0 prose soft-fails are NAMED --------------------

    def test_exit0_prose_verify_soft_fail_is_named_on_stream(self):
        # Fail-closed accounting already held for prose (extract -> None); the
        # regression is the DIAGNOSTIC: the blocked gate used to carry ONLY
        # the vague aggregate banner with no per-agent trace.
        self._install(verify_out="sorry, I could not comply")
        result, diag = self._audit()
        self.assertEqual(result["verify_unavailable"], 1)
        self.assertTrue(result["audit_incomplete"])
        self.assertIn("verify:correctness", diag)
        self.assertIn("sorry, I could not comply", diag)

    def test_exit0_prose_critic_soft_fail_is_named_on_stream(self):
        self._install(verify_out=json.dumps({"verdict": "refuted"}),
                      critic_out="sorry, no critic JSON today")
        result, diag = self._audit()
        self.assertIn("critic", diag)
        self.assertIn("sorry, no critic JSON today", diag)
        # Report-only degradation unchanged: the critic never gates the audit.
        self.assertFalse(result["audit_incomplete"])

    def test_clean_critic_payload_emits_no_soft_fail(self):
        self._install(verify_out=json.dumps({"verdict": "refuted"}))
        _result, diag = self._audit()
        self.assertNotIn("critic", diag)

class SeverityEnumEnforcementTests(unittest.TestCase):
    """Remediation round 6 (correctness) — `severity` is the reviewer-output
    field that GATES blocking (`REVIEW_BLOCKING_SEVERITIES`) and the
    verify-unavailable fail-closed accounting, yet it was kept VERBATIM when
    off the enum: a reviewer emitting {"severity": "Critical"} (the exact
    persona-drift shape this plan's history documents twice) sailed past BOTH
    checks — a CONFIRMED defect shipped the gate GREEN, and a soft-failed
    verifier on such a finding no longer failed the audit closed. Enforced
    now at normalize time, fail-CLOSED: unknown severity -> 'high' + schema
    error (mirroring the dimension-enum enforcement in the same function)."""

    def setUp(self):
        self.mod = load_cli()
        self._orig_invoke = self.mod._review_invoke_agent
        self.cfg = {"model": "opus", "effort": "max",
                    "fanout": len(self.mod.REVIEW_DIMENSION_KEYS),
                    "max_rounds": 3}

    def tearDown(self):
        self.mod._review_invoke_agent = self._orig_invoke

    def _finding(self, severity):
        return {"dimension": "correctness", "severity": severity,
                "file": "scripts/agentware", "line": 10,
                "summary": "planted", "failure_scenario": "crash"}

    def _install(self, severity, verify_out):
        finding = self._finding(severity)

        def fake(role, persona, prompt, cfg, timeout=None, cwd=None, **kw):
            if role == "reviewer:correctness":
                return _reviewer_out("correctness", [finding])
            if role.startswith("reviewer:"):
                return _reviewer_out(role.split(":", 1)[1], [])
            if role == "verify":
                return verify_out
            return json.dumps({"gaps": [], "additional_findings": []})

        self.mod._review_invoke_agent = fake

    def test_normalize_enforces_severity_enum(self):
        # Off-enum token -> coerced to 'high' (fail-closed) + schema error.
        finding, errs = self.mod._review_normalize_finding(
            self._finding("Critical"), dimension="correctness")
        self.assertEqual(finding["severity"], "high")
        self.assertIn("severity", errs)
        # Every enum member survives untouched and error-free.
        for sev in self.mod.REVIEW_SEVERITIES:
            finding, errs = self.mod._review_normalize_finding(
                self._finding(sev.upper()), dimension="correctness")
            self.assertEqual(finding["severity"], sev)
            self.assertEqual(errs, [], sev)
        # A MISSING severity is equally un-gateable -> 'high', one error.
        raw = self._finding("high")
        del raw["severity"]
        finding, errs = self.mod._review_normalize_finding(
            raw, dimension="correctness")
        self.assertEqual(finding["severity"], "high")
        self.assertEqual(errs.count("severity"), 1)

    def test_off_enum_severity_confirmed_finding_blocks_the_gate(self):
        # The audited fail-open half: severity 'Critical' + verdict=confirmed
        # used to yield confirmed_blocking=0 -> exit 0 -> a CONFIRMED defect
        # shipped GREEN.
        self._install("Critical", json.dumps(
            {"verdict": "confirmed", "verdict_rationale": "reproduced"}))
        stream = io.StringIO()
        result = self.mod._review_run_audit("DIFF", "CRITERIA", self.cfg,
                                            stream=stream)
        self.assertEqual(result["confirmed_blocking"], 1,
                         "a confirmed off-enum-severity finding must BLOCK")
        self.assertEqual(result["findings"][0]["severity"], "high")
        self.assertIn("severity", result["findings"][0]["_schema_errors"])

    def test_cmd_review_exits_1_on_confirmed_off_enum_severity(self):
        self._install("Critical", json.dumps({"verdict": "confirmed"}))
        args = argparse.Namespace(
            adversarial=True, worklog=None, diff_file=None, diff_range=None,
            acceptance_file=None, format="json")
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with pinned_review_env(), \
                contextlib.redirect_stdout(buf_out), \
                contextlib.redirect_stderr(buf_err):
            rc = self.mod.cmd_review(args)
        self.assertEqual(rc, 1, buf_err.getvalue())

    def test_off_enum_severity_with_verify_envelope_fails_closed(self):
        # The audited fail-open half of THIS diff: the round-4 fail-closed
        # branch keys on REVIEW_BLOCKING_SEVERITIES, so severity 'critical'
        # + an exit-0 error-envelope verifier used to leave
        # verify_unavailable=0 / audit_incomplete=False — the verdict was
        # silently degraded to uncertain-and-PASS.
        self._install("critical", '{"error": "rate limited"}')
        stream = io.StringIO()
        result = self.mod._review_run_audit("DIFF", "CRITERIA", self.cfg,
                                            stream=stream)
        self.assertEqual(result["verify_unavailable"], 1,
                         "an unverifiable off-enum-severity finding must "
                         "count unavailable (fail-CLOSED), not fail open")
        self.assertTrue(result["audit_incomplete"])
        self.assertEqual(result["findings"][0]["verdict"], "uncertain")

class NonObjectFindingItemsTests(unittest.TestCase):
    """Remediation round 8 (novel-input-edge-cases) — a reviewer payload whose
    `findings` LIST carries non-object items (strings / null — the exact LLM
    persona-drift shape this plan's history documents) used to be silently
    discarded: each item bumped `malformed`, yet the reviewer still counted
    toward reviewers_ok, NO diagnostic was emitted, cmd_review's exit logic
    ignores `malformed`, and the gate exited 0 GREEN — the one drift shape
    that was simultaneously SILENT and FAIL-OPEN. Now: any non-object finding
    item means the reviewer DROPPED defect text, so that reviewer does NOT
    count as run (reviewers_ok excludes it -> audit_incomplete -> exit 3,
    fail-CLOSED) and it is NAMED on the stream with its raw-output snippet."""

    DRIFTED = json.dumps({"dimension": "correctness",
                          "findings": ["HIGH: retry loop drops final attempt",
                                       None]})

    def setUp(self):
        self.mod = load_cli()
        self._orig_invoke = self.mod._review_invoke_agent
        self.cfg = {"model": "opus", "effort": "max",
                    "fanout": len(self.mod.REVIEW_DIMENSION_KEYS),
                    "max_rounds": 3}

    def tearDown(self):
        self.mod._review_invoke_agent = self._orig_invoke

    def _install(self, correctness_payload, verdict="refuted"):
        """Stub the seam: reviewer:correctness returns `correctness_payload`
        verbatim; every other reviewer is clean; verify returns `verdict`;
        critic is clean."""
        def fake(role, persona, prompt, cfg, timeout=None, cwd=None, **kw):
            if role == "reviewer:correctness":
                return correctness_payload
            if role.startswith("reviewer:"):
                return _reviewer_out(role.split(":", 1)[1], [])
            if role == "verify":
                return json.dumps({"verdict": verdict,
                                   "verdict_rationale": "checked"})
            return json.dumps({"gaps": [], "additional_findings": []})

        self.mod._review_invoke_agent = fake

    def _audit(self):
        stream = io.StringIO()
        result = self.mod._review_run_audit("DIFF", "CRITERIA", self.cfg,
                                            stream=stream)
        return result, stream.getvalue()

    def test_string_and_null_items_fail_audit_closed_and_are_named(self):
        # The audited live repro: exit 0, valid envelope, findings list of a
        # string + null. Used to yield reviewers_ok=5/5, empty stream, rc 0.
        self._install(self.DRIFTED)
        result, diag = self._audit()
        total = len(self.mod.REVIEW_DIMENSION_KEYS)
        self.assertEqual(result["reviewers_ok"], total - 1,
                         "a reviewer that DROPPED defect text (non-object "
                         "finding items) must NOT count as run")
        self.assertEqual(result["malformed"], 2)
        self.assertTrue(result["audit_incomplete"],
                        "dropped defect text must fail the audit CLOSED")
        # NEVER silent: the reviewer is NAMED with its raw-output snippet.
        self.assertIn("reviewer:correctness", diag)
        self.assertIn("non-object finding item", diag)
        self.assertIn("retry loop drops final attempt", diag)
        # The snippet is attacker-influenceable — labeled untrusted DATA.
        self.assertIn("untrusted agent stdout", diag)

    def test_cmd_review_exits_3_on_non_object_finding_items(self):
        # The full gate: the exact shape that used to ship GREEN (rc 0) while
        # the reviewer's reported defect text was dropped with zero log trace.
        self._install(self.DRIFTED)
        args = argparse.Namespace(
            adversarial=True, worklog=None, diff_file=None, diff_range=None,
            acceptance_file=None, format="json")
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with pinned_review_env(), \
                contextlib.redirect_stdout(buf_out), \
                contextlib.redirect_stderr(buf_err):
            rc = self.mod.cmd_review(args)
        err = buf_err.getvalue()
        self.assertEqual(rc, 3,
                         "dropped defect text must BLOCK (exit 3), never "
                         "ship the un-audited diff GREEN: %s" % err)
        self.assertIn("INCOMPLETE", err)
        self.assertIn("reviewer:correctness", err)
        self.assertIn("retry loop drops final attempt", err)

    def test_valid_dict_findings_alongside_junk_are_still_verified(self):
        # Partial drift: a REAL finding dict next to a junk string. The dict
        # finding must still be kept + verified (a real defect is never lost),
        # while the reviewer still fails the audit closed for the drop.
        finding = {"dimension": "correctness", "severity": "high",
                   "file": "scripts/agentware", "line": 10,
                   "summary": "planted", "failure_scenario": "crash"}
        self._install(json.dumps({"dimension": "correctness",
                                  "findings": [finding, "junk item"]}),
                      verdict="confirmed")
        result, diag = self._audit()
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["verdict"], "confirmed")
        self.assertEqual(result["confirmed_blocking"], 1)
        self.assertEqual(result["malformed"], 1)
        self.assertEqual(result["reviewers_ok"],
                         len(self.mod.REVIEW_DIMENSION_KEYS) - 1)
        self.assertTrue(result["audit_incomplete"])
        self.assertIn("reviewer:correctness", diag)

    def test_all_object_items_keep_reviewer_ok_and_silent(self):
        # Guard against over-blocking: a fully well-formed payload keeps the
        # pre-fix accounting — reviewer counts, no diagnostic fires.
        finding = {"dimension": "correctness", "severity": "low",
                   "file": "scripts/agentware", "line": 1,
                   "summary": "s", "failure_scenario": "x"}
        self._install(_reviewer_out("correctness", [finding]))
        result, diag = self._audit()
        self.assertEqual(result["reviewers_ok"],
                         len(self.mod.REVIEW_DIMENSION_KEYS))
        self.assertEqual(result["malformed"], 0)
        self.assertFalse(result["audit_incomplete"])
        self.assertNotIn("non-object finding item", diag)
