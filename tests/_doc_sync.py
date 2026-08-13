#!/usr/bin/env python3
"""Keep the two DOCUMENTED module maps in sync with the generated manifest.

Underscore-prefixed so `unittest discover` never COLLECTS it (the convention
`tests/_manifest_gen.py` and `tests/_test_ownership.py` already carry); it is a
standalone, stdlib-only, py3.7-safe writer meant to be run directly:

    python3 tests/_doc_sync.py --check    # report drift, exit 1
    python3 tests/_doc_sync.py --write    # rewrite the managed blocks

TWO MAPS, ONE SOURCE
--------------------
`catalog/modules.json` is AST-generated and is the machine-readable source of
truth (R-MOD-07). Two documents render it for humans and they render DIFFERENT
columns on purpose, so there is exactly one copy of every fact:

  * `scripts/agentware_lib/README.md` — the ROUTER: module -> layer -> **owns**
    -> owning test. The `owns` prose is HAND-WRITTEN and is never regenerated
    here: it is the one column a generator would flatten (the manifest's own
    `owns` is the docstring's `Owns:` line, which the router deliberately
    paraphrases for a reader who is scanning). This writer only ADDS the rows
    the manifest publishes and the table is missing, seeding the new row's prose
    from the manifest so it is never empty, and it always owns the LAYER and
    TEST columns, which are facts, not prose.
  * `docs/architecture.md` — the contributor doc: module -> owning test ->
    fan-in, grouped by layer, plus the layer-direction table and the ceiling
    table. Every one of those is rendered from the manifest between managed
    markers and may not be hand-edited.

So the two maps duplicate only the NAME SET, and that duplication is exactly
what `tests/_doc_extensibility_check.py` gates in both directions.

WHY INSERTION, NOT A FULL REWRITE, FOR THE ROUTER
-------------------------------------------------
The router's row order is historical (extraction-wave order, grouped by layer),
and re-sorting it would produce a 200-row diff that hides the one row a change
actually adds. A missing row is therefore inserted after the LAST existing row
of its own layer — the same "anchor on the table, not on a line number" rule
`ops.scaffoldwire.insert_doc_row` uses — which keeps the grouping, keeps every
existing row byte-identical, and is idempotent: a second run finds nothing
missing and writes nothing.

Stdlib only, deterministic, read-only outside the two documents it manages.
Never touches the knowledge base.
"""
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)

CATALOG_REL = "catalog/modules.json"
README_REL = "scripts/agentware_lib/README.md"
ARCH_REL = "docs/architecture.md"

#: Layer render order — the dependency direction itself, lowest first.
LAYER_ORDER = ("core", "knowledge", "retrieval", "ops", "features", "cli")

#: A router row, keyed by the module it claims. Must stay byte-compatible with
#: `ops.scaffoldcheck.DOC_ROW_RE`, which is what `agentware scaffold check`
#: reads: one space either side of every pipe.
ROUTER_ROW_RE = re.compile(r"^\| `(agentware_lib\.[A-Za-z0-9_.]+)` \| ([a-z]+) \|",
                           re.M)
ROUTER_ROW_PREFIX = "| `agentware_lib."

#: The managed blocks in docs/architecture.md. Each is rendered from the
#: manifest; a hand edit inside one is drift and `--check` reports it.
BLOCKS = ("module-map", "layer-direction", "ceilings")
BEGIN = "<!-- BEGIN GENERATED: %s (python3 tests/_doc_sync.py --write) -->"
END = "<!-- END GENERATED: %s -->"


def load_manifest(root=None):
    path = os.path.join(root or REPO_ROOT, CATALOG_REL)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _read(root, rel):
    with open(os.path.join(root or REPO_ROOT, rel), encoding="utf-8") as handle:
        return handle.read()


def _write(root, rel, text):
    with open(os.path.join(root or REPO_ROOT, rel), "w", encoding="utf-8") as handle:
        handle.write(text)


def cell(text):
    """A markdown table cell: one line, with the column separator escaped."""
    return " ".join(str(text).split()).replace("|", "\\|")


# --------------------------------------------------------------------------
# the router table (scripts/agentware_lib/README.md)
# --------------------------------------------------------------------------

def router_rows(text):
    """{module name: layer} for every row the router table claims."""
    return dict(ROUTER_ROW_RE.findall(text))


def render_router_row(entry):
    """The router row for a module the table is missing.

    The prose is SEEDED from the manifest's `owns` (the module's own docstring
    line) rather than left as a TODO: an empty cell in a router is worse than a
    verbose one, and the author is free to paraphrase it afterwards — nothing
    regenerates this column.
    """
    return "| `%s` | %s | %s | `%s` |" % (
        entry["name"], entry["layer"], cell(entry["owns"]),
        entry["test_file"])


