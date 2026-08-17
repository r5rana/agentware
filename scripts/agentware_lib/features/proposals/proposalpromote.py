"""agentware_lib.features.proposals.proposalpromote -- PROMOTION + the `skill` registrar (layer: features).

Owns: the human-gated PROMOTION half of the proposal lifecycle -- the frontmatter
rewrite that turns a proposed entry into an always-on one (_promote_frontmatter:
category references->skills, status proposed->active, `proposed` tag dropped,
promotion provenance stamped) and the `skill promote` handler (cmd_skill_promote),
which enforces the three promotion gates (human sign-off, `--reviewed-by`
adversarial-review sign-off, and a fail-closed vet_skill_dir pass) before MOVING
`skills-proposed/<slug>` -> `skills/<slug>`. PLUS the `skill` verb PARSER
registrar register_skill (wave-6 seam 21) -- it assembles ALL nine skill
sub-verbs: the 6 CATALOG sub-verbs (list / search / validate / add / remove /
roster-injectable) wired to features.skills handlers via the `_skills` sibling
alias, and the 3 LIFECYCLE sub-verbs (propose / proposals / promote) wired to the
handlers of this package's three submodules. This is register_skill's designed
home: proposals already imports skills (acyclic), so its catalog-handler refs
ride the existing edge; homing it in skills.py would instead close a
skills<->proposals cycle. register_skill is NOT in `_EXPORTS`; the assembler
resolves it via `_PARSER_REGISTRATION` importlib off the package aggregator.

Does NOT own: the read-only proposal-ledger + integrity reads (proposal_dir /
proposal_ledger_path / read_proposal_ledger / proposal_count /
existing_proposal_slugs) -> agentware_lib.knowledge.proposals (reached via the
`_kp` module alias); the AUTHORING half (_SLUG_RE / _valid_proposal_slug /
_slug_token_set / near_duplicate_proposal / _render_proposal_skill_md /
cmd_skill_propose) -> the sibling agentware_lib.features.proposals.proposeauthor;
the LISTING/ripeness report (_proposal_refs / _proposal_rows /
RIPE_PROPOSALS_REPORT_REL / write_ripe_proposals_report / cmd_skill_proposals) ->
the sibling agentware_lib.features.proposals.proposalreport; the skill CATALOG
management + static vetter (read_skill_frontmatter / vet_skill_dir) + the 6
catalog sub-verb HANDLERS (cmd_skill_list / search / validate / add / remove /
roster_injectable) register_skill wires -> agentware_lib.features.skills (reached
via the `_skills` sibling alias).

Layer deps: features.proposals.proposalpromote -> core (config / dates /
frontmatter / routing) + knowledge (index; knowledge.proposals via `_kp`) +
features SIBLING (skills via `_skills`; acyclic -- skills does not import
proposals) + its two PACKAGE siblings (proposeauthor / proposalreport, imported
directly, never through the aggregator package -- that would be a circular
import). Cross-module FUNCTIONS use module aliases (never by-name) so the
bytecode global-resolve gate does not drag in their global closures; the
in-package sibling handlers are by-name because they are wired as argparse
`func` defaults, which is a reference, not a call.

Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os
import re
import shutil
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.core.routing as routing
import agentware_lib.knowledge.index as index
import agentware_lib.knowledge.proposals as _kp
import agentware_lib.features.skills as _skills

from .proposalreport import cmd_skill_proposals
from agentware_lib.features.skillcandidate import (
    cmd_skill_candidate_propose,
)
from agentware_lib.features.skillsync import cmd_skill_sync
from .proposeauthor import _valid_proposal_slug, cmd_skill_propose


def _promote_frontmatter(text, promoted_by, reviewed_by, promoted_on):
    """Rewrite a PROPOSED SKILL.md frontmatter into an ALWAYS-ON skill's.

    Flips `category: references`->`skills` (so the indexer routes it into the
    always-on skills roster/injection set — the whole point of promotion),
    `status: proposed`->`status: active`, strips the `proposed` tag, and stamps
    promotion provenance (`promoted_by`, `promoted`, `reviewed_by`) so the
    human-gated + adversarial-review sign-off is auditable on the entry itself.
    Pure string transform over the leading `---` block; body is untouched.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text
    close = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close = i
            break
    if close is None:
        return text
    head, block, tail = lines[:1], lines[1:close], lines[close:]
    out = []
    for ln in block:
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", ln)
        if not m:
            out.append(ln)
            continue
        key, val = m.group(1).strip(), m.group(2)
        if key == "category":
            out.append("category: skills")
        elif key == "status":
            out.append("status: active")
        elif key == "tags":
            # Drop the transient `proposed` tag; keep the rest deterministically.
            toks = [t.strip() for t in val.strip().strip("[]").split(",")]
            toks = [t for t in toks if t and t != "proposed"]
            if not toks:
                toks = ["skill"]
            out.append("tags: [%s]" % ", ".join(toks))
        else:
            out.append(ln)
    # Stamp promotion provenance right before the closing fence.
    out.append("promoted_by: %s" % promoted_by)
    out.append("reviewed_by: %s" % reviewed_by)
    out.append("promoted: %s" % promoted_on)
    return "\n".join(head + out + tail)


