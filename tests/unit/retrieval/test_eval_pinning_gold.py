"""GoldFixtureSuiteTest, FalseFailRegressionTest, GoldPathRelativeTest, BenchRedactTest -- split from `test_eval_pinning` (task 58).

See `tests.unit.retrieval._eval_pinning_support` for the
shared header.
"""

from tests.unit.retrieval._eval_pinning_support import (  # noqa: F401
    CLI, CLI_ENTRIES, SyntheticKBTestCase, _EXTRA_ENTRY, _HOME,
    _PII_CHECKS, _PII_GIT, _PII_METRICS, _abs_gold_path, _gate_row, copy,
    json, os, shutil, tempfile)


class GoldFixtureSuiteTest(SyntheticKBTestCase):
    """`eval --suite gold-fixture` scores a FROZEN synthetic corpus shipped with
    the package (no operator data), giving a DRIFT-FREE absolute gate: its
    corpus_fingerprint is stable across runs AND independent of any operator KB,
    and re-runs are byte-identical (latency aside)."""

    def setUp(self):
        super().setUp()
        # `--gate` records, which would otherwise re-spawn the unittest suite.
        self._prev = os.environ.get("AGENTWARE_NESTED_UNITTEST")
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"
        self.addCleanup(self._restore_nested)

    def _restore_nested(self):
        if self._prev is None:
            os.environ.pop("AGENTWARE_NESTED_UNITTEST", None)
        else:
            os.environ["AGENTWARE_NESTED_UNITTEST"] = self._prev

    @staticmethod
    def _strip_volatile(payload):
        """Drop the only non-deterministic fields (latency) so two runs of the
        deterministic ranking compare byte-identically (INV-1)."""
        d = copy.deepcopy(payload)
        d.get("metrics", {}).pop("latency_ms_mean", None)
        d.get("metrics", {}).pop("latency_ms_p50", None)
        for q in d.get("per_query", []):
            q.pop("latency_ms", None)
        d.pop("recorded", None)  # run timestamp / commit are volatile
        return d

    def test_loads_and_scores_synthetic_fixture(self):
        entries, gold = CLI.load_gold_fixture(CLI.gold_fixture_path())
        self.assertGreaterEqual(len(entries), 6)
        self.assertGreaterEqual(len(gold), 6)
        result = CLI.evaluate_gold_fixture(
            self.kdir, CLI.gold_fixture_path(), "bm25", 5)
        self.assertEqual(result["suite"], "gold-fixture")
        self.assertTrue(result["determinism_ok"])
        # Lexically unambiguous corpus -> BM25 retrieves every gold doc at k=5.
        self.assertAlmostEqual(result["metrics"]["recall_at_k"], 1.0)
        self.assertTrue(result["corpus_fingerprint"])

    def test_fixture_ships_no_operator_data(self):
        # R-LOC-03: the package fixture must contain ZERO operator data — no
        # absolute HOME path and none of the live operator-identity strings.
        with open(CLI.gold_fixture_path(), encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("/Users/", raw)
        self.assertNotIn("/home/", raw)
        for needle in CLI.operator_identity_strings(self.kdir):
            if needle:
                self.assertNotIn(needle, raw)

    def test_byte_identical_across_runs(self):
        code1, out1, err1 = self.run_cli(
            ["eval", "--suite", "gold-fixture", "--strategy", "bm25",
             "--format", "json"])
        code2, out2, err2 = self.run_cli(
            ["eval", "--suite", "gold-fixture", "--strategy", "bm25",
             "--format", "json"])
        self.assertEqual(code1, 0, err1)
        self.assertEqual(code2, 0, err2)
        self.assertEqual(self._strip_volatile(json.loads(out1)),
                         self._strip_volatile(json.loads(out2)))

    def test_fingerprint_independent_of_operator_kb(self):
        # The DRIFT-FREE property: scoring the frozen fixture against two
        # DIFFERENT operator KBs yields the SAME corpus_fingerprint, so the
        # gold-fixture gate never false-FAILs on live-KB growth.
        try:
            from tests._fixtures import build_synthetic_kb
        except ImportError:
            from _fixtures import build_synthetic_kb
        other_kdir = tempfile.mkdtemp(prefix="agentware-other-kb-")
        self.addCleanup(shutil.rmtree, other_kdir, True)
        grown = copy.deepcopy(CLI_ENTRIES) + [copy.deepcopy(_EXTRA_ENTRY)]
        build_synthetic_kb(other_kdir, grown)
        fp_a = CLI.evaluate_gold_fixture(
            self.kdir, CLI.gold_fixture_path(), "bm25", 5)["corpus_fingerprint"]
        fp_b = CLI.evaluate_gold_fixture(
            other_kdir, CLI.gold_fixture_path(), "bm25", 5)["corpus_fingerprint"]
        self.assertEqual(fp_a, fp_b)

    def test_gate_seeds_then_passes_on_unchanged_code(self):
        argv = ["eval", "--suite", "gold-fixture", "--strategy", "bm25",
                "--gate", "--format", "json"]
        code1, out1, err1 = self.run_cli(argv)
        self.assertEqual(code1, 0, err1)
        g1 = json.loads(out1)["gate"]
        self.assertTrue(g1["seeded"])
        self.assertTrue(g1["passed"])
        self.assertEqual(g1["suite"], "gold-fixture")
        # Second run: same frozen corpus -> comparable baseline -> PASS (no drift).
        code2, out2, err2 = self.run_cli(argv)
        self.assertEqual(code2, 0, err2)
        g2 = json.loads(out2)["gate"]
        self.assertFalse(g2["seeded"])
        self.assertTrue(g2["passed"])

class FalseFailRegressionTest(SyntheticKBTestCase):
    """Task 7: reproduce and CLOSE the original own-gold-drift false-FAIL.

    The recurring bug: a peak row recorded against a SMALLER live KB, then a run
    over a GROWN KB whose absolute own-gold score drifts DOWN — the pre-fix gate
    (comparable = strategy+suite only) compared against the smaller-corpus peak
    and FAILED on byte-identical code. This test reproduces that exact pre-fix
    behavior, shows the corpus-aware gate now SEEDS+passes on the same data, and
    proves a genuine SAME-corpus drop is still caught (regression detection
    intact). The `--ab` clean-HEAD isolation of a real diff-caused drop is proven
    separately by RunABGateTest / ABGateIntegrationTest (Task 5)."""

    @staticmethod
    def _old_baseline(rows, strategy, suite):
        """The PRE-FIX comparability: strategy+suite ONLY (no corpus_fingerprint)
        — exactly the logic that false-failed when the live KB grew."""
        comparable = [r for r in rows
                      if r.get("strategy") == strategy
                      and r.get("suite") == suite]
        if not comparable:
            return None
        base = {}
        for key in CLI.GATE_METRICS:
            vals = [r.get("metrics", {}).get(key) for r in comparable]
            vals = [v for v in vals if isinstance(v, (int, float))]
            if vals:
                base[key] = max(vals)
        return base

    def _grown_fingerprint(self):
        """Real fingerprints over the SMALL (base) and GROWN corpora — proving
        the live KB growth alone changes the fingerprint."""
        fp_small = CLI.eval_corpus_fingerprint(
            CLI.build_corpus(self.kdir, self.index_data))
        try:
            from tests._fixtures import build_synthetic_kb
        except ImportError:
            from _fixtures import build_synthetic_kb
        grown_entries = copy.deepcopy(CLI_ENTRIES) + [copy.deepcopy(_EXTRA_ENTRY)]
        grown_data = build_synthetic_kb(self.kdir, grown_entries)
        fp_grown = CLI.eval_corpus_fingerprint(
            CLI.build_corpus(self.kdir, grown_data))
        return fp_small, fp_grown

    def test_evalpin_old_would_false_fail_new_seeds_and_passes(self):
        fp_small, fp_grown = self._grown_fingerprint()
        # Growing the live KB changes the corpus fingerprint by construction.
        self.assertNotEqual(fp_small, fp_grown)

        # Historical PEAK row recorded against the SMALLER corpus (recall 1.0).
        prior_rows = [_gate_row("bm25", 1.0, 100.0, fp_small)]
        # The grown-corpus run's own-gold score drifts DOWN to 0.80 — pure corpus
        # drift (more distractor docs dilute ranking), NOT a code regression.
        drifted = {"recall_at_k": 0.80, "precision_at_k": 0.2,
                   "ndcg_at_k": 0.80, "mrr": 0.80}

        # OLD behavior (strategy+suite only): the 1.0 peak is "comparable", so
        # 0.80 < 1.0 - 0.02 -> FALSE-FAIL on byte-identical code.
        old_base = self._old_baseline(prior_rows, "bm25", None)
        self.assertIsNotNone(old_base)
        old_pass, old_regs = CLI.evaluate_gate(drifted, 100.0, old_base, 0.02)
        self.assertFalse(old_pass)
        self.assertTrue(any(r[0] == "recall_at_k" for r in old_regs))

        # NEW behavior (corpus-aware): the grown fingerprint != the small one, so
        # there is NO comparable history -> seed + PASS. False-FAIL gone at source.
        new_base = CLI.gate_baseline(prior_rows, "bm25", None, fp_grown)
        self.assertIsNone(new_base)
        new_pass, new_regs = CLI.evaluate_gate(drifted, 100.0, new_base, 0.02)
        self.assertTrue(new_pass)
        self.assertEqual(new_regs, [])

    def test_genuine_same_corpus_drop_still_fails(self):
        # Regression detection intact: a SAME-fingerprint peak is comparable, so a
        # real drop on the unchanged corpus is still caught (exit-worthy FAIL).
        fp = "FP_SAME_CORPUS"
        dropped = {"recall_at_k": 0.80, "precision_at_k": 0.2,
                   "ndcg_at_k": 0.80, "mrr": 0.80}
        base = CLI.gate_baseline([_gate_row("bm25", 0.95, 95.0, fp)],
                                 "bm25", None, fp)
        self.assertIsNotNone(base)
        self.assertAlmostEqual(base["recall_at_k"], 0.95)
        passed, regs = CLI.evaluate_gate(dropped, 95.0, base, 0.02)
        self.assertFalse(passed)
        self.assertTrue(any(r[0] == "recall_at_k" for r in regs))

class GoldPathRelativeTest(SyntheticKBTestCase):
    """A freshly recorded ledger row must store `gold_path` RELATIVE — never an
    absolute HOME / operator-identity path (R-LOC-03). Covers BOTH row families
    (own-gold/eval via `build_ledger_row`, acr-gate via `build_acr_gate_row`) plus
    the end-to-end CLI `eval --record` path so the fix is verified wherever it lands."""

    def setUp(self):
        super().setUp()
        # `--record` would otherwise re-spawn the unittest suite (slow); guard it.
        self._prev = os.environ.get("AGENTWARE_NESTED_UNITTEST")
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"
        self.addCleanup(self._restore)

    def _restore(self):
        if self._prev is None:
            os.environ.pop("AGENTWARE_NESTED_UNITTEST", None)
        else:
            os.environ["AGENTWARE_NESTED_UNITTEST"] = self._prev

    def _assert_relative(self, gold_path):
        self.assertIsNotNone(gold_path)
        self.assertFalse(
            os.path.isabs(gold_path),
            "gold_path leaked an absolute path: %r" % gold_path)
        self.assertNotIn(_HOME, gold_path)
        for needle in CLI.operator_identity_strings(self.kdir):
            if needle:
                self.assertNotIn(needle, gold_path)

    def test_eval_row_gold_path_is_relative(self):
        result = {"strategy": "bm25", "top_k": 5, "num_queries": 3,
                  "gold_path": _abs_gold_path(), "metrics": dict(_PII_METRICS)}
        row = CLI.build_ledger_row(result, _PII_GIT, "2026-06-26T00:00:00Z",
                                   dict(_PII_CHECKS))
        self._assert_relative(row["gold_path"])

    def test_acr_gate_row_gold_path_is_relative(self):
        result = {
            "mode": "ablate", "top_k": 5, "num_queries": 3,
            "gold_path": _abs_gold_path(), "as_of": "2026-06-26",
            "baseline": "bm25", "treatment": "bm25+acr",
            "strategies": {"bm25": dict(_PII_METRICS),
                           "bm25+acr": dict(_PII_METRICS)},
            "delta": {"recall_at_k": 0.0},
        }
        decision = {"passed": True, "baseline": "bm25", "treatment": "bm25+acr",
                    "params": {}, "checks": {}}
        row = CLI.build_acr_gate_row(result, decision, _PII_GIT,
                                     "2026-06-26T00:00:00Z", dict(_PII_CHECKS))
        self._assert_relative(row["gold_path"])

    def test_end_to_end_recorded_row_is_relative(self):
        gold = [{"query": "geofence reminders never fired on iOS",
                 "expected_ids": ["learn-geofence-reminders"]}]
        gpath = os.path.join(self.kdir, "benchmarks", "recall-gold.json")
        os.makedirs(os.path.dirname(gpath), exist_ok=True)
        with open(gpath, "w", encoding="utf-8") as f:
            json.dump(gold, f)
        code, out, err = self.run_cli(
            ["eval", "--record", "--strategy", "bm25", "--format", "json"])
        self.assertEqual(code, 0, err)
        ledger = os.path.join(self.kdir, "benchmarks", "history.jsonl")
        with open(ledger, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        self.assertTrue(rows)
        self._assert_relative(rows[-1]["gold_path"])

class BenchRedactTest(SyntheticKBTestCase):
    """`bench redact` is the ONLY sanctioned mutation of the append-only ledger
    (`:3556` exception): it scrubs ONLY the leaking `gold_path` field, preserving
    every metric value AND row order byte-for-byte. RED until Task 10 adds the
    subcommand."""

    def _leaky_ledger(self):
        ledger = os.path.join(self.kdir, "benchmarks", "history.jsonl")
        os.makedirs(os.path.dirname(ledger), exist_ok=True)
        rows = [
            {"schema": 1, "run": "2026-06-01T00:00:00Z", "commit": "aaa",
             "mode": "eval", "strategy": "bm25", "suite": None, "top_k": 5,
             "num_queries": 3, "gold_path": _abs_gold_path(),
             "corpus_fingerprint": "FP1",
             "metrics": {"recall_at_k": 0.91, "mrr": 0.70},
             "reliability": 90.0},
            {"schema": 1, "run": "2026-06-02T00:00:00Z", "commit": "bbb",
             "mode": "acr-gate", "strategy": "bm25+acr", "top_k": 5,
             "num_queries": 3, "gold_path": _abs_gold_path(),
             "corpus_fingerprint": "FP2",
             "metrics": {"recall_at_k": 0.88, "mrr": 0.60},
             "reliability": 88.0},
        ]
        with open(ledger, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        return ledger, rows

    def _read(self, ledger):
        with open(ledger, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_redact_scrubs_gold_path_preserving_metrics_and_order(self):
        ledger, original = self._leaky_ledger()
        code, out, err = self.run_cli(["bench", "redact"])
        self.assertEqual(code, 0, err)
        scrubbed = self._read(ledger)
        self.assertEqual(len(scrubbed), len(original))
        for orig, new in zip(original, scrubbed):
            # Row order + every non-path field preserved byte-for-byte.
            self.assertEqual(new["run"], orig["run"])
            self.assertEqual(new["commit"], orig["commit"])
            self.assertEqual(new["metrics"], orig["metrics"])
            self.assertEqual(new["reliability"], orig["reliability"])
            self.assertEqual(new["corpus_fingerprint"],
                             orig["corpus_fingerprint"])
            # ONLY gold_path changed: now relative + PII-free.
            self.assertFalse(os.path.isabs(new["gold_path"]),
                             "gold_path still absolute: %r" % new["gold_path"])
            self.assertNotIn(_HOME, new["gold_path"])

    def test_scan_personal_data_clean_after_redact(self):
        ledger, _ = self._leaky_ledger()
        # Precondition: the operator HOME leak IS present in the committed ledger.
        self.assertTrue(CLI.scan_personal_data(self.kdir, [_HOME]))
        code, out, err = self.run_cli(["bench", "redact"])
        self.assertEqual(code, 0, err)
        self.assertEqual(CLI.scan_personal_data(self.kdir, [_HOME]), [])
