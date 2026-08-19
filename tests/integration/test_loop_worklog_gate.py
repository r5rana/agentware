"""Worklog-presence gate — end-to-end loop test
(feature 260710-learning-repair-p1 task 4).

Drives the REAL agentware.sh against a SYNTHETIC, INITIALIZED throwaway knowledge
dir and a FAKE agent runtime, proving the Task-4 gate:

  * a loop that RAN a task but wrote NO worklog.md FAILS loudly (the historical
    silent no-op PASS is how capture collapsed unnoticed); the distinctive
    'worklog.md is missing or empty' reason is surfaced.
  * a loop that writes a non-empty worklog COMPLETES (exit 0).

The operator KB is never touched (R-LOC-03): HOME + AGENTWARE_KNOWLEDGE_DIR are
patched to temp dirs and the other package gates are disabled so the test
exercises ONLY the worklog-presence gate.

Runner:  python3 -m unittest tests.integration.test_loop_worklog_gate -v
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import REPO_ROOT, build_synthetic_kb, run_cli
except ImportError:  # discover -s tests puts tests/ on sys.path
    from _fixtures import REPO_ROOT, build_synthetic_kb, run_cli

AGENTWARE_SH = os.path.join(REPO_ROOT, "agentware.sh")
FEATURE = "260710-worklog-gate-test"

# Lint-passing plan (mirrors tests/test_plan_lint.GOOD_PLAN): monotonic
# `- ⬜ **N**` markers, a *Verify:* per task, an R7 KB token, one [e2e] task,
# exactly one <promise>, no banned phrases.
PLAN_MD = """\
# Plan — worklog gate test

> Synthetic plan for the worklog-presence gate loop test.

## Target packages

- /tmp/aw-worklog-gate-target

## Tasks

- ⬜ **1** Implement the first synthetic thing deterministically.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** Update the knowledge base after building (`R-KB-*`).
  Run `scripts/agentware learn` then `scripts/agentware index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **3** [e2e] Run the end-to-end regression against the real loop.
  *Verify:* `./agentware.sh worklog-gate` aborts/passes as expected.

## Acceptance criteria

- [ ] both tasks done

<promise>TASK_COMPLETE</promise>
"""

# Both fakes flip exactly one open marker per MAIN spawn (prompt contains "find
# the next task marked") and echo each phase's completion promise. They differ
# only in whether the main spawn also appends a worklog line.
_FLIP = r"""#!/usr/bin/env bash
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
__WORKLOG_WRITE__
fi
echo "<promise>PRE_TASK_COMPLETE</promise>"
echo "<promise>TASK_COMPLETE</promise>"
echo "<promise>POST_COMPLETE</promise>"
"""

FAKE_CLI_NO_WORKLOG = _FLIP.replace("__WORKLOG_WRITE__", "")
FAKE_CLI_WITH_WORKLOG = _FLIP.replace(
    "__WORKLOG_WRITE__",
    "  printf -- '- iteration: did synthetic task, verified.\\n' >> \"$FAKE_WORKLOG\"")


def _have(binary):
    return shutil.which(binary) is not None


@unittest.skipUnless(_have("jq"), "jq (an agentware.sh preflight dep) is required")
@unittest.skipUnless(_have("bash"), "bash is required to run agentware.sh")
class WorklogGateLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-wlgate-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tmp_home = os.path.join(self.tmp, "home")
        os.makedirs(self.tmp_home)
        self.bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin_dir)
        self.kdir = os.path.join(self.tmp, "kb")
        os.makedirs(self.kdir)
        # Build + initialize a valid KB so the pre/post toolkit gates run for real.
        build_synthetic_kb(self.kdir)
        run_cli(["index", "migrate-frontmatter"], self.kdir)
        run_cli(["index", "rebuild"], self.kdir)
        with open(os.path.join(self.kdir, "MAIN.md"), "w", encoding="utf-8") as f:
            f.write("# KB\n\n- **Handle**: tester\n")
        with open(os.path.join(self.kdir, ".initialized"), "w", encoding="utf-8") as f:
            f.write("2026-07-10T00:00:00Z tester\n")
        self.docs = os.path.join(self.kdir, "work", FEATURE)
        os.makedirs(self.docs)
        self.plan = os.path.join(self.docs, "plan.md")
        with open(self.plan, "w", encoding="utf-8") as f:
            f.write(PLAN_MD)
        self.worklog = os.path.join(self.docs, "worklog.md")
        self.fake_cli = os.path.join(self.bin_dir, "claude")

    def _install_cli(self, body):
        with open(self.fake_cli, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(self.fake_cli, os.stat(self.fake_cli).st_mode
                 | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def _env(self):
        env = dict(os.environ)
        env.update({
            "HOME": self.tmp_home,
            "AGENTWARE_KNOWLEDGE_DIR": self.kdir,
            "AGENTWARE_CLI": "claude",
            "AGENTWARE_KB_AUTOCOMMIT": "0",
            "AGENTWARE_KB_PUSH": "0",
            "AGENTWARE_NO_STREAM": "1",
            "AGENTWARE_METRICS_EMIT": "0",
            "AGENTWARE_DISABLE_RELEASE_GATE": "1",   # our own edits show dirty
            # Same reason for the self-healing review gate (same self-extension
            # predicate): a temp-KB-only loop run is NOT a package self-extension, so
            # declare a non-package changed-file list -> the review gate skips cleanly.
            "AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE": "work/x/plan.md docs/readme.md",
            "AGENTWARE_DISABLE_TARGET_GATE": "1",    # target path need not exist
            "FAKE_PLAN": self.plan,
            "FAKE_WORKLOG": self.worklog,
            "PATH": self.bin_dir + os.pathsep + os.environ.get("PATH", ""),
        })
        return env

    def _run(self):
        return subprocess.run(
            ["bash", AGENTWARE_SH, FEATURE], cwd=REPO_ROOT, env=self._env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=240)

    def test_missing_worklog_after_real_run_fails_loud(self):
        self._install_cli(FAKE_CLI_NO_WORKLOG)
        proc = self._run()
        self.assertNotEqual(proc.returncode, 0,
                            "a real run with no worklog must fail:\n%s" % proc.stdout)
        self.assertIn("worklog.md is missing or empty", proc.stdout, proc.stdout)
        self.assertFalse(os.path.isfile(self.worklog))

    def test_present_nonempty_worklog_completes(self):
        self._install_cli(FAKE_CLI_WITH_WORKLOG)
        proc = self._run()
        self.assertEqual(proc.returncode, 0,
                         "a run that wrote a worklog must complete:\n%s" % proc.stdout)
        self.assertTrue(os.path.isfile(self.worklog))
        self.assertGreater(os.path.getsize(self.worklog), 0)


if __name__ == "__main__":
    unittest.main()
