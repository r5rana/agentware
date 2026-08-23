"""Owning unit file for `agentware_lib.knowledge.graph` (task 99 wave 15).

This module is the KB's declared-edge graph plus the `query` verb — the read
path every other reader trusts to be deterministic, cycle-safe and read-only.
It had coverage before this file existed and none of it was OWNERSHIP:
`tests/unit/knowledge/test_dependency_graph.py` is named for the FEATURE, drives
the graph through `load_cli()` and the CLI, and so does not redden when THIS
module alone breaks.

What is asserted here that the feature file does not reach:

  * `_lookup_maps` FIRST-WINS, per map and independently. A duplicate id and a
    duplicate path are separate claims, and an entry can carry one without the
    other. A malformed (non-dict) row degrades to "not in the map" rather than
    raising — the index is reachable from a teammate's shard, so a poisoned row
    is an input, not an accident.
  * `_direct_results` returns None ONLY when no direct mode was selected, and
    `[]` — never None — when a selected mode matched nothing. That distinction
    is what stops `query --id nope` from falling through to a graph traversal.
  * `_graph_adjacency`'s three directions come from ONE builder, and the union
    mode is a genuine merge (sorted, de-duplicated) rather than a concatenation.
  * `_traverse_results` resolves modes in PARSER ORDER, and drops ids that do
    not resolve to an entry (a `relates` edge may name a target that was never
    indexed).
  * `cmd_query` NEVER mutates the loaded index — the MCP server reuses it across
    calls — and appends freshness to a COPY.

Stdlib `unittest` only; every KB is a throwaway tempdir (R-LOC-03).

Runner:  python3 -m unittest tests.unit.knowledge.test_graph -v
"""

from tests._paths import TESTS_DIR

import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.core.config as config                     # noqa: E402
import agentware_lib.knowledge.graph as G                      # noqa: E402
import agentware_lib.knowledge.index as index                  # noqa: E402


class _Args(object):
    """A stand-in for the argparse Namespace `cmd_query` reads."""

    def __init__(self, **kw):
        base = dict(id=None, path=None, tag=None, category=None,
                    depends_on=None, impact=None, relates=None, depth=1)
        base.update(kw)
        for k, v in base.items():
            setattr(self, k, v)


def _sparse(**kw):
    """A Namespace carrying ONLY the given attributes — the shape the MCP server
    hands `cmd_query`, which is why every graph read goes through getattr."""
    ns = type("Sparse", (object,), {})()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# --- the lookup maps ---------------------------------------------------------

class LookupMapsTests(unittest.TestCase):
    def test_first_wins_on_duplicate_id(self):
        first = {"id": "d", "summary": "first"}
        second = {"id": "d", "summary": "second"}
        by_id, _ = G._lookup_maps([first, second])
        self.assertIs(by_id["d"], first)

    def test_first_wins_on_duplicate_path(self):
        first = {"id": "a", "path": "p.md"}
        second = {"id": "b", "path": "p.md"}
        _, by_path = G._lookup_maps([first, second])
        self.assertIs(by_path["p.md"], first)

    def test_duplicate_path_does_not_suppress_the_second_id(self):
        # The two maps are INDEPENDENT: a second row losing the path race must
        # still be reachable by its own distinct id.
        rows = [{"id": "a", "path": "p.md"}, {"id": "b", "path": "p.md"}]
        by_id, by_path = G._lookup_maps(rows)
        self.assertEqual(sorted(by_id), ["a", "b"])
        self.assertIs(by_path["p.md"], rows[0])

    def test_non_dict_rows_are_skipped_not_raised(self):
        by_id, by_path = G._lookup_maps(["str", 7, None, [], {"id": "a", "path": "p"}])
        self.assertEqual(list(by_id), ["a"])
        self.assertEqual(list(by_path), ["p"])

    def test_idless_row_still_registers_its_path(self):
        by_id, by_path = G._lookup_maps([{"path": "p.md"}])
        self.assertEqual(by_id, {})
        self.assertEqual(list(by_path), ["p.md"])

    def test_pathless_row_still_registers_its_id(self):
        by_id, by_path = G._lookup_maps([{"id": "a"}])
        self.assertEqual(list(by_id), ["a"])
        self.assertEqual(by_path, {})

    def test_empty_string_id_is_a_key_none_is_not(self):
        # The guard is `is not None`, not truthiness: an empty id is malformed
        # but PRESENT, and dropping it silently would make two different
        # corruptions look identical.
        by_id, _ = G._lookup_maps([{"id": ""}, {"id": None}])
        self.assertEqual(list(by_id), [""])


