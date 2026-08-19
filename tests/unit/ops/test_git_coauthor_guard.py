"""Commit-msg co-author strip guard (feature 260712-p2b-hardening-followups,
Task 6). A deterministic CODE guard for the operator preference "no Claude/AI
co-author trailer": a git commit-msg hook that STRIPS the trailer (never blocks),
installed only with explicit consent via `config --install-git-guards`.

  hook   — strips `Co-Authored-By: Claude/Anthropic` + a line-start `Generated with
           ... Claude Code` trailer; keeps a HUMAN co-author, Signed-off-by, and body
           prose that merely mentions Claude; always exits 0 (strip, not block).
  install— opt-in, NON-CLOBBER: a pre-existing foreign hook is backed up AND chained
           (still runs), uninstall restores it. The loop never commits (R-GIT-01).

Hermetic + SAFE: git runs with GIT_CONFIG_GLOBAL/SYSTEM=/dev/null and HOME on a
throwaway, so a real global `core.hooksPath` can NEVER be touched — the guard lands
only in the throwaway repo's `.git/hooks` (this was a real hazard: the operator has
a global hooksPath). `test_gitcoauthor_` is a unique -k prefix across the suite.
Runner: python3 -m unittest tests.unit.ops.test_git_coauthor_guard -v
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

try:
    from tests._fixtures import REPO_ROOT, load_cli
except ImportError:
    from _fixtures import REPO_ROOT, load_cli

HOOK_SRC = os.path.join(REPO_ROOT, "scripts", "hooks", "commit-msg")
CLAUDE_TRAILER = "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
GENERATED = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"


def _have(binary):
    return shutil.which(binary) is not None


@unittest.skipUnless(_have("bash"), "bash required")
class HookStripTests(unittest.TestCase):
    """The hook itself, run directly on a message file (no git needed)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-coauthor-hook-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _run_hook(self, text):
        msg = os.path.join(self.tmp, "COMMIT_EDITMSG")
        with open(msg, "w", encoding="utf-8") as f:
            f.write(text)
        cp = subprocess.run(["bash", HOOK_SRC, msg], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=30)
        self.assertEqual(cp.returncode, 0, cp.stdout)   # strip, never block
        with open(msg, encoding="utf-8") as f:
            return f.read()

    def test_gitcoauthor_strips_claude_trailer(self):
        out = self._run_hook("feat: thing\n\nBody.\n\n%s\n" % CLAUDE_TRAILER)
        self.assertNotIn("Claude", out)
        self.assertIn("feat: thing", out)
        self.assertIn("Body.", out)

    def test_gitcoauthor_strips_anthropic_and_generated(self):
        out = self._run_hook(
            "fix: y\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\n%s\n"
            % GENERATED)
        self.assertNotIn("anthropic", out.lower())
        self.assertNotIn("Generated with", out)
        self.assertIn("fix: y", out)

    def test_gitcoauthor_keeps_human_coauthor_and_signoff(self):
        human = "Co-Authored-By: Jane Dev <jane@example.com>"
        signoff = "Signed-off-by: Jane Dev <jane@example.com>"
        out = self._run_hook("feat: z\n\n%s\n%s\n%s\n" % (CLAUDE_TRAILER, human, signoff))
        self.assertIn(human, out)
        self.assertIn(signoff, out)
        self.assertNotIn("noreply@anthropic.com", out)

    def test_gitcoauthor_keeps_body_mention_of_claude(self):
        # A body sentence that merely MENTIONS the phrase (not a line-start trailer)
        # must survive — the Generated-with match is anchored at line start.
        body = "This was generated with Claude Code assistance."
        out = self._run_hook("feat: q\n\n%s\n\n%s\n" % (body, GENERATED))
        self.assertIn(body, out)
        self.assertNotIn(GENERATED, out)

    def test_gitcoauthor_clean_message_unchanged(self):
        clean = "feat: clean\n\nA normal body with no AI trailers.\n"
        self.assertEqual(self._run_hook(clean), clean)

    def test_gitcoauthor_missing_arg_is_noop_exit0(self):
        cp = subprocess.run(["bash", HOOK_SRC], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=30)
        self.assertEqual(cp.returncode, 0)

    def test_gitcoauthor_keeps_human_at_anthropic_and_named_claude(self):
        # Only the BOT trailer (noreply@anthropic.com) is stripped; a human at their
        # real @anthropic.com address, or a human literally named Claude, survives.
        human_anthropic = "Co-Authored-By: Jane Smith <jane@anthropic.com>"
        human_claude = "Co-Authored-By: Claude Human <claude@example.com>"
        out = self._run_hook("feat: h\n\n%s\n%s\n%s\n"
                             % (human_anthropic, human_claude, CLAUDE_TRAILER))
        self.assertIn(human_anthropic, out)
        self.assertIn(human_claude, out)
        self.assertNotIn("noreply@anthropic.com", out)   # bot trailer stripped

    def test_gitcoauthor_all_trailer_message_preserved_not_emptied(self):
        # A message that is ONLY the bot trailer must NOT be emptied — an empty
        # COMMIT_EDITMSG makes git ABORT the commit, which would be block-not-strip.
        out = self._run_hook("%s\n" % CLAUDE_TRAILER)
        self.assertTrue(out.strip(), "hook emptied the message -> git would abort")


