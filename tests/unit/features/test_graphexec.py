"""`graphexec`'s own unit file — task 99 wave 6, landed with the paydown.

`agentware_lib.features.graphdag.graphexec` is the tick loop that actually runs
the DAG, and before this wave `_graph_execute` was the widest body in the tree:
62 branches and 243 lines against caps of 12 and 120. Until this file the module
had no owning test — `test_plan_graph_driver` drives the executor, which is
coverage of the SCHEDULER's outcomes and not of this module's decomposition, and
`test_plan_graph_e2e` reaches it through the CLI.

The split is `_graph_execute_seams` / `_graph_execute_setup` (over `_preflight`,
`_topology`, `_selfext`) and the six tick arms `_graph_tick_statuses`,
`_graph_tick_halt_or_pause`, `_graph_tick_finalize_or_merge`,
`_graph_tick_terminal`, `_graph_tick_select` (over `_stalled`) and
`_graph_tick_spawn_wave`, with `_graph_run_result` stamping the counters. That
split has ONE hazard that no per-helper test can see, and it is the first class
below.

  LOOP/ARM-ORDER    — THE SCHEDULER'S PRIORITY IS THE ORDER OF THE CALLS IN THE
                      PARENT. Each arm is correct in isolation at any position,
                      so a transposition is invisible in review and invisible to
                      every fixture in which only one arm is live. It is asserted
                      DIRECTLY here, by recording the sequence of arm calls in
                      one tick, and by the short-circuit tests that prove each
                      arm's envelope stops every later arm.
  LOOP/CEILING      — `waves` bounds the loop; a tick that RE-ENTERS (finalize,
                      merge) does not advance it, which is why each of those arms
                      owns a progress guarantee.
  LOOP/COUNTERS     — every envelope carries `waves`/`dispatched`/`merged` from
                      the one state dict. `empty` is built by a direct
                      `_graph_result` call instead, preserved verbatim from the
                      pre-split body — an asymmetry that is NOT a behavioural
                      distinction, and is asserted as such rather than left to
                      be read as one.
  SEAMS/ZERO        — `tick_ceiling=0` means zero waves and is resolved with
                      `is None`; `or` would promote it to the maximum.
  PRE/KILL-SWITCH   — an orchestrator kill switch fails the run CLOSED before any
                      disk read, and a blank value is NOT presence.
  PRE/CONCURRENCY   — the bound is REFUSED, never clamped, and `True` does not
                      pass as 1 even though `bool` is an `int`.
  SETUP/ORDER       — nothing that could have refused BEFORE the `integration`
                      bootstrap (the first step that writes) may refuse after it.
  TOPO/ONCE         — resource edges are derived once per run, so a mid-run
                      manifest edit cannot move the dispatch order underneath an
                      approved graph. The two cycle reasons stay DISTINCT.
  SELFEXT/COMPUTED  — `self_extension` is recomputed from the LIVE plan and
                      re-stamped onto the node dict, so a manifest claiming
                      `false` on a self-ext plan cannot buy weakened gates.
  HALT/BEFORE-PAUSE — a tick holding both reports the HALT: resume is the answer
                      to a pause, and resuming into an unaddressed halt loops.
  FINALIZE/CLOSED   — an unfinalizable straggler HALTs rather than being retried
                      forever without advancing `waves`.
  MERGE/PROGRESS    — the merge arm's `continue` is bounded by a CROSS-MODULE
                      invariant, not by a guard here: `_graph_merge_lane` marks
                      the lane merged before every non-HALT return. That is
                      pinned below, on the shipped function, because the arm is
                      only safe for as long as it holds.
  SELECT/RETRY      — `--retry` re-offers a prior failure exactly ONCE.
  STALL/AMBIGUOUS   — a still-RUNNING node in a stalled tick is an ambiguous
                      never-arrived terminal: HALT. A merely blocked frontier is
                      reported as `blocked` and does not halt the world.
  SPAWN/BARRIER     — every dispatched lane is waited on; a handle without
                      `.wait` is tolerated rather than crashing the wave.
  DEADLINE/SEAM     — the guard is wired (its body was `pass`) and is disabled,
                      deterministically, when no clock is injected.

Hermetic: a scratch knowledge dir per test, every side-effecting seam injected,
no subprocess, no network, stdlib unittest only.
"""

