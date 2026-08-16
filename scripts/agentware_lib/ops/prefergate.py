"""agentware_lib.ops.prefergate -- does this preference LOOSEN a governance gate? (layer: ops).

Owns: the deterministic classifier answering whether a captured preference would
weaken a gate rather than express a taste -- `_pref_loosens_gate` and the regex
vocabulary it reads (the `_PREF_GATE_*` nouns, the `_PREF_LOOSEN_*` verbs and the
`_PREF_NEG*` negation sets).

WHY A GATE-LOOSENING CLASSIFIER EXISTS AT ALL
Captured preferences inject into every future session, so "stop running the tests
first" and "prefer tabs over spaces" are not the same kind of statement. The first
would quietly disable a safety property for every later run, which is exactly what
R-CAP-03 refuses. This module is the mechanical half of that refusal: a preference
matching a LOOSEN verb against a GATE noun is flagged, so the capture path can
route it to a human instead of silently adopting it.

The NEGATION sets are the interesting part. "do not skip the review gate" contains
both a loosen verb and a gate noun, and is the OPPOSITE of a loosening request --
so negation is checked before the verb/noun pair is allowed to fire. Getting that
backwards would flag the very statements that STRENGTHEN a gate.

Pure text in, verdict out: no I/O, no clock, no KB access, so an adversarial
phrasing can be tested directly.

Does NOT own: capturing a preference or the queue it lands in (the `prefer` verb,
resident until its own port); the checkup self-exam that consults this
(ops.checkup); the R-CAP-03 kernel-path refusal, which is a different obligation
(ops.planauthor.plankernelpath).
Layer deps: ops -> stdlib `re` only.
Fan-in: 1 at landing (ops.checkup); grows to the `prefer` verb at its port
(regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all.
"""

import re


# (c) Tighten-not-loosen — a capture may TIGHTEN but NEVER LOOSEN a governance
# gate; a match is REFUSED (no write) and routed to the human-authored self-
# extension path. Deterministic NL, balanced for PRECISION (an adversarial review
# found the flat "loosen-verb ANYWHERE + any gate-noun" rule both under- and
# over-fired). Governance NOUNS split by ambiguity:
#   STRONG — unambiguous, almost never benign (verify/gate/lint/eval/acceptance/
#            pre-commit/sign-off/R-id): a loosen verb ANYWHERE in the text refuses.
#   WEAK   — common English words that are governance nouns only IN CONTEXT
#            (tests/ci/cd/release/deploy/merge/review/audit/approval): refuse ONLY
#            when the loosen intent DIRECTLY governs the noun — verb→noun adjacency,
#            a `without <noun>` form, or a `<noun> is optional/skippable` form — so
#            `disable telemetry in ci` (verb governs telemetry, not ci) still captures.
_PREF_GATE_NOUN_STRONG = (
    # NOTE: `eval` is DELIBERATELY excluded — it collides with the eval() code
    # function, so `disable eval() in production` / `disable eval of untrusted
    # input` (SECURITY-tightening prefs) must capture, not be mis-read as loosening
    # an "eval gate". (The retrieval `eval` benchmark is not a gate operators loosen.)
    r"verif\w*|gates?|lint\w*|acceptance|pre-?commit|sign-?offs?|"
    r"R-[A-Z]+-\d+")


_PREF_GATE_NOUN_WEAK = (
    r"tests?|testing|ci|cd|releases?|deploy\w*|merges?|merging|reviews?|"
    r"audits?|auditing|approvals?")


_PREF_GATE_NOUN_RE = re.compile(r"\b(?:%s)\b" % _PREF_GATE_NOUN_STRONG, re.I)


