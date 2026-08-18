"""agentware_lib.retrieval.acr - the ACR provenance+freshness re-ranking prior (layer: retrieval).

Owns: the Authority/Confidence Re-ranking prior - the source-weight + half-life
freshness multipliers (ACR_SOURCE_WEIGHTS / ACR_DEFAULT_SOURCE_WEIGHT /
ACR_HALF_LIFE_DAYS / ACR_FRESHNESS_FLOOR), the pure priors source_weight /
freshness / acr, the age helpers date_age_days / answer_age_days (used by the
age-stratified ACR win gate), and the ACR-reranked BM25 retriever
retrieve_bm25_acr (final = bm25_relevance x acr(entry, as_of) x
staleness_factor(entry, ...)).
Does NOT own: BM25 scoring + the postings cache (agentware_lib.retrieval.bm25,
reached via the _bm25 module alias for retrieve_bm25_acr); the ACR WIN GATE +
default-flip ledger (a later retrieval.evaluate seam - a DISTINCT domain from this
prior); the staleness/supersede downweight itself (superseded_ids /
staleness_factor live in agentware_lib.knowledge.freshsignals - this module only
APPLIES the multiplier alongside its own prior); entry_last_verified, read from
the knowledge system of record (agentware_lib.knowledge.index, imported LAZILY
inside acr/answer_age_days so retrieval -> knowledge stays lazy); the date
validators _today/_is_valid_date (agentware_lib.core.dates).
Layer deps: retrieval -> core (agentware_lib.core.dates, eager) + the retrieval
sibling bm25 (module alias) + knowledge lazily (agentware_lib.knowledge.index in
acr/answer_age_days, agentware_lib.knowledge.freshsignals in retrieve_bm25_acr;
both function-local - freshsignals imports date_age_days FROM here, so an eager
import back would be a cycle). Nothing above retrieval; nothing in core/ or
knowledge/ imports here.
Fan-in: 3 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

INV-1: acr is a PURE function of (entry, as_of) - no LLM/RNG/network/wall-clock -
so at a FIXED as_of the ACR-weighted ranking is byte-identical across runs. `as_of`
is an EXPLICIT parameter (defaulted to today only at the CLI boundary; PINNED in
tests/eval). INV-2: read-only - never mutates the entry or the KB. The acr
multiplier is bounded in (0, 1], so it only RE-ORDERS the positive-BM25 set that
retrieve_bm25 already surfaces - it can never lift a zero-score entry into results.
"""

import agentware_lib.core.dates as dates
from agentware_lib.retrieval import bm25 as _bm25


ACR_SOURCE_WEIGHTS = {"user": 1.0, "agent": 0.9, "imported": 0.8}
# Unattributed/legacy entries (no `source` frontmatter) are treated as
# agent-authored — a gentle middle weight, never the top "user" tier.
ACR_DEFAULT_SOURCE_WEIGHT = 0.9
ACR_HALF_LIFE_DAYS = 90          # confidence half-life: freshness halves every H days
ACR_FRESHNESS_FLOOR = 0.85       # a still-true old fact is never buried below this


def source_weight(source):
    """Provenance prior for an entry's `source`. user > agent > imported.

    Unknown/missing/empty source falls back to ACR_DEFAULT_SOURCE_WEIGHT so
    legacy entries are never harshly penalized. Deterministic, stdlib-only.
    """
    if isinstance(source, str):
        key = source.strip().lower()
        if key in ACR_SOURCE_WEIGHTS:
            return ACR_SOURCE_WEIGHTS[key]
    return ACR_DEFAULT_SOURCE_WEIGHT


def freshness(last_verified, as_of, half_life=ACR_HALF_LIFE_DAYS,
              floor=ACR_FRESHNESS_FLOOR):
    """Read-time half-life decay of confidence, gently floored.

    `freshness = max(floor, 0.5 ** (age_days / half_life))` where
    `age_days = max(0, as_of - last_verified)`. A future `last_verified`
    (negative age) is clamped to age 0 → 1.0 (never boosts above fresh). An
    unknown/invalid `last_verified` returns `floor` (treated as oldest, but
    only the gentle floor penalty). Pure function of the two dates; no clock.
    """
    import datetime
    if not dates._is_valid_date(as_of):
        raise ValueError("as_of must be a YYYY-MM-DD date, got %r" % (as_of,))
    if not dates._is_valid_date(last_verified):
        return floor
    lv = datetime.date.fromisoformat(last_verified)
    now = datetime.date.fromisoformat(as_of)
    age_days = (now - lv).days
    if age_days <= 0:
        return 1.0
    return max(floor, 0.5 ** (age_days / float(half_life)))


