"""agentware_lib.core.projectcontext — "where am I?" (layer: core).

Owns: the marker-driven project/repo CONTEXT resolver — the VCS marker set
(VCS_MARKERS), the repo-root probe (_dir_is_vcs_root), the upward walk with its
two fallbacks (_resolve_context) and the `whereami` payload assembler with its
deprecated key mirrors (WHEREAMI_KEY_MIRRORS / whereami_payload).
Does NOT own: WHICH files mark a project — that is marker knowledge and lives in
agentware_lib.core.orgidentity (PROJECT_ROOT_MARKERS / _dir_has_project_marker),
which this module imports; nor the verb surface (agentware_lib.cli owns
cmd_whereami / register_whereami and does the printing).
Layer deps: core -> core.orgidentity (SAME layer, acyclic — orgidentity imports
nothing from agentware_lib) + stdlib.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

WHY THIS IS MARKER-DRIVEN, AND WHAT WAS WRONG BEFORE

The resolver answers one question every session-start hook and every
steering-capture auto-scope asks: given a directory, which project am I in and
which checkout is it part of? The implementation this replaces answered it with
ONE build system's layout — a directory carrying that vendor's manifest file
whose PARENT was named `src`. Two hard constraints, both invisible to the caller:

  * the vendor marker: no other ecosystem's project could ever resolve; and
  * the SRC-PARENT constraint: even that vendor's own package failed to resolve
    if it sat anywhere else, and every repository whose projects are not one
    directory below a `src/` directory resolved to nothing at all.

Neither failure is loud. `whereami` exits 0 with empty fields, the hook injects
no context banner, and the agent silently starts work with no idea which checkout
it is in. So the resolution is now MARKER-DRIVEN and layout-free: walk up from
the starting directory and take the nearest ancestor carrying any standard
project marker, with no requirement whatsoever on what its parent is called.

TWO MARKERS, TWO ANSWERS

A project root and a repository root are different facts, and in a monorepo they
are different directories. The walk resolves them INDEPENDENTLY in one pass —
the nearest project-marked ancestor is the project, the nearest VCS-marked
ancestor is the checkout — and stops as soon as it has both. The fallbacks then
make sure a consumer always gets a usable pair: a bare checkout with no manifest
still reports a project (the repo doubles as one) and a project vendored outside
any checkout still reports a repo_root (itself). Missing both is reported as
empty strings with exit 0, never as an error: "not in a project" is a legitimate
answer for a directory, not a failure of the resolver.

TOLERANT BY CONSTRUCTION: no stat of a nonexistent path raises, no symlink loop
can run forever (the walk terminates at the filesystem root, which
`os.path.dirname` reaches in a bounded number of steps), and nothing here writes.
"""

import os

from . import orgidentity

#: Directories that mark a REPOSITORY root, in probe order. A checkout is the
#: outer container: a monorepo holds many project roots inside one of these.
VCS_MARKERS = (".git", ".hg", ".svn")

#: Deprecated `whereami` payload keys -> the canonical key each mirrors, kept so
#: a consumer written against the older vocabulary keeps resolving. The canonical
#: names are the tree's own (repo_root / project_name / project_dir); the mirrors
#: are the vendor-workspace vocabulary the merged surface must not simply drop.
#: Ordered, and rendered AFTER the canonical block, so JSON output stays stable.
WHEREAMI_KEY_MIRRORS = (
    ("workspace_root", "repo_root"),
    ("package_name", "project_name"),
    ("package_dir", "project_dir"),
)


def _dir_is_vcs_root(path):
    """True iff `path` carries any VCS_MARKERS entry. Existence only, no I/O."""
    if not path:
        return False
    for marker in VCS_MARKERS:
        if os.path.exists(os.path.join(path, marker)):
            return True
    return False


def _resolve_context(start_dir):
    """Pure project/repo-context resolver (build-system-neutral). Walks up from
    `start_dir` and returns {invoked_from, repo_root, project_name, project_dir}.

    Shared by `cmd_whereami` and the steering-capture verb's auto-scope. Never
    raises and never writes: an unreadable, nonexistent or rootless path simply
    resolves to empty strings.
    """
    start_dir = os.path.abspath(os.path.expanduser(start_dir))

    result = {
        "invoked_from": start_dir,
        "repo_root": "",
        "project_name": "",
        "project_dir": "",
    }

    # Portable, language-neutral resolution: walk up from start_dir and match the
    # nearest ancestor that carries a conventional project-root marker. PROJECT
    # markers pin the enclosing project/package for any common ecosystem; VCS
    # markers pin the repository root (which may sit above the project in a
    # monorepo). No build-system-specific directory layout is assumed — in
    # particular the project directory's PARENT is never constrained.
    cur = start_dir
    while True:
        if not result["project_dir"] and orgidentity._dir_has_project_marker(cur):
            result["project_dir"] = cur
            result["project_name"] = os.path.basename(cur)
        if not result["repo_root"] and _dir_is_vcs_root(cur):
            result["repo_root"] = cur
        parent = os.path.dirname(cur)
        if (result["project_dir"] and result["repo_root"]) or cur == parent:
            # Both resolved, or reached the filesystem root.
            break
        cur = parent

    # Fallbacks so consumers always get a usable root: a bare VCS checkout with no
    # ecosystem manifest still resolves (the repo root doubles as the project),
    # and a project with no VCS marker still reports a repo_root.
    if result["repo_root"] and not result["project_dir"]:
        result["project_dir"] = result["repo_root"]
        result["project_name"] = os.path.basename(result["repo_root"])
    if result["project_dir"] and not result["repo_root"]:
        result["repo_root"] = result["project_dir"]

    return result


def whereami_payload(start_dir):
    """The `whereami` JSON payload: the canonical context plus the deprecated
    key mirrors, so both vocabularies read the same resolution.

    One resolver, one answer, rendered under every name it has ever had — a
    mirror can never disagree with the field it mirrors because it is copied
    from it here.
    """
    payload = _resolve_context(start_dir)
    for mirror, canonical in WHEREAMI_KEY_MIRRORS:
        payload[mirror] = payload[canonical]
    return payload


# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "VCS_MARKERS",
    "WHEREAMI_KEY_MIRRORS",
    "_dir_is_vcs_root",
    "_resolve_context",
    "whereami_payload",
]
