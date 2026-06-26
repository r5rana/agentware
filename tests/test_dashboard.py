"""Tests for the dashboard server sidecar (Task 8).

The HTTP server lives in `scripts/agentware_dashboard.py` (a sidecar the import
guard never scans, so it may use `http.server`). These tests import it directly
and may freely use `urllib`/`http` (only the CLI source is moat-guarded).

Coverage:
  * binds 127.0.0.1 and a GET of the static bundle returns 200 (Task 8 verify);
  * a non-loopback host is refused (bind 127.0.0.1 ONLY);
  * the hardened static handler confines paths under dist/ (path traversal +
    disallowed extensions are 404'd) and sets safe headers;
  * the SPA fallback serves index.html for extensionless client routes;
  * the `dashboard` subcommand is registered on the CLI.
"""

import os
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from importlib.machinery import SourceFileLoader

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_PATH = os.path.join(REPO_ROOT, "scripts", "agentware_dashboard.py")

try:
    from tests._fixtures import load_cli, build_synthetic_kb
except ImportError:  # allow `python3 -m unittest tests.test_dashboard`
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _fixtures import load_cli, build_synthetic_kb


def _load_server():
    return SourceFileLoader("agentware_dashboard", SERVER_PATH).load_module()


SRV = _load_server()


class DashboardServerTestCase(unittest.TestCase):
    """Spins up the real server on an ephemeral loopback port per test."""

    def setUp(self):
        self.dist = tempfile.mkdtemp(prefix="agentware-dist-")
        self.addCleanup(self._rmtree, self.dist)
        # A minimal built bundle.
        with open(os.path.join(self.dist, "index.html"), "w") as f:
            f.write("<!doctype html><title>agentware</title><div id=root></div>")
        os.makedirs(os.path.join(self.dist, "assets"), exist_ok=True)
        with open(os.path.join(self.dist, "assets", "app.js"), "w") as f:
            f.write("console.log('hi')")
        # A file with a disallowed extension must NEVER be served.
        with open(os.path.join(self.dist, "secret.pem"), "w") as f:
            f.write("PRIVATE KEY")

        self.httpd = SRV.make_server(host="127.0.0.1", port=0,
                                     dist_dir=self.dist, kdir=None)
        self.addCleanup(self.httpd.server_close)
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)
        host, port = self.httpd.server_address[0], self.httpd.server_address[1]
        self.base = "http://%s:%s" % (host, port)

    @staticmethod
    def _rmtree(path):
        import shutil
        shutil.rmtree(path, ignore_errors=True)

    def _get(self, path):
        req = urllib.request.Request(self.base + path)
        return urllib.request.urlopen(req, timeout=5)

    # -- the Task 8 verify ----------------------------------------------------
    def test_binds_loopback(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")

    def test_get_index_returns_200(self):
        resp = self._get("/")
        self.assertEqual(resp.status, 200)
        body = resp.read().decode("utf-8")
        self.assertIn("agentware", body)
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))
        # frame-ancestors is enforceable ONLY as a header (ignored in a <meta>
        # CSP), so the server delivers anti-clickjacking via real headers.
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
        self.assertIn(
            "frame-ancestors 'none'",
            resp.headers.get("Content-Security-Policy", ""),
        )

    def test_get_asset_returns_200(self):
        resp = self._get("/assets/app.js")
        self.assertEqual(resp.status, 200)
        self.assertIn("javascript", resp.headers.get("Content-Type", ""))

    # -- security hardening ---------------------------------------------------
    def test_non_loopback_host_refused(self):
        with self.assertRaises(ValueError):
            SRV.make_server(host="0.0.0.0", port=0, dist_dir=self.dist)

    def test_path_traversal_rejected(self):
        # A real allowed-extension file SITTING OUTSIDE dist/ must be
        # unreachable via traversal (normpath collapses `..` so the candidate
        # never escapes the confined root -> the sibling is never served).
        outside = os.path.join(os.path.dirname(self.dist), "outside-secret.js")
        with open(outside, "w") as f:
            f.write("LEAK")
        self.addCleanup(lambda: os.path.exists(outside) and os.remove(outside))
        for evil in ("/..%2foutside-secret.js",
                     "/assets%2f..%2f..%2foutside-secret.js",
                     "/assets/../../outside-secret.js"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._get(evil)
            self.assertEqual(cm.exception.code, 404, evil)

    def test_disallowed_extension_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/secret.pem")
        self.assertEqual(cm.exception.code, 404)

    def test_spa_fallback_serves_index(self):
        # An extensionless client route falls back to index.html (SPA routing).
        resp = self._get("/loops/some-feature")
        self.assertEqual(resp.status, 200)
        self.assertIn("agentware", resp.read().decode("utf-8"))

    def test_unknown_file_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/assets/missing.js")
        self.assertEqual(cm.exception.code, 404)

    def test_api_liveness_probe(self):
        import json
        resp = self._get("/api/ping")
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "agentware-dashboard")

    def test_unknown_api_route_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/does-not-exist")
        self.assertEqual(cm.exception.code, 404)


