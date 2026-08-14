"""Task-17 gate: the org-root identity probe is MARKER-DRIVEN and vendor-neutral.

The donor tree anchors the org root to a project identity — a source-of-truth
guard so a stray path left in `config.env` (a `/tmp` scratch, a sibling checkout)
is REFUSED loudly instead of silently becoming the live knowledge base. That
guard is worth keeping. Its implementation was not: it read ONE build system's
manifest file, matched ONE declaration syntax, and its env key, its function, its
regex cache and its test method all named that vendor. agentware is a
vendor-neutral, cloud- and language-agnostic package (R-LOC-03), so the probe now
reads the STANDARD project marker files instead — pyproject/Cargo/setup.cfg/
package.json/composer.json/go.mod/pom.xml/Gradle, plus a bare name file as the
escape hatch for any layout the table does not know.

Genericising a guard is where guards quietly die, and NOTHING in the surface
fixtures would notice any of the three ways it could: the marker table shrinks
and the probe stops matching anything; the whole-token semantics relax to a
substring and a sibling checkout named `FooV2` satisfies `Foo`; or the deprecated
key spelling stops being read, which DISARMS the guard with nothing printed for
every operator whose config.env predates the rename (no name resolves, the probe
short-circuits to None, the mis-pointed root becomes the live KB).

None of those changes a flag, a verb, a node id or a manifest row. So this gate
asserts the behaviour, over whichever tree currently carries the modular library:

  1. KEYS        orgkeys declares the current key AND the deprecated spelling,
                 both exported, and the retired key NAME is defined nowhere.
  2. MARKERS     the marker table resolves exactly once, is a literal table of
                 (filename, template) rows, is at least MIN_MARKERS long, every
                 template is name-anchored on BOTH sides, and every filename is
                 a standard, publicly documented project marker.
  3. EXACT       behavioural, once per marker in the tree's OWN table: `Foo`
                 matches `Foo`, and NOT `FooV2`, `Foo-Tests` or `Fo`.
  4. LEGACY      behavioural: the deprecated spelling alone still arms the
                 guard, the current spelling wins when both are set, and with
                 neither set the guard is a no-op.
  5. SURFACE     both keys resolve a read site the env/config capture can SEE,
                 so neither drops out of the env parity fixture that gates them
                 while the code goes on working.
  6. NEUTRAL     zero `environment`-class portability findings at the org
                 identity paths. The needles are private (they name a specific
                 company's build system, which R-LOC-03 keeps out of this repo),
                 so this check DELEGATES to tests/_portability_gate.py and fails
                 when that class resolved no needle source — "I had nothing to
                 look for" must never read the same as "I found nothing".

Tree resolution, in order: --tree, AGENTWARE_LIB_TREE, this repo (once the lib
lands here at plan task 25), then the task-11 staging destination. If NO tree
carries the lib the gate FAILS.

Stdlib only. Every behavioural probe runs in a subprocess with
AGENTWARE_CONFIG_HOME and AGENTWARE_KNOWLEDGE_DIR redirected to throwaway paths,
so the operator's real config and knowledge base are never read or written.
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

import _env_config_scan  # noqa: E402
import _portability_gate  # noqa: E402
import _stage_donor  # noqa: E402

LIB_REL = os.path.join("scripts", "agentware_lib")

ORGKEYS_REL = os.path.join(LIB_REL, "core", "orgkeys.py")
CONFIG_REL = os.path.join(LIB_REL, "core", "config.py")
IDENTITY_REL = os.path.join(LIB_REL, "core", "orgidentity.py")

#: The env-var key names. The current spelling, and the deprecated one that is
#: still READ (never written) so an existing config.env keeps the guard.
CURRENT_KEY = "AGENTWARE_ORG_PROJECT"
LEGACY_KEY = "AGENTWARE_ORG_PACKAGE"
CURRENT_KEY_NAME = "ORG_PROJECT_KEY"
LEGACY_KEY_NAME = "ORG_PROJECT_LEGACY_KEY"
#: The constant name the rename retired. Defining it again would mean two names
#: for one fact, and the next reader picking whichever they found first.
RETIRED_KEY_NAME = "ORG_PACKAGE_KEY"

MARKER_TABLE_NAME = "PROJECT_MARKERS"
NAME_FILE_CONST = "PROJECT_NAME_FILE"
PROBE_NAME = "_dir_declares_project"
ERROR_NAME = "org_project_identity_error"
RESOLVE_NAME = "resolve_org_project"

#: Every marker the table is ALLOWED to name: standard, publicly documented
#: project manifests, plus agentware's own bare name file. An entry outside this
#: set is how a vendor manifest would come back in, one filename at a time.
ALLOWED_MARKERS = frozenset((
    "pyproject.toml", "Cargo.toml", "setup.cfg", "setup.py", "package.json",
    "composer.json", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
    "settings.gradle", "settings.gradle.kts", "CMakeLists.txt", "Gemfile",
    "*.cabal", "mix.exs", "Project.toml", ".agentware-project",
))

#: Below this the table has stopped being a table. Eight covers the major
#: ecosystems; the floor is what stops a "simplification" reducing it to one.
MIN_MARKERS = 8

#: The name the probe always ASKS for, and every declaration that must NOT
#: satisfy it. `FooV2` and `Foo-Tests` are real neighbours of a real checkout —
#: a version bump and a test package — which is what makes a substring match
#: unsafe; `PreFoo` covers the left side and `foo` the case fold.
PROBE_NAME_MATCH = "Foo"
PROBE_DECLARATIONS = ("Foo", "FooV2", "Foo-Tests", "PreFoo", "foo")

#: The paths check 6 requires to be neutral. Scoped rather than tree-wide on
#: purpose: the surrounding tree still carries findings owned by later plan
#: tasks (18 whereami, 19 fixtures, 20 docs), and a check that cannot go green
#: until those land is a check nobody reads.
#: Entries are module STEMS (no `.py`). A stem resolves to the flat `<stem>.py`
#: or, once task 21 splits it, to EVERY `.py` file under the `<stem>/` package —
#: `core/config.py` became `core/config/{configresolve,configvalidate,
#: orgresolve,...}.py` and `features/configcmd.py` became ten per-setting
#: handlers. Expanding the stem is what keeps this check honest across a
#: decomposition: a scan still pointed at `core/config.py` would inspect a file
#: that no longer exists and report neutral by looking at NOTHING, which is the
#: failure mode `OwnedPathsTest` exists to catch. Expansion also widens
#: coverage — every file the split produced must now be neutral, not just the
#: one that inherited the old name.
ORG_IDENTITY_STEMS = (
    os.path.join(LIB_REL, "core", "config"),
    os.path.join(LIB_REL, "core", "orgkeys"),
    os.path.join(LIB_REL, "core", "orgidentity"),
    os.path.join(LIB_REL, "features", "configcmd"),
    os.path.join("tests", "unit", "core", "test_config"),
    os.path.join("tests", "unit", "core", "test_orgkeys"),
    os.path.join("tests", "unit", "core", "test_orgidentity"),
    os.path.join("tests", "unit", "features", "test_configcmd"),
)

SCAN_SKIP_DIRS = (".git", "__pycache__", ".venv", "node_modules", ".mypy_cache",
                  ".cache", "logs")


def owned_paths(tree):
    """Resolve every stem against `tree`, as forward-slash relative paths.

    A stem that resolves to NOTHING is deliberately dropped here and reported by
    `unresolved_stems` instead, so "the file moved" surfaces as its own named
    failure rather than as silent under-coverage.
    """
    out = []
    for stem in ORG_IDENTITY_STEMS:
        flat = os.path.join(tree, stem + ".py")
        if os.path.isfile(flat):
            out.append(stem.replace(os.sep, "/") + ".py")
            continue
        pkg = os.path.join(tree, stem)
        if not os.path.isdir(pkg):
            continue
        for dirpath, dirs, files in os.walk(pkg):
            dirs[:] = [d for d in dirs if d not in SCAN_SKIP_DIRS]
            for name in files:
                if not name.endswith(".py"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name), tree)
                out.append(rel.replace(os.sep, "/"))
    return sorted(out)


def unresolved_stems(tree):
    """Stems matching neither a flat module nor a package — a scan pointed at
    nothing. Renaming a file must not quietly narrow this gate's scope."""
    missing = []
    for stem in ORG_IDENTITY_STEMS:
        if (not os.path.isfile(os.path.join(tree, stem + ".py"))
                and not os.path.isdir(os.path.join(tree, stem))):
            missing.append(stem.replace(os.sep, "/"))
    return missing


