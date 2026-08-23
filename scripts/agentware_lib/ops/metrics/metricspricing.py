"""agentware_lib.ops.metrics.metricspricing -- token PRICING, cost rollups + context sizing.

Owns: the deterministic, read-only COST layer over parsed session rows --
  * pricing: the default price table (_DEFAULT_PRICES) + token->component map
    (_PRICE_COMPONENT), longest-substring model lookup (_price_for), the pure
    usage->cost (_usage_cost), the per-day cost-anomaly tuning
    (_COST_ANOMALY_FACTOR/_COST_ANOMALY_MIN_HISTORY), the rollup builders
    (_cost_record/_finalize_record/_rollup_bucket -- the get-or-create the three
    rollups share), apply_pricing (derives cost/cache/model/feature/day rollups onto
    rows + aggregate) with the two folds it walks through (_price_row_models, one
    row's per-model pricing plus the shared by_model rollup; _flag_cost_anomalies,
    the chronological trailing-median walk, which runs AFTER the finalize pass so a
    day is judged by the ROUNDED cost the report shows) and load_price_table, plus
    the p50 helper _median;
  * context-window sizing: _model_context_window/_context_window_for_rows + the
    _DEFAULT_CONTEXT_WINDOW/_CONTEXT_WINDOW_1M/_TRUNCATION_RISK_THRESHOLD constants.

Does NOT own: cross-session aggregation + session identity (the sibling
agentware_lib.ops.metrics.metricsaggregate); the logs/metrics.jsonl channel reader +
the onboarding metric emitter (the sibling agentware_lib.ops.metrics.metricsemit); the
transcript PARSE + the single-instance read cache (agentware_lib.ops.sessionparse --
imported DOWN, so the LRU stays one instance); the derive_* reporting family
(agentware_lib.ops.derive); loop analytics/trace/health/alerts/failure-ladder
(agentware_lib.ops.loopanalytics/looptrace/loophealth/alerts/failureladder); the
cmd_metrics/cmd_dashboard CLI handlers (their own sibling ops cli module, whose name
this package never spells -- naming it as an import target would close the
ops.metrics<->ops.derive cycle); fleet/org/team
(agentware_lib.ops.fleet).

Layer deps: ops.metrics.metricspricing -> ops.sessionparse (same layer, downward to
the leaf) + stdlib (json, re). No core/knowledge/retrieval/features/cli import, and NO
sibling import inside the package -- this module is a leaf of its own package.

The p50 _median here OWNS the entrypoint-level `_median` name and is imported BY
NAME onto the scripts/agentware facade; retrieval.metrics_rank keeps its OWN
module-private _median (a behaviorally identical duplicate, NOT re-exported), and
metrics_rank's facade-held evaluate resolves its p50 against the entrypoint
namespace -- so this _median must stay bound on the facade (the documented
cross-domain reason for the by-name re-export).

_DEFAULT_PRICES is module-level state and lives in EXACTLY this module: its only two
readers (_price_for's fallback and load_price_table's partial-table merge) are here,
so no second module can rebind the table and silently diverge. load_price_table never
mutates it (it copies with dict(...)) — determinism is read-only (INV-2).

Determinism: pure functions of the parsed token usage + the price table. No I/O beyond
load_price_table's single read. apply_pricing mutates only the rows/aggregate handed to
it, and parse_session returns a DEEP COPY, so the canonical cached row is never touched.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so resident callers
(the derive_* family, cmd_metrics/cmd_dashboard, fleet) resolve these as bare names
and load_cli tests + the dashboard reach them as CLI.<name>. None is monkeypatched by
any test (grep-verified), so the by-name re-export is safe.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import re

import agentware_lib.ops.sessionparse as _sessionparse
from agentware_lib.ops.sessionparse import _USAGE_KEYS


# Context-tax tuning. The model context window (tokens) the peak single-turn
# input-side total is measured against, and the utilization fraction past which
# a turn is flagged truncation-risk. Both configurable via `metrics
# --context-window N`; defaults match a 200K-token Claude window.
_DEFAULT_CONTEXT_WINDOW = 200_000


_CONTEXT_WINDOW_1M = 1_000_000


_TRUNCATION_RISK_THRESHOLD = 0.9


def _model_context_window(model):
    """The context-window size (tokens) for a model id. Claude 4-family models run
    with the 1M-token context beta in this harness — a single-turn peak input of
    >900k tokens is ONLY reachable with the 1M window, so measuring those turns
    against 200k yields a nonsensical >400% utilization and a permanent false
    'truncation risk'. Default to 200k for anything unrecognized."""
    m = (model or "").lower()
    if "[1m]" in m or "-1m" in m or "1m" in m.split("-")[-1:]:
        return _CONTEXT_WINDOW_1M
    if re.search(r"claude-(?:opus|sonnet|haiku)-(?:[4-9]|\d{2,})", m):
        return _CONTEXT_WINDOW_1M
    return _DEFAULT_CONTEXT_WINDOW


def _context_window_for_rows(rows):
    """The largest context window across the models present in `rows` (so peak
    utilization is measured against the real ceiling the run actually had)."""
    win = _DEFAULT_CONTEXT_WINDOW
    for r in rows:
        for model in (r.get("models") or {}):
            win = max(win, _model_context_window(model))
    return win


# Default price table ($ per 1,000,000 tokens), keyed by a model-id SUBSTRING.
# Lookup picks the LONGEST matching key (most specific) so e.g.
# "claude-opus-4-20250514" matches "claude-opus-4" over "claude". Fully
# configurable via `metrics --prices <file.json>` (a JSON object of the same
# shape) so no real cost number is hard-coded into behaviour. The cost math is
# PURE: cost = sum(tokens_of_type * price_of_type) / 1e6 — no collection added.
_DEFAULT_PRICES = {
    "claude-opus-4": {
        "input": 15.0, "output": 75.0,
        "cache_creation_input_tokens": 18.75, "cache_read_input_tokens": 1.5},
    "claude-sonnet-4": {
        "input": 3.0, "output": 15.0,
        "cache_creation_input_tokens": 3.75, "cache_read_input_tokens": 0.3},
    "claude-haiku-4": {
        "input": 1.0, "output": 5.0,
        "cache_creation_input_tokens": 1.25, "cache_read_input_tokens": 0.1},
    "claude-3-5-haiku": {
        "input": 0.8, "output": 4.0,
        "cache_creation_input_tokens": 1.0, "cache_read_input_tokens": 0.08},
    "claude-3-5-sonnet": {
        "input": 3.0, "output": 15.0,
        "cache_creation_input_tokens": 3.75, "cache_read_input_tokens": 0.3},
    # OpenAI Codex runtime (AGENTWARE_CLI=codex). USD per 1M tokens. OpenAI bills
    # no cache-WRITE fee (creation == input price) and discounts cached input
    # reads; longest-substring match means "gpt-5-codex" wins over "gpt-5".
    "gpt-5-codex": {
        "input": 1.25, "output": 10.0,
        "cache_creation_input_tokens": 1.25, "cache_read_input_tokens": 0.125},
    "gpt-5-mini": {
        "input": 0.25, "output": 2.0,
        "cache_creation_input_tokens": 0.25, "cache_read_input_tokens": 0.025},
    "gpt-5": {
        "input": 1.25, "output": 10.0,
        "cache_creation_input_tokens": 1.25, "cache_read_input_tokens": 0.125},
    "default": {
        "input": 3.0, "output": 15.0,
        "cache_creation_input_tokens": 3.75, "cache_read_input_tokens": 0.3},
}


# Maps each token-usage key to its price-table component.
_PRICE_COMPONENT = {
    "input_tokens": "input",
    "output_tokens": "output",
    "cache_creation_input_tokens": "cache_creation_input_tokens",
    "cache_read_input_tokens": "cache_read_input_tokens",
}


# Cost-anomaly tuning: a day is flagged when its cost exceeds the trailing
# median of all PRIOR days by this factor, once enough history exists.
_COST_ANOMALY_FACTOR = 2.0


_COST_ANOMALY_MIN_HISTORY = 3


def _price_for(model, prices):
    """Return the price entry for a model id (longest substring match wins)."""
    best_key, best_len = None, -1
    for key in prices:
        if key == "default":
            continue
        if key in (model or "") and len(key) > best_len:
            best_key, best_len = key, len(key)
    if best_key is not None:
        return prices[best_key]
    return prices.get("default", _DEFAULT_PRICES["default"])


def _usage_cost(usage, price):
    """Pure cost ($) for a usage dict under a price entry ($ per 1M tokens)."""
    total = 0.0
    for k in _USAGE_KEYS:
        comp = _PRICE_COMPONENT[k]
        total += usage.get(k, 0) * float(price.get(comp, 0.0))
    return total / 1_000_000.0


def _median(values):
    """Pure median of a numeric list (empty -> 0.0). Deterministic."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def _cost_record(usage):
    """A rollup bucket seeded from a usage dict (tokens + zeroed cost)."""
    rec = _sessionparse._empty_usage()
    _sessionparse._merge_usage(rec, usage)
    return {
        "tokens": rec,
        "total_tokens": sum(rec[k] for k in _USAGE_KEYS),
        "cost_usd": 0.0,
        "session_count": 0,
        "cache_read_ratio": 0.0,
    }


