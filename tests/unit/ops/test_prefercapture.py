"""The capture write path's own unit file — task 99 wave 2.

`agentware_lib.ops.prefercapture` had no owning unit file. Its behaviour is
covered by `test_prefer_capture.py`, `test_prefer_scope.py`,
`test_prefer_forget.py` and friends, all of which drive the verb end to end
through the CLI. Those are the right tests for "does capturing write a
source=user entry" and the wrong ones for "which of the five phases refused" —
the verb returns 1 for a kernel path, a gate-loosening text, a both-arguments
mistake and an unresolvable workorder alike.

Wave 2 split `_prefer_capture` (31 branches / 128 lines) into five phases, and
`_prefer_delete_entry` (18) into its authorization predicate plus its archiver.
Each is asserted here on concrete values.

The properties that matter, and how each can be got wrong:

  ORDER       — the two REFUSALS run BEFORE the noise bar, so a kernel-path text
                with no preference cue still exits NONZERO rather than being
                swallowed as "nothing captured".
  NOISE=0     — the noise bar exits 0 with zero writes; making it nonzero would
                turn every casual sentence into an error.
  TRUNCATION  — two distinct long preferences that share a 60-char slug prefix
                must NOT collapse onto one id: the exact-key supersede would
                delete the unrelated prior capture.
  AUTHZ       — `forget` deletes only a `preference`-tagged, `source=user` entry;
                the orphan fallback is bounded to the capture-id prefix so a real
                non-capture never becomes forgettable by deleting its file.
  SCOPE       — an explicit `--project` naming no KB project WARNS and still
                captures, because the alternative is a silently dead pref.
  FALLBACK    — an approved candidate archived out of the live queue still
                resolves, but an UNREADABLE queue is an error rather than a
                fallback.

Hermetic: pure functions over in-memory args and tempdirs; no CLI, no network.
"""

from tests._paths import TESTS_DIR

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.ops.prefercapture as capture  # noqa: E402
from agentware_lib.ops.prefercapture import (  # noqa: E402
    _capture_entry_id,
    _capture_refusals,
    _capture_resolve_text,
    _capture_scope,
    _capture_tags,
    _prefer_archive_md,
    _prefer_forgettable,
    _workorder_queue_hits,
)


def _args(**kw):
    base = {"text": None, "from_workorder": None, "scope_global": False,
            "project": None, "key": None, "tags": None, "format": "text"}
    base.update(kw)
    return argparse.Namespace(**base)


@contextlib.contextmanager
def _captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


class CaptureResolveTextTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_cap_text_")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def test_typed_text_is_stripped_and_returned(self):
        text, rc = _capture_resolve_text(_args(text="  always pin  "), self.kdir)
        self.assertEqual((text, rc), ("always pin", None))

    def test_neither_text_nor_workorder_is_refused(self):
        with _captured() as (_out, err):
            text, rc = _capture_resolve_text(_args(), self.kdir)
        self.assertEqual((text, rc), (None, 1))
        self.assertIn("requires <text> or --from-workorder", err.getvalue())

    def test_both_text_and_workorder_is_refused_rather_than_one_winning(self):
        """The operator meant two different captures; silently doing one of them
        is the failure."""
        with _captured() as (_out, err):
            text, rc = _capture_resolve_text(
                _args(text="always x", from_workorder="auto/pref-a"), self.kdir)
        self.assertEqual((text, rc), (None, 1))
        self.assertIn("either <text> or --from-workorder", err.getvalue())

    def test_an_unresolvable_workorder_reports_the_resolvers_reason(self):
        with mock.patch.object(capture, "_prefer_workorder_candidate",
                               return_value=(None, "not approved")):
            with _captured() as (_out, err):
                text, rc = _capture_resolve_text(
                    _args(from_workorder="auto/pref-a"), self.kdir)
        self.assertEqual((text, rc), (None, 1))
        self.assertIn("not approved", err.getvalue())

    def test_an_approved_candidate_supplies_its_text_and_its_project_scope(self):
        args = _args(from_workorder="auto/pref-a")
        with mock.patch.object(
                capture, "_prefer_workorder_candidate",
                return_value=({"text": " always pin ", "project": "tokto-io"},
                              None)):
            text, rc = _capture_resolve_text(args, self.kdir)
        self.assertEqual((text, rc), ("always pin", None))
        self.assertEqual(args.project, "tokto-io")
        self.assertFalse(args.scope_global)

    def test_a_globally_scoped_candidate_forces_global(self):
        args = _args(from_workorder="auto/pref-a")
        with mock.patch.object(capture, "_prefer_workorder_candidate",
                               return_value=({"text": "always pin",
                                              "project": None}, None)):
            _capture_resolve_text(args, self.kdir)
        self.assertTrue(args.scope_global)

    def test_an_explicit_scope_overrides_the_records_own(self):
        args = _args(from_workorder="auto/pref-a", project="chosen")
        with mock.patch.object(capture, "_prefer_workorder_candidate",
                               return_value=({"text": "always pin",
                                              "project": "recorded"}, None)):
            _capture_resolve_text(args, self.kdir)
        self.assertEqual(args.project, "chosen")


