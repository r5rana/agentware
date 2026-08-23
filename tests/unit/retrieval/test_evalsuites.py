"""Owning unit file for `agentware_lib.retrieval.evalsuites` — task 99 wave 10.

The module is new: it holds the `eval` verb's shared input guards, the ONE
record-and-gate block both benchmark suites run, and the four human report
renderers — all lifted out of `evalcmd`, which had hit its 600-line cap with
three ceiling rows still on its handler family.

What is asserted here is the module's PROMISE, not its shape:

  * REFUSAL ORDER IS A CONTRACT. `validate_lme_args` checks dataset-exists, then
    --top-k, then --as-of, then --limit. An invocation can be wrong in several
    of those ways at once, and WHICH message it gets is what an operator debugs
    against — so every order row is driven on an input that would also fail a
    later check. A reordering then changes the message rather than merely the
    path, and no other test in this file could see it.
  * THE GUARD RETURNS THE RESOLVED VALUE. `validate_scoring_args` hands back the
    `as_of` it validated, so a caller cannot default it a second way. A guard
    that returned only a verdict would leave two arms of one command free to
    score at two different dates.
  * PRIOR ROWS ARE READ BEFORE THE NEW ONE IS WRITTEN. `record_and_gate_suite`
    reads the ledger first, so this run's own row can never become its own
    baseline — which would make every gate pass by comparing a run to itself.
    Asserted against a ledger that ALREADY holds a comparable row.
  * `--gate` IMPLIES `--record`, AND A FIRST RUN IS `seeded`, NOT A PASS NOBODY
    MEASURED.
  * THE BLOCK MUTATES `result`, AND THAT IS THE CONTRACT. `--format json`
    serializes `result`; `recorded` and `gate` are how a consumer reads what was
    written and what was decided.

Stdlib-only, hermetic: a temp knowledge dir under `mkdtemp`, the package's own
`AGENTWARE_NESTED_UNITTEST` recursion guard set so `--record` never shells the
suite, no network.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.retrieval.evalsuites as evalsuites  # noqa: E402


class _Args(object):
    """An argparse-shaped namespace. A case that OMITS a key is a real input
    shape: every guard here reads optional attributes with `getattr`."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


@contextlib.contextmanager
def _captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


class ScoringArgGuardTest(unittest.TestCase):
    """`validate_scoring_args` — the --top-k / --as-of pair."""

    def test_evalsuites_valid_args_return_the_resolved_as_of(self):
        with _captured():
            as_of, err = evalsuites.validate_scoring_args(
                _Args(top_k=5, as_of="2026-01-15"))
        self.assertIsNone(err)
        self.assertEqual(as_of, "2026-01-15")

    def test_evalsuites_absent_as_of_defaults_to_a_real_date(self):
        """The default is today, and it must PASS its own validation — a guard
        that defaulted to None would refuse every invocation that omitted the
        flag, which is most of them."""
        with _captured():
            as_of, err = evalsuites.validate_scoring_args(_Args(top_k=5))
        self.assertIsNone(err)
        self.assertRegex(as_of, r"^\d{4}-\d\d-\d\d$")

    def test_evalsuites_zero_top_k_is_refused_with_exit_one(self):
        with _captured() as (_out, err):
            as_of, rc = evalsuites.validate_scoring_args(
                _Args(top_k=0, as_of="2026-01-15"))
        self.assertEqual(rc, 1)
        self.assertIsNone(as_of)
        self.assertIn("--top-k must be a positive integer", err.getvalue())

    def test_evalsuites_negative_top_k_is_refused_too(self):
        """`<= 0`, not `== 0`: argparse accepts a negative int happily."""
        with _captured() as (_out, err):
            _as_of, rc = evalsuites.validate_scoring_args(_Args(top_k=-1))
        self.assertEqual(rc, 1)
        self.assertIn("--top-k", err.getvalue())

    def test_evalsuites_malformed_as_of_is_refused(self):
        with _captured() as (_out, err):
            _as_of, rc = evalsuites.validate_scoring_args(
                _Args(top_k=5, as_of="15-01-2026"))
        self.assertEqual(rc, 1)
        self.assertIn("--as-of must be a YYYY-MM-DD date", err.getvalue())

    def test_evalsuites_impossible_calendar_date_is_refused(self):
        """Shape alone is not enough: `2026-13-45` matches YYYY-MM-DD and is
        not a date. A regex-only check passes it."""
        with _captured() as (_out, err):
            _as_of, rc = evalsuites.validate_scoring_args(
                _Args(top_k=5, as_of="2026-13-45"))
        self.assertEqual(rc, 1)
        self.assertIn("--as-of", err.getvalue())

    def test_evalsuites_top_k_is_checked_before_as_of(self):
        """THE ORDER ROW for this pair. Both inputs are wrong; the message must
        be the top-k one. Nothing else in this class can see a swap."""
        with _captured() as (_out, err):
            evalsuites.validate_scoring_args(_Args(top_k=0, as_of="nope"))
        self.assertIn("--top-k", err.getvalue())
        self.assertNotIn("--as-of", err.getvalue())


