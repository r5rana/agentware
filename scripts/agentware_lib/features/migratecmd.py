"""agentware_lib.features.migratecmd — the KB schema interlock + `migrate` verb (layer: features).

Owns: the knowledge-base SCHEMA INTERLOCK in full — the registered migration table
(_KB_MIGRATIONS) and the package's schema version (KB_SCHEMA_VERSION = the max
registered version), the sidecar reader/writer (_kb_schema_read/_kb_schema_write), the
pending-set (_kb_pending_migrations), the audit journal (_KB_MIGRATION_JOURNAL_REL /
_kb_journal_migration), the idempotent runner (_kb_migrate_apply), the dispatch-time
guard (_kb_writer_funcs / _kb_autoapply_funcs / _kb_predispatch_gate), migration 001's
body (_run_state_backfill / _migration_001_state_backfill), and the `migrate` command
handler + registrar (cmd_migrate / register_migrate).
Does NOT own: the schema sidecar's FILENAME (SCHEMA_REL -> agentware_lib.core.kblayout,
with the rest of the KB layout); the plan `> Status:` text primitives migration 001
rewrites (-> agentware_lib.ops.planauthor.planstate); the plan path + atomic write
(-> agentware_lib.ops.planauthor.planedit); the loop-outcome derivation
(-> agentware_lib.ops.derive); the live-session detector (->
agentware_lib.features.dreamgate); and any WRITER it guards — every one of those lives
in its own module and is named here only as a gate TARGET.
Layer deps: features.migratecmd -> core (config, dates, kblayout) + ops (derive,
planauthor.planedit, planauthor.planstate) + features (dreamgate) — ALL DOWNWARD or
same-layer. That cross-layer reach is exactly why the interlock is homed in features
rather than in knowledge: migration 001's body needs the ops plan primitives AND the
features live-session detector, and knowledge -> ops / knowledge -> features are the
upward edges the layer contract forbids.
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
falls out of the guarded set. It is EMPTY at this commit because `migrate` is the only
ported verb and `migrate` is deliberately not a guarded writer (see _kb_writer_funcs).
Each later port appends its own row; `tests/test_kbschema_interlock.py` asserts the
correspondence in both directions — no ported writer verb missing a row, no row that
does not resolve — so the guard cannot be forgotten one port at a time.
"""

import importlib
import json
import os
import re
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.core.kblayout as kblayout
import agentware_lib.features.dreamgate as dreamgate
import agentware_lib.ops.derive as derive
import agentware_lib.ops.planauthor.planedit as planedit
import agentware_lib.ops.planauthor.planstate as planstate


def _kb_schema_read(kdir):
    """Read the KB schema version from <kdir>/.schema; missing/unreadable => 0
    (a pre-versioning KB — the bootstrap contract). The full migrate runner
    (writes, journaling, guards) lives in cmd_migrate."""
    try:
        with open(os.path.join(kdir, kblayout.SCHEMA_REL), "r",
                  encoding="utf-8") as f:
            return int((f.read() or "0").strip() or "0")
    except (OSError, ValueError):
        return 0


# --- KB schema versioning + migration runner (feature 260710-plan-state-machine)
# The package carries KB_SCHEMA_VERSION (the max registered migration version);
# the KB records its own version in <kdir>/.schema (missing => 0, pre-versioning).
# An idempotent, non-destructive runner applies pending migrations: ADDITIVE ones
# auto-apply + journal; a RESTRUCTURING one is REFUSED with a suggested-workorder
# instruction (tier mapping per decide-self-correction-governance-tiers). An older
# CLL (KB_SCHEMA_VERSION < .schema) fails loud read-only on any KB write.
# Each migration: (version:int, tier:'additive'|'restructuring', name:str,
#                  fn:callable(kdir)) — fn MUST be idempotent + non-destructive.
def _backfill_plan_features(kdir):
    """Every work feature under <kdir>/work with a plan to consider, `auto/` ones
    included, in sorted order. Returns None when work/ cannot be listed at all —
    the caller reports an empty summary rather than crashing a migration."""
    work = os.path.join(kdir, "work")
    feats = []
    try:
        for name in sorted(os.listdir(work)):
            fdir = os.path.join(work, name)
            if not os.path.isdir(fdir):
                continue
            if name == "auto":
                try:
                    for sub in sorted(os.listdir(fdir)):
                        if os.path.isdir(os.path.join(fdir, sub)):
                            feats.append("auto/" + sub)
                except OSError:
                    pass
            else:
                feats.append(name)
    except OSError:
        return None
    return feats


