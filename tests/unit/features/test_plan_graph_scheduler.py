"""TestSchedulerPure, TestNodeStatus -- split from `test_plan_graph` (task 58).

See `tests.unit.features._plan_graph_support` for the
shared header.
"""

from tests.unit.features._plan_graph_support import (  # noqa: F401
    CLI, SyntheticKBTestCase, _combined, _manifest, _status_plan, os,
    unittest)


class TestSchedulerPure(unittest.TestCase):
    # --- topo_order ----------------------------------------------------------

    def test_topo_order_stable_lexical_tiebreak(self):
        # Diamond: a -> b, a -> c, b -> d, c -> d. Lexical tie-break puts b before
        # c (both ready after a). Byte-identical across repeated runs.
        man = _manifest(
            nodes=[("260722-d",), ("260722-c",), ("260722-b",), ("260722-a",)],
            edges=[("260722-a", "260722-b"), ("260722-a", "260722-c"),
                   ("260722-b", "260722-d"), ("260722-c", "260722-d")])
        nodes, forward, _rev = CLI._graph_build(man)
        order = CLI.topo_order(forward, nodes)
        self.assertEqual(order,
                         ["260722-a", "260722-b", "260722-c", "260722-d"])
        for _ in range(5):
            self.assertEqual(CLI.topo_order(forward, nodes), order)

    def test_topo_order_raises_on_cycle(self):
        man = _manifest(
            nodes=[("260722-a",), ("260722-b",)],
            edges=[("260722-a", "260722-b"), ("260722-b", "260722-a")])
        _nodes, forward, _rev = CLI._graph_build(man)
        with self.assertRaises(ValueError):
            CLI.topo_order(forward, ["260722-a", "260722-b"])

    def test_topo_order_orphans_are_lexical(self):
        man = _manifest(nodes=[("260722-z",), ("260722-a",), ("260722-m",)])
        nodes, forward, _rev = CLI._graph_build(man)
        self.assertEqual(CLI.topo_order(forward, nodes),
                         ["260722-a", "260722-m", "260722-z"])

    # --- write-set overlap ---------------------------------------------------

    def test_write_sets_overlap_exact_file(self):
        self.assertTrue(CLI._write_sets_overlap(
            ["scripts/agentware"], ["scripts/agentware", "docs/x.md"]))

    def test_write_sets_disjoint(self):
        self.assertFalse(CLI._write_sets_overlap(["docs/a.md"], ["docs/b.md"]))

    def test_write_sets_glob_cover(self):
        # A `pkg/**` un-narrowable sentinel covers any path under pkg/ and the
        # bare dir itself.
        self.assertTrue(CLI._write_sets_overlap(["pkg/**"], ["pkg/sub/x.py"]))
        self.assertTrue(CLI._write_sets_overlap(["pkg/**"], ["pkg"]))
        self.assertFalse(CLI._write_sets_overlap(["pkg/**"], ["other/x.py"]))

    # --- _graph_derive_resource_edges: the B1/A2/A1 clique -------------------

    def _assert_single_line(self, nodes, forward):
        """Assert the combined graph linearizes to ONE chain: a unique topo order
        in which every consecutive pair is directly connected."""
        order = CLI.topo_order(forward, nodes)
        succ = {n: {d for (_k, d) in forward.get(n, [])} for n in nodes}
        for earlier, later in zip(order, order[1:]):
            self.assertIn(later, succ[earlier],
                          "%s and %s are not directly ordered -> not a single "
                          "line (order=%s)" % (earlier, later, order))

    def test_same_file_clique_serializes_to_single_line(self):
        # Three plans ALL writing scripts/agentware, no logical edges: the derived
        # resource edges must serialize them into one line (deterministic).
        ws = ["scripts/agentware"]
        man = _manifest(nodes=[("260722-a1", "none", False, ws),
                               ("260722-a2", "none", False, ws),
                               ("260722-b1", "none", False, ws)])
        derived = CLI._graph_derive_resource_edges(man)
        self.assertTrue(all(e["kind"] == "resource" and e["derived"]
                            for e in derived))
        nodes, forward, _rev = _combined(man, derived)
        self.assertEqual(CLI.find_cycles(forward), [])
        self._assert_single_line(nodes, forward)

    def test_clique_with_adversarial_lexically_descending_logical_edge(self):
        # THE audit case: a same-file clique with a logical edge that runs
        # lexically DESCENDING (b1 -> a1, i.e. later-name -> earlier-name). The
        # pairwise 'logical-first-else-lexical' rule would synthesize a cycle;
        # orienting resource edges along ONE topo linearization must still yield a
        # single acyclic line.
        ws = ["scripts/agentware"]
        man = _manifest(
            nodes=[("260722-a1", "none", False, ws),
                   ("260722-a2", "none", False, ws),
                   ("260722-b1", "none", False, ws)],
            edges=[("260722-b1", "260722-a1")])  # lexically descending logical
        derived = CLI._graph_derive_resource_edges(man)
        nodes, forward, _rev = _combined(man, derived)
        self.assertEqual(CLI.find_cycles(forward), [])
        self._assert_single_line(nodes, forward)
        # The declared logical edge must be RESPECTED (b1 before a1).
        order = CLI.topo_order(forward, nodes)
        self.assertLess(order.index("260722-b1"), order.index("260722-a1"))

    def test_derive_is_deterministic_and_byte_identical(self):
        ws = ["scripts/agentware"]
        man = _manifest(nodes=[("260722-b1", "none", False, ws),
                               ("260722-a2", "none", False, ws),
                               ("260722-a1", "none", False, ws)])
        first = CLI._graph_derive_resource_edges(man)
        for _ in range(5):
            self.assertEqual(CLI._graph_derive_resource_edges(man), first)

    def test_disjoint_write_sets_derive_no_edges(self):
        man = _manifest(nodes=[("260722-a", "none", False, ["docs/a.md"]),
                               ("260722-b", "none", False, ["docs/b.md"])])
        self.assertEqual(CLI._graph_derive_resource_edges(man), [])

    def test_derive_skips_already_declared_pairs(self):
        # A logical edge already orders the overlapping pair -> no redundant
        # resource edge added.
        ws = ["scripts/agentware"]
        man = _manifest(nodes=[("260722-a", "none", False, ws),
                               ("260722-b", "none", False, ws)],
                        edges=[("260722-a", "260722-b")])
        self.assertEqual(CLI._graph_derive_resource_edges(man), [])

    # --- _graph_ready_set ----------------------------------------------------

    def test_ready_set_respects_deps_and_done_merged(self):
        man = _manifest(
            nodes=[("260722-a",), ("260722-b",), ("260722-c",)],
            edges=[("260722-a", "260722-b"), ("260722-b", "260722-c")])
        graph = CLI._graph_build(man)
        # nothing run yet -> only the root a is ready
        self.assertEqual(CLI._graph_ready_set(graph, {}), ["260722-a"])
        # a done+merged -> b unlocks; c still blocked (b not done)
        self.assertEqual(
            CLI._graph_ready_set(graph, {"260722-a": CLI.GRAPH_NODE_DONE}),
            ["260722-b"])

    def test_ready_set_failed_pred_leaves_dependent_pending(self):
        man = _manifest(nodes=[("260722-a",), ("260722-b",)],
                        edges=[("260722-a", "260722-b")])
        graph = CLI._graph_build(man)
        # a FAILED (not done) -> b is NOT ready (stays pending, never cancelled)
        self.assertEqual(
            CLI._graph_ready_set(graph, {"260722-a": CLI.GRAPH_NODE_FAILED}),
            [])

    def test_ready_set_running_node_not_redispatched(self):
        man = _manifest(nodes=[("260722-a",), ("260722-b",)])
        graph = CLI._graph_build(man)
        self.assertEqual(
            CLI._graph_ready_set(graph, {"260722-a": CLI.GRAPH_NODE_RUNNING}),
            ["260722-b"])

    def test_ready_set_diamond_waits_for_both(self):
        # d needs both b and c; both need a. d unlocks only when BOTH done.
        man = _manifest(
            nodes=[("260722-a",), ("260722-b",), ("260722-c",), ("260722-d",)],
            edges=[("260722-a", "260722-b"), ("260722-a", "260722-c"),
                   ("260722-b", "260722-d"), ("260722-c", "260722-d")])
        graph = CLI._graph_build(man)
        st = {"260722-a": CLI.GRAPH_NODE_DONE, "260722-b": CLI.GRAPH_NODE_DONE}
        self.assertEqual(CLI._graph_ready_set(graph, st), ["260722-c"])
        st["260722-c"] = CLI.GRAPH_NODE_DONE
        self.assertEqual(CLI._graph_ready_set(graph, st), ["260722-d"])

    def test_ready_set_byte_identical(self):
        man = _manifest(
            nodes=[("260722-a",), ("260722-b",), ("260722-c",)],
            edges=[("260722-a", "260722-c"), ("260722-b", "260722-c")])
        graph = CLI._graph_build(man)
        first = CLI._graph_ready_set(graph, {})
        for _ in range(5):
            self.assertEqual(CLI._graph_ready_set(graph, {}), first)

    # --- _graph_disposition (the full table + fail-closed) -------------------

    def test_disposition_table(self):
        table = {
            "completed": CLI.GRAPH_CONTINUE,
            "hit_max_iterations": CLI.GRAPH_BLOCK_SUBTREE,
            "post_hook_failure": CLI.GRAPH_BLOCK_SUBTREE,
            "pre_hook_abort": CLI.GRAPH_BLOCK_SUBTREE,
            "adversarial_review_blocked": CLI.GRAPH_HALT,
            "blocked_on_ambiguity": CLI.GRAPH_HALT,
            "quota_throttled": CLI.GRAPH_PAUSE,
            "merged": CLI.GRAPH_CONTINUE,
            "skipped": CLI.GRAPH_CONTINUE,
            "halt-package": CLI.GRAPH_HALT,
            "halt-kb": CLI.GRAPH_HALT,
            "halt-gate": CLI.GRAPH_HALT,
            "self_extension_reached": CLI.GRAPH_HALT,
            "manifest_mutated": CLI.GRAPH_HALT,
        }
        for outcome, expect in table.items():
            self.assertEqual(CLI._graph_disposition(outcome), expect,
                             "disposition(%r) should be %s" % (outcome, expect))

    def test_disposition_adversarial_review_blocked_not_unknown(self):
        # The audit HIGH: adversarial_review_blocked must HALT (R-EXEC-06), never
        # collapse to an unknown/continue.
        self.assertEqual(
            CLI._graph_disposition("adversarial_review_blocked"), CLI.GRAPH_HALT)

    def test_disposition_unknown_fails_closed_to_halt(self):
        self.assertEqual(CLI._graph_disposition("who_knows"), CLI.GRAPH_HALT)
        self.assertEqual(CLI._graph_disposition(""), CLI.GRAPH_HALT)
        self.assertEqual(CLI._graph_disposition(None), CLI.GRAPH_HALT)

    def test_disposition_premise_falsified_load_bearing_split(self):
        # node-local premise falsification -> block only the subtree
        self.assertEqual(
            CLI._graph_disposition("premise_falsified",
                                   node={"self_extension": False}),
            CLI.GRAPH_BLOCK_SUBTREE)
        # load-bearing (self-extension) shared-premise falsification -> HALT
        self.assertEqual(
            CLI._graph_disposition("premise_falsified",
                                   node={"self_extension": True}),
            CLI.GRAPH_HALT)
        self.assertEqual(
            CLI._graph_disposition("premise_falsified",
                                   node={"load_bearing": True}),
            CLI.GRAPH_HALT)