class CaptureRefusalsTest(unittest.TestCase):
    def test_a_kernel_path_is_refused_nonzero(self):
        with _captured() as (_out, err):
            rc = _capture_refusals("always edit scripts/agentware by hand")
        self.assertEqual(rc, 1)
        self.assertIn("kernel/package path", err.getvalue())

    def test_a_gate_loosening_preference_is_refused_nonzero(self):
        with _captured() as (_out, err):
            rc = _capture_refusals("always skip the release gate before shipping")
        self.assertEqual(rc, 1)
        self.assertIn("TIGHTEN but never LOOSEN", err.getvalue())

    def test_a_kernel_path_with_no_preference_cue_still_exits_nonzero(self):
        """ORDER IS THE CONTRACT: the refusals run BEFORE the noise bar, so this
        cannot be swallowed as 'not a durable preference' and exit 0."""
        with _captured() as (out, err):
            rc = _capture_refusals("edit .claude/settings.json")
        self.assertEqual(rc, 1)
        self.assertIn("kernel/package path", err.getvalue())
        self.assertEqual(out.getvalue(), "")

    def test_a_non_preference_exits_zero_with_no_write(self):
        with _captured() as (out, _err):
            rc = _capture_refusals("ok")
        self.assertEqual(rc, 0)
        self.assertIn("nothing captured", out.getvalue())

    def test_a_genuine_preference_passes_all_three_gates(self):
        self.assertIsNone(_capture_refusals("always pin dependency versions"))


class CaptureScopeTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_cap_scope_")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def test_global_wins_outright(self):
        self.assertIsNone(_capture_scope(self.kdir, _args(scope_global=True,
                                                          project="ignored")))

    def test_an_explicit_project_is_slugified(self):
        with mock.patch.object(capture, "_canonical_project_key",
                               return_value="tokto-io"):
            self.assertEqual(_capture_scope(self.kdir, _args(project="Tokto IO")),
                             "tokto-io")

    def test_an_unregistered_project_warns_but_still_captures(self):
        """A refusal would block an operator pre-scoping a project they are
        about to register; silence would leave a pref that never injects."""
        with mock.patch.object(capture, "_canonical_project_key",
                               return_value=None):
            with _captured() as (_out, err):
                proj = _capture_scope(self.kdir, _args(project="ghost"))
        self.assertEqual(proj, "ghost")
        self.assertIn("will NOT inject", err.getvalue())

    def test_with_no_flags_it_resolves_the_current_checkout(self):
        with mock.patch.object(capture, "_current_project_signal",
                               return_value="sig"), \
                mock.patch.object(capture, "_canonical_project_key",
                                  return_value="resolved"):
            self.assertEqual(_capture_scope(self.kdir, _args()), "resolved")


