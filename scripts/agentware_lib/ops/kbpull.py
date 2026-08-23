"""agentware_lib.ops.kbpull -- the KB git PULL path and its post-pull reconciliations (layer: ops).

Owns: `kb-git pull` end to end — the fetch/fast-forward primitives (`git_fetch`,
`git_pull_ff`), the handler `cmd_kb_git_pull`, and the two RECONCILIATIONS a
successful fast-forward must trigger (`_rebuild_kb_after_pull`,
`_skill_reconcile_after_pull`, plus the `_kb_entry_files_changed` predicate that
decides whether the first is needed).
Does NOT own: the commit/push/merge paths or the scoped stage allowlist (those
stay in `ops.staging`); the rebuild itself (`knowledge.index.indexrebuild`); the
skills reconcile (`features.skillsync`).

WHY A SEPARATE MODULE
---------------------
Task 54 restored the two post-pull reconciliations, which this tree's modular
copy had never had, and that pushed `ops.staging` past the HARD 600-line file cap
(no debt is available for a file). The pull path is the natural seam: it is the
only half of kb-git that MUTATES the knowledge base as a side effect of a git
operation, and the reconciliations are why.

WHAT THE RECONCILIATIONS PROTECT
--------------------------------
A fast-forward brings in a teammate's entry FILES; the index that makes them
recallable is DERIVED and is not in the pull. Without `_rebuild_kb_after_pull`
those entries exist on disk and are invisible to `recall` until someone happens
to rebuild — a silent, unbounded window in which the KB under-reports itself, and
the failure looks like "my teammate's note never synced". `_skill_reconcile_after_pull`
is the same story for skills: a pulled skill that never reaches the
pending-approval roster is simply not there. Both are best-effort by design and
NEITHER may ever raise or block the pull: a reconciliation that breaks the sync it
follows is worse than the staleness it fixes.

Layer deps: DOWNWARD only -- core.config/gitio/kblayout, knowledge.index,
ops.kbgit; `features.skillsync` is reached FUNCTION-LOCALLY inside the skills
reconcile because that module reaches back into this surface and a module-scope
edge would close the cycle.
"""

import os
import subprocess  # noqa: F401 — resolves the by-name-imported gitio._git's LOAD_GLOBAL 'subprocess' (global-resolve gate)
import sys

import agentware_lib.core.config as config
from agentware_lib.core.config import require_knowledge_dir
from agentware_lib.core.gitio import (
    _git, _git_head, _git_noninteractive_env, git_is_clean, git_is_work_tree)
from agentware_lib.knowledge.index import (
    KNOWLEDGE_SUBDIRS, build_index_from_frontmatter, rebuild_kb,
    _is_sync_candidate)
from agentware_lib.features.skilllock import (
    _skill_cache_dir, load_skill_lock, save_skill_lock)
from agentware_lib.ops.kbgit import (
    _first_line, git_has_upstream, git_toplevel)


