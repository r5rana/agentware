"""Tests for eval corpus pinning + benchmark PII hygiene (260626-eval-corpus-pinning).

The own-gold-set eval gate keeps false-FAILing because it scores against the
LIVE operator KB, which GROWS over time: the absolute own-gold score drifts DOWN
versus a peak row recorded against a SMALLER corpus, so the gate fails with
byte-identical code. The fix makes the gate REPRODUCIBLE:

  1. A deterministic whole-corpus fingerprint (`eval_corpus_fingerprint`) recorded
     on every ledger row.
  2. `gate_baseline` comparability extended from (strategy, suite) to
     (strategy, suite, corpus_fingerprint) so a changed live KB seeds a fresh
     baseline + PASSES instead of comparing apples to oranges.

This module also covers benchmark PII hygiene (relative `gold_path`, `bench
redact`) — added in the PII task block lower down.

Stdlib `unittest` only. Never touches the real KB.
"""

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import (SyntheticKBTestCase, load_cli, CLI_PATH,
                                 LEGACY_PATH)
except ImportError:  # allow `python3 -m unittest tests.test_eval_pinning`
    from _fixtures import (SyntheticKBTestCase, load_cli, CLI_PATH,
                           LEGACY_PATH)


CLI = load_cli()

# load_cli() puts scripts/ on sys.path, so the OWNING module of the A/B HEAD
# extraction is importable by name. Addressing the owner directly (rather than
# only through the facade) is what lets the negative test patch the extraction
# the way the real caller resolves it.
from agentware_lib.retrieval import regressiongate as RG  # noqa: E402
# The aggregator package re-export is a NAME COPY: patching RG.<name> rebinds a
# name the A/B code never reads, because _extract_head_cli resolves its
# collaborators in the OWNING submodule's globals. Patch targets address `AB`.
from agentware_lib.retrieval.regressiongate import abgate as AB  # noqa: E402


# A distinct extra entry to GROW the synthetic corpus (mirrors a live KB that
# accumulates learnings over time). Distinctive vocabulary so it is unambiguous.
_EXTRA_ENTRY = {
    "id": "learn-corpus-growth",
    "title": "Corpus Growth Drift",
    "category": "learnings",
    "path": "learnings/corpus-growth.md",
    "tags": ["corpus", "drift", "growth"],
    "created": "2026-02-01",
    "summary": "An extra learning that grows the live corpus over time.",
    "body": "# Corpus Growth Drift\n\nAn extra entry that enlarges the corpus.\n",
}


def _gate_row(strategy, recall, reliability, fingerprint, suite=None):
    """A minimal ledger row carrying a corpus_fingerprint for gate comparability."""
    metrics = {"recall_at_k": recall, "precision_at_k": 0.2,
               "ndcg_at_k": recall, "mrr": recall}
    return {"strategy": strategy, "suite": suite, "metrics": metrics,
            "reliability": reliability, "corpus_fingerprint": fingerprint}


class CorpusFingerprintTest(SyntheticKBTestCase):
    """`eval_corpus_fingerprint` is a deterministic, canonical-order hash of the
    (id, text) corpus actually scored."""

    def _corpus(self, data=None):
        return CLI.build_corpus(self.kdir, data or self.index_data)

    def test_same_corpus_hashes_identically(self):
        fp1 = CLI.eval_corpus_fingerprint(self._corpus())
        fp2 = CLI.eval_corpus_fingerprint(self._corpus())
        self.assertEqual(fp1, fp2)
        self.assertIsInstance(fp1, str)
        self.assertTrue(fp1)

    def test_changed_corpus_hashes_differently(self):
        base_fp = CLI.eval_corpus_fingerprint(self._corpus())
        # Grow the corpus with one extra entry -> a different fingerprint. Build
        # a grown index materialized on disk so the new entry's body resolves.
        try:
            from tests._fixtures import build_synthetic_kb
        except ImportError:
            from _fixtures import build_synthetic_kb
        grown_entries = copy.deepcopy(CLI_ENTRIES) + [copy.deepcopy(_EXTRA_ENTRY)]
        grown_data = build_synthetic_kb(self.kdir, grown_entries)
        grown_fp = CLI.eval_corpus_fingerprint(
            CLI.build_corpus(self.kdir, grown_data))
        self.assertNotEqual(base_fp, grown_fp)

    def test_fingerprint_is_order_canonical(self):
        # Reordering the corpus rows must NOT change the fingerprint: the hash is
        # over the canonical (sorted-by-id) corpus so byte-identical corpora hash
        # identically across runs/machines regardless of index order.
        corpus = self._corpus()
        reordered = list(reversed(corpus))
        self.assertEqual(
            CLI.eval_corpus_fingerprint(corpus),
            CLI.eval_corpus_fingerprint(reordered))


