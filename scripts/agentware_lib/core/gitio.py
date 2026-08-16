"""agentware_lib.core.gitio — low-level git runners (layer: core).

Owns: the low-level git subprocess runner(s) with high cross-domain fan-in.
Opened wave 3 with _git_capture (a read-only git command run in the repo root
returning stripped stdout, or '' on any failure — non-zero exit OR the OSError of
git being absent); _git_noninteractive_env (the fail-fast never-prompt git env)
co-moved wave-4 seam 8; and — COMPLETING the module in wave-5 seam 1 — the general
`git -C <target> <args>` CompletedProcess runner _git (~88 resident call sites;
never raises: FileNotFoundError → synthetic returncode 127) and the on-branch
@{upstream} ref-name resolver _git_upstream_name (reads _git). Assembled across
waves per the plan's "open early with a primitives block, complete later"
allowance — domain purity is the contract, wave order is not.
Does NOT own: any git WORKFLOW (pull/fetch/push/rebase/status/staging helpers stay
with the KB-git + staging code in agentware_lib.ops); the timed/dream git wrapper
(_git_timed defaults its timeout to a dream-domain constant, so it lives with
features.dream — putting it here would make core reference a feature and invert
the layer graph); the HEAD-snapshot reporter git_info (an evaluate/ledger symbol,
agentware_lib.retrieval.evaluate — it CONSUMES this primitive, it is not one).
Layer deps: core -> core only (imports agentware_lib.core.paths for REPO_ROOT; nothing above).
Fan-in: 15 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

core is the STABLE, feature-independent layer: a single zero-workflow git EXEC
primitive with high fan-in — evaluate's ledger (git_info), the A/B HEAD extraction
(_extract_head_cli/_extract_head_scripts), and the KB/fleet git surfaces all shell
git through it — earns its own tiny core leaf so every consumer shares ONE
definition without inverting the dependency direction. Same rationale as
core.paths / core.dates: a high-fan-in primitive gets its own module, never a
grab-bag home.

The repo root is read from agentware_lib.core.paths.REPO_ROOT (the marker-walk
anchor), NOT recomputed here: in the A/B HEAD extraction the split entrypoint runs
from a throwaway temp tree with a synthesized AGENTS.md marker, so paths.REPO_ROOT
resolves to that temp root — byte-identical to what the unmoved entrypoint's own
REPO_ROOT resolved to before this move.
"""

import os
import subprocess

from agentware_lib.core import paths
from agentware_lib.core.paths import REPO_ROOT


def _git_capture(argv, env=None):
    """Run a read-only git command in REPO_ROOT; return stripped stdout or ''."""
    try:
        proc = subprocess.run(
            ["git"] + argv, cwd=paths.REPO_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, env=env)
    except OSError:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


