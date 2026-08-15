#!/usr/bin/env python3
"""The AST BODY DIFFER — the gate no surface comparison can replace.

Feature 260813-modular-core-adoption, task 30.

Why this exists
---------------
Every other gate in this migration compares SHAPES. The union fixture compares
argparse objects; the parity manifest compares capability rows; the mutation
canary proves one patched symbol is observable; the preserved-surface freeze
compares test node ids. **Not one of them can see inside a function.**

Of the top-level definitions present in BOTH trees, 242 have normalised bodies
that DIFFER, in both directions — most where this tree's body is longer (the
donor holds the older, thinner one) and a minority where the donor's is longer
(this tree is missing donor logic inside a same-named function). A module
landing with the thinner body changes no flag, no verb, no manifest row and no
test node id. The suite stays green and the capability is gone.

So this gate fixes a per-definition FLOOR: for every divergent definition, the
merged tree must still hold a body at least as long as its adjudicated winner's.
It runs before the first port (task 37) precisely because it is the only thing
standing between a port and a silent downgrade.

What "normalised body" means here (PINNED — the row counts depend on it)
-----------------------------------------------------------------------
`ast.unparse()` of the function's body statements, joined by newlines, with the
**docstring INCLUDED**. Unparsing normalises away the things that are not the
program — comments, blank lines, indentation width, quote style, line wrapping —
so a reformat cannot redden the gate and a deletion cannot hide behind one.

The docstring is deliberately kept. A docstring here is not decoration: it is
where the contracts live (`build_index_from_frontmatter`'s docstring is what
records that `problems` is a REPORT rather than a verdict, and which caller
quarantines on it). Dropping it from the measure would let a body shed its
entire contract while the gate reported no change. The cost is that a pure prose
edit registers as a divergence — which is correct, and is exactly how the
`git_entry_ids_at_ref` row was caught and adjudicated.

Scope (also pinned)
-------------------
* LOCAL  = every top-level def under `scripts/**.py` on THIS tree, with
  `agentware_legacy.py` standing in for the pre-merge entrypoint (task 25 proved
  them byte-identical) and `agentware_lib/**` excluded, since the lib IS the
  donor's code and counting it on both sides would compare a tree with itself.
* DONOR  = every top-level def under the donor's `scripts/**.py`.
* MERGED = every top-level def under `scripts/**.py` INCLUDING `agentware_lib/`
  and `agentware_legacy.py`, plus the extensionless `scripts/agentware` facade,
  which is a real resolution site even though it carries no `.py` suffix.

Definitions are keyed by NAME, tree-wide, and a name defined at several sites is
represented by its LONGEST body (ties broken lexicographically, so the capture is
deterministic). Four names are multi-site today: `main`, `_median`, `_short`,
`get_embedder`.

How a row is discharged
-----------------------
Every row carries a `winner` — the tree whose body is authoritative — and an
`adjudication` saying how the merged tree satisfies it. Every row keeps a live
floor; there is no exempt row:

* ``longer-body``  — the default, and the length proxy: the winner is simply the
  longer body, and the merged tree must hold at least `winner_chars`.
* ``decomposed``   — the body was deliberately SPLIT (task 21's module splits,
  task 22's index work). The receiving definitions are named explicitly in
  `absorbs` at a declared `host` file, and the floor is met by the host body
  plus its absorbers. Nothing is forgiven: every absorber must still exist at
  its named path, so undoing the split, or renaming a helper away, reddens
  this — AND the host keeps a floor of its own (`floor_chars`), without which
  bulky helpers would happily pay for a parent that had been emptied out.
* ``superseded``   — a plan decision deliberately replaced the winner's body
  (task 15 deleted the KB-repointing module, task 18 rewrote `whereami`
  marker-driven, task 20 stripped an internal plan-artifact reference). The
  floor becomes `floor_chars`, pinned at the merged length on the day it was
  adjudicated, so the row still ratchets — it just ratchets against the decision
  instead of against the body the decision discarded.
* ``deferred``     — the winner's body has NOT landed yet and a named later task
  owns it (`follow_up`). Floored at `floor_chars` so it cannot get worse, and
  reported in its own column. The deferred set is pinned by name in the suite:
  growing it takes a deliberate edit that a reviewer sees.

What this gate CANNOT do (stated plainly, because the plan says so)
------------------------------------------------------------------
Length is a PROXY for correctness. A longer-and-wrong body passes. This gate
proves that nothing got THINNER, not that what landed is right; adjudicating the
242 divergences on their merits is a named follow-up plan. It also cannot see a
defect present in BOTH trees, since it only ever compares them against each
other.

Stdlib only, read-only, deterministic.

Usage:
    python3 tests/_body_differ.py --write --donor /path/to/donor
    python3 tests/_body_differ.py --check
    python3 tests/_body_differ.py --check --format json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
REPO_ROOT = os.environ.get("AGENTWARE_BODY_ROOT") or os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "fixtures", "body-divergence.json")

SCHEMA = "agentware/body-divergence@1"
NORMALISATION = ("ast.unparse of the function's body statements joined by "
                 "newlines, docstring included")

# The lib is the donor's own code living in this tree; counting it on the LOCAL
# side would diff the donor against itself and erase every real divergence.
LOCAL_EXCLUDE_PREFIXES = (os.path.join("scripts", "agentware_lib"),)
# The facade is new in both directions -- it belongs to the MERGED tree only.
FACADE = os.path.join("scripts", "agentware")

ADJUDICATIONS_DEFAULT = "longer-body"
WINNERS = ("local", "donor")


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------

def iter_sources(root, subdir="scripts", excludes=(), extra=()):
    """Yield (relpath, source) for every .py under <root>/<subdir>, sorted."""
    base = os.path.join(root, subdir)
    found = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if any(rel.startswith(p) for p in excludes):
                continue
            found.append(rel)
    found.extend(e for e in extra if os.path.isfile(os.path.join(root, e)))
    for rel in sorted(found):
        with open(os.path.join(root, rel), "r", encoding="utf-8") as fh:
            yield rel.replace(os.sep, "/"), fh.read()


def body_text(node):
    """The pinned normalisation: unparsed body statements, docstring included."""
    return "\n".join(ast.unparse(stmt) for stmt in node.body)


def collect_defs(sources):
    """{name: [(relpath, body), ...]} over TOP-LEVEL defs only.

    Top-level means module scope: a method on a class is owned by that class and
    is not a free definition either tree can silently swap.
    """
    out = {}
    for rel, src in sources:
        for node in ast.parse(src).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(node.name, []).append((rel, body_text(node)))
    return out


def representative(sites):
    """The longest body among a name's sites; ties broken lexicographically."""
    return max((body for _, body in sites), key=lambda b: (len(b), b))


