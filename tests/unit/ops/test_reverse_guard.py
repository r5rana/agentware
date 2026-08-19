"""Reverse loop-guard integration tests (feature 260712-p2b-hardening-followups,
Task 3 — audit C3/C6/C7; drives the REAL agentware.sh).

The agentware.sh RUN-BEGINS reverse-guard refuses to start a loop while a LIVE
updater/tick apply holds the lock, so a loop never reads a HALF-updated package.
These tests drive the actual bash guard against an on-disk updater lock (visible
cross-process) planted under a synthetic KB:

  HELD  — a live-pid updater lock => the loop refuses (exit 1, guard message, and
          a `pre_hook_abort` terminal LOOP_OUTCOME) (C6, the plan-claim shape).
  STALE — a dead-pid updater lock => a one-shot self-heal runs and the loop then
          PROCEEDS past the guard (no refusal) (C3 stale branch).
  FREE  — no lock => the loop proceeds normally (no guard refusal).

The C3 no-lock-recheck / C7 bounded-retry logic and the pid-alive-never-stale lock
semantics are unit-covered in test_pkg_state_lock; this module is the bash-side
integration proof. Hermetic: throwaway HOME + synthetic KB (R-LOC-03).

`test_reverseguard_` is a unique -k prefix vs every module/method in the suite.
Runner: python3 -m unittest tests.unit.ops.test_reverse_guard -v
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
FEATURE = "260712-reverseguard-test"

PLAN_MD = """# Plan — reverse-guard integration test

> Status: ready

## Target packages

- ~/tmp/synthetic-reverseguard-kb

## Tasks

- ⬜ **1** First synthetic task.
  *Verify:* trivial echo check passes.
- ⬜ **2** [e2e] End-to-end synthetic check.
  *Verify:* run the synthetic flow end to end and observe success.
- ⬜ **3** [kb] Knowledge-base update.
  *Verify:* scripts/agentware index validate exits 0.

## Acceptance criteria

- [ ] all synthetic tasks done

<promise>TASK_COMPLETE</promise>
"""

# Fake agent: flips ALL open markers to done, writes a worklog, and signals
# completion — so a loop that PROCEEDS past the guard runs to a clean finish.
FAKE_CLI = r"""#!/usr/bin/env bash
if printf '%s' "$*" | grep -q "find the next task marked"; then
  DOCS="$(dirname "$FAKE_PLAN")"
  python3 - "$FAKE_PLAN" <<'PY'
import re, sys
p = sys.argv[1]
with open(p, encoding="utf-8") as f:
    s = f.read()
s = s.replace("⬜", "✅").replace("\U0001f7e1", "✅")
with open(p, "w", encoding="utf-8") as f:
    f.write(s)
PY
  printf 'worklog: synthetic tasks complete; nothing to promote.\n' > "$DOCS/worklog.md"
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
@unittest.skipIf(os.name != "posix", "POSIX lock semantics only")
class ReverseGuardTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.tmp = tempfile.mkdtemp(prefix="agentware-reverseguard-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tmp_home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.tmp_home, "tmp", "synthetic-reverseguard-kb"))
        self.bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin_dir)
        self.kdir = os.path.join(self.tmp, "kb")
        build_synthetic_kb(self.kdir)
        with open(os.path.join(self.kdir, ".initialized"), "w", encoding="utf-8") as f:
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

    def _plant_lock(self, pid):
        """Plant an updater lock on disk (cross-process visible) with `pid`."""
        self.assertTrue(self.mod.dream_acquire_lock(
            self.kdir, pid=pid, lock_rel=self.mod.UPDATER_LOCK_REL))

    def _env(self):
        env = dict(os.environ)
        env.update({
            "HOME": self.tmp_home,
            "AGENTWARE_KNOWLEDGE_DIR": self.kdir,
            "AGENTWARE_CLI": "claude",
            "AGENTWARE_KB_AUTOCOMMIT": "0",
            "AGENTWARE_KB_PUSH": "0",
            "AGENTWARE_NO_STREAM": "1",
            "AGENTWARE_DISABLE_RELEASE_GATE": "1",
            # The self-healing review gate fires on the SAME self-extension predicate
            # (which sees THIS repo's own dirty tree). A loop test that only mutates
            # the temp KB is NOT a package self-extension, so declare a non-package
            # changed-file list and the review gate skips cleanly (no reviewer spawn).
            "AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE": "work/x/plan.md docs/readme.md",
            "AGENTWARE_UPDATE": "0",           # keep the tick/updater inert
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

    def _terminal_outcome(self):
        if not os.path.exists(self.metrics):
            return None
        outcome = None
        with open(self.metrics, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("event") == "terminal" and e.get("outcome"):
                    outcome = e["outcome"]
        return outcome

    # --- HELD: live lock => refuse (C6) ------------------------------------
    def test_reverseguard_held_lock_refuses_loop(self):
        self._plant_lock(pid=1)                 # pid 1 (init) is always live -> held
        cp = self._run()
        self.assertNotEqual(cp.returncode, 0, cp.stdout)
        self.assertEqual(cp.returncode, 1, cp.stdout)
        self.assertIn("[guard]", cp.stdout)
        self.assertIn("refusing to start a loop", cp.stdout)
        # the plan must NOT have been claimed to running (guard aborted first)
        self.assertEqual(self._terminal_outcome(), "pre_hook_abort", cp.stdout)

    # --- STALE: dead-pid lock => self-heal, then proceed (C3 stale) ---------
    def test_reverseguard_stale_lock_self_heals_and_proceeds(self):
        self._plant_lock(pid=424242)            # dead pid -> stale
        cp = self._run()
        self.assertIn("self-heal", cp.stdout.lower(), cp.stdout)
        # it PROCEEDED past the guard (never emitted the refusal)
        self.assertNotIn("refusing to start a loop", cp.stdout)
        self.assertEqual(cp.returncode, 0, cp.stdout)

    # --- FREE: no lock => proceed normally ---------------------------------
    def test_reverseguard_free_lock_proceeds(self):
        cp = self._run()
        self.assertNotIn("refusing to start a loop", cp.stdout)
        self.assertEqual(cp.returncode, 0, cp.stdout)


if __name__ == "__main__":
    unittest.main()
