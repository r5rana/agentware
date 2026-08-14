"""agentware_lib.retrieval.skillpin - skill pinning + team scoping + the shared ranked-recall core (layer: retrieval).

Owns: the R-RET-03 stage-1 skill-pin precedence (the default-OFF SKILL_PIN_FLOOR
threshold + _apply_skill_pin, applied in the shared ranking core so both recall and
the eval/gate path exercise it), the org recall-scoping filter (_entry_is_shared /
scope_entries_for_team - the org recall-scoping contract: caller-team
knowledge union shared skills by
default), the deterministic ranked-recall core recall_ranked ([(entry, score,
doc_text), ...] for bm25 / bm25+acr / bm25+embed) and the strategy dispatcher
retrieve (tag / bm25 / bm25+acr / bm25+embed -> ranked ids).
Does NOT own: BM25 scoring + the postings cache (agentware_lib.retrieval.bm25, via
the _bm25 module alias - retrieve resolves retrieve_bm25 through
getattr(_bm25, "retrieve_bm25", None), the split-module form of the old
globals().get lookup: same object, same None-guard); the shared tokenizer + corpus
assembly + the retrieve_tag baseline (agentware_lib.retrieval.corpus, via _corpus);
the ACR provenance prior + retrieve_bm25_acr (agentware_lib.retrieval.acr, via _acr);
the RRF hybrid Mode-B retrievers (agentware_lib.retrieval.hybrid, via _hybrid); the
recall/eval CLI handlers + build_recall_payload that call this core (still resident -
they reach recall_ranked / scope_entries_for_team / retrieve via the by-name facade
bind on the entrypoint).
Layer deps: retrieval -> core (agentware_lib.core.orgkeys + agentware_lib.core.dates,
eager) + retrieval -> retrieval (agentware_lib.retrieval.corpus/bm25/acr/hybrid as the
module aliases _corpus/_bm25/_acr/_hybrid, so the global-resolve gate never walks a
foreign sibling function held in this module's __dict__). Imports nothing above
retrieval; nothing in core/ or knowledge/ imports from here.
Fan-in: 3 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

INV-1: ranking is deterministic + stdlib-only - the skill pin, team scoping and
tie-break are pure functions of (entries, query, scores, as_of); at a FIXED as_of the
ordering is byte-identical across runs. SKILL_PIN_FLOOR defaults to None (OFF) so
ranking is byte-unchanged from pre-pin until a later gated flip. INV-2: read-only -
recall_ranked/retrieve never mutate the entry, index.json or the KB. The skill pin
only RE-ORDERS an already-scored list (learnings are never evicted); the acr / embed
multipliers are bounded so they re-order the positive-BM25 set, never inject a
zero-score entry.
"""

import agentware_lib.core.orgkeys as orgkeys
import agentware_lib.core.dates as dates
from agentware_lib.retrieval import corpus as _corpus
from agentware_lib.retrieval import bm25 as _bm25
from agentware_lib.retrieval import acr as _acr
from agentware_lib.retrieval import hybrid as _hybrid


SKILL_PIN_FLOOR = None  # e.g. 0.85 when enabled


def _apply_skill_pin(scored, pin_floor):
    """Apply threshold-gated skill pin to a scored list.

    `scored` is [(entry, score), ...] already sorted by BM25+tie-break.
    If `pin_floor` is None (disabled), returns scored unchanged.
    Otherwise: find the top skill; if skill_score >= pin_floor * top_overall_score
    AND > 0, move it to position 0 (pinned). Learnings are never evicted.
    Deterministic; read-only.
    """
    if pin_floor is None or not scored:
        return scored
    top_score = scored[0][1] if scored else 0.0
    if top_score <= 0:
        return scored
    threshold = pin_floor * top_score
    # Find the highest-scoring skill entry
    best_skill_idx = None
    for i, (entry, score) in enumerate(scored):
        if entry.get("category") == "skills" and score >= threshold and score > 0:
            best_skill_idx = i
            break  # Already sorted by score desc, first skill above threshold wins
    if best_skill_idx is None or best_skill_idx == 0:
        return scored  # No qualifying skill, or already at top
    # Pin: move the skill to position 0
    skill_item = scored.pop(best_skill_idx)
    scored.insert(0, skill_item)
    return scored


