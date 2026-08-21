"""`reviewremediate`'s own unit file — task 99 wave 5, landed with the paydown.

`agentware_lib.ops.reviewremediate` runs ONE remediation pass: prompt the fixer
with the CONFIRMED-blocking findings, align its reported fixes back onto those
findings, and let the REGRESSION GATE CHAIN — not the fixer's own claim — decide
whether the pass succeeded. Until this file the module had no owning test.

This wave split the driver's untrusted-output half into `_review_index_raw_fixes`
and `_review_align_fixes` (20 branches -> 10). Nine of the twenty branches were
defensive parsing of a JSON blob written by an agent that has every incentive to
claim success, which is why they deserve their own subject: each property below
is a shape the fixer can emit that would otherwise make an UNFIXED finding read
as fixed.

  SELECT/BLOCKING   — only `_review_is_blocking` findings are remediated:
                      confirmed AND high/medium. Fixing a refuted finding means
                      editing working code to satisfy a claim already shown
                      wrong, so the selection is a safety property, not a filter.
  SELECT/EMPTY      — nothing confirmed is a SUCCESS with `requested: 0`, no
                      fixer spawn and no gate chain: there was nothing to verify.
  INDEX/DECLARED    — a fix is matched to its finding by its declared `index`.
  INDEX/JUNK        — an `index` that `int()` refuses, a non-object element, and
                      a `fixes` value that is not a list are all tolerated: the
                      pass degrades to positional/unfixable, never raises.
  ALIGN/POSITIONAL  — a fix with NO index falls back to its position.
  ALIGN/PER-FINDING — there is one row per CONFIRMED FINDING, never one per
                      returned fix, so a fixer that answered two of three leaves
                      the third as an explicit 'unfixable' instead of silently
                      shortening the list into 'nothing left to fix'.
  ALIGN/STAMP       — each row carries its finding's summary and severity, so
                      the report can name what was fixed without re-joining.
  GATE/ONLY-IF-ALL  — the chain re-runs ONLY when every fix claims 'fixed'; a
                      pass with a survivor has nothing to re-verify.
  GATE/DECIDES      — `all_fixed` is the AND of the fixer's claim and the
                      chain's verdict. A green claim with a red chain is NOT
                      fixed, which is the entire point of the module.
  GATE/SKIPPED      — with the chain skipped (`--no-gate-chain`), the fixer's
                      claim stands alone and `gate_chain` is None, so the report
                      can tell 'verified' from 'unverified' after the fact.

Hermetic: the `_review_invoke_agent` and `_review_run_gate_chain` seams are
monkeypatched — no agent spawns, no subprocesses, no network.
"""

from tests._paths import TESTS_DIR

import json
import os
import sys
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

from agentware_lib.ops import reviewremediate  # noqa: E402
from agentware_lib.ops.reviewremediate import (  # noqa: E402
    _review_align_fixes,
    _review_index_raw_fixes,
    _review_run_remediation,
)

CFG = {"model": "opus", "effort": "max", "fanout": 5, "max_rounds": 3}


def _finding(summary="s", severity="high", verdict="confirmed", **extra):
    f = {"summary": summary, "severity": severity, "verdict": verdict,
         "dimension": "correctness", "file": "a.py", "line": 1,
         "failure_scenario": "f(None) raises"}
    f.update(extra)
    return f


CONFIRMED = [_finding("first"), _finding("second", severity="medium")]


class _SeamCase(unittest.TestCase):
    """Installs a scripted fixer reply and an injected gate-chain verdict."""

    def setUp(self):
        self._agent = reviewremediate._review_invoke_agent
        self._gate = reviewremediate._review_run_gate_chain
        self.gate_calls = []
        self.addCleanup(self._restore)

    def _restore(self):
        reviewremediate._review_invoke_agent = self._agent
        reviewremediate._review_run_gate_chain = self._gate

    def install(self, fixer_out, gate=None):
        def fake(role, persona, prompt, cfg, **kw):
            return fixer_out

        def fake_gate(regression_tests=None, cwd=None):
            self.gate_calls.append(list(regression_tests or []))
            return gate
        reviewremediate._review_invoke_agent = fake
        reviewremediate._review_run_gate_chain = fake_gate

    def run_pass(self, fixer_out, gate=None, findings=None, **kw):
        self.install(fixer_out, gate)
        return _review_run_remediation(
            CONFIRMED if findings is None else findings, CFG, "diff",
            "criteria", **kw)


