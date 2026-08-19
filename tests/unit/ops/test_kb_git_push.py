"""KbGitPushTest, KbGitProseMergeTest -- split from `test_kb_git` (task 58).

See `tests.unit.ops._kb_git_support` for the
shared header.
"""

from tests.unit.ops._kb_git_support import (  # noqa: F401
    HAVE_GIT, _init_repo, _run, _seed_entry, _write_entry, load_cli, os,
    run_cli, shutil, tempfile, unittest)


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
        _run(bare, "symbolic-ref", "HEAD", "refs/heads/main")
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
        _run(bare, "symbolic-ref", "HEAD", "refs/heads/main")
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
        # upstream). With derived artifacts DE-COMMITTED (P1.7 fix 1), the divergent
        # index.json/rosters are gitignored + untracked, so they no longer conflict
        # — the two distinct entry files rebase cleanly and the push succeeds
        # without invoking the derived-file rebuild resolver. A transitional KB that
        # still TRACKS derived files would instead report "auto-resolved"; either
        # way the push SUCCEEDS and NOTHING is lost (asserted below).
        self._add_learning_and_commit(clone_b, "charlie")
        cb, ob, eb = run_cli(["kb-git", "push", "--path", clone_b], clone_b)
        self.assertEqual(cb, 0, eb)
        self.assertIn("pushed", ob)

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
        _run(bare, "symbolic-ref", "HEAD", "refs/heads/main")
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
        _run(bare, "symbolic-ref", "HEAD", "refs/heads/main")
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
        _run(bare, "symbolic-ref", "HEAD", "refs/heads/main")
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
