"""Derive, verify and gate the task-9 PRESERVED-SURFACE freeze.

Tasks 6-8 froze the surfaces a parser dump and an env/config scan can see. This
module freezes what is left, and it freezes it by NODE ID and CONTENT ANCHOR
rather than by filename, because filename equality is exactly the check that
lets a same-path copy destroy one side of a collision silently.

Modes
-----
  --out <path>                 derive the fixture from both trees
  --verify <path>              recompute every count from the body
  --check-destination <root>   the gate: nothing frozen may be LOST

Ratchet
-------
The merged tree does not exist yet, so the donor node ids and donor-only CLI
symbols cannot resolve today. Following `learn-ship-a-not-yet-satisfiable-gate-
as-a-shrink-only-ratchet` (and the identical task-8 decision), they ship as a
shrink-only `pending_in_destination` set: anything resolving today that stops is
a LOST regression, and anything pending that starts resolving reddens until it
is removed from the list. The backlog can only shrink, so task 79 can simply
require it to be empty.
"""

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from _preserved_scan import (  # noqa: E402
    FIXTURE_FAMILIES, TEMPLATE_ANCHORS, collect_node_ids, git_head, module_of,
    relativise, scan_tree, tail_of)
from _test_layout import resolves  # noqa: E402

SCHEMA = 1

#: The collision PIN table and the layered subset of it, both moved to
#: `tests/_preserved_pins.py` at task 99 wave 4 (pure data; this file is the
#: derivation and the verification that read it) and re-exported here so every
#: consumer keeps importing them from where it already does.
from _preserved_pins import (  # noqa: E402,F401
    COLLISION_DESTINATIONS, RELAYERING_COLLISIONS)


# --------------------------------------------------------------------------
# derivation
# --------------------------------------------------------------------------

def _collision_rows(local_ids, donor_ids):
    """Shared-path test modules, and what a blanket same-path copy would cost."""
    local_by_mod, donor_by_mod = {}, {}
    for nid in local_ids:
        local_by_mod.setdefault(module_of(nid), set()).add(nid)
    for nid in donor_ids:
        donor_by_mod.setdefault(module_of(nid), set()).add(nid)

    rows = {}
    for mod in sorted(set(local_by_mod) & set(donor_by_mod)):
        mine, theirs = local_by_mod[mod], donor_by_mod[mod]
        lost = sorted(mine - theirs)
        if not lost:
            continue
        rel = mod.replace(".", "/") + ".py"
        disjoint = not (mine & theirs)
        rows[rel] = {
            "module": mod,
            "local_ids": len(mine),
            "donor_ids": len(theirs),
            "overlap": len(mine & theirs),
            "local_ids_destroyed_by_same_path_copy": len(lost),
            "disjoint": disjoint,
            "resolution": ("split-two-destinations" if disjoint
                           else "reconcile-in-place"),
            "destinations": COLLISION_DESTINATIONS.get(rel, {}),
        }
    return rows


def _tail_collisions(local_ids, donor_ids, rows):
    """`Class.test_method` tails supplied to one DESTINATION by two DIFFERENT
    source files -- the only shape that actually destroys a test.

    Globally-unique tails are NOT a property either tree has, and requiring
    them would be wrong: the donor's per-module contract-test convention
    repeats `ExportsTest.test_all_exports_present` across 14 modules, giving 37
    global tail collisions that are entirely benign because the module path
    disambiguates them. Nor is "both sides contribute this tail" a collision --
    for a shared-path module that is the SAME test, reconciled in place.

    A destination is keyed by SOURCE PATH, so the two sides of one path count
    as a single contributor; a conflict is two distinct source paths landing on
    one destination and both supplying the same tail.
    """
    contributors = {}
    for side, ids in (("local", local_ids), ("donor", donor_ids)):
        for nid in ids:
            rel = module_of(nid).replace(".", "/") + ".py"
            dest = rows.get(rel, {}).get("destinations", {}).get(side, rel)
            contributors.setdefault(dest, {}).setdefault(rel, set()).add(tail_of(nid))

    out = {}
    for dest, by_source in contributors.items():
        if len(by_source) < 2:
            continue
        seen, clash = set(), set()
        for tails in by_source.values():
            clash |= seen & tails
            seen |= tails
        if clash:
            out[dest] = sorted(clash)
    return out


