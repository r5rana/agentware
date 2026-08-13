"""The NEGATIVE test for the modularity invariant (plan task 68).

Every other modularity gate in this tree asserts a fact about the tree AS IT IS:
no file over its cap, every file claimed, no undeclared upward import. That is a
statement about today. Task 68 asks for the statement about TOMORROW — that a
violation ARRIVING IN FUTURE WORK is caught — and the only way to make that claim
without hand-waving is to commit the violation and watch the suite go red.

So this file plants six violation shapes, one per rule that governs the widened
task-67 corpus, and asserts each one INDEPENDENTLY:

  1. a handler added to a lib module that is already at the 600-line cap;
  2. a function over the 120-line per-function cap, in a SMALL file (the shape a
     file cap structurally cannot see: `cmd_config` was 622 lines inside an
     837-line module, so splitting the file alone would have moved the monolith
     rather than removed it);
  3. an upward cross-layer import at module level (`core` importing `features`);
  4. a new file with no manifest entry;
  5. an over-cap loop script (the subsystem task 67's widening reached — before
     it, a 400-line loop lib produced no finding at all, because nothing looked);
  6. a dumping-ground module name (`agentware_lib/ops/utils.py`), planted WITH a
     regenerated manifest so the red is about the NAME and not about the claim.

## How the claim is made honestly

The suite is run FOR REAL, as a subprocess, against a full scratch COPY of the
gated surface — never against the checkout under test, which no gate may dirty.
Each shape gets its own pristine copy, so "reddens independently" means what it
says: no shape is standing on another's mutation.

Three properties make the result mean something:

  * BASELINE — the pristine copy is asserted GREEN on all five gate ids first. A
    red that was already red proves nothing, and a copy that is subtly broken
    (a missing fixture, a path that did not survive) would otherwise read as a
    successful refutation of every rule at once.
  * NAMED — a red is not enough; the failure output must NAME the planted path.
    A gate that fails for an unrelated reason is not the gate catching the
    violation, and an assertion on the exit code alone cannot tell the two apart.
  * CORRECTED — every shape is then FIXED the way a contributor would fix it
    (split the module and regenerate the manifest, split the function, drop the
    upward edge, claim the file, split the script, rename the module) and the
    same gate id must go GREEN. Without that half, a gate that reddens on
    everything would pass this file.

The corrections are deliberately the real remedy rather than `git checkout`: a
revert would prove only that the gate is a function of the bytes, while a fix
proves the rule is satisfiable — which is the property that decides whether a
contributor who hits it can get back to green at all.

Stdlib only; hermetic (its own tempdir copy, no network); the only subprocess is
this repo's own test runner.
"""

from tests._paths import REPO_ROOT

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

#: The gate ids this file drives, keyed by the shape they own. Full ids rather
#: than whole modules: a targeted method costs one AST scan instead of forty.
GATES = {
    "lib_cap": "tests.gates.test_module_ceilings."
               "LibFileCapIsSatisfiedOutrightTest."
               "test_no_lib_file_exceeds_the_600_line_cap",
    "ratchet": "tests.gates.test_module_ceilings.CeilingDebtRatchetTest."
               "test_live_violations_match_the_declared_debt",
    "claimed": "tests.gates.test_module_manifest.EveryFileOnDiskIsClaimedTest."
               "test_every_corpus_member_has_a_manifest_entry",
    "upward": "tests.gates.test_modularity_rules.LayerDirectionTest."
              "test_no_undeclared_module_level_upward_import",
    "names": "tests.gates.test_modularity_rules.GrabbagNameTest."
             "test_no_undeclared_dumping_ground_name",
}

#: What the scratch copy needs to be a measurable tree: the whole corpus plus the
#: catalog the completeness check reads and the fixtures the ratchets read.
COPY_DIRS = ("scripts", "tests", "catalog", os.path.join("webui", "src"))
COPY_FILES = ("agentware.sh",)
COPY_IGNORE = shutil.ignore_patterns("__pycache__", "node_modules", "dist",
                                     ".git", "*.pyc")

