"""Owning unit file for `agentware_lib.ops.scaffoldwire` (R-MOD-06).

Feature 260813-modular-core-adoption, task 73.

Every write here happens inside a SCRATCH tree built by `_scratch()`, never in
the checkout — a test for a file-writing generator that ran against the real repo
would leave the tree dirty on any failure, and the one thing this module must be
trusted about is where it writes.

The insertions are asserted by RE-READING the edited ledger through the reader
that owns it (the audit's README row regex; importing the curated table as
Python), not by string-matching what was written. A row that lands syntactically
inside `CURATED_ROWS` but is unreadable by the parity gate is not a landed row.
"""

from tests._paths import REPO_ROOT

import json
import os
import shutil
import sys
import tempfile
import unittest

if os.path.join(REPO_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import agentware_lib.ops.scaffoldcheck as sc  # noqa: E402
import agentware_lib.ops.scaffoldunit as su  # noqa: E402
import agentware_lib.ops.scaffoldwire as sw  # noqa: E402

README_STUB = """# agentware_lib — module router

| Module | Layer | Owns | Test file |
|--------|-------|------|-----------|
| `agentware_lib.core.paths` | core | REPO_ROOT marker-walk | `tests/unit/core/test_paths.py` |

## Naming disambiguation

nothing here yet.
"""

PROBE_STUB = '''"""A stand-in for the runnable mutation-probe table."""

MUTATION_PROBES = {
}


def probe_for(row_id):
    spec = MUTATION_PROBES.get(row_id)
    return dict(spec) if spec else None
'''

CURATED_STUB = '''"""A stand-in for the curated parity table."""

LOCAL = ["local"]


def _row(rid, dimension, capability, sides, task, test, mutation, **extra):
    return {"id": rid, "dimension": dimension, "capability": capability,
            "sides": sides, "status": "required", "verification": "curated",
            "task": task, "evidence": {"test": test, "mutation": mutation}}


CURATED_ROWS = [
    _row("seed:row", "modularity", "a seed row", LOCAL, 1, "tests.x", "mutate x"),
]
'''


class ScratchTreeTestCase(unittest.TestCase):
    """A minimal tree carrying only the ledgers the writer edits."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-scaffold-wire-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._write("catalog/modules.json", json.dumps(
            {"layer_direction": {"core": [], "ops": ["core", "knowledge",
                                                     "retrieval"]},
             "modules": []}))
        self._write(su.README_REL, README_STUB)
        self._write(su.CURATED_REL, CURATED_STUB)
        # Task 79: a curated row's runnable mutation probe lives in its own
        # table, and `emit` writes into it in the same transaction. A scratch
        # tree without it half-completes and the failure reads as a scaffold
        # bug rather than a missing fixture.
        self._write(su.PROBE_REL, PROBE_STUB)
        os.makedirs(os.path.join(self.tmp, "scripts", "agentware_lib", "ops"))
        os.makedirs(os.path.join(self.tmp, "tests", "unit", "ops"))

    def _write(self, rel, text):
        dest = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(text)
        return dest

    def _read(self, rel):
        with open(os.path.join(self.tmp, rel), encoding="utf-8") as fh:
            return fh.read()

    def _unit(self, kind=su.KIND_MODULE, name="widgetthing", **kwargs):
        kwargs.setdefault("task", 73)
        if kind in (su.KIND_MODULE, su.KIND_VERB):
            kwargs.setdefault("layer", "ops")
        return su.resolve(kind, name, root=self.tmp, **kwargs)

    def _curated_rows(self):
        """Import the scratch curated table and return its rows."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "scratch_curated", os.path.join(self.tmp, su.CURATED_REL))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.CURATED_ROWS


class WriteTextTest(ScratchTreeTestCase):

    def test_it_creates_the_file_and_its_parent(self):
        sw.write_text(self.tmp, "scripts/agentware_lib/knowledge/new.py", "x")
        self.assertEqual(self._read("scripts/agentware_lib/knowledge/new.py"), "x")

    def test_it_refuses_to_clobber(self):
        sw.write_text(self.tmp, "tests/unit/ops/test_widget.py", "first")
        with self.assertRaises(su.ScaffoldError):
            sw.write_text(self.tmp, "tests/unit/ops/test_widget.py", "second")
        self.assertEqual(self._read("tests/unit/ops/test_widget.py"), "first")

    def test_force_replaces(self):
        sw.write_text(self.tmp, "tests/unit/ops/test_widget.py", "first")
        sw.write_text(self.tmp, "tests/unit/ops/test_widget.py", "second",
                      force=True)
        self.assertEqual(self._read("tests/unit/ops/test_widget.py"), "second")

    def test_a_new_test_package_gets_its_init(self):
        """A test file in a directory with no package marker is collected by
        nothing, so it would "pass" by never running."""
        sw.write_text(self.tmp, "tests/unit/newlayer/test_widget.py", "x")
        self.assertTrue(os.path.exists(
            os.path.join(self.tmp, "tests", "unit", "newlayer", "__init__.py")))

    def test_it_does_not_plant_an_init_outside_the_test_corpus(self):
        sw.write_text(self.tmp, "scripts/loop/widgetgate.sh", "x")
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, "scripts", "loop", "__init__.py")))


