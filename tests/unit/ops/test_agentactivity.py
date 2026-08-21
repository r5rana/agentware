"""`agentactivity`'s own unit file — task 99 wave 4, landed WITH the module.

`agentware_lib.ops.agentactivity` is the agent pillar's rollup: which sessions
belong to one resolved identity, whether that agent is running right now, and how
its spend breaks down by day and by feature. It was carved out of `ops.derive` in
this wave because an identity taxonomy plus a wall-clock reference instant is a
second obligation, and `ops.derive` had reached 587 of its 600-line cap holding
both.

`derive_agent_activity` had no direct test before this file. It was reached only
through `derive_agents`, whose payload is two of these dicts side by side — so a
selection rule that admitted the wrong sessions and one that dropped the right
ones both showed up there as "a number changed".

The properties, and how each can be got wrong:

  SELECT/IDENTITY — selection is on the RESOLVED identity, never on the passive
                    `stage` label a transcript's prose produces. A session that
                    merely discussed planning is not a planner.
  SELECT/NOISE    — empty zero-turn sessions are dropped FIRST, so they can
                    neither inflate a count nor become the most-recent row and
                    fake an active agent.
  SELECT/COPY     — selected rows are COPIED before `_identity` is stamped and
                    the pricing pass writes `cost_usd`, because the originals are
                    held in the shared session cache and would otherwise be
                    mutated for every later reader.
  ACTIVE/WINDOW   — only the NEWEST session can be live, and only inside the
                    active window. `end` is preferred over `start` so a session
                    still being written does not age out mid-run; an unparseable
                    stamp is NOT active, because an idle agent read as running is
                    the failure this window exists to prevent.
  STATUS/LIVE     — the live session's status is `active`; every other session
                    reports the terminal its identity resolved to, and `ended` is
                    neutral rather than a failure.
  BREAK/DATE      — a session with no date is left out of the day series entirely
                    rather than filed under a placeholder, because that series is
                    plotted and an invented bucket would draw.
  BREAK/UNATTRIB  — a session with no feature IS counted, under the unattributed
                    key: dropping it would make the per-feature costs silently
                    fail to sum to the total. `features` then excludes that key,
                    because it is not a feature.

Hermetic: no clock is read (`now` is always pinned), tempdirs only, stdlib
unittest only.
"""

from tests._paths import TESTS_DIR

import datetime
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

import agentware_lib.ops.agentactivity as agentactivity  # noqa: E402
from agentware_lib.ops.agentactivity import (  # noqa: E402
    _activity_breakdowns,
    _active_marker,
    _agent_status,
    _select_agent_sessions,
    derive_agent_activity,
)
from agentware_lib.ops.metrics import _UNATTRIBUTED  # noqa: E402


NOW = datetime.datetime.fromisoformat("2026-07-03T12:00:00+00:00")


def _ident(kind, feature=None, terminal="ended"):
    return {"kind": kind, "feature": feature, "name": feature or kind,
            "confidence": "high", "complete": terminal == "complete",
            "terminal": terminal}