def cmd_skill_promote(args):
    """Human-gated promotion of a PROPOSED skill into the always-on `skills/` tier.

    Promotion is deliberately gated on THREE things so the always-on tier is
    populated deliberately, not accidentally:
      1. HUMAN sign-off — an operator runs this command by hand.
      2. ADVERSARIAL-REVIEW sign-off — `--reviewed-by <handle>` records who
         performed the required adversarial content-review (Task 9 surfaces ripe
         proposals; a human still reviews + signs off here). BLOCKS without it.
      3. `vet_skill_dir` static safety pass — BLOCKS (fail-closed) on any finding,
         exactly like `skill add`, but run on the IN-PLACE proposal folder.

    On success it MOVES `skills-proposed/<slug>` -> `skills/<slug>` (explicit
    mkdirs + `shutil.move`; NEVER `copytree(dirs_exist_ok=)` — unavailable on the
    py3.7.16 runtime), rewrites the frontmatter (category->skills, status->active,
    stamped provenance), and rebuilds so it enters the roster + injection set.
    """
    slug = (getattr(args, "slug", None) or "").strip()
    fmt = getattr(args, "format", "text")
    if not _valid_proposal_slug(slug):
        sys.stderr.write("agentware: <slug> must be a lowercase kebab-case "
                         "identifier (got %r)\n" % slug)
        return 1
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1

    pdir = _kp.proposal_dir(kdir, slug)
    proposal_md = os.path.join(pdir, "SKILL.md")
    if not os.path.isfile(proposal_md):
        sys.stderr.write("agentware: no proposal '%s' under %s/ "
                         "(run `skill proposals` to list staged proposals)\n"
                         % (slug, index.SKILLS_PROPOSED_DIR))
        return 1

    # Gate 2: adversarial-review sign-off is MANDATORY (human-gated + reviewed).
    reviewed_by = (getattr(args, "reviewed_by", None) or "").strip()
    if not reviewed_by:
        sys.stderr.write(
            "agentware: promotion is human-gated and requires an "
            "adversarial-review sign-off — re-run with "
            "`--reviewed-by <handle>` once the proposal's content has been "
            "adversarially reviewed.\n")
        return 1

    # Gate 3: skill-vetter static safety pass on the IN-PLACE proposal folder
    # (closes the audit seam that vetting only ran at `skill add` time).
    findings = _skills.vet_skill_dir(pdir)
    if findings:
        sys.stderr.write("agentware: skill-vetter found issues in proposal "
                         "'%s' — refusing to promote (fail-closed):\n" % slug)
        for f in findings:
            sys.stderr.write("  - %s\n" % f)
        if fmt == "json":
            json.dump({"ok": False, "slug": slug, "reason": "vet-findings",
                       "vet_findings": findings}, sys.stdout,
                      ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 1

    dest_dir = os.path.join(kdir, "skills", slug)
    if os.path.exists(dest_dir):
        sys.stderr.write("agentware: a skill already exists at %s — refusing to "
                         "clobber it (remove or rename it first)\n" % dest_dir)
        return 1

    count = _kp.proposal_count(kdir, slug)
    threshold = config.resolve_skill_propose_threshold()

    # Explicit move (py3.7.16 has no copytree(dirs_exist_ok=)). Create the parent
    # skills/ dir, then rename the whole proposal folder (ledger travels with it
    # as the audit trail; it is a non-.md file so it is never indexed).
    os.makedirs(os.path.join(kdir, "skills"), exist_ok=True)
    shutil.move(pdir, dest_dir)

    # Rewrite the promoted SKILL.md: category->skills, status->active, provenance.
    dest_md = os.path.join(dest_dir, "SKILL.md")
    promoted_by = routing.resolve_user_handle() or frontmatter._operator_handle(kdir) or "unknown"
    try:
        with open(dest_md, "r", encoding="utf-8") as f:
            text = f.read()
        with open(dest_md, "w", encoding="utf-8") as f:
            f.write(_promote_frontmatter(text, promoted_by, reviewed_by,
                                         dates._today()))
    except OSError as exc:
        sys.stderr.write("agentware: could not rewrite %s: %s\n" % (dest_md, exc))
        return 1

    # Rebuild so it enters the skills roster + injection set immediately.
    _data, _rosters, rerrs = index.rebuild_kb(kdir)
    if rerrs:
        for e in rerrs:
            sys.stderr.write("agentware: rebuild: %s\n" % e)
        return 1

    if fmt == "json":
        json.dump({"ok": True, "slug": slug, "id": "skill-%s" % slug,
                   "promoted_by": promoted_by, "reviewed_by": reviewed_by,
                   "proposal_count": count, "threshold": threshold,
                   "path": "skills/%s/SKILL.md" % slug}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("PROMOTED proposal '%s' -> skills/%s/ (count=%d/%d, reviewed-by=%s)"
              % (slug, slug, count, threshold, reviewed_by))
        print("Now an always-on skill: skill-%s (category=skills)." % slug)
    return 0



def register_skill(sub, fmt_parent):
    # skill ------------------------------------------------------------------
    # The authored skill catalog + installer. EXTENSIBLE:
    # auto-skill-promotion adds promotion verbs (scan/candidates/show/approve/
    # reject) to this SAME group — keep `list` == installed catalog skills.
    p_skill = sub.add_parser(
        "skill", help="manage the authored skill catalog (list/search/add/remove/validate)")
    skill_sub = p_skill.add_subparsers(dest="skill_command", metavar="<subcommand>")
    skill_sub.required = True

    p_sk_list = skill_sub.add_parser(
        "list", parents=[fmt_parent], help="list authored catalog skills")
    p_sk_list.add_argument("--category", default=None,
                           help="filter by category (security|productivity|gap-filler)")
    p_sk_list.set_defaults(func=_skills.cmd_skill_list)

    p_sk_search = skill_sub.add_parser(
        "search", parents=[fmt_parent], help="search the catalog by name/summary/tag")
    p_sk_search.add_argument("query", help="case-insensitive substring query")
    p_sk_search.set_defaults(func=_skills.cmd_skill_search)

    p_sk_val = skill_sub.add_parser(
        "validate", parents=[fmt_parent],
        help="validate catalog skills (frontmatter name==folder, vet clean)")
    p_sk_val.add_argument("--name", default=None,
                          help="validate only this skill (default: all)")
    p_sk_val.add_argument("--source", default=None,
                          help="validate an IN-PLACE authored skill DIR directly "
                               "(runs vet_skill_dir; no catalog entry needed)")
    p_sk_val.set_defaults(func=_skills.cmd_skill_validate)

    p_sk_add = skill_sub.add_parser(
        "add", parents=[fmt_parent],
        help="vet + install a TRUSTED catalog skill into the KB (or a harness path)")
    p_sk_add.add_argument("name", nargs="?", default=None,
                          help="catalog skill name (omit only with --source)")
    p_sk_add.add_argument("--source", default=None,
                          help="LOCAL path to a non-catalog skill (gated; never a URL)")
    p_sk_add.add_argument("--allow-external", action="store_true",
                          dest="allow_external",
                          help="confirm install of an external (--source) skill")
    p_sk_add.add_argument("--harness-path", default=None, dest="harness_path",
                          help="install into this harness skills dir instead of the "
                               "indexed external KB (e.g. .agents/skills)")
    p_sk_add.set_defaults(func=_skills.cmd_skill_add)

    p_sk_rm = skill_sub.add_parser(
        "remove", parents=[fmt_parent],
        help="unregister + remove a KB-installed skill")
    p_sk_rm.add_argument("name", help="skill name to remove")
    p_sk_rm.add_argument("--keep-files", action="store_true", dest="keep_files",
                         help="unregister from the index but leave the files on disk")
    p_sk_rm.set_defaults(func=_skills.cmd_skill_remove)

    p_sk_inj = skill_sub.add_parser(
        "roster-injectable", parents=[fmt_parent],
        help="exit 0 iff a rendered roster file has >=1 real always-on entry "
             "(shared predicate the SessionStart hook + index-validate linter call)")
    p_sk_inj.add_argument("path", help="path to a rendered <sub>/index.md roster")
    p_sk_inj.set_defaults(func=_skills.cmd_skill_roster_injectable)

    # skill sync (feature 260713-p4-skills-two-tier) — reconcile the canonical KB
    # skills/ against the per-machine VETTED execution cache. The bare reconcile
    _register_skill_cache(skill_sub, fmt_parent)
    _register_skill_lifecycle(skill_sub, fmt_parent)


def _register_skill_cache(skill_sub, fmt_parent):
    """The EXECUTION-CACHE subverb: `skill sync`.

    Split out purely to hold the per-function line cap — the registrar
    grew past 120 lines when the install gate landed. The seam follows the
    handlers: cache management lives in features.skillsync, the lifecycle
    verbs below live in this package.
    """
    # NEVER installs; explicit --approve is the ONLY install path.
    p_sk_sync = skill_sub.add_parser(
        "sync", parents=[fmt_parent],
        help="reconcile KB skills/ -> per-machine vetted execution cache "
             "(quarantine-on-change; --approve to install)")
    p_sk_sync.add_argument("--approve", default=None, metavar="NAME[,NAME...]",
                           help="re-vet + install the named quarantined skill(s) "
                                "into the execution cache (the ONLY install path)")
    p_sk_sync.add_argument("--reject", default=None, metavar="NAME",
                           help="block a skill (removes an owned cache copy)")
    p_sk_sync.add_argument("--uninstall", default=None, metavar="NAME",
                           help="remove an owned cache copy + drop its lockfile entry")
    p_sk_sync.add_argument("--reset", action="store_true",
                           help="remove ALL owned cache copies + clear the lockfile")
    p_sk_sync.add_argument("--status", action="store_true",
                           help="show the installed / pending / blocked roster")
    p_sk_sync.add_argument("--status-line", action="store_true", dest="status_line",
                           help="read-only one-block roster for the session-start hook")
    p_sk_sync.add_argument("--harness-path", default=None, dest="harness_path",
                           help="execution-cache dir (default ~/.claude/skills)")
    p_sk_sync.add_argument("--force-shadow", action="store_true", dest="force_shadow",
                           help="allow approving a name that shadows a PACKAGE skill")
    p_sk_sync.add_argument("--force-conflict", action="store_true", dest="force_conflict",
                           help="allow approving over a FOREIGN (unowned) cache dir")
    p_sk_sync.set_defaults(func=cmd_skill_sync)


def _register_skill_lifecycle(skill_sub, fmt_parent):
    """The PROPOSAL-lifecycle subverbs: candidates / propose / proposals /
    promote."""
    # `skill candidates` — the on-demand CORPUS SWEEP. Preserved as its own
    # subverb rather than folded into `propose`, because the union fixture keeps
    # --slug/--ref REQUIRED on `propose`, and a sweep names no slug. Dropping it
    # instead would have been a silent capability loss at the moment of
    # promotion: the monolith reaches this today, and the promotion gate would
    # not have noticed, since it checks for MISSING union nodes and this one is
    # additive. The nightly dream step `k` runs the same shared emit seam, so the
    # two entry points cannot drift.
    p_sk_cand = skill_sub.add_parser(
        "candidates", parents=[fmt_parent],
        help="detect cohesive recurring learnings + PROPOSE tier-2 "
             "skill-candidate workorders (DRAFT, never auto-started; "
             "--dry-run to rank only)")
    p_sk_cand.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="rank candidate clusters (member ids + scores) WITHOUT emitting")
    p_sk_cand.set_defaults(func=cmd_skill_candidate_propose)

    p_sk_prop = skill_sub.add_parser(
        "propose", parents=[fmt_parent],
        help="create-or-increment an agent-PROPOSED skill under skills-proposed/ "
             "(recall-only candidate; graduates to always-on via `skill promote`)")
    p_sk_prop.add_argument("--slug", required=True,
                           help="stable lowercase kebab-case slug (the proposal id)")
    p_sk_prop.add_argument("--ref", required=True,
                           help="the feature/run this proposal occurred in "
                                "(uniqueness key for proposal_count)")
    p_sk_prop.add_argument("--body", default=None,
                           help="proposal SKILL.md body ('-' reads stdin); "
                                "first non-empty line becomes the description")
    p_sk_prop.add_argument("--run", default=None,
                           help="optional run id recorded in the ledger event")
    p_sk_prop.add_argument("--worklog-line", default=None, dest="worklog_line",
                           help="optional worklog line reference for the ledger")
    p_sk_prop.add_argument("--to-shared", action="store_true", dest="to_shared",
                           help="curator-only: propose into the shared/ root (CR-gated)")
    # --dry-run on the LEDGER path: compute the proposal and report what WOULD
    # be recorded (new vs increment, the near-duplicate refusal, the resulting
    # count) without writing the ledger or touching the index. The union fixture
    # keeps --slug/--ref REQUIRED on this node, so every invocation names one
    # proposal; a dry run therefore means "show me this proposal's effect",
    # not "sweep the corpus".
    p_sk_prop.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="report what this proposal WOULD record (new / increment / "
             "near-duplicate refusal) without writing the ledger or index")
    p_sk_prop.set_defaults(func=cmd_skill_propose)

    p_sk_props = skill_sub.add_parser(
        "proposals", parents=[fmt_parent],
        help="list staged skill proposals + their frequency counts (--ripe filters)")
    p_sk_props.add_argument("--ripe", action="store_true",
                            help="show ONLY proposals ripe for promotion "
                                 "(proposal_count >= threshold)")
    p_sk_props.set_defaults(func=cmd_skill_proposals)

    p_sk_promo = skill_sub.add_parser(
        "promote", parents=[fmt_parent],
        help="human-gated promotion of a PROPOSED skill into the always-on "
             "skills/ tier (requires --reviewed-by; runs vet_skill_dir; BLOCKS "
             "on findings)")
    p_sk_promo.add_argument("slug", help="proposal slug to promote")
    p_sk_promo.add_argument("--reviewed-by", default=None, dest="reviewed_by",
                            help="handle of the reviewer who performed the "
                                 "required adversarial content-review (mandatory)")
    p_sk_promo.set_defaults(func=cmd_skill_promote)


_EXPORTS = [
    "_promote_frontmatter",
    "cmd_skill_promote",
]
