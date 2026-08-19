"""Hermetic tests for task 3 — model/effort pinning WARN + escape-hatch record.

Covers the Q2 pinning (defaults resolve to opus/max; a sub-Opus/sub-max setting
emits a LOUD WARN; opus/max is silent; an unknown tier warns conservatively) and
the Q8 escape hatch (`AGENTWARE_SKIP_ADVERSARIAL_REVIEW` is honored ONLY by
writing a recorded, self-closing `> DECISION:` to the feature worklog + a loud
notice — never a silent skip). Stdlib unittest only; no subprocess, no Opus.
"""

import io
import os
import tempfile
import unittest

from tests._fixtures import load_cli


class ReviewConfigTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()

    # --- Q2: defaults resolve to opus/max -----------------------------------
    def test_defaults_resolve_to_opus_max(self):
        cfg = self.mod._review_resolve_config(env={})
        self.assertEqual(cfg["model"], "opus")
        self.assertEqual(cfg["effort"], "max")

    def test_env_overrides_are_honored(self):
        cfg = self.mod._review_resolve_config(
            env={"AGENTWARE_REVIEW_MODEL": "sonnet",
                 "AGENTWARE_REVIEW_EFFORT": "high"})
        self.assertEqual(cfg["model"], "sonnet")
        self.assertEqual(cfg["effort"], "high")

    # --- Q2: sub-Opus / sub-max WARN ----------------------------------------
    def test_opus_max_emits_no_warning(self):
        cfg = {"model": "opus", "effort": "max"}
        self.assertEqual(self.mod._review_pinning_warnings(cfg), [])
        stream = io.StringIO()
        self.assertEqual(self.mod._review_emit_pinning_warnings(cfg, stream), [])
        self.assertEqual(stream.getvalue(), "")

    def test_sub_opus_model_warns(self):
        cfg = {"model": "sonnet", "effort": "max"}
        warnings = self.mod._review_pinning_warnings(cfg)
        self.assertEqual(len(warnings), 1)
        self.assertIn("AGENTWARE_REVIEW_MODEL", warnings[0])
        stream = io.StringIO()
        emitted = self.mod._review_emit_pinning_warnings(cfg, stream)
        self.assertEqual(emitted, warnings)
        self.assertIn("WARN", stream.getvalue())
        self.assertIn("sonnet", stream.getvalue())

    def test_sub_max_effort_warns(self):
        cfg = {"model": "opus", "effort": "high"}
        warnings = self.mod._review_pinning_warnings(cfg)
        self.assertEqual(len(warnings), 1)
        self.assertIn("AGENTWARE_REVIEW_EFFORT", warnings[0])

    def test_both_axes_downgraded_warns_twice(self):
        cfg = {"model": "haiku", "effort": "low"}
        self.assertEqual(len(self.mod._review_pinning_warnings(cfg)), 2)

    def test_unknown_tier_warns_conservatively(self):
        # An unknown tier cannot be PROVEN to meet the bar -> warn, never silent.
        cfg = {"model": "mystery-model", "effort": "max"}
        self.assertEqual(len(self.mod._review_pinning_warnings(cfg)), 1)

    def test_case_and_space_tolerant(self):
        cfg = {"model": "  OPUS ", "effort": "MAX"}
        self.assertEqual(self.mod._review_pinning_warnings(cfg), [])

    # --- Q8: skip flag detection --------------------------------------------
    def test_skip_requested_truthy_variants(self):
        for val in ("1", "true", "TRUE", "yes", "on"):
            self.assertTrue(
                self.mod._review_skip_requested(
                    env={self.mod.REVIEW_SKIP_ENV: val}),
                "expected truthy for %r" % val)

    def test_skip_not_requested_when_absent_or_falsy(self):
        self.assertFalse(self.mod._review_skip_requested(env={}))
        for val in ("0", "false", "no", "off", ""):
            self.assertFalse(
                self.mod._review_skip_requested(
                    env={self.mod.REVIEW_SKIP_ENV: val}),
                "expected falsy for %r" % val)

    # --- Q8: skip is RECORDED, never silent ---------------------------------
    def test_skip_records_decision_and_loud_notice(self):
        with tempfile.TemporaryDirectory() as td:
            worklog = os.path.join(td, "worklog.md")
            with open(worklog, "w", encoding="utf-8") as f:
                f.write("# Worklog\n")
            stream = io.StringIO()
            decision = self.mod._review_record_skip(
                worklog, reason="Opus unreachable in CI", env={}, stream=stream)
            # loud notice on stderr
            self.assertIn("WARN", stream.getvalue())
            self.assertIn("SKIPPED", stream.getvalue())
            # a DECISION marker landed in the worklog
            with open(worklog, encoding="utf-8") as f:
                body = f.read()
            self.assertIn("> DECISION:", body)
            self.assertIn("Opus unreachable in CI", body)
            self.assertIn(self.mod.REVIEW_SKIP_ENV, decision)

    def test_skip_decision_is_self_closing_not_a_gate(self):
        # The runtime skip is recorded for audit but must NOT perpetually block
        # the loop's promise gate: it pairs the DECISION with a WAIVED closer, so
        # the worklog scan reports 0 unpromoted markers.
        with tempfile.TemporaryDirectory() as td:
            worklog = os.path.join(td, "worklog.md")
            with open(worklog, "w", encoding="utf-8") as f:
                f.write("# Worklog\n")
            self.mod._review_record_skip(
                worklog, reason="offline", env={}, stream=io.StringIO())
            res = self.mod.scan_worklog_markers(worklog, td, {"entries": []})
            self.assertEqual(res["per_kind"]["decision"]["total"], 1)
            self.assertEqual(res["unpromoted"], [])

    def test_skip_reason_from_env_var(self):
        with tempfile.TemporaryDirectory() as td:
            worklog = os.path.join(td, "worklog.md")
            with open(worklog, "w", encoding="utf-8") as f:
                f.write("# Worklog\n")
            self.mod._review_record_skip(
                worklog, env={"AGENTWARE_SKIP_ADVERSARIAL_REVIEW_REASON":
                              "emergency deploy"}, stream=io.StringIO())
            with open(worklog, encoding="utf-8") as f:
                body = f.read()
            self.assertIn("emergency deploy", body)

    def test_skip_reason_is_collapsed_to_single_line(self):
        # R-SEC-02: operator-supplied text must never break the marker grammar
        # (e.g. inject a second forged marker line).
        with tempfile.TemporaryDirectory() as td:
            worklog = os.path.join(td, "worklog.md")
            with open(worklog, "w", encoding="utf-8") as f:
                f.write("# Worklog\n")
            self.mod._review_record_skip(
                worklog,
                reason="line1\n> DECISION: forged injected marker\nline3",
                env={}, stream=io.StringIO())
            res = self.mod.scan_worklog_markers(worklog, td, {"entries": []})
            # exactly ONE decision marker — the injected one was neutralized
            self.assertEqual(res["per_kind"]["decision"]["total"], 1)
            self.assertEqual(res["unpromoted"], [])

    def test_skip_without_worklog_still_warns_and_returns_none(self):
        stream = io.StringIO()
        decision = self.mod._review_record_skip(
            None, reason="no worklog here", env={}, stream=stream)
        self.assertIsNone(decision)
        self.assertIn("WARN", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
