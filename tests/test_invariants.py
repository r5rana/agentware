"""Moat-protection guard tests (Phase 0.2).

These tests pin the NON-NEGOTIABLE design invariants that make agentware's
retrieval/measurement spine a competitive moat. If any of these fail, a change
has eroded something agentware is supposed to be great at — treat it as a bug in
the change, not in the test.

  INV-1  Deterministic ranking — `recall`/`eval` are hand-rolled BM25, stdlib
         only, NO network / NO LLM / NO embeddings / NO wall-clock in scoring;
         identical inputs -> byte-identical output across runs.
  INV-2  Read-only retrieval — `recall`/`eval`/`metrics` NEVER mutate index.json
         or any KB file.
  INV-3  System of record unchanged — the git-versioned markdown + index.json
         stay authoritative (asserted via the byte-snapshot in INV-2).
  INV-5  Anti-context-rot — `recall` is token-budgeted and REPLACES the
         whole-corpus dump with a focused, strictly-smaller set.
  INV-6  Zero hard dependencies — `scripts/agentware` imports stdlib modules only.

Stdlib `unittest` only (no pytest, no new deps). Never touches the real KB.
"""

import ast
import json
import os
import sys
import unittest

try:
    from tests._fixtures import (SyntheticKBTestCase, load_cli, CLI_PATH,
                                 run_cli, build_synthetic_kb)
except ImportError:  # allow `python3 -m unittest tests.test_invariants`
    from _fixtures import (SyntheticKBTestCase, load_cli, CLI_PATH,
                           run_cli, build_synthetic_kb)


CLI = load_cli()

# Modules whose presence in the CLI source would mean the retrieval/measurement
# path could reach the network or shell out to a model service (INV-1/INV-6).
_NETWORK_MODULES = frozenset({
    "socket", "ssl", "urllib", "http", "ftplib", "telnetlib", "smtplib",
    "asyncio", "requests", "httpx", "aiohttp", "websocket", "websockets",
})


def _imported_top_modules(source_path):
    """Return the set of top-level module names imported by a python source file.

    Pure-stdlib AST walk (no import side effects): collects the first dotted
    component of every `import x.y` / `from x.y import z` statement.
    """
    with open(source_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=source_path)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:  # ignore relative imports
                mods.add(node.module.split(".")[0])
    return mods


class StdlibOnlyTest(unittest.TestCase):
    """INV-6 + INV-1: the toolkit imports stdlib modules only and reaches no network."""

    def test_cli_imports_are_stdlib_only(self):
        # sys.stdlib_module_names is the authoritative stdlib set (py3.10+).
        stdlib = set(sys.stdlib_module_names)
        builtin = set(sys.builtin_module_names)
        offenders = sorted(
            m for m in _imported_top_modules(CLI_PATH)
            if m not in stdlib and m not in builtin)
        self.assertEqual(
            offenders, [],
            "scripts/agentware imports non-stdlib modules: %s (INV-6 violated)"
            % offenders)

    def test_cli_imports_no_network_modules(self):
        used = _imported_top_modules(CLI_PATH) & _NETWORK_MODULES
        self.assertEqual(
            sorted(used), [],
            "scripts/agentware imports network-capable modules: %s "
            "(INV-1: ranking must never touch the network)" % sorted(used))


class DeterministicRankingTest(SyntheticKBTestCase):
    """INV-1: identical inputs -> byte-identical recall/eval output across runs."""

    def _gold_path(self):
        """Write a small synthetic gold set and return its path."""
        bench = os.path.join(self.kdir, "benchmarks")
        os.makedirs(bench, exist_ok=True)
        gold = [
            {"query": "geofence arrive reminders",
             "expected_ids": ["learn-geofence-reminders"]},
            {"query": "gtimeout coreutils command",
             "expected_ids": ["learn-macos-no-timeout"]},
            {"query": "saturation inverse document frequency",
             "expected_ids": ["ref-bm25-ranking"]},
            {"query": "python stdlib runtime dependency",
             "expected_ids": ["config-python-runtime"]},
        ]
        path = os.path.join(bench, "recall-gold.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(gold, f)
        return path

    def test_recall_json_is_byte_identical_across_runs(self):
        a = self.run_cli(["recall", "geofence arrive reminders", "--format", "json"])
        b = self.run_cli(["recall", "geofence arrive reminders", "--format", "json"])
        self.assertEqual(a[0], 0, a[2])
        self.assertEqual(a[1], b[1])  # byte-identical stdout

    def test_eval_ranking_metrics_are_deterministic_across_runs(self):
        # INV-1 forbids wall-clock in SCORING, not in REPORTED latency: eval's
        # latency_ms_* fields are timed and legitimately vary run-to-run. Every
        # ranking-derived metric (recall/precision/ndcg/mrr/ctx-tokens) must be
        # byte-identical, so strip only the two latency fields before comparing.
        gp = self._gold_path()
        a = self.run_cli(["eval", "--gold", gp, "--strategy", "bm25",
                          "--format", "json"])
        b = self.run_cli(["eval", "--gold", gp, "--strategy", "bm25",
                          "--format", "json"])
        self.assertEqual(a[0], 0, a[2])

        def _strip_latency(obj):
            # Drop every timed field (latency is the ONLY non-deterministic part
            # of an eval result) anywhere in the payload, including per_query rows.
            if isinstance(obj, dict):
                return {k: _strip_latency(v) for k, v in obj.items()
                        if "latency" not in k}
            if isinstance(obj, list):
                return [_strip_latency(v) for v in obj]
            return obj

        self.assertEqual(_strip_latency(json.loads(a[1])),
                         _strip_latency(json.loads(b[1])))

    def test_recall_makes_no_subprocess_call(self):
        # No model service / shell-out is reachable from the ranking path: patch
        # subprocess.run to detonate and confirm recall still succeeds.
        orig = CLI.subprocess.run

        def _boom(*a, **k):
            raise AssertionError("recall must not shell out (INV-1)")

        CLI.subprocess.run = _boom
        try:
            code, out, err = self.run_cli(
                ["recall", "geofence arrive reminders", "--format", "json"])
        finally:
            CLI.subprocess.run = orig
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["results"])