def _finalize_record(rec):
    rec["cost_usd"] = round(rec["cost_usd"], 6)
    rec["cache_read_ratio"] = _sessionparse._cache_read_ratio(rec["tokens"])


def _rollup_bucket(rollup, key):
    """The bucket for `key`, created (zeroed) on first sight. The get-or-create
    the three rollups — by_model, by_day, by_feature — each wrote for itself."""
    rec = rollup.get(key)
    if rec is None:
        rec = _cost_record(_sessionparse._empty_usage())
        rollup[key] = rec
    return rec


def _price_row_models(row, prices, by_model):
    """Price ONE row's per-model usage: returns `(row_cost, priced_models)` and
    folds each model's usage into the shared `by_model` rollup.

    Models are walked in SORTED order so the rollup's float accumulation is
    associative in a fixed sequence and two runs over the same corpus produce
    the same cents. The per-model `cost_usd` is rounded for display; `row_cost`
    accumulates UNROUNDED, so the row total is not the sum of its own rounded
    parts.
    """
    row_cost = 0.0
    priced_models = {}
    for model, usage in sorted(row.get("models", {}).items()):
        price = _price_for(model, prices)
        mcost = _usage_cost(usage, price)
        row_cost += mcost
        priced_models[model] = {
            "tokens": dict(usage),
            "total_tokens": sum(usage.get(k, 0) for k in _USAGE_KEYS),
            "cost_usd": round(mcost, 6),
        }
        mr = _rollup_bucket(by_model, model)
        _sessionparse._merge_usage(mr["tokens"], usage)
        mr["total_tokens"] = sum(mr["tokens"][k] for k in _USAGE_KEYS)
        mr["cost_usd"] += mcost
    return row_cost, priced_models


