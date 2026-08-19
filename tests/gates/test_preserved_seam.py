"""TerminalStateSymbolProbeTest, LibNameResolutionRuleTest, MirrorNoneIsAValueNotAnAbsenceTest, MirroredNamesReachConsumersNotOnlyTheOwnerTest, TheSeamIsSingleInstanceTest -- split from `test_preserved_gates` (task 58).

See `tests.gates._preserved_gates_support` for the
shared header.
"""

from tests.gates._preserved_gates_support import (  # noqa: F401
    importlib, load_fixture, os, sys, types, unittest)


class TerminalStateSymbolProbeTest(unittest.TestCase):
    """Task 56: the branch `reachable_symbols` takes once the monolith is gone.

    The absent-monolith arm of `_PROBE` resolves a frozen name through
    `agentware_lib` using the suite's ONE resolver. These pin BOTH halves of
    that in the tree as it stands today -- with the monolith still present --
    so the deletion at task 96 cannot be the first time either is exercised.
    """

    def test_every_required_frozen_symbol_is_OWNED_by_a_lib_module(self):
        """The real invariant, measured independently of the facade.

        `check_destination` asks whether a name resolves on the CLI MODULE. This
        asks the stronger, facade-free question the terminal state actually turns
        on: does some `agentware_lib` module DEFINE (or re-export) it? If this is
        green, deleting the monolith loses no frozen symbol -- and if it ever
        reddens, the absent-monolith arm of the probe is resolving nothing and
        the freeze has a genuine LOSS to report rather than a namespace move.
        """
        import _fixtures
        fx = load_fixture()
        names = set(fx["cli_symbols"]["by_symbol"])
        required = names - set(fx["cli_symbols"]["pending_in_destination"])
        self.assertGreaterEqual(len(required), 350)
        unowned = sorted(n for n in required
                         if _fixtures._lib_owner_of(n)[0] is None)
        self.assertEqual(unowned, [])

    def test_the_lib_resolver_still_says_NO(self):
        """Non-vacuity for the fallback itself.

        A resolver that answered YES to everything would make the terminal-state
        probe report a tree with nothing behind it as fully preserved.
        """
        import _fixtures
        self.assertEqual(
            _fixtures._lib_owner_of("aw_symbol_no_tree_has_ever_defined")[0],
            None)

    def test_the_probe_uses_the_suites_ONE_name_resolver(self):
        """Structural pin: the gate must not grow a second resolution rule.

        `_PROBE` answers "does this name resolve at `root`" through
        `_fixtures.attach_lib_resolver`, the same `__getattr__` that ~3,000
        `CLI.<symbol>` reads go through. A private walk here would be a second
        definition of where a legacy name lives, free to disagree with the one
        the suite actually runs on -- and it would not follow task 95's decision
        about `load_cli()` when that lands.
        """
        from _preserved_freeze import _PROBE
        self.assertIn("import _fixtures", _PROBE)
        self.assertIn("_fixtures.attach_lib_resolver(m)", _PROBE)
        self.assertNotIn("walk_packages", _PROBE)

    def test_the_probe_answers_the_same_way_in_BOTH_monolith_states(self):
        """No branch on the monolith's presence -- one fixture serves both.

        Branching was tried first: resolve through the facade namespace alone
        while the monolith stands, through the lib once it is gone. It makes the
        two states disagree about the five donor symbols that landed in the lib
        during the port wave, so the single frozen `pending_in_destination` set
        cannot satisfy both, and a shrink-only ratchet has no one answer to
        shrink toward. Pinned so it is not reintroduced.
        """
        from _preserved_freeze import _PROBE
        self.assertNotIn("agentware_legacy.py", _PROBE)

