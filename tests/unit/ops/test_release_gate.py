"""Tests for the release gate harness (scripts/agentware gate release).

Verifies:
- gate release passes on a clean tree (Tier-A)
- gate blocks on a simulated regression
- kill-switch exits 0
- Tier-B skip-with-log when baselines absent
"""

import contextlib
import io
import json
import os
import tempfile
import unittest

from tests._fixtures import load_cli, run_cli, REPO_ROOT, SyntheticKBTestCase


class TestGateReleaseClean(SyntheticKBTestCase):
    """Gate release passes on a clean tree with valid steering + KB."""

    def test_gate_release_passes_json(self):
        """gate release --format json exits 0 with passed=true on clean state."""
        code, out, err = run_cli(["gate", "release", "--format", "json"],
                                 self.kdir)
        self.assertEqual(code, 0, "gate release should pass: stderr=%s" % err)
        payload = json.loads(out)
        self.assertTrue(payload["passed"])
        self.assertFalse(payload.get("skipped", False))
        # Should have 3 Tier-A gates
        self.assertEqual(len(payload["gates"]), 3)
        gate_names = [g["gate"] for g in payload["gates"]]
        self.assertIn("content-lint", gate_names)
        self.assertIn("gold-fixture", gate_names)
        self.assertIn("reliability-delta", gate_names)

    def test_gate_release_passes_text(self):
        """gate release (text format) exits 0 and prints PASSED."""
        code, out, err = run_cli(["gate", "release"], self.kdir)
        self.assertEqual(code, 0, "gate release should pass: stderr=%s" % err)
        self.assertIn("PASSED", out)


class TestGateReleaseKillSwitch(SyntheticKBTestCase):
    """Kill-switch AGENTWARE_DISABLE_RELEASE_GATE=1 exits 0 with skip."""

    def test_kill_switch_json(self):
        """Kill-switch makes gate release exit 0 with skipped=true."""
        prev = os.environ.get("AGENTWARE_DISABLE_RELEASE_GATE")
        os.environ["AGENTWARE_DISABLE_RELEASE_GATE"] = "1"
        try:
            code, out, err = run_cli(["gate", "release", "--format", "json"],
                                     self.kdir)
        finally:
            if prev is None:
                os.environ.pop("AGENTWARE_DISABLE_RELEASE_GATE", None)
            else:
                os.environ["AGENTWARE_DISABLE_RELEASE_GATE"] = prev
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["passed"])
        self.assertTrue(payload["skipped"])

    def test_kill_switch_text(self):
        """Kill-switch prints skip message in text mode."""
        prev = os.environ.get("AGENTWARE_DISABLE_RELEASE_GATE")
        os.environ["AGENTWARE_DISABLE_RELEASE_GATE"] = "1"
        try:
            code, out, err = run_cli(["gate", "release"], self.kdir)
        finally:
            if prev is None:
                os.environ.pop("AGENTWARE_DISABLE_RELEASE_GATE", None)
            else:
                os.environ["AGENTWARE_DISABLE_RELEASE_GATE"] = prev
        self.assertEqual(code, 0)
        self.assertIn("SKIPPED", out)


class TestGateReleaseRegression(unittest.TestCase):
    """Gate blocks on a simulated content-preservation regression."""

    def setUp(self):
        self.cli = load_cli()
        self._orig_agents = self._read_file("AGENTS.md")

    def tearDown(self):
        self._write_file("AGENTS.md", self._orig_agents)

    def _read_file(self, rel):
        path = os.path.join(REPO_ROOT, rel)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _write_file(self, rel, content):
        path = os.path.join(REPO_ROOT, rel)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_gate_blocks_on_missing_required_id(self):
        """Removing a required rule ID causes gate release to fail."""
        # Remove R-EXEC-01 line from AGENTS.md
        modified = self._orig_agents.replace(
            "- MUST treat building and completing tasks as the primary job. [R-EXEC-01]\n",
            ""
        )
        self._write_file("AGENTS.md", modified)
        # Use a temp dir as kdir to avoid touching real KB
        with tempfile.TemporaryDirectory() as kdir:
            os.makedirs(os.path.join(kdir, "benchmarks"), exist_ok=True)
            # Write a minimal index.json
            with open(os.path.join(kdir, "index.json"), "w") as f:
                json.dump([], f)
            code, out, err = run_cli(
                ["gate", "release", "--format", "json"], kdir)
        self.assertEqual(code, 1,
                         "gate should FAIL on missing R-EXEC-01: out=%s" % out)
        payload = json.loads(out)
        self.assertFalse(payload["passed"])


class TestGateReleaseTierB(SyntheticKBTestCase):
    """Tier-B (--full) skip-with-log when baselines absent."""

    def test_full_skips_with_log(self):
        """--full passes with SKIP messages when own-gold/LongMemEval absent."""
        code, out, err = run_cli(
            ["gate", "release", "--full", "--format", "json"], self.kdir)
        self.assertEqual(code, 0,
                         "full gate should pass (skip) when baselines absent: "
                         "stderr=%s" % err)
        payload = json.loads(out)
        self.assertTrue(payload["passed"])
        # Should have Tier-A (3) + Tier-B gates (own-gold, longmemeval, swe-task)
        gate_names = [g["gate"] for g in payload["gates"]]
        self.assertIn("own-gold", gate_names)
        self.assertIn("longmemeval", gate_names)
        self.assertIn("swe-task", gate_names)
        # own-gold and longmemeval should have SKIP in details
        for g in payload["gates"]:
            if g["gate"] in ("own-gold", "longmemeval"):
                details_str = " ".join(g.get("details", []))
                self.assertIn("SKIP", details_str,
                              "%s should have SKIP in details" % g["gate"])


class TestGateReleaseNoKDir(unittest.TestCase):
    """Graceful no-op when knowledge dir is absent."""

    def test_no_kdir_passes(self):
        """gate release passes gracefully when no knowledge dir."""
        code, out, err = run_cli(
            ["gate", "release", "--format", "json"], "/nonexistent/path")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["passed"])
        self.assertTrue(payload["skipped"])


if __name__ == "__main__":
    unittest.main()
