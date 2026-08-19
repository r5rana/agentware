#!/usr/bin/env python3
"""The per-commit suite delta gate (feature 260813, task 28).

Two arms:

  * SEMANTICS -- every rule of `diff_captures` asserted on synthetic captures,
    including the shapes that must NOT fire (a rise a new test explains, a
    module that moved rather than vanished);
  * RECORDED -- the real captures of two consecutive commits of this branch,
    diffed for real. The pair is committed as evidence, so the gate's headline
    claim ("no module's pass count rose without a new node id") is a fact about
    this tree rather than a property of a synthetic fixture.

The recorded arm carries its own non-vacuity pins. A delta gate over two empty
captures passes trivially and forever, which is the exact failure mode the gate
exists to catch, so the arm asserts the captures are BIG and that the pair
actually contains change.
"""

from __future__ import annotations

from tests._paths import REPO_ROOT, TESTS_DIR

import copy
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, TESTS_DIR)

from _suite_delta import (  # noqa: E402
    CAPTURE_KIND, checkout_note, consistency_violations, diff_captures,
    group_node_ids, load_capture, main, totals_delta)

REPO = REPO_ROOT
BASELINES = os.path.join(REPO, "tests", "baselines")
PREV = os.path.join(BASELINES, "suite-delta-prev.json")
HEAD = os.path.join(BASELINES, "suite-delta-head.json")

#: Floors for the recorded pair. Not exact counts -- those would redden on every
#: legitimate test added by tasks 29-85 -- but a capture that shrinks past these
#: is not measuring this suite any more.
MIN_MODULES = 140
MIN_NODE_IDS = 2800


def _cap(modules, node_ids, label="x", errors=(), root="/tree"):
    """Minimal well-formed capture for the pure arm.

    A module value is `(passed, status)` or `(passed, status, {counts})`.
    """
    rows = {}
    for name, spec in modules.items():
        passed, status = spec[0], spec[1]
        row = {"passed": passed, "status": status}
        row.update(spec[2] if len(spec) > 2 else {})
        rows[name] = row
    return {"schema_version": 1, "kind": CAPTURE_KIND, "label": label,
            "git_head": "0" * 40, "totals": {}, "node_id_errors": list(errors),
            "tree_root": root, "modules": rows, "node_ids": dict(node_ids)}


def _only(report):
    return report["violations"]


class GroupNodeIdsTest(unittest.TestCase):

    def test_ids_group_by_module_and_are_sorted_and_deduped(self):
        got = group_node_ids(["tests.test_b.C.t2", "tests.test_a.C.t1",
                              "tests.test_a.C.t1", "tests.test_a.D.t0"])
        self.assertEqual(got, {"tests.test_a": ["C.t1", "D.t0"],
                               "tests.test_b": ["C.t2"]})

    def test_a_package_path_keeps_its_full_dotted_module(self):
        got = group_node_ids(["tests.unit.core.test_x.C.t"])
        self.assertEqual(list(got), ["tests.unit.core.test_x"])


