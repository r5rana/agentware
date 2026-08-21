"""agentware_lib.ops.reviewaudit -- the adversarial audit FAN-OUT and the `review` verb (layer: ops).

Owns: the round driver (`_review_run_audit`) and its three STAGE functions --
the reviewer fan-out (`_review_fanout_reviewers`), the adversarial verify pass
(`_review_verify_findings`) and the coverage critic (`_review_run_critic`) --
plus the diff resolver
(`_review_resolve_diff`), the text renderer (`_review_print_text`) and the
`review` handler + registrar (`cmd_review` / `register_review`).
Does NOT own: the vocabulary -> ops.reviewschema; the config axes and the skip
ledger -> ops.reviewconfig; the prompts -> ops.reviewprompts; the degradation
diagnostics -> ops.reviewdiag; agent spawning, retries and the timeout breaker ->
features.reviewagent; the untrusted-output readers -> features.reviewoutput; the
remediation loop -> ops.reviewremediate; the report and worklog callout ->
ops.reviewreport.

REVIEW, VERIFY, CRITICIZE -- THE THREE STAGES AND WHY EACH IS SEPARATE
---------------------------------------------------------------------
1. FAN OUT one reviewer per dimension. Narrow scopes on purpose: a single
   reviewer told to "find everything" reports the first obvious thing.
2. VERIFY each finding with an independent agent whose job is to REFUTE it. This
   is the stage that earns the gate its name. Without it the output is a list of
   plausible claims, and a plausible-but-wrong finding is worse than silence,
   because someone will act on it.
3. CRITICIZE the coverage: given everything already found, what is MISSING? The
   reviewers' blind spots are correlated -- none of them reports the dimension
   nobody asked about -- so completeness needs its own pass.

Collapsing any two of these produces a gate that agrees with itself.

THE DANGEROUS FAILURE IS "CLEAN", NOT "ERROR"
---------------------------------------------
Every degradation path here is reported rather than swallowed. A reviewer that
crashes, times out, or returns unparseable output contributes zero findings --
indistinguishable at the call site from one that found nothing wrong. The round
soft-fails (one broken reviewer must not discard the others' findings) and emits
a diagnostic naming the role and the failure class, so a degraded round never
reads as a clean one.

Layer deps: ops -> core (`gitio._git`, `paths.REPO_ROOT`), ops -> ops
(`reviewschema`, `reviewconfig`, `reviewprompts`, `reviewdiag`), ops -> features
(`reviewagent._review_invoke_agent` + `REVIEW_REVIEWER_PERSONA`,
`reviewoutput._review_extract_json` and the fencing helpers). The registrar is
declared in this module's `__verbs__` and resolved from there, not `_EXPORTS`.
Security: the diff and the acceptance criteria are UNTRUSTED (R-SEC-02) and reach
the prompts fenced, never as executable or instruction lines.
"""

import argparse
import json
import os
import subprocess
import sys

from agentware_lib.core.gitio import _git
from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.features.reviewagent import (
    REVIEW_REVIEWER_PERSONA,
    _review_invoke_agent,
)
from agentware_lib.features.reviewoutput import (
    _review_extract_json,
    _review_fence,
    _review_one_line,
    _review_untrusted_inline,
)
from agentware_lib.ops.reviewconfig import (
    REVIEW_DEFAULT_EFFORT,
    REVIEW_DEFAULT_MAX_ROUNDS,
    REVIEW_DEFAULT_MODEL,
    REVIEW_SKIP_ENV,
    _review_emit_pinning_warnings,
    _review_record_skip,
    _review_resolve_config,
    _review_skip_requested,
)
from agentware_lib.ops.reviewdiag import (
    _review_emit_malformed_items_diagnostic,
    _review_emit_soft_fail_diagnostic,
)
from agentware_lib.ops.reviewprompts import (
    _review_build_critic_prompt,
    _review_build_reviewer_prompt,
    _review_build_verify_prompt,
)
from agentware_lib.ops.reviewschema import (
    REVIEW_BLOCKING_SEVERITIES,
    REVIEW_DIMENSION_KEYS,
    REVIEW_DIMENSIONS,
    REVIEW_DIMENSIONS_BY_KEY,
    REVIEW_VERDICTS,
    _review_is_blocking,
    _review_normalize_finding,
)


