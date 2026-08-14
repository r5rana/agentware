"""Task-15: the KB target guard and the retired repoint module, proven by breaking it.

`tests/_kbtarget_gate.py` runs seven checks over whichever tree carries the
modular library. This module asserts they PASS there — and then, for each one,
constructs the cheapest mutation an author could make by accident and asserts it
REDDENS. A gate only ever observed passing is not evidence.

The mutations are built as synthetic trees rather than by editing the real one,
so a failure here can never leave the working tree modified.

Stdlib `unittest` only. Never touches the real knowledge base or config.
"""

import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import _kbtarget_gate as gate  # noqa: E402
import _parity_manifest as pm  # noqa: E402
from _parity_schema import run_probe  # noqa: E402


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


class ResolvedTreeTest(unittest.TestCase):
    """The gate must find a tree, and say so — never pass by finding nothing."""

    def test_kbtarget_a_tree_carrying_the_lib_resolves(self):
        tree, problems = gate.resolve_tree()
        self.assertEqual(problems, [])
        self.assertTrue(os.path.isdir(os.path.join(tree, gate.LIB_REL)), tree)

    def test_kbtarget_no_tree_is_a_FAILURE_not_a_silent_pass(self):
        empty = tempfile.mkdtemp(prefix="aw-notree-")
        self.addCleanup(shutil.rmtree, empty, True)
        saved_repo, saved_dest = gate.REPO, gate._stage_donor.resolve_dest
        saved_env = os.environ.pop("AGENTWARE_LIB_TREE", None)
        gate.REPO = empty
        gate._stage_donor.resolve_dest = lambda *a, **k: empty
        try:
            tree, problems = gate.resolve_tree()
        finally:
            gate.REPO, gate._stage_donor.resolve_dest = saved_repo, saved_dest
            if saved_env is not None:
                os.environ["AGENTWARE_LIB_TREE"] = saved_env
        self.assertIsNone(tree)
        self.assertTrue(problems)
        self.assertIn("no tree carries", problems[0])