class SafePathUnitTest(unittest.TestCase):
    """Direct unit coverage of the path-confinement helper."""

    def setUp(self):
        import shutil
        self.dist = tempfile.mkdtemp(prefix="agentware-dist-")
        self.addCleanup(shutil.rmtree, self.dist, True)
        with open(os.path.join(self.dist, "index.html"), "w") as f:
            f.write("x")

    def test_root_maps_to_index(self):
        p = SRV._safe_static_path(self.dist, "/")
        self.assertEqual(os.path.basename(p), "index.html")

    def test_traversal_stays_confined(self):
        # `..` segments are collapsed by normpath at the absolute root, so a
        # traversal can only ever resolve to a path INSIDE dist (or None).
        root = os.path.realpath(self.dist)
        for p in ("/../agentware.js", "/a/../../b.js", "/../../x/y.js"):
            got = SRV._safe_static_path(self.dist, p)
            if got is not None:
                self.assertTrue(got == root or got.startswith(root + os.sep),
                                "%s escaped dist -> %s" % (p, got))

    def test_symlink_escape_returns_none(self):
        # A symlink inside dist pointing OUTSIDE must not be served.
        import shutil
        outside_dir = tempfile.mkdtemp(prefix="agentware-outside-")
        self.addCleanup(shutil.rmtree, outside_dir, True)
        with open(os.path.join(outside_dir, "leak.js"), "w") as f:
            f.write("LEAK")
        link = os.path.join(self.dist, "link.js")
        try:
            os.symlink(os.path.join(outside_dir, "leak.js"), link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported on this platform")
        self.assertIsNone(SRV._safe_static_path(self.dist, "/link.js"))

    def test_disallowed_ext_returns_none(self):
        self.assertIsNone(SRV._safe_static_path(self.dist, "/x.pem"))
        self.assertIsNone(SRV._safe_static_path(self.dist, "/x.py"))

    def test_allowed_ext_confined(self):
        p = SRV._safe_static_path(self.dist, "/assets/app.js")
        self.assertTrue(p.endswith(os.path.join("assets", "app.js")))
        self.assertTrue(p.startswith(os.path.realpath(self.dist) + os.sep))


class LocalhostBoundMoatTest(unittest.TestCase):
    """Task 11 guard — localhost-bound: the EFFECTIVE bind host is 127.0.0.1.

    `make_server` refuses any non-loopback host and normalizes the `localhost`
    alias to the literal loopback address, so the server can never be reachable
    off the machine (the operator is security-first; the dashboard observes a
    local KB and must not be exposed)."""

    def setUp(self):
        import shutil
        self.dist = tempfile.mkdtemp(prefix="agentware-dist-")
        self.addCleanup(shutil.rmtree, self.dist, True)
        with open(os.path.join(self.dist, "index.html"), "w") as f:
            f.write("<!doctype html><div id=root></div>")

    def _effective_host(self, host):
        httpd = SRV.make_server(host=host, port=0, dist_dir=self.dist)
        self.addCleanup(httpd.server_close)
        return httpd.server_address[0]

    def test_default_host_is_loopback(self):
        # The CLI default (no --host) binds 127.0.0.1.
        self.assertEqual(self._effective_host("127.0.0.1"), "127.0.0.1")

    def test_localhost_alias_normalizes_to_loopback(self):
        # `localhost` is accepted but the EFFECTIVE bound address is 127.0.0.1
        # (not a name that could resolve to a routable interface).
        self.assertEqual(self._effective_host("localhost"), "127.0.0.1")

    def test_non_loopback_host_refused(self):
        for evil in ("0.0.0.0", "::", "192.168.1.10", "example.com"):
            with self.assertRaises(ValueError, msg=evil):
                SRV.make_server(host=evil, port=0, dist_dir=self.dist)


class DashboardReadOnlyMoatTest(unittest.TestCase):
    """Task 11 guard — read-only: serving the dashboard NEVER mutates the KB.

    Snapshot the full synthetic KB tree (every path -> exact bytes), start the
    real server bound to it, GET `/` AND every JSON endpoint (aggregate +
    drill-down), then assert the tree is BYTE-IDENTICAL. The dashboard adds no
    writer; observation must leave the knowledge base untouched."""

    def setUp(self):
        import shutil
        self.kdir = tempfile.mkdtemp(prefix="agentware-ro-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self._seed(self.kdir)

        self.dist = tempfile.mkdtemp(prefix="agentware-ro-dist-")
        self.addCleanup(shutil.rmtree, self.dist, True)
        with open(os.path.join(self.dist, "index.html"), "w") as f:
            f.write("<!doctype html><div id=root></div>")

        self.httpd = SRV.make_server(host="127.0.0.1", port=0,
                                     dist_dir=self.dist, kdir=self.kdir)
        self.addCleanup(self.httpd.server_close)
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)
        host, port = self.httpd.server_address[0], self.httpd.server_address[1]
        self.base = "http://%s:%s" % (host, port)

    # -- synthetic, deterministic KB augmentation (no operator data, R-LOC-03) -
    @staticmethod
    def _seed(kdir):
        import json
        feature = "260101-ro-feature"

        def _write_jsonl(path, objs):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                for o in objs:
                    f.write(json.dumps(o) + "\n")

        # A loop metrics emission (per-iteration + transition + terminal).
        _write_jsonl(os.path.join(kdir, "logs", "metrics.jsonl"), [
            {"ts": "2026-01-01T00:00:30Z", "feature": feature,
             "stage": "loop-main", "phase": "main", "iteration": 1, "max": 10,
             "tasks_total": 2, "tasks_remaining": 2, "tasks_done_delta": 0,
             "self_heal_count": 0},
            {"event": "task_transition", "ts": "2026-01-01T00:00:40Z",
             "feature": feature, "iteration": 1, "task": "1",
             "from": "open", "to": "done", "approx": False},
            {"event": "terminal", "ts": "2026-01-01T00:02:00Z",
             "feature": feature, "outcome": "completed", "iterations_used": 1,
             "max": 10, "self_heal_count": 0, "tasks_total": 2, "tasks_done": 1},
        ])
        # A benchmark ledger with corpus_size (for /api/quality + /api/scaling).
        _write_jsonl(os.path.join(kdir, "benchmarks", "history.jsonl"), [
            {"schema": "ledger/v1", "run": "2026-01-01T00:00:00Z",
             "commit": "aaa", "strategy": "bm25", "suite": None,
             "metrics": {"recall_at_k": 0.40}, "corpus_size": 10,
             "reliability": 75.0},
            {"schema": "ledger/v1", "run": "2026-01-02T00:00:00Z",
             "commit": "bbb", "strategy": "bm25", "suite": None,
             "metrics": {"recall_at_k": 0.60}, "corpus_size": 20,
             "reliability": 82.0},
        ])
        # A work feature with plan markers + .loop state.
        fdir = os.path.join(kdir, "work", feature)
        os.makedirs(os.path.join(fdir, ".loop"), exist_ok=True)
        with open(os.path.join(fdir, "plan.md"), "w", encoding="utf-8") as f:
            f.write("# Plan\n\n- ✅ **1** done\n- 🟡 **2** in-progress\n")
        with open(os.path.join(fdir, ".loop", ".iteration"), "w") as f:
            f.write("1\n")
        return feature

    def _snapshot(self):
        """Map every KB file path -> exact bytes, for a before/after diff."""
        tree = {}
        for root, _dirs, files in os.walk(self.kdir):
            for name in files:
                p = os.path.join(root, name)
                with open(p, "rb") as f:
                    tree[os.path.relpath(p, self.kdir)] = f.read()
        return tree

    def _get_json(self, path):
        import json
        req = urllib.request.Request(self.base + path)
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read().decode("utf-8"))

    def test_serving_every_endpoint_leaves_kb_byte_identical(self):
        before = self._snapshot()
        self.assertTrue(before, "synthetic KB tree should be non-empty")

        # `/` static route + every exact-match JSON endpoint.
        self._get("/")
        for route in sorted(SRV.API_ROUTES):
            self._get_json(route)

        # Every parameterized drill-down with REAL ids resolved from the KB.
        learnings = self._get_json("/api/kb/learnings")["entries"]
        self.assertTrue(learnings, "synthetic KB should expose learnings")
        eid = learnings[0]["id"]
        tags = learnings[0].get("tags") or []
        self._get_json("/api/kb/learnings/%s" % eid)
        if tags:
            self._get_json("/api/kb/tags/%s" % tags[0])
        self._get_json("/api/tasks/260101-ro-feature")
        self._get_json("/api/trace/260101-ro-feature")

        after = self._snapshot()
        self.assertEqual(before, after,
                         "serving the dashboard mutated the KB tree "
                         "(read-only invariant violated)")

    def _get(self, path):
        req = urllib.request.Request(self.base + path)
        return urllib.request.urlopen(req, timeout=10)


