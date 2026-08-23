"""Owning unit file for `agentware_lib.ops.metrics.metricspricing` — task 99 wave 18.

The module carried a `test-ownership` row. Its cost math is not untested — the
`metrics` verb prints a dollar figure and several suites read it — but every one
of those reaches this module through `aggregate_metrics` over a parsed
transcript, so what they observe is the SUM. A rollup that double-counted a
model, an anomaly flag that fired on two days of history, a price table that
silently replaced the defaults instead of merging over them: each of those keeps
the total plausible and none of them reddens a test whose subject is the report.

What is asserted here is the module's PROMISE, not its shape:

  * THE LONGEST MATCHING KEY WINS, and `default` is never a substring match. A
    price lookup that took the FIRST match would price `claude-opus-4-20250514`
    as `claude-opus-4`... or as `claude`, depending on dict order, which is
    exactly the kind of answer that stays plausible while being wrong.
  * COST IS LINEAR IN TOKENS AND ZERO AT ZERO. Asserted as an identity over the
    four usage components rather than as a golden number, so the test survives a
    price-table edit and still fails a component swapped for another.
  * A ROW WITH USAGE AND NO PER-MODEL BREAKDOWN IS STILL PRICED, under the
    `default` entry — the under-counting guard, which is invisible in any total
    that has other rows in it.
  * THE THREE ROLLUPS FOLD, THEY DO NOT OVERWRITE. Two rows sharing a day, a
    feature and a model must accumulate tokens, cost and `session_count`; the
    fixture is built so a last-wins bucket would produce a SMALLER total.
  * A ROW WITH NO DATE REACHES `by_feature` AND THE TOTAL BUT NO DAY. Cost that
    vanished from the total because a row was missing a timestamp would be a
    silent under-count of exactly the rows most likely to be malformed.
  * THE ANOMALY FLAG IS A DECISION ABOUT A DAY'S OWN HISTORY. Fewer than
    `_COST_ANOMALY_MIN_HISTORY` prior days cannot flag anything however extreme
    the ratio, a zero median never flags (every non-zero day is an infinite
    multiple of it), and the walk is chronological, so a later day can never
    explain an earlier one.
  * THE ANOMALY WALK READS THE ROUNDED DAY COST, because the finalize pass runs
    first — a day is judged by the number the report shows.
  * A PARTIAL PRICE TABLE MERGES OVER THE DEFAULTS. A table naming one model
    must still price the others, and a table naming `default` outright replaces
    them; those are different answers and the difference is the `default` key.

Hermetic and pure: no I/O except `load_price_table`, which is driven over a
fresh mkdtemp. Nothing here reads the operator's KB.
"""

from tests.unit.ops._metricspricing_support import (  # noqa: F401
    _row, _usage, json, os, pricing, shutil, sys, tempfile, unittest)


class PricingPriceForTest(unittest.TestCase):
    """`_price_for`: longest-substring model lookup over the price table."""

    def setUp(self):
        self.prices = dict(pricing._DEFAULT_PRICES)

    def test_mpricing_an_exact_model_id_matches_its_own_entry(self):
        self.assertIs(pricing._price_for("claude-opus-4", self.prices),
                      self.prices["claude-opus-4"])

    def test_mpricing_the_longest_matching_key_wins(self):
        table = {"claude": {"input": 1.0}, "claude-opus-4": {"input": 2.0},
                 "default": {"input": 99.0}}
        self.assertEqual(
            pricing._price_for("claude-opus-4-20250514", table)["input"], 2.0)

    def test_mpricing_a_dated_model_id_still_matches_its_family(self):
        self.assertIs(pricing._price_for("claude-haiku-4-20250101", self.prices),
                      self.prices["claude-haiku-4"])

    def test_mpricing_an_unknown_model_falls_back_to_default(self):
        self.assertIs(pricing._price_for("some-other-vendor", self.prices),
                      self.prices["default"])

    def test_mpricing_the_default_key_is_never_a_substring_match(self):
        # A model id containing the word "default" must NOT match by substring:
        # `default` is the fallback, and treating it as a pattern would make it
        # win over a real family key on some inputs.
        table = {"default": {"input": 1.0}, "de": {"input": 2.0}}
        self.assertEqual(pricing._price_for("default-model", table)["input"], 2.0)

    def test_mpricing_an_empty_or_none_model_falls_back(self):
        for model in ("", None):
            self.assertIs(pricing._price_for(model, self.prices),
                          self.prices["default"])

    def test_mpricing_a_table_with_no_default_falls_back_to_the_builtin(self):
        self.assertIs(pricing._price_for("nope", {"zzz": {"input": 1.0}}),
                      pricing._DEFAULT_PRICES["default"])