class CaptureEntryIdTest(unittest.TestCase):
    def test_a_short_preference_keeps_a_readable_id(self):
        topic, eid = _capture_entry_id(_args(), "always pin versions", None)
        self.assertEqual(topic, "pref-global-always-pin-versions")
        self.assertEqual(eid, "learn-pref-global-always-pin-versions")

    def test_the_project_scope_is_part_of_the_key(self):
        _topic, eid = _capture_entry_id(_args(), "always pin", "tokto-io")
        self.assertEqual(eid, "learn-pref-tokto-io-always-pin")

    def test_an_explicit_key_supersedes_by_key_not_by_text(self):
        _t1, a = _capture_entry_id(_args(key="the-key"), "text one", None)
        _t2, b = _capture_entry_id(_args(key="the-key"), "utterly other", None)
        self.assertEqual(a, b)

    def test_two_long_preferences_sharing_a_prefix_do_not_collapse(self):
        """Without the truncation digest the exact-key supersede would DELETE
        the unrelated prior capture — silent data loss, not a naming wart.

        The two texts are chosen so their SLUGS agree for the first 60
        characters; a pair that merely reads similar diverges before the cut and
        would pass with the digest removed (measured: it did).
        """
        shared = ("always verify the deterministic retrieval ladder before "
                  "escalating to ")
        _t1, a = _capture_entry_id(_args(), shared + "a public web search", None)
        _t2, b = _capture_entry_id(_args(), shared + "any external source", None)
        from agentware_lib.knowledge.curation import _slugify
        self.assertEqual(_slugify(shared + "a public web search")[:60],
                         _slugify(shared + "any external source")[:60],
                         "the fixture no longer constructs a slug collision")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("learn-pref-global-always-verify-the-"))

    def test_identical_long_text_still_supersedes_itself(self):
        long_text = "always " + "x" * 200
        _t1, a = _capture_entry_id(_args(), long_text, None)
        _t2, b = _capture_entry_id(_args(), long_text, None)
        self.assertEqual(a, b)

    def test_an_unsluggable_key_falls_back_to_a_stable_id(self):
        _topic, eid = _capture_entry_id(_args(), "!!!", None)
        self.assertEqual(eid, "learn-pref-global-pref")


class CaptureTagsTest(unittest.TestCase):
    def test_preference_always_leads(self):
        self.assertEqual(_capture_tags(_args(), None), ["preference"])

    def test_a_scoped_capture_carries_its_project_tag(self):
        self.assertEqual(_capture_tags(_args(), "tokto-io"),
                         ["preference", "project-tokto-io"])

    def test_extra_tags_are_appended_in_order(self):
        self.assertEqual(_capture_tags(_args(tags="git,hygiene"), None),
                         ["preference", "git", "hygiene"])

    def test_a_duplicate_extra_tag_is_not_repeated(self):
        self.assertEqual(_capture_tags(_args(tags="preference,git"), None),
                         ["preference", "git"])


class PreferForgettableTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_cap_forget_")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    def _entry(self, tags, path=None):
        e = {"id": "learn-pref-global-x", "tags": tags}
        if path:
            e["path"] = path
        return e

    def test_a_preference_tagged_source_user_entry_is_forgettable(self):
        with mock.patch.object(capture, "_pref_entry_is_user_source",
                               return_value=True):
            self.assertTrue(_prefer_forgettable(
                self.kdir, self._entry(["preference"]), "learn-pref-global-x"))

    def test_an_untagged_entry_is_never_forgettable(self):
        """`forget <any-id>` must not become a general delete verb."""
        with mock.patch.object(capture, "_pref_entry_is_user_source",
                               return_value=True):
            self.assertFalse(_prefer_forgettable(
                self.kdir, self._entry(["learning"]), "learn-test-1"))

    def test_a_non_user_source_entry_with_a_live_file_is_not_forgettable(self):
        path = os.path.join("learnings", "x.md")
        os.makedirs(os.path.join(self.kdir, "learnings"), exist_ok=True)
        with open(os.path.join(self.kdir, path), "w", encoding="utf-8") as f:
            f.write("body\n")
        with mock.patch.object(capture, "_pref_entry_is_user_source",
                               return_value=False):
            self.assertFalse(_prefer_forgettable(
                self.kdir, self._entry(["preference"], path),
                "learn-pref-global-x"))

    def test_an_orphaned_capture_stays_revocable_by_its_id_prefix(self):
        """A capture whose .md vanished cannot prove source=user from
        frontmatter; the verb must still be able to revoke its OWN capture."""
        with mock.patch.object(capture, "_pref_entry_is_user_source",
                               return_value=False):
            self.assertTrue(_prefer_forgettable(
                self.kdir, self._entry(["preference"], "learnings/gone.md"),
                "learn-pref-global-x"))

    def test_the_orphan_fallback_does_not_cover_a_foreign_id(self):
        with mock.patch.object(capture, "_pref_entry_is_user_source",
                               return_value=False):
            self.assertFalse(_prefer_forgettable(
                self.kdir, self._entry(["preference"], "learnings/gone.md"),
                "learn-something-else"))


class PreferArchiveMdTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_cap_arch_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        os.makedirs(os.path.join(self.kdir, "learnings"), exist_ok=True)
        self.rel = os.path.join("learnings", "pref-x.md")
        with open(os.path.join(self.kdir, self.rel), "w", encoding="utf-8") as f:
            f.write("BODY\n")
        self.archive = os.path.join(self.kdir, "logs", "steering", "forgotten")

    def test_it_copies_the_entry_before_it_is_unlinked(self):
        _prefer_archive_md(self.kdir, self.rel, self.archive)
        with open(os.path.join(self.archive, "pref-x.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "BODY\n")

    def test_no_archive_dir_means_no_copy_and_no_error(self):
        _prefer_archive_md(self.kdir, self.rel, None)
        self.assertFalse(os.path.isdir(self.archive))

    def test_a_missing_source_file_degrades_silently(self):
        """The archive makes forget reversible; failing to write it must not
        block the revocation the operator asked for."""
        _prefer_archive_md(self.kdir, os.path.join("learnings", "gone.md"),
                           self.archive)
        self.assertFalse(os.path.isfile(os.path.join(self.archive, "gone.md")))


class WorkorderQueueHitsTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_cap_hits_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.qpath = os.path.join(self.kdir, capture.PREFER_QUEUE_REL)
        os.makedirs(os.path.dirname(self.qpath), exist_ok=True)

    def _write_queue(self, recs):
        with open(self.qpath, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

    def _write_archive(self, recs):
        apath = os.path.join(self.kdir, capture.PREFER_QUEUE_ARCHIVE_REL)
        os.makedirs(os.path.dirname(apath), exist_ok=True)
        with open(apath, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

    def test_it_returns_the_live_records_for_a_workorder(self):
        self._write_queue([{"workorder": "auto/pref-a", "status": "approved"},
                           {"workorder": "auto/pref-b", "status": "proposed"}])
        hits, err = _workorder_queue_hits(self.kdir, "auto/pref-a")
        self.assertIsNone(err)
        self.assertEqual([h["status"] for h in hits], ["approved"])

    def test_an_archived_record_is_still_reachable(self):
        """`approved` is TERMINAL, so rotation may archive it between approve and
        the operator running the workorder."""
        self._write_queue([{"workorder": "auto/pref-b", "status": "queued"}])
        self._write_archive([{"workorder": "auto/pref-a", "status": "approved"}])
        hits, err = _workorder_queue_hits(self.kdir, "auto/pref-a")
        self.assertIsNone(err)
        self.assertEqual(len(hits), 1)

    def test_a_live_hit_is_not_shadowed_by_the_archive(self):
        self._write_queue([{"workorder": "auto/pref-a", "status": "proposed"}])
        self._write_archive([{"workorder": "auto/pref-a", "status": "approved"}])
        hits, _err = _workorder_queue_hits(self.kdir, "auto/pref-a")
        self.assertEqual([h["status"] for h in hits], ["proposed"])

    def test_an_unreadable_queue_is_an_error_not_an_empty_result(self):
        """Falling through to the archive would turn 'I could not read your
        queue' into 'you never approved this'."""
        self._write_queue([{"workorder": "auto/pref-a", "status": "approved"}])
        with mock.patch.object(capture, "_prefer_queue_records",
                               side_effect=OSError("denied")):
            hits, err = _workorder_queue_hits(self.kdir, "auto/pref-a")
        self.assertEqual(hits, [])
        self.assertEqual(err, "cannot read the prefer-queue")

    def test_no_queue_at_all_is_simply_no_hits(self):
        hits, err = _workorder_queue_hits(self.kdir, "auto/pref-a")
        self.assertEqual((hits, err), ([], None))


if __name__ == "__main__":
    unittest.main()