class LibNameResolutionRuleTest(unittest.TestCase):
    """Task 94: WHICH module-valued names the shim reproduces, and how.

    The monolith opens with `import subprocess`, `import time`, `import os`,
    `import json` -- so those module OBJECTS really are part of the namespace
    `_ConfigMirrorModule` stands in for, and three `test_review_orchestrator`
    classes (20 tests) patch the real spawn seam through `CLI.subprocess.run`.
    b6a9de6 skipped EVERY `ModuleType` value to fix a genuine collision
    (`agentware_lib.retrieval.acr`, the submodule, shadowing the function
    inside it) and took the stdlib modules down with it. Both halves are
    pinned here so neither correction can undo the other.
    """

    def setUp(self):
        import _fixtures
        self.fx = _fixtures
        self.mod = _fixtures.load_cli()

    def test_an_external_module_resolves_to_the_sys_modules_singleton(self):
        """...which is what makes `CLI.subprocess.run = fake` reach the lib.

        There is only ever one `subprocess` module object, so mutating an
        attribute on it is seen by every importer. If this ever resolved to a
        COPY, the 20 real-spawn-seam tests would stub something nobody reads.
        """
        import subprocess
        import time
        self.assertIs(self.mod.subprocess, sys.modules["subprocess"])
        self.assertIs(self.mod.subprocess, subprocess)
        self.assertIs(self.mod.time, time)
        import agentware_lib.features.reviewagent as reviewagent
        self.assertIs(reviewagent.subprocess, self.mod.subprocess)

    def test_an_agentware_lib_submodule_is_still_NOT_a_symbol(self):
        """The b6a9de6 rule, kept: narrowing the skip must not widen it back.

        `agentware_lib.retrieval` carries an attribute `acr` pointing at the
        submodule while the suite wants the FUNCTION defined inside it. Binding
        the package attribute made `CLI.acr(...)` raise "module object is not
        callable" in 16 tests.
        """
        self.assertTrue(callable(self.mod.acr))
        self.assertNotIsInstance(self.mod.acr, type(sys))
        # Measured on a FRESH namespace the shim alone populates. Asking the
        # cached CLI module instead would grade the monolith's own header
        # (`import agentware_lib.features.migratecmd as _kbmigrate`) and the
        # facade's `_regressiongate` alias as shim leaks, which they are not --
        # and the answer would change the moment task 96 deletes that header.
        probe = types.ModuleType("aw94_populate_probe")
        self.fx._populate_lib_names(probe)
        leaked = sorted(
            n for n, v in vars(probe).items()
            if isinstance(v, type(sys))
            and getattr(v, "__name__", "").startswith("agentware_lib"))
        self.assertEqual(leaked, [])
        # ...while the external modules the monolith imports DID land.
        self.assertIs(probe.subprocess, sys.modules["subprocess"])

    def test_the_narrowing_is_measured_not_assumed(self):
        """Non-vacuity: the predicate must actually separate the two cases."""
        import subprocess
        import agentware_lib.retrieval as retrieval
        self.assertFalse(self.fx._is_lib_submodule_ref(subprocess))
        self.assertTrue(self.fx._is_lib_submodule_ref(retrieval))
        self.assertFalse(self.fx._is_lib_submodule_ref(len))

    def test_a_wholesale_module_REBIND_is_refused_naming_the_real_seam(self):
        """Neither silent outcome is allowed.

        Forwarding `CLI.os = stub` would swap the module out of the 144 lib
        modules that import it; NOT forwarding lands it on the CLI module alone
        while every implementation keeps reading the real one. Both are silent,
        so the write-through refuses and names the seam that works.
        """
        with self.assertRaises(AttributeError) as ctx:
            self.mod.subprocess = types.SimpleNamespace(run=lambda *a, **k: None)
        msg = str(ctx.exception)
        self.assertIn("refusing to rebind module attribute", msg)
        self.assertIn("subprocess", msg)
        # ...and the refusal LEFT the real module in place.
        self.assertIs(self.mod.subprocess, sys.modules["subprocess"])

    def test_the_refusal_does_not_touch_ordinary_write_through(self):
        """Non-vacuity for the guard: a normal patch still forwards to the
        OWNING lib module, which is the whole point of the shim."""
        import agentware_lib.features.reviewagent as reviewagent
        original = self.mod._review_spawn_once
        sentinel = lambda *a, **k: {"aw94": True}
        try:
            self.mod._review_spawn_once = sentinel
            self.assertIs(reviewagent._review_spawn_once, sentinel)
        finally:
            self.mod._review_spawn_once = original
        self.assertIs(reviewagent._review_spawn_once, original)