def _global_tail_duplicates(local_ids, donor_ids):
    """Count tails reused across DIFFERENT modules -- recorded as measured
    evidence that global tail-uniqueness is not a real invariant, never gated."""
    by_tail = {}
    for nid in set(local_ids) | set(donor_ids):
        by_tail.setdefault(tail_of(nid), set()).add(module_of(nid))
    return sum(1 for mods in by_tail.values() if len(mods) > 1)


def _merge_named(local, donor, key):
    """Merge a per-tree {name: meta} map into {name: {sides, ...}}.

    List-valued fields are UNIONED (a symbol read on both sides keeps both
    trees' read sites); scalar fields take the local value when present. A
    merge that silently kept one side's list would reintroduce exactly the
    single-side blindness this freeze exists to prevent.
    """
    out = {}
    for name in sorted(set(local[key]) | set(donor[key])):
        sides = [s for s, tree in (("local", local), ("donor", donor))
                 if name in tree[key]]
        row = {}
        for side in reversed(sides):  # donor first, local overwrites scalars
            for field, value in (local if side == "local" else donor)[key][name].items():
                if isinstance(value, list):
                    row[field] = sorted(set(row.get(field, [])) | set(value))
                else:
                    row[field] = value
        row["sides"] = sides
        out[name] = row
    return out


def derive(local_root, donor_root, out_path):
    local = scan_tree(local_root, "local")
    donor = scan_tree(donor_root, "donor")
    local_ids, local_errs = collect_node_ids(local_root)
    donor_ids, donor_errs = collect_node_ids(donor_root)
    if local_errs or donor_errs:
        raise SystemExit("node-id collection had load errors: %s"
                         % (local_errs + donor_errs)[:5])

    out_dir = os.path.dirname(os.path.realpath(out_path))
    for side, ids in (("local", local_ids), ("donor", donor_ids)):
        with open(os.path.join(out_dir, "nodeids-%s.json" % side), "w") as fh:
            json.dump({"side": side, "count": len(ids), "node_ids": ids},
                      fh, indent=1)
            fh.write("\n")

    rows = _collision_rows(local_ids, donor_ids)
    local_set, donor_set = set(local_ids), set(donor_ids)
    symbols = _merge_named(local, donor, "cli_symbols")
    fixture = {
        "schema": SCHEMA,
        "generated_by": "tests/_preserved_freeze.py",
        "trees": {s: {"root": t["root"], "git_head": t["git_head"]}
                  for s, t in (("local", local), ("donor", donor))},
        "node_ids": {
            "local": {"file": "nodeids-local.json", "count": len(local_ids)},
            "donor": {"file": "nodeids-donor.json", "count": len(donor_ids)},
            "union": len(local_set | donor_set),
            "shared": len(local_set & donor_set),
            "pending_in_destination": sorted(donor_set - local_set),
            # CURATED, seeded empty: donor-only ids a port wave has already
            # landed here. `_verify_backlog` requires pending + landed to
            # account for every donor-only id, so an id can MOVE between the two
            # but never leave the backlog. See that function for why the two
            # ratchet rules were previously unsatisfiable together.
            "landed_from_donor": [],
        },
        "cli_symbols": {
            "count": len(symbols),
            "by_symbol": symbols,
            # MEASURED, never inferred from side: a symbol only the donor tests
            # read may still resolve here today, and freezing it as pending
            # would trip the ratchet on a capability that is already present.
            "pending_in_destination": sorted(
                set(symbols) - reachable_symbols(local_root, set(symbols))),
        },
        "executables": _merge_named(local, donor, "executables"),
        "templates": _merge_named(local, donor, "templates"),
        "catalogs": _merge_named(local, donor, "catalogs"),
        "fixture_families": _merge_named(local, donor, "fixture_families"),
        "assets": {"pending_in_destination": []},
        "test_path_collisions": {
            "rows": rows,
            "total_local_ids_destroyed_by_blanket_copy": sum(
                r["local_ids_destroyed_by_same_path_copy"] for r in rows.values()),
            "destination_tail_collisions": _tail_collisions(
                local_ids, donor_ids, rows),
            "global_tail_duplicates_not_gated": _global_tail_duplicates(
                local_ids, donor_ids),
        },
    }
    fixture["assets"]["pending_in_destination"] = sorted(
        unsatisfied_assets(fixture, os.path.realpath(local_root)))
    with open(out_path, "w") as fh:
        json.dump(fixture, fh, indent=1, sort_keys=False)
        fh.write("\n")
    return fixture


