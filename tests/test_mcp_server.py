"""Tests for the read-only KB MCP server (`agentware mcp serve`).

The MCP server speaks JSON-RPC 2.0 over the MCP stdio transport (newline-
delimited messages) and exposes exactly two READ-ONLY tools — `recall` and
`query` — delegating to the existing read functions. These tests pin:

  * JSON-RPC 2.0 framing (parse-error -32700, method-not-found -32601, invalid-
    params -32602, id echoing);
  * the MCP lifecycle (initialize handshake, notifications/initialized silence);
  * tools/list schema parity with the `recall`/`query` argparse definitions;
  * INV-1 parity — the MCP `recall` result is byte-identical to
    `recall --format json`; `query` returns the same records as `query`;
  * INV-2 read-only — a full MCP session leaves the KB tree + index.json
    byte-identical, and NO mutating tool is registered (the CLI stays the sole
    writer of the index, R-KB-01);
  * an end-to-end subprocess drive of `mcp serve` over real stdio.

Stdlib `unittest` only (no pytest, no new deps). Never touches the real KB.
"""

import json
import os
import subprocess
import sys
import unittest

try:
    from tests._fixtures import (SyntheticKBTestCase, load_cli, CLI_PATH,
                                 run_cli, build_synthetic_kb)
except ImportError:  # allow `python3 -m unittest tests.test_mcp_server`
    from _fixtures import (SyntheticKBTestCase, load_cli, CLI_PATH,
                           run_cli, build_synthetic_kb)


CLI = load_cli()

# A pinned date so MCP recall and `recall --as-of` resolve the same ACR window.
_AS_OF = "2026-03-15"


class _McpBase(SyntheticKBTestCase):
    """Shared helper: dispatch a request dict against the synthetic KB."""

    def handle(self, req):
        return CLI.handle_mcp_request(req, self.kdir, self.index_data)


# --- Task 1: JSON-RPC 2.0 framing -------------------------------------------
class FramingTest(_McpBase):
    def test_unknown_method_is_method_not_found_and_echoes_id(self):
        resp = self.handle({"jsonrpc": "2.0", "id": 42, "method": "no/such"})
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertEqual(resp["id"], 42)
        self.assertEqual(resp["error"]["code"], -32601)
        self.assertNotIn("result", resp)

    def test_malformed_line_yields_parse_error_with_null_id(self):
        # The serve loop turns a json.loads failure into a -32700 envelope.
        resp = CLI._mcp_error(None, -32700, "parse error")
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertIsNone(resp["id"])
        self.assertEqual(resp["error"]["code"], -32700)

    def test_string_id_is_echoed_verbatim(self):
        resp = self.handle({"jsonrpc": "2.0", "id": "abc", "method": "tools/list"})
        self.assertEqual(resp["id"], "abc")

    def test_non_object_request_is_invalid_request(self):
        resp = CLI.handle_mcp_request([1, 2, 3], self.kdir, self.index_data)
        self.assertEqual(resp["error"]["code"], -32600)


# --- Task 2: lifecycle ------------------------------------------------------
class LifecycleTest(_McpBase):
    def test_initialize_response_shape(self):
        resp = self.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2024-11-05"}})
        result = resp["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["serverInfo"]["name"], "agentware-kb")
        self.assertIn("version", result["serverInfo"])
        self.assertIn("tools", result["capabilities"])

    def test_initialize_degrades_on_unsupported_protocol_version(self):
        resp = self.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "1999-01-01"}})
        # We respond with OUR supported version, not the bogus requested one.
        self.assertEqual(resp["result"]["protocolVersion"],
                         CLI.MCP_PROTOCOL_VERSION)

    def test_notifications_initialized_returns_none(self):
        resp = self.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(resp)

    def test_any_notification_returns_none(self):
        # A request without an `id` is a notification -> no envelope written.
        self.assertIsNone(self.handle({"jsonrpc": "2.0", "method": "tools/list"}))


# --- Task 3: tools/list schema parity ---------------------------------------
class ToolsListTest(_McpBase):
    def setUp(self):
        super().setUp()
        resp = self.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.tools = {t["name"]: t for t in resp["result"]["tools"]}

    def test_exactly_recall_and_query(self):
        self.assertEqual(set(self.tools), {"recall", "query"})

    def test_recall_schema_mirrors_argparse(self):
        schema = self.tools["recall"]["inputSchema"]
        props = schema["properties"]
        self.assertEqual(schema["required"], ["query"])
        # Same names + defaults as p_recall (--top-k 5, --token-budget 1500).
        self.assertEqual(props["top_k"]["default"], 5)
        self.assertEqual(props["token_budget"]["default"], 1500)
        # Strategy enum guards against drift from p_recall's choices.
        self.assertEqual(props["strategy"]["enum"], ["bm25", "bm25+acr"])
        self.assertEqual(set(props),
                         {"query", "top_k", "token_budget", "category",
                          "strategy", "as_of"})

    def test_query_schema_mirrors_argparse(self):
        props = self.tools["query"]["inputSchema"]["properties"]
        self.assertEqual(set(props), {"id", "path", "tag", "category"})


