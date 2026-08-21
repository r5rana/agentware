"""`reviewreport`'s own unit file — task 99 wave 5, landed with the paydown.

`agentware_lib.ops.reviewreport` turns the gate's per-round JSON artifacts into
the durable `adversarial-review.md` a human actually reads. Until this file the
module had no owning test; `test_review_report` exercises the worklog call-out
and the low-finding workorder, which are the module's OTHER two obligations and
say nothing about the renderer.

This wave split `_review_render_report` at its own markdown headings into
`_review_report_header_lines`, `_review_report_findings_lines`,
`_review_report_critic_lines` and `_review_report_remediation_lines` (with
`_review_report_fix_lines`), 31 branches -> 4. Every property below is a way the
report can MISREPRESENT a round that ran, which is worse than a report that
fails to render: six months later the report is all that is left of the run.

  HEADER/AXES     — the header records the model and effort the round actually
                    ran at, because a clean report from a downgraded reviewer
                    and one from a pinned reviewer are not the same evidence.
                    cfg wins, then the first round's audit, then the pinned
                    defaults — and the fallback is per-FIELD, not all-or-nothing.
  HEADER/EMPTY    — no rounds renders an explicit "no rounds ran" line rather
                    than an empty findings section that reads as "nothing found".
  ROUND/HEADING   — every round prints its own `## Round N` heading, so two
                    rounds never merge into one findings list.
  ROUND/COUNTERS  — the audit's own counters are echoed verbatim, defaulting to
                    0 rather than to "?", and `-` marks an absent dimension list.
  FINDINGS/NONE   — a round with no findings prints `_none_`, which is the only
                    thing distinguishing "audited and clean" from "not rendered".
  FINDINGS/JUNK   — non-object findings are skipped and the COUNT follows the
                    skip, so the heading never claims more than it lists.
  CRITIC/COUNTS   — gaps and additions are counted separately; an empty gap
                    prints `(empty)` rather than a blank bullet.
  REM/ABSENT      — a round with NO remediation artifact grows NO remediation
                    section: an empty one would claim zero fixes were requested.
  REM/CHAIN       — the gate-chain line appears only when a chain ran, and it
                    names every step, because `ok: False` without the step names
                    is unactionable.
  FIX/UNTRUSTED   — every fix field is agent output, collapsed to ONE bounded
                    line so a fix can never forge a heading or a second bullet.
  DOC/TRAILING    — the document ends in exactly one newline regardless of which
                    sections rendered.

Hermetic: pure rendering, no seams, no tempdirs, stdlib unittest only.
"""

from tests._paths import TESTS_DIR

import os
import sys
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

from agentware_lib.ops.reviewreport import (  # noqa: E402
    _review_render_report,
    _review_report_critic_lines,
    _review_report_findings_lines,
    _review_report_fix_lines,
    _review_report_header_lines,
    _review_report_remediation_lines,
)


def _finding(summary="s", severity="high", verdict="confirmed", **extra):
    f = {"summary": summary, "severity": severity, "verdict": verdict,
         "dimension": "correctness", "file": "a.py", "line": 7,
         "failure_scenario": "f(None) raises"}
    f.update(extra)
    return f


def _audit(**kw):
    a = {"model": "opus", "effort": "max", "dimensions": ["correctness"],
         "findings": [_finding()], "critic": {"gaps": [],
                                              "additional_findings": []},
         "confirmed_blocking": 1, "malformed": 0}
    a.update(kw)
    return a


# --- HEADER -----------------------------------------------------------------

