"""Owning unit file for `agentware_lib.ops.kbsync` — task 99 wave 8 (R-MOD-06).

The PUSH half: the nothing-lost gate, the four steps wave 8 split
`kb_git_push_once` into, the ORDER that is all the parent still holds, and the
bounded re-push-race retry around it. The two CLI handlers are in
`test_kbsync_handlers`; the shared header is `_kbsync_support`.

The module had no granular owner before this commit. `test_kb_git_push` drives
the push WORKFLOW end to end through real clones, which is the right test for
the workflow and the wrong one for the module: it reaches four of the module's
dispositions and cannot reach the other nine at all, because producing them
would mean breaking git itself (a rebuild that errors mid-rebase, a scoped
stage that refuses, a `rebase --continue` that fails, a nothing-lost gate that
rejects). Those are driven here at the module's own seams, so the PARENT under
test is always the real one.

Two properties this file exists to make falsifiable, both of which wave 8's
split could have broken silently:

  * the ORDER of the pipeline. Every arm is asserted on a fixture where a LATER
    step would ALSO have fired, so a transposition changes the answer rather
    than reproducing it;
  * the TWO-STAGE fallback of the "first line of the failure" sites whose OUTER
    and INNER strings differ. An absent stderr and a blank one must reach
    DIFFERENT notes, and only a truthy-but-blank stderr separates them.
"""

from tests.unit.ops._kbsync_support import (  # noqa: F401
    SeamRestoringTest, _Args, _CP, _capture, _raiser, index, kbgit, kbsync,
    os, staging, unittest)


class NothingLostGateTest(SeamRestoringTest):
    """`kb_nothing_lost_gate` (C-2) — the mechanical superset check that runs on
    the resolved work tree BEFORE the network push."""

    def test_rejects_invalid_frontmatter(self):
        index.build_index_from_frontmatter = lambda t: ({"entries": []},
                                                        ["bad fm"])
        ok, note = kbsync.kb_nothing_lost_gate("/kb", ["ORIG_HEAD"])
        self.assertFalse(ok)
        self.assertIn("invalid frontmatter after merge", note)

    def test_rejects_an_invalid_index(self):
        index.build_index_from_frontmatter = lambda t: ({"entries": []}, [])
        index.validate_index = lambda t, d: ["dup id"]
        ok, note = kbsync.kb_nothing_lost_gate("/kb", ["ORIG_HEAD"])
        self.assertFalse(ok)
        self.assertIn("index invalid after merge", note)

    def test_rejects_a_merge_that_drops_a_parent_id(self):
        index.build_index_from_frontmatter = lambda t: (
            {"entries": [{"id": "learn-a"}]}, [])
        index.validate_index = lambda t, d: []
        staging.git_entry_ids_at_ref = lambda t, ref: {"learn-a", "learn-b"}
        ok, note = kbsync.kb_nothing_lost_gate("/kb", ["ORIG_HEAD"])
        self.assertFalse(ok)
        self.assertIn("would DROP entry id(s): learn-b", note)

    def test_passes_when_the_merge_is_a_superset(self):
        index.build_index_from_frontmatter = lambda t: (
            {"entries": [{"id": "learn-a"}, {"id": "learn-b"}]}, [])
        index.validate_index = lambda t, d: []
        staging.git_entry_ids_at_ref = lambda t, ref: {"learn-a"}
        ok, note = kbsync.kb_nothing_lost_gate("/kb", ["ORIG_HEAD"])
        self.assertTrue(ok)
        self.assertIn("2 merged id(s)", note)

    def test_an_unresolvable_parent_is_skipped_not_counted_as_empty(self):
        """A parent whose ref does not resolve (a detached `@{upstream}`
        mid-rebase) returns None and must be SKIPPED. Counting it as an empty
        set would make the superset check vacuously pass."""
        index.build_index_from_frontmatter = lambda t: (
            {"entries": [{"id": "learn-a"}]}, [])
        index.validate_index = lambda t, d: []
        seen = []

        def _ids(t, ref):
            seen.append(ref)
            return None if ref == "@{upstream}" else {"learn-a"}
        staging.git_entry_ids_at_ref = _ids
        ok, note = kbsync.kb_nothing_lost_gate("/kb",
                                               ["ORIG_HEAD", "@{upstream}"])
        self.assertTrue(ok)
        self.assertIn("1 parent id(s)", note)
        self.assertEqual(seen, ["ORIG_HEAD", "@{upstream}"])

    def test_a_falsy_parent_ref_is_skipped_without_a_git_call(self):
        index.build_index_from_frontmatter = lambda t: ({"entries": []}, [])
        index.validate_index = lambda t, d: []
        staging.git_entry_ids_at_ref = _raiser("git_entry_ids_at_ref on None")
        ok, _note = kbsync.kb_nothing_lost_gate("/kb", [None, ""])
        self.assertTrue(ok)


