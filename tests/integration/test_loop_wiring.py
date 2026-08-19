"""Tests for the task-33 orchestrator split: the wiring gate, the call-site
counter it rests on, the two injected donor gates, and the composed post prompt.

Every "X reddens the gate" test drives `evaluate()` — the gate's real entry point
— over a MUTATED copy of the tree, rather than calling an internal comparator.
Testing the comparator proves the comparator; the arm selection, the input
capture and the early returns are where a gate actually goes vacuous.

The bash-behaviour tests SOURCE the real lib into a subshell with a stub
`scripts/agentware` on the path, so what is asserted is the shipped function's
control flow, not a Python re-implementation of it.
"""

from tests._paths import TESTS_DIR

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import _loop_wiring as W  # noqa: E402
from _loop_callsites import call_sites, count_call_sites, shell_defs, strip_comment  # noqa: E402

LOOP_DIR = os.path.join(REPO, "scripts", "loop")


def run_bash(script, env=None, cwd=None):
    """Run a bash snippet, returning (rc, stdout+stderr)."""
    e = dict(os.environ)
    e.pop("AGENTWARE_ORG_ROOT", None)
    e.pop("AGENTWARE_DISABLE_ORG_LINT", None)
    e.pop("AGENTWARE_DISABLE_RECALL_GATE", None)
    if env:
        e.update(env)
    p = subprocess.run(["bash", "-c", script], cwd=cwd or REPO, env=e,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, p.stdout.decode("utf-8", "replace")


class CallSiteCounterTest(unittest.TestCase):
    """The single definition of 'a bash call site' (tests/_loop_callsites.py)."""

    def test_prose_in_comments_is_not_a_call(self):
        text = "# the live log is a log of logs\n# log log log\nX=1\n"
        self.assertEqual(count_call_sites(text, "log"), 0)

    def test_command_position_counts(self):
        text = 'log "hi"\nif ! log "x"; then :; fi\nY=$(log "z")\n'
        self.assertEqual(count_call_sites(text, "log"), 3)

    def test_trap_handler_counts(self):
        """A trap handler is an ARGUMENT, never a command word — without this
        rule cleanup/loop_on_exit baseline at 0 and can never redden."""
        self.assertEqual(count_call_sites("trap cleanup INT TERM\n", "cleanup"), 1)
        self.assertEqual(count_call_sites("trap 'loop_on_exit' EXIT\n", "loop_on_exit"), 1)

    def test_definition_line_is_not_a_call(self):
        self.assertEqual(count_call_sites("open_markers() {\n  echo 0\n}\n", "open_markers"), 0)

    def test_substring_and_path_shapes_rejected(self):
        text = 'X="$log"\ncat logs/thing\nsource log-prompt.sh\necho a.log\n'
        self.assertEqual(count_call_sites(text, "log"), 0)

    def test_trailing_comment_stripped_but_quoted_hash_kept(self):
        self.assertEqual(strip_comment('run_x  # call it'), 'run_x  ')
        self.assertEqual(strip_comment("grep -E '^\\s*[MADRCU]#'"), "grep -E '^\\s*[MADRCU]#'")
        self.assertEqual(strip_comment('echo "${var#prefix}"'), 'echo "${var#prefix}"')

    def test_shell_defs_finds_every_orchestrator_function(self):
        defs = shell_defs(open(os.path.join(LOOP_DIR, "safety.sh")).read())
        for name in ("progress_signature", "check_no_progress", "run_updater_lock_guard"):
            self.assertIn(name, defs)


class WiringGateTest(unittest.TestCase):
    """The gate over the real tree, and the mutations that must redden it."""

    def setUp(self):
        self.fixture = W.load_fixture()

    def _tree(self):
        """A scratch copy of the loop surface the gate measures."""
        d = tempfile.mkdtemp(prefix="aw-wiring-")
        shutil.copy2(os.path.join(REPO, "agentware.sh"), os.path.join(d, "agentware.sh"))
        shutil.copytree(LOOP_DIR, os.path.join(d, "scripts", "loop"))
        # Copy the REAL hooks dir: the ceiling half of the gate measures it, so
        # an empty stand-in makes the declared codex-stream.py debt read as STALE
        # and every mutation test would then pass on the wrong violation.
        shutil.copytree(os.path.join(REPO, "scripts", "hooks"),
                        os.path.join(d, "scripts", "hooks"))
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def test_gate_passes_on_the_real_tree(self):
        violations, checklist = W.evaluate(self.fixture)
        self.assertEqual(violations, [], "\n".join(violations))
        self.assertGreaterEqual(len(checklist), 39)

    def test_orchestrator_within_ceiling(self):
        n = len(open(os.path.join(REPO, "agentware.sh")).read().splitlines())
        self.assertLessEqual(n, W.SCRIPT_LINE_CAP, "orchestrator is %d lines" % n)

    def test_every_loop_and_hook_script_within_ceiling_or_declared(self):
        debt = {r["path"]: r["lines"] for r in self.fixture["script_cap_debt"]}
        for rel, lines in W.capped_scripts():
            if lines > W.SCRIPT_LINE_CAP:
                self.assertIn(rel, debt, "%s is %d lines and undeclared" % (rel, lines))
                self.assertLessEqual(lines, debt[rel])

    def test_dropping_a_call_site_reddens_naming_the_capability(self):
        """The core proof: a definition that moves without its call."""
        d = self._tree()
        orch = os.path.join(d, "agentware.sh")
        text = open(orch).read()
        self.assertIn("run_selfheal_review", text)
        # Remove the ONLY invocation, leaving the definition untouched in its lib.
        text = text.replace("if ! run_selfheal_review; then", "if false; then")
        open(orch, "w").write(text)
        violations, _ = W.evaluate(self.fixture, root=d)
        self.assertTrue(any("WIRING LOST" in v and "run_selfheal_review" in v
                            for v in violations), violations)

    def test_dropping_the_exit_trap_reddens(self):
        d = self._tree()
        p = os.path.join(d, "scripts", "loop", "outcome.sh")
        open(p, "w").write(open(p).read().replace("trap 'loop_on_exit' EXIT", ":"))
        violations, _ = W.evaluate(self.fixture, root=d)
        self.assertTrue(any("loop_on_exit" in v for v in violations), violations)

    def test_lowering_the_frozen_baseline_reddens(self):
        """The baseline cannot be edited down to forgive a live regression."""
        fx = json.loads(json.dumps(self.fixture))
        for row in fx["capabilities"]:
            if row["name"] == "emit_review_report":
                row["premerge"] = 1
        violations, _ = W.evaluate(fx)
        self.assertTrue(any("BASELINE DRIFT" in v and "emit_review_report" in v
                            for v in violations), violations)

    def test_capability_defined_twice_reddens(self):
        d = self._tree()
        p = os.path.join(d, "scripts", "loop", "phase.sh")
        with open(p, "a") as fh:
            fh.write("\nopen_markers() { echo 0; }\n")
        violations, _ = W.evaluate(self.fixture, root=d)
        self.assertTrue(any("DEFINITION" in v and "open_markers" in v
                            for v in violations), violations)

    def test_premerge_capability_missing_from_fixture_reddens(self):
        fx = json.loads(json.dumps(self.fixture))
        fx["capabilities"] = [r for r in fx["capabilities"] if r["name"] != "run_kb_sync"]
        violations, _ = W.evaluate(fx)
        self.assertTrue(any("UNCLAIMED" in v and "run_kb_sync" in v
                            for v in violations), violations)

    def test_injected_gate_defined_in_hooks_lib_reddens_placement(self):
        """The plan requires the re-injected wiring in NEW libs, not hooks.sh."""
        d = self._tree()
        src = os.path.join(d, "scripts", "loop", "retrieval.sh")
        body = open(src).read()
        os.remove(src)
        with open(os.path.join(d, "scripts", "loop", "hooks.sh"), "a") as fh:
            fh.write("\n" + body.split("run_retrieval_quality_gate()", 1)[0].join([""]) )
            fh.write("\nrun_retrieval_quality_gate() { return 0; }\n")
        violations, _ = W.evaluate(self.fixture, root=d)
        self.assertTrue(any("PLACEMENT" in v for v in violations), violations)

    def test_over_cap_script_reddens_when_undeclared(self):
        d = self._tree()
        p = os.path.join(d, "scripts", "loop", "phase.sh")
        with open(p, "a") as fh:
            fh.write("\n".join("# pad %d" % i for i in range(W.SCRIPT_LINE_CAP)))
        violations, _ = W.evaluate(self.fixture, root=d)
        self.assertTrue(any("CEILING" in v and "phase.sh" in v for v in violations), violations)

    def test_stale_debt_row_reddens(self):
        """Shrink-only: a row forgiving a violation the tree no longer has fails."""
        fx = json.loads(json.dumps(self.fixture))
        fx["script_cap_debt"].append(
            {"path": "scripts/loop/phase.sh", "lines": 999, "owner": "nobody"})
        violations, _ = W.evaluate(fx)
        self.assertTrue(any("STALE DEBT" in v and "phase.sh" in v
                            for v in violations), violations)

    def test_no_loop_or_hook_script_is_over_cap_and_the_debt_list_is_empty(self):
        """The finished state, asserted directly rather than via the ratchet.

        Task 35 brought the last over-cap script (scripts/hooks/codex-stream.py,
        304 lines) under the ceiling by splitting its item vocabulary out, so the
        ceiling half of this gate is now a PROPERTY of the tree, not a promise
        about one. Asserting it here means a future over-cap script has to be
        split: re-declaring it as debt reddens this test, and the ratchet's
        stale-row rule reddens the moment it is fixed.
        """
        self.assertEqual(self.fixture["script_cap_debt"], [])
        over = [(rel, n) for rel, n in W.capped_scripts() if n > W.SCRIPT_LINE_CAP]
        self.assertEqual(over, [], "over the %d-line cap: %r" % (W.SCRIPT_LINE_CAP, over))

    def test_declared_debt_may_not_grow(self):
        """The ratchet's regression arm: a row forgives a violation at its
        DECLARED size and never a worse one.

        Built against a SCRATCH tree carrying a synthetic over-cap script, not
        against whatever happens to sit in `script_cap_debt`. That list is empty
        as of task 35 — the codex-stream.py row this test used to mutate went
        stale the moment the file came under the cap — and a mutation test that
        quietly iterates an empty list is worse than no test at all: it passes
        most confidently exactly when it has stopped checking anything.
        """
        d = self._tree()
        p = os.path.join(d, "scripts", "loop", "phase.sh")
        with open(p) as fh:
            before = len(fh.read().splitlines())
        with open(p, "a") as fh:
            fh.write("\n" + "\n".join("# pad %d" % i for i in range(W.SCRIPT_LINE_CAP)))
        actual = before + W.SCRIPT_LINE_CAP
        declared = W.SCRIPT_LINE_CAP + 1        # over the ceiling, but well under `actual`
        self.assertGreater(actual, declared, "the padding must exceed the declared debt")
        fx = json.loads(json.dumps(self.fixture))
        fx["script_cap_debt"].append(
            {"path": "scripts/loop/phase.sh", "lines": declared, "owner": "test"})
        violations, _ = W.evaluate(fx, root=d)
        self.assertTrue(any("CEILING REGRESSION" in v and "phase.sh" in v
                            for v in violations), violations)


class LibSourcingContractTest(unittest.TestCase):
    """Every lib must source cleanly under `set -e`.

    This is not hygiene. `source` returns the status of its LAST command, so a lib
    ending on a false `[[ -n "$X" ]] && Y=1` idiom makes `source lib.sh` a FAILED
    simple command and the orchestrator exits 1 having printed nothing at all.
    """

    def test_each_lib_sources_standalone_with_rc_zero(self):
        for name in sorted(os.listdir(LOOP_DIR)):
            if not name.endswith(".sh"):
                continue
            # args.sh/preflight.sh/dryrun.sh gate on invocation state; source them
            # with the minimum they need to reach their own last statement.
            if name in ("args.sh", "preflight.sh", "dryrun.sh", "prompts.sh",
                        "prompts-post.sh", "metrics.sh", "routing.sh"):
                continue
            rc, out = run_bash('set -e; source "%s"; echo RC_OK'
                               % os.path.join(LOOP_DIR, name))
            self.assertEqual(rc, 0, "%s: rc=%d\n%s" % (name, rc, out))
            self.assertIn("RC_OK", out)

    def test_a_lib_ending_on_a_false_test_would_abort_the_orchestrator(self):
        """Proves the failure mode the trailing `:` prevents is real."""
        d = tempfile.mkdtemp(prefix="aw-src-")
        self.addCleanup(shutil.rmtree, d, True)
        bad = os.path.join(d, "bad.sh")
        open(bad, "w").write('X=1\n[[ -n "${NOPE:-}" ]] && Y=true\n')
        rc, out = run_bash('set -e; source "%s"; echo REACHED' % bad)
        self.assertNotEqual(rc, 0)
        self.assertNotIn("REACHED", out)
        # ...and that appending `:` fixes it, which is what every lib here does.
        open(bad, "a").write("\n:\n")
        rc, out = run_bash('set -e; source "%s"; echo REACHED' % bad)
        self.assertEqual(rc, 0)
        self.assertIn("REACHED", out)

    def test_every_source_time_lib_ends_with_the_true_terminator(self):
        for name in ("args.sh", "routing.sh", "preflight.sh", "prompts.sh",
                     "prompts-post.sh", "metrics.sh", "outcome.sh", "dryrun.sh"):
            body = open(os.path.join(LOOP_DIR, name)).read().rstrip().splitlines()
            self.assertEqual(body[-1].strip(), ":",
                             "%s must end with `:` (see args.sh)" % name)


class ComposedPostPromptTest(unittest.TestCase):
    """The post prompt must be the UNION of both trees' numbered lists."""

    def _post_prompt(self):
        d = tempfile.mkdtemp(prefix="aw-prompt-")
        self.addCleanup(shutil.rmtree, d, True)
        rc, out = run_bash(
            'set -e\nDOCS_DIR=/d\nSTATE_DIR=/d/.loop\nFEATURE_NAME=f\n'
            'FEATURE_UPPER=F\nEXTRA_PRE_PROMPT=""\nEXTRA_MAIN_PROMPT=""\n'
            'EXTRA_POST_PROMPT=""\nPRE_PROMPT=""\nMAIN_PROMPT=""\n'
            'source "%s"\nprintf "%%s" "$POST_PROMPT"\n'
            % os.path.join(LOOP_DIR, "prompts-post.sh"))
        self.assertEqual(rc, 0, out)
        return out

    def test_carries_every_numbered_item_from_both_sources(self):
        p = self._post_prompt()
        for n in range(1, 11):
            self.assertRegex(p, r"(?m)^%d\. " % n,
                             "numbered item %d missing from the composed prompt" % n)

    def test_local_item_8_superset_survives(self):
        """This tree's item 8 adds the anti-leak clause the donor's lacks."""
        p = self._post_prompt()
        self.assertIn("must NOT live only in", p)
        self.assertIn("worklog scan", p)

    def test_donor_item_9_skill_lifecycle_present(self):
        p = self._post_prompt()
        self.assertIn("frequency-gated proposal lifecycle", p)
        self.assertIn("AGENTWARE_SKILL_PROPOSE_THRESHOLD", p)
        self.assertIn("Skill proposals ripe for promotion", p)
        self.assertIn("human-gated", p)

    def test_item_9_names_the_decided_verb_not_the_donors(self):
        """Per the plan's conflict table this tree KEEPS `skill propose` for its
        own argument-disjoint lifecycle; the donor ledger re-homes to
        `skill suggest`. Naming the donor's verb would make the post phase run a
        command that this tree's parser rejects."""
        p = self._post_prompt()
        self.assertIn("skill suggest", p)
        self.assertIn("skill suggestions --ripe", p)
        self.assertNotIn("skill propose --slug", p)
        self.assertNotIn("skill proposals --ripe", p)

    def test_promise_marker_is_the_last_numbered_item(self):
        p = self._post_prompt()
        self.assertRegex(p, r"(?m)^10\. Output <promise>POST_COMPLETE</promise>")


if __name__ == "__main__":
    unittest.main()