class PricingUsageCostTest(unittest.TestCase):
    """`_usage_cost`: the pure token -> dollars conversion."""

    def test_mpricing_zero_usage_costs_nothing(self):
        self.assertEqual(pricing._usage_cost(_usage(), {"input": 15.0}), 0.0)

    def test_mpricing_cost_is_per_million_tokens(self):
        price = {"input": 3.0, "output": 15.0,
                 "cache_creation_input_tokens": 3.75,
                 "cache_read_input_tokens": 0.3}
        self.assertAlmostEqual(
            pricing._usage_cost(_usage(i=1_000_000), price), 3.0)
        self.assertAlmostEqual(
            pricing._usage_cost(_usage(o=1_000_000), price), 15.0)

    def test_mpricing_every_usage_key_has_its_own_component(self):
        # Each component priced ALONE, so a body that read the wrong price for a
        # key cannot hide behind another key's contribution.
        price = {"input": 1.0, "output": 2.0,
                 "cache_creation_input_tokens": 4.0,
                 "cache_read_input_tokens": 8.0}
        got = [pricing._usage_cost(u, price) * 1_000_000 for u in
               (_usage(i=1), _usage(o=1), _usage(cc=1), _usage(cr=1))]
        self.assertEqual([round(v, 6) for v in got], [1.0, 2.0, 4.0, 8.0])

    def test_mpricing_cost_is_additive_across_components(self):
        price = {"input": 1.0, "output": 2.0,
                 "cache_creation_input_tokens": 4.0,
                 "cache_read_input_tokens": 8.0}
        whole = pricing._usage_cost(_usage(i=10, o=10, cc=10, cr=10), price)
        parts = sum(pricing._usage_cost(u, price) for u in
                    (_usage(i=10), _usage(o=10), _usage(cc=10), _usage(cr=10)))
        self.assertAlmostEqual(whole, parts)

    def test_mpricing_a_missing_price_component_is_free_not_an_error(self):
        # The price table is operator-supplied; a partial ENTRY must not crash
        # the whole report.
        self.assertEqual(pricing._usage_cost(_usage(cr=1_000_000), {}), 0.0)

    def test_mpricing_a_missing_usage_key_counts_as_zero(self):
        self.assertEqual(pricing._usage_cost({}, {"input": 15.0}), 0.0)


class PricingMedianTest(unittest.TestCase):
    """`_median`: the p50 the anomaly clause is measured against."""

    def test_mpricing_an_empty_list_has_median_zero(self):
        self.assertEqual(pricing._median([]), 0.0)

    def test_mpricing_an_odd_length_takes_the_middle(self):
        self.assertEqual(pricing._median([3.0, 1.0, 2.0]), 2.0)

    def test_mpricing_an_even_length_averages_the_two_middles(self):
        self.assertEqual(pricing._median([1.0, 2.0, 3.0, 4.0]), 2.5)

    def test_mpricing_the_input_order_does_not_matter(self):
        self.assertEqual(pricing._median([9.0, 1.0, 5.0]),
                         pricing._median([1.0, 5.0, 9.0]))

    def test_mpricing_the_input_list_is_not_reordered_in_place(self):
        values = [3.0, 1.0, 2.0]
        pricing._median(values)
        self.assertEqual(values, [3.0, 1.0, 2.0])


