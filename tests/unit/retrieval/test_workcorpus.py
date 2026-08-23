"""Owning unit file for `agentware_lib.retrieval.workcorpus` — task 99 wave 9.

The module had no owning file: its behaviour was observed only through
`recall --scope work`, which sees a RANKED LIST and none of the decisions that
build the corpus underneath it. The degradation paths in particular — an
unreadable `auto/` container, a body that cannot be decoded, a plan whose status
could not be read — are unreachable from a CLI run against a healthy tree, which
is why the seventeen-branch build had no test that could tell a skip from a
crash.

What is asserted here is the module's PROMISE:

  * EPHEMERAL, NEVER INDEXED. Work documents are ranked on demand and produce
    pseudo-entries with the shape of index entries; nothing here writes to
    index.json.
  * SIBLINGS INHERIT. The plan status is a FEATURE fact, read once from
    `plan.md` and stamped onto every document beside it. A worklog is not
    separately "done"; it belongs to a feature that is.
  * DETERMINISM (INV-1). `created` comes from the slug's YYMMDD prefix and never
    from filesystem mtime — a clone rewrites every mtime — and both directory
    levels are walked in sorted order.
  * DEGRADE, NEVER FAIL. Every I/O refusal skips exactly one unit of work and
    keeps the rest: a partial corpus beats no recall at all on a read-only path.
  * THE CACHE IS THE POINT. A warm build re-reads ZERO bodies, which is asserted
    by counting reads through the module's single read seam rather than by
    timing anything.

Stdlib-only, hermetic: every tree is a fresh `mkdtemp`, no KB is touched.
"""

from tests._paths import TESTS_DIR

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

