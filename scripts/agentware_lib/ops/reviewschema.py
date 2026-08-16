"""agentware_lib.ops.reviewschema -- the adversarial review VOCABULARY and its normalizers (layer: ops).

Owns: every closed set the review subsystem agrees on -- the severity ladder and
which rungs BLOCK (`REVIEW_SEVERITIES` / `REVIEW_BLOCKING_SEVERITIES`), the
verifier verdicts (`REVIEW_VERDICTS`), the audit DIMENSIONS and their lookup
(`REVIEW_DIMENSIONS` / `REVIEW_DIMENSION_KEYS` / `REVIEW_DIMENSIONS_BY_KEY`), the
finding field contract (`REVIEW_FINDING_REQUIRED_FIELDS` /
`REVIEW_FINDING_OPTIONAL_FIELDS`), the fix statuses (`REVIEW_FIX_STATUSES`), and
the two normalizers that force untrusted agent output into those sets
(`_review_normalize_finding` / `_review_normalize_fix`) plus the blocking test
(`_review_is_blocking`).
Does NOT own: the model/effort/rounds CONFIG axes -> ops.reviewconfig; the prompt
text built from these dimensions -> ops.reviewprompts; the audit fan-out ->
ops.reviewaudit; the remediation loop -> ops.reviewremediate; the report ->
ops.reviewreport; agent spawning -> features.reviewagent; the untrusted-output
readers (`_review_extract_json` / `_review_fence`) -> features.reviewoutput.

WHY THE VOCABULARY IS ITS OWN MODULE
------------------------------------
Everything downstream -- prompts, fan-out, remediation, report -- has to agree on
what a severity is, which severities block, and what fields a finding carries.
Homing those sets in whichever module happened to need them first would let a
second definition drift in later, and a review gate whose two halves disagree
about "blocking" fails in the worst possible direction: it passes.

NORMALIZATION IS A TRUST BOUNDARY, NOT A CONVENIENCE
----------------------------------------------------
`_review_normalize_finding` reads output produced by a language model. It is
adversarial input by construction, and the normalizer is what turns it into a
value the rest of the system may act on: an unrecognized severity, dimension, or
verdict does not pass through as-is and does not crash -- it is coerced into the
known set or the item is dropped and COUNTED, so a reviewer that returns garbage
shows up as dropped items in a diagnostic rather than as a silently empty audit.
A finding that cannot be normalized must never become a finding that cannot be
seen.

Layer deps: ops -> nothing outside stdlib. This is the deepest leaf of the review
family; every other review module imports downward or sideways into it, and it
imports none of them.
Determinism: pure. No clock, no env, no I/O -- the same raw item normalizes to
the same finding on every run (INV-1).
"""

import re
from collections import OrderedDict


# Confirmed severities, ordered most→least severe. `high`/`medium` are the
# auto-fix set (Q4) and the STOP/escalate set (Q5): a confirmed high/medium that
# survives all rounds BLOCKS the done-stamp. `low` is fixed opportunistically or
# filed as a follow-up workorder; `info` is a call-out only (never blocks).
REVIEW_SEVERITIES = ("high", "medium", "low", "info")


# Severities that (a) drive auto-fix and (b) block the done-stamp when a
# CONFIRMED finding survives the bounded loop. Derived from REVIEW_SEVERITIES so
# the two lists never drift.
REVIEW_BLOCKING_SEVERITIES = ("high", "medium")


# Adversarial-verify verdicts. Every raw finding from the audit pass is graded by
# an INDEPENDENT verifier prompted to REFUTE it (Q6 independence). Only
# `confirmed` findings count toward auto-fix / blocking; `refuted` is dropped
# (with a call-out), `uncertain` is treated conservatively as NOT confirmed but
# is surfaced in the report so nothing is silently discarded.
REVIEW_VERDICTS = ("confirmed", "refuted", "uncertain")


