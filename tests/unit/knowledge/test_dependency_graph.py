"""Tests for the declared-edge KB dependency graph (260625-kb-dependency-graph).

Covers: `relates` frontmatter emit/parse round-trip + malformed-token dropping,
the pure deterministic adjacency builder, cycle-safe traversal, dangling/unknown
edge integrity, the `query --depends-on/--impact/--relates [--depth]` surface,
the `graph_integrity` audit check, the Rule-7 graph_health metric emission, and
the no-harm-to-recall moat guard. Stdlib unittest only; never touches the real
KB. Every graph operation is deterministic + read-only (INV-1/INV-2).
"""

import json
import os

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # allow `python3 -m unittest tests.unit.knowledge.test_dependency_graph`
    from _fixtures import SyntheticKBTestCase, load_cli


# --- Pure-function tests (no CLI / no filesystem) ----------------------------
class RelatesParseTests(SyntheticKBTestCase):
    def test_emit_parse_round_trip_preserves_order(self):
        mod = load_cli()
        toks = ["depends-on:learn-b", "relates-to:ref-c", "blocks:config-d"]
        fields = {"id": "a", "title": "A", "category": "learnings", "tags": [],
                  "created": "2026-01-01", "summary": "s", "author": "op",
                  "source": "agent", "last_verified": "2026-01-01",
                  "relates": toks}
        parsed, _ = mod.split_frontmatter(mod.render_frontmatter(fields))
        self.assertEqual(parsed["relates"], toks)

    def test_malformed_tokens_dropped_deterministically(self):
        mod = load_cli()
        block = ("---\nid: a\nrelates: [nocolon, :notarget, type: , "
                 "depends-on:b, relates-to:c]\n---\nbody\n")
        parsed, _ = mod.split_frontmatter(block)
        # nocolon (no ':'), ':notarget' (empty type), 'type: ' (empty target)
        # are dropped; well-formed tokens survive in order.
        self.assertEqual(parsed["relates"], ["depends-on:b", "relates-to:c"])

    def test_unknown_type_is_kept_not_dropped(self):
        mod = load_cli()
        parsed, _ = mod.split_frontmatter(
            "---\nid: a\nrelates: [bogus-type:x]\n---\nbody\n")
        # Out-of-vocab type is structurally valid -> kept (audit flags it).
        self.assertEqual(parsed["relates"], ["bogus-type:x"])


class AdjacencyTests(SyntheticKBTestCase):
    DATA = {"entries": [
        {"id": "a", "relates": ["depends-on:b"]},
        {"id": "b", "relates": ["depends-on:c"]},
        {"id": "c", "relates": ["relates-to:a"]},
        {"id": "leaf", "relates": ["depends-on:a"]},
    ]}

    def test_adjacency_is_sorted_and_stable(self):
        mod = load_cli()
        data = {"entries": [
            {"id": "x", "relates": ["relates-to:z", "blocks:y", "depends-on:y"]},
        ]}
        fwd, rev = mod.build_adjacency(data)
        # Ordered by (type, target); identical across builds (INV-1).
        self.assertEqual(fwd["x"],
                         [("blocks", "y"), ("depends-on", "y"), ("relates-to", "z")])
        self.assertEqual(mod.build_adjacency(data)[0], fwd)
        self.assertEqual(rev["y"], [("blocks", "x"), ("depends-on", "x")])

    def test_forward_and_reverse_are_consistent(self):
        mod = load_cli()
        fwd, rev = mod.build_adjacency(self.DATA)
        self.assertEqual(fwd["a"], [("depends-on", "b")])
        self.assertEqual(rev["a"], [("depends-on", "leaf"), ("relates-to", "c")])


class CycleTests(SyntheticKBTestCase):
    def test_two_node_cycle_terminates_and_reports_once(self):
        mod = load_cli()
        data = {"entries": [
            {"id": "a", "relates": ["depends-on:b"]},
            {"id": "b", "relates": ["depends-on:a"]},  # A -> B -> A
        ]}
        fwd, _ = mod.build_adjacency(data)
        cycles = mod.find_cycles(fwd)
        self.assertEqual(cycles, [("a", "b")])  # canonical, reported once
        # Traversal over the cycle visits each node once (no infinite loop).
        self.assertEqual(mod.graph_traverse(fwd, "a", 0), ["b"])

    def test_self_loop_terminates(self):
        mod = load_cli()
        data = {"entries": [{"id": "a", "relates": ["depends-on:a"]}]}
        fwd, _ = mod.build_adjacency(data)
        self.assertEqual(mod.find_cycles(fwd), [("a",)])
        self.assertEqual(mod.graph_traverse(fwd, "a", 0), [])

    def test_traversal_is_byte_identical_across_runs(self):
        mod = load_cli()
        data = AdjacencyTests.DATA
        fwd, _ = mod.build_adjacency(data)
        a = json.dumps(mod.graph_traverse(fwd, "a", 0))
        b = json.dumps(mod.graph_traverse(fwd, "a", 0))
        self.assertEqual(a, b)


