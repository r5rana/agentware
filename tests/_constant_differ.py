#!/usr/bin/env python3
"""The CONSTANT DIFFER — the second gate no surface comparison can replace.

Feature 260813-modular-core-adoption, task 31. Sibling of `_body_differ.py`.

Why this exists
---------------
The body differ floors the definitions the two trees SHARE. It says nothing
about module-level constants, and constants are where this migration loses
things most quietly:

* Hundreds of module constants live ONLY in the monolith — the file task 56
  deletes. Every one of them is a threshold, a regex, a state table, a cap or an
  env-var key that some ported verb will read. If a port lands the verb and
  forgets the constant, the verb still parses, the surface fixture still matches
  and the manifest row still says `required`; the behaviour is simply gone.
* A handful of constants exist on BOTH sides with DIFFERENT values. A donor
  module landing keeps ITS OWN literal, and nothing anywhere says which value
  was decided. `SKILL_PIN_FLOOR` is `0.85` in the monolith and `None` in the
  lib; `BM25_CACHE_VERSION` is `2` here and `1` there over the SAME cache path.
  Neither difference changes a flag, a verb or a test node id.

So this gate pins both halves: every monolith-only constant must either still
hold its recorded literal in the monolith (not yet ported, and it may not drift
meanwhile) or resolve under the lib with a BYTE-IDENTICAL literal — and every
value-divergence must carry a recorded decision the live tree matches.

Normalisation (PINNED — the counts depend on it)
------------------------------------------------
A CONSTANT is a module-scope assignment (`Assign`, tuple-unpack targets
included, or an `AnnAssign` that carries a value) to a NAME matching
``^_?[A-Z][A-Z0-9_]*$`` — upper-snake, private or public — excluding dunders.
Its LITERAL is `ast.unparse()` of the assigned value expression, which discards
formatting (quote style, wrapping, comments) but not the program, so a reformat
cannot redden the gate and a deletion cannot hide behind one.

A name assigned more than once on one side is represented by its LONGEST
literal (ties broken lexicographically, so the capture is deterministic), and
every such name is pinned by name in `multi_literal`. That matters: the
monolith forward-declares `DREAM_STEP_FUNCS = ()` before filling it, and
`_EXPORTS` is a per-module export list defined at ~118 lib sites. Both stay
visible instead of being silently averaged away.

Scope (also pinned)
-------------------
* LEGACY = `scripts/agentware_legacy.py`, the monolith preserved verbatim by
  task 25 (proven byte-identical to the pre-merge entrypoint).
* LIB    = every `.py` under `scripts/agentware_lib/`.

Nothing else. The plan's obligation is "resolves under the LIB", so a constant
that survives in, say, `scripts/dashboard.py` has NOT been ported. The facade is
excluded on both sides: it is new to the merged tree and belongs to neither.

Because both sides live in THIS tree, `--write` needs no donor checkout — unlike
the body differ, this baseline regenerates from the repo alone.

How a row is discharged
-----------------------
`legacy_only` rows carry a `status`:

* ``pending``  — not under the lib yet; a named `follow_up` task owns it. The
  gate pins the LEGACY literal so it cannot drift or vanish before it is ported,
  and pins the lib literal the moment it appears.
* ``resolved`` — under the lib with a byte-identical literal. It may never
  change and may never go back.
* ``excluded`` — a plan decision deliberately drops it, with `reason` and
  `follow_up`. Carries an ABSENCE probe: it must be absent from the lib, so an
  excluded constant quietly reappearing is a failure too.

`divergent` rows carry a `decision`:

* ``lib``        — the lib's value is the decided one (task 24 bumped
  `BM25_CACHE_VERSION` to a value neither tree had used). Gate: the lib literal
  equals `decided_literal`, exactly.
* ``legacy``     — this tree's value is decided but has NOT landed yet. Gate:
  legacy still holds `decided_literal`, AND the lib holds either
  `decided_literal` (landed) or `pending_literal` (the value recorded on the day
  it was adjudicated). Two states, no third: any other value reddens the gate.
* ``equivalent`` — the two spellings are refactors of one another (`REPO_ROOT`
  via `_find_repo_root` rather than two `dirname` calls). A static compare can
  never discharge this, so these are proven at RUNTIME by `_constant_probe.py`,
  which imports both sides in a subprocess and compares the live objects. They
  ALSO carry a static pin on both spellings, so `--no-probe` does not leave them
  ungated: a gate armed only behind an opt-in flag is the same silence.

Why a runtime probe at all
--------------------------
Recorded in `learn-a-derived-constant-is-invisible-to-a-static-allowlist-gate`:
a static check over a DERIVED value silently narrows to the syntax it was
written against. `KB_GITIGNORE_REQUIRED` is `A + B + (C,)` and holds no literal
of its own; a change one table to the left is invisible here. The runtime half
lives in its own module because it is the one part of this gate that is not
hermetic — see `_constant_probe.py` for what it compares and why it reports
digests rather than values.

What this gate CANNOT do
------------------------
It compares literals, not meaning. A constant ported with the right literal into
the wrong module, or read by nobody, passes. It is blind to a value both trees
get wrong. And `pending` is a promise, not a proof: it says the source value is
still intact, not that anything reads it yet.

Stdlib only, read-only, deterministic.

Usage:
    python3 tests/_constant_differ.py --write
    python3 tests/_constant_differ.py --check
    python3 tests/_constant_differ.py --check --no-probe   # hermetic, static only
    python3 tests/_constant_differ.py --check --format json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
REPO_ROOT = os.environ.get("AGENTWARE_CONST_ROOT") or os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "fixtures", "constant-divergence.json")

SCHEMA = "agentware/constant-divergence@1"
CONST_NAME_RE = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
NORMALISATION = (
    "module-scope assignment to a name matching ^_?[A-Z][A-Z0-9_]*$ (dunders "
    "excluded); literal is ast.unparse of the assigned value expression; a "
    "name assigned twice on one side is represented by its longest literal"
)

LEGACY_REL = os.path.join("scripts", "agentware_legacy.py")
LIB_REL = os.path.join("scripts", "agentware_lib")

STATUSES = ("pending", "resolved", "excluded")
DECISIONS = ("lib", "legacy", "equivalent")


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------

def _targets(node):
    """Every module-scope Name this statement assigns, or ()."""
    if isinstance(node, ast.Assign):
        out = []
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                out.append(tgt.id)
            elif isinstance(tgt, (ast.Tuple, ast.List)):
                out.extend(e.id for e in tgt.elts if isinstance(e, ast.Name))
        return out
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        if isinstance(node.target, ast.Name):
            return [node.target.id]
    return []


def constants_in(source, rel):
    """{name: [(rel, literal), ...]} for one module's top-level constants."""
    out = {}
    for node in ast.parse(source).body:
        for name in _targets(node):
            if CONST_NAME_RE.match(name) and not name.startswith("__"):
                out.setdefault(name, []).append((rel, ast.unparse(node.value)))
    return out


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def collect_tree(root, rel_path):
    """Constants under <root>/<rel_path>, whether it is a file or a package."""
    full = os.path.join(root, rel_path)
    out = {}
    files = []
    if os.path.isfile(full):
        files = [rel_path]
    else:
        for dirpath, dirnames, filenames in os.walk(full):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for name in sorted(filenames):
                if name.endswith(".py"):
                    files.append(os.path.relpath(
                        os.path.join(dirpath, name), root))
    for rel in sorted(files):
        for name, sites in constants_in(_read(os.path.join(root, rel)),
                                        rel.replace(os.sep, "/")).items():
            out.setdefault(name, []).extend(sites)
    return out


