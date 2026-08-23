"""Tests for entry frontmatter: emit/parse round-trip, defaults, legacy tolerance.

Stdlib unittest only (discovered by `audit --with-tests`). Reuses the synthetic
KB fixtures so nothing touches the operator's real knowledge base.
"""

import json
import os

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # allow `python3 -m unittest tests.unit.core.test_frontmatter`
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
            # `relates` is the second inline-list field; non-empty so it emits a
            # `relates:` line and round-trips like tags (omit-empty is covered by
            # test_empty_relates_omits_line below).
            "relates": ["depends-on:learn-other", "relates-to:ref-x"],
        }
        block = mod.render_frontmatter(fields)
        parsed, body = mod.split_frontmatter(block)
        self.assertEqual(body, "")
        for k in mod.FRONTMATTER_FIELDS:
            self.assertEqual(parsed[k], fields[k], "field %s did not round-trip" % k)

    def test_empty_relates_omits_line(self):
        """A no-edge entry emits NO `relates:` line (backward-compatible:
        absent == today's byte-identical behavior)."""
        mod = load_cli()
        fields = {"id": "x", "title": "X", "category": "references", "tags": [],
                  "created": "2026-01-01", "summary": "s", "author": "testhandle",
                  "source": "agent", "last_verified": "2026-01-01", "relates": []}
        block = mod.render_frontmatter(fields)
        self.assertNotIn("relates", block)
        parsed, _ = mod.split_frontmatter(block)
        self.assertEqual(parsed.get("relates", []), [])

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
        # Set the user handle so resolve_user_handle() returns 'testhandle'
        # regardless of the operator's real ~/.agentware/config.env.
        self._prev_handle = os.environ.get("AGENTWARE_USER_HANDLE")
        os.environ["AGENTWARE_USER_HANDLE"] = "testhandle"
        # Provide a MAIN.md so the author default resolves to the operator handle.
        with open(os.path.join(self.kdir, "MAIN.md"), "w", encoding="utf-8") as f:
            f.write("# KB\n\n- **Handle**: testhandle\n")

    def tearDown(self):
        # Restore the real env so other tests are unaffected.
        if self._prev_handle is None:
            os.environ.pop("AGENTWARE_USER_HANDLE", None)
        else:
            os.environ["AGENTWARE_USER_HANDLE"] = self._prev_handle
        super().tearDown()

    def test_learn_writes_nine_field_frontmatter(self):
        code, out, err = self.run_cli(
            ["learn", "--topic", "widget-quirk", "--summary", "A widget quirk.",
             "--tags", "widgets,quirks", "--content", "Body about widgets.",
             "--format", "json"])
        self.assertEqual(code, 0, err)
        path = os.path.join(self.kdir, "learnings", "widget-quirk.md")
        mod = load_cli()
        fm = mod.read_entry_frontmatter(path)
        # `relates` is optional (omitted when empty); every other field is always
        # emitted. A learn writes no edges, so relates defaults to [] (absent).
        for k in mod.FRONTMATTER_FIELDS:
            if k == "relates":
                self.assertEqual(fm.get("relates", []), [])
                continue
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


