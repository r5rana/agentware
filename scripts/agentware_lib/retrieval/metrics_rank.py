"""agentware_lib.retrieval.metrics_rank - hand-computable ranking metrics + the gold-set loader/scorer (layer: retrieval).

Owns: the exact, hand-computable ranking metrics (recall_at_k / precision_at_k /
_dcg / ndcg_at_k / reciprocal_rank), the gold-set loader (load_gold - parse + validate
a [{query, expected_ids}] file) and the deterministic gold-set scorer evaluate (run a
gold set through a strategy and compute aggregate + per-query metrics). A module-private
_median (the p50 helper evaluate uses for latency) lives here too - a behaviorally
identical duplicate of the ops-metrics _median that owns the entrypoint-level name.
Does NOT own: the retrieval strategies it scores (the retrieve dispatcher ->
agentware_lib.retrieval.skillpin via _skillpin; corpus assembly + estimate_tokens ->
agentware_lib.retrieval.corpus via _corpus; the answer-age freshness helper ->
agentware_lib.retrieval.acr via _acr); the LongMemEval / gold-fixture suites, the ACR
win gate, the benchmark + default-flip ledgers, the regression + A/B gates, the
scorecard and the SWE bench (later wave-3 seams / still resident, reached via the
by-name facade); the eval/recall CLI handlers + build_recall_payload (resident; they
call evaluate/load_gold as bare names via the facade bind on the entrypoint).
Layer deps: retrieval -> core (agentware_lib.core.dates, eager) + retrieval ->
retrieval (agentware_lib.retrieval.corpus/acr/skillpin as the module aliases
_corpus/_acr/_skillpin, so the global-resolve gate never walks a foreign sibling
function held in this module's __dict__). Imports nothing above retrieval; nothing in
core/ or knowledge/ imports from here.
Fan-in: 5 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

INV-1: metrics are deterministic, hand-rolled + stdlib-only (no LLM, no embeddings,
no network); at a FIXED as_of evaluate's ranking + every metric value is byte-identical
across runs - the ONLY non-deterministic fields are latency_ms* (time.perf_counter),
which downstream diffing strips. INV-2: read-only - evaluate/load_gold never mutate the
entry, index.json or the KB.
"""

import json
import math
import time

import agentware_lib.core.dates as dates
from agentware_lib.retrieval import corpus as _corpus
from agentware_lib.retrieval import acr as _acr
from agentware_lib.retrieval import skillpin as _skillpin


def recall_at_k(ranked, relevant, k):
    """|relevant ∩ top-k| / |relevant|. 0.0 when there are no relevant items."""
    relevant = set(relevant)
    if not relevant:
        return 0.0
    hits = sum(1 for r in ranked[:k] if r in relevant)
    return hits / len(relevant)


def precision_at_k(ranked, relevant, k):
    """|relevant ∩ top-k| / k (standard precision@k; k is the denominator)."""
    if k <= 0:
        return 0.0
    relevant = set(relevant)
    hits = sum(1 for r in ranked[:k] if r in relevant)
    return hits / k


def _dcg(ranked, relevant, k):
    relevant = set(relevant)
    total = 0.0
    for i, r in enumerate(ranked[:k]):
        if r in relevant:
            total += 1.0 / math.log2(i + 2)  # gain 1, discount log2(rank+1)
    return total


def ndcg_at_k(ranked, relevant, k):
    """Binary-gain nDCG@k = DCG@k / ideal-DCG@k. 0.0 when ideal DCG is 0."""
    relevant = set(relevant)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    if idcg == 0:
        return 0.0
    return _dcg(ranked, relevant, k) / idcg


def reciprocal_rank(ranked, relevant):
    """1 / (rank of first relevant item) over the FULL ranking; 0.0 if none."""
    relevant = set(relevant)
    for i, r in enumerate(ranked):
        if r in relevant:
            return 1.0 / (i + 1)
    return 0.0