# POSITIVE loosen verbs (form 4, co-occurring with a STRONG noun). Only the
# unambiguous gate-weakening verbs, incl. the audit-found synonyms
# waive/omit/forgo/circumvent/sidestep/suppress. NEGATED forms (`never run`,
# `no longer enforce`) live in _PREF_NEG_ENFORCE_RE below (negation INVERTS
# polarity), NOT here — see `_pref_loosens_gate`.
_PREF_LOOSEN_VERB_RE = re.compile(
    # NOTE: bare `optional` is NOT a form-4 loosen verb — the WAIVED (`X is
    # optional`) and MADE_OPTIONAL (`make X optional`) forms handle it with
    # negation-awareness, so a NEGATED `never make the gate optional` (tightening)
    # is not mis-caught by a residual `optional` after the negation scrub.
    r"\b(?:skip\w*|disabl\w*|bypass\w*|ignor\w*|turn off|relax\w*|"
    r"weaken\w*|loosen\w*|waiv\w*|circumvent\w*|sidestep\w*|forgo|forego|"
    r"omit\w*|suppress\w*)\b", re.I)


# Form 1: a POSITIVE loosen verb DIRECTLY governs a gate noun (STRONG or WEAK):
# `skip the tests`, `waive the review`, `omit the release`. Only UNAMBIGUOUS
# weakening verbs (not `avoid`/`drop`/`remove`, which read as tightening with a
# weak noun). Deliberately NO `without <noun>` form: `without <gate>` is
# PREDOMINANTLY tightening in real prefs (`never merge without review`, `don't
# deploy without approval`) — its polarity flips on always/never, which a
# deterministic regex cannot read — so catching the niche `ship without ci` would
# over-refuse the common tightening phrasings (a strictly worse defect).
_PREF_LOOSEN_GOVERNS_RE = re.compile(
    r"\b(?:skip\w*|disabl\w*|bypass\w*|ignor\w*|omit\w*|waiv\w*|circumvent\w*|"
    r"sidestep\w*|suppress\w*|forgo|forego|turn\s*off|relax\w*|weaken\w*|"
    r"loosen\w*)\s+"
    r"(?:the\s+|a\s+|an\s+|any\s+|all\s+|our\s+|its\s+|your\s+|these\s+|"
    r"those\s+|my\s+|their\s+)?"
    # allow up to 2 MODIFIER words (adjective/gerund) directly qualifying the gate
    # noun — `skip the FLAKY tests`, `skip RUNNING the tests`, `skip the SLOW e2e
    # tests` — but STOP at a preposition/conjunction so the gate noun stays the
    # DIRECT object: `disable telemetry IN ci` / `bypass the cache IN ci` still
    # capture (ci is a location, not the object of the loosen verb).
    r"(?:(?!in\b|on\b|for\b|of\b|during\b|with\b|at\b|by\b|to\b|from\b|about\b|"
    r"and\b|but\b|or\b|so\b|because\b|while\b|when\b|via\b|per\b)\w+\s+){0,2}?"
    r"(?:%s|%s)\b" % (_PREF_GATE_NOUN_STRONG, _PREF_GATE_NOUN_WEAK), re.I)


# Form 2: a gate noun DECLARED optional / not-required — `review is optional`, the
# acceptance gate can be skipped`, `tests are not required`, `ci is no longer
# mandatory`. A POSITIVE-optional word (optional/skippable/waived/unnecessary)
# loosens on its own; a REQUIREMENT word (required/mandatory/enforced/needed)
# loosens ONLY when NEGATED (`not`/`no longer`/`never`), else it TIGHTENS (`tests
# are required` must capture).
_PREF_GATE_WAIVED_RE = re.compile(
    r"\b(?:%s|%s)\b(?:\s+\w+){0,3}?\s+(?:"
    # (a) POSITIVE copula + a positive-optional word (`review is optional`, `is now
    #     optional`) — one non-negator adverb allowed, but NOT a negator (`is not
    #     optional` = TIGHTENING must not match):
    r"(?:is|are|can be|could be|may be|be|been|being|was|were|remains?|stays?|"
    r"becomes?|became|gets?|got)\s+(?:(?!not\b|never\b|no\b)\w+\s+){0,1}?"
    r"(?:optional|skippable|skipped|waived|unnecessary|non-?mandatory)"
    r"|"
    # (b) POSITIVE copula + a NEGATED requirement (`tests are not required`, `ci is
    #     no longer mandatory`):
    r"(?:is|are|be|been|being|was|were|remains?|stays?|gets?|got)\s+"
    r"(?:no longer|not|never)\s+(?:\w+\s+){0,1}?"
    r"(?:required|mandatory|mandated|needed|necessary|enforced|run)"
    r"|"
    # (c) NEGATED copula + a requirement word (`review isn't required`, `lint isn't
    #     necessary`, `reviews aren't enforced`) — the negation fused into the copula:
    r"(?:isn'?t|aren'?t|wasn'?t|weren'?t|won'?t\s+be|no longer)\s+(?:\w+\s+){0,1}?"
    r"(?:required|mandatory|mandated|needed|necessary|enforced)"
    r")\b"
    % (_PREF_GATE_NOUN_STRONG, _PREF_GATE_NOUN_WEAK), re.I)


