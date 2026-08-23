"""Owning unit file for `agentware_lib.features.mcp` — task 99 wave 18.

The module carried a `test-ownership` row. It is not untested — `tests/integration/
test_mcp_server.py` drives a real `mcp serve` subprocess and pins the JSON-RPC
framing, the lifecycle and byte-for-byte parity with the `recall`/`query` CLI —
but that suite's subject is the SERVER, reached through a pipe and a real
knowledge base. Nothing reddened for the decisions the lookup makes over an index
it cannot trust: which entry wins a duplicate id, whether an empty-string id is a
key, what a `tags` map naming a missing entry produces.

(The file name is forced: the ownership gate requires exactly
`tests/unit/features/test_mcp.py`, which IS a `-k` prefix of `test_mcp_server`.
Every method here therefore carries the unique `test_mcpunit_` prefix.)

What is asserted here is the module's PROMISE, not its shape:

  * THE INDEX IS UNTRUSTED INPUT. It is a file on disk that the CLIENT can
    neither see nor validate, so a non-dict member, a null id, a null path and a
    missing key are all skipped rather than raising — a raise here is a dead
    server for every later request, since the index is loaded ONCE at startup.
  * FIRST WINS, on both maps. Two entries under one id is a corrupt corpus, and
    the answer must be the same one `cmd_query` reports rather than whichever
    happened to be written last.
  * AN EMPTY-STRING ID IS A KEY; AN ABSENT ONE IS NOT. The guard is `is not
    None` and not truthiness, and those differ for exactly the entry a hand-edit
    is most likely to produce.
  * A TAG NAMING AN ID THE CORPUS DOES NOT CARRY YIELDS THE ENTRIES THAT EXIST.
    Not an error, not a `None` in the result list — a client asking for a tag
    gets the entries behind it.
  * A SELECTOR OUTSIDE THE CLOSED SET RETURNS NOTHING, because `_mcp_tool_query`
    validates the selector and this function must still be total if it is ever
    called from anywhere else.
  * THE LOOKUP IS READ-ONLY (INV-2) AND HANDS BACK THE ENTRIES THEMSELVES, so a
    caller that mutated one would corrupt the loaded index for the whole session
    — the entries are asserted to be the same objects, and the corpus is
    asserted unchanged after every lookup.
  * UNTRUSTED ARGUMENTS ARE REFUSED WITH `-32602`, NEVER WITH A TRACEBACK: no
    selector, two selectors, a non-string value, a bool where an int belongs.

Hermetic: pure functions are driven directly; the two that need a knowledge dir
get a fresh mkdtemp. Nothing here reads the operator's KB.
"""

from tests.unit.features._mcp_support import (  # noqa: F401
    ENTRIES, TAGS, _data, copy, io, json, mcp, os, shutil, sys, tempfile,
    unittest)


