"""The modularity CEILING suite (plan task 21).

Two claims, deliberately separated so they can never be confused for each other:

  1. POSITIVE — the four packages task 21 split are FULLY compliant: no file over
     600 lines, no function over 120 lines or 12 branches. This is the task's own
     acceptance criterion and it is asserted directly, with no debt subtraction
     available, so the split cannot regress quietly.
  2. RATCHET — every OTHER ceiling violation in the tree is declared in
     `tests/fixtures/ceiling-debt.json`. Task 67 widened the manifest and the
     scan tree-wide and added exactly these caps, measuring 151 rows across the
     lib, the hooks, the webui and the test corpus itself; task 99 ADJUDICATED
     every one of them. Declaring the residual keeps the gate GREEN and
     therefore meaningful: a new violation arriving tomorrow reddens it, instead
     of drowning in a permanently red wall.

     What task 99 changed is what a row MEANS. Under schema 1 a row named the
     task that would remove it, and once that task closed the row was a dangling
     pointer nobody was looking for -- which is how a ratchet rots into an
     allowlist. Under schema 2 a row is a PERMANENT EXEMPTION naming the
     invariant its count encodes, `owner` is refused outright, and the row count
     is pinned by value below. The rows that had no invariant to name were not
     re-labelled: they were paid down, and they are gone.

The branch metric lives ONCE, in `tests/_ceiling_gate.py`. This file imports it
rather than restating it, so the suite and the gate can never disagree about what
"12 branches" means.

Stdlib only, deterministic, hermetic (reads the tree with `ast`; no imports of
the code under test, no subprocess, no network).
"""

from tests._paths import TESTS_DIR

import ast
import os
import sys
import unittest

try:
    from tests import _ceiling_gate as cg
    from tests import _corpus
except ImportError:  # allow `python3 -m unittest tests.gates.test_module_ceilings`
    sys.path.insert(0, TESTS_DIR)
    import _ceiling_gate as cg
    import _corpus

# The gate rides with the migration: before the modular core lands there is no
# `agentware_lib` to measure, so the whole suite is inert rather than reporting
# every declared row as stale. It arms itself the moment the core appears.
_NO_CORE = not cg.lib_present()
_NO_CORE_REASON = "scripts/agentware_lib is not present in this tree yet"

#: The population ceiling for the whole ledger, pinned BY VALUE so that
#: admitting a NEW row costs a deliberate edit here as well as in the fixture's
#: own `ratchet.max_rows`. It may only ever be lowered, in the commit that pays
#: the rows down. Task 67 measured 151; task 99 adjudicated them, wave 1 paid
#: five down and wave 2 paid the twelve of the steering-capture family.
ROW_CEILING = 127

#: The ceiling on the PAYDOWN half specifically. This is the number that has to
#: reach zero: when it does, every remaining row is a decision that stands, and
#: no row anywhere names a task at all. It only falls.
PAYDOWN_CEILING = 97

#: A syntactically valid `invariant` for the synthetic rows below. It says what
#: BREAKS if the branches are collapsed, which is the whole obligation.
_VALID_INVARIANT = (
    "its branches are the arms of one closed verdict table, and collapsing any "
    "pair of them is exactly the defect the table exists to prevent")

#: The paydown counterpart: a seam names the helpers and the resulting count.
_VALID_SEAM = (
    "extract `_render_one(row)` and `_collect(rows)`, the two obligations this "
    "body fused, leaving a three-call spine; parent 20 -> 4")


def _row(kind, location, value, reason, invariant=_VALID_INVARIANT):
    """A schema-2 EXEMPT row, built in one place.

    Every refusal test below plants a row that is valid EXCEPT for the one thing
    under test — otherwise a test meant to prove the lib file cap is refused
    could pass because the row was missing an invariant instead.
    """
    return {"kind": kind, "location": location, "value": value,
            "reason": reason, "verdict": cg.EXEMPT, "invariant": invariant}


def _paydown_row(kind, location, value, reason, seam=_VALID_SEAM,
                 owner="task 99"):
    """A schema-2 PAYDOWN row — open work, with an owner and a named seam."""
    return {"kind": kind, "location": location, "value": value,
            "reason": reason, "verdict": cg.PAYDOWN, "owner": owner,
            "seam": seam}