# Form 2b: `make/treat/render/mark/consider/keep/deem <the> <gate-noun> [as]
# optional/skippable/waived` — the transitive "make X optional" loosening the
# copular `<noun> is optional` form misses.
_PREF_GATE_MADE_OPTIONAL_RE = re.compile(
    r"\b(?:make|makes|making|treat|treats|treating|render|renders|rendering|"
    r"mark|marks|marking|consider|considers|considering|keep|keeps|keeping|"
    r"leave|leaves|leaving|deem|deems|deeming)\s+"
    r"(?:the\s+|a\s+|any\s+|all\s+|our\s+|your\s+)?(?:\w+\s+){0,1}?"
    r"(?:%s|%s)\s+(?:as\s+)?"
    r"(?:optional|skippable|waived|unnecessary|non-?mandatory)\b"
    % (_PREF_GATE_NOUN_STRONG, _PREF_GATE_NOUN_WEAK), re.I)


# Form 2d: SEPARABLE `turn <the> <gate> off` (`turn ci off`, `turn the tests off`) —
# GOVERNS handles the contiguous `turn off ci` unit; this catches the split object.
_PREF_GATE_TURN_OFF_RE = re.compile(
    r"\bturn\s+(?:the\s+|a\s+|any\s+|all\s+|our\s+|your\s+)?(?:\w+\s+){0,2}?"
    r"(?:%s|%s)(?:\s+\w+){0,2}?\s+off\b"
    % (_PREF_GATE_NOUN_STRONG, _PREF_GATE_NOUN_WEAK), re.I)


# Form 2e: PERMIT-FAILURE family — `allow/let the <gate> [to] fail`, `<gate> (should)
# be non-blocking`, `non-blocking <gate>`, `<gate> [failures] should not block` — a
# gate that may FAIL without blocking is a weakened gate.
_PREF_GATE_PERMIT_FAIL_RE = re.compile(
    r"\b(?:"
    r"(?:allow|allows|allowing|let|lets|letting|permit\w*|tolerat\w*)\s+"
    r"(?:the\s+|a\s+|any\s+|all\s+|our\s+|your\s+)?(?:\w+\s+){0,2}?"
    r"(?:%s|%s)(?:\s+\w+){0,3}?\s+(?:to\s+)?"
    r"(?:fail\w*|break\w*|error\w*|be\s+red)"
    r"|(?:non-?blocking|not\s+block\w*|do(?:es)?n'?t\s+block|should(?:\s?n'?t|\s+not)\s+block)"
    r"\s+(?:\w+\s+){0,2}?(?:%s|%s)"
    r"|(?:%s|%s)(?:\s+failures?)?(?:\s+\w+){0,3}?\s+(?:should\s+|must\s+|will\s+)?"
    r"(?:be\s+)?(?:non-?blocking|(?:should\s+|must\s+)?(?:not|never)\s+block\w*)"
    r")\b" % (_PREF_GATE_NOUN_STRONG, _PREF_GATE_NOUN_WEAK,
             _PREF_GATE_NOUN_STRONG, _PREF_GATE_NOUN_WEAK,
             _PREF_GATE_NOUN_STRONG, _PREF_GATE_NOUN_WEAK), re.I)


