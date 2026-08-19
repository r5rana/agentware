"""Signed-tag channel resolver + verify tests (feature 260712, Task 3).

Covers the core security primitive that replaces branch-tip resolution: read-only
`git fetch --tags` (timeout-bounded, rc-124 => graceful skip), SemVer channel
enumeration + total-order sort, SSH-signature verification against the HOME-pinned
trust anchor (fail-closed on unsigned/unknown-signer/tampered), no-downgrade,
newest-verified fallback past a stray/unsigned higher tag with a loud anomaly +
quarantine, and TOCTOU-safe name->SHA resolution. Hermetic: throwaway git
repo/remote + a throwaway signing key + patched HOME (NEVER the operator KB or the
real package remote, R-LOC-03). Skips cleanly when git/ssh signing is absent.

Runner: python3 -m unittest tests.unit.features.test_pkg_channel -v
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

try:
    from tests._fixtures import load_cli
except ImportError:
    from _fixtures import load_cli

DEVNULL = subprocess.DEVNULL


def _have_git():
    try:
        subprocess.run(["git", "--version"], stdout=DEVNULL, stderr=DEVNULL,
                       check=False)
        return True
    except FileNotFoundError:
        return False


def _have_ssh_signing():
    """Probe: can this git make + verify an SSH-signed tag? (git >= 2.34 + ssh-keygen)."""
    if not _have_git() or not os.path.exists("/usr/bin/ssh-keygen"):
        return False
    base = tempfile.mkdtemp()
    try:
        key = os.path.join(base, "k")
        r = subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key,
                            "-C", "probe@test", "-q"], check=False)
        if r.returncode != 0:
            return False
        repo = os.path.join(base, "r")
        os.makedirs(repo)
        for a in (["init", "-q", "-b", "main"],
                  ["config", "user.email", "probe@test"],
                  ["config", "user.name", "P"],
                  ["config", "gpg.format", "ssh"],
                  ["config", "user.signingkey", key + ".pub"]):
            subprocess.run(["git", "-C", repo] + a, check=False,
                           stdout=DEVNULL, stderr=DEVNULL)
        with open(os.path.join(repo, "f"), "w") as _f:
            _f.write("x")
        subprocess.run(["git", "-C", repo, "add", "-A"], check=False,
                       stdout=DEVNULL, stderr=DEVNULL)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "s"], check=False,
                       stdout=DEVNULL, stderr=DEVNULL)
        r = subprocess.run(["git", "-C", repo, "tag", "-s", "-m", "t", "v0.0.1"],
                           check=False, stdout=DEVNULL, stderr=DEVNULL)
        return r.returncode == 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


HAVE_SIGNING = _have_ssh_signing()


class ChannelUnitTests(unittest.TestCase):
    """Pure helpers (no git)."""

    def setUp(self):
        self.mod = load_cli()

    def test_version_tuple(self):
        self.assertEqual(self.mod._pkg_version_tuple("v2.3.4"), (2, 3, 4))
        self.assertEqual(self.mod._pkg_version_tuple("2.3.4"), (2, 3, 4))
        self.assertIsNone(self.mod._pkg_version_tuple("v2.3"))
        self.assertIsNone(self.mod._pkg_version_tuple("v2.0.0-rc1"))
        self.assertIsNone(self.mod._pkg_version_tuple(None))

    def test_channel_regex_stable_only(self):
        self.assertTrue(self.mod.PKG_CHANNEL_RE.match("v10.2.30"))
        self.assertFalse(self.mod.PKG_CHANNEL_RE.match("v2.0.0-beta"))
        self.assertFalse(self.mod.PKG_CHANNEL_RE.match("2.0.0"))


@unittest.skipUnless(HAVE_SIGNING, "git SSH tag signing unavailable")
class ChannelResolverTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-chan-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self._saved = self.mod.HOME_CONFIG
        self.addCleanup(setattr, self.mod, "HOME_CONFIG", self._saved)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")
        self._build_repo()

    def _g(self, *a, **kw):
        return subprocess.run(["git", "-C", self.repo] + list(a), check=kw.get("check", True),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _build_repo(self):
        bare = os.path.join(self.base, "remote.git")
        subprocess.run(["git", "init", "--bare", "-q", bare], check=True)
        self.repo = os.path.join(self.base, "repo")
        subprocess.run(["git", "clone", "-q", bare, self.repo], check=True)
        # throwaway signing key + a SECOND (unpinned) attacker key
        self.key = os.path.join(self.base, "signer")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", self.key,
                        "-C", "release@agentware.test", "-q"], check=True)
        self.evil = os.path.join(self.base, "evil")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", self.evil,
                        "-C", "evil@attacker.test", "-q"], check=True)
        with open(self.key + ".pub") as f:
            self.keydata = f.read().split()[1]
        for a in (("config", "user.email", "release@agentware.test"),
                  ("config", "user.name", "Agentware Release"),
                  ("config", "gpg.format", "ssh"),
                  ("config", "user.signingkey", self.key + ".pub")):
            self._g(*a)
        with open(os.path.join(self.repo, "f"), "w") as f:
            f.write("seed")
        self._g("add", "-A")
        self._g("commit", "-qm", "seed")
        self._g("push", "-q", "origin", "main")
        # pin the real publisher key with the tagger identity as principal
        self.mod._pkg_pin_publisher_key("release@agentware.test", "ssh-ed25519",
                                        self.keydata)

    def _sign_tag(self, tag, key=None):
        key = key or self.key
        self._g("-c", "user.signingkey=%s" % (key + ".pub"),
                "tag", "-s", "-m", "release %s" % tag, tag)

    def _unsigned_tag(self, tag):
        self._g("tag", tag)

    # --- resolve happy path -------------------------------------------------
    def test_resolves_verified_tag_to_concrete_sha(self):
        self._sign_tag("v2.0.0")
        r = self.mod._pkg_resolve_verified_tag(self.repo)
        self.assertEqual(r["status"], "resolved", r)
        self.assertEqual(r["tag"], "v2.0.0")
        self.assertEqual(r["version"], "2.0.0")
        want = self._g("rev-list", "-n", "1", "v2.0.0").stdout.strip()
        self.assertEqual(r["sha"], want)
        # last_check_ts + last_fetch_ts recorded
        st = self.mod._pkg_state_read()
        self.assertIn("last_check_ts", st)
        self.assertIn("last_fetch_ts", st)

    def test_no_downgrade_refused(self):
        self._sign_tag("v2.0.0")
        self.mod._pkg_state_update(last_applied_version="2.0.0")
        r = self.mod._pkg_resolve_verified_tag(self.repo)
        self.assertEqual(r["status"], "up-to-date", r)
        # An OLDER signed tag is also refused.
        self._sign_tag("v1.5.0")
        r = self.mod._pkg_resolve_verified_tag(self.repo)
        self.assertEqual(r["status"], "up-to-date", r)

    def test_newer_verified_tag_wins(self):
        self._sign_tag("v2.0.0")
        self.mod._pkg_state_update(last_applied_version="2.0.0")
        self._sign_tag("v2.1.0")
        r = self.mod._pkg_resolve_verified_tag(self.repo)
        self.assertEqual(r["status"], "resolved")
        self.assertEqual(r["tag"], "v2.1.0")

    # --- fail-closed + anomaly ---------------------------------------------
    def test_unsigned_higher_tag_quarantined_falls_back(self):
        self._sign_tag("v2.0.0")
        self._unsigned_tag("v2.0.1")     # stray unsigned HIGHER tag
        r = self.mod._pkg_resolve_verified_tag(self.repo)
        self.assertEqual(r["status"], "resolved")
        self.assertEqual(r["tag"], "v2.0.0")       # falls back to the verified tag
        self.assertTrue(any("anomalous" in w and "v2.0.1" in w for w in r["warnings"]))
        self.assertTrue(self.mod._pkg_is_quarantined("v2.0.1"))

    def test_wrong_key_tag_refused(self):
        self._sign_tag("v2.0.0")
        self._sign_tag("v2.0.1", key=self.evil)    # signed by an UNPINNED key
        r = self.mod._pkg_resolve_verified_tag(self.repo)
        self.assertEqual(r["tag"], "v2.0.0")
        self.assertTrue(self.mod._pkg_is_quarantined("v2.0.1"))

    def test_no_trust_when_unpinned(self):
        # Wipe the pinned key -> cannot verify -> fail-closed no-op.
        os.remove(self.mod._pkg_allowed_signers_path())
        self._sign_tag("v2.0.0")
        r = self.mod._pkg_resolve_verified_tag(self.repo)
        self.assertEqual(r["status"], "no-trust", r)
        self.assertIsNone(r["sha"])

    def test_no_channel_tags(self):
        r = self.mod._pkg_resolve_verified_tag(self.repo)
        self.assertEqual(r["status"], "none")

    def test_quarantined_tag_skipped_on_resolve(self):
        self._sign_tag("v2.0.0")
        self.mod._pkg_quarantine_tag("v2.0.0", "prior smoke fail")
        r = self.mod._pkg_resolve_verified_tag(self.repo)
        # Only tag is quarantined -> nothing to resolve.
        self.assertEqual(r["status"], "none")

    def test_verify_tag_helper(self):
        self._sign_tag("v2.0.0")
        signers = self.mod._pkg_allowed_signers_path()
        ok, _d = self.mod._pkg_verify_tag(self.repo, "v2.0.0", signers)
        self.assertTrue(ok)
        self._unsigned_tag("v2.0.1")
        ok, _d = self.mod._pkg_verify_tag(self.repo, "v2.0.1", signers)
        self.assertFalse(ok)

    # --- timeout / offline graceful skip -----------------------------------
    def test_fetch_timeout_graceful_skip(self):
        self._sign_tag("v2.0.0")
        fake = subprocess.CompletedProcess(["git"], 124, "", "timed out")
        with mock.patch.object(self.mod, "_git_timed", return_value=fake):
            r = self.mod._pkg_resolve_verified_tag(self.repo)
        self.assertEqual(r["status"], "skipped")
        self.assertIn("timed out", r["detail"])

    def test_fetch_offline_graceful_skip(self):
        fake = subprocess.CompletedProcess(["git"], 1, "", "Could not resolve host")
        with mock.patch.object(self.mod, "_git_timed", return_value=fake):
            r = self.mod._pkg_resolve_verified_tag(self.repo)
        self.assertEqual(r["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
