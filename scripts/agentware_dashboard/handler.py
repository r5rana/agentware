"""agentware_dashboard.handler — HTTP mechanics, and no derivation.

Owns: the request handler — response headers (including the security headers
every response carries), the GET/HEAD entry points, the client-disconnect
guard, API dispatch through the route table, static serving with the SPA
fallback, and the silent access log.
Does NOT own: path confinement or the MIME allowlist (`confine`), the route
table (`routes`), or any payload (`read*`). Nothing in this file knows what a
knowledge base is.

READ-ONLY: no method here writes anything but a socket.
"""

import json
import os
import posixpath
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler

from agentware_dashboard import confine, routes


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
        """Read-only JSON API: a liveness probe plus the route table.

        A handler that raises is reported as a 500 carrying only its message —
        never a stack trace, which would disclose paths and internals to any
        page that can reach the loopback port.
        """
        route = path.rstrip("/") or "/api"
        if route in ("/api", "/api/ping"):
            return self._send_json(HTTPStatus.OK, {
                "status": "ok",
                "service": "agentware-dashboard",
            })
        handler, params = routes.resolve(route)
        if handler is None:
            return self._not_found()
        try:
            status, payload = handler(self, **params)
        except Exception as exc:  # never leak a stack trace to the client
            return self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                                   {"error": "internal error",
                                    "detail": str(exc)})
        return self._send_json(status, payload)

    def _serve_static(self):
        abspath = confine.safe_static_path(self.dist_root, self.path)
        if abspath is None or not os.path.isfile(abspath):
            # SPA fallback: an extensionless client route serves index.html so
            # React Router deep links work, WITHOUT weakening confinement.
            route = self.path.split("?", 1)[0].split("#", 1)[0]
            if abspath is None and "." not in posixpath.basename(route):
                index = confine.safe_static_path(self.dist_root, "/index.html")
                if index and os.path.isfile(index):
                    return self._serve_file(index)
            return self._not_found()
        return self._serve_file(abspath)

    def _serve_file(self, abspath):
        try:
            with open(abspath, "rb") as f:
                body = f.read()
        except OSError:
            return self._not_found()
        return self._send_bytes(HTTPStatus.OK, confine.mime_for(abspath), body)

    # Quiet logging (no stderr spam during tests/normal runs).
    def log_message(self, *args):
        return
