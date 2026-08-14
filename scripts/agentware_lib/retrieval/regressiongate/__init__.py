"""agentware_lib.retrieval.regressiongate — the two eval gates (layer: retrieval).

AGGREGATOR ONLY. This package holds NO logic: it re-imports the public names of its
two domain submodules so every pre-split call site — which reaches the module under
an alias (`from agentware_lib.retrieval import regressiongate as _regressiongate`)
and reads `_regressiongate.<name>` — keeps resolving unchanged. The submodules are:

  * `gatebaseline` — the INV-7 ABSOLUTE gate: "is this run worse than the best
                     comparable run ever recorded?" (GATE_METRICS, gate_baseline,
                     evaluate_gate, _record_and_gate, _gate_exit, _print_gate).
  * `abgate`       — the RELATIVE clean-HEAD vs working-tree A/B gate: "is THIS diff
                     responsible?" (the verdict, the read-only whole-scripts/-tree
                     HEAD extraction and its completeness contract, the HEAD-CLI
                     subprocess scorer, run_ab_gate, _print_ab, and the transitional
                     install_ab_extraction seam).

The split happened at feature 260813 task 26: making the HEAD extraction layout-aware
and adding its completeness contract took the single file past the 600-line lib cap,
and the two gates answer genuinely different questions over different inputs — the
multiple-responsibility signal the task-21 splits used. The union of the two
submodules' `_EXPORTS` is exactly the pre-split module's `_EXPORTS` plus the names
task 26 adds — no name dropped.

REPO_ROOT is re-exported here for readers, but note the live monkeypatch target moved
with the code it governs: patch `regressiongate.abgate.REPO_ROOT`, or (better) pass
`repo_root=` explicitly, which every extraction function now accepts.

Owns: nothing of its own — only the re-export surface.
Does NOT own: any behaviour — see the two submodules above; the append-only ledger
(agentware_lib.retrieval.ledger); the eval CLI handlers
(agentware_lib.retrieval.evalcmd).
Layer deps: retrieval -> core + its own submodules only; nothing above retrieval.
"""

from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.retrieval.regressiongate.gatebaseline import (
    GATE_METRICS,
    _gate_exit,
    _print_gate,
    _record_and_gate,
    evaluate_gate,
    gate_baseline,
)
from agentware_lib.retrieval.regressiongate.abgate import (
    HeadExtractionError,
    _ab_head_recall,
    _ab_verdict,
    _cleanup_head_cli,
    _extract_head_cli,
    _extract_head_scripts,
    _head_scripts_listing,
    _print_ab,
    _run_ab_gate_ns,
    _synthesize_head_marker,
    _verify_head_extraction,
    install_ab_extraction,
    run_ab_gate,
)

from agentware_lib.retrieval.regressiongate.gatebaseline import (
    _EXPORTS as _GATEBASELINE_EXPORTS)
from agentware_lib.retrieval.regressiongate.abgate import (
    _EXPORTS as _ABGATE_EXPORTS)

# The entrypoint facade re-exports BY NAME off this package, so the aggregate must
# be the exact union of the submodules' surfaces — never a hand-maintained third
# list that can drift from either.
_EXPORTS = list(_GATEBASELINE_EXPORTS) + list(_ABGATE_EXPORTS)
