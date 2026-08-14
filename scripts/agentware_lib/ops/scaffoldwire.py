"""agentware_lib.ops.scaffoldwire — WRITE a scaffolded unit and its wiring (layer: ops).

Owns: the write half of `scaffold new` — creating the unit file and its mirrored
test (`write_text`), inserting the README doc row (`insert_doc_row`) and the
curated parity row (`insert_parity_row`) into the ledgers that own them, invoking
the two REGENERATORS whose output must not be hand-edited (`regenerate`), the
manual follow-ups a generator must not fake (`next_steps`), and the one
transaction that does all of it (`emit`).
Does NOT own: what a unit IS or what its artifacts SAY -> ops.scaffoldunit renders
every byte this module writes; the audit over an already-written tree ->
ops.scaffoldcheck; the argparse surface -> ops.scaffoldcmd. Nor the generators
themselves: `tests/_manifest_gen.py` and `tests/_parity_manifest.py` are RUN as
subprocesses, never imported, so the test corpus stays downstream of the package.
Layer deps: ops -> same-layer ops.scaffoldunit plus stdlib. All DOWNWARD.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

WHY THE GENERATORS ARE RUN, NOT REPRODUCED
------------------------------------------
`catalog/modules.json` and `tests/fixtures/parity-manifest.json` are GENERATED
artifacts whose gates assert byte-identity with a fresh regeneration. A scaffold
that wrote its own manifest entry — however carefully — would be a second
implementation of the generator, and the first field either one got wrong would
show up as "regenerating produces a diff", which is precisely the failure this
verb exists to prevent. So the wiring this module writes by hand is only the
HAND-MAINTAINED half (the module, the test, the README row, the curated parity
row, all of which a human authors), and everything derived is derived by its own
generator, in a subprocess, exactly as `R-MOD-07` and `R-MOD-08` instruct.

WHY THE PARSER PINS ARE A `next_step`, NOT AN EDIT
--------------------------------------------------
A new verb changes `--help`, and `tests/gates/test_parser_golden.py` pins the
whole ordered verb list INDEPENDENTLY of the declarations, on purpose: it is the
only place the surface is written down in one piece, so a declaration silently
dropped or re-keyed lands there as a diff. A scaffold that edited that pin would
certify its own change and destroy the property the pin exists for. So the verb
kind REPORTS the two pin edits and the corpus regeneration as steps the author
performs, and does not perform them.

Stdlib only. Writes ONLY inside the package checkout — never the knowledge base.
"""

import os
import subprocess
import sys

import agentware_lib.ops.scaffoldunit as unitmod
from agentware_lib.ops.scaffoldunit import ScaffoldError


#: The regenerators, in the order `R-MOD-07`/`R-MOD-08` name them. Each is a
#: repo-relative script plus its argv; both are stdlib-only and deterministic.
REGENERATORS = (
    ("manifest", ("tests/_manifest_gen.py", "--write")),
    ("parity", ("tests/_parity_manifest.py", "--out")),
)

#: Where a doc row is inserted: after the LAST row of the module-router table.
DOC_ROW_PREFIX = "| `agentware_lib."


def _abs(root, rel):
    return os.path.join(unitmod.repo_root(root), rel)


def write_text(root, rel, text, force=False):
    """Create `rel` with `text`. Refuses to clobber unless `force`.

    Creates the parent directory, and gives a NEW test package the `__init__.py`
    that makes it importable — a test file dropped into a directory with no
    package marker is collected by neither discovery nor pytest, so it would
    "pass" by never running.
    """
    dest = _abs(root, rel)
    if os.path.exists(dest) and not force:
        raise ScaffoldError("refusing to overwrite %s (pass --force to replace)"
                            % rel)
    parent = os.path.dirname(dest)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    if rel.startswith("tests/"):
        marker = os.path.join(parent, "__init__.py")
        if not os.path.exists(marker):
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write("")
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(text)
    return rel