# --- the four direct modes ---------------------------------------------------

class DirectResultsTests(unittest.TestCase):
    ENTRIES = [
        {"id": "a", "path": "x.md", "category": "learnings"},
        {"id": "b", "path": "y.md", "category": "references"},
        {"id": "c", "path": "z.md", "category": "references"},
    ]
    TAGS = {"t": ["a", "c"], "ghost": ["a", "not-an-entry"]}

    def _call(self, args):
        by_id, by_path = G._lookup_maps(self.ENTRIES)
        return G._direct_results(args, self.ENTRIES, self.TAGS, by_id, by_path)

    def test_no_direct_mode_returns_none(self):
        self.assertIsNone(self._call(_Args()))

    def test_selected_mode_that_misses_returns_empty_not_none(self):
        # The load-bearing distinction: [] terminates the dispatch, None falls
        # through to the graph arms.
        for args in (_Args(id="nope"), _Args(path="nope.md"),
                     _Args(tag="nope"), _Args(category="nope")):
            self.assertEqual(self._call(args), [])

    def test_id_and_path_return_the_entry_itself(self):
        self.assertIs(self._call(_Args(id="b"))[0], self.ENTRIES[1])
        self.assertIs(self._call(_Args(path="z.md"))[0], self.ENTRIES[2])

    def test_tag_preserves_the_tag_maps_order(self):
        self.assertEqual([e["id"] for e in self._call(_Args(tag="t"))], ["a", "c"])

    def test_tag_drops_an_id_that_is_not_an_entry(self):
        self.assertEqual([e["id"] for e in self._call(_Args(tag="ghost"))], ["a"])

    def test_category_scans_entries_and_skips_non_dicts(self):
        entries = list(self.ENTRIES) + ["poison", None]
        by_id, by_path = G._lookup_maps(entries)
        got = G._direct_results(_Args(category="references"), entries, self.TAGS,
                                by_id, by_path)
        self.assertEqual([e["id"] for e in got], ["b", "c"])

    def test_id_beats_every_later_mode(self):
        # argparse forbids two modes, but a hand-built namespace does not, and
        # the resolution order must not depend on dict iteration.
        got = self._call(_Args(id="a", path="y.md", tag="t", category="references"))
        self.assertEqual([e["id"] for e in got], ["a"])

    def test_path_beats_tag_and_category(self):
        got = self._call(_Args(path="y.md", tag="t", category="references"))
        self.assertEqual([e["id"] for e in got], ["b"])

    def test_tag_beats_category(self):
        got = self._call(_Args(tag="t", category="references"))
        self.assertEqual([e["id"] for e in got], ["a", "c"])


# --- the three graph modes ---------------------------------------------------

class GraphAdjacencyTests(unittest.TestCase):
    DATA = {"entries": [
        {"id": "a", "relates": ["depends-on:b", "depends-on:c"]},
        {"id": "b", "relates": ["depends-on:d"]},
        {"id": "c", "relates": ["relates-to:d"]},
        {"id": "d", "relates": []},
    ]}

    def test_depends_on_is_the_forward_map(self):
        forward, _ = G.build_adjacency(self.DATA)
        self.assertEqual(G._graph_adjacency(self.DATA, "depends_on"), forward)

    def test_impact_is_the_reverse_map(self):
        _, reverse = G.build_adjacency(self.DATA)
        self.assertEqual(G._graph_adjacency(self.DATA, "impact"), reverse)

    def test_relates_is_the_sorted_deduplicated_union(self):
        forward, reverse = G.build_adjacency(self.DATA)
        union = G._graph_adjacency(self.DATA, "relates")
        for key in set(forward) | set(reverse):
            expect = sorted(set(forward.get(key, []) + reverse.get(key, [])))
            self.assertEqual(union[key], expect, key)

    def test_union_deduplicates_a_reciprocal_edge(self):
        # a <-> b declared BOTH ways: the union must carry each (type, target)
        # once, or a traversal counts the same neighbour twice.
        data = {"entries": [{"id": "a", "relates": ["relates-to:b"]},
                            {"id": "b", "relates": ["relates-to:a"]}]}
        union = G._graph_adjacency(data, "relates")
        self.assertEqual(union["a"], [("relates-to", "b")])
        self.assertEqual(union["b"], [("relates-to", "a")])

    def test_the_union_reaches_a_node_that_only_ever_appears_as_a_target(self):
        # `d` has no OUTGOING edges, so it is absent from the forward map
        # entirely. A union built by extending the forward map instead of a
        # fresh dict would drop it, and `--relates d` would answer nothing.
        union = G._graph_adjacency(self.DATA, "relates")
        self.assertNotIn("d", G.build_adjacency(self.DATA)[0])
        self.assertEqual(union["d"], [("depends-on", "b"),
                                      ("relates-to", "c")])

    def test_unknown_mode_falls_back_to_the_union(self):
        self.assertEqual(G._graph_adjacency(self.DATA, "whatever"),
                         G._graph_adjacency(self.DATA, "relates"))


