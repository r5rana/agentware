"""agentware_lib.features -- optional/pluggable capabilities (layer: features).

Owns: the features LAYER -- fanout lanes + merge queue, the MCP server, the dream
nightly loop + scheduler, and skill list/search/add/remove/validate + proposal
lifecycle. Feature modules MAY import any layer below (core/knowledge/retrieval/
ops); nothing below may import them at module level.
Does NOT own: core primitives (-> core/), the KB system of record (-> knowledge/),
the ranking + evaluation MOAT (-> retrieval/), or the operational surfaces
(telemetry/org/fleet/lints/gates/plan/audit/kb-git -> ops/). See catalog/modules.json.
Layer deps: features -> core + knowledge + retrieval + ops (downward only); nothing
here may be imported by a lower layer at module level. Any ops->features need (the
audit<->dream cycle) is a FUNCTION-LOCAL import (asserted by
tests/test_module_boundaries.py + tests/_ast_forward_edges.py).
"""

__all__ = []
