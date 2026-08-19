"""Tests for the ACR win gate (Phase 3.2): the deterministic 5-part decision rule.

Three layers, stdlib `unittest` only (no scipy, no new deps):
  1. The pure stdlib helpers — `paired_t_test` + `t_critical` — against
     hand-computed t-statistics and critical-value lookups.
  2. The pure `acr_gate_decision(ablation, ...)` rule over hand-built synthetic
     ablation results: (a) ACR clearly wins -> PASS; (b) ACR helps the mean but
     regresses the OLD-answer stratum -> FAIL; (c) ACR within noise (t-test
     n.s.) -> FAIL; (d) Recall@k regresses -> FAIL.
  3. End-to-end `eval --acr-gate` over a synthetic KB: wiring, JSON schema,
     exit code, and determinism at a PINNED `as_of` (INV-1).
"""

import json
import math
import os

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # allow `python3 -m unittest tests.unit.retrieval.test_acr_gate`
    from _fixtures import SyntheticKBTestCase, load_cli


CLI = load_cli()


def _pq(ndcgs, ages):
    """Build a per-query list with just the fields the gate reads."""
    return [{"ndcg_at_k": nd, "answer_age_days": ag}
            for nd, ag in zip(ndcgs, ages)]


def _ablation(base_ndcgs, treat_ndcgs, ages, base_metrics=None,
              treat_metrics=None):
    """Hand-build an evaluate_ablation-shaped result for the gate.

    `base_metrics`/`treat_metrics` override the headline aggregates; by default
    the headline nDCG is the mean of the per-query nDCGs and the other guardrail
    metrics are equal (no harm) so a test can isolate a single failing check.
    """
    mean = lambda xs: (sum(xs) / len(xs)) if xs else 0.0
    base_m = {"recall_at_k": 1.0, "precision_at_k": 0.5,
              "ndcg_at_k": mean(base_ndcgs), "mrr": 1.0,
              "latency_ms_mean": 1.0, "latency_ms_p50": 1.0,
              "context_tokens_mean": 100.0}
    treat_m = {"recall_at_k": 1.0, "precision_at_k": 0.5,
               "ndcg_at_k": mean(treat_ndcgs), "mrr": 1.0,
               "latency_ms_mean": 1.0, "latency_ms_p50": 1.0,
               "context_tokens_mean": 100.0}
    if base_metrics:
        base_m.update(base_metrics)
    if treat_metrics:
        treat_m.update(treat_metrics)
    return {
        "mode": "ablate", "top_k": 5, "num_queries": len(base_ndcgs),
        "baseline": "bm25", "treatment": "bm25+acr",
        "strategies": {"bm25": base_m, "bm25+acr": treat_m},
        "delta": {k: treat_m[k] - base_m[k] for k in base_m},
        "per_query": {
            "bm25": _pq(base_ndcgs, ages),
            "bm25+acr": _pq(treat_ndcgs, ages),
        },
    }


# --- Layer 1: paired t-test + critical-value table ---------------------------

