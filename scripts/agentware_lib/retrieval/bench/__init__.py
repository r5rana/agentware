"""agentware_lib.retrieval.bench — the `bench` verb: SWE-task benchmark domain
logic + the bench CLI handlers + the parser registrar (layer: retrieval).

AGGREGATOR ONLY. This package holds NO logic: it re-imports the public names of
its three domain submodules so every pre-split call site — which reaches the
module under an alias (`import agentware_lib.retrieval.bench as _bench`) and reads
`_bench.redact_ledger_gold_paths` — keeps resolving unchanged. The `bench` verb
itself is declared in `benchcmd`'s `__verbs__`, beside the registrar that serves
it. The submodules are:

  * `benchswe`    — the 4 frozen SWE-task suite constants + the 10 deterministic
                    swe_*/`_dirs_identical` scorer/aggregator/decision/builder
                    functions, plus the `bench swe` runner handler cmd_bench_swe.
  * `benchledger` — the append-only-safe gold_path redactor
                    (redact_ledger_gold_paths) + the two ledger/gold handlers
                    cmd_bench_redact and cmd_bench_lint (the LAZY
                    retrieval->knowledge edge lives in the latter).
  * `benchcmd`    — the `bench scorecard` handler cmd_bench + the single `bench`
                    parser registrar register_bench (all four subverbs).

The union of the three submodules' `_EXPORTS` is exactly the pre-split module's
`_EXPORTS` — no name added, none dropped. `register_bench` is re-exported here by
name (it is deliberately NOT in any `_EXPORTS`, being resolved via importlib).

Owns: nothing of its own — only the re-export surface that keeps the pre-split
import path (`import agentware_lib.retrieval.bench as _bench`) and every
monkeypatch target (`<consumer>._bench.<name>`) resolving unchanged.
Does NOT own: any behaviour — see the three submodules above; the append-only
ledger (agentware_lib.retrieval.ledger); the scorecard render/write VIEW
(agentware_lib.retrieval.scorecard); the knowledge index
(agentware_lib.knowledge.index, reached LAZILY from benchledger).
Layer deps: retrieval -> core + its own submodules only; nothing above the
retrieval layer.
"""

from agentware_lib.retrieval.bench.benchswe import (
    SWE_DEFAULT_ARMS,
    SWE_DEFAULT_SEEDS,
    SWE_REGRESSION_TOLERANCE,
    SWE_SUITE_FILE,
    _dirs_identical,
    cmd_bench_swe,
    swe_aggregate_arm,
    swe_aggregate_seeds,
    swe_build_dry_run_plan,
    swe_build_ledger_row,
    swe_load_suite,
    swe_load_task_meta,
    swe_regression_decision,
    swe_score_task,
    swe_task_passed,
)
from agentware_lib.retrieval.bench.benchledger import (
    cmd_bench_lint,
    cmd_bench_redact,
    redact_ledger_gold_paths,
)
from agentware_lib.retrieval.bench.benchcmd import (
    cmd_bench,
    register_bench,
)