class DiffSemanticsTest(unittest.TestCase):

    def test_an_unchanged_pair_is_clean(self):
        cap = _cap({"tests.test_a": (3, "ok")}, {"tests.test_a": ["C.a", "C.b", "C.c"]})
        self.assertEqual(diff_captures(cap, copy.deepcopy(cap)),
                         {"violations": [], "notes": []})

    def test_a_rise_a_new_node_id_explains_is_clean(self):
        before = _cap({"tests.test_a": (2, "ok")}, {"tests.test_a": ["C.a", "C.b"]})
        after = _cap({"tests.test_a": (3, "ok")},
                     {"tests.test_a": ["C.a", "C.b", "C.c"]})
        report = diff_captures(before, after)
        self.assertEqual(report["violations"], [])
        self.assertTrue(any("CHANGED: tests.test_a" in n for n in report["notes"]))

    def test_a_rise_no_new_node_id_explains_is_a_violation(self):
        """The headline rule: three more passes out of the same three tests."""
        before = _cap({"tests.test_a": (3, "ok")}, {"tests.test_a": ["C.a", "C.b", "C.c"]})
        after = _cap({"tests.test_a": (6, "ok")}, {"tests.test_a": ["C.a", "C.b", "C.c"]})
        out = _only(diff_captures(before, after))
        rise = [v for v in out if "UNEXPLAINED PASS-COUNT RISE" in v]
        self.assertEqual(len(rise), 1, out)
        self.assertIn("tests.test_a", rise[0])
        self.assertIn("+3", rise[0])
        # Caught twice over: three tests cannot produce six passes either.
        self.assertTrue(any("COUNTS EXCEED COLLECTION" in v for v in out), out)

    def test_a_partially_explained_rise_is_still_a_violation(self):
        """+3 passes with one new test is two passes nobody can point at."""
        before = _cap({"tests.test_a": (1, "ok")}, {"tests.test_a": ["C.a"]})
        after = _cap({"tests.test_a": (4, "ok")}, {"tests.test_a": ["C.a", "C.b"]})
        out = _only(diff_captures(before, after))
        self.assertTrue(any("UNEXPLAINED PASS-COUNT RISE" in v for v in out), out)

    def test_a_rise_a_repaired_failure_explains_is_clean(self):
        """Red -> green raises `passed` with no new node id, and that is the
        shape of every fix. Refusing it would make the gate unusable."""
        before = _cap({"tests.test_a": (2, "failed", {"failures": 1})},
                      {"tests.test_a": ["C.a", "C.b", "C.c"]})
        after = _cap({"tests.test_a": (3, "ok", {"failures": 0})},
                     {"tests.test_a": ["C.a", "C.b", "C.c"]})
        report = diff_captures(before, after)
        self.assertEqual(report["violations"], [])
        self.assertTrue(any("REPAIRED: tests.test_a 1" in n
                            for n in report["notes"]), report["notes"])

    def test_a_rise_an_unskip_explains_is_clean(self):
        before = _cap({"tests.test_a": (1, "ok", {"skipped": 2})},
                      {"tests.test_a": ["C.a", "C.b", "C.c"]})
        after = _cap({"tests.test_a": (3, "ok", {"skipped": 0})},
                     {"tests.test_a": ["C.a", "C.b", "C.c"]})
        self.assertEqual(_only(diff_captures(before, after)), [])

    def test_a_repair_does_not_bankroll_a_bigger_rise(self):
        """One repair buys exactly one pass, not four."""
        before = _cap({"tests.test_a": (2, "failed", {"failures": 1})},
                      {"tests.test_a": ["C.a", "C.b", "C.c"]})
        after = _cap({"tests.test_a": (6, "ok", {"failures": 0})},
                     {"tests.test_a": ["C.a", "C.b", "C.c"]})
        out = _only(diff_captures(before, after))
        self.assertTrue(any("UNEXPLAINED PASS-COUNT RISE" in v for v in out), out)

    def test_a_rising_failure_count_is_a_violation(self):
        before = _cap({"tests.test_a": (3, "ok")},
                      {"tests.test_a": ["C.a", "C.b", "C.c"]})
        after = _cap({"tests.test_a": (2, "failed", {"failures": 1})},
                     {"tests.test_a": ["C.a", "C.b", "C.c"]})
        out = _only(diff_captures(before, after))
        self.assertTrue(any("FAILURE COUNT ROSE" in v for v in out), out)
        self.assertTrue(any("STATUS REGRESSION" in v for v in out), out)

    def test_a_newly_skipped_test_is_a_violation(self):
        """Skipping is the cheapest way to make a suite greener; the rise
        allowance must not be raidable by it."""
        before = _cap({"tests.test_a": (3, "ok")},
                      {"tests.test_a": ["C.a", "C.b", "C.c"]})
        after = _cap({"tests.test_a": (2, "ok", {"skipped": 1})},
                     {"tests.test_a": ["C.a", "C.b", "C.c"]})
        out = _only(diff_captures(before, after))
        self.assertTrue(any("NEWLY SKIPPED" in v for v in out), out)

    def test_captures_from_different_trees_are_labelled(self):
        """Suites that resolve fixtures relative to the repo root skip or fail
        purely by being run somewhere else."""
        before = _cap({}, {}, root="/a")
        after = _cap({}, {}, root="/b")
        report = diff_captures(before, after)
        self.assertEqual(report["violations"], [])
        self.assertTrue(any("CHECKOUT SKEW" in n for n in report["notes"]))
        self.assertIsNone(checkout_note(_cap({}, {}), _cap({}, {})))

    def test_a_drop_a_deletion_explains_is_clean(self):
        before = _cap({"tests.test_a": (2, "ok")}, {"tests.test_a": ["C.a", "C.b"]})
        after = _cap({"tests.test_a": (1, "ok")}, {"tests.test_a": ["C.a"]})
        self.assertEqual(_only(diff_captures(before, after)), [])

    def test_a_drop_nothing_explains_is_a_violation(self):
        before = _cap({"tests.test_a": (2, "ok")}, {"tests.test_a": ["C.a", "C.b"]})
        after = _cap({"tests.test_a": (1, "failed")}, {"tests.test_a": ["C.a", "C.b"]})
        out = _only(diff_captures(before, after))
        self.assertTrue(any("UNEXPLAINED PASS-COUNT DROP" in v for v in out), out)
        self.assertTrue(any("STATUS REGRESSION" in v for v in out), out)

    def test_a_status_regression_is_a_violation_even_at_equal_counts(self):
        before = _cap({"tests.test_a": (2, "ok")}, {"tests.test_a": ["C.a", "C.b"]})
        after = _cap({"tests.test_a": (2, "timeout")}, {"tests.test_a": ["C.a", "C.b"]})
        out = _only(diff_captures(before, after))
        self.assertEqual(len(out), 1)
        self.assertIn("STATUS REGRESSION", out[0])

    def test_a_new_module_whose_node_ids_are_new_is_clean(self):
        before = _cap({}, {})
        after = _cap({"tests.test_new": (2, "ok")}, {"tests.test_new": ["C.a", "C.b"]})
        report = diff_captures(before, after)
        self.assertEqual(report["violations"], [])
        self.assertTrue(any("ADDED: tests.test_new" in n for n in report["notes"]))

    def test_a_new_module_reusing_known_node_ids_is_a_violation(self):
        """Counts and ids disagreeing about provenance is exactly the skew that
        makes a two-artifact gate lie."""
        before = _cap({"tests.test_a": (1, "ok")}, {"tests.test_a": ["C.a"]})
        after = _cap({"tests.test_a": (1, "ok"), "tests.test_b": (1, "ok")},
                     {"tests.test_a": ["C.a"], "tests.test_b": ["C.a"]})
        out = _only(diff_captures(before, after))
        self.assertEqual(len(out), 1)
        self.assertIn("NEW MODULE UNACCOUNTED", out[0])

    def test_a_module_whose_tests_moved_is_a_note_not_a_violation(self):
        """Task 58 relocates the whole corpus; a move must be expressible."""
        before = _cap({"tests.test_a": (2, "ok")}, {"tests.test_a": ["C.a", "C.b"]})
        after = _cap({"tests.unit.test_a": (2, "ok")},
                     {"tests.unit.test_a": ["C.a", "C.b"]})
        report = diff_captures(before, after)
        self.assertEqual(report["violations"], [])
        self.assertTrue(any("MOVED: tests.test_a" in n for n in report["notes"]))

    def test_a_module_that_simply_vanishes_is_a_violation(self):
        before = _cap({"tests.test_a": (2, "ok")}, {"tests.test_a": ["C.a", "C.b"]})
        out = _only(diff_captures(before, _cap({}, {})))
        self.assertEqual(len(out), 1)
        self.assertIn("LOST MODULE: tests.test_a", out[0])
        self.assertIn("C.a", out[0])

    def test_a_collection_error_at_the_after_commit_is_a_violation(self):
        cap = _cap({"tests.test_a": (1, "ok")}, {"tests.test_a": ["C.a"]})
        after = _cap({"tests.test_a": (1, "ok")}, {"tests.test_a": ["C.a"]},
                     errors=["tests.test_broken (import failure)"])
        out = _only(diff_captures(cap, after))
        self.assertEqual(len(out), 1)
        self.assertIn("COLLECTION ERROR", out[0])

    def test_totals_delta_reports_before_after_and_difference(self):
        before = {"totals": {"passed": 10, "ran": 10}}
        after = {"totals": {"passed": 12, "ran": 13}}
        self.assertEqual(totals_delta(before, after)["passed"], (10, 12, 2))


