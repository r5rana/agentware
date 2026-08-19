"""Shared header for the `test_metrics` suite, split out by task 58.

Holds every import, helper, constant and base class the split
parts share, so no part duplicates a definition and none can
drift from another.
"""

import json
import os
import unittest
try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # allow `python3 -m unittest tests.unit.ops.test_metrics`
    from _fixtures import SyntheticKBTestCase, load_cli
CLI = load_cli()
def _assistant(ts, usage=None, tools=None, thinking=True, model=None):
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": "..."})
    for name in (tools or []):
        content.append({"type": "tool_use", "name": name, "input": {}})
    msg = {"role": "assistant", "content": content}
    if model is not None:
        msg["model"] = model
    if usage is not None:
        msg["usage"] = usage
    line = {"type": "assistant", "message": msg}
    if ts is not None:
        line["timestamp"] = ts
    return line
def _user(ts, text="hi"):
    line = {"type": "user", "message": {"role": "user", "content": text}}
    if ts is not None:
        line["timestamp"] = ts
    return line
def _control(typ, ts=None):
    line = {"type": typ}
    if ts is not None:
        line["timestamp"] = ts
    return line
def _usage(i=0, o=0, cc=0, cr=0):
    return {
        "input_tokens": i,
        "output_tokens": o,
        "cache_creation_input_tokens": cc,
        "cache_read_input_tokens": cr,
    }
def _write_jsonl(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(json.dumps(ln) + "\n")
class MetricsTestCase(SyntheticKBTestCase):

    def _session(self, sid, main_lines, subagents=None):
        sdir = os.path.join(self.kdir, "logs", "sessions", sid)
        _write_jsonl(os.path.join(sdir, "main.jsonl"), main_lines)
        for name, lines in (subagents or {}).items():
            _write_jsonl(os.path.join(sdir, "subagents", name), lines)
        return sdir

    def _agg(self, argv_extra=None):
        code, out, err = self.run_cli(
            ["metrics", "--format", "json"] + (argv_extra or []))
        self.assertEqual(code, 0, err)
        return json.loads(out)
_PLAN_FIXTURE = """# Plan — Demo

> Feature: `260626-demo`

## Tasks

- ⬜ **1.1** First task.
  *Verify:* it works.
- ✅ **1.2** Second task.
  *Verify:* it works.
- 🟡 **2.1** Third task.
  *Verify:* it works.

## Acceptance criteria

- [ ] First criterion.
- [ ] Second criterion.

<promise>260626_DEMO_COMPLETE</promise>
"""
if __name__ == "__main__":
    unittest.main()
