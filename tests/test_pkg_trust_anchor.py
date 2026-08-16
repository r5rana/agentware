"""Trust-anchor tests (feature 260712-scheduler-updater, Task 2).

Covers the HOME-pinned publisher SSH signing-key trust root: pure-stdlib SHA256
fingerprinting, multi-key pin/list with valid-after/valid-before overlap windows,
dedup idempotence, the fail-closed integrity guard (symlink/wrong-owner/group-
writable), the in-tree-vs-pinned fingerprint-compare warnings, and the `trust`
command (list/pin/rotate/verify). Hermetic: HOME_CONFIG patched onto a tempdir;
key material is a format-valid throwaway blob (never a real operator key).

Runner: python3 -m unittest tests.test_pkg_trust_anchor -v
"""

import base64
import os
import shutil
import struct
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, run_cli
except ImportError:
    from _fixtures import load_cli, run_cli


def _ed25519_blob(seed):
    """A FORMAT-valid ssh-ed25519 key blob (b64). Not a real curve point, but the
    SSH wire format the fingerprint/pin/list helpers parse. `seed` (32 bytes)
    varies the key so dedup/rotation can be exercised deterministically."""
    kt = b"ssh-ed25519"
    pub = (seed * 32)[:32]
    blob = struct.pack(">I", len(kt)) + kt + struct.pack(">I", len(pub)) + pub
    return base64.b64encode(blob).decode("ascii")


