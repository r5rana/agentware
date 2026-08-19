"""Task 57 — the de-commit sweep must never overflow argv.

WHY THIS FILE EXISTS. While `_decommit_derived` swept a FIXED set of nine derived
files it could not outgrow a command line, so splatting the list into `git rm
--cached` was safe by construction. Task 57 widened the sweep to the whole declared
ignore contract, which includes DIRECTORY rules (`logs/`, `.loop/`, `.cache/`), and
a directory pathspec enumerates whatever the repository tracks beneath it. That
made the list unbounded, and the operator's own knowledge base already carries
11,389 files under `logs/` (887 KB of paths) against a 1 MiB `ARG_MAX` that is
shared with the environment block.

REPRODUCED, not supposed: a legacy KB with 14,000 tracked log files raised
`OSError: [Errno 7] Argument list too long` from the splat. That is strictly worse
than the bug the sweep was written to fix — an ignored pathspec makes `git add`
exit `rc=1`, while this is an unhandled exception out of `kb-git commit` — and it
would land on exactly the large, long-lived tree the migration exists for.

The locks below assert the OUTCOME (how many times git was invoked, how large each
invocation was, and that every path was still untracked) rather than the batcher's
own bookkeeping, so they redden if the splat is ever restored.

Stdlib-only unittest; every scenario runs against a throwaway temp repo (R-LOC-03).
"""

import os
import shutil
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import load_cli
except ImportError:  # discover -s tests
    from _fixtures import load_cli


CLI = load_cli()

from agentware_lib.core import gitio
from agentware_lib.core.gitio import git_pathspec_batches
from agentware_lib.ops import staging


