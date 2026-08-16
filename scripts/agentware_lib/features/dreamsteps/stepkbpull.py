"""agentware_lib.features.dreamsteps.stepkbpull -- dream step "1", kb-pull: pull the latest KNOWLEDGE-BASE commits from upstream (layer: features).

Owns: dream step "1" (_dream_step_kb_pull) and the named helpers it decomposes
into -- the short-HEAD reader (_dream_pull_head_sha), the three graceful skip
guards (_dream_pull_skip_reason), the shared success payload
(_dream_pull_ok), the offline / stale-lock classifier
(_dream_pull_offline_reason) and the derived-file rebase resolver
(_dream_pull_resolve_derived). The step's target is ALWAYS the knowledge base;
the package repository is NEVER pulled or reset (see the RETIRED note below).
Does NOT own: the timeout runner + post-pull conflict check ->
dreamsteps.dreamgit; the perf/capture/fingerprint primitives ->
dreamsteps.dreamcapture; the other steps -> dreamsteps.step<step>; the ordered
registry that binds this handler to id "1" -> dreamsteps.registry. See
catalog/modules.json.
Layer deps: core (gitio, BY NAME) + knowledge (index) + ops (kbgit, staging) --
all DOWNWARD -- plus two dreamsteps SIBLINGS (dreamgit, and nothing else), which
import no step module, so the package graph stays acyclic.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: every failure path degrades to a SKIP or a FAIL dict rather than an
exception, so an unattended cron never crashes (INV-2); a prose conflict aborts
the rebase cleanly and only DERIVED files (index.json / FEATURES.md / rosters)
are auto-resolved by rebuild. Timeout: DREAM_PULL_TIMEOUT seconds.
"""

import agentware_lib.knowledge.index as index
import agentware_lib.ops.kbgit as kbgit
import agentware_lib.ops.staging as staging

from agentware_lib.core.gitio import _git, _git_noninteractive_env

from .dreamgit import DREAM_PULL_TIMEOUT, _dream_pull_post_check, _git_timed
import agentware_lib.ops.staging as _staging


# RETIRED — dream step "0", the PACKAGE-repository pull, and the two helpers that
# existed only to serve it (its post-pull syntax check and its hard-reset revert).
# It was a rival UNSIGNED updater: `git pull --ff-only`/`--rebase` to tip-of-main
# on the package root, guarded only by py_compile and reverted with a hard reset
# of the repository root. That (a) defeated the signed-tag thesis of the dedicated
# updater, (b) could stamp a higher schema out-of-band so the updater's brick-guard
# then refused rollback and stranded the machine, and (c) raced the periodic tick
# on the same tree with non-cross-checking locks.
#
# The INVARIANT it violated, and which this package now restores: a dream cycle is
# KB maintenance and convergence sync ONLY — it never mutates the package tree, so
# it can never race or brick the updater. ALL package updates are delegated to the
# dedicated, VERIFIED signed-tag updater, the ONLY code path that may advance the
# repository root. `_dream_pull_post_check` is RETAINED (in dreamsteps.dreamgit):
# it is shared by the KB-pull step, which stays.
#
# ACCEPTED trade-off (documented): the updater no-ops fail-closed until the first
# signed tag is cut, so there is NO package auto-update until then — that is the
# intended no-unsigned-updates posture, NOT a regression.


def _dream_pull_head_sha(kdir):
    """The KB work tree's short HEAD sha, or "?" when git cannot report one."""
    head = _git(kdir, "rev-parse", "--short", "HEAD")
    return head.stdout.strip() if head.returncode == 0 else "?"


def _dream_pull_skip_reason(kdir):
    """The graceful-skip result for a KB that cannot be pulled, else None.

    Not a git work tree / no upstream / a pre-existing rebase are all NORMAL
    states for a machine-local KB, so each degrades to a clean skip.
    """
    if not kbgit.git_is_work_tree(kdir):
        return {"status": "skipped", "reason": "KB is not a git work tree"}
    if not kbgit.git_has_upstream(kdir):
        return {"status": "skipped", "reason": "KB has no upstream"}
    if staging.git_rebase_in_progress(kdir):
        return {"status": "skipped",
                "reason": "pre-existing rebase in progress — resolve manually"}
    return None


def _dream_pull_ok(kdir, sha0, method, updated=None):
    """The success payload shared by the ff and rebase paths.

    Runs the autostash-pop post-check, re-reads HEAD, and reports the from/to
    shas. `updated` is derived from the sha delta unless the caller pins it (the
    rebase path always advanced, even when the short sha is unchanged).
    """
    warning = _dream_pull_post_check(kdir)
    sha1 = _dream_pull_head_sha(kdir)
    result = {"status": "ok", "method": method,
              "from": sha0, "to": sha1,
              "updated": (sha0 != sha1) if updated is None else updated}
    if warning:
        result["warning"] = warning
    return result


