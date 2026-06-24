"""Tests for `audit --stale` + conflict flagging (Phase 2.2).

Stdlib `unittest` only, driven against a synthetic temp KB. Asserts:
  - A volatile-category entry whose last_verified is older than the window is
    flagged stale; a fresh entry is NOT.
  - Two near-identical learnings are surfaced as a possible duplicate/conflict
    pair (token-set Jaccard >= 0.6); unrelated entries are not.
  - Staleness/conflict reporting is ADVISORY: it never mutates index.json and
    does not flip the audit exit code on its own.
  - The stale/conflict helpers are deterministic and read-only.
"""

import json
import os

try:
    from tests._fixtures import (SyntheticKBTestCase, build_synthetic_kb,
                                 load_cli, run_cli)
except ImportError:  # allow `python3 -m unittest tests.test_staleness`
    from _fixtures import (SyntheticKBTestCase, build_synthetic_kb,
                           load_cli, run_cli)


# A near-identical pair of learnings (high token-set overlap → Jaccard >= 0.6),
# one stale (very old last_verified) + one fresh, plus an unrelated learning.
_NEAR_DUP_BODY = (
    "# Cache Invalidation Race\n\n"
    "A stale cache entry was served because the cache invalidation hook fired "
    "before the database write committed; reorder the write to commit before "
    "invalidating the cache layer to avoid serving a stale value.\n"
)
_NEAR_DUP_BODY_2 = (
    "# Cache Invalidation Race Condition\n\n"
    "A stale cache entry was served because the cache invalidation hook fired "
    "before the database write committed; reorder the write to commit before "
    "invalidating the cache layer to avoid serving a stale value here too.\n"
)


def _entries(stale_date, fresh_date):
    return [
        {
            "id": "learn-cache-race-a",
            "title": "Cache Invalidation Race",
            "category": "learnings",
            "path": "learnings/cache-race-a.md",
            "tags": ["cache", "race"],
            "created": stale_date,
            "last_verified": stale_date,
            "summary": "Stale cache served due to invalidation ordering.",
            "body": _NEAR_DUP_BODY,
        },
        {
            "id": "learn-cache-race-b",
            "title": "Cache Invalidation Race Condition",
            "category": "learnings",
            "path": "learnings/cache-race-b.md",
            "tags": ["cache", "race"],
            "created": fresh_date,
            "last_verified": fresh_date,
            "summary": "Stale cache served due to invalidation ordering (dup).",
            "body": _NEAR_DUP_BODY_2,
        },
        {
            "id": "learn-unrelated",
            "title": "Geofence Reminders",
            "category": "learnings",
            "path": "learnings/unrelated.md",
            "tags": ["geofence", "ios"],
            "created": fresh_date,
            "last_verified": fresh_date,
            "summary": "Arrival geofence reminders never fired on iOS.",
            "body": (
                "# Geofence Reminders\n\nArrival geofence reminders never fired "
                "because defineTask was nested and the background location task "
                "was never registered at startup on iOS.\n"
            ),
        },
    ]