class TestNodeStatus(SyntheticKBTestCase):
    """Task 4: `_graph_node_status` combines the plan header, the RAW terminal
    outcome (never derive_outcome), the lane outcome, and a per-node watchdog into
    the record the scheduler consumes. All fixtures pin `--now` for determinism."""

    NOW = "2026-07-24T12:00:00Z"

    def _seed(self, feature, **kw):
        path = os.path.join(self.kdir, "work", feature, "plan.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(_status_plan(**kw))

    def _terminals(self, feature, outcomes):
        """Write metrics.jsonl `terminal` rows (in order) for `feature`."""
        import json as _json
        logs = os.path.join(self.kdir, "logs")
        os.makedirs(logs, exist_ok=True)
        with open(os.path.join(logs, "metrics.jsonl"), "w", encoding="utf-8") as f:
            for oc in outcomes:
                f.write(_json.dumps({"event": "terminal", "feature": feature,
                                     "outcome": oc, "ts": self.NOW}) + "\n")

    def _lanes(self, feature, *, merged=False):
        return {"lanes": {feature: {"feature": feature, "merged": merged,
                                    "kb_path": "/nope", "worktree": "/nope"}}}

    def _status(self, feature, **kw):
        node = kw.pop("node", None) or {"feature": feature}
        node.setdefault("feature", feature)
        kw.setdefault("now", self.NOW)
        return CLI._graph_node_status(self.kdir, node, **kw)

    # --- non-running plan states ---------------------------------------------
    def test_draft_is_pending(self):
        self._seed("260722-a", status="draft")
        r = self._status("260722-a")
        self.assertEqual(r["status"], CLI.GRAPH_NODE_PENDING)

    def test_ready_is_pending(self):
        self._seed("260722-a", status="ready")
        self.assertEqual(self._status("260722-a")["status"],
                         CLI.GRAPH_NODE_PENDING)

    def test_done_merged_is_done(self):
        self._seed("260722-a", status="done")
        r = self._status("260722-a", lanes=self._lanes("260722-a", merged=True))
        self.assertEqual(r["status"], CLI.GRAPH_NODE_DONE)
        self.assertEqual(r["disposition"], CLI.GRAPH_CONTINUE)
        self.assertTrue(r["merged"])

    def test_done_unmerged_awaits_merge(self):
        self._seed("260722-a", status="done")
        r = self._status("260722-a", lanes=self._lanes("260722-a", merged=False))
        # done but NOT merged is NOT dep-satisfying — it awaits the merge-queue.
        self.assertEqual(r["status"], CLI.GRAPH_NODE_RUNNING)
        self.assertEqual(r["detail"], CLI.GRAPH_DETAIL_AWAITING_MERGE)

    def test_selfext_done_no_lane_is_dep_satisfying(self):
        # A self-extension node is run by the OPERATOR directly in the main
        # checkout (R-PKG-03) — never in a fanout lane — so on resume its plan is
        # `done` with NO lane record. It must resolve to DONE (dep-satisfying), NOT
        # AWAITING_MERGE (which would route a lane-less node into the merge-queue,
        # return 'halt-package', and HALT the graph forever). Audit finding.
        self._seed("260722-se", status="done", self_ext=True,
                   open_markers=0, done_markers=2)
        node = {"feature": "260722-se", "self_extension": True}
        r = CLI._graph_node_status(self.kdir, node, self.NOW,
                                   lanes={"lanes": {}})
        self.assertEqual(r["status"], CLI.GRAPH_NODE_DONE)
        self.assertEqual(r["disposition"], CLI.GRAPH_CONTINUE)
        self.assertFalse(r["merged"])

    def test_nonselfext_done_no_lane_still_awaits_merge(self):
        # The relaxation is SCOPED to self-extension nodes only: a normal node is
        # always lane-run, so done + no-lane still awaits the merge-queue (unchanged).
        self._seed("260722-a", status="done", open_markers=0, done_markers=2)
        node = {"feature": "260722-a", "self_extension": False}
        r = CLI._graph_node_status(self.kdir, node, self.NOW, lanes={"lanes": {}})
        self.assertEqual(r["status"], CLI.GRAPH_NODE_RUNNING)
        self.assertEqual(r["detail"], CLI.GRAPH_DETAIL_AWAITING_MERGE)

    def test_superseded_blocks(self):
        self._seed("260722-a", status="superseded")
        r = self._status("260722-a")
        self.assertEqual(r["status"], CLI.GRAPH_NODE_BLOCKED)
        self.assertEqual(r["disposition"], CLI.GRAPH_BLOCK_SUBTREE)

    def test_cancelled_blocks(self):
        self._seed("260722-a", status="cancelled")
        self.assertEqual(self._status("260722-a")["status"],
                         CLI.GRAPH_NODE_BLOCKED)

    def test_duplicate_header_degenerate(self):
        self._seed("260722-a", status="running", dup=True)
        r = self._status("260722-a")
        self.assertEqual(r["status"], CLI.GRAPH_NODE_BLOCKED)
        self.assertEqual(r["detail"], CLI.GRAPH_DETAIL_DEGENERATE)
        self.assertEqual(r["disposition"], CLI.GRAPH_BLOCK_SUBTREE)

    def test_missing_plan_halts(self):
        r = self._status("260722-ghost")
        self.assertEqual(r["status"], CLI.GRAPH_NODE_HALT)
        self.assertEqual(r["detail"], CLI.GRAPH_DETAIL_PLAN_MISSING)
        self.assertEqual(r["disposition"], CLI.GRAPH_HALT)

    # --- running + terminal outcome ------------------------------------------
    def test_running_no_terminal_is_running(self):
        self._seed("260722-a", status="running",
                   claim="alice@host %s" % self.NOW)
        self.assertEqual(self._status("260722-a")["status"],
                         CLI.GRAPH_NODE_RUNNING)

    def test_done_unfinalized_straggler(self):
        # Loop emitted `completed` but the plan header is still running.
        self._seed("260722-a", status="running")
        self._terminals("260722-a", ["completed"])
        r = self._status("260722-a")
        self.assertEqual(r["status"], CLI.GRAPH_NODE_RUNNING)
        self.assertEqual(r["detail"], CLI.GRAPH_DETAIL_DONE_UNFINALIZED)
        self.assertEqual(r["raw_outcome"], "completed")

    def test_adversarial_review_blocked_halts(self):
        # THE key Task-4 assertion: the raw outcome is preserved by name and
        # resolves to HALT (R-EXEC-06) — never `unknown`/running.
        self._seed("260722-a", status="running")
        self._terminals("260722-a", ["adversarial_review_blocked"])
        r = self._status("260722-a")
        self.assertEqual(r["status"], CLI.GRAPH_NODE_HALT)
        self.assertEqual(r["disposition"], CLI.GRAPH_HALT)
        self.assertEqual(r["raw_outcome"], "adversarial_review_blocked")
        self.assertEqual(r["detail"], "adversarial_review_blocked")

    def test_adversarial_blocked_survives_where_derive_outcome_drops_it(self):
        # Contrast: derive_outcome collapses this to `unknown`; the raw read keeps
        # it, which is the whole reason for Task 4's separate resolver (audit HIGH).
        # An open marker keeps derive_outcome's artifact fallback at `unknown`
        # (it asserts `completed` only when every marker is ✅), isolating the
        # metrics-read behavior under test.
        self._seed("260722-a", status="running", open_markers=1, done_markers=1)
        self._terminals("260722-a", ["adversarial_review_blocked"])
        self.assertEqual(CLI.derive_outcome(self.kdir, "260722-a")["outcome"],
                         "unknown")
        self.assertEqual(
            CLI._graph_raw_terminal_outcome(self.kdir, "260722-a"),
            "adversarial_review_blocked")

    def test_blocked_on_ambiguity_halts(self):
        self._seed("260722-a", status="running")
        self._terminals("260722-a", ["blocked_on_ambiguity"])
        self.assertEqual(self._status("260722-a")["status"], CLI.GRAPH_NODE_HALT)

    def test_contained_failure_hit_max_iterations(self):
        self._seed("260722-a", status="running")
        self._terminals("260722-a", ["hit_max_iterations"])
        r = self._status("260722-a")
        self.assertEqual(r["status"], CLI.GRAPH_NODE_FAILED)
        self.assertEqual(r["disposition"], CLI.GRAPH_BLOCK_SUBTREE)
        # failure is NEVER written to the plan state — it honestly stays running.
        self.assertEqual(r["plan_state"], "running")

    def test_premise_falsified_load_bearing_halts(self):
        self._seed("260722-a", status="running", self_ext=True)
        self._terminals("260722-a", ["premise_falsified"])
        r = self._status("260722-a", node={"feature": "260722-a",
                                            "self_extension": True})
        self.assertEqual(r["status"], CLI.GRAPH_NODE_HALT)
        self.assertEqual(r["disposition"], CLI.GRAPH_HALT)

    def test_premise_falsified_node_local_blocks(self):
        self._seed("260722-a", status="running")
        self._terminals("260722-a", ["premise_falsified"])
        r = self._status("260722-a", node={"feature": "260722-a",
                                            "self_extension": False})
        self.assertEqual(r["status"], CLI.GRAPH_NODE_BLOCKED)
        self.assertEqual(r["disposition"], CLI.GRAPH_BLOCK_SUBTREE)

    def test_quota_throttled_pauses(self):
        self._seed("260722-a", status="running")
        self._terminals("260722-a", ["quota_throttled"])
        r = self._status("260722-a")
        self.assertEqual(r["disposition"], CLI.GRAPH_PAUSE)
        # pause is resumable, NOT a failure — the node stays running.
        self.assertEqual(r["status"], CLI.GRAPH_NODE_RUNNING)

    def test_unknown_outcome_fails_closed_halt(self):
        self._seed("260722-a", status="running")
        self._terminals("260722-a", ["gremlin"])
        self.assertEqual(self._status("260722-a")["status"], CLI.GRAPH_NODE_HALT)

    def test_last_definite_terminal_wins_unknown_skipped(self):
        # A later `unknown` never overrides an earlier definite outcome.
        self._seed("260722-a", status="running")
        self._terminals("260722-a", ["adversarial_review_blocked", "unknown"])
        self.assertEqual(
            CLI._graph_raw_terminal_outcome(self.kdir, "260722-a"),
            "adversarial_review_blocked")

    # --- per-node wall-clock watchdog ----------------------------------------
    def test_watchdog_fires_on_stale_claim(self):
        # Claim 30h old, no terminal, node-timeout 24h -> FAILED_WATCHDOG.
        self._seed("260722-a", status="running",
                   claim="alice@host 2026-07-23T06:00:00Z")
        r = self._status("260722-a", node_timeout_hours=24)
        self.assertEqual(r["status"], CLI.GRAPH_NODE_FAILED)
        self.assertEqual(r["detail"], CLI.GRAPH_DETAIL_FAILED_WATCHDOG)
        self.assertEqual(r["disposition"], CLI.GRAPH_BLOCK_SUBTREE)

    def test_watchdog_not_fired_on_recent_claim(self):
        self._seed("260722-a", status="running",
                   claim="alice@host 2026-07-24T11:00:00Z")  # 1h old
        r = self._status("260722-a", node_timeout_hours=24)
        self.assertEqual(r["status"], CLI.GRAPH_NODE_RUNNING)

    def test_watchdog_distinct_from_stalled_claim_hours(self):
        # The node-timeout default is DISTINCT from the 72h stale-claim window.
        self.assertNotEqual(CLI.GRAPH_NODE_TIMEOUT_HOURS, CLI.STALLED_CLAIM_HOURS)
        self.assertLess(CLI.GRAPH_NODE_TIMEOUT_HOURS, CLI.STALLED_CLAIM_HOURS)

    # --- determinism ----------------------------------------------------------
    def test_now_injectable_byte_identical(self):
        self._seed("260722-a", status="running",
                   claim="alice@host 2026-07-20T00:00:00Z")
        self._terminals("260722-a", ["hit_max_iterations"])
        node = {"feature": "260722-a"}
        r1 = CLI._graph_node_status(self.kdir, node, self.NOW, node_timeout_hours=24)
        r2 = CLI._graph_node_status(self.kdir, node, self.NOW, node_timeout_hours=24)
        self.assertEqual(r1, r2)

    # --- DONE_UNFINALIZED idempotent finalizer -------------------------------
    def _with_env(self, fn):
        import contextlib
        import io
        prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                return fn()
        finally:
            if prev is None:
                os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
            else:
                os.environ["AGENTWARE_KNOWLEDGE_DIR"] = prev

    def test_finalize_unfinalized_idempotent(self):
        # A straggler (running header, all markers done, completed terminal) is
        # finalized to done via the single-writer set-state, then becomes DONE once
        # merged; a second finalize is a no-op.
        self._seed("260722-a", status="running", open_markers=0, done_markers=2)
        self._terminals("260722-a", ["completed"])
        pre = self._status("260722-a")
        self.assertEqual(pre["detail"], CLI.GRAPH_DETAIL_DONE_UNFINALIZED)

        ok, _msg = self._with_env(
            lambda: CLI._graph_finalize_unfinalized(self.kdir, "260722-a",
                                                    now=self.NOW))
        self.assertTrue(ok)
        # Header now flipped to done (single-writer); done+merged -> DONE.
        post = self._status("260722-a",
                            lanes=self._lanes("260722-a", merged=True))
        self.assertEqual(post["plan_state"], "done")
        self.assertEqual(post["status"], CLI.GRAPH_NODE_DONE)
        # Idempotent: finalizing again is a no-op success.
        ok2, msg2 = self._with_env(
            lambda: CLI._graph_finalize_unfinalized(self.kdir, "260722-a",
                                                    now=self.NOW))
        self.assertTrue(ok2)
        self.assertIn("no-op", msg2)

    def test_finalize_refuses_open_markers(self):
        # An open marker means the loop did NOT truly finish — the open-markers==0
        # gate refuses the running->done finalize.
        self._seed("260722-a", status="running", open_markers=1, done_markers=1)
        self._terminals("260722-a", ["completed"])
        ok, _msg = self._with_env(
            lambda: CLI._graph_finalize_unfinalized(self.kdir, "260722-a",
                                                    now=self.NOW))
        self.assertFalse(ok)
        # Still running — never silently forced done.
        self.assertEqual(self._status("260722-a")["plan_state"], "running")
