"""Tests for the workspace KB-mode + per-user-handle config flags.

Feature 260625-team-mode-onboarding-fork. These flags are DISTINCT from the
pre-existing retrieval `--set-mode` (deterministic|semantic): the new
`--set-kb-mode power|team` / `--kb-mode-only` and `--set-user-handle` /
`--user-handle-only` record the workspace mode and per-user provenance handle.

Hermetic: the CLI persists to ~/.agentware/config.env, so every test redirects
the module's HOME_CONFIG/CONFIG_PATHS to a fresh tempfile and clears the relevant
env vars — the operator's real config is NEVER touched (R-LOC-03).
"""

import contextlib
import io
import os
import tempfile
import unittest

from tests._fixtures import load_cli


@contextlib.contextmanager
def isolated_config(env=None):
    """Run with HOME_CONFIG/CONFIG_PATHS redirected to a temp file + clean env."""
    mod = load_cli()
    tmpd = tempfile.mkdtemp(prefix="agentware-cfgtest-")
    cfg = os.path.join(tmpd, "config.env")
    saved = (mod.HOME_CONFIG, mod.CONFIG_PATHS)
    saved_env = {}
    for k in ("AGENTWARE_KB_MODE", "AGENTWARE_USER_HANDLE",
              "AGENTWARE_RETRIEVAL_MODE", "AGENTWARE_DREAM",
              "AGENTWARE_DREAM_SCHEDULE", "AGENTWARE_CLI"):
        saved_env[k] = os.environ.pop(k, None)
    if env:
        os.environ.update(env)
    mod.HOME_CONFIG = cfg
    mod.CONFIG_PATHS = (cfg,)
    try:
        yield mod, cfg
    finally:
        mod.HOME_CONFIG, mod.CONFIG_PATHS = saved
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if env:
            for k in env:
                if k not in saved_env:
                    os.environ.pop(k, None)
        import shutil
        shutil.rmtree(tmpd, ignore_errors=True)


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


class KbModeTests(unittest.TestCase):
    def test_default_is_power(self):
        with isolated_config() as (mod, _cfg):
            code, out, _ = run(mod, ["config", "--kb-mode-only"])
            self.assertEqual(code, 0)
            self.assertEqual(out.strip(), "power")

    def test_set_team_roundtrips(self):
        with isolated_config() as (mod, cfg):
            code, _, _ = run(mod, ["config", "--set-kb-mode", "team"])
            self.assertEqual(code, 0)
            code, out, _ = run(mod, ["config", "--kb-mode-only"])
            self.assertEqual(out.strip(), "team")
            # Persisted to the config file, not lost.
            self.assertIn("AGENTWARE_KB_MODE=team", open(cfg).read())

    def test_set_power_roundtrips(self):
        with isolated_config() as (mod, _cfg):
            run(mod, ["config", "--set-kb-mode", "team"])
            run(mod, ["config", "--set-kb-mode", "power"])
            code, out, _ = run(mod, ["config", "--kb-mode-only"])
            self.assertEqual(out.strip(), "power")

    def test_bogus_rejected(self):
        with isolated_config() as (mod, _cfg):
            code, _, err = run(mod, ["config", "--set-kb-mode", "bogus"])
            self.assertNotEqual(code, 0)
            self.assertIn("invalid --set-kb-mode", err)

    def test_env_overrides_config(self):
        with isolated_config() as (mod, _cfg):
            run(mod, ["config", "--set-kb-mode", "power"])
        with isolated_config(env={"AGENTWARE_KB_MODE": "team"}) as (mod, _cfg):
            code, out, _ = run(mod, ["config", "--kb-mode-only"])
            self.assertEqual(out.strip(), "team")

    def test_does_not_collide_with_retrieval_set_mode(self):
        # The pre-existing retrieval --set-mode must still work and be independent.
        with isolated_config() as (mod, cfg):
            run(mod, ["config", "--set-mode", "semantic"])
            run(mod, ["config", "--set-kb-mode", "team"])
            txt = open(cfg).read()
            self.assertIn("AGENTWARE_RETRIEVAL_MODE=semantic", txt)
            self.assertIn("AGENTWARE_KB_MODE=team", txt)
            code, out, _ = run(mod, ["config", "--kb-mode-only"])
            self.assertEqual(out.strip(), "team")


