"""`graphtopo`'s own unit file — task 99 wave 6, landed with the paydown.

`agentware_lib.features.graphdag.graphtopo` answers the question a DAG's declared
edges cannot: which nodes CONFLICT. Two nodes with no dependency between them but
overlapping write sets will race against one repository, last writer wins, and
the losing node's work vanishes with its lane reporting success. Until this file
the module had no owning test; `test_plan_graph*` exercises the scheduler THROUGH
it, which says nothing about the derivation's own decisions and cannot reach a
manifest the scheduler would have refused before it got here.

This wave split `_graph_derive_resource_edges` (18 branches -> 7) into
`_graph_logical_rank`, `_graph_declared_pairs` and `_graph_assert_union_acyclic`.
Every property below is a way the derivation can produce a graph that LOOKS
scheduled and is not.

  RANK/LOGICAL-ONLY  — the linearization is taken over the LOGICAL sub-graph
                       alone. Ranking over the combined graph would feed derived
                       resource edges back into the rule that orients them, so
                       the answer would depend on itself.
  RANK/CYCLE         — a cycle in the logical sub-graph RAISES rather than
                       returning a partial order, because a partial order here
                       silently drops the serialization for the missing nodes.
  RANK/DETERMINISM   — the same manifest yields the same rank across runs and
                       across node orderings, which is what makes a dry-run's
                       plan match the real run's dispatch (INV-1).
  DECL/KIND-BLIND    — a pair already ordered by a hand-written `resource` edge
                       needs no derived twin; kind-blindness is the property, not
                       an accident of the isinstance chain.
  DECL/TOTAL         — a malformed edge row is SKIPPED, never raised on: the
                       validator is `graph_manifest_errors`, and a derivation
                       that dies on a half-edited manifest cannot report why.
  ACYCLIC/ASSERTION  — the union check fires on a cycle and names it. It cannot
                       fire under the shipped orientation, so it is driven
                       directly with a derived set that contradicts a declared
                       edge — the shape a reintroduced pairwise rule produces.
  DERIVE/ORIENTATION — every derived edge points from the EARLIER to the LATER
                       node in the single linearization, which is what makes
                       `logical ∪ resource` acyclic by construction.
  DERIVE/CLIQUE      — three nodes on one file serialize into a chain, not a
                       cycle. This is the audit BLOCKER the orientation exists
                       for and the reason a lexical tie-break is wrong.
  DERIVE/SUPPRESSION — an overlap already ordered by a declared edge in EITHER
                       direction emits nothing.
  DERIVE/OVERRIDE    — `write_sets` REPLACES the manifest's stamped sets in both
                       directions: it can add an overlap and remove one.
  DERIVE/SORTED      — the returned list is sorted, so two runs over one manifest
                       are byte-identical.

Hermetic: every function here is a pure fold over a dict. No tempdirs, no seams,
no clock, stdlib unittest only.
"""

from tests._paths import TESTS_DIR

import os
import sys
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

from agentware_lib.features.graphdag.graphtopo import (  # noqa: E402
    _graph_assert_union_acyclic,
    _graph_declared_pairs,
    _graph_derive_resource_edges,
    _graph_logical_rank,
    _graph_ready_set,
    topo_order,
)
from agentware_lib.features.graphdag.graphmanifest import _graph_build  # noqa: E402


def _man(nodes, edges=None):
    """A manifest carrying only what this module reads: nodes with write sets and
    edges with a kind. `nodes` is {feature: write_set}."""
    return {
        "name": "g",
        "nodes": [{"feature": f, "write_set": list(ws)}
                  for f, ws in nodes],
        "edges": [{"from": e[0], "to": e[1],
                   "kind": e[2] if len(e) > 2 else "logical"}
                  for e in (edges or [])],
    }


def _pairs(derived):
    return [(e["from"], e["to"]) for e in derived]


