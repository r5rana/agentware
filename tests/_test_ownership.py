"""The test-corpus OWNERSHIP rule, and the ratchet that keeps it honest.

Feature 260813-modular-core-adoption, plan task 71.

## The rule

Every `scripts/agentware_lib/**` module is OWNED by exactly one test file: its
granular unit file at `tests/unit/<layer>/test_<stem>.py`. Nothing else can own
it. Not a flat `tests/test_*.py`, not an integration or e2e or gate module, and
not a differently-named file sitting in the same unit tier — no matter how many
of the module's symbols any of them touch.

The mapping is NOT defined here. It is `_manifest_gen.granular_test_file`, the
same function that fills the manifest's published `test_file` column, so the
column a contributor reads and the rule that enforces it cannot drift: there is
one function to disagree with.

## Why the rule is location-only, and what task 71 retired

The donor tree shipped this as `test_module_boundaries` M11 — "derived
test-ownership map" — and its ownership test counted EITHER tier:

    owners = []
    if os.path.isfile(granular):            # the granular file
        owners.append(...)
    for p in legacy:                        # <- THE CARVE-OUT
        if exports & legacy_refs[p]:        #    any flat tests/test_*.py that
            owners.append(...)              #    mentions one of my exports

That second branch is a carve-out for the pre-decomposition corpus, and it makes
the rule unfalsifiable in the direction that matters. A white-box file that
reaches symbols through the CLI facade references EVERY module's exports, so
under the carve-out one such file can "own" the whole lib: the map is green, and
no module has a test that fails when that module alone breaks. Ownership derived
from a REFERENCE answers "was this symbol mentioned", which is not the question.
The question is "when this module breaks, which file goes red", and only a file
whose subject IS the module answers it.

So this port drops the branch. Ownership is a LOCATION claim, decided by one
`os.path.isfile`, and `tests/gates/test_test_ownership.py` proves the retirement
by planting the carve-out's three shapes — a flat `tests/test_<stem>.py`, a
non-unit tier, and a same-tier file under another name — each importing the
module and referencing every one of its anchor symbols, and asserting the module
is STILL reported testless.

Neither M11 nor its carve-out existed in this tree before task 71: like M12 and
M10 (task 68), the rule did not survive the merge. `_manifest_gen` has computed
`test_file_present` since task 67 and its docstring named this task as the one
that would enforce it. Nothing did until now.

## The declared residual, and its ratchet

Enforcing the rule for the first time on a corpus organised by BEHAVIOUR rather
than by module finds a large residual: this tree's unit files are named for what
they exercise (`test_recall.py`, `test_dream.py`), not for the module that owns
the code. Those rows are declared in `tests/fixtures/test-ownership-debt.json`
and subtracted, on the same discipline `_ceiling_gate` and `_modularity_rules`
hold:

  * a testless module at an UNDECLARED location is a failure — a new module may
    never arrive without its unit file;
  * a row the tree no longer produces is ALSO a failure, so the file only
    shrinks and paying a row down has to be recorded;
  * a row may never be `permanent`. A testless module is DEBT with an owning
    task, never an exemption: "this module will never have a test" is not a
    position this package takes.
  * a row's `value` must equal the path the mapping computes for its location,
    so a row cannot quietly aim at a file that would never have owned it; and a
    row may only name a real lib module.

The counter is pinned twice, because rows alone do not stop the ratchet rotting
into an allowlist: the fixture carries `ratchet.max_rows`, which must equal the
row count, and the GATE pins its ceiling by value. Declaring a new row therefore
takes an edit to the fixture AND an edit to a constant in the test that guards
it — the same two-key move `_ceiling_gate` requires, and the reason a later task
cannot legalise a testless module by declaring it.

Stdlib only, read-only, deterministic, py3.7-safe. Runs as an imported helper
and as a CLI (`python3 tests/_test_ownership.py --check | --report | --capture`).
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _corpus        # noqa: E402  (the ONE corpus table)
import _layerutil     # noqa: E402  (the ONE layer table)
import _manifest_gen  # noqa: E402  (the ONE module list + granular-path mapping)

REPO_ROOT = _corpus.REPO_ROOT

DEBT_SCHEMA = "agentware/test-ownership-debt@1"
DEFAULT_DEBT = os.path.join(HERE, "fixtures", "test-ownership-debt.json")

#: The one violation kind this module measures.
TESTLESS = "module_without_granular_test"
KINDS = (TESTLESS,)

#: Problem codes, same vocabulary as `_ceiling_gate.reconcile` and
#: `_modularity_rules.reconcile`.
OVER = "over_debt"
STALE = "stale_debt"

LIB_PREFIX = _corpus.LIB_PREFIX

#: Where a granular unit file must live for it to own anything.
GRANULAR_UNIT_REL = "tests/unit"


# --- measurement ------------------------------------------------------------

def lib_present(root=None):
    """True iff the modular core is on disk — the rule's precondition."""
    return os.path.isdir(os.path.join(root or REPO_ROOT, "scripts",
                                      "agentware_lib"))


