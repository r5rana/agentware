"""The MODULE MANIFEST gate (plan task 67) — modularity made permanent.

`catalog/modules.json` is the tree's discovery surface: it answers "which module
owns this?" without reading any module. Task 67's job is to make that answer
TRUE BY CONSTRUCTION and TREE-WIDE, and this file is where each half is asserted.

Four claims, each mapping to one of the task's acceptance criteria:

  1. GENERATED, NOT MAINTAINED — the committed file is byte-identical to a fresh
     AST regeneration, and a second regeneration produces no diff. Before task 67
     there was no generator in this tree at all: the file was hand-edited, and it
     had drifted to 193 entries against 199 modules on disk with 577 stale
     fields. A manifest nobody can regenerate is a document ABOUT the tree; the
     point is for it to BE the tree.
  2. CLAIMED — every corpus member on disk has an entry, or matches a DECLARED
     exclusion carrying a reason. Adding a file to the tree cannot silently leave
     it unowned, and adding a whole directory cannot silently leave it
     unmeasured, because the enumerator would have to be taught to skip it in a
     table a reviewer reads (`tests/_corpus.py`).
  3. CAPPED — every entry carries its layer's line cap, the manifest publishes
     the per-FUNCTION caps no file cap can express, and the caps it publishes are
     the SAME objects the ceiling gate measures against. A manifest that
     advertised one cap while the gate enforced another would be worse than no
     manifest.
  4. REFUTED — the completeness check is proven to BITE on an unclaimed file and
     on a phantom entry, using synthetic paths rather than by writing a file into
     the checkout under test.

`tests/gates/test_module_ceilings.py` owns the ceiling MEASUREMENT (is the tree
within its caps, is the residual declared and shrink-only). This file owns the
MANIFEST (is every file claimed, is the manifest regenerable, do the two agree
about the caps). They share `tests/_corpus.py`, which is what stops them
disagreeing about what the tree contains.

Stdlib only, deterministic, hermetic (reads the tree with `ast` and `os.walk`;
no imports of the code under test, no subprocess, no network).
"""

from tests._paths import REPO_ROOT, TESTS_DIR

import json
import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests import _ceiling_gate as cg
    from tests import _corpus
    from tests import _manifest_gen as mg
except ImportError:  # allow a bare-name run from inside tests/
    sys.path.insert(0, TESTS_DIR)
    import _ceiling_gate as cg
    import _corpus
    import _manifest_gen as mg

CATALOG_REL = "catalog/modules.json"


def _committed_text():
    with open(os.path.join(REPO_ROOT, CATALOG_REL), encoding="utf-8") as f:
        return f.read()


class ManifestIsGeneratedTest(unittest.TestCase):
    """Claim 1 — the committed file IS the regeneration, and regeneration is a
    fixed point."""

    def test_the_committed_manifest_is_byte_identical_to_a_regeneration(self):
        fresh = mg.serialize(mg.build_manifest())
        self.assertEqual(
            fresh, _committed_text(),
            "catalog/modules.json is stale or hand-edited — regenerate it with "
            "`python3 tests/_manifest_gen.py --write`. It is GENERATED: every "
            "field is derived from the AST or from tests/_corpus.py, so an edit "
            "here is a change that belongs in the module docstring or the "
            "corpus table instead.")

    def test_a_second_generation_produces_no_diff(self):
        # The acceptance criterion states idempotence separately from freshness,
        # and it is a different failure: a generator that sorts non-deterministically,
        # or that reads a value it also writes, is fresh on the first run and
        # dirty on the second. Building twice catches that; comparing to the
        # committed file alone does not.
        self.assertEqual(mg.serialize(mg.build_manifest()),
                         mg.serialize(mg.build_manifest()))

    def test_the_manifest_declares_its_own_generator_and_regen_command(self):
        m = json.loads(_committed_text())
        self.assertEqual(m["generator"], "tests/_manifest_gen.py")
        self.assertEqual(m["regen"], "python3 tests/_manifest_gen.py --write")
        self.assertEqual(m["provenance"], "generated-from-ast")
        self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, m["generator"])),
                        "the manifest names a generator that does not exist — "
                        "which is exactly the state task 67 found the tree in")

    def test_the_static_tables_are_not_derived_from_the_filesystem(self):
        # `planned_layout` and `naming_disambiguation` name modules that may not
        # exist yet, so R-DATA-01's filesystem validation applies to `modules`
        # and `files` and must NEVER be applied to these. Pinning where they live
        # is what keeps that distinction reviewable.
        static = mg.load_static()
        m = json.loads(_committed_text())
        self.assertEqual(m["naming_disambiguation"],
                         static["naming_disambiguation"])
        self.assertEqual(m["planned_layout"], static["planned_layout"])