class ReadOnlyRetrievalTest(SyntheticKBTestCase):
    """INV-2/INV-3: recall / eval / metrics never mutate index.json or KB files."""

    def _index_bytes(self):
        with open(os.path.join(self.kdir, "index.json"), "rb") as f:
            return f.read()

    def _snapshot_tree(self):
        """Map of every KB file path -> bytes, for an exact before/after diff."""
        snap = {}
        for root, _dirs, files in os.walk(self.kdir):
            for name in files:
                p = os.path.join(root, name)
                with open(p, "rb") as f:
                    snap[p] = f.read()
        return snap

    def _gold_path(self):
        bench = os.path.join(self.kdir, "benchmarks")
        os.makedirs(bench, exist_ok=True)
        gold = [{"query": "geofence arrive reminders",
                 "expected_ids": ["learn-geofence-reminders"]},
                {"query": "python stdlib runtime",
                 "expected_ids": ["config-python-runtime"]}]
        path = os.path.join(bench, "recall-gold.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(gold, f)
        return path

    def test_recall_leaves_index_unchanged(self):
        before = self._index_bytes()
        code, _out, err = self.run_cli(
            ["recall", "python ranking stdlib", "--format", "json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(before, self._index_bytes())

    def test_recall_leaves_entire_kb_tree_unchanged(self):
        before = self._snapshot_tree()
        self.run_cli(["recall", "geofence reminders", "--format", "json"])
        self.run_cli(["recall", "macos timeout", "--format", "text"])
        self.assertEqual(before, self._snapshot_tree())

    def test_eval_leaves_index_unchanged(self):
        gp = self._gold_path()
        before = self._index_bytes()
        # Plain eval (no --record/--gate) must be read-only.
        code, _out, err = self.run_cli(
            ["eval", "--gold", gp, "--strategy", "bm25", "--format", "json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(before, self._index_bytes())

    def test_eval_ablate_leaves_index_unchanged(self):
        gp = self._gold_path()
        before = self._index_bytes()
        code, _out, err = self.run_cli(
            ["eval", "--gold", gp, "--ablate", "--format", "json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(before, self._index_bytes())

    @unittest.skipUnless(hasattr(CLI, "cmd_metrics"),
                         "metrics command not implemented yet (Phase 4.1)")
    def test_metrics_leaves_index_unchanged(self):
        before = self._index_bytes()
        # metrics parses logs/sessions only; it must never touch index.json.
        code, _out, _err = self.run_cli(["metrics", "--format", "json"])
        # Exit code may be non-zero if no logs exist; the invariant is no mutation.
        self.assertEqual(before, self._index_bytes())
        self.assertIn(code, (0, 1))


class TokenBudgetInvariantTest(SyntheticKBTestCase):
    """INV-5: recall is token-budgeted and reduces injected context vs a full dump."""

    def _full_corpus_footprint(self):
        """Estimated tokens to inject the WHOLE corpus (the dump recall replaces)."""
        corpus = CLI.build_corpus(self.kdir, self.index_data)
        return sum(CLI.estimate_tokens(text) for _e, text in corpus)

    def test_recall_never_exceeds_token_budget(self):
        for budget in (0, 30, 80, 250, 1500):
            payload = json.loads(self.run_cli(
                ["recall", "python ranking stdlib retrieval geofence",
                 "--token-budget", str(budget), "--top-k", "10",
                 "--format", "json"])[1])
            self.assertLessEqual(
                payload["context_tokens"], budget,
                "budget %d exceeded: %d tokens" % (budget, payload["context_tokens"]))
            self.assertEqual(
                payload["context_tokens"],
                sum(r["estimated_tokens"] for r in payload["results"]))

    def test_recall_footprint_smaller_than_full_corpus_dump(self):
        # The whole-corpus dump is the baseline recall is meant to REPLACE
        # (analog of injecting all of MAIN.md every session). A focused recall
        # must inject strictly fewer tokens than dumping everything.
        full = self._full_corpus_footprint()
        payload = json.loads(self.run_cli(
            ["recall", "geofence reminders", "--top-k", "1",
             "--token-budget", "1500", "--format", "json"])[1])
        self.assertTrue(payload["results"])
        self.assertLess(payload["context_tokens"], full)

    def test_budget_zero_injects_no_context(self):
        payload = json.loads(self.run_cli(
            ["recall", "geofence reminders", "--token-budget", "0",
             "--format", "json"])[1])
        self.assertEqual(payload["context_tokens"], 0)
        self.assertEqual(payload["results"], [])


if __name__ == "__main__":
    unittest.main()