class HeaderLinesTest(unittest.TestCase):

    def test_cfg_outranks_the_rounds_own_audit(self):
        """HEADER/AXES: an explicitly-resolved config is what the run used."""
        lines = _review_report_header_lines(
            [{"audit": _audit(model="opus", effort="max")}],
            cfg={"model": "sonnet", "effort": "low"})
        self.assertIn("- model: sonnet", lines)
        self.assertIn("- effort: low", lines)

    def test_the_first_rounds_audit_is_the_fallback(self):
        """HEADER/AXES: with no cfg, the artifact records what actually ran."""
        lines = _review_report_header_lines(
            [{"audit": _audit(model="haiku", effort="medium")}])
        self.assertIn("- model: haiku", lines)
        self.assertIn("- effort: medium", lines)

    def test_the_fallback_is_per_field(self):
        """HEADER/AXES: a cfg naming only the model must not drag the DEFAULT
        effort in beside it while the audit's real effort is right there."""
        lines = _review_report_header_lines(
            [{"audit": _audit(model="opus", effort="medium")}],
            cfg={"model": "sonnet"})
        self.assertIn("- model: sonnet", lines)
        self.assertIn("- effort: medium", lines)

    def test_the_pinned_defaults_are_the_last_resort(self):
        """HEADER/AXES: no cfg and no rounds."""
        lines = _review_report_header_lines([])
        self.assertIn("- model: opus", lines)
        self.assertIn("- effort: max", lines)

    def test_the_round_count_and_outcome_are_reported(self):
        """HEADER/AXES: and an absent outcome is `-`, never blank."""
        lines = _review_report_header_lines(
            [{"audit": _audit()}, {"audit": _audit()}], outcome="blocked")
        self.assertIn("- rounds: 2", lines)
        self.assertIn("- outcome: blocked", lines)
        self.assertIn("- outcome: -", _review_report_header_lines([]))

    def test_the_feature_title_falls_back_and_is_collapsed(self):
        """FIX/UNTRUSTED: the feature name reaches an H1, so a newline in it
        would forge the rest of the document."""
        self.assertIn("# Adversarial review — self-extension diff",
                      _review_report_header_lines([]))
        head = _review_report_header_lines([], feature="a\nb")[0]
        self.assertNotIn("\n", head)
        self.assertIn("a b", head)


# --- ROUND / FINDINGS -------------------------------------------------------

class FindingsLinesTest(unittest.TestCase):

    def test_the_round_heading_travels_with_the_findings(self):
        """ROUND/HEADING."""
        lines = _review_report_findings_lines({"round": 3}, _audit())
        self.assertEqual(lines[0], "## Round 3")

    def test_a_round_with_no_number_still_gets_a_heading(self):
        """ROUND/HEADING: `?` is a rendered round; an omitted heading is not."""
        self.assertEqual(
            _review_report_findings_lines({}, _audit())[0], "## Round ?")

    def test_the_counters_default_to_zero_not_to_a_question_mark(self):
        """ROUND/COUNTERS: `0 confirmed blocking` is a claim; `?` is not."""
        lines = _review_report_findings_lines({"round": 1}, {})
        self.assertIn("- confirmed blocking (high/med): 0", lines)
        self.assertIn("- malformed reviewer payloads: 0", lines)

    def test_an_absent_dimension_list_renders_as_a_dash(self):
        """ROUND/COUNTERS."""
        self.assertIn("- dimensions audited: -",
                      _review_report_findings_lines({}, {"dimensions": []}))

    def test_dimensions_are_joined_in_order(self):
        """ROUND/COUNTERS: the ORDER is the fan-out order, so it is preserved."""
        lines = _review_report_findings_lines(
            {}, _audit(dimensions=["correctness", "test-quality", 7]))
        self.assertIn("- dimensions audited: correctness, test-quality, 7",
                      lines)

    def test_no_findings_renders_none_explicitly(self):
        """FINDINGS/NONE."""
        lines = _review_report_findings_lines({}, _audit(findings=[]))
        self.assertIn("### Findings (0)", lines)
        self.assertIn("_none_", lines)

    def test_non_object_findings_are_skipped_and_the_count_follows(self):
        """FINDINGS/JUNK: a heading claiming 3 above a list of 1 is a report
        that tells the reader two findings were lost when they never existed."""
        lines = _review_report_findings_lines(
            {}, _audit(findings=["a string", _finding("real"), None]))
        self.assertIn("### Findings (1)", lines)
        self.assertEqual(
            sum(1 for line in lines if line.startswith("- [")), 1)

    def test_a_failure_scenario_is_rendered_as_a_sub_bullet(self):
        """FINDINGS/JUNK: and only when non-empty, so a finding without one does
        not grow a dangling `- failure:` label."""
        lines = _review_report_findings_lines({}, _audit())
        self.assertTrue(any(x.startswith("  - failure: ") for x in lines))
        lines = _review_report_findings_lines(
            {}, _audit(findings=[_finding(failure_scenario="")]))
        self.assertFalse(any(x.startswith("  - failure: ") for x in lines))


# --- CRITIC -----------------------------------------------------------------