# The FIXED dimension set (Q7). key == the reviewer subprocess label + the JSON
# `dimension` field; title == human-readable; focus == the hostile lens injected
# into that reviewer's prompt. Reordering/renaming keys is a breaking change to
# the report schema — add, do not mutate, and keep keys kebab-case + stable.
REVIEW_DIMENSIONS = (
    {
        "key": "correctness",
        "title": "Correctness vs. acceptance criteria",
        "focus": (
            "Read the diff against EACH acceptance criterion. Construct concrete "
            "inputs/states where the code does NOT satisfy the criterion — off-by-one, "
            "wrong branch, unhandled None/empty, race, wrong exit code, spec-vs-code "
            "mismatch. Do not re-run the author's tests; build NEW adversarial cases."
        ),
    },
    {
        "key": "security-governance",
        "title": "Security & governance",
        "focus": (
            "Hunt injection (untrusted finding/plan text reaching a --target, Verify "
            "line, shell, or eval — R-SEC-02), authz gaps, secret handling, weakened "
            "governance gates, and any kernel-path write that should have been refused. "
            "Assume every external string is hostile and try to make it execute."
        ),
    },
    {
        "key": "reuse-in-new-context",
        "title": "Reuse-in-new-context gaps",
        "focus": (
            "Find code reused/moved into a NEW context whose original invariants no "
            "longer hold — a helper called with new argument shapes, an env/global the "
            "new caller does not set, a copied claude-branch that drifts from run_agent, "
            "a predicate that matched in the old path but not the new one."
        ),
    },
    {
        "key": "novel-input-edge-cases",
        "title": "Novel-input & edge cases",
        "focus": (
            "Feed inputs the author almost certainly did not test: empty/huge/unicode, "
            "malformed JSON, missing files, zero findings, max-rounds boundary, "
            "concurrent runs, non-self-extension diffs, Opus unreachable. Show the "
            "failure mode for each that breaks."
        ),
    },
    {
        "key": "test-quality",
        "title": "Test quality & false-pass risk",
        "focus": (
            "Attack the tests themselves: assertions that pass on wrong behavior, stubs "
            "that hide the real path, missing regression lock for a claimed fix, a "
            "hermetic fake that no longer mirrors the real agent seam. A green suite "
            "that would stay green if the feature were broken is a HIGH finding."
        ),
    },
)


# Fast key lookup + the canonical ordered key list (used to size the fan-out and
# to validate a `dimension` field on an incoming finding).
REVIEW_DIMENSION_KEYS = tuple(d["key"] for d in REVIEW_DIMENSIONS)


REVIEW_DIMENSIONS_BY_KEY = OrderedDict((d["key"], d) for d in REVIEW_DIMENSIONS)


# The findings JSON schema. A reviewer returns {"dimension": <key>, "findings":
# [ <finding>, ... ]}; each finding is validated against REVIEW_FINDING_FIELDS.
# `verdict` is set by the SEPARATE adversarial-verify pass, so it is OPTIONAL on
# a raw reviewer finding and REQUIRED on a verified one. `line` is optional (0 or
# null when the finding is file- or repo-level). The schema is intentionally flat
# and stringly-typed so a stub agent in a hermetic test can emit it trivially.
REVIEW_FINDING_REQUIRED_FIELDS = ("dimension", "severity", "file", "summary",
                                  "failure_scenario")


REVIEW_FINDING_OPTIONAL_FIELDS = ("line", "verdict", "verdict_rationale",
                                  "suggested_fix")