class TraverseResultsTests(unittest.TestCase):
    DATA = {"entries": [
        {"id": "a", "relates": ["depends-on:b", "depends-on:c"]},
        {"id": "b", "relates": ["depends-on:d"]},
        {"id": "c", "relates": ["relates-to:d"]},
        {"id": "d", "relates": []},
        {"id": "e", "relates": ["depends-on:ghost"]},
    ]}

    def _ids(self, args):
        by_id, _ = G._lookup_maps(self.DATA["entries"])
        return [e["id"] for e in G._traverse_results(args, self.DATA, by_id)]

    def test_no_graph_mode_returns_empty(self):
        self.assertEqual(self._ids(_Args()), [])

    def test_depth_one_is_direct_neighbours_only(self):
        self.assertEqual(self._ids(_Args(depends_on="a", depth=1)), ["b", "c"])

    def test_depth_zero_is_the_full_closure(self):
        self.assertEqual(self._ids(_Args(depends_on="a", depth=0)),
                         ["b", "c", "d"])

    def test_impact_walks_the_other_way(self):
        self.assertEqual(self._ids(_Args(impact="d", depth=0)), ["b", "c", "a"])

    def test_relates_reaches_both_directions_at_depth_one(self):
        self.assertEqual(sorted(self._ids(_Args(relates="d", depth=1))),
                         ["b", "c"])

    def test_dangling_target_is_dropped_not_returned_as_a_hole(self):
        self.assertEqual(self._ids(_Args(depends_on="e", depth=0)), [])

    def test_unknown_start_returns_empty(self):
        self.assertEqual(self._ids(_Args(depends_on="not-an-id")), [])

    def test_modes_resolve_in_parser_order(self):
        # The three starts are chosen so the three modes give DIFFERENT answers:
        # forward from `a` is [b, c], reverse from `b` is [a], and the union of
        # `d` is [b, c]. A namespace carrying all three must answer the FIRST.
        self.assertEqual(self._ids(_Args(depends_on="a", impact="b",
                                         relates="d", depth=1)), ["b", "c"])
        self.assertEqual(self._ids(_Args(impact="b", relates="d", depth=1)),
                         ["a"])
        self.assertEqual(self._ids(_Args(relates="d", depth=1)), ["b", "c"])

    def test_depth_defaults_to_one_on_a_namespace_without_it(self):
        by_id, _ = G._lookup_maps(self.DATA["entries"])
        got = G._traverse_results(_sparse(depends_on="a"), self.DATA, by_id)
        self.assertEqual([e["id"] for e in got], ["b", "c"])


# --- cmd_query ---------------------------------------------------------------