class MirrorNoneIsAValueNotAnAbsenceTest(unittest.TestCase):
    """Task 94: the write-through must tell `previous is None` from NO previous.

    `_ConfigMirrorModule.__setattr__` keys its fan-out on the value being
    REPLACED, read with `self.__dict__.get(name)`. That returned None for two
    different facts -- "there is no previous value" (forward nowhere, by
    design) and "the previous value WAS None" (forward, like any other value)
    -- and `_lib_bindings` excluded both.

    The cost was not one skipped write but a PERMANENT hole: `X = None` is the
    ubiquitous "turn this feature off" patch, and once it landed on the mirror
    alone every lib copy stayed at None *and* every later write had
    `previous is None` too, so it forwarded nowhere either. The mirror reported
    the new value while the product read None for the rest of the process,
    across test modules. Measured on `SKILL_PIN_FLOOR`: 4 failures in
    `test_skill_pin_floor`, 3 of them only in-suite.

    The monolith hid all of it -- `exec`'d into the CLI globals, its own
    `retrieve_bm25` read the module global the test had just written, so no
    forwarding was needed to make the assertion pass.
    """

    def setUp(self):
        import _fixtures
        self.fx = _fixtures
        self.mod = _fixtures.load_cli()

    def test_a_genuine_None_previous_still_forwards(self):
        """None -> value must reach the lib copies, not the mirror alone."""
        import agentware_lib.retrieval.pinpolicy as pinpolicy
        import agentware_lib.retrieval.bm25 as bm25
        import agentware_lib.retrieval.skillpin as skillpin
        original = self.mod.SKILL_PIN_FLOOR
        try:
            self.mod.SKILL_PIN_FLOOR = None        # the "disable it" patch
            self.assertIsNone(pinpolicy.SKILL_PIN_FLOOR)
            self.mod.SKILL_PIN_FLOOR = 0.01        # ...and back OFF None again
            for m in (pinpolicy, bm25, skillpin):
                self.assertEqual(m.SKILL_PIN_FLOOR, 0.01,
                                 "%s kept the stale None" % m.__name__)
        finally:
            self.mod.SKILL_PIN_FLOOR = original
        self.assertEqual(pinpolicy.SKILL_PIN_FLOOR, original)

    def test_the_hole_was_PERMANENT_not_one_skipped_write(self):
        """Restoring after a None patch must leave no lib copy behind.

        This is the property the round trip above buys: a tearDown that writes
        the saved value back has to reach every copy, or the NEXT test in the
        process grades against None.
        """
        import agentware_lib.retrieval.pinpolicy as pinpolicy
        import agentware_lib.retrieval.bm25 as bm25
        import agentware_lib.retrieval.skillpin as skillpin
        original = self.mod.SKILL_PIN_FLOOR
        self.mod.SKILL_PIN_FLOOR = None
        self.mod.SKILL_PIN_FLOOR = original        # what tearDown does
        for m in (pinpolicy, bm25, skillpin):
            self.assertEqual(m.SKILL_PIN_FLOOR, original,
                             "%s survived the None round trip stale" % m.__name__)

    def test_an_ABSENT_previous_still_forwards_NOWHERE(self):
        """The other half: no previous value keeps the documented behaviour.

        Non-vacuity for the narrowing -- distinguishing absence from None must
        not turn every write on an unread name into a fan-out.
        """
        probe = types.ModuleType("aw94_absent_probe")
        probe.__class__ = self.fx._ConfigMirrorModule
        bindings = self.fx._ConfigMirrorModule._lib_bindings
        self.assertEqual(bindings(probe, "SKILL_PIN_FLOOR", self.fx._ABSENT), [])
        # ...while the SAME name with a real previous value resolves bindings,
        # so the empty answer above is the sentinel and not a dead lookup.
        self.assertTrue(bindings(probe, "SKILL_PIN_FLOOR",
                                 self.mod.SKILL_PIN_FLOOR))
        # ...and a previous of None resolves them too, once None is what the
        # lib copies actually hold -- the case the old exclusion could not see.
        original = self.mod.SKILL_PIN_FLOOR
        try:
            self.mod.SKILL_PIN_FLOOR = None
            self.assertTrue(bindings(probe, "SKILL_PIN_FLOOR", None))
        finally:
            self.mod.SKILL_PIN_FLOOR = original

    def test_the_sentinel_is_not_confusable_with_None(self):
        """A value-level check, so the two can never collapse back together."""
        self.assertIsNot(self.fx._ABSENT, None)
        self.assertNotEqual(self.fx._ABSENT, None)
        self.assertFalse(self.fx._ABSENT is None)

    def test_the_product_default_is_the_SHIPPED_pin_not_OFF(self):
        """The reason the round trip matters: the pin is ENABLED at 0.85.

        A stale None left in the lib copies does not merely mis-set a test
        knob -- it turns the shipped R-RET-03 stage-1 skill precedence OFF for
        every later ranking assertion in the process.
        """
        import agentware_lib.retrieval.pinpolicy as pinpolicy
        self.assertEqual(pinpolicy.SKILL_PIN_FLOOR, 0.85)

