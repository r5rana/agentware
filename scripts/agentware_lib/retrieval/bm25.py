"""agentware_lib.retrieval.bm25 — Okapi BM25 scoring + the derived postings cache (layer: retrieval).

Owns: the fixed Okapi BM25 parameters (_BM25_K1/_BM25_B), the in-memory index build
(_bm25_index) and live scorer (bm25_scores), and the DERIVED BM25 postings cache —
its path/version constants (BM25_CACHE_REL/BM25_CACHE_VERSION), builder
(build_bm25_cache), atomic writer (write_bm25_cache), loader (load_bm25_cache),
three-tier freshness check (bm25_cache_is_fresh) and its zero-body-read stat proxy
(_corpus_stat_signature), cache-backed scorer (bm25_scores_cached), the
positive-score gate (bm25_positive_scores), and the ranked BM25 recall strategy
(retrieve_bm25).
Does NOT own: the shared tokenizer + corpus assembly it scores over
(agentware_lib.retrieval.corpus — tokenize/build_corpus/eval_corpus_fingerprint); the
ACR provenance prior `acr` and the ACR-reranked strategy retrieve_bm25_acr, which
STAY RESIDENT in the entrypoint until the retrieval.acr seam lands (retrieve_bm25_acr
calls the still-unmoved `acr`); RRF hybrid fusion + the embedder/vector cache
(retrieval.hybrid / retrieval.vectorcache); index rebuild — the SOLE cache writer —
which calls write_bm25_cache (agentware_lib.knowledge.index, via the resident rebuild_kb).
Layer deps: retrieval -> retrieval (same layer: imports agentware_lib.retrieval.corpus
as the module alias _corpus — _corpus.build_corpus/eval_corpus_fingerprint/tokenize —
NOT by-name, so the global-resolve gate never walks a foreign function held in this
module's __dict__) + stdlib hashlib/json/math/os/tempfile. Imports
nothing above retrieval; nothing in core/ or knowledge/ imports from here.
Fan-in: 7 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

INV-1: ranking is hand-rolled, stdlib-only — NO LLM, NO embeddings, NO network, NO
wall-clock in scoring; the cache-backed scorer (bm25_scores_cached) is byte-identical
to the live scorer (bm25_scores). INV-2: read-only — recall/eval NEVER write the
cache; the SOLE writer is index rebuild. Missing/corrupt/stale cache => graceful
fallback to the live scan, so correctness never depends on the cache being present.
"""

from agentware_lib.retrieval.pinpolicy import (
    SKILL_PIN_FLOOR,
    _apply_skill_pin,
)

import hashlib
import json
import math
import os
import tempfile

from agentware_lib.retrieval import corpus as _corpus


# Derived BM25 postings cache (feature 260625-kb-cached-inverted-index): a
# gitignored, regenerable inverted index — NEVER source of truth, exactly like
# index.json / the vector cache. A single JSON file under `.cache/`, written ONLY
# by `index rebuild` (rebuild_kb, the sole writer, INV-2). Lets a query touch the
# postings for its query terms (O(Σ df(query-term))) instead of re-tokenizing the
# whole corpus + recomputing df/avgdl every query (O(N·doc_len)). Absence /
# corruption / version- or fingerprint-mismatch => recall silently falls back to
# the live scan, so correctness NEVER depends on the cache being present (INV-1
# byte-identity is enforced by a guard test, not assumed).
BM25_CACHE_REL = os.path.join(".cache", "bm25-postings.json")
# v3: the MERGED schema — content `corpus_fingerprint` as the sole freshness
# authority (the monotonic `generation` counter is retired: it goes stale-blind
# the moment an index authored elsewhere arrives with a coincident counter
# value) PLUS the `stat_signature` short-circuit that restores the O(1)-in-body-
# reads fast path when no fingerprint is stored.
#
# The number is deliberately one NEITHER tree has used: this tree wrote v2
# (generation-keyed) and the donor wrote v1 (fingerprint-only) to the SAME
# `.cache/bm25-postings.json` path, so reusing either number would let a
# foreign-schema cache load as valid — a v2 cache read as v3 would supply a
# `generation` this code never checks and no `stat_signature` at all. Bumping
# makes every pre-merge cache rejected by `load_bm25_cache` (=> live scan, the
# graceful path), which is correct rather than merely cautious.
BM25_CACHE_VERSION = 3


# --- BM25 deterministic ranking (Phase 1) ------------------------------------
# Fixed, documented Okapi BM25 parameters. Pinned for determinism; identical
# inputs yield byte-identical ordering (INV-1). No embeddings, no network.
_BM25_K1 = 1.5
_BM25_B = 0.75


