"""agentware_lib.ops.orgmigrate — migration 002's body: additive team-mode scaffold (layer: ops).

Owns: the ADDITIVE org/team-mode migration in full — the engagement resolver
(_org_root_for, which decides whether a knowledge dir is org-shaped at all), the
shared-root scaffolder (_scaffold_shared_dir), the pure frontmatter line-insert
(_insert_org_lines) and its file-level writer (stamp_entry_org_fields), the
per-corpus backfill walk (_backfill_org_frontmatter) and the migration entry point
run_team_mode_scaffold.
Does NOT own: the migration REGISTRY, the schema sidecar or the runner that calls
this (agentware_lib.features.migratecmd — one schema authority, per its interlock
docstring); the team-dir scaffolder + registry rows (agentware_lib.ops.team); the
org key names / layout constants / optional frontmatter field tuple
(agentware_lib.core.orgkeys); the (origin, team) inference rule
(agentware_lib.core.config.orgresolve._entry_origin_team); the frontmatter grammar
(agentware_lib.core.frontmatter); the entry-file scan (agentware_lib.knowledge.
index); the org INVARIANTS this makes satisfiable (agentware_lib.ops.orglint).
Layer deps: ops.orgmigrate -> core (config, frontmatter, kblayout, orgkeys) +
knowledge.index DOWNWARD + the SAME-LAYER siblings ops.team, ops.fleet and
ops.planauthor.planedit (for the atomic write). Nothing imports it at module
level: features.migratecmd resolves it LAZILY at call time, so the CLI's hot
import path (cli -> features.migratecmd) never pays for this module's fan-out.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by).

WHY THIS MIGRATION IS ADDITIVE, AND WHAT "ADDITIVE" HAD TO MEAN HERE
--------------------------------------------------------------------
Team mode is a SHAPE change to a knowledge base: an org root gains `teams/<team>/`
and `shared/`, and an entry gains the optional `origin:`/`team:` provenance fields.
The registered-migration tier says an additive step may auto-apply unattended, so
every operation here is bounded by three properties, in this order:

  1. A FLAT install is NOT TOUCHED. With no org root resolving, this returns an
     empty summary having created nothing and read nothing (INV-1 / R-ORG-04). The
     `.schema` stamp the runner writes afterwards is the only trace, and it is the
     runner's, not this step's.
  2. NOTHING MOVES. Existing entries stay exactly where they are; the direct
     knowledge-dir pointer keeps winning in resolve_knowledge_dir, so an operator
     who enables team mode does not wake up with a relocated corpus. Entry COUNTS
     are therefore invariant across this step by construction — it creates
     directories and appends frontmatter lines, and has no delete or rename path.
  3. NOTHING IS OVERWRITTEN. An entry that already declares `origin:`/`team:` keeps
     its own values, and an entry under `teams/<T>` DECLARING a different team is
     recorded as a conflict and left alone: that is org lint's O13 finding to
     report, and silently rewriting it would destroy the evidence of a cross-team
     write while making the lint pass.

The frontmatter stamp is a SURGICAL LINE INSERT, not a re-render. Re-rendering
through render_frontmatter would normalise quoting, field order and list spacing
across every entry it touched — a whole-corpus diff for a two-line change, and one
the losslessness rehearsal could not read. So the block is split at its closing
`---`, the missing org lines are inserted in _ORG_FRONTMATTER_FIELDS order (the
same order render_frontmatter emits, so a stamped entry is byte-identical to one
born with the fields), and every other byte of the file is preserved.

WHY THE BACKFILL IS SCOPED TO teams/ AND shared/
------------------------------------------------
An entry's team facet is a fact about WHERE IT LIVES (see
config._entry_origin_team, which infers exactly that). Under `teams/<T>` the fields
are derivable; at the org ROOT they are not — a flat corpus co-resident with a
teams/ tree belongs to no team, and stamping the caller's team onto it would assert
a provenance the layout does not support and pollute every future merged view. So
the walk covers `teams/*` and `shared/` and deliberately stops there.
"""

import os
import sys

import agentware_lib.core.config as config
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.core.kblayout as kblayout
import agentware_lib.core.orgkeys as orgkeys
import agentware_lib.knowledge.index as index
import agentware_lib.ops.fleet as _fleet
import agentware_lib.ops.planauthor.planedit as planedit
import agentware_lib.ops.team as _team


#: The shared-root MAIN stub. Deliberately plain prose: `org lint` O8 scans every
#: shared `*.md` for injection markers, so the one file this migration seeds into
#: the shared corpus must not itself trip the read-time content guard.
SHARED_MAIN_STUB = (
    "# Shared — Org Knowledge Base (MAIN)\n\n"
    "> Curated cross-team knowledge. Writes here are curator-gated; a team writes\n"
    "> to its own `teams/<team>/` folder and promotes into this root deliberately.\n\n"
    "## Contents\n\n| Area | Notes |\n|------|-------|\n"
)


