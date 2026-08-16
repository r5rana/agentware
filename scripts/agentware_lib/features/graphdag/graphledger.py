"""agentware_lib.features.graphdag.graphledger -- the append-only RUN LEDGER, status payload and reports (layer: features).

Owns: the ledger location and its append-only writer/reader (`_graph_ledger_path`
/ `_graph_ledger_append` / `_graph_ledger_read`), the approval lookup
(`_graph_last_approval`), the metric emitter (`_graph_emit_metric`), the
checkpoint log appender (`_append_checkpoint_log`), the machine-readable status
(`_graph_status_payload`), the human report (`_graph_report`) and the per-node
result shape (`_graph_result`).
Does NOT own: the manifest -> graphdag.graphmanifest; node state derivation ->
graphdag.graphstate; the tick loop -> graphdag.graphexec; the verbs ->
graphdag.graphrun.

APPEND-ONLY, BECAUSE THE HISTORY IS THE ONLY WITNESS
----------------------------------------------------
The manifest holds the graph's CURRENT shape. It cannot answer "what happened",
and after a crash-and-resume that is the only question worth asking. The ledger
is append-only for the obvious reason: a run history that can be rewritten is a
run history that cannot be used to reconstruct what a node actually did, and the
scheduler's own resume logic is one of its readers.

`_graph_last_approval` reads back the most recent checkpoint approval, which is
what lets a resume know whether the human gate it is about to pass was already
passed -- and, together with the manifest hash, whether it was passed for THIS
graph or a different one.

STATUS AND REPORT ARE THE SAME FACTS, TWICE
-------------------------------------------
`_graph_status_payload` is for machines (the loop, tests, `--format json`);
`_graph_report` is for a human. They are deliberately built from the same derived
state rather than computed separately -- two renderers over one source can drift
in presentation, but two independent computations drift in FACT, and then the
operator and the automation disagree about whether a run is finished.

Layer deps: features -> features (`graphconst`, `graphmanifest`, `graphstate`),
features -> ops (`metrics` for the emitter). Writes only the ledger, the
checkpoint log, and metrics -- never the manifest.
Determinism: the payload is built from derived state and sorted, so two calls
over unchanged evidence produce identical output (INV-1).
"""

import json
import os
import sys
import time

from agentware_lib.features.graphdag.graphconst import (
    GRAPH_DEFAULT_CONCURRENCY,
    GRAPH_NODE_DONE,
    GRAPH_NODE_FAILED,
    GRAPH_NODE_HALT,
    _GRAPH_CHECKPOINT_VERDICTS,
    _PLAN_CHECKPOINT_LOG_HEADING,
)
from agentware_lib.features.fanout import load_lanes
from agentware_lib.features.graphdag.graphmanifest import (
    _graph_dir,
    _graph_manifest_sha256,
)
from agentware_lib.features.graphdag.graphstate import _graph_node_status
from agentware_lib.ops.metrics.metricsemit import _read_metrics_jsonl


def _graph_result(status, reason, *, waves=0, dispatched=None, merged=None,
                  node=None, offenders=None, pending=None):
    """Uniform executor result envelope."""
    return {
        "status": status, "reason": reason, "waves": waves,
        "dispatched": list(dispatched or []), "merged": list(merged or []),
        "node": node, "offenders": list(offenders or []),
        "pending": list(pending or []),
    }


def _graph_ledger_path(kdir, name):
    return os.path.join(_graph_dir(kdir, name), "graph-run.jsonl")


def _graph_ledger_append(kdir, name, record):
    """Append one NON-authoritative event to the graph-run ledger (append-only).
    The ledger records approvals + run outcomes for observability; it is NEVER the
    source of truth for node state (that is always RECOMPUTED). Best-effort."""
    path = _graph_ledger_path(kdir, name)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass
    return record


def _graph_ledger_read(kdir, name):
    """Read the append-only ledger tolerantly -> list of dict events ([] when
    absent/unreadable; malformed lines skipped). Read-only."""
    path = _graph_ledger_path(kdir, name)
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except OSError:
        return []
    return out


def _graph_last_approval(kdir, name):
    """The most-recent `approval` event's pinned sha256, or None when the graph was
    never approved (re-approval supersedes: the LAST approval wins). Read-only."""
    sha = None
    for e in _graph_ledger_read(kdir, name):
        if e.get("event") == "approval" \
                and isinstance(e.get("manifest_sha256"), str):
            sha = e["manifest_sha256"]
    return sha


