"""tests/_manifest_gen.py — GENERATE the tree-wide discovery manifest from AST.

`catalog/modules.json` answers the operator's #1 pain — "find the owning module
without reading the monolith" — and until plan task 67 it answered it for
`scripts/agentware_lib/**` only, by hand. Both halves of that were failing:

  * By HAND. There was no generator in this tree at all. The committed file had
    drifted to 193 entries against 199 modules on disk (six unclaimed, including
    `ops/orgmigrate.py` and `retrieval/workcorpus.py`), with 577 stale fields —
    `dependency_direction` values like `"features -> features, features -> ops"`
    that no rule produces, `owns` text a docstring had since replaced, and
    `depended_on_by` lists missing edges that exist. A manifest maintained by
    hand is a document ABOUT the tree; the point is for it to BE the tree.
  * For the LIB ONLY. The four subsystems most likely to become the next
    monolith — the 1,146-line dashboard, the split loop and hook scripts, the
    test corpus's own helpers, the webui source — were in no manifest and under
    no cap. A ceiling that covers only what was already split records past work
    instead of constraining future work.

So this module generates BOTH arrays, and nothing in either is typed by a human:

  * ``modules`` — the RICH lib view. One entry per `agentware_lib/**/*.py` that
    is not a package `__init__.py`: the ONE domain it owns, what it does NOT own
    plus a pointer, its test file, its layer and allowed dependency direction,
    grep anchor symbols, and both fan-out (``depends_on``) and fan-in
    (``depended_on_by``). The prose fields are PARSED from the module's own
    docstring labels (`Owns:` / `Does NOT own:` / `Layer deps:`), so there is
    exactly one place to edit them — the module itself.
  * ``files`` — the WIDE view, task 67. One entry per corpus member from
    `tests/_corpus.py`: every Python and shell file under `scripts/`, the
    orchestrator, the test corpus and the webui source, each carrying its
    ``layer`` and its ``line_cap``. This array is what makes "every file on disk
    is claimed" checkable, and it is the completeness gate a later task's
    scaffold verb writes into.

Deliberately NOT emitted: a live line COUNT per file. It would churn the
manifest on every edit to every file in the tree, and it would be a second,
staler copy of a number `_ceiling_gate` reads from disk anyway. The manifest
publishes the CAP (which changes when the contract changes); the gate measures
the value (which changes constantly). Cap and value living in different artifacts
is what keeps the manifest reviewable.

The corpus, the layers and the caps come from `tests/_corpus.py`; the branch
metric from `tests/_ceiling_gate.py`; the layer-direction rules from
`tests/_layerutil.py`. This module owns ASSEMBLY and nothing else, so a cap can
never mean one thing to the manifest and another to the gate.

Underscore-prefixed so ``unittest discover`` never collects it as a test. Runs
both as an imported helper (``from tests._manifest_gen import build_manifest``)
and as a writer (``python3 tests/_manifest_gen.py --write``).

Layer: TEST/TOOLING support, not an agentware_lib module — it owns manifest
generation and no runtime behaviour.
"""

import ast
import glob
import importlib.util
import json
import os
import re
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import _corpus  # noqa: E402
import _layerutil  # noqa: E402

REPO_ROOT = _corpus.REPO_ROOT
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
LIB_DIR = os.path.join(SCRIPTS_DIR, "agentware_lib")
CATALOG_PATH = os.path.join(REPO_ROOT, "catalog", "modules.json")
GRANULAR_UNIT_ROOT = os.path.join(REPO_ROOT, "tests", "unit")
STATIC_PATH = os.path.join(_TESTS_DIR, "fixtures", "manifest-static.json")

SCHEMA_VERSION = 3
STATIC_SCHEMA = "agentware/manifest-static@1"
SOURCE_REPO = "github.com/r5rana/agentware"

#: The docstring labels every agentware_lib module carries so its first ~10
#: lines answer "is my code already here?" at the grep surface.
LABELS = ("Owns:", "Does NOT own:", "Layer deps:")
FANIN_RE = re.compile(r"^Fan-in:.*$", re.MULTILINE)

