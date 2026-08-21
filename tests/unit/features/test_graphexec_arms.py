"""The ARM half of `graphexec`'s owning unit file — task 99 wave 6.

`test_graphexec` owns the LOOP: the arm ORDER, the seams, the deadline guard, the
run-level outcomes, and the cross-module merge-progress invariant the loop rests
on. This file owns the ARMS as individual units — the three setup stages and the
five decision arms, each driven directly with inputs no reachable tick could
produce.

The split is not thematic tidiness: the loop's property (priority order) is only
visible from the parent, and each arm's property is only visible when the arm is
called with inputs the parent would never hand it. Testing one through the other
is how both go vacuous.

See `tests.unit.features._graphexec_support` for the shared header, and
`test_graphexec`'s docstring for the property list both files answer to.

  PRE/KILL-SWITCH, PRE/CONCURRENCY, TOPO/ONCE, SELFEXT/COMPUTED, SETUP/ORDER,
  HALT/BEFORE-PAUSE, LOOP/COUNTERS (the terminal half), STALL/AMBIGUOUS,
  SELECT/RETRY, SPAWN/BARRIER.
"""

from tests.unit.features._graphexec_support import (  # noqa: F401
    ARM_ORDER, ARM_PASSTHROUGH, GRAPH_KILL_SWITCH_VARS, NOW, _KdirCase,
    _graph_deadline_exceeded, _graph_deadline_start, _graph_execute,
    _graph_execute_preflight, _graph_execute_seams, _graph_execute_selfext,
    _graph_execute_setup, _graph_execute_topology, _graph_run_result,
    _graph_tick_halt_or_pause, _graph_tick_select, _graph_tick_spawn_wave,
    _graph_tick_stalled, _graph_tick_terminal, _man, _plan, ast, graphexec,
    graphlane, inspect, json, os, unittest)


class PreflightTest(unittest.TestCase):
    """PRE/KILL-SWITCH, PRE/CONCURRENCY."""

    def test_each_kill_switch_fails_the_run_closed(self):
        for var in GRAPH_KILL_SWITCH_VARS:
            cc, halt = _graph_execute_preflight(_man(), None, {var: "1"}, True)
            self.assertIsNone(cc)
            self.assertEqual(halt["reason"], "orchestrator-kill-switch")
            self.assertIn(var, halt["offenders"])

    def test_a_blank_value_is_not_presence(self):
        var = sorted(GRAPH_KILL_SWITCH_VARS)[0]
        cc, halt = _graph_execute_preflight(_man(), None, {var: "  "}, True)
        self.assertIsNone(halt)
        self.assertEqual(cc, 3)

    def test_check_env_false_skips_the_switch_entirely(self):
        var = sorted(GRAPH_KILL_SWITCH_VARS)[0]
        cc, halt = _graph_execute_preflight(_man(), None, {var: "1"}, False)
        self.assertIsNone(halt)
        self.assertEqual(cc, 3)

    def test_the_manifest_value_is_used_when_no_override_is_given(self):
        cc, halt = _graph_execute_preflight(
            _man(concurrency=7), None, {}, False)
        self.assertIsNone(halt)
        self.assertEqual(cc, 7)

    def test_an_explicit_override_beats_the_manifest(self):
        cc, _h = _graph_execute_preflight(_man(concurrency=7), 2, {}, False)
        self.assertEqual(cc, 2)

    def test_a_missing_manifest_key_falls_back_to_the_default(self):
        cc, halt = _graph_execute_preflight({"nodes": []}, None, {}, False)
        self.assertIsNone(halt)
        self.assertEqual(cc, graphexec.GRAPH_DEFAULT_CONCURRENCY)

    def test_out_of_range_is_refused_rather_than_clamped(self):
        for bad in (0, -1, graphexec.GRAPH_CONCURRENCY_MAX + 1, 1000):
            cc, halt = _graph_execute_preflight(_man(), bad, {}, False)
            self.assertIsNone(cc, bad)
            self.assertEqual(halt["reason"], "bad-concurrency:%r" % bad)

    def test_a_bool_is_refused_even_though_it_is_an_int(self):
        for bad in (True, False):
            cc, halt = _graph_execute_preflight(_man(), bad, {}, False)
            self.assertIsNone(cc)
            self.assertIn("bad-concurrency", halt["reason"])

    def test_a_non_int_is_refused(self):
        for bad in ("3", 3.0, [3]):
            cc, halt = _graph_execute_preflight(_man(), bad, {}, False)
            self.assertIsNone(cc, bad)
            self.assertIn("bad-concurrency", halt["reason"])

    def test_the_range_endpoints_are_accepted(self):
        for good in (graphexec.GRAPH_CONCURRENCY_MIN,
                     graphexec.GRAPH_CONCURRENCY_MAX):
            cc, halt = _graph_execute_preflight(_man(), good, {}, False)
            self.assertIsNone(halt)
            self.assertEqual(cc, good)


