"""agentware_lib.features.graphdag.graphrun -- the six EXECUTION subverbs (layer: features).

Owns: the subverbs that start, observe, gate or stop a run -- `graph run`,
`resume`, `status`, `checkpoint`, `cancel` and `teardown` (`cmd_graph_run` /
`cmd_graph_resume` / `cmd_graph_status` / `cmd_graph_checkpoint` /
`cmd_graph_cancel` / `cmd_graph_teardown`).
Does NOT own: the seven AUTHORING subverbs -> graphdag.graphedit; the tick loop
-> graphdag.graphexec; lane spawn and merge -> graphdag.graphlane; the ledger and
status payload -> graphdag.graphledger; the registrar -> graphdag.graphcmd.

RUN AND RESUME ARE THE SAME PATH
--------------------------------
`resume` is not recovery code. Because every node's state is DERIVED from on-disk
evidence rather than remembered, resuming is just running a graph that already
has progress -- the same tick loop, reaching the same conclusions the crashed run
would have reached. A separate recovery path would be code that runs only after
something already went wrong, which is the code least likely to be correct when
it finally runs.

CHECKPOINT IS THE HUMAN GATE, AND IT IS BOUND TO A HASH
--------------------------------------------------------
`cmd_graph_checkpoint` records an approval against the manifest's canonical
hash, so an approval means "I approved THIS graph" rather than "I approved this
graph's name at some point". Edit the DAG after approval and the hash moves, so
the stale approval no longer applies -- which is the entire reason the
canonicalization exists. The four verdicts map to dispositions
(continue / pause / halt / block-subtree) through `graphconst`'s table, so the
human's decision and the scheduler's behaviour cannot drift apart.

CANCEL AND TEARDOWN ARE DIFFERENT, DELIBERATELY
-----------------------------------------------
`cancel` stops scheduling and records the stop; `teardown` removes the worktrees
and branches a run provisioned. Keeping them apart means an operator can stop a
runaway graph immediately without also destroying the lanes they may still want
to inspect — and inspecting the wreckage is usually the point of stopping.

Layer deps: features -> features (`graphconst`, `graphmanifest`, `graphexec`,
`graphledger`, `graphstate`, `graphlane`, `fanoutmq` for teardown), features ->
core. This module and `graphexec` are the only ones that cause side effects
beyond a manifest write.
"""

import argparse
import contextlib
import json
import os
import sys
import time

from agentware_lib.core.config import require_knowledge_dir, resolve_knowledge_dir
from agentware_lib.core.dates import _now_iso, utc_now_iso
from agentware_lib.features.fanout import load_lanes
from agentware_lib.features.fanoutmq.mqteardown import cmd_fanout_teardown
from agentware_lib.features.graphdag.graphconst import (
    GRAPH_DEADLINE_HOURS,
    _GRAPH_CHECKPOINT_VERDICTS,
)
from agentware_lib.features.graphdag.graphexec import _graph_execute, _graph_finish
from agentware_lib.features.graphdag.graphledger import (
    _append_checkpoint_log,
    _graph_emit_metric,
    _graph_last_approval,
    _graph_ledger_append,
    _graph_report,
    _graph_result,
    _graph_status_payload,
)
from agentware_lib.features.graphdag.graphmanifest import (
    _graph_load_manifest,
    _graph_manifest_sha256,
    graph_manifest_errors,
)
from agentware_lib.features.graphdag.graphtopo import _graph_transitive_successors
from agentware_lib.ops.planauthor.planedit import _atomic_write, _plan_path
from agentware_lib.ops.planauthor.planstate import (
    _load_plan_tolerant,
    cmd_plan_set_state,
)


