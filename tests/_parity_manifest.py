"""Task-10 PARITY MANIFEST: derivation, verification and the CLI.

The `check_*` half lives in `tests/_parity_gate_checks.py` and is re-exported
here, so callers see one module while neither file sits against the plan's
600-line `tests/**` ceiling that task 79 still has to extend.

Modes
-----
  --out <path>              regenerate the manifest from its sources
  --verify <path>           recompute every count and side attribution from the body
  --check                   THE GATE: coverage, matrix, probes, ratchet
  --stamp-ratchet <path>    re-stamp the per-dimension floors and pending list

Sources
-------
The manifest is a BUILD ARTIFACT, never hand-edited. Its generated tier is
re-derived from the four binding fixtures (surface union, env/config parity,
preserved-surface freeze and the two node-id sets); its curated tier comes from
`tests/_parity_curated.py`. `--out` is therefore reproducible, and the suite
asserts the committed copy equals a fresh derivation — a row cannot be edited
into agreement with a capability that regressed.

The ratchet lives in a SEPARATE file on purpose. A per-dimension row count stored
inside the manifest is trivially self-consistent: deleting a row and its count in
one edit leaves nothing to compare against. Split apart, deleting any row drops
that dimension below its committed floor and the gate names it.

Stdlib only. Never writes to the real knowledge base.
"""

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from _parity_curated import CURATED_ROWS  # noqa: E402
from _parity_gate_checks import (  # noqa: E402,F401  (re-exported public surface)
    check, check_curated, check_dimensions, check_generated, check_ratchet,
    check_shape, check_statuses, test_module_path)
from _parity_schema import (  # noqa: E402
    DIMENSIONS, SCHEMA, STATUSES, STATUS_MODES, VERIFICATIONS)

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
MANIFEST = os.path.join(FIXTURES, "parity-manifest.json")
RATCHET = os.path.join(FIXTURES, "parity-ratchet.json")

#: An `excluded` row is a claim that something is deliberately gone. The claim is
#: worthless without a runnable refutation, so every exclusion carried over from
#: a source fixture must be adjudicated HERE, by row id, before it can ship. An
#: unadjudicated exclusion halts the derivation rather than emitting a probe-less
#: row (same discipline as the task-8 capture halting on an unadjudicated
#: dynamic key template).
EXCLUSION_PROBES = {
    "surface-flag:recall:--all-teams": {
        "kind": "cli-absent",
        "argv": ["recall", "parity-probe", "--all-teams"],
        "expect": "unrecognized arguments",
    },
}

#: The module plan task 15 retires: a one-shot, environment-specific KB
#: auto-repoint migration that hard-coded two checkout basenames and shelled out
#: to a vendor workspace tool. Everything generic inside it — the "is this
#: directory safe to point the KB at?" guard — moved DOWN into the core
#: knowledge-dir layout module instead, so only the environment-specific half is
#: excluded. Its absence is the probe for every row below.
_RETIRED_REPOINT_MODULE = "scripts/agentware_lib/features/dreamrepoint.py"
_RETIRED_REPOINT_PROBE = {"kind": "path-absent", "path": _RETIRED_REPOINT_MODULE}

#: GENERATED rows whose capability the merged tree deliberately does NOT carry.
#: A generated row is `required` by construction — it is re-derived from a binding
#: fixture that froze the donor tree as it was. When a plan task DELETES the thing
#: a frozen row describes, leaving the row `required` makes the ledger assert the
#: opposite of the truth, and the deletion becomes invisible until some later gate
#: fails for an unrelated-looking reason. Adjudicate it HERE, by row id, with the
#: same reason/approver/follow_up/probe an excluded row always carries; `derive`
#: asserts every id matches exactly one derived row, so a stale adjudication is a
#: hard error rather than dead weight.
GENERATED_EXCLUSIONS = {
    "env:AGENTWARE_REPOINT_AUTOPROVISION": {
        "reason": "the opt-in autoprovision key for the retired repoint migration. "
                  "Its ONLY read sites were inside that module, and the workspace "
                  "provisioner it gated is an environment-specific tool this "
                  "vendor-neutral package must not shell out to.",
        "approver": "plan task 15 (operator-approved self-extension plan)",
        "follow_up": "none: the capability is environment-specific by construction. "
                     "It stays listed in the env fixture's shrink-only "
                     "pending_in_destination, which is now permanent for this key.",
        "absence_probe": _RETIRED_REPOINT_PROBE,
    },
    "tests:tests.unit.features.test_dreamrepoint": {
        "reason": "the granular unit test OF the retired repoint module: 26 donor "
                  "node ids that cannot survive their subject. The generic half of "
                  "the module (the target guard) kept its coverage — it moved to "
                  "the core layout module's own unit test, and the guard is "
                  "additionally gated end-to-end by tests/_kbtarget_gate.py.",
        "approver": "plan task 15 (operator-approved self-extension plan)",
        "follow_up": "task 58 reconciles the frozen node-id union; these 26 ids, "
                     "plus the 14 tests.test_dream.DreamKbRepointTests ids deleted "
                     "with them (40 in total), are the adjudicated subtraction it "
                     "must honour.",
        "absence_probe": _RETIRED_REPOINT_PROBE,
    },
}