class IntegrityTests(SyntheticKBTestCase):
    def test_dangling_and_unknown_detected(self):
        mod = load_cli()
        data = {"entries": [
            {"id": "a", "relates": ["depends-on:missing", "bogus:b"]},
            {"id": "b", "relates": []},
        ]}
        dangling, unknown = mod.graph_relation_errors(data)
        self.assertEqual(dangling, [("a", "missing")])
        self.assertEqual(unknown, [("a", "bogus:b")])

    def test_clean_graph_has_no_errors(self):
        mod = load_cli()
        data = {"entries": [
            {"id": "a", "relates": ["depends-on:b"]},
            {"id": "b", "relates": []},
        ]}
        dangling, unknown = mod.graph_relation_errors(data)
        self.assertEqual((dangling, unknown), ([], []))


# --- CLI-level tests (real frontmatter + index rebuild) ----------------------
class GraphCLITests(SyntheticKBTestCase):
    """Migrate the synthetic KB to frontmatter, inject a chain + cycle + dangling
    edge, rebuild, then drive the real query/audit/recall commands."""

    EDGES = {
        # chain: geofence -> macos -> python ; plus a cycle python <-> bm25 ;
        # plus a dangling edge off geofence.
        "learnings/geofence-reminders.md":
            ["depends-on:learn-macos-no-timeout", "relates-to:missing-entry"],
        "learnings/macos-no-timeout.md": ["depends-on:config-python-runtime"],
        "configurations/python-runtime.md": ["relates-to:ref-bm25-ranking"],
        "references/bm25-ranking.md": ["relates-to:config-python-runtime"],
    }

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.kdir, "MAIN.md"), "w", encoding="utf-8") as f:
            f.write("# KB\n\n- **Handle**: testhandle\n")
        code, _, err = self.run_cli(["index", "migrate-frontmatter"])
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli(["index", "rebuild"])
        self.assertEqual(code, 0, err)
        mod = load_cli()
        for rel, toks in self.EDGES.items():
            path = os.path.join(self.kdir, rel)
            with open(path, encoding="utf-8") as f:
                fields, body = mod.split_frontmatter(f.read())
            fields["relates"] = toks
            with open(path, "w", encoding="utf-8") as f:
                f.write(mod.render_frontmatter(fields) + body)
        code, _, err = self.run_cli(["index", "rebuild"])
        self.assertEqual(code, 0, err)

    def _index_bytes(self):
        with open(os.path.join(self.kdir, "index.json"), "rb") as f:
            return f.read()

    def test_rebuild_round_trips_relates_into_index(self):
        row = next(e for e in self.read_index()["entries"]
                   if e["id"] == "learn-geofence-reminders")
        self.assertEqual(
            row["relates"],
            ["depends-on:learn-macos-no-timeout", "relates-to:missing-entry"])

    def test_rebuild_is_byte_identical_no_op(self):
        # The monotonic `generation` token (B1) increments on every rebuild by
        # design (so a stale cache is always detectably older), and index.json is a
        # DERIVED + gitignored artifact — whole-file byte-identity no longer matters
        # for git. The ranking invariant is entry ORDER + CONTENT + the tags map,
        # which stays byte-identical (generation is the one tolerated top-level diff).
        before = json.loads(self._index_bytes())
        code, _, err = self.run_cli(["index", "rebuild"])
        self.assertEqual(code, 0, err)
        after = json.loads(self._index_bytes())
        self.assertEqual(after["entries"], before["entries"])
        self.assertEqual(after["tags"], before["tags"])

    def test_query_depends_on_full_closure(self):
        code, out, err = self.run_cli(
            ["query", "--depends-on", "learn-geofence-reminders",
             "--depth", "0", "--format", "json"])
        self.assertEqual(code, 0, err)
        ids = [e["id"] for e in json.loads(out)]
        # forward closure: macos -> python -> bm25 (missing-entry dangling,
        # absent from the index so it does not appear in the entry-list output).
        self.assertEqual(
            ids,
            ["learn-macos-no-timeout", "config-python-runtime", "ref-bm25-ranking"])

    def test_query_depth_one_vs_full(self):
        code, out, _ = self.run_cli(
            ["query", "--depends-on", "learn-geofence-reminders",
             "--depth", "1", "--format", "json"])
        self.assertEqual([e["id"] for e in json.loads(out)],
                         ["learn-macos-no-timeout"])

    def test_query_impact_reverse_closure(self):
        code, out, err = self.run_cli(
            ["query", "--impact", "config-python-runtime",
             "--depth", "0", "--format", "json"])
        self.assertEqual(code, 0, err)
        ids = sorted(e["id"] for e in json.loads(out))
        # who reaches python-runtime: macos (direct), geofence (via macos),
        # bm25 (cycle relates-to).
        self.assertEqual(
            ids,
            ["learn-geofence-reminders", "learn-macos-no-timeout",
             "ref-bm25-ranking"])

    def test_query_unknown_id_returns_empty(self):
        code, out, err = self.run_cli(
            ["query", "--impact", "nonexistent-id", "--format", "json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), [])

    def test_query_is_byte_identical_and_read_only(self):
        before = self._index_bytes()
        a = self.run_cli(["query", "--impact", "config-python-runtime",
                          "--depth", "0", "--format", "json"])
        b = self.run_cli(["query", "--impact", "config-python-runtime",
                          "--depth", "0", "--format", "json"])
        self.assertEqual(a[1], b[1])               # byte-identical (INV-1)
        self.assertEqual(self._index_bytes(), before)  # read-only (INV-2)

    def test_audit_graph_integrity_reports_dangling_and_cycle(self):
        code, out, _ = self.run_cli(["audit", "--format", "json"])
        payload = json.loads(out)
        check = next(c for c in payload["checks"]
                     if c["name"] == "graph_integrity")
        self.assertFalse(check["ok"])  # dangling edge present
        blob = "\n".join(check["details"])
        self.assertIn("missing-entry", blob)       # the dangling target named
        self.assertIn("dangling edge", blob)
        self.assertIn("cycle (advisory)", blob)     # the python<->bm25 cycle

    def test_audit_emits_graph_health_metric(self):
        metrics = os.path.join(self.kdir, "logs", "metrics.jsonl")
        self.run_cli(["audit", "--format", "json"])
        self.assertTrue(os.path.exists(metrics))
        last = None
        with open(metrics, encoding="utf-8") as f:
            for line in f:
                obj = json.loads(line)
                if obj.get("event") == "graph_health":
                    last = obj
        self.assertIsNotNone(last)
        self.assertEqual(last["dangling"], 1)
        self.assertGreaterEqual(last["cycles"], 1)
        self.assertIn("edges", last)
        self.assertIn("ts", last)

    def test_clean_kb_graph_integrity_ok(self):
        # Strip all edges -> a clean graph -> ok:True.
        mod = load_cli()
        for rel in self.EDGES:
            path = os.path.join(self.kdir, rel)
            with open(path, encoding="utf-8") as f:
                fields, body = mod.split_frontmatter(f.read())
            fields["relates"] = []
            with open(path, "w", encoding="utf-8") as f:
                f.write(mod.render_frontmatter(fields) + body)
        self.run_cli(["index", "rebuild"])
        code, out, _ = self.run_cli(["audit", "--format", "json"])
        check = next(c for c in json.loads(out)["checks"]
                     if c["name"] == "graph_integrity")
        self.assertTrue(check["ok"])


class NoHarmToRecallTests(SyntheticKBTestCase):
    """Adding `relates` edges must NOT change the BM25 corpus or recall ranking
    (`relates` lives in frontmatter, stripped before corpus build)."""

    def _recall(self):
        return self.run_cli(["recall", "python stdlib ranking geofence macos",
                             "--format", "json"])[1]

    def test_recall_byte_identical_before_and_after_edges(self):
        mod = load_cli()
        with open(os.path.join(self.kdir, "MAIN.md"), "w", encoding="utf-8") as f:
            f.write("# KB\n\n- **Handle**: testhandle\n")
        self.run_cli(["index", "migrate-frontmatter"])
        self.run_cli(["index", "rebuild"])
        before = self._recall()
        # Inject edges into every entry, rebuild, recall again.
        for sub, fn in (("learnings", "geofence-reminders.md"),
                        ("learnings", "macos-no-timeout.md")):
            path = os.path.join(self.kdir, sub, fn)
            with open(path, encoding="utf-8") as f:
                fields, body = mod.split_frontmatter(f.read())
            fields["relates"] = ["depends-on:config-python-runtime"]
            with open(path, "w", encoding="utf-8") as f:
                f.write(mod.render_frontmatter(fields) + body)
        self.run_cli(["index", "rebuild"])
        self.assertEqual(self._recall(), before)


if __name__ == "__main__":
    import unittest
    unittest.main()
