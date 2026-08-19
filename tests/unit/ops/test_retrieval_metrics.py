"""Retrieval enforcement + utilization metrics
(feature 260710-learning-repair-p1 task 9).

Part (a): retrieval-state.sh parses the stdin JSON payload (the CLAUDE_TOOL_*
env vars it used to read are never set) and keys state per session_id.
Part (b): `metrics --retrieval` derives per-session recall-invoked / returned-ids
/ read-through / corpus-coverage over logs/sessions/*/live.jsonl.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import (REPO_ROOT, SyntheticKBTestCase, build_synthetic_kb,
                                 run_cli)
except ImportError:
    from _fixtures import (REPO_ROOT, SyntheticKBTestCase, build_synthetic_kb,
                          run_cli)

HOOK = os.path.join(REPO_ROOT, "scripts", "hooks", "retrieval-state.sh")


def _have(b):
    return shutil.which(b) is not None


@unittest.skipUnless(_have("jq") and _have("bash"), "jq + bash required")
class RetrievalStateHookTests(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-retstate-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        os.makedirs(os.path.join(self.kdir, "logs"))
        self.state_dir = os.path.join(self.kdir, "logs", ".retrieval-state")

    def _fire(self, payload, **env):
        e = dict(os.environ)
        e["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        e.update(env)
        return subprocess.run(["bash", HOOK], input=json.dumps(payload),
                              text=True, env=e, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)

    def test_parses_stdin_and_writes_per_session_state(self):
        # Uses stdin JSON, not CLAUDE_TOOL_* env vars (deliberately set wrong to
        # prove they are ignored).
        self._fire({"session_id": "sess-A", "tool_name": "Bash",
                    "tool_input": {"command": "grep -r foo ."}},
                   CLAUDE_TOOL_NAME="Read", CLAUDE_TOOL_INPUT="ignored")
        self.assertTrue(os.path.isfile(
            os.path.join(self.state_dir, "sess-A.state")))
        with open(os.path.join(self.state_dir, "sess-A.state")) as f:
            self.assertEqual(f.read().strip(), "GREP_WS")

    def test_sessions_keyed_separately(self):
        self._fire({"session_id": "sA", "tool_name": "Bash",
                    "tool_input": {"command": "scripts/agentware recall x"}})
        self._fire({"session_id": "sB", "tool_name": "Bash",
                    "tool_input": {"command": "grep -r y ."}})
        self.assertTrue(os.path.isfile(os.path.join(self.state_dir, "sA.state")))
        self.assertTrue(os.path.isfile(os.path.join(self.state_dir, "sB.state")))

    def test_warns_grep_before_recall(self):
        p = self._fire({"session_id": "sW", "tool_name": "Bash",
                        "tool_input": {"command": "grep -r foo ."}})
        self.assertIn("before any recall", p.stderr)
        self.assertEqual(p.returncode, 0)  # warn-only, never blocks


class RetrievalMetricsTests(SyntheticKBTestCase):
    def _session(self, sid, records):
        sdir = os.path.join(self.kdir, "logs", "sessions", sid)
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "live.jsonl"), "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def _report(self):
        code, out, err = self.run_cli(["metrics", "--retrieval", "--format", "json"])
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_recall_and_read_through_measured(self):
        path = "learnings/geofence-reminders.md"
        self._session("live-1", [
            {"tool": "Bash", "agent": "main",
             "input": '{"command":"scripts/agentware recall geofence"}',
             "response": "learn-geofence-reminders  %s  score 8.0" % path},
            {"tool": "Read", "agent": "main",
             "input": '{"file_path":"/abs/%s"}' % path, "response": "..."},
        ])
        rep = self._report()
        self.assertGreaterEqual(rep["sessions_sampled"], 1)
        s = next(x for x in rep["sessions"] if x["session"] == "live-1")
        self.assertTrue(s["recall_invoked"])
        self.assertIn("learn-geofence-reminders", s["returned_ids"])
        self.assertEqual(s["read_through"]["read"], 1)
        self.assertEqual(s["read_through"]["returned_paths"], 1)
        cc = rep["corpus_coverage"]
        self.assertGreaterEqual(cc["learning_ids_surfaced"], 1)
        self.assertEqual(cc["corpus_size"],
                         len([e for e in self.read_index()["entries"]
                              if e["category"] == "learnings"]))

    def test_verify_string_recall_excluded(self):
        # A recall inside a plan/task --verify assertion is NOT a real retrieval.
        self._session("live-2", [
            {"tool": "Bash", "agent": "main",
             "input": '{"command":"--verify: scripts/agentware recall foo"}',
             "response": "learn-geofence-reminders"},
        ])
        s = next(x for x in self._report()["sessions"] if x["session"] == "live-2")
        self.assertFalse(s["recall_invoked"])

    def test_subagent_recall_excluded(self):
        self._session("live-3", [
            {"tool": "Bash", "agent": "agent-9",
             "input": '{"command":"scripts/agentware recall foo"}',
             "response": "learn-geofence-reminders"},
        ])
        s = next(x for x in self._report()["sessions"] if x["session"] == "live-3")
        self.assertFalse(s["recall_invoked"])


if __name__ == "__main__":
    unittest.main()
