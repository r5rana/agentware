"""Provenance logging — origin tagging, turn-id join, content-based labeling,
per-agent attribution (feature 260710-learning-repair-p1 task 7).

Drives the REAL hooks against a temp knowledge dir (AGENTWARE_KNOWLEDGE_DIR) and
imports render-transcript.py directly. Stdlib only; no operator KB touched.
"""

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import REPO_ROOT
except ImportError:
    from _fixtures import REPO_ROOT

HOOKS = os.path.join(REPO_ROOT, "scripts", "hooks")


def _load_render():
    path = os.path.join(HOOKS, "render-transcript.py")
    loader = importlib.machinery.SourceFileLoader("render_transcript", path)
    spec = importlib.util.spec_from_loader("render_transcript", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _have(b):
    return shutil.which(b) is not None


class RenderTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.r = _load_render()

    def test_genuine_user_text_ignores_tool_result_blocks(self):
        self.assertEqual(self.r._genuine_user_text("hello"), "hello")
        only_tool = [{"type": "tool_result", "content": [
            {"type": "text", "text": "res"}]}]
        self.assertEqual(self.r._genuine_user_text(only_tool), "")
        mixed = [{"type": "text", "text": "real"}, {"type": "tool_result"}]
        self.assertEqual(self.r._genuine_user_text(mixed), "real")

    def test_turn_id_matches_prompt_hash(self):
        # The render turn id for a genuine user prompt is the SAME sha1[:12] that
        # log-prompt.sh writes, so prompts.log joins to the rendered transcript.
        txt = "please add a feature"
        entry = {"uuid": "x"}
        expect = "turn-" + hashlib.sha1(txt.encode()).hexdigest()[:12]
        self.assertEqual(self.r._turn_id(entry, "user", txt), expect)

    def test_render_labels_by_content(self):
        rows = [
            {"type": "user", "timestamp": "t1", "uuid": "a-b",
             "message": {"role": "user", "content": "real prompt"}},
            {"type": "user", "timestamp": "t2",
             "message": {"role": "user", "content": [
                 {"type": "tool_result", "content": [
                     {"type": "text", "text": "res"}]}]}},
        ]
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "tx.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        out = subprocess.run(
            ["python3", os.path.join(HOOKS, "render-transcript.py"), p],
            stdout=subprocess.PIPE, text=True).stdout
        self.assertIn("🧑 USER", out)
        self.assertIn("🛠 TOOL RESULT", out)
        h = hashlib.sha1("real prompt".encode()).hexdigest()[:12]
        self.assertIn("{#turn-%s}" % h, out)


@unittest.skipUnless(_have("jq") and _have("bash"), "jq + bash required")
class HookProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-prov-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        os.makedirs(os.path.join(self.kdir, "logs"))

    def _log_prompt(self, payload, **env):
        e = dict(os.environ)
        e["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        e.update(env)
        subprocess.run(["bash", os.path.join(HOOKS, "log-prompt.sh")],
                       input=json.dumps(payload), text=True, env=e)

    def _prompts(self):
        with open(os.path.join(self.kdir, "logs", "prompts.log"),
                  encoding="utf-8") as f:
            return f.read()

    def test_origin_loop_when_env_set(self):
        self._log_prompt({"session_id": "s", "prompt": "x", "cwd": "/"},
                         AGENTWARE_ORIGIN="loop-exec")
        self.assertIn("[origin=loop]", self._prompts())

    def test_origin_system_heuristic(self):
        e = dict(os.environ)
        e.pop("AGENTWARE_ORIGIN", None)
        e["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        subprocess.run(
            ["bash", os.path.join(HOOKS, "log-prompt.sh")],
            input=json.dumps({"session_id": "s",
                              "prompt": "<task-notification>t</task-notification>",
                              "cwd": "/"}), text=True, env=e)
        self.assertIn("[origin=system]", self._prompts())

    def test_origin_human_default(self):
        e = dict(os.environ)
        e.pop("AGENTWARE_ORIGIN", None)
        e["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        subprocess.run(
            ["bash", os.path.join(HOOKS, "log-prompt.sh")],
            input=json.dumps({"session_id": "s", "prompt": "add a feature",
                              "cwd": "/"}), text=True, env=e)
        self.assertIn("[origin=human]", self._prompts())

    def test_log_tool_attributes_subagent(self):
        e = dict(os.environ)
        e["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        for tp, tool in [("/p/sX.jsonl", "Bash"),
                         ("/p/sX/subagents/agent-9.jsonl", "Grep")]:
            subprocess.run(
                ["bash", os.path.join(HOOKS, "log-tool.sh")],
                input=json.dumps({"session_id": "sX", "tool_name": tool,
                                  "tool_input": {}, "tool_response": {},
                                  "transcript_path": tp}), text=True, env=e)
        rows = [json.loads(l) for l in open(
            os.path.join(self.kdir, "logs", "sessions", "sX", "live.jsonl"))
            if l.strip()]
        by_tool = {r["tool"]: r["agent"] for r in rows}
        self.assertEqual(by_tool["Bash"], "main")
        self.assertEqual(by_tool["Grep"], "agent-9")


if __name__ == "__main__":
    unittest.main()
