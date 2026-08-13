"""Task-10 PARITY MANIFEST: THE GATE.

Split from `tests/_parity_manifest.py`, which owns derivation and verification,
so neither half sits against the plan's 600-line `tests/**` ceiling while task 79
still has to extend both. `_parity_manifest` re-exports every name here, so the
gate's public surface is one module either way.

Each `check_*` returns a list of violation strings and never raises: a gate that
crashes on malformed input reports nothing, and reporting nothing is exactly what
a broken manifest wants it to do.

Stdlib only. Never touches the real knowledge base.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from _parity_schema import (  # noqa: E402
    COMMON_FIELDS, DIMENSION_BY_NAME, EVIDENCE_FIELDS, SIDE_VALUES, STATUSES,
    STATUS_FIELDS, STATUS_MODES, VERIFICATION_FIELDS, VERIFICATIONS, run_probe)

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------

def _check_row(row, declared):
    """Schema, vocabulary and the status/verification matrix, for one row."""
    bad = []
    rid = row.get("id", "<no id>")
    missing = [f for f in COMMON_FIELDS if f not in row]
    if missing:
        return ["row %s missing required field(s): %s" % (rid, ", ".join(missing))]
    if row["status"] not in STATUSES:
        return ["row %s has unknown status %r" % (rid, row["status"])]
    if row["verification"] not in VERIFICATIONS:
        return ["row %s has unknown verification %r" % (rid, row["verification"])]
    if row["verification"] not in STATUS_MODES[row["status"]]:
        bad.append("row %s: status %s may not use verification %s (allowed: %s)"
                   % (rid, row["status"], row["verification"],
                      ", ".join(STATUS_MODES[row["status"]])))
    bad.extend(_row_field_violations(row, rid))
    bad.extend(_row_reference_violations(row, rid, declared))
    return bad


def _row_field_violations(row, rid):
    """The fields this row's status and verification mode oblige it to carry."""
    bad = []
    obliged = STATUS_FIELDS[row["status"]] + VERIFICATION_FIELDS[row["verification"]]
    for field in obliged:
        if not row.get(field):
            bad.append("row %s (%s/%s) is missing %s"
                       % (rid, row["status"], row["verification"], field))
    if row["verification"] == "curated":
        for field in EVIDENCE_FIELDS:
            if not row.get("evidence", {}).get(field):
                bad.append("row %s: curated evidence is missing %s" % (rid, field))
    return bad


def _row_reference_violations(row, rid, declared):
    """Side vocabulary and the dimension the row claims to belong to."""
    bad = []
    invalid = [s for s in row["sides"] if s not in SIDE_VALUES]
    if not row["sides"] or invalid:
        bad.append("row %s has invalid sides %r" % (rid, row["sides"]))
    if row["dimension"] not in declared:
        bad.append("row %s names undeclared dimension %r" % (rid, row["dimension"]))
    return bad


def check_shape(manifest):
    bad = []
    seen = set()
    declared = {d["name"] for d in manifest["dimensions"]}
    for row in manifest["rows"]:
        bad.extend(_check_row(row, declared))
        rid = row.get("id")
        if rid in seen:
            bad.append("duplicate row id: %s" % rid)
        seen.add(rid)
    return bad


def check_dimensions(manifest):
    """Every dimension owns a row; a donor delta owns a DONOR-SIDE row."""
    bad = []
    counts = manifest["counts"]["by_dimension"]
    donor_side = manifest["counts"]["donor_side_rows_by_dimension"]
    for dim in manifest["dimensions"]:
        name = dim["name"]
        if not counts.get(name):
            bad.append("dimension %s has ZERO rows: it is unclaimed, not clean"
                       % name)
            continue
        if dim["donor_delta"] and not donor_side.get(name):
            bad.append("dimension %s declares a donor delta (%s) but owns no "
                       "donor-side row: a one-sided merge here would be silent"
                       % (name, dim["donor_delta_evidence"]))
    declared = {d["name"] for d in manifest["dimensions"]}
    for name in sorted(set(counts) - declared):
        bad.append("rows claim undeclared dimension %s" % name)
    return bad


