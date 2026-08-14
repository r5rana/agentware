"""agentware_lib.knowledge.index.indexrebuild — entry scan/inference + KB rebuild (layer: knowledge).

Owns: the entry-file collection + unindexed-gap scan and the id/title/category
inference `index sync --fix` uses (_is_sync_candidate/scan_unindexed/
scan_entry_files/_infer_category/_suggest_id/_suggest_title), plus the KB-rebuild
orchestrators build_index_from_frontmatter (frontmatter-only index
reconstruction, with its row builders _frontmatter_row/_entry_title_summary/
_apply_org_facet) and rebuild_kb (full index.json + rosters + FEATURES.md +
BM25-cache rebuild; the SOLE corpus_fingerprint + BM25-cache writer).
Does NOT own: the layout constants + READ primitives (indexed_paths/features_path)
-> agentware_lib.knowledge.index.indexread; the write core (save_index/
insert_entry_sorted/add_tags_to_map) -> agentware_lib.knowledge.index.indexwrite;
validate_index + _render_roster (rebuild_kb CALLS both) ->
agentware_lib.knowledge.index.indexvalidate; the `index rebuild`/`index sync`
command handlers -> agentware_lib.features.indexcmd.
Layer deps: knowledge -> core (module-level config/dates/frontmatter; lazy
kblayout) + the SAME-PACKAGE siblings indexread/indexwrite/indexvalidate (module
aliases, so a monkeypatch of the owner propagates), all DOWNWARD.
build_index_from_frontmatter + rebuild_kb carry FIVE M12-permitted irreducible
upward SYMBOL edges via FUNCTION-LOCAL (lazy) imports: read_skill_frontmatter
(features) + build_corpus/eval_corpus_fingerprint/build_bm25_cache/write_bm25_cache
(retrieval); plus a lazy knowledge->knowledge curation import that would otherwise
form an index<->curation module-load cycle. NO module-level import is upward or cyclic.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Moved VERBATIM from the pre-split knowledge/index.py modulo owner-module
qualification of the moved names and the decomposition of
build_index_from_frontmatter into named row builders (the emitted dict key ORDER
— and therefore the canonical index.json bytes — is unchanged).
"""

import os
import re
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.knowledge.index.indexlock as indexlock
import agentware_lib.knowledge.index.indexread as indexread
import agentware_lib.knowledge.index.indexvalidate as indexvalidate
import agentware_lib.knowledge.index.indexwrite as indexwrite
from agentware_lib.knowledge.index.indexread import (
    ENTRY_SCAN_SUBDIRS, KNOWLEDGE_SUBDIRS, SYNC_CATEGORY_RULES,
    SYNC_EXCLUDE_BASENAMES)


# --- unindexed-gap scan + inference (index sync) -----------------------------
def _is_sync_candidate(path):
    """True if `path` (knowledge-dir-relative) is an indexable *.md gap."""
    p = path.replace(os.sep, "/")
    base = os.path.basename(p)
    parts = p.split("/")
    if base in SYNC_EXCLUDE_BASENAMES:
        return False
    if "templates" in parts:
        return False
    # Section roster `<section>/index.md` (exactly 2 parts) is a human roster.
    if base == "index.md" and len(parts) == 2:
        return False
    return True


def scan_unindexed(kdir, data):
    """Return sorted unindexed indexable *.md files (knowledge-dir-relative)."""
    have = indexread.indexed_paths(data)
    found = []
    for sub in ENTRY_SCAN_SUBDIRS:
        d = os.path.join(kdir, sub)
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for fn in files:
                if not fn.endswith(".md"):
                    continue
                rel = os.path.relpath(os.path.join(root, fn), kdir).replace(os.sep, "/")
                if rel in have:
                    continue
                if _is_sync_candidate(rel):
                    found.append(rel)
    return sorted(set(found))


