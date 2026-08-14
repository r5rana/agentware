"""agentware_lib.knowledge.index.indexwrite — index WRITE core + tag-map mutators (layer: knowledge).

Owns: the canonical index.json writer (save_index, incl. the F2
corpus_fingerprint-drop rule), the (category, id) ordering key + insertion-only
sorted insert (_entry_key/insert_entry_sorted), the tag-map mutators
(add_tags_to_map/remove_tags_from_map) and the shared add core (_do_add) every
`index add` / `learn` / `decide` write path funnels through.
Does NOT own: the layout constants + READ primitives (load_index/indexed_paths/
the path helpers) -> agentware_lib.knowledge.index.indexread; validate_index +
the knowledge validators + roster rendering ->
agentware_lib.knowledge.index.indexvalidate; the entry-file scan/inference + the
KB-rebuild orchestrators (rebuild_kb is the SOLE preserve_fingerprint=True
caller) -> agentware_lib.knowledge.index.indexrebuild; the cmd_index_* handlers
-> agentware_lib.features.indexcmd.
Layer deps: knowledge -> core (module-level core.dates) + the SAME-PACKAGE sink
sibling agentware_lib.knowledge.index.indexread (via the `indexread` module
alias, so a monkeypatch of the owner propagates), all DOWNWARD. NO upward and NO
cyclic import (indexread does not import this module).
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

save_index carries a hard BYTE CONTRACT — the canonical (2-space-indent,
trailing-newline) index.json layout and the F2 fingerprint-drop rule are relied on
by the equivalence gate — so its body is moved here VERBATIM.
"""

import json
import os
import stat as _stat
import tempfile

import agentware_lib.core.dates as dates
import agentware_lib.knowledge.index.indexread as indexread


def _replace_mode(path):
    """Permission bits a fresh temp file must take on before replacing `path`.

    `tempfile.mkstemp` creates 0600, so replacing the index with it would silently
    tighten the file's mode on every write. Preserve the DESTINATION's bits when it
    exists; for a first-ever write there is no destination, so fall back to what a
    normal umask-respecting create would have produced.
    """
    try:
        return _stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        cur = os.umask(0)
        os.umask(cur)
        return 0o666 & ~cur


def save_index(kdir, data, preserve_fingerprint=False):
    """Write <kdir>/index.json canonically (2-space indent, trailing newline).

    ATOMIC: serialize to a temp file in the SAME directory, flush + fsync, then
    os.replace() onto index.json. os.replace is atomic on POSIX/Windows when source
    and destination share a filesystem, so a crash or a concurrent READER (recall,
    the nightly dream, autocommit, the dashboard) can NEVER observe a half-written
    or truncated index. The previous plain `open(path, "w")` truncated the real file
    first and then streamed into it, so every reader that polled mid-write got
    invalid JSON — and on a crash the index was left destroyed rather than merely
    stale. fsync before the replace is what makes it DURABLE as well as atomic: it
    orders the data against the rename, so a power loss cannot leave the new name
    pointing at unwritten blocks.

    Atomicity is NOT mutual exclusion: two agents that each load -> mutate -> save
    would still LOSE an update (the second overwrites the first). Callers therefore
    hold `indexlock._index_lock(kdir)` across the whole read-modify-write (every
    mutating verb does). This function stays the byte-identical atomic writer; the
    lock lives in the caller because the lost-update window opens at the READ,
    before save_index is ever entered.

    F2 fix: `corpus_fingerprint` is the O(1)-freshness key (Task 14) and is VALID
    only immediately after a full rebuild_kb. ANY other write (learn/decide/index
    add/remove) mutates the entry set WITHOUT rebuilding the BM25 cache, so it MUST
    drop the stored fingerprint — else bm25_cache_is_fresh's fast path would trust
    the now-stale cache and recall would miss the just-written entry. Dropping it
    makes freshness fall back to the safe live-scan until the next rebuild restores
    it. Only rebuild_kb passes preserve_fingerprint=True (it is the sole legitimate
    fingerprint writer). Writes a shallow copy so the caller's in-memory dict is
    untouched.
    """
    if not preserve_fingerprint and isinstance(data, dict) and "corpus_fingerprint" in data:
        data = {k: v for k, v in data.items() if k != "corpus_fingerprint"}
    path = indexread.index_path(kdir)
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".index.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, _replace_mode(path))
        os.replace(tmp, path)
    except BaseException:
        # Never leave a .index.*.tmp behind, on ANY exit path including
        # KeyboardInterrupt — the temp file lives in the knowledge dir and would
        # otherwise accumulate there and be picked up by a scan.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- tag-map helpers ---------------------------------------------------------
def _entry_key(entry):
    return (entry.get("category") or "", entry.get("id") or "")


def insert_entry_sorted(entries, entry):
    """Insert `entry` at its (category, id) sorted slot (insertion-only)."""
    key = _entry_key(entry)
    for i, existing in enumerate(entries):
        if _entry_key(existing) > key:
            entries.insert(i, entry)
            return
    entries.append(entry)


def add_tags_to_map(tags_map, entry_id, tag_list):
    for t in tag_list:
        bucket = tags_map.get(t)
        if bucket is None:
            tags_map[t] = [entry_id]
        elif entry_id not in bucket:
            bucket.append(entry_id)


def remove_tags_from_map(tags_map, entry_id):
    for t in list(tags_map.keys()):
        bucket = tags_map.get(t)
        if isinstance(bucket, list) and entry_id in bucket:
            bucket[:] = [i for i in bucket if i != entry_id]
            if not bucket:
                del tags_map[t]


def _do_add(kdir, data, eid, title, category, path, tag_list, summary,
            created=None, last_verified=None):
    """Shared add core. Validates BEFORE mutating; returns (entry, error)."""
    entries = data.setdefault("entries", [])
    tags_map = data.setdefault("tags", {})

    if category not in indexread.CATEGORIES:
        return None, "invalid category '%s' (allowed: %s)" % (
            category, ", ".join(indexread.CATEGORIES))
    if not os.path.exists(os.path.join(kdir, path)):
        return None, "path does not exist (relative to knowledge dir): %s" % path
    if any(e.get("id") == eid for e in entries):
        return None, "duplicate id '%s'" % eid
    if any(e.get("path") == path for e in entries):
        return None, "path already indexed: %s" % path
    if created is not None and not dates._is_valid_date(created):
        return None, "created is not YYYY-MM-DD: %r" % created
    if last_verified is not None and not dates._is_valid_date(last_verified):
        return None, "last_verified is not YYYY-MM-DD: %r" % last_verified

    entry = {
        "id": eid,
        "title": title,
        "category": category,
        "path": path,
        "tags": tag_list,
        "created": created or dates._today(),
        "summary": summary,
    }
    # Optional freshness field: store ONLY when explicitly provided so the 36
    # legacy entries stay byte-stable (absent == created via entry_last_verified).
    if last_verified is not None:
        entry["last_verified"] = last_verified
    insert_entry_sorted(entries, entry)
    add_tags_to_map(tags_map, eid, tag_list)
    return entry, None


# Explicit facade export list (public AND underscore-private) — the entrypoint
# binds these onto its own namespace via the re-export loop so external consumers
# (tests/_fixtures.load_cli, the dashboard sidecar) keep reaching them as CLI.<name>.
_EXPORTS = [
    "save_index",
    "_replace_mode",
    "_entry_key",
    "insert_entry_sorted",
    "add_tags_to_map",
    "remove_tags_from_map",
    "_do_add",
]
