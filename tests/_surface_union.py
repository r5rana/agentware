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

Usage:
    python3 tests/_surface_union.py --out tests/fixtures/surface-union.json
    python3 tests/_surface_union.py --verify tests/fixtures/surface-union.json \
        --expect-nodes 124 --expect-actions 504 --expect-verbs 44
    python3 tests/_surface_union.py --check-superset <merged-capture.json>
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from importlib.machinery import SourceFileLoader

SCHEMA_VERSION = 1

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_DIR = os.path.join(HERE, "fixtures")
LOCAL_FIXTURE = os.path.join(FIXTURE_DIR, "surface-local.json")
DONOR_FIXTURE = os.path.join(FIXTURE_DIR, "surface-donor.json")
UNION_FIXTURE = os.path.join(FIXTURE_DIR, "surface-union.json")

# Fields compared between the two sides. Identity is (dest, option_strings).
COMPARED_FIELDS = ("kind", "required", "nargs", "default", "choices", "type")

# Fields a union action carries beyond the capture's eight.
UNION_EXTRA_FIELDS = ("sides", "disposition")

DISPOSITIONS = ("required", "excluded")


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
# helpers
# --------------------------------------------------------------------------

def load_capture_module():
    """Import ``tests/_surface_capture.py`` for its shared invariants.

    The union reuses the capture's ACTION_FIELDS, STRUCTURAL_ACTIONS and
    prose scanner so the "object dump, no rendered help" contract has ONE
    definition rather than a second drifting copy.
    """
    path = os.path.join(HERE, "_surface_capture.py")
    loader = SourceFileLoader("aw_surface_capture", path)
    spec = importlib.util.spec_from_loader("aw_surface_capture", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def action_key(action):
    """Identity of an action within its node."""
    return (action["dest"], tuple(action["option_strings"]))


def index_nodes(fixture):
    return {tuple(n["path"]): n for n in fixture["nodes"]}


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


def sides_of(in_local, in_donor):
    if in_local and in_donor:
        return "both"
    return "local" if in_local else "donor"


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
        ln, dn = li.get(path), di.get(path)
        la = {action_key(a): a for a in (ln or {}).get("actions", [])}
        da = {action_key(a): a for a in (dn or {}).get("actions", [])}
        # Local order first, then donor-only actions in donor order.
        order = list(la) + [k for k in da if k not in la]

        actions = []
        for key in order:
            record, errs = merge_action(
                path, key, la.get(key), da.get(key), fields)
            actions.append(record)
            problems.extend(errs)

        subs = set((ln or {}).get("subcommands", []))
        subs |= set((dn or {}).get("subcommands", []))
        dests = {n["subcommand_dest"] for n in (ln, dn) if n}
        dests.discard(None)
        if len(dests) > 1:
            problems.append("/".join(path) + f": subcommand_dest diverges {dests}")

        nodes.append({
            "path": list(path),
            "depth": len(path),
            "sides": sides_of(ln is not None, dn is not None),
            "subcommand_dest": sorted(dests)[0] if dests else None,
            "subcommands": sorted(subs),
            "actions": actions,
        })

    if problems:
        raise SystemExit("\n".join(f"[FAIL] {p}" for p in problems))

    lv, dv = set(local["top_level_verbs"]), set(donor["top_level_verbs"])
    root = next(n for n in nodes if not n["path"])

    fixture = {
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
    return fixture


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

def check_shape(fixture, capture, expect=None):
    """Internal consistency: a hand-edited header cannot contradict the body."""
    problems = []
    nodes = fixture.get("nodes", [])
    by_path = {tuple(n["path"]): n for n in nodes}
    counts = fixture.get("counts", {})
    real = {
        "nodes": len(nodes),
        "actions": sum(len(n["actions"]) for n in nodes),
        "top_level_verbs": len(fixture.get("top_level_verbs", [])),
    }
    for name, value in real.items():
        if counts.get(name) != value:
            problems.append(f"counts.{name} {counts.get(name)} != {value} recorded")
        if expect and expect.get(name) is not None and value != expect[name]:
            problems.append(f"{name} {value} != expected {expect[name]}")

    if () not in by_path:
        problems.append("no root node")
    for path, node in by_path.items():
        if not path:
            continue
        parent = by_path.get(path[:-1])
        if parent is None:
            problems.append(f"orphan node {'/'.join(path)}")
        elif path[-1] not in parent["subcommands"]:
            problems.append(f"{'/'.join(path)} not declared by its parent")

    want_fields = set(capture.ACTION_FIELDS) | set(UNION_EXTRA_FIELDS)
    structural = {c.__name__ for c in capture.STRUCTURAL_ACTIONS}
    for node in nodes:
        seen = set()
        for act in node["actions"]:
            where = "/".join(node["path"]) or "<root>"
            if set(act) != want_fields:
                problems.append(f"{where}: action field set is {sorted(act)}")
                continue
            if act["kind"] in structural:
                problems.append(f"{where}: structural action {act['kind']}")
            if act["disposition"] not in DISPOSITIONS:
                problems.append(f"{where}: bad disposition {act['disposition']!r}")
            if act["sides"] not in ("both", "local", "donor"):
                problems.append(f"{where}: bad sides {act['sides']!r}")
            key = action_key(act)
            if key in seen:
                problems.append(f"{where}: duplicate action {key}")
            seen.add(key)

    problems.extend(capture.scan_for_prose(fixture))
    return problems


def check_covers_sources(fixture, local, donor):
    """EVERY local node/action and EVERY donor node/action is present."""
    problems = []
    by_path = {tuple(n["path"]): n for n in fixture["nodes"]}
    for label, source in (("local", local), ("donor", donor)):
        for node in source["nodes"]:
            path = tuple(node["path"])
            target = by_path.get(path)
            if target is None:
                problems.append(f"{label}: node {'/'.join(path) or '<root>'} missing")
                continue
            have = {action_key(a) for a in target["actions"]}
            for act in node["actions"]:
                if action_key(act) not in have:
                    problems.append(
                        f"{label}: {'/'.join(path) or '<root>'} action "
                        f"{act['dest']} {act['option_strings']} missing")
            for sub in node["subcommands"]:
                if sub not in target["subcommands"]:
                    problems.append(
                        f"{label}: {'/'.join(path) or '<root>'} subcommand "
                        f"{sub} missing")
        for verb in source["top_level_verbs"]:
            if verb not in fixture["top_level_verbs"]:
                problems.append(f"{label}: top-level verb {verb} missing")
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


def check_tables(fixture):
    """Every adjudication and exclusion resolves to a real union action."""
    problems = []
    by_path = {tuple(n["path"]): n for n in fixture["nodes"]}

    def find(row):
        node = by_path.get(tuple(row["node"]))
        if node is None:
            return None
        key = (row["dest"], tuple(row["option_strings"]))
        return next((a for a in node["actions"] if action_key(a) == key), None)

    for row in fixture.get("adjudications", []):
        act = find(row)
        if act is None:
            problems.append(f"adjudication {row['dest']}.{row['field']}: no such action")
        elif act[row["field"]] != row["decided"]:
            problems.append(
                f"adjudication {row['dest']}.{row['field']}: fixture carries "
                f"{act[row['field']]!r}, decided {row['decided']!r}")
        for need in ("rationale", "winner"):
            if not row.get(need):
                problems.append(f"adjudication {row['dest']}.{row['field']}: no {need}")

    excluded_keys = set()
    for row in fixture.get("exclusions", []):
        act = find(row)
        if act is None:
            problems.append(f"exclusion {row['dest']}: no such action")
            continue
        excluded_keys.add((tuple(row["node"]), action_key(act)))
        if act["disposition"] != "excluded":
            problems.append(f"exclusion {row['dest']}: disposition is "
                            f"{act['disposition']!r}")
        for need in ("reason", "approver", "follow_up"):
            if not row.get(need):
                problems.append(f"exclusion {row['dest']}: no {need}")

    for node in fixture["nodes"]:
        for act in node["actions"]:
            if (act["disposition"] == "excluded"
                    and (tuple(node["path"]), action_key(act)) not in excluded_keys):
                problems.append(
                    f"{'/'.join(node['path'])} {act['dest']}: excluded with no "
                    "exclusion row")
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


# --------------------------------------------------------------------------
# the downstream gate primitive: is a merged capture a superset of the union?
# --------------------------------------------------------------------------

def superset_violations(merged, union):
    """Every way a merged-tree capture FAILS to cover the binding union.

    Compares on the union's ``compared_fields`` -- kind, required, nargs,
    default, choices, type -- not on flag names, so a narrowed ``choices``, a
    moved ``default`` or a dropped ``required`` is a violation even though the
    flag is still spelled the same. ``disposition: excluded`` members are not
    required (they mirror the parity manifest's excluded rows) but are still
    reported when present so nothing is invisible.
    """
    violations = []
    have = {tuple(n["path"]): n for n in merged["nodes"]}
    for node in union["nodes"]:
        path = tuple(node["path"])
        where = "/".join(path) or "<root>"
        target = have.get(path)
        required = [a for a in node["actions"] if a["disposition"] == "required"]
        if target is None:
            if required or node["subcommands"]:
                violations.append(f"missing node {where}")
            continue
        mine = {action_key(a): a for a in target["actions"]}
        for act in required:
            key = action_key(act)
            got = mine.get(key)
            if got is None:
                violations.append(f"{where}: missing {act['dest']} "
                                  f"{act['option_strings']}")
                continue
            for field in COMPARED_FIELDS:
                want, real = act[field], got[field]
                if field == "choices" and want is not None:
                    if real is None or not set(want) <= set(real):
                        violations.append(
                            f"{where}: {act['dest']} choices {real!r} does not "
                            f"cover {want!r}")
                    continue
                if want != real:
                    violations.append(
                        f"{where}: {act['dest']} {field} {real!r} != {want!r}")
        for sub in node["subcommands"]:
            if sub not in target["subcommands"]:
                violations.append(f"{where}: missing subcommand {sub}")
    return violations


def check_superset(merged_path, union_path=UNION_FIXTURE):
    merged, union = load_json(merged_path), load_json(union_path)
    violations = superset_violations(merged, union)
    for line in violations:
        print(f"[FAIL] {line}")
    if violations:
        print(f"[FAIL] {merged_path}: {len(violations)} union surface violations")
        return 1
    print(f"[OK] {merged_path} covers the binding union "
          f"({union['counts']['nodes']} nodes, {union['counts']['actions']} actions)")
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