# --------------------------------------------------------------------------
# verification (recompute, never trust the header)
# --------------------------------------------------------------------------

def load_node_ids(fixture_path, side):
    path = os.path.join(os.path.dirname(os.path.realpath(fixture_path)),
                        "nodeids-%s.json" % side)
    with open(path) as fh:
        return json.load(fh)


def verify(fixture_path):
    with open(fixture_path) as fh:
        fx = json.load(fh)
    problems = []
    if fx.get("schema") != SCHEMA:
        problems.append("schema %r != %r" % (fx.get("schema"), SCHEMA))

    sets = {}
    for side in ("local", "donor"):
        blob = load_node_ids(fixture_path, side)
        ids = blob["node_ids"]
        sets[side] = set(ids)
        if len(ids) != len(sets[side]):
            problems.append("%s node ids contain duplicates" % side)
        if blob["count"] != len(ids):
            problems.append("%s count %d != %d listed" % (side, blob["count"], len(ids)))
        if fx["node_ids"][side]["count"] != len(ids):
            problems.append("%s header count %d != %d in nodeids-%s.json"
                            % (side, fx["node_ids"][side]["count"], len(ids), side))

    nid = fx["node_ids"]
    if nid["union"] != len(sets["local"] | sets["donor"]):
        problems.append("union %d != %d recomputed"
                        % (nid["union"], len(sets["local"] | sets["donor"])))
    if nid["shared"] != len(sets["local"] & sets["donor"]):
        problems.append("shared %d != %d recomputed"
                        % (nid["shared"], len(sets["local"] & sets["donor"])))
    problems.extend(_verify_backlog(nid, sets))

    problems.extend(_verify_symbols(fx))
    problems.extend(_verify_assets(fx))
    problems.extend(_verify_collisions(fx, sets))
    return problems


def _verify_backlog(nid, sets):
    """The donor backlog accounts for every donor-only id, pending OR landed.

    THE CONTRADICTION THIS RESOLVES. Two rules disagreed the moment a donor test
    first landed here. `diff_node_ids` reddens with "RATCHET: pending node id now
    resolves, drop it" — the backlog must SHRINK. But this verifier recomputed
    `pending == donor - local` from the two frozen captures, which are immutable
    historical records: by that rule the backlog could never shrink at all. Every
    port wave (tasks 37-54) walks into it; task 27 hit it first by landing
    `test_sidecars_excluded_from_guarded_corpus`.

    Resolved by making the accounting explicit rather than derived. Every
    donor-only id is in exactly ONE of two sets: `pending_in_destination` (not
    here yet) or `landed_from_donor` (ported, and therefore REQUIRED to resolve
    — `diff_node_ids` already reports a non-pending donor id that stops
    collecting as "LOST donor node id", so landing an id also arms its
    regression check). The union still equals the derived donor-only set, so
    nothing can be dropped from the backlog by deleting it: it can only move.
    That keeps the ratchet one-directional and lets task 79 require
    `pending_in_destination == []` unchanged.

    `landed_from_donor` is CURATED, not derived: `derive()` seeds it empty, so a
    blind re-derivation would discard it. Re-derive and diff, never regenerate
    (learn-re-derive-and-diff-never-regenerate-a-mixed-fixture).
    """
    problems = []
    pending = set(nid["pending_in_destination"])
    landed = set(nid.get("landed_from_donor", []))
    donor_only = sets["donor"] - sets["local"]
    if pending & landed:
        problems.append("node ids both pending and landed: %s"
                        % sorted(pending & landed)[:5])
    if pending | landed != donor_only:
        missing = sorted(donor_only - (pending | landed))[:5]
        stray = sorted((pending | landed) - donor_only)[:5]
        problems.append(
            "node-id backlog (pending + landed) != donor-only ids "
            "(unaccounted: %s; not donor-only: %s)" % (missing, stray))
    return problems