class PushAttemptTest(SeamRestoringTest):
    """`_push_attempt` — the first push and its two TERMINAL dispositions."""

    def test_success_reports_the_last_line_of_output(self):
        """The success note tails the LAST line, not the first: git puts the
        ref update at the end of a multi-line push report."""
        staging.git_push = lambda t: _CP(
            returncode=0, stderr="To /bare\n   aaa..bbb  main -> main\n")
        self.assertEqual(kbsync._push_attempt("/kb"),
                         (0, "pushed —    aaa..bbb  main -> main"))

    def test_success_falls_back_to_stdout_when_stderr_is_empty(self):
        staging.git_push = lambda t: _CP(returncode=0, stdout="all good\n")
        self.assertEqual(kbsync._push_attempt("/kb"), (0, "pushed — all good"))

    def test_success_with_no_output_at_all_is_a_bare_pushed(self):
        staging.git_push = lambda t: _CP(returncode=0)
        self.assertEqual(kbsync._push_attempt("/kb"), (0, "pushed"))

    def test_a_non_rejection_is_a_graceful_rc_zero_skip(self):
        staging.git_push = lambda t: _CP(
            returncode=128, stderr="fatal: No configured push destination.\n")
        rc, note = kbsync._push_attempt("/kb")
        self.assertEqual(rc, 0)
        self.assertIn("skipped — fatal: No configured push destination.", note)

    def test_a_non_rejection_with_empty_stderr_uses_the_outer_fallback(self):
        staging.git_push = lambda t: _CP(returncode=1, stderr="")
        staging._push_was_rejected = lambda cp: False
        self.assertIn("skipped — push failed", kbsync._push_attempt("/kb")[1])

    def test_a_rejection_returns_none_so_the_caller_rebases(self):
        staging.git_push = lambda t: _CP(returncode=1, stderr="! [rejected]")
        staging._push_was_rejected = lambda cp: True
        self.assertIsNone(kbsync._push_attempt("/kb"))

    def test_the_classification_reads_the_push_result_not_the_return_code(self):
        """`_push_was_rejected` is the seam that decides; a non-zero return code
        alone must not be read as a rejection, or every offline push would
        start rebasing the operator's tree."""
        staging.git_push = lambda t: _CP(returncode=1, stderr="anything")
        staging._push_was_rejected = lambda cp: False
        self.assertEqual(kbsync._push_attempt("/kb")[0], 0)


class PushAfterCleanRebaseTest(SeamRestoringTest):
    """`_push_after_clean_rebase` — the conflict-free replay's tail."""

    def test_success(self):
        staging.git_push = lambda t: _CP(returncode=0)
        self.assertEqual(kbsync._push_after_clean_rebase("/kb"),
                         (0, "pushed (after fast-forward rebase)"))

    def test_rejection_is_a_race_sentinel_never_a_failure(self):
        """A clean rebase synthesizes no content, so there is nothing for the
        nothing-lost gate to catch and a rejection here can only be the upstream
        moving again — which the bounded retry, not a fail-loud, answers."""
        staging.git_push = lambda t: _CP(returncode=1)
        rc, note = kbsync._push_after_clean_rebase("/kb")
        self.assertEqual(rc, staging.RC_REPUSH_RACE)
        self.assertIn("re-push race", note)


