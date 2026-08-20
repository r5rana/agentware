"""agentware_lib.features.migratecmd — the KB schema interlock + `migrate` verb (layer: features).

Owns: the knowledge-base SCHEMA INTERLOCK in full — the registered migration table
(_KB_MIGRATIONS) and the package's schema version (KB_SCHEMA_VERSION = the max
registered version), the sidecar writer (_kb_schema_write; the READER
_kb_schema_read moved to core.kblayout at task 39 and is re-exported here), the
pending-set (_kb_pending_migrations), the audit journal (_KB_MIGRATION_JOURNAL_REL /
_kb_journal_migration), the idempotent runner (_kb_migrate_apply), the dispatch-time
guard (_kb_writer_funcs / _kb_autoapply_funcs / _kb_predispatch_gate), the three
registered migrations' lazy entry points (_migration_001_state_backfill /
_migration_002_team_mode_scaffold / _migration_003_template_refresh) and the
`migrate` command handler + registrar (cmd_migrate / register_migrate).
Does NOT own: any migration BODY — 001's is agentware_lib.features.planbackfill,
002's agentware_lib.ops.orgmigrate, 003's agentware_lib.ops.templaterefresh, each
resolved LAZILY at call time (task 90 moved 001 out, so the registry now owns
none of the three; a body is a one-off rewrite of operator data, the registry only
SCHEDULES it); the schema sidecar's FILENAME (SCHEMA_REL ->
agentware_lib.core.kblayout, with the rest of the KB layout); the plan path +
atomic write (-> agentware_lib.ops.planauthor.planedit); and any WRITER it guards
— every one of those lives in its own module and is named here only as a gate
TARGET.
Layer deps: features.migratecmd -> core (config, dates, kblayout) +
ops.planauthor.planedit (the atomic `.schema` write) — ALL DOWNWARD. The interlock
stays homed in features because the bodies it schedules reach across ops AND
features, and knowledge -> ops / knowledge -> features are the upward edges the
layer contract forbids.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

WHY THIS IS THE FIRST PORT (feature 260813 task 37)
---------------------------------------------------
`migrate` is not the smallest verb, it is the LOAD-BEARING one. The KB carries its own
schema version in `<kdir>/.schema`; the package carries KB_SCHEMA_VERSION. Two things
hang off that pair, and every modular KB writer that lands after this one inherits both:

  * the OLDER-CLI refusal — a package whose KB_SCHEMA_VERSION is BELOW the KB's
    `.schema` refuses to write at all, so a stale clone cannot silently corrupt a
    migrated knowledge base;
  * the on-first-use AUTO-APPLY — a KB behind the package has its pending ADDITIVE
    migrations applied (and journaled) BEFORE the write proceeds.

Both are enforced at DISPATCH, keyed on the handler function that was selected. That
key is per-tree: the monolith's gate holds monolith handlers, this one holds lib
handlers. What must NOT be per-tree is the RUNNER — two schema versions, two migration
tables or two `.schema` writers would be a split brain over one file on disk. So the
runner lives HERE, exactly once, and the monolith's gate reaches THIS module for the
version, the table and the apply call. That is the interlock: one schema authority, two
dispatch arms, for as long as the strangler window lasts.

THE WRITER SET IS DECLARED, NOT DISCOVERED
------------------------------------------
_KB_WRITER_TARGETS names each PORTED writer handler by (module, attr) and is resolved at
CALL time — so this module stays import-cheap on the hot path, and so a target that
stops resolving is a loud ImportError/AttributeError rather than a handler that quietly
falls out of the guarded set. It stayed EMPTY through task 37 because `migrate` is
deliberately not a guarded writer (see _kb_writer_funcs); task 38's `relate` is the
first ported verb that writes the KB, so it is the first row.
Each later port appends its own row; `tests/unit/features/test_kbschema_interlock.py` asserts the
correspondence in both directions — no ported writer verb missing a row, no row that
does not resolve — so the guard cannot be forgotten one port at a time.
"""

