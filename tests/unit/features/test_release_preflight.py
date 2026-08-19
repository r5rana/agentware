"""Release-signing preflight tests (feature 260712, Task 12).

Covers the READ-ONLY go-live readiness check: the hermetic sign->pin->verify->resolve
machinery self-test (throwaway key, no real key/tag/remote), the preflight report
dimensions (publisher-config / client-anchor / machinery), the exit code gated on the
machinery, and go_live_ready when all three hold. Also asserts the docs/release-signing.md
runbook ships and that the command NEVER creates a real tag. Hermetic (R-LOC-03).

Runner: python3 -m unittest tests.unit.features.test_release_preflight -v
"""

from tests._paths import REPO_ROOT

import os
import shutil
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, run_cli
except ImportError:
    from _fixtures import load_cli, run_cli



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
        DN = subprocess.DEVNULL
        for a in (["init", "-q", "-b", "main"], ["config", "user.email", "p@t"],
                  ["config", "user.name", "P"], ["config", "gpg.format", "ssh"],
                  ["config", "user.signingkey", key + ".pub"]):
            subprocess.run(["git", "-C", repo] + a, check=False, stdout=DN, stderr=DN)
        with open(os.path.join(repo, "f"), "w") as f:
            f.write("x")
        subprocess.run(["git", "-C", repo, "add", "-A"], check=False, stdout=DN, stderr=DN)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "s"], check=False, stdout=DN, stderr=DN)
        return subprocess.run(["git", "-C", repo, "tag", "-s", "-m", "t", "v0.0.1"],
                              check=False, stdout=DN, stderr=DN).returncode == 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


HAVE_SIGNING = _have_signing()


class RunbookTests(unittest.TestCase):
    def test_runbook_ships_and_covers_the_essentials(self):
        path = os.path.join(REPO_ROOT, "docs", "release-signing.md")
        self.assertTrue(os.path.isfile(path), "docs/release-signing.md must ship")
        with open(path) as f:
            text = f.read()
        for token in ("allowed_signers", "gate release --full", "tag.gpgsign",
                      "trust pin", "rotate", "revocation", "OPERATOR-GATED",
                      "release preflight", "v2.0.0"):
            self.assertIn(token, text, "runbook must document %r" % token)


@unittest.skipUnless(HAVE_SIGNING, "git SSH tag signing unavailable")
class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-pre-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self._saved_home = self.mod.HOME_CONFIG
        self._saved_repo = self.mod.REPO_ROOT
        self.addCleanup(self._restore)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")

    def _restore(self):
        self.mod.HOME_CONFIG = self._saved_home
        self.mod.REPO_ROOT = self._saved_repo

    def test_selftest_passes(self):
        ok, detail = self.mod._pkg_preflight_selftest()
        self.assertTrue(ok, detail)
        self.assertIn("OK", detail)

    def test_preflight_machinery_gates_exit_code(self):
        ok, report = self.mod._pkg_release_preflight()
        self.assertTrue(ok)      # machinery works on this box
        names = {c["name"] for c in report["checks"]}
        self.assertEqual(names, {"publisher-signing-config", "client-trust-anchor",
                                 "hermetic-verify-selftest"})

    def test_go_live_ready_when_all_three_hold(self):
        # Build a publisher-configured signed repo + pin its key -> go_live_ready.
        repo = os.path.join(self.base, "repo")
        os.makedirs(repo)

        def g(*a):
            return subprocess.run(["git", "-C", repo] + list(a), check=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        g("init", "-q", "-b", "main")
        key = os.path.join(self.base, "signer")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key,
                        "-C", "release@agentware.test", "-q"], check=True)
        with open(key + ".pub") as f:
            keydata = f.read().split()[1]
        for a in (("config", "user.email", "release@agentware.test"),
                  ("config", "user.name", "R"), ("config", "gpg.format", "ssh"),
                  ("config", "tag.gpgsign", "true"),
                  ("config", "user.signingkey", key + ".pub")):
            g(*a)
        self.mod.REPO_ROOT = repo
        self.mod._pkg_pin_publisher_key("release@agentware.test", "ssh-ed25519", keydata)
        ok, report = self.mod._pkg_release_preflight()
        self.assertTrue(ok)
        self.assertTrue(report["publisher_ready"])
        self.assertTrue(report["client_ready"])
        self.assertTrue(report["go_live_ready"])

    def test_cli_preflight_exit_0_and_no_real_tag(self):
        before = subprocess.run(["git", "-C", REPO_ROOT, "tag", "-l"],
                                stdout=subprocess.PIPE, text=True).stdout
        code, out, _e = run_cli(["release", "preflight"], self.base)
        self.assertEqual(code, 0)
        self.assertIn("READY", out)
        after = subprocess.run(["git", "-C", REPO_ROOT, "tag", "-l"],
                               stdout=subprocess.PIPE, text=True).stdout
        self.assertEqual(before, after, "preflight must NEVER create a real tag")


if __name__ == "__main__":
    unittest.main()