class EveryFileOnDiskIsClaimedTest(unittest.TestCase):
    """Claim 2 — completeness, in both directions."""

    def test_every_corpus_member_has_a_manifest_entry(self):
        missing = mg.unclaimed(_corpus.corpus_files(REPO_ROOT))
        self.assertEqual(
            missing, [],
            "file(s) on disk with no manifest entry — regenerate with "
            "`python3 tests/_manifest_gen.py --write`, or, if the file is not "
            "code under the ceilings, declare an exclusion with a reason in "
            "tests/_corpus.py: %r" % (missing,))

    def test_no_manifest_entry_names_a_file_that_is_gone(self):
        ghosts = mg.phantom(root=REPO_ROOT)
        self.assertEqual(
            ghosts, [],
            "manifest entry(s) for path(s) that no longer exist: %r" % (ghosts,))

    def test_the_wide_corpus_actually_reaches_all_four_widened_subsystems(self):
        # The widening is the whole point of task 67, and a corpus enumerator
        # that quietly matched nothing outside the lib would satisfy every other
        # assertion in this file. So the four subsystems that were previously
        # unmeasured are named, and each must be non-empty.
        layers = set(e["layer"] for e in json.loads(_committed_text())["files"])
        for expected in ("lib", "tests", "webui", "loop", "hooks", "scripts",
                         "orchestrator", "entrypoint"):
            self.assertIn(expected, layers,
                          "the manifest claims no %s file — the corpus rule "
                          "matched nothing, so that subsystem is unmeasured"
                          % expected)

    def test_the_orchestrator_and_the_facade_are_claimed_by_exact_path(self):
        claims = mg.claimed_paths(json.loads(_committed_text()))
        self.assertIn("agentware.sh", claims)
        self.assertIn("scripts/agentware", claims)

    def test_every_declared_exclusion_carries_a_reason(self):
        for row in json.loads(_committed_text())["excluded"]:
            self.assertTrue(row["prefix"])
            self.assertGreater(
                len(row["reason"]), 20,
                "exclusion %r needs a real reason: an exclusion with no "
                "justification is a hole in the completeness check, not a "
                "scoping decision" % row["prefix"])