class CriticLinesTest(unittest.TestCase):

    def test_gaps_and_additions_are_counted_separately(self):
        """CRITIC/COUNTS."""
        lines = _review_report_critic_lines(_audit(critic={
            "gaps": ["g1", "g2"], "additional_findings": [_finding("added")]}))
        self.assertIn("- gaps named: 2", lines)
        self.assertIn("- additional findings (surfaced uncertain): 1", lines)

    def test_an_absent_critic_still_prints_its_zeroes(self):
        """CRITIC/COUNTS: a missing section would read as 'the critic ran and
        is not shown', which is the opposite of what happened."""
        lines = _review_report_critic_lines({})
        self.assertIn("### Completeness critic", lines)
        self.assertIn("- gaps named: 0", lines)

    def test_an_empty_gap_prints_a_placeholder(self):
        """CRITIC/COUNTS: a blank bullet is invisible, so the count and the list
        would disagree with no way for a reader to see why."""
        lines = _review_report_critic_lines(_audit(critic={"gaps": ["", None]}))
        self.assertEqual([x for x in lines if x.startswith("  - ")],
                         ["  - (empty)", "  - (empty)"])

    def test_non_object_additions_are_not_counted(self):
        """CRITIC/COUNTS: same claim as FINDINGS/JUNK, other list."""
        lines = _review_report_critic_lines(_audit(critic={
            "gaps": [], "additional_findings": ["junk", _finding("added")]}))
        self.assertIn("- additional findings (surfaced uncertain): 1", lines)

    def test_gap_text_is_collapsed_to_one_line(self):
        """FIX/UNTRUSTED: a gap is critic output and reaches a bullet."""
        lines = _review_report_critic_lines(
            _audit(critic={"gaps": ["a\nb\nc"]}))
        self.assertIn("  - a b c", lines)


# --- REMEDIATION ------------------------------------------------------------

class RemediationLinesTest(unittest.TestCase):

    def test_no_remediation_renders_no_section(self):
        """REM/ABSENT."""
        for absent in (None, {}, False):
            self.assertEqual(_review_report_remediation_lines(absent), [])

    def test_the_section_reports_the_request_count_and_verdict(self):
        """REM/CHAIN."""
        lines = _review_report_remediation_lines(
            {"requested": 2, "all_fixed": False, "fixes": []})
        self.assertIn("### Remediation", lines)
        self.assertIn("- confirmed requested: 2", lines)
        self.assertIn("- all fixed: False", lines)

    def test_the_gate_chain_line_names_every_step(self):
        """REM/CHAIN: `ok: False` alone does not say which gate failed."""
        lines = _review_report_remediation_lines({
            "requested": 1, "all_fixed": False, "fixes": [],
            "gate_chain": {"ok": False, "steps": [
                {"name": "tests", "ok": True},
                {"name": "gate-release", "ok": False}]}})
        self.assertIn("- gate chain ok: False (tests=True, gate-release=False)",
                      lines)

    def test_a_skipped_chain_prints_no_chain_line(self):
        """REM/CHAIN: `gate_chain: None` means no chain RAN, and inventing an
        `ok: None` line would read as a chain that returned nothing."""
        lines = _review_report_remediation_lines(
            {"requested": 1, "all_fixed": True, "fixes": [],
             "gate_chain": None})
        self.assertFalse(any("gate chain" in x for x in lines))

    def test_non_object_steps_are_skipped(self):
        """REM/CHAIN: the steps list is agent-adjacent JSON like everything
        else here."""
        lines = _review_report_remediation_lines({
            "fixes": [], "gate_chain": {"ok": True, "steps": [
                7, {"name": "tests", "ok": True}]}})
        self.assertIn("- gate chain ok: True (tests=True)", lines)

    def test_non_object_fixes_are_skipped(self):
        """FIX/UNTRUSTED."""
        lines = _review_report_remediation_lines(
            {"fixes": ["a string", {"index": 0, "status": "fixed",
                                    "regression_test": "t"}]})
        self.assertEqual(sum(1 for x in lines if x.startswith("  - #")), 1)