class _QueryCase(unittest.TestCase):
    ENTRIES = [
        {"id": "a", "path": "learnings/a.md", "category": "learnings",
         "created": "2026-01-01", "last_verified": "2026-01-02",
         "relates": ["depends-on:b"]},
        {"id": "b", "path": "learnings/b.md", "category": "learnings",
         "created": "2026-01-01"},
    ]

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-test-graph-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.data = {"entries": copy.deepcopy(self.ENTRIES),
                     "tags": {"t": ["a"]}, "generation": 1}
        self._real_require = config.require_knowledge_dir
        self._real_load = index.load_index
        config.require_knowledge_dir = lambda: self.kdir
        self.addCleanup(setattr, config, "require_knowledge_dir",
                        self._real_require)
        self.addCleanup(setattr, G.index, "load_index", self._real_load)

    def _load_ok(self, *a, **kw):
        return self.data, None

    def run_query(self, args, loader=None):
        G.index.load_index = loader or self._load_ok
        out, err = io.StringIO(), io.StringIO()
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = G.cmd_query(args)
        finally:
            sys.stdout, sys.stderr = real_out, real_err
        return rc, out.getvalue(), err.getvalue()


class QueryHandlerTests(_QueryCase):
    def test_no_knowledge_dir_exits_one_and_prints_nothing(self):
        config.require_knowledge_dir = lambda: None
        rc, out, err = self.run_query(_Args(id="a"))
        self.assertEqual((rc, out, err), (1, "", ""))

    def test_load_error_exits_one_and_names_the_error_on_stderr(self):
        rc, out, err = self.run_query(
            _Args(id="a"), loader=lambda *a, **kw: (None, "index.json is corrupt"))
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("index.json is corrupt", err)

    def test_a_hit_is_json_on_stdout_and_exits_zero(self):
        rc, out, _ = self.run_query(_Args(id="a"))
        self.assertEqual(rc, 0)
        got = json.loads(out)
        self.assertEqual([e["id"] for e in got], ["a"])

    def test_a_miss_is_the_empty_list_and_still_exits_zero(self):
        rc, out, _ = self.run_query(_Args(id="nope"))
        self.assertEqual((rc, json.loads(out)), (0, []))

    def test_freshness_fields_are_appended(self):
        _, out, _ = self.run_query(_Args(id="a"))
        got = json.loads(out)[0]
        self.assertEqual(got["abs_path"],
                         os.path.join(self.kdir, "learnings/a.md"))
        self.assertEqual(got["last_verified"], "2026-01-02")
        self.assertIn("age_days", got)

    def test_last_verified_falls_back_to_created(self):
        _, out, _ = self.run_query(_Args(id="b"))
        self.assertEqual(json.loads(out)[0]["last_verified"], "2026-01-01")

    def test_the_loaded_index_is_never_mutated(self):
        # INV-2 + the MCP server reuses the loaded index across calls: a
        # freshness field written into the shared dict would leak into the next
        # caller's result and outlive the request that computed it.
        before = copy.deepcopy(self.data)
        for args in (_Args(id="a"), _Args(category="learnings"),
                     _Args(relates="a", depth=0)):
            self.run_query(args)
        self.assertEqual(self.data, before)

    def test_missing_entries_key_is_an_empty_corpus_not_a_crash(self):
        rc, out, _ = self.run_query(_Args(id="a"), loader=lambda *a, **kw: ({}, None))
        self.assertEqual((rc, json.loads(out)), (0, []))

    def test_missing_tags_key_is_an_empty_tag_map(self):
        rc, out, _ = self.run_query(
            _Args(tag="t"), loader=lambda *a, **kw: ({"entries": [{"id": "a"}]}, None))
        self.assertEqual((rc, json.loads(out)), (0, []))

    def test_a_pathless_entry_gets_a_null_abs_path(self):
        rc, out, _ = self.run_query(
            _Args(id="a"),
            loader=lambda *a, **kw: ({"entries": [{"id": "a"}], "tags": {}}, None))
        self.assertIsNone(json.loads(out)[0]["abs_path"])

    def test_a_direct_miss_does_not_fall_through_to_a_traversal(self):
        # `--id nope` alongside a graph flag must answer [] — the direct mode was
        # SELECTED, and a fall-through would silently answer a different question.
        rc, out, _ = self.run_query(_sparse(id="nope", path=None, tag=None,
                                            category=None, depends_on="a",
                                            depth=0))
        self.assertEqual((rc, json.loads(out)), (0, []))

    def test_output_always_ends_with_a_newline(self):
        _, out, _ = self.run_query(_Args(id="a"))
        self.assertTrue(out.endswith("]\n"))


# --- the pure graph primitives this module owns ------------------------------

