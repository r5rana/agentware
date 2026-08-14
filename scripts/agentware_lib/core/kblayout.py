"""agentware_lib.core.kblayout — KB on-disk layout primitives (layer: core).

Owns: the knowledge-dir-relative layout NAMES — the `.initialized` sentinel, the
entry document (`MAIN.md`), the derived index (`index.json`), the `.gitignore`
rules/content and the non-indexed dir tuple — plus the idempotent
`_ensure_kb_gitignore` scaffolder and the read-only `kb_pointable` target guard
that answers "is this directory safe to point the KB pointer at?".
Does NOT own: config.env values or knowledge-dir resolution (see agentware_lib.core.config),
repo-root/CLI anchors (see agentware_lib.core.paths), or the index.json SCHEMA and
its readers/writers (see agentware_lib.knowledge.index, which imports INDEX_REL
from here so the filename has exactly one definition).
Layer deps: core -> stdlib only (imports nothing else from agentware_lib).
Fan-in: 11 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

These are STABLE, feature-independent primitives with high potential fan-in:
they describe the KB's on-disk shape and are needed DOWNWARD by knowledge (index
rebuild), ops (team scaffolding), retrieval (vector-cache gitignore guard) and
features (init/config handlers, dream lock/journal). The lowest layer every one
of those can import from is core, so they live here — moving them UP into
features/configcmd would invert the dependency direction (M12). core imports only
the stdlib and nothing else from agentware_lib.
"""

import json
import os


# Knowledge-dir-relative sentinel written by onboarding once the interview
# completes; its presence is the "workspace is initialized" gate.
SENTINEL_REL = ".initialized"

# The knowledge base's entry document: the operator profile + active work that
# every session injects. A knowledge dir without it is not a knowledge base.
MAIN_REL = "MAIN.md"

# The DERIVED index. Gitignored and regenerable (`index rebuild`), so its ABSENCE
# says nothing about a knowledge dir — see kb_pointable. The single definition of
# the filename; agentware_lib.knowledge.index imports it from here.
INDEX_REL = "index.json"

GITIGNORE_REL = ".gitignore"

# Same-host index mutation lock (an fcntl.flock sidecar; see
# agentware_lib.knowledge.index.indexlock). Purely local run state — it says
# nothing about the knowledge and is meaningless on another host — so it is
# gitignored exactly like the derived cache. Defined HERE, with the rest of the
# KB layout, so the lock module and the .gitignore scaffolder share ONE
# definition of the filename and can never disagree about what to ignore.
KB_INDEX_LOCK_REL = ".index.lock"

# The KB .gitignore `init` scaffolds. `logs/` holds full session transcripts and
# `.loop/` holds ephemeral per-feature loop state (iteration counters, .done,
# live-stream.log); both are local-only run state. With KB autocommit ON by
# default, every run would otherwise commit+push them to the remote (bloat + an
# unwanted disclosure for a security-first operator). Ignoring + untracking them
# keeps autocommit to KNOWLEDGE only (learnings, index, scorecard, MAIN).
KB_GITIGNORE_MARKER = "logs/"
# Directory run-state rules get a trailing slash in the .gitignore; the lock is a
# FILE rule and must NOT (an anchored file path, not a directory) — appending
# `.index.lock/` would ignore a directory that never exists and leave the real
# sidecar tracked.
KB_GITIGNORE_DIR_RULES = ("logs", ".loop", ".cache")
# Required rules an existing KB's .gitignore must contain — compared normalized
# (stripped, trailing-slash removed) and appended if missing by _ensure_kb_gitignore.
KB_GITIGNORE_REQUIRED = KB_GITIGNORE_DIR_RULES + (KB_INDEX_LOCK_REL,)
KB_GITIGNORE_CONTENT = (
    "# agentware knowledge base — ignored paths (scaffolded by `agentware init`)\n"
    "# Session transcripts/logs are local-only: NEVER committed or pushed. KB\n"
    "# autocommit only ever versions knowledge (learnings, index, scorecard, MAIN).\n"
    "logs/\n"
    "\n"
    "# Ephemeral per-feature loop state (iteration counters, .done, live-stream.log).\n"
    "# Local-only run state — never committed/pushed, like logs/.\n"
    ".loop/\n"
    "\n"
    "# Derived, regenerable caches (Mode-B vector cache, future postings cache).\n"
    "# Rebuilt only by `index rebuild`; never source of truth — like index.json.\n"
    ".cache/\n"
    "\n"
    "# Same-host index mutation lock (fcntl.flock sidecar) — local-only.\n"
    + KB_INDEX_LOCK_REL + "\n"
    "\n"
    "# OS / editor / tooling noise\n"
    ".DS_Store\n"
    "__pycache__/\n"
)

