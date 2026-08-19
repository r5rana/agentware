"""RESOLVER PARITY — the bash twin `scripts/aw-knowledge-dir` vs the Python authority.

Feature 260813-modular-core-adoption, task 36.

Runner:  python3 -m unittest tests.integration.test_resolver_parity -v
   or:   python3 -m unittest discover -s tests -v

Why this gate exists
--------------------
Two engines answer one question — "where is the knowledge base?". The Python one
(``agentware_lib.core.config.resolve_knowledge_dir``, composing
``…config.orgresolve.resolve_org_team_dir`` and ``…config.configkeys``) answers it
for the CLI and therefore for the loop. The bash one answers it for every agent
spawn: ``scripts/hooks/common.sh:aw_knowledge_dir`` delegates to the script by
absolute path, and the six hooks that source common.sh — plus the spawned
``scripts/hooks/codex-stream.py`` — reach their knowledge dir through it.

When the two disagree the loop writes to one knowledge base and the hooks write
to another. NOTHING TURNS RED: hooks are best-effort by contract (``|| true``,
exit 0 unconditionally), so a hook resolving the wrong dir logs happily into a
directory the CLI never reads. That is the same fail-open shape as
``learn-capability-probe-without-a-fallback-rung-fails-open`` — the divergence has
to be found by reading the two implementations side by side, which is exactly
what this file replaces.

What is asserted, and against WHICH Python
------------------------------------------
The CONTRACT arm drives the bash script against the LIB resolver over a matrix of
configurations covering all four precedence tiers plus the parsing edges (blank
values, quoting, whitespace, normalisation, the org composition). The lib is the
authority because it is where the merged tree is going.

The SHIPPED arm additionally drives the bash script against the real CLI
(``scripts/agentware config --knowledge-dir-only``). Under legacy-default routing
(task 29, ``PORTED_VERBS`` empty) ``config`` is still served by the preserved
monolith, whose resolver pre-dates BOTH the org tier and ``AGENTWARE_CONFIG_HOME``.
So the shipped arm covers the tiers the monolith has — the direct pointer from the
environment and from ``$HOME/.agentware/config.env`` — which is every tier a flat
single-operator install can reach, and therefore everything production resolves
today. It is deliberately NOT an assertion that the monolith diverges: when
``config`` is promoted (task 54) the CLI becomes the lib and this arm silently
gets stronger, rather than flipping from green to red.

Hermeticity
-----------
The developer's real ``~/.agentware/config.env`` would win tier 2 in almost every
case, so both engines run against a FAKE REPO: a temp dir carrying the ``AGENTS.md``
repo-root marker, a copy of the bash script under ``scripts/``, and a symlink to the
real ``agentware_lib``. The bash script derives its repo root from its own location
and the Python one walks up from ``paths.py`` — under this layout both land on the
temp dir, so tier 3 (the repo-local config) is reachable without ever writing into
the checkout. Every subprocess gets a scrubbed environment (no ``AGENTWARE_*``
inherited) and an explicit cwd, because ``abspath`` is relative to it.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import REPO_ROOT
except ImportError:
    from _fixtures import REPO_ROOT

BASH_RESOLVER = os.path.join(REPO_ROOT, "scripts", "aw-knowledge-dir")
CLI = os.path.join(REPO_ROOT, "scripts", "agentware")
LIB_DIR = os.path.join(REPO_ROOT, "scripts", "agentware_lib")

# Drives the LIB resolver and prints exactly what it returned ('' for None), with
# no trailing newline, so the comparison is byte-for-byte against bash's stdout.
_LIB_DRIVER = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import agentware_lib.core.config as c\n"
    "v = c.resolve_knowledge_dir()\n"
    "sys.stdout.write(v if v else '')\n"
)


def _clean_env(**overrides):
    """A scrubbed environment: no inherited AGENTWARE_* key can leak a tier in."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTWARE_")}
    env.pop("HOME", None)
    # PWD/OLDPWD are the runner's, not the subprocess's. Leaving them in means
    # bash may keep a LOGICAL cwd the test never chose, which is the one input
    # `abspath` reads for a relative value.
    env.pop("PWD", None)
    env.pop("OLDPWD", None)
    for key, val in overrides.items():
        if val is None:
            env.pop(key, None)
        else:
            env[key] = val
    return env


