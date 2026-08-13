"""agentware_lib.features.fanoutmq -- the fanout MERGE-QUEUE + TEARDOWN half: serial gated integration of finished lanes onto an integration branch, lane teardown, and the `fanout` parser registrar (layer: features).

AGGREGATOR ONLY. This package holds NO logic: it re-imports the public names of
its three domain submodules so every pre-split call site — which reaches the
module under an alias (`import agentware_lib.features.fanoutmq as fanoutmq`) —
keeps resolving unchanged. The `fanout` verb itself is declared in `mqteardown`'s
`__verbs__`, beside the registrar that serves it.

Owns: nothing but the re-export surface. The union of the submodules' `_EXPORTS`
equals the pre-split module's `_EXPORTS` EXACTLY — 16 names, none added, none
dropped.
Does NOT own: any predicate, integrator, command or registrar — each lives in
its own submodule:

  * `mqbranch`    — the git BRANCH + lane-repo primitives: `_is_ancestor`,
                    `_ensure_integration_branch`, `_merge_in_progress`,
                    `_union_resolve_conflict`, `_lane_repo_args`,
                    `_lanes_common_repo`. The package's deepest leaf.
  * `mqintegrate` — the INTEGRATION engine: the mq constants
                    (`FANOUT_APPEND_ONLY_PATHS` / `FANOUT_DEFAULT_GATES`), the
                    package + KB serial integrators, the outcome renderer and
                    `cmd_fanout_merge_queue`.
  * `mqteardown`  — the lane TEARDOWN path (`_teardown_blocker` /
                    `_remove_worktree` / `cmd_fanout_teardown`) and the `fanout`
                    parser registrar `register_fanout`.

Layer deps: none of its own — it only re-exports its submodules, whose layer
deps are declared in their own docstrings (core + knowledge + ops downward, plus
the same-layer features.fanout sibling). The package's internal graph is
acyclic: `mqbranch` imports no sibling, `mqintegrate` imports `mqbranch`, and
`mqteardown` sits on top importing both.
"""

from .mqbranch import (
    _ensure_integration_branch,
    _is_ancestor,
    _lane_repo_args,
    _lanes_common_repo,
    _merge_in_progress,
    _union_resolve_conflict,
)
from .mqintegrate import (
    FANOUT_APPEND_ONLY_PATHS,
    FANOUT_DEFAULT_GATES,
    _mq_emit,
    _mq_integrate_kb,
    _mq_integrate_package,
    cmd_fanout_merge_queue,
)
from .mqteardown import (
    _remove_worktree,
    _teardown_blocker,
    cmd_fanout_teardown,
    register_fanout,
)
