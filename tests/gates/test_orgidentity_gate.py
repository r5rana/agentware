"""Proofs that the task-17 org-identity gate BITES.

A gate authored alongside the change it guards is worth exactly as much as the
evidence that it fails when the change is undone. So every check in
`tests/_orgidentity_gate.py` is broken here the cheapest way a future author
could break it by accident — and each break is asserted to redden that check BY
NAME, then to go green again when corrected.

Everything runs over SYNTHETIC trees written into a temp directory: a minimal
`scripts/agentware_lib` carrying only the org-identity surface. The real tree is
never modified, and the operator's config and knowledge base are never read.
"""

from tests._paths import TESTS_DIR

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _orgidentity_gate as gate  # noqa: E402
import _portability_gate  # noqa: E402


ORGKEYS_SRC = '''\
ORG_ROOT_KEY = "AGENTWARE_ORG_ROOT"
ORG_TEAM_KEY = "AGENTWARE_TEAM"
ORG_PROJECT_KEY = "AGENTWARE_ORG_PROJECT"
ORG_PROJECT_LEGACY_KEY = "AGENTWARE_ORG_PACKAGE"
_EXPORTS = [
    "ORG_ROOT_KEY",
    "ORG_TEAM_KEY",
    "ORG_PROJECT_KEY",
    "ORG_PROJECT_LEGACY_KEY",
]
'''

#: A faithful miniature of the real probe: the marker table, the whole-token
#: templates, the two-key resolution and the identity error.
CONFIG_SRC = '''\
import os
import re

from agentware_lib.core import orgkeys

PROJECT_NAME_FILE = ".agentware-project"
PROJECT_MARKERS = (
    ("pyproject.toml", r\'\'\'(?m)^\\s*name\\s*=\\s*["\\\']%s["\\\']\\s*$\'\'\'),
    ("Cargo.toml", r\'\'\'(?m)^\\s*name\\s*=\\s*["\\\']%s["\\\']\\s*$\'\'\'),
    ("setup.cfg", r"(?m)^\\s*name\\s*=\\s*%s\\s*$"),
    ("package.json", r\'(?m)^\\s*"name"\\s*:\\s*"(?:@[^"/]+/)?%s"\\s*,?\\s*$\'),
    ("composer.json", r\'(?m)^\\s*"name"\\s*:\\s*"(?:[^"/]+/)?%s"\\s*,?\\s*$\'),
    ("go.mod", r"(?m)^\\s*module\\s+(?:\\S+/)?%s\\s*$"),
    ("pom.xml", r"(?m)^\\s*<artifactId>\\s*%s\\s*</artifactId>"),
    ("settings.gradle", r\'\'\'(?m)^\\s*rootProject\\.name\\s*=\\s*["\\\']%s["\\\']\\s*$\'\'\'),
    ("settings.gradle.kts", r\'\'\'(?m)^\\s*rootProject\\.name\\s*=\\s*["\\\']%s["\\\']\\s*$\'\'\'),
    (PROJECT_NAME_FILE, r"(?m)^\\s*%s\\s*$"),
)
PROJECT_MARKER_READ_BYTES = 8192
_PROJECT_MARKER_RE_CACHE = {}


def _project_marker_re(marker, template, name):
    key = (marker, name)
    rx = _PROJECT_MARKER_RE_CACHE.get(key)
    if rx is None:
        rx = re.compile(template % re.escape(name))
        _PROJECT_MARKER_RE_CACHE[key] = rx
    return rx


def _read_marker(path, marker):
    try:
        with open(os.path.join(path, marker), "r", encoding="utf-8",
                  errors="replace") as f:
            return f.read(PROJECT_MARKER_READ_BYTES)
    except OSError:
        return None


def _dir_declares_project(path, name):
    if not path or not name:
        return False
    for marker, template in PROJECT_MARKERS:
        head = _read_marker(path, marker)
        if head is not None and _project_marker_re(marker, template,
                                                   name).search(head):
            return True
    return False


def _resolve_org_project():
    value = os.environ.get(orgkeys.ORG_PROJECT_KEY)
    if value and value.strip():
        return orgkeys.ORG_PROJECT_KEY, value.strip()
    value = os.environ.get(orgkeys.ORG_PROJECT_LEGACY_KEY)
    if value and value.strip():
        return orgkeys.ORG_PROJECT_LEGACY_KEY, value.strip()
    return None, None


def resolve_org_project():
    return _resolve_org_project()[1]


def org_project_identity_error(org_root):
    key, name = _resolve_org_project()
    if not name:
        return None
    if not org_root:
        return "%s=%s is set but no org root resolves" % (key, name)
    if not _dir_declares_project(org_root, name):
        return "org root %s is NOT the expected project %s" % (org_root, name)
    return None
'''

