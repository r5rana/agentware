"""agentware_lib.core — stable, feature-independent primitives.

Owns: the core LAYER — small, high-fan-in primitives (paths, config, gitio, frontmatter).
Does NOT own: any KB/retrieval/ops/feature behaviour — see the sibling layers (knowledge/, retrieval/, ops/, features/) or catalog/modules.json.
Layer deps: core -> stdlib only; nothing here may import from knowledge/, retrieval/, ops/, features/ or cli.

Nothing here may import from knowledge/, retrieval/, ops/, features/ or cli
(asserted by tests/test_module_boundaries.py). These are the small, high-fan-in
building blocks the rest of the package depends on; keeping them at the bottom
of the layer graph is what lets an agent see what is "core" without reading
anything.
"""

__all__ = []
