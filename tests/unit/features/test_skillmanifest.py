"""Owning unit file for `agentware_lib.features.skillmanifest` — task 99 w11.

The module is NEW in this wave, landed whole per R-MOD-11, and it holds the two
ends of the `skill` catalog verbs' text: the SKILL.md frontmatter grammar on the
way in, and the two fixed-format reports on the way out. Both halves were
previously inline in bodies over the branch cap, where the only thing exercising
them was a CLI row driving one shape of each.

What is asserted here is the module's PROMISE, not its shape:

  * THE FENCE HAS THREE OUTCOMES, NOT TWO. `_frontmatter_block` returns None for
    "no frontmatter" (no opening fence, or an opening fence never closed) and []
    for a real but EMPTY block. A reader that conflated them would treat a
    document whose fences touch as unparsable, and a document with a runaway
    fence as empty — opposite errors, both silent.
  * THE SCALAR ENDS ON THE LINE THAT ENDS IT. `_folded_scalar` returns the index
    of the line that TERMINATED the gather, never i+1, so the key sitting on
    that line is not swallowed. A blank line INSIDE the scalar does not end it;
    a tab-indented body reads like a space-indented one.
  * `_folded_scalar` DOES NOT DECIDE WHETHER TO GATHER. The key test lives at
    the call site, because it is keyed on the KEY (`name: >` is the literal
    ">"), and a helper that looked total while owning that decision is exactly
    how `name` would silently start parsing as a folded scalar.
  * THE VALIDATE RENDERER PRINTS `count` FROM THE ROWS. Both arms of
    `skill validate` share one document; the `--source` arm's `"count": 1` is
    `len(results)` on a one-row list, not a literal.
  * THE ADD RENDERER DOES NOT BUILD ITS OWN PAYLOAD. The two destinations emit
    DIFFERENT documents in a fixed key ORDER — harness-path carries
    `indexed: false` and no `id`/`path`, the KB install carries both — so the
    payload arrives already built. Re-deriving it here is how the two documents
    would converge; this file asserts the renderer emits exactly what it was
    handed, key order included.

Stdlib-only, hermetic: no temp KB, no network — every input is a literal.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import json
import os
import sys
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.features.skillmanifest as skillmanifest  # noqa: E402


@contextlib.contextmanager
def _captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def _block(text):
    return skillmanifest._frontmatter_block(text)


class FrontmatterBlockTest(unittest.TestCase):
    """The fence: three outcomes, and the two absent ones are distinguishable."""

    def test_a_well_formed_fence_yields_the_lines_between(self):
        self.assertEqual(_block("---\na: 1\nb: 2\n---\nbody\n"),
                         ["a: 1", "b: 2"])

    def test_touching_fences_yield_an_EMPTY_block_not_None(self):
        # The discriminating pair: [] is a real, empty frontmatter; None is
        # "there is none". A reader that returned None here would treat a
        # deliberately-empty block as a parse failure.
        self.assertEqual(_block("---\n---\n"), [])
        self.assertIsNotNone(_block("---\n---\n"))

    def test_no_opening_fence_is_None(self):
        self.assertIsNone(_block("# heading\nno fence\n"))
        self.assertIsNone(_block(""))

    def test_a_leading_blank_line_means_no_opening_fence(self):
        # The fence must be the FIRST line. A document that opens with a blank
        # line is not frontmatter, and accepting it would let any markdown file
        # with a horizontal rule parse as a manifest.
        self.assertIsNone(_block("\n---\nname: a\n---\n"))

    def test_an_unterminated_fence_is_None_not_the_whole_file(self):
        # The dangerous direction: returning `lines[1:]` here would parse the
        # ENTIRE document body as frontmatter keys.
        self.assertIsNone(_block("---\nname: a\ndescription: d\n"))

    def test_the_first_closing_fence_wins(self):
        self.assertEqual(_block("---\na: 1\n---\nb: 2\n---\n"), ["a: 1"])

    def test_a_fence_with_surrounding_whitespace_still_closes(self):
        self.assertEqual(_block("---\na: 1\n  ---  \nb\n"), ["a: 1"])

    def test_crlf_lines_are_stripped_when_matching_the_fence(self):
        self.assertEqual(_block("---\r\na: 1\r\n---\r\n"), ["a: 1\r"])


class FoldedScalarTest(unittest.TestCase):
    """The gather: what continues it, what ends it, and where it resumes."""

    def test_indented_lines_are_joined_with_single_spaces(self):
        val, nxt = skillmanifest._folded_scalar(
            ["description: >", "  one two", "  three", "name: b"], 0)
        self.assertEqual(val, "one two three")
        self.assertEqual(nxt, 3)

    def test_next_index_is_the_line_that_ENDED_the_scalar(self):
        # Returning i+1 (or nxt+1) would swallow `name: b`, which is the only
        # observable difference between the two and produces a manifest with no
        # name rather than a parse error.
        block = ["description: >", "  gathered", "name: b"]
        _val, nxt = skillmanifest._folded_scalar(block, 0)
        self.assertEqual(block[nxt], "name: b")

    def test_a_blank_line_inside_the_scalar_does_not_end_it(self):
        val, nxt = skillmanifest._folded_scalar(
            ["d: >", "  one", "", "  two", "k: v"], 0)
        self.assertEqual(val, "one two")
        self.assertEqual(nxt, 4)

    def test_a_tab_indented_body_reads_like_a_space_indented_one(self):
        val, _n = skillmanifest._folded_scalar(["d: >", "\tone", "\ttwo"], 0)
        self.assertEqual(val, "one two")

    def test_an_unindented_line_ends_the_scalar_immediately(self):
        val, nxt = skillmanifest._folded_scalar(["d: >", "name: b"], 0)
        self.assertEqual(val, "")
        self.assertEqual(nxt, 1)

    def test_running_off_the_end_of_the_block_terminates(self):
        val, nxt = skillmanifest._folded_scalar(["d: >", "  tail"], 0)
        self.assertEqual((val, nxt), ("tail", 2))

    def test_it_does_not_test_the_key_at_all(self):
        # The guard lives at the CALL SITE, on purpose: `name: >` must yield the
        # literal ">". If this helper ever grew the key test, it would look
        # total while changing what `name` parses to.
        val, _n = skillmanifest._folded_scalar(["name: >", "  gathered"], 0)
        self.assertEqual(val, "gathered")

    def test_the_marker_table_is_the_five_shapes_including_empty(self):
        self.assertEqual(skillmanifest._FOLDED_MARKERS,
                         (">", ">-", "|", "|-", ""))


class EmitValidateTest(unittest.TestCase):
    """The one `skill validate` renderer, driven from both arms' shapes."""

    ROWS = [{"name": "alpha", "ok": True, "errors": []},
            {"name": "beta", "ok": False, "errors": ["e-one", "e-two"]}]

    def test_text_prints_ok_fail_indented_errors_then_the_footer(self):
        with _captured() as (out, _err):
            skillmanifest._emit_validate(self.ROWS, False, "text", "FAIL: 1/2")
        self.assertEqual(out.getvalue(),
                         "OK    alpha\nFAIL  beta\n"
                         "        - e-one\n        - e-two\nFAIL: 1/2\n")

    def test_json_counts_the_rows_rather_than_a_literal(self):
        # Driven on TWO rows on purpose. The `--source` arm passes a one-row
        # list, so a fixture of size 1 cannot tell `len(results)` apart from the
        # literal `1` that arm's document happens to carry.
        with _captured() as (out, _err):
            skillmanifest._emit_validate(self.ROWS, False, "json", "ignored")
        doc = json.loads(out.getvalue())
        self.assertEqual(doc["count"], 2)
        self.assertEqual(doc["results"], self.ROWS)

    def test_the_ok_field_is_the_verdict_it_was_HANDED(self):
        # `cmd_skill_validate` computes `all(rows) and bool(rows)`, so an EMPTY
        # result set is a FAIL even though `all([])` is True. A renderer that
        # re-derived the verdict from its rows would flip exactly that run to
        # PASS -- and it is the only input on which the two disagree.
        with _captured() as (out, _err):
            skillmanifest._emit_validate([], False, "json", "FAIL: 0/0")
        self.assertIs(json.loads(out.getvalue())["ok"], False)

    def test_the_footer_is_NOT_printed_in_json(self):
        # The footer is the only part the two arms spell apart, and it is a
        # human-report line. Leaking it into the JSON stream would make the
        # document unparsable for a `--format json` caller.
        with _captured() as (out, _err):
            skillmanifest._emit_validate(self.ROWS, False, "json", "FAIL: 1/2")
        self.assertNotIn("FAIL: 1/2", out.getvalue())
        json.loads(out.getvalue())

    def test_an_empty_result_set_still_prints_the_footer(self):
        with _captured() as (out, _err):
            skillmanifest._emit_validate([], False, "text", "FAIL: 0/0")
        self.assertEqual(out.getvalue(), "FAIL: 0/0\n")


