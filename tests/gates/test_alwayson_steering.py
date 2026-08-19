"""Task-13 suite: the always-on steering set, its ratchet, and the scoped scan.

The acceptance criterion is "the gate token scan over the staged steering files
returns 0 hits", and a 0 is worthless unless two other things are proven with
it, so this suite is organised around them.

**The SET is complete.** Scoped by eye, "the steering files" is three markdown
documents; scoped by what a session actually loads it is thirteen, and the one
file carrying the staged tree's leak (the SessionStart injector) is not among
the three. So the resolver is tested for the ways the set can be silently
wrong -- an @-import nobody added to a list, a persona directory that went
empty, a declared path that vanished -- because each of those makes a clean
scan a smaller claim than it looks.

**The scan DETECTS.** Every class gets its own planted probe on a throwaway
tree, and each probe is checked to name the offending file AND its steering
class. A green scan that cannot go red is not evidence.

The environment needle class is private (R-LOC-03), so every test needing it
brings its own needle file; the built-in zero-tolerance class never skips.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests import _alwayson_steering as aos
    from tests import _portability_gate as pg
except ImportError:  # pragma: no cover - direct-script invocation
    import _alwayson_steering as aos
    import _portability_gate as pg

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
WORKSPACE = os.path.dirname(REPO)

STAGING = os.path.join(WORKSPACE, "agentware-scratch",
                       "260813-modular-core-adoption", "staging", "donor")
FIXTURE = os.path.join(HERE, "fixtures", "alwayson-steering.json")

#: The operator-directed class, assembled so this file is not itself a hit.
PRODUCT = "".join(("whats", "app"))

PROBE = "zorptoken"


def write(root, rel, body=""):
    path = os.path.join(root, rel)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path


@contextlib.contextmanager
def isolated_needles(tmp):
    """No ambient needle file.

    ``resolve_needle_file`` is an ordered SEARCH PATH, so an explicit
    ``--needles`` that does not exist falls through to the environment variable,
    the knowledge dir and the workspace copy. That fails safe (you get MORE
    needles, never fewer) but it means a test proving the "unavailable" branch
    has to rehome every candidate, not just pass a bogus path.
    """
    saved_env = {k: os.environ.pop(k, None)
                 for k in (pg.NEEDLE_ENV, "AGENTWARE_KNOWLEDGE_DIR")}
    saved_ws = pg.WORKSPACE
    pg.WORKSPACE = tmp
    try:
        yield
    finally:
        pg.WORKSPACE = saved_ws
        for key, value in saved_env.items():
            if value is not None:
                os.environ[key] = value


def needle_file(root, token=PROBE):
    payload = {"schema": pg.NEEDLE_SCHEMA,
               "needles": [{"class": pg.ENVIRONMENT, "token": token,
                            "match": pg.SUBSTRING, "hint": "probe",
                            "exempt": []}]}
    return write(root, "needles.json", json.dumps(payload))


class TempCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-alwayson-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def tree(self, name="t", claude="# root\n"):
        """A minimal but COMPLETE always-on tree: every declared path present."""
        root = os.path.join(self.tmp, name)
        write(root, "CLAUDE.md", claude)
        for rule in aos.POLICY:
            if rule["kind"] != "literal" or rule["class"] == aos.HARNESS_ROOT:
                continue
            for rel in rule["paths"]:
                write(root, rel, "# bridge\n")
        write(root, os.path.join(".claude", "agents", "a.md"), "# persona\n")
        return root


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

class ResolveTest(TempCase):

    def test_alwayson_resolves_every_declared_class(self):
        root = self.tree()
        report = aos.resolve(root)
        self.assertEqual(report["problems"], [])
        found = {e["class"] for e in report["entries"]}
        self.assertEqual(found, {r["class"] for r in aos.POLICY} - {aos.IMPORT})

    def test_alwayson_follows_an_import_from_a_harness_root(self):
        root = self.tree(claude="@AGENTS.md\n")
        write(root, "AGENTS.md", "# rules\n")
        rels = [e["rel"] for e in aos.resolve(root)["entries"]
                if e["class"] == aos.IMPORT]
        self.assertEqual(rels, ["AGENTS.md"])

    def test_alwayson_follows_imports_transitively(self):
        root = self.tree(claude="@AGENTS.md\n")
        write(root, "AGENTS.md", "@steering/deep.md\n")
        write(root, os.path.join("steering", "deep.md"), "# deep\n")
        rels = sorted(e["rel"] for e in aos.resolve(root)["entries"]
                      if e["class"] == aos.IMPORT)
        self.assertEqual(rels, ["AGENTS.md", "steering/deep.md"])

    def test_alwayson_import_cycle_terminates(self):
        root = self.tree(claude="@AGENTS.md\n")
        write(root, "AGENTS.md", "@CLAUDE.md\n@b.md\n")
        write(root, "b.md", "@AGENTS.md\n")
        rels = sorted(e["rel"] for e in aos.resolve(root)["entries"]
                      if e["class"] == aos.IMPORT)
        self.assertEqual(rels, ["AGENTS.md", "b.md"])

    def test_alwayson_ignores_an_at_sign_that_is_prose(self):
        root = self.tree(claude="mail me@example.com and see @AGENTS.md now\n")
        rels = [e["rel"] for e in aos.resolve(root)["entries"]
                if e["class"] == aos.IMPORT]
        self.assertEqual(rels, [])

    def test_alwayson_import_is_relative_to_its_declaring_file(self):
        root = self.tree(claude="@steering/a.md\n")
        write(root, os.path.join("steering", "a.md"), "@b.md\n")
        write(root, os.path.join("steering", "b.md"), "# b\n")
        rels = sorted(e["rel"] for e in aos.resolve(root)["entries"]
                      if e["class"] == aos.IMPORT)
        self.assertEqual(rels, ["steering/a.md", "steering/b.md"])

    def test_alwayson_every_entry_carries_the_reason_it_is_always_on(self):
        root = self.tree(claude="@AGENTS.md\n")
        write(root, "AGENTS.md", "# rules\n")
        report = aos.resolve(root)
        self.assertEqual(report["problems"], [])
        for entry in report["entries"]:
            self.assertTrue(entry["reason"].strip(), entry["rel"])


# --------------------------------------------------------------------------
# the ways the SET can be wrong while every file in it is clean
# --------------------------------------------------------------------------

class ProblemTest(TempCase):

    def kinds(self, report):
        return sorted({p["kind"] for p in report["problems"]})

    def test_alwayson_unresolved_import_is_a_problem_naming_the_parent(self):
        root = self.tree(claude="@missing.md\n")
        report = aos.resolve(root)
        self.assertEqual(self.kinds(report), [aos.UNRESOLVED])
        self.assertIn("CLAUDE.md", report["problems"][0]["detail"])

    def test_alwayson_a_vanished_declared_path_is_a_problem(self):
        root = self.tree()
        os.remove(os.path.join(root, ".cursorrules"))
        report = aos.resolve(root)
        self.assertEqual(self.kinds(report), [aos.MISSING])
        self.assertEqual(report["problems"][0]["rel"], ".cursorrules")

    def test_alwayson_an_empty_persona_dir_is_a_problem(self):
        root = self.tree()
        os.remove(os.path.join(root, ".claude", "agents", "a.md"))
        report = aos.resolve(root)
        self.assertEqual(self.kinds(report), [aos.EMPTY_GLOB])

    def test_alwayson_a_problem_makes_the_scoped_scan_not_ok(self):
        root = self.tree(claude="@missing.md\n")
        report = aos.scan(root, needles_path=needle_file(self.tmp))
        self.assertFalse(report["ok"])
        self.assertEqual(report["findings"], [])

    def test_alwayson_policy_digest_moves_when_the_policy_widens(self):
        widened = aos.POLICY + ({"class": "extra", "kind": "literal",
                                 "paths": ("x.md",), "reason": "r"},)
        self.assertNotEqual(aos.policy_digest(), aos.policy_digest(widened))

    def test_alwayson_policy_digest_is_stable_across_calls(self):
        self.assertEqual(aos.policy_digest(), aos.policy_digest())


# --------------------------------------------------------------------------
# the scan detects -- one independent probe per class
# --------------------------------------------------------------------------

class DetectTest(TempCase):

    def planted(self, rel, cls):
        root = self.tree(claude="@AGENTS.md\n")
        write(root, "AGENTS.md", "# rules\n")
        with open(os.path.join(root, rel), "a", encoding="utf-8") as handle:
            handle.write("\nsee the %s console\n" % PROBE)
        report = aos.scan(root, needles_path=needle_file(self.tmp))
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["findings"]), 1)
        hit = report["findings"][0]
        self.assertTrue(hit["location"].startswith(rel.replace(os.sep, "/")))
        self.assertEqual(hit["steering_class"], cls)
        return hit

    def test_alwayson_detects_in_the_harness_root(self):
        self.planted("CLAUDE.md", aos.HARNESS_ROOT)

    def test_alwayson_detects_in_an_imported_document(self):
        self.planted("AGENTS.md", aos.IMPORT)

    def test_alwayson_detects_in_a_harness_bridge_file(self):
        self.planted(".cursorrules", aos.BRIDGE)

    def test_alwayson_detects_in_a_persona(self):
        self.planted(os.path.join(".claude", "agents", "a.md"), aos.PERSONA)

    def test_alwayson_detects_in_the_session_injector(self):
        self.planted(os.path.join("scripts", "hooks", "session-start.sh"),
                     aos.INJECTOR)

    def test_alwayson_detects_the_zero_tolerance_class_with_no_needle_file(self):
        root = self.tree()
        write(root, "CLAUDE.md", "integrate with %s today\n" % PRODUCT)
        with isolated_needles(self.tmp):
            report = aos.scan(root,
                              needles_path=os.path.join(self.tmp, "none.json"))
        self.assertEqual(report["classes"][pg.ENVIRONMENT]["status"],
                         "unavailable")
        classes = {f["class"] for f in report["findings"]}
        self.assertEqual(classes, {pg.ZERO_TOLERANCE})

    def test_alwayson_ignores_a_leak_outside_the_always_on_set(self):
        root = self.tree()
        write(root, os.path.join("docs", "notes.md"), "the %s console\n" % PROBE)
        report = aos.scan(root, needles_path=needle_file(self.tmp))
        self.assertTrue(report["ok"], report["findings"])

    def test_alwayson_detects_a_leak_in_a_FILENAME(self):
        root = self.tree()
        os.rename(os.path.join(root, ".claude", "agents", "a.md"),
                  os.path.join(root, ".claude", "agents", "%s.md" % PROBE))
        report = aos.scan(root, needles_path=needle_file(self.tmp))
        surfaces = {f["surface"] for f in report["findings"]}
        self.assertEqual(surfaces, {pg.FILENAMES})


# --------------------------------------------------------------------------
# the live claims: this tree, the staged tree, and the ratchet
# --------------------------------------------------------------------------

class LiveTest(unittest.TestCase):

    def fixture(self):
        with open(FIXTURE, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def test_alwayson_fixture_matches_this_tree_exactly(self):
        want = self.fixture()
        got = aos.resolve(REPO)
        self.assertEqual(want["policy_digest"], got["policy_digest"])
        self.assertEqual(want["counts"], got["counts"])
        self.assertEqual([(e["rel"], e["class"], e["via"])
                          for e in want["entries"]],
                         [(e["rel"], e["class"], e["via"])
                          for e in got["entries"]])

    def assert_clean(self, root):
        """A clean scan is only evidence if BOTH needle classes were armed."""
        report = aos.scan(root)
        for cls in pg.CLASSES:
            self.assertNotEqual(report["classes"][cls]["status"], "unavailable",
                                "%s needles unavailable: %s"
                                % (cls, report["classes"][cls]))
        self.assertEqual(report["problems"], [])
        self.assertTrue(report["ok"], report["findings"])
        return report

    def test_alwayson_this_tree_scans_clean(self):
        self.assert_clean(REPO)

    def test_alwayson_the_set_is_bigger_than_the_obvious_three(self):
        """The scope this task would have had if it were eyeballed."""
        rels = {e["rel"] for e in aos.resolve(REPO)["entries"]}
        obvious = {"CLAUDE.md", "AGENTS.md", "steering/common-problems.md",
                   "steering/project-context.md"}
        self.assertTrue(obvious <= rels)
        self.assertGreater(len(rels), len(obvious))

    @unittest.skipUnless(os.path.isdir(STAGING),
                         "staged donor absent: %s" % STAGING)
    def test_alwayson_staged_donor_steering_scans_clean(self):
        """Task 13's acceptance criterion, over the named set."""
        self.assert_clean(STAGING)

    @unittest.skipUnless(os.path.isdir(STAGING),
                         "staged donor absent: %s" % STAGING)
    def test_alwayson_staged_donor_resolves_the_same_policy(self):
        got = aos.resolve(STAGING)
        self.assertEqual(got["policy_digest"], aos.policy_digest())
        self.assertGreaterEqual(got["counts"]["import"], 3)
        self.assertEqual(got["counts"]["bridge"], 5)

    @unittest.skipUnless(os.path.isdir(STAGING),
                         "staged donor absent: %s" % STAGING)
    def test_alwayson_staged_injector_carries_the_neutral_wording(self):
        """The one file this task actually edited, asserted by content."""
        path = os.path.join(STAGING, "scripts", "hooks", "session-start.sh")
        with open(path, "r", encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("resolve the enclosing", body)
        self.assertIn("project/repo", body)


# --------------------------------------------------------------------------
# hygiene: this task's own artifacts
# --------------------------------------------------------------------------

class HygieneTest(unittest.TestCase):

    ARTIFACTS = ("tests/_alwayson_steering.py",
                 "tests/gates/test_alwayson_steering.py",
                 "tests/fixtures/alwayson-steering.json")

    def bodies(self):
        for rel in self.ARTIFACTS:
            with open(os.path.join(REPO, rel), "r", encoding="utf-8") as handle:
                yield rel, handle.read()

    def test_alwayson_artifacts_carry_no_home_path(self):
        for rel, body in self.bodies():
            self.assertNotIn(os.path.expanduser("~"), body, rel)

    def test_alwayson_artifacts_carry_no_zero_tolerance_token(self):
        for rel, body in self.bodies():
            self.assertNotIn(PRODUCT, body.lower(), rel)

    def test_alwayson_artifacts_are_within_the_test_ceiling(self):
        for rel, body in self.bodies():
            self.assertLessEqual(len(body.splitlines()), 600, rel)

    def test_alwayson_cli_exits_zero_on_this_tree(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = aos.main(["--tree", REPO])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("OK", buf.getvalue())

    def test_alwayson_cli_require_class_exits_two_when_unavailable(self):
        tmp = tempfile.mkdtemp(prefix="aw-alwayson-cli-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        buf, err = io.StringIO(), io.StringIO()
        with isolated_needles(tmp):
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                rc = aos.main(["--tree", REPO, "--json",
                               "--needles", os.path.join(tmp, "nope.json"),
                               "--require-class", pg.ENVIRONMENT])
        self.assertEqual(rc, 2, err.getvalue())
        self.assertIn(pg.ENVIRONMENT, err.getvalue())
        self.assertEqual(json.loads(buf.getvalue())["schema"], aos.SCHEMA)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
