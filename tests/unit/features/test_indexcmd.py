"""Owning unit file for `agentware_lib.features.indexcmd` (task 99 wave 15).

This module is the SIX `index` sub-command handlers — the orchestrating layer
between the CLI and `knowledge.index`, which owns the CRUD primitives. It had
coverage before this file existed and none of it was OWNERSHIP: the index is
exercised through feature-named suites that drive `scripts/agentware index ...`
end to end, so nothing in the corpus reddens when THIS module alone breaks while
the primitives beneath it stay correct.

The distinction matters most for the two things a handler — and only a handler —
decides: WHAT IS PERSISTED, and WHAT THE EXIT CODE IS. Both are asserted here
directly:

  * `_sync_apply` MUTATES only the in-memory index; `cmd_index_sync` persists it
    ONLY when something was added, so a run where every add failed leaves the
    index.json file untouched. Asserted by spying on `save_index`, not inferred
    from output.
  * A PARTIAL sync — some added, some failed — exits NON-ZERO. A caller that
    treated a partial sync as success would leave unindexed files behind and
    call the KB synced.
  * `_sync_prompt_row` never blocks on stdin when the run is not interactive
    (R-SHELL-01), and an EMPTY interactive answer accepts the derived value
    rather than writing an empty tag list.
  * `cmd_index_validate` STOPS at the first failing lint tier: the roster and
    proposal lints are only meaningful over a valid base index, and running them
    anyway would report derived noise as independent errors.

Stdlib `unittest` only; every KB is a throwaway tempdir (R-LOC-03) and no test
writes to the operator's real knowledge dir.

Runner:  python3 -m unittest tests.unit.features.test_indexcmd -v
"""

from tests._paths import TESTS_DIR

import builtins
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

import agentware_lib.core.config as config                     # noqa: E402
import agentware_lib.features.indexcmd as IC                   # noqa: E402


