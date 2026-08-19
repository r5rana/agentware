"""Tests for the knowledge-base git sync (feature 260625-kb-git-sync).

Stdlib-only (unittest). Every git scenario runs against a THROWAWAY temp repo
created inside the test (tempfile.mkdtemp) — NEVER the operator's real $KDIR.
Conflict/merge scenarios use throwaway clones of a temp bare remote.

If `git` is not on PATH the git-dependent tests skip cleanly.
"""

from tests.unit.ops._kb_git_support import (  # noqa: F401
    HAVE_GIT, _commit_count, _head_subject, _init_repo, _run,
    build_synthetic_kb, load_cli, os, run_cli, shutil, tempfile, types,
    unittest)


@unittest.skipUnless(HAVE_GIT, "git not available")
class KbGitDetectionTest(unittest.TestCase):
    """Task 1.1 — KB git detection helper reports correct booleans."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-test-kbgit-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cli = load_cli()

    def _mk(self, name):
        return os.path.join(self.tmp, name)

    def test_non_repo_dir(self):
        plain = self._mk("plain")
        os.makedirs(plain)
        st = self.cli.git_status(plain)
        self.assertFalse(st["is_work_tree"])
        self.assertFalse(st["has_upstream"])
        self.assertFalse(st["is_clean"])

    def test_tracked_no_remote(self):
        repo = _init_repo(self._mk("local"))
        st = self.cli.git_status(repo)
        self.assertTrue(st["is_work_tree"])
        self.assertFalse(st["has_upstream"])  # no upstream configured
        self.assertTrue(st["is_clean"])       # committed, nothing pending

    def test_tracked_with_remote(self):
        # Bare remote + a clone whose branch tracks origin/main.
        bare = self._mk("remote.git")
        _run(self.tmp, "init", "-q", "--bare", bare)
        _run(bare, "symbolic-ref", "HEAD", "refs/heads/main")
        seed = _init_repo(self._mk("seed"))
        _run(seed, "remote", "add", "origin", bare)
        _run(seed, "push", "-q", "-u", "origin", "main")
        clone = self._mk("clone")
        _run(self.tmp, "clone", "-q", bare, clone)
        _run(clone, "config", "user.email", "test@example.com")
        _run(clone, "config", "user.name", "Test")

        st = self.cli.git_status(clone)
        self.assertTrue(st["is_work_tree"])
        self.assertTrue(st["has_upstream"])
        self.assertTrue(st["is_clean"])

    def test_dirty_tree_not_clean(self):
        repo = _init_repo(self._mk("dirty"))
        with open(os.path.join(repo, "new.txt"), "w", encoding="utf-8") as f:
            f.write("uncommitted\n")
        st = self.cli.git_status(repo)
        self.assertTrue(st["is_work_tree"])
        self.assertFalse(st["is_clean"])

    def test_cli_status_json(self):
        repo = _init_repo(self._mk("cli"))
        # Build a synthetic KB inside the repo so the CLI has a valid target;
        # drive the kb-git status subcommand with --path override.
        build_synthetic_kb(repo)
        code, out, err = run_cli(
            ["kb-git", "status", "--path", repo, "--format", "json"], repo)
        self.assertEqual(code, 0, err)
        import json
        st = json.loads(out)
        self.assertTrue(st["is_work_tree"])
        self.assertIn("has_upstream", st)
        self.assertIn("is_clean", st)

@unittest.skipUnless(HAVE_GIT, "git not available")
class KbGitCommitTest(unittest.TestCase):
    """Task 2.1 — opt-in, KB-repo-only end-of-work commit."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-test-kbcommit-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cli = load_cli()

    def _mk(self, name):
        return os.path.join(self.tmp, name)

    def test_dirty_tree_makes_exactly_one_commit(self):
        repo = _init_repo(self._mk("kb"))
        before = _commit_count(repo)
        with open(os.path.join(repo, "learnings.md"), "w", encoding="utf-8") as f:
            f.write("a new learning\n")
        # The shell strips the leading YYMMDD- date before passing --tag.
        code, out, err = run_cli(
            ["kb-git", "commit", "--path", repo, "--tag", "kb-git-sync"], repo)
        self.assertEqual(code, 0, err)
        self.assertEqual(_commit_count(repo), before + 1)  # exactly ONE commit
        self.assertRegex(_head_subject(repo), r"^chore\(kb-git-sync\): ")
        self.assertEqual(self.cli.git_changed_paths(repo), [])  # tree now clean

    def test_clean_tree_is_noop(self):
        repo = _init_repo(self._mk("clean"))
        before = _commit_count(repo)
        code, out, err = run_cli(["kb-git", "commit", "--path", repo], repo)
        self.assertEqual(code, 0, err)
        self.assertEqual(_commit_count(repo), before)  # no new commit
        self.assertIn("nothing to commit", out)

    def test_non_repo_is_noop_local_only(self):
        plain = self._mk("plain")
        os.makedirs(plain)
        code, out, err = run_cli(["kb-git", "commit", "--path", plain], plain)
        self.assertEqual(code, 0, err)  # C-4: degrade, never block
        self.assertIn("not a git work tree", out)

    def test_custom_type_and_message(self):
        repo = _init_repo(self._mk("custom"))
        with open(os.path.join(repo, "note.md"), "w", encoding="utf-8") as f:
            f.write("x\n")
        code, out, err = run_cli(
            ["kb-git", "commit", "--path", repo, "--type", "feat",
             "--tag", "Recall", "--message", "add recall learning"], repo)
        self.assertEqual(code, 0, err)
        # tag is sanitized to lowercase; type honored; message verbatim.
        self.assertEqual(_head_subject(repo), "feat(recall): add recall learning")

    def test_knowledge_topics_helper(self):
        """kb_changed_knowledge_topics maps staged paths -> sorted entry topics."""
        topics = self.cli.kb_changed_knowledge_topics([
            "learnings/foo.md", "skills/bar/SKILL.md", "references/x.md",
            "MAIN.md", "index.json", "skills/baz.md", "learnings/foo.md",
        ])
        self.assertEqual(topics, ["bar", "baz", "foo"])  # deduped + sorted

    def test_default_message_names_changed_learnings(self):
        """No --message + changed learnings -> subject names the topics (not dir list)."""
        repo = _init_repo(self._mk("learn"))
        os.makedirs(os.path.join(repo, "learnings"))
        for t in ("alpha-topic", "beta-topic"):
            with open(os.path.join(repo, "learnings", t + ".md"),
                      "w", encoding="utf-8") as f:
                f.write("# %s\n" % t)
        _run(repo, "add", "-A")  # tracked dirs surface entries individually (real KB)
        code, out, err = run_cli(
            ["kb-git", "commit", "--path", repo, "--tag", "msgfix"], repo)
        self.assertEqual(code, 0, err)
        subj = _head_subject(repo)
        self.assertIn("alpha-topic", subj)
        self.assertIn("beta-topic", subj)
        self.assertNotIn("sync ", subj)  # NOT the generic dir-list boilerplate
        self.assertRegex(subj, r"^chore\(msgfix\): learnings: ")

    def test_message_given_appends_learning_topics(self):
        """A caller --message keeps its title AND gains a compact learnings suffix."""
        repo = _init_repo(self._mk("suffix"))
        os.makedirs(os.path.join(repo, "learnings"))
        with open(os.path.join(repo, "learnings", "gamma.md"),
                  "w", encoding="utf-8") as f:
            f.write("# gamma\n")
        _run(repo, "add", "-A")
        code, out, err = run_cli(
            ["kb-git", "commit", "--path", repo, "--type", "feat",
             "--tag", "msgfix", "--message", "my feature title"], repo)
        self.assertEqual(code, 0, err)
        subj = _head_subject(repo)
        self.assertTrue(subj.startswith("feat(msgfix): my feature title"))
        self.assertIn("gamma", subj)
        self.assertRegex(subj, r"^(feat|chore)\([a-z0-9._-]+\): .+")

    def test_no_knowledge_falls_back_to_dir_list(self):
        """No knowledge entries -> graceful fallback to the dir-list summary (C-2)."""
        repo = _init_repo(self._mk("fallback"))
        os.makedirs(os.path.join(repo, "references"))
        with open(os.path.join(repo, "references", "x.md"),
                  "w", encoding="utf-8") as f:
            f.write("ref\n")
        code, out, err = run_cli(
            ["kb-git", "commit", "--path", repo, "--tag", "msgfix"], repo)
        self.assertEqual(code, 0, err)
        subj = _head_subject(repo)
        self.assertIn("sync", subj)
        self.assertIn("references", subj)
        self.assertRegex(subj, r"^(feat|chore)\([a-z0-9._-]+\): .+")

    def test_overlong_inputs_truncate_to_one_valid_subject(self):
        """Many topics -> capped (+N more) AND clamped to one valid subject line."""
        repo = _init_repo(self._mk("trunc"))
        os.makedirs(os.path.join(repo, "learnings"))
        for i in range(12):
            with open(os.path.join(repo, "learnings", "t%02d.md" % i),
                      "w", encoding="utf-8") as f:
                f.write("x\n")
        _run(repo, "add", "-A")
        code, out, err = run_cli(
            ["kb-git", "commit", "--path", repo, "--tag", "msgfix"], repo)
        self.assertEqual(code, 0, err)
        subj = _head_subject(repo)
        self.assertLessEqual(len(subj), 100)            # one sane line (C-1)
        self.assertNotIn("\n", subj)
        self.assertIn("more", subj)                     # tail collapsed to +N more
        self.assertRegex(subj, r"^chore\(msgfix\): learnings: ")

    def test_c3_refuses_package_repo(self):
        # Simulate the target resolving to the agentware package repo: point the
        # module's REPO_ROOT at the SAME repo as the target so the C-3 toplevel
        # guard trips. Hermetic — never touches the real package.
        repo = _init_repo(self._mk("pkg"))
        with open(os.path.join(repo, "f.md"), "w", encoding="utf-8") as f:
            f.write("y\n")
        before = _commit_count(repo)
        import types
        saved = self.cli.REPO_ROOT
        self.cli.REPO_ROOT = repo
        try:
            ns = types.SimpleNamespace(
                path=repo, tag=None, type="chore", message=None)
            rc = self.cli.cmd_kb_git_commit(ns)
        finally:
            self.cli.REPO_ROOT = saved
        self.assertEqual(rc, 1)  # refused
        self.assertEqual(_commit_count(repo), before)  # nothing committed

