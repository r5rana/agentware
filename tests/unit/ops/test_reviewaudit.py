"""`reviewaudit`'s own unit file — task 99 wave 5, landed with the paydown.

`agentware_lib.ops.reviewaudit` drives ONE adversarial audit round: fan out a
reviewer per dimension, adversarially VERIFY every finding, then CRITICIZE the
coverage. Until this file the module had no owning test. The corpus was not
silent about it — `test_review_orchestrator`, `test_review_failclosed` and
`test_review_verify_critic` all drive `_review_run_audit` — but a file named for
a BEHAVIOUR is not a file that reddens when this module alone breaks, and none of
them addressed the three stages as separable units, because until this wave they
were not separable.

This wave split the round into `_review_fanout_reviewers`,
`_review_verify_findings` and `_review_run_critic` (26 branches / 139 lines -> 4
/ 41). The seams are the properties below, and the reason each one is worth a
direct test is the module's own warning: the dangerous failure here is "CLEAN",
not "error". Every property below is a way for a DEGRADED round to be
indistinguishable from a clean one at the call site.

  FANOUT/OK        — `reviewers_ok` counts only the dimensions whose reviewer
                     produced a FULLY auditable result. It is what
                     `audit_incomplete` compares against, so a reviewer wrongly
                     counted here is a gate that passes an un-audited diff.
  FANOUT/SOFTFAIL  — no usable findings RESPONSE (empty, prose, or a parsed
                     object with no findings list) is a reviewer that did NOT
                     run: malformed +1, NAMED on the stream, never counted ok.
  FANOUT/ITEMS     — a findings LIST holding non-objects is a DIFFERENT
                     degradation: the dropped items are counted, the reviewer is
                     not counted ok, and the well-formed siblings in the same
                     list are still kept and verified (a real defect is never
                     lost to a noisy neighbour).
  FANOUT/SCHEMA    — a finding missing required fields is REPORTED via
                     `_schema_errors`, never dropped.
  VERIFY/DEGRADE   — a response with no usable verdict degrades to 'uncertain'
                     and NEVER to 'confirmed', so a garbled verifier cannot
                     manufacture a blocker. The `{"error": ...}` envelope PARSES,
                     which is what makes it the dangerous shape.
  VERIFY/CLOSED    — the same degradation on a HIGH/MEDIUM finding also counts
                     `verify_unavailable` and stamps `_verify_unavailable`, which
                     is the fail-CLOSED half; on a low finding it does not.
  VERIFY/RATIONALE — only a non-empty STRING rationale is kept, stripped. A dict
                     or an all-whitespace one is dropped rather than stringified.
  CRITIC/REPORTONLY— a critic with no usable payload is NAMED and degrades to a
                     no-op; it never adds a finding and never blocks.
  CRITIC/UNCERTAIN — critic additions default to 'uncertain' and an addition
                     that already carries a verdict keeps its own.
  ROUND/INCOMPLETE — `audit_incomplete` is the OR of three disjuncts, and each
                     one alone is sufficient.
  ROUND/FANOUT     — the dimension list is truncated to `cfg["fanout"]`, and
                     `reviewers_total` follows the truncation, not the registry.

Hermetic: the `_review_invoke_agent` seam is monkeypatched; no CLI, no network,
no tempdirs, stdlib unittest only.
"""

from tests._paths import TESTS_DIR

import io
import json
import os
import sys
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

from agentware_lib.ops import reviewaudit  # noqa: E402
from agentware_lib.ops.reviewaudit import (  # noqa: E402
    _review_fanout_reviewers,
    _review_run_audit,
    _review_run_critic,
    _review_verify_findings,
)

CFG = {"model": "opus", "effort": "max", "fanout": 5, "max_rounds": 3}


def _finding(summary="s", severity="high", dimension="correctness", **extra):
    f = {"summary": summary, "severity": severity, "dimension": dimension,
         "file": "a.py", "line": 1, "failure_scenario": "f(None) raises"}
    f.update(extra)
    return f