DESCRIPTION = (
    "GENERATED discovery manifest for the agentware tree. `modules` is the rich "
    "per-module view of scripts/agentware_lib (the ONE domain each owns, what it "
    "does NOT own + a pointer, its test file, layer, allowed dependency "
    "direction, grep anchors, the CLI verbs it declares in its `__verbs__`, "
    "fan-out and fan-in). `files` is the tree-wide "
    "claim: every Python and shell file under scripts, the orchestrator, tests "
    "and the webui source, each with its layer and line cap, so no file can be "
    "added without an owner. `caps` publishes the ceilings, including the "
    "per-FUNCTION line and branch caps no file cap can express. DO NOT hand-edit: "
    "regenerate with 'python3 tests/_manifest_gen.py --write'; "
    "tests/gates/test_module_manifest.py asserts this file is byte-identical to a "
    "fresh AST regeneration, that every file on disk is claimed, and that every "
    "file is within its cap.")


def _fanin_line(count):
    """The exact ``Fan-in:`` docstring line for a computed fan-in ``count``."""
    return ("Fan-in: %d (modules that import this; see catalog/modules.json "
            "depended_on_by; regen: python3 tests/_manifest_gen.py --write)."
            % count)


def _rel(path):
    return os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")


# --- module discovery -------------------------------------------------------

def domain_modules(root=None):
    """Every agentware_lib module file except package __init__.py files.

    ``root`` overrides the checkout, so a gate can enumerate a scratch tree
    without writing inside the repository under test.
    """
    lib = LIB_DIR if root is None else os.path.join(root, "scripts",
                                                    "agentware_lib")
    return sorted(
        f for f in glob.glob(os.path.join(lib, "**", "*.py"), recursive=True)
        if os.path.basename(f) != "__init__.py")


def _dotted(mod_path):
    return _layerutil.module_dotted(mod_path, SCRIPTS_DIR)


def _lib_layer(mod_path):
    rel = os.path.relpath(mod_path, LIB_DIR)
    parts = rel.split(os.sep)
    return parts[0] if len(parts) > 1 else _layerutil.CLI_LAYER


def granular_test_file(mod_path, root=None):
    """tests/unit/<layer>/test_<stem>.py — the CONVENTIONAL home for a module's
    unit file, and the ONLY thing that can own it.

    This is the target, not a claim that the file exists: whether it does is
    `test_file_present`, and ENFORCING it is the test-corpus ownership ratchet's
    job (`tests/_test_ownership.py`, plan task 71), not the manifest's. The
    mapping lives here, once, and the ratchet reads it — the manifest's published
    `test_file` column and the rule that enforces it cannot disagree, because
    there is only one function to disagree with.

    ``root`` overrides the checkout so the ratchet can answer for a scratch tree.
    """
    base = REPO_ROOT if root is None else root
    lib = LIB_DIR if root is None else os.path.join(base, "scripts",
                                                    "agentware_lib")
    unit_root = (GRANULAR_UNIT_ROOT if root is None
                 else os.path.join(base, "tests", "unit"))
    rel = os.path.relpath(mod_path, lib)
    parts = rel.split(os.sep)
    stem = os.path.splitext(parts[-1])[0]
    layer = parts[0] if len(parts) > 1 else _layerutil.CLI_LAYER
    return os.path.join(unit_root, layer, "test_%s.py" % stem.lstrip("_"))


def _dependency_direction(layer):
    allowed = sorted(_layerutil.ALLOWED_BELOW.get(layer, set()))
    return "%s -> %s" % (layer, ", ".join(allowed) if allowed else "(nothing above)")


# --- AST extraction ---------------------------------------------------------

def _parse(mod_path):
    with open(mod_path, encoding="utf-8") as f:
        return ast.parse(f.read())


