"""Owning unit file for `agentware_lib.retrieval.pinpolicy` — task 99 wave 9.

The module had no owning file before this one: its behaviour was reachable only
through `skillpin.recall_ranked` and `bm25.retrieve_bm25`, both of which observe
a RANKED LIST and none of the decisions that produce it. That is why the row it
carried was 22 branches in sixty lines — the size was the symptom; a body whose
only witness is two callers away is the cause.

What is asserted here is the module's PROMISE, not its shape:

  * THE PIN. A skill scoring within `SKILL_PIN_FLOOR` of the best overall score
    is promoted to position 0. One merely present, or scoring far below the best
    match, is left exactly where it ranked.
  * LEARNINGS ARE NEVER EVICTED (R-RET-03). Every learning inside the PRE-pin
    window is still inside the POST-pin window. This is a postcondition, and it
    is asserted as one — a test that pins the resulting ORDER passes for the
    wrong reason the moment the tie-break changes.
  * AND WHERE IT CANNOT HOLD, IT GIVES UP RATHER THAN LYING. When the window is
    learnings plus the pinned skill there is no eviction that does not break one
    of the two rules, so the repair declines to act. That case is asserted
    explicitly, because "the guarantee holds unless it doesn't" is only
    trustworthy if the exception is named.
  * IDENTITY, NOT EQUALITY. Protection is keyed on `id()` — the objects this
    call was handed — so a corpus carrying two entries with the same `id` FIELD
    protects only the one that was actually in the window.

Stdlib-only, hermetic: pure list surgery, no I/O, no clock, no KB.
"""

from tests._paths import TESTS_DIR

import os
import sys
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.retrieval.pinpolicy as pinpolicy  # noqa: E402


def _e(eid, category):
    """A minimal entry: the pin reads `category` and nothing else."""
    return {"id": eid, "category": category}


def _ids(scored):
    return [e.get("id") for (e, _s) in scored]


class PinFloorConstantTest(unittest.TestCase):
    """The threshold is a published constant, not a magic number inline."""

    def test_pinpolicy_floor_is_enabled_at_a_relative_fraction(self):
        """The floor is a FRACTION of the top score, so it is scale-free: a
        corpus whose best score is 3.2 and one whose best is 32.0 pin the same
        way. A value above 1.0 could never be reached and is therefore a
        disabled pin written to look enabled."""
        self.assertIsInstance(pinpolicy.SKILL_PIN_FLOOR, float)
        self.assertGreater(pinpolicy.SKILL_PIN_FLOOR, 0.0)
        self.assertLessEqual(pinpolicy.SKILL_PIN_FLOOR, 1.0)

    def test_pinpolicy_exports_name_every_public_helper(self):
        """`_EXPORTS` is the module's published surface; a helper missing from
        it is one the facade cannot resolve by name."""
        for name in ("SKILL_PIN_FLOOR", "_first_qualifying_skill",
                     "_protected_learning_ids", "_window_victim",
                     "_reprotect_learnings", "_apply_skill_pin"):
            self.assertIn(name, pinpolicy._EXPORTS)
            self.assertTrue(hasattr(pinpolicy, name))