def _bm25_index(corpus):
    """Build a BM25 index from [(entry, doc_text), ...] in corpus order.

    Returns (docs, df, avgdl): `docs` is [(entry, term_freqs, doc_len), ...] in
    corpus order, `df` maps term -> number of docs containing it, `avgdl` is the
    mean document length in tokens. Pure stdlib; deterministic.
    """
    docs = []
    df = {}
    total_len = 0
    for entry, text in corpus:
        toks = _corpus.tokenize(text)
        tf = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        docs.append((entry, tf, len(toks)))
        total_len += len(toks)
        for t in tf:
            df[t] = df.get(t, 0) + 1
    avgdl = (total_len / len(docs)) if docs else 0.0
    return docs, df, avgdl


def bm25_scores(corpus, query_tokens):
    """Return [(entry, score), ...] in corpus order, scored by Okapi BM25.

    idf = ln(1 + (N - df + 0.5)/(df + 0.5)) (always positive). Term contribution
    saturates via k1 and is length-normalized via b. Deterministic for identical
    input; a doc sharing no query term scores exactly 0.0.
    """
    docs, df, avgdl = _bm25_index(corpus)
    n = len(docs)
    q_terms = set(query_tokens)
    idf = {}
    for t in q_terms:
        n_t = df.get(t, 0)
        idf[t] = math.log(1.0 + (n - n_t + 0.5) / (n_t + 0.5))
    results = []
    for entry, tf, dl in docs:
        score = 0.0
        for t in q_terms:
            f = tf.get(t, 0)
            if f == 0:
                continue
            norm = 1.0 - _BM25_B + _BM25_B * (dl / avgdl if avgdl else 0.0)
            denom = f + _BM25_K1 * norm
            score += idf[t] * (f * (_BM25_K1 + 1.0)) / denom
        results.append((entry, score))
    return results


# --- Derived BM25 postings cache (feature 260625-kb-cached-inverted-index) -----
# A persistent inverted index derived from the SAME corpus `build_corpus`/
# `_bm25_index` produce, so the cache-backed scorer is byte-identical to
# `bm25_scores` (INV-1). The cache is a DERIVED artifact (C-1/C-3): regenerable
# from frontmatter+bodies, gitignored, NEVER source of truth, written ONLY by
# `index rebuild`/`rebuild_kb` (INV-2 read-only — recall/eval never write it).
# Missing/corrupt/stale => graceful fallback to the live scan. Schema (byte-stable,
# sorted keys):
#   {version, fingerprint, stat_signature, n, avgdl, k1, b,
#    doc_lens:{id:len}, df:{term:df}, postings:{term:[[id,tf], ...]}}
# `fingerprint` is the whole-corpus content hash (the freshness AUTHORITY);
# `stat_signature` is the zero-body-read proxy that short-circuits it. Both are
# stamped by the same `build_bm25_cache` call and both are REQUIRED by
# `load_bm25_cache`, so a payload carrying one without the other is foreign.


def bm25_cache_path(kdir):
    """Absolute path to the derived BM25 postings cache JSON under `.cache/`."""
    return os.path.join(kdir, BM25_CACHE_REL)


