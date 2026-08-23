"""`mqintegrate`'s own unit file — task 99 wave 7, landed with the paydown.

`agentware_lib.features.fanoutmq.mqintegrate` is the merge-queue: the thing that
takes N finished parallel lanes and lands them, one at a time, on one integration
branch. Until this file the module had no owning test. `tests/integration/
test_fanout_merge_queue.py` drives it end-to-end against two real git repos,
which is the right test for "does a merge actually merge" and the wrong one for
every decision this file is about — it cannot reach a KB integrator that returns
`prose-conflict`, cannot make the second gate the failing one, and takes a real
merge per assertion.

This wave split `cmd_fanout_merge_queue` (31 branches / 149 lines -> 10 / 60)
into `_mq_queue_opts`, `_mq_dry_run_report`, `_mq_prepare_integration`,
`_mq_run_gates` and `_mq_process_lane`.

THIS PART owns the QUEUE — everything around a lane rather than inside one. The
single-lane pipeline, which is where the split's real risk lives, is in
`test_mqintegrate_lane`; the shared header is `_mqintegrate_support`.

  OPTS/PRECEDENCE   — the gate list is `--no-gates` beats explicit `--gate`
                      beats FANOUT_DEFAULT_GATES. An empty `--gate` list is NOT
                      "no gates": it falls through to the defaults, because
                      argparse's `append` default is None and an operator who
                      wanted none has `--no-gates`.
  OPTS/DEFAULTS     — every option is read with a default, so an in-process
                      caller with a partial namespace gets the CLI's answer
                      rather than an AttributeError from whichever arm reads
                      first.
  DRY/NO-WORK       — a dry run integrates nothing. Asserted with both
                      integrators wired to raise, so "it printed a plan" cannot
                      pass for "it did not merge".
  DRY/RENDER        — both formats name the integration branch and the gate
                      list, because the gate list is the half of a dry run an
                      operator cannot otherwise predict.
  PREP/ASYMMETRY    — the package integration branch is created unconditionally;
                      the KB one only when there IS a KB work tree. A missing KB
                      DEGRADES to package-only rather than refusing, and that
                      asymmetry is a decision, not an oversight.
  GATES/FIRST-FAIL  — the gate runner stops at the FIRST failure and returns it.
                      Later gates never run, because they would be reporting on
                      a tree the operator is about to change.
  EMIT/EXIT-CODE    — `_mq_emit` owns the process exit code: 1 iff halted.
  QUEUE/STOPS       — the queue stops at the first halting lane and emits the
                      rows accumulated SO FAR, including the halting one.

Hermetic: no git, no subprocess, no tempdir. Every collaborator this file needs
is injected at the module seam it is reached through, so the function under test
is always the real one.
"""

import json
import unittest

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
import agentware_lib.features.fanout as fanout  # noqa: F401
import agentware_lib.ops.kbgit as kbgit  # noqa: F401
import agentware_lib.features.fanoutmq.mqbranch as mqbranch  # noqa: F401
import agentware_lib.features.fanoutmq.mqintegrate as mqi

# --- OPTS -------------------------------------------------------------------