class LogicalRankTest(unittest.TestCase):
    """RANK/LOGICAL-ONLY, RANK/CYCLE, RANK/DETERMINISM."""

    def test_the_rank_ignores_non_logical_edges(self):
        # b -> a as a RESOURCE edge must not move the rank; only the logical
        # spine may. Without the kind filter, `b` would rank before `a`.
        nodes, rank = _graph_logical_rank(
            _man([("a", []), ("b", [])], [("b", "a", "resource")]))
        self.assertEqual(sorted(nodes), ["a", "b"])
        self.assertLess(rank["a"], rank["b"])
        # ... and the SAME pair with the same edge declared logical DOES move it.
        _n2, rank2 = _graph_logical_rank(
            _man([("a", []), ("b", [])], [("b", "a", "logical")]))
        self.assertLess(rank2["b"], rank2["a"])

    def test_an_unknown_edge_kind_is_not_treated_as_logical(self):
        _n, rank = _graph_logical_rank(
            _man([("a", []), ("b", [])], [("b", "a", "wishful")]))
        self.assertLess(rank["a"], rank["b"])

    def test_a_logical_cycle_raises_rather_than_ranking_partially(self):
        with self.assertRaises(ValueError) as ctx:
            _graph_logical_rank(
                _man([("a", []), ("b", [])], [("a", "b"), ("b", "a")]))
        self.assertIn("cycle", str(ctx.exception))

    def test_a_resource_only_cycle_does_not_raise(self):
        # The logical sub-graph is what must be acyclic. A cycle made purely of
        # resource edges is filtered out before the sort ever sees it.
        _n, rank = _graph_logical_rank(
            _man([("a", []), ("b", [])],
                 [("a", "b", "resource"), ("b", "a", "resource")]))
        self.assertEqual(sorted(rank), ["a", "b"])

    def test_the_rank_is_independent_of_the_nodes_declaration_order(self):
        forward = _man([("c", []), ("a", []), ("b", [])],
                       [("c", "b"), ("b", "a")])
        backward = _man([("b", []), ("c", []), ("a", [])],
                        [("b", "a"), ("c", "b")])
        self.assertEqual(_graph_logical_rank(forward)[1],
                         _graph_logical_rank(backward)[1])

    def test_the_rank_covers_every_node_including_isolated_ones(self):
        nodes, rank = _graph_logical_rank(
            _man([("a", []), ("b", []), ("lonely", [])], [("a", "b")]))
        self.assertEqual(set(rank), set(nodes))
        self.assertEqual(len(set(rank.values())), len(nodes))


class DeclaredPairsTest(unittest.TestCase):
    """DECL/KIND-BLIND, DECL/TOTAL."""

    def test_it_collects_pairs_of_every_kind(self):
        got = _graph_declared_pairs(
            _man([("a", []), ("b", []), ("c", [])],
                 [("a", "b", "logical"), ("b", "c", "resource")]))
        self.assertEqual(got, {("a", "b"), ("b", "c")})

    def test_it_is_directional(self):
        got = _graph_declared_pairs(_man([("a", []), ("b", [])], [("a", "b")]))
        self.assertIn(("a", "b"), got)
        self.assertNotIn(("b", "a"), got)

    def test_malformed_edge_rows_are_skipped_not_raised(self):
        man = _man([("a", []), ("b", [])], [("a", "b")])
        man["edges"] += ["not-a-dict", {"from": "a"}, {"to": "b"},
                         {"from": 7, "to": "b"}, {"from": "a", "to": None}]
        self.assertEqual(_graph_declared_pairs(man), {("a", "b")})

    def test_a_missing_edges_key_is_the_empty_set(self):
        self.assertEqual(_graph_declared_pairs({"nodes": []}), set())
        self.assertEqual(_graph_declared_pairs({"edges": None}), set())


class UnionAcyclicTest(unittest.TestCase):
    """ACYCLIC/ASSERTION."""

    def test_it_accepts_a_union_that_is_a_dag(self):
        man = _man([("a", []), ("b", [])], [("a", "b")])
        self.assertIsNone(_graph_assert_union_acyclic(
            man, [{"from": "a", "to": "b", "kind": "resource"}]))

    def test_it_raises_and_names_the_cycle_the_pairwise_rule_would_make(self):
        # A derived edge pointing BACKWARD against a declared one is exactly what
        # a lexical tie-break produces on a same-file clique. The assertion can
        # never see this from the shipped derivation, so it is fed directly.
        man = _man([("a", []), ("b", [])], [("a", "b")])
        with self.assertRaises(ValueError) as ctx:
            _graph_assert_union_acyclic(
                man, [{"from": "b", "to": "a", "kind": "resource"}])
        msg = str(ctx.exception)
        self.assertIn("resource-edge derivation produced a cycle", msg)
        self.assertIn("a", msg)
        self.assertIn("b", msg)

    def test_an_empty_derived_set_still_checks_the_declared_edges(self):
        man = _man([("a", []), ("b", [])], [("a", "b"), ("b", "a")])
        with self.assertRaises(ValueError):
            _graph_assert_union_acyclic(man, [])


