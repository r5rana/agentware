"""Tests for the dream `skill-propose` step `k` (feature
260722-auto-skill-promotion, Task 4).

Stdlib `unittest` only, synthetic temp KB (no `init`). Asserts that a new,
NON-change-gated dream step detects cohesive recurring learning clusters and
surfaces each as a tier-2 PROPOSED skill-candidate workorder via the SHARED
`_skill_promote_run` emit seam (detection + proposal ONLY — never authors a
stub, never `index add`, never installs):

  - Registered in `DREAM_STEP_FUNCS` as the free char `k`, ordered AFTER `s`
    (stale-propose) and BEFORE `f` (kb-git-commit) so f still backs up the emit;
    `_dream_parse_steps` validates `k`; `_DREAM_METRIC_STEP_FIELDS` carries its
    counts.
  - Below the g3 draft cap, one `--steps k` cycle EMITS exactly one tier-2
    `work/auto/skill-candidate-<fp>/plan.md` (Status: draft, member ids in the
    fenced `## Context`); re-running is a stable no-op (same slug, one workorder).
    index.json is never mutated.
  - AT the g3 cap (5 draft workorders), the step reports `deferred=true` and
    writes NOTHING; the cycle still exits cleanly.
  - `AGENTWARE_SKILL_PROMOTE=off` SKIPS the step (mode gate).
  - `--dry-run` mutates nothing and reports the planned step.
"""

import json
import os

try:
    from tests._fixtures import SyntheticKBTestCase, build_synthetic_kb, load_cli
except ImportError:  # allow `python3 -m unittest tests.unit.features.test_dream_skill_propose`
    from _fixtures import SyntheticKBTestCase, build_synthetic_kb, load_cli

CLI = load_cli()


def _learn(eid, tags, summary, body_terms):
    slug = eid.replace("learn-", "")
    return {
        "id": eid, "title": eid, "category": "learnings",
        "path": "learnings/%s.md" % slug, "tags": list(tags),
        "created": "2026-05-01", "last_verified": "2026-05-01",
        "author": "testuser", "summary": summary,
        "body": "# %s\n\n%s\n" % (eid, " ".join(body_terms)),
    }


# A TIGHT cohesive cluster (full triangle, dominant tag-spine) — qualifies as a
# skill candidate under the default thresholds.
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

# A single-linkage CHAIN A~B~C where A!~C -> density 0.67 < 0.70 => REJECTED.
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

_CORPUS = _TIGHT + _CHAIN


class _DreamStepBase(SyntheticKBTestCase):
    def setUp(self):
        super().setUp()
        prev = os.environ.get("AGENTWARE_NESTED_UNITTEST")
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"
        self.addCleanup(
            lambda: os.environ.__setitem__("AGENTWARE_NESTED_UNITTEST", prev)
            if prev is not None
            else os.environ.pop("AGENTWARE_NESTED_UNITTEST", None))
        # Pin the mode ON regardless of any ambient config.env, and restore it.
        prev_mode = os.environ.get("AGENTWARE_SKILL_PROMOTE")
        os.environ["AGENTWARE_SKILL_PROMOTE"] = "propose"
        self.addCleanup(
            lambda: os.environ.__setitem__("AGENTWARE_SKILL_PROMOTE", prev_mode)
            if prev_mode is not None
            else os.environ.pop("AGENTWARE_SKILL_PROMOTE", None))

    def _cycle(self, *extra):
        return self.run_cli(
            ["dream", "--steps", "k", "--force", "--format", "json", *extra])

    def _k_step(self, out):
        steps = json.loads(out)["steps"]
        return next(s for s in steps if s["step"] == "k")

    def _auto_dirs(self):
        base = os.path.join(self.kdir, "work", "auto")
        try:
            return sorted(os.listdir(base))
        except OSError:
            return []

    def _index_bytes(self):
        with open(os.path.join(self.kdir, "index.json"), "rb") as f:
            return f.read()


class StepRegistrationTest(_DreamStepBase):
    def setUp(self):
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=_CORPUS)

    def test_step_k_registered_after_s_before_f(self):
        steps = [s for s, _, _ in CLI.DREAM_STEP_FUNCS]
        self.assertIn("k", steps)
        self.assertGreater(steps.index("k"), steps.index("s"),
                           "step k must be ordered after s (stale-propose)")
        self.assertLess(steps.index("k"), steps.index("f"),
                        "step k must precede f (so f backs up the emit)")

    def test_parse_steps_validates_k(self):
        parsed, err = CLI._dream_parse_steps("k")
        self.assertIsNone(err, "k must be a valid dream step: %r" % err)
        self.assertEqual(parsed, ["k"])

    def test_parse_steps_rejects_unknown(self):
        parsed, err = CLI._dream_parse_steps("zz")
        self.assertIsNone(parsed)
        self.assertIn("zz", err)


