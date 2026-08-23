"""The shared probe sandbox, proven by breaking it.

`tests/_probe_sandbox.py` is the one place three behavioural gate harnesses get
their child environment from. It exists because the promise it carries -- that a
probe child never reads or writes the operator's real config or knowledge base
-- was written three times in prose and tested nowhere, and the three copies had
already drifted apart while every suite stayed green.

A shared module does not fix that on its own. It only moves the drift to ONE
place, where a single unguarded arm silently serves all three callers. So every
arm here gets a one-at-a-time proof: the value it produces, and the failure it
is supposed to make impossible.

Two arms are load-bearing beyond their size:

  * `extra` shipped as a dead parameter -- declared in the signature, documented
    as "applied LAST", and dropped by an `env.update({})` that overlaid nothing.
    The one caller that passed it got a child missing the variable it needed.
    Nothing caught it, because no test ever asked what the environment CONTAINED.
  * the redirect itself is asserted against what a real child RECEIVES, not
    against the dict this process built. A promise about a subprocess that is
    only ever checked in the parent is a promise about the wrong process.

Stdlib `unittest` only. Never touches the real knowledge base or config.
"""

from tests._paths import TESTS_DIR

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
sys.path.insert(0, HERE)

import _probe_sandbox as sandbox  # noqa: E402


class ScratchCase(unittest.TestCase):
    """A disposable root per test; nothing here outlives its case."""

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="aw-sandbox-test-")
        self.addCleanup(shutil.rmtree, self.scratch, True)


class SandboxEnvTest(ScratchCase):
    """`sandbox_env` as a pure value: what is set, removed, and overlaid."""

    def test_sandbox_both_redirect_keys_land_under_the_caller_scratch_root(self):
        env = sandbox.sandbox_env(self.scratch)
        config = env[sandbox.CONFIG_HOME_KEY]
        kdir = env[sandbox.KNOWLEDGE_DIR_KEY]
        self.assertEqual(config,
                         os.path.join(self.scratch, sandbox.CONFIG_HOME_LEAF))
        self.assertEqual(kdir,
                         os.path.join(self.scratch, sandbox.KNOWLEDGE_DIR_LEAF))
        # The point of a caller-owned root: removing it removes everything the
        # child could have written THROUGH either key.
        for path in (config, kdir):
            self.assertTrue(path.startswith(self.scratch + os.sep), path)

    def test_sandbox_the_two_leaves_are_distinct_directories(self):
        # One leaf serving both keys would let a config write land in the KB.
        env = sandbox.sandbox_env(self.scratch)
        self.assertNotEqual(env[sandbox.CONFIG_HOME_KEY],
                            env[sandbox.KNOWLEDGE_DIR_KEY])

    def test_sandbox_a_real_operator_value_is_never_inherited(self):
        saved = {k: os.environ.get(k)
                 for k in (sandbox.CONFIG_HOME_KEY, sandbox.KNOWLEDGE_DIR_KEY)}
        os.environ[sandbox.CONFIG_HOME_KEY] = "/real/config/home"
        os.environ[sandbox.KNOWLEDGE_DIR_KEY] = "/real/knowledge/dir"
        try:
            env = sandbox.sandbox_env(self.scratch)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertNotIn("/real/config/home", env[sandbox.CONFIG_HOME_KEY])
        self.assertNotIn("/real/knowledge/dir", env[sandbox.KNOWLEDGE_DIR_KEY])

    def test_sandbox_drop_removes_each_named_key(self):
        os.environ["AW_SANDBOX_DROP_ME"] = "present"
        self.addCleanup(os.environ.pop, "AW_SANDBOX_DROP_ME", None)
        env = sandbox.sandbox_env(self.scratch, drop=("AW_SANDBOX_DROP_ME",))
        self.assertNotIn("AW_SANDBOX_DROP_ME", env)

    def test_sandbox_dropping_an_absent_key_is_not_an_error(self):
        env = sandbox.sandbox_env(self.scratch, drop=("AW_NEVER_SET_ANYWHERE",))
        self.assertNotIn("AW_NEVER_SET_ANYWHERE", env)

    def test_sandbox_pythonpath_is_set_only_when_given(self):
        env = sandbox.sandbox_env(self.scratch, pythonpath="/a/b")
        self.assertEqual(env["PYTHONPATH"], "/a/b")

    # --- the arm that shipped dead -------------------------------------------

    def test_sandbox_extra_is_applied_and_not_silently_dropped(self):
        # The regression: `extra` was declared, documented, and overlaid
        # nothing. A caller's key simply never reached its child.
        env = sandbox.sandbox_env(self.scratch, extra={"AW_EXTRA_KEY": "value"})
        self.assertEqual(env.get("AW_EXTRA_KEY"), "value",
                         "extra= was accepted and then discarded")

    def test_sandbox_extra_is_applied_LAST_so_a_caller_may_override_a_redirect(self):
        # The docstring promises precedence, which is a different claim from
        # "extra is applied at all" and fails separately.
        env = sandbox.sandbox_env(
            self.scratch, extra={sandbox.CONFIG_HOME_KEY: "/caller/choice"})
        self.assertEqual(env[sandbox.CONFIG_HOME_KEY], "/caller/choice")

    def test_sandbox_extra_overlays_rather_than_replacing_the_environment(self):
        env = sandbox.sandbox_env(self.scratch, extra={"AW_EXTRA_KEY": "value"})
        self.assertIn(sandbox.KNOWLEDGE_DIR_KEY, env)
        self.assertIn("PATH", env)

    def test_sandbox_extra_defaults_to_no_overlay(self):
        env = sandbox.sandbox_env(self.scratch)
        self.assertNotIn("AW_EXTRA_KEY", env)

    def test_sandbox_drop_then_extra_lets_a_caller_reinstate_a_dropped_key(self):
        # Order matters and is observable: drop runs before the overlay.
        os.environ["AW_SANDBOX_ROUNDTRIP"] = "inherited"
        self.addCleanup(os.environ.pop, "AW_SANDBOX_ROUNDTRIP", None)
        env = sandbox.sandbox_env(self.scratch,
                                  drop=("AW_SANDBOX_ROUNDTRIP",),
                                  extra={"AW_SANDBOX_ROUNDTRIP": "restored"})
        self.assertEqual(env["AW_SANDBOX_ROUNDTRIP"], "restored")


