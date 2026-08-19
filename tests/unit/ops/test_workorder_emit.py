"""Workorder emitter + lint tests (feature 260710-plan-state-machine, Task 2).

Covers `plan new --auto` (work/auto/<slug>/ namespace, provenance headers,
knowledge-dir target seed, the g1/g3 emit-time governance guards), the R12
provenance-required lint rule with its schema-gated WARN->error flip, the
`plan lint --readiness` mode, and the R13 status-reality binding.

All test names are prefixed `test_workorder_` so a `-k test_workorder` gate
matches these and nothing pre-existing.

Runner: python3 -m unittest tests.unit.ops.test_workorder_emit -v
"""

import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import run_cli as _raw_run_cli, build_synthetic_kb
except ImportError:
    from _fixtures import run_cli as _raw_run_cli, build_synthetic_kb


class WorkorderTestCase(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-workorder-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)

    def run_cli(self, argv):
        try:
            return _raw_run_cli(argv, self.kdir)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            return code, "", "SystemExit(%r)" % (exc.code,)

    def auto_path(self, slug):
        return os.path.join(self.kdir, "work", "auto", slug, "plan.md")

    def read_auto(self, slug):
        with open(self.auto_path(slug), "r", encoding="utf-8") as f:
            return f.read()

    def emit(self, slug, *extra):
        argv = ["plan", "new", slug, "--auto", "--generated-by", "selftest",
                "--trigger", "audit-fixture", "--title", "wo"] + list(extra)
        return self.run_cli(argv)

    def set_schema(self, n):
        with open(os.path.join(self.kdir, ".schema"), "w", encoding="utf-8") as f:
            f.write("%d\n" % n)

    # --- emitter ------------------------------------------------------------
    def test_workorder_emit_creates_auto_path_with_provenance(self):
        code, _o, err = self.emit("990101-wo-selftest")
        self.assertEqual(code, 0, err)
        text = self.read_auto("990101-wo-selftest")
        self.assertIn("> Origin: auto\n", text)
        self.assertIn("> Generated-by: selftest\n", text)
        self.assertIn("> Trigger: audit-fixture\n", text)
        self.assertIn("> Feature: `auto/990101-wo-selftest`\n", text)

    def test_workorder_default_target_is_knowledge_dir(self):
        self.emit("990101-wo-tgt")
        text = self.read_auto("990101-wo-tgt")
        self.assertIn("- %s" % self.kdir, text)

    def test_workorder_evidence_header_optional(self):
        self.emit("990101-wo-ev", "--evidence", "logs/a.log,logs/b.log")
        self.assertIn("> Evidence: logs/a.log,logs/b.log\n",
                      self.read_auto("990101-wo-ev"))

    def test_workorder_slash_in_slug_refused(self):
        code, _o, err = self.emit("auto/990101-x")
        self.assertEqual(code, 1)
        self.assertIn("must not contain '/'", err.lower())

    def test_workorder_missing_provenance_refused(self):
        code, _o, err = self.run_cli(
            ["plan", "new", "990101-x", "--auto", "--title", "wo"])
        self.assertEqual(code, 1)
        self.assertIn("--generated-by", err)

    # --- g1: kernel-path refusal --------------------------------------------
    def test_workorder_g1_package_root_target_refused(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(
            __import__("tests._fixtures", fromlist=["_"]).__file__)))
        code, _o, err = self.emit("990101-evil", "--target", repo)
        self.assertEqual(code, 1)
        self.assertIn("g1", err.lower())

    def test_workorder_g1_kernel_token_target_refused(self):
        code, _o, err = self.emit(
            "990101-evil2", "--target", "/tmp/x/scripts/agentware")
        self.assertEqual(code, 1)
        self.assertIn("g1", err.lower())

    def test_workorder_g1_safe_target_allowed(self):
        code, _o, err = self.emit("990101-safe", "--target", "/tmp/somewhere")
        self.assertEqual(code, 0, err)
        self.assertIn("- /tmp/somewhere", self.read_auto("990101-safe"))

    # --- g3: concurrent draft/ready cap -------------------------------------
    def test_workorder_g3_draft_cap(self):
        for i in range(5):
            code, _o, err = self.emit("99010%d-cap" % i)
            self.assertEqual(code, 0, err)
        code, _o, err = self.emit("990199-over")
        self.assertEqual(code, 1)
        self.assertIn("cap", err.lower())

    # --- R12 provenance-required (schema-gated severity) --------------------
    def _strip_provenance(self, slug):
        p = self.auto_path(slug)
        text = self.read_auto(slug)
        kept = [ln for ln in text.splitlines()
                if not ln.startswith(("> Origin:", "> Generated-by:",
                                      "> Trigger:"))]
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(kept) + "\n")

    def test_workorder_r12_warn_below_schema1_passes_lint(self):
        self.emit("990101-r12")
        self._strip_provenance("990101-r12")
        self.set_schema(0)
        code, out, _e = self.run_cli(
            ["plan", "lint", "--path", self.auto_path("990101-r12")])
        self.assertEqual(code, 0)          # WARN only -> lint passes
        self.assertIn("R12", out)          # but a warning is surfaced

    def test_workorder_r12_error_at_schema1_fails_lint(self):
        self.emit("990101-r12e")
        self._strip_provenance("990101-r12e")
        self.set_schema(1)
        code, out, _e = self.run_cli(
            ["plan", "lint", "--path", self.auto_path("990101-r12e")])
        self.assertEqual(code, 1)          # ERROR -> lint fails
        self.assertIn("R12", out)

    def test_workorder_r12_compliant_passes_at_schema1(self):
        self.emit("990101-ok")
        self.set_schema(1)
        code, _o, _e = self.run_cli(
            ["plan", "lint", "--path", self.auto_path("990101-ok")])
        self.assertEqual(code, 0)          # has provenance -> passes even at >=1

    def test_workorder_r12_slug_convention_warns(self):
        self.emit("notdate-slug")
        code, out, _e = self.run_cli(
            ["plan", "lint", "--path", self.auto_path("notdate-slug")])
        self.assertEqual(code, 0)
        self.assertIn("YYMMDD", out)

    # --- readiness mode -----------------------------------------------------
    def test_workorder_readiness_fails_on_skeleton(self):
        self.emit("990101-ready")
        code, _o, _e = self.run_cli(
            ["plan", "lint", "--path", self.auto_path("990101-ready"),
             "--readiness"])
        self.assertEqual(code, 1)

    def test_workorder_readiness_passes_when_filled(self):
        self.emit("990101-fill")
        p = self.auto_path("990101-fill")
        text = self.read_auto("990101-fill")
        text = text.replace(
            "_TODO: problem, goal, design, constraints, environment, pitfalls._",
            "Real context.")
        text = text.replace(
            "  *Verify:* TODO — exercise the feature end-to-end and capture the "
            "outcome.", "  *Verify:* run it and observe output.")
        text = text.replace(
            "- [ ] TODO: define the verifiable acceptance criteria.",
            "- [ ] It works end to end.")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        code, _o, err = self.run_cli(
            ["plan", "lint", "--path", p, "--readiness"])
        self.assertEqual(code, 0, err)

    # --- R13 status-reality binding -----------------------------------------
    def test_workorder_r13_duplicate_status_errors(self):
        self.emit("990101-dup")
        p = self.auto_path("990101-dup")
        text = self.read_auto("990101-dup").replace(
            "> Status: draft", "> Status: draft\n> Status: running", 1)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        code, out, _e = self.run_cli(["plan", "lint", "--path", p])
        self.assertEqual(code, 1)
        self.assertIn("R13", out)

    def test_workorder_r13_done_with_open_markers_strict_errors(self):
        self.emit("990101-done")
        p = self.auto_path("990101-done")
        text = self.read_auto("990101-done").replace(
            "> Status: draft", "> Status: done")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        code, out, _e = self.run_cli(["plan", "lint", "--path", p, "--strict"])
        self.assertEqual(code, 1)
        self.assertIn("R13", out)


if __name__ == "__main__":
    unittest.main()
