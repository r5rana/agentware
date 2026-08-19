"""Shared header for the `test_dream` suite, split out by task 58.

Holds every import, helper, constant and base class the split
parts share, so no part duplicates a definition and none can
drift from another.
"""

import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock
from tests._fixtures import load_cli, build_synthetic_kb, run_cli
def _touch(path, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8"):
        pass
    if mtime is not None:
        os.utime(path, (mtime, mtime))
def _steps_mod(mod):
    """The module that OWNS DREAM_STEP_FUNCS / _dream_perf, for monkeypatching.

    Patching the CLI facade stopped working when the step family moved into
    `agentware_lib.features.dreamsteps`: a re-export binds a NEW name, so
    rebinding the facade's attribute leaves the implementation's own binding
    untouched and the override silently does nothing. The package documents
    `<consumer>.dreamsteps.<name>` as the supported target, which is this module.
    Falls back to the facade so the helper still works on a pre-split tree.
    """
    try:
        import agentware_lib.features.dreamsteps as dreamsteps
        return dreamsteps
    except ImportError:            # pragma: no cover - pre-split fallback
        return mod
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
    recursively shell the whole suite), ISOLATE the config (so resolve_dream /
    resolve_dream_schedule / resolve_kb_autocommit never fall through to the
    operator's real ~/.agentware/config.env — R-LOC-03 + determinism), and restore
    everything afterward."""

    _KEYS = ("AGENTWARE_NESTED_UNITTEST", "AGENTWARE_DREAM",
             "AGENTWARE_DREAM_SCHEDULE", "HOME", "AGENTWARE_KB_AUTOCOMMIT",
             "AGENTWARE_KB_MODE", "AGENTWARE_USER_HANDLE")

    # NAMESPACED, and the reason is a bug this mixin actually had. A mixin and
    # the class mixing it in share ONE instance namespace, so a plain
    # `self._saved_cfg` here collided with the identically-named save in
    # `DreamSchedulerTests.setUp`, which layers a SECOND config redirect on top
    # of this one. The subclass's assignment ran last and won, so the LIFO
    # cleanups restored the inner value twice: the outer `_restore_env` wrote
    # back the mixin's own temp path instead of the operator's real one, and
    # then deleted the directory that path names. Every later test in the
    # process inherited a `HOME_CONFIG` pointing at a removed temp dir, and the
    # `_ConfigMirrorModule` write-through (which matches consumers on the
    # PREVIOUS value) desynced permanently from there.

    def _save_env(self):
        self._env = {k: os.environ.get(k) for k in self._KEYS}
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"
        # Redirect the module's config file to a throwaway path so NO real
        # operator setting leaks into a test (CONFIG_PATHS is import-time-bound to
        # the real HOME, so patching os.environ['HOME'] alone does not isolate it).
        self._mod = load_cli()
        self._cfgdir = tempfile.mkdtemp(prefix="agentware-dream-cfg-")
        cfg = os.path.join(self._cfgdir, "config.env")
        self._guardedenv_saved_cfg = (self._mod.HOME_CONFIG,
                                      self._mod.CONFIG_PATHS)
        self._mod.HOME_CONFIG = cfg
        self._mod.CONFIG_PATHS = (cfg,)

    def _satisfy_health_gate(self, kdir):
        """Write minimal state so dream step 2 (health-gate) reports 'ok'.

        The health-gate checks for .initialized, MAIN.md, and
        AGENTWARE_KNOWLEDGE_DIR in config. Without these the step returns
        'warn'/'degraded' → exit 1 even though the steps under test are fine.
        """
        sentinel = os.path.join(kdir, ".initialized")
        if not os.path.exists(sentinel):
            with open(sentinel, "w") as f:
                f.write("2026-01-01T00:00:00Z testuser\nHandle: testuser\n")
        main = os.path.join(kdir, "MAIN.md")
        if not os.path.exists(main):
            with open(main, "w") as f:
                f.write("# Test KB\n")
        with open(self._mod.HOME_CONFIG, "a") as f:
            f.write("AGENTWARE_KNOWLEDGE_DIR=%s\n" % kdir)

    def _restore_env(self):
        self._mod.HOME_CONFIG, self._mod.CONFIG_PATHS = \
            self._guardedenv_saved_cfg
        shutil.rmtree(self._cfgdir, ignore_errors=True)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
if __name__ == "__main__":
    unittest.main()
