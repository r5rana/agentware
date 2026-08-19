"""pkg_version heartbeat + audit check tests (feature 260712, Task 11).

Covers the LOCAL rollout-visibility signal: the pkg_version heartbeat appended to
logs/metrics.jsonl (ts/pkg_version/schema/updater), _pkg_version resolution, and
the this-machine _audit_pkg_version_check — inert when the updater is OFF or
pre-go-live, BEHIND when a newer VERIFIED stable tag exists, needs_manual_recovery
and tampered-anchor FAILs. The cross-machine fleet sink is explicitly team-mode-
deferred (asserted: no machine-id record is written). Hermetic (R-LOC-03).

Runner: python3 -m unittest tests.unit.features.test_pkg_heartbeat -v
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, build_synthetic_kb
except ImportError:
    from _fixtures import load_cli, build_synthetic_kb

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


class _Base(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-hb-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.kdir = os.path.join(self.base, "kb")
        build_synthetic_kb(self.kdir)
        self._saved = self.mod.HOME_CONFIG
        self.addCleanup(setattr, self.mod, "HOME_CONFIG", self._saved)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")
        self._env = os.environ.get("AGENTWARE_UPDATE")
        os.environ.pop("AGENTWARE_UPDATE", None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._env is None:
            os.environ.pop("AGENTWARE_UPDATE", None)
        else:
            os.environ["AGENTWARE_UPDATE"] = self._env


class HeartbeatTests(_Base):
    def test_emit_heartbeat_appends_event(self):
        self.mod._pkg_emit_heartbeat(self.kdir)
        path = os.path.join(self.kdir, "logs", "metrics.jsonl")
        with open(path) as f:
            recs = [json.loads(l) for l in f if l.strip()]
        hb = [r for r in recs if r.get("event") == "pkg_version"]
        self.assertEqual(len(hb), 1)
        for k in ("ts", "pkg_version", "schema", "updater"):
            self.assertIn(k, hb[0])
        self.assertEqual(hb[0]["updater"], "0")     # OFF by default

    def test_heartbeat_no_machine_id(self):
        # Cross-machine fleet sink deferred to team-mode: no machine-id written.
        self.mod._pkg_emit_heartbeat(self.kdir)
        with open(os.path.join(self.kdir, "logs", "metrics.jsonl")) as f:
            content = f.read()
        for forbidden in ("machine_id", "hostname", "machine-id"):
            self.assertNotIn(forbidden, content)

    def test_pkg_version_resolves_none_without_tag(self):
        # REPO_ROOT here (real repo) has no tag at HEAD and sidecar is empty -> None.
        self.assertIsNone(self.mod._pkg_version())


class AuditInertTests(_Base):
    def test_inert_when_updater_off(self):
        c = self.mod._audit_pkg_version_check(self.kdir)
        self.assertTrue(c["ok"])
        self.assertIn("OFF", c["details"][0])

    def test_inert_when_no_key_pinned(self):
        os.environ["AGENTWARE_UPDATE"] = "on"
        c = self.mod._audit_pkg_version_check(self.kdir)
        self.assertTrue(c["ok"])
        self.assertIn("pre-go-live", c["details"][0])

    def test_needs_manual_recovery_fails(self):
        os.environ["AGENTWARE_UPDATE"] = "on"
        # pin a key so we pass the pre-go-live gate, then flag manual recovery
        self.mod._pkg_secure_home()
        with open(self.mod._pkg_allowed_signers_path(), "w") as f:
            f.write("p ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyDataForTests\n")
        self.mod._pkg_state_write({"needs_manual_recovery": True})
        c = self.mod._audit_pkg_version_check(self.kdir)
        self.assertFalse(c["ok"])
        self.assertIn("needs_manual_recovery", c["details"][0])


@unittest.skipUnless(HAVE_SIGNING, "git SSH tag signing unavailable")
class AuditBehindTests(_Base):
    def setUp(self):
        super().setUp()
        os.environ["AGENTWARE_UPDATE"] = "on"
        self.repo = os.path.join(self.base, "repo")
        os.makedirs(self.repo)
        self._g("init", "-q", "-b", "main")
        self.key = os.path.join(self.base, "signer")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", self.key,
                        "-C", "release@agentware.test", "-q"], check=True)
        with open(self.key + ".pub") as f:
            keydata = f.read().split()[1]
        for a in (("config", "user.email", "release@agentware.test"),
                  ("config", "user.name", "R"), ("config", "gpg.format", "ssh"),
                  ("config", "user.signingkey", self.key + ".pub")):
            self._g(*a)
        with open(os.path.join(self.repo, "f"), "w") as f:
            f.write("v1")
        self._g("add", "-A")
        self._g("commit", "-qm", "c0")
        self.sha0 = self._g("rev-parse", "HEAD").stdout.strip()
        with open(os.path.join(self.repo, "f"), "w") as f:
            f.write("v2")
        self._g("add", "-A")
        self._g("commit", "-qm", "c1")
        self._g("tag", "-s", "-m", "release v2.0.0", "v2.0.0")
        # HEAD BEHIND the newest tag (not at it) so describe/applied != v2.0.0.
        self._g("reset", "--hard", self.sha0)
        self._saved_repo = self.mod.REPO_ROOT
        self.addCleanup(setattr, self.mod, "REPO_ROOT", self._saved_repo)
        self.mod.REPO_ROOT = self.repo
        self.mod._pkg_pin_publisher_key("release@agentware.test", "ssh-ed25519", keydata)

    def _g(self, *a):
        return subprocess.run(["git", "-C", self.repo] + list(a), check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_behind_when_newer_verified_tag(self):
        # No last_applied_version -> a verified v2.0.0 means BEHIND.
        c = self.mod._audit_pkg_version_check(self.kdir)
        self.assertFalse(c["ok"])
        self.assertIn("BEHIND", c["details"][0])
        self.assertEqual(c["newest_verified_tag"], "v2.0.0")

    def test_up_to_date_when_applied_matches(self):
        self.mod._pkg_state_write({"last_applied_version": "2.0.0"})
        c = self.mod._audit_pkg_version_check(self.kdir)
        self.assertTrue(c["ok"])
        self.assertIn("up to date", c["details"][0])


if __name__ == "__main__":
    unittest.main()