def _seam(replies):
    """Stand in for `_review_invoke_agent`. `replies` maps a role ('reviewer:<k>',
    'verify', 'critic') to raw stdout text, with '*' as the reviewer default."""
    def fake(role, persona, prompt, cfg, **kw):
        if role in replies:
            return replies[role]
        if role.startswith("reviewer:"):
            return replies.get("*", "")
        return replies.get(role.split(":", 1)[0], "")
    return fake


class _SeamCase(unittest.TestCase):
    """Installs a scripted agent seam and captures the diagnostic stream."""

    def setUp(self):
        self._orig = reviewaudit._review_invoke_agent
        self.stream = io.StringIO()
        self.addCleanup(self._restore)

    def _restore(self):
        reviewaudit._review_invoke_agent = self._orig

    def install(self, replies):
        reviewaudit._review_invoke_agent = _seam(replies)


# --- FANOUT -----------------------------------------------------------------

class FanoutReviewersTest(_SeamCase):

    def test_a_well_formed_reviewer_counts_as_run(self):
        """FANOUT/OK: a parseable findings list is one auditable reviewer."""
        self.install({"*": json.dumps({"findings": [_finding("a")]})})
        found, malformed, ok = _review_fanout_reviewers(
            ["correctness", "test-quality"], "d", "c", CFG, stream=self.stream)
        self.assertEqual((len(found), malformed, ok), (2, 0, 2))
        self.assertEqual(self.stream.getvalue(), "")

    def test_prose_is_a_reviewer_that_did_not_run(self):
        """FANOUT/SOFTFAIL: unparseable output is malformed AND uncounted."""
        self.install({"*": "I could not analyse the diff."})
        found, malformed, ok = _review_fanout_reviewers(
            ["correctness"], "d", "c", CFG, stream=self.stream)
        self.assertEqual((found, malformed, ok), ([], 1, 0))
        self.assertIn("reviewer:correctness", self.stream.getvalue())

    def test_a_parsed_object_without_findings_is_also_a_soft_fail(self):
        """FANOUT/SOFTFAIL: the exit-0 error envelope PARSES. Counting it as a
        clean reviewer is the fail-OPEN direction, so it must not."""
        self.install({"*": json.dumps({"error": "rate limited"})})
        found, malformed, ok = _review_fanout_reviewers(
            ["correctness"], "d", "c", CFG, stream=self.stream)
        self.assertEqual((found, malformed, ok), ([], 1, 0))
        self.assertIn("rate limited", self.stream.getvalue())

    def test_a_findings_list_holding_junk_keeps_its_good_siblings(self):
        """FANOUT/ITEMS: the dropped items are counted and named, the reviewer
        is not counted ok, and the well-formed finding beside them SURVIVES."""
        self.install({"*": json.dumps(
            {"findings": ["a bare string", _finding("real"), 7, None]})})
        found, malformed, ok = _review_fanout_reviewers(
            ["correctness"], "d", "c", CFG, stream=self.stream)
        self.assertEqual([f["summary"] for f in found], ["real"])
        self.assertEqual((malformed, ok), (3, 0))
        self.assertIn("correctness", self.stream.getvalue())

    def test_the_two_degradations_are_distinguishable(self):
        """FANOUT/SOFTFAIL vs FANOUT/ITEMS: one round carrying BOTH must count
        them separately — 1 for the no-response reviewer plus 1 per dropped
        item — or the log tells the reader the wrong thing went wrong."""
        self.install({"reviewer:correctness": "prose",
                      "reviewer:test-quality": json.dumps(
                          {"findings": ["junk", _finding("real")]})})
        found, malformed, ok = _review_fanout_reviewers(
            ["correctness", "test-quality"], "d", "c", CFG, stream=self.stream)
        self.assertEqual((len(found), malformed, ok), (1, 2, 0))
        text = self.stream.getvalue()
        self.assertIn("reviewer:correctness", text)
        self.assertIn("reviewer:test-quality", text)

    def test_a_finding_missing_required_fields_is_reported_not_dropped(self):
        """FANOUT/SCHEMA: `_schema_errors` carries the missing field names."""
        self.install({"*": json.dumps({"findings": [{"summary": "no severity"}]})})
        found, _malformed, ok = _review_fanout_reviewers(
            ["correctness"], "d", "c", CFG, stream=self.stream)
        self.assertEqual(len(found), 1)
        self.assertIn("severity", found[0]["_schema_errors"])
        self.assertEqual(ok, 1)

    def test_an_empty_findings_list_is_a_clean_reviewer(self):
        """FANOUT/OK: reporting nothing wrong is not a degradation."""
        self.install({"*": json.dumps({"findings": []})})
        found, malformed, ok = _review_fanout_reviewers(
            ["correctness"], "d", "c", CFG, stream=self.stream)
        self.assertEqual((found, malformed, ok), ([], 0, 1))
        self.assertEqual(self.stream.getvalue(), "")

    def test_no_dimensions_asks_no_reviewer_anything(self):
        """FANOUT/OK: an empty key list spawns nothing at all."""
        calls = []

        def counting(role, persona, prompt, cfg, **kw):
            calls.append(role)
            return ""
        reviewaudit._review_invoke_agent = counting
        found, malformed, ok = _review_fanout_reviewers(
            [], "d", "c", CFG, stream=self.stream)
        self.assertEqual((found, malformed, ok, calls), ([], 0, 0, []))