# --- tree resolution ---

def candidate_trees(explicit=None):
    """Every tree that might carry the lib, most authoritative first."""
    out = []
    if explicit:
        out.append(explicit)
    env = os.environ.get("AGENTWARE_LIB_TREE", "").strip()
    if env:
        out.append(env)
    out.append(REPO)
    dest = _stage_donor.resolve_dest()
    out.append(dest)
    out.append(os.path.join(dest, "donor"))
    return out


def resolve_tree(explicit=None):
    """(tree, problems) — the first candidate that actually carries the lib."""
    tried = []
    for cand in candidate_trees(explicit):
        tried.append(cand)
        if os.path.isdir(os.path.join(cand, LIB_REL)):
            return cand, []
    return None, ["no tree carries %s; tried: %s" % (LIB_REL, ", ".join(tried))]


# --- static reading ---

def _parse(tree, rel):
    path = os.path.join(tree, rel)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return ast.parse(fh.read()), None
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        return None, "%s is unreadable: %s" % (rel, exc)


def _string_assignments(mod):
    """{name: literal} for every top-level `NAME = "literal"` in a module."""
    out = {}
    for node in mod.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = node.value.value
    return out


def _list_literal(mod, name):
    """The literal string list assigned to `name`, or None."""
    for node in mod.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == name
                   for t in node.targets):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            return None
        return [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
    return None


def lib_modules(tree):
    """(relative path, parsed module) for every Python file under the lib."""
    lib = os.path.join(tree, LIB_REL)
    for base, dirs, names in os.walk(lib):
        dirs[:] = sorted(d for d in dirs if d not in SCAN_SKIP_DIRS)
        for name in sorted(n for n in names if n.endswith(".py")):
            path = os.path.join(base, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    mod = ast.parse(fh.read())
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            yield os.path.relpath(path, tree), mod


def find_marker_table(tree):
    """(rows, problems) — the ONE marker table the lib defines."""
    found = []
    for rel, mod in lib_modules(tree):
        for node in mod.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == MARKER_TABLE_NAME
                       for t in node.targets):
                continue
            found.append((rel, _marker_rows(node.value)))
    if not found:
        return None, ["no %s table found under %s"
                      % (MARKER_TABLE_NAME, LIB_REL)]
    if len(found) > 1:
        return None, ["%s is assigned in %d places: %s"
                      % (MARKER_TABLE_NAME, len(found),
                         ", ".join(rel for rel, _ in found))]
    rel, rows = found[0]
    if rows is None:
        return None, ["%s in %s is not a literal table of (marker, template) "
                      "rows, so no gate can read it" % (MARKER_TABLE_NAME, rel)]
    return rows, []


def _marker_rows(node):
    """The (marker, template) pairs of a table assignment, or None."""
    if not isinstance(node, (ast.Tuple, ast.List)):
        return None
    rows = []
    for element in node.elts:
        if not isinstance(element, (ast.Tuple, ast.List)) or len(element.elts) != 2:
            return None
        marker, template = element.elts
        if isinstance(marker, ast.Name):
            marker = ast.Constant(value=NAME_FILE_CONST)
        if not (isinstance(marker, ast.Constant)
                and isinstance(template, ast.Constant)):
            return None
        rows.append((marker.value, template.value))
    return rows


# --- check 1: keys ---

def check_keys(tree):
    problems = []
    mod, err = _parse(tree, ORGKEYS_REL)
    if err:
        return [err]
    values = _string_assignments(mod)
    for const, want in ((CURRENT_KEY_NAME, CURRENT_KEY),
                        (LEGACY_KEY_NAME, LEGACY_KEY)):
        got = values.get(const)
        if got is None:
            problems.append("%s does not define %s" % (ORGKEYS_REL, const))
        elif got != want:
            problems.append("%s = %r, expected %r" % (const, got, want))
    exports = _list_literal(mod, "_EXPORTS") or []
    for const in (CURRENT_KEY_NAME, LEGACY_KEY_NAME):
        if const not in exports:
            problems.append("%s is not in the orgkeys _EXPORTS list, so the "
                            "entrypoint facade will not bind it" % const)
    for rel, other in lib_modules(tree):
        if RETIRED_KEY_NAME in _string_assignments(other):
            problems.append("%s still defines the retired constant %s"
                            % (rel, RETIRED_KEY_NAME))
    return problems


# --- check 2: the marker table ---

def _check_marker_row(marker, template, seen):
    """Everything one (marker, template) row must satisfy, on its own."""
    problems = []
    if marker in seen:
        problems.append("marker %r appears twice" % marker)
    if marker not in ALLOWED_MARKERS and marker != NAME_FILE_CONST:
        problems.append("marker %r is not a standard project manifest; a "
                        "vendor-specific marker does not belong in a "
                        "vendor-neutral package" % marker)
    if template.count("%s") != 1:
        problems.append("marker %r template takes %d substitutions, not 1"
                        % (marker, template.count("%s")))
    _head, _, tail = template.partition("%s")
    if not tail:
        problems.append("marker %r template does not anchor the name on the "
                        "right, so a longer name would match: %r"
                        % (marker, template))
    return problems


def check_markers(tree):
    rows, problems = find_marker_table(tree)
    if problems:
        return problems
    if len(rows) < MIN_MARKERS:
        problems.append("%s holds %d marker(s), below the floor of %d"
                        % (MARKER_TABLE_NAME, len(rows), MIN_MARKERS))
    seen = set()
    for marker, template in rows:
        problems.extend(_check_marker_row(marker, template, seen))
        seen.add(marker)
    # Resolved lib-WIDE rather than per-file: the marker table and the pure
    # probe may live in their own core module while the name resolution stays
    # with the config precedence. Which file owns which is a layering choice for
    # the tree to make; that all of them EXIST is what this gate is owed.
    constants, functions = set(), set()
    for _rel, mod in lib_modules(tree):
        constants.update(_string_assignments(mod))
        functions.update(n.name for n in ast.walk(mod)
                         if isinstance(n, (ast.FunctionDef,
                                           ast.AsyncFunctionDef)))
    if NAME_FILE_CONST not in constants:
        problems.append("no lib module defines %s, the layout-agnostic escape "
                        "hatch" % NAME_FILE_CONST)
    for name in (PROBE_NAME, ERROR_NAME, RESOLVE_NAME):
        if name not in functions:
            problems.append("no lib module defines %s" % name)
    return problems


# --- behavioural probes ---

_PROBE_SRC = """
import json, os, sys
sys.path.insert(0, os.path.join(%(tree)r, "scripts"))
from agentware_lib.core import config, orgkeys
# The marker surface is read off whichever module owns it: its own core module
# when the tree splits it out, else the config module that still carries it.
markers = getattr(config, "orgidentity", config)
out = {"markers": [list(r) for r in markers.PROJECT_MARKERS],
       "name_file": markers.PROJECT_NAME_FILE,
       "cases": [], "identity": []}
name = %(match)r
bodies = %(bodies)s
for marker, _template in markers.PROJECT_MARKERS:
    if marker not in bodies:
        continue
    row = {"marker": marker, "declared": {}}
    for declared in %(declarations)r:
        d = os.path.join(%(scratch)r, "m-" + marker.replace(".", "_"),
                         "d-" + declared)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, marker), "w", encoding="utf-8") as fh:
            fh.write(bodies[marker] %% declared)
        row["declared"][declared] = bool(markers._dir_declares_project(d, name))
    out["cases"].append(row)
good = os.path.join(%(scratch)r, "declared")
os.makedirs(good, exist_ok=True)
with open(os.path.join(good, markers.PROJECT_NAME_FILE), "w") as fh:
    fh.write(name)
bad = os.path.join(%(scratch)r, "undeclared")
os.makedirs(bad, exist_ok=True)
for label, env in (("none", {}),
                   ("legacy", {orgkeys.ORG_PROJECT_LEGACY_KEY: name}),
                   ("current", {orgkeys.ORG_PROJECT_KEY: name}),
                   ("both", {orgkeys.ORG_PROJECT_KEY: name,
                             orgkeys.ORG_PROJECT_LEGACY_KEY: "SomethingElse"})):
    for key in (orgkeys.ORG_PROJECT_KEY, orgkeys.ORG_PROJECT_LEGACY_KEY):
        os.environ.pop(key, None)
    os.environ.update(env)
    out["identity"].append({
        "case": label,
        "resolved": config.resolve_org_project(),
        "declared_ok": config.org_project_identity_error(good) is None,
        "undeclared_ok": config.org_project_identity_error(bad) is None,
    })
json.dump(out, sys.stdout)
"""

#: One writable body per marker the table may name. A marker in the tree's table
#: with no body here is reported, never skipped: an unknown marker is exactly
#: what a vendor manifest sneaking back in would look like.
MARKER_BODIES = {
    "pyproject.toml": '[project]\nname = "%s"\n',
    "Cargo.toml": '[package]\nname = "%s"\n',
    "setup.cfg": "[metadata]\nname = %s\n",
    "setup.py": 'setup(\n    name="%s",\n)\n',
    "package.json": '{\n  "name": "%s",\n  "version": "1.0.0"\n}\n',
    "composer.json": '{\n  "name": "acme/%s"\n}\n',
    "go.mod": "module example.com/acme/%s\n\ngo 1.22\n",
    "pom.xml": "<project>\n  <artifactId>%s</artifactId>\n</project>\n",
    "build.gradle": "rootProject.name = '%s'\n",
    "build.gradle.kts": 'rootProject.name = "%s"\n',
    "settings.gradle": "rootProject.name = '%s'\n",
    "settings.gradle.kts": 'rootProject.name = "%s"\n',
    "CMakeLists.txt": "project(%s VERSION 1.0)\n",
    "Gemfile": "# gem %s\n",
    "mix.exs": "  def project do\n    [app: :%s]\n  end\n",
    "Project.toml": 'name = "%s"\n',
    ".agentware-project": "%s\n",
}


def run_probe(tree, scratch):
    """(payload, problems) — the behavioural facts, gathered in a subprocess."""
    src = _PROBE_SRC % {"tree": tree, "scratch": scratch,
                        "match": PROBE_NAME_MATCH,
                        "declarations": PROBE_DECLARATIONS,
                        "bodies": repr(MARKER_BODIES)}
    env = dict(os.environ)
    env["AGENTWARE_CONFIG_HOME"] = os.path.join(scratch, "config")
    env["AGENTWARE_KNOWLEDGE_DIR"] = os.path.join(scratch, "kb")
    for key in (CURRENT_KEY, LEGACY_KEY):
        env.pop(key, None)
    try:
        out = subprocess.run([sys.executable, "-c", src], capture_output=True,
                             text=True, timeout=120, env=env, cwd=scratch)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, ["probe did not run: %s" % exc]
    if out.returncode != 0:
        return None, ["probe exited %s: %s"
                      % (out.returncode, (out.stderr or "").strip()[:400])]
    try:
        return json.loads(out.stdout), []
    except ValueError as exc:
        return None, ["probe emitted no JSON (%s): %r"
                      % (exc, out.stdout[:200])]


def check_exact(payload):
    """Check 3: whole-token matching, once per marker the tree actually has."""
    problems = []
    covered = {row["marker"] for row in payload["cases"]}
    for marker, _t in payload["markers"]:
        if marker not in MARKER_BODIES:
            problems.append("marker %r has no probe body here, so its matching "
                            "was never exercised" % marker)
        elif marker not in covered:
            problems.append("marker %r was not probed" % marker)
    for row in payload["cases"]:
        for declared in PROBE_DECLARATIONS:
            matched = row["declared"].get(declared)
            if matched is None:
                problems.append("marker %r never tried the declaration %r"
                                % (row["marker"], declared))
            elif declared == PROBE_NAME_MATCH and not matched:
                problems.append("marker %r does NOT match its own declared "
                                "name %r" % (row["marker"], PROBE_NAME_MATCH))
            elif declared != PROBE_NAME_MATCH and matched:
                problems.append("marker %r declaring %r SATISFIES the name %r "
                                "— the match is not a whole token"
                                % (row["marker"], declared, PROBE_NAME_MATCH))
    return problems


#: What each identity case must observe: (resolved name, declared root accepted,
#: undeclared root accepted). `None` means "any". The undeclared root must be
#: REFUSED in every case where a name resolves — that is the guard.
IDENTITY_EXPECTED = {
    "none": (None, True, True),
    "legacy": (PROBE_NAME_MATCH, True, False),
    "current": (PROBE_NAME_MATCH, True, False),
    "both": (PROBE_NAME_MATCH, True, False),
}


def check_legacy(payload):
    """Check 4: the deprecated spelling still arms the guard; current wins."""
    problems = []
    seen = {row["case"]: row for row in payload["identity"]}
    for case, (name, declared, undeclared) in sorted(IDENTITY_EXPECTED.items()):
        row = seen.get(case)
        if row is None:
            problems.append("identity case %r was not probed" % case)
            continue
        if row["resolved"] != name:
            problems.append("case %r resolved %r, expected %r"
                            % (case, row["resolved"], name))
        if row["declared_ok"] is not declared:
            problems.append("case %r: a root DECLARING the project was %s"
                            % (case, "refused" if declared else "accepted"))
        if row["undeclared_ok"] is not undeclared:
            # Describe what ACTUALLY happened, not what was expected: accepting
            # a root that does not declare the project means the guard is gone.
            actual = bool(row["undeclared_ok"])
            problems.append(
                "case %r: a root NOT declaring the project was %s — the guard "
                "is %s" % (case, "accepted" if actual else "refused",
                           "disarmed" if actual else "over-eager"))
    return problems


# --- check 5: vendor neutrality, delegated to the portability gate ---

def check_surface(tree):
    """Check 6: BOTH keys resolve a read site the env/config capture can see.

    The env parity fixture is derived by resolving the literal constant handed to
    the config resolver, so a perfectly working read written as a loop over a key
    tuple attributes to NEITHER key — the keys vanish from the fixture that gates
    them while the code keeps working. That is invisible to every other check
    here, so it gets its own.
    """
    try:
        captured = _env_config_scan.capture_tree(tree)
    except (OSError, ValueError, SyntaxError) as exc:
        return ["env/config capture did not run: %s" % exc]
    problems = []
    for key in (CURRENT_KEY, LEGACY_KEY):
        if not captured.get(key):
            problems.append("%s resolves no read site the env/config capture "
                            "can see, so it would drop out of the env parity "
                            "fixture; hand the key CONSTANT to the resolver "
                            "directly rather than through a loop variable" % key)
    return problems


def check_neutral(tree, needles_path=None):
    """Zero `environment`-class findings at the org identity paths.

    The needles live outside this repo (R-LOC-03), so an unavailable needle
    source is a FAILURE of this check, never a quiet pass.
    """
    try:
        report = _portability_gate.build_report(
            trees=(tree,), needles_path=needles_path,
            surfaces=(_portability_gate.CONTENTS, _portability_gate.FILENAMES))
    except (OSError, ValueError) as exc:
        return ["portability scan did not run: %s" % exc]
    unavailable = _portability_gate.unavailable_required(
        report, (_portability_gate.ENVIRONMENT,))
    if unavailable:
        return ["the environment needle class resolved no source, so this "
                "check saw nothing; point %s at the needle file"
                % _portability_gate.NEEDLE_ENV]
    owned = tuple(owned_paths(tree))
    problems = []
    for row in report["findings"]:
        if row["class"] != _portability_gate.ENVIRONMENT:
            continue
        where = row["location"].replace(os.sep, "/")
        if where.split(":")[0] in owned:
            problems.append("%s carries a vendor token (%s, %s)"
                            % (row["location"], row["surface"], row["detail"]))
    return problems


# --- driver ---

def check(tree, needles_path=None):
    problems = []
    for name, fn in (("keys", check_keys), ("markers", check_markers)):
        problems.extend("[%s] %s" % (name, p) for p in fn(tree))
    scratch = tempfile.mkdtemp(prefix="aw-orgident-")
    try:
        payload, probe_problems = run_probe(tree, scratch)
        if probe_problems:
            problems.extend("[probe] %s" % p for p in probe_problems)
        else:
            problems.extend("[exact] %s" % p for p in check_exact(payload))
            problems.extend("[legacy] %s" % p for p in check_legacy(payload))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    problems.extend("[surface] %s" % p for p in check_surface(tree))
    problems.extend("[neutral] %s" % p
                    for p in check_neutral(tree, needles_path))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", help="tree carrying scripts/agentware_lib")
    ap.add_argument("--needles", help="explicit private needle file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    tree, problems = resolve_tree(args.tree)
    if not problems:
        problems = check(tree, args.needles)
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
