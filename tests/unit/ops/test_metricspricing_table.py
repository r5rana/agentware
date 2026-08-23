"""PricingLoadPriceTableTest, PricingContextWindowTest, PricingExportsTest — the
TABLE-AND-SIZING half of the `agentware_lib.ops.metrics.metricspricing` owning
suite (task 99 wave 18).

Split by ADDITION from `test_metricspricing.py` when the owning file crossed the
600-line `tests/**` cap. The seam is the module's own two obligations: the file
it left behind asserts what a token COSTS, and this one asserts where the price
table and the context window it is measured against COME FROM. Ownership is
unaffected: a module is owned by exactly one file, and that file is still
`test_metricspricing.py`.

See `tests.unit.ops._metricspricing_support` for the shared header.
"""

from tests.unit.ops._metricspricing_support import (  # noqa: F401
    _row, _usage, json, os, pricing, shutil, sys, tempfile, unittest)


class PricingLoadPriceTableTest(unittest.TestCase):
    """`load_price_table`: the operator-supplied table, validated."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-mprice-")
        self.addCleanup(shutil.rmtree, self.root, True)
        # `_DEFAULT_PRICES` is MODULE state, and a test that mutates it silently
        # rewrites the fixture of every test that runs after it — which is
        # exactly how the no-mutation assertion below went vacuous: an earlier
        # case in this class had already written the same value, so a body that
        # mutated the builtin produced no visible change. Snapshot and restore.
        self._pristine = {k: dict(v)
                          for k, v in pricing._DEFAULT_PRICES.items()}
        self.addCleanup(self._restore_price_table)

    def _restore_price_table(self):
        pricing._DEFAULT_PRICES.clear()
        pricing._DEFAULT_PRICES.update(self._pristine)

    def _table(self, payload, name="prices.json"):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(payload if isinstance(payload, str) else json.dumps(payload))
        return path

    def test_mpricing_an_absent_file_is_an_error_not_a_crash(self):
        table, err = pricing.load_price_table(
            os.path.join(self.root, "nope.json"))
        self.assertIsNone(table)
        self.assertIn("could not read price table", err)

    def test_mpricing_malformed_json_is_an_error(self):
        table, err = pricing.load_price_table(self._table("{ not json"))
        self.assertIsNone(table)
        self.assertIn("could not read price table", err)

    def test_mpricing_a_non_object_table_is_refused(self):
        table, err = pricing.load_price_table(self._table([1, 2, 3]))
        self.assertIsNone(table)
        self.assertIn("non-empty JSON object", err)

    def test_mpricing_an_empty_table_is_refused(self):
        table, err = pricing.load_price_table(self._table({}))
        self.assertIsNone(table)
        self.assertIn("non-empty JSON object", err)

    def test_mpricing_a_non_object_entry_is_refused(self):
        table, err = pricing.load_price_table(self._table({"m": 3.0}))
        self.assertIsNone(table)
        self.assertIn("price entry for", err)

    def test_mpricing_a_partial_table_merges_over_the_defaults(self):
        table, err = pricing.load_price_table(
            self._table({"claude-opus-4": {"input": 1.0}}))
        self.assertIsNone(err)
        self.assertEqual(table["claude-opus-4"], {"input": 1.0})
        self.assertIn("default", table)
        self.assertEqual(table["default"], pricing._DEFAULT_PRICES["default"])

    def test_mpricing_a_table_naming_default_replaces_the_builtin_set(self):
        payload = {"default": {"input": 1.0, "output": 2.0}}
        table, err = pricing.load_price_table(self._table(payload))
        self.assertIsNone(err)
        self.assertEqual(table, payload)
        self.assertNotIn("claude-opus-4", table)

    def test_mpricing_loading_never_mutates_the_builtin_table(self):
        # The overriding VALUE is one nothing else in this suite writes, so the
        # assertion cannot be satisfied by a mutation an earlier test already
        # made, and the entry is compared against the SNAPSHOT rather than
        # against live module state.
        before = json.dumps(self._pristine, sort_keys=True)
        pricing.load_price_table(
            self._table({"claude-opus-4": {"input": 424242.0}}))
        self.assertEqual(json.dumps(pricing._DEFAULT_PRICES, sort_keys=True),
                         before)
        self.assertNotEqual(
            pricing._DEFAULT_PRICES["claude-opus-4"]["input"], 424242.0)


class PricingContextWindowTest(unittest.TestCase):
    """`_model_context_window` / `_context_window_for_rows`: the sizing half."""

    def test_mpricing_an_unknown_model_gets_the_default_window(self):
        self.assertEqual(pricing._model_context_window("some-vendor-model"),
                         pricing._DEFAULT_CONTEXT_WINDOW)

    def test_mpricing_a_claude_4_family_model_gets_the_1m_window(self):
        for model in ("claude-opus-4", "claude-sonnet-4-20250514",
                      "claude-haiku-9"):
            self.assertEqual(pricing._model_context_window(model),
                             pricing._CONTEXT_WINDOW_1M, model)

    def test_mpricing_an_explicit_1m_marker_gets_the_1m_window(self):
        for model in ("some-model[1m]", "vendor-x-1m"):
            self.assertEqual(pricing._model_context_window(model),
                             pricing._CONTEXT_WINDOW_1M, model)

    def test_mpricing_the_lookup_is_case_insensitive(self):
        self.assertEqual(pricing._model_context_window("CLAUDE-OPUS-4"),
                         pricing._CONTEXT_WINDOW_1M)

    def test_mpricing_none_gets_the_default_window(self):
        self.assertEqual(pricing._model_context_window(None),
                         pricing._DEFAULT_CONTEXT_WINDOW)

    def test_mpricing_rows_are_measured_against_the_largest_window_present(self):
        rows = [_row(models={"some-vendor-model": _usage()}),
                _row(models={"claude-opus-4": _usage()})]
        self.assertEqual(pricing._context_window_for_rows(rows),
                         pricing._CONTEXT_WINDOW_1M)

    def test_mpricing_no_rows_means_the_default_window(self):
        self.assertEqual(pricing._context_window_for_rows([]),
                         pricing._DEFAULT_CONTEXT_WINDOW)

    def test_mpricing_a_row_with_no_models_key_does_not_raise(self):
        self.assertEqual(pricing._context_window_for_rows([{}, {"models": None}]),
                         pricing._DEFAULT_CONTEXT_WINDOW)


class PricingExportsTest(unittest.TestCase):
    """The module's published surface."""

    def test_mpricing_every_exported_name_exists(self):
        for name in pricing._EXPORTS:
            self.assertTrue(hasattr(pricing, name),
                            "_EXPORTS names %r, which the module does not define"
                            % name)

    def test_mpricing_every_usage_key_maps_to_a_price_component(self):
        from agentware_lib.ops.sessionparse import _USAGE_KEYS
        self.assertEqual(sorted(pricing._PRICE_COMPONENT), sorted(_USAGE_KEYS))

    def test_mpricing_every_default_entry_prices_every_component(self):
        for model, entry in pricing._DEFAULT_PRICES.items():
            for comp in pricing._PRICE_COMPONENT.values():
                self.assertIn(comp, entry, "%s has no %s price" % (model, comp))


if __name__ == "__main__":
    unittest.main()