def _empty_summary():
    """The migration's report shape. Every key is present whether or not the step
    engaged, so a caller (and the journal) never has to branch on absence."""
    return {"engaged": False, "org_root": None, "reason": None,
            "created": [], "stamped": [], "already": [], "conflicts": [],
            "skipped_no_frontmatter": []}


def _org_root_for(kdir):
    """The org root governing `kdir`, or None when this is a flat install.

    Engagement is deliberately the SAME question `config._entry_origin_team` already
    answers for a single entry, lifted to the corpus: an explicit org root, else a
    knowledge dir whose path IS an org facet (`…/teams/<team>` or an org `…/shared`
    beside a `teams/`). Anything else is flat and this migration is a no-op there —
    which is what keeps a flat KB byte-identical (INV-1).
    """
    root = config.resolve_org_root()
    if root:
        return root
    if config._infer_team_from_kdir(kdir):
        return _fleet._fleet_org_root(kdir)
    if config._kdir_is_shared_root(kdir):
        parent = os.path.dirname(os.path.normpath(kdir or ""))
        if os.path.isdir(os.path.join(parent, orgkeys.ORG_TEAMS_DIR)):
            return parent
    return None


def _scaffold_shared_dir(org_root):
    """Create `<org-root>/shared/` with the canonical knowledge structure.

    Idempotent (makedirs exist_ok; never clobbers an existing MAIN or index).
    Knowledge subdirs only — `shared/` is a curated CORPUS, not a work area, so it
    gets no work/logs/profiles tree. Returns the absolute path.
    """
    sdir = os.path.join(org_root, orgkeys.ORG_SHARED_DIR)
    for sub in ("",) + index.KNOWLEDGE_SUBDIRS:
        os.makedirs(os.path.join(sdir, sub) if sub else sdir, exist_ok=True)
    main = os.path.join(sdir, kblayout.MAIN_REL)
    if not os.path.exists(main):
        with open(main, "w", encoding="utf-8") as f:
            f.write(SHARED_MAIN_STUB)
    kblayout._ensure_kb_gitignore(sdir)
    if not os.path.exists(index.index_path(sdir)):
        index.save_index(sdir, {"entries": [], "tags": {}})
    return sdir


def _known_teams(org_root, kdir):
    """Every team folder that should exist after this step, sorted.

    The union of the teams already on disk and the caller's own team (from
    AGENTWARE_TEAM, else inferred from the knowledge dir path) — so enabling team
    mode scaffolds the caller's folder without inventing anyone else's.
    """
    names = set()
    teams_dir = os.path.join(org_root, orgkeys.ORG_TEAMS_DIR)
    if os.path.isdir(teams_dir):
        names.update(d for d in os.listdir(teams_dir)
                     if os.path.isdir(os.path.join(teams_dir, d)))
    mine = config.resolve_team() or config._infer_team_from_kdir(kdir)
    if mine:
        names.add(mine)
    return sorted(names)


def _ensure_org_dirs(org_root, kdir, summary):
    """Create `teams/`, each known `teams/<team>/` and `shared/` (idempotent).

    Records ONLY what was absent beforehand in `summary["created"]`, so a second
    run reports an empty list — the observable half of idempotency.
    """
    teams_dir = os.path.join(org_root, orgkeys.ORG_TEAMS_DIR)
    if not os.path.isdir(teams_dir):
        os.makedirs(teams_dir, exist_ok=True)
        summary["created"].append(orgkeys.ORG_TEAMS_DIR)
    for team in _known_teams(org_root, kdir):
        tdir = os.path.join(teams_dir, team)
        existed = os.path.isdir(tdir)
        _team._scaffold_team_dir(org_root, team)
        if not existed:
            summary["created"].append("%s/%s" % (orgkeys.ORG_TEAMS_DIR, team))
    shared = os.path.join(org_root, orgkeys.ORG_SHARED_DIR)
    existed = os.path.isdir(shared)
    _scaffold_shared_dir(org_root)
    if not existed:
        summary["created"].append(orgkeys.ORG_SHARED_DIR)


def _insert_org_lines(text, additions):
    """`text` with `additions` inserted before the frontmatter block's closing
    `---`, or None when the file carries no block.

    Pure. Values are emitted through the frontmatter module's own scalar emitter,
    so a stamped line is byte-identical to the one render_frontmatter would have
    written for the same value — one quoting rule, not two.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    close = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close = i
            break
    if close is None:
        return None
    new = ["%s: %s" % (k, frontmatter._fm_emit_scalar(v)) for k, v in additions]
    return "\n".join(lines[:close] + new + lines[close:])


def stamp_entry_org_fields(path, origin, team):
    """Add the missing org provenance fields to ONE entry file.

    Returns one of: `stamped` (the file was rewritten), `present` (it already
    declared everything), `conflict` (it declares a DIFFERENT team — left alone,
    for org lint O13 to report), `no-frontmatter` (a legacy blockquote entry —
    left alone; `index migrate-frontmatter` owns that conversion) or `unreadable`.
    Never overwrites a declared value; never touches the body.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return "unreadable"
    fields, _body = frontmatter.split_frontmatter(text)
    if fields is None:
        return "no-frontmatter"
    declared = fields.get("team")
    if declared and team and declared != team:
        return "conflict"
    additions = []
    if origin and not fields.get("origin"):
        additions.append(("origin", origin))
    if team and not fields.get("team"):
        additions.append(("team", team))
    if not additions:
        return "present"
    out = _insert_org_lines(text, additions)
    if out is None:
        return "no-frontmatter"
    planedit._atomic_write(path, out)
    return "stamped"


