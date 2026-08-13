"""agentware_lib.ops.kbsync -- KB-git push/merge orchestration + the nothing-lost gate (layer: ops).

Owns: the deterministic, agent-free KB-git PUSH/MERGE workflow (feature 260625-kb-git-sync,
Phases 4-5) -- the mechanical nothing-lost gate kb_nothing_lost_gate (C-2: rebuild+validate
the index from frontmatter, then assert the merged entry-ID set is a SUPERSET of every
resolvable parent), the single-attempt push with derived-file conflict auto-resolution
kb_git_push_once, its bounded pull->resolve->push retry wrapper kb_git_push_synced (C-2
backoff+jitter), the prose-conflict merge continuation kb_git_merge_continue, their CLI
handlers cmd_kb_git_push / cmd_kb_git_merge_continue, and the `kb-git` parser registrar
register_kb_git (which wires ALL five kb-git sub-verbs: status -> kbgit, commit/pull ->
staging, push/merge-continue -> here).

Does NOT own: the scoped-staging engine + commit/pull workflow + push/entry-id/backoff
PRIMITIVES it consumes (git_push / _push_was_rejected / RC_REPUSH_RACE / git_unmerged_paths /
git_rebase_in_progress / file_has_conflict_markers / _is_derived_rel / git_entry_ids_at_ref /
KB_PUSH_MAX_RETRIES / _kb_push_backoff_delay / cmd_kb_git_commit / cmd_kb_git_pull ->
ops.staging); the read-only git work-tree STATE helpers (git_is_work_tree / git_toplevel /
git_has_upstream / cmd_kb_git_status -> ops.kbgit); the low-level git runners (_git /
_git_noninteractive_env / _git_upstream_name -> core.gitio); and the index rebuild/validate
the gate calls (rebuild_kb / build_index_from_frontmatter / validate_index -> knowledge.index).

Layer deps: ops.kbsync -> core (config/gitio/paths) DOWNWARD + knowledge.index DOWNWARD +
the SAME-LAYER siblings ops.staging (as staging) and ops.kbgit (as kbgit) -- all acyclic:
neither staging nor kbgit imports kbsync (kbsync is a pure sink, fan-in 0). Nothing above ops
imports it.

Determinism: no agent, no LLM, no RNG (INV-1). The push retry backoff jitter is derived from
attempt+pid in ops.staging; every conflict RESOLUTION is verified by the nothing-lost gate
BEFORE the network push, so a lossy/invalid merge is rejected, never pushed.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so the resident fanout
merge-queue gate (kb_nothing_lost_gate) + the dream repoint step (cmd_kb_git_push) resolve
them as bare names, and load_cli tests reach them as CLI.<name>. register_kb_git is NOT
exported (it is declared in this module's `__verbs__`).
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import os
import subprocess  # noqa: F401 -- resolves the by-name-imported gitio._git's LOAD_GLOBAL 'subprocess' (global-resolve gate)
import sys
import time

import agentware_lib.core.config as config
import agentware_lib.knowledge.index as index
import agentware_lib.ops.kbgit as kbgit
import agentware_lib.ops.staging as staging
from agentware_lib.core.gitio import _git, _git_noninteractive_env, _git_upstream_name
from agentware_lib.core.paths import REPO_ROOT


def kb_nothing_lost_gate(target, parent_refs):
    """Mechanical "nothing lost" gate after a merge resolution (C-2).

    Runs ON the resolved work tree, BEFORE the network push, so a lossy or
    invalid merge is REJECTED before it can reach the remote:

      1. index rebuild → index validate: re-derive the index from frontmatter
         (`build_index_from_frontmatter`, read-only — writes nothing) and validate
         it. Any frontmatter / validation error fails the gate.
      2. entry-ID superset check: the merged entry-ID set MUST be a SUPERSET of
         the UNION of every resolvable parent's entry-ID set. A parent ref that
         does not resolve is skipped (not counted as empty). If any id was dropped
         the gate fails.

    Returns (ok, note). The caller aborts/leaves the rebase and fails loud on a
    False result — no silent loss. Deterministic, no agent, no new dependency.
    """
    data, errors = index.build_index_from_frontmatter(target)
    if errors:
        return False, ("nothing-lost gate: invalid frontmatter after merge: %s"
                       % "; ".join(errors))
    verrs = index.validate_index(target, data)
    if verrs:
        return False, ("nothing-lost gate: index invalid after merge: %s"
                       % "; ".join(verrs))

    merged = {e.get("id") for e in data["entries"] if e.get("id")}
    union = set()
    for ref in parent_refs:
        if not ref:
            continue
        pids = staging.git_entry_ids_at_ref(target, ref)
        if pids is None:
            continue  # parent ref unavailable (e.g. detached @{upstream}) — skip
        union |= pids
    dropped = union - merged
    if dropped:
        return False, ("nothing-lost gate: merge would DROP entry id(s): %s — "
                       "merged set is not a superset of the parents (C-2 abort)"
                       % ", ".join(sorted(dropped)))
    return True, ("nothing-lost gate: ok (%d merged id(s) ⊇ %d parent id(s))"
                  % (len(merged), len(union)))

def kb_git_push_once(target, on_prose_conflict="abort"):
    """One push attempt with deterministic derived-file conflict resolution.

    Returns (rc, note). rc 0 == pushed or a graceful no-op skip (C-4); rc 1 ==
    fail loud; rc 3 == paused on a prose conflict (only when
    `on_prose_conflict == "pause"`) with the rebase LEFT IN PROGRESS for the
    MERGE_PROMPT — `note` is `"PROSE_CONFLICT\n<rel>\n<rel>..."`. The flow:

      1. push → success: done.
      2. push rejected (upstream moved) → `pull --rebase`.
         - rebase clean → push again.
         - rebase conflicts ONLY in derived files → rebuild from the entry union
           (`rebuild_kb`, C-1, NO agent), stage, `rebase --continue`, push.
         - any conflict in a NON-derived (entry) file → same-entry prose case:
           * on_prose_conflict == "abort" (default): `rebase --abort` + fail loud
             rc 1 (preserves the deterministic, agent-free push contract).
           * on_prose_conflict == "pause" (Phase 5): leave the rebase in progress
             and return rc 3 + the conflicted entry-file list, so the loop can
             reconcile them with the curated MERGE_PROMPT (agentware-execution)
             and then run `kb-git merge-continue`. NO agent is invoked here.
      3. push failed for any other reason (offline/auth) → graceful skip (C-4).

    Every conflict RESOLUTION is verified by the nothing-lost gate (C-2) BEFORE
    the resulting commit is pushed: a lossy or invalid merge aborts the rebase
    and fails loud rather than reaching the remote. A re-push race returns
    RC_REPUSH_RACE so `kb_git_push_synced` can retry within a bounded cap.
    """
    # Captured ON the branch (before any rebase detaches HEAD) so the nothing-lost
    # gate can name the upstream parent with a ref that still resolves mid-rebase.
    upstream_name = _git_upstream_name(target)

    push = staging.git_push(target)
    if push.returncode == 0:
        out = (push.stderr or push.stdout or "").strip().splitlines()
        return 0, "pushed%s" % ((" — " + out[-1]) if out else "")

    if not staging._push_was_rejected(push):
        first = (push.stderr or "push failed").strip().splitlines()
        return 0, "skipped — %s (offline or no access; not blocking work)" % (
            first[0] if first else "push failed")

    # Upstream moved: rebase our local commits on top of it, resolving any
    # derived-file conflicts deterministically.
    env = _git_noninteractive_env()
    rebase = _git(target, "pull", "--rebase", env=env)
    if rebase.returncode == 0:
        push2 = staging.git_push(target)
        if push2.returncode == 0:
            return 0, "pushed (after fast-forward rebase)"
        # A conflict-free rebase replays every commit verbatim (no synthesized
        # content) so there is nothing for the gate to catch; a rejection here is
        # purely a re-push race for the bounded retry.
        return (staging.RC_REPUSH_RACE,
                "push still rejected after clean rebase (re-push race)")

    unmerged = staging.git_unmerged_paths(target)
    if not unmerged:
        _git(target, "rebase", "--abort", env=env)
        first = (rebase.stderr or "rebase failed").strip().splitlines()
        return 1, "rebase failed: %s" % (first[0] if first else "unknown")

    non_derived = [p for p in unmerged if not staging._is_derived_rel(p)]
    if non_derived:
        # Same-entry prose conflict — the deterministic path stops here (C-1: an
        # agent NEVER merges via this engine). Two dispositions:
        if on_prose_conflict == "pause":
            # Phase 5: leave the rebase in progress for the curated MERGE_PROMPT.
            # The loop reconciles these entry files via agentware-execution, then
            # calls `kb-git merge-continue` (which rebuilds the derived files).
            return 3, "PROSE_CONFLICT\n" + "\n".join(non_derived)
        # Default: abort cleanly and fail loud rather than touch entry files.
        _git(target, "rebase", "--abort", env=env)
        return 1, ("conflict in non-derived file(s) %s — needs prose merge "
                   "(not auto-resolvable here)" % ", ".join(non_derived))

    # All conflicts are in derived files: regenerate them from the union of
    # entry frontmatter (the entry files themselves merged cleanly). NO agent.
    data, _rosters, errors = index.rebuild_kb(target)
    if errors:
        _git(target, "rebase", "--abort", env=env)
        return 1, "rebuild during merge failed: %s" % "; ".join(errors)

    # SCOPED, never `git add -A`: in org mode a blanket add stages another
    # team's files as part of this team's merge — a cross-team write that looks
    # like an ordinary commit in the log.
    rc, _n, _rej = staging.kb_git_stage_scoped(target)
    if rc != 0:
        _git(target, "rebase", "--abort", env=env)
        return 1, "git add failed during merge (scoped allowlist)"

    # Nothing-lost gate (C-2): the resolved (mid-rebase) work tree must rebuild to
    # a valid index whose entry-ID set is a superset of BOTH parents. Fail loud +
    # abort the rebase BEFORE the push if anything was dropped — no silent loss.
    ok, gnote = kb_nothing_lost_gate(target, ["ORIG_HEAD", upstream_name])
    if not ok:
        _git(target, "rebase", "--abort", env=env)
        return 1, gnote

    cont = _git(target, "rebase", "--continue", env=env)
    if cont.returncode != 0:
        _git(target, "rebase", "--abort", env=env)
        first = (cont.stderr or "rebase --continue failed").strip().splitlines()
        return 1, "rebase --continue failed: %s" % (first[0] if first else "")

    push3 = staging.git_push(target)
    if push3.returncode == 0:
        return 0, "pushed (derived files auto-resolved via index rebuild)"
    return (staging.RC_REPUSH_RACE,
            "push rejected after deterministic resolve (re-push race)")

def kb_git_push_synced(target, on_prose_conflict="abort", max_retries=None):
    """`kb_git_push_once` wrapped in a bounded pull→resolve→push retry (C-2).

    Each attempt runs a full `kb_git_push_once` (which itself pulls/rebases and
    resolves derived-file conflicts under the nothing-lost gate). When the upstream
    moves AGAIN between our resolve and our push (`RC_REPUSH_RACE`), we retry the
    whole cycle up to `max_retries` times WITH EXPONENTIAL BACKOFF + JITTER (models
    a centrally hosted git server's write lock at fleet scale), then FAIL LOUD
    (rc 1) — never an unbounded loop. Any other disposition (pushed rc 0, graceful
    skip rc 0, prose pause rc 3, fail-loud rc 1) returns immediately.
    """
    attempts = max(1, max_retries if max_retries is not None else staging.KB_PUSH_MAX_RETRIES)
    last_note = "no attempt"
    for attempt in range(1, attempts + 1):
        rc, note = kb_git_push_once(target, on_prose_conflict=on_prose_conflict)
        if rc != staging.RC_REPUSH_RACE:
            return rc, note
        last_note = note  # race — pull/resolve/push again after a backoff
        if attempt < attempts:
            time.sleep(staging._kb_push_backoff_delay(attempt))
    return 1, ("push failed after %d re-push race retries (backoff+jitter): %s "
               "(upstream write-lock contention — the local commit is retained + "
               "recoverable)" % (attempts, last_note))

def cmd_kb_git_push(args):
    """Push KB commits to upstream, auto-resolving derived-file conflicts.

    Git-sync push (feature 260625-kb-git-sync, Task 4.1). Preconditions: the
    target is a git work tree (C-4) with a configured upstream, and is NOT the
    agentware package repo (C-3). A push rejected by a moved upstream is resolved
    by rebasing and regenerating the DERIVED files (`index.json`, rosters,
    `FEATURES.md`) from the entry union via `index rebuild` — never by an agent
    (C-1). Offline / no-upstream / non-repo are graceful rc-0 skips (never block).
    """
    override = getattr(args, "path", None)
    if override:
        target = os.path.abspath(os.path.expanduser(override))
    else:
        target = config.require_knowledge_dir()
        if not target:
            return 1

    # C-4: degrade to local-only (never block) when the KB is not a git repo.
    if not kbgit.git_is_work_tree(target):
        print("kb-git push: %s is not a git work tree — skipping push "
              "(local-only)." % target)
        return 0

    # C-3 scope guard: NEVER push the agentware package repo.
    top = kbgit.git_toplevel(target)
    pkg_top = kbgit.git_toplevel(REPO_ROOT)
    if top and pkg_top and top == pkg_top:
        sys.stderr.write(
            "kb-git push: refusing — target resolves to the agentware package "
            "repo (%s); sync is scoped to the KB repo only (C-3).\n" % top)
        return 1

    if not kbgit.git_has_upstream(target):
        print("kb-git push: no upstream tracking branch — skipping push "
              "(local-only).")
        return 0

    mode = getattr(args, "on_prose_conflict", "abort")
    retries = getattr(args, "max_retries", 3) or 3
    rc, note = kb_git_push_synced(target, on_prose_conflict=mode,
                                  max_retries=retries)

    if rc == 3 and note.startswith("PROSE_CONFLICT"):
        # Paused on a same-entry prose conflict (Phase 5). Emit the conflicted
        # entry paths on stdout (one per line) so the loop can feed them to the
        # MERGE_PROMPT; the human note goes to stderr. The rebase is LEFT IN
        # PROGRESS — the loop reconciles, then runs `kb-git merge-continue`.
        files = [l for l in note.splitlines()[1:] if l.strip()]
        for f in files:
            print(f)
        sys.stderr.write(
            "kb-git push: prose conflict in %d entry file(s) — rebase left in "
            "progress; reconcile via the MERGE_PROMPT, then run "
            "`kb-git merge-continue`.\n" % len(files))
        return 3

    stream = sys.stdout if rc == 0 else sys.stderr
    stream.write("kb-git push: %s\n" % note)
    return rc

def kb_git_merge_continue(target):
    """Finish a prose-merge rebase after the agent reconciled the entry files.

    Phase 5 second half. Preconditions: a rebase is in progress (left by
    `kb-git push --on-prose-conflict pause`) and the conflicted ENTRY files have
    been reconciled in the work tree (both sides' facts retained, no conflict
    markers). Deterministic, C-1/C-2:

      1. Require a rebase in progress (else nothing to continue).
      2. Refuse if any reconciled entry file still carries conflict markers — the
         prose merge is incomplete (do NOT continue, do NOT abort: the partial
         work is preserved for another reconciliation pass).
      3. Stage the reconciled entry files, then UNCONDITIONALLY rebuild the
         DERIVED files (`index.json`, rosters, `FEATURES.md`) from the entry
         frontmatter union (`rebuild_kb`) — so even if the agent touched a derived
         file it is overwritten with the canonical regenerated content (C-1).
      4. `rebase --continue`, then push.

    Returns (rc, note). rc 0 == pushed; rc 1 == fail loud (the rebase is left in
    place for inspection, never silently aborted).
    """
    env = _git_noninteractive_env()
    if not staging.git_rebase_in_progress(target):
        return 1, "no rebase in progress — nothing to continue"

    unmerged = staging.git_unmerged_paths(target)
    entry_unmerged = [p for p in unmerged if not staging._is_derived_rel(p)]
    for rel in entry_unmerged:
        if staging.file_has_conflict_markers(os.path.join(target, rel)):
            return 1, ("unresolved conflict markers remain in %s — prose merge "
                       "incomplete (not continuing; rebase left in place)" % rel)

    # Stage the agent-reconciled entry files (C-1: derived files are NEVER
    # hand-merged — they are regenerated below).
    if entry_unmerged:
        add = _git(target, "add", "--", *entry_unmerged)
        if add.returncode != 0:
            return 1, "git add (entry files) failed: %s" % add.stderr.strip()

    # Regenerate the DERIVED files from the reconciled entry union — never merged.
    data, _rosters, errors = index.rebuild_kb(target)
    if errors:
        return 1, "rebuild after prose merge failed: %s" % "; ".join(errors)

    # The ONE caller that legitimately stages DERIVED files: this step just
    # REGENERATED them from the merged entry union, so they are the output of the
    # merge rather than a stale artifact, and leaving them unstaged leaves the
    # tree dirty for the next lane. Still scoped — cross-team paths and oversized
    # blobs are refused exactly as everywhere else.
    rc2, _n2, _rej2 = staging.kb_git_stage_scoped(target, allow_derived=True)
    if rc2 != 0:
        return 1, "git add (derived) failed (scoped allowlist)"

    # Nothing-lost gate (C-2): the reconciled (mid-rebase) work tree must rebuild
    # to a valid index whose entry-ID set is a superset of BOTH parents. On
    # failure leave the rebase in place (never silently abort — the partial work
    # is preserved for another reconciliation pass) and fail loud BEFORE the push.
    ok, gnote = kb_nothing_lost_gate(target, ["ORIG_HEAD", "@{upstream}"])
    if not ok:
        return 1, gnote

    cont = _git(target, "rebase", "--continue", env=env)
    if cont.returncode != 0:
        first = (cont.stderr or "rebase --continue failed").strip().splitlines()
        return 1, "rebase --continue failed: %s" % (first[0] if first else "")

    push = staging.git_push(target)
    if push.returncode == 0:
        return 0, "pushed (prose conflict reconciled; derived files rebuilt)"
    return 1, "push rejected after prose merge (re-push race)"

def cmd_kb_git_merge_continue(args):
    """Continue a paused prose-merge rebase, then push (feature 260625-kb-git-sync).

    Runs after the curated MERGE_PROMPT reconciled the conflicted entry markdown.
    Same scope guards as push: a non-repo target is a graceful no-op (C-4), and
    the agentware package repo is refused (C-3). The derived files are always
    rebuilt from frontmatter, never hand-merged (C-1).
    """
    override = getattr(args, "path", None)
    if override:
        target = os.path.abspath(os.path.expanduser(override))
    else:
        target = config.require_knowledge_dir()
        if not target:
            return 1

    # C-4: degrade to local-only (never block) when the KB is not a git repo.
    if not kbgit.git_is_work_tree(target):
        print("kb-git merge-continue: %s is not a git work tree — nothing to "
              "continue (local-only)." % target)
        return 0

    # C-3 scope guard: NEVER operate on the agentware package repo.
    top = kbgit.git_toplevel(target)
    pkg_top = kbgit.git_toplevel(REPO_ROOT)
    if top and pkg_top and top == pkg_top:
        sys.stderr.write(
            "kb-git merge-continue: refusing — target resolves to the agentware "
            "package repo (%s); sync is scoped to the KB repo only (C-3).\n" % top)
        return 1

    rc, note = kb_git_merge_continue(target)
    stream = sys.stdout if rc == 0 else sys.stderr
    stream.write("kb-git merge-continue: %s\n" % note)
    return rc


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("kb-git", 2800, "register_kb_git"),
)


def register_kb_git(sub, fmt_parent):
    # kb-git -----------------------------------------------------------------
    p_kbgit = sub.add_parser(
        "kb-git", help="knowledge-base git sync (detection, commit, pull/push)")
    kbgit_sub = p_kbgit.add_subparsers(dest="kb_git_command", metavar="<subcommand>")
    kbgit_sub.required = True
    p_kbstatus = kbgit_sub.add_parser(
        "status", parents=[fmt_parent],
        help="report is_work_tree / has_upstream / is_clean for the KB repo")
    p_kbstatus.add_argument(
        "--path", default=None,
        help="repo dir to inspect (default: the resolved knowledge dir)")
    p_kbstatus.set_defaults(func=kbgit.cmd_kb_git_status)

    p_kbcommit = kbgit_sub.add_parser(
        "commit",
        help="stage + commit ONLY the KB repo as one feat|chore(tag): commit")
    p_kbcommit.add_argument(
        "--path", default=None,
        help="KB repo dir to commit (default: the resolved knowledge dir)")
    p_kbcommit.add_argument(
        "--tag", default=None,
        help="commit scope tag (sanitized to [a-z0-9._-]; default: kb)")
    p_kbcommit.add_argument(
        "--type", choices=("feat", "chore"), default="chore",
        help="conventional-commit type (default: chore)")
    p_kbcommit.add_argument(
        "--message", default=None,
        help="commit summary (default: deterministic changed-dirs summary)")
    p_kbcommit.set_defaults(func=staging.cmd_kb_git_commit)

    p_kbpull = kbgit_sub.add_parser(
        "pull",
        help="fast-forward the KB repo from upstream at a safe point (no-op "
             "when dirty / offline / no upstream)")
    p_kbpull.add_argument(
        "--path", default=None,
        help="KB repo dir to pull (default: the resolved knowledge dir)")
    p_kbpull.set_defaults(func=staging.cmd_kb_git_pull)

    p_kbpush = kbgit_sub.add_parser(
        "push",
        help="push KB commits to upstream, auto-resolving derived-file "
             "conflicts via index rebuild (no-op when offline / no upstream)")
    p_kbpush.add_argument(
        "--path", default=None,
        help="KB repo dir to push (default: the resolved knowledge dir)")
    p_kbpush.add_argument(
        "--on-prose-conflict", dest="on_prose_conflict",
        choices=("abort", "pause"), default="abort",
        help="same-entry (non-derived) conflict disposition: 'abort' fails loud "
             "(default); 'pause' leaves the rebase in progress + lists the "
             "conflicted entry files (rc 3) for the MERGE_PROMPT")
    p_kbpush.add_argument(
        "--max-retries", dest="max_retries", type=int, default=3,
        help="bounded pull→resolve→push retries on a re-push race before "
             "failing loud (default: 3)")
    p_kbpush.set_defaults(func=cmd_kb_git_push)

    p_kbcont = kbgit_sub.add_parser(
        "merge-continue",
        help="continue a paused prose-merge rebase after the entry files were "
             "reconciled, rebuild derived files, then push")
    p_kbcont.add_argument(
        "--path", default=None,
        help="KB repo dir to continue (default: the resolved knowledge dir)")
    p_kbcont.set_defaults(func=cmd_kb_git_merge_continue)


_EXPORTS = [
    "kb_nothing_lost_gate", "kb_git_push_once", "kb_git_push_synced",
    "cmd_kb_git_push", "kb_git_merge_continue", "cmd_kb_git_merge_continue",
]

__all__ = list(_EXPORTS)
