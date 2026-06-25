"""Smoke tests for the EXISTING agentware CLI commands against a synthetic KB.

Runner:  python3 -m unittest discover -s tests -v
   or:   python3 -m unittest tests.test_existing_cli -v
"""

import json
import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # when run via `discover -s tests`, tests/ is on sys.path
    from _fixtures import SyntheticKBTestCase, load_cli


class TestExistingCli(SyntheticKBTestCase):
    def test_config_resolves_synthetic_kdir(self):
        code, out, _ = self.run_cli(["config", "--format", "json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["knowledge_dir"], self.kdir)
        self.assertTrue(payload["configured"])

    def test_config_knowledge_dir_only(self):
        code, out, _ = self.run_cli(["config", "--knowledge-dir-only"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), self.kdir)

    def test_index_validate_passes_on_synthetic_kb(self):
        code, out, _ = self.run_cli(["index", "validate", "--format", "json"])
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["errors"], [])

    def test_query_by_tag_returns_entry(self):
        code, out, _ = self.run_cli(["query", "--tag", "geofence"])
        self.assertEqual(code, 0)
        results = json.loads(out)
        ids = {r["id"] for r in results}
        self.assertIn("learn-geofence-reminders", ids)

    def test_query_by_unknown_tag_is_empty(self):
        code, out, _ = self.run_cli(["query", "--tag", "no-such-tag-xyz"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])

    def test_query_by_category(self):
        code, out, _ = self.run_cli(["query", "--category", "learnings"])
        self.assertEqual(code, 0)
        cats = {r["category"] for r in json.loads(out)}
        self.assertEqual(cats, {"learnings"})

    def test_learn_creates_and_registers_entry(self):
        code, out, err = self.run_cli([
            "learn", "--topic", "smoke-test-topic",
            "--summary", "a smoke test learning",
            "--tags", "smoke,test",
            "--content", "This is the body of a smoke test learning.",
            "--format", "json",
        ])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["learning"]["id"], "learn-smoke-test-topic")
        # File exists on disk...
        self.assertTrue(os.path.isfile(
            os.path.join(self.kdir, "learnings", "smoke-test-topic.md")))
        # ...and is registered in the index.
        idx = self.read_index()
        ids = {e["id"] for e in idx["entries"]}
        self.assertIn("learn-smoke-test-topic", ids)
        # Index still validates after the mutation.
        code2, _, _ = self.run_cli(["index", "validate"])
        self.assertEqual(code2, 0)

    def test_audit_passes_on_synthetic_kb(self):
        code, out, _ = self.run_cli(["audit", "--format", "json"])
        payload = json.loads(out)
        # All KB-scoped checks must be clean; steering_lint runs over the real
        # repo steering and must also be clean.
        failed = [c["name"] for c in payload["checks"] if not c["ok"]]
        self.assertEqual(failed, [], "audit checks failed: %s" % out)
        self.assertEqual(code, 0)

    def test_audit_includes_personal_data_check(self):
        # The R-LOC-03 personal-data guard is wired into every `audit` run and
        # PASSES on the real (clean) package tree.
        code, out, _ = self.run_cli(["audit", "--format", "json"])
        payload = json.loads(out)
        names = [c["name"] for c in payload["checks"]]
        self.assertIn("personal_data", names)
        pd = next(c for c in payload["checks"] if c["name"] == "personal_data")
        self.assertTrue(pd["ok"], "personal_data should be clean: %s" % out)
        self.assertEqual(code, 0)


