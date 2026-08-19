"""Tests for the deterministic `plan lint` gate (feature 260625-plan-lint-gate).

`plan lint --path <plan.md>` asserts a fixed set of STRUCTURAL invariants on a
feature plan (NOT prose/wording — that is the pre-phase agent's job). Two
severities: hard fail (non-zero exit) and warn (printed, exit 0). The R9
autonomy rule is warn by default and hard-fail under `--strict`.

Rules under test:
  R1 file        — file exists/readable
  R2 sections    — `## Tasks` AND `## Acceptance criteria` present
  R3 markers     — >=1 well-formed `- ⬜/🟡/✅ **<digit>**` marker; flag
                   `- [ ]` / `**<letter>` style with a specific fix hint
  R4 numbering   — task numbers monotonic from 1 (no dups/gaps)
  R5 verify      — every task has a `*Verify:*` line
  R6 e2e         — >=1 task carries the `[e2e]` token with a non-empty Verify
  R7 kb-update   — >=1 task references a KB update (R-KB / index validate /
                   learn / features)
  R8 promise     — exactly one `<promise>CONTENT</promise>` tag
  R9 autonomy    — flag blocking phrasing without a deterministic resolution
                   (warn; hard-fail under --strict)

These are stdlib `unittest` only and run the REAL CLI in-process. They are
written RED-first: until `cmd_plan_lint` + the `plan` subparser exist, the CLI
raises SystemExit(2) on the unknown subcommand and every assertion fails.

Runner:  python3 -m unittest tests.unit.ops.test_plan_lint -v
"""

from tests.unit.ops._plan_lint_support import (  # noqa: F401
    BAD_R2_MISSING_SECTION, BAD_R3_CHECKBOXES, BAD_R3_LETTERED,
    BAD_R4_NUMBERING, BAD_R4_PHASED_GAP, BAD_R5_NO_VERIFY, BAD_R6_NO_E2E,
    BAD_R7_NO_KB, BAD_R8_NO_PROMISE, BAD_R9_BLOCKING, GOOD_ALL_DONE,
    GOOD_PHASED, GOOD_PLAN, GOOD_PROSE_BULLETS, GOOD_R9_CARVEOUT,
    _raw_run_cli, json, os, tempfile, unittest)


