"""agentware_lib.retrieval.hybrid — RRF hybrid (BM25 + embedding) retrieval (layer: retrieval).

Owns: the Reciprocal Rank Fusion hybrid retriever (Phase 5.1, Mode B core) — the RRF +
cosine determinism constants (_RRF_K/_EMBED_POOL/_COSINE_ROUND/_RRF_ROUND), the shared
canonical tie-break sort/rank helpers (_canonical_sort/_canonical_rank), the
fixed-precision cosine similarity (_cosine), the cache-reusing in-memory corpus embedder
(_embed_corpus_vectors), the RRF fusion over 1-based INTEGER ranks (_rrf_fuse_scored/
_rrf_fuse), and the Mode-B retrievers that fuse the BM25 lexical ranking with an embedding
cosine ranking and gracefully fall back to plain BM25 when no local embedder is present
(retrieve_bm25_embed_scored/retrieve_bm25_embed).
Does NOT own: the BM25 lexical ranking it fuses (retrieval.bm25, reached via the _bm25
module alias); the shared tokenizer + corpus assembly it ranks over (retrieval.corpus, via
_corpus); the lazy embedder loader + derived vector cache it consumes (retrieval.vectorcache,
via _vectorcache); the ACR provenance prior + skill pinning (later wave-3 seams); the
recall/eval handlers + the `retrieve` dispatcher that call these (still resident — they
reach retrieve_bm25_embed/_scored via the by-name facade bind on the entrypoint).
Layer deps: retrieval -> core (agentware_lib.core.routing as routing, for the embed-model
spec) + retrieval -> retrieval (agentware_lib.retrieval.corpus/bm25/vectorcache as the
module aliases _corpus/_bm25/_vectorcache, so the global-resolve gate never walks a foreign
sibling function held in this module's __dict__) + stdlib math. Imports nothing above
retrieval; nothing in core/ or knowledge/ imports from here.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

INV-1: fusion is over 1-based INTEGER ranks (RRF) plus fixed-precision-rounded cosine /
fused scores, NEVER raw float scores — no float drift, and identical inputs at a pinned
model yield byte-identical ordering under the SAME canonical tie-break (score desc ->
created desc -> id asc) as every other strategy. Embeddings only RANK candidates — they
NEVER author memory content (the no-hallucination moat). INV-2: read-only — recall/eval
never write the persisted vector cache (`index rebuild` is its sole writer); a missing or
broken embedder degrades GRACEFULLY to deterministic Mode A (plain BM25) and never crashes.
INV-6: no static import of an optional embedding backend appears here — the embedder is
reached lazily via retrieval.vectorcache, so the stdlib-only static-import surface stays green.
"""

import math

import agentware_lib.core.routing as routing
from agentware_lib.retrieval import corpus as _corpus
from agentware_lib.retrieval import bm25 as _bm25
from agentware_lib.retrieval import vectorcache as _vectorcache


_RRF_K = 60          # standard RRF damping constant (over 1-based integer ranks)
_EMBED_POOL = 200    # deep candidate-pool depth taken from EACH ranker before RRF
_COSINE_ROUND = 9    # fixed precision for cosine similarity (determinism guard)
_RRF_ROUND = 12      # fixed precision for the fused RRF score (determinism guard)


def _canonical_sort(scored):
    """In-place canonical tie-break sort of [(entry, score), ...]; returns it.

    score desc -> created desc -> id asc, via a stable multi-pass sort (lowest
    priority first, highest last) — byte-identical to `retrieve_bm25`'s ordering.
    Shared by the id-only `_canonical_rank` and the scored RRF/recall paths so
    every strategy orders identically (INV-1).
    """
    scored.sort(key=lambda es: es[0].get("id") or "")                    # id asc
    scored.sort(key=lambda es: es[0].get("created") or "", reverse=True)  # created
    scored.sort(key=lambda es: es[1], reverse=True)                      # score desc
    return scored


def _canonical_rank(scored):
    """Sort [(entry, score), ...] by the canonical tie-break -> list of ids."""
    return [e.get("id") for (e, _s) in _canonical_sort(scored)]