def _review_fanout_reviewers(keys, diff_text, criteria_text, cfg, stream=None):
    """STAGE 1 of the round — fan out one READ-ONLY reviewer per dimension and
    collect their normalized findings. Returns
    `(raw_findings, malformed, reviewers_ok)`.

    `reviewers_ok` counts ONLY the dimensions whose reviewer produced a FULLY
    auditable result. It is deliberately not `len(keys)` minus the crashes: a
    reviewer that returned no usable findings RESPONSE, and one that returned a
    findings LIST holding non-objects, are two DIFFERENT degradations and
    neither counts, which is what lets `audit_incomplete` fail the gate CLOSED
    instead of reading a degraded round as a clean one. Both are NAMED on
    `stream` with their raw-output snippet, because the dangerous failure here
    is "clean", not "error"."""
    raw_findings = []
    malformed = 0
    reviewers_ok = 0   # dimensions whose reviewer actually RAN (parseable output)
    for key in keys:
        prompt = _review_build_reviewer_prompt(key, diff_text, criteria_text, cfg)
        out = _review_invoke_agent("reviewer:%s" % key, REVIEW_REVIEWER_PERSONA,
                                   prompt, cfg)
        obj = _review_extract_json(out)
        items = obj.get("findings") if isinstance(obj, dict) else None
        if not isinstance(items, list):
            # No usable findings RESPONSE: empty / non-JSON / OR a valid-but-non-
            # findings object ({} or {"error": "rate limited"}) that a soft-failed
            # reviewer (exit 0, HTTP-200 error envelope) can emit. The reviewer did
            # NOT produce an auditable result, so it does NOT count toward
            # reviewers_ok — audit_incomplete then fails the gate CLOSED rather than
            # treating a soft-failed reviewer as a clean pass. NEVER silent: name
            # WHICH reviewer and echo its raw-output snippet so the log always
            # carries a per-reviewer reason, not only the vague INCOMPLETE banner
            # (a clean-exit soft-fail emits no spawn diagnostic — this is its
            # only trace).
            _review_emit_soft_fail_diagnostic("reviewer:%s" % key, out,
                                              stream=stream)
            malformed += 1
            continue
        dropped = sum(1 for raw in items if not isinstance(raw, dict))
        if dropped:
            # FAIL-CLOSED + NEVER silent (remediation round 8): a findings
            # LIST whose items are not objects (strings / null — the LLM
            # persona-drift shape this plan's history documents) means the
            # reviewer's reported defect text was DROPPED at normalize time.
            # Counting such a reviewer toward reviewers_ok used to be the one
            # drift shape that was simultaneously SILENT and FAIL-OPEN: the
            # gate exited 0 GREEN with zero log trace of the dropped text.
            # The reviewer did NOT produce a fully-auditable result, so it
            # does NOT count as run -> audit_incomplete -> cmd_review exit 3.
            # Any well-formed finding dicts in the same list are still kept
            # and verified in stage 2 (a real defect is never lost).
            malformed += dropped
            _review_emit_malformed_items_diagnostic(
                "reviewer:%s" % key, dropped, out, stream=stream)
        else:
            reviewers_ok += 1
        for raw in items:
            finding, errs = _review_normalize_finding(raw, dimension=key)
            if finding is None:
                continue  # non-object item — already counted in `dropped`
            if errs:
                finding["_schema_errors"] = errs
            raw_findings.append(finding)
    return raw_findings, malformed, reviewers_ok


