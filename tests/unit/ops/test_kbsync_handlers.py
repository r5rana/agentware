"""`test_kbsync` part 2 — the two CLI HANDLERS (task 99 wave 8).

Split from the owning unit file at the module's own seam when it outgrew the
600-line corpus cap. `tests/unit/ops/test_kbsync.py` remains the R-MOD-06
owning path and holds the push pipeline; the shared header is
`_kbsync_support`. This part holds what the two `cmd_*` entry points own: the
precondition chain they share (`_resolve_sync_target`), the prose-merge
continuation (`kb_git_merge_continue`), and the stdout/stderr split that IS the
contract for a refusal.
"""

from tests.unit.ops._kbsync_support import (  # noqa: F401
    SeamRestoringTest, _Args, _CP, _capture, _raiser, index, kbgit, kbsync,
    os, staging, unittest)


class ResolveSyncTargetTest(SeamRestoringTest):
    """`_resolve_sync_target` — the precondition chain the two write verbs
    share, and the ORDER that makes each notice the actionable one."""

    def setUp(self):
        SeamRestoringTest.setUp(self)
        kbgit.git_is_work_tree = lambda t: True
        kbgit.git_toplevel = lambda t: "/somewhere/%s" % os.path.basename(t)
        kbgit.git_has_upstream = lambda t: True

    def test_an_explicit_path_is_absolutized_and_expanded(self):
        target, rc = kbsync._resolve_sync_target(_Args(path="~/kb"), "v", "s")
        self.assertEqual(rc, 0)
        self.assertEqual(target, os.path.expanduser("~/kb"))

    def test_an_explicit_path_bypasses_the_resolver_entirely(self):
        class _Cfg(object):
            @staticmethod
            def require_knowledge_dir():
                raise AssertionError("resolver reached despite --path")
        kbsync.config = _Cfg()
        target, _rc = kbsync._resolve_sync_target(_Args(path="/kb"), "v", "s")
        self.assertEqual(target, "/kb")

    def test_no_path_falls_back_to_the_resolved_knowledge_dir(self):
        class _Cfg(object):
            @staticmethod
            def require_knowledge_dir():
                return "/resolved/kb"
        kbsync.config = _Cfg()
        target, _rc = kbsync._resolve_sync_target(_Args(path=None), "v", "s")
        self.assertEqual(target, "/resolved/kb")

    def test_an_unresolvable_knowledge_dir_fails_loud(self):
        """The ONE fail-loud arm of the chain: a target that cannot be NAMED is
        a configuration error, not an absent repo, so it must not degrade."""
        class _Cfg(object):
            @staticmethod
            def require_knowledge_dir():
                return None
        kbsync.config = _Cfg()
        kbgit.git_is_work_tree = _raiser("work-tree probe on an unnamed target")
        self.assertEqual(kbsync._resolve_sync_target(_Args(path=None), "v", "s"),
                         (None, 1))

    def test_a_non_work_tree_is_a_graceful_skip_with_the_verb_wording(self):
        kbgit.git_is_work_tree = lambda t: False
        (target, rc), out, _err = _capture(
            kbsync._resolve_sync_target, _Args(path="/kb"),
            "kb-git merge-continue", "nothing to continue")
        self.assertEqual((target, rc), (None, 0))
        self.assertEqual(out, "kb-git merge-continue: /kb is not a git work "
                              "tree — nothing to continue (local-only).\n")

    def test_the_package_repo_is_refused_on_stderr_with_rc_one(self):
        kbgit.git_toplevel = lambda t: "/pkg/top"
        (target, rc), out, err = _capture(
            kbsync._resolve_sync_target, _Args(path="/kb"), "kb-git push",
            "skipping push")
        self.assertEqual((target, rc), (None, 1))
        self.assertEqual(out, "")
        self.assertIn("refusing — target resolves to the agentware package "
                      "repo (/pkg/top)", err)

    def test_the_package_refusal_precedes_the_upstream_check(self):
        """ORDER, on a fixture where BOTH would fire: a package repo with no
        upstream must REFUSE (rc 1), not skip (rc 0). A transposition turns a
        loud refusal into a quiet no-op on the operator's own checkout."""
        kbgit.git_toplevel = lambda t: "/pkg/top"
        kbgit.git_has_upstream = lambda t: False
        (target, rc), _out, err = _capture(
            kbsync._resolve_sync_target, _Args(path="/kb"), "kb-git push",
            "skipping push", require_upstream=True)
        self.assertEqual((target, rc), (None, 1))
        self.assertIn("refusing", err)

    def test_the_work_tree_check_precedes_the_package_refusal(self):
        """Also ORDER: a plain directory inside the package checkout is a
        local-only SKIP, because `git_toplevel` on a non-repo is None and the
        refusal would never have matched anyway — but only if the work-tree
        guard ran first."""
        kbgit.git_is_work_tree = lambda t: False
        kbgit.git_toplevel = _raiser("toplevel probe on a non-work-tree")
        (target, rc), out, _err = _capture(
            kbsync._resolve_sync_target, _Args(path="/kb"), "kb-git push",
            "skipping push")
        self.assertEqual((target, rc), (None, 0))
        self.assertIn("not a git work tree", out)

    def test_an_unresolvable_toplevel_falls_through_rather_than_refusing(self):
        """`top and pkg_top and top == pkg_top` — a None on either side must
        NOT match, or two unresolvable targets would be refused as each other."""
        kbgit.git_toplevel = lambda t: None
        target, rc = kbsync._resolve_sync_target(_Args(path="/kb"), "v", "s")
        self.assertEqual((target, rc), ("/kb", 0))

    def test_the_upstream_check_is_skipped_unless_requested(self):
        """merge-continue does NOT require an upstream: a paused rebase must be
        finishable on a repo whose remote has since gone away."""
        kbgit.git_has_upstream = _raiser("upstream probe without require")
        target, rc = kbsync._resolve_sync_target(_Args(path="/kb"), "v", "s")
        self.assertEqual((target, rc), ("/kb", 0))

    def test_a_missing_upstream_is_a_graceful_skip_when_requested(self):
        kbgit.git_has_upstream = lambda t: False
        (target, rc), out, _err = _capture(
            kbsync._resolve_sync_target, _Args(path="/kb"), "kb-git push",
            "skipping push", require_upstream=True)
        self.assertEqual((target, rc), (None, 0))
        self.assertIn("no upstream tracking branch", out)

    def test_a_missing_path_attribute_is_read_as_absent(self):
        class _Cfg(object):
            @staticmethod
            def require_knowledge_dir():
                return "/resolved/kb"
        kbsync.config = _Cfg()
        target, _rc = kbsync._resolve_sync_target(_Args(), "v", "s")
        self.assertEqual(target, "/resolved/kb")


