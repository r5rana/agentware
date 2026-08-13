"""The gate for the worked add-a-feature walkthrough's checker (task 75).

Two halves, the shape every documentation gate in this tree carries:

  * a POSITIVE CONTROL — the shipped `docs/add-a-feature.md` passes every arm of
    `tests/_walkthrough_check.py` over a scratch copy of the real tree;
  * a MUTATION for every arm — planted one at a time into that scratch copy,
    each asserted to be caught by ITS OWN arm and named in the message.

The second half is the load-bearing one. A documentation checker that cannot be
made to fail is indistinguishable from one that returns `[]` unconditionally,
and the failure is silent: the doc keeps looking verified while it rots.

The scratch tree is a real copy of `docs/` plus an empty stub for every path the
checker resolves. Stubs rather than the whole corpus because every arm asks the
same question of a path — *does this exist* — and copying 700 real files to
answer it would make the gate slow enough to skip.
"""

from tests._paths import REPO_ROOT, TESTS_DIR

import os
import shutil
import sys
import tempfile
import unittest

if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

# Import the real package FIRST so `live_verbs` resolves the true verb surface
# from `sys.modules` rather than from whatever a scratch root happens to hold.
if os.path.join(REPO_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import _walkthrough_check as wc  # noqa: E402

WALKTHROUGH = wc.WALKTHROUGH
ARCHITECTURE = wc.ARCHITECTURE


def _touch(root, rel):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("# stub\n")


class WalkthroughGateTest(unittest.TestCase):
    """Every arm, proven on a scratch copy of the shipped document."""

    @classmethod
    def setUpClass(cls):
        cls.text = wc.read(REPO_ROOT, WALKTHROUGH)
        assert cls.text, "the walkthrough is missing from the real tree"

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-walkthrough-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        shutil.copytree(os.path.join(REPO_ROOT, "docs"),
                        os.path.join(self.root, "docs"),
                        ignore=shutil.ignore_patterns("assets", "design"))
        for rel in wc.REQUIRED_GATES:
            _touch(self.root, rel)
        text = self.text + "\n" + (wc.read(REPO_ROOT, ARCHITECTURE) or "")
        for rel in set(wc.TESTS_SCRIPT_RE.findall(text)):
            _touch(self.root, rel)
        for rel in set(wc.GATE_PATH_RE.findall(text)):
            _touch(self.root, rel)
        # Every file an `edit` block targets must EXIST for the arm-E question
        # ("does this path resolve") to be answerable; a `write` block creates
        # its own target and is deliberately not stubbed.
        for info, _body, _line in wc.blocks(self.text):
            kind, arg = wc.classify(info)
            if kind == "edit":
                _touch(self.root, arg)

    # ---------------------------------------------------------------- helpers

    def problems(self):
        return wc.check(self.root)

    def arms(self):
        return set(p.split(":", 1)[0] for p in self.problems())

    def rewrite(self, old, new, rel=WALKTHROUGH, count=1):
        """Plant a mutation. `count=-1` means every occurrence, never 0."""
        path = os.path.join(self.root, rel)
        with open(path, encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn(old, body, "mutation anchor not found in %s" % rel)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body.replace(old, new, count))

    def append(self, chunk, rel=WALKTHROUGH):
        with open(os.path.join(self.root, rel), "a", encoding="utf-8") as handle:
            handle.write(chunk)

    # -------------------------------------------------------- positive control

    def test_the_shipped_walkthrough_passes_every_arm(self):
        self.assertEqual(self.problems(), [])

    def test_the_checker_reports_the_real_tree_green(self):
        """The gate's subject is the LIVE tree, not only its scratch copy."""
        self.assertEqual(wc.check(REPO_ROOT), [])

    def test_the_document_declares_the_conventions_it_uses(self):
        self.assertIn("How to read this document", self.text)
        for fence in ("```bash", "```bash expect-fail", "```bash norun:"):
            self.assertIn(fence.strip("`"), self.text)

    # ------------------------------------------------------------- arm A

    def test_a_runnable_block_naming_a_dead_verb_is_caught(self):
        self.append("\n```bash\nscripts/agentware notaverb --help\n```\n")
        self.assertIn("A", self.arms())

    def test_a_runnable_block_naming_a_missing_checker_is_caught(self):
        self.append("\n```bash\npython3 tests/_no_such_check.py\n```\n")
        problems = self.problems()
        self.assertTrue(any(p.startswith("A") for p in problems), problems)

    def test_a_runnable_block_naming_an_unresolvable_test_module_is_caught(self):
        self.append("\n```bash\npython3 -m unittest tests.unit.ops.test_nope\n```\n")
        self.assertIn("A", self.arms())

    def test_the_scaffolded_example_verb_is_not_reported_as_dead(self):
        """`greet` is created BY the document, so arm A must resolve it from the
        document's own `scaffold new --verb` line rather than from discovery."""
        self.assertIn("greet", wc.created_verbs(self.text))
        self.assertNotIn("greet", wc.live_verbs(REPO_ROOT))

    # ------------------------------------------------------------- arm B

    def test_a_troubleshooting_row_naming_a_missing_gate_is_caught(self):
        self.rewrite("tests/gates/test_suite_delta.py",
                     "tests/gates/test_suite_delta_gone.py", count=-1)
        problems = self.problems()
        self.assertTrue(any(p.startswith("B") and "does not exist" in p
                            for p in problems), problems)

    def test_deleting_a_named_gate_from_the_tree_reddens_the_row(self):
        os.remove(os.path.join(self.root, "tests/gates/test_parser_golden.py"))
        problems = self.problems()
        self.assertTrue(any("test_parser_golden.py" in p for p in problems),
                        problems)

    def test_a_troubleshooting_row_naming_a_dead_verb_is_caught(self):
        self.rewrite("| `scripts/agentware steering lint` |",
                     "| `scripts/agentware steeringlint` |")
        self.assertIn("B", self.arms())

    # ------------------------------------------------------------- arm C

    def test_dropping_a_required_gates_row_is_caught(self):
        path = os.path.join(self.root, WALKTHROUGH)
        with open(path, encoding="utf-8") as handle:
            kept = [line for line in handle
                    if "tests/gates/test_portability_gate.py" not in line]
        with open(path, "w", encoding="utf-8") as handle:
            handle.writelines(kept)
        problems = self.problems()
        self.assertTrue(any(p.startswith("C") and "portability" in p
                            for p in problems), problems)

    def test_a_row_with_an_empty_fix_column_is_caught(self):
        self.rewrite("| `python3 tests/_manifest_gen.py --write` — **never** "
                     "hand-edit `catalog/modules.json` |", "| — |")
        problems = self.problems()
        self.assertTrue(any(p.startswith("C") and "fix" in p
                            for p in problems), problems)

    def test_every_required_gate_really_exists_in_the_live_tree(self):
        """The roster is only a coverage claim if its own paths are real."""
        for rel in wc.REQUIRED_GATES:
            self.assertTrue(os.path.exists(os.path.join(REPO_ROOT, rel)), rel)

    # ------------------------------------------------------------- arm D

    def test_a_ghost_command_in_the_walkthrough_is_caught(self):
        self.append("\n```bash\npython3 tests/_ghost_checker.py\n```\n")
        self.assertIn("D", self.arms())

    def test_a_ghost_command_in_the_architecture_doc_is_caught(self):
        """The exact defect this arm was written for: `architecture.md` told
        contributors to run two checkers this tree has never had."""
        self.append("\n```bash\npython3 tests/_ast_forward_edges.py x\n```\n",
                    rel=ARCHITECTURE)
        problems = self.problems()
        self.assertTrue(any(p.startswith("D") and ARCHITECTURE in p
                            for p in problems), problems)

    # ------------------------------------------------------------- arm E

    def test_an_undeclared_norun_reason_is_caught(self):
        self.rewrite("```bash norun:the-suite-cannot-run-itself",
                     "```bash norun:because-i-said-so")
        self.assertIn("E", self.arms())

    def test_removing_the_mutation_proof_is_caught(self):
        # count=-1: the first occurrence is the conventions TABLE, not the
        # fence — a count of 1 mutates the legend and leaves the proof standing.
        self.rewrite("```bash expect-fail", "```text", count=-1)
        problems = self.problems()
        self.assertTrue(any(p.startswith("E") and "expect-fail" in p
                            for p in problems), problems)

    def test_a_block_targeting_a_file_that_does_not_exist_is_caught(self):
        self.append("\n```diff path=scripts/agentware_lib/ops/nothing.py\n"
                    "-a\n+b\n```\n")
        self.assertIn("E", self.arms())

    def test_an_unrecognised_bash_info_string_is_caught(self):
        self.append("\n```bash maybe-run\nls\n```\n")
        self.assertIn("E", self.arms())

    def test_a_missing_document_is_reported_rather_than_passing_silently(self):
        os.remove(os.path.join(self.root, WALKTHROUGH))
        problems = self.problems()
        self.assertTrue(any("missing document" in p for p in problems), problems)


class BlockParsingTest(unittest.TestCase):
    """The extractor's own contract — the arms are only as good as it is."""

    def test_classify_reads_every_declared_info_string(self):
        self.assertEqual(wc.classify("bash")[0], "run")
        self.assertEqual(wc.classify("bash expect-fail")[0], "run-fail")
        self.assertEqual(wc.classify("bash norun:x"), ("norun", "x"))
        self.assertEqual(wc.classify("python path=a/b.py"), ("write", "a/b.py"))
        self.assertEqual(wc.classify("diff path=a/b.py"), ("edit", "a/b.py"))
        self.assertEqual(wc.classify("text")[0], "prose")

    def test_blocks_pair_fences_and_report_the_opening_line(self):
        found = wc.blocks("intro\n```bash\nls\n```\ntail\n")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], "bash")
        self.assertEqual(found[0][1], ["ls"])
        self.assertEqual(found[0][2], 2)

    def test_command_lines_drop_comments_and_blanks(self):
        self.assertEqual(wc.command_lines(["# note", "", " ls -l "]), ["ls -l"])

    def test_the_walkthrough_has_at_least_one_block_of_every_kind(self):
        kinds = set(wc.classify(info)[0]
                    for info, _b, _l in wc.blocks(wc.read(REPO_ROOT, WALKTHROUGH)))
        for kind in ("run", "run-fail", "norun", "write", "edit"):
            self.assertIn(kind, kinds)


if __name__ == "__main__":
    unittest.main()
