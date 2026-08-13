"""The two STRUCTURAL modularity rules the ceilings cannot express (plan task 68).

`tests/_ceiling_gate.py` measures SIZE — a file's lines, a function's lines and
branches. Size is only half of what stops a tree becoming a monolith again; the
other half is SHAPE, and two shapes are load-bearing enough that the plan names
them as violation shapes the negative test must be able to redden:

  * **LAYER DIRECTION (module-level upward import).** The folder structure is a
    dependency claim: `core` depends on nothing above it, `knowledge` on `core`,
    `retrieval` on `core`+`knowledge`, `ops` on those three, `features` on
    everything below, `cli` on all. An import that crosses a layer UPWARD at
    MODULE level makes the claim false and fixes the package's load order, which
    is how an import cycle becomes possible at all. Function-local upward
    imports are the DOCUMENTED escape hatch for the irreducible symbol edge
    (`ops/audit.py` says so in its own docstring: "upward, lazy") and are
    deliberately NOT reported here — a call-time edge cannot deadlock an import
    and cannot re-order a package.

  * **DUMPING-GROUND MODULE NAME.** `utils.py`, `helpers.py`, `common.py`,
    `misc.py`, `shared.py`. A module named for what it is NOT — not any of the
    other modules — has no boundary, so nothing can ever be said to belong
    outside it. It is the monolith's seed: every file the tree already split was
    once "just a couple of helpers". The rule is a NAME rule precisely because
    it must bite on the day the file is created, when it holds two functions and
    every size cap is satisfied.

Both rules existed in the donor tree as `test_module_boundaries` M12 and M10 and
neither was ported: `tests/_layerutil.direction_violations` had no caller in this
tree at all, and no check looked at module names. Task 68 needs both to REDDEN
for its negative test, so this module supplies the measurement and the gate
(`tests/gates/test_modularity_rules.py`) supplies the assertions.

## Scope, and why each rule has the scope it has

The LAYER rule is `scripts/agentware_lib/**` only — layers are a property of that
package and nothing else in the tree declares any. The NAME rule is the WHOLE
task-67 corpus (`tests/_corpus.py`), because a dumping ground is not a Python
problem: `scripts/loop/junk.sh` and `webui/src/misc.ts` are the same defect, and
scoping the rule to the lib would exempt exactly the subsystems task 67 widened
the corpus to reach.

## The declared residual

Enforcing a rule for the first time on a tree that predates it finds a residual.
Ours is 15 lib modules carrying module-level upward edges (12 of them `ops ->
features`, from local-origin modules whose dependencies were ported into
`features`) and 3 conventionally-named files outside the lib. Both are declared
in `tests/fixtures/modularity-debt.json` and subtracted here, on the same ratchet
discipline `_ceiling_gate` holds:

  * a violation at an UNDECLARED location, or one that got WORSE than declared
    (a new upward layer on a module that already had one), is a failure — a new
    violation may never hide behind an old one;
  * a row the tree no longer produces is ALSO a failure, so the file only
    shrinks;
  * a `grabbag_module_name` row is REFUSED under `scripts/agentware_lib/`. The
    lib has zero today, so that half of the rule is an invariant rather than a
    ratchet: a new `agentware_lib/ops/utils.py` cannot be legalised by declaring
    it, by any later task.

The two kinds differ in one way that is deliberate: an upward-import row is DEBT
(owner `task <n>`, removed by paying it down), while a name row may be a
PERMANENT exemption (owner `permanent`) when the name is an ecosystem convention
rather than a dumping ground — a shell prelude, or shadcn/ui's generated
`lib/utils.ts`. A permanent row still has to justify itself in prose and still
dies the moment the file does.

Stdlib only, read-only, deterministic, py3.7-safe.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _corpus      # noqa: E402  (the ONE corpus table, shared with the generator)
import _layerutil   # noqa: E402  (the ONE layer table, shared with the manifest)

REPO_ROOT = _corpus.REPO_ROOT

DEBT_SCHEMA = "agentware/modularity-debt@1"
DEFAULT_DEBT = os.path.join(HERE, "fixtures", "modularity-debt.json")

#: The two violation kinds this module measures.
UPWARD = "module_level_upward_import"
GRABBAG = "grabbag_module_name"
KINDS = (UPWARD, GRABBAG)

#: Problem codes, same vocabulary as `_ceiling_gate.reconcile`.
OVER = "over_debt"
STALE = "stale_debt"

#: The donor's M10 set, unchanged. Kept SMALL on purpose: every name here must be
#: indefensible on its own, because the rule has no severity dial — it either
#: refuses the name or it does not. "core", "base" and "lib" are deliberately
#: absent; they name a POSITION in an architecture, which is a real claim, while
#: "misc" names the absence of one.
GRABBAG_NAMES = frozenset(("common", "utils", "helpers", "misc", "shared"))

LIB_PREFIX = _corpus.LIB_PREFIX


# --- measurement ------------------------------------------------------------

def lib_present(root=None):
    """True iff the modular core is on disk — the layer rule's precondition."""
    return os.path.isdir(os.path.join(root or REPO_ROOT, "scripts",
                                      "agentware_lib"))


