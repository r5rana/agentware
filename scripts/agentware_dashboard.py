#!/usr/bin/env python3
"""agentware dashboard server (sidecar).

Stdlib-only HTTP server for the read-only observability dashboard.

WHY A SIDECAR: the toolkit's moat is enforced by `tests/test_invariants.py`,
whose import guards (`test_cli_imports_are_stdlib_only` /
`test_cli_imports_no_network_modules`) `ast.walk` the CLI source and FORBID any
network-capable module (`http`, `socket`, `ssl`, `urllib`, ...) — even nested or
lazy imports. They scan ONLY `scripts/agentware` (CLI_PATH). This server, which
needs `http.server`, therefore lives in a SEPARATE file the guard never scans,
and `scripts/agentware dashboard` launches it via `subprocess` (the same
established pattern as `scripts/agentware_embedder_ollama.py`). The toolkit's
static-import surface stays stdlib-only.

SECURITY (operator is security-first):
  * Binds 127.0.0.1 ONLY — a non-loopback host is rejected at startup.
  * The static handler confines every path under the canonical (realpath'd)
    `webui/dist/` root: `..` traversal and symlink escapes are rejected, only an
    allowlisted set of file extensions/MIME types is served, directory listing
    is disabled, and `X-Content-Type-Options: nosniff` (+ a few safe headers)
    is sent on every response. Nothing outside `dist/` is ever served.

READ-ONLY: this server adds NO writer. The JSON API (filled in Task 9) delegates
to the toolkit's existing read functions; the static layer only reads `dist/`.
"""

import argparse
import json
import os
import posixpath
import re
import sys
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.machinery import SourceFileLoader

# --- Paths -------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
CLI_PATH = os.path.join(HERE, "agentware")

# Only loopback binds are permitted (the dashboard is a localhost-only tool).
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

# Allowlisted static file extensions -> MIME type. Anything not here is 404'd,
# so the static layer can NEVER serve, e.g., a stray `.py`/`.env`/`.pem`.
_MIME_BY_EXT = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".txt": "text/plain; charset=utf-8",
    ".webmanifest": "application/manifest+json",
}

# --- Lazy CLI module load (for the JSON API; read-only) ----------------------
_CLI_MODULE = None


def get_cli():
    """Import `scripts/agentware` as a module (cached) for its read functions.

    The JSON API (Task 9) delegates to the toolkit's existing read-only
    derivations rather than re-collecting anything. Loaded lazily so the static
    layer works even if the CLI fails to import for some reason.
    """
    global _CLI_MODULE
    if _CLI_MODULE is None:
        loader = SourceFileLoader("agentware_cli", CLI_PATH)
        mod = loader.load_module()  # noqa: DUO (trusted local file)
        _CLI_MODULE = mod
    return _CLI_MODULE


def _safe_static_path(dist_root, url_path):
    """Resolve a URL path to a confined absolute file path under `dist_root`.

    Returns the absolute path ONLY if it stays inside the canonical dist root and
    its extension is allowlisted; otherwise returns None (caller 404s). Defends
    against `..` traversal and symlink escapes by comparing realpaths.
    """
    # Decode + strip query/fragment already handled by caller; normalize here.
    path = urllib.parse.unquote(url_path)
    path = path.split("?", 1)[0].split("#", 1)[0]
    # Collapse to a posix-normalized, leading-slash-stripped relative path.
    norm = posixpath.normpath(path)
    if norm in ("", "/", "."):
        norm = "index.html"
    norm = norm.lstrip("/")
    # Reject any residual parent-traversal component outright.
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    root_real = os.path.realpath(dist_root)
    candidate = os.path.realpath(os.path.join(root_real, *parts))
    # Confinement: candidate must be the root itself or live strictly under it.
    if candidate != root_real and not candidate.startswith(root_real + os.sep):
        return None
    ext = os.path.splitext(candidate)[1].lower()
    if ext not in _MIME_BY_EXT:
        return None
    return candidate


