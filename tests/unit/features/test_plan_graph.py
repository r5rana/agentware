"""Tests for the PLAN-GRAPH orchestrator (feature 260722-plan-graph-orchestration).

The plan-graph layer is pure orchestration over the shipped `fanout` worktree-
lane substrate + the six-state plan machine. This module grows one TestCase per
plan task:

  TestPlanGraphParsers   — Task 1: the pure read-only parsers + lint rules
                           (plan_depends_on / plan_write_set / infer_write_set /
                           plan_checkpoint) and plan-lint R15/R16.
  TestManifestValidate   — Task 2 (later)
  TestSchedulerPure      — Task 3 (later)
  ...

Stdlib `unittest` only (pytest is NOT installed). The CLI is imported in-process
via tests._fixtures.load_cli so we test the REAL symbols, not a copy.

Runner:  python3 -m unittest tests.unit.features.test_plan_graph -v
"""

from tests.unit.features._plan_graph_support import (  # noqa: F401
    CLI, REPO_ROOT, SyntheticKBTestCase, _manifest, _plan, os, unittest)


class TestPlanGraphParsers(unittest.TestCase):
    # --- plan_depends_on -----------------------------------------------------

    def test_depends_on_default_logical(self):
        text = _plan("## Depends-on:\n\n- 260710-alpha\n- 260711-beta\n")
        self.assertEqual(
            CLI.plan_depends_on(text),
            [("260710-alpha", "logical"), ("260711-beta", "logical")],
        )

    def test_depends_on_explicit_kinds(self):
        text = _plan(
            "## Depends-on:\n\n"
            "- 260710-alpha logical\n"
            "- 260711-beta resource\n"
            "- 260712-gamma (resource)\n"
            "- 260713-delta:resource\n"
        )
        self.assertEqual(
            CLI.plan_depends_on(text),
            [
                ("260710-alpha", "logical"),
                ("260711-beta", "resource"),
                ("260712-gamma", "resource"),
                ("260713-delta", "resource"),
            ],
        )

    def test_depends_on_malformed_sentinel(self):
        text = _plan(
            "## Depends-on:\n\n"
            "- 260710-alpha weird\n"          # out-of-vocab kind
            "- bad slug here\n"               # extra tokens
            "- has/slash logical\n"           # slug not [A-Za-z0-9_-]+
        )
        edges = CLI.plan_depends_on(text)
        self.assertEqual([k for _, k in edges],
                         ["__malformed__", "__malformed__", "__malformed__"])

    def test_depends_on_absent_is_empty(self):
        self.assertEqual(CLI.plan_depends_on(_plan("")), [])

    def test_depends_on_stops_at_next_heading(self):
        text = _plan("## Depends-on:\n\n- 260710-alpha\n\n## Write set\n\n- docs/x.md\n")
        self.assertEqual(CLI.plan_depends_on(text), [("260710-alpha", "logical")])

    # --- plan_write_set ------------------------------------------------------

    def test_write_set_explicit(self):
        text = _plan("## Write set\n\n- docs/a.md\n- `scripts/agentware`\n")
        self.assertEqual(CLI.plan_write_set(text),
                         ["docs/a.md", "scripts/agentware"])

    def test_write_set_serialize_on_prefix(self):
        text = _plan("## Write set\n\n- serialize_on: scripts/agentware\n")
        self.assertEqual(CLI.plan_write_set(text), ["scripts/agentware"])

    def test_write_set_absent_is_empty(self):
        self.assertEqual(CLI.plan_write_set(_plan("")), [])

    def test_write_set_dedup_sorted(self):
        text = _plan("## Write set\n\n- b.md\n- a.md\n- a.md\n")
        self.assertEqual(CLI.plan_write_set(text), ["a.md", "b.md"])

    # --- infer_write_set -----------------------------------------------------

    def test_infer_self_ext_no_named_file_sentinel(self):
        # A self-extension plan naming no concrete kernel file collapses to the
        # {scripts/agentware} sentinel so two such plans serialize.
        text = _plan("A generic self-extension with no path named.",
                     self_ext=True, target="~/workspace/agentware")
        self.assertEqual(CLI.infer_write_set(text, REPO_ROOT),
                         ["scripts/agentware"])

    def test_infer_self_ext_named_kernel_file_narrows(self):
        text = _plan("Edit scripts/agentware and agentware.sh dispatch.",
                     self_ext=True)
        got = CLI.infer_write_set(text, REPO_ROOT)
        self.assertIn("scripts/agentware", got)
        self.assertIn("agentware.sh", got)

    def test_infer_non_self_ext_specific_dir(self):
        text = _plan("A docs-only change.", target=os.path.join(REPO_ROOT, "docs"))
        self.assertEqual(CLI.infer_write_set(text, REPO_ROOT), ["docs"])

    def test_infer_un_narrowable_whole_repo_sentinel(self):
        # Whole-repo target, no kernel mention -> the whole-repo sentinel, which
        # MUST overlap every repo-relative token (the old `<pkg>/**` form used the
        # repo basename as the glob root and overlapped nothing — audit finding).
        text = _plan("A vague change with no file named.",
                     target="~/workspace/agentware")
        got = CLI.infer_write_set(text, REPO_ROOT)
        self.assertEqual(got, [CLI._WRITE_SET_WHOLE_REPO])
        # The sentinel serializes against ANY concrete sibling write-set.
        self.assertTrue(CLI._write_sets_overlap(got, ["scripts/agentware"]))
        self.assertTrue(CLI._write_sets_overlap(got, ["docs/loop.md"]))
        self.assertTrue(CLI._graph_write_set_touches_kernel(got))

    # --- plan_checkpoint -----------------------------------------------------

    def test_checkpoint_default_none(self):
        self.assertEqual(CLI.plan_checkpoint(_plan("")), "none")

    def test_checkpoint_declared(self):
        text = _plan("", self_ext=True).replace(
            "> Status: draft\n", "> Status: draft\n> Checkpoint: manual\n")
        self.assertEqual(CLI.plan_checkpoint(text), "manual")

    # --- lint R15/R16 (via lint_plan directly, strict at the error ramp) ------

    def _lint(self, text, rel_path="work/260722-x/plan.md"):
        # Force the error ramp so R15/R16 surface as ERRORS (they are WARN under
        # the current schema, hard-error once schema advances).
        return CLI.lint_plan(text, strict=True, rel_path=rel_path,
                             schema_version=CLI._PLAN_GRAPH_ERROR_SCHEMA)

    def test_lint_r15_self_edge(self):
        text = _plan("## Depends-on:\n\n- 260722-x\n")
        errors, _ = self._lint(text, rel_path="work/260722-x/plan.md")
        self.assertTrue(any(e["rule"] == "R15" and "self-edge" in e["message"]
                            for e in errors))

    def test_lint_r15_duplicate_edge(self):
        text = _plan("## Depends-on:\n\n- 260710-a\n- 260710-a\n")
        errors, _ = self._lint(text)
        self.assertTrue(any(e["rule"] == "R15" and "duplicate" in e["message"]
                            for e in errors))

    def test_lint_r15_malformed_row(self):
        text = _plan("## Depends-on:\n\n- 260710-a bogus-kind\n")
        errors, _ = self._lint(text)
        self.assertTrue(any(e["rule"] == "R15" and "malformed" in e["message"]
                            for e in errors))

    def test_lint_r16_selfext_requires_non_none(self):
        # Self-extension plan with no `> Checkpoint:` (defaults none) -> R16.
        text = _plan("", self_ext=True)
        errors, _ = self._lint(text)
        self.assertTrue(any(e["rule"] == "R16" for e in errors))

    def test_lint_r16_selfext_manual_ok(self):
        text = _plan("", self_ext=True).replace(
            "> Status: draft\n", "> Status: draft\n> Checkpoint: manual\n")
        errors, _ = self._lint(text)
        self.assertFalse(any(e["rule"] == "R16" for e in errors))

    def test_lint_r16_unknown_policy(self):
        text = _plan("").replace(
            "> Status: draft\n", "> Status: draft\n> Checkpoint: sometimes\n")
        errors, _ = self._lint(text)
        self.assertTrue(any(e["rule"] == "R16" and "not a recognized" in e["message"]
                            for e in errors))

    def test_lint_r15_r16_warn_under_current_schema(self):
        # Under the current schema (< ramp) the same defects are WARN, not ERROR,
        # so existing self-extension plans are never retroactively hard-failed.
        text = _plan("", self_ext=True)  # missing checkpoint
        errors, warnings = CLI.lint_plan(text, strict=True,
                                         rel_path="work/260722-x/plan.md",
                                         schema_version=0)
        self.assertFalse(any(e["rule"] == "R16" for e in errors))
        self.assertTrue(any(w["rule"] == "R16" for w in warnings))