def legacy_tree(root=REPO_ROOT):
    return collect_tree(root, LEGACY_REL)


def lib_tree(root=REPO_ROOT):
    return collect_tree(root, LIB_REL)


def representative(sites):
    """The longest literal among a name's sites; ties broken lexicographically."""
    return max((lit for _, lit in sites), key=lambda s: (len(s), s))


def distinct(sites):
    return len({lit for _, lit in sites})


def sites_of(tree, name):
    return sorted({rel for rel, _ in tree.get(name, [])})


# --------------------------------------------------------------------------
# baseline construction
# --------------------------------------------------------------------------

def _previous(root):
    """The committed baseline, or an empty one on the very first capture."""
    path = FIXTURE if root == REPO_ROOT else os.path.join(
        root, "tests", "fixtures", os.path.basename(FIXTURE))
    try:
        return load_fixture(path)
    except (OSError, ValueError):
        return {}


def _pinned_literal(prev, key, tree, name):
    """The MONOLITH side, anchored to the day the row was first captured.

    Never re-derived. A row exists precisely because the monolith holds a value
    the lib owes, and the monolith is a file this plan DELETES (task 56); a
    baseline that re-reads it every time would quietly evaporate the moment it
    goes. Pinning it also means an edit to the monolith reddens the gate instead
    of being absorbed by the next `--write`.
    """
    if prev and key in prev:
        return prev[key]
    return representative(tree[name])


