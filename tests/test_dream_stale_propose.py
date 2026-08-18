"""Tests for the dream `stale-propose` step (feature
260717-p3-followup-declare-habitual, Task 2).

Stdlib `unittest` only, synthetic temp KB. Asserts that a new, NON-change-gated
dream step runs the SHIPPED `audit --stale --propose` emitter on the routine
cadence:

  - Registered in `DREAM_STEP_FUNCS` between `e` (detect-report) and `m`
    (transcript-mine).
  - Below the g3 draft cap, one `--steps s` cycle EMITS exactly one tier-2
    `work/auto/stale-review-<fp>/plan.md` (Status: draft, finding fenced in
    `## Context`); re-running the same cycle is a stable no-op (same slug, still
    one workorder). index.json is never mutated.
  - AT the g3 cap (5 draft workorders), the step reports `deferred=true` and
    writes NOTHING (no new workorder); the cycle still exits cleanly.
  - `--dry-run` mutates nothing and reports the planned step.
"""

import json
import os

try:
    from tests._fixtures import SyntheticKBTestCase, build_synthetic_kb, load_cli
except ImportError:  # allow `python3 -m unittest tests.test_dream_stale_propose`
    from _fixtures import SyntheticKBTestCase, build_synthetic_kb, load_cli

CLI = load_cli()
OLD = "2020-01-01"      # far past the 120d volatile window
FRESH = "2026-06-25"


def _e(eid, name, category, last_verified, **extra):
    e = {
        "id": eid, "title": name, "category": category,
        "path": "%s/%s.md" % (category, name), "tags": ["alpha", "cache"],
        "created": last_verified, "last_verified": last_verified,
        "summary": "alpha beta gamma cache note",
        "body": ("# %s\n\nThe alpha beta gamma cache invalidation race note "
                 "describes the alpha beta gamma ordering behavior.\n" % name),
    }
    e.update(extra)
    return e


# A stale volatile entry + a declared supersede pair -> a NON-empty report.
_DIRTY = [
    _e("learn-stale-note", "stale", "learnings", OLD),
    _e("learn-old-note", "old", "learnings", FRESH),
    _e("learn-new-note", "new", "learnings", FRESH,
       relates=["supersedes:learn-old-note"]),
]


class _DreamStepBase(SyntheticKBTestCase):
    def setUp(self):
        super().setUp()
        # Keep the nested audit from recursively shelling the suite.
        prev = os.environ.get("AGENTWARE_NESTED_UNITTEST")
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"
        self.addCleanup(
            lambda: os.environ.__setitem__("AGENTWARE_NESTED_UNITTEST", prev)
            if prev is not None
            else os.environ.pop("AGENTWARE_NESTED_UNITTEST", None))

    def _cycle(self, *extra):
        return self.run_cli(
            ["dream", "--steps", "s", "--force", "--format", "json", *extra])

    def _s_step(self, out):
        steps = json.loads(out)["steps"]
        return next(s for s in steps if s["step"] == "s")

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
        self.index_data = build_synthetic_kb(self.kdir, entries=_DIRTY)

    def test_step_registered_between_e_and_m(self):
        steps = [s for s, _, _ in CLI.DREAM_STEP_FUNCS]
        self.assertIn("s", steps)
        self.assertEqual(steps.index("s"), steps.index("e") + 1,
                         "step s must be ordered immediately after e")
        self.assertLess(steps.index("s"), steps.index("m"),
                        "step s must precede m (so f still backs up the emit)")


class StaleProposeEmitTest(_DreamStepBase):
    def setUp(self):
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=_DIRTY)

    def test_cycle_emits_one_tier2_draft_workorder(self):
        code, out, err = self._cycle()
        self.assertEqual(code, 0, err)
        step = self._s_step(out)
        self.assertEqual(step["status"], "ok")
        self.assertFalse(step["deferred"])
        self.assertTrue(step["proposed"].startswith("auto/stale-review-"),
                        "proposed=%r" % step["proposed"])
        # exactly ONE workorder under work/auto/, a non-auto-start DRAFT
        dirs = self._auto_dirs()
        self.assertEqual(len(dirs), 1)
        plan = os.path.join(self.kdir, "work", "auto", dirs[0], "plan.md")
        with open(plan, encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("> Status: draft", txt)
        self.assertNotIn("> Status: running", txt)
        # the finding rides ONLY the fenced Context block (R-SEC-02)
        context = txt.split("## Context", 1)[1].split("## Target packages", 1)[0]
        after = txt.split("## Target packages", 1)[1]
        for fid in ("learn-stale-note", "learn-old-note"):
            self.assertIn(fid, context)
            self.assertNotIn(fid, after)

    def test_cycle_does_not_mutate_index(self):
        before = self._index_bytes()
        self.assertEqual(self._cycle()[0], 0)
        self.assertEqual(self._index_bytes(), before,
                         "the stale-propose step must not mutate index.json")

    def test_rerun_is_stable_noop(self):
        _c1, out1, _e1 = self._cycle()
        feat1 = self._s_step(out1)["proposed"]
        _c2, out2, _e2 = self._cycle()
        feat2 = self._s_step(out2)["proposed"]
        self.assertEqual(feat1, feat2, "same finding set -> same slug")
        self.assertEqual(len(self._auto_dirs()), 1,
                         "re-run must not mint a duplicate workorder")

    def test_dry_run_mutates_nothing(self):
        code, out, err = self._cycle("--dry-run")
        self.assertEqual(code, 0, err)
        step = self._s_step(out)
        self.assertEqual(step["status"], "planned")
        self.assertIn("audit --stale --propose", step["would"])
        self.assertEqual(self._auto_dirs(), [],
                         "--dry-run must not emit a workorder")


class StaleProposeDeferAtCapTest(_DreamStepBase):
    def setUp(self):
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=_DIRTY)
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
        step = self._s_step(out)
        self.assertEqual(step["status"], "ok")
        self.assertTrue(step["deferred"], "at-cap must DEFER, got %r" % step)
        self.assertEqual(step["proposed"], "-")
        # nothing new written: same 5 filler dirs, index.json byte-identical
        self.assertEqual(self._auto_dirs(), before_dirs,
                         "at-cap must NOT emit a 6th workorder")
        self.assertEqual(self._index_bytes(), before_idx)


