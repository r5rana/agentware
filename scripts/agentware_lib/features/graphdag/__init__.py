"""agentware_lib.features.graphdag -- the DAG SCHEDULER: manifest, topology, lanes, tick loop, and the `graph` verb's thirteen subverbs (layer: features).

AGGREGATOR ONLY. This package holds NO logic: it re-imports the public names of
its eleven domain submodules so every pre-split call site — which reached these
symbols as bare globals in the monolith and now resolves them as `CLI.<name>`
through the entrypoint facade's re-export block — keeps resolving unchanged, and
so every existing monkeypatch target keeps working.

Owns: nothing but the re-export surface. The union of the submodules' `_EXPORTS`
is exactly the set of graph symbols the monolith carried — none added, none
dropped.
Does NOT own: any constant, predicate, handler or registrar — each lives in its
own submodule:

  * `graphconst`    — every CONSTANT the scheduler agrees on: the six node
                      states, the four checkpoint dispositions and their verdict
                      table, the edge kinds, the size/concurrency/deadline/tick
                      bounds, the detail codes, the kill-switch variable names,
                      the lane env allowlist and the kernel serialize token.
                      Pure data; the package's deepest leaf alongside `graphcanon`.
  * `graphcanon`    — `_graph_canonical`, and nothing else. Its own module
                      because `graphmanifest` hashes the canonical form while
                      `graphrender` renders from it, and `_graph_save` calls the
                      renderer — so homing it in either would close a cycle.
  * `graphrender`   — the three human views (markdown / DOT / Mermaid).
  * `graphmanifest` — the only DURABLE truth: paths, load, build, the full
                      validator, the durable write, the commit and the hash.
  * `graphtopo`     — topological order, the recomputed ready frontier, the
                      blast-radius walk, and the WRITE-SET machinery that turns
                      declared writes into implicit serialization edges.
  * `graphstate`    — a node's state DERIVED from the lane's on-disk evidence,
                      plus the load-bearing test and the finalize reconciliation.
  * `graphlane`     — lane env (an allowlist), spawn, dispatch, prepare, and the
                      GATED merge.
  * `graphledger`   — the append-only run ledger, the approval lookup, metrics,
                      the checkpoint log, the status payload and the report.
  * `graphexec`     — the tick loop and the terminal bookkeeping.
  * `graphedit`     — the seven AUTHORING subverbs (new / add-node / rm-node /
                      add-edge / rm-edge / show / validate).
  * `graphrun`      — the six EXECUTION subverbs (run / resume / status /
                      checkpoint / cancel / teardown).
  * `graphcmd`      — `register_graph`, resolved by the assembler through
                      `_PARSER_REGISTRATION` (so it is NOT in `_EXPORTS`).

THE SEAM THAT DEFINES THIS PACKAGE
----------------------------------
`graphedit` has no import path to `graphexec` or `graphlane`. Authoring cannot
reach the executor, and that is enforced by module structure rather than by
comment: an operator running `graph validate` or `graph show` should never have
to wonder whether the command they typed will start work. `graphcmd` is a
separate module precisely to keep it that way — the registrar must import both
handler halves, and homing it in either would give that half an import edge to
the other purely for registration.

Layer deps: none of its own — it only re-exports its submodules, whose layer deps
are declared in their own docstrings (core + knowledge + ops downward, plus the
same-layer `features.fanout` / `features.fanoutmq` siblings whose lane primitives
this package REUSES rather than reimplements, so a graph lane and a fanout lane
are the same kind of thing on disk). The package's internal graph is acyclic:
`graphconst` and `graphcanon` are the leaves, `graphcmd` sits at the top.
"""