def _review_verify_findings(raw_findings, diff_text, cfg, stream=None):
    """STAGE 2 of the round — the adversarial-verify pass: an INDEPENDENT agent
    per finding, prompted to default-REFUTE. Stamps `verdict` (and, when the
    verifier supplied a usable one, `verdict_rationale`) onto each finding IN
    PLACE and returns `verify_unavailable`: the count of high/medium findings
    whose verifier could NOT produce a verdict.

    This is the stage that earns the gate its name, and the return value is the
    fail-CLOSED half of it. A verify response without a usable verdict degrades
    to 'uncertain' — NEVER 'confirmed', so a garbled verifier can never
    manufacture a blocker — but for a BLOCKING-severity finding, silently
    clearing it to uncertain-and-pass would be the fail-OPEN direction, so it is
    counted here and the caller marks the audit incomplete."""
    verify_unavailable = 0   # high/med findings whose verifier could NOT run
    for finding in raw_findings:
        vout = _review_invoke_agent(
            "verify", REVIEW_REVIEWER_PERSONA,
            _review_build_verify_prompt(finding, diff_text, cfg), cfg)
        vobj = _review_extract_json(vout) or {}
        verdict = str(vobj.get("verdict", "")).strip().lower()
        if verdict not in REVIEW_VERDICTS:
            # The verifier did NOT produce a usable verdict. That covers BOTH
            # shapes of a failed verify agent: no JSON at all (failed spawn ->
            # '' / clean-exit-0 prose), AND a valid-but-non-verdict JSON dict
            # — the exit-0 HTTP-200 error envelope ({"error": "rate limited"})
            # that would otherwise parse, skip the unavailable branch, and
            # silently downgrade a high/medium finding to 'uncertain-and-PASS'
            # (fail-OPEN). Either way no independent verdict exists, so a
            # blocking-severity finding is flagged unverifiable -> the CALLER
            # marks the audit INCOMPLETE (fail-CLOSED). NEVER silent: name
            # the verifier + its raw-output snippet (a clean-exit soft-fail
            # emits no spawn diagnostic — this is its only trace).
            _review_emit_soft_fail_diagnostic(
                "verify:%s" % (finding.get("dimension") or "?"), vout,
                stream=stream)
            if str(finding.get("severity", "")).strip().lower() \
                    in REVIEW_BLOCKING_SEVERITIES:
                finding["_verify_unavailable"] = True
                verify_unavailable += 1
            verdict = "uncertain"
        finding["verdict"] = verdict
        rationale = vobj.get("verdict_rationale")
        if isinstance(rationale, str) and rationale.strip():
            finding["verdict_rationale"] = rationale.strip()
    return verify_unavailable


def _review_run_critic(keys, diff_text, criteria_text, raw_findings, cfg,
                       stream=None):
    """STAGE 3 of the round — the completeness critic: names skipped
    dimensions/claims and MAY add findings. Returns
    `{gaps:[...], additional_findings:[...]}`.

    Its additions are surfaced as UNCERTAIN so they are called out but never
    silently block without their own verify pass; the reviewers' blind spots are
    correlated, so completeness needs its own pass, but a pass whose output
    skipped stage 2 must not be able to fail the gate on its own. Degrading to a
    no-op critic is report-only and is NEVER silent."""
    cout = _review_invoke_agent(
        "critic", REVIEW_REVIEWER_PERSONA,
        _review_build_critic_prompt(keys, diff_text, criteria_text,
                                    raw_findings, cfg), cfg)
    cobj = _review_extract_json(cout) or {}
    if not isinstance(cobj.get("gaps"), list) \
            and not isinstance(cobj.get("additional_findings"), list):
        # The critic produced NO usable payload — failed spawn ('' ), clean-
        # exit-0 prose, or an error envelope with neither a gaps list nor
        # additional_findings. Its degradation to a no-op critic is unchanged
        # (report-only), but it is NEVER silent: name it with its raw-output
        # snippet so the log carries a per-agent reason.
        _review_emit_soft_fail_diagnostic("critic", cout, stream=stream)
    gaps = cobj.get("gaps")
    critic = {"gaps": gaps if isinstance(gaps, list) else [],
              "additional_findings": []}
    for raw in (cobj.get("additional_findings") or []):
        finding, _errs = _review_normalize_finding(raw)
        if finding is None:
            continue
        finding.setdefault("verdict", "uncertain")
        critic["additional_findings"].append(finding)
    return critic


