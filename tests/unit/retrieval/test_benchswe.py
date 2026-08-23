"""Owning unit file for `agentware_lib.retrieval.bench.benchswe` — task 99 w10.

The module had no owning file before this one. Its pure scorers were reachable
from an e2e row, but `cmd_bench_swe` — the only caller in the tree that spawns
real agents — was not asserted anywhere below that seam, and its live arm is a
triple-nested loop whose NESTING ORDER is the measurement design.

What is asserted here is the module's PROMISE, not its shape:

  * THE PASS RATE IS PER SEED; TURNS AND TOKENS ARE PER RUN. Collapsing the two
    levels produces a number that looks like a pass rate and is not one. The
    fixture makes the two disagree — different tasks pass on different seeds —
    so an averaging change is visible rather than arithmetically invisible.
  * A DEGRADED RUN IS STILL GRADED. An agent that times out, or whose binary is
    missing, scores zero turns and zero tokens and is then graded ANYWAY: an
    abstain task passes by leaving the repo alone, so a timed-out agent can
    legitimately pass it, and swallowing the run would drop it from the
    denominator.
  * THE ARM IS THE INDEPENDENT VARIABLE. `no-context` spawns the agent with
    AGENTWARE_KNOWLEDGE_DIR removed from its environment. That is the whole
    experiment, and it is asserted on the env dict the spawn actually received.
  * THE SCRATCH TREE IS ALWAYS REMOVED. The task fixtures are package data; the
    run works on a copy and cleans up on the failure path too.
  * A DELTA OF EXACTLY ZERO IS A RESULT, NOT AN ABSENCE. The report prints on
    `is not None`, so "the two arms scored identically" — the most interesting
    negative result this benchmark can produce — is not silently dropped.

Stdlib-only, hermetic: the frozen `tests/fixtures/swe_tasks` suite, the agent
spawn and the grader injected at the module's own seams (so the PARENT under
test is always the real one), no network, no agent process.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.retrieval.bench.benchswe as benchswe  # noqa: E402

SUITE_DIR = os.path.join(TESTS_DIR, "fixtures", "swe_tasks")


class _Args(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


@contextlib.contextmanager
def _captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


class _Proc(object):
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _SubprocessShim(object):
    """Stands in for the `subprocess` MODULE on `benchswe` alone.

    Patching `subprocess.run` itself would also redirect `ledger.git_info()`,
    which shells git three statements after the spawn and OUTSIDE its try — so
    an injected TimeoutExpired would escape from a place the product never
    raises it, and the test would be asserting a harness artefact. Rebinding the
    NAME on the module under test is the narrow seam.
    """

    def __init__(self, run):
        self.run = run
        self.PIPE = subprocess.PIPE
        self.TimeoutExpired = subprocess.TimeoutExpired


class _Spawn(object):
    """Records every spawn and answers with a canned result or an exception."""

    def __init__(self, stdout="", raises=None):
        self.calls = []
        self.stdout = stdout
        self.raises = raises

    def __call__(self, cmd, **kw):
        self.calls.append({"cmd": cmd, "cwd": kw.get("cwd"),
                           "env": kw.get("env") or {},
                           "timeout": kw.get("timeout")})
        if self.raises is not None:
            raise self.raises
        return _Proc(self.stdout)


class _SweSeamMixin(object):
    """Injects the spawn + the grader, and always restores them."""

    def _inject(self, spawn, passed=True, score=None):
        real_sp = benchswe.subprocess
        real_score = benchswe.swe_score_task
        real_passed = benchswe.swe_task_passed
        self.addCleanup(setattr, benchswe, "subprocess", real_sp)
        self.addCleanup(setattr, benchswe, "swe_score_task", real_score)
        self.addCleanup(setattr, benchswe, "swe_task_passed", real_passed)
        benchswe.subprocess = _SubprocessShim(spawn)
        benchswe.swe_score_task = (
            score if score is not None else (lambda *a, **kw: {"stub": True}))
        if callable(passed):
            benchswe.swe_task_passed = passed
        else:
            benchswe.swe_task_passed = lambda *a, **kw: passed


class SweSuiteLoaderTest(unittest.TestCase):
    """`swe_load_suite` — the frozen suite's own validation."""

    def test_benchswe_loads_the_shipped_suite(self):
        data = benchswe.swe_load_suite(SUITE_DIR)
        self.assertEqual(data["suite"], "swe-task")
        self.assertTrue(data["tasks"])

    def test_benchswe_every_shipped_task_has_its_four_artefacts(self):
        """The loader validates them, so a suite that loads is a suite whose
        tasks are all runnable — this asserts the shipped one really is."""
        data = benchswe.swe_load_suite(SUITE_DIR)
        for task_id in data["tasks"]:
            base = os.path.join(SUITE_DIR, task_id)
            self.assertTrue(os.path.isdir(os.path.join(base, "repo")), task_id)
            for name in ("task.md", "test_hidden.py", "meta.json"):
                self.assertTrue(os.path.isfile(os.path.join(base, name)),
                                "%s/%s" % (task_id, name))

    def test_benchswe_refuses_a_suite_with_the_wrong_kind(self):
        root = tempfile.mkdtemp(prefix="swe-badkind-")
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, "suite.json"), "w", encoding="utf-8") as f:
            json.dump({"suite": "not-swe-task", "tasks": ["x"]}, f)
        with self.assertRaises(ValueError) as ctx:
            benchswe.swe_load_suite(root)
        self.assertIn("must be 'swe-task'", str(ctx.exception))

    def test_benchswe_refuses_a_suite_with_no_tasks(self):
        root = tempfile.mkdtemp(prefix="swe-notasks-")
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, "suite.json"), "w", encoding="utf-8") as f:
            json.dump({"suite": "swe-task", "tasks": []}, f)
        with self.assertRaises(ValueError) as ctx:
            benchswe.swe_load_suite(root)
        self.assertIn("non-empty list", str(ctx.exception))

    def test_benchswe_names_the_task_and_the_artefact_that_is_missing(self):
        root = tempfile.mkdtemp(prefix="swe-partial-")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "t1", "repo"))
        with open(os.path.join(root, "suite.json"), "w", encoding="utf-8") as f:
            json.dump({"suite": "swe-task", "tasks": ["t1"]}, f)
        with self.assertRaises(ValueError) as ctx:
            benchswe.swe_load_suite(root)
        self.assertIn("t1", str(ctx.exception))
        self.assertIn("task.md", str(ctx.exception))