def cmd_graph_run(args):
    """Run a graph manifest through the deterministic wave executor (foreground).

    Requires `--i-approve-manifest` (authorizes the AUTONOMOUS non-self-extension
    portion; every self-extension node STILL HALTs for a mandatory manual
    checkpoint). Pins the approved manifest's sha256 in the append-only ledger,
    records observability events, then executes. Reads + validates the manifest
    first (refuse on any error)."""
    fmt_json = getattr(args, "format", "text") == "json"
    kdir = resolve_knowledge_dir()
    if not kdir:
        sys.stderr.write("graph run: no knowledge dir (set "
                         "AGENTWARE_KNOWLEDGE_DIR).\n")
        return 1
    name = args.name
    manifest, merr = _graph_load_manifest(kdir, name)
    if merr:
        sys.stderr.write("graph run: %s\n" % merr)
        return 1
    errs = graph_manifest_errors(manifest, kdir=kdir)
    if errs:
        sys.stderr.write("graph run: refusing invalid manifest %s:\n  - %s\n"
                         % (name, "\n  - ".join(errs)))
        return 1
    # Approval gate — the operator's ONE authorization for the autonomous portion.
    if not getattr(args, "i_approve_manifest", False):
        sys.stderr.write(
            "graph run: refusing to run %s without --i-approve-manifest.\n"
            "  The manifest is the ONE content-pinned artifact you approve; its "
            "sha256 is recorded and resume refuses on mutation. Self-extension "
            "nodes remain mandatory manual checkpoints you run yourself.\n" % name)
        return 1
    now = getattr(args, "now", None) or utc_now_iso()
    sha = _graph_manifest_sha256(manifest)
    _graph_ledger_append(kdir, name, {
        "event": "approval", "graph": name, "manifest_sha256": sha,
        "actor": "operator", "ts": now})
    _graph_emit_metric(kdir, {
        "event": "graph", "graph": name, "phase": "approve",
        "manifest_sha256": sha, "ts": now})
    result = _graph_execute(
        kdir, manifest, now,
        concurrency=getattr(args, "concurrency", None),
        node_timeout_hours=getattr(args, "node_timeout", None),
        deadline_hours=getattr(args, "deadline", GRAPH_DEADLINE_HOURS))
    result["graph"] = name
    _graph_finish(kdir, name, result, now)
    return _graph_report(result, fmt_json)


def cmd_graph_resume(args):
    """Resume a partially-executed graph. RECOMPUTES purely from live plan states +
    metrics + lane registry (NO authoritative run-state file): DONE nodes are
    skipped (`cmd_plan_claim` refuses to restart a terminal plan as the backstop),
    DONE_UNFINALIZED finalized idempotently, live RUNNING re-attached, FAILED parked
    unless `--retry`. Recomputes the manifest sha256 and REFUSES on mutation
    (`halt manifest-mutated`)."""
    fmt_json = getattr(args, "format", "text") == "json"
    kdir = resolve_knowledge_dir()
    if not kdir:
        sys.stderr.write("graph resume: no knowledge dir (set "
                         "AGENTWARE_KNOWLEDGE_DIR).\n")
        return 1
    name = args.name
    manifest, merr = _graph_load_manifest(kdir, name)
    if merr:
        sys.stderr.write("graph resume: %s\n" % merr)
        return 1
    errs = graph_manifest_errors(manifest, kdir=kdir)
    if errs:
        sys.stderr.write("graph resume: refusing invalid manifest %s:\n  - %s\n"
                         % (name, "\n  - ".join(errs)))
        return 1
    approved = _graph_last_approval(kdir, name)
    if not approved:
        sys.stderr.write(
            "graph resume: %s was never approved — run `graph run "
            "--i-approve-manifest` first.\n" % name)
        return 1
    now = getattr(args, "now", None) or utc_now_iso()
    current = _graph_manifest_sha256(manifest)
    if current != approved:
        result = _graph_result("halt", "manifest-mutated", node=name)
        result["graph"] = name
        _graph_finish(kdir, name, result, now)
        if fmt_json:
            json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stderr.write(
                "graph resume: %s manifest MUTATED after approval (approved %s, "
                "current %s) — refusing (halt manifest-mutated). Re-approve with "
                "`graph run --i-approve-manifest` if the change is intended.\n"
                % (name, approved[:12], current[:12]))
        return 1
    result = _graph_execute(
        kdir, manifest, now,
        concurrency=getattr(args, "concurrency", None),
        node_timeout_hours=getattr(args, "node_timeout", None),
        deadline_hours=getattr(args, "deadline", GRAPH_DEADLINE_HOURS),
        retry=getattr(args, "retry", False))
    result["graph"] = name
    _graph_finish(kdir, name, result, now)
    return _graph_report(result, fmt_json)


def cmd_graph_status(args):
    """Deterministic read-only status view: a per-node table (sorted by feature) +
    summary counts + the approved-vs-current sha256 pin. `--format json` is the
    exact body the read-only dashboard endpoint (Task 8) serves."""
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    manifest, err = _graph_load_manifest(kdir, args.name)
    if err:
        sys.stderr.write("graph status: %s\n" % err)
        return 1
    now = getattr(args, "now", None) or utc_now_iso()
    payload = _graph_status_payload(
        kdir, args.name, manifest, now,
        node_timeout_hours=getattr(args, "node_timeout", None))
    if getattr(args, "format", "text") == "json":
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2,
                  sort_keys=True)
        sys.stdout.write("\n")
    else:
        print("graph %s: %d node(s), concurrency %d"
              % (payload["graph"], payload["node_count"], payload["concurrency"]))
        if payload["manifest_mutated"]:
            print("  ‼️  manifest MUTATED since approval (approved %s, current %s)"
                  % ((payload["approved_sha256"] or "?")[:12],
                     payload["manifest_sha256"][:12]))
        print("  %-26s %-9s %-7s %-11s %s"
              % ("NODE", "STATUS", "MERGED", "CHECKPOINT", "DETAIL"))
        for n in payload["nodes"]:
            print("  %-26s %-9s %-7s %-11s %s"
                  % (n["feature"], n["status"], "yes" if n["merged"] else "no",
                     n["checkpoint"], n["detail"] or ""))
    return 0


