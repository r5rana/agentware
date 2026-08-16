#!/usr/bin/env python3
"""The SYMMETRIC PER-VERB PROMOTION GATE (plan task 32, gate 2 of 3).

Feature 260813-modular-core-adoption.

The question this answers
-------------------------
"May verb V be appended to ``agentware_lib.strangler.PORTED_VERBS``?" -- and,
once it has been, "does it still cover what it promised?" Tasks 37..54 each end
with *append the verb*, and this is the check that stands between a modular
implementation and that append.

SYMMETRIC, and why that word is load-bearing
--------------------------------------------
The expectation is the BINDING UNION (``tests/fixtures/surface-union.json``),
never one tree's own surface. A local-side expectation is blind to the sixty-odd
donor-only flags on shared verbs and to all six donor-only verbs; a donor-side
expectation is blind to the two-hundred-odd local-only flags. Both directions
have to be a loss the gate can see, and the union is the only artifact in the
tree that carries both.

The comparison is likewise on VALUES, not on flag names. Task 30 and task 31
between them established that this migration's characteristic failure is the
thing that changes no name: a module lands with the thinner body, the foreign
literal, the older default. A gate that only diffs option strings passes a
``--scope`` whose ``choices`` were narrowed from four values to three, a
``--token-budget`` whose default slid from the decided 1500 to the donor's
10000, and a positional that stopped being ``required``. So every action is
compared on ``kind``, ``required``, ``nargs``, ``default``, ``choices`` and
``type`` -- the union fixture's own ``compared_fields`` -- via the comparator
``_surface_union.superset_violations`` already owns. ``choices`` is compared as
a SUPERSET (a merged tree may accept more), everything else exactly.

Two arms, because the tree has two halves right now
---------------------------------------------------
Legacy-default routing (task 29) means a verb is served by ONE of two parsers,
and the gate checks whichever one actually serves it:

  * PORTED verb -> served by ``agentware_lib.cli.build_parser()``; expectation
    is the FULL union subtree. This is the promotion contract.

  * UNPORTED verb that exists locally -> served by the monolith through the
    facade; expectation is that verb's subtree in ``surface-local.json``, i.e.
    exactly what ``aw-pre-merge`` served. This arm is what keeps the gate from
    being vacuous while the allowlist is empty, and it is NOT implied by task
    25's byte-identity check on ``agentware_legacy.py``: the facade EXECS that
    file and then rebinds symbols into the resulting namespace (task 26's
    layout-aware A/B extraction does exactly that), so the file can be
    byte-identical while the effective parser is not. This arm captures the
    parser the CLI really builds.

  * DONOR-ONLY verb -> can only ever be served by the lib, so it is compared
    against the FULL union subtree exactly as a ported verb is, whether or not
    it is in the allowlist yet. The first draft of this gate returned ``[]`` for
    that arm and left the six verbs entirely to the presence gate; that made the
    task's own acceptance criterion ("deleting a team flag reddens the promotion
    gate naming it") false of the shipped binary, since ``team`` is donor-only.
    A vacuous arm is not a division of labour. The PRESENCE gate
    (``tests/_presence_gate.py``) still owns what this comparison cannot express
    -- absence framed as absence, the stub shape, and routing -- but the flags
    are checked here too.

Usage:
    python3 tests/_promotion_gate.py                    # gate the whole surface
    python3 tests/_promotion_gate.py --verb team --readiness
    python3 tests/_promotion_gate.py --verb recall --readiness

``--readiness`` asks the promotion question about a verb that is NOT yet in the
allowlist: does the LIB already cover the union for it? It reports, and exits
non-zero when the answer is no, so a port can be checked before the append
rather than after.

Stdlib only. Builds both parsers in-process; no subprocess, no I/O beyond the
two fixtures.
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

try:
    from tests import _surface_capture as capture
    from tests import _surface_union as union_mod
except ImportError:  # allow direct execution / `python3 tests/_promotion_gate.py`
    sys.path.insert(0, HERE)
    import _surface_capture as capture  # type: ignore
    import _surface_union as union_mod  # type: ignore

UNION_FIXTURE = union_mod.UNION_FIXTURE
LOCAL_FIXTURE = union_mod.LOCAL_FIXTURE

PORTED = "ported"
UNPORTED = "unported"
DONOR_ONLY = "donor-only"


# --------------------------------------------------------------------------
# captures
# --------------------------------------------------------------------------

def _walk(parser):
    nodes = []
    capture.walk(parser, [], nodes)
    return nodes


def capture_lib():
    """Nodes of the MODULAR parser -- the tree a ported verb is served by."""
    scripts = os.path.join(REPO_ROOT, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from agentware_lib import cli  # noqa: PLC0415 - deliberate late import
    return _walk(cli.build_parser())


def capture_effective():
    """Nodes of the parser the ENTRYPOINT really builds, facade wiring included.

    Deliberately not ``ast``-read off ``agentware_legacy.py``: the facade execs
    that file and then rebinds names in the resulting namespace, so only the
    live parser object shows what the CLI will actually accept.
    """
    try:
        from tests._fixtures import load_cli  # noqa: PLC0415
    except ImportError:
        sys.path.insert(0, REPO_ROOT)
        from tests._fixtures import load_cli  # noqa: PLC0415
    return _walk(load_cli().build_parser())


def ported_verbs():
    scripts = os.path.join(REPO_ROOT, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from agentware_lib import strangler  # noqa: PLC0415
    return set(strangler.PORTED_VERBS)


# --------------------------------------------------------------------------
# expectations
# --------------------------------------------------------------------------

def subtree(nodes, verb):
    """Every node at or under ``verb`` (the root node is never a verb)."""
    return [n for n in nodes if list(n["path"])[:1] == [verb]]


def as_expectation(nodes):
    """Give capture-shaped nodes the ``disposition`` the comparator reads.

    ``surface-local.json`` is a raw capture: its actions carry the eight capture
    fields and no ``disposition``. The comparator treats a missing disposition
    as absent rather than as ``required``, which would silently require nothing
    -- so it is materialised here, explicitly, as ``required``. Copies rather
    than mutating: the caller's fixture is shared with other checks.
    """
    out = []
    for node in nodes:
        actions = [dict(a, disposition=a.get("disposition", "required"))
                   for a in node["actions"]]
        out.append(dict(node, actions=actions))
    return out


def missing_nodes(candidate, expected):
    """Expected node paths with no counterpart, INCLUDING the empty ones.

    ``superset_violations`` reports a missing node only when that node has a
    required action or a subcommand of its OWN. Two union nodes have neither --
    ``bench scorecard`` and ``mcp serve`` -- so for those the only thing that
    catches a deletion is the PARENT's ``subcommands`` list still naming them.
    That works for a capture walked off a live parser, where the two always move
    together, and it is one indirection more than the claim deserves everywhere
    else: it makes "this node is present" a fact inferred from the parent's
    bookkeeping rather than a fact checked. Asserted here outright, so the two
    paths are independent, rather than by widening task 7's committed comparator
    underneath its own gate.
    """
    have = {tuple(n["path"]) for n in candidate}
    return [f"missing node {'/'.join(n['path'])}" for n in expected
            if tuple(n["path"]) not in have]


def dest_violations(candidate, expected):
    """Nodes whose SUBPARSERS dest was renamed.

    The union fixture carries ``subcommand_dest`` and its deriver treats a
    divergence as a hard error, but no comparator reads it: it is node-level and
    ``superset_violations`` compares actions plus subcommand membership. So a
    modular ``plan`` that names its subparsers ``plan_cmd`` instead of
    ``plan_command`` parses byte-identically -- same verbs, same flags, same
    defaults -- and then every ``args.plan_command`` read raises AttributeError
    at dispatch. That is precisely the "changes no flag name" class this gate
    exists for, so it is compared here.
    """
    have = {tuple(n["path"]): n for n in candidate}
    out = []
    for node in expected:
        got = have.get(tuple(node["path"]))
        if got is None or node.get("subcommand_dest") is None:
            continue
        if got.get("subcommand_dest") != node["subcommand_dest"]:
            out.append(f"{'/'.join(node['path']) or '<root>'}: subcommand dest "
                       f"{got.get('subcommand_dest')!r} != "
                       f"{node['subcommand_dest']!r}")
    return out


def promotion_violations(verb, candidate_nodes, expected_nodes):
    """Every way ``verb``'s candidate surface fails to cover its expectation.

    Both arguments are node LISTS, so a caller (and every test) can hand in a
    mutated capture in memory instead of round-tripping a file.
    """
    candidate = subtree(candidate_nodes, verb)
    expected = as_expectation(subtree(expected_nodes, verb))
    return sorted(set(
        union_mod.superset_violations({"nodes": candidate},
                                      {"nodes": expected})
        + missing_nodes(candidate, expected)
        + dest_violations(candidate, expected)))


def arm_for(verb, ported, local_verbs):
    """Which arm evaluates `verb`.

    DONOR_ONLY is decided FIRST, from whether the monolith serves the verb at
    all — not from allowlist membership. The two questions look interchangeable
    while the six donor-only verbs are unrouted, and stop being interchangeable
    the moment task 52 allowlists them: keyed on `ported` they would silently
    migrate into the PORTED arm, emptying the donor-only arm and making
    `test_the_six_donor_only_verbs_get_their_own_arm` a claim about nothing.

    The arms answer different questions. UNPORTED asks "does the CLI still serve
    what the monolith served?"; PORTED asks "does the lib serve at least what the
    monolith served, now that the router points at it?"; DONOR_ONLY asks "does
    the lib serve the full union node?" — and that last question is the SAME
    before and after routing, because there is no monolith side to compare
    against. Nothing about the check changes when the row lands; only whether a
    user can reach it does.
    """
    if verb not in local_verbs:
        return DONOR_ONLY
    if verb in ported:
        return PORTED
    return UNPORTED


def local_verbs_of(local):
    """Top-level verbs the monolith serves, i.e. what ``aw-pre-merge`` had."""
    return {n["path"][0] for n in local["nodes"] if len(n["path"]) == 1}


def evaluate(union, local, lib_nodes, cli_nodes, ported):
    """Return ``[(verb, arm, violations)]`` over every union top-level verb."""
    local_verbs = local_verbs_of(local)
    rows = []
    for verb in union["top_level_verbs"]:
        arm = arm_for(verb, ported, local_verbs)
        if arm == UNPORTED:
            expected, candidate = local["nodes"], cli_nodes
        else:  # PORTED and DONOR_ONLY are both served by the lib
            expected, candidate = union["nodes"], lib_nodes
        rows.append((verb, arm,
                     promotion_violations(verb, candidate, expected)))
    return rows


def fixture_errors(union, ported):
    """Problems with the gate's own INPUTS, which are files and get edited."""
    problems = []
    seen = {a.get("disposition") for n in union["nodes"] for a in n["actions"]}
    for value in sorted(seen - set(union_mod.DISPOSITIONS), key=str):
        problems.append(
            f"union fixture: action disposition {value!r} is outside "
            f"{union_mod.DISPOSITIONS} -- the comparator only requires rows "
            f"marked 'required', so an unknown value silently requires nothing")
    for verb in sorted(set(ported) - set(union["top_level_verbs"])):
        problems.append(
            f"strangler.PORTED_VERBS holds {verb!r}, which is not a union "
            f"top-level verb -- it is never evaluated by this gate and never "
            f"matched by the router, so the real verb stays on legacy")
    return problems


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------