class SweOneRunTest(_SweSeamMixin, unittest.TestCase):
    """`_run_swe_task` — one task, one seed, one arm."""

    def _run(self, spawn, arm="with-context", passed=True):
        self._inject(spawn, passed=passed)
        task_id = benchswe.swe_load_suite(SUITE_DIR)["tasks"][0]
        return benchswe._run_swe_task(SUITE_DIR, task_id, 0, arm)

    def test_benchswe_a_successful_run_reports_one_turn_and_its_tokens(self):
        """Tokens are estimated from the transcript length at 4 chars each; a
        40-char stdout is 10 tokens."""
        passed, turns, tokens = self._run(_Spawn(stdout="x" * 40))
        self.assertTrue(passed)
        self.assertEqual(turns, 1)
        self.assertEqual(tokens, 10)

    def test_benchswe_a_timeout_scores_zero_and_is_still_graded(self):
        """The degradation that matters. An abstain task passes by leaving the
        repo alone, so a timed-out agent can legitimately pass — swallowing the
        run instead would drop it from the denominator."""
        passed, turns, tokens = self._run(
            _Spawn(raises=subprocess.TimeoutExpired("claude", 1)))
        self.assertEqual((turns, tokens), (0, 0))
        self.assertTrue(passed)

    def test_benchswe_a_missing_agent_binary_scores_zero_and_is_still_graded(self):
        passed, turns, tokens = self._run(_Spawn(raises=OSError("no claude")))
        self.assertEqual((turns, tokens), (0, 0))
        self.assertTrue(passed)

    def test_benchswe_the_grader_decides_the_pass_not_the_spawn(self):
        """A spawn that succeeded and a grader that says no must FAIL. The two
        are separate verdicts and only the grader's counts."""
        passed, _turns, _tokens = self._run(_Spawn(stdout="ok"), passed=False)
        self.assertFalse(passed)

    def test_benchswe_no_context_arm_strips_the_knowledge_dir_from_the_env(self):
        """THE INDEPENDENT VARIABLE. Asserted on the env the spawn actually
        received, not on the arm name — an arm that forgot to strip it would
        run two identical arms and report a delta of zero as a finding."""
        prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = "/scratch/kb"
        try:
            spawn = _Spawn()
            self._run(spawn, arm="no-context")
            self.assertNotIn("AGENTWARE_KNOWLEDGE_DIR", spawn.calls[0]["env"])
        finally:
            if prev is None:
                os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
            else:
                os.environ["AGENTWARE_KNOWLEDGE_DIR"] = prev

    def test_benchswe_with_context_arm_keeps_the_knowledge_dir(self):
        prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = "/scratch/kb"
        try:
            spawn = _Spawn()
            self._run(spawn, arm="with-context")
            self.assertEqual(spawn.calls[0]["env"]["AGENTWARE_KNOWLEDGE_DIR"],
                             "/scratch/kb")
        finally:
            if prev is None:
                os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
            else:
                os.environ["AGENTWARE_KNOWLEDGE_DIR"] = prev

    def test_benchswe_the_agent_runs_in_a_copy_never_the_fixture(self):
        """The task fixtures are package data. The spawn's cwd must be under a
        temp dir, not under the suite — a run that edited the fixture would
        change what every later seed is measuring."""
        spawn = _Spawn()
        self._run(spawn)
        self.assertNotIn(SUITE_DIR, spawn.calls[0]["cwd"])

    def test_benchswe_the_scratch_tree_is_removed_afterwards(self):
        spawn = _Spawn()
        self._run(spawn)
        self.assertFalse(os.path.exists(spawn.calls[0]["cwd"]))

    def test_benchswe_the_scratch_tree_is_removed_even_when_grading_raises(self):
        """The cleanup is in a `finally`, so a grader that blows up does not
        leave a copied repo behind on every run."""
        self._inject(_Spawn(), score=self._boom)
        task_id = benchswe.swe_load_suite(SUITE_DIR)["tasks"][0]
        before = set(os.listdir(tempfile.gettempdir()))
        with self.assertRaises(RuntimeError):
            benchswe._run_swe_task(SUITE_DIR, task_id, 0, "with-context")
        leaked = [n for n in set(os.listdir(tempfile.gettempdir())) - before
                  if n.startswith("swe-run-")]
        self.assertEqual(leaked, [])

    @staticmethod
    def _boom(*a, **kw):
        raise RuntimeError("grader exploded")

    def test_benchswe_the_spawn_carries_the_tasks_own_timeout(self):
        """Each task declares its own budget in meta.json; a runner that used
        one global timeout would either cut long tasks short or let a hung
        agent stall the whole sweep.

        Driven on the ONE shipped task whose declared timeout DIFFERS from the
        120s fallback, and that choice is the whole test: the first task in the
        suite declares exactly 120, so a runner that ignored meta.json entirely
        would spawn it with the identical value and this assertion would pass
        on a guard that does nothing. Measured by the wave-10 non-vacuity sweep,
        which reported the row GREEN until the fixture was re-aimed."""
        spawn = _Spawn()
        task_id = "feature-csv-parser"
        meta = benchswe.swe_load_task_meta(SUITE_DIR, task_id)
        self.assertNotEqual(meta.get("timeout"), 120,
                            "this fixture no longer discriminates; pick a task "
                            "whose meta.json timeout is not the 120s fallback")
        self._inject(spawn, passed=True)
        benchswe._run_swe_task(SUITE_DIR, task_id, 0, "with-context")
        self.assertEqual(spawn.calls[0]["timeout"], meta["timeout"])


