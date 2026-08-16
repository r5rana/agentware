"""agentware_lib.features.graphdag.graphrender -- the DAG's three human RENDERINGS (layer: features).

Owns: the markdown view written beside the manifest (`_render_graph_md`), the
Graphviz DOT export (`_render_graph_dot`) and the Mermaid export
(`_render_graph_mermaid`).
Does NOT own: the manifest itself -> graphdag.graphmanifest; topological order ->
graphdag.graphtopo; node state -> graphdag.graphstate; the `graph show` verb ->
graphdag.graphedit.

WHY A DAG NEEDS A RENDERED VIEW AT ALL
--------------------------------------
The manifest is JSON, and a JSON DAG with twenty nodes and forty edges is not
something a human can hold in their head well enough to approve. This scheduler
spawns real agent processes against a real repository, and the operator is asked
to approve the shape BEFORE that happens. `_render_graph_md` is written beside
the manifest on every save specifically so the reviewable artifact is never
staler than the thing it describes -- a diagram regenerated on demand is a
diagram that shows the graph you asked about, not the graph that will run.

Three formats because they have different readers: markdown for the artifact
committed next to the plan, DOT for anyone with Graphviz, Mermaid for anything
that renders it inline (a PR description, a docs page) without a toolchain.

Layer deps: features -> features (`graphconst` for the node states it colours by,
`graphcanon._graph_canonical` for the stable ordering it renders from).
Pure: every renderer is a function from a manifest dict to a string. No file
reads, no writes, no clock -- the caller owns the write, so a render can be
compared, tested and diffed without touching disk.
Determinism: nodes and edges are emitted in a stable order, so re-rendering an
unchanged manifest produces byte-identical output and the committed artifact does
not churn (INV-1).
"""

from agentware_lib.features.graphdag.graphcanon import _graph_canonical
from agentware_lib.features.graphdag.graphconst import (
    GRAPH_NODE_BLOCKED,
    GRAPH_NODE_DONE,
    GRAPH_NODE_FAILED,
    GRAPH_NODE_HALT,
    GRAPH_NODE_PENDING,
    GRAPH_NODE_RUNNING,
)


def _render_graph_md(manifest):
    """Derived, human-readable `graph.md` view (regenerated on every mutation —
    NEVER hand-edited; the manifest is canonical). Deterministic ordering."""
    m = _graph_canonical(manifest)
    lines = ["# Graph — %s" % m.get("name", "?"), ""]
    lines.append("> Derived view — regenerated from `graph.json` on every "
                 "mutation. Do NOT edit by hand; the manifest is canonical.")
    lines.append("")
    lines.append("- **Concurrency:** %s" % m.get("concurrency"))
    lines.append("- **Nodes:** %d" % len(m.get("nodes") or []))
    lines.append("- **Edges:** %d" % len(m.get("edges") or []))
    lines.append("")
    lines.append("## Nodes")
    lines.append("")
    if m.get("nodes"):
        for n in m["nodes"]:
            tags = []
            if n.get("self_extension"):
                tags.append("self-extension")
            cp = n.get("checkpoint", "none")
            if cp != "none":
                tags.append("checkpoint=%s" % cp)
            suffix = (" — %s" % ", ".join(tags)) if tags else ""
            lines.append("- `%s`%s" % (n.get("feature"), suffix))
    else:
        lines.append("_(none)_")
    lines.append("")
    lines.append("## Edges")
    lines.append("")
    if m.get("edges"):
        for e in m["edges"]:
            lines.append("- `%s` -> `%s` (%s)"
                         % (e.get("from"), e.get("to"),
                            e.get("kind", "logical")))
    else:
        lines.append("_(none)_")
    lines.append("")
    return "\n".join(lines)


def _render_graph_dot(manifest):
    """Graphviz DOT rendering (read-only, deterministic)."""
    m = _graph_canonical(manifest)
    out = ['digraph "%s" {' % m.get("name", "graph")]
    for n in (m.get("nodes") or []):
        shape = "box" if n.get("self_extension") else "ellipse"
        out.append('  "%s" [shape=%s];' % (n.get("feature"), shape))
    for e in (m.get("edges") or []):
        style = "dashed" if e.get("kind") == "resource" else "solid"
        out.append('  "%s" -> "%s" [style=%s];'
                   % (e.get("from"), e.get("to"), style))
    out.append("}")
    return "\n".join(out)


def _render_graph_mermaid(manifest):
    """Mermaid `graph LR` rendering (read-only, deterministic)."""
    m = _graph_canonical(manifest)
    out = ["graph LR"]
    for n in (m.get("nodes") or []):
        out.append("  %s[%s]" % (n.get("feature"), n.get("feature")))
    for e in (m.get("edges") or []):
        arrow = "-.->" if e.get("kind") == "resource" else "-->"
        out.append("  %s %s %s" % (e.get("from"), arrow, e.get("to")))
    return "\n".join(out)


_EXPORTS = [
    "_render_graph_md",
    "_render_graph_dot",
    "_render_graph_mermaid",
]

__all__ = list(_EXPORTS)
