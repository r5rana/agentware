#!/usr/bin/env python3
"""The RUNTIME half of the constant differ (task 31).

Kept in its own file because it is the one part of this gate that is NOT
hermetic. `_constant_differ.py` reads the tree with `ast` and imports nothing;
this module starts a subprocess, imports both the monolith and a lib module for
real, and compares the objects they actually produce. Two different kinds of
evidence, two different failure modes, two files.

Why a runtime comparison is not optional
----------------------------------------
Recorded in `learn-a-derived-constant-is-invisible-to-a-static-allowlist-gate`:
a static check over a DERIVED value silently narrows to the syntax it was
written against, and reports OK while the data it was meant to police sits one
table to the left. Six of this migration's value-divergences are exactly that
shape — the lib reaches the same object by a different expression
(`_find_repo_root(__file__)` where the monolith spelt two `dirname` calls,
`paths.REPO_ROOT` where it had a module-local rebind). No literal comparison can
tell "refactored" from "changed". Importing both and asking is the only thing
that can.

What happens when the monolith goes
-----------------------------------
Once the monolith is deleted (task 56) the comparison has no left-hand side and
the probe degrades to `mode: "resolve"` — the lib side must still import and
still carry the name. See `apply_probe` for why that is not the same as
returning nothing.

Why it reports DIGESTS rather than values
-----------------------------------------
These constants resolve to absolute paths under the operator's home directory.
The report is printed, and the fixture beside it is a committed file. So the
probe hashes each `repr()` and hands back a 12-hex prefix: enough to see WHICH
side moved when two stop agreeing, and nothing that can leak into a commit.

Stdlib only, read-only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
DEFAULT_ROOT = os.environ.get("AGENTWARE_CONST_ROOT") or os.path.dirname(HERE)
PROBE_TIMEOUT = 180

# Runs in a CHILD interpreter: importing the 34k-line monolith and ~120 lib
# modules into the test process would leave both resident for every later test
# in the run, and an import-time side effect would be attributed to whatever
# test happened to be next.
_PROBE_SRC = r"""
import hashlib, importlib, json, sys
sys.path.insert(0, sys.argv[1])
out = {}
try:
    legacy = importlib.import_module("agentware_legacy")
except ModuleNotFoundError:
    legacy = None                              # task 56 terminal state -- see below
for name, modpath in json.loads(sys.argv[2]):
    row = {"mode": "compare" if legacy is not None else "resolve"}
    try:
        mod = importlib.import_module(modpath)
        vals = [repr(getattr(mod, name))]
        if legacy is not None:
            vals.insert(0, repr(getattr(legacy, name)))
            row["equal"] = vals[0] == vals[1]
        else:
            row["resolved"] = True
        row["digests"] = [hashlib.sha256(v.encode()).hexdigest()[:12] for v in vals]
    except Exception as exc:                   # noqa: BLE001 - reported, not raised
        row["error"] = "%s: %s" % (type(exc).__name__, exc)
    out[name] = row
json.dump(out, sys.stdout)
"""


def module_path(rel):
    """'scripts/agentware_lib/core/paths.py' -> 'agentware_lib.core.paths'."""
    stem = rel[len("scripts/"):] if rel.startswith("scripts/") else rel
    stem = stem[:-len(".py")] if stem.endswith(".py") else stem
    if stem.endswith("/__init__"):
        stem = stem[:-len("/__init__")]
    return stem.replace("/", ".")


def probe_targets(fixture):
    """[(name, lib module)] for every row whose live objects must agree.

    Two families. The `equivalent` divergences, which have no static obligation
    a compare could discharge; and every `resolved` monolith-only row, so a
    constant that lands with the right LITERAL but resolves to a different value
    (a changed table one level down) is still caught. The second family is empty
    today and arms itself with the first port.
    """
    out = []
    for row in fixture["divergent"]:
        if row["decision"] == "equivalent" and row.get("lib_sites"):
            out.append((row["name"], module_path(row["lib_sites"][0])))
    for row in fixture["legacy_only"]:
        if row["status"] == "resolved" and row.get("lib_sites"):
            out.append((row["name"], module_path(row["lib_sites"][0])))
    return sorted(set(out))


def run_probe(targets, root=DEFAULT_ROOT):
    """Import both sides in a SUBPROCESS and compare the live objects."""
    if not targets:
        return {}
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE_SRC, os.path.join(root, "scripts"),
         json.dumps([list(t) for t in targets])],
        capture_output=True, text=True, cwd=root, timeout=PROBE_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError("constant probe failed: %s" % proc.stderr.strip()[-400:])
    return json.loads(proc.stdout)


def apply_probe(report, results, fail):
    """Fold probe results into a differ report via its `fail` recorder.

    An ERROR is a violation, not a skip. An unimportable module or a missing
    attribute means the probe could not answer, and "could not answer" must
    never read the same as "agreed" -- that is the exact silence this whole
    gate exists to break.

    TERMINAL (`mode == "resolve"`): once the monolith is deleted there is no
    left-hand side, so the comparison is genuinely impossible. What is NOT
    impossible is asking whether the lib side still resolves -- the constant is
    still expected to exist, in that module, importable. So the probe degrades
    to the half that still has an answer rather than to nothing, and the rows it
    could only resolve are named in the report so a degraded run never reads
    like a full one. Returning an empty result set on an absent monolith would
    have been the same "could not answer looks like agreed" that the paragraph
    above forbids, one level up.
    """
    for name in sorted(results):
        row = results[name]
        if row.get("error"):
            fail(report, "runtime", name, row["error"])
        elif row.get("mode") == "resolve":
            if not row.get("resolved"):
                fail(report, "runtime", name, "lib side did not resolve")
            else:
                report.setdefault("runtime_resolved", []).append(name)
        elif not row.get("equal"):
            fail(report, "runtime", name,
                 "live values differ (%s)" % " vs ".join(row.get("digests", [])))
    return report
