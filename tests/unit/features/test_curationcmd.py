"""The learn/decide command surface's owning unit file — task 99 wave 13.

`agentware_lib.features.curationcmd` had no owning unit file. What coverage it
had came from `test_learn_hardening.py` and `test_decide_hardening.py`, which
drive the two verbs through the CLI facade to check the HARDENING added in an
earlier feature (slug normalization, the near-duplicate probe, the shared-write
gate). Those are the right tests for "does `learn` still refuse a restatement"
and the wrong ones for "what does each input contract decide": the four shared
contracts — the write prologue, the `--topic` grammar, the display-title rule
and the near-duplicate policy — are reachable only through a whole command, so
a defect in one reads as a `learn` failure and the reader has to guess which.

This file owns the four CONTRACTS plus the two input coercions that are
deliberately NOT shared; `test_curationcmd_handlers.py` owns the two handlers
end to end (one file, split at the 600-line cap along a class boundary, with
the fixture in `_curationcmd_support.py`).

The properties that matter, and why each is a way the wave-13 split can be
wrong:

  ONE GATE   — a kdir can only be obtained by passing the shared/ CR-gate, and
               the gate must see the CALLER's `--to-shared`. A prologue that
               hard-coded the flag would let it parse, be ignored, and write
               org-wide anyway — which is the exact defect the shared prologue
               was written to make unrepresentable.
  ASYMMETRY  — `learn` REFUSES a malformed `--author`; `decide` treats it as
               absent and falls back. Both are driven with the SAME handle, in
               the same file, because "let's share the author helper" is what
               these two tests exist to redden.
  DOC-SHAPED — the probe compares a candidate assembled like a corpus doc
               (title + summary + tags + body). Assembled any other way the
               ratio starts measuring the shape difference, so the probe text
               is pinned field by field and in order.
  THRESHOLD  — the near-duplicate policy is a threshold, so it is asserted from
               BOTH sides with the same corpus: a restatement is over it, an
               unrelated entry is under it, and `--force` skips the scan
               entirely rather than discarding its verdict.
"""

from tests._paths import TESTS_DIR                             # noqa: F401

import io
import os
import sys
import types
import unittest

try:
    from tests.unit.features._curationcmd_support import (
        RESTATEMENT, ScratchKB, decide_args, learn_args, run)
except ImportError:  # pragma: no cover - direct-run fallback
    from _curationcmd_support import (
        RESTATEMENT, ScratchKB, decide_args, learn_args, run)

import agentware_lib.features.curationcmd as curationcmd       # noqa: E402
import agentware_lib.ops.fleet as fleet                        # noqa: E402
from agentware_lib.knowledge.curation import _slugify          # noqa: E402
from agentware_lib.features.curationcmd import (               # noqa: E402
    LEARN_NEAR_DUP_THRESHOLD,
    SLUG_RE,
    _curation_title,
    _curation_topic,
    _decide_author,
    _entry_probe_text,
    _kdir_for_curation_write,
    _learn_body,
    _learn_provenance,
    _near_duplicate_entry,
    _near_duplicate_refusal,
    cmd_decide,
    cmd_learn,
)

KDIR_ENV = "AGENTWARE_KNOWLEDGE_DIR"


