"""agentware_lib.features.graphdag.graphedit -- the seven AUTHORING subverbs (layer: features).

Owns: the subverbs that build and inspect a DAG without running it --
`graph new`, `add-node`, `rm-node`, `add-edge`, `rm-edge`, `show` and `validate`
(`cmd_graph_new` / `cmd_graph_add_node` / `cmd_graph_rm_node` /
`cmd_graph_add_edge` / `cmd_graph_rm_edge` / `cmd_graph_show` /
`cmd_graph_validate`).
Does NOT own: the six EXECUTION subverbs -> graphdag.graphrun; the manifest and
its validator -> graphdag.graphmanifest; the renderings -> graphdag.graphrender;
the registrar that wires all thirteen -> graphdag.graphcmd.

WHY AUTHORING IS SPLIT FROM EXECUTION
-------------------------------------
Nothing in this module spawns a process, touches a worktree, or merges anything.
That is the whole point of the split: the subverbs an operator uses to SHAPE a
graph should be safe to run at any time, against any graph, without wondering
whether they will start work. `graph validate` in particular has to be
consequence-free to be useful -- a validator you hesitate to run is one that gets
run after the mistake instead of before it.

The division also means the read-only half does not import the executor, so
authoring stays cheap and a bug in the tick loop cannot break `graph show`.

VALIDATE REPORTS EVERYTHING
---------------------------
`cmd_graph_validate` surfaces the full error list from
`graphmanifest.graph_manifest_errors` rather than the first failure, because this
scheduler will spawn real agents against a real repository and the cheapest place
to reject a malformed graph is here, in one pass, before any of that starts.

Layer deps: features -> features (`graphconst`, `graphcanon`, `graphmanifest`,
`graphrender`, `fanout.FANOUT_FEATURE_RE`), features -> ops (`plangraph` for the
write-set inference and checkpoint policy declared IN THE PLAN -- so a node's
write set comes from the plan that describes the work, never from a second
declaration that could disagree; `planauthor.planedit._plan_path`,
`planpremises._plan_is_self_extension`), features -> core. NO edge to `graphexec`
or `graphlane` -- authoring cannot reach the executor, by construction.
"""

import argparse
import json
import os
import sys

from agentware_lib.core.config import require_knowledge_dir
from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.features.fanout import FANOUT_FEATURE_RE
from agentware_lib.features.graphdag.graphcanon import _graph_canonical
from agentware_lib.features.graphdag.graphconst import GRAPH_DEFAULT_CONCURRENCY
from agentware_lib.features.graphdag.graphmanifest import (
    _graph_commit,
    _graph_load_manifest,
    _graph_manifest_path,
    _graph_new_manifest,
    graph_manifest_errors,
)
from agentware_lib.features.graphdag.graphrender import (
    _render_graph_dot,
    _render_graph_md,
    _render_graph_mermaid,
)
from agentware_lib.ops.plangraph import (
    infer_write_set,
    plan_checkpoint,
    plan_write_set,
)
from agentware_lib.ops.planauthor.planedit import _plan_path
from agentware_lib.ops.planpremises import _plan_is_self_extension


def cmd_graph_new(args):
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    name = args.name
    if not FANOUT_FEATURE_RE.match(name or ""):
        sys.stderr.write("agentware: invalid graph name %r (must match %s)\n"
                         % (name, FANOUT_FEATURE_RE.pattern))
        return 1
    path = _graph_manifest_path(kdir, name)
    if os.path.exists(path) and not getattr(args, "force", False):
        sys.stderr.write(
            "agentware: graph already exists (use --force to overwrite): %s\n"
            % path)
        return 1
    concurrency = getattr(args, "concurrency", None) or GRAPH_DEFAULT_CONCURRENCY
    manifest = _graph_new_manifest(name, concurrency=concurrency)
    return _graph_commit(kdir, manifest, args, "new")


