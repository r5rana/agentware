"""Loop-endpoint performance + correctness guards (feature 260626-dashboard-loop-perf).

These pin the single-pass + cached-read refactor of the dashboard's loop
derivations so a future change cannot silently:
  * drift the JSON contract (golden equivalence: the shared single-pass index
    path must be BYTE-IDENTICAL to the original per-feature scan path), or
  * regress the O(features × sessions × file) hot path back (read-counter spy +
    a relative perf bound), or
  * break live-run correctness (cache must invalidate on file change).

Stdlib-only, hermetic (synthetic KB via tests/_fixtures.build_large_loop_kb —
never the operator KB, R-LOC-03).
"""

import builtins
import json
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _fixtures import load_cli, build_large_loop_kb  # noqa: E402

import tempfile  # noqa: E402
import shutil  # noqa: E402


def _canon(obj):
    return json.dumps(obj, sort_keys=True, default=str)


class LoopPerfTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = load_cli()
        cls.kdir = tempfile.mkdtemp(prefix="aw-loopperf-")
        cls.features = build_large_loop_kb(
            cls.kdir, n_features=20, sessions_per_feature=4,
            turns_per_session=8, filler_chars=120)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.kdir, ignore_errors=True)

    def setUp(self):
        self.cli._reset_read_cache()

    # -- Golden equivalence: indexed bulk path == per-feature fallback ---------
    def test_loop_analytics_indexed_equals_per_feature(self):
        cli, kdir = self.cli, self.kdir
        indexed = cli.derive_loop_analytics(kdir)
        feats = cli.list_loop_features(kdir)
        fallback = {
            "features": [cli._loop_analytics_for_feature(kdir, f) for f in feats],
            "throughput": cli.derive_loop_throughput(kdir),
        }
        self.assertEqual(_canon(indexed), _canon(fallback))

    def test_loop_health_indexed_equals_per_feature(self):
        cli, kdir = self.cli, self.kdir
        indexed = cli.derive_loop_health(kdir)
        feats = cli.list_loop_features(kdir)
        fb_feats = [cli._loop_health_for_feature(kdir, f) for f in feats]
        summary = {"ok": 0, "at_risk": 0, "critical": 0}
        for f in fb_feats:
            summary[f["status"]] = summary.get(f["status"], 0) + 1
        fallback = {"features": fb_feats, "summary": summary,
                    "status": cli._worst_status(f["status"] for f in fb_feats)}
        self.assertEqual(_canon(indexed), _canon(fallback))

    def test_session_index_membership_and_order_match(self):
        cli, kdir = self.cli, self.kdir
        feats = cli.list_loop_features(kdir)
        idx = cli.build_session_index(kdir, feats)
        for f in feats:
            self.assertEqual(_canon(idx["rows"][f]),
                             _canon(cli.collect_sessions(kdir, feature=f)),
                             "rows for %s diverge from collect_sessions" % f)
            self.assertEqual([d for _, d in idx["dirs"][f]],
                             [d for _, d in cli._trace_session_dirs(kdir, feature=f)],
                             "trace dirs for %s diverge" % f)

    def test_grouped_events_equal_filtered_reads(self):
        cli, kdir = self.cli, self.kdir
        allev = cli._read_metrics_jsonl(kdir)
        grouped = cli._group_events_by_feature(allev)
        for f in cli.list_loop_features(kdir):
            self.assertEqual(_canon(grouped.get(f, [])),
                             _canon(cli._read_metrics_jsonl(kdir, feature=f)))

    # -- Single-pass: each file opened a BOUNDED number of times per request --
    def test_metrics_jsonl_read_once_per_request(self):
        cli, kdir = self.cli, self.kdir
        metrics_path = os.path.join(kdir, "logs", "metrics.jsonl")
        opens = {"n": 0}
        real_open = builtins.open

        def counting_open(path, *a, **k):
            if isinstance(path, str) and os.path.abspath(path) == \
                    os.path.abspath(metrics_path):
                opens["n"] += 1
            return real_open(path, *a, **k)

        cli._reset_read_cache()
        builtins.open = counting_open
        try:
            cli.derive_loop_analytics(kdir)  # touches every feature
        finally:
            builtins.open = real_open
        # Without the cache this was once PER FEATURE (>=20). One full read now.
        self.assertEqual(opens["n"], 1,
                         "metrics.jsonl should be read exactly once per request")

    def test_session_transcript_opens_bounded(self):
        """Every session main.jsonl is read O(1) times per bulk request, NOT
        once per feature (the old O(features × sessions) blow-up)."""
        cli, kdir = self.cli, self.kdir
        # Pick one real session main.jsonl.
        sroot = cli.logs_sessions_dir(kdir)
        some = sorted(os.listdir(sroot))[0]
        target = os.path.join(sroot, some, "main.jsonl")
        opens = {"n": 0}
        real_open = builtins.open

        def counting_open(path, *a, **k):
            if isinstance(path, str) and os.path.abspath(path) == \
                    os.path.abspath(target):
                opens["n"] += 1
            return real_open(path, *a, **k)

        cli._reset_read_cache()
        builtins.open = counting_open
        try:
            cli.derive_loop_health(kdir)  # analytics + dup + context per feature
        finally:
            builtins.open = real_open
        n_features = len(cli.list_loop_features(kdir))
        # text-cache + parse cache => a small constant, FAR below per-feature.
        self.assertLessEqual(opens["n"], 4,
                             "main.jsonl opened %d times (should be O(1), not "
                             "O(features)=%d)" % (opens["n"], n_features))

    # -- Cache invalidation: live-run correctness -----------------------------
    def test_parse_session_cache_hits_then_invalidates(self):
        cli = self.cli
        sub = tempfile.mkdtemp(prefix="aw-cacheinv-")
        self.addCleanup(shutil.rmtree, sub, True)
        sdir = os.path.join(sub, "sess1")
        os.makedirs(sdir)
        main = os.path.join(sdir, "main.jsonl")
        with open(main, "w") as f:
            f.write(json.dumps({"type": "assistant", "ts": "2026-01-01T00:00:00Z",
                                "message": {"usage": {"output_tokens": 10},
                                            "content": []}}) + "\n")
        calls = {"n": 0}
        orig = cli._parse_session_uncached

        def spy(d):
            calls["n"] += 1
            return orig(d)

        cli._parse_session_uncached = spy
        try:
            cli._reset_read_cache()
            r1 = cli.parse_session(sdir)
            r2 = cli.parse_session(sdir)        # cache HIT — no recompute
            self.assertEqual(calls["n"], 1)
            self.assertEqual(_canon(r1), _canon(r2))
            # Mutating the returned row must NOT corrupt the cache.
            r2["total_tokens"] = -999
            r3 = cli.parse_session(sdir)
            self.assertNotEqual(r3["total_tokens"], -999)
            self.assertEqual(calls["n"], 1)
            # Change the file => key changes => recompute (fresh data).
            time.sleep(0.01)
            with open(main, "a") as f:
                f.write(json.dumps({"type": "assistant",
                                    "ts": "2026-01-01T00:01:00Z",
                                    "message": {"usage": {"output_tokens": 20},
                                                "content": []}}) + "\n")
            r4 = cli.parse_session(sdir)
            self.assertEqual(calls["n"], 2, "stale cache served after file change")
            self.assertGreater(r4["turns"], r1["turns"])
        finally:
            cli._parse_session_uncached = orig

    def test_concurrent_access_no_corruption(self):
        """ThreadingHTTPServer => the cache must be safe under concurrent reads."""
        cli, kdir = self.cli, self.kdir
        cli._reset_read_cache()
        baseline = _canon(cli.derive_loop_analytics(kdir))
        errors = []

        def worker():
            try:
                for _ in range(3):
                    self.assertEqual(_canon(cli.derive_loop_analytics(kdir)),
                                     baseline)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    # -- Perf guard: the indexed bulk path beats the per-feature path ----------
    def test_indexed_path_substantially_faster(self):
        cli, kdir = self.cli, self.kdir
        feats = cli.list_loop_features(kdir)

        def per_feature():
            cli._reset_read_cache()
            return [cli._loop_health_for_feature(kdir, f) for f in feats]

        def indexed():
            cli._reset_read_cache()
            return cli.derive_loop_health(kdir)

        def best(fn, reps=3):
            b = None
            for _ in range(reps):
                s = time.perf_counter()
                fn()
                e = time.perf_counter() - s
                b = e if b is None else min(b, e)
            return b

        t_pf = best(per_feature)
        t_ix = best(indexed)
        # The single-pass index removes the 3×-per-feature session re-scan; on any
        # non-trivial KB it is clearly faster. Conservative 1.5× to avoid CI flake.
        self.assertLess(t_ix, t_pf / 1.5,
                        "indexed=%.3fs not >=1.5x faster than per-feature=%.3fs"
                        % (t_ix, t_pf))