import importlib
import json
import os
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.core.kblayout as kblayout
import agentware_lib.ops.planauthor.planedit as planedit
# The schema-sidecar READER moved DOWN to core.kblayout at task 39, beside the
# SCHEMA_REL name it reads, and is re-exported here so `CLI._kb_schema_read` and
# every call site in this module resolve the SAME object. It moved because plan
# lint's R12/R14/R15/R16 severity ramps key on the KB schema generation, and
# `ops.planlint -> features.migratecmd` would be both an upward layer edge and a
# real cycle (this module imports ops.planauthor.planedit, which imports
# ops.planlint). The WRITER (_kb_schema_write) stays here with the runner.
from agentware_lib.core.kblayout import _kb_schema_read   # noqa: F401  (re-export)


# --- KB schema versioning + migration runner (feature 260710-plan-state-machine)
# The package carries KB_SCHEMA_VERSION (the max registered migration version);
# the KB records its own version in <kdir>/.schema (missing => 0, pre-versioning).
# An idempotent, non-destructive runner applies pending migrations: ADDITIVE ones
# auto-apply + journal; a RESTRUCTURING one is REFUSED with a suggested-workorder
# instruction (tier mapping per decide-self-correction-governance-tiers). An older
# CLL (KB_SCHEMA_VERSION < .schema) fails loud read-only on any KB write.
# Each migration: (version:int, tier:'additive'|'restructuring', name:str,
#                  fn:callable(kdir)) — fn MUST be idempotent + non-destructive.
def _migration_001_state_backfill(kdir):
    """Migration 001 (additive tier): dogfood the state machine by stamping a
    derived `> Status:` on every pre-existing plan. Idempotent + non-destructive;
    the body lives in agentware_lib.features.planbackfill (moved there at task 90
    — see that module's docstring for why a body is not the registry's).

    The import is LAZY, like 002's and 003's: this module is imported on EVERY
    CLI invocation (the dispatch gate lives here), and the body reaches ops.derive,
    both plan primitive modules and the live-session detector — a fan-out no
    `--help` should pay for.
    """
    import agentware_lib.features.planbackfill as planbackfill
    return planbackfill._run_state_backfill(kdir)



def _migration_002_team_mode_scaffold(kdir):
    """Migration 002 (additive tier): enable team mode ADDITIVELY — create the
    org `teams/<team>/` + `shared/` directories and backfill the optional org
    frontmatter fields, leaving every existing entry exactly where it is (the
    direct knowledge-dir pointer keeps winning, so a flat install is untouched
    and byte-identical). Idempotent + non-destructive; the body lives in
    agentware_lib.ops.orgmigrate.

    The import is LAZY on purpose. `agentware_lib.cli` imports this module on
    every CLI invocation (the dispatch gate lives here), while orgmigrate reaches
    ops.team -> ops.fleet and knowledge.index — a fan-out no `--help` should pay
    for. Same reasoning as _resolve_targets: resolve at CALL time, and let a row
    that stops resolving raise loudly instead of silently leaving the table.
    """
    import agentware_lib.ops.orgmigrate as orgmigrate
    return orgmigrate.run_team_mode_scaffold(kdir)


def _migration_003_template_refresh(kdir):
    """Migration 003 (additive tier): refresh the templates `init` INSTALLED into
    the knowledge dir, which `init` itself can never revisit — it copies each
    template only when the destination is absent, so a KB created before the
    corpus gained frontmatter keeps its legacy copies forever and `templates
    lint` reports a T1 finding with no path to clearing it. Idempotent +
    non-destructive: a file is rewritten ONLY when it is both frontmatter-less
    AND byte-identical to a generation this package shipped, so an operator-
    modified template is left alone. The body lives in
    agentware_lib.ops.templaterefresh.

    The import is LAZY for the same reason as _migration_002_team_mode_scaffold:
    `agentware_lib.cli` imports this module on every CLI invocation (the dispatch
    gate lives here), and no `--help` should pay for a migration body's fan-out.
    """
    import agentware_lib.ops.templaterefresh as templaterefresh
    return templaterefresh.run_template_refresh(kdir)


# Registered migrations, ascending version. 001 = plan state-backfill (additive);
# 002 = additive team-mode scaffold (additive); 003 = installed-template refresh
# (additive).
_KB_MIGRATIONS = ((1, "additive", "plan-state-backfill",
                   _migration_001_state_backfill),
                  (2, "additive", "team-mode-scaffold",
                   _migration_002_team_mode_scaffold),
                  (3, "additive", "template-refresh",
                   _migration_003_template_refresh))