class InsertDocRowTest(ScratchTreeTestCase):

    def test_the_row_is_found_by_the_reader_that_audits_it(self):
        unit = self._unit()
        sw.insert_doc_row(self.tmp, unit)
        self.assertIn(unit["dotted"], sc.doc_rows(self.tmp))

    def test_it_lands_inside_the_table_not_after_the_document(self):
        sw.insert_doc_row(self.tmp, self._unit())
        text = self._read(su.README_REL)
        self.assertLess(text.index("agentware_lib.ops.widgetthing"),
                        text.index("## Naming disambiguation"))

    def test_two_rows_both_survive(self):
        sw.insert_doc_row(self.tmp, self._unit(name="widgetone"))
        sw.insert_doc_row(self.tmp, self._unit(name="widgettwo"))
        rows = sc.doc_rows(self.tmp)
        self.assertIn("agentware_lib.ops.widgetone", rows)
        self.assertIn("agentware_lib.ops.widgettwo", rows)

    def test_a_readme_with_no_table_is_refused_rather_than_appended_to(self):
        self._write(su.README_REL, "# no table here\n")
        with self.assertRaises(su.ScaffoldError):
            sw.insert_doc_row(self.tmp, self._unit())


class InsertParityRowTest(ScratchTreeTestCase):

    def test_the_row_joins_curated_rows_and_still_imports(self):
        sw.insert_parity_row(self.tmp, self._unit())
        rows = self._curated_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["id"], "mod:unit:widgetthing")
        self.assertEqual(rows[-1]["task"], 73)

    def test_a_verb_unit_lands_a_surface_row(self):
        sw.insert_parity_row(self.tmp, self._unit(su.KIND_VERB, verb="widget"))
        self.assertEqual(self._curated_rows()[-1]["id"], "surface:widget")

    def test_a_table_with_no_closing_bracket_is_refused(self):
        self._write(su.CURATED_REL, "CURATED_ROWS = []\n")
        with self.assertRaises(su.ScaffoldError):
            sw.insert_parity_row(self.tmp, self._unit())


