"""Task-9 preserved-surface freeze: the gates, run against real trees.

Split from `tests/test_preserved_surface.py` (which holds the fixture-shape and
pure-diff assertions) to stay inside the plan's 600-line `tests/**` ceiling.
This half is the slower one: it copies asset trees, builds a symlink farm and
runs real `unittest` discovery.

Stdlib `unittest` only. Never touches the real knowledge base.
"""

from tests.gates._preserved_gates_support import (  # noqa: F401
    FIXTURE, FIXTURE_FAMILIES, REPO, ScopeIndex, check_destination,
    collect_node_ids, load_fixture, load_node_ids, os, reachable_symbols,
    resolves, scan_cli_symbols, shutil, tempfile, unittest,
    unsatisfied_assets)


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
        adding tests, only from losing one.

        Read THROUGH the task-58 relocation ledger. Layering the corpus changed
        the module half of every frozen id, so a raw set-difference now reports
        all 2,624 as missing -- a true statement about a string and a false one
        about the suite. `resolves()` retries each id at the module the ledger
        RECORDS as its successor; an id no successor collects is still missing,
        which `LedgerHonestyTest` proves from the other side.
        """
        live, errors = collect_node_ids(REPO)
        self.assertEqual(errors, [])
        frozen = set(load_node_ids(FIXTURE, "local")["node_ids"])
        live = set(live)
        self.assertEqual(sorted(n for n in frozen if not resolves(n, live)), [])

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
