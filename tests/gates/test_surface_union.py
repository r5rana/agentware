from tests._paths import TESTS_DIR
#!/usr/bin/env python3
"""The UNION surface fixture is the binding contract for the merged tree.

Feature 260813-modular-core-adoption, task 7.

``surface-local.json`` and ``surface-donor.json`` are frozen baselines of the
two original trees (task 6). This module asserts the union DERIVED from them
is what the plan pins -- 124 nodes / 504 actions / 44 top-level verbs -- that
it covers BOTH sides completely, and that the gate built on it is symmetric:
a one-sided tree must FAIL it, in either direction. A union gate that cannot
redden is the exact failure this migration is most exposed to, so every claim
here carries a negative proof rather than an assertion alone.
"""

import copy
import json
import os
import unittest
from importlib.machinery import SourceFileLoader
import importlib.util

HERE = TESTS_DIR
FIXTURE_DIR = os.path.join(HERE, "fixtures")
LOCAL_FIXTURE = os.path.join(FIXTURE_DIR, "surface-local.json")
DONOR_FIXTURE = os.path.join(FIXTURE_DIR, "surface-donor.json")
UNION_FIXTURE = os.path.join(FIXTURE_DIR, "surface-union.json")

# Pinned by the plan. Editing these to match a regenerated fixture defeats the
# purpose: they are what the migration is measured against.
EXPECTED_COUNTS = {"nodes": 124, "actions": 504, "top_level_verbs": 44}
EXPECTED_NODE_SIDES = {"shared": 62, "local_only": 44, "donor_only": 18}
EXPECTED_ACTION_SIDES = {"shared": 239, "local_only": 203, "donor_only": 62}
EXPECTED_VERB_SIDES = {"shared": 27, "local_only": 11, "donor_only": 6}

# The six verbs legacy-default routing can never resolve (plan task 52).
DONOR_ONLY_VERBS = ["fleet", "fleet-emit", "org", "team", "templates", "work"]


def load_module(name, filename):
    path = os.path.join(HERE, filename)
    loader = SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


UNION = load_module("aw_surface_union", "_surface_union.py")


def node_of(fixture, path):
    return next(n for n in fixture["nodes"] if n["path"] == path)


def action_of(fixture, path, dest):
    return next(a for a in node_of(fixture, path)["actions"] if a["dest"] == dest)


class UnionShapeTest(unittest.TestCase):
    """The union is exactly the shape the plan binds downstream gates to."""

    def test_fixture_exists_and_records_the_pinned_counts(self):
        self.assertTrue(os.path.isfile(UNION_FIXTURE), "union fixture missing")
        self.assertEqual(load(UNION_FIXTURE)["counts"], EXPECTED_COUNTS)

    def test_recorded_counts_agree_with_the_recorded_body(self):
        """A hand-edited header cannot pass by contradicting the nodes."""
        fixture = load(UNION_FIXTURE)
        self.assertEqual(len(fixture["nodes"]), EXPECTED_COUNTS["nodes"])
        self.assertEqual(sum(len(n["actions"]) for n in fixture["nodes"]),
                         EXPECTED_COUNTS["actions"])
        self.assertEqual(len(fixture["top_level_verbs"]),
                         EXPECTED_COUNTS["top_level_verbs"])

    def test_side_tallies_are_recorded_and_sum_to_the_counts(self):
        sides = load(UNION_FIXTURE)["sides"]
        self.assertEqual(sides["nodes"], EXPECTED_NODE_SIDES)
        self.assertEqual(sides["actions"], EXPECTED_ACTION_SIDES)
        self.assertEqual(sides["top_level_verbs"], EXPECTED_VERB_SIDES)
        self.assertEqual(sum(sides["nodes"].values()), EXPECTED_COUNTS["nodes"])
        self.assertEqual(sum(sides["actions"].values()),
                         EXPECTED_COUNTS["actions"])

    def test_the_six_donor_only_verbs_are_named(self):
        """Legacy-default routing can never resolve these; they need porting."""
        self.assertEqual(load(UNION_FIXTURE)["donor_only_verbs"],
                         DONOR_ONLY_VERBS)

    def test_every_node_is_reachable_from_its_parent(self):
        fixture = load(UNION_FIXTURE)
        by_path = {tuple(n["path"]): n for n in fixture["nodes"]}
        self.assertIn((), by_path, "no root node")
        for key, node in by_path.items():
            if not key:
                continue
            parent = by_path.get(key[:-1])
            self.assertIsNotNone(parent, f"orphan node {key}")
            self.assertIn(key[-1], parent["subcommands"],
                          f"{key} not declared by its parent")

    def test_actions_carry_the_object_fields_plus_provenance(self):
        capture = load_module("aw_surface_capture", "_surface_capture.py")
        want = set(capture.ACTION_FIELDS) | set(UNION.UNION_EXTRA_FIELDS)
        for node in load(UNION_FIXTURE)["nodes"]:
            for action in node["actions"]:
                self.assertEqual(set(action), want, node["path"])
                self.assertNotIn("-h", action["option_strings"])

    def test_no_rendered_help_and_no_operator_home_path(self):
        capture = load_module("aw_surface_capture", "_surface_capture.py")
        self.assertEqual(capture.scan_for_prose(load(UNION_FIXTURE)), [])
        with open(UNION_FIXTURE, encoding="utf-8") as fh:
            self.assertNotIn(os.path.expanduser("~"), fh.read())


