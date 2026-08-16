"""agentware_lib.ops.preferadmit — the ADMISSION BAR for a preference (layer: ops).

Owns: the deterministic predicates that decide whether a line of operator text is
worth remembering — the cue and noise vocabulary (_PREF_CUE_RE /
_PREF_STRONG_CUE_RE / _PREF_NOISE_PHRASES / _PREF_STATEMENT_QUESTION_RE /
_PREF_MIN_TOKENS), the operator-text extractor (`_pref_operator_text`), the
admission test (`_pref_is_capturable`) and the entry-provenance test
(`_pref_entry_is_user_source`).

THE BAR IS THE WHOLE VALUE
A captured preference injects into EVERY future session, so each one is a
permanent context cost paid forever. The admission test is therefore deliberately
strict and deterministic: empties, pure interrogatives, greetings and
acknowledgements are refused, and the text must read as a preference or an
imperative to pass. The standard is "would a future session behave differently
knowing this?" — a capture system with a low bar does not become more helpful, it
becomes a slow leak that degrades every session after it.

WHY ITS OWN MODULE: both the capture path and the queue scanner ask these
questions, and they cannot import each other without a cycle. Pure predicates with
no I/O also means the bar can be tested directly against adversarial phrasings,
which is the only way to keep it honest as the vocabulary grows.

Does NOT own: writing a capture (ops.prefercapture); the queue (ops.preferqueue);
gate-loosening refusal (ops.prefergate); kernel-path refusal
(ops.planauthor.plankernelpath).
Layer deps: ops -> core.frontmatter (the entry reader) plus stdlib os/re.
Fan-in: 3 at landing (regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith — byte-identical, comments and all.
"""

import os
import re

from agentware_lib.core.frontmatter import read_entry_frontmatter


_PREF_MIN_TOKENS = 3


# (a) Noise bar — a deterministic admission predicate answering "would a future
# session behave differently knowing this?". Reject empties, pure interrogatives,
# greetings/acks, and anything with no preference/imperative cue.
_PREF_NOISE_PHRASES = frozenset((
    "hi", "hello", "hey", "yo", "thanks", "thank you", "ty", "ok", "okay", "k",
    "cool", "nice", "great", "got it", "sounds good", "yes", "no", "yep", "nope",
    "sure", "please", "done", "good", "np", "lol",
))


_PREF_CUE_RE = re.compile(
    r"\b(always|never|prefer(?:s|red|ence)?|by default|default to|from now on|"
    r"going forward|make sure|do not|don'?t|dont|should|must|avoid|ensure|use|"
    r"keep|no longer|i (?:want|like|prefer|need)|we (?:want|use|prefer))\b",
    re.I)


# Operator-turn provenance gate (260719-prefer-precision-and-reject). This session's
# marquee false positives were preference CUES that appeared only inside text the
# operator QUOTED BACK from the agent as markdown blockquotes (lines led by `>`) —
# not their own instruction.
#
# A confusion/interrogative that HAPPENS to carry a cue token (e.g. "i dont
# understand …" matches the `dont` cue) is not a durable preference. This guard is
# DELIBERATELY NARROW — only explicit confusion phrases and `aux + pronoun` question
# openers — so it never drops a genuine preference that merely OPENS with a temporal/
# relative/wh clause ("When merging, always …", "Which manager — always pin …") or a
# negated imperative ("do not …", "should always …"). It anchors at ^ and runs on the
# FULL text (not the residual), so it stays strictly tighten-only.
_PREF_STATEMENT_QUESTION_RE = re.compile(
    r"^\s*(?:"
    r"i\s+(?:dont|don'?t|do\s+not)\s+understand|"
    r"i'?m\s+(?:confused|not\s+sure|unclear|lost)|"
    r"(?:can|could|would|should|will|do|does|did|are|is)\s+"
    r"(?:you|we|it|this|these|i)\b"
    r")", re.I)


