"""The layered test-corpus contract (feature 260813, task 58).

Three claims, each with its own refutation:

  1. STRUCTURE -- no test file sits flat under `tests/`, every one lives in a
     declared tier, and none is over the line cap.
  2. LEDGER HONESTY -- `_test_layout.RELOCATIONS` describes a move that actually
     happened: no key still exists as a file, every successor does, and nothing
     resolves to two places.
  3. THE CHECKS BITE -- the pure functions are run against scratch trees that
     violate each rule, so a green result here can never be a green result over
     an empty set.

Stdlib `unittest` only. The structure checks read the real corpus; the
refutations never touch it.
"""

from tests._paths import REPO_ROOT, TESTS_DIR

import os
import shutil
import tempfile
import unittest

from tests._test_layout import (
    RELOCATIONS, TEST_FILE_LINE_CAP, TIERS, flat_test_files,
    oversized_test_files, relocate, resolves, untiered_test_files)
# NOT `from ... import test_files`: pytest collects any module-level callable
# named `test_*`, so importing it by name turns the helper into a "test" that
# errors on its required argument.
from tests._test_layout import test_files as corpus_files


class CorpusStructureTest(unittest.TestCase):
    """The real corpus, measured."""

    def test_no_flat_test_file_remains(self):
        self.assertEqual(flat_test_files(TESTS_DIR), [])

    def test_every_test_file_is_inside_a_declared_tier(self):
        self.assertEqual(untiered_test_files(TESTS_DIR), [])

    def test_no_test_file_exceeds_the_line_cap(self):
        self.assertEqual(oversized_test_files(TESTS_DIR), {})

    def test_the_corpus_is_big_enough_for_those_checks_to_mean_anything(self):
        """Non-vacuity: all three checks above pass over an empty corpus."""
        files = corpus_files(TESTS_DIR)
        self.assertGreater(len(files), 150)
        for tier in TIERS:
            path = os.path.join(TESTS_DIR, tier.replace("/", os.sep))
            self.assertTrue(os.path.isdir(path), tier)
            self.assertTrue([f for f in files if f.startswith(path + os.sep)],
                            "tier %s holds no test file" % tier)

    def test_every_tier_directory_is_an_importable_package(self):
        for tier in TIERS:
            init = os.path.join(TESTS_DIR, tier.replace("/", os.sep),
                                "__init__.py")
            self.assertTrue(os.path.isfile(init), init)


class LedgerHonestyTest(unittest.TestCase):
    """The ledger records a move that happened -- not an intention."""

    def test_no_relocated_module_still_exists_at_its_old_path(self):
        for old in RELOCATIONS:
            path = os.path.join(REPO_ROOT, old.replace(".", os.sep) + ".py")
            self.assertFalse(os.path.exists(path), old)

    def test_every_successor_exists(self):
        for old, successors in RELOCATIONS.items():
            self.assertTrue(successors, old)
            for new in successors:
                path = os.path.join(REPO_ROOT, new.replace(".", os.sep) + ".py")
                self.assertTrue(os.path.isfile(path), "%s -> %s" % (old, new))

    def test_no_successor_is_listed_twice_for_one_module(self):
        for old, successors in RELOCATIONS.items():
            self.assertEqual(len(set(successors)), len(successors), old)

    def test_no_module_is_its_own_successor(self):
        """A self-mapping would make the ledger a no-op that still looks full."""
        for old, successors in RELOCATIONS.items():
            self.assertNotIn(old, successors, old)

    def test_a_successor_is_never_itself_a_relocated_key(self):
        """One hop only: a chain would leave the second hop unresolvable."""
        for old, successors in RELOCATIONS.items():
            for new in successors:
                self.assertNotIn(new, RELOCATIONS, "%s -> %s" % (old, new))

    def test_relocate_leaves_an_unlisted_module_alone(self):
        nid = "tests.gates.test_test_layout.LedgerHonestyTest.test_x"
        self.assertEqual(relocate(nid), (nid,))

    def test_relocate_rewrites_only_the_module_half(self):
        old = sorted(RELOCATIONS)[0]
        new = RELOCATIONS[old][0]
        self.assertEqual(relocate(old + ".Klass.test_thing"),
                         (new + ".Klass.test_thing",))

    def test_resolves_says_NO_when_no_successor_collects_the_tail(self):
        old = sorted(RELOCATIONS)[0]
        new = RELOCATIONS[old][0]
        self.assertTrue(resolves(old + ".K.t", {new + ".K.t"}))
        self.assertFalse(resolves(old + ".K.gone", {new + ".K.t"}))


class ChecksBiteTest(unittest.TestCase):
    """Every structure check, refuted on a scratch corpus."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-layout-")
        self.addCleanup(shutil.rmtree, self.root, True)
        for tier in TIERS:
            os.makedirs(os.path.join(self.root, tier.replace("/", os.sep)))

    def write(self, rel, lines=1):
        path = os.path.join(self.root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# x\n" * lines)
        return path

    def test_a_clean_scratch_corpus_passes_all_three(self):
        self.write("unit/ops/test_ok.py", 10)
        self.assertEqual(flat_test_files(self.root), [])
        self.assertEqual(untiered_test_files(self.root), [])
        self.assertEqual(oversized_test_files(self.root), {})

    def test_a_flat_test_file_is_caught(self):
        path = self.write("test_sneaky.py", 5)
        self.assertEqual(flat_test_files(self.root), [path])

    def test_a_test_file_outside_every_tier_is_caught(self):
        path = self.write("helpers/test_stray.py", 5)
        self.assertEqual(untiered_test_files(self.root), [path])

    def test_a_file_one_line_over_the_cap_is_caught(self):
        path = self.write("unit/ops/test_fat.py", TEST_FILE_LINE_CAP + 1)
        self.assertEqual(oversized_test_files(self.root),
                         {path: TEST_FILE_LINE_CAP + 1})

    def test_a_file_exactly_at_the_cap_is_allowed(self):
        self.write("unit/ops/test_edge.py", TEST_FILE_LINE_CAP)
        self.assertEqual(oversized_test_files(self.root), {})

    def test_fixture_data_is_not_counted_as_corpus(self):
        """`tests/fixtures/swe_tasks/*/test_hidden.py` are benchmark INPUTS."""
        self.write("fixtures/swe_tasks/x/test_hidden.py", TEST_FILE_LINE_CAP + 50)
        self.assertEqual(corpus_files(self.root), [])
        self.assertEqual(oversized_test_files(self.root), {})
        self.assertEqual(untiered_test_files(self.root), [])


if __name__ == "__main__":
    unittest.main()
