"""`loophealth`'s own unit file — task 99 wave 4, landed with the paydown.

`agentware_lib.ops.loophealth` turns four read-only signals — repeated tool
calls, a flat burndown, token burn rate and context-window pressure — into an
`ok` / `at_risk` / `critical` verdict per run. Until this file the module had no
owning test; `test_metrics_health.LoopHealthTest` drives `derive_loop_health` and
reads the rolled-up status, which is the WORST of four checks and therefore tells
you nothing about which check produced it.

This wave split `_loop_health_dup_check`, the dead-loop detector (R-FAIL-04), and
its seams are the properties below. The dup check is the one whose false
positives were observed in production: a broad substring match once folded a
single chatty session into EVERY feature's count and made 100% of runs read
`critical`.

  DUP/DOMINANT  — a supplied session index is filtered to the sessions whose
                  DOMINANT feature is this one, not the ones that merely mention
                  it. The fixture here puts the WORST raw repeat count in a
                  foreign session, so a filter that did not run is visible in the
                  offender's NAME and not only in a count.
  DUP/EMPTY     — an index that filters empty stays empty. Widening it back to
                  "all sessions" is exactly how the false-critical returned.
  DUP/SCOPE     — a signature is (session, iteration, tool, args). Counting one
                  tool across a feature's whole multi-session history turns
                  normal re-reads into a dead loop; the fixture repeats the SAME
                  call in two sessions so a global count is distinguishable.
  DUP/EARLIEST  — ties go to the FIRST offender reached, so the report is
                  reproducible; the reported iteration is where the signature was
                  first seen, not where the scan ended.
  DUP/TIERS     — the at-risk tier is DERIVED from the threshold, so the two can
                  never be configured into disagreeing, and it never falls below
                  2 (a single call is not a repeat).
  FLAT/RUN      — the no-progress signal is consecutive iterations at the SAME
                  positive `tasks_remaining`. A None or a zero BREAKS the run:
                  zero means finished, and a gap is not evidence of a streak.
  WORST/SEVERITY— the rolled-up status is the most severe of its inputs, and an
                  unknown label must not silently outrank a real one.

Hermetic: tempdirs only; no CLI, no network, stdlib unittest only.
"""

from tests._paths import TESTS_DIR

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

from agentware_lib.ops.loophealth import (  # noqa: E402
    _dup_scope_sessions,
    _dup_signature_counts,
    _dup_status,
    _dup_worst_offender,
    _longest_flat_run,
    _loop_health_dup_check,
    _worst_status,
    derive_loop_health,
)
from agentware_lib.ops.looptrace import _trace_session_dirs  # noqa: E402


def _trace(*groups):
    """A minimal derive_trace-shaped payload: (iteration, [step dicts]) pairs."""
    return {"iterations": [{"iteration": it, "steps": steps}
                           for it, steps in groups]}


def _step(session, iteration, tool, args=""):
    return {"session_id": session, "iteration": iteration, "tool": tool,
            "args": args}


class WorstStatusTest(unittest.TestCase):
    def test_the_most_severe_input_wins(self):
        self.assertEqual(_worst_status(["ok", "at_risk", "critical"]),
                         "critical")
        self.assertEqual(_worst_status(["ok", "at_risk"]), "at_risk")

    def test_no_inputs_is_ok_rather_than_unknown(self):
        self.assertEqual(_worst_status([]), "ok")

    def test_an_unrecognised_label_cannot_outrank_a_real_one(self):
        """WORST/SEVERITY: an unknown status is ranked at severity 0, so it can
        never beat the `ok` the fold starts from and never displaces a real
        label. A typo therefore degrades to `ok` rather than either propagating
        itself or silently reporting a crisis."""
        self.assertEqual(_worst_status(["nonsense"]), "ok")
        self.assertEqual(_worst_status(["nonsense", "at_risk"]), "at_risk")
        self.assertEqual(_worst_status(["critical", "nonsense"]), "critical")