from agentware_lib.features.graphdag.graphcanon import _graph_canonical
from agentware_lib.features.graphdag.graphconst import (
    GRAPH_BLOCK_SUBTREE,
    GRAPH_CONCURRENCY_MAX,
    GRAPH_CONCURRENCY_MIN,
    GRAPH_CONTINUE,
    GRAPH_DEADLINE_HOURS,
    GRAPH_DEFAULT_CONCURRENCY,
    GRAPH_DETAIL_AWAITING_MERGE,
    GRAPH_DETAIL_DEGENERATE,
    GRAPH_DETAIL_DONE_UNFINALIZED,
    GRAPH_DETAIL_FAILED_WATCHDOG,
    GRAPH_DETAIL_PLAN_MISSING,
    GRAPH_HALT,
    GRAPH_KERNEL_SERIALIZE_TOKEN,
    GRAPH_KILL_SWITCH_VARS,
    GRAPH_LANE_ENV_ALLOW,
    GRAPH_MANIFEST_SCHEMA,
    GRAPH_MAX_NODES,
    GRAPH_NODE_BLOCKED,
    GRAPH_NODE_DONE,
    GRAPH_NODE_FAILED,
    GRAPH_NODE_HALT,
    GRAPH_NODE_PENDING,
    GRAPH_NODE_RUNNING,
    GRAPH_NODE_TIMEOUT_HOURS,
    GRAPH_PAUSE,
    GRAPH_TICK_CEILING,
    _GRAPH_CHECKPOINT_VERDICTS,
    _GRAPH_CONTAINED_FAIL_OUTCOMES,
    _GRAPH_DEP_SATISFIED,
    _GRAPH_DISPATCHABLE,
    _GRAPH_DISPOSITION_TABLE,
    _GRAPH_EDGE_KINDS,
    _PLAN_CHECKPOINT_LOG_HEADING,
)
from agentware_lib.features.graphdag.graphrender import (
    _render_graph_dot,
    _render_graph_md,
    _render_graph_mermaid,
)
from agentware_lib.features.graphdag.graphmanifest import (
    _graph_build,
    _graph_commit,
    _graph_dir,
    _graph_load_manifest,
    _graph_manifest_path,
    _graph_manifest_sha256,
    _graph_md_path,
    _graph_new_manifest,
    _graph_node_self_ext,
    _graph_save,
    graph_manifest_errors,
)
from agentware_lib.features.graphdag.graphtopo import (
    _graph_derive_resource_edges,
    _graph_manifest_write_sets,
    _graph_ready_set,
    _graph_transitive_successors,
    _graph_write_set_touches_kernel,
    _write_sets_overlap,
    _write_token_matches,
    topo_order,
)
from agentware_lib.features.graphdag.graphstate import (
    _graph_disposition,
    _graph_finalize_unfinalized,
    _graph_lane_record,
    _graph_node_is_load_bearing,
    _graph_node_status,
    _graph_raw_terminal_outcome,
)
from agentware_lib.features.graphdag.graphlane import (
    _graph_bootstrap_integration,
    _graph_drive_lane,
    _graph_lane_env,
    _graph_merge_gates,
    _graph_merge_lane,
    _graph_orchestrator_env_blockers,
    _graph_prepare_lane,
    _graph_select_dispatch,
    _graph_should_reprovision,
    _graph_spawn,
)
from agentware_lib.features.graphdag.graphledger import (
    _append_checkpoint_log,
    _graph_emit_metric,
    _graph_last_approval,
    _graph_ledger_append,
    _graph_ledger_path,
    _graph_ledger_read,
    _graph_report,
    _graph_result,
    _graph_status_payload,
)
from agentware_lib.features.graphdag.graphexec import _graph_execute, _graph_finish
from agentware_lib.features.graphdag.graphedit import (
    cmd_graph_add_edge,
    cmd_graph_add_node,
    cmd_graph_new,
    cmd_graph_rm_edge,
    cmd_graph_rm_node,
    cmd_graph_show,
    cmd_graph_validate,
)
from agentware_lib.features.graphdag.graphrun import (
    cmd_graph_cancel,
    cmd_graph_checkpoint,
    cmd_graph_resume,
    cmd_graph_run,
    cmd_graph_status,
    cmd_graph_teardown,
)
