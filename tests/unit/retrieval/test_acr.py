"""Tests for the ACR prior (Phase 1.1): source_weight, freshness, acr, as_of.

Stdlib `unittest` only. ACR is a pure, deterministic, bounded function of
`(entry, as_of)` — no LLM/RNG/network, no hidden clock. These tests pin
`as_of` explicitly so they are reproducible, and check the hand-computed values
plus the gentleness invariants (floor, ordering, determinism).
"""

import datetime
import os

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # allow `python3 -m unittest tests.unit.retrieval.test_acr`
    from _fixtures import SyntheticKBTestCase, load_cli


def _date_minus(as_of, days):
    """Return the YYYY-MM-DD string `days` before `as_of` (stdlib helper)."""
    d = datetime.date.fromisoformat(as_of) - datetime.timedelta(days=days)
    return d.isoformat()


class SourceWeightTest(SyntheticKBTestCase):
    def test_known_sources(self):
        m = load_cli()
        self.assertAlmostEqual(m.source_weight("user"), 1.0)
        self.assertAlmostEqual(m.source_weight("agent"), 0.9)
        self.assertAlmostEqual(m.source_weight("imported"), 0.8)

    def test_strict_ordering_user_gt_agent_gt_imported(self):
        m = load_cli()
        self.assertGreater(m.source_weight("user"), m.source_weight("agent"))
        self.assertGreater(m.source_weight("agent"), m.source_weight("imported"))

    def test_unknown_and_missing_fall_back_to_default(self):
        m = load_cli()
        self.assertAlmostEqual(m.source_weight(None), m.ACR_DEFAULT_SOURCE_WEIGHT)
        self.assertAlmostEqual(m.source_weight(""), m.ACR_DEFAULT_SOURCE_WEIGHT)
        self.assertAlmostEqual(m.source_weight("bogus"), m.ACR_DEFAULT_SOURCE_WEIGHT)

    def test_case_and_whitespace_insensitive(self):
        m = load_cli()
        self.assertAlmostEqual(m.source_weight("  USER "), 1.0)


class FreshnessTest(SyntheticKBTestCase):
    AS_OF = "2026-06-25"

    def test_zero_age_is_one(self):
        m = load_cli()
        self.assertAlmostEqual(m.freshness(self.AS_OF, self.AS_OF), 1.0)

    def test_intermediate_strictly_between_floor_and_one(self):
        m = load_cli()
        # age 9d: raw decay 0.5**(9/90) = 2**-0.1 ≈ 0.9330 (above floor 0.85).
        lv = _date_minus(self.AS_OF, 9)
        val = m.freshness(lv, self.AS_OF)
        self.assertAlmostEqual(val, 0.5 ** (9 / 90.0))
        self.assertGreater(val, m.ACR_FRESHNESS_FLOOR)
        self.assertLess(val, 1.0)

    def test_one_half_life_decay_is_clamped_to_floor(self):
        m = load_cli()
        # FLOOR=0.85 clamps long before one half-life: raw decay at 90d is 0.5,
        # below floor, so freshness is exactly the floor (gentle by design).
        lv = _date_minus(self.AS_OF, m.ACR_HALF_LIFE_DAYS)
        self.assertAlmostEqual(m.freshness(lv, self.AS_OF), m.ACR_FRESHNESS_FLOOR)

    def test_two_half_lives_hits_floor_not_quarter(self):
        m = load_cli()
        lv = _date_minus(self.AS_OF, 2 * m.ACR_HALF_LIFE_DAYS)  # decay→0.25
        # 0.25 < FLOOR(0.85) so the floor wins (gentle: old facts not buried).
        self.assertAlmostEqual(m.freshness(lv, self.AS_OF), m.ACR_FRESHNESS_FLOOR)

    def test_monotonic_non_increasing_with_age(self):
        m = load_cli()
        prev = 1.0
        for age in (0, 3, 9, 15, 30, 90, 365):
            v = m.freshness(_date_minus(self.AS_OF, age), self.AS_OF)
            self.assertLessEqual(v, prev + 1e-12)
            prev = v

    def test_never_below_floor_even_for_ancient(self):
        m = load_cli()
        lv = _date_minus(self.AS_OF, 10000)
        self.assertGreaterEqual(m.freshness(lv, self.AS_OF), m.ACR_FRESHNESS_FLOOR)
        self.assertAlmostEqual(m.freshness(lv, self.AS_OF), m.ACR_FRESHNESS_FLOOR)

    def test_future_last_verified_clamps_to_one(self):
        m = load_cli()
        lv = _date_minus(self.AS_OF, -30)  # 30 days in the future
        self.assertAlmostEqual(m.freshness(lv, self.AS_OF), 1.0)

    def test_invalid_or_missing_last_verified_returns_floor(self):
        m = load_cli()
        self.assertAlmostEqual(m.freshness(None, self.AS_OF), m.ACR_FRESHNESS_FLOOR)
        self.assertAlmostEqual(m.freshness("not-a-date", self.AS_OF),
                               m.ACR_FRESHNESS_FLOOR)

    def test_invalid_as_of_raises(self):
        m = load_cli()
        with self.assertRaises(ValueError):
            m.freshness(self.AS_OF, "2026/06/25")


