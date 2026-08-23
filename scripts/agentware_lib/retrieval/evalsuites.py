"""agentware_lib.retrieval.evalsuites -- the `eval` verb's GUARDS and REPORTS (layer: retrieval).

Owns: the input guards every `eval` scoring arm shares (validate_scoring_args --
the --top-k / --as-of pair, returning the RESOLVED as_of so the caller cannot
default it a second way; validate_lme_args -- the longmemeval dataset resolution
plus --limit, wrapped around the shared pair so the four refusals keep their
ORDER); the ONE record-and-gate block both benchmark suites run
(record_and_gate_suite, parameterised by suite name -- it was a hand-copy at two
sites differing in nothing but that string); and the four HUMAN report renderers
the `eval` handler family prints (print_longmemeval_report,
print_gold_fixture_report, print_ablate_report, print_default_report) together
with the recorded-row line they share (print_recorded_line).

Why a sibling module rather than an in-place split (task 99 wave 10): `evalcmd`
was at 592 of its 600-line cap, so the three ceiling rows on its handler family
could not be paid down by adding helpers to it -- the seam recorded on those
rows names this module explicitly. The direction is one-way and deliberate:
`evalcmd` imports THIS module, never the reverse. The ablation instrument
(evaluate_ablation) stays in `evalcmd`, so a renderer here never needs to reach
back for it and no import cycle is possible.

Does NOT own: the `eval` CLI handlers or the ablation instrument (cmd_eval,
_run_acr_gate, _run_longmemeval_suite, _run_gold_fixture_suite,
_run_ablate_suite, _run_default_suite, evaluate_ablation, the ACR-gate printers
_ok / _print_acr_gate and the registrar register_eval ->
agentware_lib.retrieval.evalcmd, which imports this module); the append-only
ledger + its suite recorder (record_ledger_suite / HISTORY_REL ->
agentware_lib.retrieval.ledger); the regression gate's baseline picker, verdict
and exit-code mapper (gate_baseline / evaluate_gate / _print_gate / _gate_exit ->
agentware_lib.retrieval.regressiongate); the scorers (evaluate_longmemeval /
evaluate_gold_fixture / evaluate -> agentware_lib.retrieval.{longmemeval,
goldfixture,metrics_rank}) or the scorecard writer
(agentware_lib.retrieval.scorecard). The two siblings this module DOES reach are
held via the `_ledger` / `_regressiongate` module aliases and referenced by
ATTRIBUTE so the bytecode global-resolve gate never walks a foreign sibling
function held in this module's __dict__; the two CONSTANTS these guards read (HISTORY_REL,
_LME_DEFAULT_REL) are imported BY NAME, which is gate-clean because a constant
has no code object for the resolver to walk.

Layer deps: retrieval -> core (dates; module level) + the sibling
retrieval.{ledger,regressiongate} (by module alias, attribute reads) +
retrieval.longmemeval for one constant (HISTORY_REL and _LME_DEFAULT_REL by
name -- gate-clean, a constant has no code object). No knowledge, no ops/features/cli import, and no lazy import either --
nothing here needs the index. _EXPORTS lists the guards, the record-and-gate
block and the five renderers for the entrypoint by-name facade.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import os
import sys

import agentware_lib.core.dates as dates
from agentware_lib.retrieval import ledger as _ledger
from agentware_lib.retrieval import regressiongate as _regressiongate
from agentware_lib.retrieval.ledger import HISTORY_REL
from agentware_lib.retrieval.longmemeval import _LME_DEFAULT_REL


# --- input guards -------------------------------------------------------------

def validate_scoring_args(args):
    """Validate --top-k and --as-of. Returns `(as_of, err)`; err is None on pass.

    Returns the RESOLVED `as_of` rather than a bare verdict, and that is the
    point of returning a pair: the default (`dates._today()`) and the validation
    are one decision, and a caller that got back only "valid" would have to
    re-derive the value -- which is how two arms of the same command end up
    scoring at two different dates.

    `as_of` is read with getattr because a handler may be called with a
    namespace that never carried the attribute (the release gate builds one);
    `top_k` is read directly because every path that reaches a scorer has it.
    Order is fixed: top-k first, then as-of, so an invocation that is wrong in
    BOTH ways reports the same refusal it always did.
    """
    if args.top_k <= 0:
        sys.stderr.write("agentware: --top-k must be a positive integer\n")
        return None, 1
    as_of = getattr(args, "as_of", None) or dates._today()
    if not dates._is_valid_date(as_of):
        sys.stderr.write("agentware: --as-of must be a YYYY-MM-DD date\n")
        return None, 1
    return as_of, None


def validate_lme_args(args, kdir):
    """Resolve + validate the `--suite longmemeval` inputs.

    Returns `(dataset_path, as_of, err)`; err is None on pass.

    FOUR refusals in a FIXED order -- dataset-exists, then --top-k, then --as-of,
    then --limit -- and the order is a contract, not an accident: an invocation
    can be wrong in several of those ways at once, and which message it gets is
    what an operator debugs against. The dataset check comes first because it is
    the one with a remedy (acquire the file, or pass --dataset), so it is the
    most useful thing to say to someone who has none of the four right.

    `--limit` is checked LAST and only for positivity: `None` means "score them
    all" and is not an error, while `0` is, because a limit of zero scores
    nothing and would report a perfect run over an empty question set.
    """
    dataset_path = (getattr(args, "dataset", None)
                    or os.path.join(kdir, _LME_DEFAULT_REL))
    if not os.path.exists(dataset_path):
        sys.stderr.write(
            "agentware: longmemeval dataset not found: %s\n"
            "  acquire it per benchmarks/longmemeval/DATASET.md, or pass "
            "--dataset <path>\n" % dataset_path)
        return None, None, 1
    as_of, err = validate_scoring_args(args)
    if err is not None:
        return None, None, err
    limit = getattr(args, "limit", None)
    if limit is not None and limit <= 0:
        sys.stderr.write("agentware: --limit must be a positive integer\n")
        return None, None, 1
    return dataset_path, as_of, None


# --- the shared record-and-gate block -----------------------------------------

def record_and_gate_suite(args, kdir, data, result, suite):
    """Append the suite's ledger row and, with --gate, decide the regression.

    Returns `(recorded, gate)`, either of which may be None. MUTATES `result`:
    it stamps `result["recorded"]` and, when gating, `result["gate"]` -- which
    is not a side effect to tidy away, it is the contract, because `--format
    json` serializes `result` and those two keys are how a consumer reads what
    was written and what the gate decided.

    This was a hand-copy at TWO sites (`--suite longmemeval` and
    `--suite gold-fixture`) that differed in exactly one character sequence: the
    `suite` string stamped on the gate verdict. It is a parameter now, so the
    two suites cannot drift apart in what they record.

    Three orderings inside are load-bearing:

      * PRIOR ROWS ARE READ BEFORE THE NEW ONE IS WRITTEN. `_read_ledger` runs
        first, so this run's own row can never become its own baseline -- which
        would make every gate pass by comparing a run to itself.
      * `--gate` IMPLIES `--record`. The row is appended whenever either flag is
        set, so a gated run is always in the ledger it was judged against.
      * A GATE WITH NO PRIOR IS `seeded`, NOT a failure. `gate_baseline`
        returning None is the first-ever comparable run, and `seeded` says so on
        the verdict rather than reporting a pass nobody measured.
    """
    want_gate = getattr(args, "gate", False)
    recorded, gate = None, None
    if getattr(args, "record", False) or want_gate:
        ledger_path = os.path.join(kdir, HISTORY_REL)
        prior_rows = _ledger._read_ledger(ledger_path) if want_gate else None
        recorded = _ledger.record_ledger_suite(kdir, data, result)
        result["recorded"] = {
            "path": ledger_path, "commit": recorded["commit"],
            "run": recorded["run"], "reliability": recorded["reliability"],
        }
        if want_gate:
            baseline = _regressiongate.gate_baseline(
                prior_rows, recorded["strategy"], recorded.get("suite"),
                recorded.get("corpus_fingerprint"))
            passed, regressions = _regressiongate.evaluate_gate(
                recorded["metrics"], recorded["reliability"], baseline,
                args.tolerance)
            gate = {
                "passed": passed, "tolerance": args.tolerance,
                "strategy": recorded["strategy"], "suite": suite,
                "seeded": baseline is None, "baseline": baseline,
                "regressions": [
                    {"metric": m, "baseline": b, "current": c}
                    for m, b, c in regressions],
            }
            result["gate"] = gate
    return recorded, gate


# --- human report renderers ---------------------------------------------------

def print_recorded_line(recorded, kdir):
    """The one-line "this run is in the ledger" note, shared by four reports.

    A no-op when nothing was recorded, so every caller can print it
    unconditionally instead of each repeating the same guard.
    """
    if recorded is not None:
        print("  recorded: %s  commit=%s  reliability=%.2f  run=%s" % (
            os.path.join(kdir, HISTORY_REL), recorded["commit"],
            recorded["reliability"], recorded["run"]))


def print_longmemeval_report(result, dataset_path, recorded, kdir):
    """The `eval --suite longmemeval` human report.

    Two things here are the whole reason this suite exists and are printed as
    SEPARATE blocks rather than folded into the headline: the per-CATEGORY
    table, sorted by name so two runs diff cleanly, and the ABSTENTION count,
    which is excluded from Recall by construction and reported on its own line
    -- a suite that folded abstentions into the aggregate would report a
    recall figure for questions that have no correct retrieval.
    """
    m = result["metrics"]
    k = result["top_k"]
    ab = result["abstention"]
    print("eval --suite longmemeval: strategy=%s  top_k=%d  answerable=%d" % (
        result["strategy"], k, result["num_queries"]))
    print("  dataset: %s" % dataset_path)
    print("  as_of  : %s" % result["as_of"])
    print("  Recall@%d     : %.4f" % (k, m["recall_at_k"]))
    print("  Precision@%d  : %.4f" % (k, m["precision_at_k"]))
    print("  nDCG@%d       : %.4f" % (k, m["ndcg_at_k"]))
    print("  MRR           : %.4f" % m["mrr"])
    print("  latency (ms)  : mean=%.3f  p50=%.3f" % (
        m["latency_ms_mean"], m["latency_ms_p50"]))
    print("  determinism   : %s" % ("ok" if result["determinism_ok"] else "FAIL"))
    print("  per-category (Recall@%d / nDCG@%d / MRR / n):" % (k, k))
    for cat in sorted(result["per_category"]):
        c = result["per_category"][cat]
        print("    %-26s R=%.4f  nDCG=%.4f  MRR=%.4f  n=%d" % (
            cat, c["recall_at_k"], c["ndcg_at_k"], c["mrr"], c["num_queries"]))
    print("  abstention (excluded from Recall; reported separately): %d" % (
        ab["count"]))
    for cat in sorted(ab["by_category"]):
        print("    %-26s n=%d" % (cat, ab["by_category"][cat]))
    print_recorded_line(recorded, kdir)


def print_gold_fixture_report(result, fixture_path, recorded, kdir):
    """The `eval --suite gold-fixture` human report.

    Prints the corpus FINGERPRINT, which the other reports do not, because it
    is what makes this suite's absolute gate meaningful: the corpus ships frozen
    in the repo, so a fingerprint that moved means the fixture moved, not that
    the operator's KB grew.
    """
    m = result["metrics"]
    k = result["top_k"]
    print("eval --suite gold-fixture: strategy=%s  top_k=%d  queries=%d" % (
        result["strategy"], k, result["num_queries"]))
    print("  fixture: %s" % fixture_path)
    print("  as_of  : %s" % result["as_of"])
    print("  fingerprint: %s" % result.get("corpus_fingerprint"))
    print("  Recall@%d     : %.4f" % (k, m["recall_at_k"]))
    print("  Precision@%d  : %.4f" % (k, m["precision_at_k"]))
    print("  nDCG@%d       : %.4f" % (k, m["ndcg_at_k"]))
    print("  MRR           : %.4f" % m["mrr"])
    print("  determinism   : %s" % ("ok" if result["determinism_ok"] else "FAIL"))
    print_recorded_line(recorded, kdir)


def print_ablate_report(result, gold_path, recorded, kdir):
    """The `eval --ablate` per-metric lift table.

    The delta column is printed with an explicit `%+` sign so a regression reads
    as one at a glance; the three columns are the baseline, the treatment and
    `treatment - baseline`, in that order, and the header names the two
    strategies rather than saying "baseline"/"treatment", so a table pasted into
    a worklog still says what it compared.
    """
    k = result["top_k"]
    base, treat = result["baseline"], result["treatment"]
    bm, tm = result["strategies"][base], result["strategies"][treat]
    d = result["delta"]
    print("eval --ablate: %s (baseline) vs %s  top_k=%d  queries=%d" % (
        base, treat, k, result["num_queries"]))
    print("  gold: %s" % gold_path)
    labels = [
        ("Recall@%d" % k, "recall_at_k"),
        ("Precision@%d" % k, "precision_at_k"),
        ("nDCG@%d" % k, "ndcg_at_k"),
        ("MRR", "mrr"),
        ("latency_ms_mean", "latency_ms_mean"),
        ("latency_ms_p50", "latency_ms_p50"),
        ("ctx_tokens_mean", "context_tokens_mean"),
    ]
    print("  %-16s %12s %12s %12s" % ("metric", base, treat, "delta"))
    for label, key in labels:
        print("  %-16s %12.4f %12.4f %+12.4f" % (
            label, bm[key], tm[key], d[key]))
    print_recorded_line(recorded, kdir)


def print_default_report(result, gold_path, recorded, kdir):
    """The plain `eval` human report over the operator's own gold set.

    The one report that prints `ctx tokens`: the own-gold arm is the only one
    whose corpus is the operator's real KB, so the context footprint of a
    retrieval is a number that means something to them.
    """
    m = result["metrics"]
    k = result["top_k"]
    print("eval: strategy=%s  top_k=%d  queries=%d" % (
        result["strategy"], k, result["num_queries"]))
    print("  gold: %s" % gold_path)
    print("  Recall@%d     : %.4f" % (k, m["recall_at_k"]))
    print("  Precision@%d  : %.4f" % (k, m["precision_at_k"]))
    print("  nDCG@%d       : %.4f" % (k, m["ndcg_at_k"]))
    print("  MRR           : %.4f" % m["mrr"])
    print("  latency (ms)  : mean=%.3f  p50=%.3f" % (
        m["latency_ms_mean"], m["latency_ms_p50"]))
    print("  ctx tokens    : mean=%.1f" % m["context_tokens_mean"])
    print_recorded_line(recorded, kdir)


_EXPORTS = [
    "validate_scoring_args",
    "validate_lme_args",
    "record_and_gate_suite",
    "print_recorded_line",
    "print_longmemeval_report",
    "print_gold_fixture_report",
    "print_ablate_report",
    "print_default_report",
]
