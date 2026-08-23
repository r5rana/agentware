#!/usr/bin/env python3
"""Derive the BINDING union CLI surface from the two per-tree captures.

Feature 260813-modular-core-adoption, task 7.

Why a union and not "the local surface plus a donor checklist"
--------------------------------------------------------------
The migration's contract is that the merged tree is a strict SUPERSET of both
trees. A gate built from one side can only ever catch losses on that side: a
local-only check is blind to the six donor-only verbs and to the sixty-odd
donor-only flags on shared verbs, and a donor-only check is blind to the
hundred-odd local-only flags. Deriving one union fixture from
``surface-local.json`` + ``surface-donor.json`` makes every downstream gate
(the symmetric per-verb promotion gate, the donor-only presence gate, the
parity manifest's generated tier) symmetric BY CONSTRUCTION rather than by
whoever wrote it remembering to check both directions.

Identity
--------
* a NODE is identified by its ``path`` (the subcommand chain from the root).
* an ACTION is identified by ``(dest, option_strings)`` within its node.

``kind``/``required``/``nargs``/``default``/``choices``/``type`` are therefore
COMPARED, never part of identity -- a donor default that moved off the decided
value has to surface as a conflict to adjudicate, not silently become a second
action. Under this identity the union is 124 nodes / 504 actions / 44
top-level verbs, which is what the plan pins.

Conflicts are adjudicated EXPLICITLY
------------------------------------
Where the two trees disagree on a compared field, the deriver refuses to guess:
the divergence must appear in ``ADJUDICATIONS`` below with a decided value and
a rationale, or derivation FAILS. A silent side-pick is exactly the failure
mode this migration is most exposed to -- a module landing with the thinner
body or the foreign literal changes no verb and no count.

Where the rest of it lives (task 99 wave 19)
--------------------------------------------
This file owns DERIVATION: the closed adjudication and exclusion tables, the
merge that consults them, and the writer. The JUDGING half -- ``check_shape``,
``check_covers_sources``, ``check_tables``, ``superset_violations`` and
``check_superset`` -- is ``_surface_union_verify.py``, and the identity
vocabulary both halves read is ``_surface_identity.py``. Every one of those
names is re-exported below, so ``_surface_union.check_tables`` and
``_surface_union.superset_violations`` still resolve for the promotion gate, the
presence gate and their tests: the split changed where the code lives, not what
this module offers.

``check_values`` did NOT move, and that is the seam rather than an exception to
it. It is the one check that reads ``ADJUDICATIONS``, and a table whose reader
lives in another file is the two-writers-disagree shape this migration keeps
finding. It stays beside the table -- and beside the monkeypatch the gate test
uses to empty that table.

Usage:
    python3 tests/_surface_union.py --out tests/fixtures/surface-union.json
    python3 tests/_surface_union.py --verify tests/fixtures/surface-union.json \
        --expect-nodes 124 --expect-actions 504 --expect-verbs 44
    python3 tests/_surface_union.py --check-superset <merged-capture.json>
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    from tests import _surface_identity as _ident
    from tests import _surface_union_verify as _verify
except ImportError:  # allow direct execution and SourceFileLoader import
    sys.path.insert(0, HERE)
    import _surface_identity as _ident  # type: ignore
    import _surface_union_verify as _verify  # type: ignore

# --- the identity vocabulary, re-exported so this module's surface is whole ---
SCHEMA_VERSION = _ident.SCHEMA_VERSION
FIXTURE_DIR = _ident.FIXTURE_DIR
LOCAL_FIXTURE = _ident.LOCAL_FIXTURE
DONOR_FIXTURE = _ident.DONOR_FIXTURE
UNION_FIXTURE = _ident.UNION_FIXTURE
COMPARED_FIELDS = _ident.COMPARED_FIELDS
UNION_EXTRA_FIELDS = _ident.UNION_EXTRA_FIELDS
DISPOSITIONS = _ident.DISPOSITIONS
load_capture_module = _ident.load_capture_module
load_json = _ident.load_json
action_key = _ident.action_key
index_nodes = _ident.index_nodes
sides_of = _ident.sides_of

# --- the judging half, re-exported for every existing caller -----------------
check_shape = _verify.check_shape
check_covers_sources = _verify.check_covers_sources
check_tables = _verify.check_tables
superset_violations = _verify.superset_violations
check_superset = _verify.check_superset


# --------------------------------------------------------------------------
# adjudications -- the CLOSED table of decided divergences
# --------------------------------------------------------------------------
# Every field on which the two trees disagree MUST appear here. A divergence
# absent from this table is a hard derivation error, never a silent side-pick.

ADJUDICATIONS = (
    {
        "node": ["recall"],
        "dest": "scope",
        "option_strings": ["--scope"],
        "field": "choices",
        "decided": ["curated", "work", "all", "shared"],
        "winner": "donor",
        "rationale": (
            "Superset union. The donor's extra 'shared' scope is the org/team "
            "corpus selector this plan adopts wholesale (tasks 50, 63-66); "
            "keeping the local three-value set would delete a scope the merged "
            "tree is required to serve. A choices SUPERSET is additive -- every "
            "value the local tree accepts is still accepted."
        ),
    },
    {
        "node": ["recall"],
        "dest": "token_budget",
        "option_strings": ["--token-budget"],
        "field": "default",
        "decided": 1500,
        "winner": "local",
        "rationale": (
            "Plan decision, 'Seven unions that are logically impossible': 1500, "
            "preserving the committed R-RET-03 / R-RET-04 retrieval ladder "
            "including the stage-2 escalation to 3000 that the donor's 10000 "
            "default collapses -- one budget that large makes stage 1 and "
            "stage 2 indistinguishable and silently retires the escalation."
        ),
    },
)

# --------------------------------------------------------------------------
# exclusions -- union members deliberately NOT required of the merged tree
# --------------------------------------------------------------------------
# These still COUNT toward the union (the plan's 504 is the mechanical union),
# but carry disposition "excluded" so the promotion gate does not hard-require
# a capability the plan deferred. Each mirrors a parity-manifest excluded row.

EXCLUSIONS = (
    {
        "node": ["recall"],
        "dest": "all_teams",
        "option_strings": ["--all-teams"],
        "reason": (
            "Plan decision: fleet-wide recall needs either an org-root-spanning "
            "index rebuild or an N-index merge whose BM25 scores are not "
            "comparable across shards, so the flag cannot be honoured correctly "
            "by simply carrying it across."
        ),
        "approver": "operator (plan-approved self-extension, R-PKG-03)",
        "follow_up": "named follow-up plan: recall --all-teams",
    },
)


# --------------------------------------------------------------------------
# the two readers of the closed tables above
# --------------------------------------------------------------------------

def adjudication_for(path, key, field):
    for row in ADJUDICATIONS:
        if (tuple(row["node"]) == tuple(path)
                and row["dest"] == key[0]
                and tuple(row["option_strings"]) == key[1]
                and row["field"] == field):
            return row
    return None


def exclusion_for(path, key):
    for row in EXCLUSIONS:
        if (tuple(row["node"]) == tuple(path)
                and row["dest"] == key[0]
                and tuple(row["option_strings"]) == key[1]):
            return row
    return None


# --------------------------------------------------------------------------
# derivation
# --------------------------------------------------------------------------

def merge_action(path, key, local_action, donor_action, fields):
    """One union action: agreed values, or the adjudicated decision."""
    record = {}
    problems = []
    for field in fields:
        if local_action is None:
            record[field] = donor_action[field]
            continue
        if donor_action is None:
            record[field] = local_action[field]
            continue
        if local_action[field] == donor_action[field]:
            record[field] = local_action[field]
            continue
        row = adjudication_for(path, key, field)
        if row is None:
            problems.append(
                "/".join(path) + f" {key[0]} {list(key[1])}: field {field!r} "
                f"diverges ({local_action[field]!r} local vs "
                f"{donor_action[field]!r} donor) with no adjudication")
            record[field] = local_action[field]
        else:
            record[field] = row["decided"]
    record["dest"], record["option_strings"] = key[0], list(key[1])
    record["sides"] = sides_of(local_action is not None, donor_action is not None)
    record["disposition"] = (
        "excluded" if exclusion_for(path, key) else "required")
    return record, problems


def merge_node(path, ln, dn, fields):
    """ONE union node from the two sides' nodes; either side may be absent.

    Action order is load-bearing and stated once, here -- local order first,
    then the donor-only actions in donor order -- so re-deriving an unchanged
    pair of captures rewrites the fixture byte-for-byte.
    """
    problems = []
    la = {action_key(a): a for a in (ln or {}).get("actions", [])}
    da = {action_key(a): a for a in (dn or {}).get("actions", [])}
    order = list(la) + [k for k in da if k not in la]

    actions = []
    for key in order:
        record, errs = merge_action(path, key, la.get(key), da.get(key), fields)
        actions.append(record)
        problems.extend(errs)

    subs = set((ln or {}).get("subcommands", []))
    subs |= set((dn or {}).get("subcommands", []))
    dests = {n["subcommand_dest"] for n in (ln, dn) if n}
    dests.discard(None)
    if len(dests) > 1:
        problems.append("/".join(path) + f": subcommand_dest diverges {dests}")

    node = {
        "path": list(path),
        "depth": len(path),
        "sides": sides_of(ln is not None, dn is not None),
        "subcommand_dest": sorted(dests)[0] if dests else None,
        "subcommands": sorted(subs),
        "actions": actions,
    }
    return node, problems


def union_header(local, donor, local_path, donor_path, nodes, li, di):
    """Everything the fixture carries ABOUT the merged nodes.

    Split out of ``derive`` because it decides nothing: every value here is a
    count, a set difference or a projection of what the merge already produced,
    and it was the bulk of the body a reader had to scroll past to find the
    merge itself.
    """
    lv, dv = set(local["top_level_verbs"]), set(donor["top_level_verbs"])
    root = next(n for n in nodes if not n["path"])
    return {
        "schema_version": SCHEMA_VERSION,
        "label": "union",
        "binding": True,
        "derived_from": [
            {"label": side["label"], "git_head": side.get("git_head"),
             "fixture": os.path.relpath(p, os.path.dirname(HERE)),
             "counts": side["counts"]}
            for side, p in ((local, local_path), (donor, donor_path))
        ],
        "counting_convention": dict(
            local["counting_convention"],
            node_identity="path (the subcommand chain from the root)",
            action_identity=["dest", "option_strings"],
            compared_fields=list(COMPARED_FIELDS),
        ),
        "counts": {
            "nodes": len(nodes),
            "actions": sum(len(n["actions"]) for n in nodes),
            "top_level_verbs": len(root["subcommands"]),
        },
        "sides": {
            "nodes": {
                "shared": len(set(li) & set(di)),
                "local_only": len(set(li) - set(di)),
                "donor_only": len(set(di) - set(li)),
            },
            "actions": {
                kind: sum(1 for n in nodes for a in n["actions"]
                          if a["sides"] == want)
                for kind, want in (("shared", "both"), ("local_only", "local"),
                                   ("donor_only", "donor"))
            },
            "top_level_verbs": {
                "shared": len(lv & dv),
                "local_only": len(lv - dv),
                "donor_only": len(dv - lv),
            },
        },
        "top_level_verbs": root["subcommands"],
        "donor_only_verbs": sorted(dv - lv),
        "local_only_verbs": sorted(lv - dv),
        "adjudications": [dict(r) for r in ADJUDICATIONS],
        "exclusions": [dict(r) for r in EXCLUSIONS],
        "nodes": nodes,
    }


def derive(local_path=LOCAL_FIXTURE, donor_path=DONOR_FIXTURE):
    """Build the union fixture from the two per-tree captures."""
    capture = load_capture_module()
    fields = tuple(capture.ACTION_FIELDS)
    local, donor = load_json(local_path), load_json(donor_path)
    li, di = index_nodes(local), index_nodes(donor)

    problems = []
    nodes = []
    # Deterministic order: by depth then path, so the file diffs readably.
    for path in sorted(set(li) | set(di), key=lambda p: (len(p), p)):
        node, errs = merge_node(path, li.get(path), di.get(path), fields)
        nodes.append(node)
        problems.extend(errs)

    if problems:
        raise SystemExit("\n".join(f"[FAIL] {p}" for p in problems))

    return union_header(local, donor, local_path, donor_path, nodes, li, di)


# --------------------------------------------------------------------------
# verification
#
# ``check_values`` is the ONE check that reads ADJUDICATIONS, so it lives here
# beside the table. Its four siblings are in ``_surface_union_verify`` and are
# re-exported at the top of this module.
# --------------------------------------------------------------------------

def _field_value_problems(where, act, lact, dact, path, key, capture):
    """Recompute every compared field of ONE action from the two sources."""
    problems = []
    for field in capture.ACTION_FIELDS:
        if field in ("dest", "option_strings"):
            continue
        if lact is None or dact is None:
            want = (dact or lact)[field]
            if act[field] != want:
                problems.append(
                    f"{where}: {field} {act[field]!r} != source {want!r}")
            continue
        if lact[field] == dact[field]:
            if act[field] != lact[field]:
                problems.append(
                    f"{where}: {field} {act[field]!r} != agreed "
                    f"{lact[field]!r}")
            continue
        row = adjudication_for(path, key, field)
        if row is None:
            problems.append(f"{where}: {field} diverges, no adjudication")
        elif act[field] != row["decided"]:
            problems.append(
                f"{where}: {field} {act[field]!r} != decided "
                f"{row['decided']!r}")
    return problems


def check_values(fixture, local, donor, capture):
    """Recompute every value from the sources; no unadjudicated side-pick."""
    problems = []
    li, di = index_nodes(local), index_nodes(donor)
    for node in fixture["nodes"]:
        path = tuple(node["path"])
        la = {action_key(a): a for a in li.get(path, {}).get("actions", [])}
        da = {action_key(a): a for a in di.get(path, {}).get("actions", [])}
        for act in node["actions"]:
            key = action_key(act)
            lact, dact = la.get(key), da.get(key)
            where = ("/".join(path) or "<root>") + f" {key[0]} {list(key[1])}"
            if act["sides"] != sides_of(lact is not None, dact is not None):
                problems.append(f"{where}: sides {act['sides']!r} is wrong")
            problems.extend(_field_value_problems(
                where, act, lact, dact, path, key, capture))
    return problems


def verify(path=UNION_FIXTURE, local_path=LOCAL_FIXTURE, donor_path=DONOR_FIXTURE,
           expect=None):
    capture = load_capture_module()
    fixture = load_json(path)
    local, donor = load_json(local_path), load_json(donor_path)

    problems = check_shape(fixture, capture, expect)
    problems += check_covers_sources(fixture, local, donor)
    problems += check_values(fixture, local, donor, capture)
    problems += check_tables(fixture)

    for line in problems:
        print(f"[FAIL] {line}")
    if problems:
        return 1
    counts = fixture["counts"]
    print(f"[OK] {path}: {counts['nodes']} nodes, {counts['actions']} actions, "
          f"{counts['top_level_verbs']} top-level verbs; covers every local and "
          "donor node and action")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="_surface_union",
        description="derive or verify the binding union CLI surface fixture")
    ap.add_argument("--local", default=LOCAL_FIXTURE)
    ap.add_argument("--donor", default=DONOR_FIXTURE)
    ap.add_argument("--out", help="union fixture path to write")
    ap.add_argument("--verify", metavar="FIXTURE", nargs="?", const=UNION_FIXTURE)
    ap.add_argument("--check-superset", metavar="CAPTURE",
                    help="assert a merged-tree capture covers the union")
    ap.add_argument("--union", default=UNION_FIXTURE,
                    help="union fixture used by --check-superset")
    ap.add_argument("--expect-nodes", type=int, default=None)
    ap.add_argument("--expect-actions", type=int, default=None)
    ap.add_argument("--expect-verbs", type=int, default=None)
    args = ap.parse_args(argv)

    expect = {"nodes": args.expect_nodes, "actions": args.expect_actions,
              "top_level_verbs": args.expect_verbs}

    if args.check_superset:
        return check_superset(args.check_superset, args.union)
    if args.verify:
        return verify(args.verify, args.local, args.donor, expect)

    if not args.out:
        ap.error("--out is required when deriving")
    fixture = derive(args.local, args.donor)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(fixture, fh, indent=2, sort_keys=False)
        fh.write("\n")
    counts = fixture["counts"]
    print(f"wrote {args.out}: {counts['nodes']} nodes, {counts['actions']} "
          f"actions, {counts['top_level_verbs']} top-level verbs "
          f"({fixture['sides']['actions']['local_only']} local-only, "
          f"{fixture['sides']['actions']['donor_only']} donor-only, "
          f"{fixture['sides']['actions']['shared']} shared)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
