"""Tests for skill-candidate clustering + promotion (260722-auto-skill-promotion).

Stdlib `unittest` only, driven against a synthetic temp KB (no `init`, no
network). Detection-only helpers are read-only (INV-2).

Task 1 coverage (`cluster_learnings`):
  - A tight, cohesive learning cluster (dense clique + dominant tag-spine) is
    surfaced as ONE skill candidate.
  - A single-linkage `A~B~C` chain (A!~C) is REJECTED by the density gate — it is
    NOT a clique, so it must never become a skill cluster.
  - The result is deterministic and read-only w.r.t. index.json.
"""

import json
import os
import unittest

try:
    from tests._fixtures import (SyntheticKBTestCase, build_synthetic_kb,
                                 load_cli)
except ImportError:  # allow `python3 -m unittest tests.test_skill_promote`
    from _fixtures import (SyntheticKBTestCase, build_synthetic_kb, load_cli)


def _learn(eid, tags, summary, body_terms):
    """Build one learnings entry. `body_terms` is distinctive vocabulary so
    unrelated entries never accrue a stray token-Jaccard edge."""
    slug = eid.replace("learn-", "")
    return {
        "id": eid,
        "title": eid,
        "category": "learnings",
        "path": "learnings/%s.md" % slug,
        "tags": list(tags),
        "created": "2026-05-01",
        "last_verified": "2026-05-01",
        "author": "testuser",
        "summary": summary,
        "body": "# %s\n\n%s\n" % (eid, " ".join(body_terms)),
    }


# --- Fixture corpus ----------------------------------------------------------
# A TIGHT cohesive cluster: three learnings all sharing a `payment`/`refund`
# tag-spine (tag-Jaccard 0.5 on every pair => a full triangle, density 1.0),
# distinct running vocabulary (so it reads like the STEPS of one procedure).
_TIGHT = [
    _learn("learn-pay-idempotency", ["payment", "refund", "idempotency"],
           "Make refund submission idempotent under retries.",
           ["idempotency", "key", "dedupe", "submit", "ledger"]),
    _learn("learn-pay-retry", ["payment", "refund", "retry"],
           "Retry refund settlement with capped backoff.",
           ["retry", "backoff", "settlement", "queue", "attempt"]),
    _learn("learn-pay-webhook", ["payment", "refund", "webhook"],
           "Verify refund webhook signatures before applying.",
           ["webhook", "signature", "verify", "callback", "reconcile"]),
]

# A single-linkage CHAIN A~B~C where A!~C. Each adjacent pair shares ONE bridge
# tag (tag-Jaccard 1/3 = 0.33 >= 0.30 => an edge), but the endpoints share NO tag
# and NO vocabulary (no A-C edge). {A,B,C} density = 2/3 = 0.67 < 0.70 => rejected.
# NB: summaries + bodies use FULLY DISJOINT vocabulary per node so the ONLY
# cross-node signal is the bridge tag — otherwise shared filler words ("topic",
# "bridges", "beta"...) would mint a spurious A-C token edge and forge a triangle.
_CHAIN = [
    _learn("learn-chain-a", ["alpha", "bridge-ab"],
           "Aardvark azimuth anchor aperture almanac.",
           ["aardvark", "azimuth", "anchor", "aperture", "almanac"]),
    _learn("learn-chain-b", ["bridge-ab", "bridge-bc"],
           "Beacon bramble bison bulwark basalt.",
           ["beacon", "bramble", "bison", "bulwark", "basalt"]),
    _learn("learn-chain-c", ["bridge-bc", "gamma"],
           "Gadget gimbal gully granite garnet.",
           ["gadget", "gimbal", "gully", "granite", "garnet"]),
]