class DashboardHandler(BaseHTTPRequestHandler):
    """Hardened read-only handler: static files from dist/ + JSON API."""

    server_version = "agentware-dashboard/1.0"
    protocol_version = "HTTP/1.1"

    # Bound by make_server via the server instance.
    @property
    def dist_root(self):
        return self.server.dist_root

    @property
    def kdir(self):
        return self.server.kdir

    # -- helpers --------------------------------------------------------------
    def _safe_headers(self, content_type, length):
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        # `frame-ancestors` is ENFORCEABLE only via an HTTP header (browsers
        # IGNORE it in a <meta> CSP and log a console error). Deliver it here as
        # the real header so the anti-clickjacking control actually applies, and
        # keep the inert directive OUT of the index.html meta.
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def _send_bytes(self, status, content_type, body):
        self.send_response(status)
        self._safe_headers(content_type, len(body))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body)

    def _not_found(self):
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found",
                                               "path": self.path})

    # -- routing --------------------------------------------------------------
    def do_GET(self):
        # A client (browser) that navigates away or cancels a slow request closes
        # the socket mid-response; the subsequent wfile.write then raises
        # BrokenPipeError/ConnectionResetError. That is EXPECTED, not a server
        # fault — swallow it with a one-line note instead of letting the stdlib
        # handler dump a multi-line traceback to the console. We never mask other
        # errors: only the disconnect family is caught here.
        try:
            raw = self.path.split("#", 1)[0]
            path, _, query = raw.partition("?")
            # Parse the query once so API handlers can honor optional params
            # (e.g. ?limit=, ?feature=) WITHOUT changing any default contract.
            self.query_params = urllib.parse.parse_qs(query) if query else {}
            if path == "/api" or path.startswith("/api/"):
                return self._handle_api(path)
            return self._serve_static()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self._note_client_disconnect()
            return None

    def _note_client_disconnect(self):
        """One-line, traceback-free note that a client hung up mid-response."""
        # Mark the connection un-reusable so the server loop stops cleanly.
        self.close_connection = True
        try:
            sys.stderr.write(
                "agentware dashboard: client disconnected mid-response "
                "(%s) — ignored\n" % self.path)
        except Exception:
            pass

    def do_HEAD(self):
        return self.do_GET()

    def _handle_api(self, path):
        """Read-only JSON API. Task 9 registers the data endpoints; Task 8 ships
        a server-liveness probe so the API surface is wired end-to-end."""
        route = path.rstrip("/") or "/api"
        if route in ("/api", "/api/ping"):
            return self._send_json(HTTPStatus.OK, {
                "status": "ok",
                "service": "agentware-dashboard",
            })
        # Dispatch: first the exact-match data endpoints (API_ROUTES), then the
        # parameterized drill-down routes (PARAM_ROUTES, e.g. /api/kb/learnings/<id>).
        handler = API_ROUTES.get(route)
        params = {}
        if handler is None:
            for pattern, h in PARAM_ROUTES:
                m = pattern.match(route)
                if m:
                    handler, params = h, m.groupdict()
                    break
        if handler is not None:
            try:
                status, payload = handler(self, **params)
            except Exception as exc:  # never leak a stack trace to the client
                return self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                                       {"error": "internal error",
                                        "detail": str(exc)})
            return self._send_json(status, payload)
        return self._not_found()

    def _serve_static(self):
        abspath = _safe_static_path(self.dist_root, self.path)
        if abspath is None or not os.path.isfile(abspath):
            # SPA fallback: an extensionless client route serves index.html so
            # React Router deep links work, WITHOUT weakening confinement.
            route = self.path.split("?", 1)[0].split("#", 1)[0]
            if abspath is None and "." not in posixpath.basename(route):
                index = _safe_static_path(self.dist_root, "/index.html")
                if index and os.path.isfile(index):
                    return self._serve_file(index)
            return self._not_found()
        return self._serve_file(abspath)

    def _serve_file(self, abspath):
        ext = os.path.splitext(abspath)[1].lower()
        ctype = _MIME_BY_EXT.get(ext, "application/octet-stream")
        try:
            with open(abspath, "rb") as f:
                body = f.read()
        except OSError:
            return self._not_found()
        return self._send_bytes(HTTPStatus.OK, ctype, body)

    # Quiet logging (no stderr spam during tests/normal runs).
    def log_message(self, *args):
        return


# =============================================================================
# Read-only JSON API (Task 9)
# =============================================================================
# Every endpoint DELEGATES to the toolkit's existing read-only derivations
# (loaded lazily via get_cli()); the server adds NO collection and NO writer.
# Handlers are pure builders taking (request, **path_params) and returning
# (HTTPStatus, payload_dict). The request carries `request.kdir` (the resolved
# knowledge directory) and `get_cli()` exposes the CLI module's functions.

_NO_KDIR = {"error": "knowledge dir not configured", "available": False}


def _kdir(request):
    """The resolved knowledge dir for this server, or None when unconfigured."""
    return getattr(request, "kdir", None)


def _qp(request, name, default=None):
    """First value of query param `name` (or `default`). Empty/absent -> default."""
    vals = getattr(request, "query_params", {}).get(name)
    if not vals:
        return default
    return vals[0]


