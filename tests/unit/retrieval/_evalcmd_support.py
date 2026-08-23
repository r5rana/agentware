"""Shared header for the `agentware_lib.retrieval.evalcmd` owning suite.

Split out at task 99 wave 10 for the reason `tests/README.md` names: the owning
file hit the corpus's 600-line cap, and the seam is the MODULE'S OWN — the two
benchmark SUITES (a foreign corpus, their own except-chains) on one side and the
OWN-GOLD path (the ablation instrument, the dispatcher, its two arms, the ACR
printer) on the other. `test_evalcmd.py` still exists and still owns the module,
per R-MOD-06; `test_evalcmd_owngold.py` is its second half.

Holds only fixtures and the shared TestCase base: no assertions live here, so
this file can never become the place a behaviour is asserted and nobody looks.
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

import agentware_lib.retrieval.evalcmd as evalcmd  # noqa: E402


class _Args(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


@contextlib.contextmanager
def _captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def _metrics(recall=0.5):
    return {"recall_at_k": recall, "precision_at_k": 0.25, "ndcg_at_k": recall,
            "mrr": recall, "latency_ms_mean": 1.0, "latency_ms_p50": 1.0,
            "context_tokens_mean": 12.0}


def _scored(strategy="tag", suite=None):
    out = {"strategy": strategy, "top_k": 3, "num_queries": 2,
           "metrics": _metrics(), "per_query": [], "as_of": "2026-01-15",
           "determinism_ok": True, "corpus_fingerprint": "fp",
           "corpus_size": 2}
    if suite:
        out["suite"] = suite
    return out


def _lme_scored():
    out = _scored(suite="longmemeval")
    out["per_category"] = {"alpha": {"recall_at_k": 1.0, "ndcg_at_k": 1.0,
                                     "mrr": 1.0, "num_queries": 1}}
    out["abstention"] = {"count": 1, "by_category": {"alpha": 1}}
    return out


class _Boom(object):
    """A scorer stand-in that raises ONE chosen exception."""

    def __init__(self, exc):
        self.exc = exc

    def __call__(self, *a, **kw):
        raise self.exc


class _EvalCmdCase(unittest.TestCase):
    """A temp KB, the recursion guard, and a seam-injection helper."""

    def setUp(self):
        self.prev_nested = os.environ.get("AGENTWARE_NESTED_UNITTEST")
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"
        self.addCleanup(self._restore)
        self.kdir = tempfile.mkdtemp(prefix="evalcmd-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        os.makedirs(os.path.join(self.kdir, "benchmarks"), exist_ok=True)
        with open(os.path.join(self.kdir, "index.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"entries": []}, f)
        self.dataset = os.path.join(self.kdir, "dataset.json")
        with open(self.dataset, "w", encoding="utf-8") as f:
            json.dump([], f)
        self.data = {"entries": []}

    def _restore(self):
        if self.prev_nested is None:
            os.environ.pop("AGENTWARE_NESTED_UNITTEST", None)
        else:
            os.environ["AGENTWARE_NESTED_UNITTEST"] = self.prev_nested

    def _patch(self, module, name, value):
        real = getattr(module, name)
        self.addCleanup(setattr, module, name, real)
        setattr(module, name, value)

    def _call(self, fn, *a, **kw):
        with _captured() as (out, err):
            rc = fn(*a, **kw)
        return rc, out.getvalue(), err.getvalue()