def local_tree(root):
    return collect_defs(iter_sources(root, excludes=LOCAL_EXCLUDE_PREFIXES))


def donor_tree(root):
    return collect_defs(iter_sources(root))


def merged_tree(root):
    return collect_defs(iter_sources(root, extra=(FACADE,)))


# --------------------------------------------------------------------------
# baseline construction
# --------------------------------------------------------------------------

def build_rows(local, donor, adjudications):
    """Every shared name whose representative bodies differ, adjudicated."""
    rows = []
    for name in sorted(set(local) & set(donor)):
        lbody = representative(local[name])
        dbody = representative(donor[name])
        if lbody == dbody:
            continue
        row = {
            "name": name,
            "local_chars": len(lbody),
            "donor_chars": len(dbody),
            "winner": "local" if len(lbody) >= len(dbody) else "donor",
            "local_sites": sorted({rel for rel, _ in local[name]}),
            "donor_sites": sorted({rel for rel, _ in donor[name]}),
            "adjudication": ADJUDICATIONS_DEFAULT,
        }
        row["winner_chars"] = (row["local_chars"] if row["winner"] == "local"
                               else row["donor_chars"])
        row.update(adjudications.get(name, {}))
        rows.append(row)
    return rows


#: Row keys that come from the hand-audited table rather than from either tree.
#: Everything else on a row is a MEASUREMENT and may only be produced by --write.
_ADJUDICATED_KEYS = ("adjudication", "host", "floor_chars", "absorbs",
                     "rationale", "follow_up")