class ChildReceivesTest(ScratchCase):
    """What the CHILD gets -- the process the promise is actually about."""

    def child_env(self, **kwargs):
        env = sandbox.sandbox_env(self.scratch, **kwargs)
        probe = sandbox.run_json_probe(
            "import json, os; print(json.dumps(dict(os.environ)))", env)
        self.assertIsNone(probe.kind, getattr(probe, "stderr", probe.kind))
        return probe.payload

    def test_sandbox_the_child_receives_the_redirected_keys(self):
        seen = self.child_env()
        self.assertEqual(seen.get(sandbox.CONFIG_HOME_KEY),
                         os.path.join(self.scratch, sandbox.CONFIG_HOME_LEAF))
        self.assertEqual(seen.get(sandbox.KNOWLEDGE_DIR_KEY),
                         os.path.join(self.scratch, sandbox.KNOWLEDGE_DIR_LEAF))

    def test_sandbox_the_child_receives_the_extra_overlay(self):
        # The end-to-end shape of the dead-parameter bug, from the caller's
        # side: this is the assertion whose absence let it ship.
        seen = self.child_env(extra={"AW_EXTRA_KEY": "value"})
        self.assertEqual(seen.get("AW_EXTRA_KEY"), "value")

    def test_sandbox_the_child_does_not_receive_a_dropped_key(self):
        os.environ["AW_SANDBOX_DROP_ME"] = "present"
        self.addCleanup(os.environ.pop, "AW_SANDBOX_DROP_ME", None)
        seen = self.child_env(drop=("AW_SANDBOX_DROP_ME",))
        self.assertNotIn("AW_SANDBOX_DROP_ME", seen)


class OnDiskShapeTest(ScratchCase):
    """The two directory shapes a child may need to exist before it runs."""

    def test_sandbox_ensure_config_home_creates_the_redirected_directory(self):
        env = sandbox.sandbox_env(self.scratch)
        self.assertFalse(os.path.isdir(env[sandbox.CONFIG_HOME_KEY]))
        path = sandbox.ensure_config_home(env)
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(path, env[sandbox.CONFIG_HOME_KEY])

    def test_sandbox_ensure_config_home_is_idempotent(self):
        env = sandbox.sandbox_env(self.scratch)
        sandbox.ensure_config_home(env)
        self.assertTrue(os.path.isdir(sandbox.ensure_config_home(env)))

    def test_sandbox_seed_knowledge_dir_writes_both_onboarding_marks(self):
        # Sentinel AND entry document: a directory carrying one of the two
        # reads as not-initialized, which is a different code path from the one
        # a behavioural gate means to exercise.
        env = sandbox.sandbox_env(self.scratch)
        kdir = sandbox.seed_knowledge_dir(env)
        self.assertTrue(os.path.isfile(os.path.join(kdir, ".initialized")))
        self.assertTrue(os.path.isfile(os.path.join(kdir, "MAIN.md")))

    def test_sandbox_seed_knowledge_dir_lands_inside_the_scratch_root(self):
        env = sandbox.sandbox_env(self.scratch)
        kdir = sandbox.seed_knowledge_dir(env)
        self.assertTrue(kdir.startswith(self.scratch + os.sep), kdir)


