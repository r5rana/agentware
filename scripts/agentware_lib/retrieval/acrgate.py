"""agentware_lib.retrieval.acrgate - the deterministic ACR win gate (layer: retrieval).

Owns: the Phase-3.2 ACR win gate - the pure, table-driven decision rule that decides
whether `bm25+acr` should become the agent-facing DEFAULT ranking. The five-part gate
thresholds (ACR_GATE_MARGIN nDCG lift; ACR_GATE_ALPHA t-test significance;
ACR_GATE_RECALL_EPS / ACR_GATE_MRR_EPS / ACR_GATE_LATENCY_BUDGET_MS no-harm slacks;
ACR_GATE_OLD_AGE_DAYS / ACR_GATE_STRATUM_EPS age-stratum thresholds), the two-tailed
Student-t critical-value TABLE (_T_CRIT, _T_CRIT_INF) and its conservative lookup
(t_critical), the paired t-test (paired_t_test), and the decision rule itself
(acr_gate_decision -> the full 5-part {passed, checks} verdict over an evaluate_ablation
result). Pure stdlib - NO scipy: the t-test uses a critical-value table, not a continuous
CDF, so the same inputs always yield the same verdict (INV-1).
Does NOT own: the ablation that produces its input (evaluate_ablation ->
agentware_lib.retrieval.evalcmd); the ACR
*prior* that re-ranks results (source_weight/freshness/acr -> agentware_lib.retrieval.acr,
a DIFFERENT thing - the prior scores an entry, this gate decides whether the prior wins);
the ledger row + auto-flip that RECORD a decision (build_acr_gate_row / record_acr_gate /
active_default_strategy, resident - they forward-reference the still-resident ledger
schema/reliability/redaction helpers, so they ride with the ledger seam); the eval CLI
handler (cmd_eval, now agentware_lib.retrieval.evalcmd, wave-3 seam 16b) and the `eval`
parser registrar's --acr-margin/--acr-alpha defaults - they reach acr_gate_decision via the
_acrgate alias and read ACR_GATE_MARGIN / ACR_GATE_ALPHA via a by-name import into evalcmd.
Layer deps: retrieval -> (stdlib `math` only). Imports nothing from core/, knowledge/, or
sibling retrieval modules; nothing in core/ or knowledge/ imports from here.
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

INV-1: acr_gate_decision is PURE given (ablation, determinism_ok) - the only
externally-measured input (the bm25+acr twin-run determinism flag) is passed in, so the
same ablation always yields the same verdict; the t-test is table-driven (no floating CDF).
INV-2: read-only - it computes over the in-memory ablation dict and never touches the KB,
index.json, or any file.
"""

import math


ACR_GATE_MARGIN = 0.01             # min mean nDCG@k lift (treatment - baseline)
ACR_GATE_ALPHA = 0.05              # paired t-test two-tailed significance level
ACR_GATE_RECALL_EPS = 0.0          # Recall@k no-harm slack (treat >= base - eps)
ACR_GATE_MRR_EPS = 0.0             # MRR no-harm slack (treat >= base - eps)
ACR_GATE_LATENCY_BUDGET_MS = 10.0  # p50 latency slack (treat_p50 <= base_p50 + budget)
ACR_GATE_OLD_AGE_DAYS = 90         # answer_age_days >= this => OLD-answer stratum
ACR_GATE_STRATUM_EPS = 1e-9        # OLD-stratum nDCG no-regression slack

# Two-tailed Student-t critical values, keyed by df, for alpha in {0.05, 0.01}.
# For a df BETWEEN tabulated rows we use the largest tabulated df <= our df, which
# is CONSERVATIVE (a smaller df => a larger critical value => a stricter gate).
# A df above the table uses the normal-approx asymptote (df -> inf).
_T_CRIT = {
    0.05: {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
           7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179,
           13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101,
           19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064,
           25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
           40: 2.021, 50: 2.009, 60: 2.000, 80: 1.990, 100: 1.984, 120: 1.980},
    0.01: {1: 63.657, 2: 9.925, 3: 5.841, 4: 4.604, 5: 4.032, 6: 3.707,
           7: 3.499, 8: 3.355, 9: 3.250, 10: 3.169, 11: 3.106, 12: 3.055,
           13: 3.012, 14: 2.977, 15: 2.947, 16: 2.921, 17: 2.898, 18: 2.878,
           19: 2.861, 20: 2.845, 21: 2.831, 22: 2.819, 23: 2.807, 24: 2.797,
           25: 2.787, 26: 2.779, 27: 2.771, 28: 2.763, 29: 2.756, 30: 2.750,
           40: 2.704, 50: 2.678, 60: 2.660, 80: 2.639, 100: 2.626, 120: 2.617},
}
_T_CRIT_INF = {0.05: 1.960, 0.01: 2.576}


