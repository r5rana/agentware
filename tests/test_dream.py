"""Tests for dream mode (feature 260627-dream-mode).

Phase 1 = deterministic, idle-gated, unattended KB maintenance. These tests use
SYNTHETIC fixtures + a patched HOME and NEVER touch the operator's real KB or
config (R-LOC-03). Deterministic + stdlib-only: wall-clock and pid are injected
(now=/pid=) so the idle-gate and lock logic are exercised without real processes
or sleeping.

Task 3 covers the idle-gate + concurrency-lock primitives. Later tasks extend
this module (the full cycle, --dry-run, journal, the destructive-op guard).
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests._fixtures import load_cli, build_synthetic_kb, run_cli


def _touch(path, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8"):
        pass
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class DreamSessionGateTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-")
        self.addCleanup(__import__("shutil").rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.now = 1_000_000.0  # fixed synthetic clock

    def _loop_dir(self, feature):
        return os.path.join(self.kdir, "work", feature, ".loop")

    def test_no_work_dir_is_idle(self):
        self.assertIsNone(self.mod.is_session_active(self.kdir, now=self.now))

    def test_fresh_live_stream_is_active(self):
        d = self._loop_dir("260627-other")
        _touch(os.path.join(d, "live-stream.log"), mtime=self.now - 5)
        self.assertEqual(
            self.mod.is_session_active(self.kdir, now=self.now), "260627-other")

    def test_done_feature_is_not_active(self):
        d = self._loop_dir("260627-finished")
        _touch(os.path.join(d, "live-stream.log"), mtime=self.now - 5)
        _touch(os.path.join(d, ".done"))
        self.assertIsNone(self.mod.is_session_active(self.kdir, now=self.now))

    def test_stale_live_stream_is_not_active(self):
        d = self._loop_dir("260627-idle")
        old = self.now - self.mod.DREAM_SESSION_TTL_SEC - 60
        _touch(os.path.join(d, "live-stream.log"), mtime=old)
        self.assertIsNone(self.mod.is_session_active(self.kdir, now=self.now))

    def test_iteration_marker_also_counts(self):
        d = self._loop_dir("260627-iter")
        _touch(os.path.join(d, ".iteration"), mtime=self.now - 1)
        self.assertEqual(
            self.mod.is_session_active(self.kdir, now=self.now), "260627-iter")

    def test_gate_reason_skips_on_active_session(self):
        d = self._loop_dir("260627-busy")
        _touch(os.path.join(d, "live-stream.log"), mtime=self.now - 1)
        reason = self.mod.dream_gate_reason(self.kdir, now=self.now)
        self.assertIsNotNone(reason)
        self.assertIn("loop session is active", reason)

    def test_force_bypasses_idle_gate(self):
        d = self._loop_dir("260627-busy")
        _touch(os.path.join(d, "live-stream.log"), mtime=self.now - 1)
        # force ignores idle checks (manual operator run) but not the lock.
        self.assertIsNone(
            self.mod.dream_gate_reason(self.kdir, force=True, now=self.now))

    def test_gate_reason_skips_on_high_load(self):
        # Inject a high-load tuple so the test never depends on real machine load.
        reason = self.mod.dream_gate_reason(
            self.kdir, now=self.now, load=(True, 99.0, 8.0))
        self.assertIsNotNone(reason)
        self.assertIn("load is high", reason)

    def test_gate_reason_none_when_idle_and_low_load(self):
        reason = self.mod.dream_gate_reason(
            self.kdir, now=self.now, load=(False, 0.1, 8.0))
        self.assertIsNone(reason)


class DreamLockTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-")
        self.addCleanup(__import__("shutil").rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.now = 1_000_000.0

    def test_acquire_then_foreign_live_lock_fails(self):
        # PID 1 (init) is always alive and is not us => a fresh foreign lock blocks.
        self.assertTrue(
            self.mod.dream_acquire_lock(self.kdir, now=self.now, pid=1))
        self.assertFalse(
            self.mod.dream_acquire_lock(self.kdir, now=self.now, pid=424242))

    def test_reclaims_dead_pid_lock(self):
        # A very high pid is (essentially certainly) dead => reclaimable.
        dead = 2_000_000_000
        self.assertTrue(
            self.mod.dream_acquire_lock(self.kdir, now=self.now, pid=dead))
        # Even though a lock file exists, a different live pid reclaims it because
        # the recorded owner is dead.
        self.assertTrue(
            self.mod.dream_acquire_lock(self.kdir, now=self.now, pid=1))

    def test_reclaims_expired_mtime_lock(self):
        self.assertTrue(self.mod.dream_acquire_lock(self.kdir, now=self.now, pid=1))
        # Same live owner pid, but the timestamp is far in the past => stale => reclaim.
        later = self.now + self.mod.DREAM_LOCK_TTL_SEC + 60
        self.assertTrue(self.mod.dream_acquire_lock(self.kdir, now=later, pid=1))

    def test_release_only_when_owned(self):
        self.mod.dream_acquire_lock(self.kdir, now=self.now, pid=4242)
        # A foreign pid must NOT remove our lock.
        self.mod.dream_release_lock(self.kdir, pid=9999)
        self.assertTrue(os.path.isfile(self.mod._dream_lock_path(self.kdir)))
        # The owner releases it.
        self.mod.dream_release_lock(self.kdir, pid=4242)
        self.assertFalse(os.path.isfile(self.mod._dream_lock_path(self.kdir)))

    def test_lock_path_is_under_gitignored_cache(self):
        path = self.mod._dream_lock_path(self.kdir)
        self.assertIn(os.path.join(".cache", "dream.lock"), path)

    def test_lockfile_is_git_ignored_in_a_tracked_kb(self):
        # Scaffold the KB .gitignore exactly as `init` would, then confirm git
        # check-ignore matches the lock path (so step f never stages it).
        self.mod._ensure_kb_gitignore(self.kdir)
        subprocess.run(["git", "init", "-q"], cwd=self.kdir, check=True)
        self.mod.dream_acquire_lock(self.kdir, now=self.now, pid=1)
        rel = os.path.relpath(self.mod._dream_lock_path(self.kdir), self.kdir)
        r = subprocess.run(["git", "check-ignore", rel], cwd=self.kdir,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, "lock path should be gitignored: %r" % rel)


def _snapshot_tree(root):
    """Map every file under `root` -> bytes (for an exact before/after diff)."""
    snap = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(base, name)
            try:
                with open(p, "rb") as f:
                    snap[p] = f.read()
            except OSError:
                pass
    return snap


def _seed_gold(kdir):
    """Write a small synthetic gold set so eval --record has something to score."""
    bench = os.path.join(kdir, "benchmarks")
    os.makedirs(bench, exist_ok=True)
    gold = [
        {"query": "geofence arrive reminders",
         "expected_ids": ["learn-geofence-reminders"]},
        {"query": "gtimeout coreutils command",
         "expected_ids": ["learn-macos-no-timeout"]},
        {"query": "saturation inverse document frequency",
         "expected_ids": ["ref-bm25-ranking"]},
        {"query": "python stdlib runtime dependency",
         "expected_ids": ["config-python-runtime"]},
    ]
    with open(os.path.join(bench, "recall-gold.json"), "w", encoding="utf-8") as f:
        json.dump(gold, f)


def _migrate_kb(kdir):
    """Make a freshly-built synthetic KB frontmatter-complete so `index rebuild`
    (dream step a) is a clean no-op — mirrors the real KB, whose entry files all
    carry frontmatter (the rebuild source of truth). Without this, step a errors
    with 'missing frontmatter id' exactly as `index rebuild` does on a raw KB."""
    run_cli(["index", "migrate-frontmatter"], kdir)
    run_cli(["index", "rebuild"], kdir)


class _GuardedEnv:
    """Mixin: pin AGENTWARE_NESTED_UNITTEST (so the cycle's audit/eval steps never
    recursively shell the whole suite), ISOLATE the config (so resolve_dream /
    resolve_dream_schedule / resolve_kb_autocommit never fall through to the
    operator's real ~/.agentware/config.env — R-LOC-03 + determinism), and restore
    everything afterward."""

    _KEYS = ("AGENTWARE_NESTED_UNITTEST", "AGENTWARE_DREAM",
             "AGENTWARE_DREAM_SCHEDULE", "HOME", "AGENTWARE_KB_AUTOCOMMIT")

    def _save_env(self):
        self._env = {k: os.environ.get(k) for k in self._KEYS}
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"
        # Redirect the module's config file to a throwaway path so NO real
        # operator setting leaks into a test (CONFIG_PATHS is import-time-bound to
        # the real HOME, so patching os.environ['HOME'] alone does not isolate it).
        self._mod = load_cli()
        self._cfgdir = tempfile.mkdtemp(prefix="agentware-dream-cfg-")
        cfg = os.path.join(self._cfgdir, "config.env")
        self._saved_cfg = (self._mod.HOME_CONFIG, self._mod.CONFIG_PATHS)
        self._mod.HOME_CONFIG = cfg
        self._mod.CONFIG_PATHS = (cfg,)

    def _restore_env(self):
        self._mod.HOME_CONFIG, self._mod.CONFIG_PATHS = self._saved_cfg
        shutil.rmtree(self._cfgdir, ignore_errors=True)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class DreamCycleTests(unittest.TestCase, _GuardedEnv):
    """The `dream` command: dry-run, full cycle, idle-gate, idempotence, guard."""

    def setUp(self):
        self._save_env()
        self.addCleanup(self._restore_env)
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-cycle-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        # The synthetic entry files carry no frontmatter; `index migrate` stamps
        # it so rebuild_kb (frontmatter-driven) can reconstruct the index.
        run_cli(["index", "migrate-frontmatter"], self.kdir)
        _seed_gold(self.kdir)
        _migrate_kb(self.kdir)

    def _run(self, argv):
        return run_cli(["dream"] + argv, self.kdir)

    def test_dry_run_mutates_nothing(self):
        before = _snapshot_tree(self.kdir)
        code, out, err = self._run(["--dry-run", "--format", "json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(before, _snapshot_tree(self.kdir),
                         "dry-run must not mutate the KB tree")
        payload = json.loads(out)
        self.assertTrue(payload["dry_run"])
        self.assertEqual([s["step"] for s in payload["steps"]],
                         ["a", "b", "c", "d", "e", "f"])
        for s in payload["steps"]:
            self.assertEqual(s["status"], "planned")

    def test_steps_subset_returns_per_step_result(self):
        code, out, err = self._run(["--steps", "a", "--format", "json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual([s["step"] for s in payload["steps"]], ["a"])
        self.assertEqual(payload["steps"][0]["status"], "ok")
        self.assertIn("duration_s", payload["steps"][0])

    def test_unknown_step_errors(self):
        code, _out, err = self._run(["--steps", "z"])
        self.assertEqual(code, 2)
        self.assertIn("unknown dream step", err)

    def test_audit_step_ignores_self_referential_dream_health(self):
        # With dream ON and no prior cycle, dream_health is red — but step c must
        # NOT fail on it (it's circular to audit "did a dream run?" mid-cycle).
        os.environ["AGENTWARE_DREAM"] = "1"
        code, out, err = self._run(["--steps", "c", "--format", "json"])
        self.assertEqual(code, 0, err)
        c = json.loads(out)["steps"][0]
        self.assertEqual(c["status"], "ok")
        self.assertEqual(c["ignored_checks"], "dream_health")

    def test_full_cycle_records_one_row(self):
        ledger = os.path.join(self.kdir, "benchmarks", "history.jsonl")
        code, out, err = self._run(["--format", "json"])
        self.assertEqual(code, 0, err)
        by = {s["step"]: s for s in json.loads(out)["steps"]}
        self.assertEqual(by["a"]["status"], "ok")
        self.assertEqual(by["d"]["status"], "ok")
        self.assertEqual(by["d"]["rows_added"], 1)
        self.assertEqual(by["e"]["status"], "ok")
        self.assertEqual(by["f"]["status"], "skipped")  # non-git KB
        mod = load_cli()
        self.assertEqual(len(mod._read_ledger(ledger)), 1)

    def test_idempotent_one_row_one_journal_per_cycle(self):
        mod = load_cli()
        idx = os.path.join(self.kdir, "index.json")
        ledger = os.path.join(self.kdir, "benchmarks", "history.jsonl")
        journal = os.path.join(self.kdir, mod.DREAM_JOURNAL_REL)
        self._run(["--format", "json"])
        fp1, j1 = mod._dream_file_fp(idx), open(journal).read().count("## dream ")
        r1 = len(mod._read_ledger(ledger))
        self._run(["--format", "json"])
        fp2, j2 = mod._dream_file_fp(idx), open(journal).read().count("## dream ")
        r2 = len(mod._read_ledger(ledger))
        self.assertEqual(fp1, fp2, "index rebuild must be byte-stable (idempotent)")
        self.assertEqual(j2 - j1, 1, "exactly one new journal entry per cycle")
        self.assertEqual(r2 - r1, 1, "exactly one new reliability row per cycle")

    def test_idle_gate_skips_and_mutates_nothing(self):
        loop = os.path.join(self.kdir, "work", "260627-live", ".loop")
        _touch(os.path.join(loop, "live-stream.log"))
        before = _snapshot_tree(self.kdir)
        code, out, err = self._run(["--format", "json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["skipped"])
        self.assertEqual(payload["skip_kind"], "idle-gate")
        self.assertEqual(before, _snapshot_tree(self.kdir),
                         "an idle-gate skip must mutate nothing")

    def test_force_bypasses_idle_gate(self):
        loop = os.path.join(self.kdir, "work", "260627-live", ".loop")
        _touch(os.path.join(loop, "live-stream.log"))
        code, out, err = self._run(["--force", "--steps", "a", "--format", "json"])
        self.assertEqual(code, 0, err)
        self.assertFalse(json.loads(out)["skipped"])

    def test_lock_held_skips_cycle(self):
        mod = load_cli()
        self.assertTrue(mod.dream_acquire_lock(self.kdir, pid=1))
        try:
            code, out, err = self._run(["--steps", "a", "--format", "json"])
            self.assertEqual(code, 0, err)
            payload = json.loads(out)
            self.assertTrue(payload["skipped"])
            self.assertEqual(payload["skip_kind"], "lock")
        finally:
            mod.dream_release_lock(self.kdir, pid=1)

    def test_metrics_event_emitted_once(self):
        self._run(["--steps", "a", "--format", "json"])
        path = os.path.join(self.kdir, "logs", "metrics.jsonl")
        dreams = [json.loads(l) for l in open(path)
                  if l.strip() and json.loads(l).get("event") == "dream"]
        self.assertEqual(len(dreams), 1)
        self.assertIn("steps", dreams[0])

    def test_journal_well_formed(self):
        self._run(["--steps", "a", "--format", "json"])
        self._run(["--steps", "a", "--format", "json"])
        mod = load_cli()
        text = open(os.path.join(self.kdir, mod.DREAM_JOURNAL_REL)).read()
        self.assertEqual(text.count("## dream "), 2)
        self.assertIn("- step a (index-rebuild): ok", text)
        self.assertIn("- duration_s:", text)

    def test_detect_report_counts_without_acting(self):
        feat = os.path.join(self.kdir, "work", "260627-report")
        os.makedirs(feat, exist_ok=True)
        wl = os.path.join(feat, "worklog.md")
        with open(wl, "w") as f:
            f.write("# wl\n\n> LEARNED: a brand new never-promoted discovery\n")
        wl_before = open(wl).read()
        code, out, err = self._run(["--steps", "e", "--format", "json"])
        self.assertEqual(code, 0, err)
        e = json.loads(out)["steps"][0]
        self.assertGreaterEqual(e["unpromoted_markers"], 1)
        self.assertEqual(open(wl).read(), wl_before,
                         "step e must not promote/alter the worklog (Phase 1)")

    def test_guard_no_destructive_no_autopromotion(self):
        # LOAD-BEARING Phase-1 guard: a full cycle changes ONLY sanctioned paths
        # (derived caches, the ledger+scorecard, the journal, metrics, gitignore).
        # Every SOURCE entry file + worklog stays byte-identical.
        feat = os.path.join(self.kdir, "work", "260627-guard")
        os.makedirs(feat, exist_ok=True)
        wl = os.path.join(feat, "worklog.md")
        with open(wl, "w") as f:
            f.write("# wl\n\n> DECISION: an unpromoted decision marker\n")
        entry = os.path.join(self.kdir, "learnings", "geofence-reminders.md")
        before = _snapshot_tree(self.kdir)
        code, _out, err = self._run(["--format", "json"])
        self.assertEqual(code, 0, err)
        after = _snapshot_tree(self.kdir)
        for p in before:  # no source entry deleted
            if p.startswith(os.path.join(self.kdir, "learnings")):
                self.assertIn(p, after, "a learning file was deleted (destructive)")
        self.assertEqual(before[entry], after[entry])   # no merge/rewrite
        self.assertEqual(before[wl], after[wl])         # no auto-promotion
        changed = sorted(os.path.relpath(p, self.kdir)
                         for p in set(before) | set(after)
                         if before.get(p) != after.get(p))
        for rel in changed:
            self.assertTrue(
                rel == "index.json" or rel.startswith(".cache/")
                or rel.startswith("logs/") or rel.startswith("benchmarks/")
                or rel.endswith("index.md") or rel == "FEATURES.md"
                or rel == ".gitignore",
                "unexpected mutation outside the sanctioned set: %s" % rel)


class DreamGitSyncTests(unittest.TestCase, _GuardedEnv):
    """Step f — kb-git commit gated on autocommit; clean no-ops otherwise."""

    def setUp(self):
        self._save_env()
        self.addCleanup(self._restore_env)
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-git-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        # The synthetic entry files carry no frontmatter; `index migrate` stamps
        # it so rebuild_kb (frontmatter-driven) can reconstruct the index.
        run_cli(["index", "migrate-frontmatter"], self.kdir)
        _seed_gold(self.kdir)
        _migrate_kb(self.kdir)
        for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
                     "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}.items():
            os.environ[k] = v
            self.addCleanup(os.environ.pop, k, None)
        for argv in (["init", "-q"], ["add", "-A"],
                     ["commit", "-q", "-m", "chore(kb): seed"]):
            subprocess.run(["git", "-C", self.kdir] + argv,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    def test_commit_made_when_autocommit_on(self):
        os.environ["AGENTWARE_KB_AUTOCOMMIT"] = "1"
        code, out, err = run_cli(["dream", "--format", "json"], self.kdir)
        self.assertEqual(code, 0, err)
        f = {s["step"]: s for s in json.loads(out)["steps"]}["f"]
        self.assertEqual(f["status"], "ok")
        self.assertTrue(f["committed"])
        self.assertNotEqual(f["sha"], "-")

    def test_no_commit_when_autocommit_off(self):
        os.environ["AGENTWARE_KB_AUTOCOMMIT"] = "0"
        code, out, err = run_cli(["dream", "--steps", "f", "--format", "json"],
                                 self.kdir)
        self.assertEqual(code, 0, err)
        f = json.loads(out)["steps"][0]
        self.assertEqual(f["status"], "skipped")
        self.assertIn("autocommit OFF", f["reason"])


class DreamHealthAuditTests(unittest.TestCase, _GuardedEnv):
    """dream_health audit check: inert when OFF, warns/ok when ON."""

    def setUp(self):
        self._save_env()
        self.addCleanup(self._restore_env)
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-health-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        # The synthetic entry files carry no frontmatter; `index migrate` stamps
        # it so rebuild_kb (frontmatter-driven) can reconstruct the index.
        run_cli(["index", "migrate-frontmatter"], self.kdir)
        _seed_gold(self.kdir)

    def _dream_check(self):
        _code, out, _ = run_cli(["audit", "--format", "json"], self.kdir)
        for c in json.loads(out)["checks"]:
            if c["name"] == "dream_health":
                return c
        return None

    def test_inert_when_off(self):
        # Force OFF via env (hermetic): popping the var would fall through to the
        # operator's REAL ~/.agentware/config.env, which may have dream enabled.
        os.environ["AGENTWARE_DREAM"] = "0"
        c = self._dream_check()
        self.assertIsNotNone(c)
        self.assertTrue(c["ok"])
        self.assertIn("inert", c["details"][0])

    def test_warns_when_on_but_never_ran(self):
        os.environ["AGENTWARE_DREAM"] = "1"
        self.assertFalse(self._dream_check()["ok"])

    def test_ok_when_on_and_fresh(self):
        os.environ["AGENTWARE_DREAM"] = "1"
        run_cli(["dream", "--steps", "a", "--format", "json"], self.kdir)
        c = self._dream_check()
        self.assertTrue(c["ok"], c["details"])
        self.assertIn("fresh", c["details"][0])


class DreamSchedulerTests(unittest.TestCase, _GuardedEnv):
    """Scheduler installer writes only into a patched HOME (no system mutation)."""

    def setUp(self):
        self._save_env()
        self.addCleanup(self._restore_env)
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-sched-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.home = tempfile.mkdtemp(prefix="agentware-dream-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        os.environ["HOME"] = self.home
        os.environ["AGENTWARE_DREAM"] = "1"
        os.environ["AGENTWARE_DREAM_SCHEDULE"] = "03:30"
        # HOME_CONFIG/CONFIG_PATHS are frozen at import (expanduser at load time),
        # so patching $HOME alone does NOT redirect config reads. Redirect them to
        # a temp config inside the patched HOME so resolve_dream*/config reads are
        # fully hermetic and never see the operator's real config (R-LOC-03).
        mod = load_cli()
        self._saved_cfg = (mod.HOME_CONFIG, mod.CONFIG_PATHS)
        cfg = os.path.join(self.home, ".agentware", "config.env")
        mod.HOME_CONFIG, mod.CONFIG_PATHS = cfg, (cfg,)

        def _restore_cfg():
            mod.HOME_CONFIG, mod.CONFIG_PATHS = self._saved_cfg
        self.addCleanup(_restore_cfg)

    def _installed_path(self):
        import sys
        if sys.platform == "darwin":
            return os.path.join(self.home, "Library", "LaunchAgents",
                                "com.agentware.dream.plist")
        return os.path.join(self.home, ".agentware", "dream.cron")

    def test_install_writes_artifact_into_home(self):
        code, _out, err = run_cli(["dream", "--install-schedule", "--format",
                                   "json"], self.kdir)
        self.assertEqual(code, 0, err)
        path = self._installed_path()
        self.assertTrue(os.path.isfile(path), "no schedule artifact at %s" % path)
        content = open(path).read()
        self.assertIn("dream", content)
        self.assertIn(os.path.join("scripts", "agentware"), content)

    def test_install_requires_schedule(self):
        os.environ.pop("AGENTWARE_DREAM_SCHEDULE", None)
        code, _out, err = run_cli(["dream", "--install-schedule"], self.kdir)
        self.assertEqual(code, 2)
        self.assertIn("schedule", err)

    def test_install_requires_dream_on(self):
        os.environ["AGENTWARE_DREAM"] = "0"
        code, _out, err = run_cli(["dream", "--install-schedule"], self.kdir)
        self.assertEqual(code, 2)
        self.assertIn("OFF", err)

    def test_uninstall_idempotent(self):
        run_cli(["dream", "--install-schedule"], self.kdir)
        code1, o1, e1 = run_cli(["dream", "--uninstall-schedule", "--format",
                                 "json"], self.kdir)
        self.assertEqual(code1, 0, e1)
        self.assertTrue(json.loads(o1)["removed"])
        self.assertFalse(os.path.isfile(self._installed_path()))
        code2, o2, e2 = run_cli(["dream", "--uninstall-schedule", "--format",
                                 "json"], self.kdir)
        self.assertEqual(code2, 0, e2)
        self.assertFalse(json.loads(o2)["removed"])


# ============================================================================
# Phase 1.5 — observability + actionable reporting (feature 260628-dream-observability)
# Hermetic, temp-dir, stdlib-only tests for every NEW behavior added in that
# feature. Each test pins the failing-test ids it asserts on so the suite stays
# self-referential-safe under AGENTWARE_NESTED_UNITTEST.
# ============================================================================


class DreamFailingTestParseTests(unittest.TestCase):
    """Task 1 — recover *which* tests failed from unittest's stderr header lines."""

    def setUp(self):
        self.mod = load_cli()

    def test_parses_fail_and_error_ids(self):
        err = (
            "FAIL: test_alpha (tests.test_mod.Case)\n"
            "Traceback (most recent call last):\n  ...\n"
            "ERROR: test_beta (tests.test_mod.Case.test_beta)\n"
            "  raise RuntimeError\n"
            "Ran 3 tests in 0.01s\nFAILED (failures=1, errors=1)\n")
        self.assertEqual(
            self.mod._parse_failing_test_ids(err),
            ["test_alpha (tests.test_mod.Case)",
             "test_beta (tests.test_mod.Case.test_beta)"])

    def test_dedupes_preserving_first_seen_order(self):
        err = ("FAIL: test_b (m.C)\nFAIL: test_a (m.C)\nFAIL: test_b (m.C)\n")
        self.assertEqual(self.mod._parse_failing_test_ids(err),
                         ["test_b (m.C)", "test_a (m.C)"])

    def test_ignores_noise_and_empty(self):
        self.assertEqual(self.mod._parse_failing_test_ids(""), [])
        self.assertEqual(self.mod._parse_failing_test_ids(None), [])
        self.assertEqual(
            self.mod._parse_failing_test_ids("OK\nRan 5 tests\n  some FAIL: x\n"),
            [], "only header lines (after strip) count, not substrings mid-line")

    def test_record_passrate_detail_nested_guard_returns_4tuple(self):
        # We ARE inside a suite run (AGENTWARE_NESTED_UNITTEST is set by the
        # runner / the verify cmd), so the recursion guard returns the empty
        # 4-tuple instead of re-spawning the suite.
        prev = os.environ.get("AGENTWARE_NESTED_UNITTEST")
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"
        try:
            self.assertEqual(self.mod._record_test_passrate_detail(),
                             (None, 0, 0, []))
            # The back-compatible 3-tuple wrapper still works for old call sites.
            self.assertEqual(self.mod._record_test_passrate(), (None, 0, 0))
        finally:
            if prev is None:
                os.environ.pop("AGENTWARE_NESTED_UNITTEST", None)
            else:
                os.environ["AGENTWARE_NESTED_UNITTEST"] = prev

    def test_audit_tests_check_threads_sorted_failed_test_ids(self):
        # `_audit_tests_check` shells the suite for real, so we stub subprocess.run
        # (NEVER call it live from inside the suite — that recurses). The contract:
        # it parses the FAIL:/ERROR: ids and threads them, sorted (INV-1), into the
        # check payload that `audit --with-tests` (dream step c) consumes.
        mod = self.mod

        class _Proc:
            returncode = 1
            stdout = ""
            stderr = ("FAIL: test_b (m.C)\nTraceback...\n"
                      "ERROR: test_a (m.C)\nTraceback...\n"
                      "Ran 2 tests in 0.0s\n\nFAILED (failures=1, errors=1)\n")

        saved = mod.subprocess.run
        mod.subprocess.run = lambda *a, **k: _Proc()
        try:
            chk = mod._audit_tests_check()
        finally:
            mod.subprocess.run = saved
        self.assertEqual(chk["name"], "unittest")
        self.assertFalse(chk["ok"])
        self.assertEqual(chk["failed_tests"], ["test_a (m.C)", "test_b (m.C)"],
                         "failed_tests must be sorted (byte-stable, INV-1)")


class DreamMaxRuntimeParseTests(unittest.TestCase):
    """Task 7 — strict parse + resolver for the wall-clock cap (SETTINGS_AW)."""

    def setUp(self):
        self.mod = load_cli()

    def test_parse_matrix(self):
        p = self.mod._parse_dream_max_runtime
        self.assertEqual(p("900"), 900)         # bare seconds
        self.assertEqual(p("30m"), 1800)        # minutes
        self.assertEqual(p("1h"), 3600)         # hours
        self.assertEqual(p("45s"), 45)          # explicit seconds suffix
        self.assertEqual(p("off"), 0)           # off token -> disabled
        self.assertEqual(p("0"), 0)             # zero -> disabled
        self.assertEqual(p("disabled"), 0)
        self.assertIsNone(p("bogus"))           # invalid -> None (falls through)
        self.assertIsNone(p(""))
        self.assertIsNone(p(None))

    def test_resolver_env_overrides_default(self):
        key = self.mod.DREAM_MAX_RUNTIME_KEY
        prev = os.environ.get(key)
        try:
            os.environ[key] = "15m"
            self.assertEqual(self.mod.resolve_dream_max_runtime(), 900)
            os.environ[key] = "off"
            self.assertEqual(self.mod.resolve_dream_max_runtime(), 0)
            # A typo in the env degrades to the default (never silently disables).
            os.environ[key] = "nonsense"
            self.assertEqual(self.mod.resolve_dream_max_runtime(),
                             self.mod.DREAM_MAX_RUNTIME_DEFAULT)
        finally:
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev


class DreamSchedulerRedirectTests(unittest.TestCase):
    """Task 4 — the scheduler generators stop discarding output."""

    def setUp(self):
        self.mod = load_cli()

    def test_plist_has_standard_out_and_err_paths(self):
        p = self.mod._dream_launchd_plist("00:00", "/x/agentware", "/tmp/d.log")
        self.assertIn("<key>StandardOutPath</key>", p)
        self.assertIn("<key>StandardErrorPath</key>", p)
        self.assertEqual(p.count("/tmp/d.log"), 2)   # both streams -> the log
        self.assertNotIn("/dev/null", p)

    def test_cron_appends_and_drops_dev_null(self):
        c = self.mod._dream_cron_line("00:00", "/x/agentware", "/tmp/d.log")
        self.assertNotIn("/dev/null", c)
        self.assertIn(">> /tmp/d.log 2>&1", c)
        self.assertIn("0 0 * * *", c)               # 00:00 -> minute hour spec

    def test_generators_are_byte_stable(self):
        a1 = self.mod._dream_launchd_plist("03:30", "/x/agentware", "/tmp/d.log")
        a2 = self.mod._dream_launchd_plist("03:30", "/x/agentware", "/tmp/d.log")
        self.assertEqual(a1, a2)
        c1 = self.mod._dream_cron_line("03:30", "/x/agentware", "/tmp/d.log")
        c2 = self.mod._dream_cron_line("03:30", "/x/agentware", "/tmp/d.log")
        self.assertEqual(c1, c2)


class DreamDetectReportRenderTests(unittest.TestCase):
    """Task 5 — actionable, byte-stable, report-only detect report (INV-1/INV-2)."""

    def setUp(self):
        self.mod = load_cli()
        self.report = {
            "stale": [
                {"id": "learn-z", "category": "learnings",
                 "last_verified": "2025-01-01", "age_days": 400,
                 "path": "learnings/z.md"},
                {"id": "learn-a", "category": "references",
                 "last_verified": "2025-02-01", "age_days": 300,
                 "path": "references/a.md"},
            ],
            "conflicts": [
                {"a": "id-b", "b": "id-a", "category": "references",
                 "jaccard": 0.6537},
            ],
            "max_age_days": 120,
            "volatile_categories": ["configurations", "references"],
        }
        self.markers = [
            {"feature": "260628-x", "rel": "work/260628-x/worklog.md",
             "line": 12, "kind": "LEARNED", "text": "a discovery"},
            {"feature": "260628-x", "rel": "work/260628-x/worklog.md",
             "line": 3, "kind": "DECISION", "text": "a choice"},
        ]

    def test_report_enumerates_each_finding(self):
        body = self.mod._dream_render_detect_report(self.report, self.markers)
        # stale: sorted by id (learn-a before learn-z)
        self.assertLess(body.index("learn-a"), body.index("learn-z"))
        self.assertIn("learn-z [learnings] last_verified=2025-01-01 age=400d", body)
        # duplicate pair with the jaccard
        self.assertIn("id-b <-> id-a [references] jaccard=0.6537", body)
        # markers grouped by feature, sorted by line (3 before 12), with kind+text
        self.assertIn("work/260628-x/worklog.md:3 [DECISION] a choice", body)
        self.assertIn("work/260628-x/worklog.md:12 [LEARNED] a discovery", body)
        self.assertLess(body.index("worklog.md:3"), body.index("worklog.md:12"))
        # section headers carry the counts
        self.assertIn("## Stale entries (2)", body)
        self.assertIn("## Possible duplicate / conflict pairs (1)", body)
        self.assertIn("## Unpromoted worklog markers (2)", body)

    def test_render_is_byte_stable(self):
        a = self.mod._dream_render_detect_report(self.report, self.markers)
        b = self.mod._dream_render_detect_report(self.report, self.markers)
        self.assertEqual(a, b, "report body must be byte-stable for unchanged inputs")

    def test_write_report_overwrites_byte_stable(self):
        kdir = tempfile.mkdtemp(prefix="agentware-dream-report-")
        self.addCleanup(shutil.rmtree, kdir, True)
        body = self.mod._dream_render_detect_report(self.report, self.markers)
        rel1 = self.mod._dream_write_detect_report(kdir, body)
        self.assertEqual(rel1, self.mod.DREAM_REPORT_REL)
        path = os.path.join(kdir, rel1)
        first = open(path, "rb").read()
        # Overwrite (not append) with the same body -> byte-identical file.
        self.mod._dream_write_detect_report(kdir, body)
        self.assertEqual(open(path, "rb").read(), first)


class DreamReportNoClockTests(unittest.TestCase):
    """The detect report carries NO clock/run-id (INV-1 determinism)."""

    def setUp(self):
        self.mod = load_cli()

    def test_no_timestamp_in_body(self):
        body = self.mod._dream_render_detect_report(
            {"stale": [], "conflicts": [], "max_age_days": 120,
             "volatile_categories": ["references"]}, [])
        # An ISO 'Z' timestamp (YYYY-MM-DDTHH:MM:SSZ) must never appear in the body.
        import re as _re
        self.assertIsNone(_re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", body),
                          "no clock may leak into the byte-stable report body")


class DreamCycleOutcomeTests(unittest.TestCase):
    """Task 6 — heartbeat outcome classifier (skip-tolerant, like _dream_output)."""

    def setUp(self):
        self.mod = load_cli()

    def _rec(self, *statuses):
        return {"event": "dream", "ts": "2026-06-28T00:00:00Z",
                "steps": [{"step": "x%d" % i, "status": s}
                          for i, s in enumerate(statuses)]}

    def test_ok_when_none_failed(self):
        self.assertEqual(
            self.mod._dream_cycle_outcome(self._rec("ok", "skipped", "ok")), "ok")

    def test_fail_when_all_nonskipped_failed(self):
        self.assertEqual(
            self.mod._dream_cycle_outcome(self._rec("fail", "skipped", "error")),
            "fail")

    def test_partial_when_mixed(self):
        self.assertEqual(
            self.mod._dream_cycle_outcome(self._rec("ok", "fail")), "partial")

    def test_skips_and_planned_are_not_failures(self):
        self.assertEqual(
            self.mod._dream_cycle_outcome(self._rec("skipped", "planned")), "ok")

    def test_empty_steps_is_ok(self):
        self.assertEqual(self.mod._dream_cycle_outcome({"steps": []}), "ok")


class DreamHealthAgeOutcomeTests(unittest.TestCase, _GuardedEnv):
    """Task 6 — dream_health reports last-run AGE + OUTCOME and warns on stale/failed."""

    def setUp(self):
        self._save_env()
        self.addCleanup(self._restore_env)
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-hb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = self._mod  # _GuardedEnv loaded + isolated the CLI module
        os.environ["AGENTWARE_DREAM"] = "1"  # ON (hermetic via _GuardedEnv config)

    def _write_dream_event(self, ts, statuses):
        logs = os.path.join(self.kdir, "logs")
        os.makedirs(logs, exist_ok=True)
        rec = {"event": "dream", "ts": ts,
               "steps": [{"step": "x%d" % i, "status": s}
                         for i, s in enumerate(statuses)]}
        with open(os.path.join(logs, "metrics.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    def test_fresh_ok_reports_age_and_outcome(self):
        self._write_dream_event(self.mod.utc_now_iso(), ["ok", "ok", "skipped"])
        c = self.mod._audit_dream_health_check(self.kdir)
        self.assertTrue(c["ok"], c["details"])
        self.assertEqual(c["outcome"], "ok")
        self.assertIsNotNone(c["age_hours"])
        self.assertLess(c["age_hours"], 1.0)
        self.assertIsNotNone(c["last_run"])

    def test_recent_but_failed_warns_with_outcome(self):
        self._write_dream_event(self.mod.utc_now_iso(), ["ok", "fail"])
        c = self.mod._audit_dream_health_check(self.kdir)
        self.assertFalse(c["ok"])
        self.assertEqual(c["outcome"], "partial")
        self.assertIn("did not finish clean", c["details"][0])

    def test_stale_cycle_warns_and_still_reports_outcome(self):
        # A cycle far in the past trips the staleness budget regardless of outcome.
        self._write_dream_event("2020-01-01T00:00:00Z", ["ok", "ok"])
        c = self.mod._audit_dream_health_check(self.kdir)
        self.assertFalse(c["ok"])
        self.assertGreater(c["age_hours"], self.mod.DREAM_STALE_HOURS)
        self.assertIn("stale", c["details"][0])
        self.assertEqual(c["outcome"], "ok")

    def test_inert_when_off(self):
        os.environ["AGENTWARE_DREAM"] = "0"
        c = self.mod._audit_dream_health_check(self.kdir)
        self.assertTrue(c["ok"])
        self.assertIn("inert", c["details"][0])


class DreamFailureArtifactTests(unittest.TestCase, _GuardedEnv):
    """Tasks 2+3 — on a failing step the orchestrator persists the full capture to
    logs/dream-failures/<started>.log and the journal + metric carry the failure
    fields. Driven end-to-end by injecting a failing step into DREAM_STEP_FUNCS."""

    def setUp(self):
        self._save_env()
        self.addCleanup(self._restore_env)
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-fail-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = self._mod  # _GuardedEnv loaded + isolated the CLI module

    def test_failure_log_rel_strips_colons(self):
        self.assertEqual(
            self.mod._dream_failure_log_rel("2026-06-28T03:58:49Z"),
            os.path.join("logs", "dream-failures", "2026-06-28T035849Z.log"))

    def test_write_failure_capture_persists_full_text(self):
        rel = self.mod._dream_write_failure_capture(
            self.kdir, "2026-06-28T00:00:00Z", "c",
            "FAIL: tests.test_x (m.C)\nfull audit output\n")
        self.assertIsNotNone(rel)
        body = open(os.path.join(self.kdir, rel), encoding="utf-8").read()
        self.assertIn("FAIL: tests.test_x (m.C)", body)
        self.assertIn("step c", body)

    def test_metric_step_copies_only_present_failure_fields(self):
        # Step c carries failure fields; a plain step stays minimal (byte-stable).
        c = self.mod._dream_metric_step(
            {"step": "c", "status": "fail", "duration_s": 0.1,
             "failed_checks": "unittest", "failed_tests": "tests.test_x",
             "tests_failed": 1, "tests_ran": 791})
        self.assertEqual(c["failed_tests"], "tests.test_x")
        self.assertEqual(c["tests_ran"], 791)
        plain = self.mod._dream_metric_step(
            {"step": "a", "status": "ok", "duration_s": 0.2})
        self.assertEqual(set(plain), {"step", "status", "duration_s"})

    def test_full_failure_lands_on_disk_journal_and_metric(self):
        mod = self.mod
        cap = ("===== audit =====\nFAIL: tests.test_x (m.C)\n"
               "ERROR: tests.test_y (m.C)\nfull captured stderr\n")

        def fake_c(kdir, dry_run):
            if dry_run:
                return {"status": "planned"}
            return {"status": "fail", "failed_checks": "unittest",
                    "tests_ran": 791, "tests_failed": 2,
                    "failed_tests": "tests.test_x,tests.test_y",
                    "_failure_capture": cap}

        saved = mod.DREAM_STEP_FUNCS
        mod.DREAM_STEP_FUNCS = (("c", "audit", fake_c),)
        try:
            code, out, err = run_cli(
                ["dream", "--steps", "c", "--force", "--format", "json"],
                self.kdir)
        finally:
            mod.DREAM_STEP_FUNCS = saved
        self.assertEqual(code, 1, err)  # a failed step -> non-zero exit
        step = json.loads(out)["steps"][0]
        # (1) The per-cycle triage artifact is written + referenced; the private
        #     capture key never leaks into the step dict.
        self.assertNotIn("_failure_capture", step)
        self.assertIn("triage_log", step)
        art = os.path.join(self.kdir, step["triage_log"])
        self.assertTrue(os.path.isfile(art))
        body = open(art, encoding="utf-8").read()
        self.assertIn("FAIL: tests.test_x (m.C)", body)
        self.assertIn("ERROR: tests.test_y (m.C)", body)
        # (2) The journal entry carries the failure fields.
        journal = open(os.path.join(self.kdir, mod.DREAM_JOURNAL_REL),
                       encoding="utf-8").read()
        self.assertIn("failed_tests=tests.test_x,tests.test_y", journal)
        self.assertIn("tests_failed=2", journal)
        self.assertIn("failed_checks=unittest", journal)
        # (3) The metric event's per-step record carries them too.
        metrics = os.path.join(self.kdir, "logs", "metrics.jsonl")
        dreams = [json.loads(l) for l in open(metrics)
                  if l.strip() and json.loads(l).get("event") == "dream"]
        c = dreams[-1]["steps"][0]
        self.assertEqual(c["failed_tests"], "tests.test_x,tests.test_y")
        self.assertEqual(c["failed_checks"], "unittest")
        self.assertEqual(c["tests_failed"], 2)
        self.assertEqual(c["tests_ran"], 791)


class DreamMaxRuntimeGuardTests(unittest.TestCase, _GuardedEnv):
    """Task 7 — the wall-clock cap trips between steps -> PARTIAL cycle + non-zero
    exit + journal/metric trip record. Fully deterministic: the clock is injected."""

    def setUp(self):
        self._save_env()
        self.addCleanup(self._restore_env)
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-rt-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = self._mod  # _GuardedEnv loaded + isolated the CLI module

    def test_cap_trips_partial_cycle_nonzero_exit(self):
        mod = self.mod

        def ok_step(kdir, dry_run):
            return {"status": "ok"}

        # Injected perf clock: wall0=0; step-a elapsed/t0/end all 1 (<cap); step-b
        # elapsed=20 (>10s cap -> TRIP); final cycle-duration read=21.
        vals = [0.0, 1.0, 1.0, 1.0, 20.0, 21.0]

        def fake_perf():
            return vals.pop(0) if vals else 999.0

        saved_funcs, saved_perf = mod.DREAM_STEP_FUNCS, mod._dream_perf
        mod.DREAM_STEP_FUNCS = (("a", "s1", ok_step), ("b", "s2", ok_step),
                                ("c", "s3", ok_step))
        mod._dream_perf = fake_perf
        os.environ[mod.DREAM_MAX_RUNTIME_KEY] = "10"  # 10-second cap
        try:
            code, out, err = run_cli(["dream", "--force", "--format", "json"],
                                     self.kdir)
        finally:
            mod.DREAM_STEP_FUNCS, mod._dream_perf = saved_funcs, saved_perf
            os.environ.pop(mod.DREAM_MAX_RUNTIME_KEY, None)
        self.assertEqual(code, 1, err)  # guard error -> non-zero exit
        payload = json.loads(out)
        self.assertTrue(payload["timed_out"])
        self.assertIn("max-runtime", payload["timeout_reason"])
        by = [(s["step"], s["status"]) for s in payload["steps"]]
        self.assertEqual(by, [("a", "ok"), ("guard", "error"),
                              ("b", "skipped"), ("c", "skipped")])
        # PARTIAL outcome (a passed step + the guard failure).
        self.assertEqual(
            mod._dream_cycle_outcome({"steps": payload["steps"]}), "partial")
        # Journal + metric record the trip.
        journal = open(os.path.join(self.kdir, mod.DREAM_JOURNAL_REL),
                       encoding="utf-8").read()
        self.assertIn("- timed_out:", journal)
        metrics = os.path.join(self.kdir, "logs", "metrics.jsonl")
        ev = [json.loads(l) for l in open(metrics)
              if l.strip() and json.loads(l).get("event") == "dream"][-1]
        self.assertTrue(ev["timed_out"])
        self.assertIn("max-runtime", ev["timeout_reason"])

    def test_cap_zero_disables_guard(self):
        # 0 = disabled: even a "slow" injected clock never trips. Stub the step so
        # the test is hermetic (no real index rebuild) and isolates the guard.
        mod = self.mod

        def ok_step(kdir, dry_run):
            return {"status": "ok"}

        def fake_perf():
            # A monotonically huge clock; with the cap disabled it must be ignored.
            fake_perf.t += 10_000.0
            return fake_perf.t
        fake_perf.t = 0.0

        saved_funcs, saved_perf = mod.DREAM_STEP_FUNCS, mod._dream_perf
        mod.DREAM_STEP_FUNCS = (("a", "s1", ok_step), ("b", "s2", ok_step))
        mod._dream_perf = fake_perf
        os.environ[mod.DREAM_MAX_RUNTIME_KEY] = "off"
        try:
            code, out, err = run_cli(["dream", "--force", "--format", "json"],
                                     self.kdir)
        finally:
            mod.DREAM_STEP_FUNCS, mod._dream_perf = saved_funcs, saved_perf
            os.environ.pop(mod.DREAM_MAX_RUNTIME_KEY, None)
        self.assertEqual(code, 0, err)
        self.assertIsNone(json.loads(out).get("timed_out"))
        self.assertEqual([s["status"] for s in json.loads(out)["steps"]],
                         ["ok", "ok"])


class DreamNoNetworkTests(unittest.TestCase):
    """Invariant — dream stays 100% OFFLINE: no outbound ping / dead-man's-switch
    integration anywhere in the dream code (Task 7, acceptance criterion)."""

    def setUp(self):
        self.mod = load_cli()

    def test_no_network_tokens_in_dream_source(self):
        import inspect
        names = [n for n in dir(self.mod)
                 if ("dream" in n.lower()) and callable(getattr(self.mod, n))]
        self.assertTrue(names, "no dream functions discovered")
        src = []
        for n in names:
            try:
                src.append(inspect.getsource(getattr(self.mod, n)))
            except (OSError, TypeError):
                pass
        blob = "\n".join(src)
        # Pure network-call tokens (NOT 'http', which legitimately appears in the
        # launchd plist DTD URL); their absence proves no off-box integration.
        for tok in ("urllib", "urlopen", "socket.", "http.client",
                    "requests.", "healthchecks", "DREAM_PING"):
            self.assertNotIn(tok, blob,
                             "dream code must make no network calls: found %r" % tok)


class DreamObservabilityE2ETests(unittest.TestCase, _GuardedEnv):
    """Task 10 [e2e] — prove the morning-after question is answerable FROM DISK.

    A forced FULL cycle (real steps a,b,d,e,f) with ONE injected failing audit
    check (step c carrying real failing-test names) must, with zero re-runs, land:
    (a) the failing TEST NAMES in logs/metrics.jsonl, the journal, AND
        logs/dream-failures/<ts>.log;
    (b) the duplicate pair + every unpromoted marker in logs/dream-report-latest.md;
    (c) scheduler output redirection in the generated launchd plist + cron line
        (no /dev/null);
    (d) the dream_health heartbeat reporting last-run AGE + OUTCOME.

    Hermetic: synthetic KB, isolated config (_GuardedEnv), injected failing step
    (the real audit shells unittest, which the nested guard disables — so real
    failing-test NAMES can only be produced by injecting step c, per
    learn-dream-cycle-test-monkeypatch-step-funcs). Step f degrades to a skipped
    no-op (autocommit OFF under the isolated config) so the cycle touches no git.
    """

    # Two near-identical `references` entries -> a real jaccard>=0.6 duplicate pair
    # that survives the step-a frontmatter rebuild. Distinct vocabulary from the
    # default entries so EXACTLY one pair is flagged.
    _DUP_BODY = (
        "Token bucket rate limiting refills tokens at a steady configured rate "
        "and permits short bursts up to the bucket capacity before throttling "
        "any further requests, smoothing spiky traffic deterministically.\n")
    _DUP_A = {
        "id": "ref-dup-alpha", "title": "Token Bucket Rate Limiting",
        "category": "references", "path": "references/dup-alpha.md",
        "tags": ["ratelimit", "tokenbucket", "throttle"],
        "created": "2026-01-06",
        "summary": "Token bucket rate limiting refills tokens at a steady rate.",
        "body": "# Token Bucket Rate Limiting\n\n" + _DUP_BODY,
    }
    _DUP_B = {
        "id": "ref-dup-beta", "title": "Token Bucket Throttling",
        "category": "references", "path": "references/dup-beta.md",
        "tags": ["ratelimit", "tokenbucket", "throttle"],
        "created": "2026-01-07",
        "summary": "Token bucket rate limiting refills tokens at a steady rate.",
        "body": "# Token Bucket Throttling\n\n" + _DUP_BODY,
    }

    def setUp(self):
        self._save_env()
        self.addCleanup(self._restore_env)
        self.kdir = tempfile.mkdtemp(prefix="agentware-dream-e2e-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.mod = self._mod  # _GuardedEnv loaded + isolated the CLI module
        from tests import _fixtures as fx
        entries = [dict(e) for e in fx._ENTRIES] + [self._DUP_A, self._DUP_B]
        build_synthetic_kb(self.kdir, entries=entries)
        # Frontmatter-complete so step a (index rebuild) is a clean no-op and the
        # two duplicate entries survive into the rebuilt index.
        run_cli(["index", "migrate-frontmatter"], self.kdir)
        run_cli(["index", "rebuild"], self.kdir)
        _seed_gold(self.kdir)
        # An unpromoted-marker worklog for step e to enumerate (text is unique, so
        # scan_worklog flags both markers as unpromoted).
        wdir = os.path.join(self.kdir, "work", "260628-e2e")
        os.makedirs(wdir, exist_ok=True)
        with open(os.path.join(wdir, "worklog.md"), "w", encoding="utf-8") as f:
            f.write("# Worklog — 260628-e2e\n\n"
                    "> LEARNED: e2e dream observability proof marker alpha\n"
                    "> DECISION: e2e injected a failing audit step for determinism\n")
        os.environ["AGENTWARE_DREAM"] = "1"  # ON so dream_health is live (not inert)

    def test_full_cycle_is_self_explaining_from_disk(self):
        mod = self.mod
        cap = ("===== audit --with-tests =====\n"
               "FAIL: tests.test_widget (m.WidgetCase.test_render)\n"
               "ERROR: tests.test_db (m.DbCase.test_commit)\n"
               "full captured audit + unittest stderr blob\n")

        def fake_c(kdir, dry_run):
            if dry_run:
                return {"status": "planned", "would": "audit --with-tests"}
            return {"status": "fail", "failed_checks": "unittest",
                    "tests_ran": 802, "tests_failed": 2,
                    "failed_tests": "tests.test_db,tests.test_widget",
                    "_failure_capture": cap}

        # Swap ONLY step c; every other step runs for real -> a genuine full cycle.
        saved = mod.DREAM_STEP_FUNCS
        mod.DREAM_STEP_FUNCS = tuple(
            (s, lbl, fake_c if s == "c" else fn) for (s, lbl, fn) in saved)
        try:
            code, out, err = run_cli(
                ["dream", "--force", "--format", "json"], self.kdir)
        finally:
            mod.DREAM_STEP_FUNCS = saved

        self.assertEqual(code, 1, err)  # a failed step -> non-zero exit
        payload = json.loads(out)
        steps = {s["step"]: s for s in payload["steps"]}
        # The full a-f cycle ran (c failed but never aborted the rest).
        self.assertEqual([s["step"] for s in payload["steps"]],
                         ["a", "b", "c", "d", "e", "f"])
        self.assertEqual(steps["c"]["status"], "fail")
        self.assertEqual(steps["e"]["status"], "ok")

        names = ("tests.test_db", "tests.test_widget")

        # (a) The failing TEST NAMES land in all three artifacts.
        # (a.1) metrics.jsonl dream event, step-c record.
        metrics = os.path.join(self.kdir, "logs", "metrics.jsonl")
        dreams = [json.loads(l) for l in open(metrics, encoding="utf-8")
                  if l.strip() and json.loads(l).get("event") == "dream"]
        mc = {s["step"]: s for s in dreams[-1]["steps"]}["c"]
        self.assertEqual(mc["failed_tests"], "tests.test_db,tests.test_widget")
        self.assertEqual(mc["failed_checks"], "unittest")
        self.assertEqual(mc["tests_failed"], 2)
        self.assertEqual(mc["tests_ran"], 802)
        # (a.2) The journal.
        journal = open(os.path.join(self.kdir, mod.DREAM_JOURNAL_REL),
                       encoding="utf-8").read()
        self.assertIn("failed_tests=tests.test_db,tests.test_widget", journal)
        self.assertIn("tests_failed=2", journal)
        # (a.3) The per-cycle triage capture log (FULL captured output).
        self.assertIn("triage_log", steps["c"])
        self.assertNotIn("_failure_capture", steps["c"])  # never leaks
        triage = os.path.join(self.kdir, steps["c"]["triage_log"])
        self.assertTrue(os.path.isfile(triage))
        tbody = open(triage, encoding="utf-8").read()
        self.assertIn("FAIL: tests.test_widget (m.WidgetCase.test_render)", tbody)
        self.assertIn("ERROR: tests.test_db (m.DbCase.test_commit)", tbody)

        # (b) The actionable report enumerates the duplicate pair + every marker.
        report = os.path.join(self.kdir, mod.DREAM_REPORT_REL)
        self.assertTrue(os.path.isfile(report))
        rbody = open(report, encoding="utf-8").read()
        # the duplicate pair (both ids + a jaccard) -- direction is corpus order.
        self.assertIn("ref-dup-alpha <-> ref-dup-beta", rbody)
        self.assertIn("[references] jaccard=", rbody)
        # every unpromoted marker, with worklog.md:line + kind + text.
        self.assertIn("work/260628-e2e/worklog.md:", rbody)
        self.assertIn("[learned] e2e dream observability proof marker alpha", rbody)
        self.assertIn(
            "[decision] e2e injected a failing audit step for determinism", rbody)
        self.assertEqual(steps["e"].get("duplicates"), 1)
        self.assertEqual(steps["e"].get("unpromoted_markers"), 2)

        # (c) The generated scheduler artifacts REDIRECT output (no /dev/null).
        plist = mod._dream_launchd_plist("00:00", "/x/agentware")
        self.assertIn("StandardOutPath", plist)
        self.assertIn("StandardErrorPath", plist)
        cron = mod._dream_cron_line("00:00", "/x/agentware")
        self.assertNotIn("/dev/null", cron)
        self.assertIn(">>", cron)

        # (d) dream_health reports last-run AGE + OUTCOME (partial: c failed, rest ok).
        hc = mod._audit_dream_health_check(self.kdir)
        self.assertIsNotNone(hc["last_run"])
        self.assertIsNotNone(hc["age_hours"])
        self.assertLess(hc["age_hours"], 1.0)
        self.assertEqual(hc["outcome"], "partial")
        self.assertFalse(hc["ok"])  # warns: last cycle did not finish clean


if __name__ == "__main__":
    unittest.main()