CONFIGCMD_SRC = '''\
from agentware_lib.core import orgkeys  # noqa: F401
'''


def _write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


def build_tree(root, orgkeys_src=ORGKEYS_SRC, config_src=CONFIG_SRC):
    """A minimal tree carrying the org-identity surface of the modular lib."""
    lib = os.path.join(root, "scripts", "agentware_lib")
    _write(os.path.join(lib, "__init__.py"), "")
    _write(os.path.join(lib, "core", "__init__.py"), "")
    _write(os.path.join(lib, "features", "__init__.py"), "")
    _write(os.path.join(lib, "core", "orgkeys.py"), orgkeys_src)
    _write(os.path.join(lib, "core", "config.py"), config_src)
    _write(os.path.join(lib, "features", "configcmd.py"), CONFIGCMD_SRC)
    return root


class _TreeCase(unittest.TestCase):
    """A synthetic tree per test, plus a needle file the gate can actually use.

    The `environment` needle class is private (R-LOC-03), so the neutrality
    check is driven here with an explicit synthetic needle file instead of the
    operator's real one — the test proves the WIRING, never the token list.
    """

    NEEDLES = {"schema": _portability_gate.NEEDLE_SCHEMA,
               "needles": [{"class": "environment", "token": "zzvendorbuild",
                            "match": "substring"}]}

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-orgident-test-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.tree = build_tree(os.path.join(self.root, "tree"))
        self.needles = os.path.join(self.root, "needles.json")
        with open(self.needles, "w", encoding="utf-8") as fh:
            json.dump(self.NEEDLES, fh)

    # -- helpers ---------------------------------------------------------
    def rel(self, *parts):
        return os.path.join(self.tree, *parts)

    def patch_config(self, old, new, count=1):
        path = self.rel("scripts", "agentware_lib", "core", "config.py")
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn(old, body)
        _write(path, body.replace(old, new, count))

    def patch_orgkeys(self, old, new):
        path = self.rel("scripts", "agentware_lib", "core", "orgkeys.py")
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn(old, body)
        _write(path, body.replace(old, new))

    def check(self):
        return gate.check(self.tree, self.needles)

    def named(self, problems, tag):
        return [p for p in problems if p.startswith("[%s]" % tag)]

    def assertClean(self):
        problems = self.check()
        self.assertEqual(problems, [], "expected a clean tree")

    def assertReddens(self, tag, needle=None):
        problems = self.check()
        hits = self.named(problems, tag)
        self.assertTrue(hits, "expected a [%s] problem, got %s" % (tag, problems))
        if needle:
            self.assertTrue(any(needle in h for h in hits),
                            "no [%s] problem mentions %r: %s"
                            % (tag, needle, hits))
        return hits


class BaselineTest(_TreeCase):
    def test_the_synthetic_tree_is_clean(self):
        """Everything below is a DELTA from this, so it has to start green."""
        self.assertClean()

    def test_the_real_staged_tree_passes(self):
        """The gate's actual subject, resolved the way the CLI resolves it."""
        tree, problems = gate.resolve_tree()
        self.assertEqual(problems, [])
        self.assertTrue(os.path.isdir(os.path.join(tree, gate.LIB_REL)))

    def test_no_tree_carrying_the_lib_is_a_failure_not_a_pass(self):
        empty = os.path.join(self.root, "empty")
        os.makedirs(empty)
        tree, problems = gate.resolve_tree(empty)
        # the explicit candidate has no lib, so resolution falls through to the
        # real trees; point every candidate at nothing to prove the failure path
        saved = gate.REPO, gate._stage_donor.resolve_dest
        gate.REPO = empty
        gate._stage_donor.resolve_dest = lambda: empty
        try:
            tree, problems = gate.resolve_tree(empty)
        finally:
            gate.REPO, gate._stage_donor.resolve_dest = saved
        self.assertIsNone(tree)
        self.assertTrue(problems)
        self.assertIn("no tree carries", problems[0])