def check_statuses(manifest, root, run_probes=True):
    """Excluded rows must PROVE absence; superseded rows must resolve."""
    bad = []
    by_id = {r["id"]: r for r in manifest["rows"]}
    for row in manifest["rows"]:
        if row["status"] == "excluded" and row.get("absence_probe"):
            if not run_probes:
                continue
            ok, detail = run_probe(root, row["absence_probe"])
            if not ok:
                bad.append("row %s: absence probe FAILED to prove absence (%s)"
                           % (row["id"], detail))
        elif row["status"] == "superseded":
            target = by_id.get(row.get("superseded_by"))
            if target is None:
                bad.append("row %s is superseded_by %r, which is not a row"
                           % (row["id"], row.get("superseded_by")))
            elif target["status"] != "required":
                bad.append("row %s is superseded_by %s, whose status is %s, not "
                           "required" % (row["id"], target["id"], target["status"]))
    return bad


def check_generated(manifest, root):
    """A generated row is only as good as the gate that enforces its fixture."""
    bad = []
    declared = {d["name"]: d for d in manifest["dimensions"]}
    for row in manifest["rows"]:
        if row["verification"] != "generated":
            continue
        dim = declared.get(row["dimension"], DIMENSION_BY_NAME.get(row["dimension"]))
        if dim is None:
            bad.append("row %s is generated under undeclared dimension %s"
                       % (row["id"], row["dimension"]))
            continue
        for field in ("source", "gate"):
            rel = dim.get(field)
            if not rel:
                bad.append("row %s is generated but dimension %s declares no %s"
                           % (row["id"], dim["name"], field))
            elif not os.path.exists(os.path.join(root, rel)):
                bad.append("dimension %s names a missing %s: %s"
                           % (dim["name"], field, rel))
    return sorted(set(bad))


def test_module_path(root, dotted):
    return os.path.join(root, *dotted.split(".")) + ".py"


def check_curated(manifest, root, ratchet):
    """The shrink-only pending ratchet, live in BOTH directions.

    Since task 79 it also STATICALLY validates each row's runnable mutation
    probe -- its kind, its field set, that the path it names still exists, and
    that it is not the named test's own file. The sweep proves the probe
    reddens; this proves the probe still ADDRESSES the tree, in milliseconds,
    inside the gate CI already runs. A probe naming a moved file would otherwise
    be discovered only by the sweep, and its no-op reads exactly like a missing
    capability.
    """
    bad = []
    pending = set(ratchet.get("pending_verification", []))
    known = {r["id"] for r in manifest["rows"]}
    for row in manifest["rows"]:
        if row["verification"] != "curated":
            continue
        dotted = row.get("evidence", {}).get("test")
        bad.extend(_probe_violations(row, root, dotted))
        if not dotted:
            continue
        resolves = os.path.exists(test_module_path(root, dotted))
        if resolves and row["id"] in pending:
            bad.append("row %s now resolves (%s exists): remove it from "
                       "pending_verification, the ratchet only shrinks"
                       % (row["id"], dotted))
        elif not resolves and row["id"] not in pending:
            bad.append("row %s names test %s, which does not exist and is not in "
                       "pending_verification" % (row["id"], dotted))
    for rid in sorted(pending - known):
        bad.append("pending_verification lists %s, which is not a row" % rid)
    return bad


def _probe_violations(row, root, dotted):
    """One curated row's mutation probe, checked without running anything."""
    from _parity_mutations import spec_errors
    from _parity_sweep import _probe_target_errors
    spec = row.get("evidence", {}).get("probe")
    if not spec:
        return ["row %s carries no runnable mutation probe: its `mutation` is "
                "prose, and prose cannot refute a parity claim" % row["id"]]
    errors = spec_errors(spec, row["id"])
    if errors:
        return errors
    return _probe_target_errors(root, row["id"], spec, dotted)


def check_ratchet(manifest, ratchet):
    """Deleting any row drops its dimension below the committed floor."""
    bad = []
    counts = manifest["counts"]["by_dimension"]
    for name, floor in sorted(ratchet["row_floor"].items()):
        live = counts.get(name, 0)
        if live < floor:
            bad.append("RATCHET: dimension %s has %d rows, below its committed "
                       "floor of %d — %d row(s) were deleted"
                       % (name, live, floor, floor - live))
    total = manifest["counts"]["rows"]
    if total < ratchet["total_floor"]:
        bad.append("RATCHET: %d rows total, below the committed floor of %d"
                   % (total, ratchet["total_floor"]))
    return bad


def check(manifest, ratchet, root=REPO, run_probes=True):
    """THE GATE. Returns the full violation list; empty means pass."""
    from _parity_manifest import verify
    return (verify(manifest) + check_shape(manifest) + check_dimensions(manifest)
            + check_statuses(manifest, root, run_probes)
            + check_generated(manifest, root)
            + check_curated(manifest, root, ratchet)
            + check_ratchet(manifest, ratchet))

