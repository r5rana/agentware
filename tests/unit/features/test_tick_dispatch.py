"""Dedicated-updater tick job tests (feature 260712, Task 8 + 14 accessor).

Covers resolve_updater precedence (env>config>OFF), the ~6h due-gate + always-due
on a crash marker, the session-active defer, last_check_ts recorded on every
attempt, the kill-switch gate (job disabled when OFF), and the crash-window
RECONCILE: an unfinalized apply (pending marker / HEAD at a verified tag newer than
applied) re-smokes and finalizes-on-pass, rolls back+quarantines on fail when
migrate had NOT run, and STRANDS (needs_manual_recovery) when migrate had run
(rollback forbidden post-migrate). Hermetic: signed throwaway repo + patched HOME.

Runner: python3 -m unittest tests.unit.features.test_tick_dispatch -v
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


class _Base(unittest.TestCase):
    _KEYS = ("AGENTWARE_UPDATE",)

    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-tickd-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        self.kdir = os.path.join(self.base, "kb")
        os.makedirs(self.home)
        os.makedirs(self.kdir)
        self._saved_home = self.mod.HOME_CONFIG
        self._saved_cfgs = self.mod.CONFIG_PATHS
        self.addCleanup(self._restore)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")
        self.mod.CONFIG_PATHS = (self.mod.HOME_CONFIG,)
        self._env = {k: os.environ.get(k) for k in self._KEYS}
        os.environ.pop("AGENTWARE_UPDATE", None)

    def _restore(self):
        self.mod.HOME_CONFIG = self._saved_home
        self.mod.CONFIG_PATHS = self._saved_cfgs
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class ResolveAndDueTests(_Base):
    def test_resolve_updater_default_off(self):
        self.assertEqual(self.mod.resolve_updater(), "0")

    def test_resolve_updater_env_wins(self):
        os.environ["AGENTWARE_UPDATE"] = "on"
        self.assertEqual(self.mod.resolve_updater(), "1")

    def test_resolve_updater_config(self):
        os.makedirs(os.path.dirname(self.mod.HOME_CONFIG))
        with open(self.mod.HOME_CONFIG, "w") as f:
            f.write("AGENTWARE_UPDATE=1\n")
        self.assertEqual(self.mod.resolve_updater(), "1")

    def test_due_gate(self):
        now = 1_000_000.0
        self.mod._pkg_state_write({})               # no last_check -> due
        self.assertTrue(self.mod._pkg_updater_due(self.kdir, now))
        self.mod._pkg_state_write(
            {"last_check_ts": self.mod._now_iso()})
        # last check ~now -> not due (well under 6h)
        import time as _t
        self.assertFalse(self.mod._pkg_updater_due(self.kdir, _t.time()))
        # last check 7h ago -> due
        import datetime as _dt
        old = _t.time() - 7 * 3600
        iso = _dt.datetime.fromtimestamp(old, _dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        self.mod._pkg_state_write({"last_check_ts": iso})
        self.assertTrue(self.mod._pkg_updater_due(self.kdir, _t.time()))

    def test_crash_marker_always_due(self):
        import time as _t
        self.mod._pkg_state_write({"last_check_ts": self.mod._now_iso(),
                                   "pending_apply": {"applying_tag": "v2.0.0"}})
        self.assertTrue(self.mod._pkg_updater_due(self.kdir, _t.time()))

    def test_run_defers_on_active_session(self):
        loop = os.path.join(self.kdir, "work", "260712-x", ".loop")
        os.makedirs(loop)
        with open(os.path.join(loop, "live-stream.log"), "w") as f:
            f.write("live")
        r = self.mod._pkg_updater_run(self.kdir, __import__("time").time())
        self.assertEqual(r["status"], "deferred")

    def test_run_records_last_check(self):
        with mock.patch.object(self.mod, "_pkg_run_update",
                               return_value={"status": "no-trust", "detail": "x"}):
            self.mod._pkg_updater_run(self.kdir, __import__("time").time())
        self.assertIn("last_check_ts", self.mod._pkg_state_read())

    def test_job_gated_on_resolve_updater(self):
        # OFF -> dispatcher marks the real updater job disabled.
        res = self.mod._tick_dispatch(self.kdir, now=1000)
        by = {r["job"]: r["status"] for r in res}
        self.assertEqual(by.get("updater"), "disabled")


@unittest.skipUnless(HAVE_SIGNING, "git SSH tag signing unavailable")
class ReconcileTests(_Base):
    def setUp(self):
        super().setUp()
        self._build_repo()
        self.smoke = mock.patch.object(
            self.mod, "_pkg_smoke",
            return_value={"ok": True, "steps": [], "detail": "PASS"}).start()
        # The crash-window reconcile now runs the SAME migrate triage as the normal
        # apply path (feature 260712-p2b-hardening-followups, audit D13), so mock
        # migrate to ok here — these tests isolate the smoke->finalize/rollback/strand
        # control flow; the migrate-refused/error reconcile triage is covered in
        # tests/unit/features/test_pkg_apply_edges.py.
        self.migrate = mock.patch.object(
            self.mod, "_pkg_migrate_after_apply", return_value=("ok", {})).start()
        self.addCleanup(mock.patch.stopall)

    def _g(self, *a):
        return subprocess.run(["git", "-C", self.repo] + list(a), check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _build_repo(self):
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
        self.sha1 = self._g("rev-list", "-n", "1", "v2.0.0").stdout.strip()
        # HEAD LEFT at c1 (v2.0.0's commit) -> the "crashed apply" state.
        self.mod._pkg_pin_publisher_key("release@agentware.test", "ssh-ed25519", keydata)

    def _head(self):
        return self._g("rev-parse", "HEAD").stdout.strip()

    def test_crash_before_smoke_reconciles_finalize(self):
        # pending marker set, migrated false, HEAD at v2.0.0's commit, re-smoke PASS.
        self.mod._pkg_state_write({"last_good_sha": self.sha0,
                                   "last_applied_version": "1.0.0",
                                   "pending_apply": {"applying_tag": "v2.0.0",
                                                     "applying_sha": self.sha1,
                                                     "migrated": False}})
        r = self.mod._pkg_reconcile_pending(self.repo, self.kdir)
        self.assertEqual(r["status"], "reconciled-finalized", r)
        st = self.mod._pkg_state_read()
        self.assertEqual(st["last_applied_version"], "2.0.0")
        self.assertIsNone(st.get("pending_apply"))
        self.assertEqual(self._head(), self.sha1)         # accepted (smoked)

    def test_crash_detected_without_marker_via_head_tag(self):
        # No pending marker, but HEAD is at a verified tag newer than applied.
        self.mod._pkg_state_write({"last_applied_version": "1.0.0",
                                   "last_good_sha": self.sha0})
        r = self.mod._pkg_reconcile_pending(self.repo, self.kdir)
        self.assertEqual(r["status"], "reconciled-finalized")
        self.assertEqual(self.mod._pkg_state_read()["last_applied_version"], "2.0.0")

    def test_reconcile_smoke_fail_premigrate_rolls_back(self):
        self.smoke.return_value = {"ok": False, "steps": [], "detail": "FAIL"}
        self.mod._pkg_state_write({"last_good_sha": self.sha0,
                                   "last_applied_version": "1.0.0",
                                   "pending_apply": {"applying_tag": "v2.0.0",
                                                     "applying_sha": self.sha1,
                                                     "migrated": False}})
        r = self.mod._pkg_reconcile_pending(self.repo, self.kdir)
        self.assertEqual(r["status"], "reconciled-rolledback")
        self.assertEqual(self._head(), self.sha0)          # never accepted unsmoked
        self.assertTrue(self.mod._pkg_is_quarantined("v2.0.0"))

    def test_reconcile_smoke_fail_postmigrate_strands(self):
        self.smoke.return_value = {"ok": False, "steps": [], "detail": "FAIL"}
        self.mod._pkg_state_write({"last_good_sha": self.sha0,
                                   "last_applied_version": "1.0.0",
                                   "pending_apply": {"applying_tag": "v2.0.0",
                                                     "applying_sha": self.sha1,
                                                     "migrated": True}})
        r = self.mod._pkg_reconcile_pending(self.repo, self.kdir)
        self.assertEqual(r["status"], "stranded")
        self.assertTrue(r["needs_manual_recovery"])
        self.assertEqual(self._head(), self.sha1)          # rollback forbidden post-migrate
        self.assertTrue(self.mod._pkg_state_read()["needs_manual_recovery"])

    def test_no_reconcile_when_clean_and_current(self):
        # HEAD at c1 but last_applied already 2.0.0 (finalized) + no marker -> None.
        self._g("reset", "--hard", self.sha0)
        self.mod._pkg_state_write({"last_applied_version": "9.9.9",
                                   "last_good_sha": self.sha0})
        self.assertIsNone(self.mod._pkg_reconcile_pending(self.repo, self.kdir))

    def test_crash_before_ff_clears_marker_no_phantom_version(self):
        # Crash BETWEEN the pre-advance marker write and the ff-apply: HEAD is still
        # the OLD sha, the marker points at a NEVER-APPLIED sha. Reconcile must NOT
        # re-smoke the OLD code and stamp the NEW version (phantom-version bug); it
        # clears the marker WITHOUT advancing last_applied so the tag re-attempts.
        self._g("reset", "--hard", self.sha0)              # HEAD at c0 (old code)
        smoked = {"n": 0}
        real_smoke = self.mod._pkg_smoke

        def counting(*a, **k):
            smoked["n"] += 1
            return real_smoke(*a, **k)

        self.mod._pkg_state_write({"last_good_sha": self.sha0,
                                   "last_applied_version": "1.0.0",
                                   "pending_apply": {"applying_tag": "v2.0.0",
                                                     "applying_sha": self.sha1,
                                                     "migrated": False}})
        self.smoke.side_effect = counting
        r = self.mod._pkg_reconcile_pending(self.repo, self.kdir)
        self.assertEqual(r["status"], "reconciled-cleared", r)
        self.assertEqual(self._head(), self.sha0)          # HEAD unchanged
        st = self.mod._pkg_state_read()
        self.assertEqual(st["last_applied_version"], "1.0.0")   # NOT bumped to 2.0.0
        self.assertIsNone(st.get("pending_apply"))         # stale marker cleared
        self.assertEqual(smoked["n"], 0)                   # never smoked the old code

    def test_none_head_during_reconcile_is_transient_no_mutation(self):
        self.mod._pkg_state_write({"pending_apply": {"applying_tag": "v2.0.0",
                                                     "applying_sha": self.sha1,
                                                     "migrated": False}})
        with mock.patch.object(self.mod, "_git_head", return_value=None):
            r = self.mod._pkg_reconcile_pending(self.repo, self.kdir)
        self.assertEqual(r["status"], "error")
        # marker NOT cleared (transient) -> retried next tick
        self.assertIsNotNone(self.mod._pkg_state_read().get("pending_apply"))

    def test_updater_run_reconcile_holds_the_lock(self):
        # A concurrent apply holds the updater lock -> the tick must NOT reconcile
        # (never race git reset on a LIVE apply's marker); it returns 'locked'.
        self.mod.dream_acquire_lock(self.kdir, pid=1,
                                    lock_rel=self.mod.UPDATER_LOCK_REL)
        self.mod._pkg_state_write({"pending_apply": {"applying_tag": "v2.0.0",
                                                     "applying_sha": self.sha1,
                                                     "migrated": False}})
        import time as _t
        r = self.mod._pkg_updater_run(self.kdir, _t.time(), target=self.repo)
        self.assertEqual(r["status"], "locked")
        self.assertEqual(self._head(), self.sha1)          # untouched (still at c1)


if __name__ == "__main__":
    unittest.main()