class ProseConflictDispositionTest(SeamRestoringTest):
    """`_prose_conflict_disposition` — the two same-entry dispositions."""

    def test_pause_leaves_the_rebase_alone_and_lists_the_files(self):
        kbsync._git = _raiser("git ran on the pause path")
        rc, note = kbsync._prose_conflict_disposition(
            "/kb", {}, ["learnings/a.md", "learnings/b.md"], "pause")
        self.assertEqual(rc, 3)
        self.assertEqual(note,
                         "PROSE_CONFLICT\nlearnings/a.md\nlearnings/b.md")

    def test_abort_runs_rebase_abort_and_fails_loud(self):
        calls = []
        kbsync._git = lambda t, *a, **k: calls.append(a) or _CP(returncode=0)
        rc, note = kbsync._prose_conflict_disposition(
            "/kb", {}, ["learnings/a.md"], "abort")
        self.assertEqual(rc, 1)
        self.assertEqual(calls, [("rebase", "--abort")])
        self.assertIn("needs prose merge", note)

    def test_an_unknown_mode_aborts_rather_than_pausing(self):
        """The dispatch is `== "pause"`, not `!= "abort"`: an unrecognised flag
        value must take the SAFE arm, never leave a rebase in progress on the
        operator's tree with nobody coming back for it."""
        calls = []
        kbsync._git = lambda t, *a, **k: calls.append(a) or _CP(returncode=0)
        rc, _note = kbsync._prose_conflict_disposition(
            "/kb", {}, ["learnings/a.md"], "typo")
        self.assertEqual(rc, 1)
        self.assertEqual(calls, [("rebase", "--abort")])