def acr(entry, as_of):
    """Authority/Confidence prior = source_weight × freshness, in [floor*0.8, 1.0].

    Pure, deterministic, bounded. Reuses `entry_last_verified` (which falls back
    to `created`) for the freshness date — no second freshness accessor. Never
    mutates `entry` (INV-2). `as_of` is explicit and required.
    """
    from agentware_lib.knowledge import index
    if not isinstance(entry, dict):
        raise ValueError("acr expects an entry dict, got %r" % (type(entry),))
    sw = source_weight(entry.get("source"))
    fr = freshness(index.entry_last_verified(entry), as_of)
    return sw * fr


def date_age_days(date_str, as_of):
    """Age in whole days of `date_str` as-of `as_of`, clamped ≥0.

    Pure function of two YYYY-MM-DD dates (no clock). A future date (negative
    age) clamps to 0. Returns None when either date is invalid/missing — used
    by the age-stratified slice in the ACR win gate, NOT by ranking, so an
    unknown answer date simply drops out of the stratum rather than skewing it.
    """
    if not dates._is_valid_date(as_of) or not dates._is_valid_date(date_str):
        return None
    import datetime
    age = (datetime.date.fromisoformat(as_of)
           - datetime.date.fromisoformat(date_str)).days
    return max(0, age)


def answer_age_days(entries_by_id, expected_ids, as_of):
    """Representative age (days) of a gold query's correct answer, as-of `as_of`.

    For the age-stratified no-regression guard (Phase 3.2): split gold queries
    into OLD-answer vs RECENT-answer strata. When a query has several correct
    answers we take the OLDEST present one (max age) so a query counts as
    OLD-stratum if ANY of its correct answers is old — the conservative choice
    that catches ACR "winning" only by burying still-true old facts. Returns
    None when no expected entry resolves to a valid date (excluded from slices).
    """
    from agentware_lib.knowledge import index
    ages = []
    for eid in expected_ids:
        entry = entries_by_id.get(eid)
        if not isinstance(entry, dict):
            continue
        age = date_age_days(index.entry_last_verified(entry), as_of)
        if age is not None:
            ages.append(age)
    return max(ages) if ages else None


def retrieve_bm25_acr(kdir, data, query, as_of, top_k=None):
    """ACR-reranked BM25: `final = bm25_relevance × acr(entry, as_of) ×
    staleness_factor(entry, ...)`.

    Surfaces the EXACT same set as `retrieve_bm25` — relevance gates: only
    positive-BM25 entries enter, and both multipliers are bounded in (0, 1] so they
    can never lift a zero-score (non-matching) entry into the result (C-3). The
    surfaced set is merely RE-ORDERED by the weighted score under the SAME
    deterministic tie-break (score desc -> created desc -> id asc). `staleness_factor`
    downweights a stale/superseded entry (1.0 for fresh/non-superseded, so their
    score stays byte-identical to acr-only); `superseded_ids` is computed ONCE here,
    never per entry. Both multipliers are pure functions of `(entry, as_of)`, so at
    a FIXED `as_of` the ordering is byte-identical across runs (INV-1). Read-only
    (INV-2); stdlib only, no network/LLM/embeddings. Plain `bm25` is untouched.

    `knowledge.freshsignals` is imported FUNCTION-LOCAL, not at module scope: it
    eagerly imports `date_age_days` from THIS module, so an eager import here is a
    genuine cycle — and the lazy form is what this module's layer contract already
    mandates for every `retrieval -> knowledge` reach. The MODULE ALIAS (rather
    than `from ... import staleness_factor`) is load-bearing: the name is resolved
    per call, so a test patching the owning module still reaches this call site
    instead of a stale copy captured at import time.
    """
    from agentware_lib.knowledge import freshsignals as _fresh
    superseded = _fresh.superseded_ids(data)
    scored = [(e, s * acr(e, as_of) * _fresh.staleness_factor(e, superseded, as_of))
              for (e, s) in _bm25.bm25_positive_scores(kdir, data, query)]
    # Stable multi-pass sort: lowest priority first, highest priority last.
    scored.sort(key=lambda es: es[0].get("id") or "")                       # id asc
    scored.sort(key=lambda es: es[0].get("created") or "", reverse=True)    # created desc
    scored.sort(key=lambda es: es[1], reverse=True)                         # score desc
    ids = [e.get("id") for (e, _s) in scored]
    return ids if top_k is None else ids[:top_k]


_EXPORTS = [
    "ACR_SOURCE_WEIGHTS",
    "ACR_DEFAULT_SOURCE_WEIGHT",
    "ACR_HALF_LIFE_DAYS",
    "ACR_FRESHNESS_FLOOR",
    "source_weight",
    "freshness",
    "acr",
    "date_age_days",
    "answer_age_days",
    "retrieve_bm25_acr",
]