def apply_generated_exclusions(rows):
    """Convert adjudicated generated rows to `excluded`. Returns the rows.

    Halts on an adjudication that matches no row: an exclusion nobody can see is
    the same failure mode as an exclusion nobody adjudicated.
    """
    by_id = {row["id"]: row for row in rows}
    for rid, adjudication in sorted(GENERATED_EXCLUSIONS.items()):
        row = by_id.get(rid)
        if row is None:
            raise SystemExit("stale generated exclusion, no such row: %s" % rid)
        row["status"] = "excluded"
        row["verification"] = "absence_probe"
        row.update(adjudication)
    return rows


def load_json(path):
    with open(path) as fh:
        return json.load(fh)


def git_head(root):
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "--short=12", "HEAD"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _sides(value):
    """Normalise a source fixture's side attribution to a sorted list."""
    if isinstance(value, list):
        return sorted(set(value))
    if value == "both":
        return ["donor", "local"]
    return [value]


def _node_id(path):
    return ".".join(path) if path else "<root>"


# --------------------------------------------------------------------------
# generated tier
# --------------------------------------------------------------------------

def surface_rows(union):
    """One row per union parser node, plus its exclusions and adjudications."""
    rows = []
    for node in union["nodes"]:
        name = _node_id(node["path"])
        rows.append({
            "id": "surface:%s" % name,
            "dimension": "cli_surface",
            "capability": "parser node `agentware %s` with %d actions"
                          % (" ".join(node["path"]) or "(root)", len(node["actions"])),
            "sides": _sides(node["sides"]),
            "status": "required",
            "verification": "generated",
        })
    for exc in union["exclusions"]:
        flag = (exc["option_strings"] or [exc["dest"]])[0]
        rid = "surface-flag:%s:%s" % (_node_id(exc["node"]), flag)
        if rid not in EXCLUSION_PROBES:
            raise SystemExit("unadjudicated exclusion, no absence probe: %s" % rid)
        rows.append({
            "id": rid,
            "dimension": "cli_surface",
            "capability": "flag `%s` on `agentware %s`"
                          % (flag, " ".join(exc["node"])),
            "sides": ["donor"],
            "status": "excluded",
            "verification": "absence_probe",
            "reason": exc["reason"],
            "approver": exc["approver"],
            "follow_up": exc["follow_up"],
            "absence_probe": EXCLUSION_PROBES[rid],
        })
    for adj in union["adjudications"]:
        loser = "local" if adj["winner"] == "donor" else "donor"
        flag = (adj["option_strings"] or [adj["dest"]])[0]
        rows.append({
            "id": "surface-adjudication:%s:%s:%s"
                  % (_node_id(adj["node"]), flag, adj["field"]),
            "dimension": "cli_surface",
            "capability": "the %s tree's %s for `%s` on `agentware %s`, superseded "
                          "by the decided value %s"
                          % (loser, adj["field"], flag, " ".join(adj["node"]),
                             json.dumps(adj["decided"])),
            "sides": [loser],
            "status": "superseded",
            "verification": "generated",
            "superseded_by": "surface:%s" % _node_id(adj["node"]),
        })
    return rows


def env_rows(env):
    """One row per environment variable / config key, either side."""
    rows = []
    for key in env["keys"]:
        traits = [key["namespace"]]
        if key.get("config_key"):
            traits.append("config key")
        if key.get("kill_switch"):
            traits.append("kill switch")
        if key.get("flag"):
            traits.append("flag %s" % key["flag"])
        rows.append({
            "id": "env:%s" % key["key"],
            "dimension": "env_config",
            "capability": "%s (%s)" % (key["key"], ", ".join(traits)),
            "sides": _sides(key["sides"]),
            "status": "required",
            "verification": "generated",
        })
    return rows


