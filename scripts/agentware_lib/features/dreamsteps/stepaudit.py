"""agentware_lib.features.dreamsteps.stepaudit -- dream step "c", audit: the cycle's mid-flight health check (layer: features).

Owns: dream step "c" (_dream_step_audit) and the two named readers it decomposes
into -- the captured-JSON parser (_dream_audit_read_capture) and the unittest
check's id/count extractor (_dream_audit_unittest_counts). It mirrors
`audit --with-tests`, skips the suite-shell under the nested-unittest guard so a
cycle can never recurse, and excludes the circular dream_health check from the
cycle's own pass/fail while still surfacing it.
Does NOT own: cmd_audit or any individual audit check -> ops.audit; the capture
harness -> dreamsteps.dreamcapture; the triage-artifact writer that consumes the
private `_failure_capture` key -> features.dreamjournal (via the cmd_dream
driver, which pops it before journaling); the ordered registry that binds this
handler to id "c" -> dreamsteps.registry. See catalog/modules.json.
Layer deps: ops (audit) DOWNWARD -- plus ONE dreamsteps SIBLING (dreamcapture, a
stdlib-only leaf), so the package graph stays acyclic; stdlib argparse/json/os at
module scope.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: non-JSON captured output leaves the defaults rather than raising, so
an unattended cron never crashes (INV-2); every id list is sorted, so the step
payload is byte-stable for unchanged inputs (INV-1).
"""

import argparse
import json
import os

import agentware_lib.ops.audit as _audit

from .dreamcapture import _dream_change_gate_skip, _dream_capture


def _dream_audit_unittest_counts(payload):
    """(tests_ran, tests_failed, failed_tests) from the audit payload.

    Pulls the failing test ids + counts threaded into the unittest check by
    `_audit_tests_check()` (Task 1) so step c can answer "which tests failed?"
    with zero re-runs. Sorted for a byte-stable payload (INV-1).
    """
    ut = next((c for c in payload.get("checks", [])
               if c.get("name") == "unittest"), None)
    if ut is None:
        return None, 0, []
    failed_tests = sorted(ut.get("failed_tests", []))
    tests_ran = None
    for d in ut.get("details", []):
        if d.startswith("Ran ") and d.split()[1:2]:
            try:
                tests_ran = int(d.split()[1])
            except ValueError:
                pass
            break
    return tests_ran, len(failed_tests), failed_tests


def _dream_audit_read_capture(out):
    """(failed_checks, tests_ran, tests_failed, failed_tests) parsed from step c's
    captured `audit --format json` output. Output that is not JSON leaves the
    defaults rather than raising into an unattended cycle."""
    failed = []
    tests_ran, tests_failed, failed_tests = None, 0, []
    try:
        payload = json.loads(out)
        failed = sorted(c["name"] for c in payload.get("checks", [])
                        if not c.get("ok"))
        tests_ran, tests_failed, failed_tests = _dream_audit_unittest_counts(payload)
    except ValueError:
        pass
    return failed, tests_ran, tests_failed, failed_tests


def _dream_step_audit(kdir, dry_run):
    """Step c — health check. Mirrors `audit --with-tests`; the suite-shell is
    skipped under the nested-unittest guard so the cycle never recurses."""
    with_tests = os.environ.get("AGENTWARE_NESTED_UNITTEST") != "1"
    if dry_run:
        return {"status": "planned",
                "would": "audit%s" % (" --with-tests" if with_tests else "")}
    # Change-gate (Task 8): SKIP the ~150s audit on a CLEAN tree unchanged since
    # the last cycle; a DIRTY tree or any doubt RUNS (fail-open). Computed AFTER
    # step a so its fp agrees with the metric-recorded value.
    gate = _dream_change_gate_skip(kdir)
    if gate:
        return {"status": "skipped", "reason": gate}
    ns = argparse.Namespace(format="json", with_tests=with_tests, stale=False,
                            max_age_days=120)
    rc, out = _dream_capture(_audit.cmd_audit, ns)
    failed, tests_ran, tests_failed, failed_tests = _dream_audit_read_capture(out)
    # The dream is auditing ITSELF mid-cycle: dream_health asks "has a dream run
    # recently?", which is circular DURING a cycle — THIS cycle's metric isn't
    # emitted until after all steps finish, so on the first-ever (or post-stale)
    # run dream_health is necessarily red here. Exclude it from the cycle's own
    # pass/fail (it self-heals on the next run); still surface it for transparency.
    material = [c for c in failed if c != "dream_health"]
    ok = not material
    result = {"status": "ok" if ok else "fail", "audit_ok": ok,
              "with_tests": with_tests, "failed_checks": ",".join(material) or "-",
              "ignored_checks": "dream_health" if "dream_health" in failed else "-",
              "tests_ran": tests_ran if tests_ran is not None else "-",
              "tests_failed": tests_failed,
              "failed_tests": ",".join(failed_tests) or "-"}
    # On a MATERIAL audit failure, hand the orchestrator the full captured audit
    # output so it persists a per-cycle triage artifact (best-effort; the private
    # underscore key is popped before the step dict is journaled/emitted).
    if not ok:
        result["_failure_capture"] = out
    return result


_EXPORTS = [
    "_dream_audit_unittest_counts",
    "_dream_audit_read_capture",
    "_dream_step_audit",
]