class QueueOptsTest(unittest.TestCase):
    """OPTS/PRECEDENCE, OPTS/DEFAULTS."""

    def test_empty_namespace_takes_every_cli_default(self):
        o = mqi._mq_queue_opts(_Args())
        self.assertEqual(o["fmt_json"], False)
        self.assertEqual(o["dry"], False)
        self.assertEqual(o["integration"], "integration")
        self.assertIsNone(o["base"])
        self.assertIsNone(o["kb_base"])
        self.assertEqual(o["requested"], [])
        self.assertEqual(o["gates"], list(mqi.FANOUT_DEFAULT_GATES))

    def test_no_gates_beats_an_explicit_gate_list(self):
        o = mqi._mq_queue_opts(_Args(no_gates=True, gate=["echo one"]))
        self.assertEqual(o["gates"], [])

    def test_explicit_gates_beat_the_defaults(self):
        o = mqi._mq_queue_opts(_Args(gate=["echo one", "echo two"]))
        self.assertEqual(o["gates"], ["echo one", "echo two"])

    def test_an_empty_gate_list_falls_through_to_the_defaults(self):
        # argparse's `append` default is None and an operator who wants no gates
        # has --no-gates, so [] must NOT be read as "none".
        for empty in ([], None):
            o = mqi._mq_queue_opts(_Args(gate=empty))
            self.assertEqual(o["gates"], list(mqi.FANOUT_DEFAULT_GATES))

    def test_gate_list_is_a_copy_not_the_module_constant(self):
        o = mqi._mq_queue_opts(_Args())
        o["gates"].append("mutated")
        self.assertNotIn("mutated", list(mqi.FANOUT_DEFAULT_GATES))

    def test_empty_integration_branch_falls_back_to_the_default(self):
        for value in (None, ""):
            self.assertEqual(
                mqi._mq_queue_opts(_Args(integration_branch=value))["integration"],
                "integration")
        self.assertEqual(
            mqi._mq_queue_opts(_Args(integration_branch="release"))["integration"],
            "release")

    def test_format_is_json_only_on_the_exact_token(self):
        self.assertTrue(mqi._mq_queue_opts(_Args(format="json"))["fmt_json"])
        for other in ("text", "JSON", "jsonl", ""):
            self.assertFalse(mqi._mq_queue_opts(_Args(format=other))["fmt_json"])

    def test_requested_features_are_copied_into_a_list(self):
        feats = ["b", "a"]
        o = mqi._mq_queue_opts(_Args(features=feats))
        self.assertEqual(o["requested"], ["b", "a"])
        o["requested"].append("c")
        self.assertEqual(feats, ["b", "a"])


# --- DRY --------------------------------------------------------------------

class DryRunReportTest(SeamRestoringTest):
    """DRY/NO-WORK, DRY/RENDER."""

    def setUp(self):
        super(DryRunReportTest, self).setUp()
        self.patch(fanout, "_lane_outcome", lambda rec: "completed")

    def test_dry_run_integrates_nothing(self):
        self.patch(mqi, "_mq_integrate_package", _raiser("package integrator"))
        self.patch(mqi, "_mq_integrate_kb", _raiser("kb integrator"))
        rc, out, _err = _capture(mqi._mq_dry_run_report, _opts(gates=["g"]),
                                 _reg("alpha"), ["alpha"])
        self.assertEqual(rc, 0)
        self.assertIn("alpha", out)

    def test_text_names_the_branch_the_lanes_and_the_gates(self):
        _rc, out, _e = _capture(
            mqi._mq_dry_run_report,
            _opts(integration="release", gates=["echo one", "echo two"]),
            _reg("alpha", "beta"), ["alpha", "beta"])
        self.assertIn("integration=release", out)
        self.assertIn("  alpha (completed)", out)
        self.assertIn("  beta (completed)", out)
        self.assertIn("  gate: echo one", out)
        self.assertIn("  gate: echo two", out)

    def test_json_carries_the_same_four_facts(self):
        _rc, out, _e = _capture(
            mqi._mq_dry_run_report,
            _opts(fmt_json=True, integration="release", gates=["echo one"]),
            _reg("alpha"), ["alpha"])
        data = json.loads(out)
        self.assertEqual(data["dry_run"], True)
        self.assertEqual(data["integration"], "release")
        self.assertEqual(data["gates"], ["echo one"])
        self.assertEqual(data["lanes"],
                         [{"feature": "alpha", "outcome": "completed"}])

    def test_plan_follows_the_queue_order_not_the_registry_order(self):
        # BOTH directions, and that is not belt-and-braces: with only the
        # descending row, `sorted(order, reverse=True)` reproduces the expected
        # output exactly and the assertion passes against a plan that ignores
        # the queue order entirely. One direction cannot distinguish "kept the
        # order" from "sorted it the way this fixture happened to be sorted".
        for order in (["beta", "alpha"], ["alpha", "beta"]):
            _rc, out, _e = _capture(mqi._mq_dry_run_report, _opts(),
                                    _reg("alpha", "beta"), order)
            self.assertLess(out.index(order[0]), out.index(order[1]), order)

    def test_outcome_is_reported_per_lane_not_assumed(self):
        self.patch(fanout, "_lane_outcome",
                   lambda rec: "running" if rec["feature"] == "beta"
                   else "completed")
        _rc, out, _e = _capture(mqi._mq_dry_run_report, _opts(),
                                _reg("alpha", "beta"), ["alpha", "beta"])
        self.assertIn("alpha (completed)", out)
        self.assertIn("beta (running)", out)