def _review_run_audit(diff_text, criteria_text, cfg, dimensions=None,
                      stream=None):
    """Run ONE audit round over the `_review_invoke_agent` seam and return a
    structured result:
      {model, effort, fanout, dimensions:[keys], findings:[...verified...],
       critic:{gaps, additional_findings}, confirmed_blocking:int, malformed:int}
    Pure orchestration (the seam is monkeypatched in tests). The three stages —
    fan out, adversarially VERIFY, then CRITICIZE the coverage — are three
    functions for the reason the module docstring gives: collapsing any two of
    them produces a gate that agrees with itself. A finding is
    CONFIRMED-blocking only when the verify pass returns verdict=confirmed AND
    severity in high/medium (_review_is_blocking). A verify response WITHOUT a
    usable verdict — no JSON at all, OR a parsed dict whose verdict is not in
    REVIEW_VERDICTS (e.g. the exit-0 error envelope {"error": "rate limited"})
    — degrades the verdict to 'uncertain' (NEVER confirmed, so a garbled
    verifier can never manufacture a blocker) AND, for a high/medium finding,
    counts as verify_unavailable so the audit fails CLOSED rather than
    silently clearing a real defect to uncertain-and-pass. Every soft-failed
    verify/critic agent is NAMED on `stream` with its raw-output snippet."""
    keys = [k for k in (dimensions or REVIEW_DIMENSION_KEYS)][: cfg["fanout"]]
    raw_findings, malformed, reviewers_ok = _review_fanout_reviewers(
        keys, diff_text, criteria_text, cfg, stream=stream)
    verify_unavailable = _review_verify_findings(
        raw_findings, diff_text, cfg, stream=stream)
    critic = _review_run_critic(keys, diff_text, criteria_text, raw_findings,
                                cfg, stream=stream)
    confirmed_blocking = sum(1 for f in raw_findings if _review_is_blocking(f))
    # FAIL-CLOSED signal (Q7 "nothing skipped"): the audit did NOT fully run if
    # there were no dimensions, ANY dimension's reviewer produced no usable output,
    # or a high/medium finding could not be verified. cmd_review returns nonzero on
    # this so the gate never SILENTLY passes an un-audited self-extension diff.
    audit_incomplete = (
        len(keys) == 0 or reviewers_ok < len(keys) or verify_unavailable > 0)
    return {
        "model": cfg["model"], "effort": cfg["effort"], "fanout": cfg["fanout"],
        "dimensions": keys, "findings": raw_findings, "critic": critic,
        "confirmed_blocking": confirmed_blocking, "malformed": malformed,
        "reviewers_ok": reviewers_ok, "reviewers_total": len(keys),
        "verify_unavailable": verify_unavailable,
        "audit_incomplete": audit_incomplete,
    }


def _review_resolve_diff(diff_range, cwd=None):
    """Resolve the unified diff text for a range (e.g. 'HEAD~1..HEAD' or a base
    ref). Returns '' when the range resolves and the diff is GENUINELY empty,
    and None when git itself FAILED (unresolvable/gc'd ref, shallow clone,
    git absent — rc != 0). The distinction is load-bearing for fail-closed:
    cmd_review used to swallow a git failure into '' and the audit then ran
    over an EMPTY diff — reviewers_ok came back clean and the gate stamped an
    UN-audited self-extension GREEN (exit 0). A gating caller must treat None
    as 'the audit cannot run over its intended diff' and BLOCK (exit 3); a
    non-gating caller (cmd_remediate) may coerce None to '' and degrade."""
    if not diff_range:
        return ""
    cp = _git(cwd or REPO_ROOT, "diff", diff_range)
    if cp.returncode != 0:
        return None
    return cp.stdout or ""


