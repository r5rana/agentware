#!/usr/bin/env python3
"""SOLO parity gate: the merged tree vs ``aw-pre-merge``, team mode UNSET.

Feature 260813-modular-core-adoption, task 81.

Subject: the ``tests/_solo_parity.py`` + ``tests/_solo_session.py`` harness and
a tree-wide invariant (every parser surface and a scripted read-only session),
which is what puts this file in ``tests/gates/``.

The claim under test is the plan's acceptance criterion in its own words: *with
team mode unset, a fresh clone behaves identically to ``aw-pre-merge`` across
the full verb surface*. "Identically" is not "byte-identical" -- the merged
tree is a deliberate SUPERSET (45 verbs to 38, 128 parser nodes to 106) -- so
the operational form of the claim is: **nothing an operator could observe has
been lost or silently changed, and every surviving difference is declared with
a reason.**

Three arms
----------
1. the live run: both arms of the harness over real clones, zero undeclared
   differences (``test_the_merged_tree_is_solo_parity_clean``);
2. the ledger's own hygiene: every row reasoned, every row addressing something
   that exists, and the one ``rc_must_match: false`` row isolated;
3. non-vacuity: each half of the comparison is shown to REDDEN on a planted
   difference. An always-green gate is the failure mode a parity proof is most
   prone to, because "no differences found" is also what a broken comparison
   prints.

Arms 2 and 3 are cheap and run in-process. Arm 1 clones twice and spawns ~70
subprocesses (about 11 s), and skips -- rather than fails -- where the tag is
absent, which is the state of a shallow CI checkout.
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                "..", ".."))

from tests import _solo_parity as sp
from tests import _solo_session as ss


class SoloParityLedgerContract(unittest.TestCase):
    """The ledger is a reasoned ratchet, not a list of excused differences."""

    @classmethod
    def setUpClass(cls):
        cls.ledger = sp.read_ledger()

    def test_every_row_carries_a_reason_a_reader_can_act_on(self):
        for row in self.ledger["prose"] + self.ledger["session"]:
            name = row.get("id") or "%s::%s" % (row["node"], row["action"])
            self.assertIn("reason", row, "%s has no reason" % name)
            self.assertGreater(len(row["reason"]), 60,
                               "%s's reason is too short to be one" % name)

    def test_every_prose_row_carries_both_texts_so_it_licenses_one_difference(self):
        for row in self.ledger["prose"]:
            self.assertIn("pre", row)
            self.assertIn("post", row)
            self.assertNotEqual(row["pre"], row["post"],
                                "%s::%s declares a difference that is not one"
                                % (row["node"], row["action"]))

    def test_every_session_row_names_a_roster_id_that_exists(self):
        roster = {rid for rid, _argv in ss.SESSION}
        for row in self.ledger["session"]:
            self.assertIn(row["id"], roster,
                          "%s is declared but never run" % row["id"])

    def test_only_the_adjudicated_rename_may_diverge_on_exit_code(self):
        """rc divergence is the sharpest kind; exactly one row may carry it."""
        loose = [r["id"] for r in self.ledger["session"]
                 if r.get("rc_must_match", True) is False]
        self.assertEqual(loose, ["skill-propose-dry"])
        row = [r for r in self.ledger["session"]
               if r["id"] == "skill-propose-dry"][0]
        self.assertTrue(row.get("decision"),
                        "an exit-code divergence must name its decision")

    def test_every_adjudicated_row_names_the_recorded_decision(self):
        for row in self.ledger["prose"]:
            if row.get("verdict") == "adjudicated":
                self.assertTrue(row.get("decision"),
                                "%s::%s is adjudicated by nothing"
                                % (row["node"], row["action"]))


class SoloParityComparisonIsNotVacuous(unittest.TestCase):
    """Each half of the comparison reddens on a planted difference."""

    PRE = {"top": {"actions": {"--flag": "the original text",
                               "<metavar>--flag": "N"},
                   "description": "d", "epilog": ""}}

    def _post(self, **overrides):
        post = copy.deepcopy(self.PRE)
        post["top"]["actions"].update(overrides)
        return post

    def test_a_lost_node_is_reported(self):
        diffs, _stale = sp.compare_surface(self.PRE, {}, {"prose": []})
        self.assertEqual([d["kind"] for d in diffs], ["node-lost"])

    def test_a_lost_action_is_reported(self):
        post = copy.deepcopy(self.PRE)
        del post["top"]["actions"]["--flag"]
        diffs, _stale = sp.compare_surface(self.PRE, post, {"prose": []})
        self.assertEqual([d["kind"] for d in diffs], ["action-lost"])

    def test_a_rewritten_help_string_is_reported(self):
        post = self._post(**{"--flag": "thinner"})
        diffs, _stale = sp.compare_surface(self.PRE, post, {"prose": []})
        self.assertEqual([d["kind"] for d in diffs], ["prose-changed"])

    def test_a_moved_metavar_is_reported(self):
        """The field no other gate in this corpus reads. See walk_prose."""
        post = self._post(**{"<metavar>--flag": "COUNT"})
        diffs, _stale = sp.compare_surface(self.PRE, post, {"prose": []})
        self.assertEqual([d["kind"] for d in diffs], ["prose-changed"])
        self.assertEqual(diffs[0]["action"], "<metavar>--flag")

    def test_a_declared_row_that_no_longer_matches_reddens_rather_than_passes(self):
        """A row licenses THE difference it records, not the next one."""
        ledger = {"prose": [{"node": "top", "action": "--flag",
                             "pre": "the original text", "post": "thinner",
                             "reason": "x" * 70}]}
        clean, _stale = sp.compare_surface(self.PRE, self._post(
            **{"--flag": "thinner"}), ledger)
        self.assertEqual(clean, [])
        drifted, _stale2 = sp.compare_surface(self.PRE, self._post(
            **{"--flag": "thinner still"}), ledger)
        self.assertEqual([d["kind"] for d in drifted], ["prose-row-mismatch"])

    def test_a_row_whose_difference_has_gone_is_reported_as_stale(self):
        ledger = {"prose": [{"node": "top", "action": "--flag",
                             "pre": "a", "post": "b", "reason": "x" * 70}]}
        diffs, stale = sp.compare_surface(self.PRE, copy.deepcopy(self.PRE),
                                          ledger)
        self.assertEqual(diffs, [])
        self.assertEqual([s["kind"] for s in stale], ["stale-prose-row"])

    def test_an_undeclared_session_divergence_is_reported(self):
        pre = {"r": {"argv": ["config"], "rc": 0, "out": "a", "err": ""}}
        post = {"r": {"argv": ["config"], "rc": 0, "out": "b", "err": ""}}
        diffs, _stale = sp.compare_session(pre, post, {"session": []})
        self.assertEqual([d["kind"] for d in diffs], ["session-differs"])

    def test_a_declared_session_row_may_not_smuggle_an_exit_code_change(self):
        pre = {"r": {"argv": ["config"], "rc": 0, "out": "a", "err": ""}}
        post = {"r": {"argv": ["config"], "rc": 3, "out": "b", "err": ""}}
        ledger = {"session": [{"id": "r", "reason": "x" * 70}]}
        diffs, _stale = sp.compare_session(pre, post, ledger)
        self.assertEqual([d["kind"] for d in diffs], ["session-rc-differs"])

    def test_a_mutated_knowledge_base_fails_the_verdict(self):
        """`read-only` is asserted by fingerprint, not by roster discipline."""
        ok, fails = sp.verdict({"surface_diffs": [], "surface_stale": [],
                                "session_diffs": [], "session_stale": [],
                                "mutated_sides": ["post"]})
        self.assertFalse(ok)
        self.assertIn("MUTATED", fails[0])


class SoloParityAgainstThePreMergeTag(unittest.TestCase):
    """The live run: two fresh clones, both arms, zero undeclared differences."""

    @classmethod
    def setUpClass(cls):
        if not sp.ref_exists(sp.PRE_REF):
            raise unittest.SkipTest("%s is unavailable (shallow clone?)"
                                    % sp.PRE_REF)
        import shutil
        import tempfile
        cls.workdir = tempfile.mkdtemp(prefix="solo-parity-gate-")
        try:
            cls.report = sp.run_parity(cls.workdir)
        finally:
            shutil.rmtree(cls.workdir, ignore_errors=True)

    def test_the_merged_tree_is_solo_parity_clean(self):
        ok, fails = sp.verdict(self.report)
        self.assertTrue(ok, "\n".join(fails))

    def test_the_merged_surface_is_a_strict_superset_of_the_pre_merge_one(self):
        self.assertGreater(self.report["nodes"]["post"],
                           self.report["nodes"]["pre"])
        self.assertEqual(self.report["nodes"]["pre"], 106)

    def test_the_read_only_session_left_both_knowledge_bases_untouched(self):
        self.assertEqual(self.report["mutated_sides"], [])

    def test_the_session_roster_is_not_a_token_gesture(self):
        self.assertGreaterEqual(self.report["session_rows"], 25)


if __name__ == "__main__":
    unittest.main()
