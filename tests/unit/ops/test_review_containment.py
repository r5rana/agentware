"""DeadlineClassContainmentTests, BlackholeFanoutContainmentTests, NonUtf8DiffResolutionTests -- split from `test_review_orchestrator` (task 58).

See `tests.unit.ops._review_orchestrator_support` for the
shared header.
"""

from tests.unit.ops._review_orchestrator_support import (  # noqa: F401
    argparse, contextlib, io, json, load_cli, os, pinned_review_env,
    shutil, tempfile, unittest)


class DeadlineClassContainmentTests(unittest.TestCase):
    """Remediation round 4 (novel-input-edge-cases) — the blackhole breaker
    must observe DEADLINE expiries the claude CLI resolves ITSELF (connect
    ETIMEDOUT / internal request timeout exiting nonzero after burning most of
    the deadline), not only subprocess-level TimeoutExpired. Without this, the
    most common real 'Opus unreachable' shape (CLI stalls ~600s, exits 1 with
    'fetch failed ... ETIMEDOUT', never hitting the 900s subprocess deadline)
    classified as a FAST stderr transient and re-paid the full 3-attempt
    schedule per agent with no process-wide memory — hours of wall-clock
    instead of the documented ~one extra deadline total."""

    def setUp(self):
        self.mod = load_cli()
        self._orig_spawn = self.mod._review_spawn_once
        self._orig_sleep = self.mod.REVIEW_SLEEP
        self.sleeps = []
        self.mod.REVIEW_SLEEP = self.sleeps.append
        self.mod._REVIEW_TIMEOUT_BREAKER["tripped"] = False
        self.cfg = {"model": "opus", "effort": "max"}

    def tearDown(self):
        self.mod._review_spawn_once = self._orig_spawn
        self.mod.REVIEW_SLEEP = self._orig_sleep
        self.mod._REVIEW_TIMEOUT_BREAKER["tripped"] = False

    def _slow_etimedout(self, duration):
        # The live-reproduced shape: nonzero exit, ETIMEDOUT stderr, wall-clock
        # measured by _review_spawn_once — NOT a subprocess timeout.
        return {"stdout": "", "returncode": 1,
                "stderr_tail": "TypeError: fetch failed: connect ETIMEDOUT",
                "stderr_transient": True, "timed_out": False, "oserror": None,
                "duration": duration}

    def _install_always(self, result):
        calls = []

        def fake(cmd, env, cwd, timeout):
            calls.append({"timeout": timeout})
            return dict(result)

        self.mod._review_spawn_once = fake
        return calls

    def _invoke(self, role="reviewer:security"):
        stream = io.StringIO()
        out = self.mod._review_invoke_agent(
            role, "agentware-adversarial-reviewer", "PROMPT", self.cfg,
            stream=stream)
        return out, stream.getvalue()

    def test_slow_cli_resolved_hang_gets_timeout_policy_not_full_schedule(self):
        # duration ~600s of a 900s deadline: this IS a deadline expiry — one
        # second chance, then terminal + breaker, never the full 3x schedule.
        slow = self._slow_etimedout(
            self.mod.REVIEW_SLOW_FAILURE_FRACTION
            * self.mod.REVIEW_AGENT_TIMEOUT + 1.0)
        calls = self._install_always(slow)
        out, _ = self._invoke()
        self.assertEqual(out, "")
        self.assertEqual(len(calls),
                         self.mod.REVIEW_SPAWN_TIMEOUT_RETRIES + 1,
                         "a CLI-self-resolved hang must get the capped "
                         "timeout schedule, not the full transient schedule")
        self.assertEqual(self.sleeps, [self.mod.REVIEW_SPAWN_BACKOFF_BASE])
        self.assertTrue(self.mod._REVIEW_TIMEOUT_BREAKER["tripped"],
                        "two full-deadline-class expiries in one invocation "
                        "must trip the process-wide breaker")

    def test_breaker_contains_slow_hangs_across_the_rest_of_the_fanout(self):
        slow = self._slow_etimedout(
            self.mod.REVIEW_SLOW_FAILURE_FRACTION
            * self.mod.REVIEW_AGENT_TIMEOUT + 1.0)
        self._install_always(slow)
        self._invoke()  # trips the breaker (asserted above)
        calls = self._install_always(slow)
        del self.sleeps[:]
        out, _ = self._invoke(role="reviewer:perf")
        self.assertEqual(out, "")
        self.assertEqual(len(calls), 1,
                         "after the breaker trips, a later agent's slow hang "
                         "is terminal on its FIRST attempt")
        self.assertEqual(self.sleeps, [])

    def test_fast_etimedout_blip_keeps_the_full_transient_schedule(self):
        # Guard against over-containment: a FAST connection blip (seconds, not
        # a burned deadline) stays on the ordinary transient schedule and
        # never touches the breaker.
        calls = self._install_always(self._slow_etimedout(2.0))
        out, _ = self._invoke()
        self.assertEqual(out, "")
        self.assertEqual(len(calls), self.mod.REVIEW_SPAWN_RETRIES + 1)
        base = self.mod.REVIEW_SPAWN_BACKOFF_BASE
        self.assertEqual(self.sleeps, [base * 1, base * 2])
        self.assertFalse(self.mod._REVIEW_TIMEOUT_BREAKER["tripped"])

    def test_classifier_deadline_class_is_pure_and_bounded(self):
        is_dc = self.mod._review_is_deadline_class
        frac = self.mod.REVIEW_SLOW_FAILURE_FRACTION
        self.assertTrue(is_dc({"timed_out": True}, 900))
        self.assertTrue(is_dc({"duration": frac * 900}, 900))
        self.assertFalse(is_dc({"duration": frac * 900 - 1}, 900))
        # Hand-built stub results without a duration are NEVER deadline-class
        # (existing hermetic suites must keep their fast-transient semantics).
        self.assertFalse(is_dc({"returncode": 1}, 900))
        self.assertFalse(is_dc({}, 900))
        self.assertFalse(is_dc(None, 900))
        # bool is an int subclass — True must not read as a 1-second duration.
        self.assertFalse(is_dc({"duration": True}, 900))