def _entry_is_shared(e):
    """True iff an index entry is org-shared (origin=='shared')."""
    return (e.get("origin") == "shared")


def scope_entries_for_team(entries, caller_team, team=None, all_teams=False,
                           include_shared=False, scope=None):
    """Filter index entries by the org recall-scoping contract.

    DEFAULT (no flags): caller-team knowledge ∪ shared *skills* only — shared
    skills are always-on org-wide (consistent agent behavior); shared knowledge
    AND other teams are EXCLUDED (no cross-team pollution).
      --all-teams      -> every team (+ all shared).
      --team <t>       -> exactly team <t> (t == 'shared' selects origin:shared).
      --include-shared -> caller team ∪ ALL shared knowledge+skills.
      --scope shared   -> shared entries only.
    Applied to `entries` BEFORE build_corpus (R2 perf fix: only the in-scope files
    are read; df/avgdl computed over the sub-corpus). When caller_team is None
    (flat non-org KB: no entry carries a team facet), returns entries UNCHANGED so
    single-team behavior is byte-identical (INV-1). Pure; returns a NEW list.
    """
    # Flat / non-org corpus: nothing carries a team facet -> no scoping (INV-1).
    if not any(e.get("team") or e.get("origin") for e in entries):
        return list(entries)
    if scope == "shared":
        return [e for e in entries if _entry_is_shared(e)]
    if all_teams:
        return list(entries)  # every team + shared
    if team is not None:
        if team == orgkeys.ORG_SHARED_DIR:
            return [e for e in entries if _entry_is_shared(e)]
        return [e for e in entries if e.get("team") == team]
    # DEFAULT: caller team ∪ shared skills (+ all shared knowledge iff include_shared)
    out = []
    for e in entries:
        if e.get("team") == caller_team and not _entry_is_shared(e):
            out.append(e)
        elif _entry_is_shared(e):
            if include_shared or e.get("category") == "skills":
                out.append(e)
    return out


