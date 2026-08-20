"""The contributor-architecture doc gate (feature 260813-modular-core-adoption, task 74).

Two halves, the shape every ratchet in this corpus carries.

The FIRST half proves the checker BITES. A documentation gate is the easiest
kind of test to write green and leave useless: it reads a file the author just
wrote, so it agrees with itself by construction. So every arm is mutated in a
scratch copy of the REAL tree — the positive control is the shipped content, and
each mutation is one edit away from it — and the arm must name the offender.

The SECOND half runs the checker over this repository, which is the acceptance
criterion: the documented module map matches `catalog/modules.json` exactly, and
`python3 tests/_doc_sync.py --write` produces no diff.
"""

from tests._paths import TESTS_DIR

import os
import re
import shutil
import sys
import unittest

HERE = TESTS_DIR
REPO_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _doc_extensibility_check as dec  # noqa: E402
import _doc_sync as docsync  # noqa: E402


#: Everything the checker reads. Copied into the scratch tree so the positive
#: control is the shipped content rather than a fixture that can agree with a
#: broken checker.
COPIED = ([docsync.CATALOG_REL, docsync.ARCH_REL, docsync.README_REL]
          + [owner for _n, _a, owner, _l in dec.EXTENSION_POINTS])


def read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as handle:
        return handle.read()


def write(root, rel, body):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)


def edit(root, rel, old, new, count=-1):
    """Replace in a scratch file. `count=-1` (the default) replaces every
    occurrence, which is what a rename mutation means."""
    body = read(root, rel)
    assert old in body, "fixture drift: %r not in %s" % (old, rel)
    write(root, rel, body.replace(old, new, count))


class ScratchTreeTest(unittest.TestCase):
    """A copy of every file the checker reads, green before the mutation."""

    def setUp(self):
        import tempfile
        self.root = tempfile.mkdtemp(prefix="aw-docarch-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for rel in COPIED:
            dst = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(REPO_ROOT, rel), dst)
        self.doc = os.path.join(self.root, docsync.ARCH_REL)

    def errors(self):
        return dec.check_doc(self.doc, self.root)

    def assertNoErrors(self):
        self.assertEqual(self.errors(), [])

    def assertNamed(self, arm, needle):
        """Some error from `arm` names `needle`, and none did before."""
        hits = [e for e in self.errors()
                if e.startswith(arm + ":") and needle in e]
        self.assertTrue(hits, "arm %s named nothing containing %r; got %r"
                        % (arm, needle, self.errors()))


class PositiveControlTest(ScratchTreeTest):

    def test_the_unmutated_copy_of_the_real_tree_passes(self):
        """Without this, every mutation below could be failing for the wrong
        reason — a missing file, a bad copy, a path the checker cannot read."""
        self.assertNoErrors()


class RecipeArmTest(ScratchTreeTest):

    def test_a_deleted_recipe_anchor_is_named(self):
        edit(self.root, docsync.ARCH_REL, "DREAM_STEP_FUNCS", "the dream registry")
        self.assertNamed("A", "DREAM_STEP_FUNCS")

    def test_a_recipe_that_names_no_test_is_named(self):
        edit(self.root, docsync.ARCH_REL, "*Test:*", "*Covered by:*")
        hits = [e for e in self.errors() if e.startswith("A:") and "Test:" in e]
        self.assertTrue(hits, self.errors())

    def test_a_missing_recipes_section_is_named_once(self):
        edit(self.root, docsync.ARCH_REL, dec.RECIPES_HEADING,
             "### (v) Recipes, elsewhere")
        self.assertNamed("A", "recipes section")


class ChokepointArmTest(ScratchTreeTest):

    def test_an_unlisted_chokepoint_is_named(self):
        """The parity manifest is a shared, append-only registry; dropping its
        row is exactly how a contributor learns about it by colliding."""
        body = read(self.root, docsync.ARCH_REL)
        kept = [line for line in body.split("\n")
                if "tests/fixtures/parity-manifest.json" not in line
                or not line.startswith("| Parity manifest")]
        write(self.root, docsync.ARCH_REL, "\n".join(kept))
        self.assertNamed("B", "parity-manifest.json")

    def test_a_chokepoint_section_with_no_mitigation_is_named(self):
        section = dec._section(read(self.root, docsync.ARCH_REL),
                               dec.CHOKEPOINTS_HEADING)
        stripped = section
        for word in dec.MITIGATION_WORDS:
            stripped = re.sub(word, "handled", stripped, flags=re.I)
        edit(self.root, docsync.ARCH_REL, section, stripped)
        self.assertNamed("B", "no mitigation")


