"""The MODULARITY CEILING gate — file size and per-function complexity.

The plan fixes four ceilings for the merged tree: `agentware_lib/**` at 600
lines, the entrypoint facade at 120, the orchestrator / loop / hook scripts at
250, `tests/**` at 600, and — the one no file cap can express — **120 lines and
12 branches per FUNCTION**.

The per-function cap is the load-bearing half, and the tree proves why. Before
the task-21 split, `features/configcmd.py` was 837 lines with TWO top-level defs,
because `cmd_config` alone was a 622-line, 119-branch guard-clause chain. A file
cap sees an 837 and asks for a split; it cannot see that splitting the FILE
without splitting the FUNCTION just moves the monolith. `test_module_boundaries`
M2 caps modules at 900 lines / 35 defs and has no per-function rule at all, so
`cmd_config` satisfied M2 on the day it was least modular.

THIS MODULE IS THE SINGLE DEFINITION OF THE BRANCH METRIC. Duplicating it is how
two gates drift into disagreeing about whether the tree is compliant, so every
caller imports `branch_count` from here — the same discipline `_layerutil` holds
for the layer rules.

What counts as a branch: the decision points that a reader must hold in their
head to follow one path through the body — `if` / `elif`, `for`, `while`, each
`except` handler, a conditional expression, each EXTRA operand of a boolean
operator (`a and b and c` is two decisions, not one), and each `if` clause inside
a comprehension. What deliberately does NOT count: `with` (it is not a fork),
`try` itself (only its handlers are), `assert` (a guard, not a path), and `else`
(it is the same decision as its `if`, already counted). Nested functions are
attributed to their OWN definition, never doubled into the enclosing one.

## The debt ratchet

Task 21 splits TWELVE modules in two rounds — the four the plan names, then the
eight remaining files that were still over the 600-line cap. **After round 2 the
FILE cap is satisfied outright**: no file under `agentware_lib/**` exceeds 600
lines, so no `file_over_cap` row is available to anyone, anywhere in the tree
(`schema_errors` refuses one). That half of the plan's ceiling is a property now,
not a promise, and it cannot be re-broken by declaring the breakage as debt.

The residual is therefore per-FUNCTION only — ~74 functions across `orglint`,
`planlint`, `recallcmd`, `updatecmd`, `skills` and friends — and belongs to plan
task 67, which widens the manifest across the whole tree and adds exactly these
caps. It stays declared rather than fixed for a reason that is specific, not
merely economic: task 30 gates that no definition's body is SHORTER than its
adjudicated winner's, so splitting a 149-branch body into helpers here would
redden the body differ, and the later port waves rewrite many of these same
functions wholesale from the other tree. Leaving that residual undeclared would
make this gate red for forty tasks, and a permanently red gate hides the ONE
finding that matters: a NEW violation arriving today.

So the residual is declared, per location, in a committed fixture and subtracted.
It is a ratchet, not an allowlist, and it bites in BOTH directions:

  * a violation worse than its declared value, or at an undeclared location, is a
    failure — new monoliths cannot hide behind old ones;
  * a row forgiving a violation the tree no longer has is ALSO a failure, so the
    file can only shrink. When the last row goes, the gate is simply green.

A row may never forgive a location under a package this task split — those are
asserted clean directly, so the split cannot silently regress.

Stdlib only, read-only, deterministic, py3.7-safe.
"""

import ast
import json
import os

HERE = os.path.dirname(os.path.realpath(__file__))

# The tree under measurement. Defaults to the checkout this file lives in; the
# override lets the gate run FROM the package repo AGAINST the staged merge tree
# during the migration, which is the only window in which the two differ. Same
# idiom as the staging tool's AGENTWARE_DONOR_STAGING, and it means one committed
# gate covers both trees instead of a copy drifting in each.
REPO_ROOT = os.environ.get("AGENTWARE_CEILING_ROOT") or os.path.dirname(HERE)
LIB_DIR = os.path.join(REPO_ROOT, "scripts", "agentware_lib")

