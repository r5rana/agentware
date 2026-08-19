"""TwoCloneSyncTest, LegacyContractSweepTest, NestedKdirPathSpaceTest -- split from `test_decommit` (task 58).

See `tests.unit.ops._decommit_support` for the
shared header.
"""

from tests.unit.ops._decommit_support import (  # noqa: F401
    CLI, HAVE_GIT, _git, _init_repo, _tracked, json, os, run_cli, shutil,
    subprocess, tempfile, unittest)


@unittest.skipUnless(HAVE_GIT, "git not available")
class TwoCloneSyncTest(unittest.TestCase):
    """The property the de-commit EXISTS for: two clones sync without conflict.

    Derived artifacts are per-clone byte-different by construction (the index
    carries absolute paths and rebuild ordering), so while they are TRACKED every
    push from one clone conflicts in the other on files neither operator edited.
    This exercises the real thing — a bare origin, two working clones, a knowledge
    write on each side, and a real push/pull round trip — and asserts on GIT's
    outcome (merge return code + conflict markers), not on our own bookkeeping.
    """

    def _clone(self, origin, name):
        path = os.path.join(self.root, name)
        subprocess.run(["git", "clone", "-q", origin, path], check=True)
        _git(path, "config", "user.email", "%s@example.com" % name)
        _git(path, "config", "user.name", name)
        return path

    def _write_derived(self, repo, marker):
        """Give this clone its OWN derived artifacts, as a real rebuild would."""
        with open(os.path.join(repo, "index.json"), "w", encoding="utf-8") as f:
            json.dump({"entries": [], "clone": marker}, f)
        with open(os.path.join(repo, "FEATURES.md"), "w", encoding="utf-8") as f:
            f.write("# Features (%s)\n" % marker)
        for sub in CLI.KNOWLEDGE_SUBDIRS:
            d = os.path.join(repo, sub)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "index.md"), "w", encoding="utf-8") as f:
                f.write("# %s roster rebuilt by %s\n" % (sub, marker))
        with open(os.path.join(repo, ".index.lock"), "w", encoding="utf-8") as f:
            f.write(marker)

    def _write_learning(self, repo, stem, body):
        d = os.path.join(repo, "learnings")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, stem + ".md"), "w", encoding="utf-8") as f:
            f.write("---\nid: %s\ncategory: learnings\n---\n\n%s\n"
                    % (stem, body))

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-decommit-sync-")
        self.addCleanup(shutil.rmtree, self.root, True)
        # Origin seeded from a KB carrying the ignore contract and NO derived
        # artifacts in history — the post-de-commit state.
        seed = os.path.join(self.root, "seed")
        _init_repo(seed)
        CLI._ensure_kb_gitignore(seed)
        self._write_learning(seed, "seed-note", "seed")
        self._write_derived(seed, "seed")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", "seed")
        self.origin = os.path.join(self.root, "origin.git")
        subprocess.run(["git", "clone", "-q", "--bare", seed, self.origin],
                       check=True)
        self.a = self._clone(self.origin, "alice")
        self.b = self._clone(self.origin, "bob")

    def test_decommit_derived_never_enter_history(self):
        self._write_derived(self.a, "alice")
        _git(self.a, "add", "-A")
        cp = _git(self.a, "status", "--porcelain")
        self.assertEqual(cp.stdout.strip(), "",
                         "a derived-only rebuild produced stageable changes")
        for rel in CLI.KB_DERIVED_GITIGNORE_RELS + (CLI.KB_INDEX_LOCK_REL,):
            self.assertNotIn(rel, _tracked(self.a),
                             "derived path is tracked in the origin: %s" % rel)

    def test_decommit_two_clone_sync_has_no_conflict_on_derived(self):
        # Both operators rebuild their index (different bytes) AND write a real
        # knowledge entry, then sync through the origin.
        self._write_derived(self.a, "alice")
        self._write_learning(self.a, "alice-note", "alice knowledge")
        code, _out, err = run_cli(["kb-git", "commit", "--path", self.a], self.a)
        self.assertEqual(code, 0, err)
        _git(self.a, "push", "-q", "origin", "main")

        self._write_derived(self.b, "bob")
        self._write_learning(self.b, "bob-note", "bob knowledge")
        code, _out, err = run_cli(["kb-git", "commit", "--path", self.b], self.b)
        self.assertEqual(code, 0, err)

        # The sync that used to conflict on nine files nobody edited.
        _git(self.b, "fetch", "-q", "origin")
        merge = _git(self.b, "merge", "--no-edit", "origin/main", check=False)
        self.assertEqual(merge.returncode, 0,
                         "two-clone sync conflicted: %s%s"
                         % (merge.stdout, merge.stderr))
        conflicts = _git(self.b, "diff", "--name-only", "--diff-filter=U").stdout
        self.assertEqual(conflicts.strip(), "", "conflicted paths: %s" % conflicts)

        # Both sides' KNOWLEDGE survived the sync...
        for stem in ("alice-note", "bob-note", "seed-note"):
            self.assertTrue(
                os.path.isfile(os.path.join(self.b, "learnings", stem + ".md")),
                "knowledge lost in sync: %s" % stem)
        # ...and each clone kept its OWN derived artifacts, unmerged.
        with open(os.path.join(self.b, "index.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["clone"], "bob")
        with open(os.path.join(self.b, "FEATURES.md"), encoding="utf-8") as f:
            self.assertNotIn("<<<<<<<", f.read())

        _git(self.b, "push", "-q", "origin", "main")
        _git(self.a, "pull", "-q", "--no-rebase", "origin", "main")
        with open(os.path.join(self.a, "index.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["clone"], "alice",
                             "pull overwrote this clone's own derived index")
        self.assertTrue(os.path.isfile(
            os.path.join(self.a, "learnings", "bob-note.md")))

    def test_decommit_tracked_derived_conflict_control(self):
        """CONTROL: the same sync DOES conflict while the derived set is tracked.

        Without this arm the no-conflict assertion above is vacuous — it would
        pass just as happily against a scenario incapable of conflicting at all
        (it did: with the derived set emptied, the de-commit disabled and the
        allowlist bypassed, the sync test stayed green because nothing on either
        side ever COMMITTED a derived file). Here both clones commit their own
        rebuild with a blanket `git add -A`, which is precisely the donor
        behaviour the allowlist and the de-commit replaced, and git is asked for
        the verdict. Deliberately does NOT go through `kb-git commit`: the point
        is to reproduce the pre-state, not to re-test the fix.
        """
        root = tempfile.mkdtemp(prefix="aw-decommit-control-")
        self.addCleanup(shutil.rmtree, root, True)
        seed = os.path.join(root, "seed")
        _init_repo(seed)
        # The donor pre-state: NO ignore contract, so `add -A` tracks the derived.
        self._write_learning(seed, "seed-note", "seed")
        self._write_derived(seed, "seed")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", "seed with derived tracked")
        self.assertIn("index.json", _tracked(seed),
                      "control pre-state wrong: derived not tracked")
        origin = os.path.join(root, "origin.git")
        subprocess.run(["git", "clone", "-q", "--bare", seed, origin], check=True)
        a = os.path.join(root, "alice")
        b = os.path.join(root, "bob")
        for path, name in ((a, "alice"), (b, "bob")):
            subprocess.run(["git", "clone", "-q", origin, path], check=True)
            _git(path, "config", "user.email", "%s@example.com" % name)
            _git(path, "config", "user.name", name)
            self._write_derived(path, name)
            self._write_learning(path, "%s-note" % name, "%s knowledge" % name)
            _git(path, "add", "-A")
            _git(path, "commit", "-q", "-m", "%s sync" % name)
        _git(a, "push", "-q", "origin", "main")
        _git(b, "fetch", "-q", "origin")
        merge = _git(b, "merge", "--no-edit", "origin/main", check=False)
        self.assertNotEqual(merge.returncode, 0,
                            "control did not reproduce the conflict")
        conflicted = set(
            _git(b, "diff", "--name-only", "--diff-filter=U").stdout.split())
        # The conflict lands on files NEITHER operator edited by hand.
        self.assertIn("index.json", conflicted)
        self.assertIn("learnings/index.md", conflicted)
        self.assertNotIn("learnings/alice-note.md", conflicted)
        self.assertNotIn("learnings/bob-note.md", conflicted)

@unittest.skipUnless(HAVE_GIT, "git not available")
class LegacyContractSweepTest(unittest.TestCase):
    """The de-commit must sweep the WHOLE declared contract, not just the nine.

    A .gitignore rule never untracks a file git already tracks, so on a knowledge
    dir predating a rule the rule is inert forever. Both cases below were found by
    probing a legacy tree, not by reading the code, and both were live on HEAD:

      - `.index.lock` is in KB_GITIGNORE_REQUIRED but was NOT in the de-commit set,
        so a legacy KB re-committed the mutation sidecar in every single `kb-git
        commit` — the per-push churn task 57 exists to end, arriving by the tenth
        path instead of the nine.
      - a tracked file under an ignored DIR (`logs/`) was worse than churn: `git
        add` refuses an ignored pathspec, so the whole commit failed with rc=1 and
        the KB silently stopped committing knowledge at all.

    Assertions are on the OUTCOME (tracked set, exit code, bytes on disk), never on
    the sweep's own bookkeeping.
    """

    def _legacy_kb(self, *rels):
        repo = tempfile.mkdtemp(prefix="aw-decommit-legacy-")
        self.addCleanup(shutil.rmtree, repo, True)
        _init_repo(repo)
        for rel in rels:
            abs_p = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(abs_p) or repo, exist_ok=True)
            with open(abs_p, "w", encoding="utf-8") as f:
                f.write("local-only content of %s\n" % rel)
        os.makedirs(os.path.join(repo, "learnings"), exist_ok=True)
        with open(os.path.join(repo, "learnings", "a.md"), "w",
                  encoding="utf-8") as f:
            f.write("---\nid: a\ncategory: learnings\n---\n\nseed\n")
        # The donor-era .gitignore: run-state dirs absent entirely.
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "legacy: local-only paths tracked")
        for rel in rels:
            self.assertIn(rel, _tracked(repo),
                          "pre-state wrong: %s not tracked" % rel)
        return repo

    def _new_knowledge(self, repo, stem="b"):
        with open(os.path.join(repo, "learnings", stem + ".md"), "w",
                  encoding="utf-8") as f:
            f.write("---\nid: %s\ncategory: learnings\n---\n\nnew\n" % stem)

    def test_decommit_legacy_tracked_lock_is_swept(self):
        repo = self._legacy_kb(".index.lock")
        with open(os.path.join(repo, ".index.lock"), "w", encoding="utf-8") as f:
            f.write("rewritten by this run\n")
        self._new_knowledge(repo)
        code, _out, err = run_cli(["kb-git", "commit", "--path", repo], repo)
        self.assertEqual(code, 0, err)
        self.assertNotIn(".index.lock", _tracked(repo),
                         "the mutation sidecar is still tracked")
        # The local file is PRESERVED — `--cached` only, never a working-tree rm.
        with open(os.path.join(repo, ".index.lock"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "rewritten by this run\n")
        self.assertIn("learnings/b.md", _tracked(repo),
                      "the knowledge write was lost to the sweep")

    def test_decommit_legacy_tracked_runstate_no_longer_fails_the_commit(self):
        repo = self._legacy_kb("logs/sessions/s1.md", ".loop/state",
                               ".cache/bm25.json")
        # Ordinary local activity rewrites a tracked, now-ignored path.
        with open(os.path.join(repo, "logs", "sessions", "s1.md"), "a",
                  encoding="utf-8") as f:
            f.write("MORE TRANSCRIPT\n")
        self._new_knowledge(repo)
        code, _out, err = run_cli(["kb-git", "commit", "--path", repo], repo)
        self.assertEqual(code, 0, "commit failed on a legacy KB: %s" % err)
        tracked = _tracked(repo)
        for rel in ("logs/sessions/s1.md", ".loop/state", ".cache/bm25.json"):
            self.assertNotIn(rel, tracked, "run-state still tracked: %s" % rel)
        self.assertIn("learnings/b.md", tracked)
        # Transcripts are local-only, never deleted from the operator's machine.
        with open(os.path.join(repo, "logs", "sessions", "s1.md"),
                  encoding="utf-8") as f:
            body = f.read()
        self.assertIn("MORE TRANSCRIPT", body)

    def test_decommit_sweep_is_idempotent_and_second_run_is_clean(self):
        repo = self._legacy_kb(".index.lock", "logs/sessions/s1.md")
        self._new_knowledge(repo, "b")
        code, _out, err = run_cli(["kb-git", "commit", "--path", repo], repo)
        self.assertEqual(code, 0, err)
        self._new_knowledge(repo, "c")
        code, _out, err = run_cli(["kb-git", "commit", "--path", repo], repo)
        self.assertEqual(code, 0, err)
        self.assertIn("learnings/c.md", _tracked(repo))
        # Nothing left pending: the sweep did not re-stage what it removed.
        self.assertEqual(_git(repo, "status", "--porcelain").stdout.strip(), "")

    def test_decommit_sweep_never_reaches_outside_the_contract(self):
        # Decoys chosen to break a careless prefix/glob match: a top-level file
        # whose NAME starts with an ignored dir, and real knowledge in a
        # subdirectory that happens to be called `logs`.
        repo = self._legacy_kb(".index.lock")
        for rel in ("logs-notes.md", "learnings/logs/keep.md",
                    "projects/p/index.md"):
            abs_p = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(abs_p), exist_ok=True)
            with open(abs_p, "w", encoding="utf-8") as f:
                f.write("real knowledge\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "decoys")
        self._new_knowledge(repo)
        code, _out, err = run_cli(["kb-git", "commit", "--path", repo], repo)
        self.assertEqual(code, 0, err)
        tracked = _tracked(repo)
        for rel in ("logs-notes.md", "learnings/logs/keep.md",
                    "projects/p/index.md"):
            self.assertIn(rel, tracked, "sweep untracked real content: %s" % rel)

    def test_decommit_pathspecs_are_exactly_the_declared_contract(self):
        from agentware_lib.ops.staging import _kb_ignored_pathspecs
        specs = set(_kb_ignored_pathspecs())
        self.assertEqual(
            specs,
            set(CLI.KB_DERIVED_GITIGNORE_RELS) | {CLI.KB_INDEX_LOCK_REL}
            | set(CLI.KB_GITIGNORE_DIR_RULES))
        # Dir rules are passed BARE so `git ls-files -- logs` enumerates beneath.
        for spec in specs:
            self.assertFalse(spec.endswith("/"), spec)

@unittest.skipUnless(HAVE_GIT, "git not available")
class NestedKdirPathSpaceTest(unittest.TestCase):
    """The sweep and the allowlist speak different path spaces (task 57).

    `_decommit_derived` reads `git ls-files`, which prints paths relative to the
    CURRENT DIRECTORY; the allowlist reads `git status --porcelain`, which prints
    them relative to the REPOSITORY ROOT. On a flat KB those are the same string,
    so a raw set-difference between them looks correct and every flat test agrees.
    On a NESTED kdir (`<repo>/teams/<team>`, the org layout) they differ by the
    team prefix, the filter silently drops nothing, and `git add` is handed the
    ignored pathspec the filter exists to remove.

    Reproduced before it was fixed: `kb-git commit` on a legacy org KB tracking
    `teams/alpha/logs/sessions/s1.md` exited 1 with "The following paths are
    ignored by one of your .gitignore files: teams/alpha/logs" — the ORIGINAL
    task-57 defect, alive in the one mode it was never exercised in. Asserted on
    the exit code and the tracked set, so restoring the unconverted comparison
    reddens it.
    """

    def _legacy_org_repo(self):
        root = tempfile.mkdtemp(prefix="aw-decommit-org-")
        self.addCleanup(shutil.rmtree, root, True)
        _init_repo(root)
        self.kdir = os.path.join(root, "teams", "alpha")
        for sub in ("logs/sessions", "learnings"):
            os.makedirs(os.path.join(self.kdir, sub), exist_ok=True)
        with open(os.path.join(self.kdir, "logs", "sessions", "s1.md"), "w",
                  encoding="utf-8") as f:
            f.write("transcript\n")
        with open(os.path.join(self.kdir, ".index.lock"), "w",
                  encoding="utf-8") as f:
            f.write("lock\n")
        with open(os.path.join(self.kdir, "learnings", "a.md"), "w",
                  encoding="utf-8") as f:
            f.write("---\nid: a\ncategory: learnings\n---\n\nseed\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "legacy org KB: run state tracked")
        self.assertIn("teams/alpha/logs/sessions/s1.md", _tracked(root),
                      "pre-state wrong: run state not tracked")
        return root

    def test_decommit_nested_kdir_commit_succeeds_and_sweeps(self):
        root = self._legacy_org_repo()
        # Ordinary local activity rewrites the tracked, now-ignored run state.
        with open(os.path.join(self.kdir, "logs", "sessions", "s1.md"), "a",
                  encoding="utf-8") as f:
            f.write("MORE TRANSCRIPT\n")
        with open(os.path.join(self.kdir, "learnings", "b.md"), "w",
                  encoding="utf-8") as f:
            f.write("---\nid: b\ncategory: learnings\n---\n\nnew\n")

        code, _out, err = run_cli(["kb-git", "commit", "--path", self.kdir],
                                  self.kdir)
        self.assertEqual(code, 0, "commit failed on a nested kdir: %s" % err)
        tracked = _tracked(root)
        for rel in ("teams/alpha/logs/sessions/s1.md", "teams/alpha/.index.lock"):
            self.assertNotIn(rel, tracked, "run state still tracked: %s" % rel)
        self.assertIn("teams/alpha/learnings/b.md", tracked,
                      "the knowledge write was lost")
        # Local-only by policy: swept from history, never from the machine.
        with open(os.path.join(self.kdir, "logs", "sessions", "s1.md"),
                  encoding="utf-8") as f:
            self.assertIn("MORE TRANSCRIPT", f.read())

    def test_decommit_nested_kdir_never_touches_a_sibling_team(self):
        root = self._legacy_org_repo()
        beta = os.path.join(root, "teams", "beta", "logs")
        os.makedirs(beta, exist_ok=True)
        with open(os.path.join(beta, "s9.md"), "w", encoding="utf-8") as f:
            f.write("beta transcript\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "beta run state")
        with open(os.path.join(self.kdir, "learnings", "b.md"), "w",
                  encoding="utf-8") as f:
            f.write("---\nid: b\ncategory: learnings\n---\n\nnew\n")

        code, _out, err = run_cli(["kb-git", "commit", "--path", self.kdir],
                                  self.kdir)
        self.assertEqual(code, 0, err)
        self.assertIn("teams/beta/logs/s9.md", _tracked(root),
                      "the sweep reached into another team's folder")

    def test_decommit_repo_relative_lift_is_prefix_exact(self):
        from agentware_lib.ops.staging import _repo_relative
        self.assertEqual(_repo_relative(["logs/a.md"], ""), {"logs/a.md"})
        self.assertEqual(_repo_relative(["logs/a.md"], "teams/alpha"),
                         {"teams/alpha/logs/a.md"})
        # A prefix already carrying a separator must not double it.
        self.assertEqual(_repo_relative(["logs/a.md"], "teams/alpha/"),
                         {"teams/alpha/logs/a.md"})
        self.assertEqual(_repo_relative([], "teams/alpha"), set())
