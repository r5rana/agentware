"""agentware_lib.features.reviewagent -- SPAWNING a review agent, safely (layer: features).

Owns: the subprocess seam every LLM-backed review runs through -- the agent
invocation (`_review_invoke_agent`) with its retry/backoff and timeout breaker,
the single-spawn primitive (`_review_spawn_once`), the failure classifiers
(`_review_is_transient_failure` / `_review_is_deadline_class`), the diagnostics
(`_review_spawn_diagnostic` / `_review_emit_spawn_diagnostic` /
`_review_stderr_text` / `_review_one_line`), and the tuning constants.

WHY THIS IS ITS OWN MODULE, AND WHY IT MOVED AT TASK 44
An agent spawn is not "call a function": it crosses a process boundary into a
non-deterministic, network-backed, slow, occasionally-hanging peer, and every
consumer needs the SAME answers about retries, deadlines and how to treat what
comes back. `checkup` needed it for its narrative, so the choice was to duplicate
those answers inside checkup or to give the family the module it always needed.
Duplicating a retry/breaker policy is how two callers end up disagreeing about
what "transient" means.

The output is UNTRUSTED by construction (R-SEC-02) and is handed to
features.reviewoutput before any of it can reach a document -- a SECURITY boundary
kept in its own module because sanitising model text and managing a subprocess
fail in different ways and change for different reasons.

The TIMEOUT BREAKER is deliberately module-level state: when a slow peer trips
enough deadline-class failures, further spawns short-circuit instead of each
paying the full timeout. A per-call breaker would never trip.

Does NOT own: sanitising what comes back -> features.reviewoutput; the `review` VERB and its report/remediation flow, which stay
resident until their own port; what any caller does with the narrative;
the checkup self-exam that consumes this (ops.checkup).
Layer deps: features -> core (config, dates) plus stdlib. All DOWNWARD.
Fan-in: 1 at landing (ops.checkup); grows to the `review` verb at its port
(see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all.
"""

import errno
import json
import os
import re
import subprocess
import sys
import time

import agentware_lib.core.config as config
from agentware_lib.core.dates import _now_iso
from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.features.reviewoutput import (
    REVIEW_STDERR_TAIL_BYTES,
    _review_extract_json,
    _review_fence,
    _review_one_line,
    _review_stderr_text,
    _review_untrusted_inline,
)


REVIEW_REVIEWER_PERSONA = "agentware-adversarial-reviewer"


# Hard per-agent wall-clock cap (seconds). Opus at max effort over a diff is slow
# but never unbounded; a hung/absent spawn returns '' rather than stalling the gate.
REVIEW_AGENT_TIMEOUT = 900


# Transient-spawn resilience: a momentary API/network blip (429/529/overload/
# rate-limit/timeout, a transient 5xx, or a connection-level ECONNRESET-class
# error — see REVIEW_TRANSIENT_STDERR_RE) on a reviewer must not falsely
# BLOCK a correct run. A TRANSIENT
# failure (see _review_is_transient_failure) is retried up to
# REVIEW_SPAWN_RETRIES times with exponential backoff
# (REVIEW_SPAWN_BACKOFF_BASE * 2**attempt seconds). Terminal errors (OSError:
# agent binary absent) and clean exit-0 runs are NEVER retried, and exhausted
# retries still return '' — fail-closed is preserved by construction.
# REVIEW_SLEEP is an injectable seam so tests record backoff with ZERO real sleep.
#
# Retry is READ-ONLY-caller policy: the retry assumes a failed attempt had no
# side effects, which holds for reviewer/verify/critic spawns but NOT for the
# MUTATING remediation fixer (edits package files) nor for the report-only
# checkup narrative (must degrade cheaply, not stall a background job 3x).
# Those callers pass retries=0 through the keyword-defaulted `retries` param —
# exactly the pre-resilience single-attempt behavior.
#
# Timeout policy: a subprocess timeout just consumed its FULL deadline, so an
# identical-deadline retry has near-zero success probability against a genuine
# hang while multiplying wall-clock. A timed-out attempt is therefore retried
# at most REVIEW_SPAWN_TIMEOUT_RETRIES times per invocation (default 1 — one
# second chance for a momentary network stall), and when a SECOND attempt of
# the same invocation ALSO times out (two full-deadline timeouts within one
# invocation — back to back or with fast transient failures like 429s
# interleaved, including on the final attempt = a deterministic
# hang/blackhole), the process-wide _REVIEW_TIMEOUT_BREAKER
# trips: subsequent timeouts in this process are TERMINAL (no retry), so a
# blackholed audit fan-out costs ~one extra deadline total, not 3x per agent.
# Any successful spawn resets the breaker (the runtime is provably reachable).
#
# DEADLINE-CLASS is broader than a subprocess timeout: an unreachable runtime
# that the claude CLI resolves ITSELF (connect ETIMEDOUT / internal request
# timeout / streaming-stall abort) exits NONZERO after burning most of the
# deadline — it never raises TimeoutExpired, so `timed_out` alone would let it
# masquerade as a fast transient and re-pay the FULL retry schedule per agent.
# `_review_spawn_once` therefore measures each attempt's wall-clock `duration`;
# a FAILED attempt that consumed >= REVIEW_SLOW_FAILURE_FRACTION of the
# deadline is counted as a deadline expiry for the timeout-retry cap and the
# breaker, exactly as if the subprocess deadline had fired.
REVIEW_SPAWN_RETRIES = 2


