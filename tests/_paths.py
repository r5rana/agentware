"""The ONE anchor a LAYERED test file uses to find the corpus and the repo.

Task 58 layered the corpus: a test file now sits at `tests/unit/<layer>/`,
`tests/integration/`, `tests/e2e/` or `tests/gates/` rather than flat under
`tests/`. That makes the idiom every flat file used --

    REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

-- depth-dependent, and depth-dependence is the failure mode that does NOT
announce itself: the chain still resolves, just to the wrong directory, so the
test reads an empty tree and passes vacuously instead of erroring.

So the anchor is computed ONCE, here, relative to this file -- which sits at
the corpus root and never moves -- and every layered test imports it:

    from tests._paths import REPO_ROOT, TESTS_DIR

No fallback import is offered on purpose. Discovery runs with the repo root as
`top_level_dir`, and a standalone run is `python3 -m unittest
tests.unit.ops.test_x` from the repo root, so `tests` is importable in both.
An `except ImportError: from _paths import ...` fallback would only fire with
the repo root missing from `sys.path` -- exactly the state in which a
silently-wrong root is most likely.
"""

import os

#: `<repo>/tests` -- the corpus root, and the directory holding the gate
#: helper modules (`_fixtures`, `_portability_gate`, ...) that layered tests
#: put on `sys.path` before importing by bare name.
TESTS_DIR = os.path.dirname(os.path.realpath(__file__))

#: `<repo>` -- the package checkout root.
REPO_ROOT = os.path.dirname(TESTS_DIR)