class CliTests(unittest.TestCase):
    """Runtime CLI SETTINGS_AW flags (260627-codex-runtime-adapter).

    --set-cli claude|codex / --cli-only record WHICH runtime the loop spawns.
    DISTINCT from every other config axis; default 'claude' (byte-unchanged for
    existing users).
    """

    def test_default_is_claude(self):
        with isolated_config() as (mod, _cfg):
            code, out, _ = run(mod, ["config", "--cli-only"])
            self.assertEqual(code, 0)
            self.assertEqual(out.strip(), "claude")

    def test_set_codex_then_claude_roundtrips(self):
        with isolated_config() as (mod, cfg):
            code, _, _ = run(mod, ["config", "--set-cli", "codex"])
            self.assertEqual(code, 0)
            code, out, _ = run(mod, ["config", "--cli-only"])
            self.assertEqual(out.strip(), "codex")
            self.assertIn("AGENTWARE_CLI=codex", open(cfg).read())
            # flip back
            code, _, _ = run(mod, ["config", "--set-cli", "claude"])
            self.assertEqual(code, 0)
            code, out, _ = run(mod, ["config", "--cli-only"])
            self.assertEqual(out.strip(), "claude")

    def test_bogus_rejected_exits_2(self):
        with isolated_config() as (mod, _cfg):
            code, _, err = run(mod, ["config", "--set-cli", "bogus"])
            self.assertEqual(code, 2)
            self.assertIn("invalid --set-cli", err)

    def test_env_overrides_config(self):
        with isolated_config() as (mod, _cfg):
            run(mod, ["config", "--set-cli", "claude"])
        with isolated_config(env={"AGENTWARE_CLI": "codex"}) as (mod, _cfg):
            code, out, _ = run(mod, ["config", "--cli-only"])
            self.assertEqual(out.strip(), "codex")

    def test_json_surfaces_cli(self):
        import json as _json
        with isolated_config() as (mod, _cfg):
            run(mod, ["config", "--set-cli", "codex"])
            code, out, _ = run(mod, ["config", "--format", "json"])
            obj = _json.loads(out)
            self.assertEqual(obj["cli"], "codex")


class UserHandleTests(unittest.TestCase):
    def test_unset_prints_empty(self):
        with isolated_config() as (mod, _cfg):
            code, out, _ = run(mod, ["config", "--user-handle-only"])
            self.assertEqual(code, 0)
            self.assertEqual(out.strip(), "")

    def test_roundtrip(self):
        with isolated_config() as (mod, _cfg):
            run(mod, ["config", "--set-user-handle", "alice"])
            code, out, _ = run(mod, ["config", "--user-handle-only"])
            self.assertEqual(out.strip(), "alice")

    def test_spaces_quotes_sanitized_no_corruption(self):
        with isolated_config() as (mod, cfg):
            code, _, _ = run(mod, ["config", "--set-user-handle", 'Alice "The" Smith!'])
            self.assertEqual(code, 0)
            # The persisted config must remain a single clean KEY=VALUE line.
            lines = [l for l in open(cfg).read().splitlines()
                     if l.startswith("AGENTWARE_USER_HANDLE=")]
            self.assertEqual(len(lines), 1)
            val = lines[0].split("=", 1)[1]
            self.assertNotIn(" ", val)
            self.assertNotIn('"', val)
            # Reads back as a safe token.
            code, out, _ = run(mod, ["config", "--user-handle-only"])
            self.assertTrue(out.strip())
            self.assertNotIn(" ", out)

    def test_all_invalid_handle_rejected(self):
        with isolated_config() as (mod, _cfg):
            code, _, err = run(mod, ["config", "--set-user-handle", "!!! @@@"])
            self.assertNotEqual(code, 0)
            self.assertIn("invalid --set-user-handle", err)


