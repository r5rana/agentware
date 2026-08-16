"""agentware_lib.ops.reviewremediate -- the fixer loop, the REGRESSION GATE CHAIN and the `remediate` verb (layer: ops).

Owns: the test-module resolver and bounded subprocess runner
(`_review_test_module` / `_review_subprocess_rc`), the gate chain that must pass
before a fix is accepted (`_review_run_gate_chain`), the remediation driver
(`_review_run_remediation`), the findings loader (`_review_load_findings`), the
renderer (`_review_print_remediation`) and the `remediate` handler + registrar
(`cmd_remediate` / `register_remediate`).
Does NOT own: the fix status vocabulary and its normalizer -> ops.reviewschema;
the fixer prompt -> ops.reviewprompts; the fixer persona and model axis ->
ops.reviewconfig; agent spawning -> features.reviewagent; the audit that PRODUCED
the findings -> ops.reviewaudit; the report -> ops.reviewreport.

THE GATE CHAIN IS THE POINT
---------------------------
An agent that edits code to satisfy a finding will report success. That report is
worth nothing on its own -- the model has every incentive to believe it fixed the
thing, and no way to check. `_review_run_gate_chain` re-runs the project's own
tests in a SUBPROCESS and the chain's exit code, not the agent's claim, is what
decides whether a fix is accepted. This is the same discipline the rest of the
toolkit applies to itself: verification comes from the project's own commands,
never from the actor being verified.

Bounded on purpose: the subprocess runs under a timeout, so a fix that hangs the
suite fails the gate instead of hanging the remediation. A remediation loop that
can wedge is one nobody will run unattended.

FINDINGS COME IN VERIFIED, NOT RAW
----------------------------------
`_review_load_findings` reads the artifacts an audit round already produced.
Remediation acts only on findings that survived the verification stage -- fixing
a plausible-but-refuted finding means editing working code to satisfy a claim
that was already shown to be wrong.

Layer deps: ops -> ops (`reviewschema` for `REVIEW_FIX_STATUSES` /
`_review_normalize_fix`, `reviewconfig` for the fixer persona and axes,
`reviewprompts` for the fixer prompt, `reviewaudit._review_resolve_diff` -- the
one sibling edge, acyclic: reviewaudit does not import this module), ops ->
features (`reviewagent`, `reviewoutput`), ops -> core (`paths.REPO_ROOT`). The registrar is resolved by
the assembler through `_PARSER_REGISTRATION`, not through `_EXPORTS`.
Determinism: the driver is deterministic given its inputs; the agent it spawns is
not, which is exactly why the gate chain and not the agent decides the outcome.
"""

import argparse
import json
import os
import subprocess
import sys

from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.features.reviewagent import (
    REVIEW_AGENT_TIMEOUT,
    _review_invoke_agent,
)
from agentware_lib.features.reviewoutput import (
    _review_extract_json,
    _review_one_line,
)
from agentware_lib.ops.reviewconfig import (
    REVIEW_DEFAULT_MODEL,
    REVIEW_FIXER_PERSONA,
    _review_emit_pinning_warnings,
    _review_resolve_config,
)
from agentware_lib.ops.reviewaudit import _review_resolve_diff
from agentware_lib.ops.reviewdiag import _review_emit_soft_fail_diagnostic
from agentware_lib.ops.reviewprompts import _review_build_fixer_prompt
from agentware_lib.ops.reviewschema import (
    REVIEW_FIX_STATUSES,
    _review_is_blocking,
    _review_normalize_finding,
    _review_normalize_fix,
)


def _review_test_module(test_ref):
    """Normalize a regression-test reference to a `python3 -m unittest` module
    target: strip a `::test_name` selector and convert a `tests/test_x.py` path to
    the dotted `tests.test_x` form. Returns '' for junk."""
    if not test_ref or not isinstance(test_ref, str):
        return ""
    ref = test_ref.strip().split("::", 1)[0].strip()
    if not ref:
        return ""
    if ref.endswith(".py"):
        ref = ref[:-3]
    ref = ref.replace(os.sep, "/").strip("/").replace("/", ".")
    return ref


