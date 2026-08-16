"""agentware_lib.ops.reviewconfig -- the review gate's CONFIG axes, pinning guard and skip ledger (layer: ops).

Owns: the three tunable axes and their defaults (`REVIEW_DEFAULT_MODEL` /
`REVIEW_DEFAULT_EFFORT` / `REVIEW_DEFAULT_MAX_ROUNDS`), the fixer persona name,
the bounded int parser (`_review_clamp_int`), the env resolution
(`_review_resolve_config`), the axis RANKINGS (`REVIEW_MODEL_RANK` /
`REVIEW_EFFORT_RANK`) with the downgrade detector (`_review_axis_downgraded`) and
its warnings (`_review_pinning_warnings` / `_review_emit_pinning_warnings`), and
the skip escape hatch with its audit trail (`REVIEW_SKIP_ENV` /
`_review_skip_requested` / `_review_record_skip`).
Does NOT own: the finding vocabulary -> ops.reviewschema; prompt text ->
ops.reviewprompts; the fan-out -> ops.reviewaudit; agent spawning and its
timeouts -> features.reviewagent.

WHY A DOWNGRADE IS WARNED ABOUT RATHER THAN REFUSED
---------------------------------------------------
The gate is pinned to a strong model at high effort because an adversarial review
run by a weaker reviewer is not a cheaper review -- it is a review that finds less
and reports the same "clean". Anyone may still pin it lower; `_review_axis_downgraded`
compares the resolved value against the default's RANK and emits a warning naming
the axis and both values. Refusing outright would push people to skip the gate
entirely, which is strictly worse than a downgraded run they can see in the log.
That is why the ranks are ordered data rather than a boolean: "different from the
default" is not the question, "WEAKER than the default" is.

WHY SKIPPING IS RECORDED, NOT JUST ALLOWED
------------------------------------------
`REVIEW_SKIP_ENV` is a real escape hatch -- there are legitimate reasons to bypass
the gate. `_review_record_skip` writes the skip INTO THE WORKLOG, with its reason,
so the bypass leaves evidence in the artifact a human reads. An escape hatch that
leaves no trace is indistinguishable from a gate that never ran, and six months
later nobody can tell which diffs were actually reviewed.

Layer deps: ops -> ops (`reviewschema.REVIEW_DIMENSION_KEYS` to validate a
requested dimension subset, `sessionparse.minerfolds._review_truthy_env` for the
shared env-flag parse -- both sideways, both acyclic). It reads `os.environ` and appends to the worklog path it
is handed; it never resolves the knowledge dir itself.
Determinism: `_review_resolve_config` is a pure function of the env mapping it is
GIVEN (`env=None` defaults to `os.environ`), so tests inject rather than patch.
"""

import os
import sys

from agentware_lib.ops.reviewschema import REVIEW_DIMENSION_KEYS
from agentware_lib.ops.sessionparse.minerfolds import _review_truthy_env


REVIEW_DEFAULT_MODEL = "opus"


REVIEW_DEFAULT_EFFORT = "max"


REVIEW_DEFAULT_MAX_ROUNDS = 3


REVIEW_FIXER_PERSONA = "agentware-execution"


REVIEW_SKIP_ENV = "AGENTWARE_SKIP_ADVERSARIAL_REVIEW"


def _review_clamp_int(raw, default, lo, hi):
    """Parse `raw` as an int clamped to [lo, hi]; fall back to `default` on junk."""
    try:
        val = int(str(raw).strip())
    except (TypeError, ValueError, AttributeError):
        return default
    return max(lo, min(hi, val))


def _review_resolve_config(env=None):
    """Resolve the review axes ONCE (Q2): model (default opus), effort (default
    max), fanout (# dimension reviewers, default = all dimensions), max_rounds
    (default 3). Pure/deterministic — reads env only, spawns nothing. Task 3
    layers the loud sub-Opus WARN and the skip-hatch DECISION on top of these
    resolved values; this function stays side-effect-free so tests can assert the
    resolution in isolation.
    """
    env = env if env is not None else os.environ
    model = (env.get("AGENTWARE_REVIEW_MODEL") or "").strip() or REVIEW_DEFAULT_MODEL
    effort = (env.get("AGENTWARE_REVIEW_EFFORT") or "").strip() or REVIEW_DEFAULT_EFFORT
    n_dims = len(REVIEW_DIMENSION_KEYS)
    fanout = _review_clamp_int(env.get("AGENTWARE_REVIEW_FANOUT"), n_dims, 1, n_dims)
    max_rounds = _review_clamp_int(
        env.get("AGENTWARE_REVIEW_MAX_ROUNDS"), REVIEW_DEFAULT_MAX_ROUNDS, 1, 20)
    return {"model": model, "effort": effort, "fanout": fanout,
            "max_rounds": max_rounds}


# --- Task 3: model/effort pinning WARN + escape-hatch record -----------------
# Q2 pins reviewers+fixer to Opus/max, overridable UP only; a sub-Opus/sub-max
# setting is legal but emits a LOUD recorded WARN so a weakened gate is never
# silent. Q8 escape hatch (AGENTWARE_SKIP_ADVERSARIAL_REVIEW) is honored ONLY by
# RECORDING a `> DECISION:` in the feature worklog + a loud notice — never a
# silent skip. Rank maps let us distinguish "below the bar" (warn) from
# "at/above" (silent); an UNKNOWN tier cannot be proven to meet the bar, so it
# warns conservatively rather than passing silently.
REVIEW_MODEL_RANK = {"haiku": 1, "sonnet": 2, "opus": 3}