def sync_router(root=None, write=False):
    """Add a row for every manifest module the router does not claim.

    Returns (added_names, new_text). `added_names` is empty when the router is
    already complete, which is the state task 74 leaves the tree in.
    """
    text = _read(root, README_REL)
    claimed = router_rows(text)
    manifest = load_manifest(root)
    missing = [m for m in manifest["modules"] if m["name"] not in claimed]
    if not missing:
        return [], text
    lines = text.splitlines(True)
    # Last row index per layer, so an inserted row joins its own layer's run.
    last_of_layer, last_any = {}, None
    for i, line in enumerate(lines):
        if not line.startswith(ROUTER_ROW_PREFIX):
            continue
        match = ROUTER_ROW_RE.match(line)
        if match:
            last_of_layer[match.group(2)] = i
        last_any = i
    if last_any is None:
        raise SystemExit("%s has no module-router table to extend" % README_REL)
    by_layer = {}
    for entry in missing:
        by_layer.setdefault(entry["layer"], []).append(entry)
    # Insert from the bottom up so earlier insertion points keep their indices.
    for layer in sorted(by_layer, key=lambda l: last_of_layer.get(l, last_any),
                        reverse=True):
        at = last_of_layer.get(layer, last_any) + 1
        rows = sorted(by_layer[layer], key=lambda e: e["name"])
        lines[at:at] = [render_router_row(e) + "\n" for e in rows]
    new_text = "".join(lines)
    if write:
        _write(root, README_REL, new_text)
    return sorted(e["name"] for e in missing), new_text


# --------------------------------------------------------------------------
# the managed blocks (docs/architecture.md)
# --------------------------------------------------------------------------

def render_module_map(manifest):
    """Module -> owning test -> fan-in, grouped by layer.

    Deliberately NOT the `owns` column: that is the router's, and rendering it
    twice would be two copies of one fact drifting apart between regenerations.
    """
    out = ["_%d modules. Generated from `%s`; the `Owns:` column lives in the"
           " [router table](../scripts/agentware_lib/README.md) beside it._"
           % (len(manifest["modules"]), CATALOG_REL), ""]
    by_layer = {}
    for entry in manifest["modules"]:
        by_layer.setdefault(entry["layer"], []).append(entry)
    for layer in LAYER_ORDER:
        entries = sorted(by_layer.get(layer, []), key=lambda e: e["name"])
        if not entries:
            continue
        out.append("#### `%s/` — %d modules" % (layer, len(entries)))
        out.append("")
        out.append("| Module | Owning test | Fan-in |")
        out.append("|--------|-------------|--------|")
        for entry in entries:
            out.append("| `%s` | `%s` | %d |" % (
                entry["name"], entry["test_file"],
                len(entry.get("depended_on_by") or [])))
        out.append("")
    return "\n".join(out).rstrip("\n")


def render_layer_direction(manifest):
    """The layer contract, rendered from the manifest's `layer_direction`."""
    direction = manifest["layer_direction"]
    out = ["| Layer | May import at module level | Imports nothing from |",
           "|-------|----------------------------|----------------------|"]
    for layer in LAYER_ORDER:
        allowed = direction.get(layer, [])
        forbidden = [l for l in LAYER_ORDER if l != layer and l not in allowed]
        out.append("| `%s` | %s | %s |" % (
            layer,
            ", ".join("`%s`" % a for a in sorted(allowed)) or "_nothing_",
            ", ".join("`%s`" % f for f in forbidden) or "_nothing_"))
    return "\n".join(out)


def render_ceilings(manifest):
    """The ceiling table, rendered from the manifest's `caps`."""
    caps = manifest["caps"]
    out = ["| Scope | Cap |", "|-------|-----|"]
    for layer, cap in sorted(caps["file_lines_by_layer"].items()):
        out.append("| `%s` file | %d lines |" % (layer, cap))
    out.append("| any function | %d lines |" % caps["function_lines"])
    out.append("| any function | %d branches |" % caps["function_branches"])
    return "\n".join(out)


RENDERERS = {
    "module-map": render_module_map,
    "layer-direction": render_layer_direction,
    "ceilings": render_ceilings,
}


def block_span(text, name):
    """(start, end) character offsets of one managed block's BODY."""
    begin, end = BEGIN % name, END % name
    i = text.find(begin)
    j = text.find(end)
    if i < 0 or j < 0 or j < i:
        raise SystemExit("%s: managed block %r is missing its markers"
                         % (ARCH_REL, name))
    return i + len(begin), j


def sync_architecture(root=None, write=False):
    """Rewrite every managed block from the manifest. Returns the stale names."""
    text = _read(root, ARCH_REL)
    manifest = load_manifest(root)
    stale, new_text = [], text
    for name in BLOCKS:
        body = "\n\n%s\n\n" % RENDERERS[name](manifest)
        start, stop = block_span(new_text, name)
        if new_text[start:stop] != body:
            stale.append(name)
            new_text = new_text[:start] + body + new_text[stop:]
    if write and stale:
        _write(root, ARCH_REL, new_text)
    return stale, new_text


def drift(root=None):
    """Every managed surface that a regeneration would change. Empty == in sync."""
    problems = []
    added, _ = sync_router(root)
    problems.extend("%s: no router row for %s" % (README_REL, name)
                    for name in added)
    stale, _ = sync_architecture(root)
    problems.extend("%s: managed block %r is stale" % (ARCH_REL, name)
                    for name in stale)
    return problems


def main(argv):
    write = "--write" in argv[1:]
    if write:
        added, _ = sync_router(write=True)
        stale, _ = sync_architecture(write=True)
        sys.stdout.write("router rows added: %d%s\narchitecture blocks "
                         "rewritten: %s\n"
                         % (len(added), (" (%s)" % ", ".join(added)) if added else "",
                            ", ".join(stale) or "none"))
        return 0
    problems = drift()
    if not problems:
        sys.stdout.write("OK: the router table and every managed block in %s "
                         "match %s\n" % (ARCH_REL, CATALOG_REL))
        return 0
    sys.stderr.write("FAIL: %d documented-map drift(s); run "
                     "`python3 tests/_doc_sync.py --write`\n" % len(problems))
    for problem in problems:
        sys.stderr.write("  - %s\n" % problem)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