class KeysCheckTest(_TreeCase):
    def test_a_missing_current_key_reddens(self):
        self.patch_orgkeys('ORG_PROJECT_KEY = "AGENTWARE_ORG_PROJECT"\n', "")
        self.assertReddens("keys", "does not define ORG_PROJECT_KEY")

    def test_a_renamed_key_value_reddens(self):
        self.patch_orgkeys('"AGENTWARE_ORG_PROJECT"', '"AGENTWARE_ORG_PROJ"')
        self.assertReddens("keys", "expected 'AGENTWARE_ORG_PROJECT'")

    def test_dropping_the_deprecated_spelling_reddens(self):
        self.patch_orgkeys(
            'ORG_PROJECT_LEGACY_KEY = "AGENTWARE_ORG_PACKAGE"\n', "")
        self.patch_orgkeys('    "ORG_PROJECT_LEGACY_KEY",\n', "")
        self.assertReddens("keys", "ORG_PROJECT_LEGACY_KEY")

    def test_a_key_missing_from_exports_reddens(self):
        self.patch_orgkeys('    "ORG_PROJECT_KEY",\n', "")
        self.assertReddens("keys", "_EXPORTS")

    def test_redefining_the_retired_constant_reddens(self):
        self.patch_orgkeys('ORG_PROJECT_KEY = "AGENTWARE_ORG_PROJECT"',
                           'ORG_PROJECT_KEY = "AGENTWARE_ORG_PROJECT"\n'
                           'ORG_PACKAGE_KEY = "AGENTWARE_ORG_PACKAGE"')
        self.assertReddens("keys", "retired constant")


class MarkerCheckTest(_TreeCase):
    def test_a_missing_table_reddens(self):
        self.patch_config("PROJECT_MARKERS = (", "PROJECT_MARKERS_UNUSED = (")
        self.assertReddens("markers", "no PROJECT_MARKERS table")

    def test_two_tables_redden_rather_than_one_winning_silently(self):
        self.patch_config("PROJECT_MARKER_READ_BYTES = 8192",
                          'PROJECT_MARKERS = (("pyproject.toml", "%s"),)\n'
                          "PROJECT_MARKER_READ_BYTES = 8192")
        self.assertReddens("markers", "assigned in 2 places")

    def test_a_shrunken_table_reddens(self):
        path = self.rel("scripts", "agentware_lib", "core", "config.py")
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        head, _, tail = body.partition("PROJECT_MARKERS = (")
        _table, _, rest = tail.partition("\n)\n")
        _write(path, head + 'PROJECT_MARKERS = (\n    ("pyproject.toml", '
               r'''r"""(?m)^\s*name\s*=\s*[\"']%s[\"']\s*$"""),''' + "\n)\n"
               + rest)
        self.assertReddens("markers", "below the floor")

    def test_a_vendor_marker_reddens(self):
        self.patch_config('    ("pom.xml"', '    ("VendorBuildConfig", r"%s\\b"),\n'
                                            '    ("pom.xml"')
        self.assertReddens("markers", "not a standard project manifest")

    def test_an_unanchored_template_reddens_the_exact_check(self):
        """The dangerous refactor: keep every marker, lose the anchoring."""
        self.patch_config(
            r'''("setup.cfg", r"(?m)^\s*name\s*=\s*%s\s*$")''',
            r'''("setup.cfg", r"(?m)^\s*name\s*=\s*%s")''')
        hits = self.assertReddens("exact", "setup.cfg")
        self.assertTrue(any("not a whole token" in h for h in hits), hits)

    def test_a_template_with_no_substitution_reddens(self):
        self.patch_config(r'''("setup.cfg", r"(?m)^\s*name\s*=\s*%s\s*$")''',
                          r'''("setup.cfg", r"(?m)^\s*name\s*=\s*Foo\s*$")''')
        self.assertReddens("markers", "substitutions")

    def test_a_template_that_ends_at_the_name_reddens(self):
        self.patch_config(r'''("go.mod", r"(?m)^\s*module\s+(?:\S+/)?%s\s*$")''',
                          r'''("go.mod", r"(?m)^\s*module\s+(?:\S+/)?%s")''')
        self.assertReddens("markers", "anchor the name on the right")

    def test_a_duplicated_marker_reddens(self):
        self.patch_config('    ("pom.xml"', '    ("go.mod", r"(?m)^\\s*module\\s+'
                                            '(?:\\S+/)?%s\\s*$"),\n    ("pom.xml"')
        self.assertReddens("markers", "appears twice")

    def test_dropping_the_name_file_escape_hatch_reddens(self):
        self.patch_config('PROJECT_NAME_FILE = ".agentware-project"',
                          'PROJECT_NAME_FILE_X = ".agentware-project"')
        self.assertReddens("markers", "escape hatch")

    def test_dropping_the_probe_function_reddens(self):
        self.patch_config("def _dir_declares_project(", "def _dir_declares_x(")
        problems = self.check()
        self.assertTrue(self.named(problems, "markers")
                        or self.named(problems, "probe"))


