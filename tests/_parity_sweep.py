#!/usr/bin/env python3
"""THE PARITY MUTATION SWEEP -- run every curated row's proof and demand a red.

Feature 260813-modular-core-adoption, task 79.

    python3 tests/_parity_sweep.py --check          # static: coverage + schema
    python3 tests/_parity_sweep.py --list           # the probe table
    python3 tests/_parity_sweep.py --row <id>       # one row, live
    python3 tests/_parity_sweep.py --all            # THE SWEEP
    python3 tests/_parity_sweep.py --all --jobs 6 --json out.json

WHAT A ROW CLAIMS AND WHAT THIS PROVES
--------------------------------------
A curated parity row says: *this capability survived the merge, and here is the
TEST that proves it, and here is the MUTATION that must redden that test.* The
first two halves were already gated -- the test must resolve on disk, or the row
sits in the shrink-only pending list. The third half was not gated at all: the
mutation was a sentence, checked only for being longer than twenty characters.

A sentence is not a proof. A test that passes tells you nothing about whether it
would fail; the whole reason this repository carries a mutation canary, a
modularity negative test and a five-defect bootstrap probe is that "the gate ran"
and "the gate could have failed" are different claims and only the second is
worth anything. This sweep closes the last one of those seams.

THE FOUR VERDICTS THAT ARE NOT A PASS
-------------------------------------
  BASELINE-RED   the named test was already failing, so its red proves nothing
                 about the mutation. Reported before the mutation is applied.
  STALE-PROBE    the mutation did not change the tree (a `find` string that has
                 moved, a path already gone). The single most dangerous outcome,
                 because a no-op mutation looks exactly like a missing
                 capability -- so it is named as its own verdict, never folded
                 into "did not redden".
  NOT-REDDENED   the mutation applied and the test still passed. THIS is the
                 finding the sweep exists for: the row's proof is vacuous and
                 the capability is unguarded.
  LOAD-ERROR     the only red is an import/collection failure. The module never
                 ran, so no assertion was exercised; a probe that merely breaks
                 an import proves the interpreter works, not that the capability
                 is guarded. There is deliberately no flag to accept one.

WHY A SANDBOX CLONE AND NOT THE WORKING TREE
--------------------------------------------
Every mutation is destructive by construction. `git clone --shared` of this
repository takes ~0.2s, gives the sandbox its own `.git` (several named tests
read tags -- the ROADMAP byte-identity check diffs against `aw-pre-merge`), and
shares the object store read-only. The working tree is never written to; the
sweep refuses to run against a root that is not a sandbox it made. Between rows
the sandbox is restored with `checkout -- .` + `clean -fd`, which is exact
because the clone starts clean.

WHY THE CHILD REPORTS OVER JSON AND NOT STDOUT
-----------------------------------------------
Same reason `tests/_suite_gate.py` does: a named test may itself run sub-suites
that print `FAIL:` headers (`tests.gates.test_modularity_negative` plants
deliberate reds), so scraping the parent's stdout misattributes a child's
intentional failure to the module under measurement. The runner reports through
`TestResult`/`test.id()` on a private path, and a child that dies before
reporting is a FAILURE, never an empty result that reads as green.

Stdlib only. No network. Never touches the operator's knowledge base.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.realpath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from _parity_mutations import apply_mutation, spec_errors  # noqa: E402

PASS = "PASS"
BASELINE_RED = "BASELINE-RED"
STALE_PROBE = "STALE-PROBE"
NOT_REDDENED = "NOT-REDDENED"
LOAD_ERROR = "LOAD-ERROR"
CRASHED = "CRASHED"

#: Anything but this is a sweep failure.
VERDICTS = (PASS, BASELINE_RED, STALE_PROBE, NOT_REDDENED, LOAD_ERROR, CRASHED)

#: unittest reports an unimportable module as a synthetic test with this in its
#: id. Such a "failure" exercised no assertion.
LOADER_MARKER = "_FailedTest"

#: A single module's run may not exceed this. A mutation that makes a test HANG
#: is not a red; it is a wedged sweep, and this repository has lost four
#: autonomous iterations to runs that hung rather than failed (tests/README.md).
MODULE_TIMEOUT = 900


# --------------------------------------------------------------------------
# the child: run ONE module and report over a private JSON channel
# --------------------------------------------------------------------------

def run_module_in_process(dotted, emit_path):
    """CHILD ENTRY POINT. Load one module, run it, write the result as JSON."""
    import unittest as _ut
    # `python3 -m unittest` puts the CWD on sys.path; running a script does not,
    # and every test module here imports `tests.<tier>...`. Without this the
    # whole sweep reports BASELINE-RED with a ModuleNotFoundError -- a red that
    # says nothing about the tree under measurement.
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    loader = _ut.TestLoader()
    try:
        suite = loader.loadTestsFromName(dotted)
    except Exception as exc:                                  # noqa: BLE001
        payload = {"ran": 0, "failures": [], "skipped": 0,
                   "errors": [["ERROR", "%s.%s" % (dotted, LOADER_MARKER),
                               "%s: %s" % (type(exc).__name__, exc)]]}
        with open(emit_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return 1
    runner = _ut.TextTestRunner(stream=sys.stderr, verbosity=1)
    result = runner.run(suite)
    payload = {
        "ran": result.testsRun,
        "skipped": len(result.skipped),
        "failures": [["FAIL", t.id(), _head(tb)] for t, tb in result.failures],
        "errors": [["ERROR", t.id(), _head(tb)] for t, tb in result.errors],
    }
    with open(emit_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return 0 if result.wasSuccessful() else 1


def _head(traceback_text, limit=240):
    line = (traceback_text or "").strip().splitlines()
    return (line[-1] if line else "")[:limit]


def run_module(sandbox, dotted, timeout=MODULE_TIMEOUT):
    """Run one module inside `sandbox`. Returns a result dict, never raises."""
    env = dict(os.environ)
    # Two verbs measure a pass rate by shelling `unittest discover -s tests`
    # over the whole tree -- the collection that intermittently hangs.
    env["AGENTWARE_NESTED_UNITTEST"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    handle, emit = tempfile.mkstemp(prefix="agentware-sweep-", suffix=".json")
    os.close(handle)
    try:
        try:
            proc = subprocess.run(
                [sys.executable, os.path.join("tests", "_parity_sweep.py"),
                 "--run-module", dotted, "--emit", emit],
                cwd=sandbox, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, timeout=timeout)
            rc, out = proc.returncode, proc.stdout or ""
        except subprocess.TimeoutExpired:
            # A hang is a FAILURE, never a slow pass (tests/README.md).
            return {"ran": 0, "skipped": 0, "crashed": True, "failures": [],
                    "errors": [["ERROR", "<%s timed out after %ds>"
                                % (dotted, timeout), ""]], "output": ""}
        try:
            with open(emit, encoding="utf-8") as fh:
                result = json.load(fh)
        except (OSError, ValueError):
            return {"ran": 0, "skipped": 0, "crashed": True, "failures": [],
                    "errors": [["ERROR", "<%s did not report: rc %d>"
                                % (dotted, rc), out[-400:]]], "output": out}
    finally:
        if os.path.exists(emit):
            os.unlink(emit)
    result["output"] = out
    result["crashed"] = False
    return result


def reds(result):
    return [tuple(x) for x in result.get("failures", [])] \
        + [tuple(x) for x in result.get("errors", [])]


def only_load_errors(result):
    rows = reds(result)
    return bool(rows) and all(LOADER_MARKER in nid for _k, nid, *_r in rows)


# --------------------------------------------------------------------------
# the sandbox
# --------------------------------------------------------------------------

def make_sandbox(dst, root=REPO_ROOT):
    """A throwaway clone of `root`, carrying its tags and its WORKING TREE.

    `git clone` copies COMMITTED state, so a sandbox built from the clone alone
    measures the previous commit -- silently, and identically to a green run.
    Task 78 hit the untracked half of this exact seam (a new directory absent
    from a fresh clone). Both halves are carried here: tracked modifications via
    `diff HEAD --binary`, and untracked-but-not-ignored files by copy, so the
    sweep measures what the author is about to commit.
    """
    subprocess.run(["git", "clone", "--shared", "--quiet", root, dst],
                   check=True, capture_output=True, text=True)
    diff = subprocess.run(["git", "-C", root, "diff", "HEAD", "--binary"],
                          capture_output=True, text=True, check=True).stdout
    if diff.strip():
        proc = subprocess.run(["git", "-C", dst, "apply", "--whitespace=nowarn", "-"],
                              input=diff, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit("sandbox could not carry the working tree: %s"
                             % (proc.stderr.strip(),))
    _carry_untracked(root, dst)
    # The sandbox's own pristine state is what `restore_sandbox` returns to, so
    # the carried files must be part of it rather than swept away by `clean`.
    subprocess.run(["git", "-C", dst, "add", "--all"],
                   check=True, capture_output=True, text=True)
    return dst


def _carry_untracked(root, dst):
    listing = subprocess.run(
        ["git", "-C", root, "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True, text=True, check=True).stdout
    for rel in [p for p in listing.split("\0") if p]:
        src = os.path.join(root, rel)
        if not os.path.isfile(src) or os.path.islink(src):
            continue
        target = os.path.join(dst, rel)
        parent = os.path.dirname(target)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        shutil.copy2(src, target)


def restore_sandbox(sandbox):
    """Back to pristine. Exact, because the clone started clean."""
    subprocess.run(["git", "-C", sandbox, "checkout", "--quiet", "--", "."],
                   check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", sandbox, "clean", "--quiet", "-fd"],
                   check=True, capture_output=True, text=True)


# --------------------------------------------------------------------------
# one row
# --------------------------------------------------------------------------

def sweep_row(sandbox, row_id, dotted, spec, baselines=None, lock=None):
    """Baseline, mutate, re-run. Returns a verdict dict."""
    started = time.time()
    out = {"row": row_id, "test": dotted, "kind": spec.get("kind")}
    # Refuse a circular or already-stale probe BEFORE spending a baseline run on
    # it, and refuse it for an ad-hoc `--spec` too, not only for a table row.
    upfront = spec_errors(spec, row_id)
    if not upfront:
        upfront = _probe_target_errors(sandbox, row_id, spec, dotted)
    if upfront:
        out.update(verdict=STALE_PROBE, detail="; ".join(upfront),
                   seconds=round(time.time() - started, 2))
        return out
    base = _baseline(sandbox, dotted, baselines, lock)
    if base.get("crashed") or reds(base):
        out.update(verdict=BASELINE_RED, detail=_why(base),
                   seconds=round(time.time() - started, 2))
        return out
    if not base.get("ran"):
        out.update(verdict=BASELINE_RED,
                   detail="the named test ran ZERO tests: there is nothing to redden",
                   seconds=round(time.time() - started, 2))
        return out
    changed, detail = apply_mutation(sandbox, spec, row_id)
    if not changed:
        restore_sandbox(sandbox)
        out.update(verdict=STALE_PROBE, detail=detail,
                   seconds=round(time.time() - started, 2))
        return out
    mutated = run_module(sandbox, dotted)
    restore_sandbox(sandbox)
    out["applied"] = detail
    out["baseline_ran"] = base.get("ran")
    out["seconds"] = round(time.time() - started, 2)
    if mutated.get("crashed"):
        out.update(verdict=CRASHED, detail=_why(mutated))
    elif not reds(mutated):
        out.update(verdict=NOT_REDDENED,
                   detail="%d test(s) ran and ALL passed after %s"
                          % (mutated.get("ran", 0), detail))
    elif only_load_errors(mutated):
        out.update(verdict=LOAD_ERROR, detail=_why(mutated))
    else:
        rows = [r for r in reds(mutated) if LOADER_MARKER not in r[1]]
        out.update(verdict=PASS, reddened=[r[1] for r in rows[:6]],
                   detail="%d of %d test(s) reddened: %s"
                          % (len(rows), mutated.get("ran", 0), _why(mutated)))
    return out


def _baseline(sandbox, dotted, baselines, lock):
    if baselines is None:
        return run_module(sandbox, dotted)
    with (lock or threading.Lock()):
        cached = baselines.get(dotted)
    if cached is None:
        cached = run_module(sandbox, dotted)
        with (lock or threading.Lock()):
            baselines[dotted] = cached
    return cached


def _why(result, limit=3):
    rows = reds(result)
    if not rows:
        return "%d test(s), all passed" % result.get("ran", 0)
    return "; ".join("%s %s %s" % (k, nid, (extra[0] if extra else ""))
                     for k, nid, *extra in rows[:limit]).strip()


# --------------------------------------------------------------------------
# the table + the whole sweep
# --------------------------------------------------------------------------

def curated_rows():
    """(row_id, dotted test, probe spec) for every curated row, in table order."""
    from _parity_curated import CURATED_ROWS
    rows = []
    for row in CURATED_ROWS:
        if row.get("verification") != "curated":
            continue
        ev = row.get("evidence", {})
        rows.append((row["id"], ev.get("test"), ev.get("probe")))
    return rows


def check_probes(root=REPO_ROOT):
    """STATIC gate: every curated row carries a valid, resolvable probe."""
    bad = []
    for row_id, dotted, spec in curated_rows():
        if not spec:
            bad.append("row %s carries no runnable mutation probe: its proof is "
                       "prose" % row_id)
            continue
        errors = spec_errors(spec, row_id)
        bad.extend(errors)
        if not errors:
            bad.extend(_probe_target_errors(root, row_id, spec, dotted))
        if not dotted:
            bad.append("row %s carries a probe but names no test" % row_id)
        elif not os.path.exists(os.path.join(root, *dotted.split(".")) + ".py"):
            bad.append("row %s names test %s, which does not resolve on disk"
                       % (row_id, dotted))
    return bad


def _probe_target_errors(root, row_id, spec, dotted=None):
    """A probe that names a path the tree no longer has is stale BEFORE it runs.

    This is the cheap half of the STALE-PROBE verdict: `--check` costs
    milliseconds and catches a moved file without cloning anything, so a rename
    in the package is reported by the fast gate rather than only by the sweep.

    It also refuses the CIRCULAR probe -- a mutation whose target is the named
    test's own file. Editing an assertion until it fails proves the assertion
    exists, which is all `hasattr` proves and exactly the failure the mutation
    canary was built to reject. The mutation must break the CAPABILITY.
    """
    bad = []
    kind = spec["kind"]
    if kind != "write" and not os.path.exists(os.path.join(root, spec["path"])):
        bad.append("row %s: mutation %s names path %r, which does not exist -- "
                   "the probe is STALE" % (row_id, kind, spec["path"]))
    if kind == "rename" and os.path.exists(os.path.join(root, spec["to"])):
        bad.append("row %s: rename destination %r already exists, so the "
                   "mutation could never apply" % (row_id, spec["to"]))
    if dotted:
        own = os.path.join(*dotted.split(".")) + ".py"
        if spec.get("path") == own or spec.get("to") == own:
            bad.append("row %s: the mutation targets %s, which IS the named "
                       "test. A test edited until it fails proves only that it "
                       "has assertions; mutate the capability instead" % (row_id, own))
    return bad


def sweep(selected, jobs=1, root=REPO_ROOT, progress=None):
    """Run the sweep over `selected` [(row_id, dotted, spec), ...]."""
    baselines, lock, results = {}, threading.Lock(), []
    workdir = tempfile.mkdtemp(prefix="agentware-sweep-sbx-")
    try:
        pool = [make_sandbox(os.path.join(workdir, "sbx%d" % i), root)
                for i in range(max(1, jobs))]
        if jobs <= 1:
            for item in selected:
                results.append(_one(pool[0], item, baselines, lock, progress))
        else:
            results = _parallel(pool, selected, baselines, lock, progress)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    order = {rid: i for i, (rid, _t, _s) in enumerate(selected)}
    results.sort(key=lambda r: order.get(r["row"], 0))
    return results


def _one(sandbox, item, baselines, lock, progress):
    row_id, dotted, spec = item
    if not spec:
        return {"row": row_id, "test": dotted, "verdict": STALE_PROBE,
                "detail": "no runnable mutation probe declared"}
    out = sweep_row(sandbox, row_id, dotted, spec, baselines, lock)
    if progress:
        progress(out)
    return out


def _parallel(pool, selected, baselines, lock, progress):
    from concurrent.futures import ThreadPoolExecutor
    free = list(pool)
    slot = threading.Lock()

    def task(item):
        with slot:
            sandbox = free.pop()
        try:
            return _one(sandbox, item, baselines, lock, progress)
        finally:
            with slot:
                free.append(sandbox)

    with ThreadPoolExecutor(max_workers=len(pool)) as ex:
        return list(ex.map(task, selected))


def summarise(results):
    counts = {}
    for row in results:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    return counts


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="parity mutation sweep")
    ap.add_argument("--check", action="store_true",
                    help="static: every curated row has a valid probe")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--row", action="append", default=[], metavar="ID")
    ap.add_argument("--spec", metavar="JSON",
                    help="ad-hoc probe: {\"row\":..,\"test\":..,\"mutation\":{..}}")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--json", metavar="PATH")
    ap.add_argument("--root", default=REPO_ROOT)
    ap.add_argument("--run-module", metavar="DOTTED", help=argparse.SUPPRESS)
    ap.add_argument("--emit", metavar="PATH", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.run_module:
        return run_module_in_process(args.run_module, args.emit)
    if args.list:
        for row_id, dotted, spec in curated_rows():
            print("%-46s %-52s %s" % (row_id, dotted,
                                      (spec or {}).get("kind", "<none>")))
        return 0
    if args.check:
        bad = check_probes(args.root)
        if bad:
            print("[FAIL] parity mutation probes: %d violation(s)" % len(bad))
            for line in bad:
                print("  - %s" % line)
            return 1
        print("[OK] parity mutation probes: %d curated rows, all runnable"
              % len(curated_rows()))
        return 0
    selected = _selection(args)
    if selected is None:
        ap.print_help()
        return 2
    return _run(selected, args)


def _selection(args):
    if args.spec:
        spec = json.loads(args.spec)
        return [(spec.get("row", "<ad-hoc>"), spec["test"], spec["mutation"])]
    table = curated_rows()
    if args.all:
        return table
    if args.row:
        by_id = {rid: (rid, t, s) for rid, t, s in table}
        missing = [r for r in args.row if r not in by_id]
        if missing:
            raise SystemExit("no such curated row: %s" % ", ".join(missing))
        return [by_id[r] for r in args.row]
    return None


def _run(selected, args):
    started = time.time()

    def progress(row):
        print("  %-13s %-46s %5.1fs  %s"
              % (row["verdict"], row["row"], row.get("seconds", 0.0),
                 row.get("detail", "")[:110]), flush=True)

    print("parity mutation sweep: %d row(s), %d sandbox(es)"
          % (len(selected), max(1, args.jobs)), flush=True)
    results = sweep(selected, args.jobs, args.root, progress)
    counts = summarise(results)
    elapsed = time.time() - started
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"results": results, "counts": counts,
                       "seconds": round(elapsed, 1)}, fh, indent=2)
    failed = [r for r in results if r["verdict"] != PASS]
    print("\n%d/%d rows PASSED in %.1fs  %s"
          % (counts.get(PASS, 0), len(results), elapsed,
             json.dumps(counts, sort_keys=True)))
    if failed:
        print("[FAIL] parity mutation sweep: %d row(s) did not prove their "
              "capability" % len(failed))
        for row in failed:
            print("  - %-13s %s: %s" % (row["verdict"], row["row"],
                                        row.get("detail", "")))
        return 1
    print("[OK] parity mutation sweep: every curated row's mutation reddens its "
          "named test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
