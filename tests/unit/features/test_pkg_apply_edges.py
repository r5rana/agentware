"""Apply-path edge cases (feature 260712-p2b-hardening-followups, Task 4 —
audit D8/D13). Sibling to test_pkg_updater.py (the happy non-reorderable sequence);
this module covers the EDGES a normal apply does not exercise:

  D8  — an ff-only apply to a version-newer tag whose COMMIT is already an ANCESTOR
        of HEAD (git "Already up to date") is a BENIGN idempotent no-op, NOT a
        rollback: status 'up-to-date', last_applied recorded (so the resolver's
        no-downgrade stops the per-tick thrash), HEAD unchanged, smoke/migrate NOT
        run, no quarantine.
  D13 — the crash-window reconcile finalize branch runs the SAME migrate triage as
        the normal apply path: migrate ok -> reconciled-finalized; refused ->
        reconciled-migrate-workorder (package KEPT); error -> reconciled-migrate-error
        (package KEPT, loud).

Hermetic: throwaway signed repo + patched HOME (R-LOC-03); skips without git SSH
signing. `test_pkgapplyedges_` is a unique -k prefix across the suite.
Runner: python3 -m unittest tests.unit.features.test_pkg_apply_edges -v
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

try:
    from tests._fixtures import load_cli, run_cli
except ImportError:
    from _fixtures import load_cli, run_cli

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


class _SignedRepoBase(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-applyedge-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.kdir = os.path.join(self.base, "kb")
        os.makedirs(self.kdir)
        self._saved = self.mod.HOME_CONFIG
        self.addCleanup(setattr, self.mod, "HOME_CONFIG", self._saved)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")

    def _g(self, *a):
        return subprocess.run(["git", "-C", self.repo] + list(a), check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _head(self):
        return self._g("rev-parse", "HEAD").stdout.strip()

    def _sign_config(self, keydata):
        for a in (("config", "user.email", "release@agentware.test"),
                  ("config", "user.name", "Agentware Release"),
                  ("config", "gpg.format", "ssh"),
                  ("config", "user.signingkey", self.key + ".pub")):
            self._g(*a)
        self.mod._pkg_pin_publisher_key("release@agentware.test", "ssh-ed25519", keydata)


@unittest.skipUnless(HAVE_SIGNING, "git SSH tag signing unavailable")
class ApplyIdempotentTests(_SignedRepoBase):
    """D8: HEAD sits AHEAD of the resolved tag's commit (the tag is an ancestor)."""

    def setUp(self):
        super().setUp()
        self._build_repo_head_ahead()
        self.smoke = mock.patch.object(
            self.mod, "_pkg_smoke",
            return_value={"ok": True, "steps": [], "detail": "PASS"}).start()
        self.migrate = mock.patch.object(
            self.mod, "_pkg_migrate_after_apply", return_value=("ok", {})).start()
        self.addCleanup(mock.patch.stopall)

    def _build_repo_head_ahead(self):
        bare = os.path.join(self.base, "remote.git")
        subprocess.run(["git", "init", "--bare", "-q", bare], check=True)
        self.repo = os.path.join(self.base, "repo")
        subprocess.run(["git", "clone", "-q", bare, self.repo], check=True,
                       stdout=DEVNULL, stderr=DEVNULL)
        self.key = os.path.join(self.base, "signer")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", self.key,
                        "-C", "release@agentware.test", "-q"], check=True)
        with open(self.key + ".pub") as f:
            keydata = f.read().split()[1]
        self._sign_config(keydata)
        with open(os.path.join(self.repo, "f"), "w") as f:
            f.write("v1")
        self._g("add", "-A")
        self._g("commit", "-qm", "c0")
        self.sha0 = self._head()
        self._g("push", "-q", "origin", "main")
        # Signed tag v2.0.0 AT c0, then a DESCENDANT commit c1 that HEAD stays on:
        # v2.0.0's commit is now an ANCESTOR of HEAD (already contained).
        self._g("tag", "-s", "-m", "release v2.0.0", "v2.0.0")
        self.tag_sha = self._g("rev-list", "-n", "1", "v2.0.0").stdout.strip()
        with open(os.path.join(self.repo, "f"), "w") as f:
            f.write("v2")
        self._g("add", "-A")
        self._g("commit", "-qm", "c1")
        self.sha1 = self._head()

    def test_pkgapplyedges_ancestor_contained_ff_is_idempotent(self):
        self.assertEqual(self.tag_sha, self.sha0)              # tag is at c0
        self.assertTrue(self.mod._is_ancestor(self.repo, self.sha0, self.sha1))
        r = self.mod._pkg_run_update(self.repo, self.kdir)
        self.assertEqual(r["status"], "up-to-date", r)         # NOT 'rolled-back'
        self.assertEqual(self._head(), self.sha1)              # HEAD unchanged
        st = self.mod._pkg_state_read()
        self.assertEqual(st["last_applied_version"], "2.0.0")  # recorded -> no thrash
        self.assertIsNone(st.get("pending_apply"))             # marker cleared
        self.smoke.assert_not_called()                         # tree never moved
        self.migrate.assert_not_called()

    def test_pkgapplyedges_idempotent_no_thrash_on_second_run(self):
        self.mod._pkg_run_update(self.repo, self.kdir)
        r2 = self.mod._pkg_run_update(self.repo, self.kdir)
        # After last_applied=2.0.0, the resolver reports up-to-date (no-downgrade),
        # so a second tick neither rolls back nor re-applies.
        self.assertEqual(r2["status"], "up-to-date", r2)
        self.assertEqual(self._head(), self.sha1)