class MergeContinueTest(SeamRestoringTest):
    """`kb_git_merge_continue` — the fail-loud disposition set that finishes a
    paused prose merge.

    The governing asymmetry: a failure here leaves the rebase IN PLACE. The
    partial reconciliation is a human's work, and aborting it to tidy up would
    throw that work away to make the tree look clean.
    """

    def setUp(self):
        SeamRestoringTest.setUp(self)
        self.calls = []
        kbsync._git = lambda t, *a, **k: (self.calls.append(a)
                                          or _CP(returncode=0))
        staging.git_rebase_in_progress = lambda t: True
        staging.git_unmerged_paths = lambda t: ["learnings/alpha.md"]
        staging._is_derived_rel = lambda p: False
        staging.file_has_conflict_markers = lambda p: False
        staging.kb_git_stage_scoped = lambda t, **k: (0, 1, [])
        index.rebuild_kb = lambda t: ({}, {}, [])
        kbsync.kb_nothing_lost_gate = lambda t, refs: (True, "ok")
        staging.git_push = lambda t: _CP(returncode=0)

    def test_no_rebase_in_progress_is_a_fail_loud_no_op(self):
        staging.git_rebase_in_progress = lambda t: False
        staging.git_unmerged_paths = _raiser("unmerged read with no rebase")
        self.assertEqual(kbsync.kb_git_merge_continue("/kb"),
                         (1, "no rebase in progress — nothing to continue"))

    def test_remaining_conflict_markers_refuse_without_aborting(self):
        """NOT continuing AND not aborting is the point: the partial prose
        merge is preserved for another reconciliation pass."""
        staging.file_has_conflict_markers = lambda p: True
        rc, note = kbsync.kb_git_merge_continue("/kb")
        self.assertEqual(rc, 1)
        self.assertIn("unresolved conflict markers remain in "
                      "learnings/alpha.md", note)
        self.assertIn("rebase left in place", note)
        self.assertEqual(self.calls, [])

    def test_only_entry_files_are_checked_for_markers(self):
        """A DERIVED file is regenerated below, so conflict markers in one are
        about to be overwritten — refusing on them would block a merge that is
        already resolved."""
        staging.git_unmerged_paths = lambda t: ["index.json"]
        staging._is_derived_rel = lambda p: p == "index.json"
        staging.file_has_conflict_markers = _raiser("marker scan of a derived "
                                                    "file")
        self.assertEqual(kbsync.kb_git_merge_continue("/kb")[0], 0)

    def test_the_reconciled_entry_files_are_staged_by_name(self):
        kbsync.kb_git_merge_continue("/kb")
        self.assertEqual(self.calls[0], ("add", "--", "learnings/alpha.md"))

    def test_no_entry_files_unmerged_skips_the_add(self):
        staging.git_unmerged_paths = lambda t: []
        kbsync.kb_git_merge_continue("/kb")
        self.assertEqual(self.calls, [("rebase", "--continue")])

    def test_a_failed_add_fails_loud_with_the_stderr(self):
        def _git(t, *a, **k):
            self.calls.append(a)
            if a[:1] == ("add",):
                return _CP(returncode=1, stderr="fatal: pathspec\n")
            return _CP(returncode=0)
        kbsync._git = _git
        rc, note = kbsync.kb_git_merge_continue("/kb")
        self.assertEqual(rc, 1)
        self.assertEqual(note, "git add (entry files) failed: fatal: pathspec")

    def test_the_derived_files_are_regenerated_never_hand_merged(self):
        """C-1: even if the agent touched `index.json`, the rebuild overwrites
        it with the canonical content derived from the merged entry union."""
        seen = []
        index.rebuild_kb = lambda t: seen.append(t) or ({}, {}, [])
        kbsync.kb_git_merge_continue("/kb")
        self.assertEqual(seen, ["/kb"])

    def test_a_rebuild_error_fails_loud(self):
        index.rebuild_kb = lambda t: ({}, {}, ["bad fm"])
        rc, note = kbsync.kb_git_merge_continue("/kb")
        self.assertEqual((rc, note),
                         (1, "rebuild after prose merge failed: bad fm"))

    def test_the_derived_stage_is_the_one_caller_allowed_to_stage_derived(self):
        """It just REGENERATED them from the merged union, so they are the
        output of the merge rather than a stale artifact — and leaving them
        unstaged leaves the tree dirty for the next lane."""
        seen = []
        staging.kb_git_stage_scoped = lambda t, **k: (seen.append(k)
                                                      or (0, 1, []))
        kbsync.kb_git_merge_continue("/kb")
        self.assertEqual(seen, [{"allow_derived": True}])

    def test_a_refused_derived_stage_fails_loud(self):
        staging.kb_git_stage_scoped = lambda t, **k: (1, 0, ["oversized"])
        rc, note = kbsync.kb_git_merge_continue("/kb")
        self.assertEqual((rc, note),
                         (1, "git add (derived) failed (scoped allowlist)"))

    def test_a_failing_gate_stops_before_the_push_and_leaves_the_rebase(self):
        kbsync.kb_nothing_lost_gate = lambda t, refs: (False, "would DROP y")
        staging.git_push = _raiser("push after a failed nothing-lost gate")
        rc, note = kbsync.kb_git_merge_continue("/kb")
        self.assertEqual((rc, note), (1, "would DROP y"))
        self.assertNotIn(("rebase", "--abort"), self.calls)

    def test_the_gate_names_both_parents_including_the_upstream(self):
        seen = []
        kbsync.kb_nothing_lost_gate = lambda t, refs: (seen.append(list(refs))
                                                       or (True, "ok"))
        kbsync.kb_git_merge_continue("/kb")
        self.assertEqual(seen, [["ORIG_HEAD", "@{upstream}"]])

    def test_a_failed_continue_reports_the_first_stderr_line(self):
        def _git(t, *a, **k):
            self.calls.append(a)
            if a[:1] == ("rebase",):
                return _CP(returncode=1, stderr="error: no changes\nhint\n")
            return _CP(returncode=0)
        kbsync._git = _git
        self.assertEqual(kbsync.kb_git_merge_continue("/kb"),
                         (1, "rebase --continue failed: error: no changes"))

    def test_a_failed_continue_with_empty_stderr_names_the_step(self):
        def _git(t, *a, **k):
            self.calls.append(a)
            return _CP(returncode=1 if a[:1] == ("rebase",) else 0)
        kbsync._git = _git
        self.assertEqual(
            kbsync.kb_git_merge_continue("/kb")[1],
            "rebase --continue failed: rebase --continue failed")

    def test_a_failed_continue_with_blank_stderr_reports_nothing(self):
        """INNER differs from OUTER at this site, so a collapsed extraction
        returns the row above and this one reddens."""
        def _git(t, *a, **k):
            self.calls.append(a)
            if a[:1] == ("rebase",):
                return _CP(returncode=1, stderr=" \n\t\n")
            return _CP(returncode=0)
        kbsync._git = _git
        self.assertEqual(kbsync.kb_git_merge_continue("/kb")[1],
                         "rebase --continue failed: ")

    def test_a_rejected_push_after_a_prose_merge_fails_loud(self):
        """Unlike the push path, there is no retry sentinel here: the rebase is
        finished, so a rejection means a human decides what happens next."""
        staging.git_push = lambda t: _CP(returncode=1)
        self.assertEqual(kbsync.kb_git_merge_continue("/kb"),
                         (1, "push rejected after prose merge (re-push race)"))

    def test_success(self):
        self.assertEqual(
            kbsync.kb_git_merge_continue("/kb"),
            (0, "pushed (prose conflict reconciled; derived files rebuilt)"))


