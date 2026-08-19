"""The suite may never re-spawn itself through the CLI fixture.

Feature 260813-modular-core-adoption, task 60 (harness defect found while
verifying it).

The defect this locks
---------------------
Two verbs measure a test pass-rate by SHELLING the suite in a child process --
``retrieval.ledger._record_test_passrate_detail`` and
``ops.audit._audit_tests_check``, both ``python3 -m unittest discover -s tests``.
Their guard against re-entering the suite is the ``AGENTWARE_NESTED_UNITTEST``
env var, which the product sets on the child it spawns. Nothing sets it on the
PARENT, so a suite run under any runner starts unguarded, and the first test to
drive ``eval --record`` through the fixture spawns a whole nested suite -- one
that also collects ``tests/fixtures/`` (fixture DATA, including SWE task repos
carrying their own graders) and wedges. Measured: ``tests/unit/retrieval``
alone ran >10 minutes at 0.0% CPU against 10.5 seconds once guarded.

It read as intermittent because it is ORDER-dependent: classes that remember a
per-class ``setUp`` guard leave the variable set for whatever runs next in the
same process, so the same file passes or hangs depending on what preceded it.
Two classes had missed the guard. A per-class convention that a new test must
remember is not a guard; ``run_cli`` defaulting it is, so that is where the fix
lives and this is what proves it stays there.

Both claims below are behavioural, not bookkeeping: each drives the real
``eval --record`` path and fails if the spawn is reachable, rather than
asserting the string is present in the fixture source.
"""

import json
import os
import types
import unittest

from tests._fixtures import (NESTED_UNITTEST_ENV, SyntheticKBTestCase,
                             load_lib, run_cli)


class _SpawnRefused(AssertionError):
    """Raised in place of spawning the suite, so a regression FAILS not hangs."""


class NestedSuiteSpawnIsUnreachableTest(SyntheticKBTestCase):
    """`eval --record` through the fixture must not reach ``subprocess.run``."""

    def setUp(self):
        super().setUp()
        self.ledger = load_lib("agentware_lib.retrieval.ledger")
        # Module-LOCAL swap (not a patch of stdlib `subprocess.run`, which every
        # other test in the process shares): the ledger reads `subprocess` off
        # its own globals, so rebinding that name reaches exactly this call site.
        self._real = self.ledger.subprocess
        self.spawned = []

        def _boom(*a, **kw):
            self.spawned.append(a[0] if a else kw.get("args"))
            raise _SpawnRefused(
                "the suite re-spawned itself: %r" % (self.spawned[-1],))

        # `PIPE`/`OSError` are read off the same global at the call site, so the
        # stand-in must carry them or the probe fails for the wrong reason.
        self.ledger.subprocess = types.SimpleNamespace(
            run=_boom, PIPE=self._real.PIPE, STDOUT=self._real.STDOUT,
            DEVNULL=self._real.DEVNULL,
            CalledProcessError=self._real.CalledProcessError)
        self.addCleanup(setattr, self.ledger, "subprocess", self._real)

    def _gold(self):
        path = os.path.join(self.kdir, "recall-gold.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{"query": "bm25 ranking retrieval",
                        "expected_ids": ["ref-bm25-ranking"]}], f)
        return path

    def test_eval_record_never_shells_the_suite(self):
        code, out, err = self.run_cli(
            ["eval", "--gold", self._gold(), "--strategy", "bm25", "--record",
             "--format", "json"])
        self.assertEqual(self.spawned, [],
                         "a verb driven by the test fixture spawned the suite")
        self.assertEqual(code, 0, err)
        self.assertIn("recorded", json.loads(out))

    def test_the_recorded_row_says_not_measured_rather_than_guessing(self):
        """The guard's honest consequence, asserted rather than assumed.

        Suppressing the spawn must record ``test_pass_rate: None`` -- "not
        measured" -- and NOT a fabricated 1.0, which would feed a false
        reliability score into the release gate's no-regression comparison.
        """
        self.run_cli(["eval", "--gold", self._gold(), "--strategy", "bm25",
                      "--record", "--format", "json"])
        path = os.path.join(self.kdir, "benchmarks", "history.jsonl")
        with open(path, encoding="utf-8") as f:
            row = json.loads(f.read().splitlines()[-1])
        self.assertIsNone(row["checks"]["test_pass_rate"])
        self.assertTrue(row["checks"]["index_validate_ok"])

    def test_the_probe_itself_bites(self):
        """Non-vacuity: the swapped-in spawn really does fail the test.

        Without this, both assertions above would pass just as happily against a
        ledger that had stopped calling ``subprocess`` at all for an unrelated
        reason, and the gate would be measuring nothing.
        """
        prev = os.environ.get(NESTED_UNITTEST_ENV)
        os.environ.pop(NESTED_UNITTEST_ENV, None)
        try:
            with self.assertRaises(_SpawnRefused):
                self.ledger._record_test_passrate_detail()
        finally:
            if prev is not None:
                os.environ[NESTED_UNITTEST_ENV] = prev
        self.assertEqual(len(self.spawned), 1)
        self.assertIn("unittest", self.spawned[0])
        self.assertIn("discover", self.spawned[0])


class RunCliGuardScopeTest(unittest.TestCase):
    """The guard is defaulted for the CALL and never leaks or overrides."""

    def setUp(self):
        self.prev = os.environ.get(NESTED_UNITTEST_ENV)
        self.addCleanup(self._restore)
        self.seen = []

    def _restore(self):
        if self.prev is None:
            os.environ.pop(NESTED_UNITTEST_ENV, None)
        else:
            os.environ[NESTED_UNITTEST_ENV] = self.prev

    def _observe(self, kdir="/nonexistent-kb-for-a-guard-probe"):
        """Run the fixture with dispatch replaced by an env observer."""
        from tests import _fixtures
        mod = _fixtures.load_cli()
        real = mod._strangler.dispatch

        def _spy(argv, main):
            self.seen.append(os.environ.get(NESTED_UNITTEST_ENV))
            return 0

        mod._strangler.dispatch = _spy
        try:
            run_cli(["config"], kdir)
        finally:
            mod._strangler.dispatch = real
        return self.seen[-1]

    def test_an_unset_guard_is_defaulted_on_for_the_call(self):
        os.environ.pop(NESTED_UNITTEST_ENV, None)
        self.assertEqual(self._observe(), "1")
        self.assertIsNone(os.environ.get(NESTED_UNITTEST_ENV),
                          "run_cli leaked the guard into the caller")

    def test_an_explicit_caller_value_is_respected_and_restored(self):
        os.environ[NESTED_UNITTEST_ENV] = "0"
        self.assertEqual(self._observe(), "0",
                         "run_cli overrode a value the caller set explicitly")
        self.assertEqual(os.environ.get(NESTED_UNITTEST_ENV), "0")


if __name__ == "__main__":
    unittest.main()
