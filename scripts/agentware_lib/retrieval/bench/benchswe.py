"""agentware_lib.retrieval.bench.benchswe -- SWE-task benchmark DOMAIN LOGIC +
the `bench swe` runner handler (layer: retrieval).

Owns: the frozen SWE-task suite constants (SWE_SUITE_FILE, SWE_DEFAULT_SEEDS,
SWE_REGRESSION_TOLERANCE, SWE_DEFAULT_ARMS) and the deterministic SWE
scoring/aggregation/decision family -- swe_load_suite, swe_load_task_meta,
swe_score_task, _dirs_identical, swe_task_passed, swe_aggregate_arm,
swe_aggregate_seeds, swe_regression_decision, swe_build_dry_run_plan,
swe_build_ledger_row (deterministic given inputs; swe_score_task shells pytest
against a copied repo; no wall-clock, no module-level mutable state) -- PLUS the
`bench swe` CLI handler cmd_bench_swe, the only caller that spawns real agents,
and its four halves: the two renderers _print_swe_dry_run / _print_swe_report and
the live-run engine _run_swe_arm -> _run_swe_task (one arm, one run).

Does NOT own: the `bench scorecard` handler + the `bench` parser registrar
(-> agentware_lib.retrieval.bench.benchcmd); the ledger gold_path redactor and
the `bench redact` / `bench lint` handlers
(-> agentware_lib.retrieval.bench.benchledger); the append-only ledger
(-> agentware_lib.retrieval.ledger, held via the `_ledger` module alias and
referenced by ATTRIBUTE so the bytecode global-resolve gate never walks a foreign
sibling function held in this module's __dict__); the scorecard render/write VIEW
(-> agentware_lib.retrieval.scorecard).

Layer deps: retrieval -> core (config, dates; allowed at module level) + the
sibling retrieval.ledger (by module alias, attribute reads). No knowledge, no
ops/features/cli import. _EXPORTS lists the 14 SWE domain symbols + cmd_bench_swe
and its four halves for the entrypoint by-name facade.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os
import shutil
import subprocess
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
from agentware_lib.retrieval import ledger as _ledger


# --- SWE-task benchmark: frozen hidden-unit-test coding suite -----------------
# Pure scorer + aggregator + regression decision. The statistical runner (which
# spawns real agents) is separate and opt-in. This code is deterministic given
# its inputs and is unit-tested with canned transcripts (no agent spawn).

SWE_SUITE_FILE = "suite.json"
SWE_DEFAULT_SEEDS = 5
SWE_REGRESSION_TOLERANCE = 0.15  # BLOCK when new < old - tolerance
SWE_DEFAULT_ARMS = ["with-context", "no-context"]


def swe_load_suite(suite_dir):
    """Load and validate an SWE-task suite.json. Pure."""
    path = os.path.join(suite_dir, SWE_SUITE_FILE)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("suite") != "swe-task":
        raise ValueError("suite.json 'suite' must be 'swe-task', got: %r"
                         % data.get("suite"))
    if not isinstance(data.get("tasks"), list) or not data["tasks"]:
        raise ValueError("suite.json 'tasks' must be a non-empty list")
    for task_id in data["tasks"]:
        task_dir = os.path.join(suite_dir, task_id)
        for required in ("repo", "task.md", "test_hidden.py", "meta.json"):
            p = os.path.join(task_dir, required)
            if required == "repo":
                if not os.path.isdir(p):
                    raise ValueError("task %r missing repo/ directory" % task_id)
            else:
                if not os.path.isfile(p):
                    raise ValueError("task %r missing %s" % (task_id, required))
    return data


def swe_load_task_meta(suite_dir, task_id):
    """Load a task's meta.json. Pure."""
    path = os.path.join(suite_dir, task_id, "meta.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_SWE_HIDDEN_TEST_RUNNER = '''\
import importlib.util, sys, types, traceback

# Minimal, dependency-free stand-in for the tiny slice of pytest the hidden
# graders use (only pytest.raises). Installed before the test module executes so
# an in-test `import pytest` resolves to this shim rather than failing.
class _Raises:
    def __init__(self, exc):
        self.exc = exc
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError("DID NOT RAISE %r" % (self.exc,))
        return isinstance(exc, self.exc)

_pytest = types.ModuleType("pytest")
_pytest.raises = lambda exc, *a, **k: _Raises(exc)
sys.modules["pytest"] = _pytest

_test_path = sys.argv[1]
_spec = importlib.util.spec_from_file_location("hidden_test", _test_path)
_module = importlib.util.module_from_spec(_spec)
sys.modules["hidden_test"] = _module
try:
    _spec.loader.exec_module(_module)
except Exception:
    traceback.print_exc()
    sys.exit(1)

_tests = [getattr(_module, _n) for _n in dir(_module)
          if _n.startswith("test") and callable(getattr(_module, _n))]
if not _tests:
    print("no tests collected")
    sys.exit(1)

_failures = 0
for _fn in _tests:
    try:
        _fn()
        print("PASS " + _fn.__name__)
    except Exception:
        _failures += 1
        print("FAIL " + _fn.__name__)
        traceback.print_exc()
sys.exit(1 if _failures else 0)
'''


def swe_score_task(task_dir, post_edit_repo_path):
    """Run test_hidden.py against a post-edit repo copy. Pure scorer.

    Copies test_hidden.py into an isolated temp dir alongside the post-edit repo,
    runs pytest, and returns the result dict.

    Args:
        task_dir: Path to the task fixture (contains test_hidden.py, meta.json).
        post_edit_repo_path: Path to the agent's edited repo/ directory.

    Returns:
        dict with {passed: bool, test_output: str, mutated: bool}
    """
    import tempfile as _tf
    scratch = _tf.mkdtemp(prefix="swe-score-")
    try:
        scratch_repo = os.path.join(scratch, "repo")
        shutil.copytree(post_edit_repo_path, scratch_repo)
        test_src = os.path.join(task_dir, "test_hidden.py")
        test_dst = os.path.join(scratch, "test_hidden.py")
        shutil.copy2(test_src, test_dst)
        # Prefer pytest when it is importable; otherwise run the hidden tests with
        # a dependency-free fallback runner. Both execute in an isolated subprocess
        # (cwd=scratch) under a 120s timeout and exit non-zero on any failure.
        #
        # The fallback is the point: a grader environment is not guaranteed to have
        # pytest, and this tree's copy ran `python -m pytest` unconditionally — so a
        # missing pytest scored every hidden test as FAILED rather than reporting
        # that it could not run them, which reads as a broken solution instead of a
        # broken harness.
        import importlib.util as _ilu
        if _ilu.find_spec("pytest") is not None:
            _cmd = [sys.executable, "-m", "pytest", test_dst, "-q", "--tb=short"]
        else:
            _cmd = [sys.executable, "-c", _SWE_HIDDEN_TEST_RUNNER, test_dst]
        try:
            proc = subprocess.run(
                _cmd,
                cwd=scratch,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, timeout=120)
            passed = proc.returncode == 0
            output = proc.stdout
        except subprocess.TimeoutExpired:
            passed = False
            output = "TIMEOUT: test execution exceeded 120s"
        except OSError as e:
            passed = False
            output = "OSError running hidden tests: %s" % e
        original_repo = os.path.join(task_dir, "repo")
        mutated = not _dirs_identical(original_repo, post_edit_repo_path)
        return {"passed": passed, "test_output": output, "mutated": mutated}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _dirs_identical(dir_a, dir_b):
    """Check if two directory trees have byte-identical file contents. Pure.

    Ignores __pycache__ directories (transient build artifacts from pytest runs).
    """
    import filecmp
    _IGNORE = {"__pycache__"}
    cmp = filecmp.dircmp(dir_a, dir_b, ignore=list(_IGNORE))
    left = [x for x in cmp.left_only if x not in _IGNORE]
    right = [x for x in cmp.right_only if x not in _IGNORE]
    if left or right or cmp.diff_files:
        return False
    for sub in cmp.common_dirs:
        if sub in _IGNORE:
            continue
        if not _dirs_identical(os.path.join(dir_a, sub),
                               os.path.join(dir_b, sub)):
            return False
    return True


def swe_task_passed(score_result, meta):
    """Determine if a task is truly passed given score result and meta. Pure.

    For normal tasks (expected_mutation=true): passed = tests pass.
    For abstention tasks (expected_mutation=false): passed = tests pass AND not mutated.
    """
    if not meta.get("expected_mutation", True):
        return score_result["passed"] and not score_result["mutated"]
    return score_result["passed"]


def swe_aggregate_arm(results):
    """Compute pass-rate for an arm given a list of per-task pass booleans. Pure.

    Args:
        results: list of booleans (True=passed, False=failed) for tasks in one seed.

    Returns:
        float pass-rate (0.0 to 1.0).
    """
    if not results:
        return 0.0
    return sum(1 for r in results if r) / len(results)


def swe_aggregate_seeds(seed_pass_rates):
    """Compute min/mean/max pass-rate across seeds. Pure.

    Args:
        seed_pass_rates: list of per-seed pass-rates (each a float 0..1).

    Returns:
        dict with {min, mean, max, seeds}
    """
    if not seed_pass_rates:
        return {"min": 0.0, "mean": 0.0, "max": 0.0, "seeds": 0}
    return {
        "min": min(seed_pass_rates),
        "mean": sum(seed_pass_rates) / len(seed_pass_rates),
        "max": max(seed_pass_rates),
        "seeds": len(seed_pass_rates),
    }


def swe_regression_decision(old_rates, new_rates, tolerance=SWE_REGRESSION_TOLERANCE):
    """Pure regression decision: BLOCK when new mean < old mean - tolerance.

    Args:
        old_rates: list of per-seed pass-rates for the old/baseline arm.
        new_rates: list of per-seed pass-rates for the new/treatment arm.
        tolerance: maximum allowed degradation (default 0.15).

    Returns:
        dict with {regressed: bool, old_mean: float, new_mean: float,
                   delta: float, tolerance: float, verdict: str}
    """
    old_mean = sum(old_rates) / len(old_rates) if old_rates else 0.0
    new_mean = sum(new_rates) / len(new_rates) if new_rates else 0.0
    delta = new_mean - old_mean
    regressed = new_mean < (old_mean - tolerance)
    verdict = "BLOCK" if regressed else "PASS"
    return {
        "regressed": regressed,
        "old_mean": old_mean,
        "new_mean": new_mean,
        "delta": delta,
        "tolerance": tolerance,
        "verdict": verdict,
    }


def swe_build_dry_run_plan(suite_dir, seeds, arms):
    """Build the dry-run plan (what WOULD be executed). Pure.

    Returns a JSON-serializable dict describing planned runs without spawning.
    """
    suite = swe_load_suite(suite_dir)
    tasks = []
    for task_id in suite["tasks"]:
        meta = swe_load_task_meta(suite_dir, task_id)
        tasks.append({
            "id": task_id,
            "kind": meta.get("kind"),
            "timeout": meta.get("timeout"),
            "expected_mutation": meta.get("expected_mutation"),
        })
    total_runs = len(suite["tasks"]) * seeds * len(arms)
    return {
        "suite": suite["suite"],
        "version": suite.get("version"),
        "suite_dir": suite_dir,
        "tasks": tasks,
        "arms": arms,
        "seeds": seeds,
        "total_runs": total_runs,
        "dry_run": True,
    }


def swe_build_ledger_row(run_ts, git_meta, suite_dir, arms_results, seeds):
    """Build an immutable ledger row for a bench swe run. Pure.

    Args:
        run_ts: UTC ISO timestamp of the run.
        git_meta: dict with commit/subject/committed/dirty.
        suite_dir: path to the suite (for recording).
        arms_results: dict mapping arm name -> {pass_rates: [...], turns_mean, tokens_mean}
        seeds: number of seeds used.

    Returns:
        A ledger row dict.
    """
    arm_stats = {}
    for arm, data in arms_results.items():
        rates = data.get("pass_rates", [])
        arm_stats[arm] = swe_aggregate_seeds(rates)
        arm_stats[arm]["turns_mean"] = data.get("turns_mean", 0)
        arm_stats[arm]["tokens_mean"] = data.get("tokens_mean", 0)
    arm_names = list(arms_results.keys())
    delta = None
    if len(arm_names) >= 2:
        a_rates = arms_results[arm_names[0]].get("pass_rates", [])
        b_rates = arms_results[arm_names[1]].get("pass_rates", [])
        if a_rates and b_rates:
            decision = swe_regression_decision(a_rates, b_rates)
            delta = decision["delta"]
    return {
        "schema": _ledger.LEDGER_SCHEMA,
        "suite": "swe-task",
        "run": run_ts,
        "commit": git_meta.get("commit", "unknown"),
        "committed": git_meta.get("committed", ""),
        "subject": git_meta.get("subject", ""),
        "dirty": git_meta.get("dirty", False),
        "mode": "swe-task",
        "seeds": seeds,
        "arms": arm_stats,
        "delta": delta,
    }


def _print_swe_dry_run(plan, seeds, arms):
    """The `--dry-run` plan in human form. Split out at task 99 wave 10.

    Prints the same three things the JSON arm serializes -- the run arithmetic,
    the per-task row, and the arm list -- and nothing else. It takes `seeds` and
    `arms` rather than reading them off the plan because the plan records
    `total_runs` and the task list, not the two inputs that produced them.
    """
    print("bench swe [dry-run]: %d tasks x %d seeds x %d arms = %d runs"
          % (len(plan["tasks"]), seeds, len(arms), plan["total_runs"]))
    for t in plan["tasks"]:
        print("  - %s (kind=%s, timeout=%s, mutation=%s)"
              % (t["id"], t["kind"], t["timeout"], t["expected_mutation"]))
    print("  arms: %s" % ", ".join(arms))


def _run_swe_task(suite_dir, task_id, seed, arm):
    """Run ONE task, once, in one arm: spawn, grade, return (passed, turns, tokens).

    The innermost body of the live run, split out at task 99 wave 10. Everything
    here happens inside a scratch copy of the task's repo that is removed on the
    way out, pass or fail -- the task fixtures are package data and this is the
    only thing standing between a benchmark run and a mutated fixture tree.

    TWO degradations are deliberate and are the reason the spawn has its own
    try: an agent that TIMES OUT and one whose binary is missing (OSError) both
    score ZERO turns and ZERO tokens and are then GRADED ANYWAY. Grading a
    non-run is not a bug -- an abstain task passes by leaving the repo alone, so
    a timed-out agent can legitimately pass it, and swallowing the run instead
    would silently drop it from the denominator.

    `arm == "no-context"` is the ablation: the agent is spawned with
    AGENTWARE_KNOWLEDGE_DIR removed from its environment, which is the whole
    independent variable of this benchmark.
    """
    import tempfile as _tf
    task_dir_path = os.path.join(suite_dir, task_id)
    meta = swe_load_task_meta(suite_dir, task_id)
    scratch = _tf.mkdtemp(prefix="swe-run-%s-%d-" % (task_id, seed))
    try:
        scratch_repo = os.path.join(scratch, "repo")
        shutil.copytree(os.path.join(task_dir_path, "repo"), scratch_repo)
        with open(os.path.join(task_dir_path, "task.md"), encoding="utf-8") as f:
            task_prompt = f.read()
        agent_cmd = [
            "claude", "-p", task_prompt,
            "--agent", "agentware-execution",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        env = dict(os.environ)
        if arm == "no-context":
            env.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        try:
            proc = subprocess.run(
                agent_cmd, cwd=scratch_repo,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=meta.get("timeout", 120),
                env=env)
            turns = 1
            tokens = len(proc.stdout) // 4
        except (subprocess.TimeoutExpired, OSError):
            turns = 0
            tokens = 0
        result = swe_score_task(task_dir_path, scratch_repo)
        return swe_task_passed(result, meta), turns, tokens
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _run_swe_arm(suite, suite_dir, seeds, arm):
    """Run every task for every seed in ONE arm; return that arm's stats.

    Split out at task 99 wave 10 from a triple-nested loop. The nesting order is
    the measurement design and is not incidental: the pass RATE is per SEED (one
    number per full sweep of the suite), while turns and tokens are averaged
    over every individual RUN. Collapsing the two levels -- averaging rates over
    runs instead of over seeds -- produces a number that looks like a pass rate
    and is not one, which is what `swe_aggregate_seeds` then reports a spread
    over.

    A seed that ran no tasks at all scores 0.0 rather than dividing by zero, and
    an arm that ran nothing scores 0 for both means, so an empty suite reports
    zeroes instead of raising.
    """
    arm_pass_rates = []
    total_turns = 0
    total_tokens = 0
    run_count = 0
    for seed in range(seeds):
        seed_passed = 0
        seed_total = 0
        for task_id in suite["tasks"]:
            task_pass, turns, tokens = _run_swe_task(
                suite_dir, task_id, seed, arm)
            seed_total += 1
            if task_pass:
                seed_passed += 1
            total_turns += turns
            total_tokens += tokens
            run_count += 1
        arm_pass_rates.append(seed_passed / seed_total if seed_total else 0.0)
    return {
        "pass_rates": arm_pass_rates,
        "turns_mean": total_turns / run_count if run_count else 0,
        "tokens_mean": total_tokens / run_count if run_count else 0,
    }


def _print_swe_report(row, suite, seeds, arms):
    """The completed-run report in human form. Split out at task 99 wave 10.

    The delta line is printed on `is not None` rather than on truthiness: a
    delta of exactly 0.0 -- the two arms scoring identically, which is the most
    interesting negative result this benchmark can produce -- is falsy, and
    dropping it would report "no difference measured" as "no measurement".
    """
    print("bench swe: completed %d tasks x %d seeds x %d arms"
          % (len(suite["tasks"]), seeds, len(arms)))
    for arm, stats in row.get("arms", {}).items():
        print("  %s: pass_rate mean=%.2f (min=%.2f max=%.2f)"
              % (arm, stats["mean"], stats["min"], stats["max"]))
    if row.get("delta") is not None:
        print("  delta: %+.4f" % row["delta"])


def cmd_bench_swe(args):
    """`bench swe` -- SWE-task behavioral benchmark runner."""
    suite_dir = args.suite
    seeds = args.seeds
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    fmt = getattr(args, "format", "text")

    if not arms:
        print("bench swe: --arms must specify at least one arm",
              file=sys.stderr)
        return 1

    if not os.path.isdir(suite_dir):
        print("bench swe: suite directory not found: %s" % suite_dir,
              file=sys.stderr)
        return 1

    try:
        swe_load_suite(suite_dir)
    except (ValueError, IOError, OSError) as e:
        print("bench swe: invalid suite: %s" % e, file=sys.stderr)
        return 1

    if args.dry_run:
        plan = swe_build_dry_run_plan(suite_dir, seeds, arms)
        if fmt == "json":
            print(json.dumps(plan, indent=2, ensure_ascii=False))
        else:
            _print_swe_dry_run(plan, seeds, arms)
        return 0

    # Live run: spawn agents per task x seed x arm
    suite = swe_load_suite(suite_dir)
    arms_results = {arm: _run_swe_arm(suite, suite_dir, seeds, arm)
                    for arm in arms}

    run_ts = dates.utc_now_iso()
    g_meta = _ledger.git_info()
    row = swe_build_ledger_row(run_ts, g_meta, suite_dir, arms_results, seeds)

    kdir = config.resolve_knowledge_dir()
    if kdir:
        ledger_path = os.path.join(kdir, _ledger.HISTORY_REL)
        os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
        _ledger.append_ledger_row(ledger_path, row)

    if fmt == "json":
        print(json.dumps(row, indent=2, ensure_ascii=False))
    else:
        _print_swe_report(row, suite, seeds, arms)
    return 0


_EXPORTS = [
    "SWE_SUITE_FILE",
    "SWE_DEFAULT_SEEDS",
    "SWE_REGRESSION_TOLERANCE",
    "SWE_DEFAULT_ARMS",
    "swe_load_suite",
    "swe_load_task_meta",
    "swe_score_task",
    "_dirs_identical",
    "swe_task_passed",
    "swe_aggregate_arm",
    "swe_aggregate_seeds",
    "swe_regression_decision",
    "swe_build_dry_run_plan",
    "swe_build_ledger_row",
    "_print_swe_dry_run",
    "_run_swe_task",
    "_run_swe_arm",
    "_print_swe_report",
    "cmd_bench_swe",
]
