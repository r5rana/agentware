"""The CI workflow, the stage table, and the one documented bootstrap command.

Feature 260813-modular-core-adoption, task 78.

WHAT THIS GATE IS FOR
---------------------
A CI workflow is the single easiest artifact in a repository to make LOOK like
verification while measuring nothing. Task 78 found three separate ways that had
already happened here, before a line of YAML existed:

  * `python3 tests/_ceiling_gate.py` and `python3 tests/_modularity_rules.py` --
    both named in CONTRIBUTING.md as "the structural checkers", both named in
    docs/architecture.md's gate table, both named in the modular-development
    skill's finish-line list -- had NO `if __name__` dispatch. Running either
    imported a module, defined its functions and exited 0. Seven task worklogs
    recorded "rc=0" for them as evidence.
  * `python3 tests/_manifest_gen.py --check` ignored the flag entirely and fell
    through to the branch that prints the manifest, also exiting 0. It agreed
    with the committed file on the day it was measured, by luck.

Wiring those into a workflow as they stood would have produced a green CI badge
backed by three commands that could not fail. So this gate does not ask "is
there a workflow"; it asks the four questions that make a workflow mean
something, and every arm is proven to bite on a scratch copy.

  A. The workflow is well-formed and its supply chain is bounded -- explicit
     triggers, a timeout on every job, least-privilege permissions, and every
     `uses:` a first-party `actions/*` with an explicit ref.
  B. **The workflow runs the entrypoint and NOTHING else.** No `run:` line may
     name a gate command other than `tests/_ci_gate.py`. This is what makes the
     task's criterion -- "the workflow runs green via the same entrypoint
     locally" -- a mechanical property instead of a promise.
  C. **Every gate task 78 names is actually a stage**, matched through
     `_ci_gate.TASK_COVERAGE`, which maps the task's own words onto stage ids so
     a dropped stage fails by NAME rather than by silence.
  (Arms D/E/F live in the sibling `test_ci_gate_stages.py`; the shared reader
  lives in `_ci_workflow_support.py`.)

  D. **Every stage is EFFECTIVE, not vacuous.** Each harness a stage names must
     have a real `__main__` dispatch, and the three ratchet commands must go RED
     on a planted defect. This is the arm that would have caught the three
     no-op checkers, and it is behavioural: it runs the commands.
  E. **The one documented command is documented, once.** The bytes in
     `_bootstrap_gate.BOOTSTRAP_COMMAND` must appear in CONTRIBUTING.md and in
     the workflow, and the bootstrap profile must not contain the stage that
     clones -- which is the recursion guard, not a style rule.

Stdlib only (INV-6): the workflow is parsed with a small purpose-built reader
rather than PyYAML, which is not installed on a stock clone. The reader is
deliberately shallow -- it extracts the keys these arms assert on and nothing
else -- because a gate that needs a full YAML implementation to run is a gate
that stops running the day the dependency is unavailable.
"""


import os
import re
import unittest

from tests.gates._ci_workflow_support import (  # noqa: F401
    ENTRYPOINT_REL, GATE_COMMAND_PATTERNS, REPO_ROOT, WORKFLOW, WORKFLOW_REL,
    _block, _read, _uncommented, bg, cg, job_names, run_lines, sg,
    top_level_keys, trigger_events, uses_refs)


