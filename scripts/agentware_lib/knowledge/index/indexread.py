"""agentware_lib.knowledge.index.indexread — KB-layout constants + index READ primitives (layer: knowledge).

Owns: the KB-layout constants (LEARNINGS_REL/DECISIONS_REL/FEATURES_REL,
KNOWLEDGE_SUBDIRS, SKILLS_PROPOSED_DIR, ENTRY_SCAN_SUBDIRS, CATEGORIES,
SYNC_EXCLUDE_BASENAMES, SYNC_CATEGORY_RULES, REQUIRED_FIELDS), the
knowledge-dir-relative path helpers (index_path/features_path/learnings_dir/
decisions_dir), the READ-side index primitives (load_index, entry_last_verified,
indexed_paths) and the tag-string parser (parse_tags). Nothing here writes to disk.
Does NOT own: the index WRITE core (save_index/_do_add/_entry_key/
insert_entry_sorted/add_tags_to_map/remove_tags_from_map) ->
agentware_lib.knowledge.index.indexwrite; validate_index + the knowledge
validators + the derived-roster rendering/injection lint ->
agentware_lib.knowledge.index.indexvalidate; the entry-file scan/inference + the
KB-rebuild orchestrators -> agentware_lib.knowledge.index.indexrebuild; the
cmd_index_* handlers -> agentware_lib.features.indexcmd.
Layer deps: knowledge -> core (module-level kblayout for INDEX_REL only), all
DOWNWARD. NO MODULE-LEVEL upward or cyclic import; this is the sink of the index
package (its siblings import IT, never the reverse). ONE deliberate exception,
FUNCTION-LOCAL: `_try_rebuild_missing_index` imports the `indexrebuild` sibling at
call time, because auto-recovering a missing/corrupt derived index is inherently a
read path that must reach the rebuilder. Carried lazily so the module-load graph
stays acyclic and the edge only exists on the recovery path.
Fan-in: 3 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

These are the small, high-fan-in index primitives the rest of the KB system reads.
They carry hard BYTE CONTRACTS: the canonical (2-space-indent, trailing-newline)
index.json layout its writer sibling emits is read back here, and load_index's
error strings are surfaced verbatim by `index validate` / `audit`, so their bodies
are moved here VERBATIM.
"""

import json
import os

from agentware_lib.core.kblayout import INDEX_REL


# --- KB layout constants -----------------------------------------------------
# Knowledge-dir-relative locations (joined with the resolved knowledge dir).
# INDEX_REL is imported from core.kblayout (the single definition of the derived
# index's filename, shared with the kb_pointable target guard) and re-exported
# by the index package aggregator by name, so index.INDEX_REL keeps resolving for
# every existing caller.
LEARNINGS_REL = "learnings"
DECISIONS_REL = "decisions"
FEATURES_REL = "FEATURES.md"

# Indexed knowledge categories scaffolded by `init` inside the knowledge dir.
# Defined in core.kblayout (a layout fact the derived-artifact gitignore list is
# built from, and core cannot import upward) and re-exported here BY NAME so
# every `index.KNOWLEDGE_SUBDIRS` caller keeps resolving unchanged.
from agentware_lib.core.kblayout import (   # noqa: F401  (re-export)
    KNOWLEDGE_SUBDIRS)

# Staging tier for agent-PROPOSED skills (feature 260722, Part 2). Co-located
# here because ENTRY_SCAN_SUBDIRS is built from it; the skills-proposal code
# reads it via the facade re-export until the skills wave. A proposal is a
# recall-only skill candidate that graduates to an always-on `skills/` skill
# ONLY on human approval (`skill promote`). It is deliberately a SIBLING folder,
# NOT a prefix inside `skills/`, so it is invisible-by-construction to both the
# roster generator and the SessionStart injection hook (they scan `skills/` only).
SKILLS_PROPOSED_DIR = "skills-proposed"

# Subdirs the indexer SCANS for entry files (`index rebuild` / `index sync`).
# This is a SUPERSET of KNOWLEDGE_SUBDIRS: proposals must be recall-visible (so
# they are indexed), but they must NEVER render into a roster / injection set /
# CATEGORIES / attach-conformance requirement — so `skills-proposed/` is added
# HERE only, never to KNOWLEDGE_SUBDIRS. Every scan is `isdir()`-gated, so a flat
# or existing KB without this dir rebuilds byte-identically (INV-1). `skills-proposed/`
# paths infer category `references` (not `skills`) via `_infer_category`.
ENTRY_SCAN_SUBDIRS = KNOWLEDGE_SUBDIRS + (SKILLS_PROPOSED_DIR,)

# Single source of truth for knowledge categories. The index holds the USER's
# knowledge only — framework artifacts (skills, steering, the toolkit) ship with
# the repo and are documented in README/FEATURES, not in the per-user index.
CATEGORIES = (
    "learnings",
    "decisions",
    "projects",
    "configurations",
    "prompts",
    "references",
    "skills",
)

# Files that are intentionally NEVER indexed (structural / generated). `index
# sync` must not report these as gaps, otherwise the report is pure noise.
SYNC_EXCLUDE_BASENAMES = ("MAIN.md", "FEATURES.md", "README.md")

