"""Per-tree scanners for the task-9 preserved-surface freeze.

This module owns the SCANNERS only; `tests/_preserved_freeze.py` owns the merge,
the fixture derivation and the gates. Stdlib only.

Six surfaces are scanned, none of which any argparse dump (tasks 6/7) or
env/config fixture (task 8) can see:

  1. test NODE IDS      -- `unittest` discovery ids, collected in a SUBPROCESS
                           (two trees both define the `tests.*` package, so an
                           in-process collection of the second tree resolves the
                           first tree's already-imported modules)
  2. CLI SYMBOLS        -- every attribute the test corpus reads off the module
                           returned by `load_cli()`, resolved SCOPE-AWARE
  3. EXECUTABLES        -- every file under scripts/ carrying the user-exec bit
  4. TEMPLATES          -- every templates/*.md with CONTENT anchors, so a
                           regenerated template that lost its contract reddens
  5. CATALOGS           -- catalog/*.json by BASENAME (never by file count)
  6. FIXTURE FAMILIES   -- the three fixture families the merge must preserve

Every recorded path is repo-relative and every recorded root is home-relativised,
per `learn-capture-scripts-neutralise-env-and-redact-paths`.
"""

import ast
import json
import os
import subprocess
import sys
import tempfile

# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

#: Directories never walked when looking for test sources.
SKIP_DIRS = frozenset({"__pycache__", ".git", "fixtures", "node_modules"})

#: Scopes that introduce a new name-binding namespace.
SCOPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

#: The three fixture families the merge must preserve, and how each is claimed.
FIXTURE_FAMILIES = (
    ("gold_fixture_suite", "tests/fixtures/gold_fixture_suite.json", "file"),
    ("swe_tasks", "tests/fixtures/swe_tasks", "dir"),
    ("help_golden", "tests/fixtures/help_golden", "dir"),
)

#: Content anchors per template BASENAME. An anchor is a substring that must
#: survive any regeneration of that template. Anchors deliberately pin the
#: SEMANTIC contract (field names, status vocabulary, the portability note) and
#: never the markdown preamble, because task 51 adds a frontmatter block to the
#: local templates and a preamble anchor would redden that reconciliation.
TEMPLATE_ANCHORS = {
    "learning-template.md": ("**Created**: YYYY-MM-DD", "**Tags**:"),
    "project-template.md": ("**Created**: YYYY-MM-DD", "**Tags**:", "**Status**:"),
    "skill-template.md": ("name: <skill-folder-name>", "agentskills.io",
                          "HARNESS-AGNOSTIC"),
    "decision-template.md": ("**Created**: YYYY-MM-DD", "**Tags**:"),
}


def relativise(path):
    """Home-relativise an absolute path so a fixture is machine-independent."""
    home = os.path.expanduser("~")
    real = os.path.realpath(path)
    if real == home:
        return "~"
    if real.startswith(home + os.sep):
        return "~" + real[len(home):]
    return real


def walk_py(root, subdir):
    """Yield (abs_path, repo_rel_path) for every .py file under root/subdir."""
    base = os.path.join(root, subdir)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and d.isidentifier())
        for name in sorted(filenames):
            if name.endswith(".py"):
                abs_path = os.path.join(dirpath, name)
                yield abs_path, os.path.relpath(abs_path, root)


def git_head(root):
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip()[:12] if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - defensive
        return ""


# --------------------------------------------------------------------------
# 1. test node ids  (subprocess-isolated)
# --------------------------------------------------------------------------

def collect_node_ids_inprocess(root):
    """Collect unittest node ids for `root`. MUST run in a dedicated process.

    Returns (sorted ids, sorted load-error descriptions).
    """
    root = os.path.realpath(root)
    for key in [k for k in os.environ if k.startswith("AGENTWARE_")]:
        del os.environ[key]
    sandbox = tempfile.mkdtemp(prefix="aw-nodeid-")
    os.environ["AGENTWARE_KNOWLEDGE_DIR"] = os.path.join(sandbox, "kb")
    os.environ["AGENTWARE_CONFIG_HOME"] = os.path.join(sandbox, "cfg")
    # The suite's own recursion guard: several modules exercise verbs that shell
    # `unittest discover`. Without it, collection can re-enter the whole suite
    # (`learn-baseline-harness-nested-unittest-recursion-guard`).
    os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"
    os.makedirs(os.environ["AGENTWARE_KNOWLEDGE_DIR"], exist_ok=True)
    sys.path.insert(0, os.path.join(root, "scripts"))
    sys.path.insert(0, root)
    os.chdir(root)

    import unittest  # imported here so the env is neutralised first

    ids, errors = [], []

    def walk(suite):
        for test in suite:
            if isinstance(test, unittest.TestSuite):
                walk(test)
            elif type(test).__name__ == "_FailedTest":
                errors.append(test.id())
            else:
                ids.append(test.id())

    loader = unittest.defaultTestLoader
    walk(loader.discover("tests", top_level_dir=root))
    errors.extend(str(e[0]) for e in (loader.errors or []))
    return sorted(set(ids)), sorted(set(errors))