class SweArmTest(_SweSeamMixin, unittest.TestCase):
    """`_run_swe_arm` — the seed/task nesting, which IS the measurement."""

    def setUp(self):
        self.suite = {"tasks": ["fix-off-by-one", "fix-null-guard"]}

    def test_benchswe_arm_reports_one_pass_rate_per_seed(self):
        self._inject(_Spawn(stdout="x" * 8), passed=True)
        stats = benchswe._run_swe_arm(self.suite, SUITE_DIR, 3, "with-context")
        self.assertEqual(stats["pass_rates"], [1.0, 1.0, 1.0])

    def test_benchswe_arm_rate_is_per_seed_not_per_run(self):
        """THE NESTING ROW. One of the two tasks passes and the other does not,
        on every seed — so the per-seed rate is 0.5 and there are as many rates
        as SEEDS. A runner that averaged over runs would report a single 0.5,
        and `swe_aggregate_seeds` would then have no spread to report."""
        self._inject(_Spawn(),
                     passed=lambda result, meta: result["task"] == "pass")
        benchswe.swe_score_task = lambda task_dir, repo: {
            "task": "pass" if task_dir.endswith("fix-off-by-one") else "fail"}
        stats = benchswe._run_swe_arm(self.suite, SUITE_DIR, 2, "with-context")
        self.assertEqual(stats["pass_rates"], [0.5, 0.5])

    def test_benchswe_arm_averages_turns_and_tokens_over_every_run(self):
        """Turns and tokens are per RUN, not per seed: 2 tasks x 2 seeds = 4
        runs, each 1 turn, so the mean is 1.0 — and it would be 2.0 if the
        denominator were seeds."""
        self._inject(_Spawn(stdout="x" * 40), passed=True)
        stats = benchswe._run_swe_arm(self.suite, SUITE_DIR, 2, "with-context")
        self.assertEqual(stats["turns_mean"], 1.0)
        self.assertEqual(stats["tokens_mean"], 10.0)

    def test_benchswe_arm_with_no_tasks_scores_zero_rather_than_dividing(self):
        """A seed that ran nothing is 0.0, not a ZeroDivisionError."""
        self._inject(_Spawn(), passed=True)
        stats = benchswe._run_swe_arm({"tasks": []}, SUITE_DIR, 2, "arm")
        self.assertEqual(stats["pass_rates"], [0.0, 0.0])
        self.assertEqual(stats["turns_mean"], 0)
        self.assertEqual(stats["tokens_mean"], 0)

    def test_benchswe_arm_runs_every_task_for_every_seed(self):
        spawn = _Spawn()
        self._inject(spawn, passed=True)
        benchswe._run_swe_arm(self.suite, SUITE_DIR, 3, "with-context")
        self.assertEqual(len(spawn.calls), 6)


