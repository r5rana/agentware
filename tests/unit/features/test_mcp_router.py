"""McpEnvelopeTest, McpRouterTest, McpServeLoopTest — the SERVER half of the
`agentware_lib.features.mcp` owning suite (task 99 wave 18).

Split by ADDITION from `test_mcp.py` when the owning file crossed the 600-line
`tests/**` cap. The seam is the module's own: the file it left behind asserts
what the LOOKUP decides over an index it cannot trust, and this one asserts what
the SERVER does with a request it cannot trust — the envelope it wraps, the
method it routes, and the line it writes. Ownership is unaffected: a module is
owned by exactly one file, and that file is still `test_mcp.py`.

See `tests.unit.features._mcp_support` for the shared header.
"""

from tests.unit.features._mcp_support import (  # noqa: F401
    ENTRIES, TAGS, _data, copy, io, json, mcp, os, shutil, sys, tempfile,
    unittest)


class McpEnvelopeTest(unittest.TestCase):
    """The JSON-RPC envelope builders: ids are ECHOED, never generated."""

    def test_mcpunit_an_envelope_echoes_the_request_id(self):
        self.assertEqual(mcp._mcp_envelope(7, {"result": {}}),
                         {"jsonrpc": "2.0", "id": 7, "result": {}})

    def test_mcpunit_a_string_id_is_echoed_verbatim(self):
        self.assertEqual(mcp._mcp_result("abc", {})["id"], "abc")

    def test_mcpunit_a_null_id_is_preserved(self):
        self.assertIsNone(mcp._mcp_error(None, -32700, "parse error")["id"])

    def test_mcpunit_an_error_carries_its_code_and_message(self):
        err = mcp._mcp_error(1, -32602, "bad")["error"]
        self.assertEqual(err, {"code": -32602, "message": "bad"})

    def test_mcpunit_a_result_and_an_error_are_never_both_present(self):
        self.assertNotIn("error", mcp._mcp_result(1, {}))
        self.assertNotIn("result", mcp._mcp_error(1, -1, "x"))


