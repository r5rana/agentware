"""Tests for the deterministic plan-authoring emitter (feature
260625-plan-authoring-toolkit).

The emitter is the *generation* half of the generation/enforcement split: the
LLM planner decides WHAT the tasks are; these three commands guarantee the FORM
(markers, numbering, required sections, promise tag) by construction so a plan
can never drift from what `plan lint` (the enforcement half) accepts.

Commands under test:
  plan new <feature> --title "<t>" [--max-iterations N] [--self-extension]
                     [--force]
      Scaffold <knowledge-dir>/work/<feature>/plan.md with the canonical
      skeleton + a deterministically derived <promise> tag. The `## Tasks`
      section is NEVER empty: it SEEDS a mandatory penultimate `[e2e]` task and
      a mandatory final `[kb]` knowledge-base-update task. Refuses to overwrite
      an existing plan unless --force.
  plan add-task <feature> "<desc>" [--verify "<cmd>"] [--e2e] [--kb]
      INSERT a task immediately BEFORE the mandatory trailing `[e2e]`+`[kb]`
      pair, then RENUMBER all tasks 1..N so the pair stays last and numbering
      stays monotonic. Always emits `- ⬜ **N** <desc>` + an indented
      `*Verify:*` line.
  plan set-status <feature> <N> todo|wip|done
      Flip task N's marker (⬜↔🟡↔✅) in place, touching only that line.

Written RED-first: until the three `cmd_plan_*` funcs + their subparsers exist,
the CLI raises SystemExit(2) on the unknown subcommand and every assertion
fails.

Runner:  python3 -m unittest tests.test_plan_authoring -v
"""

import os
import re
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import run_cli as _raw_run_cli, build_synthetic_kb
except ImportError:  # when run via `discover -s tests`, tests/ is on sys.path
    from _fixtures import run_cli as _raw_run_cli, build_synthetic_kb


# Canonical any-status well-formed task marker (mirrors _PLAN_TASK_LINE_RE in
# the CLI). Captures the status glyph and the task number so tests can assert
# numbering/order without coupling to prose.
TASK_LINE_RE = re.compile(r"^\s*-\s*(⬜|🟡|✅)\s*\*\*(\d+)\*\*(.*)$")


