"""agentware_lib.features.dreamsteps.steptranscriptmine -- dream step "m", the offline transcript miner (layer: features).

Owns: `_dream_step_transcript_mine` -- one step handler, nothing else, per the
package's one-file-per-step contract.
Does NOT own: the miner itself -> ops.sessionparse.minerscan (`_transcript_mine`)
and its siblings (cursor + caps in `minerstate`, record readers and secret
redaction in `minerextract`, error signatures in `minererrors`); the queue the
candidates land in -> ops.preferqueue; the step ORDER -> dreamsteps.registry.

WHY THE HANDLER LIVES HERE AND THE MINER LIVES IN ops
-----------------------------------------------------
The mining machinery is an ops capability with its own tests and its own callers;
being a nightly step is one thing that HAPPENS to it, not what it is. Homing the
step handler in `ops.sessionparse` would make an ops module know about the dream
cycle -- an upward dependency in fact if not in imports -- and would break the
package's contract that `registry` imports handlers from its own siblings only.
The adapter is three lines; the coupling it removes is permanent.

ORDERING IS THE WHOLE DESIGN
----------------------------
Step m runs immediately BEFORE step g (prefer-classify), because m ENQUEUES
candidates and g DRAINS them. Ordered that way, a candidate mined tonight is
triaged tonight and backed up by f (kb-git-commit) tonight -- one cycle, not
three. Reverse them and every mined candidate waits a full day for triage.

Everything mined is QUARANTINED: a candidate is a proposal, never an active
preference. Nothing on this path can make mined text behave as a `source=user`
preference; only an explicit operator `prefer approve` can.

Layer deps: features -> ops (`ops.sessionparse.minerscan._transcript_mine`).
Faults: never aborts the cycle -- the outer `_run_cycle` per-step try/except is
the belt, and any fault leaves the queue and the watermark untouched, so the next
cycle re-mines exactly what this one did not finish.
"""

from agentware_lib.ops.sessionparse.minerscan import _transcript_mine


def _dream_step_transcript_mine(kdir, dry_run):
    """Step m — OFFLINE transcript miner (260713, Task 5): a SECOND prefer-queue
    producer ordered BEFORE step g so a mine-then-drain completes in ONE cycle (m
    enqueues quarantined candidates, g classifies them). Incremental mode.
    Best-effort: the _run_cycle per-step try/except (`:20972`) is the outer belt;
    any fault leaves the queue + watermark untouched and NEVER aborts the cycle."""
    if dry_run:
        return {"status": "planned",
                "would": "mine prompts.log/sessions/metrics -> recurrence + "
                         "error-recovery candidates (quarantined; never auto-active)"}
    return {"status": "ok", "candidates": _transcript_mine(kdir, backfill=False)}


_EXPORTS = [
    "_dream_step_transcript_mine",
]

__all__ = list(_EXPORTS)