def _legacy_only_row(name, legacy, lib, adjudications, prev):
    row = {
        "name": name,
        "literal": _pinned_literal(prev, "literal", legacy, name),
        "legacy_sites": (prev or {}).get("legacy_sites") or sites_of(legacy, name),
        "status": "resolved" if name in lib else "pending",
        "follow_up": adjudications.follow_up_for(name),
    }
    row.update(adjudications.LEGACY_ONLY.get(name, {}))
    if name in lib:
        row["lib_sites"] = sites_of(lib, name)
    return row


def _divergent_row(name, legacy, lib, adjudications, prev):
    row = {
        "name": name,
        "legacy_literal": _pinned_literal(prev, "legacy_literal", legacy, name),
        "lib_literal": representative(lib[name]) if name in lib
                       else (prev or {}).get("lib_literal", ""),
        "legacy_sites": (prev or {}).get("legacy_sites") or sites_of(legacy, name),
        "lib_sites": sites_of(lib, name) or (prev or {}).get("lib_sites", []),
    }
    row.update(adjudications.DIVERGENT[name])
    if row["decision"] == "lib":
        row["decided_literal"] = (prev or {}).get("decided_literal",
                                                  row["lib_literal"])
    elif row["decision"] == "legacy":
        row["decided_literal"] = row["legacy_literal"]
        row["landed"] = row["lib_literal"] == row["decided_literal"]
        if not row["landed"]:
            row["pending_literal"] = row["lib_literal"]
    return row


def _tracked(live_names, previous, key):
    """Row names are a RATCHET: live ones UNION every name already committed.

    Without this the gate disarms itself on the first successful port. A
    monolith-only constant that lands correctly stops being monolith-only, so a
    naive regeneration drops its row — and the constant it was protecting
    becomes free to change in the very next commit. Once a name is tracked, it
    stays tracked, and the port simply flips its status to `resolved`.
    """
    return sorted(set(live_names) | {r["name"] for r in previous.get(key, [])})


def build_fixture(root, adjudications, previous=None):
    legacy, lib = legacy_tree(root), lib_tree(root)
    previous = previous if previous is not None else _previous(root)
    shared = sorted(set(legacy) & set(lib))
    live_divergent = [n for n in shared
                      if representative(legacy[n]) != representative(lib[n])]
    unknown = sorted(set(live_divergent) - set(adjudications.DIVERGENT))
    if unknown:
        raise SystemExit("unadjudicated value-divergence: %s" % ", ".join(unknown))
    only = _tracked(set(legacy) - set(lib), previous, "legacy_only")
    divergent = _tracked(live_divergent, previous, "divergent")
    pins = {key: {r["name"]: r for r in previous.get(key, [])}
            for key in ("legacy_only", "divergent")}
    multi = {n: [distinct(legacy.get(n, [])), distinct(lib.get(n, []))]
             for n in sorted(set(legacy) | set(lib))
             if distinct(legacy.get(n, [])) > 1 or distinct(lib.get(n, [])) > 1}
    rows = [_legacy_only_row(n, legacy, lib, adjudications,
                             pins["legacy_only"].get(n)) for n in only]
    drows = [_divergent_row(n, legacy, lib, adjudications,
                            pins["divergent"].get(n)) for n in divergent]
    return {
        "schema": SCHEMA,
        "generated_by": "tests/_constant_differ.py --write",
        "normalisation": NORMALISATION,
        "scope": {"legacy": LEGACY_REL.replace(os.sep, "/"),
                  "lib": LIB_REL.replace(os.sep, "/") + "/**.py"},
        "counts": {
            "legacy": len(legacy), "lib": len(lib), "legacy_only": len(only),
            "shared": len(shared), "divergent": len(divergent),
        },
        "ratchet": {
            "unresolved_max": sum(1 for r in rows if r["status"] == "pending"),
            "undecided_max": sum(1 for r in drows if r.get("landed") is False),
        },
        "multi_literal": multi,
        "legacy_only": rows,
        "divergent": drows,
    }