DIST_DIR = os.path.join(REPO_ROOT, "webui", "dist")


def _index_resource_refs(html):
    """Every `src=`/`href=` resource reference in an HTML document."""
    import re
    refs = []
    for m in re.finditer(r"""\b(?:src|href)\s*=\s*["']([^"']+)["']""", html):
        refs.append(m.group(1))
    return refs


def _is_external_origin(ref):
    """A reference is EXTERNAL if it loads from another origin.

    Same-origin (allowed): relative (`./x`, `x`), root-absolute (`/x`), in-page
    anchors (`#x`), and `data:` URIs (inlined, no network). External (rejected):
    an explicit scheme (`http://`, `https://`) or a protocol-relative `//host`.
    """
    r = ref.strip().lower()
    if r.startswith("//"):
        return True
    import re
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://", r))


class BuiltBundleIntegrationTest(unittest.TestCase):
    """Task 23 — the REAL committed `webui/dist/` is served self-hosted with NO
    external origin (no CDN). A fresh clone must run the dashboard WITHOUT node,
    so `webui/dist/` is committed; this serves THAT bundle (not a synthetic one)
    over the sidecar and proves same-origin-only resource loading."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(os.path.join(DIST_DIR, "index.html")):
            raise unittest.SkipTest(
                "webui/dist/index.html missing — run `cd webui && npm run build`")

    def setUp(self):
        self.httpd = SRV.make_server(host="127.0.0.1", port=0,
                                     dist_dir=DIST_DIR, kdir=None)
        self.addCleanup(self.httpd.server_close)
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)
        host, port = self.httpd.server_address[0], self.httpd.server_address[1]
        self.base = "http://%s:%s" % (host, port)

    def _get(self, path):
        return urllib.request.urlopen(
            urllib.request.Request(self.base + path), timeout=10)

    def test_serves_react_bundle_200_html(self):
        # Task 23 verify: the dashboard serves the React bundle at 127.0.0.1
        # returning 200 text/html.
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")
        resp = self._get("/")
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))
        body = resp.read().decode("utf-8")
        self.assertIn('<div id="root">', body)
        # The built bundle wires its own hashed entry assets in <head>.
        self.assertIn("/assets/", body)

    def test_built_assets_are_self_hosted(self):
        # The hashed JS/CSS entry assets the index references are served 200
        # from the SAME origin (no CDN), with the right content type.
        html = self._get("/").read().decode("utf-8")
        refs = [r for r in _index_resource_refs(html)
                if "/assets/" in r and (r.endswith(".js") or r.endswith(".css"))]
        self.assertTrue(refs, "index.html should reference built /assets/ files")
        for ref in refs:
            path = ref.lstrip(".")  # './assets/x.js' -> '/assets/x.js'
            resp = self._get(path)
            self.assertEqual(resp.status, 200, "asset not served: %s" % ref)

    def test_index_references_no_external_origin(self):
        # Task 23 verify: the built assets reference NO external http(s):// origin
        # (clone-and-go, same-origin only). Inspect every resource-loading
        # reference (`src=`/`href=`) in the entry document — these are what the
        # browser actually FETCHES (vendor doc-URLs / SVG namespaces buried in JS
        # string literals are not network loads and are out of scope).
        html = self._get("/").read().decode("utf-8")
        external = [r for r in _index_resource_refs(html) if _is_external_origin(r)]
        self.assertEqual(external, [],
                         "index.html loads external origin(s): %s" % external)

    def test_built_css_has_no_external_url(self):
        # No `url(http(s)://...)` / protocol-relative `url(//...)` in the built
        # CSS — fonts/images are inlined or self-hosted (no CDN font/image pulls).
        import re
        css_dir = os.path.join(DIST_DIR, "assets")
        css_files = [f for f in os.listdir(css_dir) if f.endswith(".css")] \
            if os.path.isdir(css_dir) else []
        self.assertTrue(css_files, "built bundle should emit a CSS asset")
        pat = re.compile(r"""url\(\s*['"]?\s*(?://|[a-z][a-z0-9+.-]*://)""",
                         re.IGNORECASE)
        for name in css_files:
            with open(os.path.join(css_dir, name), "r", encoding="utf-8") as f:
                css = f.read()
            hits = pat.findall(css)
            self.assertEqual(hits, [],
                             "%s references an external url(): %s" % (name, hits))


class DashboardCommandRegisteredTest(unittest.TestCase):
    """The `dashboard` subcommand is wired into the CLI parser."""

    def test_subcommand_present(self):
        cli = load_cli()
        parser = cli.build_parser()
        # Parse just the subcommand with --no-open; func must be cmd_dashboard.
        args = parser.parse_args(["dashboard", "--no-open", "--port", "0"])
        self.assertEqual(args.func, cli.cmd_dashboard)
        self.assertEqual(args.port, 0)
        self.assertTrue(args.no_open)


if __name__ == "__main__":
    unittest.main()
