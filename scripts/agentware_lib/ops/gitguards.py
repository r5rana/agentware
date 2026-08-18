"""agentware_lib.ops.gitguards -- install/uninstall the KB git guard hooks (layer: ops).

Owns: the `config --install-git-guards` / `--uninstall-git-guards` machinery —
resolving a repository's hooks dir, copying the shipped guard hook in under a
marker, and removing only what that marker claims.
Does NOT own: the `config` flags themselves (`features.configcmd.cfgports`), the
routes that dispatch them (`cfgroutes`), or any KB git operation (`ops.kbgit`,
`ops.staging`, `ops.kbpull`).

WHY IT LANDED LATE
------------------
The FLAGS were registered during the surface pass and the HANDLERS never were, so
`config --install-git-guards` parsed, exited 0 and installed nothing. That is the
worst shape a gap can take: an operator who runs it believes the guards are in
place. Task 54's port of `config` is where the behaviour arrives.

Layer deps: DOWNWARD only — core.paths for the shipped hook source, ops.kbgit for
`git_toplevel`. Marker-scoped by construction: uninstall removes a hook ONLY when
the marker is present, so a hand-written hook the operator owns is never deleted.
"""

import json
import os
import shutil
import sys

from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.core.gitio import _git
from agentware_lib.ops.kbgit import git_toplevel


# --- Task 6: opt-in commit-msg co-author strip guard (operator commits) --------
# A deterministic CODE guard for the recorded operator preference "no Claude/AI
# co-author trailer" — injected prose loses to the harness default, so a git
# commit-msg hook STRIPS the trailer (never blocks the commit). Installed only with
# explicit consent (config --install-git-guards); the loop itself never commits
# (R-GIT-01). The hook is a package file (scripts/hooks/commit-msg); installing it
# COPIES it into a target repo's .git/hooks (allowed: .git is not a tracked file).
GIT_GUARD_MARKER = "agentware commit-msg guard"


def _git_guards_hook_src():
    return os.path.join(REPO_ROOT, "scripts", "hooks", "commit-msg")


def _resolve_git_hooks_dir(target):
    """Absolute git hooks dir for `target`, honoring core.hooksPath + worktrees
    (via `git rev-parse --git-path hooks`), or None if it cannot be resolved."""
    cp = _git(target, "rev-parse", "--git-path", "hooks")
    if cp.returncode != 0:
        return None
    p = (cp.stdout or "").strip()
    if not p:
        return None
    return p if os.path.isabs(p) else os.path.join(target, p)


def _install_git_guards(target, as_json=False):
    """Install the commit-msg co-author strip guard into `target`'s git hooks.
    Opt-in, NON-CLOBBER (a foreign commit-msg hook is backed up to
    commit-msg.pre-agentware first), idempotent. rc 0 ok / 1 FS error / 2 not-a-repo
    or no target."""
    def emit(payload, lines, rc):
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            for ln in lines:
                print(ln)
        return rc
    if not target:
        return emit({"ok": False, "status": "no-target"},
                    ["git-guards: no target repo (pass --repo or configure a "
                     "knowledge dir)."], 2)
    if git_toplevel(target) is None:
        return emit({"ok": False, "status": "not-a-repo", "target": target},
                    ["git-guards: %s is not a git work tree — nothing to install "
                     "(the KB repo becomes one on its first commit)." % target], 2)
    src = _git_guards_hook_src()
    if not os.path.isfile(src):
        return emit({"ok": False, "status": "no-source", "src": src},
                    ["git-guards: hook source missing (%s)." % src], 1)
    hooks_dir = _resolve_git_hooks_dir(target)
    if not hooks_dir:
        return emit({"ok": False, "status": "no-hooks-dir", "target": target},
                    ["git-guards: could not resolve the git hooks dir for %s."
                     % target], 1)
    dest = os.path.join(hooks_dir, "commit-msg")
    backed_up = None
    try:
        os.makedirs(hooks_dir, exist_ok=True)
        if os.path.exists(dest):
            with open(dest, "r", encoding="utf-8", errors="replace") as f:
                existing = f.read()
            if GIT_GUARD_MARKER not in existing:
                backup = dest + ".pre-agentware"
                if not os.path.exists(backup):
                    shutil.copyfile(dest, backup)
                    os.chmod(backup, 0o755)   # keep it executable so the guard can CHAIN to it
                    backed_up = backup
        shutil.copyfile(src, dest)
        os.chmod(dest, 0o755)
    except OSError as exc:
        return emit({"ok": False, "status": "error", "detail": str(exc)},
                    ["git-guards: install FAILED: %s" % exc], 1)
    lines = ["git-guards: installed the commit-msg co-author strip guard -> %s"
             % dest,
             "  strips the Claude Code bot's `Co-Authored-By: … noreply@anthropic.com`"
             " + `Generated with Claude Code` trailers (human co-authors survive);"
             " NEVER blocks a commit."]
    if backed_up:
        lines.append("  backed up + CHAINED your existing commit-msg hook (it still "
                     "runs first) -> %s" % backed_up)
    if os.path.abspath(hooks_dir) != os.path.abspath(os.path.join(target, ".git", "hooks")):
        lines.append("  note: this resolved to %s (core.hooksPath / a worktree) — it "
                     "governs hooks for its repo(s)." % hooks_dir)
    return emit({"ok": True, "status": "installed", "hook": dest,
                 "backed_up": backed_up, "hooks_dir": hooks_dir}, lines, 0)


def _uninstall_git_guards(target, as_json=False):
    """Remove the agentware commit-msg guard from `target` (restoring a backed-up
    foreign hook if present). Idempotent: a clean no-op when absent, and a foreign
    (non-agentware) hook is LEFT untouched. rc 0 ok / 1 FS error / 2 not-a-repo."""
    def emit(payload, lines, rc):
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            for ln in lines:
                print(ln)
        return rc
    if not target or git_toplevel(target) is None:
        return emit({"ok": False, "status": "not-a-repo", "target": target},
                    ["git-guards: %s is not a git work tree." % target], 2)
    hooks_dir = _resolve_git_hooks_dir(target)
    dest = os.path.join(hooks_dir, "commit-msg") if hooks_dir else None
    if not dest or not os.path.exists(dest):
        return emit({"ok": True, "status": "absent"},
                    ["git-guards: no commit-msg guard installed — nothing to remove."], 0)
    try:
        with open(dest, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if GIT_GUARD_MARKER not in content:
            return emit({"ok": True, "status": "foreign-kept", "hook": dest},
                        ["git-guards: the commit-msg hook is not agentware's — "
                         "left untouched."], 0)
        os.remove(dest)
        backup = dest + ".pre-agentware"
        restored = None
        if os.path.exists(backup):
            shutil.move(backup, dest)
            os.chmod(dest, 0o755)
            restored = dest
    except OSError as exc:
        return emit({"ok": False, "status": "error", "detail": str(exc)},
                    ["git-guards: uninstall FAILED: %s" % exc], 1)
    lines = ["git-guards: removed the commit-msg co-author strip guard."]
    if restored:
        lines.append("  restored your previous commit-msg hook.")
    return emit({"ok": True, "status": "removed", "restored": restored}, lines, 0)


_EXPORTS = ["GIT_GUARD_MARKER", "_git_guards_hook_src",
             "_resolve_git_hooks_dir", "_install_git_guards",
             "_uninstall_git_guards"]

__all__ = list(_EXPORTS)
