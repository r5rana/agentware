"""agentware_lib.features.skillcandidate -- the on-demand skill-candidate DETECTION verb (layer: features).

Owns: `cmd_skill_candidate_propose` — the backfill / fresh-clone path that ranks
cohesive recurring learning clusters and emits tier-2 PROPOSED skill-candidate
workorders, mirroring `audit --stale --propose`.
Does NOT own: the detection itself -> ops.skillcluster; the shared emit seam ->
features.skillpropose (`_skill_promote_run`); the nightly cadence that runs the
same seam -> features.dreamsteps.stepskillpropose; the PROPOSAL LEDGER lifecycle
that shares the `skill propose` verb name -> features.proposals.proposeauthor.

TWO LIFECYCLES, ONE VERB NAME
-----------------------------
`skill propose` carries two genuinely different things, merged onto one node by
the union fixture (the recorded adjudication):

  * the PROPOSAL LEDGER — `--slug`/`--ref`/`--body`/`--run`/`--worklog-line`/
    `--to-shared`: a human or agent nominating ONE named skill, tracked by
    unique-ref count until it is ripe for promotion.
  * CANDIDATE DETECTION — `--dry-run` and no slug: the corpus-wide sweep that
    finds recurring learning clusters and proposes them as workorders.

They are dispatched on shape rather than on a mode flag, because the two argument
sets are disjoint: a slug names one thing, detection names none. `--slug` and
`--ref` are therefore no longer argparse-REQUIRED — leaving them required would
make `skill propose --dry-run` refuse with "the following arguments are required:
--slug", which is a flag that exists and cannot be used.

DETECTION + PROPOSAL ONLY
-------------------------
It never authors a stub, never runs `index add`, and never installs. Its only
writes are new `work/auto/skill-candidate-*/plan.md` drafts (`Status: draft`,
never auto-started), through the SINGLE shared `_skill_promote_run` seam the
nightly dream step also uses — one seam, so the cap, the dedup and the
idempotency key cannot drift between the two entry points.

Read-only w.r.t. the KB and index, and deliberately its OWN dispatched func: the
predispatch writer-guard keys on `args.func`, so a read-only verb registered
under a writer-registered parent stays exempt by omission rather than by an
exception someone has to remember.

Layer deps: features -> features (`skillpropose._skill_promote_run`), features ->
core (`config`), features -> knowledge (`index`).
"""

import json
import sys

from agentware_lib.core.config import require_knowledge_dir
from agentware_lib.features.skillpropose import _skill_promote_run
from agentware_lib.knowledge.index import load_index


def cmd_skill_candidate_propose(args):
    """On-demand skill-candidate detection + proposal (the backfill / fresh-clone
    path), mirroring `audit --stale --propose`. DETECTION + PROPOSAL ONLY: it never
    authors a stub, never runs `index add`, and never installs — it emits at most
    `cap` tier-2 PROPOSED `work/auto/skill-candidate-*/plan.md` drafts (`Status:
    draft`, never auto-started) through the SINGLE shared `_skill_promote_run` seam.
    `--dry-run` ranks candidate clusters (member ids + scores) WITHOUT emitting.

    Read-only w.r.t. the KB/index (its only writes are new work/auto drafts, via
    `cmd_plan_new` in-process exactly like `cmd_audit`), so it is its OWN dispatched
    func and is deliberately ABSENT from `_kb_writer_funcs()` — the predispatch
    writer-guard keys on `args.func`, so a read-only verb registered by its own func
    stays exempt-by-omission (learn-read-only-subcommand-under-a-writer-registered-
    verb-silently-inherits-its-guard). Named `propose` — NOT `approve` (reserved by
    the shipped `skill sync --approve` install gate) / `scan` / `candidates`."""
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    as_json = getattr(args, "format", "text") == "json"
    data, err = load_index(kdir)
    if err is not None:
        if as_json:
            json.dump({"ok": False, "error": err}, sys.stdout,
                      ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            print("skill propose: index unavailable — %s" % err)
        return 1
    dry_run = bool(getattr(args, "dry_run", False))
    report = _skill_promote_run(kdir, data, dry_run=dry_run)
    if as_json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        _print_skill_propose_report(report, dry_run)
    return 0


_EXPORTS = [
    "cmd_skill_candidate_propose",
]

__all__ = list(_EXPORTS)