class EveryEntryCarriesItsLayerAndCapTest(unittest.TestCase):
    """Claim 3 — layer and cap, agreeing with the gate that enforces them."""

    def setUp(self):
        self.manifest = json.loads(_committed_text())

    def test_every_file_entry_carries_a_layer_and_a_line_cap(self):
        for entry in self.manifest["files"]:
            self.assertIn(entry["layer"], _corpus.LAYER_CAPS,
                          "%s has an unknown layer %r" % (entry["path"],
                                                          entry["layer"]))
            self.assertEqual(entry["line_cap"],
                             _corpus.cap_of(entry["layer"]),
                             "%s advertises a cap its layer does not have"
                             % entry["path"])

    def test_the_manifest_publishes_the_per_function_caps(self):
        # The cap no FILE cap can express, and the reason the plan states it
        # separately: configcmd.py was 799 lines with TWO defs because one
        # function was 622 of them.
        caps = self.manifest["caps"]
        self.assertEqual(caps["function_lines"], cg.FUNC_LINE_CAP)
        self.assertEqual(caps["function_branches"], cg.FUNC_BRANCH_CAP)

    def test_the_published_caps_are_the_caps_the_gate_enforces(self):
        # One table, two readers. If these could differ, the manifest would be
        # advertising a contract nobody checks.
        self.assertEqual(self.manifest["caps"]["file_lines_by_layer"],
                         _corpus.LAYER_CAPS)
        self.assertEqual(self.manifest["caps"]["file_lines_by_layer"]["lib"],
                         cg.LIB_FILE_CAP)

    def test_every_file_is_within_its_cap_or_carries_a_declared_debt_row(self):
        problems, clean = cg.reconcile()
        self.assertTrue(
            clean,
            "a corpus file is over its cap with no debt row, or a declared row "
            "is stale — the manifest publishes a cap the tree does not meet: %r"
            % (problems,))

    def test_the_plan_ceilings_are_the_ones_in_the_table(self):
        # Pinned by value against the plan's "Ceilings" section, so a later
        # change to a cap is a deliberate edit somebody defends in review rather
        # than a number that drifted to fit the tree.
        self.assertEqual(_corpus.LAYER_CAPS["entrypoint"], 120)
        self.assertEqual(_corpus.LAYER_CAPS["lib"], 600)
        self.assertEqual(_corpus.LAYER_CAPS["tests"], 600)
        for procedural in ("orchestrator", "loop", "hooks", "scripts"):
            self.assertEqual(_corpus.LAYER_CAPS[procedural], 250)


class ModulesArrayStillDescribesTheLibTest(unittest.TestCase):
    """The rich array must not be diluted by the widening.

    Widening a manifest is the easy way to lose what it was for: if `files`
    replaced `modules`, the tree would gain a file list and lose the only
    machine-readable answer to "what does this module own, and what does it
    deliberately NOT own?".
    """

    def setUp(self):
        self.manifest = json.loads(_committed_text())

    def test_every_lib_module_has_an_owns_and_a_not_owns(self):
        thin = [m["name"] for m in self.manifest["modules"]
                if not m["owns"] or not m["not_owns"]]
        self.assertEqual(
            thin, [],
            "module(s) whose docstring is missing an `Owns:` or `Does NOT own:` "
            "label — the manifest parses those from the module itself, so a "
            "blank field here is a blank docstring there: %r" % (thin,))

    def test_the_owns_text_is_the_whole_label_not_just_its_first_line(self):
        # The regression this catches is specific and was live: a first-line-only
        # parser truncated 190 of 199 `owns` values at the first newline, which
        # reads as a populated field and is in fact two thirds missing.
        wrapped = [m for m in self.manifest["modules"] if len(m["owns"]) > 200]
        self.assertGreater(
            len(wrapped), 20,
            "no module carries a multi-line `Owns:` value, which means the "
            "docstring parser is truncating at the first line again")

    def test_every_module_path_is_also_claimed_by_the_wide_array(self):
        claims = mg.claimed_paths(self.manifest)
        for m in self.manifest["modules"]:
            self.assertIn(m["path"], claims,
                          "%s is in `modules` but not in `files` — the two "
                          "arrays disagree about what is in the tree" % m["path"])

    def test_fan_in_is_the_inversion_of_fan_out(self):
        by_name = dict((m["name"], m) for m in self.manifest["modules"])
        for m in self.manifest["modules"]:
            for target in m["depends_on"]:
                self.assertIn(
                    m["name"], by_name[target]["depended_on_by"],
                    "%s depends on %s but is missing from its fan-in list"
                    % (m["name"], target))