# Form 2c: `no need to <enforce> [the] <gate>` / `no need for the <STRONG gate>` —
# the "no need" negator (distinct from "don't need") loosening a gate. The `for`
# branch is STRONG-noun-only so a benign `no need for the release notes` (release
# = weak) still captures.
_PREF_GATE_NO_NEED_RE = re.compile(
    r"\bno\s+need\s+(?:"
    r"to\s+(?:run|runs|running|execute\w*|perform\w*|do|have|check\w*|validat\w*|"
    r"verif\w*|enforc\w*|require\w*|pass)\b(?:\s+\w+){0,3}?\s+"
    r"(?:the\s+|a\s+|an\s+|any\s+|all\s+|our\s+|your\s+)?(?:%s|%s)"
    r"|for\s+(?:the\s+|a\s+|an\s+)?(?:%s))\b"
    % (_PREF_GATE_NOUN_STRONG, _PREF_GATE_NOUN_WEAK, _PREF_GATE_NOUN_STRONG), re.I)


# NEGATION inverts polarity (audit round-2/3). A NEGATED ENFORCE verb LOOSENS
# (`never run the tests`, `no longer enforce lint`, `we no longer need the tests`);
# a NEGATED LOOSEN verb TIGHTENS (`never skip the tests`, `avoid skipping tests`)
# and must NOT refuse. `_pref_loosens_gate` scrubs negated loosen verbs before the
# positive-loosen checks, and treats a negated enforce verb near a gate noun as
# loosening. `avoid`/`prevent` act as negators too (they negate the following verb).
# STRICT negators for NEG_ENFORCE (negator + enforce verb = loosening). NOTABLY
# EXCLUDES `avoid`/`prevent`: `avoid running tests on every keystroke` /
# `prevent running tests in the editor` are benign FREQUENCY/perf prefs, NOT gate
# loosening — only an explicit negation (`never/don't/no need`) reliably loosens.
_PREF_NEG = (r"never|not|no longer|don'?t|do not|dont|does\s?n'?t|didn'?t|won'?t|"
             r"stops?(?:\s+running)?|ceased?|no need(?:\s+to)?|no longer need")


# LOOSE negators for NEG_LOOSEN (negator + LOOSEN verb = TIGHTENING, scrubbed out):
# here `avoid`/`prevent` DO count — `avoid skipping the tests` is tightening.
_PREF_NEG_LOOSE = (r"%s|avoids?|avoiding|prevents?|preventing" % _PREF_NEG)


_PREF_NEG_ENFORCE_RE = re.compile(
    # the negator must DIRECTLY govern the enforce verb (at most one adverb between),
    # else `never SKIP the verify gate` / `never push without RUNNING the tests`
    # (both TIGHTENING — the negator governs skip/push, not verify/run) false-match.
    r"\b(?:%s)\s+"
    # the 1-word filler may be an ADVERB but NOT a LOOSEN verb — else `never SKIP
    # running the tests` (tightening: never governs skip) mis-reads as `never …
    # running …` (loosening).
    r"(?:(?!skip|disabl|bypass|ignor|waiv|omit|forgo|forego|circumvent|sidestep|"
    r"suppress|relax|weaken|loosen|turn|push|make|treat|keep)\w+\s+){0,1}?"
    r"(?:run|runs|running|require\w*|enforc\w*|mandat\w*|check\w*|validat\w*|"
    r"verif\w*|need\w*|have\s+to|has\s+to|had\s+to|bother\s+with)\b"
    # bounded filler spans an infinitive object (`don't need TO RUN the tests`):
    r"(?:\s+\w+){0,3}?\s+"
    r"(?:the\s+|a\s+|an\s+|any\s+|all\s+|our\s+|your\s+)?"
    r"(?:%s|%s)\b" % (_PREF_NEG, _PREF_GATE_NOUN_STRONG, _PREF_GATE_NOUN_WEAK),
    re.I)