class GraphPrimitiveTests(unittest.TestCase):
    def test_adjacency_skips_malformed_relates_shapes(self):
        data = {"entries": [
            {"id": "a", "relates": "depends-on:b"},          # scalar, not a list
            {"id": "b", "relates": {"depends-on": "c"}},     # mapping
            {"id": "c", "relates": ["nocolon", "depends-on:d"]},
            "poison", None, {"relates": ["depends-on:x"]},   # no id
        ]}
        forward, reverse = G.build_adjacency(data)
        self.assertEqual(forward, {"c": [("depends-on", "d")]})
        self.assertEqual(reverse, {"d": [("depends-on", "c")]})

    def test_canonical_cycle_rotates_to_the_smallest_id(self):
        self.assertEqual(G._canonical_cycle(["c", "a", "b"]), ("a", "b", "c"))

    def test_find_cycles_reports_each_cycle_once_in_canonical_form(self):
        # NOTE on the `seen` set inside find_cycles: it cannot change an answer.
        # A back edge is walked at most once (the adjacency is de-duplicated),
        # and the node set it yields is `path[index(target):]` — determined by
        # (target, current top) — so two distinct back edges cannot produce the
        # same set. Measured as well as argued: exhaustive over every 4-node
        # digraph with 2..6 edges and 300,000 random 5-node digraphs, dropping
        # the dedup changes nothing. It is defence in depth, and this test does
        # not pretend to guard it.
        data = {"entries": [{"id": "a", "relates": ["depends-on:b"]},
                            {"id": "b", "relates": ["depends-on:a"]},
                            {"id": "c", "relates": ["depends-on:a"]}]}
        forward, _ = G.build_adjacency(data)
        self.assertEqual(G.find_cycles(forward), [("a", "b")])

    def test_two_disjoint_cycles_are_both_reported_sorted(self):
        data = {"entries": [{"id": "a", "relates": ["depends-on:b"]},
                            {"id": "b", "relates": ["depends-on:a"]},
                            {"id": "y", "relates": ["depends-on:z"]},
                            {"id": "z", "relates": ["depends-on:y"]}]}
        forward, _ = G.build_adjacency(data)
        self.assertEqual(G.find_cycles(forward), [("a", "b"), ("y", "z")])

    def test_find_cycles_is_empty_on_a_dag(self):
        data = {"entries": [{"id": "a", "relates": ["depends-on:b"]},
                            {"id": "b", "relates": ["depends-on:c"]}]}
        self.assertEqual(G.find_cycles(G.build_adjacency(data)[0]), [])

    def test_relation_errors_separate_dangling_from_unknown_type(self):
        data = {"entries": [{"id": "a", "relates": ["depends-on:missing",
                                                    "bogus-type:a"]}]}
        dangling, unknown = G.graph_relation_errors(data)
        self.assertEqual(dangling, [("a", "missing")])
        self.assertEqual(unknown, [("a", "bogus-type:a")])

    def test_graph_health_counts_every_axis(self):
        data = {"entries": [{"id": "a", "relates": ["depends-on:b"]},
                            {"id": "b", "relates": ["depends-on:a",
                                                    "bogus:gone"]}]}
        self.assertEqual(G.graph_health(data),
                         {"edges": 3, "nodes_with_edges": 2, "dangling": 1,
                          "unknown_types": 1, "cycles": 1})

    def test_traverse_terminates_on_a_cycle(self):
        adj = {"a": [("relates-to", "b")], "b": [("relates-to", "a")]}
        self.assertEqual(G.graph_traverse(adj, "a", 0), ["b"])

    def test_traverse_excludes_the_start_node(self):
        adj = {"a": [("relates-to", "a"), ("relates-to", "b")]}
        self.assertEqual(G.graph_traverse(adj, "a", 0), ["b"])


class ExportSurfaceTests(unittest.TestCase):
    def test_the_new_helpers_are_not_exported(self):
        # They have no monolith counterpart, so exporting them would widen the
        # facade surface the parity machinery pins.
        for name in ("_lookup_maps", "_direct_results", "_graph_adjacency",
                     "_traverse_results"):
            self.assertNotIn(name, G._EXPORTS, name)

    def test_the_verb_is_declared_exactly_once(self):
        self.assertEqual(G.__verbs__, (("query", 800, "register_query"),))


if __name__ == "__main__":
    unittest.main()