class UnionCoversBothSidesTest(unittest.TestCase):
    """The whole point: symmetric, so neither side can be silently dropped."""

    def test_every_local_node_and_action_is_present(self):
        problems = UNION.check_covers_sources(
            load(UNION_FIXTURE), load(LOCAL_FIXTURE), {"nodes": [],
                                                       "top_level_verbs": []})
        self.assertEqual(problems, [])

    def test_every_donor_node_and_action_is_present(self):
        problems = UNION.check_covers_sources(
            load(UNION_FIXTURE), {"nodes": [], "top_level_verbs": []},
            load(DONOR_FIXTURE))
        self.assertEqual(problems, [])

    def test_verify_passes_on_the_committed_fixture(self):
        self.assertEqual(
            UNION.verify(UNION_FIXTURE, LOCAL_FIXTURE, DONOR_FIXTURE,
                         EXPECTED_COUNTS), 0)

    def test_derivation_is_deterministic(self):
        """Re-deriving reproduces the committed fixture byte-for-byte."""
        self.assertEqual(UNION.derive(LOCAL_FIXTURE, DONOR_FIXTURE),
                         load(UNION_FIXTURE))

    def test_dropping_a_donor_node_is_caught(self):
        """The cover check must BITE, not merely report zero."""
        tampered = copy.deepcopy(load(UNION_FIXTURE))
        tampered["nodes"] = [n for n in tampered["nodes"] if n["path"] != ["team"]]
        problems = UNION.check_covers_sources(
            tampered, {"nodes": [], "top_level_verbs": []}, load(DONOR_FIXTURE))
        self.assertTrue(any("team" in p for p in problems), problems)