class ResolverCase(unittest.TestCase):
    """A fake repo both engines resolve their repo root to, plus the two drivers."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw_resolver_parity_")
        self.repo = os.path.join(self.tmp, "fake-repo")
        self.home = os.path.join(self.tmp, "home")
        self.cwd = os.path.join(self.tmp, "cwd")
        os.makedirs(os.path.join(self.repo, "scripts"))
        os.makedirs(self.home)
        os.makedirs(self.cwd)
        # The repo-root marker agentware_lib.core.paths walks up to find.
        with open(os.path.join(self.repo, "AGENTS.md"), "w") as fh:
            fh.write("# marker\n")
        self.bash = os.path.join(self.repo, "scripts", "aw-knowledge-dir")
        shutil.copy2(BASH_RESOLVER, self.bash)
        os.symlink(LIB_DIR, os.path.join(self.repo, "scripts", "agentware_lib"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- config.env authoring -------------------------------------------------
    def write_home_config(self, text, config_home=None):
        """Write <config-home>/config.env (default $HOME/.agentware)."""
        base = config_home or os.path.join(self.home, ".agentware")
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "config.env")
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def write_repo_config(self, text):
        base = os.path.join(self.repo, ".agentware")
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "config.env")
        with open(path, "w") as fh:
            fh.write(text)
        return path

    # --- the two engines ------------------------------------------------------
    def run_bash(self, env, cwd=None):
        """(stdout, returncode) from the bash twin."""
        proc = subprocess.run([self.bash], capture_output=True, text=True,
                              env=env, cwd=cwd or self.cwd)
        return proc.stdout.strip(), proc.returncode

    def run_lib(self, env, cwd=None):
        """What the Python authority resolves, as a string ('' for None)."""
        proc = subprocess.run(
            ["python3", "-c", _LIB_DRIVER, os.path.join(self.repo, "scripts")],
            capture_output=True, text=True, env=env, cwd=cwd or self.cwd)
        self.assertEqual(proc.returncode, 0,
                         "lib driver crashed: %s" % proc.stderr)
        return proc.stdout.strip()

    def assert_parity(self, env, expected, cwd=None, msg=""):
        """Both engines resolve `expected` ('' meaning unconfigured)."""
        bash_out, bash_rc = self.run_bash(env, cwd)
        lib_out = self.run_lib(env, cwd)
        label = msg or "resolver parity"
        self.assertEqual(bash_out, lib_out,
                         "%s: bash=%r lib=%r" % (label, bash_out, lib_out))
        self.assertEqual(lib_out, expected, "%s: expected %r" % (label, expected))
        # Unconfigured MUST be rc 1 with empty stdout — hooks branch on the rc.
        self.assertEqual(bash_rc, 0 if expected else 1,
                         "%s: bash rc %d for %r" % (label, bash_rc, expected))


class TestDirectPointerTiers(ResolverCase):
    """Tiers 1-3: AGENTWARE_KNOWLEDGE_DIR from env, HOME config.env, repo config.env."""

    def test_env_pointer_wins(self):
        kb = os.path.join(self.tmp, "kb-env")
        self.write_home_config("AGENTWARE_KNOWLEDGE_DIR=%s\n"
                               % os.path.join(self.tmp, "kb-home"))
        self.assert_parity(_clean_env(HOME=self.home, AGENTWARE_KNOWLEDGE_DIR=kb),
                           kb, msg="env pointer beats config.env")

    def test_home_config_pointer(self):
        kb = os.path.join(self.tmp, "kb-home")
        self.write_home_config("AGENTWARE_KNOWLEDGE_DIR=%s\n" % kb)
        self.assert_parity(_clean_env(HOME=self.home), kb, msg="tier 2")

    def test_repo_config_pointer_when_home_has_none(self):
        kb = os.path.join(self.tmp, "kb-repo")
        self.write_home_config("# nothing here\n")
        self.write_repo_config("AGENTWARE_KNOWLEDGE_DIR=%s\n" % kb)
        self.assert_parity(_clean_env(HOME=self.home), kb, msg="tier 3")

    def test_home_config_beats_repo_config(self):
        home_kb = os.path.join(self.tmp, "kb-home")
        self.write_home_config("AGENTWARE_KNOWLEDGE_DIR=%s\n" % home_kb)
        self.write_repo_config("AGENTWARE_KNOWLEDGE_DIR=%s\n"
                               % os.path.join(self.tmp, "kb-repo"))
        self.assert_parity(_clean_env(HOME=self.home), home_kb,
                           msg="HOME config precedence")

    def test_nothing_configured_is_empty_and_rc1(self):
        self.assert_parity(_clean_env(HOME=self.home), "", msg="unconfigured")


class TestValueParsingParity(ResolverCase):
    """The parsing edges — where a hand-written bash twin drifts from Python."""

    def test_blank_env_pointer_falls_through(self):
        kb = os.path.join(self.tmp, "kb-home")
        self.write_home_config("AGENTWARE_KNOWLEDGE_DIR=%s\n" % kb)
        self.assert_parity(
            _clean_env(HOME=self.home, AGENTWARE_KNOWLEDGE_DIR="   "), kb,
            msg="whitespace-only env value is UNSET on both sides")

    def test_trailing_slash_and_dotdot_are_normalised(self):
        kb = os.path.join(self.tmp, "kb-norm")
        raw = os.path.join(self.tmp, "kb-norm", "sub", "..") + "/"
        self.assert_parity(_clean_env(HOME=self.home, AGENTWARE_KNOWLEDGE_DIR=raw),
                           kb, msg="abspath normalisation")

    def test_relative_pointer_resolves_against_cwd(self):
        # realpath: abspath joins against the PHYSICAL cwd, and on macOS the temp
        # root is reached through a /var -> /private/var symlink.
        self.assert_parity(
            _clean_env(HOME=self.home, AGENTWARE_KNOWLEDGE_DIR="kb-rel"),
            os.path.join(os.path.realpath(self.cwd), "kb-rel"),
            msg="relative pointer")

    def test_relative_pointer_uses_the_physical_cwd(self):
        """`pwd -P`, not `$PWD`: a symlinked cwd must not fork the two engines.

        Bash keeps the LOGICAL path it was handed; python's os.getcwd() is always
        physical. A hook spawned through a symlinked checkout would otherwise
        resolve the same config file to two different knowledge dirs.
        """
        link = os.path.join(self.tmp, "cwd-link")
        os.symlink(self.cwd, link)
        env = _clean_env(HOME=self.home, AGENTWARE_KNOWLEDGE_DIR="kb-rel")
        env["PWD"] = link  # what a shell that cd'd through the symlink would export
        self.assert_parity(env, os.path.join(os.path.realpath(self.cwd), "kb-rel"),
                           cwd=link, msg="physical cwd")

    def test_tilde_expands_to_home(self):
        self.assert_parity(
            _clean_env(HOME=self.home, AGENTWARE_KNOWLEDGE_DIR="~/kb-tilde"),
            os.path.join(self.home, "kb-tilde"), msg="tilde expansion")

    def test_quoted_and_double_quoted_config_values(self):
        kb = os.path.join(self.tmp, "kb-quoted")
        self.write_home_config('AGENTWARE_KNOWLEDGE_DIR=""%s""\n' % kb)
        self.assert_parity(_clean_env(HOME=self.home), kb,
                           msg="repeated quote stripping")

    def test_single_quoted_config_value(self):
        kb = os.path.join(self.tmp, "kb-sq")
        self.write_home_config("AGENTWARE_KNOWLEDGE_DIR='%s'\n" % kb)
        self.assert_parity(_clean_env(HOME=self.home), kb, msg="single quotes")

    def test_whitespace_around_key_and_value(self):
        kb = os.path.join(self.tmp, "kb-ws")
        self.write_home_config("   AGENTWARE_KNOWLEDGE_DIR  =  %s  \n" % kb)
        self.assert_parity(_clean_env(HOME=self.home), kb,
                           msg="key/value whitespace trimmed")

    def test_commented_line_is_ignored(self):
        kb = os.path.join(self.tmp, "kb-live")
        self.write_home_config(
            "# AGENTWARE_KNOWLEDGE_DIR=%s\nAGENTWARE_KNOWLEDGE_DIR=%s\n"
            % (os.path.join(self.tmp, "kb-dead"), kb))
        self.assert_parity(_clean_env(HOME=self.home), kb, msg="comment skipped")

    def test_blank_pointer_line_does_not_stop_the_scan(self):
        """`if val:` in resolve_knowledge_dir CONTINUES past a blank value."""
        kb = os.path.join(self.tmp, "kb-second")
        self.write_home_config("AGENTWARE_KNOWLEDGE_DIR=\n"
                               "AGENTWARE_KNOWLEDGE_DIR=%s\n" % kb)
        self.assert_parity(_clean_env(HOME=self.home), kb,
                           msg="blank pointer line skipped")

    def test_blank_pointer_line_falls_through_to_repo_config(self):
        kb = os.path.join(self.tmp, "kb-repo")
        self.write_home_config("AGENTWARE_KNOWLEDGE_DIR=\n")
        self.write_repo_config("AGENTWARE_KNOWLEDGE_DIR=%s\n" % kb)
        self.assert_parity(_clean_env(HOME=self.home), kb,
                           msg="blank HOME pointer yields to REPO")


class TestConfigHomeParity(ResolverCase):
    """AGENTWARE_CONFIG_HOME relocates the config DIRECTORY for both engines."""

    def test_config_home_redirects_both_engines(self):
        cfg_home = os.path.join(self.tmp, "cfg-home")
        kb = os.path.join(self.tmp, "kb-cfghome")
        self.write_home_config("AGENTWARE_KNOWLEDGE_DIR=%s\n" % kb,
                               config_home=cfg_home)
        # A DIFFERENT pointer at the real $HOME must lose.
        self.write_home_config("AGENTWARE_KNOWLEDGE_DIR=%s\n"
                               % os.path.join(self.tmp, "kb-realhome"))
        self.assert_parity(
            _clean_env(HOME=self.home, AGENTWARE_CONFIG_HOME=cfg_home), kb,
            msg="AGENTWARE_CONFIG_HOME redirect")

    def test_blank_config_home_falls_back_to_home(self):
        """configkeys uses `.strip() or ~/.agentware`, which `${VAR:-d}` would not."""
        kb = os.path.join(self.tmp, "kb-home")
        self.write_home_config("AGENTWARE_KNOWLEDGE_DIR=%s\n" % kb)
        self.assert_parity(
            _clean_env(HOME=self.home, AGENTWARE_CONFIG_HOME="  "), kb,
            msg="blank AGENTWARE_CONFIG_HOME is UNSET")


class TestOrgCompositionParity(ResolverCase):
    """Tier 4 — <org-root>/teams/<team>, reached only when the pointer is unset."""

    def test_org_root_and_team_from_env(self):
        org = os.path.join(self.tmp, "org")
        self.assert_parity(
            _clean_env(HOME=self.home, AGENTWARE_ORG_ROOT=org,
                       AGENTWARE_TEAM="platform"),
            os.path.join(org, "teams", "platform"), msg="org tier from env")

    def test_org_keys_from_config_env(self):
        org = os.path.join(self.tmp, "org")
        self.write_home_config("AGENTWARE_ORG_ROOT=%s\nAGENTWARE_TEAM=payments\n"
                               % org)
        self.assert_parity(_clean_env(HOME=self.home),
                           os.path.join(org, "teams", "payments"),
                           msg="org tier from config.env")

    def test_env_org_root_with_config_team(self):
        org = os.path.join(self.tmp, "org")
        self.write_home_config("AGENTWARE_TEAM=infra\n")
        self.assert_parity(_clean_env(HOME=self.home, AGENTWARE_ORG_ROOT=org),
                           os.path.join(org, "teams", "infra"),
                           msg="mixed env/config org keys")

    def test_direct_pointer_beats_org_composition(self):
        kb = os.path.join(self.tmp, "kb-direct")
        self.assert_parity(
            _clean_env(HOME=self.home, AGENTWARE_KNOWLEDGE_DIR=kb,
                       AGENTWARE_ORG_ROOT=os.path.join(self.tmp, "org"),
                       AGENTWARE_TEAM="platform"),
            kb, msg="INV-1: the direct pointer always wins")

    def test_org_root_without_team_is_unconfigured(self):
        self.assert_parity(
            _clean_env(HOME=self.home,
                       AGENTWARE_ORG_ROOT=os.path.join(self.tmp, "org")),
            "", msg="half-configured org mode resolves nothing")

    def test_team_without_org_root_is_unconfigured(self):
        self.assert_parity(_clean_env(HOME=self.home, AGENTWARE_TEAM="platform"),
                           "", msg="team alone resolves nothing")

    def test_blank_org_key_shadows_a_later_good_one(self):
        """_read_config_key returns on the FIRST key hit, blank value included."""
        self.write_home_config("AGENTWARE_ORG_ROOT=\n"
                               "AGENTWARE_ORG_ROOT=%s\nAGENTWARE_TEAM=platform\n"
                               % os.path.join(self.tmp, "org"))
        self.assert_parity(_clean_env(HOME=self.home), "",
                           msg="blank org key shadows (both sides)")

    def test_org_root_is_normalised(self):
        org = os.path.join(self.tmp, "org")
        raw = os.path.join(self.tmp, "org", "x", "..") + "/"
        self.assert_parity(
            _clean_env(HOME=self.home, AGENTWARE_ORG_ROOT=raw,
                       AGENTWARE_TEAM="platform"),
            os.path.join(org, "teams", "platform"), msg="org root normalised")

    def test_relative_org_root_resolves_against_cwd(self):
        self.assert_parity(
            _clean_env(HOME=self.home, AGENTWARE_ORG_ROOT="org-rel",
                       AGENTWARE_TEAM="platform"),
            os.path.join(os.path.realpath(self.cwd), "org-rel", "teams",
                         "platform"),
            msg="relative org root")

    def test_absolute_team_resets_the_join(self):
        """os.path.join resets on an absolute later component; bash must too."""
        self.assert_parity(
            _clean_env(HOME=self.home,
                       AGENTWARE_ORG_ROOT=os.path.join(self.tmp, "org"),
                       AGENTWARE_TEAM="/elsewhere"),
            "/elsewhere", msg="absolute team component")


class TestShippedCliParity(ResolverCase):
    """The bash twin vs the CLI the loop actually invokes, over the FLAT tiers.

    `config` is unported (task 29: PORTED_VERBS is empty), so this is the preserved
    monolith's resolver — which has the direct-pointer tiers and not the org or
    config-home ones. Those are covered against the lib above; here we prove the
    path production runs today. The CLI needs the real checkout (agentware_legacy
    lives there), so these run against REPO_ROOT with $HOME redirected — the
    monolith derives its config path from expanduser('~'), so a redirected HOME
    isolates it exactly as it isolates the lib.
    """

    def run_cli_resolver(self, env):
        proc = subprocess.run([CLI, "config", "--knowledge-dir-only"],
                              capture_output=True, text=True, env=env,
                              cwd=REPO_ROOT)
        return proc.stdout.strip()

    def test_env_pointer_agrees_with_the_shipped_cli(self):
        kb = os.path.join(self.tmp, "kb-env")
        env = _clean_env(HOME=self.home, AGENTWARE_KNOWLEDGE_DIR=kb)
        bash_out, _ = self.run_bash(env, cwd=REPO_ROOT)
        self.assertEqual(bash_out, self.run_cli_resolver(env))
        self.assertEqual(bash_out, kb)

    def test_home_config_pointer_agrees_with_the_shipped_cli(self):
        kb = os.path.join(self.tmp, "kb-home")
        self.write_home_config("AGENTWARE_KNOWLEDGE_DIR=%s\n" % kb)
        env = _clean_env(HOME=self.home)
        bash_out, _ = self.run_bash(env, cwd=REPO_ROOT)
        self.assertEqual(bash_out, self.run_cli_resolver(env))
        self.assertEqual(bash_out, kb)

    def test_quoted_home_config_pointer_agrees_with_the_shipped_cli(self):
        kb = os.path.join(self.tmp, "kb-q")
        self.write_home_config('AGENTWARE_KNOWLEDGE_DIR="%s"\n' % kb)
        env = _clean_env(HOME=self.home)
        bash_out, _ = self.run_bash(env, cwd=REPO_ROOT)
        self.assertEqual(bash_out, self.run_cli_resolver(env))
        self.assertEqual(bash_out, kb)

    def test_unconfigured_agrees_with_the_shipped_cli(self):
        env = _clean_env(HOME=self.home)
        bash_out, bash_rc = self.run_bash(env, cwd=REPO_ROOT)
        self.assertEqual(bash_out, "")
        self.assertEqual(bash_rc, 1)
        self.assertEqual(self.run_cli_resolver(env), "")


class TestHookCallSitesResolve(unittest.TestCase):
    """Every consumer of the resolver still gets an answer through its own path.

    Task 35 collapsed the six hooks' duplicated resolution onto ONE call site
    (`common.sh:aw_knowledge_dir`), so the coverage here is: the shared helper the
    hooks call, and the independent Python consumer that spawns the script itself.
    A resolver that resolves in isolation but not through `aw_knowledge_dir`'s
    absolute-path invocation is a live defect the parity matrix cannot see.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw_resolver_hooks_")
        self.kb = os.path.join(self.tmp, "kb")
        os.makedirs(self.kb)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_common_sh_helper_resolves(self):
        """aw_knowledge_dir / aw_log_dir, the single seam all six hooks source."""
        script = (
            'set -e\n'
            'AW_REPO_ROOT="%s"\n'
            '. "%s/scripts/hooks/common.sh"\n'
            'printf "%%s\\n" "$(aw_knowledge_dir)"\n'
            'printf "%%s\\n" "$(aw_log_dir)"\n' % (REPO_ROOT, REPO_ROOT))
        env = _clean_env(AGENTWARE_KNOWLEDGE_DIR=self.kb)
        env["HOME"] = self.tmp
        proc = subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, env=env, cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.strip().splitlines()
        self.assertEqual(lines[0], self.kb, "aw_knowledge_dir did not resolve")
        self.assertEqual(lines[1], os.path.join(self.kb, "logs"))

    def test_hooks_sourcing_common_sh_are_unchanged_in_count(self):
        """The resolution stays defined ONCE, and every hook still reaches it."""
        hooks_dir = os.path.join(REPO_ROOT, "scripts", "hooks")
        sourcing = []
        for name in sorted(os.listdir(hooks_dir)):
            path = os.path.join(hooks_dir, name)
            if name == "common.sh" or not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                if "common.sh" in fh.read():
                    sourcing.append(name)
        self.assertGreaterEqual(len(sourcing), 5,
                                "expected >=5 hooks sourcing common.sh, got %r"
                                % sourcing)
        # And the resolver script is invoked from exactly one place in the lib.
        with open(os.path.join(hooks_dir, "common.sh"), encoding="utf-8") as fh:
            common = fh.read()
        self.assertEqual(common.count('scripts/aw-knowledge-dir"'), 1)

    def test_codex_stream_resolves_through_the_script(self):
        """codex-stream.py spawns the resolver directly (it cannot source bash)."""
        driver = (
            "import sys, os\n"
            "sys.path.insert(0, os.path.join(%r, 'scripts', 'hooks'))\n"
            "import importlib.util as u\n"
            "spec = u.spec_from_file_location('codex_stream', os.path.join(%r,"
            " 'scripts', 'hooks', 'codex-stream.py'))\n"
            "m = u.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "sys.stdout.write(m._resolve_log_dir(None))\n" % (REPO_ROOT, REPO_ROOT))
        env = _clean_env()
        env["HOME"] = self.tmp
        # No AGENTWARE_KNOWLEDGE_DIR: force the subprocess spawn of the resolver,
        # which is the branch that would break if the script stopped resolving.
        cfg = os.path.join(self.tmp, ".agentware")
        os.makedirs(cfg, exist_ok=True)
        with open(os.path.join(cfg, "config.env"), "w") as fh:
            fh.write("AGENTWARE_KNOWLEDGE_DIR=%s\n" % self.kb)
        proc = subprocess.run(["python3", "-c", driver], capture_output=True,
                              text=True, env=env, cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), os.path.join(self.kb, "logs"))