@unittest.skipUnless(HAVE_SIGNING, "git SSH tag signing unavailable")
class ReconcileMigrateTriageTests(_SignedRepoBase):
    """D13: the crash-window reconcile finalize runs the migrate triage."""

    def setUp(self):
        super().setUp()
        self._build_repo_head_at_tag()
        self.smoke = mock.patch.object(
            self.mod, "_pkg_smoke",
            return_value={"ok": True, "steps": [], "detail": "PASS"}).start()
        self.migrate = mock.patch.object(
            self.mod, "_pkg_migrate_after_apply", return_value=("ok", {})).start()
        self.addCleanup(mock.patch.stopall)

    def _build_repo_head_at_tag(self):
        self.repo = os.path.join(self.base, "repo")
        os.makedirs(self.repo)
        self._g("init", "-q", "-b", "main")
        self.key = os.path.join(self.base, "signer")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", self.key,
                        "-C", "release@agentware.test", "-q"], check=True)
        with open(self.key + ".pub") as f:
            keydata = f.read().split()[1]
        self._sign_config(keydata)
        with open(os.path.join(self.repo, "f"), "w") as f:
            f.write("v1")
        self._g("add", "-A")
        self._g("commit", "-qm", "c0")
        self.sha0 = self._head()
        with open(os.path.join(self.repo, "f"), "w") as f:
            f.write("v2")
        self._g("add", "-A")
        self._g("commit", "-qm", "c1")
        self._g("tag", "-s", "-m", "release v2.0.0", "v2.0.0")
        self.sha1 = self._g("rev-list", "-n", "1", "v2.0.0").stdout.strip()
        # HEAD LEFT at c1 (v2.0.0's commit): the "crashed after ff, before finalize".

    def _set_pending(self, migrated=False):
        self.mod._pkg_state_write({"last_good_sha": self.sha0,
                                   "pending_apply": {"applying_tag": "v2.0.0",
                                                     "applying_sha": self.sha1,
                                                     "migrated": migrated}})

    def test_pkgapplyedges_reconcile_migrate_ok_finalizes(self):
        self._set_pending()
        r = self.mod._pkg_reconcile_pending(self.repo, self.kdir)
        self.assertEqual(r["status"], "reconciled-finalized", r)
        self.migrate.assert_called_once()                       # migrate DID run
        st = self.mod._pkg_state_read()
        self.assertEqual(st["last_applied_version"], "2.0.0")
        self.assertIsNone(st.get("pending_apply"))

    def test_pkgapplyedges_reconcile_migrate_refused_workorder(self):
        self.migrate.return_value = ("refused", {"refused": {"name": "restructure-x"}})
        self._set_pending()
        r = self.mod._pkg_reconcile_pending(self.repo, self.kdir)
        self.assertEqual(r["status"], "reconciled-migrate-workorder", r)
        self.assertIn("restructure-x", r["detail"])
        self.assertEqual(self._head(), self.sha1)               # package KEPT
        st = self.mod._pkg_state_read()
        self.assertEqual(st["last_applied_version"], "2.0.0")   # finalized
        self.assertIsNone(st.get("pending_apply"))

    def test_pkgapplyedges_reconcile_migrate_error_kept_loud(self):
        self.migrate.return_value = ("error", {"detail": "older-CLI guard tripped"})
        self._set_pending()
        r = self.mod._pkg_reconcile_pending(self.repo, self.kdir)
        self.assertEqual(r["status"], "reconciled-migrate-error", r)
        self.assertIn("older-CLI", r["detail"])
        self.assertEqual(self._head(), self.sha1)               # package KEPT
        st = self.mod._pkg_state_read()
        self.assertEqual(st["last_applied_version"], "2.0.0")

    def test_pkgapplyedges_reconcile_smoke_fail_still_rolls_back(self):
        # Regression: a re-smoke FAIL pre-migrate must still roll back (migrate is
        # only reached on smoke PASS) — the D13 change must not swallow this.
        self.smoke.return_value = {"ok": False, "steps": [], "detail": "FAIL"}
        self._set_pending()
        r = self.mod._pkg_reconcile_pending(self.repo, self.kdir)
        self.assertEqual(r["status"], "reconciled-rolledback", r)
        self.migrate.assert_not_called()                        # never reached

    def test_pkgapplyedges_reconcile_stamps_migrated_before_migrate(self):
        # Brick-ordering: the reconcile smoke-ok branch MUST persist
        # pending_apply.migrated=True BEFORE calling migrate (which advances the KB
        # schema). Then a crash during/after migrate leaves migrated=True, so a later
        # reconcile STRANDS loudly instead of doing a FORBIDDEN post-migrate reset.
        seen = {}

        def _capture(target, kdir):
            st = self.mod._pkg_state_read()
            seen["migrated"] = (st.get("pending_apply") or {}).get("migrated")
            return ("ok", {})
        self.migrate.side_effect = _capture
        self._set_pending(migrated=False)
        self.mod._pkg_reconcile_pending(self.repo, self.kdir)
        self.assertIs(seen.get("migrated"), True,
                      "migrated=True must be persisted BEFORE migrate runs")

    def test_pkgapplyedges_selfheal_migrate_error_exits_nonzero(self):
        # A migrate ERROR during self-heal must surface as a FAILURE (rc!=0), not be
        # masked as success — it does not set needs_manual_recovery.
        self.migrate.return_value = ("error", {"detail": "older-CLI guard tripped"})
        self._set_pending()
        with mock.patch.object(self.mod, "REPO_ROOT", self.repo):
            code, out, _e = run_cli(["update", "--self-heal"], self.kdir)
        self.assertNotEqual(code, 0, out)

    def test_pkgapplyedges_update_reconcile_workorder_exits_zero(self):
        # A crash-window reconcile that finalizes with a migrate WORKORDER is a
        # SUCCESS (package kept) — `update` must exit 0, matching the normal-path
        # migrate-workorder (not exit 1 from the raw reconciled- prefix).
        self.migrate.return_value = ("refused", {"refused": {"name": "restructure"}})
        self._set_pending()
        with mock.patch.object(self.mod, "REPO_ROOT", self.repo):
            code, out, _e = run_cli(["update"], self.kdir)
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
