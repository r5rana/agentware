"""Tests for the agentware.sh loop metrics emission (feature 260626-observability-suite, Task 6).

The loop appends ONE structured JSON event per phase/iteration to
`<kdir>/logs/metrics.jsonl` so that the LOOP ITSELF is observable (agentware IS
the loop). These tests drive the REAL `agentware.sh` end-to-end against a
SYNTHETIC, throwaway knowledge dir (`AGENTWARE_KNOWLEDGE_DIR`) and a FAKE agent
runtime (`AGENTWARE_CLI`) that completes the plan deterministically — the
operator KB is NEVER touched (R-LOC-03).

Asserts:
  - one parseable JSON line per iteration across pre/main/post phases;
  - each line carries the full Task-6 schema (ts, feature, stage, phase,
    iteration, max, tasks_total, tasks_remaining, tasks_done_delta,
    promise_status, result, phase_wall_s, self_heal_count) + the gen_ai.* aliases;
  - the main-loop burndown (tasks_done_delta) tracks markers flipping ⬜→✅;
  - with AGENTWARE_METRICS_EMIT=0 (opt-out) NO line is appended;
  - every emitted line parses via `json.loads` (the `python3 -c "import json"` gate).

Also the named evidence for parity row `loop:outcome-value-union` (plan task 34),
whose mutation is "drop either locally-emitted outcome value from the accepted set:
the pre-hook hard-blocks the next run in team mode". `LoopOutcomeUnionTest` and
`OutcomeUnionPreHookTest` below prove BOTH halves: the value set the loop can emit
is re-derived from the shell sources on every run and must be covered by
`agentware_lib.ops.fleet.LOOP_OUTCOME_ENUM` (which org lint O12 enforces), and the
real pre-hook org-lint gate is driven over a real fleet sink carrying each of those
values — with the enum mutated to prove the gate would otherwise hard-block.

Runner:  python3 -m unittest tests.integration.test_loop_emission -v
"""

import glob
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

try:
    from tests._fixtures import REPO_ROOT
except ImportError:  # when run via `discover -s tests`, tests/ is on sys.path
    from _fixtures import REPO_ROOT

AGENTWARE_SH = os.path.join(REPO_ROOT, "agentware.sh")
FEATURE = "260626-loop-emit-test"

PLAN_MD = """# Plan — loop emission test

## Tasks

- ⬜ **1** First synthetic task.
  *Verify:* trivial.
- ⬜ **2** Second synthetic task.
  *Verify:* trivial.

## Acceptance criteria

- [ ] both tasks done

<promise>TASK_COMPLETE</promise>
"""

# A FAKE agent runtime. It ignores its flags, prints every phase's completion
# promise (so pre/main/post each complete on their first task), and — ONLY when
# handed the MAIN prompt — flips the first remaining open marker (⬜/🟡 → ✅) in
# the plan so the main loop burns down one task per iteration and then completes.
FAKE_CLI = r"""#!/usr/bin/env bash
# Flip exactly one open marker, but only for the main phase (its prompt asks to
# "find the next task marked"). Pre/post prompts must not mutate the plan.
if printf '%s' "$*" | grep -q "find the next task marked"; then
  python3 - "$FAKE_PLAN" <<'PY'
import re, sys
p = sys.argv[1]
with open(p, encoding="utf-8") as f:
    s = f.read()
s = re.sub(r"⬜|🟡", "✅", s, count=1)
with open(p, "w", encoding="utf-8") as f:
    f.write(s)
PY
fi
echo "<promise>PRE_TASK_COMPLETE</promise>"
echo "<promise>TASK_COMPLETE</promise>"
echo "<promise>POST_COMPLETE</promise>"
"""

REQUIRED_KEYS = {
    "ts", "feature", "stage", "phase", "iteration", "max", "tasks_total",
    "tasks_remaining", "tasks_done_delta", "promise_status", "result",
    "phase_wall_s", "self_heal_count",
}


def _have(binary):
    return shutil.which(binary) is not None