def cmd_graph_checkpoint(args):
    """Record an explicit checkpoint verdict on a graph node (append-only barrier).

    `graph checkpoint <name> <node> --verdict proceed|hold|abort-subtree
    --actor loop|operator`. A `manual` checkpoint (mandatory on every self-ext
    node) accepts ONLY an operator verdict. `abort-subtree` cancels the node's
    transitive successor set via the normal set-state matrix (operator actor +
    reason); a successor whose current state has no legal edge to `cancelled`
    (e.g. already `done`) is left untouched. Writes NOTHING on a refusal."""
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    name = args.name
    node = args.node
    verdict = getattr(args, "verdict", None)
    actor = getattr(args, "actor", "operator") or "operator"
    as_json = getattr(args, "format", "text") == "json"

    if verdict not in _GRAPH_CHECKPOINT_VERDICTS:
        sys.stderr.write("graph checkpoint: unknown verdict %r (one of: %s)\n"
                         % (verdict, ", ".join(_GRAPH_CHECKPOINT_VERDICTS)))
        return 1
    if actor not in ("loop", "operator"):
        sys.stderr.write("graph checkpoint: unknown actor %r (loop|operator)\n"
                         % (actor,))
        return 1

    manifest, err = _graph_load_manifest(kdir, name)
    if err:
        sys.stderr.write("graph checkpoint: %s\n" % err)
        return 1
    node_by_feat = {n["feature"]: n for n in (manifest.get("nodes") or [])
                    if isinstance(n, dict) and isinstance(n.get("feature"), str)}
    if node not in node_by_feat:
        sys.stderr.write("graph checkpoint: node %r is not in graph %r\n"
                         % (node, name))
        return 1
    policy = node_by_feat[node].get("checkpoint", "none")

    # A `manual` checkpoint accepts ONLY an operator verdict — the loop can never
    # clear a manual barrier (a self-ext node is operator-run under R-PKG-03).
    if policy == "manual" and actor != "operator":
        sys.stderr.write(
            "graph checkpoint: node %r carries a 'manual' checkpoint — only an "
            "operator may record a verdict (got actor %r); the loop can never "
            "clear a manual barrier\n" % (node, actor))
        return 1

    text, terr = _load_plan_tolerant(kdir, node)
    if terr is not None:
        sys.stderr.write("graph checkpoint: %s\n" % terr)
        return 1

    now_iso = getattr(args, "now", None) or _now_iso()
    entry = "- %s %s by %s (graph %s, checkpoint %s)" % (
        now_iso, verdict, actor, name, policy)
    lines = _append_checkpoint_log(text.splitlines(), entry)
    out = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    _atomic_write(_plan_path(kdir, node), out)

    cancelled, refused = [], []
    if verdict == "abort-subtree":
        reason = "graph %s checkpoint abort-subtree from %s" % (name, node)
        for f in _graph_transitive_successors(manifest, node):
            ns = argparse.Namespace(
                feature=f, state="cancelled", actor="operator", reason=reason,
                superseded_by=None, handle=getattr(args, "handle", None),
                host=getattr(args, "host", None), now=now_iso, format="text")
            # An illegal cancel edge (e.g. a `done` successor) writes to stderr and
            # returns non-zero; suppress that noise and record it as not-cancelled.
            try:
                with open(os.devnull, "w") as _dn, \
                        contextlib.redirect_stdout(_dn), \
                        contextlib.redirect_stderr(_dn):
                    rc = cmd_plan_set_state(ns)
            except Exception:
                rc = 1
            (cancelled if rc == 0 else refused).append(f)

    if as_json:
        json.dump({"ok": True, "graph": name, "node": node, "verdict": verdict,
                   "actor": actor, "checkpoint": policy, "cancelled": cancelled,
                   "not_cancelled": refused}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("graph checkpoint '%s' node '%s': %s (by %s)"
              % (name, node, verdict, actor))
        if verdict == "abort-subtree":
            print("  cancelled subtree: %s" % (", ".join(cancelled) or "(none)"))
            if refused:
                print("  left as-is (no legal cancel edge): %s"
                      % ", ".join(refused))
    return 0


def cmd_graph_cancel(args):
    """Cancel graph nodes through the six-state plan machine (operator actor).

    `graph cancel <name> [--node <feature>]`. With `--node`, cancels that node AND
    its transitive successor set (across the combined logical ∪ resource graph);
    with no `--node`, cancels EVERY node in the graph. Each cancel routes through
    the SOLE state writer `cmd_plan_set_state` (operator actor), which enforces the
    legal-edge matrix — a node with no legal edge to `cancelled` (e.g. already
    `done`/terminal) is left untouched, never force-written. Writes NOTHING to the
    manifest (the manifest stays immutable). Failure is never written to a plan
    state; this is the explicit operator-driven cancellation path."""
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    name = args.name
    as_json = getattr(args, "format", "text") == "json"
    manifest, err = _graph_load_manifest(kdir, name)
    if err:
        sys.stderr.write("graph cancel: %s\n" % err)
        return 1
    features = [n["feature"] for n in (manifest.get("nodes") or [])
                if isinstance(n, dict) and isinstance(n.get("feature"), str)]
    target = getattr(args, "node", None)
    if target:
        if target not in features:
            sys.stderr.write("graph cancel: node %r is not in graph %r\n"
                             % (target, name))
            return 1
        victims = [target] + _graph_transitive_successors(manifest, target)
    else:
        victims = sorted(features)
    now_iso = getattr(args, "now", None) or _now_iso()
    reason = "graph %s cancel%s" % (
        name, (" from %s" % target) if target else "")
    cancelled, refused = [], []
    for f in victims:
        ns = argparse.Namespace(
            feature=f, state="cancelled", actor="operator", reason=reason,
            superseded_by=None, handle=getattr(args, "handle", None),
            host=getattr(args, "host", None), now=now_iso, format="text")
        try:
            with open(os.devnull, "w") as _dn, \
                    contextlib.redirect_stdout(_dn), \
                    contextlib.redirect_stderr(_dn):
                rc = cmd_plan_set_state(ns)
        except Exception:
            rc = 1
        (cancelled if rc == 0 else refused).append(f)
    if as_json:
        json.dump({"ok": True, "graph": name, "node": target,
                   "cancelled": cancelled, "not_cancelled": refused},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("graph cancel '%s': cancelled %s"
              % (name, ", ".join(cancelled) or "(none)"))
        if refused:
            print("  left as-is (no legal cancel edge): %s"
                  % ", ".join(refused))
    return 0


def cmd_graph_teardown(args):
    """Tear down the graph's provisioned lanes by delegating to `fanout teardown`.

    `graph teardown <name> [--force] [--dry-run]`. Only the manifest nodes that
    ACTUALLY have a registered lane are handed to the shipped `cmd_fanout_teardown`
    (which removes merged lanes' worktrees + branches and REFUSES unmerged/dirty
    lanes without --force — R-GIT-02). Adds no new teardown logic; reuses the
    fanout safety gate verbatim so no unmerged work is ever silently discarded."""
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    name = args.name
    as_json = getattr(args, "format", "text") == "json"
    manifest, err = _graph_load_manifest(kdir, name)
    if err:
        sys.stderr.write("graph teardown: %s\n" % err)
        return 1
    features = [n["feature"] for n in (manifest.get("nodes") or [])
                if isinstance(n, dict) and isinstance(n.get("feature"), str)]
    reg, rerr = load_lanes()
    if rerr:
        sys.stderr.write("graph teardown: %s\n" % rerr)
        return 1
    lanes = [f for f in sorted(features) if f in (reg.get("lanes") or {})]
    if not lanes:
        if as_json:
            json.dump({"ok": True, "graph": name, "torn_down": [],
                       "detail": "no provisioned lanes"}, sys.stdout,
                      ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            print("graph teardown '%s': no provisioned lanes to remove" % name)
        return 0
    ns = argparse.Namespace(
        features=lanes, repo=getattr(args, "repo", None),
        kb_repo=getattr(args, "kb_repo", None),
        integration_branch=getattr(args, "integration_branch", None),
        force=getattr(args, "force", False),
        dry_run=getattr(args, "dry_run", False),
        format=getattr(args, "format", "text"))
    return cmd_fanout_teardown(ns)


_EXPORTS = [
    "cmd_graph_run",
    "cmd_graph_resume",
    "cmd_graph_status",
    "cmd_graph_checkpoint",
    "cmd_graph_cancel",
    "cmd_graph_teardown",
]

__all__ = list(_EXPORTS)