def _review_print_text(result):
    """Human-readable summary of an audit round (--format text)."""
    sys.stdout.write(
        "review --adversarial: model=%s effort=%s fanout=%d dims=%s\n" % (
            result["model"], result["effort"], result["fanout"],
            ",".join(result["dimensions"])))
    for f in result["findings"]:
        sys.stdout.write("  [%s/%s] %s:%s  %s  (%s)\n" % (
            f.get("severity", "?"), f.get("verdict", "?"),
            f.get("file", "?"), f.get("line", ""), f.get("summary", ""),
            f.get("dimension", "?")))
    gaps = result["critic"].get("gaps") or []
    if gaps:
        sys.stdout.write("  completeness-critic gaps: %d\n" % len(gaps))
    sys.stdout.write(
        "  confirmed blocking (high/med): %d  malformed: %d  reviewers_ok: %d/%d"
        "  incomplete: %s\n" % (
            result["confirmed_blocking"], result["malformed"],
            result.get("reviewers_ok", 0), result.get("reviewers_total", 0),
            bool(result.get("audit_incomplete"))))


def cmd_review(args):
    """`review --adversarial`: run ONE adversarial audit round on a diff and
    RETURN nonzero iff a CONFIRMED high/medium finding survives. Read-only; the
    bounded AUDIT→FIX→RE-AUDIT loop + STOP/escalate wiring is task 5."""
    if not getattr(args, "adversarial", False):
        sys.stderr.write("review: only --adversarial mode is supported\n")
        return 2
    cfg = _review_resolve_config()
    # Q2 pinning: a sub-Opus/sub-max model/effort emits a loud WARN (never silent).
    _review_emit_pinning_warnings(cfg)
    # Q8 escape hatch: honored ONLY by RECORDING it (never silent). The task-5 loop
    # invokes `review --adversarial --worklog <feature>/worklog.md`; when
    # AGENTWARE_SKIP_ADVERSARIAL_REVIEW is truthy this short-circuits the audit,
    # writing a self-closing `> DECISION:` skip marker + a loud notice, and returns
    # 0 so the gate BYPASSES (for CI/offline emergencies where Opus is unreachable).
    if _review_skip_requested():
        _review_record_skip(getattr(args, "worklog", None))
        return 0
    diff_text = ""
    diff_file = getattr(args, "diff_file", None)
    if diff_file:
        # errors='replace': the diff under audit is UNTRUSTED bytes — a strict
        # decode would crash the gate (exit 1, misread as confirmed findings)
        # on one latin-1 byte instead of auditing with U+FFFD substitution.
        try:
            with open(diff_file, encoding="utf-8", errors="replace") as f:
                diff_text = f.read()
        except OSError as exc:
            # FAIL-CLOSED: a REQUESTED diff source that cannot be read means
            # the audit CANNOT run over its intended diff. Degrading to an
            # empty diff here used to let the audit run over NOTHING and
            # stamp the un-audited self-extension GREEN (exit 0) — a mistyped
            # path or a $STATE_DIR temp file deleted by a concurrent run
            # silently bypassed the gate. Exit 3 = the same distinct
            # incomplete-audit code run_selfheal_review BLOCKS on.
            sys.stderr.write(
                "review: cannot read --diff-file: %s — the audit cannot run "
                "over its intended diff. FAILING CLOSED (nothing-skipped): "
                "the gate blocks rather than silently passing an un-audited "
                "diff.\n" % exc)
            return 3
    elif getattr(args, "diff_range", None):
        diff_text = _review_resolve_diff(args.diff_range)
        if diff_text is None:
            # FAIL-CLOSED: git could not resolve the requested range (gc'd or
            # absent base ref, shallow clone, git unavailable) — same class as
            # an unreadable --diff-file: the reviewers would see an EMPTY
            # diff, not the diff under audit. A range that RESOLVES to a
            # genuinely empty diff ('' with git rc 0) still audits and passes.
            sys.stderr.write(
                "review: cannot resolve --diff-range %r (git failed) — the "
                "audit cannot run over its intended diff. FAILING CLOSED "
                "(nothing-skipped): the gate blocks rather than silently "
                "passing an un-audited diff.\n" % args.diff_range)
            return 3
    criteria_text = ""
    acc_file = getattr(args, "acceptance_file", None)
    if acc_file:
        try:
            with open(acc_file, encoding="utf-8", errors="replace") as f:
                criteria_text = f.read()
        except OSError as exc:
            # FAIL-CLOSED (remediation round 10): the acceptance criteria are
            # the SPEC half of the review contract — a REQUESTED criteria
            # source that cannot be read (mistyped path, plan.md deleted or
            # replaced by a concurrent run between rounds) means the audit
            # cannot check the diff against the plan's criteria. Degrading to
            # empty criteria here used to let the audit run against NO spec
            # and stamp the self-extension GREEN (exit 0) — the same silent
            # fail-open class as an unreadable --diff-file. Exit 3 = the
            # distinct incomplete-audit code run_selfheal_review BLOCKS on;
            # its bash classifier keys on the line-anchored
            # '^review: cannot read --acceptance-file' prefix below.
            sys.stderr.write(
                "review: cannot read --acceptance-file: %s — the audit "
                "cannot check the diff against its acceptance criteria. "
                "FAILING CLOSED (nothing-skipped): the gate blocks rather "
                "than silently passing a diff audited against empty "
                "criteria.\n" % exc)
            return 3
    result = _review_run_audit(diff_text, criteria_text, cfg)
    if getattr(args, "format", "json") == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _review_print_text(result)
    # FAIL-CLOSED: if the audit did not fully run (a reviewer or a high/medium
    # verifier could not execute — e.g. Opus unreachable / timeout / nonzero exit),
    # return a DISTINCT nonzero code (3) so the gate BLOCKS + escalates rather than
    # silently shipping an un-audited diff. Exit 2 already means 'bad usage'.
    if result.get("audit_incomplete"):
        sys.stderr.write(
            "review: adversarial audit INCOMPLETE — %d/%d dimension reviewers ran, "
            "%d high/medium finding(s) unverifiable (agent runtime unreachable?). "
            "FAILING CLOSED (nothing-skipped): the gate blocks. Fix agent access or "
            "set %s=1 to bypass with a recorded DECISION.\n" % (
                result.get("reviewers_ok", 0), result.get("reviewers_total", 0),
                result.get("verify_unavailable", 0), REVIEW_SKIP_ENV))
        return 3
    return 1 if result["confirmed_blocking"] > 0 else 0


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("review", 4100, "register_review"),
)


