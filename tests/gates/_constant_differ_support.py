"""Shared header for the `test_constant_differ` suite, split out by task 58.

Holds every import, helper, constant and base class the split
parts share, so no part duplicates a definition and none can
drift from another.
"""

from tests._paths import TESTS_DIR
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
try:
    from tests import _constant_differ as cd
    from tests import _constant_adjudication as adj
    from tests import _constant_probe as cp
except ImportError:  # allow `python3 -m unittest tests.gates.test_constant_differ`
    sys.path.insert(0, TESTS_DIR)
    import _constant_differ as cd
    import _constant_adjudication as adj
    import _constant_probe as cp
REPO = cd.REPO_ROOT
SENTINEL = "'constant-differ-sentinel'"
TERMINAL = cd.legacy_absent()
def _tree(**pairs):
    """A synthetic capture: name -> [(relpath, literal)]."""
    return {name: [("m.py", literal)] for name, literal in pairs.items()}
class _StubAdjudications:
    """A synthetic adjudication table, so generator tests need no real name."""

    LEGACY_ONLY = {}
    DIVERGENT = {"D_LIB": {"decision": "lib", "rationale": "x"}}

    @staticmethod
    def follow_up_for(_name):
        return "task 44"
def _kinds(report):
    return {kind for kind in cd.REPORT_KINDS if report[kind]}
def _named(report, kind):
    return {row["name"] for row in report[kind]}
if __name__ == "__main__":
    unittest.main()