def _sessrow(sid, **over):
    """A parsed-session row of the shape `collect_sessions` produces.

    The usage sub-dicts are part of that shape, not decoration: `aggregate_metrics`
    merges `tokens` and prices `models`, so a fixture without them would exercise
    a row this module can never actually be handed."""
    row = {"session_id": sid, "turns": 1, "tool_calls": 1, "subagent_count": 0,
           "total_tokens": 100, "duration_seconds": 60.0, "date": "2026-07-01",
           "start": "2026-07-01T09:00:00Z", "end": "2026-07-01T09:01:00Z",
           "cost_usd": 0.0, "stage": "work", "tools": {"Edit": 1},
           "tokens": {"input_tokens": 60, "output_tokens": 40,
                      "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0},
           "models": {}}
    row.update(over)
    return row


class ModuleContractTest(unittest.TestCase):
    def test_the_module_publishes_its_docstring_labels(self):
        doc = agentactivity.__doc__ or ""
        for label in ("Owns:", "Does NOT own:", "Layer deps:"):
            self.assertIn(label, doc,
                          "%s is missing the %s label the manifest parses"
                          % (agentactivity.__name__, label))

    def test_the_module_imports_nothing_above_its_own_layer(self):
        """R-MOD-02: `ops` may reach core/knowledge/retrieval and its own layer,
        never `features` or `cli`. Asserted on the source rather than on the
        import graph so it reddens before the module is even loadable."""
        with open(agentactivity.__file__, encoding="utf-8") as f:
            src = f.read()
        for forbidden in ("agentware_lib.features", "agentware_lib.cli"):
            self.assertNotIn(forbidden, src)


class SelectSessionsTest(unittest.TestCase):
    def test_only_the_asked_for_identity_kind_is_selected(self):
        """SELECT/IDENTITY."""
        rows = [_sessrow("a", stage="loop-main"),
                _sessrow("b", stage="work", code_edits=3)]
        sel = _select_agent_sessions(rows, "loop")
        self.assertEqual([r["session_id"] for r in sel], ["a"])
        self.assertEqual(sel[0]["_identity"]["kind"], "loop")

    def test_a_passive_stage_label_does_not_make_a_planner(self):
        """SELECT/IDENTITY: `stage` is inferred from prose. A session labelled
        `plan` that EDITED code is a worker that also wrote its plan."""
        rows = [_sessrow("a", stage="plan", code_edits=9)]
        self.assertEqual(_select_agent_sessions(rows, "plan"), [])
        self.assertEqual(
            [r["session_id"] for r in _select_agent_sessions(rows, "work")],
            ["a"])

    def test_a_noise_session_is_dropped_before_anything_else(self):
        """SELECT/NOISE."""
        rows = [_sessrow("noise", turns=0, tool_calls=0, total_tokens=0,
                         duration_seconds=0.0)]
        self.assertEqual(_select_agent_sessions(rows, "work"), [])

    def test_the_caller_s_rows_are_never_mutated(self):
        """SELECT/COPY: these rows come from the shared parse cache."""
        rows = [_sessrow("a", code_edits=1)]
        sel = _select_agent_sessions(rows, "work")
        self.assertEqual(len(sel), 1)
        self.assertNotIn("_identity", rows[0])
        sel[0]["total_tokens"] = 999
        self.assertEqual(rows[0]["total_tokens"], 100)


class ActiveMarkerTest(unittest.TestCase):
    def test_no_sessions_means_no_active_agent(self):
        self.assertEqual(_active_marker([], NOW), (False, None))

    def test_a_session_inside_the_window_is_active(self):
        """ACTIVE/WINDOW."""
        ordered = [_sessrow("live", end="2026-07-03T11:55:00Z")]
        self.assertEqual(_active_marker(ordered, NOW), (True, "live"))

    def test_a_session_outside_the_window_is_not(self):
        ordered = [_sessrow("old", end="2026-07-03T11:00:00Z")]
        self.assertEqual(_active_marker(ordered, NOW), (False, None))

    def test_start_is_the_fallback_when_the_session_has_no_end_yet(self):
        """ACTIVE/WINDOW: a session still being written has no end, and that is
        exactly the case the marker exists to report."""
        ordered = [_sessrow("running", start="2026-07-03T11:58:00Z", end=None)]
        self.assertEqual(_active_marker(ordered, NOW), (True, "running"))

    def test_an_unparseable_stamp_is_not_active(self):
        ordered = [_sessrow("weird", end="never", start=None)]
        self.assertEqual(_active_marker(ordered, NOW), (False, None))

    def test_only_the_newest_session_is_a_candidate(self):
        """An older session cannot be live while a newer one exists, so a
        marker that scanned the whole list would report the wrong id."""
        ordered = [_sessrow("newest", end="2026-07-03T11:00:00Z"),
                   _sessrow("older", end="2026-07-03T11:59:00Z")]
        self.assertEqual(_active_marker(ordered, NOW), (False, None))


class AgentStatusTest(unittest.TestCase):
    def test_the_live_session_reports_active_whatever_its_terminal(self):
        """STATUS/LIVE."""
        r = dict(_sessrow("s"), _identity=_ident("work", terminal="incomplete"))
        self.assertEqual(_agent_status(r, "s"), "active")

    def test_every_other_session_reports_its_own_terminal(self):
        r = dict(_sessrow("s"), _identity=_ident("work", terminal="complete"))
        self.assertEqual(_agent_status(r, "other"), "complete")

    def test_an_identity_with_no_terminal_is_neutral_rather_than_failed(self):
        r = dict(_sessrow("s"), _identity={"kind": "work", "feature": None})
        self.assertEqual(_agent_status(r, None), "ended")


class BreakdownsTest(unittest.TestCase):
    def test_a_dateless_session_is_absent_from_the_day_series(self):
        """BREAK/DATE."""
        sel = [dict(_sessrow("a", date=None), _identity=_ident("work", "F"))]
        by_day, by_feature = _activity_breakdowns(sel)
        self.assertEqual(by_day, {})
        self.assertEqual(by_feature["F"]["sessions"], 1)

    def test_a_featureless_session_is_counted_as_unattributed(self):
        """BREAK/UNATTRIB: dropping it would make the per-feature costs stop
        summing to the total."""
        sel = [dict(_sessrow("a"), _identity=_ident("work", None))]
        _by_day, by_feature = _activity_breakdowns(sel)
        self.assertEqual(list(by_feature), [_UNATTRIBUTED])

    def test_sessions_sharing_a_day_or_feature_accumulate(self):
        sel = [dict(_sessrow("a", cost_usd=0.5), _identity=_ident("work", "F")),
               dict(_sessrow("b", cost_usd=0.25),
                    _identity=_ident("work", "F"))]
        by_day, by_feature = _activity_breakdowns(sel)
        self.assertEqual(by_day["2026-07-01"]["sessions"], 2)
        self.assertEqual(by_day["2026-07-01"]["total_tokens"], 200)
        self.assertEqual(by_feature["F"]["cost_usd"], 0.75)


class DeriveAgentActivityTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_activity_")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def _session(self, sid, lines):
        d = os.path.join(self.kdir, "logs", "sessions", sid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "main.jsonl"), "w", encoding="utf-8") as f:
            for ln in lines:
                f.write(json.dumps(ln) + "\n")

    @staticmethod
    def _turn(ts, tool, inp, out=5):
        return {"type": "assistant", "timestamp": ts,
                "message": {"role": "assistant", "model": "m",
                            "usage": {"input_tokens": 10, "output_tokens": out},
                            "content": [{"type": "tool_use", "name": tool,
                                         "input": inp}]}}

    def test_an_empty_row_set_reports_a_zeroed_but_complete_payload(self):
        out = derive_agent_activity(self.kdir, "work", rows=[], now=NOW)
        self.assertEqual(out["kind"], "work")
        self.assertFalse(out["active"])
        self.assertIsNone(out["active_session"])
        self.assertEqual(out["session_count"], 0)
        self.assertEqual(out["attributed_count"], 0)
        self.assertEqual(out["incomplete_count"], 0)
        self.assertEqual(out["by_day"], {})
        self.assertEqual(out["features"], [])
        self.assertEqual(out["aggregate"]["total_tokens"], 0)

    def test_the_unattributed_key_is_not_reported_as_a_feature(self):
        """BREAK/UNATTRIB."""
        rows = [_sessrow("a", code_edits=1),
                _sessrow("b", code_edits=1, action_feature="260701-alpha")]
        out = derive_agent_activity(self.kdir, "work", rows=rows, now=NOW)
        self.assertEqual(out["session_count"], 2)
        self.assertEqual(out["attributed_count"], 1)
        self.assertEqual(out["features"], ["260701-alpha"])
        self.assertIn(_UNATTRIBUTED, out["by_feature"])

    def test_an_incomplete_planner_is_counted_as_incomplete(self):
        rows = [_sessrow("p", stage="plan", planner_spawned=True)]
        out = derive_agent_activity(self.kdir, "plan", rows=rows, now=NOW)
        self.assertEqual(out["incomplete_count"], 1)
        self.assertEqual(out["sessions"][0]["status"], "incomplete")

    def test_sessions_are_newest_first_and_the_live_one_is_marked(self):
        """STATUS/LIVE + ACTIVE/WINDOW, together: the newest row is BOTH the
        head of the history and the active session."""
        rows = [_sessrow("old", code_edits=1, start="2026-07-01T09:00:00Z",
                         end="2026-07-01T09:01:00Z"),
                _sessrow("new", code_edits=1, start="2026-07-03T11:50:00Z",
                         end="2026-07-03T11:55:00Z")]
        out = derive_agent_activity(self.kdir, "work", rows=rows, now=NOW)
        self.assertEqual([s["session_id"] for s in out["sessions"]],
                         ["new", "old"])
        self.assertTrue(out["active"])
        self.assertEqual(out["active_session"]["session_id"], "new")
        self.assertEqual(out["sessions"][0]["status"], "active")
        self.assertNotEqual(out["sessions"][1]["status"], "active")

    def test_with_no_rows_supplied_the_sessions_are_read_from_the_kb(self):
        self._session("s-work", [
            {"type": "user", "timestamp": "2026-07-01T09:00:00Z",
             "message": {"role": "user", "content": "build it"}},
            self._turn("2026-07-01T09:01:00Z", "Edit",
                       {"file_path": "work/260701-alpha/notes.md"})])
        out = derive_agent_activity(self.kdir, "work", now=NOW)
        self.assertEqual(out["session_count"], 1)
        self.assertEqual(out["features"], ["260701-alpha"])

    def test_pricing_is_applied_to_the_copies_and_reaches_the_aggregate(self):
        rows = [_sessrow("a", code_edits=1,
                         models={"claude-opus-4": {
                             "input_tokens": 1000, "output_tokens": 1000,
                             "cache_creation_input_tokens": 0,
                             "cache_read_input_tokens": 0}})]
        out = derive_agent_activity(self.kdir, "work", rows=rows, now=NOW)
        self.assertGreater(out["aggregate"]["cost_usd"], 0.0)
        self.assertNotIn("cost_usd", rows[0].get("_identity", {}))


if __name__ == "__main__":
    unittest.main()
