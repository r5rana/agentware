"""agentware_lib.retrieval.regressiongate.gatebaseline — the INV-7 absolute
no-regression gate (layer: retrieval).

Owns: the deterministic no-regression gate guarding every package update
(`eval --gate`). The gated headline-metric set (GATE_METRICS); the
best-comparable-baseline selector (gate_baseline — MAX per metric among prior rows
sharing strategy+suite+corpus_fingerprint); the pass/fail-within-tolerance decision
(evaluate_gate); the --record/--gate orchestrator that reads the prior ledger BEFORE
appending so a fresh row never gates against itself (_record_and_gate); the gate
exit-code (_gate_exit) and the human-readable gate printer (_print_gate).

Does NOT own: the clean-HEAD vs working-tree A/B gate, which answers a DIFFERENT
question (`is THIS diff responsible?` rather than `is this run worse than the best
ever seen?`) and is the sibling agentware_lib.retrieval.regressiongate.abgate — the
split that made this a package; the append-only benchmark ledger it reads + writes
(HISTORY_REL / _read_ledger / record_ledger -> agentware_lib.retrieval.ledger,
referenced via the _ledger module alias); the ranking engine + gold-fixture scorer
feeding a gate run (agentware_lib.retrieval.{metrics_rank,goldfixture}); the eval CLI
handlers (agentware_lib.retrieval.evalcmd), which call these through the
_regressiongate module alias by attribute — so the package __init__ must keep every
one of these names re-exported.
Layer deps: retrieval -> retrieval (agentware_lib.retrieval.ledger as _ledger,
referenced by ATTRIBUTE so the global-resolve gate never walks a foreign sibling
function held in this module's __dict__). Imports nothing in ops/features/knowledge.

INV-7: gate_baseline + evaluate_gate are pure over the injected ledger rows, so at a
fixed ledger the gate verdict is deterministic; a first-ever comparable run seeds +
PASSES (never a false-fail on corpus growth). _gate_exit is the ONE place the two
gates meet: an A/B FAIL forces exit 1 independently of the absolute verdict.
"""

import os

from agentware_lib.retrieval import ledger as _ledger


GATE_METRICS = ("recall_at_k", "precision_at_k", "ndcg_at_k", "mrr")

def gate_baseline(rows, strategy, suite=None, corpus_fingerprint=None):
    """Best-seen value per gated metric + reliability among COMPARABLE rows.

    Comparable = rows recorded with the same retrieval `strategy` AND the same
    benchmark `suite` AND the same `corpus_fingerprint` (apples to apples: a bm25
    run is never gated against a tag-only baseline, a session-level LongMemEval
    row is never gated against an own-gold-set row even when both are
    strategy="bm25", AND an own-gold run is never gated against a row scored over
    a DIFFERENT corpus). `suite=None` selects the historical own-gold-set rows
    (which carry no/None suite).

    The `corpus_fingerprint` filter is what makes own-gold gating REPRODUCIBLE:
    the live operator KB grows over time, so a row recorded against a smaller
    corpus has a different fingerprint and is NOT comparable to a run over the
    grown corpus -> the gate seeds a fresh baseline and PASSES instead of
    false-failing on corpus drift (byte-identical code). Legacy rows recorded
    before fingerprinting carry no `corpus_fingerprint` (None) and so form their
    own distinct bucket -> they never gate a new fingerprinted run. Callers that
    omit `corpus_fingerprint` (None) match only rows whose fingerprint is also
    None (legacy semantics preserved).

    The baseline is the MAX of each metric across all prior comparable rows, so a
    run can never silently drop below any earlier peak. Returns None when there
    is no comparable history (first-ever run for this strategy+suite+corpus ->
    seeds + passes).
    """
    comparable = [r for r in rows
                  if r.get("strategy") == strategy and r.get("suite") == suite
                  and r.get("corpus_fingerprint") == corpus_fingerprint]
    if not comparable:
        return None
    base = {}
    for key in GATE_METRICS:
        vals = [r.get("metrics", {}).get(key) for r in comparable]
        vals = [v for v in vals if isinstance(v, (int, float))]
        if vals:
            base[key] = max(vals)
    rels = [r.get("reliability") for r in comparable
            if isinstance(r.get("reliability"), (int, float))]
    if rels:
        base["reliability"] = max(rels)
    return base

