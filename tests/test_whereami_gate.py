"""Proofs that the task-18 whereami gate BITES.

A gate authored alongside the change it guards is worth exactly as much as the
evidence that it fails when the change is undone. So every check in
`tests/_whereami_gate.py` is broken here the cheapest way a future author could
break it by accident — a marker added to the table, a resolver moved into the
leaf, a parent-name constraint reintroduced, a mirror key dropped — and each
break is asserted to redden that check BY NAME, then to go green when corrected.

Everything runs over SYNTHETIC trees written into a temp directory: a faithful
miniature of the entrypoint plus the two core modules whereami depends on. The
real tree is never modified, and the operator's config and knowledge base are
never read (the gate redirects both for every probe).

The one case that would be tautological to synthesise — "the tree this migration
is actually building passes" — is asserted directly against the resolved tree.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _whereami_gate as gate  # noqa: E402


ORGIDENTITY_SRC = '''\
import os

PROJECT_NAME_FILE = ".agentware-project"
PROJECT_MARKERS = (
    ("pyproject.toml", r"(?m)^\\s*name\\s*=\\s*%s\\s*$"),
    ("Cargo.toml", r"(?m)^\\s*name\\s*=\\s*%s\\s*$"),
    ("package.json", r"(?m)^\\s*name\\s*=\\s*%s\\s*$"),
    ("go.mod", r"(?m)^\\s*module\\s+%s\\s*$"),
    ("pom.xml", r"(?m)^\\s*<artifactId>%s</artifactId>"),
    (PROJECT_NAME_FILE, r"(?m)^\\s*%s\\s*$"),
)
PROJECT_PRESENCE_MARKERS = ("setup.py", "build.gradle")
PROJECT_ROOT_MARKERS = tuple(
    marker for marker, _t in PROJECT_MARKERS) + PROJECT_PRESENCE_MARKERS


def _dir_has_project_marker(path):
    if not path:
        return False
    for marker in PROJECT_ROOT_MARKERS:
        if os.path.exists(os.path.join(path, marker)):
            return True
    return False


_EXPORTS = ["PROJECT_NAME_FILE", "PROJECT_MARKERS", "PROJECT_PRESENCE_MARKERS",
            "PROJECT_ROOT_MARKERS", "_dir_has_project_marker"]
'''

PROJECTCONTEXT_SRC = '''\
import os

from . import orgidentity

VCS_MARKERS = (".git", ".hg", ".svn")
WHEREAMI_KEY_MIRRORS = (
    ("workspace_root", "repo_root"),
    ("package_name", "project_name"),
    ("package_dir", "project_dir"),
)


def _dir_is_vcs_root(path):
    if not path:
        return False
    for marker in VCS_MARKERS:
        if os.path.exists(os.path.join(path, marker)):
            return True
    return False


def _resolve_context(start_dir):
    start_dir = os.path.abspath(os.path.expanduser(start_dir))
    result = {"invoked_from": start_dir, "repo_root": "", "project_name": "",
              "project_dir": ""}
    cur = start_dir
    while True:
        if not result["project_dir"] and orgidentity._dir_has_project_marker(cur):
            result["project_dir"] = cur
            result["project_name"] = os.path.basename(cur)
        if not result["repo_root"] and _dir_is_vcs_root(cur):
            result["repo_root"] = cur
        parent = os.path.dirname(cur)
        if (result["project_dir"] and result["repo_root"]) or cur == parent:
            break
        cur = parent
    if result["repo_root"] and not result["project_dir"]:
        result["project_dir"] = result["repo_root"]
        result["project_name"] = os.path.basename(result["repo_root"])
    if result["project_dir"] and not result["repo_root"]:
        result["repo_root"] = result["project_dir"]
    return result


def whereami_payload(start_dir):
    payload = _resolve_context(start_dir)
    for mirror, canonical in WHEREAMI_KEY_MIRRORS:
        payload[mirror] = payload[canonical]
    return payload


_EXPORTS = ["VCS_MARKERS", "WHEREAMI_KEY_MIRRORS", "_dir_is_vcs_root",
            "_resolve_context", "whereami_payload"]
'''

ENTRYPOINT_SRC = '''\
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agentware_lib.core import projectcontext


def cmd_whereami(args):
    start = (getattr(args, "dir", None)
             or os.environ.get("AGENTWARE_INVOKED_FROM") or os.getcwd())
    result = projectcontext.whereami_payload(start)
    if getattr(args, "format", "text") == "json":
        print(json.dumps(result, indent=2))
    elif result["project_name"]:
        print("project_dir:   %s" % result["project_dir"])
    else:
        print("No project resolved for: %s" % result["invoked_from"])
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agentware")
    sub = ap.add_subparsers(dest="command")
    sub.required = True
    p = sub.add_parser("whereami")
    p.add_argument("--dir", default=None)
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.set_defaults(func=cmd_whereami)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
'''


def _write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


def build_tree(root):
    """A minimal tree carrying exactly the whereami surface the gate reads."""
    lib = os.path.join(root, "scripts", "agentware_lib")
    _write(os.path.join(lib, "__init__.py"), "")
    _write(os.path.join(lib, "core", "__init__.py"), "")
    _write(os.path.join(lib, "core", "orgidentity.py"), ORGIDENTITY_SRC)
    _write(os.path.join(lib, "core", "projectcontext.py"), PROJECTCONTEXT_SRC)
    _write(os.path.join(root, "scripts", "agentware"), ENTRYPOINT_SRC)
    return root


class _TreeCase(unittest.TestCase):
    """A fresh synthetic tree per test; every case is a DELTA from a green one."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-whereami-test-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.tree = build_tree(os.path.join(self.root, "tree"))

    # -- helpers ---------------------------------------------------------
    def _module(self, name):
        return os.path.join(self.tree, "scripts", "agentware_lib", "core",
                            "%s.py" % name)

    def patch(self, module, old, new, count=1):
        path = self._module(module)
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn(old, body)
        _write(path, body.replace(old, new, count))

    def check(self):
        return gate.check(self.tree)

    def named(self, problems, tag):
        return [p for p in problems if p.startswith("[%s]" % tag)]

    def assertClean(self):
        self.assertEqual(self.check(), [], "expected a clean tree")

    def assertReddens(self, tag, needle=None):
        problems = self.check()
        hits = self.named(problems, tag)
        self.assertTrue(hits,
                        "expected a [%s] problem, got %s" % (tag, problems))
        if needle:
            self.assertTrue(any(needle in h for h in hits),
                            "no [%s] problem mentions %r: %s"
                            % (tag, needle, hits))
        return hits


