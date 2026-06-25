"""Tests for the knowledge-base git sync (feature 260625-kb-git-sync).

Stdlib-only (unittest). Every git scenario runs against a THROWAWAY temp repo
created inside the test (tempfile.mkdtemp) — NEVER the operator's real $KDIR.
Conflict/merge scenarios use throwaway clones of a temp bare remote.

If `git` is not on PATH the git-dependent tests skip cleanly.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

try:  # works both as `tests.test_kb_git` and under `discover -s tests`
    from tests._fixtures import load_cli, run_cli, build_synthetic_kb
except ImportError:
    from _fixtures import load_cli, run_cli, build_synthetic_kb


def _have_git():
    try:
        subprocess.run(["git", "--version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
        return True
    except FileNotFoundError:
        return False


HAVE_GIT = _have_git()


def _run(cwd, *args):
    """Run a git command in `cwd`, raising on failure (test setup must succeed)."""
    return subprocess.run(["git", "-C", cwd] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=True)


def _init_repo(path, with_commit=True):
    """Initialize a git repo at `path` with deterministic identity + branch."""
    os.makedirs(path, exist_ok=True)
    _run(path, "init", "-q", "-b", "main")
    _run(path, "config", "user.email", "test@example.com")
    _run(path, "config", "user.name", "Test")
    if with_commit:
        with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as f:
            f.write("seed\n")
        _run(path, "add", "-A")
        _run(path, "commit", "-q", "-m", "seed")
    return path


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


def _commit_count(repo):
    cp = _run(repo, "rev-list", "--count", "HEAD")
    return int(cp.stdout.strip())


def _head_subject(repo):
    return _run(repo, "log", "-1", "--pretty=%s").stdout.strip()


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


def _write_entry(cli, repo, entry):
    """Materialize one entry .md WITH frontmatter under `repo` (the rebuild source)."""
    abs_path = os.path.join(repo, entry["path"])
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    fm = cli.build_entry_frontmatter(entry, repo, author="test")
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(cli.render_frontmatter(fm) + entry["body"])


def _seed_entry(eid):
    return {
        "id": "learn-%s" % eid, "title": "Seed %s" % eid,
        "category": "learnings", "path": "learnings/%s.md" % eid,
        "tags": ["seed", eid], "created": "2026-01-01",
        "summary": "Seed entry %s." % eid,
        "body": "# Seed %s\n\nSeed knowledge body for %s.\n" % (eid, eid),
    }


@unittest.skipUnless(HAVE_GIT, "git not available")
class KbGitPushTest(unittest.TestCase):
    """Task 4.1 — push with deterministic derived-file conflict resolution."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-test-kbpush-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cli = load_cli()

    def _mk(self, name):
        return os.path.join(self.tmp, name)

    def _build_frontmatter_kb(self, repo):
        """A valid KB (frontmatter entries + rebuilt derived files), committed."""
        for sub in ("learnings", "projects", "configurations", "prompts",
                    "references", "skills"):
            os.makedirs(os.path.join(repo, sub), exist_ok=True)
        _write_entry(self.cli, repo, _seed_entry("alpha"))
        data, _rosters, errors = self.cli.rebuild_kb(repo)
        self.assertEqual(errors, [], "seed rebuild must be clean")
        self.assertIsNotNone(data)
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "seed kb")

    def _clone(self, name, bare):
        clone = self._mk(name)
        _run(self.tmp, "clone", "-q", bare, clone)
        _run(clone, "config", "user.email", "test@example.com")
        _run(clone, "config", "user.name", "Test")
        return clone

    def _add_learning_and_commit(self, repo, eid):
        """Add a NEW learning + regenerate derived files + commit (the per-agent flow)."""
        _write_entry(self.cli, repo, _seed_entry(eid))
        _data, _r, errors = self.cli.rebuild_kb(repo)
        self.assertEqual(errors, [])
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "add %s" % eid)

    def _ids_in(self, repo):
        """Entry-id set recovered from frontmatter across all entry files."""
        ids = set()
        for rel in self.cli.scan_entry_files(repo):
            fm = self.cli.read_entry_frontmatter(os.path.join(repo, rel))
            if fm.get("id"):
                ids.add(fm["id"])
        return ids

    def test_non_repo_is_noop_local_only(self):
        plain = self._mk("plain")
        os.makedirs(plain)
        code, out, err = run_cli(["kb-git", "push", "--path", plain], plain)
        self.assertEqual(code, 0, err)  # C-4: degrade, never block
        self.assertIn("not a git work tree", out)

    def test_no_upstream_skips(self):
        repo = _init_repo(self._mk("local"))
        code, out, err = run_cli(["kb-git", "push", "--path", repo], repo)
        self.assertEqual(code, 0, err)
        self.assertIn("no upstream", out)

    def test_clean_fast_forward_push(self):
        bare = self._mk("ff.git")
        _run(self.tmp, "init", "-q", "--bare", bare)
        seed = _init_repo(self._mk("ff-seed"), with_commit=False)
        self._build_frontmatter_kb(seed)
        _run(seed, "remote", "add", "origin", bare)
        _run(seed, "push", "-q", "-u", "origin", "main")
        clone = self._clone("ff-clone", bare)
        self._add_learning_and_commit(clone, "beta")
        code, out, err = run_cli(["kb-git", "push", "--path", clone], clone)
        self.assertEqual(code, 0, err)
        self.assertIn("pushed", out)

    def test_concurrent_adds_resolve_via_rebuild(self):
        """Two clones add DIFFERENT learnings; the 2nd push auto-resolves (no agent)."""
        bare = self._mk("conc.git")
        _run(self.tmp, "init", "-q", "--bare", bare)
        seed = _init_repo(self._mk("conc-seed"), with_commit=False)
        self._build_frontmatter_kb(seed)
        _run(seed, "remote", "add", "origin", bare)
        _run(seed, "push", "-q", "-u", "origin", "main")

        clone_a = self._clone("conc-a", bare)
        clone_b = self._clone("conc-b", bare)

        # Clone A adds + pushes first (clean fast-forward).
        self._add_learning_and_commit(clone_a, "bravo")
        ca, oa, ea = run_cli(["kb-git", "push", "--path", clone_a], clone_a)
        self.assertEqual(ca, 0, ea)

        # Clone B adds a DIFFERENT learning; its push is rejected (A moved
        # upstream) and must auto-resolve the derived-file conflicts via rebuild.
        self._add_learning_and_commit(clone_b, "charlie")
        cb, ob, eb = run_cli(["kb-git", "push", "--path", clone_b], clone_b)
        self.assertEqual(cb, 0, eb)
        self.assertIn("auto-resolved", ob)

        # A fresh clone of the remote has BOTH learnings; the rebuilt index is
        # valid; nothing was lost.
        final = self._clone("conc-final", bare)
        ids = self._ids_in(final)
        self.assertIn("learn-bravo", ids)
        self.assertIn("learn-charlie", ids)
        self.assertIn("learn-alpha", ids)
        # The derived index is internally consistent after the deterministic merge.
        data, _r, errors = self.cli.rebuild_kb(final)
        self.assertEqual(errors, [])
        self.assertTrue(self.cli.git_is_clean(final),
                        "rebuild on the merged remote is a no-op (derived files "
                        "already canonical)")

    def test_non_derived_conflict_fails_loud(self):
        """A same-file (entry) conflict is NOT auto-resolved here (Phase 5 territory)."""
        bare = self._mk("prose.git")
        _run(self.tmp, "init", "-q", "--bare", bare)
        seed = _init_repo(self._mk("prose-seed"), with_commit=False)
        self._build_frontmatter_kb(seed)
        _run(seed, "remote", "add", "origin", bare)
        _run(seed, "push", "-q", "-u", "origin", "main")

        clone_a = self._clone("prose-a", bare)
        clone_b = self._clone("prose-b", bare)

        # Both clones edit the SAME entry file two different ways.
        def _edit(repo, text):
            p = os.path.join(repo, "learnings", "alpha.md")
            with open(p, "a", encoding="utf-8") as f:
                f.write(text)
            _run(repo, "add", "-A")
            _run(repo, "commit", "-q", "-m", "edit alpha")

        _edit(clone_a, "\nEdit from A.\n")
        ca, _oa, ea = run_cli(["kb-git", "push", "--path", clone_a], clone_a)
        self.assertEqual(ca, 0, ea)

        _edit(clone_b, "\nEdit from B.\n")
        cb, ob, eb = run_cli(["kb-git", "push", "--path", clone_b], clone_b)
        self.assertEqual(cb, 1)  # fail loud — needs the Phase 5 prose merge
        self.assertIn("non-derived", eb + ob)
        # Rebase was aborted cleanly — no dangling conflict state.
        self.assertEqual(self.cli.git_unmerged_paths(clone_b), [])


