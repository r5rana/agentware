"""Tests for the SWE-task benchmark (hidden-unit-test coding suite).

Covers:
  1. suite.json parses correctly
  2. Each task's test_hidden.py FAILS on unedited repo (fix/feature/efficiency)
  3. Scorer returns correct results for canned good/bad edits
  4. Aggregator + regression decision are pure (deterministic)
  5. Abstention task passes only on no-mutation
  6. --dry-run spawns nothing

Stdlib unittest only (no live agent spawn). Never touches the real KB.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, REPO_ROOT
except ImportError:
    from _fixtures import load_cli, REPO_ROOT


CLI = load_cli()
SUITE_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "swe_tasks")


class SuiteParsingTest(unittest.TestCase):
    """suite.json loads and validates correctly."""

    def test_suite_json_loads(self):
        data = CLI.swe_load_suite(SUITE_DIR)
        self.assertEqual(data["suite"], "swe-task")
        self.assertEqual(data["version"], "1.0")
        self.assertIsInstance(data["tasks"], list)
        self.assertEqual(len(data["tasks"]), 5)

    def test_suite_task_ids(self):
        data = CLI.swe_load_suite(SUITE_DIR)
        expected = ["fix-off-by-one", "fix-null-guard", "feature-csv-parser",
                    "efficiency-sort", "abstain-correct"]
        self.assertEqual(data["tasks"], expected)

    def test_each_task_has_required_files(self):
        data = CLI.swe_load_suite(SUITE_DIR)
        for task_id in data["tasks"]:
            task_dir = os.path.join(SUITE_DIR, task_id)
            self.assertTrue(os.path.isdir(os.path.join(task_dir, "repo")),
                            "%s missing repo/" % task_id)
            self.assertTrue(os.path.isfile(os.path.join(task_dir, "task.md")),
                            "%s missing task.md" % task_id)
            self.assertTrue(os.path.isfile(os.path.join(task_dir, "test_hidden.py")),
                            "%s missing test_hidden.py" % task_id)
            self.assertTrue(os.path.isfile(os.path.join(task_dir, "meta.json")),
                            "%s missing meta.json" % task_id)

    def test_each_meta_has_required_fields(self):
        data = CLI.swe_load_suite(SUITE_DIR)
        for task_id in data["tasks"]:
            meta = CLI.swe_load_task_meta(SUITE_DIR, task_id)
            self.assertIn("id", meta)
            self.assertIn("kind", meta)
            self.assertIn("timeout", meta)
            self.assertIn("expected_mutation", meta)
            self.assertEqual(meta["id"], task_id)

    def test_invalid_suite_raises(self):
        tmp = tempfile.mkdtemp()
        try:
            # Missing suite.json
            with self.assertRaises((IOError, OSError)):
                CLI.swe_load_suite(tmp)
            # Bad suite field
            with open(os.path.join(tmp, "suite.json"), "w") as f:
                json.dump({"suite": "wrong", "tasks": ["x"]}, f)
            with self.assertRaises(ValueError):
                CLI.swe_load_suite(tmp)
        finally:
            shutil.rmtree(tmp)


class HiddenTestFailsOnUneditedTest(unittest.TestCase):
    """Each fix/feature/efficiency task's test_hidden.py FAILS on unedited repo."""

    def _run_hidden_test(self, task_id):
        """Run test_hidden.py on the unedited repo/ using the scorer."""
        task_dir = os.path.join(SUITE_DIR, task_id)
        repo_path = os.path.join(task_dir, "repo")
        result = CLI.swe_score_task(task_dir, repo_path)
        return result

    def test_fix_off_by_one_fails(self):
        result = self._run_hidden_test("fix-off-by-one")
        self.assertFalse(result["passed"],
                         "fix-off-by-one should FAIL on unedited repo")

    def test_fix_null_guard_fails(self):
        result = self._run_hidden_test("fix-null-guard")
        self.assertFalse(result["passed"],
                         "fix-null-guard should FAIL on unedited repo")

    def test_feature_csv_parser_fails(self):
        result = self._run_hidden_test("feature-csv-parser")
        self.assertFalse(result["passed"],
                         "feature-csv-parser should FAIL on unedited repo")

    def test_efficiency_sort_fails(self):
        result = self._run_hidden_test("efficiency-sort")
        self.assertFalse(result["passed"],
                         "efficiency-sort should FAIL on unedited repo")

    def test_abstain_correct_passes_unedited(self):
        """Abstention task: tests PASS on unedited (code is already correct)."""
        result = self._run_hidden_test("abstain-correct")
        self.assertTrue(result["passed"],
                        "abstain-correct should PASS on unedited repo "
                        "(code is already correct)")
        self.assertFalse(result["mutated"],
                         "unedited repo should show mutated=False")


