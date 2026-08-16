"""agentware_lib.features.graphdag.graphmanifest -- the DAG MANIFEST: paths, load, build, validate, save (layer: features).

Owns: the on-disk locations (`_graph_dir` / `_graph_manifest_path` /
`_graph_md_path`), the reader and constructor (`_graph_load_manifest` /
`_graph_new_manifest`), the builder (`_graph_build`), the self-extension flag (`_graph_node_self_ext`), the full
validator (`graph_manifest_errors`), the durable write (`_graph_save`), the
commit helper (`_graph_commit`) and the content hash (`_graph_manifest_sha256`).
Does NOT own: the vocabulary -> graphdag.graphconst; the canonical form ->
graphdag.graphcanon; topological order and the
ready set -> graphdag.graphtopo; the human renderings -> graphdag.graphrender;
node STATE -> graphdag.graphstate; execution -> graphdag.graphexec; the verbs ->
graphdag.graphedit / graphdag.graphrun.

THE MANIFEST IS THE ONLY DURABLE TRUTH
--------------------------------------
Everything else in this package is derived: the ready set, the topological order,
the rendered graph, the status payload. The manifest is what survives a crash,
and it is what `graph resume` reads to decide what already happened. That is why
`_graph_save` writes durably and `_graph_canonical` exists at all -- a manifest
whose byte form depends on dict ordering cannot be diffed, cannot be hashed
stably, and cannot be compared across a resume.

`_graph_manifest_sha256` over the canonical form is what lets a checkpoint say
"the graph I approved is the graph that ran". Without a stable canonicalization
that hash changes on a no-op rewrite and the guarantee evaporates.

VALIDATION IS UP FRONT AND TOTAL
--------------------------------
`graph_manifest_errors` returns EVERY problem rather than the first: a scheduler
that reports one error per run makes the operator play twenty questions with a
DAG they cannot execute. It checks node identity, edge endpoints and kinds, the
size bound, and the shape of every field the executor will later assume is
well-formed -- because the executor spawns real processes against a real
repository, and the cheapest place to reject a malformed graph is before any of
that starts.

Layer deps: features -> features (`graphconst`, `graphcanon`, `graphrender` --
`_graph_save` writes the rendered view beside the manifest so the reviewable
artifact is never staler than what it describes; `fanout.FANOUT_FEATURE_RE` for
the feature-name shape), features -> ops (`plangraph._CHECKPOINT_POLICIES`,
`planauthor.planedit._atomic_write`/`_plan_path`,
`planpremises._plan_is_self_extension`), features -> knowledge
(`graph.find_cycles` -- the SAME cycle detector the knowledge graph uses, reused
rather than reimplemented), features -> core (`config`). No edge to any module
that EXECUTES: validation must be payable without importing the executor.
Determinism: canonical JSON with sorted keys, so the same logical manifest
serializes to the same bytes and hashes to the same digest on every run (INV-1).
"""

import hashlib
import json
import os
import sys

import agentware_lib.core.config as config
from agentware_lib.features.fanout import FANOUT_FEATURE_RE
from agentware_lib.features.graphdag.graphcanon import _graph_canonical
from agentware_lib.features.graphdag.graphconst import (
    GRAPH_CONCURRENCY_MAX,
    GRAPH_CONCURRENCY_MIN,
    GRAPH_DEFAULT_CONCURRENCY,
    GRAPH_MANIFEST_SCHEMA,
    GRAPH_MAX_NODES,
    _GRAPH_EDGE_KINDS,
)
from agentware_lib.features.graphdag.graphrender import _render_graph_md
from agentware_lib.knowledge.graph import find_cycles
from agentware_lib.ops.plangraph import _CHECKPOINT_POLICIES
from agentware_lib.ops.planauthor.planedit import _atomic_write, _plan_path
from agentware_lib.ops.planpremises import _plan_is_self_extension


def _graph_dir(kdir, name):
    """The per-graph directory `<kdir>/work/graphs/<name>/`."""
    return os.path.join(kdir, "work", "graphs", name)


def _graph_manifest_path(kdir, name):
    return os.path.join(_graph_dir(kdir, name), "graph.json")


def _graph_md_path(kdir, name):
    return os.path.join(_graph_dir(kdir, name), "graph.md")


