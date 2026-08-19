"""TestDriverAndCaps -- split from `test_plan_graph` (task 58).

See `tests.unit.features._plan_graph_support` for the
shared header.
"""

from tests.unit.features._plan_graph_support import (  # noqa: F401
    CLI, HAVE_GIT_T5, _FakeHandle, _manifest, _status_plan, os, tempfile,
    unittest)


class TestDriverAndCaps(unittest.TestCase):
    """Task 5: the lane DRIVER, the deterministic wave EXECUTOR, and the cap +
    kernel-serialization + self-ext-halt decisions — all drivable with NO real
    subprocess via the injectable spawn/prepare/merge seams."""

    NOW = "2026-07-24T12:00:00Z"

    def setUp(self):
        import shutil
        self.kdir = tempfile.mkdtemp(prefix="agentware-t5-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.fanout_dir = tempfile.mkdtemp(prefix="agentware-t5-fanout-")
        self.addCleanup(shutil.rmtree, self.fanout_dir, True)
        self._prev_fanout = os.environ.get("AGENTWARE_FANOUT_DIR")
        os.environ["AGENTWARE_FANOUT_DIR"] = self.fanout_dir
        self.addCleanup(self._restore_fanout)
        self._orig_spawn = CLI._graph_spawn
        self.addCleanup(lambda: setattr(CLI, "_graph_spawn", self._orig_spawn))

    def _restore_fanout(self):
        if self._prev_fanout is None:
            os.environ.pop("AGENTWARE_FANOUT_DIR", None)
        else:
            os.environ["AGENTWARE_FANOUT_DIR"] = self._prev_fanout

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
        with open(os.path.join(logs, "metrics.jsonl"), "a", encoding="utf-8") as f:
            f.write(_json.dumps({"event": "terminal", "feature": feature,
                                 "outcome": outcome, "ts": self.NOW}) + "\n")

    def _stubs(self, outcomes=None):
        """Injectable seams that model a lane loop WITHOUT a subprocess. `outcomes`
        maps feature -> terminal outcome ('completed' flips the plan to done; any
        other stays running-with-a-terminal, i.e. an honest contained failure)."""
        outcomes = outcomes or {}
        dispatched = []

        def prepare(reg, feature, repo, kb_repo, integration):
            reg["lanes"][feature] = {
                "feature": feature, "merged": False,
                "kb_path": "/nope", "worktree": "/nope",
                "feat_branch": "feat/%s" % feature, "kb_branch": "kb/%s" % feature}
            return "created", None

        def spawn(rec, feature):
            dispatched.append(feature)
            oc = outcomes.get(feature, "completed")
            if oc == "completed":
                self._seed(feature, status="done", open_markers=0, done_markers=2)
                self._terminal(feature, "completed")
            else:
                # A dispatched lane claims first (running); failure is NEVER
                # written to a plan state — the loop honestly STAYS running and its
                # verdict lives only in the metrics terminal event.
                self._seed(feature, status="running",
                           claim="lane@host %s" % self.NOW,
                           open_markers=1, done_markers=1)
                self._terminal(feature, oc)
            return _FakeHandle()

        def merge(reg, feature, repo, kb_repo, integration, gates=None):
            reg["lanes"][feature]["merged"] = True
            return "merged"

        return dispatched, prepare, spawn, merge

    def _run(self, manifest, outcomes=None, **kw):
        dispatched, prepare, spawn, merge = self._stubs(outcomes)
        res = CLI._graph_execute(
            self.kdir, manifest, self.NOW, repo=self.kdir, kb_repo=self.kdir,
            spawn=spawn, merge=merge, prepare=prepare,
            bootstrap=False, check_env=False, **kw)
        return res, dispatched

    # -- orchestrator-env fail-closed -----------------------------------------
    def test_orchestrator_env_blockers_detects_each_switch(self):
        for v in CLI.GRAPH_KILL_SWITCH_VARS:
            self.assertEqual(CLI._graph_orchestrator_env_blockers({v: "1"}), [v])
        self.assertEqual(CLI._graph_orchestrator_env_blockers({}), [])
        # an empty / whitespace value is NOT "present".
        self.assertEqual(
            CLI._graph_orchestrator_env_blockers(
                {"AGENTWARE_DISABLE_RELEASE_GATE": "  "}), [])

    def test_execute_refuses_on_orchestrator_kill_switch(self):
        man = _manifest(nodes=[("260722-a",)])
        env = {"AGENTWARE_SKIP_ADVERSARIAL_REVIEW": "1"}
        res = CLI._graph_execute(self.kdir, man, self.NOW, repo=self.kdir,
                                 kb_repo=self.kdir, bootstrap=False,
                                 check_env=True, environ=env)
        self.assertEqual(res["status"], "halt")
        self.assertEqual(res["reason"], "orchestrator-kill-switch")
        self.assertIn("AGENTWARE_SKIP_ADVERSARIAL_REVIEW", res["offenders"])

    # -- lane env allow-list --------------------------------------------------
    def test_lane_env_built_by_allowlist_excludes_kill_switches(self):
        env_in = {"PATH": "/usr/bin", "HOME": "/home/x"}
        for v in CLI.GRAPH_KILL_SWITCH_VARS:
            env_in[v] = "1"
        env_in["AGENTWARE_DREAM"] = "1"  # any other stray AGENTWARE_* toggle
        out = CLI._graph_lane_env("/lane/kb", environ=env_in)
        for v in CLI.GRAPH_KILL_SWITCH_VARS:
            self.assertNotIn(v, out)
        self.assertNotIn("AGENTWARE_DREAM", out)
        self.assertEqual(out["AGENTWARE_KNOWLEDGE_DIR"], "/lane/kb")
        self.assertEqual(out["PATH"], "/usr/bin")
        self.assertEqual(out["GIT_TERMINAL_PROMPT"], "0")

    def test_drive_lane_uses_spawn_seam_with_allowlist_env(self):
        captured = {}

        def fake_spawn(argv, cwd, env):
            captured.update(argv=argv, cwd=cwd, env=env)
            return _FakeHandle()

        CLI._graph_spawn = fake_spawn
        rec = {"feature": "260722-a", "kb_path": "/lane/kb",
               "worktree": "/lane/pkg"}
        CLI._graph_drive_lane(rec)
        self.assertEqual(captured["argv"], ["./agentware.sh", "260722-a"])
        self.assertEqual(captured["cwd"], "/lane/pkg")
        self.assertEqual(captured["env"]["AGENTWARE_KNOWLEDGE_DIR"], "/lane/kb")
        for v in CLI.GRAPH_KILL_SWITCH_VARS:
            self.assertNotIn(v, captured["env"])

    # -- pure cap + serialization decisions -----------------------------------
    def test_select_dispatch_respects_cap(self):
        ready = ["a", "b", "c", "d"]
        ws = {f: [] for f in ready}
        se = {f: False for f in ready}
        sel = CLI._graph_select_dispatch(ready, ws, se, 2)
        self.assertEqual(sel["dispatch"], ["a", "b"])
        self.assertEqual(sel["deferred"], ["c", "d"])

    def test_select_dispatch_serializes_kernel_lanes(self):
        ready = ["a", "b", "c"]
        ws = {"a": ["scripts/agentware"], "b": ["scripts/agentware"],
              "c": ["docs"]}
        se = {f: False for f in ready}
        sel = CLI._graph_select_dispatch(ready, ws, se, 3)
        # exactly ONE kernel lane dispatched; the disjoint docs lane rides along.
        self.assertIn("a", sel["dispatch"])
        self.assertIn("c", sel["dispatch"])
        self.assertNotIn("b", sel["dispatch"])
        self.assertIn("b", sel["deferred"])

    def test_select_dispatch_surfaces_self_ext_as_halt(self):
        ready = ["a", "s"]
        sel = CLI._graph_select_dispatch(
            ready, {"a": [], "s": []}, {"a": False, "s": True}, 3)
        self.assertEqual(sel["halt_selfext"], ["s"])
        self.assertEqual(sel["dispatch"], ["a"])

    def test_should_reprovision_pure(self):
        self.assertTrue(CLI._graph_should_reprovision(True, False))
        self.assertFalse(CLI._graph_should_reprovision(True, True))
        self.assertFalse(CLI._graph_should_reprovision(False, False))

    def test_write_set_touches_kernel(self):
        self.assertTrue(CLI._graph_write_set_touches_kernel(["scripts/agentware"]))
        self.assertTrue(
            CLI._graph_write_set_touches_kernel(["scripts/agentware/**"]))
        self.assertFalse(CLI._graph_write_set_touches_kernel(["docs", "README.md"]))

    # -- executor end-to-end (stub seams, deterministic) ----------------------
    def test_executor_serializes_kernel_clique_disjoint_concurrent(self):
        man = _manifest(nodes=[
            ("260722-a1", "none", False, ["scripts/agentware"]),
            ("260722-a2", "none", False, ["scripts/agentware"]),
            ("260722-a3", "none", False, ["scripts/agentware"]),
            ("260722-d", "none", False, ["docs"]),
        ], concurrency=3)
        for f in ("260722-a1", "260722-a2", "260722-a3", "260722-d"):
            self._seed(f, status="ready")
        res, dispatched = self._run(man)
        self.assertEqual(res["status"], "complete", res)
        # The same-file clique serialized in the single topo linearization.
        self.assertLess(dispatched.index("260722-a1"),
                        dispatched.index("260722-a2"))
        self.assertLess(dispatched.index("260722-a2"),
                        dispatched.index("260722-a3"))
        # The disjoint docs node fanned out in wave 1 (concurrent with a1).
        self.assertIn("260722-d", dispatched[:2])

    def test_executor_halts_at_self_ext_node(self):
        man = _manifest(nodes=[("260722-a",), ("260722-s", "manual", True)],
                        edges=[("260722-a", "260722-s")])
        self._seed("260722-a", status="ready")
        self._seed("260722-s", status="ready", self_ext=True)
        res, dispatched = self._run(man)
        self.assertEqual(res["status"], "halt")
        self.assertEqual(res["reason"], "awaiting-manual-selfext:260722-s")
        # It runs UP TO the self-ext node, then never autonomously spawns it.
        self.assertEqual(dispatched, ["260722-a"])

    def test_executor_recomputes_self_ext_from_plan_not_manifest(self):
        # Audit HIGH regression: `self_extension` is COMPUTED from the LIVE plan.md
        # at execution time, NEVER trusted from the operator-editable manifest
        # field. A node stored `self_extension:false` (a hand-edited graph.json, or
        # a plan.md that drifted to self-extension AFTER `add-node`) whose plan.md
        # IS self-extension must STILL halt for the mandatory manual checkpoint —
        # never be autonomously spawned with weakened gates.
        man = _manifest(nodes=[("260722-a",),
                               ("260722-s", "manual", False)],  # stored: NOT self-ext
                        edges=[("260722-a", "260722-s")])
        self._seed("260722-a", status="ready")
        self._seed("260722-s", status="ready", self_ext=True)  # plan.md: self-ext
        res, dispatched = self._run(man)
        self.assertEqual(res["status"], "halt")
        self.assertEqual(res["reason"], "awaiting-manual-selfext:260722-s")
        self.assertEqual(dispatched, ["260722-a"])

    def test_executor_halts_on_adversarial_review_blocked(self):
        man = _manifest(nodes=[("260722-a",)])
        self._seed("260722-a", status="running", claim="a@h %s" % self.NOW)
        self._terminal("260722-a", "adversarial_review_blocked")
        res, dispatched = self._run(man)
        self.assertEqual(res["status"], "halt")
        self.assertEqual(res["reason"], "adversarial_review_blocked")
        self.assertEqual(dispatched, [])

    def test_executor_contained_failure_blocks_subtree(self):
        man = _manifest(nodes=[("260722-x",), ("260722-y",), ("260722-z",)],
                        edges=[("260722-x", "260722-y")])
        for f in ("260722-x", "260722-y", "260722-z"):
            self._seed(f, status="ready")
        res, dispatched = self._run(
            man, outcomes={"260722-x": "hit_max_iterations"})
        self.assertEqual(res["status"], "blocked")
        # x failed (contained), y (its dependent) blocked, z (independent) ran.
        self.assertIn("260722-x", dispatched)
        self.assertIn("260722-z", dispatched)
        self.assertNotIn("260722-y", dispatched)
        self.assertIn("260722-x", res["pending"])
        self.assertIn("260722-y", res["pending"])

    def test_executor_empty_manifest_is_noop_complete(self):
        res, dispatched = self._run(_manifest())
        self.assertEqual(res["status"], "complete")
        self.assertEqual(dispatched, [])

    def test_selfext_done_no_lane_resume_continues_not_halt_package(self):
        # Finding 0/5: after the operator runs a self-ext node in the MAIN checkout
        # (no lane), resume must CONTINUE to its dependents — never route the
        # lane-less node into the merge-queue (which returns 'halt-package' and
        # HALTs the whole graph forever). The `merge` seam here faithfully models
        # the REAL integrator's contract: a lane-less node -> 'halt-package'.
        man = _manifest(
            nodes=[("260722-se", "manual", True, []),
                   ("260722-d", "none", False, [])],
            edges=[("260722-se", "260722-d")])
        self._seed("260722-se", status="done", self_ext=True,
                   open_markers=0, done_markers=2)          # operator-run, NO lane
        self._seed("260722-d", status="ready", open_markers=1, done_markers=0)
        dispatched, prepare, spawn, _stub = self._stubs()

        def merge(reg, feature, repo, kb_repo, integration, gates=None):
            # Mirror `_graph_merge_lane`: `if not rec: return "halt-package"`.
            if (reg.get("lanes") or {}).get(feature) is None:
                return "halt-package"
            reg["lanes"][feature]["merged"] = True
            return "merged"

        res = CLI._graph_execute(
            self.kdir, man, self.NOW, repo=self.kdir, kb_repo=self.kdir,
            spawn=spawn, merge=merge, prepare=prepare,
            bootstrap=False, check_env=False)
        self.assertEqual(res["status"], "complete", res)
        self.assertIn("260722-d", dispatched)          # dependent ran
        # SE was NEVER handed to the merge-queue.
        self.assertNotIn("260722-se",
                         [m["feature"] for m in res["merged"]])

    def test_unfinalizable_straggler_halts_instead_of_hanging(self):
        # Finding 1/9: a DONE_UNFINALIZED straggler whose finalize is REFUSED (the
        # loop emitted `completed` but left an open marker, so running->done is
        # gated out) must fail closed, NOT spin the tick loop forever without
        # advancing `waves` (which would defeat the GRAPH_TICK_CEILING backstop).
        man = _manifest(nodes=[("260722-s",)])
        self._seed("260722-s", status="running",
                   claim="lane@host %s" % self.NOW,
                   open_markers=1, done_markers=1)          # open marker blocks finalize
        self._terminal("260722-s", "completed")             # but loop said completed
        dispatched, prepare, spawn, merge = self._stubs()
        res = CLI._graph_execute(
            self.kdir, man, self.NOW, repo=self.kdir, kb_repo=self.kdir,
            spawn=spawn, merge=merge, prepare=prepare,
            bootstrap=False, check_env=False, tick_ceiling=25)
        self.assertEqual(res["status"], "halt")
        self.assertTrue(res["reason"].startswith("unfinalizable-straggler:"),
                        res["reason"])
        self.assertEqual(res["node"], "260722-s")

    def test_executor_deterministic_dispatch_order(self):
        man = _manifest(nodes=[("260722-a",), ("260722-b",), ("260722-c",)],
                        concurrency=1)
        for f in ("260722-a", "260722-b", "260722-c"):
            self._seed(f, status="ready")
        _r1, d1 = self._run(man)
        # Fresh state, same inputs -> byte-identical dispatch order.
        for f in ("260722-a", "260722-b", "260722-c"):
            self._seed(f, status="ready")
        os.remove(os.path.join(self.kdir, "logs", "metrics.jsonl"))
        CLI.save_lanes({"lanes": {}})
        _r2, d2 = self._run(man)
        self.assertEqual(d1, d2)
        self.assertEqual(d1, ["260722-a", "260722-b", "260722-c"])

    # -- base-off-integration + stale-base reprovision (real git) -------------
    @unittest.skipUnless(HAVE_GIT_T5, "git not available")
    def test_prepare_lane_reprovisions_off_stale_integration(self):
        import subprocess
        import shutil

        def git(cwd, *a):
            subprocess.run(["git", "-C", cwd] + list(a), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, check=True, text=True)

        repo = tempfile.mkdtemp(prefix="t5-pkg-")
        self.addCleanup(shutil.rmtree, repo, True)
        kb = tempfile.mkdtemp(prefix="t5-kb-")
        self.addCleanup(shutil.rmtree, kb, True)
        for r, rel, content in ((repo, "scripts/agentware", "x = 1\n"),
                                (kb, "MAIN.md", "# kb\n")):
            p = os.path.join(r, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            git(r, "init", "-q", "-b", "main")
            git(r, "config", "user.email", "t@t")
            git(r, "config", "user.name", "t")
            git(r, "add", "-A")
            git(r, "commit", "-q", "-m", "seed")

        integration = "integration"
        ok, err = CLI._graph_bootstrap_integration(repo, kb, integration)
        self.assertTrue(ok, err)

        reg, _e = CLI.load_lanes()
        feat = "260722-r"
        lane = CLI._fanout_lane_paths(feat, repo, kb)

        action, perr = CLI._graph_prepare_lane(reg, feat, repo, kb, integration)
        self.assertIsNone(perr, perr)
        self.assertEqual(action, "created")
        # The lane was cut off integration -> integration IS an ancestor of feat.
        self.assertTrue(CLI._is_ancestor(repo, integration, lane["feat_branch"]))

        # Advance integration beyond the lane (simulating a sibling's merge).
        with open(os.path.join(repo, "NEW.txt"), "w", encoding="utf-8") as f:
            f.write("new\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "advance")
        git(repo, "branch", "-f", integration, "main")
        self.assertFalse(CLI._is_ancestor(repo, integration, lane["feat_branch"]))

        # Prepare again -> detects the stale base -> tears down + re-cuts off HEAD.
        action2, perr2 = CLI._graph_prepare_lane(reg, feat, repo, kb, integration)
        self.assertIsNone(perr2, perr2)
        self.assertEqual(action2, "reprovisioned")
        self.assertTrue(CLI._is_ancestor(repo, integration, lane["feat_branch"]))