class SkillProposeEmitTest(_DreamStepBase):
    def setUp(self):
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=_CORPUS)

    def test_cycle_emits_one_tier2_draft_workorder(self):
        code, out, err = self._cycle()
        self.assertEqual(code, 0, err)
        step = self._k_step(out)
        self.assertEqual(step["status"], "ok")
        self.assertFalse(step["deferred"])
        self.assertFalse(step["blocked"])
        self.assertEqual(step["proposed"], 1, "one tight cluster -> one emit")
        self.assertEqual(step["extended"], 0)
        # exactly ONE workorder under work/auto/, a non-auto-start DRAFT
        dirs = [d for d in self._auto_dirs() if d.startswith("skill-candidate-")]
        self.assertEqual(len(dirs), 1)
        plan = os.path.join(self.kdir, "work", "auto", dirs[0], "plan.md")
        with open(plan, encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("> Status: draft", txt)
        self.assertNotIn("> Status: running", txt)
        # the tight-cluster member ids ride ONLY the fenced Context block (R-SEC-02)
        context = txt.split("## Context", 1)[1].split("## Target packages", 1)[0]
        for mid in (e["id"] for e in _TIGHT):
            self.assertIn(mid, context)
        # detection + proposal ONLY: the workorder authors via `skill add`, not
        # `index add`, and never auto-installs.
        self.assertIn("skill add", txt)
        self.assertNotIn("index add", txt)

    def test_cycle_does_not_mutate_index(self):
        before = self._index_bytes()
        self.assertEqual(self._cycle()[0], 0)
        self.assertEqual(self._index_bytes(), before,
                         "the skill-propose step must not mutate index.json")

    def test_rerun_is_stable_noop(self):
        self.assertEqual(self._cycle()[0], 0)
        dirs1 = [d for d in self._auto_dirs() if d.startswith("skill-candidate-")]
        code2, out2, _e2 = self._cycle()
        self.assertEqual(code2, 0)
        step2 = self._k_step(out2)
        # the second run finds the same slug already present -> nothing new emitted
        self.assertEqual(step2["proposed"], 0, "re-run must not re-emit")
        dirs2 = [d for d in self._auto_dirs() if d.startswith("skill-candidate-")]
        self.assertEqual(dirs1, dirs2, "same cluster -> same slug, no duplicate")

    def test_dry_run_mutates_nothing(self):
        code, out, err = self._cycle("--dry-run")
        self.assertEqual(code, 0, err)
        step = self._k_step(out)
        self.assertEqual(step["status"], "planned")
        self.assertIn("skill propose", step["would"])
        self.assertEqual(
            [d for d in self._auto_dirs() if d.startswith("skill-candidate-")], [],
            "--dry-run must not emit a workorder")


class SkillProposeModeGateTest(_DreamStepBase):
    def setUp(self):
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=_CORPUS)
        os.environ["AGENTWARE_SKILL_PROMOTE"] = "off"   # override the base pin

    def test_off_mode_skips_the_step(self):
        code, out, err = self._cycle()
        self.assertEqual(code, 0, err)
        step = self._k_step(out)
        self.assertEqual(step["status"], "skipped")
        self.assertIn("off", step.get("reason", ""))
        self.assertEqual(
            [d for d in self._auto_dirs() if d.startswith("skill-candidate-")], [],
            "off mode must emit nothing")


class SkillProposeDeferAtCapTest(_DreamStepBase):
    def setUp(self):
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=_CORPUS)
        # Pre-load the g3 draft cap with 5 open (draft) workorders so the shipped
        # emitter REFUSES and the step must DEFER gracefully.
        for i in range(5):
            d = os.path.join(self.kdir, "work", "auto", "wo-filler-%d" % i)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as f:
                f.write("# Plan — filler %d\n\n> Status: draft\n\n"
                        "## Tasks\n- ⬜ **1** x\n" % i)
        self.assertEqual(CLI._count_auto_open_workorders(self.kdir), 5)

    def test_step_defers_and_writes_nothing(self):
        before_dirs = self._auto_dirs()
        before_idx = self._index_bytes()
        code, out, err = self._cycle()
        self.assertEqual(code, 0, err)           # a defer is NOT a cycle failure
        step = self._k_step(out)
        self.assertEqual(step["status"], "ok")
        self.assertTrue(step["deferred"], "at-cap must DEFER, got %r" % step)
        self.assertEqual(step["proposed"], 0)
        self.assertEqual(self._auto_dirs(), before_dirs,
                         "at-cap must NOT emit a skill-candidate workorder")
        self.assertEqual(self._index_bytes(), before_idx)


class SkillProposeMetricTest(_DreamStepBase):
    def setUp(self):
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=_CORPUS)

    def test_metric_event_carries_skill_propose_counts(self):
        m = CLI._dream_metric_step({
            "step": "k", "status": "ok", "duration_s": 0.1,
            "proposed": 1, "extended": 0, "deferred": False,
            "blocked": False, "clean": False})
        self.assertEqual(m.get("proposed"), 1)
        self.assertEqual(m.get("extended"), 0)
        self.assertFalse(m.get("deferred"))
        self.assertFalse(m.get("blocked"))
        self.assertFalse(m.get("clean"))


if __name__ == "__main__":
    import unittest
    unittest.main()