def _graph_emit_metric(kdir, record):
    """Append an additive `graph`/`graph_node` observability event to
    logs/metrics.jsonl. Both event types are in derive_iteration_costs' skip-set so
    they never corrupt the per-iteration cost derivation, and they carry no
    `terminal` discriminator so `_graph_raw_terminal_outcome` ignores them.
    Best-effort (observability, never fatal)."""
    path = os.path.join(kdir, "logs", "metrics.jsonl")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


def _graph_status_payload(kdir, name, manifest, now, node_timeout_hours=None):
    """A DETERMINISTIC read-only status snapshot: per-node live status (sorted by
    feature) + summary counts + the approved-vs-current sha256 pin. A pure function
    of (manifest, live plan+metric+lane states, `now`) that does NOT embed `now`,
    so the read-only dashboard endpoint (Task 8) can byte-match
    `graph status --format json`."""
    reg, _rerr = load_lanes()
    events = _read_metrics_jsonl(kdir)
    node_by_feat = {n["feature"]: n for n in (manifest.get("nodes") or [])
                    if isinstance(n, dict) and isinstance(n.get("feature"), str)}
    features = sorted(node_by_feat)
    nodes, counts = [], {}
    for f in features:
        node = node_by_feat[f]
        r = _graph_node_status(kdir, node, now,
                               node_timeout_hours=node_timeout_hours,
                               lanes=reg, events=events)
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        nodes.append({
            "feature": f,
            "status": r["status"],
            "plan_state": r["plan_state"],
            "detail": r["detail"],
            "disposition": r["disposition"],
            "merged": r["merged"],
            "lane_outcome": r["lane_outcome"],
            "checkpoint": node.get("checkpoint", "none"),
            "self_extension": bool(node.get("self_extension")),
        })
    approved = _graph_last_approval(kdir, name)
    current = _graph_manifest_sha256(manifest)
    return {
        "graph": name,
        "concurrency": manifest.get("concurrency", GRAPH_DEFAULT_CONCURRENCY),
        "node_count": len(features),
        "approved_sha256": approved,
        "manifest_sha256": current,
        "manifest_mutated": bool(approved) and approved != current,
        "nodes": nodes,
        "summary": counts,
    }


def _graph_report(result, fmt_json):
    """Print a run/resume result and return the process exit code (0 iff complete).
    A self-extension HALT prints the exact operator-run command (R-PKG-03)."""
    if fmt_json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("graph run: %s (%s) — %d wave(s), %d dispatched, %d merged"
              % (result["status"], result["reason"], result["waves"],
                 len(result["dispatched"]), len(result["merged"])))
        reason = result.get("reason") or ""
        if reason.startswith("awaiting-manual-selfext:"):
            node = result.get("node")
            print("  ▶ self-extension node reached — the graph HALTED. Run it "
                  "YOURSELF under the normal gated loop (R-PKG-03), then resume:")
            print("      ./agentware.sh %s" % node)
            print("      scripts/agentware graph resume %s"
                  % (result.get("graph") or "<graph>"))
    return 0 if result["status"] == "complete" else 1


def _append_checkpoint_log(lines, entry):
    """Append `entry` under the append-only `## Checkpoint log` section (created at
    end-of-file when absent). Mirrors `_append_status_log`; the `graph checkpoint`
    verb is its SOLE writer. Returns the mutated list."""
    idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == _PLAN_CHECKPOINT_LOG_HEADING:
            idx = i
            break
    if idx is None:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append(_PLAN_CHECKPOINT_LOG_HEADING)
        lines.append("")
        lines.append(entry)
        return lines
    end = len(lines)
    for j in range(idx + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    k = end - 1
    while k > idx and lines[k].strip() == "":
        k -= 1
    lines.insert(k + 1, entry)
    return lines


_EXPORTS = [
    "_graph_ledger_path",
    "_graph_ledger_append",
    "_graph_ledger_read",
    "_graph_last_approval",
    "_graph_emit_metric",
    "_append_checkpoint_log",
    "_graph_status_payload",
    "_graph_report",
    "_graph_result",
]

__all__ = list(_EXPORTS)