class OperatorHandleTests(SyntheticKBTestCase):
    """The two halves of the operator-handle lookup (plan task 89).

    `_operator_handle` answers "what string goes in the `author` field", and it
    must always answer something. `_resolve_operator_handle` answers "who is
    named in this knowledge base", and it must be allowed to answer "nobody" —
    the placeholder is a render-boundary default, not a person, and a caller
    that COUNTS identities has to be able to tell the two apart.
    """

    def _kb(self, main_text=None):
        import shutil
        import tempfile
        kb = tempfile.mkdtemp(prefix="aw_oh_")
        self.addCleanup(shutil.rmtree, kb, True)
        if main_text is not None:
            with open(os.path.join(kb, "MAIN.md"), "w", encoding="utf-8") as f:
                f.write(main_text)
        return kb

    def test_resolver_reads_the_handle_line(self):
        mod = load_cli()
        kb = self._kb("# KB\n\n- **Handle**: ada\n")
        self.assertEqual(mod._resolve_operator_handle(kb), "ada")
        self.assertEqual(mod._operator_handle(kb), "ada")

    def test_resolver_falls_back_to_the_operator_line(self):
        mod = load_cli()
        kb = self._kb("# KB\n\n> **Operator**: ada \u00b7 **Onboarded**: 2026-06-24\n")
        self.assertEqual(mod._resolve_operator_handle(kb), "ada")

    def test_resolver_returns_none_when_nobody_is_named(self):
        mod = load_cli()
        # three ways to be unresolved: no kdir, no MAIN.md, a MAIN.md with no
        # handle line. NONE of them may answer with the placeholder.
        self.assertIsNone(mod._resolve_operator_handle(None))
        self.assertIsNone(mod._resolve_operator_handle(""))
        self.assertIsNone(mod._resolve_operator_handle(self._kb()))
        self.assertIsNone(mod._resolve_operator_handle(self._kb("# KB\n\nno profile\n")))

    def test_author_default_still_applies_the_placeholder(self):
        mod = load_cli()
        # the RENDER boundary is unchanged — a writer never blocks on a missing
        # profile, which is the property the placeholder exists for.
        self.assertEqual(mod._operator_handle(None), "operator")
        self.assertEqual(mod._operator_handle(self._kb()), "operator")
        self.assertEqual(mod._operator_handle(self._kb("# KB\n\nno profile\n")),
                         "operator")

    def test_placeholder_is_indistinguishable_downstream_unless_asked_here(self):
        # the REGRESSION this pair exists for: a KB that literally names the
        # operator 'operator' is a resolved handle and must read as one, while an
        # unresolved KB must not — the two cases are equal through
        # `_operator_handle` and MUST differ through the resolver.
        mod = load_cli()
        named = self._kb("# KB\n\n- **Handle**: operator\n")
        bare = self._kb()
        self.assertEqual(mod._operator_handle(named), mod._operator_handle(bare))
        self.assertEqual(mod._resolve_operator_handle(named), "operator")
        self.assertIsNone(mod._resolve_operator_handle(bare))


class FrontmatterBlockTests(SyntheticKBTestCase):
    """The package's ONE fence finder (task 99 w16).

    It replaced nine hand-written copies, so what it must get right is exactly
    what those nine had to agree on: the three outcomes, the FIRST close, and a
    lossless round trip. Every rewriter in the package rests on this.
    """

    def _fm(self):
        # `_frontmatter_block` is deliberately NOT on the facade surface (it has
        # no monolith counterpart and every caller is in-package), so it is
        # reached through the module rather than through `load_cli()`.
        load_cli()
        import agentware_lib.core.frontmatter as fm
        return fm

    def _split(self, text):
        return self._fm()._frontmatter_block(text)

    def test_the_three_parts_rejoin_to_the_input_byte_for_byte(self):
        for text in ("---\nid: x\n---\n\n# T\n\nbody\n",
                     "---\n---\n",
                     "---\nid: x\n---",
                     "  ---\nid: x\n---   \n\ntail\n",
                     "---\na: 1\n---\n\n---\n\nafter a rule\n"):
            head, block, tail = self._split(text)
            self.assertEqual("\n".join(head + block + tail), text, text)

    def test_no_opening_fence_is_none(self):
        self.assertIsNone(self._split("# Title\n\nbody\n"))
        self.assertIsNone(self._split(""))
        # A fence on the SECOND line is not a leading fence.
        self.assertIsNone(self._split("\n---\nid: x\n---\n"))

    def test_an_unclosed_fence_is_none_too_down_a_different_arm(self):
        self.assertIsNone(self._split("---\nid: x\n\n# T\n"))
        self.assertIsNone(self._split("---\n"))

    def test_touching_fences_are_a_real_empty_block(self):
        head, block, tail = self._split("---\n---\n\n# T\n")
        self.assertEqual(block, [])
        self.assertEqual(head, ["---"])
        self.assertEqual(tail[0], "---")

    def test_the_close_is_the_first_one_so_a_body_rule_stays_in_the_tail(self):
        head, block, tail = self._split("---\na: 1\n---\n\n# T\n\n---\n\nz\n")
        self.assertEqual(block, ["a: 1"])
        self.assertIn("---", tail[1:])
        self.assertEqual(tail[-2], "z")

    def test_an_indented_open_is_accepted_and_returned_verbatim(self):
        head, block, _tail = self._split("  ---\nid: x\n---\n")
        self.assertEqual(head, ["  ---"])
        self.assertEqual(block, ["id: x"])

    def test_a_padded_close_is_accepted_and_returned_verbatim(self):
        _head, block, tail = self._split("---\nid: x\n---   \n\nb\n")
        self.assertEqual(block, ["id: x"])
        self.assertEqual(tail[0], "---   ")

    def test_the_head_is_always_exactly_one_line(self):
        for text in ("---\n---\n", "---\na\nb\n---\n", "  ---\n---\n"):
            head, _b, _t = self._split(text)
            self.assertEqual(len(head), 1, text)

    def test_split_frontmatter_and_the_fence_agree_about_what_is_a_block(self):
        # The two are one decision now; a document either has a block for both
        # or for neither. This is the pin that keeps them from drifting apart
        # again the way the nine copies did.
        fm = self._fm()
        for text in ("# T\n", "---\nid: x\n", "---\n---\n", "---\nid: x\n---\n",
                     "  ---\nid: x\n---\n", "", "\n---\nid: x\n---\n"):
            fields, _body = fm.split_frontmatter(text)
            self.assertEqual(fields is None, self._split(text) is None, text)


