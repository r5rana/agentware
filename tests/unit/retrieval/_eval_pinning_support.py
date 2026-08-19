"""Shared header for the `test_eval_pinning` suite, split out by task 58.

Holds every import, helper, constant and base class the split
parts share, so no part duplicates a definition and none can
drift from another.
"""

import ast
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
except ImportError:  # allow `python3 -m unittest tests.unit.retrieval.test_eval_pinning`
    from _fixtures import (SyntheticKBTestCase, load_cli, CLI_PATH,
                           LEGACY_PATH)
CLI = load_cli()
from agentware_lib.retrieval import regressiongate as RG  # noqa: E402
from agentware_lib.retrieval.regressiongate import abgate as AB  # noqa: E402
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
class _ABArgs:
    """Minimal args stand-in for run_ab_gate (mirrors the eval argparse defaults)."""

    def __init__(self, tolerance=0.02, top_k=5, strategy="bm25"):
        self.tolerance = tolerance
        self.top_k = top_k
        self.strategy = strategy
        self.ab = True
try:
    from tests._fixtures import _ENTRIES as CLI_ENTRIES
except ImportError:
    from _fixtures import _ENTRIES as CLI_ENTRIES
_HOME = os.path.expanduser("~")
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
