"""Tests for scripts/hooks/codex-stream.py — codex `--json` logging parity.

Feature 260627-codex-runtime-adapter, Task 6. Codex fires NO `.claude/*` hooks,
so the rich logging a claude spawn gets (prompts.log + per-action live.jsonl /
live.md + main.jsonl + the $AGENTWARE_LIVE_LOG live-stream sink) is reconstructed
by piping `codex exec --json` through this renderer. This test feeds a CAPTURED
sample codex event stream (prompt + ≥2 tool calls + final message, one oversized
tool payload, plus a malformed line) to the renderer and asserts it produced the
SAME sinks the claude hooks write, with tool I/O bounded and the final assistant
message echoed to stdout so run_phase's `<promise>` grep keeps working.

Hermetic: every run uses a fresh temp log dir and a temp $AGENTWARE_LIVE_LOG, so
the operator's real logs/ is NEVER touched (R-LOC-03).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDERER = os.path.join(REPO, "scripts", "hooks", "codex-stream.py")
SID = "019f074e-cafe-7a00-test-thread0000001"
MAXLEN = 1500
TRUNC_MARK = " …[truncated]"

# A captured-shape codex `--json` event stream: thread.started (=> sid),
# turn.started, an intro agent_message, TWO command_execution tool calls (the
# second with an oversized aggregated_output), a MALFORMED line (must be skipped),
# a final agent_message carrying the <promise> tag, then turn.completed.
BIG_OUTPUT = "X" * 5000


def _sample_stream():
    events = [
        {"type": "thread.started", "thread_id": SID},
        {"type": "turn.started"},
        {"type": "item.completed",
         "item": {"id": "item_0", "type": "agent_message",
                  "text": "I'll run two shell commands and summarize."}},
        {"type": "item.started",
         "item": {"id": "item_1", "type": "command_execution",
                  "command": "/bin/zsh -lc 'echo hello'", "aggregated_output": "",
                  "exit_code": None, "status": "in_progress"}},
        {"type": "item.completed",
         "item": {"id": "item_1", "type": "command_execution",
                  "command": "/bin/zsh -lc 'echo hello'",
                  "aggregated_output": "hello\n", "exit_code": 0,
                  "status": "completed"}},
        {"type": "item.completed",
         "item": {"id": "item_2", "type": "command_execution",
                  "command": "/bin/zsh -lc 'cat big.txt'",
                  "aggregated_output": BIG_OUTPUT, "exit_code": 0,
                  "status": "completed"}},
        {"type": "item.completed",
         "item": {"id": "item_3", "type": "agent_message",
                  "text": "All done. <promise>TASK_COMPLETE</promise>"}},
        {"type": "turn.completed",
         "usage": {"input_tokens": 100, "output_tokens": 20}},
    ]
    lines = [json.dumps(e) for e in events]
    # Inject a malformed line right before the final message (index 6).
    lines.insert(6, "{ this is not valid json ::::")
    return "\n".join(lines) + "\n"


class CodexStreamLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-codexlog-")
        self.log_dir = os.path.join(self.tmp, "logs")
        self.live_log = os.path.join(self.tmp, "live.stream")
        env = dict(os.environ)
        env["AGENTWARE_LIVE_LOG"] = self.live_log
        self.prompt = "Implement the next task in the plan."
        self.proc = subprocess.run(
            [sys.executable, RENDERER,
             "--log-dir", self.log_dir,
             "--feature", "zz-codex-smoke",
             "--prompt", self.prompt],
            input=_sample_stream(), capture_output=True, text=True, env=env,
        )
        self.sess = os.path.join(self.log_dir, "sessions", SID)

    def _read(self, *parts):
        with open(os.path.join(*parts), "r", encoding="utf-8") as fh:
            return fh.read()

    def test_exit_zero_despite_malformed_line(self):
        # A malformed/partial JSON line must be skipped, never abort the run.
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)

    def test_sid_derived_from_thread_started(self):
        self.assertTrue(os.path.isdir(self.sess),
                        "session dir not derived from codex thread_id")

    def test_live_jsonl_one_record_per_tool_call(self):
        raw = self._read(self.sess, "live.jsonl").strip().splitlines()
        self.assertEqual(len(raw), 2, "expected 1 live.jsonl record per tool call")
        recs = [json.loads(line) for line in raw]
        for r in recs:
            self.assertEqual(r["tool"], "command_execution")
            self.assertIn("ts", r)
            self.assertEqual(r["status"], "ok")

    def test_live_md_one_human_line_per_tool_call(self):
        lines = [ln for ln in self._read(self.sess, "live.md").splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        for ln in lines:
            self.assertIn("🔧", ln)

    def test_main_jsonl_lossless_and_skips_malformed(self):
        lines = [ln for ln in self._read(self.sess, "main.jsonl").splitlines() if ln.strip()]
        # All 8 valid events land; the malformed line is skipped (not 9).
        self.assertEqual(len(lines), 8)
        for ln in lines:
            json.loads(ln)  # every recorded line is valid JSON

    def test_prompts_log_records_initial_prompt(self):
        body = self._read(self.log_dir, "prompts.log")
        self.assertIn(self.prompt, body)
        self.assertIn("[session %s]" % SID, body)

    def test_live_log_sink_appended(self):
        lines = [ln for ln in self._read(self.live_log).splitlines() if ln.strip()]
        # One human line per tool call (NOT gated by --no-stream).
        self.assertEqual(len(lines), 2)
        for ln in lines:
            self.assertIn("🔧", ln)

    def test_oversized_tool_io_truncated(self):
        recs = [json.loads(l) for l in self._read(self.sess, "live.jsonl").splitlines() if l.strip()]
        big = [r for r in recs if r["response"].startswith("X")][0]
        self.assertTrue(big["response"].endswith(TRUNC_MARK))
        self.assertLessEqual(len(big["response"]), MAXLEN + len(TRUNC_MARK))
        # The bounded payload is far shorter than the 5000-char original.
        self.assertLess(len(big["response"]), len(BIG_OUTPUT))

    def test_final_message_echoed_to_stdout(self):
        # Invariant (i): the <promise> must reach stdout for run_phase's grep.
        self.assertIn("<promise>TASK_COMPLETE</promise>", self.proc.stdout)


if __name__ == "__main__":
    unittest.main()