REVIEW_EFFORT_RANK = {"low": 1, "medium": 2, "high": 3, "max": 4}


def _review_axis_downgraded(value, rank_map, default_value):
    """True iff `value` sits BELOW the pinned `default_value` tier, OR is an
    unknown tier we cannot prove meets the bar. The default itself (and any
    recognized higher tier) is NOT a downgrade. Case/space tolerant."""
    v = (value or "").strip().lower()
    default_rank = rank_map.get((default_value or "").strip().lower())
    if default_rank is None:  # misconfigured default — never crash the gate
        return False
    vr = rank_map.get(v)
    if vr is None:
        return True  # unknown tier -> cannot confirm >= bar -> warn
    return vr < default_rank


def _review_pinning_warnings(cfg):
    """Return the list of loud WARN lines for any review axis set BELOW the
    pinned Opus/max bar (Q2). Empty when model==opus AND effort==max. Pure —
    computes text only; `_review_emit_pinning_warnings` does the I/O."""
    cfg = cfg or {}
    out = []
    if _review_axis_downgraded(cfg.get("model"), REVIEW_MODEL_RANK,
                               REVIEW_DEFAULT_MODEL):
        out.append(
            "AGENTWARE_REVIEW_MODEL=%r is BELOW the pinned '%s': the adversarial "
            "review gate is running on a WEAKER model — defects it misses ship "
            "into every future self-extension run." % (
                cfg.get("model"), REVIEW_DEFAULT_MODEL))
    if _review_axis_downgraded(cfg.get("effort"), REVIEW_EFFORT_RANK,
                               REVIEW_DEFAULT_EFFORT):
        out.append(
            "AGENTWARE_REVIEW_EFFORT=%r is BELOW the pinned '%s': reviewers may "
            "rush and miss defects." % (cfg.get("effort"), REVIEW_DEFAULT_EFFORT))
    return out


def _review_emit_pinning_warnings(cfg, stream=None):
    """Emit the sub-Opus/sub-max WARN lines LOUDLY to stderr and return them so
    callers/tests can assert. No-op (empty list) when model/effort are at/above
    the pinned bar."""
    stream = stream if stream is not None else sys.stderr
    warnings = _review_pinning_warnings(cfg)
    for w in warnings:
        stream.write("⚠️  WARN [adversarial-review]: %s\n" % w)
    return warnings


def _review_skip_requested(env=None):
    """True iff the operator set AGENTWARE_SKIP_ADVERSARIAL_REVIEW to a truthy
    value (Q8 escape hatch). Honored ONLY through `_review_record_skip` so the
    skip is always RECORDED, never silent."""
    env = env if env is not None else os.environ
    return _review_truthy_env(env.get(REVIEW_SKIP_ENV))


def _review_record_skip(worklog_path, reason=None, env=None, stream=None):
    """Honor the Q8 escape hatch by RECORDING it, never silently: append a
    self-closing `> DECISION:` marker (paired with a `> WAIVED:` closer so a
    runtime skip does not perpetually gate the loop's promise) to the feature
    worklog AND emit a loud stderr notice. Returns the DECISION text written, or
    None if no worklog path was given (still emits the loud notice).

    R-SEC-02: `reason` is operator-supplied text placed ONLY inside a worklog
    marker line — never argv, never a --target, never executed.
    """
    env = env if env is not None else os.environ
    stream = stream if stream is not None else sys.stderr
    reason = (reason
              or (env.get("AGENTWARE_SKIP_ADVERSARIAL_REVIEW_REASON") or "").strip()
              or "operator set %s (CI/offline emergency — Opus unreachable)"
              % REVIEW_SKIP_ENV)
    # Collapse to a single line so it can never break the marker grammar.
    reason = " ".join(str(reason).splitlines()).strip()
    decision = ("adversarial-review gate SKIPPED via %s — %s" % (
        REVIEW_SKIP_ENV, reason))
    stream.write(
        "⚠️  WARN [adversarial-review]: gate SKIPPED (%s). This ships a "
        "self-extension diff WITHOUT the Opus adversarial audit; recorded as a "
        "worklog DECISION.\n" % REVIEW_SKIP_ENV)
    if not worklog_path:
        return None
    line = ("> DECISION: %s\n> WAIVED: runtime skip — recorded for audit, not a "
            "promotable knowledge decision\n" % decision)
    try:
        with open(worklog_path, "a", encoding="utf-8") as f:
            f.write("\n" + line)
    except OSError as exc:
        stream.write(
            "⚠️  WARN [adversarial-review]: could not record skip DECISION to "
            "%s: %s\n" % (worklog_path, exc))
        return decision
    return decision


_EXPORTS = [
    "REVIEW_DEFAULT_MODEL",
    "REVIEW_DEFAULT_EFFORT",
    "REVIEW_DEFAULT_MAX_ROUNDS",
    "REVIEW_FIXER_PERSONA",
    "REVIEW_SKIP_ENV",
    "REVIEW_MODEL_RANK",
    "REVIEW_EFFORT_RANK",
    "_review_clamp_int",
    "_review_resolve_config",
    "_review_axis_downgraded",
    "_review_pinning_warnings",
    "_review_emit_pinning_warnings",
    "_review_skip_requested",
    "_review_record_skip",
]

__all__ = list(_EXPORTS)