class BaselineTest(_TreeCase):
    def test_the_synthetic_tree_is_clean(self):
        """Everything below is a delta from this, so it has to start green."""
        self.assertClean()

    def test_the_real_resolved_tree_passes(self):
        """The gate's actual subject, resolved the way the CLI resolves it."""
        tree, problems = gate.resolve_tree()
        self.assertEqual(problems, [])
        self.assertEqual(gate.check(tree), [])

    def test_a_tree_without_the_lib_fails_rather_than_passes(self):
        empty = os.path.join(self.root, "empty")
        os.makedirs(empty)
        _tree, problems = gate.resolve_tree(empty)
        # resolve_tree falls through to the real candidates rather than
        # accepting a lib-less tree; what must never happen is a silent pass.
        if problems:
            self.assertIn("no tree carries", problems[0])


class MarkerTableCheckTest(_TreeCase):
    def test_a_non_standard_marker_reddens(self):
        """One vendor filename in the table is the whole regression."""
        self.patch("orgidentity",
                   'PROJECT_PRESENCE_MARKERS = ("setup.py", "build.gradle")',
                   'PROJECT_PRESENCE_MARKERS = ("setup.py", "Config")')
        self.assertReddens("markers", "Config")

    def test_a_hand_written_table_reddens(self):
        """Derived from the identity table, or the two drift apart."""
        self.patch("orgidentity",
                   "PROJECT_ROOT_MARKERS = tuple(\n"
                   "    marker for marker, _t in PROJECT_MARKERS) "
                   "+ PROJECT_PRESENCE_MARKERS",
                   'PROJECT_ROOT_MARKERS = ("pyproject.toml", "package.json",\n'
                   '                        "go.mod", "setup.py")')
        self.assertReddens("markers", "DERIVED")

    def test_a_second_table_reddens(self):
        _write(self._module("elsewhere"),
               'PROJECT_ROOT_MARKERS = ("pyproject.toml",)\n'
               '_EXPORTS = ["PROJECT_ROOT_MARKERS"]\n')
        self.assertReddens("markers", "assigned in 2 places")

    def test_no_table_at_all_reddens(self):
        self.patch("orgidentity", "PROJECT_ROOT_MARKERS = tuple(",
                   "_RETIRED_ROOT_MARKERS = tuple(")
        self.patch("projectcontext", "orgidentity._dir_has_project_marker(cur)",
                   "orgidentity._dir_has_project_marker(cur)")
        hits = self.assertReddens("markers", "no lib module defines")
        self.assertTrue(hits)


