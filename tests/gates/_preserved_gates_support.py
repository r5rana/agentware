"""Shared header for the `test_preserved_gates` suite, split out by task 58.

Holds every import, helper, constant and base class the split
parts share, so no part duplicates a definition and none can
drift from another.
"""

from tests._paths import TESTS_DIR
import importlib.util
import json
import os
import shutil
import sys
import types
import tempfile
import unittest
HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
DONOR = "/tmp/aw-scratch/Agentware"
sys.path.insert(0, HERE)
from _preserved_freeze import (  # noqa: E402
    COLLISION_DESTINATIONS, RELAYERING_COLLISIONS, check_destination, derive,
    diff_node_ids, load_node_ids, reachable_symbols, unsatisfied_assets, verify)
from _preserved_scan import (  # noqa: E402
    FIXTURE_FAMILIES, ScopeIndex, collect_node_ids, scan_cli_symbols)
from _test_layout import RELOCATIONS, relocate, resolves  # noqa: E402
FIXTURE = os.path.join(HERE, "fixtures", "preserved-surface.json")
def load_fixture():
    with open(FIXTURE) as fh:
        return json.load(fh)
def donor_available():
    return os.path.isdir(os.path.join(DONOR, "tests"))
if __name__ == "__main__":
    unittest.main()
