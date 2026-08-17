"""agentware_lib.features.dreamsteps.stepstalepropose -- dream step "s", the staleness review proposal (layer: features).

Owns: `_dream_step_stale_propose` -- one step handler, nothing else, per the
package's one-file-per-step contract.
Does NOT own: the emitter it drives -> features.staleproposal
(`_emit_stale_review_workorder`); the freshness detectors behind the report ->
ops.freshness; the `audit` command it runs -> ops.audit; the step ORDER ->
dreamsteps.registry.

WHY THIS STEP IS NOT CHANGE-GATED
---------------------------------
Steps c and d only run when the tree changed, because their findings are
tree-derived. Staleness is not: `last_verified` ages against the WALL CLOCK, so a
knowledge base that nobody touched for four months is exactly the one with the
most stale entries and exactly the one a change-gate would skip forever. This
step therefore runs on every cycle regardless of tree state.

WHY IT SHELLS THE SHIPPED AUDIT INSTEAD OF EMITTING DIRECTLY
------------------------------------------------------------
It runs `audit --stale --propose` through the capture harness rather than calling
the emitter itself. That keeps ONE emit seam: whatever a human sees when they run
`audit --stale --propose` by hand is byte-for-byte what the nightly cycle
produces. A second, cycle-only emit path would drift from the interactive one and
the drift would surface as workorders no human command can reproduce.

OUTCOMES ARE DISTINGUISHED, INCLUDING THE ONES THAT LOOK ALIKE
-------------------------------------------------------------
`proposed` / `deferred` / `blocked` / clean / `skipped` are four different states
and the step refuses to collapse them:

  * DEFERRED is TRANSIENT -- the g3 draft cap is full; draining `work/auto/`
    clears it.
  * BLOCKED is PERMANENT -- a g1 kernel-path refusal; draining will NOT clear it,
    and reporting it as "deferred" would send the operator to do futile work.
  * SKIPPED means the propose path could not RUN (no `proposal` key in the audit
    payload, e.g. an unloadable index). This is the important one: a broken index
    produces the same empty finding set as a healthy corpus with nothing stale,
    and reporting that as "clean" would let a broken index look like good news
    every single night.

Read-only w.r.t. the KB and index except the SINGLE emitted
`work/auto/stale-review-<fp>/plan.md`. The fingerprint keys on the finding
IDENTITY, not on wall-clock age, so re-running over an unchanged finding set is a
stable no-op rather than a fresh draft per calendar day.

Layer deps: features -> features (`dreamcapture._dream_capture`), features ->
knowledge (`freshsignals.STALE_MAX_AGE_DAYS`, the single stale-window
definition), features -> ops (`ops.audit.cmd_audit`).
Faults: never aborts the cycle -- the outer `_run_cycle` per-step try/except is
the belt.
"""

import argparse
import json

from agentware_lib.core.capture import _dream_capture
# The stale WINDOW comes from its single definition in knowledge, NOT from
# `features.staleproposal`. Reaching it through the emitter would close a cycle
# (staleproposal -> dreamsteps.dreamcapture -> the dreamsteps package -> this
# step -> staleproposal) and, more importantly, would make the step's window a
# re-export of a re-export instead of the one value `audit`, the trust fields and
# the ranking downweight all share.
from agentware_lib.knowledge.freshsignals import STALE_MAX_AGE_DAYS
from agentware_lib.ops.audit import cmd_audit


def _dream_step_stale_propose(kdir, dry_run):
    """Step s — surface stale / superseded / conflicting KB entries as tier-2
    PROPOSED review workorders on the routine cadence, by running the SHIPPED
    `audit --stale --propose` emitter (never a second emit seam).

    NOT change-gated: staleness advances with wall-clock even on a clean tree, so
    (unlike steps c/d) it must run regardless of tree state. Read-only w.r.t. the
    KB/index except the SINGLE emitted `work/auto/stale-review-<fp>/plan.md`; the
    shipped fingerprint slug keys on the finding IDENTITY (not the wall-clock age),
    so a re-run over the same unresolved finding set is a stable no-op. A full g3
    `WORKORDER_DRAFT_CAP` DEFERS gracefully (the emitter refuses with a nonzero exit
    and writes NOTHING → we record `deferred=true` and move on). A fault never
    aborts the cycle — the outer `_run_cycle` per-step try/except is the belt.

    Distinguishes the outcomes via the audit's `proposal` payload:
      EMITTED  -> proposed=<feature>, deferred=False, blocked=False
      DEFERRED -> proposed='-', deferred=True         (g3 draft cap — TRANSIENT)
      BLOCKED  -> proposed='-', blocked=True          (g1 kernel-path guard —
                  PERMANENT; draining work/auto/ will NOT clear it)
      CLEAN    -> proposed='-', deferred=False        (nothing to review)
      SKIPPED  -> status='skipped'                    (audit could not run the
                  propose path, e.g. index unavailable)
    """
    if dry_run:
        return {"status": "planned", "would": "audit --stale --propose"}
    ns = argparse.Namespace(format="json", with_tests=False, stale=True,
                            propose=True, max_age_days=STALE_MAX_AGE_DAYS)
    rc, out = _dream_capture(cmd_audit, ns)
    try:
        payload = json.loads(out) or {}
    except ValueError:
        payload = None
    # The propose path only ran when the index loaded (`proposal` key present).
    # Its ABSENCE (index-load error / unparseable output) is NOT a clean no-op —
    # surface it distinctly so a broken index is not mistaken for "nothing stale".
    if payload is None or "proposal" not in payload:
        return {"status": "skipped", "proposed": "-", "deferred": False,
                "blocked": False,
                "reason": "audit could not run the propose path (rc=%d)" % rc}
    prop = payload.get("proposal") or {}
    proposed, deferred, blocked, reason = "-", False, False, "-"
    if prop.get("proposed"):
        proposed = prop.get("feature") or "-"
    else:
        reason = prop.get("reason") or "-"
        # The emitter reports its OWN refusal class in the reason (`deferred:` =
        # transient g3 cap, drainable; `blocked:` = permanent g1 kernel refusal),
        # so a permanent block never masquerades as a drainable backlog. Read it
        # directly — no post-hoc cap re-read (which a concurrent plan-new/cancel
        # could flip); a clean report ('clean report'/'-') sets neither flag.
        if reason.startswith("deferred"):
            deferred = True
        elif reason.startswith("blocked"):
            blocked = True
    return {"status": "ok", "proposed": proposed, "deferred": deferred,
            "blocked": blocked, "reason": reason}


_EXPORTS = [
    "_dream_step_stale_propose",
]

__all__ = list(_EXPORTS)
