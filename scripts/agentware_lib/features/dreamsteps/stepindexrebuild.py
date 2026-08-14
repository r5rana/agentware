"""agentware_lib.features.dreamsteps.stepindexrebuild -- dream step "a", index-rebuild: refresh the derived caches (layer: features).

Owns: dream step "a" (_dream_step_index_rebuild) -- the single call to
index.rebuild_kb, the SOLE cache writer, plus the before/after fingerprint delta
that reports whether index.json and the BM25 cache actually changed, and the
rebase-in-progress guard that refuses to rebuild from an inconsistent tree.
Does NOT own: rebuild_kb itself -> knowledge.index; the fingerprint primitive ->
dreamsteps.dreamcapture; the pull that may have left the rebase in progress ->
dreamsteps.stepkbpull; the other steps -> dreamsteps.step<step>; the ordered
registry that binds this handler to id "a" -> dreamsteps.registry. See
catalog/modules.json.
Layer deps: knowledge (index) + retrieval (bm25, the BM25_CACHE_REL constant BY
NAME) + ops (kbgit, staging) -- all DOWNWARD -- plus ONE dreamsteps SIBLING
(dreamcapture, a stdlib-only leaf), so the package graph stays acyclic.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: guarded and best-effort (INV-2) -- a KB mid-rebase degrades to a
clean SKIP rather than writing a corrupt index, and rebuild errors are reported
as an "error" payload instead of raising into an unattended cron.
"""

import os

import agentware_lib.knowledge.index as index
import agentware_lib.ops.kbgit as kbgit
import agentware_lib.ops.staging as staging

from agentware_lib.retrieval.bm25 import BM25_CACHE_REL

from .dreamcapture import _dream_file_fp


def _dream_step_index_rebuild(kdir, dry_run):
    """Step a — refresh derived caches via rebuild_kb (the SOLE cache writer)."""
    if dry_run:
        return {"status": "planned",
                "would": "index rebuild (index.json + rosters + FEATURES + BM25 cache)"}
    # Guard: if KB has a rebase-in-progress (from a prior crash), do NOT rebuild
    # from the potentially inconsistent tree. Skip cleanly — step 1 already
    # reported the issue and the user must resolve it manually.
    if kbgit.git_is_work_tree(kdir) and staging.git_rebase_in_progress(kdir):
        return {"status": "skipped",
                "reason": "KB has rebase-in-progress — skipping to avoid corrupt index"}
    idx, bm = os.path.join(kdir, index.INDEX_REL), os.path.join(kdir, BM25_CACHE_REL)
    before = (_dream_file_fp(idx), _dream_file_fp(bm))
    data, rosters, errors = index.rebuild_kb(kdir)
    if errors:
        return {"status": "error", "errors": errors}
    after = (_dream_file_fp(idx), _dream_file_fp(bm))
    return {"status": "ok", "entries": len(data["entries"]),
            "rosters": len(rosters),
            "index_changed": before[0] != after[0],
            "bm25_changed": before[1] != after[1]}


_EXPORTS = [
    "_dream_step_index_rebuild",
]
