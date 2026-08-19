"""Post-update smoke-test contract tests (feature 260712, Task 4).

Covers the fast, exit-code-gated smoke: PASS on a healthy KB; FAIL on a syntax-
broken CLI (short-circuit, no exec of later steps); FAIL when any liveness step
exits non-zero; FAIL (not hang) on a hard timeout; and — the audit-hardening
requirement — ZERO writes to benchmarks/history.jsonl + SCORECARD.md against a
gold-PRESENT fixture (proving it does NOT run the ledger-writing audit tier).

Runner: python3 -m unittest tests.e2e.test_pkg_smoke -v
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

try:
    from tests._fixtures import load_cli, build_synthetic_kb, run_cli
except ImportError:
    from _fixtures import load_cli, build_synthetic_kb, run_cli


class SmokeTestCase(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="agentware-smoke-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        # Gold-PRESENT fixture: a ledger + scorecard the smoke must NOT touch.
        self.ledger = os.path.join(self.kdir, "benchmarks", "history.jsonl")
        self.scorecard = os.path.join(self.kdir, "SCORECARD.md")
        os.makedirs(os.path.dirname(self.ledger), exist_ok=True)
        with open(self.ledger, "w", encoding="utf-8") as f:
            f.write('{"ts":"2026-01-01T00:00:00Z","sentinel":"GOLD"}\n')
        with open(self.scorecard, "w", encoding="utf-8") as f:
            f.write("# Scorecard\nSENTINEL-GOLD\n")

    def _snap(self, path):
        with open(path, "rb") as f:
            return f.read()

    # --- happy path ---------------------------------------------------------
    def test_smoke_pass_on_healthy_kb(self):
        r = self.mod._pkg_smoke(self.kdir, timeout=120)
        self.assertTrue(r["ok"], r)
        names = {s["name"] for s in r["steps"]}
        self.assertIn("syntax", names)
        self.assertIn("steering-lint", names)
        self.assertIn("index-validate", names)
        self.assertIn("canary-recall", names)
        for s in r["steps"]:
            self.assertEqual(s["rc"], 0, s)

    def test_smoke_writes_no_ledger_or_scorecard(self):
        before_l, before_s = self._snap(self.ledger), self._snap(self.scorecard)
        r = self.mod._pkg_smoke(self.kdir, timeout=120)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self._snap(self.ledger), before_l,
                         "smoke must NOT append to benchmarks/history.jsonl")
        self.assertEqual(self._snap(self.scorecard), before_s,
                         "smoke must NOT rewrite SCORECARD.md")

    # --- failure paths ------------------------------------------------------
    def test_syntax_failure_short_circuits_and_fails(self):
        with mock.patch.object(self.mod, "_pkg_syntax_check",
                               return_value="SyntaxError: boom"):
            r = self.mod._pkg_smoke(self.kdir, timeout=120)
        self.assertFalse(r["ok"])
        # Short-circuit: only the syntax step ran (no exec of a broken CLI).
        self.assertEqual([s["name"] for s in r["steps"]], ["syntax"])
        self.assertIn("syntax", r["detail"])

    def test_step_nonzero_fails_smoke(self):
        real = self.mod._pkg_run_bounded

        def fake(cmd, cwd, kdir, timeout):
            if "validate" in cmd:
                return 1, "index invalid"
            return real(cmd, cwd, kdir, timeout)

        with mock.patch.object(self.mod, "_pkg_run_bounded", side_effect=fake):
            r = self.mod._pkg_smoke(self.kdir, timeout=120)
        self.assertFalse(r["ok"])
        self.assertIn("index-validate", r["detail"])

    def test_timeout_fails_not_hangs(self):
        def fake(cmd, cwd, kdir, timeout):
            if "steering" in cmd:
                return 124, "timed out after %ds" % timeout
            return 0, "ok"

        with mock.patch.object(self.mod, "_pkg_run_bounded", side_effect=fake):
            r = self.mod._pkg_smoke(self.kdir, timeout=1)
        self.assertFalse(r["ok"])
        bad = [s for s in r["steps"] if s["name"] == "steering-lint"][0]
        self.assertEqual(bad["rc"], 124)

    def test_run_bounded_real_timeout(self):
        # Exercise the REAL hard-timeout path with a sleepy command.
        rc, detail = self.mod._pkg_run_bounded(
            ["/bin/sh", "-c", "sleep 5"], self.kdir, None, timeout=1)
        self.assertEqual(rc, 124)
        self.assertIn("timed out", detail)


if __name__ == "__main__":
    unittest.main()
