"""DreamSchedulerTests, DreamSchedulerRedirectTests, DreamMaxRuntimeParseTests, DreamMaxRuntimeGuardTests, GuardedEnvLeavesNothingBehindTest -- split from `test_dream` (task 58).

See `tests.unit.features._dream_support` for the
shared header.
"""

from tests.unit.features._dream_support import (  # noqa: F401
    _GuardedEnv, _steps_mod, build_synthetic_kb, io, json, load_cli, os,
    run_cli, shutil, tempfile, unittest)


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
        # Named for THIS class, not `_saved_cfg`: `_GuardedEnv._save_env` above
        # already redirected the config once and stashed the pre-redirect value
        # in the shared instance namespace. The two saves used to share a name,
        # the later one won, and the outer restore then wrote the INNER temp
        # path back as if it were the operator's real config -- see the note on
        # `_GuardedEnv._KEYS`. This save is the mixin's temp path, and restoring
        # it is correct; the mixin restores the real one after.
        self._scheduler_saved_cfg = (mod.HOME_CONFIG, mod.CONFIG_PATHS)
        cfg = os.path.join(self.home, ".agentware", "config.env")
        mod.HOME_CONFIG, mod.CONFIG_PATHS = cfg, (cfg,)

        def _restore_cfg():
            mod.HOME_CONFIG, mod.CONFIG_PATHS = self._scheduler_saved_cfg
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

        saved_funcs, saved_perf = _steps_mod(mod).DREAM_STEP_FUNCS, _steps_mod(mod)._dream_perf
        _steps_mod(mod).DREAM_STEP_FUNCS = (("a", "s1", ok_step), ("b", "s2", ok_step),
                                ("c", "s3", ok_step))
        _steps_mod(mod)._dream_perf = fake_perf
        os.environ[mod.DREAM_MAX_RUNTIME_KEY] = "10"  # 10-second cap
        try:
            code, out, err = run_cli(["dream", "--force", "--format", "json"],
                                     self.kdir)
        finally:
            _steps_mod(mod).DREAM_STEP_FUNCS, _steps_mod(mod)._dream_perf = saved_funcs, saved_perf
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

        saved_funcs, saved_perf = _steps_mod(mod).DREAM_STEP_FUNCS, _steps_mod(mod)._dream_perf
        _steps_mod(mod).DREAM_STEP_FUNCS = (("a", "s1", ok_step), ("b", "s2", ok_step))
        _steps_mod(mod)._dream_perf = fake_perf
        os.environ[mod.DREAM_MAX_RUNTIME_KEY] = "off"
        try:
            code, out, err = run_cli(["dream", "--force", "--format", "json"],
                                     self.kdir)
        finally:
            _steps_mod(mod).DREAM_STEP_FUNCS, _steps_mod(mod)._dream_perf = saved_funcs, saved_perf
            os.environ.pop(mod.DREAM_MAX_RUNTIME_KEY, None)
        self.assertEqual(code, 0, err)
        self.assertIsNone(json.loads(out).get("timed_out"))
        self.assertEqual([s["status"] for s in json.loads(out)["steps"]],
                         ["ok", "ok"])