def scan_entry_files(kdir):
    """All indexable entry *.md paths (knowledge-dir-relative), sorted.

    Same candidate rule as `index sync` (2-part `<section>/index.md` rosters and
    structural files are excluded), but WITHOUT the already-indexed filter — this
    is the full set of files `index rebuild` derives the index from.

    Scans ENTRY_SCAN_SUBDIRS (KNOWLEDGE_SUBDIRS + `skills-proposed/`), each
    `isdir()`-gated, so a proposed skill is recall-visible but a flat/existing
    KB without `skills-proposed/` rebuilds byte-identically (INV-1). Proposals
    infer category `references` (via `_infer_category`), so they are indexed but
    never rendered into the skills roster / injection set.
    """
    found = []
    for sub in ENTRY_SCAN_SUBDIRS:
        d = os.path.join(kdir, sub)
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for fn in files:
                if not fn.endswith(".md"):
                    continue
                rel = os.path.relpath(os.path.join(root, fn), kdir).replace(os.sep, "/")
                if _is_sync_candidate(rel):
                    found.append(rel)
    return sorted(set(found))


def _infer_category(path):
    p = path.replace(os.sep, "/")
    for prefix, category, _id_prefix in SYNC_CATEGORY_RULES:
        if p.startswith(prefix):
            return category
    return "references"


def _suggest_id(path):
    p = path.replace(os.sep, "/")
    category = _infer_category(p)
    id_prefix = "ref"
    for prefix, cat, ip in SYNC_CATEGORY_RULES:
        if cat == category:
            id_prefix = ip
            break
    stem = p[:-3] if p.endswith(".md") else p
    base = os.path.basename(stem)
    if base in ("SKILL", "index"):
        base = os.path.basename(os.path.dirname(stem))
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return "%s-%s" % (id_prefix, slug)


def _suggest_title(path):
    eid = _suggest_id(path)
    parts = eid.split("-")[1:] or eid.split("-")
    return " ".join(w.capitalize() for w in parts)


# --- KB rebuild: frontmatter reconstruction + full rebuild orchestrator -------
# build_index_from_frontmatter + rebuild_kb (wave-6 seam 6). VERBATIM from the
# pre-split index.py modulo owner-module qualification of the moved names + the
# lazy upward imports documented in the module docstring. rebuild_kb is the SOLE
# writer of the index.json corpus_fingerprint (F2) and the derived BM25 postings
# cache (INV-2).
def _entry_title_summary(kdir, rel, fm, category):
    """(title, summary) for one entry, with the skills field-CAPTURE fix.

    Field-CAPTURE fix (feature 260722): a skill's identity lives in `name` +
    `description` frontmatter (agentskills.io SKILL.md spec), neither of which is
    an index field — so `split_frontmatter` strips them and title/summary land
    empty. Capture them here via the skill-specific parser (name->title,
    description->summary) rather than widening FRONTMATTER_FIELDS (that would emit
    empty name:/description: on every non-skill entry, breaking INV-1). Only skill
    entries take this path, so non-skill entries + FLAT KBs rebuild byte-identically.
    """
    title = fm.get("title") or ""
    summary = fm.get("summary") or ""
    if category == "skills" and (not title or not summary):
        # LAZY (function-local) upward import: read_skill_frontmatter lives
        # in features.skills (seam-2); knowledge->features is one of the five
        # M12-permitted irreducible upward SYMBOL edges (skills docstring),
        # carried function-local so this module has no module-level upward edge.
        from agentware_lib.features.skills import read_skill_frontmatter
        sfm = read_skill_frontmatter(os.path.join(kdir, rel))
        if not title and sfm.get("name"):
            title = sfm["name"]
        if not summary and sfm.get("description"):
            summary = sfm["description"]
    return title, summary


def _apply_org_facet(row, fm, kdir):
    """Org facet (feature 260721): record origin/team/project/status so recall
    can filter by team and org lint can enforce provenance. Added ONLY when
    resolvable (frontmatter or path-inference) so a FLAT non-org KB rebuilds
    to a byte-identical index.json (INV-1) — these keys are simply absent
    there. Never read by _entry_doc_text, so ranking is unaffected."""
    origin, team = config._entry_origin_team(fm, kdir)
    if origin:
        row["origin"] = origin
    if team:
        row["team"] = team
    for opt in ("project", "status", "supersedes"):
        v = fm.get(opt)
        if v:
            row[opt] = v


