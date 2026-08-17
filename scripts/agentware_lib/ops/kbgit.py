"""agentware_lib.ops.kbgit -- pure-git KB work-tree STATE helpers (layer: ops).

Owns: the read-only git work-tree STATE queries the conflict-safe KB sync is built on
(feature 260625-kb-git-sync) -- the three state booleans (git_is_work_tree /
git_has_upstream / git_is_clean), their combined dict shape (git_status) + its CLI
(cmd_kb_git_status), the toplevel / changed-path introspectors (git_toplevel /
git_changed_paths) and the conventional-commit scope-tag sanitizer (_sanitize_commit_tag).
Every helper takes an explicit target dir, shells the operator's `git` via
core.gitio._git, and NEVER raises on a non-repo dir (degrades to a safe default) so
tests drive throwaway temp repos and the loop never blocks.

Does NOT own: the scoped-staging engine + commit/pull workflow (agentware_lib.ops.staging,
which imports THESE helpers downward as a same-layer ops->ops edge); the low-level git
runners (core.gitio -- _git/_git_capture/_git_noninteractive_env/_git_upstream_name); and
the push/merge-continue handlers that rebuild the index (kb_nothing_lost_gate /
kb_git_push_once / kb_git_push_synced / kb_git_merge_continue + cmd_kb_git_push /
cmd_kb_git_merge_continue) -- those stay RESIDENT in scripts/agentware until
rebuild_kb / build_index_from_frontmatter leave the monolith (they forward-reference
those resident index functions; NO-FORWARD-REFERENCE).

Layer deps: ops.kbgit -> core only (core.config for the knowledge-dir resolver;
core.gitio._git). Nothing above ops imports it; ops.staging imports it (same-layer ops).

Determinism: read-only git introspection (INV-2). No new dependency (shells `git` only);
never raises on a non-repo target.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so the resident deferred
push/merge handlers + the many resident git callers (loop-sync / dream / fanout) + the
kb-git registrar resolve these as bare names, and load_cli tests reach them as CLI.<name>.
Fan-in: 10 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import os
import re
import subprocess  # noqa: F401 — resolves the by-name-imported gitio._git's LOAD_GLOBAL 'subprocess' (global-resolve gate)
import sys

import agentware_lib.core.config as config
from agentware_lib.core.gitio import _git


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


def git_status(target):
    """Report the three KB git-state booleans as a dict.

    has_upstream / is_clean are forced False on a non-work-tree so the shape is
    always the same regardless of the target.
    """
    iwt = git_is_work_tree(target)
    return {
        "is_work_tree": iwt,
        "has_upstream": git_has_upstream(target) if iwt else False,
        "is_clean": git_is_clean(target) if iwt else False,
    }


def cmd_kb_git_status(args):
    """Print the KB git-state booleans (is_work_tree / has_upstream / is_clean).

    Targets the resolved knowledge dir by default; `--path` overrides it so the
    loop (and tests) can point at a throwaway repo. NEVER mutates anything.
    """
    override = getattr(args, "path", None)
    if override:
        target = os.path.abspath(os.path.expanduser(override))
    else:
        target = config.require_knowledge_dir()
        if not target:
            return 1
    st = git_status(target)
    if getattr(args, "format", "text") == "json":
        json.dump(st, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("is_work_tree: %s" % ("yes" if st["is_work_tree"] else "no"))
        print("has_upstream: %s" % ("yes" if st["has_upstream"] else "no"))
        print("is_clean:     %s" % ("yes" if st["is_clean"] else "no"))
    return 0


def git_toplevel(target):
    """Absolute path of `target`'s git work-tree root, or None if not a repo."""
    cp = _git(target, "rev-parse", "--show-toplevel")
    if cp.returncode != 0:
        return None
    top = cp.stdout.strip()
    return os.path.realpath(top) if top else None


def git_changed_paths(target):
    """Repo-relative paths with pending changes (staged, unstaged, untracked).

    Parses `status --porcelain` (machine-stable). Rename entries (`old -> new`)
    contribute the NEW path. Returns a de-duplicated, sorted list.
    """
    cp = _git(target, "status", "--porcelain")
    if cp.returncode != 0:
        return []
    paths = set()
    for line in cp.stdout.splitlines():
        if len(line) < 4:
            continue
        p = line[3:]
        if " -> " in p:  # rename / copy: take the destination
            p = p.split(" -> ", 1)[1]
        p = p.strip().strip('"')
        if p:
            paths.add(p)
    return sorted(paths)


def _sanitize_commit_tag(tag):
    """Lowercase + restrict a commit scope tag to [a-z0-9._-] (collapsed `-`)."""
    tag = (tag or "kb").strip().lower()
    tag = re.sub(r"[^a-z0-9._-]+", "-", tag).strip("-._")
    return tag or "kb"


_EXPORTS = [
    "git_is_work_tree",
    "git_has_upstream",
    "git_is_clean",
    "git_status",
    "cmd_kb_git_status",
    "git_toplevel",
    "git_changed_paths",
    "_sanitize_commit_tag",
]

__all__ = list(_EXPORTS)


def git_tracked_paths(target, rels):
    """Subset of `rels` (repo-relative) that git currently TRACKS in `target`.

    Used by the commit path to detect DERIVED artifacts a pre-de-commit history
    still tracks, so they can be untracked (`git rm --cached`) exactly once.
    Empty on any git error. NUL-delimited so unusual paths are safe.
    """
    if not rels:
        return []
    cp = _git(target, "ls-files", "-z", "--", *rels)
    if cp.returncode != 0:
        return []
    return [p for p in cp.stdout.split("\0") if p]