def _dream_pull_offline_reason(stderr):
    """Classify a pull failure that left NO rebase in progress: a stale index
    lock or (the common unattended case) simply being offline."""
    first_err = stderr.strip().splitlines()
    err_line = first_err[0] if first_err else "unknown"
    if "index.lock" in stderr:
        return {"status": "skipped", "reason": "stale lock file — %s" % err_line}
    return {"status": "skipped", "reason": "offline — %s" % err_line}


def _dream_pull_resolve_derived(kdir, sha0, env):
    """Resolve a conflicted rebase whose unmerged paths are ALL derived files.

    Derived-file conflicts (index.json, FEATURES.md, rosters) are regenerated by
    rebuild and the rebase continues; a prose conflict aborts cleanly (same as
    dream's push policy: on_prose_conflict=abort) and needs a manual merge.
    """
    unmerged = staging.git_unmerged_paths(kdir)
    non_derived = [p for p in unmerged if not staging._is_derived_rel(p)]
    if non_derived:
        _git(kdir, "rebase", "--abort", env=env)
        return {"status": "fail",
                "reason": "prose conflict in %s — aborted (needs manual merge)"
                          % ", ".join(non_derived[:5])}
    data, _rosters, errors = index.rebuild_kb(kdir)
    if errors:
        _git(kdir, "rebase", "--abort", env=env)
        return {"status": "fail",
                "reason": "rebuild during pull-merge failed: %s" % "; ".join(errors)}
    rc_add, _n, _rej = _staging.kb_git_stage_scoped(kdir)
    if rc_add != 0:
        _git(kdir, "rebase", "--abort", env=env)
        return {"status": "fail",
                "reason": "git add failed: %s" % add.stderr.strip()}
    cont = _git(kdir, "rebase", "--continue", env=env)
    if cont.returncode != 0:
        _git(kdir, "rebase", "--abort", env=env)
        cont_lines = (cont.stderr or "").strip().splitlines()
        return {"status": "fail",
                "reason": "rebase --continue failed: %s"
                          % (cont_lines[0] if cont_lines else "unknown")}
    sha1 = _dream_pull_head_sha(kdir)
    return {"status": "ok", "method": "rebase-resolve",
            "from": sha0, "to": sha1, "updated": True,
            "resolved": "derived files regenerated"}


def _dream_step_kb_pull(kdir, dry_run):
    """Step 1 — pull latest KB commits from upstream.

    Gets shared KB changes (learnings, configs, profiles) from teammates.
    Non-interactive with --autostash. Derived-file conflicts (index.json,
    FEATURES.md, rosters) are auto-resolved via rebuild. Prose conflicts
    abort cleanly (same as dream's push policy: on_prose_conflict=abort).
    Graceful skip if not a git repo, no upstream, offline, or pre-existing
    rebase. Timeout: DREAM_PULL_TIMEOUT seconds.
    """
    if dry_run:
        return {"status": "planned",
                "would": "git pull KB repo (%s)" % kdir}
    skip = _dream_pull_skip_reason(kdir)
    if skip is not None:
        return skip
    sha0 = _dream_pull_head_sha(kdir)
    env = _git_noninteractive_env()
    ff = _git_timed(kdir, "pull", "--ff-only", "--autostash", env=env)
    if ff.returncode == 0:
        return _dream_pull_ok(kdir, sha0, "ff")
    rb = _git_timed(kdir, "pull", "--rebase", "--autostash", env=env)
    if rb.returncode == 0:
        return _dream_pull_ok(kdir, sha0, "rebase", updated=True)
    stderr = rb.stderr or ""
    if rb.returncode == 124:
        if staging.git_rebase_in_progress(kdir):
            _git(kdir, "rebase", "--abort", env=env)
        return {"status": "skipped", "reason": "timed out after %ds" % DREAM_PULL_TIMEOUT}
    if not staging.git_rebase_in_progress(kdir):
        return _dream_pull_offline_reason(stderr)
    return _dream_pull_resolve_derived(kdir, sha0, env)


_EXPORTS = [
    "_dream_pull_head_sha",
    "_dream_pull_skip_reason",
    "_dream_pull_ok",
    "_dream_pull_offline_reason",
    "_dream_pull_resolve_derived",
    "_dream_step_kb_pull",
]
