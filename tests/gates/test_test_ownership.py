"""The test-corpus OWNERSHIP rule (plan task 71) — one module, one owning file.

`test_test_layout.py` asserts the corpus has the right SHAPE (no flat file,
every file in a declared tier, none over the line cap). `test_module_manifest.py`
asserts every file on disk is CLAIMED. Neither asks the question this file asks:
when a module breaks, is there a test file whose subject IS that module?

The donor answered it as `test_module_boundaries` M11 and forgave the answer
with a carve-out — a module also counted as owned when any flat white-box
`tests/test_*.py` referenced one of its exports. That branch makes the rule
unfalsifiable in the only direction that matters, because a file reaching
symbols through the CLI facade references every module in the lib. Task 71 ports
the rule without it, so ownership is a LOCATION claim decided by one
`os.path.isfile`, and this file proves the retirement rather than asserting it.

Five claims:

  1. LIVE — no lib module is testless at an undeclared location, and no declared
     row is stale. The ratchet only shrinks.
  2. FIXTURE — the residual is well-formed: every row is owned by a task (never
     `permanent`), names a real lib module, aims at the path that would actually
     have owned it, and the counter matches the rows AND the ceiling pinned by
     value HERE, so a new testless module cannot be legalised by one edit.
  3. TWO WITNESSES — the committed `catalog/modules.json` `test_file` /
     `test_file_present` columns agree with the rule, module for module. The
     published contract and the gate that enforces it cannot drift.
  4. THE CARVE-OUT IS GONE — the three shapes it forgave (a flat
     `tests/test_<stem>.py`, a non-unit tier, a same-tier file under another
     name), each importing the module and referencing EVERY one of its anchor
     symbols, all leave the module testless. The positive control in the same
     scratch tree proves that is not because ownership is unreachable.
  5. THE CHECKS BITE — undeclared, stale and every schema refusal are each
     reddened on a scratch tree.

Stdlib `unittest` only. The live checks read the real tree; every refutation
builds its own tree under `tempfile` and never writes into the checkout.
"""

from tests._paths import REPO_ROOT, TESTS_DIR

import json
import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests import _manifest_gen
    from tests import _test_ownership as to
except ImportError:  # allow a bare-name run from inside tests/
    sys.path.insert(0, TESTS_DIR)
    import _manifest_gen
    import _test_ownership as to

_NO_CORE = not to.lib_present(REPO_ROOT)
_NO_CORE_REASON = "scripts/agentware_lib is not present in this tree yet"

#: The ceiling the fixture's counter may not exceed, pinned BY VALUE so that
#: declaring a new testless module takes an edit here as well as in the fixture.
#: It may only ever be lowered, in the same commit that pays rows down.
OWNERSHIP_DEBT_CEILING = 174

MODULE_BODY = ('"""Owns: one scratch behaviour.\n\n'
               'Does NOT own: anything real.\n\n'
               'Layer deps: none.\n"""\n\n\n'
               'def scratch_behaviour():\n'
               '    return 1\n\n\n'
               'class ScratchThing(object):\n'
               '    pass\n')

#: Every anchor symbol MODULE_BODY declares — a carve-out shape must reference
#: all of them, so the refutation is strictly stronger than the donor's `&`.
MODULE_ANCHORS = ("ScratchThing", "scratch_behaviour")


def _write(root, rel, body):
    path = os.path.join(root, rel.replace("/", os.sep))
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def _scratch_tree(testcase, layer="ops", stem="scratchmod"):
    """A tree with one real lib module and the unit tiers, cleaned up after."""
    tmp = tempfile.mkdtemp(prefix="aw-ownership-")
    testcase.addCleanup(shutil.rmtree, tmp, True)
    _write(tmp, "scripts/agentware_lib/__init__.py", "")
    _write(tmp, "scripts/agentware_lib/%s/__init__.py" % layer, "")
    _write(tmp, "scripts/agentware_lib/%s/%s.py" % (layer, stem), MODULE_BODY)
    for tier in ("unit/core", "unit/ops", "integration", "e2e", "gates"):
        _write(tmp, "tests/%s/__init__.py" % tier, "")
    return tmp


