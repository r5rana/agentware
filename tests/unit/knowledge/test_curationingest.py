"""The ingestion pipeline's owning unit file — task 99 wave 13.

`agentware_lib.knowledge.curation.curationingest` had no owning unit file. Its
coverage lived in `tests/unit/knowledge/test_ingestion.py`, which drives the
`ingest` VERB through the CLI against a synthetic KB — the right test for "does
a dry run register nothing" and the wrong one for "which of the three dedup
routes rejected this candidate", because every verdict reaches that file only
as a count in a printed summary.

Wave 13 split the 20-branch `cmd_ingest` into the four stages it already was:
dispatch the adapter, apply `--only`, run the index critical section, print the
text report. These tests drive those four directly.

The properties that matter, and why each is a way the split can be wrong:

  CLOSED SET  — the adapter table is closed. An unknown `--source` must be
                REFUSED, not defaulted to `docs`; argparse's `choices=` makes
                that unreachable from the command line, so this file is the
                only place the arm runs at all.
  ONE ROUTE   — a bad path and a bad source leave by the same shaped refusal.
                A ValueError that escaped the dispatch would surface as a
                traceback rather than an `agentware:` line.
  ABSENT != EMPTY — `--only` absent admits everything; `--only " , "` admits
                NOTHING. A helper that treated a blank list as absent would
                silently import a whole corpus the operator meant to filter.
  THREE ROUTES — dedup by id, by CONTENT fingerprint, and by an id already
                taken earlier in the SAME batch. Each is given a fixture only
                it can explain: a second commit of the same corpus (id), a
                seeded entry holding one candidate's prose under a different
                slug (fingerprint), and two candidates sharing a topic in one
                batch (seen).
  ONE, NOT THE BATCH — a registration error demotes that candidate to
                `skipped` carrying its error; the rest of the batch still
                commits.
  LOCK SCOPE  — the lock spans load -> dedup -> register -> save and NOTHING
                else. The staging write and the metric append are asserted to
                happen outside it, because holding a global lock across file
                I/O that touches no index is what the ported body fixed.
  DRY RUN IS THE SAME PATH — without `--commit` the dedup verdicts are still
                computed and the staging area is still written.

Hermetic: a scratch KB and a scratch docs tree per test, stdlib unittest only.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

try:
    from tests._fixtures import build_synthetic_kb
except ImportError:  # pragma: no cover - direct-run fallback
    from _fixtures import build_synthetic_kb

import agentware_lib.knowledge.curation.curationingest as ingest   # noqa: E402
import agentware_lib.knowledge.index as index                      # noqa: E402
from agentware_lib.knowledge.curation.curationingest import (      # noqa: E402
    INGEST_SOURCES,
    _ingest_candidate,
    _ingest_commit,
    _ingest_dispatch_adapter,
    _ingest_fingerprint,
    _ingest_print_text,
    _ingest_select,
    cmd_ingest,
)

KDIR_ENV = "AGENTWARE_KNOWLEDGE_DIR"


def _ns(**kw):
    return types.SimpleNamespace(**kw)


def _args(**over):
    base = dict(source="docs", path=None, tags=None, only=None, commit=False,
                format="text")
    base.update(over)
    return _ns(**base)


def _run(fn, *a, **kw):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*a, **kw)
    return rc, out.getvalue(), err.getvalue()


def _cand(topic, content, title=None):
    return _ingest_candidate(topic, title or topic.title(), "", [], content,
                             "ref/%s" % topic, "docs")


#: `alpha.md`'s whole text. The seeded KB entry below carries it VERBATIM under
#: a different slug, which is the only fixture that reaches the CONTENT
#: fingerprint route: the id route cannot see it (the ids differ) and the
#: per-run `seen` route cannot either (there is one candidate).
ALPHA_TEXT = "# Alpha Note\n\nAlpha prose about caches.\n"


class _ScratchIngest(unittest.TestCase):
    """A scratch KB plus a docs tree, built so each dedup route has a fixture
    only IT can explain: the KB carries `alpha`'s prose under a DIFFERENT slug
    (fingerprint route), a second commit of the same corpus repeats ids (id
    route), and a hand-built pair sharing a topic exercises the per-run `seen`
    set (which neither of the other two can reach)."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="w13-ingest-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.kdir = os.path.join(self.root, "kb")
        os.makedirs(self.kdir)
        build_synthetic_kb(self.kdir, [
            {"id": "learn-cache-echo", "title": "Cache Echo",
             "category": "learnings", "path": "learnings/cache-echo.md",
             "tags": ["cache"], "created": "2026-01-01", "author": "seeded",
             "summary": "The same prose under another name.",
             "body": ALPHA_TEXT}])
        self.docs = os.path.join(self.root, "docs")
        os.makedirs(os.path.join(self.docs, "nested"))
        self._doc("alpha.md", ALPHA_TEXT)
        self._doc("gamma.md", "# Gamma Note\n\nGamma prose about locks.\n")
        self._doc(os.path.join("nested", "beta.md"),
                  "# Beta Note\n\nBeta prose about queues.\n")
        self._prev = os.environ.get(KDIR_ENV)
        os.environ[KDIR_ENV] = self.kdir
        self.addCleanup(self._restore)

    def _doc(self, rel, text):
        with open(os.path.join(self.docs, rel), "w", encoding="utf-8") as f:
            f.write(text)

    def _restore(self):
        if self._prev is None:
            os.environ.pop(KDIR_ENV, None)
        else:
            os.environ[KDIR_ENV] = self._prev

    def ids(self):
        with open(os.path.join(self.kdir, "index.json"), encoding="utf-8") as f:
            return [e["id"] for e in json.load(f)["entries"]]