def _cosine(a, b):
    """Deterministic cosine similarity rounded to fixed precision.

    Returns 0.0 if either vector is missing/empty, dims differ, or either has
    zero norm. Inputs are already embedder-rounded; the fixed-precision round here
    is a belt-and-suspenders determinism guard before ranks are taken (INV-1).
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return round(dot / math.sqrt(na * nb), _COSINE_ROUND)


def _embed_corpus_vectors(kdir, corpus, embedder, model):
    """Return {id: vector} for `corpus`, reusing the derived cache where valid.

    Reuses a persisted vector whose entry id + content fingerprint are unchanged
    (the same key build_vector_cache writes), and embeds the rest IN-MEMORY in one
    batched call. NEVER writes the cache (INV-2): the in-memory embeds cover the
    ephemeral LongMemEval session corpora (which have no persisted vectors) and
    any KB entry not yet in the cache. Deterministic given a pinned model.
    """
    cache = _vectorcache.load_vector_cache(kdir, model) or {}
    cached_vectors = cache.get("vectors") or {}
    vectors = {}
    to_embed_ids, to_embed_txt = [], []
    for entry, doc_text in corpus:
        eid = entry.get("id")
        if eid is None:
            continue
        fp = _vectorcache._corpus_fingerprint(doc_text)
        hit = cached_vectors.get(eid)
        if isinstance(hit, dict) and hit.get("fp") == fp \
                and isinstance(hit.get("v"), list):
            vectors[eid] = list(hit["v"])
        else:
            to_embed_ids.append(eid)
            to_embed_txt.append(doc_text)
    if to_embed_txt:
        embedded = embedder.embed(to_embed_txt)
        if len(embedded) != len(to_embed_txt):
            raise ValueError(
                "embedder returned %d vectors for %d inputs"
                % (len(embedded), len(to_embed_txt)))
        for eid, vec in zip(to_embed_ids, embedded):
            vectors[eid] = list(vec)
    return vectors


def _rrf_fuse_scored(rankings, data, k=_RRF_K, pool=_EMBED_POOL):
    """Reciprocal Rank Fusion -> [(entry, fused_score), ...] in canonical order.

    score(d) = sum_r 1/(k + rank_r(d)) over each ranker `r`, using 1-based INTEGER
    ranks within the top-`pool` of that ranker (the deep candidate pool). The
    fused float score is rounded (determinism guard) and the canonical tie-break
    (created desc, id asc) resolves exact ties — so identical inputs yield
    byte-identical ordering (INV-1). Returns the scored tuples so callers that
    need the fused score (e.g. `recall`) get it; `_rrf_fuse` drops it to ids.
    """
    by_id = {}
    for e in data.get("entries") or []:
        if isinstance(e, dict) and e.get("id") is not None:
            by_id.setdefault(e.get("id"), e)
    scores = {}
    for ranking in rankings:
        for rank, eid in enumerate(ranking[:pool], start=1):
            scores[eid] = scores.get(eid, 0.0) + 1.0 / (k + rank)
    scored = [(by_id.get(eid, {"id": eid}), round(s, _RRF_ROUND))
              for eid, s in scores.items()]
    return _canonical_sort(scored)


def _rrf_fuse(rankings, data, k=_RRF_K, pool=_EMBED_POOL):
    """RRF of ranked id-lists -> fused ids (canonical order). See `_rrf_fuse_scored`."""
    return [e.get("id") for (e, _s) in _rrf_fuse_scored(rankings, data, k, pool)]


def retrieve_bm25_embed_scored(kdir, data, query, top_k=None, as_of=None):
    """Hybrid Mode-B retrieval -> [(entry, fused_score), ...] in canonical order.

    1. BM25 lexical ranking (positive scores only, canonical tie-break) — this is
       byte-identical to `retrieve_bm25` (Mode A stays the lexical floor).
    2. Embedder gate: load the LOCAL embedder; if ABSENT or it errors, return the
       plain BM25 (entry, score) list (GRACEFUL Mode-A fallback — never crash).
    3. Embedding cosine ranking over the corpus (cached + in-memory vectors).
    4. RRF over 1-based integer ranks of (1) and (3) across a DEEP candidate pool,
       re-sorted under the canonical tie-break; returns the fused score.

    The scored core behind `retrieve_bm25_embed` (ids) AND `recall_ranked`
    (entry+score+doc_text). Embeddings RANK only (no LLM, no authored content; the
    moat). Deterministic given a pinned model + cached vectors. `as_of` is accepted
    for a uniform strategy signature (the embedding signal is date-independent).
    Read-only (INV-2): never writes the persisted vector cache.
    """
    corpus = _corpus.build_corpus(kdir, data)
    bm25_sorted = _canonical_sort(
        [(e, s) for (e, s) in _bm25.bm25_scores(corpus, _corpus.tokenize(query)) if s > 0.0])

    def _fallback():
        return bm25_sorted if top_k is None else bm25_sorted[:top_k]

    embedder = _vectorcache.load_embedder()
    if embedder is None:
        return _fallback()

    model = routing.resolve_embed_model()
    try:
        corpus_vectors = _embed_corpus_vectors(kdir, corpus, embedder, model)
        qvecs = embedder.embed([query])
        qvec = qvecs[0] if qvecs else None
    except Exception:
        # Any embedding failure => graceful Mode-A fallback; never crash recall.
        return _fallback()
    if not qvec:
        return _fallback()

    bm25_ranking = [e.get("id") for (e, _s) in bm25_sorted]
    embed_scored = [(e, _cosine(qvec, corpus_vectors.get(e.get("id"))))
                    for (e, _doc) in corpus if e.get("id") in corpus_vectors]
    embed_ranking = _canonical_rank(embed_scored)

    fused = _rrf_fuse_scored([bm25_ranking, embed_ranking], data)
    return fused if top_k is None else fused[:top_k]


def retrieve_bm25_embed(kdir, data, query, top_k=None, as_of=None):
    """Hybrid Mode-B retrieval -> ranked entry ids. See `retrieve_bm25_embed_scored`."""
    return [e.get("id") for (e, _s)
            in retrieve_bm25_embed_scored(kdir, data, query, top_k, as_of)]


# Explicit export list (public AND underscore-private) — the M3/_EXPORTS convention:
# the manifest anchor-symbol set (tests/_manifest_gen.py) and the granular surface-floor
# denominator (tests/unit/retrieval/test_hybrid.py). Like corpus/bm25/vectorcache (and
# unlike the loop-bound core/knowledge modules), hybrid is NOT in the entrypoint _reexport
# loop: these names reach the CLI facade (CLI.<name> for SourceFileLoader tests + the
# dashboard) via the by-name `from agentware_lib.retrieval.hybrid import (...)` at the top
# of scripts/agentware. Keep THIS list and that import tuple in sync (both name all 12).
_EXPORTS = [
    "_RRF_K",
    "_EMBED_POOL",
    "_COSINE_ROUND",
    "_RRF_ROUND",
    "_canonical_sort",
    "_canonical_rank",
    "_cosine",
    "_embed_corpus_vectors",
    "_rrf_fuse_scored",
    "_rrf_fuse",
    "retrieve_bm25_embed_scored",
    "retrieve_bm25_embed",
]
