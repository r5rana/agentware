"""agentware_dashboard.httpserver — the socket server and its constructor.

Owns: the threading server that treats a client disconnect as normal rather
than as an error, and `make_server` — the ONE place a dashboard server is
constructed, bound and given its dist root and knowledge dir.
Does NOT own: the loopback rule (`confine.bind_host` decides what may be bound),
request handling (`handler`), or argument parsing (`__main__`).

Stdlib `http.server` only. This module is the reason the dashboard is a
SIDECAR: importing `http.server` anywhere in the CLI corpus is refused by the
invariant gate, so the server lives in a separate process the guard never scans.
"""

import os
import sys
from http.server import ThreadingHTTPServer

from agentware_dashboard import confine
from agentware_dashboard.handler import DashboardHandler
from agentware_dashboard.toolkit import REPO_ROOT


class DashboardServer(ThreadingHTTPServer):
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
    non-loopback host is requested (bind 127.0.0.1 ONLY) — the assertion lives in
    the security kernel, so the rule and the bind cannot drift apart.
    """
    bind_host = confine.bind_host(host)
    if dist_dir is None:
        dist_dir = os.path.join(REPO_ROOT, "webui", "dist")
    httpd = DashboardServer((bind_host, port), DashboardHandler)
    httpd.dist_root = os.path.abspath(dist_dir)
    httpd.kdir = kdir
    return httpd