def collect_node_ids(root):
    """Collect node ids for `root` in a SUBPROCESS, returning (ids, errors).

    Isolation is not a nicety: `tests` is a top-level package name in BOTH
    trees, so a second in-process discovery silently re-collects the first
    tree's already-imported modules and the two frozen sets come out identical.
    """
    worker = (
        "import json,sys;"
        "sys.path.insert(0, %r);"
        "from _preserved_scan import collect_node_ids_inprocess as c;"
        "ids, errs = c(%r);"
        "sys.stdout.write(json.dumps({'ids': ids, 'errors': errs}))"
        % (os.path.dirname(os.path.realpath(__file__)), os.path.realpath(root))
    )
    proc = subprocess.run([sys.executable, "-c", worker],
                          capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise SystemExit("node-id collection failed for %s (rc=%d)\n%s"
                         % (root, proc.returncode, proc.stderr[-2000:]))
    payload = json.loads(proc.stdout)
    return payload["ids"], payload["errors"]


def module_of(node_id):
    """`tests.pkg.test_mod.Class.test_method` -> `tests.pkg.test_mod`."""
    return ".".join(node_id.split(".")[:-2])


def tail_of(node_id):
    """`tests.pkg.test_mod.Class.test_method` -> `Class.test_method`."""
    return ".".join(node_id.split(".")[-2:])


# --------------------------------------------------------------------------
# 2. CLI symbols  (scope-aware)
# --------------------------------------------------------------------------

def _is_load_cli(node):
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id == "load_cli"
    return isinstance(fn, ast.Attribute) and fn.attr == "load_cli"


class ScopeIndex:
    """Name bindings per lexical scope, tracking CLI-vs-other and shadowing.

    A file-global binding tracker over-captures badly: on this tree it reports
    951 symbols of which 377 do not exist on the CLI module, because a name
    bound to `load_cli()` at module level is re-bound to an unrelated local
    inside a test method. Resolving each attribute access against its NEAREST
    enclosing binding scope, and requiring every binding in that scope to be
    `load_cli()`, cuts it to 350 with ZERO unreachable names.
    """

    def __init__(self):
        self.assigns = {}
        self.parent = {}
        self.attr_cli = set()
        self.attr_other = set()

    def build(self, tree):
        self._walk(tree, None)
        return self

    def _record(self, scope, name, kind):
        self.assigns.setdefault(scope, {}).setdefault(name, set()).add(kind)

    def _bind(self, scope, targets, value):
        kind = "cli" if _is_load_cli(value) else "other"
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name):
                    self._record(scope, sub.id, kind)
                elif (isinstance(sub, ast.Attribute)
                      and isinstance(sub.value, ast.Name)
                      and sub.value.id in ("self", "cls")):
                    bucket = self.attr_cli if kind == "cli" else self.attr_other
                    bucket.add(sub.attr)

    def _bind_defs(self, scope, child):
        """Record def/class/import/arg bindings as non-CLI."""
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._record(scope, child.name, "other")
            args = child.args
            for arg in (list(args.posonlyargs) + list(args.args)
                        + list(args.kwonlyargs)):
                self._record(child, arg.arg, "other")
        elif isinstance(child, ast.ClassDef):
            self._record(scope, child.name, "other")
        elif isinstance(child, (ast.Import, ast.ImportFrom)):
            for alias in child.names:
                self._record(scope, (alias.asname or alias.name).split(".")[0],
                             "other")

    def _walk(self, node, scope):
        if isinstance(node, SCOPES):
            self.parent[node] = scope
            scope = node
            self.assigns.setdefault(node, {})
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Assign):
                self._bind(scope, child.targets, child.value)
            elif isinstance(child, ast.AnnAssign) and child.value is not None:
                self._bind(scope, [child.target], child.value)
            elif isinstance(child, (ast.For, ast.AsyncFor)):
                self._bind(scope, [child.target], child.iter)
            elif isinstance(child, ast.With):
                for item in child.items:
                    if item.optional_vars is not None:
                        self._bind(scope, [item.optional_vars], item.context_expr)
            else:
                self._bind_defs(scope, child)
            self._walk(child, scope)

    def resolves_to_cli(self, name, scope):
        """True when `name` in `scope` resolves to a binding that is only CLI."""
        current = scope
        while current is not None:
            kinds = self.assigns.get(current, {}).get(name)
            if kinds:
                return kinds == {"cli"}
            current = self.parent.get(current)
        return False


