"""Updater activation + consent tests (feature 260712, Task 14).

Covers the opt-in kill switch: config --set-updater on|off persistence + --updater-only
reader + AGENTWARE_UPDATE env override, the tick install-guard integration (the REAL
registry refuses install when the updater is OFF and allows it when ON), the read-only
no-network SessionStart status-line (silent when clean, surfaces stranded/unfinalized/
tampered), and the onboarding Step 7b-5 opt-in prompt. Hermetic (R-LOC-03).

Runner: python3 -m unittest tests.unit.features.test_updater_activation -v
"""

from tests._paths import REPO_ROOT

import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, run_cli
except ImportError:
    from _fixtures import load_cli, run_cli



class _Base(unittest.TestCase):
    _KEYS = ("HOME", "AGENTWARE_UPDATE", "AGENTWARE_NESTED_UNITTEST")

    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-act-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        self.kdir = os.path.join(self.base, "kb")
        os.makedirs(self.home)
        os.makedirs(self.kdir)
        self._saved = (self.mod.HOME_CONFIG, self.mod.CONFIG_PATHS)
        self._env = {k: os.environ.get(k) for k in self._KEYS}
        self.addCleanup(self._restore)
        cfg = os.path.join(self.home, ".agentware", "config.env")
        self.mod.HOME_CONFIG = cfg
        self.mod.CONFIG_PATHS = (cfg,)
        os.environ.pop("AGENTWARE_UPDATE", None)
        os.environ["HOME"] = self.home
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"

    def _restore(self):
        self.mod.HOME_CONFIG, self.mod.CONFIG_PATHS = self._saved
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class ConfigSetterTests(_Base):
    def test_default_off(self):
        code, out, _e = run_cli(["config", "--updater-only"], self.kdir)
        self.assertEqual(out.strip(), "off")

    def test_set_on_then_read(self):
        code, _o, _e = run_cli(["config", "--set-updater", "on"], self.kdir)
        self.assertEqual(code, 0)
        self.assertEqual(self.mod.resolve_updater(), "1")
        code, out, _e = run_cli(["config", "--updater-only"], self.kdir)
        self.assertEqual(out.strip(), "on")

    def test_set_off(self):
        run_cli(["config", "--set-updater", "on"], self.kdir)
        run_cli(["config", "--set-updater", "off"], self.kdir)
        self.assertEqual(self.mod.resolve_updater(), "0")

    def test_invalid_value_rc2(self):
        code, _o, err = run_cli(["config", "--set-updater", "bogus"], self.kdir)
        self.assertEqual(code, 2)
        self.assertIn("invalid --set-updater", err)

    def test_env_overrides_config(self):
        run_cli(["config", "--set-updater", "off"], self.kdir)
        os.environ["AGENTWARE_UPDATE"] = "on"
        self.assertEqual(self.mod.resolve_updater(), "1")


class InstallGuardTests(_Base):
    def test_install_refused_when_updater_off(self):
        # REAL registry (updater job) — OFF => no job enabled => refuse.
        rc = self.mod._tick_install_schedule(self.kdir, as_json=False)
        self.assertEqual(rc, 2)

    def test_install_allowed_when_updater_on(self):
        os.environ["AGENTWARE_UPDATE"] = "on"
        rc = self.mod._tick_install_schedule(self.kdir, as_json=False)
        self.assertEqual(rc, 0)
        if sys.platform == "darwin":
            self.assertTrue(os.path.exists(self.mod._tick_launchd_plist_path()))

    def test_kill_switch_skips_job_without_uninstall(self):
        # Job disabled when OFF (skips), preserving the installed tick for P2c.
        res = self.mod._tick_dispatch(self.kdir, now=1000)
        by = {r["job"]: r["status"] for r in res}
        self.assertEqual(by.get("updater"), "disabled")


class StatusLineTests(_Base):
    def test_silent_when_clean(self):
        self.mod._pkg_state_write({})
        code, out, _e = run_cli(["update", "--status-line"], self.kdir)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_surfaces_needs_manual_recovery(self):
        self.mod._pkg_state_write({"needs_manual_recovery": True})
        code, out, _e = run_cli(["update", "--status-line"], self.kdir)
        self.assertIn("STRANDED", out)

    def test_surfaces_pending_apply(self):
        self.mod._pkg_state_write({"pending_apply": {"applying_tag": "v2.0.0"}})
        code, out, _e = run_cli(["update", "--status-line"], self.kdir)
        self.assertIn("unfinalized", out)

    def test_surfaces_tampered_anchor(self):
        self.mod._pkg_secure_home()
        victim = os.path.join(self.home, ".agentware", "v")
        with open(victim, "w") as f:
            f.write("{}")
        os.symlink(victim, self.mod._pkg_state_path())
        code, out, _e = run_cli(["update", "--status-line"], self.kdir)
        self.assertIn("tampered", out)


class OnboardingTests(unittest.TestCase):
    def test_step_7b5_updater_optin_present(self):
        path = os.path.join(REPO_ROOT, ".claude", "skills", "onboarding", "SKILL.md")
        with open(path) as f:
            text = f.read()
        self.assertIn("Step 7b-5", text)
        self.assertIn("config --set-updater on", text)
        self.assertIn("tick --install-schedule", text)
        self.assertIn("config --updater-only", text)
        # Default OPT-IN + OFF, with a team-mode recommend.
        self.assertIn("default OFF", text)
        self.assertIn("recommended: ON for team mode", text)


if __name__ == "__main__":
    unittest.main()
