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

from tests.unit.ops._decommit_support import (  # noqa: F401
    CLI, HAVE_GIT, _git, _init_repo, _is_ignored, _tracked,
    build_synthetic_kb, json, os, run_cli, shutil, tempfile, unittest)


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
