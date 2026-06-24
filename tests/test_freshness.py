"""Tests for the `last_verified` freshness metadata (Phase 2.1).

Stdlib `unittest` only, driven against a synthetic temp KB. Asserts:
  - `index add --last-verified` round-trips the field into index.json.
  - `index validate` passes on BOTH legacy entries (field absent) and new
    entries (field present) — back-compat is preserved.
  - An invalid `last_verified` is rejected by both `index add` and `validate`.
  - `entry_last_verified()` falls back to `created` when the field is absent.
"""

import os

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # allow `python3 -m unittest tests.test_freshness`
    from _fixtures import SyntheticKBTestCase, load_cli


class LastVerifiedTest(SyntheticKBTestCase):
    def _write_entry_file(self, rel):
        abs_ = os.path.join(self.kdir, rel)
        os.makedirs(os.path.dirname(abs_), exist_ok=True)
        with open(abs_, "w", encoding="utf-8") as f:
            f.write("# new entry\n\nfresh body about caching layers.\n")

    def test_add_with_last_verified_roundtrips(self):
        self._write_entry_file("learnings/fresh.md")
        code, out, err = self.run_cli([
            "index", "add", "--id", "learn-fresh", "--title", "Fresh Entry",
            "--category", "learnings", "--path", "learnings/fresh.md",
            "--tags", "fresh,cache", "--summary", "a fresh learning",
            "--created", "2026-02-01", "--last-verified", "2026-06-20",
        ])
        self.assertEqual(code, 0, err)
        data = self.read_index()
        entry = next(e for e in data["entries"] if e["id"] == "learn-fresh")
        self.assertEqual(entry["last_verified"], "2026-06-20")
        self.assertEqual(entry["created"], "2026-02-01")
        # index validate accepts the new field.
        code, out, err = self.run_cli(["index", "validate"])
        self.assertEqual(code, 0, out + err)

    def test_add_without_last_verified_omits_field(self):
        self._write_entry_file("learnings/plain.md")
        code, out, err = self.run_cli([
            "index", "add", "--id", "learn-plain", "--title", "Plain Entry",
            "--category", "learnings", "--path", "learnings/plain.md",
            "--tags", "plain", "--summary", "a plain learning",
            "--created", "2026-02-02",
        ])
        self.assertEqual(code, 0, err)
        data = self.read_index()
        entry = next(e for e in data["entries"] if e["id"] == "learn-plain")
        # Field is omitted for back-compat byte-stability.
        self.assertNotIn("last_verified", entry)

    def test_validate_passes_on_legacy_entries(self):
        # The synthetic KB ships entries WITHOUT last_verified (legacy shape).
        code, out, err = self.run_cli(["index", "validate"])
        self.assertEqual(code, 0, out + err)
        for e in self.index_data["entries"]:
            self.assertNotIn("last_verified", e)

    def test_add_rejects_malformed_last_verified(self):
        self._write_entry_file("learnings/bad.md")
        code, out, err = self.run_cli([
            "index", "add", "--id", "learn-bad", "--title", "Bad Date",
            "--category", "learnings", "--path", "learnings/bad.md",
            "--tags", "bad", "--summary", "bad date learning",
            "--last-verified", "2026/06/20",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("last_verified", err)
        # Nothing was added.
        data = self.read_index()
        self.assertFalse(any(e["id"] == "learn-bad" for e in data["entries"]))

    def test_validate_flags_malformed_last_verified(self):
        import json
        path = os.path.join(self.kdir, "index.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["entries"][0]["last_verified"] = "not-a-date"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        code, out, err = self.run_cli(["index", "validate"])
        self.assertNotEqual(code, 0)
        self.assertIn("last_verified", out + err)

    def test_entry_last_verified_fallback(self):
        mod = load_cli()
        self.assertEqual(
            mod.entry_last_verified({"created": "2026-01-01"}), "2026-01-01")
        self.assertEqual(
            mod.entry_last_verified(
                {"created": "2026-01-01", "last_verified": "2026-05-05"}),
            "2026-05-05")
        # Empty string falls back to created.
        self.assertEqual(
            mod.entry_last_verified(
                {"created": "2026-01-01", "last_verified": ""}), "2026-01-01")


if __name__ == "__main__":
    import unittest
    unittest.main()