class ResolveDerivedConflictTest(SeamRestoringTest):
    """`_resolve_derived_conflict` — regenerate, stage, gate, continue, push.

    Every failing arm must ABORT the rebase before returning: a half-resolved
    rebase left in place is the state the next lane inherits.
    """

    def setUp(self):
        SeamRestoringTest.setUp(self)
        self.calls = []
        kbsync._git = lambda t, *a, **k: (self.calls.append(a)
                                          or _CP(returncode=0))
        index.rebuild_kb = lambda t: ({}, {}, [])
        staging.kb_git_stage_scoped = lambda t, **k: (0, 1, [])
        kbsync.kb_nothing_lost_gate = lambda t, refs: (True, "ok")
        staging.git_push = lambda t: _CP(returncode=0)

    def test_success(self):
        rc, note = kbsync._resolve_derived_conflict("/kb", {}, "origin/main")
        self.assertEqual(rc, 0)
        self.assertIn("auto-resolved via index rebuild", note)
        self.assertEqual(self.calls, [("rebase", "--continue")])

    def test_a_rebuild_error_aborts_and_fails_loud(self):
        index.rebuild_kb = lambda t: ({}, {}, ["bad fm", "dup id"])
        rc, note = kbsync._resolve_derived_conflict("/kb", {}, "origin/main")
        self.assertEqual(rc, 1)
        self.assertIn("rebuild during merge failed: bad fm; dup id", note)
        self.assertEqual(self.calls, [("rebase", "--abort")])

    def test_a_refused_scoped_stage_aborts_and_fails_loud(self):
        staging.kb_git_stage_scoped = lambda t, **k: (1, 0, ["cross-team"])
        rc, note = kbsync._resolve_derived_conflict("/kb", {}, "origin/main")
        self.assertEqual(rc, 1)
        self.assertIn("scoped allowlist", note)
        self.assertEqual(self.calls, [("rebase", "--abort")])

    def test_the_stage_is_scoped_rather_than_add_all(self):
        """`git add -A` mid-merge stages another team's files as part of this
        team's commit. The scoped engine is the seam, and it must be the one
        called — a `_git(add, -A)` would show up in `self.calls`."""
        kbsync._resolve_derived_conflict("/kb", {}, "origin/main")
        self.assertNotIn(("add", "-A"), self.calls)

    def test_a_failing_gate_aborts_before_the_push(self):
        kbsync.kb_nothing_lost_gate = lambda t, refs: (False, "would DROP x")
        staging.git_push = _raiser("push after a failed nothing-lost gate")
        rc, note = kbsync._resolve_derived_conflict("/kb", {}, "origin/main")
        self.assertEqual((rc, note), (1, "would DROP x"))
        self.assertEqual(self.calls, [("rebase", "--abort")])

    def test_the_gate_is_asked_about_both_parents(self):
        seen = []
        kbsync.kb_nothing_lost_gate = lambda t, refs: (seen.append(list(refs))
                                                       or (True, "ok"))
        kbsync._resolve_derived_conflict("/kb", {}, "origin/trunk")
        self.assertEqual(seen, [["ORIG_HEAD", "origin/trunk"]])

    def test_a_failed_continue_reports_the_first_stderr_line(self):
        def _git(t, *a, **k):
            self.calls.append(a)
            if a[:2] == ("rebase", "--continue"):
                return _CP(returncode=1, stderr="error: could not apply\nhint\n")
            return _CP(returncode=0)
        kbsync._git = _git
        rc, note = kbsync._resolve_derived_conflict("/kb", {}, "origin/main")
        self.assertEqual(rc, 1)
        self.assertEqual(note,
                         "rebase --continue failed: error: could not apply")
        self.assertIn(("rebase", "--abort"), self.calls)

    def test_a_failed_continue_with_empty_stderr_names_the_step(self):
        """OUTER: git said nothing, so the note supplies its own description."""
        kbsync._git = lambda t, *a, **k: (
            self.calls.append(a)
            or _CP(returncode=1 if a[:2] == ("rebase", "--continue") else 0))
        _rc, note = kbsync._resolve_derived_conflict("/kb", {}, "origin/main")
        self.assertEqual(note,
                         "rebase --continue failed: rebase --continue failed")

    def test_a_failed_continue_with_blank_stderr_reports_nothing(self):
        """INNER, and the row the two-stage fallback exists for: git DID write,
        and it carried no information, so the note trails off rather than
        repeating its own prefix. Collapsing the two fallbacks makes this row
        identical to the one above."""
        def _git(t, *a, **k):
            self.calls.append(a)
            if a[:2] == ("rebase", "--continue"):
                return _CP(returncode=1, stderr="   \n\t\n")
            return _CP(returncode=0)
        kbsync._git = _git
        _rc, note = kbsync._resolve_derived_conflict("/kb", {}, "origin/main")
        self.assertEqual(note, "rebase --continue failed: ")

    def test_a_rejected_final_push_is_a_race_not_a_loss(self):
        staging.git_push = lambda t: _CP(returncode=1)
        rc, note = kbsync._resolve_derived_conflict("/kb", {}, "origin/main")
        self.assertEqual(rc, staging.RC_REPUSH_RACE)
        self.assertIn("after deterministic resolve", note)


