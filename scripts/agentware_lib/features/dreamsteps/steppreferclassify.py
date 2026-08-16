"""agentware_lib.features.dreamsteps.steppreferclassify -- dream step "g", the prefer-queue drain (layer: features).

Owns: `_dream_step_prefer_classify` -- one step handler, nothing else, per the
package's one-file-per-step contract.
Does NOT own: the drain and the tier classifier -> ops.preferclassify
(`_prefer_classify_drain`); the queue -> ops.preferqueue; the admission bar ->
ops.preferadmit; approval and rejection -> ops.preferdispose; the step ORDER ->
dreamsteps.registry.

WHY THE HANDLER LIVES HERE AND THE DRAIN LIVES IN ops
-----------------------------------------------------
Same reason as step m: triage is an ops capability with its own tests and its own
interactive callers, and running nightly is something that happens to it rather
than what it is. Keeping the handler here preserves the registry's contract that
it imports handlers from its own siblings only, and keeps `ops.preferclassify`
ignorant of the dream cycle.

TIERS, AND THE ONE THAT IS A HARD REFUSAL
-----------------------------------------
The drain sorts queued candidates by governance tier: tier-3 (kernel-path or
gate-loosening) is REJECTED outright -- an unattended job must never be the thing
that weakens a governance gate; tier-2 emits a SUGGESTED workorder with the
candidate fenced in Context; tier-0/1 are journaled. Nothing here promotes
anything to a live preference. Mined text NEVER becomes a `source=user`
preference on this path -- that requires an explicit operator `prefer approve`.

IDEMPOTENT, AND DEFER-NOT-DROP
------------------------------
Only `status=queued` candidates are processed, so a re-run is a no-op over
already-triaged items. When the g3 draft cap is full the step DEFERS rather than
dropping: the candidate stays queued and is picked up once the operator drains
`work/auto/`. Dropping would silently lose the proposal, and the operator would
never learn it existed.

Ordered BEFORE f (kb-git-commit) so this cycle's queue transitions and emitted
workorders are backed up in the same cycle that produced them.

Layer deps: features -> ops (`ops.preferclassify._prefer_classify_drain`).
Faults: never aborts the cycle -- the outer `_run_cycle` per-step try/except is
the belt.
"""

from agentware_lib.ops.preferclassify import _prefer_classify_drain


def _dream_step_prefer_classify(kdir, dry_run):
    """Step g — OFFLINE layer-3 consumer (Task 7): drain the prefer-queue by
    governance tier. tier-3 (kernel-path / gate-loosening) REJECTED; tier-2 emits
    a suggested workorder via the EXISTING `plan new --auto` emitter (candidate
    fenced in Context); tier-0/1 journaled. Idempotent (only status=queued
    processed), g3 draft-cap defer-not-drop, and NEVER auto-activates mined text
    as a source=user preference (that stays an explicit operator `prefer approve`
    -> run). Runs BEFORE kb-git-commit so its queue/workorder changes are backed
    up in the same cycle."""
    if dry_run:
        return {"status": "planned",
                "would": "classify prefer-queue candidates -> reject/journal/"
                         "suggested-workorder (proposed; never auto-active)"}
    return _prefer_classify_drain(kdir)


_EXPORTS = [
    "_dream_step_prefer_classify",
]

__all__ = list(_EXPORTS)
