"""agentware_lib.features.dreamsteps.stepbenchredact -- dream step "b", bench-redact: scrub the operator HOME out of the reliability ledger (layer: features).

Owns: dream step "b" (_dream_step_bench_redact) -- the ONE mutation the cycle
makes to the reliability ledger (scrubbing the operator's HOME out of every
gold_path, R-LOC-03) plus the scorecard re-render that follows it, and the
before/after fingerprint that reports whether the ledger actually changed.
Does NOT own: the redaction itself -> retrieval.bench; the ledger reader ->
retrieval.ledger; the scorecard renderer -> retrieval.scorecard; the fingerprint
primitive -> dreamsteps.dreamcapture; the eval-record step that re-runs this one
post-record -> dreamsteps.stepevalrecord; the ordered registry that binds this
handler to id "b" -> dreamsteps.registry. See catalog/modules.json.
Layer deps: retrieval (bench, ledger, scorecard + the HISTORY_REL /
SCORECARD_REL constants BY NAME) DOWNWARD -- plus ONE dreamsteps SIBLING
(dreamcapture, a stdlib-only leaf), so the package graph stays acyclic.
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: a missing ledger degrades to a clean SKIP and the scorecard write is
best-effort (an OSError is swallowed), so an unattended cron never crashes
(INV-2). The scorecard is rendered from the ledger rows only, so it is
byte-stable for unchanged inputs (INV-1).
"""

import os

import agentware_lib.retrieval.bench as _bench
import agentware_lib.retrieval.ledger as _ledger
import agentware_lib.retrieval.scorecard as _scorecard

from agentware_lib.retrieval.ledger import HISTORY_REL
from agentware_lib.retrieval.scorecard import SCORECARD_REL

from .dreamcapture import _dream_file_fp


def _dream_step_bench_redact(kdir, dry_run):
    """Step b — scrub operator HOME from the ledger gold_path (only mutation)."""
    ledger = os.path.join(kdir, HISTORY_REL)
    if dry_run:
        return {"status": "planned", "would": "bench redact (scrub gold_path)"}
    if not os.path.isfile(ledger):
        return {"status": "skipped", "reason": "no ledger"}
    before = _dream_file_fp(ledger)
    total, changed = _bench.redact_ledger_gold_paths(ledger)
    try:
        rows = _ledger._read_ledger(ledger)
        scorecard = os.path.join(os.path.dirname(ledger),
                                 os.path.basename(SCORECARD_REL))
        with open(scorecard, "w", encoding="utf-8") as f:
            f.write(_scorecard.render_scorecard(rows))
    except OSError:
        pass
    return {"status": "ok", "rows": total, "scrubbed": changed,
            "ledger_changed": before != _dream_file_fp(ledger)}


_EXPORTS = [
    "_dream_step_bench_redact",
]
