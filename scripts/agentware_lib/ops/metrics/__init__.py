"""agentware_lib.ops.metrics — cross-session metrics COMPUTE (layer: ops).

AGGREGATOR ONLY. This package holds NO logic: it re-imports the public names of
its three domain submodules so every pre-split call site — which reaches the
module under an alias (`import agentware_lib.ops.metrics as _metrics`) and reads
`_metrics.apply_pricing`, plus the two by-name importers
(`from agentware_lib.ops.metrics import _TRUNCATION_RISK_THRESHOLD` in
`ops.loophealth`, and the four-constant import in `ops.derive`) — keeps working
byte-identically after the split, and so does every existing monkeypatch target
(`metrics._sessionparse.<name>`). The submodules:

  * `metricspricing`   — the price table + longest-substring model lookup, the pure
                         usage->cost, the p50 helper, the cost-record rollup
                         builders, apply_pricing, load_price_table, and the
                         context-window sizing constants + helpers.
  * `metricsaggregate` — cross-session aggregation (aggregate_metrics /
                         build_session_index / the feature-grouping helpers) and
                         the session-identity model (resolve_session_identity /
                         _is_noise_session / _agent_session_view + its constants).
  * `metricsemit`      — both ends of the logs/metrics.jsonl emission channel
                         (_read_metrics_jsonl + emit_onboarding_metric).

Owns: nothing of its own — only the re-export surface that keeps the pre-split
import path and every alias target resolving unchanged. It deliberately carries
NO `_EXPORTS`: the submodules carry theirs and are the entries the entrypoint
facade iterates, so restating them here would give one symbol two owners.
Does NOT own: any behaviour — see the submodules above; the transcript PARSE + the
single-instance read cache -> agentware_lib.ops.sessionparse; the derive_* reporting
family -> agentware_lib.ops.derive; loop analytics/trace/health/alerts/failure-ladder
-> agentware_lib.ops.loopanalytics/looptrace/loophealth/alerts/failureladder; the
cmd_metrics/cmd_dashboard CLI handlers -> their own sibling ops cli module, whose
name is deliberately NOT spelled anywhere in this file: ops.metrics must never name
it as an import target (that edge would close the ops.metrics<->ops.derive cycle),
and its granular unit test asserts the token is absent from this source; fleet/org
/team -> agentware_lib.ops.fleet.
Layer deps: ops.metrics -> ops.sessionparse (same layer, downward to the leaf) +
core.dates (downward) — both owned by the submodules; the aliases below are bound
here only so the pre-split namespace still resolves. The three submodules are
mutually independent (no sibling import), so this package has no internal cycle.
Fan-in: 7 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

# Namespace parity with the pre-split flat module: callers and tests reach these
# through THIS package (`metrics._sessionparse.parse_session`), so the aliases must
# keep resolving here even though every body now lives in a submodule. The read
# cache stays the SINGLE ops.sessionparse instance — no submodule rebinds it.
import agentware_lib.ops.sessionparse as _sessionparse   # noqa: F401
import agentware_lib.core.dates as dates                 # noqa: F401

from .metricsaggregate import (                          # noqa: F401
    _AGENT_ACTIVE_WINDOW_S,
    _UNATTRIBUTED,
    _agent_session_view,
    _group_events_by_feature,
    _is_noise_session,
    _session_matched_features,
    aggregate_metrics,
    build_session_index,
    resolve_session_identity,
)
from .metricsemit import (                                # noqa: F401
    _read_metrics_jsonl,
    emit_onboarding_metric,
)
from .metricspricing import (                             # noqa: F401
    _COST_ANOMALY_FACTOR,
    _COST_ANOMALY_MIN_HISTORY,
    _CONTEXT_WINDOW_1M,
    _DEFAULT_CONTEXT_WINDOW,
    _DEFAULT_PRICES,
    _PRICE_COMPONENT,
    _TRUNCATION_RISK_THRESHOLD,
    _context_window_for_rows,
    _cost_record,
    _finalize_record,
    _median,
    _model_context_window,
    _price_for,
    _usage_cost,
    apply_pricing,
    load_price_table,
)
