"""The repository-standard gate (feature 260813-modular-core-adoption, task 77).

Two halves, the shape every ratchet in this corpus carries.

The FIRST half proves the checker BITES. A gate over community-health files is
the easiest kind to write green and leave useless: it reads files the author has
just written, so it agrees with itself by construction. So the scratch tree is a
copy of the REAL files -- the positive control is the shipped content -- and each
mutation is ONE edit away from it and must be named by its own arm.

Two mutations are deliberately PAIRS rather than singles, because the arm they
prove is not the obvious ban:

  * arm B forbids a FOREIGN licence identifier, but `NOTICE` must be able to say
    the code of conduct is CC BY 4.0. So one mutation names an undeclared licence
    (red) and its twin names the SAME licence with an `ADOPTED` row for it
    (green). A ban that cannot tell those apart forbids attribution, which is the
    opposite of what a NOTICE is for.
  * arm C is two-directional: dropping the block from `NOTICE` fails, and so does
    keeping the block while the FILE stops adopting the text. A one-directional
    attribution check certifies a NOTICE that attributes nothing real.

The SECOND half runs the checker over this repository, which is the acceptance
criterion, plus the two facts the checker deliberately does not import: that its
changelog version regex is the one the signed-tag release channel really uses,
and that the markdown link check passes over every new document.
"""

from tests._paths import TESTS_DIR

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _markdown_links as ml  # noqa: E402
import _repo_standard as rs  # noqa: E402


def read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as handle:
        return handle.read()


def write(root, rel, body):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)