class EmitTest(ScratchTreeTestCase):

    def _emit(self, unit, **kwargs):
        kwargs.setdefault("regen", False)
        return sw.emit(unit, root=self.tmp, **kwargs)

    def test_a_dry_run_writes_absolutely_nothing(self):
        unit = self._unit()
        before = self._read(su.CURATED_REL), self._read(su.README_REL)
        result = self._emit(unit, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, unit["path"])))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, unit["test_path"])))
        self.assertEqual((self._read(su.CURATED_REL),
                          self._read(su.README_REL)), before)

    def test_a_dry_run_reports_the_same_paths_the_real_run_claims(self):
        planned = self._emit(self._unit(), dry_run=True)
        done = self._emit(self._unit())
        self.assertEqual(planned["created"], done["created"])
        self.assertEqual(planned["edited"], done["edited"])

    def test_it_lands_all_five_artifacts_for_a_verb(self):
        """R-MOD-11's whole unit — now SIX artifacts, not five.

        The name is left alone deliberately (`tests/README.md`: the preserved-
        surface freeze cannot tell a rename from a deletion). Task 79 split a
        curated row's proof out of the row: the claim stays in
        `_parity_curated.py` and the runnable mutation lands in
        `_parity_mutation_rows.py`, so a unit that lands its row without its
        probe has landed a claim nothing can refute.
        """
        unit = self._unit(su.KIND_VERB, verb="widget")
        self._emit(unit)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, unit["path"])))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, unit["test_path"])))
        self.assertIn(unit["dotted"], sc.doc_rows(self.tmp))
        self.assertEqual(self._curated_rows()[-1]["id"], "surface:widget")
        self.assertIn("__verbs__", self._read(unit["path"]))
        self.assertIn('"surface:widget": {', self._read(su.PROBE_REL))

    def test_the_probe_entry_lands_keyed_by_the_rows_id(self):
        unit = self._unit(su.KIND_VERB, verb="widget")
        self._emit(unit)
        table = self._read(su.PROBE_REL)
        self.assertIn(unit["row_id"], table)
        self.assertIn(unit["path"], table,
                      "the probe must address the module the unit just landed")

    def test_a_probe_table_with_no_closing_brace_is_refused(self):
        self._write(su.PROBE_REL, "MUTATION_PROBES = {}\n")
        with self.assertRaises(su.ScaffoldError):
            sw.insert_probe_entry(self.tmp, self._unit())

    def test_a_panel_gets_no_doc_row_because_the_table_is_the_lib_router(self):
        unit = self._unit(su.KIND_PANEL, name="readwidget")
        result = self._emit(unit)
        self.assertNotIn(su.README_REL, result["edited"])
        self.assertEqual(sc.doc_rows(self.tmp), {"agentware_lib.core.paths"})

    def test_a_loop_lib_still_owes_a_parity_row(self):
        self._emit(self._unit(su.KIND_LOOP, name="widgetgate"))
        self.assertEqual(self._curated_rows()[-1]["dimension"],
                         "loop_orchestration")


class NextStepsTest(ScratchTreeTestCase):
    """The follow-ups a generator must not fake."""

    def test_a_verb_is_told_to_update_the_independent_parser_pins(self):
        steps = " ".join(sw.next_steps(self._unit(su.KIND_VERB, verb="widget")))
        self.assertIn("EXPECTED_VERBS", steps)
        self.assertIn("EXPECTED_SURFACES", steps)
        self.assertIn("_help_golden.py --write", steps)

    def test_a_plain_module_is_not(self):
        steps = " ".join(sw.next_steps(self._unit()))
        self.assertNotIn("EXPECTED_VERBS", steps)

    def test_a_loop_lib_is_told_to_wire_its_call_site(self):
        steps = " ".join(sw.next_steps(self._unit(su.KIND_LOOP, name="widgetgate")))
        self.assertIn("agentware.sh", steps)

    def test_every_kind_is_told_to_run_the_audit(self):
        for kind, kwargs in ((su.KIND_MODULE, {}),
                             (su.KIND_VERB, {"verb": "widget"}),
                             (su.KIND_PANEL, {"name": "readwidget"}),
                             (su.KIND_LOOP, {"name": "widgetgate"})):
            steps = sw.next_steps(self._unit(kind, **kwargs))
            self.assertIn("run: agentware scaffold check", steps, kind)

    def test_emit_never_touches_the_parser_pin_itself(self):
        """The pin is the only place the whole ordered surface is written down;
        a scaffold that edited it would certify its own change."""
        pin = "tests/gates/test_parser_golden.py"
        self._write(pin, "EXPECTED_VERBS = []\n")
        sw.emit(self._unit(su.KIND_VERB, verb="widget"), root=self.tmp,
                regen=False)
        self.assertEqual(self._read(pin), "EXPECTED_VERBS = []\n")


class RegenerateTest(ScratchTreeTestCase):
    """The derived artifacts are derived by their OWN generators, or reported."""

    def test_a_missing_regenerator_is_reported_not_swallowed(self):
        results = sw.regenerate(self.tmp)
        self.assertEqual([name for name, _rc, _out in results],
                         [name for name, _argv in sw.REGENERATORS])
        for _name, rc, out in results:
            self.assertEqual(rc, 127)
            self.assertIn("missing regenerator", out)

    def test_the_regenerators_are_the_commands_the_rules_name(self):
        argvs = dict(sw.REGENERATORS)
        self.assertEqual(argvs["manifest"], ("tests/_manifest_gen.py", "--write"))
        self.assertEqual(argvs["parity"], ("tests/_parity_manifest.py", "--out"))


if __name__ == "__main__":
    unittest.main()
