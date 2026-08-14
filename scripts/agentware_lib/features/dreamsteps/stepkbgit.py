"""agentware_lib.features.dreamsteps.stepkbgit -- dream step "f", kb-git-commit: the nightly knowledge-base backup (layer: features).

Owns: dream step "f" (_dream_step_kb_git) -- the cycle's LAST step and its only
durable-backup action: commit the knowledge base through the shared kb-git
commit verb and, when the KB has an upstream, optionally push it. Owns the two
independent gates that make the step a clean no-op rather than a failure on an
ordinary host: autocommit (AGENTWARE_KB_AUTOCOMMIT) governs the commit, and
AGENTWARE_KB_PUSH governs ONLY the push, so a machine that commits locally but
never pushes is a first-class configuration. Owns the before/after HEAD compare
that decides whether anything was actually committed.
Does NOT own: the commit + push verbs it re-enters (cmd_kb_git_commit ->
ops.staging, cmd_kb_git_push -> ops.kbsync); the work-tree / upstream / rebase
probes -> ops.kbgit + ops.staging; the capture harness -> dreamsteps.dreamcapture;
the other steps -> dreamsteps.step<step>; the ordered registry that binds this
handler to id "f" -> dreamsteps.registry. See catalog/modules.json.
Layer deps: core (config, and the _git primitive BY NAME from core.gitio) + ops
(kbgit, kbsync, staging) -- all DOWNWARD -- plus ONE dreamsteps SIBLING
(dreamcapture, a stdlib-only leaf), so the package graph stays acyclic.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: guarded and best-effort (INV-2). It NEVER stages the package repo
-- the underlying helper refuses (C-3) -- and every gate that is off degrades to
a structured "skipped" payload instead of raising into an unattended cron. The
--dry-run branch is a PURE plan: it probes NO environment-specific state, so
`dream --dry-run` stays byte-stable (INV-1) regardless of the host KB's git
status.
"""

import argparse

import agentware_lib.core.config as config
import agentware_lib.ops.kbgit as kbgit
import agentware_lib.ops.kbsync as kbsync
import agentware_lib.ops.staging as staging

from agentware_lib.core.gitio import _git

from .dreamcapture import _dream_capture


def _dream_step_kb_git(kdir, dry_run):
    """Step f — nightly backup: kb-git commit (+ optional push), gated on
    autocommit; push is separately gated on AGENTWARE_KB_PUSH (default ON).
    A non-tracked KB / autocommit OFF / no upstream degrades to a clean no-op.
    NEVER stages the package repo (the helper refuses, C-3)."""
    # Dry-run is a PURE plan: report the intended step uniformly with every other
    # step and probe NO environment-specific state (autocommit / git work tree),
    # so `dream --dry-run` is byte-stable regardless of the host KB's git status.
    if dry_run:
        return {"status": "planned", "would": "kb-git commit (+ optional push)"}
    if config.resolve_kb_autocommit() != "1":
        return {"status": "skipped", "reason": "kb autocommit OFF"}
    if not kbgit.git_is_work_tree(kdir):
        return {"status": "skipped", "reason": "KB not a git work tree"}
    if staging.git_rebase_in_progress(kdir):
        return {"status": "skipped",
                "reason": "KB has rebase-in-progress — refusing to commit"}
    head0 = _git(kdir, "rev-parse", "HEAD")
    sha0 = head0.stdout.strip() if head0.returncode == 0 else None
    ns = argparse.Namespace(path=None, tag="dream", type="chore", message=None)
    rc, _out = _dream_capture(staging.cmd_kb_git_commit, ns)
    head1 = _git(kdir, "rev-parse", "HEAD")
    sha1 = head1.stdout.strip() if head1.returncode == 0 else None
    committed = bool(sha1) and sha1 != sha0
    result = {"status": "ok" if rc == 0 else "fail", "committed": committed,
              "sha": sha1[:9] if committed else "-"}
    if committed and kbgit.git_has_upstream(kdir):
        if config.resolve_kb_push() == "1":
            push_ns = argparse.Namespace(path=None, on_prose_conflict="abort",
                                         max_retries=3, format="text")
            prc, _ = _dream_capture(kbsync.cmd_kb_git_push, push_ns)
            result["pushed"] = prc == 0
        else:
            result["pushed"] = False
            result["push_skipped"] = "AGENTWARE_KB_PUSH=0"
    else:
        result["pushed"] = False
    return result


_EXPORTS = [
    "_dream_step_kb_git",
]
