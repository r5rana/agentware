"""Owning unit file for `agentware_lib.ops.kbpull` — task 99 wave 8 (R-MOD-06).

The module had no granular owner before this commit. Its pull path was reached
through `test_kb_git` (the workflow) and its two RECONCILIATIONS — the reason
this module exists at all — were exercised only incidentally, so a break in
either went red somewhere else or nowhere.

What this file is built to make falsifiable:

  * the ORDER of `_pull_preconditions`. All three can be unmet at once, and the
    notice the operator gets decides which thing they go and fix. Every arm is
    asserted on a fixture where a LATER precondition would ALSO have failed, so
    a transposition changes the message rather than reproducing it.
  * `NEVER RAISES`. Both reconciliations are best-effort by contract: a
    reconciliation that breaks the sync it follows is worse than the staleness
    it fixes. Each is driven with its collaborator wired to RAISE, and the
    assertion is that the pull still returns.
  * the QUARANTINE report. `rebuild_kb` drops a malformed or duplicate-id entry
    instead of failing, so a lossy pull would otherwise report a clean success.

Nothing here touches the operator's checkout or knowledge base: every repo is a
`mkdtemp` scratch tree and every seam is restored by `SeamRestoringTest`.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.ops.kbgit as kbgit  # noqa: E402
import agentware_lib.ops.kbpull as kbpull  # noqa: E402


def _have_git():
    try:
        subprocess.run(["git", "--version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
        return True
    except FileNotFoundError:  # pragma: no cover - environment dependent
        return False


HAVE_GIT = _have_git()


class _CP(object):
    """A CompletedProcess stand-in with exactly the three fields this module
    reads. Constructed rather than run, so a stdout/stderr can be made EMPTY or
    WHITESPACE-ONLY — the two inputs that separate the shared reduction's two
    fallback stages."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Args(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _raiser(label):
    def _fn(*_a, **_k):
        raise AssertionError("reached: %s" % label)
    return _fn


