"""Shared fixture for the `ops.premisecheck` owning unit files (task 99 wave 14).

`test_premisecheck.py` reached the 600-line `tests/**` cap, so it was split along
a class boundary into itself (the two cite resolvers, the path evaluator, and the
two pure helpers underneath it) and `test_premisecheck_corpus.py` (the two corpus
folds, the metric dispatcher, the remaining evaluators and `evaluate_premise`).
The fixture both halves need lives here rather than being imported from one test
file into the other, which would make a failure in the importer look like a
failure in the imported.

R-MOD-04: split where the obligation changes, never raise the cap.
"""

from tests._paths import TESTS_DIR

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

import agentware_lib.core.config as _libconfig                 # noqa: E402
import agentware_lib.ops.premisecheck as PC                    # noqa: E402
import agentware_lib.ops.planpremises as PP                    # noqa: E402


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _prompt(sid, turn, body, origin="human"):
    return ("[2026-07-10T00:00:00Z] [session %s] [cwd /tmp] "
            "[origin=%s] [turn=%s]\n%s\n" % (sid, origin, turn, body))


class _KBCase(unittest.TestCase):
    """A throwaway KB plus the recurrence gate's three knobs PINNED.

    The knobs resolve env -> config.env -> default, so without this an
    operator's machine-level ~/.agentware/config.env decides what
    `recurrence-keys-at-n` counts and the assertions below become
    machine-dependent.
    """

    GATE_ENV = {
        "AGENTWARE_MINER_RECURRENCE_MIN_LEN": "16",
        "AGENTWARE_MINER_RECURRENCE_CUE_REQUIRED": "0",
        "AGENTWARE_MINER_RECURRENCE_STOPWORDS": "",
    }

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw_premisecheck_")
        self.addCleanup(self._cleanup)
        self._saved = {k: os.environ.get(k) for k in self.GATE_ENV}
        os.environ.update(self.GATE_ENV)
        self.kdir = os.path.join(self.root, "kb")
        _write(os.path.join(self.kdir, "MAIN.md"), "# MAIN\n")
        _write(os.path.join(self.kdir, "learnings", "one.md"),
               "---\nid: learn-one\n---\n\nbody\n")
        _write(os.path.join(self.kdir, "index.json"), json.dumps({
            "entries": [
                {"id": "learn-one", "path": "learnings/one.md",
                 "category": "learnings"},
            ]}))
        _write(os.path.join(self.kdir, "work", "f", "worklog.md"), "# w\n")

    def _cleanup(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for dirpath, dirnames, filenames in os.walk(self.root):
            for name in dirnames + filenames:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.root, ignore_errors=True)

    def bare(self):
        d = os.path.join(self.root, "bare")
        _write(os.path.join(d, "MAIN.md"), "# MAIN\n")
        return d




if __name__ == "__main__":                       # pragma: no cover - a fixture
    raise SystemExit("this module holds fixtures, not tests")