REVIEW_SPAWN_TIMEOUT_RETRIES = 1


REVIEW_SPAWN_BACKOFF_BASE = 1.0


REVIEW_SLOW_FAILURE_FRACTION = 0.5


REVIEW_SLEEP = time.sleep


_REVIEW_TIMEOUT_BREAKER = {"tripped": False}


# ARG_MAX containment (remediation round 8): the audit prompt embeds the
# ENTIRE diff + acceptance criteria in one argv element, and the OS caps argv:
# macOS shares ~1MiB (ARG_MAX) between argv AND the environment; Linux
# additionally caps a SINGLE argv element at MAX_ARG_STRLEN (128KiB). A
# legitimate self-extension whose prompt exceeded those caps made EVERY spawn
# fail pre-fork with OSError E2BIG — terminal, no retry, hard-blocking the
# gate for all rounds. A prompt whose UTF-8 byte length exceeds this threshold
# is therefore delivered via STDIN instead (`claude -p` reads the prompt from
# stdin when no positional prompt is given — the standard
# `cat prompt | claude -p` shape), which has no argv size limit. 64KiB sits
# safely below every platform cap while leaving typical prompts on the
# argv path every existing fake-CLI dispatcher reads (`${!#}`).
REVIEW_PROMPT_ARGV_MAX = 65536


