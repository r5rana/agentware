"""Smoke tests for the EXISTING agentware CLI commands against a synthetic KB.

Runner:  python3 -m unittest discover -s tests -v
   or:   python3 -m unittest tests.test_existing_cli -v
"""

import json
import os

try:
    from tests._fixtures import SyntheticKBTestCase
except ImportError:  # when run via `discover -s tests`, tests/ is on sys.path
    from _fixtures import SyntheticKBTestCase


class TestExistingCli(SyntheticKBTestCase):
    def test_config_resolves_synthetic_kdir(self):
        code, out, _ = self.run_cli(["config", "--format", "json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["knowledge_dir"], self.kdir)
        self.assertTrue(payload["configured"])

    def test_config_knowledge_dir_only(self):
        code, out, _ = self.run_cli(["config", "--knowledge-dir-only"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), self.kdir)

    def test_index_validate_passes_on_synthetic_kb(self):
        code, out, _ = self.run_cli(["index", "validate", "--format", "json"])
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["errors"], [])

    def test_query_by_tag_returns_entry(self):
        code, out, _ = self.run_cli(["query", "--tag", "geofence"])
        self.assertEqual(code, 0)
        results = json.loads(out)
        ids = {r["id"] for r in results}
        self.assertIn("learn-geofence-reminders", ids)

    def test_query_by_unknown_tag_is_empty(self):
        code, out, _ = self.run_cli(["query", "--tag", "no-such-tag-xyz"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])

    def test_query_by_category(self):
        code, out, _ = self.run_cli(["query", "--category", "learnings"])
        self.assertEqual(code, 0)
        cats = {r["category"] for r in json.loads(out)}
        self.assertEqual(cats, {"learnings"})

    def test_learn_creates_and_registers_entry(self):
        code, out, err = self.run_cli([
            "learn", "--topic", "smoke-test-topic",
            "--summary", "a smoke test learning",
            "--tags", "smoke,test",
            "--content", "This is the body of a smoke test learning.",
            "--format", "json",
        ])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["learning"]["id"], "learn-smoke-test-topic")
        # File exists on disk...
        self.assertTrue(os.path.isfile(
            os.path.join(self.kdir, "learnings", "smoke-test-topic.md")))
        # ...and is registered in the index.
        idx = self.read_index()
        ids = {e["id"] for e in idx["entries"]}
        self.assertIn("learn-smoke-test-topic", ids)
        # Index still validates after the mutation.
        code2, _, _ = self.run_cli(["index", "validate"])
        self.assertEqual(code2, 0)

    def test_audit_passes_on_synthetic_kb(self):
        code, out, _ = self.run_cli(["audit", "--format", "json"])
        payload = json.loads(out)
        # All KB-scoped checks must be clean; steering_lint runs over the real
        # repo steering and must also be clean.
        failed = [c["name"] for c in payload["checks"] if not c["ok"]]
        self.assertEqual(failed, [], "audit checks failed: %s" % out)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