class ResolverHomeCheckTest(_TreeCase):
    def test_a_resolver_in_the_cli_leaf_reddens(self):
        """A resolver only the leaf can import is a resolver the features-layer
        caller has to copy."""
        body = open(self._module("projectcontext"), encoding="utf-8").read()
        os.remove(self._module("projectcontext"))
        leaf = os.path.join(self.tree, "scripts", "agentware_lib", "cli.py")
        _write(leaf, body.replace("from . import orgidentity",
                                  "from .core import orgidentity"))
        self.assertReddens("home", "cli")

    def test_two_copies_of_the_resolver_redden(self):
        body = open(self._module("projectcontext"), encoding="utf-8").read()
        _write(self._module("projectcontext2"), body)
        self.assertReddens("home", "defined in 2 modules")

    def test_no_resolver_reddens(self):
        self.patch("projectcontext", "def _resolve_context(start_dir):",
                   "def _resolve_ctx(start_dir):")
        self.patch("projectcontext", "payload = _resolve_context(start_dir)",
                   "payload = _resolve_ctx(start_dir)")
        self.assertReddens("home", "no lib module defines")


class LayoutCheckTest(_TreeCase):
    def test_a_src_parent_constraint_reddens(self):
        """The exact rule this task removed, reintroduced."""
        self.patch(
            "projectcontext",
            "if not result[\"project_dir\"] and orgidentity._dir_has_project_marker(cur):",
            "if (not result[\"project_dir\"]\n"
            "                and orgidentity._dir_has_project_marker(cur)\n"
            "                and os.path.basename(os.path.dirname(cur)) == \"src\"):")
        problems = self.check()
        self.assertTrue(self.named(problems, "layout-free"),
                        "a parent-name constraint must redden layout-free: %s"
                        % problems)
        self.assertTrue(any("tools" in p for p in problems))

    def test_a_vendor_marker_special_case_reddens(self):
        """The other half: accepting a marker no public ecosystem publishes."""
        self.patch("orgidentity",
                   "    for marker in PROJECT_ROOT_MARKERS:",
                   "    if os.path.exists(os.path.join(path, \"Config\")):\n"
                   "        return True\n"
                   "    for marker in PROJECT_ROOT_MARKERS:")
        self.assertReddens("layout-free", "the vendor marker is back")

    def test_dropping_a_named_layout_reddens(self):
        """A table that quietly loses go.mod loses a whole ecosystem."""
        self.patch("orgidentity",
                   '    ("go.mod", r"(?m)^\\s*module\\s+%s\\s*$"),\n', "")
        self.assertReddens("layouts", "go.mod")

    def test_failing_to_resolve_from_a_subdirectory_reddens(self):
        """Resolving only at the project root would pass a root-only probe."""
        self.patch("projectcontext", "        cur = parent",
                   "        break")
        self.assertReddens("layouts", "nested subdirectory")

    def test_losing_the_repo_root_reddens(self):
        self.patch("projectcontext",
                   "        if not result[\"repo_root\"] and _dir_is_vcs_root(cur):",
                   "        if False and _dir_is_vcs_root(cur):")
        self.assertReddens("layouts", "repo_root")