@unittest.skipUnless(HAVE_GIT, "git not available")
class KbGitPullTest(unittest.TestCase):
    """Task 3.1 — pull cadence: fast-forward from upstream at safe points."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-test-kbpull-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cli = load_cli()

    def _mk(self, name):
        return os.path.join(self.tmp, name)

    def _clone_tracking(self, name):
        """A bare remote + a seed pushing main + a clone tracking origin/main."""
        bare = self._mk(name + ".git")
        _run(self.tmp, "init", "-q", "--bare", bare)
        _run(bare, "symbolic-ref", "HEAD", "refs/heads/main")
        seed = _init_repo(self._mk(name + "-seed"))
        _run(seed, "remote", "add", "origin", bare)
        _run(seed, "push", "-q", "-u", "origin", "main")
        clone = self._mk(name + "-clone")
        _run(self.tmp, "clone", "-q", bare, clone)
        _run(clone, "config", "user.email", "test@example.com")
        _run(clone, "config", "user.name", "Test")
        return bare, seed, clone

    def test_non_repo_is_noop_local_only(self):
        plain = self._mk("plain")
        os.makedirs(plain)
        code, out, err = run_cli(["kb-git", "pull", "--path", plain], plain)
        self.assertEqual(code, 0, err)  # C-4: degrade, never block
        self.assertIn("not a git work tree", out)

    def test_no_upstream_skips(self):
        repo = _init_repo(self._mk("local"))  # tracked, no remote/upstream
        code, out, err = run_cli(["kb-git", "pull", "--path", repo], repo)
        self.assertEqual(code, 0, err)
        self.assertIn("no upstream", out)

    def test_dirty_tree_skips(self):
        _bare, _seed, clone = self._clone_tracking("dirty")
        with open(os.path.join(clone, "wip.md"), "w", encoding="utf-8") as f:
            f.write("uncommitted work\n")
        code, out, err = run_cli(["kb-git", "pull", "--path", clone], clone)
        self.assertEqual(code, 0, err)
        self.assertIn("uncommitted", out)

    def test_clean_up_to_date(self):
        _bare, _seed, clone = self._clone_tracking("uptodate")
        code, out, err = run_cli(["kb-git", "pull", "--path", clone], clone)
        self.assertEqual(code, 0, err)  # already current, clean no-op

    def test_fast_forward_pull(self):
        _bare, seed, clone = self._clone_tracking("ff")
        # The seed adds + pushes a new commit AFTER the clone was taken.
        with open(os.path.join(seed, "extra.md"), "w", encoding="utf-8") as f:
            f.write("more knowledge\n")
        _run(seed, "add", "-A")
        _run(seed, "commit", "-q", "-m", "extra")
        _run(seed, "push", "-q")
        self.assertFalse(os.path.exists(os.path.join(clone, "extra.md")))
        code, out, err = run_cli(["kb-git", "pull", "--path", clone], clone)
        self.assertEqual(code, 0, err)
        # Fast-forwarded: the new file is now present in the clone.
        self.assertTrue(os.path.exists(os.path.join(clone, "extra.md")))

    def test_unreachable_remote_skips_gracefully(self):
        _bare, _seed, clone = self._clone_tracking("offline")
        # Point origin at a non-existent path to simulate offline; the branch
        # still tracks origin/main so the fetch is attempted and fails fast.
        _run(clone, "remote", "set-url", "origin", self._mk("nope.git"))
        code, out, err = run_cli(["kb-git", "pull", "--path", clone], clone)
        self.assertEqual(code, 0, err)  # graceful skip, never blocks
        self.assertIn("skipped", out)