DEBT_SCHEMA = "agentware/ceiling-debt@1"
DEFAULT_DEBT = os.path.join(HERE, "fixtures", "ceiling-debt.json")

# --- the ceilings (plan "Ceilings" section) ---------------------------------
LIB_FILE_CAP = 600
FUNC_LINE_CAP = 120
FUNC_BRANCH_CAP = 12

# ROUND 1 — the four modules the plan names. These carried the worst FUNCTION as
# well as the worst FILE (`cmd_config` was 622 lines / 119 branches), so they were
# split on both axes and are asserted FULLY clean: no debt row of ANY kind may
# forgive a violation inside one, or the split could regress without reddening
# anything.
TASK21_CLEAN_PREFIXES = (
    "scripts/agentware_lib/core/config/",
    "scripts/agentware_lib/knowledge/index/",
    "scripts/agentware_lib/features/configcmd/",
    "scripts/agentware_lib/features/dreamsteps/",
)

# ROUND 2 — the eight files that were still over the 600-line cap after round 1.
# These were split to satisfy the FILE cap, which is task 21's own acceptance
# criterion ("no file under the lib exceeds 600 lines"). Their inherited
# per-FUNCTION complexity came with the donor and is task 67's, so unlike round 1
# they may still carry function debt. They are listed for the reader and for the
# manifest, not as a carve-out: the file cap needs no per-prefix rule any more
# because NO file_over_cap row is accepted anywhere (see schema_errors).
TASK21_SPLIT_PREFIXES = (
    "scripts/agentware_lib/features/fanoutmq/",
    "scripts/agentware_lib/features/proposals/",
    "scripts/agentware_lib/knowledge/curation/",
    "scripts/agentware_lib/ops/fleet/",
    "scripts/agentware_lib/ops/metrics/",
    "scripts/agentware_lib/ops/planauthor/",
    "scripts/agentware_lib/ops/sessionparse/",
    "scripts/agentware_lib/retrieval/bench/",
)

FILE_OVER = "file_over_cap"
FUNC_LINES = "function_over_line_cap"
FUNC_BRANCHES = "function_over_branch_cap"

STALE = "stale_debt"
OVER = "over_debt"


def branch_count(node):
    """Decision points inside ONE function, excluding nested function bodies.

    Nested defs are their own unit — a closure's branches belong to the closure,
    not to its parent, or a module that factors work into inner helpers would
    look WORSE than the same code written flat, which would punish exactly the
    refactor this cap exists to encourage.
    """
    total = 0
    for child in ast.iter_child_nodes(node):
        total += _branches_in(child)
    return total


def _branches_in(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return 0  # its own unit; counted when we reach its own definition
    n = 0
    if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While,
                         ast.ExceptHandler, ast.IfExp)):
        n += 1
    elif isinstance(node, ast.BoolOp):
        n += len(node.values) - 1
    elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                           ast.GeneratorExp)):
        n += sum(len(g.ifs) for g in node.generators)
    for child in ast.iter_child_nodes(node):
        n += _branches_in(child)
    return n


def _rel(path):
    return os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")


def lib_present():
    """True iff the modular core is on disk in THIS tree.

    The gate ships alongside the migration and is meaningful only where
    `agentware_lib` exists. In a tree that still runs the monolith (every commit
    before the core lands) there is nothing to measure, and a gate that reported
    the whole debt file STALE there would be pure noise — so callers make the
    whole suite inert instead. This is a real absence, not a skipped assertion:
    once the core lands, the gate arms itself with no edit.
    """
    return os.path.isdir(LIB_DIR)


def lib_files():
    """Every agentware_lib .py file on disk, sorted, as absolute paths."""
    out = []
    for dirpath, _dirs, files in os.walk(LIB_DIR):
        for f in files:
            if f.endswith(".py"):
                out.append(os.path.join(dirpath, f))
    return sorted(out)