class SweRenderersTest(unittest.TestCase):
    """The two human reports."""

    def _render(self, fn, *a):
        with _captured() as (out, _err):
            fn(*a)
        return out.getvalue()

    def test_benchswe_dry_run_report_states_the_run_arithmetic(self):
        plan = benchswe.swe_build_dry_run_plan(SUITE_DIR, 3, ["a", "b"])
        text = self._render(benchswe._print_swe_dry_run, plan, 3, ["a", "b"])
        self.assertIn("%d runs" % plan["total_runs"], text)
        self.assertIn("arms: a, b", text)

    def test_benchswe_dry_run_report_lists_every_task(self):
        plan = benchswe.swe_build_dry_run_plan(SUITE_DIR, 1, ["a"])
        text = self._render(benchswe._print_swe_dry_run, plan, 1, ["a"])
        for t in plan["tasks"]:
            self.assertIn(t["id"], text)

    def test_benchswe_report_prints_the_spread_not_just_the_mean(self):
        """A mean pass rate over five seeds hides a bimodal result; min and max
        are what say whether the benchmark is stable."""
        row = {"arms": {"with-context": {"mean": 0.6, "min": 0.2, "max": 1.0}},
               "delta": 0.25}
        text = self._render(benchswe._print_swe_report, row, {"tasks": ["t"]},
                            5, ["with-context"])
        self.assertIn("mean=0.60", text)
        self.assertIn("min=0.20", text)
        self.assertIn("max=1.00", text)

    def test_benchswe_report_prints_a_zero_delta(self):
        """`is not None`, not truthiness. Two arms scoring identically is the
        most interesting NEGATIVE result this benchmark produces, and 0.0 is
        falsy — a truthiness check drops exactly that finding."""
        row = {"arms": {}, "delta": 0.0}
        text = self._render(benchswe._print_swe_report, row, {"tasks": []}, 1,
                            ["a"])
        self.assertIn("delta: +0.0000", text)

    def test_benchswe_report_omits_the_delta_when_there_is_none(self):
        """A single-arm run has nothing to compare, so there is no delta line
        rather than a delta of zero that reads like a measurement."""
        row = {"arms": {}, "delta": None}
        text = self._render(benchswe._print_swe_report, row, {"tasks": []}, 1,
                            ["a"])
        self.assertNotIn("delta", text)