@unittest.skipUnless(_have("jq"), "jq (a hard agentware.sh preflight dep) is required")
@unittest.skipUnless(_have("bash"), "bash is required to run agentware.sh")
class LoopEmissionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-emit-")
        # Patch HOME so that `scripts/agentware init` (called by the loop's
        # preflight) writes config.env into the TEMP dir, never the operator's
        # real ~/.agentware/config.env. Without this, running the full test suite
        # clobbers the real config pointer — the load-bearing isolation fix.
        self.tmp_home = os.path.join(self.tmp, "home")
        os.makedirs(self.tmp_home)
        # Bin dir for the fake CLI: named 'claude' so the per-phase routing
        # resolution (scripts/agentware config --main-cli-only -> "claude") finds
        # OUR fake binary on PATH instead of the real claude binary.
        self.bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin_dir)
        self.kdir = os.path.join(self.tmp, "kb")
        self.docs = os.path.join(self.kdir, "work", FEATURE)
        os.makedirs(self.docs)
        self.plan = os.path.join(self.docs, "plan.md")
        with open(self.plan, "w", encoding="utf-8") as f:
            f.write(PLAN_MD)
        # Deliberately do NOT create <kdir>/.initialized: keeping the workspace
        # "uninitialized" makes the pre/post toolkit gates no-op cleanly so the
        # test exercises ONLY the loop + emitter, never the index/KB machinery.
        # Write the fake binary as 'claude' in bin_dir so it shadows the real one.
        self.fake_cli = os.path.join(self.bin_dir, "claude")
        with open(self.fake_cli, "w", encoding="utf-8") as f:
            f.write(FAKE_CLI)
        os.chmod(self.fake_cli, os.stat(self.fake_cli).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        self.metrics = os.path.join(self.kdir, "logs", "metrics.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _env(self, **overrides):
        env = dict(os.environ)
        env.update({
            "HOME": self.tmp_home,             # isolate config writes from real ~
            "AGENTWARE_KNOWLEDGE_DIR": self.kdir,
            "AGENTWARE_CLI": "claude",         # use the token, not a path
            "AGENTWARE_KB_AUTOCOMMIT": "0",   # no git side effects in the test
            "AGENTWARE_KB_PUSH": "0",         # no push attempts in the test
            "AGENTWARE_NO_STREAM": "1",       # no background tail follower
            "FAKE_PLAN": self.plan,
            # Prepend bin_dir so 'claude' resolves to our fake binary first
            "PATH": self.bin_dir + os.pathsep + os.environ.get("PATH", ""),
        })
        env.update(overrides)
        return env

    def _run(self, env):
        return subprocess.run(
            ["bash", AGENTWARE_SH, FEATURE],
            cwd=REPO_ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=180,
        )

    def _reset_plan(self):
        with open(self.plan, "w", encoding="utf-8") as f:
            f.write(PLAN_MD)

    def _read_events(self):
        events = []
        with open(self.metrics, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                events.append(json.loads(line))  # the `import json` parse gate
        return events

    def test_emits_one_parseable_event_per_iteration(self):
        proc = self._run(self._env())
        self.assertTrue(
            os.path.exists(self.metrics),
            "metrics.jsonl was not created\n--- loop output ---\n%s" % proc.stdout,
        )
        events = self._read_events()
        self.assertGreaterEqual(len(events), 1, "no events emitted")

        # Task 7 adds task_transition + terminal events to the SAME channel; the
        # Task-6 per-iteration/phase schema applies only to events WITHOUT an
        # `event` discriminator. Scope the Task-6 assertions to those.
        events = [ev for ev in events if ev.get("event") is None]
        self.assertGreaterEqual(len(events), 1, "no per-iteration events emitted")

        # Every event carries the full Task-6 schema + OTel GenAI aliases.
        for ev in events:
            self.assertTrue(
                REQUIRED_KEYS.issubset(ev.keys()),
                "missing keys %s in %s" % (REQUIRED_KEYS - set(ev.keys()), ev),
            )
            self.assertEqual(ev["feature"], FEATURE)
            self.assertIsInstance(ev["iteration"], int)
            self.assertIsInstance(ev["tasks_remaining"], int)
            self.assertIsInstance(ev["tasks_done_delta"], int)
            self.assertIsInstance(ev["self_heal_count"], int)
            self.assertEqual(ev["stage"], "loop-%s" % ev["phase"])
            self.assertEqual(ev["gen_ai.system"], "agentware")
            self.assertEqual(ev["gen_ai.operation.name"], "agentware.loop")

        # The pre, main, and post phases each emit at least one event.
        phases = {ev["phase"] for ev in events}
        self.assertIn("pre", phases)
        self.assertIn("main", phases)
        self.assertIn("post", phases)

        # The main loop emits one event per iteration and burns the plan down:
        # 2 open tasks -> iter1 closes one (delta 1) -> iter2 closes the last.
        main = [ev for ev in events if ev["phase"] == "main"]
        self.assertEqual([ev["iteration"] for ev in main], list(range(1, len(main) + 1)),
                         "main iterations must be contiguous from 1")
        self.assertEqual(sum(ev["tasks_done_delta"] for ev in main), 2,
                         "total burndown must equal the 2 tasks closed")
        self.assertEqual(main[-1]["tasks_remaining"], 0)
        self.assertEqual(main[-1]["promise_status"], "signalled")
        for ev in main:
            self.assertEqual(ev["max"], 100)            # default MAX_ITERATIONS
            self.assertEqual(ev["tasks_total"], 2)
            self.assertGreaterEqual(ev["tasks_done_delta"], 0)  # never un-flips

    def test_the_terminal_event_of_a_real_run_is_accepted_by_the_fleet_enum(self):
        # The bridge between the two halves of plan task 34: the value a REAL
        # scripted run puts on the ledger is the same value cmd_fleet_emit copies
        # into the committed sink that org lint O12 judges. Asserting it here (on
        # the loop's own output, not on a hand-written string) is what makes the
        # union claim about THIS loop rather than about a constant.
        self._run(self._env())
        terminal = [ev for ev in self._read_events() if ev.get("event") == "terminal"]
        self.assertTrue(terminal, "the run emitted no terminal event")
        fleet, _derive = _lib()
        for ev in terminal:
            self.assertIn(ev["outcome"], fleet.LOOP_OUTCOME_ENUM,
                          "a real run emitted %r, which org lint O12 would reject "
                          "in team mode" % ev["outcome"])
        self.assertEqual(terminal[-1]["outcome"], "completed")

    def test_opt_out_emits_nothing(self):
        proc = self._run(self._env(AGENTWARE_METRICS_EMIT="0"))
        self.assertFalse(
            os.path.exists(self.metrics) and os.path.getsize(self.metrics) > 0,
            "AGENTWARE_METRICS_EMIT=0 must append NO metrics line\n"
            "--- loop output ---\n%s" % proc.stdout,
        )


# ---------------------------------------------------------------------------
# Plan task 34 — the LOOP_OUTCOME value set is the UNION
# ---------------------------------------------------------------------------
#
# The loop's terminal vocabulary has two consumers of DIFFERENT widths, and the
# wide one is a GATE: org lint O12 rejects any fleet-sink outcome outside
# `agentware_lib.ops.fleet.LOOP_OUTCOME_ENUM`, and the pre/post-hook org-lint gate
# exits 1 on an O12 error. The sink is append-only, so a single event carrying a
# value the enum does not name hard-blocks EVERY subsequent run in team mode —
# there is no way to make the tree valid again short of editing history.
#
# The donor enum omits two outcomes THIS tree sets: `premise_falsified` (the
# R-GND-01 premise-grounding halt) and `adversarial_review_blocked` (the R-EXEC-06
# self-heal block). Both are proven live below rather than asserted from a list:
# the emitted set is re-derived from the shell sources, and the gate is driven over
# a sink written by the REAL emitter.

LOOP_SH_DIR = os.path.join(REPO_ROOT, "scripts", "loop")
ORGLINT_SH = os.path.join(LOOP_SH_DIR, "orglint.sh")
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")

# A set-point is a LOOP_OUTCOME assignment in command position. Comment lines are
# skipped so prose naming an outcome (safety.sh documents the plan-claim refusal
# shape in a comment) is never miscounted as an emission — the same
# command-position discipline tests/_loop_wiring.py uses.
_ASSIGN_RE = re.compile(r'^\s*(?:local\s+)?LOOP_OUTCOME="([A-Za-z0-9_]+)"')

# The set-points that existed when task 34 reconciled the vocabulary. A FLOOR, not
# an equality: a new terminal is allowed to appear (it must then be added to the
# enum, which `test_every_emitted_outcome_is_accepted_by_the_fleet_enum` forces),
# but a regex that silently stops matching — a renamed file, a changed quoting
# style — reddens here instead of passing vacuously over an empty set.
EMITTED_FLOOR = frozenset({
    "completed", "hit_max_iterations", "post_hook_failure", "pre_hook_abort",
    "premise_falsified", "blocked_on_ambiguity", "adversarial_review_blocked",
    "unknown",
})

# The two the DONOR enum lacks — the whole reason this row exists.
LOCAL_ONLY_OUTCOMES = ("premise_falsified", "adversarial_review_blocked")


def emitted_outcomes():
    """Every value assigned to LOOP_OUTCOME -> the sorted list of its call sites.

    Derived from the shipped shell on every run: agentware.sh plus every
    scripts/loop/*.sh it sources. Pure/read-only.
    """
    sites = {}
    sources = [AGENTWARE_SH] + sorted(glob.glob(os.path.join(LOOP_SH_DIR, "*.sh")))
    for path in sources:
        rel = os.path.relpath(path, REPO_ROOT)
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if line.lstrip().startswith("#"):
                    continue
                m = _ASSIGN_RE.match(line)
                if m:
                    sites.setdefault(m.group(1), []).append("%s:%d" % (rel, i))
    return sites


def _lib():
    """Import the modular lib the way the entrypoint does (scripts/ on sys.path)."""
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    import agentware_lib.ops.derive as derive
    import agentware_lib.ops.fleet as fleet
    return fleet, derive


class LoopOutcomeUnionTest(unittest.TestCase):
    """The emitted vocabulary vs the two constants that consume it."""

    def setUp(self):
        self.sites = emitted_outcomes()
        self.fleet, self.derive = _lib()

    def test_the_scan_finds_every_known_set_point(self):
        missing = sorted(EMITTED_FLOOR - set(self.sites))
        self.assertEqual(
            missing, [],
            "the LOOP_OUTCOME scan lost set-points %s — the emitted set is the "
            "input to every assertion below, so a scan that matches nothing "
            "would make them all pass vacuously" % missing)

    def test_every_emitted_outcome_is_accepted_by_the_fleet_enum(self):
        enum = set(self.fleet.LOOP_OUTCOME_ENUM)
        unaccepted = {k: v for k, v in self.sites.items() if k not in enum}
        self.assertEqual(
            unaccepted, {},
            "these outcomes are emitted by the loop but REJECTED by org lint O12: "
            "%s. The fleet sink is append-only, so one such event hard-blocks "
            "every subsequent run in team mode. Widen LOOP_OUTCOME_ENUM in "
            "agentware_lib/ops/fleet/fleetshard.py in the same change."
            % sorted(unaccepted))

    def test_the_two_local_only_outcomes_are_in_the_union(self):
        # Named explicitly: the donor enum has neither, and the derived check
        # above would still pass if a future edit deleted BOTH set-points.
        for outcome in LOCAL_ONLY_OUTCOMES:
            self.assertIn(outcome, self.sites,
                          "%s is no longer emitted by the loop" % outcome)
            self.assertIn(outcome, self.fleet.LOOP_OUTCOME_ENUM,
                          "%s is emitted but not in LOOP_OUTCOME_ENUM" % outcome)

    def test_derive_terminal_outcomes_is_narrower_but_never_wider(self):
        # The two consumers are DELIBERATELY different widths: derive_outcome
        # trusts a narrow set (the R-EXEC-06 hard blockers are preserved by name
        # elsewhere so the graph HALT signal survives). Narrower is by design;
        # WIDER would mean derive trusts a value the sink gate rejects.
        narrow = set(self.derive._TERMINAL_OUTCOMES)
        self.assertLessEqual(narrow, set(self.fleet.LOOP_OUTCOME_ENUM))
        self.assertIn("premise_falsified", narrow,
                      "the donor tuple omits premise_falsified, which collapses a "
                      "premise-gate halt to `unknown` in derive_outcome")

    def test_o12_still_rejects_an_outcome_outside_the_enum(self):
        # Non-vacuity: the union must widen the gate, never disarm it.
        org = tempfile.mkdtemp(prefix="aw-o12-")
        self.addCleanup(shutil.rmtree, org, True)
        shard = os.path.join(org, "teams", "core", "fleet", "probe.jsonl")
        os.makedirs(os.path.dirname(shard))
        with open(shard, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"event": "terminal", "ts": "2026-08-14",
                                 "outcome": "not_a_loop_outcome",
                                 "steering_sha": "deadbeef", "team": "core"}) + "\n")
        res = _lint_org_out_of_process(org)
        rules = [e["rule"] for e in res["errors"]]
        self.assertIn("O12", rules, res)


def _lint_org_out_of_process(org_root, drop=None):
    """Run the REAL lint_org over `org_root` in a clean subprocess -> its result.

    Out of process because AGENTWARE_ORG_ROOT is read at call time and the lib
    caches config: an in-process env mutation would leak into every later test in
    the run. `drop` removes one value from LOOP_OUTCOME_ENUM first (the mutation
    proof) without touching the shipped source.
    """
    code = (
        "import json,os,sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import agentware_lib.ops.fleet as f, agentware_lib.ops.orglint as o\n"
        "drop = os.environ.get('AW_DROP_OUTCOME')\n"
        "if drop:\n"
        "    f.LOOP_OUTCOME_ENUM = tuple(x for x in f.LOOP_OUTCOME_ENUM if x != drop)\n"
        "print(json.dumps(o.lint_org(sys.argv[2])))\n"
    )
    env = _clean_org_env(org_root)
    if drop:
        env["AW_DROP_OUTCOME"] = drop
    kdir = os.path.join(org_root, "teams", "core")
    p = subprocess.run([sys.executable, "-c", code, SCRIPTS_DIR, kdir],
                       env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, timeout=120)
    if p.returncode != 0:
        raise AssertionError("lint_org probe failed: %s" % p.stderr)
    return json.loads(p.stdout.strip().splitlines()[-1])


def _clean_org_env(org_root):
    """Env for an ORG-mode subprocess isolated from the operator's own config."""
    env = dict(os.environ)
    env.pop("AW_DROP_OUTCOME", None)
    env["AGENTWARE_ORG_ROOT"] = org_root
    env["AGENTWARE_KNOWLEDGE_DIR"] = os.path.join(org_root, "teams", "core")
    env["AGENTWARE_CONFIG_HOME"] = os.path.join(org_root, "_config")
    env["HOME"] = os.path.join(org_root, "_home")
    return env


@unittest.skipUnless(_have("bash"), "bash is required to source the gate")
class OutcomeUnionPreHookTest(unittest.TestCase):
    """The gate half of the row: a real sink, the real emitter, the real gate.

    The mutation proof ("drop either locally-emitted outcome value from the
    accepted set: the pre-hook hard-blocks the next run in team mode") is executed
    rather than described — the enum is narrowed in the subprocess and the SAME
    gate that proceeds above then exits 1.
    """

    def setUp(self):
        self.org = tempfile.mkdtemp(prefix="aw-outcome-union-")
        self.addCleanup(shutil.rmtree, self.org, True)
        self.kdir = os.path.join(self.org, "teams", "core")
        for sub in ("_home", "_config"):
            os.makedirs(os.path.join(self.org, sub))
        os.makedirs(self.kdir)
        self.emitted = sorted(emitted_outcomes())
        self._emit_a_run_per_outcome()

    def _emit_a_run_per_outcome(self):
        """Write ONE terminal event per emitted outcome through the REAL emitter.

        cmd_fleet_emit is the only writer of the committed sink, so driving it
        (rather than hand-writing JSON) proves the bytes the loop actually
        produces satisfy O12 — schema keys, steering_sha and outcome together.
        """
        code = (
            "import sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import agentware_lib.ops.fleet as f\n"
            "class A: pass\n"
            "for oc in sys.argv[2:]:\n"
            "    a = A(); a.outcome = oc; a.feature = '260813-outcome-union'\n"
            "    a.format = 'text'\n"
            "    assert f.cmd_fleet_emit(a) == 0\n"
        )
        p = subprocess.run([sys.executable, "-c", code, SCRIPTS_DIR] + self.emitted,
                           env=_clean_org_env(self.org), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout)
        shards = glob.glob(os.path.join(self.kdir, "fleet", "*.jsonl"))
        self.assertEqual(len(shards), 1, "expected one per-user shard: %s" % shards)
        self.shard = shards[0]

    def _gate(self, phase="pre-hook", drop=None):
        """Source the SHIPPED orglint.sh and run its gate against the real linter.

        The stub `scripts/agentware` serves only `org lint` (and the `--help`
        availability probe the gate memoises); everything else exits 2, so a gate
        that called the wrong thing fails loudly instead of passing.
        """
        d = tempfile.mkdtemp(prefix="aw-outcome-gate-")
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "scripts"))
        runner = os.path.join(d, "runner.py")
        with open(runner, "w", encoding="utf-8") as fh:
            fh.write(
                "import json, os, sys\n"
                "sys.path.insert(0, %r)\n"
                "import agentware_lib.ops.fleet as f, agentware_lib.ops.orglint as o\n"
                "drop = os.environ.get('AW_DROP_OUTCOME')\n"
                "if drop:\n"
                "    f.LOOP_OUTCOME_ENUM = tuple(x for x in f.LOOP_OUTCOME_ENUM"
                " if x != drop)\n"
                "r = o.lint_org(%r)\n"
                "print(json.dumps(r['errors']))\n"
                "sys.exit(0 if r['ok'] else 1)\n" % (SCRIPTS_DIR, self.kdir))
        stub = os.path.join(d, "scripts", "agentware")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write("#!/usr/bin/env bash\n"
                     'for a in "$@"; do [[ "$a" == "--help" ]] && exit 0; done\n'
                     '[[ "$1" == "org" && "$2" == "lint" ]] || exit 2\n'
                     'exec %s %s\n' % (sys.executable, runner))
        os.chmod(stub, 0o755)
        env = _clean_org_env(self.org)
        env.pop("AGENTWARE_DISABLE_ORG_LINT", None)
        if drop:
            env["AW_DROP_OUTCOME"] = drop
        script = ('set -e\nlog() { echo "[log] $1"; }\nsource "%s"\n'
                  'run_org_lint_gate %s\necho PROCEEDED\n' % (ORGLINT_SH, phase))
        p = subprocess.run(["bash", "-c", script], cwd=d, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=120)
        return p.returncode, p.stdout

    def test_the_sink_carries_every_outcome_the_loop_can_emit(self):
        with open(self.shard, encoding="utf-8") as fh:
            got = {json.loads(l)["outcome"] for l in fh if l.strip()}
        self.assertEqual(got, set(self.emitted))
        for outcome in LOCAL_ONLY_OUTCOMES:
            self.assertIn(outcome, got)

    def test_pre_hook_gate_proceeds_over_a_sink_holding_each_outcome(self):
        rc, out = self._gate("pre-hook")
        self.assertEqual(rc, 0, out)
        self.assertIn("PROCEEDED", out, out)

    def test_post_hook_gate_proceeds_over_the_same_sink(self):
        # Both call sites read the same enum; a run that emitted a terminal must
        # not block its own post-hook either.
        rc, out = self._gate("post-hook")
        self.assertEqual(rc, 0, out)
        self.assertIn("PROCEEDED", out, out)

    def test_dropping_either_local_outcome_hard_blocks_the_pre_hook(self):
        for outcome in LOCAL_ONLY_OUTCOMES:
            with self.subTest(outcome=outcome):
                rc, out = self._gate("pre-hook", drop=outcome)
                self.assertEqual(rc, 1, "dropping %s must abort the pre-hook, "
                                        "got rc=0\n%s" % (outcome, out))
                self.assertNotIn("PROCEEDED", out)
                self.assertIn("org lint failed", out)

    def test_a_second_run_is_blocked_too_because_the_sink_is_append_only(self):
        # Why this is a HARD block and not a one-off failure: the offending event
        # stays in the append-only sink, so the NEXT run's pre-hook re-reads it and
        # aborts before spawning anything. Proven by gating twice.
        first = self._gate("pre-hook", drop="premise_falsified")[0]
        second = self._gate("pre-hook", drop="premise_falsified")[0]
        self.assertEqual((first, second), (1, 1))


if __name__ == "__main__":
    unittest.main()
