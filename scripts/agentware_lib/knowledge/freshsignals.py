"""agentware_lib.knowledge.freshsignals -- the ONE definition of the stale window, and the read-time trust surface built on it (layer: knowledge).

Owns: the staleness WINDOW and vocabulary (`STALE_MAX_AGE_DAYS`,
`VOLATILE_CATEGORIES`, `stale_entries`), the declared-supersede edge and its
reverse lookup (`SUPERSEDES_RELATION`, `superseded_ids`), the read-time trust
record (`_trust_unknown` / `_trust_record` / `trust_signals`), and the RANKING
downweight built from both (`_stale_age`, `staleness_factor`,
`STALENESS_STALE_MULT`, `STALENESS_SUPERSEDED_MULT`).
Does NOT own: the duplicate/conflict detector and the advisory report assembled
around these -> ops.freshness; the staleness review PROPOSAL -> features.staleproposal;
the ACR prior these ride alongside -> retrieval.acr; the ranking core that applies
the downweight -> retrieval.skillpin.

WHY ONE DEFINITION, IN THIS LAYER
---------------------------------
Three consumers ask "is this entry stale?" and they must agree: `audit --stale`
(via ops.freshness), the read-time TRUST fields on every recall record, and the
RANKING downweight. They sit in three different layers -- ops, retrieval and the
command surface -- and knowledge is the only tier all three already reach.
Homing the window anywhere else forces either an upward import or a second
constant, and a second constant is the failure mode outright: an entry reported
stale by `audit` but ranked as current by recall is a system whose two halves
disagree about the same fact, and the operator has no way to tell which to
believe.

WHY THE DOWNWEIGHT IS SEPARATE FROM THE ACR PRIOR
-------------------------------------------------
The ACR prior is deliberately GENTLE: freshness is floored at
`ACR_FRESHNESS_FLOOR` so a still-true OLD fact is never buried. That is the right
default for a fact that merely AGED, and the WRONG one for a fact that is
genuinely STALE (a point-in-time value whose `last_verified` is long past --
R-FAIL-08) or SUPERSEDED (a newer entry DECLARES `supersedes:<id>` against it).
`staleness_factor` is the bounded (0,1] multiplier that rides ALONGSIDE acr for
exactly those two cases; a fresh, non-superseded entry gets 1.0, so its score is
byte-identical to the un-downweighted one.

READ-TIME, AND WRITE-FREE (INV-2)
---------------------------------
Every signal here is COMPUTED from already-declared data at read time. Nothing
writes a `stale:` or `trust:` field back onto an entry, and nothing rewrites
trust silently. A proven-wrong learning is demoted by a DECLARED supersede edge
plus a flag for review -- never an auto-delete, and never an auto-mark. Auto-
correction is a tier-2 PROPOSED workorder instead
(decide-p3-trust-staleness, decide-self-correction-governance-tiers).

Layer deps: knowledge -> core (`dates`), knowledge -> knowledge (`index` for the
effective `last_verified`, `graph.build_adjacency` for the reverse supersede
edges -- the SAME adjacency the knowledge graph uses, not a second walk),
knowledge -> retrieval (`acr.date_age_days`, the pure two-date helper; acyclic
because `acr` imports `knowledge.index` lazily by design).
Determinism: `stale_entries` and `trust_signals` take `today`/`as_of` as
parameters rather than reading the clock, so the ranking path is reproducible
(INV-1 forbids wall-clock in RANKING); the advisory report path may default to
the system date because it only reports.
"""

import agentware_lib.core.dates as dates
import agentware_lib.knowledge.index as index
from agentware_lib.core.dates import _is_valid_date
from agentware_lib.knowledge.graph import build_adjacency
from agentware_lib.knowledge.index import entry_last_verified
# The pure two-date age helper lives with the ACR prior that introduced it. The
# edge is safe: `retrieval.acr` imports `knowledge.index` LAZILY (function-local)
# precisely so this direction stays acyclic, and duplicating a six-line date
# calculation is how two "ages" start disagreeing by a day at a boundary.
from agentware_lib.retrieval.acr import date_age_days


def _trust_unknown():
    """The no-signal fallback record (a FRESH dict per call — never a shared
    mutable, since callers merge it into their own result dict)."""
    return {"stale": False, "superseded_by": [], "trust": "current"}


def _trust_record(signals, eid):
    """A FRESH trust record for `eid` from a `trust_signals` map, for merging into
    one result. Copies the `superseded_by` LIST: `dict.update` copies key/value
    PAIRS, so merging the map's record raw would alias its list into the result —
    letting a mutating consumer reach back into the shared map (and, on an index
    with duplicate ids, letting two results alias ONE list). Unreachable today
    (`index validate` rejects duplicate ids and every consumer only reads), so
    this is insurance against a future in-place consumer, bought for one copy of
    an almost-always-empty list."""
    sig = signals.get(eid)
    if not sig:
        return _trust_unknown()
    return {"stale": sig["stale"],
            "superseded_by": list(sig["superseded_by"]),
            "trust": sig["trust"]}