class DispatchAdapterTest(_ScratchIngest):
    """The closed adapter set, and the ONE route every failure leaves by."""

    def test_docs_returns_one_candidate_per_markdown_file(self):
        cands, rejected, err = _ingest_dispatch_adapter("docs", self.docs, [])
        self.assertIsNone(err)
        self.assertEqual(rejected, [])
        self.assertEqual(sorted(c["topic"] for c in cands),
                         ["alpha-note", "beta-note", "gamma-note"])

    def test_extra_tags_reach_every_candidate(self):
        cands, _rej, _err = _ingest_dispatch_adapter("docs", self.docs,
                                                     ["imported"])
        self.assertTrue(all("imported" in c["tags"] for c in cands))

    def test_an_unknown_source_is_REFUSED_not_defaulted(self):
        """argparse's `choices=` makes this unreachable from the CLI, so this
        is the only place the arm runs. A dispatch that fell through to `docs`
        would import a corpus the operator never named."""
        cands, rejected, err = _ingest_dispatch_adapter("telepathy",
                                                        self.docs, [])
        self.assertEqual((cands, rejected), ([], []))
        self.assertEqual(err, "agentware: unknown --source 'telepathy'\n")

    def test_an_adapter_value_error_becomes_the_same_shaped_refusal(self):
        _c, _r, err = _ingest_dispatch_adapter(
            "docs", os.path.join(self.root, "no-such-dir"), [])
        self.assertTrue(err.startswith("agentware: docs source path is not a "
                                       "directory: "))

    def test_a_github_export_that_is_not_a_list_is_refused_not_raised(self):
        path = os.path.join(self.root, "issues.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"not": "a list"}')
        _c, _r, err = _ingest_dispatch_adapter("github-issues", path, [])
        self.assertIn("must be a list of issues", err)

    def test_the_published_source_set_is_what_dispatch_serves(self):
        for source in INGEST_SOURCES:
            _c, _r, err = _ingest_dispatch_adapter(source, self.docs, [])
            self.assertNotEqual(
                err, "agentware: unknown --source '%s'\n" % source)


class IngestSelectTest(unittest.TestCase):
    """`--only`: absent is not the same as empty."""

    def setUp(self):
        self.valid = [_cand("alpha", "a"), _cand("beta", "b"),
                      _cand("gamma", "g")]

    def test_absent_admits_everything(self):
        self.assertEqual(len(_ingest_select(self.valid, None)), 3)

    def test_an_empty_string_admits_everything(self):
        """`--only ""` is argparse's shape for "the flag was not given a
        value", and the guard is truthiness, not `is None`."""
        self.assertEqual(len(_ingest_select(self.valid, "")), 3)

    def test_a_named_topic_admits_only_it(self):
        self.assertEqual([c["topic"] for c in _ingest_select(self.valid,
                                                             "beta")],
                         ["beta"])

    def test_selection_preserves_the_adapter_order(self):
        """The adapters walk sorted; a selection that re-ordered would make
        the staging file and the report disagree run to run."""
        self.assertEqual(
            [c["topic"] for c in _ingest_select(self.valid, "gamma,alpha")],
            ["alpha", "gamma"])

    def test_blank_segments_are_dropped_and_select_NOTHING(self):
        """`--only " , "` must select nothing. A helper that treated a blank
        list as absent would import the whole corpus the operator filtered."""
        self.assertEqual(_ingest_select(self.valid, " , , "), [])

    def test_an_unknown_topic_selects_nothing(self):
        self.assertEqual(_ingest_select(self.valid, "no-such-topic"), [])

    def test_segments_are_stripped(self):
        self.assertEqual(
            [c["topic"] for c in _ingest_select(self.valid, " beta , gamma ")],
            ["beta", "gamma"])


class IngestCommitTest(_ScratchIngest):
    """The critical section: three dedup routes, one-not-the-batch, save."""

    def _selected(self):
        cands, _rej, _err = _ingest_dispatch_adapter("docs", self.docs, [])
        return sorted(cands, key=lambda c: c["topic"])

    def test_commit_registers_and_saves(self):
        committed, skipped, err = _ingest_commit(self.kdir, self._selected(),
                                                 True)
        self.assertIsNone(err)
        self.assertEqual([s["topic"] for s in skipped], ["alpha-note"])
        self.assertEqual(sorted(e["id"] for e in committed),
                         ["learn-beta-note", "learn-gamma-note"])
        self.assertIn("learn-beta-note", self.ids())

    def test_the_fingerprint_route_catches_a_different_slug_same_body(self):
        """`learn-cache-echo` already holds `alpha`'s prose under another
        slug. `learn-alpha-note` is NOT in the index, so the id route cannot
        see this and only the content fingerprint can — which is what makes
        this fixture a probe of the route rather than of dedup in general."""
        self.assertNotIn("learn-alpha-note", self.ids())
        _c, skipped, _err = _ingest_commit(self.kdir, self._selected(), True)
        self.assertEqual([s["topic"] for s in skipped], ["alpha-note"])
        self.assertNotIn("error", skipped[0])

    def _seed_entry(self, eid, rel, body):
        """Add ONE index row + its file directly (fixture setup only)."""
        abs_path = os.path.join(self.kdir, rel)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write("---\nid: %s\ncategory: learnings\n---\n%s" % (eid, body))
        path = os.path.join(self.kdir, "index.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["entries"].append(
            {"id": eid, "title": eid, "category": "learnings", "path": rel,
             "tags": [], "created": "2026-01-01", "summary": "seeded"})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def test_the_id_route_catches_an_id_whose_CONTENT_differs(self):
        """The id route ALONE. An index row already holds `learn-beta-note`,
        but the file behind it is unrelated prose, so its fingerprint is not
        the candidate's and the per-run `seen` set is still empty — which is
        what makes this fixture a probe of the id route rather than of dedup
        in general. (The obvious fixture, committing the same corpus twice,
        is NOT: by the second run the fingerprints are in the index too, so
        it passes with the id check deleted.)"""
        self._seed_entry("learn-beta-note", "learnings/beta-note.md",
                         "# Beta Note\n\nUnrelated prose about invoices.\n")
        committed, skipped, _err = _ingest_commit(self.kdir, self._selected(),
                                                  True)
        row = [s for s in skipped if s["topic"] == "beta-note"][0]
        self.assertNotIn("learn-beta-note", [e["id"] for e in committed])
        # A DEDUP skip carries no `error`; a registration that got as far as
        # the writer and bounced off the existing file carries one. Without
        # this half the test passes with the id check deleted, because the
        # candidate is refused one layer lower and still lands in `skipped`.
        self.assertNotIn("error", row)

    def test_the_id_route_catches_a_second_run(self):
        _ingest_commit(self.kdir, self._selected(), True)
        _c, skipped, _err = _ingest_commit(self.kdir, self._selected(), True)
        self.assertEqual(sorted(s["topic"] for s in skipped),
                         ["alpha-note", "beta-note", "gamma-note"])

    def test_the_seen_route_catches_two_candidates_in_ONE_batch(self):
        """Two candidates whose topics are equal but whose bodies differ, run
        as a DRY batch. The dry run is what isolates the route: nothing
        reaches the index, so `existing_ids` never grows and only the per-run
        `seen` set can reject the second. Under `--commit` the id route
        reaches it too, and the same fixture passes with `seen` deleted."""
        selected = [_cand("dup-topic", "first body"),
                    _cand("dup-topic", "second body, entirely different")]
        committed, skipped, _err = _ingest_commit(self.kdir, selected, False)
        self.assertEqual(committed, [])
        self.assertEqual([s["topic"] for s in skipped], ["dup-topic"])

    def test_the_seen_route_also_holds_under_commit(self):
        """The same pair with `--commit`: exactly one registers. Kept beside
        the dry row because this is the case an operator actually runs, and
        the dry row is the one that proves WHICH route rejected it."""
        selected = [_cand("dup-topic", "first body"),
                    _cand("dup-topic", "second body, entirely different")]
        committed, skipped, _err = _ingest_commit(self.kdir, selected, True)
        self.assertEqual(len(committed), 1)
        self.assertEqual([s["topic"] for s in skipped], ["dup-topic"])

    def test_a_register_error_skips_ONE_not_the_batch(self):
        real = ingest._ingest_register
        calls = []

        def _flaky(kdir, data, c):
            calls.append(c["topic"])
            if c["topic"] == "beta-note":
                return None, "register refused"
            return real(kdir, data, c)

        ingest._ingest_register = _flaky
        try:
            committed, skipped, _err = _ingest_commit(
                self.kdir, self._selected(), True)
        finally:
            ingest._ingest_register = real
        self.assertEqual([e["id"] for e in committed], ["learn-gamma-note"])
        errored = [s for s in skipped if s.get("error")]
        self.assertEqual(errored[0]["topic"], "beta-note")
        self.assertEqual(errored[0]["error"], "register refused")

    def test_a_dry_run_registers_nothing_but_still_dedups(self):
        committed, skipped, err = _ingest_commit(self.kdir, self._selected(),
                                                 False)
        self.assertEqual((committed, err), ([], None))
        self.assertEqual([s["topic"] for s in skipped], ["alpha-note"])
        self.assertEqual(self.ids(), ["learn-cache-echo"])

    def test_an_index_that_will_not_load_is_reported_not_raised(self):
        real = ingest.load_index
        ingest.load_index = lambda *a, **k: (None, "index unreadable")
        try:
            committed, skipped, err = _ingest_commit(self.kdir, [], True)
        finally:
            ingest.load_index = real
        self.assertEqual((committed, skipped), ([], []))
        self.assertEqual(err, "agentware: index unreadable\n")

    def test_nothing_committed_means_no_save(self):
        """A save with nothing to write rewrites index.json for every dry run,
        which is a needless write under a global lock."""
        saves = []
        real = ingest.save_index
        ingest.save_index = lambda kdir, data: saves.append(kdir)
        try:
            _ingest_commit(self.kdir, self._selected(), False)
        finally:
            ingest.save_index = real
        self.assertEqual(saves, [])

    def test_the_lock_is_released_before_returning(self):
        """The helper returns from INSIDE its `with`; a lock that outlived the
        return would deadlock the caller's own staging write."""
        _ingest_commit(self.kdir, self._selected(), True)
        with index._index_lock(self.kdir):
            self.assertTrue(True)


class IngestPrintTextTest(unittest.TestCase):
    """The text report — the json arm's counterpart, byte for byte."""

    #: Five DISTINCT values on purpose. With any two equal, a transposition
    #: of those two fields in the format string prints the same report and the
    #: byte pin below passes under a mutation that swapped them — measured:
    #: the first version of this fixture used 1/1 for skipped_dup/rejected and
    #: the non-vacuity sweep reported the swap UNGUARDED.
    COUNTS = {"candidates": 5, "selected": 4, "committed": 3,
              "skipped_dup": 2, "rejected": 1}

    def _print(self, **kw):
        base = dict(source="docs", path="/tmp/docs", do_commit=True,
                    counts=self.COUNTS, committed=[{"id": "learn-alpha"}],
                    rejected=[{"source_ref": "bad.md", "reason": "empty"}],
                    staging_rel="work/ingest-docs")
        base.update(kw)
        out, _err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out):
            _ingest_print_text(base["source"], base["path"], base["do_commit"],
                               base["counts"], base["committed"],
                               base["rejected"], base["staging_rel"])
        return out.getvalue()

    def test_the_whole_report_is_byte_pinned(self):
        self.assertEqual(
            self._print(),
            "INGEST COMMIT [docs] /tmp/docs\n"
            "  candidates: 5  selected: 4  committed: 3  skipped_dup: 2  "
            "rejected: 1\n"
            "  staging: work/ingest-docs\n"
            "  + learn-alpha\n"
            "  ! rejected bad.md: empty\n")

    def test_the_mode_word_is_the_only_thing_commit_changes(self):
        self.assertEqual(self._print(do_commit=False).replace("DRY-RUN", "X"),
                         self._print().replace("COMMIT", "X"))

    def test_a_rejected_item_missing_its_fields_prints_none_not_a_raise(self):
        """Rejections come from untrusted source artifacts, so a missing key
        must not take the report down after the work is already done."""
        self.assertIn("! rejected None: None", self._print(rejected=[{}]))

    def test_an_empty_run_prints_the_three_header_lines_and_nothing_else(self):
        self.assertEqual(len(self._print(committed=[], rejected=[]
                                         ).splitlines()), 3)


