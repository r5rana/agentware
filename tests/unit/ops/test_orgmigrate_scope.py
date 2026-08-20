"""Migration 002's CALLER SCOPING — whose shard a `migrate --apply` may touch.

Feature 260813-modular-core-adoption, task 98. The sibling `test_orgmigrate.py`
asserts WHAT the step does; this file asserts WHOSE corpus it may do it to, which
until task 98 nothing did.

THE FINDING THIS FILE CLOSES
----------------------------
Task 66's org-mode x local-writer cross product ran a local writer in org mode for
the first time on either side of the merge, and found that `migrate --apply`
issued by team A performed a read-modify-write on team B's entry files and seeded
the org-root `shared/` skeleton — both at rc 0, silently. A direct R-ORG-02
violation by an ordinary team-loop verb, and R-ORG-03 for the `shared/` half. NOT
corruption: the values written were the correct path-derived ones, which is why
the defect is authorship and locking rather than content, and why a test that only
checked the VALUES could never have seen it. It also multiplied — each team runs
the migration once, so with N teams every team's files were rewritten N times.

WHY THE ASSERTIONS ARE SHAPED THE WAY THEY ARE
----------------------------------------------
Containment is asserted as a whole-corpus sha256 manifest taken before and after,
not as "the summary did not mention that path": a report is a claim about a write,
and the write is the thing under test. Every containment claim is paired with a
NON-VACUITY assertion in the same run — the caller's OWN entry really was stamped
— because a migration that has stopped working entirely also touches nobody else's
files, and that is the failure this file would otherwise certify as a fix.

The org-root caller's full walk is asserted POSITIVELY for the same reason: the
narrowing must not be mistaken for the removal of a capability that org-wide
migration legitimately needs. It moved to the position that owns it.

Stdlib only, hermetic (every KB is a tempdir; the live knowledge base is never
touched), deterministic.

Runner: python3 -m unittest tests.unit.ops.test_orgmigrate_scope -v
"""

import os
import unittest

from tests.unit.ops._orgmigrate_support import (  # noqa: F401
    STD_FIELDS, OrgMigrateTestCase, tree_manifest, write_entry)


class CallerFacetTest(OrgMigrateTestCase):
    """Task 98's resolver, pinned by position — the one thing every scoping
    claim in this file rests on, asserted directly so a regression names the RULE
    rather than showing up as a mysteriously untouched corpus.

    The asymmetry is deliberate and is the part worth pinning: the caller's
    POSITION (its kdir) decides, and AGENTWARE_TEAM is consulted only when the
    position carries no facet of its own. A curator working in `shared/` with a
    team exported is a shared caller, not a team one.
    """

    def facet(self, kdir):
        return self.om._caller_facet(self.org, kdir)

    def test_a_team_shaped_kdir_is_a_team_caller(self):
        self.assertEqual(self.facet(self.team_dir("alpha")),
                         (self.om.FACET_TEAM, "alpha"))

    def test_the_shared_root_is_a_shared_caller(self):
        self.assertEqual(self.facet(self.shared_dir()),
                         (self.om.FACET_SHARED, None))

    def test_the_org_root_itself_is_the_admin_caller(self):
        self.assertEqual(self.facet(self.org), (self.om.FACET_ORG, None))

    def test_the_kdir_position_outranks_an_exported_team_name(self):
        """A curator in `shared/` who also belongs to a team is still a curator:
        honouring the env var here would send `shared/`'s migration into that
        team's shard, which is the R-ORG-02 write this task removes."""
        os.environ["AGENTWARE_TEAM"] = "alpha"
        self.assertEqual(self.facet(self.shared_dir()),
                         (self.om.FACET_SHARED, None))
        self.assertEqual(self.facet(self.org), (self.om.FACET_ORG, None))

    def test_a_positionless_kdir_falls_back_to_the_declared_team(self):
        """A kdir that is neither a shard, the shared root, nor the org root
        carries no facet — so the narrowest thing left to honour is whatever team
        the caller declared, never the org-wide walk."""
        os.environ["AGENTWARE_TEAM"] = "alpha"
        self.assertEqual(self.facet(os.path.join(self.org, "elsewhere")),
                         (self.om.FACET_TEAM, "alpha"))

    def test_a_positionless_kdir_with_no_team_is_the_admin_caller(self):
        self.assertEqual(self.facet(os.path.join(self.org, "elsewhere")),
                         (self.om.FACET_ORG, None))

    def test_a_trailing_slash_does_not_change_the_position(self):
        """Path equality is normalised, so `…/shared/` and `…/shared` are one
        caller — a raw string compare would silently demote the first to admin."""
        self.assertEqual(self.facet(self.shared_dir() + os.sep),
                         (self.om.FACET_SHARED, None))
        self.assertEqual(self.facet(self.org + os.sep),
                         (self.om.FACET_ORG, None))