class GatePassesTest(unittest.TestCase):
    """The real assertion: every check is clean over the resolved tree."""

    @classmethod
    def setUpClass(cls):
        cls.tree, problems = gate.resolve_tree()
        if problems:
            raise unittest.SkipTest(problems[0])

    def test_kbtarget_whole_gate_is_clean(self):
        self.assertEqual(gate.check(self.tree), [])

    def test_kbtarget_gate_runs_as_a_command_and_exits_zero(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = gate.main(["--tree", self.tree, "--json"])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertTrue(json.loads(buf.getvalue())["ok"])

    def test_kbtarget_guard_lives_in_the_core_layout_module(self):
        path = os.path.join(self.tree, gate.LIB_REL, "core", "kblayout.py")
        self.assertTrue(os.path.isfile(path), path)
        with open(path, encoding="utf-8") as fh:
            self.assertIn("def %s(" % gate.GUARD_NAME, fh.read())

    def test_kbtarget_every_repoint_path_is_named_and_wired(self):
        # Both pointer-moving paths, not just init: a guard on one of two doors
        # is a guard on neither.
        labels = {label for _stem, _handler, label in gate.REPOINT_STEMS}
        self.assertIn("agentware init --knowledge-dir", labels)
        self.assertIn("agentware config --set-knowledge-dir", labels)
        self.assertEqual(gate.check_wiring(self.tree), [])


class SyntheticTree(object):
    """A minimal tree carrying only what a given check reads."""

    def __init__(self, case):
        self.root = tempfile.mkdtemp(prefix="aw-kbsynth-%s-" % case)

    def lib(self, *parts):
        return os.path.join(self.root, gate.LIB_REL, *parts)

    def package(self, kblayout_body):
        write(os.path.join(self.root, "scripts", "agentware_lib", "__init__.py"), "")
        write(self.lib("core", "__init__.py"), "")
        write(self.lib("core", "kblayout.py"), kblayout_body)
        return self


_STUB_ALWAYS_OK = '''"""stub"""
SENTINEL_REL = ".initialized"
MAIN_REL = "MAIN.md"
INDEX_REL = "index.json"


def kb_pointable(path):
    return True, "ok"
'''


class MutationTest(unittest.TestCase):
    """Each check, broken the cheapest way, must name what broke."""

    def synth(self, case):
        tree = SyntheticTree(case)
        self.addCleanup(shutil.rmtree, tree.root, True)
        return tree

    def test_kbtarget_a_guard_that_accepts_everything_reddens(self):
        tree = self.synth("stub").package(_STUB_ALWAYS_OK)
        problems = gate.check_guard(tree.root)
        self.assertTrue(problems, "a rubber-stamp guard passed the gate")
        joined = "\n".join(problems)
        for case in ("no_sentinel", "no_entry_document", "corrupt_index"):
            self.assertIn(case, joined)

    def test_kbtarget_a_guard_requiring_the_derived_index_reddens(self):
        # The exemption is the whole point: a fresh clone has no index.json.
        body = _STUB_ALWAYS_OK.replace(
            "    return True, \"ok\"\n",
            "    import os\n"
            "    if not os.path.isfile(os.path.join(path, INDEX_REL)):\n"
            "        return False, 'no index.json'\n"
            "    return True, 'ok'\n")
        tree = self.synth("needs-index").package(body)
        problems = gate.check_guard(tree.root)
        self.assertTrue(any("no_derived_index" in p for p in problems),
                        "refusing a fresh checkout passed the gate: %r" % problems)

    def test_kbtarget_a_guard_that_mutates_its_target_reddens(self):
        body = _STUB_ALWAYS_OK.replace(
            "    return True, \"ok\"\n",
            "    import os\n"
            "    if os.path.isdir(path):\n"
            "        open(os.path.join(path, 'touched'), 'w').close()\n"
            "    return True, 'ok'\n")
        tree = self.synth("mutating").package(body)
        problems = gate.check_guard(tree.root)
        self.assertTrue(any("MUTATED" in p for p in problems), problems)

    def test_kbtarget_an_unwired_repoint_path_reddens_naming_the_path(self):
        tree = self.synth("unwired")
        write(tree.lib("features", "setupcmd.py"),
              "import agentware_lib.core.kblayout as kblayout\n\n\n"
              "def cmd_init(args):\n    return kblayout.kb_pointable(args.d)[0]\n")
        write(tree.lib("features", "configcmd.py"),
              "def cmd_config(args):\n    return 0\n")
        problems = gate.check_wiring(tree.root)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("--set-knowledge-dir", problems[0])
        self.assertIn("does not call", problems[0])

    def test_kbtarget_a_missing_repoint_path_reddens(self):
        tree = self.synth("missing")
        problems = gate.check_wiring(tree.root)
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(all("missing" in p for p in problems), problems)

    def test_kbtarget_wiring_follows_a_module_split_into_a_package(self):
        # Task 21 turned `configcmd.py` into `configcmd/`, which made this gate
        # report the guard MISSING purely because the file moved. A wiring gate
        # must track the HANDLER, not a filename, or every decomposition reddens
        # a gate it did not actually break.
        tree = self.synth("split")
        write(tree.lib("features", "setupcmd.py"),
              "import agentware_lib.core.kblayout as kblayout\n\n\n"
              "def cmd_init(args):\n    return kblayout.kb_pointable(args.d)[0]\n")
        write(tree.lib("features", "configcmd", "__init__.py"), "")
        write(tree.lib("features", "configcmd", "cfgroutes.py"),
              "def _opt(x):\n    return x\n")
        write(tree.lib("features", "configcmd", "cfgidentity.py"),
              "import agentware_lib.core.kblayout as kblayout\n\n\n"
              "def cmd_config(args):\n"
              "    return kblayout.kb_pointable(args.d)[0]\n")
        self.assertEqual(gate.check_wiring(tree.root), [])

    def test_kbtarget_a_split_package_that_drops_the_guard_still_reddens(self):
        # The tolerance must not become a hole: resolving a stem to a PACKAGE
        # cannot mean "assume it is wired somewhere in there".
        tree = self.synth("split-unwired")
        write(tree.lib("features", "setupcmd.py"),
              "import agentware_lib.core.kblayout as kblayout\n\n\n"
              "def cmd_init(args):\n    return kblayout.kb_pointable(args.d)[0]\n")
        write(tree.lib("features", "configcmd", "__init__.py"), "")
        write(tree.lib("features", "configcmd", "cfgidentity.py"),
              "def cmd_config(args):\n    return 0\n")
        problems = gate.check_wiring(tree.root)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("--set-knowledge-dir", problems[0])
        self.assertIn("does not call", problems[0])

    def test_kbtarget_a_package_with_no_handler_at_all_reddens(self):
        tree = self.synth("split-handlerless")
        write(tree.lib("features", "setupcmd.py"),
              "import agentware_lib.core.kblayout as kblayout\n\n\n"
              "def cmd_init(args):\n    return kblayout.kb_pointable(args.d)[0]\n")
        write(tree.lib("features", "configcmd", "__init__.py"), "")
        write(tree.lib("features", "configcmd", "cfgroutes.py"),
              "def _opt(x):\n    return x\n")
        problems = gate.check_wiring(tree.root)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("no file defines cmd_config", problems[0])

    def test_kbtarget_reintroducing_the_retired_module_reddens(self):
        tree = self.synth("retired")
        write(os.path.join(tree.root, gate.RETIRED_REL), "# back from the dead\n")
        problems = gate.check_retired(tree.root)
        self.assertTrue(any("still present" in p for p in problems), problems)

    def test_kbtarget_a_stray_reference_to_the_retired_name_reddens(self):
        tree = self.synth("stray")
        write(os.path.join(tree.root, "docs", "architecture.md"),
              "see agentware_lib.features.%s for details\n" % gate.RETIRED_STEM)
        problems = gate.check_retired(tree.root)
        self.assertTrue(any("still referenced" in p for p in problems), problems)
        self.assertIn("docs/architecture.md", problems[0])

    def test_kbtarget_a_second_definition_of_the_index_filename_reddens(self):
        tree = self.synth("dup").package(_STUB_ALWAYS_OK)
        write(tree.lib("knowledge", "index.py"), 'INDEX_REL = "index.json"\n')
        problems = gate.check_single_index_rel(tree.root)
        self.assertTrue(problems)
        self.assertIn("exactly once", problems[0])
        self.assertIn("found 2", problems[0])

    def test_kbtarget_no_definition_of_the_index_filename_reddens(self):
        tree = self.synth("none")
        write(tree.lib("core", "kblayout.py"), "SENTINEL_REL = '.initialized'\n")
        problems = gate.check_single_index_rel(tree.root)
        self.assertTrue(problems)
        self.assertIn("found 0", problems[0])


class ClobberTableTest(unittest.TestCase):
    """The four clobber cases are compared against LIVE output, not restated."""

    @classmethod
    def setUpClass(cls):
        cls.tree, problems = gate.resolve_tree()
        if problems:
            raise unittest.SkipTest(problems[0])

    def test_kbtarget_the_table_covers_the_four_cases_with_both_conflict_shapes(self):
        names = [row[0] for row in gate.CLOBBER_CASES]
        self.assertEqual(names, [
            "first_run", "idempotent_same_dir", "conflicting_live_kb",
            "conflicting_bare_scaffold", "conflicting_with_force"])
        refusals = [row for row in gate.CLOBBER_CASES if row[6]]
        self.assertEqual(len(refusals), 2)
        for row in refusals:
            self.assertEqual(row[5], 3, "every refusal exits 3")
            self.assertEqual(row[6], "kb-already-configured")

    def test_kbtarget_the_live_run_matches_every_row(self):
        self.assertEqual(gate.check_clobber_table(self.tree), [])

    def test_kbtarget_expecting_the_wrong_exit_code_reddens(self):
        saved = gate.CLOBBER_CASES
        mutated = list(copy.deepcopy(list(saved)))
        row = list(mutated[2])
        row[5] = 0  # pretend a conflicting re-init is accepted
        mutated[2] = tuple(row)
        gate.CLOBBER_CASES = tuple(mutated)
        try:
            problems = gate.check_clobber_table(self.tree)
        finally:
            gate.CLOBBER_CASES = saved
        self.assertTrue(any("expected rc=0, got rc=3" in p for p in problems),
                        problems)

    def test_kbtarget_expecting_the_wrong_error_key_reddens(self):
        saved = gate.CLOBBER_CASES
        mutated = list(copy.deepcopy(list(saved)))
        row = list(mutated[3])
        row[6] = "kb-not-pointable"  # the OTHER repoint path's key
        mutated[3] = tuple(row)
        gate.CLOBBER_CASES = tuple(mutated)
        try:
            problems = gate.check_clobber_table(self.tree)
        finally:
            gate.CLOBBER_CASES = saved
        self.assertTrue(any("JSON error key" in p for p in problems), problems)


class ParityLedgerTest(unittest.TestCase):
    """The three ledger rows this task owes, and the exclusion's live probe."""

    def setUp(self):
        self.manifest = pm.load_json(pm.MANIFEST)
        self.rows = {row["id"]: row for row in self.manifest["rows"]}

    def test_kbtarget_the_relocated_guard_and_union_clobber_guard_are_required_rows(self):
        for rid in ("kb:pointer-target-guard", "kb:init-clobber-guard-union"):
            row = self.rows[rid]
            self.assertEqual(row["status"], "required", rid)
            self.assertEqual(row["evidence"]["test"], "tests.test_kbtarget_guard")
            self.assertTrue(row["evidence"]["mutation"], rid)

    def test_kbtarget_the_retired_migration_is_an_excluded_row_with_a_passing_probe(self):
        row = self.rows["kb:vendor-kb-repoint-migration"]
        self.assertEqual(row["status"], "excluded")
        self.assertEqual(row["verification"], "absence_probe")
        for field in ("reason", "approver", "follow_up", "absence_probe"):
            self.assertTrue(row.get(field), field)
        ok, detail = run_probe(REPO, row["absence_probe"])
        self.assertTrue(ok, detail)

    def test_kbtarget_the_generated_rows_the_deletion_orphans_are_adjudicated(self):
        # A frozen fixture row still claiming `required` for something this task
        # deleted would make the ledger assert the opposite of the truth.
        for rid in pm.GENERATED_EXCLUSIONS:
            row = self.rows[rid]
            self.assertEqual(row["status"], "excluded", rid)
            self.assertEqual(row["verification"], "absence_probe", rid)
            ok, detail = run_probe(REPO, row["absence_probe"])
            self.assertTrue(ok, "%s: %s" % (rid, detail))

    def test_kbtarget_a_stale_generated_exclusion_halts_the_derivation(self):
        saved = pm.GENERATED_EXCLUSIONS
        pm.GENERATED_EXCLUSIONS = dict(saved)
        pm.GENERATED_EXCLUSIONS["env:NO_SUCH_KEY_AT_ALL"] = saved[
            "env:AGENTWARE_REPOINT_AUTOPROVISION"]
        try:
            with self.assertRaises(SystemExit) as ctx:
                pm.apply_generated_exclusions([{"id": "x"}])
        finally:
            pm.GENERATED_EXCLUSIONS = saved
        self.assertIn("stale generated exclusion", str(ctx.exception))

    def test_kbtarget_the_committed_manifest_equals_a_fresh_derivation(self):
        fresh = pm.derive(REPO)
        self.assertEqual(json.dumps(fresh["rows"], sort_keys=True),
                         json.dumps(self.manifest["rows"], sort_keys=True))


if __name__ == "__main__":
    unittest.main()
