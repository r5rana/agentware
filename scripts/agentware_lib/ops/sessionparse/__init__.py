"""agentware_lib.ops.sessionparse — session-transcript PARSING + the single-instance read cache (layer: ops).

AGGREGATOR ONLY. This package holds NO logic: it re-imports the public names of
its three domain submodules so every pre-split call site — which reaches the module
under an alias (`import agentware_lib.ops.sessionparse as _sessionparse`) and reads
`_sessionparse.parse_session` — keeps resolving unchanged, and so every existing
monkeypatch target (`<consumer>._sessionparse.<name>`) keeps working. The
submodules are:

  * `sessioncache`  — the SINGLE-INSTANCE bounded thread-safe LRU read cache
                      (_READ_CACHE/_READ_CACHE_LOCK/_READ_CACHE_MAX/_CACHE_MISS),
                      the (mtime_ns,size) file keys, _cached_by_key/_cached_text/
                      _reset_read_cache, and _parse_iso/_duration_seconds.
  * `sessionusage`  — ONE-transcript JSONL aggregation (aggregate_jsonl), the
                      usage-key tuples, the transcript regexes + stage signal
                      tuples, _empty_usage/_cache_read_ratio and the
                      _merge_usage/_merge_tools/_merge_models fold helpers.
  * `sessionread`   — the sessions-dir resolver, classify_stage/_dominant_feature,
                      the memoized parsers (parse_session/_parse_session_uncached)
                      and _session_feature_match/collect_sessions.

The union of the three submodules' `_EXPORTS` is exactly the pre-split module's
`_EXPORTS` (34 names) — no name added, none dropped.

⚠️ SINGLE LRU INSTANCE: the cache dict, its lock and the `_READ_CACHE_MAX` bound
live in `sessioncache` and NOWHERE else; both siblings reach them through the
`sessioncache` module alias by attribute. The names re-exported HERE are the same
objects, so calling `sessionparse._reset_read_cache()` clears the one true cache —
but REBINDING a name on this package (e.g. `sessionparse._READ_CACHE_MAX = 2`) does
NOT reach `_cached_by_key`, which reads its own module's globals. Rebind on the
OWNER (`sessionparse.sessioncache._READ_CACHE_MAX`).

⚠️ MONKEYPATCH SEAM: `_parse_session_uncached` is resolved by `parse_session` in
`sessionread`'s OWN globals, so the test spy must patch
`sessionparse.sessionread._parse_session_uncached`, never the copy re-exported here.

Owns: nothing of its own — only the re-export surface that keeps the pre-split
import path (`import agentware_lib.ops.sessionparse as _sessionparse`) and every
monkeypatch target resolving unchanged.
Does NOT own: any behaviour — see the three submodules above; cross-session
aggregation, pricing and session identity (agentware_lib.ops.metrics); the derive_*
reporting family (agentware_lib.ops.derive); loop analytics/trace/health/alerts;
cmd_metrics/cmd_dashboard (agentware_lib.ops.metricscmd).
Layer deps: ops -> its own submodules only; those are stdlib-only leaves, so the
package imports nothing from core/knowledge/retrieval/features/cli.
"""

from agentware_lib.ops.sessionparse.sessioncache import (
    _CACHE_MISS,
    _READ_CACHE,
    _READ_CACHE_LOCK,
    _READ_CACHE_MAX,
    _cached_by_key,
    _cached_text,
    _duration_seconds,
    _file_key,
    _multi_file_key,
    _parse_iso,
    _reset_read_cache,
)
from agentware_lib.ops.sessionparse.sessionusage import (
    _FEATURE_RE,
    _INPUT_SIDE_KEYS,
    _PLAN_DOC_RE,
    _PLAN_PATH_RE,
    _PROMISE_RE,
    _STAGE_LOOP_RE,
    _STAGE_PLAN_SIGNALS,
    _STAGE_WORK_SIGNALS,
    _USAGE_KEYS,
    _cache_read_ratio,
    _empty_usage,
    _merge_models,
    _merge_tools,
    _merge_usage,
    aggregate_jsonl,
)
from agentware_lib.ops.sessionparse.sessionread import (
    LOGS_SESSIONS_REL,
    _dominant_feature,
    _parse_session_uncached,
    _session_feature_match,
    classify_stage,
    collect_sessions,
    logs_sessions_dir,
    parse_session,
)