class MirroredNamesReachConsumersNotOnlyTheOwnerTest(unittest.TestCase):
    """Task 94: the two `_MIRRORED` config globals must reach CONSUMERS too.

    `__setattr__` special-cases `HOME_CONFIG` / `CONFIG_PATHS`: it writes them
    to `agentware_lib.core.config` -- the OWNER -- and used to `return` there.
    A lib module that does `from agentware_lib.core.config import HOME_CONFIG`
    binds the VALUE at import, not the module, so the owner write never reached
    it and it kept the operator's REAL `~/.agentware` for the whole process.

    That is the failure this class exists to PREVENT, inverted. The mirror was
    written because a test rebinding `CLI.HOME_CONFIG` would otherwise fall
    back to live state -- "not a failing test, a dangerous passing one" -- and
    for import-by-value consumers the mirror did exactly that.

    The monolith hid it completely: `CLI._skill_lock_path` resolved to the
    monolith's copy, whose `__globals__` IS the CLI module dict, so it read the
    rebind straight back and no consumer was ever consulted. Deleting the
    monolith is what made `features.skilllock` resolve instead, and
    `test_lock_path_default_outside_kb` fail against the real home path.
    """

    def setUp(self):
        import _fixtures
        self.fx = _fixtures
        self.mod = _fixtures.load_cli()

    def test_a_mirrored_name_reaches_an_import_by_value_consumer(self):
        """The live instance, asserted on the consumer's own module global."""
        import agentware_lib.core.config as libconfig
        import agentware_lib.features.skilllock as skilllock
        original = self.mod.HOME_CONFIG
        redirected = os.path.join(os.sep, "tmp", "aw94-mirror-probe",
                                  ".agentware", "config.env")
        try:
            self.mod.HOME_CONFIG = redirected
            self.assertEqual(libconfig.HOME_CONFIG, redirected,
                             "the OWNER lost its write")
            self.assertEqual(skilllock.HOME_CONFIG, redirected,
                             "the CONSUMER kept the real home")
        finally:
            self.mod.HOME_CONFIG = original
        # The restore must reach the consumer too, or the next test in this
        # process resolves a lockfile path under /tmp.
        self.assertEqual(skilllock.HOME_CONFIG, original)
        self.assertEqual(libconfig.HOME_CONFIG, original)

    def test_the_consumer_is_what_the_BEHAVIOUR_reads(self):
        """Not just the global: the function's answer has to move with it.

        Asserting the module attribute alone would still pass if
        `_skill_lock_path` had been rewritten to read the owner directly, which
        is a different repair with different consequences. This pins the
        observable the failing test actually asserted.
        """
        import agentware_lib.features.skilllock as skilllock
        prev_env = os.environ.pop("AGENTWARE_SKILLS_LOCK_PATH", None)
        original = self.mod.HOME_CONFIG
        root = os.path.join(os.sep, "tmp", "aw94-mirror-probe2")
        try:
            self.mod.HOME_CONFIG = os.path.join(root, ".agentware",
                                                "config.env")
            self.assertEqual(
                skilllock._skill_lock_path(),
                os.path.join(root, ".agentware", "skills.lock.json"))
        finally:
            self.mod.HOME_CONFIG = original
            if prev_env is not None:
                os.environ["AGENTWARE_SKILLS_LOCK_PATH"] = prev_env
        self.assertNotIn("aw94-mirror-probe2", skilllock._skill_lock_path())

    def test_the_owner_is_still_written_with_NO_previous_value(self):
        """The unconditional half, kept: `_lib_bindings` forwards an absent
        previous NOWHERE, but the owner write may not depend on it.

        `load_cli` applies this class only AFTER exec so the module body's own
        assignment of the real home cannot clobber a redirect an outer test
        installed on the lib -- an ordering that is only safe while the owner
        write is unconditional.
        """
        import agentware_lib.core.config as libconfig
        probe = types.ModuleType("aw94_mirror_absent_probe")
        probe.__class__ = self.fx._ConfigMirrorModule
        self.assertEqual(
            self.fx._ConfigMirrorModule._lib_bindings(
                probe, "HOME_CONFIG", self.fx._ABSENT), [])
        original = libconfig.HOME_CONFIG
        try:
            probe.HOME_CONFIG = "/tmp/aw94-absent-owner/.agentware/config.env"
            self.assertEqual(libconfig.HOME_CONFIG,
                             "/tmp/aw94-absent-owner/.agentware/config.env")
        finally:
            libconfig.HOME_CONFIG = original
            self.mod.HOME_CONFIG = original