class ModuleTableArmTest(ScratchTreeTest):

    def test_a_manifest_module_with_no_doc_row_is_named(self):
        edit(self.root, docsync.ARCH_REL,
             "| `agentware_lib.core.paths` | `tests/unit/core/test_paths.py` ", "| `x` | `y` ")
        self.assertNamed("C", "agentware_lib.core.paths")

    def test_a_doc_row_naming_no_module_is_named(self):
        edit(self.root, docsync.ARCH_REL,
             "| `agentware_lib.core.paths` |",
             "| `agentware_lib.core.ghost` |\n| `agentware_lib.core.paths` |")
        self.assertNamed("C", "agentware_lib.core.ghost")


class ManagedBlockArmTest(ScratchTreeTest):

    def test_a_hand_edited_managed_block_is_named(self):
        """The fan-in column is a fact, not prose: nudging it by one is the
        smallest possible hand edit and it must still be caught.

        The count is READ from the scratch tree rather than written here (task
        90). Pinning the literal made this fixture a second, hand-maintained copy
        of a GENERATED number: adding any module that imports `core.dates` moved
        the real row and the mutation stopped applying, so the arm failed with
        `fixture drift` — a gate reporting its own fixture rather than the thing
        it guards. Deriving it keeps the mutation exactly one off-by-one.
        """
        row = re.search(
            r"\| `agentware_lib\.core\.dates` \| `[^`]+` \| (\d+) \|",
            read(self.root, docsync.ARCH_REL))
        self.assertIsNotNone(row, "the module-map row for core.dates is gone")
        edit(self.root, docsync.ARCH_REL, row.group(0),
             row.group(0).replace("| %s |" % row.group(1),
                                  "| %d |" % (int(row.group(1)) + 1)))
        self.assertNamed("D", "module-map")

    def test_a_hand_edited_ceiling_table_is_named(self):
        edit(self.root, docsync.ARCH_REL, "| `lib` file | 600 lines |",
             "| `lib` file | 900 lines |")
        self.assertNamed("D", "ceilings")

    def test_a_hand_edited_layer_table_is_named(self):
        edit(self.root, docsync.ARCH_REL, "| `core` | _nothing_ |",
             "| `core` | `ops` |")
        self.assertNamed("D", "layer-direction")

    def test_a_removed_marker_is_a_loud_failure_not_a_pass(self):
        edit(self.root, docsync.ARCH_REL, docsync.END % "ceilings", "")
        with self.assertRaises(SystemExit):
            self.errors()


class RouterArmTest(ScratchTreeTest):

    def test_a_module_with_no_router_row_is_named(self):
        edit(self.root, docsync.README_REL, "| `agentware_lib.core.paths` | core |",
             "| `agentware_lib.core.pathz` | core |")
        self.assertNamed("E", "agentware_lib.core.paths")

    def test_a_router_row_naming_no_module_is_named(self):
        edit(self.root, docsync.README_REL, "| `agentware_lib.core.paths` | core |",
             "| `agentware_lib.core.ghost` | core | x | `y` |\n"
             "| `agentware_lib.core.paths` | core |")
        self.assertNamed("E", "agentware_lib.core.ghost")

    def test_a_row_naming_a_real_PACKAGE_is_allowed(self):
        """`agentware_lib.core.config` is a package the manifest publishes
        modules inside; the router documents the seam as well as its leaves, and
        that is not drift."""
        self.assertIn("agentware_lib.core.config",
                      docsync.router_rows(read(self.root, docsync.README_REL)))
        self.assertNoErrors()


