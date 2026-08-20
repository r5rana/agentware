"""ORG MODE x LOCAL WRITER — the cross product, through the real CLI. Task 66.

The seam this file covers is the one place the two merged architectures actually
meet, and it was uncovered on BOTH sides: the donor's org mode was tested against
the donor's three writers, and this tree's two dozen writers were tested on a
flat knowledge dir, because a flat knowledge dir was the only kind this tree had.
`tests/_org_writers.py` carries the full rationale and the probe runner;
`tests/_org_writer_rows.py` carries the roster.

Every assertion here goes through `scripts/agentware` as a SUBPROCESS. Task 65
is the reason: `cmd_learn` lost its curator gate in the merge while the unit test
that drove the gate FUNCTION stayed green, because a gate is only reachable if a
verb reaches it. Testing the rule and testing the verb are different questions
and only the second is the one an operator's run asks.

WHAT THIS PROVED WHEN IT WAS WRITTEN, AND WHAT CHANGED
------------------------------------------------------
Twenty-two of the twenty-three probed writers were clean. One was not:
`migrate --apply`, run by team A, performed a read-modify-write on team B's entry
files and seeded the org-root `shared/` skeleton. It was pinned by its own
characterization test below and deferred as plan task 98 — the containment layer
was what bounded it, and that was asserted here too: the staging allowlist refused
every one of those paths, so the leak was an uncommittable dirty tree rather than
a cross-team commit.

Task 98 landed the fix: migration 002 now resolves the CALLER'S FACET and acts
only within it. `migrate` is an ordinary roster row here now, covered by the same
whole-roster rules as the other twenty-two, and the class that pinned the leak
asserts containment outright instead. All twenty-three are clean.
"""

from tests._org_writer_rows import (
    NON_WRITERS, UNPROBED_WRITERS, WRITERS)
from tests._org_writers import (
    COMMIT, Ctx, LOCAL, OrgTree, local_only_surface_nodes)
from tests._paths import REPO_ROOT

import os
import shutil
import sys
import tempfile
import unittest