class AcrTest(SyntheticKBTestCase):
    AS_OF = "2026-06-25"

    def test_hand_computed_user_floored_at_one_half_life(self):
        m = load_cli()
        lv = _date_minus(self.AS_OF, m.ACR_HALF_LIFE_DAYS)
        entry = {"id": "e1", "source": "user", "last_verified": lv}
        # acr = source_weight(user=1.0) * freshness(floored to 0.85) = 0.85
        self.assertAlmostEqual(m.acr(entry, self.AS_OF), 1.0 * m.ACR_FRESHNESS_FLOOR)

    def test_hand_computed_imported_fresh(self):
        m = load_cli()
        entry = {"id": "e2", "source": "imported", "last_verified": self.AS_OF}
        # acr = 0.8 * 1.0
        self.assertAlmostEqual(m.acr(entry, self.AS_OF), 0.8)

    def test_falls_back_to_created_when_no_last_verified(self):
        m = load_cli()
        lv = _date_minus(self.AS_OF, m.ACR_HALF_LIFE_DAYS)
        entry = {"id": "e3", "source": "agent", "created": lv}
        # entry_last_verified() falls back to created → freshness floored 0.85;
        # agent weight 0.9 → 0.9 * 0.85 = 0.765
        self.assertAlmostEqual(m.acr(entry, self.AS_OF), 0.9 * m.ACR_FRESHNESS_FLOOR)

    def test_provenance_ordering_at_same_freshness(self):
        m = load_cli()
        base = {"last_verified": self.AS_OF}
        user = m.acr(dict(base, source="user"), self.AS_OF)
        agent = m.acr(dict(base, source="agent"), self.AS_OF)
        imported = m.acr(dict(base, source="imported"), self.AS_OF)
        self.assertGreater(user, agent)
        self.assertGreater(agent, imported)

    def test_deterministic_two_calls_identical(self):
        m = load_cli()
        entry = {"id": "e4", "source": "user", "last_verified": "2026-03-01"}
        self.assertEqual(m.acr(entry, self.AS_OF), m.acr(entry, self.AS_OF))

    def test_bounded_within_floor_times_min_weight_and_one(self):
        m = load_cli()
        lo = m.ACR_FRESHNESS_FLOOR * min(m.ACR_SOURCE_WEIGHTS.values())
        for src in ("user", "agent", "imported", None):
            for age in (0, 45, 90, 365, 10000):
                lv = _date_minus(self.AS_OF, age)
                v = m.acr({"source": src, "last_verified": lv}, self.AS_OF)
                self.assertGreaterEqual(v, lo)
                self.assertLessEqual(v, 1.0)

    def test_does_not_mutate_entry(self):
        m = load_cli()
        entry = {"id": "e5", "source": "user", "last_verified": "2026-03-01"}
        snapshot = dict(entry)
        m.acr(entry, self.AS_OF)
        self.assertEqual(entry, snapshot)


class AsOfPlumbingTest(SyntheticKBTestCase):
    """`--as-of` threads through recall/eval and is surfaced/validated."""

    def test_recall_json_surfaces_as_of(self):
        import json
        code, out, err = self.run_cli([
            "recall", "bm25 ranking", "--as-of", "2026-06-25", "--format", "json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["as_of"], "2026-06-25")

    def test_recall_rejects_bad_as_of(self):
        code, out, err = self.run_cli([
            "recall", "anything", "--as-of", "2026/06/25"])
        self.assertEqual(code, 1)
        self.assertIn("--as-of", err)

    def test_eval_json_surfaces_as_of(self):
        import json
        self._write_gold()
        code, out, err = self.run_cli([
            "eval", "--strategy", "bm25", "--gold", self.gold_path,
            "--as-of", "2026-06-25", "--format", "json"])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["as_of"], "2026-06-25")

    def test_eval_rejects_bad_as_of(self):
        self._write_gold()
        code, out, err = self.run_cli([
            "eval", "--strategy", "bm25", "--gold", self.gold_path,
            "--as-of", "garbage"])
        self.assertEqual(code, 1)
        self.assertIn("--as-of", err)

    def _write_gold(self):
        import json
        self.gold_path = os.path.join(self.kdir, "gold.json")
        with open(self.gold_path, "w", encoding="utf-8") as f:
            json.dump([{"query": "bm25 ranking",
                        "expected_ids": ["ref-bm25-ranking"]}], f)


if __name__ == "__main__":
    import unittest
    unittest.main()