def _qp_int(request, name, default=None):
    """Query param `name` as a non-negative int, or `default` when absent/invalid."""
    raw = _qp(request, name)
    if raw is None:
        return default
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return n if n >= 0 else default


def _linear_slope(points):
    """Ordinary-least-squares slope over (x, y) numeric points; None if < 2 or
    degenerate. Used by /api/scaling to summarize Recall@k vs corpus size."""
    pts = [(x, y) for (x, y) in points
           if isinstance(x, (int, float)) and not isinstance(x, bool)
           and isinstance(y, (int, float)) and not isinstance(y, bool)]
    n = len(pts)
    if n < 2:
        return None
    sx = sum(x for x, _ in pts)
    sy = sum(y for _, y in pts)
    sxx = sum(x * x for x, _ in pts)
    sxy = sum(x * y for x, y in pts)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    return (n * sxy - sx * sy) / denom


def _metrics_aggregate(cli, kdir, feature=None):
    """Mirror cmd_metrics' read-only aggregate build (cost, cache ratio, per-model
    /feature/day rollups, anomaly flag, authoring, context-tax, phase costs). When
    a feature is in scope, also attach per-iteration + terminal outcome. Returns
    (rows, agg)."""
    rows = cli.collect_sessions(kdir, feature=feature)
    agg = cli.aggregate_metrics(rows)
    cli.apply_pricing(rows, agg, cli._DEFAULT_PRICES)
    agg["authoring"] = cli.derive_authoring(rows)
    agg["context_tax"] = cli.derive_context_tax(rows, kdir)
    agg["phase_costs"] = cli.derive_phase_costs(rows)
    if feature:
        events = cli._read_metrics_jsonl(kdir, feature=feature)
        agg["iterations"] = cli.derive_iteration_costs(events)
        agg["outcome"] = cli.derive_outcome(kdir, feature, events=events)
    return rows, agg


def _list_work_features(kdir):
    """Sorted feature dirs under <kdir>/work that carry a plan.md or a .loop/ —
    the unit the loop operates on. Read-only directory listing."""
    work = os.path.join(kdir, "work")
    out = []
    try:
        for name in sorted(os.listdir(work)):
            fdir = os.path.join(work, name)
            if not os.path.isdir(fdir):
                continue
            if os.path.isfile(os.path.join(fdir, "plan.md")) or \
                    os.path.isdir(os.path.join(fdir, ".loop")):
                out.append(name)
    except OSError:
        return []
    return out


def _loop_feature_state(cli, kdir, feature):
    """Per-feature loop state: persisted .loop counters + plan marker burndown +
    terminal outcome + the tail of its metrics.jsonl emission. Read-only; works
    fully from PERSISTED data so the panel is idle-resilient."""
    fdir = os.path.join(kdir, "work", feature)
    loop_dir = os.path.join(fdir, ".loop")
    iteration = None
    try:
        with open(os.path.join(loop_dir, ".iteration"),
                  "r", encoding="utf-8", errors="replace") as f:
            iteration = int((f.read().strip() or "0"))
    except (OSError, ValueError):
        iteration = None
    done = os.path.isfile(os.path.join(loop_dir, ".done"))
    open_n, done_n = cli._plan_marker_status_counts(os.path.join(fdir, "plan.md"))
    events = cli._read_metrics_jsonl(kdir, feature=feature)
    outcome = cli.derive_outcome(kdir, feature, events=events)
    last_event = events[-1] if events else None
    last_ts = (last_event or {}).get("ts")
    return {
        "feature": feature,
        "iteration": iteration,
        "done": done,
        "tasks_open": open_n,
        "tasks_done": done_n,
        "tasks_total": open_n + done_n,
        "outcome": outcome,
        "event_count": len(events),
        "last_event": last_event,
        "last_ts": last_ts,
    }


def _entries(cli, kdir):
    """(data, err) from load_index; entries default to []. Read-only."""
    data, err = cli.load_index(kdir)
    if err is not None:
        return None, err
    return data, None