class TestPersonalDataGuard(unittest.TestCase):
    """Unit tests for the R-LOC-03 personal-data leak guard helpers."""

    def setUp(self):
        self.cli = load_cli()
        self.root = tempfile.mkdtemp(prefix="agentware-pd-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_scan_detects_injected_leak(self):
        self._write("docs/leak.md", "operator home is /Users/someone here\n")
        hits = self.cli.scan_personal_data(self.root, ["/Users/someone"])
        self.assertTrue(hits, "injected leak should be detected")
        self.assertIn("docs/leak.md", hits[0])

    def test_scan_clean_tree_passes(self):
        self._write("docs/ok.md", "nothing personal in this file\n")
        hits = self.cli.scan_personal_data(self.root, ["/Users/someone"])
        self.assertEqual(hits, [])

    def test_scan_skips_excluded_dirs(self):
        # A leak inside an excluded dir (e.g. .agentware-logs) must NOT trip it.
        self._write(".agentware-logs/transcript.txt", "/Users/someone\n")
        hits = self.cli.scan_personal_data(self.root, ["/Users/someone"])
        self.assertEqual(hits, [])

    def test_scan_empty_needles_is_noop(self):
        self._write("docs/x.md", "anything\n")
        self.assertEqual(self.cli.scan_personal_data(self.root, []), [])

    def test_identity_excludes_generic_handle(self):
        # A KB whose MAIN.md carries only a generic handle yields no handle
        # needle (would otherwise match framework prose everywhere).
        kdir = tempfile.mkdtemp(prefix="agentware-pd-kb-")
        self.addCleanup(shutil.rmtree, kdir, True)
        with open(os.path.join(kdir, "MAIN.md"), "w", encoding="utf-8") as f:
            f.write("> **Handle**: operator\n")
        strings = self.cli.operator_identity_strings(kdir)
        self.assertNotIn("operator", strings)

    def test_identity_includes_real_handle_and_email(self):
        kdir = tempfile.mkdtemp(prefix="agentware-pd-kb-")
        self.addCleanup(shutil.rmtree, kdir, True)
        with open(os.path.join(kdir, "MAIN.md"), "w", encoding="utf-8") as f:
            f.write("- **Handle**: zaphod\n- email: zaphod@example.com\n")
        strings = self.cli.operator_identity_strings(kdir)
        self.assertIn("zaphod", strings)
        self.assertIn("zaphod@example.com", strings)


class TestKbAutocommitConfig(unittest.TestCase):
    """Resolution + persistence of AGENTWARE_KB_AUTOCOMMIT (feature
    260625-kb-autocommit-default, Task 1.1).

    Precedence: env -> config.env -> default ON. The suite patches HOME_CONFIG /
    CONFIG_PATHS onto a temp file and manages the env var, so it NEVER touches
    the operator's real ~/.agentware/config.env.
    """

    def setUp(self):
        import io as _io
        import contextlib as _ctx
        self._io, self._ctx = _io, _ctx
        self.cli = load_cli()
        self.home = tempfile.mkdtemp(prefix="agentware-ac-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cfg = os.path.join(self.home, ".agentware", "config.env")

        # Patch the module's config paths to the temp config; restore on cleanup.
        self._orig_home_config = self.cli.HOME_CONFIG
        self._orig_config_paths = self.cli.CONFIG_PATHS
        self.cli.HOME_CONFIG = self.cfg
        self.cli.CONFIG_PATHS = (self.cfg,)

        def _restore_paths():
            self.cli.HOME_CONFIG = self._orig_home_config
            self.cli.CONFIG_PATHS = self._orig_config_paths
        self.addCleanup(_restore_paths)

        # Neutralize any inherited env var; restore exactly afterward.
        self._prev_env = os.environ.pop(self.cli.AUTOCOMMIT_KEY, None)

        def _restore_env():
            if self._prev_env is None:
                os.environ.pop(self.cli.AUTOCOMMIT_KEY, None)
            else:
                os.environ[self.cli.AUTOCOMMIT_KEY] = self._prev_env
        self.addCleanup(_restore_env)

    def _run(self, argv):
        out, err = self._io.StringIO(), self._io.StringIO()
        with self._ctx.redirect_stdout(out), self._ctx.redirect_stderr(err):
            code = self.cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _set_env(self, val):
        os.environ[self.cli.AUTOCOMMIT_KEY] = val

    def _write_cfg(self, text):
        os.makedirs(os.path.dirname(self.cfg), exist_ok=True)
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write(text)

    def _read_cfg(self):
        with open(self.cfg, "r", encoding="utf-8") as f:
            return f.read()

    # --- resolution precedence ------------------------------------------------
    def test_default_on_when_unset(self):
        code, out, _ = self._run(["config", "--kb-autocommit-only"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "1")

    def test_config_overrides_default(self):
        self._write_cfg("AGENTWARE_KB_AUTOCOMMIT=0\n")
        code, out, _ = self._run(["config", "--kb-autocommit-only"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "0")

    def test_env_overrides_config(self):
        self._write_cfg("AGENTWARE_KB_AUTOCOMMIT=0\n")
        self._set_env("1")
        code, out, _ = self._run(["config", "--kb-autocommit-only"])
        self.assertEqual(out.strip(), "1")
        # And the reverse: env off beats config on.
        self._write_cfg("AGENTWARE_KB_AUTOCOMMIT=1\n")
        self._set_env("0")
        _, out2, _ = self._run(["config", "--kb-autocommit-only"])
        self.assertEqual(out2.strip(), "0")

    def test_env_empty_falls_through_to_config(self):
        # An empty env var is treated as unset (does not force a value).
        self._write_cfg("AGENTWARE_KB_AUTOCOMMIT=0\n")
        self._set_env("")
        _, out, _ = self._run(["config", "--kb-autocommit-only"])
        self.assertEqual(out.strip(), "0")

    def test_unknown_value_is_on(self):
        # "anything that isn't an explicit off-token is ON".
        self._set_env("garbage")
        _, out, _ = self._run(["config", "--kb-autocommit-only"])
        self.assertEqual(out.strip(), "1")

    # --- persistence ----------------------------------------------------------
    def test_set_autocommit_off_persists_and_resolves(self):
        code, out, _ = self._run(["config", "--set-autocommit", "off"])
        self.assertEqual(code, 0, out)
        self.assertIn("AGENTWARE_KB_AUTOCOMMIT=0", self._read_cfg())
        # And resolving it back returns 0.
        _, out2, _ = self._run(["config", "--kb-autocommit-only"])
        self.assertEqual(out2.strip(), "0")

    def test_set_autocommit_on_persists_one(self):
        self._run(["config", "--set-autocommit", "yes"])
        self.assertIn("AGENTWARE_KB_AUTOCOMMIT=1", self._read_cfg())
        _, out, _ = self._run(["config", "--kb-autocommit-only"])
        self.assertEqual(out.strip(), "1")

    def test_set_autocommit_preserves_knowledge_dir(self):
        self._write_cfg("AGENTWARE_KNOWLEDGE_DIR=/tmp/kb-xyz\n")
        self._run(["config", "--set-autocommit", "off"])
        body = self._read_cfg()
        self.assertIn("AGENTWARE_KNOWLEDGE_DIR=/tmp/kb-xyz", body)
        self.assertIn("AGENTWARE_KB_AUTOCOMMIT=0", body)

    def test_set_autocommit_upserts_no_duplicates(self):
        self._run(["config", "--set-autocommit", "off"])
        self._run(["config", "--set-autocommit", "on"])
        body = self._read_cfg()
        self.assertEqual(body.count("AGENTWARE_KB_AUTOCOMMIT="), 1, body)
        self.assertIn("AGENTWARE_KB_AUTOCOMMIT=1", body)

    def test_set_autocommit_invalid_value_errors(self):
        code, _, err = self._run(["config", "--set-autocommit", "maybe"])
        self.assertEqual(code, 2)
        self.assertIn("invalid", err.lower())
        # Nothing persisted.
        self.assertFalse(os.path.isfile(self.cfg))

    def test_config_json_surfaces_kb_autocommit(self):
        self._write_cfg("AGENTWARE_KNOWLEDGE_DIR=%s\nAGENTWARE_KB_AUTOCOMMIT=0\n"
                        % self.home)
        code, out, _ = self._run(["config", "--format", "json"])
        payload = json.loads(out)
        self.assertEqual(payload["kb_autocommit"], "0")


class TestKbGitignoreScaffold(unittest.TestCase):
    """`_ensure_kb_gitignore` — KB .gitignore that excludes logs/ transcripts.

    Tests the helper directly (never invokes `init`, which would touch the real
    ~/.agentware/config.env).
    """

    def setUp(self):
        self.cli = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="agentware-gi-")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def _path(self):
        return os.path.join(self.kdir, ".gitignore")

    def _read(self):
        with open(self._path(), "r", encoding="utf-8") as f:
            return f.read()

    def test_creates_gitignore_with_logs_rule(self):
        action = self.cli._ensure_kb_gitignore(self.kdir)
        self.assertEqual(action, "created")
        self.assertTrue(os.path.isfile(self._path()))
        lines = [ln.strip() for ln in self._read().splitlines()]
        self.assertIn("logs/", lines)

    def test_idempotent_keeps_existing_logs_rule(self):
        self.cli._ensure_kb_gitignore(self.kdir)
        before = self._read()
        action = self.cli._ensure_kb_gitignore(self.kdir)
        self.assertEqual(action, "kept")
        self.assertEqual(self._read(), before)

    def test_appends_logs_rule_to_preexisting_gitignore(self):
        with open(self._path(), "w", encoding="utf-8") as f:
            f.write("*.tmp\n.DS_Store\n")
        action = self.cli._ensure_kb_gitignore(self.kdir)
        self.assertEqual(action, "appended")
        lines = [ln.strip() for ln in self._read().splitlines()]
        self.assertIn("logs/", lines)
        self.assertIn("*.tmp", lines)  # original content preserved

    def test_recognizes_bare_logs_without_trailing_slash(self):
        with open(self._path(), "w", encoding="utf-8") as f:
            f.write("logs\n")
        action = self.cli._ensure_kb_gitignore(self.kdir)
        self.assertEqual(action, "kept")


if __name__ == "__main__":
    import unittest
    unittest.main()