KB_SCHEMA_VERSION = max((m[0] for m in _KB_MIGRATIONS), default=0)
_KB_MIGRATION_JOURNAL_REL = os.path.join("logs", "migrations.jsonl")


def _kb_schema_write(kdir, n):
    """Stamp <kdir>/.schema atomically."""
    planedit._atomic_write(os.path.join(kdir, kblayout.SCHEMA_REL),
                           "%d\n" % int(n))


def _kb_pending_migrations(kdir):
    """Registered migrations with version > the KB's current schema, in order."""
    cur = _kb_schema_read(kdir)
    return [m for m in _KB_MIGRATIONS if m[0] > cur]


def _kb_journal_migration(kdir, record):
    """Append one JSON line to <kdir>/logs/migrations.jsonl (best-effort audit)."""
    try:
        path = os.path.join(kdir, _KB_MIGRATION_JOURNAL_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        sys.stderr.write("agentware: migration journal write failed: %s\n" % exc)


def _kb_migrate_apply(kdir):
    """Apply pending ADDITIVE migrations in order (journal each), then stamp
    .schema. A RESTRUCTURING migration STOPS the run (refusal) without applying
    it. Returns (result, error): error is set only for the older-CLI guard.
    result = {applied:[…], schema:int, pending:int, refused:{…}|None}."""
    cur = _kb_schema_read(kdir)
    if cur > KB_SCHEMA_VERSION:
        return None, ("older CLI (schema %d) on a newer KB (schema %d) — refusing "
                      "any write; update the agentware package first"
                      % (KB_SCHEMA_VERSION, cur))
    pending = _kb_pending_migrations(kdir)
    applied, reached, refused = [], cur, None
    for (ver, tier, name, fn) in pending:
        if tier != "additive":
            refused = {"version": ver, "name": name, "tier": tier}
            break
        fn(kdir)
        rec = {"ts": dates.utc_now_iso(), "version": ver, "name": name,
               "tier": tier}
        _kb_journal_migration(kdir, rec)
        applied.append(rec)
        reached = ver
    target = reached if refused else KB_SCHEMA_VERSION
    _kb_schema_write(kdir, target)
    return {"applied": applied, "schema": target,
            "pending": len(pending) - len(applied), "refused": refused}, None


# Every KB-WRITING verb (and subverb) the monolith registered in its own
# `_kb_writer_funcs`, by the verb id the strangler routes on. This is the CONTRACT
# half of the interlock: it says which verbs MUST be guarded once they are ported,
# independently of whether anyone remembered to add the row below. The suite reads
# both and fails on the difference — see tests/unit/features/test_kbschema_interlock.py.
_KB_WRITER_VERBS = frozenset((
    "index", "learn", "decide", "ingest", "relate", "prefer", "plan",
    "features", "skill", "dream",
))

# The PORTED writer handlers, as (module, attr) resolved at call time. Every port
# appends its own row here in the same commit that appends its verb to
# strangler.PORTED_VERBS; `migrate` itself is deliberately not guarded (see
# _kb_writer_funcs), so the set stayed empty until the first ported WRITER.
_KB_WRITER_TARGETS = (
    # task 38 — `relate` mutates the subject `.md` AND index.json via
    # _declare_relation -> save_index, so an older CLI must refuse it on a
    # newer/migrated KB. Not dual-mode (no read-only path), so it stays in the
    # auto-apply subset too — no _KB_AUTOAPPLY_EXCLUDED_TARGETS row.
    ("agentware_lib.knowledge.relatecmd", "cmd_relate"),
    # task 39 — the SIX `plan` writers, mirroring the monolith's own set exactly
    # ("plan-state writers (previously OMITTED — the schema-guard hole)"). Each
    # writes a plan.md into the configured KB, so an older CLI must refuse it on a
    # newer/migrated KB. The three READ-ONLY plan leaves (lint, verify-premises,
    # target-dirs) are deliberately absent: a guard over a reader would refuse work
    # that mutates nothing. `plan` is in _KB_WRITER_VERBS, so the interlock's
    # registry/allowlist correspondence check REQUIRES these rows in the same
    # commit that appends `plan` to strangler.PORTED_VERBS — without them the
    # handlers dispatch straight past the schema guard.
    ("agentware_lib.ops.planauthor.plannew", "cmd_plan_new"),
    ("agentware_lib.ops.planauthor.planedit", "cmd_plan_add_task"),
    ("agentware_lib.ops.planauthor.planedit", "cmd_plan_set_status"),
    ("agentware_lib.ops.planauthor.planstate", "cmd_plan_set_state"),
    ("agentware_lib.ops.planauthor.planstate", "cmd_plan_claim"),
    ("agentware_lib.features.planbackfill", "cmd_plan_state_backfill"),
    # task 45 — the `prefer` writers. `prefer` is in _KB_WRITER_VERBS, so the
    # interlock's correspondence check requires a row per DISPATCHED handler.
    # cmd_prefer is registered even though it is largely sugar over cmd_learn:
    # the predispatch gate keys on the DISPATCHED func, so a cmd_prefer that
    # delegates in-process would otherwise be UNGUARDED on a newer/migrated KB.
    ("agentware_lib.ops.prefercmd", "cmd_prefer"),
    ("agentware_lib.ops.prefercmd", "cmd_prefer_mine"),
    ("agentware_lib.ops.preferdispose", "cmd_prefer_approve"),
    ("agentware_lib.ops.preferdispose", "cmd_prefer_reject"),
    # task 54 — each shared-verb promotion brings its writer row IN THE SAME
    # COMMIT. Without the row the handler dispatches straight PAST the older-CLI
    # schema refusal: the interlock failing OPEN, silently, for exactly the verbs
    # that mutate the knowledge base. The correspondence check refuses a row for
    # an unrouted verb and refuses a routed writer with no row, so the two halves
    # cannot drift apart.
    ("agentware_lib.knowledge.curation.curationfeatures", "cmd_features"),
    ("agentware_lib.features.curationcmd", "cmd_decide"),
    ("agentware_lib.features.curationcmd", "cmd_learn"),
    ("agentware_lib.knowledge.curation.curationingest", "cmd_ingest"),
    ("agentware_lib.features.dream", "cmd_dream"),
    # task 54 — the `index` family. Five of the six subverbs MUTATE (add /
    # remove / rebuild / sync / migrate); `validate` is read-only and is
    # deliberately absent, since guarding a validator would make a health
    # check refuse to run on exactly the KB whose schema it is meant to
    # report on.
    ("agentware_lib.features.indexcmd", "cmd_index_add"),
    ("agentware_lib.features.indexcmd", "cmd_index_remove"),
    ("agentware_lib.features.indexcmd", "cmd_index_rebuild"),
    ("agentware_lib.features.indexcmd", "cmd_index_sync"),
    ("agentware_lib.features.indexcmd", "cmd_index_migrate"),
    # task 54 — `skill add` / `skill remove` register and unregister a `skills`
    # index entry. The other skill subverbs are read-only reports or operate on
    # the proposals tier, and `skill propose` is deliberately absent: it is
    # dispatched by its OWN func and is read-only w.r.t. the index (its writes
    # are work/auto drafts), so it stays exempt-by-omission.
    ("agentware_lib.features.skills", "cmd_skill_add"),
    ("agentware_lib.features.skills", "cmd_skill_remove"),
)

# Ported handlers that carry a READ-ONLY mode and therefore must never trip the
# auto-apply (a read that writes `.schema` is the failure this set prevents):
# the monolith excludes cmd_plan_state_backfill (it IS migration 001's body),
# cmd_dream (`--dry-run`) and cmd_prefer (digest/list/scan-queue run at
# session-start). Rows arrive with those ports.
_KB_AUTOAPPLY_EXCLUDED_TARGETS = (
    # task 39 — `plan state-backfill` IS migration 001's body. If the gate
    # auto-applied migrations before dispatching it, the migration would run
    # itself: the runner would apply 001 and then hand control to the handler that
    # performs 001's work on an already-migrated KB. Version-REFUSAL still applies
    # (it stays in _KB_WRITER_TARGETS above); only the auto-apply is withheld.
    ("agentware_lib.features.planbackfill", "cmd_plan_state_backfill"),
    # task 45 — `cmd_prefer` is DUAL-MODE: digest / list / scan-queue are
    # read-only and run at SESSION START, so auto-applying migrations from them
    # would let a read silently advance the KB `.schema` on every session.
    # Version-REFUSAL still applies (it stays in _KB_WRITER_TARGETS above);
    # only the auto-apply is withheld.
    ("agentware_lib.ops.prefercmd", "cmd_prefer"),
    # task 54 — `cmd_dream` is DUAL-MODE: `--dry-run` runs the whole cycle and
    # writes nothing, and the cadence tick invokes it unattended, so
    # auto-applying migrations from it would let a scheduled READ advance the
    # KB `.schema` with no operator present. Version-REFUSAL still applies (it
    # stays in _KB_WRITER_TARGETS above); only the auto-apply is withheld.
    ("agentware_lib.features.dream", "cmd_dream"),
)


def _resolve_targets(targets):
    """(module, attr) rows -> the live function objects, resolved at CALL time.

    Deliberately NOT tolerant: a row that no longer resolves raises here rather
    than dropping out of the guarded set, because a writer silently leaving the
    set is precisely the failure the gate exists to prevent, and it would leave
    no trace anywhere else.
    """
    out = set()
    for module, attr in targets:
        out.add(getattr(importlib.import_module(module), attr))
    return out


def _kb_writer_funcs():
    """Version-refusal SUPERSET: EVERY ported KB-mutating command func. On a NEWER
    KB (`.schema` > this CLI's KB_SCHEMA_VERSION) the older-CLI guard refuses ALL
    of these loudly, so an out-of-date package can never silently corrupt a
    migrated KB. Resolved at call time (see _resolve_targets).

    Deliberately EXCLUDED (NOT registered):
      - cmd_migrate — one func serves `--list` (read-only) AND `--apply`;
        registering it would make read-only `--list` trip the auto-apply and
        write `.schema` during a READ. It self-guards `cur > target` and owns its
        own schema writes.
      - cmd_attach — targets an ARBITRARY candidate path (not the configured KB)
        and has its own conformance/migrate flow.
    Read-only verbs (recall, query, config, metrics, worklog scan, plan lint,
    features-less lookups) are exempt by OMISSION — correct by construction."""
    return _resolve_targets(_KB_WRITER_TARGETS)


def _kb_autoapply_funcs():
    """Auto-apply SUBSET = the writer superset MINUS the DUAL-MODE / migration-
    RUNNER verbs whose read-only path must never trip a `.schema` write. All still
    carry version-refusal via superset membership."""
    return _kb_writer_funcs() - _resolve_targets(_KB_AUTOAPPLY_EXCLUDED_TARGETS)


def _kb_predispatch_gate(args):
    """On a KB-WRITING command dispatch: (1) older-CLI guard — refuse the write
    loudly on a newer KB, for EVERY writer (the superset); (2) cheap pending-check
    + auto-apply of ADDITIVE migrations FIRST (the on-first-CLI-use trigger), for
    every writer EXCEPT the migration-runner verbs (the auto-apply subset). No-op
    for readers and for an unconfigured/uninitialized KB. Returns an exit code to
    short-circuit, else None."""
    func = getattr(args, "func", None)
    if func not in _kb_writer_funcs():
        return None
    kdir = config.resolve_knowledge_dir()
    if not kdir or not os.path.isdir(kdir):
        return None
    if not os.path.isfile(os.path.join(kdir, kblayout.SENTINEL_REL)):
        return None   # uninitialized: init/onboarding path — never migrate
    cur = _kb_schema_read(kdir)
    if cur > KB_SCHEMA_VERSION:
        sys.stderr.write(
            "agentware: refusing to write — this CLI is schema %d but the KB is "
            "schema %d (older CLI on a newer KB). Update the agentware package "
            "first.\n" % (KB_SCHEMA_VERSION, cur))
        return 1
    if cur < KB_SCHEMA_VERSION and func in _kb_autoapply_funcs():
        result, err = _kb_migrate_apply(kdir)
        if err:
            sys.stderr.write("agentware: %s\n" % err)
            return 1
        if result.get("refused"):
            r = result["refused"]
            sys.stderr.write(
                "agentware: a restructuring migration (%d %s) is pending — run "
                "`scripts/agentware migrate --apply` (it will emit a migration "
                "workorder) before writing.\n" % (r["version"], r["name"]))
            return 1
    return None


def _cmd_migrate_apply(kdir, as_json):
    """`migrate --apply`: run the runner and render its result. Returns an exit
    code. Split out of cmd_migrate so each half stays inside the per-function
    ceiling; the OUTPUT text is pinned by the suite and is unchanged."""
    result, err = _kb_migrate_apply(kdir)
    if err:
        if as_json:
            json.dump({"ok": False, "error": err}, sys.stdout,
                      ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stderr.write("migrate: %s\n" % err)
        return 1
    applied = result.get("applied", [])
    refused = result.get("refused")
    if as_json:
        json.dump({"ok": not refused, "applied": applied,
                   "schema": result["schema"], "pending": result["pending"],
                   "refused": refused}, sys.stdout, ensure_ascii=False,
                  indent=2)
        sys.stdout.write("\n")
    if refused:
        print("migrate: refusing to auto-apply RESTRUCTURING migration %d "
              "(%s) — create a migration workorder: `scripts/agentware plan "
              "new --auto <YYMMDD>-migrate-%d --generated-by migrate --trigger "
              "schema-%d --title 'apply migration %d'`"
              % (refused["version"], refused["name"], refused["version"],
                 refused["version"], refused["version"]))
        return 1
    if not applied:
        print("migrate: up to date (schema %d, 0 pending)" % result["schema"])
    else:
        print("migrate: applied %d migration(s) -> schema %d"
              % (len(applied), result["schema"]))
    return 0


def cmd_migrate(args):
    """`migrate --list` (read-only) / `migrate --apply` (apply + stamp .schema).

    Pinned output: `--list` prints `migrate: schema <cur> -> <target>, <k> pending`
    or `migrate: up to date (schema <N>, 0 pending)`; `--apply` with nothing
    pending prints exactly `migrate: up to date (schema <N>, 0 pending)`.
    """
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    as_json = getattr(args, "format", "text") == "json"
    cur = _kb_schema_read(kdir)
    target = KB_SCHEMA_VERSION

    if getattr(args, "apply", False):
        return _cmd_migrate_apply(kdir, as_json)

    # --list (read-only; NEVER creates .schema)
    pending = _kb_pending_migrations(kdir)
    if as_json:
        json.dump({"ok": True, "schema": cur, "target": target,
                   "pending": [{"version": m[0], "tier": m[1], "name": m[2]}
                               for m in pending]},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    if cur > target:
        # Older CLI on a newer KB: `_kb_pending_migrations` returns [] here, so the
        # old code misreported this as "up to date". Report the mismatch honestly;
        # still read-only (never stamps .schema).
        print("migrate: OLDER CLI (schema %d) on a NEWER KB (schema %d) — update "
              "the agentware package to migrate; writes are refused until then"
              % (target, cur))
    elif not pending:
        print("migrate: up to date (schema %d, 0 pending)" % cur)
    else:
        print("migrate: schema %d -> %d, %d pending" % (cur, target, len(pending)))
    return 0


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("migrate", 1400, "register_migrate"),
)


def register_migrate(sub, fmt_parent):
    # migrate ----------------------------------------------------------------
    p_mig = sub.add_parser(
        "migrate", parents=[fmt_parent],
        help="KB schema migration runner: --list (read-only) / --apply (idempotent)")
    mig_grp = p_mig.add_mutually_exclusive_group()
    mig_grp.add_argument("--list", action="store_true",
                         help="report pending migrations, read-only (the default)")
    mig_grp.add_argument("--apply", action="store_true",
                         help="apply pending additive migrations and stamp .schema "
                              "(idempotent; refuses restructuring migrations)")
    p_mig.set_defaults(func=cmd_migrate)


# Explicit facade export list (public AND underscore-private): the entrypoint binds
# exactly these names, so the monolith's own dispatch gate and every
# SourceFileLoader-based test reach ONE runner — the interlock's whole point.
_EXPORTS = [
    "KB_SCHEMA_VERSION",
    "_KB_MIGRATIONS",
    "_KB_MIGRATION_JOURNAL_REL",
    "_kb_schema_read",
    "_kb_schema_write",
    "_kb_pending_migrations",
    "_kb_journal_migration",
    "_kb_migrate_apply",
    "_migration_001_state_backfill",
    "_migration_002_team_mode_scaffold",
    "_migration_003_template_refresh",
    "cmd_migrate",
]

__all__ = list(_EXPORTS) + ["register_migrate", "_kb_writer_funcs",
                            "_kb_autoapply_funcs", "_kb_predispatch_gate"]
