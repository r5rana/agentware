#!/usr/bin/env python3
"""JUDGE a fixture or a merged capture against the binding union surface.

Feature 260813-modular-core-adoption, task 99 wave 19.

The sibling of `tests/_surface_union.py`, split off at the seam the file already
stated in its own section comments: the owner DERIVES the union (the closed
adjudication table, the merge, the writer), and this module JUDGES against it.
Three of the five function ceiling rows wave 19 pays down lived on this side
(`check_shape` at 18 branches, `superset_violations` at 16, `check_tables` at
14), and the sixth row — the file's own 618 lines against a 600 cap — lived on
the sum of the two obligations sitting in one place.

WHY THE SEAM IS HERE AND NOT ELSEWHERE. The judging half needs nothing the owner
decides. Every check below reads the fixture in front of it plus the identity
vocabulary in `_surface_identity`; not one of them consults `ADJUDICATIONS` or
`EXCLUSIONS`. The one check that DOES — `check_values`, which recomputes every
value from the two sources and needs the decided value for a divergence — stayed
in the owner deliberately, beside the table it reads and beside the monkeypatch
the gate test uses to empty that table. Splitting a table from its reader is the
two-writers-disagree shape this migration keeps finding; splitting a reader that
never touches the table is free.

WHY EACH BODY IS NOW SEVERAL. The four wide bodies were wide for one reason
stated four times: each is a REPORT-EVERY-PROBLEM walk that appends one message
per distinct defect and keeps going, so its branch count is the CHECK count, not
the depth of any decision. That makes the count irreducible in aggregate and
completely reducible per check — each check is independent of its neighbours,
reads its own slice of the fixture and appends its own message. So each walk now
names its checks as helpers and the parent is the ORDER in which they run, which
is the only thing the parent ever decided. The order is load-bearing and
preserved exactly: the captured problem list is compared byte-for-byte before and
after, and a reordering would show up as a diff.

Nothing here raises. Every function returns a list of problem strings, empty
when clean, because the callers (`verify`, `check_superset`, the gate tests, and
the CI workflow) all report the whole list rather than the first line.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    from tests import _surface_identity as _ident
except ImportError:  # allow direct execution and SourceFileLoader import
    sys.path.insert(0, HERE)
    import _surface_identity as _ident  # type: ignore

COMPARED_FIELDS = _ident.COMPARED_FIELDS
UNION_EXTRA_FIELDS = _ident.UNION_EXTRA_FIELDS
DISPOSITIONS = _ident.DISPOSITIONS
UNION_FIXTURE = _ident.UNION_FIXTURE
action_key = _ident.action_key
load_json = _ident.load_json


# --------------------------------------------------------------------------
# check_shape -- a hand-edited header cannot contradict the body
# --------------------------------------------------------------------------

def _count_problems(fixture, nodes, expect):
    """The recorded `counts` header against the body, then against `expect`."""
    problems = []
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
    return problems


def _tree_problems(by_path):
    """The node tree is connected: a root, no orphan, no undeclared child."""
    problems = []
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
    return problems


def _node_action_problems(node, want_fields, structural):
    """Every per-action shape check for ONE node, in the recorded order."""
    problems = []
    seen = set()
    where = "/".join(node["path"]) or "<root>"
    for act in node["actions"]:
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
    return problems


def check_shape(fixture, capture, expect=None):
    """Internal consistency: a hand-edited header cannot contradict the body."""
    nodes = fixture.get("nodes", [])
    by_path = {tuple(n["path"]): n for n in nodes}
    want_fields = set(capture.ACTION_FIELDS) | set(UNION_EXTRA_FIELDS)
    structural = {c.__name__ for c in capture.STRUCTURAL_ACTIONS}

    problems = _count_problems(fixture, nodes, expect)
    problems.extend(_tree_problems(by_path))
    for node in nodes:
        problems.extend(_node_action_problems(node, want_fields, structural))
    problems.extend(capture.scan_for_prose(fixture))
    return problems


# --------------------------------------------------------------------------
# check_covers_sources -- nothing either side had may be missing
# --------------------------------------------------------------------------

def _covers_node_problems(label, node, target):
    """Every action and subcommand ONE source node contributes is present."""
    problems = []
    path = tuple(node["path"])
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
            problems.extend(_covers_node_problems(label, node, target))
        for verb in source["top_level_verbs"]:
            if verb not in fixture["top_level_verbs"]:
                problems.append(f"{label}: top-level verb {verb} missing")
    return problems


# --------------------------------------------------------------------------
# check_tables -- every adjudication and exclusion resolves to a real action
# --------------------------------------------------------------------------

def _table_finder(fixture):
    """Resolve a table row's (node, dest, option_strings) to a union action."""
    by_path = {tuple(n["path"]): n for n in fixture["nodes"]}

    def find(row):
        node = by_path.get(tuple(row["node"]))
        if node is None:
            return None
        key = (row["dest"], tuple(row["option_strings"]))
        return next((a for a in node["actions"] if action_key(a) == key), None)

    return find


def _adjudication_problems(fixture, find):
    """Each adjudication names a real action, whose value it decided, with why."""
    problems = []
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
    return problems


def _exclusion_problems(fixture, find):
    """Each exclusion names a real, excluded action with its three obligations.

    Returns `(problems, excluded_keys)` -- the key set is what the orphan scan
    below needs, and deriving it twice is how the two halves would disagree.
    """
    problems = []
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
    return problems, excluded_keys


def _orphan_exclusion_problems(fixture, excluded_keys):
    """No action may carry `excluded` without an exclusion row behind it."""
    problems = []
    for node in fixture["nodes"]:
        for act in node["actions"]:
            if (act["disposition"] == "excluded"
                    and (tuple(node["path"]), action_key(act)) not in excluded_keys):
                problems.append(
                    f"{'/'.join(node['path'])} {act['dest']}: excluded with no "
                    "exclusion row")
    return problems


def check_tables(fixture):
    """Every adjudication and exclusion resolves to a real union action."""
    find = _table_finder(fixture)
    problems = _adjudication_problems(fixture, find)
    exclusion_problems, excluded_keys = _exclusion_problems(fixture, find)
    problems.extend(exclusion_problems)
    problems.extend(_orphan_exclusion_problems(fixture, excluded_keys))
    return problems


# --------------------------------------------------------------------------
# the downstream gate primitive: is a merged capture a superset of the union?
# --------------------------------------------------------------------------

def _field_violations(where, act, got):
    """Every compared field on which a merged action falls short of the union.

    `choices` is compared as a SUPERSET rather than for equality -- a merged tree
    may accept more values than the union requires, but never fewer -- which is
    why it is the one field with its own arm.
    """
    violations = []
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
    return violations


def _node_violations(node, target, required):
    """Every way ONE merged node fails to cover its union counterpart."""
    violations = []
    where = "/".join(node["path"]) or "<root>"
    mine = {action_key(a): a for a in target["actions"]}
    for act in required:
        key = action_key(act)
        got = mine.get(key)
        if got is None:
            violations.append(f"{where}: missing {act['dest']} "
                              f"{act['option_strings']}")
            continue
        violations.extend(_field_violations(where, act, got))
    for sub in node["subcommands"]:
        if sub not in target["subcommands"]:
            violations.append(f"{where}: missing subcommand {sub}")
    return violations


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
        target = have.get(path)
        required = [a for a in node["actions"] if a["disposition"] == "required"]
        if target is None:
            if required or node["subcommands"]:
                violations.append(f"missing node {'/'.join(path) or '<root>'}")
            continue
        violations.extend(_node_violations(node, target, required))
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