def recall_ranked(kdir, data, query, top_k=None, category=None,
                  strategy="bm25", as_of=None):
    """Deterministic ranked recall returning [(entry, score, doc_text), ...].

    `strategy="bm25"` (default) reuses the shared `build_corpus` + `bm25_scores`
    so ordering is byte-identical to `retrieve_bm25`: score desc -> created desc
    -> id asc, positive scores only. `strategy="bm25+acr"` multiplies each BM25
    score by `acr(entry, as_of)` (provenance × freshness prior) and re-sorts under
    the SAME tie-break — same surfaced set (relevance gates; acr ∈ (0,1]), merely
    reordered. `strategy="bm25+embed"` is the Mode-B hybrid (RRF of BM25 + local
    query-embedding cosine), gracefully falling back to plain `bm25` when no local
    embedder is installed — embeddings RANK only (no LLM; the moat). `as_of`
    defaults to today only at this boundary; PIN it for reproducible runs. An
    optional `category` restricts the corpus BEFORE scoring. Read-only (INV-2);
    only `bm25+embed` consults the LOCAL embedder (INV-1, INV-6). `doc_text` is the
    entry's full corpus document (title+summary+tags+body) for token estimation.
    """
    if strategy not in ("bm25", "bm25+acr", "bm25+embed"):
        raise ValueError("unknown recall strategy: %s" % strategy)
    if strategy == "bm25+embed":
        # Restrict the corpus+fusion to `category` BEFORE ranking (mirrors the
        # bm25 path) by passing an entries-filtered view; the hybrid core builds
        # its own corpus + fuses over `sub`, so both stay consistent.
        sub = data
        if category:
            sub = dict(data)
            sub["entries"] = [e for e in (data.get("entries") or [])
                              if e.get("category") == category]
        scored = _hybrid.retrieve_bm25_embed_scored(kdir, sub, query, top_k=top_k,
                                            as_of=as_of)
        text_by_id = {e.get("id"): t for (e, t) in _corpus.build_corpus(kdir, sub)}
        return [(e, s, text_by_id.get(e.get("id"), "")) for (e, s) in scored]
    # Cache-backed (O(query-terms)) when fresh + uncategorized, else the live scan;
    # bm25_positive_scores guarantees byte-identical scores either way (INV-1).
    scored = _bm25.bm25_positive_scores(kdir, data, query, category=category)
    if strategy == "bm25+acr":
        if as_of is None:
            as_of = dates._today()
        scored = [(e, s * _acr.acr(e, as_of)) for (e, s) in scored]
    # Stable multi-pass sort: lowest priority first, highest priority last.
    scored.sort(key=lambda es: es[0].get("id") or "")                       # id asc
    scored.sort(key=lambda es: es[0].get("created") or "", reverse=True)    # created desc
    scored.sort(key=lambda es: es[1], reverse=True)                         # score desc
    # Skill pin (shared ranking core — both recall_ranked and the eval/gate
    # retrieve_bm25 path exercise this). Applied AFTER sort, BEFORE top_k trim.
    scored = _apply_skill_pin(scored, SKILL_PIN_FLOOR)
    if top_k is not None:
        scored = scored[:top_k]
    # Reconstruct doc_text for the SURFACED entries only (token estimation), via
    # the shared `_entry_doc_text` join so it is byte-identical to build_corpus.
    return [(e, s, _corpus._entry_doc_text(kdir, e)) for (e, s) in scored]


def retrieve(kdir, data, query, strategy, top_k=None, as_of=None):
    """Dispatch to a retrieval strategy. Returns a ranked list of entry ids.

    'tag'      — the legacy exact-tag baseline (available from day one).
    'bm25'     — the deterministic BM25 recall engine (provided by Phase 1 as the
                 module-level `retrieve_bm25`); errors clearly until that lands.
    'bm25+acr' — BM25 relevance re-weighted by the ACR provenance+freshness prior
                 (`final = bm25 × acr(entry, as_of)`). `as_of` defaults to today
                 only here; PIN it (eval/tests) for reproducible, byte-identical
                 ordering (INV-1). Surfaces the SAME set as 'bm25' (relevance
                 gates), merely reordered (C-3 — plain 'bm25' stays untouched).
    'bm25+embed' — hybrid Mode-B retrieval: RRF fusion (integer ranks) of the
                 BM25 ranking + a query-embedding cosine ranking from the LOCAL
                 embedder (rank-only, no LLM). GRACEFULLY falls back to 'bm25'
                 when no local embedder is installed. Deterministic given a pinned
                 model + cached vectors; 'tag'/'bm25'/'bm25+acr' stay UNCHANGED.
    """
    if strategy == "tag":
        return _corpus.retrieve_tag(kdir, data, query, top_k)
    if strategy == "bm25":
        fn = getattr(_bm25, "retrieve_bm25", None)
        if fn is None:
            raise NotImplementedError(
                "the 'bm25' strategy requires the recall engine (Phase 1); "
                "use --strategy tag for the baseline")
        return fn(kdir, data, query, top_k)
    if strategy == "bm25+acr":
        if as_of is None:
            as_of = dates._today()
        return _acr.retrieve_bm25_acr(kdir, data, query, as_of, top_k)
    if strategy == "bm25+embed":
        return _hybrid.retrieve_bm25_embed(kdir, data, query, top_k, as_of=as_of)
    raise ValueError("unknown retrieval strategy: %s" % strategy)


_EXPORTS = [
    "SKILL_PIN_FLOOR",
    "_apply_skill_pin",
    "_entry_is_shared",
    "scope_entries_for_team",
    "recall_ranked",
    "retrieve",
]