class BlackholeFanoutContainmentTests(unittest.TestCase):
    """Remediation round 4 (test-quality) — lock the HEADLINE containment
    guarantee at the level it is written about: a blackholed audit FAN-OUT
    (every spawn times out) costs ~one extra deadline TOTAL — the first
    invocation burns 2 deadlines (one second chance) and trips the breaker;
    every later agent in the same fan-out is terminal on its first attempt.
    Drives the REAL audit loop / cmd_review entrypoint with ONLY the raw
    `_review_spawn_once` seam stubbed, so a future refactor that resets or
    re-scopes `_REVIEW_TIMEOUT_BREAKER` per reviewer/round breaks THIS test
    instead of restoring 2-full-deadline cost per hung agent."""

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

    def _install_blackhole(self):
        """Every spawn attempt hits the subprocess deadline."""
        calls = []

        def fake(cmd, env, cwd, timeout):
            calls.append({"timeout": timeout})
            return {"stdout": "", "returncode": None, "stderr_tail": "",
                    "stderr_transient": False, "timed_out": True,
                    "oserror": None}

        self.mod._review_spawn_once = fake
        return calls

    def test_blackholed_fanout_costs_one_extra_deadline_total(self):
        calls = self._install_blackhole()
        buf_err = io.StringIO()
        with contextlib.redirect_stderr(buf_err):
            result = self.mod._review_run_audit(
                "DIFF", "CRITERIA", self.cfg, stream=buf_err)
        total_agents = len(self.mod.REVIEW_DIMENSION_KEYS) + 1  # + critic
        # THE guarantee: agents + exactly ONE extra deadline (the first
        # invocation's single second chance) — not 2 deadlines per agent.
        self.assertEqual(len(calls), total_agents + 1,
                         "a blackholed fan-out must cost ~one extra deadline "
                         "TOTAL: %d attempts for %d agents"
                         % (len(calls), total_agents))
        # Exactly one backoff (the one retry) — zero real sleep either way.
        self.assertEqual(self.sleeps, [self.mod.REVIEW_SPAWN_BACKOFF_BASE])
        self.assertTrue(self.mod._REVIEW_TIMEOUT_BREAKER["tripped"])
        # And the gate still fails CLOSED.
        self.assertEqual(result["reviewers_ok"], 0)
        self.assertTrue(result["audit_incomplete"])

    def test_blackholed_fanout_blocks_exit3_with_containment_e2e(self):
        calls = self._install_blackhole()
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
        total_agents = len(self.mod.REVIEW_DIMENSION_KEYS) + 1
        self.assertEqual(len(calls), total_agents + 1)
        self.assertEqual(self.sleeps, [self.mod.REVIEW_SPAWN_BACKOFF_BASE])
        # The per-agent diagnostics name the hung reviewers on the stream.
        self.assertIn("timed out after", err)
        self.assertIn("INCOMPLETE", err)