class KdirForCurationWriteTest(ScratchKB):
    """The ONE prologue: resolve, then gate. Never one without the other."""

    def test_it_returns_the_resolved_dir_when_the_gate_allows(self):
        self.assertEqual(_kdir_for_curation_write(learn_args()), self.kdir)

    def test_a_refused_shared_write_returns_none_and_names_the_reason(self):
        real = fleet.guard_shared_write
        fleet.guard_shared_write = lambda *a, **k: (False, "curator only")
        try:
            rc, _out, err = run(_kdir_for_curation_write,
                                learn_args(to_shared=True))
        finally:
            fleet.guard_shared_write = real
        self.assertIsNone(rc)
        self.assertEqual(err, "agentware: curator only\n")

    def test_the_gate_sees_the_callers_to_shared_flag(self):
        """A prologue that hard-coded `to_shared=False` would pass every gate,
        and `--to-shared` would parse, be ignored, and write org-wide while the
        help text said it could not."""
        seen = {}
        real = fleet.guard_shared_write

        def _spy(kdir, to_shared=False):
            seen["to_shared"] = to_shared
            return True, None

        fleet.guard_shared_write = _spy
        try:
            _kdir_for_curation_write(learn_args(to_shared=True))
        finally:
            fleet.guard_shared_write = real
        self.assertIs(seen["to_shared"], True)

    def test_an_args_object_with_no_to_shared_attribute_is_not_shared(self):
        """A missing attribute must mean "not shared", not an AttributeError
        raised mid-write by a handler that has already resolved a kdir."""
        seen = {}
        real = fleet.guard_shared_write

        def _spy(kdir, to_shared=False):
            seen["to_shared"] = to_shared
            return True, None

        fleet.guard_shared_write = _spy
        try:
            _kdir_for_curation_write(types.SimpleNamespace(topic="t"))
        finally:
            fleet.guard_shared_write = real
        self.assertIs(seen["to_shared"], False)

    def test_an_unresolvable_knowledge_dir_returns_none(self):
        os.environ[KDIR_ENV] = os.path.join(self.root, "no-such-kb")
        rc, _out, err = run(_kdir_for_curation_write, learn_args())
        self.assertIsNone(rc)
        self.assertIn("does not exist", err)

    def test_both_handlers_stop_at_the_prologue_and_write_nothing(self):
        before = sorted(self.ids())
        real = fleet.guard_shared_write
        fleet.guard_shared_write = lambda *a, **k: (False, "curator only")
        try:
            rc_l, _o, _e = run(cmd_learn, learn_args(to_shared=True))
            rc_d, _o, _e = run(cmd_decide, decide_args(to_shared=True))
        finally:
            fleet.guard_shared_write = real
        self.assertEqual((rc_l, rc_d), (1, 1))
        self.assertEqual(sorted(self.ids()), before)


class CurationTopicTest(unittest.TestCase):
    """The `--topic` grammar: one normalization for both verbs."""

    def test_an_already_slug_topic_passes_through_silently(self):
        rc, _out, err = run(_curation_topic, "already-a-slug")
        self.assertEqual((rc, err), ("already-a-slug", ""))

    def test_a_prose_topic_is_slugified_with_a_notice(self):
        rc, _out, err = run(_curation_topic, "iOS Signing Note!")
        self.assertEqual(rc, "ios-signing-note")
        self.assertEqual(
            err, "agentware: normalized --topic 'iOS Signing Note!' -> "
                 "'ios-signing-note'\n")

    def test_an_empty_topic_is_refused(self):
        rc, _out, err = run(_curation_topic, "")
        self.assertIsNone(rc)
        self.assertEqual(err, "agentware: --topic must not be empty\n")

    def test_a_topic_with_no_slug_safe_characters_is_refused(self):
        rc, _out, err = run(_curation_topic, "***")
        self.assertIsNone(rc)
        self.assertEqual(
            err, "agentware: --topic '***' has no slug-safe characters\n")

    def test_a_normalized_topic_is_itself_slug_safe(self):
        """The notice is only worth printing if what it announces is legal:
        an auto-slugified stem that still failed SLUG_RE would be written to
        disk as an id nobody can promote, which is the bug this grammar
        exists to prevent."""
        for raw in ("iOS Signing Note!", "Batch A/B rollout", "  spaced  out "):
            rc, _out, _err = run(_curation_topic, raw)
            self.assertTrue(SLUG_RE.match(rc), (raw, rc))

    def test_the_grammar_it_enforces_is_the_published_one(self):
        for good in ("a", "a-b", "0-9", "ios-signing-note"):
            self.assertTrue(SLUG_RE.match(good), good)
        for bad in ("-leading", "Upper", "has space", "trailing_"):
            self.assertFalse(SLUG_RE.match(bad), bad)


class CurationTitleTest(unittest.TestCase):
    """The display-title rule both verbs now share."""

    def test_the_title_derives_from_the_topic_it_is_given(self):
        self.assertEqual(_curation_title("ios-signing-note"),
                         "Ios Signing Note")

    def test_an_override_wins_and_is_stripped(self):
        self.assertEqual(_curation_title("a-topic", "  A Chosen Title  "),
                         "A Chosen Title")

    def test_a_blank_override_falls_through_to_the_derived_title(self):
        """An empty `--title` must not write an empty title; it means
        "derive"."""
        self.assertEqual(_curation_title("a-topic", "   "),
                         _curation_title("a-topic"))

    def test_no_override_is_the_same_as_none(self):
        self.assertEqual(_curation_title("a-topic", None),
                         _curation_title("a-topic"))

    def test_the_derived_title_is_INVARIANT_under_slugify(self):
        """Both handlers carry a comment saying the title derives from the
        ORIGINAL topic "so readability survives auto-slugify". Measured, that
        distinction is unobservable for every topic the grammar admits:
        `_topic_to_title` splits on the same non-alnum class `_slugify`
        replaces and capitalizes each word, so title(raw) == title(slug).
        Pinned as an equality rather than left as prose, so the day either
        function's character class changes, the comment stops being true out
        loud instead of quietly."""
        for raw in ("iOS Signing Note!", "Batch A/B rollout", "2026 Q1 plan",
                    "don't panic", "a  b", "Café note"):
            self.assertEqual(_curation_title(raw),
                             _curation_title(_slugify(raw)), raw)