def evaluate_gate(metrics, reliability, baseline, tolerance):
    """Decide pass/fail vs a baseline. Returns (passed, regressions).

    Each gated metric must stay within `tolerance` of the best prior value
    (current >= best - tolerance). Reliability is a 0-100 score, so its
    allowance is `tolerance * 100` points. `baseline is None` => first run =>
    pass with no regressions (caller marks it seeded). `regressions` is a list of
    (metric, baseline_value, current_value) for every metric that dropped too far.
    """
    if baseline is None:
        return True, []
    regressions = []
    for key in GATE_METRICS:
        if key not in baseline:
            continue
        cur = metrics.get(key)
        if not isinstance(cur, (int, float)):
            continue
        if cur < baseline[key] - tolerance:
            regressions.append((key, baseline[key], cur))
    if "reliability" in baseline and isinstance(reliability, (int, float)):
        if reliability < baseline["reliability"] - tolerance * 100.0:
            regressions.append(
                ("reliability", baseline["reliability"], reliability))
    return (len(regressions) == 0), regressions

def _record_and_gate(args, kdir, data, gold, gold_path, result):
    """Apply --record / --gate to a computed eval result.

    --gate implies --record (every gated run appends an immutable row). Reads the
    prior ledger BEFORE appending so the new row never compares against itself.
    Returns (recorded_or_None, gate_or_None); attaches `recorded`/`gate` to
    `result` for the JSON output.
    """
    want_gate = getattr(args, "gate", False)
    if not (getattr(args, "record", False) or want_gate):
        return None, None
    ledger_path = os.path.join(kdir, _ledger.HISTORY_REL)
    prior_rows = _ledger._read_ledger(ledger_path) if want_gate else None
    recorded = _ledger.record_ledger(kdir, data, gold, gold_path, result, args.top_k)
    result["recorded"] = {
        "path": ledger_path, "commit": recorded["commit"],
        "run": recorded["run"], "reliability": recorded["reliability"],
    }
    gate = None
    if want_gate:
        baseline = gate_baseline(prior_rows, recorded["strategy"],
                                 recorded.get("suite"),
                                 recorded.get("corpus_fingerprint"))
        passed, regressions = evaluate_gate(
            recorded["metrics"], recorded["reliability"],
            baseline, args.tolerance)
        gate = {
            "passed": passed,
            "tolerance": args.tolerance,
            "strategy": recorded["strategy"],
            "seeded": baseline is None,
            "baseline": baseline,
            "regressions": [
                {"metric": m, "baseline": b, "current": c}
                for m, b, c in regressions
            ],
        }
        result["gate"] = gate
    return recorded, gate

def _gate_exit(gate, ab=None):
    """Exit code for a gate result: non-zero (1) only on a real regression.

    When an A/B result is supplied (`eval --gate --ab`), a real A/B FAIL also
    forces exit 1 — the clean-HEAD vs working-tree comparison is an independent
    regression signal layered on top of the absolute baseline gate.
    """
    code = 0 if (gate is None or gate["passed"]) else 1
    if ab is not None and not ab["passed"]:
        code = 1
    return code

def _print_gate(gate):
    """Human-readable gate verdict for text format."""
    if gate is None:
        return
    if gate["seeded"]:
        print("  gate: PASS (seeded baseline; no prior %s history)"
              % gate["strategy"])
        return
    if gate["passed"]:
        print("  gate: PASS (no regression beyond tolerance %.4f)"
              % gate["tolerance"])
        return
    print("  gate: FAIL (regression beyond tolerance %.4f)" % gate["tolerance"])
    for r in gate["regressions"]:
        print("    %-16s best=%.4f  current=%.4f  drop=%.4f" % (
            r["metric"], r["baseline"], r["current"],
            r["baseline"] - r["current"]))

# Re-exported BY NAME on the entrypoint facade (the M3/_EXPORTS convention) so the
# tests still reach them as CLI.<name>, and aggregated by the package __init__ so
# every pre-split `_regressiongate.<name>` call site resolves unchanged.
_EXPORTS = [
    'GATE_METRICS',
    'gate_baseline',
    'evaluate_gate',
    '_record_and_gate',
    '_gate_exit',
    '_print_gate',
]