class NonUtf8DiffResolutionTests(unittest.TestCase):
    """Remediation round 6 (novel-input-edge-cases) — the round-5 claim 'no
    strict text-mode decode can crash the gate' was refuted on the gate's
    PRIMARY untrusted input: `_review_resolve_diff` -> `_git(..., text=True)`
    whose STRICT decode raised UnicodeDecodeError inside communicate() for a
    diff touching any non-UTF-8 (latin-1) file — crashing cmd_review (exit 1,
    misread by the bash gate as 'confirmed blocking findings') BEFORE the
    audit started. `_git` now decodes with errors='replace', and the
    --diff-file/--acceptance-file reads are equally tolerant. Runs REAL git
    over a throwaway temp repo — never the operator's checkout."""

    def setUp(self):
        self.mod = load_cli()
        self._orig_invoke = self.mod._review_invoke_agent
        self.tmp = tempfile.mkdtemp(prefix="aw-nonutf8-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        for args in (("init",), ("config", "user.email", "t@example.com"),
                     ("config", "user.name", "t")):
            cp = self.mod._git(self.repo, *args)
            if cp.returncode != 0:
                self.skipTest("git unavailable: %s" % cp.stderr)
        path = os.path.join(self.repo, "latin1.txt")
        with open(path, "wb") as f:
            f.write(b"caf\xe9 before\n")
        self.mod._git(self.repo, "add", "latin1.txt")
        self.mod._git(self.repo, "commit", "-m", "seed", "--no-gpg-sign")
        with open(path, "wb") as f:
            f.write(b"caf\xe9 after \xff\n")  # still no NUL: git diffs as text

    def tearDown(self):
        self.mod._review_invoke_agent = self._orig_invoke
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_resolve_diff_over_latin1_file_never_raises(self):
        # The live repro: this call used to raise UnicodeDecodeError out of
        # cmd_review before any reviewer spawned.
        diff = self.mod._review_resolve_diff("HEAD", cwd=self.repo)
        self.assertIn("latin1.txt", diff)
        self.assertIn("�", diff,
                      "undecodable bytes must degrade to U+FFFD, not crash")

    def test_git_helper_decodes_nonutf8_with_replacement(self):
        cp = self.mod._git(self.repo, "diff", "HEAD")
        self.assertEqual(cp.returncode, 0)
        self.assertIn("�", cp.stdout)

    def test_cmd_review_diff_file_with_latin1_bytes_never_crashes(self):
        # Entry-point lock: a --diff-file carrying a latin-1 byte used to
        # raise UnicodeDecodeError (a ValueError — NOT caught by the OSError
        # handler) out of cmd_review.
        diff_file = os.path.join(self.tmp, "diff.patch")
        with open(diff_file, "wb") as f:
            f.write(b"diff --git a/x b/x\n+caf\xe9\n")
        clean = json.dumps({"findings": [], "gaps": [],
                            "additional_findings": []})
        self.mod._review_invoke_agent = lambda *a, **k: clean
        args = argparse.Namespace(
            adversarial=True, worklog=None, diff_file=diff_file,
            diff_range=None, acceptance_file=None, format="json")
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with pinned_review_env(), \
                contextlib.redirect_stdout(buf_out), \
                contextlib.redirect_stderr(buf_err):
            rc = self.mod.cmd_review(args)
        self.assertEqual(rc, 0, buf_err.getvalue())

    def test_cmd_review_acceptance_file_with_latin1_bytes_never_crashes(self):
        # Remediation round 7 (test-quality): the --acceptance-file read is a
        # PARALLEL errors='replace' site to the regression-locked --diff-file
        # one — un-locked, a revert to strict UTF-8 decode shipped green and
        # crashed cmd_review (UnicodeDecodeError, exit 1, misread by the bash
        # gate as 'confirmed blocking findings') on one latin-1 criteria byte.
        acc_file = os.path.join(self.tmp, "criteria.md")
        with open(acc_file, "wb") as f:
            f.write(b"# Crit\xe8res\n- the caf\xe9 path must not crash \xff\n")
        clean = json.dumps({"findings": [], "gaps": [],
                            "additional_findings": []})
        self.mod._review_invoke_agent = lambda *a, **k: clean
        args = argparse.Namespace(
            adversarial=True, worklog=None, diff_file=None,
            diff_range=None, acceptance_file=acc_file, format="json")
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with pinned_review_env(), \
                contextlib.redirect_stdout(buf_out), \
                contextlib.redirect_stderr(buf_err):
            rc = self.mod.cmd_review(args)
        self.assertEqual(rc, 0, buf_err.getvalue())
