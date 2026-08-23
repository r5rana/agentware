"""Owning unit file for `agentware_lib.features.proposals.proposeauthor` — task 99 w16.

The module carried a `test-ownership` row. The pre-existing coverage drives the
proposal lifecycle through the CLI, which observes the printed count and the
files on disk — so it reddens when the FEATURE breaks, and not when the ledger's
optional fields, the dedup guard's ORDER relative to the dry-run preview, or the
`ripe` comparison behind two renderers change while every collaborator stays
correct.

What is asserted here is the module's PROMISE, not its shape:

  * THE DEDUP GUARD OWNS ITS OWN "IS THIS NEW" TEST. An EXACT slug match is the
    create-or-increment path, not a near-duplicate, so "already staged" is a
    reason to SKIP the guard rather than a case for the caller to pre-compute.
  * THE GUARD RUNS BEFORE THE PREVIEW. A `--dry-run` over a near-duplicate slug
    must be REFUSED, not previewed: a dry run that skipped the refusals would
    report success for a proposal the real command rejects, which is worse than
    no preview at all.
  * AN ABSENT OPTIONAL LEDGER FIELD IS OMITTED, NOT EMPTY. Every reader of the
    append-only ledger treats a present key as a claim, so `run` and
    `worklog_line` appear only when they were given.
  * `ripe` IS DECIDED ONCE. The json flag and the text renderer's `[RIPE]`
    suffix are two spellings of one comparison, and they are asserted to agree
    at the boundary value, not merely to exist.
  * THE COUNT IS DERIVED FROM UNIQUE REFS. A REPEAT `--ref` appends a ledger
    line and does NOT inflate the count; a DISTINCT one does. Both are driven,
    because a fixture using one ref cannot tell the two apart.
  * THE DESCRIPTION IS THE FIRST NON-EMPTY BODY LINE, DE-HEADED. An empty body
    falls back to a stub rather than emitting `description:` with nothing after
    it, which is unroutable.

Hermetic: every filesystem input is a fresh mkdtemp, `rebuild_kb` is stubbed
where the assertion is not about the index, and no live KB is touched.
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

import agentware_lib.core.config as config                      # noqa: E402
import agentware_lib.knowledge.index as index                   # noqa: E402
import agentware_lib.features.proposals.proposeauthor as author  # noqa: E402


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
# the slug grammar and the dedup primitives
# --------------------------------------------------------------------------

class SlugGrammarTest(unittest.TestCase):

    def test_a_lowercase_kebab_identifier_is_valid(self):
        for slug in ("a1", "alpha", "alpha-beta", "a-b-c", "x9-y8"):
            self.assertTrue(author._valid_proposal_slug(slug), slug)

    def test_a_slug_may_not_start_or_end_with_a_hyphen(self):
        for slug in ("-alpha", "alpha-", "-"):
            self.assertFalse(author._valid_proposal_slug(slug), slug)

    def test_uppercase_spaces_and_underscores_are_refused(self):
        for slug in ("Alpha", "al pha", "al_pha", "al.pha", "al/pha"):
            self.assertFalse(author._valid_proposal_slug(slug), slug)

    def test_the_empty_slug_is_refused_without_touching_the_regex(self):
        self.assertFalse(author._valid_proposal_slug(""))
        self.assertFalse(author._valid_proposal_slug(None))

    def test_a_single_character_is_refused_because_the_ends_may_not_coincide(self):
        # `^[a-z0-9][a-z0-9-]*[a-z0-9]$` spells the first and last characters
        # SEPARATELY, so the shortest legal slug is two characters. Recorded
        # rather than assumed: the obvious reading of "starts and ends
        # alphanumeric" admits "a", and this file found that it does not.
        self.assertFalse(author._valid_proposal_slug("a"))
        self.assertTrue(author._valid_proposal_slug("ab"))


class SlugTokenSetTest(unittest.TestCase):

    def test_hyphens_become_word_boundaries(self):
        self.assertEqual(author._slug_token_set("alpha-beta"),
                         author._slug_token_set("beta alpha"))

    def test_the_body_widens_the_set(self):
        bare = author._slug_token_set("alpha")
        withbody = author._slug_token_set("alpha", "gamma delta")
        self.assertTrue(bare < withbody)

    def test_a_none_body_is_the_same_as_no_body(self):
        self.assertEqual(author._slug_token_set("alpha", None),
                         author._slug_token_set("alpha"))


# --------------------------------------------------------------------------
# _propose_dedup_guard
# --------------------------------------------------------------------------

class DedupGuardTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-t99w16-dedup-")
        self.kdir = os.path.join(self.root, "kb")
        os.makedirs(os.path.join(self.kdir, "skills-proposed"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _stage(self, slug, desc):
        _write(os.path.join(self.kdir, "skills-proposed", slug, "SKILL.md"),
               "---\nname: %s\ndescription: %s\n---\n\n# %s\n"
               % (slug, desc, slug))

    def test_an_existing_slug_skips_the_guard_entirely(self):
        # An EXACT match is the increment path. The guard owns this test so the
        # caller spends one branch, not three.
        #
        # THE FIXTURE MUST STAGE A SIBLING TOO. `near_duplicate_proposal` skips
        # the slug it was asked about, so with only that one proposal on disk
        # the guard finds nothing whether or not the `exists` test runs — the
        # first draft of this test passed with the test deleted. The near-
        # duplicate sibling is what separates "skipped the guard" from "ran it
        # and found nothing" (the wave-15 lesson: a test can only distinguish
        # alternatives its fixture separates).
        self._stage("alpha-beta-gamma", "alpha beta gamma procedure")
        self._stage("gamma-beta-alpha", "alpha beta gamma procedure")
        with _captured() as (out, err):
            got = author._propose_dedup_guard(
                self.kdir, "alpha-beta-gamma", "alpha beta gamma procedure",
                "ref", "text", True)
        self.assertIsNone(got)
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def test_the_same_pair_IS_refused_when_the_slug_is_new(self):
        # The non-vacuity half of the test above, in the same fixture: the only
        # thing that changed is `exists`.
        self._stage("alpha-beta-gamma", "alpha beta gamma procedure")
        self._stage("gamma-beta-alpha", "alpha beta gamma procedure")
        with _captured() as (_o, err):
            got = author._propose_dedup_guard(
                self.kdir, "alpha-beta-gamma", "alpha beta gamma procedure",
                "ref", "text", False)
        self.assertEqual(got, 1)
        self.assertIn("near-duplicate", err.getvalue())

    def test_a_genuinely_new_slug_passes(self):
        self._stage("alpha-beta-gamma", "alpha beta gamma procedure")
        with _captured() as (out, err):
            got = author._propose_dedup_guard(
                self.kdir, "totally-unrelated-words", "nothing alike here",
                "ref", "text", False)
        self.assertIsNone(got)
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def test_a_near_duplicate_is_refused_and_names_the_existing_slug(self):
        self._stage("alpha-beta-gamma", "alpha beta gamma procedure")
        with _captured() as (_o, err):
            got = author._propose_dedup_guard(
                self.kdir, "gamma-beta-alpha", "alpha beta gamma procedure",
                "feat-1", "text", False)
        self.assertEqual(got, 1)
        msg = err.getvalue()
        self.assertIn("near-duplicate", msg)
        self.assertIn("did you mean 'alpha-beta-gamma'", msg)
        # The refusal must name the RE-RUN, because forcing reuse is the point.
        self.assertIn("--slug alpha-beta-gamma --ref feat-1", msg)

    def test_the_json_refusal_carries_the_suggestion_as_data(self):
        self._stage("alpha-beta-gamma", "alpha beta gamma procedure")
        with _captured() as (out, _e):
            author._propose_dedup_guard(
                self.kdir, "gamma-beta-alpha", "alpha beta gamma procedure",
                "feat-1", "json", False)
        doc = json.loads(out.getvalue())
        self.assertEqual(doc["did_you_mean"], "alpha-beta-gamma")
        self.assertEqual(list(doc), ["ok", "slug", "reason", "did_you_mean"])

    def test_the_text_refusal_writes_nothing_to_stdout(self):
        self._stage("alpha-beta-gamma", "alpha beta gamma procedure")
        with _captured() as (out, _e):
            author._propose_dedup_guard(
                self.kdir, "gamma-beta-alpha", "alpha beta gamma procedure",
                "feat-1", "text", False)
        self.assertEqual(out.getvalue(), "")

    def test_an_empty_staging_area_can_never_refuse(self):
        with _captured() as (_o, err):
            got = author._propose_dedup_guard(self.kdir, "anything-at-all",
                                              "body", "ref", "text", False)
        self.assertIsNone(got)
        self.assertEqual(err.getvalue(), "")


# --------------------------------------------------------------------------
# _propose_ledger_event
# --------------------------------------------------------------------------

class LedgerEventTest(unittest.TestCase):

    def test_the_three_always_present_fields(self):
        ev = author._propose_ledger_event(Args(), "feat-1", "auth", "2026-01-01")
        self.assertEqual(ev, {"ref": "feat-1", "author": "auth",
                              "date": "2026-01-01"})

    def test_an_absent_optional_is_omitted_not_emitted_empty(self):
        ev = author._propose_ledger_event(Args(run=None, worklog_line=None),
                                          "r", "a", "d")
        self.assertNotIn("run", ev)
        self.assertNotIn("worklog_line", ev)

    def test_an_empty_string_optional_is_also_omitted(self):
        # The test is TRUTHINESS, not presence: `--run ""` is not a run id.
        ev = author._propose_ledger_event(Args(run="", worklog_line=""),
                                          "r", "a", "d")
        self.assertNotIn("run", ev)
        self.assertNotIn("worklog_line", ev)

    def test_each_optional_appears_alone(self):
        ev = author._propose_ledger_event(Args(run="run-7"), "r", "a", "d")
        self.assertEqual(ev.get("run"), "run-7")
        self.assertNotIn("worklog_line", ev)
        ev = author._propose_ledger_event(Args(worklog_line="w.md:3"),
                                          "r", "a", "d")
        self.assertEqual(ev.get("worklog_line"), "w.md:3")
        self.assertNotIn("run", ev)

    def test_both_optionals_appear_together(self):
        ev = author._propose_ledger_event(Args(run="run-8", worklog_line="w:4"),
                                          "r", "a", "d")
        self.assertEqual(ev["run"], "run-8")
        self.assertEqual(ev["worklog_line"], "w:4")


# --------------------------------------------------------------------------
# _report_dry_run and _propose_emit
# --------------------------------------------------------------------------

class DryRunReportTest(unittest.TestCase):

    def test_it_returns_none_when_this_is_not_a_dry_run(self):
        # It owns the TEST as well as the report, so the caller spends one
        # branch instead of two.
        with _captured() as (out, _e):
            got = author._report_dry_run(Args(dry_run=False), "/k", "s", "r",
                                         "a", False, "text")
        self.assertIsNone(got)
        self.assertEqual(out.getvalue(), "")

    def test_a_missing_dry_run_attribute_is_not_a_dry_run(self):
        with _captured():
            self.assertIsNone(author._report_dry_run(Args(), "/k", "s", "r",
                                                     "a", False, "text"))

    def test_create_and_increment_are_different_words(self):
        with _captured() as (out, _e):
            author._report_dry_run(Args(dry_run=True), "/k", "s", "r", "a",
                                   False, "text")
        self.assertIn("would create", out.getvalue())
        with _captured() as (out, _e):
            author._report_dry_run(Args(dry_run=True), "/k", "s", "r", "a",
                                   True, "text")
        self.assertIn("would increment", out.getvalue())

    def test_the_json_preview_carries_the_resolved_proposal_dir(self):
        with _captured() as (out, _e):
            rc = author._report_dry_run(Args(dry_run=True), "/k", "s", "r", "a",
                                        False, "json")
        self.assertEqual(rc, 0)
        doc = json.loads(out.getvalue())
        self.assertTrue(doc["dry_run"])
        self.assertTrue(doc["proposal_dir"].endswith("/s"))

    def test_the_text_preview_says_nothing_was_written(self):
        with _captured() as (out, _e):
            author._report_dry_run(Args(dry_run=True), "/k", "s", "r", "a",
                                   False, "text")
        self.assertIn("nothing written", out.getvalue())


class ProposeEmitTest(unittest.TestCase):

    def test_ripe_is_one_comparison_behind_two_renderers(self):
        # Boundary value: count == threshold IS ripe. Both spellings agree.
        with _captured() as (out, _e):
            author._propose_emit("s", "r", False, 5, 5, "json")
        self.assertTrue(json.loads(out.getvalue())["ripe"])
        with _captured() as (out, _e):
            author._propose_emit("s", "r", False, 5, 5, "text")
        self.assertIn("[RIPE]", out.getvalue())

    def test_one_below_the_threshold_is_not_ripe_in_either_renderer(self):
        with _captured() as (out, _e):
            author._propose_emit("s", "r", False, 4, 5, "json")
        self.assertFalse(json.loads(out.getvalue())["ripe"])
        with _captured() as (out, _e):
            author._propose_emit("s", "r", False, 4, 5, "text")
        self.assertNotIn("[RIPE]", out.getvalue())

    def test_created_and_incremented_are_complementary(self):
        with _captured() as (out, _e):
            author._propose_emit("s", "r", False, 1, 5, "json")
        doc = json.loads(out.getvalue())
        self.assertEqual((doc["created"], doc["incremented"]), (True, False))
        with _captured() as (out, _e):
            author._propose_emit("s", "r", True, 2, 5, "json")
        doc = json.loads(out.getvalue())
        self.assertEqual((doc["created"], doc["incremented"]), (False, True))

    def test_the_text_verb_matches_the_created_flag(self):
        with _captured() as (out, _e):
            author._propose_emit("s", "r", False, 1, 5, "text")
        self.assertTrue(out.getvalue().startswith("CREATED "))
        with _captured() as (out, _e):
            author._propose_emit("s", "r", True, 2, 5, "text")
        self.assertTrue(out.getvalue().startswith("INCREMENTED "))

    def test_the_json_document_carries_a_fixed_key_order(self):
        with _captured() as (out, _e):
            rc = author._propose_emit("s", "r", False, 1, 5, "json")
        self.assertEqual(rc, 0)
        self.assertEqual(list(json.loads(out.getvalue())),
                         ["ok", "slug", "created", "incremented", "ref",
                          "proposal_count", "threshold", "ripe", "path"])


# --------------------------------------------------------------------------
# _render_proposal_skill_md
# --------------------------------------------------------------------------

class RenderProposalTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-t99w16-render-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_the_description_is_the_first_non_empty_body_line_de_headed(self):
        out = author._render_proposal_skill_md(
            "s", self.root, "\n\n## A Heading\nrest\n", "a", "2026-01-01")
        self.assertIn("description: A Heading", out)

    def test_an_empty_body_falls_back_to_a_stub_description(self):
        # `description:` with nothing after it is unroutable, so the fallback is
        # the point of the branch.
        out = author._render_proposal_skill_md("s", self.root, "", "a", "d")
        self.assertIn("awaiting frequency + promotion", out)

    def test_a_whitespace_only_body_also_falls_back(self):
        out = author._render_proposal_skill_md("s", self.root, "\n  \n\t\n",
                                               "a", "d")
        self.assertIn("awaiting frequency + promotion", out)

    def test_the_category_is_references_so_it_never_joins_the_roster(self):
        out = author._render_proposal_skill_md("s", self.root, "b", "a", "d")
        self.assertIn("category: references", out)
        self.assertIn("status: proposed", out)
        self.assertIn("tags: [skill, proposed]", out)

    def test_a_flat_kb_emits_neither_org_key(self):
        out = author._render_proposal_skill_md("s", self.root, "b", "a", "d")
        self.assertNotIn("\norigin:", out)
        self.assertNotIn("\nteam:", out)

    def test_the_body_is_appended_below_an_h1(self):
        out = author._render_proposal_skill_md("s", self.root, "the body\n",
                                               "a", "d")
        self.assertTrue(out.endswith("# s\n\nthe body\n"))

    def test_an_empty_body_leaves_the_h1_with_nothing_below_it(self):
        out = author._render_proposal_skill_md("s", self.root, "", "a", "d")
        self.assertTrue(out.endswith("# s\n\n"))


# --------------------------------------------------------------------------
# cmd_skill_propose / _propose_author — the composed handler
# --------------------------------------------------------------------------

class CmdSkillProposeTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-t99w16-prop-")
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

    def _args(self, **kw):
        base = dict(slug="alpha-one", ref="feat-1", body=None, run=None,
                    worklog_line=None, to_shared=False, dry_run=False,
                    format="text")
        base.update(kw)
        return Args(**base)

    def _run(self, **kw):
        with _captured() as (out, err):
            rc = author.cmd_skill_propose(self._args(**kw))
        return rc, out.getvalue(), err.getvalue()

    def test_a_bad_slug_is_refused_before_anything_is_resolved(self):
        rc, _o, err = self._run(slug="Bad Slug")
        self.assertEqual(rc, 1)
        self.assertIn("--slug must be a lowercase kebab-case", err)

    def test_an_empty_ref_is_refused_after_the_slug(self):
        rc, _o, err = self._run(ref="   ")
        self.assertEqual(rc, 1)
        self.assertIn("--ref must not be empty", err)

    def test_a_bad_slug_wins_over_an_empty_ref(self):
        # BOTH wrong at once — the only input on which the order is observable.
        _rc, _o, err = self._run(slug="Bad Slug", ref="")
        self.assertIn("--slug", err)
        self.assertNotIn("--ref must not be empty", err)

    def test_a_new_slug_creates_the_manifest_and_seeds_the_ledger(self):
        rc, out, _e = self._run(body="the alpha one procedure")
        self.assertEqual(rc, 0)
        self.assertIn("CREATED proposal", out)
        pdir = os.path.join(self.kdir, "skills-proposed", "alpha-one")
        self.assertIn("the alpha one procedure",
                      _read(os.path.join(pdir, "SKILL.md")))
        self.assertEqual(
            len(_read(os.path.join(pdir, "references.jsonl")).strip().split("\n")),
            1)

    def test_a_repeat_ref_appends_a_line_but_does_not_inflate_the_count(self):
        self._run()
        rc, out, _e = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("INCREMENTED proposal", out)
        self.assertIn("proposal_count=1/", out)
        ledger = _read(os.path.join(self.kdir, "skills-proposed", "alpha-one",
                                    "references.jsonl"))
        self.assertEqual(len(ledger.strip().split("\n")), 2)

    def test_a_distinct_ref_increments_the_count(self):
        self._run()
        rc, out, _e = self._run(ref="feat-2")
        self.assertEqual(rc, 0)
        self.assertIn("proposal_count=2/", out)

    def test_an_increment_does_not_rewrite_the_manifest(self):
        self._run(body="original body")
        self._run(ref="feat-2", body="a different body")
        self.assertIn("original body", _read(os.path.join(
            self.kdir, "skills-proposed", "alpha-one", "SKILL.md")))

    def test_a_dry_run_over_a_near_duplicate_is_refused_not_previewed(self):
        # The guard runs BEFORE the preview: a dry run that skipped the refusals
        # would report success for a proposal the real command rejects.
        self._run(slug="alpha-beta-gamma", body="alpha beta gamma procedure")
        rc, out, err = self._run(slug="gamma-beta-alpha",
                                 body="alpha beta gamma procedure",
                                 dry_run=True)
        self.assertEqual(rc, 1)
        self.assertIn("near-duplicate", err)
        self.assertNotIn("would create", out)

    def test_a_dry_run_writes_nothing_at_all(self):
        rc, out, _e = self._run(slug="never-written", dry_run=True)
        self.assertEqual(rc, 0)
        self.assertIn("would create", out)
        self.assertFalse(os.path.exists(os.path.join(
            self.kdir, "skills-proposed", "never-written")))

    def test_a_dry_run_over_a_staged_slug_previews_an_increment(self):
        self._run()
        rc, out, _e = self._run(dry_run=True, ref="feat-2")
        self.assertEqual(rc, 0)
        self.assertIn("would increment", out)
        self.assertEqual(len(_read(os.path.join(
            self.kdir, "skills-proposed", "alpha-one",
            "references.jsonl")).strip().split("\n")), 1)

    def test_a_rebuild_error_exits_one_and_reports_every_error(self):
        index.rebuild_kb = lambda k: ({}, {}, ["one", "two"])
        rc, _o, err = self._run()
        self.assertEqual(rc, 1)
        self.assertEqual(err.count("rebuild:"), 2)

    def test_an_unresolvable_knowledge_dir_exits_one(self):
        config.require_knowledge_dir = lambda: None
        rc, _o, _e = self._run()
        self.assertEqual(rc, 1)

    def test_a_refused_shared_write_guard_exits_one_and_writes_nothing(self):
        real = author._fleet.guard_shared_write
        author._fleet.guard_shared_write = lambda k, to_shared=False: (
            False, "shared writes are curator-only")
        try:
            rc, _o, err = self._run()
        finally:
            author._fleet.guard_shared_write = real
        self.assertEqual(rc, 1)
        self.assertIn("curator-only", err)
        self.assertFalse(os.path.exists(os.path.join(
            self.kdir, "skills-proposed", "alpha-one")))

    def test_body_dash_reads_stdin(self):
        real_stdin = sys.stdin
        sys.stdin = io.StringIO("piped body text\n")
        try:
            rc, _o, _e = self._run(body="-")
        finally:
            sys.stdin = real_stdin
        self.assertEqual(rc, 0)
        self.assertIn("piped body text", _read(os.path.join(
            self.kdir, "skills-proposed", "alpha-one", "SKILL.md")))

    def test_the_ledger_line_is_sorted_so_it_is_byte_stable(self):
        self._run(run="run-1", worklog_line="w.md:1")
        line = _read(os.path.join(self.kdir, "skills-proposed", "alpha-one",
                                  "references.jsonl")).strip()
        self.assertEqual(list(json.loads(line)),
                         sorted(json.loads(line)))


class ExportSurfaceTest(unittest.TestCase):
    """The wave's new helpers are deliberately ABSENT from the facade surface."""

    def test_exports_are_exactly_the_six_shipped_names(self):
        self.assertEqual(author._EXPORTS, [
            "_SLUG_RE", "_valid_proposal_slug", "_slug_token_set",
            "near_duplicate_proposal", "_render_proposal_skill_md",
            "cmd_skill_propose"])

    def test_the_new_helpers_exist_but_are_not_exported(self):
        for name in ("_propose_dedup_guard", "_propose_ledger_event",
                     "_propose_emit"):
            self.assertTrue(hasattr(author, name), name)
            self.assertNotIn(name, author._EXPORTS)


if __name__ == "__main__":
    unittest.main()
