"""Done-stamp un-swallow tests (feature 260711-emitter-gate-hardening, T4).

The loop's terminal `running->done` stamp used to be swallowed by `|| true`: a
REFUSED stamp vanished silently. Now the loop WARNs loudly and appends a
`done_stamp_failed` event to metrics.jsonl (through the EXISTING channel), with
NO integer `iteration` field so `derive_iteration_costs` never folds it into a
phantom iteration-0 record. The run STAYS honestly successful.

This drives the REAL `agentware.sh` end-to-end against a SYNTHETIC throwaway KB
(the operator KB is NEVER touched, R-LOC-03) with a FAKE agent runtime that
signals completion (`.loop/.done`) while deliberately leaving ONE task marker
OPEN — so the final `running->done` stamp is REFUSED by the open-markers gate.

Asserts: the loop exits 0 (success); exactly one `done_stamp_failed` event is
appended and it carries NO `iteration`; `derive_iteration_costs` folds no phantom
record; the plan stays `running` (the stamp genuinely did not apply); and
`agentware metrics` still parses the channel.

`test_donestamp_` is unique vs every existing module filename and method.
Runner: python3 -m unittest tests.integration.test_donestamp -v
"""

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import REPO_ROOT, build_synthetic_kb, load_cli
except ImportError:
    from _fixtures import REPO_ROOT, build_synthetic_kb, load_cli

AGENTWARE_SH = os.path.join(REPO_ROOT, "agentware.sh")
FEATURE = "260711-donestamp-test"

# A plan that STARTS ready (so the loop's ready->running claim succeeds) and is
# lint-clean (the pre-hook runs `plan lint --strict` regardless of --skip-pre, so
# it needs the [e2e]+[kb] pair and a Target-packages section). The fake agent
# flips only ONE marker, so markers stay OPEN at completion -> the done-stamp is
# refused by the running->done open-markers gate.
PLAN_MD = """# Plan — done-stamp refusal test

> Status: ready

## Target packages

- ~/tmp/synthetic-donestamp-kb

## Tasks

- ⬜ **1** First synthetic task.
  *Verify:* trivial echo check passes.
- ⬜ **2** Second synthetic task (left OPEN on purpose).
  *Verify:* trivial echo check passes.
- ⬜ **3** [e2e] End-to-end synthetic check.
  *Verify:* run the synthetic flow end to end and observe success.
- ⬜ **4** [kb] Knowledge-base update.
  *Verify:* scripts/agentware index validate exits 0.

## Acceptance criteria

- [ ] all synthetic tasks done

<promise>TASK_COMPLETE</promise>
"""

# Fake agent. On the MAIN prompt it: flips exactly ONE open marker (leaving one
# open), writes a non-empty worklog (so the zero-knowledge-loss gate passes), and
# writes .loop/.done to force completion DESPITE the remaining open marker. Pre/
# post prompts just echo their promises.
FAKE_CLI = r"""#!/usr/bin/env bash
if printf '%s' "$*" | grep -q "find the next task marked"; then
  DOCS="$(dirname "$FAKE_PLAN")"
  python3 - "$FAKE_PLAN" <<'PY'
import re, sys
p = sys.argv[1]
with open(p, encoding="utf-8") as f:
    s = f.read()
s = re.sub(r"⬜|🟡", "✅", s, count=1)   # flip exactly ONE
with open(p, "w", encoding="utf-8") as f:
    f.write(s)
PY
  printf 'worklog: synthetic task complete; nothing to promote.\n' > "$DOCS/worklog.md"
  mkdir -p "$DOCS/.loop"
  : > "$DOCS/.loop/.done"
fi
echo "<promise>PRE_TASK_COMPLETE</promise>"
echo "<promise>TASK_COMPLETE</promise>"
echo "<promise>POST_COMPLETE</promise>"
"""


def _have(binary):
    return shutil.which(binary) is not None