class FirstQualifyingSkillTest(unittest.TestCase):
    """`_first_qualifying_skill` — which skill, if any, earns the pin."""

    def test_pinpolicy_empty_list_qualifies_nothing(self):
        self.assertIsNone(pinpolicy._first_qualifying_skill([], 0.0))

    def test_pinpolicy_corpus_without_a_skill_qualifies_nothing(self):
        scored = [(_e("a", "learnings"), 9.0), (_e("b", "configurations"), 8.0)]
        self.assertIsNone(pinpolicy._first_qualifying_skill(scored, 1.0))

    def test_pinpolicy_skill_below_threshold_does_not_qualify(self):
        """A skill that is merely PRESENT does not get promoted; the whole point
        of a floor is that a weak match stays where it ranked."""
        scored = [(_e("a", "learnings"), 9.0), (_e("s", "skills"), 1.0)]
        self.assertIsNone(pinpolicy._first_qualifying_skill(scored, 7.65))

    def test_pinpolicy_skill_exactly_at_threshold_qualifies(self):
        """The comparison is `>=`: a skill sitting exactly on the floor is
        inside it. An off-by-one here silently disables the pin for the corpus
        the floor was tuned against."""
        scored = [(_e("a", "learnings"), 10.0), (_e("s", "skills"), 8.5)]
        self.assertEqual(pinpolicy._first_qualifying_skill(scored, 8.5), 1)

    def test_pinpolicy_zero_scoring_skill_never_qualifies_at_a_zero_floor(self):
        """The `> 0` test is NOT redundant with the threshold. A `pin_floor` of
        0.0 makes the threshold 0.0, and `0.0 >= 0.0` is true — so without the
        second test an entry that matched NOTHING would be pinned to the top."""
        scored = [(_e("a", "learnings"), 0.0), (_e("s", "skills"), 0.0)]
        self.assertIsNone(pinpolicy._first_qualifying_skill(scored, 0.0))

    def test_pinpolicy_first_skill_wins_over_a_later_one(self):
        """The list is score-sorted, so the first qualifying skill is the best
        one and the scan stops. Two skills above the floor must resolve to the
        earlier index."""
        scored = [(_e("a", "learnings"), 9.0), (_e("s1", "skills"), 8.8),
                  (_e("s2", "skills"), 8.7)]
        self.assertEqual(pinpolicy._first_qualifying_skill(scored, 8.0), 1)

    def test_pinpolicy_a_skill_already_at_the_top_reports_index_zero(self):
        """Reporting 0 rather than None is what lets the caller distinguish
        "nothing qualified" from "the qualifier is already pinned" — both are
        no-ops, but only one of them means the pin is working."""
        scored = [(_e("s", "skills"), 9.0), (_e("a", "learnings"), 8.9)]
        self.assertEqual(pinpolicy._first_qualifying_skill(scored, 8.0), 0)

    def test_pinpolicy_a_high_scoring_non_skill_is_not_a_qualifier(self):
        """Category is load-bearing: the pin promotes PROCEDURES, and a
        reference outscoring everything is still not one."""
        scored = [(_e("r", "references"), 9.0), (_e("s", "skills"), 1.0)]
        self.assertIsNone(pinpolicy._first_qualifying_skill(scored, 8.0))

    def test_pinpolicy_entry_without_a_category_is_not_a_skill(self):
        """`.get` rather than `[...]`: a pseudo-entry with no category must be
        ignored, not crash the ranking path that produced it."""
        scored = [({"id": "x"}, 9.0), (_e("s", "skills"), 8.9)]
        self.assertEqual(pinpolicy._first_qualifying_skill(scored, 8.0), 1)


class ProtectedLearningIdsTest(unittest.TestCase):
    """`_protected_learning_ids` — what the caller can currently see."""

    def test_pinpolicy_no_window_protects_nothing(self):
        """`top_k=None` is the eval/gate path: it surfaces everything, so
        nothing can fall out and there is nothing to protect."""
        scored = [(_e("L", "learnings"), 9.0)]
        self.assertEqual(pinpolicy._protected_learning_ids(scored, None), set())

    def test_pinpolicy_only_learnings_inside_the_window_are_protected(self):
        inside, outside = _e("L1", "learnings"), _e("L2", "learnings")
        scored = [(inside, 9.0), (_e("c", "configurations"), 8.0),
                  (outside, 7.0)]
        got = pinpolicy._protected_learning_ids(scored, 2)
        self.assertEqual(got, {id(inside)})
        self.assertNotIn(id(outside), got)

    def test_pinpolicy_non_learnings_in_the_window_are_not_protected(self):
        skill, conf = _e("s", "skills"), _e("c", "configurations")
        scored = [(skill, 9.0), (conf, 8.0)]
        self.assertEqual(pinpolicy._protected_learning_ids(scored, 2), set())

    def test_pinpolicy_protection_is_object_identity_not_the_id_field(self):
        """Two entries may legitimately carry the same `id` STRING in a corpus
        that was never deduplicated. Protection is about the objects this call
        was handed, so only the one actually in the window is protected."""
        a = {"id": "dup", "category": "learnings"}
        b = {"id": "dup", "category": "learnings"}
        got = pinpolicy._protected_learning_ids([(a, 9.0), (b, 8.0)], 1)
        self.assertEqual(got, {id(a)})
        self.assertNotIn(id(b), got)

    def test_pinpolicy_window_wider_than_the_list_is_not_an_error(self):
        scored = [(_e("L", "learnings"), 9.0)]
        self.assertEqual(len(pinpolicy._protected_learning_ids(scored, 99)), 1)


