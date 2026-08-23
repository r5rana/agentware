"""The OWN-GOLD half of the `agentware_lib.retrieval.evalcmd` owning suite.

Second half of `test_evalcmd.py`, split at task 99 wave 10 when that file hit
the corpus's 600-line cap. The seam is the module's own: the file beside this
one drives the two benchmark SUITES (a foreign corpus, their own except-chains);
this one drives everything that scores the OPERATOR'S own gold set — the
ablation instrument, `cmd_eval`'s arm dispatch, the two own-gold arms and the
ACR win gate's renderer. The shared header lives in `_evalcmd_support.py`.

Two properties here exist nowhere else in the corpus:

  * ARM PRECEDENCE. `cmd_eval` is a dispatcher, and its arms are selected by
    flags that can be set together. Each precedence row sets BOTH flags, so a
    reordering changes which arm ran rather than merely which line executed.
  * THE A/B VERDICT IS A SECOND, INDEPENDENT GATE. A run with no `--gate` and a
    FAILING A/B must still exit non-zero, which is only true if `_gate_exit` is
    handed both verdicts.
"""

from tests._paths import TESTS_DIR  # noqa: F401  (repo anchor, see the support file)

import json
import os
import unittest

try:
    from tests.unit.retrieval._evalcmd_support import (
        _Args, _Boom, _EvalCmdCase, _captured, _lme_scored, _metrics, _scored,
        evalcmd)
except ImportError:  # allow `python3 -m unittest tests.unit...` from tests/
    from _evalcmd_support import (  # noqa: F401
        _Args, _Boom, _EvalCmdCase, _captured, _lme_scored, _metrics, _scored,
        evalcmd)


class AblationInstrumentTest(_EvalCmdCase):
    """`evaluate_ablation` — the instrument that stays in this module."""

    def _fake_evaluate(self, per_strategy):
        def _ev(kdir, data, gold, strategy, top_k, as_of=None):
            return {"metrics": _metrics(per_strategy[strategy]),
                    "per_query": [{"query": "q", "strategy": strategy}],
                    "corpus_fingerprint": "fp", "corpus_size": 4}
        return _ev

    def test_evalcmd_ablation_reports_treatment_minus_baseline(self):
        """The sign convention IS the instrument: a positive delta means the
        treatment won. Reversing it turns every regression into a lift."""
        self._patch(evalcmd._metrics_rank, "evaluate",
                    self._fake_evaluate({"tag": 0.2, "bm25": 0.7}))
        out = evalcmd.evaluate_ablation(self.kdir, self.data, [1, 2], 3)
        self.assertAlmostEqual(out["delta"]["recall_at_k"], 0.5)

    def test_evalcmd_ablation_covers_every_headline_metric(self):
        self._patch(evalcmd._metrics_rank, "evaluate",
                    self._fake_evaluate({"tag": 0.2, "bm25": 0.7}))
        out = evalcmd.evaluate_ablation(self.kdir, self.data, [1], 3)
        self.assertEqual(sorted(out["delta"]),
                         sorted(evalcmd._ABLATE_METRIC_KEYS))

    def test_evalcmd_ablation_scores_the_same_corpus_for_both_arms(self):
        """Both strategies must be scored over ONE corpus, so a single
        fingerprint identifies the run. Two fingerprints would make the delta
        a comparison of two different things."""
        self._patch(evalcmd._metrics_rank, "evaluate",
                    self._fake_evaluate({"tag": 0.2, "bm25": 0.7}))
        out = evalcmd.evaluate_ablation(self.kdir, self.data, [1], 3)
        self.assertEqual(out["corpus_fingerprint"], "fp")
        self.assertEqual(out["corpus_size"], 4)

    def test_evalcmd_ablation_keeps_both_per_query_traces(self):
        """The per-query rows are the evidence behind the delta; dropping one
        side leaves a headline nobody can audit."""
        self._patch(evalcmd._metrics_rank, "evaluate",
                    self._fake_evaluate({"tag": 0.2, "bm25": 0.7}))
        out = evalcmd.evaluate_ablation(self.kdir, self.data, [1], 3)
        self.assertEqual(sorted(out["per_query"]), ["bm25", "tag"])

    def test_evalcmd_ablation_labels_the_two_strategies_it_ran(self):
        self._patch(evalcmd._metrics_rank, "evaluate",
                    self._fake_evaluate({"bm25": 0.2, "bm25+acr": 0.7}))
        out = evalcmd.evaluate_ablation(self.kdir, self.data, [1], 3,
                                        baseline="bm25", treatment="bm25+acr")
        self.assertEqual(out["baseline"], "bm25")
        self.assertEqual(out["treatment"], "bm25+acr")
        self.assertEqual(sorted(out["strategies"]), ["bm25", "bm25+acr"])