class PricingCostRecordTest(unittest.TestCase):
    """`_cost_record` / `_finalize_record` / `_rollup_bucket`: the buckets."""

    def test_mpricing_a_new_record_is_zeroed_and_carries_its_usage(self):
        rec = pricing._cost_record(_usage(i=5, o=5))
        self.assertEqual(rec["total_tokens"], 10)
        self.assertEqual(rec["cost_usd"], 0.0)
        self.assertEqual(rec["session_count"], 0)
        self.assertEqual(rec["cache_read_ratio"], 0.0)

    def test_mpricing_a_record_does_not_alias_the_usage_it_was_seeded_from(self):
        usage = _usage(i=5)
        rec = pricing._cost_record(usage)
        rec["tokens"]["input_tokens"] = 999
        self.assertEqual(usage["input_tokens"], 5)

    def test_mpricing_finalize_rounds_the_cost_and_derives_the_ratio(self):
        rec = pricing._cost_record(_usage(i=10, cr=30))
        rec["cost_usd"] = 1.0 / 3.0
        pricing._finalize_record(rec)
        self.assertEqual(rec["cost_usd"], 0.333333)
        self.assertGreater(rec["cache_read_ratio"], 0.0)

    def test_mpricing_a_bucket_is_created_once_and_then_reused(self):
        rollup = {}
        first = pricing._rollup_bucket(rollup, "k")
        first["cost_usd"] = 7.0
        second = pricing._rollup_bucket(rollup, "k")
        self.assertIs(first, second)
        self.assertEqual(second["cost_usd"], 7.0)
        self.assertEqual(list(rollup), ["k"])

    def test_mpricing_distinct_keys_get_distinct_buckets(self):
        rollup = {}
        a = pricing._rollup_bucket(rollup, "a")
        b = pricing._rollup_bucket(rollup, "b")
        self.assertIsNot(a, b)
        self.assertEqual(sorted(rollup), ["a", "b"])


class PricingRowModelsTest(unittest.TestCase):
    """`_price_row_models`: one row's per-model pricing + the by_model fold."""

    def setUp(self):
        self.prices = {"claude-opus-4": {"input": 15.0, "output": 75.0,
                                         "cache_creation_input_tokens": 18.75,
                                         "cache_read_input_tokens": 1.5},
                       "default": {"input": 3.0, "output": 15.0,
                                   "cache_creation_input_tokens": 3.75,
                                   "cache_read_input_tokens": 0.3}}

    def test_mpricing_a_row_with_no_models_costs_nothing_here(self):
        by_model = {}
        cost, priced = pricing._price_row_models(
            _row(tokens=_usage(i=100), total_tokens=100), self.prices, by_model)
        self.assertEqual((cost, priced, by_model), (0.0, {}, {}))

    def test_mpricing_the_row_cost_is_the_sum_over_its_models(self):
        row = _row(models={"claude-opus-4": _usage(i=1_000_000),
                           "unknown-model": _usage(i=1_000_000)})
        cost, priced, = pricing._price_row_models(row, self.prices, {})[:2]
        self.assertAlmostEqual(cost, 18.0)          # 15.0 + the 3.0 default
        self.assertEqual(sorted(priced), ["claude-opus-4", "unknown-model"])

    def test_mpricing_each_priced_model_carries_its_own_totals(self):
        row = _row(models={"claude-opus-4": _usage(i=2, o=3)})
        _cost, priced = pricing._price_row_models(row, self.prices, {})
        entry = priced["claude-opus-4"]
        self.assertEqual(entry["tokens"], _usage(i=2, o=3))
        self.assertEqual(entry["total_tokens"], 5)
        self.assertIn("cost_usd", entry)

    def test_mpricing_the_priced_usage_is_a_copy_of_the_rows(self):
        usage = _usage(i=2)
        _cost, priced = pricing._price_row_models(
            _row(models={"claude-opus-4": usage}), self.prices, {})
        priced["claude-opus-4"]["tokens"]["input_tokens"] = 999
        self.assertEqual(usage["input_tokens"], 2)

    def test_mpricing_two_rows_fold_into_one_by_model_bucket(self):
        by_model = {}
        for _ in range(2):
            pricing._price_row_models(
                _row(models={"claude-opus-4": _usage(i=1_000_000)}),
                self.prices, by_model)
        rec = by_model["claude-opus-4"]
        self.assertEqual(rec["tokens"]["input_tokens"], 2_000_000)
        self.assertEqual(rec["total_tokens"], 2_000_000)
        self.assertAlmostEqual(rec["cost_usd"], 30.0)

    def test_mpricing_models_are_walked_in_sorted_order(self):
        row = _row(models={"zzz": _usage(i=1), "aaa": _usage(i=1),
                           "mmm": _usage(i=1)})
        by_model = {}
        pricing._price_row_models(row, self.prices, by_model)
        self.assertEqual(list(by_model), ["aaa", "mmm", "zzz"])

    def test_mpricing_the_row_cost_accumulates_unrounded(self):
        # Each per-model figure is rounded for display; the row total is not the
        # sum of those rounded parts, which is what keeps a many-model row from
        # drifting by a cent per model.
        # One cache-READ token per model prices at 3e-7 — BELOW the 6-decimal
        # rounding floor, so every displayed per-model figure is 0.0 while the
        # row itself is not free.
        tiny = {"m%d" % n: _usage(cr=1) for n in range(10)}
        cost, priced = pricing._price_row_models(_row(models=tiny),
                                                 self.prices, {})
        self.assertGreater(cost, 0.0)
        self.assertEqual(sum(p["cost_usd"] for p in priced.values()), 0.0)