class TopologyTest(unittest.TestCase):
    """TOPO/ONCE and the two DISTINCT cycle reasons."""

    def test_it_returns_the_combined_graph_and_a_rank(self):
        topo, halt = _graph_execute_topology(_man(["a", "b"], [("a", "b")]))
        self.assertIsNone(halt)
        self.assertEqual(sorted(topo["nodes"]), ["a", "b"])
        self.assertLess(topo["rank"]["a"], topo["rank"]["b"])
        self.assertEqual(topo["order"], ["a", "b"])

    def test_the_derived_resource_edges_are_in_the_combined_graph(self):
        topo, _h = _graph_execute_topology(
            _man(["a", "b"], write_sets={"a": ["x.py"], "b": ["x.py"]}))
        _nodes, _forward, reverse = topo["graph"]
        self.assertEqual([p for _k, p in reverse.get("b", [])], ["a"])

    def test_a_declared_cycle_and_a_derived_cycle_are_distinct_reasons(self):
        _t, halt = _graph_execute_topology(
            _man(["a", "b"], [("a", "b"), ("b", "a")]))
        self.assertTrue(halt["reason"].startswith("resource-derive-cycle:"),
                        halt["reason"])

    def test_the_write_sets_are_carried_forward(self):
        topo, _h = _graph_execute_topology(
            _man(["a"], write_sets={"a": ["x.py"]}))
        self.assertEqual(topo["write_sets"], {"a": ["x.py"]})


class SelfExtTest(_KdirCase):
    """SELFEXT/COMPUTED."""

    def test_a_manifest_claiming_false_is_overruled_by_the_live_plan(self):
        self.seed("a", status="draft", self_ext=True)
        man = _man(["a"])
        man["nodes"][0]["self_extension"] = False
        node_by_feat, self_ext = _graph_execute_selfext(self.kdir, man)
        self.assertTrue(self_ext["a"])
        self.assertTrue(node_by_feat["a"]["self_extension"],
                        "the node dict was not re-stamped, so downstream "
                        "consumers would still read the manifest's claim")

    def test_a_manifest_claiming_true_is_overruled_the_other_way(self):
        self.seed("a", status="draft", self_ext=False)
        man = _man(["a"])
        man["nodes"][0]["self_extension"] = True
        _n, self_ext = _graph_execute_selfext(self.kdir, man)
        self.assertFalse(self_ext["a"])

    def test_junk_node_rows_are_skipped(self):
        man = _man(["a"])
        man["nodes"] += ["nope", {"no_feature": 1}, {"feature": 7}]
        self.seed("a", status="draft")
        node_by_feat, self_ext = _graph_execute_selfext(self.kdir, man)
        self.assertEqual(sorted(node_by_feat), ["a"])
        self.assertEqual(sorted(self_ext), ["a"])


