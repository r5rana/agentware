"""ClusterLearningsTest, SkillProposeCmdTest, EndToEndSkillPromoteTest -- split from `test_skill_promote` (task 58).

See `tests.unit.features._skill_promote_support` for the
shared header.
"""

from tests.unit.features._skill_promote_support import (  # noqa: F401
    SyntheticKBTestCase, _CHAIN, _COVERED_DEBUG, _TIGHT, _learn,
    build_synthetic_kb, json, load_cli, os)


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