class DispatchTest(_EvalCmdCase):
    """`cmd_eval` — the dispatcher, and the precedence between its arms."""

    def setUp(self):
        super(DispatchTest, self).setUp()
        self.gold_path = os.path.join(self.kdir, "benchmarks",
                                      "recall-gold.json")
        with open(self.gold_path, "w", encoding="utf-8") as f:
            json.dump([{"query": "q", "expected_ids": ["e1"]}], f)
        self.prev_kdir = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(self._restore_kdir)
        self.seen = []

    def _restore_kdir(self):
        if self.prev_kdir is None:
            os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        else:
            os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.prev_kdir

    def _spy(self, label, rc=0):
        def _fn(*a, **kw):
            self.seen.append(label)
            return rc
        return _fn

    def _all_arms(self):
        for name, label in (("_run_longmemeval_suite", "lme"),
                            ("_run_gold_fixture_suite", "gold"),
                            ("_run_acr_gate", "acr"),
                            ("_run_ablate_suite", "ablate"),
                            ("_run_default_suite", "default")):
            self._patch(evalcmd, name, self._spy(label))

    def _dispatch(self, **kw):
        kw.setdefault("gold", None)
        kw.setdefault("top_k", 3)
        kw.setdefault("as_of", "2026-01-15")
        kw.setdefault("strategy", "tag")
        kw.setdefault("format", "text")
        kw.setdefault("tolerance", 0.02)
        rc, _out, err = self._call(evalcmd.cmd_eval, _Args(**kw))
        return rc, err

    def test_evalcmd_dispatches_the_default_arm_with_no_flags(self):
        self._all_arms()
        self.assertEqual(self._dispatch()[0], 0)
        self.assertEqual(self.seen, ["default"])

    def test_evalcmd_dispatches_the_ablate_arm(self):
        self._all_arms()
        self._dispatch(ablate=True)
        self.assertEqual(self.seen, ["ablate"])

    def test_evalcmd_dispatches_the_acr_gate_arm(self):
        self._all_arms()
        self._dispatch(acr_gate=True)
        self.assertEqual(self.seen, ["acr"])

    def test_evalcmd_acr_gate_wins_over_ablate(self):
        """Both flags set. `--acr-gate` is the specific instrument and runs its
        OWN ablation, so it takes precedence — a swap would silently run the
        generic table instead of the win gate."""
        self._all_arms()
        self._dispatch(acr_gate=True, ablate=True)
        self.assertEqual(self.seen, ["acr"])

    def test_evalcmd_a_suite_wins_over_every_own_gold_arm(self):
        """`--suite` selects a different CORPUS, so it is decided before any
        flag that selects a strategy over the operator's own gold set — and
        before the gold set is even loaded."""
        self._all_arms()
        self._dispatch(suite="longmemeval", acr_gate=True, ablate=True)
        self.assertEqual(self.seen, ["lme"])
        self.seen = []
        self._dispatch(suite="gold-fixture", ablate=True)
        self.assertEqual(self.seen, ["gold"])

    def test_evalcmd_a_suite_does_not_need_an_own_gold_set(self):
        """A suite scores its own corpus, so a KB with no `recall-gold.json`
        must still run one. Dispatching after the gold load would refuse it."""
        os.remove(self.gold_path)
        self._all_arms()
        rc, _err = self._dispatch(suite="gold-fixture")
        self.assertEqual(rc, 0)
        self.assertEqual(self.seen, ["gold"])

    def test_evalcmd_reports_a_missing_gold_set_before_a_bad_top_k(self):
        """ORDER in the own-gold path: there is nothing to score either way,
        and the missing file is the actionable message."""
        os.remove(self.gold_path)
        self._all_arms()
        rc, err = self._dispatch(top_k=0)
        self.assertEqual(rc, 1)
        self.assertIn("gold set not found", err)
        self.assertEqual(self.seen, [])

    def test_evalcmd_refuses_a_bad_top_k_before_dispatching_any_arm(self):
        self._all_arms()
        rc, err = self._dispatch(top_k=0)
        self.assertEqual(rc, 1)
        self.assertIn("--top-k", err)
        self.assertEqual(self.seen, [])

    def test_evalcmd_refuses_a_bad_as_of_before_dispatching_any_arm(self):
        self._all_arms()
        rc, err = self._dispatch(as_of="15-01-2026")
        self.assertEqual(rc, 1)
        self.assertIn("--as-of", err)
        self.assertEqual(self.seen, [])

    def test_evalcmd_threads_the_resolved_as_of_into_the_arm(self):
        """The arm receives the RESOLVED value, not the raw flag, so an
        invocation that omitted `--as-of` still scores at one pinned date
        rather than letting each arm default it again."""
        captured = {}

        def _fn(args, kdir, data, gold, gold_path, as_of):
            captured["as_of"] = as_of
            return 0
        self._patch(evalcmd, "_run_default_suite", _fn)
        self._dispatch(as_of=None)
        self.assertRegex(captured["as_of"], r"^\d{4}-\d\d-\d\d$")

    def test_evalcmd_wraps_an_acr_gate_backend_failure_as_exit_two(self):
        """The acr-gate arm is called inside a try because its ablation can hit
        an unavailable backend, and that is "cannot run" (2), not "failed" (1)."""
        self._patch(evalcmd, "_run_acr_gate",
                    _Boom(NotImplementedError("no embedder")))
        rc, err = self._dispatch(acr_gate=True)
        self.assertEqual(rc, 2)
        self.assertIn("no embedder", err)

    def test_evalcmd_propagates_the_arms_exit_code(self):
        """The dispatcher returns what the arm returned; swallowing a non-zero
        would turn a gate failure into a pass."""
        self._patch(evalcmd, "_run_default_suite", self._spy("default", rc=7))
        self.assertEqual(self._dispatch()[0], 7)


