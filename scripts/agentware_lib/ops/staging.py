"""agentware_lib.ops.staging -- scoped-staging engine + KB-git commit/pull workflow (layer: ops).

Owns: the cross-team-write-isolation scoped-staging engine -- the
blob/artifact caps (KB_BLOB_MAX_BYTES / _KB_STAGE_REJECT_EXTS), the rel-prefix machinery
(kb_rel_prefix / _strip_rel_prefix / _is_derived_rel / _is_derived_rel_scoped), the
team-scoped stage allowlist (kb_git_stage_allowlist), the conventional-commit subject
rules (COMMIT_MSG_RE / COMMIT_SUBJECT_MAX / COMMIT_TOPICS_LIMIT / kb_changed_knowledge_topics
/ _format_topics_label / _truncate_subject) + the commit CLI (cmd_kb_git_commit), the
fast-forward pull/fetch primitives (git_pull_ff / git_fetch) + pull CLI (cmd_kb_git_pull),
the conflict-introspection helpers (git_unmerged_paths / git_rebase_in_progress /
file_has_conflict_markers), the push primitive + rejection classifier (git_push /
_push_was_rejected / RC_REPUSH_RACE), the entry-id readers the nothing-lost gate compares
(git_entry_ids_at_ref / kb_entry_ids), and the bounded-retry backoff schedule
(KB_PUSH_MAX_RETRIES / _KB_PUSH_BACKOFF_BASE / _KB_PUSH_BACKOFF_CAP / _kb_push_backoff_delay).

Does NOT own: the nothing-lost gate kb_nothing_lost_gate and the push/merge orchestration
kb_git_push_once / kb_git_push_synced / kb_git_merge_continue + cmd_kb_git_push /
cmd_kb_git_merge_continue + the `kb-git` registrar register_kb_git -- these CONSUME the
primitives above and moved to a NEW pure-sink module ops.kbsync (wave-6 seam 20; the wave-5
deferral is complete now that rebuild_kb / build_index_from_frontmatter live in
knowledge.index). They were NOT folded back INTO staging because 581 + ~380 > the 900-line
module cap. Also does NOT own the git state queries (ops.kbgit, imported downward as a
same-layer ops->ops edge) or the low-level git runners (core.gitio).

Layer deps: ops.staging -> core (config/frontmatter/orgkeys/paths/gitio) + knowledge (index)
+ a same-layer ops edge to ops.kbgit. Nothing above ops imports it.

Determinism: read-only git introspection + deterministic string ops over the staged diff
(INV-1: the retry backoff jitter is derived from attempt+pid, NO RNG). No new dependency
(shells `git` only); every helper takes an explicit target dir and degrades gracefully.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so the resident deferred
kb_nothing_lost_gate / kb_git_push_once / kb_git_push_synced / kb_git_merge_continue +
their cmd_* handlers + the resident loop-sync / org-lint callers + the kb-git registrar
resolve these as bare names, and load_cli tests reach them as CLI.<name>.
Fan-in: 8 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import os
import re
import subprocess  # noqa: F401 — resolves the by-name-imported gitio._git's LOAD_GLOBAL 'subprocess' (global-resolve gate)
import sys

import agentware_lib.core.config as config
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.core.orgkeys as orgkeys
# The staging ALLOWLIST moved to its own module when this file crossed the 600-line
# cap (a hard cap: no debt rows). Re-exported BY NAME so `cmd_kb_git_commit`, the
# entrypoint facade and every existing caller resolve exactly as before.
from agentware_lib.ops.kbstage import (   # noqa: F401  (re-export)
    KB_BLOB_MAX_BYTES,
    _KB_STAGE_REJECT_EXTS,
    _is_derived_rel,
    _is_derived_rel_scoped,
    _strip_rel_prefix,
    kb_git_stage_allowlist,
    kb_git_stage_scoped,
    kb_rel_prefix,
)
import agentware_lib.knowledge.index as index
from agentware_lib.core.gitio import _git, _git_head, _git_noninteractive_env
from agentware_lib.core.kblayout import (
    KB_DERIVED_GITIGNORE_RELS, _ensure_kb_gitignore)
from agentware_lib.ops.kbgit import git_tracked_paths
# Bound BY NAME because the ported pull path is the preserved monolith's body
# verbatim and calls these bare. The two POST-PULL RECONCILIATIONS it restores
# are the reason this port matters: without them a fast-forward that brought in
# a teammate's entries left the DERIVED index untouched, so `recall` silently
# dropped every pulled entry until someone happened to rebuild, and a pulled
# SKILL never reached the pending-approval roster at all. Both are best-effort
# and neither may ever break a pull.
from agentware_lib.core.config import require_knowledge_dir
from agentware_lib.knowledge.index import (
    KNOWLEDGE_SUBDIRS, build_index_from_frontmatter, rebuild_kb,
    _is_sync_candidate)
from agentware_lib.features.skilllock import (
    _skill_cache_dir, load_skill_lock, save_skill_lock)
from agentware_lib.core.paths import REPO_ROOT
# The PULL path moved to ops.kbpull when restoring its post-pull reconciliations
# pushed this file past the hard 600-line cap. Re-exported BY NAME so every
# pre-split caller and monkeypatch target (`staging.cmd_kb_git_pull`) resolves
# unchanged.
from agentware_lib.ops.kbpull import (   # noqa: F401  (re-export)
    cmd_kb_git_pull, git_fetch, git_pull_ff, _kb_entry_files_changed,
    _rebuild_kb_after_pull, _skill_reconcile_after_pull)
from agentware_lib.ops.kbgit import (
    _sanitize_commit_tag, git_changed_paths, git_has_upstream, git_is_clean,
    git_is_work_tree, git_toplevel)


# Conventional-commit subject the auto-commit must satisfy: feat|chore(scope): msg
COMMIT_MSG_RE = re.compile(r"^(feat|chore)\([a-z0-9._-]+\): .+")

# A commit subject stays one sane line; cap its total length (C-1) and truncate
# with an ellipsis rather than spill the dir list / topic list across the line.
COMMIT_SUBJECT_MAX = 100
# How many knowledge topics to name before collapsing the tail to "+N more".
COMMIT_TOPICS_LIMIT = 5


def kb_changed_knowledge_topics(changed):
    """Knowledge entry 'topics' (basenames) among the changed paths.

    `learnings/<topic>.md` -> `<topic>`; `skills/<topic>/...` or
    `skills/<topic>.md` -> `<topic>`. De-duplicated, sorted, deterministic — used
    to make the autocommit subject name WHAT knowledge changed instead of the
    generic top-level dir list. Pure string ops over the staged diff (no LLM /
    network), so the moat is preserved (feature 260625-autocommit-message-fix).
    """
    seen = set()
    for p in changed:
        parts = p.split("/")
        if len(parts) < 2:
            continue  # top-level file (MAIN.md, index.json) — not a knowledge entry
        topic = None
        if parts[0] == "learnings" and parts[-1].endswith(".md"):
            topic = parts[-1][:-3]
        elif parts[0] == "skills":
            seg = parts[1]
            topic = seg[:-3] if seg.endswith(".md") else seg
        if topic:
            seen.add(topic)
    return sorted(seen)


def _format_topics_label(topics, limit=COMMIT_TOPICS_LIMIT):
    """`learnings: a, b, c` (first `limit`, tail collapsed to `+N more`)."""
    shown = topics[:limit]
    label = ", ".join(shown)
    extra = len(topics) - len(shown)
    if extra > 0:
        label += ", +%d more" % extra
    return "learnings: %s" % label


def _truncate_subject(subject, limit=COMMIT_SUBJECT_MAX):
    """Clamp a commit subject to one sane line ≤ `limit` chars (C-1)."""
    subject = subject.splitlines()[0] if subject else subject
    if len(subject) > limit:
        subject = subject[:limit - 3].rstrip().rstrip(",") + "..."
    return subject


def _decommit_derived(target):
    """Untrack derived artifacts a pre-de-commit history still tracks.

    `git rm --cached` stages the removal but PRESERVES the local file, and the
    derived rules are ensured in .gitignore FIRST so the staging that follows
    cannot re-add them. Returns the paths untracked (empty when already clean, so
    an already-migrated tree is a no-op). A fresh clone recovers the files via the
    load_index/pull auto-rebuild, so removing them from history never bricks.

    UNION NOTE (task 54): this half comes from the preserved monolith and the
    SCOPED-ALLOWLIST staging in the caller comes from the donor — neither tree had
    both. They are complementary, not alternative: the allowlist stops a NEW
    derived file from being staged, this stops an OLD one history already tracks
    from staying tracked forever. It must run BEFORE the caller's clean-tree early
    return, because the derived files are typically TRACKED BUT UNCHANGED — so
    `git_changed_paths` reports nothing and returns on exactly the tree this
    exists to migrate.
    """
    tracked_derived = git_tracked_paths(target, KB_DERIVED_GITIGNORE_RELS)
    if tracked_derived:
        _ensure_kb_gitignore(target)
        _git(target, "rm", "--cached", "--quiet", "--ignore-unmatch",
             *tracked_derived)
    return tracked_derived


def cmd_kb_git_commit(args):
    """Stage + commit ONLY the knowledge-base repo as one conventional commit.

    Deterministic, opt-in commit discipline (feature 260625-kb-git-sync, Task
    2.1). Scope is the KB repo ONLY (C-3): the helper operates with `git -C
    <target>` against a SEPARATE git repo and refuses if the target's work-tree
    root resolves to the agentware package repo, so the project/package can
    never be staged. No-ops (rc 0) on a non-tracked KB (C-4) or a clean tree.

    Message format is ENFORCED: `feat|chore(<tag>): <summary>`. The tag is
    derived deterministically (caller-supplied --tag, sanitized); when --message
    is omitted a deterministic summary of the changed top-level dirs is built.
    """
    override = getattr(args, "path", None)
    if override:
        target = os.path.abspath(os.path.expanduser(override))
    else:
        target = config.require_knowledge_dir()
        if not target:
            return 1

    # C-4: degrade to local-only (no failure) when the KB is not a git repo.
    if not git_is_work_tree(target):
        print("kb-git commit: %s is not a git work tree — skipping commit "
              "(local-only)." % target)
        return 0

    # C-3 scope guard: NEVER commit the agentware package repo. The KB is a
    # separate repo; if the target somehow resolves to this package's work-tree
    # root, refuse loudly rather than stage package/project files.
    top = git_toplevel(target)
    pkg_top = git_toplevel(REPO_ROOT)
    if top and pkg_top and top == pkg_top:
        sys.stderr.write(
            "kb-git commit: refusing — target resolves to the agentware "
            "package repo (%s); commits are scoped to the KB repo only "
            "(C-3).\n" % top)
        return 1

    tracked_derived = _decommit_derived(target)

    changed = git_changed_paths(target)
    if not changed:
        print("kb-git commit: nothing to commit — work tree clean.")
        return 0

    tag = _sanitize_commit_tag(getattr(args, "tag", None))
    ctype = getattr(args, "type", None) or "chore"
    message = getattr(args, "message", None)
    # Name WHAT knowledge changed (deterministic, from the staged diff). Used as
    # the default summary, or appended as a compact suffix to a caller --message.
    topics = kb_changed_knowledge_topics(changed)
    if not message:
        if topics:
            # Default summary describes the changed knowledge entries...
            message = _format_topics_label(topics)
        else:
            # ...else fall back to the changed top-level dir list (C-2, never empty).
            tops = sorted({p.split("/", 1)[0] for p in changed})
            n = len(changed)
            message = "sync %s (%d file%s)" % (
                ", ".join(tops), n, "" if n == 1 else "s")
    elif topics:
        # A caller-supplied title (e.g. the plan title from agentware.sh) gets a
        # compact knowledge suffix so the subject names BOTH feature + knowledge.
        message = "%s — %s" % (message, _format_topics_label(topics))

    subject = _truncate_subject("%s(%s): %s" % (ctype, tag, message))
    if not COMMIT_MSG_RE.match(subject):
        sys.stderr.write(
            "kb-git commit: refusing — message '%s' does not match "
            "feat|chore(<tag>): <summary>.\n" % subject)
        return 1


    # Stage via the SCOPED ALLOWLIST — never a blanket `git add -A`.
    # Team-scoped (refuses cross-team paths), drops derived/artifact/oversized blobs,
    # allows the committed sink/data files. rel_prefix='' (flat KB) => stages the
    # same knowledge files as before (byte-compatible), just guarded.
    rel_prefix = kb_rel_prefix(target)
    allow, rejected = kb_git_stage_allowlist(target, rel_prefix)
    for path, reason in rejected:
        sys.stderr.write("kb-git commit: skipping %s — %s\n" % (path, reason))
    if not allow and not tracked_derived:
        print("kb-git commit: nothing to stage after the scoped allowlist "
              "(only derived/artifact/out-of-scope changes).")
        return 0
    # `tracked_derived` guards the early return because the de-commit above has
    # ALREADY staged `git rm --cached` for those paths. Returning here on an empty
    # allowlist would abandon that staged removal — the untracking would be redone
    # and re-abandoned on every subsequent commit, so the derived artifacts would
    # stay tracked forever while each run reported "nothing to stage". The two
    # halves are unioned from different trees, and this is the seam between them.
    # Stage exactly the allowlisted paths. `allow` holds REPO-ROOT-relative paths
    # (from `git status --porcelain`), but `git -C <target> add` resolves pathspecs
    # relative to <target> (the team subdir in org mode) — so we MUST convert to
    # ABSOLUTE paths via the git toplevel, else org-mode staging fails with a
    # `pathspec 'teams/<t>/teams/<t>/…' did not match` error (F1). Absolute paths
    # are unambiguous regardless of git's cwd; flat KBs (toplevel==target) are
    # unaffected.
    top = git_toplevel(target) or target
    abs_paths = [os.path.join(top, p) for p in allow]
    add = _git(target, "add", "--", *abs_paths)
    if add.returncode != 0:
        sys.stderr.write("kb-git commit: git add failed: %s\n" % add.stderr.strip())
        return 1

    commit = _git(target, "commit", "-m", subject)
    if commit.returncode != 0:
        sys.stderr.write("kb-git commit: git commit failed: %s\n"
                         % (commit.stderr.strip() or commit.stdout.strip()))
        return 1

    head = _git(target, "rev-parse", "HEAD").stdout.strip()
    print("kb-git commit: %s — %s" % (head[:9], subject))
    return 0














def git_unmerged_paths(target):
    """Repo-relative paths currently in a conflicted (unmerged) state, sorted."""
    cp = _git(target, "diff", "--name-only", "--diff-filter=U")
    if cp.returncode != 0:
        return []
    return sorted({l.strip().strip('"') for l in cp.stdout.splitlines()
                   if l.strip()})


def git_rebase_in_progress(target):
    """True iff a rebase is currently paused/in progress in `target`.

    Checks both git rebase backends (merge → `rebase-merge`, am → `rebase-apply`)
    via `rev-parse --git-path`, which respects worktrees. Used by the prose-merge
    path (Phase 5): `kb-git push --on-prose-conflict pause` leaves a rebase in
    progress for the MERGE_PROMPT, and `kb-git merge-continue` only proceeds when
    one is actually in progress.
    """
    for marker in ("rebase-merge", "rebase-apply"):
        cp = _git(target, "rev-parse", "--git-path", marker)
        if cp.returncode != 0:
            continue
        p = cp.stdout.strip()
        if not p:
            continue
        if not os.path.isabs(p):
            p = os.path.join(target, p)
        if os.path.isdir(p):
            return True
    return False


def file_has_conflict_markers(path):
    """True iff `path` still contains git conflict markers (`<<<<<<<`/`>>>>>>>`).

    Only the 7-angle-bracket markers are checked: they never occur in normal
    prose/markdown, so this avoids false positives on a `=======` setext heading
    or horizontal rule. The nothing-lost prose merge (Phase 5) refuses to continue
    a rebase while any reconciled entry file still carries an unresolved marker.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("<<<<<<<") or line.startswith(">>>>>>>"):
                    return True
    except OSError:
        return False
    return False


