"""`_mq_process_lane` — the merge-queue's SINGLE-LANE pipeline. Task 99 wave 7.

Split out of `test_mqintegrate` because the wave's owning unit file outgrew the
600-line corpus cap. The seam is the module's own: that file owns the QUEUE (the
options, the dry run, the branch preflight, the gate runner, the emitter, the
parent loop); this one owns what happens INSIDE one lane. See
`tests.unit.features._mqintegrate_support` for the shared header.

This is where the wave's real risk lives. Before the split the pipeline was a
run of statements in one 31-branch body, and its order was simply the order they
were written in. After it, each step is a named call and every one of them reads
correct in isolation — so a transposition is invisible to any fixture in which
only one step can fail. Every ORDER row below is therefore built on a lane rigged
to fail TWO steps at once, and asserts WHICH one won.

  LANE/SKIP-FIRST   — `merged` and `not finished` short-circuit BEFORE any git
                      runs. Asserted with tripwire integrators: a reordering
                      that integrates first and skips second returns the same
                      row, so only a raise can tell them apart.
  LANE/ORDER        — package before KB before gates before the artifact commit
                      before `merged`.
  LANE/HALT-SHAPE   — a halt returns (result, msg) and emits NOTHING itself; the
                      queue is the only writer. A halting lane is never stamped
                      `merged`, so the retry is idempotent.
  LANE/ARTIFACTS    — the gate-artifact commit runs only after the gates PASSED
                      and only when the KB tree is actually dirty.

Hermetic: no git, no subprocess, no tempdir.
"""

import unittest  # noqa: F401

from tests.unit.features._mqintegrate_support import (  # noqa: F401
    _Args,
    _raiser,
    _capture,
    _rec,
    _reg,
    OPTS,
    _opts,
    SeamRestoringTest,
)


# The shared header is imported FIRST: it is what puts `scripts/` on
# sys.path, so an `agentware_lib` import above it fails on a clean
# interpreter and passes only when another test file ran first.
import agentware_lib.features.fanout as fanout
import agentware_lib.ops.kbgit as kbgit
import agentware_lib.features.fanoutmq.mqintegrate as mqi

# --- LANE -------------------------------------------------------------------

