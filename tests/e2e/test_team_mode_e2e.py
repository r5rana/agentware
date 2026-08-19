"""End-to-end hermetic round-trip for team-mode onboarding.

Feature 260625-team-mode-onboarding-fork, Task 10. Exercises the whole feature
through the real CLI in one flow, fully isolated (temp HOME for config + temp KB),
never touching the operator's real config (R-LOC-03):

  1. `config --set-kb-mode team` persists and reads back via `--kb-mode-only`.
  2. `config --set-user-handle alice` round-trips via `--user-handle-only`.
  3. `learn --author alice --source user` writes alice's provenance frontmatter.
  4. `attach` accepts a conformant temp repo and refuses a broken one.

Stdlib unittest only; deterministic.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from tests._fixtures import load_cli, run_cli


@contextlib.contextmanager
def isolated_home():
    """Redirect the module's HOME_CONFIG/CONFIG_PATHS to a throwaway file + clear
    the relevant env vars so the operator's real ~/.agentware is never touched."""
    mod = load_cli()
    tmpd = tempfile.mkdtemp(prefix="agentware-e2e-home-")
    cfg = os.path.join(tmpd, "config.env")
    saved = (mod.HOME_CONFIG, mod.CONFIG_PATHS)
    saved_env = {k: os.environ.pop(k, None)
                 for k in ("AGENTWARE_KB_MODE", "AGENTWARE_USER_HANDLE")}
    mod.HOME_CONFIG, mod.CONFIG_PATHS = cfg, (cfg,)
    try:
        yield mod
    finally:
        mod.HOME_CONFIG, mod.CONFIG_PATHS = saved
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v
        shutil.rmtree(tmpd, ignore_errors=True)


def _cap(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def _conformant_kb(mod):
    kdir = tempfile.mkdtemp(prefix="agentware-e2e-kb-")
    for sub in mod.ATTACH_REQUIRED_DIRS:
        os.makedirs(os.path.join(kdir, sub), exist_ok=True)
    mod.save_index(kdir, {"entries": [], "tags": {}})
    return kdir


class TeamModeE2E(unittest.TestCase):
    def test_full_team_mode_round_trip(self):
        with isolated_home() as mod:
            # 1. mode persists + reads back
            code, _o, err = _cap(mod, ["config", "--set-kb-mode", "team"])
            self.assertEqual(code, 0, err)
            code, out, _ = _cap(mod, ["config", "--kb-mode-only"])
            self.assertEqual(out.strip(), "team")

            # 2. handle round-trips
            _cap(mod, ["config", "--set-user-handle", "alice"])
            code, out, _ = _cap(mod, ["config", "--user-handle-only"])
            self.assertEqual(out.strip(), "alice")

            # 3. a learn entry carries alice's provenance. Use a fresh KB pinned
            #    via AGENTWARE_KNOWLEDGE_DIR (run_cli sets it).
            kdir = _conformant_kb(mod)
            self.addCleanup(shutil.rmtree, kdir, True)
            code, _o, err = run_cli(
                ["learn", "--topic", "team-note", "--summary", "S",
                 "--tags", "x,y", "--content", "Shared body.",
                 "--author", "alice", "--source", "user"], kdir)
            self.assertEqual(code, 0, err)
            with open(os.path.join(kdir, "learnings", "team-note.md")) as f:
                fm, _b = mod.split_frontmatter(f.read())
            self.assertEqual(fm["author"], "alice")
            self.assertEqual(fm["source"], "user")
            # provenance feeds the ACR prior: user outranks agent/imported.
            self.assertGreater(mod.source_weight("user"), mod.source_weight("agent"))

            # 4. attach accepts the conformant KB, refuses a broken one.
            code, _o, _ = run_cli(["attach", "--path", kdir], kdir)
            self.assertEqual(code, 0)
            broken = _conformant_kb(mod)
            self.addCleanup(shutil.rmtree, broken, True)
            shutil.rmtree(os.path.join(broken, "learnings"))
            code, out, _ = run_cli(
                ["attach", "--path", broken, "--format", "json"], broken)
            self.assertEqual(code, 1)
            self.assertIn("learnings", json.loads(out)["missing_dirs"])

    def test_personal_data_guard_holds_no_handle_in_package(self):
        # The per-user handle must live ONLY in config/KB frontmatter, never the
        # package tree. Assert a freshly-set handle does not leak into REPO_ROOT.
        with isolated_home() as mod:
            # Assemble the sentinel at runtime so the literal handle never
            # appears verbatim in THIS source file — otherwise the package scan
            # would flag its own fixture line as a false leak.
            handle = "zz" + "topsecret" + "handle"
            _cap(mod, ["config", "--set-user-handle", handle])
            hits = mod.scan_personal_data(mod.REPO_ROOT, [handle])
            self.assertEqual(hits, [], "per-user handle leaked into the package")


if __name__ == "__main__":
    unittest.main()