def _have_git():
    try:
        subprocess.run(["git", "--version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
        return True
    except FileNotFoundError:
        return False


HAVE_GIT = _have_git()


def _git(cwd, *args, check=True):
    return subprocess.run(["git", "-C", cwd] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=check)


def _argv_bytes(paths):
    return sum(len(str(p).encode("utf-8")) + 1 for p in paths)


class PathspecBatchesTest(unittest.TestCase):
    """The batcher itself: budget respected, nothing dropped, nothing reordered."""

    def test_batching_empty_yields_nothing(self):
        self.assertEqual(list(git_pathspec_batches([])), [])

    def test_batching_small_list_is_a_single_batch(self):
        paths = ["a.md", "b.md", "c.md"]
        self.assertEqual(list(git_pathspec_batches(paths)), [paths])

    def test_batching_preserves_every_path_exactly_once_and_in_order(self):
        paths = ["logs/sessions/%04d.md" % i for i in range(5000)]
        batches = list(git_pathspec_batches(paths, max_bytes=4096))
        self.assertGreater(len(batches), 1, "budget did not force a split")
        flat = [p for b in batches for p in b]
        self.assertEqual(flat, paths, "batching lost, duplicated or reordered a path")

    def test_batching_every_batch_is_within_the_byte_budget(self):
        paths = ["logs/sessions/%04d.md" % i for i in range(5000)]
        for batch in git_pathspec_batches(paths, max_bytes=4096):
            self.assertLessEqual(_argv_bytes(batch), 4096,
                                 "batch exceeded its byte budget")

    def test_batching_respects_the_count_cap(self):
        paths = ["%d" % i for i in range(2500)]
        for batch in git_pathspec_batches(paths, max_bytes=10 ** 9, max_count=100):
            self.assertLessEqual(len(batch), 100)

    def test_batching_yields_an_over_long_path_alone_rather_than_dropping_it(self):
        # Silently skipping a path would leave a real file tracked forever, which
        # is a worse failure than handing the kernel one long argument.
        huge = "x" * 5000
        batches = list(git_pathspec_batches(["a.md", huge, "b.md"], max_bytes=100))
        flat = [p for b in batches for p in b]
        self.assertIn(huge, flat)
        self.assertEqual(flat, ["a.md", huge, "b.md"])
        self.assertIn([huge], batches, "the over-long path was not isolated")

    def test_batching_default_budget_leaves_headroom_under_arg_max(self):
        # The reproduction raised at ~1.30 MB. A default anywhere near the
        # platform ceiling would be a lock that passes and still breaks live.
        self.assertLessEqual(gitio.GIT_ARGV_BATCH_BYTES, 256 * 1024)


@unittest.skipUnless(HAVE_GIT, "git not available")
class DecommitCallSiteBatchingTest(unittest.TestCase):
    """The CALL SITE: `_decommit_derived` must batch, and still untrack everything.

    Records every `git` invocation the sweep makes and asserts each one fits the
    budget. With the splat restored this reddens twice over — one invocation, far
    over budget — while the completeness assertions keep the lock from being
    satisfiable by a batcher that simply drops paths.
    """

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="aw-argv-batch-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "test@example.com")
        _git(self.repo, "config", "user.name", "Test")
        os.makedirs(os.path.join(self.repo, "logs", "sessions"))
        self.names = ["logs/sessions/transcript-%04d.md" % i for i in range(400)]
        for rel in self.names:
            with open(os.path.join(self.repo, rel), "w", encoding="utf-8") as f:
                f.write("transcript\n")
        os.makedirs(os.path.join(self.repo, "learnings"))
        with open(os.path.join(self.repo, "learnings", "a.md"), "w",
                  encoding="utf-8") as f:
            f.write("---\nid: a\ncategory: learnings\n---\n\nseed\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "legacy: run state tracked")
        self.assertIn(self.names[0], _git(self.repo, "ls-files").stdout)

    def _sweep_recording_argv(self, budget):
        """Run the sweep with a shrunk budget, capturing each git argv."""
        seen = []
        real_git = staging._git

        def spy(target, *args, **kwargs):
            seen.append(list(args))
            return real_git(target, *args, **kwargs)

        real_batches = staging.git_pathspec_batches

        def small_batches(paths, **kw):
            return real_batches(paths, max_bytes=budget)

        staging._git = spy
        staging.git_pathspec_batches = small_batches
        try:
            untracked = staging._decommit_derived(self.repo)
        finally:
            staging._git = real_git
            staging.git_pathspec_batches = real_batches
        return untracked, seen

    def test_batching_decommit_splits_into_several_bounded_invocations(self):
        budget = 2048
        untracked, seen = self._sweep_recording_argv(budget)
        rm_calls = [a for a in seen if a[:2] == ["rm", "--cached"]]
        self.assertGreater(len(rm_calls), 1,
                           "the sweep issued a single unbounded `git rm`")
        for argv in rm_calls:
            paths = argv[4:]  # rm --cached --quiet --ignore-unmatch <paths...>
            self.assertLessEqual(
                _argv_bytes(paths), budget,
                "a `git rm` invocation exceeded the argv budget (%d paths)"
                % len(paths))
        # ...and the split cost nothing: every path was still swept.
        self.assertEqual(len(untracked), len(self.names) + 0,
                         "sweep returned a different number of paths than tracked")

    def test_batching_decommit_untracks_all_and_preserves_local_files(self):
        _untracked, _seen = self._sweep_recording_argv(2048)
        tracked = set(_git(self.repo, "ls-files").stdout.split("\n")) - {""}
        for rel in self.names:
            self.assertNotIn(rel, tracked, "batched sweep left %s tracked" % rel)
            self.assertTrue(
                os.path.isfile(os.path.join(self.repo, rel)),
                "batched sweep deleted the local file: %s" % rel)
        self.assertIn("learnings/a.md", tracked,
                      "batched sweep untracked real knowledge")

    def test_batching_commit_path_survives_a_large_legacy_run_state(self):
        # End to end through the real verb, not the helper: the whole point is
        # that `kb-git commit` no longer dies on a large legacy knowledge base.
        from tests._fixtures import run_cli
        with open(os.path.join(self.repo, "learnings", "b.md"), "w",
                  encoding="utf-8") as f:
            f.write("---\nid: b\ncategory: learnings\n---\n\nnew\n")
        code, _out, err = run_cli(
            ["kb-git", "commit", "--path", self.repo], self.repo)
        self.assertEqual(code, 0, "commit failed on a large legacy KB: %s" % err)
        tracked = set(_git(self.repo, "ls-files").stdout.split("\n")) - {""}
        self.assertIn("learnings/b.md", tracked)
        self.assertNotIn(self.names[0], tracked)


@unittest.skipUnless(HAVE_GIT, "git not available")
class StageBatchingTest(unittest.TestCase):
    """`_git_add_batched` splits, stages everything, and reports the first error.

    The staging half of the same defect class: `allow` is derived from what
    CHANGED, so a first commit of a large knowledge base stages every file, as
    ABSOLUTE paths. Not yet reachable at the operator's ~2k tracked files (~207 KB
    of argv), which is why this is a lock rather than a reproduction — but it is
    the identical shape one line from the one that did overflow.
    """

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="aw-add-batch-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "test@example.com")
        _git(self.repo, "config", "user.name", "Test")
        os.makedirs(os.path.join(self.repo, "learnings"))
        self.rels = ["learnings/note-%04d.md" % i for i in range(300)]
        for rel in self.rels:
            with open(os.path.join(self.repo, rel), "w", encoding="utf-8") as f:
                f.write("---\nid: n\ncategory: learnings\n---\n\nbody\n")

    def test_batching_add_stages_every_path_across_batches(self):
        abs_paths = [os.path.join(self.repo, r) for r in self.rels]
        real_batches = staging.git_pathspec_batches
        seen = []

        def small(paths, **kw):
            for b in real_batches(paths, max_bytes=1024):
                seen.append(b)
                yield b

        staging.git_pathspec_batches = small
        try:
            err = staging._git_add_batched(self.repo, abs_paths)
        finally:
            staging.git_pathspec_batches = real_batches
        self.assertIsNone(err, "batched add reported an error: %s" % err)
        self.assertGreater(len(seen), 1, "add was not split into batches")
        staged = set(_git(self.repo, "diff", "--cached", "--name-only").stdout.split())
        for rel in self.rels:
            self.assertIn(rel, staged, "batched add missed %s" % rel)

    def test_batching_add_reports_the_failure_instead_of_swallowing_it(self):
        # A path outside the repo makes `git add` fail; the helper must surface it
        # rather than return None and let an empty commit look like success.
        err = staging._git_add_batched(self.repo, ["/definitely/not/here.md"])
        self.assertIsNotNone(err, "a failing `git add` was swallowed")
        self.assertTrue(err.strip(), "error string was empty")


if __name__ == "__main__":
    unittest.main()