class PushOnceOrderTest(SeamRestoringTest):
    """`kb_git_push_once` — the ORDER, which is all this body still holds."""

    def test_a_settled_attempt_short_circuits_before_any_git(self):
        """The classification precedes the work: a push that was not rejected
        must return without a `pull --rebase` ever running."""
        kbsync._push_attempt = lambda t: (0, "settled")
        kbsync._git = _raiser("pull --rebase after a settled attempt")
        kbsync._git_upstream_name = lambda t: "origin/main"
        self.assertEqual(kbsync.kb_git_push_once("/kb"), (0, "settled"))

    def test_the_upstream_name_is_read_before_the_rebase_detaches_head(self):
        """It is captured ON the branch, so the gate can still name the parent
        mid-rebase. A spy on the reader proves the READ happened first; the
        error message alone could not, since the consumer stays put."""
        order = []
        kbsync._git_upstream_name = lambda t: order.append("read") or "up"
        staging.git_push = lambda t: (order.append("push")
                                      or _CP(returncode=0))
        kbsync.kb_git_push_once("/kb")
        self.assertEqual(order, ["read", "push"])

    def test_a_clean_rebase_takes_the_clean_tail(self):
        kbsync._push_attempt = lambda t: None
        kbsync._git_upstream_name = lambda t: "up"
        kbsync._git = lambda t, *a, **k: _CP(returncode=0)
        staging.git_unmerged_paths = _raiser("unmerged read after a clean rebase")
        staging.git_push = lambda t: _CP(returncode=0)
        self.assertEqual(kbsync.kb_git_push_once("/kb"),
                         (0, "pushed (after fast-forward rebase)"))

    def test_a_failed_rebase_with_nothing_unmerged_aborts(self):
        kbsync._push_attempt = lambda t: None
        kbsync._git_upstream_name = lambda t: "up"
        calls = []
        kbsync._git = lambda t, *a, **k: (
            calls.append(a)
            or _CP(returncode=1 if a[:1] == ("pull",) else 0,
                   stderr="fatal: it broke\nmore\n" if a[:1] == ("pull",)
                   else ""))
        staging.git_unmerged_paths = lambda t: []
        rc, note = kbsync.kb_git_push_once("/kb")
        self.assertEqual((rc, note), (1, "rebase failed: fatal: it broke"))
        self.assertIn(("rebase", "--abort"), calls)

    def test_a_failed_rebase_with_empty_stderr_names_the_step(self):
        kbsync._push_attempt = lambda t: None
        kbsync._git_upstream_name = lambda t: "up"
        kbsync._git = lambda t, *a, **k: _CP(
            returncode=1 if a[:1] == ("pull",) else 0)
        staging.git_unmerged_paths = lambda t: []
        self.assertEqual(kbsync.kb_git_push_once("/kb")[1],
                         "rebase failed: rebase failed")

    def test_a_failed_rebase_with_blank_stderr_reports_unknown(self):
        """INNER differs from OUTER here ("unknown" vs "rebase failed"), so a
        single-fallback extraction returns the row above and this one reddens."""
        kbsync._push_attempt = lambda t: None
        kbsync._git_upstream_name = lambda t: "up"
        kbsync._git = lambda t, *a, **k: _CP(
            returncode=1 if a[:1] == ("pull",) else 0,
            stderr="  \n \n" if a[:1] == ("pull",) else "")
        staging.git_unmerged_paths = lambda t: []
        self.assertEqual(kbsync.kb_git_push_once("/kb")[1],
                         "rebase failed: unknown")

    def test_one_non_derived_path_among_derived_ones_takes_the_prose_arm(self):
        """The classification is ANY non-derived, not ALL: a single entry file
        in the conflict set means an agent-free resolve would lose prose."""
        self._derived_only()
        staging.git_unmerged_paths = lambda t: ["index.json",
                                                "learnings/a.md"]
        staging._is_derived_rel = lambda p: p == "index.json"
        index.rebuild_kb = _raiser("rebuild with an entry file conflicted")
        rc, note = kbsync.kb_git_push_once("/kb")
        self.assertEqual(rc, 1)
        self.assertIn("learnings/a.md", note)

    def test_the_prose_arm_receives_only_the_non_derived_paths(self):
        self._derived_only()
        staging.git_unmerged_paths = lambda t: ["index.json", "learnings/a.md"]
        staging._is_derived_rel = lambda p: p == "index.json"
        _rc, note = kbsync.kb_git_push_once("/kb", on_prose_conflict="pause")
        self.assertEqual(note, "PROSE_CONFLICT\nlearnings/a.md")

    def test_all_derived_conflicts_reach_the_resolver(self):
        self._derived_only()
        seen = []
        kbsync._resolve_derived_conflict = lambda t, env, up: (
            seen.append(up) or (0, "resolved"))
        self.assertEqual(kbsync.kb_git_push_once("/kb"), (0, "resolved"))
        self.assertEqual(seen, ["origin/main"])