def _median(values):
    """Deterministic p50: middle of an odd list, mean of the two middles else."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def load_gold(path):
    """Load a gold set: list of {query, expected_ids[]}. Returns (items, error)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None, "gold set not found: %s" % path
    except json.JSONDecodeError as exc:
        return None, "invalid JSON in gold set %s: %s" % (path, exc)
    if not isinstance(data, list):
        return None, "gold set must be a JSON list of {query, expected_ids}"
    items = []
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            return None, "gold entry #%d is not an object" % i
        q = row.get("query")
        exp = row.get("expected_ids")
        if not isinstance(q, str) or not q.strip():
            return None, "gold entry #%d missing a non-empty 'query'" % i
        if not isinstance(exp, list) or not exp:
            return None, "gold entry #%d missing a non-empty 'expected_ids' list" % i
        items.append({"query": q, "expected_ids": [str(x) for x in exp]})
    if not items:
        return None, "gold set is empty"
    return items, None


def evaluate(kdir, data, gold, strategy, top_k, as_of=None):
    """Run the gold set through a strategy and compute graded metrics.

    Returns a dict with aggregate `metrics` + `per_query` rows. Read-only.
    Latency uses time.perf_counter and is the ONLY non-deterministic field; the
    ranking itself is deterministic (INV-1). `as_of` is threaded to `retrieve` so
    the `bm25+acr` strategy decays freshness at a PINNED date (reproducible).
    """
    corpus = _corpus.build_corpus(kdir, data)
    corpus_fp = _corpus.eval_corpus_fingerprint(corpus)
    docs_by_id = {e.get("id"): doc for e, doc in corpus}
    entries_by_id = {e.get("id"): e for e, doc in corpus}
    as_of_for_age = as_of or dates._today()
    per_query = []
    recalls, precisions, ndcgs, rrs, latencies, footprints = [], [], [], [], [], []
    for row in gold:
        query = row["query"]
        relevant = set(row["expected_ids"])
        t0 = time.perf_counter()
        ranked = _skillpin.retrieve(kdir, data, query, strategy, top_k=None, as_of=as_of)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        topk = ranked[:top_k]
        rec = recall_at_k(ranked, relevant, top_k)
        prec = precision_at_k(ranked, relevant, top_k)
        nd = ndcg_at_k(ranked, relevant, top_k)
        rr = reciprocal_rank(ranked, relevant)
        footprint = sum(_corpus.estimate_tokens(docs_by_id.get(eid, "")) for eid in topk)
        recalls.append(rec)
        precisions.append(prec)
        ndcgs.append(nd)
        rrs.append(rr)
        latencies.append(latency_ms)
        footprints.append(footprint)
        per_query.append({
            "query": query,
            "expected_ids": sorted(relevant),
            "ranked": topk,
            "recall_at_k": rec,
            "precision_at_k": prec,
            "ndcg_at_k": nd,
            "reciprocal_rank": rr,
            "latency_ms": latency_ms,
            "context_tokens": footprint,
            # Age (days) of the correct answer as-of the PINNED date — drives the
            # OLD vs RECENT stratum in the ACR win gate (Phase 3.2). Pure
            # date-math, deterministic at a fixed `as_of`; None ⇒ excluded.
            "answer_age_days": _acr.answer_age_days(entries_by_id, relevant,
                                               as_of_for_age),
        })
    n = len(gold)
    mean = lambda xs: (sum(xs) / len(xs)) if xs else 0.0
    return {
        "strategy": strategy,
        "top_k": top_k,
        "num_queries": n,
        "corpus_fingerprint": corpus_fp,
        # Number of scored corpus entries (additive, append-only — same seam as
        # corpus_fingerprint). Lets /api/scaling plot Recall@k vs corpus size and
        # compute a meaningful slope. Legacy rows lacking it report N unknown.
        "corpus_size": len(corpus),
        "metrics": {
            "recall_at_k": mean(recalls),
            "precision_at_k": mean(precisions),
            "ndcg_at_k": mean(ndcgs),
            "mrr": mean(rrs),
            "latency_ms_mean": mean(latencies),
            "latency_ms_p50": _median(latencies),
            "context_tokens_mean": mean(footprints),
        },
        "per_query": per_query,
    }


_EXPORTS = [
    "recall_at_k",
    "precision_at_k",
    "_dcg",
    "ndcg_at_k",
    "reciprocal_rank",
    "load_gold",
    "evaluate",
]
