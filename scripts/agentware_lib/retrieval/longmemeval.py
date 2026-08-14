"""agentware_lib.retrieval.longmemeval - the LongMemEval-S public benchmark scorer (layer: retrieval).

Owns: the COMPARABLE public benchmark scorer (LongMemEval-S, arXiv:2410.10813) -
the frozen-dataset constants (_LME_DEFAULT_REL / _LME_DET_SAMPLE), the official
abstention detector (is_longmemeval_abstention - the `_abs` question_id suffix),
the session-text flattener (longmemeval_session_text), the session-granularity
synthetic-corpus builder (longmemeval_corpus_data - one corpus entry per haystack
session, the retrieval unit) and the strategy-agnostic session-level scorer
(evaluate_longmemeval - Recall@5 / nDCG@k / MRR / precision@k over the gold answer
sessions, abstention EXCLUDED from the recall aggregate and reported separately).
Does NOT own: the ranking metrics it calls (recall_at_k / precision_at_k /
ndcg_at_k / reciprocal_rank + the p50 _median -> agentware_lib.retrieval.metrics_rank
via _metrics_rank); the retrieval strategies it scores (the retrieve dispatcher ->
agentware_lib.retrieval.skillpin via _skillpin); the corpus fingerprint ->
agentware_lib.retrieval.corpus via _corpus; the own gold-fixture suite, the ACR win
gate, the benchmark + default-flip ledgers, the regression + A/B gates, the scorecard
and the SWE bench (later wave-3 seams / still resident, reached via the by-name facade);
the eval CLI handler (cmd_eval, now agentware_lib.retrieval.evalcmd, wave-3 seam 16b;
it reads _LME_DEFAULT_REL via a by-name import and calls evaluate_longmemeval via the
_longmemeval alias).
Layer deps: retrieval -> retrieval (agentware_lib.retrieval.corpus/skillpin/metrics_rank
as the module aliases _corpus/_skillpin/_metrics_rank, so the global-resolve gate never
walks a foreign sibling function held in this module's __dict__). Imports nothing above
retrieval, nothing in core/, and nothing in knowledge/; nothing in core/ or knowledge/
imports from here.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

INV-1: the scorer is deterministic, hand-rolled + stdlib-only (no LLM, no embeddings,
no network); at a FIXED as_of the ranking + every metric value is byte-identical across
runs (a cheap re-score of the first _LME_DET_SAMPLE answerable questions surfaces this as
determinism_ok) - the ONLY non-deterministic field is latency_ms* (time.perf_counter),
which downstream diffing strips. INV-2: read-only - it builds temp in-memory corpora and
never mutates the entry, index.json or the KB.
"""

import json
import os
import time

from agentware_lib.retrieval import corpus as _corpus
from agentware_lib.retrieval import skillpin as _skillpin
from agentware_lib.retrieval import metrics_rank as _metrics_rank


_LME_DEFAULT_REL = os.path.join(
    "benchmarks", "longmemeval", "longmemeval_s_cleaned.json")
_LME_DET_SAMPLE = 3  # # of questions re-scored to assert byte-identical ordering


def is_longmemeval_abstention(question_id):
    """True for the official LongMemEval abstention questions.

    Abstention questions carry an `_abs` suffix on `question_id` AND a planted
    distractor `answer_session_ids` that IS in the haystack but does not answer
    the question — so Recall@k is undefined for them. Detect by the suffix (the
    official convention), NEVER by "no gold id" (they have one).
    """
    return str(question_id).endswith("_abs")


def longmemeval_session_text(session):
    """Flatten one LongMemEval session (a list of {role, content} turns) to text.

    Joins the substantive `content` of each turn with newlines — that is what
    BM25 (and later the embedder) rank on. Pure + deterministic; tolerant of
    malformed turns (skips non-dict / empty entries).
    """
    parts = []
    for turn in session or []:
        if isinstance(turn, dict):
            content = turn.get("content")
            if content:
                parts.append(str(content))
    return "\n".join(parts)


def longmemeval_corpus_data(session_ids, sessions):
    """Build a synthetic index-`data` dict: ONE entry per haystack session.

    The session is the retrieval unit (validity fix #1). Each entry carries the
    session id and its full flattened text inline (in `summary`), so the EXISTING
    strategy-agnostic retrieve()/build_corpus() path scores it with NO file I/O,
    NO network and NO LLM. Deterministic; read-only (INV-2).
    """
    entries = []
    for sid, sess in zip(session_ids or [], sessions or []):
        entries.append({"id": str(sid),
                        "summary": longmemeval_session_text(sess)})
    return {"entries": entries}