# --- PREPARE ----------------------------------------------------------------

class PrepareIntegrationTest(SeamRestoringTest):
    """PREP/ASYMMETRY."""

    def _record(self, answers):
        seen = []

        def _ensure(repo, branch, base):
            seen.append((repo, branch, base))
            return answers.pop(0)
        self.patch(mqbranch, "_ensure_integration_branch", _ensure)
        return seen

    def test_both_sides_are_prepared_when_a_kb_exists(self):
        seen = self._record([(True, None), (True, None)])
        err = mqi._mq_prepare_integration(
            _opts(integration="release", base="b", kb_base="kb"),
            "/pkg", "/kb", True)
        self.assertIsNone(err)
        self.assertEqual(seen, [("/pkg", "release", "b"),
                                ("/kb", "release", "kb")])

    def test_kb_side_is_skipped_when_there_is_no_kb_work_tree(self):
        seen = self._record([(True, None)])
        self.assertIsNone(
            mqi._mq_prepare_integration(_opts(), "/pkg", "/kb", False))
        self.assertEqual([r for r, _b, _base in seen], ["/pkg"])

    def test_package_failure_returns_its_error_and_never_touches_the_kb(self):
        seen = self._record([(False, "package boom")])
        self.assertEqual(
            mqi._mq_prepare_integration(_opts(), "/pkg", "/kb", True),
            "package boom")
        self.assertEqual([r for r, _b, _base in seen], ["/pkg"])

    def test_kb_failure_returns_its_own_error(self):
        self._record([(True, None), (False, "kb boom")])
        self.assertEqual(
            mqi._mq_prepare_integration(_opts(), "/pkg", "/kb", True), "kb boom")

    def test_each_side_gets_its_own_base_ref(self):
        seen = self._record([(True, None), (True, None)])
        mqi._mq_prepare_integration(_opts(base="pkgbase", kb_base="kbbase"),
                                    "/pkg", "/kb", True)
        self.assertEqual([base for _r, _b, base in seen], ["pkgbase", "kbbase"])


# --- GATES ------------------------------------------------------------------

class RunGatesTest(SeamRestoringTest):
    """GATES/FIRST-FAIL."""

    def _gates(self, table):
        seen = []

        def _run(cmd, repo, kb_repo):
            seen.append((cmd, repo, kb_repo))
            return table.get(cmd, (0, "ok\n"))
        self.patch(fanout, "_run_gate", _run)
        return seen

    def test_all_passing_returns_none_and_runs_every_gate(self):
        seen = self._gates({})
        self.assertIsNone(mqi._mq_run_gates(["a", "b", "c"], "/pkg", "/kb"))
        self.assertEqual([c for c, _r, _k in seen], ["a", "b", "c"])

    def test_first_failure_is_returned_and_stops_the_rest(self):
        seen = self._gates({"b": (3, "boom\n")})
        self.assertEqual(mqi._mq_run_gates(["a", "b", "c"], "/pkg", "/kb"),
                         ("b", 3, "boom\n"))
        self.assertEqual([c for c, _r, _k in seen], ["a", "b"])

    def test_an_empty_gate_list_runs_nothing_and_passes(self):
        seen = self._gates({})
        self.assertIsNone(mqi._mq_run_gates([], "/pkg", "/kb"))
        self.assertEqual(seen, [])

    def test_both_repos_are_passed_through_unchanged(self):
        seen = self._gates({})
        mqi._mq_run_gates(["a"], "/pkg", "/kbint")
        self.assertEqual(seen, [("a", "/pkg", "/kbint")])

    def test_a_negative_return_code_still_counts_as_failure(self):
        # `rc != 0`, not `rc > 0`: a signal-killed gate returns -N.
        self._gates({"a": (-9, "killed\n")})
        self.assertEqual(mqi._mq_run_gates(["a"], "/pkg", "/kb"),
                         ("a", -9, "killed\n"))