class SetupTest(_KdirCase):
    """SETUP/ORDER."""

    def _setup(self, manifest, **kw):
        kw.setdefault("repo", self.kdir)
        kw.setdefault("kb_repo", self.kdir)
        kw.setdefault("concurrency", None)
        kw.setdefault("integration", "integration")
        kw.setdefault("bootstrap", False)
        kw.setdefault("check_env", False)
        kw.setdefault("environ", {})
        return _graph_execute_setup(self.kdir, manifest, **kw)

    def test_it_returns_a_context_carrying_every_per_run_value(self):
        self.seed("a", status="draft")
        ctx, halt = self._setup(_man(["a"]))
        self.assertIsNone(halt)
        for key in ("graph", "nodes", "order", "rank", "write_sets", "repo",
                    "kb_repo", "cc", "reg", "integration", "node_by_feat",
                    "self_ext"):
            self.assertIn(key, ctx)

    def test_the_preflight_refusal_precedes_the_bootstrap_write(self):
        # ORDER: bootstrap is the first step that WRITES. A bad concurrency must
        # refuse before it, so a typo never creates two git branches. Proven by
        # arming a bootstrap that would EXPLODE and asking for a bad bound.
        def boom(*_a, **_k):
            raise AssertionError("bootstrap ran after a preflight refusal")

        real = graphexec._graph_bootstrap_integration
        graphexec._graph_bootstrap_integration = boom
        self.addCleanup(setattr, graphexec, "_graph_bootstrap_integration",
                        real)
        ctx, halt = self._setup(_man(["a"]), concurrency=99, bootstrap=True)
        self.assertIsNone(ctx)
        self.assertIn("bad-concurrency", halt["reason"])

    def test_a_failed_bootstrap_halts_and_names_the_error(self):
        real = graphexec._graph_bootstrap_integration
        graphexec._graph_bootstrap_integration = lambda *a, **k: (False, "nope")
        self.addCleanup(setattr, graphexec, "_graph_bootstrap_integration",
                        real)
        ctx, halt = self._setup(_man([]), bootstrap=True)
        self.assertIsNone(ctx)
        self.assertEqual(halt["reason"], "bootstrap-failed:nope")

    def test_a_malformed_lane_registry_halts(self):
        with open(os.path.join(self.fanout, "lanes.json"), "w",
                  encoding="utf-8") as f:
            f.write("{not json")
        ctx, halt = self._setup(_man([]))
        self.assertIsNone(ctx)
        self.assertTrue(halt["reason"].startswith("lane-registry:"))

    def test_a_topology_cycle_halts_before_the_lane_registry_is_read(self):
        # ORDER: topology precedes the registry read, so a cyclic manifest
        # reports the CYCLE even when the registry is also broken.
        with open(os.path.join(self.fanout, "lanes.json"), "w",
                  encoding="utf-8") as f:
            f.write("{not json")
        ctx, halt = self._setup(_man(["a", "b"], [("a", "b"), ("b", "a")]))
        self.assertIsNone(ctx)
        self.assertIn("cycle", halt["reason"])
        self.assertNotIn("lane-registry", halt["reason"])