def _fixes(*rows):
    return json.dumps({"fixes": list(rows)})


FIXED_0 = {"index": 0, "status": "fixed", "regression_test": "tests.unit.t0"}
FIXED_1 = {"index": 1, "status": "fixed", "regression_test": "tests.unit.t1"}
GATE_OK = {"ok": True, "steps": [{"name": "tests", "ok": True}]}
GATE_RED = {"ok": False, "steps": [{"name": "tests", "ok": False}]}


# --- the untrusted-output helpers -------------------------------------------

class IndexRawFixesTest(unittest.TestCase):

    def test_declared_indices_are_mapped(self):
        """INDEX/DECLARED."""
        raw, by_index = _review_index_raw_fixes([FIXED_0, FIXED_1])
        self.assertEqual(len(raw), 2)
        self.assertEqual(sorted(by_index), [0, 1])

    def test_a_string_index_that_parses_is_accepted(self):
        """INDEX/DECLARED: agents emit JSON, and JSON numbers arrive as strings
        often enough that refusing them would drop real fixes."""
        _raw, by_index = _review_index_raw_fixes(
            [{"index": "1", "status": "fixed", "regression_test": "t"}])
        self.assertEqual(list(by_index), [1])

    def test_an_unparseable_index_is_skipped_not_raised(self):
        """INDEX/JUNK: the row is dropped from the map; the pass continues."""
        _raw, by_index = _review_index_raw_fixes(
            [{"index": "not-an-int", "status": "fixed"}, FIXED_1])
        self.assertEqual(list(by_index), [1])

    def test_a_none_index_is_left_for_the_positional_fallback(self):
        """INDEX/JUNK: absent is not junk — it means 'match me by position'."""
        _raw, by_index = _review_index_raw_fixes(
            [{"status": "fixed", "regression_test": "t"}])
        self.assertEqual(by_index, {})

    def test_non_object_elements_are_ignored(self):
        """INDEX/JUNK: a bare string cannot declare an index."""
        raw, by_index = _review_index_raw_fixes(["a string", None, FIXED_1])
        self.assertEqual(len(raw), 3)
        self.assertEqual(list(by_index), [1])

    def test_a_non_list_fixes_value_becomes_an_empty_list(self):
        """INDEX/JUNK: the coerced list is what the positional fallback indexes
        into, so returning the raw value would make `raw_fixes[i]` raise."""
        for junk in ({"index": 0}, "fixes", 7, None):
            raw, by_index = _review_index_raw_fixes(junk)
            self.assertEqual((raw, by_index), ([], {}))

    def test_a_later_duplicate_index_wins(self):
        """INDEX/DECLARED: last write wins, deterministically — two rows for one
        finding is malformed input, and picking a winner beats raising."""
        _raw, by_index = _review_index_raw_fixes(
            [FIXED_0, {"index": 0, "status": "unfixable"}])
        self.assertEqual(by_index[0]["status"], "unfixable")


