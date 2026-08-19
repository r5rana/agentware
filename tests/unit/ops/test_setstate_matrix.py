"""Full-matrix tests for the plan state machine (feature 260710-plan-state-machine).

Exercises `plan set-state <feature> <state>` against the SIX states
(draft|ready|running|done|superseded|cancelled) and the FULL legal-transition
matrix: every legal edge succeeds from the right precondition, every unlisted
edge is refused, actor restrictions hold, and the non-argv gates (readiness,
open-markers, re-claim) fire. Also covers `> Status:` header seeding/parsing,
Claimed-by stamping, the append-only `## Status log`, duplicate-status refusal,
and the `_merge_plan_status` helper.

All test names are prefixed `test_setstate_` (incl. `test_setstate_merge_`) so a
`-k test_setstate` gate matches these and nothing pre-existing.

Runner: python3 -m unittest tests.unit.ops.test_setstate_matrix -v
"""

import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import run_cli as _raw_run_cli, build_synthetic_kb, load_cli
except ImportError:  # when run via `discover -s tests`, tests/ is on sys.path
    from _fixtures import run_cli as _raw_run_cli, build_synthetic_kb, load_cli


# The four exact skeleton placeholder signatures -> real content, so a filled
# plan passes `plan set-state ... ready` readiness.
_FILL = [
    ("_TODO: problem, goal, design, constraints, environment, pitfalls._",
     "Real context: the problem, the goal, and the design are described here."),
    ("- TODO: replace with the absolute or ~-rooted directory path(s) this "
     "plan targets",
     "- ~/workspace/agentware"),
    ("  *Verify:* TODO — exercise the feature end-to-end and capture the outcome.",
     "  *Verify:* run the feature end-to-end and observe the real output."),
    ("- [ ] TODO: define the verifiable acceptance criteria.",
     "- [ ] The feature works end-to-end and is independently verifiable."),
]


