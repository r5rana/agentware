"""agentware_lib.knowledge.skillentry — the skill <-> index entry seam (layer: knowledge).

Owns: the TWO index read-modify-write critical sections the `skill` verb performs
— registering an installed skill as a `skills`-category entry
(`skill_register_entry`) and unregistering it (`skill_unregister_entry`). Both hold
`index._index_lock` across their whole load -> mutate -> save.
Does NOT own: the authored-skill catalog, the SKILL.md frontmatter reader, the
pinned-ref resolver, the static vetter or any `cmd_skill_*` handler ->
agentware_lib.features.skills; the index primitives themselves ->
agentware_lib.knowledge.index; the proposal lifecycle ->
agentware_lib.features.proposals.
Layer deps: knowledge -> knowledge (index, a same-layer sibling) + core
(frontmatter), all DOWNWARD/same-layer. It lives in KNOWLEDGE, not features,
because what it produces is a knowledge ENTRY — the `features.skills` handler that
calls it may not import a features SIBLING, and registering an index entry was
never a features concern in the first place. It imports `features.skills` NOWHERE:
the dependency runs strictly downward, so there is no cycle, and the two catalog
values it needs (a summary and a tag list) are passed IN by the caller rather than
looked up here.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

## Why this is its own module and not two blocks inside the handlers

Two reasons, and the second is the one that matters.

The mechanical reason: `features/skills.py` sat at 598 of its 600-line cap, so the
lock could not be added there without breaking a ceiling the plan makes an
INVARIANT rather than a ratchet. "Split the file" is the documented answer.

The substantive reason: an index read-modify-write must be a NAMED, lock-wrapped
unit. Task 22 exists because atomicity is not mutual exclusion — two agents that
each load, mutate and save leave only the second's entry, and the first's is simply
gone from recall with nothing turning red. A critical section buried in the middle
of a 126-line handler is one a future edit reorders without noticing; a function
whose entire body is the locked section is one a reader can check at a glance and a
gate can verify by reachability. Both of the `skill` verb's index writes now live
side by side under one lock discipline.

Stdlib only, no network.
"""

import os

import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.knowledge.index as index

# An index summary is a one-line roster cell, not prose. Longer than this and the
# generated roster tables wrap unreadably, so it is truncated on an ellipsis.
SKILL_SUMMARY_MAX = 200
SKILL_SUMMARY_FALLBACK = "Authored agentware skill."
SKILL_DEFAULT_TAGS = ("skill",)


def skill_entry_id(name):
    """The index id for skill `name`. One definition, used by both directions."""
    return "skill-%s" % name


def _skill_summary(description, catalog_summary):
    """Roster summary for a skill: its SKILL.md description, else the catalog's."""
    summary = (description or catalog_summary or SKILL_SUMMARY_FALLBACK).strip()
    if len(summary) > SKILL_SUMMARY_MAX:
        summary = summary[:SKILL_SUMMARY_MAX - 3].rstrip() + "..."
    return summary


def skill_register_entry(kdir, eid, name, rel_md, description=None,
                         catalog_summary=None, catalog_tags=None):
    """Register an installed skill as a `skills` index entry. (indexed, error).

    Returns `(False, None)` when the entry already exists — a re-install is
    idempotent, not a failure. The whole load -> mutate -> save runs under
    `index._index_lock`, because the lost-update window opens at the LOAD: without
    it a concurrent `learn` reloads the pre-install index and saves it back, and the
    skill entry vanishes with no error anywhere.
    """
    with index._index_lock(kdir):  # serialize load->mutate->save (B2)
        idx, ierr = index.load_index(kdir)
        if ierr is not None:
            return False, ierr
        if any(e.get("id") == eid for e in idx.get("entries", [])):
            return False, None
        tags = list(catalog_tags) if isinstance(catalog_tags, list) else []
        entry, aerr = index._do_add(
            kdir, idx, eid, "Skill: %s" % name, "skills", rel_md,
            tags or list(SKILL_DEFAULT_TAGS),
            _skill_summary(description, catalog_summary))
        if aerr is not None:
            return False, "index add failed: %s" % aerr
        frontmatter.ensure_entry_frontmatter(kdir, entry)
        index.save_index(kdir, idx)
        return True, None


def skill_unregister_entry(kdir, eid):
    """Drop a skill's index entry + tag-map rows. (had_entry, error).

    The file on disk is NOT touched here — removing it is the caller's separate,
    explicitly-flagged decision. Same lock discipline as the register direction.
    """
    with index._index_lock(kdir):  # serialize load->mutate->save (B2)
        idx, ierr = index.load_index(kdir)
        if ierr is not None:
            return False, ierr
        entries = idx.setdefault("entries", [])
        tags_map = idx.setdefault("tags", {})
        had_entry = any(e.get("id") == eid for e in entries)
        if had_entry:
            entries[:] = [e for e in entries if e.get("id") != eid]
            index.remove_tags_from_map(tags_map, eid)
            index.save_index(kdir, idx)
        return had_entry, None


def skill_md_path(dest_dir):
    """Absolute path to an installed skill's SKILL.md."""
    return os.path.join(dest_dir, "SKILL.md")


# Explicit facade export list (public AND underscore-private) — the entrypoint
# binds these onto its own namespace via the re-export loop so external consumers
# (tests/_fixtures.load_cli, the dashboard sidecar) keep reaching them as CLI.<name>.
_EXPORTS = [
    "SKILL_SUMMARY_MAX",
    "SKILL_SUMMARY_FALLBACK",
    "SKILL_DEFAULT_TAGS",
    "skill_entry_id",
    "_skill_summary",
    "skill_register_entry",
    "skill_unregister_entry",
    "skill_md_path",
]