# Non-indexed dirs `init` also scaffolds in the knowledge dir: per-feature work
# artifacts (plans/worklogs/state) and append-only session logs. These hold the
# operator's project work + audit trail OUTSIDE the orchestrator package so the
# package stays read-only across projects.
NON_INDEXED_DIRS = ("work", "logs", os.path.join("logs", "sessions"), "profiles")


def _ensure_kb_gitignore(kdir):
    """Ensure the KB `.gitignore` exists and ignores local-only run state.

    Ignored: `logs/` (session transcripts), `.loop/` (ephemeral per-feature loop
    state — iteration counters, .done, live-stream.log), `.cache/` (derived,
    regenerable caches) and the `.index.lock` mutation sidecar. All are local-only
    or regenerable and must never be committed/pushed.

    Idempotent: creates the file with the standard content if absent; if a
    `.gitignore` already exists but is missing a required rule, appends the missing
    one(s) in their CANONICAL form — dir rules get a trailing `/`, file rules do
    not — so a KB scaffolded before a rule was added self-heals. Returns
    'created' | 'appended' | 'kept'.
    """
    path = os.path.join(kdir, GITIGNORE_REL)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(KB_GITIGNORE_CONTENT)
        return "created"
    with open(path, "r", encoding="utf-8") as f:
        existing = f.read()
    # Normalize each line (strip whitespace + trailing slash) so a bare `logs/`
    # or `.loop/` rule is recognized regardless of surrounding formatting.
    present = set(ln.strip().rstrip("/") for ln in existing.splitlines())
    missing = [rule for rule in KB_GITIGNORE_REQUIRED if rule not in present]
    if not missing:
        return "kept"
    sep = "" if existing.endswith("\n") or existing == "" else "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(sep + "\n# Local-only run state (added by agentware).\n"
                + "".join(_gitignore_rule_line(rule) + "\n" for rule in missing))
    return "appended"


def _gitignore_rule_line(rule):
    """Canonical .gitignore line for a required rule.

    A DIRECTORY rule carries a trailing slash (so it can only ever match a
    directory); a FILE rule — the `.index.lock` sidecar — must not, or git would
    ignore a directory of that name and keep tracking the actual file.
    """
    return (rule + "/") if rule in KB_GITIGNORE_DIR_RULES else rule


def kb_pointable(path):
    """Lightweight 'safe to point the KB pointer at `path`?' check. (ok, reason).

    Deliberately LIGHTER than attach_conformance_report (which also requires every
    optional category dir + full index-rebuild parity — so strict it rejects even
    a live, working KB that merely lacks empty prompts/ + references/ dirs). This
    gate asserts only what pointing the config at a directory truly needs: the dir
    exists and carries the two COMMITTED structural markers, .initialized + MAIN.md.

    CRITICAL: it must NOT require index.json. index.json (like FEATURES.md and the
    per-category index.md files) is GITIGNORED/derived, so a FRESH clone — exactly
    the teammate a repoint targets — never has it until an `index rebuild` writes
    it. Requiring it here would refuse every clean checkout. If index.json happens
    to be present we sanity-check that it parses, but its ABSENCE is fine
    (regenerable); only a PRESENT-but-CORRUPT index is a positive signal of a
    broken KB.

    Read-only: parses the derived index with the stdlib rather than through the
    knowledge layer, so the guard can never rebuild, repair or otherwise mutate
    the directory it is judging (and so core keeps importing nothing above it).
    """
    if not os.path.isdir(path):
        return False, "not a directory"
    if not os.path.isfile(os.path.join(path, SENTINEL_REL)):
        return False, "no %s sentinel" % SENTINEL_REL
    if not os.path.isfile(os.path.join(path, MAIN_REL)):
        return False, "no %s" % MAIN_REL
    idx = os.path.join(path, INDEX_REL)
    if os.path.isfile(idx):
        try:
            with open(idx, "r", encoding="utf-8") as f:
                json.load(f)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return False, "%s present but unreadable: %s" % (INDEX_REL, exc)
    return True, "ok"


_EXPORTS = [
    "SENTINEL_REL",
    "MAIN_REL",
    "INDEX_REL",
    "kb_pointable",
    "GITIGNORE_REL",
    "KB_INDEX_LOCK_REL",
    "KB_GITIGNORE_MARKER",
    "KB_GITIGNORE_DIR_RULES",
    "KB_GITIGNORE_REQUIRED",
    "KB_GITIGNORE_CONTENT",
    "NON_INDEXED_DIRS",
    "_ensure_kb_gitignore",
    "_gitignore_rule_line",
]
