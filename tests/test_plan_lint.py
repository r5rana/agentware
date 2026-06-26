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

Runner:  python3 -m unittest tests.test_plan_lint -v
"""

import json
import os
import re
import tempfile
import unittest

try:
    from tests._fixtures import run_cli as _raw_run_cli, load_cli, REPO_ROOT
except ImportError:  # when run via `discover -s tests`, tests/ is on sys.path
    from _fixtures import run_cli as _raw_run_cli, load_cli, REPO_ROOT


# --- Plan fixtures -----------------------------------------------------------
# A well-formed plan that satisfies EVERY rule R1–R9. Each bad fixture is a
# minimal mutation of this so a single broken invariant is isolated.

GOOD_PLAN = """\
# Plan — sample feature

> A throwaway plan used only by the linter tests.

## Tasks

- ⬜ **1** Implement the first thing deterministically.
  *Verify:* `python3 -m unittest tests.test_sample` passes.

- ⬜ **2** Update the knowledge base after building (`R-KB-*`).
  Run `scripts/agentware learn` then `scripts/agentware index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **3** [e2e] Run the end-to-end regression against the real loop.
  *Verify:* `./agentware.sh sample` aborts/passes as expected.

## Acceptance criteria

- [ ] The sample feature works end to end.

<promise>SAMPLE_FEATURE_COMPLETE</promise>
"""

# (b) R3 — GitHub-checkbox / lettered markers instead of `- ⬜ **N**`.
BAD_R3_CHECKBOXES = """\
# Plan — bad markers

## Tasks

- [ ] **T1** Implement the first thing.
  *Verify:* `python3 -m unittest` passes.

- [ ] **T2** Update the knowledge base (`R-KB`); run `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- [ ] **T3** [e2e] Run the end-to-end regression.
  *Verify:* loop run passes.

## Acceptance criteria

- [ ] Works.

<promise>BAD_MARKERS_COMPLETE</promise>
"""

# (c) R2 — missing the `## Acceptance criteria` section.
BAD_R2_MISSING_SECTION = """\
# Plan — missing section

## Tasks

- ⬜ **1** Implement the thing; update KB via `scripts/agentware learn`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** [e2e] End-to-end regression.
  *Verify:* loop passes.

<promise>MISSING_SECTION_COMPLETE</promise>
"""

# (d) R8 — no `<promise>` tag at all.
BAD_R8_NO_PROMISE = """\
# Plan — no promise

## Tasks

- ⬜ **1** Implement the thing; update KB via `index validate` and `learn`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.
"""

# (e) R4 — duplicate / non-monotonic task numbers (1, 1, 3 — gap + dup).
BAD_R4_NUMBERING = """\
# Plan — bad numbering

## Tasks

- ⬜ **1** Implement the thing; update KB via `learn` + `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **1** Duplicate number one.
  *Verify:* something is checked.

- ⬜ **3** [e2e] Gap to three; end-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>BAD_NUMBERING_COMPLETE</promise>
"""

# (f) R5 — a task with no `*Verify:*` line (task 2).
BAD_R5_NO_VERIFY = """\
# Plan — missing verify

## Tasks

- ⬜ **1** Implement the thing; update KB via `learn` + `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** This task has no verify line at all.

- ⬜ **3** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>MISSING_VERIFY_COMPLETE</promise>
"""

# (g) R6 — no task carries the `[e2e]` token.
BAD_R6_NO_E2E = """\
# Plan — no e2e task

## Tasks

- ⬜ **1** Implement the thing; update KB via `learn` + `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** Do another thing.
  *Verify:* a unit test passes.

## Acceptance criteria

- [ ] Works.

<promise>NO_E2E_COMPLETE</promise>
"""

# (h) R7 — no KB-update task (no R-KB / index validate / learn / features).
BAD_R7_NO_KB = """\
# Plan — no kb task

## Tasks

- ⬜ **1** Implement the thing.
  *Verify:* a unit test passes.

- ⬜ **2** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>NO_KB_COMPLETE</promise>
"""

# (i) An all-`✅` well-formed plan (every task complete) — still PASSES the lint.
GOOD_ALL_DONE = """\
# Plan — all done

## Tasks

- ✅ **1** Implemented the thing; updated KB via `learn` + `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ✅ **2** [e2e] End-to-end regression done.
  *Verify:* loop passed.

## Acceptance criteria

- [ ] Works.

<promise>ALL_DONE_COMPLETE</promise>
"""

# (j) R9 — blocking phrasing with NO deterministic resolution -> warn / strict-fail.
BAD_R9_BLOCKING = """\
# Plan — non-autonomous