class _Args(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


@contextlib.contextmanager
def _captured():
    out, err = io.StringIO(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        yield out, err
    finally:
        sys.stdout, sys.stderr = real_out, real_err


class _Tty(io.StringIO):
    def isatty(self):
        return True


class _NotTty(io.StringIO):
    def isatty(self):
        return False


class IndexCmdCase(unittest.TestCase):
    """A scratch KB with two unindexed files and one indexed entry."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-test-indexcmd-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        _write(os.path.join(self.kdir, "learnings/known.md"),
               "---\nid: learn-known\n---\nbody\n")
        _write(os.path.join(self.kdir, "learnings/loose-one.md"), "# loose one\n")
        _write(os.path.join(self.kdir, "references/loose-two.md"), "# loose two\n")
        _write(os.path.join(self.kdir, "index.json"), json.dumps({
            "entries": [{"id": "learn-known", "title": "Known",
                         "category": "learnings", "path": "learnings/known.md",
                         "tags": ["known"], "created": "2026-01-01",
                         "summary": "s", "last_verified": "2026-01-01"}],
            "tags": {"known": ["learn-known"]}, "generation": 1,
        }, indent=2) + "\n")
        self._real_require = config.require_knowledge_dir
        config.require_knowledge_dir = lambda: self.kdir
        self.addCleanup(setattr, config, "require_knowledge_dir",
                        self._real_require)
        self._real_stdin = sys.stdin
        sys.stdin = _NotTty("")
        self.addCleanup(setattr, sys, "stdin", self._real_stdin)

    def read_index(self):
        with open(os.path.join(self.kdir, "index.json"), encoding="utf-8") as f:
            return json.load(f)

    def sync(self, **kw):
        args = _Args(**dict(dict(fix=False, format="text"), **kw))
        with _captured() as (out, err):
            rc = IC.cmd_index_sync(args)
        return rc, out.getvalue(), err.getvalue()

    def patch(self, name, value):
        real = getattr(IC.index, name)
        setattr(IC.index, name, value)
        self.addCleanup(setattr, IC.index, name, real)


# --- the dry-run renderer ----------------------------------------------------

class SyncDryReportTests(IndexCmdCase):
    def test_dry_run_lists_every_candidate_with_its_suggestion(self):
        rc, out, err = self.sync()
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("learnings/loose-one.md", out)
        self.assertIn("references/loose-two.md", out)
        self.assertIn("id=learn-loose-one", out)
        self.assertIn("category=references", out)

    def test_dry_run_writes_nothing(self):
        before = self.read_index()
        self.sync()
        self.assertEqual(self.read_index(), before)

    def test_empty_dry_run_says_so_and_still_exits_zero(self):
        os.remove(os.path.join(self.kdir, "learnings/loose-one.md"))
        os.remove(os.path.join(self.kdir, "references/loose-two.md"))
        rc, out, _ = self.sync()
        self.assertEqual(rc, 0)
        self.assertIn("no unindexed", out)

    def test_empty_dry_run_json_is_a_zero_count_not_a_bare_list(self):
        os.remove(os.path.join(self.kdir, "learnings/loose-one.md"))
        os.remove(os.path.join(self.kdir, "references/loose-two.md"))
        _, out, _ = self.sync(format="json")
        self.assertEqual(json.loads(out), {"unindexed": [], "count": 0})

    def test_json_count_matches_the_reported_rows(self):
        _, out, _ = self.sync(format="json")
        got = json.loads(out)
        self.assertEqual(got["count"], len(got["unindexed"]))
        self.assertEqual(got["count"], 2)

    def test_json_mode_prints_no_text_banner(self):
        _, out, _ = self.sync(format="json")
        self.assertNotIn("SYNC:", out)

    def test_a_namespace_without_format_defaults_to_text(self):
        args = _Args(fix=False)
        with _captured() as (out, _err):
            rc = IC.cmd_index_sync(args)
        self.assertEqual(rc, 0)
        self.assertIn("SYNC:", out.getvalue())


# --- the per-candidate prompt row -------------------------------------------

class SyncPromptRowTests(IndexCmdCase):
    def test_non_interactive_derives_every_field_without_reading_stdin(self):
        def _boom(prompt=""):
            raise AssertionError("input() must not be called non-interactively")

        real_input = builtins.input
        builtins.input = _boom
        self.addCleanup(setattr, builtins, "input", real_input)
        got = IC._sync_prompt_row("learnings/loose-one.md", False)
        self.assertEqual(got, ("learn-loose-one", "Loose One", "learnings",
                               "learnings", "Loose One"))

    def test_interactive_answers_are_used_verbatim(self):
        answers = iter(["alpha, beta", "a real summary"])
        real_input = builtins.input
        builtins.input = lambda prompt="": next(answers)
        self.addCleanup(setattr, builtins, "input", real_input)
        eid, title, category, tags_in, summary_in = IC._sync_prompt_row(
            "learnings/loose-one.md", True)
        self.assertEqual((eid, title, category), ("learn-loose-one", "Loose One",
                                                  "learnings"))
        self.assertEqual((tags_in, summary_in), ("alpha, beta", "a real summary"))

    def test_an_empty_interactive_answer_accepts_the_derived_value(self):
        answers = iter(["   ", "  "])
        real_input = builtins.input
        builtins.input = lambda prompt="": next(answers)
        self.addCleanup(setattr, builtins, "input", real_input)
        _, _, category, tags_in, summary_in = IC._sync_prompt_row(
            "references/loose-two.md", True)
        self.assertEqual((tags_in, summary_in), (category, "Loose Two"))

    def test_the_prompts_name_the_file_and_the_default(self):
        seen = []
        real_input = builtins.input

        def _record(prompt=""):
            seen.append(prompt)
            return ""

        builtins.input = _record
        self.addCleanup(setattr, builtins, "input", real_input)
        IC._sync_prompt_row("learnings/loose-one.md", True)
        self.assertEqual(len(seen), 2)
        for p in seen:
            self.assertIn("learnings/loose-one.md", p)
        self.assertIn("[learnings]", seen[0])
        self.assertIn("[Loose One]", seen[1])


# --- the apply half ----------------------------------------------------------

class SyncApplyTests(IndexCmdCase):
    def test_fix_adds_every_candidate_and_persists_them(self):
        rc, out, err = self.sync(fix=True)
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual(out.count("ADDED entry"), 2)
        # "nothing to fix" beside two ADDED lines would be a contradiction the
        # operator has to resolve; the banner is for the EMPTY run only.
        self.assertNotIn("nothing to fix", out)
        paths = {e["path"] for e in self.read_index()["entries"]}
        self.assertEqual(paths, {"learnings/known.md", "learnings/loose-one.md",
                                 "references/loose-two.md"})

    def test_fix_is_idempotent(self):
        self.sync(fix=True)
        snapshot = self.read_index()
        rc, out, _ = self.sync(fix=True)
        self.assertEqual(rc, 0)
        self.assertIn("nothing to fix", out)
        self.assertEqual(self.read_index(), snapshot)

    def test_a_partial_failure_exits_non_zero_and_reports_both_halves(self):
        real_add = IC.index._do_add

        def _half(kdir, data, eid, title, category, path, *a, **kw):
            if "loose-one" in path:
                return None, "simulated duplicate id"
            return real_add(kdir, data, eid, title, category, path, *a, **kw)

        self.patch("_do_add", _half)
        rc, out, err = self.sync(fix=True)
        self.assertEqual(rc, 1)
        self.assertIn("ADDED entry", out)
        self.assertIn("could not add learnings/loose-one.md", err)
        self.assertIn("simulated duplicate id", err)

    def test_a_partial_failure_still_persists_what_succeeded(self):
        real_add = IC.index._do_add

        def _half(kdir, data, eid, title, category, path, *a, **kw):
            if "loose-one" in path:
                return None, "nope"
            return real_add(kdir, data, eid, title, category, path, *a, **kw)

        self.patch("_do_add", _half)
        self.sync(fix=True)
        paths = {e["path"] for e in self.read_index()["entries"]}
        self.assertIn("references/loose-two.md", paths)
        self.assertNotIn("learnings/loose-one.md", paths)

    def test_an_all_fail_run_never_writes_the_index(self):
        saves = []
        self.patch("_do_add", lambda *a, **kw: (None, "always fails"))
        real_save = IC.index.save_index
        self.patch("save_index",
                   lambda kdir, data, **kw: (saves.append(kdir),
                                             real_save(kdir, data, **kw))[1])
        before = self.read_index()
        rc, out, err = self.sync(fix=True)
        self.assertEqual(rc, 1)
        self.assertEqual(saves, [])
        self.assertEqual(self.read_index(), before)
        self.assertEqual(err.count("could not add"), 2)
        # Two failures is not "nothing to fix": the banner would tell an
        # operator the KB is clean in the run that proved it is not.
        self.assertNotIn("nothing to fix", out)

    def test_sync_apply_mutates_only_in_memory(self):
        before = self.read_index()
        data, err = IC.index.load_index(self.kdir)
        self.assertIsNone(err)
        added, failures = IC._sync_apply(
            self.kdir, data, IC.index.scan_unindexed(self.kdir, data))
        self.assertEqual((len(added), failures), (2, []))
        self.assertEqual(len(data["entries"]), 3)
        self.assertEqual(self.read_index(), before)

    def test_fix_json_reports_ok_true_and_the_added_entries(self):
        rc, out, _ = self.sync(fix=True, format="json")
        got = json.loads(out)
        self.assertEqual((rc, got["ok"], got["failures"]), (0, True, []))
        self.assertEqual(len(got["added"]), 2)

    def test_fix_json_reports_ok_false_on_any_failure(self):
        self.patch("_do_add", lambda *a, **kw: (None, "always fails"))
        rc, out, _ = self.sync(fix=True, format="json")
        got = json.loads(out)
        self.assertEqual((rc, got["ok"], got["added"]), (1, False, []))
        self.assertEqual(len(got["failures"]), 2)

    def test_fix_json_writes_nothing_to_stderr(self):
        self.patch("_do_add", lambda *a, **kw: (None, "always fails"))
        _, _, err = self.sync(fix=True, format="json")
        self.assertEqual(err, "")

    def test_interactive_fix_uses_the_answered_tags(self):
        sys.stdin = _Tty("")
        answers = iter(["custom-tag", "custom summary", "", ""])
        real_input = builtins.input
        builtins.input = lambda prompt="": next(answers)
        self.addCleanup(setattr, builtins, "input", real_input)
        rc, _, _ = self.sync(fix=True)
        self.assertEqual(rc, 0)
        added = {e["path"]: e for e in self.read_index()["entries"]}
        self.assertEqual(added["learnings/loose-one.md"]["tags"], ["custom-tag"])
        self.assertEqual(added["learnings/loose-one.md"]["summary"],
                         "custom summary")
        self.assertEqual(added["references/loose-two.md"]["tags"], ["references"])


# --- guards shared by every handler -----------------------------------------

class SyncGuardTests(IndexCmdCase):
    def test_no_knowledge_dir_exits_one_before_touching_the_lock(self):
        config.require_knowledge_dir = lambda: None
        self.patch("_index_lock", lambda kdir: (_ for _ in ()).throw(
            AssertionError("must not lock without a knowledge dir")))
        with _captured() as (out, err):
            rc = IC.cmd_index_sync(_Args(fix=True, format="text"))
        self.assertEqual((rc, out.getvalue(), err.getvalue()), (1, "", ""))

    def test_a_load_error_exits_one_in_both_modes(self):
        self.patch("load_index", lambda *a, **kw: (None, "index.json is corrupt"))
        for fix in (False, True):
            with _captured() as (out, err):
                rc = IC.cmd_index_sync(_Args(fix=fix, format="text"))
            self.assertEqual(rc, 1)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("index.json is corrupt", err.getvalue())

    def test_a_load_error_never_scans_the_knowledge_dir(self):
        self.patch("load_index", lambda *a, **kw: (None, "corrupt"))
        self.patch("scan_unindexed", lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("must not scan after a failed load")))
        with _captured():
            self.assertEqual(IC.cmd_index_sync(_Args(fix=True, format="text")), 1)


# --- the other five handlers this module owns -------------------------------

class AddRemoveTests(IndexCmdCase):
    def _add_args(self, **kw):
        base = dict(id="learn-new", title="New", category="learnings",
                    path="learnings/loose-one.md", tags="a,b", summary="s",
                    created="2026-02-02", last_verified=None, format="text")
        base.update(kw)
        return _Args(**base)

    def test_add_registers_and_persists_the_entry(self):
        with _captured() as (out, err):
            rc = IC.cmd_index_add(self._add_args())
        self.assertEqual((rc, err.getvalue()), (0, ""))
        self.assertIn("ADDED entry 'learn-new'", out.getvalue())
        ids = {e["id"] for e in self.read_index()["entries"]}
        self.assertIn("learn-new", ids)

    def test_add_reports_the_frontmatter_backfill(self):
        with _captured() as (out, _err):
            IC.cmd_index_add(self._add_args())
        self.assertIn("frontmatter backfilled", out.getvalue())
        with open(os.path.join(self.kdir, "learnings/loose-one.md"),
                  encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.startswith("---\n"))

    def test_add_refuses_a_missing_path_and_writes_nothing(self):
        before = self.read_index()
        with _captured() as (out, err):
            rc = IC.cmd_index_add(self._add_args(path="learnings/absent.md"))
        self.assertEqual(rc, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("agentware:", err.getvalue())
        self.assertEqual(self.read_index(), before)

    def test_remove_drops_the_entry_and_its_tag_rows(self):
        with _captured() as (out, _err):
            rc = IC.cmd_index_remove(_Args(id="learn-known", format="text"))
        self.assertEqual(rc, 0)
        self.assertIn("REMOVED entry 'learn-known'", out.getvalue())
        data = self.read_index()
        self.assertEqual(data["entries"], [])
        self.assertNotIn("learn-known", data["tags"].get("known", []))

    def test_remove_takes_exactly_the_named_entry(self):
        # A one-entry fixture cannot tell "removed the id" from "emptied the
        # index" — the second entry is what makes the claim falsifiable.
        data = self.read_index()
        data["entries"].append({"id": "learn-other", "title": "Other",
                                "category": "learnings",
                                "path": "learnings/loose-one.md",
                                "tags": ["other"], "created": "2026-01-01",
                                "summary": "s", "last_verified": "2026-01-01"})
        data["tags"]["other"] = ["learn-other"]
        _write(os.path.join(self.kdir, "index.json"), json.dumps(data) + "\n")
        with _captured():
            rc = IC.cmd_index_remove(_Args(id="learn-known", format="text"))
        self.assertEqual(rc, 0)
        after = self.read_index()
        self.assertEqual([e["id"] for e in after["entries"]], ["learn-other"])
        self.assertEqual(after["tags"].get("other"), ["learn-other"])

    def test_remove_leaves_the_file_on_disk(self):
        with _captured():
            IC.cmd_index_remove(_Args(id="learn-known", format="text"))
        self.assertTrue(os.path.exists(os.path.join(self.kdir,
                                                    "learnings/known.md")))

    def test_remove_of_an_unknown_id_exits_one_and_changes_nothing(self):
        before = self.read_index()
        with _captured() as (_out, err):
            rc = IC.cmd_index_remove(_Args(id="nope", format="text"))
        self.assertEqual(rc, 1)
        self.assertIn("id not found: nope", err.getvalue())
        self.assertEqual(self.read_index(), before)


class ValidateTests(IndexCmdCase):
    def test_a_clean_index_passes(self):
        with _captured() as (out, _err):
            rc = IC.cmd_index_validate(_Args(format="text"))
        self.assertEqual(rc, 0)
        self.assertIn("VALIDATION PASSED", out.getvalue())

    def test_a_load_error_is_reported_as_the_only_error(self):
        self.patch("load_index", lambda *a, **kw: (None, "unreadable"))
        with _captured() as (out, _err):
            rc = IC.cmd_index_validate(_Args(format="json"))
        self.assertEqual((rc, json.loads(out.getvalue())),
                         (1, {"ok": False, "errors": ["unreadable"]}))

    def test_a_load_error_skips_the_base_validator_entirely(self):
        self.patch("load_index", lambda *a, **kw: (None, "unreadable"))
        self.patch("validate_index", lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("must not validate an index that did not load")))
        with _captured():
            self.assertEqual(IC.cmd_index_validate(_Args(format="json")), 1)

    def test_base_errors_stop_the_later_lint_tiers(self):
        self.patch("validate_index", lambda *a, **kw: ["base problem"])
        self.patch("roster_injection_errors", lambda *a, **kw: (
            _ for _ in ()).throw(AssertionError("must not run over a bad index")))
        with _captured() as (out, _err):
            rc = IC.cmd_index_validate(_Args(format="json"))
        self.assertEqual((rc, json.loads(out.getvalue())["errors"]),
                         (1, ["base problem"]))

    def test_roster_errors_stop_the_proposal_tier(self):
        self.patch("validate_index", lambda *a, **kw: [])
        self.patch("roster_injection_errors", lambda *a, **kw: ["roster problem"])
        real = IC.proposals.proposal_integrity_errors
        IC.proposals.proposal_integrity_errors = lambda *a, **kw: (
            _ for _ in ()).throw(AssertionError("must not run after a roster fail"))
        self.addCleanup(setattr, IC.proposals, "proposal_integrity_errors", real)
        with _captured() as (out, _err):
            rc = IC.cmd_index_validate(_Args(format="json"))
        self.assertEqual((rc, json.loads(out.getvalue())["errors"]),
                         (1, ["roster problem"]))

    def test_the_text_render_lists_every_error(self):
        self.patch("validate_index", lambda *a, **kw: ["one", "two"])
        with _captured() as (out, _err):
            rc = IC.cmd_index_validate(_Args(format="text"))
        text = out.getvalue()
        self.assertEqual(rc, 1)
        self.assertIn("VALIDATION FAILED (2 error(s))", text)
        self.assertIn(" - one", text)
        self.assertIn(" - two", text)

    def test_validate_never_locks_the_index(self):
        # It is a pure read: taking the write lock would serialize a read-only
        # gate against every concurrent writer for no reason.
        self.patch("_index_lock", lambda kdir: (_ for _ in ()).throw(
            AssertionError("validate must not take the write lock")))
        with _captured():
            self.assertEqual(IC.cmd_index_validate(_Args(format="text")), 0)


class MigrateRebuildTests(IndexCmdCase):
    def test_migrate_prepends_frontmatter_and_is_idempotent(self):
        _write(os.path.join(self.kdir, "learnings/known.md"), "plain body\n")
        with _captured() as (out, _err):
            rc = IC.cmd_index_migrate(_Args(format="text"))
        self.assertEqual(rc, 0)
        self.assertIn("1 file(s) gained frontmatter", out.getvalue())
        with _captured() as (out2, _err2):
            rc2 = IC.cmd_index_migrate(_Args(format="text"))
        self.assertEqual(rc2, 0)
        self.assertIn("0 file(s) gained frontmatter", out2.getvalue())

    def test_migrate_reports_an_indexed_path_missing_on_disk(self):
        os.remove(os.path.join(self.kdir, "learnings/known.md"))
        with _captured() as (_out, err):
            rc = IC.cmd_index_migrate(_Args(format="text"))
        self.assertEqual(rc, 1)
        self.assertIn("indexed path missing on disk", err.getvalue())

    def test_migrate_json_separates_migrated_skipped_and_missing(self):
        _write(os.path.join(self.kdir, "learnings/known.md"), "plain body\n")
        with _captured() as (out, _err):
            IC.cmd_index_migrate(_Args(format="json"))
        got = json.loads(out.getvalue())
        self.assertEqual(got["migrated"], ["learnings/known.md"])
        self.assertEqual((got["skipped"], got["missing"]), ([], []))
        self.assertEqual((got["migrated_count"], got["skipped_count"]), (1, 0))

    def test_migrate_skips_a_non_dict_entry(self):
        data = self.read_index()
        data["entries"].append("poison")
        _write(os.path.join(self.kdir, "index.json"), json.dumps(data) + "\n")
        with _captured() as (out, _err):
            rc = IC.cmd_index_migrate(_Args(format="json"))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())["missing"], [])

    def test_rebuild_reports_entries_rosters_and_features(self):
        with _captured() as (out, _err):
            rc = IC.cmd_index_rebuild(_Args(format="json"))
        self.assertEqual(rc, 0)
        got = json.loads(out.getvalue())
        self.assertEqual(got["ok"], True)
        self.assertGreaterEqual(got["entries"], 1)
        self.assertTrue(got["features"].endswith("FEATURES.md"))

    def test_rebuild_surfaces_errors_and_exits_one(self):
        self.patch("rebuild_kb", lambda kdir: (None, None, ["bad frontmatter"]))
        with _captured() as (out, err):
            rc = IC.cmd_index_rebuild(_Args(format="json"))
        self.assertEqual((rc, out.getvalue()), (1, ""))
        self.assertIn("rebuild: bad frontmatter", err.getvalue())

    def test_rebuild_reports_a_vector_cache_error_without_failing(self):
        real = IC.vectorcache.rebuild_vector_cache
        IC.vectorcache.rebuild_vector_cache = lambda kdir, data: {
            "status": "error", "reason": "no embedder"}
        self.addCleanup(setattr, IC.vectorcache, "rebuild_vector_cache", real)
        with _captured() as (_out, err):
            rc = IC.cmd_index_rebuild(_Args(format="text"))
        self.assertEqual(rc, 0)
        self.assertIn("Mode A unaffected", err.getvalue())

    def test_rebuild_reports_a_built_vector_cache(self):
        real = IC.vectorcache.rebuild_vector_cache
        IC.vectorcache.rebuild_vector_cache = lambda kdir, data: {
            "status": "built", "model": "m", "embedded": 2, "reused": 1,
            "total": 3, "dim": 8}
        self.addCleanup(setattr, IC.vectorcache, "rebuild_vector_cache", real)
        with _captured() as (out, _err):
            IC.cmd_index_rebuild(_Args(format="text"))
        self.assertIn("REBUILT vector cache (model m)", out.getvalue())


class ExportSurfaceTests(unittest.TestCase):
    def test_the_new_sync_helpers_are_not_exported(self):
        for name in ("_sync_dry_report", "_sync_prompt_row", "_sync_apply",
                     "_sync_fix_report"):
            self.assertNotIn(name, IC._EXPORTS, name)

    def test_the_verb_is_declared_exactly_once(self):
        self.assertEqual(IC.__verbs__, (("index", 500, "register_index"),))


if __name__ == "__main__":
    unittest.main()
