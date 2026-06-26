"""Tests for agentware.sh per-task transition + terminal outcome emission
(feature 260626-observability-suite, Task 7).

Beyond the Task-6 per-iteration emission, Task 7 makes individual TASK lifecycle
and the run's terminal outcome observable on the SAME append-only channel
(`<kdir>/logs/metrics.jsonl`):

  - one `task_transition` event per task whose plan.md marker state CHANGED across
    an iteration (⬜->🟡 start, 🟡->✅ / ⬜->✅ complete), each carrying
    {event, ts, feature, stage, iteration, task, from, to, approx}; and
  - one `terminal` event on loop exit, carrying {event, ts, feature, outcome,
    iterations_used, max, self_heal_count, tasks_total, tasks_done, promise_status}.

Both new event types carry an `event` discriminator so the Task-6 per-iteration
consumer (`derive_iteration_costs`) skips them — the per-iteration emission is
unchanged (additive). These tests drive the REAL `agentware.sh` end-to-end against
a SYNTHETIC throwaway knowledge dir + a FAKE agent runtime (operator KB NEVER
touched — R-LOC-03).

Runner:  python3 -m unittest tests.test_loop_transitions -v
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
FEATURE = "260626-loop-transitions-test"

PLAN_MD = """# Plan — loop transition test

## Tasks

- ⬜ **1** First synthetic task.
  *Verify:* trivial.
- ⬜ **2** Second synthetic task.
  *Verify:* trivial.

## Acceptance criteria

- [ ] both tasks done

<promise>TASK_COMPLETE</promise>
"""

# A FAKE agent runtime that, on a MAIN prompt only, advances the FIRST not-done
# task by ONE step: ⬜->🟡 (start) if any ⬜ remains, else 🟡->✅ (complete) the
# first 🟡. This yields distinct open->started then started->done transitions per
# task (never an ⬜->✅ jump), so a two-step lifecycle is observable.
FAKE_CLI = r"""#!/usr/bin/env bash
if printf '%s' "$*" | grep -q "find the next task marked"; then
  python3 - "$FAKE_PLAN" <<'PY'
import re, sys
p = sys.argv[1]
with open(p, encoding="utf-8") as f:
    s = f.read()
if "⬜" in s:          # any open task -> start the first one
    s = s.replace("⬜", "\U0001f7e1", 1)
elif "\U0001f7e1" in s:    # else complete the first started task
    s = s.replace("\U0001f7e1", "✅", 1)
with open(p, "w", encoding="utf-8") as f:
    f.write(s)
