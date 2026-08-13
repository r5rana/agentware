#!/usr/bin/env python3
"""Derive the ENV-VAR + CONFIG-KEY parity fixture from both trees, and gate it.

Feature 260813-modular-core-adoption, task 8 -- the MERGE + GATE half. The
per-tree scanning lives in ``tests/_env_config_scan.py``; this module merges the
two captures, attributes every key to a side, and ships the two gates.

Why this exists
---------------
Task 6/7 froze the CLI surface as an argparse OBJECT dump, and every downstream
promotion gate compares against it. But an argparse dump can only see what is
DECLARED on a parser. It is completely blind to the other input surface this
package has: the environment variables and ``config.env`` keys that steer
resolution, gating and routing.

That surface is where the silent losses live. ``AGENTWARE_DISABLE_RELEASE_GATE``
has no flag, no subparser and no help text -- it is read in two languages at
four sites, and a module landing without one of those reads disables a gate
while changing NO verb, NO flag, NO count and NO test node id. Same shape for
``AGENTWARE_CONFIG_HOME`` (donor-only, and what makes task 65's global-config
isolation possible at all) and for ``AGENTWARE_ORG_ROOT`` / ``AGENTWARE_TEAM``
(donor-only, the whole org kernel).

The two gates
-------------
``--check-destination`` asserts every recorded key still resolves to at least
one site in the destination tree. Keys that do not yet -- the donor-only
backlog awaiting its port -- must be a SUBSET of the frozen
``pending_in_destination`` ratchet. So a key that resolves today and stops
resolving tomorrow is a REGRESSION, while the backlog only ever shrinks, and
task 54/79 can require it to reach empty.

``--verify`` re-asserts the fixture's own shape: side attribution, the
default/flag/read-site fields, and that every kill switch carries an explicit
fail-safe default (a switch whose default is missing or truthy would ship a
gate that is off by accident).

Usage:
    python3 tests/_env_config_capture.py --out tests/fixtures/env-config-parity.json
    python3 tests/_env_config_capture.py --verify tests/fixtures/env-config-parity.json
    python3 tests/_env_config_capture.py --check-destination . \
        --fixture tests/fixtures/env-config-parity.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _env_config_scan import (  # noqa: E402
    DYNAMIC_EXPANSIONS, NAMESPACE, capture_tree,
)

SCHEMA_VERSION = 1

# Tree roots. The donor is read-only; both are recorded home-relativised.
DEFAULT_LOCAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DONOR_ROOT = "/tmp/aw-scratch/Agentware"

# Non-derivable key -> config-verb flag stems. The derived rule below handles
# the regular cases (AGENTWARE_DREAM -> --set-dream / --dream-only); these are
# the ones whose flag was named differently from the key.
FLAG_ALIASES = {
    "AGENTWARE_KB_AUTOCOMMIT": ("autocommit", "kb_autocommit"),
    "AGENTWARE_KB_PUSH": ("push", "kb_push"),
    "AGENTWARE_RETRIEVAL_MODE": ("retrieval", "retrieval_mode"),
    "AGENTWARE_UPDATE": ("updater",),
    "AGENTWARE_EMBEDDER_BACKEND": ("embedder",),
}

# A kill switch is a key whose PRESENCE disables a gate. Mechanically: the
# DISABLE_/SKIP_ families. The reason-carrying companion of a switch is not
# itself a switch.
KILL_SWITCH_RE = re.compile(r"^AGENTWARE_(DISABLE|SKIP)_")
NOT_A_SWITCH = ("AGENTWARE_SKIP_ADVERSARIAL_REVIEW_REASON",)

# Values that mean "unset" for a kill switch, i.e. the gate stays ENABLED.
FAIL_SAFE_DEFAULTS = ("", "0", "off", "no", "false", None)

INCLUSION_RULE = (
    "AGENTWARE_* names read or written through an environment API in either "
    "language, plus any other name that can only come from the environment "
    "(Python: os.environ / os.getenv or a copy of it; shell: UPPERCASE, read "
    "but never assigned anywhere in the tree's shell surface, excluding bash "
    "specials). A name MENTION is not a read -- detection is syntactic. "
    "Dynamically constructed families (AGENTWARE_%s_%s) are expanded through a "
    "closed table; an unadjudicated template halts the capture."
)


def rel_home(path):
    """Home-relativise so a committed fixture carries no operator path."""
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


def git_head(root):
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=15)
        return out.stdout.strip()[:12] if out.returncode == 0 else None
    except Exception:
        return None


# --- row fields --------------------------------------------------------------

def _explicit_defaults(sites):
    return sorted({s["default"] for s in sites if s["default"] is not None},
                  key=lambda v: (v is None, str(v)))


def _is_config_key(sites):
    """True iff the key is resolvable from config.env, not just the environment."""
    for site in sites:
        form = site["form"]
        if form.endswith("_config_key()") or \
                form.startswith("_resolve_key_env_then_config"):
            return True
        if form.startswith("template:"):
            spec = DYNAMIC_EXPANSIONS.get(form[len("template:"):], {})
            if spec.get("config_key"):
                return True
    return False


def _flag_for(key, dests, options):
    """(flag, dests, source) for the CLI flag that can SET this key, if any."""
    if not key.startswith(NAMESPACE):
        return None, [], "none"
    stems = [key[len(NAMESPACE):].lower()]
    stems.extend(FLAG_ALIASES.get(key, ()))
    source = "curated" if key in FLAG_ALIASES else "derived"
    found = []
    for stem in stems:
        for candidate in ("set_%s" % stem, "%s_only" % stem, stem):
            if candidate in dests:
                found.append(candidate)
        dashed = "--" + stem.replace("_", "-")
        if dashed in options:
            found.append(dashed)
    found = sorted(set(found))
    if not found:
        return None, [], "none"
    setter = [f for f in found if f.startswith("set_")]
    return (setter[0] if setter else found[0]), found, source


def _union_surface_dests(fixture_path):
    """dests + option strings from the task-7 union fixture (flag matching)."""
    if not os.path.exists(fixture_path):
        return set(), set()
    with open(fixture_path, encoding="utf-8") as handle:
        data = json.load(handle)
    dests, options = set(), set()
    for node in data.get("nodes", []):
        for action in node.get("actions", []):
            if action.get("dest"):
                dests.add(action["dest"])
            for opt in action.get("option_strings") or []:
                options.add(opt)
    return dests, options


def _row(key, l_sites, d_sites, dests, options):
    sides = [s for s, v in (("local", l_sites), ("donor", d_sites)) if v]
    all_sites = l_sites + d_sites
    reads = [s for s in all_sites if s["kind"] == "read"]
    writes = [s for s in all_sites if s["kind"] == "write"]
    defaults = _explicit_defaults(all_sites)
    kill = bool(KILL_SWITCH_RE.match(key)) and key not in NOT_A_SWITCH
    flag, flag_dests, flag_source = _flag_for(key, dests, options)
    return {
        "key": key,
        "namespace": "agentware" if key.startswith(NAMESPACE) else "external",
        "sides": sides,
        "side": "shared" if len(sides) == 2 else sides[0] + "-only",
        "direction": "both" if reads and writes else "read" if reads else "write",
        "config_key": _is_config_key(all_sites),
        "dynamic": any(s["form"].startswith("template:") for s in all_sites),
        "kill_switch": kill,
        "unset_means": "gate enabled (fail-safe)" if kill else None,
        "default": defaults[0] if len(defaults) == 1 else None,
        "default_divergent": len(defaults) > 1,
        "defaults_observed": defaults,
        "has_explicit_default": bool(defaults),
        "flag": flag,
        "flag_dests": flag_dests,
        "flag_source": flag_source,
        "read_sites": {"local": [s for s in l_sites if s["kind"] == "read"],
                       "donor": [s for s in d_sites if s["kind"] == "read"]},
        "write_sites": {"local": [s for s in l_sites if s["kind"] == "write"],
                        "donor": [s for s in d_sites if s["kind"] == "write"]},
    }


def _counts(rows):
    return {
        "keys": len(rows),
        "side_attributed": sum(1 for r in rows if r["sides"]),
        "shared": sum(1 for r in rows if r["side"] == "shared"),
        "local_only": sum(1 for r in rows if r["side"] == "local-only"),
        "donor_only": sum(1 for r in rows if r["side"] == "donor-only"),
        "kill_switches": sum(1 for r in rows if r["kill_switch"]),
        "config_keys": sum(1 for r in rows if r["config_key"]),
        "with_flag": sum(1 for r in rows if r["flag"]),
        "dynamic": sum(1 for r in rows if r["dynamic"]),
        "agentware_namespace": sum(1 for r in rows if r["namespace"] == "agentware"),
        "external_namespace": sum(1 for r in rows if r["namespace"] == "external"),
        "read_sites": sum(len(r["read_sites"]["local"]) + len(r["read_sites"]["donor"])
                          for r in rows),
    }


def derive(local_root, donor_root, union_fixture):
    local = capture_tree(local_root)
    donor = capture_tree(donor_root)
    dests, options = _union_surface_dests(union_fixture)
    rows = [_row(key, local.get(key, []), donor.get(key, []), dests, options)
            for key in sorted(set(local) | set(donor))]
    pending = sorted(r["key"] for r in rows if not r["read_sites"]["local"]
                     and not r["write_sites"]["local"])
    return {
        "schema_version": SCHEMA_VERSION,
        "binding": True,
        "feature": "260813-modular-core-adoption",
        "task": 8,
        "derived_from": {
            "local": {"root": rel_home(os.path.abspath(local_root)),
                      "git_head": git_head(local_root)},
            "donor": {"root": rel_home(os.path.abspath(donor_root)),
                      "git_head": git_head(donor_root)},
            "union_surface": os.path.relpath(union_fixture, local_root)
            if os.path.exists(union_fixture) else None,
        },
        "inclusion_rule": INCLUSION_RULE,
        "dynamic_expansions": DYNAMIC_EXPANSIONS,
        "counts": _counts(rows),
        "pending_in_destination": pending,
        "keys": rows,
    }


# --- gates -------------------------------------------------------------------

def check_destination(fixture, root):
    """Every key must resolve to >=1 site in the DESTINATION tree.

    Keys that do not yet (the donor-only backlog awaiting its port) must be a
    SUBSET of the frozen ``pending_in_destination`` ratchet -- so a key that
    resolves today and stops resolving tomorrow is a regression, while the
    backlog only ever shrinks.
    """
    live = capture_tree(root)
    pending = set(fixture.get("pending_in_destination", []))
    unresolved = sorted(r["key"] for r in fixture["keys"] if r["key"] not in live)
    problems = ["LOST: %s no longer resolves to any read site in %s" % (key, root)
                for key in unresolved if key not in pending]
    problems.extend("RATCHET: %s now resolves -- remove it from "
                    "pending_in_destination" % key
                    for key in sorted(pending) if key in live)
    return problems, unresolved


def check_kill_switches(fixture):
    problems = []
    switches = [r for r in fixture["keys"] if r["kill_switch"]]
    if not switches:
        problems.append("no kill-switch key recorded at all")
    for row in switches:
        if not row["has_explicit_default"]:
            problems.append("kill switch %s carries no explicit default"
                            % row["key"])
        elif row["default"] not in FAIL_SAFE_DEFAULTS:
            problems.append("kill switch %s defaults to %r, which is not "
                            "fail-safe" % (row["key"], row["default"]))
        if not row["unset_means"]:
            problems.append("kill switch %s does not record what unset means"
                            % row["key"])
    return problems


def _check_row(row):
    problems = []
    for field in ("default", "flag", "read_sites", "write_sites", "sides", "side"):
        if field not in row:
            problems.append("%s: missing field %s" % (row.get("key"), field))
    if problems:
        return problems
    if row["side"] not in ("shared", "local-only", "donor-only"):
        problems.append("%s: bad side %r" % (row["key"], row["side"]))
    if row["sides"] and not any(row[bucket][side]
                                for bucket in ("read_sites", "write_sites")
                                for side in ("local", "donor")):
        problems.append("%s: side-attributed but carries no site" % row["key"])
    for side in ("local", "donor"):
        if row["read_sites"][side] and side not in row["sides"]:
            problems.append("%s: has %s read sites but %s is not in sides"
                            % (row["key"], side, side))
    return problems


def verify(fixture, min_keys=36):
    problems = []
    if fixture.get("schema_version") != SCHEMA_VERSION:
        problems.append("schema_version %r != %r"
                        % (fixture.get("schema_version"), SCHEMA_VERSION))
    rows = fixture.get("keys", [])
    counts = fixture.get("counts", {})
    for name, value in _counts(rows).items():
        if counts.get(name) != value:
            problems.append("counts.%s %r != %r recomputed"
                            % (name, counts.get(name), value))
    attributed = [r for r in rows if r.get("sides")]
    if len(attributed) < min_keys:
        problems.append("only %d side-attributed keys, need >= %d"
                        % (len(attributed), min_keys))
    for row in rows:
        problems.extend(_check_row(row))
    problems.extend(check_kill_switches(fixture))
    if os.path.expanduser("~") in json.dumps(fixture):
        problems.append("fixture leaks the operator home path")
    return problems


# --- CLI ---------------------------------------------------------------------

def _cmd_check_destination(args):
    path = args.fixture or os.path.join(
        args.local_root, "tests", "fixtures", "env-config-parity.json")
    with open(path, encoding="utf-8") as handle:
        fixture = json.load(handle)
    problems, unresolved = check_destination(fixture, args.check_destination)
    for problem in problems:
        print("[FAIL] %s" % problem)
    print("[%s] destination %s: %d/%d keys resolve, %d pending"
          % ("FAIL" if problems else "OK", args.check_destination,
             len(fixture["keys"]) - len(unresolved), len(fixture["keys"]),
             len(unresolved)))
    return 1 if problems else 0


def _cmd_verify(args):
    with open(args.verify, encoding="utf-8") as handle:
        fixture = json.load(handle)
    problems = verify(fixture, args.min_keys)
    for problem in problems:
        print("[FAIL] %s" % problem)
    if problems:
        return 1
    counts = fixture["counts"]
    print("[OK] %s: %d keys (%d shared, %d local-only, %d donor-only), "
          "%d kill switches, %d config keys, %d with a CLI flag"
          % (args.verify, counts["keys"], counts["shared"], counts["local_only"],
             counts["donor_only"], counts["kill_switches"], counts["config_keys"],
             counts["with_flag"]))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--local-root", default=DEFAULT_LOCAL_ROOT)
    parser.add_argument("--donor-root", default=DEFAULT_DONOR_ROOT)
    parser.add_argument("--union-fixture", default=None)
    parser.add_argument("--out")
    parser.add_argument("--verify")
    parser.add_argument("--check-destination")
    parser.add_argument("--fixture")
    parser.add_argument("--min-keys", type=int, default=36)
    args = parser.parse_args(argv)

    if args.check_destination:
        return _cmd_check_destination(args)
    if args.verify:
        return _cmd_verify(args)

    union = args.union_fixture or os.path.join(
        args.local_root, "tests", "fixtures", "surface-union.json")
    fixture = derive(args.local_root, args.donor_root, union)
    text = json.dumps(fixture, indent=2, sort_keys=False) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)
    counts = fixture["counts"]
    print("%d keys, %d shared, %d local-only, %d donor-only, %d kill switches, "
          "%d read sites" % (counts["keys"], counts["shared"], counts["local_only"],
                             counts["donor_only"], counts["kill_switches"],
                             counts["read_sites"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