# A cohesive cluster whose member SUMMARIES deliberately name kernel tokens
# (`scripts/agentware`, `.claude`, `AGENTS.md`) — the exact shape that would trip
# `cmd_plan_new`'s `_pref_hits_kernel` hard-refusal IF summaries rode the context.
# The emitter must STILL emit because only member ids + category ride --context.
# Shared 3-tag spine (tag-Jaccard 0.5 on every pair => full triangle) + distinct
# bodies so `cluster_learnings` surfaces them as ONE candidate.
_KERNEL_SUMMARY = [
    _learn("learn-toolkit-recall", ["toolkit", "promote", "recall-note"],
           "Run scripts/agentware recall before editing AGENTS.md rules.",
           ["recall", "budget", "topk", "curated", "ladder"]),
    _learn("learn-toolkit-learn", ["toolkit", "promote", "learn-note"],
           "Promote via scripts/agentware learn; never hand-edit .claude files.",
           ["promote", "durable", "supersede", "handle", "verify"]),
    _learn("learn-toolkit-audit", ["toolkit", "promote", "audit-note"],
           "scripts/agentware audit --stale flags drift in .claude/skills.",
           ["audit", "stale", "drift", "orphan", "provenance"]),
]


class EmitSkillCandidateTest(SyntheticKBTestCase):
    """Task 2: `_skill_candidate_fingerprint` + `_emit_skill_candidate_workorder`
    mirror `_stale_report_fingerprint` / `_emit_stale_review_workorder`."""

    def setUp(self):
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=_KERNEL_SUMMARY)
        # The emitter calls cmd_plan_new -> require_knowledge_dir(), which resolves
        # from the environment. Bind it to the synthetic KB so a direct emitter
        # call stays hermetic (never touches the operator's real KB).
        self._prev_kdir = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(self._restore_kdir)

    def _restore_kdir(self):
        if self._prev_kdir is None:
            os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        else:
            os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self._prev_kdir

    def _cluster(self, mod):
        clusters = mod.cluster_learnings(self.kdir, self.read_index())
        self.assertTrue(clusters, "the toolkit cluster must be detected")
        return clusters[0]

    def _workorder_plan(self, feature):
        return os.path.join(
            self.kdir, feature.replace("auto/", "work/auto/", 1), "plan.md")

    # --- fingerprint is member-set keyed + stable ---------------------------

    def test_fingerprint_keys_on_member_set_only(self):
        mod = load_cli()
        a = {"members": ["learn-b", "learn-a", "learn-c"], "size": 3,
             "cohesion": 0.9, "density": 1.0, "separation": 0.3}
        # Same member SET, different order + different scores -> same fingerprint.
        b = {"members": ["learn-a", "learn-c", "learn-b"], "size": 3,
             "cohesion": 0.1, "density": 0.8, "separation": 0.1}
        self.assertEqual(mod._skill_candidate_fingerprint(a),
                         mod._skill_candidate_fingerprint(b))
        # A different member set -> a different fingerprint.
        c = {"members": ["learn-a", "learn-c", "learn-d"]}
        self.assertNotEqual(mod._skill_candidate_fingerprint(a),
                            mod._skill_candidate_fingerprint(c))

    # --- kernel-token summaries STILL emit (only ids ride the context) ------

    def test_emit_skill_candidate_workorder(self):
        mod = load_cli()
        cluster = self._cluster(mod)
        feature, err = mod._emit_skill_candidate_workorder(self.kdir, cluster)
        self.assertIsNone(err)
        self.assertTrue(feature.startswith("auto/skill-candidate-"),
                        "emitted feature: %r" % feature)
        plan = self._workorder_plan(feature)
        self.assertTrue(os.path.isfile(plan))
        with open(plan, encoding="utf-8") as f:
            txt = f.read()
        # A non-auto-start DRAFT with the skill-promote provenance header.
        self.assertIn("> Status: draft", txt)
        self.assertNotIn("> Status: running", txt)
        self.assertNotIn("> Status: ready", txt)
        self.assertIn("> Origin: auto", txt)
        self.assertIn("> Generated-by: skill-promote", txt)
        self.assertIn("> Trigger: skill-candidate", txt)
        # The kernel-token SUMMARIES never rode the context, so the emitter's
        # `_pref_hits_kernel` refusal could not block this legitimate cluster.
        for token in ("scripts/agentware recall", "hand-edit .claude",
                      "flags drift in .claude"):
            self.assertNotIn(token, txt,
                             "member summary text leaked into the workorder")
        # Only member IDS + [learnings] ride the fenced Context block.
        context = txt.split("## Context", 1)[1].split("## Target packages", 1)[0]
        after_context = txt.split("## Target packages", 1)[1]
        for mid in cluster["members"]:
            self.assertIn(mid, context, "%s must be in Context" % mid)
            self.assertNotIn(mid, after_context,
                             "%s leaked past Context" % mid)
        self.assertIn("[learnings]", context)
        # The trusted static task seed directs authoring via `skill add`, and does
        # NOT instruct a raw `index add` promotion (Task 7 e2e invariant).
        tasks = txt.split("## Tasks", 1)[1]
        self.assertIn("skill add", tasks)
        self.assertNotIn("index add", tasks)
        self.assertIn("skill sync --approve", tasks)
        # exactly ONE workorder under work/auto/
        self.assertEqual(
            len(os.listdir(os.path.join(self.kdir, "work", "auto"))), 1)

    # --- idempotent (same member set -> stable slug -> no duplicate) --------

    def test_emit_is_idempotent(self):
        mod = load_cli()
        cluster = self._cluster(mod)
        f1, e1 = mod._emit_skill_candidate_workorder(self.kdir, cluster)
        f2, e2 = mod._emit_skill_candidate_workorder(self.kdir, cluster)
        self.assertIsNone(e1)
        self.assertIsNone(e2)
        self.assertEqual(f1, f2)
        self.assertEqual(
            len(os.listdir(os.path.join(self.kdir, "work", "auto"))), 1)

    def test_emit_read_only_wrt_index(self):
        mod = load_cli()
        idx = os.path.join(self.kdir, "index.json")
        with open(idx, "rb") as f:
            before = f.read()
        mod._emit_skill_candidate_workorder(self.kdir, self._cluster(mod))
        with open(idx, "rb") as f:
            self.assertEqual(f.read(), before,
                             "emit must not mutate index.json")

    # --- g3 cap DEFERS (drainable), distinct from a kernel BLOCK ------------

    def test_emit_defers_at_draft_cap(self):
        mod = load_cli()
        cluster = self._cluster(mod)
        prev = mod.WORKORDER_DRAFT_CAP
        mod.WORKORDER_DRAFT_CAP = 0        # force the cap
        try:
            feature, err = mod._emit_skill_candidate_workorder(self.kdir, cluster)
        finally:
            mod.WORKORDER_DRAFT_CAP = prev
        self.assertIsNone(feature)
        self.assertTrue(err.startswith("deferred:"), "reason: %r" % err)
        # nothing written when deferred
        auto = os.path.join(self.kdir, "work", "auto")
        self.assertFalse(os.path.isdir(auto) and os.listdir(auto))

    # --- kernel-path member id is BLOCKED (R-SEC-02 backstop) ---------------

    def test_emit_blocks_kernel_path_member(self):
        mod = load_cli()
        # A crafted cluster whose member id names a kernel token -> the id rides
        # the context -> `_pref_hits_kernel` refuses -> blocked (no write).
        bad = {"members": ["AGENTS.md", "learn-x", "learn-y"], "size": 3,
               "cohesion": 0.9, "density": 1.0, "separation": 0.3,
               "tag_spine_share": 1.0}
        feature, err = mod._emit_skill_candidate_workorder(self.kdir, bad)
        self.assertIsNone(feature)
        self.assertTrue(err.startswith("blocked:"), "reason: %r" % err)
        auto = os.path.join(self.kdir, "work", "auto")
        self.assertFalse(os.path.isdir(auto) and os.listdir(auto))