def _review_spawn_once(cmd, env, cwd, timeout, stdin_data=None):
    """Run ONE raw agent-spawn attempt — the single place the agent-spawn
    `subprocess.run` lives. (The gate-chain seam `_review_subprocess_rc` is a
    SEPARATE, deliberate subprocess site — do not consolidate.)

    `stdin_data` (keyword-defaulted — every existing 4-positional stub stays
    valid) carries an OVERSIZE prompt the wrapper routed OFF argv (ARG_MAX
    containment, see REVIEW_PROMPT_ARGV_MAX): when set, it is piped to the
    child's stdin as UTF-8 bytes (errors='replace') instead of stdin=DEVNULL.
    An OSError whose errno is E2BIG (argv+env exceed the OS ARG_MAX — still
    reachable via a huge environment) is classified as an UNSPAWNABLE command
    naming ARG_MAX, so the diagnostic taxonomy can never mislabel it
    '<role> agent not found' (a missing-binary misdirection).

    Returns a structured dict so the wrapper can classify the outcome instead
    of discarding it:
      {stdout, returncode, stderr_tail (last ~2KB), stderr_transient,
       timed_out, oserror, duration}
    `returncode` is None when the process never completed (timeout/OSError);
    `oserror` carries the OSError text (agent binary not found, etc.).
    `stderr_transient` is the transient-signature match computed HERE against
    the FULL captured stderr — the ~2KB `stderr_tail` is for the diagnostic
    only, so a 429/rate-limit line followed by >2KB of stack trace is still
    classified transient (the tail alone would truncate the signature away).
    `duration` is the attempt's wall-clock seconds (time.monotonic), measured
    HERE so the wrapper can recognize a DEADLINE-CLASS failure the CLI
    resolved itself (connect ETIMEDOUT / streaming stall exiting nonzero
    after ~most of the deadline) — a hang shape `timed_out` alone misses.
    Never raises — including on arbitrary BYTE output: both streams are
    captured as bytes (no text mode) and decoded HERE via
    `_review_stderr_text` (errors='replace'), because subprocess.run's
    strict text-mode decode raises UnicodeDecodeError inside communicate()
    on a single invalid UTF-8 byte (truncated multibyte char / binary crash
    dump) from a normally-completing process — which would crash the whole
    audit fan-out instead of degrading fail-closed.

    NUL-SAFE argv: the prompt embeds the raw diff/criteria text verbatim, and
    git emits text hunks WITH embedded NUL bytes when the NUL sits past its
    ~8KB binary-sniff window (valid UTF-8, so no decode ever rejects it) — but
    `subprocess.run` raises ValueError('embedded null byte') BEFORE fork on
    any argv element carrying \\x00, which is caught by neither except clause
    below. Every argv element is therefore sanitized HERE (\\x00 -> U+FFFD,
    the same replacement the decode path uses), and — defense in depth — a
    residual ValueError from the spawn (e.g. a NUL smuggled into env) is
    caught and classified TERMINAL via `oserror`, exactly like an absent
    binary: the audit degrades fail-closed instead of crashing with a
    traceback the bash gate would misread as 'confirmed blocking findings'."""
    result = {"stdout": "", "returncode": None, "stderr_tail": "",
              "stderr_transient": False, "timed_out": False, "oserror": None,
              "duration": 0.0}
    cmd = [str(c).replace("\x00", "�") for c in cmd]
    if stdin_data is None:
        stdin_kwargs = {"stdin": subprocess.DEVNULL}
    else:
        # Oversize-prompt delivery: bytes-encoded here (errors='replace' —
        # the same tolerant policy as every other decode/encode on this seam)
        # so an unpaired surrogate can never crash the encode. NUL bytes are
        # legal on a pipe, so no argv-style sanitization is needed.
        stdin_kwargs = {"input": str(stdin_data).encode("utf-8", "replace")}
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env,
            timeout=timeout, **stdin_kwargs)
    except ValueError as exc:
        # Unspawnable argv/env (embedded NUL etc.) — the process never ran.
        # TERMINAL (no retry): retrying an identical unspawnable command can
        # never succeed. Recorded on `oserror` so classification/diagnostics
        # treat it exactly like the other never-ran shape.
        result["oserror"] = "unspawnable command: %s" % (
            str(exc) or exc.__class__.__name__)
        result["duration"] = time.monotonic() - start
        return result
    except subprocess.TimeoutExpired as exc:
        full = _review_stderr_text(exc.stderr)
        result["timed_out"] = True
        result["stderr_tail"] = full[-REVIEW_STDERR_TAIL_BYTES:]
        result["stderr_transient"] = bool(
            REVIEW_TRANSIENT_STDERR_RE.search(full))
        result["duration"] = time.monotonic() - start
        return result
    except OSError as exc:
        msg = str(exc) or exc.__class__.__name__
        if getattr(exc, "errno", None) == errno.E2BIG:
            # NOT a missing binary: the argv+env exceed the OS ARG_MAX (the
            # prompt embeds the diff under audit). Classified as an
            # unspawnable command — TERMINAL like every never-ran shape —
            # and NAMED accurately so the operator is never misdirected
            # toward a missing-binary diagnosis. (The wrapper's stdin
            # routing makes this branch a residual defense: only a huge
            # ENVIRONMENT can still trip E2BIG.)
            msg = ("unspawnable command: argv+env exceed the OS ARG_MAX "
                   "(prompt/diff too large for argv): %s" % msg)
        result["oserror"] = msg
        result["duration"] = time.monotonic() - start
        return result
    full = _review_stderr_text(proc.stderr)
    result["stdout"] = _review_stderr_text(proc.stdout)
    result["returncode"] = proc.returncode
    result["stderr_tail"] = full[-REVIEW_STDERR_TAIL_BYTES:]
    result["stderr_transient"] = bool(REVIEW_TRANSIENT_STDERR_RE.search(full))
    result["duration"] = time.monotonic() - start
    return result


