"""agentware_lib.features.proposals.proposeauthor -- AUTHORING a skill proposal (layer: features).

Owns: the create-or-increment AUTHORING half of the proposal lifecycle -- the slug
grammar (_SLUG_RE / _valid_proposal_slug), the dedup similarity primitives
(_slug_token_set / near_duplicate_proposal), the proposed-SKILL.md renderer
(_render_proposal_skill_md) and the `skill propose` handler (cmd_skill_propose),
which validates the slug, guards near-duplicates so the frequency signal
accumulates on ONE proposal, creates-or-increments the append-only `.jsonl`
references ledger and rebuilds so the proposal is recall-visible immediately.

Does NOT own: the read-only proposal-ledger + integrity reads (proposal_dir /
proposal_ledger_path / read_proposal_ledger / proposal_count /
existing_proposal_slugs) -> agentware_lib.knowledge.proposals (reached via the
`_kp` module alias); the LISTING/ripeness report (_proposal_refs / _proposal_rows /
RIPE_PROPOSALS_REPORT_REL / write_ripe_proposals_report / cmd_skill_proposals) ->
the sibling agentware_lib.features.proposals.proposalreport; the PROMOTION half
(_promote_frontmatter / cmd_skill_promote) + the `skill` verb registrar
register_skill -> the sibling agentware_lib.features.proposals.proposalpromote;
the skill CATALOG management + static vetter (read_skill_frontmatter /
vet_skill_dir) -> agentware_lib.features.skills (reached via the `_skills`
sibling alias).

Layer deps: features.proposals.proposeauthor -> core (config / dates /
frontmatter / routing) + knowledge (index; knowledge.proposals via `_kp`) + ops
(fleet via `_fleet` for guard_shared_write; freshness `_CONFLICT_JACCARD`
by-name -- a DEFAULT-ARG, so it is a MODULE-level import, not lazy) + retrieval
(corpus via `_corpus` for tokenize) + features SIBLING (skills via `_skills`;
acyclic -- skills does not import proposals). Cross-module FUNCTIONS use module
aliases (never by-name) so the bytecode global-resolve gate does not drag in
their global closures.

Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os
import re
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.core.routing as routing
import agentware_lib.knowledge.index as index
import agentware_lib.knowledge.proposals as _kp
import agentware_lib.features.skills as _skills
import agentware_lib.retrieval.corpus as _corpus
import agentware_lib.ops.fleet as _fleet
from agentware_lib.ops.freshness import _CONFLICT_JACCARD


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


def _valid_proposal_slug(slug):
    """A proposal slug is a lowercase kebab identifier (folder-safe, id-safe)."""
    return bool(slug) and bool(_SLUG_RE.match(slug))


# proposal_dir / proposal_ledger_path / read_proposal_ledger / proposal_count /
# existing_proposal_slugs -> agentware_lib.knowledge.proposals (wave-6 seam 7).
# The read-only proposal-staging path resolvers + ledger reader + derived
# worthiness count + staged-slug enumerator. Imported BY NAME above.


def _slug_token_set(slug, body=None):
    """Token set for the dedup similarity check.

    Uses the SAME tokenizer BM25 uses (deterministic), over the slug plus any
    body text, so a near-duplicate procedure filed under a slightly different
    slug is caught and the caller is forced to REUSE the existing slug (so counts
    accumulate on ONE proposal rather than fragmenting).
    """
    return set(_corpus.tokenize("%s %s" % (slug.replace("-", " "), body or "")))


def near_duplicate_proposal(kdir, slug, body=None, threshold=_CONFLICT_JACCARD):
    """Return an existing slug whose token-set Jaccard vs `slug` >= threshold, else None.

    Reuses the shipped `_CONFLICT_JACCARD=0.6` primitive. An EXACT slug match is
    NOT a near-duplicate — that is the create-or-increment path. Read-only.
    """
    target = _slug_token_set(slug, body)
    if not target:
        return None
    best, best_jac = None, 0.0
    for other in _kp.existing_proposal_slugs(kdir):
        if other == slug:
            continue
        ofm = _skills.read_skill_frontmatter(os.path.join(_kp.proposal_dir(kdir, other),
                                                   "SKILL.md"))
        oset = _slug_token_set(other, ofm.get("description"))
        union = target | oset
        if not union:
            continue
        jac = len(target & oset) / len(union)
        if jac >= threshold and jac > best_jac:
            best, best_jac = other, jac
    return best


def _render_proposal_skill_md(slug, kdir, body, author, created):
    """Compose a proposed SKILL.md: full hybrid frontmatter + body.

    Stamps `id: skill-<slug>`, `name`, `category: references` (so the indexer
    never routes it into the skills roster/injection set — Task 5 invariant),
    `description`, `tags`, `author`, `created`, `status: proposed`, and — when the
    kdir resolves an org team — `origin`/`team` so `index rebuild` does not abort
    on missing provenance (F12/F13) and org lint O1 is satisfied. A flat KB emits
    neither org key (byte-identical to a single-team install).
    """
    # First non-empty body line is the routing description; fall back to a stub.
    desc = ""
    for ln in (body or "").splitlines():
        ln = ln.strip().lstrip("#").strip()
        if ln:
            desc = ln
            break
    if not desc:
        desc = "Proposed skill candidate '%s' (awaiting frequency + promotion)." % slug
    lines = ["---",
             "id: skill-%s" % slug,
             "name: %s" % slug,
             "category: references",
             "description: %s" % desc,
             "tags: [skill, proposed]",
             "author: %s" % author,
             "created: %s" % created,
             "status: proposed"]
    origin, team = config._entry_origin_team({}, kdir)
    if origin:
        lines.append("origin: %s" % origin)
    if team:
        lines.append("team: %s" % team)
    lines.append("---")
    lines.append("")
    header = "# %s\n\n" % slug
    return "\n".join(lines) + "\n" + header + (body.rstrip("\n") + "\n" if body.strip() else "")


def cmd_skill_propose(args):
    """Create-or-increment an agent-PROPOSED skill under `skills-proposed/<slug>`.

    A proposal is a recall-only skill CANDIDATE that graduates to an always-on
    `skills/` skill ONLY on human approval (`skill promote`, Task 7). This command
    is what the POST phase calls for every reusable-procedure candidate:

      - NEW slug  -> create `skills-proposed/<slug>/SKILL.md` (full hybrid
        frontmatter, `status: proposed`) + seed the `.jsonl` ledger at count=1.
      - KNOWN slug -> APPEND a ledger event; `proposal_count` = unique refs, so a
        DISTINCT --ref increments and a REPEAT --ref does NOT inflate.
      - NEAR-DUPLICATE slug (Jaccard >= _CONFLICT_JACCARD vs an existing proposal)
        -> REFUSE with "did you mean <slug>?", forcing reuse so counts accumulate
        on ONE proposal instead of fragmenting across near-identical slugs.

    The ledger is append-only `.jsonl` (git-merge-friendly; the count is derived,
    never stored authoritatively), and `index rebuild` is run so the proposal is
    immediately recall-visible (but never in the skills roster — Task 5).
    """
    slug = (getattr(args, "slug", None) or "").strip()
    ref = (getattr(args, "ref", None) or "").strip()
    fmt = getattr(args, "format", "text")
    if not _valid_proposal_slug(slug):
        sys.stderr.write("agentware: --slug must be a lowercase kebab-case "
                         "identifier (got %r)\n" % slug)
        return 1
    if not ref:
        sys.stderr.write("agentware: --ref must not be empty (the feature/run "
                         "this proposal occurred in)\n")
        return 1

    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    ok, gerr = _fleet.guard_shared_write(kdir, to_shared=getattr(args, "to_shared", False))
    if not ok:
        sys.stderr.write("agentware: %s\n" % gerr)
        return 1

    body = ""
    if getattr(args, "body", None) is not None:
        body = sys.stdin.read() if args.body == "-" else args.body

    pdir = _kp.proposal_dir(kdir, slug)
    skill_md = os.path.join(pdir, "SKILL.md")
    exists = os.path.isfile(skill_md)

    # Dedup guard ONLY on a genuinely NEW slug (an exact match is increment, not
    # a near-duplicate). Forces reuse so a near-identical procedure accumulates on
    # the existing proposal instead of fragmenting the frequency signal.
    if not exists:
        dup = near_duplicate_proposal(kdir, slug, body)
        if dup is not None:
            sys.stderr.write(
                "agentware: '%s' looks like a near-duplicate of an existing "
                "proposal — did you mean '%s'? Re-run `skill propose --slug %s "
                "--ref %s` to increment it (reuse keeps the frequency signal on "
                "one proposal).\n" % (slug, dup, dup, ref))
            if fmt == "json":
                json.dump({"ok": False, "slug": slug, "reason": "near-duplicate",
                           "did_you_mean": dup}, sys.stdout,
                          ensure_ascii=False, indent=2)
                sys.stdout.write("\n")
            return 1

    author = routing.resolve_user_handle() or frontmatter._operator_handle(kdir)
    created = dates._today()
    if not exists:
        os.makedirs(pdir, exist_ok=True)
        with open(skill_md, "w", encoding="utf-8") as f:
            f.write(_render_proposal_skill_md(slug, kdir, body, author, created))

    # Append the ledger event (append-only; one line per occurrence). The count
    # is DERIVED from unique refs — the stored events are the audit trail.
    event = {"ref": ref, "author": author, "date": created}
    run = getattr(args, "run", None)
    if run:
        event["run"] = run
    wl = getattr(args, "worklog_line", None)
    if wl:
        event["worklog_line"] = wl
    with open(_kp.proposal_ledger_path(kdir, slug), "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    # Recall-visible immediately (never in the skills roster — Task 5 invariant).
    _data, _rosters, rerrs = index.rebuild_kb(kdir)
    if rerrs:
        for e in rerrs:
            sys.stderr.write("agentware: rebuild: %s\n" % e)
        return 1

    count = _kp.proposal_count(kdir, slug)
    threshold = config.resolve_skill_propose_threshold()
    ripe = count >= threshold
    if fmt == "json":
        json.dump({"ok": True, "slug": slug, "created": not exists,
                   "incremented": exists, "ref": ref,
                   "proposal_count": count, "threshold": threshold,
                   "ripe": ripe,
                   "path": "%s/%s/SKILL.md" % (index.SKILLS_PROPOSED_DIR, slug)},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("%s proposal '%s' (ref=%s) — proposal_count=%d/%d%s"
              % ("CREATED" if not exists else "INCREMENTED", slug, ref,
                 count, threshold, "  [RIPE]" if ripe else ""))
    return 0


# resolve_skill_propose_threshold + _parse_skill_propose_threshold now live in
# agentware_lib.core.config (they read only config-owned constants).


_EXPORTS = [
    "_SLUG_RE",
    "_valid_proposal_slug",
    "_slug_token_set",
    "near_duplicate_proposal",
    "_render_proposal_skill_md",
    "cmd_skill_propose",
]