def git_push(target):
    """`git push` to the upstream; non-interactive. Returns the CompletedProcess."""
    return _git(target, "push", env=_git_noninteractive_env())


def _push_was_rejected(cp):
    """True iff a non-zero push failed because the upstream moved (non-fast-forward).

    Distinguishes a resolvable rejection (rebase + retry) from an offline / auth /
    network failure (graceful skip, C-4). Matches git's stable wording.
    """
    blob = ((cp.stderr or "") + (cp.stdout or "")).lower()
    return ("rejected" in blob or "non-fast-forward" in blob
            or "fetch first" in blob or "updates were rejected" in blob)


# Distinct return code for a re-push RACE (upstream moved again between our
# resolve and our push). It is recoverable — `kb_git_push_synced` retries the
# whole pull→resolve→push up to a bounded cap (C-2) — so it is kept separate from
# a fail-loud rc 1. Never surfaced to a CLI caller: the wrapper maps an exhausted
# race to a plain rc 1.
RC_REPUSH_RACE = 4


def git_entry_ids_at_ref(target, ref, rel_prefix=None):
    """Set of entry frontmatter IDs present at a git ref's tree, or None.

    Lists the tree at `ref` WITHOUT a checkout (`git ls-tree -r`), keeps only
    indexable entry files (the same `<sub>/…md` candidate rule as
    `scan_entry_files`, minus the `<sub>/index.md` rosters), reads each blob via
    `git show <ref>:<path>`, and parses its frontmatter `id`. Returns None when
    `ref` does not resolve, so the nothing-lost gate can SKIP an unavailable
    parent (e.g. `@{upstream}` during a detached-HEAD rebase) rather than
    mis-count it as empty. Pure git — no new dependency (C-5).

    rel_prefix: when the KB is a repo SUBDIR (org mode), the tree
    lists repo-relative paths; strip rel_prefix so the flat `<sub>/…` candidate
    rule matches, and count ONLY entries under this KB's prefix. rel_prefix='' /
    None (flat KB) → byte-identical to before.
    """
    if rel_prefix is None:
        rel_prefix = kb_rel_prefix(target)
    ls = _git(target, "ls-tree", "-r", "--name-only", ref)
    if ls.returncode != 0:
        return None
    ids = set()
    for rel in ls.stdout.splitlines():
        rel = rel.strip().strip('"')
        if not rel.endswith(".md"):
            continue
        inner = _strip_rel_prefix(rel, rel_prefix)
        if inner is None:  # outside this KB's prefix (another team / shared)
            continue
        parts = inner.split("/")
        if parts[0] not in index.KNOWLEDGE_SUBDIRS:
            continue
        if not index._is_sync_candidate(inner):
            continue
        show = _git(target, "show", "%s:%s" % (ref, rel))
        if show.returncode != 0:
            continue
        fields, _body = frontmatter.split_frontmatter(show.stdout)
        if fields and fields.get("id"):
            ids.add(fields["id"])
    return ids


