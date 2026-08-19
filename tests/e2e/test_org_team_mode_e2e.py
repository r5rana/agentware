"""Org TEAM MODE, end to end through the real CLI — feature 260813, task 65.

The unit probes for these rules already exist and pass: `tests/unit/ops/
test_org_curator.py` drives `guard_shared_write` directly, `tests/unit/core/
test_org_isolation.py` drives `lint_org` directly. Both were green while the
`learn` verb wrote org-wide knowledge with no gate at all, because neither of
them goes through a COMMAND. The guard was a function nobody called: `cmd_learn`
lost its one `guard_shared_write` line in the merge (the donor's
`features/curationcmd.py` calls it at the top of BOTH handlers; this tree's kept
it in `cmd_decide` only), while `register_learn` still declared `--to-shared`.
So the flag parsed, the help text promised a CR-gate, the unit test proved the
gate worked, and a non-curator team loop wrote to `shared/` anyway.

That is the shape this file exists to catch, and it is why every assertion below
goes through `scripts/agentware` as a SUBPROCESS rather than through an imported
symbol. A gate is only reachable if a verb reaches it; testing the gate and
testing the verb are different questions, and only the second one is the one the
operator's runs ask.

WHAT IS COVERED (task 65's four acceptance criteria, in order)
  1. `team create` / `register` / `list` succeed — a real registry, a real
     scaffold, a real persisted pointer.
  2. A non-curator `shared/` write is REFUSED — for `learn` AND `decide`, in both
     failing states (no flag; flag without curator), with the curator path and
     the ordinary team path proven still to work so the gate is not just "refuse
     everything".
  3. A second teammate is scoped correctly and both fleet shards coexist —
     per-USER shards, in their own teams, neither overwriting the other.
  4. Both fleet reports return structured output over COMMITTED events.

Plus the two provenance obligations the criteria assume: work artifacts carry
their team stamp (R-ORG-05), and a write aimed at another team's folder is
refused by the staging allowlist (R-ORG-02).

HERMETIC: a scratch org tree + a scratch `AGENTWARE_CONFIG_HOME` per test, so
`team create`'s config persistence never reaches the operator's real
`~/.agentware/config.env`. Stdlib unittest + subprocess only.
"""

from tests._paths import REPO_ROOT

import json
import os
import shutil
import subprocess
import tempfile
import unittest

CLI = os.path.join(REPO_ROOT, "scripts", "agentware")

CURATOR = "carol"
TEAMMATE_A = "alice"
TEAMMATE_B = "bob"
TEAM_A = "platform"
TEAM_B = "research"

#: Outcomes from `LOOP_OUTCOME_ENUM`. Using real ones matters: org lint O12
#: rejects anything else, so a fixture built from invented outcome strings would
#: make the whole org tree lint-invalid for a reason unrelated to what is asserted.
OUTCOME_OK = "completed"
OUTCOME_FAIL = "hit_max_iterations"