# --- /api/health -------------------------------------------------------------
def api_health(request):
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        return HTTPStatus.OK, _NO_KDIR
    data, err = cli.load_index(kdir)
    if err is not None:
        checks = [{"name": "index_validate", "ok": False, "details": [err]}]
    else:
        ve = cli.validate_index(kdir, data)
        un = cli.scan_unindexed(kdir, data)
        te = cli.tag_consistency_errors(data)
        tce = cli.template_conformance_errors(kdir, data)
        oe = cli.orphaned_learning_errors(kdir, data)
        checks = [
            {"name": "index_validate", "ok": not ve, "details": ve},
            {"name": "filesystem_sync", "ok": not un, "details": un},
            {"name": "tag_consistency", "ok": not te, "details": te},
            {"name": "template_conformance", "ok": not tce, "details": tce},
            {"name": "orphaned_learnings", "ok": not oe, "details": oe},
            cli._audit_steering_lint_check(),
            cli._audit_personal_data_check(kdir),
        ]
        # Dream-mode heartbeat (260627-dream-mode + 260628-dream-observability):
        # inert when dream is OFF, so it never false-alarms the default; when ON
        # the check dict carries last_run / age_hours / outcome (ok|fail|partial),
        # which flow through this JSON verbatim so the dream_health panel can
        # surface last-run age + outcome and warn on stale-or-failed. Tolerant of
        # an older CLI without the check.
        if hasattr(cli, "_audit_dream_health_check"):
            checks.append(cli._audit_dream_health_check(kdir))
    ok = all(c["ok"] for c in checks)
    return HTTPStatus.OK, {"ok": ok, "checks": checks}


# --- /api/quality ------------------------------------------------------------
def api_quality(request):
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, ledger=[], count=0, latest=None)
    rows = cli._read_ledger(os.path.join(kdir, cli.HISTORY_REL))
    series = [{
        "run": r.get("run"),
        "commit": r.get("commit"),
        "strategy": r.get("strategy"),
        "suite": r.get("suite"),
        "reliability": r.get("reliability"),
        "metrics": r.get("metrics", {}),
    } for r in rows]
    return HTTPStatus.OK, {
        "ledger": rows,
        "series": series,
        "count": len(rows),
        "latest": rows[-1] if rows else None,
    }


# --- /api/loop ---------------------------------------------------------------
def api_loop(request):
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, features=[], active=None,
                                   recent_events=[])
    features = [_loop_feature_state(cli, kdir, f)
                for f in _list_work_features(kdir)]
    # The active run = the feature with the most-recent emission that is not done.
    active = None
    best_ts = None
    for f in features:
        if f["done"] or not f["last_ts"]:
            continue
        if best_ts is None or str(f["last_ts"]) > str(best_ts):
            best_ts, active = f["last_ts"], f["feature"]
    # Optional, contract-preserving pagination of the event tail. With NO params
    # the response is byte-identical to before (most-recent 50, chronological).
    # ?feature= scopes the tail to one run; ?limit=N caps it (most-recent N, still
    # returned chronologically). The `features` list is always the full set.
    feature_q = _qp(request, "feature")
    limit_q = _qp_int(request, "limit")
    recent = cli._read_metrics_jsonl(kdir, feature=feature_q) if feature_q \
        else cli._read_metrics_jsonl(kdir)
    tail = limit_q if limit_q is not None else 50
    return HTTPStatus.OK, {
        "features": features,
        "active": active,
        "recent_events": recent[-tail:] if tail else [],
    }


# --- /api/agents (PLAN_AW + WORK_AW) -----------------------------------------
def api_agents(request):
    """Per-agent activity for the PLAN_AW / WORK_AW sidebar views: the active
    planner/worker (most-recent session + active flag) plus the full persisted
    history, cost/token aggregates and per-day/per-feature breakdowns. Polled
    like /api/loop so a freshly-started planner or worker shows up live."""
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        empty = {"kind": None, "active": False, "active_session": None,
                 "session_count": 0, "attributed_count": 0, "incomplete_count": 0,
                 "aggregate": {}, "by_day": {}, "by_feature": {}, "sessions": [],
                 "features": []}
        return HTTPStatus.OK, dict(
            _NO_KDIR, plan=dict(empty, kind="plan", plans=[]),
            work=dict(empty, kind="work"))
    return HTTPStatus.OK, cli.derive_agents(kdir)


# --- /api/loop-analytics -----------------------------------------------------
def api_loop_analytics(request):
    """First-class loop analytics (Task 28): per-feature per-phase wall/token
    split, tasks-remaining burndown, iteration efficiency, self-heal count,
    max-iteration utilization, pre/post-hook gate outcomes, promise latency, and
    loop throughput over time. Delegates to the CLI's read-only derivation."""
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, features=[], throughput={})
    return HTTPStatus.OK, cli.derive_loop_analytics(kdir)


# --- /api/loop-health --------------------------------------------------------
def api_loop_health(request):
    """Loop-health & runaway detection (Task 30): per-run duplicate-tool-call /
    no-progress / token-burn / context-window checks folded into an OK/at-risk/
    critical badge naming the offending tool/iteration. Delegates to the CLI's
    read-only derivation; surfaced on the Overview + Loops sections."""
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, features=[],
                                   summary={"ok": 0, "at_risk": 0,
                                            "critical": 0}, status="ok")
    return HTTPStatus.OK, cli.derive_loop_health(kdir)