def kb_entry_ids(target):
    """Set of entry frontmatter IDs in the WORKING TREE at `target`.

    The same enumeration the index derives from (`scan_entry_files` +
    `read_entry_frontmatter`); used by the nothing-lost gate to compute the
    MERGED entry-ID set after a resolution.
    """
    ids = set()
    for rel in index.scan_entry_files(target):
        fm = frontmatter.read_entry_frontmatter(os.path.join(target, rel))
        if fm.get("id"):
            ids.add(fm["id"])
    return ids


# Org-scale push retry: a shared knowledge repo behind a server-side write lock
# strands ~40% of pushes at ~100 concurrent writers (measured on a centrally
# hosted git server). Raise the retry cap and add exponential backoff + jitter so
# a lost fast-forward race retries instead of failing the run. Jitter is
# DETERMINISTIC (attempt + pid derived) — no RNG (INV-1 forbids Math.random in the
# moat; this is off the recall path but we keep it reproducible). Cap ~8-10 models
# peak strand 40% -> < 10%.
KB_PUSH_MAX_RETRIES = 8
_KB_PUSH_BACKOFF_BASE = 0.2   # seconds
_KB_PUSH_BACKOFF_CAP = 5.0    # seconds


def _kb_push_backoff_delay(attempt):
    """Deterministic exponential backoff + jitter for push retry `attempt` (1-based)."""
    base = min(_KB_PUSH_BACKOFF_BASE * (2 ** (attempt - 1)), _KB_PUSH_BACKOFF_CAP)
    # Deterministic jitter in [0, base/2): derived from attempt + pid, no RNG.
    jitter = (base / 2.0) * (((attempt * 2654435761 + os.getpid()) % 1000) / 1000.0)
    return base + jitter


_EXPORTS = [
    "KB_BLOB_MAX_BYTES", "_KB_STAGE_REJECT_EXTS", "kb_rel_prefix", "_strip_rel_prefix",
    "_is_derived_rel_scoped", "kb_git_stage_allowlist", "kb_git_stage_scoped",
    "COMMIT_MSG_RE",
    "COMMIT_SUBJECT_MAX", "COMMIT_TOPICS_LIMIT", "kb_changed_knowledge_topics",
    "_format_topics_label", "_truncate_subject", "cmd_kb_git_commit", "git_pull_ff",
    "git_fetch", "cmd_kb_git_pull", "_is_derived_rel", "git_unmerged_paths",
    "git_rebase_in_progress", "file_has_conflict_markers", "git_push", "_push_was_rejected",
    "RC_REPUSH_RACE", "git_entry_ids_at_ref", "kb_entry_ids", "KB_PUSH_MAX_RETRIES",
    "_KB_PUSH_BACKOFF_BASE", "_KB_PUSH_BACKOFF_CAP", "_kb_push_backoff_delay",
]

__all__ = list(_EXPORTS)
