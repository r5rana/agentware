"""Phase 4.1 — `metrics` transcript parser tests.

Builds synthetic session transcripts (logs/sessions/<sid>/main.jsonl plus
subagents/*.jsonl) with KNOWN token usage, timestamps and tool calls, then
asserts the parser yields exactly the expected totals, duration and counts.
Also covers filters (--session/--feature/--since), malformed-line tolerance,
determinism, and read-only behaviour (INV-2). Stdlib unittest only.
"""

import json
import os
import unittest

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # allow `python3 -m unittest tests.test_metrics`
    from _fixtures import SyntheticKBTestCase, load_cli


CLI = load_cli()


def _assistant(ts, usage=None, tools=None, thinking=True):
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": "..."})
    for name in (tools or []):
        content.append({"type": "tool_use", "name": name, "input": {}})
    msg = {"role": "assistant", "content": content}
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


class TokenAndCountTest(MetricsTestCase):

    def test_known_totals_duration_and_tool_counts(self):
        # Two assistant turns with known usage + tools, spanning 90 seconds.
        self._session("sid-a", [
            _control("agent-setting"),                       # no timestamp
            _user("2026-06-24T10:00:00.000Z"),
            _assistant("2026-06-24T10:00:30.000Z",
                       usage=_usage(i=100, o=10, cc=5, cr=200),
                       tools=["Bash", "Read"]),
            _assistant("2026-06-24T10:01:30.000Z",
                       usage=_usage(i=50, o=20, cc=0, cr=300),
                       tools=["Bash"]),
        ])
        code, out, err = self.run_cli(["metrics", "--format", "json"])
        self.assertEqual(code, 0, err)
        row = json.loads(out)["sessions"][0]
        self.assertEqual(row["session_id"], "sid-a")
        self.assertEqual(row["turns"], 2)
        self.assertEqual(row["tokens"], _usage(i=150, o=30, cc=5, cr=500))
        self.assertEqual(row["total_tokens"], 150 + 30 + 5 + 500)
        self.assertEqual(row["duration_seconds"], 90.0)
        self.assertEqual(row["tools"], {"Bash": 2, "Read": 1})
        self.assertEqual(row["tool_calls"], 3)
        self.assertEqual(row["subagent_count"], 0)

    def test_subagents_fold_into_tokens_and_counts(self):
        self._session(
            "sid-b",
            [_assistant("2026-06-24T10:00:00.000Z",
                        usage=_usage(i=100, o=10), tools=["Read"])],
            subagents={
                "agent-1.jsonl": [
                    _assistant("2026-06-24T10:00:05.000Z",
                               usage=_usage(i=40, o=5), tools=["Grep"])],
                "agent-2.jsonl": [
                    _assistant("2026-06-24T10:00:06.000Z",
                               usage=_usage(i=10, o=2), tools=["Read"])],
            })
        code, out, err = self.run_cli(["metrics", "--format", "json"])
        self.assertEqual(code, 0, err)
        row = json.loads(out)["sessions"][0]
        self.assertEqual(row["subagent_count"], 2)
        # tokens = main + both subagents
        self.assertEqual(row["tokens"]["input_tokens"], 150)
        self.assertEqual(row["tokens"]["output_tokens"], 17)
        self.assertEqual(row["subagent_tokens"]["input_tokens"], 50)
        self.assertEqual(row["tools"], {"Read": 2, "Grep": 1})
        self.assertEqual(row["turns"], 3)            # 1 main + 2 subagent
        self.assertEqual(row["main_turns"], 1)
        self.assertEqual(row["subagent_turns"], 2)

    def test_aggregate_sums_across_sessions(self):
        self._session("s1", [
            _assistant("2026-06-24T10:00:00.000Z",
                       usage=_usage(i=100), tools=["Bash"]),
            _assistant("2026-06-24T10:00:10.000Z", usage=_usage(o=50)),
        ])
        self._session("s2", [
            _assistant("2026-06-25T10:00:00.000Z",
                       usage=_usage(i=25), tools=["Bash", "Edit"]),
        ])
        code, out, err = self.run_cli(["metrics", "--format", "json"])
        self.assertEqual(code, 0, err)
        agg = json.loads(out)["aggregate"]
        self.assertEqual(agg["session_count"], 2)
        self.assertEqual(agg["turns"], 3)
        self.assertEqual(agg["tokens"]["input_tokens"], 125)
        self.assertEqual(agg["tokens"]["output_tokens"], 50)
        self.assertEqual(agg["tools"], {"Bash": 2, "Edit": 1})


