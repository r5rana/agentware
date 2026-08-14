"""agentware_lib.knowledge — the knowledge-base system of record (layer: knowledge).

Owns: the knowledge LAYER — index CRUD/validate/rebuild, the declared-edge graph,
attach conformance, personal-data scan, and the learn/decide/ingest/features/
worklog curation surfaces (split across index.py + curation.py).
Does NOT own: retrieval/ranking (retrieval/), operational surfaces (ops/),
optional features (features/), or the core primitives it builds on (core/).
Layer deps: knowledge -> core only (retrieval is imported LAZILY where a rebuild
must refresh the derived caches; nothing above core is imported eagerly).

The knowledge layer sits directly above core: it may import core.* downward, and
nothing in core/ may import from here (asserted by tests/test_module_boundaries.py
+ the per-wave forward-edge / global-resolve gates).
"""

__all__ = []