def _module_of(node_id):
    return ".".join(node_id.split(".")[:-2])


def _module_counts(ids):
    counts = {}
    for node_id in ids:
        module = _module_of(node_id)
        counts[module] = counts.get(module, 0) + 1
    return counts


def test_corpus_rows(preserved, local_ids, donor_ids):
    """One row per test MODULE on either side, plus one per path collision."""
    local = _module_counts(local_ids)
    donor = _module_counts(donor_ids)
    rows = []
    for module in sorted(set(local) | set(donor)):
        sides = [s for s, table in (("donor", donor), ("local", local))
                 if module in table]
        rows.append({
            "id": "tests:%s" % module,
            "dimension": "test_corpus",
            "capability": "test module %s (%d local, %d donor node ids)"
                          % (module, local.get(module, 0), donor.get(module, 0)),
            "sides": sides,
            "status": "required",
            "verification": "generated",
        })
    collisions = preserved["test_path_collisions"]["rows"]
    for path in sorted(collisions):
        row = collisions[path]
        rows.append({
            "id": "tests-collision:%s" % path,
            "dimension": "test_corpus",
            "capability": "same-path collision on %s: %d local ids destroyed by a "
                          "blanket copy, resolution %s"
                          % (path, row["local_ids_destroyed_by_same_path_copy"],
                             row["resolution"]),
            "sides": ["donor", "local"],
            "status": "required",
            "verification": "generated",
        })
    return rows


ASSET_KINDS = (("executables", "exec"), ("templates", "template"),
               ("catalogs", "catalog"), ("fixture_families", "fixture"))


def asset_rows(preserved):
    """One row per executable, template, catalog and fixture family."""
    rows = []
    for section, label in ASSET_KINDS:
        for name in sorted(preserved[section]):
            entry = preserved[section][name]
            rows.append({
                "id": "asset:%s:%s" % (label, name),
                "dimension": "assets",
                "capability": "%s %s" % (label, name),
                "sides": _sides(entry["sides"]),
                "status": "required",
                "verification": "generated",
            })
    return rows


def _derived_donor_delta(union, env, preserved):
    """Re-derive donor_delta for the four generated dimensions from source."""
    return {
        "cli_surface": union["sides"]["nodes"]["donor_only"] > 0,
        "env_config": env["counts"]["donor_only"] > 0,
        "test_corpus": len(preserved["node_ids"]["pending_in_destination"]) > 0,
        "assets": len(preserved["assets"]["pending_in_destination"]) > 0,
    }


def derive(root=REPO):
    """Rebuild the whole manifest from its four fixtures and the curated table."""
    fixtures = os.path.join(root, "tests", "fixtures")
    union = load_json(os.path.join(fixtures, "surface-union.json"))
    env = load_json(os.path.join(fixtures, "env-config-parity.json"))
    preserved = load_json(os.path.join(fixtures, "preserved-surface.json"))
    local_ids = load_json(os.path.join(fixtures, "nodeids-local.json"))["node_ids"]
    donor_ids = load_json(os.path.join(fixtures, "nodeids-donor.json"))["node_ids"]

    rows = (surface_rows(union) + env_rows(env)
            + test_corpus_rows(preserved, local_ids, donor_ids)
            + asset_rows(preserved) + [dict(r) for r in CURATED_ROWS])
    apply_generated_exclusions(rows)
    rows.sort(key=lambda r: (r["dimension"], r["id"]))

    derived = _derived_donor_delta(union, env, preserved)
    dimensions = []
    for dim in DIMENSIONS:
        entry = dict(dim)
        if dim["name"] in derived:
            entry["donor_delta_derived"] = derived[dim["name"]]
        dimensions.append(entry)

    return {
        "schema": SCHEMA,
        "generated_by": "tests/_parity_manifest.py --out",
        "feature": "260813-modular-core-adoption",
        "task": 10,
        "statuses": list(STATUSES),
        "verifications": list(VERIFICATIONS),
        "status_modes": {k: list(v) for k, v in STATUS_MODES.items()},
        "derived_from": {
            "surface_union": "tests/fixtures/surface-union.json",
            "env_config": "tests/fixtures/env-config-parity.json",
            "preserved_surface": "tests/fixtures/preserved-surface.json",
            "curated_table": "tests/_parity_curated.py",
        },
        "counts": summarise(rows),
        "dimensions": dimensions,
        "rows": rows,
    }