class SweHandlerTest(_SweSeamMixin, unittest.TestCase):
    """`cmd_bench_swe` — the guards, the dry run and the ledger append."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="benchswe-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(self._restore)

    def _restore(self):
        if self.prev is None:
            os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        else:
            os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.prev

    def _run(self, **kw):
        kw.setdefault("suite", SUITE_DIR)
        kw.setdefault("seeds", 1)
        kw.setdefault("arms", "with-context")
        kw.setdefault("dry_run", True)
        with _captured() as (out, err):
            rc = benchswe.cmd_bench_swe(_Args(**kw))
        return rc, out.getvalue(), err.getvalue()

    def test_benchswe_refuses_an_empty_arm_list(self):
        """`" , "` splits to nothing once blanks are dropped — a runner that
        accepted it would do zero work and exit 0."""
        rc, _out, err = self._run(arms=" , ")
        self.assertEqual(rc, 1)
        self.assertIn("--arms must specify at least one arm", err)

    def test_benchswe_refuses_a_missing_suite_directory(self):
        rc, _out, err = self._run(suite="/nonexistent/suite")
        self.assertEqual(rc, 1)
        self.assertIn("suite directory not found", err)

    def test_benchswe_refuses_an_invalid_suite_and_says_why(self):
        root = tempfile.mkdtemp(prefix="benchswe-bad-")
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, "suite.json"), "w", encoding="utf-8") as f:
            json.dump({"suite": "nope", "tasks": []}, f)
        rc, _out, err = self._run(suite=root)
        self.assertEqual(rc, 1)
        self.assertIn("invalid suite", err)

    def test_benchswe_refuses_the_arms_before_it_looks_at_the_suite(self):
        """ORDER: an invocation with no arms AND a missing suite reports the
        arms. A reordering changes the message an operator debugs against."""
        rc, _out, err = self._run(arms="", suite="/nonexistent/suite")
        self.assertEqual(rc, 1)
        self.assertIn("--arms", err)
        self.assertNotIn("suite directory", err)

    def test_benchswe_dry_run_spawns_nothing(self):
        """The whole point of --dry-run: it must not reach an agent."""
        spawn = _Spawn()
        self._inject(spawn)
        rc, out, _err = self._run(seeds=2, arms="a,b")
        self.assertEqual(rc, 0)
        self.assertEqual(spawn.calls, [])
        self.assertIn("[dry-run]", out)

    def test_benchswe_dry_run_json_is_the_plan(self):
        rc, out, _err = self._run(format="json", seeds=2, arms="a,b")
        self.assertEqual(rc, 0)
        plan = json.loads(out)
        self.assertEqual(plan["total_runs"], len(plan["tasks"]) * 2 * 2)

    def test_benchswe_dry_run_writes_no_ledger_row(self):
        self._run()
        self.assertFalse(os.path.isfile(
            os.path.join(self.kdir, "benchmarks", "history.jsonl")))

    def test_benchswe_live_run_appends_exactly_one_ledger_row(self):
        self._inject(_Spawn(stdout="x" * 8), passed=True)
        rc, _out, _err = self._run(dry_run=False, seeds=1,
                                   arms="with-context,no-context")
        self.assertEqual(rc, 0)
        path = os.path.join(self.kdir, "benchmarks", "history.jsonl")
        with open(path, encoding="utf-8") as f:
            rows = [ln for ln in f if ln.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])["mode"], "swe-task")

    def test_benchswe_live_run_records_every_arm(self):
        self._inject(_Spawn(stdout="x" * 8), passed=True)
        _rc, out, _err = self._run(dry_run=False, seeds=1, format="json",
                                   arms="with-context,no-context")
        row = json.loads(out)
        self.assertEqual(sorted(row["arms"]), ["no-context", "with-context"])

    def test_benchswe_live_run_still_reports_with_no_knowledge_dir(self):
        """The ledger append is best-effort: an operator running the benchmark
        outside a configured workspace still gets their numbers."""
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = os.path.join(
            self.kdir, "does-not-exist")
        self._inject(_Spawn(stdout="x"), passed=True)
        rc, out, _err = self._run(dry_run=False, seeds=1)
        self.assertEqual(rc, 0)
        self.assertIn("bench swe: completed", out)


class BenchSweSurfaceTest(unittest.TestCase):

    def test_benchswe_exports_name_every_helper(self):
        for name in ("swe_load_suite", "swe_load_task_meta", "swe_score_task",
                     "swe_task_passed", "swe_aggregate_arm",
                     "swe_aggregate_seeds", "swe_regression_decision",
                     "swe_build_dry_run_plan", "swe_build_ledger_row",
                     "_print_swe_dry_run", "_run_swe_task", "_run_swe_arm",
                     "_print_swe_report", "cmd_bench_swe"):
            self.assertIn(name, benchswe._EXPORTS)
            self.assertTrue(hasattr(benchswe, name), name)

    def test_benchswe_regression_tolerance_blocks_below_not_above(self):
        """The published constant is a DROP tolerance: new < old - tolerance
        blocks. A run that improves is never blocked."""
        self.assertGreater(benchswe.SWE_REGRESSION_TOLERANCE, 0.0)
        self.assertEqual(
            benchswe.swe_regression_decision([0.9], [0.1])["verdict"], "BLOCK")
        self.assertEqual(
            benchswe.swe_regression_decision([0.1], [0.9])["verdict"], "PASS")

    def test_benchswe_a_drop_inside_the_tolerance_does_not_block(self):
        """The tolerance is what separates noise from a regression: a drop of
        exactly the tolerance passes, one hair beyond it blocks."""
        tol = benchswe.SWE_REGRESSION_TOLERANCE
        self.assertEqual(
            benchswe.swe_regression_decision([1.0], [1.0 - tol])["verdict"],
            "PASS")
        self.assertEqual(
            benchswe.swe_regression_decision(
                [1.0], [1.0 - tol - 0.01])["verdict"], "BLOCK")


if __name__ == "__main__":
    unittest.main()