class HaltOrPauseTest(unittest.TestCase):
    """HALT/BEFORE-PAUSE."""

    STATE = {"waves": 0, "dispatched": [], "merged": []}

    def _rec(self, **kw):
        base = {"status": None, "detail": None, "disposition": None}
        base.update(kw)
        return base

    def test_a_halted_node_produces_a_halt_naming_its_detail(self):
        order = ["a"]
        records = {"a": self._rec(status=graphexec.GRAPH_NODE_HALT,
                                  detail="adversarial_review_blocked")}
        statuses = {"a": graphexec.GRAPH_NODE_HALT}
        env = _graph_tick_halt_or_pause(dict(self.STATE), order, records,
                                        statuses)
        self.assertEqual(env["status"], "halt")
        self.assertEqual(env["reason"], "adversarial_review_blocked")
        self.assertEqual(env["node"], "a")

    def test_a_halted_node_without_a_detail_falls_back_to_node_halt(self):
        env = _graph_tick_halt_or_pause(
            dict(self.STATE), ["a"],
            {"a": self._rec(status=graphexec.GRAPH_NODE_HALT)},
            {"a": graphexec.GRAPH_NODE_HALT})
        self.assertEqual(env["reason"], "node-halt")

    def test_a_paused_node_produces_a_pause_with_the_pending_frontier(self):
        order = ["a", "b"]
        records = {"a": self._rec(status=graphexec.GRAPH_NODE_RUNNING,
                                  disposition=graphexec.GRAPH_PAUSE),
                   "b": self._rec(status=graphexec.GRAPH_NODE_DONE)}
        statuses = {"a": graphexec.GRAPH_NODE_RUNNING,
                    "b": graphexec.GRAPH_NODE_DONE}
        env = _graph_tick_halt_or_pause(dict(self.STATE), order, records,
                                        statuses)
        self.assertEqual(env["status"], "paused")
        self.assertEqual(env["reason"], "quota-throttle:a")
        self.assertEqual(env["pending"], ["a"])

    def test_halt_outranks_pause_in_the_same_tick(self):
        order = ["p", "h"]
        records = {"h": self._rec(status=graphexec.GRAPH_NODE_HALT,
                                  detail="blocked_on_ambiguity"),
                   "p": self._rec(status=graphexec.GRAPH_NODE_RUNNING,
                                  disposition=graphexec.GRAPH_PAUSE)}
        statuses = {"h": graphexec.GRAPH_NODE_HALT,
                    "p": graphexec.GRAPH_NODE_RUNNING}
        env = _graph_tick_halt_or_pause(dict(self.STATE), order, records,
                                        statuses)
        self.assertEqual(env["status"], "halt")

    def test_the_named_node_follows_the_topo_order_not_the_dict_order(self):
        order = ["a", "b"]
        records = {k: self._rec(status=graphexec.GRAPH_NODE_HALT, detail=k)
                   for k in ("b", "a")}
        statuses = {"b": graphexec.GRAPH_NODE_HALT,
                    "a": graphexec.GRAPH_NODE_HALT}
        env = _graph_tick_halt_or_pause(dict(self.STATE), order, records,
                                        statuses)
        self.assertEqual(env["node"], "a")

    def test_a_quiet_tick_returns_none(self):
        self.assertIsNone(_graph_tick_halt_or_pause(
            dict(self.STATE), ["a"],
            {"a": self._rec(status=graphexec.GRAPH_NODE_PENDING)},
            {"a": graphexec.GRAPH_NODE_PENDING}))

    def test_a_pause_disposition_on_a_non_running_node_is_not_a_pause(self):
        # The pause arm requires BOTH: status RUNNING and disposition PAUSE. A
        # test that only set the disposition would pass with the status half
        # deleted.
        self.assertIsNone(_graph_tick_halt_or_pause(
            dict(self.STATE), ["a"],
            {"a": self._rec(status=graphexec.GRAPH_NODE_PENDING,
                            disposition=graphexec.GRAPH_PAUSE)},
            {"a": graphexec.GRAPH_NODE_PENDING}))


class TerminalAndStallTest(unittest.TestCase):
    """LOOP/COUNTERS, STALL/AMBIGUOUS."""

    STATE = {"waves": 1, "dispatched": ["a"], "merged": []}

    def test_all_done_is_complete_and_carries_the_counters(self):
        env = _graph_tick_terminal(dict(self.STATE), ["a"],
                                   {"a": graphexec.GRAPH_NODE_DONE})
        self.assertEqual((env["status"], env["reason"]), ("complete", "all-done"))
        self.assertEqual(env["dispatched"], ["a"])

    def test_an_empty_graph_is_complete_with_empty_frontier_counters(self):
        # `empty` is built by a DIRECT `_graph_result` call rather than through
        # `_graph_run_result`, preserved verbatim from the pre-split body. That
        # asymmetry is NOT a behavioural distinction and is asserted as such:
        # with no nodes the counters are empty either way, so a reader who
        # "fixes" the asymmetry changes nothing, and a reader who assumes it
        # withholds the counters is wrong.
        env = _graph_tick_terminal(dict(self.STATE), [], {})
        self.assertEqual((env["status"], env["reason"]), ("complete", "empty"))
        self.assertEqual(env["dispatched"], [])
        self.assertEqual(env["merged"], [])
        self.assertEqual(env["waves"], self.STATE["waves"])

    def test_a_partially_done_graph_is_not_terminal(self):
        self.assertIsNone(_graph_tick_terminal(
            dict(self.STATE), ["a", "b"],
            {"a": graphexec.GRAPH_NODE_DONE,
             "b": graphexec.GRAPH_NODE_PENDING}))

    def test_a_stalled_tick_with_a_running_node_halts_as_ambiguous(self):
        env = _graph_tick_stalled(
            dict(self.STATE), ["a", "b"],
            {"a": graphexec.GRAPH_NODE_DONE,
             "b": graphexec.GRAPH_NODE_RUNNING})
        self.assertEqual(env["status"], "halt")
        self.assertEqual(env["reason"], "ambiguous-running")
        self.assertEqual(env["node"], "b")
        self.assertEqual(env["pending"], ["b"])

    def test_a_stalled_tick_without_a_running_node_is_blocked_not_halted(self):
        env = _graph_tick_stalled(
            dict(self.STATE), ["a", "b"],
            {"a": graphexec.GRAPH_NODE_DONE,
             "b": graphexec.GRAPH_NODE_FAILED})
        self.assertEqual(env["status"], "blocked")
        self.assertEqual(env["reason"], "frontier-blocked")
        self.assertEqual(env["pending"], ["b"])