def lib_modules(root=None):
    """Every agentware_lib module file except package `__init__.py`, sorted."""
    base = root or REPO_ROOT
    lib = os.path.join(base, "scripts", "agentware_lib")
    out = []
    for dirpath, dirs, files in os.walk(lib):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith(".py") and name != "__init__.py":
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def upward_imports(root=None):
    """Module-level upward edges: [(rel_path, own_layer, [bad layers])], sorted.

    A module that cannot be parsed is skipped rather than guessed at — a syntax
    error is the SUITE's finding to report, not this gate's, and inventing a
    violation from an unreadable file would make the ratchet unfixable.
    """
    base = root or REPO_ROOT
    scripts_root = os.path.join(base, "scripts")
    found = []
    for mod in lib_modules(base):
        try:
            bad = _layerutil.module_level_direction_violations(mod, scripts_root)
        except SyntaxError:
            continue
        if bad:
            rel = os.path.relpath(mod, base).replace(os.sep, "/")
            own = _layerutil.module_layer(mod, _layerutil.lib_dir(base))
            found.append((rel, own, bad))
    return sorted(found)


def name_stem(rel):
    """The NAME a file claims, normalised: `tests/gates/test_shared.py` -> shared.

    A leading `test_` and any leading underscores are stripped before the name is
    judged, because they say WHERE the file sits (a test, a private helper), not
    what it owns. Without that, `tests/_utils.py` and `tests/gates/test_misc.py`
    would both slip the rule — and the test corpus is exactly where this tree
    already grew nine over-cap helper modules that the ceiling gate had to catch
    after the fact.
    """
    stem = os.path.splitext(os.path.basename(rel))[0]
    if stem.startswith("test_"):
        stem = stem[len("test_"):]
    return stem.lstrip("_").lower()


def grabbag_files(root=None):
    """Corpus members whose filename stem is a dumping-ground name, sorted."""
    return sorted(rel for rel in _corpus.corpus_files(root or REPO_ROOT)
                  if name_stem(rel) in GRABBAG_NAMES)


def scan(root=None):
    """Every structural violation, as (kind, location, value) triples.

    `value` is comparable per kind: the sorted bad-layer list for an upward
    import, the offending name stem for a dumping-ground module.
    """
    found = []
    if lib_present(root):
        for rel, _own, bad in upward_imports(root):
            found.append((UPWARD, rel, list(bad)))
    for rel in grabbag_files(root):
        found.append((GRABBAG, rel,
                      name_stem(rel)))
    return sorted(found, key=lambda r: (r[1], r[0]))


# --- the declared residual --------------------------------------------------