class TheCompletenessCheckBitesTest(unittest.TestCase):
    """Claim 4 — refutation. A check that has never failed has never been tested.

    Every case here drives the PURE functions with synthetic inputs rather than
    writing a file into the checkout: a gate that has to dirty the tree to prove
    itself will eventually be run somewhere it must not dirty.
    """

    def test_a_new_file_with_no_manifest_entry_is_reported(self):
        invented = "scripts/agentware_lib/ops/a_module_nobody_declared.py"
        self.assertEqual(
            mg.unclaimed(_corpus.corpus_files(REPO_ROOT) + [invented]),
            [invented],
            "an unclaimed file must be named by the completeness check — this "
            "is the criterion 'a file added without an entry fails the check'")

    def test_an_unclaimed_file_in_each_widened_subsystem_is_reported(self):
        # One shape per widened subsystem, because the corpus rules are
        # per-prefix and a rule that stopped matching would make its whole
        # subsystem silently unclaimable rather than loudly unclaimed.
        for invented in ("scripts/hooks/undeclared-hook.sh",
                         "scripts/loop/undeclared.sh",
                         "tests/gates/test_undeclared.py",
                         "webui/src/panels/Undeclared.tsx",
                         "scripts/undeclared_tool.py"):
            self.assertIn(invented, mg.unclaimed([invented]),
                          "%s would be added to the tree unclaimed" % invented)

    def test_an_excluded_path_is_claimed_rather_than_reported(self):
        # The other side of the same rule: a declared exclusion must SATISFY the
        # check, or every fixture file would read as an unclaimed source file and
        # the gate would be permanently red — which is how a gate becomes
        # decoration.
        self.assertEqual(mg.unclaimed(["tests/fixtures/whatever.py",
                                       "webui/dist/assets/index.js"]), [])

    def test_the_claim_is_read_from_the_committed_file_never_rebuilt(self):
        """The vacuity this gate nearly shipped with, pinned so it cannot return.

        `claimed_paths` originally defaulted to `build_manifest()`. That is
        vacuous by construction: the builder enumerates the same disk the check
        walks, so a file added WITHOUT regenerating the catalog is claimed the
        instant the check runs, and the acceptance criterion "a file added
        without an entry fails the check" becomes untestable while every
        assertion stays green. It was caught by adding a real module to a scratch
        copy of the tree and watching the gate pass.

        The refutation here is on a real filesystem — a file that EXISTS on disk
        and is ABSENT from the manifest it is checked against — but on a
        synthetic three-file tree, so it costs milliseconds rather than a copy of
        the checkout.
        """
        tmp = tempfile.mkdtemp(prefix="aw-manifest-claim-")
        self.addCleanup(shutil.rmtree, tmp, True)
        declared = "scripts/agentware_lib/ops/declared.py"
        snuck_in = "scripts/agentware_lib/ops/snuck_in.py"
        for rel in (declared, snuck_in):
            path = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write('"""Owns: nothing.\n\nDoes NOT own: anything.\n"""\n')
        catalog = {"files": [{"path": declared, "layer": "lib",
                              "line_cap": 600}]}

        on_disk = _corpus.corpus_files(tmp)
        self.assertIn(snuck_in, on_disk, "the scratch tree was not built")
        self.assertEqual(
            mg.unclaimed(on_disk, manifest=catalog), [snuck_in],
            "a file present on disk and absent from the manifest it is checked "
            "against must be reported — if this returns [], the check is "
            "reading a manifest it rebuilt from that same disk")

    def test_a_phantom_entry_is_reported(self):
        ghost = {"path": "scripts/agentware_lib/core/deleted_yesterday.py",
                 "layer": "lib", "line_cap": 600}
        manifest = dict(json.loads(_committed_text()))
        manifest["files"] = manifest["files"] + [ghost]
        self.assertIn(ghost["path"], mg.phantom(manifest=manifest,
                                                root=REPO_ROOT))

    def test_the_corpus_enumerator_is_not_vacuously_empty(self):
        # The failure mode that passes every other test in this file: an
        # enumerator rooted at the wrong directory returns [], and "every file is
        # claimed" is then trivially true of nothing.
        members = _corpus.corpus_files(REPO_ROOT)
        self.assertGreater(len(members), 400,
                           "the corpus enumerator found %d files, which is far "
                           "too few — it is probably rooted at the wrong "
                           "directory" % len(members))


if __name__ == "__main__":
    unittest.main()