class ScorerTest(unittest.TestCase):
    """Scorer returns correct results for canned good/bad edits."""

    def test_scorer_good_edit_fix_off_by_one(self):
        """A correct fix makes the hidden tests pass."""
        task_dir = os.path.join(SUITE_DIR, "fix-off-by-one")
        tmp = tempfile.mkdtemp()
        try:
            # Copy repo and apply fix
            repo_copy = os.path.join(tmp, "repo")
            shutil.copytree(os.path.join(task_dir, "repo"), repo_copy)
            # Apply the correct fix (ceil division)
            fixed = os.path.join(repo_copy, "paginate.py")
            with open(fixed, "r") as f:
                content = f.read()
            content = content.replace(
                'total_pages = len(items) // page_size  # BUG: should use ceil division',
                'total_pages = (len(items) + page_size - 1) // page_size')
            content = content.replace(
                'count = len(items) // page_size  # BUG: same off-by-one',
                'count = (len(items) + page_size - 1) // page_size')
            with open(fixed, "w") as f:
                f.write(content)
            result = CLI.swe_score_task(task_dir, repo_copy)
            self.assertTrue(result["passed"])
            self.assertTrue(result["mutated"])
        finally:
            shutil.rmtree(tmp)

    def test_scorer_bad_edit_still_fails(self):
        """A wrong edit keeps the hidden tests failing."""
        task_dir = os.path.join(SUITE_DIR, "fix-off-by-one")
        tmp = tempfile.mkdtemp()
        try:
            repo_copy = os.path.join(tmp, "repo")
            shutil.copytree(os.path.join(task_dir, "repo"), repo_copy)
            # Apply a wrong fix (just add 1 to total_pages but not fix get_page)
            fixed = os.path.join(repo_copy, "paginate.py")
            with open(fixed, "r") as f:
                content = f.read()
            content = content.replace(
                'count = len(items) // page_size  # BUG: same off-by-one',
                'count = len(items) // page_size + 99  # wrong fix')
            with open(fixed, "w") as f:
                f.write(content)
            result = CLI.swe_score_task(task_dir, repo_copy)
            self.assertFalse(result["passed"])
            self.assertTrue(result["mutated"])
        finally:
            shutil.rmtree(tmp)

    def test_scorer_good_edit_null_guard(self):
        """Correct null guard fix passes hidden tests."""
        task_dir = os.path.join(SUITE_DIR, "fix-null-guard")
        tmp = tempfile.mkdtemp()
        try:
            repo_copy = os.path.join(tmp, "repo")
            shutil.copytree(os.path.join(task_dir, "repo"), repo_copy)
            # Write a correct implementation
            fixed = os.path.join(repo_copy, "users.py")
            with open(fixed, "w") as f:
                f.write('''"""User lookup utilities."""


def find_user(users, email):
    """Find a user dict by email (case-insensitive match)."""
    if email is None:
        return None
    normalized = email.lower()
    for user in users:
        if user.get("email") is None:
            continue
        if user["email"].lower() == normalized:
            return user
    return None


def find_users_by_domain(users, domain):
    """Return all users whose email ends with the given domain."""
    result = []
    for user in users:
        if user.get("email") is None:
            continue
        if user["email"].endswith("@" + domain):
            result.append(user)
    return result
''')
            result = CLI.swe_score_task(task_dir, repo_copy)
            self.assertTrue(result["passed"])
            self.assertTrue(result["mutated"])
        finally:
            shutil.rmtree(tmp)

    def test_scorer_mutation_detection(self):
        """Scorer correctly detects whether repo was mutated."""
        task_dir = os.path.join(SUITE_DIR, "abstain-correct")
        # Unedited -> not mutated
        result = CLI.swe_score_task(task_dir,
                                    os.path.join(task_dir, "repo"))
        self.assertFalse(result["mutated"])
        # Edited -> mutated
        tmp = tempfile.mkdtemp()
        try:
            repo_copy = os.path.join(tmp, "repo")
            shutil.copytree(os.path.join(task_dir, "repo"), repo_copy)
            with open(os.path.join(repo_copy, "fibonacci.py"), "a") as f:
                f.write("\n# unnecessary edit\n")
            result = CLI.swe_score_task(task_dir, repo_copy)
            self.assertTrue(result["mutated"])
        finally:
            shutil.rmtree(tmp)