class CmdKbGitPushTest(SeamRestoringTest):
    """`cmd_kb_git_push` — flag plumbing and the stdout/stderr split."""

    def setUp(self):
        SeamRestoringTest.setUp(self)
        self.seen = []
        kbsync._resolve_sync_target = lambda a, v, s, **k: ("/kb", 0)
        kbsync.kb_git_push_synced = lambda t, **k: (self.seen.append(k)
                                                    or (0, "pushed"))

    def test_a_none_target_returns_the_resolver_rc_without_pushing(self):
        for rc in (0, 1):
            kbsync._resolve_sync_target = lambda a, v, s, rc=rc, **k: (None, rc)
            kbsync.kb_git_push_synced = _raiser("push on an unresolved target")
            self.assertEqual(
                _capture(kbsync.cmd_kb_git_push, _Args(path=None))[0], rc)

    def test_the_resolver_is_asked_for_an_upstream(self):
        """push REQUIRES one; merge-continue does not, and the two verbs share
        one resolver — so which of them passes the flag is the difference."""
        seen = []
        kbsync._resolve_sync_target = lambda a, v, s, **k: (seen.append((v, s, k))
                                                            or ("/kb", 0))
        _capture(kbsync.cmd_kb_git_push, _Args(path="/kb"))
        self.assertEqual(seen, [("kb-git push", "skipping push",
                                 {"require_upstream": True})])

    def test_the_flags_are_forwarded_to_the_synced_push(self):
        _capture(kbsync.cmd_kb_git_push,
                 _Args(path="/kb", on_prose_conflict="pause", max_retries=7))
        self.assertEqual(self.seen,
                         [{"on_prose_conflict": "pause", "max_retries": 7}])

    def test_absent_flag_attributes_take_their_defaults(self):
        _capture(kbsync.cmd_kb_git_push, _Args(path="/kb"))
        self.assertEqual(self.seen,
                         [{"on_prose_conflict": "abort", "max_retries": 3}])

    def test_a_zero_max_retries_floors_at_three(self):
        """`getattr(...) or 3`: an explicit 0 is falsy, and an unbounded-looking
        0 must not become "one attempt" by accident here — the flooring to a
        single attempt belongs to `kb_git_push_synced`, not to the flag."""
        _capture(kbsync.cmd_kb_git_push, _Args(path="/kb", max_retries=0))
        self.assertEqual(self.seen[0]["max_retries"], 3)

    def test_the_prose_pause_writes_paths_to_stdout_and_prose_to_stderr(self):
        """The SPLIT is the contract: the loop consumes stdout to feed the
        MERGE_PROMPT, so a human note on the same stream would be read as a
        file path."""
        kbsync.kb_git_push_synced = lambda t, **k: (
            3, "PROSE_CONFLICT\nlearnings/a.md\n\nlearnings/b.md")
        rc, out, err = _capture(kbsync.cmd_kb_git_push, _Args(path="/kb"))
        self.assertEqual(rc, 3)
        self.assertEqual(out, "learnings/a.md\nlearnings/b.md\n")
        self.assertIn("prose conflict in 2 entry file(s)", err)
        self.assertIn("rebase left in progress", err)

    def test_an_rc_three_without_the_sentinel_takes_the_ordinary_tail(self):
        kbsync.kb_git_push_synced = lambda t, **k: (3, "something else")
        rc, out, err = _capture(kbsync.cmd_kb_git_push, _Args(path="/kb"))
        self.assertEqual(rc, 3)
        self.assertEqual(out, "")
        self.assertEqual(err, "kb-git push: something else\n")

    def test_rc_zero_goes_to_stdout(self):
        rc, out, err = _capture(kbsync.cmd_kb_git_push, _Args(path="/kb"))
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual(out, "kb-git push: pushed\n")

    def test_a_non_zero_rc_goes_to_stderr(self):
        kbsync.kb_git_push_synced = lambda t, **k: (1, "failed loud")
        rc, out, err = _capture(kbsync.cmd_kb_git_push, _Args(path="/kb"))
        self.assertEqual((rc, out), (1, ""))
        self.assertEqual(err, "kb-git push: failed loud\n")


