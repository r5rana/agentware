"""Task-18 gate: `whereami` resolves in ANY repository layout, not one vendor's.

`whereami` answers the question every session-start hook and every
steering-capture auto-scope asks: given a directory, which project am I in and
which checkout is it part of? The donor implementation answered it with ONE
build system's layout — a directory carrying that vendor's marker file whose
PARENT was named `src` — so two constraints were baked in, and BOTH fail silent:

  * the vendor marker: no other ecosystem's project could ever resolve; and
  * the SRC-PARENT constraint: even that vendor's own package failed to resolve
    anywhere else, and any repository whose projects are not one directory below
    a `src/` directory resolved to nothing at all.

Nothing goes red when that happens. The verb exits 0, the JSON payload carries
empty strings, the hook prints no context banner, and the agent starts work with
no idea which checkout it is in — which is exactly how a resolver silently
serving one layout survives a suite, a surface fixture and a help golden intact.

So this gate asserts the BEHAVIOUR, over whichever tree currently carries the
modular library:

  1. LAYOUTS     behavioural, once per marker in the tree's OWN root-marker
                 table (the three the plan names — pyproject, a package
                 manifest, a go module — plus every other one, reported and
                 never skipped): the project resolves from its own root AND
                 from a nested subdirectory, with the checkout root reported
                 separately.
  2. LAYOUT-FREE behavioural: the SAME project resolves under parents named
                 `src`, `tools`, `packages` and at the checkout root itself —
                 the dropped constraint — while a directory whose only marker is
                 a vendor's own file resolves NOTHING (the escape hatch, not a
                 special case, is how such a layout declares itself).
  3. NOWHERE     behavioural: an unmarked directory and a path that does not
                 exist each exit 0 with empty fields and an EMPTY stderr, in
                 both output formats. "Not in a project" is an answer, not an
                 error — and never a traceback.
  4. MARKERS     static: the root-marker table resolves exactly ONCE lib-wide,
                 is DERIVED from the identity table (so the two can never
                 disagree about what a project is), and every filename in it is
                 a standard, publicly documented project manifest — an entry
                 outside that set is how a vendor marker returns, one filename
                 at a time. The ALLOWED set is `_orgidentity_gate`'s: one list,
                 one place, shared by both gates.
  5. HOME        static: the resolver is defined in a module the LAYER GRAPH
                 lets every caller reach. `whereami` renders it, but the
                 steering-capture auto-scope resolves the same context from the
                 features layer, and features may not import the cli leaf — so a
                 resolver homed in the leaf is a resolver only one caller can
                 have, and the second caller would grow its own copy.
  6. MIRRORS     behavioural: the payload carries BOTH key vocabularies and each
                 deprecated key equals the canonical field it mirrors, so the
                 merged surface drops no donor consumer and the two names can
                 never disagree about one resolution.

Tree resolution, in order: --tree, AGENTWARE_LIB_TREE, this repo (once the lib
lands here at plan task 25), then the task-11 staging destination. If NO tree
carries the lib the gate FAILS — a gate that finds nothing to inspect must never
report success.

Stdlib only. Every behavioural probe runs in a subprocess with
AGENTWARE_CONFIG_HOME and AGENTWARE_KNOWLEDGE_DIR redirected to throwaway paths,
so the operator's real config and knowledge base are never read or written, and
every scratch layout is built under a temp dir this gate removes.
"""

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _orgidentity_gate  # noqa: E402

LIB_REL = _orgidentity_gate.LIB_REL
ENTRYPOINT_REL = os.path.join("scripts", "agentware")

#: The derived table every project-root probe reads, and the identity table it
#: must be derived FROM.
ROOT_TABLE_NAME = "PROJECT_ROOT_MARKERS"
IDENTITY_TABLE_NAME = _orgidentity_gate.MARKER_TABLE_NAME

#: The pure resolver and the payload assembler the verb renders.
RESOLVER_NAME = "_resolve_context"
PAYLOAD_NAME = "whereami_payload"

#: The layer whose modules every other layer may import. A resolver outside it
#: is reachable by the cli leaf and nothing else.
RESOLVER_LAYER = "core"

#: The three layouts the plan names by hand. Every OTHER marker in the tree's
#: own table is probed too; these three must be present, so a table that quietly
#: dropped one is a failure rather than three fewer silent cases.
REQUIRED_LAYOUT_MARKERS = ("pyproject.toml", "package.json", "go.mod")

#: Parent directory names the project is placed under, in turn. `src` is the one
#: the replaced resolver REQUIRED; the rest are the layouts it could not see.
#: The empty string places the project AT the checkout root.
PARENT_NAMES = ("src", "tools", "packages", "")