def git_pull_ff(target):
    """Fast-forward-only `git pull`; non-interactive (never prompts for input).

    Returns the CompletedProcess. `GIT_TERMINAL_PROMPT=0` makes an offline /
    auth-required fetch fail fast instead of hanging for credentials
    (R-SHELL-01), and `--ff-only` refuses to create a merge commit — a divergent
    upstream is reported as a non-zero exit the caller treats as a graceful skip
    (the push/rebase path, Phase 4, owns real divergence).
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return _git(target, "pull", "--ff-only", env=env)


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


def _kb_entry_files_changed(target, old_ref, new_ref):
    """True iff any INDEXABLE entry `.md` file changed between two refs.

    Rosters (`<sub>/index.md`) and non-knowledge files are ignored — only real
    entry files (`<KNOWLEDGE_SUBDIRS>/…md`, minus the roster) drive an index
    rebuild. On any git error the diff is UNKNOWN, so we conservatively return
    True (rebuild rather than risk a stale index). Pure git; no new dependency.
    """
    cp = _git(target, "diff", "--name-only", "%s..%s" % (old_ref, new_ref))
    if cp.returncode != 0:
        return True
    for rel in cp.stdout.splitlines():
        rel = rel.strip().strip('"')
        if not rel.endswith(".md"):
            continue
        parts = rel.split("/")
        if parts and parts[0] in KNOWLEDGE_SUBDIRS and _is_sync_candidate(rel):
            return True
    return False


def _rebuild_kb_after_pull(target, before, after):
    """After a HEAD-advancing pull, rebuild the DERIVED index iff tracked entry
    files changed. Best-effort; NEVER raises. Returns True iff it rebuilt.

    The rebuilt files (index.json / rosters / FEATURES.md) are gitignored, so this
    reconciliation never dirties the git tree or blocks the next pull/commit.

    NOTHING-LOST at the SYNC boundary: unlike the push paths (guarded by
    kb_nothing_lost_gate), this pull reconcile has no gate, and rebuild_kb
    quarantines a malformed / duplicate-id (same-id-different-file) entry the pull
    introduced instead of failing. Such an entry stays on disk but is dropped from
    the index (unrecallable until resolved), so we surface it LOUDLY here rather
    than report a clean success — the drop is REPORTED, never silent.
    """
    if not (before and after and before != after):
        return False
    if not _kb_entry_files_changed(target, before, after):
        return False
    try:
        data, _rosters, errors = rebuild_kb(target)
    except Exception:  # noqa: BLE001 — reconciliation must never break a pull
        return False
    if errors or data is None:
        return False
    # Re-derive the quarantine list (deterministic; rebuild_kb already logged it to
    # stderr, but the pull-level summary must reflect a lossy sync, not silent OK).
    try:
        quarantined = build_index_from_frontmatter(target)[1]
    except Exception:  # noqa: BLE001
        quarantined = []
    if quarantined:
        sys.stderr.write(
            "agentware: kb-git pull: WARNING — %d pulled entr%s QUARANTINED from "
            "the index (on disk but UNRECALLABLE until the id collision/malformed "
            "frontmatter is resolved): %s\n"
            % (len(quarantined), "y" if len(quarantined) == 1 else "ies",
               "; ".join(quarantined)))
    return True


def _skill_reconcile_after_pull(kdir):
    """Quarantine-only reconcile invoked at the tail of a kb-git pull so a freshly
    pulled skill reaches the pending-approval roster. NEVER installs, NEVER
    raises, NEVER blocks the pull (best-effort, mirrors _rebuild_kb_after_pull)."""
    # Function-local: `features.skillsync` reaches back into the staging/kb-git
    # surface, so a module-scope edge from here would close the cycle. Deferring
    # it also keeps the skills machinery entirely off the import path of a pull
    # that never needs it.
    from agentware_lib.features.skillsync import _skill_reconcile, _skill_sync_lock
    try:
        with _skill_sync_lock():
            lock = load_skill_lock()
            _skill_reconcile(kdir, lock, _skill_cache_dir(None, lock))
            save_skill_lock(lock)
    except Exception:  # noqa: BLE001 - reconciliation must never break a pull
        return False
    return True


def _pull_preconditions(target):
    """The three preconditions a fast-forward pull requires, IN ORDER.

    Returns True when the pull may proceed. Each unmet precondition PRINTS its
    own notice and returns False, which the caller turns into a graceful rc-0
    skip — an unmet precondition never blocks work (C-4).

    The order is load-bearing, and is the reason this is ONE function rather
    than three inline guards that could be reordered without anyone noticing: a
    repo with no upstream is reported as such even when it is ALSO dirty,
    because "you have no remote to pull from" is the actionable fact and
    "commit your work first" would send the operator to fix the wrong thing.
    """
    # C-4: degrade to local-only (never block) when the KB is not a git repo.
    if not git_is_work_tree(target):
        print("kb-git pull: %s is not a git work tree — skipping pull "
              "(local-only)." % target)
        return False

    if not git_has_upstream(target):
        print("kb-git pull: no upstream tracking branch — skipping pull "
              "(local-only).")
        return False

    # Precondition: a clean tree. Prior writes must be committed first so a
    # fast-forward can never clobber or conflict with uncommitted edits.
    if not git_is_clean(target):
        print("kb-git pull: work tree has uncommitted changes — skipping pull "
              "(commit first; never pulling onto uncommitted writes).")
        return False
    return True


def cmd_kb_git_pull(args):
    """Fast-forward the KB repo from its upstream at a safe point.

    Pull cadence (feature 260625-kb-git-sync, Task 3.1): run at agent start and
    again right before a push. Preconditions — the target is a git work tree
    (C-4), has a configured upstream, and the tree is CLEAN (prior writes must be
    committed first; we NEVER pull onto uncommitted edits). Any unmet
    precondition, an offline fetch, or a non-fast-forward divergence is a
    graceful SKIP (rc 0 + a clear notice) — pulling NEVER blocks work. Beyond the
    fast-forward this mutates nothing.
    """
    override = getattr(args, "path", None)
    if override:
        target = os.path.abspath(os.path.expanduser(override))
    else:
        target = require_knowledge_dir()
        if not target:
            return 1

    if not _pull_preconditions(target):
        return 0

    before = _git_head(target)
    cp = git_pull_ff(target)
    if cp.returncode != 0:
        print("kb-git pull: skipped — %s (offline or diverged; not blocking "
              "work)." % _first_line(cp.stderr, "fetch failed"))
        return 0

    summary = _first_line(cp.stdout, "up to date")
    # Trigger (ii): a fast-forward that ADVANCED HEAD and changed tracked entry
    # `.md` files makes the DERIVED index stale — teammates' pulled entries would
    # be silently dropped by recall (they are not in our old index.json) until the
    # next rebuild. Reconcile now so a pull leaves the KB immediately recallable.
    if _rebuild_kb_after_pull(target, before, _git_head(target)):
        summary += " (index rebuilt from pulled frontmatter)"
    # Trigger (feature 260713-p4): a QUARANTINE-ONLY skills reconcile so a freshly
    # pulled skill reaches the pending-approval roster. NEVER installs, NEVER
    # raises, NEVER blocks the pull (best-effort; mirrors _rebuild_kb_after_pull).
    _skill_reconcile_after_pull(target)
    print("kb-git pull: %s" % summary)
    return 0


_EXPORTS = ["git_fetch", "git_pull_ff", "_pull_preconditions",
             "cmd_kb_git_pull", "_kb_entry_files_changed",
             "_rebuild_kb_after_pull", "_skill_reconcile_after_pull"]

__all__ = list(_EXPORTS)