def _corpus_stat_signature(kdir, data):
    """A cheap corpus signature that reads ZERO entry bodies.

    Every input `build_corpus` feeds the fingerprint comes from exactly two
    places: the entry METADATA already in memory (title/summary/tags, plus the
    id/path that decide which body is read), and the body FILE at `entry.path`.
    So the signature hashes the canonical JSON of each entry — a superset of the
    corpus-bearing fields, deliberately, since an over-broad signature can only
    cost an extra hash, never a false-fresh — together with that file's
    (st_size, st_mtime_ns) from a single `os.stat`. No `open()`, no read, no
    hash of any body: this is O(entries) stats against O(corpus bytes) of reads.

    Used ONLY as a short-circuit BEFORE hashing (see `bm25_cache_is_fresh`), and
    only in the direction that is safe: signatures EQUAL => skip the whole-corpus
    re-read; signatures DIFFER => fall through and hash, so a stat that lies
    (git does not preserve mtime across a clone) costs a rebuild-check, never a
    wrong answer. The residual false-fresh window is a body rewritten to the
    SAME byte length within the same nanosecond timestamp, with the index
    metadata unchanged — strictly narrower than the `generation` counter this
    replaces, which never noticed an out-of-band body edit at all.

    Unstattable body (missing/permission) contributes the "-" sentinel rather
    than raising: absence is itself a state worth detecting, and it changes the
    signature the moment the file comes back. Read-only, deterministic, stdlib.
    """
    h = hashlib.sha256()
    entries = data.get("entries") if isinstance(data, dict) else None
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        try:
            meta = json.dumps(entry, sort_keys=True, ensure_ascii=False,
                              default=str)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            meta = repr(entry)
        rel = entry.get("path")
        stat_part = "-"
        if isinstance(rel, str) and rel:
            try:
                st = os.stat(os.path.join(kdir, rel))
                stat_part = "%d:%d" % (st.st_size, st.st_mtime_ns)
            except OSError:
                stat_part = "-"
        h.update(meta.encode("utf-8"))
        h.update(b"\x1f")
        h.update(stat_part.encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()


def build_bm25_cache(kdir, data):
    """Build the (pure) BM25 postings cache dict from the live corpus.

    Derives from the IDENTICAL `build_corpus` + `_bm25_index` the live scorer uses,
    so the persisted df/avgdl/n/doc_lens/postings reproduce `bm25_scores` exactly.
    The `fingerprint` is the deterministic whole-corpus content hash
    (`eval_corpus_fingerprint`) so ANY body/frontmatter/add/remove change
    invalidates the cache. Postings are ordered by doc_id and all dict keys are
    serialized sorted (see `write_bm25_cache`), so two builds over one KB state are
    byte-identical. Read-only.
    """
    corpus = _corpus.build_corpus(kdir, data)
    docs, df, avgdl = _bm25_index(corpus)
    fingerprint = _corpus.eval_corpus_fingerprint(corpus)
    doc_lens = {}
    postings = {}
    for entry, tf, dl in docs:
        eid = entry.get("id")
        if eid is None:
            continue
        doc_lens[eid] = dl
        for term, freq in tf.items():
            postings.setdefault(term, []).append([eid, freq])
    for term in postings:
        postings[term].sort(key=lambda p: p[0])  # stable: order by doc_id
    return {
        "version": BM25_CACHE_VERSION,
        "fingerprint": fingerprint,
        # The zero-body-read proxy for the SAME corpus state the fingerprint
        # above was computed over. Stamped in the same call, so the two can
        # never describe different states of the tree.
        "stat_signature": _corpus_stat_signature(kdir, data),
        "n": len(docs),
        "avgdl": avgdl,
        "k1": _BM25_K1,
        "b": _BM25_B,
        "doc_lens": doc_lens,
        "df": df,
        "postings": postings,
    }


def write_bm25_cache(kdir, cache):
    """Persist the postings cache deterministically (sorted keys, byte-stable).

    The SOLE writer is `rebuild_kb` (`index rebuild`). Creates `.cache/` as needed.
    ATOMIC (tempfile + fsync + os.replace, like save_index): a lock-free concurrent
    recall can NEVER observe a half-written cache — it sees either the previous
    complete cache or the new one, so a torn read can't be mistaken for fresh. A
    truncating `open(path, "w")` streams into the REAL file, so a reader that polls
    mid-write gets invalid JSON; that degrades to a live scan today only because
    `load_bm25_cache` swallows ValueError, and it destroys the cache outright on a
    crash instead of leaving it merely stale.
    """
    path = bm25_cache_path(kdir)
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".bm25.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Never leave a .bm25.*.tmp behind on ANY exit path, including
        # KeyboardInterrupt — it would sit inside the knowledge dir.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def load_bm25_cache(kdir):
    """Load the derived postings cache, or None if unusable.

    Graceful by contract: a missing file, corrupt JSON, non-dict payload, or a
    version mismatch ALL return None so the caller falls back to the live scan.
    Never raises, never mutates.
    """
    path = bm25_cache_path(kdir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(cache, dict):
        return None
    if cache.get("version") != BM25_CACHE_VERSION:
        return None
    for key in ("fingerprint", "stat_signature", "n", "avgdl", "doc_lens", "df",
                "postings"):
        if key not in cache:
            return None
    if not isinstance(cache.get("postings"), dict) \
            or not isinstance(cache.get("df"), dict) \
            or not isinstance(cache.get("doc_lens"), dict):
        return None
    return cache


def bm25_cache_is_fresh(cache, data, kdir):
    """True iff `cache` matches the LIVE KB CONTENT (no staleness/divergence).

    CONTENT is the authority here, not a counter. The retired alternative stamped
    a monotonic `generation` into both index.json and the cache and compared the
    two integers: O(1), but stale-BLIND exactly where a shared knowledge base
    diverges. A counter only orders writes made by ONE writer — the moment an
    index authored elsewhere arrives (a pull, a restored backup, a second
    teammate's shard) its counter is an unrelated sequence, and two genuinely
    different corpora that happen to share a number read as fresh. A content
    fingerprint has no such blind spot: different bytes, different answer.

    Three tiers, cheapest first, each one only ever ADVANCING to a more expensive
    check — never returning a laxer answer than the tier below it would:

    1. FAST PATH — `rebuild_kb` (the sole legitimate writer) stores the
       whole-corpus fingerprint IN index.json as `corpus_fingerprint`. When it is
       present, compare the cache's fingerprint against that ALREADY-IN-MEMORY
       value: O(1), zero body reads. This is what kills the confirmed regression
       where every recall re-scanned and sha256'd the whole corpus (0.01s→0.20s
       at 20 teams, scaling with whole-fleet bytes rather than the caller's team).
       It is safe only because `save_index` DROPS the stored fingerprint on every
       non-rebuild write — the coupled half of this design, without which the
       fast path would happily trust a cache that predates the last `learn`.

    2. STAT SHORT-CIRCUIT — with no stored fingerprint (a legacy index, a
       hand-built test index, or the window between a `learn` and the next
       rebuild) the tier below re-reads and hashes the ENTIRE corpus on EVERY
       recall. Compare instead the cache's `stat_signature` against a freshly
       computed one: entry metadata already in memory plus one `os.stat` per body
       (see `_corpus_stat_signature`). Still zero body reads, so the O(1)-in-body-
       reads property survives the absence of a stored fingerprint.

    3. HASH — signatures differ (or the cache predates the signature), so fall
       through and re-derive the true fingerprint from the tree. This is what
       makes tier 2 safe to trust in one direction only: a stat that lies (git
       does not preserve mtime across clones) costs a hash, never a wrong answer.

    An absent fingerprint is NEVER treated as fresh (correctness over speed).
    Read-only; no writes, no network.
    """
    if not isinstance(cache, dict):
        return False
    cache_fp = cache.get("fingerprint")
    if not cache_fp:
        return False
    stored_fp = data.get("corpus_fingerprint") if isinstance(data, dict) else None
    if stored_fp:
        return cache_fp == stored_fp  # tier 1: O(1) in-memory — no tree re-read
    cache_sig = cache.get("stat_signature")
    if cache_sig and cache_sig == _corpus_stat_signature(kdir, data):
        return True  # tier 2: stat-only short-circuit — still no body reads
    # tier 3: re-derive from the tree (the only tier that reads entry bodies).
    live_fp = _corpus.eval_corpus_fingerprint(_corpus.build_corpus(kdir, data))
    return cache_fp == live_fp


def bm25_scores_cached(cache, query_tokens, entries_by_id):
    """Cache-backed BM25 scorer — byte-identical to `bm25_scores` (INV-1).

    Returns [(entry, score), ...] for the docs that share >=1 query term (the same
    positive-score subset `retrieve_bm25` keeps after its `s > 0.0` filter), scored
    by the IDENTICAL Okapi BM25 arithmetic as `bm25_scores`:
      idf = ln(1 + (n - df + 0.5)/(df + 0.5));  per-doc k1/b length-norm.
    Only the query terms' postings are touched (O(Σ df(query-term))), but to keep
    the float SUMMATION bit-for-bit identical to the live path, the per-doc inner
    loop iterates `q_terms = set(query_tokens)` in the SAME set-iteration order as
    `bm25_scores` (built from the same `query_tokens` in the same process) and
    skips f==0 the same way. `n`, `df`, and `avgdl` come from the cache and equal
    the whole-corpus values the live `_bm25_index` computes, so idf matches exactly.
    `entries_by_id` maps doc_id -> entry; docs with no live entry are skipped. Pure.
    """
    df = cache["df"]
    postings = cache["postings"]
    doc_lens = cache["doc_lens"]
    n = cache["n"]
    avgdl = cache["avgdl"]
    q_terms = set(query_tokens)
    idf = {}
    for t in q_terms:
        n_t = df.get(t, 0)
        idf[t] = math.log(1.0 + (n - n_t + 0.5) / (n_t + 0.5))
    # Gather only the candidate docs (those in a query term's postings), with the
    # per-doc tf for the matching query terms.
    tf_by_doc = {}
    for t in q_terms:
        for doc_id, freq in postings.get(t, []):
            tf_by_doc.setdefault(doc_id, {})[t] = freq
    results = []
    for doc_id, tfs in tf_by_doc.items():
        entry = entries_by_id.get(doc_id)
        if entry is None:
            continue
        dl = doc_lens.get(doc_id, 0)
        score = 0.0
        for t in q_terms:  # SAME order as bm25_scores' inner loop (INV-1)
            f = tfs.get(t, 0)
            if f == 0:
                continue
            norm = 1.0 - _BM25_B + _BM25_B * (dl / avgdl if avgdl else 0.0)
            denom = f + _BM25_K1 * norm
            score += idf[t] * (f * (_BM25_K1 + 1.0)) / denom
        results.append((entry, score))
    return results


def bm25_positive_scores(kdir, data, query, category=None):
    """Positive-score BM25 list [(entry, score>0), ...], cache-backed when safe.

    Uses the fresh postings cache (O(query-terms)) iff the cache loads, is fresh,
    AND `category is None`; otherwise the live `build_corpus` + `bm25_scores` scan.
    A category filter is the one case the whole-corpus cache cannot serve: the live
    path recomputes df/avgdl over the FILTERED sub-corpus, so its statistics (and
    thus scores) differ from the whole-corpus cache — category-scoped recall always
    uses the live scan to preserve byte-identity. The returned ordering is
    unsorted; every caller applies the canonical tie-break sort. Read-only (INV-2).
    """
    q_tokens = _corpus.tokenize(query)
    if category is None:
        cache = load_bm25_cache(kdir)
        if cache is not None and bm25_cache_is_fresh(cache, data, kdir):
            entries_by_id = {}
            for e in data.get("entries") or []:
                if isinstance(e, dict) and e.get("id") is not None:
                    entries_by_id.setdefault(e.get("id"), e)
            return [(e, s) for (e, s)
                    in bm25_scores_cached(cache, q_tokens, entries_by_id)
                    if s > 0.0]
    corpus = _corpus.build_corpus(kdir, data)
    if category:
        corpus = [(e, t) for (e, t) in corpus if e.get("category") == category]
    return [(e, s) for (e, s) in bm25_scores(corpus, q_tokens) if s > 0.0]


def retrieve_bm25(kdir, data, query, top_k=None):
    """BM25 recall strategy over title+summary+tags+body. Returns ranked ids.

    Deterministic ordering: score desc -> created desc -> id asc. Only entries
    with a positive score (sharing >=1 query term) are returned. Read-only
    (INV-2); no network/LLM/embeddings (INV-1, INV-6).
    """
    scored = bm25_positive_scores(kdir, data, query)
    # Stable multi-pass sort: lowest priority first, highest priority last.
    scored.sort(key=lambda es: es[0].get("id") or "")                       # id asc
    scored.sort(key=lambda es: es[0].get("created") or "", reverse=True)    # created desc
    scored.sort(key=lambda es: es[1], reverse=True)                         # score desc
    # Skill pin in the SHARED core so the eval/gate path (evaluate -> retrieve ->
    # retrieve_bm25) exercises it too (feature 260713-p4). Applied pre-trim.
    scored = _apply_skill_pin(scored, SKILL_PIN_FLOOR, top_k)
    ids = [e.get("id") for (e, _s) in scored]
    return ids if top_k is None else ids[:top_k]


# Explicit export list (public AND underscore-private) — the M3/_EXPORTS
# convention: the manifest anchor-symbol set (tests/_manifest_gen.py) and the
# granular surface-floor denominator (tests/unit/retrieval/test_bm25.py).
# bm25 IS in the entrypoint's `_FACADE_MODULES` re-export loop, so THIS list is
# the single source of truth for what reaches the CLI facade (CLI.<name> for the
# SourceFileLoader tests + the dashboard) — there is no second by-name import
# tuple to keep in sync. The resident `retrieve` dispatcher reaches retrieve_bm25
# through globals().get("retrieve_bm25"), which the loop's bind satisfies. All 15
# names below are re-exported; adding one here is the whole wiring.
_EXPORTS = [
    "BM25_CACHE_REL",
    "BM25_CACHE_VERSION",
    "_BM25_K1",
    "_BM25_B",
    "_bm25_index",
    "bm25_scores",
    "bm25_cache_path",
    "_corpus_stat_signature",
    "build_bm25_cache",
    "write_bm25_cache",
    "load_bm25_cache",
    "bm25_cache_is_fresh",
    "bm25_scores_cached",
    "bm25_positive_scores",
    "retrieve_bm25",
]