def _backfill_read_plan(path, feat):
    """(text, None) for a plan-shaped file, else (None, reason). A plan needs a
    title or a `> Feature:`/`> Created:` header anchor; anything else is one of
    the legacy non-plan variants and is SKIPPED (logged), never a crash."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        sys.stderr.write("migration-001: skipping unreadable plan %s\n" % feat)
        return None, "unreadable"
    if not (re.search(r"(?m)^#\s", text)
            or re.search(r"(?m)^>\s*(Feature|Created):", text)):
        sys.stderr.write("migration-001: skipping unparseable plan %s\n" % feat)
        return None, "unparseable"
    return text, None


def _backfill_stamp_plan(kdir, feat, path, text, now_iso):
    """Stamp a derived `> Status:` + one status-log line onto ONE legacy plan.
    Returns the state written. Non-destructive: only ADDS the two lines."""
    outcome = derive.derive_outcome(kdir, feat)
    state = "done" if (outcome and outcome.get("outcome") == "completed") \
        else "draft"
    lines = text.splitlines()
    lines = planstate._set_header_line(lines, "Status", state)
    lines = planstate._append_status_log(
        lines, "- %s (none) -> %s by migration-001 reason: backfilled by "
               "migration-001" % (now_iso, state))
    out = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    planedit._atomic_write(path, out)
    return state


def _run_state_backfill(kdir, now_iso=None):
    """Stamp `> Status:` on every legacy work/*/plan.md AND work/auto/*/plan.md
    that lacks one, deriving `done` (metrics terminal `completed` > `.loop/.done` >
    all-tasks-✅, via derive_outcome) else `draft`. NEVER overwrites an existing
    status; skips the live loop feature (is_session_active) and unparseable plans
    (logged); appends a `backfilled by migration-001` status-log line per stamp.
    Idempotent + non-destructive. Returns a summary dict."""
    now_iso = now_iso or dates.utc_now_iso()
    live = dreamgate.is_session_active(kdir)
    stamped, skipped_existing, skipped_live, skipped_unparseable = [], [], [], []
    feats = _backfill_plan_features(kdir)
    if feats is None:
        return {"stamped": [], "skipped_existing": [], "skipped_live": [],
                "skipped_unparseable": []}
    for feat in feats:
        path = planedit._plan_path(kdir, feat)
        if not os.path.isfile(path):
            continue
        text, _reason = _backfill_read_plan(path, feat)
        if text is None:
            skipped_unparseable.append(feat)
            continue
        _state, cnt = planstate._read_plan_status(text)
        if cnt >= 1:
            skipped_existing.append(feat)   # NEVER overwrite an existing status
            continue
        if feat == live:
            skipped_live.append(feat)       # the loop stamps it done at run end
            continue
        state = _backfill_stamp_plan(kdir, feat, path, text, now_iso)
        stamped.append({"feature": feat, "state": state})
    return {"stamped": stamped, "skipped_existing": skipped_existing,
            "skipped_live": skipped_live,
            "skipped_unparseable": skipped_unparseable}


def _migration_001_state_backfill(kdir):
    """Migration 001 (additive tier): dogfood the state machine by stamping a
    derived `> Status:` on every pre-existing plan. Idempotent + non-destructive."""
    return _run_state_backfill(kdir)


# Registered migrations, ascending version. 001 = plan state-backfill (additive).
_KB_MIGRATIONS = ((1, "additive", "plan-state-backfill",
                   _migration_001_state_backfill),)
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
# both and fails on the difference — see tests/test_kbschema_interlock.py.
_KB_WRITER_VERBS = frozenset((
    "index", "learn", "decide", "ingest", "relate", "prefer", "plan",
    "features", "skill", "dream",
))

# The PORTED writer handlers, as (module, attr) resolved at call time. EMPTY at
# task 37: `migrate` is the only ported verb and it is deliberately not guarded
# (see _kb_writer_funcs). Every later port appends its own row here in the same
# commit that appends its verb to strangler.PORTED_VERBS.
_KB_WRITER_TARGETS = ()

# Ported handlers that carry a READ-ONLY mode and therefore must never trip the
# auto-apply (a read that writes `.schema` is the failure this set prevents):
# the monolith excludes cmd_plan_state_backfill (it IS migration 001's body),
# cmd_dream (`--dry-run`) and cmd_prefer (digest/list/scan-queue run at
# session-start). Rows arrive with those ports.
_KB_AUTOAPPLY_EXCLUDED_TARGETS = ()


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
    "_run_state_backfill",
    "_migration_001_state_backfill",
    "cmd_migrate",
]

__all__ = list(_EXPORTS) + ["register_migrate", "_kb_writer_funcs",
                            "_kb_autoapply_funcs", "_kb_predispatch_gate"]