# --- VERIFY -----------------------------------------------------------------

class VerifyFindingsTest(_SeamCase):

    def test_a_usable_verdict_is_kept_lowercased(self):
        """VERIFY/DEGRADE: the happy path stamps the verifier's own verdict."""
        self.install({"verify": json.dumps({"verdict": "  CONFIRMED  "})})
        findings = [_finding("a")]
        n = _review_verify_findings(findings, "d", CFG, stream=self.stream)
        self.assertEqual((findings[0]["verdict"], n), ("confirmed", 0))

    def test_no_json_degrades_to_uncertain_never_confirmed(self):
        """VERIFY/DEGRADE: a failed verifier must not manufacture a verdict."""
        self.install({"verify": ""})
        findings = [_finding("a")]
        _review_verify_findings(findings, "d", CFG, stream=self.stream)
        self.assertEqual(findings[0]["verdict"], "uncertain")

    def test_an_off_enum_verdict_degrades_too(self):
        """VERIFY/DEGRADE: 'probably' is not one of REVIEW_VERDICTS, and a
        verdict the enum does not know is not a verdict."""
        self.install({"verify": json.dumps({"verdict": "probably"})})
        findings = [_finding("a")]
        _review_verify_findings(findings, "d", CFG, stream=self.stream)
        self.assertEqual(findings[0]["verdict"], "uncertain")

    def test_the_error_envelope_fails_a_blocking_finding_closed(self):
        """VERIFY/CLOSED: it PARSES, so the unavailable branch is the only thing
        standing between it and a silent uncertain-and-PASS."""
        self.install({"verify": json.dumps({"error": "rate limited"})})
        findings = [_finding("a", severity="high")]
        n = _review_verify_findings(findings, "d", CFG, stream=self.stream)
        self.assertEqual(n, 1)
        self.assertTrue(findings[0]["_verify_unavailable"])
        self.assertIn("verify:correctness", self.stream.getvalue())

    def test_a_low_finding_degrades_without_failing_the_round(self):
        """VERIFY/CLOSED: only BLOCKING severities count as unavailable — a low
        finding nobody could verify must not block a release on its own."""
        self.install({"verify": ""})
        findings = [_finding("a", severity="low")]
        n = _review_verify_findings(findings, "d", CFG, stream=self.stream)
        self.assertEqual(n, 0)
        self.assertNotIn("_verify_unavailable", findings[0])
        self.assertEqual(findings[0]["verdict"], "uncertain")

    def test_both_blocking_severities_count(self):
        """VERIFY/CLOSED: medium is blocking too, so the count is 2 not 1."""
        self.install({"verify": ""})
        findings = [_finding("a", severity="high"),
                    _finding("b", severity="medium"),
                    _finding("c", severity="low")]
        self.assertEqual(
            _review_verify_findings(findings, "d", CFG, stream=self.stream), 2)

    def test_only_a_non_empty_string_rationale_survives(self):
        """VERIFY/RATIONALE: stripped when usable, absent otherwise — never
        stringified, because a dict rendered into the report is noise a reader
        would read as the verifier's reasoning."""
        self.install({"verify": json.dumps(
            {"verdict": "confirmed", "verdict_rationale": "  reproduced  "})})
        findings = [_finding("a")]
        _review_verify_findings(findings, "d", CFG, stream=self.stream)
        self.assertEqual(findings[0]["verdict_rationale"], "reproduced")

        for bad in ({"not": "a string"}, "   ", 7, None):
            self.install({"verify": json.dumps(
                {"verdict": "confirmed", "verdict_rationale": bad})})
            findings = [_finding("a")]
            _review_verify_findings(findings, "d", CFG, stream=self.stream)
            self.assertNotIn("verdict_rationale", findings[0],
                             "rationale %r must be dropped" % (bad,))

    def test_each_finding_gets_its_own_verifier(self):
        """VERIFY/DEGRADE: the pass is per-finding and INDEPENDENT, so three
        findings spawn three verify agents, not one shared verdict."""
        seen = []

        def counting(role, persona, prompt, cfg, **kw):
            seen.append(role)
            return json.dumps({"verdict": "refuted"})
        reviewaudit._review_invoke_agent = counting
        _review_verify_findings([_finding("a"), _finding("b"), _finding("c")],
                                "d", CFG, stream=self.stream)
        self.assertEqual(seen, ["verify"] * 3)


