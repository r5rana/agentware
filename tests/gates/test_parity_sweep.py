"""The parity mutation sweep, proven able to fail.

Feature 260813-modular-core-adoption, task 79.

`tests/_parity_sweep.py` is the instrument that finally RUNS the `mutation`
field every curated parity row has carried since task 10. An instrument nobody
has seen report a failure is worth nothing -- that is the whole lesson of task
78, which found three of the five documented structural checkers exiting 0
without measuring anything, their `rc=0` recorded as verification by seven task
worklogs. So this module never merely watches the sweep pass.

FIVE ARMS
---------
  A  STATIC COVERAGE -- every curated row carries a probe that parses under the
     closed grammar, names a path the tree actually has, and is not circular.
     Costs milliseconds and needs no clone.
  B  THE GRAMMAR IS FAIL-LOUD -- each applier changes the tree when it should
     and returns (False, reason) when it cannot. The silent no-op is the
     dangerous failure (it looks exactly like a missing capability), so each
     kind is proven to refuse one.
  C  THE FOUR NON-PASS VERDICTS, LIVE -- a real sandbox, a real test module, and
     one ad-hoc probe per verdict: PASS, STALE-PROBE, NOT-REDDENED, LOAD-ERROR.
     This is the arm that proves the sweep can go red at all.
  D  THE INSTRUMENT IS NOT MUTABLE -- a probe naming the sweep's own files, or
     naming the test it is supposed to redden, is refused.
  E  A HANG IS A FAILURE -- a timed-out module reports as a red, never as an
     empty result that reconciles to green. This repository has lost four
     autonomous iterations to test runs that hung rather than failed.

Every mutation in every arm happens inside a temporary clone or a temporary
directory. Nothing here writes to the working tree or the knowledge base.
"""

from tests._paths import TESTS_DIR

import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _parity_mutations as pm   # noqa: E402
import _parity_sweep as sweep    # noqa: E402

#: A cheap, hermetic subject: nine tests, ~0.05s, and its capability (the
#: preserved ROADMAP and the union README) is mutable from a data file rather
#: than from code, so a probe against it cannot break an import by accident.
SUBJECT = "tests.gates.test_readme_union"


def _status(sandbox):
    """`git status --porcelain` for a sandbox, as a sorted, comparable list."""
    import subprocess
    proc = subprocess.run(["git", "-C", sandbox, "status", "--porcelain"],
                          capture_output=True, text=True, check=True)
    return sorted(line for line in proc.stdout.splitlines() if line.strip())


class ProbeCoverageTest(unittest.TestCase):
    """Arm A -- the static gate, over the live table."""

    def test_every_curated_row_carries_a_runnable_probe(self):
        problems = sweep.check_probes(REPO)
        self.assertEqual(
            [], problems,
            "a curated parity row without a runnable mutation probe is a "
            "capability claim nothing can refute:\n  " + "\n  ".join(problems))

    def test_the_table_is_not_empty(self):
        # A coverage assertion over an empty table passes vacuously, which is
        # the shape this whole file exists to reject.
        rows = sweep.curated_rows()
        self.assertGreater(len(rows), 60, "the curated table lost rows")
        self.assertTrue(all(spec for _rid, _t, spec in rows))


