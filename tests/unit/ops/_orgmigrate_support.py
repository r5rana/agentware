"""Shared header for the `test_orgmigrate` suite — migration 002's tests.

Split out at task 98, when the suite crossed the 600-line corpus cap: task 63's
behaviour tests stay in `test_orgmigrate.py` and task 98's CALLER-SCOPING tests
live in `test_orgmigrate_scope.py`. Every import, helper, constant and base class
the two parts share lives here, so neither part duplicates a definition and
neither can drift from the other.

No test class moved in that split — the parts were ADDED beside the original
file, whose classes and node ids are untouched, so the preserved-surface freeze
sees no relocation to record.

Stdlib only, hermetic (every KB is a tempdir; the live knowledge base is never
touched), deterministic.
"""

import hashlib
import os
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from tests._fixtures import (build_synthetic_kb, load_lib,
                                 run_cli as _raw_run_cli)
except ImportError:  # allow `python3 -m unittest tests.unit.ops.test_orgmigrate`
    from _fixtures import (build_synthetic_kb, load_lib,
                           run_cli as _raw_run_cli)

ORGMIGRATE_MOD = "agentware_lib.ops.orgmigrate"
MIGRATE_MOD = "agentware_lib.features.migratecmd"


def tree_manifest(root, skip_rels=()):
    """{relative path: sha256} for every file under `root`, minus `skip_rels`.

    The evidence a no-op claim actually rests on: a content fingerprint per file,
    so a rewritten byte anywhere in the corpus reddens the assertion even when the
    file count, the entry count and the index all stay put.
    """
    out = {}
    skip = set(skip_rels)
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = sorted(dirnames)
        for fn in sorted(files):
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel in skip:
                continue
            with open(path, "rb") as handle:
                out[rel] = hashlib.sha256(handle.read()).hexdigest()
    return out


def write_entry(path, fields, body="\nbody text\n"):
    """Write one frontmatter-carrying entry file; `fields` is a list of (k, v)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["---"] + ["%s: %s" % (k, v) for k, v in fields] + ["---"]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n" + body)


STD_FIELDS = [("id", "learn-x"), ("title", "X"), ("category", "learnings"),
              ("tags", "[a, b]"), ("created", "2026-08-01"),
              ("author", "casey"), ("source", "auto"),
              ("last_verified", "2026-08-01")]


class OrgMigrateTestCase(unittest.TestCase):
    """A tempdir org root + the module under test, with no env leakage."""

    def setUp(self):
        self.om = load_lib(ORGMIGRATE_MOD)
        self.mig = load_lib(MIGRATE_MOD)
        self.config = load_lib("agentware_lib.core.config")
        self.org = tempfile.mkdtemp(prefix="agentware-org-")
        self.addCleanup(shutil.rmtree, self.org, True)
        # Every org key explicitly cleared, so a test that means "flat" cannot be
        # silently rescued by the developer's own config/env.
        patcher = mock.patch.dict(os.environ, {"AGENTWARE_ORG_ROOT": "",
                                               "AGENTWARE_TEAM": "",
                                               "AGENTWARE_ORG_PROJECT": "",
                                               "AGENTWARE_ORG_PACKAGE": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def org_mode(self, team="alpha", root=None):
        """Point the org keys at this test's tree (env wins over config.env)."""
        os.environ["AGENTWARE_ORG_ROOT"] = root if root is not None else self.org
        os.environ["AGENTWARE_TEAM"] = team

    def admin_mode(self, root=None):
        """The ORG-ROOT position (task 98): an org root with NO team identity.

        This is where an org-wide migration belongs, and after task 98 it is the
        only position still licensed to walk every shard.
        """
        os.environ["AGENTWARE_ORG_ROOT"] = root if root is not None else self.org
        os.environ["AGENTWARE_TEAM"] = ""

    def shared_dir(self):
        return os.path.join(self.org, "shared")

    def team_dir(self, team="alpha"):
        return os.path.join(self.org, "teams", team)

    def seed_team(self, team="alpha"):
        """A team folder holding the four entry shapes the backfill must tell
        apart: bare, already-stamped, foreign-team, and legacy (no block)."""
        tdir = self.team_dir(team)
        write_entry(os.path.join(tdir, "learnings", "plain.md"), STD_FIELDS)
        write_entry(os.path.join(tdir, "learnings", "already.md"),
                    STD_FIELDS + [("origin", "team"), ("team", team)])
        write_entry(os.path.join(tdir, "learnings", "foreign.md"),
                    STD_FIELDS + [("origin", "team"), ("team", "beta")])
        os.makedirs(os.path.join(tdir, "decisions"), exist_ok=True)
        with open(os.path.join(tdir, "learnings", "legacy.md"), "w",
                  encoding="utf-8") as handle:
            handle.write("# Legacy\n\n> **Created**: 2026-08-01\n\nbody\n")
        return tdir

    def run_cli(self, argv, kdir):
        try:
            return _raw_run_cli(argv, kdir)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            return code, "", "SystemExit(%r)" % (exc.code,)