class WindowVictimTest(unittest.TestCase):
    """`_window_victim` — who may be evicted to keep a learning."""

    def test_pinpolicy_victim_is_the_lowest_ranked_eligible_entry(self):
        """Bottom-up, so the entry that loses its place is always the
        least-well-ranked one that may lose it."""
        scored = [(_e("s", "skills"), 9.0), (_e("c1", "configurations"), 8.0),
                  (_e("c2", "configurations"), 7.0)]
        self.assertEqual(pinpolicy._window_victim(scored, 3), 2)

    def test_pinpolicy_victim_is_never_a_learning(self):
        """Evicting a learning to protect a learning is the defect the repair
        exists to prevent."""
        scored = [(_e("s", "skills"), 9.0), (_e("c", "configurations"), 8.0),
                  (_e("L", "learnings"), 7.0)]
        self.assertEqual(pinpolicy._window_victim(scored, 3), 1)

    def test_pinpolicy_victim_is_never_a_skill(self):
        """Demoting a skill to protect a learning would undo R-RET-03's
        precedence in the same call that just applied it."""
        scored = [(_e("s1", "skills"), 9.0), (_e("c", "configurations"), 8.0),
                  (_e("s2", "skills"), 7.0)]
        self.assertEqual(pinpolicy._window_victim(scored, 3), 1)

    def test_pinpolicy_victim_is_never_position_zero(self):
        """Position 0 holds the entry the pin just put there. A window whose
        only non-learning is at 0 has NO victim."""
        scored = [(_e("c", "configurations"), 9.0), (_e("L", "learnings"), 8.0)]
        self.assertIsNone(pinpolicy._window_victim(scored, 2))

    def test_pinpolicy_all_learning_window_has_no_victim(self):
        scored = [(_e("s", "skills"), 9.0), (_e("L1", "learnings"), 8.0),
                  (_e("L2", "learnings"), 7.0)]
        self.assertIsNone(pinpolicy._window_victim(scored, 3))

    def test_pinpolicy_victim_search_never_looks_past_the_window(self):
        """An eligible entry BELOW the window is not a victim: evicting it would
        change nothing the caller can see, and the repair would then believe it
        had made room when it had not."""
        scored = [(_e("s", "skills"), 9.0), (_e("L", "learnings"), 8.0),
                  (_e("c", "configurations"), 7.0)]
        self.assertIsNone(pinpolicy._window_victim(scored, 2))


