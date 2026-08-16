"""agentware_lib.ops.reviewprompts -- the four review PROMPT builders (layer: ops).

Owns: the effort preamble (`_review_effort_preamble`) and the four prompts the
gate spawns agents with -- the per-dimension REVIEWER
(`_review_build_reviewer_prompt`), the per-finding VERIFIER
(`_review_build_verify_prompt`), the coverage CRITIC
(`_review_build_critic_prompt`) and the FIXER (`_review_build_fixer_prompt`).
Does NOT own: the dimensions and severities the prompts quote ->
ops.reviewschema; the model/effort axes -> ops.reviewconfig; the spawning itself
-> features.reviewagent; the fencing/escaping primitives ->
features.reviewoutput; the fan-out that sequences these -> ops.reviewaudit.

FOUR PROMPTS, FOUR DIFFERENT JOBS
---------------------------------
They are separate builders rather than one parameterized template because they
ask for opposite things, and blurring them is what produces an agreeable review:

  * REVIEWER — find problems along ONE dimension. Scoped narrowly on purpose: a
    reviewer asked to find everything finds the obvious thing and stops.
  * VERIFIER — try to REFUTE a specific finding. Its default must be skepticism;
    a verifier that merely re-reads the finding will confirm it, and a confirmed
    wrong finding is worse than no finding, because it gets acted on.
  * CRITIC — given what was already covered, say what is MISSING. This is the
    only prompt that can grow the audit, and it exists because the reviewers'
    blind spots are correlated: they will not report the dimension nobody asked
    about.
  * FIXER — repair a confirmed finding. It receives findings that already
    survived verification, never raw reviewer output.

THE DIFF AND THE CRITERIA ARE UNTRUSTED
---------------------------------------
Both are fenced before interpolation (R-SEC-02). A diff is attacker-influenced
text in the general case -- anyone who can land a commit can put instructions in
it -- and a prompt that pastes it raw is a prompt that can be told to report
"no findings". Fencing is not cosmetic here; it is the boundary.

Layer deps: ops -> ops (`reviewschema` for dimensions/severities, `reviewconfig`
for the effort axis), ops -> features (`reviewoutput._review_fence` /
`_review_untrusted_inline`), all reached BY NAME.
Determinism: pure string building -- same inputs, same prompt, every run (INV-1),
which is what makes a review round reproducible from its recorded artifacts.
"""

import json

from agentware_lib.features.reviewoutput import (
    _review_fence,
    _review_untrusted_inline,
)
from agentware_lib.ops.reviewconfig import REVIEW_DEFAULT_EFFORT
from agentware_lib.ops.reviewschema import (
    REVIEW_DIMENSIONS_BY_KEY,
    REVIEW_FINDING_REQUIRED_FIELDS,
)


def _review_effort_preamble(cfg):
    """One-line effort directive prepended to every reviewer/verify/critic prompt.
    The CLI has no effort flag, so this is how 'max effort' reaches the agent."""
    effort = (cfg or {}).get("effort") or REVIEW_DEFAULT_EFFORT
    return ("Reason at %s effort — this is a self-extension review gate; a defect "
            "you miss ships into every future run. Do not rush." % effort)


def _review_build_reviewer_prompt(dimension, diff_text, criteria_text, cfg):
    """Prompt for one dimension reviewer. Injects the dimension's hostile `focus`
    lens verbatim + the fenced diff and acceptance criteria."""
    dim = REVIEW_DIMENSIONS_BY_KEY.get(dimension, {"key": dimension,
                                                   "title": dimension, "focus": ""})
    return "\n\n".join([
        _review_effort_preamble(cfg),
        "You are the '%s' dimension reviewer (%s)." % (dim["key"], dim["title"]),
        "Your hostile lens: %s" % dim["focus"],
        "Review the shipped self-extension DIFF against the ACCEPTANCE CRITERIA "
        "below. Construct NEW adversarial inputs; do NOT re-run the author's "
        "tests as evidence. Return ONLY the single JSON object your persona "
        "specifies, with dimension=\"%s\"." % dim["key"],
        _review_fence("DIFF", diff_text),
        _review_fence("ACCEPTANCE_CRITERIA", criteria_text),
    ])