class TestParityGateBites(ResolverCase):
    """The gate's own regression arm: a broken twin MUST redden it.

    Without this, "the resolvers agree" is unfalsifiable — a matrix that passes
    against a twin that cannot fail proves nothing (the
    `learn-gate-the-invariant-not-the-outcome-when-a-gate-precedes-its-ports`
    shape). Each mutation removes one parity property the union port added.
    """

    def _mutate(self, old, new):
        with open(self.bash, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn(old, src, "mutation anchor missing from the resolver")
        with open(self.bash, "w", encoding="utf-8") as fh:
            fh.write(src.replace(old, new, 1))

    def test_dropping_the_org_tier_reddens(self):
        self._mutate('  org="$(aw_lookup_key AGENTWARE_ORG_ROOT',
                     '  return 1\n  org="$(aw_lookup_key AGENTWARE_ORG_ROOT')
        env = _clean_env(HOME=self.home,
                         AGENTWARE_ORG_ROOT=os.path.join(self.tmp, "org"),
                         AGENTWARE_TEAM="platform")
        bash_out, _ = self.run_bash(env)
        self.assertNotEqual(bash_out, self.run_lib(env),
                            "removing the org tier did NOT break parity")

    def test_dropping_config_home_reddens(self):
        self._mutate('CONFIG_HOME="$(aw_trim "${AGENTWARE_CONFIG_HOME:-}")"',
                     'CONFIG_HOME=""')
        cfg_home = os.path.join(self.tmp, "cfg-home")
        kb = os.path.join(self.tmp, "kb-cfghome")
        self.write_home_config("AGENTWARE_KNOWLEDGE_DIR=%s\n" % kb,
                               config_home=cfg_home)
        env = _clean_env(HOME=self.home, AGENTWARE_CONFIG_HOME=cfg_home)
        bash_out, _ = self.run_bash(env)
        self.assertNotEqual(bash_out, self.run_lib(env),
                            "ignoring AGENTWARE_CONFIG_HOME did NOT break parity")

    def test_dropping_abspath_normalisation_reddens(self):
        self._mutate('  local p="$1" lead="/" out="" comp',
                     '  printf \'%s\' "$1"; return 0\n'
                     '  local p="$1" lead="/" out="" comp')
        raw = os.path.join(self.tmp, "kb-norm", "sub", "..") + "/"
        env = _clean_env(HOME=self.home, AGENTWARE_KNOWLEDGE_DIR=raw)
        bash_out, _ = self.run_bash(env)
        self.assertNotEqual(bash_out, self.run_lib(env),
                            "dropping abspath did NOT break parity")

    def test_dropping_blank_value_handling_reddens(self):
        self._mutate('  envval="$(aw_trim "${AGENTWARE_KNOWLEDGE_DIR:-}")"',
                     '  envval="${AGENTWARE_KNOWLEDGE_DIR:-}"')
        kb = os.path.join(self.tmp, "kb-home")
        self.write_home_config("AGENTWARE_KNOWLEDGE_DIR=%s\n" % kb)
        env = _clean_env(HOME=self.home, AGENTWARE_KNOWLEDGE_DIR="   ")
        bash_out, _ = self.run_bash(env)
        self.assertNotEqual(bash_out, self.run_lib(env),
                            "treating a blank env value as SET did NOT break parity")


if __name__ == "__main__":
    unittest.main()
