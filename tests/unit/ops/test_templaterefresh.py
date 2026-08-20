"""Owning unit file for `agentware_lib.ops.templaterefresh` (R-MOD-06).

Migration 003's body: refresh the templates `init` INSTALLED into a knowledge
dir, which `init` itself can never revisit (it copies create-if-absent).

The suite is organised around the ONE judgement the module makes — may this file
be overwritten? — because every other property (idempotency, the notice, the
summary shape) is downstream of it. Both directions are asserted from real bytes:
a template byte-identical to a generation this package SHIPPED is refreshed, and
a legacy-shaped template that is NOT one of ours is left alone byte-for-byte.
The pristine fixtures are the actual historical files, reconstructed from the
digest table's own provenance rather than invented, so a fixture that stops
matching the table is a fixture that has stopped testing the module.
"""

from tests._paths import REPO_ROOT

import hashlib
import os
import shutil
import sys
import tempfile
import unittest

if os.path.join(REPO_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import agentware_lib.ops.templaterefresh as templaterefresh  # noqa: E402


#: A frontmatter-less template body, in the shape every pre-reconciliation
#: template had: an H1, a blockquote header, no `---` block anywhere.
LEGACY_BODY = ("# [Topic Name]\n\n"
               "> **Created**: YYYY-MM-DD  \n"
               "> **Tags**: `tag-one`, `tag-two`\n\n"
               "## Summary\n\nOne paragraph.\n")


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TemplateRefreshTestCase(unittest.TestCase):
    """A scratch knowledge dir with an installed `templates/` directory."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-tpl-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.tdir = os.path.join(self.kdir, "templates")
        os.makedirs(self.tdir)

    def write(self, name, text):
        path = os.path.join(self.tdir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def read(self, name):
        with open(os.path.join(self.tdir, name), "r", encoding="utf-8") as handle:
            return handle.read()

    def shipped(self, name):
        path = os.path.join(templaterefresh.shipped_templates_dir(), name)
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def install_pristine_legacy(self, name):
        """Install a file whose bytes ARE a shipped legacy generation of `name`.

        Reconstructed from the module's own table: the digest is the authority for
        "unedited", so the fixture is the byte string that hashes to it. Written
        through a search of this repository's history, so a table row nobody can
        produce a file for is a row this test cannot use.
        """
        for text in _historical_texts(name):
            if _digest(text) in templaterefresh.LEGACY_TEMPLATE_DIGESTS[name]:
                return self.write(name, text)
        self.skipTest("no historical text available for %s" % name)


def _historical_texts(name):
    """Every version of `templates/<name>` this repository has ever committed.

    Read from git, newest first, and EMPTY when git is unavailable or the clone is
    shallow — the caller skips rather than fails, because a shallow clone is a
    legitimate way to run this suite and the digest table is deliberately pinned
    by value precisely so the MODULE never needs git at run time.
    """
    import subprocess
    try:
        rel = "templates/%s" % name
        commits = subprocess.run(["git", "log", "--format=%H", "--", rel],
                                 cwd=REPO_ROOT, capture_output=True, text=True,
                                 timeout=30)
        if commits.returncode != 0:
            return []
        out = []
        for sha in commits.stdout.split():
            blob = subprocess.run(["git", "show", "%s:%s" % (sha, rel)],
                                  cwd=REPO_ROOT, capture_output=True, text=True,
                                  timeout=30)
            if blob.returncode == 0:
                out.append(blob.stdout)
        return out
    except (OSError, subprocess.SubprocessError):
        return []


class DigestTableTest(TemplateRefreshTestCase):
    """The table is the module's whole authority for the word 'unmodified'."""

    def test_every_row_names_a_template_the_package_still_ships(self):
        for name in templaterefresh.LEGACY_TEMPLATE_DIGESTS:
            self.assertTrue(
                os.path.isfile(os.path.join(
                    templaterefresh.shipped_templates_dir(), name)),
                "%s has a legacy digest row but is no longer shipped — the step "
                "would classify it `foreign` and never refresh it" % name)

    def test_no_row_holds_a_digest_of_a_currently_shipped_file(self):
        """A CURRENT file listed as legacy would make the step rewrite a file to
        itself forever — reported as a change on every single run."""
        for name, digests in templaterefresh.LEGACY_TEMPLATE_DIGESTS.items():
            self.assertNotIn(_digest(self.shipped(name)), digests,
                             "%s's shipped digest is listed as LEGACY" % name)

    def test_every_shipped_template_is_itself_conformant(self):
        """The refresh SOURCE must satisfy the contract it is refreshing toward,
        or the step trades one T1 finding for another."""
        for name in sorted(os.listdir(templaterefresh.shipped_templates_dir())):
            if name.endswith(".md"):
                self.assertTrue(templaterefresh._has_frontmatter(self.shipped(name)),
                                "shipped %s carries no frontmatter" % name)

    def test_every_digest_is_a_sha256_hex(self):
        for name, digests in templaterefresh.LEGACY_TEMPLATE_DIGESTS.items():
            for d in digests:
                self.assertEqual(len(d), 64, "%s: %r is not a sha256 hex" % (name, d))
                self.assertEqual(d, d.lower().strip())
                int(d, 16)


class ClassifyTest(TemplateRefreshTestCase):
    """One judgement, six answers — asserted at the classifier, where it is made."""

    def _classify(self, name):
        return templaterefresh._classify_template(
            os.path.join(templaterefresh.shipped_templates_dir(), name),
            os.path.join(self.tdir, name))

    def test_a_pristine_legacy_template_is_refreshed(self):
        self.install_pristine_legacy("learning-template.md")
        self.assertEqual(self._classify("learning-template.md"), "refresh")

    def test_an_operator_modified_legacy_template_is_not(self):
        self.write("learning-template.md", LEGACY_BODY + "\nMy own notes.\n")
        self.assertEqual(self._classify("learning-template.md"), "modified")

    def test_a_template_that_already_carries_frontmatter_is_current(self):
        self.write("learning-template.md", self.shipped("learning-template.md"))
        self.assertEqual(self._classify("learning-template.md"), "current")

    def test_a_template_the_package_does_not_ship_is_foreign(self):
        self.write("team-runbook-template.md", LEGACY_BODY)
        self.assertEqual(self._classify("team-runbook-template.md"), "foreign")

    def test_undecodable_bytes_are_reported_not_raised(self):
        with open(os.path.join(self.tdir, "learning-template.md"), "wb") as handle:
            handle.write(b"\xff\xfe\x00not utf-8")
        self.assertEqual(self._classify("learning-template.md"), "unreadable")

    def test_a_non_conformant_source_refuses_to_be_a_refresh_target(self):
        """A package regression must not become a rewrite of operator files."""
        src_dir = tempfile.mkdtemp(prefix="agentware-src-")
        self.addCleanup(shutil.rmtree, src_dir, True)
        src = os.path.join(src_dir, "learning-template.md")
        with open(src, "w", encoding="utf-8") as handle:
            handle.write(LEGACY_BODY)
        self.install_pristine_legacy("learning-template.md")
        self.assertEqual(
            templaterefresh._classify_template(
                src, os.path.join(self.tdir, "learning-template.md")),
            "source-not-conformant")


class RefreshTest(TemplateRefreshTestCase):
    """The file-level behaviour: what is written, what is not, and what repeats."""

    def test_a_pristine_legacy_template_gains_the_shipped_bytes(self):
        self.install_pristine_legacy("learning-template.md")
        summary = templaterefresh.refresh_installed_templates(self.kdir)
        self.assertEqual(summary["refreshed"], ["learning-template.md"])
        self.assertEqual(self.read("learning-template.md"),
                         self.shipped("learning-template.md"))

    def test_an_operator_modified_template_is_left_byte_identical(self):
        mine = LEGACY_BODY + "\n> my house style, no frontmatter, on purpose\n"
        self.write("learning-template.md", mine)
        summary = templaterefresh.refresh_installed_templates(self.kdir)
        self.assertEqual(summary["modified"], ["learning-template.md"])
        self.assertEqual(summary["refreshed"], [])
        self.assertEqual(self.read("learning-template.md"), mine)

    def test_the_step_is_idempotent(self):
        self.install_pristine_legacy("learning-template.md")
        first = templaterefresh.refresh_installed_templates(self.kdir)
        after_first = self.read("learning-template.md")
        second = templaterefresh.refresh_installed_templates(self.kdir)
        self.assertEqual(first["refreshed"], ["learning-template.md"])
        self.assertEqual(second["refreshed"], [])
        self.assertEqual(second["current"], ["learning-template.md"])
        self.assertEqual(self.read("learning-template.md"), after_first)

    def test_a_knowledge_dir_with_no_templates_directory_is_a_clean_no_op(self):
        bare = tempfile.mkdtemp(prefix="agentware-bare-")
        self.addCleanup(shutil.rmtree, bare, True)
        summary = templaterefresh.refresh_installed_templates(bare)
        self.assertFalse(summary["engaged"])
        self.assertEqual(summary["reason"], "no installed templates/ directory")
        self.assertFalse(os.path.exists(os.path.join(bare, "templates")))

    def test_non_markdown_files_are_never_touched(self):
        path = os.path.join(self.tdir, "notes.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("scratch\n")
        summary = templaterefresh.refresh_installed_templates(self.kdir)
        for key in ("refreshed", "current", "modified", "foreign"):
            self.assertNotIn("notes.txt", summary[key])

    def test_every_classifier_outcome_has_a_summary_key(self):
        empty = templaterefresh._empty_summary()
        for outcome, key in templaterefresh._REFRESH_OUTCOME_KEYS.items():
            self.assertIn(key, empty,
                          "outcome %r lands in absent summary key %r"
                          % (outcome, key))


class ReportTest(TemplateRefreshTestCase):
    """The notice is CHANGE-only: a standing condition must not narrate forever."""

    def _report(self, summary):
        from io import StringIO
        from unittest import mock
        buf = StringIO()
        with mock.patch.object(templaterefresh.sys, "stderr", buf):
            templaterefresh._report(summary)
        return buf.getvalue()

    def test_a_refresh_is_announced(self):
        summary = templaterefresh._empty_summary()
        summary["refreshed"] = ["learning-template.md"]
        out = self._report(summary)
        self.assertIn("migration-003", out)
        self.assertIn("refreshed 1", out)

    def test_an_operator_modified_template_alone_says_nothing(self):
        """It is still there next run, and the run after — a permanent notice is
        how an operator learns to stop reading the channel. `templates lint` T1
        owns that finding, because it can name the file."""
        summary = templaterefresh._empty_summary()
        summary["modified"] = ["learning-template.md"]
        self.assertEqual(self._report(summary), "")

    def test_a_no_op_run_says_nothing(self):
        self.assertEqual(self._report(templaterefresh._empty_summary()), "")


class RegistrationTest(TemplateRefreshTestCase):
    """The step is REGISTERED — not merely importable."""

    def setUp(self):
        super().setUp()
        import agentware_lib.features.migratecmd as migratecmd
        self.mig = migratecmd

    def test_the_step_is_in_the_migration_table_as_additive_version_three(self):
        rows = [(v, tier, name) for (v, tier, name, _fn) in self.mig._KB_MIGRATIONS]
        self.assertIn((3, "additive", "template-refresh"), rows)

    def test_the_registered_callable_is_the_template_refresh_body(self):
        fn = [f for (v, _t, _n, f) in self.mig._KB_MIGRATIONS if v == 3][0]
        self.assertIs(fn, self.mig._migration_003_template_refresh)

    def test_the_package_schema_version_is_the_table_max(self):
        self.assertEqual(self.mig.KB_SCHEMA_VERSION,
                         max(v for (v, _t, _n, _f) in self.mig._KB_MIGRATIONS))
        self.assertGreaterEqual(self.mig.KB_SCHEMA_VERSION, 3)

    def test_applying_through_the_runner_actually_executes_the_body(self):
        """The table row is only half the claim — the registered callable has to
        RUN when the runner does, or the step is listed and inert."""
        self.install_pristine_legacy("learning-template.md")
        self.mig._migration_003_template_refresh(self.kdir)
        self.assertEqual(self.read("learning-template.md"),
                         self.shipped("learning-template.md"))


class ModuleContractTest(unittest.TestCase):

    def test_the_module_publishes_its_docstring_labels(self):
        doc = templaterefresh.__doc__ or ""
        for label in ("Owns:", "Does NOT own:", "Layer deps:"):
            self.assertIn(label, doc,
                          "%s is missing the %s label the manifest parses"
                          % (templaterefresh.__name__, label))


if __name__ == "__main__":
    unittest.main()