def _review_build_verify_prompt(finding, diff_text, cfg):
    """Prompt for the INDEPENDENT adversarial-verify pass on one finding (Q6):
    default-REFUTE. Returns {verdict, verdict_rationale}."""
    fjson = json.dumps({k: finding.get(k) for k in REVIEW_FINDING_REQUIRED_FIELDS},
                       ensure_ascii=False, indent=2)
    return "\n\n".join([
        _review_effort_preamble(cfg),
        "An earlier reviewer filed the FINDING below against this diff. You did "
        "NOT write it and you did NOT write the code. Try HARD to REFUTE the "
        "finding: reproduce its failure_scenario against the actual diff/source. "
        "Default to verdict=\"refuted\" unless you can concretely reproduce the "
        "defect (verdict=\"confirmed\"); use \"uncertain\" only when the evidence "
        "is genuinely ambiguous.",
        "Return ONLY a single JSON object: "
        "{\"verdict\": \"confirmed|refuted|uncertain\", "
        "\"verdict_rationale\": \"<concrete reason>\"}.",
        _review_fence("FINDING", fjson),
        _review_fence("DIFF", diff_text),
    ])


def _review_build_critic_prompt(covered_keys, diff_text, criteria_text,
                                findings, cfg):
    """Completeness-critic prompt (Q7): name any un-run dimension/modality or
    unverified claim. Returns {gaps:[...], additional_findings:[...]}."""
    summary = json.dumps(
        [{"dimension": f.get("dimension"), "severity": f.get("severity"),
          "summary": f.get("summary"), "verdict": f.get("verdict")}
         for f in findings], ensure_ascii=False, indent=2)
    return "\n\n".join([
        _review_effort_preamble(cfg),
        "You are the COMPLETENESS CRITIC. Dimensions reviewed this round: %s. "
        "The findings so far are summarized below. Name anything SKIPPED: an "
        "acceptance criterion no reviewer exercised, a modality/input class not "
        "tried, or a claim asserted but not verified. If you can construct a NEW "
        "concrete defect the reviewers missed, add it to additional_findings."
        % ", ".join(covered_keys),
        "Return ONLY a single JSON object: {\"gaps\": [\"<gap>\", ...], "
        "\"additional_findings\": [<finding objects, same schema as a reviewer "
        "finding>]}.",
        _review_fence("FINDINGS_SO_FAR", summary),
        _review_fence("DIFF", diff_text),
        _review_fence("ACCEPTANCE_CRITERIA", criteria_text),
    ])


def _review_build_fixer_prompt(findings, diff_text, criteria_text, cfg):
    """Prompt for the auto-fix pass (persona agentware-execution). Each confirmed
    finding must be fixed WITH a regression test that FAILS before + PASSES after
    (TDD). Finding/diff/criteria text is FENCED as analyzed data (R-SEC-02)."""
    fjson = json.dumps(
        [{k: f.get(k) for k in REVIEW_FINDING_REQUIRED_FIELDS + ("line",)}
         for f in findings], ensure_ascii=False, indent=2)
    return "\n\n".join([
        _review_effort_preamble(cfg),
        "You are the REMEDIATION fixer. The CONFIRMED findings below survived an "
        "adversarial audit of a self-extension diff. Fix EACH one. For EVERY fix "
        "you MUST add a regression test that FAILS before your change and PASSES "
        "after (TDD) — a fix with no regression test is REJECTED as unverified. "
        "After you fix, the gate chain (affected tests + `steering lint` + `gate "
        "release`) is re-run and your fixes are RE-AUDITED by a FRESH reviewer, "
        "so introduce NO new defect. If a finding is genuinely not fixable, mark "
        "it unfixable with a reason; do NOT claim a fix you did not verify.",
        "Return ONLY a single JSON object: {\"fixes\": [{\"index\": <int 0-based "
        "position of the finding in the list below>, \"status\": "
        "\"fixed|unfixable|deferred\", \"regression_test\": \"<module::test that "
        "locks it — REQUIRED whenever status is fixed>\", \"notes\": \"<what you "
        "changed>\"}]}.",
        _review_fence("CONFIRMED_FINDINGS", fjson),
        _review_fence("DIFF", diff_text),
        _review_fence("ACCEPTANCE_CRITERIA", criteria_text),
    ])


_EXPORTS = [
    "_review_effort_preamble",
    "_review_build_reviewer_prompt",
    "_review_build_verify_prompt",
    "_review_build_critic_prompt",
    "_review_build_fixer_prompt",
]

__all__ = list(_EXPORTS)