class TheSeamIsSingleInstanceTest(unittest.TestCase):
    """Task 95: ONE CLI module object, whichever spelling imported it.

    `discover -s tests -t .` puts BOTH `tests/` and the `tests` package on the
    path, and the suite uses both spellings of this file -- `import _fixtures`
    and `from tests._fixtures import ...`. Python executes it twice, and each
    copy used to build its OWN CLI module with its OWN mirror class.

    Two instances defeat the seam without failing anything: a write through one
    reaches the lib owner, so the product sees the patch, while the other
    copy's dict keeps the pre-patch value. A test that reads `CLI.<name>` to
    save "the current value" and writes it back in a cleanup then restores a
    STALE value over a live redirect -- the same silent shape the mirror exists
    to prevent, one level up.
    """

    def setUp(self):
        import _fixtures
        self.mod = _fixtures.load_cli()

    def test_both_import_spellings_yield_the_SAME_cli_module(self):
        import _fixtures as flat
        from tests import _fixtures as pkg
        self.assertIsNot(flat, pkg,
                         "premise: the suite really does hold two copies of "
                         "_fixtures; if this ever stops being true the "
                         "single-instance rule below is untested, not safe")
        self.assertIs(flat.load_cli(), pkg.load_cli())

    def test_a_write_through_one_spelling_is_VISIBLE_through_the_other(self):
        """The observable, not just object identity.

        Identity alone would still pass if a future refactor handed out
        equal-but-separate namespaces, so assert what the tests actually
        depend on: the value moves.
        """
        import _fixtures as flat
        from tests import _fixtures as pkg
        a, b = flat.load_cli(), pkg.load_cli()
        original = a.HOME_CONFIG
        redirected = os.path.join(os.sep, "tmp", "aw95-one-seam",
                                  ".agentware", "config.env")
        try:
            a.HOME_CONFIG = redirected
            self.assertEqual(b.HOME_CONFIG, redirected)
        finally:
            a.HOME_CONFIG = original
        self.assertEqual(b.HOME_CONFIG, original)

    def test_mock_patch_object_round_trips_through_the_seam(self):
        """The dominant real shape, and the one that fails SILENTLY.

        `mock.patch.object` reads `target.__dict__[name]` to decide whether the
        attribute is local. For a name reachable only through `__getattr__` it
        takes the non-local path and DELETES the attribute on exit instead of
        restoring it -- so the owning lib module keeps the mock for the rest of
        the process and every later test grades against it. `_populate_lib_names`
        exists to stop that; this asserts the outcome rather than the mechanism,
        on the OWNING module, in all three phases.
        """
        from unittest import mock
        import agentware_lib.features.reviewagent as reviewagent
        before = reviewagent._review_spawn_once
        sentinel = lambda *a, **k: {"aw95": True}
        with mock.patch.object(self.mod, "_review_spawn_once", sentinel):
            self.assertIs(reviewagent._review_spawn_once, sentinel,
                          "the patch never reached the owning module")
        self.assertIs(reviewagent._review_spawn_once, before,
                      "the owning module kept the mock after the patch exited")
        self.assertIn("_review_spawn_once", vars(self.mod),
                      "the name was deleted from the CLI module instead of "
                      "restored, which is how the leak starts")

    def test_the_shared_key_is_not_reachable_by_an_import_statement(self):
        """It may be a `sys.modules` key without being an import path.

        A key something could `import` would be acquired by accident, and a key
        under `agentware_lib` would be scanned by the write-through as if it
        OWNED every name it re-exports.
        """
        import _fixtures as flat
        self.assertFalse(flat._CLI_SYS_KEY.startswith("agentware_lib"))
        self.assertIsNone(
            importlib.util.find_spec(flat._CLI_SYS_KEY)
            if flat._CLI_SYS_KEY not in sys.modules else None)
        self.assertIs(sys.modules.get(flat._CLI_SYS_KEY), flat.load_cli())