class ToleranceTest(MetricsTestCase):

    def test_malformed_and_typeless_lines_are_tolerated(self):
        sdir = os.path.join(self.kdir, "logs", "sessions", "sid-bad")
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "main.jsonl"), "w", encoding="utf-8") as f:
            f.write("{ this is not json\n")                  # malformed
            f.write("\n")                                      # blank
            f.write(json.dumps([1, 2, 3]) + "\n")              # not a dict
            f.write(json.dumps(_control("queue-operation")) + "\n")
            f.write(json.dumps(_assistant(
                "2026-06-24T10:00:00.000Z", usage=_usage(i=7))) + "\n")
        code, out, err = self.run_cli(["metrics", "--format", "json"])
        self.assertEqual(code, 0, err)
        row = json.loads(out)["sessions"][0]
        self.assertEqual(row["turns"], 1)
        self.assertEqual(row["tokens"]["input_tokens"], 7)
        self.assertEqual(row["malformed_lines"], 2)           # bad json + list

    def test_assistant_without_usage_does_not_crash(self):
        self._session("sid-nousage", [
            _assistant("2026-06-24T10:00:00.000Z", usage=None, tools=["Bash"]),
        ])
        code, out, err = self.run_cli(["metrics", "--format", "json"])
        self.assertEqual(code, 0, err)
        row = json.loads(out)["sessions"][0]
        self.assertEqual(row["total_tokens"], 0)
        self.assertEqual(row["tool_calls"], 1)

    def test_no_sessions_returns_nonzero(self):
        # No logs dir at all.
        code, out, err = self.run_cli(["metrics", "--format", "json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["sessions"], [])


class FilterTest(MetricsTestCase):

    def setUp(self):
        super().setUp()
        self._session("sid-old", [
            _assistant("2026-06-20T10:00:00.000Z", usage=_usage(i=1))])
        self._session("sid-new", [
            _user("2026-06-24T09:00:00.000Z", text="work on 260624-foo feature"),
            _assistant("2026-06-24T10:00:00.000Z", usage=_usage(i=2))])

    def test_session_filter(self):
        code, out, _ = self.run_cli(
            ["metrics", "--session", "sid-old", "--format", "json"])
        self.assertEqual(code, 0)
        rows = json.loads(out)["sessions"]
        self.assertEqual([r["session_id"] for r in rows], ["sid-old"])

    def test_since_filter(self):
        code, out, _ = self.run_cli(
            ["metrics", "--since", "2026-06-24", "--format", "json"])
        self.assertEqual(code, 0)
        rows = json.loads(out)["sessions"]
        self.assertEqual([r["session_id"] for r in rows], ["sid-new"])

    def test_since_rejects_bad_date(self):
        code, _out, err = self.run_cli(
            ["metrics", "--since", "june", "--format", "json"])
        self.assertEqual(code, 1)
        self.assertIn("YYYY-MM-DD", err)

    def test_feature_filter_matches_transcript_mention(self):
        code, out, _ = self.run_cli(
            ["metrics", "--feature", "260624-foo", "--format", "json"])
        self.assertEqual(code, 0)
        rows = json.loads(out)["sessions"]
        self.assertEqual([r["session_id"] for r in rows], ["sid-new"])

    def test_ordering_is_by_start_then_id(self):
        code, out, _ = self.run_cli(["metrics", "--format", "json"])
        rows = json.loads(out)["sessions"]
        self.assertEqual([r["session_id"] for r in rows],
                         ["sid-old", "sid-new"])


class DeterminismAndReadOnlyTest(MetricsTestCase):

    def _index_bytes(self):
        with open(os.path.join(self.kdir, "index.json"), "rb") as f:
            return f.read()

    def test_byte_identical_across_runs(self):
        self._session("sid-x", [
            _assistant("2026-06-24T10:00:00.000Z",
                       usage=_usage(i=3, o=4), tools=["Bash"])])
        a = self.run_cli(["metrics", "--format", "json"])[1]
        b = self.run_cli(["metrics", "--format", "json"])[1]
        self.assertEqual(a, b)

    def test_metrics_does_not_mutate_index(self):
        self._session("sid-y", [
            _assistant("2026-06-24T10:00:00.000Z", usage=_usage(i=1))])
        before = self._index_bytes()
        self.run_cli(["metrics", "--format", "json"])
        self.run_cli(["metrics", "--format", "text"])
        self.assertEqual(before, self._index_bytes())

    def test_text_output_renders(self):
        self._session("sid-z", [
            _assistant("2026-06-24T10:00:00.000Z",
                       usage=_usage(i=10, o=5), tools=["Bash"])])
        code, out, err = self.run_cli(["metrics"])
        self.assertEqual(code, 0, err)
        self.assertIn("sid-z", out)
        self.assertIn("TOTAL", out)


if __name__ == "__main__":
    unittest.main()
