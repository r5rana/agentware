"""The CONSTANT BASELINE suite (plan task 31) — capture and fixture shape.

The differ's row count is only as trustworthy as its normalisation, and this
migration has already been burned once by trusting a count instead of a
derivation: the plan's "243 divergent bodies" (task 30) turned out to be a
number no normalisation reproduced. The plan's "251 constants / 16 divergences"
is the same kind of number. So what COUNTS as a constant, and what its literal
is, are pinned here BY EXAMPLE — the 265 rows in the fixture are re-derivable
from these assertions rather than inherited from an audit.

The rest of this file checks the committed artifact is well formed: every row
adjudicated, every decision reasoned, every owner named, and no operator path
leaked into a file that gets committed.

The gate's teeth — that it actually BITES — live in `test_constant_differ.py`.

Stdlib only, hermetic, deterministic.
"""

import json
import os
import sys
import unittest

try:
    from tests import _constant_differ as cd
    from tests import _constant_adjudication as adj
except ImportError:  # allow `python3 -m unittest tests.test_constant_baseline`
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _constant_differ as cd
    import _constant_adjudication as adj


class CaptureTest(unittest.TestCase):
    """The normalisation, pinned by example rather than by prose."""

    @staticmethod
    def _names(src):
        return sorted(cd.constants_in(src, "m.py"))

    def test_upper_snake_public_and_private_are_constants(self):
        self.assertEqual(self._names("A = 1\n_B = 2\nC_D_2 = 3\n"),
                         ["A", "C_D_2", "_B"])

    def test_lowercase_and_dunder_and_mixed_case_are_not(self):
        self.assertEqual(self._names("a = 1\n__all__ = []\nCamelCase = 2\n"), [])

    def test_annotated_assignment_with_a_value_counts(self):
        self.assertEqual(self._names("A: int = 1\n"), ["A"])

    def test_annotated_declaration_without_a_value_does_not(self):
        self.assertEqual(self._names("A: int\n"), [])

    def test_tuple_unpacking_claims_every_upper_target(self):
        self.assertEqual(self._names("A, B = 1, 2\n"), ["A", "B"])

    def test_nested_scopes_are_not_module_constants(self):
        src = "class K:\n    A = 1\n\ndef f():\n    B = 2\n"
        self.assertEqual(self._names(src), [])

    def test_literal_is_unparsed_so_formatting_is_not_a_divergence(self):
        one = cd.constants_in("A = (1,\n     2)\n", "m.py")["A"][0][1]
        two = cd.constants_in('A = (\n  1, 2)\n', "m.py")["A"][0][1]
        self.assertEqual(one, two)

    def test_a_deletion_cannot_hide_behind_a_reformat(self):
        one = cd.constants_in("A = (1, 2)\n", "m.py")["A"][0][1]
        two = cd.constants_in("A = (1,)\n", "m.py")["A"][0][1]
        self.assertNotEqual(one, two)

    def test_representative_is_the_longest_literal_deterministically(self):
        sites = [("a.py", "()"), ("b.py", "(1, 2, 3)")]
        self.assertEqual(cd.representative(sites), "(1, 2, 3)")
        self.assertEqual(cd.distinct(sites), 2)


class FixtureShapeTest(unittest.TestCase):
    """The committed baseline is well-formed and fully adjudicated."""

    @classmethod
    def setUpClass(cls):
        cls.fx = cd.load_fixture()

    def test_schema_and_normalisation_are_recorded_in_the_artifact(self):
        self.assertEqual(self.fx["schema"], cd.SCHEMA)
        self.assertEqual(self.fx["normalisation"], cd.NORMALISATION)
        self.assertIn("legacy", self.fx["scope"])
        self.assertIn("lib", self.fx["scope"])

    def test_counts_agree_with_the_rows(self):
        self.assertEqual(self.fx["counts"]["legacy_only"], len(self.fx["legacy_only"]))
        self.assertEqual(self.fx["counts"]["divergent"], len(self.fx["divergent"]))

    def test_every_legacy_only_row_carries_a_status_and_an_owner(self):
        for row in self.fx["legacy_only"]:
            self.assertIn(row["status"], cd.STATUSES, row["name"])
            self.assertRegex(row["follow_up"], r"^task \d+$", row["name"])
            self.assertTrue(row["literal"], row["name"])

    def test_every_excluded_row_carries_a_reason(self):
        for row in self.fx["legacy_only"]:
            if row["status"] == "excluded":
                self.assertTrue(row.get("reason"), row["name"])

    def test_every_divergence_carries_a_decision_and_a_rationale(self):
        for row in self.fx["divergent"]:
            self.assertIn(row["decision"], cd.DECISIONS, row["name"])
            self.assertTrue(row["rationale"], row["name"])
            self.assertNotEqual(row["legacy_literal"], row["lib_literal"])

    def test_an_unlanded_legacy_decision_names_the_task_that_lands_it(self):
        for row in self.fx["divergent"]:
            if row["decision"] == "legacy" and not row["landed"]:
                self.assertRegex(row["follow_up"], r"^task \d+$", row["name"])
                self.assertIn("pending_literal", row)

    def test_the_four_documented_silent_loss_classes_are_each_a_row(self):
        # The plan names these explicitly as losses no CLI surface can see.
        rows = {r["name"]: r for r in self.fx["divergent"]}
        for name in ("SKILL_PIN_FLOOR", "LEARNED_RE", "DECISION_RE", "PROMOTED_RE"):
            self.assertEqual(rows[name]["decision"], "legacy", name)
        self.assertEqual(rows["SKILL_PIN_FLOOR"]["decided_literal"], "0.85")

    def test_the_cache_version_decision_is_a_value_neither_tree_used(self):
        row = {r["name"]: r for r in self.fx["divergent"]}["BM25_CACHE_VERSION"]
        self.assertEqual(row["decision"], "lib")
        self.assertEqual(row["decided_literal"], "3")
        self.assertNotIn(row["decided_literal"],
                         (row["legacy_literal"], "1"))  # 1 is the donor's

    def test_the_baseline_leaks_no_operator_path(self):
        # The fixture is a committed file; the runtime probe compares digests
        # for exactly this reason. Assert the static half stayed clean too.
        blob = json.dumps(self.fx)
        self.assertNotIn(os.path.expanduser("~"), blob)
        self.assertNotIn("/Users/", blob)


class FollowUpTableTest(unittest.TestCase):
    """The owner map is documentation, but it should still be right."""

    def test_longest_prefix_wins(self):
        self.assertEqual(adj.follow_up_for("DREAM_CADENCE_INTERVAL_SEC"), "task 46")
        self.assertEqual(adj.follow_up_for("CHECKUP_WINDOW_DAYS"), "task 44")
        self.assertEqual(adj.follow_up_for("_PLAN_STATES"), "task 39")

    def test_an_unclaimed_name_falls_to_the_promotion_wave(self):
        self.assertEqual(adj.follow_up_for("ZZ_UNCLAIMED"), adj.FOLLOW_UP_DEFAULT)

    def test_every_adjudicated_divergence_is_still_a_real_divergence(self):
        # A stale adjudication for a name that no longer diverges is dead prose
        # pretending to be a decision. `--write` raises on the opposite case (an
        # unadjudicated divergence); this closes the other direction.
        live = {r["name"] for r in cd.load_fixture()["divergent"]}
        self.assertEqual(sorted(adj.DIVERGENT), sorted(live))


if __name__ == "__main__":
    unittest.main()
