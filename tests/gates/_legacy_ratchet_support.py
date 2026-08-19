"""Shared header for the `test_legacy_ratchet` suite, split out by task 58.

Holds every import, helper, constant and base class the split
parts share, so no part duplicates a definition and none can
drift from another.
"""

from tests._paths import TESTS_DIR
import json
import os
import shutil
import sys
import tempfile
import unittest
try:
    from tests import _legacy_ratchet as lr
except ImportError:  # allow `python3 -m unittest tests.gates.test_legacy_ratchet`
    sys.path.insert(0, TESTS_DIR)
    import _legacy_ratchet as lr  # type: ignore
REPO = lr.REPO_ROOT
LEGACY_STUB = '''"""scratch monolith"""


def cmd_alpha():
    return 0


def cmd_beta_sub():
    return 0


def helper():
    return 0
'''
STRANGLER_STUB = "PORTED_VERBS = frozenset(%r)\n"
SCRATCH_VERBS = ["alpha", "beta", "gamma", "two-words"]
def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
class _ScratchTree:
    """A minimal tree the ratchet can measure: a monolith and an allowlist."""

    def __init__(self, root, legacy=LEGACY_STUB, ported=(), verbs=None):
        self.root = root
        self.legacy_path = os.path.join(root, "scripts", "agentware_legacy.py")
        self.fixture_path = os.path.join(root, "legacy-ratchet.json")
        self.union_path = os.path.join(root, "union.json")
        self.lib_dir = os.path.join(root, "scripts", "agentware_lib")
        if legacy is not None:
            _write(self.legacy_path, legacy)
        _write(os.path.join(self.lib_dir, "__init__.py"), "")
        self.set_ported(ported)
        _write(self.union_path, json.dumps(
            {"top_level_verbs": list(SCRATCH_VERBS if verbs is None else verbs)}))

    # -- mutations ---------------------------------------------------------

    def set_legacy(self, text):
        if text is None:
            os.remove(self.legacy_path)
        else:
            _write(self.legacy_path, text)

    def set_ported(self, verbs):
        _write(os.path.join(self.lib_dir, "strangler.py"),
               STRANGLER_STUB % (sorted(verbs),))

    def drop_strangler(self):
        os.remove(os.path.join(self.lib_dir, "strangler.py"))

    # -- fixture -----------------------------------------------------------

    def seed_fixture(self, **overrides):
        current = lr.measure_legacy(self.legacy_path)
        data = {
            "schema": lr.SCHEMA,
            "high_water": {m: current[m] for m in lr.CEILINGS},
            "floor": {"ported_verbs": list(lr.measure_ported(self.root))},
        }
        for mark in lr.NAME_SETS:
            data["high_water"][mark] = list(current[mark])
        data["high_water"].update(overrides.pop("high_water", {}))
        data["floor"].update(overrides.pop("floor", {}))
        data.update(overrides)
        _write(self.fixture_path, json.dumps(data))
        return data

    def read_fixture(self):
        with open(self.fixture_path, encoding="utf-8") as fh:
            return json.load(fh)

    # -- gate --------------------------------------------------------------

    def check(self, strict=False):
        return lr.check(self.fixture_path, self.root, strict, self.union_path)

    def record(self):
        return lr.record(self.fixture_path, self.root, self.union_path)

    def violations(self):
        return lr.compare(lr.measure_legacy(self.legacy_path),
                          lr.measure_ported(self.root), self.read_fixture(),
                          lr.union_verbs(self.union_path))
class _ScratchCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aw-ratchet-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def tree(self, **kw):
        return _ScratchTree(self.dir, **kw)
def _lib_def_names():
    """Every module-scope def name defined anywhere under `scripts/agentware_lib`.

    Walked from DISK rather than from `sys.modules`, for the same reason
    `ported_module_paths` is: an import-order-dependent scan makes a gate that
    passes alone and fails in company.
    """
    import ast as _ast
    import os as _os
    root = _os.path.join(lr.REPO_ROOT if hasattr(lr, "REPO_ROOT") else
                         _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         "scripts", "agentware_lib")
    names = set()
    for base, dirs, files in _os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith(".py"):
                continue
            try:
                tree = _ast.parse(open(_os.path.join(base, f), encoding="utf-8").read())
            except SyntaxError:
                continue
            for n in tree.body:
                if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    names.add(n.name)
    return names
if __name__ == "__main__":
    unittest.main()
