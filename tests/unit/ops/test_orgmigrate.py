"""Migration 002 — the additive team-mode scaffold (feature 260813, task 63).

The step has to be two contradictory things at once, and each half is asserted
against the OTHER half's failure mode:

  * on a FLAT install it must be a true no-op — proved by a whole-tree sha256
    manifest taken before and after, not by reading the code and agreeing with it;
  * in ORG mode it must create the shared/team layout and stamp the optional org
    frontmatter fields — additively, without moving an entry, overwriting a
    declared value, or papering over a cross-team declaration that org lint O13
    exists to report.

Idempotency is likewise asserted as a BYTE claim (a second run changes no file),
because "makedirs(exist_ok=True)" is idempotent for directories while the
frontmatter writer beside it is where a second run could genuinely double-stamp.

Stdlib only, hermetic (every KB is a tempdir; the live knowledge base is never
touched), deterministic.

Runner: python3 -m unittest tests.unit.ops.test_orgmigrate -v
"""

import hashlib
import json
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


class RegistrationTest(OrgMigrateTestCase):
    """The step is REGISTERED — not merely importable."""

    def test_the_step_is_in_the_migration_table_as_additive_version_two(self):
        rows = [(v, tier, name) for (v, tier, name, _fn) in self.mig._KB_MIGRATIONS]
        self.assertIn((2, "additive", "team-mode-scaffold"), rows)

    def test_the_registered_callable_is_the_team_mode_body(self):
        fn = [f for (v, _t, _n, f) in self.mig._KB_MIGRATIONS if v == 2][0]
        self.assertIs(fn, self.mig._migration_002_team_mode_scaffold)

    def test_the_package_schema_version_is_the_table_max(self):
        self.assertEqual(self.mig.KB_SCHEMA_VERSION,
                         max(v for (v, _t, _n, _f) in self.mig._KB_MIGRATIONS))
        self.assertGreaterEqual(self.mig.KB_SCHEMA_VERSION, 2)

    def test_migrate_list_reports_the_step_as_pending_on_an_older_kb(self):
        kdir = tempfile.mkdtemp(prefix="agentware-kb-")
        self.addCleanup(shutil.rmtree, kdir, True)
        build_synthetic_kb(kdir)
        with open(os.path.join(kdir, ".schema"), "w", encoding="utf-8") as handle:
            handle.write("1\n")
        code, out, err = self.run_cli(["migrate", "--list", "--format", "json"], kdir)
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual([p["name"] for p in payload["pending"]],
                         ["team-mode-scaffold"])
        self.assertEqual(payload["target"], self.mig.KB_SCHEMA_VERSION)

    def test_applying_through_the_runner_actually_executes_the_body(self):
        """The table row is only half the claim — the registered callable has to
        RUN when `migrate --apply` does, or the step is listed and inert."""
        self.org_mode()
        tdir = self.seed_team()
        code, _out, err = self.run_cli(["migrate", "--apply"], tdir)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isdir(os.path.join(self.org, "shared")))
        with open(os.path.join(tdir, "learnings", "plain.md"),
                  "r", encoding="utf-8") as handle:
            self.assertIn("\nteam: alpha\n", handle.read())

    def test_the_body_is_reachable_without_migratecmd_importing_it_eagerly(self):
        """The lazy import is load-bearing: features.migratecmd is on the CLI's
        hot import path, so a module-level edge to ops.orgmigrate would drag
        ops.team + ops.fleet + knowledge.index into every `--help`."""
        import ast
        src = os.path.join(os.path.dirname(os.path.dirname(self.om.__file__)),
                           "features", "migratecmd.py")
        with open(src, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        top_level_imports = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level_imports.update(a.name for a in node.names)
        self.assertNotIn(ORGMIGRATE_MOD, top_level_imports)


class FlatInstallIsUntouchedTest(OrgMigrateTestCase):
    """INV-1: with no org root, the step must not read or write anything."""

    def setUp(self):
        super(FlatInstallIsUntouchedTest, self).setUp()
        self.kdir = tempfile.mkdtemp(prefix="agentware-flat-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)

    def test_a_flat_knowledge_dir_reports_a_declared_no_op(self):
        summary = self.om.run_team_mode_scaffold(self.kdir)
        self.assertFalse(summary["engaged"])
        self.assertIn("flat install", summary["reason"])
        self.assertEqual(summary["created"], [])
        self.assertEqual(summary["stamped"], [])

    def test_applying_the_migration_changes_no_file_but_the_schema(self):
        before = tree_manifest(self.kdir, skip_rels=(".schema",))
        code, _out, err = self.run_cli(["migrate", "--apply"], self.kdir)
        self.assertEqual(code, 0, err)
        after = tree_manifest(self.kdir, skip_rels=(".schema",))
        # logs/migrations.jsonl is the runner's audit journal, not this step's.
        for rel in list(after):
            if rel.startswith("logs/"):
                after.pop(rel)
        self.assertEqual(before, after)

    def test_the_entry_count_is_identical_before_and_after(self):
        path = os.path.join(self.kdir, "index.json")
        with open(path, "r", encoding="utf-8") as handle:
            before = len(json.load(handle)["entries"])
        self.om.run_team_mode_scaffold(self.kdir)
        with open(path, "r", encoding="utf-8") as handle:
            after = len(json.load(handle)["entries"])
        self.assertEqual(before, after)

    def test_a_flat_path_merely_CONTAINING_shared_is_not_the_org_shared_root(self):
        """The F3 failure class: a flat KB at …/shared-notes must stay flat."""
        kdir = os.path.join(self.org, "shared-notes")
        os.makedirs(kdir)
        summary = self.om.run_team_mode_scaffold(kdir)
        self.assertFalse(summary["engaged"])

    def test_the_direct_knowledge_dir_pointer_still_wins_over_the_org_keys(self):
        """Byte-identical flat resolution is what makes this migration additive:
        enabling team mode must not relocate an operator's knowledge dir."""
        self.org_mode()
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(os.environ.pop, "AGENTWARE_KNOWLEDGE_DIR", None)
        self.assertEqual(self.config.resolve_knowledge_dir(), self.kdir)


class OrgScaffoldTest(OrgMigrateTestCase):
    """Org mode: the directories appear, exactly once."""

    def setUp(self):
        super(OrgScaffoldTest, self).setUp()
        self.seed_team()
        self.org_mode()

    def test_it_creates_the_shared_and_per_team_directories(self):
        summary = self.om.run_team_mode_scaffold(self.team_dir())
        self.assertTrue(summary["engaged"])
        self.assertTrue(os.path.isdir(os.path.join(self.org, "shared")))
        self.assertTrue(os.path.isdir(os.path.join(self.org, "shared", "learnings")))
        self.assertTrue(os.path.isdir(os.path.join(self.team_dir(), "fleet")))
        self.assertIn("shared", summary["created"])

    def test_the_caller_team_folder_is_scaffolded_when_it_does_not_exist_yet(self):
        self.org_mode(team="brandnew")
        summary = self.om.run_team_mode_scaffold(self.team_dir("brandnew"))
        self.assertIn("teams/brandnew", summary["created"])
        self.assertTrue(os.path.isdir(os.path.join(self.team_dir("brandnew"),
                                                   "learnings")))

    def test_a_team_shaped_path_engages_even_with_the_org_keys_unset(self):
        """Engagement is the same question `_entry_origin_team` already answers
        for one entry: a kdir that IS `…/teams/<team>` carries the facet whether
        or not anyone exported AGENTWARE_ORG_ROOT, and this tree's own
        frontmatter builder already stamps new entries there on that basis."""
        os.environ["AGENTWARE_ORG_ROOT"] = ""
        os.environ["AGENTWARE_TEAM"] = ""
        summary = self.om.run_team_mode_scaffold(self.team_dir())
        self.assertTrue(summary["engaged"], summary["reason"])
        self.assertEqual(summary["org_root"], self.org)

    def test_it_never_invents_a_team_nobody_asked_for(self):
        self.om.run_team_mode_scaffold(self.team_dir())
        self.assertEqual(sorted(os.listdir(os.path.join(self.org, "teams"))),
                         ["alpha"])

    def test_a_second_run_creates_nothing_and_changes_no_byte(self):
        self.om.run_team_mode_scaffold(self.team_dir())
        before = tree_manifest(self.org)
        summary = self.om.run_team_mode_scaffold(self.team_dir())
        self.assertEqual(summary["created"], [])
        self.assertEqual(summary["stamped"], [])
        self.assertEqual(tree_manifest(self.org), before)

    def test_the_shared_stub_trips_no_read_time_content_guard(self):
        """O8 scans every shared *.md for injection markers — including the one
        file this migration itself seeds there."""
        orgkeys = load_lib("agentware_lib.core.orgkeys")
        self.om.run_team_mode_scaffold(self.team_dir())
        with open(os.path.join(self.org, "shared", "MAIN.md"),
                  "r", encoding="utf-8") as handle:
            body = handle.read().lower()
        for marker in orgkeys.INJECTION_MARKERS:
            self.assertNotIn(marker, body, marker)


class AnnouncementTest(OrgMigrateTestCase):
    """One line when it changed something; SILENCE otherwise.

    The silence half is written first and deliberately: the auto-apply runs this
    unattended on the first write after an upgrade, and a step that narrates a
    no-op on every invocation trains an operator to stop reading stderr — which
    is exactly the channel the loud case needs.
    """

    def capture(self, kdir):
        import io
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.om.run_team_mode_scaffold(kdir)
        return err.getvalue()

    def test_a_flat_install_says_nothing_at_all(self):
        kdir = tempfile.mkdtemp(prefix="agentware-flat-")
        self.addCleanup(shutil.rmtree, kdir, True)
        build_synthetic_kb(kdir)
        self.assertEqual(self.capture(kdir), "")

    def test_an_already_migrated_org_tree_says_nothing_on_the_second_run(self):
        self.seed_team()
        self.org_mode()
        self.capture(self.team_dir())
        self.assertEqual(self.capture(self.team_dir()), "")

    def test_a_STANDING_conflict_alone_never_re_announces(self):
        """Found by running: the first cut put `conflicts` in the changed-
        something predicate, so a tree holding one cross-team entry — a
        condition this step deliberately never resolves — announced itself on
        every single run, forever. A recurring condition is not an event."""
        self.seed_team()
        self.org_mode()
        self.capture(self.team_dir())
        for _ in range(3):
            self.assertEqual(self.capture(self.team_dir()), "")

    def test_the_first_org_run_announces_what_it_changed(self):
        self.seed_team()
        self.org_mode()
        line = self.capture(self.team_dir())
        self.assertIn("migration-002", line)
        self.assertIn("stamped 1", line)
        self.assertIn("1 cross-team conflict", line)


class FrontmatterBackfillTest(OrgMigrateTestCase):
    """The optional org fields land — additively, and only where derivable."""

    def setUp(self):
        super(FrontmatterBackfillTest, self).setUp()
        self.tdir = self.seed_team()
        self.org_mode()

    def read(self, rel):
        with open(os.path.join(self.tdir, rel), "r", encoding="utf-8") as handle:
            return handle.read()

    def test_a_bare_entry_gains_origin_and_team(self):
        summary = self.om.run_team_mode_scaffold(self.tdir)
        self.assertIn("teams/alpha/learnings/plain.md", summary["stamped"])
        text = self.read("learnings/plain.md")
        self.assertIn("\norigin: team\n", text)
        self.assertIn("\nteam: alpha\n", text)

    def test_the_stamped_fields_land_inside_the_block_in_canonical_order(self):
        """Same order render_frontmatter emits, so a stamped entry and one born
        with the fields are indistinguishable to any later reader or differ."""
        self.om.run_team_mode_scaffold(self.tdir)
        lines = self.read("learnings/plain.md").split("\n")
        close = lines.index("---", 1)
        self.assertEqual(lines[close - 2:close], ["origin: team", "team: alpha"])

    def test_every_pre_existing_byte_survives_the_stamp(self):
        before = self.read("learnings/plain.md")
        self.om.run_team_mode_scaffold(self.tdir)
        after = self.read("learnings/plain.md")
        added = [ln for ln in after.split("\n") if ln not in before.split("\n")]
        self.assertEqual(added, ["origin: team", "team: alpha"])
        self.assertTrue(after.endswith("\nbody text\n"))

    def test_an_entry_that_already_declares_the_fields_is_left_alone(self):
        before = self.read("learnings/already.md")
        summary = self.om.run_team_mode_scaffold(self.tdir)
        self.assertEqual(self.read("learnings/already.md"), before)
        self.assertIn("teams/alpha/learnings/already.md", summary["already"])

    def test_a_foreign_team_declaration_is_reported_and_never_rewritten(self):
        """O13 contamination is EVIDENCE. Rewriting it would make the lint pass
        by destroying the only record that a cross-team write happened."""
        before = self.read("learnings/foreign.md")
        summary = self.om.run_team_mode_scaffold(self.tdir)
        self.assertEqual(self.read("learnings/foreign.md"), before)
        self.assertIn("teams/alpha/learnings/foreign.md", summary["conflicts"])

    def test_a_legacy_entry_with_no_frontmatter_block_is_untouched(self):
        before = self.read("learnings/legacy.md")
        summary = self.om.run_team_mode_scaffold(self.tdir)
        self.assertEqual(self.read("learnings/legacy.md"), before)
        self.assertIn("teams/alpha/learnings/legacy.md",
                      summary["skipped_no_frontmatter"])

    def test_no_entry_file_is_added_moved_or_removed(self):
        index = load_lib("agentware_lib.knowledge.index")
        before = index.scan_entry_files(self.tdir)
        self.om.run_team_mode_scaffold(self.tdir)
        self.assertEqual(index.scan_entry_files(self.tdir), before)

    def test_entries_at_the_org_root_are_not_stamped_with_a_team(self):
        """A corpus co-resident with teams/ belongs to no team; stamping one
        would assert a provenance the layout cannot support."""
        write_entry(os.path.join(self.org, "learnings", "rootlevel.md"), STD_FIELDS)
        before = tree_manifest(os.path.join(self.org, "learnings"))
        self.om.run_team_mode_scaffold(self.tdir)
        self.assertEqual(tree_manifest(os.path.join(self.org, "learnings")), before)

    def test_a_shared_entry_gains_origin_shared_and_no_team(self):
        self.om.run_team_mode_scaffold(self.tdir)
        write_entry(os.path.join(self.org, "shared", "learnings", "s.md"), STD_FIELDS)
        summary = self.om.run_team_mode_scaffold(self.tdir)
        self.assertIn("shared/learnings/s.md", summary["stamped"])
        with open(os.path.join(self.org, "shared", "learnings", "s.md"),
                  "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("\norigin: shared\n", text)
        self.assertNotIn("\nteam:", text)

    def test_the_stamp_does_not_change_the_text_the_scorer_reads(self):
        """The org keys are excluded from _entry_doc_text (INV-1 / R-ORG-08), so
        a backfilled corpus must rank byte-identically to the one before it."""
        corpus = load_lib("agentware_lib.retrieval.corpus")
        entry = {"id": "learn-x", "path": "learnings/plain.md", "title": "X",
                 "summary": "s", "tags": ["a", "b"]}
        before = corpus._entry_doc_text(self.tdir, entry)
        self.om.run_team_mode_scaffold(self.tdir)
        self.assertEqual(corpus._entry_doc_text(self.tdir, entry), before)


class LineInsertTest(OrgMigrateTestCase):
    """The pure insert, pinned by example — one quoting rule, not two."""

    def test_a_file_without_a_block_returns_none(self):
        self.assertIsNone(self.om._insert_org_lines("# no block\n",
                                                    [("origin", "team")]))

    def test_an_unterminated_block_returns_none(self):
        self.assertIsNone(self.om._insert_org_lines("---\nid: x\n",
                                                    [("origin", "team")]))

    def test_a_value_needing_quotes_is_emitted_the_way_render_frontmatter_would(self):
        frontmatter = load_lib("agentware_lib.core.frontmatter")
        weird = "team: with colon"
        out = self.om._insert_org_lines("---\nid: x\n---\nbody\n",
                                        [("team", weird)])
        rendered = frontmatter.render_frontmatter({"id": "x", "team": weird})
        self.assertIn([ln for ln in rendered.split("\n")
                       if ln.startswith("team:")][0], out)

    def test_the_body_is_never_touched_even_when_it_holds_a_horizontal_rule(self):
        text = "---\nid: x\n---\n\nintro\n\n---\n\ntail\n"
        out = self.om._insert_org_lines(text, [("origin", "team")])
        self.assertTrue(out.endswith("\n\nintro\n\n---\n\ntail\n"))


class MispointedOrgRootTest(OrgMigrateTestCase):
    """A resolved-but-wrong org root is a refusal, never a materialisation."""

    def test_an_org_root_that_does_not_exist_creates_nothing(self):
        missing = os.path.join(self.org, "not-here")
        self.org_mode(root=missing)
        summary = self.om.run_team_mode_scaffold(missing)
        self.assertFalse(summary["engaged"])
        self.assertIn("does not exist", summary["reason"])
        self.assertFalse(os.path.exists(missing))

    def test_an_org_root_failing_the_project_identity_guard_is_refused(self):
        self.org_mode()
        os.environ["AGENTWARE_ORG_PROJECT"] = "some-other-product"
        summary = self.om.run_team_mode_scaffold(self.team_dir())
        self.assertFalse(summary["engaged"])
        self.assertIn("some-other-product", summary["reason"])
        self.assertFalse(os.path.isdir(os.path.join(self.org, "shared")))


if __name__ == "__main__":
    unittest.main()