def reapply(fixture, adjudications):
    """Overlay the adjudication table onto the EXISTING rows. Returns (fixture, changed).

    The maintenance path for a port (tasks 37..54), and the reason it is separate
    from ``--write``: a port legitimately shrinks a shared definition (each one
    moves a verb's argparse block out of the monolith's ``build_parser`` into its
    own registrar), and re-deriving the baseline would silently RE-PIN
    ``winner_chars`` to the post-move length — the floor would follow the code
    down and the gate would certify its own regression. That is the trap recorded
    in `learn-under-a-body-length-differ-a-move-must-be-a-pure-move`. So this
    rewrites ONLY the hand-audited keys and never touches a measurement: the
    floors stay exactly where the two trees put them, and what changes is the
    written-down verdict about how the merged tree satisfies them.

    Idempotent: a row absent from the table is reset to the default, so removing
    an entry is as effective as adding one, and running it twice is a no-op.
    """
    changed = []
    for row in fixture["rows"]:
        before = {k: row.get(k) for k in _ADJUDICATED_KEYS}
        verdict = dict(adjudications.get(row["name"], {}))
        verdict.setdefault("adjudication", ADJUDICATIONS_DEFAULT)
        # Assign in place (a dict keeps an existing key's POSITION) and delete
        # only what the verdict no longer claims, so an unchanged row is
        # byte-identical and the diff shows exactly the rows that moved.
        for key in _ADJUDICATED_KEYS:
            if key in verdict:
                row[key] = verdict[key]
            elif key in row:
                del row[key]
        if {k: row.get(k) for k in _ADJUDICATED_KEYS} != before:
            changed.append(row["name"])
    return fixture, changed


def counts_of(local, donor, rows):
    shared = set(local) & set(donor)
    return {"shared": len(shared), "identical": len(shared) - len(rows),
            "divergent": len(rows)}


def build_fixture(local_root, donor_root, adjudications):
    local, donor = local_tree(local_root), donor_tree(donor_root)
    rows = build_rows(local, donor, adjudications)
    return {
        "schema": SCHEMA,
        "generated_by": "tests/_body_differ.py --write --donor <donor>",
        "normalisation": NORMALISATION,
        "scope": {
            "local": "scripts/**.py minus agentware_lib/** "
                     "(agentware_legacy.py stands in for the pre-merge entrypoint)",
            "donor": "scripts/**.py",
            "merged": "scripts/**.py plus the extensionless scripts/agentware facade",
        },
        "counts": counts_of(local, donor, rows),
        "rows": rows,
    }


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------

def _site_lengths(tree, name):
    return {rel: len(body) for rel, body in tree.get(name, [])}


def _absorber_lengths(tree, refs):
    """Resolve 'relpath::name' absorbers. Returns (total, missing_refs)."""
    total, missing = 0, []
    for ref in refs:
        rel, _, name = ref.partition("::")
        lengths = _site_lengths(tree, name)
        if rel not in lengths:
            missing.append(ref)
        else:
            total += lengths[rel]
    return total, missing


def available_chars(tree, row):
    """What the merged tree actually offers for this row.

    The plain case is the longest body carrying the name. A ``decomposed`` row
    adds its declared absorbers to its declared HOST site only -- never to some
    other site's body, which would let a legacy handler's bulk pay for a lib
    split it has nothing to do with.
    """
    lengths = _site_lengths(tree, row["name"])
    if not lengths:
        return None, []
    best = max(lengths.values())
    missing = []
    if row.get("absorbs"):
        extra, missing = _absorber_lengths(tree, row["absorbs"])
        host = row.get("host")
        if host in lengths:
            best = max(best, lengths[host] + extra)
    return best, missing


def required_chars(row):
    """The floor this row's TOTAL available body must clear."""
    if row["adjudication"] in ("superseded", "deferred"):
        return row["floor_chars"]
    return row["winner_chars"]


def obligations(tree, row):
    """Every (label, need, have) pair this row must satisfy.

    A plain row has one obligation: the merged tree offers at least the winner's
    length. A ``decomposed`` row has TWO, and the second is not optional — the
    suite's exhaustive refutation pass is what proved why. With only the combined
    floor, the split's receivers were bulky enough to pay for a parent that had
    been emptied out: `_dream_health_gate_checks` could be reduced to a single
    character and the nine helpers would still cover the 4854-char floor between
    them, leaving nine functions nobody calls and a gate reporting green. So the
    HOST body carries its own floor, pinned when the split was adjudicated.
    """
    out = []
    lengths = _site_lengths(tree, row["name"])
    have, missing = available_chars(tree, row)
    out.append(("floor", required_chars(row), have))
    if row["adjudication"] == "decomposed":
        out.append(("host-floor", row["floor_chars"],
                    lengths.get(row.get("host"), 0)))
    return out, missing


