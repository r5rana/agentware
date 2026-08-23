"""Owning unit file for `agentware_lib.ops.kbgit` — task 99 wave 8 (R-MOD-06).

The module had no granular owner before this commit. Its behaviour was reached
only through `test_kb_git*` (the staging/commit/pull/push WORKFLOW) and through
whatever else happened to shell git, so a break confined to these read-only
state helpers reddened somebody else's file, or nobody's.

The load-bearing half is `_first_line`, the shared reduction wave 8 extracted
from six sites across `ops.kbsync` and `ops.kbpull`. It is TWO-STAGE on
purpose — callers pass `cp.stderr or OUTER` as the text and a possibly
DIFFERENT `INNER` as the fallback — and the ONLY input that separates the two
stages is a stderr that is TRUTHY BUT BLANK. A single-fallback extraction
passes every ordinary test and silently rewrites two of the six notes, so both
directions are asserted here rather than assumed.

Every git-touching test drives a throwaway repo under `mkdtemp`: nothing here
reads the operator's checkout or knowledge base.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import json
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


def _have_git():
    try:
        subprocess.run(["git", "--version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
        return True
    except FileNotFoundError:  # pragma: no cover - environment dependent
        return False


HAVE_GIT = _have_git()


def _run(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=True)


class _Args(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _capture(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rv = fn(*a, **k)
    return rv, out.getvalue(), err.getvalue()


class FirstLineTest(unittest.TestCase):
    """`_first_line` — the shared two-stage reduction (wave 8's seam).

    Pure: no git, no filesystem. Each test names the caller shape it protects.
    """

    def test_returns_the_first_line_of_multi_line_text(self):
        self.assertEqual(
            kbgit._first_line("fatal: it broke\nhint: try again\n", "FB"),
            "fatal: it broke")

    def test_single_line_without_trailing_newline(self):
        self.assertEqual(kbgit._first_line("just this", "FB"), "just this")

    def test_empty_text_takes_the_fallback(self):
        self.assertEqual(kbgit._first_line("", "FB"), "FB")

    def test_none_text_takes_the_fallback(self):
        """Callers hand it `cp.stderr`, which a CompletedProcess stand-in (and
        a `capture_output=False` run) can leave as None."""
        self.assertEqual(kbgit._first_line(None, "FB"), "FB")

    def test_whitespace_only_text_takes_the_fallback(self):
        """THE row the two-stage contract exists for. A caller writes
        `_first_line(cp.stderr or OUTER, INNER)`: an ABSENT stderr never reaches
        here (OUTER was substituted upstream and comes back as the first line),
        so the fallback is reachable ONLY from a stderr that is truthy and
        blank — which is why OUTER and INNER may differ."""
        self.assertEqual(kbgit._first_line("   \n\t\n  ", "INNER"), "INNER")

    def test_the_two_stages_are_independently_selectable(self):
        """Absent vs blank must reach DIFFERENT strings when the caller asks
        for different strings. A single-fallback extraction collapses these two
        assertions into one answer and this test is the one that reddens."""
        self.assertEqual(kbgit._first_line("" or "OUTER", "INNER"), "OUTER")
        self.assertEqual(kbgit._first_line("  \n" or "OUTER", "INNER"), "INNER")

    def test_leading_blank_lines_are_skipped_not_returned(self):
        """`.strip()` already removes every leading line boundary, so the first
        element is never blank — the property that lets the filtered and
        unfiltered spellings of this idiom agree on every input."""
        self.assertEqual(kbgit._first_line("\n\n \nreal first\nsecond\n", "FB"),
                         "real first")

    def test_interior_blank_lines_do_not_become_the_answer(self):
        self.assertEqual(kbgit._first_line("first\n\n\nsecond", "FB"), "first")

    def test_exotic_line_boundaries_are_stripped_like_newlines(self):
        """`str.splitlines` splits on \\x0b/\\x0c/\\x85 too, and `str.strip`
        counts each as whitespace — so a text made only of them is BLANK, not a
        list of empty lines that would index a blank string out."""
        self.assertEqual(kbgit._first_line("\x0b\x0c\x85", "FB"), "FB")
        self.assertEqual(kbgit._first_line("\x0balpha", "FB"), "alpha")

    def test_fallback_is_returned_verbatim_including_empty_string(self):
        """One live caller passes "" as INNER, so an implementation that
        substituted a default for a falsy fallback would change that note."""
        self.assertEqual(kbgit._first_line("", ""), "")
        self.assertEqual(kbgit._first_line("  \n", ""), "")

    def test_the_line_is_returned_as_the_whole_text_strip_left_it(self):
        """The reduction strips the WHOLE TEXT, not each line — so a first line
        keeps its own trailing spacing when another line follows it, and loses
        it when it is also the last. Asserted rather than tidied because the
        six call sites interpolate the result into an operator-facing note, and
        `%s` renders whatever this returns."""
        self.assertEqual(kbgit._first_line("   a  b   \nrest", "FB"), "a  b   ")
        self.assertEqual(kbgit._first_line("   a  b   ", "FB"), "a  b")


class SanitizeCommitTagTest(unittest.TestCase):
    """`_sanitize_commit_tag` — the conventional-commit scope sanitizer."""

    def test_lowercases_and_keeps_allowed_characters(self):
        self.assertEqual(kbgit._sanitize_commit_tag("KB.Sync-v2_x"),
                         "kb.sync-v2_x")

    def test_collapses_disallowed_runs_into_one_dash(self):
        self.assertEqual(kbgit._sanitize_commit_tag("a  b//c"), "a-b-c")

    def test_strips_leading_and_trailing_punctuation(self):
        self.assertEqual(kbgit._sanitize_commit_tag("__feat__"), "feat")

    def test_empty_and_none_fall_back_to_kb(self):
        self.assertEqual(kbgit._sanitize_commit_tag(None), "kb")
        self.assertEqual(kbgit._sanitize_commit_tag("   "), "kb")

    def test_all_disallowed_falls_back_to_kb_rather_than_empty(self):
        """A tag of pure punctuation must not produce `chore(): …`."""
        self.assertEqual(kbgit._sanitize_commit_tag("///"), "kb")


@unittest.skipUnless(HAVE_GIT, "git not available")
class GitStateTest(unittest.TestCase):
    """The three state booleans, their dict shape, and the introspectors.

    Every one of them must DEGRADE on a non-repo rather than raise: the loop
    calls them on whatever the operator's knowledge dir happens to be.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-test-kbgit-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.plain = os.path.join(self.tmp, "plain")
        os.makedirs(self.plain)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        _run(self.repo, "init", "-q", "-b", "main")
        _run(self.repo, "config", "user.email", "test@example.com")
        _run(self.repo, "config", "user.name", "Test")
        with open(os.path.join(self.repo, "README.md"), "w",
                  encoding="utf-8") as f:
            f.write("seed\n")
        _run(self.repo, "add", "-A")
        _run(self.repo, "commit", "-q", "-m", "seed")

    def test_is_work_tree_true_inside_a_repo(self):
        self.assertTrue(kbgit.git_is_work_tree(self.repo))

    def test_is_work_tree_false_outside_and_does_not_raise(self):
        self.assertFalse(kbgit.git_is_work_tree(self.plain))

    def test_is_work_tree_false_on_a_path_that_does_not_exist(self):
        self.assertFalse(
            kbgit.git_is_work_tree(os.path.join(self.tmp, "absent")))

    def test_has_upstream_false_without_a_tracking_branch(self):
        self.assertFalse(kbgit.git_has_upstream(self.repo))

    def test_has_upstream_short_circuits_on_a_non_repo(self):
        """The work-tree guard comes FIRST, so a non-repo answers False without
        the `@{upstream}` rev-parse — which on a nested checkout would resolve
        against the PARENT repo and report a tracking branch this dir has not
        got."""
        self.assertFalse(kbgit.git_has_upstream(self.plain))

    def test_has_upstream_true_after_a_push_with_u(self):
        bare = os.path.join(self.tmp, "up.git")
        _run(self.tmp, "init", "-q", "--bare", bare)
        _run(bare, "symbolic-ref", "HEAD", "refs/heads/main")
        _run(self.repo, "remote", "add", "origin", bare)
        _run(self.repo, "push", "-q", "-u", "origin", "main")
        self.assertTrue(kbgit.git_has_upstream(self.repo))

    def test_is_clean_true_on_a_committed_tree(self):
        self.assertTrue(kbgit.git_is_clean(self.repo))

    def test_is_clean_false_with_an_untracked_file(self):
        """UNTRACKED counts as dirty: `status --porcelain` lists it, and the
        pull precondition must refuse to fast-forward over one."""
        with open(os.path.join(self.repo, "new.md"), "w",
                  encoding="utf-8") as f:
            f.write("x\n")
        self.assertFalse(kbgit.git_is_clean(self.repo))

    def test_is_clean_false_with_a_modified_tracked_file(self):
        with open(os.path.join(self.repo, "README.md"), "a",
                  encoding="utf-8") as f:
            f.write("more\n")
        self.assertFalse(kbgit.git_is_clean(self.repo))

    def test_is_clean_false_on_a_non_repo(self):
        self.assertFalse(kbgit.git_is_clean(self.plain))

    def test_status_dict_shape_on_a_repo(self):
        self.assertEqual(kbgit.git_status(self.repo),
                         {"is_work_tree": True, "has_upstream": False,
                          "is_clean": True})

    def test_status_forces_the_other_two_false_on_a_non_repo(self):
        """The SHAPE is the contract: three keys whatever the target, so a
        consumer never has to distinguish absent from False."""
        self.assertEqual(kbgit.git_status(self.plain),
                         {"is_work_tree": False, "has_upstream": False,
                          "is_clean": False})

    def test_toplevel_is_the_realpath_of_the_repo_root(self):
        sub = os.path.join(self.repo, "learnings")
        os.makedirs(sub)
        self.assertEqual(kbgit.git_toplevel(sub),
                         os.path.realpath(self.repo))

    def test_toplevel_is_none_outside_a_repo(self):
        """`None` rather than "" is what makes the C-3 package-repo guard
        (`top and pkg_top and top == pkg_top`) fall through instead of matching
        two unresolvable targets to each other."""
        self.assertIsNone(kbgit.git_toplevel(self.plain))

    def test_changed_paths_lists_staged_unstaged_and_untracked(self):
        with open(os.path.join(self.repo, "README.md"), "a",
                  encoding="utf-8") as f:
            f.write("edit\n")
        with open(os.path.join(self.repo, "untracked.md"), "w",
                  encoding="utf-8") as f:
            f.write("u\n")
        with open(os.path.join(self.repo, "staged.md"), "w",
                  encoding="utf-8") as f:
            f.write("s\n")
        _run(self.repo, "add", "staged.md")
        self.assertEqual(kbgit.git_changed_paths(self.repo),
                         ["README.md", "staged.md", "untracked.md"])

    def test_changed_paths_is_empty_on_a_clean_tree(self):
        self.assertEqual(kbgit.git_changed_paths(self.repo), [])

    def test_changed_paths_is_empty_on_a_non_repo(self):
        self.assertEqual(kbgit.git_changed_paths(self.plain), [])

    def test_changed_paths_takes_the_destination_of_a_rename(self):
        """A rename must contribute the NEW path: the commit summary names what
        exists now, and staging the old one would fail."""
        _run(self.repo, "mv", "README.md", "RENAMED.md")
        self.assertEqual(kbgit.git_changed_paths(self.repo), ["RENAMED.md"])

    def test_tracked_paths_reports_only_what_git_tracks(self):
        with open(os.path.join(self.repo, "untracked.md"), "w",
                  encoding="utf-8") as f:
            f.write("u\n")
        self.assertEqual(
            kbgit.git_tracked_paths(self.repo,
                                    ["README.md", "untracked.md"]),
            ["README.md"])

    def test_tracked_paths_is_empty_for_an_empty_request(self):
        """The early return matters: `ls-files -- ` with no pathspec would list
        the WHOLE index, so the de-commit step would untrack everything."""
        self.assertEqual(kbgit.git_tracked_paths(self.repo, []), [])

    def test_tracked_paths_is_empty_on_a_non_repo(self):
        self.assertEqual(
            kbgit.git_tracked_paths(self.plain, ["README.md"]), [])


@unittest.skipUnless(HAVE_GIT, "git not available")
class KbGitStatusCommandTest(unittest.TestCase):
    """`cmd_kb_git_status` — the read-only CLI handler."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-test-kbstatus-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        _run(self.repo, "init", "-q", "-b", "main")
        _run(self.repo, "config", "user.email", "test@example.com")
        _run(self.repo, "config", "user.name", "Test")
        self._saved = [(kbgit, "config", kbgit.config)]
        for mod, name, val in self._saved:
            self.addCleanup(setattr, mod, name, val)

    def test_text_format_prints_the_three_booleans(self):
        rc, out, _err = _capture(kbgit.cmd_kb_git_status,
                                 _Args(path=self.repo, format="text"))
        self.assertEqual(rc, 0)
        self.assertIn("is_work_tree: yes", out)
        self.assertIn("has_upstream: no", out)
        self.assertIn("is_clean:     yes", out)

    def test_json_format_is_parseable_and_matches_git_status(self):
        rc, out, _err = _capture(kbgit.cmd_kb_git_status,
                                 _Args(path=self.repo, format="json"))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), kbgit.git_status(self.repo))

    def test_missing_format_attribute_defaults_to_text(self):
        """The handler reads `getattr(args, "format", "text")`, so a caller
        that never registered the flag must still get output."""
        rc, out, _err = _capture(kbgit.cmd_kb_git_status, _Args(path=self.repo))
        self.assertEqual(rc, 0)
        self.assertIn("is_work_tree:", out)

    def test_path_override_wins_over_the_resolved_knowledge_dir(self):
        class _Cfg(object):
            @staticmethod
            def require_knowledge_dir():
                raise AssertionError("resolver reached despite --path")
        kbgit.config = _Cfg()
        rc, out, _err = _capture(kbgit.cmd_kb_git_status,
                                 _Args(path=self.repo, format="text"))
        self.assertEqual(rc, 0)
        self.assertIn("is_work_tree: yes", out)

    def test_no_path_and_no_knowledge_dir_fails_loud(self):
        class _Cfg(object):
            @staticmethod
            def require_knowledge_dir():
                return None
        kbgit.config = _Cfg()
        rc, out, _err = _capture(kbgit.cmd_kb_git_status,
                                 _Args(path=None, format="text"))
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")

    def test_no_path_uses_the_resolved_knowledge_dir(self):
        class _Cfg(object):
            repo = self.repo

            @classmethod
            def require_knowledge_dir(cls):
                return cls.repo
        kbgit.config = _Cfg()
        rc, out, _err = _capture(kbgit.cmd_kb_git_status,
                                 _Args(path=None, format="json"))
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["is_work_tree"])

    def test_user_path_is_expanded(self):
        """`--path ~/kb` must resolve; the handler expanduser()s before use."""
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        saved = os.environ.get("HOME")
        os.environ["HOME"] = home
        if saved is None:
            self.addCleanup(os.environ.pop, "HOME", None)
        else:
            self.addCleanup(os.environ.__setitem__, "HOME", saved)
        rc, out, _err = _capture(kbgit.cmd_kb_git_status,
                                 _Args(path="~", format="text"))
        self.assertEqual(rc, 0)
        self.assertIn("is_work_tree: no", out)


class ModuleSurfaceTest(unittest.TestCase):
    """The re-export contract: `_EXPORTS` is imported BY NAME onto the facade,
    so a name that is listed but absent (or defined but unlisted) breaks the
    bare-name resolution of ~10 dependents rather than this module."""

    def test_every_exported_name_exists(self):
        for name in kbgit._EXPORTS:
            self.assertTrue(hasattr(kbgit, name), "missing export: %s" % name)

    def test_all_mirrors_exports(self):
        self.assertEqual(list(kbgit.__all__), list(kbgit._EXPORTS))

    def test_first_line_is_exported(self):
        """Wave 8's shared helper is reached from `ops.kbpull` by NAME import
        and from `ops.kbsync` through the module alias; dropping it from the
        surface breaks the facade's bare-name re-export."""
        self.assertIn("_first_line", kbgit._EXPORTS)


if __name__ == "__main__":
    unittest.main()