PY
fi
echo "<promise>PRE_TASK_COMPLETE</promise>"
echo "<promise>TASK_COMPLETE</promise>"
echo "<promise>POST_COMPLETE</promise>"
"""

# A FAKE runtime that NEVER mutates the plan (so the loop never completes) — used
# to exercise the hit_max_iterations terminal outcome.
FAKE_CLI_NOOP = r"""#!/usr/bin/env bash
echo "<promise>PRE_TASK_COMPLETE</promise>"
echo "working, but never flipping a marker"
"""

TRANSITION_KEYS = {"event", "ts", "feature", "stage", "iteration", "task",
                   "from", "to", "approx"}
TERMINAL_KEYS = {"event", "ts", "feature", "outcome", "iterations_used", "max",
                 "self_heal_count", "tasks_total", "tasks_done", "promise_status"}


def _have(binary):
    return shutil.which(binary) is not None


@unittest.skipUnless(_have("jq"), "jq (a hard agentware.sh preflight dep) is required")
@unittest.skipUnless(_have("bash"), "bash is required to run agentware.sh")
class LoopTransitionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-trans-")
        self.kdir = os.path.join(self.tmp, "kb")
        self.docs = os.path.join(self.kdir, "work", FEATURE)
        os.makedirs(self.docs)
        self.plan = os.path.join(self.docs, "plan.md")
        with open(self.plan, "w", encoding="utf-8") as f:
            f.write(PLAN_MD)
        # Uninitialized workspace: pre/post toolkit gates no-op cleanly so the test
        # exercises only the loop + emitter, never the index/KB machinery.
        self.metrics = os.path.join(self.kdir, "logs", "metrics.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_cli(self, body):
        path = os.path.join(self.tmp, "fake-claude")
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return path

    def _env(self, cli, **overrides):
        env = dict(os.environ)
        env.update({
            "AGENTWARE_KNOWLEDGE_DIR": self.kdir,
            "AGENTWARE_CLI": cli,
            "AGENTWARE_KB_AUTOCOMMIT": "0",
            "AGENTWARE_NO_STREAM": "1",
            "FAKE_PLAN": self.plan,
        })
        env.update(overrides)
        return env

    def _run(self, env, *args):
        return subprocess.run(
            ["bash", AGENTWARE_SH, FEATURE, *args],
            cwd=REPO_ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=300,
        )

    def _read_events(self):
        events = []
        with open(self.metrics, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                events.append(json.loads(line))  # the `import json` parse gate
        return events

    def test_task_transitions_and_completed_terminal(self):
        cli = self._write_cli(FAKE_CLI)
        proc = self._run(self._env(cli))
        self.assertTrue(os.path.exists(self.metrics),
                        "metrics.jsonl not created\n--- output ---\n%s" % proc.stdout)
        events = self._read_events()

        transitions = [e for e in events if e.get("event") == "task_transition"]
        self.assertTrue(transitions, "no task_transition events emitted\n%s" % proc.stdout)
        for ev in transitions:
            self.assertTrue(TRANSITION_KEYS.issubset(ev.keys()),
                            "missing %s in %s" % (TRANSITION_KEYS - set(ev.keys()), ev))
            self.assertEqual(ev["feature"], FEATURE)
            self.assertIsInstance(ev["iteration"], int)
            self.assertIn(ev["from"], {"open", "started", "done"})
            self.assertIn(ev["to"], {"open", "started", "done"})
            self.assertIsInstance(ev["approx"], bool)
            self.assertEqual(ev["gen_ai.system"], "agentware")

        # Task "1" walks ⬜->🟡 then 🟡->✅ across two iterations: exactly two
        # transitions, in order, with monotonic (non-decreasing) timestamps and
        # NOT flagged approx (the start was observed, so it is not a jump).
        t1 = [e for e in transitions if e["task"] == "1"]
        self.assertEqual([(e["from"], e["to"]) for e in t1],
                         [("open", "started"), ("started", "done")],
                         "task 1 must transition open->started->done\n%s" % t1)
        self.assertLessEqual(t1[0]["ts"], t1[1]["ts"], "transition ts must be monotonic")
        self.assertFalse(any(e["approx"] for e in t1), "stepwise flips are not approx")
        # Task "2" likewise completes (both tasks burn down).
        t2 = [e for e in transitions if e["task"] == "2"]
        self.assertEqual([(e["from"], e["to"]) for e in t2],
                         [("open", "started"), ("started", "done")])

        # Exactly one terminal event, outcome=completed, with the full schema.
        terminals = [e for e in events if e.get("event") == "terminal"]
        self.assertEqual(len(terminals), 1, "expected exactly one terminal event\n%s" % terminals)
        term = terminals[0]
        self.assertTrue(TERMINAL_KEYS.issubset(term.keys()),
                        "missing %s in %s" % (TERMINAL_KEYS - set(term.keys()), term))
        self.assertEqual(term["outcome"], "completed")
        self.assertEqual(term["feature"], FEATURE)
        self.assertEqual(term["tasks_total"], 2)
        self.assertEqual(term["tasks_done"], 2)
        self.assertEqual(term["promise_status"], "signalled")
        self.assertGreaterEqual(term["iterations_used"], 1)
        self.assertEqual(term["max"], 100)

        # ADDITIVE: the per-iteration (Task-6) emission is unchanged — its events
        # carry NO `event` discriminator and still drive the iteration series.
        per_iter = [e for e in events if e.get("event") is None and e.get("phase") == "main"]
        self.assertTrue(per_iter, "per-iteration main events must still be emitted")
        self.assertEqual(sum(e["tasks_done_delta"] for e in per_iter), 2)

    def test_hit_max_iterations_terminal(self):
        cli = self._write_cli(FAKE_CLI_NOOP)
        proc = self._run(self._env(cli), "--max-iterations", "1")
        self.assertNotEqual(proc.returncode, 0,
                            "a non-completing run must exit non-zero")
        events = self._read_events()
        terminals = [e for e in events if e.get("event") == "terminal"]
        self.assertEqual(len(terminals), 1, "expected one terminal event\n%s" % proc.stdout)
        self.assertEqual(terminals[0]["outcome"], "hit_max_iterations")
        self.assertEqual(terminals[0]["iterations_used"], 1)
        self.assertEqual(terminals[0]["max"], 1)
        self.assertEqual(terminals[0]["promise_status"], "pending")


if __name__ == "__main__":
    unittest.main()