class PushSyncedTest(SeamRestoringTest):
    """`kb_git_push_synced` — the BOUNDED pull->resolve->push retry (C-2)."""

    def setUp(self):
        SeamRestoringTest.setUp(self)
        self.sleeps = []
        staging._kb_push_backoff_delay = lambda a: (self.sleeps.append(a) or 0)

    def _script(self, *results):
        seq = {"i": 0}

        def _once(target, on_prose_conflict="abort"):
            i = seq["i"]
            seq["i"] += 1
            return results[min(i, len(results) - 1)]
        kbsync.kb_git_push_once = _once

    def test_a_first_attempt_success_returns_without_sleeping(self):
        self._script((0, "pushed"))
        self.assertEqual(kbsync.kb_git_push_synced("/kb"), (0, "pushed"))
        self.assertEqual(self.sleeps, [])

    def test_a_race_is_retried_with_a_backoff_then_succeeds(self):
        self._script((staging.RC_REPUSH_RACE, "race"), (0, "pushed later"))
        self.assertEqual(kbsync.kb_git_push_synced("/kb"), (0, "pushed later"))
        self.assertEqual(self.sleeps, [1])

    def test_exhausted_retries_fail_loud_rather_than_loop(self):
        self._script((staging.RC_REPUSH_RACE, "race"))
        rc, note = kbsync.kb_git_push_synced("/kb", max_retries=3)
        self.assertEqual(rc, 1)
        self.assertIn("after 3 re-push race retries", note)
        self.assertIn("the local commit is retained", note)

    def test_the_backoff_is_not_slept_after_the_final_attempt(self):
        """N attempts means N-1 waits: sleeping after the last one delays a
        failure that has already been decided."""
        self._script((staging.RC_REPUSH_RACE, "race"))
        kbsync.kb_git_push_synced("/kb", max_retries=3)
        self.assertEqual(self.sleeps, [1, 2])

    def test_a_zero_max_retries_floors_at_one_attempt(self):
        """`max(1, …)`: a 0 must still TRY once, never return "no attempt"."""
        self._script((staging.RC_REPUSH_RACE, "race"))
        rc, note = kbsync.kb_git_push_synced("/kb", max_retries=0)
        self.assertEqual(rc, 1)
        self.assertIn("after 1 re-push race retries", note)
        self.assertNotIn("no attempt", note)

    def test_none_max_retries_takes_the_staging_constant(self):
        self._script((staging.RC_REPUSH_RACE, "race"))
        _rc, note = kbsync.kb_git_push_synced("/kb", max_retries=None)
        self.assertIn("after %d re-push race retries"
                      % staging.KB_PUSH_MAX_RETRIES, note)

    def test_every_other_disposition_returns_immediately(self):
        for rc_in, label in ((0, "skipped"), (1, "fail loud"),
                             (3, "PROSE_CONFLICT\nlearnings/a.md")):
            self._script((rc_in, label), (0, "must not be reached"))
            self.assertEqual(kbsync.kb_git_push_synced("/kb"), (rc_in, label))
            self.assertEqual(self.sleeps, [])

    def test_the_prose_mode_is_forwarded_to_every_attempt(self):
        seen = []

        def _once(target, on_prose_conflict="abort"):
            seen.append(on_prose_conflict)
            return (staging.RC_REPUSH_RACE, "race") if len(seen) < 2 \
                else (0, "ok")
        kbsync.kb_git_push_once = _once
        kbsync.kb_git_push_synced("/kb", on_prose_conflict="pause")
        self.assertEqual(seen, ["pause", "pause"])

if __name__ == "__main__":
    unittest.main()