def evaluate_longmemeval(kdir, dataset_path, strategy, top_k, as_of=None,
                         limit=None):
    """Score a retrieval `strategy` on LongMemEval-S at SESSION granularity.

    For every question: build a session-level temp corpus (longmemeval_corpus_data),
    run retrieve(strategy) over it, and score Recall@k / nDCG@k / MRR / precision@k
    on the gold evidence session id(s) (`answer_session_ids`). Aggregates over all
    ANSWERABLE questions and per `question_type` category. Abstention questions
    (`_abs` suffix) are EXCLUDED from the recall aggregate and reported separately
    (count + per-category breakdown) — never crashed on, never silently mis-scored.

    Deterministic given a PINNED `as_of` (INV-1): a cheap re-score of the first
    `_LME_DET_SAMPLE` answerable questions confirms byte-identical ordering and is
    surfaced as `determinism_ok`. Read-only (INV-2); no network/LLM/embeddings.
    `limit` (testing/dev) scores only the first N questions of the file.
    """
    with open(dataset_path, "r", encoding="utf-8") as f:
        items = json.load(f)
    if not isinstance(items, list):
        raise ValueError("longmemeval dataset must be a JSON list of questions")
    if limit is not None:
        items = items[:limit]

    # Whole-corpus fingerprint over the FROZEN dataset actually scored (the
    # `(question_id, canonical-question-json)` pairs). LongMemEval is already
    # drift-free (frozen file), but recording the fingerprint keeps the invariant
    # that EVERY ledger row carries one, and keeps suites comparable only to runs
    # over the identical frozen slice.
    _lme_corpus = [({"id": str(it.get("question_id", i))},
                    json.dumps(it, sort_keys=True, ensure_ascii=False))
                   for i, it in enumerate(items) if isinstance(it, dict)]
    corpus_fp = _corpus.eval_corpus_fingerprint(_lme_corpus)

    recalls, precisions, ndcgs, rrs, latencies = [], [], [], [], []
    per_cat = {}
    abstention = {"count": 0, "by_category": {}}
    det_ok, det_checked = True, 0

    for it in items:
        if not isinstance(it, dict):
            continue
        qid = it.get("question_id", "")
        category = it.get("question_type") or "unknown"
        if is_longmemeval_abstention(qid):
            abstention["count"] += 1
            abstention["by_category"][category] = (
                abstention["by_category"].get(category, 0) + 1)
            continue
        query = it.get("question") or ""
        synth = longmemeval_corpus_data(
            it.get("haystack_session_ids"), it.get("haystack_sessions"))
        gold = set(str(g) for g in (it.get("answer_session_ids") or []))
        t0 = time.perf_counter()
        ranked = _skillpin.retrieve(kdir, synth, query, strategy, top_k=None, as_of=as_of)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        if det_checked < _LME_DET_SAMPLE:
            ranked2 = _skillpin.retrieve(kdir, synth, query, strategy, top_k=None,
                               as_of=as_of)
            if ranked2[:top_k] != ranked[:top_k]:
                det_ok = False
            det_checked += 1
        rec = _metrics_rank.recall_at_k(ranked, gold, top_k)
        prec = _metrics_rank.precision_at_k(ranked, gold, top_k)
        nd = _metrics_rank.ndcg_at_k(ranked, gold, top_k)
        rr = _metrics_rank.reciprocal_rank(ranked, gold)
        recalls.append(rec)
        precisions.append(prec)
        ndcgs.append(nd)
        rrs.append(rr)
        latencies.append(latency_ms)
        c = per_cat.setdefault(
            category, {"recalls": [], "precisions": [], "ndcgs": [], "rrs": []})
        c["recalls"].append(rec)
        c["precisions"].append(prec)
        c["ndcgs"].append(nd)
        c["rrs"].append(rr)

    mean = lambda xs: (sum(xs) / len(xs)) if xs else 0.0
    per_category = {}
    for cat in sorted(per_cat):
        c = per_cat[cat]
        per_category[cat] = {
            "num_queries": len(c["recalls"]),
            "recall_at_k": mean(c["recalls"]),
            "precision_at_k": mean(c["precisions"]),
            "ndcg_at_k": mean(c["ndcgs"]),
            "mrr": mean(c["rrs"]),
        }
    return {
        "suite": "longmemeval",
        "strategy": strategy,
        "top_k": top_k,
        "num_queries": len(recalls),
        "corpus_fingerprint": corpus_fp,
        # Scored-corpus entry count (additive; mirrors the own-gold evaluate()
        # seam) so LongMemEval rows also carry corpus_size for /api/scaling.
        "corpus_size": len(_lme_corpus),
        "dataset_path": dataset_path,
        "as_of": as_of,
        "metrics": {
            "recall_at_k": mean(recalls),
            "precision_at_k": mean(precisions),
            "ndcg_at_k": mean(ndcgs),
            "mrr": mean(rrs),
            "latency_ms_mean": mean(latencies),
            "latency_ms_p50": _metrics_rank._median(latencies),
        },
        "per_category": per_category,
        "abstention": abstention,
        "determinism_ok": det_ok,
    }


_EXPORTS = [
    "_LME_DEFAULT_REL",
    "_LME_DET_SAMPLE",
    "is_longmemeval_abstention",
    "longmemeval_session_text",
    "longmemeval_corpus_data",
    "evaluate_longmemeval",
]