class McpRouterTest(unittest.TestCase):
    """`handle_mcp_request` / `_mcp_handle_tools_call` / `_mcp_initialize`."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-mcpunit-r-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.data = _data()

    def _handle(self, req):
        return mcp.handle_mcp_request(req, self.kdir, self.data)

    def test_mcpunit_a_non_object_request_is_invalid_request(self):
        self.assertEqual(self._handle(["not", "an", "object"])["error"]["code"],
                         -32600)

    def test_mcpunit_a_request_with_no_method_is_invalid_request(self):
        self.assertEqual(self._handle({"id": 1})["error"]["code"], -32600)

    def test_mcpunit_a_notification_gets_no_response(self):
        self.assertIsNone(self._handle({"method": "tools/list"}))
        self.assertIsNone(self._handle({"jsonrpc": "2.0",
                                        "method": "notifications/initialized"}))

    def test_mcpunit_a_malformed_notification_is_silent(self):
        self.assertIsNone(self._handle({"jsonrpc": "2.0"}))

    def test_mcpunit_an_unknown_method_is_method_not_found(self):
        self.assertEqual(self._handle({"id": 1, "method": "nope"})
                         ["error"]["code"], -32601)

    def test_mcpunit_initialize_echoes_a_supported_protocol_version(self):
        for version in mcp.MCP_SUPPORTED_PROTOCOL_VERSIONS:
            got = mcp._mcp_initialize({"protocolVersion": version})
            self.assertEqual(got["protocolVersion"], version)

    def test_mcpunit_initialize_degrades_on_an_unsupported_version(self):
        got = mcp._mcp_initialize({"protocolVersion": "1999-01-01"})
        self.assertEqual(got["protocolVersion"], mcp.MCP_PROTOCOL_VERSION)

    def test_mcpunit_initialize_survives_non_dict_params(self):
        self.assertEqual(mcp._mcp_initialize("nonsense")["protocolVersion"],
                         mcp.MCP_PROTOCOL_VERSION)

    def test_mcpunit_initialize_names_the_server(self):
        info = mcp._mcp_initialize({})["serverInfo"]
        self.assertEqual(info, {"name": mcp.MCP_SERVER_NAME,
                                "version": mcp.MCP_SERVER_VERSION})

    def test_mcpunit_tools_list_offers_exactly_recall_and_query(self):
        res = self._handle({"id": 1, "method": "tools/list"})["result"]
        self.assertEqual(sorted(t["name"] for t in res["tools"]),
                         ["query", "recall"])

    def test_mcpunit_no_tool_declares_a_write(self):
        # INV-2 / R-KB-01: the CLI stays the sole writer of the index, so an
        # untrusted client must have no mutating tool to call.
        names = [t["name"] for t in mcp._mcp_tool_definitions()]
        self.assertEqual(sorted(names), ["query", "recall"])

    def test_mcpunit_the_query_schema_names_exactly_the_four_selectors(self):
        query = [t for t in mcp._mcp_tool_definitions()
                 if t["name"] == "query"][0]
        self.assertEqual(sorted(query["inputSchema"]["properties"]),
                         sorted(mcp.MCP_QUERY_SELECTORS))
        self.assertFalse(query["inputSchema"]["additionalProperties"])

    def test_mcpunit_non_dict_params_on_a_tool_call_is_invalid_params(self):
        self.assertEqual(
            mcp._mcp_handle_tools_call(1, "nonsense", self.kdir, self.data)
            ["error"]["code"], -32602)

    def test_mcpunit_non_dict_arguments_are_invalid_params(self):
        # The MESSAGE is asserted, not just the code: a list falls through the
        # selector scan as "no selector given" and is refused with the SAME
        # -32602, so the code alone cannot tell the shape check from its absence.
        res = mcp._mcp_handle_tools_call(1, {"name": "query", "arguments": []},
                                         self.kdir, self.data)
        self.assertEqual(res["error"]["code"], -32602)
        self.assertIn("arguments must be an object", res["error"]["message"])

    def test_mcpunit_absent_arguments_are_treated_as_an_empty_object(self):
        # `query` with no arguments must fail the SELECTOR check (-32602 with the
        # selector message), not a TypeError on the missing key.
        res = mcp._mcp_handle_tools_call(1, {"name": "query"}, self.kdir,
                                         self.data)
        self.assertEqual(res["error"]["code"], -32602)
        self.assertIn("exactly one of", res["error"]["message"])

    def test_mcpunit_an_unknown_tool_name_is_a_structured_error(self):
        res = mcp._mcp_handle_tools_call(1, {"name": "delete_everything"},
                                         self.kdir, self.data)
        self.assertEqual(res["error"]["code"], -32602)
        self.assertIn("unknown tool", res["error"]["message"])

    def test_mcpunit_a_tool_call_hit_is_wrapped_in_a_content_block(self):
        res = self._handle({"id": 3, "method": "tools/call",
                            "params": {"name": "query",
                                       "arguments": {"id": "decide-gamma"}}})
        self.assertFalse(res["result"]["isError"])
        block = res["result"]["content"][0]
        self.assertEqual(block["type"], "text")
        self.assertEqual([e["id"] for e in json.loads(block["text"])],
                         ["decide-gamma"])

    def test_mcpunit_a_tool_that_blows_up_becomes_an_internal_error(self):
        # The server must stay alive: a tool raising anything other than an
        # argument refusal is reported as -32603 rather than killing the loop.
        original = mcp._mcp_tool_query

        def _boom(*_a, **_kw):
            raise RuntimeError("kaboom")

        mcp._mcp_tool_query = _boom
        try:
            res = mcp._mcp_handle_tools_call(
                1, {"name": "query", "arguments": {"id": "x"}}, self.kdir,
                self.data)
        finally:
            mcp._mcp_tool_query = original
        self.assertEqual(res["error"]["code"], -32603)
        self.assertIn("kaboom", res["error"]["message"])

    def test_mcpunit_no_request_shape_raises(self):
        for req in (None, 7, "string", [], {}, {"id": 1, "method": 7},
                    {"id": 1, "method": "tools/call", "params": None},
                    {"id": 1, "method": "tools/call",
                     "params": {"name": "recall", "arguments": {"query": 7}}}):
            self._handle(req)      # the assertion is that none of these raise


class McpServeLoopTest(unittest.TestCase):
    """`cmd_mcp`: the stdio loop, driven over pipes rather than a subprocess."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-mcpunit-s-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        with open(os.path.join(self.kdir, "index.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"version": 1,
                       "entries": [e for e in ENTRIES if isinstance(e, dict)],
                       "tags": TAGS}, f)
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(os.environ.pop, "AGENTWARE_KNOWLEDGE_DIR", None)

    def _serve(self, lines):
        stdin, stdout = sys.stdin, sys.stdout
        sys.stdin = io.StringIO("".join(l + "\n" for l in lines))
        sys.stdout = io.StringIO()
        try:
            rc = mcp.cmd_mcp(object())
            out = sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = stdin, stdout
        return rc, [json.loads(l) for l in out.splitlines() if l.strip()]

    def test_mcpunit_the_loop_exits_zero_on_eof(self):
        rc, responses = self._serve([])
        self.assertEqual((rc, responses), (0, []))

    def test_mcpunit_a_blank_line_is_ignored(self):
        rc, responses = self._serve(["", "   "])
        self.assertEqual((rc, responses), (0, []))

    def test_mcpunit_a_malformed_line_is_a_parse_error_with_a_null_id(self):
        _rc, responses = self._serve(["{ not json"])
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertIsNone(responses[0]["id"])

    def test_mcpunit_a_malformed_line_does_not_stop_the_session(self):
        _rc, responses = self._serve([
            "{ not json",
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})])
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[1]["id"], 2)

    def test_mcpunit_a_notification_produces_no_output_line(self):
        _rc, responses = self._serve([
            json.dumps({"jsonrpc": "2.0",
                        "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})])
        self.assertEqual([r["id"] for r in responses], [1])

    def test_mcpunit_a_query_call_reaches_the_lookup(self):
        _rc, responses = self._serve([json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "query", "arguments": {"tag": "loops"}}})])
        payload = json.loads(responses[0]["result"]["content"][0]["text"])
        self.assertEqual([e["id"] for e in payload],
                         ["learn-alpha", "decide-gamma"])

    def test_mcpunit_the_session_leaves_the_index_byte_identical(self):
        path = os.path.join(self.kdir, "index.json")
        with open(path, "rb") as f:
            before = f.read()
        self._serve([json.dumps(
            {"jsonrpc": "2.0", "id": n, "method": "tools/call",
             "params": {"name": "query", "arguments": {"category": "learnings"}}})
            for n in range(3)])
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_mcpunit_a_corrupt_index_is_rebuilt_rather_than_bricking_the_server(self):
        # `load_index` treats index.json as a DERIVED cache and rebuilds it once
        # from entry frontmatter, so a corrupt file is not a dead server — it is
        # a server over whatever the entry files say. Pinned because the loop
        # loads the index ONCE and has no second chance.
        with open(os.path.join(self.kdir, "index.json"), "w",
                  encoding="utf-8") as f:
            f.write("{ not json")
        rc, responses = self._serve([json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "query", "arguments": {"category": "learnings"}}})])
        self.assertEqual(rc, 0)
        self.assertEqual(
            json.loads(responses[0]["result"]["content"][0]["text"]), [])

    def test_mcpunit_an_unresolvable_knowledge_dir_refuses_to_serve(self):
        # Point the resolver at a path that does NOT exist rather than popping
        # the variable: popping falls through to the operator's real machine
        # config, and the refusal arm would never run.
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = os.path.join(self.kdir, "nope")
        stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc, responses = self._serve([json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})])
        finally:
            sys.stderr = stderr
        self.assertEqual(rc, 1)
        self.assertEqual(responses, [])


if __name__ == "__main__":
    unittest.main()
