"""agentware_lib.features.graphdag.graphcmd -- the `graph` REGISTRAR wiring all thirteen subverbs (layer: features).

Owns: `register_graph` -- the parser wiring, and nothing else. It is a separate
module from both handler halves because it is the ONE place the verb's public
surface is declared, and it must import from both `graphedit` (the seven
authoring subverbs) and `graphrun` (the six execution ones). Homing it in either
half would give that half an import edge to the other purely for registration,
and the authoring/execution separation -- authoring can never reach the executor
-- is the property worth protecting.
Does NOT own: any handler. `graph new` / `add-node` / `rm-node` / `add-edge` /
`rm-edge` / `show` / `validate` -> graphdag.graphedit; `run` / `resume` /
`status` / `checkpoint` / `cancel` / `teardown` -> graphdag.graphrun; the
manifest, topology, state, lanes, ledger and tick loop -> their own modules.

Layer deps: features -> features (`graphconst` for the bounds and vocabulary the
help strings quote, `graphedit`, `graphrun`), features -> ops
(`plangraph._CHECKPOINT_POLICIES` -- the checkpoint policy names come from the
PLAN's vocabulary, so `graph add-node --checkpoint` and the plan that declares it
cannot offer different sets). Declared in this module's `__verbs__` and
resolved from there by the parser assembler, so `register_graph` is deliberately
absent from `_EXPORTS`.
"""

import argparse

from agentware_lib.features.graphdag.graphconst import (
    GRAPH_CONCURRENCY_MAX,
    GRAPH_CONCURRENCY_MIN,
    GRAPH_DEADLINE_HOURS,
    GRAPH_DEFAULT_CONCURRENCY,
    GRAPH_NODE_TIMEOUT_HOURS,
    _GRAPH_CHECKPOINT_VERDICTS,
    _GRAPH_EDGE_KINDS,
)
from agentware_lib.ops.plangraph import _CHECKPOINT_POLICIES
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


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("graph", 4400, "register_graph"),
)


def register_graph(sub, fmt_parent):
    # graph ------------------------------------------------------------------
    # Top-level DAG orchestrator (sibling of `fanout`/`plan`): schedules the
    # per-feature loop over the fanout lanes + gated merge-queue. Mirrors the
    # `p_fanout`/`fanout_sub` wiring and shares `fmt_parent` so every subcommand
    # gets `--format {text,json}`.
    p_graph = sub.add_parser(
        "graph",
        help="deterministic DAG orchestration over fanout lanes + gated merge")
    graph_sub = p_graph.add_subparsers(
        dest="graph_command", metavar="<subcommand>")
    graph_sub.required = True

    p_g_new = graph_sub.add_parser(
        "new", parents=[fmt_parent], help="create an empty graph manifest")
    p_g_new.add_argument("name", help="graph name (matches the fanout feature RE)")
    p_g_new.add_argument("--concurrency", type=int, default=None,
                         help="max concurrent lanes (default %d, ceiling %d)"
                              % (GRAPH_DEFAULT_CONCURRENCY, GRAPH_CONCURRENCY_MAX))
    p_g_new.add_argument("--force", action="store_true",
                         help="overwrite an existing manifest")
    p_g_new.set_defaults(func=cmd_graph_new)

    p_g_an = graph_sub.add_parser(
        "add-node", parents=[fmt_parent],
        help="add a plan as a graph node (self_extension + write-set computed)")
    p_g_an.add_argument("name", help="graph name")
    p_g_an.add_argument("feature", help="plan feature slug (the node)")
    p_g_an.add_argument("--checkpoint", choices=_CHECKPOINT_POLICIES, default=None,
                        help="checkpoint policy (default: read from the plan.md)")
    p_g_an.set_defaults(func=cmd_graph_add_node)

    p_g_rn = graph_sub.add_parser(
        "rm-node", parents=[fmt_parent],
        help="remove a node (and every edge touching it)")
    p_g_rn.add_argument("name", help="graph name")
    p_g_rn.add_argument("feature", help="node to remove")
    p_g_rn.set_defaults(func=cmd_graph_rm_node)

    p_g_ae = graph_sub.add_parser(
        "add-edge", parents=[fmt_parent], help="add a dependency edge")
    p_g_ae.add_argument("name", help="graph name")
    p_g_ae.add_argument("src", help="predecessor node")
    p_g_ae.add_argument("dst", help="successor node")
    p_g_ae.add_argument("--kind", choices=_GRAPH_EDGE_KINDS, default="logical",
                        help="edge kind (default: logical)")
    p_g_ae.set_defaults(func=cmd_graph_add_edge)

    p_g_re = graph_sub.add_parser(
        "rm-edge", parents=[fmt_parent], help="remove a dependency edge")
    p_g_re.add_argument("name", help="graph name")
    p_g_re.add_argument("src", help="predecessor node")
    p_g_re.add_argument("dst", help="successor node")
    p_g_re.set_defaults(func=cmd_graph_rm_edge)

    p_g_show = graph_sub.add_parser(
        "show", parents=[fmt_parent],
        help="render the manifest (text/dot/mermaid/--format json)")
    p_g_show.add_argument("name", help="graph name")
    p_g_show.add_argument("--render", choices=("text", "dot", "mermaid"),
                          default="text", help="text renderer (default: text)")
    p_g_show.set_defaults(func=cmd_graph_show)

    p_g_val = graph_sub.add_parser(
        "validate", parents=[fmt_parent],
        help="validate the manifest (refuses cycle/dangling/self-ext-checkpoint)")
    p_g_val.add_argument("name", help="graph name")
    p_g_val.set_defaults(func=cmd_graph_validate)
    _register_graph_execution(graph_sub, fmt_parent)