def _graph_load_manifest(kdir, name):
    """Load a graph manifest. Returns (manifest, error). A missing file is an
    error (the caller decides whether that is fatal). Read-only."""
    path = _graph_manifest_path(kdir, name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None, "no such graph %r (manifest not found: %s)" % (name, path)
    except (OSError, json.JSONDecodeError) as exc:
        return None, "invalid graph manifest %s: %s" % (path, exc)
    if not isinstance(data, dict):
        return None, "malformed graph manifest %s (expected a JSON object)" % path
    return data, None


def _graph_new_manifest(name, concurrency=GRAPH_DEFAULT_CONCURRENCY):
    """An empty, schema-stamped manifest (canonical field order)."""
    return {
        "schema": GRAPH_MANIFEST_SCHEMA,
        "name": name,
        "concurrency": concurrency,
        "nodes": [],
        "edges": [],
    }


def _graph_build(manifest):
    """Shape a manifest into (nodes, forward, reverse) for the generic graph
    primitives (`find_cycles`/`topo_order`). Pure + read-only.

    nodes    = sorted, de-duplicated node feature names
    forward  = pred -> sorted [(kind, succ)]   (edge from=pred, to=succ)
    reverse  = succ -> sorted [(kind, pred)]
    Edges are sorted `(kind, target)` for determinism. Malformed edges (non-str
    endpoints) are skipped here — the validator names them separately."""
    nodes = sorted({n["feature"] for n in (manifest.get("nodes") or [])
                    if isinstance(n, dict) and isinstance(n.get("feature"), str)})
    forward, reverse = {}, {}
    for e in (manifest.get("edges") or []):
        if not isinstance(e, dict):
            continue
        src, dst = e.get("from"), e.get("to")
        kind = e.get("kind", "logical")
        if not isinstance(src, str) or not isinstance(dst, str):
            continue
        forward.setdefault(src, []).append((kind, dst))
        reverse.setdefault(dst, []).append((kind, src))
    for amap in (forward, reverse):
        for k in list(amap):
            amap[k] = sorted(set(amap[k]))
    return nodes, forward, reverse


def _graph_node_self_ext(kdir, feature, stored=None):
    """COMPUTE whether a node is self-extension by reading its plan.md (never
    trusted from operator input). Falls back to `stored` when the plan cannot be
    read (a missing plan is flagged separately as a dangling node). Returns a
    (bool, error_or_None) pair."""
    if not kdir:
        return bool(stored), None
    ppath = _plan_path(kdir, feature)
    try:
        with open(ppath, "r", encoding="utf-8") as f:
            return _plan_is_self_extension(f.read()), None
    except FileNotFoundError:
        return bool(stored), "plan-missing"
    except OSError as exc:
        return bool(stored), "plan-unreadable: %s" % exc


def graph_manifest_errors(manifest, kdir=None):
    """Validate a graph manifest. Returns an ORDERED list of error strings (empty
    == valid). A non-empty list means REFUSE (the caller exits non-zero and writes
    nothing). Every error NAMES the offender. Pure + read-only.

    Checks, in order: schema, graph name, concurrency range, nodes/edges shape,
    node-count bound, per-node (name / plan-exists / checkpoint vocab / self-ext
    ⇒ manual checkpoint), then INLINE edge integrity over the manifest's OWN
    node/edge lists (self-edge, dangling endpoint, duplicate, kind vocab) — NOT
    `graph_relation_errors`/`build_adjacency`, which consume the KB-index
    `entries`/`relates` shape and would silently pass a `{nodes,edges}` manifest
    (audit finding) — and finally a cycle scan via the generic `find_cycles`.

    `self_extension` is COMPUTED per node from its plan.md when `kdir` is given
    (never trusted from operator input); without `kdir` the node's stored
    `self_extension` field is used so the pure structural checks stay testable."""
    errors = []
    if not isinstance(manifest, dict):
        return ["manifest is not a JSON object"]

    schema = manifest.get("schema")
    if schema != GRAPH_MANIFEST_SCHEMA:
        errors.append("unsupported manifest schema %r (expected %d)"
                      % (schema, GRAPH_MANIFEST_SCHEMA))

    name = manifest.get("name")
    if not isinstance(name, str) or not FANOUT_FEATURE_RE.match(name or ""):
        errors.append("invalid graph name %r (must match %s)"
                      % (name, FANOUT_FEATURE_RE.pattern))

    concurrency = manifest.get("concurrency", GRAPH_DEFAULT_CONCURRENCY)
    if (not isinstance(concurrency, int) or isinstance(concurrency, bool)
            or not GRAPH_CONCURRENCY_MIN <= concurrency <= GRAPH_CONCURRENCY_MAX):
        errors.append("concurrency %r out of range [%d,%d]"
                      % (concurrency, GRAPH_CONCURRENCY_MIN,
                         GRAPH_CONCURRENCY_MAX))

    nodes = manifest.get("nodes")
    edges = manifest.get("edges")
    if not isinstance(nodes, list):
        errors.append("nodes is not a list")
        nodes = []
    if not isinstance(edges, list):
        errors.append("edges is not a list")
        edges = []

    if len(nodes) > GRAPH_MAX_NODES:
        errors.append("node count %d exceeds the per-graph maximum %d"
                      % (len(nodes), GRAPH_MAX_NODES))

    node_set = set()
    for n in nodes:
        if not isinstance(n, dict):
            errors.append("node entry is not an object: %r" % (n,))
            continue
        feat = n.get("feature")
        if not isinstance(feat, str) or not FANOUT_FEATURE_RE.match(feat or ""):
            errors.append("invalid node feature name %r (must match %s)"
                          % (feat, FANOUT_FEATURE_RE.pattern))
            continue
        if feat in node_set:
            errors.append("duplicate node %r" % feat)
            continue
        node_set.add(feat)

        cp = n.get("checkpoint", "none")
        if cp not in _CHECKPOINT_POLICIES:
            errors.append("node %r has unknown checkpoint policy %r (vocab: %s)"
                          % (feat, cp, ", ".join(_CHECKPOINT_POLICIES)))

        self_ext, se_err = _graph_node_self_ext(
            kdir, feat, stored=n.get("self_extension"))
        if se_err == "plan-missing":
            errors.append("node %r references a plan that does not exist: %s"
                          % (feat, _plan_path(kdir, feat)))
        elif se_err:
            errors.append("node %r plan unreadable (%s)" % (feat, se_err))
        # A self-extension node is a MANDATORY manual checkpoint (tier-3: the
        # operator runs it themselves; the orchestrator never autonomously spawns
        # it). A weaker checkpoint is a validate-time refusal.
        if self_ext and cp != "manual":
            errors.append(
                "self-extension node %r must carry a 'manual' checkpoint (got "
                "%r) — a self-extension node is a mandatory manual checkpoint "
                "the operator runs themselves" % (feat, cp))

    # Inline edge integrity over the manifest's OWN node/edge lists.
    seen_edges = set()
    for e in edges:
        if not isinstance(e, dict):
            errors.append("edge entry is not an object: %r" % (e,))
            continue
        src, dst = e.get("from"), e.get("to")
        kind = e.get("kind", "logical")
        if kind not in _GRAPH_EDGE_KINDS:
            errors.append("edge %r->%r has unknown kind %r (vocab: %s)"
                          % (src, dst, kind, ", ".join(_GRAPH_EDGE_KINDS)))
        if not isinstance(src, str) or not isinstance(dst, str):
            errors.append("edge has non-string endpoint(s): from=%r to=%r"
                          % (src, dst))
            continue
        if src == dst:
            errors.append("self-edge on node %r is not allowed" % src)
            continue
        if src not in node_set:
            errors.append("dangling edge: source %r is not a node (edge %r->%r)"
                          % (src, src, dst))
        if dst not in node_set:
            errors.append("dangling edge: target %r is not a node (edge %r->%r)"
                          % (dst, src, dst))
        ekey = (src, dst, kind)
        if ekey in seen_edges:
            errors.append("duplicate edge %r->%r (%s)" % (src, dst, kind))
        seen_edges.add(ekey)

    # Cycle scan via the generic colored-DFS (reused verbatim). Runs over the
    # manifest's declared edges; the scheduler's derived resource edges (Task 3)
    # are re-checked at run-start.
    _nodes, forward, _reverse = _graph_build(manifest)
    for cyc in find_cycles(forward):
        errors.append("cycle detected: %s"
                      % " -> ".join(list(cyc) + [cyc[0]]))

    return errors


def _graph_save(kdir, manifest):
    """The SINGLE writer for a graph manifest: canonicalize, atomically write
    `graph.json`, then regenerate the derived `graph.md`. Both writes go through
    `_atomic_write` so a crash can never leave a half-written manifest."""
    name = manifest["name"]
    canon = _graph_canonical(manifest)
    _atomic_write(
        _graph_manifest_path(kdir, name),
        json.dumps(canon, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    _atomic_write(_graph_md_path(kdir, name), _render_graph_md(canon) + "\n")


def _graph_commit(kdir, manifest, args, action):
    """Validate the PROSPECTIVE manifest, then (only if clean) persist it. On any
    validation error: print each offender to stderr and return 1 WITHOUT writing
    (the no-writes-on-error invariant). Shared tail of every writer."""
    errs = graph_manifest_errors(manifest, kdir=kdir)
    if errs:
        sys.stderr.write(
            "agentware: refusing to %s — graph %r is invalid:\n" %
            (action, manifest.get("name")))
        for e in errs:
            sys.stderr.write("  - %s\n" % e)
        return 1
    _graph_save(kdir, manifest)
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": True, "action": action, "graph": manifest["name"],
                   "path": _graph_manifest_path(kdir, manifest["name"]),
                   "nodes": len(manifest.get("nodes") or []),
                   "edges": len(manifest.get("edges") or [])},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("graph %s: %s (%d nodes, %d edges)"
              % (manifest["name"], action, len(manifest.get("nodes") or []),
                 len(manifest.get("edges") or [])))
    return 0


def _graph_manifest_sha256(manifest):
    """Stable sha256 of the CANONICAL manifest bytes (nodes/edges sorted, compact
    separators). This is the content-pin the operator approves; resume refuses on
    any drift. Pure — no clock, no RNG."""
    canon = _graph_canonical(manifest)
    blob = json.dumps(canon, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


_EXPORTS = [
    "_graph_dir",
    "_graph_manifest_path",
    "_graph_md_path",
    "_graph_load_manifest",
    "_graph_new_manifest",
    "_graph_build",
    "_graph_node_self_ext",
    "graph_manifest_errors",
    "_graph_save",
    "_graph_commit",
    "_graph_manifest_sha256",
]

__all__ = list(_EXPORTS)