class OrgTeamModeE2E(unittest.TestCase):
    """One scratch org repo per test, driven only through the real CLI."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-org-e2e-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.org = os.path.join(self.root, "org")
        self.cfg = os.path.join(self.root, "cfg")
        os.makedirs(self.org)
        os.makedirs(self.cfg)
        self._git("init", "-q", ".")
        self._git("config", "user.email", "e2e@local")
        self._git("config", "user.name", "e2e")

    # --- harness ------------------------------------------------------------

    def _git(self, *args):
        return subprocess.run(["git", "-C", self.org] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True)

    def cli(self, argv, handle=None, kdir=None):
        """Run the real CLI. Returns (rc, stdout, stderr).

        `AGENTWARE_CONFIG_HOME` is redirected to the scratch dir so a subprocess
        that PERSISTS config (`team create` writes AGENTWARE_TEAM) cannot reach
        the operator's real config — patching the module global in-process could
        not give a subprocess this, which is why the env key exists.
        """
        env = dict(os.environ)
        env["AGENTWARE_CONFIG_HOME"] = self.cfg
        env["AGENTWARE_ORG_ROOT"] = self.org
        env["AGENTWARE_DREAM"] = "0"
        env["AGENTWARE_NESTED_UNITTEST"] = "1"
        env.pop("AGENTWARE_TEAM", None)
        if handle:
            env["AGENTWARE_USER_HANDLE"] = handle
        else:
            env.pop("AGENTWARE_USER_HANDLE", None)
        if kdir:
            env["AGENTWARE_KNOWLEDGE_DIR"] = kdir
        else:
            env.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        p = subprocess.run(["python3", CLI] + argv, cwd=REPO_ROOT, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True)
        return p.returncode, p.stdout, p.stderr

    def team_dir(self, team):
        return os.path.join(self.org, "teams", team)

    def shared_dir(self):
        return os.path.join(self.org, "shared")

    def seed_shared_root(self, curators=(CURATOR,)):
        """A `shared/` root with an OWNERS.md naming `curators` and nobody else."""
        d = self.shared_dir()
        for sub in ("learnings", "decisions", "skills", "projects",
                    "references", "configurations", "prompts"):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
        with open(os.path.join(d, "OWNERS.md"), "w", encoding="utf-8") as f:
            f.write("# Shared curators\n\n| Handle | Role |\n|--------|------|\n")
            for c in curators:
                f.write("| %s | curator |\n" % c)
        return d

    def two_teams(self):
        """The task's simulated org: alice creates A, bob JOINS A then creates B."""
        rc, _o, err = self.cli(["team", "create", "--name", TEAM_A,
                                "--format", "json"], handle=TEAMMATE_A)
        self.assertEqual(rc, 0, err)
        rc, _o, err = self.cli(["team", "register", "--name", TEAM_A,
                                "--handle", TEAMMATE_B, "--no-set-default",
                                "--format", "json"], handle=TEAMMATE_B)
        self.assertEqual(rc, 0, err)
        rc, _o, err = self.cli(["team", "create", "--name", TEAM_B,
                                "--handle", TEAMMATE_B, "--no-set-default",
                                "--format", "json"], handle=TEAMMATE_B)
        self.assertEqual(rc, 0, err)

    def emit(self, team, handle, outcome, ts, feature="f"):
        rc, _o, err = self.cli(
            ["fleet-emit", "--outcome", outcome, "--feature", feature,
             "--ts", ts, "--format", "json"],
            handle=handle, kdir=self.team_dir(team))
        self.assertEqual(rc, 0, err)

    # --- criterion 1: team create / register / list -------------------------

    def test_team_create_register_and_list_succeed(self):
        """The onboarding trio, asserted on its EFFECTS rather than its exit code.

        `team create` returning 0 proves nothing on its own — the verb could
        print a receipt and scaffold nothing. So this checks the three things a
        second engineer's clone actually needs: the registry row, the canonical
        team folder, and the persisted pointer that makes the next command
        resolve there without an env var.
        """
        self.two_teams()

        rc, out, err = self.cli(["team", "list", "--format", "json"])
        self.assertEqual(rc, 0, err)
        reg = json.loads(out)
        self.assertEqual(sorted(t["name"] for t in reg["teams"]),
                         sorted([TEAM_A, TEAM_B]))
        self.assertIn({"user": TEAMMATE_A, "team": TEAM_A}, reg["members"])
        self.assertIn({"user": TEAMMATE_B, "team": TEAM_A}, reg["members"])
        self.assertIn({"user": TEAMMATE_B, "team": TEAM_B}, reg["members"])

        # The scaffold is real: the canonical subdirs + a seeded index exist.
        for team in (TEAM_A, TEAM_B):
            for sub in ("learnings", "decisions", "work", "fleet"):
                self.assertTrue(os.path.isdir(os.path.join(self.team_dir(team), sub)),
                                "missing %s/%s" % (team, sub))
            self.assertTrue(os.path.isfile(os.path.join(self.team_dir(team),
                                                        "index.json")))

        # `create` without --no-set-default persisted the pointer (alice/TEAM_A).
        with open(os.path.join(self.cfg, "config.env"), encoding="utf-8") as f:
            cfg = f.read()
        self.assertIn("AGENTWARE_TEAM=%s" % TEAM_A, cfg)

    def test_register_refuses_a_team_that_was_never_created(self):
        """JOIN is not CREATE. Without this, a typo'd `--name` silently scaffolds
        a second team nobody meant to exist and the registry grows a ghost row."""
        rc, _o, err = self.cli(["team", "register", "--name", "no-such-team",
                                "--handle", TEAMMATE_B, "--no-set-default"],
                               handle=TEAMMATE_B)
        self.assertEqual(rc, 1)
        self.assertIn("does not exist", err)
        self.assertFalse(os.path.isdir(self.team_dir("no-such-team")))

    # --- criterion 2: the curator gate on shared writes ---------------------

    def test_a_non_curator_shared_write_is_refused_for_learn_and_decide(self):
        """The regression this file was written for.

        Parameterised over BOTH curation verbs deliberately. The defect was that
        one of the two skipped the guard, so a test covering either verb alone
        had a 50% chance of being green on a broken tree — and the one that was
        covered (`decide`) was the one that still worked.

        Both refusal states are checked because they fail differently. Without
        the flag check every ordinary team write starts landing org-wide; without
        the curator check `--to-shared` becomes self-service and any teammate who
        reads `--help` can write the org corpus, which is the same as no gate
        while looking exactly like one.
        """
        shared = self.seed_shared_root()
        for verb, extra in (
            ("learn", ["--content", "c", "--tags", "t"]),
            ("decide", ["--options", "a|b", "--choice", "a",
                        "--rationale", "r", "--reversible", "trivial"]),
        ):
            with self.subTest(verb=verb):
                base = [verb, "--topic", "gate-%s" % verb, "--summary", "s"] + extra

                rc, _o, err = self.cli(base, handle=TEAMMATE_B, kdir=shared)
                self.assertEqual(rc, 1, "%s without --to-shared was ALLOWED" % verb)
                self.assertIn("shared/ write refused", err)
                self.assertIn("--to-shared", err)

                rc, _o, err = self.cli(base + ["--to-shared"],
                                       handle=TEAMMATE_B, kdir=shared)
                self.assertEqual(rc, 1, "%s --to-shared by a NON-curator was "
                                        "ALLOWED" % verb)
                self.assertIn("requires a curator", err)
                self.assertIn(TEAMMATE_B, err)

        # Nothing reached the corpus through either refusal.
        for cat in ("learnings", "decisions"):
            self.assertEqual(
                [f for f in os.listdir(os.path.join(shared, cat))
                 if f.endswith(".md") and f != "index.md"], [])

    def test_a_curator_may_write_shared_and_a_teammate_may_write_their_team(self):
        """The gate's POSITIVE half — without this the whole suite passes if the
        guard simply refuses everything, which is not a gate but an outage."""
        shared = self.seed_shared_root()
        self.two_teams()

        rc, _o, err = self.cli(
            ["learn", "--topic", "curated-entry", "--summary", "s",
             "--tags", "t", "--content", "c", "--to-shared"],
            handle=CURATOR, kdir=shared)
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.isfile(
            os.path.join(shared, "learnings", "curated-entry.md")))

        rc, _o, err = self.cli(
            ["learn", "--topic", "team-entry", "--summary", "s",
             "--tags", "t", "--content", "c"],
            handle=TEAMMATE_B, kdir=self.team_dir(TEAM_B))
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.isfile(
            os.path.join(self.team_dir(TEAM_B), "learnings", "team-entry.md")))

    def test_the_gate_is_inert_on_a_flat_single_operator_knowledge_dir(self):
        """INV-1 / R-ORG-04: a flat install never opted into org mode, has no
        `shared/` and no curators, so a gate that engaged there would refuse
        ordinary writes on a knowledge base that has done nothing wrong."""
        flat = os.path.join(self.root, "flat")
        for sub in ("learnings", "decisions", "skills", "projects",
                    "references", "configurations", "prompts"):
            os.makedirs(os.path.join(flat, sub))
        with open(os.path.join(flat, "index.json"), "w", encoding="utf-8") as f:
            json.dump({"entries": [], "tags": {}}, f)
        rc, _o, err = self.cli(
            ["learn", "--topic", "flat-entry", "--summary", "s",
             "--tags", "t", "--content", "c"],
            handle=TEAMMATE_B, kdir=flat)
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(flat, "learnings",
                                                    "flat-entry.md")))

    # --- work-artifact provenance (R-ORG-05) --------------------------------

    def test_work_artifacts_are_stamped_with_their_team(self):
        """A plan/worklog carries `team:` so a committed artifact says whose it
        is. The prose `> Team:` line alone is not indexer-readable, which is why
        `--frontmatter` exists and why both halves are asserted here."""
        self.two_teams()
        wdir = os.path.join(self.team_dir(TEAM_A), "work", "260819-demo")
        os.makedirs(wdir)
        for name in ("plan.md", "worklog.md"):
            with open(os.path.join(wdir, name), "w", encoding="utf-8") as f:
                f.write("# demo — %s\n\n> Created: 2026-08-19\n" % name)

        rc, out, err = self.cli(["work", "stamp", "--frontmatter", "--format", "json"],
                                handle=TEAMMATE_A, kdir=self.team_dir(TEAM_A))
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["team"], TEAM_A)

        for name in ("plan.md", "worklog.md"):
            with open(os.path.join(wdir, name), encoding="utf-8") as f:
                text = f.read()
            self.assertTrue(text.startswith("---\n"), name)
            self.assertIn("team: %s" % TEAM_A, text)
            self.assertIn("origin: team", text)
            self.assertIn("> Team: %s" % TEAM_A, text)
            self.assertIn("Author: %s" % TEAMMATE_A, text)

    # --- cross-team write isolation (R-ORG-02) ------------------------------

    def test_a_write_into_another_teams_folder_is_refused_by_the_staging_gate(self):
        """R-ORG-02 at the layer that decides what gets COMMITTED.

        Silent redirection would be the worse failure mode, so the assertion is
        two-sided: the caller's own path is allowed AND the other team's path is
        rejected by name. One team's knowledge landing in another team's shard is
        recalled there as that team's own, and nobody reads a commit diff and
        notices a path prefix.
        """
        self.two_teams()
        import sys
        if os.path.join(REPO_ROOT, "scripts") not in sys.path:
            sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
        from agentware_lib.ops.kbstage import kb_git_stage_allowlist

        mine = os.path.join(self.team_dir(TEAM_A), "learnings", "mine.md")
        theirs = os.path.join(self.team_dir(TEAM_B), "learnings", "theirs.md")
        for p in (mine, theirs):
            with open(p, "w", encoding="utf-8") as f:
                f.write("# x\n")
        self._git("add", "-A", "--", "teams")

        allow, rejected = kb_git_stage_allowlist(self.team_dir(TEAM_A),
                                                 rel_prefix="teams/%s" % TEAM_A)
        rej_paths = [p for p, _why in rejected]
        self.assertIn("teams/%s/learnings/mine.md" % TEAM_A, allow)
        self.assertNotIn("teams/%s/learnings/theirs.md" % TEAM_B, allow)
        self.assertIn("teams/%s/learnings/theirs.md" % TEAM_B, rej_paths)
        self.assertTrue(any("cross-team" in why for _p, why in rejected), rejected)

    # --- criteria 3 + 4: fleet shards and the two reports -------------------

    def test_two_teammates_shards_coexist_and_both_reports_read_them(self):
        """Criteria 3 and 4 in one flow, because they are one claim: the reports
        are only meaningful if the shards they read are genuinely separate.

        Shards are per-USER, not per-team, so two engineers on the SAME team get
        different files — that is what stops their lines colliding textually on a
        rebase, and it is asserted here rather than assumed.

        The events are COMMITTED before the reports run. The sink's whole purpose
        is to outlive the machine that wrote it; a report that only works against
        a dirty worktree would be reading local state, which the machine-local
        metrics log already does.
        """
        self.two_teams()
        self.emit(TEAM_A, TEAMMATE_A, OUTCOME_OK, "2026-08-10")
        self.emit(TEAM_A, TEAMMATE_B, OUTCOME_FAIL, "2026-08-18")
        self.emit(TEAM_B, TEAMMATE_B, OUTCOME_OK, "2026-08-18")

        shard_a = os.path.join(self.team_dir(TEAM_A), "fleet", "%s.jsonl" % TEAMMATE_A)
        shard_b = os.path.join(self.team_dir(TEAM_A), "fleet", "%s.jsonl" % TEAMMATE_B)
        shard_c = os.path.join(self.team_dir(TEAM_B), "fleet", "%s.jsonl" % TEAMMATE_B)
        for p in (shard_a, shard_b, shard_c):
            self.assertTrue(os.path.isfile(p), "missing shard %s" % p)
        # Same team, two engineers => two FILES, not one interleaved file.
        self.assertNotEqual(shard_a, shard_b)

        self._git("add", "-A", "--", "teams")
        self._git("commit", "-q", "-m", "fleet events")
        st = self._git("status", "--porcelain", "--", "teams/*/fleet")
        self.assertEqual(st.stdout.strip(), "", "fleet sink left uncommitted")

        rc, out, err = self.cli(["fleet", "issues", "--format", "json"],
                                kdir=self.team_dir(TEAM_A))
        self.assertEqual(rc, 0, err)
        issues = json.loads(out)
        self.assertEqual(issues["command"], "issues")
        self.assertEqual(issues["n_events"], 3)
        counts = {r["outcome"]: r["count"] for r in issues["top_outcomes"]}
        self.assertEqual(counts, {OUTCOME_OK: 2, OUTCOME_FAIL: 1})

        # Team scoping: TEAM_B contributed exactly one of the three.
        rc, out, err = self.cli(["fleet", "issues", "--team", TEAM_B,
                                 "--format", "json"], kdir=self.team_dir(TEAM_A))
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["n_events"], 1)

        rc, out, err = self.cli(["fleet", "impact", "--since", "2026-08-15",
                                 "--format", "json"], kdir=self.team_dir(TEAM_A))
        self.assertEqual(rc, 0, err)
        impact = json.loads(out)
        self.assertEqual(impact["command"], "impact")
        self.assertEqual((impact["n_before"], impact["n_after"]), (1, 2))
        # R-ORG-07: below the floor the delta is SUPPRESSED, and the caveat rides
        # every payload. A fleet report that reported a confident delta from three
        # events would be the failure mode the floor exists for.
        self.assertTrue(impact["insufficient_data"])
        self.assertIsNone(impact["fail_rate_delta"])
        self.assertIn("correlational", impact["caveat"])

    def test_the_committed_sink_the_loop_writes_is_org_lint_valid(self):
        """`fleet-emit`'s own output must pass org lint O12 unedited.

        O12 failing hard-blocks the pre-hook on every SUBSEQUENT run in team
        mode, so an emitter that writes an outcome the enum does not name would
        take the whole fleet down one run after the first bad event — the reason
        plan task 34 reconciled the two vocabularies before this landed. The
        negative half is asserted too: an outcome outside the enum IS caught, so
        a green O12 here is not a check that never fires.
        """
        self.two_teams()
        self.emit(TEAM_A, TEAMMATE_A, OUTCOME_FAIL, "2026-08-18")

        rc, out, err = self.cli(["org", "lint", "--format", "json"],
                                kdir=self.team_dir(TEAM_A))
        self.assertEqual(rc, 0, err)
        report = json.loads(out)
        self.assertTrue(report["engaged"])
        self.assertEqual([e for e in report["errors"] if e["rule"] == "O12"], [])

        shard = os.path.join(self.team_dir(TEAM_A), "fleet", "%s.jsonl" % TEAMMATE_A)
        with open(shard, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "terminal", "ts": "2026-08-18",
                                "feature": "f", "outcome": "not_an_outcome",
                                "steering_sha": "deadbeef", "team": TEAM_A,
                                "handle": TEAMMATE_A}) + "\n")
        rc, out, _err = self.cli(["org", "lint", "--format", "json"],
                                 kdir=self.team_dir(TEAM_A))
        o12 = [e for e in json.loads(out)["errors"] if e["rule"] == "O12"]
        self.assertTrue(o12, "O12 accepted an outcome outside the enum")


if __name__ == "__main__":
    unittest.main()