def _verify_symbols(fx):
    problems = []
    syms = fx["cli_symbols"]
    if syms["count"] != len(syms["by_symbol"]):
        problems.append("cli_symbols count %d != %d rows"
                        % (syms["count"], len(syms["by_symbol"])))
    donor_only = {n for n, r in syms["by_symbol"].items() if r["sides"] == ["donor"]}
    stray = sorted(set(syms["pending_in_destination"]) - donor_only)
    if stray:
        # A symbol the LOCAL tests read must resolve here today, or the suite
        # would already be red. Pending may therefore only hold donor-side rows.
        problems.append("cli pending set holds locally-read symbols: %r" % stray)
    for name, row in syms["by_symbol"].items():
        if not row.get("sides"):
            problems.append("cli symbol %s has no side" % name)
    return problems


def _verify_assets(fx):
    problems = []
    for name, row in fx["templates"].items():
        expected = sorted(TEMPLATE_ANCHORS.get(name, ()))
        if sorted(row["anchors"]) != expected:
            problems.append("template %s anchors %r != %r"
                            % (name, row["anchors"], expected))
        if not expected:
            problems.append("template %s declares no anchor" % name)
    families = {f for f, _rel, _k in FIXTURE_FAMILIES}
    if set(fx["fixture_families"]) != families:
        problems.append("fixture families %r != %r"
                        % (sorted(fx["fixture_families"]), sorted(families)))
    if not fx["catalogs"]:
        problems.append("no catalog file claimed")
    section = {"executable": "executables", "template": "templates",
               "catalog": "catalogs", "fixture_family": "fixture_families"}
    for key in fx["assets"]["pending_in_destination"]:
        kind, _, name = key.partition(":")
        row = fx.get(section.get(kind), {}).get(name)
        if row is None:
            problems.append("pending asset %s claims no frozen row" % key)
        elif row["sides"] != ["donor"]:
            # Anything this tree owns must be satisfied here TODAY; parking a
            # local asset in the backlog would hide a live defect.
            problems.append("pending asset %s is not donor-only (sides=%r)"
                            % (key, row["sides"]))
    return problems


def _verify_collisions(fx, sets):
    problems = []
    coll = fx["test_path_collisions"]
    rows = coll["rows"]
    total = sum(r["local_ids_destroyed_by_same_path_copy"] for r in rows.values())
    if coll["total_local_ids_destroyed_by_blanket_copy"] != total:
        problems.append("collision total %d != %d recomputed"
                        % (coll["total_local_ids_destroyed_by_blanket_copy"], total))
    for rel, row in rows.items():
        if row["disjoint"] and not row["destinations"]:
            problems.append("disjoint collision %s has no pinned destinations" % rel)
        if row["destinations"]:
            dests = sorted(row["destinations"].values())
            if len(set(dests)) != len(dests):
                problems.append("%s pins the same destination twice" % rel)
            if set(row["destinations"]) != {"local", "donor"}:
                problems.append("%s does not pin BOTH sides" % rel)
    if coll["destination_tail_collisions"]:
        problems.append("destination tail collisions: %r"
                        % coll["destination_tail_collisions"])
    if not sets["local"] or not sets["donor"]:
        problems.append("a frozen node-id set is empty")
    return problems


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------

