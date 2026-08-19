"""Tamper-consistency of the HOME state sidecar + trust anchor across EVERY
consumer (feature 260712-p2b-hardening-followups, Task 1 — audit A1/A4/A5).

The main resolver (`_pkg_resolve_verified_tag`) already fails closed on a
tampered `allowed_signers` trust anchor. These tests assert the OTHER consumers
are consistent with it — never crash, never TRUST a tampered anchor:

  A1 — the crash-window reconcile helper `_pkg_tag_at_head_if_newer` runs the same
       lstat integrity guard, so a symlinked/group-writable anchor makes it return
       (None, None) (fail-closed) instead of trusting the anchor to finalize.
  A4 — `_pkg_emit_heartbeat` does NOT raise on a tampered sidecar (a scheduled tick
       must not crash) — `_pkg_version()` reaches the sidecar via its
       last_applied_version fallback.
  A5 — `_audit_pkg_version_check` reports a clean ok=False FAIL on a tampered
       sidecar instead of an unhandled traceback that crashes `audit`.

Hermetic: HOME_CONFIG is patched onto a throwaway home so the real ~/.agentware is
NEVER touched (R-LOC-03). The pure-sidecar/anchor cases need no signing toolchain.

Runner: python3 -m unittest tests.unit.features.test_pkg_tamper_consistency -v
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


def _have_signing():
    if not os.path.exists("/usr/bin/ssh-keygen"):
        return False
    base = tempfile.mkdtemp()
    try:
        key = os.path.join(base, "k")
        if subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key, "-q"],
                          check=False).returncode != 0:
            return False
        repo = os.path.join(base, "r")
        os.makedirs(repo)
        for a in (["init", "-q", "-b", "main"], ["config", "user.email", "p@t"],
                  ["config", "user.name", "P"], ["config", "gpg.format", "ssh"],
                  ["config", "user.signingkey", key + ".pub"]):
            subprocess.run(["git", "-C", repo] + a, check=False, stdout=DEVNULL, stderr=DEVNULL)
        with open(os.path.join(repo, "f"), "w") as f:
            f.write("x")
        subprocess.run(["git", "-C", repo, "add", "-A"], check=False, stdout=DEVNULL, stderr=DEVNULL)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "s"], check=False, stdout=DEVNULL, stderr=DEVNULL)
        return subprocess.run(["git", "-C", repo, "tag", "-s", "-m", "t", "v0.0.1"],
                              check=False, stdout=DEVNULL, stderr=DEVNULL).returncode == 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


HAVE_SIGNING = _have_signing()


@unittest.skipIf(os.name != "posix", "POSIX lstat/symlink guards only")
class TamperConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-tamper-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.kdir = os.path.join(self.base, "kb")
        os.makedirs(self.kdir)
        self._saved = self.mod.HOME_CONFIG
        self.addCleanup(setattr, self.mod, "HOME_CONFIG", self._saved)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")
        self.mod._pkg_secure_home()

    def _anchor_path(self):
        return self.mod._pkg_allowed_signers_path()

    def _state_path(self):
        return self.mod._pkg_state_path()

    def _tamper_group_writable(self, path):
        """Make `path` a real, group-writable regular file (fails lstat guard)."""
        with open(path, "w", encoding="utf-8") as f:
            f.write("x")
        os.chmod(path, 0o664)

    def _symlink_anchor(self):
        """Point the anchor at an attacker-controlled file via a symlink."""
        evil = os.path.join(self.base, "evil_signers")
        with open(evil, "w", encoding="utf-8") as f:
            f.write("attacker key\n")
        os.symlink(evil, self._anchor_path())

    # --- A1: reconcile helper fails closed on a tampered anchor -------------
    def test_pkgtamper_tag_at_head_symlinked_anchor_fails_closed(self):
        self._symlink_anchor()
        # The load-bearing guard the A1 fix relies on MUST detect the tamper. This
        # assertion is signing-independent and DISCRIMINATING (it does not raise on a
        # clean anchor), so the test is not tautological — the reconcile helper's
        # (None,None) here would otherwise pass via the no-git-HEAD early return.
        with self.assertRaises(self.mod._PkgStateError):
            self.mod._pkg_assert_signers_secure()
        # A tampered anchor must yield (None, None), never a "verified" tag.
        tag, ver = self.mod._pkg_tag_at_head_if_newer(self.kdir, None)
        self.assertIsNone(tag)
        self.assertIsNone(ver)

    def test_pkgtamper_tag_at_head_group_writable_anchor_fails_closed(self):
        self._tamper_group_writable(self._anchor_path())
        with self.assertRaises(self.mod._PkgStateError):
            self.mod._pkg_assert_signers_secure()          # guard detects the tamper
        tag, ver = self.mod._pkg_tag_at_head_if_newer(self.kdir, None)
        self.assertIsNone(tag)
        self.assertIsNone(ver)

    def test_pkgtamper_tag_at_head_does_not_raise_on_tamper(self):
        # It must fail closed WITHOUT propagating _PkgStateError (the reconcile
        # caller does not wrap it in a _PkgStateError handler).
        self._symlink_anchor()
        with self.assertRaises(self.mod._PkgStateError):
            self.mod._pkg_assert_signers_secure()          # the tamper IS detectable
        try:
            self.mod._pkg_tag_at_head_if_newer(self.kdir, None)
        except self.mod._PkgStateError as exc:  # pragma: no cover - regression guard
            self.fail("_pkg_tag_at_head_if_newer raised on a tampered anchor: %s" % exc)

    def test_pkgtamper_tag_at_head_clean_anchor_still_resolves_path(self):
        # Sanity: with NO tamper the guard is transparent (the happy path is
        # unchanged) — an absent anchor + no channel tag yields (None, None) too,
        # but critically WITHOUT raising, and _pkg_assert_signers_secure returns
        # the same path _pkg_allowed_signers_path would.
        self.assertEqual(self.mod._pkg_assert_signers_secure(), self._anchor_path())
        tag, ver = self.mod._pkg_tag_at_head_if_newer(self.kdir, None)
        self.assertIsNone(tag)  # no repo/tags here — but no crash

    # --- A4: heartbeat never raises on a tampered sidecar ------------------
    def test_pkgtamper_heartbeat_no_raise_on_tampered_sidecar(self):
        # Force _pkg_version() down its sidecar fallback: patch the git-describe
        # exact-match to miss so _pkg_version reads last_applied_version from the
        # (now tampered) sidecar and raises _PkgStateError inside the heartbeat.
        self._tamper_group_writable(self._state_path())
        # A group-writable sidecar makes _pkg_state_read raise _PkgStateError.
        with self.assertRaises(self.mod._PkgStateError):
            self.mod._pkg_state_read()
        # Patch REPO_ROOT to a NON-git dir so _pkg_version()'s `git describe` misses
        # and it falls to the tampered sidecar — otherwise on a released (tagged)
        # checkout the heartbeat never reads the sidecar and the test is vacuous.
        with mock.patch.object(self.mod, "REPO_ROOT", self.base):
            try:
                self.mod._pkg_emit_heartbeat(self.kdir)   # must swallow, never raise
            except self.mod._PkgStateError as exc:
                self.fail("_pkg_emit_heartbeat raised on a tampered sidecar: %s" % exc)

    def test_pkgtamper_heartbeat_via_pkg_version_fallback_no_raise(self):
        # Explicitly exercise the _pkg_version() -> _pkg_state_read() path with a
        # symlinked sidecar (another tamper shape). REPO_ROOT -> non-git so describe
        # misses and the sidecar fallback is taken regardless of the ambient checkout.
        evil = os.path.join(self.base, "evil_state")
        with open(evil, "w", encoding="utf-8") as f:
            f.write("{}")
        os.symlink(evil, self._state_path())
        with mock.patch.object(self.mod, "REPO_ROOT", self.base):
            try:
                self.mod._pkg_emit_heartbeat(self.kdir)
            except self.mod._PkgStateError as exc:
                self.fail("heartbeat raised on a symlinked sidecar: %s" % exc)

    # --- A5: audit reports a clean FAIL on a tampered sidecar --------------
    def test_pkgtamper_audit_clean_fail_on_tampered_sidecar(self):
        # updater ON + a pinned key so the audit reaches the direct sidecar read;
        # pin via monkeypatch (no signing toolchain needed for this path) so
        # _pkg_list_signers() returns non-empty and we fall through to the read.
        self._tamper_group_writable(self._state_path())
        with mock.patch.object(self.mod, "resolve_updater", return_value="1"), \
             mock.patch.object(self.mod, "_pkg_list_signers",
                               return_value=[{"principal": "x"}]):
            c = self.mod._audit_pkg_version_check(self.kdir)
        self.assertFalse(c["ok"])
        self.assertIn("tampered", " ".join(c["details"]).lower())

    def test_pkgtamper_audit_no_raise_when_off_and_sidecar_tampered(self):
        # Even with the updater OFF the audit must not crash: the OFF-inert branch
        # calls _pkg_version() which reaches the tampered sidecar. REPO_ROOT -> a
        # non-git dir makes `git describe` deterministically miss, so this holds on
        # ANY checkout (including a released, tagged one) not just an untagged HEAD.
        self._tamper_group_writable(self._state_path())
        with mock.patch.object(self.mod, "resolve_updater", return_value="0"), \
             mock.patch.object(self.mod, "REPO_ROOT", self.base):
            c = self.mod._audit_pkg_version_check(self.kdir)
        self.assertFalse(c["ok"])
        self.assertIn("tampered", " ".join(c["details"]).lower())

    def test_pkgtamper_audit_clean_sidecar_off_is_inert_ok(self):
        # Regression: a CLEAN sidecar + updater OFF is still inert ok=True.
        with mock.patch.object(self.mod, "resolve_updater", return_value="0"):
            c = self.mod._audit_pkg_version_check(self.kdir)
        self.assertTrue(c["ok"])
        self.assertIn("OFF", c["details"][0])


@unittest.skipUnless(HAVE_SIGNING, "git SSH tag signing unavailable")
class TamperReconcileDiscriminatorTests(unittest.TestCase):
    """A1 discriminator: HEAD sitting AT a genuinely-verifiable signed tag, with a
    trust anchor whose CONTENT is valid but whose MODE is tampered (group-writable).
    The OLD code (no lstat guard) would `git tag -v` successfully and TRUST the tag;
    the fixed code fails closed to (None, None). This is the case that actually
    distinguishes the fix — the pure-sidecar class has no tag at HEAD to detect."""

    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-tamper-sig-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.kdir = os.path.join(self.base, "kb")
        os.makedirs(self.kdir)
        self._saved = self.mod.HOME_CONFIG
        self.addCleanup(setattr, self.mod, "HOME_CONFIG", self._saved)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")
        self._build_repo_head_at_tag()

    def _g(self, *a):
        return subprocess.run(["git", "-C", self.repo] + list(a), check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _build_repo_head_at_tag(self):
        self.repo = os.path.join(self.base, "repo")
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        self.key = os.path.join(self.base, "signer")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", self.key,
                        "-C", "release@agentware.test", "-q"], check=True)
        with open(self.key + ".pub") as f:
            keydata = f.read().split()[1]
        for a in (("config", "user.email", "release@agentware.test"),
                  ("config", "user.name", "Agentware Release"),
                  ("config", "gpg.format", "ssh"),
                  ("config", "user.signingkey", self.key + ".pub")):
            self._g(*a)
        with open(os.path.join(self.repo, "f"), "w") as f:
            f.write("v2")
        self._g("add", "-A")
        self._g("commit", "-qm", "c1")
        # HEAD stays AT the signed tag's commit (no rewind) so the reconcile helper
        # detects "HEAD is at a verified newer tag".
        self._g("tag", "-s", "-m", "release v2.0.0", "v2.0.0")
        self.mod._pkg_pin_publisher_key("release@agentware.test", "ssh-ed25519", keydata)

    def test_pkgtamper_reconcile_detects_verified_tag_with_clean_anchor(self):
        # Baseline: with a CLEAN anchor, HEAD-at-a-verified-tag IS detected.
        tag, ver = self.mod._pkg_tag_at_head_if_newer(self.repo, None)
        self.assertEqual(tag, "v2.0.0")
        self.assertIsNotNone(ver)

    def test_pkgtamper_reconcile_refuses_group_writable_anchor(self):
        # The DISCRIMINATOR: the anchor content is the SAME valid key (git tag -v
        # would still pass), but it is group-writable. The lstat guard must fail
        # closed to (None, None) — never finalize against a tamperable anchor.
        os.chmod(self.mod._pkg_allowed_signers_path(), 0o664)
        tag, ver = self.mod._pkg_tag_at_head_if_newer(self.repo, None)
        self.assertIsNone(tag)
        self.assertIsNone(ver)

    def test_pkgtamper_reconcile_refuses_symlinked_anchor(self):
        anchor = self.mod._pkg_allowed_signers_path()
        real = anchor + ".real"
        os.rename(anchor, real)
        os.symlink(real, anchor)          # same content, but now a symlink
        tag, ver = self.mod._pkg_tag_at_head_if_newer(self.repo, None)
        self.assertIsNone(tag)
        self.assertIsNone(ver)


if __name__ == "__main__":
    unittest.main()