def _scan_cli_file(abs_path, rel_path, out):
    try:
        with open(abs_path, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=abs_path)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return
    index = ScopeIndex().build(tree)
    cli_attrs = index.attr_cli - index.attr_other

    def visit(node, scope):
        if isinstance(node, SCOPES) and node in index.parent:
            scope = node
        if isinstance(node, ast.Attribute):
            value = node.value
            direct = isinstance(value, ast.Name) and index.resolves_to_cli(value.id, scope)
            viaself = (isinstance(value, ast.Attribute) and value.attr in cli_attrs
                       and isinstance(value.value, ast.Name)
                       and value.value.id in ("self", "cls"))
            if direct or viaself:
                out.setdefault(node.attr, []).append("%s:%d" % (rel_path, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, tree)


def scan_cli_symbols(root):
    """Return {symbol: [read sites]} for every CLI attribute the tests read."""
    out = {}
    for abs_path, rel_path in walk_py(root, "tests"):
        _scan_cli_file(abs_path, rel_path, out)
    return {k: {"sites": sorted(set(v))} for k, v in sorted(out.items())}


# --------------------------------------------------------------------------
# 3-6. executables, templates, catalogs, fixture families
# --------------------------------------------------------------------------

def scan_executables(root):
    """Every file under scripts/ carrying the owner-execute bit."""
    out = {}
    base = os.path.join(root, "scripts")
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                rel = os.path.relpath(path, root)
                out[rel] = {"mode": oct(os.stat(path).st_mode & 0o777)}
    return dict(sorted(out.items()))


def scan_templates(root):
    """Every templates/*.md with the content anchors it must keep satisfying."""
    out = {}
    base = os.path.join(root, "templates")
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        if not name.endswith(".md"):
            continue
        with open(os.path.join(base, name), encoding="utf-8") as fh:
            text = fh.read()
        anchors = TEMPLATE_ANCHORS.get(name, ())
        out[name] = {
            "anchors": list(anchors),
            "satisfied": [a for a in anchors if a in text],
            "bytes": len(text.encode("utf-8")),
        }
    return out


def scan_catalogs(root):
    """Catalog files claimed by BASENAME. Never pin a file count: the manifest
    generator (task 67) and the scaffold verb (task 73) both add catalog rows,
    so a count would redden on legitimate growth while a basename cannot."""
    base = os.path.join(root, "catalog")
    if not os.path.isdir(base):
        return {}
    return {name: {"bytes": os.path.getsize(os.path.join(base, name))}
            for name in sorted(os.listdir(base)) if name.endswith(".json")}


def scan_fixture_families(root):
    """Claim the preserved fixture families PRESENT in this tree.

    An absent family is omitted rather than recorded as `present: false`, so
    the merged row's `sides` list means what it says everywhere else in the
    fixture: which trees actually carry it. A row claiming both sides for a
    family only one tree has would park a donor-only backlog item under a
    "shared" label and defeat the ratchet's donor-only invariant.
    """
    out = {}
    for family, rel, kind in FIXTURE_FAMILIES:
        path = os.path.join(root, rel)
        if kind == "dir":
            if not os.path.isdir(path):
                continue
            members = sorted(os.listdir(path))
        else:
            if not os.path.isfile(path):
                continue
            members = []
        out[family] = {"path": rel, "kind": kind, "members": members}
    return out


def scan_tree(root, label):
    """Full per-tree scan. Node ids are collected separately (subprocess)."""
    root = os.path.realpath(root)
    return {
        "label": label,
        "root": relativise(root),
        "git_head": git_head(root),
        "cli_symbols": scan_cli_symbols(root),
        "executables": scan_executables(root),
        "templates": scan_templates(root),
        "catalogs": scan_catalogs(root),
        "fixture_families": scan_fixture_families(root),
    }
