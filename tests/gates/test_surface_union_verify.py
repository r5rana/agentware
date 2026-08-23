from tests._paths import TESTS_DIR
#!/usr/bin/env python3
"""Every check in the union JUDGE must BITE, not merely report zero.

Feature 260813-modular-core-adoption, task 99 wave 19.

`tests/_surface_union_verify.py` is the half of the union harness that JUDGES:
`check_shape` (a fixture cannot contradict its own header), `check_covers_sources`
(nothing either side had may be missing), `check_tables` (every adjudication and
exclusion resolves) and `superset_violations` (the primitive the promotion gate
and the presence gate both call). Wave 19 split it out of `_surface_union.py` to
pay six ceiling rows down, and the non-vacuity sweep it ran on the way asked the
question this file exists to answer.

WHAT THE SWEEP FOUND, which is why this file is new. Of the 32 arms the sweep
mutates, EIGHTEEN stayed GREEN — the suite passed with the check deleted. The
same sweep re-run against a pristine PRE-SPLIT tree returned the same eighteen
(`evidence/task99/presplit-nonvacuity-w19.txt`), so the split lost no guard:
there was none. The old suite drives each check on the CLEAN fixture and asserts
an empty problem list, which is exactly the shape a deleted check also satisfies.
`test_dropping_a_donor_node_is_caught` was the one negative proof in the family,
and its docstring already said what the rest were missing.

So each test below deletes ONE thing from a deep copy of the committed fixture
and asserts the check NAMES it. The tampers are deliberately one-at-a-time: a
fixture broken two ways cannot tell you which check answered, and a check that
only fires alongside another is not independently guarded.
"""

import copy
import importlib.util
import json
import os
import unittest
from importlib.machinery import SourceFileLoader

HERE = TESTS_DIR
FIXTURE_DIR = os.path.join(HERE, "fixtures")
LOCAL_FIXTURE = os.path.join(FIXTURE_DIR, "surface-local.json")
DONOR_FIXTURE = os.path.join(FIXTURE_DIR, "surface-donor.json")
UNION_FIXTURE = os.path.join(FIXTURE_DIR, "surface-union.json")

EMPTY_SOURCE = {"nodes": [], "top_level_verbs": []}


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


VERIFY = load_module("aw_surface_union_verify", "_surface_union_verify.py")
CAPTURE = load_module("aw_surface_capture_v", "_surface_capture.py")


def union():
    return copy.deepcopy(load(UNION_FIXTURE))


def first_verb_node(fixture):
    """The first node that is a verb (not the root) and carries actions."""
    return next(n for n in fixture["nodes"] if n["path"] and n["actions"])


def shape(fixture, expect=None):
    return VERIFY.check_shape(fixture, CAPTURE, expect)


class ShapeChecksBiteTest(unittest.TestCase):
    """`check_shape`: a hand-edited header cannot contradict the body."""

    def test_the_committed_fixture_is_clean(self):
        self.assertEqual(shape(union()), [])

    def test_a_recorded_count_contradicting_the_body_is_caught(self):
        f = union()
        f["counts"]["actions"] += 1
        self.assertTrue(any("counts.actions" in p for p in shape(f)), shape(f))

    def test_an_expected_count_that_does_not_match_is_caught(self):
        problems = shape(union(), {"nodes": 999, "actions": None,
                                   "top_level_verbs": None})
        self.assertTrue(any("expected 999" in p for p in problems), problems)

    def test_an_expected_count_of_none_is_not_asserted(self):
        """`--expect-nodes` is optional; None must not read as zero."""
        self.assertEqual(shape(union(), {"nodes": None, "actions": None,
                                         "top_level_verbs": None}), [])

    def test_a_fixture_with_no_root_node_is_caught(self):
        f = union()
        f["nodes"] = [n for n in f["nodes"] if n["path"]]
        self.assertIn("no root node", shape(f))

    def test_an_orphan_node_is_caught(self):
        f = union()
        f["nodes"].append({"path": ["nosuchparent", "child"], "depth": 2,
                           "sides": "both", "subcommand_dest": None,
                           "subcommands": [], "actions": []})
        problems = shape(f)
        self.assertTrue(any("orphan node nosuchparent/child" in p
                            for p in problems), problems)

    def test_a_node_its_parent_does_not_declare_is_caught(self):
        f = union()
        node = first_verb_node(f)
        root = next(n for n in f["nodes"] if not n["path"])
        root["subcommands"] = [s for s in root["subcommands"]
                               if s != node["path"][0]]
        problems = shape(f)
        self.assertTrue(any("not declared by its parent" in p
                            for p in problems), problems)

    def test_an_action_with_the_wrong_field_set_is_caught(self):
        f = union()
        first_verb_node(f)["actions"][0].pop("nargs")
        problems = shape(f)
        self.assertTrue(any("action field set is" in p for p in problems),
                        problems)

    def test_a_structural_argparse_action_is_caught(self):
        """The counting convention EXCLUDES the two synthesised kinds."""
        f = union()
        first_verb_node(f)["actions"][0]["kind"] = "_HelpAction"
        problems = shape(f)
        self.assertTrue(any("structural action _HelpAction" in p
                            for p in problems), problems)

    def test_a_disposition_outside_the_vocabulary_is_caught(self):
        f = union()
        first_verb_node(f)["actions"][0]["disposition"] = "maybe"
        problems = shape(f)
        self.assertTrue(any("bad disposition" in p for p in problems), problems)

    def test_a_sides_value_outside_the_vocabulary_is_caught(self):
        f = union()
        first_verb_node(f)["actions"][0]["sides"] = "neither"
        problems = shape(f)
        self.assertTrue(any("bad sides" in p for p in problems), problems)

    def test_a_duplicate_action_key_is_caught(self):
        """Identity is (dest, option_strings); the same key twice is a defect."""
        f = union()
        node = first_verb_node(f)
        node["actions"].append(copy.deepcopy(node["actions"][0]))
        f["counts"]["actions"] += 1
        problems = shape(f)
        self.assertTrue(any("duplicate action" in p for p in problems), problems)