#: A marker file a real vendor build system uses and no public ecosystem does.
#: Named here (rather than in the private needle file) because the string is a
#: generic English word: it identifies no vendor on its own, and the gate needs
#: a concrete non-standard filename to prove the table refuses one.
NON_STANDARD_MARKER = "Config"

#: The escape-hatch filename, used ONLY when the tree carries no marker table to
#: read it from (a tree this gate is otherwise failing anyway). The live value is
#: always orgidentity.PROJECT_NAME_FILE, read in-process by the probe.
NAME_FILE_FALLBACK = ".agentware-project"

PROJECT_NAME = "TheProject"

#: The canonical payload fields and the deprecated mirrors, as the merged
#: surface must serve them.
CANONICAL_FIELDS = ("invoked_from", "repo_root", "project_name", "project_dir")
MIRROR_FIELDS = (("workspace_root", "repo_root"),
                 ("package_name", "project_name"),
                 ("package_dir", "project_dir"))


# --- tree resolution (shared with the task-17 gate: one ladder, one place) ---

def resolve_tree(explicit=None):
    return _orgidentity_gate.resolve_tree(explicit)


def _parse(tree, rel):
    return _orgidentity_gate._parse(tree, rel)


# --- static reading ---

def _assignments(mod, name):
    """Every top-level statement assigning `name`, as AST value nodes."""
    out = []
    for node in mod.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            out.append(node.value)
    return out


