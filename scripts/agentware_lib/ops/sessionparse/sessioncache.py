"""agentware_lib.ops.sessionparse.sessioncache -- the SINGLE-INSTANCE bounded LRU read cache (layer: ops).

⚠️ SINGLE LRU INSTANCE -- THIS MODULE IS THE SOLE OWNER of the cache dict and its
lock. Every reader, inside this package and out, MUST reach the cache THROUGH this
module (sessioncache._cached_by_key / sessioncache._READ_CACHE_MAX /
sessioncache._READ_CACHE), never through a name re-bound in another module: a bare
`from .sessioncache import _READ_CACHE_MAX` copy would fork the tunable and make
_reset_read_cache a silent no-op for that reader, and no test would say so. The
sibling parsers therefore call in via the `sessioncache` module ALIAS by attribute,
so a monkeypatch of this owner propagates.

Owns: the bounded thread-safe LRU read cache (_READ_CACHE + _READ_CACHE_LOCK +
_READ_CACHE_MAX + _CACHE_MISS), the (mtime_ns, size) change-detecting file identity
keys (_file_key / _multi_file_key), the memoization core (_cached_by_key), the test
isolation hook (_reset_read_cache), the memoized full-text read (_cached_text) and
the two small time helpers (_parse_iso / _duration_seconds).
Does NOT own: the JSONL transcript aggregator, the transcript regexes, the
usage-key tuples and the token/tool/model merge helpers ->
agentware_lib.ops.sessionparse.sessionusage; the memoized session parsers, the
stage classifier and the sessions-dir resolver ->
agentware_lib.ops.sessionparse.sessionread; cross-session aggregation, pricing and
the derive_* reporting family -> agentware_lib.ops.metrics / agentware_lib.ops.derive.
Layer deps: ops.sessionparse.sessioncache -> stdlib only (datetime, os, threading,
collections.OrderedDict). It is the SINK of this package -- it imports NO sibling
and both siblings import IT, so the package's internal graph is acyclic. No
core/knowledge/retrieval/features/cli import.
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Determinism: read-only (INV-2). A cache HIT requires an unchanged (mtime_ns, size)
key, so a stale entry can never be served for a changed file; a None key (absent
file) is NEVER cached, so a file appearing later is picked up immediately.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so resident callers
resolve these as bare names and load_cli tests + the dashboard reach them as
CLI.<name>.
"""
import datetime
import os
import threading
from collections import OrderedDict


def _parse_iso(ts):
    """Parse an ISO-8601 timestamp (tolerating a trailing 'Z') to a datetime.

    Returns None on anything unparseable so a single malformed line can never
    crash the parser (transcripts mix control lines that lack timestamps)."""
    if not isinstance(ts, str) or not ts:
        return None
    import datetime
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_seconds(first_dt, last_dt):
    if first_dt is None or last_dt is None:
        return 0.0
    return max(0.0, (last_dt - first_dt).total_seconds())


_READ_CACHE_LOCK = threading.Lock()


_READ_CACHE = OrderedDict()       # cache_key -> (file_key_tuple, value)


_READ_CACHE_MAX = 4096            # bounded LRU; evicts least-recently-used


_CACHE_MISS = object()


def _file_key(path):
    """A change-detecting identity for `path`: (mtime_ns, size), or None when the
    file is absent/unstatable. Any edit (append, rewrite) changes mtime or size,
    so a stale cache entry can never be served for a changed file."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _multi_file_key(paths):
    """Composite change-key over several files (e.g. a session's live+main)."""
    return tuple(_file_key(p) for p in paths)


def _cached_by_key(cache_id, file_key, compute):
    """Return compute() memoized under (cache_id) and validated by file_key.

    A cache HIT requires the same recorded file_key (so a changed file misses and
    re-computes). `file_key` of None (missing file) is NOT cached — compute() runs
    every time so a file appearing later is picked up immediately. Thread-safe +
    LRU-bounded."""
    if file_key is None:
        return compute()
    with _READ_CACHE_LOCK:
        hit = _READ_CACHE.get(cache_id)
        if hit is not None and hit[0] == file_key:
            _READ_CACHE.move_to_end(cache_id)
            return hit[1]
    value = compute()
    with _READ_CACHE_LOCK:
        _READ_CACHE[cache_id] = (file_key, value)
        _READ_CACHE.move_to_end(cache_id)
        while len(_READ_CACHE) > _READ_CACHE_MAX:
            _READ_CACHE.popitem(last=False)
    return value


def _reset_read_cache():
    """Clear the read cache (used by tests for isolation; harmless in prod)."""
    with _READ_CACHE_LOCK:
        _READ_CACHE.clear()


def _cached_text(path):
    """Memoized full-text read of `path` (mtime/size-keyed), or None when absent.
    Used by the substring feature-match so a transcript is read from disk ONCE
    per request instead of once per feature."""
    fk = _file_key(path)

    def _compute():
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except (FileNotFoundError, IsADirectoryError, OSError):
            return None
    return _cached_by_key(("text", path), fk, _compute)


# Explicit facade export list (public AND underscore-private) — the entrypoint
# binds these onto its own namespace via the re-export loop so external consumers
# (tests/_fixtures.load_cli, the dashboard sidecar) keep reaching them as CLI.<name>.
_EXPORTS = [
    '_parse_iso',
    '_duration_seconds',
    '_READ_CACHE_LOCK',
    '_READ_CACHE',
    '_READ_CACHE_MAX',
    '_CACHE_MISS',
    '_file_key',
    '_multi_file_key',
    '_cached_by_key',
    '_reset_read_cache',
    '_cached_text',
]