# The statement-question guard PREFIX-matches, so it must NOT fire when the turn
# ALSO carries a STRONG directive cue (a genuine preference stated after a leading
# question/confusion clause, e.g. "Can we always use spaces" / "i'm not sure, but
# from now on always lint"). Only a confusion/question with NO strong cue — e.g.
# "i dont understand what this is" (which merely trips the weak `dont` cue) — is
# dropped. Strong cues are the unambiguous directives, NOT the weak `dont/do not`.
_PREF_STRONG_CUE_RE = re.compile(
    r"\b(always|never|prefer(?:s|red|ence)?|by default|default to|from now on)\b",
    re.I)


def _pref_operator_text(text):
    """Return the genuinely OPERATOR-AUTHORED residual of a human turn: DROP every
    markdown-blockquote line (first non-space char `>` — content the operator quoted
    BACK from the agent/elsewhere, not their own instruction), then strip. Pure,
    deterministic, offline (INV-1). Returns "" for non-str/empty input.

    `>` is ambiguous (quote-back vs the operator's OWN emphasis); this session's
    entire false-positive class was quote-backs, so it is treated as such. A genuine
    preference stated ONLY inside a `>` line is therefore a (safe) FALSE NEGATIVE: it
    is not captured, and the operator can simply restate it without the `>`. Precision
    over recall — never a false positive from this gate."""
    if not isinstance(text, str):
        return ""
    kept = [ln for ln in text.splitlines() if not ln.lstrip().startswith(">")]
    return "\n".join(kept).strip()


def _pref_is_capturable(text):
    """(a) noise bar — True iff `text` is a durable, future-affecting preference.

    Precision gate (260719): STRICTLY tighten-only over the pre-gate behavior — a
    text is capturable IFF it was capturable ON ITS OWN (the noise bar below runs on
    the FULL text, unchanged) AND its preference cue ALSO survives in the operator-
    authored residual (`_pref_operator_text` strips `>`-blockquote quote-backs). The
    residual check is purely ADDITIVE, so it can only REJECT more, never capture more
    — a cue that lives only in quoted-back agent text is dropped without ever
    reclassifying a previously-rejected turn (e.g. a trailing `?`) as capturable."""
    if not isinstance(text, str):        # tolerate a non-str queue record text
        return False
    t = text.strip()                     # noise bar runs on the FULL text (tighten-only)
    if not t:
        return False
    low = t.lower()
    if low.rstrip("!.").strip() in _PREF_NOISE_PHRASES:
        return False
    if low.endswith("?"):                        # pure interrogative
        return False
    if (_PREF_STATEMENT_QUESTION_RE.match(low)   # confusion/question opener AND
            and not _PREF_STRONG_CUE_RE.search(low)):  # no strong directive later
        return False
    if len(low.split()) < _PREF_MIN_TOKENS:
        return False
    if not _PREF_CUE_RE.search(low):
        return False
    # ADDITIONAL operator-turn gate: the cue must ALSO be in the authored residual
    # (blockquote quote-backs stripped) — a cue only in quoted-back text is dropped.
    residual = _pref_operator_text(text)
    return bool(residual) and bool(_PREF_CUE_RE.search(residual.lower()))


def _pref_entry_is_user_source(kdir, entry):
    """True iff a `preference`-tagged entry was captured with `source=user`.

    The index does NOT persist `source` (it lives only in the entry's `.md`
    frontmatter), so fall back to reading the frontmatter when the index entry
    carries no `source`. This restricts the digest to the operator's own
    `prefer` captures and EXCLUDES agent/imported `preference`-tagged learnings.
    """
    src = entry.get("source")
    if src is None:
        path = entry.get("path")
        if path:
            src = read_entry_frontmatter(os.path.join(kdir, path)).get("source")
    return src == "user"


_EXPORTS = [
    "_PREF_MIN_TOKENS",
    "_PREF_NOISE_PHRASES",
    "_PREF_CUE_RE",
    "_PREF_STATEMENT_QUESTION_RE",
    "_PREF_STRONG_CUE_RE",
    "_pref_operator_text",
    "_pref_is_capturable",
    "_pref_entry_is_user_source",
]

__all__ = list(_EXPORTS)
