"""agentware_lib.features.mcp -- the read-only MCP (Model Context Protocol) stdio server over the KB (layer: features).

Owns: the `mcp serve` command surface -- a stdlib-only (json + sys) JSON-RPC 2.0
server speaking the MCP STDIO transport that exposes exactly two READ-ONLY tools
(recall + query): the protocol/server constants (MCP_PROTOCOL_VERSION /
MCP_SUPPORTED_PROTOCOL_VERSIONS / MCP_SERVER_NAME / MCP_SERVER_VERSION /
MCP_RECALL_STRATEGIES / MCP_QUERY_SELECTORS), the JSON-RPC envelope/result/error
builders (_mcp_envelope / _mcp_result / _mcp_error), the tool descriptors
(_mcp_tool_definitions), the untrusted-arg validators (_mcp_require_int /
_mcp_require_str / _McpInvalidParams), the two READ-ONLY tool implementations
(_mcp_tool_recall / _mcp_query_lookup / _mcp_tool_query) with the two lookup
primitives the query tool folds over an UNTRUSTED index (_mcp_entry_maps, the
first-wins id/path maps; _mcp_entries_for_ids, the tag arm's id -> entry
resolution, which skips an id the corpus does not carry), the request router
(_mcp_handle_tools_call / _mcp_initialize / handle_mcp_request), the stdio serve
loop (cmd_mcp), and the `mcp` parser registrar (register_mcp).

Does NOT own: the canonical recall payload builder
(agentware_lib.retrieval.recallcmd.build_recall_payload, imported DOWN as _recall --
the single source of truth shared with the CLI `recall` verb); the KB index loader
(agentware_lib.knowledge.index.load_index, as index); the knowledge-dir resolver
(agentware_lib.core.config.require_knowledge_dir, as config); the date validator
(agentware_lib.core.dates._is_valid_date, as dates). NO write tool is registered, so
the CLI stays the sole writer of the index (R-KB-01) and an untrusted client cannot
mutate the KB (INV-2).

Layer deps: features.mcp -> core (config, dates) + knowledge (index) + retrieval
(recallcmd), all DOWNWARD (none of them imports mcp, so no cycle). A LEAF: nothing
imports it (fan-in 0); the scripts/agentware entrypoint re-exports its _EXPORTS BY
NAME so the entrypoint-facade oracle (test_mcp_server: CLI.handle_mcp_request /
CLI._mcp_error / CLI.MCP_PROTOCOL_VERSION) resolves them; register_mcp is NOT in
_EXPORTS -- it is declared in this module's `__verbs__` and the assembler resolves
it from there.

Determinism: read-only (INV-2). The index is loaded ONCE at startup and never
written; request ids are echoed, never generated (no RNG); tool-call arguments are
untrusted input and are validated (R-SEC-02). Because stdio needs no network module,
test_cli_imports_no_network_modules + test_cli_imports_are_stdlib_only stay green.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.knowledge.index as index
import agentware_lib.retrieval.recallcmd as _recall


# A stdlib-only (`json` + `sys`) JSON-RPC 2.0 server speaking the MCP STDIO
# transport: newline-delimited JSON-RPC messages on stdin/stdout (no HTTP, no
# socket, no Content-Length framing — that's the HTTP/SSE transport, which is a
# future, separately-subprocess'd tier; see the plan's non-goals). The server is
# READ-ONLY by construction: it exposes exactly two tools — `recall` and `query`
# — that delegate to the existing read functions (`build_recall_payload` /
# `cmd_query`'s lookup). NO write tool is registered, so the CLI stays the sole
# writer of the index (R-KB-01) and an untrusted client cannot mutate the KB
# (INV-2). Tool-call arguments are untrusted input and are validated (R-SEC-02).
# Because stdio needs no network module, `test_cli_imports_no_network_modules`
# and `test_cli_imports_are_stdlib_only` stay green by construction.

# Pinned MCP protocol version (the widely-supported stable revision). We echo a
# client's requested version when we support it, else degrade to ours.
MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
MCP_SERVER_NAME = "agentware-kb"
MCP_SERVER_VERSION = "0.1.0"

# Read-only tools exposed over MCP. Schemas MIRROR the `recall`/`query` argparse
# definitions (same names/defaults/enum) so behavior is identical across surfaces.
MCP_RECALL_STRATEGIES = ("bm25", "bm25+acr")
MCP_QUERY_SELECTORS = ("id", "path", "tag", "category")


class _McpInvalidParams(Exception):
    """Raised by a tool when its (untrusted) arguments fail validation -> -32602."""


def _mcp_envelope(req_id, body):
    """Wrap a JSON-RPC 2.0 response, echoing the request id (never generated)."""
    resp = {"jsonrpc": "2.0", "id": req_id}
    resp.update(body)
    return resp


def _mcp_result(req_id, result):
    return _mcp_envelope(req_id, {"result": result})


def _mcp_error(req_id, code, message):
    return _mcp_envelope(req_id, {"error": {"code": code, "message": message}})


def _mcp_tool_definitions():
    """The two read-only tool descriptors with JSON Schemas mirroring the CLI."""
    return [
        {
            "name": "recall",
            "description": (
                "Read-only deterministic BM25 ranked retrieval over the knowledge "
                "base, token-budgeted. Byte-identical to `agentware recall "
                "--format json`. Never mutates the KB."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "free-text query to rank KB entries against",
                    },
                    "top_k": {
                        "type": "integer", "minimum": 0, "default": 5,
                        "description": "max entries to return before budgeting",
                    },
                    "token_budget": {
                        "type": "integer", "minimum": 0, "default": 1500,
                        "description": "trim so cumulative est. tokens <= budget",
                    },
                    "category": {
                        "type": "string",
                        "description": "restrict ranking to a single category",
                    },
                    "strategy": {
                        "type": "string", "enum": list(MCP_RECALL_STRATEGIES),
                        "description": "ranking strategy (default: the gated default)",
                    },
                    "as_of": {
                        "type": "string",
                        "description": "YYYY-MM-DD date for ACR freshness decay "
                                       "(default: today; PIN it for reproducible runs)",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "query",
            "description": (
                "Read-only lookup of KB entries by exactly one of id/path/tag/"
                "category. Same records as `agentware query`. Never mutates the KB."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "look up a single entry by id"},
                    "path": {"type": "string", "description": "look up a single entry by path"},
                    "tag": {"type": "string", "description": "list all entries carrying a tag"},
                    "category": {"type": "string", "description": "list all entries in a category"},
                },
                "additionalProperties": False,
            },
        },
    ]


def _mcp_require_int(arguments, key, default):
    val = arguments.get(key, default)
    # JSON booleans parse to Python bool (an int subclass) — reject explicitly.
    if isinstance(val, bool) or not isinstance(val, int):
        raise _McpInvalidParams("%r must be an integer" % key)
    if val < 0:
        raise _McpInvalidParams("%r must be >= 0" % key)
    return val


def _mcp_require_str(arguments, key, default=None):
    val = arguments.get(key, default)
    if val is None:
        return None
    if not isinstance(val, str):
        raise _McpInvalidParams("%r must be a string" % key)
    return val


def _mcp_tool_recall(kdir, data, arguments):
    """Validate untrusted args, then build the canonical recall payload (read-only)."""
    query = arguments.get("query")
    if not isinstance(query, str):
        raise _McpInvalidParams("'query' is required and must be a string")
    top_k = _mcp_require_int(arguments, "top_k", 5)
    # 1500, matching the CLI default (surface-union adjudication, winner local).
    # The two surfaces are asserted BYTE-IDENTICAL by test_mcp_server's parity
    # test, so a different default here is not a smaller divergence than a
    # different field — it is the same divergence, in the value instead of the key.
    token_budget = _mcp_require_int(arguments, "token_budget", 1500)
    category = _mcp_require_str(arguments, "category")
    strategy = _mcp_require_str(arguments, "strategy")
    if strategy is not None and strategy not in MCP_RECALL_STRATEGIES:
        raise _McpInvalidParams(
            "'strategy' must be one of %s" % (list(MCP_RECALL_STRATEGIES),))
    as_of = _mcp_require_str(arguments, "as_of")
    if as_of is not None and not dates._is_valid_date(as_of):
        raise _McpInvalidParams("'as_of' must be a YYYY-MM-DD date")
    payload, _notice = _recall.build_recall_payload(
        kdir, data, query, top_k=top_k, token_budget=token_budget,
        category=category, strategy=strategy, as_of=as_of)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _mcp_entry_maps(entries):
    """FIRST-WINS id->entry and path->entry maps over an index entry list.

    The index is untrusted input here — it is a file on disk that a client can
    neither see nor validate — so a non-dict member is skipped rather than
    raising, and a duplicate id or path keeps the FIRST entry, which is the
    order `cmd_query` reports. The guards are `is not None` and not truthiness
    on purpose: an entry whose id is the EMPTY STRING is a key like any other,
    while an entry that omits the field entirely is not.
    """
    by_id, by_path = {}, {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        if eid is not None and eid not in by_id:
            by_id[eid] = e
        path = e.get("path")
        if path is not None and path not in by_path:
            by_path[path] = e
    return by_id, by_path


def _mcp_entries_for_ids(by_id, ids):
    """The entries named by an id list, in the list's order, skipping ids the
    corpus does not carry — a `tags` map is a file on disk that can name an id
    no entry has (a removed entry, a hand-edit), and a client asking for a tag
    gets the entries that exist rather than an error about the ones that do not.
    """
    out = []
    for eid in ids:
        hit = by_id.get(eid)
        if hit is not None:
            out.append(hit)
    return out


def _mcp_query_lookup(data, selector, value):
    """Replicate cmd_query's id/path/tag/category dispatch (read-only). Returns list."""
    entries = data.get("entries") or []
    tags_map = data.get("tags") or {}
    by_id, by_path = _mcp_entry_maps(entries)
    results = []
    if selector == "id":
        hit = by_id.get(value)
        if hit is not None:
            results = [hit]
    elif selector == "path":
        hit = by_path.get(value)
        if hit is not None:
            results = [hit]
    elif selector == "tag":
        results = _mcp_entries_for_ids(by_id, tags_map.get(value, []))
    elif selector == "category":
        results = [e for e in entries
                   if isinstance(e, dict) and e.get("category") == value]
    return results