_OUTCOME_KEYS = {"stamped": "stamped", "present": "already",
                 "conflict": "conflicts", "no-frontmatter": "skipped_no_frontmatter"}


def _backfill_corpus(root, display, origin, team, summary):
    """Stamp every indexable entry under one corpus root; record each outcome.

    `display` is the org-root-relative prefix used in the report, so a summary
    names `teams/platform/learnings/x.md` rather than an absolute path.
    """
    if not os.path.isdir(root):
        return
    for rel in index.scan_entry_files(root):
        outcome = stamp_entry_org_fields(os.path.join(root, rel), origin, team)
        key = _OUTCOME_KEYS.get(outcome)
        if key:
            summary[key].append("%s/%s" % (display, rel))


def _backfill_org_frontmatter(org_root, summary):
    """Walk `teams/*` and `shared/`, stamping the derivable org fields.

    Scoped deliberately: an entry's team facet is a fact about where it lives, so
    only the two corpora whose path determines it are walked (see the module
    docstring). Entries at the org ROOT are left untouched.
    """
    teams_dir = os.path.join(org_root, orgkeys.ORG_TEAMS_DIR)
    if os.path.isdir(teams_dir):
        for team in sorted(d for d in os.listdir(teams_dir)
                           if os.path.isdir(os.path.join(teams_dir, d))):
            _backfill_corpus(os.path.join(teams_dir, team),
                             "%s/%s" % (orgkeys.ORG_TEAMS_DIR, team),
                             "team", team, summary)
    _backfill_corpus(os.path.join(org_root, orgkeys.ORG_SHARED_DIR),
                     orgkeys.ORG_SHARED_DIR, "shared", None, summary)


def _report(summary):
    """Announce ONE line on stderr iff the step actually changed something.

    The auto-apply trigger runs this unattended, on the first KB write after an
    upgrade, and it can rewrite entry frontmatter — a mutation nobody asked for
    in that moment deserves a line saying it happened. The negative half is the
    one that matters and is asserted directly: a flat install, and an already-
    migrated org tree, print NOTHING. A migration that narrates a no-op on every
    run is how an operator learns to stop reading the channel.

    The predicate is CHANGES ONLY (created / stamped). A cross-team conflict is a
    STANDING condition, not an event — it is still there on the next run, and on
    every run after that, because this step deliberately never rewrites it. Left
    in the predicate it would make the notice permanent, which is the same defect
    as narrating a no-op. It is reported inside the line when the step DID act,
    and owned as a finding by `org lint` O13.
    """
    if not (summary["created"] or summary["stamped"]):
        return
    sys.stderr.write(
        "migration-002: team mode — created %d dir(s), stamped %d entr(ies), "
        "%d cross-team conflict(s) left for `org lint`\n"
        % (len(summary["created"]), len(summary["stamped"]),
           len(summary["conflicts"])))


def run_team_mode_scaffold(kdir):
    """Migration 002's body: additively enable team mode. Idempotent.

    A true NO-OP on a flat install (no org root resolves) and on a mis-pointed one
    (an org root that does not exist, or that fails the org-project identity
    guard) — in both cases nothing is created and nothing is read, and `reason`
    says which. Otherwise: create `teams/`, the known team folders and `shared/`,
    then backfill the optional org frontmatter fields on the entries whose path
    determines them, leaving every entry in place.
    """
    summary = _empty_summary()
    org_root = _org_root_for(kdir)
    if not org_root:
        summary["reason"] = "flat install (no org root resolves)"
        return summary
    summary["org_root"] = org_root
    if not os.path.isdir(org_root):
        # A resolved-but-absent org root is a typo or a stale config.env row, not
        # an instruction to materialise a knowledge tree from an env var.
        summary["reason"] = "org root does not exist: %s" % org_root
        return summary
    identity_error = config.org_project_identity_error(org_root)
    if identity_error:
        summary["reason"] = identity_error
        return summary
    summary["engaged"] = True
    _ensure_org_dirs(org_root, kdir, summary)
    _backfill_org_frontmatter(org_root, summary)
    _report(summary)
    return summary


# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "SHARED_MAIN_STUB",
    "_org_root_for",
    "_scaffold_shared_dir",
    "_insert_org_lines",
    "_report",
    "stamp_entry_org_fields",
    "run_team_mode_scaffold",
]