# --- EMIT + QUEUE ------------------------------------------------------------

class EmitTest(unittest.TestCase):
    """EMIT/EXIT-CODE."""

    def test_exit_code_is_one_iff_halted(self):
        for halt, expected in ((False, 0), (True, 1)):
            rc, _o, _e = _capture(mqi._mq_emit, [], False, halt, "msg")
            self.assertEqual(rc, expected)

    def test_halt_message_goes_to_stderr_only_when_present(self):
        _rc, _o, err = _capture(mqi._mq_emit, [], False, True, "the reason")
        self.assertEqual(err, "the reason\n")
        for msg in (None, ""):
            _rc, _o, err = _capture(mqi._mq_emit, [], False, True, msg)
            self.assertEqual(err, "")

    def test_a_message_without_a_halt_is_never_printed(self):
        _rc, _o, err = _capture(mqi._mq_emit, [], False, False, "ignored")
        self.assertEqual(err, "")

    def test_text_renders_one_padded_line_per_row(self):
        rows = [{"feature": "alpha", "status": "merged", "detail": "ok"}]
        _rc, out, _e = _capture(mqi._mq_emit, rows, False, False, None)
        self.assertEqual(
            out, "fanout merge-queue: alpha                      merged         ok\n")

    def test_text_tolerates_a_row_with_no_detail_key(self):
        _rc, out, _e = _capture(mqi._mq_emit,
                                [{"feature": "a", "status": "skipped"}],
                                False, False, None)
        self.assertTrue(out.endswith("skipped        \n"))

    def test_json_carries_the_halt_flag_and_every_row(self):
        rows = [{"feature": "a", "status": "merged", "detail": "ok"}]
        _rc, out, _e = _capture(mqi._mq_emit, rows, True, True, "msg")
        self.assertEqual(json.loads(out), {"halted": True, "lanes": rows})

    def test_json_keeps_non_ascii_unescaped(self):
        rows = [{"feature": "ünï", "status": "merged", "detail": "détail"}]
        _rc, out, _e = _capture(mqi._mq_emit, rows, True, False, None)
        self.assertIn("ünï", out)