class AnchorResolutionArmTest(ScratchTreeTest):
    """Arm F: the arm that stops the document rotting (R-FAIL-08)."""

    def test_an_anchor_whose_symbol_was_renamed_is_named(self):
        edit(self.root, "scripts/agentware_lib/features/dreamsteps/registry.py",
             "DREAM_STEP_FUNCS", "DREAM_STEPS")
        self.assertNamed("F", "DREAM_STEP_FUNCS")

    def test_an_anchor_whose_owner_file_was_moved_is_named(self):
        os.remove(os.path.join(self.root,
                               "scripts/agentware_lib/features/mcp.py"))
        self.assertNamed("F", "does not exist")

    def test_the_next_rule_id_probes_its_PREDECESSOR(self):
        """`O14` does not exist and must not be required to; what must exist is
        `O13`, the id the next rule follows. A probe for the new id would be
        unsatisfiable and a probe for nothing would be vacuous."""
        edit(self.root, "scripts/agentware_lib/ops/orglint.py", "O13", "O12")
        self.assertNamed("F", "O14")


class SyncWriterTest(ScratchTreeTest):
    """`_doc_sync` is the writer the whole gate leans on; prove it repairs."""

    def test_a_missing_router_row_is_added_into_its_own_layer_run(self):
        edit(self.root, docsync.README_REL,
             "| `agentware_lib.core.paths` | core |", "")
        added, text = docsync.sync_router(self.root, write=True)
        self.assertEqual(added, ["agentware_lib.core.paths"])
        rows = [layer for _name, layer in docsync.ROUTER_ROW_RE.findall(text)]
        core_at = [i for i, layer in enumerate(rows) if layer == "core"]
        added_at = [i for i, (name, _l)
                    in enumerate(docsync.ROUTER_ROW_RE.findall(text))
                    if name == "agentware_lib.core.paths"]
        self.assertEqual(len(added_at), 1)
        self.assertIn(added_at[0], core_at)

    def test_repair_is_idempotent_and_leaves_the_tree_green(self):
        edit(self.root, docsync.README_REL,
             "| `agentware_lib.core.paths` | core |", "")
        edit(self.root, docsync.ARCH_REL, "| `lib` file | 600 lines |",
             "| `lib` file | 900 lines |")
        docsync.sync_router(self.root, write=True)
        docsync.sync_architecture(self.root, write=True)
        self.assertEqual(docsync.drift(self.root), [])
        before = read(self.root, docsync.README_REL)
        docsync.sync_router(self.root, write=True)
        docsync.sync_architecture(self.root, write=True)
        self.assertEqual(read(self.root, docsync.README_REL), before)

    def test_a_cell_containing_a_pipe_cannot_break_the_table(self):
        self.assertEqual(docsync.cell("a | b\n c"), "a \\| b c")


class LiveRepoTest(unittest.TestCase):
    """The acceptance criterion, run against this repository."""

    def test_the_architecture_doc_passes_every_arm(self):
        self.assertEqual(
            dec.check_doc(os.path.join(REPO_ROOT, docsync.ARCH_REL)), [])

    def test_regenerating_the_documented_maps_produces_no_diff(self):
        self.assertEqual(docsync.drift(REPO_ROOT), [])

    def test_every_manifest_module_is_documented_in_both_maps(self):
        manifest = docsync.load_manifest(REPO_ROOT)
        published = set(m["name"] for m in manifest["modules"])
        router = set(docsync.router_rows(read(REPO_ROOT, docsync.README_REL)))
        doc = set(dec.ROW_RE.findall(read(REPO_ROOT, docsync.ARCH_REL)))
        self.assertEqual(published - router, set())
        self.assertEqual(published - doc, set())
        self.assertEqual(doc - published, set())

    def test_the_doc_row_debt_ledger_is_gone(self):
        """Task 73 declared 82 undocumented modules as a shrink-only ratchet
        owned by task 74. Emptying it is this task's contract, and a zero-row
        ledger left on disk is an invitation to re-add rows to it."""
        self.assertFalse(os.path.exists(
            os.path.join(REPO_ROOT, "tests/fixtures/doc-row-debt.json")))

    def test_scaffold_check_still_finds_a_router_row_for_every_module(self):
        sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
        import agentware_lib.ops.scaffoldcheck as sc
        self.assertEqual(sc.check_docs(REPO_ROOT,
                                       docsync.load_manifest(REPO_ROOT)), [])


if __name__ == "__main__":
    unittest.main()
