"""The two STRUCTURAL modularity rules (plan task 68) — layer direction, names.

`test_module_ceilings.py` asserts the tree is within its SIZE caps and
`test_module_manifest.py` asserts every file is claimed. Neither can see the two
shapes a monolith actually grows back through:

  1. an upward MODULE-LEVEL import, which makes the layer names a fiction and
     fixes the package's load order; and
  2. a dumping-ground module name (`utils`, `helpers`, `common`, `misc`,
     `shared`), which has no boundary to be outside of, so everything belongs in
     it from the day it is created — while every size cap is still satisfied.

Both rules shipped in the donor tree (`test_module_boundaries` M12 and M10) and
neither survived the merge: `_layerutil.direction_violations` had NO caller in
this tree, and nothing looked at module names at all. Task 68 needs both to
redden for its negative test, so this file arms them and
`tests/_modularity_rules.py` measures them.

Four claims:

  1. DIRECTION — no lib module imports an upper layer at module level, except at
     a declared, owned location. The function-local escape hatch is asserted to
     be genuinely exempt against a REAL module (`ops/audit.py`, whose own
     docstring says "upward, lazy"), so the module-level scoping is a measured
     property rather than a comment.
  2. NAMES — no corpus member carries a dumping-ground name except at a declared
     row, and under `agentware_lib/**` the ban is ABSOLUTE: `schema_errors`
     refuses the row, so a new `ops/utils.py` cannot be legalised by declaring
     it.
  3. RATCHET — the declared residual is well-formed, owned, and shrink-only in
     both directions (a worsened violation and a row the tree no longer produces
     are both failures).
  4. REFUTED — every rule is proven to BITE on a synthetic tree, because a check
     that has never failed has never been tested.

Stdlib only, deterministic, hermetic (reads the tree with `ast` and `os.walk`;
the refutations build three-file scratch trees in `tempfile`, never writing into
the checkout under test).
"""

from tests._paths import REPO_ROOT, TESTS_DIR

import json
import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests import _corpus
    from tests import _layerutil
    from tests import _modularity_rules as mr
except ImportError:  # allow a bare-name run from inside tests/
    sys.path.insert(0, TESTS_DIR)
    import _corpus
    import _layerutil
    import _modularity_rules as mr

_NO_CORE = not mr.lib_present(REPO_ROOT)
_NO_CORE_REASON = "scripts/agentware_lib is not present in this tree yet"

MODULE_HEAD = ('"""Owns: nothing real — a scratch module.\n\n'
               'Does NOT own: anything.\n"""\n')


def _write(root, rel, body):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def _scratch_lib(testcase):
    """A minimal tree with a real agentware_lib package, cleaned up after."""
    tmp = tempfile.mkdtemp(prefix="aw-modrules-")
    testcase.addCleanup(shutil.rmtree, tmp, True)
    _write(tmp, "scripts/agentware_lib/__init__.py", "")
    for layer in ("core", "knowledge", "retrieval", "ops", "features"):
        _write(tmp, "scripts/agentware_lib/%s/__init__.py" % layer, "")
    return tmp


