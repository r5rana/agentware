"""Tests for the `scripts/agentware whereami` subcommand.

Runner:  python3 -m unittest tests.unit.cli.test_whereami -v
   or:   python3 -m unittest discover -s tests -v

Covers:
- Given a synthetic temp tree with a language-neutral project marker (a manifest
  under a VCS checkout), whereami resolves the project + repo root correctly
- Outside any project -> empty JSON, exit 0
- Deterministic (same inputs -> same output)
- Session-start injection is no-op when AGENTWARE_INVOKED_FROM unset
"""

import json
import os
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, run_cli, REPO_ROOT
except ImportError:
    from _fixtures import load_cli, run_cli, REPO_ROOT


class TestWhereami(unittest.TestCase):
    """Unit tests for the whereami subcommand."""

    def setUp(self):
        """Create a synthetic, build-system-neutral project tree in a temp dir.

        Layout:  <tmpdir>/myrepo/.git/                        (VCS marker)
                 <tmpdir>/myrepo/services/webapp/package.json  (project marker)
                 <tmpdir>/myrepo/services/webapp/src/components (nested subdir)
        """
        self.tmpdir = tempfile.mkdtemp(prefix="aw_whereami_")
        self.repo_root = os.path.join(self.tmpdir, "myrepo")
        # VCS marker pins the repo root.
        os.makedirs(os.path.join(self.repo_root, ".git"))
        # An ecosystem manifest pins the enclosing project.
        self.project_dir = os.path.join(self.repo_root, "services", "webapp")
        os.makedirs(self.project_dir)
        with open(os.path.join(self.project_dir, "package.json"), "w") as f:
            f.write('{"name": "webapp"}\n')
        # A nested subdirectory inside the project.
        self.subdir = os.path.join(self.project_dir, "src", "components")
        os.makedirs(self.subdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_whereami(self, dir_path):
        """Run whereami via the CLI module with the given --dir."""
        mod = load_cli()
        import io
        import contextlib
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main(["whereami", "--dir", dir_path, "--format", "json"])
        return code, out.getvalue(), err.getvalue()

    def test_resolves_project_from_project_root(self):
        """When invoked from the project root, resolves project + repo root."""
        code, out, _ = self._run_whereami(self.project_dir)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["project_name"], "webapp")
        self.assertEqual(data["project_dir"], self.project_dir)
        self.assertEqual(data["repo_root"], self.repo_root)
        self.assertEqual(data["invoked_from"], self.project_dir)

    def test_resolves_project_from_subdirectory(self):
        """When invoked from a subdirectory inside a project, resolves the project."""
        code, out, _ = self._run_whereami(self.subdir)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["project_name"], "webapp")
        self.assertEqual(data["project_dir"], self.project_dir)
        self.assertEqual(data["repo_root"], self.repo_root)
        self.assertEqual(data["invoked_from"], self.subdir)

    def test_outside_project_returns_empty(self):
        """When not inside any project, returns empty fields with exit 0."""
        # Use the tmpdir itself (no manifest or VCS marker at or above it)
        code, out, _ = self._run_whereami(self.tmpdir)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["project_name"], "")
        self.assertEqual(data["project_dir"], "")
        self.assertEqual(data["repo_root"], "")
        self.assertEqual(data["invoked_from"], self.tmpdir)

    def test_never_errors_on_nonexistent_path(self):
        """Even with a nonexistent path, returns exit 0 with empty fields."""
        fake_path = os.path.join(self.tmpdir, "does", "not", "exist")
        code, out, _ = self._run_whereami(fake_path)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["project_name"], "")
        self.assertEqual(data["invoked_from"], fake_path)

    def test_deterministic_same_inputs_same_output(self):
        """Same input produces byte-identical output across calls."""
        _, out1, _ = self._run_whereami(self.project_dir)
        _, out2, _ = self._run_whereami(self.project_dir)
        self.assertEqual(out1, out2)

    def test_uses_env_var_fallback(self):
        """When --dir is not provided, falls back to AGENTWARE_INVOKED_FROM env."""
        mod = load_cli()
        import io
        import contextlib
        prev = os.environ.get("AGENTWARE_INVOKED_FROM")
        os.environ["AGENTWARE_INVOKED_FROM"] = self.project_dir
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = mod.main(["whereami", "--format", "json"])
            data = json.loads(out.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(data["project_name"], "webapp")
        finally:
            if prev is None:
                os.environ.pop("AGENTWARE_INVOKED_FROM", None)
            else:
                os.environ["AGENTWARE_INVOKED_FROM"] = prev

    def test_repo_root_with_multiple_projects(self):
        """With multiple projects under the repo, resolves only the one we are in."""
        # Add another project under the same repo
        other_project = os.path.join(self.repo_root, "services", "api")
        os.makedirs(other_project)
        with open(os.path.join(other_project, "package.json"), "w") as f:
            f.write('{"name": "api"}\n')

        code, out, _ = self._run_whereami(self.project_dir)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["project_name"], "webapp")

    def test_bare_vcs_checkout_resolves_repo_as_project(self):
        """A VCS checkout with no ecosystem manifest still resolves (repo == project)."""
        code, out, _ = self._run_whereami(self.repo_root)
        self.assertEqual(code, 0)
        data = json.loads(out)
        # repo_root has a .git but no manifest of its own -> repo doubles as project
        self.assertEqual(data["repo_root"], self.repo_root)
        self.assertEqual(data["project_dir"], self.repo_root)
        self.assertEqual(data["project_name"], "myrepo")


class TestSessionStartNoOp(unittest.TestCase):
    """Test that session-start injection is no-op when AGENTWARE_INVOKED_FROM unset."""

    def test_session_start_noop_without_env(self):
        """session-start.sh does not inject AGENTWARE_INVOKED_FROM context when unset."""
        hook_path = os.path.join(REPO_ROOT, "scripts", "hooks", "session-start.sh")
        # Run with AGENTWARE_INVOKED_FROM explicitly unset
        env = os.environ.copy()
        env.pop("AGENTWARE_INVOKED_FROM", None)
        result = subprocess.run(
            ["bash", hook_path],
            input="{}",
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        # The output should NOT contain AGENTWARE_INVOKED_FROM context block
        self.assertNotIn("AGENTWARE_INVOKED_FROM:", result.stdout)


if __name__ == "__main__":
    unittest.main()
