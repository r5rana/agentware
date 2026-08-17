"""agentware_lib.retrieval.corpus — the shared BM25 corpus + tokenizer (layer: retrieval).

Owns: the single shared tokenizer (_TOKEN_RE + tokenize) reused by every ranking
path, the ~4-chars/token estimate_tokens heuristic, the read-only corpus document
assembly (_read_entry_body + _entry_doc_text + build_corpus for curated entries,
build_work_corpus for --scope work), the deterministic whole-corpus
eval_corpus_fingerprint (gate comparability), and the exact-tag retrieve_tag
baseline strategy.
Does NOT own: BM25 scoring + the derived postings cache (agentware_lib.retrieval.bm25,
a later wave-3 seam); RRF hybrid fusion + the embedder/vector cache
(retrieval.hybrid / retrieval.vectorcache); the ACR provenance prior (retrieval.acr);
the frontmatter parser it calls to strip machine blocks from entry bodies
(agentware_lib.core.frontmatter).
Layer deps: retrieval -> core only this seam (imports agentware_lib.core.frontmatter
for strip_frontmatter; stdlib os/re/hashlib). Imports nothing above core; nothing in
core/ or knowledge/ imports from here.
Fan-in: 12 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

INV-1: ranking is hand-rolled, stdlib-only — NO LLM, NO embeddings, NO network, NO
wall-clock in scoring. INV-2: read-only — never mutates index.json or the KB. The
tokenizer here is the SINGLE shared tokenizer reused by the BM25 `recall` engine
(Phase 1) so the baseline (tag) and ranked (bm25) paths stay comparable.
"""

import os
import re

from agentware_lib.core import frontmatter
# The WORK-scope corpus moved to its own module with its cache and its plan-status
# weighting (task 55): it grows without bound, needs a stat-digest cache to stay
# affordable, and carries a ranking multiplier the curated corpus must never see.
# Imported LAZILY inside the re-export below — workcorpus imports this module for
# the tokenizer, so a module-scope import here would close the cycle.


# Fixed, documented tokenizer: lowercase, split on non-alphanumeric runs, no
# stemming (v1). Pure stdlib; deterministic for identical input.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text):
    """Lowercase + split on non-alphanumeric. Returns a list of tokens."""
    return _TOKEN_RE.findall((text or "").lower())


def estimate_tokens(text):
    """Documented heuristic: ~4 characters per token (context-budget estimate)."""
    return (len(text or "") + 3) // 4


def _read_entry_body(kdir, entry):
    """Read an entry's markdown body (read-only, best-effort). '' on any error.

    Strips any leading machine frontmatter block so the BM25 corpus sees only the
    human body — keeping recall/eval byte-identical to the pre-frontmatter corpus
    (the index already contributes title/summary/tags separately).
    """
    rel = entry.get("path")
    if not rel:
        return ""
    try:
        with open(os.path.join(kdir, rel), "r", encoding="utf-8") as f:
            return frontmatter.strip_frontmatter(f.read())
    except OSError:
        return ""


def _entry_doc_text(kdir, entry):
    """The BM25 corpus document for one entry: title + summary + tags + body.

    Factored out of `build_corpus` so the cache-backed recall path can reconstruct
    the EXACT same doc_text for a surfaced top-k entry (for token estimation)
    without rebuilding the whole corpus — guaranteeing byte-identity by sharing
    this single join (INV-1). Read-only.
    """
    parts = [
        entry.get("title") or "",
        entry.get("summary") or "",
        # `_entry_list_field` keeps the recall corpus from crashing (scalar tags)
        # or silently corrupting (a string `tags` would join per-char) on a
        # poisoned index; byte-identical for a valid list-of-strings.
        " ".join(frontmatter._entry_list_field(entry, "tags")),
        _read_entry_body(kdir, entry),
    ]
    return "\n".join(parts)


def build_corpus(kdir, data):
    """Return [(entry, doc_text), ...] for every dict entry, in index order.

    doc_text = title + summary + tags + file body. Read-only; this is the shared
    corpus builder for both the tag baseline (footprint) and BM25 recall (Phase 1).
    """
    corpus = []
    for e in data.get("entries") or []:
        if not isinstance(e, dict):
            continue
        corpus.append((e, _entry_doc_text(kdir, e)))
    return corpus