@unittest.skipUnless(_have("jq"), "jq (a hard agentware.sh preflight dep) is required")
@unittest.skipUnless(_have("bash"), "bash is required to run agentware.sh")
class DoneStampTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-donestamp-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tmp_home = os.path.join(self.tmp, "home")
        os.makedirs(self.tmp_home)
        # The pre-hook's target-packages existence gate requires the plan's
        # declared '## Target packages' dir to exist; ~ resolves to this test HOME.
        os.makedirs(os.path.join(self.tmp_home, "tmp", "synthetic-donestamp-kb"))
        self.bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin_dir)
        self.kdir = os.path.join(self.tmp, "kb")
        # A valid, INITIALIZED synthetic KB so the loop's claim + state machine +
        # post-hooks engage (they no-op on an uninitialized KB).
        build_synthetic_kb(self.kdir)
        with open(os.path.join(self.kdir, ".initialized"), "w",
                  encoding="utf-8") as f:
            f.write("test\n")
        self.docs = os.path.join(self.kdir, "work", FEATURE)
        os.makedirs(self.docs)
        self.plan = os.path.join(self.docs, "plan.md")
        with open(self.plan, "w", encoding="utf-8") as f:
            f.write(PLAN_MD)
        self.fake_cli = os.path.join(self.bin_dir, "claude")
        with open(self.fake_cli, "w", encoding="utf-8") as f:
            f.write(FAKE_CLI)
        os.chmod(self.fake_cli, os.stat(self.fake_cli).st_mode
                 | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        self.metrics = os.path.join(self.kdir, "logs", "metrics.jsonl")

    def _env(self):
        env = dict(os.environ)
        env.update({
            "HOME": self.tmp_home,
            "AGENTWARE_KNOWLEDGE_DIR": self.kdir,
            "AGENTWARE_CLI": "claude",
            "AGENTWARE_KB_AUTOCOMMIT": "0",
            "AGENTWARE_KB_PUSH": "0",
            "AGENTWARE_NO_STREAM": "1",
            # Isolate from package-diff release-gate flakiness: the done-stamp is
            # orthogonal to the gate, and running from the repo makes the post-hook
            # gate fire on this working tree's diff.
            "AGENTWARE_DISABLE_RELEASE_GATE": "1",
            # The self-healing review gate fires on the SAME self-extension predicate
            # (which sees THIS repo's own dirty tree). A loop test that only mutates
            # the temp KB is NOT a package self-extension, so declare a non-package
            # changed-file list and the review gate skips cleanly (no reviewer spawn).
            "AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE": "work/x/plan.md docs/readme.md",
            "FAKE_PLAN": self.plan,
            "PATH": self.bin_dir + os.pathsep + os.environ.get("PATH", ""),
        })
        return env

    def _run(self):
        return subprocess.run(
            ["bash", AGENTWARE_SH, FEATURE, "--skip-pre", "--skip-post"],
            cwd=REPO_ROOT, env=self._env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=180,
        )

    def _events(self):
        events = []
        with open(self.metrics, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))  # the json.loads parse gate
        return events

    def test_donestamp_refusal_emits_event_and_stays_successful(self):
        proc = self._run()
        self.assertEqual(
            proc.returncode, 0,
            "loop must stay SUCCESSFUL despite the refused done-stamp\n"
            "--- loop output ---\n%s" % proc.stdout)

        self.assertTrue(os.path.exists(self.metrics),
                        "metrics.jsonl not created\n%s" % proc.stdout)
        events = self._events()

        # Exactly one done_stamp_failed event, carrying NO integer iteration.
        ds = [e for e in events if e.get("event") == "done_stamp_failed"]
        self.assertEqual(len(ds), 1,
                         "expected exactly one done_stamp_failed event\n%s"
                         % proc.stdout)
        self.assertNotIn("iteration", ds[0],
                         "done_stamp_failed must carry NO iteration field")
        self.assertEqual(ds[0].get("feature"), FEATURE)

        # derive_iteration_costs must NOT fold a phantom iteration for it.
        mod = load_cli()
        derived = mod.derive_iteration_costs(events)
        self.assertTrue(
            all(isinstance(r["iteration"], int) and r["iteration"] >= 1
                for r in derived),
            "a phantom iteration (<1) leaked into per-iteration costs")

        # Read-after-write: the stamp genuinely did NOT apply -> still running.
        with open(self.plan, encoding="utf-8") as f:
            plan_text = f.read()
        self.assertIn("> Status: running", plan_text,
                      "plan must remain running (done-stamp was refused)")
        self.assertNotIn("> Status: done", plan_text)

    def test_donestamp_metrics_reader_tolerates_event(self):
        self._run()
        events = self._events()  # json.loads per line -> raises on a bad line
        self.assertTrue(
            any(e.get("event") == "done_stamp_failed" for e in events),
            "done_stamp_failed event must be present in the channel")
        # The per-iteration derivation must not choke on the new event type.
        mod = load_cli()
        self.assertIsInstance(mod.derive_iteration_costs(events), list)
        # `agentware metrics` must not CRASH on the channel. A synthetic KB with no
        # session transcripts exits non-zero GRACEFULLY ("no matching sessions") —
        # that is not a parse error, so we assert only the absence of a traceback.
        proc = subprocess.run(
            [os.path.join(REPO_ROOT, "scripts", "agentware"), "metrics"],
            cwd=REPO_ROOT, env=self._env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=60)
        self.assertNotIn("Traceback", proc.stdout,
                         "metrics reader crashed on the channel:\n%s" % proc.stdout)


if __name__ == "__main__":
    unittest.main()