class AbstentionTest(unittest.TestCase):
    """Abstention task passes only when code is NOT mutated."""

    def test_abstain_passes_on_no_mutation(self):
        """Correct: tests pass AND not mutated -> task passes."""
        meta = {"expected_mutation": False}
        score = {"passed": True, "mutated": False, "test_output": ""}
        self.assertTrue(CLI.swe_task_passed(score, meta))

    def test_abstain_fails_on_mutation(self):
        """Wrong: agent mutated the code -> task fails even if tests pass."""
        meta = {"expected_mutation": False}
        score = {"passed": True, "mutated": True, "test_output": ""}
        self.assertFalse(CLI.swe_task_passed(score, meta))

    def test_abstain_fails_on_test_failure(self):
        """Wrong: tests fail -> task fails regardless of mutation."""
        meta = {"expected_mutation": False}
        score = {"passed": False, "mutated": False, "test_output": ""}
        self.assertFalse(CLI.swe_task_passed(score, meta))

    def test_normal_task_ignores_mutation(self):
        """Normal tasks only care about tests passing."""
        meta = {"expected_mutation": True}
        score = {"passed": True, "mutated": True, "test_output": ""}
        self.assertTrue(CLI.swe_task_passed(score, meta))
        score2 = {"passed": True, "mutated": False, "test_output": ""}
        self.assertTrue(CLI.swe_task_passed(score2, meta))


class AggregatorTest(unittest.TestCase):
    """Pass-rate aggregator and regression decision are pure."""

    def test_aggregate_arm_empty(self):
        self.assertEqual(CLI.swe_aggregate_arm([]), 0.0)

    def test_aggregate_arm_all_pass(self):
        self.assertEqual(CLI.swe_aggregate_arm([True, True, True]), 1.0)

    def test_aggregate_arm_mixed(self):
        self.assertAlmostEqual(CLI.swe_aggregate_arm([True, False, True, False, True]),
                               0.6)

    def test_aggregate_seeds_empty(self):
        r = CLI.swe_aggregate_seeds([])
        self.assertEqual(r["min"], 0.0)
        self.assertEqual(r["mean"], 0.0)
        self.assertEqual(r["max"], 0.0)
        self.assertEqual(r["seeds"], 0)

    def test_aggregate_seeds_values(self):
        r = CLI.swe_aggregate_seeds([0.4, 0.6, 0.8, 1.0, 0.6])
        self.assertAlmostEqual(r["min"], 0.4)
        self.assertAlmostEqual(r["mean"], 0.68)
        self.assertAlmostEqual(r["max"], 1.0)
        self.assertEqual(r["seeds"], 5)

    def test_aggregate_seeds_deterministic(self):
        """Same inputs always produce same outputs (pure)."""
        rates = [0.5, 0.7, 0.3, 0.9, 0.6]
        r1 = CLI.swe_aggregate_seeds(rates)
        r2 = CLI.swe_aggregate_seeds(rates)
        self.assertEqual(r1, r2)