# --------------------------------------------------------------------------
# the gate — pure functions over (fixture, live capture)
# --------------------------------------------------------------------------

def _fail(report, kind, name, detail):
    report[kind].append({"name": name, "detail": detail})


def check_legacy_only(row, legacy, lib, report):
    """One monolith-only constant against the live trees."""
    name = row["name"]
    in_lib = name in lib
    if row["status"] == "excluded":
        if in_lib:
            _fail(report, "excluded_present", name, "absence probe failed")
        return
    if in_lib:
        report["landed"].append(name)
        live = representative(lib[name])
        if live != row["literal"]:
            _fail(report, "literal_mismatch", name,
                  "lib literal differs from the monolith's")
        return
    report["unresolved"].append(name)
    if name not in legacy:
        _fail(report, "lost", name, "gone from the monolith and never ported")
    elif representative(legacy[name]) != row["literal"]:
        _fail(report, "legacy_drift", name, "monolith literal changed while pending")


def check_divergent(row, legacy, lib, report):
    """One value-divergence against its recorded decision."""
    name = row["name"]
    if name not in lib:
        _fail(report, "lost", name, "divergent constant vanished from the lib")
        return
    live = representative(lib[name])
    if row["decision"] == "equivalent":
        report["equivalent"].append(name)
        if name not in legacy:
            _fail(report, "lost", name, "equivalence lost its monolith side")
            return
        # An equivalence is discharged at RUNTIME, but pin both spellings
        # statically too: without this, `--no-probe` would leave these six rows
        # entirely ungated, and a gate that is only armed on an opt-in flag is
        # the same silence this differ exists to break. Re-spelling either side
        # is legitimate -- it just has to go through `--write`, where a reviewer
        # sees it.
        if representative(legacy[name]) != row["legacy_literal"]:
            _fail(report, "legacy_drift", name, "equivalence re-spelt in the monolith")
        if live != row["lib_literal"]:
            _fail(report, "decision_mismatch", name, "equivalence re-spelt in the lib")
        return
    allowed = [row["decided_literal"]]
    if row["decision"] == "legacy":
        if name in legacy and representative(legacy[name]) != row["decided_literal"]:
            _fail(report, "legacy_drift", name, "decided value edited in the monolith")
        if "pending_literal" in row:
            allowed.append(row["pending_literal"])
    if live not in allowed:
        _fail(report, "decision_mismatch", name,
              "lib value is neither the decision nor its pinned pending value")
    elif live != row["decided_literal"]:
        report["undecided"].append(name)


def check_ratchet(fixture, report):
    limits = fixture["ratchet"]
    if len(report["unresolved"]) > limits["unresolved_max"]:
        _fail(report, "ratchet", "unresolved",
              "%d > %d" % (len(report["unresolved"]), limits["unresolved_max"]))
    if len(report["undecided"]) > limits["undecided_max"]:
        _fail(report, "ratchet", "undecided",
              "%d > %d" % (len(report["undecided"]), limits["undecided_max"]))


def check_multi(fixture, legacy, lib, report):
    """A constant may not escape the gate by gaining a second definition."""
    live = {n: [distinct(legacy.get(n, [])), distinct(lib.get(n, []))]
            for n in sorted(set(legacy) | set(lib))
            if distinct(legacy.get(n, [])) > 1 or distinct(lib.get(n, [])) > 1}
    pinned = fixture["multi_literal"]
    for name in sorted(set(live) | set(pinned)):
        if live.get(name) != pinned.get(name):
            _fail(report, "multi_literal", name,
                  "pinned %s, live %s" % (pinned.get(name), live.get(name)))


