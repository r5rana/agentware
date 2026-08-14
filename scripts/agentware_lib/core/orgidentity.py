"""agentware_lib.core.orgidentity — project identity from marker files (layer: core).

Owns: ALL project-marker knowledge — the STANDARD name-declaring marker table
(PROJECT_MARKERS), the presence-only markers that declare no machine-readable
name (PROJECT_PRESENCE_MARKERS), their derived union (PROJECT_ROOT_MARKERS), the
bare name-file escape hatch (PROJECT_NAME_FILE), the bounded marker read and the
whole-token matcher — i.e. the two pure filesystem questions "does this DIRECTORY
declare the project <name>?" (_dir_declares_project, IDENTITY) and "is this
directory a project root at all?" (_dir_has_project_marker, LOCATION).
Does NOT own: WHERE the project root is relative to a starting directory — that
walk, its VCS-root half and their fallbacks are
agentware_lib.core.projectcontext's (it imports this module); nor which name to
ask about — that is a CONFIG question
(AGENTWARE_ORG_PROJECT and its deprecated spelling, resolved in
agentware_lib.core.config, which imports this module and raises the identity
error); nor the org key NAMES (agentware_lib.core.orgkeys).
Layer deps: core -> stdlib only (imports nothing else from agentware_lib).
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

WHY THIS IS ITS OWN MODULE, and why it is marker-driven

The org-root identity anchor is a source-of-truth guard: with a project name
configured, a resolved org root that is not that project is REFUSED, so a stray
path left in config.env (a /tmp scratch, a sibling checkout) fails loudly instead
of silently becoming the live knowledge base. The guard is worth keeping. Its
original implementation was not: it read ONE build system's manifest and matched
ONE declaration syntax, which is environment-specific knowledge a vendor-neutral,
language-agnostic package must not carry (R-LOC-03).

So identity is asked of the markers a project ALREADY has — pyproject.toml,
Cargo.toml, setup.cfg, package.json, composer.json, go.mod, pom.xml, the Gradle
settings files — plus PROJECT_NAME_FILE, a one-line file any other layout
(including a build system this package deliberately does not know about) can drop
in to declare itself.

The match is a WHOLE TOKEN, never a substring, and every template in the table
anchors the name on both sides. That is the entire security value of the guard: a
sibling checkout named `<Name>V2` or `<Name>-Tests` sitting where the org root
should be is exactly the mis-pointing being refused, and a prefix match would
accept it. tests/_orgidentity_gate.py probes every marker in this table for that
property, in both directions.

Pure and dependency-free on purpose: no config, no env, no I/O beyond a bounded
read of one file per marker. That is what lets the resolver call it on a path
every command walks without inverting the layer graph.
"""

import os
import re

#: A directory holding a name file whose ENTIRE content is the project name. The
#: generic escape hatch: any build system whose own manifest this vendor-neutral
#: package does not (and must not) know how to parse can still anchor its
#: identity by dropping one line in this file. R-LOC-03 — the framework names no
#: vendor.
PROJECT_NAME_FILE = ".agentware-project"

#: STANDARD project marker files, in probe order: (filename, regex template).
#: Each template takes ONE `%s` — the re.escape'd project name — and MUST anchor
#: the name on both sides (a closing quote, tag or end-of-line) so the match is a
#: WHOLE TOKEN and never a prefix: a marker declaring `FooV2` or `Foo-Tests` must
#: NOT satisfy the name `Foo`.
PROJECT_MARKERS = (
    ("pyproject.toml", r"""(?m)^\s*name\s*=\s*["']%s["']\s*$"""),
    ("Cargo.toml", r"""(?m)^\s*name\s*=\s*["']%s["']\s*$"""),
    ("setup.cfg", r"(?m)^\s*name\s*=\s*%s\s*$"),
    ("package.json", r'(?m)^\s*"name"\s*:\s*"(?:@[^"/]+/)?%s"\s*,?\s*$'),
    ("composer.json", r'(?m)^\s*"name"\s*:\s*"(?:[^"/]+/)?%s"\s*,?\s*$'),
    ("go.mod", r"(?m)^\s*module\s+(?:\S+/)?%s\s*$"),
    ("pom.xml", r"(?m)^\s*<artifactId>\s*%s\s*</artifactId>"),
    ("settings.gradle", r"""(?m)^\s*rootProject\.name\s*=\s*["']%s["']\s*$"""),
    ("settings.gradle.kts", r"""(?m)^\s*rootProject\.name\s*=\s*["']%s["']\s*$"""),
    (PROJECT_NAME_FILE, r"(?m)^\s*%s\s*$"),
)