def trust_signals(data, as_of, max_age_days=None):
    """Per-entry read-time trust judgment: {id: {stale, superseded_by, trust}}.

    `trust` is "superseded" when another entry DECLARES `supersedes:<id>` against
    this one, else "stale" when it is a VOLATILE_CATEGORIES entry whose effective
    last_verified is older than `max_age_days`, else "current". Superseded
    OUTRANKS stale (an entry a newer one explicitly replaced is suspect no matter
    its age); `stale` is reported ALONGSIDE `trust` so that bit survives the
    precedence, and `superseded_by` is the sorted list of superseding ids ([] when
    none).

    Reuses `build_adjacency`'s reverse map for the DECLARED supersede edges and
    `stale_entries` for the window — no second edge walk and no second staleness
    definition, so this ranking signal and the advisory `audit --stale` report
    share VOLATILE_CATEGORIES + STALE_MAX_AGE_DAYS and can never diverge.
    `stale_entries` may default `today` to the wall clock because it is ADVISORY;
    this feeds RANKING, so `as_of` is explicit, REQUIRED, and validated then
    passed straight through — never a hidden clock (INV-1). `max_age_days=None`
    resolves to the shared STALE_MAX_AGE_DAYS (a None sentinel, not a literal
    default, so the one definition stays at its declaration site). Pure +
    read-only — never mutates the KB, the index, or any entry (INV-2).

    Deliberate edges, each a silent-wrong-answer trap if left implicit:
      * `stale` is False for a NON-volatile entry because the window does not
        APPLY to it — "not applicable", never "verified fresh".
      * A self-supersede edge (X supersedes X) is IGNORED: an entry cannot
        replace itself, and honoring it would bury X forever on one typo.
      * A MUTUAL supersede (X<->Y) marks BOTH superseded — faithful to what the
        pair declares, and `audit`'s graph_integrity reports the cycle. This path
        reports declared edges; it never guesses which side is authoritative.
      * An out-of-vocab type ("Supersede:X") is NOT counted — graph_integrity
        flags it rather than this ranking path second-guessing the vocabulary.
      * An entry with no valid date is NOT stale here (stale_entries skips it),
        though `freshness` treats it as oldest — staleness needs a date to be
        beyond the window; absence of evidence is not evidence of staleness.
    """
    if not _is_valid_date(as_of):
        raise ValueError("as_of must be a YYYY-MM-DD date, got %r" % (as_of,))
    if max_age_days is None:
        max_age_days = STALE_MAX_AGE_DAYS
    _forward, reverse = build_adjacency(data)
    stale_ids = {e.get("id")
                 for (e, _age) in stale_entries(data, max_age_days, today=as_of)}
    out = {}
    for e in data.get("entries") or []:
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        if not eid:
            continue
        superseded_by = sorted({src for (etype, src) in reverse.get(eid, [])
                                if etype == SUPERSEDES_RELATION and src != eid})
        is_stale = eid in stale_ids
        out[eid] = {
            "stale": is_stale,
            "superseded_by": superseded_by,
            "trust": ("superseded" if superseded_by
                      else ("stale" if is_stale else "current")),
        }
    return out


def superseded_ids(data):
    """Set of entry ids DECLARED superseded — an id carrying a `supersedes`
    reverse edge from a DIFFERENT id (self-supersede ignored, matching
    `trust_signals`). Reuses `build_adjacency`'s reverse map. Pure + read-only
    (INV-2). Precomputed ONCE per ranking call and threaded into
    `staleness_factor` (never re-walked per entry — plan pitfall b)."""
    _forward, reverse = build_adjacency(data)
    # Restrict to ids that are REAL entries (a dangling `supersedes:ghost` edge
    # does not invent `ghost`), so this set and `trust_signals`' superseded marks
    # agree on exactly the same entries — surface and downweight never diverge.
    real = {e.get("id") for e in (data.get("entries") or [])
            if isinstance(e, dict) and e.get("id")}
    out = set()
    for eid, edges in reverse.items():
        if eid not in real:
            continue
        if any(etype == SUPERSEDES_RELATION and src != eid
               for (etype, src) in edges):
            out.add(eid)
    return out


def _stale_age(entry, as_of, max_age_days):
    """Age in days IF `entry` is stale under THE shared definition — a
    VOLATILE_CATEGORIES member whose effective last_verified is a valid date
    older than `max_age_days` — else None.

    The SINGLE per-entry staleness predicate: `stale_entries` (the advisory
    report), `trust_signals` (via `stale_entries`), and `staleness_factor` (the
    ranking downweight) all resolve "stale" through this one function, so the
    surfaced trust signal and the applied downweight can NEVER disagree about
    which entries are stale (Q4 — one definition, by construction). Strict/
    exclusive at the boundary (`age > max_age_days`). Pure + read-only; `as_of`
    explicit (reuses `date_age_days`, which returns None for an invalid/missing
    date on either side)."""
    if not isinstance(entry, dict):
        return None
    if (entry.get("category") or "") not in VOLATILE_CATEGORIES:
        return None
    age = date_age_days(entry_last_verified(entry), as_of)
    if age is None or age <= max_age_days:
        return None
    return age