def t_critical(df, alpha=ACR_GATE_ALPHA):
    """Two-tailed Student-t critical value from a table (stdlib, no scipy).

    For a df not tabulated we pick the largest tabulated df <= our df, which is
    CONSERVATIVE (smaller df => larger critical value => harder to pass). A df
    above the table uses the normal-approx asymptote. Raises ValueError for an
    unsupported alpha or df < 1.
    """
    table = _T_CRIT.get(alpha)
    if table is None:
        raise ValueError("t_critical: unsupported alpha %r (have %s)"
                         % (alpha, sorted(_T_CRIT)))
    if df < 1:
        raise ValueError("t_critical: df must be >= 1, got %r" % (df,))
    keys = sorted(table)
    if df > keys[-1]:
        return _T_CRIT_INF[alpha]
    chosen = keys[0]
    for k in keys:
        if k <= df:
            chosen = k
        else:
            break
    return table[chosen]


def paired_t_test(deltas):
    """Paired t-test on per-query deltas (treatment - baseline). Pure stdlib.

    Returns {n, df, mean, sd, se, t}. The t-statistic is mean / (sd / sqrt(n))
    with df = n - 1 and sd the sample (ddof=1) standard deviation. Degenerate
    cases: n < 2 => t = 0, df = 0 (cannot test); sd == 0 => t = 0 when the mean
    is 0, else +/-inf (a perfectly consistent non-zero shift). Deterministic.
    """
    n = len(deltas)
    if n == 0:
        return {"n": 0, "df": 0, "mean": 0.0, "sd": 0.0, "se": 0.0, "t": 0.0}
    mean = sum(deltas) / n
    if n < 2:
        return {"n": n, "df": 0, "mean": mean, "sd": 0.0, "se": 0.0, "t": 0.0}
    var = sum((d - mean) ** 2 for d in deltas) / (n - 1)
    sd = math.sqrt(var)
    se = sd / math.sqrt(n)
    if se == 0.0:
        t = 0.0 if mean == 0.0 else math.copysign(float("inf"), mean)
    else:
        t = mean / se
    return {"n": n, "df": n - 1, "mean": mean, "sd": sd, "se": se, "t": t}