class LoopPaginationTestCase(unittest.TestCase):
    """/api/loop honors optional ?limit=/?feature= WITHOUT changing the default
    (no-param) contract (feature Task 5)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        from importlib.machinery import SourceFileLoader
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo, "scripts", "agentware_dashboard.py")
        loader = SourceFileLoader("agentware_dashboard", path)
        spec = importlib.util.spec_from_loader("agentware_dashboard", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        cls.SRV = mod
        cls.cli = load_cli()
        cls.kdir = tempfile.mkdtemp(prefix="aw-loadpage-")
        cls.features = build_large_loop_kb(
            cls.kdir, n_features=12, sessions_per_feature=2,
            turns_per_session=4, filler_chars=0)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.kdir, ignore_errors=True)

    def _req(self, **qp):
        class _R:
            pass
        r = _R()
        r.kdir = self.kdir
        r.query_params = {k: [str(v)] for k, v in qp.items()}
        return r

    def test_default_contract_unchanged(self):
        status, payload = self.SRV.api_loop(self._req())
        self.assertEqual(int(status), 200)
        self.assertIn("features", payload)
        self.assertIn("recent_events", payload)
        self.assertLessEqual(len(payload["recent_events"]), 50)
        # Default tail == last 50 of the full chronological channel.
        full = self.cli._read_metrics_jsonl(self.kdir)
        self.assertEqual(payload["recent_events"], full[-50:])

    def test_limit_caps_tail(self):
        _s, payload = self.SRV.api_loop(self._req(limit=5))
        self.assertLessEqual(len(payload["recent_events"]), 5)
        full = self.cli._read_metrics_jsonl(self.kdir)
        self.assertEqual(payload["recent_events"], full[-5:])
        # features list is the full set, unaffected by the event-tail limit.
        self.assertEqual(len(payload["features"]),
                         len(self.cli.list_loop_features(self.kdir)))

    def test_feature_scopes_tail(self):
        f = self.features[0]
        _s, payload = self.SRV.api_loop(self._req(feature=f))
        for e in payload["recent_events"]:
            self.assertEqual(e.get("feature"), f)

    def test_invalid_limit_falls_back_to_default(self):
        _s, payload = self.SRV.api_loop(self._req(limit="abc"))
        full = self.cli._read_metrics_jsonl(self.kdir)
        self.assertEqual(payload["recent_events"], full[-50:])


class BrokenPipeHardeningTestCase(unittest.TestCase):
    """A client that disconnects mid-response must NOT produce a traceback —
    the write raises BrokenPipeError/ConnectionResetError, which the handler
    swallows with a one-line note (feature Task 9)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        from importlib.machinery import SourceFileLoader
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo, "scripts", "agentware_dashboard.py")
        loader = SourceFileLoader("agentware_dashboard", path)
        spec = importlib.util.spec_from_loader("agentware_dashboard", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        cls.SRV = mod

    def _make_handler(self):
        # Bypass the socket-bound __init__; we only exercise do_GET's dispatch.
        h = self.SRV.DashboardHandler.__new__(self.SRV.DashboardHandler)
        h.path = "/api/health"
        h.command = "GET"
        h.close_connection = False
        return h

    def test_broken_pipe_is_swallowed(self):
        for exc_cls in (BrokenPipeError, ConnectionResetError,
                        ConnectionAbortedError):
            h = self._make_handler()

            def boom(_path, _exc=exc_cls):
                raise _exc()

            h._handle_api = boom
            captured = io = __import__("io").StringIO()
            real_stderr = sys.stderr
            sys.stderr = captured
            try:
                # Must NOT raise.
                result = self.SRV.DashboardHandler.do_GET(h)
            finally:
                sys.stderr = real_stderr
            self.assertIsNone(result)
            self.assertTrue(h.close_connection,
                            "disconnect should mark the connection closed")
            self.assertIn("client disconnected", captured.getvalue())
            self.assertNotIn("Traceback", captured.getvalue())

    def test_real_dispatch_still_runs(self):
        # A non-disconnect path is unaffected: a normal API error is handled by
        # _handle_api's own 500 path, not swallowed by the disconnect guard.
        h = self._make_handler()
        seen = {"called": False}

        def ok(_path):
            seen["called"] = True
            return None

        h._handle_api = ok
        self.SRV.DashboardHandler.do_GET(h)
        self.assertTrue(seen["called"])

    def test_server_handle_error_swallows_disconnects(self):
        # The server-level backstop: a connection-family error raised in EITHER
        # half of the exchange (request-read reset OR response-write broken pipe)
        # must be swallowed by _DashboardServer.handle_error WITHOUT delegating to
        # the stdlib base (which prints a traceback). Real errors still delegate.
        srv = self.SRV._DashboardServer.__new__(self.SRV._DashboardServer)
        delegated = {"n": 0}

        # Monkeypatch the base handle_error to detect delegation.
        import socketserver

        orig = socketserver.BaseServer.handle_error
        socketserver.BaseServer.handle_error = lambda *_a, **_k: \
            delegated.__setitem__("n", delegated["n"] + 1)
        try:
            for exc in (ConnectionResetError(), BrokenPipeError(),
                        ConnectionAbortedError()):
                try:
                    raise exc
                except Exception:  # noqa: BLE001 — populate sys.exc_info()
                    srv.handle_error(None, ("127.0.0.1", 0))
            self.assertEqual(delegated["n"], 0, "disconnect should NOT delegate")
            # A genuine error DOES delegate (keeps its traceback).
            try:
                raise ValueError("real bug")
            except Exception:  # noqa: BLE001
                srv.handle_error(None, ("127.0.0.1", 0))
            self.assertEqual(delegated["n"], 1)
        finally:
            socketserver.BaseServer.handle_error = orig


if __name__ == "__main__":
    unittest.main()
