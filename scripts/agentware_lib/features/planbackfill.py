"""agentware_lib.features.planbackfill — migration 001's body: plan state-backfill (layer: features).

Owns: migration 001's BODY in full — the work-feature walk
(_backfill_plan_features), the plan-shaped reader (_backfill_read_plan), the
already-stamped guard that asks with the WRITER's own predicate
(_backfill_existing_status_lines), the per-plan stamp (_backfill_stamp_plan), the
idempotent runner (_run_state_backfill)
and the `plan state-backfill` handler (cmd_plan_state_backfill) that makes the
same logic runnable directly.
Does NOT own: the migration REGISTRY, the schema sidecar, the dispatch gate or the
runner that calls this (agentware_lib.features.migratecmd — one schema authority,
per its interlock docstring); the plan `> Status:` text primitives this rewrites
(agentware_lib.ops.planauthor.planstate); the plan path + atomic write
(agentware_lib.ops.planauthor.planedit); the loop-outcome derivation
(agentware_lib.ops.derive); the live-session detector
(agentware_lib.features.dreamgate); the `plan` registrar, which resolves this
handler by (module, attr) at REGISTRATION time
(agentware_lib.ops.planauthor.planedit.register_plan).
Layer deps: features.planbackfill -> core (config, dates) + ops (derive,
planauthor.planedit, planauthor.planstate) + the SAME-LAYER sibling
features.dreamgate — ALL DOWNWARD or same-layer. That reach is exactly why this
body is homed in features and not lower: it needs the ops plan primitives AND the
features live-session detector, and knowledge -> ops / knowledge -> features are
the upward edges the layer contract forbids.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

WHY THE BODY LIVES OUTSIDE THE REGISTRY (feature 260813 task 90)
----------------------------------------------------------------
`migratecmd` owns the schema INTERLOCK: one migration table, one `.schema`
writer, one runner, one dispatch gate. A migration BODY is a different
obligation — it is a one-off rewrite of operator data that the interlock merely
schedules — and the two were separated for migrations 002 (ops.orgmigrate) and
003 (ops.templaterefresh) from the day each landed. 001 was the last body still
living inside the registry, and registering 003 pushed that file past its
600-line cap. Per R-MOD-04 the cap does not move and no debt row was declared:
the file split where its own docstring already said two obligations met.

Nothing imports this at module level — `migratecmd._migration_001_state_backfill`
resolves it LAZILY at call time (like 002 and 003), so the CLI's hot import path
never pays for `ops.derive` + the two plan primitive modules + the live-session
detector on a `--help`.
"""

import json
import os
import re
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.features.dreamgate as dreamgate
import agentware_lib.ops.derive as derive
import agentware_lib.ops.planauthor.planedit as planedit
import agentware_lib.ops.planauthor.planstate as planstate


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


def _backfill_existing_status_lines(text):
    """How many `> Status:` header lines this plan ALREADY carries — the guard
    `_run_state_backfill` uses to honour "NEVER overwrites an existing status".

    Asked as the UNION of the two predicates that describe the same line, because
    neither alone is safe (feature 260813 task 97):

      * `planstate._header_field_rx("Status")` is the WRITER's own removal
        pattern. It is what `_set_header_line` DELETES with, so anything it sees
        is a line this migration would destroy — including the annotated forms
        the strict reader is blind to (`> Status: draft (not ready yet)`,
        `> Status: **ready** (lints clean)`), which is the data loss this task
        closes: found live by the task-64 rehearsal on two real hand-annotated
        plans, where a human `**ready**` was replaced by a derived `done`.
      * `planstate._read_plan_status` is the strict end-anchored READER. It is
        kept in the union for the opposite edge: `> Status:draft` (no space after
        the colon) parses THERE but is invisible to the writer's pattern, so
        guarding on the writer alone would stamp a second header onto a plan that
        already has one — a duplicate, which lint reports as an error.

    A guard that over-triggers costs a skipped file; one that under-triggers costs
    the file. This one over-triggers by construction.
    """
    rx = planstate._header_field_rx("Status")
    loose = sum(1 for ln in (text or "").splitlines() if rx.match(ln))
    strict = planstate._read_plan_status(text)[1]
    return max(loose, strict)


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
    status — asked with the WRITER's own predicate via
    `_backfill_existing_status_lines`, so an ANNOTATED status line the strict
    reader cannot parse still counts as existing rather than being deleted (task
    97); skips the live loop feature (is_session_active) and unparseable plans
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
        if _backfill_existing_status_lines(text) >= 1:
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


def cmd_plan_state_backfill(args):
    """`plan state-backfill` — migration 001's logic, also runnable directly."""
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    summary = _run_state_backfill(kdir)
    if getattr(args, "format", "text") == "json":
        out = {"ok": True}
        out.update(summary)
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("PLAN state-backfill: stamped %d, skipped %d existing + %d live + "
              "%d unparseable"
              % (len(summary["stamped"]), len(summary["skipped_existing"]),
                 len(summary["skipped_live"]), len(summary["skipped_unparseable"])))
    return 0


# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these names, so every SourceFileLoader-based test
# reaches the SAME runner the registry schedules.
_EXPORTS = [
    "_backfill_plan_features",
    "_backfill_read_plan",
    "_backfill_existing_status_lines",
    "_backfill_stamp_plan",
    "_run_state_backfill",
    "cmd_plan_state_backfill",
]