def _rel(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def lib_modules(root=None):
    """Every agentware_lib module except package `__init__.py`, sorted."""
    return _manifest_gen.domain_modules(root)


def granular_test_rel(mod_path, root=None):
    """The ONE test file that may own `mod_path`, repo-relative.

    Delegates to the manifest's mapping so the published column and this rule
    are the same answer. Existence is deliberately not consulted here.
    """
    base = root or REPO_ROOT
    return _rel(_manifest_gen.granular_test_file(mod_path, root), base)


def owners(mod_path, root=None):
    """Every test file that owns `mod_path` — a list of 0 or 1 repo-relative
    paths.

    A list rather than a bool because the donor's M11 returned a list of owners
    across two tiers, and keeping the shape makes the retirement legible: the
    second source is gone, so the list can no longer hold more than one entry.
    """
    base = root or REPO_ROOT
    rel = granular_test_rel(mod_path, base)
    return [rel] if os.path.isfile(os.path.join(base, rel)) else []


def ownership_map(root=None):
    """[(module rel path, [owner rel path, ...])] for the whole lib, sorted."""
    base = root or REPO_ROOT
    return [(_rel(mod, base), owners(mod, base)) for mod in lib_modules(base)]


def testless_modules(root=None):
    """Repo-relative paths of every lib module with no granular unit file."""
    return sorted(rel for rel, owned in ownership_map(root) if not owned)


def scan(root=None):
    """Every ownership violation, as (kind, location, value) triples.

    `value` is the granular path that WOULD have owned the module, so a reader
    of the fixture is told the remedy rather than only the complaint.
    """
    base = root or REPO_ROOT
    if not lib_present(base):
        return []
    found = []
    for mod in lib_modules(base):
        if not owners(mod, base):
            found.append((TESTLESS, _rel(mod, base),
                          granular_test_rel(mod, base)))
    return sorted(found, key=lambda r: (r[1], r[0]))


# --- the adjudicator's live report ------------------------------------------

def _names_referenced(path):
    """Every Name/Attribute identifier appearing in a Python file."""
    import ast
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError):
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
    return out


def _layer_unit_files(layer, root):
    directory = os.path.join(root, "tests", "unit", layer)
    if not os.path.isdir(directory):
        return []
    return sorted(os.path.join(directory, name)
                  for name in os.listdir(directory)
                  if name.startswith("test_") and name.endswith(".py"))


def granular_references(root=None):
    """{testless module rel: [same-layer unit files touching its anchors]}.

    NOT part of the rule and deliberately not stored in the fixture — a
    reference is not ownership, which is the whole point of task 71. It is a
    LIVE triage aid for the task that pays the residual down: a module with a
    same-layer file already exercising it needs that file renamed or split,
    while a module with an empty list needs a unit file written from nothing.
    Computed on demand so it can never rot in a committed row.
    """
    base = root or REPO_ROOT
    cache = {}
    out = {}
    for mod in lib_modules(base):
        rel = _rel(mod, base)
        if owners(mod, base):
            continue
        layer = _layerutil.module_layer(
            mod, _layerutil.lib_dir(base))
        anchors = set(_manifest_gen.anchor_symbols(mod))
        hits = []
        for path in _layer_unit_files(layer, base):
            if path not in cache:
                cache[path] = _names_referenced(path)
            if anchors & cache[path]:
                hits.append(_rel(path, base))
        out[rel] = hits
    return out


# --- the declared residual --------------------------------------------------