def _mcp_tool_query(kdir, data, arguments):
    """Validate untrusted args: exactly one selector, then dispatch (read-only)."""
    present = [(k, arguments[k]) for k in MCP_QUERY_SELECTORS if k in arguments]
    if len(present) != 1:
        raise _McpInvalidParams(
            "exactly one of %s must be provided" % (list(MCP_QUERY_SELECTORS),))
    selector, value = present[0]
    if not isinstance(value, str):
        raise _McpInvalidParams("%r must be a string" % selector)
    results = _mcp_query_lookup(data, selector, value)
    # Same freshness surface the CLI's `query` emits, through the SAME helper.
    # These two are asserted byte-identical, so a field present on one and absent
    # on the other is not a smaller divergence than a different value — and the
    # LIVE-anchored abs_path is the one a caller most needs, since an MCP client
    # does not share this process's cwd at all.
    from agentware_lib.knowledge.graph import _entry_with_freshness
    results = [_entry_with_freshness(e, kdir, dates._today())
               for e in results]
    return json.dumps(results, ensure_ascii=False, indent=2)


def _mcp_handle_tools_call(req_id, params, kdir, data):
    if not isinstance(params, dict):
        return _mcp_error(req_id, -32602, "params must be an object")
    name = params.get("name")
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return _mcp_error(req_id, -32602, "tool arguments must be an object")
    try:
        if name == "recall":
            text = _mcp_tool_recall(kdir, data, arguments)
        elif name == "query":
            text = _mcp_tool_query(kdir, data, arguments)
        else:
            return _mcp_error(req_id, -32602, "unknown tool: %r" % (name,))
    except _McpInvalidParams as e:
        return _mcp_error(req_id, -32602, str(e))
    except Exception as e:  # a tool blew up -> structured error, server stays alive
        return _mcp_error(req_id, -32603, "tool execution error: %s" % e)
    return _mcp_result(req_id, {
        "content": [{"type": "text", "text": text}],
        "isError": False,
    })