# --- /api/loop-health/<feature> (per-run drill-down) -------------------------
def api_loop_health_feature(request, feature):
    cli = get_cli()
    kdir = _kdir(request)
    feature = urllib.parse.unquote(feature)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, feature=feature, status="ok",
                                   checks={})
    return HTTPStatus.OK, cli.derive_loop_health(kdir, feature=feature)


# --- /api/cost ---------------------------------------------------------------
def api_cost(request):
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, session_count=0, sessions=[],
                                   aggregate={})
    rows, agg = _metrics_aggregate(cli, kdir)
    return HTTPStatus.OK, {
        "session_count": len(rows),
        "sessions": rows,
        "aggregate": agg,
    }


# --- /api/authoring ----------------------------------------------------------
def api_authoring(request):
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, authoring={}, session_count=0)
    rows = cli.collect_sessions(kdir)
    return HTTPStatus.OK, {
        "authoring": cli.derive_authoring(rows),
        "session_count": len(rows),
    }


# --- /api/context-tax --------------------------------------------------------
def api_context_tax(request):
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, context_tax={}, session_count=0)
    rows = cli.collect_sessions(kdir)
    return HTTPStatus.OK, {
        "context_tax": cli.derive_context_tax(rows, kdir),
        "session_count": len(rows),
    }


# --- /api/scaling ------------------------------------------------------------
def api_scaling(request):
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, points=[], slope=None, count=0)
    rows = cli._read_ledger(os.path.join(kdir, cli.HISTORY_REL))
    points = []
    xy = []
    for r in rows:
        recall = (r.get("metrics") or {}).get("recall_at_k")
        size = r.get("corpus_size")  # added to row builders in Task 10
        points.append({
            "corpus_size": size,
            "recall_at_k": recall,
            "commit": r.get("commit"),
            "run": r.get("run"),
            "strategy": r.get("strategy"),
        })
        if isinstance(size, (int, float)) and not isinstance(size, bool) \
                and isinstance(recall, (int, float)):
            xy.append((size, recall))
    return HTTPStatus.OK, {
        "points": points,
        "slope": _linear_slope(xy),
        "count": len(points),
        "measured": len(xy),
    }


# --- /api/outcomes -----------------------------------------------------------
def api_outcomes(request):
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, features=[], summary={})
    out = []
    summary = {}
    for feature in _list_work_features(kdir):
        oc = cli.derive_outcome(kdir, feature)
        rec = dict(oc or {"outcome": "unknown"})
        rec["feature"] = feature
        out.append(rec)
        key = rec.get("outcome", "unknown")
        summary[key] = summary.get(key, 0) + 1
    return HTTPStatus.OK, {"features": out, "summary": summary}


# --- /api/kb (aggregate) -----------------------------------------------------
def api_kb(request):
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, entry_count=0, categories={})
    data, err = _entries(cli, kdir)
    if err is not None:
        return HTTPStatus.OK, {"error": err, "entry_count": 0, "categories": {}}
    entries = data.get("entries", [])
    cats = {}
    for e in entries:
        cats[e.get("category", "uncategorized")] = \
            cats.get(e.get("category", "uncategorized"), 0) + 1
    tags = data.get("tags", {})
    return HTTPStatus.OK, {
        "entry_count": len(entries),
        "categories": cats,
        "category_count": len(cats),
        "tag_count": len(tags),
        "entries": [{
            "id": e.get("id"), "title": e.get("title"),
            "category": e.get("category"), "path": e.get("path"),
            "summary": e.get("summary"), "tags": e.get("tags", []),
        } for e in entries],
    }


def _kb_category(request, category):
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, category=category, entries=[])
    data, err = _entries(cli, kdir)
    if err is not None:
        return HTTPStatus.OK, {"error": err, "category": category, "entries": []}
    rows = [e for e in data.get("entries", [])
            if e.get("category") == category]
    return HTTPStatus.OK, {"category": category, "count": len(rows),
                           "entries": rows}


# --- /api/kb/projects, /api/kb/learnings (category drill-downs) ---------------
def api_kb_projects(request):
    return _kb_category(request, "projects")


def api_kb_learnings(request):
    return _kb_category(request, "learnings")


# --- /api/kb/learnings/<id> (entry detail) -----------------------------------
def api_kb_learning_detail(request, entry_id):
    cli = get_cli()
    kdir = _kdir(request)
    entry_id = urllib.parse.unquote(entry_id)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, id=entry_id)
    data, err = _entries(cli, kdir)
    if err is not None:
        return HTTPStatus.OK, {"error": err, "id": entry_id}
    entry = next((e for e in data.get("entries", [])
                  if e.get("id") == entry_id), None)
    if entry is None:
        return HTTPStatus.NOT_FOUND, {"error": "learning not found",
                                      "id": entry_id}
    return HTTPStatus.OK, {"entry": entry,
                           "body": cli._read_entry_body(kdir, entry)}