_PREF_NEG_LOOSEN_RE = re.compile(
    r"\b(?:%s)\s+(?:\w+\s+){0,1}?(?:skip\w*|disabl\w*|bypass\w*|ignor\w*|waiv\w*|"
    r"omit\w*|forgo|forego|circumvent\w*|sidestep\w*|suppress\w*|relax\w*|"
    r"weaken\w*|loosen\w*|turn\w*|allow\w*|lets?|letting|permit\w*|tolerat\w*|"
    # make/treat/keep... a gate optional is ALSO loosening, so a NEGATED one
    # (`never make review optional`, `don't keep tests optional`, `never turn ci
    # off`, `never allow tests to fail`) is TIGHTENING:
    r"make\w*|treat\w*|keep\w*|mark\w*|leave\w*|render\w*|consider\w*|deem\w*)\b"
    % _PREF_NEG_LOOSE, re.I)


def _pref_loosens_gate(text):
    """(c) tighten-not-loosen — True iff `text` reads as WEAKENING a governance
    gate. Balanced for precision AND negation polarity (no over-refusal of benign
    or TIGHTENING prose):
    (2) a governance noun declared optional/skippable/not-required;
    (neg-enforce) a NEGATED enforce verb near a gate noun (`never run the tests`);
    (4) a POSITIVE loosen verb + a STRONG noun; (1) a POSITIVE loosen verb DIRECTLY
    governing any gate noun — both after SCRUBBING negated loosen verbs, so a
    TIGHTENING `never skip the tests` / `don't disable lint` is admitted."""
    if not text or not isinstance(text, str):        # tolerate non-str queue text
        return False
    # Neutralize NEGATED loosen/make-optional verbs FIRST — they read as TIGHTENING
    # (`never skip the tests`, `never make review optional`, `avoid skipping tests`)
    # and must NOT refuse. Run the optional/made-optional/positive-loosen forms on
    # the SCRUBBED text; the NO_NEED + NEG_ENFORCE forms are inherently negated and
    # run on the raw text.
    scrubbed = _PREF_NEG_LOOSEN_RE.sub(" ", text)
    if _PREF_GATE_WAIVED_RE.search(scrubbed):        # form 2 (`X is optional`)
        return True
    if _PREF_GATE_MADE_OPTIONAL_RE.search(scrubbed):  # form 2b (`make X optional`)
        return True
    if _PREF_GATE_TURN_OFF_RE.search(scrubbed):      # form 2d (`turn ci off`)
        return True
    if _PREF_GATE_PERMIT_FAIL_RE.search(scrubbed):   # form 2e (`allow tests to fail`)
        return True
    if _PREF_GATE_NO_NEED_RE.search(text):           # form 2c (`no need to run X`)
        return True
    if _PREF_NEG_ENFORCE_RE.search(text):            # negated enforcement = loosening
        return True
    if _PREF_LOOSEN_VERB_RE.search(scrubbed) and _PREF_GATE_NOUN_RE.search(scrubbed):
        return True                                  # form 4
    if _PREF_LOOSEN_GOVERNS_RE.search(scrubbed):     # form 1
        return True
    return False


_EXPORTS = [
    "_PREF_GATE_NOUN_STRONG",
    "_PREF_GATE_NOUN_WEAK",
    "_PREF_GATE_NOUN_RE",
    "_PREF_GATE_MADE_OPTIONAL_RE",
    "_PREF_GATE_NO_NEED_RE",
    "_PREF_GATE_PERMIT_FAIL_RE",
    "_PREF_GATE_TURN_OFF_RE",
    "_PREF_GATE_WAIVED_RE",
    "_PREF_LOOSEN_GOVERNS_RE",
    "_PREF_LOOSEN_VERB_RE",
    "_PREF_NEG",
    "_PREF_NEG_LOOSE",
    "_PREF_NEG_ENFORCE_RE",
    "_PREF_NEG_LOOSEN_RE",
    "_pref_loosens_gate",
]

__all__ = list(_EXPORTS)