#: Project-root markers that declare no MACHINE-READABLE whole-token name, so
#: identity must never ask them: `setup.py` puts the name inside a `setup()`
#: call (an expression, not a declaration) and a Gradle BUILD script names
#: nothing at all — the name lives in `settings.gradle`, which is in the table
#: above. They are still definitive answers to the other question, WHERE the
#: project root is: a directory carrying one IS a project root. Kept in this
#: module because it is marker knowledge, and kept SEPARATE from the table above
#: because a row there is a promise that a name can be matched as a whole token,
#: which these cannot keep. Same ALLOWED-manifest rule applies (R-LOC-03): a
#: vendor's own marker file belongs in neither table — such a layout declares
#: itself with PROJECT_NAME_FILE.
PROJECT_PRESENCE_MARKERS = ("setup.py", "build.gradle", "build.gradle.kts")

#: Every filename that marks a project root, DERIVED so the two tables above stay
#: the single source (adding a marker to either is the one edit needed). Consumed
#: by the project-context resolver in agentware_lib.core.projectcontext.
PROJECT_ROOT_MARKERS = tuple(
    marker for marker, _template in PROJECT_MARKERS) + PROJECT_PRESENCE_MARKERS

#: Marker files are declarations, not documents: the name is at the top. Bounded
#: so the guard stays cheap on a resolver path every command walks.
PROJECT_MARKER_READ_BYTES = 8192

_PROJECT_MARKER_RE_CACHE = {}


def _project_marker_re(marker, template, name):
    """Compiled whole-token matcher for `name` in `marker`, memoised per pair."""
    key = (marker, name)
    rx = _PROJECT_MARKER_RE_CACHE.get(key)
    if rx is None:
        rx = re.compile(template % re.escape(name))
        _PROJECT_MARKER_RE_CACHE[key] = rx
    return rx


def _read_marker(path, marker):
    """The head of `path/marker`, or None when it is missing or unreadable."""
    try:
        with open(os.path.join(path, marker), "r", encoding="utf-8",
                  errors="replace") as f:
            return f.read(PROJECT_MARKER_READ_BYTES)
    except OSError:
        return None


def _dir_declares_project(path, name):
    """True iff `path` declares the project `name` in a STANDARD marker file —
    matched as a WHOLE token. Cheap, read-only, tolerant (no marker → False).

    Marker-driven rather than tied to one build system: whichever of
    PROJECT_MARKERS the directory happens to carry decides, so the same guard
    works in a Python, Node, Rust, Go, Java, PHP or Gradle checkout, and any
    other layout can declare itself in PROJECT_NAME_FILE."""
    if not path or not name:
        return False
    for marker, template in PROJECT_MARKERS:
        head = _read_marker(path, marker)
        if head is not None and _project_marker_re(marker, template,
                                                   name).search(head):
            return True
    return False


def _dir_has_project_marker(path):
    """True iff `path` carries ANY project-root marker (PROJECT_ROOT_MARKERS).

    Existence only — the LOCATION question, deliberately separate from the
    IDENTITY question `_dir_declares_project` answers. No file is read and no
    name is matched, so a directory whose manifest declares a different project
    is still a project root, which is what a monorepo needs.

    NO layout constraint of any kind: the directory does not have to sit under a
    parent of a particular name. A resolver that required one answered correctly
    in exactly one repository layout and silently answered "nowhere" in every
    other."""
    if not path:
        return False
    for marker in PROJECT_ROOT_MARKERS:
        if os.path.exists(os.path.join(path, marker)):
            return True
    return False


# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "PROJECT_NAME_FILE",
    "PROJECT_MARKERS",
    "PROJECT_PRESENCE_MARKERS",
    "PROJECT_ROOT_MARKERS",
    "PROJECT_MARKER_READ_BYTES",
    "_PROJECT_MARKER_RE_CACHE",
    "_project_marker_re",
    "_read_marker",
    "_dir_declares_project",
    "_dir_has_project_marker",
]