class PairedTHelperTest(SyntheticKBTestCase):
    def test_hand_computed_t_statistic(self):
        # deltas = [1,2,3,4,5]: mean=3, var(ddof=1)=10/4=2.5, sd=sqrt(2.5),
        # se=sd/sqrt(5), t = 3 / (sqrt(2.5)/sqrt(5)) = 3 / sqrt(0.5) = 4.2426407.
        tt = CLI.paired_t_test([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(tt["n"], 5)
        self.assertEqual(tt["df"], 4)
        self.assertAlmostEqual(tt["mean"], 3.0)
        self.assertAlmostEqual(tt["sd"], math.sqrt(2.5))
        self.assertAlmostEqual(tt["t"], 3.0 / math.sqrt(0.5))

    def test_zero_variance_nonzero_mean_is_infinite(self):
        tt = CLI.paired_t_test([0.2, 0.2, 0.2, 0.2])
        self.assertEqual(tt["sd"], 0.0)
        self.assertEqual(tt["t"], float("inf"))

    def test_all_zero_deltas_is_zero_t(self):
        tt = CLI.paired_t_test([0.0, 0.0, 0.0])
        self.assertEqual(tt["t"], 0.0)
        self.assertEqual(tt["mean"], 0.0)

    def test_single_and_empty_cannot_test(self):
        self.assertEqual(CLI.paired_t_test([0.5])["df"], 0)
        self.assertEqual(CLI.paired_t_test([0.5])["t"], 0.0)
        self.assertEqual(CLI.paired_t_test([])["n"], 0)

    def test_t_critical_table_lookups(self):
        self.assertAlmostEqual(CLI.t_critical(1, 0.05), 12.706)
        self.assertAlmostEqual(CLI.t_critical(4, 0.05), 2.776)
        self.assertAlmostEqual(CLI.t_critical(30, 0.05), 2.042)
        self.assertAlmostEqual(CLI.t_critical(4, 0.01), 4.604)

    def test_t_critical_between_rows_is_conservative(self):
        # df=55 is between tabulated 50 and 60 -> use 50 (the larger crit value).
        self.assertAlmostEqual(CLI.t_critical(55, 0.05), CLI.t_critical(50, 0.05))
        self.assertGreaterEqual(CLI.t_critical(55, 0.05), CLI.t_critical(60, 0.05))

    def test_t_critical_above_table_uses_normal_asymptote(self):
        self.assertAlmostEqual(CLI.t_critical(500, 0.05), 1.960)
        self.assertAlmostEqual(CLI.t_critical(500, 0.01), 2.576)

    def test_t_critical_rejects_bad_alpha_and_df(self):
        with self.assertRaises(ValueError):
            CLI.t_critical(10, 0.10)
        with self.assertRaises(ValueError):
            CLI.t_critical(0, 0.05)


# --- Layer 2: the 5-part decision rule (pure) --------------------------------

class AcrGateDecisionTest(SyntheticKBTestCase):
    def test_a_clear_win_passes(self):
        # Consistent +0.1 lift across 8 queries (recent + old), no harm,
        # old-stratum also improves -> all five checks hold.
        base = [0.5, 0.5, 0.6, 0.6, 0.7, 0.7, 0.4, 0.4]
        treat = [b + 0.1 for b in base]
        ages = [10, 20, 200, 300, 5, 8, 365, 400]  # mix of recent + old
        decision = CLI.acr_gate_decision(_ablation(base, treat, ages),
                                         determinism_ok=True)
        self.assertTrue(decision["passed"], decision["checks"])
        c = decision["checks"]
        self.assertTrue(c["primary"]["passed"])
        self.assertTrue(c["significance"]["passed"])
        self.assertTrue(c["win_rate"]["passed"])
        self.assertTrue(c["no_harm"]["passed"])
        self.assertTrue(c["age_stratum"]["passed"])
        self.assertGreater(c["age_stratum"]["old_count"], 0)

    def test_b_old_stratum_regression_fails(self):
        # Mean lift positive & significant, BUT the OLD-answer queries (age>=90)
        # REGRESS -> ACR "won" only by boosting recent entries -> gate FAIL.
        ages = [10, 15, 20, 25, 200, 300, 365, 400]
        base = [0.4, 0.4, 0.4, 0.4, 0.8, 0.8, 0.8, 0.8]
        # recent (first 4) jump +0.4; old (last 4) drop -0.2 each.
        treat = [0.8, 0.8, 0.8, 0.8, 0.6, 0.6, 0.6, 0.6]
        decision = CLI.acr_gate_decision(_ablation(base, treat, ages),
                                         determinism_ok=True)
        self.assertFalse(decision["passed"])
        self.assertFalse(decision["checks"]["age_stratum"]["passed"])
        # the mean still rose (isolates the stratum guard, not the primary)
        self.assertTrue(decision["checks"]["primary"]["passed"])

    def test_c_within_noise_fails_significance(self):
        # Mean lift clears the margin but is NOISY: high variance -> |t| < t_crit.
        # deltas alternate large +/- with a small positive mean.
        base = [0.5] * 8
        deltas = [0.9, -0.7, 0.8, -0.6, 0.9, -0.7, 0.8, -0.488]
        treat = [b + d for b, d in zip(base, deltas)]
        ages = [10, 20, 30, 40, 50, 60, 70, 80]  # all recent -> stratum vacuous
        decision = CLI.acr_gate_decision(_ablation(base, treat, ages),
                                         determinism_ok=True)
        self.assertFalse(decision["passed"])
        self.assertFalse(decision["checks"]["significance"]["passed"])
        # primary margin still met -> the t-test is what kills it
        self.assertTrue(decision["checks"]["primary"]["passed"])

    def test_d_recall_regression_fails(self):
        # Everything else strong, but Recall@k drops below baseline -> no-harm FAIL.
        base = [0.5, 0.5, 0.6, 0.6, 0.7, 0.7, 0.4, 0.4]
        treat = [b + 0.1 for b in base]
        ages = [10, 20, 30, 40, 50, 60, 70, 80]
        abl = _ablation(base, treat, ages,
                        treat_metrics={"recall_at_k": 0.80})  # base recall = 1.0
        decision = CLI.acr_gate_decision(abl, determinism_ok=True)
        self.assertFalse(decision["passed"])
        self.assertFalse(decision["checks"]["no_harm"]["passed"])
        self.assertFalse(decision["checks"]["no_harm"]["recall"]["passed"])

    def test_determinism_flag_is_a_guardrail(self):
        base = [0.5, 0.5, 0.6, 0.6, 0.7, 0.7, 0.4, 0.4]
        treat = [b + 0.1 for b in base]
        ages = [10, 20, 30, 40, 50, 60, 70, 80]
        abl = _ablation(base, treat, ages)
        self.assertFalse(
            CLI.acr_gate_decision(abl, determinism_ok=False)["passed"])
        self.assertTrue(
            CLI.acr_gate_decision(abl, determinism_ok=True)["passed"])

    def test_latency_budget_guardrail(self):
        base = [0.5, 0.5, 0.6, 0.6, 0.7, 0.7, 0.4, 0.4]
        treat = [b + 0.1 for b in base]
        ages = [10, 20, 30, 40, 50, 60, 70, 80]
        # treatment p50 far above baseline + budget -> latency check fails.
        abl = _ablation(base, treat, ages,
                        base_metrics={"latency_ms_p50": 1.0},
                        treat_metrics={"latency_ms_p50": 100.0})
        decision = CLI.acr_gate_decision(abl, determinism_ok=True,
                                         latency_budget_ms=10.0)
        self.assertFalse(decision["checks"]["no_harm"]["latency_p50"]["passed"])
        self.assertFalse(decision["passed"])

    def test_tunable_margin_changes_verdict(self):
        # A +0.05 mean lift passes the default 0.01 margin but fails a 0.2 margin.
        base = [0.5, 0.5, 0.6, 0.6, 0.7, 0.7, 0.4, 0.4]
        treat = [b + 0.05 for b in base]
        ages = [10, 20, 30, 40, 50, 60, 70, 80]
        abl = _ablation(base, treat, ages)
        self.assertTrue(
            CLI.acr_gate_decision(abl, determinism_ok=True,
                                  margin=0.01)["checks"]["primary"]["passed"])
        self.assertFalse(
            CLI.acr_gate_decision(abl, determinism_ok=True,
                                  margin=0.2)["checks"]["primary"]["passed"])


# --- Layer 3: end-to-end CLI wiring ------------------------------------------

class AcrGateCliTest(SyntheticKBTestCase):
    AS_OF = "2026-06-25"

    def _write_gold(self, rows):
        path = os.path.join(self.kdir, "recall-gold.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f)
        return path

    def _gold(self):
        return self._write_gold([
            {"query": "geofence ios reminders",
             "expected_ids": ["learn-geofence-reminders"]},
            {"query": "bm25 ranking retrieval",
             "expected_ids": ["ref-bm25-ranking"]},
            {"query": "macos timeout shell",
             "expected_ids": ["learn-macos-no-timeout"]},
            {"query": "python runtime stdlib",
             "expected_ids": ["config-python-runtime"]},
        ])

    def test_acr_gate_emits_decision_and_exit_code(self):
        gold = self._gold()
        code, out, err = self.run_cli(
            ["eval", "--gold", gold, "--acr-gate", "--as-of", self.AS_OF,
             "--format", "json"])
        payload = json.loads(out)
        self.assertIn("acr_gate", payload)
        dec = payload["acr_gate"]
        self.assertEqual(dec["baseline"], "bm25")
        self.assertEqual(dec["treatment"], "bm25+acr")
        # full 5-check structure present
        for key in ("primary", "significance", "win_rate", "no_harm",
                    "age_stratum"):
            self.assertIn(key, dec["checks"])
        # exit code mirrors the verdict (homogeneous synthetic KB => no lift =>
        # gate FAILs => exit 1, an honest outcome)
        self.assertEqual(code, 0 if dec["passed"] else 1)
        self.assertFalse(dec["passed"])  # no provenance/freshness spread here

    def test_acr_gate_is_deterministic_at_fixed_as_of(self):
        gold = self._gold()
        a = self.run_cli(["eval", "--gold", gold, "--acr-gate", "--as-of",
                          self.AS_OF, "--format", "json"])[1]
        b = self.run_cli(["eval", "--gold", gold, "--acr-gate", "--as-of",
                          self.AS_OF, "--format", "json"])[1]
        da, db = json.loads(a), json.loads(b)
        # strip the only non-deterministic field (latency) before comparing
        for d in (da, db):
            for strat in d["strategies"].values():
                strat.pop("latency_ms_mean", None)
                strat.pop("latency_ms_p50", None)
            d["acr_gate"]["checks"]["no_harm"]["latency_p50"] = None
            for s in d["per_query"].values():
                for row in s:
                    row.pop("latency_ms", None)
        self.assertEqual(da["acr_gate"]["passed"], db["acr_gate"]["passed"])
        self.assertEqual([q["ranked"] for q in da["per_query"]["bm25+acr"]],
                         [q["ranked"] for q in db["per_query"]["bm25+acr"]])

    def test_acr_gate_text_output_renders(self):
        gold = self._gold()
        code, out, err = self.run_cli(
            ["eval", "--gold", gold, "--acr-gate", "--as-of", self.AS_OF])
        self.assertIn("eval --acr-gate", out)
        self.assertIn("DECISION:", out)
        self.assertIn("old-stratum", out)


# --- Layer 4: Phase 4.1 — ledger recording + auto-flip default ---------------

def _decision(passed):
    """A minimal acr-gate decision dict (just what the row builder reads)."""
    return {
        "passed": passed,
        "baseline": "bm25",
        "treatment": "bm25+acr",
        "params": {"margin": 0.01, "alpha": 0.05},
        "checks": {"primary": {"passed": passed, "delta_ndcg": 0.05,
                               "margin": 0.01}},
    }


def _row_result():
    """An evaluate_ablation-shaped result the row builder can serialize."""
    r = _ablation([0.5, 0.6], [0.7, 0.8], [10, 200])
    r["gold_path"] = "/tmp/gold.json"
    r["as_of"] = "2026-06-25"
    return r


class AcrGateRowAndDefaultTest(SyntheticKBTestCase):
    """The pure Phase 4.1 seams: row builder + ledger-driven default resolver."""

    def test_row_carries_decided_strategy_and_evidence(self):
        git = {"commit": "abc1234", "committed": "", "subject": "", "dirty": False}
        checks = {"test_pass_rate": 1.0, "tests_ran": 1, "tests_failed": 0,
                  "index_validate_ok": True, "determinism_ok": True}
        win = CLI.build_acr_gate_row(_row_result(), _decision(True), git,
                                     "2026-06-25T00:00:00Z", checks)
        lose = CLI.build_acr_gate_row(_row_result(), _decision(False), git,
                                      "2026-06-25T00:00:00Z", checks)
        self.assertEqual(win["mode"], CLI.ACR_GATE_MODE)
        # PASS -> the recorded default strategy is the treatment; FAIL -> baseline
        self.assertEqual(win["strategy"], "bm25+acr")
        self.assertEqual(win["acr_gate"]["decided_strategy"], "bm25+acr")
        self.assertTrue(win["acr_gate"]["passed"])
        self.assertEqual(lose["strategy"], "bm25")
        self.assertFalse(lose["acr_gate"]["passed"])
        # full decision evidence is embedded
        self.assertIn("checks", win["acr_gate"])
        self.assertIn("ablation", win)

    def test_row_is_byte_identical_for_a_fixed_decision(self):
        git = {"commit": "abc1234", "committed": "", "subject": "", "dirty": False}
        checks = {"test_pass_rate": 1.0, "tests_ran": 1, "tests_failed": 0,
                  "index_validate_ok": True, "determinism_ok": True}
        a = CLI.build_acr_gate_row(_row_result(), _decision(True), git,
                                   "2026-06-25T00:00:00Z", checks)
        b = CLI.build_acr_gate_row(_row_result(), _decision(True), git,
                                   "2026-06-25T00:00:00Z", checks)
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))

    def _append_acr_row(self, passed, run):
        """Append one acr-gate row to the synthetic KB's ledger."""
        git = {"commit": "abc1234", "committed": "", "subject": "", "dirty": False}
        checks = {"test_pass_rate": 1.0, "tests_ran": 1, "tests_failed": 0,
                  "index_validate_ok": True, "determinism_ok": True}
        row = CLI.build_acr_gate_row(_row_result(), _decision(passed), git,
                                     run, checks)
        CLI.append_ledger_row(
            os.path.join(self.kdir, CLI.HISTORY_REL), row)
        return row

    def test_default_is_bm25_with_no_acr_gate_row(self):
        self.assertEqual(CLI.active_default_strategy(self.kdir), "bm25")

    def test_default_flips_to_acr_only_on_a_recorded_pass(self):
        self._append_acr_row(False, "2026-06-25T00:00:00Z")
        self.assertEqual(CLI.active_default_strategy(self.kdir), "bm25")
        # a later PASS row flips the default (most-recent run wins)
        self._append_acr_row(True, "2026-06-26T00:00:00Z")
        self.assertEqual(CLI.active_default_strategy(self.kdir), "bm25+acr")
        # a still-later FAIL row flips it back
        self._append_acr_row(False, "2026-06-27T00:00:00Z")
        self.assertEqual(CLI.active_default_strategy(self.kdir), "bm25")

    def test_bare_recall_inherits_the_recorded_default(self):
        # FAIL recorded -> bare recall stays bm25
        self._append_acr_row(False, "2026-06-25T00:00:00Z")
        code, out, _ = self.run_cli(
            ["recall", "bm25 ranking retrieval", "--format", "json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["strategy"], "bm25")
        # PASS recorded later -> bare recall auto-flips to bm25+acr
        self._append_acr_row(True, "2026-06-26T00:00:00Z")
        out2 = self.run_cli(
            ["recall", "bm25 ranking retrieval", "--format", "json"])[1]
        self.assertEqual(json.loads(out2)["strategy"], "bm25+acr")
        # an explicit --strategy bm25 still overrides (baselining reachable)
        out3 = self.run_cli(
            ["recall", "bm25 ranking retrieval", "--strategy", "bm25",
             "--format", "json"])[1]
        self.assertEqual(json.loads(out3)["strategy"], "bm25")


class AcrGateRecordCliTest(SyntheticKBTestCase):
    """End-to-end `eval --acr-gate --record`: ledger row + scorecard + default."""

    AS_OF = "2026-06-25"

    def _gold(self):
        path = os.path.join(self.kdir, "recall-gold.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([
                {"query": "geofence ios reminders",
                 "expected_ids": ["learn-geofence-reminders"]},
                {"query": "bm25 ranking retrieval",
                 "expected_ids": ["ref-bm25-ranking"]},
            ], f)
        return path

    def test_record_appends_row_regens_scorecard_and_keeps_default_bm25(self):
        gold = self._gold()
        code, out, _ = self.run_cli(
            ["eval", "--gold", gold, "--acr-gate", "--record",
             "--as-of", self.AS_OF, "--format", "json"])
        payload = json.loads(out)
        # homogeneous synthetic KB => no lift => FAIL => exit 1 (honest)
        self.assertEqual(code, 1)
        self.assertFalse(payload["acr_gate"]["passed"])
        self.assertIn("recorded", payload)
        self.assertEqual(payload["recorded"]["default_strategy"], "bm25")
        # the immutable ledger grew exactly one acr-gate row
        ledger = os.path.join(self.kdir, CLI.HISTORY_REL)
        rows = CLI._read_ledger(ledger)
        acr_rows = [r for r in rows if r.get("mode") == CLI.ACR_GATE_MODE]
        self.assertEqual(len(acr_rows), 1)
        self.assertEqual(acr_rows[0]["strategy"], "bm25")
        # SCORECARD.md was regenerated as a derived view
        self.assertTrue(os.path.isfile(
            os.path.join(self.kdir, CLI.SCORECARD_REL)))
        # the FAIL keeps the agent default on bm25
        self.assertEqual(CLI.active_default_strategy(self.kdir), "bm25")
        out2 = self.run_cli(
            ["recall", "bm25 ranking retrieval", "--format", "json"])[1]
        self.assertEqual(json.loads(out2)["strategy"], "bm25")

    def test_recorded_default_is_byte_identical_across_runs_at_fixed_as_of(self):
        gold = self._gold()
        self.run_cli(["eval", "--gold", gold, "--acr-gate", "--record",
                      "--as-of", self.AS_OF, "--format", "json"])
        a = self.run_cli(
            ["recall", "bm25 ranking retrieval", "--as-of", self.AS_OF,
             "--format", "json"])[1]
        b = self.run_cli(
            ["recall", "bm25 ranking retrieval", "--as-of", self.AS_OF,
             "--format", "json"])[1]
        self.assertEqual(a, b)  # INV-1: byte-identical at a fixed ledger + as_of

    def test_acr_gate_without_record_writes_nothing(self):
        gold = self._gold()
        self.run_cli(["eval", "--gold", gold, "--acr-gate",
                      "--as-of", self.AS_OF, "--format", "json"])
        self.assertFalse(os.path.isfile(
            os.path.join(self.kdir, CLI.HISTORY_REL)))


if __name__ == "__main__":
    import unittest
    unittest.main()
