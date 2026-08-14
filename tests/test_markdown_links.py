"""The markdown link check (feature 260813-modular-core-adoption, task 20).

Two halves. The first proves the checker BITES -- every broken shape it claims
to catch is constructed in a scratch tree and asserted to fail, because a link
checker that silently passes everything is indistinguishable from one that
works. The second runs it over this repo, which is the acceptance criterion.
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _markdown_links as ml  # noqa: E402


def write(root, rel, body):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path


class TempTreeTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-mdlinks-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def problems(self):
        found, _ = ml.check_tree(self.root)
        return found

    def targets(self):
        return sorted(p["target"] for p in self.problems())


class DetectionTest(TempTreeTest):

    def test_a_link_to_a_missing_file_is_caught(self):
        write(self.root, "README.md", "see [the guide](docs/guide.md)\n")
        self.assertEqual(self.targets(), ["docs/guide.md"])

    def test_a_link_to_a_present_file_is_clean(self):
        write(self.root, "docs/guide.md", "# Guide\n")
        write(self.root, "README.md", "see [the guide](docs/guide.md)\n")
        self.assertEqual(self.problems(), [])

    def test_a_relative_link_resolves_from_the_linking_file(self):
        write(self.root, "docs/a.md", "up to [root](../README.md)\n")
        write(self.root, "README.md", "# Root\n")
        self.assertEqual(self.problems(), [])

    def test_an_image_target_is_checked_too(self):
        write(self.root, "README.md", "![hero](docs/assets/hero.png)\n")
        self.assertEqual(self.targets(), ["docs/assets/hero.png"])

    def test_a_reference_definition_is_checked(self):
        write(self.root, "README.md", "see [guide][g]\n\n[g]: docs/guide.md\n")
        self.assertEqual(self.targets(), ["docs/guide.md"])

    def test_a_missing_anchor_in_a_target_file_is_caught(self):
        write(self.root, "docs/guide.md", "# Guide\n\n## Setup steps\n")
        write(self.root, "README.md",
              "[ok](docs/guide.md#setup-steps) [no](docs/guide.md#teardown)\n")
        self.assertEqual(self.targets(), ["docs/guide.md#teardown"])

    def test_a_missing_anchor_in_the_same_file_is_caught(self):
        write(self.root, "README.md", "# Top\n\n[here](#top) [gone](#bottom)\n")
        self.assertEqual(self.targets(), ["#bottom"])

    def test_an_anchor_with_punctuation_and_code_slugs_like_github(self):
        write(self.root, "docs/loop.md",
              "## Per-phase routing — the `hybrid` profile (v2)\n")
        write(self.root, "README.md",
              "[x](docs/loop.md#per-phase-routing--the-hybrid-profile-v2)\n")
        self.assertEqual(self.problems(), [])

    def test_an_external_link_is_never_resolved_or_fetched(self):
        write(self.root, "README.md",
              "[a](https://example.invalid/nope) [b](mailto:x@example.invalid)\n")
        self.assertEqual(self.problems(), [])

    def test_a_link_inside_a_fenced_code_block_is_ignored(self):
        write(self.root, "README.md",
              "```md\n[sample](does/not/exist.md)\n```\n")
        self.assertEqual(self.problems(), [])

    def test_a_placeholder_target_is_not_asserted_to_be_a_real_file(self):
        write(self.root, "README.md",
              "[a](../<knowledge-dir>/work/x.md) [b](docs/…/y.md) "
              "[c]($KDIR/MAIN.md) [d](docs/{{name}}.md)\n")
        self.assertEqual(self.problems(), [])

    def test_a_root_absolute_target_resolves_from_the_tree_root(self):
        write(self.root, "docs/a.md", "[l](/LICENSE)\n")
        write(self.root, "LICENSE", "apache\n")
        self.assertEqual(self.problems(), [])


class ExemptionTest(TempTreeTest):

    def test_a_template_link_resolving_in_the_knowledge_dir_is_exempt(self):
        write(self.root, "templates/learning-template.md",
              "back to [MAIN](../MAIN.md)\n")
        self.assertEqual(self.problems(), [])

    def test_the_exemption_does_not_leak_outside_its_prefix(self):
        write(self.root, "templates/learning-template.md",
              "back to [MAIN](../MAIN.md)\n")
        write(self.root, "docs/guide.md", "back to [MAIN](../MAIN.md)\n")
        self.assertEqual(self.targets(), ["../MAIN.md"])

    def test_an_anchor_is_still_checked_inside_an_exempt_file(self):
        """The exemption forgives PATHS resolved elsewhere, not typos."""
        write(self.root, "templates/t.md", "# Top\n\n[x](#nowhere)\n")
        self.assertEqual(self.targets(), ["#nowhere"])

    def test_every_exemption_row_carries_a_prefix_reason_and_destination(self):
        self.assertTrue(ml.RESOLVED_ELSEWHERE)
        for row in ml.RESOLVED_ELSEWHERE:
            self.assertTrue(row["prefix"].endswith("/"), row)
            self.assertTrue(row["resolves_in"], row)
            self.assertGreater(len(row["reason"]), 40, row)

    def test_no_exemption_row_is_stale_in_this_repo(self):
        self.assertEqual(ml.unclaimed_exemptions(REPO_ROOT), [])


class ThisRepoTest(unittest.TestCase):

    def test_the_repo_has_markdown_files_to_check(self):
        self.assertGreater(len(ml.markdown_files(REPO_ROOT)), 20)

    def test_the_markdown_link_check_passes_over_this_repo(self):
        problems, files = ml.check_tree(REPO_ROOT)
        self.assertEqual(problems, [],
                         "%d broken link(s) across %d file(s)"
                         % (len(problems), len(files)))

    def test_the_cli_exits_zero_over_this_repo(self):
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = ml.main(["--tree", REPO_ROOT, "--json"])
        self.assertEqual(code, 0, buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
