"""ClusterLearningsTest, SkillProposeCmdTest, EndToEndSkillPromoteTest -- split from `test_skill_promote` (task 58).

See `tests.unit.features._skill_promote_support` for the
shared header.

Task 99 adds `SkillClusterHelperTest` / `GrowClusterTest`, the BEHAVIOURAL cover
for the six seams the `ops.skillcluster` branch-ceiling payoff extracted. The
classes live here, beside the `cluster_learnings` end-to-end cover they hold the
seams of, and NOT in `tests/unit/ops/test_skillcluster.py`: that path is a LIVE
row in `tests/fixtures/test-ownership-debt.json`, so creating the file would
strand the row as `stale_debt` and redden the ownership gate until the fixture
and its `ratchet.max_rows` are edited too -- a two-file move outside this task.
"""

from tests._paths import TESTS_DIR

import sys

from tests.unit.features._skill_promote_support import (  # noqa: F401
    SyntheticKBTestCase, _CHAIN, _COVERED_DEBUG, _TIGHT, _learn,
    build_synthetic_kb, json, load_cli, os, unittest)

_SCRIPTS = os.path.join(os.path.dirname(TESTS_DIR), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from agentware_lib.ops import skillcluster as _sc  # noqa: E402

# A hand-built weighted graph, so every assertion below is an EXACT number
# rather than a property of the tokenizer. Triangle a~b~c~a, plus a strong
# outside neighbour `d` hanging off `c` and a weak one `e` hanging off `a`.
_W = {
    "a": {"b": 0.8, "c": 0.6, "e": 0.2},
    "b": {"a": 0.8, "c": 0.4},
    "c": {"a": 0.6, "b": 0.4, "d": 0.5},
    "d": {"c": 0.5},
    "e": {"a": 0.2},
}
_TAGS = {"a": {"spine", "one"}, "b": {"spine"}, "c": {"spine", "two"},
         "d": {"outside"}, "e": {"outside"}}

# The single-linkage chain the density gate exists to reject: a~b, b~c, a!~c.
_CHAIN_W = {"a": {"b": 0.5}, "b": {"a": 0.5, "c": 0.5}, "c": {"b": 0.5}}


class SkillClusterHelperTest(SyntheticKBTestCase):
    """Task 99: the pure metric + graph-construction seams lifted out of
    `_skill_cluster_scores` and `cluster_learnings`."""

    # --- _intra_scores owns (cohesion, density) over the members' own pairs --

    def test_intra_scores_complete_triangle(self):
        cohesion, density = _sc._intra_scores(["a", "b", "c"], _W)
        # all three pairs are edges: (0.8 + 0.6 + 0.4) / 3, and 3 of 3 possible.
        self.assertAlmostEqual(cohesion, 0.6, places=12)
        self.assertAlmostEqual(density, 1.0, places=12)

    def test_intra_scores_counts_only_edges_that_exist(self):
        # a~b is an edge; a~d and b~d are not, so density is 1 of 3 possible and
        # cohesion averages over the ONE edge, never over the missing pairs.
        cohesion, density = _sc._intra_scores(["a", "b", "d"], _W)
        self.assertAlmostEqual(cohesion, 0.8, places=12)
        self.assertAlmostEqual(density, 1.0 / 3.0, places=12)

    def test_intra_scores_singleton_is_vacuously_dense(self):
        # Fewer than two members has no pair to measure: density 1.0 by
        # convention, cohesion 0.0 because there is no weight to average.
        self.assertEqual(_sc._intra_scores(["a"], _W), (0.0, 1.0))
        self.assertEqual(_sc._intra_scores([], _W), (0.0, 1.0))

    # --- _mean_external owns the halo ---------------------------------------

    def test_mean_external_averages_each_members_best_outside_edge(self):
        # a -> e (0.2); b -> nothing outside (0.0); c -> d (0.5).
        got = _sc._mean_external(["a", "b", "c"], _W, {"a", "b", "c"})
        self.assertAlmostEqual(got, 0.7 / 3.0, places=12)

    def test_mean_external_ignores_membership_not_weight(self):
        # Same node, different member set: with only `b` a member, its strongest
        # edge (a, 0.8) is EXTERNAL and dominates the 0.4 to c.
        self.assertAlmostEqual(_sc._mean_external(["b"], _W, {"b"}), 0.8,
                               places=12)
        # And a member set with no outside neighbour at all scores exactly 0.0.
        self.assertEqual(_sc._mean_external(["d"], _W, {"c", "d"}), 0.0)
        self.assertEqual(_sc._mean_external([], _W, set()), 0.0)

    # --- _tag_spine owns the dominant tag + its share -----------------------

    def test_tag_spine_picks_dominant_tag_and_share(self):
        # "spine" is on all three; "one"/"two" on one each.
        self.assertEqual(_sc._tag_spine(["a", "b", "c"], _TAGS), ("spine", 1.0))
        # 2 of 3 members carry "spine" here -> share 2/3, not 1.0.
        tag, share = _sc._tag_spine(["a", "b", "d"], _TAGS)
        self.assertEqual(tag, "spine")
        self.assertAlmostEqual(share, 2.0 / 3.0, places=12)

    def test_tag_spine_breaks_ties_by_name_ascending(self):
        tags = {"a": {"zebra", "alpha"}, "b": {"zebra", "alpha"}}
        self.assertEqual(_sc._tag_spine(["a", "b"], tags), ("alpha", 1.0))

    def test_tag_spine_untagged_members_have_no_spine(self):
        self.assertEqual(_sc._tag_spine(["a", "b"], {}), (None, 0.0))
        self.assertEqual(_sc._tag_spine(["a"], {"a": None}), (None, 0.0))
        self.assertEqual(_sc._tag_spine([], _TAGS), (None, 0.0))

    # --- the parent is assembly + the separation margin ---------------------

    def test_skill_cluster_scores_composes_the_three_seams(self):
        sc = _sc._skill_cluster_scores({"c", "a", "b"}, _W, _TAGS)
        self.assertAlmostEqual(sc["cohesion"], 0.6, places=12)
        self.assertAlmostEqual(sc["density"], 1.0, places=12)
        # separation is exactly cohesion - mean_external, the one value the
        # parent still computes itself.
        self.assertAlmostEqual(sc["separation"], 0.6 - 0.7 / 3.0, places=12)
        self.assertEqual(sc["tag_spine_tag"], "spine")
        self.assertAlmostEqual(sc["tag_spine_share"], 1.0, places=12)

    # --- _cluster_nodes owns the node set -----------------------------------

    def test_cluster_nodes_keeps_learnings_in_id_order(self):
        data = build_synthetic_kb(self.kdir, entries=_TIGHT + _CHAIN)
        ids, tokmap, tagmap = _sc._cluster_nodes(self.kdir, data)
        self.assertEqual(ids, sorted(e["id"] for e in _TIGHT + _CHAIN))
        self.assertEqual(set(tokmap), set(ids))
        self.assertEqual(tagmap["learn-pay-retry"],
                         {"payment", "refund", "retry"})
        # The corpus is the BM25 one, so the body vocabulary is tokenized in.
        self.assertIn("backoff", tokmap["learn-pay-retry"])

    def test_cluster_nodes_drops_non_learnings_and_idless_entries(self):
        entries = list(_TIGHT)
        skill = dict(entries[0])
        skill.update({"id": "skill-not-a-learning", "category": "skills",
                      "path": "skills/not-a-learning.md"})
        data = build_synthetic_kb(self.kdir, entries=entries + [skill])
        ids, _tok, _tag = _sc._cluster_nodes(self.kdir, data)
        self.assertEqual(ids, sorted(e["id"] for e in _TIGHT))

    # --- _cluster_adjacency owns the edge set -------------------------------

    def test_cluster_adjacency_is_symmetric_and_bar_gated(self):
        ids = ["p", "q", "r"]
        tok = {"p": {"k1", "k2"}, "q": {"k1", "k2"}, "r": {"z1", "z2"}}
        tags = {"p": set(), "q": set(), "r": set()}
        adjw = _sc._cluster_adjacency(ids, tok, tags, 0.30)
        # p/q share every token -> Jaccard 1.0, recorded BOTH ways.
        self.assertEqual(adjw["p"], {"q": 1.0})
        self.assertEqual(adjw["q"], {"p": 1.0})
        # r shares nothing -> 0.0 < 0.30, so it gets an EMPTY row, not a KeyError.
        self.assertEqual(adjw["r"], {})

    def test_cluster_adjacency_edge_min_is_inclusive(self):
        ids = ["p", "q"]
        tok = {"p": {"a", "b"}, "q": {"b", "c"}}          # Jaccard = 1/3
        tags = {"p": set(), "q": set()}
        third = 1.0 / 3.0
        self.assertEqual(_sc._cluster_adjacency(ids, tok, tags, third),
                         {"p": {"q": third}, "q": {"p": third}})
        self.assertEqual(_sc._cluster_adjacency(ids, tok, tags, 0.34),
                         {"p": {}, "q": {}})


class GrowClusterTest(SyntheticKBTestCase):
    """Task 99: `_grow_cluster` -- the density-gated quasi-clique growth loop.
    The gate ORDER is the behaviour under test, so each case isolates ONE gate."""

    def test_growth_refuses_the_candidate_that_would_drop_density(self):
        # THE load-bearing case. `c` IS connected (b~c), so connectivity admits
        # it; only the density-DURING-growth gate can refuse it, and it must,
        # because a~b~c with a!~c is density 2/3 = 0.667 < 0.70.
        got = _sc._grow_cluster("a", ["a", "b", "c"], set(), _CHAIN_W,
                                _sc._SKILL_CLUSTER_DENSITY_MIN, 6)
        self.assertEqual(got, {"a", "b"})

    def test_the_same_candidate_is_admitted_once_the_bar_drops(self):
        # Same graph, same seed, bar lowered under 2/3: `c` now qualifies. This
        # is what proves the refusal above came from the DENSITY gate and not
        # from connectivity or the size clamp.
        got = _sc._grow_cluster("a", ["a", "b", "c"], set(), _CHAIN_W, 0.6, 6)
        self.assertEqual(got, {"a", "b", "c"})

    def test_growth_admits_a_true_triangle_whole(self):
        got = _sc._grow_cluster("a", ["a", "b", "c", "d", "e"], set(), _W,
                                _sc._SKILL_CLUSTER_DENSITY_MIN, 6)
        # a, b, c are mutually adjacent (density 1.0); d and e are single-linkage
        # hangers-on that would take density to 6/10 and 6/10, so both are out.
        self.assertEqual(got, {"a", "b", "c"})

    def test_growth_stops_at_max_size(self):
        got = _sc._grow_cluster("a", ["a", "b", "c", "d", "e"], set(), _W,
                                _sc._SKILL_CLUSTER_DENSITY_MIN, 2)
        # Greedy: the strongest admissible edge out of {a} is a~b at 0.8.
        self.assertEqual(got, {"a", "b"})

    def test_growth_never_takes_a_member_of_an_earlier_cluster(self):
        got = _sc._grow_cluster("a", ["a", "b", "c", "d", "e"], {"b"}, _W,
                                _sc._SKILL_CLUSTER_DENSITY_MIN, 6)
        self.assertEqual(got, {"a", "c"})

    def test_growth_reads_used_and_never_mutates_it(self):
        used = {"b"}
        _sc._grow_cluster("a", ["a", "b", "c", "d", "e"], used, _W,
                          _sc._SKILL_CLUSTER_DENSITY_MIN, 6)
        self.assertEqual(used, {"b"},
                         "the caller consumes a cluster only once it QUALIFIES; "
                         "growth must not claim ids for a cluster that is then "
                         "thrown away")

    def test_an_isolated_seed_grows_to_itself(self):
        self.assertEqual(
            _sc._grow_cluster("z", ["z"], set(), {"z": {}},
                              _sc._SKILL_CLUSTER_DENSITY_MIN, 6),
            {"z"})


class ClusterLearningsTest(SyntheticKBTestCase):
    def setUp(self):
        super().setUp()
        self.entries = _TIGHT + _CHAIN
        self.index_data = build_synthetic_kb(self.kdir, entries=self.entries)

    def test_cluster_learnings(self):
        mod = load_cli()
        data = self.read_index()
        clusters = mod.cluster_learnings(self.kdir, data)
        member_sets = [frozenset(c["members"]) for c in clusters]

        tight = frozenset(e["id"] for e in _TIGHT)
        self.assertIn(tight, member_sets,
                      "the dense payment/refund clique must surface as a skill "
                      "candidate")

        # The chain must NOT appear as (or inside) any qualified cluster.
        chain_ids = {e["id"] for e in _CHAIN}
        for c in clusters:
            self.assertFalse(
                chain_ids & set(c["members"]),
                "single-linkage chain A~B~C (A!~C) must be rejected by the "
                "density gate, not clustered: %r" % c)

        # The surfaced cluster carries sane, in-range scores + a dominant tag-spine.
        tc = next(c for c in clusters if frozenset(c["members"]) == tight)
        self.assertEqual(tc["size"], 3)
        self.assertGreaterEqual(tc["density"], mod._SKILL_CLUSTER_DENSITY_MIN)
        self.assertGreaterEqual(tc["separation"], mod._SKILL_CLUSTER_SEPARATION_MIN)
        self.assertGreaterEqual(tc["tag_spine_share"],
                                mod._SKILL_CLUSTER_TAGSPINE_MIN)
        self.assertIn(tc["tag_spine"], ("payment", "refund"))

    def test_cluster_learnings_deterministic_and_read_only(self):
        mod = load_cli()
        idx = os.path.join(self.kdir, "index.json")
        with open(idx, "rb") as f:
            before = f.read()
        data = self.read_index()
        r1 = mod.cluster_learnings(self.kdir, data)
        r2 = mod.cluster_learnings(self.kdir, data)
        self.assertEqual(r1, r2)
        with open(idx, "rb") as f:
            after = f.read()
        self.assertEqual(before, after,
                         "cluster_learnings must not mutate index.json")

    def test_size_gate_excludes_duplicate_pair(self):
        # A near-duplicate PAIR (size 2) must never become a skill cluster — that is
        # `conflict_pairs`' territory. Two entries alone can never reach min_size 3.
        mod = load_cli()
        kdir2 = self.kdir + "-pair"
        os.makedirs(kdir2, exist_ok=True)
        self.addCleanup(__import__("shutil").rmtree, kdir2, True)
        pair = [
            _learn("learn-dup-a", ["cache", "race"],
                   "Cache invalidation race.", ["cache", "race", "invalidate"]),
            _learn("learn-dup-b", ["cache", "race"],
                   "Cache invalidation race dup.", ["cache", "race", "invalidate"]),
        ]
        data = build_synthetic_kb(kdir2, entries=pair)
        self.assertEqual(mod.cluster_learnings(kdir2, data), [])

class SkillProposeCmdTest(SyntheticKBTestCase):
    """Task 5: the on-demand `scripts/agentware skill propose` verb — the backfill
    / fresh-clone path, driven end-to-end through `main()` (argparse dispatch),
    mirroring `audit --stale --propose`. DETECTION + PROPOSAL ONLY.
    SPELLING (task 54): this exercised `skill propose` with no arguments. The
    merged CLI's `skill propose` is the donor's proposal-AUTHORING lifecycle and
    hard-requires --slug/--ref (the union fixture is derived, and its adjudication
    table only fires where BOTH trees declare an action, so a donor-only
    `required` cannot be relaxed). The argument-less skill-CANDIDATE detection
    this class covers keeps its own spelling, `skill candidates` — same handler,
    same report, same assertions. The capability is preserved; only the
    invocation moved.
    """

    def _auto_dirs(self):
        auto = os.path.join(self.kdir, "work", "auto")
        return sorted(os.listdir(auto)) if os.path.isdir(auto) else []

    def _index_bytes(self):
        with open(os.path.join(self.kdir, "index.json"), "rb") as f:
            return f.read()

    # --- live run emits exactly one tier-2 DRAFT workorder ------------------

    def test_skill_propose_cmd_emits_draft(self):
        self.index_data = build_synthetic_kb(self.kdir, entries=_TIGHT)
        before = self._index_bytes()
        code, out, err = self.run_cli(["skill", "candidates"])
        self.assertEqual(code, 0, err)
        dirs = self._auto_dirs()
        self.assertEqual(len(dirs), 1, dirs)
        self.assertTrue(dirs[0].startswith("skill-candidate-"), dirs)
        # It is a NON-auto-started DRAFT.
        plan = os.path.join(self.kdir, "work", "auto", dirs[0], "plan.md")
        with open(plan, encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("> Status: draft", txt)
        # Directs authoring via `skill add`, NEVER a raw `index add`.
        tasks = txt.split("## Tasks", 1)[1]
        self.assertIn("skill add", tasks)
        self.assertNotIn("index add", tasks)
        # The verb is read-only w.r.t. the KB index — no mutation.
        self.assertEqual(self._index_bytes(), before,
                         "skill propose must not mutate index.json")
        self.assertIn("proposed", out)

    # --- --dry-run ranks WITHOUT emitting -----------------------------------

    def test_skill_propose_cmd_dry_run_no_emit(self):
        self.index_data = build_synthetic_kb(self.kdir, entries=_TIGHT)
        code, out, err = self.run_cli(["skill", "candidates", "--dry-run"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self._auto_dirs(), [], "dry-run must emit nothing")
        # Ranked listing surfaces the member ids.
        for e in _TIGHT:
            self.assertIn(e["id"], out)

    # --- --format json surfaces the structured report ----------------------

    def test_skill_propose_cmd_json(self):
        self.index_data = build_synthetic_kb(self.kdir, entries=_TIGHT)
        code, out, err = self.run_cli(
            ["skill", "candidates", "--dry-run", "--format", "json"])
        self.assertEqual(code, 0, err)
        obj = json.loads(out)
        for k in ("candidates", "emitted", "cap", "ranked", "proposed",
                  "extended", "existing", "deferred", "blocked", "clean"):
            self.assertIn(k, obj)
        self.assertEqual(obj["emitted"], 0)          # dry-run
        self.assertEqual(len(obj["ranked"]), 1)
        self.assertEqual(sorted(obj["ranked"][0]["members"]),
                         sorted(e["id"] for e in _TIGHT))

    # --- idempotent: a second run re-proposes nothing -----------------------

    def test_skill_propose_cmd_idempotent(self):
        self.index_data = build_synthetic_kb(self.kdir, entries=_TIGHT)
        code, _o, err = self.run_cli(["skill", "candidates"])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self._auto_dirs()), 1)
        code, out, err = self.run_cli(["skill", "candidates"])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self._auto_dirs()), 1, "re-run must not duplicate")
        # JSON re-run confirms the existing draft is an idempotent no-op.
        code, out, err = self.run_cli(["skill", "candidates", "--format", "json"])
        obj = json.loads(out)
        self.assertEqual(obj["emitted"], 0)
        self.assertEqual(len(obj["existing"]), 1)

    # --- the verb is `propose`, NOT `approve` (the install gate) ------------

    def test_skill_approve_is_not_a_verb(self):
        # `approve` is reserved by the shipped `skill sync --approve` install gate;
        # this pipeline never registers or installs, so there is no `skill approve`
        # subcommand — argparse rejects the unknown choice (exits 2).
        with self.assertRaises(SystemExit) as cm:
            self.run_cli(["skill", "approve"])
        self.assertEqual(cm.exception.code, 2,
                         "there must be no `skill approve` subcommand")