class ReprotectLearningsTest(unittest.TestCase):
    """`_reprotect_learnings` — the repair, and its own preconditions."""

    def test_pinpolicy_repair_without_a_window_is_a_no_op(self):
        scored = [(_e("s", "skills"), 9.0), (_e("L", "learnings"), 8.0)]
        before = list(scored)
        pinpolicy._reprotect_learnings(scored, {id(scored[1][0])}, None)
        self.assertEqual(scored, before)

    def test_pinpolicy_repair_with_nothing_protected_is_a_no_op(self):
        scored = [(_e("s", "skills"), 9.0), (_e("c", "configurations"), 8.0)]
        before = list(scored)
        pinpolicy._reprotect_learnings(scored, set(), 1)
        self.assertEqual(scored, before)

    def test_pinpolicy_repair_is_a_no_op_when_the_window_covers_everything(self):
        """Nothing can fall out of a window as wide as the list, so there is
        nothing to put back."""
        L = _e("L", "learnings")
        scored = [(_e("s", "skills"), 9.0), (L, 8.0)]
        before = list(scored)
        pinpolicy._reprotect_learnings(scored, {id(L)}, 2)
        self.assertEqual(scored, before)

    def test_pinpolicy_repair_swaps_the_learning_back_to_the_victims_rank(self):
        """The learning takes the victim's PLACE and the victim is pushed just
        past the window edge — everything else keeps its rank."""
        L = _e("L", "learnings")
        scored = [(_e("s", "skills"), 9.0), (_e("c1", "configurations"), 8.8),
                  (_e("c2", "configurations"), 8.7), (L, 8.6),
                  (_e("r", "references"), 1.0)]
        pinpolicy._reprotect_learnings(scored, {id(L)}, 3)
        self.assertEqual(_ids(scored), ["s", "c1", "L", "c2", "r"])

    def test_pinpolicy_repair_gives_up_when_there_is_no_eligible_victim(self):
        """Named explicitly: the guarantee has exactly one exception, and a
        silent one would be indistinguishable from a broken repair."""
        L3 = _e("L3", "learnings")
        scored = [(_e("s", "skills"), 9.0), (_e("L1", "learnings"), 8.8),
                  (_e("L2", "learnings"), 8.7), (L3, 8.6)]
        pinpolicy._reprotect_learnings(scored, {id(L3)}, 3)
        self.assertEqual(_ids(scored), ["s", "L1", "L2", "L3"])

    def test_pinpolicy_repair_ignores_a_protected_learning_still_in_window(self):
        """A learning that never fell out needs no repair; acting on it would
        reorder a window nothing was wrong with.

        Reachable only when the SAME entry object sits both inside and outside
        the window, which is why the fixture puts one there deliberately: with
        one object per rank the scan already starts past the window edge, so the
        in-window test would be unfalsifiable and a guard nothing can kill is a
        guard nobody can trust. Here the learning is still visible at rank 2, so
        the copy at rank 4 must NOT trigger a swap — without the test the repair
        promotes the duplicate and demotes a configuration for nothing.
        """
        L = _e("L", "learnings")
        scored = [(_e("s", "skills"), 9.0), (L, 8.8),
                  (_e("c", "configurations"), 8.7),
                  (_e("d", "configurations"), 1.0), (L, 0.5)]
        pinpolicy._reprotect_learnings(scored, {id(L)}, 3)
        self.assertEqual(_ids(scored), ["s", "L", "c", "d", "L"])

    def test_pinpolicy_repair_leaves_a_window_resident_learning_alone(self):
        """The simple shape of the same rule: nothing outside the window is
        protected, so there is nothing to put back."""
        L = _e("L", "learnings")
        scored = [(_e("s", "skills"), 9.0), (L, 8.8),
                  (_e("c", "configurations"), 8.7)]
        before = list(scored)
        pinpolicy._reprotect_learnings(scored, {id(L)}, 2)
        self.assertEqual(scored, before)

    def test_pinpolicy_repair_runs_at_most_once(self):
        """A single pin move pushes exactly ONE entry out, so a second swap
        would be repairing damage the pin did not do."""
        L1, L2 = _e("L1", "learnings"), _e("L2", "learnings")
        scored = [(_e("s", "skills"), 9.0), (_e("c1", "configurations"), 8.8),
                  (_e("c2", "configurations"), 8.7), (L1, 8.6), (L2, 8.5)]
        pinpolicy._reprotect_learnings(scored, {id(L1), id(L2)}, 3)
        self.assertEqual(_ids(scored), ["s", "c1", "L1", "c2", "L2"])

    def test_pinpolicy_repair_returns_nothing_and_mutates_in_place(self):
        L = _e("L", "learnings")
        scored = [(_e("s", "skills"), 9.0), (_e("c", "configurations"), 8.0),
                  (L, 7.0)]
        self.assertIsNone(pinpolicy._reprotect_learnings(scored, {id(L)}, 2))
        self.assertEqual(_ids(scored), ["s", "L", "c"])