class TeamCallerScopeTest(OrgMigrateTestCase):
    """A TEAM caller acts inside `teams/<mine>/` and nowhere else.

    The two halves of task 66's finding get one test each — the directory half
    (R-ORG-03: seeding the curator-owned `shared/` root from a team loop) and the
    entry-file half (R-ORG-02: rewriting another team's entries) — because they
    fail independently and a single assertion over "outside" would let one hide
    behind the other.
    """

    def setUp(self):
        super(TeamCallerScopeTest, self).setUp()
        self.tdir = self.seed_team()
        self.org_mode()

    def test_a_team_caller_scaffolds_its_own_shard_and_never_the_shared_root(self):
        """The team half of the re-aimed claim above (task 98). R-ORG-03 reserves
        `shared/` for a curator; an ordinary team loop must not seed it."""
        summary = self.om.run_team_mode_scaffold(self.team_dir())
        self.assertEqual(summary["scope"], "team:alpha")
        self.assertTrue(os.path.isdir(os.path.join(self.team_dir(), "fleet")))
        self.assertFalse(os.path.isdir(self.shared_dir()))
        self.assertNotIn("shared", summary["created"])

    def test_a_team_caller_does_not_scaffold_another_teams_folder(self):
        """R-ORG-02 for the DIRECTORY half. `_known_teams` used to union every
        folder on disk from any position, so team alpha's migration ran the team
        scaffolder over team beta's shard as well."""
        beta = self.team_dir("beta")
        os.makedirs(beta)
        before = tree_manifest(beta)
        self.om.run_team_mode_scaffold(self.team_dir())
        self.assertEqual(tree_manifest(beta), before)
        self.assertFalse(os.path.isdir(os.path.join(beta, "learnings")))

    def test_a_team_caller_never_rewrites_another_teams_entry_file(self):
        """THE finding task 66 reported and task 98 fixes (R-ORG-02): team alpha's
        `migrate --apply` performed a read-modify-write on team beta's entry files
        at rc 0, with no lock beta's agent participates in."""
        theirs = os.path.join(self.team_dir("beta"), "learnings", "theirs.md")
        write_entry(theirs, STD_FIELDS)
        before = tree_manifest(os.path.dirname(theirs))
        summary = self.om.run_team_mode_scaffold(self.tdir)
        self.assertEqual(summary["scope"], "team:alpha")
        self.assertEqual(tree_manifest(os.path.dirname(theirs)), before)
        self.assertEqual([p for p in summary["stamped"]
                          if p.startswith("teams/beta/")], [])
        # Non-vacuity: the SAME run did stamp the caller's own bare entry, so the
        # containment above is isolation and not an inert migration.
        self.assertIn("teams/alpha/learnings/plain.md", summary["stamped"])


class SharedAndOrgCallerScopeTest(OrgMigrateTestCase):
    """The other two positions: a curator in `shared/`, and the org-root admin.

    The shared caller is the symmetric case — a curator has no more licence over a
    team's entry files than the reverse — and the org-root caller is the escape
    hatch, kept OPEN and asserted, because an org-wide migration is a real need
    that task 98 relocates rather than removes.
    """

    def setUp(self):
        super(SharedAndOrgCallerScopeTest, self).setUp()
        self.tdir = self.seed_team()
        self.org_mode()

    def test_a_shared_root_caller_does_not_reach_into_any_team_shard(self):
        """The other side of the re-aim (task 98): a curator working in `shared/`
        has no more licence over a team's entry files than the reverse."""
        self.admin_mode()
        self.om.run_team_mode_scaffold(self.org)   # seed the layout
        write_entry(os.path.join(self.tdir, "learnings", "fresh.md"), STD_FIELDS)
        before = tree_manifest(self.tdir)
        summary = self.om.run_team_mode_scaffold(os.path.join(self.org, "shared"))
        self.assertEqual(summary["scope"], "shared")
        self.assertEqual(tree_manifest(self.tdir), before)

    def test_the_org_root_caller_still_walks_every_shard(self):
        """The escape hatch stays open where it belongs — an org-wide migration
        issued from the org root still reaches every team."""
        theirs = os.path.join(self.team_dir("beta"), "learnings", "theirs.md")
        write_entry(theirs, STD_FIELDS)
        self.admin_mode()
        summary = self.om.run_team_mode_scaffold(self.org)
        self.assertEqual(summary["scope"], "org")
        self.assertIn("teams/beta/learnings/theirs.md", summary["stamped"])
        self.assertIn("teams/alpha/learnings/plain.md", summary["stamped"])


if __name__ == "__main__":
    unittest.main()
