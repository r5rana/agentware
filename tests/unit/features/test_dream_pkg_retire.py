"""Dream step-0 pkg-pull retirement tests (feature 260712, Task 13).

Proves the CRITICAL invariant closed: dream no longer runs an UNSIGNED tip-of-main
pull/migrate on REPO_ROOT, so exactly ONE code path advances the package
(_pkg_run_update). Covers the step-list removal, the retired functions being gone,
the distinct updater/dream locks, a dream dry-run planning no package pull, and a
dream invocation leaving REPO_ROOT HEAD unchanged. Hermetic (R-LOC-03).

Runner: python3 -m unittest tests.unit.features.test_dream_pkg_retire -v
"""

from tests._paths import REPO_ROOT

import json
import os
import shutil
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, run_cli, build_synthetic_kb
except ImportError:
    from _fixtures import load_cli, run_cli, build_synthetic_kb



class DreamPkgRetireTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()

    def test_no_pkg_pull_step_in_registry(self):
        steps = [s for s, _l, _f in self.mod.DREAM_STEP_FUNCS]
        labels = [l for _s, l, _f in self.mod.DREAM_STEP_FUNCS]
        self.assertNotIn("0", steps)
        self.assertNotIn("pkg-pull", labels)
        # "g" (prefer-classify, 260712 Task 7) runs before the final kb-git-commit;
        # "k" (skill-propose, 260722 Task 4) is ordered after "s", before "f".
        self.assertEqual(
            steps, ["1", "2", "a", "b", "c", "d", "e", "s", "k", "m", "g", "f"])

    def test_retired_functions_removed(self):
        for name in ("_dream_step_pkg_pull", "_dream_pkg_syntax_check",
                     "_dream_pkg_pull_revert", "_dream_apply_migrations_after_pull"):
            self.assertFalse(hasattr(self.mod, name),
                             "%s must be retired (rival unsigned updater)" % name)
        # The shared post-check used by the KB-pull step is RETAINED.
        self.assertTrue(hasattr(self.mod, "_dream_pull_post_check"))

    def test_updater_lock_distinct_from_dream_lock(self):
        self.assertNotEqual(self.mod.UPDATER_LOCK_REL, self.mod.DREAM_LOCK_REL)

    def test_only_pkg_run_update_advances_repo_root(self):
        # No remaining dream step function references a package-advancing git verb
        # on REPO_ROOT (pull/merge/reset). Static guard over the step sources.
        import inspect
        for step, label, fn in self.mod.DREAM_STEP_FUNCS:
            src = inspect.getsource(fn)
            self.assertNotIn("REPO_ROOT", src,
                             "dream step %s (%s) must not touch REPO_ROOT" % (step, label))

    def test_dream_dry_run_plans_no_pkg_pull(self):
        kdir = tempfile.mkdtemp(prefix="agentware-retire-")
        self.addCleanup(shutil.rmtree, kdir, True)
        build_synthetic_kb(kdir)
        code, out, _e = run_cli(["dream", "--dry-run", "--format", "json"], kdir)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        step_ids = [s["step"] for s in payload["steps"]]
        self.assertNotIn("0", step_ids)
        for s in payload["steps"]:
            self.assertNotIn("pull agentware", json.dumps(s))

    def test_dream_leaves_repo_root_head_unchanged(self):
        kdir = tempfile.mkdtemp(prefix="agentware-retire2-")
        self.addCleanup(shutil.rmtree, kdir, True)
        build_synthetic_kb(kdir)
        head_before = subprocess.run(["git", "-C", REPO_ROOT, "rev-parse", "HEAD"],
                                     stdout=subprocess.PIPE, text=True).stdout.strip()
        # kb-pull (step 1) + health-gate (step 2) operate on the KB, never REPO_ROOT.
        run_cli(["dream", "--steps", "1,2", "--format", "json"], kdir)
        head_after = subprocess.run(["git", "-C", REPO_ROOT, "rev-parse", "HEAD"],
                                    stdout=subprocess.PIPE, text=True).stdout.strip()
        self.assertEqual(head_before, head_after,
                         "a dream cycle must NEVER advance REPO_ROOT")


if __name__ == "__main__":
    unittest.main()