class DreamModeTests(unittest.TestCase):
    """Dream-mode opt-in + schedule SETTINGS_AW flags (260627-dream-mode)."""

    def test_default_is_off(self):
        with isolated_config() as (mod, _cfg):
            code, out, _ = run(mod, ["config", "--dream-only"])
            self.assertEqual(code, 0)
            self.assertEqual(out.strip(), "off")

    def test_set_on_roundtrips(self):
        with isolated_config() as (mod, cfg):
            code, _, _ = run(mod, ["config", "--set-dream", "on"])
            self.assertEqual(code, 0)
            code, out, _ = run(mod, ["config", "--dream-only"])
            self.assertEqual(out.strip(), "on")
            self.assertIn("AGENTWARE_DREAM=1", open(cfg).read())

    def test_set_off_roundtrips(self):
        with isolated_config() as (mod, _cfg):
            run(mod, ["config", "--set-dream", "on"])
            run(mod, ["config", "--set-dream", "off"])
            code, out, _ = run(mod, ["config", "--dream-only"])
            self.assertEqual(out.strip(), "off")

    def test_bogus_dream_rejected(self):
        with isolated_config() as (mod, _cfg):
            code, _, err = run(mod, ["config", "--set-dream", "maybe"])
            self.assertNotEqual(code, 0)
            self.assertIn("invalid --set-dream", err)

    def test_env_overrides_config(self):
        with isolated_config() as (mod, _cfg):
            run(mod, ["config", "--set-dream", "off"])
        with isolated_config(env={"AGENTWARE_DREAM": "on"}) as (mod, _cfg):
            code, out, _ = run(mod, ["config", "--dream-only"])
            self.assertEqual(out.strip(), "on")

    def test_schedule_unset_prints_empty(self):
        with isolated_config() as (mod, _cfg):
            code, out, _ = run(mod, ["config", "--dream-schedule-only"])
            self.assertEqual(code, 0)
            self.assertEqual(out.strip(), "")

    def test_schedule_hhmm_roundtrips_normalized(self):
        with isolated_config() as (mod, cfg):
            code, _, _ = run(mod, ["config", "--set-dream-schedule", "3:07"])
            self.assertEqual(code, 0)
            code, out, _ = run(mod, ["config", "--dream-schedule-only"])
            self.assertEqual(out.strip(), "03:07")
            self.assertIn("AGENTWARE_DREAM_SCHEDULE=03:07", open(cfg).read())

    def test_schedule_cron_roundtrips(self):
        with isolated_config() as (mod, _cfg):
            code, _, _ = run(mod, ["config", "--set-dream-schedule", "0 3 * * *"])
            self.assertEqual(code, 0)
            code, out, _ = run(mod, ["config", "--dream-schedule-only"])
            self.assertEqual(out.strip(), "0 3 * * *")

    def test_schedule_bogus_rejected(self):
        with isolated_config() as (mod, _cfg):
            for bad in ("25:00", "3pm", "0 3 * *", "nonsense"):
                code, _, err = run(mod, ["config", "--set-dream-schedule", bad])
                self.assertNotEqual(code, 0, "should reject %r" % bad)
                self.assertIn("invalid --set-dream-schedule", err)

    def test_json_surfaces_both_keys(self):
        import json as _json
        with isolated_config() as (mod, _cfg):
            run(mod, ["config", "--set-dream", "on"])
            run(mod, ["config", "--set-dream-schedule", "02:30"])
            code, out, _ = run(mod, ["config", "--format", "json"])
            obj = _json.loads(out)
            self.assertEqual(obj["dream"], "1")
            self.assertEqual(obj["dream_schedule"], "02:30")


if __name__ == "__main__":
    unittest.main()