from tests.unit.features._graphexec_support import (  # noqa: F401
    ARM_ORDER, ARM_PASSTHROUGH, GRAPH_KILL_SWITCH_VARS, NOW, _KdirCase,
    _graph_deadline_exceeded, _graph_deadline_start, _graph_execute,
    _graph_execute_preflight, _graph_execute_seams, _graph_execute_selfext,
    _graph_execute_setup, _graph_execute_topology, _graph_run_result,
    _graph_tick_halt_or_pause, _graph_tick_select, _graph_tick_spawn_wave,
    _graph_tick_stalled, _graph_tick_terminal, _man, _plan, ast, graphexec,
    graphlane, inspect, json, os, unittest)


class ArmOrderTest(_KdirCase):
    """LOOP/ARM-ORDER — the single property no per-helper test can see."""

    def _spy_arms(self, overrides=None):
        """Replace every arm with a recorder. Returns the shared `calls` list."""
        calls = []
        overrides = overrides or {}

        def make(name):
            def spy(*_a, **_k):
                calls.append(name)
                if name in overrides:
                    return overrides[name]
                return ARM_PASSTHROUGH[name]
            return spy

        for name in ARM_ORDER:
            real = getattr(graphexec, name)
            self.addCleanup(setattr, graphexec, name, real)
            setattr(graphexec, name, make(name))
        return calls

    def test_one_tick_calls_the_six_arms_in_the_scheduler_priority_order(self):
        calls = self._spy_arms()
        self.execute(_man(["a"]), tick_ceiling=1)
        self.assertEqual(tuple(calls), ARM_ORDER)

    def test_two_ticks_repeat_the_same_sequence(self):
        calls = self._spy_arms()
        self.execute(_man(["a"]), tick_ceiling=2)
        self.assertEqual(tuple(calls), ARM_ORDER * 2)

    def test_a_halt_or_pause_envelope_stops_every_later_arm(self):
        calls = self._spy_arms(
            {"_graph_tick_halt_or_pause": {"status": "halt", "reason": "x"}})
        res = self.execute(_man(["a"]), tick_ceiling=3)
        self.assertEqual(res["reason"], "x")
        self.assertEqual(tuple(calls),
                         ("_graph_tick_statuses", "_graph_tick_halt_or_pause"))

    def test_a_finalize_envelope_stops_every_later_arm(self):
        calls = self._spy_arms(
            {"_graph_tick_finalize_or_merge": ({"status": "halt",
                                                "reason": "y"}, False)})
        res = self.execute(_man(["a"]), tick_ceiling=3)
        self.assertEqual(res["reason"], "y")
        self.assertEqual(tuple(calls), ARM_ORDER[:3])

    def test_an_again_signal_restarts_the_tick_without_reaching_terminal(self):
        # `again` is the re-entry that does NOT advance `waves`: the loop must
        # go back to `statuses`, never on to `terminal`.
        state = {"n": 0}
        real = graphexec._graph_tick_finalize_or_merge

        def once(*_a, **_k):
            state["n"] += 1
            return (None, True) if state["n"] == 1 else (None, False)

        graphexec._graph_tick_finalize_or_merge = once
        self.addCleanup(setattr, graphexec, "_graph_tick_finalize_or_merge",
                        real)
        calls = []
        for name in ("_graph_tick_statuses", "_graph_tick_terminal"):
            r = getattr(graphexec, name)
            self.addCleanup(setattr, graphexec, name, r)

            def make(nm, orig):
                def spy(*a, **k):
                    calls.append(nm)
                    return orig(*a, **k)
                return spy
            setattr(graphexec, name, make(name, r))
        self.execute(_man([]), tick_ceiling=3)
        # statuses ran twice (the re-entry) before terminal ran once.
        self.assertEqual(calls, ["_graph_tick_statuses", "_graph_tick_statuses",
                                 "_graph_tick_terminal"])

    def test_a_terminal_envelope_stops_select_and_spawn(self):
        calls = self._spy_arms(
            {"_graph_tick_terminal": {"status": "complete", "reason": "z"}})
        res = self.execute(_man(["a"]), tick_ceiling=3)
        self.assertEqual(res["reason"], "z")
        self.assertEqual(tuple(calls), ARM_ORDER[:4])

    def test_a_select_envelope_stops_spawn(self):
        calls = self._spy_arms(
            {"_graph_tick_select": (None, {"status": "blocked",
                                           "reason": "w"})})
        res = self.execute(_man(["a"]), tick_ceiling=3)
        self.assertEqual(res["reason"], "w")
        self.assertEqual(tuple(calls), ARM_ORDER[:5])

    def test_the_parents_source_calls_the_arms_in_the_declared_order(self):
        """A second, independent reading of the same property: the ORDER of the
        arm names as they appear in the parent's own source. The spy test proves
        the runtime sequence for one reachable tick; this proves the code shape,
        so a future arm added in the wrong place is caught even if no fixture
        reaches it."""
        tree = ast.parse(inspect.getsource(_graph_execute))
        seen = [n.func.id for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id in ARM_ORDER]
        self.assertEqual(tuple(seen), ARM_ORDER)