def _claimant(module_dotted):
    """A test file that does everything the retired carve-out asked for: it
    imports the module by name and references every anchor symbol it owns."""
    return ('"""A white-box file claiming ownership it may not have."""\n'
            'from %s import ScratchThing, scratch_behaviour\n\n\n'
            'class ClaimTest(object):\n'
            '    def test_it(self):\n'
            '        assert scratch_behaviour() == 1\n'
            '        assert ScratchThing is not None\n' % module_dotted)


def _row(location, value, owner="task 101", reason="scratch"):
    return {"kind": to.TESTLESS, "location": location, "value": value,
            "owner": owner, "reason": reason}


def _debt(rows):
    return {"schema": to.DEBT_SCHEMA, "rows": rows,
            "ratchet": {"max_rows": len(rows)}}


@unittest.skipIf(_NO_CORE, _NO_CORE_REASON)
class LiveOwnershipTest(unittest.TestCase):
    """Claim 1 — the real tree, measured against the declared residual."""

    def test_no_module_is_testless_at_an_undeclared_location(self):
        problems, _clean = to.reconcile()
        over = [p for p in problems if p[0] == to.OVER]
        self.assertEqual(
            over, [],
            "lib module(s) with no owning granular test and no declared row. "
            "Every module is owned by exactly one file, "
            "tests/unit/<layer>/test_<stem>.py — write it. A test elsewhere "
            "that touches the module does NOT own it (plan task 71 retired "
            "that carve-out): %r" % (over,))

    def test_no_declared_row_is_stale(self):
        problems, _clean = to.reconcile()
        stale = [p for p in problems if p[0] == to.STALE]
        self.assertEqual(
            stale, [],
            "declared row(s) the tree no longer produces — delete them and "
            "decrement ratchet.max_rows, this file only shrinks: %r" % (stale,))

    def test_the_rule_measures_a_real_corpus(self):
        """Non-vacuity: every claim above is true of an empty lib."""
        modules = to.lib_modules()
        self.assertGreater(len(modules), 150)
        self.assertTrue(os.path.isdir(os.path.join(REPO_ROOT, "tests", "unit")))
        owned = [rel for rel, own in to.ownership_map() if own]
        self.assertTrue(owned, "no module in the tree is owned by any file — "
                               "the rule's positive branch is never exercised")


@unittest.skipIf(_NO_CORE, _NO_CORE_REASON)
class DeclaredResidualTest(unittest.TestCase):
    """Claim 2 — the fixture is well-formed, owned, and doubly counted."""

    def setUp(self):
        self.data = to.load_debt()

    def test_the_fixture_has_no_schema_errors(self):
        self.assertEqual(to.schema_errors(self.data), [])

    def test_every_row_names_an_owning_task(self):
        bad = [r["location"] for r in self.data["rows"]
               if not to._owner_ok(r.get("owner"))]
        self.assertEqual(bad, [], "row(s) without an owning 'task <n>': %r" % bad)

    def test_no_row_is_a_permanent_exemption(self):
        # The absolute half of the rule. An upward import may not be permanent
        # because a layer claim is either true or false; a testless module may
        # not be permanent for the same kind of reason — "this will never have a
        # test" is an argument for MOVING the code, not for exempting it.
        permanent = [r["location"] for r in self.data["rows"]
                     if r.get("owner") == "permanent"]
        self.assertEqual(permanent, [], "%r" % permanent)

    def test_the_counter_matches_the_rows(self):
        self.assertEqual(self.data["ratchet"].get("max_rows"),
                         len(self.data["rows"]))

    def test_the_counter_is_within_the_pinned_ceiling(self):
        self.assertLessEqual(
            self.data["ratchet"].get("max_rows"), OWNERSHIP_DEBT_CEILING,
            "the residual GREW. A new module must ship its granular unit file; "
            "this ceiling may only be lowered, and only in the commit that pays "
            "the rows down.")

    def test_every_row_is_live(self):
        """The rows describe THIS tree, not a photograph of an older one."""
        self.assertEqual(sorted(r["location"] for r in self.data["rows"]),
                         to.testless_modules())