class McpEntryMapsTest(unittest.TestCase):
    """`_mcp_entry_maps`: the first-wins id/path index over untrusted entries."""

    def test_mcpunit_an_empty_entry_list_gives_two_empty_maps(self):
        self.assertEqual(mcp._mcp_entry_maps([]), ({}, {}))

    def test_mcpunit_a_non_dict_member_is_skipped_rather_than_raising(self):
        by_id, by_path = mcp._mcp_entry_maps(["str", ["l"], None, 7])
        self.assertEqual((by_id, by_path), ({}, {}))

    def test_mcpunit_the_first_entry_under_an_id_wins(self):
        by_id, _ = mcp._mcp_entry_maps(copy.deepcopy(ENTRIES))
        self.assertEqual(by_id["learn-alpha"]["summary"], "FIRST under this id")

    def test_mcpunit_the_first_entry_at_a_path_wins(self):
        _, by_path = mcp._mcp_entry_maps(copy.deepcopy(ENTRIES))
        self.assertEqual(by_path["learnings/alpha.md"]["id"], "learn-alpha")

    def test_mcpunit_an_empty_string_id_is_a_key(self):
        by_id, _ = mcp._mcp_entry_maps(copy.deepcopy(ENTRIES))
        self.assertIn("", by_id)

    def test_mcpunit_an_empty_string_path_is_a_key(self):
        _, by_path = mcp._mcp_entry_maps(copy.deepcopy(ENTRIES))
        self.assertIn("", by_path)

    def test_mcpunit_a_null_id_is_not_a_key(self):
        by_id, _ = mcp._mcp_entry_maps(copy.deepcopy(ENTRIES))
        self.assertNotIn(None, by_id)

    def test_mcpunit_a_null_path_is_not_a_key(self):
        _, by_path = mcp._mcp_entry_maps(copy.deepcopy(ENTRIES))
        self.assertNotIn(None, by_path)

    def test_mcpunit_an_entry_missing_one_key_still_reaches_the_other_map(self):
        by_id, by_path = mcp._mcp_entry_maps(copy.deepcopy(ENTRIES))
        self.assertIn("ref-no-path", by_id)
        self.assertIn("references/no-id.md", by_path)

    def test_mcpunit_the_maps_hold_the_entries_themselves(self):
        entries = copy.deepcopy(ENTRIES)
        by_id, by_path = mcp._mcp_entry_maps(entries)
        self.assertIs(by_id["learn-alpha"], entries[0])
        self.assertIs(by_path["learnings/alpha.md"], entries[0])

    def test_mcpunit_building_the_maps_does_not_touch_the_corpus(self):
        entries = copy.deepcopy(ENTRIES)
        mcp._mcp_entry_maps(entries)
        self.assertEqual(entries, ENTRIES)


class McpEntriesForIdsTest(unittest.TestCase):
    """`_mcp_entries_for_ids`: the tag arm's id -> entry resolution."""

    def setUp(self):
        self.by_id, _ = mcp._mcp_entry_maps(copy.deepcopy(ENTRIES))

    def test_mcpunit_an_empty_id_list_yields_nothing(self):
        self.assertEqual(mcp._mcp_entries_for_ids(self.by_id, []), [])

    def test_mcpunit_ids_are_resolved_in_the_lists_order(self):
        got = mcp._mcp_entries_for_ids(self.by_id,
                                       ["decide-gamma", "learn-alpha"])
        self.assertEqual([e["id"] for e in got],
                         ["decide-gamma", "learn-alpha"])

    def test_mcpunit_an_id_the_corpus_does_not_carry_is_skipped(self):
        got = mcp._mcp_entries_for_ids(self.by_id, ["nobody", "learn-alpha"])
        self.assertEqual([e["id"] for e in got], ["learn-alpha"])

    def test_mcpunit_a_missing_id_never_becomes_a_none_in_the_result(self):
        got = mcp._mcp_entries_for_ids(self.by_id, ["nobody"])
        self.assertEqual(got, [])
        self.assertNotIn(None, got)

    def test_mcpunit_a_repeated_id_is_returned_twice(self):
        # The tag map is the caller's data; de-duplicating it here would be this
        # function deciding something it was not asked to decide.
        got = mcp._mcp_entries_for_ids(self.by_id, ["learn-alpha"] * 2)
        self.assertEqual(len(got), 2)