class DupSignatureTest(unittest.TestCase):
    def test_the_signature_is_scoped_by_session_and_iteration(self):
        """DUP/SCOPE: the same call in two sessions is two signatures, so a
        long-lived feature is not read as a stuck one."""
        counts = _dup_signature_counts(_trace(
            (1, [_step("s1", 1, "Bash", "make"), _step("s1", 1, "Bash", "make"),
                 _step("s2", 1, "Bash", "make")])))
        self.assertEqual(sorted(counts.values()), [1, 2])

    def test_a_different_argument_is_a_different_signature(self):
        counts = _dup_signature_counts(_trace(
            (1, [_step("s", 1, "Bash", "a"), _step("s", 1, "Bash", "b")])))
        self.assertEqual(sorted(counts.values()), [1, 1])

    def test_a_missing_tool_or_args_gets_the_neutral_value(self):
        counts = _dup_signature_counts(_trace(
            (1, [{"session_id": "s", "iteration": 1}] * 2)))
        self.assertEqual(list(counts), [("s", 1, "<unknown>", "")])

    def test_the_recorded_iteration_is_where_the_signature_first_appeared(self):
        """DUP/ITERATION. NARROWED at task 99 wave 4, and the name is kept
        deliberately (a rename reads as a deletion to the preserved-surface
        freeze): the iteration is a COMPONENT of the signature, so "where it
        first appeared" and "the iteration on the key" are the same value by
        construction, and the earlier assertion — that a separate first-seen map
        held 1 — could not fail for any input. What IS falsifiable is that the
        iteration is in the key at all: the same call in two DIFFERENT iterations
        must stay two signatures, so a badge can never point at an iteration the
        offending run of calls did not happen in."""
        counts = _dup_signature_counts(_trace(
            (1, [_step("s", 1, "Bash", "x"), _step("s", 1, "Bash", "x")]),
            (2, [_step("s", 2, "Bash", "x")])))
        self.assertEqual(counts[("s", 1, "Bash", "x")], 2)
        self.assertEqual(counts[("s", 2, "Bash", "x")], 1)

    def test_a_trace_with_no_iterations_counts_nothing(self):
        self.assertEqual(_dup_signature_counts({}), {})


class DupOffenderTest(unittest.TestCase):
    def test_the_worst_offender_is_the_highest_count(self):
        counts = {("s", 1, "Bash", ""): 3, ("s", 1, "Read", ""): 7}
        self.assertEqual(_dup_worst_offender(counts),
                         (7, ("s", 1, "Read", "")))

    def test_an_empty_map_reports_zero_and_no_offender(self):
        self.assertEqual(_dup_worst_offender({}), (0, None))

    def test_a_tie_keeps_the_first_signature_reached(self):
        """DUP/EARLIEST: strictly-greater makes the report reproducible."""
        counts = {}
        counts[("s", 1, "A", "")] = 4
        counts[("s", 1, "B", "")] = 4
        self.assertEqual(_dup_worst_offender(counts)[1], ("s", 1, "A", ""))


class DupStatusTest(unittest.TestCase):
    def test_the_three_tiers_are_ordered_and_exhaustive(self):
        self.assertEqual(_dup_status(12, 12, 7), "critical")
        self.assertEqual(_dup_status(11, 12, 7), "at_risk")
        self.assertEqual(_dup_status(7, 12, 7), "at_risk")
        self.assertEqual(_dup_status(6, 12, 7), "ok")
        self.assertEqual(_dup_status(0, 12, 7), "ok")


class DupScopeTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_lhealth_")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def _session(self, sid, feature, live=None):
        d = os.path.join(self.kdir, "logs", "sessions", sid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "main.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "user", "timestamp": "2026-07-01T09:00:00Z",
                "message": {"role": "user",
                            "content": "editing work/%s" % feature}}) + "\n")
            f.write(json.dumps({
                "type": "assistant", "timestamp": "2026-07-01T09:01:00Z",
                "message": {"role": "assistant",
                            "usage": {"output_tokens": 4},
                            "content": [{"type": "tool_use", "name": "Edit",
                                         "input": {"file_path":
                                                   "work/%s/x.md" % feature}}]}
            }) + "\n")
        if live is not None:
            with open(os.path.join(d, "live.jsonl"), "w",
                      encoding="utf-8") as f:
                for row in live:
                    f.write(json.dumps(row) + "\n")
        return d

    def test_none_means_no_index_and_is_passed_through(self):
        self.assertIsNone(_dup_scope_sessions(None, "260701-alpha"))

    def test_only_sessions_whose_dominant_feature_matches_survive(self):
        """DUP/DOMINANT."""
        a = self._session("s-a", "260701-alpha")
        b = self._session("s-b", "260702-beta")
        kept = _dup_scope_sessions([("s-a", a), ("s-b", b)], "260701-alpha")
        self.assertEqual([sid for sid, _d in kept], ["s-a"])

    def test_an_index_that_filters_empty_stays_empty(self):
        """DUP/EMPTY: re-widening here is how the false-critical returned."""
        b = self._session("s-b", "260702-beta")
        self.assertEqual(_dup_scope_sessions([("s-b", b)], "260701-alpha"), [])

    def test_a_foreign_session_cannot_supply_this_features_offender(self):
        """DUP/DOMINANT, end to end: the foreign session carries the WORST raw
        count in the tree, so a check that skipped the filter would report ITS
        tool as this feature's offender."""
        self._session("s-a", "260701-alpha",
                      live=[{"ts": "2026-07-01T09:0%d:00Z" % n, "tool": "Bash",
                             "status": "ok",
                             "input": json.dumps({"c": "make"})}
                            for n in range(1, 6)])
        self._session("s-b", "260702-beta",
                      live=[{"ts": "2026-07-01T10:%02d:00Z" % n,
                             "tool": "Read", "status": "ok",
                             "input": json.dumps({"c": "cat"})}
                            for n in range(1, 21)])
        dirs = _trace_session_dirs(self.kdir)
        out = _loop_health_dup_check(self.kdir, "260701-alpha", 4,
                                     sess_dirs=dirs, events=[])
        self.assertEqual(out["tool"], "Bash")
        self.assertEqual(out["max_repeat"], 5)
        self.assertTrue(out["flagged"])

    def test_the_badge_points_at_the_iteration_the_repeats_happened_in(self):
        """DUP/ITERATION, end to end and the only place it is observable.

        The same call runs twice in iteration 1 and four times in iteration 3.
        Both the COUNT (4, not 6) and the reported ITERATION (3, not 1 and not
        None) depend on the iteration being part of the signature, so a check
        that folded the run together would be visible in either field."""
        live = [{"ts": "2026-07-01T09:0%d:00Z" % n, "tool": "Bash",
                 "status": "ok", "input": json.dumps({"c": "make"})}
                for n in (1, 2, 5, 6, 7, 8)]
        self._session("s-a", "260701-alpha", live=live)
        events = [{"event": "iteration", "phase": "main", "iteration": 1,
                   "ts": "2026-07-01T09:00:00Z", "feature": "260701-alpha"},
                  {"event": "iteration", "phase": "main", "iteration": 3,
                   "ts": "2026-07-01T09:04:00Z", "feature": "260701-alpha"}]
        dirs = _trace_session_dirs(self.kdir)
        out = _loop_health_dup_check(self.kdir, "260701-alpha", 4,
                                     sess_dirs=dirs, events=events)
        self.assertEqual(out["max_repeat"], 4)
        self.assertEqual(out["iteration"], 3)
        self.assertEqual(out["tool"], "Bash")

    def test_the_at_risk_tier_is_derived_from_the_threshold(self):
        """DUP/TIERS."""
        self._session("s-a", "260701-alpha",
                      live=[{"ts": "2026-07-01T09:0%d:00Z" % n, "tool": "Bash",
                             "status": "ok", "input": json.dumps({"c": "x"})}
                            for n in range(1, 8)])
        dirs = _trace_session_dirs(self.kdir)
        crit = _loop_health_dup_check(self.kdir, "260701-alpha", 7,
                                      sess_dirs=dirs, events=[])
        risk = _loop_health_dup_check(self.kdir, "260701-alpha", 12,
                                      sess_dirs=dirs, events=[])
        calm = _loop_health_dup_check(self.kdir, "260701-alpha", 40,
                                      sess_dirs=dirs, events=[])
        self.assertEqual(crit["status"], "critical")
        self.assertEqual(risk["status"], "at_risk")
        self.assertEqual(calm["status"], "ok")
        self.assertIsNone(calm["detail"])

    def test_a_feature_with_no_steps_reports_ok_and_names_nobody(self):
        out = _loop_health_dup_check(self.kdir, "260799-none", 12,
                                     sess_dirs=[], events=[])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["max_repeat"], 0)
        self.assertIsNone(out["tool"])
        self.assertIsNone(out["iteration"])
        self.assertIsNone(out["detail"])
        self.assertFalse(out["flagged"])


