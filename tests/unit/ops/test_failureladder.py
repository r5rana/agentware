"""Owning unit file for `agentware_lib.ops.failureladder` — task 99 wave 18.

The module carried a `test-ownership` row, and what the pre-existing coverage
could not do is the reason it did. `FailuresTest` (in `test_metrics_alerts.py`)
drives `derive_failures` over a synthetic transcript and reads the fourteen
fields it returns — so it reddens when the FEATURE breaks, and stays green for
every decision the fold makes on the way there. Nothing in the corpus called
`_err_tallies`, `_ladder_tally`, `_fail_step_is_web`, `_fail_step_is_kb_lookup`
or `_classify_recovery` directly, and two of them are only reachable through a
trace that has already normalised its own steps.

What is asserted here is the module's PROMISE, not its shape:

  * THE TALLIES ARE A FUNCTION OF A STEP ALONE — err-by-tool, the web count and
    the KB count read one step and never its neighbours, which is exactly what
    separates them from the ladder. Each is driven with a step list built to
    make the OTHER two zero, so a tally that read the wrong field would show.
  * THE LADDER IS A FUNCTION OF A PAIR. It reads the step that FOLLOWED each
    ERR, so it is the one tally that depends on the caller's global re-sort;
    the fixture puts the recovery of the FIRST error after the SECOND error in
    list order, so a body reading the previous step, or the next ERR rather than
    the next STEP, produces a different ladder.
  * "SAME TOOL, SAME ARGS" AND "SAME TOOL, CHANGED ARGS" ARE DIFFERENT TIERS
    (R-FAIL-04). The two are distinguishable ONLY by the argument strings, so
    they are asserted as a PAIR over fixtures that differ in nothing else.
  * AN ERR WITH NO FOLLOWING STEP IS `unrecovered`, NOT A TIER. A run that ended
    on its error recovered from nothing, and counting it as `reasoning` would
    make the ladder claim a recovery that never happened.
  * A WEB STEP IS NEVER ALSO A KB LOOKUP. `_fail_step_is_kb_lookup` refuses a
    web tool BEFORE reading its args, so a `WebSearch` whose query mentions
    `agentware recall` counts once, in the tier that ran.
  * THE MARKER TALLY DELEGATES. It must agree with `worklog scan` on a waived
    marker and on a cross-category (skill) promotion — the two shapes a
    re-implemented scan loop got wrong before it was made a delegation.
  * `self_heal_count` IS AN INT OR ZERO. `True` is an `int` in Python, and the
    loop's terminal event is JSON from disk, so the bool check is the difference
    between a tally and a lie.

Hermetic: every knowledge dir is a fresh mkdtemp; nothing here reads the
operator's KB, and no test writes outside its own temp tree.
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

import agentware_lib.ops.failureladder as failureladder    # noqa: E402


def _step(tool, status="ok", args="{}", index=None):
    """A trace step as `ops.looptrace` hands it over: a bounded dict with the
    tool name already normalised and the input already serialised."""
    s = {"tool": tool, "status": status, "args": args}
    if index is not None:
        s["index"] = index
    return s


class FailLadderStepClassifierTest(unittest.TestCase):
    """The three argument-only classifiers: pure functions of ONE step dict."""

    def test_flad_a_web_tool_is_a_web_escalation(self):
        for tool in sorted(failureladder._FAIL_WEB_TOOLS):
            self.assertTrue(failureladder._fail_step_is_web(_step(tool)),
                            "%s must count as a web escalation" % tool)

    def test_flad_a_non_web_tool_is_not(self):
        self.assertFalse(failureladder._fail_step_is_web(_step("Bash")))
        self.assertFalse(failureladder._fail_step_is_web(_step(None)))
        self.assertFalse(failureladder._fail_step_is_web({}))

    def test_flad_the_web_check_reads_the_tool_and_not_the_args(self):
        # A Bash step whose command literally names WebSearch is NOT a web
        # escalation: the tool is what ran.
        self.assertFalse(failureladder._fail_step_is_web(
            _step("Bash", args='{"command": "echo WebSearch"}')))

    def test_flad_a_kb_lookup_is_recognised_by_every_signature(self):
        for sig in failureladder._FAIL_KB_SIGNATURES:
            self.assertTrue(
                failureladder._fail_step_is_kb_lookup(
                    _step("Bash", args='{"command": "%s x"}' % sig)),
                "%r must count as a KB lookup" % sig)

    def test_flad_a_web_step_is_never_also_a_kb_lookup(self):
        # The refusal runs BEFORE the args are read, so a web search FOR the
        # phrase counts once — in the tier that actually ran.
        s = _step("WebSearch", args='{"query": "scripts/agentware recall"}')
        self.assertTrue(failureladder._fail_step_is_web(s))
        self.assertFalse(failureladder._fail_step_is_kb_lookup(s))

    def test_flad_a_step_with_no_args_is_not_a_kb_lookup(self):
        self.assertFalse(failureladder._fail_step_is_kb_lookup({"tool": "Bash"}))
        self.assertFalse(failureladder._fail_step_is_kb_lookup(
            {"tool": "Bash", "args": None}))

    def test_flad_an_unrelated_command_is_not_a_kb_lookup(self):
        self.assertFalse(failureladder._fail_step_is_kb_lookup(
            _step("Bash", args='{"command": "git status"}')))


class FailLadderClassifyRecoveryTest(unittest.TestCase):
    """`_classify_recovery`: the ERR step + what FOLLOWED it -> an R-FAIL tier."""

    def setUp(self):
        self.err = _step("Bash", status="ERR", args='{"command": "make"}')

    def test_flad_no_next_step_is_unrecovered(self):
        self.assertIsNone(failureladder._classify_recovery(self.err, None))

    def test_flad_a_following_web_search_is_tier_web(self):
        self.assertEqual(
            failureladder._classify_recovery(self.err, _step("WebSearch")), "web")

    def test_flad_a_following_kb_lookup_is_tier_kb(self):
        nxt = _step("Bash", args='{"command": "scripts/agentware query --id x"}')
        self.assertEqual(failureladder._classify_recovery(self.err, nxt), "kb")

    def test_flad_a_step_that_reads_as_both_is_classified_web(self):
        # NOT because `web` is checked first: `_fail_step_is_kb_lookup` REFUSES a
        # web tool before it reads the args, so swapping the two checks changes
        # nothing (measured — the swap is an equivalent mutant). The refusal is
        # what decides, and it is asserted directly above; this pins the
        # CONSEQUENCE at the tier boundary, which is where a reader looks.
        nxt = _step("WebFetch", args='{"url": "scripts/agentware recall"}')
        self.assertFalse(failureladder._fail_step_is_kb_lookup(nxt))
        self.assertEqual(failureladder._classify_recovery(self.err, nxt), "web")

    def test_flad_same_tool_changed_args_is_tier_inputs(self):
        nxt = _step("Bash", args='{"command": "make -j1"}')
        self.assertEqual(failureladder._classify_recovery(self.err, nxt), "inputs")

    def test_flad_same_tool_same_args_is_tier_reasoning(self):
        nxt = _step("Bash", args='{"command": "make"}')
        self.assertEqual(failureladder._classify_recovery(self.err, nxt),
                         "reasoning")

    def test_flad_inputs_and_reasoning_differ_only_by_the_args(self):
        # The pair, asserted together over fixtures identical in every other
        # field: this is the whole content of R-FAIL-04 ("every retry MUST
        # change an input"), and a body that stopped comparing args would
        # collapse both into one tier while every single-case test still passed.
        same = _step("Bash", args='{"command": "make"}')
        changed = _step("Bash", args='{"command": "make -j1"}')
        self.assertNotEqual(
            failureladder._classify_recovery(self.err, same),
            failureladder._classify_recovery(self.err, changed))

    def test_flad_a_different_tool_is_tier_switch(self):
        self.assertEqual(
            failureladder._classify_recovery(self.err, _step("Read")), "switch")

    def test_flad_a_missing_args_key_compares_as_the_empty_string(self):
        # Two steps that both omit `args` are the SAME arguments, not two
        # unknowns — so this is `reasoning`, never `inputs`.
        err = {"tool": "Bash", "status": "ERR"}
        self.assertEqual(failureladder._classify_recovery(err, {"tool": "Bash"}),
                         "reasoning")

    def test_flad_every_tier_it_returns_is_in_the_published_order(self):
        tiers = set(failureladder._FAIL_LADDER_TIERS)
        for nxt in (_step("WebSearch"),
                    _step("Bash", args='{"command": "scripts/agentware audit"}'),
                    _step("Bash", args='{"command": "other"}'),
                    _step("Bash", args='{"command": "make"}'),
                    _step("Read")):
            self.assertIn(failureladder._classify_recovery(self.err, nxt), tiers)


class FailLadderErrTalliesTest(unittest.TestCase):
    """`_err_tallies`: the three per-step tallies, in one pass."""

    def test_flad_an_empty_step_list_tallies_nothing(self):
        self.assertEqual(failureladder._err_tallies([]), ({}, 0, 0))

    def test_flad_only_err_steps_reach_the_per_tool_tally(self):
        steps = [_step("Bash", status="ERR"), _step("Bash"),
                 _step("Read", status="ERR"), _step("Read")]
        by_tool, web, kb = failureladder._err_tallies(steps)
        self.assertEqual(by_tool, {"Bash": 1, "Read": 1})
        self.assertEqual((web, kb), (0, 0))

    def test_flad_repeated_failures_of_one_tool_accumulate(self):
        steps = [_step("Bash", status="ERR")] * 3
        by_tool, _w, _k = failureladder._err_tallies(steps)
        self.assertEqual(by_tool, {"Bash": 3})

    def test_flad_an_err_step_with_no_tool_lands_in_the_unknown_bucket(self):
        # NOT reachable through `derive_failures` — `ops.looptrace` normalises
        # every step's tool to `<unknown>` before the fold sees it, at both its
        # read arms. It is asserted HERE, at the helper's own boundary, because
        # the helper is a published function whose contract is a step dict; a
        # dropped failure would make `err_by_tool` disagree with `err_count`.
        by_tool, _w, _k = failureladder._err_tallies(
            [{"status": "ERR"}, {"status": "ERR", "tool": ""}])
        self.assertEqual(by_tool, {"<unknown>": 2})

    def test_flad_the_web_count_ignores_status(self):
        # A web search is an escalation whether or not it succeeded.
        steps = [_step("WebSearch"), _step("WebFetch", status="ERR")]
        _by_tool, web, _kb = failureladder._err_tallies(steps)
        self.assertEqual(web, 2)

    def test_flad_the_three_tallies_are_independent(self):
        # One list, three answers, each of which must be reachable while the
        # other two stay zero — otherwise a body that fused them would pass.
        errs = [_step("Bash", status="ERR")]
        webs = [_step("WebSearch")]
        kbs = [_step("Bash", args='{"command": "scripts/agentware recall x"}')]
        self.assertEqual(failureladder._err_tallies(errs), ({"Bash": 1}, 0, 0))
        self.assertEqual(failureladder._err_tallies(webs), ({}, 1, 0))
        self.assertEqual(failureladder._err_tallies(kbs), ({}, 0, 1))

    def test_flad_one_step_can_be_counted_by_two_tallies(self):
        # A FAILED KB lookup is both an ERR and a KB lookup; the tallies answer
        # different questions and neither excludes the other.
        step = _step("Bash", status="ERR",
                     args='{"command": "scripts/agentware recall x"}')
        by_tool, web, kb = failureladder._err_tallies([step])
        self.assertEqual((by_tool, web, kb), ({"Bash": 1}, 0, 1))


class FailLadderLadderTallyTest(unittest.TestCase):
    """`_ladder_tally`: the one tally that reads a step's NEIGHBOUR."""

    def _zero(self):
        return {t: 0 for t in failureladder._FAIL_LADDER_TIERS}

    def test_flad_an_empty_list_gives_the_zeroed_ladder(self):
        ladder, unrecovered = failureladder._ladder_tally([])
        self.assertEqual(ladder, self._zero())
        self.assertEqual(unrecovered, 0)

    def test_flad_a_run_with_no_errors_climbs_nothing(self):
        ladder, unrecovered = failureladder._ladder_tally(
            [_step("Bash"), _step("Read")])
        self.assertEqual(ladder, self._zero())
        self.assertEqual(unrecovered, 0)

    def test_flad_the_recovery_is_the_next_step_not_the_next_error(self):
        # Two ERRs with an unrelated step between them. The first error's
        # recovery is that step (`switch`), NOT the second error.
        steps = [_step("Read", status="ERR"),
                 _step("Grep"),
                 _step("Edit", status="ERR"),
                 _step("WebSearch")]
        ladder, unrecovered = failureladder._ladder_tally(steps)
        self.assertEqual(ladder["switch"], 1)
        self.assertEqual(ladder["web"], 1)
        self.assertEqual(unrecovered, 0)

    def test_flad_the_recovery_is_the_NEXT_step_and_not_the_previous_one(self):
        # The fixture is deliberately asymmetric: reading backwards would score
        # `web` for the error and leave `switch` at zero.
        steps = [_step("WebSearch"),
                 _step("Read", status="ERR"),
                 _step("Grep")]
        ladder, _unrecovered = failureladder._ladder_tally(steps)
        self.assertEqual(ladder["switch"], 1)
        self.assertEqual(ladder["web"], 0)

    def test_flad_an_error_at_the_end_of_the_run_is_unrecovered(self):
        ladder, unrecovered = failureladder._ladder_tally(
            [_step("Bash"), _step("Edit", status="ERR")])
        self.assertEqual(ladder, self._zero())
        self.assertEqual(unrecovered, 1)

    def test_flad_unrecovered_and_the_tiers_partition_the_errors(self):
        steps = [_step("Read", status="ERR"), _step("WebSearch"),
                 _step("Bash", status="ERR"), _step("Bash", args="{}"),
                 _step("Edit", status="ERR")]
        ladder, unrecovered = failureladder._ladder_tally(steps)
        self.assertEqual(sum(ladder.values()) + unrecovered, 3)

    def test_flad_every_tier_key_is_present_even_at_zero(self):
        ladder, _ = failureladder._ladder_tally([_step("Bash")])
        self.assertEqual(sorted(ladder), sorted(failureladder._FAIL_LADDER_TIERS))