def register_review(sub, fmt_parent):
    # review -----------------------------------------------------------------
    p_review = sub.add_parser(
        "review", parents=[fmt_parent],
        help="adversarial audit fan-out over a self-extension diff (Opus/max)")
    p_review.add_argument("--adversarial", action="store_true",
                          help="run the adversarial audit round (required)")
    p_review.add_argument("--diff-range", dest="diff_range",
                          help="git range to diff (e.g. HEAD~1..HEAD or a base ref)")
    p_review.add_argument("--diff-file", dest="diff_file",
                          help="read the unified diff from a file instead of git")
    p_review.add_argument("--acceptance-file", dest="acceptance_file",
                          help="path to the feature plan.md whose acceptance "
                               "criteria the reviewers audit against")
    p_review.add_argument("--worklog", dest="worklog",
                          help="feature worklog.md path; when the Q8 escape hatch "
                               "AGENTWARE_SKIP_ADVERSARIAL_REVIEW is set, a "
                               "self-closing `> DECISION:` skip marker is recorded "
                               "here instead of running the audit")
    p_review.set_defaults(func=cmd_review)


_EXPORTS = [
    "_review_fanout_reviewers",
    "_review_verify_findings",
    "_review_run_critic",
    "_review_run_audit",
    "_review_resolve_diff",
    "_review_print_text",
    "cmd_review",
]

__all__ = list(_EXPORTS)
