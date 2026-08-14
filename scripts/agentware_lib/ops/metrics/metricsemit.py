"""agentware_lib.ops.metrics.metricsemit -- the logs/metrics.jsonl EMISSION CHANNEL (read + append).

Owns: both ends of the append-only loop-emission channel logs/metrics.jsonl --
  * the reader _read_metrics_jsonl (co-moved wave-4 seam 4; parses the whole
    channel ONCE DOWN through the single-instance read cache, then filters in
    memory, optionally to one feature);
  * the writer emit_onboarding_metric (co-moved wave-4 seam 8; best-effort append
    of one `onboarding` event, never blocking onboarding on a write failure).

Does NOT own: pricing, the cost/day/feature rollups and context-window sizing (the
sibling agentware_lib.ops.metrics.metricspricing); cross-session aggregation + session
identity (the sibling agentware_lib.ops.metrics.metricsaggregate); the transcript PARSE
+ the single-instance read cache (agentware_lib.ops.sessionparse -- imported DOWN, so
the LRU stays one instance); the derive_* reporting family (agentware_lib.ops.derive);
loop analytics/trace/health/alerts/failure-ladder
(agentware_lib.ops.loopanalytics/looptrace/loophealth/alerts/failureladder); the
cmd_metrics/cmd_dashboard CLI handlers (their own sibling ops cli module, whose name
this package never spells -- naming it as an import target would close the
ops.metrics<->ops.derive cycle); the fleet event
shards, which are a DIFFERENT channel (agentware_lib.ops.fleet).

Layer deps: ops.metrics.metricsemit -> ops.sessionparse (same layer, downward to the
leaf) + core.dates (downward, for the emit timestamp) + stdlib (json, os). No
knowledge/retrieval/features/cli import, and NO sibling import inside the package --
this module is a leaf of its own package.

Determinism: the READ half is read-only (INV-2) and reaches the (mtime,size)-keyed
memo ONLY as _sessionparse._cached_by_key/_file_key -- the cache dict itself is owned
by ops.sessionparse and is never rebound here, so _sessionparse._reset_read_cache()
stays authoritative for this reader too. The WRITE half touches only the gitignored
logs/ tree, never the KB or the index.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so resident callers
(the deferred derive_loop_* family, loopanalytics/looptrace, the onboarding config
surface) resolve these as bare names and load_cli tests reach them as CLI.<name>.
Neither is monkeypatched by any test (grep-verified), so the by-name re-export is safe.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os

import agentware_lib.ops.sessionparse as _sessionparse
import agentware_lib.core.dates as dates


# --- loop-emission channel reader (co-moved wave-4 seam 4) -------------------
# _read_metrics_jsonl reads the append-only channel logs/metrics.jsonl. Co-moved
# here from the entrypoint WITH the ops.loopanalytics/ops.looptrace seam because the
# loop derive_* (throughput/analytics/health/trace) are its consumers; it reads
# DOWN into the single-instance read cache via _sessionparse._cached_by_key/
# _file_key. Imported BY NAME onto the facade so the resident deferred derive_loop_*
# and loopanalytics/trace (_metrics._read_metrics_jsonl) reach it.
def _read_metrics_jsonl(kdir, feature=None):
    """Read the append-only loop-emission channel logs/metrics.jsonl (Task 6/7).

    READ-ONLY + tolerant: a missing file yields [], malformed JSON lines are
    skipped, non-dict payloads ignored. Optionally filters to one `feature`. The
    channel does not exist until the Task 6/7 shell emitter lands; until then this
    returns [] so the derivations are forward-compatible (empty, never crashing).
    """
    path = os.path.join(kdir, "logs", "metrics.jsonl")

    def _compute_all():
        out = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    out.append(obj)
        except OSError:
            return []
        return out

    # Parse the whole channel ONCE (mtime/size-keyed), then filter in memory.
    # Previously each of the 37 per-feature calls re-read+re-parsed the entire
    # file; now repeated calls within a request (and across polls) are O(events).
    all_events = _sessionparse._cached_by_key(("metrics_jsonl", path), _sessionparse._file_key(path),
                                _compute_all)
    if feature is None:
        return list(all_events)
    return [e for e in all_events if e.get("feature") == feature]


def emit_onboarding_metric(kdir, mode, user_handle=None, ts=None):
    """Rule-7 observability: append one `onboarding` event to logs/metrics.jsonl.

    Best-effort + read-only w.r.t. the KB/index (INV-2): a write failure never
    blocks onboarding. `logs/` is gitignored, so this never reaches the remote.
    Shape: {ts, event:"onboarding", mode, user_handle}. Feature
    260625-team-mode-onboarding-fork.
    """
    try:
        rec = {"event": "onboarding", "mode": mode,
               "user_handle": user_handle, "ts": ts or dates.utc_now_iso()}
        logs = os.path.join(kdir, "logs")
        os.makedirs(logs, exist_ok=True)
        with open(os.path.join(logs, "metrics.jsonl"), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        return True
    except OSError:
        return False


_EXPORTS = [
    '_read_metrics_jsonl',
    'emit_onboarding_metric',
]