class StaleProposeBlockedTest(_DreamStepBase):
    """A finding whose id is a kernel token trips the emitter's PERMANENT g1
    kernel-path guard (draining work/auto/ can never clear it). The step must
    report BLOCKED — distinct from the transient at-cap DEFER — so a permanently-
    suppressed report is not mistaken for a drainable backlog."""

    def setUp(self):
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=[
            _e("AGENTS.md", "kernelname", "learnings", OLD),   # id is a kernel token
            _e("learn-benign", "benign", "learnings", FRESH)])

    def test_kernel_finding_reports_blocked_not_deferred(self):
        # below the g3 cap, so it is NOT a defer.
        self.assertLess(CLI._count_auto_open_workorders(self.kdir), 5)
        code, out, err = self._cycle()
        self.assertEqual(code, 0, err)
        step = self._s_step(out)
        self.assertEqual(step["status"], "ok")
        self.assertTrue(step["blocked"],
                        "a permanent kernel refusal must report blocked, got %r"
                        % step)
        self.assertFalse(step["deferred"], "a permanent block is NOT a defer")
        self.assertEqual(step["proposed"], "-")
        self.assertEqual(self._auto_dirs(), [], "nothing emitted")

    def test_metric_event_carries_block_signal(self):
        # re-review #8: the DURABLE metric event must distinguish a permanent block
        # from a clean no-op (not only the human-readable journal).
        m = CLI._dream_metric_step({
            "step": "s", "status": "ok", "duration_s": 0.1,
            "proposed": "-", "deferred": False, "blocked": True,
            "reason": "blocked: emitter refused (rc=1)"})
        self.assertTrue(m.get("blocked"))
        self.assertFalse(m.get("deferred"))
        self.assertEqual(m.get("proposed"), "-")

    def test_a_blocked_step_is_not_byte_identical_to_a_clean_no_op(self):
        """The PROPERTY the field list exists for, asserted as a property.

        `_DREAM_METRIC_STEP_FIELDS` is a tuple, and a test that restates its
        contents passes for any tuple someone writes down -- including the
        six-field-shorter one the lib port shipped, had the expectation been
        edited to match it. What the fields are FOR is that the durable record
        in logs/metrics.jsonl can tell two outcomes apart the morning after.

        So this compares two steps that differ ONLY in outcome. Drop the
        outcome fields and both render to the same bytes: a permanent block
        becomes indistinguishable from a clean no-op, which is precisely the
        failure the monolith's own comment on these fields names.
        """
        base = {"step": "s", "status": "ok", "duration_s": 0.1}
        blocked = CLI._dream_metric_step(dict(
            base, proposed="-", deferred=False, blocked=True,
            reason="blocked: emitter refused (rc=1)"))
        clean = CLI._dream_metric_step(dict(
            base, proposed=0, deferred=False, blocked=False, clean=True))
        self.assertNotEqual(
            json.dumps(blocked, sort_keys=True),
            json.dumps(clean, sort_keys=True),
            "a blocked step and a clean no-op serialise identically -- the "
            "durable metric cannot tell them apart")
        # ...and the discriminating fields are the reason it differs, not the
        # base fields, which are equal by construction here.
        for field in ("blocked", "reason", "clean"):
            self.assertTrue(field in blocked or field in clean,
                            "%s never reaches the metric record" % field)

    def test_the_skill_propose_step_keeps_its_three_outcomes_apart(self):
        """Step k's half: an emit, an extend-note and a clean no-op differ."""
        base = {"step": "k", "status": "ok", "duration_s": 0.1}
        emit = CLI._dream_metric_step(dict(base, proposed=1, extended=0,
                                           clean=False))
        extend = CLI._dream_metric_step(dict(base, proposed=0, extended=1,
                                             clean=False))
        noop = CLI._dream_metric_step(dict(base, proposed=0, extended=0,
                                           clean=True))
        rendered = [json.dumps(x, sort_keys=True) for x in (emit, extend, noop)]
        self.assertEqual(len(set(rendered)), 3,
                         "step k's three outcomes must not collapse: %s"
                         % rendered)


if __name__ == "__main__":
    import unittest
    unittest.main()
