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

from tests.unit.features._skill_promote_support import (  # noqa: F401
    SyntheticKBTestCase, _CACHE, _COVERED_DEBUG, _KERNEL_SUMMARY, _TIGHT,
    build_synthetic_kb, load_cli, os, unittest)


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