class TestManifestValidate(unittest.TestCase):
    def _errs(self, manifest, kdir=None):
        return CLI.graph_manifest_errors(manifest, kdir=kdir)

    # --- happy path ----------------------------------------------------------

    def test_valid_manifest_no_errors(self):
        man = _manifest(
            nodes=[("260722-a",), ("260722-b",), ("260722-c",)],
            edges=[("260722-a", "260722-b"), ("260722-b", "260722-c")])
        self.assertEqual(self._errs(man), [])

    def test_empty_manifest_is_valid(self):
        self.assertEqual(self._errs(_manifest()), [])

    # --- schema / name / concurrency / bounds --------------------------------

    def test_bad_schema(self):
        man = _manifest(schema=99)
        self.assertTrue(any("schema" in e for e in self._errs(man)))

    def test_bad_graph_name(self):
        man = _manifest(name="bad name!")
        self.assertTrue(any("graph name" in e for e in self._errs(man)))

    def test_concurrency_out_of_range_low(self):
        self.assertTrue(any("concurrency" in e
                            for e in self._errs(_manifest(concurrency=0))))

    def test_concurrency_out_of_range_high(self):
        self.assertTrue(any("concurrency" in e
                            for e in self._errs(_manifest(concurrency=17))))

    def test_concurrency_bool_rejected(self):
        # True is an int in Python — must NOT be accepted as concurrency 1.
        self.assertTrue(any("concurrency" in e
                            for e in self._errs(_manifest(concurrency=True))))

    def test_node_count_over_max_refused(self):
        nodes = [("260722-n%d" % i,) for i in range(CLI.GRAPH_MAX_NODES + 1)]
        self.assertTrue(any("exceeds" in e for e in self._errs(_manifest(nodes))))

    def test_node_count_at_max_ok(self):
        nodes = [("260722-n%d" % i,) for i in range(CLI.GRAPH_MAX_NODES)]
        self.assertEqual(self._errs(_manifest(nodes)), [])

    # --- per-node ------------------------------------------------------------

    def test_bad_node_feature_name(self):
        man = _manifest(nodes=[("bad name",)])
        self.assertTrue(any("node feature name" in e for e in self._errs(man)))

    def test_duplicate_node(self):
        man = _manifest(nodes=[("260722-a",), ("260722-a",)])
        self.assertTrue(any("duplicate node" in e for e in self._errs(man)))

    def test_unknown_checkpoint_policy(self):
        man = _manifest(nodes=[("260722-a", "sometimes", False)])
        self.assertTrue(any("checkpoint policy" in e for e in self._errs(man)))

    def test_self_ext_without_manual_checkpoint_refused(self):
        # The audit-critical rule: a self-extension node MUST be a manual
        # checkpoint. A weaker checkpoint is a validate-time refusal.
        man = _manifest(nodes=[("260722-a", "review", True)])
        errs = self._errs(man)
        self.assertTrue(any("must carry a 'manual' checkpoint" in e
                            for e in errs))

    def test_self_ext_with_manual_checkpoint_ok(self):
        man = _manifest(nodes=[("260722-a", "manual", True)])
        self.assertEqual(self._errs(man), [])

    def test_self_ext_default_none_checkpoint_refused(self):
        man = _manifest(nodes=[("260722-a", "none", True)])
        self.assertTrue(any("must carry a 'manual' checkpoint" in e
                            for e in self._errs(man)))

    # --- INLINE edge integrity (NOT graph_relation_errors) -------------------

    def test_dangling_edge_target_caught(self):
        # THE audit case: a manifest {nodes,edges} dangling edge MUST be caught by
        # the inline check (graph_relation_errors/build_adjacency would silently
        # pass it — they consume the KB-index entries/relates shape).
        man = _manifest(nodes=[("260722-a",)],
                        edges=[("260722-a", "260722-missing")])
        errs = self._errs(man)
        self.assertTrue(any("dangling edge" in e and "260722-missing" in e
                            for e in errs))

    def test_dangling_edge_source_caught(self):
        man = _manifest(nodes=[("260722-a",)],
                        edges=[("260722-ghost", "260722-a")])
        self.assertTrue(any("dangling edge" in e and "260722-ghost" in e
                            for e in self._errs(man)))

    def test_dangling_not_via_graph_relation_errors(self):
        # Prove the manifest validator does NOT rely on the KB-index scanner:
        # graph_relation_errors over a manifest-shaped dict finds nothing (no
        # `entries`), yet graph_manifest_errors catches the dangling edge.
        man = _manifest(nodes=[("260722-a",)],
                        edges=[("260722-a", "260722-missing")])
        dangling, _unknown = CLI.graph_relation_errors(man)
        self.assertEqual(dangling, [])  # the KB scanner is blind to a manifest
        self.assertTrue(self._errs(man))  # the inline check is not

    def test_self_edge_refused(self):
        man = _manifest(nodes=[("260722-a",)], edges=[("260722-a", "260722-a")])
        self.assertTrue(any("self-edge" in e for e in self._errs(man)))

    def test_duplicate_edge_refused(self):
        man = _manifest(nodes=[("260722-a",), ("260722-b",)],
                        edges=[("260722-a", "260722-b"),
                               ("260722-a", "260722-b")])
        self.assertTrue(any("duplicate edge" in e for e in self._errs(man)))

    def test_unknown_edge_kind_refused(self):
        man = _manifest(nodes=[("260722-a",), ("260722-b",)],
                        edges=[("260722-a", "260722-b", "spooky")])
        self.assertTrue(any("unknown kind" in e for e in self._errs(man)))

    # --- cycles (multi-hop + resource) ---------------------------------------

    def test_multi_hop_cycle_refused(self):
        man = _manifest(
            nodes=[("260722-a",), ("260722-b",), ("260722-c",)],
            edges=[("260722-a", "260722-b"), ("260722-b", "260722-c"),
                   ("260722-c", "260722-a")])
        self.assertTrue(any("cycle detected" in e for e in self._errs(man)))

    def test_resource_cycle_refused(self):
        man = _manifest(
            nodes=[("260722-a",), ("260722-b",)],
            edges=[("260722-a", "260722-b", "resource"),
                   ("260722-b", "260722-a", "resource")])
        self.assertTrue(any("cycle detected" in e for e in self._errs(man)))

    # --- _graph_build determinism --------------------------------------------

    def test_graph_build_sorted_deterministic(self):
        man = _manifest(
            nodes=[("260722-c",), ("260722-a",), ("260722-b",)],
            edges=[("260722-a", "260722-c", "resource"),
                   ("260722-a", "260722-b")])
        nodes, forward, reverse = CLI._graph_build(man)
        self.assertEqual(nodes, ["260722-a", "260722-b", "260722-c"])
        # edges sorted (kind, target)
        self.assertEqual(forward["260722-a"],
                         [("logical", "260722-b"), ("resource", "260722-c")])
        self.assertEqual(reverse["260722-c"], [("resource", "260722-a")])
        # byte-identical across repeated builds
        self.assertEqual(CLI._graph_build(man), (nodes, forward, reverse))