class SeamsTest(unittest.TestCase):
    """SEAMS/ZERO."""

    def test_none_resolves_each_seam_to_its_shipped_default(self):
        spawn, merge, prepare, ceiling = _graph_execute_seams(
            None, None, None, None)
        self.assertIs(spawn, graphexec._graph_drive_lane)
        self.assertIs(merge, graphexec._graph_merge_lane)
        self.assertIs(prepare, graphexec._graph_prepare_lane)
        self.assertEqual(ceiling, graphexec.GRAPH_TICK_CEILING)

    def test_an_injected_seam_wins(self):
        s, m, p, _c = _graph_execute_seams("S", "M", "P", 5)
        self.assertEqual((s, m, p), ("S", "M", "P"))

    def test_a_ceiling_of_zero_is_zero_and_not_the_maximum(self):
        # `or` would turn a request for zero waves into a request for the max.
        self.assertEqual(_graph_execute_seams(None, None, None, 0)[3], 0)


class RunResultTest(unittest.TestCase):
    """LOOP/COUNTERS."""

    def test_it_stamps_every_counter_from_the_state_dict(self):
        state = {"waves": 3, "dispatched": ["a"], "merged": [{"feature": "a"}]}
        env = _graph_run_result(state, "halt", "why", node="a")
        self.assertEqual(env["waves"], 3)
        self.assertEqual(env["dispatched"], ["a"])
        self.assertEqual(env["merged"], [{"feature": "a"}])
        self.assertEqual(env["node"], "a")
        self.assertEqual(env["status"], "halt")


class DeadlineTest(unittest.TestCase):
    """DEADLINE/SEAM."""

    def test_no_clock_means_no_origin_reading(self):
        self.assertIsNone(_graph_deadline_start(None))

    def test_an_injected_clock_is_read_once_for_the_origin(self):
        calls = []
        self.assertEqual(_graph_deadline_start(
            lambda: calls.append(1) or 100.0), 100.0)
        self.assertEqual(len(calls), 1)

    def test_the_guard_is_disabled_without_a_clock(self):
        self.assertFalse(_graph_deadline_exceeded(None, 0, None))
        self.assertFalse(_graph_deadline_exceeded(None, 48, 0))

    def test_the_guard_is_disabled_without_a_deadline(self):
        self.assertFalse(_graph_deadline_exceeded(lambda: 1e9, None, 0.0))

    def test_the_guard_fires_once_the_deadline_is_passed(self):
        # 2 hours of budget, 3 hours elapsed.
        self.assertTrue(_graph_deadline_exceeded(lambda: 3 * 3600.0, 2, 0.0))

    def test_the_guard_does_not_fire_before_the_deadline(self):
        self.assertFalse(_graph_deadline_exceeded(lambda: 1 * 3600.0, 2, 0.0))

    def test_the_boundary_is_inclusive(self):
        self.assertTrue(_graph_deadline_exceeded(lambda: 2 * 3600.0, 2, 0.0))

    def test_a_zero_deadline_with_a_clock_fires_immediately_at_the_helper(self):
        # `deadline_hours=0` must NOT be read as "disabled": the disable switch
        # is None. `if not deadline_hours` would get this backwards and turn the
        # tightest possible bound into no bound at all.
        self.assertTrue(_graph_deadline_exceeded(lambda: 0.0, 0, 0.0))