class FlatRunTest(unittest.TestCase):
    def test_the_longest_streak_at_the_same_positive_remaining_is_found(self):
        """The run length counts the ITERATIONS in the streak, so three readings
        at 4 is a run of 3, and the reported iteration is where the streak ENDS
        — which is the iteration the operator has to look at."""
        run, at = _longest_flat_run([
            {"iteration": 1, "tasks_remaining": 4},
            {"iteration": 2, "tasks_remaining": 4},
            {"iteration": 3, "tasks_remaining": 4},
            {"iteration": 4, "tasks_remaining": 2}])
        self.assertEqual(run, 3)
        self.assertEqual(at, 3)

    def test_a_zero_remaining_breaks_the_run(self):
        """FLAT/RUN: zero means finished, which is progress, not a stall."""
        run, _at = _longest_flat_run([{"tasks_remaining": 0}] * 5)
        self.assertEqual(run, 0)

    def test_a_gap_breaks_the_run_rather_than_bridging_it(self):
        """FLAT/RUN: a missing reading is not evidence of a streak. The pair
        WITHOUT the gap is asserted beside it, because "1" alone is also what a
        bridge that reset the counter would produce — only the contrast between
        the two shows the gap was honoured."""
        bridged, _a = _longest_flat_run([
            {"tasks_remaining": 3}, {"tasks_remaining": None},
            {"tasks_remaining": 3}])
        contiguous, _b = _longest_flat_run([
            {"tasks_remaining": 3}, {"tasks_remaining": 3}])
        self.assertEqual((bridged, contiguous), (1, 2))

    def test_an_empty_burndown_has_no_run(self):
        self.assertEqual(_longest_flat_run([]), (0, None))


class DeriveLoopHealthTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_lhealth_all_")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def test_an_empty_knowledge_dir_reports_no_features_and_stays_ok(self):
        out = derive_loop_health(self.kdir)
        self.assertEqual(out["features"], [])
        self.assertEqual(out["status"], "ok")

    def test_a_named_feature_reports_all_four_checks(self):
        os.makedirs(os.path.join(self.kdir, "work", "260701-alpha"))
        with open(os.path.join(self.kdir, "work", "260701-alpha", "plan.md"),
                  "w", encoding="utf-8") as f:
            f.write("# plan\n")
        out = derive_loop_health(self.kdir, feature="260701-alpha")
        self.assertEqual(sorted(out["checks"]),
                         ["context_window", "duplicate_tool_calls",
                          "no_progress", "token_burn"])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["flagged_checks"], [])
        self.assertIsNone(out["offender"])


if __name__ == "__main__":
    unittest.main()