def _register_graph_execution(graph_sub, fmt_parent):
    """The six EXECUTION subverbs: run / resume / status / checkpoint / cancel /
    teardown.

    Split from the authoring half purely to hold the per-function line cap — the
    monolith declared all thirteen inline in one 158-line block. The split follows
    the same seam the HANDLERS do (graphedit vs graphrun), so the registrar's shape
    matches the module boundary it wires rather than cutting across it.
    """

    p_g_run = graph_sub.add_parser(
        "run", parents=[fmt_parent],
        help="run the graph (requires --i-approve-manifest)")
    p_g_run.add_argument("name", help="graph name")
    p_g_run.add_argument("--i-approve-manifest", action="store_true",
                         dest="i_approve_manifest",
                         help="authorize the AUTONOMOUS non-self-extension portion")
    p_g_run.add_argument("--concurrency", type=int, default=None,
                         help="override the manifest concurrency")
    p_g_run.add_argument("--node-timeout", type=float, default=None,
                         dest="node_timeout",
                         help="per-node wall-clock watchdog hours (default %d)"
                              % GRAPH_NODE_TIMEOUT_HOURS)
    p_g_run.add_argument("--deadline", type=float, default=GRAPH_DEADLINE_HOURS,
                         help="whole-run deadline hours (default %d)"
                              % GRAPH_DEADLINE_HOURS)
    p_g_run.add_argument("--now", default=None,
                         help="inject the clock (ISO) for byte-identical replay")
    p_g_run.set_defaults(func=cmd_graph_run)

    p_g_res = graph_sub.add_parser(
        "resume", parents=[fmt_parent],
        help="resume a partially-run graph (recomputation; refuses on hash drift)")
    p_g_res.add_argument("name", help="graph name")
    p_g_res.add_argument("--concurrency", type=int, default=None,
                         help="override the manifest concurrency")
    p_g_res.add_argument("--node-timeout", type=float, default=None,
                         dest="node_timeout",
                         help="per-node wall-clock watchdog hours")
    p_g_res.add_argument("--deadline", type=float, default=GRAPH_DEADLINE_HOURS,
                         help="whole-run deadline hours")
    p_g_res.add_argument("--retry", action="store_true",
                         help="re-offer a FAILED node exactly once (R-FAIL-04)")
    p_g_res.add_argument("--now", default=None,
                         help="inject the clock (ISO) for byte-identical replay")
    p_g_res.set_defaults(func=cmd_graph_resume)

    p_g_st = graph_sub.add_parser(
        "status", parents=[fmt_parent],
        help="deterministic per-node status table (--format json for the API)")
    p_g_st.add_argument("name", help="graph name")
    p_g_st.add_argument("--node-timeout", type=float, default=None,
                        dest="node_timeout",
                        help="per-node watchdog hours for the status view")
    p_g_st.add_argument("--now", default=None,
                        help="inject the clock (ISO) for a pinned status render")
    p_g_st.set_defaults(func=cmd_graph_status)

    p_g_cp = graph_sub.add_parser(
        "checkpoint", parents=[fmt_parent],
        help="record a checkpoint verdict (manual barrier is operator-only)")
    p_g_cp.add_argument("name", help="graph name")
    p_g_cp.add_argument("node", help="node the verdict applies to")
    p_g_cp.add_argument("--verdict", choices=_GRAPH_CHECKPOINT_VERDICTS,
                        required=True, help="proceed|hold|abort-subtree")
    p_g_cp.add_argument("--actor", choices=("loop", "operator"),
                        default="operator",
                        help="who records the verdict (default: operator)")
    p_g_cp.add_argument("--handle", default=None, help="actor handle (optional)")
    p_g_cp.add_argument("--host", default=None, help="actor host (optional)")
    p_g_cp.add_argument("--now", default=None, help="inject the clock (ISO)")
    p_g_cp.set_defaults(func=cmd_graph_checkpoint)

    p_g_can = graph_sub.add_parser(
        "cancel", parents=[fmt_parent],
        help="cancel a node+subtree (or the whole graph) via the state machine")
    p_g_can.add_argument("name", help="graph name")
    p_g_can.add_argument("--node", default=None,
                         help="cancel this node + its subtree (default: all nodes)")
    p_g_can.add_argument("--handle", default=None, help="actor handle (optional)")
    p_g_can.add_argument("--host", default=None, help="actor host (optional)")
    p_g_can.add_argument("--now", default=None, help="inject the clock (ISO)")
    p_g_can.set_defaults(func=cmd_graph_cancel)

    p_g_td = graph_sub.add_parser(
        "teardown", parents=[fmt_parent],
        help="tear down the graph's lanes (delegates to the gated fanout teardown)")
    p_g_td.add_argument("name", help="graph name")
    p_g_td.add_argument("--repo", default=None, help="package repo override")
    p_g_td.add_argument("--kb-repo", default=None, dest="kb_repo",
                        help="KB repo override")
    p_g_td.add_argument("--integration-branch", default=None,
                        dest="integration_branch",
                        help="integration branch for the merged check")
    p_g_td.add_argument("--force", action="store_true",
                        help="remove even unmerged/dirty lanes (R-GIT-02 override)")
    p_g_td.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="print what would be removed; remove nothing")
    p_g_td.set_defaults(func=cmd_graph_teardown)



_EXPORTS = []

__all__ = list(_EXPORTS)
