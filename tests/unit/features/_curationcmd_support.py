"""Shared header for the `features/curationcmd` owning unit files — wave 13.

`test_curationcmd.py` (the four shared input contracts) and
`test_curationcmd_handlers.py` (the two handlers end to end) are one file split
at the 600-line corpus cap, along a class boundary. Everything both halves need
to stand a scratch knowledge dir up lives here, so neither copy can drift from
the other's fixture — which would make the two halves silently test different
corpora and the near-duplicate ratios incomparable.

Not a test module: it defines no TestCase with test methods of its own, and the
`_`-prefix keeps `unittest discover` from collecting it.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

try:
    from tests._fixtures import build_synthetic_kb
except ImportError:  # pragma: no cover - direct-run fallback
    from _fixtures import build_synthetic_kb

KDIR_ENV = "AGENTWARE_KNOWLEDGE_DIR"

#: The restatement both halves probe with. It is a near-verbatim copy of the
#: seeded learning's body ON PURPOSE: the near-duplicate policy is a THRESHOLD,
#: so a fixture that merely shares a topic proves nothing about where the line
#: sits, and one that shares nothing proves nothing either.
RESTATEMENT = ("A vacuum flask keeps heat in because the gap between its "
               "walls carries no air to conduct it.")

#: A FIXED two-entry corpus, one per category. Fixed because the probe is a
#: BM25 score over it: a corpus that varied per test would make the ratio vary
#: and the threshold assertions would be measuring the fixture.
ENTRIES = [
    {"id": "learn-flask-vacuum-note", "title": "Flask Vacuum Note",
     "category": "learnings", "path": "learnings/flask-vacuum-note.md",
     "tags": ["thermos", "vacuum"], "created": "2026-01-01",
     "author": "seeded", "summary": "A vacuum flask keeps heat in.",
     "body": "# Flask Vacuum Note\n\n" + RESTATEMENT + "\n"},
    {"id": "decide-flask-vacuum-choice", "title": "Flask Vacuum Choice",
     "category": "decisions", "path": "decisions/flask-vacuum-choice.md",
     "tags": ["decision", "thermos"], "created": "2026-01-02",
     "author": "seeded", "summary": "Chose the vacuum flask.",
     "body": "# Flask Vacuum Choice\n\n## Decision\n\n" + RESTATEMENT + "\n"},
]


def _ns(**kw):
    return types.SimpleNamespace(**kw)


def learn_args(**over):
    base = dict(topic="a-fresh-learning", summary="One line.", tags="alpha",
                content="Body about vacuum flasks.", author=None, source=None,
                to_shared=False, force=False, title=None, format="text")
    base.update(over)
    return _ns(**base)


def decide_args(**over):
    base = dict(topic="a-fresh-decision", summary="One line.",
                options="(a); (b)", choice="(a)", rationale="Because.",
                reversible="Yes.", tags=None, author=None, to_shared=False,
                force=False, format="text")
    base.update(over)
    return _ns(**base)


def run(fn, *a, **kw):
    """Call `fn`, returning (result, stdout, stderr). The two handlers write
    their refusals to stderr and their success line to stdout, and the SPLIT is
    part of the contract, so both streams are captured separately."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*a, **kw)
    return rc, out.getvalue(), err.getvalue()


class ScratchKB(unittest.TestCase):
    """A synthetic knowledge dir per test, with the env pointed at it.

    The env var is set EXPLICITLY rather than popped: the resolver's precedence
    is env -> ~/.agentware/config.env -> default, so a test that merely deletes
    the variable runs against the operator's real knowledge base — and every
    test here WRITES.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="w13-curationcmd-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.kdir = os.path.join(self.root, "kb")
        os.makedirs(self.kdir)
        self.data = build_synthetic_kb(self.kdir, [dict(e) for e in ENTRIES])
        self._prev = os.environ.get(KDIR_ENV)
        os.environ[KDIR_ENV] = self.kdir
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._prev is None:
            os.environ.pop(KDIR_ENV, None)
        else:
            os.environ[KDIR_ENV] = self._prev

    def index(self):
        with open(os.path.join(self.kdir, "index.json"), encoding="utf-8") as f:
            return json.load(f)

    def ids(self):
        return [e["id"] for e in self.index()["entries"]]

    def row(self, eid):
        return [e for e in self.index()["entries"] if e["id"] == eid][0]