# A tight cohesive cluster whose dominant tag-spine is an EXISTING skill name
# (`.claude/skills/systematic-debugging`), so anti-bloat must propose it as an
# EXTEND note, not a duplicate sibling. Full 3-node triangle on the spine tag.
_COVERED_DEBUG = [
    _learn("learn-dbg-repro", ["systematic-debugging", "reproduce"],
           "Reproduce the failure deterministically first.",
           ["reproduce", "deterministic", "seed", "harness", "capture"]),
    _learn("learn-dbg-bisect", ["systematic-debugging", "bisect"],
           "Bisect the regression to isolate the culprit commit.",
           ["bisect", "regression", "isolate", "culprit", "window"]),
    _learn("learn-dbg-hypothesis", ["systematic-debugging", "hypothesis"],
           "Form one hypothesis and instrument before changing code.",
           ["hypothesis", "instrument", "evidence", "confirm", "lock"]),
]

# A second NEW (uncovered) tight cluster used to exercise the per-run cap: its
# tag-spine `cache-eviction` matches no existing skill, so it is a fresh proposal.
_CACHE = [
    _learn("learn-cache-lru", ["cache-eviction", "lru"],
           "Evict least-recently-used entries under memory pressure.",
           ["lru", "pressure", "victim", "recency", "slab"]),
    _learn("learn-cache-ttl", ["cache-eviction", "ttl"],
           "Expire entries past their time-to-live on read.",
           ["ttl", "expire", "clock", "lazy", "sweep"]),
    _learn("learn-cache-stampede", ["cache-eviction", "stampede"],
           "Guard against a thundering-herd cache stampede.",
           ["stampede", "herd", "lock", "coalesce", "singleflight"]),
]