@unittest.skipIf(_NO_CORE, _NO_CORE_REASON)
class ManifestAgreementTest(unittest.TestCase):
    """Claim 3 — the published column and the enforcing rule are one answer."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "catalog", "modules.json"),
                  encoding="utf-8") as fh:
            self.manifest = json.load(fh)

    def test_every_manifest_row_agrees_with_the_rule(self):
        disagree = []
        for entry in self.manifest["modules"]:
            mod = os.path.join(REPO_ROOT, entry["path"].replace("/", os.sep))
            want = to.granular_test_rel(mod)
            owned = bool(to.owners(mod))
            if entry["test_file"] != want or entry["test_file_present"] != owned:
                disagree.append((entry["path"], entry["test_file"], want,
                                 entry["test_file_present"], owned))
        self.assertEqual(disagree, [],
                         "manifest test_file/test_file_present disagrees with "
                         "the ownership rule: %r" % (disagree,))

    def test_the_manifest_and_the_rule_see_the_same_modules(self):
        self.assertEqual(
            sorted(e["path"] for e in self.manifest["modules"]),
            sorted(to._rel(m, REPO_ROOT) for m in to.lib_modules()))


class CarveOutRetiredTest(unittest.TestCase):
    """Claim 4 — the three shapes the donor's M11 forgave own nothing now."""

    def setUp(self):
        self.root = _scratch_tree(self)
        self.mod = os.path.join(self.root, "scripts", "agentware_lib", "ops",
                                "scratchmod.py")
        self.rel = "scripts/agentware_lib/ops/scratchmod.py"
        self.claim = _claimant("agentware_lib.ops.scratchmod")

    def _testless(self):
        return [loc for _kind, loc, _value in to.scan(self.root)]

    def test_the_scratch_module_declares_the_anchors_the_claimants_use(self):
        # Guards the refutations below: if the module stopped exporting these,
        # every carve-out shape would "fail to own" it for the wrong reason.
        self.assertEqual(tuple(_manifest_gen.anchor_symbols(self.mod)),
                         MODULE_ANCHORS)

    def test_a_flat_white_box_test_does_not_own_the_module(self):
        _write(self.root, "tests/test_scratchmod.py", self.claim)
        self.assertEqual(self._testless(), [self.rel])
        self.assertEqual(to.owners(self.mod, self.root), [])

    def test_a_non_unit_tier_does_not_own_the_module(self):
        for tier in ("integration", "e2e", "gates"):
            _write(self.root, "tests/%s/test_scratchmod.py" % tier, self.claim)
        self.assertEqual(self._testless(), [self.rel])

    def test_the_same_tier_under_another_name_does_not_own_the_module(self):
        # The subtlest shape, and the one this tree is actually full of: a unit
        # file in the right layer, exercising the module, named for the
        # BEHAVIOUR instead of the module.
        _write(self.root, "tests/unit/ops/test_some_behaviour.py", self.claim)
        self.assertEqual(self._testless(), [self.rel])

    def test_another_layers_unit_file_does_not_own_the_module(self):
        _write(self.root, "tests/unit/core/test_scratchmod.py", self.claim)
        self.assertEqual(self._testless(), [self.rel])

    def test_the_granular_file_does_own_the_module(self):
        """The positive control: ownership is reachable in this same tree, so
        the four refutations above are not green because nothing can own."""
        _write(self.root, "tests/unit/ops/test_scratchmod.py", self.claim)
        self.assertEqual(self._testless(), [])
        self.assertEqual(to.owners(self.mod, self.root),
                         ["tests/unit/ops/test_scratchmod.py"])

    def test_a_private_module_maps_to_the_underscore_stripped_name(self):
        _write(self.root, "scripts/agentware_lib/ops/_private.py", MODULE_BODY)
        mod = os.path.join(self.root, "scripts", "agentware_lib", "ops",
                           "_private.py")
        self.assertEqual(to.granular_test_rel(mod, self.root),
                         "tests/unit/ops/test_private.py")

    def test_the_references_report_is_advice_and_not_ownership(self):
        _write(self.root, "tests/unit/ops/test_some_behaviour.py", self.claim)
        refs = to.granular_references(self.root)
        self.assertEqual(refs[self.rel],
                         ["tests/unit/ops/test_some_behaviour.py"])
        self.assertEqual(to.owners(self.mod, self.root), [])


