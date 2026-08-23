"""agentware_lib.retrieval.evalcmd -- the `eval` verb: gold-set benchmark CLI.

Owns: the ablation instrument (_ABLATE_METRIC_KEYS + evaluate_ablation), the
`eval` CLI handler family (cmd_eval as a pure dispatcher + the suite/gate
sub-runners _run_acr_gate, _run_longmemeval_suite, _run_gold_fixture_suite,
_run_ablate_suite, _run_default_suite and the ACR-gate text printers
_ok / _print_acr_gate) and the `eval` parser registrar (register_eval, declared in
this module's `__verbs__` and resolved from there by the assembler;
handler+registrar travel together).

Does NOT own: the gold-set scorer/loader (evaluate/load_gold ->
agentware_lib.retrieval.metrics_rank), the ACR win gate (acr_gate_decision + the
ACR_GATE_* thresholds -> agentware_lib.retrieval.acrgate), the benchmark ledger
(_determinism_check_at/record_acr_gate/record_ledger_suite/_read_ledger/HISTORY_REL
-> agentware_lib.retrieval.ledger), the regression + clean-HEAD A/B gate (gate_baseline/
evaluate_gate/_record_and_gate/_gate_exit/_print_gate/_print_ab/run_ab_gate ->
agentware_lib.retrieval.regressiongate), the LongMemEval scorer (evaluate_longmemeval +
_LME_DEFAULT_REL -> agentware_lib.retrieval.longmemeval), the gold-fixture scorer
(evaluate_gold_fixture/gold_fixture_path/GOLD_FIXTURE_SUITE ->
agentware_lib.retrieval.goldfixture), the scorecard writer (write_scorecard ->
agentware_lib.retrieval.scorecard) or -- since task 99 wave 10 -- the suite GUARDS
and human REPORT renderers (validate_scoring_args/validate_lme_args/
record_and_gate_suite/print_{recorded_line,longmemeval_report,gold_fixture_report,
ablate_report,default_report} -> agentware_lib.retrieval.evalsuites, a sibling this
module imports and which never imports back, so evaluate_ablation can stay here). Sibling FUNCTIONS are held via the _metrics_rank/
_acrgate/_ledger/_regressiongate/_longmemeval/_goldfixture/_scorecard/_evalsuites module aliases
and referenced by ATTRIBUTE so the bytecode global-resolve gate never walks a foreign
sibling function held in this module's __dict__; the sibling CONSTANTS the registrar +
handlers read (ACR_GATE_MARGIN/ACR_GATE_ALPHA/HISTORY_REL/
GOLD_FIXTURE_SUITE) are imported BY NAME (gate-clean -- constants have no code object --
so register_eval and every handler body stay byte-verbatim). Nor the knowledge index --
cmd_eval imports it LAZILY (`from agentware_lib.knowledge import index`) because
retrieval->knowledge must stay function-local (layer discipline).

Layer deps: retrieval -> core (config; module level) + the sibling
retrieval.{metrics_rank,acrgate,ledger,regressiongate,longmemeval,goldfixture,scorecard,evalsuites}
(by module alias, attribute reads; the 3 remaining constants by name) + knowledge.index LAZILY
(function-local, cmd_eval only). No ops/features/cli import. _EXPORTS lists the ablation
instrument + the eval handler family for the entrypoint by-name facade (the dream
eval-capture step reaches cmd_eval as a bare name; the flat legacy oracles reach the
family as CLI.<name>); register_eval is intentionally excluded (importlib).
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os
import sys

import agentware_lib.core.config as config
from agentware_lib.retrieval import acrgate as _acrgate
from agentware_lib.retrieval import evalsuites as _evalsuites
from agentware_lib.retrieval import goldfixture as _goldfixture
from agentware_lib.retrieval import ledger as _ledger
from agentware_lib.retrieval import longmemeval as _longmemeval
from agentware_lib.retrieval import metrics_rank as _metrics_rank
from agentware_lib.retrieval import regressiongate as _regressiongate
from agentware_lib.retrieval import scorecard as _scorecard
from agentware_lib.retrieval.acrgate import ACR_GATE_MARGIN, ACR_GATE_ALPHA
from agentware_lib.retrieval.goldfixture import GOLD_FIXTURE_SUITE
from agentware_lib.retrieval.ledger import HISTORY_REL

_ABLATE_METRIC_KEYS = (
    "recall_at_k", "precision_at_k", "ndcg_at_k", "mrr",
    "latency_ms_mean", "latency_ms_p50", "context_tokens_mean",
)

def evaluate_ablation(kdir, data, gold, top_k, baseline="tag", treatment="bm25",
                      as_of=None):
    """Run the SAME gold set through two strategies and report the per-metric lift.

    Proof-of-lift instrument (Phase 3.2): scores the `baseline` and `treatment`
    strategies over identical queries, then reports `treatment - baseline` for
    every headline metric. `as_of` is threaded to both runs so an ACR treatment
    (`bm25+acr`) decays at a PINNED date. Read-only (INV-2); ranking is
    deterministic (INV-1) so the delta is reproducible (latency excepted).
    """
    base = _metrics_rank.evaluate(kdir, data, gold, baseline, top_k, as_of=as_of)
    treat = _metrics_rank.evaluate(kdir, data, gold, treatment, top_k, as_of=as_of)
    delta = {}
    for key in _ABLATE_METRIC_KEYS:
        delta[key] = treat["metrics"][key] - base["metrics"][key]
    return {
        "mode": "ablate",
        "top_k": top_k,
        "num_queries": len(gold),
        # Both strategies scored the SAME corpus, so either evaluate() result's
        # fingerprint identifies it (acr-gate rows inherit this for comparability).
        "corpus_fingerprint": base.get("corpus_fingerprint"),
        # Both strategies scored the SAME corpus, so inherit the corpus_size from
        # the baseline evaluate() result (acr-gate rows pick this up too).
        "corpus_size": base.get("corpus_size"),
        "baseline": baseline,
        "treatment": treatment,
        "strategies": {
            baseline: base["metrics"],
            treatment: treat["metrics"],
        },
        "delta": delta,
        "per_query": {
            baseline: base["per_query"],
            treatment: treat["per_query"],
        },
    }

def _ok(flag):
    """PASS/FAIL token for a boolean check in --acr-gate text output."""
    return "PASS" if flag else "FAIL"

def _print_acr_gate(result, decision, as_of):
    """Human-readable verdict for `eval --acr-gate` (text format)."""
    c = decision["checks"]
    print("eval --acr-gate: %s vs %s  top_k=%d  queries=%d  as_of=%s" % (
        decision["baseline"], decision["treatment"], result["top_k"],
        result["num_queries"], as_of))
    print("  gold: %s" % result.get("gold_path"))
    verdict = "PASS" if decision["passed"] else "FAIL"
    tail = "bm25+acr MAY become the default" if decision["passed"] \
        else "default stays bm25"
    print("  DECISION: %s  (%s)" % (verdict, tail))
    p = c["primary"]
    print("  [%s] primary    : nDCG delta %+.4f  (>= margin %.4f)" % (
        _ok(p["passed"]), p["delta_ndcg"], p["margin"]))
    s = c["significance"]
    print("  [%s] significant: t=%.4f  df=%d  t_crit=%.4f  mean_delta=%+.4f  "
          "(alpha=%.2f)" % (_ok(s["passed"]), s["t"], s["df"], s["t_crit"],
                            s["mean_delta"], s["alpha"]))
    w = c["win_rate"]
    print("  [%s] win-rate   : improved=%d  regressed=%d  ties=%d" % (
        _ok(w["passed"]), w["improved"], w["regressed"], w["ties"]))
    nh = c["no_harm"]
    print("  [%s] no-harm    : recall=%s  mrr=%s  latency_p50=%s  determinism=%s"
          % (_ok(nh["passed"]), _ok(nh["recall"]["passed"]),
             _ok(nh["mrr"]["passed"]), _ok(nh["latency_p50"]["passed"]),
             _ok(nh["determinism_ok"])))
    a = c["age_stratum"]
    print("  [%s] old-stratum: n=%d  base nDCG=%.4f  treat nDCG=%.4f  "
          "(no-regression)" % (_ok(a["passed"]), a["old_count"],
                               a["old_ndcg_baseline"], a["old_ndcg_treatment"]))

def _run_acr_gate(args, kdir, data, gold, gold_path, as_of):
    """Run the bm25-vs-bm25+acr ablation at a PINNED `as_of` and apply the
    deterministic 5-part ACR win gate. Exits non-zero on FAIL.

    Phase 4.1: with `--record`, appends ONE immutable `mode="acr-gate"` row
    (decision + both strategies + deltas + t-stat + per-stratum) to the ledger
    and regenerates SCORECARD.md. The recorded `strategy` field IS the resulting
    agent-facing default — `active_default_strategy` reads it so bare `recall`
    auto-flips iff the gate PASSED (else stays `bm25`). Recording is read-only
    w.r.t. the KB (INV-2). Without `--record` the gate stays a pure read."""
    result = evaluate_ablation(kdir, data, gold, args.top_k,
                               baseline="bm25", treatment="bm25+acr",
                               as_of=as_of)
    result["as_of"] = as_of
    result["gold_path"] = gold_path
    determinism_ok = _ledger._determinism_check_at(
        kdir, data, gold, args.top_k, "bm25+acr", as_of)
    decision = _acrgate.acr_gate_decision(
        result,
        margin=getattr(args, "acr_margin", ACR_GATE_MARGIN),
        alpha=getattr(args, "acr_alpha", ACR_GATE_ALPHA),
        determinism_ok=determinism_ok)
    result["acr_gate"] = decision
    decided = "bm25+acr" if decision["passed"] else "bm25"
    if getattr(args, "record", False):
        recorded = _ledger.record_acr_gate(
            kdir, data, gold, gold_path, result, decision, args.top_k, as_of)
        scorecard_path, _ = _scorecard.write_scorecard(kdir)
        result["recorded"] = {
            "path": os.path.join(kdir, HISTORY_REL),
            "commit": recorded["commit"], "run": recorded["run"],
            "reliability": recorded["reliability"],
            "default_strategy": decided,
            "scorecard": scorecard_path,
        }
    if args.format == "json":
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0 if decision["passed"] else 1
    _print_acr_gate(result, decision, as_of)
    if "recorded" in result:
        print("  recorded : %s (run %s, reliability %.2f)" % (
            result["recorded"]["path"], result["recorded"]["run"],
            result["recorded"]["reliability"]))
        print("  default  : bare `recall` now resolves to '%s'" % decided)
    return 0 if decision["passed"] else 1

def _run_longmemeval_suite(args, kdir, data):
    """Run `eval --suite longmemeval`: the strategy-agnostic public scorer.

    Resolves the dataset (default: the CLEANED variant — the comparable baseline),
    scores the requested strategy at SESSION granularity (Recall@k headline k=5,
    nDCG/MRR, per-category, abstention separated), optionally records/gates an
    immutable ledger row, and prints a human or JSON report. Read-only w.r.t. the
    KB except the append-only ledger.
    """
    dataset_path, as_of, err = _evalsuites.validate_lme_args(args, kdir)
    if err is not None:
        return err
    try:
        result = _longmemeval.evaluate_longmemeval(
            kdir, dataset_path, args.strategy, args.top_k, as_of=as_of,
            limit=getattr(args, "limit", None))
    except NotImplementedError as exc:
        sys.stderr.write("agentware: %s\n" % exc)
        return 2
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        # WIDER than the gold-fixture arm's, and deliberately so: this suite
        # reads an operator-supplied dataset FILE, so an unreadable path
        # (OSError) or a malformed one (JSONDecodeError) is an input problem to
        # report, not a crash. The frozen fixture ships with the package, so
        # there is nothing there for an OSError to mean.
        sys.stderr.write("agentware: %s\n" % exc)
        return 1
    result["gold_path"] = dataset_path
    recorded, gate = _evalsuites.record_and_gate_suite(
        args, kdir, data, result, "longmemeval")
    if args.format == "json":
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return _regressiongate._gate_exit(gate)
    _evalsuites.print_longmemeval_report(result, dataset_path, recorded, kdir)
    _regressiongate._print_gate(gate)
    return _regressiongate._gate_exit(gate)

def _run_gold_fixture_suite(args, kdir, data):
    """Run `eval --suite gold-fixture`: the frozen synthetic drift-free scorer.

    Scores the requested --strategy over the package-shipped synthetic corpus
    (NO operator data, R-LOC-03). Because the corpus is frozen its
    corpus_fingerprint is stable, so the absolute gate on this suite never
    false-FAILs on live-KB drift. Optionally records/gates an immutable ledger
    row (suite="gold-fixture"). Read-only w.r.t. the KB except the append-only
    ledger.
    """
    as_of, err = _evalsuites.validate_scoring_args(args)
    if err is not None:
        return err
    fixture_path = getattr(args, "gold", None) or _goldfixture.gold_fixture_path()
    try:
        result = _goldfixture.evaluate_gold_fixture(kdir, fixture_path, args.strategy,
                                       args.top_k, as_of=as_of)
    except NotImplementedError as exc:
        sys.stderr.write("agentware: %s\n" % exc)
        return 2
    except ValueError as exc:
        # NARROWER than the longmemeval arm's on purpose: the corpus ships
        # FROZEN with the package, so a malformed one is a packaging defect and
        # an OSError reading it is not an operator input to report -- both
        # should surface as the crash they are rather than as exit 1.
        sys.stderr.write("agentware: %s\n" % exc)
        return 1
    result["gold_path"] = fixture_path
    recorded, gate = _evalsuites.record_and_gate_suite(
        args, kdir, data, result, GOLD_FIXTURE_SUITE)
    if args.format == "json":
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return _regressiongate._gate_exit(gate)
    _evalsuites.print_gold_fixture_report(result, fixture_path, recorded, kdir)
    _regressiongate._print_gate(gate)
    return _regressiongate._gate_exit(gate)

def _run_ablate_suite(args, kdir, data, gold, gold_path, as_of):
    """Run `eval --ablate`: the same gold set through two strategies, with lift.

    Split out of `cmd_eval` at task 99 wave 10. It stays in THIS module rather
    than moving to `evalsuites` with its report, and the reason is a cycle:
    `evaluate_ablation` -- the instrument this arm exists to run -- lives here,
    and a sibling that called it would have to import this module while this
    module imports it.

    The same-strategy refusal is the arm's one input guard and is checked
    BEFORE any scoring: `--baseline x --treatment x` reports a delta of exactly
    zero for every metric, which reads as a measurement rather than a mistake.
    """
    baseline = getattr(args, "baseline", "tag")
    treatment = getattr(args, "treatment", "bm25")
    if baseline == treatment:
        sys.stderr.write(
            "agentware: --baseline and --treatment must differ (got %s)\n"
            % baseline)
        return 1
    try:
        result = evaluate_ablation(kdir, data, gold, args.top_k,
                                   baseline=baseline, treatment=treatment,
                                   as_of=as_of)
        result["as_of"] = as_of
    except NotImplementedError as exc:
        sys.stderr.write("agentware: %s\n" % exc)
        return 2
    result["gold_path"] = gold_path
    recorded, gate = _regressiongate._record_and_gate(
        args, kdir, data, gold, gold_path, result)
    if args.format == "json":
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return _regressiongate._gate_exit(gate)
    _evalsuites.print_ablate_report(result, gold_path, recorded, kdir)
    _regressiongate._print_gate(gate)
    return _regressiongate._gate_exit(gate)


def _run_default_suite(args, kdir, data, gold, gold_path, as_of):
    """Run plain `eval`: score ONE strategy over the operator's own gold set.

    Split out of `cmd_eval` at task 99 wave 10; it stays in this module for the
    same reason as its ablate sibling, so the two arms of one command are not
    split across two files.

    This is the ONLY arm that can run the clean-HEAD A/B gate, and the A/B
    verdict is a SECOND, independent gate: `_gate_exit(gate, ab)` takes both, so
    a run that passes the regression gate and fails the A/B still exits
    non-zero. Passing only `gate` there is a change no report can show.
    """
    try:
        result = _metrics_rank.evaluate(kdir, data, gold, args.strategy, args.top_k, as_of=as_of)
    except NotImplementedError as exc:
        sys.stderr.write("agentware: %s\n" % exc)
        return 2
    result["gold_path"] = gold_path
    result["as_of"] = as_of
    recorded, gate = _regressiongate._record_and_gate(
        args, kdir, data, gold, gold_path, result)
    ab = (_regressiongate.run_ab_gate(args, kdir, result, gold_path)
          if getattr(args, "ab", False) else None)
    if args.format == "json":
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return _regressiongate._gate_exit(gate, ab)
    _evalsuites.print_default_report(result, gold_path, recorded, kdir)
    _regressiongate._print_gate(gate)
    _regressiongate._print_ab(ab)
    return _regressiongate._gate_exit(gate, ab)


def cmd_eval(args):
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    from agentware_lib.knowledge import index  # lazy: retrieval -> knowledge (layer discipline)
    data, err = index.load_index(kdir)
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1

    if getattr(args, "suite", None) == "longmemeval":
        return _run_longmemeval_suite(args, kdir, data)
    if getattr(args, "suite", None) == GOLD_FIXTURE_SUITE:
        return _run_gold_fixture_suite(args, kdir, data)

    gold_path = args.gold or os.path.join(kdir, "benchmarks", "recall-gold.json")
    gold, gerr = _metrics_rank.load_gold(gold_path)
    if gerr is not None:
        sys.stderr.write("agentware: %s\n" % gerr)
        return 1

    as_of, err = _evalsuites.validate_scoring_args(args)
    if err is not None:
        return err

    if getattr(args, "acr_gate", False):
        try:
            return _run_acr_gate(args, kdir, data, gold, gold_path, as_of)
        except NotImplementedError as exc:
            sys.stderr.write("agentware: %s\n" % exc)
            return 2

    if getattr(args, "ablate", False):
        return _run_ablate_suite(args, kdir, data, gold, gold_path, as_of)
    return _run_default_suite(args, kdir, data, gold, gold_path, as_of)


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("eval", 2000, "register_eval"),
)


def register_eval(sub, fmt_parent):
    # eval -------------------------------------------------------------------
    p_eval = sub.add_parser(
        "eval", parents=[fmt_parent],
        help="benchmark recall over a gold set (Recall@k, precision@k, nDCG@k, MRR)")
    p_eval.add_argument(
        "--gold", default=None,
        help="path to gold set JSON (default: <kdir>/benchmarks/recall-gold.json)")
    p_eval.add_argument(
        "--suite", choices=("longmemeval", "gold-fixture"), default=None,
        help="run a benchmark suite instead of the own gold set. "
             "'longmemeval' scores the requested --strategy on LongMemEval-S at "
             "SESSION granularity (Recall@k headline k=5, per-category, "
             "abstention reported separately). 'gold-fixture' scores a tiny "
             "FROZEN synthetic corpus shipped with the package (no operator "
             "data) for a DRIFT-FREE absolute gate. Strategy-agnostic.")
    p_eval.add_argument(
        "--dataset", default=None,
        help="path to the LongMemEval-S JSON for --suite longmemeval (default: "
             "<kdir>/benchmarks/longmemeval/longmemeval_s_cleaned.json)")
    p_eval.add_argument(
        "--limit", type=int, default=None,
        help="score only the first N questions of a --suite run "
             "(dev/testing; default: all)")
    p_eval.add_argument("--top-k", type=int, default=5, dest="top_k",
                        help="cutoff k for @k metrics (default: 5)")
    p_eval.add_argument("--strategy",
                        choices=("tag", "bm25", "bm25+acr", "bm25+embed"),
                        default="tag",
                        help="retrieval strategy to score (default: tag baseline; "
                             "bm25+acr applies the ACR prior over BM25; bm25+embed "
                             "fuses BM25 + local embeddings via RRF, falling back "
                             "to bm25 with no local embedder)")
    p_eval.add_argument("--ablate", action="store_true",
                        help="run the gold set through both --baseline and "
                             "--treatment strategies and report the per-metric lift")
    p_eval.add_argument("--baseline",
                        choices=("tag", "bm25", "bm25+acr", "bm25+embed"),
                        default="tag",
                        help="baseline strategy for --ablate (default: tag). "
                             "Use bm25 to ablate the ACR prior (bm25 vs bm25+acr) "
                             "or the embed lift (bm25 vs bm25+embed)")
    p_eval.add_argument("--treatment",
                        choices=("tag", "bm25", "bm25+acr", "bm25+embed"),
                        default="bm25",
                        help="treatment strategy for --ablate (default: bm25). "
                             "Use bm25+acr to measure the ACR lift, or bm25+embed "
                             "to measure the hybrid embedding lift over plain BM25")
    p_eval.add_argument("--record", action="store_true",
                        help="append one immutable commit+UTC-date-stamped row "
                             "(with a 0-100 reliability score) to "
                             "<kdir>/benchmarks/history.jsonl (append-only)")
    p_eval.add_argument("--gate", action="store_true",
                        help="compare this run to the best prior comparable "
                             "ledger row and EXIT NON-ZERO on a regression "
                             "beyond --tolerance (implies --record; seeds + "
                             "passes when there is no prior history)")
    p_eval.add_argument("--ab", action="store_true",
                        help="clean-HEAD A/B gate: score the working-tree CLI and "
                             "the git-HEAD CLI (extracted to a temp file via "
                             "`git show HEAD:scripts/agentware`, read-only) over "
                             "the SAME corpus and PASS iff working >= head - "
                             "tolerance. Isolates THIS diff's recall effect from "
                             "live-KB corpus drift; emits both numbers + the delta "
                             "in --format json. Unavailable HEAD ⇒ passes.")
    p_eval.add_argument("--tolerance", type=float, default=0.02,
                        help="max allowed drop per headline metric before --gate "
                             "fails (default: 0.02; reliability uses x100 points)")
    p_eval.add_argument("--as-of", default=None, dest="as_of",
                        help="YYYY-MM-DD date for ACR freshness decay "
                             "(default: today; PINNED for reproducible benchmarks)")
    p_eval.add_argument("--acr-gate", action="store_true", dest="acr_gate",
                        help="run the bm25-vs-bm25+acr ablation at --as-of and "
                             "apply the deterministic 5-part ACR win gate (nDCG "
                             "margin + paired t-test + win-rate + no-harm "
                             "guardrails + age-stratified no-regression); EXIT "
                             "NON-ZERO on FAIL. Distinct from --gate.")
    p_eval.add_argument("--acr-margin", type=float, default=ACR_GATE_MARGIN,
                        dest="acr_margin",
                        help="min mean nDCG@k lift for the ACR-gate primary "
                             "check (default: %g)" % ACR_GATE_MARGIN)
    p_eval.add_argument("--acr-alpha", type=float, default=ACR_GATE_ALPHA,
                        dest="acr_alpha",
                        help="paired t-test two-tailed significance level for "
                             "the ACR gate (default: %g; table supports 0.05, "
                             "0.01)" % ACR_GATE_ALPHA)
    p_eval.set_defaults(func=cmd_eval)


_EXPORTS = [
    "_ABLATE_METRIC_KEYS",
    "evaluate_ablation",
    "_ok",
    "_print_acr_gate",
    "_run_acr_gate",
    "_run_longmemeval_suite",
    "_run_gold_fixture_suite",
    "_run_ablate_suite",
    "_run_default_suite",
    "cmd_eval",
]
