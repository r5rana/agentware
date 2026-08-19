"""Atomicity + crash-safety guard for `save_index`
(feature 260710-learning-repair-p1 task 3).

`save_index` is the SOLE writer of index.json. A torn/partial write (crash or a
concurrent reader mid-write) would be committed and pushed to every clone by
autocommit. The fix serializes to a temp file in the same directory, fsyncs, and
os.replace()s onto index.json — atomic on a single filesystem. Stdlib only.
"""

import glob
import inspect
import json
import os
import shutil
import stat
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, build_synthetic_kb
except ImportError:  # discover -s tests puts tests/ on sys.path
    from _fixtures import load_cli, build_synthetic_kb


class AtomicSaveTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="agentware-atomic-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.index = os.path.join(self.kdir, "index.json")

    def _tmps(self):
        return glob.glob(os.path.join(self.kdir, ".index.*.tmp"))

    def test_uses_atomic_replace(self):
        src = inspect.getsource(self.mod.save_index)
        self.assertIn("os.replace", src, "save_index must rename atomically")
        self.assertIn("fsync", src, "save_index must fsync before replace")

    def test_roundtrip_is_byte_canonical(self):
        data, err = self.mod.load_index(self.kdir)
        self.assertIsNone(err)
        self.mod.save_index(self.kdir, data)
        with open(self.index, encoding="utf-8") as f:
            raw = f.read()
        self.assertEqual(
            raw, json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            "atomic write must be byte-identical to the canonical format")
        self.assertEqual(self._tmps(), [], "no temp file left behind")

    def test_partial_write_leaves_original_intact(self):
        with open(self.index, "rb") as f:
            before = f.read()
        # A set is not JSON-serializable: json.dump raises mid-write, so the
        # temp file is discarded and os.replace never runs.
        with self.assertRaises(TypeError):
            self.mod.save_index(self.kdir, {"entries": {1, 2, 3}, "tags": {}})
        with open(self.index, "rb") as f:
            after = f.read()
        self.assertEqual(before, after,
                         "a failed save must not corrupt the live index.json")
        self.assertEqual(self._tmps(), [],
                         "a failed save must not leak a temp file")

    def test_preserves_existing_file_mode(self):
        os.chmod(self.index, 0o600)
        data, _ = self.mod.load_index(self.kdir)
        self.mod.save_index(self.kdir, data)
        self.assertEqual(stat.S_IMODE(os.stat(self.index).st_mode), 0o600,
                         "atomic replace must preserve the index's mode bits")


if __name__ == "__main__":
    unittest.main()