def insert_doc_row(root, unit):
    """Append the unit's row to the README module-router table.

    Inserted after the LAST existing row rather than at a line number, so the
    table can grow, shrink or be re-ordered without moving the insertion point.
    """
    rel = unitmod.README_REL
    path = _abs(root, rel)
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines(True)
    last = None
    for i, line in enumerate(lines):
        if line.startswith(DOC_ROW_PREFIX):
            last = i
    if last is None:
        raise ScaffoldError("%s has no module-router table to extend" % rel)
    lines.insert(last + 1, unitmod.render_doc_row(unit) + "\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(lines))
    return rel


def insert_parity_row(root, unit):
    """Append the unit's curated row to `CURATED_ROWS` in tests/_parity_curated.py.

    Inserted before the table's closing bracket — the last line of the file that
    is exactly `]` — so the row joins the list rather than following it.
    """
    rel = unitmod.CURATED_REL
    path = _abs(root, rel)
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines(True)
    close = None
    for i, line in enumerate(lines):
        if line.rstrip("\n") == "]":
            close = i
    if close is None:
        raise ScaffoldError("%s does not end in a closing `]`: refusing to guess "
                            "where CURATED_ROWS ends" % rel)
    lines.insert(close, unitmod.render_parity_row(unit))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(lines))
    return rel


def regenerate(root=None):
    """Run every derived-artifact regenerator. Returns `[(name, rc, output)]`."""
    top = unitmod.repo_root(root)
    out = []
    for name, argv in REGENERATORS:
        script = os.path.join(top, argv[0])
        if not os.path.exists(script):
            out.append((name, 127, "missing regenerator: %s" % argv[0]))
            continue
        proc = subprocess.run([sys.executable, script] + list(argv[1:]),
                              cwd=top, capture_output=True, text=True)
        out.append((name, proc.returncode,
                    (proc.stdout or "").strip() or (proc.stderr or "").strip()))
    return out


def next_steps(unit):
    """The follow-ups a generator must not fake, in the order they are done."""
    steps = ["fill in every %s marker in %s and %s"
             % (unitmod.TODO, unit["path"], unit["test_path"])]
    if unit["kind"] == unitmod.KIND_VERB:
        steps.append("add %r to EXPECTED_VERBS (last) in "
                     "tests/gates/test_parser_golden.py" % unit["verb"])
        steps.append("raise EXPECTED_SURFACES in tests/gates/test_parser_golden.py "
                     "by 1 per new help surface (the verb plus each sub-verb)")
        steps.append("regenerate the rendered help corpus: "
                     "python3 tests/_help_golden.py --write")
    if unit["kind"] == unitmod.KIND_LOOP:
        steps.append("source scripts/loop/%s.sh from agentware.sh AND call its "
                     "function — a definition with no call site is the fail-open "
                     "shape tests/_loop_wiring.py catches" % unit["name"])
    steps.append("run: agentware scaffold check")
    return steps


def emit(unit, root=None, dry_run=False, force=False, regen=True):
    """Write the unit and all of its wiring. Returns a JSON-able result record.

    `dry_run` computes the same record and touches nothing, so an author can see
    every path a scaffold would claim before it claims any of them.
    """
    plan = {"unit": unit, "created": [unit["path"], unit["test_path"]],
            "edited": [unitmod.CURATED_REL], "regenerated": [],
            "next_steps": next_steps(unit), "dry_run": bool(dry_run)}
    if unit["doc_row"]:
        plan["edited"].insert(0, unitmod.README_REL)
    if dry_run:
        return plan
    write_text(root, unit["path"], unitmod.render_unit(unit, root), force)
    write_text(root, unit["test_path"], unitmod.render_test(unit), force)
    if unit["doc_row"]:
        insert_doc_row(root, unit)
    insert_parity_row(root, unit)
    if regen:
        plan["regenerated"] = [{"generator": name, "rc": rc, "output": out}
                               for name, rc, out in regenerate(root)]
    return plan


_EXPORTS = [
    "REGENERATORS",
    "write_text",
    "insert_doc_row",
    "insert_parity_row",
    "regenerate",
    "next_steps",
    "emit",
]