def _frontmatter_row(kdir, rel, fm, eid):
    """Build ONE index row from an entry's frontmatter (`path` is path-derived).

    Key INSERTION ORDER is a byte contract — index.json is serialized in this
    order, so it matches the pre-split builder exactly.
    """
    category = fm.get("category") or _infer_category(rel)
    created = fm.get("created") or dates._today()
    title, summary = _entry_title_summary(kdir, rel, fm, category)
    row = {
        "id": eid,
        "title": title,
        "category": category,
        "path": rel,
        "tags": list(fm.get("tags") or []),
        "created": created,
        "summary": summary,
        "last_verified": fm.get("last_verified") or created,
    }
    # Derived graph column: include `relates` ONLY when the entry declares
    # edges, so a relates-free KB rebuilds to a byte-identical index.json
    # (backward-compatible). Tokens are already normalized by the parser.
    relates = list(fm.get("relates") or [])
    if relates:
        row["relates"] = relates
    _apply_org_facet(row, fm, kdir)
    return row


def build_index_from_frontmatter(kdir):
    """Reconstruct the index dict from per-entry frontmatter alone.

    Returns (data, problems). Deterministic: entries are (category, id)-sorted and
    the tags map is rebuilt from that stable order, so identical inputs yield
    byte-identical output (C-2). `path` is derived from each file's location;
    every other column comes from its frontmatter (C-1).

    `problems` is a REPORT, not a verdict — the caller decides. `rebuild_kb`
    QUARANTINES (skips the offender, rebuilds the rest); the nothing-lost merge
    gate and `attach` call this directly and keep treating any problem as fatal.
    """
    entries, problems = [], []
    seen_ids = {}
    for rel in scan_entry_files(kdir):
        fm = frontmatter.read_entry_frontmatter(os.path.join(kdir, rel))
        eid = fm.get("id")
        if not eid:
            problems.append("missing frontmatter id (un-migrated?): %s" % rel)
            continue
        # De-duplicate globally-unique ids AT THE SOURCE (B9): the SECOND file
        # carrying a colliding id is a problem — keep the first (scan order, which
        # is sorted and therefore deterministic), skip the rest. Without this a
        # duplicate id reaches the index twice: `query --id` becomes ambiguous, the
        # tag map double-counts, and `_entry_key` no longer yields a TOTAL order.
        if eid in seen_ids:
            problems.append("duplicate id '%s': keeping %s, skipping %s"
                            % (eid, seen_ids[eid], rel))
            continue
        seen_ids[eid] = rel
        entries.append(_frontmatter_row(kdir, rel, fm, eid))
    # Sort ONCE — O(n log n) — instead of a per-file insert_entry_sorted (an O(n)
    # linear scan per file => O(n^2) over a full rebuild, on the git-conflict hot
    # path, B4). list.sort is STABLE and uses the SAME `_entry_key` ((category,
    # id)); ids are de-duplicated above so the keys are unique, giving a TOTAL
    # order byte-identical to the incremental-insert order.
    entries.sort(key=indexwrite._entry_key)
    tags_map = {}
    for e in entries:
        indexwrite.add_tags_to_map(tags_map, e["id"], e["tags"])
    return {"entries": entries, "tags": tags_map}, problems


def rebuild_kb(kdir):
    """Regenerate index.json + rosters + FEATURES.md from frontmatter at `kdir`.

    Path-parameterized core of `index rebuild` so callers (the CLI command AND
    the git-sync push resolver, Task 4.1) can rebuild ANY knowledge dir — e.g. a
    throwaway clone mid-rebase — not just the resolved operator KB. Returns
    `(data, rosters, errors)`: on any frontmatter/validation error `data` is None,
    `errors` is the non-empty reason list, and NOTHING is written (the caller
    decides how to surface the failure). Deterministic + idempotent (C-1/C-2).

    FAULT-ISOLATING (B9): a single malformed or duplicate-id entry file no longer
    aborts the WHOLE rebuild. Aborting wrote NOTHING, which left the index stale
    for every reader — the same failure class that once ate an entire category.
    The offender is SKIPPED and WARNED about (structured, to stderr, never
    silently) and the rest is rebuilt; its `.md` stays on disk, so nothing is lost
    and the fix is a normal edit. `validate_index` still guards the RESULT set
    below, and the merge/attach callers that must stay strict call
    `build_index_from_frontmatter` DIRECTLY — quarantine is scoped to this
    `index rebuild` path alone.
    """
    # Serialize the whole build -> save -> derived-write against concurrent
    # mutating verbs (B2): a `learn`/`decide`/`index add` landing between our build
    # and our save would otherwise be clobbered out of the index (recoverable from
    # frontmatter, but transiently dropped from recall). Reentrant with
    # `load_index`'s auto-rebuild path, which calls rebuild_kb while already
    # holding this same lock.
    with indexlock._index_lock(kdir):
        return _rebuild_kb_locked(kdir)