class ExactCheckTest(unittest.TestCase):
    """The pure half of check 3, driven on synthetic payloads."""

    def payload(self, **declared):
        base = dict.fromkeys(gate.PROBE_DECLARATIONS, False)
        base[gate.PROBE_NAME_MATCH] = True
        base.update(declared)
        return {"markers": [["pyproject.toml", "%s"]],
                "cases": [{"marker": "pyproject.toml", "declared": base}]}

    def test_a_clean_payload_is_silent(self):
        self.assertEqual(gate.check_exact(self.payload()), [])

    def test_a_prefix_declaration_matching_reddens(self):
        problems = gate.check_exact(self.payload(FooV2=True))
        self.assertTrue(any("not a whole token" in p for p in problems), problems)

    def test_the_name_failing_to_match_itself_reddens(self):
        problems = gate.check_exact(self.payload(Foo=False))
        self.assertTrue(any("does NOT match" in p for p in problems), problems)

    def test_a_marker_with_no_probe_body_is_reported_not_skipped(self):
        payload = self.payload()
        payload["markers"].append(["VendorBuildConfig", "%s"])
        problems = gate.check_exact(payload)
        self.assertTrue(any("no probe body" in p for p in problems), problems)


class LegacyCheckTest(unittest.TestCase):
    """The pure half of check 4: the compatibility guarantee itself."""

    def payload(self, **override):
        rows = []
        for case, (name, declared, undeclared) in sorted(
                gate.IDENTITY_EXPECTED.items()):
            row = {"case": case, "resolved": name, "declared_ok": declared,
                   "undeclared_ok": undeclared}
            row.update(override.get(case, {}))
            rows.append(row)
        return {"identity": rows}

    def test_a_clean_payload_is_silent(self):
        self.assertEqual(gate.check_legacy(self.payload()), [])

    def test_the_deprecated_key_going_unread_reddens_as_disarmed(self):
        problems = gate.check_legacy(
            self.payload(legacy={"resolved": None, "undeclared_ok": True}))
        self.assertTrue(any("disarmed" in p for p in problems), problems)

    def test_the_current_key_losing_to_the_deprecated_one_reddens(self):
        problems = gate.check_legacy(
            self.payload(both={"resolved": "SomethingElse"}))
        self.assertTrue(any("case 'both' resolved" in p for p in problems),
                        problems)

    def test_the_guard_firing_with_no_key_set_reddens(self):
        problems = gate.check_legacy(self.payload(none={"undeclared_ok": False}))
        self.assertTrue(any("over-eager" in p for p in problems), problems)

    def test_a_missing_case_reddens_rather_than_passing_vacuously(self):
        payload = self.payload()
        payload["identity"] = [r for r in payload["identity"]
                               if r["case"] != "legacy"]
        problems = gate.check_legacy(payload)
        self.assertTrue(any("was not probed" in p for p in problems), problems)