# --- CRITIC -----------------------------------------------------------------

class RunCriticTest(_SeamCase):

    def test_gaps_and_additions_are_both_read(self):
        """CRITIC/UNCERTAIN: additions are normalized findings."""
        self.install({"critic": json.dumps(
            {"gaps": ["nobody ran concurrency"],
             "additional_findings": [_finding("added")]})})
        critic = _review_run_critic(["correctness"], "d", "c", [], CFG,
                                    stream=self.stream)
        self.assertEqual(critic["gaps"], ["nobody ran concurrency"])
        self.assertEqual(critic["additional_findings"][0]["summary"], "added")

    def test_an_addition_defaults_to_uncertain(self):
        """CRITIC/UNCERTAIN: an addition never had its own verify pass, so it is
        called out but can never be CONFIRMED-blocking on the critic's say-so."""
        self.install({"critic": json.dumps(
            {"gaps": [], "additional_findings": [_finding("added")]})})
        critic = _review_run_critic([], "d", "c", [], CFG, stream=self.stream)
        self.assertEqual(critic["additional_findings"][0]["verdict"],
                         "uncertain")

    def test_an_addition_keeps_a_verdict_it_already_carries(self):
        """CRITIC/UNCERTAIN: setdefault, not assignment."""
        self.install({"critic": json.dumps({"gaps": [], "additional_findings": [
            _finding("added", verdict="refuted")]})})
        critic = _review_run_critic([], "d", "c", [], CFG, stream=self.stream)
        self.assertEqual(critic["additional_findings"][0]["verdict"], "refuted")

    def test_non_object_additions_are_dropped(self):
        """CRITIC/UNCERTAIN: the normalizer returns None for a bare string."""
        self.install({"critic": json.dumps({
            "gaps": [], "additional_findings": ["a string", None,
                                                _finding("kept")]})})
        critic = _review_run_critic([], "d", "c", [], CFG, stream=self.stream)
        self.assertEqual([f["summary"] for f in critic["additional_findings"]],
                         ["kept"])

    def test_prose_degrades_to_a_named_no_op(self):
        """CRITIC/REPORTONLY: named on the stream, empty payload, no crash."""
        self.install({"critic": "the critic returned prose"})
        critic = _review_run_critic([], "d", "c", [], CFG, stream=self.stream)
        self.assertEqual(critic, {"gaps": [], "additional_findings": []})
        self.assertIn("critic", self.stream.getvalue())

    def test_a_gaps_only_payload_is_usable_and_silent(self):
        """CRITIC/REPORTONLY: EITHER list makes the payload usable, so a critic
        that named gaps and added nothing is not a degraded critic."""
        self.install({"critic": json.dumps({"gaps": ["one gap"]})})
        critic = _review_run_critic([], "d", "c", [], CFG, stream=self.stream)
        self.assertEqual(critic["gaps"], ["one gap"])
        self.assertEqual(self.stream.getvalue(), "")

    def test_an_additions_only_payload_is_usable_and_silent(self):
        """CRITIC/REPORTONLY: the other half of the same disjunction."""
        self.install({"critic": json.dumps(
            {"additional_findings": [_finding("added")]})})
        critic = _review_run_critic([], "d", "c", [], CFG, stream=self.stream)
        self.assertEqual(critic["gaps"], [])
        self.assertEqual(len(critic["additional_findings"]), 1)
        self.assertEqual(self.stream.getvalue(), "")

    def test_a_non_list_gaps_value_becomes_an_empty_list(self):
        """CRITIC/REPORTONLY: the report renders `gaps` directly, so a string
        here would print character by character."""
        self.install({"critic": json.dumps(
            {"gaps": "one big string",
             "additional_findings": [_finding("added")]})})
        critic = _review_run_critic([], "d", "c", [], CFG, stream=self.stream)
        self.assertEqual(critic["gaps"], [])