# --- /api/kb/tags/<tag> ------------------------------------------------------
def api_kb_tag(request, tag):
    cli = get_cli()
    kdir = _kdir(request)
    tag = urllib.parse.unquote(tag)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, tag=tag, entries=[])
    data, err = _entries(cli, kdir)
    if err is not None:
        return HTTPStatus.OK, {"error": err, "tag": tag, "entries": []}
    rows = [e for e in data.get("entries", [])
            if tag in (e.get("tags") or [])]
    return HTTPStatus.OK, {"tag": tag, "count": len(rows), "entries": rows}


# --- /api/features -----------------------------------------------------------
def api_features(request):
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, categories={}, entry_count=0)
    data, err = _entries(cli, kdir)
    if err is not None:
        return HTTPStatus.OK, {"error": err, "categories": {}, "entry_count": 0}
    by_cat = {}
    for e in data.get("entries", []):
        by_cat.setdefault(e.get("category", "uncategorized"), []).append({
            "id": e.get("id"), "title": e.get("title"),
            "path": e.get("path"), "summary": e.get("summary"),
            "tags": e.get("tags", []),
        })
    return HTTPStatus.OK, {
        "categories": by_cat,
        "category_count": len(by_cat),
        "entry_count": sum(len(v) for v in by_cat.values()),
    }


# --- /api/tasks/<feature> ----------------------------------------------------
def api_tasks(request, feature):
    cli = get_cli()
    kdir = _kdir(request)
    feature = urllib.parse.unquote(feature)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, feature=feature, transitions=[])
    events = cli._read_metrics_jsonl(kdir, feature=feature)
    transitions = [e for e in events if e.get("event") == "task_transition"]
    open_n, done_n = cli._plan_marker_status_counts(
        os.path.join(kdir, "work", feature, "plan.md"))
    return HTTPStatus.OK, {
        "feature": feature,
        "transitions": transitions,
        "transition_count": len(transitions),
        "plan": {"open": open_n, "done": done_n, "total": open_n + done_n},
    }


# --- /api/trace/<session|feature> --------------------------------------------
def api_trace(request, target):
    """Step-level run trace (Task 29): an ordered tool-call timeline grouped by
    loop iteration, built from logs/sessions/<sid>/{live.jsonl,main.jsonl} + the
    metrics.jsonl emission. `target` is matched as a SESSION id first (a session
    dir exists), else treated as a FEATURE. Delegates to the read-only CLI."""
    cli = get_cli()
    kdir = _kdir(request)
    target = urllib.parse.unquote(target)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, target=target, iterations=[],
                                   step_count=0)
    root = cli.logs_sessions_dir(kdir)
    if os.path.isdir(os.path.join(root, target)):
        return HTTPStatus.OK, cli.derive_trace(kdir, session=target)
    return HTTPStatus.OK, cli.derive_trace(kdir, feature=target)


# --- /api/alerts -------------------------------------------------------------
def api_alerts(request):
    """Symptom-based, severity-ranked alerts (Task 31): reliability/nDCG
    regression, retrieval scaling-slope, cost spike, stuck-loop/runaway,
    stale/conflicting KB, and unpromoted LEARNED/DECISION at finish — each with a
    deep-link to its panel. Also returns the ledger `commit_markers` so the trend
    charts can place commit markers aligned to ledger SHAs. Delegates to the
    read-only CLI derivation; degrades to an empty list when sources are absent."""
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, alerts=[], open_count=0,
                                   summary={"critical": 0, "warning": 0,
                                            "info": 0},
                                   status="ok", commit_markers=[])
    return HTTPStatus.OK, cli.derive_alerts(kdir)


# --- /api/failures/<feature> -------------------------------------------------
def api_failures(request, feature):
    """Failure-ladder & error-recovery (Task 32): tool ERR rate (live.jsonl) +
    per-tool tallies, R-FAIL ladder tier usage (kb->reasoning->inputs->switch->
    web), web-search escalations, KB-lookup count, self-heal re-engagements, and
    the DECISION/LEARNED marker tallies for ONE feature. Delegates to the
    read-only CLI derivation; degrades to a zeroed tally when sources are absent."""
    cli = get_cli()
    kdir = _kdir(request)
    feature = urllib.parse.unquote(feature)
    if not kdir:
        return HTTPStatus.OK, dict(
            _NO_KDIR, feature=feature, scope="feature", step_count=0,
            err_count=0, err_rate=0.0, err_by_tool={},
            ladder={t: 0 for t in cli._FAIL_LADDER_TIERS},
            ladder_order=list(cli._FAIL_LADDER_TIERS), unrecovered=0,
            web_search_count=0, kb_lookup_count=0, self_heal_count=0,
            markers={"learned": {"total": 0, "unpromoted": 0},
                     "decision": {"total": 0, "unpromoted": 0}},
            tool_summary={}, sessions=[])
    return HTTPStatus.OK, cli.derive_failures(kdir, feature)


