#!/usr/bin/env python3
"""agentware dashboard ENTRYPOINT — argv in, a bound server out.

Owns: argument parsing, the knowledge-dir default, the bind attempt and its two
refusal paths (a non-loopback host, an unavailable port), the startup banner and
the browser open, and the serve/shutdown lifecycle.
Does NOT own: anything else. Every rule this file enforces is decided
elsewhere — the loopback assertion by `confine`, construction by `httpserver`,
resolution of the knowledge dir by `toolkit`.

Runs in every launch mode: `python3 -m agentware_dashboard`, `python3
scripts/agentware_dashboard/`, or the path this file sits at. The sys.path
bootstrap below is what makes the last two work, and it is idempotent.
"""

import argparse
import os
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_HERE)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from agentware_dashboard import toolkit                     # noqa: E402
from agentware_dashboard.httpserver import make_server      # noqa: E402


def build_parser():
    """The sidecar's argv contract. `scripts/agentware dashboard` fills it in."""
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
                        help="resolved knowledge directory (for the JSON API); "
                             "resolved from the workspace config when omitted")
    parser.add_argument("--no-open", dest="no_open", action="store_true",
                        help="do not open a browser window")
    return parser


def _open_browser(url):
    """Open the dashboard without importing a network module at module scope."""
    try:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    except Exception:
        pass


def main(argv=None):
    args = build_parser().parse_args(argv)
    # A directly-launched sidecar resolves its own knowledge dir rather than
    # serving an empty dashboard: the CLI passes --kdir, nothing else has to.
    kdir = args.kdir or toolkit.resolve_knowledge_dir()

    try:
        httpd = make_server(host=args.host, port=args.port,
                            dist_dir=args.dist, kdir=kdir)
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
        _open_browser(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
