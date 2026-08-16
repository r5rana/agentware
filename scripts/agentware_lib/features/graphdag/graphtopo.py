"""agentware_lib.features.graphdag.graphtopo -- topological ORDER, the ready set, and the write-set guards (layer: features).

Owns: the topological sort (`topo_order`), the dispatch frontier
(`_graph_ready_set`), the blast-radius walk (`_graph_transitive_successors`), and
the WRITE-SET machinery that turns declared file writes into implicit ordering
edges -- `_graph_manifest_write_sets`, `_write_token_matches`,
`_write_sets_overlap`, `_graph_derive_resource_edges` and the kernel-path guard
`_graph_write_set_touches_kernel`.
Does NOT own: the manifest -> graphdag.graphmanifest; node STATE ->
graphdag.graphstate; lane lifecycle -> graphdag.graphlane; execution ->
graphdag.graphexec.

THE POINT: TWO NODES THAT WRITE THE SAME FILE ARE NOT PARALLEL
--------------------------------------------------------------
A DAG's declared edges say what depends on what. They do NOT say what CONFLICTS
with what, and this scheduler runs lanes concurrently against one repository. Two
nodes with no dependency between them but overlapping WRITE SETS will race --
last writer wins, silently, and the losing node's work vanishes with its lane
reporting success.

`_graph_derive_resource_edges` closes that by DERIVING serialization edges from
declared write sets: nodes whose write sets overlap get an implicit edge, so they
run in some order rather than at the same time. The matching is token-based
(`_write_token_matches`) rather than exact-string, because `scripts/foo.py` and
`scripts/` are the same conflict and a naive equality check would miss it.

`_graph_write_set_touches_kernel` is the harder refusal: a node whose write set
reaches a kernel path is serialized against every other such node via
`GRAPH_KERNEL_SERIALIZE_TOKEN`, because two concurrent agents editing the
orchestrator itself is the failure that takes the whole system down rather than
one lane.

READY SET, NOT A QUEUE
----------------------
`_graph_ready_set` recomputes the frontier from CURRENT node states every tick
rather than maintaining a queue. A queue would have to be kept consistent with a
manifest that a crash, a resume, or a human `graph cancel` can change underneath
it; a recomputed frontier cannot go stale. `_graph_transitive_successors` answers
the blast-radius question a failure asks -- when a node halts its subtree, this is
what says which nodes that is.

Layer deps: features -> features (`graphconst` for the states and the serialize
token, `graphmanifest._graph_build` for the adjacency it sorts), features ->
knowledge (`graph.find_cycles` -- the SAME detector the knowledge graph uses),
features -> ops (`plangraph._write_token_norm` / `_WRITE_SET_WHOLE_REPO`, so the
write-set token grammar is defined ONCE and the plan's declaration and the
scheduler's conflict check can never disagree about what a token means). Pure: every function is a fold over the manifest structure -- no file
reads, no spawning, no clock.
Determinism: sorts break every tie, so the frontier and the derived edge set are
identical across runs over an unchanged manifest (INV-1) -- which is what makes a
dry-run's plan match the real run's dispatch.
"""

from agentware_lib.features.graphdag.graphconst import (
    GRAPH_KERNEL_SERIALIZE_TOKEN,
    GRAPH_NODE_PENDING,
    _GRAPH_DEP_SATISFIED,
    _GRAPH_DISPATCHABLE,
)
from agentware_lib.features.graphdag.graphmanifest import _graph_build
from agentware_lib.knowledge.graph import find_cycles
from agentware_lib.ops.plangraph import _WRITE_SET_WHOLE_REPO, _write_token_norm