REPORT_KINDS = ("lost", "literal_mismatch", "legacy_drift", "decision_mismatch",
                "excluded_present", "multi_literal", "ratchet", "runtime")


def check(fixture, legacy, lib):
    """Compare the live trees against the baseline. Pure; no I/O.

    An EMPTY legacy tree is task 56's terminal state, not a finding. Once the
    monolith is deleted there is no monolith side for an `equivalent` row to be
    compared against and no second literal for a `multi_literal` row to disagree
    with, so those two checks are discharged rather than reported — the same
    shape as the legacy ratchet, which scores an absent monolith as zero rather
    than special-casing its own retirement.

    Keyed on the WHOLE tree being empty, deliberately, and never on an individual
    name going missing: one constant vanishing while the monolith still stands is
    exactly the silent loss this differ exists to catch, and it stays a failure.
    """
    report = {k: [] for k in REPORT_KINDS}
    report.update({"checked": len(fixture["legacy_only"]) + len(fixture["divergent"]),
                   "unresolved": [], "landed": [], "undecided": [],
                   "equivalent": []})
    for row in fixture["legacy_only"]:
        check_legacy_only(row, legacy, lib, report)
    terminal = not legacy
    for row in fixture["divergent"]:
        if terminal and row.get("decision") == "equivalent":
            report["equivalent"].append(row["name"])
            continue
        check_divergent(row, legacy, lib, report)
    if not terminal:
        check_multi(fixture, legacy, lib, report)
    check_ratchet(fixture, report)
    return report


def violations(report):
    return [(kind, row) for kind in REPORT_KINDS for row in report[kind]]


def load_fixture(path=FIXTURE):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _render(report):
    lines = ["constant-differ: %d rows checked" % report["checked"],
             "  unresolved: %d   landed: %d   undecided: %d   equivalent: %d"
             % (len(report["unresolved"]), len(report["landed"]),
                len(report["undecided"]), len(report["equivalent"]))]
    for kind in REPORT_KINDS:
        if report[kind]:
            lines.append("  %s: %d" % (kind, len(report[kind])))
            for row in report[kind]:
                lines.append("    %-44s %s" % (row["name"], row["detail"]))
    if not violations(report):
        lines.append("  OK - no constant lost, drifted or overruled")
    return "\n".join(lines)


def _write(args):
    sys.path.insert(0, HERE)
    import _constant_adjudication as adj
    fixture = build_fixture(args.root, adj)
    with open(args.fixture, "w", encoding="utf-8") as fh:
        json.dump(fixture, fh, indent=1, ensure_ascii=False, sort_keys=False)
        fh.write("\n")
    print("wrote %s: %s" % (args.fixture, fixture["counts"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="constant differ (task 31)")
    ap.add_argument("--write", action="store_true", help="regenerate the baseline")
    ap.add_argument("--check", action="store_true", help="gate the tree")
    ap.add_argument("--no-probe", dest="probe", action="store_false",
                    help="skip the runtime comparison (hermetic static check only)")
    ap.set_defaults(probe=True)
    ap.add_argument("--root", default=REPO_ROOT)
    ap.add_argument("--fixture", default=FIXTURE)
    ap.add_argument("--format", choices=("text", "json"), default="text")
    args = ap.parse_args(argv)

    if args.write:
        return _write(args)
    if not args.check:
        ap.error("one of --write / --check is required")
    fixture = load_fixture(args.fixture)
    report = check(fixture, legacy_tree(args.root), lib_tree(args.root))
    if args.probe:
        sys.path.insert(0, HERE)
        import _constant_probe as probe
        probe.apply_probe(report, probe.run_probe(probe.probe_targets(fixture),
                                                  args.root), _fail)
    if args.format == "json":
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(_render(report))
    return 1 if violations(report) else 0


if __name__ == "__main__":
    sys.exit(main())
