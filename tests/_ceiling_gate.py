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

The residual is therefore per-FUNCTION only, and it is declared, per location, in
a committed fixture and subtracted. It is a ratchet, not an allowlist, and it
bites in BOTH directions:

  * a violation worse than its declared value, or at an undeclared location, is a
    failure — new monoliths cannot hide behind old ones;
  * a row forgiving a violation the tree no longer has is ALSO a failure, so the
    file can only shrink. When the last row goes, the gate is simply green.

A row may never forgive a location under a package this task split — those are
asserted clean directly, so the split cannot silently regress.

## Schema 2 — every row carries a DECISION (task 99)

Under schema 1 every row named an OWNING TASK: "task 67 removes this". That is
the right shape for a residual with a scheduled end, and the wrong shape the
moment the owning task closes — the pointer dangles, nobody is looking for the
row any more, and a ratchet with 151 rows owned by finished work is an allowlist
wearing a ratchet's clothes. Task 67 measured the residual tree-wide; task 99
read every row against its body and gave each one of exactly two verdicts:

  * `exempt` — PERMANENT, because the count is a property of the code. The row
    carries an `invariant` naming what the branches ARE: a closed taxonomy, a
    report-every-problem check set, a per-field coercion of an external schema
    this package does not own. It carries NO `owner`, because there is no work
    to own, and the schema refuses one.
  * `paydown` — WORK, because the reading found nothing encoded: a guard-clause
    chain, a flag dispatch whose arms each re-implement their own renderer, a
    hand-rolled reader written at eight sites, repeated inline formatting. The
    row carries a `seam` naming the concrete helpers that remove it and the
    parent count that results, so it is a work order rather than a complaint and
    the next reader does not have to re-derive the reading.

The third state — declared, with no decision, owned by a task that has closed —
is what this schema makes unrepresentable. `verdict` is required; an `exempt`
row may not carry an owner; a `paydown` row must carry both an owner and a seam.

`invariant` is additionally held to naming a property of the CODE. The row's
history (which tree it came from, which port carried it, why splitting it then
was risky) is real and belongs in the group `reason`; it is refused inside
`invariant` by a closed word list, because "inherited from the donor at task 67"
justifies nothing about whether the count is correct TODAY. A `seam` is under no
such rule: naming the blocker that clears before the seam can be cut is exactly
what a work order should say.

Two counters, both pinned BY VALUE in `tests/gates/test_module_ceilings.py`:
`ratchet.max_rows` must equal the row count, and `ratchet.max_paydown_rows` may
only fall. Admitting a new row therefore takes a deliberate edit in two files —
the same two-key move `_test_ownership` requires — and the paydown group can
only shrink.

An exemption is still a CEILING, not a pardon: the declared value is the worst
the location may be, and a location that improves must have its row lowered or
removed in the same commit. Both directions still bite.

