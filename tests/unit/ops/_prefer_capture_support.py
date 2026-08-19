"""Shared header for the `test_prefer_capture` suite, split out by task 58.

Holds every import, helper, constant and base class the split
parts share, so no part duplicates a definition and none can
drift from another.
"""

import json
import os
import shutil
import tempfile
import unittest
try:
    from tests._fixtures import run_cli, build_synthetic_kb, load_cli
except ImportError:
    from _fixtures import run_cli, build_synthetic_kb, load_cli
def _mk_project(kdir, slug):
    d = os.path.join(kdir, "projects", slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "index.md"), "w", encoding="utf-8") as f:
        f.write("# %s\n\n_project._\n" % slug)
if __name__ == "__main__":
    unittest.main()


class _PreferCaptureCase(unittest.TestCase):
    """Fixture shared by both halves of the `TestPreferCapture` suite.

    Task 58 had to split the class itself, not just the file: at 607
    lines it was over the ceiling on its own, with no class boundary to
    split at. Both halves keep the class NAME `TestPreferCapture`, so
    every frozen node-id tail (`TestPreferCapture.test_...`) still
    collects -- a rename would read as a deleted test to the freeze, and
    it would be right to.
    """

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_prefer_capture_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        with open(os.path.join(self.kdir, ".initialized"), "w",
                  encoding="utf-8") as f:
            f.write("test\n")
        _mk_project(self.kdir, "tokto-io")

    # --- helpers ------------------------------------------------------------
    def _cli(self, argv, **kw):
        return run_cli(argv, self.kdir, **kw)

    def _index(self):
        with open(os.path.join(self.kdir, "index.json"), encoding="utf-8") as f:
            return json.load(f)

    def _entry(self, eid):
        return next((e for e in self._index()["entries"]
                     if e.get("id") == eid), None)

    def _count(self):
        return len(self._index()["entries"])
