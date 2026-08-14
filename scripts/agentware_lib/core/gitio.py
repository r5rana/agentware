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
__all__ = ["_git", "_git_capture", "_git_noninteractive_env", "_git_upstream_name"]
