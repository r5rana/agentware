"""Owning unit file for `agentware_lib.ops.scaffoldunit` (R-MOD-06).

Feature 260813-modular-core-adoption, task 73.

The point of a scaffold is that what it emits PASSES the gates the tree already
runs. So these assertions are made against those gates' own definitions wherever
one exists — the manifest generator's docstring-label parser, the verb-discovery
grammar, the parity row schema, the audit's README row regex — rather than
against a copy of what each is believed to require. A template that drifts away
from a gate is then a red test here, not a red suite three commits later.
"""

from tests._paths import REPO_ROOT, TESTS_DIR

import os
import sys
import tempfile
import unittest

if os.path.join(REPO_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

import agentware_lib.ops.scaffoldcheck as sc  # noqa: E402
import agentware_lib.ops.scaffoldunit as su  # noqa: E402
from agentware_lib import verbs as vb  # noqa: E402

import _manifest_gen as mg  # noqa: E402
import _parity_gate_checks as pgc  # noqa: E402
import _parity_schema as psch  # noqa: E402


def _write(tmp, rel, text):
    dest = os.path.join(tmp, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(text)
    return dest


class ResolveValidationTest(unittest.TestCase):
    """A bad description is refused BEFORE anything is written."""

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(su.ScaffoldError):
            su.resolve("widget", "thing", layer="ops", task=73)

    def test_a_malformed_stem_is_refused(self):
        for bad in ("", "Thing", "two_words", "with-dash", "9lives"):
            with self.assertRaises(su.ScaffoldError):
                su.resolve(su.KIND_MODULE, bad, layer="ops", task=73)

    def test_a_grabbag_name_is_refused_by_name(self):
        """R-MOD-05: a module named for what it is NOT has no boundary."""
        for bad in ("utils", "helpers", "common", "misc", "shared"):
            with self.assertRaises(su.ScaffoldError) as ctx:
                su.resolve(su.KIND_MODULE, bad, layer="ops", task=73)
            self.assertIn("R-MOD-05", str(ctx.exception))

    def test_a_lib_kind_needs_a_known_layer(self):
        with self.assertRaises(su.ScaffoldError):
            su.resolve(su.KIND_MODULE, "thing", task=73)
        with self.assertRaises(su.ScaffoldError):
            su.resolve(su.KIND_MODULE, "thing", layer="nowhere", task=73)

    def test_the_verb_kind_needs_a_verb_the_discovery_contract_accepts(self):
        with self.assertRaises(su.ScaffoldError):
            su.resolve(su.KIND_VERB, "thing", layer="ops", task=73)
        with self.assertRaises(su.ScaffoldError):
            su.resolve(su.KIND_VERB, "thing", layer="ops", verb="Bad_Verb", task=73)

    def test_an_existing_target_is_refused(self):
        """`ops/scaffoldunit.py` exists — scaffolding onto it would clobber it."""
        with self.assertRaises(su.ScaffoldError) as ctx:
            su.resolve(su.KIND_MODULE, "scaffoldunit", layer="ops", task=73)
        self.assertIn("already exists", str(ctx.exception))

    def test_a_unit_with_no_plan_task_is_refused(self):
        """The parity schema refuses a row with no task, so the scaffold refuses
        the unit rather than emitting a row the gate will reject."""
        for bad in (0, None, -1):
            with self.assertRaises(su.ScaffoldError) as ctx:
                su.resolve(su.KIND_MODULE, "widgetthing", layer="ops", task=bad)
            self.assertIn("--task", str(ctx.exception))

    def test_a_non_lib_kind_ignores_layer_and_verb(self):
        unit = su.resolve(su.KIND_PANEL, "readthing", layer="ops", verb="thing",
                          task=73)
        self.assertIsNone(unit["layer"])
        self.assertIsNone(unit["verb"])


class ResolvedPathsTest(unittest.TestCase):
    """Each kind lands where its layer says, with the mirrored test beside it."""

    def test_module_and_verb_land_in_the_lib_with_a_unit_file(self):
        unit = su.resolve(su.KIND_MODULE, "widgetlint", layer="retrieval", task=73)
        self.assertEqual(unit["path"],
                         "scripts/agentware_lib/retrieval/widgetlint.py")
        self.assertEqual(unit["test_path"],
                         "tests/unit/retrieval/test_widgetlint.py")
        self.assertEqual(unit["dotted"], "agentware_lib.retrieval.widgetlint")
        self.assertEqual(unit["test_dotted"],
                         "tests.unit.retrieval.test_widgetlint")

    def test_a_panel_lands_in_the_dashboard_package(self):
        unit = su.resolve(su.KIND_PANEL, "readwidget", task=73)
        self.assertEqual(unit["path"], "scripts/agentware_dashboard/readwidget.py")
        self.assertEqual(unit["test_path"], "tests/unit/dashboard/test_readwidget.py")

    def test_a_loop_lib_lands_in_scripts_loop_as_shell(self):
        unit = su.resolve(su.KIND_LOOP, "widgetgate", task=73)
        self.assertEqual(unit["path"], "scripts/loop/widgetgate.sh")
        self.assertEqual(unit["test_path"], "tests/integration/test_loop_widgetgate.py")

    def test_the_test_path_matches_the_ownership_rule_the_tree_enforces(self):
        """The mirrored path is R-MOD-06's, not a second opinion about it."""
        import _test_ownership as to  # noqa: E402
        unit = su.resolve(su.KIND_MODULE, "widgetlint", layer="ops", task=73)
        self.assertEqual(
            unit["test_path"],
            to.granular_test_rel(os.path.join(REPO_ROOT, unit["path"])))


class OrderKeyTest(unittest.TestCase):
    """A new verb takes the next SPARSE key, so no existing verb moves."""

    def test_the_next_key_is_one_step_past_the_live_maximum(self):
        keys = [order for _v, order, _m, _a in vb.discover()]
        self.assertEqual(su.next_order(), max(keys) + su.ORDER_STEP)

    def test_the_next_key_collides_with_nothing(self):
        keys = {order for _v, order, _m, _a in vb.discover()}
        self.assertNotIn(su.next_order(), keys)

    def test_the_step_matches_the_discovery_contract(self):
        self.assertEqual(su.ORDER_STEP, vb.ORDER_STEP)


class RenderedModuleTest(unittest.TestCase):
    """What the skeleton emits satisfies the gates, checked against those gates."""

    def _emit(self, unit):
        tmp = tempfile.mkdtemp(prefix="aw-scaffold-unit-")
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        return _write(tmp, unit["path"], su.render_unit(unit))

    def test_the_skeleton_carries_every_label_the_manifest_parses(self):
        for kind, kwargs in ((su.KIND_MODULE, {"layer": "ops"}),
                             (su.KIND_VERB, {"layer": "ops", "verb": "widget"}),
                             (su.KIND_PANEL, {})):
            unit = su.resolve(kind, "widgetthing", task=73, **kwargs)
            path = self._emit(unit)
            self.assertEqual(mg.docstring_missing_labels(path), [], kind)

    def test_the_skeleton_declares_a_fanin_line_the_generator_recognises(self):
        unit = su.resolve(su.KIND_MODULE, "widgetthing", layer="ops", task=73)
        meta = mg.parse_docstring_meta(self._emit(unit))
        self.assertTrue(meta["has_fanin"])
        self.assertEqual(meta["fanin_declared"], 0)

    def test_the_layer_deps_line_names_only_layers_below(self):
        """Rendered from the PUBLISHED direction table, so it cannot invent an
        upward edge the boundary gate would then reject."""
        allowed = su.layer_direction().get("knowledge") or []
        unit = su.resolve(su.KIND_MODULE, "widgetthing", layer="knowledge", task=73)
        deps = su._deps_sentence("knowledge")
        self.assertTrue(deps.startswith("knowledge ->"), deps)
        for layer in su.LIB_LAYERS:
            if layer in allowed or layer == "knowledge":
                continue
            self.assertNotIn(" %s," % layer, deps)
        self.assertIn(deps.split(" -> ")[1].split(" (")[0].split(",")[0],
                      allowed or ["(nothing"])
        self.assertIn("Layer deps:", su.render_unit(unit))

    def test_a_verb_skeleton_declares_a_row_the_discovery_contract_accepts(self):
        unit = su.resolve(su.KIND_VERB, "widgetthing", layer="ops", verb="widget", task=73)
        src = su.render_unit(unit)
        namespace = {}
        exec(compile(src, "widgetthing.py", "exec"), namespace)  # noqa: S102
        rows = vb.collect([("agentware_lib.ops.widgetthing",
                            namespace["__verbs__"])])
        self.assertEqual(rows, (("widget", unit["order"],
                                 "agentware_lib.ops.widgetthing",
                                 "register_widget"),))

    def test_a_verb_skeleton_leaves_a_marker_where_the_behaviour_goes(self):
        unit = su.resolve(su.KIND_VERB, "widgetthing", layer="ops", verb="widget", task=73)
        self.assertIn(su.TODO, su.render_unit(unit))

    def test_a_loop_lib_is_shell_that_parses(self):
        import subprocess
        unit = su.resolve(su.KIND_LOOP, "widgetgate", task=73)
        path = self._emit(unit)
        proc = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class RenderedTestTest(unittest.TestCase):
    """The mirrored stub is a REAL test on the day it lands, not a pass-by-default."""

    def test_the_stub_imports_the_module_it_owns(self):
        unit = su.resolve(su.KIND_MODULE, "widgetthing", layer="ops", task=73)
        self.assertIn("import agentware_lib.ops.widgetthing", su.render_test(unit))

    def test_the_stub_asserts_something(self):
        for kind, kwargs in ((su.KIND_MODULE, {"layer": "ops"}),
                             (su.KIND_VERB, {"layer": "ops", "verb": "widget"}),
                             (su.KIND_PANEL, {}), (su.KIND_LOOP, {})):
            body = su.render_test(su.resolve(kind, "widgetthing", task=73, **kwargs))
            self.assertIn("self.assert", body, kind)
            compile(body, "test_widgetthing.py", "exec")

    def test_the_stub_puts_scripts_on_the_path_before_importing(self):
        """Layered unit files must, or the import resolves only by luck."""
        body = su.render_test(su.resolve(su.KIND_MODULE, "widgetthing", layer="ops", task=73))
        self.assertLess(body.index('"scripts"'), body.index("import agentware_lib"))


class RenderedRowsTest(unittest.TestCase):
    """The doc row and the parity row are accepted by the things that read them."""

    def test_the_doc_row_matches_the_readme_row_grammar(self):
        unit = su.resolve(su.KIND_MODULE, "widgetthing", layer="ops", task=73)
        found = sc.DOC_ROW_RE.findall(su.render_doc_row(unit))
        self.assertEqual(found, ["agentware_lib.ops.widgetthing"])

    def _row(self, unit):
        namespace = {}
        src = ("from tests._parity_curated import _row, LOCAL\nROW = [\n%s]\n"
               % su.render_parity_row(unit))
        exec(compile(src, "row.py", "exec"), namespace)  # noqa: S102
        return namespace["ROW"][0]

    def test_the_parity_row_is_valid_python_and_a_schema_valid_row(self):
        unit = su.resolve(su.KIND_VERB, "widgetthing", layer="ops",
                          verb="widget", task=73)
        row = self._row(unit)
        self.assertEqual(row["id"], "surface:widget")
        self.assertEqual(row["dimension"], "cli_surface")
        self.assertEqual(row["task"], 73)
        self.assertEqual(pgc._check_row(row, set(psch.DIMENSION_NAMES)), [])

    def test_a_non_verb_unit_gets_a_modularity_row(self):
        row = self._row(su.resolve(su.KIND_MODULE, "widgetthing", layer="ops",
                                   task=73))
        self.assertEqual(row["dimension"], "modularity")
        self.assertEqual(row["id"], "mod:unit:widgetthing")
        self.assertEqual(pgc._check_row(row, set(psch.DIMENSION_NAMES)), [])

    def test_the_row_names_the_units_own_test_module(self):
        unit = su.resolve(su.KIND_MODULE, "widgetthing", layer="ops", task=73)
        self.assertEqual(self._row(unit)["evidence"]["test"], unit["test_dotted"])

    def test_prose_with_a_quote_cannot_break_the_row_it_is_written_into(self):
        self.assertEqual(su._pysafe('a "b" c\\d\ne'), 'a \\"b\\" c\\\\d e')


if __name__ == "__main__":
    unittest.main()
