"""agentware_lib.retrieval — the deterministic ranking + evaluation MOAT (layer: retrieval).

Owns: the retrieval LAYER — the shared tokenizer + corpus assembly, BM25 scoring +
postings cache, RRF hybrid fusion, the embedder/vector cache, the ACR provenance
prior, skill pinning, ranking metrics, and the eval/ledger/gate/scorecard/SWE
surfaces (split across corpus.py, bm25.py, hybrid.py, … as wave-3 seams land).
Does NOT own: operational surfaces (ops/), optional features (features/), the
knowledge system of record (knowledge/) — imported LAZILY where a rebuild must
refresh derived caches — or the core primitives it builds on (core/).
Layer deps: retrieval -> core (+ knowledge lazily); nothing above may be imported
here, and nothing in core/ or knowledge/ may import from this layer (asserted by
the per-wave forward-edge / global-resolve gates).
"""

__all__ = []