## Tasks

- ⬜ **1** Implement the thing; update KB via `learn` + `index validate`.
  STOP and ask the operator which approach to take before proceeding.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>NON_AUTONOMOUS_COMPLETE</promise>
"""

# (j') R9 — same blocking phrasing but WITH a cited deterministic carve-out -> passes.
GOOD_R9_CARVEOUT = """\
# Plan — autonomous with carve-out

## Tasks

- ⬜ **1** Obtain self-extension confirmation (`R-PKG-03`).
  STOP and ask the operator before editing package files; the operator
  authoring + launching this plan is the explicit `R-PKG-03` confirmation, so
  proceed deterministically on that pre-decided pass rule. Update KB via
  `learn` + `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>CARVEOUT_COMPLETE</promise>
"""


# (k) Phased `**N.M**` markers (the documented form in docs/loop.md) — PASSES.
# Today's flat-only `_PLAN_TASK_LINE_RE` rejected these; Task 1 broadens it.
GOOD_PHASED = """\
# Plan — phased sample

> A phased plan used to prove the linter accepts `**N.M**` markers.

## Tasks

### Phase 1: Foundation

- ⬜ **1.1** Implement the first thing deterministically.
  *Verify:* `python3 -m unittest tests.test_sample` passes.

- ⬜ **1.2** Update the knowledge base after building (`R-KB-*`).
  Run `scripts/agentware learn` then `scripts/agentware index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

### Phase 2: Verification

- ⬜ **2.1** [e2e] Run the end-to-end regression against the real loop.
  *Verify:* `./agentware.sh sample` aborts/passes as expected.

## Acceptance criteria

- [ ] The phased feature works end to end.

<promise>PHASED_FEATURE_COMPLETE</promise>
"""

# (l) Ordinary PROSE bullets (`- **Word:** …`) inside the Tasks section must NOT
# be flagged as malformed task markers (the narrowed `_PLAN_LETTERED_RE`).
GOOD_PROSE_BULLETS = """\
# Plan — prose bullets inside tasks

## Tasks

- ⬜ **1** Implement the thing; update KB via `learn` + `index validate`.
  - **Note:** this sub-bullet is prose, not a task marker.
  - **Foundation first:** another prose bullet that must not trip R3.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>PROSE_BULLETS_COMPLETE</promise>
"""

# (m) Bare LETTERED task ids (`- **T1**`, no emoji) — the real mistake the loop's
# grep silently no-ops on; MUST still hard-fail R3.
BAD_R3_LETTERED = """\
# Plan — lettered task ids

## Tasks

- **T1** Lettered id instead of a number; update KB via `learn` + `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- **T2** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>LETTERED_IDS_COMPLETE</promise>
"""

# (n) Phased markers with a GAP in the minors (1.1 then 1.3) — MUST fail R4.
BAD_R4_PHASED_GAP = """\
# Plan — bad phased numbering

## Tasks

### Phase 1

- ⬜ **1.1** First; update KB via `learn` + `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **1.3** Gap in the minors (no 1.2).
  *Verify:* something is checked.

- ⬜ **2.1** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>BAD_PHASED_COMPLETE</promise>
"""


# --- Harness -----------------------------------------------------------------
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


# --- Task 4: loop <-> linter contract test -----------------------------------
class LoopLinterContractTestCase(unittest.TestCase):
    """Pin the canonical task-marker regex byte-identical across the loop and linter.

    The loop counts open task markers with a single `grep -cE '…'` in
    agentware.sh:open_markers(). The linter stores those exact POSIX-ERE bytes in
    `PLAN_TASK_MARKER_RE` (scripts/agentware) and derives a Python-usable form from
    them. If the two ever drift, a plan that lints clean could still count 0 open
    markers (the original bug). This test locates the grep pattern BY CONTENT (the
    `⬜|🟡` token, so it survives edits that shift line numbers) and asserts it is
    byte-identical to the stored constant.
    """

    AGENTWARE_SH = os.path.join(REPO_ROOT, "agentware.sh")

    def _extract_grep_marker_regex(self):
        with open(self.AGENTWARE_SH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        # Locate by content: the open-marker count uses `grep -cE '<pattern>'` and
        # the pattern is the only one containing the ⬜|🟡 alternation.
        candidates = [
            ln for ln in lines
            if "grep -cE" in ln and "⬜|🟡" in ln
        ]
        self.assertEqual(
            len(candidates), 1,
            "expected exactly one `grep -cE '…⬜|🟡…'` line in agentware.sh, "
            "found %d: %r" % (len(candidates), candidates),
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


if __name__ == "__main__":
    unittest.main()
