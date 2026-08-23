"""Owning unit file for `agentware_lib.features.proposals.proposalpromote` — task 99 w16.

The module carried a `test-ownership` row, and the reason it existed is visible
in what this file asserts that the pre-existing coverage could not. The proposal
lifecycle is exercised end-to-end elsewhere (`test_skill_proposals.py` drives it
through the CLI), which reddens when the FEATURE breaks — but not when the
HANDLER's gate ORDER changes while every collaborator stays correct, and not at
all for the frontmatter rewrite's document taxonomy, which a CLI run reaches in
exactly one shape.

What is asserted here is the module's PROMISE, not its shape:

  * THE GATE ORDER IS THE CONTRACT. `_promote_preflight` refuses a bad slug
    before it resolves a knowledge dir, demands the proposal EXIST before it
    demands a reviewer (so a typo'd slug reports the typo, not the missing
    sign-off), vets the IN-PLACE folder before anything moves, and checks the
    destination last. Each pair is driven with BOTH conditions failing at once,
    which is the only input on which an ordering is observable at all.
  * THE VETTER IS FAIL-CLOSED AND REPORTS EVERY FINDING. Two findings are
    driven, not one: a single-finding fixture cannot tell "reports all" from
    "reports the first".
  * THE REWRITE IS A BLOCK EDIT, NOT A DOCUMENT REWRITE. `_promote_frontmatter`
    edits only the lines between the fences; a document whose BODY carries a
    `---` horizontal rule keeps it, an unclosed fence is left alone entirely,
    and fences that TOUCH are a real EMPTY block that still gains provenance.
  * THE TAGS ARM HAS A FALLBACK, AND THE FALLBACK IS THE POINT.
    `_promoted_tags_line` never emits an empty list: a proposal whose only tag
    was `proposed` becomes `[skill]`, because an entry with no tags is
    unreachable by every tag-scoped read.
  * `None` FROM `_promote_rewrite` MEANS FAILURE, NOT "NO HANDLE". The handle
    chain ends in the literal "unknown", so a successful rewrite always returns
    something truthy — which is what lets the caller spend ONE branch on it.
  * THE JSON DOCUMENTS HAVE A KEY ORDER. Both renderers are asserted against
    their raw text, not a re-parse, so a reorder is visible.

Hermetic: every filesystem input is a fresh mkdtemp; no live KB, no network.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.core.config as config                       # noqa: E402
import agentware_lib.knowledge.index as index                    # noqa: E402
import agentware_lib.features.proposals.proposalpromote as promote  # noqa: E402


@contextlib.contextmanager
def _captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


class Args(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# --------------------------------------------------------------------------
# _promoted_tags_line — the one arm with a value GRAMMAR
# --------------------------------------------------------------------------

class PromotedTagsLineTest(unittest.TestCase):

    def test_it_drops_only_the_transient_proposed_tag(self):
        self.assertEqual(promote._promoted_tags_line("[skill, proposed, x]"),
                         "tags: [skill, x]")

    def test_it_preserves_the_declared_order(self):
        self.assertEqual(promote._promoted_tags_line("[z, a, m]"),
                         "tags: [z, a, m]")

    def test_it_reads_an_unbracketed_list(self):
        self.assertEqual(promote._promoted_tags_line("skill, proposed"),
                         "tags: [skill]")

    def test_it_strips_padding_around_every_token(self):
        self.assertEqual(promote._promoted_tags_line("[ a ,b , proposed ]"),
                         "tags: [a, b]")

    def test_a_list_that_was_only_proposed_falls_back_to_skill(self):
        # The fallback is the POINT of the branch: an entry with no tags is
        # unreachable by every tag-scoped read, so an empty list is not a
        # tidier answer, it is a lost entry.
        self.assertEqual(promote._promoted_tags_line("[proposed]"),
                         "tags: [skill]")

    def test_an_empty_list_falls_back_too(self):
        self.assertEqual(promote._promoted_tags_line("[]"), "tags: [skill]")
        self.assertEqual(promote._promoted_tags_line(""), "tags: [skill]")

    def test_a_tag_merely_containing_proposed_survives(self):
        # The drop is an EQUALITY test, not a substring one.
        self.assertEqual(promote._promoted_tags_line("[proposed-work, proposed]"),
                         "tags: [proposed-work]")


# --------------------------------------------------------------------------
# _promote_frontmatter — the document taxonomy
# --------------------------------------------------------------------------

class PromoteFrontmatterTest(unittest.TestCase):

    def _rewrite(self, text):
        return promote._promote_frontmatter(text, "pby", "rby", "2026-01-02")

    def test_it_flips_category_status_and_tags_together(self):
        out = self._rewrite("---\nid: x\ncategory: references\n"
                            "status: proposed\ntags: [skill, proposed]\n---\n\nB\n")
        self.assertIn("category: skills", out)
        self.assertIn("status: active", out)
        self.assertIn("tags: [skill]", out)
        self.assertNotIn("category: references", out)

    def test_it_stamps_all_three_provenance_lines_before_the_close(self):
        out = self._rewrite("---\nid: x\n---\n\nB\n").split("\n")
        self.assertEqual(out[2:5], ["promoted_by: pby", "reviewed_by: rby",
                                    "promoted: 2026-01-02"])
        self.assertEqual(out[5], "---")

    def test_a_document_with_no_fence_comes_back_byte_identical(self):
        text = "# Title\n\nnot frontmatter\n"
        self.assertEqual(self._rewrite(text), text)

    def test_an_unclosed_fence_comes_back_byte_identical(self):
        # The SAME answer as "no fence", down a different arm. Inventing a block
        # here would stamp provenance onto prose.
        text = "---\nid: x\n\n# Title\n"
        self.assertEqual(self._rewrite(text), text)

    def test_touching_fences_are_a_real_empty_block_and_still_gain_provenance(self):
        out = self._rewrite("---\n---\n\n# T\n")
        self.assertIn("promoted_by: pby", out)
        self.assertTrue(out.startswith("---\npromoted_by:"))

    def test_a_horizontal_rule_in_the_body_survives(self):
        # The close is the FIRST one. A finder searching from the end would
        # swallow the body into the block.
        text = "---\ncategory: references\n---\n\n# T\n\n---\n\ntail text\n"
        out = self._rewrite(text)
        self.assertTrue(out.rstrip().endswith("tail text"))
        self.assertIn("\n---\n\ntail text", out)

    def test_an_indented_opening_fence_is_re_emitted_verbatim(self):
        out = self._rewrite("  ---\ncategory: references\n---\n\n# T\n")
        self.assertTrue(out.startswith("  ---\n"))
        self.assertIn("category: skills", out)

    def test_a_non_key_line_passes_through_untouched(self):
        out = self._rewrite("---\n# a comment\nbare\n\ncategory: references\n"
                            "---\n\n# T\n")
        self.assertIn("\n# a comment\nbare\n\n", out)

    def test_an_unrecognised_key_passes_through_untouched(self):
        out = self._rewrite("---\nauthor: someone\ncreated: 2026-01-01\n---\n")
        self.assertIn("author: someone", out)
        self.assertIn("created: 2026-01-01", out)

    def test_a_value_containing_colons_is_not_re_split(self):
        out = self._rewrite("---\ndescription: a: b: c\n---\n")
        self.assertIn("description: a: b: c", out)

    def test_every_duplicate_key_is_rewritten_not_just_the_first(self):
        out = self._rewrite("---\ncategory: references\ncategory: references\n---\n")
        self.assertEqual(out.count("category: skills"), 2)

    def test_the_body_is_never_touched(self):
        body = "\n# T\n\nsome body\nwith lines\n"
        out = self._rewrite("---\nid: x\n---" + body)
        self.assertTrue(out.endswith(body))


# --------------------------------------------------------------------------
# _promote_preflight — the gate ORDER
# --------------------------------------------------------------------------

class PromotePreflightTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-t99w16-pre-")
        self.kdir = os.path.join(self.root, "kb")
        os.makedirs(os.path.join(self.kdir, "skills-proposed"))
        self._real = config.require_knowledge_dir
        config.require_knowledge_dir = lambda: self.kdir

    def tearDown(self):
        config.require_knowledge_dir = self._real
        shutil.rmtree(self.root, ignore_errors=True)

    def _seed(self, slug, extra=None):
        pdir = os.path.join(self.kdir, "skills-proposed", slug)
        _write(os.path.join(pdir, "SKILL.md"),
               "---\nid: skill-%s\ncategory: references\n---\n\n# %s\n"
               % (slug, slug))
        if extra:
            for rel, text in extra.items():
                _write(os.path.join(pdir, rel), text)
        return pdir

    def test_a_bad_slug_is_refused_before_a_knowledge_dir_is_resolved(self):
        called = []
        config.require_knowledge_dir = lambda: called.append(1)
        with _captured() as (_o, err):
            self.assertIsNone(promote._promote_preflight("Bad Slug", "r", "text"))
        self.assertEqual(called, [])
        self.assertIn("kebab-case", err.getvalue())

    def test_an_unresolvable_knowledge_dir_refuses_silently(self):
        config.require_knowledge_dir = lambda: None
        with _captured() as (out, err):
            self.assertIsNone(promote._promote_preflight("ok-slug", "r", "text"))
        # require_knowledge_dir has already spoken; this must not speak twice.
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(out.getvalue(), "")

    def test_a_missing_proposal_is_reported_before_the_missing_reviewer(self):
        # BOTH are wrong at once — the only input on which the order shows.
        with _captured() as (_o, err):
            self.assertIsNone(promote._promote_preflight("gone-slug", "", "text"))
        self.assertIn("no proposal", err.getvalue())
        self.assertNotIn("reviewed-by", err.getvalue())

    def test_a_missing_reviewer_is_reported_before_the_vetter_runs(self):
        self._seed("dirty-x", {"run.sh": "exec 3<>/dev/tcp/h/1\n"})
        with _captured() as (_o, err):
            self.assertIsNone(promote._promote_preflight("dirty-x", "", "text"))
        self.assertIn("reviewed-by", err.getvalue())
        self.assertNotIn("skill-vetter", err.getvalue())

    def test_the_vetter_reports_every_finding_not_the_first(self):
        self._seed("dirty-y", {"run.sh": "exec 3<>/dev/tcp/h/1\nbase64 -d p\n"})
        with _captured() as (_o, err):
            self.assertIsNone(promote._promote_preflight("dirty-y", "r", "text"))
        self.assertEqual(err.getvalue().count("\n  - "), 2)

    def test_the_vetter_refusal_leaves_the_proposal_in_place(self):
        pdir = self._seed("dirty-z", {"run.sh": "exec 3<>/dev/tcp/h/1\n"})
        with _captured():
            promote._promote_preflight("dirty-z", "r", "text")
        self.assertTrue(os.path.isdir(pdir))

    def test_the_vetter_refusal_emits_a_json_document_in_json_mode(self):
        self._seed("dirty-j", {"run.sh": "exec 3<>/dev/tcp/h/1\n"})
        with _captured() as (out, _e):
            promote._promote_preflight("dirty-j", "r", "json")
        doc = json.loads(out.getvalue())
        self.assertEqual(doc["ok"], False)
        self.assertEqual(doc["reason"], "vet-findings")
        self.assertEqual(list(doc), ["ok", "slug", "reason", "vet_findings"])

    def test_the_vetter_refusal_is_silent_on_stdout_in_text_mode(self):
        self._seed("dirty-t", {"run.sh": "exec 3<>/dev/tcp/h/1\n"})
        with _captured() as (out, _e):
            promote._promote_preflight("dirty-t", "r", "text")
        self.assertEqual(out.getvalue(), "")

    def test_an_occupied_destination_is_refused_last(self):
        self._seed("occupied")
        os.makedirs(os.path.join(self.kdir, "skills", "occupied"))
        with _captured() as (_o, err):
            self.assertIsNone(promote._promote_preflight("occupied", "r", "text"))
        self.assertIn("refusing to clobber", err.getvalue())

    def test_a_clean_proposal_returns_the_three_resolved_paths(self):
        pdir = self._seed("clean-one")
        got = promote._promote_preflight("clean-one", "r", "text")
        self.assertEqual(got, (self.kdir, pdir,
                               os.path.join(self.kdir, "skills", "clean-one")))


# --------------------------------------------------------------------------
# _promote_rewrite / _promote_emit
# --------------------------------------------------------------------------

class PromoteRewriteTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-t99w16-rw-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_it_returns_a_truthy_handle_on_success(self):
        md = _write(os.path.join(self.root, "SKILL.md"),
                    "---\ncategory: references\n---\n\n# T\n")
        got = promote._promote_rewrite(self.root, md, "rev")
        self.assertTrue(got)
        self.assertIn("category: skills", _read(md))

    def test_none_means_failure_and_can_never_mean_no_handle(self):
        # The chain ends in the literal "unknown", so a successful rewrite is
        # always truthy — which is what lets the caller spend ONE branch here.
        md = os.path.join(self.root, "as-a-dir", "SKILL.md")
        os.makedirs(md)
        with _captured() as (_o, err):
            self.assertIsNone(promote._promote_rewrite(self.root, md, "rev"))
        self.assertIn("could not rewrite", err.getvalue())

    def test_an_unresolvable_handle_stamps_the_literal_unknown(self):
        # The tail of the chain, and the reason `None` can mean FAILURE: with
        # both resolvers dry the result is still truthy. Found by the wave's
        # mutation sweep — dropping the `or "unknown"` left every other test
        # green, because a real environment always resolves something.
        import agentware_lib.core.routing as routing
        import agentware_lib.core.frontmatter as frontmatter
        real_user, real_op = (routing.resolve_user_handle,
                              frontmatter._operator_handle)
        routing.resolve_user_handle = lambda: None
        frontmatter._operator_handle = lambda kdir: None
        try:
            md = _write(os.path.join(self.root, "SKILL.md"), "---\nid: x\n---\n")
            got = promote._promote_rewrite(self.root, md, "rev")
        finally:
            routing.resolve_user_handle = real_user
            frontmatter._operator_handle = real_op
        self.assertEqual(got, "unknown")
        self.assertIn("promoted_by: unknown", _read(md))

    def test_it_stamps_the_reviewer_it_was_handed(self):
        md = _write(os.path.join(self.root, "SKILL.md"), "---\nid: x\n---\n")
        promote._promote_rewrite(self.root, md, "the-reviewer")
        self.assertIn("reviewed_by: the-reviewer", _read(md))


class PromoteEmitTest(unittest.TestCase):

    def test_the_json_document_carries_a_fixed_key_order(self):
        with _captured() as (out, _e):
            rc = promote._promote_emit("s", "json", "pby", "rby", 3, 5)
        self.assertEqual(rc, 0)
        self.assertEqual(
            list(json.loads(out.getvalue())),
            ["ok", "slug", "id", "promoted_by", "reviewed_by",
             "proposal_count", "threshold", "path"])

    def test_the_text_renderer_prints_two_lines_with_the_count_and_reviewer(self):
        with _captured() as (out, _e):
            promote._promote_emit("s", "text", "pby", "rby", 3, 5)
        lines = out.getvalue().rstrip("\n").split("\n")
        self.assertEqual(len(lines), 2)
        self.assertIn("count=3/5", lines[0])
        self.assertIn("reviewed-by=rby", lines[0])
        self.assertIn("skill-s (category=skills)", lines[1])

    def test_neither_renderer_writes_to_stderr(self):
        for fmt in ("text", "json"):
            with _captured() as (_o, err):
                promote._promote_emit("s", fmt, "p", "r", 1, 5)
            self.assertEqual(err.getvalue(), "")


# --------------------------------------------------------------------------
# cmd_skill_promote — the composed handler
# --------------------------------------------------------------------------

class CmdSkillPromoteTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-t99w16-cmd-")
        self.kdir = os.path.join(self.root, "kb")
        os.makedirs(os.path.join(self.kdir, "skills-proposed"))
        self._real = config.require_knowledge_dir
        self._rebuild = index.rebuild_kb
        config.require_knowledge_dir = lambda: self.kdir
        index.rebuild_kb = lambda k: ({}, {}, [])

    def tearDown(self):
        config.require_knowledge_dir = self._real
        index.rebuild_kb = self._rebuild
        shutil.rmtree(self.root, ignore_errors=True)

    def _seed(self, slug):
        pdir = os.path.join(self.kdir, "skills-proposed", slug)
        _write(os.path.join(pdir, "SKILL.md"),
               "---\nid: skill-%s\ncategory: references\nstatus: proposed\n"
               "tags: [skill, proposed]\n---\n\n# %s\n" % (slug, slug))
        _write(os.path.join(pdir, "references.jsonl"),
               '{"date": "2026-01-01", "ref": "feat-1"}\n')
        return pdir

    def test_a_successful_promotion_moves_the_folder_with_its_ledger(self):
        self._seed("happy")
        with _captured() as (out, _e):
            rc = promote.cmd_skill_promote(Args(slug="happy", reviewed_by="rev",
                                                format="text"))
        self.assertEqual(rc, 0)
        self.assertIn("PROMOTED proposal", out.getvalue())
        self.assertFalse(os.path.exists(
            os.path.join(self.kdir, "skills-proposed", "happy")))
        dest = os.path.join(self.kdir, "skills", "happy")
        self.assertTrue(os.path.isfile(os.path.join(dest, "references.jsonl")))
        text = _read(os.path.join(dest, "SKILL.md"))
        self.assertIn("category: skills", text)
        self.assertIn("status: active", text)
        self.assertIn("reviewed_by: rev", text)

    def test_the_slug_and_reviewer_are_stripped_before_the_gates_see_them(self):
        self._seed("padded")
        with _captured():
            rc = promote.cmd_skill_promote(Args(slug="  padded  ",
                                                reviewed_by="  rev  ",
                                                format="text"))
        self.assertEqual(rc, 0)
        self.assertIn("reviewed_by: rev\n", _read(os.path.join(
            self.kdir, "skills", "padded", "SKILL.md")))

    def test_a_whitespace_only_reviewer_is_no_reviewer(self):
        self._seed("blank-rev")
        with _captured() as (_o, err):
            rc = promote.cmd_skill_promote(Args(slug="blank-rev",
                                                reviewed_by="   ",
                                                format="text"))
        self.assertEqual(rc, 1)
        self.assertIn("reviewed-by", err.getvalue())

    def test_a_refused_preflight_exits_one_without_moving_anything(self):
        pdir = self._seed("no-rev")
        with _captured():
            rc = promote.cmd_skill_promote(Args(slug="no-rev", reviewed_by=None,
                                                format="text"))
        self.assertEqual(rc, 1)
        self.assertTrue(os.path.isdir(pdir))
        self.assertFalse(os.path.exists(os.path.join(self.kdir, "skills")))

    def test_a_rebuild_error_exits_one_and_reports_every_error(self):
        self._seed("rb")
        index.rebuild_kb = lambda k: ({}, {}, ["one", "two"])
        with _captured() as (_o, err):
            rc = promote.cmd_skill_promote(Args(slug="rb", reviewed_by="rev",
                                                format="text"))
        self.assertEqual(rc, 1)
        self.assertEqual(err.getvalue().count("rebuild:"), 2)

    def test_a_rebuild_error_still_leaves_the_manifest_rewritten(self):
        # The rewrite happens BEFORE the rebuild; a failed rebuild is a reporting
        # failure, not a reason to have half-written the entry.
        self._seed("rb2")
        index.rebuild_kb = lambda k: ({}, {}, ["boom"])
        with _captured():
            promote.cmd_skill_promote(Args(slug="rb2", reviewed_by="rev",
                                           format="text"))
        self.assertIn("category: skills", _read(os.path.join(
            self.kdir, "skills", "rb2", "SKILL.md")))

    def test_the_json_arm_reports_the_count_and_threshold(self):
        self._seed("jd")
        with _captured() as (out, _e):
            rc = promote.cmd_skill_promote(Args(slug="jd", reviewed_by="rev",
                                                format="json"))
        self.assertEqual(rc, 0)
        doc = json.loads(out.getvalue())
        self.assertEqual(doc["id"], "skill-jd")
        self.assertEqual(doc["proposal_count"], 1)
        self.assertEqual(doc["path"], "skills/jd/SKILL.md")

    def test_promoting_twice_is_refused_by_the_destination_gate(self):
        self._seed("twice")
        with _captured():
            promote.cmd_skill_promote(Args(slug="twice", reviewed_by="rev",
                                           format="text"))
        self._seed("twice")
        with _captured() as (_o, err):
            rc = promote.cmd_skill_promote(Args(slug="twice", reviewed_by="rev",
                                                format="text"))
        self.assertEqual(rc, 1)
        self.assertIn("refusing to clobber", err.getvalue())


class ExportSurfaceTest(unittest.TestCase):
    """The wave's new helpers are deliberately ABSENT from the facade surface.

    They have no monolith counterpart, and exporting them would widen the
    surface the parity machinery pins for no caller's benefit — every one of
    them has exactly one caller, in this module.
    """

    def test_exports_are_exactly_the_two_shipped_names(self):
        self.assertEqual(promote._EXPORTS,
                         ["_promote_frontmatter", "cmd_skill_promote"])

    def test_the_new_helpers_exist_but_are_not_exported(self):
        for name in ("_promoted_tags_line", "_promote_preflight",
                     "_promote_rewrite", "_promote_emit"):
            self.assertTrue(hasattr(promote, name), name)
            self.assertNotIn(name, promote._EXPORTS)


if __name__ == "__main__":
    unittest.main()