class ApplySkillPinTest(unittest.TestCase):
    """`_apply_skill_pin` — the composed promise."""

    def test_pinpolicy_disabled_floor_returns_the_input_untouched(self):
        scored = [(_e("a", "learnings"), 9.0), (_e("s", "skills"), 8.5)]
        out = pinpolicy._apply_skill_pin(scored, None, top_k=3)
        self.assertIs(out, scored)
        self.assertEqual(_ids(out), ["a", "s"])

    def test_pinpolicy_empty_input_is_returned_as_is(self):
        scored = []
        self.assertIs(pinpolicy._apply_skill_pin(scored, 0.85, top_k=3), scored)

    def test_pinpolicy_all_zero_scores_are_not_a_ranking(self):
        """A top score of zero means nothing matched; pinning inside that is
        promoting noise, and the relative floor is undefined there anyway."""
        scored = [(_e("a", "learnings"), 0.0), (_e("s", "skills"), 0.0)]
        self.assertEqual(_ids(pinpolicy._apply_skill_pin(scored, 0.85, 3)),
                         ["a", "s"])

    def test_pinpolicy_qualifying_skill_is_promoted_to_position_zero(self):
        scored = [(_e("a", "configurations"), 9.0), (_e("s", "skills"), 8.6)]
        self.assertEqual(_ids(pinpolicy._apply_skill_pin(scored, 0.85, 3)),
                         ["s", "a"])

    def test_pinpolicy_skill_far_below_the_floor_keeps_its_rank(self):
        scored = [(_e("a", "configurations"), 9.0), (_e("s", "skills"), 1.0)]
        self.assertEqual(_ids(pinpolicy._apply_skill_pin(scored, 0.85, 3)),
                         ["a", "s"])

    def test_pinpolicy_promotion_preserves_the_order_of_everything_else(self):
        """The pin is a MOVE, not a re-sort: the remaining entries keep their
        relative order, so a pinned result is still a ranked one."""
        scored = [(_e("a", "configurations"), 9.0),
                  (_e("b", "configurations"), 8.9),
                  (_e("s", "skills"), 8.8),
                  (_e("c", "configurations"), 8.7)]
        self.assertEqual(_ids(pinpolicy._apply_skill_pin(scored, 0.85, 4)),
                         ["s", "a", "b", "c"])

    def test_pinpolicy_scores_travel_with_their_entries(self):
        """A move that reorders entries but not scores would produce a list
        whose ranking is arithmetically wrong while LOOKING sorted."""
        scored = [(_e("a", "configurations"), 9.0), (_e("s", "skills"), 8.6)]
        out = pinpolicy._apply_skill_pin(scored, 0.85, 3)
        self.assertEqual([(e["id"], s) for (e, s) in out],
                         [("s", 8.6), ("a", 9.0)])

    def test_pinpolicy_mutates_in_place_and_returns_the_same_list(self):
        """Both are contract: `recall_ranked` reads the return value and
        `retrieve_bm25` reads the list it passed in. A refactor that starts
        returning a COPY breaks the second caller and no return-value-only test
        would notice."""
        scored = [(_e("a", "configurations"), 9.0), (_e("s", "skills"), 8.6)]
        out = pinpolicy._apply_skill_pin(scored, 0.85, 3)
        self.assertIs(out, scored)
        self.assertEqual(_ids(scored), ["s", "a"])

    def test_pinpolicy_learning_in_window_survives_the_pin(self):
        """THE postcondition, asserted as one rather than inferred from the
        resulting order."""
        L = _e("L", "learnings")
        scored = [(_e("a", "configurations"), 9.0),
                  (_e("b", "configurations"), 8.8), (L, 8.7),
                  (_e("s", "skills"), 8.6), (_e("r", "references"), 1.0)]
        pre = set(id(e) for (e, _s) in scored[:3]
                  if e.get("category") == "learnings")
        out = pinpolicy._apply_skill_pin(scored, 0.85, top_k=3)
        post = set(id(e) for (e, _s) in out[:3])
        self.assertTrue(pre <= post)
        self.assertEqual(_ids(out), ["s", "a", "L", "b", "r"])

    def test_pinpolicy_non_learning_eviction_needs_no_repair(self):
        """When the entry pushed out is a configuration, the plain pop+insert
        stands: the repair must not fire and reorder a correct window."""
        scored = [(_e("a", "configurations"), 9.0),
                  (_e("b", "configurations"), 8.8),
                  (_e("c", "configurations"), 8.7),
                  (_e("s", "skills"), 8.6), (_e("d", "references"), 1.0)]
        self.assertEqual(_ids(pinpolicy._apply_skill_pin(scored, 0.85, 3)),
                         ["s", "a", "b", "c", "d"])

    def test_pinpolicy_eval_path_without_a_window_still_pins(self):
        """`top_k=None` (retrieve_bm25) must still apply the pin — a pin the
        interactive path applies and the evaluation path does not is a pin the
        release gate cannot measure."""
        scored = [(_e("a", "configurations"), 9.0), (_e("L", "learnings"), 8.8),
                  (_e("s", "skills"), 8.7)]
        self.assertEqual(_ids(pinpolicy._apply_skill_pin(scored, 0.85, None)),
                         ["s", "a", "L"])

    def test_pinpolicy_repeated_application_is_idempotent(self):
        """The second call finds the skill already at 0 and returns early, so a
        double-applied pin cannot rotate the list."""
        scored = [(_e("a", "configurations"), 9.0), (_e("s", "skills"), 8.6)]
        once = _ids(pinpolicy._apply_skill_pin(scored, 0.85, 3))
        twice = _ids(pinpolicy._apply_skill_pin(scored, 0.85, 3))
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()