class GateBaselineCorpusAwareTest(SyntheticKBTestCase):
    """`gate_baseline` only compares rows sharing the same corpus_fingerprint."""

    def test_excludes_rows_with_different_fingerprint(self):
        rows = [_gate_row("bm25", 0.99, 99.0, "FP_OTHER"),
                _gate_row("bm25", 0.50, 60.0, "FP_WANT")]
        base = CLI.gate_baseline(rows, "bm25", suite=None,
                                 corpus_fingerprint="FP_WANT")
        # Only the FP_WANT row is comparable -> its recall, not the 0.99 peak.
        self.assertAlmostEqual(base["recall_at_k"], 0.50)

    def test_legacy_rows_without_fingerprint_not_comparable(self):
        # A pre-fingerprint row (no corpus_fingerprint) forms its own bucket and
        # must NOT gate a new fingerprinted run.
        legacy = {"strategy": "bm25", "suite": None,
                  "metrics": {"recall_at_k": 0.99}, "reliability": 99.0}
        base = CLI.gate_baseline([legacy], "bm25", suite=None,
                                 corpus_fingerprint="FP_NEW")
        self.assertIsNone(base)

    def test_grown_corpus_seeds_instead_of_failing(self):
        # Peak row recorded against a SMALL corpus; a run against a GROWN corpus
        # has a different fingerprint -> no comparable history -> seed + pass
        # (None), instead of false-failing against the smaller-corpus peak.
        rows = [_gate_row("bm25", 1.0, 100.0, "FP_SMALL")]
        base = CLI.gate_baseline(rows, "bm25", suite=None,
                                 corpus_fingerprint="FP_GROWN")
        self.assertIsNone(base)

    def test_same_corpus_drop_still_detected(self):
        # Regression detection intact: same fingerprint -> the peak IS the
        # baseline, so a later same-corpus drop is still caught.
        rows = [_gate_row("bm25", 0.95, 95.0, "FP_SAME")]
        base = CLI.gate_baseline(rows, "bm25", suite=None,
                                 corpus_fingerprint="FP_SAME")
        self.assertIsNotNone(base)
        self.assertAlmostEqual(base["recall_at_k"], 0.95)


class _ABArgs:
    """Minimal args stand-in for run_ab_gate (mirrors the eval argparse defaults)."""

    def __init__(self, tolerance=0.02, top_k=5, strategy="bm25"):
        self.tolerance = tolerance
        self.top_k = top_k
        self.strategy = strategy
        self.ab = True


class ABVerdictTest(unittest.TestCase):
    """`_ab_verdict` is the pure clean-HEAD vs working-tree comparison:
    PASS iff working >= head - tolerance."""

    def test_equal_passes_delta_zero(self):
        passed, delta = CLI._ab_verdict(0.80, 0.80, 0.02)
        self.assertTrue(passed)
        self.assertAlmostEqual(delta, 0.0)

    def test_within_tolerance_passes(self):
        # working is slightly worse than head but within tolerance -> PASS.
        passed, delta = CLI._ab_verdict(0.79, 0.80, 0.02)
        self.assertTrue(passed)
        self.assertAlmostEqual(delta, -0.01)

    def test_drop_beyond_tolerance_fails(self):
        # working is materially worse than head -> the diff regressed recall.
        passed, delta = CLI._ab_verdict(0.70, 0.80, 0.02)
        self.assertFalse(passed)
        self.assertAlmostEqual(delta, -0.10)

    def test_improvement_passes(self):
        passed, delta = CLI._ab_verdict(0.90, 0.80, 0.02)
        self.assertTrue(passed)
        self.assertAlmostEqual(delta, 0.10)