class FixLinesTest(unittest.TestCase):

    def test_a_fix_renders_index_status_summary_and_regression_test(self):
        """FIX/UNTRUSTED: the four fields a reader triages on."""
        self.assertEqual(
            _review_report_fix_lines({"index": 0, "status": "fixed",
                                      "finding_summary": "the bug",
                                      "regression_test": "tests.unit.t"}),
            ["  - #0 [fixed] the bug — reg-test: tests.unit.t"])

    def test_missing_fields_get_explicit_placeholders(self):
        """FIX/UNTRUSTED: `(no summary)` is a claim; an empty gap is not."""
        line = _review_report_fix_lines({})[0]
        self.assertIn("[?]", line)
        self.assertIn("(no summary)", line)
        self.assertIn("reg-test: -", line)

    def test_a_present_but_empty_field_gets_the_placeholder_too(self):
        """FIX/UNTRUSTED: an ABSENT key and a key PRESENT-but-empty are two
        different inputs, and the guards that answer them are two different
        pieces of code — `.get(k, default)` handles the first, the trailing
        `or` handles the second. `status` in particular defaults to '?' when
        absent, so the `or "?"` beside it is reachable ONLY through this input;
        a fixer that emits `"status": ""` (or null, or whitespace) is the shape
        that finds it, and asserting only the absent-key case leaves the guard
        untested. Found by the wave-5 non-vacuity sweep."""
        for empty in ("", "   ", None):
            line = _review_report_fix_lines(
                {"index": 0, "status": empty, "finding_summary": empty,
                 "regression_test": empty, "notes": empty})[0]
            self.assertIn("[?]", line)
            self.assertIn("(no summary)", line)
            self.assertIn("reg-test: -", line)

    def test_notes_are_a_second_line_only_when_present(self):
        """FIX/UNTRUSTED."""
        self.assertEqual(len(_review_report_fix_lines(
            {"index": 0, "status": "fixed", "regression_test": "t"})), 1)
        self.assertEqual(_review_report_fix_lines(
            {"index": 0, "status": "fixed", "regression_test": "t",
             "notes": "why"})[1], "    notes: why")

    def test_every_field_is_collapsed_to_one_line(self):
        """FIX/UNTRUSTED: a status carrying a newline could otherwise open a
        forged bullet inside the remediation list."""
        lines = _review_report_fix_lines(
            {"index": 0, "status": "fi\nxed", "regression_test": "t\nx",
             "finding_summary": "s\nu", "notes": "n\no"})
        for line in lines:
            self.assertEqual(line.count("\n"), 0)


# --- THE DOCUMENT -----------------------------------------------------------

class RenderReportTest(unittest.TestCase):

    def test_no_rounds_says_so_and_renders_no_sections(self):
        """HEADER/EMPTY."""
        for rounds in ([], None):
            text = _review_render_report(rounds)
            self.assertIn("_No audit rounds ran", text)
            self.assertNotIn("## Round", text)
            self.assertNotIn("### Findings", text)

    def test_two_rounds_render_two_headings_in_order(self):
        """ROUND/HEADING: the failure this guards is two rounds' findings
        merging into one list, which silently doubles round 1's apparent yield."""
        text = _review_render_report(
            [{"round": 1, "audit": _audit(findings=[_finding("first")])},
             {"round": 2, "audit": _audit(findings=[_finding("second")])}])
        self.assertLess(text.index("## Round 1"), text.index("first"))
        self.assertLess(text.index("first"), text.index("## Round 2"))
        self.assertLess(text.index("## Round 2"), text.index("second"))
        self.assertEqual(text.count("### Findings"), 2)
        self.assertEqual(text.count("### Completeness critic"), 2)

    def test_only_the_round_with_a_remediation_grows_that_section(self):
        """REM/ABSENT: an audit-only re-audit round is the normal final round."""
        text = _review_render_report([
            {"round": 1, "audit": _audit(),
             "remediation": {"requested": 1, "all_fixed": True, "fixes": []}},
            {"round": 2, "audit": _audit()}])
        self.assertEqual(text.count("### Remediation"), 1)

    def test_the_document_ends_in_exactly_one_newline(self):
        """DOC/TRAILING: for every shape the sections can take."""
        for rounds in ([], [{"round": 1, "audit": _audit()}],
                       [{"round": 1, "audit": _audit(),
                         "remediation": {"fixes": [{"index": 0}]}}]):
            text = _review_render_report(rounds)
            self.assertTrue(text.endswith("\n"))
            self.assertFalse(text.endswith("\n\n"))

    def test_the_header_is_rendered_before_any_round(self):
        """HEADER/AXES: the axes must not be readable as round 1's own."""
        text = _review_render_report([{"round": 1, "audit": _audit()}],
                                     feature="feat", outcome="clean")
        self.assertLess(text.index("- outcome: clean"), text.index("## Round 1"))
        self.assertTrue(text.startswith("# Adversarial review — feat"))

    def test_a_round_that_is_an_empty_object_still_renders(self):
        """ROUND/HEADING + FINDINGS/NONE: a garbled artifact the loader kept
        must not take the whole report down with it."""
        text = _review_render_report([{}])
        self.assertIn("## Round ?", text)
        self.assertIn("_none_", text)


if __name__ == "__main__":
    unittest.main()
