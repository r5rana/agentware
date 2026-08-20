"""The `fleet issues|impact` reporter's own unit file — task 99.

`agentware_lib.ops.fleet.fleetreport` had no owning unit file: its only coverage
was `tests/e2e/test_org_team_mode_e2e.py`, which drives the verb through a real
CLI over a real git org tree. That e2e is the right test for "does the verb reach
the sink", and the wrong one for "what does this arithmetic return" — it can only
assert the report as a whole, so a helper that is wrong in a way the fixture does
not happen to exercise stays green.

Task 99 split `cmd_fleet` (35 branches / 133 lines) into one helper per
subcommand plus one per obligation inside `impact`. Each of those helpers is now
callable on its own, so each is asserted here on CONCRETE returned or printed
values — the point of the split is that these are separately checkable, and a
test that only proved they were callable would have banked none of that.

The properties that matter, and why each is a way the split can be wrong:

  WINDOW     — `_fleet_issues` takes the caller's window predicate. If it
               re-derived one, `--since`/`--team` would be applied twice or in a
               subtly different order, and the counts would drift from the
               payload's own `n_events`.
  BOUNDARY   — a steering_sha resolves to the EARLIEST ts carrying it. Resolving
               to the latest would put that steering's own early runs on the
               "before" side and flatter every delta measured against it.
  SYMMETRY   — `_impact_split` scopes by team ONCE, before the split. Scoping the
               two sides differently changes one rate's denominator and nothing
               in the payload reveals it.
  FLOOR      — below FLEET_IMPACT_FLOOR_N per side the delta is SUPPRESSED and
               the caveat still rides the payload (R-ORG-07). A report that
               printed a confident delta from three events is the exact failure
               the floor exists to prevent.
  RAW/ROUND  — `_impact_payload` returns the unrounded rates alongside the
               rounded payload because the text renderer formats to 4 places and
               the payload rounds to 4 places; feeding the renderer the rounded
               number is a second rounding of an already-rounded value.

Hermetic: pure functions over in-memory event dicts, stdout captured in-process.
Stdlib unittest only — no tempdir, no git, no CLI.
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

from agentware_lib.ops.fleet.fleetreport import (  # noqa: E402
    FLEET_IMPACT_FLOOR_N,
    _fleet_impact,
    _fleet_issues,
    _impact_boundary,
    _impact_payload,
    _impact_split,
    _print_impact,
)


class _Args(object):
    """The argparse Namespace slice these helpers actually read."""

    def __init__(self, **kw):
        self.format = kw.pop("format", "text")
        for k, v in kw.items():
            setattr(self, k, v)


def _ev(ts, outcome="completed", team="platform", sha=None):
    e = {"ts": ts, "outcome": outcome, "team": team}
    if sha:
        e["steering_sha"] = sha
    return e


def _capture(fn, *a, **kw):
    """(return value, stdout text) for one call."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rv = fn(*a, **kw)
    return rv, buf.getvalue()


#: Five events per side of 2026-08-15 — enough that the sufficiency floor is
#: SATISFIED, so the tests below exercise the reported-delta branch rather than
#: only the suppression branch.
EVENTS = [
    _ev("2026-08-10", "completed", "platform", "aaaa111"),
    _ev("2026-08-11", "hit_max_iterations", "platform", "aaaa111"),
    _ev("2026-08-12", "completed", "platform", "aaaa111"),
    _ev("2026-08-13", "sync_failed", "platform", "aaaa111"),
    _ev("2026-08-14", "completed", "platform", "aaaa111"),
    _ev("2026-08-16", "completed", "platform", "bbbb222"),
    _ev("2026-08-17", "hit_max_iterations", "platform", "bbbb222"),
    _ev("2026-08-18", "completed", "platform", "bbbb222"),
    _ev("2026-08-19", "hit_max_iterations", "research", "bbbb222"),
    _ev("2026-08-20", "completed", "research", "bbbb222"),
]