class DedupAndCapTest(SyntheticKBTestCase):
    """Task 3: anti-bloat dedup (extend-note vs sibling) + per-run emission cap,
    driven through the shared `_skill_promote_run` seam."""

    def _bind_kdir(self):
        prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir

        def _restore():
            if prev is None:
                os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
            else:
                os.environ["AGENTWARE_KNOWLEDGE_DIR"] = prev
        self.addCleanup(_restore)

    def _auto_dirs(self):
        auto = os.path.join(self.kdir, "work", "auto")
        return sorted(os.listdir(auto)) if os.path.isdir(auto) else []

    # --- an EXISTING-skill-covered cluster becomes an EXTEND note ------------

    def test_dedup_and_cap_extends_covered_cluster(self):
        mod = load_cli()
        build_synthetic_kb(self.kdir, entries=_TIGHT + _COVERED_DEBUG)
        self._bind_kdir()
        report = mod._skill_promote_run(self.kdir, self.read_index(), cap=5)

        # The payment/refund cluster is NEW; the debugging cluster is covered by
        # the `.claude/skills/systematic-debugging` name -> extended, not a sibling.
        self.assertEqual(len(report["proposed"]), 1, report)
        self.assertEqual(len(report["extended"]), 1, report)
        self.assertEqual(report["extended"][0]["skill"], "systematic-debugging")
        # Two workorders total (one author, one extend) — no duplicate sibling.
        self.assertEqual(len(self._auto_dirs()), 2)

        # The extend workorder directs EXTENDING the named skill via `skill add`,
        # never a raw `index add`, and is a non-auto-start draft.
        ext_feature = report["extended"][0]["feature"]
        plan = os.path.join(
            self.kdir, ext_feature.replace("auto/", "work/auto/", 1), "plan.md")
        with open(plan, encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("> Status: draft", txt)
        self.assertIn("systematic-debugging", txt)
        self.assertIn("EXTEND", txt)
        tasks = txt.split("## Tasks", 1)[1]
        self.assertIn("skill add", tasks)
        self.assertNotIn("index add", tasks)

    # --- the per-run cap bounds NEW emissions -------------------------------

    def test_dedup_and_cap_limits_emissions(self):
        mod = load_cli()
        build_synthetic_kb(self.kdir, entries=_TIGHT + _CACHE)
        self._bind_kdir()
        report = mod._skill_promote_run(self.kdir, self.read_index(), cap=1)
        # Two qualifying NEW clusters, cap 1 -> exactly one emitted, one skipped.
        self.assertEqual(report["candidates"], 2, report)
        self.assertEqual(report["emitted"], 1, report)
        self.assertEqual(report["skipped_cap"], 1, report)
        self.assertEqual(len(self._auto_dirs()), 1)

    # --- cap=0 is detect/report-only (no emission) --------------------------

    def test_dedup_and_cap_zero_is_report_only(self):
        mod = load_cli()
        build_synthetic_kb(self.kdir, entries=_TIGHT + _CACHE)
        self._bind_kdir()
        report = mod._skill_promote_run(self.kdir, self.read_index(), cap=0)
        self.assertEqual(report["emitted"], 0)
        self.assertEqual(report["skipped_cap"], 2)
        self.assertEqual(self._auto_dirs(), [])

    # --- dry-run ranks candidates WITHOUT emitting --------------------------

    def test_dedup_and_cap_dry_run_no_emit(self):
        mod = load_cli()
        build_synthetic_kb(self.kdir, entries=_TIGHT + _COVERED_DEBUG)
        self._bind_kdir()
        report = mod._skill_promote_run(self.kdir, self.read_index(),
                                        dry_run=True, cap=5)
        self.assertEqual(len(report["ranked"]), 2)
        self.assertEqual(report["emitted"], 0)
        self.assertEqual(self._auto_dirs(), [])
        # The covered cluster is annotated with its extend target in the ranking.
        extends = {r["extend"] for r in report["ranked"]}
        self.assertIn("systematic-debugging", extends)
        self.assertIn(None, extends)

    # --- idempotent: a second run re-proposes nothing (no dup, no budget) ---

    def test_dedup_and_cap_idempotent(self):
        mod = load_cli()
        build_synthetic_kb(self.kdir, entries=_TIGHT + _CACHE)
        self._bind_kdir()
        r1 = mod._skill_promote_run(self.kdir, self.read_index(), cap=5)
        self.assertEqual(r1["emitted"], 2)
        r2 = mod._skill_promote_run(self.kdir, self.read_index(), cap=5)
        self.assertEqual(r2["emitted"], 0, "re-run must not duplicate")
        self.assertEqual(len(r2["existing"]), 2)
        self.assertEqual(len(self._auto_dirs()), 2)

    # --- unit: coverage index + tag-spine matching --------------------------

    def test_dedup_and_cap_match_helpers(self):
        mod = load_cli()
        data = build_synthetic_kb(self.kdir, entries=_TIGHT)
        coverage = mod._existing_skill_coverage(self.kdir, data)
        names = {n for n, _t in coverage}
        self.assertIn("systematic-debugging", names)   # from .claude/skills/
        # A covered spine matches; an unknown spine does not.
        self.assertEqual(
            mod._match_existing_skill(
                {"tag_spine": "systematic-debugging"}, coverage),
            "systematic-debugging")
        self.assertIsNone(
            mod._match_existing_skill({"tag_spine": "payment"}, coverage))
        # A kernel-token spine is never surfaced (defensive R-SEC-02).
        self.assertFalse(mod._safe_skill_name("AGENTS.md"))
        self.assertTrue(mod._safe_skill_name("systematic-debugging"))


class ConfigSkillPromoteTest(unittest.TestCase):
    """Task 3: SETTINGS_AW round-trip for the skill-promotion keys (mode, cap,
    thresholds) with HOME_CONFIG patched onto a temp file (never the operator's
    real ~/.agentware/config.env)."""

    def setUp(self):
        import io as _io
        import contextlib as _ctx
        import shutil as _sh
        import tempfile as _tf
        self._io, self._ctx = _io, _ctx
        self.cli = load_cli()
        self.home = _tf.mkdtemp(prefix="agentware-skp-home-")
        self.addCleanup(_sh.rmtree, self.home, True)
        self.cfg = os.path.join(self.home, ".agentware", "config.env")
        self._orig_hc = self.cli.HOME_CONFIG
        self._orig_cp = self.cli.CONFIG_PATHS
        self.cli.HOME_CONFIG = self.cfg
        self.cli.CONFIG_PATHS = (self.cfg,)

        def _restore():
            self.cli.HOME_CONFIG = self._orig_hc
            self.cli.CONFIG_PATHS = self._orig_cp
        self.addCleanup(_restore)
        # Neutralize inherited env for every key touched; restore afterward.
        self._prev_env = {}
        for k in (self.cli.SKILL_PROMOTE_KEY, self.cli.SKILL_PROMOTE_CAP_KEY,
                  self.cli.SKILL_PROMOTE_EDGE_MIN_KEY):
            self._prev_env[k] = os.environ.pop(k, None)

        def _restore_env():
            for k, v in self._prev_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(_restore_env)

    def _run(self, argv):
        out, err = self._io.StringIO(), self._io.StringIO()
        with self._ctx.redirect_stdout(out), self._ctx.redirect_stderr(err):
            code = self.cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_default_mode_is_propose(self):
        self.assertEqual(self.cli.resolve_skill_promote(), "propose")

    def test_set_mode_round_trips(self):
        code, _o, _e = self._run(["config", "--set-skill-promote", "off"])
        self.assertEqual(code, 0)
        self.assertEqual(self.cli.resolve_skill_promote(), "off")
        code, out, _e = self._run(["config", "--skill-promote-only"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "off")

    def test_invalid_mode_rejected_no_corruption(self):
        code, _o, err = self._run(["config", "--set-skill-promote", "bogus"])
        self.assertEqual(code, 2)
        self.assertIn("invalid", err)
        self.assertEqual(self.cli.resolve_skill_promote(), "propose")

    def test_set_cap_round_trips(self):
        code, _o, _e = self._run(["config", "--set-skill-promote-cap", "4"])
        self.assertEqual(code, 0)
        self.assertEqual(self.cli.resolve_skill_promote_cap(), 4)

    def test_invalid_cap_rejected(self):
        code, _o, err = self._run(["config", "--set-skill-promote-cap", "-1"])
        self.assertEqual(code, 2)
        self.assertIn("invalid", err)

    def test_set_threshold_round_trips_and_clamps(self):
        code, _o, _e = self._run(
            ["config", "--set-skill-promote-edge-min", "0.42"])
        self.assertEqual(code, 0)
        self.assertAlmostEqual(
            self.cli.resolve_skill_promote_thresholds()["edge_min"], 0.42)
        # Out-of-range is rejected (no corruption; prior value stands).
        code, _o, err = self._run(
            ["config", "--set-skill-promote-edge-min", "1.5"])
        self.assertEqual(code, 2)
        self.assertAlmostEqual(
            self.cli.resolve_skill_promote_thresholds()["edge_min"], 0.42)

    def test_nan_inf_threshold_rejected(self):
        # adversarial-review: `float("nan")` parses but every NaN comparison is
        # False, so a bare `f < 0.0 or f > 1.0` guard would accept NaN as a live
        # threshold (and inf must be rejected too). Both are refused (code 2, no
        # corruption — the prior valid value stands).
        code, _o, _e = self._run(
            ["config", "--set-skill-promote-edge-min", "0.42"])
        self.assertEqual(code, 0)
        # (`-inf` is caught earlier by argparse's leading-dash handling; `nan`/
        # `inf` are the values that PARSE as floats and would slip a naive guard.)
        for bad in ("nan", "NaN", "inf"):
            code, _o, _e = self._run(
                ["config", "--set-skill-promote-edge-min", bad])
            self.assertEqual(code, 2, "%r must be rejected" % bad)
            self.assertAlmostEqual(
                self.cli.resolve_skill_promote_thresholds()["edge_min"], 0.42,
                msg="%r must not corrupt the stored threshold" % bad)

    def test_config_json_surfaces_settings(self):
        import json as _json
        code, out, _e = self._run(["config", "--skill-promote-only"])
        # Also confirm the JSON config surfacing includes the keys.
        code, out, _e = self._run(["config", "--format", "json"])
        obj = _json.loads(out)
        self.assertIn("skill_promote", obj)
        self.assertIn("skill_promote_cap", obj)
        self.assertIn("skill_promote_thresholds", obj)


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


# --- Task 7: end-to-end pipeline on ONE hermetic fixture KB -------------------
# A single narrative over a MIXED corpus that exercises every arm of the shipped
# pipeline through the REAL CLI (`skill propose`) + a live dream step `k`:
#   * _TIGHT (payment/refund)  -> ONE new skill-candidate DRAFT (author a skill)
#   * _COVERED_DEBUG           -> an EXTEND note for `.claude/skills/systematic-debugging`
#   * _CHAIN (A~B~C, A!~C)     -> NOTHING (the density gate rejects the single-linkage chain)
# plus: idempotent re-run (propose AND dream k), the emitted seed authors via
# `skill add` (never `index add`), and the g3 draft cap defers cleanly.
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


if __name__ == "__main__":
    import unittest
    unittest.main()