@unittest.skipIf(_NO_CORE, _NO_CORE_REASON)
class LayerDirectionTest(unittest.TestCase):
    """Claim 1 — the folder structure is a dependency claim, and it is true."""

    def test_no_undeclared_module_level_upward_import(self):
        problems, _clean = mr.reconcile()
        over = [p for p in problems if p[0] == mr.OVER
                and p[2].startswith(mr.UPWARD)]
        self.assertEqual(
            over, [],
            "MODULE-LEVEL upward import(s) at an undeclared location, or worse "
            "than declared (core -> nothing; knowledge -> core; retrieval -> "
            "core/knowledge; ops -> core/knowledge/retrieval; features -> any; "
            "cli -> all). Move the shared symbol DOWN to a layer both sides may "
            "import, or make the edge a function-local import if it is "
            "genuinely irreducible: %r" % (over,))

    def test_no_declared_upward_row_is_stale(self):
        problems, _clean = mr.reconcile()
        stale = [p for p in problems if p[0] == mr.STALE]
        self.assertEqual(
            stale, [],
            "declared row(s) the tree no longer produces — delete them, this "
            "file only shrinks: %r" % (stale,))

    def test_a_function_local_upward_import_is_the_documented_escape_hatch(self):
        # Measured against a REAL module rather than a synthetic one, because
        # the exemption is only meaningful if it matches what the tree actually
        # does: ops/audit.py imports features.dream and features.staleproposal
        # INSIDE cmd_audit and says so in its own docstring ("upward, lazy").
        # The whole-file analysis reports it; the module-level analysis must not.
        rel = "scripts/agentware_lib/ops/audit.py"
        mod = os.path.join(REPO_ROOT, rel)
        scripts_root = os.path.join(REPO_ROOT, "scripts")
        self.assertEqual(
            _layerutil.direction_violations(mod, scripts_root), ["features"],
            "ops/audit.py no longer carries its lazy upward edge — this test "
            "pins the DIFFERENCE between the two analyses and needs a module "
            "that has one")
        self.assertEqual(
            _layerutil.module_level_direction_violations(mod, scripts_root), [],
            "a function-local upward import must NOT be reported: it is the "
            "documented escape hatch, and a call-time edge can neither deadlock "
            "an import nor re-order a package")
        self.assertNotIn(rel, [r[0] for r in mr.upward_imports(REPO_ROOT)])

    def test_the_layer_table_is_the_one_the_manifest_publishes(self):
        # One table, two readers — the same discipline `_corpus` holds for the
        # caps. If these could differ, the manifest would publish a direction
        # nobody enforces.
        with open(os.path.join(REPO_ROOT, "catalog", "modules.json"),
                  encoding="utf-8") as f:
            published = json.load(f)["layer_direction"]
        self.assertEqual(
            published,
            dict((layer, sorted(allowed))
                 for layer, allowed in _layerutil.ALLOWED_BELOW.items()))


class GrabbagNameTest(unittest.TestCase):
    """Claim 2 — no module named for what it is NOT."""

    def test_no_undeclared_dumping_ground_name(self):
        problems, _clean = mr.reconcile()
        over = [p for p in problems if p[0] == mr.OVER
                and p[2].startswith(mr.GRABBAG)]
        self.assertEqual(
            over, [],
            "module(s) named %r — a name that says what the file is NOT has no "
            "boundary, so nothing can ever belong outside it. Name the module "
            "for what it owns: %r" % (sorted(mr.GRABBAG_NAMES), over))

    @unittest.skipIf(_NO_CORE, _NO_CORE_REASON)
    def test_the_lib_itself_carries_no_dumping_ground_name_at_all(self):
        # The absolute arm. Asserted DIRECTLY, with no row subtraction, so the
        # claim cannot be weakened by editing the fixture.
        inside = [rel for rel in mr.grabbag_files(REPO_ROOT)
                  if rel.startswith(mr.LIB_PREFIX)]
        self.assertEqual(
            inside, [],
            "dumping-ground module name(s) inside agentware_lib, where the ban "
            "admits no exemption: %r" % (inside,))

    def test_the_schema_refuses_an_exemption_row_under_the_lib(self):
        # Refutation: prove the loophole is closed rather than merely unused.
        bad = {"kind": mr.GRABBAG,
               "location": "scripts/agentware_lib/ops/utils.py",
               "value": "utils", "owner": "permanent",
               "reason": "synthetic row that must be refused because the lib "
                         "admits no dumping-ground name"}
        errors = mr.schema_errors({"schema": mr.DEBT_SCHEMA, "rows": [bad]})
        self.assertTrue(
            any(mr.LIB_PREFIX in e for e in errors),
            "a grabbag row under the lib must be refused outright: %r" % (errors,))

    def test_a_row_outside_the_lib_is_accepted(self):
        # The other half of the same rule, pinned so the two cannot drift into
        # one: shell has no import system, so a prelude sourced first is a
        # structural position, and shadcn/ui fixes `lib/utils.ts` by generator.
        ok = {"kind": mr.GRABBAG, "location": "scripts/loop/common.sh",
              "value": "common", "owner": "permanent",
              "reason": "a shell prelude sourced first is a position, not a "
                        "leftovers bin, and it is bounded by the 250-line cap"}
        self.assertEqual(
            mr.schema_errors({"schema": mr.DEBT_SCHEMA, "rows": [ok]}), [])


