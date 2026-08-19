"""Cross-team staging ISOLATION — the behavioural probe.

Parity row: `org:cross-team-write-isolation` (task 50). No CLI surface, no
distinguishing node id, so the generated parity tiers cannot see it.

What is at stake
----------------
R-ORG-02: a caller writes to its OWN `teams/<team>/` and never into another
team's. The blast radius of getting this wrong is not a bad file — it is one
team's agent silently writing knowledge into another team's shard, where it will
be recalled as that team's own and acted on. Nobody reads the path in a commit
diff and notices.

The isolation has to hold at TWO layers, and this file probes both, because
either alone is insufficient:

  * RESOLUTION — the caller's team is inferred from the knowledge-dir path, so a
    write is aimed by WHERE the loop is pointed, not by a value it can pass.
  * REFUSAL — an attempted write whose resolved target is not the caller's own
    shard must be refused rather than silently redirected. Silent redirection is
    the worse failure: the write appears to succeed.

The mutation the parity row names — "drop the target-team assertion: a
cross-team write succeeds" — reddens
`test_a_write_aimed_at_another_teams_shard_is_refused`, which drives the REAL
`ops.orglint.lint_org` rather than re-deriving the rule: a probe that
reimplements the check it guards proves only that it agrees with itself, and the
mutation would leave it green.

Hermetic: a two-team scratch org tree in a tempdir, stdlib unittest only.
"""

from tests._paths import TESTS_DIR

import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

from agentware_lib.core.config import (  # noqa: E402
    _entry_origin_team,
    _infer_team_from_kdir,
)
from agentware_lib.core.orgkeys import ORG_ROOT_KEY  # noqa: E402


def _org_tree():
    root = tempfile.mkdtemp(prefix="aw-org-iso-")
    for rel in ("teams/alpha", "teams/beta", "shared"):
        os.makedirs(os.path.join(root, rel), exist_ok=True)
    return root


class CrossTeamResolutionTests(unittest.TestCase):
    """A caller's team comes from the PATH, so it cannot be spoofed by a value."""

    def setUp(self):
        self.root = _org_tree()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.alpha = os.path.join(self.root, "teams", "alpha")
        self.beta = os.path.join(self.root, "teams", "beta")

    def test_each_shard_resolves_to_its_own_team(self):
        self.assertEqual(_infer_team_from_kdir(self.alpha), "alpha")
        self.assertEqual(_infer_team_from_kdir(self.beta), "beta")
        self.assertNotEqual(_infer_team_from_kdir(self.alpha),
                            _infer_team_from_kdir(self.beta))

    def test_an_entry_written_in_a_shard_is_stamped_with_that_shard(self):
        """R-ORG-05: every org entry carries origin+team. On a LEGACY entry with
        no frontmatter the facets are INFERRED from the shard it lives in — which
        is what makes the stamp unspoofable by an entry that simply omits it."""
        origin, team = _entry_origin_team({}, self.alpha)
        self.assertEqual((origin, team), ("team", "alpha"))
        origin, team = _entry_origin_team({}, self.beta)
        self.assertEqual((origin, team), ("team", "beta"))

    def test_explicit_frontmatter_wins_over_inference(self):
        """The backfill migration writes real facets; once present they are
        authoritative, so a moved file keeps its provenance."""
        origin, team = _entry_origin_team(
            {"origin": "team", "team": "alpha"}, self.beta)
        self.assertEqual((origin, team), ("team", "alpha"))

    def test_a_flat_kb_gets_no_facets_at_all(self):
        """INV-1: a flat install must be byte-identical, which means NOT stamping
        it with invented org facets."""
        flat = tempfile.mkdtemp(prefix="aw-flat-iso-")
        self.addCleanup(shutil.rmtree, flat, ignore_errors=True)
        self.assertEqual(_entry_origin_team({}, flat), (None, None))

    def test_the_shared_root_resolves_to_shared_not_to_a_team(self):
        origin, team = _entry_origin_team({}, os.path.join(self.root, "shared"))
        self.assertEqual(origin, "shared")
        self.assertIsNone(team)