class RatchetBitesTest(unittest.TestCase):
    """Claim 5 — undeclared, stale and every schema refusal redden."""

    def setUp(self):
        self.root = _scratch_tree(self)
        self.rel = "scripts/agentware_lib/ops/scratchmod.py"
        self.want = "tests/unit/ops/test_scratchmod.py"

    def test_an_undeclared_testless_module_is_over_debt(self):
        problems, clean = to.reconcile(debt=_debt([]), root=self.root)
        self.assertFalse(clean)
        self.assertEqual([(p[0], p[1]) for p in problems],
                         [(to.OVER, self.rel)])

    def test_a_declared_testless_module_is_clean(self):
        _problems, clean = to.reconcile(
            debt=_debt([_row(self.rel, self.want)]), root=self.root)
        self.assertTrue(clean)

    def test_a_row_whose_module_gained_its_test_is_stale(self):
        _write(self.root, self.want, "# owned now\n")
        problems, clean = to.reconcile(
            debt=_debt([_row(self.rel, self.want)]), root=self.root)
        self.assertFalse(clean)
        self.assertEqual([(p[0], p[1]) for p in problems],
                         [(to.STALE, self.rel)])

    def test_a_permanent_owner_is_refused(self):
        errors = to.schema_errors(
            _debt([_row(self.rel, self.want, owner="permanent")]), self.root)
        self.assertTrue([e for e in errors if "owner must be" in e], errors)

    def test_a_row_aimed_at_the_wrong_file_is_refused(self):
        errors = to.schema_errors(
            _debt([_row(self.rel, "tests/integration/test_scratchmod.py")]),
            self.root)
        self.assertTrue([e for e in errors if "may not aim at" in e], errors)

    def test_a_row_outside_the_lib_is_refused(self):
        errors = to.schema_errors(
            _debt([_row("scripts/agentware_dashboard/routes.py",
                        "tests/unit/dashboard/test_routes.py")]), self.root)
        self.assertTrue([e for e in errors if "outside" in e], errors)

    def test_a_row_naming_no_module_on_disk_is_refused(self):
        errors = to.schema_errors(
            _debt([_row("scripts/agentware_lib/ops/ghost.py",
                        "tests/unit/ops/test_ghost.py")]), self.root)
        self.assertTrue([e for e in errors if "no module on disk" in e], errors)

    def test_a_duplicate_row_is_refused(self):
        rows = [_row(self.rel, self.want), _row(self.rel, self.want)]
        errors = to.schema_errors(_debt(rows), self.root)
        self.assertTrue([e for e in errors if "duplicate row" in e], errors)

    def test_a_counter_that_disagrees_with_the_rows_is_refused(self):
        data = _debt([_row(self.rel, self.want)])
        data["ratchet"]["max_rows"] = 7
        errors = to.schema_errors(data, self.root)
        self.assertTrue([e for e in errors if "max_rows" in e], errors)

    def test_a_missing_field_is_refused(self):
        row = _row(self.rel, self.want)
        del row["reason"]
        errors = to.schema_errors(_debt([row]), self.root)
        self.assertTrue([e for e in errors if "missing 'reason'" in e], errors)

    def test_a_foreign_schema_is_refused(self):
        path = _write(self.root, "debt.json",
                      json.dumps({"schema": "something/else@1", "rows": []}))
        self.assertRaises(ValueError, to.load_debt, path)

    def test_groups_flatten_into_rows_carrying_the_group_fields(self):
        path = _write(self.root, "debt.json", json.dumps({
            "schema": to.DEBT_SCHEMA, "rows": [], "ratchet": {"max_rows": 1},
            "groups": [{"owner": "task 101", "reason": "because",
                        "rows": [{"kind": to.TESTLESS, "location": self.rel,
                                  "value": self.want}]}]}))
        data = to.load_debt(path)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["owner"], "task 101")
        self.assertEqual(data["rows"][0]["reason"], "because")
        self.assertEqual(to.schema_errors(data, self.root), [])

    def test_an_empty_lib_produces_no_findings_rather_than_guessing(self):
        empty = tempfile.mkdtemp(prefix="aw-ownership-empty-")
        self.addCleanup(shutil.rmtree, empty, True)
        self.assertEqual(to.scan(empty), [])
        self.assertFalse(to.lib_present(empty))


if __name__ == "__main__":
    unittest.main()