class WorkflowIsWellFormedTest(unittest.TestCase):
    """Arm A — shape, triggers, timeouts, permissions, supply chain."""

    def setUp(self):
        self.text = _read(WORKFLOW_REL)

    def test_ciwf_the_workflow_file_exists_and_is_not_a_stub(self):
        self.assertTrue(os.path.isfile(WORKFLOW),
                        "%s is missing" % WORKFLOW_REL)
        self.assertGreater(len(self.text), 800,
                           "the workflow is a stub; a CI file nobody can read "
                           "is one nobody maintains")

    def test_ciwf_it_declares_a_name_triggers_and_jobs(self):
        keys = top_level_keys(self.text)
        for key in ("name", "on", "permissions", "jobs"):
            self.assertIn(key, keys, "the workflow declares no top-level %r" % key)

    def test_ciwf_it_runs_on_push_and_pull_request(self):
        events = trigger_events(self.text)
        for event in ("push", "pull_request"):
            self.assertIn(event, events,
                          "the workflow does not run on %s; declared events are "
                          "%s" % (event, events))

    def test_ciwf_every_job_carries_a_timeout(self):
        jobs = job_names(self.text)
        self.assertTrue(jobs, "no job found under jobs:")
        timeouts = re.findall(r"^\s*timeout-minutes:\s*(\d+)",
                              "\n".join(_block(self.text, "jobs")), re.M)
        self.assertEqual(len(timeouts), len(jobs),
                         "every job needs timeout-minutes and %d of %d have one "
                         "(%s): a hung step at 0.0%% CPU is the failure mode "
                         "this suite documents, and without a timeout it burns "
                         "the runner instead of reporting"
                         % (len(timeouts), len(jobs), ", ".join(jobs)))

    def test_ciwf_permissions_are_least_privilege(self):
        self.assertIn("contents: read",
                      "\n".join(_block(self.text, "permissions")),
                      "the workflow does not drop its token to contents: read")

    def test_ciwf_the_block_reader_does_not_confuse_on_keys_with_jobs(self):
        """The reader's own regression: `on:` and `jobs:` share an indent.

        A first draft of this gate scanned `^  (\\w+):` over the whole file,
        counted `push:` as a job, and reported "1 of 2 jobs have a timeout" —
        a failure message that was a lie about the file. Scoping is asserted
        here so the reader cannot silently regress to that.
        """
        self.assertEqual(job_names(self.text), ["gate"])
        self.assertNotIn("push", job_names(self.text))
        self.assertIn("push", trigger_events(self.text))
        self.assertNotIn("gate", trigger_events(self.text))

    def test_ciwf_every_action_is_first_party_and_carries_an_explicit_ref(self):
        refs = uses_refs(self.text)
        self.assertTrue(refs, "the workflow uses no actions at all — if that is "
                              "deliberate, delete this assertion with a reason")
        for ref in refs:
            self.assertIn("@", ref, "%s has no version ref — an unpinned action "
                                    "is whatever its author pushed today" % ref)
            self.assertTrue(ref.startswith("actions/"),
                            "%s is a third-party action; this workflow is "
                            "first-party only, and the package installs no "
                            "dependencies to compromise" % ref)

    def test_ciwf_the_checkout_is_not_shallow(self):
        self.assertRegex(_uncommented(self.text), r"fetch-depth:\s*0",
                         "the portability gate reasons about git metadata and "
                         "the branch commit range; a shallow clone makes it "
                         "under-report rather than fail")


class WorkflowRunsOnlyTheEntrypointTest(unittest.TestCase):
    """Arm B — no gate command may live in the YAML. The drift-proof property."""

    def setUp(self):
        self.text = _read(WORKFLOW_REL)

    def test_ciwf_the_workflow_invokes_the_entrypoint(self):
        self.assertTrue(
            any(ENTRYPOINT_REL in line for line in run_lines(self.text)),
            "no run: line invokes %s — the workflow runs something else"
            % ENTRYPOINT_REL)

    def test_ciwf_no_run_line_names_a_gate_command_other_than_the_entrypoint(self):
        offenders = self._offenders(self.text)
        self.assertEqual(offenders, [],
                         "the workflow inlines gate command(s) instead of "
                         "calling %s: %s. A gate that exists only in YAML "
                         "cannot be run locally and drifts from CONTRIBUTING.md "
                         "the first time either side changes."
                         % (ENTRYPOINT_REL, offenders))

    def test_ciwf_arm_b_bites_on_an_inlined_suite_run(self):
        """Behavioural: plant the exact drift this arm exists to forbid."""
        planted = self.text.replace(
            "      - name: Run the gate",
            "      - name: Run the tests\n"
            "        run: python3 -m unittest discover -s tests/unit -t .\n"
            "      - name: Run the gate")
        self.assertNotEqual(planted, self.text, "the mutation did not apply")
        offenders = self._offenders(planted)
        self.assertTrue(offenders,
                        "arm B did not notice an inlined `-m unittest` run step")

    def test_ciwf_arm_b_bites_on_an_inlined_checker(self):
        planted = self.text.replace(
            "        run: python3 tests/_ci_gate.py",
            "        run: |\n"
            "          python3 tests/_ceiling_gate.py\n"
            "          python3 tests/_ci_gate.py")
        self.assertNotEqual(planted, self.text, "the mutation did not apply")
        self.assertTrue(self._offenders(planted),
                        "arm B did not notice an inlined structural checker "
                        "inside a block scalar")

    def test_ciwf_arm_b_does_not_fire_on_the_sanctioned_entrypoint(self):
        """The twin: the allowed command must stay LEGAL, or the arm bans CI."""
        self.assertEqual(
            self._offenders("jobs:\n  g:\n    steps:\n"
                            "      - run: python3 tests/_ci_gate.py\n"), [])

    def test_ciwf_setup_commands_are_not_mistaken_for_gates(self):
        """`git config` is setup. An arm that banned it would ban working CI."""
        self.assertEqual(
            self._offenders('jobs:\n  g:\n    steps:\n      - run: |\n'
                            '          git config --global user.name "x"\n'), [])

    @staticmethod
    def _offenders(text):
        found = []
        for line in run_lines(text):
            if ENTRYPOINT_REL in line:
                continue
            for pattern in GATE_COMMAND_PATTERNS:
                if re.search(pattern, line):
                    found.append(line)
                    break
        return found


