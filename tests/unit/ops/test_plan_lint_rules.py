"""LoopLinterContractTestCase, R11TargetPackagesTestCase, R14PremisesTestCase -- split from `test_plan_lint` (task 58).

See `tests.unit.ops._plan_lint_support` for the
shared header.
"""

from tests.unit.ops._plan_lint_support import (  # noqa: F401
    GOOD_PLAN, REPO_ROOT, _raw_run_cli, glob, json, load_cli, os, re,
    tempfile, unittest)


class LoopLinterContractTestCase(unittest.TestCase):
    """Pin the canonical task-marker regex byte-identical across the loop and linter.

    The loop counts open task markers with a single `grep -cE '…'` in
    open_markers(). The linter stores those exact POSIX-ERE bytes in
    `PLAN_TASK_MARKER_RE` (scripts/agentware) and derives a Python-usable form from
    them. If the two ever drift, a plan that lints clean could still count 0 open
    markers (the original bug). This test locates the grep pattern BY CONTENT (the
    `⬜|🟡` token, so it survives edits that shift line numbers) and asserts it is
    byte-identical to the stored constant.

    The search covers the WHOLE loop surface — the orchestrator plus every
    `scripts/loop/*.sh` — because plan task 33 split `agentware.sh` and
    `open_markers()` now lives in `scripts/loop/markers.sh`. Scanning only the
    orchestrator would find ZERO candidates and fail on a tree that is correct.
    The "exactly one" assertion is kept and is now TREE-WIDE, which is strictly
    stronger than before: it also forbids a second copy of the canonical grep
    appearing in any lib, which is exactly how the loop and linter would drift.
    """

    LOOP_SOURCES = [os.path.join(REPO_ROOT, "agentware.sh")] + sorted(
        glob.glob(os.path.join(REPO_ROOT, "scripts", "loop", "*.sh")))

    def _extract_grep_marker_regex(self):
        # Locate by content: the open-marker count uses `grep -cE '<pattern>'` and
        # the pattern is the only one containing the ⬜|🟡 alternation. (The ✅-only
        # tallies use a different pattern, and progress_signature uses `grep -nE`,
        # so neither collides with this probe.)
        candidates = []
        for path in self.LOOP_SOURCES:
            if not os.path.isfile(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                for ln in f.read().splitlines():
                    if "grep -cE" in ln and "⬜|🟡" in ln:
                        candidates.append(ln)
        self.assertEqual(
            len(candidates), 1,
            "expected exactly one `grep -cE '…⬜|🟡…'` line across the loop "
            "surface (agentware.sh + scripts/loop/*.sh), found %d: %r"
            % (len(candidates), candidates),
        )
        line = candidates[0]
        # Extract the single-quoted pattern: grep -cE '<pattern>'
        m = re.search(r"grep -cE '([^']*)'", line)
        self.assertIsNotNone(
            m, "could not extract the single-quoted grep pattern from: %r" % line)
        return m.group(1)

    def test_canonical_regex_is_byte_identical_to_loop(self):
        loop_regex = self._extract_grep_marker_regex()
        mod = load_cli()
        const = mod.PLAN_TASK_MARKER_RE
        self.assertEqual(
            loop_regex, const,
            "DRIFT: agentware.sh grep pattern (%r) != PLAN_TASK_MARKER_RE (%r). "
            "These MUST stay byte-identical so the loop and linter agree." % (
                loop_regex, const),
        )

    def test_canonical_regex_has_expected_posix_ere_shape(self):
        # Guard against a well-meaning edit that silently changes the contract on
        # BOTH sides (the byte-identity test alone wouldn't catch that).
        mod = load_cli()
        self.assertEqual(
            mod.PLAN_TASK_MARKER_RE,
            r"^[[:space:]]*-[[:space:]]*(⬜|🟡)[[:space:]]*\*\*[0-9]",
        )

class R11TargetPackagesTestCase(unittest.TestCase):
    """Tests for R11 — mandatory `## Target packages` section."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-r11-")
        self.addCleanup(self._cleanup)
        self.kdir = self.tmp

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _lint(self, plan_text, strict=False):
        path = os.path.join(self.tmp, "plan.md")
        with open(path, "w") as f:
            f.write(plan_text)
        args = ["plan", "lint", "--path", path, "--format", "json"]
        if strict:
            args.append("--strict")
        code, out, _err = _raw_run_cli(args, kdir=self.kdir)
        payload = json.loads(out) if out.strip() else {}
        return code, payload

    def test_r11_missing_section_fails(self):
        """A plan with NO `## Target packages` section FAILS R11."""
        plan = GOOD_PLAN.replace(
            "## Target packages\n\n- /tmp/test-target-dir\n\n", "")
        code, payload = self._lint(plan)
        self.assertEqual(code, 1)
        r11_errors = [e for e in payload.get("errors", [])
                      if e["rule"] == "R11"]
        self.assertTrue(len(r11_errors) >= 1,
                        "expected R11 error for missing section")
        self.assertIn("missing", r11_errors[0]["message"].lower())

    def test_r11_stub_only_fails(self):
        """A stub-TODO-only section (no absolute/~ path) FAILS R11."""
        plan = GOOD_PLAN.replace(
            "- /tmp/test-target-dir",
            "- TODO: replace with the absolute path")
        code, payload = self._lint(plan)
        self.assertEqual(code, 1)
        r11_errors = [e for e in payload.get("errors", [])
                      if e["rule"] == "R11"]
        self.assertTrue(len(r11_errors) >= 1,
                        "expected R11 error for stub-only section")

    def test_r11_valid_absolute_path_passes(self):
        """A section with an absolute path PASSES R11."""
        code, payload = self._lint(GOOD_PLAN)
        r11_errors = [e for e in payload.get("errors", [])
                      if e["rule"] == "R11"]
        self.assertEqual(len(r11_errors), 0,
                         "unexpected R11 error: %s" % r11_errors)

    def test_r11_tilde_path_passes(self):
        """A section with a ~-rooted path PASSES R11."""
        plan = GOOD_PLAN.replace(
            "- /tmp/test-target-dir",
            "- ~/workspace/agentware")
        code, payload = self._lint(plan)
        r11_errors = [e for e in payload.get("errors", [])
                      if e["rule"] == "R11"]
        self.assertEqual(len(r11_errors), 0,
                         "~ path should pass R11: %s" % r11_errors)

    def test_plan_target_dirs_extracts_paths(self):
        """plan_target_dirs() returns declared paths with ~ expanded."""
        mod = load_cli()
        dirs = mod.plan_target_dirs(GOOD_PLAN)
        self.assertEqual(dirs, ["/tmp/test-target-dir"])

    def test_plan_target_dirs_tilde_expansion(self):
        """plan_target_dirs() expands ~ to $HOME."""
        mod = load_cli()
        plan = GOOD_PLAN.replace(
            "- /tmp/test-target-dir",
            "- ~/workspace/agentware")
        dirs = mod.plan_target_dirs(plan)
        self.assertEqual(dirs, [os.path.expanduser("~/workspace/agentware")])

class R14PremisesTestCase(unittest.TestCase):
    """R14 — a LOAD-BEARING plan (self-extension OR names a live KB id) must carry
    a well-formed cited `## Premises` block (feature 260717-premise-grounding-gate).
    Drives lint_plan() directly to control the WARN->ERROR schema ramp precisely."""

    SELF_EXT = GOOD_PLAN.replace(
        "> A throwaway plan used only by the linter tests.\n",
        "> A throwaway plan used only by the linter tests.\n"
        "> Type: **self-extension (package change)**\n")

    PREMISES = ("## Premises\n\n"
                "- premise: a grounded fact\n"
                "  cite: work/x/RCA.md\n"
                "  check: cite-live\n\n"
                "---\n\n")

    def _lint(self, text, strict=False, schema=0, kdir=None):
        return load_cli().lint_plan(text, strict=strict, schema_version=schema,
                                    kdir=kdir)

    def _ramp(self):
        return load_cli()._PLAN_PREMISES_ERROR_SCHEMA

    def test_selfext_without_premises_warns_by_default(self):
        errors, warnings = self._lint(self.SELF_EXT, strict=False, schema=0)
        self.assertTrue(any(w["rule"] == "R14" for w in warnings))
        self.assertFalse(any(e["rule"] == "R14" for e in errors))

    def test_selfext_stays_warn_under_strict_below_ramp(self):
        # The strict pre-hook runs at the live schema (=1, below the ramp) so an
        # existing self-extension plan is never retroactively hard-failed.
        errors, warnings = self._lint(self.SELF_EXT, strict=True,
                                      schema=self._ramp() - 1)
        self.assertTrue(any(w["rule"] == "R14" for w in warnings))
        self.assertFalse(any(e["rule"] == "R14" for e in errors))

    def test_selfext_errors_under_strict_at_ramp(self):
        errors, _ = self._lint(self.SELF_EXT, strict=True, schema=self._ramp())
        self.assertTrue(any(e["rule"] == "R14" for e in errors))

    def test_selfext_with_premises_passes(self):
        text = self.SELF_EXT.replace("## Tasks", self.PREMISES + "## Tasks", 1)
        errors, warnings = self._lint(text, strict=True, schema=self._ramp())
        self.assertFalse(any(x["rule"] == "R14" for x in errors + warnings))

    def test_trivial_plan_is_noop(self):
        errors, warnings = self._lint(GOOD_PLAN, strict=True, schema=self._ramp())
        self.assertFalse(any(x["rule"] == "R14" for x in errors + warnings))

    def test_names_live_kb_id_is_load_bearing(self):
        import shutil
        from tests._fixtures import build_synthetic_kb
        kdir = tempfile.mkdtemp(prefix="agentware-r14-kb-")
        self.addCleanup(shutil.rmtree, kdir, True)
        build_synthetic_kb(kdir)
        # A NON-self-extension plan that references a live KB id is load-bearing (D6).
        text = GOOD_PLAN.replace(
            "Implement the first thing deterministically.",
            "Depends on learn-geofence-reminders behavior.")
        errors, warnings = self._lint(text, strict=False, schema=0, kdir=kdir)
        self.assertTrue(any(x["rule"] == "R14" for x in errors + warnings))

    def test_malformed_check_flagged(self):
        bad = ("## Premises\n\n- premise: x\n  cite: y\n"
               "  check: bogus-kind foo\n\n---\n\n")
        text = self.SELF_EXT.replace("## Tasks", bad + "## Tasks", 1)
        errors, warnings = self._lint(text, strict=True, schema=self._ramp())
        self.assertTrue(any(x["rule"] == "R14" for x in errors + warnings))