class SelectTest(unittest.TestCase):
    """SELECT/RETRY and the self-extension halt."""

    def _ctx(self, features, statuses, edges=(), cc=3, self_ext=None,
             write_sets=None):
        man = _man(features, edges, write_sets=write_sets)
        topo, halt = _graph_execute_topology(man)
        self.assertIsNone(halt)
        ctx = dict(topo)
        ctx.update({"cc": cc,
                    "self_ext": self_ext or {f: False for f in features}})
        return ctx, statuses

    def test_it_returns_the_dispatch_set_for_a_pending_frontier(self):
        ctx, statuses = self._ctx(["a", "b"], {"a": "pending", "b": "pending"})
        sel, env = _graph_tick_select(
            {"waves": 0, "dispatched": [], "merged": []}, ctx,
            ctx["order"], statuses, False)
        self.assertIsNone(env)
        self.assertEqual(sel["dispatch"], ["a", "b"])

    def test_a_self_extension_node_halts_instead_of_being_dispatched(self):
        ctx, statuses = self._ctx(["a"], {"a": "pending"},
                                  self_ext={"a": True})
        sel, env = _graph_tick_select(
            {"waves": 0, "dispatched": [], "merged": []}, ctx,
            ctx["order"], statuses, False)
        self.assertIsNone(sel)
        self.assertEqual(env["reason"], "awaiting-manual-selfext:a")

    def test_the_selfext_halt_outranks_dispatching_the_rest_of_the_wave(self):
        ctx, statuses = self._ctx(["ok", "se"],
                                  {"ok": "pending", "se": "pending"},
                                  self_ext={"ok": False, "se": True})
        sel, env = _graph_tick_select(
            {"waves": 0, "dispatched": [], "merged": []}, ctx,
            ctx["order"], statuses, False)
        self.assertIsNone(sel, "a wave was dispatched alongside a self-ext halt")
        self.assertIn("awaiting-manual-selfext", env["reason"])

    def test_retry_remaps_a_prior_failure_to_pending(self):
        ctx, statuses = self._ctx(["a"], {"a": graphexec.GRAPH_NODE_FAILED})
        state = {"waves": 0, "dispatched": [], "merged": []}
        sel, env = _graph_tick_select(state, ctx, ctx["order"], statuses, True)
        self.assertIsNone(env)
        self.assertEqual(sel["dispatch"], ["a"])

    def test_without_retry_a_failure_stays_parked(self):
        ctx, statuses = self._ctx(["a"], {"a": graphexec.GRAPH_NODE_FAILED})
        sel, env = _graph_tick_select(
            {"waves": 0, "dispatched": [], "merged": []}, ctx,
            ctx["order"], statuses, False)
        self.assertIsNone(sel)
        self.assertEqual(env["status"], "blocked")

    def test_retry_re_offers_a_failure_only_once(self):
        # Already dispatched this run => NOT remapped, even under --retry. This
        # is R-FAIL-04: a retry must change something, and a second identical
        # dispatch changes nothing.
        ctx, statuses = self._ctx(["a"], {"a": graphexec.GRAPH_NODE_FAILED})
        state = {"waves": 1, "dispatched": ["a"], "merged": []}
        sel, env = _graph_tick_select(state, ctx, ctx["order"], statuses, True)
        self.assertIsNone(sel)
        self.assertEqual(env["status"], "blocked")

    def test_the_concurrency_cap_bounds_the_wave(self):
        ctx, statuses = self._ctx(["a", "b", "c"],
                                  {f: "pending" for f in "abc"}, cc=2)
        sel, _env = _graph_tick_select(
            {"waves": 0, "dispatched": [], "merged": []}, ctx,
            ctx["order"], statuses, False)
        self.assertEqual(len(sel["dispatch"]), 2)

    def test_the_dispatch_order_follows_the_topo_rank(self):
        ctx, statuses = self._ctx(["z", "a"], {"z": "pending", "a": "pending"},
                                  edges=[("z", "a")], cc=1)
        sel, _env = _graph_tick_select(
            {"waves": 0, "dispatched": [], "merged": []}, ctx,
            ctx["order"], statuses, False)
        self.assertEqual(sel["dispatch"], ["z"])