def topo_order(forward, nodes):
    """Kahn topological sort over a `pred -> [(kind, succ)]` forward adjacency,
    restricted to `nodes`. Returns a deterministic linearization: the in-degree-0
    frontier is RE-SORTED lexically on every step (a stable lexical tie-break), so
    the chosen linear extension of the partial order is byte-identical across runs.
    Raises ValueError on a residual (a cycle among `nodes`). Edges whose endpoint
    is outside `nodes` are ignored (the validator names a dangling edge
    separately). Pure — no clock, no RNG.

    Built FIRST because `_graph_derive_resource_edges` orients every resource edge
    along THIS single linearization of the logical DAG (never a pairwise rule)."""
    node_set = set(nodes)
    indeg = {n: 0 for n in node_set}
    succ = {n: [] for n in node_set}
    for src in forward:
        if src not in node_set:
            continue
        for _kind, dst in forward[src]:
            if dst not in node_set:
                continue
            succ[src].append(dst)
            indeg[dst] += 1
    frontier = sorted(n for n in node_set if indeg[n] == 0)
    order = []
    while frontier:
        n = frontier.pop(0)
        order.append(n)
        newly = False
        for dst in succ[n]:
            indeg[dst] -= 1
            if indeg[dst] == 0:
                frontier.append(dst)
                newly = True
        if newly:
            frontier.sort()
    if len(order) != len(node_set):
        residual = sorted(node_set - set(order))
        raise ValueError("topological sort failed: cycle among %s"
                         % ", ".join(residual))
    return order


def _graph_manifest_write_sets(manifest):
    """{feature: write-set list} straight from the manifest nodes (the single
    writer stamped each node's `write_set` at add-node time). Read-only."""
    out = {}
    for n in (manifest.get("nodes") or []):
        if isinstance(n, dict) and isinstance(n.get("feature"), str):
            ws = n.get("write_set")
            out[n["feature"]] = list(ws) if isinstance(ws, list) else []
    return out


def _write_token_matches(a, b):
    """Two write-set tokens CONFLICT when, after normalization, they name the same
    path, OR one is the whole-repo sentinel, OR one is a `dir/**` glob that covers
    the other, OR one is a DIRECTORY that contains the other (a bare directory
    token — which `infer_write_set` emits from a plan's target dirs — covers every
    path beneath it). Normalization folds `./`-prefixed and case-variant spellings
    of the same file together so a benign path variant can never defeat
    serialization (audit finding). Conservative by design: it only ever ADDS
    matches, so it can over-serialize but never miss a genuine same-file pair.
    Pure."""
    a = _write_token_norm(a)
    b = _write_token_norm(b)
    if a == b:
        return True
    for x, y in ((a, b), (b, a)):
        if x == _WRITE_SET_WHOLE_REPO:      # whole-repo sentinel overlaps all
            return True
        if x.endswith("/**"):               # `dir/**` glob cover
            base = x[:-3]
            if base == "" or y == base or y.startswith(base + "/"):
                return True
        elif y.startswith(x + "/"):         # bare directory contains y
            return True
    return False


def _write_sets_overlap(ws_a, ws_b):
    """True iff any token in `ws_a` conflicts with any token in `ws_b` (exact file
    or `dir/**` cover). Pure/read-only."""
    for a in ws_a:
        for b in ws_b:
            if _write_token_matches(a, b):
                return True
    return False


