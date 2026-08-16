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
    """
    if pin_floor is None or not scored:
        return scored
    top_score = scored[0][1] if scored else 0.0
    if top_score <= 0:
        return scored
    threshold = pin_floor * top_score
    best_skill_idx = None
    for i, (entry, score) in enumerate(scored):
        if entry.get("category") == "skills" and score >= threshold and score > 0:
            best_skill_idx = i
            break  # sorted by score desc, first skill above threshold wins
    if best_skill_idx is None or best_skill_idx == 0:
        return scored  # no qualifying skill, or already at top
    # Learnings surfaced BEFORE the pin (in the pre-pin top_k window) to protect.
    protected = set()
    if top_k is not None:
        for entry, _s in scored[:top_k]:
            if entry.get("category") == "learnings":
                protected.add(id(entry))
    # Pin: move the skill to position 0.
    skill_item = scored.pop(best_skill_idx)
    scored.insert(0, skill_item)
    # A single pin move pushes exactly one entry out of the window; if that entry
    # is a protected learning, swap it back in by evicting the lowest-ranked
    # non-learning, non-skill window entry instead.
    if top_k is not None and protected and top_k < len(scored):
        in_window = set(id(e) for (e, _s) in scored[:top_k])
        for oi in range(top_k, len(scored)):
            e = scored[oi][0]
            if id(e) in protected and id(e) not in in_window:
                victim = None
                for wi in range(top_k - 1, 0, -1):  # never evict the pinned skill (idx 0)
                    if scored[wi][0].get("category") not in ("learnings", "skills"):
                        victim = wi
                        break
                if victim is None:
                    break  # window is all learnings + the pinned skill; cannot protect
                learning_item = scored.pop(oi)     # oi > victim -> victim index stable
                victim_item = scored.pop(victim)
                scored.insert(victim, learning_item)
                scored.insert(top_k, victim_item)  # push the victim just out of window
                break  # only one entry can fall out from a single pin move
    return scored


_EXPORTS = [
    "SKILL_PIN_FLOOR",
    "_apply_skill_pin",
]

__all__ = list(_EXPORTS)