class LearnBodyTest(unittest.TestCase):
    """`--content`, including the '-' stdin route."""

    def test_inline_content_is_returned_verbatim(self):
        self.assertEqual(_learn_body(learn_args(content="  Body.  ")),
                         ("  Body.  ", None))

    def test_a_dash_reads_stdin(self):
        prev = sys.stdin
        sys.stdin = io.StringIO("From the pipe.\n")
        try:
            body, err = _learn_body(learn_args(content="-"))
        finally:
            sys.stdin = prev
        self.assertEqual((body, err), ("From the pipe.\n", None))

    def test_empty_content_is_refused(self):
        body, err = _learn_body(learn_args(content="   "))
        self.assertIsNone(body)
        self.assertEqual(err, "agentware: --content must not be empty\n")

    def test_whitespace_only_stdin_is_refused_the_same_way(self):
        """The emptiness test is on the RESOLVED body, not on the flag: a
        check that ran before the stdin read would accept an empty pipe."""
        prev = sys.stdin
        sys.stdin = io.StringIO("\n  \n")
        try:
            body, err = _learn_body(learn_args(content="-"))
        finally:
            sys.stdin = prev
        self.assertIsNone(body)
        self.assertEqual(err, "agentware: --content must not be empty\n")

    def test_a_none_content_is_refused_rather_than_raising(self):
        self.assertIsNone(_learn_body(learn_args(content=None))[0])


class LearnProvenanceTest(unittest.TestCase):
    """`learn`'s (--source, --author): the REFUSING half of the asymmetry."""

    def test_omitting_the_source_yields_the_pre_provenance_default(self):
        src, _author, err = _learn_provenance(learn_args())
        self.assertEqual((src, err), ("agent", None))

    def test_a_source_is_normalized_case_and_whitespace_insensitively(self):
        src, _author, err = _learn_provenance(learn_args(source="  User  "))
        self.assertEqual((src, err), ("user", None))

    def test_an_unknown_source_is_refused_and_lists_the_legal_set(self):
        _src, _author, err = _learn_provenance(learn_args(source="hearsay"))
        self.assertTrue(
            err.startswith("agentware: invalid --source 'hearsay' (use "))
        self.assertIn("|", err)

    def test_a_refused_source_yields_no_author_either(self):
        """The refusal is terminal: returning a usable author beside an error
        invites a caller that prints the error and writes anyway."""
        src, author, err = _learn_provenance(learn_args(source="hearsay"))
        self.assertEqual((src, author), (None, None))
        self.assertIsNotNone(err)

    def test_an_author_is_normalized(self):
        _src, author, err = _learn_provenance(
            learn_args(author="  ada.lovelace "))
        self.assertEqual((author, err), ("ada.lovelace", None))

    def test_an_unusable_author_is_REFUSED(self):
        _src, author, err = _learn_provenance(learn_args(author="!!!"))
        self.assertIsNone(author)
        self.assertEqual(
            err, "agentware: invalid --author (must contain at least one of "
                 "A-Z a-z 0-9 . _ -)\n")

    def test_an_explicit_empty_author_is_refused_not_defaulted(self):
        _src, _author, err = _learn_provenance(learn_args(author=""))
        self.assertIsNotNone(err)

    def test_an_args_object_missing_both_flags_still_resolves(self):
        """Every call site reaches these through `getattr(..., None)`, so a
        namespace that predates the flags must not raise."""
        src, _author, err = _learn_provenance(
            types.SimpleNamespace())
        self.assertEqual((src, err), ("agent", None))


