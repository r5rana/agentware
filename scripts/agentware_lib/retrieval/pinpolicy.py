"""agentware_lib.retrieval.pinpolicy -- the R-RET-03 stage-1 SKILL PIN (layer: retrieval).

Owns: `SKILL_PIN_FLOOR` (the threshold, ENABLED at 0.85) and `_apply_skill_pin`
(the pin itself, including the learnings-eviction guarantee). Nothing else.
Does NOT own: the ranking core that calls it -> retrieval.skillpin; the plain
BM25 retriever that also calls it on the eval/gate path -> retrieval.bm25; the
ACR prior or the staleness downweight it composes with -> retrieval.acr /
knowledge.freshsignals.

WHY THIS IS ITS OWN MODULE
--------------------------
The pin must run at THREE call sites, and two of them cannot import each other:
`retrieval.skillpin.recall_ranked` (both the plain and the bm25+embed branches)
and `retrieval.bm25.retrieve_bm25` (the eval/gate path). `skillpin` already
imports `bm25`, so putting the pin in `skillpin` and calling it from `bm25`
closes an import cycle. Extracting it here is what lets ALL THREE sites share ONE
implementation -- which is the whole point: a pin the interactive path applies and
the evaluation path does not is a pin the release gate cannot measure, and an
unmeasured ranking change is one that regresses silently.

WHY THE PIN EXISTS
------------------
R-RET-03 says a directly-matched SKILL takes precedence: it is a PROCEDURE to
follow, not one more fact to weigh. BM25 alone does not know that -- a skill and a
learning that match a query equally well score equally well. The pin promotes a
skill scoring within `SKILL_PIN_FLOOR` of the top overall score to position 0,
and only then; a skill that is merely present, or scores far below the best
match, is left where it ranked.

WHY LEARNINGS ARE NEVER EVICTED
-------------------------------
The naive pin is a pop-and-insert, and at a small `top_k` that pushes whatever
sat at rank `top_k-1` out of the surfaced window. If that entry is a LEARNING,
the pin has just hidden the prior problem-and-error context that R-RET-03
guarantees is always included -- to make room for a procedure. That is the wrong
trade in exactly the situation the operator most needs both. So when `top_k` is
given, any learning inside the PRE-pin window is guaranteed to remain in the
POST-pin window, and the lowest-ranked non-learning, non-skill entry is evicted
instead.

Layer deps: none. Pure list surgery over `[(entry, score), ...]` -- no I/O, no
clock, no imports beyond stdlib. That is also why it can sit below both callers.
Determinism: pure and order-preserving apart from the single documented move, so
a given scored list pins identically on every run (INV-1).
"""


# --- Skill pin (R-RET-03 stage-1 precedence) ----------------------------------
# When set to a float (e.g. 0.85), a skill whose score >= floor * top_overall_score
# AND > 0 is pinned to position 0 (the "matched procedure — follow it" behavior).
# Learnings are NEVER evicted (see _apply_skill_pin). ENABLED (feature 260713-p4):
# the pin is applied in the SHARED ranking core so BOTH recall_ranked (plain-bm25
# AND bm25+embed branches) AND the eval/gate retrieve_bm25 path exercise it — the
# release gate can therefore measure it, and the flip is gated by a self-contained
# gold-fixture Recall@5 no-regression check (R-PKG-06).
SKILL_PIN_FLOOR = 0.85


def _first_qualifying_skill(scored, threshold):
    """Index of the first SKILL scoring at or above `threshold` AND above zero,
    or None when no entry qualifies.

    `scored` is sorted by score descending, so the FIRST match is the best one
    and the scan stops there — which is why this is a search rather than a max:
    a later skill can never beat an earlier one, and walking on would only cost
    time. The `> 0` test is not redundant with the threshold: a `pin_floor` of
    0.0 makes `threshold` 0.0, and a zero-scoring skill matches `>= 0.0`.
    """
    for i, (entry, score) in enumerate(scored):
        if entry.get("category") == "skills" and score >= threshold and score > 0:
            return i
    return None


def _protected_learning_ids(scored, top_k):
    """The identities of the learnings the caller can currently SEE — those
    inside the PRE-pin `top_k` window. Empty when there is no window (`top_k`
    None: the eval/gate path surfaces everything, so nothing can fall out).

    Keyed on `id()` rather than the entry's `id` FIELD: the guarantee is about
    the objects this call was handed, and two distinct entries may legitimately
    carry the same id string in a corpus that has not been deduplicated. Must be
    computed BEFORE the pin moves anything, since after the move the window no
    longer describes what the caller saw.
    """
    if top_k is None:
        return set()
    return set(id(entry) for (entry, _s) in scored[:top_k]
               if entry.get("category") == "learnings")