class ScratchTreeTest(unittest.TestCase):
    """The real community files, copied; the positive control is what ships."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-repostd-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for rel, _what, _floor, _sections in rs.REQUIRED:
            shutil.copy2(os.path.join(REPO_ROOT, rel),
                         self._make(rel))
        for rel, _field in rs.LICENCE_MANIFESTS:
            shutil.copy2(os.path.join(REPO_ROOT, rel), self._make(rel))

    def _make(self, rel):
        dst = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        return dst

    # ------------------------------------------------------------- utilities

    def problems(self, arm=None):
        """Every arm EXCEPT F, which scans a whole git tree rather than this
        handful of files; F gets its own injected-needle test below."""
        found = []
        for check in (rs.check_required, rs.check_licence, rs.check_notice,
                      rs.check_templates, rs.check_changelog):
            found.extend(check(self.root))
        if arm:
            found = [p for p in found if p.startswith(arm + ":")]
        return found

    def edit(self, rel, old, new, count=-1):
        body = read(self.root, rel)
        self.assertIn(old, body, "fixture drift: %r not in %s" % (old, rel))
        write(self.root, rel, body.replace(old, new, count))

    def assertNamed(self, arm, needle):
        found = self.problems(arm)
        self.assertTrue(found, "arm %s did not fire" % arm)
        self.assertTrue(any(needle in p for p in found),
                        "arm %s fired without naming %r: %s"
                        % (arm, needle, found))

    # ------------------------------------------------------- positive control

    def test_the_shipped_community_files_are_clean(self):
        self.assertEqual(self.problems(), [])

    # ------------------------------------------------------------------ arm A

    def test_a_missing_required_file_is_named(self):
        os.remove(os.path.join(self.root, "SECURITY.md"))
        self.assertNamed("A", "SECURITY.md")

    def test_a_stub_that_merely_exists_is_named(self):
        write(self.root, "CONTRIBUTING.md", "# Contributing\n\nTBD.\n")
        self.assertNamed("A", "under the")

    def test_a_dropped_section_is_named(self):
        self.edit("SECURITY.md", "## Reporting a vulnerability",
                  "## How to shout about it")
        self.assertNamed("A", "Reporting a vulnerability")

    def test_an_unrouted_community_file_is_named(self):
        self.edit("README.md", "(CODE_OF_CONDUCT.md)", "(docs/GUIDE.md)")
        self.assertNamed("A", "CODE_OF_CONDUCT.md")

    def test_a_case_drifted_filename_is_named(self):
        """`os.path.isfile` says yes to the wrong case on macOS; GitHub's
        tooling and a Linux runner both say no."""
        os.rename(os.path.join(self.root, ".github/PULL_REQUEST_TEMPLATE.md"),
                  os.path.join(self.root, ".github/pull_request_template.md"))
        self.assertNamed("A", "case must match exactly")

    def test_a_pr_template_that_stops_asking_the_hard_question_is_named(self):
        self.edit(".github/PULL_REQUEST_TEMPLATE.md", "R-MOD-11", "the rules")
        self.assertNamed("A", "R-MOD-11")

    def test_a_gutted_pr_checklist_is_named(self):
        self.edit(".github/PULL_REQUEST_TEMPLATE.md", "- [ ]", "- ")
        self.assertNamed("A", "checkboxes")

    # ------------------------------------------------------------------ arm B

    def test_a_licence_file_that_is_not_the_declared_licence_is_named(self):
        self.edit("LICENSE", "3. Grant of Patent License",
                  "3. No Patent License")
        self.assertNamed("B", "Grant of Patent License")

    def test_a_dropped_licence_declaration_is_named(self):
        self.edit("README.md", "[Apache-2.0](LICENSE)", "the licence")
        self.assertNamed("B", "the licence footer")

    def test_a_manifest_declaring_a_different_licence_is_named(self):
        path = os.path.join(self.root, "webui", "package.json")
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest["license"] = "MIT"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
        self.assertNamed("B", "package.json")

    def test_an_undeclared_foreign_licence_is_named(self):
        self.edit("CONTRIBUTING.md", "## Ground rules",
                  "## Ground rules\n\nParts of this are under the MIT License.")
        self.assertNamed("B", "MIT")

    def test_the_same_foreign_licence_is_legal_once_a_row_declares_it(self):
        """The twin of the mutation above: a NOTICE must be ABLE to attribute
        third-party text, so the ban is on an UNDECLARED id, not on the word."""
        self.assertEqual(
            [p for p in self.problems("B") if "CC-BY-4.0" in p], [],
            "NOTICE names CC BY 4.0 and an ADOPTED row declares it")
        original = rs.ADOPTED
        try:
            rs.ADOPTED = ()
            self.assertNamed("B", "CC-BY-4.0")
        finally:
            rs.ADOPTED = original

    # ------------------------------------------------------------------ arm C

    def test_a_notice_that_stops_attributing_adopted_text_is_named(self):
        self.edit("NOTICE", "https://www.contributor-covenant.org/version/2/1/"
                            "code_of_conduct.html", "(see the file)")
        self.assertNamed("C", "canonical URL")

    def test_a_file_that_stops_adopting_while_the_notice_claims_it_is_named(self):
        """The other direction: the attribution outlived the adoption."""
        self.edit("CODE_OF_CONDUCT.md", "Contributor Covenant", "House Rules")
        self.assertNamed("C", "attribution")

    def test_a_copyright_holder_that_disagrees_with_the_readme_is_named(self):
        self.edit("NOTICE", "Copyright 2026", "Copyright 2019")
        self.assertNamed("C", "disagrees")

    # ------------------------------------------------------------------ arm D

    def test_a_template_without_frontmatter_is_named(self):
        body = read(self.root, ".github/ISSUE_TEMPLATE/bug_report.md")
        write(self.root, ".github/ISSUE_TEMPLATE/bug_report.md",
              body.split("---", 2)[2])
        self.assertNamed("D", "frontmatter")

    def test_a_template_with_an_empty_chooser_field_is_named(self):
        self.edit(".github/ISSUE_TEMPLATE/bug_report.md", "name: Bug report",
                  "name:")
        self.assertNamed("D", "'name'")

    def test_a_chooser_config_with_no_verdict_is_named(self):
        write(self.root, ".github/ISSUE_TEMPLATE/config.yml", "# nothing\n")
        self.assertNamed("D", "blank_issues_enabled")

    def test_disabling_blank_issues_with_no_template_closes_the_front_door(self):
        for name in ("bug_report.md", "feature_request.md"):
            os.remove(os.path.join(self.root, ".github/ISSUE_TEMPLATE", name))
        self.assertNamed("D", "front door")

    # ------------------------------------------------------------------ arm E

    def test_a_changelog_without_unreleased_is_named(self):
        self.edit("CHANGELOG.md", "## [Unreleased]", "## [Next]")
        self.assertNamed("E", "Unreleased")

    def test_a_version_the_release_channel_would_refuse_is_named(self):
        self.edit("CHANGELOG.md", "## [Unreleased]",
                  "## [Unreleased]\n\n## [1.0] - 2026-08-13\n\n### Added\n\n- x\n")
        self.assertNamed("E", "signed-tag channel")

    def test_a_version_without_an_iso_date_is_named(self):
        self.edit("CHANGELOG.md", "## [Unreleased]",
                  "## [Unreleased]\n\n## [1.0.0] - soon\n\n### Added\n\n- x\n")
        self.assertNamed("E", "ISO date")

    def test_versions_out_of_order_are_named(self):
        self.edit("CHANGELOG.md", "## [Unreleased]",
                  "## [Unreleased]\n\n## [1.0.0] - 2026-08-01\n\n### Added\n\n"
                  "- x\n\n## [2.0.0] - 2026-08-02\n\n### Added\n\n- y\n")
        self.assertNamed("E", "newest-first")

    def test_an_invented_section_name_is_named(self):
        self.edit("CHANGELOG.md", "### Added", "### Improvements", 1)
        self.assertNamed("E", "Keep a Changelog section")

    def test_an_empty_section_is_named(self):
        self.edit("CHANGELOG.md", "### Removed", "### Fixed\n\n### Removed", 1)
        self.assertNamed("E", "is empty")


class PersonalDataArmTest(unittest.TestCase):
    """Arm F, which scans the WHOLE shipped tree rather than a copy of it."""

    def test_the_repository_carries_no_live_operator_identity(self):
        self.assertEqual(rs.check_personal_data(REPO_ROOT), [])

    def test_the_arm_bites_on_an_injected_needle(self):
        """Proven without depending on the runner having a knowledge base: a
        needle that IS in the tree must be reported, or the clean result above
        means nothing."""
        hits = rs.check_personal_data(REPO_ROOT, ["Contributor Covenant"])
        self.assertTrue(hits, "the scanner found nothing it must find")
        self.assertTrue(all(h.startswith("F: ") for h in hits), hits)

    def test_the_arm_uses_the_same_scanner_the_audit_runs(self):
        sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
        import agentware_lib.knowledge.privacy as privacy
        import agentware_lib.ops.audit as audit
        self.assertIs(audit.privacy, privacy)


class ThisRepoTest(unittest.TestCase):

    def test_the_repo_standard_check_passes(self):
        found = rs.problems(REPO_ROOT)
        self.assertEqual(found, [], "\n".join(found))

    def test_the_cli_exits_zero_over_this_repo(self):
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = rs.main(["--tree", REPO_ROOT, "--json"])
        self.assertEqual(code, 0, buffer.getvalue())

    def test_the_changelog_version_shape_is_the_release_channels_own(self):
        """The checker deliberately does not import the package (it must run
        from a bare clone), so the agreement is asserted HERE instead of
        duplicated silently: a version this gate accepts must be one the
        updater would accept as a tag, and one it rejects must be refused."""
        sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
        import agentware_lib.core.pkgchannel as pkgchannel
        for good in ("1.0.0", "0.2.13", "10.20.30"):
            self.assertTrue(rs.CHANGELOG_VERSION_RE.match(good), good)
            self.assertTrue(pkgchannel.PKG_CHANNEL_RE.match("v" + good), good)
        for bad in ("1.0", "v1.0.0", "1.0.0-rc1", "latest"):
            self.assertIsNone(rs.CHANGELOG_VERSION_RE.match(bad), bad)
            self.assertIsNone(pkgchannel.PKG_CHANNEL_RE.match("v" + bad), bad)

    def test_every_new_document_passes_the_markdown_link_check(self):
        for rel, _what, _floor, _sections in rs.REQUIRED:
            if not rel.endswith(".md"):
                continue
            self.assertEqual(ml.check_one(REPO_ROOT, rel, {}), [], rel)


if __name__ == "__main__":
    unittest.main()
