"""A fresh clone, from an EMPTY directory, reaches a working state with ONE command.

Feature 260813-modular-core-adoption, task 78 (the second acceptance criterion:
"a fresh-clone bootstrap test passes from an empty directory").

WHAT THIS PROVES THAT `tests/e2e/test_fresh_clone.py` CANNOT
------------------------------------------------------------
That module already stands up a hermetic empty-KB world and asserts it is
healthy — but it runs against the checkout the suite itself lives in. Everything
on the developer's disk is therefore available to it, which makes it blind to
exactly the class of defect a real clone exposes:

  * a file that works because it is UNTRACKED, and so would never ship;
  * a generated artifact that exists only because someone ran the generator once
    and never committed the result;
  * an import that resolves off the developer's environment rather than the
    repository's own `sys.path` bootstrap;
  * a step that silently needs a dependency the developer happens to have.

This module clones into a directory it first asserts is EMPTY, then runs the one
documented command inside the clone. `tests/_bootstrap_gate.py` owns the
mechanics and the reasoning (including why the clone is patched with the
working-tree diff of TRACKED files only); this module is the suite's route into
it, plus the assertions about the clone itself that belong to a test rather than
to a runnable checker.

COST NOTE. The clone is a local one, so git hardlinks the object store and the
whole path runs in single-digit seconds. It is in `e2e/` because it is a whole
user-visible path — the clone-and-go promise — not because it is slow.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests._paths import REPO_ROOT

sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

import _bootstrap_gate as bg      # noqa: E402
import _ci_gate as cg             # noqa: E402


def _git(argv, cwd):
    proc = subprocess.run(["git"] + list(argv), cwd=cwd, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc.returncode, proc.stdout or ""


class FreshCloneFromAnEmptyDirectoryTest(unittest.TestCase):
    """The clone step itself: it starts empty, and it starts from git."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-ciboot-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_ciboot_the_destination_is_empty_before_the_clone(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        self.assertEqual(os.listdir(empty), [])
        clone, notes = bg.clone_into_empty(empty)
        self.assertTrue(os.path.isdir(os.path.join(clone, ".git")))
        self.assertTrue(notes, "the clone reported nothing about what it did")

    def test_ciboot_a_non_empty_destination_is_refused(self):
        """'From an empty directory' is the claim, so it is asserted, not assumed."""
        dirty = os.path.join(self.tmp, "dirty")
        os.makedirs(dirty)
        with open(os.path.join(dirty, "leftover"), "w", encoding="utf-8") as f:
            f.write("state from a previous run\n")
        self.assertRaises(ValueError, bg.clone_into_empty, dirty)

    def test_ciboot_the_clone_carries_the_tracked_tree(self):
        """A clone missing the entrypoint would make every later assertion moot."""
        clone, _ = bg.clone_into_empty(os.path.join(self.tmp, "e"))
        for rel in ("scripts/agentware", "tests/_ci_gate.py",
                    "tests/_bootstrap_gate.py", "AGENTS.md",
                    ".github/workflows/ci.yml"):
            self.assertTrue(os.path.exists(os.path.join(clone, rel)),
                            "the fresh clone has no %s" % rel)

    def test_ciboot_the_clone_needs_no_dependency_install(self):
        """Clone-and-go: nothing between `git clone` and the documented command."""
        clone, _ = bg.clone_into_empty(os.path.join(self.tmp, "e"))
        for manifest in ("requirements.txt", "Pipfile", "poetry.lock",
                         "setup.py", "pyproject.toml"):
            self.assertFalse(
                os.path.exists(os.path.join(clone, manifest)),
                "%s ships in the clone; the bootstrap claim is that NO install "
                "step precedes the one documented command (INV-6)" % manifest)

    def test_ciboot_the_clone_is_a_real_repository_at_our_head(self):
        clone, _ = bg.clone_into_empty(os.path.join(self.tmp, "e"))
        rc, cloned_head = _git(["rev-parse", "HEAD"], clone)
        self.assertEqual(rc, 0)
        rc, our_head = _git(["rev-parse", "HEAD"], REPO_ROOT)
        self.assertEqual(cloned_head.strip(), our_head.strip())


