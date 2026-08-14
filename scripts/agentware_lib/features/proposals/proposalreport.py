"""agentware_lib.features.proposals.proposalreport -- proposal LISTING + ripeness report (layer: features).

Owns: the read-only REPORTING half of the proposal lifecycle -- the unique-ref
enumerator (_proposal_refs), the single deterministic row builder every consumer
shares (_proposal_rows, sorted by (-count, slug)), the machine-local nudge-report
path + writer (RIPE_PROPOSALS_REPORT_REL / write_ripe_proposals_report) and the
`skill proposals` handler (cmd_skill_proposals). _proposal_rows is the ONE source
of truth behind the listing, the ripeness report and the POST-phase nudge, so all
three always agree.

Does NOT own: the read-only proposal-ledger + integrity reads (proposal_dir /
proposal_ledger_path / read_proposal_ledger / proposal_count /
existing_proposal_slugs) -> agentware_lib.knowledge.proposals (reached via the
`_kp` module alias); the AUTHORING half (_SLUG_RE / _valid_proposal_slug /
_slug_token_set / near_duplicate_proposal / _render_proposal_skill_md /
cmd_skill_propose) -> the sibling
agentware_lib.features.proposals.proposeauthor; the PROMOTION half
(_promote_frontmatter / cmd_skill_promote) + the `skill` verb registrar
register_skill -> the sibling agentware_lib.features.proposals.proposalpromote;
the ripeness THRESHOLD resolver (resolve_skill_propose_threshold) ->
agentware_lib.core.config.

Layer deps: features.proposals.proposalreport -> core (config) + knowledge
(knowledge.proposals via `_kp`) -- all DOWNWARD for the features layer. Nothing
here writes to the knowledge base except the gitignored `logs/` nudge report,
which is best-effort by design (a nudge must never crash a run).

Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os
import sys

import agentware_lib.core.config as config
import agentware_lib.knowledge.proposals as _kp


def _proposal_refs(kdir, slug):
    """Sorted list of UNIQUE refs (features) accumulated on a proposal."""
    refs = set()
    for ev in _kp.read_proposal_ledger(kdir, slug):
        r = ev.get("ref")
        if r:
            refs.add(r)
    return sorted(refs)


def _proposal_rows(kdir, ripe_only=False):
    """Deterministic proposal rows sorted by (-count, slug).

    Each row: {slug, proposal_count, threshold, ripe, refs}. The single source
    of truth behind `skill proposals`, the ripeness report, and the POST-phase
    nudge, so all three agree.
    """
    threshold = config.resolve_skill_propose_threshold()
    rows = []
    for slug in _kp.existing_proposal_slugs(kdir):
        count = _kp.proposal_count(kdir, slug)
        rows.append({"slug": slug, "proposal_count": count,
                     "threshold": threshold, "ripe": count >= threshold,
                     "refs": _proposal_refs(kdir, slug)})
    if ripe_only:
        rows = [r for r in rows if r["ripe"]]
    rows.sort(key=lambda r: (-r["proposal_count"], r["slug"]))
    return rows


RIPE_PROPOSALS_REPORT_REL = os.path.join("logs", "skill-proposals-ripe.md")


def write_ripe_proposals_report(kdir):
    """Write the deterministic ripe-proposals nudge report (machine-local).

    Enumerates every ripe proposal (slug, unique-ref count, refs) into
    `<kdir>/logs/skill-proposals-ripe.md`. Deterministic (rows are already
    sorted, refs sorted). `logs/` is gitignored, so this stays machine-local and
    never reaches the remote. Below-threshold proposals are NOT written (silent).
    Returns the list of ripe rows (empty if none). Best-effort: an unwritable
    logs/ dir is swallowed (a nudge report must never crash a run).
    """
    ripe = _proposal_rows(kdir, ripe_only=True)
    path = os.path.join(kdir, RIPE_PROPOSALS_REPORT_REL)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = ["# Skill proposals ripe for promotion", ""]
        threshold = config.resolve_skill_propose_threshold()
        if not ripe:
            lines.append("_No proposals have reached the ripeness threshold "
                         "(%d) yet._" % threshold)
        else:
            lines.append("These staged proposals have reached the ripeness "
                         "threshold (>= %d distinct feature refs). Promotion is "
                         "ALWAYS human-gated — review, then "
                         "`scripts/agentware skill promote <slug> "
                         "--reviewed-by <handle>`." % threshold)
            lines.append("")
            lines.append("| Slug | Count | Refs |")
            lines.append("|------|-------|------|")
            for r in ripe:
                lines.append("| %s | %d/%d | %s |"
                             % (r["slug"], r["proposal_count"], r["threshold"],
                                ", ".join(r["refs"]) or "-"))
        lines.append("")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError:
        pass
    return ripe


def cmd_skill_proposals(args):
    """List staged skill proposals with their frequency counts (`--ripe` filters).

    A proposal is "ripe" (surfaced for human-gated promotion) when its
    unique-ref `proposal_count` >= `AGENTWARE_SKILL_PROPOSE_THRESHOLD` (default
    5). Read-only. Also (re)writes the deterministic ripe-proposals nudge report.
    """
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    threshold = config.resolve_skill_propose_threshold()
    # Refresh the deterministic nudge report each listing (machine-local).
    write_ripe_proposals_report(kdir)
    rows = _proposal_rows(kdir, ripe_only=getattr(args, "ripe", False))
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": True, "threshold": threshold, "count": len(rows),
                   "proposals": rows}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        if not rows:
            print("No skill proposals%s."
                  % (" ripe for promotion" if getattr(args, "ripe", False)
                     else ""))
        else:
            for r in rows:
                print("%-30s count=%d/%d%s  refs=%s"
                      % (r["slug"], r["proposal_count"], r["threshold"],
                         "  [RIPE]" if r["ripe"] else "",
                         ",".join(r["refs"]) or "-"))
    return 0


_EXPORTS = [
    "_proposal_refs",
    "_proposal_rows",
    "RIPE_PROPOSALS_REPORT_REL",
    "write_ripe_proposals_report",
    "cmd_skill_proposals",
]