def load_debt(path=None):
    """Read the declared-residual fixture and FLATTEN its groups into rows.

    Groups carry the owner and the paragraph; a row carries only what varies.
    Same shape as `_ceiling_gate.load_debt` and `_modularity_rules.load_debt`,
    so a reader who has read one has read all three.
    """
    with open(path or DEFAULT_DEBT, encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("schema") != DEBT_SCHEMA:
        raise ValueError("test-ownership-debt schema mismatch: %r"
                         % data.get("schema"))
    rows = list(data.get("rows", []))
    for group in data.get("groups", []):
        for row in group.get("rows", []):
            merged = dict(row)
            merged.setdefault("owner", group.get("owner"))
            merged.setdefault("reason", group.get("reason"))
            rows.append(merged)
    return {"schema": data["schema"], "rows": rows,
            "note": data.get("note"), "captured": data.get("captured"),
            "ratchet": data.get("ratchet") or {}}


def _row_key(row):
    return (row["kind"], row["location"])


def _owner_ok(owner):
    if not owner:
        return False
    parts = owner.split()
    return len(parts) == 2 and parts[0] == "task" and parts[1].isdigit()


def _row_field_errors(row, loc):
    errors = []
    for field in ("kind", "location", "value", "owner", "reason"):
        if not row.get(field):
            errors.append("row %r missing %r" % (loc, field))
    if row.get("kind") not in KINDS:
        errors.append("row %r has unknown kind %r" % (loc, row.get("kind")))
    if not _owner_ok(row.get("owner")):
        errors.append(
            "row %r owner must be 'task <n>', got %r — a testless module is "
            "DEBT with an owning task and may never be a 'permanent' "
            "exemption: 'this module will never have a test' is not a position "
            "this package takes" % (loc, row.get("owner")))
    if not loc.startswith(LIB_PREFIX):
        errors.append(
            "row %r is outside %s: this rule owns the modular core and nothing "
            "else" % (loc, LIB_PREFIX))
    return errors


def schema_errors(data, root=None):
    """Structural problems in the residual file itself, as readable strings."""
    base = root or REPO_ROOT
    errors = []
    seen = set()
    for row in data.get("rows", []):
        loc = row.get("location", "")
        errors.extend(_row_field_errors(row, loc))
        mod = os.path.join(base, loc.replace("/", os.sep))
        if not os.path.isfile(mod):
            errors.append("row %r names no module on disk — delete it" % loc)
        else:
            want = granular_test_rel(mod, base)
            if row.get("value") != want:
                errors.append(
                    "row %r declares value %r but the granular path for that "
                    "module is %r — a row may not aim at a file that would "
                    "never have owned it" % (loc, row.get("value"), want))
        key = _row_key(row)
        if key in seen:
            errors.append("duplicate row for %r" % (key,))
        seen.add(key)
    ratchet = data.get("ratchet") or {}
    if ratchet.get("max_rows") != len(data.get("rows", [])):
        errors.append(
            "ratchet.max_rows is %r but the file carries %d row(s) — pay a row "
            "down and decrement the counter in the same commit"
            % (ratchet.get("max_rows"), len(data.get("rows", []))))
    return errors


def reconcile(found=None, debt=None, root=None):
    """Compare live ownership violations to the declared residual.

    Returns (problems, clean), problems being (code, location, detail) with code
    OVER (undeclared) or STALE (declared but no longer present).
    """
    found = scan(root) if found is None else found
    data = load_debt() if debt is None else debt
    rows = dict((_row_key(r), r) for r in data.get("rows", []))
    problems = []
    live = {}
    for kind, location, value in found:
        live[(kind, location)] = value
        if (kind, location) not in rows:
            problems.append((OVER, location,
                             "%s: no test file owns it — write %s"
                             % (kind, value)))
    for key, row in sorted(rows.items()):
        if key not in live:
            problems.append((STALE, row["location"],
                             "declared %s is no longer present — remove the row"
                             % row["kind"]))
    return problems, not problems


# --- CLI --------------------------------------------------------------------

def _capture(root=None):
    """The residual as a fixture body, for the FIRST capture and for re-stamping
    the counter after a payoff. Groups are prose and are never regenerated."""
    found = scan(root)
    return {
        "schema": DEBT_SCHEMA,
        "rows": [{"kind": k, "location": loc, "value": val}
                 for k, loc, val in found],
        "ratchet": {"max_rows": len(found)},
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--report" in argv:
        for rel, owned in ownership_map():
            sys.stdout.write("%-58s %s\n" % (rel, ", ".join(owned) or "NONE"))
        return 0
    if "--references" in argv:
        sys.stdout.write(json.dumps(granular_references(), indent=2,
                                    sort_keys=True) + "\n")
        return 0
    if "--capture" in argv:
        sys.stdout.write(json.dumps(_capture(), indent=1, sort_keys=True) + "\n")
        return 0
    data = load_debt()
    errors = schema_errors(data)
    problems, clean = reconcile(debt=data)
    for err in errors:
        sys.stderr.write("SCHEMA %s\n" % err)
    for code, loc, detail in problems:
        sys.stderr.write("%s %s: %s\n" % (code, loc, detail))
    if errors or not clean:
        return 1
    sys.stdout.write("test ownership OK: %d module(s), %d declared\n"
                     % (len(lib_modules()), len(data["rows"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