class RecordedDeltaTest(unittest.TestCase):
    """The real pair. No skipping: an absent artifact is a failure."""

    @classmethod
    def setUpClass(cls):
        cls.prev = load_capture(PREV)
        cls.head = load_capture(HEAD)
        cls.report = diff_captures(cls.prev, cls.head)

    def test_no_module_pass_count_rose_without_a_new_node_id(self):
        self.assertEqual(self.report["violations"], [])

    def test_the_recorded_pair_is_two_distinct_real_commits(self):
        heads = [self.prev["git_head"], self.head["git_head"]]
        for sha in heads:
            self.assertRegex(sha, r"^[0-9a-f]{40}$")
            self.assertEqual(subprocess.run(
                ["git", "cat-file", "-e", sha], cwd=REPO).returncode, 0, sha)
        self.assertNotEqual(heads[0], heads[1])

    def test_the_captures_are_big_enough_to_be_measuring_this_suite(self):
        """Non-vacuity: a gate over two empty captures passes forever."""
        for cap in (self.prev, self.head):
            self.assertGreaterEqual(cap["totals"]["modules"], MIN_MODULES)
            self.assertGreaterEqual(cap["totals"]["node_ids"], MIN_NODE_IDS)
            self.assertGreaterEqual(cap["totals"]["passed"], MIN_NODE_IDS)

    def test_the_pair_actually_contains_change(self):
        """Two identical captures also produce zero violations."""
        self.assertTrue(self.report["notes"],
                        "the recorded pair is inert -- re-stamp it")
        self.assertGreater(totals_delta(self.prev, self.head)["node_ids"][2], 0)

    def test_every_captured_module_carries_a_status_and_a_pass_count(self):
        for cap in (self.prev, self.head):
            for name, row in cap["modules"].items():
                self.assertIn("status", row, name)
                self.assertIsInstance(row["passed"], int)

    def test_inflating_one_real_module_pass_count_reddens_the_gate(self):
        """Mutation proof on live data, not on a synthetic capture."""
        module = sorted(set(self.prev["modules"]) & set(self.head["modules"]))[0]
        mutated = copy.deepcopy(self.head)
        mutated["modules"][module]["passed"] += 1
        out = _only(diff_captures(self.prev, mutated))
        self.assertTrue(any("UNEXPLAINED PASS-COUNT RISE" in v and module in v
                            for v in out), out)

    def test_dropping_one_real_node_id_reddens_the_gate(self):
        """A test quietly falling out of collection while the pass count stays
        put is the same defect wearing a different hat."""
        module = sorted(m for m in set(self.prev["modules"])
                        & set(self.head["modules"])
                        if self.head["modules"][m]["passed"]
                        == len(self.head["node_ids"].get(m, ())) > 0)[0]
        mutated = copy.deepcopy(self.head)
        mutated["node_ids"][module] = mutated["node_ids"][module][1:]
        out = _only(diff_captures(self.prev, mutated))
        self.assertTrue(any("COUNTS EXCEED COLLECTION" in v and module in v
                            for v in out), out)

    def test_both_seed_captures_are_internally_consistent(self):
        self.assertEqual(consistency_violations(self.prev, "before"), [])
        self.assertEqual(consistency_violations(self.head, "after"), [])

    def test_the_check_command_exits_zero_on_the_recorded_pair(self):
        self.assertEqual(main(["check", "--before", PREV, "--after", HEAD]), 0)

    def test_the_check_command_rejects_a_foreign_artifact(self):
        with self.assertRaises(SystemExit):
            main(["check", "--before", os.path.join(BASELINES,
                                                    "baseline-local.json"),
                  "--after", HEAD])

    def test_the_artifacts_are_indented_json_a_reviewer_can_diff(self):
        for path in (PREV, HEAD):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn('\n  "kind"', text)
            json.loads(text)


if __name__ == "__main__":
    unittest.main()