class RunABGateTest(unittest.TestCase):
    """`run_ab_gate` assembles the verdict over a mocked HEAD recall, isolating
    the comparison logic from the (git-dependent) HEAD extraction."""

    def setUp(self):
        self._orig_extract = CLI._extract_head_cli
        self._orig_head = CLI._ab_head_recall
        self.addCleanup(self._restore)

    def _restore(self):
        CLI._extract_head_cli = self._orig_extract
        CLI._ab_head_recall = self._orig_head

    @staticmethod
    def _result(recall):
        return {"metrics": {"recall_at_k": recall}, "strategy": "bm25",
                "top_k": 5, "as_of": "2026-06-26"}

    def test_passes_when_working_matches_head(self):
        # No-op code change: HEAD scores identically -> delta 0 -> PASS.
        CLI._extract_head_cli = lambda: ("/nonexistent/head.py", "abc1234")
        CLI._ab_head_recall = lambda *a, **k: 0.80
        result = self._result(0.80)
        ab = CLI.run_ab_gate(_ABArgs(), "/tmp/kdir", result, "/tmp/gold.json")
        self.assertTrue(ab["available"])
        self.assertTrue(ab["passed"])
        self.assertAlmostEqual(ab["delta"], 0.0)
        self.assertEqual(ab["head_commit"], "abc1234")
        self.assertIs(result["ab"], ab)

    def test_fails_when_working_below_head(self):
        # A recall-lowering working-tree edit: working < head - tol -> FAIL.
        CLI._extract_head_cli = lambda: ("/nonexistent/head.py", "abc1234")
        CLI._ab_head_recall = lambda *a, **k: 0.90
        ab = CLI.run_ab_gate(_ABArgs(), "/tmp/kdir", self._result(0.70),
                             "/tmp/gold.json")
        self.assertTrue(ab["available"])
        self.assertFalse(ab["passed"])
        self.assertAlmostEqual(ab["delta"], -0.20)

    def test_unavailable_head_passes(self):
        # No git/HEAD -> cannot prove a regression -> PASS with available False.
        CLI._extract_head_cli = lambda: (None, None)
        ab = CLI.run_ab_gate(_ABArgs(), "/tmp/kdir", self._result(0.50),
                             "/tmp/gold.json")
        self.assertFalse(ab["available"])
        self.assertTrue(ab["passed"])


