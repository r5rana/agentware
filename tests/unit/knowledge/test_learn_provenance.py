"""Tests for the optional `learn --author/--source` provenance passthrough.

Feature 260625-team-mode-onboarding-fork. The flags are OPTIONAL and
backward-compatible: omitting BOTH must reproduce today's frontmatter exactly
(author defaults to the operator handle, source defaults to `agent`). Supplying
them stamps the entry's provenance, which feeds the EXISTING ACR source_weight
prior (no ranking change).

Hermetic: synthetic KB in a tempdir via the shared fixtures (R-LOC-03).
"""

import os
import shutil
import tempfile
import unittest

from tests._fixtures import SyntheticKBTestCase, load_cli


def _read_frontmatter(kdir, rel):
    mod = load_cli()
    with open(os.path.join(kdir, rel), encoding="utf-8") as f:
        text = f.read()
    fm, _body = mod.split_frontmatter(text)
    return fm


class LearnProvenanceTests(SyntheticKBTestCase):
    """A test that asserts a DEFAULT must own what the default resolves FROM.

    `SyntheticKBTestCase` isolates the KNOWLEDGE BASE, not the machine config.
    `learn`'s author default is `resolve_user_handle()` -- env, then
    `~/.agentware/config.env`, then `None` so the writer falls back to
    `_operator_handle(kdir)`. This class clears the env key but used to leave the
    CONFIG file alone, so on any machine whose operator has set a handle the verb
    stamped the REAL one while the assertion expected the synthetic KB's
    placeholder. Operator-dependent, and an R-LOC-03 read of live state.

    It passed in full runs for the wrong reason: `test_dream` left `HOME_CONFIG`
    pointing at a directory it had already deleted, so the config read found
    nothing. Repairing that guard (task 94) removed the accident and exposed
    this. A test that only passes because an earlier module corrupted global
    state is not a passing test.
    """

    def setUp(self):
        super().setUp()
        mod = load_cli()
        cfgdir = tempfile.mkdtemp(prefix="agentware-learnprov-cfg-")
        self.addCleanup(shutil.rmtree, cfgdir, True)
        cfg = os.path.join(cfgdir, "config.env")
        saved = (mod.HOME_CONFIG, mod.CONFIG_PATHS)

        def _restore():
            mod.HOME_CONFIG, mod.CONFIG_PATHS = saved
        self.addCleanup(_restore)
        mod.HOME_CONFIG, mod.CONFIG_PATHS = cfg, (cfg,)

    def _learn(self, topic, extra=None, env=None):
        argv = ["learn", "--topic", topic, "--summary", "S for %s" % topic,
                "--tags", "alpha,beta", "--content", "Body text for %s." % topic]
        if extra:
            argv += extra
        saved = {}
        if env:
            for k, v in env.items():
                saved[k] = os.environ.get(k)
                os.environ[k] = v
        try:
            return self.run_cli(argv)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_omitted_flags_byte_identical_to_today(self):
        # No per-user handle env, no flags => author == _operator_handle, source == agent.
        for k in ("AGENTWARE_USER_HANDLE",):
            os.environ.pop(k, None)
        code, _out, err = self._learn("prov-default")
        self.assertEqual(code, 0, err)
        fm = _read_frontmatter(self.kdir, "learnings/prov-default.md")
        mod = load_cli()
        expected_author = mod._operator_handle(self.kdir)
        self.assertEqual(fm["author"], expected_author)
        self.assertEqual(fm["source"], "agent")
        self.assertEqual(fm["source"], mod.DEFAULT_ENTRY_SOURCE)

    def test_author_and_source_written(self):
        code, _out, err = self._learn(
            "prov-explicit", extra=["--author", "alice", "--source", "user"])
        self.assertEqual(code, 0, err)
        fm = _read_frontmatter(self.kdir, "learnings/prov-explicit.md")
        self.assertEqual(fm["author"], "alice")
        self.assertEqual(fm["source"], "user")

    def test_invalid_source_rejected(self):
        code, _out, err = self._learn(
            "prov-bad", extra=["--source", "robot"])
        self.assertNotEqual(code, 0)
        self.assertIn("invalid --source", err)
        # Nothing should have been written/registered for a rejected entry.
        self.assertFalse(os.path.exists(
            os.path.join(self.kdir, "learnings/prov-bad.md")))

    def test_user_handle_env_default_author(self):
        # With a per-user handle configured (env tier), omitting --author uses it.
        code, _out, err = self._learn(
            "prov-env", env={"AGENTWARE_USER_HANDLE": "bob"})
        self.assertEqual(code, 0, err)
        fm = _read_frontmatter(self.kdir, "learnings/prov-env.md")
        self.assertEqual(fm["author"], "bob")
        self.assertEqual(fm["source"], "agent")

    def test_source_feeds_acr_ordering(self):
        # The whole point: source maps to the user>agent>imported prior.
        mod = load_cli()
        self.assertGreater(mod.source_weight("user"), mod.source_weight("agent"))
        self.assertGreater(mod.source_weight("agent"), mod.source_weight("imported"))


if __name__ == "__main__":
    unittest.main()
