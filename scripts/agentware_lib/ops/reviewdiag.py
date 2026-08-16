"""agentware_lib.ops.reviewdiag -- the review gate's SOFT-FAILURE diagnostics (layer: ops).

Owns: the two diagnostics that make a degraded review round VISIBLE -- the
soft-fail report for a reviewer whose output could not be used at all
(`_review_soft_fail_diagnostic` / `_review_emit_soft_fail_diagnostic`) and the
dropped-item report for one whose output was partly unusable
(`_review_malformed_items_diagnostic` / `_review_emit_malformed_items_diagnostic`).
Does NOT own: the normalization that DECIDES an item is malformed ->
ops.reviewschema; the spawn-failure classification -> features.reviewagent; the
untrusted-output readers -> features.reviewoutput; the fan-out that calls these
-> ops.reviewaudit.

WHY THESE EXIST AT ALL
----------------------
A review gate has exactly one dangerous failure mode: reporting "no findings"
when it actually failed to review. Every path here converts a silent zero into a
loud one.

  * A reviewer that CRASHES or returns unparseable output contributes zero
    findings -- identical, at the call site, to a reviewer that found nothing
    wrong. `_review_soft_fail_diagnostic` names the role, the exit class, and a
    bounded tail of stderr, so the round reads as DEGRADED rather than clean.
  * A reviewer whose output was partly malformed contributes fewer findings than
    it produced. `_review_malformed_items_diagnostic` reports the DROP COUNT
    alongside a bounded sample, because "3 findings" and "3 findings, 5 dropped"
    are different results and only one of them is trustworthy.

The gate deliberately SOFT-fails rather than aborting the run: one broken
reviewer should not discard the findings of the others. That trade is only safe
because the degradation is reported -- soft-failing quietly would be the worst of
both.

Bounded by construction: stderr is tailed, samples are truncated. A diagnostic
that floods the log is a diagnostic that gets scrolled past, and the whole point
is that a human notices it.

Layer deps: ops -> features (`reviewoutput` for the bounded stderr/one-line
helpers), reached BY NAME. Writes to the stream it is HANDED (`stream=None`
defaults to stderr), so tests capture rather than patch.
Determinism: pure given (role, raw output) -- no clock, no env, no I/O of its own.
"""

import sys

from agentware_lib.features.reviewoutput import (
    REVIEW_STDERR_TAIL_BYTES,
    _review_one_line,
    _review_stderr_text,
    _review_untrusted_inline,
)


def _review_stderr_tail(raw):
    """Return the last ~2KB of a captured stderr payload as text (for the
    one-line diagnostic). Never raises."""
    return _review_stderr_text(raw)[-REVIEW_STDERR_TAIL_BYTES:]


def _review_soft_fail_diagnostic(role, raw_out):
    """One-line diagnostic for a reviewer that produced NO usable findings JSON
    — either a soft-failed agent (clean exit 0 but an HTTP-200 error envelope /
    non-JSON prose on stdout) or a spawn that already failed and degraded to ''
    (its per-attempt spawn diagnostic precedes this line). Pure — computes text
    only; collapsed to one line so it can never break the log grammar. The
    snippet is raw agent stdout — fully attacker-influenceable (a hostile
    diff can steer a reviewer into emitting prose that names the gate's
    bypass env var), so it is wrapped by `_review_untrusted_inline` and
    always labeled DATA (R-SEC-02), never echoed as bare operational
    prose. `role` is ALSO collapsed to one line: the verify path builds it
    from a finding's `dimension` (reviewer output), so even though the enum
    is enforced upstream at normalize time, a newline smuggled into role
    must never forge a pristine un-fenced operational line in the log
    (defense in depth, R-SEC-02)."""
    role = _review_one_line(role, 120) or "?"
    snippet = " ".join((raw_out or "").split())[:200]
    return ("%s returned no usable findings JSON (soft-failed agent or failed "
            "spawn): %s" % (
                role,
                _review_untrusted_inline("agent stdout", snippet) if snippet
                else "<empty stdout>"))


def _review_emit_soft_fail_diagnostic(role, raw_out, stream=None):
    """Emit the soft-fail diagnostic LOUDLY (default sys.stderr — captured into
    $STATE_DIR/adversarial-review.log by the bash loop) so a reviewer counted
    out of reviewers_ok is ALWAYS named with its raw-output snippet — never
    only the vague aggregate INCOMPLETE banner. Returns the line."""
    stream = stream if stream is not None else sys.stderr
    line = _review_soft_fail_diagnostic(role, raw_out)
    stream.write("⚠️  WARN [adversarial-review]: %s\n" % line)
    return line


def _review_malformed_items_diagnostic(role, dropped, raw_out):
    """One-line diagnostic for a reviewer whose findings LIST carried
    NON-OBJECT items (strings / null — a plausible LLM persona-drift shape):
    the reviewer DROPPED defect text, so it is treated exactly like the other
    soft-fail shapes — named, snippeted, and counted OUT of reviewers_ok
    (fail-CLOSED), never silently discarded while the gate ships GREEN. Pure
    — computes text only; the snippet is raw agent stdout (fully attacker-
    influenceable) so it is wrapped by `_review_untrusted_inline` and always
    labeled DATA (R-SEC-02); `role` is collapsed to one line (defense in
    depth against log-line forgery)."""
    role = _review_one_line(role, 120) or "?"
    snippet = " ".join((raw_out or "").split())[:200]
    return ("%s emitted %d non-object finding item(s) — its reported defect "
            "text was DROPPED, so the reviewer payload is malformed and does "
            "NOT count as run (soft-failed reviewer): %s" % (
                role, dropped,
                _review_untrusted_inline("agent stdout", snippet) if snippet
                else "<empty stdout>"))


def _review_emit_malformed_items_diagnostic(role, dropped, raw_out,
                                            stream=None):
    """Emit the non-object-finding-items diagnostic LOUDLY (default
    sys.stderr — captured into $STATE_DIR/adversarial-review.log by the bash
    loop) so a reviewer counted out of reviewers_ok for dropped finding items
    is ALWAYS named with its raw-output snippet. Returns the line."""
    stream = stream if stream is not None else sys.stderr
    line = _review_malformed_items_diagnostic(role, dropped, raw_out)
    stream.write("⚠️  WARN [adversarial-review]: %s\n" % line)
    return line


_EXPORTS = [
    "_review_stderr_tail",
    "_review_soft_fail_diagnostic",
    "_review_emit_soft_fail_diagnostic",
    "_review_malformed_items_diagnostic",
    "_review_emit_malformed_items_diagnostic",
]

__all__ = list(_EXPORTS)