class AlignFixesTest(unittest.TestCase):

    def test_one_row_per_confirmed_finding(self):
        """ALIGN/PER-FINDING: three findings, one answer -> still three rows."""
        raw, by_index = _review_index_raw_fixes([FIXED_0])
        fixes = _review_align_fixes(
            [_finding("a"), _finding("b"), _finding("c")], raw, by_index)
        self.assertEqual(len(fixes), 3)
        self.assertEqual([f["status"] for f in fixes],
                         ["fixed", "unfixable", "unfixable"])

    def test_a_declared_index_beats_position(self):
        """INDEX/DECLARED: the fixer answered finding 1 first."""
        raw, by_index = _review_index_raw_fixes([FIXED_1, FIXED_0])
        fixes = _review_align_fixes(CONFIRMED, raw, by_index)
        self.assertEqual([f["regression_test"] for f in fixes],
                         ["tests.unit.t0", "tests.unit.t1"])

    def test_an_index_less_fix_falls_back_to_its_position(self):
        """ALIGN/POSITIONAL."""
        rows = [{"status": "fixed", "regression_test": "tests.unit.a"},
                {"status": "fixed", "regression_test": "tests.unit.b"}]
        raw, by_index = _review_index_raw_fixes(rows)
        fixes = _review_align_fixes(CONFIRMED, raw, by_index)
        self.assertEqual([f["regression_test"] for f in fixes],
                         ["tests.unit.a", "tests.unit.b"])

    def test_the_positional_fallback_never_steals_an_indexed_row(self):
        """ALIGN/POSITIONAL: position 0 holds a row that DECLARED index 1, so
        finding 0 must fall through to 'unfixable' rather than adopt it."""
        raw, by_index = _review_index_raw_fixes([FIXED_1])
        fixes = _review_align_fixes(CONFIRMED, raw, by_index)
        self.assertEqual([f["status"] for f in fixes], ["unfixable", "fixed"])

    def test_a_non_object_at_the_position_is_not_adopted(self):
        """ALIGN/POSITIONAL: 'a string' at index 0 normalizes to unfixable with
        the normalizer's own note, rather than crashing the alignment."""
        raw, by_index = _review_index_raw_fixes(["a string"])
        fixes = _review_align_fixes([_finding("a")], raw, by_index)
        self.assertEqual(fixes[0]["status"], "unfixable")

    def test_each_row_is_stamped_with_its_findings_identity(self):
        """ALIGN/STAMP."""
        raw, by_index = _review_index_raw_fixes([FIXED_0, FIXED_1])
        fixes = _review_align_fixes(CONFIRMED, raw, by_index)
        self.assertEqual([f["finding_summary"] for f in fixes],
                         ["first", "second"])
        self.assertEqual([f["severity"] for f in fixes], ["high", "medium"])

    def test_a_fix_claiming_fixed_without_a_regression_test_is_refused(self):
        """ALIGN/PER-FINDING: the normalizer downgrades it, and this alignment
        must not paper over that — an unverified fix is the one thing the module
        exists to refuse."""
        raw, by_index = _review_index_raw_fixes(
            [{"index": 0, "status": "fixed", "regression_test": ""}])
        fixes = _review_align_fixes([_finding("a")], raw, by_index)
        self.assertEqual(fixes[0]["status"], "unfixable")
        self.assertIn("REFUSED", fixes[0]["notes"])

    def test_no_confirmed_findings_produce_no_rows(self):
        """ALIGN/PER-FINDING: the list is driven by the findings, not the fixes."""
        raw, by_index = _review_index_raw_fixes([FIXED_0, FIXED_1])
        self.assertEqual(_review_align_fixes([], raw, by_index), [])


# --- the pass ---------------------------------------------------------------