class QueueTest(SeamRestoringTest):
    """QUEUE/STOPS — the parent's own loop."""

    def setUp(self):
        super(QueueTest, self).setUp()
        self.seen = []
        self.patch(fanout, "_resolve_repo_arg", lambda v, d: v or d)
        self.patch(mqbranch, "_lanes_common_repo",
                   lambda reg, order, key, default: default)
        self.patch(kbgit, "git_is_work_tree", lambda p: True)
        self.patch(mqbranch, "_ensure_integration_branch",
                   lambda *a, **k: (True, None))

    def _lanes(self, table):
        def _proc(feature, reg, opts, repo, kb_repo, have_kb):
            self.seen.append(feature)
            return table[feature]
        self.patch(mqi, "_mq_process_lane", _proc)

    def _reg(self, *features):
        self.patch(fanout, "load_lanes", lambda: (_reg(*features), None))

    def test_every_lane_runs_when_none_halts(self):
        self._reg("alpha", "beta")
        self._lanes({f: ({"feature": f, "status": "merged"}, None)
                     for f in ("alpha", "beta")})
        rc, out, _e = _capture(mqi.cmd_fanout_merge_queue, _Args())
        self.assertEqual(rc, 0)
        self.assertEqual(self.seen, ["alpha", "beta"])

    def test_the_queue_stops_at_the_first_halting_lane(self):
        self._reg("alpha", "beta", "gamma")
        self._lanes({
            "alpha": ({"feature": "alpha", "status": "merged"}, None),
            "beta": ({"feature": "beta", "status": "halt-kb"}, "stop here"),
            "gamma": ({"feature": "gamma", "status": "merged"}, None)})
        rc, out, err = _capture(mqi.cmd_fanout_merge_queue, _Args())
        self.assertEqual(rc, 1)
        self.assertEqual(self.seen, ["alpha", "beta"])
        self.assertEqual(err, "stop here\n")
        # The halting row is emitted WITH the rows before it, never dropped.
        self.assertIn("alpha", out)
        self.assertIn("beta", out)

    def test_lanes_default_to_sorted_registry_order(self):
        self._reg("gamma", "alpha", "beta")
        self._lanes({f: ({"feature": f, "status": "merged"}, None)
                     for f in ("alpha", "beta", "gamma")})
        _capture(mqi.cmd_fanout_merge_queue, _Args())
        self.assertEqual(self.seen, ["alpha", "beta", "gamma"])

    def test_requested_features_win_over_the_registry_order(self):
        self._reg("alpha", "beta")
        self._lanes({f: ({"feature": f, "status": "merged"}, None)
                     for f in ("alpha", "beta")})
        _capture(mqi.cmd_fanout_merge_queue, _Args(features=["beta", "alpha"]))
        self.assertEqual(self.seen, ["beta", "alpha"])

    def test_an_unknown_lane_refuses_before_anything_runs(self):
        self._reg("alpha")
        self.patch(mqi, "_mq_process_lane", _raiser("lane processor"))
        rc, _o, err = _capture(mqi.cmd_fanout_merge_queue,
                               _Args(features=["nope"]))
        self.assertEqual(rc, 1)
        self.assertIn("unknown lane(s): nope", err)

    def test_a_registry_error_refuses_before_anything_runs(self):
        self.patch(fanout, "load_lanes", lambda: (None, "invalid registry"))
        self.patch(mqi, "_mq_process_lane", _raiser("lane processor"))
        rc, _o, err = _capture(mqi.cmd_fanout_merge_queue, _Args())
        self.assertEqual(rc, 1)
        self.assertIn("invalid registry", err)

    def test_a_package_repo_that_is_not_a_work_tree_refuses(self):
        self._reg("alpha")
        self.patch(kbgit, "git_is_work_tree", lambda p: False)
        self.patch(mqi, "_mq_process_lane", _raiser("lane processor"))
        rc, _o, err = _capture(mqi.cmd_fanout_merge_queue, _Args())
        self.assertEqual(rc, 1)
        self.assertIn("package repo is not a git work tree", err)

    def test_a_dry_run_answers_before_the_integration_branches_exist(self):
        self._reg("alpha")
        self.patch(mqbranch, "_ensure_integration_branch",
                   _raiser("ensure integration branch"))
        self.patch(mqi, "_mq_process_lane", _raiser("lane processor"))
        self.patch(fanout, "_lane_outcome", lambda rec: "completed")
        rc, out, _e = _capture(mqi.cmd_fanout_merge_queue,
                               _Args(dry_run=True))
        self.assertEqual(rc, 0)
        self.assertIn("dry-run", out)

    def test_a_preflight_failure_refuses_before_any_lane_runs(self):
        self._reg("alpha")
        self.patch(mqbranch, "_ensure_integration_branch",
                   lambda *a, **k: (False, "cannot create integration"))
        self.patch(mqi, "_mq_process_lane", _raiser("lane processor"))
        rc, _o, err = _capture(mqi.cmd_fanout_merge_queue, _Args())
        self.assertEqual(rc, 1)
        self.assertIn("cannot create integration", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