if os.path.join(REPO_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from agentware_lib.ops.kbstage import kb_git_stage_allowlist  # noqa: E402

TEAM_A, HANDLE_A = "platform", "alice"
TEAM_B, HANDLE_B = "research", "bob"
TEAMS = ((TEAM_A, HANDLE_A), (TEAM_B, HANDLE_B))

#: The acceptance floor the task states. Asserted so that gutting the roster
#: down to a token row or two cannot make this file pass by measuring less.
MIN_PROBED_WRITERS = 8

CROSS_TEAM_REASON = "cross-team"


class RosterCompletenessTest(unittest.TestCase):
    """The roster ENUMERATES the local-only surface — it is not a sample.

    Derived from the generated parity manifest rather than restated, so a verb
    added to this tree later cannot join an unenumerated set: it lands in the
    surface fixture, fails here, and someone has to classify it as a writer or
    say in writing why it is not one.
    """

    def setUp(self):
        self.writers = {w.node for w in WRITERS}
        self.roster = self.writers | set(UNPROBED_WRITERS) | set(NON_WRITERS)

    def test_the_roster_covers_the_local_only_surface_exactly(self):
        surface = local_only_surface_nodes()
        self.assertEqual(sorted(surface - self.roster), [],
                         "local-only verbs missing from the writer roster")
        self.assertEqual(sorted(self.roster - surface), [],
                         "roster names verbs that are not local-only any more")

    def test_no_verb_is_classified_twice(self):
        """A node in two buckets means one of them is not being enforced."""
        buckets = (self.writers, set(UNPROBED_WRITERS), set(NON_WRITERS))
        for i, left in enumerate(buckets):
            for right in buckets[i + 1:]:
                self.assertEqual(sorted(left & right), [])
        self.assertEqual(len(self.roster),
                         sum(len(b) for b in buckets))

    def test_enough_writers_are_actually_probed(self):
        self.assertGreaterEqual(len(WRITERS), MIN_PROBED_WRITERS)

    def test_every_row_that_is_not_probed_says_why(self):
        """The escape hatches have to be arguments, not empty strings — an
        unexplained exemption is indistinguishable from an oversight."""
        for node, reason in list(UNPROBED_WRITERS.items()) + list(
                NON_WRITERS.items()):
            self.assertTrue(len(reason) > 20, "%s: thin reason %r" % (node, reason))

    def test_every_probed_writer_expects_a_concrete_write(self):
        """A row with no expected path is a probe that proves nothing: the verb
        could write nowhere at all and the containment assertion would hold
        vacuously."""
        for writer in WRITERS:
            self.assertTrue(writer.writes, "%s declares no writes" % writer.node)
            for rel in writer.writes:
                self.assertFalse(rel.startswith("/"), "%s: %s" % (writer.node, rel))


class _OrgProbeCase(unittest.TestCase):
    """One scratch two-team org repo per test."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-org-writers-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.tree = OrgTree(self.root, TEAMS)

    def ctx(self, team, handle):
        return Ctx(self.tree, team, handle)

    def outside(self, touched, team):
        prefix = "teams/%s/" % team
        return [p for p in touched
                if p != prefix.rstrip("/") and not p.startswith(prefix)]


class WritesLandInTheCallersShardTest(_OrgProbeCase):
    """Criterion 1: aimed by RESOLUTION, lands in that shard, and nowhere else.

    A fresh org tree per writer, so one row's writes can never be mistaken for
    another's, and the team is resolved from `AGENTWARE_ORG_ROOT` +
    `AGENTWARE_TEAM` with the direct knowledge-dir pointer REMOVED — a probe that
    passed an explicit kdir would be asserting that a write aimed by hand lands
    where it was aimed, which is a tautology.

    "Nowhere else" is a whole-org-tree sha256 manifest diff, not a list of paths
    someone thought to check. An assertion that only looks where it expects
    cannot see the write it did not expect, and that is the exact shape of the
    leak this file found in `migrate` (fixed at task 98; `migrate` is an ordinary
    row in this loop now).
    """

    def test_each_writer_writes_only_inside_its_own_team_shard(self):
        from tests._org_writers import probe
        probed = 0
        for writer in WRITERS:
            with self.subTest(writer=writer.node):
                tree = OrgTree(tempfile.mkdtemp(prefix="aw-w-"), TEAMS)
                self.addCleanup(shutil.rmtree, tree.root, True)
                rc, out, err, touched = probe(writer, Ctx(tree, TEAM_A, HANDLE_A))
                self.assertEqual(rc, 0, "%s: %s" % (writer.node, err or out))
                self.assertEqual(self.outside(touched, TEAM_A), [],
                                 "%s wrote outside teams/%s" % (writer.node, TEAM_A))
                for rel in writer.writes:
                    self.assertTrue(
                        any(p.endswith(rel) for p in touched),
                        "%s did not write %s (touched %s)"
                        % (writer.node, rel, touched))
                probed += 1
        self.assertGreaterEqual(probed, MIN_PROBED_WRITERS)

    def test_the_same_writer_aimed_at_the_other_team_lands_there_instead(self):
        """The positive control. Without it every assertion above is satisfied by
        a writer that refuses to write at all, or by an org tree in which team B
        is unreachable — neither of which is isolation."""
        from tests._org_writers import probe
        writer = [w for w in WRITERS if w.node == "prefer.capture"][0]
        for team, handle in TEAMS:
            with self.subTest(team=team):
                tree = OrgTree(tempfile.mkdtemp(prefix="aw-w2-"), TEAMS)
                self.addCleanup(shutil.rmtree, tree.root, True)
                rc, out, err, touched = probe(writer, Ctx(tree, team, handle))
                self.assertEqual(rc, 0, err or out)
                self.assertEqual(self.outside(touched, team), [])
                self.assertTrue(any(p.startswith("teams/%s/" % team)
                                    for p in touched), touched)


class CrossTeamRefusalTest(_OrgProbeCase):
    """Criterion 2: no writer's output can be committed into another team's shard.

    TWO mechanisms hold, and conflating them would make half the roster assert
    nothing. Which one applies is DECLARED per row (`Writer.commits`) rather than
    discovered at runtime, so a writer moving between the classes — knowledge
    that quietly stops being committed, machine-local log state that quietly
    starts being — is a failure rather than a silent reclassification.

      COMMIT  the output is a tracked knowledge/work artifact. The staging
              allowlist ALLOWS the caller's own path and REFUSES the other
              team's BY NAME with a cross-team reason. Both directions are
              asserted: a one-sided check also passes on an allowlist that
              refuses everything, which is not isolation but an outage.
      LOCAL   the output is machine-local state under `logs/` (or the derived
              `index.json`). Git never offers it, so it can be neither allowed
              nor refused, and asserting a cross-team refusal over it would be
              asserting nothing. What IS asserted: it never appears in either
              team's stage candidates, so it cannot become a cross-team commit
              by any path.
    """

    def _paths_from_both_teams(self, writer):
        from tests._org_writers import probe
        tree = OrgTree(tempfile.mkdtemp(prefix="aw-x-"), TEAMS)
        self.addCleanup(shutil.rmtree, tree.root, True)
        produced = {}
        for team, handle in TEAMS:
            rc, out, err, touched = probe(writer, Ctx(tree, team, handle))
            self.assertEqual(rc, 0, "%s/%s: %s" % (writer.node, team, err or out))
            produced[team] = [p for p in touched
                              if p.startswith("teams/%s/" % team)
                              and os.path.isfile(os.path.join(tree.org, p))]
        tree.stage_everything()
        return tree, produced

    def test_each_writers_output_is_refused_by_the_other_teams_stage_gate(self):
        for writer in WRITERS:
            with self.subTest(writer=writer.node):
                tree, produced = self._paths_from_both_teams(writer)
                if writer.commits == COMMIT:
                    self._assert_partitioned(tree, produced, writer.node)
                else:
                    self._assert_never_offered(tree, produced, writer.node)

    def _assert_partitioned(self, tree, produced, label):
        candidates = tree.candidates()
        for mine, theirs in ((TEAM_A, TEAM_B), (TEAM_B, TEAM_A)):
            allow, rejected = kb_git_stage_allowlist(
                tree.team_dir(mine), rel_prefix="teams/%s" % mine)
            rej = dict(rejected)
            own = [p for p in produced[mine] if p in candidates]
            self.assertTrue(own, "%s: %s produced no committable artifact — the "
                                 "row claims COMMIT" % (label, mine))
            for path in own:
                self.assertIn(path, allow, "%s: %s own path not stageable"
                              % (label, mine))
            for path in [p for p in produced[theirs] if p in candidates]:
                self.assertNotIn(path, allow, "%s: %s STAGED %s's path %s"
                                 % (label, mine, theirs, path))
                self.assertIn(path, rej, "%s: %s not refused by name" % (label, path))
                self.assertIn(CROSS_TEAM_REASON, rej[path])

    def _assert_never_offered(self, tree, produced, label):
        """A LOCAL row: git offers none of it, from either side."""
        candidates = tree.candidates()
        for team in (TEAM_A, TEAM_B):
            self.assertTrue(produced[team], "%s: %s produced nothing"
                            % (label, team))
            offered = [p for p in produced[team] if p in candidates]
            self.assertEqual(offered, [],
                             "%s: %s is declared LOCAL but git offers %s — it is "
                             "committable now and the row must say COMMIT"
                             % (label, team, offered))

    def test_the_refusal_names_the_reason_rather_than_dropping_silently(self):
        """A silently dropped path and a refused one produce the same commit.
        They are not the same INCIDENT: only the refusal leaves a record that a
        cross-team write was attempted."""
        writer = [w for w in WRITERS if w.node == "prefer.capture"][0]
        tree, produced = self._paths_from_both_teams(writer)
        _allow, rejected = kb_git_stage_allowlist(
            tree.team_dir(TEAM_A), rel_prefix="teams/%s" % TEAM_A)
        reasons = {why for path, why in rejected if path in produced[TEAM_B]}
        self.assertTrue(reasons, "team B's paths were dropped without a reason")
        for why in reasons:
            self.assertIn(CROSS_TEAM_REASON, why)
            self.assertIn("refused", why)

    def test_both_isolation_classes_are_actually_exercised(self):
        """Non-vacuity. If every row drifted into one class the other branch
        would stop running and nobody would see it from a green suite."""
        classes = {w.commits for w in WRITERS}
        self.assertIn(COMMIT, classes)
        self.assertIn(LOCAL, classes)


class MigrateStaysInsideTheCallersShardTest(_OrgProbeCase):
    """THE FINDING, CLOSED. `migrate --apply` was the one probed writer that wrote
    outside the caller's team folder; plan task 98 scoped it to the caller's own
    shard, and this class — which used to pin the leak as a two-directional
    ratchet — now asserts the containment outright.

    Two leaks were fixed and each keeps its own assertion, because they fail
    independently:

      * it seeded the org-root `shared/` skeleton, which R-ORG-03 reserves for a
        curator, from an ordinary team loop;
      * it performed a read-modify-write on ANOTHER TEAM'S entry files, at rc 0,
        with no lock the owning team's agent participates in. Each team runs the
        migration once, so with N teams every team's files were rewritten N times.

    `migrate` is an ordinary row in the roster now, so the whole-roster rules above
    already cover it. This class stays for what they cannot construct: the other
    team's UNSTAMPED entry. Without one, team B's shard holds nothing the backfill
    would have rewritten even before the fix, and every containment assertion over
    it passes vacuously — which is precisely why the leak went unseen until task 66
    built this setup.
    """

    def _run_migrate(self):
        from tests._org_writers import probe
        writer = [w for w in WRITERS if w.node == "migrate"][0]
        ctx = self.ctx(TEAM_A, HANDLE_A)
        # An UNSTAMPED entry in the other team's shard: without one the backfill
        # has nothing to rewrite there and the containment claim is vacuous.
        theirs = os.path.join(self.tree.team_dir(TEAM_B), "learnings", "theirs.md")
        os.makedirs(os.path.dirname(theirs), exist_ok=True)
        with open(theirs, "w", encoding="utf-8") as handle:
            handle.write("---\nid: learn-theirs\nsummary: s\n---\n\n# theirs\n")
        with open(theirs, encoding="utf-8") as handle:
            before = handle.read()
        rc, out, err, touched = probe(writer, ctx)
        self.assertEqual(rc, 0, err or out)
        return touched, theirs, before

    def test_it_writes_nothing_outside_the_callers_own_shard(self):
        """The claim the removed `KNOWN_OUTSIDE_SHARED` tuple used to enumerate.
        Whole-org-tree manifest diff, so a path nobody thought to name still
        shows up."""
        touched, _theirs, _before = self._run_migrate()
        self.assertEqual(sorted(self.outside(touched, TEAM_A)), [],
                         "migration 002 wrote outside teams/%s" % TEAM_A)

    def test_the_other_teams_unstamped_entry_is_left_byte_identical(self):
        """The serious half, at the byte level. Named against the shape of the
        original finding: the values it used to write were the CORRECT
        path-derived ones, so an assertion about content would have passed
        throughout — the defect was authorship and the lock, and only an
        untouched-file claim can see it."""
        _touched, theirs, before = self._run_migrate()
        with open(theirs, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), before)

    def test_the_org_root_shared_skeleton_is_not_seeded_by_a_team_loop(self):
        """R-ORG-03: `shared/` is curator-gated, and a team's migration is not a
        curator."""
        self._run_migrate()
        shared = os.path.join(self.tree.org, "shared")
        self.assertFalse(os.path.isdir(shared),
                         "a team loop seeded the curator-owned shared root")

    def test_the_caller_own_shard_is_still_actually_migrated(self):
        """NON-VACUITY. A migration that has stopped doing anything at all also
        writes outside nobody's shard, and would satisfy every assertion above.
        The caller's own `.schema` stamp is the proof it ran."""
        touched, _theirs, _before = self._run_migrate()
        mine = [p for p in touched if p.startswith("teams/%s/" % TEAM_A)]
        self.assertTrue(mine, "migrate touched nothing in its own shard")
        self.assertIn("teams/%s/.schema" % TEAM_A, touched)


if __name__ == "__main__":
    unittest.main()