class CrossTeamWriteRefusalTests(unittest.TestCase):
    """The refusal itself, driven through the REAL linter.

    Deliberately NOT a reimplementation of the containment rule. A probe that
    re-derives the check it is meant to guard proves only that the probe agrees
    with itself: the parity row's mutation ("drop the target-team assertion")
    would leave such a probe green, which is exactly the failure the curated tier
    exists to prevent. So this drives `ops.orglint.lint_org` and asserts on the
    O13 finding it emits.
    """

    def setUp(self):
        self.root = _org_tree()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.alpha = os.path.join(self.root, "teams", "alpha")
        self.beta = os.path.join(self.root, "teams", "beta")
        self._saved = os.environ.get(ORG_ROOT_KEY)
        os.environ[ORG_ROOT_KEY] = self.root
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._saved is None:
            os.environ.pop(ORG_ROOT_KEY, None)
        else:
            os.environ[ORG_ROOT_KEY] = self._saved

    def _entry_file(self, shard, name, declared_team=None):
        """Write a real entry file into `shard`, optionally DECLARING a team."""
        d = os.path.join(shard, "learnings")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name + ".md")
        fm = ["---", "id: learn-%s" % name, "title: %s" % name,
              "category: learnings", "created: 2026-01-01",
              "last_verified: 2026-01-01", "tags: []", "origin: team"]
        if declared_team:
            fm.append("team: %s" % declared_team)
        fm += ["---", "", "body", ""]
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("\n".join(fm))
        return p

    def _o13(self):
        from agentware_lib.ops.orglint import lint_org
        rep = lint_org(self.alpha)
        return [e for e in (rep.get("errors") or []) if e.get("rule") == "O13"]

    def test_a_clean_two_team_tree_raises_no_cross_team_finding(self):
        """The negative control. Without it, an O13 that fires on EVERYTHING
        would pass the positive test below and prove nothing."""
        self._entry_file(self.alpha, "mine", declared_team="alpha")
        self._entry_file(self.beta, "theirs", declared_team="beta")
        self.assertEqual(self._o13(), [])

    def test_a_write_aimed_at_another_teams_shard_is_refused(self):
        """The row's named property: an entry sitting under teams/beta while
        DECLARING team:alpha is an agent that wrote outside its own shard, and
        the linter must say so by name."""
        self._entry_file(self.beta, "leaked", declared_team="alpha")
        found = self._o13()
        self.assertEqual(len(found), 1, "cross-team contamination not reported: %r"
                         % (self._o13(),))
        msg = found[0]["message"]
        self.assertIn("alpha", msg)
        self.assertIn("beta", msg)
        self.assertIn("cross-team", msg)

    def test_the_refusal_is_symmetric(self):
        """Isolation that only holds in one direction is not isolation."""
        self._entry_file(self.alpha, "leaked", declared_team="beta")
        found = self._o13()
        self.assertEqual(len(found), 1)
        self.assertIn("teams/alpha", found[0]["file"])

    def test_a_legacy_entry_with_no_declared_team_is_not_contamination(self):
        """O1's path-inference exists precisely so LEGACY entries need no
        rewrite. An entry with no `team:` line resolves to its shard and must
        NOT be reported as cross-team — otherwise every pre-org entry in the
        corpus reads as a violation the day org mode turns on."""
        self._entry_file(self.alpha, "legacy")          # no team: line
        self.assertEqual(self._o13(), [])

    def test_the_lint_is_a_true_noop_on_a_flat_install(self):
        """INV-1: with no org root configured the whole rule set must not
        engage, or a flat single-operator KB starts failing org invariants it
        has no way to satisfy."""
        from agentware_lib.ops.orglint import lint_org
        os.environ.pop(ORG_ROOT_KEY, None)
        rep = lint_org(self.alpha)
        self.assertTrue(rep.get("ok"))
        self.assertFalse(rep.get("engaged"))
        self.assertEqual(rep.get("errors"), [])


if __name__ == "__main__":
    unittest.main()