def check(fixture, tree):
    """Compare a merged-tree capture against the baseline. Pure; no I/O."""
    report = {"checked": len(fixture["rows"]), "missing": [],
              "local_longer": [], "donor_longer": [], "absorber_missing": [],
              "deferred": [], "superseded": []}
    for row in fixture["rows"]:
        name = row["name"]
        if row["adjudication"] == "deferred":
            report["deferred"].append(name)
        elif row["adjudication"] == "superseded":
            report["superseded"].append(name)
        if name not in tree:
            report["missing"].append(name)
            continue
        checks, missing_abs = obligations(tree, row)
        for ref in missing_abs:
            report["absorber_missing"].append("%s -> %s" % (name, ref))
        column = "local_longer" if row["winner"] == "local" else "donor_longer"
        for label, need, have in checks:
            if have < need:
                report[column].append({"name": name, "check": label,
                                       "need": need, "have": have,
                                       "winner": row["winner"],
                                       "adjudication": row["adjudication"]})
    return report


def violations(report):
    return (report["missing"] + report["absorber_missing"]
            + report["local_longer"] + report["donor_longer"])


def load_fixture(path=FIXTURE):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _render(report):
    lines = ["body-differ: %d rows checked" % report["checked"]]
    for column in ("local_longer", "donor_longer"):
        rows = report[column]
        lines.append("  %s: %d" % (column, len(rows)))
        for row in rows:
            lines.append("    %-44s %-10s need %6d  have %6d  (%s)"
                         % (row["name"], row["check"], row["need"], row["have"],
                            row["adjudication"]))
    for column in ("missing", "absorber_missing"):
        if report[column]:
            lines.append("  %s: %s" % (column, ", ".join(report[column])))
    lines.append("  deferred: %s" % (", ".join(report["deferred"]) or "none"))
    lines.append("  superseded: %s" % (", ".join(report["superseded"]) or "none"))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="AST body differ (task 30)")
    ap.add_argument("--write", action="store_true",
                    help="regenerate the baseline (requires --donor)")
    ap.add_argument("--check", action="store_true",
                    help="gate the merged tree against the baseline")
    ap.add_argument("--adjudicate", action="store_true",
                    help="re-apply the adjudication table to the existing "
                         "baseline rows (no donor needed; measurements are "
                         "never touched)")
    ap.add_argument("--donor", help="donor tree root (for --write)")
    ap.add_argument("--root", default=REPO_ROOT, help="merged/local tree root")
    ap.add_argument("--fixture", default=FIXTURE)
    ap.add_argument("--format", choices=("text", "json"), default="text")
    args = ap.parse_args(argv)

    if args.write:
        if not args.donor:
            ap.error("--write requires --donor")
        from _body_adjudication import ADJUDICATIONS
        fixture = build_fixture(args.root, args.donor, ADJUDICATIONS)
        with open(args.fixture, "w", encoding="utf-8") as fh:
            json.dump(fixture, fh, indent=1, ensure_ascii=False, sort_keys=False)
            fh.write("\n")
        print("wrote %s: %s" % (args.fixture, fixture["counts"]))
        return 0

    if args.adjudicate:
        from _body_adjudication import ADJUDICATIONS
        fixture, changed = reapply(load_fixture(args.fixture), ADJUDICATIONS)
        with open(args.fixture, "w", encoding="utf-8") as fh:
            json.dump(fixture, fh, indent=1, ensure_ascii=False, sort_keys=False)
            fh.write("\n")
        print("re-adjudicated %s: %d row(s) changed%s"
              % (args.fixture, len(changed),
                 (" (%s)" % ", ".join(changed)) if changed else ""))
        return 0

    if not args.check:
        ap.error("one of --write / --check / --adjudicate is required")
    fixture = load_fixture(args.fixture)
    report = check(fixture, merged_tree(args.root))
    if args.format == "json":
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(_render(report))
    return 1 if violations(report) else 0


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    sys.exit(main())