def summarise(rows):
    """Every published count, recomputed from the rows themselves."""
    by_dimension = {}
    by_status = {s: 0 for s in STATUSES}
    by_verification = {v: 0 for v in VERIFICATIONS}
    donor_side = {}
    for row in rows:
        by_dimension[row["dimension"]] = by_dimension.get(row["dimension"], 0) + 1
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
        by_verification[row["verification"]] = \
            by_verification.get(row["verification"], 0) + 1
        if "donor" in row["sides"]:
            donor_side[row["dimension"]] = donor_side.get(row["dimension"], 0) + 1
    return {
        "rows": len(rows),
        "by_dimension": by_dimension,
        "by_status": by_status,
        "by_verification": by_verification,
        "donor_side_rows_by_dimension": donor_side,
    }


# --------------------------------------------------------------------------
# verification: the header may never disagree with the body
# --------------------------------------------------------------------------

def verify(manifest):
    """Recompute every published count from the rows. Returns violations."""
    bad = []
    if manifest.get("schema") != SCHEMA:
        bad.append("schema is %r, expected %r" % (manifest.get("schema"), SCHEMA))
    recomputed = summarise(manifest["rows"])
    if recomputed != manifest["counts"]:
        bad.append("counts header contradicts the body: header=%s recomputed=%s"
                   % (json.dumps(manifest["counts"], sort_keys=True),
                      json.dumps(recomputed, sort_keys=True)))
    for dim in manifest["dimensions"]:
        declared = dim["donor_delta"]
        got = dim.get("donor_delta_derived")
        if got is not None and got != declared:
            bad.append("dimension %s declares donor_delta=%r but its source "
                       "fixture derives %r" % (dim["name"], declared, got))
    ordered = sorted(manifest["rows"], key=lambda r: (r["dimension"], r["id"]))
    if [r["id"] for r in ordered] != [r["id"] for r in manifest["rows"]]:
        bad.append("rows are not in (dimension, id) order")
    return bad



def stamp_ratchet(manifest, root=REPO):
    pending = []
    for row in manifest["rows"]:
        if row["verification"] != "curated":
            continue
        dotted = row.get("evidence", {}).get("test")
        if dotted and not os.path.exists(test_module_path(root, dotted)):
            pending.append(row["id"])
    return {
        "schema": SCHEMA,
        "generated_by": "tests/_parity_manifest.py --stamp-ratchet",
        "stamped_at_head": git_head(root),
        "note": "Per-dimension row floors live OUTSIDE the manifest on purpose: a "
                "count stored beside the rows it counts is trivially self-"
                "consistent, so deleting a row and its count in one edit would "
                "leave nothing to compare against. pending_verification is the "
                "shrink-only backlog of curated rows whose named test does not "
                "exist yet; it may only get shorter.",
        "row_floor": dict(manifest["counts"]["by_dimension"]),
        "total_floor": manifest["counts"]["rows"],
        "pending_verification": sorted(pending),
    }


def _write(path, payload):
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="parity manifest: derive, verify, gate")
    ap.add_argument("--out", metavar="PATH", nargs="?", const=MANIFEST)
    ap.add_argument("--verify", metavar="PATH", nargs="?", const=MANIFEST)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--stamp-ratchet", metavar="PATH", nargs="?", const=RATCHET)
    ap.add_argument("--root", default=REPO)
    ap.add_argument("--no-probes", action="store_true")
    args = ap.parse_args(argv)

    if args.out:
        _write(args.out, derive(args.root))
        print("[OK] wrote %s" % os.path.relpath(args.out, args.root))
    if args.stamp_ratchet:
        _write(args.stamp_ratchet, stamp_ratchet(derive(args.root), args.root))
        print("[OK] wrote %s" % os.path.relpath(args.stamp_ratchet, args.root))
    if args.verify:
        bad = verify(load_json(args.verify))
        return _report("verify", bad, os.path.relpath(args.verify, args.root))
    if args.check:
        bad = check(load_json(MANIFEST), load_json(RATCHET), args.root,
                    run_probes=not args.no_probes)
        return _report("parity gate", bad, os.path.relpath(args.root, args.root))
    return 0


def _report(label, bad, subject):
    if bad:
        print("[FAIL] %s: %d violation(s)" % (label, len(bad)))
        for line in bad:
            print("  - %s" % line)
        return 1
    print("[OK] %s: %s" % (label, subject))
    return 0


if __name__ == "__main__":
    sys.exit(main())