class PlanLintTestCase(unittest.TestCase):
    """Drives the real CLI `plan lint` against plan files written to a tempdir.

    `plan lint` is KB-independent (it lints an arbitrary file), but run_cli
    needs a knowledge dir for env wiring; a throwaway tempdir suffices.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-planlint-")
        self.addCleanup(self._cleanup)
        self.kdir = self.tmp  # throwaway; plan lint never reads the KB

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_plan(self, text, name="plan.md"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def lint(self, text_or_path, *extra, as_json=False):
        """Run `plan lint --path <p> [extra...]`; return (code, out, err).

        Catches SystemExit (argparse raises it on an unknown subcommand during
        the RED phase, or on a usage error) and surfaces its code so tests
        FAIL (assert mismatch) rather than ERROR.
        """
        s = str(text_or_path)
        # A path-like argument (ends in .md, single line) is used verbatim so the
        # R1 missing-file test can point at a non-existent path; everything else
        # is plan TEXT written to a temp file.
        if s.endswith(".md") and "\n" not in s:
            path = s
        else:
            path = self.write_plan(text_or_path)
        argv = ["plan", "lint", "--path", path]
        if as_json:
            argv += ["--format", "json"]
        argv += list(extra)
        try:
            return _raw_run_cli(argv, self.kdir)
        except SystemExit as exc:  # argparse usage error / unknown subcommand
            code = exc.code if isinstance(exc.code, int) else 1
            return code, "", "SystemExit(%r)" % (exc.code,)

    def errors_of(self, text, *extra):
        code, out, _ = self.lint(text, *extra, as_json=True)
        payload = json.loads(out)
        return code, payload

    # --- (a) good plan passes ----------------------------------------------
    def test_good_plan_passes(self):
        code, out, err = self.lint(GOOD_PLAN)
        self.assertEqual(code, 0, "good plan should pass: %s %s" % (out, err))

    def test_good_plan_json_ok_true_empty_errors(self):
        code, payload = self.errors_of(GOOD_PLAN)
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["errors"], [])
        self.assertIn("warnings", payload)

    # --- (b) R3 markers -----------------------------------------------------
    def test_r3_checkbox_markers_fail_with_hint(self):
        code, out, err = self.lint(BAD_R3_CHECKBOXES)
        self.assertNotEqual(code, 0)
        blob = (out + err)
        self.assertIn("R3", blob)
        # The actionable fix hint shows the expected marker shape.
        self.assertIn("- ⬜ **N**", blob)

    def test_r3_json_reports_rule(self):
        code, payload = self.errors_of(BAD_R3_CHECKBOXES)
        self.assertNotEqual(code, 0)
        self.assertFalse(payload["ok"])
        self.assertTrue(any(e["rule"] == "R3" for e in payload["errors"]),
                        payload["errors"])
        # Offending line numbers are named.
        self.assertTrue(all("line" in e for e in payload["errors"]))

    # --- (c) R2 sections ----------------------------------------------------
    def test_r2_missing_section_fails(self):
        code, payload = self.errors_of(BAD_R2_MISSING_SECTION)
        self.assertNotEqual(code, 0)
        self.assertTrue(any(e["rule"] == "R2" for e in payload["errors"]),
                        payload["errors"])

    # --- (d) R8 promise -----------------------------------------------------
    def test_r8_missing_promise_fails(self):
        code, payload = self.errors_of(BAD_R8_NO_PROMISE)
        self.assertNotEqual(code, 0)
        self.assertTrue(any(e["rule"] == "R8" for e in payload["errors"]),
                        payload["errors"])

    # --- (e) R4 numbering ---------------------------------------------------
    def test_r4_bad_numbering_fails(self):
        code, payload = self.errors_of(BAD_R4_NUMBERING)
        self.assertNotEqual(code, 0)
        self.assertTrue(any(e["rule"] == "R4" for e in payload["errors"]),
                        payload["errors"])

    # --- (f) R5 per-task verify --------------------------------------------
    def test_r5_missing_verify_fails(self):
        code, payload = self.errors_of(BAD_R5_NO_VERIFY)
        self.assertNotEqual(code, 0)
        self.assertTrue(any(e["rule"] == "R5" for e in payload["errors"]),
                        payload["errors"])

    # --- (g) R6 e2e task ----------------------------------------------------
    def test_r6_missing_e2e_fails(self):
        code, payload = self.errors_of(BAD_R6_NO_E2E)
        self.assertNotEqual(code, 0)
        self.assertTrue(any(e["rule"] == "R6" for e in payload["errors"]),
                        payload["errors"])

    # --- (h) R7 kb-update task ----------------------------------------------
    def test_r7_missing_kb_task_fails(self):
        code, payload = self.errors_of(BAD_R7_NO_KB)
        self.assertNotEqual(code, 0)
        self.assertTrue(any(e["rule"] == "R7" for e in payload["errors"]),
                        payload["errors"])

    # --- (i) all-done plan still passes -------------------------------------
    def test_all_done_plan_passes(self):
        code, out, err = self.lint(GOOD_ALL_DONE)
        self.assertEqual(code, 0, "%s %s" % (out, err))

    # --- (j) R9 autonomy: warn by default, hard-fail under --strict ---------
    def test_r9_blocking_is_warning_by_default(self):
        # Default: exit 0, but a warning is recorded.
        code, payload = self.errors_of(BAD_R9_BLOCKING)
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["ok"])
        self.assertTrue(any(w["rule"] == "R9" for w in payload["warnings"]),
                        payload["warnings"])

    def test_r9_blocking_hard_fails_under_strict(self):
        code, payload = self.errors_of(BAD_R9_BLOCKING, "--strict")
        self.assertNotEqual(code, 0)
        self.assertFalse(payload["ok"])
        self.assertTrue(any(e["rule"] == "R9" for e in payload["errors"]),
                        payload["errors"])

    def test_r9_carveout_passes_even_under_strict(self):
        code, payload = self.errors_of(GOOD_R9_CARVEOUT, "--strict")
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["ok"])
        self.assertFalse(any(e["rule"] == "R9" for e in payload["errors"]),
                         payload["errors"])
        self.assertFalse(any(w["rule"] == "R9" for w in payload["warnings"]),
                         payload["warnings"])

    # --- (k) phased `**N.M**` markers lint clean (Task 1 compat) -----------
    def test_phased_markers_pass(self):
        code, out, err = self.lint(GOOD_PHASED)
        self.assertEqual(code, 0, "phased plan should pass: %s %s" % (out, err))

    def test_phased_markers_json_ok_true(self):
        code, payload = self.errors_of(GOOD_PHASED)
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["errors"], [])

    # --- (l) prose bullets inside Tasks are NOT flagged --------------------
    def test_prose_bullets_not_flagged(self):
        code, payload = self.errors_of(GOOD_PROSE_BULLETS)
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["ok"])
        self.assertFalse(any(e["rule"] == "R3" for e in payload["errors"]),
                         payload["errors"])

    # --- (m) bare lettered task ids STILL fail R3 (regression guard) -------
    def test_r3_lettered_task_ids_fail(self):
        code, payload = self.errors_of(BAD_R3_LETTERED)
        self.assertNotEqual(code, 0)
        self.assertFalse(payload["ok"])
        self.assertTrue(any(e["rule"] == "R3" for e in payload["errors"]),
                        payload["errors"])

    # --- (n) phased numbering with a gap fails R4 --------------------------
    def test_r4_phased_gap_fails(self):
        code, payload = self.errors_of(BAD_R4_PHASED_GAP)
        self.assertNotEqual(code, 0)
        self.assertTrue(any(e["rule"] == "R4" for e in payload["errors"]),
                        payload["errors"])

    # --- R1 file ------------------------------------------------------------
    def test_r1_missing_file_fails(self):
        missing = os.path.join(self.tmp, "does-not-exist.md")
        code, out, err = self.lint(missing)
        self.assertNotEqual(code, 0)
        self.assertIn("R1", (out + err))