def _capture(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rv = fn(*a, **k)
    return rv, out.getvalue(), err.getvalue()


class SeamRestoringTest(unittest.TestCase):
    """Restores every module-level seam this file injects into. The originals
    are captured in `setUp` BEFORE any test patches anything: a cleanup
    registered after its own patch captures the ALREADY-PATCHED value, passes
    in its own test, and reddens an innocent later one."""

    def setUp(self):
        self._saved = [
            (kbpull, "_git", kbpull._git),
            (kbpull, "_git_head", kbpull._git_head),
            (kbpull, "git_pull_ff", kbpull.git_pull_ff),
            (kbpull, "git_is_work_tree", kbpull.git_is_work_tree),
            (kbpull, "git_has_upstream", kbpull.git_has_upstream),
            (kbpull, "git_is_clean", kbpull.git_is_clean),
            (kbpull, "_pull_preconditions", kbpull._pull_preconditions),
            (kbpull, "_kb_entry_files_changed", kbpull._kb_entry_files_changed),
            (kbpull, "_rebuild_kb_after_pull", kbpull._rebuild_kb_after_pull),
            (kbpull, "_skill_reconcile_after_pull",
             kbpull._skill_reconcile_after_pull),
            (kbpull, "rebuild_kb", kbpull.rebuild_kb),
            (kbpull, "build_index_from_frontmatter",
             kbpull.build_index_from_frontmatter),
            (kbpull, "require_knowledge_dir", kbpull.require_knowledge_dir),
            (kbpull, "_is_sync_candidate", kbpull._is_sync_candidate),
        ]
        for mod, name, val in self._saved:
            self.addCleanup(setattr, mod, name, val)


class GitPrimitivesTest(SeamRestoringTest):
    """`git_pull_ff` / `git_fetch` — the two non-interactive git invocations."""

    def setUp(self):
        SeamRestoringTest.setUp(self)
        self.calls = []
        kbpull._git = lambda t, *a, **k: (self.calls.append((t, a, k))
                                          or _CP(returncode=0))

    def test_pull_ff_refuses_to_create_a_merge_commit(self):
        """`--ff-only` is the whole safety property: real divergence is the
        push/rebase path's business, and a merge commit here would rewrite the
        KB's history behind the operator's back."""
        kbpull.git_pull_ff("/kb")
        _t, argv, _kw = self.calls[0]
        self.assertEqual(argv, ("pull", "--ff-only"))

    def test_pull_ff_never_prompts_for_credentials(self):
        kbpull.git_pull_ff("/kb")
        _t, _argv, kw = self.calls[0]
        self.assertEqual(kw["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_fetch_is_quiet_and_read_only(self):
        """A fetch updates only the remote-tracking refs, which is what makes
        it safe for a read-only `update --check` and a SessionStart nudge."""
        kbpull.git_fetch("/kb")
        _t, argv, kw = self.calls[0]
        self.assertEqual(argv, ("fetch", "--quiet"))
        self.assertEqual(kw["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_the_environment_is_a_copy_not_a_mutation_of_os_environ(self):
        before = os.environ.get("GIT_TERMINAL_PROMPT")
        kbpull.git_fetch("/kb")
        self.assertEqual(os.environ.get("GIT_TERMINAL_PROMPT"), before)


class PullPreconditionsTest(SeamRestoringTest):
    """`_pull_preconditions` — three guards whose ORDER decides which fact the
    operator is told, on a repo where more than one can be false at once."""

    def setUp(self):
        SeamRestoringTest.setUp(self)
        kbpull.git_is_work_tree = lambda t: True
        kbpull.git_has_upstream = lambda t: True
        kbpull.git_is_clean = lambda t: True

    def test_all_three_met_returns_true_and_prints_nothing(self):
        ok, out, err = _capture(kbpull._pull_preconditions, "/kb")
        self.assertTrue(ok)
        self.assertEqual((out, err), ("", ""))

    def test_a_non_work_tree_is_reported_first(self):
        kbpull.git_is_work_tree = lambda t: False
        kbpull.git_has_upstream = _raiser("upstream probe on a non-work-tree")
        ok, out, _err = _capture(kbpull._pull_preconditions, "/kb")
        self.assertFalse(ok)
        self.assertEqual(out, "kb-git pull: /kb is not a git work tree — "
                              "skipping pull (local-only).\n")

    def test_a_missing_upstream_wins_over_a_dirty_tree(self):
        """ORDER, on a fixture where BOTH are false: "you have no remote to
        pull from" is the actionable fact; reporting the dirty tree instead
        sends the operator to commit work that still cannot be pulled onto."""
        kbpull.git_has_upstream = lambda t: False
        kbpull.git_is_clean = lambda t: False
        ok, out, _err = _capture(kbpull._pull_preconditions, "/kb")
        self.assertFalse(ok)
        self.assertIn("no upstream tracking branch", out)
        self.assertNotIn("uncommitted", out)

    def test_a_dirty_tree_is_the_last_guard(self):
        kbpull.git_is_clean = lambda t: False
        ok, out, _err = _capture(kbpull._pull_preconditions, "/kb")
        self.assertFalse(ok)
        self.assertIn("work tree has uncommitted changes", out)
        self.assertIn("never pulling onto uncommitted writes", out)

    def test_the_clean_check_is_not_reached_without_an_upstream(self):
        kbpull.git_has_upstream = lambda t: False
        kbpull.git_is_clean = _raiser("clean probe without an upstream")
        self.assertFalse(_capture(kbpull._pull_preconditions, "/kb")[0])

    def test_every_notice_goes_to_stdout_not_stderr(self):
        """An unmet precondition is a graceful skip, not an error: the loop
        runs this at every agent start and a stderr line would read as a
        failure in every transcript."""
        for seam in ("git_is_work_tree", "git_has_upstream", "git_is_clean"):
            setattr(kbpull, seam, lambda t: False)
            _ok, out, err = _capture(kbpull._pull_preconditions, "/kb")
            self.assertEqual(err, "")
            self.assertTrue(out)
            setattr(kbpull, seam, lambda t: True)


class EntryFilesChangedTest(SeamRestoringTest):
    """`_kb_entry_files_changed` — the predicate that decides whether a pull
    made the DERIVED index stale."""

    def _diff(self, rc, names):
        kbpull._git = lambda t, *a, **k: _CP(returncode=rc,
                                             stdout="\n".join(names))

    def test_a_changed_entry_file_returns_true(self):
        self._diff(0, ["learnings/alpha.md"])
        self.assertTrue(kbpull._kb_entry_files_changed("/kb", "a", "b"))

    def test_a_git_error_conservatively_returns_true(self):
        """UNKNOWN must mean rebuild: a stale index silently under-reports the
        KB, and the cost of a needless rebuild is a few hundred milliseconds."""
        self._diff(1, [])
        self.assertTrue(kbpull._kb_entry_files_changed("/kb", "a", "b"))

    def test_a_non_markdown_change_returns_false(self):
        self._diff(0, ["learnings/notes.txt", "README.md"])
        self.assertFalse(kbpull._kb_entry_files_changed("/kb", "a", "b"))

    def test_a_file_outside_the_knowledge_subdirs_returns_false(self):
        self._diff(0, ["work/260813-x/plan.md"])
        self.assertFalse(kbpull._kb_entry_files_changed("/kb", "a", "b"))

    def test_a_roster_change_alone_returns_false(self):
        """Rosters are DERIVED. Rebuilding because a derived file changed would
        make every pull that carried one trigger a rebuild of itself."""
        self._diff(0, ["learnings/index.md"])
        self.assertFalse(kbpull._kb_entry_files_changed("/kb", "a", "b"))

    def test_quoted_paths_are_unquoted_before_classification(self):
        """git quotes a path containing unusual bytes; leaving the quotes on
        would push the subdir out of position and drop a real entry change."""
        self._diff(0, ['"learnings/wéird.md"'])
        self.assertTrue(kbpull._kb_entry_files_changed("/kb", "a", "b"))

    def test_one_entry_among_many_non_entries_still_returns_true(self):
        self._diff(0, ["README.md", "logs/x.log", "learnings/alpha.md"])
        self.assertTrue(kbpull._kb_entry_files_changed("/kb", "a", "b"))

    def test_the_two_refs_are_compared_as_a_range(self):
        seen = []
        kbpull._git = lambda t, *a, **k: (seen.append(a)
                                          or _CP(returncode=0, stdout=""))
        kbpull._kb_entry_files_changed("/kb", "OLD", "NEW")
        self.assertEqual(seen, [("diff", "--name-only", "OLD..NEW")])


class RebuildAfterPullTest(SeamRestoringTest):
    """`_rebuild_kb_after_pull` — best-effort, NEVER raises, and REPORTS a
    lossy sync rather than reporting a clean one."""

    def setUp(self):
        SeamRestoringTest.setUp(self)
        kbpull._kb_entry_files_changed = lambda t, a, b: True
        kbpull.rebuild_kb = lambda t: ({"entries": []}, {}, [])
        kbpull.build_index_from_frontmatter = lambda t: ({"entries": []}, [])

    def test_a_head_that_did_not_advance_does_no_work(self):
        kbpull._kb_entry_files_changed = _raiser("diff on a non-advancing pull")
        self.assertFalse(kbpull._rebuild_kb_after_pull("/kb", "aaa", "aaa"))

    def test_a_falsy_ref_on_either_side_does_no_work(self):
        kbpull._kb_entry_files_changed = _raiser("diff on an unknown head")
        self.assertFalse(kbpull._rebuild_kb_after_pull("/kb", "", "bbb"))
        self.assertFalse(kbpull._rebuild_kb_after_pull("/kb", "aaa", None))

    def test_no_entry_file_changed_skips_the_rebuild(self):
        kbpull._kb_entry_files_changed = lambda t, a, b: False
        kbpull.rebuild_kb = _raiser("rebuild with no entry change")
        self.assertFalse(kbpull._rebuild_kb_after_pull("/kb", "aaa", "bbb"))

    def test_a_clean_rebuild_returns_true(self):
        self.assertTrue(kbpull._rebuild_kb_after_pull("/kb", "aaa", "bbb"))

    def test_a_raising_rebuild_is_swallowed(self):
        """The contract is NEVER RAISES: a reconciliation that breaks the sync
        it follows is worse than the staleness it fixes."""
        kbpull.rebuild_kb = _raiser("rebuild")
        self.assertFalse(kbpull._rebuild_kb_after_pull("/kb", "aaa", "bbb"))

    def test_rebuild_errors_return_false_without_raising(self):
        kbpull.rebuild_kb = lambda t: ({"entries": []}, {}, ["bad fm"])
        self.assertFalse(kbpull._rebuild_kb_after_pull("/kb", "aaa", "bbb"))

    def test_a_none_data_result_returns_false(self):
        kbpull.rebuild_kb = lambda t: (None, {}, [])
        self.assertFalse(kbpull._rebuild_kb_after_pull("/kb", "aaa", "bbb"))

    def test_quarantined_entries_are_reported_loudly_on_stderr(self):
        """`rebuild_kb` DROPS a malformed / duplicate-id entry instead of
        failing, so without this warning a lossy pull reports a clean success
        and the entry is simply unrecallable with nobody told."""
        kbpull.build_index_from_frontmatter = lambda t: (
            {"entries": []}, ["learnings/dup.md: duplicate id"])
        rv, _out, err = _capture(kbpull._rebuild_kb_after_pull, "/kb", "a", "b")
        self.assertTrue(rv)
        self.assertIn("1 pulled entry QUARANTINED", err)
        self.assertIn("learnings/dup.md", err)

    def test_the_quarantine_warning_pluralizes(self):
        kbpull.build_index_from_frontmatter = lambda t: (
            {"entries": []}, ["a.md: x", "b.md: y"])
        _rv, _out, err = _capture(kbpull._rebuild_kb_after_pull, "/kb", "a", "b")
        self.assertIn("2 pulled entries QUARANTINED", err)

    def test_a_raising_quarantine_probe_still_returns_true(self):
        """The rebuild ALREADY succeeded at that point; failing the return here
        would report a successful reconciliation as a skipped one."""
        kbpull.build_index_from_frontmatter = _raiser("quarantine re-derive")
        self.assertTrue(kbpull._rebuild_kb_after_pull("/kb", "aaa", "bbb"))


class SkillReconcileAfterPullTest(SeamRestoringTest):
    """`_skill_reconcile_after_pull` — quarantine-only, never installs, never
    raises. Driven through the FUNCTION-LOCAL import it defers on purpose."""

    def _install(self, reconcile=None, lock=None):
        import types
        mod = types.ModuleType("agentware_lib.features.skillsync")

        @contextlib.contextmanager
        def _sync_lock():
            yield

        mod._skill_sync_lock = lock or _sync_lock
        mod._skill_reconcile = reconcile or (lambda k, l, c: None)
        saved = sys.modules.get("agentware_lib.features.skillsync")
        sys.modules["agentware_lib.features.skillsync"] = mod
        if saved is None:
            self.addCleanup(sys.modules.pop,
                            "agentware_lib.features.skillsync", None)
        else:
            self.addCleanup(sys.modules.__setitem__,
                            "agentware_lib.features.skillsync", saved)
        return mod

    def test_a_successful_reconcile_returns_true(self):
        self._install()
        self.assertTrue(kbpull._skill_reconcile_after_pull("/kb"))

    def test_the_knowledge_dir_is_forwarded_to_the_reconciler(self):
        seen = []
        self._install(reconcile=lambda k, l, c: seen.append(k))
        kbpull._skill_reconcile_after_pull("/kb")
        self.assertEqual(seen, ["/kb"])

    def test_a_raising_reconcile_is_swallowed(self):
        self._install(reconcile=_raiser("skill reconcile"))
        self.assertFalse(kbpull._skill_reconcile_after_pull("/kb"))

    def test_a_lock_that_cannot_be_taken_is_swallowed(self):
        """Two agents pulling at once must not turn a contended lock into a
        failed pull."""
        self._install(lock=_raiser("skill sync lock"))
        self.assertFalse(kbpull._skill_reconcile_after_pull("/kb"))


class CmdKbGitPullTest(SeamRestoringTest):
    """`cmd_kb_git_pull` — the handler, top to bottom."""

    def setUp(self):
        SeamRestoringTest.setUp(self)
        kbpull._pull_preconditions = lambda t: True
        kbpull._git_head = lambda t: "aaa"
        kbpull.git_pull_ff = lambda t: _CP(returncode=0,
                                           stdout="Already up to date.\n")
        kbpull._rebuild_kb_after_pull = lambda t, b, a: False
        kbpull._skill_reconcile_after_pull = lambda k: True

    def test_an_explicit_path_is_absolutized_and_expanded(self):
        seen = []
        kbpull._pull_preconditions = lambda t: seen.append(t) or False
        self.assertEqual(
            _capture(kbpull.cmd_kb_git_pull, _Args(path="~/kb"))[0], 0)
        self.assertEqual(seen, [os.path.expanduser("~/kb")])

    def test_an_explicit_path_bypasses_the_resolver(self):
        kbpull.require_knowledge_dir = _raiser("resolver reached despite --path")
        kbpull._pull_preconditions = lambda t: False
        self.assertEqual(
            _capture(kbpull.cmd_kb_git_pull, _Args(path="/kb"))[0], 0)

    def test_no_path_uses_the_resolved_knowledge_dir(self):
        seen = []
        kbpull.require_knowledge_dir = lambda: "/resolved"
        kbpull._pull_preconditions = lambda t: seen.append(t) or False
        _capture(kbpull.cmd_kb_git_pull, _Args(path=None))
        self.assertEqual(seen, ["/resolved"])

    def test_an_unresolvable_knowledge_dir_fails_loud(self):
        kbpull.require_knowledge_dir = lambda: None
        kbpull._pull_preconditions = _raiser("preconditions on an unnamed target")
        self.assertEqual(
            _capture(kbpull.cmd_kb_git_pull, _Args(path=None))[0], 1)

    def test_an_unmet_precondition_is_a_graceful_rc_zero_skip(self):
        kbpull._pull_preconditions = lambda t: False
        kbpull.git_pull_ff = _raiser("fetch on an unmet precondition")
        self.assertEqual(
            _capture(kbpull.cmd_kb_git_pull, _Args(path="/kb"))[0], 0)

    def test_a_failed_fetch_is_a_graceful_skip_naming_the_first_line(self):
        kbpull.git_pull_ff = lambda t: _CP(
            returncode=1, stderr="fatal: could not read\nhint: check\n")
        rc, out, _err = _capture(kbpull.cmd_kb_git_pull, _Args(path="/kb"))
        self.assertEqual(rc, 0)
        self.assertIn("skipped — fatal: could not read (offline or diverged",
                      out)

    def test_a_failed_fetch_with_no_stderr_names_the_step(self):
        kbpull.git_pull_ff = lambda t: _CP(returncode=1, stderr="")
        _rc, out, _err = _capture(kbpull.cmd_kb_git_pull, _Args(path="/kb"))
        self.assertIn("skipped — fetch failed", out)

    def test_a_failed_fetch_with_blank_stderr_names_the_step(self):
        kbpull.git_pull_ff = lambda t: _CP(returncode=1, stderr="  \n \n")
        _rc, out, _err = _capture(kbpull.cmd_kb_git_pull, _Args(path="/kb"))
        self.assertIn("skipped — fetch failed", out)

    def test_a_failed_fetch_skips_leading_blank_lines(self):
        kbpull.git_pull_ff = lambda t: _CP(returncode=1,
                                           stderr="\n\nreal first\n")
        _rc, out, _err = _capture(kbpull.cmd_kb_git_pull, _Args(path="/kb"))
        self.assertIn("skipped — real first", out)

    def test_a_failed_fetch_does_not_reconcile(self):
        kbpull.git_pull_ff = lambda t: _CP(returncode=1, stderr="offline")
        kbpull._rebuild_kb_after_pull = _raiser("rebuild after a failed fetch")
        kbpull._skill_reconcile_after_pull = _raiser("skills after a failed "
                                                     "fetch")
        self.assertEqual(
            _capture(kbpull.cmd_kb_git_pull, _Args(path="/kb"))[0], 0)

    def test_the_summary_is_the_first_line_of_stdout(self):
        kbpull.git_pull_ff = lambda t: _CP(
            returncode=0, stdout="Updating aaa..bbb\nFast-forward\n")
        _rc, out, _err = _capture(kbpull.cmd_kb_git_pull, _Args(path="/kb"))
        self.assertEqual(out, "kb-git pull: Updating aaa..bbb\n")

    def test_an_empty_stdout_falls_back_to_up_to_date(self):
        kbpull.git_pull_ff = lambda t: _CP(returncode=0, stdout="")
        _rc, out, _err = _capture(kbpull.cmd_kb_git_pull, _Args(path="/kb"))
        self.assertEqual(out, "kb-git pull: up to date\n")

    def test_a_blank_stdout_falls_back_to_up_to_date(self):
        kbpull.git_pull_ff = lambda t: _CP(returncode=0, stdout=" \n\t\n")
        _rc, out, _err = _capture(kbpull.cmd_kb_git_pull, _Args(path="/kb"))
        self.assertEqual(out, "kb-git pull: up to date\n")

    def test_a_rebuild_that_ran_is_appended_to_the_summary(self):
        kbpull._rebuild_kb_after_pull = lambda t, b, a: True
        _rc, out, _err = _capture(kbpull.cmd_kb_git_pull, _Args(path="/kb"))
        self.assertEqual(out, "kb-git pull: Already up to date. "
                              "(index rebuilt from pulled frontmatter)\n")

    def test_a_rebuild_that_did_not_run_appends_nothing(self):
        _rc, out, _err = _capture(kbpull.cmd_kb_git_pull, _Args(path="/kb"))
        self.assertEqual(out, "kb-git pull: Already up to date.\n")

    def test_the_head_is_read_before_and_after_the_fetch(self):
        """The rebuild predicate compares the two, so a `before` captured after
        the pull would make every fast-forward look like a no-op and the index
        would stay stale."""
        order = []
        kbpull._git_head = lambda t: order.append("head") or "aaa"
        kbpull.git_pull_ff = lambda t: (order.append("pull")
                                        or _CP(returncode=0, stdout="ok\n"))
        _capture(kbpull.cmd_kb_git_pull, _Args(path="/kb"))
        self.assertEqual(order, ["head", "pull", "head"])

    def test_the_skills_reconcile_runs_even_when_no_rebuild_was_needed(self):
        """It is a SEPARATE trigger: a pulled skill reaches the pending roster
        whether or not any knowledge entry changed."""
        seen = []
        kbpull._skill_reconcile_after_pull = lambda k: seen.append(k) or True
        _capture(kbpull.cmd_kb_git_pull, _Args(path="/kb"))
        self.assertEqual(seen, ["/kb"])

    def test_a_failing_skills_reconcile_does_not_change_the_result(self):
        kbpull._skill_reconcile_after_pull = lambda k: False
        rc, out, _err = _capture(kbpull.cmd_kb_git_pull, _Args(path="/kb"))
        self.assertEqual(rc, 0)
        self.assertIn("Already up to date.", out)

    def test_a_missing_path_attribute_is_read_as_absent(self):
        kbpull.require_knowledge_dir = lambda: "/resolved"
        seen = []
        kbpull._pull_preconditions = lambda t: seen.append(t) or False
        _capture(kbpull.cmd_kb_git_pull, _Args())
        self.assertEqual(seen, ["/resolved"])


@unittest.skipUnless(HAVE_GIT, "git not available")
class PullAgainstRealGitTest(unittest.TestCase):
    """One end-to-end row against real git, so the seam-driven rows above
    cannot all agree with each other while disagreeing with git."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-test-kbpull-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _run(self, cwd, *args):
        return subprocess.run(["git", "-C", cwd] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, check=True)

    def test_a_plain_directory_is_a_graceful_skip(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        rc, out, err = _capture(kbpull.cmd_kb_git_pull, _Args(path=plain))
        self.assertEqual(rc, 0)
        self.assertIn("is not a git work tree", out)
        self.assertEqual(err, "")

    def test_a_repo_without_an_upstream_is_a_graceful_skip(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        self._run(repo, "init", "-q", "-b", "main")
        self._run(repo, "config", "user.email", "test@example.com")
        self._run(repo, "config", "user.name", "Test")
        with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as f:
            f.write("seed\n")
        self._run(repo, "add", "-A")
        self._run(repo, "commit", "-q", "-m", "seed")
        rc, out, _err = _capture(kbpull.cmd_kb_git_pull, _Args(path=repo))
        self.assertEqual(rc, 0)
        self.assertIn("no upstream tracking branch", out)

    def test_the_module_reads_the_same_state_helpers_kbgit_publishes(self):
        """`ops.kbpull` imports `git_has_upstream` from `ops.kbgit` and the
        other two from `core.gitio`; a divergence between the two definitions
        would make the pull's preconditions disagree with `kb-git status`."""
        plain = os.path.join(self.tmp, "plain2")
        os.makedirs(plain)
        self.assertEqual(kbpull.git_has_upstream, kbgit.git_has_upstream)
        self.assertFalse(kbpull.git_is_work_tree(plain))
        self.assertFalse(kbgit.git_is_work_tree(plain))


class ModuleSurfaceTest(unittest.TestCase):
    """The re-export contract: `_EXPORTS` is imported BY NAME onto the facade."""

    def test_every_exported_name_exists(self):
        for name in kbpull._EXPORTS:
            self.assertTrue(hasattr(kbpull, name), "missing export: %s" % name)

    def test_all_mirrors_exports(self):
        self.assertEqual(list(kbpull.__all__), list(kbpull._EXPORTS))

    def test_the_precondition_helper_is_exported(self):
        self.assertIn("_pull_preconditions", kbpull._EXPORTS)


if __name__ == "__main__":
    unittest.main()
