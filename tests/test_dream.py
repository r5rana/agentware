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
    recursively shell the whole suite) and restore touched env keys afterward."""

    _KEYS = ("AGENTWARE_NESTED_UNITTEST", "AGENTWARE_DREAM",
             "AGENTWARE_DREAM_SCHEDULE", "HOME", "AGENTWARE_KB_AUTOCOMMIT")

    def _save_env(self):
        self._env = {k: os.environ.get(k) for k in self._KEYS}
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"

    def _restore_env(self):
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
        os.environ.pop("AGENTWARE_DREAM", None)
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


if __name__ == "__main__":
    unittest.main()