class TheOneDocumentedCommandTest(unittest.TestCase):
    """The command a new contributor types, run where they would type it."""

    def test_ciboot_it_is_one_command_with_no_shell_operators(self):
        for operator in bg.COMPOUND_OPERATORS:
            self.assertNotIn(operator, bg.BOOTSTRAP_COMMAND)
        self.assertTrue(bg.BOOTSTRAP_COMMAND.startswith("python3 "),
                        "the documented command must be runnable by a stock "
                        "python3 with nothing installed")

    def test_ciboot_the_command_reports_itself(self):
        proc = subprocess.run(
            [sys.executable, "tests/_bootstrap_gate.py", "--command"],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), bg.BOOTSTRAP_COMMAND)

    def test_ciboot_a_fresh_clone_reaches_a_working_state(self):
        """THE criterion. Clone into an empty dir; run the documented command."""
        failures = bg.from_empty(echo=False)
        self.assertEqual(failures, [],
                         "a fresh clone did not reach a working state:\n%s"
                         % "\n".join(failures))


class TheHealthSequenceTest(unittest.TestCase):
    """What 'a working state' MEANS, asserted as behaviour rather than prose."""

    def test_ciboot_the_health_sequence_passes_on_this_tree(self):
        self.assertEqual(bg.health(echo=False), [])

    def test_ciboot_the_health_sequence_leaves_the_real_config_untouched(self):
        """`init` writes ~/.agentware/config.env; a leak clobbers the operator."""
        before = bg._real_config_digest()
        bg.health(echo=False)
        self.assertEqual(before, bg._real_config_digest())

    def test_ciboot_a_broken_tree_is_reported_rather_than_passed(self):
        """Non-vacuity: the sequence must be able to FAIL on a real defect."""
        tmp = tempfile.mkdtemp(prefix="agentware-ciboot-broken-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        broken = os.path.join(tmp, "tree")
        rc, out = _git(["clone", "--quiet", "--no-tags", REPO_ROOT, broken], tmp)
        self.assertEqual(rc, 0, out)
        shutil.rmtree(os.path.join(broken, "scripts", "agentware_lib",
                                   "knowledge", "index"))
        failures = bg.health(root=broken, echo=False)
        self.assertTrue(failures,
                        "the health sequence passed a tree with a load-bearing "
                        "package deleted — it is measuring nothing")

    def test_ciboot_the_expected_audit_checks_are_ones_an_empty_kb_can_pass(self):
        """A gate must assert what is true, not what would be nice."""
        self.assertTrue(set(bg.EXPECTED_AUDIT_CHECKS)
                        <= {"index_validate", "steering_lint", "personal_data",
                            "filesystem_sync", "tag_consistency"},
                        "an audit check that needs CONTENT cannot be asserted "
                        "against a freshly-scaffolded, empty knowledge base")


class TheBootstrapProfileTest(unittest.TestCase):
    """The profile the clone runs, and the guard that stops it recursing."""

    def test_ciboot_the_bootstrap_profile_terminates(self):
        ids = [s["id"] for s in cg.stages_for(cg.BOOTSTRAP)]
        self.assertNotIn("bootstrap", ids,
                         "the profile the clone runs contains the stage that "
                         "makes a clone")

    def test_ciboot_the_bootstrap_profile_runs_no_test_tier(self):
        """It answers 'does this clone work', not 'is the suite green'."""
        for stage in cg.stages_for(cg.BOOTSTRAP):
            self.assertFalse(stage["id"].startswith("suite-"),
                             "%s puts a whole test tier inside the fresh-clone "
                             "path, which the full profile already runs"
                             % stage["id"])

    def test_ciboot_the_bootstrap_profile_includes_the_health_sequence(self):
        self.assertIn("clone-health",
                      [s["id"] for s in cg.stages_for(cg.BOOTSTRAP)])


if __name__ == "__main__":
    unittest.main()
