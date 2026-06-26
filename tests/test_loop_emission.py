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

Runner:  python3 -m unittest tests.test_loop_emission -v
"""

import json
import os
import shutil
import stat
import subprocess
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
        self.kdir = os.path.join(self.tmp, "kb")
        self.docs = os.path.join(self.kdir, "work", FEATURE)
        os.makedirs(self.docs)
        self.plan = os.path.join(self.docs, "plan.md")
        with open(self.plan, "w", encoding="utf-8") as f:
            f.write(PLAN_MD)
        # Deliberately do NOT create <kdir>/.initialized: keeping the workspace
        # "uninitialized" makes the pre/post toolkit gates no-op cleanly so the
        # test exercises ONLY the loop + emitter, never the index/KB machinery.
        self.fake_cli = os.path.join(self.tmp, "fake-claude")
        with open(self.fake_cli, "w", encoding="utf-8") as f:
            f.write(FAKE_CLI)
        os.chmod(self.fake_cli, os.stat(self.fake_cli).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        self.metrics = os.path.join(self.kdir, "logs", "metrics.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _env(self, **overrides):
        env = dict(os.environ)
        env.update({
            "AGENTWARE_KNOWLEDGE_DIR": self.kdir,
            "AGENTWARE_CLI": self.fake_cli,
            "AGENTWARE_KB_AUTOCOMMIT": "0",   # no git side effects in the test
            "AGENTWARE_NO_STREAM": "1",       # no background tail follower
            "FAKE_PLAN": self.plan,
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

    def test_opt_out_emits_nothing(self):
        proc = self._run(self._env(AGENTWARE_METRICS_EMIT="0"))
        self.assertFalse(
            os.path.exists(self.metrics) and os.path.getsize(self.metrics) > 0,
            "AGENTWARE_METRICS_EMIT=0 must append NO metrics line\n"
            "--- loop output ---\n%s" % proc.stdout,
        )


if __name__ == "__main__":
    unittest.main()