class DecideAuthorTest(unittest.TestCase):
    """`decide`'s author: the FALLING-BACK half of the same asymmetry."""

    def test_an_unusable_author_FALLS_BACK_rather_than_refusing(self):
        """The same handle `learn` refuses. The loop promotes `> DECISION:`
        markers unattended and must not lose one to a malformed handle."""
        self.assertEqual(_decide_author(decide_args(author="!!!")),
                         _decide_author(decide_args(author=None)))

    def test_a_usable_author_is_kept_and_normalized(self):
        self.assertEqual(_decide_author(decide_args(author=" ada ")), "ada")

    def test_the_two_verbs_disagree_on_purpose(self):
        """Pinned as ONE assertion so a future shared helper cannot satisfy
        both files by accident: same input, two dispositions."""
        _src, _author, learn_err = _learn_provenance(learn_args(author="!!!"))
        self.assertIsNotNone(learn_err)
        self.assertIsNotNone(_decide_author(decide_args(author="!!!")))


class EntryProbeTextTest(unittest.TestCase):
    """The candidate is shaped like a corpus doc, or the ratio measures shape."""

    def test_it_joins_title_summary_tags_and_body_in_that_order(self):
        self.assertEqual(_entry_probe_text("T", "S", ["a", "b"], "BODY"),
                         "T\nS\na b\nBODY")

    def test_a_missing_summary_becomes_an_empty_line_not_the_word_none(self):
        self.assertEqual(_entry_probe_text("T", None, [], "BODY"),
                         "T\n\n\nBODY")


class NearDuplicatePolicyTest(ScratchKB):
    """The probe answers "how close"; the policy answers "does that block"."""

    def test_a_restatement_scores_at_or_above_the_threshold(self):
        entry, ratio = _near_duplicate_entry(
            self.kdir, self.data, RESTATEMENT, "learnings")
        self.assertEqual(entry["id"], "learn-flask-vacuum-note")
        self.assertGreaterEqual(ratio, LEARN_NEAR_DUP_THRESHOLD)

    def test_the_probe_is_scoped_to_one_category(self):
        """A decision must be compared against decisions: comparing across
        categories measures the shared scaffold rather than the claim."""
        entry, _ratio = _near_duplicate_entry(
            self.kdir, self.data, RESTATEMENT, "decisions")
        self.assertEqual(entry["id"], "decide-flask-vacuum-choice")

    def test_an_empty_query_matches_nothing(self):
        self.assertEqual(
            _near_duplicate_entry(self.kdir, self.data, "   ", "learnings"),
            (None, 0.0))

    def test_an_empty_category_corpus_matches_nothing(self):
        self.assertEqual(
            _near_duplicate_entry(self.kdir, self.data, RESTATEMENT,
                                  "references"),
            (None, 0.0))

    def test_the_refusal_names_the_entry_and_its_overlap(self):
        msg = _near_duplicate_refusal(self.kdir, self.data, RESTATEMENT,
                                      "learnings", "learning")
        self.assertIn("near-duplicate of existing learning "
                      "'learn-flask-vacuum-note'", msg)
        self.assertIn("% overlap)", msg)
        self.assertIn("--force to record anyway", msg)

    def test_the_noun_is_the_callers(self):
        msg = _near_duplicate_refusal(self.kdir, self.data, RESTATEMENT,
                                      "decisions", "decision")
        self.assertIn("near-duplicate of existing decision ", msg)

    def test_force_short_circuits_the_probe_entirely(self):
        """`--force` also means "do not pay for the corpus scan": a probe that
        ran and had its verdict discarded would be a full BM25 pass per write.
        The corpus builder is replaced with a tripwire that RAISES, so a
        short-circuit that stopped short-circuiting is an error, not a
        same-looking pass."""
        import agentware_lib.retrieval.corpus as corpus
        real = corpus.build_corpus

        def _tripwire(*a, **k):
            raise AssertionError("the probe ran under --force")

        corpus.build_corpus = _tripwire
        curationcmd.build_corpus = _tripwire
        try:
            self.assertIsNone(_near_duplicate_refusal(
                self.kdir, self.data, RESTATEMENT, "learnings", "learning",
                force=True))
        finally:
            corpus.build_corpus = real
            curationcmd.build_corpus = real

    def test_an_unrelated_entry_is_under_the_threshold_and_allowed(self):
        self.assertIsNone(_near_duplicate_refusal(
            self.kdir, self.data,
            "Quarterly invoicing runs on the fifth business day.",
            "learnings", "learning"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
