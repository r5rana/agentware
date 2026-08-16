"""agentware_lib.features.graphdag.graphcanon -- the manifest's CANONICAL FORM (layer: features).

Owns: `_graph_canonical` -- the one normalization of a manifest into a stable,
ordered structure. Nothing else.
Does NOT own: the manifest's paths, reads, writes or validation ->
graphdag.graphmanifest; the human renderings -> graphdag.graphrender.

WHY THIS IS ITS OWN MODULE
--------------------------
Two modules need the canonical form and neither can own it: `graphmanifest`
hashes and saves it, `graphrender` renders from it, and `graphmanifest._graph_save`
calls the renderer -- so homing the function in either one closes an import cycle.
It is extracted here rather than duplicated because two canonicalizations that
drift produce a manifest whose saved hash does not match the rendered graph, and
the checkpoint guarantee ("the graph I approved is the graph that ran") is exactly
a comparison of those two things.

WHY CANONICALIZATION MATTERS AT ALL
-----------------------------------
A manifest whose byte form depends on dict insertion order cannot be diffed
across a resume, cannot be hashed stably, and re-serializes differently on every
no-op rewrite -- so the committed artifact churns and the approval hash changes
without the graph changing. Sorting is not tidiness here; it is what makes the
hash mean something.

Layer deps: none beyond stdlib. The deepest leaf of the package alongside
`graphconst`.
Determinism: pure -- same manifest in, same structure out, every run (INV-1).
"""

import copy


def _graph_canonical(manifest):
    """Return the manifest with nodes sorted by feature and edges sorted by
    (from,to,kind) so the on-disk bytes are stable regardless of insertion order
    — the precondition for the Task-7 sha256 content-pin. Pure (returns a copy)."""
    nodes = sorted(
        (n for n in (manifest.get("nodes") or []) if isinstance(n, dict)),
        key=lambda n: n.get("feature") or "")
    edges = sorted(
        (e for e in (manifest.get("edges") or []) if isinstance(e, dict)),
        key=lambda e: (e.get("from") or "", e.get("to") or "",
                       e.get("kind") or ""))
    out = dict(manifest)
    out["nodes"] = nodes
    out["edges"] = edges
    return out


_EXPORTS = [
    "_graph_canonical",
]

__all__ = list(_EXPORTS)