class FleetIssuesTest(unittest.TestCase):
    """`_fleet_issues` — the recurrence ranking and its two renderings."""

    def test_it_ranks_outcomes_by_recurrence_then_name(self):
        """Ties break by NAME, not by insertion order; a dict-order ranking is
        stable on one Python and reorders the report on another."""
        rv, out = _capture(_fleet_issues, _Args(format="json"), None, EVENTS,
                           lambda e: True, None, None)
        self.assertEqual(rv, 0)
        import json as _json
        payload = _json.loads(out)
        self.assertEqual(payload["n_events"], 10)
        self.assertEqual(
            payload["top_outcomes"],
            [{"outcome": "completed", "count": 6},
             {"outcome": "hit_max_iterations", "count": 3},
             {"outcome": "sync_failed", "count": 1}])

    def test_it_counts_only_what_the_callers_window_admits(self):
        """The predicate is the CALLER's. Passing a window that admits one team
        must move `n_events` and every count, or the helper is filtering on
        something it re-derived."""
        rv, out = _capture(_fleet_issues, _Args(format="json"), None, EVENTS,
                           lambda e: e.get("team") == "research", None,
                           "research")
        self.assertEqual(rv, 0)
        import json as _json
        payload = _json.loads(out)
        self.assertEqual(payload["n_events"], 2)
        self.assertEqual(payload["team"], "research")
        self.assertEqual({r["outcome"]: r["count"] for r in payload["top_outcomes"]},
                         {"completed": 1, "hit_max_iterations": 1})

    def test_an_event_with_no_outcome_is_counted_as_unknown(self):
        """A blank outcome must land in a visible bucket. Dropping it would make
        a malformed event indistinguishable from a run that never happened."""
        rv, out = _capture(_fleet_issues, _Args(format="json"), None,
                           [{"ts": "2026-08-10"}], lambda e: True, None, None)
        self.assertEqual(rv, 0)
        import json as _json
        self.assertEqual(_json.loads(out)["top_outcomes"],
                         [{"outcome": "unknown", "count": 1}])

    def test_the_text_rendering_carries_the_window_in_its_header(self):
        rv, out = _capture(_fleet_issues, _Args(format="text"), None, EVENTS,
                           lambda e: e.get("team") == "research",
                           "2026-08-15", "research")
        self.assertEqual(rv, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0],
                         "fleet issues since 2026-08-15 team research: 2 events")
        self.assertIn("  completed              1", lines)


class ImpactBoundaryTest(unittest.TestCase):
    """`_impact_boundary` — t0 as a date, as a sha, or as an error."""

    def _err(self, fn, *a):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rv = fn(*a)
        return rv, buf.getvalue()

    def test_a_valid_date_is_its_own_boundary(self):
        self.assertEqual(_impact_boundary(EVENTS, "2026-08-15"), "2026-08-15")

    def test_a_sha_resolves_to_the_earliest_ts_carrying_it(self):
        """EARLIEST, not latest: the boundary is where that steering first
        reached the fleet, so its own early runs sit on the AFTER side."""
        self.assertEqual(_impact_boundary(EVENTS, "bbbb222"), "2026-08-16")
        self.assertEqual(_impact_boundary(EVENTS, "aaaa111"), "2026-08-10")

    def test_a_missing_t0_is_none_with_the_operator_diagnostic(self):
        rv, err = self._err(_impact_boundary, EVENTS, None)
        self.assertIsNone(rv)
        self.assertIn("--since <date|steering-sha> required", err)

    def test_an_unknown_sha_is_none_rather_than_a_silent_epoch(self):
        """Falling through to a default boundary would report a delta over a
        split the operator never asked for."""
        rv, err = self._err(_impact_boundary, EVENTS, "deadbeef")
        self.assertIsNone(rv)
        self.assertIn("steering_sha 'deadbeef' not found", err)