class CoversSourcesBitesTest(unittest.TestCase):
    """`check_covers_sources`: nothing either side had may go missing."""

    def test_the_committed_fixture_covers_both_sides(self):
        self.assertEqual(
            VERIFY.check_covers_sources(union(), load(LOCAL_FIXTURE),
                                        load(DONOR_FIXTURE)), [])

    def test_a_dropped_source_action_is_caught(self):
        f = union()
        node = first_verb_node(f)
        victim = node["actions"][0]
        node["actions"] = node["actions"][1:]
        problems = VERIFY.check_covers_sources(f, load(LOCAL_FIXTURE),
                                               load(DONOR_FIXTURE))
        self.assertTrue(any(victim["dest"] in p and "action" in p
                            for p in problems), problems)

    def test_a_dropped_source_subcommand_is_caught(self):
        f = union()
        node = next(n for n in f["nodes"] if n["path"] and n["subcommands"])
        victim = node["subcommands"][0]
        node["subcommands"] = node["subcommands"][1:]
        problems = VERIFY.check_covers_sources(f, load(LOCAL_FIXTURE),
                                               load(DONOR_FIXTURE))
        self.assertTrue(any("subcommand %s missing" % victim in p
                            for p in problems), problems)

    def test_a_dropped_top_level_verb_is_caught(self):
        f = union()
        victim = f["top_level_verbs"][0]
        f["top_level_verbs"] = f["top_level_verbs"][1:]
        problems = VERIFY.check_covers_sources(f, load(LOCAL_FIXTURE),
                                               EMPTY_SOURCE)
        self.assertTrue(any("top-level verb %s missing" % victim in p
                            for p in problems), problems)


class TableChecksBiteTest(unittest.TestCase):
    """`check_tables`: an adjudication or exclusion that resolves to nothing."""

    def test_the_committed_tables_resolve(self):
        self.assertEqual(VERIFY.check_tables(union()), [])

    def test_an_adjudication_naming_no_action_is_caught(self):
        f = union()
        f["adjudications"][0]["dest"] = "no_such_dest"
        problems = VERIFY.check_tables(f)
        self.assertTrue(any("no such action" in p for p in problems), problems)

    def test_an_adjudication_whose_node_does_not_exist_is_caught(self):
        f = union()
        f["adjudications"][0]["node"] = ["no-such-verb"]
        problems = VERIFY.check_tables(f)
        self.assertTrue(any("no such action" in p for p in problems), problems)

    def test_an_adjudication_without_a_rationale_is_caught(self):
        f = union()
        f["adjudications"][0]["rationale"] = ""
        problems = VERIFY.check_tables(f)
        self.assertTrue(any("no rationale" in p for p in problems), problems)

    def test_an_adjudication_without_a_winner_is_caught(self):
        f = union()
        f["adjudications"][0].pop("winner")
        problems = VERIFY.check_tables(f)
        self.assertTrue(any("no winner" in p for p in problems), problems)

    def test_an_exclusion_without_an_approver_is_caught(self):
        """The three obligations are what separates an exclusion from a gap."""
        f = union()
        f["exclusions"][0]["approver"] = ""
        problems = VERIFY.check_tables(f)
        self.assertTrue(any("no approver" in p for p in problems), problems)

    def test_an_exclusion_without_a_follow_up_is_caught(self):
        f = union()
        f["exclusions"][0]["follow_up"] = ""
        problems = VERIFY.check_tables(f)
        self.assertTrue(any("no follow_up" in p for p in problems), problems)

    def test_an_exclusion_whose_action_is_not_excluded_is_caught(self):
        f = union()
        row = f["exclusions"][0]
        node = next(n for n in f["nodes"] if n["path"] == row["node"])
        act = next(a for a in node["actions"] if a["dest"] == row["dest"])
        act["disposition"] = "required"
        problems = VERIFY.check_tables(f)
        self.assertTrue(any("disposition is" in p for p in problems), problems)