class RegressionDecisionTest(unittest.TestCase):
    """Regression decision is pure and uses the correct tolerance band."""

    def test_no_regression_equal(self):
        d = CLI.swe_regression_decision([0.8, 0.8, 0.8], [0.8, 0.8, 0.8])
        self.assertFalse(d["regressed"])
        self.assertEqual(d["verdict"], "PASS")
        self.assertAlmostEqual(d["delta"], 0.0)

    def test_no_regression_slight_drop(self):
        """Drop within tolerance (0.15) -> PASS."""
        d = CLI.swe_regression_decision([0.8, 0.8, 0.8, 0.8, 0.8],
                                        [0.7, 0.7, 0.7, 0.7, 0.7])
        self.assertFalse(d["regressed"])
        self.assertEqual(d["verdict"], "PASS")

    def test_regression_large_drop(self):
        """Drop beyond tolerance -> BLOCK."""
        d = CLI.swe_regression_decision([0.8, 0.8, 0.8, 0.8, 0.8],
                                        [0.5, 0.5, 0.5, 0.5, 0.5])
        self.assertTrue(d["regressed"])
        self.assertEqual(d["verdict"], "BLOCK")
        self.assertAlmostEqual(d["delta"], -0.3)

    def test_regression_exact_boundary(self):
        """Exactly at tolerance boundary (new == old - 0.15) -> PASS (not strictly less)."""
        d = CLI.swe_regression_decision([0.8, 0.8, 0.8, 0.8, 0.8],
                                        [0.65, 0.65, 0.65, 0.65, 0.65])
        self.assertFalse(d["regressed"])
        self.assertEqual(d["verdict"], "PASS")

    def test_regression_just_below_boundary(self):
        """Just below tolerance -> BLOCK."""
        d = CLI.swe_regression_decision([0.8, 0.8, 0.8, 0.8, 0.8],
                                        [0.64, 0.64, 0.64, 0.64, 0.64])
        self.assertTrue(d["regressed"])
        self.assertEqual(d["verdict"], "BLOCK")

    def test_improvement_passes(self):
        """New arm is BETTER -> PASS."""
        d = CLI.swe_regression_decision([0.6, 0.6, 0.6, 0.6, 0.6],
                                        [0.9, 0.9, 0.9, 0.9, 0.9])
        self.assertFalse(d["regressed"])
        self.assertEqual(d["verdict"], "PASS")
        self.assertAlmostEqual(d["delta"], 0.3)

    def test_deterministic(self):
        """Same inputs always produce same output (pure)."""
        old = [0.7, 0.8, 0.6, 0.7, 0.8]
        new = [0.5, 0.6, 0.4, 0.5, 0.6]
        d1 = CLI.swe_regression_decision(old, new)
        d2 = CLI.swe_regression_decision(old, new)
        self.assertEqual(d1, d2)


class DryRunTest(unittest.TestCase):
    """--dry-run spawns nothing and produces valid output."""

    def test_dry_run_plan_structure(self):
        plan = CLI.swe_build_dry_run_plan(SUITE_DIR, 5,
                                          ["with-context", "no-context"])
        self.assertEqual(plan["suite"], "swe-task")
        self.assertEqual(plan["seeds"], 5)
        self.assertEqual(plan["arms"], ["with-context", "no-context"])
        self.assertEqual(len(plan["tasks"]), 5)
        self.assertEqual(plan["total_runs"], 50)
        self.assertTrue(plan["dry_run"])

    def test_dry_run_does_not_spawn(self):
        """Verify dry-run via subprocess exits 0 with valid JSON, no agent spawn."""
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "scripts", "agentware"),
             "bench", "swe", "--suite", SUITE_DIR, "--dry-run", "--format", "json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["suite"], "swe-task")
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["total_runs"], 50)

    def test_bad_suite_path_exits_nonzero(self):
        """Bad --suite path exits non-zero."""
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "scripts", "agentware"),
             "bench", "swe", "--suite", "/nonexistent/path", "--dry-run"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=10)
        self.assertNotEqual(proc.returncode, 0)