# --- Task 4: recall parity (byte-identical to `recall --format json`) -------
class RecallParityTest(_McpBase):
    def _mcp_recall_text(self, arguments):
        resp = self.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "recall", "arguments": arguments}})
        self.assertFalse(resp["result"]["isError"])
        return resp["result"]["content"][0]["text"]

    def test_recall_is_byte_identical_to_cli(self):
        for query in ("geofence arrive reminders", "bm25 ranking saturation",
                      "python stdlib runtime"):
            for strategy in ("bm25", "bm25+acr"):
                text = self._mcp_recall_text(
                    {"query": query, "strategy": strategy, "as_of": _AS_OF})
                code, out, err = run_cli(
                    ["recall", query, "--strategy", strategy,
                     "--as-of", _AS_OF, "--format", "json"], self.kdir)
                self.assertEqual(code, 0, err)
                self.assertEqual(text + "\n", out)

    def test_recall_default_strategy_matches_bare_cli(self):
        text = self._mcp_recall_text({"query": "macos timeout", "as_of": _AS_OF})
        code, out, _ = run_cli(
            ["recall", "macos timeout", "--as-of", _AS_OF, "--format", "json"],
            self.kdir)
        self.assertEqual(text + "\n", out)


# --- Task 5: query parity + invalid selectors -------------------------------
class QueryParityTest(_McpBase):
    def _mcp_query(self, arguments):
        resp = self.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "query", "arguments": arguments}})
        return resp

    def test_query_by_tag_matches_cli(self):
        resp = self._mcp_query({"tag": "bm25"})
        text = resp["result"]["content"][0]["text"]
        code, out, _ = run_cli(["query", "--tag", "bm25"], self.kdir)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text), json.loads(out))

    def test_query_by_id_matches_cli(self):
        resp = self._mcp_query({"id": "ref-bm25-ranking"})
        text = resp["result"]["content"][0]["text"]
        _, out, _ = run_cli(["query", "--id", "ref-bm25-ranking"], self.kdir)
        self.assertEqual(json.loads(text), json.loads(out))

    def test_query_by_category_matches_cli(self):
        resp = self._mcp_query({"category": "learnings"})
        text = resp["result"]["content"][0]["text"]
        _, out, _ = run_cli(["query", "--category", "learnings"], self.kdir)
        self.assertEqual(json.loads(text), json.loads(out))

    def test_no_selector_is_invalid_params(self):
        resp = self._mcp_query({})
        self.assertEqual(resp["error"]["code"], -32602)

    def test_two_selectors_is_invalid_params(self):
        resp = self._mcp_query({"tag": "bm25", "category": "references"})
        self.assertEqual(resp["error"]["code"], -32602)


# --- Task 6: tool-call error handling ---------------------------------------
class ToolErrorTest(_McpBase):
    def test_unknown_tool_name_is_structured_error(self):
        resp = self.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                            "params": {"name": "frobnicate", "arguments": {}}})
        self.assertEqual(resp["id"], 9)
        self.assertEqual(resp["error"]["code"], -32602)

    def test_recall_missing_query_is_invalid_params(self):
        resp = self.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "recall", "arguments": {}}})
        self.assertEqual(resp["error"]["code"], -32602)

    def test_recall_rejects_non_integer_top_k(self):
        resp = self.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "recall",
                                       "arguments": {"query": "x", "top_k": "lots"}}})
        self.assertEqual(resp["error"]["code"], -32602)

    def test_recall_rejects_bad_as_of(self):
        resp = self.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "recall",
                                       "arguments": {"query": "x", "as_of": "nope"}}})
        self.assertEqual(resp["error"]["code"], -32602)

    def test_handler_does_not_raise_on_bad_input(self):
        # The handler must convert errors to JSON-RPC, never propagate them.
        try:
            resp = self.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": "not-an-object"})
        except Exception as e:  # noqa: BLE001
            self.fail("handler raised instead of returning an error: %s" % e)
        self.assertEqual(resp["error"]["code"], -32602)