def _window_victim(scored, top_k):
    """The lowest-ranked window entry eligible for eviction, or None.

    Eligible means: not a learning (evicting one is the very thing the repair
    exists to prevent), not a skill (the pin just promoted one, and demoting a
    skill to protect a learning would undo R-RET-03's precedence), and not
    position 0 (which holds the pinned skill itself). The scan runs from the
    window's BOTTOM upward, so the entry that loses its place is always the
    least-well-ranked one that may lose it.

    None means the window is learnings plus the pinned skill and nothing else:
    there is no eviction that does not break one of the two rules, and the
    caller must GIVE UP rather than pick the least-bad victim.
    """
    for wi in range(top_k - 1, 0, -1):
        if scored[wi][0].get("category") not in ("learnings", "skills"):
            return wi
    return None


def _reprotect_learnings(scored, protected, top_k):
    """Restore the learnings-are-never-evicted guarantee after the pin move.

    A single pin move pushes exactly ONE entry out of the `top_k` window, so at
    most one protected learning can have fallen out and the repair runs at most
    once — which is why both arms below leave immediately. If that entry is a
    protected learning it is swapped back to the victim's rank and the victim is
    pushed just past the window edge, preserving the rank order of everything
    else. Mutates `scored` in place; returns nothing.

    Owns its own preconditions rather than being guarded at the call site: this
    is the ONE place that knows a repair is meaningless without a window
    (`top_k` None), without anything to protect (`protected` empty), or when the
    window covers the whole list (nothing can fall out of it).
    """
    if top_k is None or not protected or top_k >= len(scored):
        return
    in_window = set(id(e) for (e, _s) in scored[:top_k])
    for oi in range(top_k, len(scored)):
        e = scored[oi][0]
        if id(e) not in protected or id(e) in in_window:
            continue
        victim = _window_victim(scored, top_k)
        if victim is None:
            return  # window is all learnings + the pinned skill; cannot protect
        learning_item = scored.pop(oi)     # oi > victim -> victim index stable
        victim_item = scored.pop(victim)
        scored.insert(victim, learning_item)
        scored.insert(top_k, victim_item)  # push the victim just out of window
        return  # only one entry can fall out from a single pin move


def _apply_skill_pin(scored, pin_floor, top_k=None):
    """Apply the threshold-gated skill pin to a scored list.

    `scored` is [(entry, score), ...] already sorted by BM25+tie-break.
    If `pin_floor` is None (disabled), returns scored unchanged.
    Otherwise: find the top skill; if skill_score >= pin_floor * top_overall_score
    AND > 0, move it to position 0 (pinned). Deterministic; read-only.

    LEARNINGS ARE NEVER EVICTED (R-RET-03, feature 260713-p4 R4-3): the plain
    pop+insert can push whatever sat at rank `top_k-1` out of the surfaced window.
    When `top_k` is given, any learning that was in the PRE-pin top_k window is
    guaranteed to remain in the POST-pin window — if the pin would drop one, the
    lowest-ranked non-learning, non-skill entry in the window is evicted instead.
    Callers that trim to `top_k` (recall_ranked) pass it; the eval/gate path
    (retrieve_bm25 with top_k=None) is guarded by the self-contained pin
    regression check.

    That guarantee is a POSTCONDITION of one repair step, not a property of any
    single branch, which is why the repair lives in `_reprotect_learnings` and is
    asserted directly rather than inferred from the order this function returns.
    Mutates `scored` in place AND returns it — every caller relies on the return
    value, and one (`retrieve_bm25`) also reads the list it passed in.
    """
    if pin_floor is None or not scored:
        return scored
    top_score = scored[0][1]
    if top_score <= 0:
        return scored
    best_skill_idx = _first_qualifying_skill(scored, pin_floor * top_score)
    if best_skill_idx is None or best_skill_idx == 0:
        return scored  # no qualifying skill, or already at top
    # Learnings surfaced BEFORE the pin (in the pre-pin top_k window) to protect.
    protected = _protected_learning_ids(scored, top_k)
    # Pin: move the skill to position 0.
    skill_item = scored.pop(best_skill_idx)
    scored.insert(0, skill_item)
    # A single pin move pushes exactly one entry out of the window; if that entry
    # is a protected learning, swap it back in by evicting the lowest-ranked
    # non-learning, non-skill window entry instead.
    _reprotect_learnings(scored, protected, top_k)
    return scored


_EXPORTS = [
    "SKILL_PIN_FLOOR",
    "_first_qualifying_skill",
    "_protected_learning_ids",
    "_window_victim",
    "_reprotect_learnings",
    "_apply_skill_pin",
]

__all__ = list(_EXPORTS)
