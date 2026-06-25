"""Tests for entry frontmatter: emit/parse round-trip, defaults, legacy tolerance.

Stdlib unittest only (discovered by `audit --with-tests`). Reuses the synthetic
KB fixtures so nothing touches the operator's real knowledge base.
"""

import json
import os

from _fixtures import SyntheticKBTestCase, load_cli


class FrontmatterRoundTripTests(SyntheticKBTestCase):
    def test_emit_parse_lossless_all_fields(self):
        mod = load_cli()
        fields = {
            "id": "learn-On failure, re-query: by signature",
            "title": "A Title: with a colon, comma & #hash",
            "category": "learnings",
            "tags": ["alpha", "beta-gamma", "needs space", "with,comma"],
            "created": "2026-06-25",
            "summary": "Summary with: colon, comma, # and \"quotes\" inside.",
            "author": "testhandle",
            "source": "agent",
            "last_verified": "2026-06-25",
        }
        block = mod.render_frontmatter(fields)
        parsed, body = mod.split_frontmatter(block)
        self.assertEqual(body, "")
        for k in mod.FRONTMATTER_FIELDS:
            self.assertEqual(parsed[k], fields[k], "field %s did not round-trip" % k)

    def test_empty_tags_round_trip(self):
        mod = load_cli()
        fields = {"id": "x", "title": "X", "category": "references", "tags": [],
                  "created": "2026-01-01", "summary": "s", "author": "testhandle",
                  "source": "agent", "last_verified": "2026-01-01"}
        parsed, _ = mod.split_frontmatter(mod.render_frontmatter(fields))
        self.assertEqual(parsed["tags"], [])

    def test_strip_frontmatter_is_lossless_tail(self):
        mod = load_cli()
        body = "# Heading\n\n> **Created**: 2026-01-01\n\nSome body.\n\n---\n\nfooter\n"
        fields = {"id": "x", "title": "Heading", "category": "learnings",
                  "tags": ["a"], "created": "2026-01-01", "summary": "s",
                  "author": "testhandle", "source": "agent", "last_verified": "2026-01-01"}
        full = mod.render_frontmatter(fields) + body
        self.assertEqual(mod.strip_frontmatter(full), body)

    def test_split_ignores_horizontal_rules_in_legacy_body(self):
        mod = load_cli()
        legacy = "# Title\n\n> **Created**: 2026-01-01\n\n---\n\nbody\n"
        fields, body = mod.split_frontmatter(legacy)
        self.assertIsNone(fields)
        self.assertEqual(body, legacy)


class FrontmatterDefaultsTests(SyntheticKBTestCase):
    def setUp(self):
        super().setUp()
        # Provide a MAIN.md so the author default resolves to the operator handle.
        with open(os.path.join(self.kdir, "MAIN.md"), "w", encoding="utf-8") as f:
            f.write("# KB\n\n- **Handle**: testhandle\n")

    def test_learn_writes_nine_field_frontmatter(self):
        code, out, err = self.run_cli(
            ["learn", "--topic", "widget-quirk", "--summary", "A widget quirk.",
             "--tags", "widgets,quirks", "--content", "Body about widgets.",
             "--format", "json"])
        self.assertEqual(code, 0, err)
        path = os.path.join(self.kdir, "learnings", "widget-quirk.md")
        mod = load_cli()
        fm = mod.read_entry_frontmatter(path)
        for k in mod.FRONTMATTER_FIELDS:
            self.assertIn(k, fm, "missing frontmatter field %s" % k)
        self.assertEqual(fm["id"], "learn-widget-quirk")
        self.assertEqual(fm["category"], "learnings")
        self.assertEqual(fm["tags"], ["widgets", "quirks"])
        self.assertEqual(fm["summary"], "A widget quirk.")
        self.assertEqual(fm["author"], "testhandle")
        self.assertEqual(fm["source"], "agent")
        # Defaults: last_verified == created.
        self.assertEqual(fm["last_verified"], fm["created"])

    def test_index_validate_passes_after_learn(self):
        code, _, err = self.run_cli(
            ["learn", "--topic", "another-thing", "--summary", "Another.",
             "--tags", "misc", "--content", "Body."])
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli(["index", "validate"])
        self.assertEqual(code, 0, err)


class IndexAddBackfillTests(SyntheticKBTestCase):
    def test_index_add_backfills_and_roundtrips_to_index_row(self):
        # Create a pre-existing frontmatter-less file, then `index add` it.
        rel = "references/manual-note.md"
        abs_path = os.path.join(self.kdir, rel)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write("# Manual Note\n\nHand-written reference body.\n")
        code, out, err = self.run_cli(
            ["index", "add", "--id", "ref-manual-note", "--title", "Manual Note",
             "--category", "references", "--path", rel, "--tags", "manual,note",
             "--summary", "A manual note.", "--created", "2026-02-02",
             "--format", "json"])
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["frontmatter_backfilled"])
        mod = load_cli()
        fm = mod.read_entry_frontmatter(abs_path)
        row = json.loads(out)["added"]
        # Parsed frontmatter matches the index row it produced.
        self.assertEqual(fm["id"], row["id"])
        self.assertEqual(fm["title"], row["title"])
        self.assertEqual(fm["category"], row["category"])
        self.assertEqual(fm["tags"], row["tags"])
        self.assertEqual(fm["created"], row["created"])
        self.assertEqual(fm["summary"], row["summary"])
        # Body is untouched below the prepended block.
        with open(abs_path, encoding="utf-8") as f:
            self.assertIn("Hand-written reference body.", f.read())

    def test_index_add_idempotent_when_frontmatter_present(self):
        rel = "references/already.md"
        abs_path = os.path.join(self.kdir, rel)
        mod = load_cli()
        fm = {"id": "ref-already", "title": "Already", "category": "references",
              "tags": ["x"], "created": "2026-03-03", "summary": "Has fm.",
              "author": "testhandle", "source": "agent", "last_verified": "2026-03-03"}
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(mod.render_frontmatter(fm) + "# Already\n\nbody\n")
        with open(abs_path, encoding="utf-8") as f:
            before = f.read()
        code, out, err = self.run_cli(
            ["index", "add", "--id", "ref-already", "--title", "Already",
             "--category", "references", "--path", rel, "--tags", "x",
             "--summary", "Has fm.", "--format", "json"])
        self.assertEqual(code, 0, err)
        self.assertFalse(json.loads(out)["frontmatter_backfilled"])
        with open(abs_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)


class LegacyToleranceTests(SyntheticKBTestCase):
    def test_parser_tolerates_legacy_blockquote_file(self):
        rel = "learnings/legacy-style.md"
        abs_path = os.path.join(self.kdir, rel)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write("# Legacy Style\n\n> **Created**: 2026-04-04  \n"
                    "> **Tags**: `foo`, `bar`  \n> **Category**: learnings\n\n"
                    "---\n\nbody text\n")
        mod = load_cli()
        fm = mod.read_entry_frontmatter(abs_path)
        self.assertEqual(fm.get("title"), "Legacy Style")
        self.assertEqual(fm.get("created"), "2026-04-04")
        self.assertEqual(fm.get("category"), "learnings")
        self.assertEqual(fm.get("tags"), ["foo", "bar"])


if __name__ == "__main__":
    import unittest
    unittest.main()
