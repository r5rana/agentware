"""agentware_lib.features.dreamsteps.stepskillpropose -- dream step "k", the skill-candidate proposal (layer: features).

Owns: `_dream_step_skill_propose` -- one step handler, nothing else, per the
package's one-file-per-step contract.
Does NOT own: the emit seam it drives -> features.skillpropose
(`_skill_promote_run`); cluster DETECTION -> ops.skillcluster; the mode setting
-> core.config (`resolve_skill_promote`); the step ORDER -> dreamsteps.registry.

DETECTION + PROPOSAL ONLY
-------------------------
This step never authors a skill stub, never runs `index add`, never installs.
The only writes are new `work/auto/skill-candidate-*/plan.md` drafts, each
`Status: draft` and never auto-started. The operator approves and runs the
workorder, which authors the skill, and `skill sync --approve` makes it
invocable. A nightly job that could promote its own procedures unattended would
be rewriting the agent's behaviour while nobody is watching; three human gates
stand between detection and execution, and that is the point.

WHY IT IS NOT CHANGE-GATED, AND WHY IT RUNS WHERE IT DOES
---------------------------------------------------------
Recurrence accrues with wall-clock, exactly like staleness (step s) -- a corpus
that has not changed today can still have crossed the recurrence bar. It is
ordered AFTER s and BEFORE f so that f (kb-git-commit) still backs up anything
emitted in the same cycle; an emit that is not committed the same night is an
emit the operator can lose.

A BROKEN INDEX IS NOT A CLEAN RESULT
------------------------------------
A missing or unloadable index yields no clusters, which is indistinguishable from
a healthy corpus with nothing to propose. The step reports that case as `skipped`
with the reason attached, mirroring step s. Reporting it as clean would let a
broken corpus look like a quiet, healthy night indefinitely.

Layer deps: features -> core (`core.config.resolve_skill_promote`), features ->
knowledge (`knowledge.index.load_index`), features -> features
(`skillpropose._skill_promote_run`).
Faults: never aborts the cycle -- the outer `_run_cycle` per-step try/except is
the belt. Gated by AGENTWARE_SKILL_PROMOTE (off|propose); 'off' skips cleanly.
"""

from agentware_lib.core.config import resolve_skill_promote
from agentware_lib.features.skillpropose import _skill_promote_run
from agentware_lib.knowledge.index import load_index


def _dream_step_skill_propose(kdir, dry_run):
    """Step k — detect cohesive, recurring learning clusters and surface each as a
    tier-2 PROPOSED skill-candidate workorder on the routine cadence, via the SHARED
    `_skill_promote_run` emit seam (Task 4). DETECTION + PROPOSAL ONLY: never authors
    a skill stub, never runs `index add`, never installs — the executed workorder
    authors the skill (skill-creator + `skill add`) and `skill sync --approve` makes
    it invocable. Report-only; the ONLY writes are new `work/auto/skill-candidate-*/
    plan.md` drafts (`Status: draft`, never auto-started).

    Gated by AGENTWARE_SKILL_PROMOTE (off|propose, default propose): 'off' -> skip.
    NOT change-gated — recurrence accrues with wall-clock (like step s), so it runs
    regardless of tree state. Ordered AFTER s (stale-propose) and BEFORE f
    (kb-git-commit), so f still backs up any workorder emitted this cycle. A fault
    never aborts the cycle — the outer `_run_cycle` per-step try/except is the belt.

    Distinguishes the outcomes from the shared `_skill_promote_run` report:
      proposed=<n>  new sibling skill-candidate workorders emitted
      extended=<n>  EXTEND-note workorders (cluster already covered by a skill)
      deferred=True g3 `WORKORDER_DRAFT_CAP` hit -> TRANSIENT (drainable)
      blocked=True  a kernel-path / sanitize refusal -> PERMANENT
      clean=True    nothing qualifying (or all idempotent no-ops)
    """
    mode = resolve_skill_promote()
    if mode == "off":
        return {"status": "skipped", "reason": "AGENTWARE_SKILL_PROMOTE=off",
                "proposed": 0, "extended": 0, "deferred": False,
                "blocked": False}
    if dry_run:
        return {"status": "planned", "would": "skill propose"}
    data, err = load_index(kdir)
    # A missing/broken index is NOT a clean no-op — surface it distinctly so a
    # broken corpus is not mistaken for "no clusters" (mirrors step s's skipped).
    if err or not data:
        return {"status": "skipped", "proposed": 0, "extended": 0,
                "deferred": False, "blocked": False,
                "reason": "index unavailable: %s" % (err or "empty")}
    report = _skill_promote_run(kdir, data, dry_run=False)
    return {"status": "ok",
            "proposed": len(report.get("proposed") or []),
            "extended": len(report.get("extended") or []),
            "deferred": bool(report.get("deferred")),
            "blocked": bool(report.get("blocked")),
            "clean": bool(report.get("clean"))}


_EXPORTS = [
    "_dream_step_skill_propose",
]

__all__ = list(_EXPORTS)