class UpsertRelatesLineTests(SyntheticKBTestCase):
    """Owning tests for `_fm_upsert_relates_line` — added at task 99 w16.

    The wave's mutation sweep found this function had NO test in its own
    module's file: two of its three properties were guarded only by
    `tests/unit/knowledge/test_relate.py`, which tests the VERB, and the third
    was guarded nowhere at all. That is precisely the difference a
    `test-ownership` row is about — a suite that reddens when the feature breaks
    is not a suite that reddens when this function alone breaks — and it is why
    the wave adopted the shared fence here rather than leaving a ninth copy.
    """

    def _fm(self):
        load_cli()
        import agentware_lib.core.frontmatter as fm
        return fm

    def _upsert(self, text, tokens):
        return self._fm()._fm_upsert_relates_line(text, tokens)

    def test_a_new_line_is_inserted_as_the_last_field_before_the_close(self):
        out = self._upsert("---\nid: x\ntags: [a]\n---\n\n# T\n",
                           ["depends-on:b"])
        self.assertEqual(out.split("\n")[3], "relates: [depends-on:b]")
        self.assertEqual(out.split("\n")[4], "---")

    def test_an_existing_line_is_replaced_in_place(self):
        # In PLACE: `relates` keeps its position, and the field after it stays
        # after it. Appending instead would reorder the block.
        out = self._upsert(
            "---\nid: x\nrelates: [relates-to:old]\ntags: [a]\n---\n\n# T\n",
            ["depends-on:b", "blocks:c"])
        lines = out.split("\n")
        self.assertEqual(lines[2], "relates: [depends-on:b, blocks:c]")
        self.assertEqual(lines[3], "tags: [a]")
        self.assertEqual(out.count("relates: "), 1)

    def test_the_key_test_is_equality_not_a_prefix(self):
        # UNGUARDED before this test: `relatesx:` is a different field, and a
        # `startswith` test would overwrite it and leave the real `relates`
        # line absent.
        out = self._upsert("---\nid: x\nrelatesx: [relates-to:old]\n---\n\n# T\n",
                           ["depends-on:b"])
        self.assertIn("relatesx: [relates-to:old]", out)
        self.assertIn("relates: [depends-on:b]", out)
        self.assertEqual(out.count("relatesx: "), 1)

    def test_a_key_with_surrounding_space_still_matches(self):
        out = self._upsert("---\n  relates : [relates-to:old]\n---\n", ["blocks:c"])
        self.assertEqual(out.count("relates"), 1)
        self.assertIn("relates: [blocks:c]", out)

    def test_a_document_with_no_fence_is_returned_unchanged(self):
        text = "# T\n\nbody\n"
        self.assertEqual(self._upsert(text, ["depends-on:b"]), text)

    def test_an_unclosed_fence_is_returned_unchanged(self):
        text = "---\nid: x\n\n# T\n"
        self.assertEqual(self._upsert(text, ["depends-on:b"]), text)

    def test_an_empty_token_list_still_writes_an_empty_line(self):
        # The caller decides whether to call; this function does not silently
        # decline to record "no edges".
        out = self._upsert("---\nid: x\n---\n", [])
        self.assertIn("relates: []", out)

    def test_the_body_survives_including_a_horizontal_rule(self):
        out = self._upsert("---\nid: x\n---\n\n# T\n\n---\n\ntail\n",
                           ["depends-on:b"])
        self.assertTrue(out.rstrip().endswith("tail"))
        self.assertIn("\n---\n\ntail", out)

    def test_the_emitted_line_round_trips_through_the_parser(self):
        fm = self._fm()
        out = self._upsert("---\nid: x\n---\n",
                           ["depends-on:b", "relates-to:c"])
        fields, _body = fm.split_frontmatter(out)
        self.assertEqual(fields["relates"], ["depends-on:b", "relates-to:c"])


if __name__ == "__main__":
    import unittest
    unittest.main()