class OwnGoldArmTest(_EvalCmdCase):
    """`_run_ablate_suite` and `_run_default_suite`."""

    def setUp(self):
        super(OwnGoldArmTest, self).setUp()
        self.gold = [{"query": "q", "expected_ids": ["e1"]}]

    def _args(self, **kw):
        kw.setdefault("top_k", 3)
        kw.setdefault("format", "text")
        kw.setdefault("tolerance", 0.02)
        return _Args(**kw)

    def test_evalcmd_ablate_refuses_two_identical_strategies(self):
        """`--baseline x --treatment x` produces a delta of exactly zero for
        every metric, which reads as a measurement rather than a mistake. The
        refusal comes BEFORE any scoring — asserted with an instrument that
        would explode if it ran."""
        self._patch(evalcmd, "evaluate_ablation",
                    _Boom(AssertionError("must not score")))
        rc, _out, err = self._call(
            evalcmd._run_ablate_suite,
            self._args(baseline="bm25", treatment="bm25"),
            self.kdir, self.data, self.gold, "/gold.json", "2026-01-15")
        self.assertEqual(rc, 1)
        self.assertIn("must differ (got bm25)", err)

    def test_evalcmd_ablate_defaults_the_two_strategies(self):
        """No baseline/treatment attributes at all: the arm must fall back to
        the documented tag-vs-bm25 pair rather than raise."""
        seen = {}

        def _ablate(kdir, data, gold, top_k, baseline=None, treatment=None,
                    as_of=None):
            seen.update(baseline=baseline, treatment=treatment)
            return {"top_k": top_k, "num_queries": 1, "baseline": baseline,
                    "treatment": treatment,
                    "strategies": {baseline: _metrics(0.2),
                                   treatment: _metrics(0.7)},
                    "delta": {k: 0.5 for k in evalcmd._ABLATE_METRIC_KEYS}}
        self._patch(evalcmd, "evaluate_ablation", _ablate)
        rc, _out, _err = self._call(
            evalcmd._run_ablate_suite, self._args(), self.kdir, self.data,
            self.gold, "/gold.json", "2026-01-15")
        self.assertEqual(rc, 0)
        self.assertEqual(seen, {"baseline": "tag", "treatment": "bm25"})

    def test_evalcmd_ablate_reports_an_unavailable_backend_as_exit_two(self):
        self._patch(evalcmd, "evaluate_ablation",
                    _Boom(NotImplementedError("no embedder")))
        rc, _out, err = self._call(
            evalcmd._run_ablate_suite, self._args(), self.kdir, self.data,
            self.gold, "/gold.json", "2026-01-15")
        self.assertEqual(rc, 2)
        self.assertIn("no embedder", err)

    def test_evalcmd_default_arm_reports_an_unavailable_backend_as_exit_two(self):
        self._patch(evalcmd._metrics_rank, "evaluate",
                    _Boom(NotImplementedError("no embedder")))
        rc, _out, err = self._call(
            evalcmd._run_default_suite, self._args(strategy="bm25+embed"),
            self.kdir, self.data, self.gold, "/gold.json", "2026-01-15")
        self.assertEqual(rc, 2)
        self.assertIn("no embedder", err)

    def test_evalcmd_default_arm_stamps_gold_path_and_as_of(self):
        self._patch(evalcmd._metrics_rank, "evaluate",
                    lambda *a, **k: _scored())
        _rc, out, _err = self._call(
            evalcmd._run_default_suite,
            self._args(strategy="tag", format="json"), self.kdir, self.data,
            self.gold, "/gold.json", "2020-02-02")
        payload = json.loads(out)
        self.assertEqual(payload["gold_path"], "/gold.json")
        self.assertEqual(payload["as_of"], "2020-02-02")

    def test_evalcmd_default_arm_skips_the_ab_gate_unless_asked(self):
        self._patch(evalcmd._metrics_rank, "evaluate",
                    lambda *a, **k: _scored())
        self._patch(evalcmd._regressiongate, "run_ab_gate",
                    _Boom(AssertionError("A/B must not run")))
        rc, _out, _err = self._call(
            evalcmd._run_default_suite, self._args(strategy="tag"), self.kdir,
            self.data, self.gold, "/gold.json", "2026-01-15")
        self.assertEqual(rc, 0)

    def test_evalcmd_default_arm_fails_when_only_the_ab_gate_fails(self):
        """THE SECOND GATE. The regression gate is absent (no --gate), so the
        ONLY thing that can fail this run is the A/B verdict — which means
        `_gate_exit(gate, ab)` must be passed both. Dropping `ab` there exits
        0 and no report changes."""
        self._patch(evalcmd._metrics_rank, "evaluate",
                    lambda *a, **k: _scored())
        self._patch(evalcmd._regressiongate, "run_ab_gate",
                    lambda *a, **k: {"passed": False, "available": True,
                                     "working": 0.1, "head": 0.9,
                                     "delta": -0.8, "tolerance": 0.02,
                                     "head_commit": "abc1234"})
        rc, _out, _err = self._call(
            evalcmd._run_default_suite, self._args(strategy="tag", ab=True),
            self.kdir, self.data, self.gold, "/gold.json", "2026-01-15")
        self.assertNotEqual(rc, 0)

    def test_evalcmd_default_arm_passes_when_the_ab_gate_passes(self):
        self._patch(evalcmd._metrics_rank, "evaluate",
                    lambda *a, **k: _scored())
        self._patch(evalcmd._regressiongate, "run_ab_gate",
                    lambda *a, **k: {"passed": True, "available": True,
                                     "working": 0.9, "head": 0.9,
                                     "delta": 0.0, "tolerance": 0.02,
                                     "head_commit": "abc1234"})
        rc, _out, _err = self._call(
            evalcmd._run_default_suite, self._args(strategy="tag", ab=True),
            self.kdir, self.data, self.gold, "/gold.json", "2026-01-15")
        self.assertEqual(rc, 0)