#: Targets. Named here rather than inline so the sizes that make each shape the
#: shape it is stay visible: skills.py sits at 598 of 600, dates.py is a 94-line
#: module (so shape 2 cannot be mistaken for a file-cap failure), review.sh is
#: 224 of 250.
AT_CAP_MODULE = "scripts/agentware_lib/features/skills.py"
SMALL_MODULE = "scripts/agentware_lib/core/dates.py"
CORE_MODULE = "scripts/agentware_lib/core/paths.py"
LOOP_SCRIPT = "scripts/loop/review.sh"

NEW_HANDLER = '''

def cmd_skill_export(args):
    """A handler added to a module that was already at its cap."""
    kdir = require_knowledge_dir()
    out = []
    for name in sorted(os.listdir(os.path.join(kdir, "skills"))):
        if name.startswith("."):
            continue
        out.append(name)
    print("\\n".join(out))
    return 0
'''

EXTRACTED_MODULE = '''"""Owns: the skill EXPORT handler, extracted so its arrival does not push
features/skills.py past the 600-line cap.

Does NOT own: skill installation, removal or the roster (features.skills).
"""

import os

from agentware_lib.core.config import require_knowledge_dir
''' + NEW_HANDLER

UPWARD_IMPORT = "from agentware_lib.features.dream import DREAM_STALE_HOURS\n"

NEW_LIB_MODULE = '''"""Owns: nothing yet — a module added without its manifest entry.

Does NOT own: anything.
"""


def helper():
    return 1
'''

GRABBAG_MODULE = '''"""Owns: a couple of path helpers, under a name that claims no boundary.

Does NOT own: anything in particular, which is the defect.
"""


def shorten(path):
    return path.rsplit("/", 1)[-1]
'''

RENAMED_MODULE = GRABBAG_MODULE.replace(
    "a couple of path helpers, under a name that claims no boundary",
    "path shortening for display").replace(
    "anything in particular, which is the defect",
    "path resolution or validation (core.paths)")


def _long_function(nlines, name="_added"):
    """A syntactically real function of `nlines` body statements, no branches."""
    body = "".join("    total = total + %d\n" % i for i in range(nlines))
    return "\n\ndef %s():\n    total = 0\n%s    return total\n" % (name, body)


def _shell_block(nlines, name="review_extra_note"):
    body = "".join('  echo "line %d"\n' % i for i in range(nlines))
    return "\n%s() {\n%s}\n" % (name, body)