def _mcp_initialize(params):
    requested = None
    if isinstance(params, dict):
        requested = params.get("protocolVersion")
    version = requested if requested in MCP_SUPPORTED_PROTOCOL_VERSIONS \
        else MCP_PROTOCOL_VERSION
    return {
        "protocolVersion": version,
        "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
        "capabilities": {"tools": {}},
    }


def handle_mcp_request(req, kdir, data):
    """Handle ONE parsed JSON-RPC request dict. Returns a response dict, or None
    for notifications (no `id`). Read-only over (kdir, data). Pure given inputs —
    request ids are echoed, never generated (no RNG)."""
    if not isinstance(req, dict):
        return _mcp_error(None, -32600, "invalid request: not a JSON object")
    is_notification = "id" not in req
    req_id = req.get("id")
    method = req.get("method")
    if not isinstance(method, str):
        if is_notification:
            return None
        return _mcp_error(req_id, -32600, "invalid request: missing method")
    # Notifications (incl. notifications/initialized) get no response.
    if is_notification or method.startswith("notifications/"):
        return None
    if method == "initialize":
        return _mcp_result(req_id, _mcp_initialize(req.get("params") or {}))
    if method == "tools/list":
        return _mcp_result(req_id, {"tools": _mcp_tool_definitions()})
    if method == "tools/call":
        return _mcp_handle_tools_call(req_id, req.get("params") or {}, kdir, data)
    return _mcp_error(req_id, -32601, "method not found: %s" % method)


