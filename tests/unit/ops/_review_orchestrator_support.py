"""Shared header for the `test_review_orchestrator` suite, split out by task 58.

Holds every import, helper, constant and base class the split
parts share, so no part duplicates a definition and none can
drift from another.
"""

import argparse
import contextlib
import io
import json
import os
import shutil
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from tests._fixtures import load_cli
def _reviewer_out(dimension, findings):
    return json.dumps({"dimension": dimension, "findings": findings})
REVIEW_ENV_KNOBS = (
    "AGENTWARE_REVIEW_MODEL", "AGENTWARE_REVIEW_EFFORT",
    "AGENTWARE_REVIEW_FANOUT", "AGENTWARE_REVIEW_MAX_ROUNDS",
    "AGENTWARE_SKIP_ADVERSARIAL_REVIEW", "AGENTWARE_CLI",
)
@contextlib.contextmanager
def pinned_review_env():
    """Snapshot + pop every review env knob for the duration of a cmd_review
    drive, restoring the ambient values afterward (mirroring how the checkup
    suites snapshot env). Keeps the gate-chain tests hermetic."""
    saved = {k: os.environ.get(k) for k in REVIEW_ENV_KNOBS}
    for k in REVIEW_ENV_KNOBS:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
if __name__ == "__main__":
    unittest.main()
