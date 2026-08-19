"""SpawnResilienceE2ETests, RealSpawnSeamContractTests, InvokeAgentCommandContractTests -- split from `test_review_orchestrator` (task 58).

See `tests.unit.ops._review_orchestrator_support` for the
shared header.
"""

from tests.unit.ops._review_orchestrator_support import (  # noqa: F401
    REVIEW_ENV_KNOBS, argparse, contextlib, io, json, load_cli, os,
    pinned_review_env, shutil, tempfile, types, unittest)


class SpawnResilienceE2ETests(unittest.TestCase):
    """Task 6 — [e2e] END-TO-END drives of the FULL `cmd_review --adversarial`
    entrypoint with ONLY the raw `_review_spawn_once` seam stubbed (the real
    retry wrapper, diagnostics, audit orchestration, and exit-code logic all
    run LIVE) and REVIEW_SLEEP replaced by a recorder. Proves the three
    feature guarantees TOGETHER, deterministically:

      * a TRANSIENT blip (429 on one reviewer's first attempt) is retried
        with recorded backoff and the gate PASSES (exit 0, audit complete) —
        while the failed attempt's diagnostic still names reviewer + reason;
      * a reviewer transient on EVERY attempt exhausts its bounded retries
        and the gate BLOCKS (exit 3 — the distinct code run_selfheal_review
        blocks on) with the named diagnostic on the captured stream;
      * both at once in ONE audit: the recovered reviewer counts toward
        reviewers_ok, the exhausted one fails the audit CLOSED.

    ZERO real sleep (all backoff through the recorder) and no real Opus."""

    ALL_OK = json.dumps({"findings": [], "gaps": [], "additional_findings": []})
    RECOVER_MARK = "'correctness' dimension reviewer"
    EXHAUST_MARK = "'security-governance' dimension reviewer"

    def setUp(self):
        self.mod = load_cli()
        self._orig_spawn = self.mod._review_spawn_once
        self._orig_sleep = self.mod.REVIEW_SLEEP
        self.sleeps = []
        self.mod.REVIEW_SLEEP = self.sleeps.append
        self.mod._REVIEW_TIMEOUT_BREAKER["tripped"] = False

    def tearDown(self):
        self.mod._review_spawn_once = self._orig_spawn
        self.mod.REVIEW_SLEEP = self._orig_sleep
        self.mod._REVIEW_TIMEOUT_BREAKER["tripped"] = False

    @staticmethod
    def _transient():
        return {"stdout": "", "returncode": 1,
                "stderr_tail": "API error 429 Too Many Requests",
                "stderr_transient": True, "timed_out": False, "oserror": None}

    def _ok(self):
        return {"stdout": self.ALL_OK, "returncode": 0, "stderr_tail": "",
                "stderr_transient": False, "timed_out": False, "oserror": None}

    def _install(self, recover_mark=None, exhaust_mark=None):
        """Stub ONLY the raw inner spawn seam. A spawn whose prompt contains
        `recover_mark` fails transiently on its FIRST attempt then succeeds;
        one containing `exhaust_mark` fails transiently on EVERY attempt;
        everything else succeeds with an empty-findings payload. Returns the
        per-mark raw-attempt counters."""
        attempts = {"recover": 0, "exhaust": 0}

        def fake(cmd, env, cwd, timeout):
            prompt = cmd[-1]
            if recover_mark and recover_mark in prompt:
                attempts["recover"] += 1
                return self._transient() if attempts["recover"] == 1 \
                    else self._ok()
            if exhaust_mark and exhaust_mark in prompt:
                attempts["exhaust"] += 1
                return self._transient()
            return self._ok()

        self.mod._review_spawn_once = fake
        return attempts

    def _drive(self):
        """Run the FULL cmd_review --adversarial entrypoint, capturing both
        streams, with every ambient review env knob pinned (hermetic — an
        operator's AGENTWARE_REVIEW_FANOUT/etc. must not skew the drive).
        Returns (rc, stdout_text, stderr_text)."""
        args = argparse.Namespace(
            adversarial=True, worklog=None, diff_file=None, diff_range=None,
            acceptance_file=None, format="json")
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with pinned_review_env(), \
                contextlib.redirect_stdout(buf_out), \
                contextlib.redirect_stderr(buf_err):
            rc = self.mod.cmd_review(args)
        return rc, buf_out.getvalue(), buf_err.getvalue()

    def test_transient_blip_is_retried_and_gate_passes_e2e(self):
        attempts = self._install(recover_mark=self.RECOVER_MARK)
        rc, out, err = self._drive()
        # Gate PASSES: the blip was retried, the reviewer ran, nothing blocks.
        self.assertEqual(rc, 0, err)
        result = json.loads(out)
        total = len(self.mod.REVIEW_DIMENSION_KEYS)
        self.assertEqual(result["reviewers_ok"], total)
        self.assertEqual(result["reviewers_total"], total)
        self.assertFalse(result["audit_incomplete"])
        self.assertEqual(result["confirmed_blocking"], 0)
        # Exactly ONE retry happened: fail-once then succeed -> 2 raw attempts,
        # one recorded backoff of base * 2**0 — through the recorder only.
        self.assertEqual(attempts["recover"], 2)
        base = self.mod.REVIEW_SPAWN_BACKOFF_BASE
        self.assertEqual(self.sleeps, [base * 1])
        # The failed attempt was NOT silent even though the gate passed: the
        # diagnostic names WHICH reviewer and WHY on the captured stream.
        self.assertIn("reviewer:correctness", err)
        self.assertIn("429", err)
        # But it never escalated to the fail-closed INCOMPLETE message.
        self.assertNotIn("INCOMPLETE", err)

    def test_exhausted_transient_reviewer_blocks_exit3_e2e(self):
        attempts = self._install(exhaust_mark=self.EXHAUST_MARK)
        rc, _out, err = self._drive()
        # Bounded retries exhausted -> '' -> audit incomplete -> exit 3, the
        # DISTINCT code run_selfheal_review BLOCKS on (bash side proven by
        # tests.integration.test_loop_selfheal_gate).
        self.assertEqual(rc, 3, err)
        self.assertEqual(attempts["exhaust"],
                         self.mod.REVIEW_SPAWN_RETRIES + 1)
        base = self.mod.REVIEW_SPAWN_BACKOFF_BASE
        self.assertEqual(self.sleeps, [base * 1, base * 2])
        # The stream carries BOTH the fail-closed banner and the named
        # per-attempt diagnostic (grep-able in $STATE_DIR/adversarial-review.log)
        # — never again the vague "unreachable?" guess alone.
        self.assertIn("INCOMPLETE", err)
        self.assertIn("reviewer:security-governance", err)
        self.assertIn("429", err)

    def test_mixed_recovery_and_exhaustion_in_one_audit_e2e(self):
        attempts = self._install(recover_mark=self.RECOVER_MARK,
                                 exhaust_mark=self.EXHAUST_MARK)
        rc, out, err = self._drive()
        # The exhausted reviewer fails the audit CLOSED even though the other
        # recovered — partial resilience can never mask a genuinely-down agent.
        self.assertEqual(rc, 3, err)
        result = json.loads(out)
        total = len(self.mod.REVIEW_DIMENSION_KEYS)
        # The RECOVERED reviewer counts toward reviewers_ok; ONLY the
        # exhausted one is missing.
        self.assertEqual(result["reviewers_ok"], total - 1)
        self.assertTrue(result["audit_incomplete"])
        self.assertEqual(attempts["recover"], 2)
        self.assertEqual(attempts["exhaust"],
                         self.mod.REVIEW_SPAWN_RETRIES + 1)
        # Backoff interleaves deterministically in dimension order:
        # correctness runs first (one retry), then security-governance
        # (exhausts its full schedule).
        base = self.mod.REVIEW_SPAWN_BACKOFF_BASE
        self.assertEqual(self.sleeps, [base * 1, base * 1, base * 2])
        # Both reviewers' diagnostics are on the stream; only the exhausted
        # one triggered the fail-closed banner.
        self.assertIn("reviewer:correctness", err)
        self.assertIn("reviewer:security-governance", err)
        self.assertIn("INCOMPLETE", err)

    def test_drive_is_hermetic_to_ambient_review_env_knobs(self):
        # Regression: these cmd_review-level drives resolve config from the
        # AMBIENT environment; an operator/CI env with a documented knob set
        # (AGENTWARE_REVIEW_FANOUT=1) used to truncate the fan-out out from
        # under the test and flip the expected exit code — a spurious failure
        # on the self-extension gate chain's own test step.
        attempts = self._install(exhaust_mark=self.EXHAUST_MARK)
        saved = {k: os.environ.get(k) for k in REVIEW_ENV_KNOBS}
        os.environ["AGENTWARE_REVIEW_FANOUT"] = "1"
        os.environ["AGENTWARE_REVIEW_MAX_ROUNDS"] = "9"
        try:
            rc, out, err = self._drive()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(rc, 3, err)
        result = json.loads(out)
        self.assertEqual(result["reviewers_total"],
                         len(self.mod.REVIEW_DIMENSION_KEYS),
                         "ambient FANOUT=1 must not truncate the pinned drive")
        self.assertEqual(attempts["exhaust"],
                         self.mod.REVIEW_SPAWN_RETRIES + 1)