class DeclaredResidualTest(unittest.TestCase):
    """Claim 3 — the residual is well-formed, owned, and shrink-only."""

    def setUp(self):
        self.data = mr.load_debt()

    def test_the_residual_file_schema_is_valid(self):
        self.assertEqual(self.data["schema"], mr.DEBT_SCHEMA)
        self.assertEqual(mr.schema_errors(self.data), [])

    def test_every_row_names_an_owner_and_a_real_reason(self):
        for row in self.data["rows"]:
            self.assertTrue(
                row["owner"] == "permanent" or row["owner"].startswith("task "),
                "row %r must name the plan task that removes it, or be a "
                "justified permanent exemption: %r"
                % (row["location"], row["owner"]))
            self.assertGreater(
                len(row["reason"]), 30,
                "row %r needs a real reason, not a placeholder" % row["location"])

    def test_an_upward_import_may_never_be_declared_permanent(self):
        # The asymmetry between the two kinds, pinned. A NAME can be an
        # ecosystem convention and stay forever; a layer claim is either true or
        # it is false, so its residual is debt with an owner.
        bad = {"kind": mr.UPWARD,
               "location": "scripts/agentware_lib/ops/whatever.py",
               "value": ["features"], "owner": "permanent",
               "reason": "synthetic row that must be refused because an upward "
                         "edge is debt, never an exemption"}
        errors = mr.schema_errors({"schema": mr.DEBT_SCHEMA, "rows": [bad]})
        self.assertTrue(any("PERMANENT" in e for e in errors), errors)

    def test_an_unknown_kind_and_a_duplicate_row_are_refused(self):
        rows = [{"kind": "made_up", "location": "a.py", "value": 1,
                 "owner": "task 100", "reason": "x" * 40},
                {"kind": mr.UPWARD, "location": "b.py", "value": ["ops"],
                 "owner": "task 100", "reason": "y" * 40},
                {"kind": mr.UPWARD, "location": "b.py", "value": ["ops"],
                 "owner": "task 100", "reason": "y" * 40}]
        errors = mr.schema_errors({"schema": mr.DEBT_SCHEMA, "rows": rows})
        self.assertTrue(any("unknown kind" in e for e in errors), errors)
        self.assertTrue(any("duplicate" in e for e in errors), errors)

    def test_a_foreign_schema_is_refused_rather_than_read(self):
        tmp = tempfile.mkdtemp(prefix="aw-modrules-schema-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = _write(tmp, "debt.json", json.dumps({"schema": "something/else@9",
                                                    "rows": []}))
        with self.assertRaises(ValueError):
            mr.load_debt(path)

    def test_the_ratchet_reports_a_worsened_violation(self):
        # A module that already owes ONE upward layer may not quietly acquire a
        # second: the row declares a set, and anything outside it is OVER.
        found = [(mr.UPWARD, "scripts/agentware_lib/ops/tickcmd.py",
                  ["features", "retrieval"])]
        problems, clean = mr.reconcile(found=found, debt=self.data)
        self.assertFalse(clean)
        self.assertTrue(any(p[0] == mr.OVER and "tickcmd" in p[1]
                            for p in problems), problems)

    def test_the_ratchet_reports_a_stale_row(self):
        invented = {"kind": mr.UPWARD,
                    "location": "scripts/agentware_lib/ops/does_not_exist.py",
                    "value": ["features"], "owner": "task 100",
                    "reason": "synthetic row proving the ratchet reports debt "
                              "the tree no longer has"}
        debt = {"schema": mr.DEBT_SCHEMA,
                "rows": self.data["rows"] + [invented]}
        problems, clean = mr.reconcile(debt=debt)
        self.assertFalse(clean)
        self.assertTrue(any(p[0] == mr.STALE and "does_not_exist" in p[1]
                            for p in problems), problems)