def _rebuild_kb_locked(kdir):
    """`rebuild_kb`'s body, with the index mutation lock already held.

    Split out so the lock acquisition is a single unmissable line at the one entry
    point and the body stays within the per-function ceiling. NEVER call this
    directly — an unlocked rebuild is exactly the lost update the lock exists to
    prevent.
    """
    data, quarantined = build_index_from_frontmatter(kdir)
    for msg in quarantined:
        sys.stderr.write("agentware: rebuild: QUARANTINED (skipped, rebuilding "
                         "the rest): %s\n" % msg)
    verrs = indexvalidate.validate_index(kdir, data)
    if verrs:
        return None, [], ["rebuild produced an invalid index: %s" % e
                          for e in verrs]
    # LAZY (function-local) imports -- rebuild_kb is the cross-layer KB-rebuild
    # orchestrator. build_corpus/eval_corpus_fingerprint (retrieval.corpus) +
    # build_bm25_cache/write_bm25_cache (retrieval.bm25) are FOUR of the five
    # M12-permitted irreducible knowledge->retrieval upward SYMBOL edges; curation
    # is a knowledge SIBLING whose MODULE-level import would form an index<->curation
    # load cycle; _ensure_kb_gitignore is DOWNWARD (core.kblayout). Carried
    # function-local so this module keeps ZERO module-level upward/cyclic edge.
    from agentware_lib.retrieval.corpus import build_corpus, eval_corpus_fingerprint
    from agentware_lib.retrieval.bm25 import build_bm25_cache, write_bm25_cache
    from agentware_lib.core.kblayout import _ensure_kb_gitignore
    import agentware_lib.knowledge.curation as curation

    # Store the whole-corpus fingerprint IN index.json:
    # bm25_cache_is_fresh compares the cache's fingerprint to this IN-MEMORY value,
    # an O(1) freshness check that never re-reads every body file on recall. Derived
    # from the SAME build_corpus/eval_corpus_fingerprint the cache uses, so they
    # agree. Deterministic (content hash), so a rebuild over one KB state is stable.
    data["corpus_fingerprint"] = eval_corpus_fingerprint(build_corpus(kdir, data))

    # rebuild_kb is the SOLE legitimate fingerprint writer — preserve it here (every
    # other save_index drops it so a write-without-rebuild falls back to live-scan; F2).
    indexwrite.save_index(kdir, data, preserve_fingerprint=True)
    # Derived BM25 postings cache: rebuild_kb is its SOLE writer (INV-2 — recall/
    # eval never write it). Pure/stdlib/no-network, so this is safe even on the
    # git-sync throwaway-clone rebuild path. Best-effort: a write failure must
    # never block the index rebuild — the cache is regenerable and recall falls
    # back to the live scan when it is absent. Ensure `.cache/` is gitignored
    # before the cache is ever materialized.
    try:
        _ensure_kb_gitignore(kdir)
        write_bm25_cache(kdir, build_bm25_cache(kdir, data))
    except OSError:
        pass
    rosters = []
    for sub in KNOWLEDGE_SUBDIRS:
        d = os.path.join(kdir, sub)
        if not os.path.isdir(d):
            continue
        with open(os.path.join(d, "index.md"), "w", encoding="utf-8") as f:
            f.write(indexvalidate._render_roster(sub, data["entries"]))
        rosters.append("%s/index.md" % sub)
    with open(indexread.features_path(kdir), "w", encoding="utf-8") as f:
        f.write(curation._render_features(data))
    return data, rosters, []


# Explicit facade export list (public AND underscore-private) — the entrypoint
# binds these onto its own namespace via the re-export loop so external consumers
# (tests/_fixtures.load_cli, the dashboard sidecar) keep reaching them as CLI.<name>.
_EXPORTS = [
    "_is_sync_candidate",
    "scan_unindexed",
    "scan_entry_files",
    "_infer_category",
    "_suggest_id",
    "_suggest_title",
    "build_index_from_frontmatter",
    "rebuild_kb",
    "_rebuild_kb_locked",
]