class MutationGrammarTest(unittest.TestCase):
    """Arm B -- every applier changes the tree, or says loudly that it did not."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-mutgrammar-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "pkg"))
        with open(os.path.join(self.root, "pkg", "mod.py"), "w") as fh:
            fh.write("VALUE = 1\n")

    def _read(self, rel):
        with open(os.path.join(self.root, rel)) as fh:
            return fh.read()

    def test_replace_substitutes_and_reports(self):
        ok, detail = pm.apply_mutation(
            self.root, {"kind": "replace", "path": "pkg/mod.py",
                        "find": "VALUE = 1", "replace": "VALUE = 2"})
        self.assertTrue(ok, detail)
        self.assertEqual("VALUE = 2\n", self._read("pkg/mod.py"))

    def test_replace_refuses_a_find_string_that_is_absent(self):
        ok, detail = pm.apply_mutation(
            self.root, {"kind": "replace", "path": "pkg/mod.py",
                        "find": "NOT THERE", "replace": "x"}, "row:x")
        self.assertFalse(ok)
        self.assertIn("STALE", detail)
        self.assertEqual("VALUE = 1\n", self._read("pkg/mod.py"))

    def test_delete_path_removes_a_file_and_refuses_an_absent_one(self):
        ok, _d = pm.apply_mutation(self.root,
                                   {"kind": "delete-path", "path": "pkg/mod.py"})
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(os.path.join(self.root, "pkg/mod.py")))
        ok, detail = pm.apply_mutation(
            self.root, {"kind": "delete-path", "path": "pkg/mod.py"}, "row:x")
        self.assertFalse(ok)
        self.assertIn("STALE", detail)

    def test_delete_path_removes_a_whole_directory(self):
        ok, _d = pm.apply_mutation(self.root,
                                   {"kind": "delete-path", "path": "pkg"})
        self.assertTrue(ok)
        self.assertFalse(os.path.isdir(os.path.join(self.root, "pkg")))

    def test_append_adds_bytes_and_refuses_an_empty_text(self):
        ok, _d = pm.apply_mutation(self.root, {"kind": "append",
                                               "path": "pkg/mod.py",
                                               "text": "EXTRA = 3\n"})
        self.assertTrue(ok)
        self.assertIn("EXTRA = 3", self._read("pkg/mod.py"))
        ok, detail = pm.apply_mutation(self.root, {"kind": "append",
                                                   "path": "pkg/mod.py",
                                                   "text": ""})
        self.assertFalse(ok)
        self.assertIn("changes nothing", detail)

    def test_write_creates_missing_parents_and_refuses_an_identical_body(self):
        ok, _d = pm.apply_mutation(self.root, {"kind": "write",
                                               "path": "pkg/sub/new.py",
                                               "text": "X = 1\n"})
        self.assertTrue(ok)
        self.assertEqual("X = 1\n", self._read("pkg/sub/new.py"))
        ok, detail = pm.apply_mutation(self.root, {"kind": "write",
                                                   "path": "pkg/sub/new.py",
                                                   "text": "X = 1\n"}, "row:x")
        self.assertFalse(ok)
        self.assertIn("reproduce the existing bytes", detail)

    def test_rename_moves_and_refuses_an_occupied_destination(self):
        ok, _d = pm.apply_mutation(self.root, {"kind": "rename",
                                               "path": "pkg/mod.py",
                                               "to": "pkg/moved.py"})
        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "pkg/moved.py")))
        with open(os.path.join(self.root, "pkg", "mod.py"), "w") as fh:
            fh.write("VALUE = 1\n")
        ok, detail = pm.apply_mutation(self.root, {"kind": "rename",
                                                   "path": "pkg/mod.py",
                                                   "to": "pkg/moved.py"})
        self.assertFalse(ok)
        self.assertIn("already exists", detail)

    def test_an_unknown_kind_is_refused_rather_than_guessed_at(self):
        ok, detail = pm.apply_mutation(self.root, {"kind": "chmod",
                                                   "path": "pkg/mod.py"}, "row:x")
        self.assertFalse(ok)
        self.assertIn("unknown mutation kind", detail)

    def test_a_path_escaping_the_tree_is_refused(self):
        errors = pm.spec_errors({"kind": "delete-path", "path": "../outside"},
                                "row:x")
        self.assertTrue(any("relative path inside the tree" in e for e in errors),
                        errors)

    def test_a_replace_that_substitutes_a_string_for_itself_is_refused(self):
        errors = pm.spec_errors({"kind": "replace", "path": "pkg/mod.py",
                                 "find": "VALUE", "replace": "VALUE"}, "row:x")
        self.assertTrue(any("changes nothing" in e for e in errors), errors)


class InstrumentIsNotMutableTest(unittest.TestCase):
    """Arm D -- the sweep may not be pointed at itself or at its own subject."""

    def test_the_sweep_and_its_grammar_may_not_be_mutated(self):
        for rel in pm.UNMUTABLE:
            errors = pm.spec_errors({"kind": "append", "path": rel,
                                     "text": "#\n"}, "row:x")
            self.assertTrue(
                any("instrument" in e for e in errors),
                "%s must be refused as a mutation target: %r" % (rel, errors))

    def test_the_probe_TABLE_is_mutable_because_it_is_data_not_instrument(self):
        """The line between the instrument and its input, asserted.

        Only the two files EXECUTED in the sandbox are unmutable. The probe
        table is read by the parent before any sandbox exists, so mutating the
        sandbox's copy cannot change what the sweep does -- and it has to be
        mutable, because the row claiming every curated row carries a probe is
        refuted only by taking a probe away.
        """
        self.assertNotIn("tests/_parity_mutation_rows.py", pm.UNMUTABLE)
        self.assertEqual(
            [], pm.spec_errors({"kind": "replace",
                                "path": "tests/_parity_mutation_rows.py",
                                "find": "a", "replace": "b"}, "row:x"))

    def test_version_control_internals_may_not_be_mutated(self):
        errors = pm.spec_errors({"kind": "delete-path", "path": ".git/HEAD"},
                                "row:x")
        self.assertTrue(any("version-control internals" in e for e in errors),
                        errors)

    def test_a_probe_targeting_its_own_named_test_is_circular_and_refused(self):
        own = os.path.join(*SUBJECT.split(".")) + ".py"
        errors = sweep._probe_target_errors(
            REPO, "row:x", {"kind": "append", "path": own, "text": "#\n"}, SUBJECT)
        self.assertTrue(any("IS the named test" in e for e in errors), errors)


class HangIsAFailureTest(unittest.TestCase):
    """Arm E -- a module that does not finish reports RED, never empty-green."""

    def test_a_timed_out_module_reports_as_an_error(self):
        sandbox = tempfile.mkdtemp(prefix="aw-sweep-timeout-")
        self.addCleanup(shutil.rmtree, sandbox, True)
        sweep.make_sandbox(os.path.join(sandbox, "sbx"), REPO)
        result = sweep.run_module(os.path.join(sandbox, "sbx"), SUBJECT,
                                  timeout=0.001)
        self.assertTrue(result["crashed"])
        self.assertTrue(sweep.reds(result),
                        "a run that never finished must report a red, not {}")
        self.assertIn("timed out", sweep.reds(result)[0][1])


class SweepVerdictsAreReachableTest(unittest.TestCase):
    """Arm C -- the four non-PASS verdicts, produced live in a real sandbox."""

    @classmethod
    def setUpClass(cls):
        cls.workdir = tempfile.mkdtemp(prefix="aw-sweep-verdicts-")
        cls.sandbox = sweep.make_sandbox(os.path.join(cls.workdir, "sbx"), REPO)
        cls.pristine = _status(cls.sandbox)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.workdir, ignore_errors=True)

    def _verdict(self, spec):
        return sweep.sweep_row(self.sandbox, "arm-c", SUBJECT, spec)

    def test_a_faithful_mutation_reddens_and_names_the_test_it_reddened(self):
        out = self._verdict({"kind": "append", "path": "ROADMAP.md",
                             "text": "\nplanted by the sweep's own gate\n"})
        self.assertEqual(sweep.PASS, out["verdict"], out.get("detail"))
        self.assertTrue(out["reddened"], "a PASS must name what went red")
        self.assertTrue(any("Roadmap" in nid or "roadmap" in nid
                            for nid in out["reddened"]), out["reddened"])

    def test_a_no_op_mutation_is_STALE_PROBE_and_never_a_quiet_pass(self):
        out = self._verdict({"kind": "replace", "path": "ROADMAP.md",
                             "find": "a string this document does not contain",
                             "replace": "x"})
        self.assertEqual(sweep.STALE_PROBE, out["verdict"], out.get("detail"))

    def test_a_mutation_the_test_cannot_see_is_NOT_REDDENED(self):
        # CHANGELOG.md is not what test_readme_union asserts about, so this
        # mutation applies cleanly and the module stays green -- the exact
        # shape of an unguarded capability.
        out = self._verdict({"kind": "append", "path": "CHANGELOG.md",
                             "text": "\n<!-- invisible to the subject -->\n"})
        self.assertEqual(sweep.NOT_REDDENED, out["verdict"], out.get("detail"))

    def test_a_mutation_that_only_breaks_an_import_is_LOAD_ERROR_not_a_pass(self):
        # The subject imports `tests/_markdown_links.py`. Deleting it makes the
        # module unimportable: unittest reports a synthetic _FailedTest, no
        # assertion runs, and accepting that as proof would let any probe that
        # breaks Python masquerade as a capability check.
        out = self._verdict({"kind": "delete-path",
                             "path": "tests/_markdown_links.py"})
        self.assertEqual(sweep.LOAD_ERROR, out["verdict"], out.get("detail"))

    def test_the_sandbox_is_restored_between_rows(self):
        # Every case above mutated the same sandbox. If restoration were
        # incomplete the later rows would have measured a dirty tree, so the
        # state of the clone IS an assertion, not housekeeping. The comparison
        # is against the state the sandbox was BUILT in -- which carries the
        # author's uncommitted work staged -- not against an empty status,
        # because "clean" here means "as created", never "as committed".
        self.assertEqual(self.pristine, _status(self.sandbox),
                         "the sandbox drifted from the state it was created in")


if __name__ == "__main__":
    unittest.main()