class AdjudicationTest(unittest.TestCase):
    """Divergent fields carry a DECIDED value, never a silent side-pick."""

    def test_both_divergences_are_adjudicated_with_a_rationale(self):
        rows = load(UNION_FIXTURE)["adjudications"]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertTrue(row["rationale"].strip(), row)
            self.assertIn(row["winner"], ("local", "donor"))

    def test_token_budget_default_is_the_decided_1500(self):
        """Plan decision: 1500 preserves the R-RET-03/04 stage escalation."""
        action = action_of(load(UNION_FIXTURE), ["recall"], "token_budget")
        self.assertEqual(action["default"], 1500)
        self.assertEqual(action_of(load(DONOR_FIXTURE), ["recall"],
                                   "token_budget")["default"], 10000)

    def test_recall_scope_choices_are_the_superset_of_both(self):
        union = action_of(load(UNION_FIXTURE), ["recall"], "scope")
        local = action_of(load(LOCAL_FIXTURE), ["recall"], "scope")
        donor = action_of(load(DONOR_FIXTURE), ["recall"], "scope")
        self.assertTrue(set(local["choices"]) <= set(union["choices"]))
        self.assertTrue(set(donor["choices"]) <= set(union["choices"]))
        self.assertIn("shared", union["choices"])

    def test_an_unadjudicated_divergence_fails_derivation_loudly(self):
        """Removing the table must break derivation, not pick a side."""
        saved = UNION.ADJUDICATIONS
        try:
            UNION.ADJUDICATIONS = ()
            with self.assertRaises(SystemExit) as caught:
                UNION.derive(LOCAL_FIXTURE, DONOR_FIXTURE)
            self.assertIn("no adjudication", str(caught.exception))
            self.assertIn("token_budget", str(caught.exception))
        finally:
            UNION.ADJUDICATIONS = saved

    def test_a_fixture_contradicting_its_adjudication_is_caught(self):
        tampered = copy.deepcopy(load(UNION_FIXTURE))
        action_of(tampered, ["recall"], "token_budget")["default"] = 10000
        problems = UNION.check_tables(tampered)
        self.assertTrue(any("token_budget" in p for p in problems), problems)


class ExclusionTest(unittest.TestCase):
    """A deferred capability is recorded, not quietly required or dropped."""

    def test_all_teams_is_the_single_recorded_exclusion(self):
        fixture = load(UNION_FIXTURE)
        self.assertEqual([r["dest"] for r in fixture["exclusions"]], ["all_teams"])
        row = fixture["exclusions"][0]
        for field in ("reason", "approver", "follow_up"):
            self.assertTrue(row[field].strip(), field)
        self.assertEqual(
            action_of(fixture, ["recall"], "all_teams")["disposition"], "excluded")

    def test_it_still_counts_toward_the_union(self):
        """504 is the MECHANICAL union; exclusions are a disposition, not a delete."""
        self.assertEqual(load(UNION_FIXTURE)["counts"]["actions"],
                         EXPECTED_COUNTS["actions"])

    def test_every_other_action_is_required(self):
        excluded = [(n["path"], a["dest"]) for n in load(UNION_FIXTURE)["nodes"]
                    for a in n["actions"] if a["disposition"] == "excluded"]
        self.assertEqual(excluded, [(["recall"], "all_teams")])

    def test_an_excluded_action_without_a_row_is_caught(self):
        tampered = copy.deepcopy(load(UNION_FIXTURE))
        action_of(tampered, ["recall"], "top_k")["disposition"] = "excluded"
        self.assertTrue(any("top_k" in p for p in UNION.check_tables(tampered)))


class CheckValuesBitesTest(unittest.TestCase):
    """`check_values` RECOMPUTES every value from the two sources.

    It is the one check that stayed in `_surface_union` when task 99 wave 19
    split the judging half out, because it is the one that reads
    ``ADJUDICATIONS`` -- and the sweep that split ran found its three arms
    unguarded on BOTH sides of the split: the suite drove it on the clean
    fixture only, which a deleted check satisfies just as well. Each test below
    moves ONE value off what the sources say and asserts the check names it.

    Its two adjudication arms are already guarded above, by
    `test_an_unadjudicated_divergence_fails_derivation_loudly` and
    `test_a_fixture_contradicting_its_adjudication_is_caught`.
    """

    def setUp(self):
        self.capture = load_module("aw_surface_capture_cv", "_surface_capture.py")
        self.local, self.donor = load(LOCAL_FIXTURE), load(DONOR_FIXTURE)

    def values(self, fixture):
        return UNION.check_values(fixture, self.local, self.donor, self.capture)

    def test_the_committed_fixture_recomputes_clean(self):
        self.assertEqual(self.values(load(UNION_FIXTURE)), [])

    def test_a_wrong_sides_value_is_caught(self):
        """`sides` is DERIVED provenance, so it is recomputed, never trusted."""
        tampered = copy.deepcopy(load(UNION_FIXTURE))
        action_of(tampered, ["recall"], "top_k")["sides"] = "donor"
        problems = self.values(tampered)
        self.assertTrue(any("sides" in p and "is wrong" in p for p in problems),
                        problems)

    def test_an_agreed_field_moved_off_the_agreed_value_is_caught(self):
        """Both sides said the same thing; the union may not say another."""
        tampered = copy.deepcopy(load(UNION_FIXTURE))
        action_of(tampered, ["recall"], "top_k")["default"] = 999
        problems = self.values(tampered)
        self.assertTrue(any("!= agreed" in p for p in problems), problems)

    def test_a_one_sided_field_moved_off_its_source_is_caught(self):
        """An action only one tree had still has exactly one right answer."""
        union = load(UNION_FIXTURE)
        target = next(
            (n["path"], a["dest"]) for n in union["nodes"] for a in n["actions"]
            if a["sides"] in ("local", "donor") and a["option_strings"])
        tampered = copy.deepcopy(union)
        action_of(tampered, target[0], target[1])["default"] = "not-the-source"
        problems = self.values(tampered)
        self.assertTrue(any("!= source" in p for p in problems), problems)


