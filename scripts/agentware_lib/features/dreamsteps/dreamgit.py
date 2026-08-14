"""agentware_lib.features.dreamsteps.dreamgit -- the dream cycle's git primitives: the hard-timeout runner + the post-pull conflict check (layer: features).

Owns: the per-pull hard timeout budget (DREAM_PULL_TIMEOUT), the process-group
killing git runner an unattended cron can never hang on (_git_timed), and the
autostash-pop conflict detector a successful pull is followed by
(_dream_pull_post_check). Every target is the KNOWLEDGE BASE work tree; the
package repository is NEVER a target (see the RETIRED note in
dreamsteps.stepkbpull) -- all package updates are delegated to the dedicated
verified signed-tag updater, so no function here reaches paths.REPO_ROOT.
Does NOT own: the perf clock / capture harness / fingerprint / report path ->
dreamsteps.dreamcapture; the KB-pull STEP that drives these ->
dreamsteps.stepkbpull; the registry -> dreamsteps.registry. See
catalog/modules.json.
Layer deps: core (gitio) DOWNWARD only -- the git primitives (_git) are imported
BY NAME; stdlib os/subprocess at module scope. No sibling package import beyond
that, so it cannot cycle.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: _git_timed returns a synthetic rc 124 (like GNU timeout) rather than
raising, so a hung remote degrades to a clean SKIP instead of crashing an
unattended cycle (INV-2). _dream_pull_post_check acts ONLY on real unmerged
conflict states (UU/AA/DU/UD) -- a merely dirty tree is the user's legitimate
in-progress work and is never touched.
"""

import os
import subprocess

from agentware_lib.core.gitio import _git


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


def _dream_pull_post_check(target):
    """After a successful pull, detect autostash-pop conflicts.

    When --autostash pops and conflicts, git returns 0 for the pull but leaves
    conflict markers in the work tree (shown as UU in porcelain status). A
    dirty-but-no-conflicts tree is FINE — it just means the stash was re-applied
    successfully (the user had legitimate uncommitted edits). Only act on actual
    CONFLICT state (UU/AA/DU/UD). Returns a warning string or None.

    CRITICAL: never run destructive commands (checkout --, clean -fd) on the
    target unless there are REAL unmerged conflicts. The tree may be dirty with
    the user's legitimate in-progress work.
    """
    cp = _git(target, "status", "--porcelain")
    if cp.returncode != 0:
        return None
    has_conflicts = any(
        line[:2] in ("UU", "AA", "DU", "UD")
        for line in cp.stdout.splitlines() if len(line) >= 2)
    if not has_conflicts:
        return None
    # Real conflict from autostash pop. In stash-pop semantics:
    #   "ours"   = current HEAD (the freshly-pulled/rebased state — what we want)
    #   "theirs" = the stashed local edits (what we want to discard on conflict)
    # Accept --ours to keep the pulled version for conflicted files only.
    for line in cp.stdout.splitlines():
        if len(line) >= 2 and line[:2] in ("UU", "AA", "DU", "UD"):
            path = line[3:].strip()
            if path:
                _git(target, "checkout", "--ours", "--", path)
                _git(target, "add", "--", path)
    return "autostash pop conflicted — kept pulled version (stash preserved)"


_EXPORTS = [
    "DREAM_PULL_TIMEOUT",
    "_git_timed",
    "_dream_pull_post_check",
]