def acr_gate_decision(ablation, margin=ACR_GATE_MARGIN, alpha=ACR_GATE_ALPHA,
                      recall_eps=ACR_GATE_RECALL_EPS, mrr_eps=ACR_GATE_MRR_EPS,
                      latency_budget_ms=ACR_GATE_LATENCY_BUDGET_MS,
                      old_age_days=ACR_GATE_OLD_AGE_DAYS,
                      stratum_eps=ACR_GATE_STRATUM_EPS, determinism_ok=False):
    """Apply the deterministic 5-part ACR win gate to a bm25-vs-bm25+acr ablation.

    `ablation` is an `evaluate_ablation` result (baseline + treatment headline
    metrics + per-query nDCG and `answer_age_days` on each row). ALL five checks
    must hold for `passed`. Pure given (ablation, determinism_ok): the only
    externally-measured input is `determinism_ok` (the bm25+acr twin-run equality
    at the pinned `as_of`), passed in so this stays a pure, table-driven function.
    """
    base_name = ablation.get("baseline")
    treat_name = ablation.get("treatment")
    strategies = ablation.get("strategies", {})
    base_m = strategies.get(base_name, {})
    treat_m = strategies.get(treat_name, {})
    pq = ablation.get("per_query", {})
    base_pq = pq.get(base_name, [])
    treat_pq = pq.get(treat_name, [])

    # Per-query nDCG deltas (aligned by gold order).
    deltas = [t.get("ndcg_at_k", 0.0) - b.get("ndcg_at_k", 0.0)
              for b, t in zip(base_pq, treat_pq)]

    # 1. Primary: mean nDCG@k lift >= margin.
    delta_ndcg = treat_m.get("ndcg_at_k", 0.0) - base_m.get("ndcg_at_k", 0.0)
    primary_ok = delta_ndcg >= margin

    # 2. Significance: paired t-test, lift in the right direction AND |t| >= t_crit.
    tt = paired_t_test(deltas)
    tcrit = t_critical(tt["df"], alpha) if tt["df"] >= 1 else float("inf")
    sig_ok = tt["df"] >= 1 and tt["mean"] > 0.0 and tt["t"] >= tcrit

    # 3. Win-rate (descriptive sign check): improved > regressed.
    improved = sum(1 for d in deltas if d > 0)
    regressed = sum(1 for d in deltas if d < 0)
    ties = sum(1 for d in deltas if d == 0)
    winrate_ok = improved > regressed

    # 4. No-harm guardrails: Recall@k, MRR, p50 latency, determinism.
    recall_ok = (treat_m.get("recall_at_k", 0.0)
                 >= base_m.get("recall_at_k", 0.0) - recall_eps)
    mrr_ok = treat_m.get("mrr", 0.0) >= base_m.get("mrr", 0.0) - mrr_eps
    latency_ok = (treat_m.get("latency_ms_p50", 0.0)
                  <= base_m.get("latency_ms_p50", 0.0) + latency_budget_ms)
    no_harm_ok = recall_ok and mrr_ok and latency_ok and bool(determinism_ok)

    # 5. Age-stratified no-regression on the OLD-answer stratum.
    old_base, old_treat = [], []
    for b, t in zip(base_pq, treat_pq):
        age = b.get("answer_age_days")
        if isinstance(age, (int, float)) and not isinstance(age, bool) \
                and age >= old_age_days:
            old_base.append(b.get("ndcg_at_k", 0.0))
            old_treat.append(t.get("ndcg_at_k", 0.0))
    old_n = len(old_base)
    _mean = lambda xs: (sum(xs) / len(xs)) if xs else 0.0
    old_base_ndcg = _mean(old_base)
    old_treat_ndcg = _mean(old_treat)
    # Vacuously passes when the stratum is empty (recorded via old_count).
    stratum_ok = (old_n == 0) or (old_treat_ndcg >= old_base_ndcg - stratum_eps)

    passed = bool(primary_ok and sig_ok and winrate_ok and no_harm_ok
                  and stratum_ok)
    return {
        "passed": passed,
        "baseline": base_name,
        "treatment": treat_name,
        "params": {"margin": margin, "alpha": alpha, "recall_eps": recall_eps,
                   "mrr_eps": mrr_eps, "latency_budget_ms": latency_budget_ms,
                   "old_age_days": old_age_days, "stratum_eps": stratum_eps},
        "checks": {
            "primary": {"passed": bool(primary_ok), "delta_ndcg": delta_ndcg,
                        "margin": margin},
            "significance": {"passed": bool(sig_ok), "t": tt["t"], "df": tt["df"],
                             "t_crit": tcrit, "mean_delta": tt["mean"],
                             "n": tt["n"], "alpha": alpha},
            "win_rate": {"passed": bool(winrate_ok), "improved": improved,
                         "regressed": regressed, "ties": ties},
            "no_harm": {
                "passed": bool(no_harm_ok),
                "recall": {"passed": bool(recall_ok),
                           "baseline": base_m.get("recall_at_k"),
                           "treatment": treat_m.get("recall_at_k")},
                "mrr": {"passed": bool(mrr_ok), "baseline": base_m.get("mrr"),
                        "treatment": treat_m.get("mrr")},
                "latency_p50": {"passed": bool(latency_ok),
                                "baseline": base_m.get("latency_ms_p50"),
                                "treatment": treat_m.get("latency_ms_p50"),
                                "budget_ms": latency_budget_ms},
                "determinism_ok": bool(determinism_ok),
            },
            "age_stratum": {"passed": bool(stratum_ok), "old_count": old_n,
                            "old_ndcg_baseline": old_base_ndcg,
                            "old_ndcg_treatment": old_treat_ndcg,
                            "recent_count": len(deltas) - old_n,
                            "old_age_days": old_age_days},
        },
    }


_EXPORTS = [
    "ACR_GATE_MARGIN",
    "ACR_GATE_ALPHA",
    "ACR_GATE_RECALL_EPS",
    "ACR_GATE_MRR_EPS",
    "ACR_GATE_LATENCY_BUDGET_MS",
    "ACR_GATE_OLD_AGE_DAYS",
    "ACR_GATE_STRATUM_EPS",
    "_T_CRIT",
    "_T_CRIT_INF",
    "t_critical",
    "paired_t_test",
    "acr_gate_decision",
]
