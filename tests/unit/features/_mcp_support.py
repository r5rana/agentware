"""Shared header for the `test_mcp` owning suite — task 99 wave 18.

Holds the imports, the untrusted-index fixture and the helper the two parts
share, so neither part duplicates a definition and neither can drift from the
other. The suite was split by ADDITION when the owning file crossed the 600-line
`tests/**` cap: the LOOKUP half (the entry maps, the id/path/tag/category
dispatch, the argument validators and the freshness-stamped tool payload) stays
in the owning file; the SERVER half (the JSON-RPC envelopes, the router and the
stdio loop) moved to the sibling.
"""

from tests._paths import TESTS_DIR

import copy
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

import agentware_lib.features.mcp as mcp        # noqa: E402


#: An index carrying every shape the entry maps can meet. The duplicate id and
#: the duplicate path are the load-bearing rows: their payloads DIFFER, so
#: first-wins and last-wins produce visibly different answers.
ENTRIES = [
    {"id": "learn-alpha", "path": "learnings/alpha.md", "category": "learnings",
     "summary": "FIRST under this id"},
    {"id": "learn-alpha", "path": "learnings/alpha-dup.md",
     "category": "learnings", "summary": "SECOND under this id"},
    {"id": "learn-beta", "path": "learnings/alpha.md", "category": "learnings",
     "summary": "SECOND at this path"},
    "a bare string",
    ["a", "list"],
    None,
    {"path": "references/no-id.md", "category": "references"},
    {"id": "ref-no-path", "category": "references"},
    {"id": None, "path": "references/null-id.md", "category": "references"},
    {"id": "ref-null-path", "path": None, "category": "references"},
    {"id": "", "path": "", "category": "references"},
    {"id": "decide-gamma", "path": "decisions/gamma.md", "category": "decisions"},
]

TAGS = {"loops": ["learn-alpha", "decide-gamma"],
        "ghost": ["learn-nobody", "learn-alpha"],
        "empty": []}


def _data():
    return {"entries": copy.deepcopy(ENTRIES), "tags": copy.deepcopy(TAGS)}
