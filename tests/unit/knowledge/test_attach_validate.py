"""Tests for `agentware attach` — attach-existing-repo conformance validation.

Feature 260625-team-mode-onboarding-fork. Before team-mode adopts an existing
repo as the shared KB, it must conform: expected dirs present, `index validate`
passes, and `index rebuild` is a parity no-op. Non-conformant repos are refused;
`--migrate` fills missing STRUCTURE idempotently.

Hermetic: every KB is a fresh tempdir; entries are created with synthetic content
via the real `learn` CLI (R-LOC-03). The real ~/.agentware config is never touched
(attach/learn write only to the KB dir, never HOME).
"""

import json
import os
import shutil
import tempfile
import unittest

from tests._fixtures import load_cli, run_cli


def _make_conformant_kb(with_entries=True):
    """Build a fresh conformant KB: all required dirs + valid seeded index +
    (optionally) a couple of learn entries that carry frontmatter so rebuild is
    a parity no-op."""
    mod = load_cli()
    kdir = tempfile.mkdtemp(prefix="agentware-attach-")
    for sub in mod.ATTACH_REQUIRED_DIRS:
        os.makedirs(os.path.join(kdir, sub), exist_ok=True)
        if sub in mod.KNOWLEDGE_SUBDIRS:
            with open(os.path.join(kdir, sub, "index.md"), "w") as f:
                f.write("# %s\n\n_Roster._\n" % sub.capitalize())
    mod.save_index(kdir, {"entries": [], "tags": {}})
    if with_entries:
        for topic in ("alpha-note", "beta-note"):
            code, _o, err = run_cli(
                ["learn", "--topic", topic, "--summary", "S %s" % topic,
                 "--tags", "x,y", "--content", "Body for %s." % topic], kdir)
            assert code == 0, err
    return kdir


def _attach(kdir, migrate=False):
    argv = ["attach", "--path", kdir, "--format", "json"]
    if migrate:
        argv.append("--migrate")
    # attach resolves its own --path; AGENTWARE_KNOWLEDGE_DIR is irrelevant here.
    code, out, err = run_cli(argv, kdir)
    return code, json.loads(out), err


class AttachConformanceTests(unittest.TestCase):
    def test_conformant_repo_passes(self):
        kdir = _make_conformant_kb(with_entries=True)
        self.addCleanup(shutil.rmtree, kdir, True)
        code, report, _ = _attach(kdir)
        self.assertEqual(code, 0, report)
        self.assertTrue(report["ok"])
        self.assertEqual(report["missing_dirs"], [])
        self.assertEqual(report["parity_errors"], [])

    def test_empty_conformant_repo_passes(self):
        kdir = _make_conformant_kb(with_entries=False)
        self.addCleanup(shutil.rmtree, kdir, True)
        code, report, _ = _attach(kdir)
        self.assertEqual(code, 0, report)
        self.assertTrue(report["ok"])

    def test_missing_learnings_dir_refused(self):
        kdir = _make_conformant_kb(with_entries=False)
        self.addCleanup(shutil.rmtree, kdir, True)
        shutil.rmtree(os.path.join(kdir, "learnings"))
        code, report, _ = _attach(kdir)
        self.assertEqual(code, 1)
        self.assertFalse(report["ok"])
        self.assertIn("learnings", report["missing_dirs"])
        self.assertIn("refused", report["summary"])

    def test_corrupted_index_refused(self):
        kdir = _make_conformant_kb(with_entries=False)
        self.addCleanup(shutil.rmtree, kdir, True)
        with open(os.path.join(kdir, "index.json"), "w") as f:
            f.write("{ this is not valid json :::")
        code, report, _ = _attach(kdir)
        self.assertEqual(code, 1)
        self.assertFalse(report["ok"])
        self.assertTrue(report["index_errors"])

    def test_parity_divergence_refused(self):
        # Hand-edit index.json so it no longer matches its frontmatter -> rebuild
        # would not be a no-op.
        kdir = _make_conformant_kb(with_entries=True)
        self.addCleanup(shutil.rmtree, kdir, True)
        ipath = os.path.join(kdir, "index.json")
        data = json.load(open(ipath))
        # Mutate a summary so committed != frontmatter-derived.
        data["entries"][0]["summary"] = "TAMPERED summary not in frontmatter"
        json.dump(data, open(ipath, "w"), indent=2)
        code, report, _ = _attach(kdir)
        self.assertEqual(code, 1)
        self.assertFalse(report["ok"])
        self.assertTrue(report["parity_errors"])

    def test_attachmigrate_makes_missing_dirs_conformant(self):
        kdir = _make_conformant_kb(with_entries=False)
        self.addCleanup(shutil.rmtree, kdir, True)
        shutil.rmtree(os.path.join(kdir, "learnings"))
        # Refused before migration.
        code, report, _ = _attach(kdir)
        self.assertEqual(code, 1)
        # Migration recreates structure -> conformant.
        code, report, _ = _attach(kdir, migrate=True)
        self.assertEqual(code, 0, report)
        self.assertTrue(report["ok"])
        self.assertIn("learnings", report.get("migrated_dirs", []))

    def test_attach_is_read_only_without_migrate(self):
        kdir = _make_conformant_kb(with_entries=True)
        self.addCleanup(shutil.rmtree, kdir, True)
        before = json.load(open(os.path.join(kdir, "index.json")))
        _attach(kdir)  # no --migrate
        after = json.load(open(os.path.join(kdir, "index.json")))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