# Stderr signatures of a MOMENTARY failure worth retrying: API overload /
# rate-limit / timeout words and status codes (429/529) PLUS the
# connection-level shapes a momentary network blip actually prints —
# ECONNRESET / ECONNREFUSED / ENOTFOUND / EAI_AGAIN / ETIMEDOUT-class errno
# names, 'socket hang up', 'connection reset/refused/closed/aborted',
# 'fetch failed', 'network error', and the transient HTTP 5xx statuses
# (500/502/503/504, 'internal server error', 'bad gateway', 'service
# unavailable', 'gateway timeout'). Bare status codes are \b-anchored so
# digits inside larger numbers or identifiers ('5023', 'module_4290') never
# match. Anything else nonzero-exit is treated as terminal — retrying a
# genuine auth/config/crash error only delays the fail-closed BLOCK.
REVIEW_TRANSIENT_STDERR_RE = re.compile(
    r"(?i)\b(?:429|500|502|503|504|529)\b"
    r"|overload|rate.?limit|too many requests|timeout|timed out"
    r"|\bECONN(?:RESET|REFUSED|ABORTED)\b|\bENOTFOUND\b|\bEAI_AGAIN\b"
    r"|\bETIMEDOUT\b|\bENETUNREACH\b|\bEHOSTUNREACH\b|\bEPIPE\b"
    r"|socket hang.?up|connection (?:reset|refused|closed|aborted)"
    r"|network (?:error|is unreachable)|fetch failed"
    r"|internal server error|bad gateway|service unavailable"
    r"|gateway time.?out")


def _review_is_transient_failure(spawn_result):
    """Classify ONE `_review_spawn_once` result: True iff the failed attempt
    looks MOMENTARY and is worth retrying. Rules (per
    decide-adversarial-review-gate-spawn-resilience):
      - returncode == 0 is never a failure -> False (even with noisy stderr);
      - an OSError (agent binary not found) is TERMINAL -> False;
      - a subprocess timeout is transient -> True (the RETRY POLICY in
        _review_invoke_agent separately caps timeout retries and trips the
        process-wide hang breaker — classification stays pure);
      - a nonzero exit is transient iff its FULL stderr carried a transient
        signature per REVIEW_TRANSIENT_STDERR_RE — 429/529/overload/rate-
        limit/timeout, a transient 5xx status, or a connection-level
        ECONNRESET-class blip (`stderr_transient`,
        computed at capture time in _review_spawn_once so a signature buried
        before >2KB of trailing stack trace is not truncated away); the ~2KB
        `stderr_tail` is re-checked as a fallback for hand-built results.
    Pure and never raises (tolerates None / partial dicts)."""
    res = spawn_result or {}
    if res.get("returncode") == 0:
        return False
    if res.get("oserror"):
        return False
    if res.get("timed_out"):
        return True
    if res.get("stderr_transient"):
        return True
    return bool(REVIEW_TRANSIENT_STDERR_RE.search(res.get("stderr_tail") or ""))


def _review_is_deadline_class(spawn_result, timeout):
    """True iff ONE failed `_review_spawn_once` result consumed a deadline:
    either the subprocess deadline actually fired (`timed_out`), OR the claude
    CLI resolved the hang ITSELF (connect ETIMEDOUT / internal request timeout
    / streaming-stall abort exiting nonzero) after burning >=
    REVIEW_SLOW_FAILURE_FRACTION of the deadline — measured via the attempt's
    wall-clock `duration`. Without the duration clause, a blackholed runtime
    whose CLI stalls ~600s then exits 1 with 'fetch failed ... ETIMEDOUT'
    would classify as a FAST stderr transient and re-pay the full 3-attempt
    schedule per agent (~hours across a fan-out) instead of the documented
    ~one extra deadline total. Pure and never raises; hand-built results
    without a numeric `duration` (hermetic test stubs) are never deadline-
    class unless `timed_out` is set."""
    res = spawn_result or {}
    if res.get("timed_out"):
        return True
    dur = res.get("duration")
    return bool(
        timeout and isinstance(dur, (int, float)) and not isinstance(dur, bool)
        and dur >= REVIEW_SLOW_FAILURE_FRACTION * timeout)


