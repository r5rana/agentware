"""agentware_dashboard.readgraph — the fan-out GRAPH read model.

Owns: `/api/graph/<name>` — a read-only mirror of `graph status --format json`,
returning the SAME derived payload with NO new collection and NO writer, plus
the name check that has to happen before the manifest path is built.
Does NOT own: the manifest format or its status derivation
(`agentware_lib.features.graphdag.*`), or the clock (`core.dates`).

SECURITY: the route regex matches the RAW percent-encoded segment, so a
`..%2F..%2F..` traversal arrives here as `../../..` after unquote, and the
manifest path builder does no confinement of its own. A graph name is a strict
slug, so anything else can only be a traversal probe and is refused with a 404
BEFORE any filesystem path is built from it.
"""

import urllib.parse
from http import HTTPStatus

from agentware_lib.core import dates
from agentware_lib.features import fanout
from agentware_lib.features.graphdag import graphledger, graphmanifest

from agentware_dashboard.reqparams import NO_KDIR, kdir_of, qp


def api_graph(request, name):
    """`/api/graph/<name>` — the exact object `graph status <name> --format json`
    prints. An optional `?now=<iso>` pins the clock for a byte-identical compare.
    """
    kdir = kdir_of(request)
    name = urllib.parse.unquote(name)
    if not fanout.FANOUT_FEATURE_RE.match(name or ""):
        return HTTPStatus.NOT_FOUND, {"error": "no such graph", "graph": name}
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, graph=name)
    manifest, err = graphmanifest._graph_load_manifest(kdir, name)
    if err:
        return HTTPStatus.NOT_FOUND, {"error": err, "graph": name}
    now = qp(request, "now") or dates.utc_now_iso()
    return HTTPStatus.OK, graphledger._graph_status_payload(
        kdir, name, manifest, now)