class TheRulesBiteTest(unittest.TestCase):
    """Claim 4 — refutation, on scratch trees rather than the live checkout."""

    def test_a_module_level_upward_import_is_reported(self):
        tmp = _scratch_lib(self)
        _write(tmp, "scripts/agentware_lib/features/handler.py",
               MODULE_HEAD + "VALUE = 1\n")
        _write(tmp, "scripts/agentware_lib/core/thing.py",
               MODULE_HEAD +
               "from agentware_lib.features.handler import VALUE\n\n"
               "def go():\n    return VALUE\n")
        found = mr.upward_imports(tmp)
        self.assertEqual(
            found, [("scripts/agentware_lib/core/thing.py", "core",
                     ["features"])],
            "a core module importing features at module level must be reported")

    def test_the_same_import_inside_a_function_is_not_reported(self):
        tmp = _scratch_lib(self)
        _write(tmp, "scripts/agentware_lib/features/handler.py",
               MODULE_HEAD + "VALUE = 1\n")
        _write(tmp, "scripts/agentware_lib/core/thing.py",
               MODULE_HEAD +
               "def go():\n"
               "    from agentware_lib.features.handler import VALUE\n"
               "    return VALUE\n")
        self.assertEqual(mr.upward_imports(tmp), [])

    def test_an_import_in_a_class_body_or_a_try_block_still_counts(self):
        # Both run at import time, so both fix the load order. A rule that
        # skipped them would be trivially bypassable by indenting the import.
        tmp = _scratch_lib(self)
        _write(tmp, "scripts/agentware_lib/features/handler.py",
               MODULE_HEAD + "VALUE = 1\n")
        _write(tmp, "scripts/agentware_lib/core/klass.py",
               MODULE_HEAD +
               "class C(object):\n"
               "    from agentware_lib.features.handler import VALUE\n")
        _write(tmp, "scripts/agentware_lib/core/guarded.py",
               MODULE_HEAD +
               "try:\n"
               "    from agentware_lib.features.handler import VALUE\n"
               "except ImportError:\n"
               "    VALUE = None\n")
        self.assertEqual([r[0] for r in mr.upward_imports(tmp)],
                         ["scripts/agentware_lib/core/guarded.py",
                          "scripts/agentware_lib/core/klass.py"])

    def test_a_downward_import_is_never_reported(self):
        # The rule must be a DIRECTION rule, not an "imports anything" rule:
        # features -> core is the whole point of having layers.
        tmp = _scratch_lib(self)
        _write(tmp, "scripts/agentware_lib/core/thing.py",
               MODULE_HEAD + "VALUE = 1\n")
        _write(tmp, "scripts/agentware_lib/features/handler.py",
               MODULE_HEAD + "from agentware_lib.core.thing import VALUE\n")
        self.assertEqual(mr.upward_imports(tmp), [])

    def test_a_relative_upward_import_is_reported_too(self):
        # `from ...features import handler` resolves to the same edge; a rule
        # that only understood absolute imports would be bypassable by writing
        # the import the other way.
        tmp = _scratch_lib(self)
        _write(tmp, "scripts/agentware_lib/features/handler.py",
               MODULE_HEAD + "VALUE = 1\n")
        _write(tmp, "scripts/agentware_lib/core/rel.py",
               MODULE_HEAD + "from ..features.handler import VALUE\n")
        self.assertEqual([r[0] for r in mr.upward_imports(tmp)],
                         ["scripts/agentware_lib/core/rel.py"])

    def test_a_dumping_ground_name_is_reported_in_every_layer(self):
        # One shape per subsystem the task-67 corpus widened to, because the
        # name rule is worth nothing if it only reaches the lib.
        tmp = _scratch_lib(self)
        planted = ("scripts/agentware_lib/ops/utils.py",
                   "scripts/loop/misc.sh",
                   "scripts/hooks/helpers.sh",
                   "tests/gates/test_shared.py",
                   "webui/src/panels/common.ts")
        for rel in planted:
            _write(tmp, rel, "# scratch\n")
        self.assertEqual(mr.grabbag_files(tmp), sorted(planted))

    def test_a_domain_name_is_not_reported(self):
        tmp = _scratch_lib(self)
        for rel in ("scripts/agentware_lib/ops/pathfmt.py",
                    "scripts/loop/kbsync.sh",
                    "webui/src/lib/formatters.ts"):
            _write(tmp, rel, "# scratch\n")
        self.assertEqual(mr.grabbag_files(tmp), [])

    def test_a_fixture_file_is_not_a_dumping_ground(self):
        # Fixture DATA is outside the corpus by a declared exclusion, so a
        # fixture named utils.py is not a module at all. Without this, the rule
        # would redden on data nobody imports.
        tmp = _scratch_lib(self)
        _write(tmp, "tests/fixtures/utils.py", "# fixture data\n")
        self.assertEqual(mr.grabbag_files(tmp), [])

    def test_the_scan_is_not_vacuously_empty(self):
        # The failure mode that passes every other test here: an enumerator
        # rooted at the wrong directory measures nothing and reports nothing.
        self.assertGreater(
            len(mr.lib_modules(REPO_ROOT)), 150,
            "the lib enumerator found too few modules — it is probably rooted "
            "at the wrong directory")
        self.assertGreater(len(_corpus.corpus_files(REPO_ROOT)), 400)


if __name__ == "__main__":
    unittest.main()
