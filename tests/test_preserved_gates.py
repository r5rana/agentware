"""Task-9 preserved-surface freeze: the gates, run against real trees.

Split from `tests/test_preserved_surface.py` (which holds the fixture-shape and
pure-diff assertions) to stay inside the plan's 600-line `tests/**` ceiling.
This half is the slower one: it copies asset trees, builds a symlink farm and
runs real `unittest` discovery.

Stdlib `unittest` only. Never touches the real knowledge base.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
DONOR = "/tmp/aw-scratch/Agentware"
sys.path.insert(0, HERE)

from _preserved_freeze import (  # noqa: E402
    check_destination, derive, diff_node_ids, load_node_ids,
    reachable_symbols, unsatisfied_assets, verify)
from _preserved_scan import (  # noqa: E402
    FIXTURE_FAMILIES, ScopeIndex, collect_node_ids, scan_cli_symbols)

FIXTURE = os.path.join(HERE, "fixtures", "preserved-surface.json")


def load_fixture():
    with open(FIXTURE) as fh:
        return json.load(fh)


def donor_available():
    return os.path.isdir(os.path.join(DONOR, "tests"))


class AssetGateTest(unittest.TestCase):
    """Asset checks run against a scratch tree, so each can be broken cheaply."""

    def setUp(self):
        self.fx = load_fixture()
        self.root = tempfile.mkdtemp(prefix="aw-assets-")
        self.addCleanup(shutil.rmtree, self.root, True)
        for rel in self.fx["executables"]:
            dst = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            src = os.path.join(REPO, rel)
            if os.path.exists(src):
                shutil.copy2(src, dst)
                os.chmod(dst, 0o755)
        for sub in ("templates", "catalog"):
            os.makedirs(os.path.join(self.root, sub), exist_ok=True)
        for name in self.fx["templates"]:
            src = os.path.join(REPO, "templates", name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(self.root, "templates", name))
        for name in self.fx["catalogs"]:
            src = os.path.join(REPO, "catalog", name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(self.root, "catalog", name))
        os.makedirs(os.path.join(self.root, "tests", "fixtures"), exist_ok=True)
        for _family, rel, kind in FIXTURE_FAMILIES:
            src = os.path.join(REPO, rel)
            dst = os.path.join(self.root, rel)
            if kind == "dir" and os.path.isdir(src):
                shutil.copytree(src, dst)
            elif kind == "file" and os.path.isfile(src):
                shutil.copy2(src, dst)

    def unsatisfied(self):
        return unsatisfied_assets(self.fx, self.root)

    def test_the_scratch_tree_matches_the_frozen_pending_set(self):
        self.assertEqual(set(self.unsatisfied()),
                         set(self.fx["assets"]["pending_in_destination"]))

    def test_deleting_the_commit_msg_hook_reddens(self):
        os.remove(os.path.join(self.root, "scripts/hooks/commit-msg"))
        self.assertIn("MISSING executable: scripts/hooks/commit-msg",
                      self.unsatisfied().values())

    def test_dropping_the_executable_bit_reddens(self):
        """Mode 0644 is the failure task 25 warns about: eleven `-x` guards in
        the orchestrator FAIL OPEN, so nothing else turns red."""
        os.chmod(os.path.join(self.root, "scripts/agentware"), 0o644)
        self.assertEqual(self.unsatisfied()["executable:scripts/agentware"],
                         "NOT EXECUTABLE: scripts/agentware")

    def test_gutting_a_template_reddens_naming_the_anchor(self):
        path = os.path.join(self.root, "templates", "skill-template.md")
        with open(path, "w") as fh:
            fh.write("# regenerated, contract lost\n")
        msg = self.unsatisfied()["template:skill-template.md"]
        self.assertIn("lost anchor", msg)
        self.assertIn("agentskills.io", msg)

    def test_a_template_present_but_renamed_still_reddens(self):
        os.rename(os.path.join(self.root, "templates", "learning-template.md"),
                  os.path.join(self.root, "templates", "learning.md"))
        self.assertIn("template:learning-template.md", self.unsatisfied())

    def test_removing_a_catalog_file_reddens_by_basename(self):
        os.remove(os.path.join(self.root, "catalog", "skills.json"))
        self.assertIn("MISSING catalog: skills.json", self.unsatisfied().values())

    def test_removing_a_fixture_family_reddens(self):
        shutil.rmtree(os.path.join(self.root, "tests", "fixtures", "swe_tasks"))
        self.assertIn("fixture_family:swe_tasks", self.unsatisfied())

    def test_adding_the_donor_only_assets_clears_them_from_pending(self):
        shutil.copy2(os.path.join(REPO, "templates", "learning-template.md"),
                     os.path.join(self.root, "templates", "decision-template.md"))
        self.assertNotIn("template:decision-template.md", self.unsatisfied())


class ScopeAwareSymbolScanTest(unittest.TestCase):
    """The CLI-symbol scanner resolves bindings per scope, not per file."""

    def scan(self, source):
        root = tempfile.mkdtemp(prefix="aw-scope-")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "tests"))
        with open(os.path.join(root, "tests", "test_x.py"), "w") as fh:
            fh.write(source)
        return set(scan_cli_symbols(root))

    def test_module_level_binding_is_followed(self):
        self.assertEqual(self.scan("mod = load_cli()\nmod.save_index()\n"),
                         {"save_index"})

    def test_a_shadowing_local_binding_is_NOT_followed(self):
        """The exact false positive that made a file-global tracker report 951
        symbols of which 377 do not exist on the CLI module."""
        found = self.scan("mod = load_cli()\n"
                          "def t():\n"
                          "    mod = object()\n"
                          "    return mod.NOT_A_CLI_SYMBOL\n")
        self.assertEqual(found, set())

    def test_a_parameter_shadowing_the_name_is_NOT_followed(self):
        found = self.scan("m = load_cli()\n"
                          "def t(m):\n"
                          "    return m.NOT_A_CLI_SYMBOL\n")
        self.assertEqual(found, set())

    def test_self_attribute_binding_is_followed(self):
        found = self.scan("class T:\n"
                          "    def setUp(self):\n"
                          "        self.cli = load_cli()\n"
                          "    def t(self):\n"
                          "        return self.cli.load_index\n")
        self.assertEqual(found, {"load_index"})

    def test_an_attribute_also_bound_to_something_else_is_NOT_followed(self):
        found = self.scan("class T:\n"
                          "    def a(self):\n"
                          "        self.cli = load_cli()\n"
                          "    def b(self):\n"
                          "        self.cli = object()\n"
                          "    def t(self):\n"
                          "        return self.cli.AMBIGUOUS\n")
        self.assertEqual(found, set())

    def test_an_unrelated_module_attribute_is_not_a_cli_symbol(self):
        self.assertEqual(self.scan("import os\nos.path.join('a')\n"), set())

    def test_for_loop_rebinding_shadows_the_cli_binding(self):
        found = self.scan("mod = load_cli()\n"
                          "for mod in [1, 2]:\n"
                          "    print(mod.bit_length)\n")
        self.assertEqual(found, set())

    def test_scope_index_reports_cli_only_bindings(self):
        import ast
        tree = ast.parse("a = load_cli()\nb = 1\n")
        idx = ScopeIndex().build(tree)
        self.assertTrue(idx.resolves_to_cli("a", tree))
        self.assertFalse(idx.resolves_to_cli("b", tree))
        self.assertFalse(idx.resolves_to_cli("never_bound", tree))


class LiveGateTest(unittest.TestCase):
    """The real gate, against the real tree. Slower, and worth it."""

    def test_head_loses_nothing_that_is_frozen(self):
        self.assertEqual(check_destination(FIXTURE, REPO), [])

    def test_every_locally_read_cli_symbol_resolves_at_head(self):
        fx = load_fixture()
        names = set(fx["cli_symbols"]["by_symbol"])
        required = names - set(fx["cli_symbols"]["pending_in_destination"])
        self.assertEqual(sorted(required - reachable_symbols(REPO, names)), [])

    def test_the_cli_symbol_gate_is_not_measuring_an_empty_set(self):
        """Task 28 non-vacuity pin on the READ seam.

        The assertion above is `required - reachable == []`, which an empty
        `required` satisfies forever -- and `required` is derived from a fixture
        and a pending list that later tasks edit. So pin the floor, and prove
        the probe can say NO: a probe that reports every name it is handed as
        reachable would report a facade with nothing behind it as complete
        coverage. The WRITE half of this seam is `tests/test_mutation_canary`.
        """
        fx = load_fixture()
        names = set(fx["cli_symbols"]["by_symbol"])
        required = names - set(fx["cli_symbols"]["pending_in_destination"])
        self.assertGreaterEqual(len(required), 350)
        self.assertEqual(
            reachable_symbols(REPO, {"aw_symbol_no_tree_has_ever_defined"}),
            set())

    def test_head_is_a_SUPERSET_of_the_frozen_local_set(self):
        """Superset, never equality: the freeze must not block later tasks from
        adding tests, only from losing one."""
        live, errors = collect_node_ids(REPO)
        self.assertEqual(errors, [])
        frozen = set(load_node_ids(FIXTURE, "local")["node_ids"])
        self.assertEqual(sorted(frozen - set(live)), [])

    def test_head_is_not_yet_a_superset_of_the_donor_set(self):
        """States the ratchet's premise explicitly, so the day it becomes false
        the backlog is provably empty rather than silently ignored."""
        fx = load_fixture()
        pending = set(fx["node_ids"]["pending_in_destination"])
        landed = set(fx["node_ids"]["landed_from_donor"])
        donor = set(load_node_ids(FIXTURE, "donor")["node_ids"])
        local = set(load_node_ids(FIXTURE, "local")["node_ids"])
        self.assertTrue(pending, "donor backlog already empty -- retire the ratchet")
        # A donor id is either shared with the frozen local set or explicitly
        # LANDED by a port wave; nothing else may be off the backlog. Landed ids
        # are then required to keep collecting (diff_node_ids reports a
        # non-pending donor id that stops resolving as LOST), so moving one out
        # of pending arms its regression check rather than silencing it.
        self.assertEqual(donor - pending, (donor & local) | landed)


@unittest.skipUnless(donor_available(), "donor tree not staged")
class CopyOverCollisionTest(unittest.TestCase):
    """THE acceptance proof: copying the donor test file over the local path
    must redden the local frozen set, naming every destroyed test."""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="aw-collide-")
        for name in os.listdir(REPO):
            if name not in (".git", "tests"):
                os.symlink(os.path.join(REPO, name), os.path.join(cls.root, name))
        os.mkdir(os.path.join(cls.root, "tests"))
        for name in os.listdir(os.path.join(REPO, "tests")):
            if name == "__pycache__":
                continue
            src = (os.path.join(DONOR, "tests", name)
                   if name == "test_skill_promote.py"
                   else os.path.join(REPO, "tests", name))
            os.symlink(src, os.path.join(cls.root, "tests", name))
        cls.live, cls.errors = collect_node_ids(cls.root)
        cls.base, _ = collect_node_ids(REPO)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_the_copy_itself_collects_cleanly(self):
        """The donor file IMPORTS fine here -- the loss is silent, not a crash.

        Measured as a DELTA against the live tree rather than an absolute
        count, so later tasks adding tests cannot redden this proof.
        """
        self.assertEqual(self.errors, [])
        prefix = "tests.test_skill_promote."
        base = {n for n in self.base if not n.startswith(prefix)}
        self.assertEqual(len(self.base) - len(base), 30)
        self.assertEqual(len(set(self.live) - base), 8)
        self.assertEqual(len(base - set(self.live)), 0)

    def test_the_frozen_local_set_reddens_naming_every_destroyed_test(self):
        fx = load_fixture()
        out = diff_node_ids(load_node_ids(FIXTURE, "local")["node_ids"],
                            load_node_ids(FIXTURE, "donor")["node_ids"],
                            fx["node_ids"]["pending_in_destination"], self.live)
        lost = [v for v in out if v.startswith("LOST node id:")]
        self.assertEqual(len(lost), 30)
        for v in lost:
            self.assertIn("tests.test_skill_promote.", v)

    def test_the_incoming_donor_tests_trip_the_ratchet_too(self):
        fx = load_fixture()
        out = diff_node_ids([], load_node_ids(FIXTURE, "donor")["node_ids"],
                            fx["node_ids"]["pending_in_destination"], self.live)
        self.assertEqual(len([v for v in out if "RATCHET" in v]), 8)

    def test_no_filename_check_could_have_seen_this(self):
        """Both trees carry the same path; only node ids expose the loss."""
        self.assertTrue(os.path.exists(os.path.join(REPO, "tests/test_skill_promote.py")))
        self.assertTrue(os.path.exists(os.path.join(DONOR, "tests/test_skill_promote.py")))


@unittest.skipUnless(donor_available(), "donor tree not staged")
class DeterminismTest(unittest.TestCase):

    """Determinism is asserted against the FROZEN input (the read-only donor
    tree) and as a superset against the live one, so a later task adding a test
    cannot make a determinism check look like a determinism failure."""

    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="aw-redrv-")
        cls.path = os.path.join(cls.out, "preserved-surface.json")
        cls.fresh = derive(REPO, DONOR, cls.path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_the_donor_half_reproduces_byte_for_byte(self):
        with open(os.path.join(self.out, "nodeids-donor.json")) as fh:
            self.assertEqual(json.load(fh), load_node_ids(FIXTURE, "donor"))

    def test_the_local_half_is_a_superset_of_the_frozen_one(self):
        with open(os.path.join(self.out, "nodeids-local.json")) as fh:
            fresh = set(json.load(fh)["node_ids"])
        self.assertEqual(sorted(set(load_node_ids(FIXTURE, "local")["node_ids"])
                                - fresh), [])

    def test_every_non_node_id_section_reproduces_exactly(self):
        committed = load_fixture()
        for section in ("executables", "templates", "catalogs",
                        "fixture_families", "assets"):
            self.assertEqual(self.fresh[section], committed[section], section)

    def test_the_pinned_collision_destinations_reproduce_exactly(self):
        committed = load_fixture()["test_path_collisions"]["rows"]
        for rel, row in committed.items():
            self.assertEqual(self.fresh["test_path_collisions"]["rows"][rel]
                             ["destinations"], row["destinations"], rel)

    def test_a_freshly_derived_fixture_verifies(self):
        self.assertEqual(verify(self.path), [])


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