class DeriveResourceEdgesTest(unittest.TestCase):
    """DERIVE/ORIENTATION, DERIVE/CLIQUE, DERIVE/SUPPRESSION, DERIVE/OVERRIDE,
    DERIVE/SORTED."""

    def test_no_overlap_derives_nothing(self):
        self.assertEqual(
            _graph_derive_resource_edges(_man([("a", ["x.py"]),
                                               ("b", ["y.py"])])), [])

    def test_an_overlapping_pair_is_serialized(self):
        got = _graph_derive_resource_edges(
            _man([("a", ["x.py"]), ("b", ["x.py"])]))
        self.assertEqual(got, [{"from": "a", "to": "b", "kind": "resource",
                                "derived": True}])

    def test_the_orientation_follows_the_logical_rank_not_the_alphabet(self):
        # The logical spine b -> a inverts the lexical order. A lexical rule
        # would emit a -> b and CONTRADICT the declared edge.
        man = _man([("a", ["x.py"]), ("b", ["x.py"]), ("c", ["x.py"])],
                   [("b", "a")])
        got = _pairs(_graph_derive_resource_edges(man))
        self.assertIn(("b", "c"), got + [("b", "a")])
        for src, dst in got:
            self.assertNotEqual((src, dst), ("a", "b"),
                                "a derived edge contradicted the declared b->a")

    def test_a_same_file_clique_of_three_becomes_a_chain_not_a_cycle(self):
        man = _man([("c", ["x.py"]), ("a", ["x.py"]), ("b", ["x.py"])])
        got = _pairs(_graph_derive_resource_edges(man))
        self.assertEqual(sorted(got), [("a", "b"), ("a", "c"), ("b", "c")])
        # ... and the union is a DAG, which is the property the chain buys.
        combined = _man([("c", ["x.py"]), ("a", ["x.py"]), ("b", ["x.py"])])
        combined["edges"] = [{"from": s, "to": d, "kind": "resource"}
                             for s, d in got]
        nodes, forward, _r = _graph_build(combined)
        self.assertEqual(len(topo_order(forward, nodes)), 3)

    def test_a_spined_clique_orients_along_the_spine(self):
        man = _man([("c", ["x.py"]), ("a", ["x.py"]), ("b", ["x.py"])],
                   [("c", "b"), ("b", "a")])
        got = _pairs(_graph_derive_resource_edges(man))
        # c -> b and b -> a are declared, so only the c/a pair needs a derived
        # edge, and it must follow the spine (c before a), never the alphabet.
        self.assertEqual(got, [("c", "a")])

    def test_a_declared_edge_in_either_direction_suppresses_the_derived_one(self):
        for edge in (("a", "b"), ("b", "a")):
            self.assertEqual(
                _graph_derive_resource_edges(
                    _man([("a", ["x.py"]), ("b", ["x.py"])], [edge])), [],
                "declared %s did not suppress the derived twin" % (edge,))

    def test_a_declared_resource_edge_suppresses_too(self):
        self.assertEqual(
            _graph_derive_resource_edges(
                _man([("a", ["x.py"]), ("b", ["x.py"])],
                     [("a", "b", "resource")])), [])

    def test_a_declared_edge_pointing_AGAINST_the_rank_still_suppresses(self):
        """The `(dst, src) in declared` half of the suppression, on the only
        input that can reach it.

        Found by the wave-6 non-vacuity sweep: deleting that half left every
        suppression test green. A LOGICAL edge can never contradict the rank —
        the rank is derived from it — so with a logical edge the FIRST half
        always fires and the second is dead weight in the fixture. A RESOURCE
        edge does NOT feed the rank, so it can point the other way: here the rank
        is a < b, the declared pair is (b, a), and only the reverse half sees it.

        Without it the derivation emits a -> b ALONGSIDE the declared b -> a,
        which is a two-node cycle in the union — so the bug does not merely add a
        redundant edge, it deadlocks the graph."""
        man = _man([("a", ["x.py"]), ("b", ["x.py"])],
                   [("b", "a", "resource")])
        _n, rank = _graph_logical_rank(man)
        self.assertLess(rank["a"], rank["b"],
                        "the fixture no longer discriminates: the resource edge "
                        "must NOT move the rank, or the first half suppresses")
        self.assertEqual(_graph_derive_resource_edges(man), [])

    def test_the_write_sets_override_can_add_an_overlap(self):
        man = _man([("a", ["x.py"]), ("b", ["y.py"])])
        self.assertEqual(_graph_derive_resource_edges(man), [])
        self.assertEqual(
            _pairs(_graph_derive_resource_edges(
                man, write_sets={"a": ["z.py"], "b": ["z.py"]})),
            [("a", "b")])

    def test_the_write_sets_override_can_remove_an_overlap(self):
        man = _man([("a", ["x.py"]), ("b", ["x.py"])])
        self.assertEqual(
            _graph_derive_resource_edges(
                man, write_sets={"a": ["p.py"], "b": ["q.py"]}), [])

    def test_an_empty_override_dict_overrides_rather_than_falling_back(self):
        # `{}` is FALSY but not None: it must mean "no node writes anything",
        # not "use the manifest". `write_sets or manifest_sets` would get this
        # exactly backwards, and the result would be a silently unserialized run.
        man = _man([("a", ["x.py"]), ("b", ["x.py"])])
        self.assertEqual(_graph_derive_resource_edges(man, write_sets={}), [])

    def test_a_directory_glob_conflicts_with_a_file_beneath_it(self):
        self.assertEqual(
            _pairs(_graph_derive_resource_edges(
                _man([("a", ["scripts/**"]), ("b", ["scripts/foo.py"])]))),
            [("a", "b")])

    def test_the_whole_repo_sentinel_conflicts_with_everything(self):
        self.assertEqual(
            _pairs(_graph_derive_resource_edges(
                _man([("a", ["**"]), ("b", ["docs/x.md"]), ("c", ["y.py"])]))),
            [("a", "b"), ("a", "c")])

    def test_the_result_is_sorted_regardless_of_node_order(self):
        man = _man([("z", ["x.py"]), ("m", ["x.py"]), ("a", ["x.py"])])
        got = _pairs(_graph_derive_resource_edges(man))
        self.assertEqual(got, sorted(got))

    def test_a_logical_cycle_surfaces_as_a_valueerror(self):
        with self.assertRaises(ValueError):
            _graph_derive_resource_edges(
                _man([("a", ["x.py"]), ("b", ["x.py"])],
                     [("a", "b"), ("b", "a")]))

    def test_nodes_without_a_write_set_key_never_conflict(self):
        man = {"name": "g", "nodes": [{"feature": "a"}, {"feature": "b"}],
               "edges": []}
        self.assertEqual(_graph_derive_resource_edges(man), [])

    def test_junk_node_rows_are_ignored(self):
        man = _man([("a", ["x.py"])])
        man["nodes"] += ["nope", {"no_feature": 1}, {"feature": 7}]
        self.assertEqual(_graph_derive_resource_edges(man), [])


class DerivedEdgesReachTheFrontierTest(unittest.TestCase):
    """The derivation is only meaningful through the frontier it produces: a
    derived edge that never reached `_graph_ready_set` would look correct as a
    list and serialize nothing."""

    def test_two_conflicting_pending_nodes_are_not_both_ready(self):
        man = _man([("a", ["x.py"]), ("b", ["x.py"])])
        derived = _graph_derive_resource_edges(man)
        combined = dict(man)
        combined["edges"] = list(man["edges"]) + derived
        graph = _graph_build(combined)
        ready = _graph_ready_set(graph, {"a": "pending", "b": "pending"})
        self.assertEqual(ready, ["a"])

    def test_two_independent_pending_nodes_are_both_ready(self):
        man = _man([("a", ["x.py"]), ("b", ["y.py"])])
        derived = _graph_derive_resource_edges(man)
        self.assertEqual(derived, [])
        combined = dict(man)
        combined["edges"] = list(man["edges"]) + derived
        graph = _graph_build(combined)
        self.assertEqual(
            _graph_ready_set(graph, {"a": "pending", "b": "pending"}),
            ["a", "b"])


if __name__ == "__main__":
    unittest.main()