class SupersetPrimitiveBitesTest(unittest.TestCase):
    """`superset_violations`: the primitive both downstream gates call."""

    def test_the_union_covers_itself(self):
        self.assertEqual(VERIFY.superset_violations(union(), load(UNION_FIXTURE)),
                         [])

    def test_a_wholly_missing_node_is_caught(self):
        merged = union()
        victim = next(n for n in merged["nodes"]
                      if len(n["path"]) == 1 and n["actions"])
        merged["nodes"] = [n for n in merged["nodes"] if n is not victim]
        violations = VERIFY.superset_violations(merged, load(UNION_FIXTURE))
        self.assertTrue(any("missing node %s" % "/".join(victim["path"]) in v
                            for v in violations), violations)

    def test_a_missing_node_with_nothing_required_is_not_a_violation(self):
        """An empty node is not substance; only its members are."""
        merged = union()
        pure = load(UNION_FIXTURE)
        empty = {"path": ["synthetic-empty"], "depth": 1, "sides": "both",
                 "subcommand_dest": None, "subcommands": [], "actions": []}
        pure["nodes"].append(empty)
        self.assertEqual(VERIFY.superset_violations(merged, pure), [])

    def test_a_missing_subcommand_is_caught(self):
        merged = union()
        node = next(n for n in merged["nodes"] if n["subcommands"])
        victim = node["subcommands"][0]
        node["subcommands"] = node["subcommands"][1:]
        violations = VERIFY.superset_violations(merged, load(UNION_FIXTURE))
        self.assertTrue(any("missing subcommand %s" % victim in v
                            for v in violations), violations)

    def test_a_dropped_required_action_is_caught(self):
        merged = union()
        node, act = _first_required(merged)
        node["actions"] = [a for a in node["actions"] if a is not act]
        violations = VERIFY.superset_violations(merged, load(UNION_FIXTURE))
        self.assertTrue(any("missing %s" % act["dest"] in v
                            for v in violations), violations)

    def test_a_moved_default_is_caught(self):
        merged = union()
        _node, act = _first_required(merged)
        act["default"] = "moved"
        violations = VERIFY.superset_violations(merged, load(UNION_FIXTURE))
        self.assertTrue(any("default" in v for v in violations), violations)

    def test_a_narrowed_choices_set_is_caught(self):
        """`choices` is a SUPERSET comparison -- more is fine, fewer is not."""
        merged = union()
        act = next(a for n in merged["nodes"] for a in n["actions"]
                   if a["choices"] and a["disposition"] == "required")
        act["choices"] = act["choices"][:1]
        violations = VERIFY.superset_violations(merged, load(UNION_FIXTURE))
        self.assertTrue(any("does not cover" in v for v in violations),
                        violations)

    def test_a_widened_choices_set_is_allowed(self):
        merged = union()
        act = next(a for n in merged["nodes"] for a in n["actions"]
                   if a["choices"] and a["disposition"] == "required")
        act["choices"] = list(act["choices"]) + ["an-extra-value"]
        self.assertEqual(VERIFY.superset_violations(merged, load(UNION_FIXTURE)),
                         [])

    def test_an_excluded_member_is_not_required(self):
        merged = union()
        for node in merged["nodes"]:
            node["actions"] = [a for a in node["actions"]
                               if a["disposition"] != "excluded"]
        self.assertEqual(VERIFY.superset_violations(merged, load(UNION_FIXTURE)),
                         [])


def _first_required(fixture):
    for node in fixture["nodes"]:
        for act in node["actions"]:
            if act["disposition"] == "required":
                return node, act
    raise AssertionError("no required action in the union fixture")


if __name__ == "__main__":
    unittest.main()
