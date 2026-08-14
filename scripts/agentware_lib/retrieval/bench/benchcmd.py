"""agentware_lib.retrieval.bench.benchcmd -- the `bench scorecard` handler + the
`bench` parser registrar (layer: retrieval).

Owns: cmd_bench (the `bench scorecard` handler, which regenerates
benchmarks/SCORECARD.md from history.jsonl via the scorecard sibling) and
register_bench -- the single `bench` parser registrar that wires all four
subverbs (scorecard / redact / swe / lint). register_bench is resolved by the
assembler via importlib from _PARSER_REGISTRATION off the PACKAGE
`agentware_lib.retrieval.bench`, so the aggregator __init__ re-exports it;
handler+registrar travel together.

Does NOT own: the SWE-task domain logic + the `bench swe` runner
(-> agentware_lib.retrieval.bench.benchswe); the gold_path redactor + the
`bench redact` / `bench lint` handlers
(-> agentware_lib.retrieval.bench.benchledger) -- both are SAME-PACKAGE siblings
whose HANDLERS are reached through the OWNER module by ATTRIBUTE
(`benchswe.cmd_bench_swe`), so a monkeypatch of the owner propagates, while the
two frozen SWE parser-default CONSTANTS are bound BY NAME. Nor the append-only
ledger (-> agentware_lib.retrieval.ledger) or the scorecard render/write VIEW
(-> agentware_lib.retrieval.scorecard), held via the `_scorecard` module alias and
referenced by ATTRIBUTE so the bytecode global-resolve gate never walks a foreign
sibling function held in this module's __dict__.

Layer deps: retrieval -> core (config; allowed at module level) + the sibling
retrieval.scorecard (by module alias, attribute reads) + the two SAME-PACKAGE
siblings benchswe/benchledger (neither imports this module, so the package graph
stays acyclic). No knowledge, no ops/features/cli import. _EXPORTS lists cmd_bench
for the entrypoint by-name facade; register_bench is intentionally excluded
(importlib).
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import agentware_lib.core.config as config
import agentware_lib.retrieval.bench.benchledger as benchledger
import agentware_lib.retrieval.bench.benchswe as benchswe
from agentware_lib.retrieval import scorecard as _scorecard
from agentware_lib.retrieval.bench.benchswe import (
    SWE_DEFAULT_ARMS, SWE_DEFAULT_SEEDS)


def cmd_bench(args):
    """`bench scorecard` regenerates benchmarks/SCORECARD.md from history.jsonl."""
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    path, n = _scorecard.write_scorecard(kdir)
    print("bench scorecard: wrote %s (%d ledger row%s)" % (
        path, n, "" if n == 1 else "s"))
    return 0


def register_bench(sub, fmt_parent):
    # bench ------------------------------------------------------------------
    p_bench = sub.add_parser("bench", help="benchmark scorecard utilities")
    bench_sub = p_bench.add_subparsers(dest="bench_command", metavar="<subcommand>")
    bench_sub.required = True
    p_score = bench_sub.add_parser(
        "scorecard",
        help="regenerate benchmarks/SCORECARD.md trend table from history.jsonl")
    p_score.set_defaults(func=cmd_bench)
    p_redact = bench_sub.add_parser(
        "redact",
        help="scrub operator HOME from ledger gold_path (PII redaction; the ONLY "
             "sanctioned mutation of the append-only ledger)")
    p_redact.add_argument(
        "path", nargs="?", default=None,
        help="ledger file to scrub (default: <knowledge-dir>/benchmarks/history.jsonl)")
    p_redact.set_defaults(func=benchledger.cmd_bench_redact)
    p_swe = bench_sub.add_parser(
        "swe",
        help="SWE-task behavioral benchmark (hidden-unit-test coding suite)")
    p_swe.add_argument(
        "--suite", required=True,
        help="path to the suite directory containing suite.json and task dirs")
    p_swe.add_argument(
        "--seeds", type=int, default=SWE_DEFAULT_SEEDS,
        help="number of seeds per arm (default: %d)" % SWE_DEFAULT_SEEDS)
    p_swe.add_argument(
        "--arms", default=",".join(SWE_DEFAULT_ARMS),
        help="comma-separated arm names (default: %s)" % ",".join(SWE_DEFAULT_ARMS))
    p_swe.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="print planned runs as JSON without spawning agents")
    p_swe.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="output format (default: text)")
    p_swe.set_defaults(func=benchswe.cmd_bench_swe)
    p_lint = bench_sub.add_parser(
        "lint",
        help="gold-set staleness and coverage-ratio check")
    p_lint.add_argument(
        "--min-coverage", type=float, default=0.25,
        help="minimum gold-query-to-entry ratio (default: 0.25)")
    p_lint.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="output format (default: text)")
    p_lint.set_defaults(func=benchledger.cmd_bench_lint)


_EXPORTS = [
    "cmd_bench",
]