class RunEntryTest(ScratchCase):
    """A child that ran is data; only one that never started is a `kind`."""

    def test_sandbox_a_child_that_ran_carries_no_failure_kind(self):
        env = sandbox.sandbox_env(self.scratch)
        probe = sandbox.run_entry([sys.executable, "-c", "print('hi')"], env)
        self.assertIsNone(probe.kind)
        self.assertEqual(probe.returncode, 0)
        self.assertIn("hi", probe.stdout)

    def test_sandbox_a_nonzero_child_is_data_not_a_failure_of_run_entry(self):
        env = sandbox.sandbox_env(self.scratch)
        probe = sandbox.run_entry([sys.executable, "-c", "raise SystemExit(3)"],
                                  env)
        self.assertIsNone(probe.kind, "a ran-and-failed child is the caller's")
        self.assertEqual(probe.returncode, 3)

    def test_sandbox_a_child_that_cannot_start_is_could_not_run(self):
        env = sandbox.sandbox_env(self.scratch)
        probe = sandbox.run_entry(
            [os.path.join(self.scratch, "no-such-binary")], env)
        self.assertEqual(probe.kind, sandbox.COULD_NOT_RUN)


class RunJsonProbeTest(ScratchCase):
    """The three failures, and the ORDER that decides between them."""

    def run_source(self, source):
        return sandbox.run_json_probe(source,
                                      sandbox.sandbox_env(self.scratch))

    def test_sandbox_a_json_payload_is_decoded(self):
        probe = self.run_source("import json; print(json.dumps({'ok': True}))")
        self.assertIsNone(probe.kind)
        self.assertEqual(probe.payload, {"ok": True})

    def test_sandbox_a_nonzero_child_is_nonzero_exit(self):
        probe = self.run_source("raise SystemExit(2)")
        self.assertEqual(probe.kind, sandbox.NONZERO_EXIT)

    def test_sandbox_a_clean_child_printing_garbage_is_no_json(self):
        probe = self.run_source("print('not json')")
        self.assertEqual(probe.kind, sandbox.NO_JSON)

    def test_sandbox_a_nonzero_child_printing_garbage_is_NOT_reported_as_no_json(self):
        # The ordering claim: a child that exited non-zero had no reason to
        # print a payload, so a decode complaint would name the wrong cause.
        probe = self.run_source("print('not json'); raise SystemExit(1)")
        self.assertEqual(probe.kind, sandbox.NONZERO_EXIT)

    def test_sandbox_a_failed_child_yields_no_payload(self):
        for source in ("raise SystemExit(1)", "print('not json')"):
            self.assertIsNone(getattr(self.run_source(source), "payload", None),
                              source)


class FailureVocabularyTest(unittest.TestCase):
    """`FAILURES` is CLOSED: a fourth kind is one no caller has words for."""

    def test_sandbox_failures_is_exactly_the_three_named_kinds(self):
        self.assertEqual(sandbox.FAILURES,
                         (sandbox.COULD_NOT_RUN, sandbox.NONZERO_EXIT,
                          sandbox.NO_JSON))

    def test_sandbox_every_failure_kind_is_a_distinct_nonempty_string(self):
        self.assertEqual(len(set(sandbox.FAILURES)), len(sandbox.FAILURES))
        for kind in sandbox.FAILURES:
            self.assertTrue(kind and isinstance(kind, str), kind)

    def test_sandbox_every_kind_a_probe_can_report_is_in_the_closed_set(self):
        # Binds the vocabulary to the two functions that assign from it, so a
        # new kind cannot be introduced at a call site without landing here.
        scratch = tempfile.mkdtemp(prefix="aw-sandbox-vocab-")
        self.addCleanup(shutil.rmtree, scratch, True)
        env = sandbox.sandbox_env(scratch)
        observed = {
            sandbox.run_json_probe("raise SystemExit(1)", env).kind,
            sandbox.run_json_probe("print('not json')", env).kind,
            sandbox.run_entry([os.path.join(scratch, "nope")], env).kind,
        }
        self.assertTrue(observed <= set(sandbox.FAILURES), observed)


class ImportDirectionTest(unittest.TestCase):
    """The sandbox sits BELOW every gate; nothing here may import one."""

    def test_sandbox_imports_no_gate_module(self):
        with open(os.path.join(HERE, "_probe_sandbox.py"), encoding="utf-8") as fh:
            body = fh.read()
        for offender in ("_dreampkg_gate", "_kbtarget_gate", "_orgidentity_gate",
                         "_dreampkg_retired", "_kbtarget_probe",
                         "_orgidentity_probe"):
            self.assertNotIn("import %s" % offender, body, offender)


if __name__ == "__main__":
    unittest.main()
