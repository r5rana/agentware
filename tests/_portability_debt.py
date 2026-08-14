"""The portability gate's SHRINK-ONLY debt ratchet.

The five-surface gate is red on this tree today, and honestly so: the migration
deliberately committed donor-derived artifacts (the frozen node-id sets, the
donor test baseline, the staging exclusion policy) whose whole job is to name
donor surfaces, and later tasks neutralise them. A gate that stays red for
twenty tasks gets ignored, and "red" then hides the ONE finding that matters --
a NEW leak arriving today.

So the known findings are declared, per location, in a committed fixture, and
the gate subtracts them. The declaration is a ratchet, not an allowlist, and it
bites in BOTH directions:

  * a finding above a row's declared count, or at an undeclared location, is a
    failure -- new leaks cannot hide behind old ones;
  * a row that forgives findings the tree no longer has is ALSO a failure, so
    the file can only shrink. When the last row goes, the gate is simply green,
    which is what task 83 asserts.

Two things the debt file may NEVER do, enforced here and asserted by the suite:

  * forgive an ``operator_product`` finding. That class is zero tolerance by
    operator directive, with no allowlist and no exemption. A row claiming it is
    a schema violation, not a quiet pass.
  * carry the token text. Rows record surface, class, location, count, the task
    that owns the cleanup and why -- never the needle itself, because the
    ``environment`` needles are private by design (R-LOC-03) and this file is
    committed.

Stdlib only, read-only.
"""

import json
import os

try:
    from tests import _portability_gate as pg
except ImportError:  # pragma: no cover - direct-script invocation
    import _portability_gate as pg

HERE = os.path.dirname(os.path.realpath(__file__))

DEBT_SCHEMA = "agentware/portability-debt@1"
DEFAULT_DEBT = os.path.join(HERE, "fixtures", "portability-debt.json")

STALE = "stale_debt"
OVER = "over_debt"


def _row_key(row):
    return (row["surface"], row.get("class"), row["location"])


def bare_location(found):
    """A finding's location without its line number, so a row survives churn."""
    location = found["location"]
    if found["surface"] == pg.CONTENTS and ":" in location:
        head, _, tail = location.rpartition(":")
        if tail.isdigit():
            return head
    return location


def finding_key(found):
    return (found["surface"], found["class"], bare_location(found))


def validate_row(row, source):
    for field in ("surface", "location", "count", "owner", "reason"):
        if field not in row:
            raise ValueError("%s: debt row missing %r" % (source, field))
    if row["surface"] not in pg.SURFACES:
        raise ValueError("%s: unknown surface %r" % (source, row["surface"]))
    if row.get("class") == pg.ZERO_TOLERANCE:
        raise ValueError(
            "%s: the debt file may never forgive a %r finding -- that class is "
            "zero tolerance, with no allowlist and no exemption"
            % (source, pg.ZERO_TOLERANCE))
    if not isinstance(row["count"], int) or row["count"] < 1:
        raise ValueError("%s: debt row count must be a positive int" % source)
    for field in ("owner", "reason"):
        if not str(row.get(field) or "").strip():
            raise ValueError("%s: debt row %r must say who cleans it up and why"
                             % (source, field))
    if "token" in row:
        raise ValueError(
            "%s: debt rows must not carry token text; the environment needles "
            "are private and this file is committed" % source)
    return row


def load_debt(path=None):
    """Read and validate the committed debt fixture (missing file == no debt)."""
    path = path or DEFAULT_DEBT
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema") != DEBT_SCHEMA:
        raise ValueError("%s: expected schema %r, got %r"
                         % (path, DEBT_SCHEMA, payload.get("schema")))
    return [validate_row(row, path) for row in payload.get("rows", [])]


def _budget(rows):
    budget = {}
    for row in rows:
        budget[_row_key(row)] = budget.get(_row_key(row), 0) + row["count"]
    return budget


def apply_debt(report, rows):
    """Subtract declared debt from `report`; return a new report view.

    Adds ``forgiven`` (findings a row covered) and appends a synthetic finding
    for every stale row, so the view's exit decision is the ratchet's.
    """
    budget = _budget(rows)
    kept, forgiven, used = [], [], {}
    for found in report["findings"]:
        key = finding_key(found)
        if found["class"] != pg.ZERO_TOLERANCE and used.get(key, 0) < budget.get(key, 0):
            used[key] = used.get(key, 0) + 1
            forgiven.append(found)
            continue
        kept.append(found)
    for row in rows:
        key = _row_key(row)
        spare = budget.get(key, 0) - used.get(key, 0)
        if spare > 0:
            kept.append(pg.finding(
                row["surface"], STALE, row.get("class"), None, row["location"],
                "debt row forgives %d finding(s) the tree no longer has; "
                "remove it (owner: %s)" % (spare, row["owner"])))
    view = dict(report)
    view["findings"] = kept
    view["forgiven"] = forgiven
    view["debt_rows"] = len(rows)
    view["counts"] = pg._counts(kept)
    view["counts"]["forgiven"] = len(forgiven)
    view["ok"] = not kept
    return view


def summarize(rows):
    """Per-owner debt totals, for the worklog and the task-83 burndown."""
    totals = {}
    for row in rows:
        totals[row["owner"]] = totals.get(row["owner"], 0) + row["count"]
    return totals