class BranchMetricTest(unittest.TestCase):
    """The metric is the contract — pin it by example, not by prose."""

    @staticmethod
    def _count(src):
        fn = ast.parse(src).body[0]
        return cg.branch_count(fn)

    def test_simple_forks_count_once(self):
        self.assertEqual(self._count("def f(x):\n    if x: return 1\n    return 0\n"), 1)
        self.assertEqual(self._count("def f(x):\n    for i in x: pass\n"), 1)
        self.assertEqual(self._count("def f(x):\n    while x: pass\n"), 1)

    def test_else_is_not_a_second_decision(self):
        # `else` is the same fork as its `if`, already counted.
        self.assertEqual(
            self._count("def f(x):\n    if x:\n        pass\n    else:\n        pass\n"), 1)

    def test_elif_is_a_new_decision(self):
        self.assertEqual(
            self._count("def f(x):\n    if x:\n        pass\n    elif x == 2:\n        pass\n"), 2)

    def test_each_extra_boolean_operand_is_a_decision(self):
        # `a and b and c` is TWO decisions, not one.
        self.assertEqual(self._count("def f(a,b,c):\n    return a and b and c\n"), 2)

    def test_each_except_handler_counts_but_try_does_not(self):
        src = ("def f():\n    try:\n        pass\n    except KeyError:\n        pass\n"
               "    except ValueError:\n        pass\n")
        self.assertEqual(self._count(src), 2)

    def test_with_and_assert_are_not_branches(self):
        self.assertEqual(self._count("def f(c):\n    with c:\n        pass\n"), 0)
        self.assertEqual(self._count("def f(x):\n    assert x\n"), 0)

    def test_comprehension_filters_count(self):
        self.assertEqual(self._count("def f(x):\n    return [i for i in x if i if i > 2]\n"), 2)

    def test_nested_function_branches_are_not_doubled(self):
        # A closure is its own unit: factoring work INTO a helper must not make
        # the parent look worse, or the cap would punish the very refactor it
        # exists to encourage.
        src = ("def outer(x):\n"
               "    def inner(y):\n"
               "        if y: return 1\n"
               "        return 0\n"
               "    return inner(x)\n")
        self.assertEqual(self._count(src), 0)


@unittest.skipIf(_NO_CORE, _NO_CORE_REASON)
class LibFileCapIsSatisfiedOutrightTest(unittest.TestCase):
    """Claim 1a — the FILE cap, tree-wide, with NO debt subtraction available.

    This is task 21's own acceptance criterion ("no file under the lib exceeds
    600 lines") and after round 2 it is true of every file, not just the split
    packages. Asserting it directly here — rather than letting `reconcile`
    subtract declared rows — is what turns the cap from a ratchet into an
    invariant: there is no arrangement of the debt fixture that makes an
    over-cap file acceptable, because `schema_errors` refuses the row kind
    entirely and this test never consults the fixture at all.
    """

    def test_no_lib_file_exceeds_the_600_line_cap(self):
        # `scan()` is tree-wide after task 67, so the lib slice is taken
        # explicitly. The claim is still absolute WHERE IT IS ABSOLUTE: no
        # arrangement of the debt fixture can forgive a lib file, because
        # `schema_errors` refuses the row kind under the lib prefix.
        over = [(r[2], r[1]) for r in cg.scan_lib() if r[0] == cg.FILE_OVER]
        self.assertEqual(
            sorted(over, reverse=True), [],
            "file(s) over the %d-line agentware_lib cap, with no debt available "
            "— split the file into a package (see the twelve task-21 splits for "
            "the shape): %r" % (cg.LIB_FILE_CAP, sorted(over, reverse=True)))

    def test_the_debt_schema_refuses_a_file_over_cap_row(self):
        # Refutation: prove the loophole is actually closed, rather than merely
        # unused today. A later task must not be able to legalise a new monolith
        # by declaring it.
        bad = _row(cg.FILE_OVER,
                   "scripts/agentware_lib/ops/some_new_monolith.py", 4000,
                   "synthetic row that must be refused because the lib file "
                   "cap admits no forgiveness")
        errors = cg.schema_errors({"schema": cg.DEBT_SCHEMA, "rows": [bad]})
        self.assertTrue(
            any(cg.FILE_OVER in e for e in errors),
            "a file_over_cap debt row under the lib must be refused outright: "
            "%r" % (errors,))

    def test_a_file_over_cap_row_outside_the_lib_is_accepted(self):
        # The other half of the same rule, pinned so the two cannot drift into
        # one. Task 67's widening measured ten files already over a cap on the
        # day it first applied to them, in subsystems whose decomposition the
        # plan assigns elsewhere (task 69 took the dashboard, which is why the
        # example row moved off it; task 99 owns the rest). Refusing the row
        # would not split those files; it would only
        # force the cap down until the tree fit, which is the failure the
        # ratchet exists to prevent.
        ok = _row(cg.FILE_OVER, "scripts/hooks/codex-stream.py", 900,
                  "an over-cap file outside the lib, which is what a "
                  "declarable row looks like")
        errors = cg.schema_errors({"schema": cg.DEBT_SCHEMA, "rows": [ok]})
        self.assertEqual(
            errors, [],
            "a file_over_cap row OUTSIDE agentware_lib must be declarable: %r"
            % (errors,))