class ABGateIntegrationTest(SyntheticKBTestCase):
    """End-to-end `eval --ab` over a real temp git repo: the clean HEAD is the
    committed CLI, the working tree is the in-process CLI. Same code + same corpus
    -> delta 0 -> PASS, proving the read-only HEAD extraction + subprocess plumbing
    works (and never mutates the work tree)."""

    def _git(self, repo, *a):
        env = dict(os.environ)
        env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        return subprocess.run(["git", *a], cwd=repo, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def _write_gold(self):
        gold = [
            {"query": "geofence reminders never fired on iOS",
             "expected_ids": ["learn-geofence-reminders"]},
            {"query": "macOS ships no timeout command",
             "expected_ids": ["learn-macos-no-timeout"]},
        ]
        path = os.path.join(self.kdir, "benchmarks", "recall-gold.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(gold, f)
        return path

    def _scratch_repo(self):
        """A temp git repo whose HEAD carries the WHOLE scripts/ package.

        Copying only the entrypoint was correct while the entrypoint WAS the
        implementation. Under the strangler layout it is a ~100-line facade with
        no agentware_lib and no agentware_legacy behind it, so the HEAD arm can
        no longer start — which is the defect task 26 closes, not something to
        assert around.
        """
        repo = tempfile.mkdtemp(prefix="agentware-ab-repo-")
        self.addCleanup(shutil.rmtree, repo, True)
        shutil.copytree(os.path.dirname(CLI_PATH), os.path.join(repo, "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc",
                                                      ".DS_Store"))
        if self._git(repo, "init", "-q").returncode != 0:
            self.skipTest("git init failed")
        self._git(repo, "add", "-A")
        if self._git(repo, "commit", "-q", "-m", "base").returncode != 0:
            self.skipTest("git commit failed")
        orig_root = CLI.REPO_ROOT
        CLI.REPO_ROOT = repo
        self.addCleanup(setattr, CLI, "REPO_ROOT", orig_root)
        return repo

    def test_noop_ab_passes_delta_zero(self):
        if shutil.which("git") is None:
            self.skipTest("git not available")
        repo = self._scratch_repo()
        self._write_gold()
        code, out, err = self.run_cli(
            ["eval", "--strategy", "bm25", "--ab", "--format", "json"])
        self.assertEqual(code, 0, err)
        ab = json.loads(out)["ab"]
        self.assertTrue(ab["available"], ab)
        self.assertTrue(ab["passed"], ab)
        self.assertAlmostEqual(ab["delta"], 0.0)
        # Read-only: the HEAD extraction must not have dirtied the temp work tree.
        status = self._git(repo, "status", "--porcelain")
        self.assertEqual(status.stdout.strip(), "")

    def test_head_arm_materialises_the_whole_package(self):
        """The extraction must land every path git lists at HEAD, not one blob.

        This is the positive half of the task-26 repair: a single-blob extraction
        would satisfy `os.path.isfile(entry)` and still be missing the package.
        """
        if shutil.which("git") is None:
            self.skipTest("git not available")
        repo = self._scratch_repo()
        listing = RG._head_scripts_listing(repo)
        self.assertTrue(any(p.startswith("scripts/agentware_lib/") for p in listing),
                        "scratch HEAD carries no lib modules to extract")
        head_path, head_commit = RG._extract_head_cli(repo_root=repo)
        self.addCleanup(RG._cleanup_head_cli, head_path)
        self.assertTrue(head_commit)
        tmpdir = os.path.dirname(os.path.dirname(head_path))
        missing = [p for p in listing
                   if not os.path.exists(os.path.join(tmpdir, p))]
        self.assertEqual(missing, [], "HEAD extraction dropped %d path(s)"
                         % len(missing))
        # The marker the split package's REPO_ROOT walk needs.
        self.assertTrue(os.path.exists(os.path.join(tmpdir, "AGENTS.md")))

    def test_missing_lib_module_reddens_with_named_error(self):
        """Dropping ONE lib module from the extraction must FAIL, by name.

        The pre-task-26 failure mode is the whole point: the HEAD subprocess dies
        on the missing import, `_ab_head_recall` returns None, and the arm
        reports `available: False, passed: True` — indistinguishable from "no git
        here" and therefore a green gate over an arm that never ran.
        """
        if shutil.which("git") is None:
            self.skipTest("git not available")
        repo = self._scratch_repo()
        # Derived, never hardcoded: a module PATH is exactly the thing this
        # migration keeps moving, and a gate keyed to a literal path silently
        # changes subject the day the file moves.
        victim = sorted(p for p in RG._head_scripts_listing(repo)
                        if p.startswith("scripts/agentware_lib/")
                        and p.endswith(".py"))[0]

        real = AB._extract_head_scripts

        def _lossy(tmpdir, repo_root=None):
            ok = real(tmpdir, repo_root)
            try:
                os.unlink(os.path.join(tmpdir, victim))
            except OSError:
                pass
            return ok

        AB._extract_head_scripts = _lossy
        self.addCleanup(setattr, AB, "_extract_head_scripts", real)

        with self.assertRaises(CLI.HeadExtractionError) as caught:
            AB._extract_head_cli(repo_root=repo)
        self.assertIn(victim, caught.exception.missing)
        self.assertIn(victim, str(caught.exception))

        # ...and the gate that consumes it goes RED, naming the module.
        result = {"metrics": {"recall_at_k": 0.8}, "strategy": "bm25",
                  "top_k": 5, "as_of": "2026-06-26"}
        ab = CLI.run_ab_gate(_ABArgs(), self.kdir, result, "/tmp/gold.json")
        self.assertFalse(ab["passed"], ab)
        self.assertIn(victim, ab["error"])
        self.assertIn(victim, ab["missing"])
        self.assertEqual(CLI._gate_exit(None, ab), 1)


class HeadExtractionCompletenessTest(unittest.TestCase):
    """`_verify_head_extraction` is pure over (tmpdir, listing) — no git, no
    subprocess — so the completeness contract is testable without a repo."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="agentware-verify-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

    def _touch(self, rel):
        dest = os.path.join(self.tmpdir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write("x")

    def test_complete_extraction_verifies(self):
        listing = ["scripts/agentware", "scripts/agentware_lib/cli.py"]
        for rel in listing:
            self._touch(rel)
        self.assertTrue(RG._verify_head_extraction(self.tmpdir, listing))

    def test_missing_path_raises_naming_it(self):
        listing = ["scripts/agentware", "scripts/agentware_lib/cli.py"]
        self._touch("scripts/agentware")
        with self.assertRaises(RG.HeadExtractionError) as caught:
            RG._verify_head_extraction(self.tmpdir, listing)
        self.assertEqual(caught.exception.missing,
                         ["scripts/agentware_lib/cli.py"])
        self.assertIn("scripts/agentware_lib/cli.py", str(caught.exception))

    def test_empty_listing_is_vacuously_complete(self):
        # An ABSENT tree is not a BROKEN one: _extract_head_cli returns
        # (None, None) for that case and the gate keeps passing on it.
        self.assertTrue(RG._verify_head_extraction(self.tmpdir, []))


class ABInstallSeamTest(unittest.TestCase):
    """The facade's post-exec wiring must be a WRITE seam, not a photograph.

    Task 25's recorded lesson: an indirection that satisfies `hasattr` but does
    not carry a monkeypatch through is exactly how a gate reports full coverage
    while measuring nothing."""

    def test_facade_ab_family_is_the_lib_one(self):
        self.assertIs(CLI._verify_head_extraction, RG._verify_head_extraction)
        self.assertIs(CLI.HeadExtractionError, RG.HeadExtractionError)
        self.assertIs(CLI._cleanup_head_cli, RG._cleanup_head_cli)

    def test_installed_run_ab_gate_honours_a_monkeypatch(self):
        orig_extract, orig_head = CLI._extract_head_cli, CLI._ab_head_recall
        self.addCleanup(setattr, CLI, "_extract_head_cli", orig_extract)
        self.addCleanup(setattr, CLI, "_ab_head_recall", orig_head)
        CLI._extract_head_cli = lambda: ("/nonexistent/head.py", "deadbee")
        CLI._ab_head_recall = lambda *a, **k: 0.42
        result = {"metrics": {"recall_at_k": 0.42}, "strategy": "bm25",
                  "top_k": 5, "as_of": "2026-06-26"}
        ab = CLI.run_ab_gate(_ABArgs(), "/tmp/kdir", result, "/tmp/gold.json")
        self.assertEqual(ab["head_commit"], "deadbee")
        self.assertTrue(ab["available"], ab)
        self.assertAlmostEqual(ab["delta"], 0.0)

    def test_dispatch_tail_lives_in_the_facade_not_the_exec(self):
        """The guard must sit AFTER the wiring, or the wiring is tests-only.

        Under the real CLI `__name__ == "__main__"`, so a guard left at the end
        of the exec'd legacy body would `sys.exit(main())` before any post-exec
        statement ran — while `load_cli()`'s non-`__main__` load would run them
        all. Green tests, unwired production.
        """
        with open(CLI_PATH, encoding="utf-8") as f:
            facade = f.read()
        install_at = facade.index("install_ab_extraction(globals())")
        guard_at = facade.rindex('if __name__ == "__main__":')
        self.assertLess(install_at, guard_at)
        with open(LEGACY_PATH, encoding="utf-8") as f:
            legacy = f.read()
        # The facade must still be splitting off a tail that actually exists.
        self.assertIn('\nif __name__ == "__main__":\n    sys.exit(main())\n',
                      legacy)


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


# Expose the fixture's canonical entry list for corpus-growth tests.
try:
    from tests._fixtures import _ENTRIES as CLI_ENTRIES
except ImportError:
    from _fixtures import _ENTRIES as CLI_ENTRIES


# ---------------------------------------------------------------------------
# Task 8 — benchmark PII hygiene (R-LOC-03).  RED until Tasks 9-10 land:
#   9  source fix: ledger-row builders store gold_path RELATIVE (no HOME leak).
#  10  `bench redact`: the ONLY sanctioned mutation of the append-only ledger —
#      scrubs ONLY the gold_path field, every metric value + row order preserved.
# The committed benchmarks/history.jsonl is public+reproducible; it must be
# PII-free, so a recorded row's gold_path must never carry the operator HOME.
# ---------------------------------------------------------------------------

_HOME = os.path.expanduser("~")

# Volatile-state stand-ins for the PURE row builders (mirrors test_ledger.py).
_PII_METRICS = {
    "recall_at_k": 0.90, "precision_at_k": 0.20, "ndcg_at_k": 0.85,
    "mrr": 0.80, "latency_ms_mean": 1.23, "latency_ms_p50": 1.10,
    "context_tokens_mean": 512.0,
}
_PII_GIT = {"commit": "abc1234", "subject": "s", "committed": "", "dirty": False}
_PII_CHECKS = {"test_pass_rate": 1.0, "tests_ran": 1, "tests_failed": 0,
               "index_validate_ok": True, "determinism_ok": True}


def _abs_gold_path():
    """An ABSOLUTE gold path under the operator HOME — exactly the leak shape the
    committed ledger carries today (e.g. /Users/<op>/…/benchmarks/recall-gold.json)."""
    return os.path.join(_HOME, "workspace", "agentware-knowledge",
                        "benchmarks", "recall-gold.json")


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