class StaleAndConflictTest(SyntheticKBTestCase):
    # Old enough to always exceed any reasonable window regardless of run date.
    STALE_DATE = "2000-01-01"

    def setUp(self):
        super().setUp()
        # Rebuild the KB with our purpose-built entries (stale + near-dup pair).
        self.fresh_date = self._mod_today()
        self.index_data = build_synthetic_kb(
            self.kdir, entries=_entries(self.STALE_DATE, self.fresh_date))

    def _mod_today(self):
        return load_cli()._today()

    # --- helper-level (deterministic, read-only) ----------------------------
    def test_stale_entries_flags_old_not_fresh(self):
        mod = load_cli()
        data = self.read_index()
        stale = mod.stale_entries(data, 120)
        ids = [e.get("id") for (e, _age) in stale]
        self.assertIn("learn-cache-race-a", ids)       # last_verified 2000 → stale
        self.assertNotIn("learn-cache-race-b", ids)    # last_verified today → fresh
        self.assertNotIn("learn-unrelated", ids)

    def test_stale_respects_window(self):
        mod = load_cli()
        data = self.read_index()
        # An enormous window makes even the 2000-dated entry not stale.
        self.assertEqual(mod.stale_entries(data, 10**9), [])

    def test_stale_skips_non_volatile_categories(self):
        mod = load_cli()
        # A references entry (non-volatile) with an ancient date is NOT flagged.
        entries = _entries(self.STALE_DATE, self.fresh_date) + [{
            "id": "ref-old", "title": "Old Reference", "category": "references",
            "path": "references/old.md", "tags": ["ref"],
            "created": self.STALE_DATE, "last_verified": self.STALE_DATE,
            "summary": "ancient reference", "body": "# Old\n\nancient ref.\n",
        }]
        kdir2 = self.kdir + "-2"
        os.makedirs(kdir2, exist_ok=True)
        self.addCleanup(__import__("shutil").rmtree, kdir2, True)
        data = build_synthetic_kb(kdir2, entries=entries)
        ids = [e.get("id") for (e, _a) in mod.stale_entries(data, 120)]
        self.assertNotIn("ref-old", ids)
        self.assertIn("learn-cache-race-a", ids)

    def test_conflict_pairs_flags_near_dup_not_unrelated(self):
        mod = load_cli()
        data = self.read_index()
        pairs = mod.conflict_pairs(self.kdir, data)
        flagged = {tuple(sorted((a, b))) for (a, b, _j) in
                   [(p[0].get("id"), p[1].get("id"), p[2]) for p in pairs]}
        self.assertIn(
            ("learn-cache-race-a", "learn-cache-race-b"), flagged)
        # The unrelated learning is never paired with the cache entries.
        for a, b in flagged:
            self.assertNotIn("learn-unrelated", (a, b))

    def test_conflict_pairs_deterministic(self):
        mod = load_cli()
        data = self.read_index()
        p1 = mod.conflict_pairs(self.kdir, data)
        p2 = mod.conflict_pairs(self.kdir, data)
        key = lambda ps: [(a.get("id"), b.get("id"), j) for (a, b, j) in ps]
        self.assertEqual(key(p1), key(p2))

    # --- CLI-level ----------------------------------------------------------
    def test_audit_stale_json_reports_and_is_read_only(self):
        idx = os.path.join(self.kdir, "index.json")
        with open(idx, "rb") as f:
            before = f.read()
        code, out, err = self.run_cli(["audit", "--stale", "--format", "json"])
        with open(idx, "rb") as f:
            after = f.read()
        self.assertEqual(before, after, "audit --stale must not mutate index.json")
        payload = json.loads(out)
        self.assertIn("stale_report", payload)
        rep = payload["stale_report"]
        self.assertEqual(rep["max_age_days"], 120)
        stale_ids = {s["id"] for s in rep["stale"]}
        self.assertIn("learn-cache-race-a", stale_ids)
        self.assertNotIn("learn-cache-race-b", stale_ids)
        conflict_keys = {tuple(sorted((c["a"], c["b"]))) for c in rep["conflicts"]}
        self.assertIn(("learn-cache-race-a", "learn-cache-race-b"), conflict_keys)

    def test_audit_stale_text_output(self):
        code, out, err = self.run_cli(["audit", "--stale"])
        self.assertIn("STALE REPORT", out)
        self.assertIn("learn-cache-race-a", out)

    def test_audit_without_stale_has_no_report(self):
        code, out, err = self.run_cli(["audit", "--format", "json"])
        payload = json.loads(out)
        self.assertNotIn("stale_report", payload)

    def test_max_age_days_flag(self):
        code, out, err = self.run_cli(
            ["audit", "--stale", "--max-age-days", "1000000000", "--format", "json"])
        rep = json.loads(out)["stale_report"]
        self.assertEqual(rep["max_age_days"], 1000000000)
        self.assertEqual(rep["stale"], [])  # nothing is that old


if __name__ == "__main__":
    import unittest
    unittest.main()