def _review_spawn_diagnostic(role, res, timeout):
    """Return the ONE-LINE structured diagnostic for a failed spawn attempt —
    naming WHICH agent (`role`) and WHY — or None when the attempt succeeded
    (returncode == 0). Pure — computes text only; the wrapper does the I/O
    (mirroring the `_review_pinning_warnings` / `_review_emit_pinning_warnings`
    split). Failure taxonomy:
      OSError        -> '<role> agent not found: <err>'
      timeout        -> '<role> timed out after <N>s'
      nonzero exit   -> '<role> exited <code>: <labeled stderr tail>'
    The stderr tail is collapsed to a single line so the diagnostic can never
    break the one-line log grammar, and — because it is fully
    agent/attacker-influenceable text — it is wrapped by
    `_review_untrusted_inline` so it is always labeled DATA (R-SEC-02), never
    echoed as bare operational prose. `role` is ALSO collapsed to one line:
    the verify path builds it from a finding's `dimension` (reviewer output),
    so even though the enum is enforced upstream at normalize time, a newline
    smuggled into role must never be able to forge a pristine un-fenced
    operational line in the log (defense in depth, R-SEC-02)."""
    role = _review_one_line(role, 120) or "?"
    res = res or {}
    if res.get("oserror"):
        err = str(res["oserror"])
        if err.startswith("unspawnable command"):
            # NOT a missing binary (embedded NUL, E2BIG/ARG_MAX, ...) — name
            # the real class so the operator is never misdirected toward an
            # agent-not-found diagnosis (remediation round 8).
            return "%s could not be spawned: %s" % (role, err)
        return "%s agent not found: %s" % (role, err)
    if res.get("timed_out"):
        return "%s timed out after %ss" % (role, timeout)
    if res.get("returncode") != 0:
        tail = " ".join((res.get("stderr_tail") or "").split())
        return "%s exited %s: %s" % (
            role, res.get("returncode"),
            _review_untrusted_inline("agent stderr", tail) if tail
            else "<no stderr captured>")
    return None


def _review_emit_spawn_diagnostic(role, res, timeout, stream=None,
                                  warn_tag="adversarial-review"):
    """Emit the spawn-failure diagnostic LOUDLY to stderr (default — the bash
    loop captures stderr into $STATE_DIR/adversarial-review.log) and return the
    line so callers/tests can assert. No-op (None) on a successful attempt.
    `warn_tag` names the SUBSYSTEM whose spawn failed (default the review
    gate); the checkup caller passes 'checkup' so a checkup degradation is
    never mislabeled as an adversarial-review event in captured logs."""
    stream = stream if stream is not None else sys.stderr
    diag = _review_spawn_diagnostic(role, res, timeout)
    if diag:
        stream.write("⚠️  WARN [%s]: spawn failed — %s\n" % (warn_tag, diag))
    return diag


def _review_build_invocation(cfg, persona, prompt):
    """Build (argv, stdin_data, env) for one agent invocation.

    ARG_MAX containment: a prompt whose UTF-8 byte length exceeds
    REVIEW_PROMPT_ARGV_MAX would fail PRE-FORK with OSError E2BIG on every
    attempt — terminal, unretryable, and it would hard-block the gate for a
    legitimately-large diff. Such a prompt is delivered on the child's STDIN
    instead (`claude -p` reads the prompt from stdin when no positional prompt is
    given). Smaller prompts keep the exact run_agent-mirror argv shape every
    existing fake-CLI dispatcher reads (`${!#}`), so the containment is invisible
    to them.

    The environment is pinned non-interactive (CI, npm_config_yes,
    HOMEBREW_NO_AUTO_UPDATE) because a child that stops to ask a question is
    indistinguishable from a hang, and this runs unattended.
    """
    cli = (os.environ.get("AGENTWARE_CLI") or "claude").strip() or "claude"
    cmd = [cli, "-p", "--agent", persona, "--dangerously-skip-permissions"]
    model = cfg.get("model")
    if model:
        cmd += ["--model", str(model)]
    prompt = "" if prompt is None else str(prompt)
    stdin_data = None
    if len(prompt.encode("utf-8", "replace")) > REVIEW_PROMPT_ARGV_MAX:
        stdin_data = prompt
    else:
        cmd += [prompt]
    env = dict(os.environ)
    env["CI"] = "true"
    env["npm_config_yes"] = "true"
    env["HOMEBREW_NO_AUTO_UPDATE"] = "1"
    effort = cfg.get("effort")
    if effort:
        env["AGENTWARE_REVIEW_EFFORT"] = str(effort)
    return cmd, stdin_data, env