def load_debt(path=None):
    """Read the declared-residual fixture and FLATTEN its groups into rows.

    Groups carry the owner and the paragraph; a row carries only what varies
    (what, where, how bad) unless it needs its own reason. Same shape as
    `_ceiling_gate.load_debt`, so a reader who has read one has read both.
    """
    with open(path or DEFAULT_DEBT, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("schema") != DEBT_SCHEMA:
        raise ValueError("modularity-debt schema mismatch: %r" % data.get("schema"))
    rows = list(data.get("rows", []))
    for group in data.get("groups", []):
        for row in group.get("rows", []):
            merged = dict(row)
            merged.setdefault("owner", group.get("owner"))
            merged.setdefault("reason", group.get("reason"))
            rows.append(merged)
    return {"schema": data["schema"], "rows": rows,
            "note": data.get("note"), "captured": data.get("captured")}


def _row_key(row):
    return (row["kind"], row["location"])


def _owner_ok(owner):
    if not owner:
        return False
    if owner == "permanent":
        return True
    parts = owner.split()
    return len(parts) == 2 and parts[0] == "task" and parts[1].isdigit()


def schema_errors(data):
    """Structural problems in the residual file itself, as readable strings."""
    errors = []
    seen = set()
    for row in data.get("rows", []):
        loc = row.get("location", "")
        for field in ("kind", "location", "value", "owner", "reason"):
            if not row.get(field) and row.get(field) != 0:
                errors.append("row %r missing %r" % (loc, field))
        if row.get("kind") not in KINDS:
            errors.append("row %r has unknown kind %r" % (loc, row.get("kind")))
        if not _owner_ok(row.get("owner")):
            errors.append("row %r owner must be 'task <n>' or 'permanent', got "
                          "%r" % (loc, row.get("owner")))
        if row.get("kind") == UPWARD and row.get("owner") == "permanent":
            errors.append(
                "row %r declares %s as PERMANENT: an upward import is debt with "
                "an owning task, never an exemption — the layer claim is either "
                "true or it is not" % (loc, UPWARD))
        if row.get("kind") == GRABBAG and loc.startswith(LIB_PREFIX):
            # The absolute half of the rule. The lib has zero dumping-ground
            # names today, so there is no residual for a row to describe THERE,
            # and refusing the row outright is what stops a later task
            # legalising `agentware_lib/ops/utils.py` by declaring it — the same
            # move `_ceiling_gate` makes for a lib file over the 600-line cap.
            errors.append(
                "row %r declares %s under %s: the lib carries no dumping-ground "
                "name and may not acquire one — give the module a name that "
                "says what it owns" % (loc, GRABBAG, LIB_PREFIX))
        key = _row_key(row)
        if key in seen:
            errors.append("duplicate row for %r" % (key,))
        seen.add(key)
    return errors


def _worse(kind, live_value, declared_value):
    """True iff a live violation is worse than the row that declares it."""
    if kind == UPWARD:
        return bool(set(live_value) - set(declared_value or ()))
    return live_value != declared_value


def reconcile(found=None, debt=None, root=None):
    """Compare live structural violations to the declared residual.

    Returns (problems, clean), problems being (code, location, detail) with code
    OVER (undeclared or worsened) or STALE (declared but no longer present).
    """
    found = scan(root) if found is None else found
    data = load_debt() if debt is None else debt
    rows = dict((_row_key(r), r) for r in data.get("rows", []))
    problems = []
    live = {}
    for kind, location, value in found:
        live[(kind, location)] = value
        row = rows.get((kind, location))
        if row is None:
            problems.append((OVER, location,
                             "%s (%s) has no declared row" % (kind, value)))
        elif _worse(kind, value, row.get("value")):
            problems.append((OVER, location,
                             "%s is %s, worse than the declared %s"
                             % (kind, value, row.get("value"))))
    for key, row in sorted(rows.items()):
        if key not in live:
            problems.append((STALE, row["location"],
                             "declared %s is no longer present — remove the row"
                             % row["kind"]))
    return problems, not problems


# --- the command ------------------------------------------------------------
#
# Task 78, and the same defect `_ceiling_gate.main` closes: this module had no
# `if __name__` dispatch, so `python3 tests/_modularity_rules.py` -- documented
# in CONTRIBUTING.md, in docs/architecture.md twice, and in the
# modular-development skill's finish-line list -- imported the module and exited
# 0 having measured nothing. The layer-direction rule (R-MOD-02) and the
# grab-bag-name rule (R-MOD-05) are enforced by `tests/gates/test_modularity_
# rules.py`; what was missing was any way for a CONTRIBUTOR (or CI) to ask them
# a question and get an answer. `main` asks the gate test's own question through
# the same `schema_errors` + `reconcile` pair.
#
# `--root PATH` is already the module's supported override (the gate runs from
# the package repo against a staged tree during the migration) and is exposed
# here for the same reason; `--debt PATH` lets a test prove the command reddens.

def main(argv=None):
    """Report structural violations not covered by the declared residual."""
    argv = list(sys.argv[1:] if argv is None else argv)
    debt_path = argv[argv.index("--debt") + 1] if "--debt" in argv else None
    root = argv[argv.index("--root") + 1] if "--root" in argv else None
    data = load_debt(debt_path)
    errors = schema_errors(data)
    problems, clean = reconcile(debt=data, root=root)
    for err in errors:
        sys.stderr.write("SCHEMA %s\n" % err)
    for code, loc, detail in problems:
        sys.stderr.write("%s %s: %s\n" % (code, loc, detail))
    if errors or not clean:
        sys.stderr.write("modularity rules FAILED: %d schema error(s), %d "
                         "problem(s)\n" % (len(errors), len(problems)))
        return 1
    sys.stdout.write("modularity rules OK: %d module(s), %d declared\n"
                     % (len(lib_modules(root)), len(data["rows"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
