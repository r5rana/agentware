"""CI stage EFFECTIVENESS, the one documented command, and the failure ledger.

Feature 260813-modular-core-adoption, task 78. Arms D, E and F, split from
`test_ci_workflow.py` at the 600-line ceiling (`tests/README.md`); arms A, B and
C live there and the shared workflow reader in `_ci_workflow_support.py`.

  D. **Every stage is EFFECTIVE, not vacuous.** Each harness a stage names must
     carry a real `__main__` dispatch, and the ratchet commands must go RED on a
     planted defect. This is the arm that would have caught the three checkers
     task 78 found could not fail: `tests/_ceiling_gate.py` and
     `tests/_modularity_rules.py` had no `__main__` at all, and
     `tests/_manifest_gen.py --check` ignored its flag -- all three exited 0
     having measured nothing, in three documented places, for seven worklogs.
     "The gate ran" and "the gate could have failed" are different claims.
  E. **The one documented command is documented, once.** The bytes in
     `_bootstrap_gate.BOOTSTRAP_COMMAND` must appear in `CONTRIBUTING.md` and in
     the workflow, and the bootstrap profile must not contain the stage that
     clones -- the recursion guard, not a style rule.
  F. **Green means no worse than the recorded baseline.** The known-failure
     ledger is a shrink-only ratchet: an undeclared failure reddens, and so does
     a declared one that has started passing.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.gates._ci_workflow_support import (  # noqa: F401
    ENTRYPOINT_REL, GATE_COMMAND_PATTERNS, REPO_ROOT, WORKFLOW, WORKFLOW_REL,
    _block, _read, _uncommented, bg, cg, job_names, run_lines, sg,
    top_level_keys, trigger_events, uses_refs)


class EveryStageIsEffectiveTest(unittest.TestCase):
    """Arm D — a stage that cannot fail is worse than no stage at all."""

    #: Harness stages whose command is `python3 tests/_x.py ...`.
    def _harness_stages(self):
        for stage in cg.STAGES:
            argv = stage["argv"]
            if len(argv) >= 2 and argv[0] == "python3" \
                    and argv[1].startswith("tests/_"):
                yield stage, argv[1]

    def test_ciwf_every_stage_command_resolves_on_disk(self):
        for stage in cg.STAGES:
            argv = stage["argv"]
            target = argv[1] if argv[0] == "python3" else argv[0]
            if target.startswith("-"):
                continue
            self.assertTrue(os.path.exists(os.path.join(REPO_ROOT, target)),
                            "stage %s names %s, which does not exist"
                            % (stage["id"], target))

    def test_ciwf_every_harness_stage_has_a_real_main_dispatch(self):
        """The exact defect task 78 found: no `__main__`, so rc is always 0."""
        for stage, rel in self._harness_stages():
            source = _read(rel)
            self.assertIn('if __name__ == "__main__":', source,
                          "%s (stage %s) has no __main__ dispatch — running it "
                          "imports a module and exits 0 without measuring "
                          "anything" % (rel, stage["id"]))
            self.assertRegex(source, r"def main\(",
                             "%s defines no main()" % rel)

    def test_ciwf_the_three_previously_vacuous_checkers_now_report(self):
        """They must print a verdict, not exit 0 in silence."""
        for rel, argv in (("tests/_ceiling_gate.py", []),
                          ("tests/_modularity_rules.py", []),
                          ("tests/_manifest_gen.py", ["--check"])):
            proc = self._run([rel] + argv)
            self.assertEqual(proc.returncode, 0,
                             "%s is RED: %s" % (rel, proc.stdout[-400:]))
            self.assertTrue(proc.stdout.strip(),
                            "%s exited 0 and printed NOTHING — that is the "
                            "vacuous shape, not a pass" % rel)
            self.assertIn("OK", proc.stdout)

    def test_ciwf_the_ceiling_ratchet_goes_red_on_a_dropped_debt_row(self):
        self._assert_reddens_on_shrunk_debt("tests/_ceiling_gate.py", "_ceiling_gate")

    def test_ciwf_the_layer_ratchet_goes_red_on_a_dropped_debt_row(self):
        self._assert_reddens_on_shrunk_debt("tests/_modularity_rules.py",
                                            "_modularity_rules")

    def test_ciwf_the_manifest_check_goes_red_on_a_stale_catalog(self):
        catalog = os.path.join(REPO_ROOT, "catalog", "modules.json")
        with open(catalog, encoding="utf-8") as f:
            body = f.read()
        tmp = tempfile.mkdtemp(prefix="agentware-ciwf-")
        try:
            stale = os.path.join(tmp, "modules.json")
            with open(stale, "w", encoding="utf-8") as f:
                f.write(body.replace('"caps"', '"cAps"', 1))
            proc = self._run(["tests/_manifest_gen.py", "--check",
                              "--catalog", stale])
            self.assertEqual(proc.returncode, 1,
                             "--check accepted a stale catalog: %s"
                             % proc.stdout[-300:])
            self.assertIn("stale", proc.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _assert_reddens_on_shrunk_debt(self, rel, module_name):
        module = __import__(module_name)
        data = module.load_debt()
        rows = list(data["rows"])
        self.assertTrue(rows, "%s declares no debt rows, so this proof cannot "
                              "run; replace it with a planted violation" % rel)
        dropped = rows.pop(0)
        tmp = tempfile.mkdtemp(prefix="agentware-ciwf-")
        try:
            path = os.path.join(tmp, "debt.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"schema": module.DEBT_SCHEMA, "rows": rows}, f)
            proc = self._run([rel, "--debt", path])
            self.assertEqual(proc.returncode, 1,
                             "%s stayed green with the %r row removed — the "
                             "command cannot fail, so running it proves nothing"
                             % (rel, dropped["location"]))
            self.assertIn(dropped["location"], proc.stdout,
                          "%s went red without naming the offender" % rel)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _run(argv):
        env = dict(os.environ)
        env["AGENTWARE_NESTED_UNITTEST"] = "1"
        return subprocess.run([sys.executable] + argv, cwd=REPO_ROOT, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True)


class OneDocumentedCommandTest(unittest.TestCase):
    """Arm E — the fresh-clone command is documented once and cannot recurse."""

    def test_ciwf_the_bootstrap_command_is_a_single_command(self):
        for operator in bg.COMPOUND_OPERATORS:
            self.assertNotIn(operator, bg.BOOTSTRAP_COMMAND,
                             "%r makes the 'one documented command' claim false"
                             % operator)

    def test_ciwf_the_bootstrap_command_appears_verbatim_in_contributing(self):
        self.assertIn(bg.BOOTSTRAP_COMMAND, _read("CONTRIBUTING.md"),
                      "CONTRIBUTING.md does not carry the exact command "
                      "tests/_bootstrap_gate.py proves works: %r"
                      % bg.BOOTSTRAP_COMMAND)

    def test_ciwf_the_bootstrap_command_appears_verbatim_in_the_workflow(self):
        self.assertIn(bg.BOOTSTRAP_COMMAND, _read(WORKFLOW_REL),
                      "the workflow does not name the fresh-clone command, so a "
                      "reader of CI has no route to it")

    def test_ciwf_the_bootstrap_command_runs_the_bootstrap_profile(self):
        self.assertIn("--profile " + cg.BOOTSTRAP, bg.BOOTSTRAP_COMMAND)
        self.assertIn(ENTRYPOINT_REL, bg.BOOTSTRAP_COMMAND)

    def test_ciwf_the_bootstrap_profile_does_not_contain_the_cloning_stage(self):
        """The recursion guard: the clone runs the profile that does not clone."""
        ids = [s["id"] for s in cg.stages_for(cg.BOOTSTRAP)]
        self.assertNotIn("bootstrap", ids,
                         "the bootstrap profile contains the stage that clones "
                         "and runs the bootstrap profile — that recurses until "
                         "the disk is full")

    def test_ciwf_the_bootstrap_profile_is_a_strict_subset_of_declared_stages(self):
        full = set(s["id"] for s in cg.STAGES)
        self.assertTrue(set(s["id"] for s in cg.stages_for(cg.BOOTSTRAP)) <= full)

    def test_ciwf_the_bootstrap_profile_is_not_empty(self):
        self.assertGreaterEqual(len(cg.stages_for(cg.BOOTSTRAP)), 5,
                                "a bootstrap profile that checks almost nothing "
                                "makes the fresh-clone proof vacuous")

    def test_ciwf_contributing_routes_a_contributor_to_the_ci_entrypoint(self):
        body = _read("CONTRIBUTING.md")
        self.assertIn(ENTRYPOINT_REL, body,
                      "CONTRIBUTING.md never names the CI entrypoint, so a "
                      "contributor cannot run what CI runs")

    def test_ciwf_a_drifted_contributing_command_is_caught(self):
        """Behavioural: the arm must fail when the documented bytes change."""
        drifted = _read("CONTRIBUTING.md").replace(
            bg.BOOTSTRAP_COMMAND, "python3 tests/_ci_gate.py --profile quick")
        self.assertNotIn(bg.BOOTSTRAP_COMMAND, drifted,
                         "the mutation did not apply")


class KnownFailureLedgerTest(unittest.TestCase):
    """Arm F — 'green' is the baseline definition, and the ledger is a ratchet.

    `tests/baselines/README.md` states the rule this repository actually uses:
    green means NO WORSE THAN THE RECORDED BASELINE, never 'all pass'. A CI stage
    that reads `unittest`'s exit code implements the other one and is red on day
    one for the failure plan task 89 already owns — and a permanently-red CI is
    one everybody learns to scroll past. Subtraction is only safe if it can never
    grow silently, so the ledger bites in BOTH directions, exactly like the four
    other declared-residual ledgers in this tree.
    """

    def setUp(self):
        self.data = sg.load_ledger()
        self.rows = self.data["rows"]

    def test_ciwf_the_committed_ledger_is_schema_valid(self):
        self.assertEqual(sg.schema_errors(self.data), [])

    def test_ciwf_every_row_names_the_plan_task_that_removes_it(self):
        """An excused failure owned by 'flaky' is one nobody will ever fix."""
        for row in self.rows:
            self.assertRegex(row["owner"], r"^task \d+$", row["node_id"])

    def test_ciwf_every_row_states_a_diagnosis_not_a_shrug(self):
        for row in self.rows:
            self.assertGreater(len(row["reason"]), 80, row["node_id"])

    def test_ciwf_the_ledger_schema_refuses_an_ownerless_row(self):
        bad = {"node_id": "tests.unit.x.Y.z", "tier": "unit",
               "owner": "known flake", "reason": "x" * 100}
        errors = sg.schema_errors({"schema": sg.LEDGER_SCHEMA, "rows": [bad]})
        self.assertTrue(any("owner" in e for e in errors), errors)

    def test_ciwf_the_ledger_schema_refuses_a_reasonless_row(self):
        bad = {"node_id": "tests.unit.x.Y.z", "tier": "unit",
               "owner": "task 89", "reason": "known"}
        errors = sg.schema_errors({"schema": sg.LEDGER_SCHEMA, "rows": [bad]})
        self.assertTrue(any("diagnosis" in e for e in errors), errors)

    def test_ciwf_an_undeclared_failure_reddens_the_tier(self):
        """Direction one: a NEW break may not hide behind an old one."""
        problems, clean = sg.reconcile(
            [("FAIL", "tests.unit.ops.test_new.NewTest.test_broke_today")],
            self.rows, "unit")
        self.assertFalse(clean)
        self.assertEqual(problems[0][0], "UNDECLARED")
        self.assertIn("test_broke_today", problems[0][1])

    def test_ciwf_a_declared_failure_that_now_passes_reddens_the_tier(self):
        """Direction two: the ledger may only SHRINK, so a fix removes its row."""
        problems, clean = sg.reconcile([], self.rows, "unit")
        self.assertFalse(clean, "an empty failure set reconciled clean while "
                                "the ledger still declares a unit-tier row")
        self.assertEqual(problems[0][0], "STALE")
        self.assertIn("delete the row", problems[0][2])

    def test_ciwf_the_declared_failure_is_subtracted_and_named(self):
        declared = [r for r in self.rows if r["tier"] == "unit"]
        problems, clean = sg.reconcile(
            [("FAIL", r["node_id"]) for r in declared], self.rows, "unit")
        self.assertTrue(clean, problems)

    def test_ciwf_a_row_only_forgives_its_own_tier(self):
        """A unit-tier row must not silently excuse the same id in e2e."""
        declared = [r for r in self.rows if r["tier"] == "unit"]
        problems, clean = sg.reconcile(
            [("FAIL", declared[0]["node_id"])], self.rows, "e2e")
        self.assertFalse(clean)
        self.assertEqual(problems[0][0], "UNDECLARED")

    def test_ciwf_a_tier_that_crashes_before_reporting_is_a_failure(self):
        """A killed or crashed tier must never reconcile to clean-and-empty.

        This is the shape that turns a gate into decoration: the child dies, the
        result file is absent, and a naive reader sees zero failures.
        """
        result, _out, _cmd = sg.run_tier("unit", root=os.path.join(
            REPO_ROOT, "tests", "fixtures"))
        self.assertTrue(result.get("crashed") or result.get("failures"),
                        "a tier that could not run reported a clean result: %r"
                        % result)

    def test_ciwf_the_ledger_covers_only_tiers_that_exist(self):
        for row in self.rows:
            self.assertIn(row["tier"], sg.TIERS)


class StageRunnerTest(unittest.TestCase):
    """The runner's own contracts — filters, guards and exit codes."""

    def test_ciwf_an_unknown_stage_id_is_refused(self):
        self.assertRaises(ValueError, cg.stages_for, cg.FULL, ("no-such-stage",))

    def test_ciwf_a_stage_filter_selects_exactly_that_stage(self):
        picked = cg.stages_for(cg.FULL, ("parity",))
        self.assertEqual([s["id"] for s in picked], ["parity"])

    def test_ciwf_children_inherit_the_nested_unittest_guard(self):
        """Without it, a stage can re-enter the hanging whole-tree collection."""
        self.assertEqual(cg.child_env({})["AGENTWARE_NESTED_UNITTEST"], "1")

    def test_ciwf_the_full_profile_contains_every_task_named_stage(self):
        full = set(s["id"] for s in cg.stages_for(cg.FULL))
        for phrase, ids in cg.TASK_COVERAGE:
            for sid in ids:
                self.assertIn(sid, full,
                              "%s (%s) is not in the full profile, so CI never "
                              "runs it" % (sid, phrase))

    def test_ciwf_list_mode_exits_zero_and_prints_every_stage(self):
        proc = subprocess.run(
            [sys.executable, "tests/_ci_gate.py", "--list", "--json"],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout[-400:])
        rows = json.loads(proc.stdout)
        self.assertEqual(len(rows), len(cg.STAGES))

    def test_ciwf_a_failing_stage_makes_the_runner_exit_nonzero(self):
        """Behavioural: the runner must PROPAGATE a red, not summarise it away."""
        stage = cg._stage("selftest-red", "deliberate failure",
                          ("python3", "-c", "import sys; sys.exit(3)"),
                          (cg.FULL,), "x" * 70)
        result = cg.run_stage(stage, echo=False)
        self.assertEqual(result["returncode"], 3)
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