class ProcessLaneTest(SeamRestoringTest):
    """LANE/SKIP-FIRST, LANE/ORDER, LANE/HALT-SHAPE, LANE/ARTIFACTS."""

    def setUp(self):
        super(ProcessLaneTest, self).setUp()
        self.order = []
        self.patch(fanout, "_lane_outcome", lambda rec: "completed")
        self.patch(fanout, "save_lanes", lambda reg: self.order.append("save"))
        self.patch(kbgit, "git_is_work_tree", lambda p: True)
        self.patch(kbgit, "git_is_clean", lambda p: True)
        self._pkg(("merged", "ok"))
        self._kb(("merged", "ok"))
        self._gates(None)

    def _pkg(self, ret):
        def _fn(repo, integration, branch):
            self.order.append("package")
            return ret
        self.patch(mqi, "_mq_integrate_package", _fn)

    def _kb(self, ret):
        def _fn(kb_repo, integration, branch):
            self.order.append("kb")
            return ret
        self.patch(mqi, "_mq_integrate_kb", _fn)

    def _gates(self, ret):
        def _fn(gates, repo, kb_repo):
            self.order.append("gates")
            return ret
        self.patch(mqi, "_mq_run_gates", _fn)

    def _run(self, reg, feature="alpha", **kw):
        return _capture(mqi._mq_process_lane, feature, reg,
                        _opts(**kw), "/pkg", "/kb", kw.pop("have_kb", True))

    # -- the skips --------------------------------------------------------
    def test_an_already_merged_lane_skips_before_any_git_runs(self):
        self.patch(mqi, "_mq_integrate_package", _raiser("package"))
        self.patch(mqi, "_mq_integrate_kb", _raiser("kb"))
        self.patch(mqi, "_mq_run_gates", _raiser("gates"))
        reg = _reg("alpha", merged=True)
        (result, halt), _o, _e = self._run(reg)
        self.assertIsNone(halt)
        self.assertEqual(result, {"feature": "alpha", "status": "skipped",
                                  "detail": "already merged"})

    def test_an_unfinished_lane_skips_before_any_git_runs(self):
        self.patch(fanout, "_lane_outcome", lambda rec: "running")
        self.patch(mqi, "_mq_integrate_package", _raiser("package"))
        self.patch(mqi, "_mq_integrate_kb", _raiser("kb"))
        (result, halt), _o, _e = self._run(_reg("alpha"))
        self.assertIsNone(halt)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["detail"], "not finished (outcome=running)")

    def test_the_merged_flag_is_checked_before_the_outcome(self):
        # A lane that is BOTH merged and unfinished reports "already merged":
        # the resume path must not re-classify a finished lane as unfinished.
        self.patch(fanout, "_lane_outcome", lambda rec: "running")
        (result, _h), _o, _e = self._run(_reg("alpha", merged=True))
        self.assertEqual(result["detail"], "already merged")

    # -- the order --------------------------------------------------------
    def test_the_happy_path_runs_package_then_kb_then_gates_then_save(self):
        (result, halt), _o, _e = self._run(_reg("alpha"))
        self.assertIsNone(halt)
        self.assertEqual(result, {"feature": "alpha", "status": "merged",
                                  "detail": "ok"})
        self.assertEqual(self.order, ["package", "kb", "gates", "save"])

    def test_a_package_conflict_halts_before_the_kb_is_opened(self):
        # The lane is rigged to fail the KB step AND the gates too; if the
        # pipeline were reordered this row would name a different halt.
        self._pkg(("conflict", "conflict in: scripts/agentware"))
        self._kb(("prose-conflict", "would also fail"))
        self._gates(("g", 1, "would also fail"))
        (result, halt), _o, _e = self._run(_reg("alpha"))
        self.assertEqual(result["status"], "halt-package")
        self.assertEqual(self.order, ["package"])
        self.assertIn("package merge conflict", halt)
        self.assertIn("alpha", halt)

    def test_a_package_error_halts_the_same_way_as_a_conflict(self):
        self._pkg(("error", "checkout failed"))
        (result, halt), _o, _e = self._run(_reg("alpha"))
        self.assertEqual(result["status"], "halt-package")
        self.assertIn("package merge error", halt)

    def test_a_package_status_outside_the_halt_set_falls_through(self):
        self._pkg(("skipped", "already merged"))
        (result, halt), _o, _e = self._run(_reg("alpha"))
        self.assertIsNone(halt)
        self.assertEqual(self.order, ["package", "kb", "gates", "save"])

    def test_a_kb_halt_stops_before_the_gates(self):
        self._kb(("prose-conflict", "same-entry conflict in: x.md"))
        self._gates(("g", 1, "would also fail"))
        (result, halt), _o, _e = self._run(_reg("alpha"))
        self.assertEqual(result["status"], "halt-kb")
        self.assertEqual(result["detail"], "same-entry conflict in: x.md")
        self.assertEqual(self.order, ["package", "kb"])
        self.assertIn("KB merge prose-conflict", halt)

    def test_a_kb_status_outside_the_halt_set_falls_through_to_the_gates(self):
        self._kb(("skipped", "already merged"))
        (result, halt), _o, _e = self._run(_reg("alpha"))
        self.assertIsNone(halt)
        self.assertEqual(result["status"], "merged")
        self.assertEqual(self.order, ["package", "kb", "gates", "save"])

    def test_the_kb_step_is_skipped_entirely_without_a_kb_work_tree(self):
        self._kb(("prose-conflict", "must never be reached"))
        (result, halt), _o, _e = self._run(_reg("alpha"), have_kb=False)
        self.assertIsNone(halt)
        self.assertNotIn("kb", self.order)

    def test_a_gate_failure_halts_and_never_stamps_merged(self):
        self._gates(("scripts/agentware steering lint", 7, "boom\n"))
        reg = _reg("alpha")
        (result, halt), _o, _e = self._run(reg)
        self.assertEqual(result["status"], "halt-gate")
        self.assertEqual(result["detail"],
                         "gate 'scripts/agentware steering lint' exited 7")
        self.assertNotIn("merged", reg["lanes"]["alpha"])
        self.assertNotIn("save", self.order)

    def test_a_gate_failure_message_carries_only_the_last_eight_lines(self):
        body = "\n".join("line %02d" % i for i in range(20)) + "\n"
        self._gates(("g", 1, body))
        (_result, halt), _o, _e = self._run(_reg("alpha"))
        self.assertIn("line 12", halt)
        self.assertIn("line 19", halt)
        self.assertNotIn("line 11", halt)

    # -- the halt shape ----------------------------------------------------
    def test_a_halting_lane_emits_nothing_itself(self):
        self._pkg(("conflict", "boom"))
        (_result, _halt), out, err = self._run(_reg("alpha"))
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_a_halting_lane_leaves_the_registry_re_processable(self):
        self._kb(("error", "boom"))
        reg = _reg("alpha")
        self._run(reg)
        self.assertNotIn("merged", reg["lanes"]["alpha"])
        self.assertEqual(self.order.count("save"), 0)

    def test_success_stamps_merged_on_the_registry_record(self):
        reg = _reg("alpha")
        self._run(reg)
        self.assertIs(reg["lanes"]["alpha"]["merged"], True)

    # -- the gate-artifact commit ------------------------------------------
    def test_a_clean_kb_tree_is_not_committed(self):
        self.patch(kbgit, "git_is_clean", lambda p: True)
        self.patch(mqi, "staging", _StagingSpy(self.order))
        self._run(_reg("alpha"))
        self.assertNotIn("stage", self.order)

    def test_a_dirty_kb_tree_is_staged_after_the_gates_passed(self):
        self.patch(kbgit, "git_is_clean", lambda p: False)
        spy = _StagingSpy(self.order)
        self.patch(mqi, "staging", spy)
        self.patch(mqi, "_git", lambda *a, **k: self.order.append("commit"))
        self._run(_reg("alpha"))
        self.assertEqual(self.order,
                         ["package", "kb", "gates", "stage", "commit", "save"])

    def test_a_dirty_kb_tree_is_not_committed_when_a_gate_failed(self):
        self.patch(kbgit, "git_is_clean", lambda p: False)
        self.patch(mqi, "staging", _StagingSpy(self.order))
        self.patch(mqi, "_git", lambda *a, **k: self.order.append("commit"))
        self._gates(("g", 1, "boom\n"))
        self._run(_reg("alpha"))
        self.assertNotIn("stage", self.order)
        self.assertNotIn("commit", self.order)

    def test_no_kb_work_tree_means_no_artifact_commit(self):
        self.patch(kbgit, "git_is_clean", lambda p: False)
        self.patch(mqi, "staging", _StagingSpy(self.order))
        self._run(_reg("alpha"), have_kb=False)
        self.assertNotIn("stage", self.order)


class _StagingSpy(object):
    """Stands in for the `staging` module alias, recording the ONE call the
    lane processor makes into it."""

    def __init__(self, log):
        self._log = log

    def kb_git_stage_scoped(self, repo, allow_derived=False):
        self._log.append("stage")