class DeadlineWiringTest(_KdirCase):
    """DEADLINE/SEAM — the guard is CALLED by the loop, not merely correct.

    Found by the wave-6 non-vacuity sweep: `DeadlineTest` exercised
    `_graph_deadline_exceeded` directly and every one of its rows stayed green
    when the loop's CALL to it was disabled. A helper that is right and never
    invoked is exactly the state this wave found the guard in — its body was
    `pass` — so testing the helper alone would have re-created the defect one
    level up."""

    def test_an_injected_clock_past_the_deadline_halts_the_run(self):
        self.seed("a", status="draft")
        ticks = iter([0.0, 10 * 3600.0, 20 * 3600.0, 30 * 3600.0])
        res = self.execute(_man(["a"]), clock=lambda: next(ticks),
                           deadline_hours=1, tick_ceiling=5)
        self.assertEqual(res["status"], "halt")
        self.assertEqual(res["reason"], "deadline-exceeded")

    def test_an_injected_clock_inside_the_deadline_does_not_halt(self):
        self.seed("a", status="draft")
        res = self.execute(_man([]), clock=lambda: 0.0, deadline_hours=48)
        self.assertEqual(res["reason"], "empty")

    def test_no_clock_leaves_the_run_unbounded_by_wall_clock(self):
        # The seam's whole determinism claim: with clock=None the deadline is
        # inert even at zero hours.
        self.seed("a", status="draft")
        res = self.execute(_man(["a"]), deadline_hours=0, tick_ceiling=0)
        self.assertEqual(res["reason"], "tick-ceiling-exceeded")

    def test_the_guard_is_checked_before_the_first_status_read(self):
        # The deadline is a WHOLE-RUN stop: it must precede the tick's work, or
        # an over-deadline run still pays for one more full recompute.
        calls = []
        real = graphexec._graph_tick_statuses
        graphexec._graph_tick_statuses = lambda *a, **k: (
            calls.append(1) or real(*a, **k))
        self.addCleanup(setattr, graphexec, "_graph_tick_statuses", real)
        self.seed("a", status="draft")
        # The FIRST read is the origin (`_graph_deadline_start`); the second is
        # the loop's check. A constant clock would make elapsed 0 and prove
        # nothing — which is how this test failed the first time it was written.
        ticks = iter([0.0, 1e9, 1e9])
        res = self.execute(_man(["a"]), clock=lambda: next(ticks),
                           deadline_hours=1, tick_ceiling=3)
        self.assertEqual(res["reason"], "deadline-exceeded")
        self.assertEqual(calls, [], "a tick recomputed after the deadline blew")


class ExecuteEndToEndTest(_KdirCase):
    """The loop as a whole, over the seams — the outcomes an operator sees."""

    def test_an_empty_graph_completes(self):
        res = self.execute(_man([]))
        self.assertEqual((res["status"], res["reason"]), ("complete", "empty"))

    def test_a_tick_ceiling_of_zero_ticks_nothing(self):
        self.seed("a", status="draft")
        res = self.execute(_man(["a"]), tick_ceiling=0)
        self.assertEqual(res["reason"], "tick-ceiling-exceeded")
        self.assertEqual(res["waves"], 0)
        self.assertEqual(res["dispatched"], [])

    def test_a_wave_dispatches_and_the_counters_follow(self):
        self.seed("a", status="draft")
        dispatched = []

        def spawn(rec, f):
            dispatched.append(f)
            self.seed(f, status="done", done_markers=2)
            self.terminal(f, "completed")
            return None

        def prepare(reg, f, *_a):
            reg.setdefault("lanes", {})[f] = {"feature": f, "merged": False}
            return "created", None

        def merge(reg, f, *_a, **_k):
            reg["lanes"][f]["merged"] = True
            return "merged"

        res = self.execute(_man(["a"]), spawn=spawn, prepare=prepare,
                           merge=merge)
        self.assertEqual((res["status"], res["reason"]),
                         ("complete", "all-done"))
        self.assertEqual(dispatched, ["a"])
        self.assertEqual(res["dispatched"], ["a"])
        self.assertEqual(res["merged"], [{"feature": "a", "status": "merged"}])

    def test_an_unfinalizable_straggler_halts_rather_than_spinning(self):
        # FINALIZE/CLOSED. The straggler's plan still has an OPEN marker, so
        # running->done is refused forever; the arm re-enters WITHOUT advancing
        # `waves`, so only a fail-closed HALT terminates this.
        self.seed("a", status="running", claim="lane@host %s" % NOW,
                  open_markers=1, done_markers=1)
        self.terminal("a", "completed")
        res = self.execute(_man(["a"]))
        self.assertEqual(res["status"], "halt")
        self.assertEqual(res["reason"], "unfinalizable-straggler:a")

    def test_a_merge_whose_disposition_halts_stops_the_run(self):
        self.seed("a", status="done")
        self.lanes({"a": False})
        res = self.execute(_man(["a"]), merge=lambda *a, **k: "halt-gate")
        self.assertEqual(res["status"], "halt")
        self.assertEqual(res["reason"], "merge:halt-gate")
        self.assertEqual(res["merged"],
                         [{"feature": "a", "status": "halt-gate"}])

    def test_a_self_extension_node_halts_the_whole_run(self):
        self.seed("a", status="draft", self_ext=True)
        res = self.execute(_man(["a"]))
        self.assertEqual(res["reason"], "awaiting-manual-selfext:a")

    def test_a_kill_switch_halts_before_anything_is_read(self):
        var = sorted(GRAPH_KILL_SWITCH_VARS)[0]
        res = self.execute(_man(["a"]), check_env=True, environ={var: "1"})
        self.assertEqual(res["reason"], "orchestrator-kill-switch")