class StageTableCoversTheTaskTest(unittest.TestCase):
    """Arm C — every gate task 78 names resolves to a declared, runnable stage."""

    def test_ciwf_every_declared_coverage_phrase_maps_to_real_stages(self):
        ids = set(s["id"] for s in cg.STAGES)
        for phrase, stage_ids in cg.TASK_COVERAGE:
            self.assertTrue(stage_ids, "%r maps to no stage" % phrase)
            missing = [sid for sid in stage_ids if sid not in ids]
            self.assertEqual(missing, [],
                             "task 78 requires %r but stage(s) %s are not "
                             "declared" % (phrase, missing))

    def test_ciwf_the_coverage_table_names_every_gate_the_task_lists(self):
        """The task's own words, so a reviewer needs no second document."""
        required = ("the full suite", "the portability gate", "the parity gate",
                    "the body differ", "the constant differ",
                    "the modularity ratchet", "the negative test",
                    "steering lint")
        declared = [phrase for phrase, _ in cg.TASK_COVERAGE]
        for phrase in required:
            self.assertIn(phrase, declared,
                          "task 78 names %r and the coverage table does not"
                          % phrase)

    def test_ciwf_the_full_suite_is_four_tier_scoped_runs(self):
        tiers = [s for s in cg.STAGES if s["id"].startswith("suite-")]
        self.assertEqual(sorted(s["id"] for s in tiers),
                         sorted("suite-" + t for t in cg.SUITE_TIERS))
        for stage in tiers:
            argv = stage["argv"]
            self.assertIn("tests/_suite_gate.py", argv,
                          "%s runs unittest directly, which implements 'all "
                          "pass' — this repository's definition of green is 'no "
                          "worse than the recorded baseline' "
                          "(tests/baselines/README.md)" % stage["id"])
            self.assertIn("--tier", argv)
            self.assertIn(stage["id"].split("-", 1)[1], argv)

    def test_ciwf_no_suite_stage_runs_a_whole_tree_discover(self):
        """The collection that intermittently hangs must not be reachable."""
        for stage in cg.STAGES:
            joined = " ".join(stage["argv"]) + " "
            self.assertNotIn("-s tests ", joined,
                             "%s collects tests/fixtures/ (tests/README.md)"
                             % stage["id"])
            self.assertNotIn("discover -s tests -t", joined)

    def test_ciwf_every_stage_belongs_to_at_least_one_profile(self):
        for stage in cg.STAGES:
            self.assertTrue(stage["profiles"],
                            "stage %s is in no profile, so nothing runs it"
                            % stage["id"])
            for profile in stage["profiles"]:
                self.assertIn(profile, cg.PROFILES)

    def test_ciwf_every_stage_states_why_it_exists(self):
        for stage in cg.STAGES:
            self.assertGreater(len(stage["why"]), 60,
                               "stage %s does not say what it protects; a gate "
                               "nobody can justify is one that gets deleted the "
                               "first time it is inconvenient" % stage["id"])

    def test_ciwf_stage_ids_are_unique(self):
        ids = [s["id"] for s in cg.STAGES]
        self.assertEqual(sorted(ids), sorted(set(ids)), "duplicate stage id")

    def test_ciwf_a_dropped_stage_is_reported_by_the_task_phrase(self):
        """Behavioural: remove a stage and confirm arm C names the task's words."""
        surviving = set(s["id"] for s in cg.STAGES if s["id"] != "portability")
        broken = [(p, ids) for p, ids in cg.TASK_COVERAGE
                  if any(i not in surviving for i in ids)]
        self.assertEqual([p for p, _ in broken], ["the portability gate"])


if __name__ == "__main__":
    unittest.main()
