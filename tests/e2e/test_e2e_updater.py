"""End-to-end updater integration (feature 260712, Task 16).

Proves the WHOLE invariant set holds TOGETHER against a hermetic fixture: a copy of
the real package in a throwaway git repo/remote + a throwaway signing key + patched
HOME (NEVER the operator KB or the real package remote, R-LOC-03). Because the target
is a real agentware checkout, smoke + migrate run the ACTUAL on-disk CLI. Skips
cleanly when git SSH signing is unavailable. The self-extension gate chain (steering
lint / eval --record --gate / gate release) runs OUTSIDE this module against the LIVE
package (see the worklog).

Runner: python3 -m unittest tests.e2e.test_e2e_updater -v
"""

from tests._paths import REPO_ROOT

import glob
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

try:
    from tests._fixtures import load_cli, build_synthetic_kb
except ImportError:
    from _fixtures import load_cli, build_synthetic_kb

PIPE, DN = subprocess.PIPE, subprocess.DEVNULL


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


@unittest.skipUnless(HAVE_SIGNING, "git SSH tag signing unavailable")
class E2EUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-e2e-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self._saved = self.mod.HOME_CONFIG
        self.addCleanup(setattr, self.mod, "HOME_CONFIG", self._saved)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")
        self.kdir = os.path.join(self.base, "kb")
        build_synthetic_kb(self.kdir)
        with open(os.path.join(self.kdir, ".initialized"), "w") as f:
            f.write("2026-01-01T00:00:00Z tester\n")
        self._build_pkg()

    def _g(self, *a, check=True):
        return subprocess.run(["git", "-C", self.pkg] + list(a), check=check,
                              stdout=PIPE, stderr=PIPE, text=True)

    def _build_pkg(self):
        src, self.pkg = REPO_ROOT, os.path.join(self.base, "pkg")
        os.makedirs(self.pkg)
        ign = shutil.ignore_patterns("__pycache__", "*.pyc", ".git")
        for item in ("scripts", "steering", "catalog"):
            s = os.path.join(src, item)
            if os.path.isdir(s):
                shutil.copytree(s, os.path.join(self.pkg, item), ignore=ign)
        for item in ("AGENTS.md", "CLAUDE.md"):
            if os.path.isfile(os.path.join(src, item)):
                shutil.copy2(os.path.join(src, item), os.path.join(self.pkg, item))
        bare = os.path.join(self.base, "remote.git")
        subprocess.run(["git", "init", "--bare", "-q", bare], check=True)
        self._g("init", "-q", "-b", "main")
        self._g("remote", "add", "origin", bare)
        self.key = os.path.join(self.base, "signer")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", self.key,
                        "-C", "release@agentware.test", "-q"], check=True)
        self.evil = os.path.join(self.base, "evil")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", self.evil,
                        "-C", "evil@x.test", "-q"], check=True)
        with open(self.key + ".pub") as f:
            self.keydata = f.read().split()[1]
        for a in (("config", "user.email", "release@agentware.test"),
                  ("config", "user.name", "R"), ("config", "gpg.format", "ssh"),
                  ("config", "user.signingkey", self.key + ".pub")):
            self._g(*a)
        self._g("add", "-A")
        self._g("commit", "-qm", "c0")
        self.sha0 = self._g("rev-parse", "HEAD").stdout.strip()
        self._g("push", "-q", "origin", "main")
        self.mod._pkg_pin_publisher_key("release@agentware.test", "ssh-ed25519",
                                        self.keydata)

    def _tag_ahead(self, tag, break_cli=False, key=None, unsigned=False):
        """Create a commit ahead of HEAD, tag it (signed unless unsigned/wrong-key),
        then reset HEAD back so the tag is a ff-apply target. Returns its commit SHA."""
        head = self._g("rev-parse", "HEAD").stdout.strip()
        with open(os.path.join(self.pkg, "RELEASE_%s" % tag.replace(".", "_")), "w") as f:
            f.write(tag)
        if break_cli:
            with open(os.path.join(self.pkg, "scripts", "agentware"), "a") as f:
                f.write("\ndef broken(:\n")   # syntax error -> smoke py_compile FAILS
        self._g("add", "-A")
        self._g("commit", "-qm", "release %s" % tag)
        if unsigned:
            self._g("tag", tag)
        else:
            k = key or self.key
            self._g("-c", "user.signingkey=%s" % (k + ".pub"),
                    "tag", "-s", "-m", "release %s" % tag, tag)
        sha = self._g("rev-list", "-n", "1", tag).stdout.strip()
        self._g("reset", "--hard", head)
        return sha

    def _head(self):
        return self._g("rev-parse", "HEAD").stdout.strip()

    # (1) full verified apply end-to-end -----------------------------------
    def test_1_full_apply(self):
        sha = self._tag_ahead("v2.0.0")
        r = self.mod._pkg_run_update(self.pkg, self.kdir)
        self.assertEqual(r["status"], "applied", r)
        self.assertEqual(self._head(), sha)
        st = self.mod._pkg_state_read()
        self.assertEqual(st["last_good_sha"], sha)
        self.assertEqual(st["last_applied_version"], "2.0.0")
        self.assertIsNone(st.get("pending_apply"))
        # migrate ran -> .schema stamped in the KB
        self.assertTrue(os.path.exists(os.path.join(self.kdir, ".schema")))

    # (2) unsigned + wrong-key refused fail-closed --------------------------
    def test_2_unsigned_and_wrongkey_refused(self):
        self._tag_ahead("v2.0.0", unsigned=True)
        r = self.mod._pkg_run_update(self.pkg, self.kdir)
        self.assertIn(r["status"], ("none", "up-to-date"))
        self.assertEqual(self._head(), self.sha0)          # never applied
        self._tag_ahead("v2.0.1", key=self.evil)           # signed by unpinned key
        r = self.mod._pkg_run_update(self.pkg, self.kdir)
        self.assertEqual(self._head(), self.sha0)
        self.assertTrue(self.mod._pkg_is_quarantined("v2.0.1"))

    # (3) no-downgrade -------------------------------------------------------
    def test_3_downgrade_refused(self):
        self.mod._pkg_state_write({"last_applied_version": "2.0.0"})
        self._tag_ahead("v1.0.0")
        r = self.mod._pkg_run_update(self.pkg, self.kdir)
        self.assertEqual(r["status"], "up-to-date")
        self.assertEqual(self._head(), self.sha0)

    # (4) smoke-fail -> scoped rollback + quarantine + re-smoke -------------
    def test_4_smoke_fail_rolls_back(self):
        self._tag_ahead("v2.0.0", break_cli=True)          # new code fails py_compile
        r = self.mod._pkg_run_update(self.pkg, self.kdir)
        self.assertEqual(r["status"], "rolled-back", r)
        self.assertEqual(self._head(), self.sha0)          # restored to last-good
        self.assertTrue(self.mod._pkg_is_quarantined("v2.0.0"))
        # re-smoke confirmed restoration (the restored CLI parses/runs)
        self.assertTrue(self.mod._pkg_smoke(self.kdir, target=self.pkg)["ok"])

    # (5) lock + session-active guards --------------------------------------
    def test_5_lock_and_session_guard(self):
        self._tag_ahead("v2.0.0")
        # updater lock held (as if a tick is applying) -> a second apply refuses
        self.assertTrue(self.mod.dream_acquire_lock(
            self.kdir, pid=1, lock_rel=self.mod.UPDATER_LOCK_REL))
        r = self.mod._pkg_run_update(self.pkg, self.kdir)
        self.assertEqual(r["status"], "locked")
        self.mod.dream_release_lock(self.kdir, pid=1, lock_rel=self.mod.UPDATER_LOCK_REL)
        # a loop session active -> the tick updater job defers
        loop = os.path.join(self.kdir, "work", "260712-x", ".loop")
        os.makedirs(loop)
        with open(os.path.join(loop, "live-stream.log"), "w") as f:
            f.write("live")
        r = self.mod._pkg_updater_run(self.kdir, time.time(), target=self.pkg)
        self.assertEqual(r["status"], "deferred")
        self.assertEqual(self._head(), self.sha0)

    # (7) crash after ff-apply BEFORE smoke -> reconcile finalizes ----------
    def test_7_crash_before_smoke_reconciles(self):
        sha = self._tag_ahead("v2.0.0")
        self._g("merge", "--ff-only", sha)                 # simulate the ff-advance
        self.mod._pkg_state_write({"last_good_sha": self.sha0,
                                   "last_applied_version": None,
                                   "pending_apply": {"applying_tag": "v2.0.0",
                                                     "applying_sha": sha,
                                                     "migrated": False}})
        r = self.mod._pkg_reconcile_pending(self.pkg, self.kdir)
        self.assertEqual(r["status"], "reconciled-finalized", r)
        self.assertEqual(self._head(), sha)                # accepted (re-smoked)
        self.assertEqual(self.mod._pkg_state_read()["last_applied_version"], "2.0.0")

    # (8) signed v2.0.0 + unsigned v2.0.1 -> apply v2.0.0 + anomaly warning --
    def test_8_anomalous_higher_tag_fallback(self):
        sha200 = self._tag_ahead("v2.0.0")
        self._tag_ahead("v2.0.1", unsigned=True)           # stray unsigned HIGHER tag
        r = self.mod._pkg_run_update(self.pkg, self.kdir)
        self.assertEqual(r["status"], "applied")
        self.assertEqual(r["tag"], "v2.0.0")
        self.assertEqual(self._head(), sha200)
        self.assertTrue(any("anomalous" in w and "v2.0.1" in w for w in r["warnings"]))
        self.assertTrue(self.mod._pkg_is_quarantined("v2.0.1"))

    # (9) unreachable/slow remote -> tick fetch SKIP within timeout ---------
    def test_9_fetch_timeout_graceful_skip(self):
        self._tag_ahead("v2.0.0")
        fake = subprocess.CompletedProcess(["git"], 124, "", "timed out")
        with mock.patch.object(self.mod, "_git_timed", return_value=fake):
            r = self.mod._pkg_run_update(self.pkg, self.kdir)
        self.assertEqual(r["status"], "skipped")
        self.assertEqual(self._head(), self.sha0)          # no hang, no apply

    # (10) read-only HOME during pre-advance write -> NO tree advance -------
    @unittest.skipIf(os.geteuid() == 0 if hasattr(os, "geteuid") else False, "root")
    def test_10_readonly_home_blocks_apply(self):
        self._tag_ahead("v2.0.0")
        real = self.mod._pkg_state_write

        def failing(state):
            pa = state.get("pending_apply")
            if isinstance(pa, dict) and pa.get("applying_tag"):
                raise OSError("disk full")
            return real(state)

        with mock.patch.object(self.mod, "_pkg_state_write", side_effect=failing):
            r = self.mod._pkg_run_update(self.pkg, self.kdir)
        self.assertEqual(r["status"], "error")
        self.assertEqual(self._head(), self.sha0)          # NEVER advanced

    # (11) stale index.lock cleared during rollback ------------------------
    def test_11_stale_index_lock_recovery(self):
        self.mod._pkg_state_write({"last_good_sha": self.sha0})
        self._g("merge", "--ff-only", self._tag_ahead("v2.0.0"))  # advance
        lock = os.path.join(self.pkg, ".git", "index.lock")
        with open(lock, "w") as f:
            f.write("")
        old = time.time() - 10_000
        os.utime(lock, (old, old))
        rb = self.mod._pkg_rollback("v2.0.0", target=self.pkg, kdir=self.kdir)
        self.assertFalse(os.path.exists(lock))
        self.assertEqual(self._head(), self.sha0)

    # (12) a dream cycle never advances REPO_ROOT ---------------------------
    def test_12_dream_never_advances_pkg(self):
        self._tag_ahead("v2.0.0")
        # No dream step references REPO_ROOT (static guard) + HEAD unchanged.
        import inspect
        for _s, _l, fn in self.mod.DREAM_STEP_FUNCS:
            self.assertNotIn("REPO_ROOT", inspect.getsource(fn))
        self.assertEqual(self._head(), self.sha0)

    # (13) crontab cross-preservation --------------------------------------
    def test_13_crontab_cross_preservation(self):
        dream = "0 3 * * * nice -n 10 /x/scripts/agentware dream >> /l 2>&1\n"
        tick = "*/30 * * * * nice -n 10 /x/scripts/agentware tick >> /l 2>&1\n"
        both = dream + tick
        self.assertIn(dream, self.mod._crontab_filter(both, ("agentware", "tick")))
        self.assertIn(tick, self.mod._crontab_filter(both, ("agentware", "dream")))

    # (14) updater OFF -> tick does no fetch/verify/apply -------------------
    def test_14_updater_off_no_op(self):
        self._tag_ahead("v2.0.0")
        # updater OFF (default) -> the job is disabled in the dispatcher.
        res = self.mod._tick_dispatch(self.kdir, now=time.time())
        by = {r["job"]: r["status"] for r in res}
        self.assertEqual(by.get("updater"), "disabled")
        self.assertEqual(self._head(), self.sha0)

    # (6)+(15) staleness pre-hook exits 0 offline; agentware.sh || true -----
    def test_15_staleness_offline_exit_0(self):
        cli = os.path.join(self.pkg, "scripts", "agentware")
        env = dict(os.environ, AGENTWARE_KNOWLEDGE_DIR=self.kdir, HOME=self.home)
        # Offline (no reachable remote change) still exits 0, never blocks.
        cp = subprocess.run([cli, "update", "--staleness-check"], env=env,
                            stdout=PIPE, stderr=PIPE, text=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        # The pre-hook wraps the call with `|| true` (set -e footgun guard).
        # Searched across the whole loop surface: plan task 33 split agentware.sh
        # into a thin orchestrator plus scripts/loop/*.sh, so run_pre_hooks now
        # lives in scripts/loop/hooks.sh. Reading only agentware.sh would assert
        # the guard is missing on a tree where it is present and correct.
        surface = [os.path.join(REPO_ROOT, "agentware.sh")] + sorted(
            glob.glob(os.path.join(REPO_ROOT, "scripts", "loop", "*.sh")))
        sh = ""
        for path in surface:
            with open(path) as f:
                sh += f.read()
        self.assertIn("update --staleness-check || true", sh)


if __name__ == "__main__":
    unittest.main()
