"""Task 57 — de-commit derived artifacts + auto-rebuild guards.

Proves:
  (a) the 9 derived paths are in the ignore set, git-ignored, and untracked by the
      commit path;
  (b) per-project CONTENT (`projects/<name>/index.md`) and a learning stem ending
      `-index.md` STAY trackable (the `**/index.md` glob DATA-LOSS trap is avoided);
  (c) a missing OR corrupt index.json auto-rebuilds on the next command (a fresh
      clone is never bricked);
  (d) a fast-forward pull that changed entry files rebuilds so the pulled entry is
      immediately recallable.

Stdlib-only unittest; every scenario runs against a throwaway temp KB (R-LOC-03).
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, run_cli, build_synthetic_kb
except ImportError:  # discover -s tests
    from _fixtures import load_cli, run_cli, build_synthetic_kb


CLI = load_cli()


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


def _init_repo(path):
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    return path


def _tracked(repo):
    return set(_git(repo, "ls-files").stdout.split("\n")) - {""}


def _is_ignored(repo, rel):
    """True iff git would ignore `rel` in `repo` (check-ignore exit 0)."""
    cp = _git(repo, "check-ignore", "-q", rel, check=False)
    return cp.returncode == 0


class DerivedSetShapeTest(unittest.TestCase):
    def test_decommit_derived_set_is_exactly_the_nine_paths(self):
        expected = {"index.json", "FEATURES.md"} | {
            "%s/index.md" % s for s in CLI.KNOWLEDGE_SUBDIRS}
        self.assertEqual(set(CLI.KB_DERIVED_GITIGNORE_RELS), expected)
        self.assertEqual(len(CLI.KB_DERIVED_GITIGNORE_RELS), 9)

    def test_decommit_required_contains_derived_and_lock(self):
        ig = set(CLI.KB_GITIGNORE_REQUIRED)
        self.assertLessEqual({"index.json", "FEATURES.md"}, ig)
        self.assertIn("learnings/index.md", ig)
        self.assertIn(".index.lock", ig)

    def test_decommit_no_unsafe_glob_in_required(self):
        # The audit-confirmed data-loss trap: NEVER `**/index.md` or `*index.md`.
        for r in CLI.KB_GITIGNORE_REQUIRED:
            self.assertNotIn("**", r)
            self.assertNotEqual(r, "*index.md")
            self.assertFalse(r.startswith("*"), r)


@unittest.skipUnless(HAVE_GIT, "git not available")
class GitignoreMatchingTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="aw-decommit-gi-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        _init_repo(self.repo)
        CLI._ensure_kb_gitignore(self.repo)

    def test_decommit_each_derived_path_is_git_ignored(self):
        for rel in CLI.KB_DERIVED_GITIGNORE_RELS:
            self.assertTrue(_is_ignored(self.repo, rel),
                            "derived path not ignored: %s" % rel)
        self.assertTrue(_is_ignored(self.repo, ".index.lock"))

    def test_decommit_per_project_content_index_stays_trackable(self):
        # DATA-LOSS GUARD (b): a per-project content index.md and a learning stem
        # ending -index.md must NOT be ignored (a **/index.md glob would eat them).
        for rel in ("projects/oaplace/index.md",
                    "projects/tokto-io/index.md",
                    "learnings/longmemeval-scorer-strategy-agnostic-synthetic-"
                    "index.md"):
            self.assertFalse(_is_ignored(self.repo, rel),
                             "per-project/content path wrongly ignored: %s" % rel)
            self.assertNotIn(rel, CLI.KB_DERIVED_GITIGNORE_RELS)


@unittest.skipUnless(HAVE_GIT, "git not available")
class CommitUntracksDerivedTest(unittest.TestCase):
    def test_decommit_commit_untracks_derived_but_keeps_local(self):
        repo = tempfile.mkdtemp(prefix="aw-decommit-commit-")
        self.addCleanup(shutil.rmtree, repo, True)
        _init_repo(repo)
        build_synthetic_kb(repo)  # writes index.json + rosters + entry files
        with open(os.path.join(repo, "FEATURES.md"), "w", encoding="utf-8") as f:
            f.write("# Features\n")
        # A pre-de-commit history that TRACKS the derived files.
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "seed with derived tracked")
        tracked_before = _tracked(repo)
        self.assertIn("index.json", tracked_before)
        self.assertIn("learnings/index.md", tracked_before)
        # The commit path untracks the derived set (one-time migration).
        code, out, err = run_cli(["kb-git", "commit", "--path", repo], repo)
        self.assertEqual(code, 0, err)
        tracked_after = _tracked(repo)
        for rel in CLI.KB_DERIVED_GITIGNORE_RELS:
            self.assertNotIn(rel, tracked_after,
                             "derived path still tracked after commit: %s" % rel)
        # Local files are PRESERVED (only --cached removal).
        self.assertTrue(os.path.exists(os.path.join(repo, "index.json")))
        # A per-project content index.md is NOT untracked (data-loss guard).
        pj = os.path.join(repo, "projects", "demoproj")
        os.makedirs(pj, exist_ok=True)
        with open(os.path.join(pj, "index.md"), "w", encoding="utf-8") as f:
            f.write("# demoproj content\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "add project content")
        self.assertIn("projects/demoproj/index.md", _tracked(repo))


@unittest.skipUnless(HAVE_GIT, "git not available")
class LegacyGitignoreSelfHealTest(unittest.TestCase):
    """The MIGRATION path: a KB scaffolded before the derived rules existed.

    `init` only writes `KB_GITIGNORE_CONTENT` when the file is ABSENT, so on every
    donor-era knowledge dir the derived rules arrive through the APPEND arm of
    `_ensure_kb_gitignore` instead. That arm renders each rule itself
    (`_gitignore_rule_line`) rather than copying the canonical block, so the two
    arms can drift — and the drift is SILENT: a `.index.lock/` line ignores a
    directory that never exists while the real sidecar stays trackable. Assert the
    appended rules against GIT, not against the string that produced them.
    """

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="aw-decommit-heal-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        _init_repo(self.repo)
        # The donor-era contract: the run-state dirs only, no derived rules.
        with open(os.path.join(self.repo, ".gitignore"), "w",
                  encoding="utf-8") as f:
            f.write("logs/\n.loop/\n")

    def test_decommit_legacy_gitignore_heals_to_ignore_every_derived_path(self):
        self.assertFalse(_is_ignored(self.repo, "index.json"),
                         "pre-state wrong: derived path already ignored")
        self.assertEqual(CLI._ensure_kb_gitignore(self.repo), "appended")
        for rel in CLI.KB_DERIVED_GITIGNORE_RELS:
            self.assertTrue(_is_ignored(self.repo, rel),
                            "self-heal left a derived path trackable: %s" % rel)
        self.assertTrue(_is_ignored(self.repo, ".index.lock"),
                        "self-heal left the lock sidecar trackable")

    def test_decommit_healed_file_rules_carry_no_trailing_slash(self):
        CLI._ensure_kb_gitignore(self.repo)
        with open(os.path.join(self.repo, ".gitignore"), encoding="utf-8") as f:
            rules = set(ln.strip() for ln in f.read().splitlines()
                        if ln.strip() and not ln.strip().startswith("#"))
        for rel in CLI.KB_DERIVED_GITIGNORE_RELS + (CLI.KB_INDEX_LOCK_REL,):
            self.assertIn(rel, rules)
            self.assertNotIn(rel + "/", rules,
                             "file rule appended as a directory rule: %s" % rel)

    def test_decommit_self_heal_is_idempotent(self):
        self.assertEqual(CLI._ensure_kb_gitignore(self.repo), "appended")
        with open(os.path.join(self.repo, ".gitignore"), encoding="utf-8") as f:
            once = f.read()
        self.assertEqual(CLI._ensure_kb_gitignore(self.repo), "kept")
        with open(os.path.join(self.repo, ".gitignore"), encoding="utf-8") as f:
            self.assertEqual(f.read(), once)

    def test_decommit_self_heal_preserves_operator_rules(self):
        # A KB owner's own rules are theirs; healing appends, never rewrites.
        with open(os.path.join(self.repo, ".gitignore"), "a",
                  encoding="utf-8") as f:
            f.write("my-scratch/\n")
        CLI._ensure_kb_gitignore(self.repo)
        with open(os.path.join(self.repo, ".gitignore"), encoding="utf-8") as f:
            body = f.read()
        self.assertIn("my-scratch/", body)
        self.assertIn("logs/", body)


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


@unittest.skipUnless(HAVE_GIT, "git not available")
class DerivedDefinitionCoherenceTest(unittest.TestCase):
    """The derived set is declared TWICE and the two copies must not drift.

    `core.kblayout.KB_DERIVED_GITIGNORE_RELS` (the ignore contract + the one-time
    `git rm --cached` migration) and `ops.kbstage._is_derived_rel` (the staging
    allowlist's drop rule) each define "derived" independently — the first as a
    literal tuple, the second by re-deriving the rule from `KNOWLEDGE_SUBDIRS`.
    They defend the SAME property from opposite ends: the allowlist stops a new
    derived file being staged, the de-commit stops an old one staying tracked. A
    path classified by only one of them is silently half-protected, and this was
    measured, not supposed: emptying the tuple left the allowlist still dropping
    every derived path, so the drift produces no failure anywhere else.
    """

    def test_decommit_both_derived_definitions_agree(self):
        from agentware_lib.ops.kbstage import _is_derived_rel
        for rel in CLI.KB_DERIVED_GITIGNORE_RELS:
            self.assertTrue(_is_derived_rel(rel),
                            "ignored as derived but stageable: %s" % rel)
        # ...and nothing the allowlist calls derived escapes the de-commit set.
        candidates = ["index.json", "FEATURES.md"] + [
            "%s/index.md" % s for s in CLI.KNOWLEDGE_SUBDIRS] + [
            "MAIN.md", "learnings/a.md", "projects/p/index.md",
            "learnings/x-index.md", "teams/t/index.json"]
        for rel in candidates:
            self.assertEqual(
                _is_derived_rel(rel), rel in CLI.KB_DERIVED_GITIGNORE_RELS,
                "derived classification disagrees for: %s" % rel)


class AutoRebuildOnMissingTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-decommit-rebuild-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)

    def test_decommit_missing_index_autorebuilds_and_recalls(self):
        os.remove(os.path.join(self.kdir, "index.json"))
        self.assertFalse(os.path.exists(os.path.join(self.kdir, "index.json")))
        # The next command must NOT error — it auto-rebuilds from frontmatter.
        code, out, err = run_cli(["recall", "geofence reminders",
                                  "--format", "json"], self.kdir)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(os.path.join(self.kdir, "index.json")),
                        "index.json was not auto-rebuilt")
        res = json.loads(out)
        ids = [r.get("id") for r in res.get("results", [])]
        self.assertIn("learn-geofence-reminders", ids)

    def test_decommit_corrupt_index_autorebuilds(self):
        with open(os.path.join(self.kdir, "index.json"), "w",
                  encoding="utf-8") as f:
            f.write("{ this is not valid json ][")
        code, out, err = run_cli(["index", "validate"], self.kdir)
        self.assertEqual(code, 0, err)  # recovered via rebuild, not bricked
        data, lerr = CLI.load_index(self.kdir)
        self.assertIsNone(lerr, lerr)
        self.assertTrue(any(e.get("id") == "learn-geofence-reminders"
                            for e in data["entries"]))

    def test_decommit_load_index_direct_recovers(self):
        os.remove(os.path.join(self.kdir, "index.json"))
        data, err = CLI.load_index(self.kdir)
        self.assertIsNone(err, err)
        self.assertIsNotNone(data)
        self.assertTrue(os.path.exists(os.path.join(self.kdir, "index.json")))


@unittest.skipUnless(HAVE_GIT, "git not available")
class PullRebuildsTest(unittest.TestCase):
    def test_decommit_ff_pull_rebuilds_and_new_entry_recallable(self):
        tmp = tempfile.mkdtemp(prefix="aw-decommit-pull-")
        self.addCleanup(shutil.rmtree, tmp, True)
        bare = os.path.join(tmp, "remote.git")
        _git(tmp, "init", "-q", "--bare", bare)
        _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")

        # Seed clone: a KB with entry frontmatter, pushed to the bare remote.
        seed = os.path.join(tmp, "seed")
        _init_repo(seed)
        _git(seed, "remote", "add", "origin", bare)
        build_synthetic_kb(seed)
        CLI._ensure_kb_gitignore(seed)
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", "seed kb")
        _git(seed, "push", "-q", "-u", "origin", "main")

        # Working clone tracks origin/main.
        clone = os.path.join(tmp, "clone")
        _git(tmp, "clone", "-q", bare, clone)
        _git(clone, "config", "user.email", "test@example.com")
        _git(clone, "config", "user.name", "Test")
        # Materialize the derived index locally (gitignored, not from git).
        code, _o, err = run_cli(["index", "rebuild"], clone)
        self.assertEqual(code, 0, err)

        # Seed adds a NEW entry with a distinctive token, pushes it.
        newp = os.path.join(seed, "learnings", "pulled-entry.md")
        fm = CLI.build_entry_frontmatter(
            {"id": "learn-pulled-entry", "title": "Pulled Entry",
             "category": "learnings", "tags": ["pulled"],
             "created": "2026-07-12",
             "summary": "A teammate entry with token zqxwv."}, seed, author="mate")
        with open(newp, "w", encoding="utf-8") as f:
            f.write(CLI.render_frontmatter(fm) + "# Pulled\n\nzqxwvunique body.\n")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", "add pulled entry")
        _git(seed, "push", "-q")

        # Before the pull the clone cannot recall the teammate token.
        _c, out_before, _e = run_cli(["recall", "zqxwvunique",
                                      "--format", "json"], clone)
        self.assertNotIn("learn-pulled-entry", out_before)

        # Pull → HEAD advances + entry file changed → index auto-rebuilt.
        code, out, err = run_cli(["kb-git", "pull", "--path", clone], clone)
        self.assertEqual(code, 0, err)
        self.assertIn("index rebuilt", out)

        _c, out_after, _e = run_cli(["recall", "zqxwvunique",
                                     "--format", "json"], clone)
        ids = [r.get("id") for r in json.loads(out_after).get("results", [])]
        self.assertIn("learn-pulled-entry", ids,
                      "pulled entry not recallable after rebuild")


if __name__ == "__main__":
    unittest.main()
