#!/usr/bin/env python3
"""Static ENV-VAR / CONFIG-KEY scanner for one tree (Python + shell).

Feature 260813-modular-core-adoption, task 8 -- the SCANNING half.
``tests/_env_config_capture.py`` imports this and does the two-tree merge, the
fixture derivation and the gates. Split so neither file approaches the 600-line
``tests/**` ceiling this plan pins.

What counts as a key (the inclusion rule, pinned)
-------------------------------------------------
1. Any name in the package's own ``AGENTWARE_*`` namespace read or written
   through an environment API, in either language. Always in scope -- it is the
   package's own contract.
2. Any OTHER name that can only be satisfied by the environment:
   * Python: accessed through ``os.environ`` / ``os.getenv`` (or a copy of the
     environment -- ``dict(os.environ)``, ``os.environ.copy()``, the ``env``
     parameter the review resolver threads through).
   * Shell: read in a file of the shell surface AND never assigned anywhere in
     that tree's shell surface (an assigned name is a within-process global --
     the donor's ``DOCS_DIR``/``KDIR`` are sourced-lib globals, not environment
     inputs), UPPERCASE (the env-var convention), and not a bash special.

A MENTION is not a read. ``AGENTWARE_STATUS:`` is a printed line prefix and
``AGENTWARE_STAGE=loop-pre`` appears inside a transcript regex; neither is an
environment read, and a name-grep cannot tell the difference. Detection is
therefore SYNTACTIC: Python via ``ast`` (call / subscript / membership context
with an environment receiver), shell via parameter-expansion forms with
comments stripped.

Two shapes a literal scanner would otherwise miss, both handled here:
* **Registry declarations.** ``for v in GRAPH_KILL_SWITCH_VARS: env.get(v)`` --
  the literal never appears at a ``.get()`` site, so the four merge kill
  switches would be invisible. Module-level tuples/lists of string constants
  containing at least one ``AGENTWARE_*`` name are recorded as
  ``registry:<NAME>`` sites.
* **Dynamically constructed families.** ``_phase_key()`` builds
  ``"AGENTWARE_%s_%s" % (phase.upper(), axis)``, so nine real keys
  (``AGENTWARE_PRE_CLI`` ... ``AGENTWARE_POST_LOCAL``) exist as a literal
  NOWHERE in either tree. Templates are detected and expanded through a closed
  table; an unadjudicated template HALTS the capture rather than dropping a
  whole family silently.
"""

from __future__ import annotations

import ast
import os
import re

NAMESPACE = "AGENTWARE_"

# Python scan skips these top-level dirs: tests are the harness, not the tree,
# and webui is a JS bundle whose vendored chunks are not this package's surface.
PY_SKIP_TOP = ("tests", "webui")
PY_SKIP_DIRS = (".git", "__pycache__", "node_modules", "dist", "venv", ".venv")

# The shell surface: the orchestrator, the bash knowledge-dir resolver, the
# loop libs (donor-only today) and every hook. These are the files task 8 names.
SHELL_FILES = ("agentware.sh", "scripts/aw-knowledge-dir")
SHELL_DIRS = ("scripts/hooks", "scripts/loop")

# Bash specials and shell built-ins: read from the shell, never from the
# environment contract this fixture is about.
SHELL_SPECIALS = frozenset("""
BASH BASH_SOURCE BASH_VERSION BASH_REMATCH BASHPID EUID FUNCNAME GROUPS
HOSTTYPE IFS LINENO MACHTYPE OLDPWD OPTARG OPTIND OSTYPE PIPESTATUS PPID PS1
PS2 PS3 PS4 PWD RANDOM REPLY SECONDS SHELL SHLVL UID
""".split())

# Python call names that read a config.env-backed key by name.
CONFIG_READERS = ("_read_config_key", "_resolve_key_env_then_config",
                  "resolve_key_env_then_config")

# Receiver expressions that ARE the process environment (or a copy of it).
ENV_RECEIVER_NAMES = ("environ", "env", "_env", "environment", "os_environ")

# Dynamically CONSTRUCTED key families, with the evidence pinning each domain.
DYNAMIC_EXPANSIONS = {
    "AGENTWARE_%s_%s": {
        "domains": [["PRE", "MAIN", "POST"], ["CLI", "MODEL", "LOCAL"]],
        "evidence": ("config.PHASES = ('pre','main','post') x the axis literals "
                     "at the three _phase_key(phase, ...) call sites "
                     "(CLI/MODEL/LOCAL)"),
        "config_key": True,
    },
}
# A template must have the SHAPE of a key: the namespace, then only key
# characters and `%s` placeholders. Without the anchor, prose like
# "AGENTWARE_REVIEW_EFFORT=%r is BELOW ..." would be mistaken for one.
DYNAMIC_TEMPLATE_RE = re.compile(r"^AGENTWARE_(?:[A-Z0-9_]|%s)+$")