class SurfaceCheckTest(_TreeCase):
    """The env/config capture resolves a read site from the literal constant
    handed to the resolver. Rewriting the two direct reads as a loop keeps the
    code working and erases BOTH keys from the fixture that gates them — the
    regression this check exists for, reproduced here exactly."""

    def _use_resolver(self, body):
        path = self.rel("scripts", "agentware_lib", "core", "config.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        head, _, tail = src.partition("def _resolve_org_project():")
        _old, _, rest = tail.partition("\n\n\ndef ")
        _write(path, head + body + "\n\ndef " + rest)

    def test_a_resolver_the_capture_can_read_is_silent(self):
        self.assertEqual(gate.check_surface(self.tree), [])

    def test_a_looped_resolver_reddens_naming_both_keys(self):
        self._use_resolver(
            "def _resolve_org_project():\n"
            "    for key in (orgkeys.ORG_PROJECT_KEY,\n"
            "                orgkeys.ORG_PROJECT_LEGACY_KEY):\n"
            "        value = os.environ.get(key)\n"
            "        if value:\n"
            "            return key, value\n"
            "    return None, None\n")
        problems = gate.check_surface(self.tree)
        self.assertEqual(len(problems), 2, problems)
        for key in (gate.CURRENT_KEY, gate.LEGACY_KEY):
            self.assertTrue(any(key in p for p in problems), (key, problems))

    def test_dropping_only_the_deprecated_read_reddens_only_that_key(self):
        self._use_resolver(
            "def _resolve_org_project():\n"
            "    value = os.environ.get(orgkeys.ORG_PROJECT_KEY)\n"
            "    if value:\n"
            "        return orgkeys.ORG_PROJECT_KEY, value\n"
            "    return None, None\n")
        problems = gate.check_surface(self.tree)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(gate.LEGACY_KEY, problems[0])


class NeutralCheckTest(_TreeCase):
    def test_a_vendor_token_in_an_owned_path_reddens(self):
        self.patch_config("import os", "import os  # zzvendorbuild")
        self.assertReddens("neutral", "config.py")

    def test_a_vendor_token_outside_the_owned_paths_is_not_this_gates_business(self):
        _write(self.rel("docs", "GUIDE.md"), "zzvendorbuild\n")
        self.assertClean()

    def test_an_unavailable_needle_class_is_a_failure_not_a_pass(self):
        """Every candidate path must miss — including the operator's real one,
        which is why this repoints the search root rather than passing a bad
        path (an unreadable explicit path silently falls through to the rest)."""
        missing = os.path.join(self.root, "no-such-needles.json")
        saved_ws = _portability_gate.WORKSPACE
        saved_env = {k: os.environ.pop(k, None) for k in
                     (_portability_gate.NEEDLE_ENV, "AGENTWARE_KNOWLEDGE_DIR")}
        _portability_gate.WORKSPACE = os.path.join(self.root, "nowhere")
        try:
            problems = gate.check_neutral(self.tree, missing)
        finally:
            _portability_gate.WORKSPACE = saved_ws
            for key, value in saved_env.items():
                if value is not None:
                    os.environ[key] = value
        self.assertTrue(problems)
        self.assertIn("saw nothing", problems[0])


class CliTest(_TreeCase):
    def test_the_cli_exits_non_zero_and_names_the_tree(self):
        out = subprocess.run(
            [sys.executable, os.path.join(HERE, "_orgidentity_gate.py"),
             "--tree", self.tree, "--needles", self.needles, "--json"],
            capture_output=True, text=True, timeout=300)
        self.assertEqual(out.returncode, 0, out.stderr)
        payload = json.loads(out.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tree"], self.tree)

    def test_the_cli_reports_problems_for_a_broken_tree(self):
        self.patch_orgkeys('"AGENTWARE_ORG_PROJECT"', '"WRONG"')
        out = subprocess.run(
            [sys.executable, os.path.join(HERE, "_orgidentity_gate.py"),
             "--tree", self.tree, "--needles", self.needles, "--json"],
            capture_output=True, text=True, timeout=300)
        self.assertEqual(out.returncode, 1)
        payload = json.loads(out.stdout)
        self.assertFalse(payload["ok"])
        self.assertTrue(any(p.startswith("[keys]") for p in payload["problems"]))


class OwnedPathsTest(unittest.TestCase):
    def test_every_owned_path_is_a_real_file_in_the_staged_tree(self):
        """A neutrality check scoped to a path that no longer exists is a check
        that passes by looking at nothing."""
        tree, problems = gate.resolve_tree()
        self.assertEqual(problems, [])
        resolved = gate.owned_paths(tree)
        self.assertTrue(resolved, "no owned path resolved at all")
        for rel in resolved:
            self.assertTrue(os.path.isfile(os.path.join(tree, rel)),
                            "%s is not a file under %s" % (rel, tree))

    def test_every_stem_resolves_to_a_module_or_a_package(self):
        """The split-tolerance must not become a way to lose coverage: a stem
        matching neither shape means the gate silently stopped scanning it."""
        tree, problems = gate.resolve_tree()
        self.assertEqual(problems, [])
        self.assertEqual(
            gate.unresolved_stems(tree), [],
            "stem(s) resolve to nothing — the file moved and this gate is now "
            "scanning less than it claims")

    def test_a_missing_REQUIRED_stem_still_reddens(self):
        """Pending must narrow the check, never disable it."""
        empty = tempfile.mkdtemp(prefix="aw-orgid-nostems-")
        self.addCleanup(shutil.rmtree, empty, True)
        missing = gate.unresolved_stems(empty)
        required = [s.replace(os.sep, "/") for s in gate.ORG_IDENTITY_STEMS
                    if s not in gate.PENDING_STEMS]
        self.assertEqual(sorted(missing), sorted(required))

    def test_a_PENDING_stem_that_lands_must_be_promoted(self):
        """Task 58 lands `tests/unit/**`; the day it does, the exemption is a
        lie and the gate says so rather than quietly excusing a path it can now
        check. Shrink-only, in the direction the migration actually moves."""
        root = tempfile.mkdtemp(prefix="aw-orgid-pending-")
        self.addCleanup(shutil.rmtree, root, True)
        landed = sorted(gate.PENDING_STEMS)[0]
        path = os.path.join(root, landed + ".py")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# landed by task 58\n")
        problems = gate.unresolved_stems(root)
        self.assertTrue(any("pending stem now resolves" in p for p in problems),
                        problems)
        self.assertTrue(any(landed.replace(os.sep, "/") in p for p in problems),
                        problems)

    def test_every_pending_stem_is_also_a_declared_stem(self):
        """Pending can only ever exempt something the table already claims."""
        self.assertTrue(gate.PENDING_STEMS)
        self.assertLessEqual(set(gate.PENDING_STEMS),
                             set(gate.ORG_IDENTITY_STEMS))
        for stem in gate.PENDING_STEMS:
            self.assertTrue(stem.startswith("tests" + os.sep),
                            "only the donor TEST corpus may be pended: %s" % stem)

    def test_a_split_stem_expands_to_every_file_in_the_package(self):
        """Task 21 turned `core/config.py` into a package; the neutrality scan
        must cover ALL of it, not just whichever file kept the old name."""
        tree, _problems = gate.resolve_tree()
        resolved = set(gate.owned_paths(tree))
        cfg = {p for p in resolved
               if p.startswith("scripts/agentware_lib/core/config/")}
        self.assertGreater(
            len(cfg), 1,
            "core/config resolved to %r — a split package must expand to every "
            "file it contains" % (sorted(cfg),))
        self.assertNotIn("scripts/agentware_lib/core/config.py", resolved)

    def test_the_marker_floor_and_allowlist_are_pinned(self):
        """Both are policy: widening either is an edit somebody has to defend."""
        self.assertEqual(gate.MIN_MARKERS, 8)
        self.assertIn(".agentware-project", gate.ALLOWED_MARKERS)
        self.assertNotIn("Config", gate.ALLOWED_MARKERS)


if __name__ == "__main__":
    unittest.main()