class AcrGatePrinterTest(unittest.TestCase):
    """`_ok` and `_print_acr_gate` — the win gate's own renderer."""

    def test_evalcmd_ok_token_is_pass_or_fail(self):
        self.assertEqual(evalcmd._ok(True), "PASS")
        self.assertEqual(evalcmd._ok(False), "FAIL")

    def _decision(self, passed):
        return {
            "baseline": "bm25", "treatment": "bm25+acr", "passed": passed,
            "checks": {
                "primary": {"passed": passed, "delta_ndcg": 0.02,
                            "margin": 0.01},
                "significance": {"passed": passed, "t": 3.0, "df": 4,
                                 "t_crit": 2.7, "mean_delta": 0.02,
                                 "alpha": 0.05},
                "win_rate": {"passed": passed, "improved": 3, "regressed": 1,
                             "ties": 0},
                "no_harm": {"passed": passed,
                            "recall": {"passed": True},
                            "mrr": {"passed": True},
                            "latency_p50": {"passed": True},
                            "determinism_ok": True},
                "age_stratum": {"passed": passed, "old_count": 2,
                                "old_ndcg_baseline": 0.1,
                                "old_ndcg_treatment": 0.2},
            },
        }

    def _render(self, passed):
        result = {"top_k": 5, "num_queries": 4, "gold_path": "/g.json"}
        with _captured() as (out, _err):
            evalcmd._print_acr_gate(result, self._decision(passed),
                                    "2026-01-15")
        return out.getvalue()

    def test_evalcmd_acr_report_states_the_consequence_of_the_verdict(self):
        """The gate decides the AGENT-FACING DEFAULT, so the report says what
        the verdict means rather than only whether it passed."""
        self.assertIn("bm25+acr MAY become the default", self._render(True))
        self.assertIn("default stays bm25", self._render(False))

    def test_evalcmd_acr_report_names_all_five_checks(self):
        text = self._render(True)
        for check in ("primary", "significant", "win-rate", "no-harm",
                      "old-stratum"):
            self.assertIn(check, text)

    def test_evalcmd_acr_report_breaks_the_no_harm_check_into_its_parts(self):
        """`no-harm` is four guardrails, and a composite PASS hides which one
        would have blocked; each sub-verdict is printed."""
        text = self._render(True)
        line = [ln for ln in text.splitlines() if "no-harm" in ln][0]
        for part in ("recall=", "mrr=", "latency_p50=", "determinism="):
            self.assertIn(part, line)


if __name__ == "__main__":
    unittest.main()