# --- shell parameter-expansion forms -----------------------------------------
# Shell names are matched UPPERCASE-only: that is the environment-variable
# convention, and a lowercase shell name (`$i`, `$line`, `$feature`) is a
# function-local, not an environment input.
SH_BRACE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(:?[-=+?])?([^}]*)\}")
SH_BARE = re.compile(r"\$([A-Z][A-Z0-9_]*)")
SH_ASSIGN = re.compile(r"(?:^|;|\s|&&|\|\|)\s*(?:export\s+|local\s+|declare\s+-\w+\s+)?"
                       r"([A-Za-z_][A-Za-z0-9_]*)=")
SH_BIND = re.compile(r"\b(?:for|read(?:\s+-r)?)\s+([A-Za-z_][A-Za-z0-9_]*)")
SH_EXPORT = re.compile(r"\bexport\s+([A-Za-z_][A-Za-z0-9_]*)")


# --- Python scanning ---------------------------------------------------------

def python_files(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PY_SKIP_DIRS]
        rel = os.path.relpath(dirpath, root)
        if rel.split(os.sep)[0] in PY_SKIP_TOP:
            continue
        for name in filenames:
            if name.endswith(".py") or name == "agentware":
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def _is_env_receiver(node):
    """True iff this expression is the process environment or a copy of it."""
    if isinstance(node, ast.Attribute):
        return node.attr == "environ"
    if isinstance(node, ast.Name):
        return node.id in ENV_RECEIVER_NAMES
    if isinstance(node, ast.Call):          # dict(os.environ) / os.environ.copy()
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "copy":
            return _is_env_receiver(func.value)
        if isinstance(func, ast.Name) and func.id == "dict" and node.args:
            return _is_env_receiver(node.args[0])
    return False


