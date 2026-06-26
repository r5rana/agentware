"""Hermetic fresh-clone smoke test — proves the clone-and-go promise mechanically.

agentware's headline claim is *clone-and-go*: a brand-new operator runs
`git clone … && ./agentware.sh <feature>`, onboards, and gets a working empty KB
with passing health checks and ZERO personal data shipped. After many package
changes (autocommit onboarding, the personal_data audit guard, kb-git sync, the
benchmark gate, recall/eval) nothing *mechanically* proved that path still works
from scratch. This module simulates a fresh clone end-to-end in a fully isolated
temp environment and asserts the empty-KB world is healthy.

WHY SUBPROCESS + PATCHED HOME IS MANDATORY HERE (the load-bearing design idea)
-----------------------------------------------------------------------------
`agentware init` → `_write_config(kdir)` → `_set_config_value(CONFIG_KEY, kdir)`
writes `HOME_CONFIG = ~/.agentware/config.env`, and `HOME_CONFIG` is computed at
MODULE IMPORT TIME (`scripts/agentware:44`). So an *in-process* `init` (the
`tests/_fixtures.run_cli` style that imports the module once) CANNOT redirect
where config is written — `HOME_CONFIG` was already bound to the operator's real
HOME at import — and it WILL clobber the operator's real KB pointer (the learning
`agentware-init-clobbers-operator-config`; `tests/_fixtures.py:9-11` warns tests
must never call `init`). THEREFORE this smoke test runs `init` (and every
post-init CLI touch: config/recall/index validate) in a SUBPROCESS with a patched
`HOME` (and a temp `AGENTWARE_KNOWLEDGE_DIR`). A fresh subprocess re-imports the
module and re-binds `HOME_CONFIG` to the temp HOME, so the write lands in the
temp dir and the operator's real config is never touched. `tearDown` asserts the
real `~/.agentware/config.env` is byte-for-byte unchanged.

Read-only in-process calls (`_audit_gate_check`, `_audit_personal_data_check`)
are the SANCTIONED exception: they never write config or index.json, so we invoke
them directly via the imported module for speed.

Scope note: this does NOT simulate the interactive onboarding interview (that
needs an LLM). It exercises the DETERMINISTIC CLI seams a fresh clone relies on —
`init`, `config`, `index validate`, `recall`, and the audit checks — not the chat.

Stdlib-only (unittest + tempfile + subprocess + json + os + shutil), mirroring
`tests/_fixtures.py`. No pytest, no third-party deps, no network, no LLM.
Deterministic: fixed inputs → same result; no wall-clock/RNG assertions.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# Repo root = parent of tests/. The CLI is a no-extension python script. Mirror
# the resolution in tests/_fixtures.py rather than importing it, so this module
# stays self-contained for the subprocess seam it exercises.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI_PATH = os.path.join(REPO_ROOT, "scripts", "agentware")

# Live seams asserted below (re-verified against scripts/agentware on 2026-06-26):
#   KNOWLEDGE_SUBDIRS (:137), NON_INDEXED_DIRS (:144), KB_GITIGNORE_REQUIRED (:116).
KNOWLEDGE_SUBDIRS = ("learnings", "projects", "configurations", "prompts",
                     "references", "skills")
NON_INDEXED_DIRS = ("work", "logs", os.path.join("logs", "sessions"))
GITIGNORE_REQUIRED = ("logs", ".loop")  # subset the plan pins; .cache also added live


def _load_cli():
    """Import scripts/agentware as a module (stdlib importlib only).

    Used ONLY for read-only, config-safe helpers (`_audit_gate_check`,
    `_audit_personal_data_check`, `HOME_CONFIG`/`resolve_knowledge_dir`). NEVER
    used to run `init` or any config mutation — see the module docstring.
    """
    import importlib.util
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("agentware_cli_smoke", CLI_PATH)
    spec = importlib.util.spec_from_loader("agentware_cli_smoke", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _hermetic_env(tmp_home, tmp_kb):
    """Build a subprocess env with a patched HOME + temp AGENTWARE_KNOWLEDGE_DIR.

    Strips any inherited `AGENTWARE_KB_AUTOCOMMIT`/`AGENTWARE_RETRIEVAL_MODE` so
    the operator's shell settings cannot mask what the temp `config.env` persists
    (`resolve_kb_autocommit` reads the env var BEFORE config.env). This keeps the
    autocommit-persistence assertion deterministic.
    """
    env = dict(os.environ)
    env["HOME"] = tmp_home
    env["AGENTWARE_KNOWLEDGE_DIR"] = tmp_kb
    env.pop("AGENTWARE_KB_AUTOCOMMIT", None)
    env.pop("AGENTWARE_RETRIEVAL_MODE", None)
    return env


class TestFreshClone(unittest.TestCase):
    """Stands up a hermetic fresh clone (temp HOME + temp KB) and verifies it."""

    def setUp(self):
        # Isolated temp HOME and temp knowledge dir, both auto-cleaned.
        self.tmp_home = tempfile.mkdtemp(prefix="agentware-smoke-home-")
        self.tmp_kb = tempfile.mkdtemp(prefix="agentware-smoke-kb-")
        self.addCleanup(shutil.rmtree, self.tmp_home, True)
        self.addCleanup(shutil.rmtree, self.tmp_kb, True)

        # Snapshot the REAL operator config for the teardown hermeticity guard.
        self.mod = _load_cli()
        self.real_home_config = self.mod.HOME_CONFIG
        self.real_config_bytes = None
        if os.path.isfile(self.real_home_config):
            with open(self.real_home_config, "rb") as f:
                self.real_config_bytes = f.read()
        # Snapshot the real resolved KB pointer (read-only; this process never
        # sets AGENTWARE_KNOWLEDGE_DIR in its own os.environ).
        self.real_kb_pointer = self.mod.resolve_knowledge_dir()

    def tearDown(self):
        # INV-2 HERMETICITY: the operator's real config must be byte-identical
        # (or still absent) and still resolve to the same real KB. This runs for
        # EVERY test method, so any leak in any step is caught immediately.
        if self.real_config_bytes is None:
            self.assertFalse(
                os.path.isfile(self.real_home_config),
                "smoke test CREATED the operator's real config.env — HOME leak!")
        else:
            self.assertTrue(os.path.isfile(self.real_home_config),
                            "smoke test DELETED the operator's real config.env!")
            with open(self.real_home_config, "rb") as f:
                self.assertEqual(
                    f.read(), self.real_config_bytes,
                    "smoke test MUTATED the operator's real config.env — "
                    "HOME_CONFIG leak (subprocess HOME patch failed?)")
        self.assertEqual(
            self.mod.resolve_knowledge_dir(), self.real_kb_pointer,
            "smoke test repointed the operator's real KB resolution")

    # --- subprocess CLI helpers (the only sanctioned way to drive init/config) -

    def _run_init_subprocess(self):
        """Run `init` in a SUBPROCESS with patched HOME → (code, parsed_json).

        A fresh subprocess re-imports scripts/agentware and re-binds HOME_CONFIG
        to tmp_home, so the config write lands in the temp dir, never the real
        ~/.agentware/config.env. THIS is the load-bearing pattern of the test.
        """
        return self._run_cli_subprocess(
            ["init", "--knowledge-dir", self.tmp_kb, "--format", "json"])

    def _run_cli_subprocess(self, argv):
        """Run any CLI subcommand under the hermetic env → (code, stdout, stderr).

        EVERY init/config/recall/validate touch goes through here (never the
        in-process `mod.main`), so HOME_CONFIG binds to the temp HOME.
        """
        proc = subprocess.run(
            [sys.executable, CLI_PATH] + argv,
            cwd=REPO_ROOT, env=_hermetic_env(self.tmp_home, self.tmp_kb),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
        return proc.returncode, proc.stdout, proc.stderr

    def _init_and_parse(self):
        code, out, err = self._run_cli_subprocess(
            ["init", "--knowledge-dir", self.tmp_kb, "--format", "json"])
        self.assertEqual(code, 0, "init failed: %s\n%s" % (out, err))
        return json.loads(out)

    # --- Task 3: scaffold ----------------------------------------------------

    def test_scaffold(self):
        """init scaffolds the full empty-KB tree, seeded index, templates, gitignore."""
        payload = self._init_and_parse()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["seeded_index"])
        # The KB tree exists: every indexed category + non-indexed work/logs dirs.
        for sub in KNOWLEDGE_SUBDIRS + NON_INDEXED_DIRS:
            self.assertTrue(os.path.isdir(os.path.join(self.tmp_kb, sub)),
                            "missing scaffolded dir: %s" % sub)

        # A valid seeded index.json: entries == [] and tags == {}.
        with open(os.path.join(self.tmp_kb, "index.json"), encoding="utf-8") as f:
            idx = json.load(f)
        self.assertEqual(idx.get("entries"), [])
        self.assertEqual(idx.get("tags"), {})

        # Templates installed into the operator's dir.
        self.assertTrue(os.path.isdir(os.path.join(self.tmp_kb, "templates")))
        self.assertTrue(
            any(fn.endswith(".md")
                for fn in os.listdir(os.path.join(self.tmp_kb, "templates"))),
            "no *.md templates installed")

        # KB .gitignore ignores logs/ and .loop/.
        with open(os.path.join(self.tmp_kb, ".gitignore"), encoding="utf-8") as f:
            present = set(ln.strip().rstrip("/") for ln in f.read().splitlines())
        for rule in GITIGNORE_REQUIRED:
            self.assertIn(rule, present,
                          ".gitignore missing required rule: %s/" % rule)

        # init must NOT write the .initialized sentinel (onboarding does that).
        self.assertFalse(
            os.path.isfile(os.path.join(self.tmp_kb, ".initialized")),
            "init unexpectedly wrote the .initialized sentinel")

    # --- Task 4: index validate ----------------------------------------------

    def test_index_validate(self):
        """index validate passes against the freshly seeded empty index."""
        self._init_and_parse()
        code, out, err = self._run_cli_subprocess(
            ["index", "validate", "--format", "json"])
        self.assertEqual(code, 0, "index validate failed: %s\n%s" % (out, err))
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["errors"], [])

    # --- Task 5: empty-KB recall ---------------------------------------------

    def test_recall_empty_kb_returns_count_zero(self):
        """Bare recall on an empty corpus returns count==0, not a crash."""
        self._init_and_parse()
        code, out, err = self._run_cli_subprocess(
            ["recall", "anything", "--format", "json"])
        self.assertEqual(code, 0, "recall crashed on empty KB: %s\n%s" % (out, err))
        payload = json.loads(out)
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["results"], [])

    # --- Task 6: gate graceful-skip (no gold set) ----------------------------

    def test_gate_graceful_skip_no_gold_set(self):
        """With no benchmarks/recall-gold.json, the benchmark gate soft-passes.

        Calls `_audit_gate_check` in-process (read-only: it returns the soft pass
        BEFORE any scorecard write when gold is absent), and asserts the EXACT
        skip-detail string emitted at scripts/agentware:~8201.
        """
        self._init_and_parse()
        gold = os.path.join(self.tmp_kb, "benchmarks", "recall-gold.json")
        self.assertFalse(os.path.isfile(gold), "fresh clone should have no gold set")
        check = self.mod._audit_gate_check(self.tmp_kb)
        self.assertEqual(check["name"], "benchmark_gate")
        self.assertTrue(check["ok"], "gate should soft-pass with no gold set")
        joined = " ".join(check["details"])
        self.assertIn("no gold set", joined)
        self.assertIn("skipped", joined)
        # The gate must NOT have created a gold set or scorecard as a side effect.
        self.assertFalse(os.path.isfile(gold))

    # --- Task 7: autocommit persists (Step 7b) -------------------------------

    def test_autocommit_choice_persists(self):
        """The Step-7b autocommit choice persists to the TEMP config.env."""
        self._init_and_parse()
        # off -> 0
        code, out, err = self._run_cli_subprocess(
            ["config", "--set-autocommit", "off"])
        self.assertEqual(code, 0, err)
        code, out, _ = self._run_cli_subprocess(["config", "--kb-autocommit-only"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "0")
        # on -> 1
        code, out, err = self._run_cli_subprocess(
            ["config", "--set-autocommit", "on"])
        self.assertEqual(code, 0, err)
        code, out, _ = self._run_cli_subprocess(["config", "--kb-autocommit-only"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "1")

        # The value landed in the TEMP home config, never the operator's.
        temp_config = os.path.join(self.tmp_home, ".agentware", "config.env")
        self.assertTrue(os.path.isfile(temp_config),
                        "autocommit setting did not land in the temp config.env")
        with open(temp_config, encoding="utf-8") as f:
            self.assertIn("AGENTWARE_KB_AUTOCOMMIT=1", f.read())

    # --- Task 8: personal_data clean -----------------------------------------

    def test_personal_data_clean(self):
        """A fresh clone ships ZERO operator personal data (R-LOC-03).

        Resolves the operator identity at runtime (hardcodes nothing) exactly like
        the audit check, then asserts the package tree is clean.
        """
        self._init_and_parse()
        check = self.mod._audit_personal_data_check(self.tmp_kb)
        self.assertEqual(check["name"], "personal_data")
        self.assertTrue(
            check["ok"],
            "package tree leaks operator personal data: %s" % check["details"])

    # --- Task 9: hermeticity guard (dedicated, beyond the per-test tearDown) --

    def test_hermeticity_real_config_untouched(self):
        """A full init + config-mutation cycle leaves the real config untouched.

        The byte-for-byte assertion runs again in tearDown for EVERY test; this
        dedicated method makes the guarantee explicit after the most invasive
        sequence (init + autocommit writes), and confirms the resolver still
        points at the operator's real KB mid-run.
        """
        self._init_and_parse()
        self._run_cli_subprocess(["config", "--set-autocommit", "off"])
        self._run_cli_subprocess(["config", "--set-autocommit", "on"])
        # Real config bytes unchanged right now (tearDown re-checks post-cleanup).
        if self.real_config_bytes is None:
            self.assertFalse(os.path.isfile(self.real_home_config))
        else:
            with open(self.real_home_config, "rb") as f:
                self.assertEqual(f.read(), self.real_config_bytes)
        self.assertEqual(self.mod.resolve_knowledge_dir(), self.real_kb_pointer)


if __name__ == "__main__":
    unittest.main()