def _review_invoke_agent(role, persona, prompt, cfg, timeout=None, cwd=None,
                         stream=None, retries=None,
                         warn_tag="adversarial-review"):
    """Spawn ONE `claude -p --agent <persona>` subprocess, mirroring
    run_agent()'s claude branch (Q3), and return its raw stdout text.

    THE single seam hermetic tests monkeypatch — no test spawns a real Opus.
    `role` is a label ('reviewer:<dim>', 'verify', 'critic', 'fixer') for
    observability and to let a stub dispatch canned output. Model comes from
    cfg['model']; the CLI exposes no reasoning-effort flag (run_agent passes only
    --model), so effort is carried the two ways a spawn can: an env var
    (AGENTWARE_REVIEW_EFFORT) and a prompt preamble the caller builds. Degrades to
    '' on spawn failure/timeout so a hung agent never stalls the gate. The raw
    subprocess attempt lives in `_review_spawn_once`; this wrapper builds
    cmd/env and enforces the str contract: stdout on exit 0, else ''. A failed
    attempt is no longer silent: its structured one-line diagnostic (which
    agent, exit code + stderr tail / timeout / agent-not-found) is emitted via
    `_review_emit_spawn_diagnostic` to `stream` (default sys.stderr, which the
    bash loop captures into $STATE_DIR/adversarial-review.log).

    TRANSIENT-ONLY retry: an attempt `_review_is_transient_failure` classifies
    as momentary (timeout, or a REVIEW_TRANSIENT_STDERR_RE stderr signature:
    429/529/overload/rate-limit, a transient 5xx, or a connection-level
    ECONNRESET-class blip)
    is retried up to `retries` times (default REVIEW_SPAWN_RETRIES) with
    exponential backoff via the injectable REVIEW_SLEEP hook
    (REVIEW_SPAWN_BACKOFF_BASE * 2**attempt seconds, attempt = 0-based index of
    the failed attempt). A clean exit-0 (even with empty stdout) and a terminal
    OSError are NEVER retried. Exhausted retries still return '' — fail-closed
    is byte-for-byte unchanged.

    `retries` exists because the retry assumes the failed attempt had NO side
    effects — true for read-only reviewer/verify/critic spawns, FALSE for the
    mutating remediation fixer (a killed-mid-edit fixer must never be replayed
    over the half-mutated tree) and wrong for the report-only checkup narrative
    (a background job must degrade cheaply, not stall 3x). Both pass retries=0
    — exactly the pre-resilience single-attempt behavior.

    Timeout policy: a timed-out attempt already consumed its FULL deadline, so
    it is retried at most REVIEW_SPAWN_TIMEOUT_RETRIES times (default 1), and
    two full-deadline timeouts in one invocation — back to back or with fast
    transient failures interleaved, including a timeout landing on the final
    attempt (bookkeeping runs BEFORE the exhaustion return) — trip the
    process-wide
    _REVIEW_TIMEOUT_BREAKER — subsequent timeouts in this process are TERMINAL
    (a blackholed fan-out costs ~one extra deadline total, not 3x per agent).
    DEADLINE-CLASS (`_review_is_deadline_class`) is broader than `timed_out`:
    a hang the CLI resolves ITSELF (ETIMEDOUT-style nonzero exit after
    burning >= REVIEW_SLOW_FAILURE_FRACTION of the deadline, per the measured
    attempt duration) counts identically toward the cap and the breaker.
    Any success resets the breaker. `warn_tag` labels the WARN lines with the
    calling subsystem (default 'adversarial-review'; checkup passes 'checkup').
    """
    cfg = cfg or {}
    cmd, stdin_data, env = _review_build_invocation(cfg, persona, prompt)
    eff_timeout = timeout or REVIEW_AGENT_TIMEOUT
    # A NONZERO exit (API/auth/rate-limit error, crash), timeout, or OSError is a
    # FAILED run, not empty output — surface WHY (one-line diagnostic per failed
    # attempt), retry ONLY a transient blip (bounded, exponential backoff), then
    # return '' so the orchestrator counts it as a reviewer that did NOT run
    # (fail-CLOSED), matching the gate-chain seam `_review_subprocess_rc` whose
    # whole point is that a broken/absent agent can NEVER silently pass.
    eff_retries = REVIEW_SPAWN_RETRIES if retries is None else max(0, retries)
    timeouts = 0
    # The extra stdin_data argument is passed ONLY for an oversize prompt so
    # every existing 4-positional `_review_spawn_once` test stub stays valid.
    spawn_args = (cmd, env, cwd or REPO_ROOT, eff_timeout)
    if stdin_data is not None:
        spawn_args += (stdin_data,)
    for attempt in range(eff_retries + 1):
        res = _review_spawn_once(*spawn_args)
        if res["returncode"] == 0:
            # The runtime is provably reachable — reset the hang breaker.
            _REVIEW_TIMEOUT_BREAKER["tripped"] = False
            return res["stdout"]
        _review_emit_spawn_diagnostic(role, res, eff_timeout, stream=stream,
                                      warn_tag=warn_tag)
        if _review_is_deadline_class(res, eff_timeout):
            # Timeout bookkeeping runs BEFORE the exhaustion check so a full-
            # deadline timeout landing on the FINAL attempt still counts —
            # otherwise a mixed sequence like [429, timeout, timeout] would
            # burn two full deadlines yet leave the breaker untripped, and
            # every later agent in a blackholed fan-out would re-pay its own
            # extra deadlines (hours of wall-clock vs the documented ~one
            # extra deadline total). DEADLINE-CLASS covers both a subprocess
            # deadline expiry (timed_out) AND a hang the CLI resolved itself
            # (ETIMEDOUT-style nonzero exit after burning >=
            # REVIEW_SLOW_FAILURE_FRACTION of the deadline, per the measured
            # attempt duration) — the latter used to masquerade as a fast
            # stderr transient and re-pay the full schedule per agent.
            timeouts += 1
            if _REVIEW_TIMEOUT_BREAKER["tripped"] \
                    or timeouts > REVIEW_SPAWN_TIMEOUT_RETRIES:
                # A full-deadline timeout retried at the SAME deadline has
                # near-zero success against a genuine hang: cap per-invocation
                # timeout retries, and once TWO attempts of one invocation
                # have timed out (back to back or with fast transient
                # failures interleaved), treat the runtime as
                # deterministically hung for this process.
                _REVIEW_TIMEOUT_BREAKER["tripped"] = True
                return ""
        if attempt >= eff_retries or not _review_is_transient_failure(res):
            return ""
        REVIEW_SLEEP(REVIEW_SPAWN_BACKOFF_BASE * (2 ** attempt))
    return ""


_EXPORTS = [
    "REVIEW_AGENT_TIMEOUT",
    "REVIEW_PROMPT_ARGV_MAX",
    "REVIEW_REVIEWER_PERSONA",
    "REVIEW_SLEEP",
    "REVIEW_SLOW_FAILURE_FRACTION",
    "REVIEW_SPAWN_BACKOFF_BASE",
    "REVIEW_SPAWN_RETRIES",
    "REVIEW_SPAWN_TIMEOUT_RETRIES",
    "REVIEW_TRANSIENT_STDERR_RE",
    "_REVIEW_TIMEOUT_BREAKER",
    "_review_is_transient_failure",
    "_review_is_deadline_class",
    "_review_spawn_diagnostic",
    "_review_emit_spawn_diagnostic",
    "_review_spawn_once",
    "_review_invoke_agent",
]

__all__ = list(_EXPORTS)
