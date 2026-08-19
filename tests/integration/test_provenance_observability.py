"""Tests for per-user provenance coverage audit + onboarding metric emission.

Feature 260625-team-mode-onboarding-fork, Task 9 (Rule-7 observability):
- `provenance_coverage` / the `provenance_coverage` audit check report entries
  whose frontmatter lacks an `author` (the provenance-mix panel signal). Advisory
  in power mode, FAIL in team mode.
- `emit_onboarding_metric` / `config --record-onboarding` append one onboarding
  event line to logs/metrics.jsonl with the documented shape.

Hermetic synthetic KB (R-LOC-03).
"""

import json
import os
import unittest

from tests._fixtures import SyntheticKBTestCase, load_cli


class ProvenanceCoverageTests(SyntheticKBTestCase):
    def setUp(self):
        super().setUp()
        # Overwrite the fixture entry files with RAW bodies (no frontmatter) so
        # the provenance audit correctly reports them as missing `author`. The
        # shared _fixtures now writes frontmatter, but THESE tests specifically
        # exercise the "no author" detection path.
        mod = load_cli()
        data = self.read_index()
        for e in data.get("entries", []):
            path = os.path.join(self.kdir, e["path"])
            body = mod.strip_frontmatter(open(path, encoding="utf-8").read())
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)

    def test_reports_missing_author_entries(self):
        # Entry files have NO frontmatter => no author detected.
        mod = load_cli()
        data = self.read_index()
        missing, total = mod.provenance_coverage(self.kdir, data)
        self.assertGreater(total, 0)
        self.assertEqual(sorted(missing), sorted(e["id"] for e in data["entries"]))

    def test_check_advisory_in_power_mode(self):
        # No author anywhere, but power mode => check stays ok (advisory).
        mod = load_cli()
        os.environ["AGENTWARE_KB_MODE"] = "power"
        try:
            data = self.read_index()
            chk = mod._audit_provenance_coverage_check(self.kdir, data)
            self.assertEqual(chk["name"], "provenance_coverage")
            self.assertTrue(chk["ok"])
            self.assertIn("advisory", " ".join(chk["details"]))
        finally:
            os.environ.pop("AGENTWARE_KB_MODE", None)

    def test_check_fails_in_team_mode(self):
        mod = load_cli()
        os.environ["AGENTWARE_KB_MODE"] = "team"
        try:
            data = self.read_index()
            chk = mod._audit_provenance_coverage_check(self.kdir, data)
            self.assertFalse(chk["ok"])
            self.assertIn("FAIL", " ".join(chk["details"]))
        finally:
            os.environ.pop("AGENTWARE_KB_MODE", None)

    def test_full_coverage_passes_in_team_mode(self):
        # Add an entry WITH author frontmatter via learn; then point the index at
        # only that entry so coverage is full.
        mod = load_cli()
        code, _o, err = self.run_cli(
            ["learn", "--topic", "authored", "--summary", "S", "--tags", "a,b",
             "--content", "Body.", "--author", "alice", "--source", "user"])
        self.assertEqual(code, 0, err)
        full_data = {"entries": [e for e in self.read_index()["entries"]
                                 if e["id"] == "learn-authored"], "tags": {}}
        os.environ["AGENTWARE_KB_MODE"] = "team"
        try:
            chk = mod._audit_provenance_coverage_check(self.kdir, full_data)
            self.assertTrue(chk["ok"], chk)
            self.assertIn("full coverage", " ".join(chk["details"]))
        finally:
            os.environ.pop("AGENTWARE_KB_MODE", None)


class OnboardingMetricTests(SyntheticKBTestCase):
    def test_emit_onboarding_metric_shape(self):
        mod = load_cli()
        ok = mod.emit_onboarding_metric(self.kdir, "team", "alice")
        self.assertTrue(ok)
        path = os.path.join(self.kdir, "logs", "metrics.jsonl")
        self.assertTrue(os.path.exists(path))
        lines = [json.loads(l) for l in open(path) if l.strip()]
        self.assertEqual(len(lines), 1)
        rec = lines[0]
        self.assertEqual(rec["event"], "onboarding")
        self.assertEqual(rec["mode"], "team")
        self.assertEqual(rec["user_handle"], "alice")
        self.assertIn("ts", rec)

    def test_record_onboarding_via_cli(self):
        os.environ["AGENTWARE_KB_MODE"] = "team"
        os.environ["AGENTWARE_USER_HANDLE"] = "bob"
        try:
            code, _out, err = self.run_cli(["config", "--record-onboarding"])
            self.assertEqual(code, 0, err)
        finally:
            os.environ.pop("AGENTWARE_KB_MODE", None)
            os.environ.pop("AGENTWARE_USER_HANDLE", None)
        path = os.path.join(self.kdir, "logs", "metrics.jsonl")
        rec = [json.loads(l) for l in open(path) if l.strip()][-1]
        self.assertEqual(rec["event"], "onboarding")
        self.assertEqual(rec["mode"], "team")
        self.assertEqual(rec["user_handle"], "bob")


if __name__ == "__main__":
    unittest.main()