#: TERMINAL STATE (task 56). While the monolith stands it is `exec`'d into the
#: facade's globals, so every frozen symbol is already in that module's
#: `__dict__` and a plain `hasattr` finds it. Once it is DELETED the facade is
#: the pure lib entrypoint: the frozen names still EXIST -- all 359 required ones
#: are owned by an `agentware_lib` module -- but the module OBJECT they land on
#: has moved from the exec'd namespace into the package. A `hasattr` on the
#: facade alone then reports 358 of them UNREACHABLE, which is a true statement
#: about a namespace and a false one about the tree.
#:
#: So the probe resolves through the suite's ONE definition of where a legacy
#: name lives -- `_fixtures._ConfigMirrorModule.__getattr__`, applied by
#: `attach_lib_resolver` -- in BOTH states, rather than branching on whether the
#: monolith happens to be present. Branching was tried first and is wrong: it
#: makes the two states disagree about the five donor symbols that landed in the
#: lib during the port wave, so ONE frozen fixture cannot satisfy both, and the
#: shrink-only ratchet has no single answer to shrink toward.
#:
#: Reusing the harness resolver rather than writing a second walk here is
#: deliberate: `CLI.<name>` is how ~3,000 call sites reach these symbols, so the
#: resolver IS the tree's answer to "does this name resolve", and one definition
#: cannot drift from itself. It also means task 95's decision about `load_cli()`
#: propagates to this gate automatically instead of silently.
#:
#: This does NOT retire the freeze's strangler-window job. The question it asks
#: is whether the TREE still carries the name, so a symbol deleted from the
#: monolith and absent from the lib is still LOST -- while a symbol deleted from
#: the monolith BECAUSE it was ported is correctly not a loss, which is the one
#: verdict a facade-only probe got backwards.
#:
#: NOT a blanket yes: `__getattr__` raises for a name no lib module defines (and
#: refuses submodule REFERENCES on purpose), so a genuinely lost symbol stays
#: LOST. `test_the_cli_symbol_gate_is_not_measuring_an_empty_set` and
#: `TerminalStateSymbolProbeTest` pin that floor from the other side.
_PROBE = (
    "import json,os,sys,tempfile\n"
    "root=%r\n"
    "for k in [k for k in os.environ if k.startswith('AGENTWARE_')]: del os.environ[k]\n"
    "t=tempfile.mkdtemp(prefix='aw-sym-')\n"
    "os.environ['AGENTWARE_KNOWLEDGE_DIR']=os.path.join(t,'kb')\n"
    "os.environ['AGENTWARE_CONFIG_HOME']=os.path.join(t,'cfg')\n"
    "os.makedirs(os.environ['AGENTWARE_KNOWLEDGE_DIR'], exist_ok=True)\n"
    "sys.path.insert(0, os.path.join(root,'scripts')); sys.path.insert(0, root)\n"
    "sys.path.insert(0, os.path.join(root,'tests'))\n"
    "from importlib.machinery import SourceFileLoader\n"
    "m=SourceFileLoader('agentware_cli', os.path.join(root,'scripts','agentware')).load_module()\n"
    "import _fixtures\n"
    "_fixtures.attach_lib_resolver(m)\n"
    "sys.stdout.write(json.dumps(sorted(n for n in json.loads(sys.argv[1]) if hasattr(m,n))))\n"
)