def _review_subprocess_rc(cmd, cwd=None, timeout=None):
    """Run a gate-chain subprocess and return its return code. A spawn failure or
    timeout returns nonzero so a broken/absent gate can NEVER silently pass."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd or REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout or REVIEW_AGENT_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        return 1
    return proc.returncode


def _review_run_gate_chain(regression_tests=None, cwd=None):
    """Re-run the self-extension gate chain after a remediation: the affected/
    regression tests, then `steering lint`, then `gate release`. Returns
    {ok: bool, steps: [{name, ok, code}]} where ok is True iff EVERY step passed.
    THE seam hermetic tests monkeypatch — no test spawns the real gates. Bounded
    and deterministic; a spawn failure is a FAILED step (never a silent pass)."""
    cwd = cwd or REPO_ROOT
    cli = os.path.join(cwd, "scripts", "agentware")
    steps = []
    modules = sorted({m for m in (_review_test_module(t)
                                  for t in (regression_tests or [])) if m})
    if modules:
        code = _review_subprocess_rc(
            [sys.executable, "-m", "unittest", *modules], cwd)
        steps.append({"name": "tests", "ok": code == 0, "code": code})
    code = _review_subprocess_rc([cli, "steering", "lint"], cwd)
    steps.append({"name": "steering-lint", "ok": code == 0, "code": code})
    code = _review_subprocess_rc([cli, "gate", "release"], cwd)
    steps.append({"name": "gate-release", "ok": code == 0, "code": code})
    return {"ok": all(s["ok"] for s in steps), "steps": steps}


def _review_run_remediation(findings, cfg, diff_text="", criteria_text="",
                            diff_range=None, run_gate_chain=True):
    """Run ONE remediation pass over the CONFIRMED-blocking findings and return a
    structured result:
      {model, effort, requested:int,
       fixes:[{index, status, regression_test, notes, finding_summary, severity}],
       all_fixed:bool, gate_chain:{ok, steps}|None, new_diff_range}
    Only findings that are _review_is_blocking (verdict==confirmed AND high/med)
    are remediated. A confirmed finding whose fix names no regression test is
    REFUSED (unfixable) — never auto-applied unverified. The gate chain re-runs
    ONLY when every confirmed finding was fixed; all_fixed is True iff every
    confirmed finding is 'fixed' AND (when run) the gate chain is green. Pure
    orchestration — the fixer + gate-chain seams are monkeypatched in tests."""
    confirmed = [f for f in (findings or []) if _review_is_blocking(f)]
    if not confirmed:
        return {"model": cfg["model"], "effort": cfg["effort"], "requested": 0,
                "fixes": [], "all_fixed": True, "gate_chain": None,
                "new_diff_range": diff_range}
    prompt = _review_build_fixer_prompt(confirmed, diff_text, criteria_text, cfg)
    # retries=0: the fixer is the one MUTATING caller of the seam (persona
    # agentware-execution, edits package files). A transiently-failed or
    # timeout-killed fixer may have half-applied its edits — auto-respawning it
    # would replay non-idempotent side effects over the already-mutated tree
    # (and hand an injected agent free re-executions). Exactly ONE mutation
    # attempt, as before the resilience feature; a failure degrades to '' and
    # the gate reports the fixer did not run.
    out = _review_invoke_agent("fixer", REVIEW_FIXER_PERSONA, prompt, cfg,
                               retries=0)
    obj = _review_extract_json(out) or {}
    raw_fixes = obj.get("fixes")
    raw_fixes = raw_fixes if isinstance(raw_fixes, list) else []
    by_index = {}
    for rf in raw_fixes:
        if isinstance(rf, dict) and rf.get("index") is not None:
            try:
                by_index[int(rf["index"])] = rf
            except (TypeError, ValueError):
                pass
    fixes = []
    for i, finding in enumerate(confirmed):
        raw = by_index.get(i)
        if raw is None and i < len(raw_fixes) and isinstance(raw_fixes[i], dict) \
                and raw_fixes[i].get("index") is None:
            raw = raw_fixes[i]  # positional fallback when index omitted
        fix = _review_normalize_fix(raw, index=i)
        fix["finding_summary"] = finding.get("summary", "")
        fix["severity"] = finding.get("severity", "")
        fixes.append(fix)
    all_applied = bool(fixes) and all(f["status"] == "fixed" for f in fixes)
    gate = None
    if all_applied and run_gate_chain:
        reg_tests = [f["regression_test"] for f in fixes if f["regression_test"]]
        gate = _review_run_gate_chain(reg_tests)
    all_fixed = bool(all_applied and (gate is None or gate.get("ok")))
    return {"model": cfg["model"], "effort": cfg["effort"],
            "requested": len(confirmed), "fixes": fixes, "all_fixed": all_fixed,
            "gate_chain": gate, "new_diff_range": diff_range}


def _review_load_findings(args):
    """Load the confirmed findings for remediation from --findings-file (the
    `review --adversarial` JSON output, or a bare findings array) or stdin.
    Tolerant: returns [] on junk rather than crashing (novel-input hardening)."""
    path = getattr(args, "findings_file", None)
    raw = ""
    if path and path != "-":
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except OSError as exc:
            sys.stderr.write("remediate: cannot read --findings-file: %s\n" % exc)
            return []
    else:
        try:
            if not sys.stdin.isatty():
                raw = sys.stdin.read()
        except (OSError, ValueError):
            raw = ""
    if not raw.strip():
        return []
    # Strict parse first so a BARE findings array is not mistaken for the first
    # object nested inside it (a bare `[{...}]` starts with a `{` inside element 0).
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        items = parsed.get("findings")
        return items if isinstance(items, list) else []
    # Prose/fence-wrapped: fall back to the tolerant first-object extractor.
    obj = _review_extract_json(raw)
    if isinstance(obj, dict):
        items = obj.get("findings")
        return items if isinstance(items, list) else []
    return []


def _review_print_remediation(result):
    """Human-readable summary of a remediation pass (--format text)."""
    sys.stdout.write(
        "remediate: model=%s effort=%s confirmed=%d all_fixed=%s\n" % (
            result["model"], result["effort"], result["requested"],
            result["all_fixed"]))
    for f in result["fixes"]:
        sys.stdout.write("  #%s [%s] %s  reg=%s\n" % (
            f.get("index"), f.get("status"), f.get("finding_summary", ""),
            f.get("regression_test") or "-"))
    g = result.get("gate_chain")
    if g:
        sys.stdout.write("  gate-chain: ok=%s (%s)\n" % (
            g.get("ok"), ", ".join(
                "%s=%s" % (s["name"], s["ok"]) for s in g.get("steps", []))))


def cmd_remediate(args):
    """`remediate`: auto-fix the CONFIRMED-blocking findings from an audit round,
    each WITH a regression test, then re-run the gate chain. RETURNS 0 iff every
    confirmed finding was fixed-and-verified; nonzero otherwise (a survivor is
    escalated by the task-5 loop). This command APPLIES + gate-checks; the fresh
    RE-AUDIT of the fix is task 5's job."""
    cfg = _review_resolve_config()
    _review_emit_pinning_warnings(cfg)
    findings = _review_load_findings(args)
    diff_text = ""
    diff_file = getattr(args, "diff_file", None)
    if diff_file:
        # errors='replace': same crash class as cmd_review — untrusted diff
        # bytes must never crash the remediation pass with a strict decode.
        try:
            with open(diff_file, encoding="utf-8", errors="replace") as f:
                diff_text = f.read()
        except OSError as exc:
            sys.stderr.write("remediate: cannot read --diff-file: %s\n" % exc)
    elif getattr(args, "diff_range", None):
        # None = git failed to resolve the range. Remediate is NOT the gate
        # (cmd_review fails closed on this; the fixer merely loses diff
        # context while the findings still carry the defect details), so it
        # degrades to '' — loudly, never silently.
        diff_text = _review_resolve_diff(args.diff_range)
        if diff_text is None:
            sys.stderr.write(
                "remediate: cannot resolve --diff-range %r (git failed) — "
                "proceeding with an empty diff context\n" % args.diff_range)
            diff_text = ""
    criteria_text = ""
    acc_file = getattr(args, "acceptance_file", None)
    if acc_file:
        try:
            with open(acc_file, encoding="utf-8", errors="replace") as f:
                criteria_text = f.read()
        except OSError as exc:
            sys.stderr.write("remediate: cannot read --acceptance-file: %s\n" % exc)
    result = _review_run_remediation(
        findings, cfg, diff_text, criteria_text,
        diff_range=getattr(args, "diff_range", None),
        run_gate_chain=not getattr(args, "no_gate_chain", False))
    if getattr(args, "format", "json") == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _review_print_remediation(result)
    return 0 if result["all_fixed"] else 1