def check(union_path=None, local_path=None):
    union = union_mod.load_json(union_path or UNION_FIXTURE)
    local = union_mod.load_json(local_path or LOCAL_FIXTURE)
    ported = ported_verbs()
    problems = fixture_errors(union, ported)
    for line in problems:
        print(f"[FAIL] {line}")
    if problems:
        return 1
    # The unported arm is the ONLY reason to build the entrypoint's parser, and
    # at task 56 there is no entrypoint parser to build in the old sense: the
    # monolith is deleted and every verb is ported. Capturing it unconditionally
    # would make this gate's survival depend on a passthrough the plan removes.
    cli_nodes = (capture_effective()
                 if local_verbs_of(local) - ported else [])
    rows = evaluate(union, local, capture_lib(), cli_nodes, ported)
    failed = 0
    for verb, arm, violations in rows:
        for line in violations:
            print(f"[FAIL] {verb} [{arm}]: {line}")
        failed += bool(violations)
    counts = {}
    for _verb, arm, _v in rows:
        counts[arm] = counts.get(arm, 0) + 1
    if failed:
        print(f"[FAIL] promotion gate: {failed} verb(s) below their expectation")
        return 1
    print("[OK] promotion gate: " + ", ".join(
        f"{n} {arm}" for arm, n in sorted(counts.items())))
    return 0