class SetStateTestCase(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-setstate-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = load_cli()

    # --- helpers ------------------------------------------------------------
    def run_cli(self, argv):
        try:
            return _raw_run_cli(argv, self.kdir)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            return code, "", "SystemExit(%r)" % (exc.code,)

    def plan_path(self, feature):
        return os.path.join(self.kdir, "work", feature, "plan.md")

    def read(self, feature):
        with open(self.plan_path(feature), "r", encoding="utf-8") as f:
            return f.read()

    def write(self, feature, text):
        with open(self.plan_path(feature), "w", encoding="utf-8") as f:
            f.write(text)

    def scaffold(self, feature="demo", filled=False, done=False):
        code, _out, err = self.run_cli(["plan", "new", feature, "--title", "Demo"])
        self.assertEqual(code, 0, err)
        if filled or done:
            text = self.read(feature)
            for placeholder, real in _FILL:
                text = text.replace(placeholder, real)
            if done:
                text = text.replace("- ⬜ **", "- ✅ **")
            self.write(feature, text)
        return feature

    def force_state(self, feature, state):
        """SETUP shortcut: rewrite the `> Status:` header directly (not via the
        command under test) so an edge can be exercised from any from-state."""
        lines = self.read(feature).splitlines()
        out, replaced = [], False
        for ln in lines:
            if ln.startswith("> Status:"):
                out.append("> Status: %s" % state)
                replaced = True
            else:
                out.append(ln)
        if not replaced:
            # insert after Created
            out2 = []
            for ln in out:
                out2.append(ln)
                if ln.startswith("> Created:"):
                    out2.append("> Status: %s" % state)
            out = out2
        self.write(feature, "\n".join(out) + "\n")

    def force_claim(self, feature, who, ts):
        lines = self.read(feature).splitlines()
        out = []
        for ln in lines:
            out.append(ln)
            if ln.startswith("> Status:"):
                out.append("> Claimed-by: %s %s" % (who, ts))
        self.write(feature, "\n".join(out) + "\n")

    def status_of(self, feature):
        state, _n = self.mod._read_plan_status(self.read(feature))
        return state

    # --- header seeding / parsing -------------------------------------------
    def test_setstate_new_plan_born_draft(self):
        self.scaffold("born")
        text = self.read("born")
        self.assertEqual(text.count("\n> Status: draft"), 1)
        self.assertEqual(self.status_of("born"), "draft")

    def test_setstate_missing_status_reads_as_draft(self):
        self.scaffold("legacy")
        # strip the header line -> legacy plan with no `> Status:`
        text = "\n".join(ln for ln in self.read("legacy").splitlines()
                         if not ln.startswith("> Status:")) + "\n"
        self.write("legacy", text)
        self.assertEqual(self.status_of("legacy"), "draft")
        # a draft-legal edge succeeds, proving it was read as draft
        code, _o, err = self.run_cli(
            ["plan", "set-state", "legacy", "cancelled", "--reason", "x"])
        self.assertEqual(code, 0, err)

    def test_setstate_duplicate_status_header_refused(self):
        self.scaffold("dup")
        text = self.read("dup").replace(
            "> Status: draft", "> Status: draft\n> Status: running", 1)
        self.write("dup", text)
        code, _o, err = self.run_cli(
            ["plan", "set-state", "dup", "cancelled", "--reason", "x"])
        self.assertEqual(code, 1)
        self.assertIn("duplicate", err.lower())

    # --- readiness gate (draft -> ready) ------------------------------------
    def test_setstate_draft_to_ready_blocked_on_skeleton(self):
        self.scaffold("sk", filled=False)
        code, _o, err = self.run_cli(["plan", "set-state", "sk", "ready"])
        self.assertEqual(code, 1)
        self.assertIn("not ready", err.lower())

    def test_setstate_draft_to_ready_passes_when_filled(self):
        self.scaffold("fl", filled=True)
        code, _o, err = self.run_cli(["plan", "set-state", "fl", "ready"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.status_of("fl"), "ready")

    # --- approval edge + claim stamping -------------------------------------
    def test_setstate_ready_to_running_stamps_claim(self):
        f = self.scaffold("run1", filled=True)
        self.force_state(f, "ready")
        code, _o, err = self.run_cli(
            ["plan", "set-state", f, "running", "--actor", "loop",
             "--handle", "alice", "--host", "boxA", "--now",
             "2026-07-10T00:00:00Z"])
        self.assertEqual(code, 0, err)
        text = self.read(f)
        self.assertIn("> Claimed-by: alice@boxA 2026-07-10T00:00:00Z", text)
        self.assertEqual(self.status_of(f), "running")

    def test_setstate_leaving_running_removes_claim(self):
        f = self.scaffold("run2", done=True)
        self.force_state(f, "running")
        self.force_claim(f, "alice@boxA", "2026-07-10T00:00:00Z")
        code, _o, err = self.run_cli(["plan", "set-state", f, "done",
                                      "--actor", "loop"])
        self.assertEqual(code, 0, err)
        self.assertNotIn("Claimed-by", self.read(f))

    # --- re-claim (running -> running) --------------------------------------
    def test_setstate_reclaim_same_handle_ok(self):
        f = self.scaffold("rc1")
        self.force_state(f, "running")
        self.force_claim(f, "alice@boxA", "2026-07-10T00:00:00Z")
        code, _o, err = self.run_cli(
            ["plan", "set-state", f, "running", "--actor", "loop",
             "--handle", "alice", "--host", "boxA", "--now",
             "2026-07-10T01:00:00Z"])
        self.assertEqual(code, 0, err)

    def test_setstate_reclaim_other_handle_refused(self):
        f = self.scaffold("rc2")
        self.force_state(f, "running")
        self.force_claim(f, "alice@boxA", "2026-07-10T00:00:00Z")
        code, _o, err = self.run_cli(
            ["plan", "set-state", f, "running", "--actor", "loop",
             "--handle", "bob", "--host", "boxB", "--now",
             "2026-07-10T01:00:00Z"])
        self.assertEqual(code, 1)
        self.assertIn("claimed by", err.lower())

    def test_setstate_reclaim_other_handle_ok_when_stale(self):
        f = self.scaffold("rc3")
        self.force_state(f, "running")
        self.force_claim(f, "alice@boxA", "2026-07-01T00:00:00Z")  # 9 days old
        code, _o, err = self.run_cli(
            ["plan", "set-state", f, "running", "--actor", "loop",
             "--handle", "bob", "--host", "boxB", "--now",
             "2026-07-10T00:00:00Z"])
        self.assertEqual(code, 0, err)

    # --- running -> done open-marker gate -----------------------------------
    def test_setstate_running_to_done_blocked_open_markers(self):
        f = self.scaffold("d1", done=False)
        self.force_state(f, "running")
        code, _o, err = self.run_cli(["plan", "set-state", f, "done",
                                      "--actor", "loop"])
        self.assertEqual(code, 1)
        self.assertIn("open task marker", err.lower())

    def test_setstate_running_to_done_ok_all_checked(self):
        f = self.scaffold("d2", done=True)
        self.force_state(f, "running")
        code, _o, err = self.run_cli(["plan", "set-state", f, "done",
                                      "--actor", "loop"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.status_of(f), "done")

    # --- superseded edges ---------------------------------------------------
    def test_setstate_superseded_requires_superseded_by(self):
        f = self.scaffold("s1")
        self.force_state(f, "draft")
        code, _o, err = self.run_cli(["plan", "set-state", f, "superseded"])
        self.assertEqual(code, 1)
        self.assertIn("superseded-by", err.lower())
        code, _o, err = self.run_cli(
            ["plan", "set-state", f, "superseded", "--superseded-by", "f2"])
        self.assertEqual(code, 0, err)
        self.assertIn("> Superseded-by: f2", self.read(f))

    def test_setstate_done_to_superseded_allowed(self):
        f = self.scaffold("s2", done=True)
        self.force_state(f, "done")
        code, _o, err = self.run_cli(
            ["plan", "set-state", f, "superseded", "--superseded-by", "f3"])
        self.assertEqual(code, 0, err)

    def test_setstate_running_to_superseded_operator_only(self):
        f = self.scaffold("s3")
        self.force_state(f, "running")
        code, _o, err = self.run_cli(
            ["plan", "set-state", f, "superseded", "--superseded-by", "f4",
             "--actor", "loop"])
        self.assertEqual(code, 1)
        self.assertIn("actor", err.lower())
        code, _o, err = self.run_cli(
            ["plan", "set-state", f, "superseded", "--superseded-by", "f4",
             "--actor", "operator"])
        self.assertEqual(code, 0, err)

    # --- cancel edges + reopen ----------------------------------------------
    def test_setstate_draft_to_cancelled_requires_reason(self):
        f = self.scaffold("c1")
        code, _o, err = self.run_cli(["plan", "set-state", f, "cancelled"])
        self.assertEqual(code, 1)
        self.assertIn("reason", err.lower())

    def test_setstate_running_to_cancelled_operator_only(self):
        f = self.scaffold("c2")
        self.force_state(f, "running")
        code, _o, _e = self.run_cli(
            ["plan", "set-state", f, "cancelled", "--reason", "x",
             "--actor", "loop"])
        self.assertEqual(code, 1)
        code, _o, err = self.run_cli(
            ["plan", "set-state", f, "cancelled", "--reason", "x",
             "--actor", "operator"])
        self.assertEqual(code, 0, err)

    def test_setstate_cancelled_to_draft_operator_only_reopen(self):
        f = self.scaffold("c3")
        self.force_state(f, "cancelled")
        code, _o, _e = self.run_cli(
            ["plan", "set-state", f, "draft", "--reason", "r", "--actor", "loop"])
        self.assertEqual(code, 1)
        code, _o, err = self.run_cli(
            ["plan", "set-state", f, "draft", "--reason", "r",
             "--actor", "operator"])
        self.assertEqual(code, 0, err)

    # --- status log is append-only ------------------------------------------
    def test_setstate_status_log_appends(self):
        f = self.scaffold("log1")
        self.run_cli(["plan", "set-state", f, "cancelled", "--reason", "one"])
        self.run_cli(["plan", "set-state", f, "draft", "--reason", "two",
                      "--actor", "operator"])
        text = self.read(f)
        self.assertEqual(text.count("## Status log"), 1)
        self.assertIn("draft -> cancelled by operator reason: one", text)
        self.assertIn("cancelled -> draft by operator reason: two", text)

    # --- provenance injection guard (g2) ------------------------------------
    def test_setstate_reason_cannot_inject_header(self):
        f = self.scaffold("inj")
        evil = "> Status: done\n> Claimed-by: evil@host 2020-01-01T00:00:00Z"
        code, _o, err = self.run_cli(
            ["plan", "set-state", f, "cancelled", "--reason", evil])
        self.assertEqual(code, 0, err)
        # exactly one status header, and it is the legit `cancelled`
        state, n = self.mod._read_plan_status(self.read(f))
        self.assertEqual((state, n), ("cancelled", 1))
        # no FORGED header line (the sanitized reason text may mention the words
        # inline in the status log, but no line may START a `> Claimed-by:` header)
        self.assertFalse(any(ln.startswith("> Claimed-by:")
                             for ln in self.read(f).splitlines()))

    # --- every UNLISTED edge is refused -------------------------------------
    def test_setstate_all_unlisted_edges_refused(self):
        legal = set(self.mod._PLAN_STATE_TRANSITIONS.keys())
        states = self.mod._PLAN_STATES
        i = 0
        for a in states:
            for b in states:
                if (a, b) in legal:
                    continue
                i += 1
                feat = "edge%d" % i
                self.scaffold(feat)
                self.force_state(feat, a)
                code, _o, _e = self.run_cli(
                    ["plan", "set-state", feat, b, "--actor", "operator",
                     "--reason", "x", "--superseded-by", "z"])
                self.assertEqual(code, 1,
                                 "unlisted edge %s->%s should be refused" % (a, b))

    # --- merge helper (pitfall f) -------------------------------------------
    def test_setstate_merge_terminal_beats_nonterminal(self):
        self.assertEqual(self.mod._merge_plan_status("running", "done"), "done")
        self.assertEqual(self.mod._merge_plan_status("done", "running"), "done")

    def test_setstate_merge_downstream_wins(self):
        self.assertEqual(self.mod._merge_plan_status("draft", "running"),
                         "running")
        self.assertEqual(self.mod._merge_plan_status("ready", "draft"), "ready")

    def test_setstate_merge_superseded_is_most_final(self):
        self.assertEqual(self.mod._merge_plan_status("done", "superseded"),
                         "superseded")
        self.assertEqual(self.mod._merge_plan_status("cancelled", "superseded"),
                         "superseded")


if __name__ == "__main__":
    unittest.main()