class RunRemediationTest(_SeamCase):

    def test_only_confirmed_blocking_findings_are_remediated(self):
        """SELECT/BLOCKING: a refuted high and a confirmed low are both out."""
        findings = [_finding("keep", severity="high"),
                    _finding("refuted", verdict="refuted"),
                    _finding("low", severity="low"),
                    _finding("uncertain", verdict="uncertain")]
        res = self.run_pass(_fixes(FIXED_0), gate=GATE_OK, findings=findings)
        self.assertEqual(res["requested"], 1)
        self.assertEqual(res["fixes"][0]["finding_summary"], "keep")

    def test_nothing_confirmed_succeeds_without_spawning_anything(self):
        """SELECT/EMPTY: no fixer, no gate chain, all_fixed True."""
        spawned = []

        def counting(role, persona, prompt, cfg, **kw):
            spawned.append(role)
            return _fixes(FIXED_0)
        reviewremediate._review_invoke_agent = counting
        reviewremediate._review_run_gate_chain = (
            lambda regression_tests=None, cwd=None: GATE_OK)
        res = _review_run_remediation([_finding("r", verdict="refuted")], CFG,
                                      diff_range="HEAD~1..HEAD")
        self.assertEqual(res["requested"], 0)
        self.assertTrue(res["all_fixed"])
        self.assertIsNone(res["gate_chain"])
        self.assertEqual(spawned, [])
        self.assertEqual(res["new_diff_range"], "HEAD~1..HEAD")

    def test_an_empty_findings_list_is_the_same_early_return(self):
        """SELECT/EMPTY: and a None findings argument too."""
        self.install(_fixes(FIXED_0), GATE_OK)
        for findings in ([], None):
            res = _review_run_remediation(findings, CFG, "diff", "criteria")
            self.assertEqual((res["requested"], res["all_fixed"]), (0, True))
            self.assertEqual(res["fixes"], [])
        self.assertEqual(self.gate_calls, [])

    def test_a_green_claim_with_a_red_chain_is_not_fixed(self):
        """GATE/DECIDES: the chain's exit code, not the agent's claim."""
        res = self.run_pass(_fixes(FIXED_0, FIXED_1), gate=GATE_RED)
        self.assertEqual([f["status"] for f in res["fixes"]],
                         ["fixed", "fixed"])
        self.assertFalse(res["all_fixed"])
        self.assertEqual(res["gate_chain"], GATE_RED)

    def test_a_green_claim_with_a_green_chain_is_fixed(self):
        """GATE/DECIDES: the other half of the AND."""
        res = self.run_pass(_fixes(FIXED_0, FIXED_1), gate=GATE_OK)
        self.assertTrue(res["all_fixed"])
        self.assertEqual(self.gate_calls, [["tests.unit.t0", "tests.unit.t1"]])

    def test_the_chain_does_not_run_when_a_finding_survives(self):
        """GATE/ONLY-IF-ALL: nothing to re-verify, so nothing is re-run — and
        `gate_chain` stays None so the report cannot read a skipped chain as a
        passed one."""
        res = self.run_pass(
            _fixes(FIXED_0, {"index": 1, "status": "unfixable"}), gate=GATE_OK)
        self.assertFalse(res["all_fixed"])
        self.assertIsNone(res["gate_chain"])
        self.assertEqual(self.gate_calls, [])

    def test_the_chain_can_be_skipped_explicitly(self):
        """GATE/SKIPPED: `--no-gate-chain` leaves the fixer's claim standing."""
        res = self.run_pass(_fixes(FIXED_0, FIXED_1), gate=GATE_OK,
                            run_gate_chain=False)
        self.assertTrue(res["all_fixed"])
        self.assertIsNone(res["gate_chain"])
        self.assertEqual(self.gate_calls, [])

    def test_only_named_regression_tests_reach_the_chain(self):
        """GATE/ONLY-IF-ALL: an empty regression_test would downgrade the fix to
        unfixable anyway, so the chain never receives a blank module name."""
        res = self.run_pass(
            _fixes(FIXED_0, {"index": 1, "status": "fixed",
                             "regression_test": "   "}), gate=GATE_OK)
        self.assertFalse(res["all_fixed"])
        self.assertEqual(self.gate_calls, [])

    def test_a_gate_chain_that_returns_none_does_not_block(self):
        """GATE/DECIDES: `gate is None or gate.get('ok')` — an injected None is
        'no chain ran', which must not be read as a red chain."""
        res = self.run_pass(_fixes(FIXED_0, FIXED_1), gate=None)
        self.assertTrue(res["all_fixed"])
        self.assertIsNone(res["gate_chain"])

    def test_a_fixer_that_returned_prose_leaves_every_finding_unfixed(self):
        """INDEX/JUNK: no JSON at all is the failed-spawn shape, and it must
        produce explicit unfixable rows rather than an empty, all-clear list."""
        res = self.run_pass("the fixer returned prose", gate=GATE_OK)
        self.assertEqual([f["status"] for f in res["fixes"]],
                         ["unfixable", "unfixable"])
        self.assertFalse(res["all_fixed"])
        self.assertEqual(res["requested"], 2)

    def test_the_envelope_echoes_the_axes_and_the_range(self):
        """The report reads model/effort off this envelope, and task 5's
        re-audit reads `new_diff_range`."""
        res = self.run_pass(_fixes(FIXED_0, FIXED_1), gate=GATE_OK,
                            diff_range="abc..def")
        self.assertEqual((res["model"], res["effort"]), ("opus", "max"))
        self.assertEqual(res["new_diff_range"], "abc..def")

    def test_the_fixer_is_spawned_exactly_once(self):
        """SELECT/BLOCKING: ONE mutating attempt. A retried fixer would replay
        non-idempotent edits over an already-mutated tree, which is why the
        driver passes retries=0 — so a second spawn here is a real regression."""
        spawned = []

        def counting(role, persona, prompt, cfg, **kw):
            spawned.append((role, kw.get("retries")))
            return _fixes(FIXED_0, FIXED_1)
        reviewremediate._review_invoke_agent = counting
        reviewremediate._review_run_gate_chain = (
            lambda regression_tests=None, cwd=None: GATE_OK)
        _review_run_remediation(CONFIRMED, CFG)
        self.assertEqual(spawned, [("fixer", 0)])


if __name__ == "__main__":
    unittest.main()