# --- Task 7: moat guards (read-only + no mutating tool) ---------------------
class ReadOnlyMoatTest(_McpBase):
    def _snapshot_tree(self):
        snap = {}
        for root, _dirs, files in os.walk(self.kdir):
            for name in files:
                p = os.path.join(root, name)
                with open(p, "rb") as f:
                    snap[p] = f.read()
        return snap

    def test_full_session_leaves_kb_tree_byte_identical(self):
        before = self._snapshot_tree()
        self.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05"}})
        self.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": "recall",
                                "arguments": {"query": "bm25 geofence python"}}})
        self.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                     "params": {"name": "query", "arguments": {"tag": "bm25"}}})
        self.assertEqual(before, self._snapshot_tree())

    def test_no_mutating_tool_is_registered(self):
        resp = self.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = {t["name"] for t in resp["result"]["tools"]}
        # The CLI stays the sole writer (R-KB-01): only read tools are exposed.
        self.assertEqual(names, {"recall", "query"})
        mutators = {"learn", "index", "index_add", "index-add", "decide",
                    "add", "remove", "ingest", "write", "delete", "init",
                    "set-status", "merge"}
        self.assertEqual(names & mutators, set())

    def test_unknown_methods_cannot_reach_a_writer(self):
        # Even a request naming a CLI writer verb as a method is method-not-found.
        for method in ("learn", "index/add", "decide", "tools/write"):
            resp = self.handle({"jsonrpc": "2.0", "id": 1, "method": method})
            self.assertIn("error", resp)
            self.assertEqual(resp["error"]["code"], -32601)


# --- Task 9: end-to-end subprocess over real stdio --------------------------
class EndToEndStdioTest(unittest.TestCase):
    def setUp(self):
        import tempfile, shutil
        self.kdir = tempfile.mkdtemp(prefix="agentware-mcp-e2e-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)

    def _drive(self, lines):
        env = dict(os.environ)
        env["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        env["CI"] = "true"
        payload = "".join(json.dumps(o) + "\n" for o in lines)
        proc = subprocess.run(
            [sys.executable, CLI_PATH, "mcp", "serve"],
            input=payload, capture_output=True, text=True, env=env, timeout=60)
        return proc

    def test_full_stdio_session(self):
        proc = self._drive([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "recall",
                        "arguments": {"query": "bm25 ranking", "top_k": 3}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "query", "arguments": {"tag": "bm25"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "bogus/method"},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
             "params": {"name": "nope", "arguments": {}}},
        ])
        # (6) close stdin -> exit 0 (no hang).
        self.assertEqual(proc.returncode, 0, proc.stderr)
        responses = [json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip()]
        by_id = {r.get("id"): r for r in responses}
        # notifications/initialized produced NO response line.
        self.assertNotIn(None, by_id)
        self.assertEqual(len(responses), 6)  # ids 1..6, no notification echo

        # (1) initialize advertises the handshake.
        init = by_id[1]["result"]
        self.assertEqual(init["protocolVersion"], "2024-11-05")
        self.assertEqual(init["serverInfo"]["name"], "agentware-kb")
        self.assertIn("tools", init["capabilities"])

        # (2) tools/list -> exactly recall + query.
        names = {t["name"] for t in by_id[2]["result"]["tools"]}
        self.assertEqual(names, {"recall", "query"})

        # (3) recall byte-identical to the CLI invocation.
        text = by_id[3]["result"]["content"][0]["text"]
        code, out, err = run_cli(
            ["recall", "bm25 ranking", "--top-k", "3", "--format", "json"],
            self.kdir)
        self.assertEqual(code, 0, err)
        self.assertEqual(text + "\n", out)

        # (4) query -> same records as the CLI.
        qtext = by_id[4]["result"]["content"][0]["text"]
        _, qout, _ = run_cli(["query", "--tag", "bm25"], self.kdir)
        self.assertEqual(json.loads(qtext), json.loads(qout))

        # (5) bogus method -> -32601; (6) bogus tool -> -32602; server stayed alive.
        self.assertEqual(by_id[5]["error"]["code"], -32601)
        self.assertEqual(by_id[6]["error"]["code"], -32602)

    def test_index_unchanged_after_session(self):
        ipath = os.path.join(self.kdir, "index.json")
        with open(ipath, "rb") as f:
            before = f.read()
        self._drive([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "recall", "arguments": {"query": "python bm25"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "query", "arguments": {"category": "learnings"}}},
        ])
        with open(ipath, "rb") as f:
            self.assertEqual(before, f.read())


if __name__ == "__main__":
    unittest.main()