import agentware_lib.retrieval.workcorpus as workcorpus  # noqa: E402


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class SeamRestoringTest(unittest.TestCase):
    """Base for every class here: names the ONE module-level seam this suite
    injects into, and restores it in `tearDown` rather than in a per-test
    cleanup registered AFTER the patch — which would capture the already-patched
    value and reinstall the mutant for every later test in the process."""

    _SEAMS = ("_read_work_text",)

    def setUp(self):
        self._saved = {n: getattr(workcorpus, n) for n in self._SEAMS}
        self.root = tempfile.mkdtemp(prefix="w9-workcorpus-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def tearDown(self):
        for name, val in self._saved.items():
            setattr(workcorpus, name, val)

    def kb(self):
        """A work tree exercising every shape the walk must handle."""
        kdir = os.path.join(self.root, "kb")
        w = os.path.join(kdir, "work")
        _write(os.path.join(w, "260101-alpha", "plan.md"),
               "# Alpha plan\n\n> Status: done\n\nAlpha body.\n")
        _write(os.path.join(w, "260101-alpha", "worklog.md"),
               "# Alpha worklog\n\nAlpha notes.\n")
        _write(os.path.join(w, "260202-beta", "plan.md"),
               "# Beta plan\n\n> Status: superseded\n\nBeta body.\n")
        _write(os.path.join(w, "260303-gamma", "notes.md"), "no heading here\n")
        _write(os.path.join(w, "260303-gamma", "README.txt"), "not markdown\n")
        os.makedirs(os.path.join(w, "260303-gamma", "nested"), exist_ok=True)
        _write(os.path.join(w, "nodate-delta", "plan.md"), "# Delta\n\nbody\n")
        _write(os.path.join(w, "auto", "260404-epsilon", "plan.md"),
               "# Epsilon\n\n> Status: cancelled\n\nEpsilon body.\n")
        _write(os.path.join(w, "stray.md"), "# Stray\n\nloose file\n")
        return kdir


class EnumerateFeatureDirsTest(SeamRestoringTest):
    """`_enumerate_feature_dirs` — the directory-shape question."""

    def test_workcorpus_enumerates_every_feature_directory_sorted(self):
        """Sorted, so the corpus order is a property of the TREE rather than of
        the filesystem's iteration order (INV-1)."""
        got = workcorpus._enumerate_feature_dirs(
            os.path.join(self.kb(), "work"))
        self.assertEqual([fid for (fid, _p) in got],
                         ["260101-alpha", "260202-beta", "260303-gamma",
                          "auto/260404-epsilon", "nodate-delta"])

    def test_workcorpus_a_loose_file_under_work_is_not_a_feature(self):
        got = workcorpus._enumerate_feature_dirs(
            os.path.join(self.kb(), "work"))
        self.assertNotIn("stray.md", [fid for (fid, _p) in got])

    def test_workcorpus_auto_is_a_container_not_a_feature(self):
        """Without the descent, every autonomous run collapses into ONE giant
        pseudo-feature whose documents share a plan status none of them has."""
        got = dict(workcorpus._enumerate_feature_dirs(
            os.path.join(self.kb(), "work")))
        self.assertNotIn("auto", got)
        self.assertIn("auto/260404-epsilon", got)

    def test_workcorpus_auto_lane_ids_are_namespaced(self):
        """`auto/<slug>`, so an autonomous feature and a hand-run one with the
        same slug cannot collide in the ranked output."""
        got = dict(workcorpus._enumerate_feature_dirs(
            os.path.join(self.kb(), "work")))
        self.assertTrue(got["auto/260404-epsilon"].endswith(
            os.path.join("auto", "260404-epsilon")))

    def test_workcorpus_a_file_inside_auto_is_not_a_lane(self):
        kdir = self.kb()
        _write(os.path.join(kdir, "work", "auto", "notes.md"), "loose\n")
        got = dict(workcorpus._enumerate_feature_dirs(
            os.path.join(kdir, "work")))
        self.assertNotIn("auto/notes.md", got)

    def test_workcorpus_unreadable_auto_container_does_not_lose_the_rest(self):
        """A read-only ranking path must degrade: an `auto/` that cannot be
        listed skips the lane, not the whole corpus."""
        kdir = self.kb()
        real = os.listdir

        def _boom(path):
            if os.path.basename(path) == "auto":
                raise OSError("refused")
            return real(path)

        os.listdir = _boom
        try:
            got = [fid for (fid, _p) in workcorpus._enumerate_feature_dirs(
                os.path.join(kdir, "work"))]
        finally:
            os.listdir = real
        self.assertEqual(got, ["260101-alpha", "260202-beta", "260303-gamma",
                               "nodate-delta"])

    def test_workcorpus_sorted_at_both_levels(self):
        kdir = self.kb()
        for slug in ("260909-zulu", "260808-yankee"):
            _write(os.path.join(kdir, "work", "auto", slug, "plan.md"), "# x\n")
        got = [fid for (fid, _p) in workcorpus._enumerate_feature_dirs(
            os.path.join(kdir, "work"))]
        auto = [f for f in got if f.startswith("auto/")]
        self.assertEqual(auto, sorted(auto))


class FeatureCreatedTest(SeamRestoringTest):
    """`_feature_created` — a clone-stable date, or none at all."""

    def test_workcorpus_created_comes_from_the_yymmdd_prefix(self):
        self.assertEqual(workcorpus._feature_created("260813-something"),
                         "2026-08-13")

    def test_workcorpus_a_slug_without_a_date_gets_no_date(self):
        """No date beats a GUESSED one: `created` is a ranking tie-break, and an
        invented value would order features by a fact nobody stated."""
        self.assertEqual(workcorpus._feature_created("nodate-delta"), "")

    def test_workcorpus_the_prefix_must_be_at_the_start(self):
        self.assertEqual(workcorpus._feature_created("x260813-late"), "")

    def test_workcorpus_a_short_numeric_prefix_is_not_a_date(self):
        self.assertEqual(workcorpus._feature_created("2608-short"), "")

    def test_workcorpus_a_longer_run_of_digits_takes_the_first_six(self):
        self.assertEqual(workcorpus._feature_created("26081399"), "2026-08-13")


class FeaturePlanStateTest(SeamRestoringTest):
    """`_feature_plan_state` — read once, per feature."""

    def _feature(self, plan_text=None):
        fpath = os.path.join(self.root, "feat")
        os.makedirs(fpath, exist_ok=True)
        if plan_text is not None:
            _write(os.path.join(fpath, "plan.md"), plan_text)
        return fpath

    def test_workcorpus_no_plan_means_the_default_state(self):
        self.assertEqual(workcorpus._feature_plan_state(self._feature()),
                         workcorpus._PLAN_DEFAULT_STATE)

    def test_workcorpus_a_declared_status_is_read(self):
        fpath = self._feature("# P\n\n> Status: superseded\n")
        self.assertEqual(workcorpus._feature_plan_state(fpath), "superseded")

    def test_workcorpus_an_unreadable_plan_is_not_evidence_of_done(self):
        """The two absent cases give the SAME answer on purpose: downweighting
        on a failed read would silently bury a live feature's documents."""
        fpath = self._feature("# P\n\n> Status: done\n")
        workcorpus._read_work_text = lambda _p: None
        self.assertEqual(workcorpus._feature_plan_state(fpath),
                         workcorpus._PLAN_DEFAULT_STATE)

    def test_workcorpus_a_plan_directory_is_not_a_plan_file(self):
        fpath = os.path.join(self.root, "featdir")
        os.makedirs(os.path.join(fpath, "plan.md"), exist_ok=True)
        self.assertEqual(workcorpus._feature_plan_state(fpath),
                         workcorpus._PLAN_DEFAULT_STATE)


class DocTitleTest(SeamRestoringTest):
    """`_doc_title` — the first heading, or nothing."""

    def test_workcorpus_title_is_the_first_heading_without_its_hashes(self):
        self.assertEqual(workcorpus._doc_title("## Deep heading\n\nbody\n"),
                         "Deep heading")

    def test_workcorpus_the_first_heading_wins(self):
        self.assertEqual(workcorpus._doc_title("# One\n\n# Two\n"), "One")

    def test_workcorpus_a_heading_after_prose_is_still_found(self):
        self.assertEqual(workcorpus._doc_title("intro\n\n# Later\n"), "Later")

    def test_workcorpus_an_indented_heading_counts(self):
        self.assertEqual(workcorpus._doc_title("   # Indented\n"), "Indented")

    def test_workcorpus_no_heading_means_no_title(self):
        """The corpus never synthesizes a title from the filename: `title` is a
        RANKED field, and an invented one would score."""
        self.assertEqual(workcorpus._doc_title("just prose\n"), "")


class FeatureDocsTest(SeamRestoringTest):
    """`_feature_docs` — one feature's rows."""

    def rows(self, feature_id="260101-alpha"):
        kdir = self.kb()
        fpath = os.path.join(kdir, "work", feature_id)
        return workcorpus._feature_docs(feature_id, fpath)

    def test_workcorpus_siblings_inherit_the_features_plan_status(self):
        """THE property: a worklog is not separately "done" — it belongs to a
        feature that is. A per-FILE status read would leave the worklog at the
        default while the plan carried `done`."""
        rows = self.rows()
        states = dict((e["id"], (e["status"], e["status_mult"]))
                      for (e, _t) in rows)
        self.assertEqual(states["260101-alpha/plan"], ("done", 0.7))
        self.assertEqual(states["260101-alpha/worklog"], ("done", 0.7))

    def test_workcorpus_pseudo_entries_have_the_index_entry_shape(self):
        entry, _text = self.rows()[0]
        self.assertEqual(
            sorted(entry),
            sorted(["id", "title", "summary", "tags", "path", "category",
                    "created", "status", "status_mult"]))
        self.assertEqual(entry["category"], "work")
        self.assertEqual(entry["tags"], ["work"])
        self.assertEqual(entry["summary"], "")

    def test_workcorpus_the_relative_path_is_kb_anchored(self):
        entry, _text = self.rows()[0]
        self.assertEqual(entry["path"], "work/260101-alpha/plan.md")

    def test_workcorpus_the_entry_id_drops_the_md_suffix(self):
        ids = [e["id"] for (e, _t) in self.rows()]
        self.assertEqual(ids, ["260101-alpha/plan", "260101-alpha/worklog"])

    def test_workcorpus_doc_text_is_the_title_then_a_blank_line_then_body(self):
        """The title is repeated INTO the document so BM25 weighs it twice — a
        deliberate boost that a refactor emitting the body alone would silently
        remove."""
        entry, text = self.rows()[0]
        self.assertTrue(text.startswith(entry["title"] + "\n\n"))
        self.assertIn("Alpha body.", text)

    def test_workcorpus_non_markdown_files_never_enter_the_corpus(self):
        rows = self.rows("260303-gamma")
        self.assertEqual([e["id"] for (e, _t) in rows],
                         ["260303-gamma/notes"])

    def test_workcorpus_a_directory_named_like_a_document_is_skipped(self):
        kdir = self.kb()
        os.makedirs(os.path.join(kdir, "work", "260202-beta", "sub.md"),
                    exist_ok=True)
        rows = workcorpus._feature_docs(
            "260202-beta", os.path.join(kdir, "work", "260202-beta"))
        self.assertEqual([e["id"] for (e, _t) in rows], ["260202-beta/plan"])

    def test_workcorpus_an_unreadable_body_skips_only_that_document(self):
        kdir = self.kb()
        real = workcorpus._read_work_text

        def _partial(path):
            if os.path.basename(path) == "worklog.md":
                return None
            return real(path)

        workcorpus._read_work_text = _partial
        rows = workcorpus._feature_docs(
            "260101-alpha", os.path.join(kdir, "work", "260101-alpha"))
        self.assertEqual([e["id"] for (e, _t) in rows], ["260101-alpha/plan"])

    def test_workcorpus_a_dateless_feature_yields_an_empty_created(self):
        rows = self.rows("nodate-delta")
        self.assertEqual(rows[0][0]["created"], "")

    def test_workcorpus_documents_are_walked_in_sorted_order(self):
        ids = [e["id"] for (e, _t) in self.rows()]
        self.assertEqual(ids, sorted(ids))


class BuildWorkCorpusUncachedTest(SeamRestoringTest):
    """`_build_work_corpus_uncached` — the composed walk."""

    def test_workcorpus_a_kb_without_work_yields_an_empty_corpus(self):
        bare = os.path.join(self.root, "bare")
        os.makedirs(bare, exist_ok=True)
        self.assertEqual(workcorpus._build_work_corpus_uncached(bare), [])

    def test_workcorpus_the_build_covers_every_feature_including_the_lane(self):
        got = [e["id"] for (e, _t)
               in workcorpus._build_work_corpus_uncached(self.kb())]
        self.assertEqual(got, ["260101-alpha/plan", "260101-alpha/worklog",
                               "260202-beta/plan", "260303-gamma/notes",
                               "auto/260404-epsilon/plan",
                               "nodate-delta/plan"])

    def test_workcorpus_every_body_is_read_through_the_one_seam(self):
        """The single read point is what lets the warm-cache claim be COUNTED
        rather than argued: a body read that bypassed it would be invisible."""
        kdir = self.kb()
        seen = []
        real = workcorpus._read_work_text

        def _counting(path):
            seen.append(path)
            return real(path)

        workcorpus._read_work_text = _counting
        workcorpus._build_work_corpus_uncached(kdir)
        self.assertTrue(seen)
        self.assertTrue(all(p.endswith(".md") for p in seen))

    def test_workcorpus_the_build_is_read_only(self):
        """INV-2: ranking never writes. The uncached build in particular must
        not create the cache — that is `build_work_corpus`'s job."""
        kdir = self.kb()
        before = sorted(os.listdir(kdir))
        workcorpus._build_work_corpus_uncached(kdir)
        self.assertEqual(sorted(os.listdir(kdir)), before)

    def test_workcorpus_repeated_builds_are_identical(self):
        kdir = self.kb()
        a = workcorpus._build_work_corpus_uncached(kdir)
        b = workcorpus._build_work_corpus_uncached(kdir)
        self.assertEqual(a, b)


class StatusMultiplierTest(SeamRestoringTest):
    """`_work_status_multiplier` — the plan-state weighting."""

    def test_workcorpus_finished_states_are_downweighted(self):
        self.assertEqual(workcorpus._work_status_multiplier("superseded"), 0.3)
        self.assertEqual(workcorpus._work_status_multiplier("cancelled"), 0.3)
        self.assertEqual(workcorpus._work_status_multiplier("done"), 0.7)

    def test_workcorpus_live_states_are_neutral(self):
        for state in ("draft", "ready", "running"):
            self.assertEqual(workcorpus._work_status_multiplier(state), 1.0)

    def test_workcorpus_an_unknown_or_missing_state_is_neutral(self):
        """Neutral, not punitive: a state the parser does not recognise is a
        parser gap, and downweighting on it would bury live work."""
        for state in ("", None, "unknown-state"):
            self.assertEqual(workcorpus._work_status_multiplier(state), 1.0)

    def test_workcorpus_state_matching_is_case_insensitive(self):
        self.assertEqual(workcorpus._work_status_multiplier("DONE"), 0.7)


class CacheTest(SeamRestoringTest):
    """The stat-digest cache: freshness without reading bodies."""

    def test_workcorpus_a_warm_build_reads_zero_bodies(self):
        """THE point of the cache, asserted by counting reads through the single
        seam. Keying on CONTENT would mean reading everything to find out
        whether you needed to read everything."""
        kdir = self.kb()
        workcorpus.build_work_corpus(kdir)
        seen = []
        real = workcorpus._read_work_text
        workcorpus._read_work_text = lambda p: (seen.append(p), real(p))[1]
        workcorpus.build_work_corpus(kdir)
        self.assertEqual(seen, [])

    def test_workcorpus_warm_and_cold_corpora_are_identical(self):
        kdir = self.kb()
        cold = workcorpus._build_work_corpus_uncached(kdir)
        warm = workcorpus.build_work_corpus(kdir)
        warm = workcorpus.build_work_corpus(kdir)
        self.assertEqual([list(r) for r in cold], [list(r) for r in warm])

    def test_workcorpus_an_edited_body_invalidates_the_cache(self):
        """The digest covers size and mtime, so an in-place edit that CHANGES
        the size is caught even inside the same second."""
        kdir = self.kb()
        workcorpus.build_work_corpus(kdir)
        _write(os.path.join(kdir, "work", "260202-beta", "plan.md"),
               "# Beta plan\n\n> Status: superseded\n\nBeta body, much longer "
               "than it was before this edit landed.\n")
        seen = []
        real = workcorpus._read_work_text
        workcorpus._read_work_text = lambda p: (seen.append(p), real(p))[1]
        workcorpus.build_work_corpus(kdir)
        self.assertTrue(seen)

    def test_workcorpus_a_version_bump_invalidates_the_cache(self):
        kdir = self.kb()
        workcorpus.build_work_corpus(kdir)
        digest = workcorpus._work_corpus_stat_digest(kdir)
        path = workcorpus._work_corpus_cache_path(kdir)
        with open(path, "r", encoding="utf-8") as f:
            blob = json.load(f)
        blob["version"] = workcorpus.WORK_CORPUS_CACHE_VERSION + 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f)
        self.assertIsNone(workcorpus._load_work_corpus_cache(kdir, digest))

    def test_workcorpus_a_digest_mismatch_invalidates_the_cache(self):
        kdir = self.kb()
        workcorpus.build_work_corpus(kdir)
        self.assertIsNone(
            workcorpus._load_work_corpus_cache(kdir, "not-the-digest"))

    def test_workcorpus_a_corrupt_cache_file_rebuilds_rather_than_raising(self):
        kdir = self.kb()
        workcorpus.build_work_corpus(kdir)
        _write(workcorpus._work_corpus_cache_path(kdir), "{not json")
        self.assertIsNone(workcorpus._load_work_corpus_cache(kdir, "x"))
        self.assertTrue(workcorpus.build_work_corpus(kdir))

    def test_workcorpus_a_malformed_row_invalidates_the_whole_cache(self):
        """A cache file is JSON on disk that anything may have rewritten; one
        bad row means the file cannot be trusted, not that one entry is
        skipped."""
        kdir = self.kb()
        workcorpus.build_work_corpus(kdir)
        digest = workcorpus._work_corpus_stat_digest(kdir)
        path = workcorpus._work_corpus_cache_path(kdir)
        with open(path, "r", encoding="utf-8") as f:
            blob = json.load(f)
        blob["corpus"][0] = ["not-a-dict", "text"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f)
        self.assertIsNone(workcorpus._load_work_corpus_cache(kdir, digest))

    def test_workcorpus_a_missing_cache_is_not_an_error(self):
        kdir = self.kb()
        self.assertIsNone(workcorpus._load_work_corpus_cache(kdir, "x"))

    def test_workcorpus_an_unwritable_cache_dir_does_not_break_recall(self):
        """Best-effort: a write failure just means the next call rebuilds, and
        correctness is intact."""
        kdir = self.kb()
        workcorpus._write_work_corpus_cache(kdir, "d", [({"id": "x"}, "t")])
        real_makedirs = os.makedirs

        def _boom(*_a, **_k):
            raise OSError("read-only")

        os.makedirs = _boom
        try:
            workcorpus._write_work_corpus_cache(kdir, "d2", [({"id": "y"}, "t")])
        finally:
            os.makedirs = real_makedirs
        self.assertTrue(workcorpus.build_work_corpus(kdir))

    def test_workcorpus_the_cache_lives_under_the_gitignored_cache_dir(self):
        """It is DERIVED and machine-local — which is what makes an mtime-based
        freshness key safe here and unsafe for the clone-surviving index."""
        self.assertTrue(
            workcorpus.WORK_CORPUS_CACHE_REL.startswith(".cache"))

    def test_workcorpus_the_digest_is_stable_and_order_independent(self):
        kdir = self.kb()
        self.assertEqual(workcorpus._work_corpus_stat_digest(kdir),
                         workcorpus._work_corpus_stat_digest(kdir))

    def test_workcorpus_read_work_text_returns_none_on_a_missing_file(self):
        self.assertIsNone(
            workcorpus._read_work_text(os.path.join(self.root, "nope.md")))


if __name__ == "__main__":
    unittest.main()
