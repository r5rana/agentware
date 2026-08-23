#!/usr/bin/env python3
"""The union surface's IDENTITY vocabulary — shared by the deriver and the judge.

Feature 260813-modular-core-adoption, task 99 wave 19.

`tests/_surface_union.py` states TWO obligations: DERIVE the binding union from
the two per-tree captures, and JUDGE a fixture or a merged capture against it.
Wave 19 splits the judging half into `_surface_union_verify.py` so neither half
carries the other's ceiling — and the moment there are two files, the question
"what IS a node, what IS an action, which fields are COMPARED" has two possible
homes, which is the shape that lets two answers drift apart.

So it has exactly one home, here, and both halves import it. This module is
named for what it owns rather than for the fact that more than one caller wants
it: identity is a real boundary (the plan's whole union rests on `path` for a
node and `(dest, option_strings)` for an action being the SAME identity on both
sides), and a module named `utils` for the same contents would have no boundary
at all (R-MOD-05).

Nothing here decides anything. Every value is either a constant the plan pinned,
a pure projection of a capture record, or the loader for the sibling capture
module — so a reader can check the whole file against the counting convention in
`_surface_capture.py` without holding either caller in their head.
"""

from __future__ import annotations

import importlib.util
import json
import os
from importlib.machinery import SourceFileLoader

SCHEMA_VERSION = 1

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_DIR = os.path.join(HERE, "fixtures")
LOCAL_FIXTURE = os.path.join(FIXTURE_DIR, "surface-local.json")
DONOR_FIXTURE = os.path.join(FIXTURE_DIR, "surface-donor.json")
UNION_FIXTURE = os.path.join(FIXTURE_DIR, "surface-union.json")

# Fields compared between the two sides. Identity is (dest, option_strings).
COMPARED_FIELDS = ("kind", "required", "nargs", "default", "choices", "type")

# Fields a union action carries beyond the capture's eight.
UNION_EXTRA_FIELDS = ("sides", "disposition")

DISPOSITIONS = ("required", "excluded")


def load_capture_module():
    """Import ``tests/_surface_capture.py`` for its shared invariants.

    The union reuses the capture's ACTION_FIELDS, STRUCTURAL_ACTIONS and
    prose scanner so the "object dump, no rendered help" contract has ONE
    definition rather than a second drifting copy.
    """
    path = os.path.join(HERE, "_surface_capture.py")
    loader = SourceFileLoader("aw_surface_capture", path)
    spec = importlib.util.spec_from_loader("aw_surface_capture", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def action_key(action):
    """Identity of an action within its node."""
    return (action["dest"], tuple(action["option_strings"]))


def index_nodes(fixture):
    return {tuple(n["path"]): n for n in fixture["nodes"]}


def sides_of(in_local, in_donor):
    if in_local and in_donor:
        return "both"
    return "local" if in_local else "donor"
