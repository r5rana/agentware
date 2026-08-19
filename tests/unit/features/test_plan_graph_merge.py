"""TestMergeGates -- split from `test_plan_graph` (task 58).

See `tests.unit.features._plan_graph_support` for the
shared header.
"""

from tests.unit.features._plan_graph_support import (  # noqa: F401
    CLI, _FakeHandle, _manifest, _status_plan, os, tempfile, unittest)


class TestMergeGates(unittest.TestCase):
    """Task 6: the EXTENDED (never weakened) per-node merge-gate chain
    (`_graph_merge_gates`) and the explicit operator-only manual checkpoint
    barrier (`graph checkpoint`)."""

    NOW = "2026-07-24T12:00:00Z"

    def setUp(self):
        import shutil
        self.kdir = tempfile.mkdtemp(prefix="agentware-t6-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.fanout_dir = tempfile.mkdtemp(prefix="agentware-t6-fanout-")
        self.addCleanup(shutil.rmtree, self.fanout_dir, True)
        self._env_prev = {}
        for k, v in (("AGENTWARE_FANOUT_DIR", self.fanout_dir),
                     ("AGENTWARE_KNOWLEDGE_DIR", self.kdir)):
            self._env_prev[k] = os.environ.get(k)
            os.environ[k] = v
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for k, v in self._env_prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # -- seeding helpers ------------------------------------------------------
    def _seed(self, feature, **kw):
        path = os.path.join(self.kdir, "work", feature, "plan.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(_status_plan(**kw))

    def _terminal(self, feature, outcome):
        import json as _json
        logs = os.path.join(self.kdir, "logs")
        os.makedirs(logs, exist_ok=True)
        with open(os.path.join(logs, "metrics.jsonl"), "a",
                  encoding="utf-8") as f:
            f.write(_json.dumps({"event": "terminal", "feature": feature,
                                 "outcome": outcome, "ts": self.NOW}) + "\n")

    def _write_manifest(self, name, nodes, edges=None, concurrency=3):
        import json as _json
        man = _manifest(nodes=nodes, edges=edges, name=name,
                        concurrency=concurrency)
        mpath = CLI._graph_manifest_path(self.kdir, name)
        os.makedirs(os.path.dirname(mpath), exist_ok=True)
        with open(mpath, "w", encoding="utf-8") as f:
            _json.dump(man, f)
        return man

    def _args(self, **kw):
        import types
        kw.setdefault("format", "text")
        kw.setdefault("now", self.NOW)
        return types.SimpleNamespace(**kw)

    def _ckpt_log(self, feature):
        with open(os.path.join(self.kdir, "work", feature, "plan.md"),
                  encoding="utf-8") as f:
            return f.read()

    def _plan_state(self, feature):
        text = self._ckpt_log(feature)
        state, _cnt = CLI._read_plan_status(text)
        return state

    # -- _graph_merge_gates: always a SUPERSET of the defaults -----------------
    def test_plain_node_gates_are_exactly_the_defaults_verbatim(self):
        gates = CLI._graph_merge_gates({"feature": "260722-a"})
        # Re-listed VERBATIM (the merge-queue --gate REPLACES the defaults, so a
        # per-node list MUST re-list them or it would silently drop them).
        self.assertEqual(gates, list(CLI.FANOUT_DEFAULT_GATES))

    def test_gates_always_a_superset_and_prefix_of_defaults(self):
        for node in ({"feature": "260722-a"},
                     {"feature": "260722-a", "self_extension": True},
                     {"feature": "260722-a", "load_bearing": True}):
            gates = CLI._graph_merge_gates(node)
            # superset — no default is ever removed
            self.assertTrue(set(CLI.FANOUT_DEFAULT_GATES) <= set(gates))
            # verbatim order-preserving prefix — the replace-not-append guard
            self.assertEqual(gates[:len(CLI.FANOUT_DEFAULT_GATES)],
                             list(CLI.FANOUT_DEFAULT_GATES))

    def test_selfext_node_appends_gate_release_full_and_verify(self):
        gates = CLI._graph_merge_gates(
            {"feature": "260722-se", "self_extension": True})
        self.assertIn("scripts/agentware gate release --full", gates)
        self.assertTrue(any("verify-premises" in g for g in gates))
        self.assertTrue(set(CLI.FANOUT_DEFAULT_GATES) <= set(gates))

    def test_load_bearing_nonselfext_appends_verify_not_release(self):
        gates = CLI._graph_merge_gates(
            {"feature": "260722-lb", "load_bearing": True})
        self.assertTrue(any("verify-premises" in g for g in gates))
        self.assertNotIn("scripts/agentware gate release --full", gates)

    def test_verify_premises_gate_is_kb_rooted_and_strict(self):
        gates = CLI._graph_merge_gates(
            {"feature": "260722-se", "self_extension": True})
        vp = [g for g in gates if "verify-premises" in g][0]
        self.assertIn('$AGENTWARE_KNOWLEDGE_DIR/work/260722-se/plan.md', vp)
        self.assertIn("--strict", vp)

    # -- the executor hands the merge seam a SUPERSET, on the integrated tree --
    def test_execute_hands_superset_gates_to_merge_on_integration(self):
        man = self._write_manifest(
            "260722-mg", nodes=[("260722-lb", "none", False, [])])
        man["nodes"][0]["load_bearing"] = True   # force a strict superset
        self._seed("260722-lb", status="done", open_markers=0, done_markers=2)
        self._terminal("260722-lb", "completed")
        captured = {}

        def prepare(reg, feature, repo, kb_repo, integration):
            reg["lanes"][feature] = {
                "feature": feature, "merged": False,
                "feat_branch": "feat/%s" % feature,
                "kb_branch": "kb/%s" % feature}
            return "created", None

        def spawn(rec, feature):
            return _FakeHandle()

        def merge(reg, feature, repo, kb_repo, integration, gates=None):
            captured["gates"] = gates
            captured["repo"] = repo
            reg.setdefault("lanes", {}).setdefault(
                feature, {"feature": feature})["merged"] = True
            return "merged"

        res = CLI._graph_execute(
            self.kdir, man, self.NOW, repo=self.kdir, kb_repo=self.kdir,
            spawn=spawn, merge=merge, prepare=prepare,
            bootstrap=False, check_env=False)
        self.assertEqual(res["status"], "complete", res)
        self.assertIn("gates", captured)
        self.assertTrue(set(CLI.FANOUT_DEFAULT_GATES) <= set(captured["gates"]))
        self.assertTrue(any("verify-premises" in g for g in captured["gates"]))
        # The gates run on the INTEGRATION tree (the repo handed to the merge).
        self.assertEqual(captured["repo"], self.kdir)

    def test_merge_lane_runs_nonempty_gate_list_and_halts_on_failure(self):
        # Finding 12: exercise the REAL `_graph_merge_lane` gate-execution loop
        # with a NON-EMPTY gate list. The only prior caller passed gates=[] (gates
        # off), so a regression that skipped the `for g in glist: _run_gate(...)`
        # loop or stopped returning 'halt-gate' on a failing gate would ship
        # undetected — this locks the integrated-tree review-then-proceed floor.
        def _reg():
            return {"lanes": {"260722-g": {
                "feature": "260722-g", "merged": False,
                "feat_branch": "feat/260722-g", "kb_branch": "kb/260722-g"}}}

        orig = {n: getattr(CLI, n) for n in (
            "_mq_integrate_package", "_mq_integrate_kb", "_run_gate",
            "git_is_work_tree", "git_is_clean", "save_lanes")}
        self.addCleanup(lambda: [setattr(CLI, n, f) for n, f in orig.items()])
        CLI._mq_integrate_package = lambda *a, **k: ("merged", "")
        CLI._mq_integrate_kb = lambda *a, **k: ("merged", "")
        CLI.git_is_work_tree = lambda *a, **k: False   # no KB repo -> skip KB path
        CLI.git_is_clean = lambda *a, **k: True
        CLI.save_lanes = lambda *a, **k: None

        # (a) all gates PASS -> merged, and EACH gate in the list actually ran.
        ran = []
        CLI._run_gate = lambda g, *a, **k: (ran.append(g) or (0, ""))
        st = CLI._graph_merge_lane(_reg(), "260722-g", "/repo", None,
                                   "integration", gates=["gate-a", "gate-b"])
        self.assertEqual(st, "merged")
        self.assertEqual(ran, ["gate-a", "gate-b"])

        # (b) a FAILING gate -> halt-gate (the gate loop is load-bearing).
        ran2 = []

        def failing(g, *a, **k):
            ran2.append(g)
            return (0, "") if g == "gate-a" else (1, "boom")

        CLI._run_gate = failing
        st2 = CLI._graph_merge_lane(_reg(), "260722-g", "/repo", None,
                                    "integration", gates=["gate-a", "gate-b"])
        self.assertEqual(st2, "halt-gate")
        self.assertIn("gate-b", ran2)

    # -- graph checkpoint: the operator-only manual barrier --------------------
    def test_manual_checkpoint_refuses_loop_actor_no_write(self):
        self._write_manifest("260722-cp",
                             nodes=[("260722-se", "manual", True, [])])
        self._seed("260722-se", status="running", self_ext=True)
        rc = CLI.cmd_graph_checkpoint(self._args(
            name="260722-cp", node="260722-se", verdict="proceed", actor="loop"))
        self.assertEqual(rc, 1)
        # NOTHING written — no checkpoint log on a refusal.
        self.assertNotIn(CLI._PLAN_CHECKPOINT_LOG_HEADING,
                         self._ckpt_log("260722-se"))

    def test_manual_checkpoint_accepts_operator_and_appends_log(self):
        self._write_manifest("260722-cp",
                             nodes=[("260722-se", "manual", True, [])])
        self._seed("260722-se", status="running", self_ext=True)
        rc = CLI.cmd_graph_checkpoint(self._args(
            name="260722-cp", node="260722-se", verdict="proceed",
            actor="operator"))
        self.assertEqual(rc, 0)
        body = self._ckpt_log("260722-se")
        self.assertIn(CLI._PLAN_CHECKPOINT_LOG_HEADING, body)
        self.assertIn("proceed by operator", body)

    def test_review_checkpoint_accepts_loop_verdict(self):
        self._write_manifest("260722-cp",
                             nodes=[("260722-r", "review", False, [])])
        self._seed("260722-r", status="running")
        rc = CLI.cmd_graph_checkpoint(self._args(
            name="260722-cp", node="260722-r", verdict="hold", actor="loop"))
        self.assertEqual(rc, 0)
        self.assertIn("hold by loop", self._ckpt_log("260722-r"))

    def test_checkpoint_log_is_append_only(self):
        self._write_manifest("260722-cp",
                             nodes=[("260722-r", "review", False, [])])
        self._seed("260722-r", status="running")
        CLI.cmd_graph_checkpoint(self._args(
            name="260722-cp", node="260722-r", verdict="hold", actor="operator"))
        CLI.cmd_graph_checkpoint(self._args(
            name="260722-cp", node="260722-r", verdict="proceed",
            actor="operator"))
        body = self._ckpt_log("260722-r")
        self.assertEqual(body.count(CLI._PLAN_CHECKPOINT_LOG_HEADING), 1)
        self.assertIn("hold by operator", body)
        self.assertIn("proceed by operator", body)

    def test_abort_subtree_cancels_transitive_successors(self):
        self._write_manifest(
            "260722-cp",
            nodes=[("260722-a", "manual", True, []),
                   ("260722-b", "none", False, []),
                   ("260722-c", "none", False, [])],
            edges=[("260722-a", "260722-b"), ("260722-b", "260722-c")])
        self._seed("260722-a", status="running", self_ext=True)
        self._seed("260722-b", status="ready")
        self._seed("260722-c", status="ready")
        rc = CLI.cmd_graph_checkpoint(self._args(
            name="260722-cp", node="260722-a", verdict="abort-subtree",
            actor="operator"))
        self.assertEqual(rc, 0)
        # The whole transitive successor set is cancelled via the set-state matrix.
        self.assertEqual(self._plan_state("260722-b"), "cancelled")
        self.assertEqual(self._plan_state("260722-c"), "cancelled")
        # The node itself is NOT cancelled (only its successors).
        self.assertEqual(self._plan_state("260722-a"), "running")

    def test_abort_subtree_leaves_terminal_successor_untouched(self):
        self._write_manifest(
            "260722-cp",
            nodes=[("260722-a", "manual", True, []),
                   ("260722-b", "none", False, [])],
            edges=[("260722-a", "260722-b")])
        self._seed("260722-a", status="running", self_ext=True)
        # A `done` successor has no legal edge to cancelled — left as-is.
        self._seed("260722-b", status="done", open_markers=0, done_markers=2)
        rc = CLI.cmd_graph_checkpoint(self._args(
            name="260722-cp", node="260722-a", verdict="abort-subtree",
            actor="operator"))
        self.assertEqual(rc, 0)
        self.assertEqual(self._plan_state("260722-b"), "done")

    def test_checkpoint_unknown_node_refused(self):
        self._write_manifest("260722-cp",
                             nodes=[("260722-a", "none", False, [])])
        self._seed("260722-a", status="running")
        rc = CLI.cmd_graph_checkpoint(self._args(
            name="260722-cp", node="260722-ghost", verdict="proceed",
            actor="operator"))
        self.assertEqual(rc, 1)

    def test_checkpoint_unknown_verdict_refused(self):
        self._write_manifest("260722-cp",
                             nodes=[("260722-a", "review", False, [])])
        self._seed("260722-a", status="running")
        rc = CLI.cmd_graph_checkpoint(self._args(
            name="260722-cp", node="260722-a", verdict="bogus",
            actor="operator"))
        self.assertEqual(rc, 1)