@unittest.skipUnless(_have("git") and _have("bash"), "git + bash required")
class GuardInstallTests(unittest.TestCase):
    """Install / commit / non-clobber-chain / uninstall — fully isolated from the
    operator's real global git hooks (core.hooksPath) via env."""

    def setUp(self):
        self.mod = load_cli()
        self.base = tempfile.mkdtemp(prefix="agentware-coauthor-git-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        # HARD isolation: never resolve/touch the real ~/.gitconfig or a global
        # core.hooksPath — the guard must land only in this throwaway repo.
        self._env = mock.patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": self.home,
        })
        self._env.start()
        self.addCleanup(self._env.stop)
        self.repo = os.path.join(self.base, "repo")
        os.makedirs(self.repo)
        self._g("init", "-q", "-b", "main")
        self._g("config", "user.email", "op@example.com")
        self._g("config", "user.name", "Operator")
        self.hooks = os.path.join(self.repo, ".git", "hooks")
        self.dest = os.path.join(self.hooks, "commit-msg")

    def _g(self, *a):
        return subprocess.run(["git", "-C", self.repo] + list(a), check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    _commit_n = 0

    def _commit(self, message):
        self._commit_n += 1
        with open(os.path.join(self.repo, "f_%d" % self._commit_n), "w") as f:
            f.write("x")
        self._g("add", "-A")
        self._g("commit", "-m", message)
        return self._g("log", "-1", "--format=%B").stdout

    def test_gitcoauthor_install_places_marked_hook(self):
        rc = self.mod._install_git_guards(self.repo)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(self.dest))
        self.assertTrue(os.access(self.dest, os.X_OK))
        with open(self.dest, encoding="utf-8") as f:
            self.assertIn("agentware commit-msg guard", f.read())

    def test_gitcoauthor_installed_hook_strips_on_real_commit(self):
        self.mod._install_git_guards(self.repo)
        body = self._commit("feat: real\n\nBody line.\n\n%s\n%s"
                            % (CLAUDE_TRAILER, GENERATED))
        self.assertNotIn("Claude", body)
        self.assertNotIn("Generated with", body)
        self.assertIn("feat: real", body)

    def test_gitcoauthor_refuses_non_git_dir(self):
        plain = os.path.join(self.base, "plain")
        os.makedirs(plain)
        rc = self.mod._install_git_guards(plain)
        self.assertEqual(rc, 2)                       # not a repo
        self.assertFalse(os.path.exists(os.path.join(plain, ".git")))

    def test_gitcoauthor_non_clobber_backs_up_and_chains(self):
        # A pre-existing FOREIGN commit-msg hook that appends a sentinel to the
        # message. After install it must be backed up AND still run (chained).
        os.makedirs(self.hooks, exist_ok=True)
        with open(self.dest, "w", encoding="utf-8") as f:
            f.write('#!/bin/bash\nprintf "\\nforeign-ran: yes\\n" >> "$1"\nexit 0\n')
        os.chmod(self.dest, 0o755)
        rc = self.mod._install_git_guards(self.repo)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(self.dest + ".pre-agentware"))   # backed up
        body = self._commit("feat: chained\n\n%s" % CLAUDE_TRAILER)
        self.assertIn("foreign-ran: yes", body)       # foreign STILL ran (chained)
        self.assertNotIn("Claude", body)              # AND agentware stripped

    def test_gitcoauthor_uninstall_restores_foreign(self):
        os.makedirs(self.hooks, exist_ok=True)
        with open(self.dest, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\nexit 0\n# my original hook\n")
        os.chmod(self.dest, 0o755)
        self.mod._install_git_guards(self.repo)
        rc = self.mod._uninstall_git_guards(self.repo)
        self.assertEqual(rc, 0)
        with open(self.dest, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("my original hook", content)              # restored
        self.assertNotIn("agentware commit-msg guard", content)  # guard gone
        self.assertFalse(os.path.exists(self.dest + ".pre-agentware"))

    def test_gitcoauthor_uninstall_leaves_foreign_untouched(self):
        # A foreign hook with NO agentware install must be left untouched.
        os.makedirs(self.hooks, exist_ok=True)
        with open(self.dest, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\nexit 0\n# someone-elses hook\n")
        os.chmod(self.dest, 0o755)
        rc = self.mod._uninstall_git_guards(self.repo)
        self.assertEqual(rc, 0)
        with open(self.dest, encoding="utf-8") as f:
            self.assertIn("someone-elses hook", f.read())

    def test_gitcoauthor_install_idempotent(self):
        self.assertEqual(self.mod._install_git_guards(self.repo), 0)
        self.assertEqual(self.mod._install_git_guards(self.repo), 0)   # re-install ok
        # no self-backup on re-install (the marker is recognized)
        self.assertFalse(os.path.exists(self.dest + ".pre-agentware"))


if __name__ == "__main__":
    unittest.main()