class LongMemEvalArgGuardTest(unittest.TestCase):
    """`validate_lme_args` — four refusals whose ORDER is the contract."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="evalsuites-lme-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.dataset = os.path.join(self.kdir, "dataset.json")
        with open(self.dataset, "w", encoding="utf-8") as f:
            json.dump([], f)

    def test_evalsuites_lme_valid_args_return_path_as_of_and_no_error(self):
        with _captured():
            path, as_of, err = evalsuites.validate_lme_args(
                _Args(dataset=self.dataset, top_k=2, as_of="2026-01-15",
                      limit=None), self.kdir)
        self.assertIsNone(err)
        self.assertEqual(path, self.dataset)
        self.assertEqual(as_of, "2026-01-15")

    def test_evalsuites_lme_absent_dataset_attr_falls_back_under_the_kdir(self):
        """With no --dataset the guard resolves the package's documented default
        RELATIVE TO THE KNOWLEDGE DIR. Here that file does not exist, so the
        refusal names the path it looked at — which is the useful half."""
        with _captured() as (_out, err):
            path, _as_of, rc = evalsuites.validate_lme_args(
                _Args(top_k=2, as_of="2026-01-15", limit=None), self.kdir)
        self.assertEqual(rc, 1)
        self.assertIsNone(path)
        self.assertIn(self.kdir, err.getvalue())
        self.assertIn("longmemeval dataset not found", err.getvalue())

    def test_evalsuites_lme_missing_dataset_names_the_remedy(self):
        """The refusal an operator most often hits is the one with an action
        attached: acquire the file, or pass --dataset."""
        with _captured() as (_out, err):
            evalsuites.validate_lme_args(
                _Args(dataset=os.path.join(self.kdir, "absent.json"), top_k=2,
                      as_of="2026-01-15", limit=None), self.kdir)
        self.assertIn("DATASET.md", err.getvalue())
        self.assertIn("--dataset", err.getvalue())

    def test_evalsuites_lme_limit_none_means_score_everything(self):
        """`None` is not an error — it is the default and it means all."""
        with _captured():
            _p, _a, err = evalsuites.validate_lme_args(
                _Args(dataset=self.dataset, top_k=2, as_of="2026-01-15",
                      limit=None), self.kdir)
        self.assertIsNone(err)

    def test_evalsuites_lme_absent_limit_attr_is_also_fine(self):
        with _captured():
            _p, _a, err = evalsuites.validate_lme_args(
                _Args(dataset=self.dataset, top_k=2, as_of="2026-01-15"),
                self.kdir)
        self.assertIsNone(err)

    def test_evalsuites_lme_zero_limit_is_refused(self):
        """A limit of zero scores nothing and would report a perfect run over
        an empty question set."""
        with _captured() as (_out, err):
            _p, _a, rc = evalsuites.validate_lme_args(
                _Args(dataset=self.dataset, top_k=2, as_of="2026-01-15",
                      limit=0), self.kdir)
        self.assertEqual(rc, 1)
        self.assertIn("--limit must be a positive integer", err.getvalue())

    def test_evalsuites_lme_refusals_keep_their_order(self):
        """THE ORDER TABLE. Each row is wrong in its OWN way AND in every later
        way, so the message names the check that actually came first. A guard
        whose four checks were reordered passes every single-fault test and
        fails exactly here."""
        absent = os.path.join(self.kdir, "absent.json")
        rows = [
            ("longmemeval dataset not found",
             _Args(dataset=absent, top_k=0, as_of="nope", limit=0)),
            ("--top-k must be a positive integer",
             _Args(dataset=self.dataset, top_k=0, as_of="nope", limit=0)),
            ("--as-of must be a YYYY-MM-DD date",
             _Args(dataset=self.dataset, top_k=2, as_of="nope", limit=0)),
            ("--limit must be a positive integer",
             _Args(dataset=self.dataset, top_k=2, as_of="2026-01-15", limit=0)),
        ]
        for needle, args in rows:
            with _captured() as (_out, err):
                _p, _a, rc = evalsuites.validate_lme_args(args, self.kdir)
            self.assertEqual(rc, 1, needle)
            self.assertIn(needle, err.getvalue())

    def test_evalsuites_lme_reuses_the_shared_pair_rather_than_restating_it(self):
        """The top-k and as-of messages here are the SAME strings the shared
        guard emits. Two copies of a refusal drift; one does not."""
        with _captured() as (_out, shared):
            evalsuites.validate_scoring_args(_Args(top_k=0))
        with _captured() as (_out, wrapped):
            evalsuites.validate_lme_args(
                _Args(dataset=self.dataset, top_k=0), self.kdir)
        self.assertEqual(shared.getvalue(), wrapped.getvalue())


def _suite_result(recall=0.5, suite="gold-fixture", strategy="tag"):
    """A scorer-shaped result the ledger recorder can serialize."""
    metrics = {"recall_at_k": recall, "precision_at_k": 0.2,
               "ndcg_at_k": recall, "mrr": recall,
               "latency_ms_mean": 1.0, "latency_ms_p50": 1.0,
               "context_tokens_mean": 10.0}
    return {"strategy": strategy, "top_k": 3, "num_queries": 2,
            "metrics": metrics, "per_query": [], "suite": suite,
            "as_of": "2026-01-15", "determinism_ok": True,
            "corpus_fingerprint": "fp-frozen", "corpus_size": 2,
            "gold_path": "<fixture>"}


class RecordAndGateSuiteTest(unittest.TestCase):
    """`record_and_gate_suite` — the block both suites used to hand-copy."""

    def setUp(self):
        # The package's own recursion guard: `record_ledger_suite` measures a
        # test pass rate by SHELLING the suite, and a suite run never sets it.
        self.prev = os.environ.get("AGENTWARE_NESTED_UNITTEST")
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"
        self.kdir = tempfile.mkdtemp(prefix="evalsuites-rec-")
        os.makedirs(os.path.join(self.kdir, "benchmarks"), exist_ok=True)
        with open(os.path.join(self.kdir, "index.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"entries": []}, f)
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.addCleanup(self._restore)
        self.ledger = os.path.join(self.kdir, "benchmarks", "history.jsonl")

    def _restore(self):
        if self.prev is None:
            os.environ.pop("AGENTWARE_NESTED_UNITTEST", None)
        else:
            os.environ["AGENTWARE_NESTED_UNITTEST"] = self.prev

    def _run(self, args, result=None, suite="gold-fixture"):
        result = _suite_result() if result is None else result
        with _captured():
            recorded, gate = evalsuites.record_and_gate_suite(
                args, self.kdir, {"entries": []}, result, suite)
        return recorded, gate, result

    def _rows(self):
        if not os.path.isfile(self.ledger):
            return []
        with open(self.ledger, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def test_evalsuites_no_flags_records_nothing_and_leaves_result_alone(self):
        recorded, gate, result = self._run(_Args(tolerance=0.02))
        self.assertIsNone(recorded)
        self.assertIsNone(gate)
        self.assertNotIn("recorded", result)
        self.assertNotIn("gate", result)
        self.assertEqual(self._rows(), [])

    def test_evalsuites_record_appends_exactly_one_row(self):
        recorded, gate, result = self._run(_Args(record=True, tolerance=0.02))
        self.assertIsNotNone(recorded)
        self.assertIsNone(gate)
        self.assertEqual(len(self._rows()), 1)
        self.assertIn("recorded", result)

    def test_evalsuites_recorded_stamp_carries_the_ledger_path(self):
        """The stamp is what `--format json` publishes; a consumer reads the
        path from it rather than re-deriving where the ledger lives."""
        _rec, _gate, result = self._run(_Args(record=True, tolerance=0.02))
        self.assertEqual(result["recorded"]["path"], self.ledger)
        for key in ("commit", "run", "reliability"):
            self.assertIn(key, result["recorded"])

    def test_evalsuites_gate_implies_record(self):
        """A gated run must be IN the ledger it was judged against — otherwise
        the next run has no prior to compare to and every run seeds forever."""
        recorded, gate, _r = self._run(_Args(gate=True, tolerance=0.02))
        self.assertIsNotNone(recorded)
        self.assertIsNotNone(gate)
        self.assertEqual(len(self._rows()), 1)

    def test_evalsuites_first_gated_run_is_seeded_not_a_measured_pass(self):
        _rec, gate, _r = self._run(_Args(gate=True, tolerance=0.02))
        self.assertTrue(gate["seeded"])
        self.assertIsNone(gate["baseline"])
        self.assertTrue(gate["passed"])

    def test_evalsuites_gate_stamps_the_suite_it_was_given(self):
        """The ONE thing that differed between the two hand-copied sites. It is
        a parameter now, so the two suites cannot drift apart in what they
        record — and a hard-coded value fails this."""
        _rec, gate, _r = self._run(_Args(gate=True, tolerance=0.02),
                                   suite="longmemeval")
        self.assertEqual(gate["suite"], "longmemeval")

    def test_evalsuites_gate_reads_priors_before_writing_this_run(self):
        """THE ORDER ROW. A first run seeds. A SECOND run over the same suite,
        strategy and fingerprint must find the first as its baseline — which is
        only possible if the ledger is read BEFORE this run's row is appended.
        An implementation that recorded first would find its own row, compare a
        run to itself, and report `seeded=False` with a baseline equal to the
        current metrics — passing every other test here."""
        self._run(_Args(gate=True, tolerance=0.02))
        _rec, gate, _r = self._run(_Args(gate=True, tolerance=0.02))
        self.assertFalse(gate["seeded"])
        self.assertIsNotNone(gate["baseline"])
        self.assertEqual(len(self._rows()), 2)

    def test_evalsuites_a_worse_run_fails_the_gate_and_names_the_metric(self):
        """The gate's whole job. The prior scores 0.9, this run 0.1, tolerance
        0.02 — so it regresses, and the report says which metric."""
        self._run(_Args(gate=True, tolerance=0.02), _suite_result(recall=0.9))
        _rec, gate, _r = self._run(_Args(gate=True, tolerance=0.02),
                                   _suite_result(recall=0.1))
        self.assertFalse(gate["passed"])
        self.assertTrue(gate["regressions"])
        self.assertIn("metric", gate["regressions"][0])

    def test_evalsuites_gate_is_stamped_onto_the_result(self):
        _rec, gate, result = self._run(_Args(gate=True, tolerance=0.02))
        self.assertIs(result["gate"], gate)

    def test_evalsuites_record_without_gate_leaves_no_gate_key(self):
        """`--record` alone must not fabricate a verdict nobody asked for."""
        _rec, gate, result = self._run(_Args(record=True, tolerance=0.02))
        self.assertIsNone(gate)
        self.assertNotIn("gate", result)


class ReportRendererTest(unittest.TestCase):
    """The four human reports and the recorded line they share."""

    KDIR = "/scratch/kb"
    RECORDED = {"commit": "abc1234", "reliability": 77.5, "run": "R-1"}

    def _render(self, fn, *a):
        with _captured() as (out, _err):
            fn(*a)
        return out.getvalue()

    def test_evalsuites_recorded_line_is_a_noop_when_nothing_recorded(self):
        """Every caller prints it unconditionally, so `None` must produce no
        output at all rather than a line saying nothing was recorded."""
        self.assertEqual(
            self._render(evalsuites.print_recorded_line, None, self.KDIR), "")

    def test_evalsuites_recorded_line_names_ledger_commit_reliability_and_run(self):
        text = self._render(evalsuites.print_recorded_line, self.RECORDED,
                            self.KDIR)
        for needle in (self.KDIR, "abc1234", "77.50", "R-1"):
            self.assertIn(needle, text)

    def _lme_result(self):
        return {
            "strategy": "tag", "top_k": 2, "num_queries": 2,
            "as_of": "2026-01-15", "determinism_ok": True,
            "metrics": {"recall_at_k": 0.5, "precision_at_k": 0.25,
                        "ndcg_at_k": 0.5, "mrr": 0.5,
                        "latency_ms_mean": 1.5, "latency_ms_p50": 1.25},
            "per_category": {
                "zeta": {"recall_at_k": 1.0, "ndcg_at_k": 1.0, "mrr": 1.0,
                         "num_queries": 1},
                "alpha": {"recall_at_k": 0.0, "ndcg_at_k": 0.0, "mrr": 0.0,
                          "num_queries": 1}},
            "abstention": {"count": 3, "by_category": {"beta": 2, "alpha": 1}},
        }

    def test_evalsuites_lme_report_sorts_the_per_category_table(self):
        """Sorted by name so two runs diff cleanly. The fixture deliberately
        declares `zeta` before `alpha`, so insertion order and sorted order
        disagree and only one of them can pass."""
        text = self._render(evalsuites.print_longmemeval_report,
                            self._lme_result(), "/ds.json", None, self.KDIR)
        self.assertLess(text.index("alpha"), text.index("zeta"))

    def test_evalsuites_lme_report_separates_abstention_from_recall(self):
        """Abstention questions have no correct retrieval, so they are excluded
        from the aggregate and reported on their own line. A report that folded
        them in would publish a recall figure for unanswerable questions."""
        text = self._render(evalsuites.print_longmemeval_report,
                            self._lme_result(), "/ds.json", None, self.KDIR)
        self.assertIn("abstention (excluded from Recall", text)
        self.assertIn("3", text.split("abstention")[1])

    def test_evalsuites_lme_report_names_the_dataset_it_scored(self):
        text = self._render(evalsuites.print_longmemeval_report,
                            self._lme_result(), "/ds.json", None, self.KDIR)
        self.assertIn("/ds.json", text)

    def test_evalsuites_lme_report_appends_the_recorded_line_when_recorded(self):
        plain = self._render(evalsuites.print_longmemeval_report,
                             self._lme_result(), "/ds.json", None, self.KDIR)
        stamped = self._render(evalsuites.print_longmemeval_report,
                               self._lme_result(), "/ds.json", self.RECORDED,
                               self.KDIR)
        self.assertNotIn("recorded:", plain)
        self.assertIn("recorded:", stamped)

    def test_evalsuites_gold_report_prints_the_corpus_fingerprint(self):
        """The fingerprint is what makes this suite's absolute gate meaningful,
        and it is the one field the other three reports do not print."""
        text = self._render(evalsuites.print_gold_fixture_report,
                            _suite_result(), "/fixture.json", None, self.KDIR)
        self.assertIn("fingerprint: fp-frozen", text)

    def test_evalsuites_gold_report_reports_a_determinism_failure_loudly(self):
        """`ok` vs `FAIL`, not a boolean repr: a re-score that ranked
        differently is the one result this frozen suite must not whisper."""
        result = _suite_result()
        result["determinism_ok"] = False
        text = self._render(evalsuites.print_gold_fixture_report, result,
                            "/fixture.json", None, self.KDIR)
        self.assertIn("determinism   : FAIL", text)

    def _ablate_result(self):
        base = {"recall_at_k": 0.4, "precision_at_k": 0.2, "ndcg_at_k": 0.4,
                "mrr": 0.4, "latency_ms_mean": 1.0, "latency_ms_p50": 1.0,
                "context_tokens_mean": 10.0}
        treat = dict(base, recall_at_k=0.1, ndcg_at_k=0.9)
        delta = {k: treat[k] - base[k] for k in base}
        return {"top_k": 3, "num_queries": 2, "baseline": "tag",
                "treatment": "bm25", "strategies": {"tag": base, "bm25": treat},
                "delta": delta}

    def test_evalsuites_ablate_report_signs_the_delta_in_both_directions(self):
        """A regression must READ as one. The fixture moves nDCG UP and recall
        DOWN in the same run, so a renderer that dropped the sign, or took an
        absolute value, cannot pass."""
        text = self._render(evalsuites.print_ablate_report,
                            self._ablate_result(), "/gold.json", None,
                            self.KDIR)
        self.assertIn("+0.5000", text)
        self.assertIn("-0.3000", text)

    def test_evalsuites_ablate_report_names_the_two_strategies_in_the_header(self):
        """A table pasted into a worklog has to say what it compared, so the
        columns are named `tag` and `bm25`, not `baseline`/`treatment`."""
        text = self._render(evalsuites.print_ablate_report,
                            self._ablate_result(), "/gold.json", None,
                            self.KDIR)
        header = [ln for ln in text.splitlines() if "metric" in ln][0]
        self.assertIn("tag", header)
        self.assertIn("bm25", header)

    def test_evalsuites_ablate_report_prints_every_headline_metric(self):
        text = self._render(evalsuites.print_ablate_report,
                            self._ablate_result(), "/gold.json", None,
                            self.KDIR)
        for label in ("Recall@3", "Precision@3", "nDCG@3", "MRR",
                      "latency_ms_mean", "latency_ms_p50", "ctx_tokens_mean"):
            self.assertIn(label, text)

    def test_evalsuites_default_report_prints_the_context_footprint(self):
        """The own-gold arm is the only one whose corpus is the operator's real
        KB, so it is the only report where a context-token mean means anything
        — and the only one that prints it."""
        text = self._render(evalsuites.print_default_report, _suite_result(),
                            "/gold.json", None, self.KDIR)
        self.assertIn("ctx tokens", text)

    def test_evalsuites_default_report_names_the_gold_set_it_scored(self):
        text = self._render(evalsuites.print_default_report, _suite_result(),
                            "/gold.json", None, self.KDIR)
        self.assertIn("gold: /gold.json", text)

    def test_evalsuites_reports_share_one_recorded_line(self):
        """All four reports end with the SAME line, byte for byte. Four copies
        of it would drift; one does not."""
        shared = self._render(evalsuites.print_recorded_line, self.RECORDED,
                              self.KDIR)
        for text in (
            self._render(evalsuites.print_gold_fixture_report, _suite_result(),
                         "/f", self.RECORDED, self.KDIR),
            self._render(evalsuites.print_default_report, _suite_result(),
                         "/g", self.RECORDED, self.KDIR),
            self._render(evalsuites.print_ablate_report, self._ablate_result(),
                         "/g", self.RECORDED, self.KDIR),
            self._render(evalsuites.print_longmemeval_report,
                         self._lme_result(), "/d", self.RECORDED, self.KDIR),
        ):
            self.assertTrue(text.endswith(shared))


class EvalSuitesSurfaceTest(unittest.TestCase):

    def test_evalsuites_exports_name_every_helper(self):
        for name in ("validate_scoring_args", "validate_lme_args",
                     "record_and_gate_suite", "print_recorded_line",
                     "print_longmemeval_report", "print_gold_fixture_report",
                     "print_ablate_report", "print_default_report"):
            self.assertIn(name, evalsuites._EXPORTS)
            self.assertTrue(hasattr(evalsuites, name), name)

    def test_evalsuites_never_imports_the_handler_module(self):
        """The direction is one-way BY CONSTRUCTION: `evalcmd` imports this
        module, never the reverse. An import back would be a cycle, and the
        ablation instrument the reports describe deliberately stays in
        `evalcmd` so no renderer ever needs one."""
        with open(evalsuites.__file__, encoding="utf-8") as fh:
            src = fh.read()
        code = "\n".join(ln for ln in src.splitlines()
                         if ln.startswith(("import ", "from ")))
        self.assertNotIn("evalcmd", code)

    def test_evalsuites_stays_inside_its_layer(self):
        """retrieval may reach core and its own siblings, nothing above."""
        with open(evalsuites.__file__, encoding="utf-8") as fh:
            src = fh.read()
        for banned in ("agentware_lib.ops", "agentware_lib.features",
                       "agentware_lib.cli", "agentware_lib.knowledge"):
            self.assertNotIn("import %s" % banned, src)


if __name__ == "__main__":
    unittest.main()