def readiness(verb, union_path=None):
    """Would ``verb`` pass the promotion gate if it were appended today?"""
    union = union_mod.load_json(union_path or UNION_FIXTURE)
    if verb not in union["top_level_verbs"]:
        print(f"[FAIL] {verb!r} is not a union top-level verb")
        return 1
    violations = promotion_violations(verb, capture_lib(), union["nodes"])
    for line in violations:
        print(f"[FAIL] {verb} [readiness]: {line}")
    if violations:
        print(f"[FAIL] {verb}: NOT ready to promote "
              f"({len(violations)} union violation(s))")
        return 1
    print(f"[OK] {verb}: the modular surface covers the union -- ready to "
          f"append to strangler.PORTED_VERBS")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="_promotion_gate",
        description="symmetric per-verb promotion gate against the union surface")
    ap.add_argument("--union", default=UNION_FIXTURE)
    ap.add_argument("--local", default=LOCAL_FIXTURE)
    ap.add_argument("--verb", default=None)
    ap.add_argument("--readiness", action="store_true",
                    help="ask the promotion question about --verb before the "
                         "allowlist append")
    args = ap.parse_args(argv)
    if args.readiness:
        if not args.verb:
            ap.error("--readiness requires --verb")
        return readiness(args.verb, args.union)
    return check(args.union, args.local)


if __name__ == "__main__":
    sys.exit(main())
