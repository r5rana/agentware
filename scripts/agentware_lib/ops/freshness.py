"""agentware_lib.ops.freshness -- KB staleness + duplicate/conflict detection (layer: ops).

Owns: the advisory freshness + conflict-detection domain (Phase 2.2): the volatile-
category set (VOLATILE_CATEGORIES) whose facts decay per R-FAIL-08, the same-category
token-set Jaccard duplicate threshold (_CONFLICT_JACCARD), the two pure detectors --
stale_entries (volatile entries whose effective last_verified is older than the window)
and conflict_pairs (same-category pairs at/above the Jaccard threshold) -- plus the
advisory report assembler build_stale_report and its text renderer _print_stale_report.

Does NOT own: the `audit` command that CONSUMES this report -- cmd_audit + the _audit_*
checks stay RESIDENT in scripts/agentware this seam and move to agentware_lib.ops.audit,
which imports build_stale_report/_print_stale_report from HERE downward (this seam
UNBLOCKS that move). The date primitives it calls (_today/_is_valid_date) live in
agentware_lib.core.dates; entry_last_verified in agentware_lib.knowledge.index; the shared
tokenizer + corpus builder (tokenize/build_corpus) in agentware_lib.retrieval.corpus.

Layer deps: ops.freshness -> core.dates + knowledge.index + retrieval.corpus (all
downward). Nothing above ops imports it; ops.audit (and the deferred _alerts_kb) import it
downward. retrieval.corpus is bound as the alias `_corpus` (NOT `corpus`) because
conflict_pairs binds a LOCAL variable named `corpus` -- a `corpus.<fn>` owner-ref would
shadow it (the identical reason the entrypoint aliased it _corpus).

Determinism: read-only (INV-2) -- reporting only, NEVER auto-mutates/merges/deletes the
KB. stale_entries takes `today` as a param (defaults to dates._today(); this advisory
freshness report is allowed wall-clock -- INV-1 forbids wall-clock only in RANKING).
conflict_pairs is a pure function of the shared BM25 corpus tokens, so duplicate flagging
is stable/deterministic. stdlib only besides the three downward lib imports.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so the resident audit
consumers (cmd_audit -> build_stale_report/_print_stale_report), the deferred loop-alert
channel (_alerts_kb -> build_stale_report), the resident dream detect-report step
(_dream_step_detect_report -> build_stale_report) and the resident proposal near-dup check
(near_duplicate_proposal's `threshold=_CONFLICT_JACCARD` DEFAULT-ARG, evaluated at def
time) resolve every name as a bare name, and load_cli tests reach them as CLI.<name>.
Fan-in: 4 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import agentware_lib.core.dates as dates
import agentware_lib.knowledge.index as index
import agentware_lib.retrieval.corpus as _corpus
# The stale WINDOW and its scan live one layer down, in knowledge, because three
# consumers must agree on them: `audit --stale` (here), the read-time trust
# fields on every recall record, and the ranking downweight. A second constant is
# the failure outright — an entry reported stale by audit but ranked as current
# by recall is a system whose halves disagree about one fact.
from agentware_lib.knowledge.freshsignals import (   # noqa: F401  (re-export)
    STALE_MAX_AGE_DAYS,
    VOLATILE_CATEGORIES,
    stale_entries,
)
# Token-set Jaccard at/above this threshold flags a same-category pair as a
# possible duplicate/conflict. Reporting only; never auto-mutates the KB (INV-2).
_CONFLICT_JACCARD = 0.6


def conflict_pairs(kdir, data, threshold=_CONFLICT_JACCARD):
    """Return [(entry_a, entry_b, jaccard), ...] for same-category entry pairs
    whose token-set Jaccard >= threshold (possible duplicate/conflict).

    Token sets come from the shared corpus document (title+summary+tags+body)
    via the same tokenizer BM25 uses, so flagging is deterministic and stable.
    Read-only (INV-2); reporting only — never auto-deletes/merges. Ordering:
    highest similarity first, then (id_a, id_b) asc.
    """
    corpus = _corpus.build_corpus(kdir, data)
    items = [(e, set(_corpus.tokenize(text))) for (e, text) in corpus]
    pairs = []
    for i in range(len(items)):
        ei, si = items[i]
        if not si:
            continue
        for j in range(i + 1, len(items)):
            ej, sj = items[j]
            if not sj:
                continue
            if (ei.get("category") or "") != (ej.get("category") or ""):
                continue
            union = si | sj
            if not union:
                continue
            jac = len(si & sj) / len(union)
            if jac >= threshold:
                pairs.append((ei, ej, round(jac, 6)))
    pairs.sort(key=lambda p: ((p[0].get("id") or ""), (p[1].get("id") or "")))
    pairs.sort(key=lambda p: p[2], reverse=True)            # most similar first
    return pairs


def build_stale_report(kdir, data, max_age_days):
    """Build the advisory staleness + conflict report dict. Reporting only."""
    stale = stale_entries(data, max_age_days)
    conflicts = conflict_pairs(kdir, data)
    return {
        "max_age_days": max_age_days,
        "volatile_categories": list(VOLATILE_CATEGORIES),
        "stale": [
            {"id": e.get("id"), "category": e.get("category"),
             "path": e.get("path"), "last_verified": index.entry_last_verified(e),
             "age_days": age}
            for (e, age) in stale
        ],
        "conflicts": [
            {"a": a.get("id"), "b": b.get("id"),
             "category": a.get("category"), "jaccard": jac}
            for (a, b, jac) in conflicts
        ],
    }


def _print_stale_report(report):
    print("STALE REPORT (advisory — reporting only, no mutation):")
    print("  window: last_verified older than %d day(s) in %s" % (
        report["max_age_days"], ", ".join(report["volatile_categories"])))
    if report["stale"]:
        print("  stale entries (%d):" % len(report["stale"]))
        for s in report["stale"]:
            print("    - %s [%s] last_verified=%s age=%dd" % (
                s["id"], s["category"], s["last_verified"], s["age_days"]))
    else:
        print("  stale entries: none")
    if report["conflicts"]:
        print("  possible duplicate/conflict pairs (%d, Jaccard >= %.2f):" % (
            len(report["conflicts"]), _CONFLICT_JACCARD))
        for c in report["conflicts"]:
            print("    - %s <-> %s [%s] jaccard=%.4f" % (
                c["a"], c["b"], c["category"], c["jaccard"]))
    else:
        print("  possible duplicate/conflict pairs: none")


_EXPORTS = [
    "VOLATILE_CATEGORIES", "STALE_MAX_AGE_DAYS", "_CONFLICT_JACCARD",
    "stale_entries", "conflict_pairs",
    "build_stale_report", "_print_stale_report",
]
__all__ = list(_EXPORTS)
