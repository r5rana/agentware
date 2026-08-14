#!/usr/bin/env python3
"""The SHRINK-ONLY LEGACY RATCHET (plan task 32, gate 1 of 3).

Feature 260813-modular-core-adoption.

What it guards
--------------
The strangler's whole safety argument is that ``scripts/agentware_legacy.py``
only ever gets SMALLER: a verb leaves the monolith exactly when it arrives in
``agentware_lib`` and joins ``agentware_lib.strangler.PORTED_VERBS``. Nothing
else in the tree enforces the "only ever" part. Legacy is a 34,792-line module
that every unported verb still runs through, and a port that *copies* rather
than *moves* -- or a bugfix applied to the legacy body instead of the lib --
grows the thing the migration exists to delete, with every other gate green. The
surface fixtures cannot see it (the parser is unchanged), the body differ cannot
see it (it compares the two trees' bodies, not the monolith's size), and the
parity manifest cannot see it (no capability was lost).

The marks
---------
Two CEILINGS, which may only fall:

  * ``legacy_lines``  -- physical line count;
  * ``legacy_bytes``  -- non-whitespace byte count. Present because
    ``legacy_lines`` alone is gameable by reflowing, and because the def SET
    below is gameable by RELOCATING code rather than deleting it (wrap the
    module body in a class and the module-scope def list empties while every
    capability stays put). Bytes move only when text actually leaves.

Two NAME SETS, which may only lose members:

  * ``legacy_defs``    -- every module-scope ``def``. Recorded by NAME, not by
    count, so the ratchet's record of what left is legible in one diff. This is
    where the plan's four silent-loss classes live: ``_apply_skill_pin``,
    ``abs_path``, the R-SI-03 marker regexes -- none of them a handler, none of
    them visible to any surface gate.
  * ``legacy_handlers`` -- module-scope-or-nested ``cmd_*`` names. Collected via
    a full AST walk rather than ``tree.body`` so moving a handler inside a class
    does not hide it.

And one FLOOR, which may only rise:

  * ``ported_verbs`` -- the recorded members of ``PORTED_VERBS``, so a promotion
    can never be quietly reverted.

Why the marks are COUPLED, and how tightly
-------------------------------------------
`learn-gate-the-invariant-not-the-outcome-when-a-gate-precedes-its-ports` and
`learn-an-opt-out-flag-un-gates-every-row-that-check-solely-proves` between them
say the interesting failure is not the gate reddening -- it is the gate being
disarmed by the very tool that maintains it. Here that tool is ``--record``. A
naive ``--record`` writes down whatever the tree currently holds, so legacy
shrinks because a handler was DELETED rather than PORTED, the mark is
re-recorded, and the capability is gone with a green run at every step.

``--record`` therefore refuses to write a mark that rises, and enforces the
coupling PER HANDLER rather than per commit. "At least one verb was ported this
commit" is not enough: task 39 legitimately removes nine ``cmd_plan_*`` handlers
for one verb, so N-for-1 is the NORMAL shape, and a commit-level check would let
that one port pay for deleting ``cmd_profile`` too -- leaving ``profile``
registered in the parser, dispatching, and raising ``NameError``, with both this
gate and the promotion gate green. So every handler that leaves must NAME a verb
that arrived: ``cmd_<verb>`` or ``cmd_<verb>_<sub>``, with ``-`` written ``_``.
An orphan is refused by name.

The verbs credited as payment are intersected with the binding union fixture's
top-level verb list, because ``PORTED_VERBS`` is a bare ``frozenset`` of strings
and nothing else rejects a member that is not a verb: appending ``'not-a-verb'``
-- or the typo ``'fleet_emit'`` for ``fleet-emit`` -- would otherwise buy a
deletion while the real verb stayed on the legacy arm.

There is deliberately no flag to skip any of this. An opt-out would leave every
shrink unproven by default, which is exactly the hole the second learning names.

What it still does NOT prove
-----------------------------
A def that leaves ``legacy_defs`` without being a handler is REFUSED a silent
re-record only in the sense that it must appear in the recorded diff -- the
coupling is enforced on handlers alone, because helpers move with their verb
under names that share no prefix with it (``lint_plan`` for ``plan``), and
demanding a name-level mapping for those would reject correct ports. The set is
recorded by name so such a departure is legible and reviewable; it is not
mechanically tied to a port.

Retirement
----------
Task 56 deletes ``agentware_legacy.py`` and, with it, ``strangler.PORTED_VERBS``.
BOTH absences are terminal states, not errors: an absent monolith scores zero on
every ceiling and an empty name set, and an absent strangler module scores an
empty allowlist. The gate reports the retirement instead of raising.

Usage:
    python3 tests/_legacy_ratchet.py                  # check (shrink-only)
    python3 tests/_legacy_ratchet.py --strict         # also assert tightness
    python3 tests/_legacy_ratchet.py --record         # tighten after a port

Stdlib only, hermetic: ``ast`` over the monolith on disk, one JSON fixture read,
and one import of ``agentware_lib.strangler`` (which imports nothing at module
scope by design).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys

SCHEMA = "agentware/legacy-ratchet@2"

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "fixtures", "legacy-ratchet.json")
UNION_FIXTURE = os.path.join(HERE, "fixtures", "surface-union.json")

#: Marks that may only ever DECREASE.
CEILINGS = ("legacy_lines", "legacy_bytes")

#: Marks that are NAME SETS and may only ever lose members.
NAME_SETS = ("legacy_defs", "legacy_handlers")

#: Prefix of a legacy command handler. The monolith registers no ``register_*``
#: functions (its parser is built inline in ``build_parser``), so ``cmd_*`` is
#: the only handler-shaped name there is.
HANDLER_PREFIX = "cmd_"


def legacy_path(root=None):
    return os.path.join(root or REPO_ROOT, "scripts", "agentware_legacy.py")


def strangler_path(root=None):
    return os.path.join(root or REPO_ROOT, "scripts", "agentware_lib",
                        "strangler.py")


def measure_legacy(path=None):
    """Return the four marks for the monolith at ``path``.

    An ABSENT monolith is the terminal state (task 56), not a failure: it scores
    zero on every ceiling and empty on every set, so the ratchet stays satisfied
    after the file is deleted instead of needing a special case at its own
    retirement.
    """
    path = path or legacy_path()
    if not os.path.isfile(path):
        return {"legacy_lines": 0, "legacy_bytes": 0,
                "legacy_defs": [], "legacy_handlers": []}
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source)
    is_def = (ast.FunctionDef, ast.AsyncFunctionDef)
    return {
        "legacy_lines": source.count("\n") + (0 if source.endswith("\n") else 1),
        # Whitespace-insensitive size: the one mark that cannot be satisfied by
        # reflowing the file or by relocating code into a class.
        "legacy_bytes": len("".join(source.split())),
        "legacy_defs": sorted(n.name for n in tree.body
                              if isinstance(n, is_def)),
        # ast.walk, not tree.body: a handler moved inside a class still counts.
        "legacy_handlers": sorted({n.name for n in ast.walk(tree)
                                   if isinstance(n, is_def)
                                   and n.name.startswith(HANDLER_PREFIX)}),
    }


def measure_ported(root=None):
    """Return the sorted members of ``strangler.PORTED_VERBS``.

    Read through a fresh import off ``scripts/`` rather than from a cached
    module, so a caller can point ``--root`` at a scratch tree and measure THAT
    tree's allowlist. An ABSENT strangler module is task 56's other terminal
    state and scores an empty allowlist; a strangler that EXISTS but cannot be
    imported is a real defect and is left to raise.
    """
    root = root or REPO_ROOT
    if not os.path.isfile(strangler_path(root)):
        return []
    scripts = os.path.join(root, "scripts")
    saved_path = sys.path[:]
    saved_mods = {k: v for k, v in sys.modules.items()
                  if k == "agentware_lib" or k.startswith("agentware_lib.")}
    try:
        sys.path.insert(0, scripts)
        for name in list(saved_mods):
            del sys.modules[name]
        from agentware_lib import strangler  # noqa: PLC0415 - deliberate
        return sorted(strangler.PORTED_VERBS)
    finally:
        sys.path[:] = saved_path
        for name in [k for k in sys.modules
                     if k == "agentware_lib" or k.startswith("agentware_lib.")]:
            del sys.modules[name]
        sys.modules.update(saved_mods)


def union_verbs(path=None):
    """Top-level verbs the binding union fixture declares."""
    with open(path or UNION_FIXTURE, encoding="utf-8") as fh:
        return set(json.load(fh)["top_level_verbs"])


def handler_prefix(verb):
    return HANDLER_PREFIX + verb.replace("-", "_")


def credited_by(handler, verbs):
    """The verb in ``verbs`` whose port accounts for ``handler``, or ``None``."""
    for verb in sorted(verbs):
        prefix = handler_prefix(verb)
        if handler == prefix or handler.startswith(prefix + "_"):
            return verb
    return None


def load_fixture(path=None):
    with open(path or FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def save_fixture(data, path=None):
    with open(path or FIXTURE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=False)
        fh.write("\n")


def schema_errors(data):
    """Structural problems with the fixture itself."""
    problems = []
    if data.get("schema") != SCHEMA:
        problems.append(f"schema {data.get('schema')!r} != {SCHEMA!r}")
    high = data.get("high_water")
    if not isinstance(high, dict):
        return problems + ["high_water missing or not an object"]
    for mark in CEILINGS:
        if not isinstance(high.get(mark), int):
            problems.append(f"high_water.{mark} missing or not an int")
    for mark in NAME_SETS:
        if not isinstance(high.get(mark), list):
            problems.append(f"high_water.{mark} missing or not a list")
    if not isinstance((data.get("floor") or {}).get("ported_verbs"), list):
        problems.append("floor.ported_verbs missing or not a list")
    return problems


def compare(current, ported, fixture, verbs=None):
    """Every way the tree VIOLATES the recorded ratchet. Empty list == green."""
    violations = []
    high = fixture["high_water"]
    for mark in CEILINGS:
        if current[mark] > high[mark]:
            violations.append(
                f"{mark} rose: {current[mark]} > recorded high-water "
                f"{high[mark]} -- legacy may only shrink")
    for mark in NAME_SETS:
        for name in sorted(set(current[mark]) - set(high[mark])):
            violations.append(
                f"{mark[len('legacy_'):]} {name} is not in the recorded set -- "
                f"it was ADDED to the monolith, or renamed inside it")
    floor = set(fixture["floor"]["ported_verbs"])
    for verb in sorted(floor - set(ported)):
        violations.append(
            f"ported verb {verb!r} left strangler.PORTED_VERBS -- a promotion "
            f"was reverted; the allowlist may only grow")
    for verb in sorted(set(ported) - (verbs if verbs is not None
                                      else union_verbs())):
        violations.append(
            f"{verb!r} is in strangler.PORTED_VERBS but is not a top-level verb "
            f"of the union surface -- a typo here routes the real verb to legacy "
            f"while looking promoted")
    return violations


def slack(current, fixture):
    """Marks the tree has moved past but the fixture has not recorded."""
    high, loose = fixture["high_water"], []
    for mark in CEILINGS:
        if current[mark] < high[mark]:
            loose.append(f"{mark} is {current[mark]} but recorded "
                         f"{high[mark]} -- re-record with --record")
    for mark in NAME_SETS:
        gone = sorted(set(high[mark]) - set(current[mark]))
        if gone:
            loose.append(f"{len(gone)} {mark[len('legacy_'):]} left legacy "
                         f"without being re-recorded: {', '.join(gone[:6])}"
                         + (" ..." if len(gone) > 6 else ""))
    return loose


def record_errors(current, ported, fixture, verbs=None):
    """Why a ``--record`` must be REFUSED. Empty list == the write is legal."""
    verbs = union_verbs() if verbs is None else verbs
    problems = list(compare(current, ported, fixture, verbs))
    high = fixture["high_water"]
    gone = sorted(set(high["legacy_handlers"]) - set(current["legacy_handlers"]))
    # Only verbs that are BOTH new to the allowlist and real can pay for a
    # deletion; see the module docstring on why membership alone is not enough.
    paid = (set(ported) - set(fixture["floor"]["ported_verbs"])) & verbs
    orphans = [h for h in gone if credited_by(h, paid) is None]
    if orphans:
        problems.append(
            f"refusing to record: {len(orphans)} handler(s) left legacy with no "
            f"port to account for them ({', '.join(orphans[:6])}"
            + (" ..." if len(orphans) > 6 else "")
            + f"). Legacy code is deleted as the SECOND HALF OF A PORT: each "
            f"cmd_<verb>[_<sub>] must name a verb appended to "
            f"strangler.PORTED_VERBS in the same commit "
            f"(newly ported this commit: {sorted(paid) or 'none'}). "
            f"There is no flag to skip this check.")
    return problems


def tighten(current, ported, fixture):
    """Return the fixture with every mark pulled down to the live tree."""
    updated = json.loads(json.dumps(fixture))
    for mark in CEILINGS:
        updated["high_water"][mark] = current[mark]
    for mark in NAME_SETS:
        updated["high_water"][mark] = list(current[mark])
    updated["floor"]["ported_verbs"] = list(ported)
    return updated


def _summary(current, ported):
    return (f"{current['legacy_lines']} lines, {current['legacy_bytes']} bytes, "
            f"{len(current['legacy_defs'])} defs, "
            f"{len(current['legacy_handlers'])} handlers, "
            f"{len(ported)} ported verb(s)")


def _load(path, root):
    fixture = load_fixture(path)
    problems = schema_errors(fixture)
    if problems:
        for line in problems:
            print(f"[FAIL] legacy-ratchet fixture: {line}")
        return None, None, None
    return (fixture, measure_legacy(legacy_path(root)), measure_ported(root))


def check(path=None, root=None, strict=False, union=None):
    fixture, current, ported = _load(path, root)
    if fixture is None:
        return 1
    violations = compare(current, ported, fixture, union_verbs(union))
    for line in violations:
        print(f"[FAIL] {line}")
    loose = slack(current, fixture)
    for line in loose:
        print(f"[{'FAIL' if strict else 'warn'}] ratchet not tight: {line}")
    if violations or (strict and loose):
        return 1
    print(f"[OK] legacy ratchet: {_summary(current, ported)}")
    return 0


def record(path=None, root=None, union=None):
    path = path or FIXTURE
    fixture, current, ported = _load(path, root)
    if fixture is None:
        return 1
    problems = record_errors(current, ported, fixture, union_verbs(union))
    if problems:
        for line in problems:
            print(f"[FAIL] {line}")
        return 1
    save_fixture(tighten(current, ported, fixture), path)
    print(f"recorded {path}: {_summary(current, ported)}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="_legacy_ratchet",
        description="shrink-only ratchet over the preserved monolith")
    ap.add_argument("--fixture", default=FIXTURE)
    ap.add_argument("--root", default=None,
                    help="tree to measure (default: this repo)")
    ap.add_argument("--strict", action="store_true",
                    help="also fail when the fixture is looser than the tree")
    ap.add_argument("--record", action="store_true",
                    help="tighten the fixture; refuses to loosen it")
    ap.add_argument("--union", default=UNION_FIXTURE,
                    help="binding union fixture the credited verbs must be in")
    args = ap.parse_args(argv)
    if args.record:
        return record(args.fixture, args.root, args.union)
    return check(args.fixture, args.root, args.strict, args.union)


if __name__ == "__main__":
    sys.exit(main())
