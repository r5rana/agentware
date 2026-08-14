"""agentware_lib.ops — operational surfaces (layer: ops).

Owns: the ops LAYER — telemetry (session parse + metrics + loop analytics/trace/
health/alerts), org/fleet events + org/template lint, team registry, failure-ladder
reporting, KB freshness, steering/release/plan lints, plan authoring, audit checks,
and the KB git workflow.
Does NOT own: core primitives (paths/config/gitio/frontmatter -> core/), the KB
system of record (index/curation/graph/privacy/ingest -> knowledge/), the ranking +
evaluation MOAT (-> retrieval/), or the optional/pluggable capabilities
(fanout/mcp/dream/skills -> features/). See catalog/modules.json.
Layer deps: ops -> core + knowledge + retrieval (downward only); nothing here may
import from features/ or cli (asserted by tests/test_module_boundaries.py).
"""

__all__ = []