class EmitSkillAddTest(unittest.TestCase):
    """The one `skill add` renderer, and the two documents it must NOT merge."""

    PROV = {"name": "s", "trust": "external (untrusted)", "source": "/src",
            "pinned_ref": "n/a (external)", "vet_findings": [], "verdict": "PASS"}

    def test_json_emits_the_payload_VERBATIM_including_key_order(self):
        # The payload arrives already built because the two destinations emit
        # different key SETS in a fixed ORDER. Re-serialising from a re-derived
        # dict is what would make them converge, so the assertion is on the raw
        # text, not on a parsed dict.
        payload = {"ok": True, "installed": True, "dest": "/d",
                   "indexed": True, "id": "skill-s", "path": "skills/s/SKILL.md",
                   "provenance": self.PROV, "note": "copied"}
        with _captured() as (out, _err):
            skillmanifest._emit_skill_add("json", payload, "t", self.PROV,
                                          True, "s", "/d")
        text = out.getvalue()
        self.assertEqual(text, json.dumps(payload, ensure_ascii=False,
                                          indent=2) + "\n")
        self.assertLess(text.index('"indexed"'), text.index('"id"'))
        self.assertLess(text.index('"id"'), text.index('"path"'))

    def test_the_harness_document_carries_no_id_or_path(self):
        payload = {"ok": True, "installed": False, "dest": "/h",
                   "indexed": False, "provenance": self.PROV, "note": "n"}
        with _captured() as (out, _err):
            skillmanifest._emit_skill_add("json", payload, "t", self.PROV,
                                          False, "s", "/h")
        doc = json.loads(out.getvalue())
        self.assertNotIn("id", doc)
        self.assertNotIn("path", doc)
        self.assertIs(doc["indexed"], False)

    def test_text_prints_the_vet_line_then_the_install_line(self):
        with _captured() as (out, _err):
            skillmanifest._emit_skill_add("text", {}, "trusted in-repo catalog",
                                          self.PROV, True, "s", "/d")
        self.assertEqual(
            out.getvalue(),
            "VET PASS (trusted in-repo catalog, ref n/a (external))\n"
            "INSTALLED skill 's' -> /d\n")

    def test_copied_false_prints_SKIPPED(self):
        with _captured() as (out, _err):
            skillmanifest._emit_skill_add("text", {}, "t", self.PROV,
                                          False, "s", "/d")
        self.assertIn("SKIPPED skill 's' -> /d", out.getvalue())

    def test_the_suffix_lands_on_the_install_line_not_a_new_one(self):
        with _captured() as (out, _err):
            skillmanifest._emit_skill_add("text", {}, "t", self.PROV, True,
                                          "s", "/h",
                                          suffix=" (harness path, not indexed)")
        self.assertIn("INSTALLED skill 's' -> /h (harness path, not indexed)\n",
                      out.getvalue())
        self.assertEqual(len(out.getvalue().strip().splitlines()), 2)

    def test_the_extra_line_is_printed_last_and_only_when_given(self):
        with _captured() as (out, _err):
            skillmanifest._emit_skill_add("text", {}, "t", self.PROV, True,
                                          "s", "/d", extra_line="REGISTERED x")
        self.assertTrue(out.getvalue().endswith("REGISTERED x\n"))
        with _captured() as (out2, _err):
            skillmanifest._emit_skill_add("text", {}, "t", self.PROV, True,
                                          "s", "/d", extra_line="")
        # Compared UNSTRIPPED: an `extra_line is not None` guard prints a blank
        # line here, and `.strip()` would hide exactly that.
        self.assertEqual(out2.getvalue(),
                         "VET PASS (t, ref n/a (external))\n"
                         "INSTALLED skill 's' -> /d\n")

    def test_neither_report_field_leaks_into_the_json_arm(self):
        with _captured() as (out, _err):
            skillmanifest._emit_skill_add("json", {"ok": True}, "t", self.PROV,
                                          True, "s", "/d",
                                          extra_line="REGISTERED x",
                                          suffix=" (harness path)")
        self.assertEqual(json.loads(out.getvalue()), {"ok": True})


