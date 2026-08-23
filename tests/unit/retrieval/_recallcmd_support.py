"""Shared header for the `test_recallcmd` suite — task 99 wave 9.

Holds every import, stand-in and fixture builder the two parts share, so neither
part duplicates a definition and the two cannot drift. Split out because the
wave's owning unit file for `retrieval.recallcmd` outgrew the 600-line corpus
cap; the seam is the one the MODULE itself has, and the one the wave's own
paydown named — the CURATED path (`test_recallcmd`: the token-budget walk, the
payload schema, the org scope block, the argument refusals and the human
renderer) and the `--scope work` ARM (`test_recallcmd_work`: an ephemeral corpus
that is never indexed, ranked by BM25 and weighted by each feature's plan
state, with a deliberately thinner view of its own).

`RecallCmdTestCase` lives here rather than in either part on purpose: it builds
both scratch knowledge bases, and a second copy is exactly the drift that would
let one part rank a work tree the other part does not have.
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

try:
    from tests._fixtures import build_synthetic_kb, run_cli
except ImportError:  # direct invocation
    sys.path.insert(0, TESTS_DIR)
    from _fixtures import build_synthetic_kb, run_cli

import agentware_lib.retrieval.recallcmd as recallcmd  # noqa: E402


class _Args(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _capture(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rv = fn(*a, **k)
    return rv, out.getvalue(), err.getvalue()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class RecallCmdTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="w9-recallcmd-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.kdir = os.path.join(self.root, "kb")
        os.makedirs(self.kdir, exist_ok=True)
        self.data = build_synthetic_kb(self.kdir)

    def work_kb(self):
        """A work tree whose features carry three different plan states, so the
        multiplier is observable rather than merely applied."""
        kdir = os.path.join(self.root, "workkb")
        os.makedirs(kdir, exist_ok=True)
        build_synthetic_kb(kdir)
        w = os.path.join(kdir, "work")
        _write(os.path.join(w, "260101-alpha", "plan.md"),
               "# Alpha plan\n\n> Status: running\n\ngeofence work in flight\n")
        _write(os.path.join(w, "260202-beta", "plan.md"),
               "# Beta plan\n\n> Status: superseded\n\ngeofence work replaced\n")
        _write(os.path.join(w, "260303-gamma", "worklog.md"),
               "# Gamma worklog\n\ngeofence notes\n")
        return kdir