# Maps a knowledge subdir to the canonical category + id prefix `index sync
# --fix` assigns to a newly discovered file.
SYNC_CATEGORY_RULES = (
    ("learnings/", "learnings", "learn"),
    ("decisions/", "decisions", "decide"),
    ("projects/", "projects", "project"),
    ("configurations/", "configurations", "config"),
    ("prompts/", "prompts", "prompt"),
    ("references/", "references", "ref"),
    ("skills/", "skills", "skill"),
)

# Required fields every index entry must carry.
REQUIRED_FIELDS = ("id", "title", "category", "path", "tags", "summary")


# --- knowledge-dir-relative path helpers -------------------------------------
def index_path(kdir):
    return os.path.join(kdir, INDEX_REL)


def features_path(kdir):
    return os.path.join(kdir, FEATURES_REL)


def learnings_dir(kdir):
    return os.path.join(kdir, LEARNINGS_REL)


def decisions_dir(kdir):
    return os.path.join(kdir, DECISIONS_REL)


# --- shared index READ helpers -----------------------------------------------
def _try_rebuild_missing_index(kdir):
    """Best-effort rebuild of a MISSING/corrupt derived index.json from entry
    frontmatter. Returns True iff a valid index.json now exists on disk.

    Never raises: a KB with no rebuildable entries, or a frontmatter error the
    quarantine path still rejects wholesale, leaves the caller's original error
    intact (returns False). Guarded so this is only ever a RECOVERY, never a silent
    data mutation on a healthy index — the caller only invokes it after a genuine
    load failure.

    The `indexrebuild` import is FUNCTION-LOCAL by necessity, not by taste: this
    module is the SINK of the index package (indexrebuild imports IT), so a
    module-level edge would be a hard import cycle. Deferring it to call time makes
    the edge exist only on the recovery path, which runs at most once per load.
    """
    import agentware_lib.knowledge.index.indexrebuild as indexrebuild
    try:
        data, _rosters, errors = indexrebuild.rebuild_kb(kdir)
    except Exception:  # noqa: BLE001 — recovery must never crash the caller
        return False
    if errors or data is None:
        return False
    return os.path.exists(index_path(kdir))


def load_index(kdir, _allow_rebuild=True):
    """Load <kdir>/index.json. Returns (data, error).

    index.json is a DERIVED cache (C-1): it is regenerable from per-entry
    frontmatter, so a FRESH CLONE (which never carried it) or a post-`pull` tree
    can legitimately have it MISSING — and a raw corruption is likewise
    recoverable. To keep a fresh clone from being bricked (this used to hard-error,
    failing the very FIRST command anyone ran), transparently rebuild it ONCE from
    entry frontmatter, then load.

    `rebuild_kb` never calls back into `load_index`, so there is no recursion, and
    `_allow_rebuild=False` on the retry guarantees a SINGLE attempt regardless. The
    rebuild is best-effort: if it cannot produce a valid index the ORIGINAL error is
    returned unchanged, so `index validate` / `audit` keep surfacing the same
    verbatim message they always did.
    """
    path = index_path(kdir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        if _allow_rebuild and _try_rebuild_missing_index(kdir):
            return load_index(kdir, _allow_rebuild=False)
        return None, "index file not found: %s (run 'agentware init')" % path
    except json.JSONDecodeError as exc:
        if _allow_rebuild and _try_rebuild_missing_index(kdir):
            return load_index(kdir, _allow_rebuild=False)
        return None, "invalid JSON in %s: %s" % (path, exc)
    return data, None


def entry_last_verified(entry):
    """Effective freshness date for an entry.

    Back-compat: entries without an explicit `last_verified` are treated as
    `last_verified == created` (R-FAIL-08 freshness without a forced migration).
    """
    if not isinstance(entry, dict):
        return None
    lv = entry.get("last_verified")
    if isinstance(lv, str) and lv:
        return lv
    return entry.get("created")


def indexed_paths(data):
    return {e.get("path") for e in (data.get("entries") or [])
            if isinstance(e, dict) and e.get("path")}


def parse_tags(raw):
    out = []
    for part in (raw or "").split(","):
        t = part.strip()
        if t and t not in out:
            out.append(t)
    return out


# Explicit facade export list (public AND underscore-private) — the entrypoint
# binds these onto its own namespace via the re-export loop so external consumers
# (tests/_fixtures.load_cli, the dashboard sidecar) keep reaching them as CLI.<name>.
_EXPORTS = [
    "LEARNINGS_REL",
    "DECISIONS_REL",
    "FEATURES_REL",
    "KNOWLEDGE_SUBDIRS",
    "SKILLS_PROPOSED_DIR",
    "ENTRY_SCAN_SUBDIRS",
    "CATEGORIES",
    "SYNC_EXCLUDE_BASENAMES",
    "SYNC_CATEGORY_RULES",
    "REQUIRED_FIELDS",
    "index_path",
    "features_path",
    "learnings_dir",
    "decisions_dir",
    "load_index",
    "_try_rebuild_missing_index",
    "entry_last_verified",
    "indexed_paths",
    "parse_tags",
]