Stdlib only, read-only, deterministic, py3.7-safe.
"""

import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _corpus  # noqa: E402  (the ONE corpus + cap table, shared with the generator)

# The tree under measurement. Defaults to the checkout this file lives in; the
# override lets the gate run FROM the package repo AGAINST the staged merge tree
# during the migration, which is the only window in which the two differ. Same
# idiom as the staging tool's AGENTWARE_DONOR_STAGING, and it means one committed
# gate covers both trees instead of a copy drifting in each.
REPO_ROOT = _corpus.REPO_ROOT
LIB_DIR = os.path.join(REPO_ROOT, "scripts", "agentware_lib")

DEBT_SCHEMA = "agentware/ceiling-debt@2"
DEFAULT_DEBT = os.path.join(HERE, "fixtures", "ceiling-debt.json")

#: The shortest a row's justification may be, whichever kind it is. A one-liner
#: like "it is a dispatcher" names a shape, not a guarantee; an `invariant` has
#: to say what breaks if the branches are collapsed and a `seam` has to name the
#: helpers that remove the row, and neither fits in forty characters.
JUSTIFICATION_MIN_CHARS = 60

#: Words that describe a row's HISTORY or a reviewer's NERVES rather than the
#: code's behaviour. Every one of them appeared in the schema-1 group reasons,
#: where they were correct and useful — they explain how the row got here. In
#: `invariant` they are a rubber stamp: none of them can be checked against the
#: body, and all of them stay true forever no matter how the code changes. The
#: match is word-boundaried so "reports" is not read as "port".
PROVENANCE_WORDS = (
    "task", "todo", "tbd", "inherited", "donor", "monolith", "legacy",
    "pre-existing", "preexisting", "port", "ported", "move", "moved",
    "temporary", "temporarily", "later", "eventually", "someday",
    "risk", "risky", "dangerous", "complex", "complexity", "churn",
    "refactor", "scope", "verbatim", "economic", "expensive",
)
_PROVENANCE_RX = re.compile(
    r"\b(%s)\b" % "|".join(re.escape(w) for w in PROVENANCE_WORDS))

# --- the ceilings (plan "Ceilings" section; the table lives in _corpus) ------
LIB_FILE_CAP = _corpus.LAYER_CAPS[_corpus.LIB_LAYER]
FUNC_LINE_CAP = _corpus.FUNC_LINE_CAP
FUNC_BRANCH_CAP = _corpus.FUNC_BRANCH_CAP
LIB_PREFIX = _corpus.LIB_PREFIX

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


def corpus_files():
    """Every corpus member on disk (task 67), repo-relative and sorted."""
    return _corpus.corpus_files(REPO_ROOT)


def scan_file(rel, parse=True):
    """Violations in ONE corpus member, given its repo-relative path.

    `parse` is False for the members the stdlib cannot parse — shell scripts and
    the webui's TypeScript — which carry the FILE cap only. That is a real limit
    of a zero-dependency gate (INV-6 forbids adding a parser), and it is stated
    here rather than hidden: a 249-line shell script with one 200-line function
    passes, so the 250 cap is doing all the work on that side.
    """
    path = os.path.join(REPO_ROOT, rel)
    with open(path, encoding="utf-8") as f:
        src = f.read()
    layer = _corpus.layer_of(rel)
    cap = _corpus.cap_of(layer)
    found = []
    n_lines = len(src.splitlines())
    if n_lines > cap:
        found.append((FILE_OVER, rel, n_lines, cap))
    if not parse:
        return found
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
    """Every ceiling violation across the WHOLE corpus, sorted deterministically.

    Task 67 widened this from `agentware_lib/**` to every Python and shell file
    under `scripts/`, the orchestrator, the test corpus and the webui source. The
    narrow scan was the reason the 1,146-line dashboard and nine over-cap test
    helpers were invisible: they were never measured, so the gate was green about
    a tree it had not looked at.
    """
    parseable = set(_corpus.python_files(REPO_ROOT))
    found = []
    for rel in corpus_files():
        found.extend(scan_file(rel, parse=rel in parseable))
    return sorted(found, key=lambda r: (r[1], r[0]))


def scan_lib():
    """The lib-only slice of `scan()` — what task 21 asserts clean outright."""
    return [r for r in scan() if r[1].startswith(LIB_PREFIX)]


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
            merged.setdefault("reason", group.get("reason"))
            merged.setdefault("verdict", group.get("verdict"))
            if group.get("invariant") is not None:
                merged.setdefault("invariant", group.get("invariant"))
            if group.get("seam") is not None:
                merged.setdefault("seam", group.get("seam"))
            # NOT setdefault: an `owner` anywhere must reach `schema_errors`
            # rather than being silently absorbed into the row it decorates.
            if "owner" in group and "owner" not in merged:
                merged["owner"] = group["owner"]
            rows.append(merged)
    return {"schema": data["schema"], "rows": rows,
            "note": data.get("note"), "captured": data.get("captured"),
            "ratchet": data.get("ratchet") or {}}


def _row_key(row):
    return (row["kind"], row["location"])


EXEMPT = "exempt"
PAYDOWN = "paydown"
VERDICTS = (EXEMPT, PAYDOWN)


def invariant_errors(location, text):
    """Why this `invariant` does not name a property of the code, if it does not.

    Split out so the refusal can be exercised directly on a string: the rule is
    "say what the branches ARE", and the two ways to break it are to say nothing
    (too short) or to say where the row came from (provenance language).
    """
    errors = []
    if not text or not str(text).strip():
        return ["row %r declares no invariant: an exemption must name the "
                "property its count encodes, or its verdict is paydown"
                % location]
    text = str(text)
    if len(text) < JUSTIFICATION_MIN_CHARS:
        errors.append(
            "row %r has a %d-character invariant; %d is the floor, because an "
            "exemption has to say what BREAKS if the branches are collapsed"
            % (location, len(text), JUSTIFICATION_MIN_CHARS))
    hits = sorted(set(_PROVENANCE_RX.findall(text.lower())))
    if hits:
        errors.append(
            "row %r justifies its exemption with provenance/nerves language "
            "%r: `invariant` must name a property of the CODE that a reader can "
            "check against the body — where the row came from belongs in the "
            "group `reason`" % (location, hits))
    return errors


def _verdict_errors(row):
    """The per-verdict obligations: what each kind of row must and may not carry.

    A `paydown` row keeps an owner ON PURPOSE. It is open work, and open work
    has someone doing it; what schema 1 got wrong was letting that owner outlive
    the task, not having one at all. The counter is what keeps it honest —
    `max_paydown_rows` only falls, so this group cannot grow while nobody is
    looking, and when it reaches zero every remaining row is a decision that
    stands.
    """
    loc, verdict = row.get("location"), row.get("verdict")
    if verdict not in VERDICTS:
        return ["row %r has verdict %r; every row must be adjudicated as one of "
                "%r — a row with no verdict is the state this schema exists to "
                "make unrepresentable" % (loc, verdict, list(VERDICTS))]
    errors = []
    if verdict == EXEMPT:
        if "owner" in row:
            errors.append(
                "row %r is exempt but carries an `owner` (%r): an exemption has "
                "no owner because there is no work to own. If there IS work, "
                "its verdict is paydown." % (loc, row.get("owner")))
        errors.extend(invariant_errors(loc, row.get("invariant")))
        if row.get("seam"):
            errors.append("row %r is exempt but names a `seam`: a seam is a "
                          "work order, so the verdict should be paydown" % loc)
    else:
        owner = row.get("owner") or ""
        if not owner.startswith("task "):
            errors.append(
                "row %r is a paydown but names no owning task (got %r): open "
                "work has someone doing it" % (loc, row.get("owner")))
        seam = row.get("seam") or ""
        if len(seam) < JUSTIFICATION_MIN_CHARS:
            errors.append(
                "row %r is a paydown with a %d-character seam; %d is the floor, "
                "because a work order has to name the helpers that remove the "
                "row, not merely complain about it" % (loc, len(seam),
                                                       JUSTIFICATION_MIN_CHARS))
        if row.get("invariant"):
            errors.append("row %r is a paydown but names an `invariant`: if the "
                          "count encodes something, the verdict is exempt" % loc)
    return errors


def schema_errors(data):
    """Structural problems in the debt file itself, as human-readable strings."""
    errors = []
    seen = set()
    for row in data.get("rows", []):
        for field in ("kind", "location", "value", "reason", "verdict"):
            if not row.get(field) and row.get(field) != 0:
                errors.append("row %r missing %r" % (row.get("location"), field))
        errors.extend(_verdict_errors(row))
        key = _row_key(row)
        if key in seen:
            errors.append("duplicate row for %r" % (key,))
        seen.add(key)
        loc = row.get("location", "")
        if loc.startswith(TASK21_CLEAN_PREFIXES):
            errors.append(
                "row %r forgives a violation inside a package task 21 split; "
                "those are asserted clean and may never carry debt" % loc)
        if row.get("kind") == FILE_OVER and loc.startswith(LIB_PREFIX):
            # Task 21 round 2 brought EVERY lib file under the 600-line cap, so
            # there is no residual left for a row to describe THERE. Refusing the
            # kind outright under the lib — rather than listing the twelve split
            # packages as carve-outs — is what makes that half of the file cap
            # unbreakable: a NEW over-cap lib file cannot be legalised by
            # declaring it, by any later task, under EITHER verdict.
            #
            # Task 67 widened the corpus to the dashboard, the loop and hook
            # scripts, the test corpus and the webui, and that widening DID find
            # a residual: ten files already over their cap on the day the cap
            # first applied to them. Those may be declared, because refusing the
            # row would not split them — it would only force the cap down until
            # the tree fit, which is the failure the ratchet exists to prevent.
            # The refusal stays absolute where the claim is absolute: inside the
            # lib.
            errors.append(
                "row %r declares %s under %s: the lib file cap is satisfied "
                "outright after task 21, so no lib file may be forgiven for "
                "exceeding it — split the file instead"
                % (loc, FILE_OVER, LIB_PREFIX))
    errors.extend(_counter_errors(data))
    return errors


def _counter_errors(data):
    """The population ratchet: the declared counter must equal the row count.

    Checked HERE and not only in the gate test, so the documented command
    (`python3 tests/_ceiling_gate.py`) refuses a hand-added exemption too. The
    CEILING itself is pinned by value in `tests/gates/test_module_ceilings.py`
    — one key in the fixture, one in the gate, so admitting a new exemption
    cannot be a one-line edit anywhere.

    A fixture with no `ratchet` block at all is not failed here: `load_debt`
    hands synthetic single-row fixtures to `schema_errors` throughout the
    suite, and requiring a counter on those would test the harness rather than
    the ledger. The committed fixture's counter is asserted present by its own
    gate test.
    """
    ratchet = data.get("ratchet") or {}
    if "max_rows" not in ratchet:
        return []
    errors = []
    rows = data.get("rows", [])
    declared, actual = ratchet["max_rows"], len(rows)
    if declared != actual:
        errors.append(
            "ratchet.max_rows is %r but the file carries %d row(s): the counter "
            "is how a NEW row is made to cost two deliberate edits, so it must "
            "be updated in the same commit" % (declared, actual))
    live_pd = len([r for r in rows if r.get("verdict") == PAYDOWN])
    declared_pd = ratchet.get("max_paydown_rows")
    if declared_pd is not None and declared_pd != live_pd:
        errors.append(
            "ratchet.max_paydown_rows is %r but %d row(s) carry the paydown "
            "verdict: this counter only falls, so paying a row down means "
            "lowering it here in the same commit" % (declared_pd, live_pd))
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


# --- the command ------------------------------------------------------------
#
# Task 78. Until this dispatch landed, the module had NO `if __name__` block, so
# the `python3 tests/_ceiling_gate.py` line that CONTRIBUTING.md ("the structural
# checkers") and docs/architecture.md (the gate table) both document imported the
# module, defined every function and exited 0 WITHOUT MEASURING ANYTHING. It was
# a green that could never be red, and seven task worklogs recorded it as
# verification. The invariant itself was never unguarded -- the gates tier owns
# it (`tests/gates/test_module_ceilings.py`) -- but the COMMAND was theatre, and
# a CI workflow that ran the ratchet that way would have been green by
# construction. `main` therefore runs exactly what the gate test runs, through
# the same `schema_errors` + `reconcile` pair, so the two can never disagree.
#
# `--debt PATH` exists so a test can point the command at a SHRUNK fixture and
# prove it goes red: an effectiveness claim has to be behavioural, and nothing
# else here can make this command fail on a healthy tree.

def main(argv=None):
    """Report ceiling violations not covered by the declared debt. rc 1 on any."""
    argv = list(sys.argv[1:] if argv is None else argv)
    debt_path = argv[argv.index("--debt") + 1] if "--debt" in argv else None
    data = load_debt(debt_path)
    errors = schema_errors(data)
    problems, clean = reconcile(debt=data)
    for err in errors:
        sys.stderr.write("SCHEMA %s\n" % err)
    for code, loc, detail in problems:
        sys.stderr.write("%s %s: %s\n" % (code, loc, detail))
    if errors or not clean:
        sys.stderr.write("ceiling gate FAILED: %d schema error(s), %d "
                         "problem(s)\n" % (len(errors), len(problems)))
        return 1
    sys.stdout.write("ceiling gate OK: %d file(s) scanned, %d row(s) "
                     "declared\n" % (len(corpus_files()), len(data["rows"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