class FailLadderDeriveFailuresTest(unittest.TestCase):
    """`derive_failures`: the fold over a REAL trace + outcome + worklog."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-flad-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self._write("index.json", json.dumps({"version": 1, "entries": []}))

    def _write(self, rel, text):
        path = os.path.join(self.kdir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def _jsonl(self, rel, rows):
        self._write(rel, "".join(json.dumps(r) + "\n" for r in rows))

    def _session(self, sid, feature, live):
        self._jsonl("logs/sessions/%s/main.jsonl" % sid, [
            {"type": "user", "timestamp": "2026-07-01T09:00:00Z",
             "message": {"role": "user",
                         "content": "work on work/%s" % feature}}])
        self._jsonl("logs/sessions/%s/live.jsonl" % sid, live)

    @staticmethod
    def _live(ts, tool, status="ok", inp=None):
        return {"ts": ts, "tool": tool, "status": status,
                "input": json.dumps(inp or {}), "response": "{}"}

    def test_flad_an_absent_feature_yields_a_zeroed_fold(self):
        f = failureladder.derive_failures(self.kdir, "260799-nope")
        self.assertEqual(f["feature"], "260799-nope")
        self.assertEqual(f["scope"], "feature")
        self.assertEqual((f["step_count"], f["err_count"], f["err_rate"]),
                         (0, 0, 0.0))
        self.assertEqual(f["err_by_tool"], {})
        self.assertEqual(f["ladder"],
                         {t: 0 for t in failureladder._FAIL_LADDER_TIERS})
        self.assertEqual(f["unrecovered"], 0)
        self.assertEqual(f["self_heal_count"], 0)
        self.assertEqual(f["markers"]["learned"], {"total": 0, "unpromoted": 0})

    def test_flad_the_err_rate_is_errors_over_steps(self):
        feat = "260701-alpha"
        self._session("s1", feat, [
            self._live("2026-07-01T09:00:01Z", "Read", "ERR",
                       {"file_path": "/x"}),
            self._live("2026-07-01T09:00:02Z", "Bash",
                       inp={"command": "scripts/agentware recall x"}),
            self._live("2026-07-01T09:00:03Z", "Bash", inp={"command": "ok"}),
            self._live("2026-07-01T09:00:04Z", "Bash", inp={"command": "ok2"}),
        ])
        f = failureladder.derive_failures(self.kdir, feat)
        self.assertEqual(f["step_count"], 4)
        self.assertEqual(f["err_count"], 1)
        self.assertEqual(f["err_rate"], 0.25)
        self.assertEqual(f["ladder"]["kb"], 1)
        self.assertEqual(f["kb_lookup_count"], 1)

    def test_flad_the_rate_is_rounded_to_four_places(self):
        feat = "260701-round"
        self._session("s-round", feat, [
            self._live("2026-07-01T09:00:0%d" % n + "Z", "Bash",
                       "ERR" if n == 0 else "ok", {"command": str(n)})
            for n in range(3)])
        f = failureladder.derive_failures(self.kdir, feat)
        self.assertEqual(f["err_rate"], 0.3333)

    def test_flad_steps_from_two_sessions_are_folded_in_global_order(self):
        # The two sessions' steps interleave only after the sort by `index`;
        # a fold that read them session-by-session would classify the LAST step
        # of the first session against the FIRST step of the second.
        feat = "260701-two"
        self._session("s-a", feat, [
            self._live("2026-07-01T09:00:01Z", "Edit", "ERR",
                       {"file_path": "/y"})])
        self._session("s-b", feat, [
            self._live("2026-07-01T10:00:01Z", "Bash", inp={"command": "x"})])
        f = failureladder.derive_failures(self.kdir, feat)
        self.assertEqual(f["step_count"], 2)
        self.assertEqual(f["err_count"], 1)
        self.assertEqual(sum(f["ladder"].values()) + f["unrecovered"], 1)

    def test_flad_the_published_tier_order_is_a_copy_of_the_constant(self):
        f = failureladder.derive_failures(self.kdir, "260799-nope")
        self.assertEqual(f["ladder_order"], failureladder._FAIL_LADDER_TIERS)
        f["ladder_order"].append("mutated")
        self.assertEqual(failureladder._FAIL_LADDER_TIERS,
                         ["kb", "reasoning", "inputs", "switch", "web"])

    def test_flad_an_integer_self_heal_count_is_read_from_the_terminal(self):
        feat = "260701-heal"
        self._jsonl("logs/metrics.jsonl", [
            {"event": "terminal", "ts": "2026-07-01T11:00:00Z", "feature": feat,
             "outcome": "completed", "iterations_used": 2, "max": 10,
             "self_heal_count": 3, "tasks_total": 1, "tasks_done": 1,
             "promise_status": "signalled"}])
        f = failureladder.derive_failures(self.kdir, feat)
        self.assertEqual(f["self_heal_count"], 3)

    def test_flad_a_boolean_self_heal_count_is_refused(self):
        # `True` IS an int in Python, and this value is JSON from disk. Without
        # the explicit bool check the report would show a self-heal count of 1
        # for a run that never self-healed. The event's `outcome` must be one
        # the derivation TREATS as terminal (`hit_max_iterations`); an outcome
        # outside that closed set is ignored entirely, and a fixture using one
        # reads zero whether the guard is there or not.
        feat = "260701-bool"
        self._jsonl("logs/metrics.jsonl", [
            {"event": "terminal", "ts": "2026-07-01T11:00:00Z", "feature": feat,
             "outcome": "hit_max_iterations", "iterations_used": 1, "max": 10,
             "self_heal_count": True, "tasks_total": 1, "tasks_done": 0,
             "promise_status": "absent"}])
        f = failureladder.derive_failures(self.kdir, feat)
        self.assertEqual(f["self_heal_count"], 0)

    def test_flad_a_non_numeric_self_heal_count_is_refused(self):
        feat = "260701-str"
        self._jsonl("logs/metrics.jsonl", [
            {"event": "terminal", "ts": "2026-07-01T11:00:00Z", "feature": feat,
             "outcome": "completed", "iterations_used": 1, "max": 10,
             "self_heal_count": "three", "tasks_total": 1, "tasks_done": 1,
             "promise_status": "signalled"}])
        f = failureladder.derive_failures(self.kdir, feat)
        self.assertEqual(f["self_heal_count"], 0)

    def test_flad_the_fold_never_writes(self):
        feat = "260701-ro"
        self._session("s-ro", feat, [
            self._live("2026-07-01T09:00:01Z", "Read", "ERR",
                       {"file_path": "/x"})])
        idx = os.path.join(self.kdir, "index.json")
        before = os.path.getmtime(idx)
        failureladder.derive_failures(self.kdir, feat)
        self.assertEqual(os.path.getmtime(idx), before)


class FailLadderMarkerTallyTest(unittest.TestCase):
    """`_failure_marker_tally`: a DELEGATION to the completion gate's scanner."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-flad-mk-")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def _index(self, entries):
        path = os.path.join(self.kdir, "index.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "entries": entries}, f)

    def _worklog(self, feature, body):
        path = os.path.join(self.kdir, "work", feature, "worklog.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)

    def test_flad_an_absent_worklog_tallies_zero(self):
        self._index([])
        self.assertEqual(
            failureladder._failure_marker_tally(self.kdir, "260799-nope"),
            {"learned": {"total": 0, "unpromoted": 0},
             "decision": {"total": 0, "unpromoted": 0}})

    def test_flad_open_markers_are_counted_per_kind(self):
        self._index([])
        self._worklog("260701-m", "> LEARNED: one\n> LEARNED: two\n"
                                  "> DECISION: three\n")
        tally = failureladder._failure_marker_tally(self.kdir, "260701-m")
        self.assertEqual(tally["learned"], {"total": 2, "unpromoted": 2})
        self.assertEqual(tally["decision"], {"total": 1, "unpromoted": 1})

    def test_flad_a_promoted_marker_is_closed(self):
        self._index([{"id": "learn-a-thing", "path": "learnings/a-thing.md",
                      "category": "learnings"}])
        self._worklog("260701-p", "> LEARNED: (Promoted: learn-a-thing) x\n")
        tally = failureladder._failure_marker_tally(self.kdir, "260701-p")
        self.assertEqual(tally["learned"], {"total": 1, "unpromoted": 0})

    def test_flad_a_waived_marker_is_closed(self):
        # The first of the two shapes a re-implemented scan loop got wrong: the
        # tally said "unpromoted" while the gate itself said "closed".
        self._index([])
        self._worklog("260701-w", "> LEARNED: one\n> WAIVED: not durable\n")
        tally = failureladder._failure_marker_tally(self.kdir, "260701-w")
        self.assertEqual(tally["learned"], {"total": 1, "unpromoted": 0})

    def test_flad_a_cross_category_skill_promotion_is_closed(self):
        # The second shape: a `> LEARNED:` promoted as a SKILL. Only the shared
        # scanner resolves that, which is why this function delegates.
        self._index([{"id": "skill-a-thing", "path": "skills/a-thing/SKILL.md",
                      "category": "skills"}])
        self._worklog("260701-s", "> LEARNED: (Promoted: skill-a-thing) x\n")
        tally = failureladder._failure_marker_tally(self.kdir, "260701-s")
        self.assertEqual(tally["learned"], {"total": 1, "unpromoted": 0})

    def test_flad_the_tally_agrees_with_the_scanner_it_delegates_to(self):
        import agentware_lib.knowledge.worklog as worklog
        import agentware_lib.knowledge.index as index
        self._index([{"id": "learn-a-thing", "path": "learnings/a-thing.md",
                      "category": "learnings"}])
        body = ("> LEARNED: (Promoted: learn-a-thing) closed\n"
                "> LEARNED: open\n> DECISION: open too\n"
                "> WAIVED: the decision above\n")
        self._worklog("260701-agree", body)
        data, _err = index.load_index(self.kdir)
        scan = worklog.scan_worklog_markers(
            os.path.join(self.kdir, "work", "260701-agree", "worklog.md"),
            self.kdir, data)
        self.assertEqual(
            failureladder._failure_marker_tally(self.kdir, "260701-agree"),
            scan["per_kind"])


class FailLadderExportsTest(unittest.TestCase):
    """The module's published surface."""

    def test_flad_every_exported_name_exists(self):
        for name in failureladder._EXPORTS:
            self.assertTrue(hasattr(failureladder, name),
                            "_EXPORTS names %r, which the module does not define"
                            % name)

    def test_flad_the_web_tool_set_and_kb_signatures_are_non_empty(self):
        self.assertTrue(failureladder._FAIL_WEB_TOOLS)
        self.assertTrue(failureladder._FAIL_KB_SIGNATURES)

    def test_flad_the_tier_order_is_the_R_FAIL_ladder(self):
        self.assertEqual(failureladder._FAIL_LADDER_TIERS,
                         ["kb", "reasoning", "inputs", "switch", "web"])


if __name__ == "__main__":
    unittest.main()
