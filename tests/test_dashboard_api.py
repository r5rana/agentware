"""Tests for the read-only dashboard JSON API (Task 9).

The HTTP server lives in `scripts/agentware_dashboard.py` (a sidecar the import
guard never scans). These tests stand up a SYNTHETIC knowledge base (sessions +
metrics.jsonl emission + benchmark ledger + a work feature), start the real
server bound to it, urllib-GET every `/api/*` endpoint, and assert the JSON
shape (aggregate + drill-down records). Tests may freely use urllib/http —
only the CLI source is moat-guarded.

R-LOC-03: all fixtures are synthetic/hand-authored; no operator data is touched.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from importlib.machinery import SourceFileLoader

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_PATH = os.path.join(REPO_ROOT, "scripts", "agentware_dashboard.py")

try:
    from tests._fixtures import build_synthetic_kb
except ImportError:  # allow `python3 -m unittest tests.test_dashboard_api`
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _fixtures import build_synthetic_kb


SRV = SourceFileLoader("agentware_dashboard", SERVER_PATH).load_module()

FEATURE = "260101-demo-feature"


def _write_jsonl(path, objs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for o in objs:
            f.write(json.dumps(o) + "\n")


def _seed_metrics(kdir):
    """Synthetic, deterministic KB augmentation: one session transcript, a loop
    metrics.jsonl emission (per-iteration + task transition + terminal), a
    benchmark ledger with corpus_size, and a work feature with .loop state."""
    # --- a session transcript (logs/sessions/<sid>/main.jsonl) ---------------
    sid = "sess-demo"
    sdir = os.path.join(kdir, "logs", "sessions", sid)
    usage = {"input_tokens": 1000, "output_tokens": 200,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 500}
    main_lines = [
        {"type": "user",
         "message": {"role": "user",
                     "content": "work on work/%s plan" % FEATURE},
         "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "assistant",
         "message": {"role": "assistant", "model": "claude-sonnet-4",
                     "usage": usage,
                     "content": [{"type": "thinking", "thinking": "."},
                                 {"type": "tool_use", "name": "Read",
                                  "input": {}}]},
         "timestamp": "2026-01-01T00:01:00Z"},
    ]
    _write_jsonl(os.path.join(sdir, "main.jsonl"), main_lines)
    # a live.jsonl per-action stream for the trace endpoint (Task 29)
    _write_jsonl(os.path.join(sdir, "live.jsonl"), [
        {"ts": "2026-01-01T00:01:00Z", "tool": "Read", "status": "ok",
         "input": json.dumps({"file_path": "/x"}),
         "response": json.dumps({"content": "hi"})},
    ])

    # --- loop emission channel (logs/metrics.jsonl) --------------------------
    metrics = [
        {"ts": "2026-01-01T00:00:30Z", "feature": FEATURE, "stage": "loop-main",
         "phase": "main", "iteration": 1, "max": 10, "tasks_total": 2,
         "tasks_remaining": 2, "tasks_done_delta": 0, "self_heal_count": 0},
        {"event": "task_transition", "ts": "2026-01-01T00:00:40Z",
         "feature": FEATURE, "iteration": 1, "task": "1",
         "from": "open", "to": "in_progress", "approx": False},
        {"ts": "2026-01-01T00:01:30Z", "feature": FEATURE, "stage": "loop-main",
         "phase": "main", "iteration": 2, "max": 10, "tasks_total": 2,
         "tasks_remaining": 1, "tasks_done_delta": 1, "self_heal_count": 0},
        {"event": "task_transition", "ts": "2026-01-01T00:01:40Z",
         "feature": FEATURE, "iteration": 2, "task": "1",
         "from": "in_progress", "to": "done", "approx": False},
        {"event": "terminal", "ts": "2026-01-01T00:02:00Z", "feature": FEATURE,
         "outcome": "completed", "iterations_used": 2, "max": 10,
         "self_heal_count": 0, "tasks_total": 2, "tasks_done": 2},
    ]
    _write_jsonl(os.path.join(kdir, "logs", "metrics.jsonl"), metrics)

    # --- benchmark ledger with corpus_size (for /api/scaling slope) ----------
    ledger = [
        {"schema": "ledger/v1", "run": "2026-01-01T00:00:00Z", "commit": "aaa",
         "strategy": "bm25", "suite": None, "metrics": {"recall_at_k": 0.40},
         "corpus_size": 10, "reliability": 75.0},
        {"schema": "ledger/v1", "run": "2026-01-02T00:00:00Z", "commit": "bbb",
         "strategy": "bm25", "suite": None, "metrics": {"recall_at_k": 0.60},
         "corpus_size": 20, "reliability": 82.0},
    ]
    _write_jsonl(os.path.join(kdir, "benchmarks", "history.jsonl"), ledger)

    # --- a work feature with plan markers + .loop state ----------------------
    fdir = os.path.join(kdir, "work", FEATURE)
    os.makedirs(os.path.join(fdir, ".loop"), exist_ok=True)
    with open(os.path.join(fdir, "plan.md"), "w", encoding="utf-8") as f:
        f.write("# Plan\n\n- ✅ **1** done task\n- 🟡 **2** in-progress task\n")
    with open(os.path.join(fdir, ".loop", ".iteration"), "w") as f:
        f.write("2\n")
    # --- a post-phase self-assessment (for /api/assessments/<feature>) --------
    with open(os.path.join(fdir, "assessment.md"), "w", encoding="utf-8") as f:
        f.write("# Post-Execution Assessment — `%s`\n\n"
                "> Verdict: **PASS** ✅\n\nAll tasks verified.\n" % FEATURE)


class DashboardApiTestCase(unittest.TestCase):
    """Spins up the real server bound to a synthetic KB on an ephemeral port."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-api-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        _seed_metrics(self.kdir)
        self.dist = tempfile.mkdtemp(prefix="agentware-api-dist-")
        self.addCleanup(shutil.rmtree, self.dist, True)

        self.httpd = SRV.make_server(host="127.0.0.1", port=0,
                                     dist_dir=self.dist, kdir=self.kdir)
        self.addCleanup(self.httpd.server_close)
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)
        host, port = self.httpd.server_address[0], self.httpd.server_address[1]
        self.base = "http://%s:%s" % (host, port)

    def _get(self, path):
        req = urllib.request.Request(self.base + path)
        resp = urllib.request.urlopen(req, timeout=10)
        self.assertEqual(resp.status, 200)
        self.assertIn("application/json", resp.headers.get("Content-Type", ""))
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        return json.loads(resp.read().decode("utf-8"))

    # -- aggregate endpoints --------------------------------------------------
    def test_health(self):
        data = self._get("/api/health")
        self.assertIn("ok", data)
        self.assertIsInstance(data["checks"], list)
        names = {c["name"] for c in data["checks"]}
        self.assertIn("index_validate", names)

    def test_quality(self):
        data = self._get("/api/quality")
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["latest"]["commit"], "bbb")
        self.assertEqual(len(data["series"]), 2)

    def test_loop(self):
        data = self._get("/api/loop")
        feats = {f["feature"]: f for f in data["features"]}
        self.assertIn(FEATURE, feats)
        f = feats[FEATURE]
        self.assertEqual(f["iteration"], 2)
        self.assertEqual(f["tasks_done"], 1)
        self.assertEqual(f["tasks_open"], 1)
        self.assertEqual(f["outcome"]["outcome"], "completed")
        self.assertGreaterEqual(len(data["recent_events"]), 5)

    def test_loop_analytics(self):
        data = self._get("/api/loop-analytics")
        # Aggregate shape: per-feature analytics + loop throughput.
        feats = {f["feature"]: f for f in data["features"]}
        self.assertIn(FEATURE, feats)
        f = feats[FEATURE]
        # Two main iterations in the fixture: burndown 2 -> 1.
        self.assertEqual([(b["iteration"], b["tasks_remaining"])
                          for b in f["burndown"]], [(1, 2), (2, 1)])
        self.assertEqual(f["iterations_to_completion"], 2)
        self.assertEqual(f["outcome"], "completed")
        # iteration efficiency present (tasks closed / iterations).
        self.assertIsNotNone(f["iteration_efficiency"])
        # gate buckets always present (pre/post hook outcomes).
        self.assertIn("pre", f["gates"])
        self.assertIn("post", f["gates"])
        # Throughput counts the one completed feature.
        self.assertGreaterEqual(data["throughput"]["completed_total"], 1)

    def test_loop_health(self):
        data = self._get("/api/loop-health")
        feats = {f["feature"]: f for f in data["features"]}
        self.assertIn(FEATURE, feats)
        f = feats[FEATURE]
        # The completed two-iteration fixture (no dups, burndown 2->1) is healthy.
        self.assertEqual(f["status"], "ok")
        self.assertIn("duplicate_tool_calls", f["checks"])
        self.assertIn("no_progress", f["checks"])
        self.assertIn("token_burn", f["checks"])
        self.assertIn("context_window", f["checks"])
        self.assertIn("summary", data)
        self.assertIn(data["status"], ("ok", "at_risk", "critical"))

    def test_alerts(self):
        data = self._get("/api/alerts")
        # Shape: ranked alerts + severity summary + commit markers (Task 31).
        self.assertIsInstance(data["alerts"], list)
        self.assertIn("summary", data)
        for k in ("critical", "warning", "info"):
            self.assertIn(k, data["summary"])
        self.assertIn(data["status"], ("ok", "info", "warning", "critical"))
        self.assertEqual(data["open_count"], len(data["alerts"]))
        # Commit markers expose the ledger SHAs for the trend charts.
        shas = [m["commit"] for m in data["commit_markers"]]
        self.assertEqual(shas, ["aaa", "bbb"])
        # Every alert carries a deep-link and a known severity.
        for a in data["alerts"]:
            self.assertIn(a["severity"], ("critical", "warning", "info"))
            self.assertIsNotNone(a["deep_link"])

    def test_failures_feature(self):
        # Failure-ladder & error-recovery drill-down (Task 32). The demo fixture
        # has one healthy (ok) live step, so ERR rate is 0 and the ladder empty.
        data = self._get("/api/failures/%s" % FEATURE)
        self.assertEqual(data["feature"], FEATURE)
        self.assertEqual(data["scope"], "feature")
        self.assertGreaterEqual(data["step_count"], 1)
        self.assertEqual(data["err_count"], 0)
        self.assertEqual(data["err_rate"], 0.0)
        self.assertEqual(data["ladder_order"],
                         ["kb", "reasoning", "inputs", "switch", "web"])
        for tier in data["ladder_order"]:
            self.assertIn(tier, data["ladder"])
        self.assertIn("web_search_count", data)
        self.assertIn("self_heal_count", data)
        self.assertIn("learned", data["markers"])
        self.assertIn("decision", data["markers"])

    def test_evals(self):
        # Evaluation & quality trend (Task 33): the eval ledger split from ACR
        # gate rows. The shared synthetic ledger has 2 eval rows, no ACR rows.
        data = self._get("/api/evals")
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["acr_count"], 0)
        self.assertEqual(data["acr"], [])
        self.assertEqual(data["latest"]["commit"], "bbb")
        self.assertEqual(data["latest"]["reliability"], 82.0)
        self.assertEqual(data["latest"]["recall_at_k"], 0.60)
        self.assertIsNone(data["latest_acr"])

    def test_evals_acr_split(self):
        # An ACR-gate ledger row is SPLIT out of the eval series into `acr`.
        import types
        kdir = tempfile.mkdtemp(prefix="agentware-evals-acr-")
        self.addCleanup(shutil.rmtree, kdir, True)
        _write_jsonl(os.path.join(kdir, "benchmarks", "history.jsonl"), [
            {"schema": "ledger/v1", "run": "r1", "commit": "e1", "mode": "eval",
             "strategy": "bm25",
             "metrics": {"recall_at_k": 0.5, "ndcg_at_k": 0.4, "mrr": 0.45},
             "reliability": 80.0},
            {"schema": "ledger/v1", "run": "r2", "commit": "g1",
             "mode": "acr-gate",
             "acr_gate": {"decided_strategy": "bm25", "passed": False,
                          "checks": {"primary": {"passed": False}}}},
        ])
        _status, data = SRV.api_evals(types.SimpleNamespace(kdir=kdir))
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["acr_count"], 1)
        self.assertEqual(data["series"][0]["commit"], "e1")
        self.assertEqual(data["acr"][0]["decided_strategy"], "bm25")
        self.assertEqual(data["acr"][0]["passed"], False)
        self.assertEqual(data["latest_acr"]["commit"], "g1")

    def test_assessments_feature(self):
        # Post-phase self-assessment text for a feature (Task 33).
        data = self._get("/api/assessments/%s" % FEATURE)
        self.assertEqual(data["feature"], FEATURE)
        self.assertTrue(data["exists"])
        self.assertIn("Post-Execution Assessment", data["text"])
        self.assertGreater(data["bytes"], 0)
        self.assertTrue(data["path"].endswith("assessment.md"))

    def test_assessments_missing(self):
        data = self._get("/api/assessments/no-such-feature")
        self.assertFalse(data["exists"])
        self.assertEqual(data["text"], "")
        self.assertIsNone(data["path"])

    def test_assessments_path_traversal_confined(self):
        # A `..` escape is canonicalized + confined under <kdir>/work (security).
        import types
        _status, data = SRV.api_assessments(
            types.SimpleNamespace(kdir=self.kdir), "../../../etc/passwd")
        self.assertFalse(data["exists"])
        self.assertEqual(data.get("error"), "invalid feature")
        self.assertEqual(data["text"], "")

    def test_loop_health_feature_drilldown(self):
        data = self._get("/api/loop-health/%s" % FEATURE)
        self.assertEqual(data["feature"], FEATURE)
        self.assertIn("checks", data)
        self.assertIn(data["status"], ("ok", "at_risk", "critical"))

    def test_trace_feature(self):
        # Feature-scoped: collects the demo session, groups steps by iteration.
        data = self._get("/api/trace/%s" % FEATURE)
        self.assertEqual(data["scope"], "feature")
        self.assertEqual(data["feature"], FEATURE)
        self.assertGreaterEqual(data["step_count"], 1)
        self.assertEqual(data["tool_summary"].get("Read"), 1)
        # The single live step (09:01) lands in main iteration 2 (>=00:01:30? no:
        # it falls in iteration 1's window which opens at 00:00:30).
        groups = {g["iteration"]: g for g in data["iterations"]}
        self.assertTrue(any(g["steps"] for g in data["iterations"]))
        self.assertIn(1, groups)

    def test_trace_session(self):
        # Session-scoped: target resolves as a session dir first.
        data = self._get("/api/trace/sess-demo")
        self.assertEqual(data["scope"], "session")
        self.assertEqual(data["session"], "sess-demo")
        step = data["iterations"][0]["steps"][0]
        self.assertEqual(step["tool"], "Read")
        self.assertIn("content", step["result"])

    def test_cost(self):
        data = self._get("/api/cost")
        self.assertGreaterEqual(data["session_count"], 1)
        agg = data["aggregate"]
        self.assertIn("cost_usd", agg)
        self.assertIn("cache_read_ratio", agg)
        self.assertIn("context_tax", agg)
        self.assertIn("phase_costs", agg)

    def test_authoring(self):
        data = self._get("/api/authoring")
        self.assertIn("authoring", data)
        self.assertGreaterEqual(data["session_count"], 1)

    def test_context_tax(self):
        data = self._get("/api/context-tax")
        ct = data["context_tax"]
        self.assertIn("cache_read_per_turn", ct)
        self.assertIn("injected_tokens", ct)

    def test_scaling(self):
        data = self._get("/api/scaling")
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["measured"], 2)
        # recall rises 0.40 -> 0.60 as corpus 10 -> 20: slope = 0.2/10 = 0.02.
        self.assertAlmostEqual(data["slope"], 0.02, places=4)

    def test_outcomes(self):
        data = self._get("/api/outcomes")
        feats = {f["feature"]: f for f in data["features"]}
        self.assertEqual(feats[FEATURE]["outcome"], "completed")
        self.assertGreaterEqual(data["summary"].get("completed", 0), 1)

    def test_features(self):
        data = self._get("/api/features")
        self.assertIn("learnings", data["categories"])
        self.assertGreaterEqual(data["entry_count"], 1)

    # -- kb aggregate + drill-downs -------------------------------------------
    def test_kb_aggregate(self):
        data = self._get("/api/kb")
        self.assertGreaterEqual(data["entry_count"], 1)
        self.assertIn("learnings", data["categories"])
        self.assertTrue(data["entries"])

    def test_kb_learnings_list(self):
        data = self._get("/api/kb/learnings")
        self.assertEqual(data["category"], "learnings")
        self.assertTrue(all(e["category"] == "learnings" for e in data["entries"]))
        self.assertTrue(data["entries"])

    def test_kb_projects_list(self):
        data = self._get("/api/kb/projects")
        self.assertEqual(data["category"], "projects")

    def test_kb_learning_detail(self):
        listing = self._get("/api/kb/learnings")
        eid = listing["entries"][0]["id"]
        data = self._get("/api/kb/learnings/%s" % eid)
        self.assertEqual(data["entry"]["id"], eid)
        self.assertIsInstance(data["body"], str)

    def test_kb_learning_detail_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/kb/learnings/does-not-exist")
        self.assertEqual(cm.exception.code, 404)

    def test_kb_tag_drilldown(self):
        # The synthetic fixture tags a learning with 'geofence'.
        data = self._get("/api/kb/tags/geofence")
        self.assertEqual(data["tag"], "geofence")
        self.assertTrue(data["entries"])
        self.assertTrue(all("geofence" in e.get("tags", [])
                            for e in data["entries"]))

    def test_tasks_drilldown(self):
        data = self._get("/api/tasks/%s" % FEATURE)
        self.assertEqual(data["feature"], FEATURE)
        self.assertEqual(data["transition_count"], 2)
        self.assertEqual(data["plan"]["done"], 1)
        self.assertEqual(data["plan"]["open"], 1)

    # -- routing edge cases ---------------------------------------------------
    def test_unknown_api_route_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/nope")
        self.assertEqual(cm.exception.code, 404)


class NoKdirGracefulTest(unittest.TestCase):
    """With no knowledge dir bound, data endpoints degrade gracefully (200 +
    empty shape) rather than 500 — the SPA can still render empty states."""

    def setUp(self):
        self.dist = tempfile.mkdtemp(prefix="agentware-api-dist-")
        self.addCleanup(shutil.rmtree, self.dist, True)
        self.httpd = SRV.make_server(host="127.0.0.1", port=0,
                                     dist_dir=self.dist, kdir=None)
        self.addCleanup(self.httpd.server_close)
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)
        host, port = self.httpd.server_address[0], self.httpd.server_address[1]
        self.base = "http://%s:%s" % (host, port)

    def test_endpoints_degrade(self):
        for path in ("/api/loop", "/api/cost", "/api/kb", "/api/scaling",
                     "/api/outcomes", "/api/failures/some-feature",
                     "/api/evals", "/api/assessments/some-feature"):
            req = urllib.request.Request(self.base + path)
            resp = urllib.request.urlopen(req, timeout=10)
            self.assertEqual(resp.status, 200, path)
            json.loads(resp.read().decode("utf-8"))  # parses


if __name__ == "__main__":
    unittest.main()