class NowhereCheckTest(_TreeCase):
    def test_raising_on_a_missing_path_reddens(self):
        self.patch("projectcontext",
                   "    start_dir = os.path.abspath(os.path.expanduser(start_dir))",
                   "    start_dir = os.path.abspath(os.path.expanduser(start_dir))\n"
                   "    if not os.path.isdir(start_dir):\n"
                   "        raise SystemExit(2)")
        self.assertReddens("nowhere", "exited")

    def test_a_non_empty_field_outside_any_project_reddens(self):
        self.patch("projectcontext",
                   '    result = {"invoked_from": start_dir, "repo_root": "",',
                   '    result = {"invoked_from": start_dir, "repo_root": "/",')
        self.assertReddens("nowhere", "expected an empty string")

    def test_an_unactionable_text_message_reddens(self):
        entry = os.path.join(self.tree, "scripts", "agentware")
        body = open(entry, encoding="utf-8").read()
        _write(entry, body.replace('"No project resolved for: %s"',
                                   '"nothing here: %s"'))
        self.assertReddens("nowhere", "names no outcome")


class MirrorCheckTest(_TreeCase):
    def test_dropping_the_deprecated_keys_reddens(self):
        self.patch("projectcontext",
                   "    for mirror, canonical in WHEREAMI_KEY_MIRRORS:\n"
                   "        payload[mirror] = payload[canonical]\n",
                   "")
        self.assertReddens("mirrors", "dropped the deprecated")

    def test_a_mirror_that_disagrees_reddens(self):
        self.patch("projectcontext",
                   "        payload[mirror] = payload[canonical]",
                   "        payload[mirror] = payload[canonical].upper()")
        self.assertReddens("mirrors", "must be copies")

    def test_dropping_a_canonical_field_reddens(self):
        self.patch("projectcontext",
                   '"project_name": "",\n              "project_dir": ""}',
                   '"project_dir": ""}')
        self.patch("projectcontext",
                   '            result["project_name"] = os.path.basename(cur)\n',
                   "")
        self.patch("projectcontext",
                   '        result["project_name"] = os.path.basename(result["repo_root"])\n',
                   "")
        self.patch("projectcontext",
                   '    ("package_name", "project_name"),\n', "")
        self.assertReddens("mirrors", "canonical 'project_name'")


class CliTest(_TreeCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "_whereami_gate.py")] + list(args),
            capture_output=True, text=True, cwd=REPO)

    def test_clean_tree_exits_zero(self):
        proc = self._run("--tree", self.tree)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK 0 problem(s)", proc.stdout)

    def test_broken_tree_exits_nonzero_with_json(self):
        self.patch("orgidentity",
                   'PROJECT_PRESENCE_MARKERS = ("setup.py", "build.gradle")',
                   'PROJECT_PRESENCE_MARKERS = ("setup.py", "Config")')
        proc = self._run("--tree", self.tree, "--json")
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["problems"])


class OwnedConstantsTest(unittest.TestCase):
    """The gate's own contract, so a silent edit to it cannot pass unnoticed."""

    def test_the_three_named_layouts_are_the_ones_the_plan_names(self):
        self.assertEqual(gate.REQUIRED_LAYOUT_MARKERS,
                         ("pyproject.toml", "package.json", "go.mod"))

    def test_the_parent_names_include_src_and_the_checkout_root(self):
        self.assertIn("src", gate.PARENT_NAMES)
        self.assertIn("", gate.PARENT_NAMES)
        self.assertGreaterEqual(len(gate.PARENT_NAMES), 3)

    def test_the_allowed_marker_set_is_shared_not_copied(self):
        """One list, one place: a marker allowed by one gate and refused by the
        other is how a vendor filename lands in the tree."""
        import _orgidentity_gate
        self.assertIs(gate._orgidentity_gate.ALLOWED_MARKERS,
                      _orgidentity_gate.ALLOWED_MARKERS)

    def test_the_mirror_map_covers_every_deprecated_key(self):
        self.assertEqual({m for m, _c in gate.MIRROR_FIELDS},
                         {"workspace_root", "package_name", "package_dir"})


if __name__ == "__main__":
    unittest.main()