class RealSpawnSeamContractTests(unittest.TestCase):
    """Remediation regression (test-quality) — bind the REAL
    `_review_spawn_once` result contract to its consumers. No hand-built
    replica dicts here: these tests run the real function against a stubbed
    module-level subprocess.run, a REALLY absent binary (the live OSError
    branch), or a real fake-CLI script on disk — so a one-key contract drift
    (e.g. a typo'd `timed_out`) breaks a test instead of shipping green."""

    def setUp(self):
        self.mod = load_cli()
        self._orig_run = self.mod.subprocess.run
        self._orig_sleep = self.mod.REVIEW_SLEEP
        self._orig_mono = self.mod.time.monotonic
        self.sleeps = []
        self.mod.REVIEW_SLEEP = self.sleeps.append
        self.mod._REVIEW_TIMEOUT_BREAKER["tripped"] = False
        self._env_cli = os.environ.get("AGENTWARE_CLI")
        self.cfg = {"model": "opus", "effort": "max"}
        self.tmp = tempfile.mkdtemp(prefix="aw-realseam-")

    def tearDown(self):
        self.mod.subprocess.run = self._orig_run
        self.mod.REVIEW_SLEEP = self._orig_sleep
        self.mod.time.monotonic = self._orig_mono
        self.mod._REVIEW_TIMEOUT_BREAKER["tripped"] = False
        if self._env_cli is None:
            os.environ.pop("AGENTWARE_CLI", None)
        else:
            os.environ["AGENTWARE_CLI"] = self._env_cli
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pin_monotonic(self, seq, last=None):
        """Replace time.monotonic with a deterministic tick sequence (repeating
        the final value once exhausted) — restored in tearDown."""
        ticks = list(seq)
        final = last if last is not None else ticks[-1]
        self.mod.time.monotonic = (
            lambda: ticks.pop(0) if ticks else final)

    def test_real_timeout_contract_drives_retry_and_diagnostic(self):
        # A REAL subprocess timeout (raised by the module's subprocess.run,
        # carrying BYTES stderr with an undecodable byte) must flow through
        # the real `_review_spawn_once` as timed_out=True: one full-deadline
        # retry, per-attempt 'timed out after Ns' diagnostics, breaker trip.
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            raise self.mod.subprocess.TimeoutExpired(
                cmd=cmd, timeout=5, stderr=b"partial \xff output")

        self.mod.subprocess.run = fake_run
        stream = io.StringIO()
        out = self.mod._review_invoke_agent(
            "reviewer:perf", "agentware-adversarial-reviewer", "PROMPT",
            self.cfg, timeout=5, stream=stream)
        self.assertEqual(out, "")
        self.assertEqual(len(calls),
                         self.mod.REVIEW_SPAWN_TIMEOUT_RETRIES + 1)
        self.assertEqual(self.sleeps, [self.mod.REVIEW_SPAWN_BACKOFF_BASE])
        diag = stream.getvalue()
        self.assertEqual(diag.count("reviewer:perf timed out after 5s"), 2)
        # Contract drift guard: a timeout must never degrade to the generic
        # nonzero-exit taxonomy on the real path.
        self.assertNotIn("exited None", diag)
        self.assertTrue(self.mod._REVIEW_TIMEOUT_BREAKER["tripped"])

    def test_real_oserror_branch_agent_not_found_is_terminal(self):
        # NO stubbing at all: point the CLI at a really-absent binary so the
        # live OSError except-branch of `_review_spawn_once` executes.
        os.environ["AGENTWARE_CLI"] = os.path.join(
            self.tmp, "definitely-absent-agent-binary")
        stream = io.StringIO()
        out = self.mod._review_invoke_agent(
            "critic", "agentware-adversarial-reviewer", "PROMPT", self.cfg,
            timeout=10, stream=stream)
        self.assertEqual(out, "")
        diag = stream.getvalue()
        self.assertIn("critic agent not found:", diag)
        self.assertEqual(diag.count("spawn failed"), 1,
                         "a terminal OSError gets exactly ONE attempt")
        self.assertEqual(self.sleeps, [])

    def test_transient_signature_beyond_2kb_tail_retries_on_real_path(self):
        # Regression (correctness): a genuine 429 line followed by >2KB of
        # stack-trace output used to be truncated out of the classified tail
        # -> misclassified terminal -> no retry -> false BLOCK. Fully real
        # path: a fake CLI script fails once with that exact stderr shape,
        # then succeeds — the real seam + wrapper must retry and recover.
        marker = os.path.join(self.tmp, "first-attempt-done")
        script = os.path.join(self.tmp, "fake-claude")
        with open(script, "w") as f:
            f.write(
                "#!/bin/sh\n"
                "if [ ! -f '%s' ]; then\n"
                "  : > '%s'\n"
                "  printf 'API Error: 429 Too Many Requests\\n' >&2\n"
                "  head -c 3000 /dev/zero | tr '\\0' 'x' >&2\n"
                "  exit 1\n"
                "fi\n"
                "printf 'REAL OK'\n" % (marker, marker))
        os.chmod(script, 0o755)
        os.environ["AGENTWARE_CLI"] = script
        stream = io.StringIO()
        out = self.mod._review_invoke_agent(
            "reviewer:correctness", "agentware-adversarial-reviewer",
            "PROMPT", self.cfg, timeout=30, stream=stream)
        self.assertEqual(out, "REAL OK",
                         "the buried 429 must classify transient and retry")
        self.assertEqual(self.sleeps, [self.mod.REVIEW_SPAWN_BACKOFF_BASE])
        diag = stream.getvalue()
        self.assertIn("reviewer:correctness exited 1:", diag)
        # The ~2KB diagnostic tail genuinely lost the signature — proving the
        # transient classification ran against the FULL stderr, not the tail.
        self.assertNotIn("429", diag)

    def test_spawn_once_flags_full_stderr_transient_beyond_tail(self):
        # Real `_review_spawn_once` against a stubbed completed process:
        # the transient flag is computed from the FULL stderr at capture time
        # while the tail (diagnostic-only) no longer carries the signature.
        big_stderr = "429 Too Many Requests\n" + "x" * 3000
        self.mod.subprocess.run = lambda *a, **k: types.SimpleNamespace(
            returncode=1, stdout="", stderr=big_stderr)
        res = self.mod._review_spawn_once(
            ["claude", "-p", "PROMPT"], dict(os.environ), self.tmp, 5)
        self.assertEqual(res["returncode"], 1)
        self.assertTrue(res["stderr_transient"])
        self.assertNotIn("429", res["stderr_tail"])
        self.assertTrue(self.mod._review_is_transient_failure(res))

    def test_stderr_tail_decodes_bytes_with_replacement(self):
        # The bytes path of the stderr decoder (TimeoutExpired can carry
        # undecodable partial output) must never raise.
        self.assertEqual(self.mod._review_stderr_tail(b"abc\xffdef"),
                         "abc�def")
        self.assertEqual(self.mod._review_stderr_tail(None), "")
        self.assertEqual(self.mod._review_stderr_text(b"\xff"), "�")

    # --- invalid-UTF-8 process output (remediation round 5, novel-input) ----
    # `_review_spawn_once` documents 'Never raises', but strict text-mode
    # decoding inside subprocess.run (universal_newlines=True) raised
    # UnicodeDecodeError on a single invalid byte from a NORMALLY-COMPLETING
    # process — crashing the whole audit fan-out (bash gate misread the
    # traceback exit as 'confirmed blocking findings'). These run the REAL
    # seam against a REAL CLI script emitting the exact bytes.

    def test_completed_process_invalid_utf8_stderr_never_raises(self):
        script = os.path.join(self.tmp, "fake-claude-bad-stderr")
        with open(script, "w") as f:
            f.write("#!/bin/sh\nprintf 'API error \\377 partial' >&2\nexit 1\n")
        os.chmod(script, 0o755)
        os.environ["AGENTWARE_CLI"] = script
        stream = io.StringIO()
        # The live repro from the audit: this call used to raise
        # UnicodeDecodeError out of the entire gate.
        out = self.mod._review_invoke_agent(
            "reviewer:correctness", "agentware-adversarial-reviewer",
            "PROMPT", self.cfg, timeout=30, stream=stream)
        self.assertEqual(out, "", "nonzero exit stays fail-closed")
        diag = stream.getvalue()
        self.assertIn("reviewer:correctness exited 1:", diag)
        # The undecodable byte survives as U+FFFD inside the diagnostic.
        self.assertIn("API error � partial", diag)
        self.assertEqual(self.sleeps, [], "non-transient: no retry")

    def test_completed_process_invalid_utf8_stdout_never_raises(self):
        script = os.path.join(self.tmp, "fake-claude-bad-stdout")
        with open(script, "w") as f:
            f.write("#!/bin/sh\nprintf 'ok \\377 out'\nexit 0\n")
        os.chmod(script, 0o755)
        os.environ["AGENTWARE_CLI"] = script
        out = self.mod._review_invoke_agent(
            "reviewer:perf", "agentware-adversarial-reviewer", "PROMPT",
            self.cfg, timeout=30, stream=io.StringIO())
        self.assertEqual(out, "ok � out",
                         "exit-0 stdout with a bad byte must decode "
                         "tolerantly, not crash the fan-out")

    def test_spawn_once_tolerates_invalid_utf8_on_both_streams(self):
        script = os.path.join(self.tmp, "fake-claude-bad-both")
        with open(script, "w") as f:
            f.write("#!/bin/sh\nprintf 'out \\377 x'\n"
                    "printf 'err \\377 y' >&2\nexit 1\n")
        os.chmod(script, 0o755)
        res = self.mod._review_spawn_once(
            [script], dict(os.environ), self.tmp, 30)
        self.assertEqual(res["returncode"], 1)
        self.assertEqual(res["stdout"], "out � x")
        self.assertIn("err � y", res["stderr_tail"])

    # --- duration measurement (remediation round 5, test-quality) -----------
    # Bind the REAL `duration` assignments in `_review_spawn_once` — the wire
    # powering round-4 deadline-class containment. Without these, deleting or
    # typo-renaming the `result["duration"] = time.monotonic() - start` lines
    # shipped green (hand-built dicts still carried the key) while a
    # CLI-self-resolved hang silently reverted to the full retry schedule.

    def test_real_seam_measures_completed_attempt_duration(self):
        self._pin_monotonic([100.0, 700.0])
        self.mod.subprocess.run = lambda *a, **k: types.SimpleNamespace(
            returncode=1, stdout=b"",
            stderr=b"TypeError: fetch failed: connect ETIMEDOUT")
        res = self.mod._review_spawn_once(
            ["claude", "-p", "PROMPT"], dict(os.environ), self.tmp, 900)
        self.assertEqual(res["duration"], 600.0)
        # The measured duration is what makes this deadline-class at a 900s
        # deadline (600 >= 0.5*900) and NOT at a 1300s one (600 < 650).
        self.assertTrue(self.mod._review_is_deadline_class(res, 900))
        self.assertFalse(self.mod._review_is_deadline_class(res, 1300))

    def test_real_seam_measures_timeout_branch_duration(self):
        self._pin_monotonic([1000.0, 1005.0])

        def fake_run(cmd, **kwargs):
            raise self.mod.subprocess.TimeoutExpired(
                cmd=cmd, timeout=5, stderr=b"x")

        self.mod.subprocess.run = fake_run
        res = self.mod._review_spawn_once(
            ["claude", "-p", "PROMPT"], dict(os.environ), self.tmp, 5)
        self.assertTrue(res["timed_out"])
        self.assertEqual(res["duration"], 5.0)

    def test_real_seam_measures_oserror_branch_duration(self):
        self._pin_monotonic([50.0, 53.5])

        def fake_run(cmd, **kwargs):
            raise OSError("no such file")

        self.mod.subprocess.run = fake_run
        res = self.mod._review_spawn_once(
            ["claude", "-p", "PROMPT"], dict(os.environ), self.tmp, 5)
        self.assertEqual(res["oserror"], "no such file")
        self.assertEqual(res["duration"], 3.5)

    def test_real_duration_drives_deadline_class_containment(self):
        # END-TO-END wire: real `_review_spawn_once` measures each attempt at
        # 300s wall-clock (monotonic advances +300 per call) against a 100s
        # deadline — a CLI-self-resolved hang. The wrapper must apply the
        # TIMEOUT policy (capped second chance + breaker), NOT the full
        # 3-attempt transient schedule the pre-fix code re-paid per agent.
        state = {"t": 0.0}

        def mono():
            state["t"] += 300.0
            return state["t"]

        self.mod.time.monotonic = mono
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return types.SimpleNamespace(
                returncode=1, stdout=b"",
                stderr=b"TypeError: fetch failed: connect ETIMEDOUT")

        self.mod.subprocess.run = fake_run
        out = self.mod._review_invoke_agent(
            "reviewer:security", "agentware-adversarial-reviewer", "PROMPT",
            self.cfg, timeout=100, stream=io.StringIO())
        self.assertEqual(out, "")
        self.assertEqual(len(calls),
                         self.mod.REVIEW_SPAWN_TIMEOUT_RETRIES + 1,
                         "a slow CLI-resolved hang measured by the REAL seam "
                         "must be contained by the timeout policy")
        self.assertEqual(self.sleeps, [self.mod.REVIEW_SPAWN_BACKOFF_BASE])
        self.assertTrue(self.mod._REVIEW_TIMEOUT_BREAKER["tripped"])