def cmd_mcp(args):
    """Serve the read-only KB over MCP stdio (JSON-RPC 2.0, newline-delimited).

    Reads one JSON-RPC message per stdin line, writes one response line per
    non-notification. Malformed JSON -> a parse-error (-32700) envelope with a
    null id. Exits 0 cleanly on stdin EOF (no hang). Read-only: the index is
    loaded ONCE at startup and never written (INV-2).
    """
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    data, err = index.load_index(kdir)
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except (ValueError, TypeError):
            resp = _mcp_error(None, -32700, "parse error")
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()
            continue
        resp = handle_mcp_request(req, kdir, data)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()
    return 0


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("mcp", 1900, "register_mcp"),
)


def register_mcp(sub, fmt_parent):
    # mcp --------------------------------------------------------------------
    p_mcp = sub.add_parser(
        "mcp", help="read-only Model Context Protocol server over the KB (stdio)")
    mcp_sub = p_mcp.add_subparsers(dest="mcp_command", metavar="<subcommand>")
    mcp_sub.required = True
    p_mcp_serve = mcp_sub.add_parser(
        "serve",
        help="speak MCP (JSON-RPC 2.0) over stdio; exposes read-only recall+query")
    p_mcp_serve.set_defaults(func=cmd_mcp)


_EXPORTS = [
    "MCP_PROTOCOL_VERSION",
    "MCP_SUPPORTED_PROTOCOL_VERSIONS",
    "MCP_SERVER_NAME",
    "MCP_SERVER_VERSION",
    "MCP_RECALL_STRATEGIES",
    "MCP_QUERY_SELECTORS",
    "_McpInvalidParams",
    "_mcp_envelope",
    "_mcp_result",
    "_mcp_error",
    "_mcp_tool_definitions",
    "_mcp_require_int",
    "_mcp_require_str",
    "_mcp_tool_recall",
    "_mcp_query_lookup",
    "_mcp_tool_query",
    "_mcp_handle_tools_call",
    "_mcp_initialize",
    "handle_mcp_request",
    "cmd_mcp",
]
