"""The loop's COMMITTED fleet sink — `emit_fleet_line` / `commit_fleet_sink_only`
(scripts/loop/kbsync.sh + the failure branch in scripts/loop/outcome.sh).

Feature 260813-modular-core-adoption, task 65. Donor wiring re-injected here: the
`fleet-emit` VERB was ported (it is one of the six donor-only verbs the presence
gate covers) but nothing in this tree's loop ever called it, so R-ORG-06 — "a loop
run MUST emit ONE committed steering_sha-stamped terminal event, including on
failure exit paths" — was satisfied by no code at all. The sink existed, the
reports read it, and it stayed empty forever.

That is why this file tests the CALL PATH and not the emitter. `fleet-emit` itself
already has coverage; what had no coverage was whether anything reaches it. The
three properties below are each a way the wiring can be present and still wrong:

  ONCE      — a completed run emits inside `run_kb_sync` so the line rides the KB
              commit; the EXIT trap must then NOT emit a second line. Two lines per
              run double-counts every outcome in every aggregate, and the report has
              no way to tell a duplicate from a genuine re-run.
  ON FAILURE — hit_max_iterations / post_hook_failure / blocked_on_ambiguity /
              sync_failed all exit BEFORE `run_kb_sync`. If only the healthy path
              emitted, the sink would record successes exclusively and every fleet
              report over it would be a survivorship-biased flat line that reads as
              a healthy fleet. These are the exact events fleet observability is for.
  INERT     — a flat single-operator KB must write and commit NOTHING (R-ORG-04).

Hermetic: the SHIPPED bash libs are sourced with a stub toolkit for the control-flow
assertions, and driven against a real scratch org repo with the REAL CLI for the
end-to-end one. Stdlib unittest + subprocess.
"""

from tests._paths import REPO_ROOT

import json
import os
import shutil
import subprocess
import tempfile
import unittest

LOOP_DIR = os.path.join(REPO_ROOT, "scripts", "loop")
KBSYNC = os.path.join(LOOP_DIR, "kbsync.sh")
OUTCOME = os.path.join(LOOP_DIR, "outcome.sh")

FAILURE_OUTCOMES = ("hit_max_iterations", "post_hook_failure",
                    "blocked_on_ambiguity", "sync_failed")


