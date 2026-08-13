#!/usr/bin/env python3
"""Run ONE test tier and gate it against the declared known-failure ledger.

Feature 260813-modular-core-adoption, task 78.

    python3 tests/_suite_gate.py --tier unit
    python3 tests/_suite_gate.py --tier gates --ledger <path>

WHY THIS EXISTS
---------------
`tests/baselines/README.md` states this repository's definition of green in bold:
**"Green means 'no worse than the recorded baseline', never 'all pass'."** The
plan's Constraints section says the same thing. A CI stage that shells
`python3 -m unittest discover -s tests/<tier> -t .` and reads its exit code
implements the OTHER definition, and the difference is not academic: the suite
carries one declared pre-existing failure (owned by plan task 89), so such a
stage is red on day one for something no contributor introduced. A CI that is
permanently red is one everybody learns to scroll past, which costs more than
having no CI at all — the first real regression arrives into a red build and
nobody notices.

So the tier runner subtracts the DECLARED red and nothing else, exactly as
`_ceiling_gate`, `_modularity_rules`, `_test_ownership` and `_portability_debt`
subtract their declared residuals. The ledger is
`tests/fixtures/suite-known-failures.json` and it is a RATCHET, not an
allowlist — it bites in both directions:

  * a failure with no row is a gate failure. A new break cannot hide behind an
    old one, which is the property that makes subtraction safe at all.
  * a declared failure that has started PASSING is ALSO a gate failure, naming
    the row to delete. Without this half, a row outlives its defect and the
    ledger silently becomes a list of tests nobody notices are excused.

There is no flag to skip the ledger and no flag to widen it at runtime.

WHY IT DOES NOT PARSE THE TIER'S STDOUT
----------------------------------------
The obvious implementation shells `unittest discover` and scrapes its
`FAIL:`/`ERROR:` header lines. That was the first cut here and it is WRONG, which
this tree proves within one run: `tests/gates/test_modularity_negative.py` plants
violations in a scratch tree and runs sub-suites over it, and their output —
including their own `FAIL:` headers and a `FAILED (failures=1)` summary — lands in
the parent's stdout. A scraper attributes a CHILD's deliberate, expected red to
the PARENT tier and reports a phantom undeclared failure. A gate that
misattributes is worse than one that under-reports, because the name it prints
sends the reader somewhere real and wrong.

So the tier still runs in a SUBPROCESS — `discover` imports every module in the
tier and several mutate process-global state, so an in-process run would make the
result depend on which tier ran first — but the child reports through
`TestResult` and `test.id()`, written as JSON to a file descriptor of its own.
Nested output cannot reach that channel, and a node id from `test.id()` is exact
rather than reconstructed.

Read-only. No network. Standard library only.
"""

import argparse
import json
import os
import re
import tempfile
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
REPO_ROOT = os.path.dirname(HERE)

LEDGER_SCHEMA = "agentware/suite-known-failures@1"
DEFAULT_LEDGER = os.path.join(HERE, "fixtures", "suite-known-failures.json")

TIERS = ("unit", "gates", "integration", "e2e")