@unittest.skipUnless(HAVE_GIT, "git not available")
class KbGitProseMergeTest(unittest.TestCase):
    """Task 5.1 — same-entry prose conflict: pause + agent-reconcile + continue.

    The deterministic plumbing (pause the rebase, surface the conflicted entry
    files, then stage + rebuild derived + continue + push) is tested here with the
    TEST standing in for the MERGE_PROMPT agent (it writes the reconciled file).
    The LLM's reconciliation QUALITY is verified by a manual run; this proves the
    machinery loses nothing and never lets an agent touch a derived file.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-test-kbprose-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cli = load_cli()

    def _mk(self, name):
        return os.path.join(self.tmp, name)

    def _build_frontmatter_kb(self, repo):
        for sub in ("learnings", "projects", "configurations", "prompts",
                    "references", "skills"):
            os.makedirs(os.path.join(repo, sub), exist_ok=True)
        _write_entry(self.cli, repo, _seed_entry("alpha"))
        data, _rosters, errors = self.cli.rebuild_kb(repo)
        self.assertEqual(errors, [], "seed rebuild must be clean")
        self.assertIsNotNone(data)
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "seed kb")

    def _clone(self, name, bare):
        clone = self._mk(name)
        _run(self.tmp, "clone", "-q", bare, clone)
        _run(clone, "config", "user.email", "test@example.com")
        _run(clone, "config", "user.name", "Test")
        return clone

    def _ids_in(self, repo):
        ids = set()
        for rel in self.cli.scan_entry_files(repo):
            fm = self.cli.read_entry_frontmatter(os.path.join(repo, rel))
            if fm.get("id"):
                ids.add(fm["id"])
        return ids

    @staticmethod
    def _alpha_path(repo):
        return os.path.join(repo, "learnings", "alpha.md")

    def _edit_alpha(self, repo, text):
        with open(self._alpha_path(repo), "a", encoding="utf-8") as f:
            f.write(text)
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "edit alpha")

    def _diverged_clones(self, slug):
        """Seed → bare → two clones that edit the SAME entry two different ways.

        Clone A pushes first (clean fast-forward); clone B is returned PAUSED on a
        prose conflict (rebase in progress, alpha.md unmerged).
        """
        bare = self._mk(slug + ".git")
        _run(self.tmp, "init", "-q", "--bare", bare)
        seed = _init_repo(self._mk(slug + "-seed"), with_commit=False)
        self._build_frontmatter_kb(seed)
        _run(seed, "remote", "add", "origin", bare)
        _run(seed, "push", "-q", "-u", "origin", "main")
        clone_a = self._clone(slug + "-a", bare)
        clone_b = self._clone(slug + "-b", bare)
        self._edit_alpha(clone_a, "\nEdit from A — fact A only.\n")
        ca, _oa, ea = run_cli(["kb-git", "push", "--path", clone_a], clone_a)
        self.assertEqual(ca, 0, ea)
        self._edit_alpha(clone_b, "\nEdit from B — fact B only.\n")
        code, out, err = run_cli(
            ["kb-git", "push", "--path", clone_b, "--on-prose-conflict", "pause"],
            clone_b)
        self.assertEqual(code, 3, err)                 # paused, not aborted
        self.assertIn("learnings/alpha.md", out)       # conflicted file on stdout
        self.assertTrue(self.cli.git_rebase_in_progress(clone_b))
        return bare, clone_b

    def test_pause_then_merge_continue_retains_both_sides(self):
        bare, clone_b = self._diverged_clones("prose")
        # Simulate the MERGE_PROMPT agent: reconcile alpha.md keeping BOTH facts,
        # frontmatter intact, no conflict markers. (Never touches derived files.)
        entry = _seed_entry("alpha")
        entry["body"] = (entry["body"] + "\nEdit from A — fact A only.\n"
                         + "Edit from B — fact B only.\n")
        _write_entry(self.cli, clone_b, entry)
        self.assertFalse(
            self.cli.file_has_conflict_markers(self._alpha_path(clone_b)))

        code, out, err = run_cli(
            ["kb-git", "merge-continue", "--path", clone_b], clone_b)
        self.assertEqual(code, 0, err)
        self.assertIn("pushed", out)
        self.assertFalse(self.cli.git_rebase_in_progress(clone_b))

        # A fresh clone has BOTH sides' facts + a valid, canonical index.
        final = self._clone("prose-final", bare)
        with open(self._alpha_path(final), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("fact A only", text)
        self.assertIn("fact B only", text)
        self.assertNotIn("<<<<<<<", text)
        self.assertIn("learn-alpha", self._ids_in(final))
        data, _r, errors = self.cli.rebuild_kb(final)
        self.assertEqual(errors, [])
        self.assertTrue(self.cli.git_is_clean(final),
                        "rebuild on the merged remote is a no-op (derived files "
                        "already canonical)")

    def test_merge_continue_refuses_unresolved_markers(self):
        _bare, clone_b = self._diverged_clones("markers")
        # Do NOT reconcile — the conflicted file still has git markers.
        self.assertTrue(
            self.cli.file_has_conflict_markers(self._alpha_path(clone_b)))
        code, out, err = run_cli(
            ["kb-git", "merge-continue", "--path", clone_b], clone_b)
        self.assertEqual(code, 1)
        self.assertIn("conflict markers remain", err + out)
        # Rebase is LEFT IN PLACE (partial work preserved, never silently aborted).
        self.assertTrue(self.cli.git_rebase_in_progress(clone_b))
        _run(clone_b, "rebase", "--abort")  # tidy up for cleanup

    def test_merge_continue_no_rebase_in_progress_fails(self):
        bare = self._mk("norebase.git")
        _run(self.tmp, "init", "-q", "--bare", bare)
        seed = _init_repo(self._mk("norebase-seed"), with_commit=False)
        self._build_frontmatter_kb(seed)
        _run(seed, "remote", "add", "origin", bare)
        _run(seed, "push", "-q", "-u", "origin", "main")
        clone = self._clone("norebase-clone", bare)
        code, out, err = run_cli(
            ["kb-git", "merge-continue", "--path", clone], clone)
        self.assertEqual(code, 1)
        self.assertIn("no rebase in progress", err + out)

    def test_merge_continue_non_repo_is_noop_local_only(self):
        plain = self._mk("plain")
        os.makedirs(plain)
        code, out, err = run_cli(
            ["kb-git", "merge-continue", "--path", plain], plain)
        self.assertEqual(code, 0, err)  # C-4: degrade, never block
        self.assertIn("not a git work tree", out)


@unittest.skipUnless(HAVE_GIT, "git not available")
class KbNothingLostGateTest(unittest.TestCase):
    """Task 6.1 — mechanical nothing-lost ID-superset gate (C-2) + bounded retry."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-test-kbgate-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cli = load_cli()

    def _mk(self, name):
        return os.path.join(self.tmp, name)

    def _build_frontmatter_kb(self, repo, eid="alpha"):
        for sub in ("learnings", "projects", "configurations", "prompts",
                    "references", "skills"):
            os.makedirs(os.path.join(repo, sub), exist_ok=True)
        _write_entry(self.cli, repo, _seed_entry(eid))
        _data, _r, errors = self.cli.rebuild_kb(repo)
        self.assertEqual(errors, [], "seed rebuild must be clean")
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "seed kb")

    def _add_learning_and_commit(self, repo, eid):
        _write_entry(self.cli, repo, _seed_entry(eid))
        _data, _r, errors = self.cli.rebuild_kb(repo)
        self.assertEqual(errors, [])
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "add %s" % eid)

    def _rev(self, repo, ref="HEAD"):
        return _run(repo, "rev-parse", ref).stdout.strip()

    # --- gate core ---------------------------------------------------------

    def test_gate_rejects_dropped_id(self):
        """A merge whose result is missing a parent's entry id is REJECTED."""
        repo = _init_repo(self._mk("drop"), with_commit=False)
        self._build_frontmatter_kb(repo, "alpha")        # HEAD: alpha
        self._add_learning_and_commit(repo, "beta")      # HEAD: alpha + beta
        both = self._rev(repo)                            # parent with BOTH ids
        # Now drop beta from the work tree (simulating a lossy merge result).
        os.remove(os.path.join(repo, "learnings", "beta.md"))
        self.cli.rebuild_kb(repo)
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "lossy: drop beta")

        ok, note = self.cli.kb_nothing_lost_gate(repo, [both, "HEAD"])
        self.assertFalse(ok)
        self.assertIn("DROP", note)
        self.assertIn("learn-beta", note)

    def test_gate_passes_clean_union(self):
        """A result that is a superset of both parents PASSES the gate."""
        repo = _init_repo(self._mk("union"), with_commit=False)
        self._build_frontmatter_kb(repo, "alpha")        # P1: alpha
        p1 = self._rev(repo)
        self._add_learning_and_commit(repo, "beta")      # HEAD: alpha + beta
        ok, note = self.cli.kb_nothing_lost_gate(repo, [p1, "HEAD"])
        self.assertTrue(ok, note)
        self.assertIn("ok", note)

    def test_gate_skips_unresolvable_parent(self):
        """A parent ref that does not resolve is skipped, not counted as empty."""
        repo = _init_repo(self._mk("skip"), with_commit=False)
        self._build_frontmatter_kb(repo, "alpha")
        ok, note = self.cli.kb_nothing_lost_gate(
            repo, ["HEAD", "refs/does/not/exist"])
        self.assertTrue(ok, note)

    def test_entry_ids_at_ref(self):
        """git_entry_ids_at_ref reads entry ids from a ref tree (no checkout)."""
        repo = _init_repo(self._mk("ids"), with_commit=False)
        self._build_frontmatter_kb(repo, "alpha")
        self._add_learning_and_commit(repo, "beta")
        ids = self.cli.git_entry_ids_at_ref(repo, "HEAD")
        self.assertEqual(ids, {"learn-alpha", "learn-beta"})
        self.assertIsNone(
            self.cli.git_entry_ids_at_ref(repo, "refs/nope"))

    # --- bounded retry -----------------------------------------------------

    def test_bounded_retry_fails_loud_after_cap(self):
        """A persistent re-push race retries exactly max_retries times, then rc 1."""
        calls = {"n": 0}

        def fake_push_once(target, on_prose_conflict="abort"):
            calls["n"] += 1
            return self.cli.RC_REPUSH_RACE, "race"

        saved = self.cli.kb_git_push_once
        self.cli.kb_git_push_once = fake_push_once
        try:
            rc, note = self.cli.kb_git_push_synced(
                self._mk("unused"), max_retries=3)
        finally:
            self.cli.kb_git_push_once = saved
        self.assertEqual(calls["n"], 3)          # bounded — no unbounded loop
        self.assertEqual(rc, 1)                  # fail loud (race mapped to 1)
        self.assertIn("re-push race", note)

    def test_bounded_retry_succeeds_within_cap(self):
        """A race that clears before the cap returns the eventual success."""
        seq = [self.cli.RC_REPUSH_RACE, self.cli.RC_REPUSH_RACE, 0]
        calls = {"n": 0}

        def fake_push_once(target, on_prose_conflict="abort"):
            rc = seq[calls["n"]]
            calls["n"] += 1
            return rc, ("pushed" if rc == 0 else "race")

        saved = self.cli.kb_git_push_once
        self.cli.kb_git_push_once = fake_push_once
        try:
            rc, note = self.cli.kb_git_push_synced(
                self._mk("unused"), max_retries=3)
        finally:
            self.cli.kb_git_push_once = saved
        self.assertEqual(calls["n"], 3)
        self.assertEqual(rc, 0)
        self.assertIn("pushed", note)

    def test_non_race_failure_returns_immediately(self):
        """A fail-loud rc 1 (not a race) is returned without retrying."""
        calls = {"n": 0}

        def fake_push_once(target, on_prose_conflict="abort"):
            calls["n"] += 1
            return 1, "non-derived conflict"

        saved = self.cli.kb_git_push_once
        self.cli.kb_git_push_once = fake_push_once
        try:
            rc, note = self.cli.kb_git_push_synced(
                self._mk("unused"), max_retries=3)
        finally:
            self.cli.kb_git_push_once = saved
        self.assertEqual(calls["n"], 1)          # no retry on a non-race failure
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
