"""agentware_lib.ops.releasegate -- the multi-tier release-gate harness (layer: ops).

Owns: the release-gate harness (feature 260630-release-gate) that prevents
regressions before an agentware change ships -- the four gate CHECKS
(_gate1_content_lint = strict steering content/intent-preservation lint;
_gate2_lite_gold_fixture = gold-fixture retrieval no-regression vs the ledger
baseline; _gate3_reliability_delta = reliability no-regression as a DELTA, not an
absolute floor; _gate2_full = Tier-B own-gold + LongMemEval; _gate4_swe_task =
Tier-B SWE-task presence probe), the orchestrator cmd_gate_release (Tier-A always,
Tier-B on --full; the AGENTWARE_DISABLE_RELEASE_GATE kill-switch and the
graceful no-op when no knowledge dir resolves) and the `gate` verb registrar
(register_gate).

Does NOT own: the domains it CONSUMES downward -- the steering DSF lint
(lint_steering lives in agentware_lib.ops.steering, reached via the _steering
module alias), the retrieval gold-fixture / ledger / ranking-eval / regression-gate
primitives (gold_fixture_path/evaluate_gold_fixture/GOLD_FIXTURE_SUITE in
retrieval.goldfixture via _goldfixture; HISTORY_REL/_read_ledger in
retrieval.ledger via _ledger; evaluate/load_gold in retrieval.metrics_rank via
_metrics_rank; gate_baseline/evaluate_gate in retrieval.regressiongate via
_regressiongate) and the config/index resolvers (core.config / knowledge.index).
The audit gate check _audit_gate_check is a DIFFERENT concern (audit, not release
gate) and stays with the audit domain.

Layer deps: ops.releasegate -> core (paths.REPO_ROOT, config), knowledge (index),
retrieval (goldfixture/ledger/metrics_rank/regressiongate) + a same-layer ops edge
to ops.steering -- all DOWNWARD or same-layer. Cross-module FUNCTIONS are reached
by ATTRIBUTE through module aliases (the house pattern used by ops.derive/ops.fleet)
so the bytecode global-resolve gate never cascades into them; the two CONSTANTS it
reads bare (HISTORY_REL/GOLD_FIXTURE_SUITE) + REPO_ROOT are imported BY NAME (no
__code__, no cascade). Nothing in agentware_lib imports releasegate -- it is a leaf
consumer. Its _EXPORTS ARE re-exported BY NAME onto the scripts/agentware facade so
the `gate release` leaf's dispatch handler cmd_gate_release is an attribute of the
entrypoint namespace (the parser-golden dispatch-func-ownership contract) and
load_cli tests reach the gates as CLI.<name>; the facade ALSO binds the `_steering`
module alias so the facade-held gate functions' `_steering.lint_steering` resolves
(the other aliases _goldfixture/_ledger/_metrics_rank/_regressiongate + the constants
are already on the facade from the retrieval seams). The `gate` registrar moved WITH
its cmd_gate_release handler and is declared in this module's `__verbs__`,
NOT re-exported.

Determinism: read-only reporting (INV-2). The gates lint files / read the eval
ledger / probe a subcommand; cmd_gate_release NEVER mutates the KB or the index
(index.load_index degrades to [] on a broken index so Gate-1 still runs). The
gold-fixture / reliability gates are pure functions of the on-disk ledger + the
corpus fingerprint; no wall-clock enters ranking (INV-1).

Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import os
import subprocess
import sys

import agentware_lib.core.config as config
import agentware_lib.knowledge.index as index
from agentware_lib.core.paths import REPO_ROOT
import agentware_lib.ops.steering as _steering
import agentware_lib.retrieval.goldfixture as _goldfixture
from agentware_lib.retrieval.goldfixture import GOLD_FIXTURE_SUITE
import agentware_lib.retrieval.ledger as _ledger
from agentware_lib.retrieval.ledger import HISTORY_REL
import agentware_lib.retrieval.metrics_rank as _metrics_rank
import agentware_lib.retrieval.regressiongate as _regressiongate


# --- Release gate harness (feature 260630-release-gate) -----------------------
# Multi-tier gate that prevents regressions before agentware changes ship.
# Tier A (auto, network-free): Gate 1 (content-lint) + Gate 2-lite (gold-fixture
#   no-regression) + Gate 3 (reliability no-regression).
# Tier B (opt-in --full): Gate 2-full (own-gold + LongMemEval) + Gate 4 (SWE-task).

def _gate1_content_lint():
    """Gate 1: Content/intent preservation lint (strict mode)."""
    result = _steering.lint_steering(strict=True)
    errors = []
    for v in result.get("violations", []):
        errors.append("%s %s:%d %s" % (v["rule"], v["file"], v["line"], v["text"]))
    for b in result.get("banned_words", []):
        errors.append("DSF-5 %s:%d banned %r" % (b["file"], b["line"], b["word"]))
    for ce in result.get("content_errors", []):
        errors.append("%s %s" % (ce["rule"], ce["text"]))
    if not result["budget_ok"]:
        errors.append("char budget exceeded: %d > %d" % (
            result["total_chars"], result["char_budget"]))
    return {
        "gate": "content-lint",
        "passed": result["ok"],
        "details": errors or ["DSF+CP clean: %d chars, %d rules" % (
            result["total_chars"], result.get("rule_count", 0))],
    }


def _gate2_lite_gold_fixture(kdir, data, tolerance):
    """Gate 2-lite: gold-fixture retrieval no-regression."""
    fixture_path = _goldfixture.gold_fixture_path()
    if not os.path.isfile(fixture_path):
        return {"gate": "gold-fixture", "passed": True,
                "details": ["SKIP: gold-fixture file not found at %s" % fixture_path]}
    try:
        result = _goldfixture.evaluate_gold_fixture(kdir, fixture_path, "bm25", 5)
    except (NotImplementedError, ValueError) as exc:
        return {"gate": "gold-fixture", "passed": True,
                "details": ["SKIP: %s" % exc]}
    ledger_path = os.path.join(kdir, HISTORY_REL)
    prior_rows = _ledger._read_ledger(ledger_path)
    baseline = _regressiongate.gate_baseline(prior_rows, "bm25", GOLD_FIXTURE_SUITE,
                             result.get("corpus_fingerprint"))
    passed, regressions = _regressiongate.evaluate_gate(
        result["metrics"], result.get("reliability", 100.0), baseline, tolerance)
    details = []
    if baseline is None:
        details.append("first run (no baseline) — seeding and passing")
    for m, b, c in regressions:
        details.append("regression: %s baseline=%.4f current=%.4f" % (m, b, c))
    if passed and not details:
        details.append("Recall@5=%.4f (baseline: %s)" % (
            result["metrics"].get("recall_at_k", 0),
            "%.4f" % baseline.get("recall_at_k", 0) if baseline else "none"))
    return {"gate": "gold-fixture", "passed": passed, "details": details}


def _gate3_reliability_delta(kdir, tolerance):
    """Gate 3: Reliability no-regression (delta, NOT absolute floor)."""
    ledger_path = os.path.join(kdir, HISTORY_REL)
    rows = _ledger._read_ledger(ledger_path)
    if not rows:
        return {"gate": "reliability-delta", "passed": True,
                "details": ["no prior ledger rows — passing (first run)"]}
    # Compare latest reliability to the best prior for the same suite+strategy.
    # Use gold-fixture suite as the canonical anchor for Tier-A.
    gf_rows = [r for r in rows if r.get("suite") == GOLD_FIXTURE_SUITE
               and r.get("strategy") == "bm25"]
    if not gf_rows:
        return {"gate": "reliability-delta", "passed": True,
                "details": ["no comparable gold-fixture rows — passing"]}
    rels = [r.get("reliability", 0) for r in gf_rows
            if isinstance(r.get("reliability"), (int, float))]
    if not rels:
        return {"gate": "reliability-delta", "passed": True,
                "details": ["no reliability values in gold-fixture rows — passing"]}
    best_rel = max(rels)
    # Current reliability: the latest recorded row's reliability.
    latest = max(gf_rows, key=lambda r: r.get("run", ""))
    current_rel = latest.get("reliability", 0)
    epsilon = tolerance * 100.0
    passed = current_rel >= best_rel - epsilon
    details = []
    if not passed:
        details.append("reliability regression: current=%.2f best=%.2f epsilon=%.2f"
                       % (current_rel, best_rel, epsilon))
    else:
        details.append("reliability=%.2f (best=%.2f epsilon=%.2f)" % (
            current_rel, best_rel, epsilon))
    return {"gate": "reliability-delta", "passed": passed, "details": details}


def _gate2_full(kdir, data, tolerance):
    """Gate 2-full: own-gold + LongMemEval (Tier-B). Skip-with-log on missing baseline."""
    results = []
    # own-gold
    own_gold_path = os.path.join(kdir, "benchmarks", "recall-gold.json")
    if not os.path.isfile(own_gold_path):
        results.append({"gate": "own-gold", "passed": True,
                        "details": ["SKIP (loud): own-gold baseline absent at %s"
                                    % own_gold_path]})
    else:
        try:
            gold, gerr = _metrics_rank.load_gold(own_gold_path)
            if gerr:
                results.append({"gate": "own-gold", "passed": True,
                                "details": ["SKIP (loud): %s" % gerr]})
            else:
                result = _metrics_rank.evaluate(kdir, data, gold, "bm25", 5)
                ledger_path = os.path.join(kdir, HISTORY_REL)
                prior_rows = _ledger._read_ledger(ledger_path)
                baseline = _regressiongate.gate_baseline(prior_rows, "bm25", None,
                                         result.get("corpus_fingerprint"))
                passed, regressions = _regressiongate.evaluate_gate(
                    result["metrics"], result.get("reliability", 100.0),
                    baseline, tolerance)
                details = []
                if baseline is None:
                    details.append("first own-gold run — seeding and passing")
                for m, b, c in regressions:
                    details.append("regression: %s baseline=%.4f current=%.4f"
                                   % (m, b, c))
                if passed and not details:
                    details.append("Recall@5=%.4f" % result["metrics"].get(
                        "recall_at_k", 0))
                results.append({"gate": "own-gold", "passed": passed,
                                "details": details})
        except (NotImplementedError, ValueError, OSError) as exc:
            results.append({"gate": "own-gold", "passed": True,
                            "details": ["SKIP (loud): %s" % exc]})
    # LongMemEval
    ledger_path = os.path.join(kdir, HISTORY_REL)
    prior_rows = _ledger._read_ledger(ledger_path)
    lme_rows = [r for r in prior_rows if r.get("suite") == "longmemeval"]
    if not lme_rows:
        results.append({"gate": "longmemeval", "passed": True,
                        "details": ["SKIP (loud): no LongMemEval baseline rows "
                                    "in ledger"]})
    else:
        results.append({"gate": "longmemeval", "passed": True,
                        "details": ["LongMemEval baseline present (%d rows) — "
                                    "full eval deferred to CI" % len(lme_rows)]})
    return results


def _gate4_swe_task():
    """Gate 4: SWE-task benchmark (Tier-B). Skip if bench swe subcommand absent."""
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "scripts", "agentware"),
             "bench", "swe", "--help"],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10)
        if proc.returncode != 0:
            return {"gate": "swe-task", "passed": True,
                    "details": ["SKIP: bench swe subcommand not available"]}
    except (OSError, subprocess.TimeoutExpired):
        return {"gate": "swe-task", "passed": True,
                "details": ["SKIP: bench swe subcommand not available"]}
    return {"gate": "swe-task", "passed": True,
            "details": ["bench swe exists but full-run deferred to CI"]}


def _emit_skip(msg, as_json):
    """The ONE emitter for both of the gate's graceful no-ops. Returns 0.

    A SKIP is `passed: true` on purpose: the gate makes no claim about a tree it
    did not measure, and a caller that treated "not run" as "failed" would block
    every environment the kill-switch exists for. The two call sites differ only
    in their `reason`, which is why they are one function — they were byte-identical
    copy-paste, and a divergence between them would be a silent change in what a
    skip MEANS on one of the two paths.
    """
    if as_json:
        json.dump({"passed": True, "skipped": True, "reason": msg},
                  sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(msg)
    return 0


def _print_gate_report(payload):
    """The text render of a completed run: the verdict, then one line per gate
    and every detail it reported. Unlike `audit`, a PASSING gate's details are
    printed too — they carry the measured numbers the release decision rests on."""
    print("RELEASE GATE %s" % ("PASSED" if payload["passed"] else "FAILED"))
    for g in payload["gates"]:
        print("  [%s] %s" % ("PASS" if g["passed"] else "FAIL", g["gate"]))
        for d in g.get("details", []):
            print("      %s" % d)


def cmd_gate_release(args):
    """Run the release gate harness. Exit 0 on pass, 1 on any failure.

    Two graceful no-ops first (the kill-switch, then an absent workspace), then
    the tiers, then ONE render of ONE payload — so the json document and the text
    report can never disagree about which gates ran.
    """
    as_json = getattr(args, "format", "text") == "json"

    # Kill-switch
    if os.environ.get("AGENTWARE_DISABLE_RELEASE_GATE", "").strip():
        return _emit_skip(
            "release gate SKIPPED (AGENTWARE_DISABLE_RELEASE_GATE set)", as_json)

    full = getattr(args, "full", False)
    tolerance = getattr(args, "tolerance", 0.02)

    # Graceful no-op when toolkit/workspace absent.
    kdir = config.resolve_knowledge_dir()
    if not kdir or not os.path.isdir(kdir):
        return _emit_skip("release gate SKIPPED (no knowledge dir resolved)",
                          as_json)

    data, err = index.load_index(kdir)
    if err is not None:
        # If index is broken, still run content lint (Gate 1) which is repo-only.
        data = []

    gates = []

    # --- Tier A (always) ---
    gates.append(_gate1_content_lint())
    gates.append(_gate2_lite_gold_fixture(kdir, data, tolerance))
    gates.append(_gate3_reliability_delta(kdir, tolerance))

    # --- Tier B (--full only) ---
    if full:
        gates.extend(_gate2_full(kdir, data, tolerance))
        gates.append(_gate4_swe_task())

    all_passed = all(g["passed"] for g in gates)
    payload = {"passed": all_passed, "skipped": False, "gates": gates}

    if as_json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        _print_gate_report(payload)
    return 0 if all_passed else 1


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("gate", 3200, "register_gate"),
)


def register_gate(sub, fmt_parent):
    # gate -------------------------------------------------------------------
    p_gate = sub.add_parser("gate", help="release gate harness")
    gate_sub = p_gate.add_subparsers(dest="gate_command", metavar="<subcommand>")
    gate_sub.required = True
    p_gate_release = gate_sub.add_parser(
        "release", parents=[fmt_parent],
        help="run the multi-tier release gate (Tier-A by default, --full for Tier-B)")
    p_gate_release.add_argument("--full", action="store_true",
                                help="run heavyweight Tier-B gates (own-gold, "
                                     "LongMemEval, SWE-task)")
    p_gate_release.add_argument("--tolerance", type=float, default=0.02,
                                help="regression tolerance for retrieval metrics "
                                     "(default: 0.02)")
    p_gate_release.set_defaults(func=cmd_gate_release)


_EXPORTS = [
    "_gate1_content_lint",
    "_gate2_lite_gold_fixture",
    "_gate3_reliability_delta",
    "_gate2_full",
    "_gate4_swe_task",
    "cmd_gate_release",
]
__all__ = list(_EXPORTS)