@unittest.skipIf(_NO_CORE, _NO_CORE_REASON)
class Task21SplitPackagesAreCompliantTest(unittest.TestCase):
    """Claim 1b — the POSITIVE assertion, with no debt subtraction available."""

    # Every flat module task 21 retired, in both rounds. The split is only real
    # if the flat module was RETIRED, not copied alongside — a leftover flat
    # module keeps serving every old import and the package becomes dead code
    # that no gate exercises.
    RETIRED_FLAT_MODULES = (
        # round 1 — the four the plan names
        "scripts/agentware_lib/core/config.py",
        "scripts/agentware_lib/features/configcmd.py",
        "scripts/agentware_lib/features/dreamsteps.py",
        "scripts/agentware_lib/knowledge/index.py",
        # round 2 — the eight remaining over-cap files
        "scripts/agentware_lib/features/fanoutmq.py",
        "scripts/agentware_lib/features/proposals.py",
        "scripts/agentware_lib/knowledge/curation.py",
        "scripts/agentware_lib/ops/fleet.py",
        "scripts/agentware_lib/ops/metrics.py",
        "scripts/agentware_lib/ops/planauthor.py",
        "scripts/agentware_lib/ops/sessionparse.py",
        "scripts/agentware_lib/retrieval/bench.py",
    )

    def test_round1_packages_have_no_ceiling_violation_of_any_kind(self):
        offenders = [r for r in cg.scan()
                     if r[1].startswith(cg.TASK21_CLEAN_PREFIXES)]
        self.assertEqual(
            offenders, [],
            "the four packages task 21 names must be fully within the ceilings "
            "(600 lines/file, 120 lines + 12 branches/function); over: %r"
            % (offenders,))

    def test_the_pre_split_monoliths_are_gone(self):
        for rel in self.RETIRED_FLAT_MODULES:
            self.assertFalse(
                os.path.isfile(os.path.join(cg.REPO_ROOT, rel)),
                "%s still exists — the package split left the flat module in "
                "place, so the package is unreachable dead code" % rel)

    def test_every_split_package_is_a_real_package(self):
        for prefix in cg.TASK21_CLEAN_PREFIXES + cg.TASK21_SPLIT_PREFIXES:
            init = os.path.join(cg.REPO_ROOT, prefix, "__init__.py")
            self.assertTrue(
                os.path.isfile(init),
                "%s has no __init__.py — py3.7 has no namespace-package support "
                "under unittest discover, so the layer silently vanishes" % prefix)

    def test_every_retired_module_has_a_package_in_its_place(self):
        # The two halves must agree: a flat module that vanished without a
        # package replacing it is a DELETION, not a split, and would take its
        # whole verb surface with it.
        for rel in self.RETIRED_FLAT_MODULES:
            pkg = os.path.join(cg.REPO_ROOT, rel[:-len(".py")])
            self.assertTrue(
                os.path.isdir(pkg),
                "%s was retired but no package took its place — that is a "
                "deletion, not a decomposition" % rel)

    def test_round2_packages_are_within_the_file_cap(self):
        over = [r for r in cg.scan()
                if r[0] == cg.FILE_OVER
                and r[1].startswith(cg.TASK21_SPLIT_PREFIXES)]
        self.assertEqual(
            over, [],
            "a round-2 split left a file over the cap — splitting a 677-line "
            "module into a 610-line part and a stub is not a split: %r" % (over,))


