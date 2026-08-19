"""TransientRetryTests, VerifyCriticLiveRetryTests -- split from `test_review_orchestrator` (task 58).

See `tests.unit.ops._review_orchestrator_support` for the
shared header.
"""

from tests.unit.ops._review_orchestrator_support import (  # noqa: F401
    contextlib, io, json, load_cli, unittest)


class TransientRetryTests(unittest.TestCase):
    """Task 4 — bounded TRANSIENT-ONLY retry with exponential backoff over the
    `_review_spawn_once` seam. Deterministic and ZERO real sleep: backoff goes
    through the injectable REVIEW_SLEEP hook, replaced here by a recorder.
    Fail-closed invariant: exhausted retries and non-transient failures still
    return '' (the str contract every caller depends on)."""

    def setUp(self):
        self.mod = load_cli()
        self._orig_spawn = self.mod._review_spawn_once
        self._orig_sleep = self.mod.REVIEW_SLEEP
        self.sleeps = []
        self.mod.REVIEW_SLEEP = self.sleeps.append
        # Hermetic: the process-wide hang breaker must never leak across tests.
        self.mod._REVIEW_TIMEOUT_BREAKER["tripped"] = False
        self.cfg = {"model": "opus", "effort": "max"}

    def tearDown(self):
        self.mod._review_spawn_once = self._orig_spawn
        self.mod.REVIEW_SLEEP = self._orig_sleep
        self.mod._REVIEW_TIMEOUT_BREAKER["tripped"] = False

    def _res(self, **overrides):
        res = {"stdout": "", "returncode": None, "stderr_tail": "",
               "stderr_transient": False, "timed_out": False, "oserror": None}
        res.update(overrides)
        return res

    def _install_sequence(self, results):
        """Stub `_review_spawn_once` to return `results` in order (the last one
        repeats if called again). Returns the list of recorded calls."""
        calls = []

        def fake(cmd, env, cwd, timeout):
            calls.append({"cmd": cmd, "timeout": timeout})
            return results[min(len(calls) - 1, len(results) - 1)]

        self.mod._review_spawn_once = fake
        return calls

    def _invoke(self, role="reviewer:security"):
        stream = io.StringIO()
        out = self.mod._review_invoke_agent(
            role, "agentware-adversarial-reviewer", "PROMPT", self.cfg,
            stream=stream)
        return out, stream.getvalue()

    # --- retry behavior -------------------------------------------------

    def test_timeout_then_success_is_retried_and_returns_stdout(self):
        calls = self._install_sequence([
            self._res(timed_out=True),
            self._res(returncode=0, stdout="AGENT OUTPUT")])
        out, diag = self._invoke()
        self.assertEqual(out, "AGENT OUTPUT")
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.sleeps,
                         [self.mod.REVIEW_SPAWN_BACKOFF_BASE * 1])
        # The failed first attempt was still surfaced.
        self.assertIn("timed out after", diag)

    def test_transient_stderr_signature_is_retried(self):
        calls = self._install_sequence([
            self._res(returncode=1, stderr_tail="429 Too Many Requests"),
            self._res(returncode=0, stdout="OK")])
        out, _ = self._invoke()
        self.assertEqual(out, "OK")
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.sleeps), 1)

    def test_exhausted_transient_retries_fail_closed_with_exp_backoff(self):
        # 429-based (not timeout-based): a rate-limit blip gets the FULL retry
        # schedule; timeouts have their own tighter policy (tests below).
        calls = self._install_sequence([
            self._res(returncode=1, stderr_tail="429 Too Many Requests")])
        out, diag = self._invoke()
        self.assertEqual(out, "")  # fail-closed: '' -> reviewer did NOT run
        self.assertEqual(len(calls), self.mod.REVIEW_SPAWN_RETRIES + 1)
        base = self.mod.REVIEW_SPAWN_BACKOFF_BASE
        # Exponential, deterministic: base * 2**attempt (0-based).
        self.assertEqual(self.sleeps, [base * 1, base * 2])
        # Every failed attempt emitted its diagnostic.
        self.assertEqual(diag.count("spawn failed"),
                         self.mod.REVIEW_SPAWN_RETRIES + 1)

    def test_terminal_oserror_is_not_retried(self):
        calls = self._install_sequence([
            self._res(oserror="[Errno 2] No such file or directory: 'claude'")])
        out, _ = self._invoke()
        self.assertEqual(out, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_nontransient_nonzero_exit_is_not_retried(self):
        calls = self._install_sequence([
            self._res(returncode=2, stderr_tail="invalid x-api-key")])
        out, _ = self._invoke()
        self.assertEqual(out, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_clean_exit0_with_empty_stdout_is_not_retried(self):
        calls = self._install_sequence([self._res(returncode=0, stdout="")])
        out, diag = self._invoke()
        self.assertEqual(out, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(diag, "")  # a clean run emits no diagnostic

    def test_backoff_scales_with_base_constant(self):
        self._install_sequence([
            self._res(returncode=1, stderr_tail="429 Too Many Requests")])
        orig_base = self.mod.REVIEW_SPAWN_BACKOFF_BASE
        try:
            self.mod.REVIEW_SPAWN_BACKOFF_BASE = 0.5
            self._invoke()
        finally:
            self.mod.REVIEW_SPAWN_BACKOFF_BASE = orig_base
        self.assertEqual(self.sleeps, [0.5, 1.0])

    # --- timeout policy (deterministic-hang containment) ------------------
    # A timed-out attempt already consumed its FULL deadline; an identical-
    # deadline retry has near-zero success against a genuine hang while
    # multiplying wall-clock. Regression for the audited defect: timeouts were
    # retried REVIEW_SPAWN_RETRIES times, tripling a blackholed audit's cost.

    def test_second_consecutive_timeout_is_terminal_and_trips_breaker(self):
        calls = self._install_sequence([self._res(timed_out=True)])
        out, diag = self._invoke()
        self.assertEqual(out, "")
        # ONE second chance (a momentary network stall), then terminal —
        # never the full REVIEW_SPAWN_RETRIES schedule at full deadline each.
        self.assertEqual(len(calls),
                         self.mod.REVIEW_SPAWN_TIMEOUT_RETRIES + 1)
        self.assertEqual(self.sleeps, [self.mod.REVIEW_SPAWN_BACKOFF_BASE])
        self.assertEqual(diag.count("timed out after"), 2)
        # Two full-deadline timeouts back to back = deterministic hang: the
        # process-wide breaker trips so the REST of the fan-out is not taxed.
        self.assertTrue(self.mod._REVIEW_TIMEOUT_BREAKER["tripped"])

    def test_tripped_breaker_makes_further_timeouts_terminal(self):
        self.mod._REVIEW_TIMEOUT_BREAKER["tripped"] = True
        calls = self._install_sequence([self._res(timed_out=True)])
        out, _ = self._invoke()
        self.assertEqual(out, "")
        self.assertEqual(len(calls), 1, "hung runtime: no timeout retry at all")
        self.assertEqual(self.sleeps, [])

    def test_tripped_breaker_still_allows_stderr_transient_retry(self):
        # The breaker is about DEADLINE hangs only — a 429 blip (fast failure)
        # keeps its retry even after the breaker tripped.
        self.mod._REVIEW_TIMEOUT_BREAKER["tripped"] = True
        calls = self._install_sequence([
            self._res(returncode=1, stderr_tail="429 Too Many Requests"),
            self._res(returncode=0, stdout="OK")])
        out, _ = self._invoke()
        self.assertEqual(out, "OK")
        self.assertEqual(len(calls), 2)

    def test_successful_spawn_resets_the_breaker(self):
        self.mod._REVIEW_TIMEOUT_BREAKER["tripped"] = True
        self._install_sequence([self._res(returncode=0, stdout="OK")])
        out, _ = self._invoke()
        self.assertEqual(out, "OK")
        self.assertFalse(self.mod._REVIEW_TIMEOUT_BREAKER["tripped"],
                         "a success proves the runtime reachable")

    # --- mixed transient sequences (remediation round 3 regressions) -------
    # Audited defect: the `attempt >= eff_retries` exhaustion return used to
    # run BEFORE the timeout bookkeeping, so a full-deadline timeout landing
    # on the FINAL attempt was never counted — [429, timeout, timeout] left
    # the breaker untripped and every later agent in a blackholed fan-out
    # re-paid its own extra full deadlines (hours of wall-clock versus the
    # documented ~one extra deadline total).

    def test_final_attempt_timeout_after_429_still_trips_breaker(self):
        calls = self._install_sequence([
            self._res(returncode=1, stderr_tail="429 Too Many Requests"),
            self._res(timed_out=True),
            self._res(timed_out=True)])
        out, diag = self._invoke()
        self.assertEqual(out, "")
        self.assertEqual(len(calls), self.mod.REVIEW_SPAWN_RETRIES + 1)
        base = self.mod.REVIEW_SPAWN_BACKOFF_BASE
        self.assertEqual(self.sleeps, [base * 1, base * 2])
        self.assertEqual(diag.count("timed out after"), 2)
        # Two full-deadline timeouts within ONE invocation — even with a fast
        # 429 interleaved, even when the second lands on the LAST attempt —
        # prove a deterministic hang: the process-wide breaker MUST trip.
        self.assertTrue(self.mod._REVIEW_TIMEOUT_BREAKER["tripped"],
                        "a final-attempt timeout must still count toward "
                        "the hang breaker")
        # Containment holds for the REST of the fan-out: the next agent's
        # timeout is terminal after a single attempt (no extra deadline).
        next_calls = self._install_sequence([self._res(timed_out=True)])
        del self.sleeps[:]
        out2, _ = self._invoke(role="reviewer:perf")
        self.assertEqual(out2, "")
        self.assertEqual(len(next_calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_timeout_429_timeout_sequence_trips_breaker(self):
        calls = self._install_sequence([
            self._res(timed_out=True),
            self._res(returncode=1, stderr_tail="429 Too Many Requests"),
            self._res(timed_out=True)])
        out, _ = self._invoke()
        self.assertEqual(out, "")
        self.assertEqual(len(calls), self.mod.REVIEW_SPAWN_RETRIES + 1)
        self.assertTrue(self.mod._REVIEW_TIMEOUT_BREAKER["tripped"],
                        "two full-deadline timeouts in one invocation must "
                        "trip the breaker even with a 429 between them")

    def test_single_final_attempt_timeout_does_not_trip_breaker(self):
        # Guard: ONE timeout in an invocation (even on the last attempt) does
        # not prove a hang — [429, 429, timeout] must NOT trip the breaker.
        calls = self._install_sequence([
            self._res(returncode=1, stderr_tail="429 Too Many Requests"),
            self._res(returncode=1, stderr_tail="429 Too Many Requests"),
            self._res(timed_out=True)])
        out, _ = self._invoke()
        self.assertEqual(out, "")
        self.assertEqual(len(calls), self.mod.REVIEW_SPAWN_RETRIES + 1)
        self.assertFalse(self.mod._REVIEW_TIMEOUT_BREAKER["tripped"])

    # --- retries=0 opt-out (mutating / report-only callers) ---------------

    def test_retries_zero_disables_retry_even_for_transient(self):
        calls = self._install_sequence([
            self._res(returncode=1, stderr_tail="429 Too Many Requests")])
        stream = io.StringIO()
        out = self.mod._review_invoke_agent(
            "fixer", "agentware-execution", "PROMPT", self.cfg,
            stream=stream, retries=0)
        self.assertEqual(out, "")
        self.assertEqual(len(calls), 1, "retries=0 = exactly ONE attempt")
        self.assertEqual(self.sleeps, [])
        # The single failed attempt is still surfaced (diagnostics unchanged).
        self.assertIn("fixer exited 1:", stream.getvalue())
        self.assertIn("429 Too Many Requests", stream.getvalue())

    def test_retries_zero_timeout_is_single_attempt_and_no_breaker_trip(self):
        calls = self._install_sequence([self._res(timed_out=True)])
        out = self.mod._review_invoke_agent(
            "checkup", "agentware-adversarial-reviewer", "PROMPT", self.cfg,
            stream=io.StringIO(), retries=0)
        self.assertEqual(out, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.sleeps, [])
        # A single-attempt caller cannot distinguish hang from blip — it must
        # never trip the process-wide breaker for the audit fan-out.
        self.assertFalse(self.mod._REVIEW_TIMEOUT_BREAKER["tripped"])

    # --- classifier (pure) ----------------------------------------------

    def test_classifier_timeout_is_transient(self):
        self.assertTrue(self.mod._review_is_transient_failure(
            self._res(timed_out=True)))

    def test_classifier_transient_stderr_signatures(self):
        for tail in ("HTTP 429", "error 529", "overloaded_error",
                     "Rate-Limit exceeded", "rate limit", "Too Many Requests",
                     "connection timeout", "request timed out"):
            self.assertTrue(self.mod._review_is_transient_failure(
                self._res(returncode=1, stderr_tail=tail)), tail)

    def test_classifier_connection_level_blips_are_transient(self):
        # Remediation round 3 (novel-input): the live-reproduced momentary
        # network-failure shapes that used to classify TERMINAL (zero
        # retries -> false BLOCK — the exact pre-feature fragility this
        # feature was shipped to eliminate).
        for tail in ("TypeError: fetch failed: read ECONNRESET",
                     "connect ECONNREFUSED 160.79.104.10:443",
                     "503 Service Unavailable",
                     "502 Bad Gateway",
                     "500 Internal Server Error",
                     "504 Gateway Timeout",
                     "getaddrinfo ENOTFOUND api.anthropic.com",
                     "getaddrinfo EAI_AGAIN api.anthropic.com",
                     "socket hang up",
                     "read ETIMEDOUT",
                     "Error: connection reset by peer",
                     "network error while fetching"):
            self.assertTrue(self.mod._review_is_transient_failure(
                self._res(returncode=1, stderr_tail=tail)), tail)

    def test_classifier_status_codes_are_word_bounded(self):
        # Guard on the broadened regex: bare status-code digits inside larger
        # numbers or identifiers must NOT classify transient (an accidental
        # retry of a genuine crash only delays the fail-closed BLOCK).
        for tail in ("ValueError at line 5023 of module_4290.py",
                     "assertion failed: expected 15000 got 15290",
                     "authentication_error", "invalid x-api-key",
                     "permission denied"):
            self.assertFalse(self.mod._review_is_transient_failure(
                self._res(returncode=1, stderr_tail=tail)), tail)

    def test_classifier_nontransient_stderr_is_terminal(self):
        self.assertFalse(self.mod._review_is_transient_failure(
            self._res(returncode=1, stderr_tail="authentication_error")))

    def test_classifier_honors_full_stderr_flag_when_tail_truncated(self):
        # Regression: a 429 signature followed by >2KB of stack trace is cut
        # out of the ~2KB tail; `stderr_transient` (computed at capture time
        # against the FULL stderr) must still classify the failure transient.
        res = self._res(returncode=1, stderr_transient=True,
                        stderr_tail="x" * 2048)  # tail carries no signature
        self.assertTrue(self.mod._review_is_transient_failure(res))

    def test_classifier_oserror_is_terminal_even_with_transient_stderr(self):
        self.assertFalse(self.mod._review_is_transient_failure(
            self._res(oserror="boom", stderr_tail="429 rate limit")))

    def test_classifier_exit0_is_never_a_failure(self):
        self.assertFalse(self.mod._review_is_transient_failure(
            self._res(returncode=0, stderr_tail="timeout noise on stderr")))

    def test_classifier_tolerates_none_and_empty(self):
        self.assertFalse(self.mod._review_is_transient_failure(None))
        self.assertFalse(self.mod._review_is_transient_failure({}))

class VerifyCriticLiveRetryTests(unittest.TestCase):
    """Remediation round 6 (test-quality) — lock the claimed transient-retry
    for the VERIFY and CRITIC legs through the LIVE wrapper (the code comment
    says retry 'holds for reviewer/verify/critic spawns', but every prior
    live-wrapper test spawned zero verify agents). Stubs ONLY the raw
    `_review_spawn_once` seam: a future refactor passing retries=0 on the
    verify (or critic) call — mirroring the adjacent fixer/checkup callers —
    breaks THIS test instead of shipping green and re-introducing the
    one-429-blip-falsely-BLOCKS fragility on the verify leg."""

    ALL_OK = json.dumps({"findings": [], "gaps": [], "additional_findings": []})
    VERIFY_MARK = "Try HARD to REFUTE"          # unique to the verify prompt
    CRITIC_MARK = "COMPLETENESS CRITIC"         # unique to the critic prompt
    REVIEWER_MARK = "'correctness' dimension reviewer"

    def setUp(self):
        self.mod = load_cli()
        self._orig_spawn = self.mod._review_spawn_once
        self._orig_sleep = self.mod.REVIEW_SLEEP
        self.sleeps = []
        self.mod.REVIEW_SLEEP = self.sleeps.append
        self.mod._REVIEW_TIMEOUT_BREAKER["tripped"] = False
        self.cfg = {"model": "opus", "effort": "max",
                    "fanout": len(self.mod.REVIEW_DIMENSION_KEYS),
                    "max_rounds": 3}

    def tearDown(self):
        self.mod._review_spawn_once = self._orig_spawn
        self.mod.REVIEW_SLEEP = self._orig_sleep
        self.mod._REVIEW_TIMEOUT_BREAKER["tripped"] = False

    @staticmethod
    def _transient():
        return {"stdout": "", "returncode": 1,
                "stderr_tail": "429 Too Many Requests",
                "stderr_transient": True, "timed_out": False, "oserror": None}

    @staticmethod
    def _ok(payload):
        return {"stdout": payload, "returncode": 0, "stderr_tail": "",
                "stderr_transient": False, "timed_out": False, "oserror": None}

    def _install(self, flaky_mark, flaky_payload):
        """Stub the INNER seam: the reviewer:correctness spawn emits ONE high
        finding; the spawn whose prompt carries `flaky_mark` fails transiently
        on its FIRST attempt then succeeds with `flaky_payload`; everything
        else succeeds cleanly. Returns the per-mark attempt counter."""
        finding = {"dimension": "correctness", "severity": "high",
                   "file": "scripts/agentware", "line": 10,
                   "summary": "planted", "failure_scenario": "crash"}
        reviewer_payload = json.dumps(
            {"dimension": "correctness", "findings": [finding]})
        attempts = {"flaky": 0}

        def fake(cmd, env, cwd, timeout):
            prompt = cmd[-1]
            if flaky_mark in prompt:
                attempts["flaky"] += 1
                return self._transient() if attempts["flaky"] == 1 \
                    else self._ok(flaky_payload)
            if self.REVIEWER_MARK in prompt:
                return self._ok(reviewer_payload)
            return self._ok(self.ALL_OK)

        self.mod._review_spawn_once = fake
        return attempts

    def test_verify_leg_transient_blip_is_retried_through_live_wrapper(self):
        attempts = self._install(self.VERIFY_MARK, json.dumps(
            {"verdict": "confirmed", "verdict_rationale": "reproduced"}))
        buf_err = io.StringIO()
        with contextlib.redirect_stderr(buf_err):
            result = self.mod._review_run_audit(
                "DIFF", "CRITERIA", self.cfg, stream=buf_err)
        # The verify spawn was RETRIED (2 raw attempts, one recorded backoff)
        # and its verdict landed — a retries=0 regression on the verify call
        # yields '' -> verdict uncertain + verify_unavailable=1 instead.
        self.assertEqual(attempts["flaky"], 2,
                         "the verify leg must traverse the transient retry")
        self.assertEqual(self.sleeps, [self.mod.REVIEW_SPAWN_BACKOFF_BASE])
        self.assertEqual(result["findings"][0]["verdict"], "confirmed")
        self.assertEqual(result["confirmed_blocking"], 1)
        self.assertEqual(result["verify_unavailable"], 0)
        self.assertFalse(result["audit_incomplete"],
                         "a retried-then-verified finding must not fail the "
                         "audit closed: %s" % buf_err.getvalue())
        # The failed first attempt was still surfaced, naming the verify role.
        self.assertIn("verify", buf_err.getvalue())
        self.assertIn("429", buf_err.getvalue())

    def test_critic_leg_transient_blip_is_retried_through_live_wrapper(self):
        attempts = self._install(self.CRITIC_MARK, json.dumps(
            {"gaps": ["one gap"], "additional_findings": []}))
        buf_err = io.StringIO()
        with contextlib.redirect_stderr(buf_err):
            result = self.mod._review_run_audit(
                "DIFF", "CRITERIA", self.cfg, stream=buf_err)
        self.assertEqual(attempts["flaky"], 2,
                         "the critic leg must traverse the transient retry")
        self.assertEqual(result["critic"]["gaps"], ["one gap"],
                         "the retried critic's payload must land")

    def test_verify_leg_exhausted_retries_still_fail_closed(self):
        # Guard: the retry must not weaken fail-closed on the verify leg — a
        # verify spawn transient on EVERY attempt still blocks the audit.
        finding = {"dimension": "correctness", "severity": "high",
                   "file": "scripts/agentware", "line": 10,
                   "summary": "planted", "failure_scenario": "crash"}
        reviewer_payload = json.dumps(
            {"dimension": "correctness", "findings": [finding]})
        verify_attempts = []

        def fake(cmd, env, cwd, timeout):
            prompt = cmd[-1]
            if self.VERIFY_MARK in prompt:
                verify_attempts.append(1)
                return self._transient()
            if self.REVIEWER_MARK in prompt:
                return self._ok(reviewer_payload)
            return self._ok(self.ALL_OK)

        self.mod._review_spawn_once = fake
        with contextlib.redirect_stderr(io.StringIO()):
            result = self.mod._review_run_audit("DIFF", "CRITERIA", self.cfg,
                                                stream=io.StringIO())
        self.assertEqual(len(verify_attempts),
                         self.mod.REVIEW_SPAWN_RETRIES + 1)
        self.assertEqual(result["verify_unavailable"], 1)
        self.assertTrue(result["audit_incomplete"])