class TrustAnchorTestCase(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.home = tempfile.mkdtemp(prefix="agentware-trust-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        # Task 40: the anchor moved to agentware_lib.core.pkgtrust, which resolves
        # HOME_CONFIG late-bound through the core.config AGGREGATOR — the declared
        # home of the two mutable globals the suite patches. Rebinding it on the
        # legacy module alone no longer reaches the anchor, so both are redirected.
        # Import-only diff: every assertion below is unchanged.
        import agentware_lib.core.config as _libconfig
        self._libconfig = _libconfig
        self._saved = (self.mod.HOME_CONFIG, _libconfig.HOME_CONFIG)
        self.addCleanup(self._restore_home_config)
        cfg_path = os.path.join(self.home, ".agentware", "config.env")
        self.mod.HOME_CONFIG = cfg_path
        _libconfig.HOME_CONFIG = cfg_path
        self.key_a = _ed25519_blob(b"A")
        self.key_b = _ed25519_blob(b"B")

    def _restore_home_config(self):
        self.mod.HOME_CONFIG, self._libconfig.HOME_CONFIG = self._saved

    def signers_path(self):
        return self.mod._pkg_allowed_signers_path()

    # --- fingerprint --------------------------------------------------------
    def test_fingerprint_shape_and_determinism(self):
        fp = self.mod._pkg_ssh_key_fingerprint(self.key_a)
        self.assertTrue(fp.startswith("SHA256:"))
        self.assertEqual(fp, self.mod._pkg_ssh_key_fingerprint(self.key_a))
        self.assertNotEqual(fp, self.mod._pkg_ssh_key_fingerprint(self.key_b))

    def test_fingerprint_rejects_non_base64(self):
        self.assertIsNone(self.mod._pkg_ssh_key_fingerprint("!!!not-b64!!!"))

    @unittest.skipUnless(os.path.exists("/usr/bin/ssh-keygen"), "no ssh-keygen")
    def test_fingerprint_matches_ssh_keygen(self):
        # Cross-check the pure-stdlib fingerprint against ssh-keygen on a REAL key.
        kd = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, kd, True)
        key = os.path.join(kd, "k")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key,
                        "-C", "test"], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        with open(key + ".pub") as f:
            keytype, keydata = f.read().split()[:2]
        want = subprocess.run(["ssh-keygen", "-lf", key + ".pub"],
                              stdout=subprocess.PIPE, text=True, check=True
                              ).stdout.split()[1]
        self.assertEqual(self.mod._pkg_ssh_key_fingerprint(keydata), want)

    # --- pin / list / multi-key / dedup ------------------------------------
    def test_pin_then_list(self):
        fp, added = self.mod._pkg_pin_publisher_key("agentware-release",
                                                    "ssh-ed25519", self.key_a)
        self.assertTrue(added)
        recs = self.mod._pkg_list_signers()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["principal"], "agentware-release")
        self.assertEqual(recs[0]["keytype"], "ssh-ed25519")
        self.assertEqual(recs[0]["fingerprint"], fp)

    def test_pin_dedup_idempotent(self):
        self.mod._pkg_pin_publisher_key("p", "ssh-ed25519", self.key_a)
        _fp, added = self.mod._pkg_pin_publisher_key("p", "ssh-ed25519", self.key_a)
        self.assertFalse(added)
        self.assertEqual(len(self.mod._pkg_list_signers()), 1)

    def test_multi_key_overlap_window(self):
        # Old key with a valid-before; new key with a valid-after -> both trusted.
        self.mod._pkg_pin_publisher_key("p", "ssh-ed25519", self.key_a,
                                        valid_before="20270101")
        self.mod._pkg_pin_publisher_key("p", "ssh-ed25519", self.key_b,
                                        valid_after="20260101")
        recs = self.mod._pkg_list_signers()
        self.assertEqual(len(recs), 2)
        raw = "\n".join(r["raw"] for r in recs)
        self.assertIn('valid-before="20270101"', raw)
        self.assertIn('valid-after="20260101"', raw)

    def test_pin_rejects_bad_key(self):
        with self.assertRaises(self.mod._PkgStateError):
            self.mod._pkg_pin_publisher_key("p", "ssh-ed25519", "!!!not-b64!!!")
        with self.assertRaises(self.mod._PkgStateError):
            self.mod._pkg_pin_publisher_key("p", "bogus-type", self.key_a)

    def test_signers_file_is_0600(self):
        import stat as _s
        self.mod._pkg_pin_publisher_key("p", "ssh-ed25519", self.key_a)
        self.assertEqual(_s.S_IMODE(os.lstat(self.signers_path()).st_mode), 0o600)

    # --- fail-closed integrity ---------------------------------------------
    def test_symlinked_signers_refused(self):
        self.mod._pkg_secure_home()
        victim = os.path.join(self.home, "victim")
        with open(victim, "w") as f:
            f.write("evil ssh-ed25519 %s\n" % self.key_b)
        os.symlink(victim, self.signers_path())
        with self.assertRaises(self.mod._PkgStateError):
            self.mod._pkg_list_signers()

    def test_group_writable_signers_refused(self):
        self.mod._pkg_pin_publisher_key("p", "ssh-ed25519", self.key_a)
        os.chmod(self.signers_path(), 0o664)
        with self.assertRaises(self.mod._PkgStateError):
            self.mod._pkg_list_signers()

    # --- in-tree fingerprint compare ---------------------------------------
    def test_intree_fingerprint_warning(self):
        # Pin key A; an in-tree key B (different fp) must trigger a loud warning.
        self.mod._pkg_pin_publisher_key("p", "ssh-ed25519", self.key_a)
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo, True)
        os.makedirs(os.path.join(repo, "catalog"))
        with open(os.path.join(repo, "catalog", "agentware-release.pub"), "w") as f:
            f.write("ssh-ed25519 %s release@agentware\n" % self.key_b)
        # Task 40: `_pkg_intree_signing_key_path` moved to core.pkgtrust, which
        # resolves REPO_ROOT late-bound through core.paths. Redirect both, same as
        # HOME_CONFIG in setUp. Import-only diff: the assertions are unchanged.
        import agentware_lib.core.paths as _libpaths
        saved = (self.mod.REPO_ROOT, _libpaths.REPO_ROOT)

        def _restore(_s=saved, _p=_libpaths):
            self.mod.REPO_ROOT, _p.REPO_ROOT = _s
        self.addCleanup(_restore)
        self.mod.REPO_ROOT = repo
        _libpaths.REPO_ROOT = repo
        warnings = self.mod._pkg_trust_fingerprint_warnings()
        self.assertEqual(len(warnings), 1)
        self.assertIn("rotation must be operator-confirmed", warnings[0])
        # Pinning key B too clears the warning (rotation accepted).
        self.mod._pkg_pin_publisher_key("p", "ssh-ed25519", self.key_b)
        self.assertEqual(self.mod._pkg_trust_fingerprint_warnings(), [])

    def test_no_warning_when_nothing_pinned(self):
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo, True)
        os.makedirs(os.path.join(repo, "catalog"))
        with open(os.path.join(repo, "catalog", "agentware-release.pub"), "w") as f:
            f.write("ssh-ed25519 %s\n" % self.key_b)
        # Task 40: `_pkg_intree_signing_key_path` moved to core.pkgtrust, which
        # resolves REPO_ROOT late-bound through core.paths. Redirect both, same as
        # HOME_CONFIG in setUp. Import-only diff: the assertions are unchanged.
        import agentware_lib.core.paths as _libpaths
        saved = (self.mod.REPO_ROOT, _libpaths.REPO_ROOT)

        def _restore(_s=saved, _p=_libpaths):
            self.mod.REPO_ROOT, _p.REPO_ROOT = _s
        self.addCleanup(_restore)
        self.mod.REPO_ROOT = repo
        _libpaths.REPO_ROOT = repo
        self.assertEqual(self.mod._pkg_trust_fingerprint_warnings(), [])

    # --- cmd_trust via argparse dispatch -----------------------------------
    def test_cmd_trust_list_empty(self):
        code, out, _e = run_cli(["trust", "list"], self.home)
        self.assertEqual(code, 0)
        self.assertIn("no publisher keys pinned", out)

    def test_cmd_trust_pin_and_verify(self):
        kd = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, kd, True)
        pub = os.path.join(kd, "k.pub")
        with open(pub, "w") as f:
            f.write("ssh-ed25519 %s release@agentware\n" % self.key_a)
        code, out, err = run_cli(["trust", "pin", "--principal", "agentware-release",
                                  "--key-file", pub], self.home)
        self.assertEqual(code, 0, err)
        self.assertIn("pinned", out)
        code, out, _e = run_cli(["trust", "verify", "--format", "json"], self.home)
        import json as _json
        payload = _json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["pinned_count"], 1)


if __name__ == "__main__":
    unittest.main()