# --- THE ROUND --------------------------------------------------------------

class RunAuditRoundTest(_SeamCase):

    def _round(self, replies, **kw):
        self.install(replies)
        return _review_run_audit("d", "c", kw.pop("cfg", CFG),
                                 stream=self.stream, **kw)

    def test_a_clean_round_is_complete_and_unblocked(self):
        """ROUND/INCOMPLETE: none of the three disjuncts holds."""
        res = self._round({"*": json.dumps({"findings": []}),
                           "critic": json.dumps({"gaps": []})})
        self.assertFalse(res["audit_incomplete"])
        self.assertEqual(res["confirmed_blocking"], 0)
        self.assertEqual((res["reviewers_ok"], res["reviewers_total"]), (5, 5))

    def test_an_empty_dimension_list_falls_back_to_the_registry(self):
        """ROUND/FANOUT: `dimensions or REVIEW_DIMENSION_KEYS` — an EMPTY list
        is falsy, so it selects the full registry rather than auditing nothing.
        Pinned because the two readings differ in the direction that matters:
        'audit everything' versus 'audit nothing and call it complete'."""
        res = self._round({"*": json.dumps({"findings": []}),
                           "critic": json.dumps({"gaps": []})}, dimensions=[])
        self.assertEqual(res["reviewers_total"], 5)
        self.assertFalse(res["audit_incomplete"])

    def test_zero_dimensions_is_incomplete_on_its_own(self):
        """ROUND/INCOMPLETE: disjunct 1. A round that audited NOTHING is the
        purest form of 'clean' meaning 'never ran'. Only a fanout of zero can
        reach it — the CLI clamps that to 1, so this is a direct-caller path and
        the reason the disjunct is not dead code."""
        res = self._round({"critic": json.dumps({"gaps": []})},
                          cfg=dict(CFG, fanout=0))
        self.assertTrue(res["audit_incomplete"])
        self.assertEqual((res["reviewers_ok"], res["reviewers_total"]), (0, 0))

    def test_one_soft_failed_reviewer_is_incomplete_on_its_own(self):
        """ROUND/INCOMPLETE: disjunct 2, with nothing unverifiable anywhere."""
        res = self._round({"reviewer:correctness": "prose",
                           "*": json.dumps({"findings": []}),
                           "critic": json.dumps({"gaps": []})})
        self.assertTrue(res["audit_incomplete"])
        self.assertEqual(res["verify_unavailable"], 0)
        self.assertEqual(res["reviewers_ok"], 4)

    def test_one_unverifiable_blocker_is_incomplete_on_its_own(self):
        """ROUND/INCOMPLETE: disjunct 3, with every reviewer counted ok."""
        res = self._round({"*": json.dumps({"findings": [_finding("a")]}),
                           "verify": json.dumps({"error": "rate limited"}),
                           "critic": json.dumps({"gaps": []})})
        self.assertTrue(res["audit_incomplete"])
        self.assertEqual(res["reviewers_ok"], res["reviewers_total"])
        self.assertEqual(res["verify_unavailable"], 5)

    def test_confirmed_blocking_counts_only_confirmed_high_or_medium(self):
        """ROUND/INCOMPLETE: the gate's exit-1 signal is the AND of verdict and
        severity, so a confirmed LOW and a refuted HIGH both count zero."""
        self.install({"reviewer:correctness": json.dumps({"findings": [
            _finding("high", severity="high"),
            _finding("low", severity="low")]}),
            "verify": json.dumps({"verdict": "confirmed"}),
            "critic": json.dumps({"gaps": []})})
        res = _review_run_audit("d", "c", CFG, dimensions=["correctness"],
                                stream=self.stream)
        self.assertEqual(res["confirmed_blocking"], 1)

    def test_fanout_truncates_the_dimension_list(self):
        """ROUND/FANOUT: and `reviewers_total` follows the truncation, so a
        2-dimension round is not reported as 3 of 5 reviewers missing."""
        cfg = dict(CFG, fanout=2)
        res = self._round({"*": json.dumps({"findings": []}),
                           "critic": json.dumps({"gaps": []})}, cfg=cfg)
        self.assertEqual(len(res["dimensions"]), 2)
        self.assertEqual(res["reviewers_total"], 2)
        self.assertFalse(res["audit_incomplete"])

    def test_an_explicit_dimension_list_is_also_truncated(self):
        """ROUND/FANOUT: the cap applies to the caller's list too, not only to
        the registry default."""
        cfg = dict(CFG, fanout=1)
        self.install({"*": json.dumps({"findings": []}),
                      "critic": json.dumps({"gaps": []})})
        res = _review_run_audit("d", "c", cfg,
                                dimensions=["correctness", "test-quality"],
                                stream=self.stream)
        self.assertEqual(res["dimensions"], ["correctness"])

    def test_the_envelope_echoes_the_resolved_axes(self):
        """ROUND/FANOUT: model/effort/fanout ride the result, because a clean
        report from a downgraded reviewer is not the same evidence."""
        cfg = dict(CFG, model="sonnet", effort="low", fanout=1)
        res = self._round({"*": json.dumps({"findings": []}),
                           "critic": json.dumps({"gaps": []})}, cfg=cfg)
        self.assertEqual((res["model"], res["effort"], res["fanout"]),
                         ("sonnet", "low", 1))

    def test_the_three_stages_run_in_order(self):
        """ROUND: the critic is asked LAST, after every finding has a verdict —
        its prompt summarises the findings, so an ordering that ran it first
        would hand it an empty round to criticise."""
        order = []

        def tracing(role, persona, prompt, cfg, **kw):
            order.append(role.split(":", 1)[0])
            if role.startswith("reviewer"):
                return json.dumps({"findings": [_finding("a")]})
            if role == "verify":
                return json.dumps({"verdict": "refuted"})
            return json.dumps({"gaps": []})
        reviewaudit._review_invoke_agent = tracing
        _review_run_audit("d", "c", CFG, dimensions=["correctness"],
                          stream=self.stream)
        self.assertEqual(order, ["reviewer", "verify", "critic"])


if __name__ == "__main__":
    unittest.main()