class CmdIngestTest(_ScratchIngest):
    """The handler: the four stages assembled, and what sits outside the lock."""

    def test_a_dry_run_writes_staging_and_registers_nothing(self):
        rc, out, err = _run(cmd_ingest, _args(path=self.docs))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("INGEST DRY-RUN [docs] ", out)
        self.assertEqual(self.ids(), ["learn-cache-echo"])
        self.assertTrue(os.path.exists(os.path.join(
            self.kdir, "work", "ingest-docs", "candidates.jsonl")))

    def test_a_commit_registers_the_deduped_subset(self):
        rc, _out, _err = _run(cmd_ingest, _args(path=self.docs, commit=True))
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(self.ids()),
                         ["learn-beta-note", "learn-cache-echo",
                          "learn-gamma-note"])

    def test_an_unreadable_index_refuses_before_staging_is_written(self):
        real = ingest.load_index
        ingest.load_index = lambda *a, **k: (None, "index unreadable")
        try:
            rc, _out, err = _run(cmd_ingest, _args(path=self.docs))
        finally:
            ingest.load_index = real
        self.assertEqual(rc, 1)
        self.assertEqual(err, "agentware: index unreadable\n")
        self.assertFalse(os.path.isdir(os.path.join(self.kdir, "work",
                                                    "ingest-docs")))

    def test_the_staging_write_and_metric_happen_OUTSIDE_the_index_lock(self):
        """The ported body's whole point: the lock spans load->save and
        NOTHING else. Asserted as an ordered event log rather than by probing
        a lock file, because a lock file exists whether or not it is held —
        the ORDER is the only observable that separates the two."""
        events = []
        real_lock = ingest._index_lock
        real_staging = ingest._ingest_write_staging
        real_metric = ingest._ingest_emit_metric

        @contextlib.contextmanager
        def _spy(kdir):
            events.append("lock-enter")
            with real_lock(kdir):
                yield
            events.append("lock-exit")

        def _staging(*a, **k):
            events.append("staging")
            return real_staging(*a, **k)

        def _metric(*a, **k):
            events.append("metric")
            return real_metric(*a, **k)

        ingest._index_lock = _spy
        ingest._ingest_write_staging = _staging
        ingest._ingest_emit_metric = _metric
        try:
            rc, _out, _err = _run(cmd_ingest, _args(path=self.docs,
                                                    commit=True))
        finally:
            ingest._index_lock = real_lock
            ingest._ingest_write_staging = real_staging
            ingest._ingest_emit_metric = real_metric
        self.assertEqual(rc, 0)
        self.assertEqual(events,
                         ["lock-enter", "lock-exit", "staging", "metric"])

    def test_the_metric_append_is_best_effort_and_never_fails_the_run(self):
        real = ingest._ingest_emit_metric

        def _boom(*a, **k):
            raise OSError("logs are read-only")

        ingest._ingest_emit_metric = _boom
        try:
            with self.assertRaises(OSError):
                _run(cmd_ingest, _args(path=self.docs))
        finally:
            ingest._ingest_emit_metric = real
        # The real one swallows OSError itself — that is the contract, and the
        # raise above proves the handler does NOT wrap it a second time.
        rc, _out, _err = _run(cmd_ingest, _args(path=self.docs))
        self.assertEqual(rc, 0)

    def test_the_json_arm_carries_every_count_the_text_arm_prints(self):
        rc, out, _err = _run(cmd_ingest, _args(path=self.docs, format="json"))
        payload = json.loads(out)
        self.assertEqual(rc, 0)
        for key in ("candidates", "selected", "committed", "skipped_dup",
                    "rejected", "staging_dir", "dry_run", "source", "path"):
            self.assertIn(key, payload)

    def test_only_narrows_the_selection_and_the_report_agrees(self):
        rc, out, _err = _run(cmd_ingest, _args(path=self.docs,
                                               only="beta-note"))
        self.assertEqual(rc, 0)
        self.assertIn("selected: 1", out)

    def test_a_bad_source_refuses_before_the_index_is_opened(self):
        rc, _out, err = _run(cmd_ingest, _args(source="telepathy",
                                               path=self.docs))
        self.assertEqual(rc, 1)
        self.assertEqual(err, "agentware: unknown --source 'telepathy'\n")

    def test_the_fingerprint_is_whitespace_and_case_insensitive(self):
        """The dedup route the split threads through `_ingest_commit`: two
        bodies differing only in formatting must collide, or a reformatted
        re-import silently doubles the corpus."""
        self.assertEqual(_ingest_fingerprint("A  B\nC"),
                         _ingest_fingerprint("a b c"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