# --- /api/evals --------------------------------------------------------------
def api_evals(request):
    """Evaluation & quality (Task 33): the eval/gate ledger trend — Recall@k /
    nDCG / MRR / reliability per run — split from the ACR-gate decisions, both
    read READ-ONLY from benchmarks/history.jsonl (the sole eval writer is the
    CLI's `eval --record`). Part of the Memory + Health pillars; pairs with
    /api/assessments/<feature> for the post-phase self-assessment text."""
    cli = get_cli()
    kdir = _kdir(request)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, series=[], acr=[], count=0,
                                   acr_count=0, latest=None, latest_acr=None)
    rows = cli._read_ledger(os.path.join(kdir, cli.HISTORY_REL))
    series = []
    acr = []
    for r in rows:
        mode = r.get("mode")
        m = r.get("metrics") or {}
        if mode == "acr-gate":
            gate = r.get("acr_gate") or {}
            acr.append({
                "run": r.get("run"),
                "commit": r.get("commit"),
                "decided_strategy": gate.get("decided_strategy"),
                "passed": gate.get("passed"),
                "checks": gate.get("checks", {}),
            })
            continue
        series.append({
            "run": r.get("run"),
            "commit": r.get("commit"),
            "strategy": r.get("strategy"),
            "suite": r.get("suite"),
            "mode": mode,
            "reliability": r.get("reliability"),
            "recall_at_k": m.get("recall_at_k"),
            "ndcg_at_k": m.get("ndcg_at_k"),
            "mrr": m.get("mrr"),
            "precision_at_k": m.get("precision_at_k"),
            "corpus_size": r.get("corpus_size"),
        })
    return HTTPStatus.OK, {
        "series": series,
        "acr": acr,
        "count": len(series),
        "acr_count": len(acr),
        "latest": series[-1] if series else None,
        "latest_acr": acr[-1] if acr else None,
    }


# --- /api/assessments/<feature> ----------------------------------------------
def api_assessments(request, feature):
    """Post-phase self-ASSESSMENT for one feature (Task 33): the raw
    work/<feature>/assessment.md text the loop's post phase writes, so quality
    drift is visible alongside the eval trend. READ-ONLY; the feature path is
    canonicalized and CONFINED under <kdir>/work (reject `..`/symlink escapes,
    security-first) before any read. Missing assessment → exists:false, no error."""
    cli = get_cli()  # noqa: F841 (parity with sibling handlers; kdir comes from request)
    kdir = _kdir(request)
    feature = urllib.parse.unquote(feature)
    if not kdir:
        return HTTPStatus.OK, dict(_NO_KDIR, feature=feature, exists=False,
                                   text="", bytes=0, path=None)
    work_root = os.path.realpath(os.path.join(kdir, "work"))
    target = os.path.realpath(os.path.join(work_root, feature, "assessment.md"))
    base = os.path.basename(target)
    try:
        confined = os.path.commonpath([work_root, target]) == work_root
    except ValueError:
        confined = False
    if not confined or base != "assessment.md":
        return HTTPStatus.OK, {"feature": feature, "exists": False, "text": "",
                               "bytes": 0, "path": None,
                               "error": "invalid feature"}
    text = ""
    exists = os.path.isfile(target)
    if exists:
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            exists, text = False, ""
    return HTTPStatus.OK, {
        "feature": feature,
        "exists": exists,
        "text": text,
        "bytes": len(text.encode("utf-8")),
        "path": os.path.relpath(target, kdir) if exists else None,
    }


# Exact-match data endpoints: "/api/<name>" -> handler(request) -> (status, obj).
API_ROUTES = {
    "/api/health": api_health,
    "/api/quality": api_quality,
    "/api/loop": api_loop,
    "/api/agents": api_agents,
    "/api/loop-analytics": api_loop_analytics,
    "/api/loop-health": api_loop_health,
    "/api/cost": api_cost,
    "/api/authoring": api_authoring,
    "/api/context-tax": api_context_tax,
    "/api/scaling": api_scaling,
    "/api/outcomes": api_outcomes,
    "/api/evals": api_evals,
    "/api/alerts": api_alerts,
    "/api/kb": api_kb,
    "/api/kb/projects": api_kb_projects,
    "/api/kb/learnings": api_kb_learnings,
    "/api/features": api_features,
}