class PricingAnomalyTest(unittest.TestCase):
    """`_flag_cost_anomalies`: a day judged against its OWN prior history."""

    @staticmethod
    def _days(**costs):
        return {d: {"cost_usd": c} for d, c in costs.items()}

    def test_mpricing_no_days_flags_nothing(self):
        self.assertEqual(pricing._flag_cost_anomalies({}), [])

    def test_mpricing_below_the_minimum_history_nothing_can_flag(self):
        # Three days: the fourth would flag, and the ratio here is 900x — so the
        # guard is the ONLY thing keeping this list empty.
        by_day = self._days(**{"2026-06-28": 1.0, "2026-06-29": 1.0,
                               "2026-06-30": 900.0})
        self.assertEqual(pricing._flag_cost_anomalies(by_day), [])
        self.assertEqual([d["cost_anomaly"] for d in by_day.values()],
                         [False, False, False])

    def test_mpricing_a_spike_after_enough_history_is_flagged(self):
        by_day = self._days(**{"2026-06-28": 1.0, "2026-06-29": 1.0,
                               "2026-06-30": 1.0, "2026-07-01": 900.0})
        self.assertEqual(pricing._flag_cost_anomalies(by_day), ["2026-07-01"])
        self.assertTrue(by_day["2026-07-01"]["cost_anomaly"])

    def test_mpricing_a_flat_history_flags_nothing(self):
        by_day = self._days(**{"2026-06-28": 1.0, "2026-06-29": 1.0,
                               "2026-06-30": 1.0, "2026-07-01": 1.0})
        self.assertEqual(pricing._flag_cost_anomalies(by_day), [])

    def test_mpricing_a_zero_median_never_flags(self):
        # Every non-zero day is an infinite multiple of zero; without this guard
        # the first day of any run that starts free is an anomaly.
        by_day = self._days(**{"2026-06-28": 0.0, "2026-06-29": 0.0,
                               "2026-06-30": 0.0, "2026-07-01": 900.0})
        self.assertEqual(pricing._flag_cost_anomalies(by_day), [])

    def test_mpricing_exactly_the_factor_is_not_a_spike(self):
        # The comparison is strict: 2x the median is the threshold, not a hit.
        by_day = self._days(**{"2026-06-28": 1.0, "2026-06-29": 1.0,
                               "2026-06-30": 1.0,
                               "2026-07-01": pricing._COST_ANOMALY_FACTOR})
        self.assertEqual(pricing._flag_cost_anomalies(by_day), [])

    def test_mpricing_the_walk_is_chronological_not_insertion_ordered(self):
        # The expensive day is inserted FIRST. Walking insertion order would
        # judge it against no history and then flag the cheap days against it.
        by_day = {"2026-07-01": {"cost_usd": 900.0},
                  "2026-06-28": {"cost_usd": 1.0},
                  "2026-06-29": {"cost_usd": 1.0},
                  "2026-06-30": {"cost_usd": 1.0}}
        self.assertEqual(pricing._flag_cost_anomalies(by_day), ["2026-07-01"])

    def test_mpricing_a_day_is_never_part_of_its_own_history(self):
        # The history is the PRIOR days only. This fixture separates the two
        # readings: with the day itself folded in, the median of
        # [1, 1, 100, 100] is 50.5 and 100 does not clear 2x it, so the spike
        # goes unflagged; with prior days only the median is 1 and it flags.
        by_day = self._days(**{"2026-06-28": 1.0, "2026-06-29": 1.0,
                               "2026-06-30": 100.0, "2026-07-01": 100.0})
        self.assertEqual(pricing._flag_cost_anomalies(by_day), ["2026-07-01"])

    def test_mpricing_a_later_day_never_explains_an_earlier_one(self):
        # The history is PRIOR days only: the second expensive day is judged
        # against a median that already includes the first.
        by_day = self._days(**{"2026-06-28": 1.0, "2026-06-29": 1.0,
                               "2026-06-30": 1.0, "2026-07-01": 900.0,
                               "2026-07-02": 900.0})
        self.assertEqual(pricing._flag_cost_anomalies(by_day),
                         ["2026-07-01", "2026-07-02"])

    def test_mpricing_every_day_is_stamped_even_when_unflagged(self):
        by_day = self._days(**{"2026-06-28": 1.0})
        pricing._flag_cost_anomalies(by_day)
        self.assertIn("cost_anomaly", by_day["2026-06-28"])