# NOT part of the entrypoint's _EXPORTS re-export loop (same as core.paths):
# _git_capture is a private primitive reached by its OWNER-module reference —
# gitio._git_capture — from the still-unmoved entrypoint code, and from wave 4/5
# on by direct `from agentware_lib.core.gitio import _git_capture` in the lib
# modules that need it. __all__ documents the module's public surface for the
# granular-test surface floor + the discovery manifest's anchor symbols.
def _git_noninteractive_env():
    """Env that makes git fail fast instead of prompting (stdin/editor/creds)."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # never prompt for credentials (R-SHELL-01)
    env["GIT_EDITOR"] = "true"        # never open an editor (rebase --continue)
    return env


def _git(target, *args, **kwargs):
    """Run `git -C <target> <args>`; return the CompletedProcess.

    Never raises on a non-zero exit (check=False); callers inspect returncode.
    Captures stdout/stderr as text. If the `git` binary is absent the
    FileNotFoundError is surfaced as a synthetic returncode 127 so callers can
    degrade to local-only (C-4) without crashing.
    """
    cmd = ["git", "-C", target] + [str(a) for a in args]
    try:
        return subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=False, **kwargs)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", "git: command not found")


def _git_upstream_name(target):
    """The current branch's upstream ref name (e.g. `origin/main`), or "".

    Resolved while ON the branch (before any rebase detaches HEAD) so the
    nothing-lost gate can reference the upstream parent by a concrete name that
    still resolves mid-rebase.
    """
    cp = _git(target, "rev-parse", "--abbrev-ref",
              "--symbolic-full-name", "@{upstream}")
    return cp.stdout.strip() if cp.returncode == 0 else ""


# _git / _git_upstream_name ARE re-exported BY NAME on the entrypoint facade
# (unlike the private _git_capture): ~88 resident bare-name `_git(...)` callers in
# the KB-git/staging/fanout/dream workflow code + the resident kb_git_push_once's
# `_git_upstream_name(target)` resolve them as bare re-exported names.

# --- repository STATE predicates (feature 260813 task 41) -------------------
# Moved here rather than into the updater that needed them: 'is this a work tree',
# 'is it clean', 'does it have an upstream', 'is A an ancestor of B' are questions
# about GIT, not about updating, and the update channel, the dream sync and the
# release preflight all ask them. One definition, one answer.

def git_is_work_tree(target):
    """True iff `target` is inside a git work tree."""
    cp = _git(target, "rev-parse", "--is-inside-work-tree")
    return cp.returncode == 0 and cp.stdout.strip() == "true"


def git_has_upstream(target):
    """True iff the current branch has a configured upstream tracking branch.

    This is the precise precondition for a fast-forward pull / push: a bare
    remote alone is not enough — the branch must track an upstream (`@{u}`).
    """
    if not git_is_work_tree(target):
        return False
    cp = _git(target, "rev-parse", "--abbrev-ref",
              "--symbolic-full-name", "@{upstream}")
    return cp.returncode == 0 and bool(cp.stdout.strip())


def git_is_clean(target):
    """True iff the work tree has no staged/unstaged/untracked changes."""
    if not git_is_work_tree(target):
        return False
    cp = _git(target, "status", "--porcelain")
    return cp.returncode == 0 and cp.stdout.strip() == ""


def git_fetch(target):
    """Read-only `git fetch` of the configured upstream; non-interactive.

    Returns the CompletedProcess. `GIT_TERMINAL_PROMPT=0` makes an offline /
    auth-required fetch fail fast instead of hanging for credentials
    (R-SHELL-01). A fetch updates only the remote-tracking refs — it NEVER
    touches the work tree, so it is safe to run for a read-only `update --check`
    (and as a SessionStart updates-available nudge).
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return _git(target, "fetch", "--quiet", env=env)


def _git_head(target):
    """Current HEAD sha of `target`, or None if it cannot be resolved."""
    cp = _git(target, "rev-parse", "HEAD")
    return cp.stdout.strip() if cp.returncode == 0 else None


def _is_ancestor(repo, ancestor, descendant):
    """True iff `ancestor` is an ancestor of `descendant` (already-merged check)."""
    return _git(repo, "merge-base", "--is-ancestor",
                ancestor, descendant).returncode == 0


DREAM_PULL_TIMEOUT = 300  # 5 min per pull step — prevents indefinite hangs


def _git_timed(target, *args, **kwargs):
    """Like _git but with a hard timeout (DREAM_PULL_TIMEOUT seconds).

    On timeout, kills the ENTIRE process group (git + its children like ssh,
    git-remote-https, pack-objects) to prevent orphaned processes consuming
    bandwidth. Returns a synthetic rc 124 (like GNU timeout). The git process
    is killed but the repo is NOT left in a broken state — git fetch/pull are
    atomic (refs update only on success), and a killed rebase leaves a
    rebase-in-progress that the pre-flight check handles on the next cycle.
    """
    timeout = kwargs.pop("timeout", DREAM_PULL_TIMEOUT)
    cmd = ["git", "-C", target] + [str(a) for a in args]
    try:
        # start_new_session=True puts git in its own process group so we can
        # kill the entire group (git + ssh + pack-objects) on timeout.
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True, **kwargs)
        with proc:
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Kill the entire process group, not just the leader
                try:
                    os.killpg(proc.pid, 9)  # SIGKILL to the group
                except (OSError, ProcessLookupError):
                    proc.kill()  # fallback: kill just the leader
                proc.wait()
                return subprocess.CompletedProcess(
                    cmd, 124, "", "timed out after %ds" % timeout)
            return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", "git: command not found")
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 126, "", "OS error: %s" % exc)


def _pinned_ref():
    """Short git SHA of the package repo, or 'unknown' (read-only)."""
    cp = _git(REPO_ROOT, "rev-parse", "--short", "HEAD")
    if cp.returncode == 0 and cp.stdout.strip():
        return cp.stdout.strip()
    return "unknown"


__all__ = ["_git", "_git_capture", "_git_noninteractive_env", "_git_upstream_name",
           "git_is_work_tree", "git_has_upstream", "git_is_clean", "git_fetch",
           "_git_head", "_is_ancestor", "DREAM_PULL_TIMEOUT", "_git_timed",
           "_pinned_ref"]