class SupersetGateTest(unittest.TestCase):
    """The gate downstream tasks build on must redden in BOTH directions."""

    def test_the_union_itself_satisfies_the_gate(self):
        union = load(UNION_FIXTURE)
        self.assertEqual(UNION.superset_violations(union, union), [])

    def test_the_local_tree_alone_fails_it(self):
        """One-sided is not enough: the donor-only verbs must be named."""
        violations = UNION.superset_violations(load(LOCAL_FIXTURE),
                                               load(UNION_FIXTURE))
        self.assertTrue(violations)
        for verb in DONOR_ONLY_VERBS:
            self.assertTrue(any(verb in v for v in violations), verb)

    def test_the_donor_tree_alone_fails_it(self):
        violations = UNION.superset_violations(load(DONOR_FIXTURE),
                                               load(UNION_FIXTURE))
        self.assertTrue(violations)
        for verb in ("graph", "trust", "migrate"):
            self.assertTrue(any(verb in v for v in violations), verb)

    def test_a_narrowed_choices_set_reddens_it(self):
        merged = copy.deepcopy(load(UNION_FIXTURE))
        action_of(merged, ["recall"], "scope")["choices"] = ["curated"]
        violations = UNION.superset_violations(merged, load(UNION_FIXTURE))
        self.assertTrue(any("scope choices" in v for v in violations), violations)

    def test_a_moved_default_reddens_it(self):
        merged = copy.deepcopy(load(UNION_FIXTURE))
        action_of(merged, ["recall"], "token_budget")["default"] = 10000
        violations = UNION.superset_violations(merged, load(UNION_FIXTURE))
        self.assertTrue(any("token_budget default" in v for v in violations),
                        violations)

    def test_a_dropped_required_flag_reddens_it_by_name(self):
        merged = copy.deepcopy(load(UNION_FIXTURE))
        node = node_of(merged, ["recall"])
        node["actions"] = [a for a in node["actions"] if a["dest"] != "top_k"]
        violations = UNION.superset_violations(merged, load(UNION_FIXTURE))
        self.assertTrue(any("top_k" in v for v in violations), violations)

    def test_a_relaxed_requiredness_reddens_it(self):
        union = load(UNION_FIXTURE)
        target = next(
            (n["path"], a["dest"]) for n in union["nodes"] for a in n["actions"]
            if a["required"] and a["option_strings"])
        merged = copy.deepcopy(union)
        action_of(merged, target[0], target[1])["required"] = False
        violations = UNION.superset_violations(merged, union)
        self.assertTrue(any(target[1] in v for v in violations), violations)

    def test_the_excluded_action_is_not_required(self):
        """A deferred flag must not hard-block the merged tree."""
        union = load(UNION_FIXTURE)
        merged = copy.deepcopy(union)
        node = node_of(merged, ["recall"])
        node["actions"] = [a for a in node["actions"] if a["dest"] != "all_teams"]
        self.assertEqual(UNION.superset_violations(merged, union), [])


if __name__ == "__main__":
    unittest.main()