class TestManifestWriters(SyntheticKBTestCase):
    """The single-writer CLI writers, exercised by calling the cmd_* functions
    directly with the KB env pinned (the `graph` argparse group is wired in
    Task 8; here we test the writer semantics, not the CLI dispatch)."""

    def _args(self, **kw):
        import types
        kw.setdefault("format", "text")
        return types.SimpleNamespace(**kw)

    def _seed_plan(self, feature, *, self_ext=False, checkpoint=None):
        text = _plan("", self_ext=self_ext)
        if checkpoint:
            text = text.replace(
                "> Status: draft\n",
                "> Status: draft\n> Checkpoint: %s\n" % checkpoint)
        path = os.path.join(self.kdir, "work", feature, "plan.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def _with_env(self, fn):
        import contextlib
        import io
        prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return fn()
        finally:
            if prev is None:
                os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
            else:
                os.environ["AGENTWARE_KNOWLEDGE_DIR"] = prev

    def test_new_add_node_edge_roundtrip_and_graphmd(self):
        import json as _json
        self._seed_plan("260722-a")
        self._seed_plan("260722-b")

        def run():
            self.assertEqual(CLI.cmd_graph_new(self._args(name="g1")), 0)
            self.assertEqual(
                CLI.cmd_graph_add_node(self._args(name="g1", feature="260722-a")),
                0)
            self.assertEqual(
                CLI.cmd_graph_add_node(self._args(name="g1", feature="260722-b")),
                0)
            self.assertEqual(
                CLI.cmd_graph_add_edge(self._args(name="g1", src="260722-a",
                                                  dst="260722-b")), 0)
        self._with_env(run)

        mpath = CLI._graph_manifest_path(self.kdir, "g1")
        self.assertTrue(os.path.exists(mpath))
        with open(mpath, encoding="utf-8") as f:
            man = _json.load(f)
        self.assertEqual([n["feature"] for n in man["nodes"]],
                         ["260722-a", "260722-b"])
        self.assertEqual(man["edges"],
                         [{"from": "260722-a", "to": "260722-b",
                           "kind": "logical"}])
        # derived graph.md regenerated
        self.assertTrue(os.path.exists(CLI._graph_md_path(self.kdir, "g1")))

    def test_add_node_computes_self_extension_from_plan(self):
        self._seed_plan("260722-se", self_ext=True, checkpoint="manual")

        def run():
            self.assertEqual(CLI.cmd_graph_new(self._args(name="g2")), 0)
            self.assertEqual(
                CLI.cmd_graph_add_node(self._args(name="g2",
                                                  feature="260722-se")), 0)
        self._with_env(run)
        import json as _json
        with open(CLI._graph_manifest_path(self.kdir, "g2"), encoding="utf-8") as f:
            man = _json.load(f)
        self.assertTrue(man["nodes"][0]["self_extension"])
        self.assertEqual(man["nodes"][0]["checkpoint"], "manual")

    def test_add_node_infers_write_set_when_undeclared(self):
        # Finding 2/4/6/7/8: a plan with NO `## Write set` section must get the
        # CONSERVATIVE inferred write-set (never an empty set that overlaps
        # nothing). Two such same-target self-ext plans must therefore both stamp
        # a non-empty set that OVERLAPS, so the resource layer serializes them.
        self._seed_plan("260722-u1", self_ext=True, checkpoint="manual")
        self._seed_plan("260722-u2", self_ext=True, checkpoint="manual")

        def run():
            self.assertEqual(CLI.cmd_graph_new(self._args(name="gws")), 0)
            for f in ("260722-u1", "260722-u2"):
                self.assertEqual(
                    CLI.cmd_graph_add_node(self._args(name="gws", feature=f)), 0)
        self._with_env(run)
        import json as _json
        with open(CLI._graph_manifest_path(self.kdir, "gws"),
                  encoding="utf-8") as f:
            man = _json.load(f)
        ws = {n["feature"]: n["write_set"] for n in man["nodes"]}
        self.assertTrue(ws["260722-u1"], "undeclared write-set must be inferred")
        self.assertTrue(ws["260722-u2"])
        # Inferred sets of two same-target self-ext plans OVERLAP -> serialized.
        self.assertTrue(
            CLI._write_sets_overlap(ws["260722-u1"], ws["260722-u2"]))

    def test_add_node_uses_declared_write_set_verbatim(self):
        # The declared `## Write set` still WINS over inference when present.
        text = _plan("## Write set\n\n- docs/loop.md\n")
        path = os.path.join(self.kdir, "work", "260722-decl", "plan.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

        def run():
            self.assertEqual(CLI.cmd_graph_new(self._args(name="gdc")), 0)
            self.assertEqual(CLI.cmd_graph_add_node(
                self._args(name="gdc", feature="260722-decl")), 0)
        self._with_env(run)
        import json as _json
        with open(CLI._graph_manifest_path(self.kdir, "gdc"),
                  encoding="utf-8") as f:
            man = _json.load(f)
        self.assertEqual(man["nodes"][0]["write_set"], ["docs/loop.md"])

    def test_add_edge_that_closes_cycle_refused_no_write(self):
        import json as _json
        self._seed_plan("260722-a")
        self._seed_plan("260722-b")

        def run():
            CLI.cmd_graph_new(self._args(name="g3"))
            CLI.cmd_graph_add_node(self._args(name="g3", feature="260722-a"))
            CLI.cmd_graph_add_node(self._args(name="g3", feature="260722-b"))
            CLI.cmd_graph_add_edge(self._args(name="g3", src="260722-a",
                                              dst="260722-b"))
            # This edge would close a 2-cycle -> refused, NOT written.
            rc = CLI.cmd_graph_add_edge(self._args(name="g3", src="260722-b",
                                                   dst="260722-a"))
            return rc
        rc = self._with_env(run)
        self.assertEqual(rc, 1)
        with open(CLI._graph_manifest_path(self.kdir, "g3"), encoding="utf-8") as f:
            man = _json.load(f)
        self.assertEqual(len(man["edges"]), 1)  # the cycle edge was NOT persisted

    def test_add_edge_dangling_refused_no_write(self):
        self._seed_plan("260722-a")

        def run():
            CLI.cmd_graph_new(self._args(name="g4"))
            CLI.cmd_graph_add_node(self._args(name="g4", feature="260722-a"))
            return CLI.cmd_graph_add_edge(self._args(name="g4", src="260722-a",
                                                     dst="260722-ghost"))
        self.assertEqual(self._with_env(run), 1)