def eval_corpus_fingerprint(corpus):
    """Deterministic whole-corpus fingerprint (sha256 hex) for gate comparability.

    DISTINCT from the per-document `_corpus_fingerprint` (:492, the vector-cache
    key): this hashes the ENTIRE corpus actually scored — the ordered set of
    `(id, doc_text)` pairs — in CANONICAL order (sorted by id) so byte-identical
    corpora hash identically across runs/machines regardless of index order, while
    ANY add/remove/edit of a scored entry changes the hash. Pure: no timestamps,
    no machine state. `corpus` is the `[(entry, doc_text), ...]` list from
    `build_corpus`. This is the seam that makes `eval --gate` reproducible — two
    runs only ever compare absolute scores when they scored the same corpus.
    """
    import hashlib  # stdlib, lazy
    pairs = sorted(
        ((str((e or {}).get("id") or ""), doc or "") for (e, doc) in corpus),
        key=lambda p: p[0])
    h = hashlib.sha256()
    for eid, doc in pairs:
        h.update(eid.encode("utf-8"))
        h.update(b"\x00")
        h.update(doc.encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()


def retrieve_tag(kdir, data, query, top_k=None):
    """Baseline strategy: map a free-text query onto the EXACT-tag `query` path.

    Tokenize the query, union the exact-tag hits for each token that is a known
    tag, and rank entries by (# matching query tokens) desc, then `created` desc,
    then `id` asc. Deterministic and tokenizer-stable so the baseline reproduces.
    `kdir` is accepted for a uniform strategy signature (unused here).
    """
    tags_map = data.get("tags") or {}
    by_id = {}
    for e in data.get("entries") or []:
        if isinstance(e, dict) and e.get("id") is not None:
            by_id.setdefault(e.get("id"), e)

    counts = {}
    for tok in set(tokenize(query)):
        for eid in tags_map.get(tok, []):
            counts[eid] = counts.get(eid, 0) + 1

    ranked = list(counts.keys())
    # Stable multi-pass sort, lowest priority first → highest priority last.
    ranked.sort(key=lambda eid: eid)                                      # id asc
    ranked.sort(key=lambda eid: by_id.get(eid, {}).get("created") or "",  # created
                reverse=True)                                            # desc
    ranked.sort(key=lambda eid: counts[eid], reverse=True)               # matches desc
    return ranked if top_k is None else ranked[:top_k]

# Explicit export list (public AND underscore-private) — the M3/_EXPORTS
# convention: it is the manifest anchor-symbol set (tests/_manifest_gen.py) and the
# granular surface-floor denominator (tests/unit/retrieval/test_corpus.py).
# ⚠️ Unlike the loop-bound core/knowledge modules, corpus is NOT in the entrypoint
# _reexport loop: these 9 names reach the CLI facade (CLI.<name> for
# SourceFileLoader tests + the dashboard) via the by-name
# `from agentware_lib.retrieval.corpus import (...)` at the top of scripts/agentware
# — because an owner-module `corpus.<fn>` ref would collide with the local `corpus`
# variable the resident BM25 scorers bind. Keep this list and that import tuple in
# sync (both must name all 9).
_EXPORTS = [
    "_TOKEN_RE",
    "tokenize",
    "estimate_tokens",
    "_read_entry_body",
    "_entry_doc_text",
    "build_corpus",
    "build_work_corpus",
    "eval_corpus_fingerprint",
    "retrieve_tag",
]


def build_work_corpus(kdir):
    """Re-export of `retrieval.workcorpus.build_work_corpus`.

    Kept here BY NAME so every pre-split caller and monkeypatch target resolves
    unchanged. Lazy import, because `workcorpus` imports this module for the
    tokenizer and estimator — a module-scope import would close the cycle.
    """
    from agentware_lib.retrieval.workcorpus import build_work_corpus as _impl
    return _impl(kdir)