def _defined_functions(mod):
    return {n.name for n in ast.walk(mod)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def find_root_table(tree):
    """([rel paths that assign it], the single assignment node or None)."""
    owners, value = [], None
    for rel, mod in _orgidentity_gate.lib_modules(tree):
        found = _assignments(mod, ROOT_TABLE_NAME)
        if found:
            owners.append(rel)
            value = found[-1] if len(found) == 1 else None
            if len(found) > 1:
                owners.append(rel)  # counted twice: assigned twice in one file
    return owners, value


def _literal_names(node):
    """Every string literal reachable in a tuple/list/BinOp expression."""
    out = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            out.append(child.value)
    return out


def _referenced_names(node):
    """Every bare Name the expression reads (the tables a derived one composes)."""
    return {c.id for c in ast.walk(node) if isinstance(c, ast.Name)}


def _references(node, name):
    """True iff `name` appears as a Name node anywhere in the expression."""
    return name in _referenced_names(node)


# --- check 4: the root-marker table ---

def check_markers(tree):
    problems = []
    owners, value = find_root_table(tree)
    if not owners:
        return ["no lib module defines %s, the project-root marker table"
                % ROOT_TABLE_NAME]
    if len(owners) > 1:
        problems.append("%s is assigned in %d places (%s); two tables is how a "
                        "marker gets added to one and not the other"
                        % (ROOT_TABLE_NAME, len(owners), ", ".join(owners)))
        return problems
    if value is None:
        return problems
    if not _references(value, IDENTITY_TABLE_NAME):
        problems.append("%s is not DERIVED from %s, so the location table and "
                        "the identity table can drift apart"
                        % (ROOT_TABLE_NAME, IDENTITY_TABLE_NAME))
    # Being derived is what makes the table safe to READ and what makes it hard
    # to CHECK: the filenames are not in this expression, they are in the tables
    # it references. So follow every reference. The identity table is skipped
    # because _orgidentity_gate.check_markers already vets its filenames (and
    # its rows carry regex templates, which are not markers); anything ELSE it
    # composes — a presence-only table, a bare name file — is checked here, or a
    # vendor filename simply lands one table to the left of the one being read.
    owner_rel = owners[0]
    mod = dict(_orgidentity_gate.lib_modules(tree)).get(owner_rel)
    for ref in sorted(_referenced_names(value) - {IDENTITY_TABLE_NAME}):
        for composed in _assignments(mod, ref) if mod else []:
            for marker in _literal_names(composed):
                if marker not in _orgidentity_gate.ALLOWED_MARKERS:
                    problems.append(
                        "%s composes %s, which names %r — not a standard "
                        "project manifest; a vendor-specific marker does not "
                        "belong in a vendor-neutral package"
                        % (ROOT_TABLE_NAME, ref, marker))
    for marker in _literal_names(value):
        if marker not in _orgidentity_gate.ALLOWED_MARKERS:
            problems.append("root marker %r is not a standard project "
                            "manifest; a vendor-specific marker does not belong "
                            "in a vendor-neutral package" % marker)
    return problems


# --- check 5: where the resolver lives ---

def check_home(tree):
    """The resolver must live where every LAYER can reach it."""
    problems = []
    homes = []
    for rel, mod in _orgidentity_gate.lib_modules(tree):
        if RESOLVER_NAME in _defined_functions(mod):
            homes.append(rel)
    if not homes:
        return ["no lib module defines %s" % RESOLVER_NAME]
    if len(homes) > 1:
        problems.append("%s is defined in %d modules (%s); the second copy is "
                        "the one that stops being fixed"
                        % (RESOLVER_NAME, len(homes), ", ".join(homes)))
    for rel in homes:
        parts = rel.replace(os.sep, "/").split("/")
        layer = parts[2] if len(parts) > 3 else "cli"
        if layer != RESOLVER_LAYER:
            problems.append(
                "%s lives in the %r layer; the steering-capture auto-scope "
                "resolves the same context from the features layer and may not "
                "import the cli leaf, so it belongs in %r"
                % (rel, layer, RESOLVER_LAYER))
    return problems


# --- behavioural probe ---

_PROBE_SRC = r"""
import json, os, subprocess, sys
tree, scratch, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, os.path.join(tree, "scripts"))

# A tree with no root-marker table is a tree this gate has PLENTY to say about,
# so the behavioural half must still run: fall back to the layouts the plan
# names by hand and let the resolver fail them on its own terms. Aborting here
# would turn "the resolver serves one vendor layout" into one import error.
try:
    from agentware_lib.core import orgidentity
    MARKERS = list(orgidentity.PROJECT_ROOT_MARKERS)
    NAME_FILE = orgidentity.PROJECT_NAME_FILE
    TABLE_PRESENT = True
except (ImportError, AttributeError):
    MARKERS = list(%(required)r)
    NAME_FILE = %(name_file)r
    TABLE_PRESENT = False

# Address the SUBJECT, not the path. `<tree>/scripts/agentware` is a strangler
# facade from plan task 25 on, and it routes by LEGACY DEFAULT: until `whereami`
# is promoted onto the lib (task 54) the entrypoint serves the monolith's old
# src-parent-constrained resolver, so a probe through the entrypoint would report
# task 18's rewrite as broken in every tree that carries both. Driving
# `agentware_lib.cli.main` runs exactly what the donor entrypoint ran and exactly
# what the entrypoint will run once the verb is promoted.
#
# A tree with no lib CLI leaf (the synthetic trees this gate's own mutation cases
# build, where the entrypoint IS the only implementation) falls back to the
# entrypoint. The mode is reported in the payload, never inferred, so a lib that
# stops being importable shows up as a MODE CHANGE rather than as a silent
# re-measurement of the legacy resolver.
scripts_dir = os.path.join(tree, "scripts")
lib_cli = os.path.join(scripts_dir, "agentware_lib", "cli.py")
entry_mode = "entrypoint"
try:
    with open(lib_cli, encoding="utf-8") as fh:
        if "def main(" in fh.read():
            entry_mode = "lib"
except OSError:
    pass
if entry_mode == "lib":
    entry_argv = [sys.executable, "-c",
                  "import sys; sys.path.insert(0, " + repr(scripts_dir)
                  + "); from agentware_lib.cli import main; sys.exit(main())"]
else:
    entry_argv = [sys.executable, os.path.join(scripts_dir, "agentware")]
env = dict(os.environ)
env["AGENTWARE_CONFIG_HOME"] = os.path.join(scratch, "config-home")
env["AGENTWARE_KNOWLEDGE_DIR"] = os.path.join(scratch, "kb")
env.pop("AGENTWARE_INVOKED_FROM", None)

def run(where, fmt="json"):
    proc = subprocess.run(entry_argv + ["whereami", "--dir", where,
                                        "--format", fmt],
                          capture_output=True, text=True, env=env)
    row = {"rc": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    if fmt == "json" and proc.returncode == 0:
        try:
            row["data"] = json.loads(proc.stdout)
        except ValueError as exc:
            row["parse_error"] = str(exc)
    return row

def mk(*parts):
    p = os.path.join(scratch, *parts)
    os.makedirs(p, exist_ok=True)
    return p

def mark(path, marker):
    with open(os.path.join(path, marker), "w", encoding="utf-8") as fh:
        fh.write("name = 'Whatever'\n")
    return path

out = {"markers": MARKERS, "table_present": TABLE_PRESENT,
       "entry_mode": entry_mode,
       "layouts": [], "parents": [], "nowhere": {}, "mirrors": {}}

# 1. one checkout per marker: <root>/.git + <root>/pkg/<Name>/<marker> + nested
for marker in MARKERS:
    slug = marker.replace(".", "_")
    root = mk("layouts", slug)
    os.makedirs(os.path.join(root, ".git"), exist_ok=True)
    proj = mark(mk("layouts", slug, "pkg", %(project)r), marker)
    deep = mk("layouts", slug, "pkg", %(project)r, "a", "b")
    out["layouts"].append({"marker": marker, "root": root, "project": proj,
                           "deep": deep, "from_root": run(proj),
                           "from_deep": run(deep)})

# 2. the same marker under every parent name, and at the checkout root
for i, parent in enumerate(%(parents)r):
    root = mk("parents", "p%%d" %% i)
    os.makedirs(os.path.join(root, ".git"), exist_ok=True)
    proj = mark(mk("parents", "p%%d" %% i, *( [parent, %(project)r] if parent
                                              else [] )) if parent
                else root, %(required)r[0])
    out["parents"].append({"parent": parent, "project": proj, "root": root,
                           "result": run(proj)})

# 3. a directory whose ONLY marker is a non-standard one, under a src/ parent —
#    the exact shape the replaced resolver accepted and nothing else did.
vendor = mark(mk("vendor", "src", %(project)r), %(nonstandard)r)
out["nowhere"]["vendor_only"] = run(vendor)
out["nowhere"]["unmarked"] = run(mk("bare", "deep", "path"))
out["nowhere"]["missing"] = run(os.path.join(scratch, "no", "such", "dir"))
out["nowhere"]["unmarked_text"] = run(mk("bare2"), fmt="text")
# ...and the escape hatch resolves it, so the refusal above is a policy, not a
# dead end for a layout the table does not know.
mark(vendor, NAME_FILE)
out["nowhere"]["vendor_with_escape_hatch"] = run(vendor)

# 4. the payload key vocabularies
root = mk("mirrors")
os.makedirs(os.path.join(root, ".git"), exist_ok=True)
proj = mark(mk("mirrors", "svc"), %(required)r[0])
out["mirrors"] = {"project": proj, "root": root, "result": run(proj)}

with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(out, fh)
"""


def run_probe(tree, scratch):
    """(payload, problems) — every behavioural case, in ONE subprocess."""
    src = _PROBE_SRC % {"project": PROJECT_NAME, "parents": PARENT_NAMES,
                        "required": REQUIRED_LAYOUT_MARKERS,
                        "name_file": NAME_FILE_FALLBACK,
                        "nonstandard": NON_STANDARD_MARKER}
    script = os.path.join(scratch, "_probe.py")
    out_path = os.path.join(scratch, "probe.json")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(src)
    env = dict(os.environ)
    env["AGENTWARE_CONFIG_HOME"] = os.path.join(scratch, "config-home")
    env["AGENTWARE_KNOWLEDGE_DIR"] = os.path.join(scratch, "kb")
    proc = subprocess.run([sys.executable, script, tree,
                           os.path.join(scratch, "work"), out_path],
                          capture_output=True, text=True, env=env)
    if proc.returncode != 0 or not os.path.exists(out_path):
        return None, ["the behavioural probe did not run (rc=%d): %s"
                      % (proc.returncode,
                         (proc.stderr or proc.stdout).strip()[-600:])]
    with open(out_path, encoding="utf-8") as fh:
        return json.load(fh), []


# --- check 1: the layout matrix ---

def _resolution_problems(label, row, want_project, want_name, want_root):
    problems = []
    if row["rc"] != 0:
        problems.append("%s exited %d (stderr: %s)"
                        % (label, row["rc"], row["stderr"].strip()[:200]))
        return problems
    data = row.get("data")
    if data is None:
        problems.append("%s did not emit parseable JSON: %s"
                        % (label, row.get("parse_error", row["stdout"][:120])))
        return problems
    for field, want in (("project_dir", want_project),
                        ("project_name", want_name),
                        ("repo_root", want_root)):
        if data.get(field) != want:
            problems.append("%s resolved %s=%r, expected %r"
                            % (label, field, data.get(field), want))
    return problems


def check_layouts(payload):
    problems = []
    covered = {row["marker"] for row in payload["layouts"]}
    for marker in (REQUIRED_LAYOUT_MARKERS if payload.get("table_present")
                   else ()):
        if marker not in covered:
            problems.append("the tree's root-marker table has no %r row, so the "
                            "layout the plan names by hand went unprobed"
                            % marker)
    for row in payload["layouts"]:
        marker = row["marker"]
        problems.extend(_resolution_problems(
            "%s from the project root" % marker, row["from_root"],
            row["project"], PROJECT_NAME, row["root"]))
        problems.extend(_resolution_problems(
            "%s from a nested subdirectory" % marker, row["from_deep"],
            row["project"], PROJECT_NAME, row["root"]))
    return problems


# --- check 2: no layout constraint ---

def check_layout_free(payload):
    problems = []
    seen = set()
    for row in payload["parents"]:
        parent = row["parent"] or "(checkout root)"
        seen.add(row["parent"])
        want_name = os.path.basename(row["project"])
        problems.extend(_resolution_problems(
            "a project under %r" % parent, row["result"],
            row["project"], want_name, row["root"]))
    for parent in PARENT_NAMES:
        if parent not in seen:
            problems.append("the probe never placed a project under %r" % parent)

    vendor = payload["nowhere"]["vendor_only"]
    if vendor["rc"] != 0:
        problems.append("a non-standard marker made whereami exit %d rather "
                        "than answer" % vendor["rc"])
    elif (vendor.get("data") or {}).get("project_dir"):
        problems.append("a directory whose only marker is %r resolved as a "
                        "project (%r); the vendor marker is back"
                        % (NON_STANDARD_MARKER,
                           vendor["data"]["project_dir"]))
    escape = payload["nowhere"]["vendor_with_escape_hatch"]
    if not (escape.get("data") or {}).get("project_dir"):
        problems.append("the name-file escape hatch did not resolve a layout "
                        "the marker table does not know, so refusing the "
                        "non-standard marker leaves that layout with no way in")
    return problems


# --- check 3: "nowhere" is an answer ---

def check_nowhere(payload):
    problems = []
    for label in ("unmarked", "missing"):
        row = payload["nowhere"][label]
        if row["rc"] != 0:
            problems.append("%s path exited %d; not being in a project is an "
                            "answer, not an error" % (label, row["rc"]))
            continue
        if row["stderr"].strip():
            problems.append("%s path wrote to stderr: %s"
                            % (label, row["stderr"].strip()[:200]))
        data = row.get("data") or {}
        for field in ("project_name", "project_dir", "repo_root"):
            if field not in data:
                problems.append("%s path emitted no %s field at all"
                                % (label, field))
            elif data[field] != "":
                problems.append("%s path resolved %s=%r, expected an empty "
                                "string" % (label, field, data[field]))
    text = payload["nowhere"]["unmarked_text"]
    if text["rc"] != 0:
        problems.append("text format exited %d outside any project" % text["rc"])
    elif "No project resolved" not in text["stdout"]:
        problems.append("text format outside any project printed %r, which "
                        "names no outcome the operator can act on"
                        % text["stdout"].strip()[:120])
    return problems


# --- check 6: both key vocabularies ---

def check_mirrors(payload):
    row = payload["mirrors"]["result"]
    if row["rc"] != 0:
        return ["the mirror probe exited %d" % row["rc"]]
    data = row.get("data")
    if data is None:
        return ["the mirror probe emitted no parseable JSON"]
    problems = []
    for field in CANONICAL_FIELDS:
        if field not in data:
            problems.append("the payload has no canonical %r field" % field)
    for mirror, canonical in MIRROR_FIELDS:
        if mirror not in data:
            problems.append("the payload dropped the deprecated %r key, so a "
                            "consumer written against it silently reads nothing"
                            % mirror)
        elif data[mirror] != data.get(canonical):
            problems.append("%r=%r disagrees with %r=%r; two names for one "
                            "resolution must be copies"
                            % (mirror, data[mirror], canonical,
                               data.get(canonical)))
    return problems


# --- driver ---

def check(tree):
    problems = []
    for name, fn in (("markers", check_markers), ("home", check_home)):
        problems.extend("[%s] %s" % (name, p) for p in fn(tree))
    scratch = tempfile.mkdtemp(prefix="aw-whereami-")
    try:
        payload, probe_problems = run_probe(tree, scratch)
        if probe_problems:
            problems.extend("[probe] %s" % p for p in probe_problems)
        else:
            for name, fn in (("layouts", check_layouts),
                             ("layout-free", check_layout_free),
                             ("nowhere", check_nowhere),
                             ("mirrors", check_mirrors)):
                problems.extend("[%s] %s" % (name, p) for p in fn(payload))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", help="tree carrying scripts/agentware_lib")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    tree, problems = resolve_tree(args.tree)
    if not problems:
        problems = check(tree)
    if args.json:
        print(json.dumps({"tree": tree, "ok": not problems,
                          "problems": problems}, indent=2))
    else:
        print("tree: %s" % (tree or "(none)"))
        for p in problems:
            print("PROBLEM %s" % p)
        print("%s %d problem(s)" % ("FAIL" if problems else "OK", len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
