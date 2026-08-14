#!/usr/bin/env python3
"""The DONOR-ONLY VERB PRESENCE GATE (plan task 32, gate 3 of 3).

Feature 260813-modular-core-adoption.

Why a THIRD gate, when two already compare surfaces
----------------------------------------------------
Legacy-default routing (task 29) serves every verb from the monolith until it is
appended to ``agentware_lib.strangler.PORTED_VERBS``. That is the right default
for the thirty-eight verbs this tree already had -- but it is structurally blind
to the six the DONOR brought and this tree never had:

    fleet · fleet-emit · org · team · templates · work

They are served by nothing. The monolith has no handler for them, and until task
52 ports them the allowlist has no entry either. The promotion gate's unported
arm therefore has nothing to compare: its expectation for an unported verb is
that verb's ``surface-local.json`` subtree, and for these six that subtree is
EMPTY. A superset-of-empty check is satisfied by absence. Delete
``agentware_lib.ops.fleet`` outright and every surface gate in the tree still
passes, because each one only ever asks "is the merged surface at least what was
promised" about verbs something already promises.

So presence is asserted POSITIVELY and against the LIB, which is the only tree
that can ever serve them:

  1. the verb node exists in ``agentware_lib.cli.build_parser()``;
  2. its whole union subtree is covered -- every subnode, every subcommand and
     every required action compared on kind / required / nargs / default /
     choices / type, not on flag names (the comparator is the one
     ``_surface_union.superset_violations`` owns, so this gate and the promotion
     gate cannot drift apart);
  3. it is not a STUB. Registering a bare ``sub.add_parser("team")`` satisfies
     "the verb exists" and, for a verb whose union node happens to carry no
     declared arguments of its own (``org``, ``templates`` and ``work`` each
     carry none -- their substance is entirely in one subcommand), would satisfy
     a naive action comparison too. A verb must therefore carry SUBSTANCE:
     at least one subcommand, or at least one required action that is not the
     inherited global ``--format``.

  4. it is REACHABLE, or explicitly recorded as not yet reachable. Presence in
     the parser is not the same as being served: a donor-only verb also has to
     be in ``strangler.PORTED_VERBS`` for the router to send it to the lib.
     Today none of the six is (task 52 appends them), so every one is registered
     and unreachable -- ``agentware team --help`` still answers `invalid choice:
     'team'`. That is the expected pre-task-52 state, but a gate that prints an
     unqualified OK over it would print the same OK after task 52 if the
     allowlist append were forgotten or misspelled. So routing is CHECKED for
     any of the six already in the allowlist, and the ones that are not are
     named in the verdict as pending rather than passed over in silence.

Checks 3 and 4 are the ones that survive the others. Checks 1 and 2 are implied
by a correct union comparison; check 3 exists because the plan names the failure
explicitly ("a stub verb with no subverbs reddens it") and because a stub is the
natural artifact of a half-finished port -- the registrar lands first and the
subcommands follow in the next commit.

Retirement: none. Unlike the legacy ratchet and the promotion gate's unported
arm, this gate outlives the strangler -- these six verbs must still be present
long after task 56 deletes the monolith.

Usage:
    python3 tests/_presence_gate.py
    python3 tests/_presence_gate.py --verbs team,org

Stdlib only. Builds the modular parser in-process; no subprocess.
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    from tests import _promotion_gate as promo
    from tests import _surface_union as union_mod
except ImportError:  # allow direct execution
    sys.path.insert(0, HERE)
    import _promotion_gate as promo  # type: ignore
    import _surface_union as union_mod  # type: ignore

UNION_FIXTURE = union_mod.UNION_FIXTURE

#: The one option every verb inherits from the shared ``--format`` parent. It is
#: not substance: a stub registered with ``parents=[fmt_parent]`` carries it.
INHERITED = ("--format",)


def top_node(nodes, verb):
    for node in nodes:
        if list(node["path"]) == [verb]:
            return node
    return None


def substance_of(node):
    """``(subcommands, own_actions)`` -- what makes a verb more than a stub."""
    own = [a for a in node["actions"]
           if a.get("disposition", "required") == "required"
           and tuple(a["option_strings"]) != INHERITED]
    return list(node["subcommands"]), own


def stub_violations(verb, node):
    """Non-empty when ``verb`` is registered but carries nothing."""
    subs, own = substance_of(node)
    if subs or own:
        return []
    return [f"{verb} is a STUB: registered with no subcommands and no arguments "
            f"of its own beyond the inherited --format"]


def routing_violations(verb, ported):
    """Non-empty when an ALLOWLISTED verb still cannot reach the lib.

    Checks the ROUTER's own argv scan rather than ``route()``: ``route`` reads
    the live module global, so passing a substituted allowlist to it would
    prove nothing. What can actually go wrong at the append is that the token
    written into the allowlist is not what the scan resolves as the verb --
    ``verb_of`` steps over global options and their values, so a token shaped
    like one is consumed rather than returned, and the entry is dead.
    """
    if verb not in ported:
        return []
    scripts = os.path.join(promo.REPO_ROOT, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from agentware_lib import strangler  # noqa: PLC0415
    resolved = strangler.verb_of([verb])
    if resolved == verb:
        return []
    return [f"{verb} is in PORTED_VERBS but the router's argv scan resolves "
            f"{resolved!r} as the verb, so the entry can never match -- the "
            f"verb stays on the legacy arm while looking promoted"]


def verb_violations(verb, lib_nodes, union_nodes, ported=()):
    """Presence + coverage + not-a-stub + routing for one donor-only verb."""
    node = top_node(lib_nodes, verb)
    if node is None:
        return [f"{verb} is ABSENT from the modular parser -- no tree serves it "
                f"(legacy has no handler and the allowlist cannot route to one)"]
    return (promo.promotion_violations(verb, lib_nodes, union_nodes)
            + stub_violations(verb, node)
            + routing_violations(verb, ported))


def evaluate(union, lib_nodes, verbs=None, ported=()):
    """Return ``[(verb, violations)]`` for the donor-only verb set."""
    targets = verbs if verbs is not None else union["donor_only_verbs"]
    return [(verb, verb_violations(verb, lib_nodes, union["nodes"], ported))
            for verb in targets]


def check(union_path=None, verbs=None):
    union = union_mod.load_json(union_path or UNION_FIXTURE)
    unknown = sorted(set(verbs or ()) - set(union["donor_only_verbs"]))
    if unknown:
        for verb in unknown:
            print(f"[FAIL] {verb!r} is not a donor-only verb")
        return 1
    ported = promo.ported_verbs()
    rows = evaluate(union, promo.capture_lib(), verbs, ported)
    if not rows:
        print("[FAIL] presence gate: nothing to check -- the donor-only verb "
              "set is empty, which cannot be right")
        return 1
    failed = 0
    for verb, violations in rows:
        for line in violations:
            print(f"[FAIL] presence: {line}")
        failed += bool(violations)
    if failed:
        print(f"[FAIL] presence gate: {failed} of {len(rows)} donor-only "
              f"verb(s) missing, incomplete, stubbed or misrouted")
        return 1
    pending = [v for v, _ in rows if v not in ported]
    print(f"[OK] presence gate: all {len(rows)} donor-only verb(s) present, "
          f"complete against the union and non-stub "
          f"({', '.join(v for v, _ in rows)})")
    if pending:
        # Named, never silent: registered-but-unroutable is the pre-task-52
        # state, and it must not look the same as a finished port.
        print(f"[pending] {len(pending)} of {len(rows)} not yet in "
              f"strangler.PORTED_VERBS, so still unreachable from the CLI "
              f"(task 52): {', '.join(pending)}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="_presence_gate",
        description="assert the six donor-only verbs exist in the modular tree")
    ap.add_argument("--union", default=UNION_FIXTURE)
    ap.add_argument("--verbs", default=None,
                    help="comma-separated subset (default: all donor-only)")
    args = ap.parse_args(argv)
    verbs = ([v for v in args.verbs.split(",") if v]
             if args.verbs is not None else None)
    return check(args.union, verbs)


if __name__ == "__main__":
    sys.exit(main())