class LedgerRowTest(unittest.TestCase):
    """Ledger row building is pure and produces valid schema."""

    def test_build_ledger_row(self):
        arms_results = {
            "with-context": {"pass_rates": [0.8, 0.6, 0.8, 0.6, 0.8],
                             "turns_mean": 3.2, "tokens_mean": 1500},
            "no-context": {"pass_rates": [0.4, 0.6, 0.4, 0.6, 0.4],
                           "turns_mean": 4.1, "tokens_mean": 2000},
        }
        git_meta = {"commit": "abc123", "subject": "test",
                    "committed": "2026-06-30T00:00:00Z", "dirty": False}
        row = CLI.swe_build_ledger_row("2026-06-30T12:00:00Z", git_meta,
                                       SUITE_DIR, arms_results, 5)
        self.assertEqual(row["schema"], CLI.LEDGER_SCHEMA)
        self.assertEqual(row["suite"], "swe-task")
        self.assertEqual(row["mode"], "swe-task")
        self.assertEqual(row["seeds"], 5)
        self.assertIn("with-context", row["arms"])
        self.assertIn("no-context", row["arms"])
        # Delta should be computed (no-context mean - with-context mean)
        # Actually it's regression decision between first two arms
        self.assertIsNotNone(row["delta"])

    def test_ledger_row_deterministic(self):
        """Same inputs -> same row (pure)."""
        arms_results = {
            "arm-a": {"pass_rates": [1.0, 0.8], "turns_mean": 2, "tokens_mean": 500},
            "arm-b": {"pass_rates": [0.6, 0.4], "turns_mean": 3, "tokens_mean": 700},
        }
        git_meta = {"commit": "x", "subject": "y",
                    "committed": "2026-01-01T00:00:00Z", "dirty": True}
        r1 = CLI.swe_build_ledger_row("T", git_meta, "/x", arms_results, 2)
        r2 = CLI.swe_build_ledger_row("T", git_meta, "/x", arms_results, 2)
        self.assertEqual(r1, r2)


class ScorecardSWETest(unittest.TestCase):
    """bench scorecard renders an SWE section for swe-task rows."""

    def test_scorecard_includes_swe_section(self):
        rows = [
            {
                "schema": CLI.LEDGER_SCHEMA,
                "suite": "swe-task",
                "run": "2026-06-30T12:00:00Z",
                "commit": "abc123",
                "committed": "2026-06-30T00:00:00Z",
                "subject": "test",
                "dirty": False,
                "mode": "swe-task",
                "seeds": 5,
                "arms": {
                    "with-context": {"min": 0.6, "mean": 0.72, "max": 0.8,
                                     "seeds": 5, "turns_mean": 3, "tokens_mean": 1500},
                    "no-context": {"min": 0.4, "mean": 0.48, "max": 0.6,
                                   "seeds": 5, "turns_mean": 4, "tokens_mean": 2000},
                },
                "delta": -0.24,
            }
        ]
        text = CLI.render_scorecard(rows)
        self.assertIn("## SWE-Task Benchmark", text)
        self.assertIn("abc123", text)
        self.assertIn("-0.24", text)

    def test_scorecard_no_swe_rows(self):
        """No SWE rows -> no SWE section."""
        rows = [
            {
                "schema": CLI.LEDGER_SCHEMA,
                "run": "2026-06-30T12:00:00Z",
                "commit": "x",
                "committed": "2026-06-30T00:00:00Z",
                "subject": "s",
                "dirty": False,
                "mode": "eval",
                "strategy": "bm25",
                "top_k": 5,
                "num_queries": 3,
                "gold_path": "/x",
                "metrics": {"recall_at_k": 0.9, "precision_at_k": 0.2,
                            "ndcg_at_k": 0.85, "mrr": 0.8,
                            "latency_ms_p50": 1.0, "context_tokens_mean": 500},
                "reliability": 96.0,
            }
        ]
        text = CLI.render_scorecard(rows)
        self.assertNotIn("SWE-Task", text)


if __name__ == "__main__":
    unittest.main()