class SpawnWaveTest(unittest.TestCase):
    """SPAWN/BARRIER."""

    def _ctx(self, lanes=None):
        return {"reg": {"lanes": dict(lanes or {})}, "repo": "/r",
                "kb_repo": "/k", "integration": "integration"}

    def test_each_selected_lane_is_prepared_then_spawned_then_waited(self):
        events = []

        class H(object):
            def wait(self):
                events.append("wait")

        def prepare(reg, f, *_a):
            events.append(("prepare", f))
            reg["lanes"][f] = {"feature": f, "feat_branch": "feat/%s" % f}
            return "created", None

        def spawn(rec, f):
            events.append(("spawn", f, rec.get("feat_branch")))
            return H()

        state = {"waves": 0, "dispatched": [], "merged": []}
        env = _graph_tick_spawn_wave(state, self._ctx(),
                                     {"dispatch": ["a", "b"]}, prepare, spawn)
        self.assertIsNone(env)
        self.assertEqual(state["dispatched"], ["a", "b"])
        # The BARRIER: both spawns happen before either wait.
        self.assertEqual(
            events,
            [("prepare", "a"), ("spawn", "a", "feat/a"),
             ("prepare", "b"), ("spawn", "b", "feat/b"), "wait", "wait"])

    def test_a_provisioning_failure_halts_and_names_the_node(self):
        def prepare(_reg, f, *_a):
            return (None, "worktree exists") if f == "b" else ("created", None)

        state = {"waves": 0, "dispatched": [], "merged": []}
        env = _graph_tick_spawn_wave(state, self._ctx(),
                                     {"dispatch": ["a", "b"]}, prepare,
                                     lambda rec, f: None)
        self.assertEqual(env["status"], "halt")
        self.assertEqual(env["reason"], "provision-failed:b:worktree exists")
        self.assertEqual(env["node"], "b")
        # The lane already dispatched in THIS wave is still recorded, so the
        # envelope names what is actually running.
        self.assertEqual(state["dispatched"], ["a"])

    def test_a_handle_without_wait_is_tolerated(self):
        state = {"waves": 0, "dispatched": [], "merged": []}
        for handle in (None, object(), "not-a-handle"):
            env = _graph_tick_spawn_wave(
                dict(state, dispatched=[]), self._ctx(), {"dispatch": ["a"]},
                lambda *a: ("created", None), lambda rec, f: handle)
            self.assertIsNone(env)

    def test_a_lane_missing_from_the_registry_still_gets_a_record(self):
        seen = []
        _env = _graph_tick_spawn_wave(
            {"waves": 0, "dispatched": [], "merged": []},
            {"reg": {}, "repo": "/r", "kb_repo": "/k",
             "integration": "integration"},
            {"dispatch": ["a"]}, lambda *a: ("created", None),
            lambda rec, f: seen.append(rec) or None)
        self.assertEqual(seen, [{"feature": "a"}])


if __name__ == "__main__":
    unittest.main()