class MergeProgressInvariantTest(unittest.TestCase):
    """MERGE/PROGRESS — the CROSS-MODULE invariant the merge arm rests on.

    `_graph_tick_finalize_or_merge`'s merge branch ends in a re-entry that does
    NOT advance `waves`, so GRAPH_TICK_CEILING cannot bound it. Nothing in
    `graphexec` stops that loop; what stops it is that `_graph_merge_lane` marks
    `rec["merged"]` before returning any status whose disposition is not HALT, so
    the node's status advances to DONE and the arm is not re-entered on it.

    That is an invariant of a DIFFERENT module, held by no test until this one.
    It is pinned here, next to the loop that depends on it, rather than in
    `graphlane`'s own file: the property is not "merge sets a flag", it is "the
    tick loop terminates", and a reader who breaks it must be brought to the
    reason. Discovered while building this wave's capture harness — driving the
    injectable seam to `skipped`-without-merging hangs the executor outright."""

    def test_every_non_halt_return_of_the_shipped_merge_marks_the_lane(self):
        # Read the shipped function's own source and require that no `return`
        # of a non-HALT status can be reached without `rec["merged"]` being set
        # or already true. The three HALT statuses are exempt: they terminate the
        # run, so re-entry is impossible.
        from agentware_lib.features.graphdag.graphconst import (
            _GRAPH_DISPOSITION_TABLE, GRAPH_HALT)
        src = inspect.getsource(graphlane._graph_merge_lane)
        tree = ast.parse(src.lstrip())
        returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return)]
        statuses = [n.value.value for n in returns
                    if isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str)]
        self.assertTrue(statuses, "the merge function returned no literal status")
        non_halt = [s for s in statuses
                    if _GRAPH_DISPOSITION_TABLE.get(s) != GRAPH_HALT]
        self.assertEqual(sorted(non_halt), ["merged", "skipped"],
                         "a NEW non-HALT merge status appeared. The tick loop's "
                         "merge arm re-enters without advancing `waves`, so any "
                         "such status MUST leave the lane marked merged or the "
                         "executor spins forever.")
        # `skipped` is returned only on the already-merged branch, and `merged`
        # only after the flag is set. Both are read off the source so a future
        # edit that moves the assignment is caught.
        self.assertIn('if rec.get("merged"):\n        return "skipped"', src)
        self.assertLess(src.index('rec["merged"] = True'),
                        src.index('return "merged"'))

    def test_the_disposition_table_maps_every_shipped_merge_status(self):
        from agentware_lib.features.graphdag.graphconst import (
            _GRAPH_DISPOSITION_TABLE)
        src = inspect.getsource(graphlane._graph_merge_lane)
        tree = ast.parse(src.lstrip())
        statuses = {n.value.value for n in ast.walk(tree)
                    if isinstance(n, ast.Return)
                    and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str)}
        missing = sorted(s for s in statuses
                         if s not in _GRAPH_DISPOSITION_TABLE)
        self.assertEqual(missing, [],
                         "a merge status with no disposition falls through the "
                         "table's fail-closed default rather than being decided")


if __name__ == "__main__":
    unittest.main()