def _str_const(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def anchor_symbols(mod_path):
    """Top-level def/class names + __all__/_EXPORTS entries — the grep anchors."""
    names = set()
    for node in _parse(mod_path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(_export_names(node))
    return sorted(names)


#: The module attribute an owning module declares its verbs in. Duplicated from
#: ``agentware_lib.verbs.DECLARATION`` on purpose and asserted equal by
#: ``tests/gates/test_verb_discovery.py``: the manifest is generated from the
#: AST WITHOUT importing the package, so it cannot read the runtime constant,
#: and a gate whose two halves import each other proves nothing about either.
VERB_DECLARATION = "__verbs__"


def declared_verbs(mod_path):
    """The ``__verbs__`` rows a module declares, as ``{verb, order, attr}``.

    Read from the AST, never by importing. This is the manifest's half of the
    verb-discovery completeness gate (plan task 70): `agentware_lib.verbs` finds
    declarations by IMPORTING the package, this finds them by READING it, and
    `tests/gates/test_verb_discovery.py` asserts the two agree. A declaration
    only one of them can see -- a module that fails to import, a module the walk
    never reaches, a tuple built at runtime rather than written down -- is
    exactly the silent divergence that pairing makes loud.

    Order is the DECLARED order within the module, not the sort order: the
    manifest records what each file says, and the display order the sparse key
    produces is the CLI's business.
    """
    out = []
    for node in _parse(mod_path).body:
        if VERB_DECLARATION not in _targets(node):
            continue
        for elt in getattr(node.value, "elts", []):
            row = [_literal(e) for e in getattr(elt, "elts", [])]
            if len(row) == 3:
                out.append({"verb": row[0], "order": row[1], "attr": row[2]})
    return out


def _targets(node):
    """Assigned names of a module-level assignment (plain or annotated)."""
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def _literal(node):
    """A str/int constant's value, or None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int)):
        return node.value
    return None


def _export_names(node):
    out = set()
    for tgt in node.targets:
        if not (isinstance(tgt, ast.Name) and tgt.id in ("_EXPORTS", "__all__")):
            continue
        if isinstance(node.value, (ast.List, ast.Tuple)):
            for el in node.value.elts:
                v = _str_const(el)
                if v is not None:
                    out.add(v)
    return out


def _import_from_targets(node, pkg):
    """Dotted module names an ImportFrom references, INCLUDING the
    ``from pkg import submodule`` case a layer-only resolver drops."""
    out = []
    prefix = "." * node.level
    base = None
    if node.module is not None or node.level:
        try:
            base = importlib.util.resolve_name(prefix + (node.module or ""), pkg)
        except (ImportError, ValueError):
            base = None
    if base:
        out.append(base)
        for alias in node.names:
            if alias.name != "*":
                out.append(base + "." + alias.name)
    return out


def depends_on(mod_path, universe):
    """Dotted agentware_lib DOMAIN modules this module imports — absolute and
    relative, including lazy in-function imports."""
    tree = _parse(mod_path)
    dotted = _dotted(mod_path)
    pkg = dotted.rsplit(".", 1)[0] if "." in dotted else dotted
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in universe:
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            for tgt in _import_from_targets(node, pkg):
                if tgt in universe:
                    found.add(tgt)
    found.discard(dotted)  # never self
    return sorted(found)


# --- docstring metadata (owns / not_owns / layer deps) ----------------------

def module_docstring(mod_path):
    return ast.get_docstring(_parse(mod_path)) or ""


def parse_docstring_meta(mod_path):
    """The labelled Owns / Does NOT own / Layer deps lines from a docstring.

    A label's value runs to the next label or the next blank line, with wrapped
    continuation lines JOINED — the local convention writes multi-line `Owns:`
    text, and a first-line-only parser would silently truncate two thirds of the
    manifest's most useful field.
    """
    meta = {"owns": "", "not_owns": "", "layer_deps": "",
            "has_fanin": False, "fanin_declared": None}
    current = None
    for line in module_docstring(mod_path).splitlines():
        s = line.strip()
        key = _label_key(s)
        if key:
            current = key
            meta[key] = s.split(":", 1)[1].strip()
        elif s.startswith("Fan-in:"):
            current = None
            meta["has_fanin"] = True
            m = re.match(r"Fan-in:\s*(\d+)", s)
            if m:
                meta["fanin_declared"] = int(m.group(1))
        elif current and s:
            meta[current] = (meta[current] + " " + s).strip()
        else:
            current = None
    return meta


def _label_key(stripped_line):
    for label, key in (("Owns:", "owns"), ("Does NOT own:", "not_owns"),
                       ("Layer deps:", "layer_deps")):
        if stripped_line.startswith(label):
            return key
    return None


def docstring_missing_labels(mod_path):
    """The required labels a module's docstring is MISSING (empty -> all present)."""
    doc = module_docstring(mod_path)
    return [lbl for lbl in LABELS if lbl not in doc]


# --- manifest assembly ------------------------------------------------------

def build_modules():
    """The rich, filesystem-validated ``modules`` array."""
    mods = domain_modules()
    universe = set(_dotted(m) for m in mods)
    edges = dict((_dotted(m), depends_on(m, universe)) for m in mods)
    fanin = dict((d, set()) for d in universe)
    for src, targets in edges.items():
        for tgt in targets:
            fanin[tgt].add(src)
    return sorted((_module_entry(m, edges, fanin) for m in mods),
                  key=lambda e: e["name"])


def _module_entry(mod_path, edges, fanin):
    dotted = _dotted(mod_path)
    layer = _lib_layer(mod_path)
    meta = parse_docstring_meta(mod_path)
    test_file = granular_test_file(mod_path)
    return {
        "name": dotted,
        "path": _rel(mod_path),
        "layer": layer,
        "owns": meta["owns"],
        "not_owns": meta["not_owns"],
        "test_file": _rel(test_file),
        "test_file_present": os.path.exists(test_file),
        "dependency_direction": _dependency_direction(layer),
        "anchor_symbols": anchor_symbols(mod_path),
        "verbs": declared_verbs(mod_path),
        "depends_on": edges[dotted],
        "depended_on_by": sorted(fanin[dotted]),
    }


def build_files():
    """The tree-wide ``files`` array — one entry per corpus member (task 67)."""
    out = []
    for rel in _corpus.corpus_files(REPO_ROOT):
        layer = _corpus.layer_of(rel)
        out.append({"path": rel, "layer": layer,
                    "line_cap": _corpus.cap_of(layer)})
    return sorted(out, key=lambda e: e["path"])


def load_static(path=None):
    """The declared (non-filesystem-derived) routing tables."""
    with open(path or STATIC_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("schema") != STATIC_SCHEMA:
        raise ValueError("manifest-static schema mismatch: %r" % data.get("schema"))
    return data


def build_manifest():
    """The full manifest dict. Deterministic: serialize with sort_keys=True."""
    static = load_static()
    return {
        "schema": SCHEMA_VERSION,
        "description": DESCRIPTION,
        "source_repo": SOURCE_REPO,
        "provenance": "generated-from-ast",
        "generator": "tests/_manifest_gen.py",
        "regen": "python3 tests/_manifest_gen.py --write",
        "layer_direction": dict(
            (layer, sorted(allowed))
            for layer, allowed in _layerutil.ALLOWED_BELOW.items()),
        "caps": _corpus.caps_block(),
        "excluded": _corpus.exclusions_block(),
        "modules": build_modules(),
        "files": build_files(),
        "naming_disambiguation": static["naming_disambiguation"],
        "planned_layout": static["planned_layout"],
    }


def serialize(manifest):
    """Deterministic JSON text (sorted keys, 2-space indent, trailing newline)."""
    return json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


# --- claims (the completeness check the gate runs) --------------------------

def load_committed(path=None):
    """The manifest AS COMMITTED, parsed.

    The completeness checks below default to THIS and never to
    `build_manifest()`. Comparing disk against a freshly-built manifest is
    vacuous by construction — the builder enumerates the same disk, so a file
    added without regenerating the catalog is "claimed" the instant the check
    runs, and the criterion "a file added without an entry fails the check"
    silently becomes untestable. That vacuity was live here until an end-to-end
    probe added a real module to a scratch copy of the tree and the gate stayed
    green.
    """
    with open(path or CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


def claimed_paths(manifest=None):
    """Every repo-relative path the COMMITTED manifest's ``files`` array claims."""
    manifest = load_committed() if manifest is None else manifest
    return set(e["path"] for e in manifest.get("files", []))


def unclaimed(paths, manifest=None):
    """Corpus members with no manifest entry and no declared exclusion.

    Takes the path LIST rather than reading disk itself, so the check can also be
    refuted with a synthetic path — proving it reports an unclaimed file without
    writing one into the checkout under test.
    """
    claims = claimed_paths(manifest)
    return sorted(p for p in paths
                  if p not in claims and not _corpus.is_excluded(p))


def phantom(manifest=None, root=None):
    """Claimed paths that are not on disk — the check's other direction."""
    manifest = load_committed() if manifest is None else manifest
    base = root or REPO_ROOT
    return sorted(p for p in claimed_paths(manifest)
                  if not os.path.exists(os.path.join(base, p)))


# --- writers (--write) ------------------------------------------------------

def write_manifest():
    """(Re)write catalog/modules.json from the current tree. Returns the text."""
    text = serialize(build_manifest())
    os.makedirs(os.path.dirname(CATALOG_PATH), exist_ok=True)
    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    return text


def sync_fanin_doclines():
    """Update each domain module's ``Fan-in:`` docstring line to the AST count.

    Surgical: only the single ``Fan-in:`` line changes (distinctive marker, one
    per file). Modules missing the line get it inserted immediately after their
    ``Layer deps:`` line. Returns the list of modified paths.

    OPT-IN (``--sync-fanin``), not part of ``--write``. Roughly forty of this
    tree's Fan-in lines carry hand-written provenance the canonical form cannot
    express — "Fan-in: 0 at landing; grows to 2 with tasks 41-42", "Fan-in: 1 —
    agentware_lib.strangler, which imports main() INSIDE dispatch()" — and a
    blanket rewrite would delete that prose from ~199 files to fix a number the
    manifest already reports correctly. Closing that drift is a deliberate edit
    with a deliberate diff, not a side effect of regenerating a catalog.
    """
    counts = dict((m["name"], len(m["depended_on_by"]))
                  for m in build_manifest()["modules"])
    changed = []
    for mod_path in domain_modules():
        want = _fanin_line(counts[_dotted(mod_path)])
        with open(mod_path, encoding="utf-8") as f:
            src = f.read()
        new = _apply_fanin(src, want)
        if new != src:
            with open(mod_path, "w", encoding="utf-8") as f:
                f.write(new)
            changed.append(_rel(mod_path))
    return changed


def _apply_fanin(src, want):
    if FANIN_RE.search(src):
        return FANIN_RE.sub(want.replace("\\", "\\\\"), src, count=1)
    out, inserted = [], False
    for line in src.splitlines(keepends=True):
        out.append(line)
        if not inserted and line.strip().startswith("Layer deps:"):
            out.append(want + ("\n" if line.endswith("\n") else ""))
            inserted = True
    return "".join(out)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--write" in argv or "--sync-fanin" in argv:
        changed = sync_fanin_doclines() if "--sync-fanin" in argv else []
        # Regenerate AFTER doclines are synced so the manifest reflects the
        # final state — the sync changes file bytes the manifest describes.
        write_manifest()
        print("wrote %s" % os.path.relpath(CATALOG_PATH, REPO_ROOT))
        if changed:
            print("updated Fan-in doclines in:\n  " + "\n  ".join(changed))
        return 0
    sys.stdout.write(serialize(build_manifest()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
