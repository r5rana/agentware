#!/usr/bin/env python3
"""Per-commit suite delta gate: a pass-count RISE must be explained.

Feature 260813-modular-core-adoption, task 28.

The recorded baselines answer "is the suite no worse than before". They cannot
answer the question this migration actually turns on: **did a test stop
running?** Deleting an assertion, an import that silently no-ops, a facade that
breaks the monkeypatch seam, a module that stops collecting half its classes --
none of those make anything red. Several make the numbers go UP, because a test
that no longer exercises the real code path is a test that no longer fails.

So this gate treats an UNEXPLAINED PASS-COUNT RISE as a defect. A module whose
pass count rises must have gained at least that many NEW node ids; a module
whose pass count falls must have lost at least that many. Counts and node ids
are captured in the SAME process pass, at the same commit, so the two halves
can never be compared across a skew.

What each capture holds, per tree:

  * one ``python3 -m unittest <dotted>`` subprocess per test module, with its
    parsed pass/fail/error/skip counts -- reusing ``_baseline_capture`` so the
    delta and the baseline can never disagree about what a module scored;
  * the collected unittest node ids, grouped by module, from
    ``_preserved_scan.collect_node_ids`` -- the same collector the preserved
    surface freeze is anchored to.

Usage:
    python3 tests/_suite_delta.py capture --root . --out tests/baselines/suite-delta-head.json
    python3 tests/_suite_delta.py check \\
        --before tests/baselines/suite-delta-prev.json \\
        --after  tests/baselines/suite-delta-head.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _baseline_capture import (  # noqa: E402
    DEFAULT_MODULE_TIMEOUT, NESTED_GUARD_ENV, discover_modules, git_head,
    run_module)
from _preserved_scan import collect_node_ids, module_of, tail_of  # noqa: E402
from _baseline_capture import redact  # noqa: E402

SCHEMA_VERSION = 1
CAPTURE_KIND = "suite-delta-capture"

COUNT_KEYS = ("ran", "passed", "failures", "errors", "skipped",
              "expected_failures", "unexpected_successes")

#: Every outcome that is not a pass. `passed + these == ran` for a module the
#: runner summarised completely.
NON_PASSING_KEYS = ("failures", "errors", "skipped", "expected_failures",
                    "unexpected_successes")


# --- capture -----------------------------------------------------------------

def group_node_ids(node_ids):
    """`[tests.m.C.t, ...]` -> `{tests.m: [C.t, ...]}`, sorted and de-duped."""
    grouped = {}
    for nid in node_ids:
        grouped.setdefault(module_of(nid), set()).add(tail_of(nid))
    return {mod: sorted(tails) for mod, tails in sorted(grouped.items())}


def capture(root, label, module_timeout=DEFAULT_MODULE_TIMEOUT):
    """Run every test module and collect node ids, at ONE commit, in one pass."""
    python = sys.executable
    root = os.path.abspath(root)
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.update(NESTED_GUARD_ENV)

    started = time.time()
    rows = [run_module(python, root, dotted, module_timeout, env)
            for dotted in discover_modules(root)]
    node_ids, errors = collect_node_ids(root)
    elapsed = time.time() - started

    modules = {row["module"]: {k: row.get(k, 0) for k in COUNT_KEYS}
               for row in rows}
    for row in rows:
        modules[row["module"]]["status"] = row["status"]
        modules[row["module"]]["rc"] = row["rc"]

    totals = {"modules": len(rows), "node_ids": len(node_ids)}
    for key in COUNT_KEYS:
        totals[key] = sum(row.get(key, 0) for row in rows)
    totals["modules_ok"] = sum(1 for row in rows if row["status"] == "ok")

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": CAPTURE_KIND,
        "label": label,
        "tree_root": redact(root),
        "git_head": git_head(root),
        "captured_at": datetime.datetime.now(datetime.timezone.utc)
                               .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python": sys.version.split()[0],
        "wall_seconds": round(elapsed, 1),
        "module_env": dict(NESTED_GUARD_ENV),
        "totals": totals,
        "node_id_errors": errors,
        "modules": modules,
        "node_ids": group_node_ids(node_ids),
    }


# --- the gate (PURE) ----------------------------------------------------------

def _ids(cap, module):
    return set(cap.get("node_ids", {}).get(module, ()))


def _passed(cap, module):
    return int(cap.get("modules", {}).get(module, {}).get("passed", 0))


def _count(cap, module, key):
    return int(cap.get("modules", {}).get(module, {}).get(key, 0))


def _non_passing(cap, module):
    """Every outcome that is not a pass: the pool a rise may legitimately
    draw from. A test going red->green raises `passed` with no new node id,
    and that is progress, not a fabrication."""
    return sum(_count(cap, module, key) for key in NON_PASSING_KEYS)


def _failing(cap, module):
    return _count(cap, module, "failures") + _count(cap, module, "errors")


def _outcome_rules(before, after, module, out, notes):
    """Direction rules on the non-passing pool, so it cannot be raided quietly.

    Without these, the rise allowance below would launder the two cheapest ways
    to make a suite greener without fixing anything: let a test start failing
    somewhere else, or skip it.
    """
    if _failing(after, module) > _failing(before, module):
        out.append("FAILURE COUNT ROSE: %s failures+errors %d -> %d"
                   % (module, _failing(before, module), _failing(after, module)))
    if _count(after, module, "skipped") > _count(before, module, "skipped"):
        out.append("NEWLY SKIPPED: %s skipped %d -> %d -- a skipped test is a "
                   "test that stopped exercising something"
                   % (module, _count(before, module, "skipped"),
                      _count(after, module, "skipped")))
    old_status = before["modules"][module].get("status")
    new_status = after["modules"][module].get("status")
    if old_status == "ok" and new_status != "ok":
        out.append("STATUS REGRESSION: %s %s -> %s"
                   % (module, old_status, new_status))
    converted = _non_passing(before, module) - _non_passing(after, module)
    if converted > 0:
        notes.append("REPAIRED: %s %d non-passing outcome(s) became passes"
                     % (module, converted))


def _shared_module(before, after, module, out, notes):
    """One module present on BOTH sides: the pass-count accounting.

    A pass count may rise by exactly what NEW NODE IDS and REPAIRED OUTCOMES
    account for, and no more. The remainder is the defect this gate exists for:
    the same tests, the same outcomes, and somehow more passes.

    What it can NOT see, stated so nobody mistakes a green gate for more than
    it is: a test whose assertions were gutted still resolves red->green here
    and looks like a repair. This gate closes the ARITHMETIC; the body differ
    (task 30) and review close the meaning.
    """
    was, now = _passed(before, module), _passed(after, module)
    gained = _ids(after, module) - _ids(before, module)
    lost = _ids(before, module) - _ids(after, module)
    delta_np = _non_passing(after, module) - _non_passing(before, module)
    _outcome_rules(before, after, module, out, notes)

    allowed_rise = len(gained) + max(0, -delta_np)
    if now > was and (now - was) > allowed_rise:
        out.append("UNEXPLAINED PASS-COUNT RISE: %s passed %d -> %d (+%d); %d "
                   "new node id(s) and %d repaired outcome(s) account for only "
                   "%d -- a rise nobody added a test for and nothing was fixed "
                   "for is a test that stopped exercising something"
                   % (module, was, now, now - was, len(gained),
                      max(0, -delta_np), allowed_rise))
    allowed_drop = len(lost) + max(0, delta_np)
    if now < was and (was - now) > allowed_drop:
        out.append("UNEXPLAINED PASS-COUNT DROP: %s passed %d -> %d (-%d); %d "
                   "removed node id(s) and %d new non-passing outcome(s) "
                   "account for only %d -- passes went somewhere unrecorded"
                   % (module, was, now, was - now, len(lost),
                      max(0, delta_np), allowed_drop))
    if gained or lost:
        notes.append("CHANGED: %s +%d/-%d node id(s), passed %d -> %d"
                     % (module, len(gained), len(lost), was, now))


def _carried_over(before, after):
    """Node ids `before` owned under a module that STILL exists in `after`.

    Ids owned by a module that disappeared are deliberately NOT carried over:
    those are exactly the ids a MOVE re-homes, and counting them as already-seen
    would make every relocation look like a module inventing passes out of
    tests it does not own.
    """
    surviving = set(after.get("modules", {}))
    return {nid for mod, ids in before.get("node_ids", {}).items()
            if mod in surviving for nid in ids}


def _added_module(before, after, module, out, notes):
    """A module only in `after`: every pass must be a genuinely new node id."""
    fresh = _ids(after, module) - _carried_over(before, after)
    now = _passed(after, module)
    if len(fresh) < now:
        out.append("NEW MODULE UNACCOUNTED: %s reports %d pass(es) but only %d "
                   "of its node ids are new -- counts and node ids disagree "
                   "about where those tests came from" % (module, now, len(fresh)))
    else:
        notes.append("ADDED: %s (+%d node id(s), %d pass(es))"
                     % (module, len(fresh), now))


def _removed_module(before, after, module, out, notes):
    """A module only in `before`: a MOVE is fine, a disappearance is not."""
    everywhere = {nid for ids in after.get("node_ids", {}).values()
                  for nid in ids}
    orphaned = sorted(_ids(before, module) - everywhere)
    if orphaned:
        out.append("LOST MODULE: %s (%d pass(es)) is gone and %d of its node "
                   "ids are collected nowhere else, e.g. %s"
                   % (module, _passed(before, module), len(orphaned),
                      ", ".join(orphaned[:3])))
    else:
        notes.append("MOVED: %s -- every node id it owned is collected "
                     "elsewhere" % module)


def consistency_violations(cap, side):
    """PURE: a single capture's counts vs its own collection.

    A module cannot run -- let alone pass -- more tests than were collected
    from it. Verified true for every module of both seed captures, which makes
    it a real invariant rather than a hopeful one, and it is what turns a
    silently DROPPED node id into a failure: without it, ids only ever act as
    an allowance for count changes, so removing one while the pass count stays
    put is invisible.
    """
    out = []
    for module, row in sorted(cap.get("modules", {}).items()):
        collected = len(cap.get("node_ids", {}).get(module, ()))
        ran, passed = int(row.get("ran", 0)), int(row.get("passed", 0))
        if ran > collected or passed > collected:
            out.append("COUNTS EXCEED COLLECTION: %s %s ran %d / passed %d but "
                       "only %d node id(s) were collected"
                       % (side, module, ran, passed, collected))
    return out


def checkout_note(before, after):
    """A one-line warning when the two sides were captured in DIFFERENT trees.

    Learned the hard way while seeding this gate: several suites resolve
    fixtures RELATIVE to the repo root, so a capture taken in a `git worktree`
    at another path skips or fails tests for reasons that have nothing to do
    with the commit -- three modules' outcomes changed here purely by moving.
    Comparing two checkouts measures the LOCATION as well as the code, so the
    per-commit workflow captures both sides in the same tree, and a pair that
    does not is labelled rather than quietly trusted.
    """
    old, new = before.get("tree_root"), after.get("tree_root")
    if old and new and old != new:
        return ("CHECKOUT SKEW: captured in different trees (%s -> %s); "
                "outcome differences may be environmental, not code" % (old, new))
    return None


def diff_captures(before, after):
    """PURE: two captures -> {'violations': [...], 'notes': [...]}."""
    out, notes = [], []
    skew = checkout_note(before, after)
    if skew:
        notes.append(skew)
    old = set(before.get("modules", {}))
    new = set(after.get("modules", {}))
    for module in sorted(old & new):
        _shared_module(before, after, module, out, notes)
    for module in sorted(new - old):
        _added_module(before, after, module, out, notes)
    for module in sorted(old - new):
        _removed_module(before, after, module, out, notes)
    out.extend(consistency_violations(before, "before"))
    out.extend(consistency_violations(after, "after"))
    for err in after.get("node_id_errors", ()):
        out.append("COLLECTION ERROR at the after commit: %s" % err)
    return {"violations": sorted(out), "notes": sorted(notes)}


def totals_delta(before, after):
    """Headline numbers for the report. Never a gate on its own: a suite-wide
    total can stay flat while one module quietly gains what another lost."""
    a, b = before.get("totals", {}), after.get("totals", {})
    return {key: (a.get(key, 0), b.get(key, 0), b.get(key, 0) - a.get(key, 0))
            for key in ("modules", "ran", "passed", "failures", "errors",
                        "node_ids")}


# --- CLI ----------------------------------------------------------------------

def load_capture(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("kind") != CAPTURE_KIND:
        raise SystemExit("%s is not a %s" % (path, CAPTURE_KIND))
    return data


def _cmd_capture(args):
    data = capture(args.root, args.label, args.module_timeout)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=False)
        fh.write("\n")
    t = data["totals"]
    print("suite-delta capture[%s] -> %s" % (data["label"], args.out))
    print("  head:     %s" % data["git_head"])
    print("  modules:  %d (ok %d)" % (t["modules"], t["modules_ok"]))
    print("  tests:    ran %d, passed %d, failures %d, errors %d"
          % (t["ran"], t["passed"], t["failures"], t["errors"]))
    print("  node ids: %d in %d module(s)"
          % (t["node_ids"], len(data["node_ids"])))
    print("  wall:     %ss" % data["wall_seconds"])
    return 0


def _cmd_check(args):
    before, after = load_capture(args.before), load_capture(args.after)
    report = diff_captures(before, after)
    print("suite delta %s (%s) -> %s (%s)"
          % (before["label"], (before.get("git_head") or "?")[:9],
             after["label"], (after.get("git_head") or "?")[:9]))
    for key, (was, now, delta) in totals_delta(before, after).items():
        print("  %-9s %6d -> %6d  (%+d)" % (key, was, now, delta))
    if args.verbose:
        for note in report["notes"]:
            print("  note: %s" % note)
    for line in report["violations"]:
        print("  %s" % line)
    print("SUITE DELTA %s (%d violation(s), %d accounted change(s))"
          % ("FAILED" if report["violations"] else "PASSED",
             len(report["violations"]), len(report["notes"])))
    return 1 if report["violations"] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="per-commit suite delta gate")
    sub = parser.add_subparsers(dest="cmd", required=True)

    cap = sub.add_parser("capture", help="run the suite and stamp a capture")
    cap.add_argument("--root", default=".")
    cap.add_argument("--label", default="head")
    cap.add_argument("--out", required=True)
    cap.add_argument("--module-timeout", type=int, default=DEFAULT_MODULE_TIMEOUT)
    cap.set_defaults(func=_cmd_capture)

    chk = sub.add_parser("check", help="diff two captures")
    chk.add_argument("--before", required=True)
    chk.add_argument("--after", required=True)
    chk.add_argument("--verbose", action="store_true")
    chk.set_defaults(func=_cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
