"""KB schema versioning + migration-runner tests (feature 260710, Task 3).

Covers the bootstrap contract (missing .schema == 0, --list read-only, --apply
stamps), idempotency (apply twice == once), the additive auto-apply + journal,
the restructuring REFUSAL path, the older-CLI version guard, and the KB-write
dispatch trigger. Synthetic migrations are injected via patch.object so the
runner is exercised independently of the real registry.

All test names are prefixed `test_migrate_`.

Runner: python3 -m unittest tests.unit.features.test_migrate_runner -v
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from tests._fixtures import (run_cli as _raw_run_cli, build_synthetic_kb,
                                 load_cli, load_lib)
except ImportError:
    from _fixtures import (run_cli as _raw_run_cli, build_synthetic_kb,
                           load_cli, load_lib)

#: The module that OWNS the runner since task 37. Patch targets address it, not
#: the facade: the runner reads its own globals, so patching a re-export would be
#: a silent no-op (see tests/_fixtures.load_lib).
MIGRATE_MOD = "agentware_lib.features.migratecmd"


class MigrateTestCase(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-migrate-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = load_lib(MIGRATE_MOD)
        # The monolith's own dispatch arm, which reaches the SAME runner through
        # its module alias — the interlock the dispatch-trigger tests below prove
        # by patching `self.mod` and observing the effect through `self.cli`.
        self.cli = load_cli()

    def run_cli(self, argv):
        try:
            return _raw_run_cli(argv, self.kdir)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            return code, "", "SystemExit(%r)" % (exc.code,)

    def schema_path(self):
        return os.path.join(self.kdir, ".schema")

    def write_schema(self, n):
        with open(self.schema_path(), "w", encoding="utf-8") as f:
            f.write("%d\n" % n)

    def mark_initialized(self):
        with open(os.path.join(self.kdir, ".initialized"), "w",
                  encoding="utf-8") as f:
            f.write("test\n")

    # --- bootstrap ----------------------------------------------------------
    def test_migrate_list_is_read_only(self):
        code, out, _e = self.run_cli(["migrate", "--list"])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.schema_path()),
                         "--list must NOT create .schema")
        self.assertRegex(out, r"^migrate: (schema \d+ -> \d+, \d+ pending|"
                              r"up to date \(schema \d+, 0 pending\))")

    def test_migrate_bootstrap_apply_stamps_schema(self):
        self.assertFalse(os.path.exists(self.schema_path()))
        code, _o, _e = self.run_cli(["migrate", "--apply"])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.schema_path()))
        with open(self.schema_path()) as f:
            self.assertEqual(int(f.read().strip()), self.mod.KB_SCHEMA_VERSION)

    def test_migrate_idempotent_apply_twice(self):
        self.run_cli(["migrate", "--apply"])
        code, out, _e = self.run_cli(["migrate", "--apply"])
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^migrate: up to date \(schema \d+, 0 pending\)\n?$")

    # --- additive apply + journal (synthetic migration) ---------------------
    def _additive_migration(self, marker):
        def fn(kdir):
            with open(os.path.join(kdir, marker), "a", encoding="utf-8") as f:
                f.write("ran\n")
        return (1, "additive", "synthetic-additive", fn)

    def test_migrate_additive_applies_and_journals(self):
        mig = self._additive_migration("MIG1_RAN")
        with mock.patch.object(self.mod, "_KB_MIGRATIONS", (mig,)), \
                mock.patch.object(self.mod, "KB_SCHEMA_VERSION", 1):
            result, err = self.mod._kb_migrate_apply(self.kdir)
        self.assertIsNone(err)
        self.assertEqual(result["schema"], 1)
        self.assertEqual(len(result["applied"]), 1)
        self.assertTrue(os.path.exists(os.path.join(self.kdir, "MIG1_RAN")))
        journal = os.path.join(self.kdir, "logs", "migrations.jsonl")
        self.assertTrue(os.path.exists(journal))
        with open(journal) as f:
            rec = json.loads(f.readline())
        self.assertEqual(rec["version"], 1)
        self.assertEqual(rec["tier"], "additive")

    def test_migrate_additive_idempotent_runs_once(self):
        mig = self._additive_migration("MIG1_ONCE")
        with mock.patch.object(self.mod, "_KB_MIGRATIONS", (mig,)), \
                mock.patch.object(self.mod, "KB_SCHEMA_VERSION", 1):
            self.mod._kb_migrate_apply(self.kdir)
            self.mod._kb_migrate_apply(self.kdir)
        with open(os.path.join(self.kdir, "MIG1_ONCE")) as f:
            self.assertEqual(f.read().count("ran"), 1)

    # --- restructuring refusal ----------------------------------------------
    def test_migrate_restructuring_refused(self):
        def fn(kdir):
            raise AssertionError("restructuring migration must NOT be auto-applied")
        mig = (1, "restructuring", "synthetic-restructure", fn)
        with mock.patch.object(self.mod, "_KB_MIGRATIONS", (mig,)), \
                mock.patch.object(self.mod, "KB_SCHEMA_VERSION", 1):
            result, err = self.mod._kb_migrate_apply(self.kdir)
            code, out, _e = self.run_cli(["migrate", "--apply"])
        self.assertIsNone(err)
        self.assertIsNotNone(result["refused"])
        self.assertEqual(result["schema"], 0)   # NOT bumped past the refusal
        self.assertEqual(code, 1)
        self.assertIn("workorder", out.lower())

    # --- older-CLI version guard --------------------------------------------
    def test_migrate_version_guard_older_cli_refuses(self):
        self.write_schema(9)   # KB newer than the package
        with mock.patch.object(self.mod, "KB_SCHEMA_VERSION", 1):
            result, err = self.mod._kb_migrate_apply(self.kdir)
        self.assertIsNone(result)
        self.assertIsNotNone(err)
        self.assertIn("older cli", err.lower())

    # --- dispatch trigger ---------------------------------------------------
    def test_migrate_dispatch_trigger_autoapplies_on_write(self):
        self.mark_initialized()
        self.write_schema(0)
        mig = self._additive_migration("MIG_DISPATCH")
        prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(lambda: os.environ.__setitem__(
            "AGENTWARE_KNOWLEDGE_DIR", prev) if prev is not None
            else os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None))
        args = mock.Mock()
        args.func = self.cli.cmd_learn   # a KB-writer func
        with mock.patch.object(self.mod, "_KB_MIGRATIONS", (mig,)), \
                mock.patch.object(self.mod, "KB_SCHEMA_VERSION", 1):
            rc = self.cli._kb_predispatch_gate(args)
        self.assertIsNone(rc)   # proceed
        self.assertTrue(os.path.exists(os.path.join(self.kdir, "MIG_DISPATCH")))
        with open(self.schema_path()) as f:
            self.assertEqual(int(f.read().strip()), 1)

    def test_migrate_dispatch_guard_older_cli_refuses_write(self):
        self.mark_initialized()
        self.write_schema(9)
        prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(lambda: os.environ.__setitem__(
            "AGENTWARE_KNOWLEDGE_DIR", prev) if prev is not None
            else os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None))
        args = mock.Mock()
        args.func = self.cli.cmd_learn
        with mock.patch.object(self.mod, "KB_SCHEMA_VERSION", 1):
            rc = self.cli._kb_predispatch_gate(args)
        self.assertEqual(rc, 1)

    def test_migrate_dispatch_noop_for_reader(self):
        self.mark_initialized()
        self.write_schema(0)
        args = mock.Mock()
        args.func = self.cli.cmd_recall   # a READER — not in the writer set
        with mock.patch.object(self.mod, "_KB_MIGRATIONS",
                               (self._additive_migration("SHOULD_NOT_RUN"),)), \
                mock.patch.object(self.mod, "KB_SCHEMA_VERSION", 1):
            rc = self.cli._kb_predispatch_gate(args)
        self.assertIsNone(rc)
        self.assertFalse(os.path.exists(os.path.join(self.kdir, "SHOULD_NOT_RUN")))


if __name__ == "__main__":
    unittest.main()