class GuardedEnvLeavesNothingBehindTest(unittest.TestCase):
    """The isolation guard must restore what it found, on the OWNER too.

    Feature 260813 task 94. `_GuardedEnv` and `DreamSchedulerTests.setUp` each
    stashed the pre-redirect config under `self._saved_cfg` -- one instance
    namespace, one name, two writers. The subclass's save ran last, so the
    mixin's LIFO restore wrote the INNER temp path back as the "original" and
    then `rmtree`'d the directory it named. From that point every test in the
    process ran with `HOME_CONFIG` pointing at a deleted directory.

    That is the shape the mixin exists to prevent, inverted -- a guard whose
    own restore installs a wrong value is worse than no guard, because the
    tests after it believe they are isolated. It reddened
    `test_preserved_gates.MirroredNamesReachConsumersNotOnlyTheOwnerTest`
    ~60 modules later, and nothing in between said a word.

    Asserted on the OWNER (`agentware_lib.core.config`) and a CONSUMER, not
    just on the CLI module: a restore that reaches the CLI dict and stops there
    leaves the implementation reading the stale path, which is exactly the
    class of silent failure `_ConfigMirrorModule` was written for.
    """

    def _run_one(self, name):
        suite = unittest.TestLoader().loadTestsFromName(
            "%s.DreamSchedulerTests.%s" % (__name__, name))
        result = unittest.TextTestRunner(
            verbosity=0, stream=io.StringIO()).run(suite)
        self.assertTrue(result.wasSuccessful(),
                        result.failures or result.errors)

    def test_the_nested_guards_restore_every_binding_they_found(self):
        import agentware_lib.core.config as libconfig
        import agentware_lib.features.skilllock as skilllock
        mod = load_cli()
        before = (mod.HOME_CONFIG, mod.CONFIG_PATHS,
                  libconfig.HOME_CONFIG, skilllock.HOME_CONFIG)
        self._run_one("test_install_requires_dream_on")
        after = (mod.HOME_CONFIG, mod.CONFIG_PATHS,
                 libconfig.HOME_CONFIG, skilllock.HOME_CONFIG)
        self.assertEqual(after, before,
                         "the guard left a redirect behind; every later test "
                         "in this process now resolves config from it")

    def test_the_restored_path_is_not_a_directory_the_guard_deleted(self):
        """The discriminator: equality alone would pass on a shared stale value.

        If BOTH saves were wrong in the same direction the tuple compare above
        would still match. What cannot be faked is that the restored config
        directory still exists -- the guard `rmtree`s its own temp dir, so a
        restored path under it is provably dead.
        """
        mod = load_cli()
        self._run_one("test_install_requires_dream_on")
        self.assertTrue(os.path.isdir(os.path.dirname(mod.HOME_CONFIG)),
                        "HOME_CONFIG restored to a deleted directory: %s"
                        % mod.HOME_CONFIG)

    def test_no_attribute_is_written_by_BOTH_the_mixin_and_the_subclass(self):
        """The root cause as an INVARIANT, over the AST rather than the text.

        Not "these two names differ" -- that is satisfiable by renaming one and
        colliding somewhere else. The property is that the mixin and the class
        mixing it in never write the same instance attribute, which is what
        makes their LIFO restores independent.

        Read from the AST, per `learn-assert-a-terminal-state-over-the-ast-not-
        the-source-text`: a source-text check reddens on the comments that
        EXPLAIN the collision, and the cheapest fix for that is to delete the
        explanation.
        """
        import ast
        import inspect
        import textwrap

        def written_attrs(source):
            names = set()
            for node in ast.walk(ast.parse(textwrap.dedent(source))):
                if not isinstance(node, (ast.Assign, ast.AugAssign,
                                         ast.AnnAssign)):
                    continue
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target])
                stack = list(targets)
                while stack:
                    t = stack.pop()
                    if isinstance(t, (ast.Tuple, ast.List)):
                        stack.extend(t.elts)
                    elif (isinstance(t, ast.Attribute)
                          and isinstance(t.value, ast.Name)
                          and t.value.id == "self"):
                        names.add(t.attr)
            return names

        mixin = written_attrs(inspect.getsource(_GuardedEnv))
        subclass = written_attrs(inspect.getsource(DreamSchedulerTests))
        shared = mixin & subclass
        self.assertEqual(shared, set(),
                         "%s writes attribute(s) %s that _GuardedEnv also "
                         "writes; one instance namespace, two owners, and the "
                         "LIFO restores stop being independent"
                         % (DreamSchedulerTests.__name__, sorted(shared)))
        self.assertIn("_guardedenv_saved_cfg", mixin, "the mixin still saves")
        self.assertIn("_scheduler_saved_cfg", subclass, "the class still saves")