def reachable_symbols(root, names):
    """Which of `names` resolve on the CLI module at `root` (subprocess).

    Monolith-aware in the terminal state -- see `_PROBE`.
    """
    proc = subprocess.run([sys.executable, "-c", _PROBE % os.path.realpath(root),
                           json.dumps(sorted(names))],
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise SystemExit("CLI symbol probe failed for %s (rc=%d)\n%s"
                         % (root, proc.returncode, proc.stderr[-2000:]))
    return set(json.loads(proc.stdout))


def diff_node_ids(frozen_local, frozen_donor, pending, live):
    """PURE: frozen node ids vs what a destination actually collects.

    Kept free of I/O so the gate's semantics can be tested directly on
    synthetic sets instead of only through a multi-second real discovery.

    RESOLVED THROUGH THE TASK-58 RELOCATION LEDGER. A node id embeds its module
    path, so layering the corpus changes every id in a moved file. `resolves()`
    retries a missing id at the module(s) `_test_layout.RELOCATIONS` records as
    that module's successors -- a RECORDED move, never a guess: a module absent
    from the ledger is not relocated, and an id whose tail no successor collects
    is still LOST. `pending` is checked against `live` DIRECTLY (no relocation):
    a donor id may only be dropped from the backlog when it genuinely collects.
    """
    out = []
    for nid in sorted(set(frozen_local)):
        if not resolves(nid, live):
            out.append("LOST node id: %s" % nid)
    for nid in sorted(set(frozen_donor) - set(pending)):
        if not resolves(nid, live):
            out.append("LOST donor node id: %s" % nid)
    for nid in sorted(set(pending) & set(live)):
        out.append("RATCHET: pending node id now resolves, drop it: %s" % nid)
    return out


def diff_symbols(all_symbols, pending, live):
    """PURE: frozen CLI symbols vs what resolves on a destination CLI module."""
    out = []
    for name in sorted((set(all_symbols) - set(pending)) - set(live)):
        out.append("UNREACHABLE CLI symbol: %s" % name)
    for name in sorted(set(pending) & set(live)):
        out.append("RATCHET: pending CLI symbol now resolves, drop it: %s" % name)
    return out


def _check_node_ids(fx, fixture_path, root, violations):
    live, errors = collect_node_ids(root)
    if errors:
        violations.append("LOAD-ERROR: %s" % errors[:3])
    violations.extend(diff_node_ids(
        load_node_ids(fixture_path, "local")["node_ids"],
        load_node_ids(fixture_path, "donor")["node_ids"],
        fx["node_ids"]["pending_in_destination"], live))
    return set(live)


def _check_symbols(fx, root, violations):
    syms = fx["cli_symbols"]
    violations.extend(diff_symbols(
        set(syms["by_symbol"]), syms["pending_in_destination"],
        reachable_symbols(root, set(syms["by_symbol"]))))


def unsatisfied_assets(fx, root):
    """{asset key: why} for every frozen non-test asset NOT satisfied at `root`.

    One function serves both callers: derivation measures it against the local
    tree to seed the ratchet, and the gate measures it against the destination.
    Deriving the backlog with the same code that gates it is what stops the two
    from drifting into disagreement.
    """
    bad = {}
    bad.update(_bad_executables(fx, root))
    bad.update(_bad_templates(fx, root))
    bad.update(_bad_catalogs_and_families(fx, root))
    return bad


def _bad_executables(fx, root):
    bad = {}
    for rel in fx["executables"]:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            bad["executable:" + rel] = "MISSING executable: %s" % rel
        elif not os.access(path, os.X_OK):
            # Mode 0644 is the silent failure task 25 warns about: eleven `-x`
            # guards in the orchestrator FAIL OPEN, so nothing else turns red.
            bad["executable:" + rel] = "NOT EXECUTABLE: %s" % rel
    return bad


def _bad_templates(fx, root):
    bad = {}
    for name, row in fx["templates"].items():
        path = os.path.join(root, "templates", name)
        if not os.path.isfile(path):
            bad["template:" + name] = "MISSING template: %s" % name
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        lost = [a for a in row["anchors"] if a not in text]
        if lost:
            bad["template:" + name] = "template %s lost anchor(s) %r" % (name, lost)
    return bad


def _bad_catalogs_and_families(fx, root):
    bad = {}
    for name in fx["catalogs"]:
        if not os.path.isfile(os.path.join(root, "catalog", name)):
            bad["catalog:" + name] = "MISSING catalog: %s" % name
    for family, row in fx["fixture_families"].items():
        path = os.path.join(root, row["path"])
        exists = os.path.isdir(path) if row["kind"] == "dir" else os.path.isfile(path)
        if not exists:
            bad["fixture_family:" + family] = ("MISSING fixture family: %s (%s)"
                                               % (family, row["path"]))
    return bad


def _check_assets(fx, root, violations):
    pending = set(fx["assets"]["pending_in_destination"])
    bad = unsatisfied_assets(fx, root)
    for key in sorted(set(bad) - pending):
        violations.append(bad[key])
    for key in sorted(pending - set(bad)):
        violations.append("RATCHET: pending asset now satisfied, drop it: %s" % key)


def _check_destinations_free(fx, root, live_ids, violations):
    """Pinned destinations must be distinct and not already occupied by a
    DIFFERENT module than the one pinned to them."""
    live_mods = {module_of(n) for n in live_ids}
    seen = {}
    for rel, row in fx["test_path_collisions"]["rows"].items():
        for side, dest in (row.get("destinations") or {}).items():
            if dest in seen:
                violations.append("destination %s pinned twice (%s, %s)"
                                  % (dest, seen[dest], "%s/%s" % (rel, side)))
            seen[dest] = "%s/%s" % (rel, side)
            mod = dest[:-3].replace("/", ".")
            src = rel[:-3].replace("/", ".")
            if mod in live_mods and src in live_mods:
                violations.append("destination %s occupied while source %s still exists"
                                  % (dest, rel))


def check_destination(fixture_path, root):
    with open(fixture_path) as fh:
        fx = json.load(fh)
    root = os.path.realpath(root)
    violations = []
    live_ids = _check_node_ids(fx, fixture_path, root, violations)
    _check_symbols(fx, root, violations)
    _check_assets(fx, root, violations)
    _check_destinations_free(fx, root, live_ids, violations)
    return violations


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None):
    here = os.path.dirname(os.path.realpath(__file__))
    repo = os.path.dirname(here)
    ap = argparse.ArgumentParser(description="task-9 preserved-surface freeze")
    ap.add_argument("--local-root", default=repo)
    ap.add_argument("--donor-root", default="/tmp/aw-scratch/Agentware")
    ap.add_argument("--out")
    ap.add_argument("--verify")
    ap.add_argument("--check-destination")
    ap.add_argument("--fixture",
                    default=os.path.join(here, "fixtures", "preserved-surface.json"))
    args = ap.parse_args(argv)

    if args.out:
        fx = derive(args.local_root, args.donor_root, args.out)
        print("%d local node ids, %d donor, %d union, %d shared; %d CLI symbols "
              "(%d pending); %d executables, %d templates, %d catalogs, "
              "%d fixture families; %d collision rows costing %d local ids"
              % (fx["node_ids"]["local"]["count"], fx["node_ids"]["donor"]["count"],
                 fx["node_ids"]["union"], fx["node_ids"]["shared"],
                 fx["cli_symbols"]["count"],
                 len(fx["cli_symbols"]["pending_in_destination"]),
                 len(fx["executables"]), len(fx["templates"]), len(fx["catalogs"]),
                 len(fx["fixture_families"]),
                 len(fx["test_path_collisions"]["rows"]),
                 fx["test_path_collisions"]["total_local_ids_destroyed_by_blanket_copy"]))
        return 0

    if args.verify:
        problems = verify(args.verify)
        for p in problems:
            print("[FAIL] %s" % p)
        if not problems:
            print("[OK] %s: internally consistent" % relativise(args.verify))
        return 1 if problems else 0

    if args.check_destination:
        violations = check_destination(args.fixture, args.check_destination)
        for v in violations[:40]:
            print("[FAIL] %s" % v)
        if violations:
            print("[FAIL] %d violation(s) at %s"
                  % (len(violations), relativise(args.check_destination)))
            return 1
        with open(args.fixture) as fh:
            fx = json.load(fh)
        print("[OK] destination %s: nothing frozen was lost "
              "(%d node ids, %d CLI symbols, %d assets pending), head=%s"
              % (relativise(args.check_destination),
                 len(fx["node_ids"]["pending_in_destination"]),
                 len(fx["cli_symbols"]["pending_in_destination"]),
                 len(fx["assets"]["pending_in_destination"]),
                 git_head(args.check_destination) or "?"))
        return 0

    ap.error("one of --out / --verify / --check-destination is required")


if __name__ == "__main__":
    raise SystemExit(main())