def cmd_graph_add_node(args):
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    manifest, err = _graph_load_manifest(kdir, args.name)
    if err:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    feature = args.feature
    if any(isinstance(n, dict) and n.get("feature") == feature
           for n in manifest.get("nodes") or []):
        sys.stderr.write("agentware: node %r already in graph %r\n"
                         % (feature, args.name))
        return 1
    # COMPUTE self_extension + declared write-set from the plan.md (single-writer
    # stamps derived values; never trusted from operator input).
    ppath = _plan_path(kdir, feature)
    plan_text = ""
    try:
        with open(ppath, "r", encoding="utf-8") as f:
            plan_text = f.read()
    except OSError:
        plan_text = ""  # a missing plan is surfaced by the validator (dangling)
    self_ext = _plan_is_self_extension(plan_text)
    checkpoint = getattr(args, "checkpoint", None) or plan_checkpoint(plan_text)
    # Write-set: the DECLARED `## Write set` wins; when the plan declares none,
    # fall back to the conservative INFERENCE (target dirs + kernel scan; self-ext
    # -> {scripts/agentware}; un-narrowable -> whole-repo sentinel) so an
    # undeclared-write-set plan is still serialized against a same-file sibling
    # instead of getting an empty set that overlaps nothing (audit finding).
    declared_ws = plan_write_set(plan_text)
    write_set = declared_ws if declared_ws else infer_write_set(plan_text, REPO_ROOT)
    node = {
        "feature": feature,
        "checkpoint": checkpoint,
        "self_extension": self_ext,
        "write_set": write_set,
    }
    manifest.setdefault("nodes", []).append(node)
    return _graph_commit(kdir, manifest, args, "add-node")


def cmd_graph_rm_node(args):
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    manifest, err = _graph_load_manifest(kdir, args.name)
    if err:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    feature = args.feature
    before = len(manifest.get("nodes") or [])
    manifest["nodes"] = [n for n in manifest.get("nodes") or []
                         if not (isinstance(n, dict)
                                 and n.get("feature") == feature)]
    if len(manifest["nodes"]) == before:
        sys.stderr.write("agentware: no such node %r in graph %r\n"
                         % (feature, args.name))
        return 1
    # Drop any edge touching the removed node so the manifest stays acyclic-valid
    # (no dangling endpoint left behind).
    manifest["edges"] = [e for e in manifest.get("edges") or []
                         if not (isinstance(e, dict)
                                 and feature in (e.get("from"), e.get("to")))]
    return _graph_commit(kdir, manifest, args, "rm-node")


def cmd_graph_add_edge(args):
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    manifest, err = _graph_load_manifest(kdir, args.name)
    if err:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    kind = getattr(args, "kind", None) or "logical"
    edge = {"from": args.src, "to": args.dst, "kind": kind}
    for e in manifest.get("edges") or []:
        if (isinstance(e, dict) and e.get("from") == args.src
                and e.get("to") == args.dst and e.get("kind", "logical") == kind):
            sys.stderr.write("agentware: edge %r->%r (%s) already in graph %r\n"
                             % (args.src, args.dst, kind, args.name))
            return 1
    manifest.setdefault("edges", []).append(edge)
    return _graph_commit(kdir, manifest, args, "add-edge")


def cmd_graph_rm_edge(args):
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    manifest, err = _graph_load_manifest(kdir, args.name)
    if err:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    before = len(manifest.get("edges") or [])
    manifest["edges"] = [
        e for e in manifest.get("edges") or []
        if not (isinstance(e, dict) and e.get("from") == args.src
                and e.get("to") == args.dst)]
    if len(manifest["edges"]) == before:
        sys.stderr.write("agentware: no such edge %r->%r in graph %r\n"
                         % (args.src, args.dst, args.name))
        return 1
    return _graph_commit(kdir, manifest, args, "rm-edge")


def cmd_graph_show(args):
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    manifest, err = _graph_load_manifest(kdir, args.name)
    if err:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    render = getattr(args, "render", None) or "text"
    if getattr(args, "format", "text") == "json":
        json.dump(_graph_canonical(manifest), sys.stdout,
                  ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    elif render == "dot":
        print(_render_graph_dot(manifest))
    elif render == "mermaid":
        print(_render_graph_mermaid(manifest))
    else:
        print(_render_graph_md(manifest))
    return 0


def cmd_graph_validate(args):
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    manifest, err = _graph_load_manifest(kdir, args.name)
    if err:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    errs = graph_manifest_errors(manifest, kdir=kdir)
    empty = not (manifest.get("nodes") or [])
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": not errs, "graph": args.name, "empty": empty,
                   "errors": errs}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        if errs:
            print("graph %s: INVALID (%d error(s))" % (args.name, len(errs)))
            for e in errs:
                print("  - %s" % e)
        else:
            print("graph %s: valid%s"
                  % (args.name, " (empty)" if empty else ""))
    return 1 if errs else 0


_EXPORTS = [
    "cmd_graph_new",
    "cmd_graph_add_node",
    "cmd_graph_rm_node",
    "cmd_graph_add_edge",
    "cmd_graph_rm_edge",
    "cmd_graph_show",
    "cmd_graph_validate",
]

__all__ = list(_EXPORTS)