class ImpactSplitTest(unittest.TestCase):
    """`_impact_split` — the before/after partition, scoped symmetrically."""

    def test_it_partitions_at_the_boundary_with_after_inclusive(self):
        before, after = _impact_split(EVENTS, "2026-08-15", None)
        self.assertEqual([e["ts"] for e in before],
                         ["2026-08-10", "2026-08-11", "2026-08-12",
                          "2026-08-13", "2026-08-14"])
        self.assertEqual([e["ts"] for e in after],
                         ["2026-08-16", "2026-08-17", "2026-08-18",
                          "2026-08-19", "2026-08-20"])

    def test_an_event_exactly_on_the_boundary_is_after(self):
        before, after = _impact_split(EVENTS, "2026-08-16", None)
        self.assertEqual(len(before), 5)
        self.assertEqual(after[0]["ts"], "2026-08-16")

    def test_team_scoping_applies_to_both_sides_of_the_split(self):
        """The asymmetry this pins: scoping only one side changes that side's
        denominator, and the payload has no field that would show it."""
        before, after = _impact_split(EVENTS, "2026-08-15", "research")
        self.assertEqual([e["team"] for e in before + after],
                         ["research", "research"])
        self.assertEqual(len(before), 0)
        self.assertEqual(len(after), 2)

    def test_an_event_with_no_ts_sorts_before_every_boundary(self):
        """A missing ts reads as the empty string, not as a crash — a report may
        never be the thing that fails on one malformed line."""
        before, after = _impact_split([{"outcome": "completed"}],
                                      "2026-08-15", None)
        self.assertEqual(len(before), 1)
        self.assertEqual(after, [])


class ImpactPayloadTest(unittest.TestCase):
    """`_impact_payload` — the correlational computation (R-ORG-07)."""

    def test_it_reports_exact_counts_rates_and_the_ols_slope(self):
        payload, raw = _impact_payload(EVENTS, "2026-08-15", "2026-08-15", None)
        self.assertEqual(payload["command"], "impact")
        self.assertEqual((payload["n_before"], payload["n_after"]), (5, 5))
        # 2 of 5 non-`completed` on each side.
        self.assertEqual(payload["fail_rate_before"], 0.4)
        self.assertEqual(payload["fail_rate_after"], 0.4)
        self.assertEqual(payload["fail_rate_delta"], 0.0)
        self.assertEqual(payload["ols_slope_daily_fail"], 0.0)
        self.assertFalse(payload["insufficient_data"])
        self.assertEqual(payload["floor_n"], FLEET_IMPACT_FLOOR_N)

    def test_the_caveat_rides_every_payload(self):
        """R-ORG-07: the delta is correlational and says so, in the sufficient
        case and the suppressed case alike."""
        for boundary in ("2026-08-15", "2026-08-19"):
            payload, _raw = _impact_payload(EVENTS, boundary, boundary, None)
            self.assertIn("correlational, not causal", payload["caveat"])

    def test_below_the_floor_the_delta_is_suppressed_but_the_counts_stay(self):
        """Suppressing the DELTA while keeping n-per-side is the whole point: the
        operator still sees how much evidence there was."""
        payload, _raw = _impact_payload(EVENTS, "2026-08-19", "2026-08-19", None)
        self.assertTrue(payload["insufficient_data"])
        self.assertIsNone(payload["fail_rate_delta"])
        self.assertEqual((payload["n_before"], payload["n_after"]), (8, 2))
        # 3 of the 8 before-side events are non-`completed`.
        self.assertEqual(payload["fail_rate_before"], 0.375)

    def test_an_empty_side_has_a_none_rate_rather_than_a_zero_one(self):
        """A rate of 0.0 means "nothing failed"; None means "nothing ran". A
        report that conflated them would show an empty side as flawless."""
        payload, raw = _impact_payload(EVENTS, "2026-08-01", "2026-08-01", None)
        self.assertEqual(payload["n_before"], 0)
        self.assertIsNone(payload["fail_rate_before"])
        self.assertIsNone(payload["fail_rate_delta"])
        self.assertIsNone(raw["fail_rate_before"])

    def test_raw_carries_the_unrounded_rates_the_text_renderer_formats(self):
        """The payload rounds to 4 places; the text lines format to 4 places.
        Handing the renderer the rounded value would round it twice."""
        # 1 failure in 3 before-side events => a rate with no exact 4-place form.
        evs = ([_ev("2026-08-10", "hit_max_iterations")]
               + [_ev("2026-08-1%d" % i) for i in range(1, 7)])
        payload, raw = _impact_payload(evs, "2026-08-13", "2026-08-13", None)
        self.assertEqual(payload["fail_rate_before"], 0.3333)
        self.assertAlmostEqual(raw["fail_rate_before"], 1.0 / 3.0)
        self.assertNotEqual(raw["fail_rate_before"], payload["fail_rate_before"])

    def test_team_scoping_reaches_the_payload_counts(self):
        payload, _raw = _impact_payload(EVENTS, "2026-08-15", "2026-08-15",
                                        "research")
        self.assertEqual((payload["n_before"], payload["n_after"]), (0, 2))
        self.assertEqual(payload["team"], "research")