@unittest.skipIf(_NO_CORE, _NO_CORE_REASON)
class CeilingDebtRatchetTest(unittest.TestCase):
    """Claim 2 — the residual is declared, shrink-only, and owned."""

    def test_debt_file_schema_is_valid(self):
        data = cg.load_debt()
        self.assertEqual(data["schema"], cg.DEBT_SCHEMA)
        self.assertEqual(cg.schema_errors(data), [])

    def test_every_row_carries_a_verdict_and_its_justification(self):
        """The task-99 claim: no row is declared without a decision.

        Schema 1 let a row say only "task 67 owns this", and 151 of them did,
        long after task 67 closed. Here every row must be adjudicated: an
        `exempt` row names the invariant its count encodes, a `paydown` row
        names the seam that removes it and who is cutting it.
        """
        for row in cg.load_debt()["rows"]:
            loc = row["location"]
            self.assertIn(row.get("verdict"), cg.VERDICTS,
                          "row %r carries no verdict" % loc)
            self.assertGreater(
                len(row["reason"]), 30,
                "row %r needs a real reason, not a placeholder" % loc)
            if row["verdict"] == cg.EXEMPT:
                self.assertNotIn("owner", row,
                                 "exempt row %r must not name an owner" % loc)
                self.assertEqual(cg.invariant_errors(loc, row.get("invariant")),
                                 [], "row %r" % loc)
            else:
                self.assertTrue(row.get("owner", "").startswith("task "),
                                "paydown row %r names no owning task" % loc)
                self.assertGreaterEqual(
                    len(row.get("seam") or ""), cg.JUSTIFICATION_MIN_CHARS,
                    "paydown row %r has no usable seam" % loc)

    def test_the_counters_match_the_rows_and_stay_within_their_ceilings(self):
        data = cg.load_debt()
        rows = data["rows"]
        paydown = [r for r in rows if r["verdict"] == cg.PAYDOWN]
        self.assertEqual(data["ratchet"]["max_rows"], len(rows))
        self.assertEqual(data["ratchet"]["max_paydown_rows"], len(paydown))
        self.assertLessEqual(
            len(rows), ROW_CEILING,
            "the ledger GREW. A new ceiling violation must be split, not "
            "declared; this ceiling may only be lowered, and only in the commit "
            "that pays the rows down.")
        self.assertLessEqual(
            len(paydown), PAYDOWN_CEILING,
            "the PAYDOWN half grew. Every row here is open work with a named "
            "seam, and the number only falls — when it reaches zero, no row in "
            "this ledger names a task at all.")

    def test_a_row_with_no_verdict_is_refused(self):
        # The schema-1 shape, planted verbatim: kind, location, value, owner,
        # reason and nothing else. It was legal for 151 rows; it is now the one
        # thing the schema exists to refuse.
        bare = {"kind": cg.FUNC_BRANCHES,
                "location": "scripts/agentware_lib/ops/audit.py::cmd_audit",
                "value": 13, "owner": "task 67",
                "reason": "pre-existing complexity inherited with the donor "
                          "tree, in subsystems task 21 does not touch"}
        errors = cg.schema_errors({"schema": cg.DEBT_SCHEMA, "rows": [bare]})
        self.assertTrue(any("verdict" in e for e in errors),
                        "a row with no verdict must be refused: %r" % (errors,))

    def test_an_exempt_row_may_not_carry_an_owner(self):
        # This is the mechanical form of "no row names a closed task as its
        # owner". The gate cannot check whether a task is still open — the plan
        # lives outside the repo — so the answer is that a decision HAS no
        # owner, and a row that needs one is paydown instead.
        row = _row(cg.FUNC_BRANCHES,
                   "scripts/agentware_lib/ops/audit.py::cmd_audit", 13,
                   "a decision that stands, wrongly given an owner")
        row["owner"] = "task 67"
        errors = cg.schema_errors({"schema": cg.DEBT_SCHEMA, "rows": [row]})
        self.assertTrue(any("owner" in e for e in errors),
                        "an exempt row with an owner must be refused: %r"
                        % (errors,))

    def test_an_invariant_written_as_provenance_is_refused(self):
        # The rubber stamp this field exists to prevent: every word of it is
        # true, none of it can be checked against the body, and all of it stays
        # true no matter how the code changes.
        row = _row(cg.FUNC_BRANCHES,
                   "scripts/agentware_lib/ops/audit.py::cmd_audit", 13,
                   "a real reason lives here",
                   invariant="pre-existing complexity inherited with the donor "
                             "tree and moved here verbatim by a port; splitting "
                             "it now would be risky and is out of scope")
        errors = cg.schema_errors({"schema": cg.DEBT_SCHEMA, "rows": [row]})
        self.assertTrue(
            any("provenance" in e for e in errors),
            "an invariant made of provenance language must be refused: %r"
            % (errors,))

    def test_a_paydown_row_needs_both_an_owner_and_a_seam(self):
        for drop in ("owner", "seam"):
            row = _paydown_row(cg.FUNC_BRANCHES,
                               "scripts/agentware_lib/ops/audit.py::cmd_audit",
                               13, "open work, wrongly declared")
            del row[drop]
            errors = cg.schema_errors({"schema": cg.DEBT_SCHEMA, "rows": [row]})
            self.assertTrue(errors,
                            "a paydown row with no %s must be refused" % drop)

    def test_the_paydown_counter_must_be_lowered_when_a_row_is_paid(self):
        # The two-key move: dropping a paydown row without lowering the counter
        # is the edit that would let the group quietly refill later.
        data = cg.load_debt()
        rows = [r for r in data["rows"] if r["verdict"] == cg.PAYDOWN]
        stale = {"schema": cg.DEBT_SCHEMA, "rows": rows[1:],
                 "ratchet": {"max_rows": len(rows) - 1,
                             "max_paydown_rows": len(rows)}}
        errors = cg.schema_errors(stale)
        self.assertTrue(any("max_paydown_rows" in e for e in errors),
                        "%r" % (errors,))

    def test_live_violations_match_the_declared_debt(self):
        problems, clean = cg.reconcile()
        over = [p for p in problems if p[0] == cg.OVER]
        stale = [p for p in problems if p[0] == cg.STALE]
        self.assertEqual(
            over, [],
            "UNDECLARED or WORSENED ceiling violation(s) — a new monolith may "
            "not hide behind the declared residual: %r" % (over,))
        self.assertEqual(
            stale, [],
            "STALE debt row(s): the tree no longer has these violations, so the "
            "rows must be deleted (this file only shrinks): %r" % (stale,))
        self.assertTrue(clean)

    def test_ratchet_detects_a_worsened_violation(self):
        # Refutation: the ratchet must BITE, not merely pass.
        data = cg.load_debt()
        if not data["rows"]:
            self.skipTest("no debt rows left — the ratchet is already retired")
        row = dict(data["rows"][0])
        row["value"] = 1  # pretend we had declared a far smaller violation
        shrunk = {"schema": cg.DEBT_SCHEMA,
                  "rows": [row] + [r for r in data["rows"][1:]]}
        problems, clean = cg.reconcile(debt=shrunk)
        self.assertFalse(clean)
        self.assertTrue(any(p[0] == cg.OVER for p in problems),
                        "a violation worse than declared must be reported OVER")

    def test_an_undeclared_over_cap_file_outside_the_lib_is_reported(self):
        # The shape task 67's widening created: before it, a 300-line loop
        # script or a 900-line webui panel produced NO violation at all, because
        # the scan never looked there. Planting one proves the widened scan
        # reaches the new layers rather than merely enumerating them.
        for planted, cap in ((cg.FILE_OVER, "scripts/loop/newmonolith.sh"),
                             (cg.FILE_OVER, "webui/src/panels/Huge.tsx"),
                             (cg.FILE_OVER, "tests/gates/test_enormous.py")):
            found = cg.scan() + [(planted, cap, 9999, 250)]
            problems, clean = cg.reconcile(found=found)
            self.assertFalse(clean)
            self.assertTrue(
                any(p[0] == cg.OVER and p[1] == cap for p in problems),
                "an over-cap %s with no debt row must be reported" % cap)

    def test_the_widened_scan_actually_measures_every_layer(self):
        # Guards the vacuous pass: a corpus rule that matched nothing outside the
        # lib would make every assertion above true of a tree nobody looked at.
        #
        # Narrowed at task 69, and the narrowing is the finding. This asserted
        # that every layer PRODUCED a violation, which measured coverage only
        # while each layer happened to be dirty -- so decomposing the dashboard
        # (the `scripts` layer's only violation, and the fix this ratchet
        # existed to demand) turned the gate red for a tree that had just got
        # better. A vacuity check that reddens on a repair is measuring the
        # residual, not the scan. So it now proves the scan REACHES each layer
        # directly: take a REAL file from it, drop that layer's cap, and require
        # the finding to come back naming that path.
        by_layer = {}
        for rel in cg.corpus_files():
            by_layer.setdefault(_corpus.layer_of(rel), []).append(rel)
        for layer in ("lib", "tests", "hooks", "scripts", "loop", "webui",
                      "entrypoint", "orchestrator"):
            rels = by_layer.get(layer, [])
            self.assertTrue(rels, "the corpus enumerates no %s file -- check "
                                  "tests/_corpus.py" % layer)
            rel = sorted(rels)[0]
            # Patched through the GATE's own corpus reference: `tests._corpus`
            # and bare `_corpus` are two module objects under `discover`, and
            # patching the wrong copy is a test that quietly measures the real
            # cap (the seam `tests/_fixtures.load_cli` documents).
            caps = cg._corpus.LAYER_CAPS
            real_cap = caps[layer]
            caps[layer] = 1
            try:
                found = cg.scan_file(rel, parse=False)
            finally:
                caps[layer] = real_cap
            self.assertTrue(
                any(kind == cg.FILE_OVER and loc == rel
                    for kind, loc, _v, _c in found),
                "the scan measured no line count for %s, so the %s layer is "
                "enumerated but not measured -- check tests/_corpus.py"
                % (rel, layer))
        self.assertGreater(len(cg.corpus_files()), 400)

    def test_ratchet_detects_a_stale_row(self):
        data = cg.load_debt()
        invented = _row(cg.FUNC_BRANCHES,
                        "scripts/agentware_lib/core/does_not_exist.py::f", 9999,
                        "synthetic row proving the ratchet reports an exemption "
                        "the tree no longer needs")
        problems, clean = cg.reconcile(
            debt={"schema": cg.DEBT_SCHEMA, "rows": data["rows"] + [invented]})
        self.assertFalse(clean)
        self.assertTrue(
            any(p[0] == cg.STALE and "does_not_exist" in p[1] for p in problems),
            "a row the tree no longer produces must be reported STALE")

    def test_debt_may_not_forgive_a_task21_package(self):
        # The one carve-out the schema refuses, so claim 1 cannot be bypassed by
        # simply declaring the regression as debt.
        bad = _row(cg.FUNC_BRANCHES,
                   "scripts/agentware_lib/features/dreamsteps/"
                   "registry.py::register", 9999,
                   "synthetic row that must be refused because it forgives a "
                   "task-21 split package")
        errors = cg.schema_errors({"schema": cg.DEBT_SCHEMA, "rows": [bad]})
        self.assertTrue(
            any("task 21 split" in e for e in errors),
            "the schema must refuse a debt row inside a task-21 package: %r"
            % (errors,))

    def test_round2_packages_may_still_carry_function_debt(self):
        # The asymmetry is deliberate and worth pinning, because it is the one
        # place the two rounds differ. Round 1 split both axes; round 2 split
        # FILES only, so its inherited per-function complexity stays declared
        # under task 99 rather than being rewritten here — task 30's body differ
        # would redden if these bodies were shortened.
        ok = _row(cg.FUNC_BRANCHES,
                  "scripts/agentware_lib/ops/fleet/fleetreport.py::cmd_fleet",
                  44, "inherited donor complexity in a round-2 package; the "
                  "file cap is met, the function cap is adjudicated separately")
        errors = cg.schema_errors({"schema": cg.DEBT_SCHEMA, "rows": [ok]})
        self.assertEqual(
            errors, [],
            "a round-2 package must be allowed to carry FUNCTION debt: %r"
            % (errors,))


if __name__ == "__main__":
    unittest.main()
