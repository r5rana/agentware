"""Shared header for the `test_metricspricing` owning suite — task 99 wave 18.

Holds the imports and the two row/usage builders the parts share, so neither
duplicates a definition and neither can drift from the other. The suite was
split by ADDITION when the owning file crossed the 600-line `tests/**` cap: the
COST half (price lookup, the pure usage->cost, the p50, the buckets, the per-row
pricing, the anomaly walk and the whole `apply_pricing` fold) stays in the owning
file; the TABLE-AND-SIZING half (`load_price_table`, the context-window lookup
and the published surface) moved to the sibling.
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

import agentware_lib.ops.metrics.metricspricing as pricing   # noqa: E402


def _usage(i=0, o=0, cc=0, cr=0):
    return {"input_tokens": i, "output_tokens": o,
            "cache_creation_input_tokens": cc, "cache_read_input_tokens": cr}


def _row(**kw):
    base = {"tokens": _usage(), "models": {}, "total_tokens": 0,
            "date": None, "feature": None}
    base.update(kw)
    return base
