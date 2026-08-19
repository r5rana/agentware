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

from tests.unit.retrieval._eval_pinning_support import (  # noqa: F401
    AB, CLI, CLI_ENTRIES, CLI_PATH, RG, SyntheticKBTestCase, _ABArgs,
    _EXTRA_ENTRY, _gate_row, copy, json, os, shutil, subprocess, tempfile,
    unittest)


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
