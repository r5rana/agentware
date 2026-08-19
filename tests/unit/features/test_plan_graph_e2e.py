"""TestEndToEnd -- split from `test_plan_graph` (task 58).

See `tests.unit.features._plan_graph_support` for the
shared header.
"""

from tests.unit.features._plan_graph_support import (  # noqa: F401
    CLI, HAVE_GIT_T5, _FakeHandle, _manifest, _status_plan, os, tempfile,
    unittest)


class TestEndToEnd(unittest.TestCase):
    """Task 9: a full end-to-end drive of a hermetic multi-node graph through the
    REAL CLI command path (`cmd_graph_validate` / `cmd_graph_run
    --i-approve-manifest --now` / `cmd_graph_resume`), with the four side-effecting
    seams (`bootstrap`/`prepare`/`drive`/`merge`) monkeypatched to a fake
    `agentware.sh` that flips plan markers + emits a terminal metric — NO real loop,
    NO subprocess. `--now` is pinned so every run is byte-identical.

    The one graph exercises: a non-self-extension same-file write-set CLIQUE (with an
    adversarial lexically-descending logical edge) that must serialize to one line, a
    disjoint docs node that fans out concurrently, a logical chain whose dependent
    waits for done+merged, and a self-extension node that HALTS for a manual
    checkpoint instead of being autonomously spawned. Separate methods cover the
    `adversarial_review_blocked`→HALT and contained-failure-blocks-only-subtree
    dispositions and idempotent resume. A final REAL-GIT method proves the clique
    merges one-at-a-time off `integration` with no textual conflict."""

    NOW = "2026-07-24T12:00:00Z"

    # feature slugs -----------------------------------------------------------
    A1, A2, A3 = "260722-a1", "260722-a2", "260722-a3"       # same-file clique
    DOC = "260722-doc"                                        # disjoint docs node
    CH1, CH2 = "260722-ch1", "260722-ch2"                    # logical chain
    SE = "260722-se"                                          # self-extension node
    SHARED_WS = ["app/shared.py"]

    def setUp(self):
        import shutil
        self.kdir = tempfile.mkdtemp(prefix="agentware-t9-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.fanout_dir = tempfile.mkdtemp(prefix="agentware-t9-fanout-")
        self.addCleanup(shutil.rmtree, self.fanout_dir, True)
        self._env_prev = {}
        for k, v in (("AGENTWARE_FANOUT_DIR", self.fanout_dir),
                     ("AGENTWARE_KNOWLEDGE_DIR", self.kdir)):
            self._env_prev[k] = os.environ.get(k)
            os.environ[k] = v
        # A kill-switch left in the operator shell would fail `graph run` closed —
        # scrub the four so the hermetic run is not derailed by the CI environment.
        for v in CLI.GRAPH_KILL_SWITCH_VARS:
            self._env_prev[v] = os.environ.get(v)
            os.environ.pop(v, None)
        self.addCleanup(self._restore_env)
        # Save/restore the four module-level side-effecting seams.
        self._orig = {n: getattr(CLI, n) for n in (
            "_graph_bootstrap_integration", "_graph_prepare_lane",
            "_graph_drive_lane", "_graph_merge_lane")}
        self.addCleanup(self._restore_seams)
        self.dispatched = []

    def _restore_env(self):
        for k, v in self._env_prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _restore_seams(self):
        for n, fn in self._orig.items():
            setattr(CLI, n, fn)

    # -- the fake-agentware.sh seams ------------------------------------------
    def _install_seams(self, outcomes=None):
        """Wire the four seams to a fake loop. `outcomes` maps feature -> terminal
        outcome; 'completed' (default) flips the plan to done, any other outcome
        leaves the plan honestly running with only the metrics terminal event (a
        contained failure). `bootstrap` is a no-op success; `prepare`/`merge` mutate
        + persist the lane registry so a later resume recomputes from live state."""
        outcomes = outcomes or {}

        def bootstrap(*a, **k):
            return (True, None)

        def prepare(reg, feature, repo, kb_repo, integration):
            reg.setdefault("lanes", {})[feature] = {
                "feature": feature, "merged": False,
                "kb_path": "/nope", "worktree": "/nope",
                "feat_branch": "feat/%s" % feature,
                "kb_branch": "kb/%s" % feature}
            CLI.save_lanes(reg)
            return "created", None

        def drive(rec, feature=None):
            feature = feature or rec.get("feature")
            self.dispatched.append(feature)
            oc = outcomes.get(feature, "completed")
            if oc == "completed":
                self._seed(feature, status="done", open_markers=0, done_markers=2)
                self._terminal(feature, "completed")
            else:
                self._seed(feature, status="running",
                           claim="lane@host %s" % self.NOW,
                           open_markers=1, done_markers=1)
                self._terminal(feature, oc)
            return _FakeHandle()

        def merge(reg, feature, repo, kb_repo, integration, gates=None):
            reg.setdefault("lanes", {}).setdefault(
                feature, {"feature": feature})["merged"] = True
            CLI.save_lanes(reg)
            return "merged"

        CLI._graph_bootstrap_integration = bootstrap
        CLI._graph_prepare_lane = prepare
        CLI._graph_drive_lane = drive
        CLI._graph_merge_lane = merge

    # -- seeding helpers ------------------------------------------------------
    def _seed(self, feature, *, self_ext=False, **kw):
        path = os.path.join(self.kdir, "work", feature, "plan.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(_status_plan(self_ext=self_ext, **kw))

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
        kw.setdefault("concurrency", None)
        kw.setdefault("node_timeout", None)
        kw.setdefault("deadline", CLI.GRAPH_DEADLINE_HOURS)
        return types.SimpleNamespace(**kw)

    def _quiet(self, fn):
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return fn()

    def _ledger(self, name):
        return CLI._graph_ledger_read(self.kdir, name)

    def _last_result(self, name):
        return [e for e in self._ledger(name)
                if e.get("event") == "result"][-1]

    def _node_status(self, feature, name):
        reg, _ = CLI.load_lanes()
        return CLI._graph_node_status(
            self.kdir, {"feature": feature}, self.NOW, lanes=reg)["status"]

    # -- the headline multi-node graph ----------------------------------------
    def _headline_manifest(self, name="260722-e2e"):
        return self._write_manifest(
            name,
            nodes=[
                (self.A1, "none", False, self.SHARED_WS),
                (self.A2, "none", False, self.SHARED_WS),
                (self.A3, "none", False, self.SHARED_WS),
                (self.DOC, "none", False, ["docs/notes.md"]),
                (self.CH1, "none", False, []),
                (self.CH2, "none", False, []),
                (self.SE, "manual", True, []),
            ],
            edges=[
                (self.A3, self.A1),         # adversarial lexically-descending logical
                (self.CH1, self.CH2),       # logical chain
                # the self-ext node sinks the whole autonomous portion so the graph
                # runs everything, THEN halts at the manual checkpoint.
                (self.A1, self.SE), (self.A2, self.SE), (self.A3, self.SE),
                (self.CH2, self.SE), (self.DOC, self.SE),
            ],
            concurrency=3)

    def _seed_headline(self):
        for f in (self.A1, self.A2, self.A3, self.DOC, self.CH1, self.CH2):
            self._seed(f, status="ready", open_markers=1, done_markers=0)
        self._seed(self.SE, status="ready", self_ext=True,
                   open_markers=1, done_markers=0)

    def test_headline_graph_runs_autonomous_then_halts_at_selfext(self):
        name = "260722-e2e"
        man = self._headline_manifest(name)
        self._seed_headline()
        self._install_seams()

        # 1) validate: acyclic, NON-empty, resource edges derivable along the topo
        #    linearization (the union stays a DAG).
        rc = self._quiet(lambda: CLI.cmd_graph_validate(
            self._args(name=name, format="text")))
        self.assertEqual(rc, 0)
        derived = CLI._graph_derive_resource_edges(man)
        self.assertTrue(derived)
        self.assertTrue(all(e["kind"] == "resource" and e["derived"]
                            for e in derived))
        full = {"nodes": man["nodes"],
                "edges": list(man["edges"]) + derived}
        _n, forward, _r = CLI._graph_build(full)
        self.assertEqual(CLI.find_cycles(forward), [])

        # 2) run: approved, pinned --now.
        rc = self._quiet(lambda: CLI.cmd_graph_run(
            self._args(name=name, i_approve_manifest=True)))
        # The graph HALTED at the self-extension node -> non-complete exit.
        self.assertEqual(rc, 1)
        res = self._last_result(name)
        self.assertEqual(res["status"], "halt")
        self.assertEqual(res["reason"], "awaiting-manual-selfext:%s" % self.SE)
        self.assertEqual(res["node"], self.SE)

        # 3) the self-extension node was NEVER autonomously spawned.
        self.assertNotIn(self.SE, self.dispatched)

        # 4) the same-file clique serialized to ONE line, in the derived topo order
        #    (respecting the adversarial descending logical edge a3 before a1).
        clique = {self.A1, self.A2, self.A3}
        order = CLI.topo_order(forward, _n)
        clique_order = [f for f in order if f in clique]
        disp_clique = [f for f in self.dispatched if f in clique]
        self.assertEqual(disp_clique, clique_order,
                         "clique must dispatch in the single-line topo order")
        self.assertLess(self.dispatched.index(self.A3),
                        self.dispatched.index(self.A1))

        # 5) the disjoint docs node + the chain root fanned out CONCURRENTLY with
        #    the clique's first node (wave 1 = the first `concurrency` dispatches).
        wave1 = set(self.dispatched[:3])
        self.assertIn(self.DOC, wave1)
        self.assertIn(self.CH1, wave1)
        self.assertEqual(len(wave1 & clique), 1)   # exactly one clique node in wave 1

        # 6) the chain dependent waited for its predecessor to be done+merged.
        self.assertLess(self.dispatched.index(self.CH1),
                        self.dispatched.index(self.CH2))

        # 7) every autonomous node reached done+merged before the halt.
        for f in (self.A1, self.A2, self.A3, self.DOC, self.CH1, self.CH2):
            self.assertEqual(self._node_status(f, name), CLI.GRAPH_NODE_DONE,
                             "%s should be done+merged" % f)

    def test_headline_run_is_byte_identical_across_replays(self):
        # Determinism floor: the same pinned --now over reset live state yields a
        # byte-identical dispatch sequence and the same halt.
        name = "260722-det"
        self._headline_manifest(name)

        def one_run():
            # fresh live state for each replay.
            self.dispatched = []
            for f in (self.A1, self.A2, self.A3, self.DOC, self.CH1, self.CH2):
                self._seed(f, status="ready", open_markers=1, done_markers=0)
            self._seed(self.SE, status="ready", self_ext=True,
                       open_markers=1, done_markers=0)
            logs = os.path.join(self.kdir, "logs", "metrics.jsonl")
            if os.path.exists(logs):
                os.remove(logs)
            CLI.save_lanes({"lanes": {}})
            self._install_seams()
            self._quiet(lambda: CLI.cmd_graph_run(
                self._args(name=name, i_approve_manifest=True)))
            return list(self.dispatched)

        d1 = one_run()
        d2 = one_run()
        self.assertEqual(d1, d2)

    def test_run_refuses_without_approval(self):
        name = "260722-noappr"
        self._headline_manifest(name)
        self._seed_headline()
        self._install_seams()
        rc = self._quiet(lambda: CLI.cmd_graph_run(
            self._args(name=name, i_approve_manifest=False)))
        self.assertEqual(rc, 1)
        self.assertEqual(self.dispatched, [])            # nothing dispatched
        self.assertEqual(self._ledger(name), [])         # no approval recorded

    # -- adversarial_review_blocked -> HALT (through the CLI path) -------------
    def test_adversarial_review_blocked_halts_graph(self):
        name = "260722-adv"
        self._write_manifest(name, nodes=[
            ("260722-adv-a", "none", False, []),
            ("260722-adv-b", "none", False, []),
        ])
        self._seed("260722-adv-a", status="ready", open_markers=1, done_markers=0)
        self._seed("260722-adv-b", status="ready", open_markers=1, done_markers=0)
        self._install_seams(outcomes={"260722-adv-a": "adversarial_review_blocked"})
        rc = self._quiet(lambda: CLI.cmd_graph_run(
            self._args(name=name, i_approve_manifest=True)))
        self.assertEqual(rc, 1)
        res = self._last_result(name)
        self.assertEqual(res["status"], "halt")
        self.assertEqual(res["reason"], "adversarial_review_blocked")

    # -- contained failure blocks ONLY its subtree, independent branch continues -
    def test_contained_failure_blocks_only_subtree(self):
        name = "260722-cf"
        # x -> y (dependent); z independent.
        self._write_manifest(name, nodes=[
            ("260722-cf-x", "none", False, []),
            ("260722-cf-y", "none", False, []),
            ("260722-cf-z", "none", False, []),
        ], edges=[("260722-cf-x", "260722-cf-y")])
        for f in ("260722-cf-x", "260722-cf-y", "260722-cf-z"):
            self._seed(f, status="ready", open_markers=1, done_markers=0)
        self._install_seams(outcomes={"260722-cf-x": "hit_max_iterations"})
        rc = self._quiet(lambda: CLI.cmd_graph_run(
            self._args(name=name, i_approve_manifest=True)))
        self.assertEqual(rc, 1)
        res = self._last_result(name)
        self.assertEqual(res["status"], "blocked")
        # x failed (contained); z (independent) ran; y (dependent) never dispatched.
        self.assertIn("260722-cf-x", self.dispatched)
        self.assertIn("260722-cf-z", self.dispatched)
        self.assertNotIn("260722-cf-y", self.dispatched)
        self.assertEqual(self._node_status("260722-cf-z", name),
                         CLI.GRAPH_NODE_DONE)   # independent branch completed

    # -- resume is idempotent over the already-done autonomous portion --------
    def test_resume_after_selfext_completes_idempotently(self):
        name = "260722-rse"
        self._headline_manifest(name)
        self._seed_headline()
        self._install_seams()
        # First run HALTs at the self-ext node (autonomous portion done+merged).
        rc = self._quiet(lambda: CLI.cmd_graph_run(
            self._args(name=name, i_approve_manifest=True)))
        self.assertEqual(rc, 1)
        self.assertEqual(self._last_result(name)["reason"],
                         "awaiting-manual-selfext:%s" % self.SE)

        # The operator records a proceed verdict at the mandatory manual checkpoint
        # (operator-only barrier), then runs the self-ext node themselves — modelled
        # here by flipping it done+merged.
        rc = self._quiet(lambda: CLI.cmd_graph_checkpoint(self._args(
            name=name, node=self.SE, verdict="proceed", actor="operator")))
        self.assertEqual(rc, 0)
        self._seed(self.SE, status="done", self_ext=True,
                   open_markers=0, done_markers=2)
        self._terminal(self.SE, "completed")
        reg, _ = CLI.load_lanes()
        reg.setdefault("lanes", {}).setdefault(
            self.SE, {"feature": self.SE})["merged"] = True
        CLI.save_lanes(reg)

        # Resume recomputes purely from live state: every node is now done+merged ->
        # complete, and NO already-done node is re-dispatched.
        self.dispatched = []
        rc = self._quiet(lambda: CLI.cmd_graph_resume(
            self._args(name=name, retry=False)))
        self.assertEqual(rc, 0)
        self.assertEqual(self.dispatched, [])
        self.assertEqual(self._last_result(name)["status"], "complete")

    # -- REAL GIT: the same-file clique merges off integration with no conflict --
    @unittest.skipUnless(HAVE_GIT_T5, "git not available")
    def test_same_file_clique_serializes_off_integration_no_conflict(self):
        """The B1/A2/A1 correctness floor with REAL git: bootstrap `integration`
        before wave 1, then provision + merge each same-file clique lane serially,
        each cut off the CURRENT integration HEAD. Because a successor branches off a
        base that already contains every merged predecessor, the serial
        `git merge --no-ff` applies each append cleanly — NO textual conflict."""
        import subprocess
        import shutil

        def git(cwd, *a):
            subprocess.run(["git", "-C", cwd] + list(a), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, check=True, text=True)

        repo = tempfile.mkdtemp(prefix="t9-pkg-")
        self.addCleanup(shutil.rmtree, repo, True)
        kb = tempfile.mkdtemp(prefix="t9-kb-")
        self.addCleanup(shutil.rmtree, kb, True)
        shared_rel = "app/shared.py"
        for r, rel, content in ((repo, shared_rel, "# shared\nbase = 0\n"),
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
        clique = [self.A1, self.A2, self.A3]
        for feat in clique:
            action, perr = CLI._graph_prepare_lane(reg, feat, repo, kb, integration)
            self.assertIsNone(perr, perr)
            lane = CLI._fanout_lane_paths(feat, repo, kb)
            # the lane was cut off the CURRENT integration HEAD (the invariant).
            self.assertTrue(CLI._is_ancestor(repo, integration,
                                             lane["feat_branch"]),
                            "%s must branch off integration" % feat)
            # fake loop: append this lane's distinct line to the SHARED file, commit
            # on the feat branch in the lane worktree.
            wt = reg["lanes"][feat]["worktree"]
            sp = os.path.join(wt, shared_rel)
            with open(sp, "a", encoding="utf-8") as f:
                f.write("touched_by = %r\n" % feat)
            git(wt, "add", "-A")
            git(wt, "commit", "-q", "-m", "lane %s" % feat)
            # merge onto integration through the real serial integrator (gates off:
            # a hermetic repo has no unittest/eval suite — the merge floor is what we
            # are proving here, not the gate chain, which Task 6 covers).
            mstatus = CLI._graph_merge_lane(reg, feat, repo, kb, integration,
                                            gates=[])
            self.assertEqual(mstatus, "merged", "%s must merge clean" % feat)

        # integration now carries ALL three distinct same-file edits — proof the
        # serialized clique merged with no textual conflict.
        out = subprocess.run(
            ["git", "-C", repo, "show", "%s:%s" % (integration, shared_rel)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True)
        body = out.stdout
        for feat in clique:
            self.assertIn("touched_by = %r" % feat, body)
        self.assertNotIn("<<<<<<<", body)   # no conflict markers ever landed
