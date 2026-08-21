"""`metricscmd`'s own unit file — task 99 wave 4, landed with the paydown.

`agentware_lib.ops.metricscmd` is the `metrics` verb's CLI surface: the text
renderer, the wall-clock formatter, the `dashboard` launcher and the two parser
registrars. Until this file the module had no owning test. The renderer in
particular is only ever observed through whole-CLI captures, and those run one
corpus at a time — a section that never appears because its guard is wrong looks
exactly like a section that had nothing to say.

`_print_metrics_text` PRINTS, it does not return, so its contract cannot be
asserted from a return value. This file asserts it where it is observable: on
captured stdout, one section at a time.

  DESC/ORDER    — the header's filter description is built in a FIXED key order,
                  so two runs with the same filters print the same header. A
                  dict-order-dependent header is undiffable against itself.
  DESC/EMPTY    — no filters means no parenthesis at all, not an empty one.
  ROWS/TALLY    — a session with no tool calls prints no tally line. The report
                  is read by eye and a column of empty `tool calls:` lines hides
                  the sessions that did work.
  EXTRAS/GUARDS — the four optional sections are guarded on their OWN key, not on
                  one verbosity flag: `--session X` has no outcome (that is
                  per-feature) and `--retrieval` has none of them.
  EXTRAS/SUBCLAUSE — the cost line's anomaly count and the context line's
                  TRUNCATION-RISK marker appear only when there is something to
                  say. Both are the sub-clauses a whole-CLI capture over a quiet
                  corpus can never reach.
  DURATION/UNITS— the formatter drops the units it does not need, so a 30-second
                  run reads `30s` and not `0h00m30s`; the seconds are ROUNDED,
                  not truncated.

Hermetic: no CLI subprocess, no network, no KB; stdlib unittest only.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import os
import sys
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.ops.metricscmd as metricscmd  # noqa: E402
from agentware_lib.ops.metricscmd import (  # noqa: E402
    DASHBOARD_SERVER,
    _filter_desc,
    _fmt_duration,
    _print_agg_extras,
    _print_metrics_text,
    _print_session_rows,
)


def _capture(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a, **kw)
    return buf.getvalue()


def _row(sid, turns=1, tools=None, subagents=0, tokens=10, wall=30.0):
    return {"session_id": sid, "turns": turns, "tool_calls":
            sum((tools or {}).values()), "subagent_count": subagents,
            "total_tokens": tokens, "duration_seconds": wall,
            "tools": tools or {}}


def _agg(**over):
    base = {"session_count": 1, "turns": 1, "tool_calls": 0,
            "subagent_count": 0, "total_tokens": 10, "duration_seconds": 30.0,
            "tokens": {"input_tokens": 5, "output_tokens": 5,
                       "cache_creation_input_tokens": 0,
                       "cache_read_input_tokens": 0}}
    base.update(over)
    return base


class FmtDurationTest(unittest.TestCase):
    def test_only_the_units_that_are_needed_are_printed(self):
        """DURATION/UNITS."""
        self.assertEqual(_fmt_duration(30), "30s")
        self.assertEqual(_fmt_duration(90), "1m30s")
        self.assertEqual(_fmt_duration(3661), "1h01m01s")

    def test_seconds_are_rounded_rather_than_truncated(self):
        self.assertEqual(_fmt_duration(29.6), "30s")
        self.assertEqual(_fmt_duration(0.4), "0s")


class FilterDescTest(unittest.TestCase):
    def test_no_filters_produces_no_parenthesis_at_all(self):
        """DESC/EMPTY."""
        self.assertEqual(_filter_desc({}), "")
        self.assertEqual(_filter_desc({"session": None, "feature": None,
                                       "since": None}), "")

    def test_the_key_order_is_fixed_regardless_of_insertion_order(self):
        """DESC/ORDER: built the other way round, this would render
        `since=..., feature=..., session=...` and the header would depend on how
        the filter dict happened to be assembled."""
        flt = {}
        flt["since"] = "2026-07-01"
        flt["feature"] = "F"
        flt["session"] = "S"
        self.assertEqual(_filter_desc(flt),
                         "  (session=S, feature=F, since=2026-07-01)")

    def test_a_single_filter_renders_alone(self):
        self.assertEqual(_filter_desc({"feature": "F"}), "  (feature=F)")


class SessionRowsTest(unittest.TestCase):
    def test_a_session_with_no_tool_calls_prints_no_tally_line(self):
        """ROWS/TALLY."""
        out = _capture(_print_session_rows, [_row("s-quiet")])
        self.assertIn("s-quiet", out)
        self.assertNotIn("tool calls:", out)

    def test_the_tally_is_sorted_by_tool_name(self):
        out = _capture(_print_session_rows,
                       [_row("s", tools={"Read": 2, "Bash": 1, "Edit": 3})])
        self.assertIn("tool calls: Bash=1, Edit=3, Read=2", out)

    def test_one_line_per_session_in_the_order_given(self):
        out = _capture(_print_session_rows, [_row("b"), _row("a")])
        self.assertLess(out.index("b"), out.index("a"))


class AggExtrasTest(unittest.TestCase):
    def test_an_aggregate_with_none_of_the_optional_keys_prints_nothing(self):
        """EXTRAS/GUARDS."""
        self.assertEqual(_capture(_print_agg_extras, {}), "")

    def test_the_cost_line_omits_the_anomaly_clause_when_there_are_none(self):
        """EXTRAS/SUBCLAUSE."""
        out = _capture(_print_agg_extras,
                       {"cost_usd": 1.5, "cache_read_ratio": 0.25,
                        "cost_anomaly_dates": []})
        self.assertIn("cost: $1.5000  cache_read_ratio=0.250", out)
        self.assertNotIn("cost_anomalies", out)

    def test_the_anomaly_clause_reports_how_many_days_were_flagged(self):
        out = _capture(_print_agg_extras,
                       {"cost_usd": 1.5,
                        "cost_anomaly_dates": ["2026-07-01", "2026-07-02"]})
        self.assertIn("cost_anomalies=2", out)

    def test_the_truncation_marker_appears_only_at_risk(self):
        """EXTRAS/SUBCLAUSE: a quiet corpus can never reach this branch through
        the CLI, which is why it is asserted directly here."""
        calm = _capture(_print_agg_extras, {"context_tax": {
            "cache_read_per_turn": 1.0, "injected_tokens": 2,
            "context_window_pct": 0.1, "truncation_risk": False}})
        hot = _capture(_print_agg_extras, {"context_tax": {
            "cache_read_per_turn": 1.0, "injected_tokens": 2,
            "context_window_pct": 6.0, "truncation_risk": True}})
        self.assertIn("window_util=10.0%", calm)
        self.assertNotIn("TRUNCATION-RISK", calm)
        self.assertIn("TRUNCATION-RISK", hot)

    def test_the_outcome_line_names_its_own_source(self):
        out = _capture(_print_agg_extras,
                       {"outcome": {"outcome": "completed",
                                    "source": "metrics.jsonl"}})
        self.assertIn("outcome: completed (metrics.jsonl)", out)

    def test_an_empty_phase_cost_map_prints_no_phase_line(self):
        """EXTRAS/GUARDS: the key is present but there is nothing to say, and an
        empty `phase_costs:` line says something false."""
        out = _capture(_print_agg_extras, {"phase_costs": {"by_phase": {}}})
        self.assertEqual(out, "")

    def test_phase_costs_are_sorted_and_formatted_per_phase(self):
        out = _capture(_print_agg_extras, {"phase_costs": {"by_phase": {
            "work": {"cost_usd": 0.5}, "main": {"cost_usd": 0.25}}}})
        self.assertIn("phase_costs: main=$0.2500, work=$0.5000", out)


class PrintMetricsTextTest(unittest.TestCase):
    def test_the_header_pluralises_the_session_count(self):
        one = _capture(_print_metrics_text, [_row("a")], _agg(), {})
        two = _capture(_print_metrics_text, [_row("a"), _row("b")],
                       _agg(session_count=2), {})
        self.assertTrue(one.startswith("metrics: 1 session\n"))
        self.assertTrue(two.startswith("metrics: 2 sessions\n"))

    def test_the_header_carries_the_filter_description(self):
        out = _capture(_print_metrics_text, [_row("a")], _agg(),
                       {"feature": "260701-alpha"})
        self.assertIn("metrics: 1 session  (feature=260701-alpha)", out)

    def test_the_total_block_always_prints_even_with_no_sessions(self):
        """The totals are the answer to `metrics`; a zero-session render that
        printed nothing would look like a crash."""
        out = _capture(_print_metrics_text, [], _agg(session_count=0), {})
        self.assertIn("  ----", out)
        self.assertIn("TOTAL  sessions=0", out)
        self.assertIn("tokens: in=5 out=5", out)

    def test_the_sections_appear_in_their_fixed_order(self):
        out = _capture(_print_metrics_text, [_row("a")],
                       _agg(cost_usd=1.0,
                            context_tax={"cache_read_per_turn": 1.0,
                                         "injected_tokens": 0,
                                         "context_window_pct": 0.1,
                                         "truncation_risk": False},
                            outcome={"outcome": "completed", "source": "s"},
                            phase_costs={"by_phase": {"work":
                                                      {"cost_usd": 1.0}}}),
                       {})
        order = [out.index(k) for k in
                 ("metrics: 1 session", "  ----", "TOTAL", "tokens: in=",
                  "cost: $", "context_tax:", "outcome:", "phase_costs:")]
        self.assertEqual(order, sorted(order))


class DashboardServerPathTest(unittest.TestCase):
    def test_the_sidecar_is_a_directory_executed_as_a_package(self):
        """The launcher subprocess-execs this path; it is a PACKAGE dir, and the
        import guards (INV-1/INV-6) never scan it, which is the whole reason the
        dashboard's `http.server` can exist at all."""
        self.assertTrue(os.path.isdir(DASHBOARD_SERVER), DASHBOARD_SERVER)
        self.assertTrue(os.path.isfile(
            os.path.join(DASHBOARD_SERVER, "__main__.py")))

    def test_the_module_declares_both_of_its_verbs_with_a_live_registrar(self):
        """R-MOD-01: a verb is declared in the ONE module that owns it, and the
        declaration names a registrar that actually exists — a stale name would
        pass an AST-only reading of the tuple and fail at parser build."""
        verbs = {v: reg for v, _order, reg in metricscmd.__verbs__}
        self.assertEqual(set(verbs), {"metrics", "dashboard"})
        for reg in verbs.values():
            self.assertTrue(callable(getattr(metricscmd, reg, None)), reg)


if __name__ == "__main__":
    unittest.main()