# Bounded (0,1] downweight multipliers folded into the bm25+acr prior ALONGSIDE
# acr() (never baked into it), so a fresh, non-superseded entry keeps factor 1.0
# and its acr-weighted score stays BYTE-IDENTICAL (x * 1.0 == x in IEEE-754).
# Plain `bm25` NEVER calls staleness_factor — the gold-fixture invariant (C-3).
STALENESS_SUPERSEDED_MULT = 0.3   # mirrors _WORK_STATUS_MULT["superseded"]


STALENESS_STALE_MULT = 0.5        # a real penalty BELOW ACR_FRESHNESS_FLOOR (0.85)


def staleness_factor(entry, superseded, as_of, max_age_days=None):
    """Bounded (0,1] read-time downweight for a stale/superseded entry.

    `superseded` is the set from `superseded_ids(data)`, precomputed ONCE per
    ranking call. Precedence mirrors `trust_signals`: a superseded id DOMINATES
    (STALENESS_SUPERSEDED_MULT) — an entry a newer one explicitly replaced is
    suspect regardless of age — else a stale entry (the shared `_stale_age`
    predicate) gets STALENESS_STALE_MULT, else 1.0 (fresh, non-superseded → acr
    unchanged). Bounded in (0, 1]: never > 1.0 (can't promote) and never <= 0, so
    it can NEVER lift a zero-BM25 entry into results — relevance still gates
    upstream in `bm25_positive_scores` (C-3). `as_of` is explicit and validated
    (no hidden wall-clock into ranking, INV-1); `max_age_days=None` resolves to
    the shared STALE_MAX_AGE_DAYS at call time. Pure + read-only (INV-2); does
    NOT modify acr()/freshness()."""
    if not _is_valid_date(as_of):
        raise ValueError("as_of must be a YYYY-MM-DD date, got %r" % (as_of,))
    if max_age_days is None:
        max_age_days = STALE_MAX_AGE_DAYS
    eid = entry.get("id") if isinstance(entry, dict) else None
    if eid is not None and eid in superseded:
        return STALENESS_SUPERSEDED_MULT
    if _stale_age(entry, as_of, max_age_days) is not None:
        return STALENESS_STALE_MULT
    return 1.0


# Closed vocabulary of declared relation types for the `relates` graph edges.
# Edges are DECLARED-ONLY (never LLM-extracted): each token is "<type>:<target>"
# where target is another entry id. An out-of-vocab type is parsed/kept but
# flagged by the graph_integrity audit (advisory) — never silently dropped.
# `supersedes` is named because the read-time trust surface (`trust_signals`)
# keys off it: ONE spelling, so the ranking signal and this vocabulary can never
# drift apart (a bare literal there would fail silently on a typo).
SUPERSEDES_RELATION = "supersedes"


# --- Freshness & conflict detection (Phase 2.2) ------------------------------
# "Volatile" categories whose facts decay (R-FAIL-08): treat a point-in-time
# value as suspect once its last_verified is older than the staleness window.
VOLATILE_CATEGORIES = ("learnings", "configurations")


# The default `last_verified` age window, in days, past which a VOLATILE entry is
# reported stale. Lives here beside its only non-test consumer pair (this module's
# emitter and the `audit --stale` default) rather than in the read-only detector,
# which takes the window as a PARAMETER precisely so it never owns a policy value.
STALE_MAX_AGE_DAYS = 120         # `last_verified` older than this == stale


def stale_entries(data, max_age_days, today=None):
    """Return [(entry, age_days), ...] for volatile-category entries whose
    effective last_verified is older than max_age_days.

    Deterministic given `today` (defaults to the system date — reporting only,
    so wall-clock here is fine; INV-1 forbids wall-clock in RANKING, not in this
    advisory freshness report). Entries with no valid date are skipped. Ordering:
    oldest first, then id asc. Read-only (INV-2).
    """
    import datetime
    today = today or dates._today()
    try:
        today_d = datetime.date.fromisoformat(today)
    except ValueError:
        return []
    out = []
    for e in data.get("entries") or []:
        if not isinstance(e, dict):
            continue
        if (e.get("category") or "") not in VOLATILE_CATEGORIES:
            continue
        lv = index.entry_last_verified(e)
        if not dates._is_valid_date(lv):
            continue
        age = (today_d - datetime.date.fromisoformat(lv)).days
        if age > max_age_days:
            out.append((e, age))
    out.sort(key=lambda ea: ea[0].get("id") or "")          # id asc
    out.sort(key=lambda ea: ea[1], reverse=True)            # oldest first
    return out


_EXPORTS = [
    "VOLATILE_CATEGORIES",
    "STALE_MAX_AGE_DAYS",
    "stale_entries",
    "SUPERSEDES_RELATION",
    "STALENESS_STALE_MULT",
    "STALENESS_SUPERSEDED_MULT",
    "_trust_unknown",
    "_trust_record",
    "trust_signals",
    "superseded_ids",
    "_stale_age",
    "staleness_factor",
]

__all__ = list(_EXPORTS)