class _Tree(object):
    """A scratch copy of the gated surface, with the gate runner attached."""

    def __init__(self, testcase):
        self.case = testcase
        self.root = tempfile.mkdtemp(prefix="aw-negative-")
        testcase.addCleanup(shutil.rmtree, self.root, True)
        for rel in COPY_DIRS:
            src = os.path.join(REPO_ROOT, rel)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(self.root, rel),
                                ignore=COPY_IGNORE)
        for rel in COPY_FILES:
            src = os.path.join(REPO_ROOT, rel)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(self.root, rel))

    # --- filesystem -------------------------------------------------------

    def path(self, rel):
        return os.path.join(self.root, rel)

    def read(self, rel):
        with open(self.path(rel), encoding="utf-8") as f:
            return f.read()

    def write(self, rel, text):
        full = self.path(rel)
        parent = os.path.dirname(full)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)

    def append(self, rel, text):
        self.write(rel, self.read(rel) + text)

    def remove(self, rel):
        os.remove(self.path(rel))

    def lines(self, rel):
        return len(self.read(rel).splitlines())

    # --- the tree's own tooling -------------------------------------------

    def regen_manifest(self):
        """Run the real generator, the way the failure message tells you to."""
        proc = self._run([sys.executable, "tests/_manifest_gen.py", "--write"])
        self.case.assertEqual(proc.returncode, 0,
                              "regenerating the manifest failed:\n%s"
                              % proc.stdout)
        return proc.stdout

    def _run(self, argv):
        env = dict(os.environ)
        # The gate reads its root from the checkout it lives in; an inherited
        # override would silently aim the scratch run back at the real tree.
        env.pop("AGENTWARE_CEILING_ROOT", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(argv, cwd=self.root, env=env, timeout=600,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True)

    def run_gate(self, key):
        proc = self._run([sys.executable, "-m", "unittest", GATES[key]])
        return proc.returncode, proc.stdout

    # --- assertions -------------------------------------------------------

    def assert_red(self, key, *needles):
        rc, out = self.run_gate(key)
        self.case.assertNotEqual(
            rc, 0, "%s stayed GREEN over a planted violation — the rule does "
                   "not hold for future work:\n%s" % (key, out))
        for needle in needles:
            self.case.assertIn(
                needle, out,
                "%s failed, but its output never names %r, so this red is not "
                "the planted violation:\n%s" % (key, needle, out))
        return out

    def assert_green(self, key):
        rc, out = self.run_gate(key)
        self.case.assertEqual(
            rc, 0, "%s stayed RED after the violation was corrected — the rule "
                   "is not satisfiable, which makes it a wall rather than a "
                   "gate:\n%s" % (key, out))
        return out


class ScratchTreeBaselineTest(unittest.TestCase):
    """The precondition every shape below depends on."""

    def test_the_pristine_scratch_copy_is_green_on_every_gate_this_file_drives(self):
        tree = _Tree(self)
        for key in sorted(GATES):
            tree.assert_green(key)

    def test_the_copy_actually_contains_the_tree(self):
        # A copy that lost the corpus would make every rule below vacuously
        # green, and the baseline test above would pass on nothing.
        tree = _Tree(self)
        for rel in (AT_CAP_MODULE, SMALL_MODULE, LOOP_SCRIPT,
                    "catalog/modules.json", "tests/fixtures/ceiling-debt.json",
                    "tests/fixtures/modularity-debt.json"):
            self.assertTrue(os.path.isfile(tree.path(rel)),
                            "the scratch copy is missing %s" % rel)
        self.assertGreater(tree.lines(AT_CAP_MODULE), 500)


class SixViolationShapesTest(unittest.TestCase):
    """One test per shape: plant it, prove the red names it, then correct it."""

    def test_1_a_handler_added_to_a_module_already_at_the_cap(self):
        tree = _Tree(self)
        self.assertLess(tree.lines(AT_CAP_MODULE), 600)
        tree.append(AT_CAP_MODULE, NEW_HANDLER)
        self.assertGreater(tree.lines(AT_CAP_MODULE), 600)

        out = tree.assert_red("lib_cap", AT_CAP_MODULE)
        self.assertIn("no debt available", out,
                      "the lib file cap must offer no forgiveness — a new "
                      "monolith may not be legalised by declaring it")

        # The correction a contributor makes: the handler moves into its own
        # module, and the manifest learns about it.
        tree.write(AT_CAP_MODULE, tree.read(AT_CAP_MODULE)[:-len(NEW_HANDLER)])
        tree.write("scripts/agentware_lib/features/skillexport.py",
                   EXTRACTED_MODULE)
        tree.regen_manifest()
        tree.assert_green("lib_cap")
        tree.assert_green("claimed")

    def test_2_a_function_over_the_per_function_cap_in_a_small_file(self):
        tree = _Tree(self)
        tree.append(SMALL_MODULE, _long_function(130))
        self.assertLess(tree.lines(SMALL_MODULE), 600,
                        "the file must stay well UNDER its cap, or this shape "
                        "would be indistinguishable from shape 1")

        tree.assert_red("ratchet", "%s::_added" % SMALL_MODULE,
                        "function_over_line_cap")

        # The correction: two functions, each under the cap. The FILE is the
        # same length it was — which is exactly why a file cap could not see
        # this violation and cannot see this fix either.
        before = tree.read(SMALL_MODULE)
        tree.write(SMALL_MODULE,
                   before[:-len(_long_function(130))]
                   + _long_function(60, "_added_first")
                   + _long_function(60, "_added_second"))
        tree.assert_green("ratchet")

    def test_3_an_upward_cross_layer_import(self):
        tree = _Tree(self)
        src = tree.read(CORE_MODULE)
        tree.write(CORE_MODULE, src + "\n" + UPWARD_IMPORT)

        tree.assert_red("upward", CORE_MODULE, "features")

        # The correction: the edge goes away. (A genuinely irreducible upward
        # SYMBOL edge has a second remedy — the function-local import — which
        # `test_modularity_rules` pins against a real module.)
        tree.write(CORE_MODULE, src)
        tree.assert_green("upward")

    def test_4_a_new_file_with_no_manifest_entry(self):
        tree = _Tree(self)
        rel = "scripts/agentware_lib/ops/undeclared_newthing.py"
        tree.write(rel, NEW_LIB_MODULE)

        tree.assert_red("claimed", rel)

        # The correction is the one the failure message prints.
        tree.regen_manifest()
        tree.assert_green("claimed")

    def test_5_an_over_cap_loop_script(self):
        tree = _Tree(self)
        self.assertLess(tree.lines(LOOP_SCRIPT), 250)
        block = _shell_block(40)
        tree.append(LOOP_SCRIPT, block)
        self.assertGreater(tree.lines(LOOP_SCRIPT), 250)

        tree.assert_red("ratchet", LOOP_SCRIPT, "file_over_cap")

        # The correction: the addition becomes its own lib, claimed like every
        # other file. Splitting a shell lib is the same move as splitting a
        # module — the loop sources what it needs.
        tree.write(LOOP_SCRIPT, tree.read(LOOP_SCRIPT)[:-len(block)])
        tree.write("scripts/loop/reviewextra.sh",
                   "#!/usr/bin/env bash\n# shellcheck shell=bash\n" + block)
        tree.regen_manifest()
        tree.assert_green("ratchet")
        tree.assert_green("claimed")

    def test_6_a_dumping_ground_module_name(self):
        tree = _Tree(self)
        rel = "scripts/agentware_lib/ops/utils.py"
        tree.write(rel, GRABBAG_MODULE)
        # Regenerated FIRST, so the file is claimed, capped and inside every
        # other rule. The only thing wrong with it is its name.
        tree.regen_manifest()
        tree.assert_green("claimed")

        tree.assert_red("names", rel)

        # The correction: a name that says what the module owns.
        tree.remove(rel)
        tree.write("scripts/agentware_lib/ops/pathfmt.py", RENAMED_MODULE)
        tree.regen_manifest()
        tree.assert_green("names")
        tree.assert_green("claimed")


class EveryShapeIsCoveredTest(unittest.TestCase):
    """The roster check: the plan names six shapes, so six must be here."""

    def test_there_is_one_test_per_named_shape(self):
        planted = [name for name in dir(SixViolationShapesTest)
                   if name.startswith("test_")]
        self.assertEqual(
            len(planted), 6,
            "plan task 68 names SIX violation shapes; this file drives %d — a "
            "shape without a test is a rule nobody has proven catches anything: "
            "%r" % (len(planted), sorted(planted)))

    def test_every_gate_id_this_file_names_actually_exists(self):
        # A typo in a gate id makes `python3 -m unittest <id>` fail with an
        # IMPORT error — a non-zero exit that reads exactly like a caught
        # violation. Resolving each id here separates the two for good.
        loader = unittest.TestLoader()
        for key, test_id in sorted(GATES.items()):
            suite = loader.loadTestsFromName(test_id)
            self.assertEqual(suite.countTestCases(), 1,
                             "%s does not resolve to exactly one test" % key)
            self.assertFalse(
                any("_FailedTest" in type(t).__name__
                    for t in suite),
                "gate id for %r does not resolve: %s" % (key, test_id))


if __name__ == "__main__":
    unittest.main()