class McpQueryLookupTest(unittest.TestCase):
    """`_mcp_query_lookup`: the id/path/tag/category dispatch."""

    def setUp(self):
        self.data = _data()

    def _ids(self, selector, value):
        return [e.get("id") for e in
                mcp._mcp_query_lookup(self.data, selector, value)]

    def test_mcpunit_an_id_hit_returns_exactly_one_entry(self):
        self.assertEqual(self._ids("id", "decide-gamma"), ["decide-gamma"])

    def test_mcpunit_an_id_miss_returns_the_empty_list(self):
        self.assertEqual(self._ids("id", "nobody"), [])

    def test_mcpunit_a_duplicate_id_resolves_to_the_first_entry(self):
        hit = mcp._mcp_query_lookup(self.data, "id", "learn-alpha")
        self.assertEqual(hit[0]["summary"], "FIRST under this id")

    def test_mcpunit_a_path_hit_returns_exactly_one_entry(self):
        self.assertEqual(self._ids("path", "decisions/gamma.md"),
                         ["decide-gamma"])

    def test_mcpunit_a_duplicate_path_resolves_to_the_first_entry(self):
        hit = mcp._mcp_query_lookup(self.data, "path", "learnings/alpha.md")
        self.assertEqual(hit[0]["summary"], "FIRST under this id")

    def test_mcpunit_a_tag_returns_its_entries_in_the_maps_order(self):
        self.assertEqual(self._ids("tag", "loops"),
                         ["learn-alpha", "decide-gamma"])

    def test_mcpunit_a_tag_naming_a_missing_entry_returns_the_rest(self):
        self.assertEqual(self._ids("tag", "ghost"), ["learn-alpha"])

    def test_mcpunit_an_empty_tag_list_returns_nothing(self):
        self.assertEqual(self._ids("tag", "empty"), [])

    def test_mcpunit_an_unknown_tag_returns_nothing(self):
        self.assertEqual(self._ids("tag", "nope"), [])

    def test_mcpunit_a_category_returns_every_entry_in_it(self):
        self.assertEqual(self._ids("category", "learnings"),
                         ["learn-alpha", "learn-alpha", "learn-beta"])

    def test_mcpunit_a_category_lookup_skips_non_dict_entries(self):
        self.assertEqual(self._ids("category", "decisions"), ["decide-gamma"])

    def test_mcpunit_an_unknown_category_returns_nothing(self):
        self.assertEqual(self._ids("category", "nope"), [])

    def test_mcpunit_an_unknown_selector_returns_nothing(self):
        # The VALUE is a real category, deliberately: a dispatch whose last arm
        # became an `else` would answer an unknown selector with the category
        # results, and a value nothing matches cannot tell the two apart.
        self.assertEqual(mcp._mcp_query_lookup(self.data, "sideways",
                                               "learnings"), [])
        self.assertEqual(mcp._mcp_query_lookup(self.data, None, "learnings"), [])
        self.assertEqual(self._ids("category", "learnings"),
                         ["learn-alpha", "learn-alpha", "learn-beta"])

    def test_mcpunit_an_index_with_no_entries_key_answers_every_selector(self):
        for selector in mcp.MCP_QUERY_SELECTORS:
            self.assertEqual(mcp._mcp_query_lookup({}, selector, "x"), [])

    def test_mcpunit_a_null_entries_or_tags_value_is_treated_as_empty(self):
        data = {"entries": None, "tags": None}
        for selector in mcp.MCP_QUERY_SELECTORS:
            self.assertEqual(mcp._mcp_query_lookup(data, selector, "x"), [])

    def test_mcpunit_the_lookup_returns_the_entry_itself_not_a_copy(self):
        hit = mcp._mcp_query_lookup(self.data, "id", "learn-beta")
        self.assertIs(hit[0], self.data["entries"][2])

    def test_mcpunit_the_lookup_never_mutates_the_corpus(self):
        before = json.dumps(self.data, sort_keys=True, default=str)
        for selector in mcp.MCP_QUERY_SELECTORS:
            mcp._mcp_query_lookup(self.data, selector, "learn-alpha")
        self.assertEqual(json.dumps(self.data, sort_keys=True, default=str),
                         before)


class McpArgumentValidatorTest(unittest.TestCase):
    """`_mcp_require_int` / `_mcp_require_str`: untrusted argument coercion."""

    def test_mcpunit_an_absent_int_takes_the_default(self):
        self.assertEqual(mcp._mcp_require_int({}, "top_k", 5), 5)

    def test_mcpunit_an_int_is_returned(self):
        self.assertEqual(mcp._mcp_require_int({"top_k": 9}, "top_k", 5), 9)

    def test_mcpunit_a_bool_is_not_an_int(self):
        # JSON `true` parses to a Python bool, which IS an int subclass.
        with self.assertRaises(mcp._McpInvalidParams):
            mcp._mcp_require_int({"top_k": True}, "top_k", 5)

    def test_mcpunit_a_string_int_is_refused(self):
        with self.assertRaises(mcp._McpInvalidParams):
            mcp._mcp_require_int({"top_k": "9"}, "top_k", 5)

    def test_mcpunit_an_absent_string_takes_the_default(self):
        self.assertIsNone(mcp._mcp_require_str({}, "category"))
        self.assertEqual(mcp._mcp_require_str({}, "category", "x"), "x")

    def test_mcpunit_a_non_string_is_refused(self):
        with self.assertRaises(mcp._McpInvalidParams):
            mcp._mcp_require_str({"category": 7}, "category")

    def test_mcpunit_an_explicit_null_takes_the_default(self):
        self.assertIsNone(mcp._mcp_require_str({"category": None}, "category"))