def _review_normalize_finding(raw, dimension=None):
    """Coerce one raw finding dict into the canonical shape, defaulting missing
    optional fields. Returns (finding, errors): `errors` is a list of missing
    REQUIRED fields (empty when valid). Never raises on a dict input so a
    malformed agent payload is REPORTED, not crashed on (novel-input hardening).
    """
    if not isinstance(raw, dict):
        return None, ["finding is not an object"]
    finding = {}
    errors = []
    for field in REVIEW_FINDING_REQUIRED_FIELDS:
        val = raw.get(field)
        if field == "dimension" and (val is None or val == "") and dimension:
            val = dimension
        if val is None or (isinstance(val, str) and val.strip() == ""):
            errors.append(field)
            val = "" if field != "dimension" else (dimension or "")
        finding[field] = val
    for field in REVIEW_FINDING_OPTIONAL_FIELDS:
        if field in raw:
            finding[field] = raw[field]
    # SECURITY (R-SEC-02): `dimension` is attacker-influenceable reviewer
    # output that later becomes part of OPERATIONAL text — the
    # 'verify:<dimension>' role label in log diagnostics and the JSON report —
    # so it is enforced against the REVIEW_DIMENSION_KEYS enum HERE (the
    # validation the schema comment above always promised). A hostile value
    # (e.g. one carrying newlines that would forge pristine log lines) is
    # coerced to the spawning reviewer's dimension (or '?') and recorded as a
    # schema error, never kept verbatim.
    if finding.get("dimension") not in REVIEW_DIMENSION_KEYS:
        finding["dimension"] = (dimension if dimension in REVIEW_DIMENSION_KEYS
                                else "?")
        if "dimension" not in errors:
            errors.append("dimension")
    # Normalize severity casing, then enforce it against the REVIEW_SEVERITIES
    # enum — `severity` is the reviewer-output field that actually GATES
    # blocking (`_review_is_blocking`) and the verify-unavailable fail-closed
    # accounting, so an off-enum token (e.g. 'critical' — a recurring
    # reviewer-persona drift shape) must never be kept verbatim: kept, it
    # would sail past BOTH `REVIEW_BLOCKING_SEVERITIES` checks and let a
    # CONFIRMED defect (or an unverifiable finding) ship the gate GREEN.
    # FAIL-CLOSED coercion: an unknown severity is treated as the MOST severe
    # ('high') and recorded as a schema error — over-blocking is recoverable
    # (the verify pass still has to confirm it / the operator sees the error);
    # under-blocking ships a defect into every future run.
    sev = str(finding.get("severity", "")).strip().lower()
    finding["severity"] = sev
    if sev not in REVIEW_SEVERITIES:
        finding["severity"] = "high"
        if "severity" not in errors:
            errors.append("severity")
    if "verdict" in finding and isinstance(finding["verdict"], str):
        finding["verdict"] = finding["verdict"].strip().lower()
    return finding, errors


def _review_is_blocking(finding):
    """True iff this finding is CONFIRMED at a blocking severity (high/medium).
    A finding with no verdict is NOT blocking until the verify pass confirms it.
    """
    if not isinstance(finding, dict):
        return False
    if str(finding.get("verdict", "")).strip().lower() != "confirmed":
        return False
    return str(finding.get("severity", "")).strip().lower() in REVIEW_BLOCKING_SEVERITIES


REVIEW_FIX_STATUSES = ("fixed", "unfixable", "deferred")


def _review_normalize_fix(raw, index=None):
    """Coerce one raw fixer report into the canonical fix shape. A fix claiming
    'fixed' WITHOUT a regression_test is DOWNGRADED to 'unfixable' — an unverified
    fix is REFUSED, never auto-applied (the human-fix-pass discipline). Never
    raises on junk input (novel-input hardening)."""
    fix = {"index": index, "status": "unfixable", "regression_test": "",
           "notes": ""}
    if not isinstance(raw, dict):
        fix["notes"] = "fixer returned a non-object fix entry"
        return fix
    if raw.get("index") is not None:
        fix["index"] = raw.get("index")
    status = str(raw.get("status", "") or "").strip().lower()
    reg = str(raw.get("regression_test", "") or "").strip()
    notes = str(raw.get("notes", "") or "").strip()
    fix["regression_test"] = reg
    if status not in REVIEW_FIX_STATUSES:
        status = "unfixable"
    if status == "fixed" and not reg:
        fix["status"] = "unfixable"
        fix["notes"] = ("REFUSED: fix claims fixed but names no regression test "
                        "(unverified fix). " + notes).strip()
    else:
        fix["status"] = status
        fix["notes"] = notes
    return fix


_EXPORTS = [
    "REVIEW_SEVERITIES",
    "REVIEW_BLOCKING_SEVERITIES",
    "REVIEW_VERDICTS",
    "REVIEW_DIMENSIONS",
    "REVIEW_DIMENSION_KEYS",
    "REVIEW_DIMENSIONS_BY_KEY",
    "REVIEW_FINDING_REQUIRED_FIELDS",
    "REVIEW_FINDING_OPTIONAL_FIELDS",
    "REVIEW_FIX_STATUSES",
    "_review_normalize_finding",
    "_review_is_blocking",
    "_review_normalize_fix",
]

__all__ = list(_EXPORTS)