def load_ledger(path=None):
    """Read the declared known-failure ledger."""
    with open(path or DEFAULT_LEDGER, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("schema") != LEDGER_SCHEMA:
        raise ValueError("known-failure ledger schema mismatch: %r"
                         % data.get("schema"))
    return data


def schema_errors(data):
    """Structural problems in the ledger itself, as human-readable strings."""
    errors = []
    seen = set()
    for row in data.get("rows", []):
        for field in ("node_id", "tier", "owner", "reason"):
            if not row.get(field):
                errors.append("row %r has no %s" % (row.get("node_id"), field))
        node_id = row.get("node_id")
        if node_id in seen:
            errors.append("duplicate row for %s" % node_id)
        seen.add(node_id)
        if row.get("tier") not in TIERS:
            errors.append("row %s names tier %r, which is not one of %s"
                          % (node_id, row.get("tier"), ", ".join(TIERS)))
        # A row must name a TASK, not a mood. An excused failure whose owner is
        # "flaky" or "known" is one nobody is going to fix.
        if row.get("owner") and not re.match(r"^task \d+$", row["owner"]):
            errors.append("row %s has owner %r; a row must name the plan task "
                          "that removes it" % (node_id, row["owner"]))
        if row.get("reason") and len(row["reason"]) < 80:
            errors.append("row %s states no diagnosis — a subtracted failure "
                          "needs a root cause, or it is an allowlist entry"
                          % node_id)
    return errors


def run_tier_in_process(tier, emit_path):
    """CHILD ENTRY POINT. Run one tier here and write the result as JSON.

    Never called in the parent: this mutates the interpreter it runs in (every
    module in the tier gets imported). `emit_path` is a private channel — the
    tier's own stdout, and the stdout of any sub-suite it spawns, cannot reach
    it, which is the whole point.
    """
    import unittest as _ut
    loader = _ut.TestLoader()
    suite = loader.discover(start_dir=os.path.join("tests", tier),
                            top_level_dir=".")
    runner = _ut.TextTestRunner(stream=sys.stderr, verbosity=1)
    result = runner.run(suite)
    payload = {
        "ran": result.testsRun,
        "skipped": len(result.skipped),
        "failures": [("FAIL", test.id()) for test, _tb in result.failures],
        "errors": [("ERROR", test.id()) for test, _tb in result.errors],
    }
    with open(emit_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return 0 if result.wasSuccessful() else 1


def reconcile(failures, rows, tier):
    """Compare live failures to the declared rows for this tier.

    Returns (problems, clean). Two directions, both fatal:
    UNDECLARED — a failure with no row; STALE — a row whose test now passes.
    """
    declared = dict((r["node_id"], r) for r in rows if r.get("tier") == tier)
    live = set(node_id for _kind, node_id in failures)
    problems = []
    for kind, node_id in failures:
        if node_id not in declared:
            problems.append(("UNDECLARED", node_id,
                             "%s is not in the known-failure ledger" % kind))
    for node_id, row in sorted(declared.items()):
        if node_id not in live:
            problems.append(("STALE", node_id,
                             "declared as a known failure (%s) but it PASSES "
                             "now — delete the row" % row["owner"]))
    return problems, not problems


def run_tier(tier, root=None, env=None):
    """Run one tier in a subprocess. Returns (result dict, output, command).

    The equivalent hand-typed command is reported so the CI log still shows a
    line a human can copy; what is GATED on is the child's JSON, not its text.
    """
    root = root or REPO_ROOT
    child = dict(os.environ if env is None else env)
    # Two verbs measure a pass-rate by shelling `unittest discover -s tests`
    # over the WHOLE tree — the collection that intermittently hangs. Without
    # this the tier can re-enter it inside itself.
    child["AGENTWARE_NESTED_UNITTEST"] = "1"
    handle, emit = tempfile.mkstemp(prefix="agentware-suitegate-", suffix=".json")
    os.close(handle)
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join("tests", "_suite_gate.py"),
             "--run-tier", tier, "--emit", emit],
            cwd=root, env=child, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True)
        try:
            with open(emit, encoding="utf-8") as f:
                result = json.load(f)
        except (OSError, ValueError):
            # The child died before reporting — a crashed or killed tier must be
            # a FAILURE, never an empty result that reconciles to clean.
            result = {"ran": 0, "skipped": 0, "crashed": True,
                      "failures": [("ERROR", "<tier %s did not report: rc %d>"
                                    % (tier, proc.returncode))], "errors": []}
    finally:
        if os.path.exists(emit):
            os.unlink(emit)
    equivalent = "python3 -m unittest discover -s tests/%s -t ." % tier
    return result, proc.stdout or "", equivalent


def gate_tier(tier, ledger_path=None, root=None):
    """Run a tier and reconcile it. Returns (problems, ran, failures, cmd, out)."""
    data = load_ledger(ledger_path)
    errors = schema_errors(data)
    result, output, cmd = run_tier(tier, root)
    failures = [tuple(x) for x in result.get("failures", [])] \
        + [tuple(x) for x in result.get("errors", [])]
    problems, _clean = reconcile(failures, data.get("rows", []), tier)
    return ([("SCHEMA", "ledger", e) for e in errors] + problems,
            result.get("ran", 0), failures, cmd, output)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="tests/_suite_gate.py",
        description="Run one test tier and gate it against the declared "
                    "known-failure ledger (green = no worse than the baseline).")
    ap.add_argument("--tier", choices=TIERS)
    ap.add_argument("--ledger", default=None, help="ledger fixture to apply")
    ap.add_argument("--root", default=None, help="tree to run against")
    ap.add_argument("--show-output", action="store_true",
                    help="echo the tier's own output as well as the verdict")
    ap.add_argument("--run-tier", choices=TIERS, default=None,
                    help=argparse.SUPPRESS)   # child entry point; see run_tier
    ap.add_argument("--emit", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.run_tier:
        if not args.emit:
            ap.error("--run-tier requires --emit")
        return run_tier_in_process(args.run_tier, args.emit)
    if not args.tier:
        ap.error("--tier is required")

    problems, ran, failures, cmd, output = gate_tier(
        args.tier, args.ledger, args.root)
    if args.show_output or problems:
        sys.stderr.write(output)
    for code, location, detail in problems:
        sys.stderr.write("%s %s: %s\n" % (code, location, detail))
    if problems:
        sys.stderr.write("suite gate FAILED (%s): %d problem(s); ran %d\n"
                         % (args.tier, len(problems), ran))
        return 1
    subtracted = len(failures)
    sys.stdout.write(
        "suite gate OK (%s): %d test(s) ran, %d declared failure(s) subtracted"
        "%s\n  %s\n"
        % (args.tier, ran, subtracted,
           "" if not subtracted else
           " (%s)" % ", ".join(n.rsplit(".", 1)[-1] for _k, n in failures),
           cmd))
    return 0


if __name__ == "__main__":
    sys.exit(main())