class McpToolQueryTest(unittest.TestCase):
    """`_mcp_tool_query`: selector validation + the freshness-stamped payload."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-mcpunit-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.data = _data()

    def test_mcpunit_no_selector_is_invalid_params(self):
        with self.assertRaises(mcp._McpInvalidParams):
            mcp._mcp_tool_query(self.kdir, self.data, {})

    def test_mcpunit_two_selectors_is_invalid_params(self):
        with self.assertRaises(mcp._McpInvalidParams):
            mcp._mcp_tool_query(self.kdir, self.data,
                                {"id": "a", "category": "b"})

    def test_mcpunit_an_unknown_argument_key_is_not_a_selector(self):
        with self.assertRaises(mcp._McpInvalidParams):
            mcp._mcp_tool_query(self.kdir, self.data, {"nope": "x"})

    def test_mcpunit_a_non_string_selector_value_is_invalid_params(self):
        with self.assertRaises(mcp._McpInvalidParams):
            mcp._mcp_tool_query(self.kdir, self.data, {"id": 7})

    def test_mcpunit_a_hit_is_returned_as_indented_json(self):
        text = mcp._mcp_tool_query(self.kdir, self.data, {"id": "decide-gamma"})
        payload = json.loads(text)
        self.assertEqual([e["id"] for e in payload], ["decide-gamma"])
        self.assertIn("\n", text)

    def test_mcpunit_a_miss_is_an_empty_json_list_not_an_error(self):
        self.assertEqual(
            json.loads(mcp._mcp_tool_query(self.kdir, self.data,
                                           {"id": "nobody"})), [])

    def test_mcpunit_every_result_carries_the_freshness_surface(self):
        payload = json.loads(
            mcp._mcp_tool_query(self.kdir, self.data, {"tag": "loops"}))
        for entry in payload:
            for field in ("abs_path", "created", "last_verified", "age_days"):
                self.assertIn(field, entry)

    def test_mcpunit_the_abs_path_is_anchored_at_the_served_knowledge_dir(self):
        payload = json.loads(
            mcp._mcp_tool_query(self.kdir, self.data, {"id": "decide-gamma"}))
        self.assertTrue(payload[0]["abs_path"].startswith(self.kdir))

    def test_mcpunit_stamping_does_not_mutate_the_loaded_index(self):
        # The index is loaded ONCE at startup; a stamp written back onto the
        # entry would leak into every later request in the session.
        before = json.dumps(self.data, sort_keys=True, default=str)
        mcp._mcp_tool_query(self.kdir, self.data, {"category": "learnings"})
        self.assertEqual(json.dumps(self.data, sort_keys=True, default=str),
                         before)


class McpExportsTest(unittest.TestCase):
    """The module's published surface."""

    def test_mcpunit_every_exported_name_exists(self):
        for name in mcp._EXPORTS:
            self.assertTrue(hasattr(mcp, name),
                            "_EXPORTS names %r, which the module does not define"
                            % name)

    def test_mcpunit_the_registrar_is_declared_not_exported(self):
        self.assertIn("mcp", [v[0] for v in mcp.__verbs__])
        self.assertNotIn("register_mcp", mcp._EXPORTS)

    def test_mcpunit_the_pinned_protocol_version_is_supported(self):
        self.assertIn(mcp.MCP_PROTOCOL_VERSION,
                      mcp.MCP_SUPPORTED_PROTOCOL_VERSIONS)


if __name__ == "__main__":
    unittest.main()