def _graph_derive_resource_edges(manifest, write_sets=None):
    """Auto-derive resource (mutual-exclusion) edges for every pair of nodes with
    OVERLAPPING write-sets, oriented from the EARLIER to the LATER node in the
    SINGLE `topo_order` linearization of the LOGICAL DAG.

    Orienting along one linear extension of the logical partial order is
    cycle-proof BY CONSTRUCTION: a resource edge never contradicts a logical edge
    (both point 'forward' in the same linearization), so `logical ∪ resource` is
    always a DAG. The naive pairwise 'logical-first-else-lexical' rule is WRONG —
    it is not a consistent total order and can synthesize a cycle (a→b, b→c, c→a)
    out of the very same-file clique it must serialize (audit BLOCKER).

    Returns a sorted list of edge dicts {from,to,kind:'resource',derived:True} for
    each overlapping pair NOT already ordered by a declared edge (either
    direction, either kind). `write_sets` overrides the manifest's stamped sets
    (testability). Pure; raises ValueError if the LOGICAL sub-graph has a cycle
    (the caller validates first) OR — as a defence-in-depth assertion — if the
    union somehow produced a cycle."""
    nodes, forward, _reverse = _graph_build(manifest)
    logical_forward = {}
    for src, edges in forward.items():
        keep = [(k, d) for (k, d) in edges if k == "logical"]
        if keep:
            logical_forward[src] = keep
    order = topo_order(logical_forward, nodes)
    rank = {feat: i for i, feat in enumerate(order)}

    ws = (dict(write_sets) if write_sets is not None
          else _graph_manifest_write_sets(manifest))
    declared = set()
    for e in (manifest.get("edges") or []):
        if (isinstance(e, dict) and isinstance(e.get("from"), str)
                and isinstance(e.get("to"), str)):
            declared.add((e["from"], e["to"]))

    derived = []
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a, b = nodes[i], nodes[j]
            if not _write_sets_overlap(ws.get(a, []), ws.get(b, [])):
                continue
            src, dst = (a, b) if rank[a] < rank[b] else (b, a)
            if (src, dst) in declared or (dst, src) in declared:
                continue  # already ordered by a declared edge
            derived.append({"from": src, "to": dst, "kind": "resource",
                            "derived": True})
    derived.sort(key=lambda e: (e["from"], e["to"]))

    # Defence-in-depth: `logical ∪ resource` MUST be acyclic (the whole point of
    # the linear-extension orientation). Assert it, so a future edit that
    # reintroduces the pairwise rule fails loudly instead of deadlocking a run.
    combined = {"nodes": manifest.get("nodes") or [],
                "edges": list(manifest.get("edges") or []) + derived}
    _cn, comb_forward, _cr = _graph_build(combined)
    cycles = find_cycles(comb_forward)
    if cycles:
        raise ValueError(
            "resource-edge derivation produced a cycle: %s"
            % "; ".join(" -> ".join(list(c) + [c[0]]) for c in cycles))
    return derived


def _graph_ready_set(graph, statuses):
    """The nodes dispatchable THIS tick — a PURE function of (topology, statuses).

    A node is ready iff its own status is dispatchable (`pending`) AND every
    predecessor (logical OR resource) is `done` (the sole dependency-satisfying
    state). A predecessor that is superseded/cancelled/failed/blocked/running
    leaves the dependent un-ready (it stays pending — the scheduler reports it as
    transitively blocked, never writes `cancelled`), so a fixed-and-re-run
    upstream auto-releases it. Returns a sorted list (stable dispatch order).

    `graph` is the (nodes, forward, reverse) triple from `_graph_build` over the
    `logical ∪ resource` edge set. Read-only; no clock, no RNG."""
    nodes, _forward, reverse = graph
    ready = []
    for n in nodes:
        if statuses.get(n, GRAPH_NODE_PENDING) not in _GRAPH_DISPATCHABLE:
            continue
        preds = reverse.get(n, [])
        if all(statuses.get(p) in _GRAPH_DEP_SATISFIED for (_k, p) in preds):
            ready.append(n)
    return sorted(ready)


def _graph_write_set_touches_kernel(write_set):
    """True iff a write-set touches the governance kernel (`scripts/agentware`) —
    exactly, or via a covering `dir/**` glob / the un-narrowable sentinel. Pure."""
    for tok in (write_set or []):
        if _write_token_matches(tok, GRAPH_KERNEL_SERIALIZE_TOKEN):
            return True
    return False


def _graph_transitive_successors(manifest, node):
    """The transitive forward-reachable successor set of `node` across the COMBINED
    logical ∪ resource graph (sorted, excluding `node` itself). Pure + read-only —
    resource edges are re-derived along the single topo linearization so the abort
    reaches every serialized sibling the scheduler would have ordered after it."""
    try:
        derived = _graph_derive_resource_edges(manifest)
    except ValueError:
        derived = []
    full = {"nodes": manifest.get("nodes") or [],
            "edges": list(manifest.get("edges") or []) + derived}
    _nodes, forward, _reverse = _graph_build(full)
    seen, stack = set(), [node]
    while stack:
        cur = stack.pop()
        for _kind, dst in forward.get(cur, []):
            if dst not in seen:
                seen.add(dst)
                stack.append(dst)
    seen.discard(node)
    return sorted(seen)


_EXPORTS = [
    "topo_order",
    "_graph_manifest_write_sets",
    "_write_token_matches",
    "_write_sets_overlap",
    "_graph_derive_resource_edges",
    "_graph_ready_set",
    "_graph_transitive_successors",
    "_graph_write_set_touches_kernel",
]

__all__ = list(_EXPORTS)
