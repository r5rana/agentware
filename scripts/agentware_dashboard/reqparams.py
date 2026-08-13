"""agentware_dashboard.reqparams — reading a request WITHOUT touching HTTP.

Owns: the four things every read-model module asks of a request — the resolved
knowledge dir, a query parameter, a non-negative integer query parameter, and
the payload served when no knowledge dir is configured.
Does NOT own: parsing (the handler parses the query string once, before
dispatch), routing (`routes`), or any payload of its own (`read*`).

WHY A MODULE: every handler is a pure builder `(request, **path_params) ->
(HTTPStatus, payload)`, and the request object it receives is duck-typed — the
live one is a `BaseHTTPRequestHandler`, and a test passes a
`types.SimpleNamespace`. Reading it through named accessors is what keeps that
contract honest: a handler never reaches for an attribute the namespace does not
have, so a unit test of a payload needs no socket.

Stdlib only, read-only, total functions.
"""

#: The payload every endpoint returns when the workspace has no knowledge dir.
#: Merged into an endpoint's own empty shape (`dict(NO_KDIR, features=[])`) so a
#: panel gets its expected keys and `available: false` in the same response.
NO_KDIR = {"error": "knowledge dir not configured", "available": False}


def kdir_of(request):
    """The resolved knowledge dir for this server, or None when unconfigured."""
    return getattr(request, "kdir", None)


def qp(request, name, default=None):
    """First value of query param `name` (or `default`). Empty/absent -> default."""
    vals = getattr(request, "query_params", {}).get(name)
    if not vals:
        return default
    return vals[0]


def qp_int(request, name, default=None):
    """Query param `name` as a non-negative int, or `default` when absent/invalid."""
    raw = qp(request, name)
    if raw is None:
        return default
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return n if n >= 0 else default