class PrintImpactTest(unittest.TestCase):
    """`_print_impact` — the json|text emit for one computed payload."""

    def _payload(self, boundary):
        return _impact_payload(EVENTS, boundary, boundary, None)

    def test_the_json_rendering_is_the_payload_and_nothing_else(self):
        payload, raw = self._payload("2026-08-15")
        _rv, out = _capture(_print_impact, _Args(format="json"), payload, raw)
        import json as _json
        self.assertEqual(_json.loads(out), payload)
        self.assertTrue(out.endswith("\n"))

    def test_the_sufficient_text_rendering_prints_counts_rates_slope_caveat(self):
        payload, raw = self._payload("2026-08-15")
        _rv, out = _capture(_print_impact, _Args(format="text"), payload, raw)
        self.assertEqual(out.splitlines(), [
            "fleet impact since 2026-08-15 (t0=2026-08-15):",
            "  n_before=5 fail_rate=0.4000  n_after=5 fail_rate=0.4000  "
            "delta=+0.0000",
            "  OLS daily-fail slope: 0.0000",
            "  caveat: correlational, not causal; confounders: "
            "team/task/model mix",
        ])

    def test_the_insufficient_text_rendering_names_the_floor_and_still_caveats(self):
        payload, raw = self._payload("2026-08-19")
        _rv, out = _capture(_print_impact, _Args(format="text"), payload, raw)
        self.assertEqual(out.splitlines(), [
            "fleet impact: insufficient data (n_before=8, n_after=2; "
            "floor=5 per side)",
            "  caveat: correlational, not causal; confounders: "
            "team/task/model mix",
        ])

    def test_an_unmeasurable_slope_renders_as_n_a_not_as_zero(self):
        """`n/a` and `0.0000` are different claims; a single-day window has no
        slope, and printing 0 would assert a flat trend that was never measured."""
        evs = [_ev("2026-08-16", "hit_max_iterations") for _ in range(5)]
        evs += [_ev("2026-08-10") for _ in range(5)]
        payload, raw = _impact_payload(evs, "2026-08-16", "2026-08-16", None)
        payload = dict(payload)
        raw = dict(raw, ols_slope=None)
        _rv, out = _capture(_print_impact, _Args(format="text"), payload, raw)
        self.assertIn("  OLS daily-fail slope: n/a", out)


class FleetImpactTest(unittest.TestCase):
    """`_fleet_impact` — the composition, asserted on its rc and its output."""

    def test_it_returns_zero_and_prints_the_report(self):
        rv, out = _capture(_fleet_impact, _Args(format="json"), EVENTS,
                           "2026-08-15", None)
        self.assertEqual(rv, 0)
        import json as _json
        self.assertEqual(_json.loads(out)["boundary_ts"], "2026-08-15")

    def test_it_resolves_a_sha_t0_through_to_the_payload(self):
        rv, out = _capture(_fleet_impact, _Args(format="json"), EVENTS,
                           "bbbb222", None)
        self.assertEqual(rv, 0)
        import json as _json
        payload = _json.loads(out)
        self.assertEqual(payload["since"], "bbbb222")
        self.assertEqual(payload["boundary_ts"], "2026-08-16")

    def test_an_unresolvable_t0_is_rc_one_with_no_report_on_stdout(self):
        """A report printed beside a nonzero rc is the worst outcome: a caller
        that reads stdout gets numbers for a boundary that was never resolved."""
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf_out), \
                contextlib.redirect_stderr(buf_err):
            rv = _fleet_impact(_Args(format="json"), EVENTS, "deadbeef", None)
        self.assertEqual(rv, 1)
        self.assertEqual(buf_out.getvalue(), "")
        self.assertIn("not found", buf_err.getvalue())


if __name__ == "__main__":
    unittest.main()