class EndToEndSkillPromoteTest(SyntheticKBTestCase):
    """Task 7 [e2e]: the full detect -> propose -> (extend|author) -> idempotent
    pipeline driven end-to-end through `main()` over one hermetic synthetic KB.
    DETECTION + PROPOSAL ONLY — never authors a stub, never `index add`, never
    installs."""

    _CORPUS = _TIGHT + _CHAIN + _COVERED_DEBUG

    def setUp(self):
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=self._CORPUS)
        # Pin the propose knobs ON regardless of any ambient machine config.env
        # (env wins over config.env), and enable nested-unittest for the dream run.
        self._set_env("AGENTWARE_SKILL_PROMOTE", "propose")
        self._set_env("AGENTWARE_SKILL_PROMOTE_CAP", "5")
        self._set_env("AGENTWARE_NESTED_UNITTEST", "1")

    def _set_env(self, key, val):
        prev = os.environ.get(key)
        os.environ[key] = val

        def _restore():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
        self.addCleanup(_restore)

    def _auto_dirs(self):
        auto = os.path.join(self.kdir, "work", "auto")
        return sorted(os.listdir(auto)) if os.path.isdir(auto) else []

    def _candidate_dirs(self):
        return [d for d in self._auto_dirs() if d.startswith("skill-candidate-")]

    def _plan_text(self, slug):
        with open(os.path.join(self.kdir, "work", "auto", slug, "plan.md"),
                  encoding="utf-8") as f:
            return f.read()

    def _index_bytes(self):
        with open(os.path.join(self.kdir, "index.json"), "rb") as f:
            return f.read()

    # --- the whole pipeline: propose (author + extend, reject chain) then dream k
    def test_e2e_full_pipeline_propose_then_dream(self):
        before_idx = self._index_bytes()

        # 1) On-demand `skill propose` over the mixed corpus.
        code, out, err = self.run_cli(["skill", "candidates", "--format", "json"])
        self.assertEqual(code, 0, err)
        report = json.loads(out)

        # Exactly TWO qualifying clusters (tight + covered). The A~B~C chain is
        # rejected by the density gate, so it is NEVER a candidate.
        self.assertEqual(report["candidates"], 2, report)
        self.assertEqual(len(report["proposed"]), 1, report)   # tight -> author
        self.assertEqual(len(report["extended"]), 1, report)   # covered -> extend
        self.assertEqual(report["extended"][0]["skill"], "systematic-debugging")
        self.assertEqual(report["emitted"], 2, report)
        self.assertFalse(report["deferred"], report)           # empty list
        self.assertFalse(report["blocked"], report)

        # Two workorders total; both are non-auto-start DRAFTS.
        cand = self._candidate_dirs()
        self.assertEqual(len(cand), 2, cand)
        for slug in cand:
            txt = self._plan_text(slug)
            self.assertIn("> Status: draft", txt)
            self.assertNotIn("> Status: running", txt)
            self.assertNotIn("> Status: ready", txt)
            # DETECTION + PROPOSAL ONLY: author via `skill add`, made invocable via
            # `skill sync --approve`; NEVER a raw `index add` promotion.
            self.assertIn("skill add", txt)
            self.assertNotIn("index add", txt)
            self.assertIn("skill sync --approve", txt)
            # The rejected chain never leaks into any emitted workorder.
            for e in _CHAIN:
                self.assertNotIn(e["id"], txt,
                                 "rejected chain member %s leaked" % e["id"])

        # The tight-cluster author workorder carries the payment/refund member ids
        # in its fenced Context; the extend workorder names the covered skill.
        tight_feature = report["proposed"][0]        # "auto/skill-candidate-<fp>"
        tight_txt = self._plan_text(tight_feature.split("/", 1)[1])
        context = (tight_txt.split("## Context", 1)[1]
                   .split("## Target packages", 1)[0])
        for e in _TIGHT:
            self.assertIn(e["id"], context, "%s must ride Context" % e["id"])
        ext_txt = self._plan_text(
            report["extended"][0]["feature"].split("/", 1)[1])
        self.assertIn("systematic-debugging", ext_txt)
        self.assertIn("EXTEND", ext_txt)

        # The whole pipeline is read-only w.r.t. the KB index.
        self.assertEqual(self._index_bytes(), before_idx,
                         "skill propose must not mutate index.json")

        # 2) Idempotent re-run of `skill propose`: nothing new, same two drafts.
        code, out, err = self.run_cli(["skill", "candidates", "--format", "json"])
        self.assertEqual(code, 0, err)
        r2 = json.loads(out)
        self.assertEqual(r2["emitted"], 0, r2)
        self.assertEqual(len(r2["existing"]), 2, r2)
        self.assertEqual(self._candidate_dirs(), cand,
                         "re-run must not duplicate")

        # 3) A live dream step `k` over the SAME corpus is likewise idempotent —
        # it shares the exact `_skill_promote_run` emit seam, so the slugs already
        # exist and nothing new is emitted.
        code, out, err = self.run_cli(
            ["dream", "--steps", "k", "--force", "--format", "json"])
        self.assertEqual(code, 0, err)
        step = next(s for s in json.loads(out)["steps"] if s["step"] == "k")
        self.assertEqual(step["status"], "ok", step)
        self.assertEqual(step["proposed"], 0, "dream k must not re-emit")
        self.assertEqual(step["extended"], 0, step)
        self.assertFalse(step["deferred"], step)
        self.assertFalse(step["blocked"], step)
        self.assertEqual(self._candidate_dirs(), cand,
                         "dream k must not duplicate")

    # --- the g3 draft cap defers cleanly (transient, not a failure) ----------
    def test_e2e_defers_cleanly_at_g3_cap(self):
        # Pre-load the g3 draft cap (WORKORDER_DRAFT_CAP=5) so the shipped emitter
        # must DEFER rather than emit — a defer is a clean transient, not a fault.
        for i in range(5):
            d = os.path.join(self.kdir, "work", "auto", "wo-filler-%d" % i)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as f:
                f.write("# Plan — filler %d\n\n> Status: draft\n\n"
                        "## Tasks\n- ⬜ **1** x\n" % i)
        load_cli()  # ensure WORKORDER_DRAFT_CAP is the shipped 5
        before_idx = self._index_bytes()
        code, out, err = self.run_cli(["skill", "candidates", "--format", "json"])
        self.assertEqual(code, 0, err)          # a defer is NOT a failure
        report = json.loads(out)
        self.assertTrue(report["deferred"], report)   # non-empty defer list
        self.assertEqual(report["emitted"], 0, report)
        # No NEW skill-candidate workorder written; the index is untouched.
        self.assertEqual(self._candidate_dirs(), [],
                         "at the g3 cap nothing new may be emitted")
        self.assertEqual(self._index_bytes(), before_idx)
