"""agentware_lib.knowledge.graph — declared-edge KB dependency graph + `query` (layer: knowledge).

Owns: the deterministic, read-only declared-edge graph reconstructed from each
entry's `relates` frontmatter — build_adjacency (forward/reverse), _canonical_cycle,
find_cycles (colored DFS), graph_relation_errors (dangling/unknown-type scan),
graph_health (Rule-7 advisory counts), graph_traverse (cycle-safe closure) — plus
the `query` verb: its handler cmd_query (O(1) id/path/tag/category + graph
--depends-on/--impact/--relates dispatch) and its parser registrar register_query.
Does NOT own: the `relates` token PARSE/vocab (agentware_lib.core.frontmatter:
_fm_parse_relate_token/RELATION_TYPES); index load + layout (agentware_lib.knowledge.index);
the graph_integrity AUDIT check and the graph_health metric EMISSION (still
entrypoint-resident; join agentware_lib.ops in waves 4/5 and call graph.* downward).
Layer deps: knowledge -> core (config, frontmatter) + knowledge (index); nothing
above. The edges are DECLARED by a human/agent, never LLM-extracted (INV-2); every
op is pure/stdlib/no-clock/no-network and visits neighbors in stable sorted
(type, target) order, so output is byte-identical given the same KB (INV-1).
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
# The freshness fields are defined ONCE, beside the recall payload that also
# emits them — two copies would drift and `query` would start disagreeing
# with `recall` about the same entry's age. Imported lazily inside the
# helper to keep knowledge -> retrieval off the module-import path.
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.knowledge.index as index


def build_adjacency(data):
    """Build (forward, reverse) adjacency from `data["entries"]` `relates` edges.

    forward[id] = sorted, de-duplicated [(type, target_id), ...]
    reverse[id] = sorted, de-duplicated [(type, source_id), ...]
    Ordered by (type, target) for determinism (INV-1). Malformed tokens are
    skipped. Pure + read-only — never mutates the KB or index (INV-2).

    `relates` is read through `frontmatter._entry_list_field`: this is the recall
    HOT PATH (trust signals / superseded ids build adjacency), so a poisoned index
    entry whose `relates` is a scalar, string or mapping must degrade to "no edges
    for that entry", never crash the caller.
    """
    forward, reverse = {}, {}
    for e in data.get("entries") or []:
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        if not eid:
            continue
        for tok in frontmatter._entry_list_field(e, "relates"):
            parsed = frontmatter._fm_parse_relate_token(tok)
            if parsed is None:
                continue
            etype, target = parsed
            forward.setdefault(eid, []).append((etype, target))
            reverse.setdefault(target, []).append((etype, eid))
    for amap in (forward, reverse):
        for k in list(amap):
            amap[k] = sorted(set(amap[k]))
    return forward, reverse


def _canonical_cycle(cyc):
    """Rotate a cycle to start at its lexically-smallest id (stable form)."""
    i = cyc.index(min(cyc))
    return tuple(cyc[i:] + cyc[:i])


def find_cycles(forward):
    """Return a sorted list of canonical cycles (each a tuple of ids).

    Iterative colored DFS over the sorted forward adjacency — deterministic and
    cycle-safe (no infinite loop). Each distinct cycle (by node set) reported
    once. Pure + read-only (advisory; never mutates anything).
    """
    nodes = sorted(set(forward) | {t for es in forward.values() for (_, t) in es})
    adj = {n: sorted({t for (_, t) in forward.get(n, [])}) for n in nodes}
    color = {n: 0 for n in nodes}  # 0 white, 1 grey (on stack), 2 black
    cycles, seen = [], set()
    for root in nodes:
        if color[root] != 0:
            continue
        color[root] = 1
        stack = [(root, 0)]
        path = [root]
        while stack:
            node, idx = stack[-1]
            nbrs = adj[node]
            if idx < len(nbrs):
                stack[-1] = (node, idx + 1)
                nxt = nbrs[idx]
                if color[nxt] == 0:
                    color[nxt] = 1
                    path.append(nxt)
                    stack.append((nxt, 0))
                elif color[nxt] == 1 and nxt in path:
                    cyc = path[path.index(nxt):]
                    key = frozenset(cyc)
                    if key not in seen:
                        seen.add(key)
                        cycles.append(_canonical_cycle(cyc))
            else:
                color[node] = 2
                stack.pop()
                path.pop()
    return sorted(cycles)


def graph_relation_errors(data):
    """Declared-edge integrity scan. Returns (dangling, unknown_types).

    dangling       = sorted [(source_id, target_id)] where target is not in the
                     index (a `relates` id that does not resolve).
    unknown_types  = sorted [(source_id, token)] whose type is out of vocab.
    Pure + read-only (INV-2); consumed by the graph_integrity audit (Task 8) and
    edge validation (Task 4).
    """
    ids = {e.get("id") for e in (data.get("entries") or [])
           if isinstance(e, dict) and e.get("id")}
    dangling, unknown = [], []
    for e in data.get("entries") or []:
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        if not eid:
            continue
        for tok in frontmatter._entry_list_field(e, "relates"):
            parsed = frontmatter._fm_parse_relate_token(tok)
            if parsed is None:
                continue
            etype, target = parsed
            if etype not in frontmatter.RELATION_TYPES:
                unknown.append((eid, tok))
            if target not in ids:
                dangling.append((eid, target))
    return sorted(set(dangling)), sorted(set(unknown))


def graph_health(data):
    """Advisory graph-health counts (Rule-7 observability). Pure + read-only."""
    forward, _reverse = build_adjacency(data)
    dangling, unknown = graph_relation_errors(data)
    cycles = find_cycles(forward)
    edges = sum(len(v) for v in forward.values())
    return {
        "edges": edges,
        "nodes_with_edges": len([k for k in forward if forward[k]]),
        "dangling": len(dangling),
        "unknown_types": len(unknown),
        "cycles": len(cycles),
    }


def graph_traverse(adjacency, start, depth):
    """Deterministic, cycle-safe closure from `start` over `adjacency`.

    depth == 1 -> direct neighbors; depth == 0 -> full transitive closure;
    depth == N -> up to N hops. Neighbors are visited in sorted (type, target)
    order; the returned id list excludes `start` and is in deterministic BFS
    order. A visited-set makes cycles terminate (INV-1 + cycle-safe).
    """
    visited = {start}
    order = []
    frontier = [start]
    level = 0
    while frontier:
        level += 1
        if depth != 0 and level > depth:
            break
        neigh = []
        for node in frontier:
            for (etype, target) in sorted(adjacency.get(node, [])):
                neigh.append((etype, target))
        next_frontier = []
        for (_etype, target) in sorted(neigh):
            if target not in visited:
                visited.add(target)
                order.append(target)
                next_frontier.append(target)
        frontier = sorted(next_frontier)
    return order


def _entry_with_freshness(entry, kdir, as_of):
    """A COPY of an index entry with the freshness fields appended. Never mutates
    the input dict — the MCP server reuses the loaded index across calls and
    recall/query are read-only (INV-2)."""
    from agentware_lib.retrieval.recallcmd import _freshness_fields
    out = dict(entry)
    out.update(_freshness_fields(entry, kdir, as_of))
    return out


def _lookup_maps(entries):
    """(by_id, by_path) O(1) lookup maps over the index entries.

    FIRST wins on a duplicate id or path: a KB with two rows claiming one id is
    malformed, and `query --id` must still answer the same entry every time
    rather than depend on scan order (INV-1). Non-dict rows are skipped — the
    index is attacker-influenceable through a teammate's shard, so a poisoned
    row degrades to "not found", never to a crash.
    """
    by_id, by_path = {}, {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        if eid is not None and eid not in by_id:
            by_id[eid] = e
        path = e.get("path")
        if path is not None and path not in by_path:
            by_path[path] = e
    return by_id, by_path


def _direct_results(args, entries, tags_map, by_id, by_path):
    """Results for the four DIRECT lookup modes, or None if none was selected.

    None (not []) is the "no direct mode" signal, because a SELECTED mode that
    matches nothing must stay an empty result and never fall through to the
    graph arms — `query --id nope` answers `[]`, it does not silently become a
    traversal.
    """
    if args.id is not None:
        hit = by_id.get(args.id)
        return [hit] if hit is not None else []
    if args.path is not None:
        hit = by_path.get(args.path)
        return [hit] if hit is not None else []
    if args.tag is not None:
        return [by_id[eid] for eid in tags_map.get(args.tag, []) if eid in by_id]
    if args.category is not None:
        return [e for e in entries
                if isinstance(e, dict) and e.get("category") == args.category]
    return None


def _graph_adjacency(data, mode):
    """The adjacency one graph mode traverses: forward, reverse, or their union.

    The three modes differ ONLY in which direction they walk, which is why they
    share a builder — three near-verbatim copies are three places for the union
    merge and the sorted-dedup to drift apart.
    """
    forward, reverse = build_adjacency(data)
    if mode == "depends_on":
        return forward
    if mode == "impact":
        return reverse
    combined = {}
    for amap in (forward, reverse):
        for k, v in amap.items():
            combined.setdefault(k, []).extend(v)
    for k in list(combined):
        combined[k] = sorted(set(combined[k]))
    return combined


def _traverse_results(args, data, by_id):
    """Results for the three GRAPH modes ([] when none was selected).

    Modes are tried in the parser's own order so a Namespace carrying more than
    one (which argparse's mutually-exclusive group forbids, but the MCP server's
    hand-built namespace does not) resolves the same way it always has. Ids that
    do not resolve to an entry are dropped: a `relates` edge may name a target
    that was never indexed, and a dangling edge is not a result.
    """
    for mode in ("depends_on", "impact", "relates"):
        start = getattr(args, mode, None)
        if start is None:
            continue
        ids = graph_traverse(_graph_adjacency(data, mode), start,
                             getattr(args, "depth", 1))
        return [by_id[i] for i in ids if i in by_id]
    return []


def cmd_query(args):
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    data, err = index.load_index(kdir)
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1

    entries = data.get("entries") or []
    by_id, by_path = _lookup_maps(entries)
    results = _direct_results(args, entries, data.get("tags") or {},
                              by_id, by_path)
    if results is None:
        results = _traverse_results(args, data, by_id)

    # Append the point-in-time freshness fields to a COPY of each entry. Never
    # mutates the loaded index: the MCP server reuses it across calls and both
    # `query` and `recall` are read-only (INV-2). The LIVE-anchored `abs_path` is
    # the load-bearing one — a caller acting on a repo-relative path resolves it
    # against ITS cwd, which is not necessarily this one (R-FAIL-08).
    results = [_entry_with_freshness(e, kdir, dates._today()) for e in results]
    json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("query", 800, "register_query"),
)


def register_query(sub, fmt_parent):
    # query ------------------------------------------------------------------
    p_query = sub.add_parser(
        "query", parents=[fmt_parent], help="look up entries (O(1) by id/path/tag)")
    q_group = p_query.add_mutually_exclusive_group(required=True)
    q_group.add_argument("--id", help="look up a single entry by id")
    q_group.add_argument("--path", help="look up a single entry by path")
    q_group.add_argument("--category", help="list all entries in a category")
    q_group.add_argument("--tag", help="list all entries carrying a tag")
    q_group.add_argument("--depends-on", dest="depends_on",
                         help="forward closure: entries the given id depends on")
    q_group.add_argument("--impact",
                         help="reverse closure: entries that depend on the given id")
    q_group.add_argument("--relates",
                         help="direct graph neighbors of the given id (both directions)")
    p_query.add_argument("--depth", type=int, default=1,
                         help="traversal hops for graph queries (default 1; 0 = full closure)")
    p_query.set_defaults(func=cmd_query)


_EXPORTS = [
    "build_adjacency",
    "_canonical_cycle",
    "find_cycles",
    "graph_relation_errors",
    "graph_health",
    "graph_traverse",
    "cmd_query",
    "register_query",
]
