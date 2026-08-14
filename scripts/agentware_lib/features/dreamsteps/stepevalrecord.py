"""agentware_lib.features.dreamsteps.stepevalrecord -- dream step "d", eval-record: append ONE reliability row, then redact it (layer: features).

Owns: dream step "d" (_dream_step_eval_record) -- the single
`eval --strategy bm25 --record` run per cycle, the before/after ledger row count
that proves exactly one row was appended, the reliability figure read back out of
the recorded payload, and the immediate re-run of the bench-redact step so the
freshly appended row can never carry an operator HOME (R-LOC-03).
Does NOT own: cmd_eval -> retrieval.evalcmd; the ledger reader ->
retrieval.ledger; the redaction step it re-runs -> dreamsteps.stepbenchredact;
the capture harness -> dreamsteps.dreamcapture; the ordered registry that binds
this handler to id "d" -> dreamsteps.registry. See catalog/modules.json.
Layer deps: retrieval (evalcmd, ledger + the ACR_GATE_ALPHA / ACR_GATE_MARGIN /
HISTORY_REL constants BY NAME) DOWNWARD -- plus two dreamsteps SIBLINGS
(dreamcapture, stepbenchredact), neither of which imports this module, so the
package graph stays acyclic.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: a missing gold set degrades to a clean SKIP and non-JSON captured
output leaves `reliability` as None rather than raising, so an unattended cron
never crashes (INV-2). The eval Namespace pins every knob (top_k/strategy/
tolerance/acr_*), so the recorded row is reproducible (INV-1).
"""

import argparse
import json
import os

import agentware_lib.retrieval.evalcmd as _evalcmd
import agentware_lib.retrieval.ledger as _ledger

from agentware_lib.retrieval.acrgate import ACR_GATE_ALPHA, ACR_GATE_MARGIN
from agentware_lib.retrieval.ledger import HISTORY_REL

from .dreamcapture import _dream_capture
from .stepbenchredact import _dream_step_bench_redact


def _dream_step_eval_record(kdir, dry_run):
    """Step d — append ONE reliability row (eval --record), then redact it."""
    gold = os.path.join(kdir, "benchmarks", "recall-gold.json")
    if dry_run:
        return {"status": "planned",
                "would": "eval --strategy bm25 --record, then bench redact"}
    if not os.path.isfile(gold):
        return {"status": "skipped", "reason": "no gold set"}
    ledger = os.path.join(kdir, HISTORY_REL)
    before = len(_ledger._read_ledger(ledger)) if os.path.isfile(ledger) else 0
    ns = argparse.Namespace(
        format="json", gold=None, suite=None, dataset=None, limit=None,
        top_k=5, strategy="bm25", ablate=False, baseline="tag", treatment="bm25",
        record=True, gate=False, ab=False, tolerance=0.02, as_of=None,
        acr_gate=False, acr_margin=ACR_GATE_MARGIN, acr_alpha=ACR_GATE_ALPHA)
    rc, out = _dream_capture(_evalcmd.cmd_eval, ns)
    reliability = None
    try:
        reliability = (json.loads(out).get("recorded") or {}).get("reliability")
    except ValueError:
        pass
    after = len(_ledger._read_ledger(ledger)) if os.path.isfile(ledger) else 0
    redact = _dream_step_bench_redact(kdir, dry_run=False)
    return {"status": "ok" if rc == 0 else "fail",
            "rows_added": after - before, "reliability": reliability,
            "post_redact_scrubbed": redact.get("scrubbed", 0)}


_EXPORTS = [
    "_dream_step_eval_record",
]
