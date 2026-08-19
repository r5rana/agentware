"""checkup opt-in flag + config wiring (Task 1, 260713-p5-checkup).

Runner: python3 -m unittest tests.unit.features.test_checkup_config -v

Proven here: `resolve_checkup()` defaults OFF; env on/off wins; a typo falls
through to the safe OFF default (never silently enables the unattended exam);
`config --set-checkup on|off` persists to config.env and an invalid value is
rejected (rc 2); the `--checkup-only` shell accessor prints on/off; and the
bounded-LLM kill switch `resolve_checkup_llm()` defaults ON. Hermetic: patched
HOME_CONFIG + a synthetic KB, so the operator's real config is never touched.
"""

import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, run_cli, build_synthetic_kb
except ImportError:
    from _fixtures import load_cli, run_cli, build_synthetic_kb


class CheckupConfigTests(unittest.TestCase):
    _KEYS = ("AGENTWARE_CHECKUP", "AGENTWARE_CHECKUP_LLM")

    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="aw_ck_cfg_")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.kdir = os.path.join(self.base, "kb")
        build_synthetic_kb(self.kdir)
        self._saved = (self.mod.HOME_CONFIG, self.mod.CONFIG_PATHS)
        self.mod.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")
        self.mod.CONFIG_PATHS = (self.mod.HOME_CONFIG,)
        self._env = {k: os.environ.get(k) for k in self._KEYS}
        for k in self._KEYS:
            os.environ.pop(k, None)
        self.addCleanup(self._restore)

    def _restore(self):
        self.mod.HOME_CONFIG, self.mod.CONFIG_PATHS = self._saved
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_off(self):
        self.assertEqual(self.mod.resolve_checkup(), "0")

    def test_env_on_off(self):
        os.environ["AGENTWARE_CHECKUP"] = "on"
        self.assertEqual(self.mod.resolve_checkup(), "1")
        os.environ["AGENTWARE_CHECKUP"] = "off"
        self.assertEqual(self.mod.resolve_checkup(), "0")

    def test_env_typo_falls_through_to_off(self):
        os.environ["AGENTWARE_CHECKUP"] = "mabye"
        self.assertEqual(self.mod.resolve_checkup(), "0",
                         "a typo must never silently ENABLE the exam")

    def test_set_checkup_persists_and_toggles(self):
        code, out, err = run_cli(["config", "--set-checkup", "on"], self.kdir)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.mod.resolve_checkup(), "1")   # config.env wins (env unset)
        code, _o, _e = run_cli(["config", "--set-checkup", "off"], self.kdir)
        self.assertEqual(code, 0)
        self.assertEqual(self.mod.resolve_checkup(), "0")

    def test_set_checkup_invalid_rejected(self):
        code, _o, err = run_cli(["config", "--set-checkup", "maybe"], self.kdir)
        self.assertEqual(code, 2)
        self.assertIn("invalid --set-checkup", err)

    def test_checkup_only_accessor(self):
        run_cli(["config", "--set-checkup", "on"], self.kdir)
        code, out, _e = run_cli(["config", "--checkup-only"], self.kdir)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "on")

    def test_llm_kill_switch_default_on(self):
        self.assertEqual(self.mod.resolve_checkup_llm(), "1")
        os.environ["AGENTWARE_CHECKUP_LLM"] = "0"
        self.assertEqual(self.mod.resolve_checkup_llm(), "0")


if __name__ == "__main__":
    unittest.main()