class _PyScanner(ast.NodeVisitor):
    """Records env-key hits by SYNTACTIC CONTEXT, never by name mention."""

    def __init__(self, rel, consts):
        self.rel = rel
        self.consts = consts
        self.hits = []
        self.templates = []

    def _key(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        if name:
            vals = self.consts.get(name)
            if vals and len(vals) == 1:
                return next(iter(vals))
        return None

    def _add(self, key, node, form, default, kind):
        if key:
            self.hits.append((key, {"file": self.rel,
                                    "line": getattr(node, "lineno", 0),
                                    "lang": "python", "form": form,
                                    "default": default, "kind": kind}))

    def _in_scope(self, receiver, key):
        """A mapping access counts only if the RECEIVER is an environment
        mapping, or the key is in the package's own namespace (an
        ``AGENTWARE_*`` key looked up in any mapping is an env-dict copy).
        Without this, every ``payload.get("outcome")`` in the tree would land
        in the fixture as a phantom key."""
        if key and key.startswith(NAMESPACE):
            return True
        return _is_env_receiver(receiver)

    def _default_arg(self, node):
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            return node.args[1].value
        return None

    def visit_Call(self, node):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("get", "setdefault",
                                                             "pop") and node.args:
            key = self._key(node.args[0])
            if self._in_scope(func.value, key):
                kind = "read" if func.attr == "get" else "write"
                self._add(key, node, "%s()" % func.attr,
                          self._default_arg(node), kind)
        else:
            name = func.id if isinstance(func, ast.Name) else \
                func.attr if isinstance(func, ast.Attribute) else None
            if name == "getenv" and node.args:
                self._add(self._key(node.args[0]), node, "getenv()",
                          self._default_arg(node), "read")
            elif name in CONFIG_READERS and node.args:
                self._add(self._key(node.args[0]), node, "%s()" % name, None, "read")
        self.generic_visit(node)

    def visit_Subscript(self, node):
        key = self._key(node.slice)
        if self._in_scope(node.value, key):
            kind = "write" if isinstance(node.ctx, (ast.Store, ast.Del)) else "read"
            self._add(key, node, "environ[]", None, kind)
        self.generic_visit(node)

    def visit_BinOp(self, node):
        """`"AGENTWARE_%s_%s" % (...)` -- a dynamically constructed key family."""
        if isinstance(node.op, (ast.Mod, ast.Add)) and \
                isinstance(node.left, ast.Constant) and \
                isinstance(node.left.value, str) and \
                "%s" in node.left.value and \
                DYNAMIC_TEMPLATE_RE.match(node.left.value):
            self.templates.append({"file": self.rel, "line": node.lineno,
                                   "template": node.left.value})
        self.generic_visit(node)

    def visit_Compare(self, node):
        if any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            key = self._key(node.left)
            for comparator in node.comparators:
                if self._in_scope(comparator, key):
                    self._add(key, node, "in-environ", None, "read")
                    break
        self.generic_visit(node)


def _module_tables(tree, rel):
    """(string constants, registry tuples) declared at module level."""
    consts, registries = {}, []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for name in targets:
                consts.setdefault(name, set()).add(value.value)
        elif isinstance(value, (ast.Tuple, ast.List)) and targets:
            members = [e.value for e in value.elts
                       if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if any(m.startswith(NAMESPACE) for m in members):
                registries.append((rel, node.lineno, targets[0], members))
    return consts, registries


def scan_python(root):
    """({key: [site, ...]}, [template, ...]) for the tree's Python surface."""
    trees, consts, registries = {}, {}, []
    for path in python_files(root):
        with open(path, encoding="utf-8", errors="replace") as handle:
            source = handle.read()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        trees[path] = tree
        file_consts, file_registries = _module_tables(tree,
                                                      os.path.relpath(path, root))
        for name, values in file_consts.items():
            consts.setdefault(name, set()).update(values)
        registries.extend(file_registries)
    hits, templates = {}, []
    for path, tree in trees.items():
        scanner = _PyScanner(os.path.relpath(path, root), consts)
        scanner.visit(tree)
        for key, site in scanner.hits:
            hits.setdefault(key, []).append(site)
        templates.extend(scanner.templates)
    for rel, line, const_name, members in registries:
        for member in members:
            hits.setdefault(member, []).append(
                {"file": rel, "line": line, "lang": "python",
                 "form": "registry:%s" % const_name, "default": None, "kind": "read"})
    return hits, sorted(templates, key=lambda t: (t["template"], t["file"], t["line"]))


def expand_templates(templates):
    """Turn detected key TEMPLATES into the real keys they construct.

    Fails closed: an unknown template halts the capture rather than dropping a
    whole family silently (the same discipline the union fixture's adjudication
    table uses)."""
    expanded = {}
    for entry in templates:
        spec = DYNAMIC_EXPANSIONS.get(entry["template"])
        if spec is None:
            raise SystemExit(
                "unadjudicated dynamic key template %r at %s:%d -- add it to "
                "DYNAMIC_EXPANSIONS with its domain and the evidence pinning "
                "that domain, or the whole key family is invisible."
                % (entry["template"], entry["file"], entry["line"]))
        combos = [[]]
        for domain in spec["domains"]:
            combos = [combo + [value] for combo in combos for value in domain]
        for combo in combos:
            key = entry["template"] % tuple(combo)
            expanded.setdefault(key, []).append(
                {"file": entry["file"], "line": entry["line"], "lang": "python",
                 "form": "template:%s" % entry["template"], "default": None,
                 "kind": "read"})
    return expanded


# --- shell scanning ----------------------------------------------------------

def shell_files(root):
    out = []
    for rel in SHELL_FILES:
        path = os.path.join(root, rel)
        if os.path.isfile(path):
            out.append(path)
    for sub in SHELL_DIRS:
        directory = os.path.join(root, sub)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            path = os.path.join(directory, name)
            if os.path.isfile(path) and (name.endswith(".sh") or name == "commit-msg"):
                out.append(path)
    return out


def _strip_comment(line):
    """Drop a trailing comment. Conservative: only outside quotes."""
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote and line[i - 1:i] != "\\":
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out)


def _scan_shell_line(rel, lineno, code, hits):
    seen = set()
    for match in SH_BRACE.finditer(code):
        name, op, body = match.group(1), match.group(2), match.group(3)
        default = body if op and op.endswith(("-", "=")) else None
        hits.setdefault(name, []).append(
            {"file": rel, "line": lineno, "lang": "shell",
             "form": "${%s%s}" % (name, op or ""), "default": default,
             "kind": "read"})
        seen.add(name)
    for match in SH_BARE.finditer(code):
        if match.group(1) in seen:
            continue
        hits.setdefault(match.group(1), []).append(
            {"file": rel, "line": lineno, "lang": "shell",
             "form": "$%s" % match.group(1), "default": None, "kind": "read"})
    for match in SH_EXPORT.finditer(code):
        hits.setdefault(match.group(1), []).append(
            {"file": rel, "line": lineno, "lang": "shell",
             "form": "export", "default": None, "kind": "write"})


def scan_shell(root):
    """({key: [site, ...]}, assigned_names, exported_names)."""
    hits, assigned, exported = {}, set(), set()
    for path in shell_files(root):
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        rel = os.path.relpath(path, root)
        assigned |= set(SH_ASSIGN.findall(text)) | set(SH_BIND.findall(text))
        exported |= set(SH_EXPORT.findall(text))
        for lineno, raw in enumerate(text.splitlines(), 1):
            if raw.lstrip().startswith("#"):
                continue
            _scan_shell_line(rel, lineno, _strip_comment(raw), hits)
    return hits, assigned, exported


# --- per-tree capture --------------------------------------------------------

def capture_tree(root):
    """Every in-scope key of ONE tree: {key: [site, ...]}."""
    py_hits, templates = scan_python(root)
    sh_hits, sh_assigned, _ = scan_shell(root)
    out = {}
    for key, sites in expand_templates(templates).items():
        out.setdefault(key, []).extend(sites)
    # The Python scanner already enforced an environment receiver (or the
    # package namespace), so every hit it returns is in scope by construction.
    for key, sites in py_hits.items():
        out.setdefault(key, []).extend(sites)
    for key, sites in sh_hits.items():
        if key in SHELL_SPECIALS:
            continue
        if key.startswith(NAMESPACE) or key not in sh_assigned:
            out.setdefault(key, []).extend(sites)
    for sites in out.values():
        sites.sort(key=lambda s: (s["file"], s["line"], s["form"]))
    return out