class PlanAuthoringTestCase(unittest.TestCase):
    """Drives the real CLI `plan new|add-task|set-status` against a synthetic KB.

    The emitter writes to <kdir>/work/<feature>/plan.md, so a synthetic KB dir
    wired via AGENTWARE_KNOWLEDGE_DIR is all that is needed (NEVER the operator's
    real KB; NEVER `agentware init`).
    """

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-planauthor-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)

    # --- helpers ------------------------------------------------------------
    def run_plan(self, argv):
        """Run a `plan …` argv; catch argparse SystemExit so RED-phase unknown
        subcommands surface as a (code, out, err) tuple and tests FAIL (assert
        mismatch) rather than ERROR."""
        try:
            return _raw_run_cli(argv, self.kdir)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            return code, "", "SystemExit(%r)" % (exc.code,)

    def plan_path(self, feature):
        return os.path.join(self.kdir, "work", feature, "plan.md")

    def read_plan(self, feature):
        with open(self.plan_path(feature), "r", encoding="utf-8") as f:
            return f.read()

    def tasks(self, feature):
        """Return [(status_glyph, number_int, rest_of_line), …] in file order."""
        out = []
        for ln in self.read_plan(feature).splitlines():
            m = TASK_LINE_RE.match(ln)
            if m:
                out.append((m.group(1), int(m.group(2)), m.group(3)))
        return out

    def new_plan(self, feature="demo", title="Demo Feature", *extra):
        argv = ["plan", "new", feature, "--title", title] + list(extra)
        return self.run_plan(argv)

    # --- (a) plan new: skeleton + promise + mandatory seed pair -------------
    def test_new_writes_skeleton_with_sections_and_promise(self):
        code, out, err = self.new_plan("demo", "Demo Feature")
        self.assertEqual(code, 0, "plan new should succeed: %s %s" % (out, err))
        self.assertTrue(os.path.isfile(self.plan_path("demo")),
                        "plan new must write <kdir>/work/<feature>/plan.md")
        text = self.read_plan("demo")
        # Required R2 headings present, byte-exact.
        self.assertIn("## Tasks", text)
        self.assertIn("## Acceptance criteria", text)
        # Exactly one promise tag (R8), derived deterministically from feature.
        promises = re.findall(r"<promise>([A-Z0-9_]+)</promise>", text)
        self.assertEqual(promises, ["DEMO_COMPLETE"],
                         "promise must derive UPPER_SNAKE_COMPLETE from feature")

    def test_promise_derivation_from_hyphenated_feature(self):
        code, _, _ = self.new_plan("260625-plan-authoring-toolkit", "T")
        text = self.read_plan("260625-plan-authoring-toolkit")
        self.assertIn("<promise>260625_PLAN_AUTHORING_TOOLKIT_COMPLETE</promise>",
                      text)

    def test_new_seeds_mandatory_e2e_and_kb_trailing_pair(self):
        # A freshly scaffolded plan — BEFORE any add-task — already contains
        # exactly one [e2e] task and one [kb] task as the trailing pair.
        self.new_plan("demo", "Demo Feature")
        text = self.read_plan("demo")
        ts = self.tasks("demo")
        self.assertGreaterEqual(len(ts), 2,
                                "plan new must seed >=2 tasks (e2e + kb)")
        # Exactly one [e2e] token across the seeded plan.
        self.assertEqual(text.count("[e2e]"), 1, "exactly one seeded [e2e] task")
        self.assertEqual(text.count("[kb]"), 1, "exactly one seeded [kb] task")
        # The [e2e] task is penultimate and the [kb] task is last.
        e2e_lines = [n for (_, n, rest) in ts if "[e2e]" in rest]
        kb_lines = [n for (_, n, rest) in ts if "[kb]" in rest]
        self.assertEqual(len(e2e_lines), 1)
        self.assertEqual(len(kb_lines), 1)
        last_two = [n for (_, n, _) in ts][-2:]
        self.assertEqual([e2e_lines[0], kb_lines[0]], last_two,
                         "[e2e] then [kb] must be the last two tasks")
        # The seeded [kb] task must embed an R7 token so a fresh plan passes R7.
        kb_block = self._task_block(text, kb_lines[0])
        self.assertTrue(
            any(tok in kb_block for tok in ("R-KB", "index validate",
                                            "learn", "features")),
            "seeded [kb] task must embed an R7 token (R-KB/index validate/"
            "learn/features): %r" % kb_block)

    def _task_block(self, text, number):
        """Return the lines of task `number` (its marker line + continuation)."""
        lines = text.splitlines()
        block, capturing = [], False
        for ln in lines:
            m = TASK_LINE_RE.match(ln)
            if m:
                if int(m.group(2)) == number:
                    capturing = True
                    block = [ln]
                    continue
                elif capturing:
                    break
            elif capturing:
                if ln.startswith("## "):
                    break
                block.append(ln)
        return "\n".join(block)

    # --- (f) refuse-overwrite without --force -------------------------------
    def test_new_refuses_overwrite_without_force(self):
        self.new_plan("demo", "Demo Feature")
        before = self.read_plan("demo")
        code, out, err = self.new_plan("demo", "Demo Feature")
        self.assertNotEqual(code, 0,
                            "plan new must refuse to overwrite an existing plan")
        self.assertEqual(self.read_plan("demo"), before,
                         "refused plan new must not modify the existing file")

    def test_new_force_overwrites(self):
        self.new_plan("demo", "Demo Feature")
        code, out, err = self.new_plan("demo", "Other Title", "--force")
        self.assertEqual(code, 0, "%s %s" % (out, err))
        self.assertTrue(os.path.isfile(self.plan_path("demo")))

    # --- (b) add-task inserts BEFORE the trailing pair ----------------------
    def test_add_task_inserts_before_trailing_pair(self):
        self.new_plan("demo", "Demo Feature")
        code, out, err = self.run_plan(
            ["plan", "add-task", "demo", "Implement the core thing",
             "--verify", "python3 -m unittest tests.test_core"])
        self.assertEqual(code, 0, "%s %s" % (out, err))
        ts = self.tasks("demo")
        # The new substantive task is now task 1; e2e/kb remain the last two.
        rests = [rest for (_, _, rest) in ts]
        self.assertIn("Implement the core thing", rests[0])
        self.assertIn("[e2e]", rests[-2])
        self.assertIn("[kb]", rests[-1])
        # Its emitted Verify line is present and non-empty.
        block = self._task_block(self.read_plan("demo"), ts[0][1])
        self.assertRegex(block, r"\*Verify:\*\s*\S")

    def test_add_task_emits_open_marker_with_number_and_verify(self):
        self.new_plan("demo", "Demo Feature")
        self.run_plan(["plan", "add-task", "demo", "Do a thing"])
        text = self.read_plan("demo")
        # Always a `- ⬜ **N** …` open marker (never `- [ ]` / lettered).
        self.assertNotIn("- [ ]", text.split("## Acceptance")[0])
        self.assertRegex(text, r"- ⬜ \*\*1\*\* Do a thing")

    # --- (c) numbering stays monotonic 1..N after several inserts -----------
    def test_numbering_monotonic_after_several_inserts(self):
        self.new_plan("demo", "Demo Feature")
        for i in range(1, 4):
            code, out, err = self.run_plan(
                ["plan", "add-task", "demo", "Task body number %d" % i])
            self.assertEqual(code, 0, "%s %s" % (out, err))
        numbers = [n for (_, n, _) in self.tasks("demo")]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)),
                         "task numbers must be monotonic 1..N with no gaps/dups")
        # 3 inserts + 2 mandatory seeds = 5 tasks, pair still last.
        self.assertEqual(len(numbers), 5)
        ts = self.tasks("demo")
        self.assertIn("[e2e]", ts[-2][2])
        self.assertIn("[kb]", ts[-1][2])

    # --- (d) --e2e/--kb add EXTRA tagged tasks, mandatory pair intact --------
    def test_extra_e2e_kb_flags_do_not_remove_mandatory_pair(self):
        self.new_plan("demo", "Demo Feature")
        self.run_plan(["plan", "add-task", "demo", "An extra e2e check",
                       "--e2e"])
        self.run_plan(["plan", "add-task", "demo", "An extra kb step",
                       "--kb"])
        text = self.read_plan("demo")
        # Mandatory pair still present AND still the last two tasks.
        ts = self.tasks("demo")
        self.assertIn("[e2e]", ts[-2][2])
        self.assertIn("[kb]", ts[-1][2])
        # The extra tagged tasks were inserted before the pair (so >=2 [e2e]
        # tokens now exist: the extra one + the mandatory seed).
        self.assertGreaterEqual(text.count("[e2e]"), 2)

    # --- (e) set-status flips only the target line --------------------------
    def test_set_status_flips_only_target_line(self):
        self.new_plan("demo", "Demo Feature")
        self.run_plan(["plan", "add-task", "demo", "First real task"])
        self.run_plan(["plan", "add-task", "demo", "Second real task"])
        before = self.read_plan("demo")
        # Flip task 1 ⬜ -> 🟡 -> ✅.
        code, out, err = self.run_plan(["plan", "set-status", "demo", "1", "wip"])
        self.assertEqual(code, 0, "%s %s" % (out, err))
        ts = {n: s for (s, n, _) in self.tasks("demo")}
        self.assertEqual(ts[1], "🟡")
        code, _, _ = self.run_plan(["plan", "set-status", "demo", "1", "done"])
        self.assertEqual(code, 0)
        ts = {n: s for (s, n, _) in self.tasks("demo")}
        self.assertEqual(ts[1], "✅")
        # Every OTHER task line is byte-identical to before (line-local edit).
        before_lines = before.splitlines()
        after_lines = self.read_plan("demo").splitlines()
        self.assertEqual(len(before_lines), len(after_lines),
                         "set-status must not add/remove lines")
        changed = [i for i in range(len(before_lines))
                   if before_lines[i] != after_lines[i]]
        # Only the single task-1 marker line changed.
        self.assertEqual(len(changed), 1,
                         "exactly one line should change: %r" %
                         [before_lines[i] for i in changed])
        self.assertRegex(after_lines[changed[0]], r"- ✅ \*\*1\*\*")

    def test_set_status_back_to_todo(self):
        self.new_plan("demo", "Demo Feature")
        self.run_plan(["plan", "add-task", "demo", "Real task"])
        self.run_plan(["plan", "set-status", "demo", "1", "done"])
        self.run_plan(["plan", "set-status", "demo", "1", "todo"])
        ts = {n: s for (s, n, _) in self.tasks("demo")}
        self.assertEqual(ts[1], "⬜")

    # --- (--self-extension) seeds warning + autonomous R-PKG tasks ----------
    def test_self_extension_seeds_warning_and_autonomous_tasks(self):
        code, out, err = self.new_plan(
            "selfext", "Self Ext", "--self-extension")
        self.assertEqual(code, 0, "%s %s" % (out, err))
        text = self.read_plan("selfext")
        self.assertIn("Self-extension", text)
        # Mandatory pair still present.
        self.assertEqual(text.count("[e2e]"), 1)
        self.assertEqual(text.count("[kb]"), 1)


    # --- (Task 5) round-trip: emitter output ALWAYS passes plan lint ---------
    def _lint(self, feature, strict=False):
        import json
        argv = ["plan", "lint", "--path", self.plan_path(feature),
                "--format", "json"]
        if strict:
            argv.append("--strict")
        code, out, err = self.run_plan(argv)
        return code, json.loads(out)

    def test_roundtrip_fresh_plan_passes_lint(self):
        # A freshly seeded plan (no add-task) already satisfies R6 (e2e) + R7 (kb).
        self.new_plan("demo", "Demo Feature")
        code, payload = self._lint("demo")
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["errors"], [])

    def test_roundtrip_after_inserts_passes_strict_lint(self):
        self.new_plan("demo", "Demo Feature")
        self.run_plan(["plan", "add-task", "demo", "Implement core",
                       "--verify", "python3 -m unittest tests.test_core"])
        self.run_plan(["plan", "add-task", "demo", "Extra e2e", "--e2e"])
        self.run_plan(["plan", "add-task", "demo", "Extra kb", "--kb"])
        code, payload = self._lint("demo", strict=True)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["warnings"], [])

    def test_roundtrip_self_extension_passes_strict_lint(self):
        self.new_plan("selfext", "Self Ext", "--self-extension")
        code, payload = self._lint("selfext", strict=True)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["errors"], [])


if __name__ == "__main__":
    unittest.main()