def register_remediate(sub, fmt_parent):
    # remediate --------------------------------------------------------------
    p_remediate = sub.add_parser(
        "remediate", parents=[fmt_parent],
        help="auto-fix confirmed adversarial-review findings (Opus/max), each "
             "with a regression test, then re-run the gate chain")
    p_remediate.add_argument(
        "--findings-file", dest="findings_file",
        help="JSON of confirmed findings (the `review --adversarial` output, or a "
             "bare findings array); '-' or omitted reads stdin")
    p_remediate.add_argument(
        "--diff-range", dest="diff_range",
        help="git range the fix is applied against (echoed as new_diff_range for "
             "the task-5 re-audit)")
    p_remediate.add_argument(
        "--diff-file", dest="diff_file",
        help="read the unified diff from a file instead of git")
    p_remediate.add_argument(
        "--acceptance-file", dest="acceptance_file",
        help="path to the feature plan.md acceptance criteria the fixer honors")
    p_remediate.add_argument(
        "--no-gate-chain", dest="no_gate_chain", action="store_true",
        help="skip the post-fix gate-chain re-run (the task-5 re-audit still "
             "runs); used by hermetic tests")
    p_remediate.set_defaults(func=cmd_remediate)


_EXPORTS = [
    "_review_test_module",
    "_review_subprocess_rc",
    "_review_run_gate_chain",
    "_review_run_remediation",
    "_review_load_findings",
    "_review_print_remediation",
    "cmd_remediate",
]

__all__ = list(_EXPORTS)
