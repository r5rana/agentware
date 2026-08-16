"""agentware_lib.features.dreamsteps.registry -- the ordered dream step registry + the --steps subset parser (layer: features).

Owns: DREAM_STEP_FUNCS, the ORDER-BEARING tuple that binds each step id to its
label and its handler, and _dream_parse_steps, the `--steps a,c,f` parser that
validates a requested subset against it. This module is the ONE place the cycle's
shape is declared: adding, removing or reordering a step is a single edit here
plus the step's own file, and nothing else in the package knows the order.
Does NOT own: any step HANDLER -> dreamsteps.step<step> (one file per step); the
measure/capture primitives -> dreamsteps.dreamcapture; the git runner ->
dreamsteps.dreamgit; the cycle DRIVER that iterates this registry (cmd_dream) ->
features.dream. Step "0", the PACKAGE-repository pull, is RETIRED and is
deliberately absent: package updates are delegated to the dedicated verified
updater, and dream never mutates the repository root. See catalog/modules.json.
Layer deps: NONE outside its own package -- it imports only the twelve step
handlers from dreamsteps SIBLINGS, so it sits at the TOP of the package's
internal graph (every sibling it imports is acyclic w.r.t. it, none imports back).
That sibling-only rule is why steps m and g have their own handler files here even
though the machinery they drive lives in `ops`: the three-line adapter keeps the
registry's import surface inside the package and keeps `ops` ignorant of the dream
cycle it happens to be scheduled by.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: pure data + a pure function. The registry is a module-level tuple
evaluated once at import; _dream_parse_steps performs NO I/O and returns the
subset in REGISTRY order regardless of the order the caller listed them in, so a
`--steps` run is byte-stable (INV-1) for a given set.
"""

from .stepaudit import _dream_step_audit
from .stepbenchredact import _dream_step_bench_redact
from .stepdetectreport import _dream_step_detect_report
from .stepevalrecord import _dream_step_eval_record
from .stephealthgate import _dream_step_health_gate
from .stepindexrebuild import _dream_step_index_rebuild
from .stepkbgit import _dream_step_kb_git
from .stepkbpull import _dream_step_kb_pull
from .steppreferclassify import _dream_step_prefer_classify
from .stepskillpropose import _dream_step_skill_propose
from .stepstalepropose import _dream_step_stale_propose
from .steptranscriptmine import _dream_step_transcript_mine


DREAM_STEP_FUNCS = (
    # Step "0", the PACKAGE-repository pull, is RETIRED (see the note above the
    # KB-pull step): package updates are delegated to the dedicated verified
    # updater, and dream never mutates the repository root.
    ("1", "kb-pull", _dream_step_kb_pull),
    ("2", "health-gate", _dream_step_health_gate),
    ("a", "index-rebuild", _dream_step_index_rebuild),
    ("b", "bench-redact", _dream_step_bench_redact),
    ("c", "audit", _dream_step_audit),
    ("d", "eval-record", _dream_step_eval_record),
    ("e", "detect-report", _dream_step_detect_report),
    # Staleness/supersede/conflict propose (260717 follow-up B1): run the SHIPPED
    # `audit --stale --propose` emitter on the routine cadence so stale/superseded/
    # conflict candidates surface as tier-2 PROPOSED review workorders on their own
    # (not only when a human runs `audit`). Ordered AFTER e (detect-report) and
    # BEFORE m/g/f, so f (kb-git-commit) still backs up any emitted workorder. NOT
    # change-gated — staleness is wall-clock, not tree-based.
    ("s", "stale-propose", _dream_step_stale_propose),
    # Auto skill-promotion (260722, Task 4): detect cohesive recurring learning
    # clusters and surface each as a tier-2 PROPOSED skill-candidate workorder on
    # the routine cadence (detection + proposal only — never authors/installs).
    # Ordered AFTER s and BEFORE f so f (kb-git-commit) still backs up any emit.
    # Gated by AGENTWARE_SKILL_PROMOTE (off|propose). `k` is the free step char.
    ("k", "skill-propose", _dream_step_skill_propose),
    # P2c transcript miner (260713, Task 5): mine the corpus into the prefer-queue
    # BEFORE step g drains it, so a mine-then-drain completes in ONE cycle. Ordered
    # ...e, m, g, f. _dream_parse_steps auto-validates 'm' from this tuple.
    ("m", "transcript-mine", _dream_step_transcript_mine),
    # Layer-3 steering-capture consumer (260712, Task 7): drain BEFORE the nightly
    # kb-git-commit so proposed workorders + queue transitions land in the backup.
    ("g", "prefer-classify", _dream_step_prefer_classify),
    ("f", "kb-git-commit", _dream_step_kb_git),
)


def _dream_parse_steps(raw):
    """Parse --steps 'a,c,f' into an ordered validated subset. Returns
    (steps_list, None) or (None, error)."""
    valid = [s for s, _, _ in DREAM_STEP_FUNCS]
    if not raw:
        return valid, None
    want = {t.strip().lower() for t in raw.split(",") if t.strip()}
    bad = sorted(t for t in want if t not in set(valid))
    if bad:
        return None, ("unknown dream step(s): %s (valid: %s)"
                      % (", ".join(bad), ", ".join(valid)))
    return [s for s in valid if s in want], None


_EXPORTS = [
    "DREAM_STEP_FUNCS",
    "_dream_parse_steps",
]