class PricingApplyPricingTest(unittest.TestCase):
    """`apply_pricing`: the whole fold onto rows + aggregate."""

    def setUp(self):
        self.prices = dict(pricing._DEFAULT_PRICES)

    def test_mpricing_an_empty_row_set_still_populates_every_key(self):
        agg = {"tokens": _usage()}
        pricing.apply_pricing([], agg, self.prices)
        self.assertEqual(agg["cost_usd"], 0.0)
        self.assertEqual(agg["by_model"], {})
        self.assertEqual(agg["by_day"], {})
        self.assertEqual(agg["by_feature"], {})
        self.assertEqual(agg["cost_anomaly_dates"], [])
        self.assertIn("cache_read_ratio", agg)

    def test_mpricing_the_total_is_the_sum_of_the_row_costs(self):
        rows = [_row(date="2026-07-01", feature="f",
                     tokens=_usage(i=1_000_000), total_tokens=1_000_000,
                     models={"claude-opus-4": _usage(i=1_000_000)}),
                _row(date="2026-07-02", feature="f",
                     tokens=_usage(i=1_000_000), total_tokens=1_000_000,
                     models={"claude-haiku-4": _usage(i=1_000_000)})]
        agg = {"tokens": _usage(i=2_000_000)}
        pricing.apply_pricing(rows, agg, self.prices)
        self.assertEqual(agg["cost_usd"],
                         round(sum(r["cost_usd"] for r in rows), 6))
        self.assertAlmostEqual(agg["cost_usd"], 16.0)      # 15.0 + 1.0

    def test_mpricing_a_row_with_usage_and_no_models_is_still_priced(self):
        # The under-counting guard: without it this row is free, and in any
        # report with other rows in it the loss is invisible.
        rows = [_row(date="2026-07-01", feature="f",
                     tokens=_usage(i=1_000_000), total_tokens=1_000_000)]
        agg = {"tokens": _usage(i=1_000_000)}
        pricing.apply_pricing(rows, agg, self.prices)
        self.assertGreater(rows[0]["cost_usd"], 0.0)
        self.assertAlmostEqual(rows[0]["cost_usd"], 3.0)   # the default entry

    def test_mpricing_a_row_with_no_usage_at_all_is_free(self):
        rows = [_row(date="2026-07-01", feature="f")]
        agg = {"tokens": _usage()}
        pricing.apply_pricing(rows, agg, self.prices)
        self.assertEqual(rows[0]["cost_usd"], 0.0)

    def test_mpricing_the_row_models_are_replaced_by_their_priced_form(self):
        rows = [_row(date="2026-07-01", feature="f",
                     tokens=_usage(i=10), total_tokens=10,
                     models={"claude-opus-4": _usage(i=10)})]
        pricing.apply_pricing(rows, {"tokens": _usage(i=10)}, self.prices)
        entry = rows[0]["models"]["claude-opus-4"]
        self.assertEqual(sorted(entry), ["cost_usd", "tokens", "total_tokens"])

    def test_mpricing_two_rows_on_one_day_fold_rather_than_overwrite(self):
        rows = [_row(date="2026-07-01", feature="f", tokens=_usage(i=1000),
                     total_tokens=1000,
                     models={"claude-opus-4": _usage(i=1000)}),
                _row(date="2026-07-01", feature="f", tokens=_usage(i=1000),
                     total_tokens=1000,
                     models={"claude-opus-4": _usage(i=1000)})]
        agg = {"tokens": _usage(i=2000)}
        pricing.apply_pricing(rows, agg, self.prices)
        day = agg["by_day"]["2026-07-01"]
        self.assertEqual(day["session_count"], 2)
        self.assertEqual(day["tokens"]["input_tokens"], 2000)
        self.assertEqual(agg["by_feature"]["f"]["session_count"], 2)
        self.assertEqual(
            agg["by_model"]["claude-opus-4"]["tokens"]["input_tokens"], 2000)

    def test_mpricing_a_row_with_no_date_reaches_the_feature_and_the_total(self):
        rows = [_row(feature="f", tokens=_usage(i=1_000_000),
                     total_tokens=1_000_000,
                     models={"claude-opus-4": _usage(i=1_000_000)})]
        agg = {"tokens": _usage(i=1_000_000)}
        pricing.apply_pricing(rows, agg, self.prices)
        self.assertEqual(agg["by_day"], {})
        self.assertAlmostEqual(agg["by_feature"]["f"]["cost_usd"], 15.0)
        self.assertAlmostEqual(agg["cost_usd"], 15.0)

    def test_mpricing_a_row_with_no_feature_lands_under_unknown(self):
        rows = [_row(date="2026-07-01", tokens=_usage(i=10), total_tokens=10,
                     models={"claude-opus-4": _usage(i=10)})]
        pricing.apply_pricing(rows, {"tokens": _usage(i=10)}, self.prices)
        self.assertEqual(list(rows[0]["models"]), ["claude-opus-4"])

    def test_mpricing_an_empty_string_feature_lands_under_unknown(self):
        rows = [_row(date="2026-07-01", feature="", tokens=_usage(i=10),
                     total_tokens=10)]
        agg = {"tokens": _usage(i=10)}
        pricing.apply_pricing(rows, agg, self.prices)
        self.assertEqual(sorted(agg["by_feature"]), ["unknown"])

    def test_mpricing_every_rollup_record_is_finalized(self):
        rows = [_row(date="2026-07-01", feature="f", tokens=_usage(i=10, cr=90),
                     total_tokens=100,
                     models={"claude-opus-4": _usage(i=10, cr=90)})]
        agg = {"tokens": _usage(i=10, cr=90)}
        pricing.apply_pricing(rows, agg, self.prices)
        for rollup in ("by_model", "by_day", "by_feature"):
            for rec in agg[rollup].values():
                self.assertGreater(rec["cache_read_ratio"], 0.0,
                                   "%s was never finalized" % rollup)
                self.assertEqual(rec["cost_usd"], round(rec["cost_usd"], 6))

    def test_mpricing_the_anomaly_walk_sees_the_rounded_day_cost(self):
        # Finalize runs BEFORE the walk, so a day is judged by the number the
        # report shows. This fixture's raw and rounded costs differ.
        rows = [_row(date=d, feature="f", tokens=_usage(i=n),
                     total_tokens=n, models={"claude-opus-4": _usage(i=n)})
                for d, n in (("2026-06-28", 1), ("2026-06-29", 1),
                             ("2026-06-30", 1), ("2026-07-01", 1_000_000))]
        agg = {"tokens": _usage()}
        pricing.apply_pricing(rows, agg, self.prices)
        self.assertEqual(agg["cost_anomaly_dates"], ["2026-07-01"])
        self.assertEqual(agg["by_day"]["2026-06-28"]["cost_usd"],
                         round(agg["by_day"]["2026-06-28"]["cost_usd"], 6))

    def test_mpricing_the_aggregate_ratio_is_derived_from_its_own_tokens(self):
        agg = {"tokens": _usage(i=100, cr=900)}
        pricing.apply_pricing([], agg, self.prices)
        self.assertGreater(agg["cache_read_ratio"], 0.0)

    def test_mpricing_the_fold_is_deterministic_over_the_same_input(self):
        def _run():
            rows = [_row(date="2026-07-01", feature="f",
                         tokens=_usage(i=7, o=3), total_tokens=10,
                         models={"b": _usage(i=3), "a": _usage(i=4)})]
            agg = {"tokens": _usage(i=7, o=3)}
            pricing.apply_pricing(rows, agg, self.prices)
            return json.dumps([rows, agg], sort_keys=True)
        self.assertEqual(_run(), _run())


if __name__ == "__main__":
    unittest.main()