def _flag_cost_anomalies(by_day):
    """Stamp `cost_anomaly` on every day and return the flagged dates, in order.

    Walks days CHRONOLOGICALLY and compares each against the trailing median of
    all PRIOR days — so the flag is a statement about a day's own history and
    never about days that came after it. Two guards keep it from firing on
    noise: fewer than `_COST_ANOMALY_MIN_HISTORY` prior days is not a history at
    all, and a zero median would make every non-zero day an infinite multiple.
    """
    anomaly_dates = []
    history = []
    for date in sorted(by_day):
        cost = by_day[date]["cost_usd"]
        flagged = False
        if len(history) >= _COST_ANOMALY_MIN_HISTORY:
            med = _median(history)
            flagged = bool(med > 0 and cost > med * _COST_ANOMALY_FACTOR)
        by_day[date]["cost_anomaly"] = flagged
        if flagged:
            anomaly_dates.append(date)
        history.append(cost)
    return anomaly_dates


def apply_pricing(rows, agg, prices):
    """Derive PURE cost/cache/model/feature/day rollups onto rows + aggregate.

    Mutates each row in `rows` (adds per-model cost + row cost_usd) and `agg`
    (adds cost_usd, cache_read_ratio, by_model, by_day, by_feature and a
    cost-anomaly flag per day). No collection, no I/O — derived only from the
    already-parsed token usage. Deterministic."""
    by_model = {}
    by_day = {}
    by_feature = {}

    for r in rows:
        row_cost, priced_models = _price_row_models(r, prices, by_model)
        # If a row had usage but no per-model breakdown (no model on lines),
        # price the whole row under the default entry so cost is never under-counted.
        if not r.get("models") and r.get("total_tokens"):
            row_cost += _usage_cost(r["tokens"], _price_for("", prices))
        r["models"] = priced_models
        r["cost_usd"] = round(row_cost, 6)

        date = r.get("date")
        if date:
            dr = _rollup_bucket(by_day, date)
            _sessionparse._merge_usage(dr["tokens"], r["tokens"])
            dr["cost_usd"] += row_cost
            dr["session_count"] += 1

        fr = _rollup_bucket(by_feature, r.get("feature") or "unknown")
        _sessionparse._merge_usage(fr["tokens"], r["tokens"])
        fr["cost_usd"] += row_cost
        fr["session_count"] += 1

    # Finalize BEFORE the anomaly walk: it compares ROUNDED day costs, so a walk
    # that ran first would judge a day by a value the report never shows.
    for rollup in (by_model, by_day, by_feature):
        for rec in rollup.values():
            _finalize_record(rec)

    anomaly_dates = _flag_cost_anomalies(by_day)

    agg["cost_usd"] = round(sum(r["cost_usd"] for r in rows), 6)
    agg["cache_read_ratio"] = _sessionparse._cache_read_ratio(agg["tokens"])
    agg["by_model"] = by_model
    agg["by_day"] = by_day
    agg["by_feature"] = by_feature
    agg["cost_anomaly_dates"] = anomaly_dates


def load_price_table(path):
    """Load + validate a JSON price table; returns (prices, error_or_None)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return None, "could not read price table %s: %s" % (path, e)
    if not isinstance(data, dict) or not data:
        return None, "price table must be a non-empty JSON object"
    for model, entry in data.items():
        if not isinstance(entry, dict):
            return None, "price entry for %r must be an object" % model
    if "default" not in data:
        # merge over defaults so a partial table still prices unknown models
        merged = dict(_DEFAULT_PRICES)
        merged.update(data)
        return merged, None
    return data, None


_EXPORTS = [
    '_DEFAULT_CONTEXT_WINDOW',
    '_CONTEXT_WINDOW_1M',
    '_TRUNCATION_RISK_THRESHOLD',
    '_model_context_window',
    '_context_window_for_rows',
    '_DEFAULT_PRICES',
    '_PRICE_COMPONENT',
    '_COST_ANOMALY_FACTOR',
    '_COST_ANOMALY_MIN_HISTORY',
    '_price_for',
    '_usage_cost',
    '_median',
    '_cost_record',
    '_finalize_record',
    'apply_pricing',
    'load_price_table',
]