def scan_file(path):
    """Violations in ONE file: (kind, location, value, cap) tuples."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    rel = _rel(path)
    found = []
    n_lines = len(src.splitlines())
    if n_lines > LIB_FILE_CAP:
        found.append((FILE_OVER, rel, n_lines, LIB_FILE_CAP))
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        loc = "%s::%s" % (rel, node.name)
        n = node.end_lineno - node.lineno + 1
        if n > FUNC_LINE_CAP:
            found.append((FUNC_LINES, loc, n, FUNC_LINE_CAP))
        b = branch_count(node)
        if b > FUNC_BRANCH_CAP:
            found.append((FUNC_BRANCHES, loc, b, FUNC_BRANCH_CAP))
    return found


def scan():
    """Every ceiling violation across agentware_lib, sorted deterministically."""
    found = []
    for path in lib_files():
        found.extend(scan_file(path))
    return sorted(found, key=lambda r: (r[1], r[0]))


def load_debt(path=None):
    """Read the debt fixture and FLATTEN its groups into rows.

    The residual is not a pile of unrelated exceptions — it is one homogeneous
    class (pre-existing complexity inherited with the donor tree, tree-wide,
    removed by one task). Writing the same owner and the same paragraph onto
    eighty-one rows would be noise that hides the moment a row is genuinely
    DIFFERENT, so the reason lives once per GROUP and each row carries only what
    actually varies: what it is, where, and how bad. A future row that needs its
    own justification gets its own group, which makes that difference visible
    rather than burying it in a wall of duplicated prose.
    """
    with open(path or DEFAULT_DEBT, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("schema") != DEBT_SCHEMA:
        raise ValueError("ceiling-debt schema mismatch: %r" % data.get("schema"))
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


def schema_errors(data):
    """Structural problems in the debt file itself, as human-readable strings."""
    errors = []
    seen = set()
    for row in data.get("rows", []):
        for field in ("kind", "location", "value", "owner", "reason"):
            if not row.get(field) and row.get(field) != 0:
                errors.append("row %r missing %r" % (row.get("location"), field))
        key = _row_key(row)
        if key in seen:
            errors.append("duplicate row for %r" % (key,))
        seen.add(key)
        loc = row.get("location", "")
        if loc.startswith(TASK21_CLEAN_PREFIXES):
            errors.append(
                "row %r forgives a violation inside a package task 21 split; "
                "those are asserted clean and may never carry debt" % loc)
        if row.get("kind") == FILE_OVER:
            # Task 21 round 2 brought EVERY lib file under the 600-line cap, so
            # there is no residual left for a row to describe. Refusing the kind
            # outright — rather than listing the twelve split packages as
            # carve-outs — is what makes the file cap unbreakable: a NEW
            # over-cap file cannot be legalised by declaring it, in any
            # subsystem, by any later task.
            errors.append(
                "row %r declares %s: the file cap is satisfied outright after "
                "task 21, so no file may be forgiven for exceeding it — split "
                "the file instead" % (loc, FILE_OVER))
    return errors


def reconcile(found=None, debt=None):
    """Compare live violations to the declared debt.

    Returns (problems, clean) where problems is a list of (code, location,
    detail) — code is OVER (worse than declared, or undeclared) or STALE (a row
    the tree no longer produces).
    """
    found = scan() if found is None else found
    data = load_debt() if debt is None else debt
    rows = dict((_row_key(r), r) for r in data.get("rows", []))
    problems = []
    live = {}
    for kind, location, value, cap in found:
        live[(kind, location)] = value
        row = rows.get((kind, location))
        if row is None:
            problems.append((OVER, location,
                             "%s is %d (cap %d) and has no debt row" %
                             (kind, value, cap)))
        elif value > row["value"]:
            problems.append((OVER, location,
                             "%s is %d, worse than the declared %d" %
                             (kind, value, row["value"])))
    for key, row in sorted(rows.items()):
        if key not in live:
            problems.append((STALE, row["location"],
                             "declared %s is no longer present — remove the row"
                             % row["kind"]))
    return problems, not problems
