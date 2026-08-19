"""Task-11 donor staging: the policy bites, and it bites only where declared.

Two halves.

`PolicyTest` and `StagingTest` build SYNTHETIC trees in a tempdir, so they run
anywhere -- including the fresh clone task 78 gates -- and never need the donor
snapshot. They prove the drop classes actually drop, the empty-dir pass actually
prunes (including a directory that is empty only BECAUSE of an exclusion), the
executable bit survives, and -- the direction that matters most -- that a file
which merely resembles an excluded one is KEPT.

`DonorStagingTest` runs against the real donor snapshot and is skipped when it is
absent. It re-stages into a tempdir and asserts the result equals the committed
manifest, which is the source-independence proof: the two donor snapshots differ
(one carries caches the other lost to /tmp cleanup, the other carries empty
directories) yet must stage to a byte-identical tree.

Stdlib `unittest` only. Never touches the real knowledge base, and never writes
to the real staging directory -- every test stages into its own tempdir.
"""

from tests._paths import TESTS_DIR

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import _stage_donor as sd  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "staging-manifest.json")


def write(root, rel, body="x", mode=None):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(body)
    if mode is not None:
        os.chmod(path, mode)
    return path


def mkdir(root, rel):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(path, exist_ok=True)
    return path


class TempTreeTest(unittest.TestCase):
    """A donor-shaped synthetic tree, plus a staged copy of it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-stage-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = os.path.join(self.tmp, "src")
        self.dst = os.path.join(self.tmp, "dst")
        os.makedirs(self.src)

    def run_stage(self):
        return sd.stage(self.src, self.dst)

    def staged(self):
        return set(sd.walk_files(self.dst))


class PolicyTest(unittest.TestCase):
    """The policy is data; these are its truth-table entries."""

    def test_every_rule_declares_class_kind_patterns_and_reason(self):
        kinds = {sd.DIR_NAME, sd.FILE_GLOB, sd.REL_PREFIX}
        for rule in sd.EXCLUSIONS:
            self.assertIn(rule["kind"], kinds, rule)
            self.assertTrue(rule["class"], rule)
            self.assertTrue(rule["patterns"], rule)
            self.assertGreater(len(rule["reason"]), 30, rule["class"])

    def test_the_plan_named_classes_are_all_present(self):
        named = {"vcs", "python_cache", "log_dirs", "built_bundle",
                 "diligence_doc"}
        self.assertLessEqual(named, {r["class"] for r in sd.EXCLUSIONS})

    def test_directories_the_plan_names_are_classified(self):
        for rel, cls in ((".git", "vcs"),
                         ("scripts/__pycache__", "python_cache"),
                         (".pytest_cache", "python_cache"),
                         (".editor-logs", "log_dirs"),
                         (".agentware-logs", "log_dirs"),
                         ("webui/dist", "built_bundle"),
                         ("webui/dist/assets", "built_bundle"),
                         ("webui/test-results", "built_bundle")):
            self.assertEqual(sd.classify_dir(rel), cls, rel)

    def test_the_log_dir_rule_matches_by_shape_not_by_tool_name(self):
        """A runtime this migration never saw must still be excluded.

        The rule used to name one IDE's log directory literally, which both
        leaked an operator's environment into a committed file and left every
        OTHER runtime's stray log directory staged. It is now shape-driven.
        """
        for rel in (".editor-logs", ".agentware-logs", ".someagent-logs",
                    "tools/.newruntime-logs", "logs", "scripts/logs"):
            self.assertEqual(sd.classify_dir(rel), "log_dirs", rel)

    def test_the_log_dir_shape_does_not_swallow_real_directories(self):
        for rel in ("docs/logs-and-metrics", "scripts/logging",
                    "webui/src/loggers", "docs/changelogs", "catalog"):
            self.assertIsNone(sd.classify_dir(rel), rel)

    def test_files_the_plan_names_are_classified(self):
        for rel, cls in (("scripts/x.pyc", "python_cache"),
                         ("run.log", "log_dirs"),
                         (".DS_Store", "os_noise"),
                         ("webui/dist/index.html", "built_bundle"),
                         ("DUE-DILIGENCE.md", "diligence_doc"),
                         ("docs/donor diligence review.md", "diligence_doc")):
            self.assertEqual(sd.classify_file(rel), cls, rel)

    def test_the_diligence_rule_is_case_insensitive(self):
        for name in ("diligence.md", "Diligence.md", "DILIGENCE.MD",
                     "2026-donor-DiLiGeNcE-notes.txt"):
            self.assertEqual(sd.classify_file(name), "diligence_doc", name)

    def test_lookalikes_are_kept(self):
        """The direction that loses data silently: a false positive."""
        for rel in ("scripts/agentware", "webui/src/dist.ts",
                    "webui/src/distribution/index.ts", "docs/logging.md",
                    "scripts/loggers.py", "tests/test_git.py",
                    "catalog/skills.json", ".gitignore", ".gitattributes"):
            self.assertIsNone(sd.excluded_class(rel, False), rel)
        for rel in ("webui/src", "docs/logs-and-metrics", "scripts/git"):
            self.assertIsNone(sd.excluded_class(rel, True), rel)

    def test_an_excluded_ancestor_excludes_its_children(self):
        self.assertEqual(sd.excluded_class(".git/objects/ab/cd", False), "vcs")
        self.assertEqual(sd.excluded_class("tests/__pycache__/a.pyc", False),
                         "python_cache")

    def test_the_policy_digest_is_stable_and_rows_are_a_copy(self):
        before = sd.policy_digest()
        rows = sd.policy_rows()
        rows[0]["patterns"].append(".darcs")
        self.assertEqual(before, sd.policy_digest(),
                         "policy_rows() must hand out a copy")

    def test_widening_the_policy_moves_the_digest(self):
        """A silent widening of what staging may drop must be visible."""
        original = sd.EXCLUSIONS
        before = sd.policy_digest()
        sd.EXCLUSIONS = original + (
            {"class": "extra", "kind": sd.DIR_NAME, "patterns": ("docs",),
             "reason": "a synthetic widening used only by this test case."},)
        try:
            self.assertNotEqual(sd.policy_digest(), before)
        finally:
            sd.EXCLUSIONS = original
        self.assertEqual(sd.policy_digest(), before)


class StagingTest(TempTreeTest):

    def build(self):
        write(self.src, "AGENTS.md", "steering\n")
        write(self.src, "scripts/agentware", "#!/usr/bin/env python3\n",
              mode=0o755)
        write(self.src, "scripts/agentware_lib/core/config.py", "cfg\n")
        write(self.src, "webui/src/App.tsx", "app\n")
        # excluded surfaces
        write(self.src, ".git/config", "[core]\n")
        write(self.src, ".git/objects/ab/cdef", "blob\n")
        write(self.src, "scripts/__pycache__/config.cpython-313.pyc", "b\n")
        write(self.src, ".pytest_cache/CACHEDIR.TAG", "tag\n")
        write(self.src, "webui/dist/assets/index-abc.js", "bundle\n")
        write(self.src, "webui/dist/index.html", "<html>\n")
        write(self.src, "DUE-DILIGENCE.md", "private\n")
        write(self.src, ".DS_Store", "junk\n")
        # empty directory surfaces
        mkdir(self.src, ".editor-logs/12463")
        mkdir(self.src, ".claude/skills/vendor-ops-report")
        mkdir(self.src, "deep/empty/nest")

    def test_the_kept_surface_survives_byte_for_byte(self):
        self.build()
        self.run_stage()
        self.assertEqual(self.staged(),
                         {"AGENTS.md", "scripts/agentware",
                          "scripts/agentware_lib/core/config.py",
                          "webui/src/App.tsx"})
        self.assertEqual(sd.verify_no_loss(self.src, self.dst), [])

    def test_the_executable_bit_survives(self):
        self.build()
        self.run_stage()
        self.assertTrue(sd.is_exec(os.path.join(self.dst, "scripts",
                                                "agentware")))
        self.assertFalse(sd.is_exec(os.path.join(self.dst, "AGENTS.md")))

    def test_a_mode_0644_copy_of_an_executable_reddens_the_loss_check(self):
        self.build()
        self.run_stage()
        os.chmod(os.path.join(self.dst, "scripts", "agentware"), 0o644)
        problems = sd.verify_no_loss(self.src, self.dst)
        self.assertTrue(any("exec bit differs: scripts/agentware" in p
                            for p in problems), problems)

    def test_no_git_and_no_cache_and_no_bundle_reaches_staging(self):
        self.build()
        self.run_stage()
        self.assertFalse(os.path.exists(os.path.join(self.dst, ".git")))
        for rel in sd.walk_files(self.dst) + sd.walk_dirs(self.dst):
            self.assertNotIn("__pycache__", rel)
            self.assertNotIn("dist", rel)

    def test_the_diligence_document_is_absent_from_staging(self):
        self.build()
        self.run_stage()
        self.assertNotIn("DUE-DILIGENCE.md", self.staged())
        self.assertEqual(sd.assert_clean(self.dst), [])

    def test_every_empty_directory_is_pruned_including_nested(self):
        self.build()
        report = self.run_stage()
        self.assertEqual(sd.empty_dirs(self.dst), [])
        self.assertIn("deep/empty/nest", report["pruned_empty_dirs"])
        self.assertIn("deep", report["pruned_empty_dirs"])
        self.assertIn(".claude/skills/vendor-ops-report",
                      report["pruned_empty_dirs"])

    def test_a_directory_emptied_by_an_exclusion_is_pruned_too(self):
        """`caches/__pycache__` is the only child; `caches` must not survive."""
        write(self.src, "keep.md", "k\n")
        write(self.src, "caches/__pycache__/a.pyc", "b\n")
        report = self.run_stage()
        self.assertIn("caches", report["pruned_empty_dirs"])
        self.assertEqual(sd.empty_dirs(self.dst), [])

    def test_a_directory_whose_only_files_are_excluded_is_not_a_loss(self):
        """Found by a live probe against the donor's own `docs/` subtree.

        `notes/` holds one diligence document and nothing else. The policy drops
        the document, the empty-dir pass prunes the directory -- and a loss check
        that asks "did this source directory hold files?" reports a false loss,
        which would train the reader to ignore it.
        """
        write(self.src, "keep.md", "k\n")
        write(self.src, "notes/DUE-DILIGENCE.md", "private\n")
        report = self.run_stage()
        self.assertIn("notes", report["pruned_empty_dirs"])
        self.assertEqual(sd.verify_no_loss(self.src, self.dst), [])

    def test_a_directory_holding_files_is_never_pruned(self):
        write(self.src, "docs/deep/nested/page.md", "p\n")
        self.run_stage()
        self.assertIn("docs/deep/nested", sd.walk_dirs(self.dst))

    def test_the_excluded_accounting_counts_the_files_it_took(self):
        self.build()
        report = self.run_stage()
        self.assertEqual(report["excluded"]["vcs"]["files"], 2)
        self.assertEqual(report["excluded"]["built_bundle"]["files"], 2)
        self.assertEqual(report["excluded"]["diligence_doc"]["files"], 1)

    def test_staging_is_idempotent_and_deterministic(self):
        self.build()
        self.run_stage()
        first = sd.build_manifest(self.dst)
        self.run_stage()
        self.assertEqual(sd.build_manifest(self.dst), first)

    def test_a_symlink_is_carried_as_a_symlink(self):
        write(self.src, "real.md", "r\n")
        os.symlink("real.md", os.path.join(self.src, "alias.md"))
        report = self.run_stage()
        self.assertIn("alias.md", report["symlinks"])
        self.assertTrue(os.path.islink(os.path.join(self.dst, "alias.md")))
        self.assertEqual(sd.verify_no_loss(self.src, self.dst), [])


class LossProofTest(TempTreeTest):
    """`verify_no_loss` must bite in every direction, or it proves nothing."""

    def setUp(self):
        super().setUp()
        write(self.src, "a.md", "a\n")
        write(self.src, "docs/b.md", "b\n")
        write(self.src, ".git/config", "[core]\n")
        sd.stage(self.src, self.dst)

    def test_it_passes_on_a_faithful_copy(self):
        self.assertEqual(sd.verify_no_loss(self.src, self.dst), [])

    def test_a_deleted_file_reddens_by_name(self):
        os.remove(os.path.join(self.dst, "docs", "b.md"))
        self.assertIn("lost in staging: docs/b.md",
                      sd.verify_no_loss(self.src, self.dst))

    def test_an_edited_file_reddens_by_name(self):
        write(self.dst, "a.md", "tampered\n")
        self.assertIn("content differs: a.md",
                      sd.verify_no_loss(self.src, self.dst))

    def test_an_invented_file_reddens_by_name(self):
        write(self.dst, "extra.md", "e\n")
        self.assertIn("invented by staging: extra.md",
                      sd.verify_no_loss(self.src, self.dst))

    def test_an_excluded_file_that_survived_reddens_by_class(self):
        write(self.dst, ".git/config", "[core]\n")
        problems = sd.verify_no_loss(self.src, self.dst)
        self.assertTrue(any("excluded (vcs) but staged" in p for p in problems),
                        problems)

    def test_assert_clean_reddens_on_a_reintroduced_git_dir(self):
        mkdir(self.dst, ".git/objects")
        write(self.dst, ".git/config", "[core]\n")
        problems = sd.assert_clean(self.dst)
        self.assertIn("a .git directory exists under staging", problems)

    def test_assert_clean_reddens_on_an_empty_directory(self):
        mkdir(self.dst, "hollow")
        self.assertIn("empty directory under staging: hollow",
                      sd.assert_clean(self.dst))

    def test_assert_clean_reddens_on_a_diligence_document(self):
        write(self.dst, "docs/diligence.md", "private\n")
        problems = sd.assert_clean(self.dst)
        self.assertTrue(any("diligence" in p for p in problems), problems)


class ManifestTest(TempTreeTest):

    def setUp(self):
        super().setUp()
        write(self.src, "a.md", "a\n")
        write(self.src, "bin/tool", "#!/bin/sh\n", mode=0o755)
        sd.stage(self.src, self.dst)

    def test_the_manifest_records_only_the_staged_result(self):
        manifest = sd.build_manifest(self.dst)
        self.assertEqual(manifest["schema"], sd.SCHEMA)
        self.assertEqual(manifest["files"], ["a.md", "bin/tool"])
        self.assertEqual(manifest["counts"]["files"], 2)
        self.assertEqual(manifest["counts"]["dirs"], 1)

    def test_the_digest_sees_content(self):
        before = sd.build_manifest(self.dst)["digest"]
        write(self.dst, "a.md", "b\n")
        self.assertNotEqual(sd.build_manifest(self.dst)["digest"], before)

    def test_the_digest_sees_the_executable_bit(self):
        before = sd.build_manifest(self.dst)["digest"]
        os.chmod(os.path.join(self.dst, "bin", "tool"), 0o644)
        self.assertNotEqual(sd.build_manifest(self.dst)["digest"], before)

    def test_diff_manifests_names_a_missing_file(self):
        expected = sd.build_manifest(self.dst)
        os.remove(os.path.join(self.dst, "a.md"))
        problems = sd.diff_manifests(expected, sd.build_manifest(self.dst))
        self.assertTrue(any("missing from staging: a.md" in p
                            for p in problems), problems)


class DonorStagingTest(unittest.TestCase):
    """Against the real donor snapshot; skipped when it is absent."""

    @classmethod
    def setUpClass(cls):
        cls.source = sd.resolve_source()
        if not os.path.isdir(cls.source):
            raise unittest.SkipTest("donor snapshot not present")
        cls.tmp = tempfile.mkdtemp(prefix="aw-donor-stage-")
        cls.dest = os.path.join(cls.tmp, "donor")
        cls.report = sd.stage(cls.source, cls.dest)
        cls.manifest = sd.build_manifest(cls.dest)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tmp"):
            shutil.rmtree(cls.tmp, True)

    def fixture(self):
        with open(FIXTURE) as handle:
            return json.load(handle)

    def test_the_committed_manifest_reproduces(self):
        self.assertEqual(sd.diff_manifests(self.fixture(), self.manifest), [])

    def test_staging_lost_nothing_outside_the_policy(self):
        self.assertEqual(sd.verify_no_loss(self.source, self.dest), [])

    def test_the_acceptance_checks_pass(self):
        self.assertEqual(sd.assert_clean(self.dest), [])

    def test_the_donor_snapshot_carries_no_diligence_document(self):
        """No silent loss: the ratchet rule must have had nothing to drop."""
        self.assertNotIn("diligence_doc", self.report["excluded"])

    def test_the_donor_entrypoints_stay_executable(self):
        for rel in ("agentware.sh", "scripts/agentware"):
            self.assertTrue(sd.is_exec(os.path.join(self.dest, *rel.split("/"))),
                            rel)

    def test_the_donor_module_tree_is_present(self):
        files = set(self.manifest["files"])
        lib = [f for f in files if f.startswith("scripts/agentware_lib/")]
        self.assertGreater(len(lib), 60, "the modular core must be staged")
        self.assertIn("scripts/agentware", files)
        self.assertIn("agentware.sh", files)


class HygieneTest(unittest.TestCase):

    ARTIFACTS = ("tests/_stage_donor.py", "tests/gates/test_stage_donor.py",
                 "tests/fixtures/staging-manifest.json")

    #: The task-12 operator-directed banned-token class, ASSEMBLED from parts so
    #: this file is not itself a hit the zero-tolerance scan has to exempt.
    BANNED = tuple("".join(parts) for parts in
                   (("whats", "app"), ("wa", "bridge"), ("wa-", "bridge"),
                    ("wa_", "bridge")))

    def bodies(self):
        for rel in self.ARTIFACTS:
            with open(os.path.join(REPO, rel)) as handle:
                yield rel, handle.read()

    def test_no_artifact_carries_an_absolute_home_path(self):
        home = os.path.expanduser("~")
        for rel, body in self.bodies():
            self.assertNotIn(home, body, rel)

    def test_no_artifact_carries_a_banned_vendor_token(self):
        for rel, body in self.bodies():
            for token in self.BANNED:
                self.assertNotIn(token, body.lower(), "%s: %s" % (rel, token))

    def test_the_fixture_is_valid_json_at_the_pinned_schema(self):
        with open(os.path.join(REPO, self.ARTIFACTS[2])) as handle:
            payload = json.load(handle)
        self.assertEqual(payload["schema"], sd.SCHEMA)
        self.assertEqual(payload["policy_digest"], sd.policy_digest())

    def test_staging_never_defaults_inside_the_repository(self):
        for path in (sd.DEFAULT_DEST,) + sd.DEFAULT_SOURCES:
            self.assertFalse(os.path.realpath(path).startswith(
                os.path.realpath(REPO) + os.sep), path)

    def test_no_committed_file_exceeds_the_tests_ceiling(self):
        for rel in self.ARTIFACTS:
            if rel.endswith(".json"):
                continue
            with open(os.path.join(REPO, rel)) as handle:
                self.assertLessEqual(len(handle.readlines()), 600, rel)


if __name__ == "__main__":
    unittest.main()