class CmdKbGitMergeContinueTest(SeamRestoringTest):
    """`cmd_kb_git_merge_continue` — the same shape without the upstream."""

    def setUp(self):
        SeamRestoringTest.setUp(self)
        kbsync._resolve_sync_target = lambda a, v, s, **k: ("/kb", 0)
        kbsync.kb_git_merge_continue = lambda t: (0, "pushed")

    def test_a_none_target_returns_the_resolver_rc_without_continuing(self):
        for rc in (0, 1):
            kbsync._resolve_sync_target = lambda a, v, s, rc=rc, **k: (None, rc)
            kbsync.kb_git_merge_continue = _raiser("continue on an unresolved "
                                                   "target")
            self.assertEqual(
                _capture(kbsync.cmd_kb_git_merge_continue,
                         _Args(path=None))[0], rc)

    def test_the_resolver_is_not_asked_for_an_upstream(self):
        """A paused rebase must be finishable on a repo whose remote has since
        gone away, so this verb passes no `require_upstream`."""
        seen = []
        kbsync._resolve_sync_target = lambda a, v, s, **k: (seen.append((v, s, k))
                                                            or ("/kb", 0))
        _capture(kbsync.cmd_kb_git_merge_continue, _Args(path="/kb"))
        self.assertEqual(seen, [("kb-git merge-continue",
                                 "nothing to continue", {})])

    def test_rc_zero_goes_to_stdout(self):
        rc, out, err = _capture(kbsync.cmd_kb_git_merge_continue,
                                _Args(path="/kb"))
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual(out, "kb-git merge-continue: pushed\n")

    def test_a_non_zero_rc_goes_to_stderr(self):
        kbsync.kb_git_merge_continue = lambda t: (1, "no rebase in progress")
        rc, out, err = _capture(kbsync.cmd_kb_git_merge_continue,
                                _Args(path="/kb"))
        self.assertEqual((rc, out), (1, ""))
        self.assertEqual(err, "kb-git merge-continue: no rebase in progress\n")


class ModuleSurfaceTest(unittest.TestCase):
    """The re-export contract: `_EXPORTS` is imported BY NAME onto the facade,
    where the resident fanout merge-queue gate and the dream repoint step
    resolve these as bare names."""

    def test_every_exported_name_exists(self):
        for name in kbsync._EXPORTS:
            self.assertTrue(hasattr(kbsync, name), "missing export: %s" % name)

    def test_all_mirrors_exports(self):
        self.assertEqual(list(kbsync.__all__), list(kbsync._EXPORTS))

    def test_the_five_wave_eight_helpers_are_exported(self):
        for name in ("_push_attempt", "_push_after_clean_rebase",
                     "_prose_conflict_disposition", "_resolve_derived_conflict",
                     "_resolve_sync_target"):
            self.assertIn(name, kbsync._EXPORTS)

    def test_the_verb_declaration_names_this_module_registrar(self):
        self.assertEqual(kbsync.__verbs__, (("kb-git", 2800, "register_kb_git"),))


if __name__ == "__main__":
    unittest.main()

if __name__ == "__main__":
    unittest.main()