class FleetSinkHarness(unittest.TestCase):
    """Sources the real loop libs with the minimum surrounding loop state."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-fleet-sink-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.docs = os.path.join(self.root, "docs")
        self.state = os.path.join(self.root, "state")
        os.makedirs(self.docs)
        os.makedirs(self.state)
        with open(os.path.join(self.docs, "plan.md"), "w", encoding="utf-8") as f:
            f.write("# Plan: demo\n\n- ✅ **1** done\n- ⬜ **2** open\n")

    def stub_toolkit(self, probe_ok=True):
        """A scratch cwd whose `scripts/agentware` records its argv instead of running.

        The recording is what lets the ONCE property be asserted as a COUNT rather
        than as "a file exists" — one emission and two emissions both leave a file.
        """
        d = os.path.join(self.root, "stub")
        os.makedirs(os.path.join(d, "scripts"), exist_ok=True)
        calls = os.path.join(d, "calls.log")
        stub = os.path.join(d, "scripts", "agentware")
        with open(stub, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n")
            f.write('printf "%%s\\n" "$*" >> %s\n' % calls)
            if probe_ok:
                f.write('for a in "$@"; do [[ "$a" == "--help" ]] && exit 0; done\n')
            else:
                f.write('for a in "$@"; do [[ "$a" == "--help" ]] && exit 2; done\n')
            f.write("exit 0\n")
        os.chmod(stub, 0o755)
        return d, calls

    def run_bash(self, body, cwd, extra_env=None):
        preamble = (
            'set -uo pipefail\n'
            'log() { :; }\n'
            'open_markers() { echo 1; }\n'
            'DOCS_DIR=%s\nSTATE_DIR=%s\n'
            'FEATURE_NAME=demo-feature\nFEATURE=260819-demo-feature\n'
            'LOOP_ITERATIONS_USED=3\nMAX_ITERATIONS=3\n'
            % (self.docs, self.state))
        env = dict(os.environ)
        env["AGENTWARE_NESTED_UNITTEST"] = "1"
        env.pop("AGENTWARE_ORG_ROOT", None)
        env.pop("AGENTWARE_TEAM", None)
        env.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        env.update(extra_env or {})
        p = subprocess.run(["bash", "-c", preamble + body], cwd=cwd, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True)
        return p.returncode, p.stdout

    @staticmethod
    def emitted(calls_path):
        """The `fleet-emit` invocations recorded by the stub (excluding the probe)."""
        if not os.path.isfile(calls_path):
            return []
        with open(calls_path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        return [ln for ln in lines
                if ln.startswith("fleet-emit") and "--help" not in ln]


class EmitFleetLineTest(FleetSinkHarness):
    def test_it_passes_the_run_outcome_feature_and_plan_tallies(self):
        """The event's fields are what the reports group by; an emitter that fires
        with an empty outcome produces a line that lands in the sink and drops out
        of every aggregate, which is indistinguishable from a run that never ran."""
        d, calls = self.stub_toolkit()
        rc, out = self.run_bash(
            'source "%s"\nLOOP_OUTCOME=hit_max_iterations\nemit_fleet_line\n'
            % KBSYNC, cwd=d)
        self.assertEqual(rc, 0, out)
        emits = self.emitted(calls)
        self.assertEqual(len(emits), 1, emits)
        argv = emits[0]
        self.assertIn("--outcome hit_max_iterations", argv)
        self.assertIn("--feature demo-feature", argv)
        self.assertIn("--iterations-used 3", argv)
        self.assertIn("--max 3", argv)
        # One ✅ and one ⬜ in the fixture plan.
        self.assertIn("--tasks-total 2", argv)
        self.assertIn("--tasks-done 1", argv)
        self.assertIn("--promise-status pending", argv)

    def test_it_reports_the_promise_signalled_once_the_done_stamp_exists(self):
        d, calls = self.stub_toolkit()
        os.makedirs(os.path.join(self.state), exist_ok=True)
        open(os.path.join(self.state, ".done"), "w").close()
        rc, out = self.run_bash(
            'source "%s"\nLOOP_OUTCOME=completed\nemit_fleet_line\n' % KBSYNC, cwd=d)
        self.assertEqual(rc, 0, out)
        self.assertIn("--promise-status signalled", self.emitted(calls)[0])

    def test_it_is_a_no_op_on_a_build_that_does_not_serve_the_verb(self):
        """Telemetry may never be the reason a run fails. A build without
        `fleet-emit` must skip silently rather than error into the loop."""
        d, calls = self.stub_toolkit(probe_ok=False)
        rc, out = self.run_bash(
            'source "%s"\nLOOP_OUTCOME=completed\nemit_fleet_line\necho DONE\n'
            % KBSYNC, cwd=d)
        self.assertEqual(rc, 0, out)
        self.assertIn("DONE", out)
        self.assertEqual(self.emitted(calls), [])


class ExitTrapRoutingTest(FleetSinkHarness):
    """WHICH outcomes reach the failure-path micro-commit, and how many times."""

    def _loop_on_exit(self, outcome, already_committed=False):
        d, calls = self.stub_toolkit()
        # Stub commit_fleet_sink_only so this asserts ROUTING, not the commit.
        rc, out = self.run_bash(
            'metrics_emit_enabled() { return 1; }\n'
            'source "%s"\n'
            'trap - EXIT\n'
            'commit_fleet_sink_only() { echo "SINK_COMMIT"; }\n'
            'LOOP_STARTED=true\nLOOP_OUTCOME=%s\nFLEET_SINK_COMMITTED=%s\n'
            'loop_on_exit\n'
            % (OUTCOME, outcome, "true" if already_committed else "false"), cwd=d)
        self.assertEqual(rc, 0, out)
        return out

    def test_every_failure_outcome_commits_its_terminal_line(self):
        for outcome in FAILURE_OUTCOMES:
            with self.subTest(outcome=outcome):
                self.assertIn("SINK_COMMIT", self._loop_on_exit(outcome))

    def test_a_completed_run_does_not_double_commit(self):
        """`completed` emits inside run_kb_sync and rides the KB commit; a trap that
        also fired would put TWO terminal lines in the sink for one run."""
        self.assertNotIn("SINK_COMMIT", self._loop_on_exit("completed"))

    def test_the_guard_flag_suppresses_a_second_line_on_any_outcome(self):
        for outcome in FAILURE_OUTCOMES:
            with self.subTest(outcome=outcome):
                self.assertNotIn(
                    "SINK_COMMIT",
                    self._loop_on_exit(outcome, already_committed=True))

    def test_a_run_that_never_started_emits_nothing(self):
        """--help / --dry-run / a failed dependency preflight exit before the run
        begins; a terminal event for them would be a run that never happened."""
        d, _calls = self.stub_toolkit()
        rc, out = self.run_bash(
            'metrics_emit_enabled() { return 1; }\n'
            'source "%s"\ntrap - EXIT\n'
            'commit_fleet_sink_only() { echo "SINK_COMMIT"; }\n'
            'LOOP_STARTED=false\nLOOP_OUTCOME=hit_max_iterations\n'
            'loop_on_exit\n' % OUTCOME, cwd=d)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("SINK_COMMIT", out)

    def test_the_healthy_path_marks_the_line_committed_before_the_kb_commit(self):
        """run_kb_sync must set the guard, or the trap fires afterwards and the
        completed path becomes the double-emitting one."""
        with open(KBSYNC, encoding="utf-8") as f:
            body = f.read()
        emit_at = body.index("\n    emit_fleet_line\n")
        guard_at = body.index("FLEET_SINK_COMMITTED=true", emit_at)
        commit_at = body.index("kb-git commit", emit_at)
        self.assertLess(emit_at, guard_at)
        self.assertLess(guard_at, commit_at,
                        "the fleet line must be staged BEFORE the KB commit it rides")


class CommittedSinkEndToEndTest(FleetSinkHarness):
    """The real CLI against a real git org repo — what actually lands on disk."""

    def _org_repo(self):
        org = os.path.join(self.root, "org")
        cfg = os.path.join(self.root, "cfg")
        os.makedirs(org)
        os.makedirs(cfg)
        for args in (("init", "-q", "."), ("config", "user.email", "e2e@local"),
                     ("config", "user.name", "e2e")):
            subprocess.run(["git", "-C", org] + list(args),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        env = {"AGENTWARE_CONFIG_HOME": cfg, "AGENTWARE_ORG_ROOT": org,
               "AGENTWARE_USER_HANDLE": "alice"}
        p = subprocess.run(
            ["python3", os.path.join(REPO_ROOT, "scripts", "agentware"),
             "team", "create", "--name", "platform", "--format", "json"],
            cwd=REPO_ROOT, env=dict(os.environ, **env),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        kdir = os.path.join(org, "teams", "platform")
        open(os.path.join(kdir, ".initialized"), "w").close()
        env["AGENTWARE_KNOWLEDGE_DIR"] = kdir
        return org, kdir, env

    def test_a_failure_exit_commits_exactly_the_fleet_shard_and_nothing_else(self):
        """The micro-commit is path-scoped on purpose: a failed run's tree holds
        half-finished edits, and sweeping them into a telemetry commit would
        publish work the run never verified."""
        org, kdir, env = self._org_repo()
        stray = os.path.join(kdir, "learnings", "unrelated-pending-edit.md")
        os.makedirs(os.path.dirname(stray), exist_ok=True)
        with open(stray, "w", encoding="utf-8") as f:
            f.write("# a pending edit the failed run must not publish\n")
        subprocess.run(["git", "-C", org, "add", "-A", "--", "teams"],
                       stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-C", org, "commit", "-q", "-m", "seed"],
                       stdout=subprocess.DEVNULL)
        with open(stray, "a", encoding="utf-8") as f:
            f.write("more\n")

        rc, out = self.run_bash(
            'source "%s"\nLOOP_OUTCOME=hit_max_iterations\n'
            'commit_fleet_sink_only\necho "GUARD=${FLEET_SINK_COMMITTED:-unset}"\n'
            % KBSYNC, cwd=REPO_ROOT, extra_env=env)
        self.assertEqual(rc, 0, out)
        self.assertIn("GUARD=true", out)

        show = subprocess.run(
            ["git", "-C", org, "show", "--name-only", "--format=%s", "HEAD"],
            stdout=subprocess.PIPE, text=True).stdout.split()
        self.assertIn("teams/platform/fleet/alice.jsonl", show)
        self.assertNotIn("teams/platform/learnings/unrelated-pending-edit.md", show)

        shard = os.path.join(kdir, "fleet", "alice.jsonl")
        with open(shard, encoding="utf-8") as f:
            events = [json.loads(ln) for ln in f if ln.strip()]
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["event"], "terminal")
        self.assertEqual(ev["outcome"], "hit_max_iterations")
        self.assertEqual(ev["team"], "platform")
        self.assertEqual(ev["handle"], "alice")
        # The causal join key the reports group by (R-ORG-06).
        self.assertTrue(ev.get("steering_sha"))
        self.assertNotEqual(ev["steering_sha"], "unknown")

    def test_it_writes_and_commits_nothing_on_a_flat_knowledge_dir(self):
        """R-ORG-04: with the org keys unset a single-operator install must be
        byte-identical to one that never heard of teams."""
        flat = os.path.join(self.root, "flat")
        os.makedirs(flat)
        subprocess.run(["git", "-C", flat, "init", "-q", "."],
                       stdout=subprocess.DEVNULL)
        open(os.path.join(flat, ".initialized"), "w").close()
        before = sorted(os.listdir(flat))

        rc, out = self.run_bash(
            'source "%s"\nLOOP_OUTCOME=hit_max_iterations\n'
            'commit_fleet_sink_only\necho "GUARD=${FLEET_SINK_COMMITTED:-unset}"\n'
            % KBSYNC, cwd=REPO_ROOT,
            extra_env={"AGENTWARE_KNOWLEDGE_DIR": flat,
                       "AGENTWARE_CONFIG_HOME": os.path.join(self.root, "cfg2")})
        self.assertEqual(rc, 0, out)
        self.assertIn("GUARD=unset", out)
        self.assertEqual(sorted(os.listdir(flat)), before)
        self.assertFalse(os.path.exists(os.path.join(flat, "fleet")))
        log = subprocess.run(["git", "-C", flat, "log", "--oneline"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True)
        self.assertNotEqual(log.returncode, 0, "a flat KB gained a commit")


if __name__ == "__main__":
    unittest.main()