class InvokeAgentCommandContractTests(unittest.TestCase):
    """Remediation round 5 (test-quality) — bind `_review_invoke_agent`'s
    spawn command/environment construction. Task 2 claimed 'byte-identical
    external behavior', yet nothing asserted `--agent <persona>`,
    `--dangerously-skip-permissions`, `--model <cfg model>`, prompt-as-last-
    arg, or the CI/AGENTWARE_REVIEW_EFFORT env injection — the exact mechanism
    that applies the pinned Opus/max adversarial persona. A regression here
    (e.g. dropping the `--model` pair) silently weakened the governance gate
    with a 100% green suite."""

    def setUp(self):
        self.mod = load_cli()
        self._orig_spawn = self.mod._review_spawn_once
        self._env_cli = os.environ.get("AGENTWARE_CLI")
        os.environ.pop("AGENTWARE_CLI", None)

    def tearDown(self):
        self.mod._review_spawn_once = self._orig_spawn
        if self._env_cli is None:
            os.environ.pop("AGENTWARE_CLI", None)
        else:
            os.environ["AGENTWARE_CLI"] = self._env_cli

    def _capture(self):
        captured = {}

        def fake(cmd, env, cwd, timeout):
            captured.update(cmd=list(cmd), env=dict(env), cwd=cwd,
                            timeout=timeout)
            return {"stdout": "OK", "returncode": 0, "stderr_tail": "",
                    "stderr_transient": False, "timed_out": False,
                    "oserror": None, "duration": 0.1}

        self.mod._review_spawn_once = fake
        return captured

    def test_cmd_pins_persona_flags_model_and_prompt_last(self):
        cap = self._capture()
        out = self.mod._review_invoke_agent(
            "reviewer:correctness", "agentware-adversarial-reviewer",
            "AUDIT PROMPT", {"model": "opus", "effort": "max"})
        self.assertEqual(out, "OK")
        cmd = cap["cmd"]
        # Mirrors run_agent()'s claude branch exactly:
        #   claude -p --agent <persona> --dangerously-skip-permissions
        #   [--model M] <prompt>
        self.assertEqual(cmd[0], "claude")
        self.assertEqual(cmd[1], "-p")
        i = cmd.index("--agent")
        self.assertEqual(cmd[i + 1], "agentware-adversarial-reviewer",
                         "the adversarial persona pin must ride --agent")
        self.assertIn("--dangerously-skip-permissions", cmd)
        m = cmd.index("--model")
        self.assertEqual(cmd[m + 1], "opus",
                         "the pinned review model must ride --model")
        self.assertEqual(cmd[-1], "AUDIT PROMPT",
                         "the prompt must be the LAST argv element")
        self.assertEqual(cmd.count("AUDIT PROMPT"), 1)

    def test_env_injects_ci_flags_and_review_effort(self):
        cap = self._capture()
        self.mod._review_invoke_agent(
            "critic", "agentware-adversarial-reviewer", "PROMPT",
            {"model": "opus", "effort": "max"})
        env = cap["env"]
        self.assertEqual(env["CI"], "true")
        self.assertEqual(env["npm_config_yes"], "true")
        self.assertEqual(env["HOMEBREW_NO_AUTO_UPDATE"], "1")
        self.assertEqual(env["AGENTWARE_REVIEW_EFFORT"], "max",
                         "effort has no CLI flag — the env var is the only "
                         "spawn-side carrier and must be set")

    def test_no_model_flag_and_no_effort_env_when_cfg_omits_them(self):
        cap = self._capture()
        env_before = os.environ.pop("AGENTWARE_REVIEW_EFFORT", None)
        try:
            self.mod._review_invoke_agent(
                "checkup", "agentware-adversarial-reviewer", "PROMPT", {})
        finally:
            if env_before is not None:
                os.environ["AGENTWARE_REVIEW_EFFORT"] = env_before
        self.assertNotIn("--model", cap["cmd"])
        self.assertNotIn("AGENTWARE_REVIEW_EFFORT", cap["env"])
        self.assertEqual(cap["cmd"][-1], "PROMPT")

    def test_default_timeout_cwd_and_cli_env_override(self):
        cap = self._capture()
        self.mod._review_invoke_agent(
            "verify", "agentware-adversarial-reviewer", "PROMPT",
            {"model": "opus"})
        self.assertEqual(cap["timeout"], self.mod.REVIEW_AGENT_TIMEOUT)
        self.assertEqual(cap["cwd"], self.mod.REPO_ROOT)
        os.environ["AGENTWARE_CLI"] = "/tmp/custom-agent-cli"
        self.mod._review_invoke_agent(
            "verify", "agentware-adversarial-reviewer", "PROMPT", {})
        self.assertEqual(cap["cmd"][0], "/tmp/custom-agent-cli")
