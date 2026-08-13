"""agentware_dashboard.confine — the sidecar's SECURITY KERNEL.

Owns: every rule that decides what the read-only server may bind to and which
bytes on disk it may disclose — the loopback-bind assertion, the static-file
confinement under `webui/dist/`, the served MIME allowlist, and the
confinement of a knowledge-dir `work/<feature>/` read.
Does NOT own: HTTP mechanics (`handler`), the socket server (`httpserver`), or
any payload (`read*`). Nothing here reads a request object or writes a byte.

WHY ITS OWN FILE: these four rules are the whole security surface of a server
that runs on the operator's machine with the knowledge base in reach. Spread
through a 1,146-line file they were four fragments a reviewer had to find; here
they are one page with one unit file (`tests/unit/dashboard/test_confine.py`),
so "what can this server disclose?" is answered by reading ONE module.

Pure and stdlib-only: every function is a total function of its arguments and
the filesystem, so the kernel is testable without binding a port.
"""

import os
import posixpath
import urllib.parse

#: Only loopback binds are permitted (the dashboard is a localhost-only tool).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

#: Allowlisted static file extensions -> MIME type. Anything not here is 404'd,
#: so the static layer can NEVER serve, e.g., a stray `.py`/`.env`/`.pem`.
MIME_BY_EXT = {
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

#: The content type served for an allowlisted extension whose MIME is somehow
#: unknown. Unreachable while `safe_static_path` is the only gate (it rejects
#: any extension absent from the table), and kept as the explicit fallback so a
#: future caller cannot end up sending a guessed type.
DEFAULT_CONTENT_TYPE = "application/octet-stream"


def bind_host(host):
    """The address to bind for `host`, or ValueError if it is not loopback.

    The assertion, not a preference: a dashboard reachable from the network
    would expose the operator's whole knowledge base read-only to anyone who can
    route to the port. `localhost` is accepted as a spelling and normalised to
    127.0.0.1 so the bind cannot land on a name that resolves elsewhere.
    """
    if host not in LOOPBACK_HOSTS:
        raise ValueError(
            "dashboard binds loopback only; refusing host %r (allowed: %s)"
            % (host, ", ".join(sorted(LOOPBACK_HOSTS))))
    return "127.0.0.1" if host == "localhost" else host


def mime_for(path):
    """The served content type for a path, by extension."""
    ext = os.path.splitext(path)[1].lower()
    return MIME_BY_EXT.get(ext, DEFAULT_CONTENT_TYPE)


def safe_static_path(dist_root, url_path):
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
    if ext not in MIME_BY_EXT:
        return None
    return candidate


def confined_work_file(kdir, feature, filename):
    """`<kdir>/work/<feature>/<filename>`, canonicalized and CONFINED, or None.

    The second confinement, and it guards a different root from the first: the
    static kernel confines under the built bundle, this one under the knowledge
    dir's `work/`, which is where a drill-down endpoint reads a per-feature
    artifact (the post-phase `assessment.md`). Same defence, same realpath
    comparison — a `..` escape, an absolute `feature`, or a symlink pointing out
    of `work/` all resolve outside the root and return None.

    The basename is re-checked after canonicalisation: without it a `feature`
    ending in a separator could still land the read on a DIFFERENT file inside
    `work/`, which is confined but not what the caller asked for.
    """
    work_root = os.path.realpath(os.path.join(kdir, "work"))
    target = os.path.realpath(os.path.join(work_root, feature, filename))
    try:
        confined = os.path.commonpath([work_root, target]) == work_root
    except ValueError:
        return None
    if not confined or os.path.basename(target) != filename:
        return None
    return target