class ModuleBoundaryTest(unittest.TestCase):
    """The dependency direction IS this module's boundary (R-MOD-02)."""

    def test_it_imports_only_downward_into_core(self):
        # RE-AIMED at task 99 wave 16, from "imports nothing from agentware_lib"
        # to the claim that actually carries the boundary. The reason these five
        # live below `features.skills` is that they reach back for nothing: an
        # import of `skills` here would close a cycle and the split would have
        # bought nothing. A `core` import cannot close a cycle — `core` is the
        # bottom layer and imports nothing above itself — so the old assertion
        # was strictly stronger than its own stated reason, and it refused the
        # ONE edge that removes a duplicated fence finder.
        #
        # This form is the R-MOD-02 rule itself, and it is a HARDER test: it
        # names every layer that would be a violation, where the old one would
        # have passed a module that imported nothing but grew a `features`
        # sibling edge tomorrow only if that edge were spelled with a plain
        # `import agentware_lib` prefix it also refused. Both refuse `skills`.
        import ast
        path = os.path.join(REPO, "scripts", "agentware_lib", "features",
                            "skillmanifest.py")
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        seen = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                seen.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                seen.append(node.module or "")
        lib = [m for m in seen if m.startswith("agentware_lib")]
        for mod in lib:
            self.assertTrue(mod.startswith("agentware_lib.core."),
                            "%s is not a DOWNWARD core import" % mod)
        # And the one it may not have, spelled out so a reader of a future
        # failure sees the cycle rather than a layer-prefix mismatch.
        for mod in lib:
            self.assertNotIn("features.skills", mod)
        self.assertEqual(lib, ["agentware_lib.core.frontmatter"])

    def test_exports_are_exact_and_all_resolve(self):
        self.assertEqual(skillmanifest._EXPORTS, [
            "_FOLDED_MARKERS", "_frontmatter_block", "_folded_scalar",
            "_emit_validate", "_emit_skill_add"])
        self.assertEqual(sorted(skillmanifest.__all__),
                         sorted(skillmanifest._EXPORTS))
        for name in skillmanifest._EXPORTS:
            self.assertTrue(hasattr(skillmanifest, name), name)


if __name__ == "__main__":
    unittest.main()