# Parameterized drill-down routes: (compiled pattern, handler(request, **groups)).
# A path segment captures everything-but-slash so ids/tags/features with hyphens
# resolve; the more specific /api/kb/learnings/<id> is matched before the broader
# tag route because it lives earlier in this ordered list.
PARAM_ROUTES = [
    (re.compile(r"^/api/kb/learnings/(?P<entry_id>[^/]+)$"),
     api_kb_learning_detail),
    (re.compile(r"^/api/kb/tags/(?P<tag>[^/]+)$"), api_kb_tag),
    (re.compile(r"^/api/tasks/(?P<feature>[^/]+)$"), api_tasks),
    (re.compile(r"^/api/trace/(?P<target>[^/]+)$"), api_trace),
    (re.compile(r"^/api/loop-health/(?P<feature>[^/]+)$"), api_loop_health_feature),
    (re.compile(r"^/api/failures/(?P<feature>[^/]+)$"), api_failures),
    (re.compile(r"^/api/assessments/(?P<feature>[^/]+)$"), api_assessments),
]


class _DashboardServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that treats client disconnects as NORMAL, not errors.

    A browser that navigates away, refreshes, or cancels a request resets the
    socket. That surfaces as a connection-family exception in EITHER half of the
    exchange — `ConnectionResetError` while the server reads the request line
    (`handle_one_request` → `rfile.readline`), or `BrokenPipeError` while it
    writes the response (`wfile.write`). The stdlib base server's `handle_error`
    would dump a full multi-line traceback PER disconnect (the noise the operator
    saw). We override it to swallow the whole disconnect family with a single
    quiet line; only genuine errors keep their traceback. This is the
    server-level backstop complementing the handler-level guard in `do_GET`."""

    daemon_threads = True
    # Don't let one slow/dead client block shutdown.
    block_on_close = False

    _DISCONNECT = (BrokenPipeError, ConnectionResetError,
                   ConnectionAbortedError, TimeoutError)

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, self._DISCONNECT):
            return  # expected client disconnect — not a server fault
        return super().handle_error(request, client_address)


def make_server(host="127.0.0.1", port=0, dist_dir=None, kdir=None):
    """Construct a hardened, disconnect-tolerant server bound to a loopback host.

    `port=0` binds an ephemeral port (used by tests). Raises ValueError if a
    non-loopback host is requested (bind 127.0.0.1 ONLY).
    """
    if host not in _LOOPBACK_HOSTS:
        raise ValueError(
            "dashboard binds loopback only; refusing host %r (allowed: %s)"
            % (host, ", ".join(sorted(_LOOPBACK_HOSTS))))
    bind_host = "127.0.0.1" if host == "localhost" else host
    if dist_dir is None:
        dist_dir = os.path.join(REPO_ROOT, "webui", "dist")
    httpd = _DashboardServer((bind_host, port), DashboardHandler)
    httpd.dist_root = os.path.abspath(dist_dir)
    httpd.kdir = kdir
    return httpd


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="agentware-dashboard",
        description="Read-only agentware observability dashboard server "
                    "(localhost-only).")
    parser.add_argument("--host", default="127.0.0.1",
                        help="loopback host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765,
                        help="port to bind (default: 8765)")
    parser.add_argument("--dist", default=None,
                        help="path to the built static bundle (webui/dist/)")
    parser.add_argument("--kdir", default=None,
                        help="resolved knowledge directory (for the JSON API)")
    parser.add_argument("--no-open", dest="no_open", action="store_true",
                        help="do not open a browser window")
    args = parser.parse_args(argv)

    try:
        httpd = make_server(host=args.host, port=args.port,
                            dist_dir=args.dist, kdir=args.kdir)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("error: cannot bind %s:%s (%s)" % (args.host, args.port, exc),
              file=sys.stderr)
        return 1

    host, port = httpd.server_address[0], httpd.server_address[1]
    url = "http://%s:%s/" % (host, port)
    dist = httpd.dist_root
    if not os.path.isdir(dist):
        print("note: static bundle not built yet (%s); JSON API still served. "
              "Run `cd webui && npm run build` to populate it." % dist,
              file=sys.stderr)
    print("agentware dashboard serving %s (dist: %s)" % (url, dist))

    if not args.no_open:
        # Open the browser without importing a network module at module scope.
        try:
            import webbrowser
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
