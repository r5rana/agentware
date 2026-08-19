"""TestApprovalResumeObservability -- split from `test_plan_graph` (task 58).

See `tests.unit.features._plan_graph_support` for the
shared header.
"""

from tests.unit.features._plan_graph_support import (  # noqa: F401
    CLI, _FakeHandle, _manifest, _status_plan, os, tempfile, unittest)


class TestApprovalResumeObservability(unittest.TestCase):
    """Task 7: the `--i-approve-manifest` gate + sha256 content-pin, resume as pure
    recomputation (DONE-skip / hash-mutation refusal / FAILED-park-unless-retry),
    the self-extension HALT-for-manual-checkpoint, deterministic `graph status`,
    and the additive `graph`/`graph_node` observability events."""

    NOW = "2026-07-24T12:00:00Z"

    def setUp(self):
        import shutil
        self.kdir = tempfile.mkdtemp(prefix="agentware-t7-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.fanout_dir = tempfile.mkdtemp(prefix="agentware-t7-fanout-")
        self.addCleanup(shutil.rmtree, self.fanout_dir, True)
        self._env_prev = {}
        for k, v in (("AGENTWARE_FANOUT_DIR", self.fanout_dir),
                     ("AGENTWARE_KNOWLEDGE_DIR", self.kdir)):
            self._env_prev[k] = os.environ.get(k)
            os.environ[k] = v
        self.addCleanup(self._restore_env)
        # Neutralize the real-git bootstrap + lane seams so the command wrappers
        # never touch a real repo/subprocess. A drive/prepare/merge call in these
        # fixtures is a BUG (the graph should halt/complete without dispatch); the
        # stubs record so a test can assert they were never called.
        self.calls = {"drive": [], "prepare": [], "merge": []}
        self._orig = {
            "boot": CLI._graph_bootstrap_integration,
            "drive": CLI._graph_drive_lane,
            "prepare": CLI._graph_prepare_lane,
            "merge": CLI._graph_merge_lane,
        }
        CLI._graph_bootstrap_integration = lambda *a, **k: (True, None)
        CLI._graph_drive_lane = lambda rec, *a, **k: (
            self.calls["drive"].append(rec) or _FakeHandle())
        CLI._graph_prepare_lane = lambda *a, **k: (
            self.calls["prepare"].append(a) or ("created", None))
        CLI._graph_merge_lane = lambda *a, **k: (
            self.calls["merge"].append(a) or "merged")
        self.addCleanup(self._restore_seams)

    def _restore_env(self):
        for k, v in self._env_prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _restore_seams(self):
        for name, fn in self._orig.items():
            setattr(CLI, {"boot": "_graph_bootstrap_integration",
                          "drive": "_graph_drive_lane",
                          "prepare": "_graph_prepare_lane",
                          "merge": "_graph_merge_lane"}[name], fn)

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

    def _mark_merged(self, *features):
        data, _ = CLI.load_lanes()
        for feature in features:
            data["lanes"][feature] = {"feature": feature, "merged": True}
        CLI.save_lanes(data)

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
        return types.SimpleNamespace(**kw)

    def _ledger(self, name):
        return CLI._graph_ledger_read(self.kdir, name)

    # -- approval gate --------------------------------------------------------
    def test_run_refuses_without_approval(self):
        self._write_manifest("260722-ap",
                             nodes=[("260722-a", "none", False, [])])
        self._seed("260722-a", status="ready", open_markers=1, done_markers=0)
        rc = CLI.cmd_graph_run(self._args(name="260722-ap",
                                          i_approve_manifest=False))
        self.assertEqual(rc, 1)
        # nothing executed, no approval recorded
        self.assertEqual(self.calls["drive"], [])
        self.assertEqual(self._ledger("260722-ap"), [])

    def test_run_records_approval_with_manifest_sha256(self):
        man = self._write_manifest(
            "260722-ap", nodes=[("260722-a", "none", False, [])])
        # a done+merged single node -> completes with no dispatch.
        self._seed("260722-a", status="done", open_markers=0, done_markers=2)
        self._terminal("260722-a", "completed")
        self._mark_merged("260722-a")
        rc = CLI.cmd_graph_run(self._args(name="260722-ap",
                                          i_approve_manifest=True))
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls["drive"], [])   # nothing to dispatch
        appr = [e for e in self._ledger("260722-ap")
                if e.get("event") == "approval"]
        self.assertEqual(len(appr), 1)
        self.assertEqual(appr[0]["manifest_sha256"],
                         CLI._graph_manifest_sha256(man))
        # additive observability events landed and are ignored by iteration costs.
        events = CLI._read_metrics_jsonl(self.kdir)
        self.assertTrue(any(e.get("event") == "graph" for e in events))
        self.assertEqual(CLI.derive_iteration_costs(events), [])

    # -- self-extension node HALTs for a manual checkpoint --------------------
    def test_selfext_node_halts_for_manual_checkpoint(self):
        self._write_manifest(
            "260722-se", nodes=[("260722-x", "manual", True, [])])
        self._seed("260722-x", status="ready", self_ext=True,
                   open_markers=1, done_markers=0)
        rc = CLI.cmd_graph_run(self._args(name="260722-se",
                                          i_approve_manifest=True))
        self.assertEqual(rc, 1)                      # not complete
        # the self-ext node was NEVER autonomously spawned.
        self.assertEqual(self.calls["drive"], [])
        res = [e for e in self._ledger("260722-se")
               if e.get("event") == "result"][-1]
        self.assertEqual(res["status"], "halt")
        self.assertEqual(res["reason"], "awaiting-manual-selfext:260722-x")

    # -- resume: prior approval required + hash-mutation refusal ---------------
    def test_resume_refuses_without_prior_approval(self):
        self._write_manifest("260722-rs",
                             nodes=[("260722-a", "none", False, [])])
        self._seed("260722-a", status="done", open_markers=0, done_markers=2)
        rc = CLI.cmd_graph_resume(self._args(name="260722-rs", retry=False))
        self.assertEqual(rc, 1)
        self.assertEqual(self.calls["drive"], [])

    def test_resume_refuses_on_manifest_mutation(self):
        import json as _json
        self._write_manifest(
            "260722-rs", nodes=[("260722-a", "none", False, [])])
        self._seed("260722-a", status="done", open_markers=0, done_markers=2)
        self._terminal("260722-a", "completed")
        self._mark_merged("260722-a")
        self.assertEqual(
            CLI.cmd_graph_run(self._args(name="260722-rs",
                                         i_approve_manifest=True)), 0)
        # MUTATE the approved manifest on disk (add a node), then resume.
        man2 = _manifest(name="260722-rs",
                         nodes=[("260722-a", "none", False, []),
                                ("260722-b", "none", False, [])])
        with open(CLI._graph_manifest_path(self.kdir, "260722-rs"),
                  "w", encoding="utf-8") as f:
            _json.dump(man2, f)
        self._seed("260722-b", status="ready", open_markers=1, done_markers=0)
        rc = CLI.cmd_graph_resume(self._args(name="260722-rs", retry=False))
        self.assertEqual(rc, 1)
        res = [e for e in self._ledger("260722-rs")
               if e.get("event") == "result"][-1]
        self.assertEqual(res["reason"], "manifest-mutated")
        self.assertEqual(self.calls["drive"], [])   # refused before dispatch

    def test_resume_idempotent_over_partially_done_graph(self):
        self._write_manifest(
            "260722-rs",
            nodes=[("260722-a", "none", False, []),
                   ("260722-b", "none", False, [])],
            edges=[("260722-a", "260722-b")])
        for f in ("260722-a", "260722-b"):
            self._seed(f, status="done", open_markers=0, done_markers=2)
            self._terminal(f, "completed")
        self._mark_merged("260722-a", "260722-b")
        self.assertEqual(
            CLI.cmd_graph_run(self._args(name="260722-rs",
                                         i_approve_manifest=True)), 0)
        # A fresh resume recomputes purely from live state: every node is
        # done+merged -> complete, and NO done node is re-dispatched.
        rc = CLI.cmd_graph_resume(self._args(name="260722-rs", retry=False))
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls["drive"], [])

    # -- FAILED parked unless --retry -----------------------------------------
    def test_retry_reoffers_a_failed_node_exactly_once(self):
        # Driven directly through _graph_execute with stub seams (no git): a
        # contained failure parks the node without --retry; a --retry resume
        # re-offers it once and it then completes.
        import shutil
        kdir = tempfile.mkdtemp(prefix="agentware-t7r-kb-")
        self.addCleanup(shutil.rmtree, kdir, True)
        fdir = tempfile.mkdtemp(prefix="agentware-t7r-fanout-")
        self.addCleanup(shutil.rmtree, fdir, True)
        prev = os.environ.get("AGENTWARE_FANOUT_DIR")
        os.environ["AGENTWARE_FANOUT_DIR"] = fdir
        self.addCleanup(lambda: os.environ.__setitem__(
            "AGENTWARE_FANOUT_DIR", prev) if prev is not None
            else os.environ.pop("AGENTWARE_FANOUT_DIR", None))

        def seed(feature, **kw):
            p = os.path.join(kdir, "work", feature, "plan.md")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(_status_plan(**kw))

        def terminal(feature, outcome):
            import json as _json
            logs = os.path.join(kdir, "logs")
            os.makedirs(logs, exist_ok=True)
            with open(os.path.join(logs, "metrics.jsonl"), "a",
                      encoding="utf-8") as f:
                f.write(_json.dumps({"event": "terminal", "feature": feature,
                                     "outcome": outcome, "ts": self.NOW}) + "\n")

        outcomes = {"260722-a": "hit_max_iterations"}
        spawned = []

        def prepare(reg, feature, repo, kb_repo, integration):
            reg["lanes"][feature] = {"feature": feature, "merged": False}
            return "created", None

        def spawn(rec, feature):
            spawned.append(feature)
            oc = outcomes.get(feature, "completed")
            if oc == "completed":
                seed(feature, status="done", open_markers=0, done_markers=2)
                terminal(feature, "completed")
            else:
                seed(feature, status="running",
                     claim="lane@host %s" % self.NOW,
                     open_markers=1, done_markers=1)
                terminal(feature, oc)
            return _FakeHandle()

        def merge(reg, feature, repo, kb_repo, integration, gates=None):
            reg["lanes"][feature]["merged"] = True
            return "merged"

        man = _manifest(name="260722-rt",
                        nodes=[("260722-a", "none", False, [])])
        seed("260722-a", status="ready", open_markers=1, done_markers=0)
        # First run: the node fails (contained) -> parked, blocked frontier.
        r1 = CLI._graph_execute(kdir, man, self.NOW, repo=kdir, kb_repo=kdir,
                                spawn=spawn, merge=merge, prepare=prepare,
                                bootstrap=False, check_env=False, retry=False)
        self.assertEqual(r1["status"], "blocked")
        self.assertEqual(spawned, ["260722-a"])      # dispatched once, then parked

        # Now make the retry succeed, and resume WITH --retry.
        outcomes["260722-a"] = "completed"
        r2 = CLI._graph_execute(kdir, man, self.NOW, repo=kdir, kb_repo=kdir,
                                spawn=spawn, merge=merge, prepare=prepare,
                                bootstrap=False, check_env=False, retry=True)
        self.assertEqual(r2["status"], "complete")
        self.assertEqual(spawned, ["260722-a", "260722-a"])  # re-offered once

    def test_failed_node_parked_without_retry(self):
        import shutil
        kdir = tempfile.mkdtemp(prefix="agentware-t7p-kb-")
        self.addCleanup(shutil.rmtree, kdir, True)
        fdir = tempfile.mkdtemp(prefix="agentware-t7p-fanout-")
        self.addCleanup(shutil.rmtree, fdir, True)
        prev = os.environ.get("AGENTWARE_FANOUT_DIR")
        os.environ["AGENTWARE_FANOUT_DIR"] = fdir
        self.addCleanup(lambda: os.environ.__setitem__(
            "AGENTWARE_FANOUT_DIR", prev) if prev is not None
            else os.environ.pop("AGENTWARE_FANOUT_DIR", None))
        spawned = []

        def prepare(reg, feature, repo, kb_repo, integration):
            reg["lanes"][feature] = {"feature": feature, "merged": False}
            return "created", None

        def spawn(rec, feature):
            spawned.append(feature)
            p = os.path.join(kdir, "work", feature, "plan.md")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(_status_plan(status="running",
                                     claim="lane@host %s" % self.NOW,
                                     open_markers=1, done_markers=1))
            import json as _json
            logs = os.path.join(kdir, "logs")
            os.makedirs(logs, exist_ok=True)
            with open(os.path.join(logs, "metrics.jsonl"), "a",
                      encoding="utf-8") as f:
                f.write(_json.dumps({"event": "terminal", "feature": feature,
                                     "outcome": "post_hook_failure",
                                     "ts": self.NOW}) + "\n")
            return _FakeHandle()

        def merge(reg, feature, repo, kb_repo, integration, gates=None):
            reg["lanes"][feature]["merged"] = True
            return "merged"

        man = _manifest(name="260722-pk",
                        nodes=[("260722-a", "none", False, [])])
        p0 = os.path.join(kdir, "work", "260722-a", "plan.md")
        os.makedirs(os.path.dirname(p0), exist_ok=True)
        with open(p0, "w", encoding="utf-8") as f:
            f.write(_status_plan(status="ready", open_markers=1, done_markers=0))
        r = CLI._graph_execute(kdir, man, self.NOW, repo=kdir, kb_repo=kdir,
                               spawn=spawn, merge=merge, prepare=prepare,
                               bootstrap=False, check_env=False, retry=False)
        self.assertEqual(r["status"], "blocked")
        # dispatched exactly once and then LEFT parked (no auto-retry, R-FAIL-04).
        self.assertEqual(spawned, ["260722-a"])

    # -- quota throttle pauses (never fails) ----------------------------------
    def test_quota_throttle_pauses_run(self):
        import shutil
        kdir = tempfile.mkdtemp(prefix="agentware-t7q-kb-")
        self.addCleanup(shutil.rmtree, kdir, True)
        fdir = tempfile.mkdtemp(prefix="agentware-t7q-fanout-")
        self.addCleanup(shutil.rmtree, fdir, True)
        prev = os.environ.get("AGENTWARE_FANOUT_DIR")
        os.environ["AGENTWARE_FANOUT_DIR"] = fdir
        self.addCleanup(lambda: os.environ.__setitem__(
            "AGENTWARE_FANOUT_DIR", prev) if prev is not None
            else os.environ.pop("AGENTWARE_FANOUT_DIR", None))

        def prepare(reg, feature, repo, kb_repo, integration):
            reg["lanes"][feature] = {"feature": feature, "merged": False}
            return "created", None

        def spawn(rec, feature):
            p = os.path.join(kdir, "work", feature, "plan.md")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(_status_plan(status="running",
                                     claim="lane@host %s" % self.NOW,
                                     open_markers=1, done_markers=1))
            import json as _json
            logs = os.path.join(kdir, "logs")
            os.makedirs(logs, exist_ok=True)
            with open(os.path.join(logs, "metrics.jsonl"), "a",
                      encoding="utf-8") as f:
                f.write(_json.dumps({"event": "terminal", "feature": feature,
                                     "outcome": "quota_throttled",
                                     "ts": self.NOW}) + "\n")
            return _FakeHandle()

        def merge(reg, feature, repo, kb_repo, integration, gates=None):
            return "merged"

        man = _manifest(name="260722-q",
                        nodes=[("260722-a", "none", False, [])])
        p0 = os.path.join(kdir, "work", "260722-a", "plan.md")
        os.makedirs(os.path.dirname(p0), exist_ok=True)
        with open(p0, "w", encoding="utf-8") as f:
            f.write(_status_plan(status="ready", open_markers=1, done_markers=0))
        r = CLI._graph_execute(kdir, man, self.NOW, repo=kdir, kb_repo=kdir,
                               spawn=spawn, merge=merge, prepare=prepare,
                               bootstrap=False, check_env=False)
        self.assertEqual(r["status"], "paused")
        self.assertTrue(r["reason"].startswith("quota-throttle:"))

    # -- deterministic status render ------------------------------------------
    def test_status_json_is_deterministic_and_pins_sha256(self):
        import io
        import contextlib
        man = self._write_manifest(
            "260722-st",
            nodes=[("260722-a", "none", False, []),
                   ("260722-b", "review", False, [])],
            edges=[("260722-a", "260722-b")])
        self._seed("260722-a", status="done", open_markers=0, done_markers=2)
        self._terminal("260722-a", "completed")
        self._mark_merged("260722-a")
        self._seed("260722-b", status="ready", open_markers=1, done_markers=0)
        CLI._graph_ledger_append(self.kdir, "260722-st", {
            "event": "approval", "graph": "260722-st",
            "manifest_sha256": CLI._graph_manifest_sha256(man), "ts": self.NOW})

        def render():
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = CLI.cmd_graph_status(self._args(name="260722-st",
                                                     format="json"))
            return rc, buf.getvalue()

        rc1, out1 = render()
        rc2, out2 = render()
        self.assertEqual(rc1, 0)
        self.assertEqual(out1, out2)                 # byte-identical
        import json as _json
        payload = _json.loads(out1)
        self.assertEqual(payload["node_count"], 2)
        self.assertEqual(payload["approved_sha256"],
                         CLI._graph_manifest_sha256(man))
        self.assertEqual(payload["manifest_sha256"], payload["approved_sha256"])
        self.assertFalse(payload["manifest_mutated"])
        by_feat = {n["feature"]: n for n in payload["nodes"]}
        self.assertEqual(by_feat["260722-a"]["status"], "done")
        self.assertTrue(by_feat["260722-a"]["merged"])
        self.assertEqual(by_feat["260722-b"]["status"], "pending")

    def test_status_reports_manifest_mutation(self):
        import io
        import contextlib
        import json as _json
        man = self._write_manifest(
            "260722-st", nodes=[("260722-a", "none", False, [])])
        self._seed("260722-a", status="ready", open_markers=1, done_markers=0)
        CLI._graph_ledger_append(self.kdir, "260722-st", {
            "event": "approval", "graph": "260722-st",
            "manifest_sha256": CLI._graph_manifest_sha256(man), "ts": self.NOW})
        # mutate the manifest concurrency (a content change).
        man["concurrency"] = 5
        with open(CLI._graph_manifest_path(self.kdir, "260722-st"),
                  "w", encoding="utf-8") as f:
            _json.dump(man, f)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            CLI.cmd_graph_status(self._args(name="260722-st", format="json"))
        payload = _json.loads(buf.getvalue())
        self.assertTrue(payload["manifest_mutated"])
        self.assertNotEqual(payload["approved_sha256"],
                            payload["manifest_sha256"])
