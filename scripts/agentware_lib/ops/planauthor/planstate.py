"""agentware_lib.ops.planauthor.planstate — the plan state machine (layer: ops).

Owns: the toolkit-owned plan LIFECYCLE end to end — the transition MATRIX and the
constants that describe it (_PLAN_TERMINAL_STATES / _PLAN_STATE_TRANSITIONS /
_PLAN_STATE_RANK / STALLED_CLAIM_HOURS / _PLAN_CLAIMED_BY_RE /
_PLAN_SUPERSEDED_BY_RE), the claim-stamp primitives that read and write it
(_short_host / _claim_age_hours / _parse_claim), the tolerant plan reader the state
edit uses instead of the lint gate (_load_plan_tolerant), the deterministic merge
resolver for a conflicted `> Status:` (_merge_plan_status), the two PURE plan-text
WRITER primitives (_set_header_line / _append_status_log + _PLAN_STATUS_LOG_HEADING),
and the two handlers that are the SOLE writers of the header — cmd_plan_set_state
and its loop-facing orchestrator cmd_plan_claim. The READER half (_read_plan_status /
_PLAN_DEFAULT_STATE / _PLAN_STATUS_HEADER_RE) moved DOWN to ops.plangraph at task
39 to break a real `planlint -> planstate` cycle and is re-exported from here
unchanged; see the import block below.
Does NOT own: the g2 text neutralization the --reason/--superseded-by values pass
through (_sanitize_provenance and its two siblings) -> agentware_lib.ops.planauthor
.plansanitize; the plan GENERATION half (_plan_skeleton/_plan_path/_atomic_write/
cmd_plan_new/…) -> agentware_lib.ops.planauthor.planedit; the ENFORCEMENT half ->
agentware_lib.ops.planlint; the `recall --scope work` plan-status ranking multipliers
(_WORK_STATUS_MULT / _work_status_multiplier), which are a RETRIEVAL concern and
cannot live in ops because `retrieval -> ops` is an upward edge — they land with task
55; the migration that BACKFILLS a status onto every legacy plan, and the
`plan state-backfill` handler that re-exposes it ->
agentware_lib.features.migratecmd (the payload needs the live-session detector, which
is a features-layer symbol, so neither it nor its one-line CLI adapter can come down
here — see that module).
Layer deps: ops.planauthor.planstate -> ops.plangraph (the re-exported `> Status:`
parser), ops.planauthor.plansanitize (the g2 provenance sanitizer),
ops.planauthor.planedit (_plan_path / _atomic_write — the one plan.md writer),
ops.planmarkers (the open-marker counter the running->done gate reads), ops.planlint
(the draft->ready readiness gate) and core (config/dates/routing) — all DOWNWARD or
same-layer; nothing above ops is imported.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

WHY THESE THREE LAND BEFORE THE `plan` PORT (feature 260813 task 37)
--------------------------------------------------------------------
Migration 001 — the KB schema migration that stamps a derived `> Status:` on every
pre-versioning plan — is the FIRST thing the modular tree must be able to run, because
the schema interlock is what every subsequent modular KB writer is gated on. Its body
reads and rewrites the plan header, so these three primitives have to exist under the
lib before `plan` itself is ported. They are moved here, to their DESTINATION module,
rather than parked in the migration: task 39 lands the rest of the state machine on top
of them instead of moving them a second time.

The move is a PURE move — the bodies are byte-identical to the ones the monolith held,
which is the discipline the body differ's length floor demands (see
`learn-under-a-body-length-differ-a-move-must-be-a-pure-move`: tidying in transit reads
as a body that LOST something). The entrypoint keeps the names resolving by aliasing
this module's functions, so every `CLI._read_plan_status` call site — 26 of them across
the suite — is unchanged.

THE HANDLERS ARE DECOMPOSED, AND WHY THAT IS NOT A REWRITE
----------------------------------------------------------
`cmd_plan_set_state` arrives from the monolith at 126 lines and ~35 decision points
— over BOTH per-function ceilings (120 lines / 12 branches) ON ARRIVAL. The ceiling
debt file is shrink-only and accepts no new row, so it is decomposed here into the
four questions it actually asks, each its own named unit: is the EDGE legal
(_setstate_resolve_edge), does the edge's argv contract hold (_setstate_argv_error),
do the three NON-ARGV gates pass (_setstate_gate_error + _setstate_reclaim_error),
and what does the resulting document look like (_setstate_apply). This is a
DECOMPOSITION, not an edit: every emitted string, every check and their ORDER are
byte-preserved, including the deliberate ordering that resolves the handle/host/now
identity only AFTER the argv check (so a refused transition never reads the operator
handle). `cmd_plan_set_state` is a LOCAL-ONLY definition with no donor counterpart,
so it carries no row in the body differ's 243-row baseline and no length floor is
crossed by splitting it.

Deterministic apart from its two declared inputs: the wall clock (`--now`
overridable, metadata only — never ranking, INV-1) and the plan.md it reads and
rewrites through the one atomic writer.
"""

import argparse
import json
import os
import re
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.core.routing as routing
import agentware_lib.ops.planauthor.planedit as planedit
import agentware_lib.ops.planauthor.plansanitize as plansanitize
import agentware_lib.ops.planlint as _planlint
import agentware_lib.ops.planmarkers as planmarkers

# The `> Status:` PARSER moved DOWN to ops.plangraph at task 39 and is re-exported
# here. Reason (the same one that moved the R11 target-packages parser): R13 makes
# `ops.planlint` need `_read_plan_status`, and `ops.planauthor.__init__` imports
# `ops.planlint` — so a `planlint -> planstate` edge is a genuine import cycle,
# reproduced in a scratch copy as `ImportError: cannot import name
# '_PLAN_TASK_LINE_RE' from partially initialized module 'agentware_lib.ops.planlint'`.
# The parser is a leaf and belongs beside its four sibling declarative blocks; what
# stays here is the WRITER half. Re-exporting rather than re-defining keeps
# `is`-identity, so all 26 resident `_read_plan_status` call sites, the migration-001
# backfill and every `CLI.<name>` oracle resolve exactly the object they always did.
from agentware_lib.ops.plangraph import (   # noqa: F401  (re-export)
    _PLAN_DEFAULT_STATE, _PLAN_STATES, _PLAN_STATUS_HEADER_RE, _read_plan_status)

_PLAN_STATUS_LOG_HEADING = "## Status log"


def _header_field_rx(field):
    """The writer's OWN predicate for "this line IS the `> Field:` header".

    Published rather than left inline in `_set_header_line` because that writer
    REMOVES every line it matches, so any read-side guard deciding whether a
    header already exists must ask THIS question and not a stricter one. The
    strict `_PLAN_STATUS_HEADER_RE` above is end-anchored on `[A-Za-z-]+`, so a
    hand-annotated `> Status: draft (not ready yet)` or `> Status: **ready**`
    reads as ABSENT through it while remaining fully visible — and deletable —
    here (feature 260813 task 97; see the migration-001 guard in
    `features.planbackfill` and the learning
    `learn-a-read-side-guard-must-use-the-writes-own-predicate`).

    Deliberately prefix-only (`:\\s`), matching the removal exactly: a guard that
    over-triggers costs a skipped file, one that under-triggers costs the file.
    """
    return re.compile(r"^>\s*%s:\s" % re.escape(field))


def _set_header_line(lines, field, value):
    """Set/replace/remove a toolkit-owned `> Field: value` header line (first
    match removed, new one inserted after its anchor). value=None removes it.
    Returns the mutated list."""
    field_rx = _header_field_rx(field)
    out = [ln for ln in lines if not field_rx.match(ln)]
    if value is None:
        return out
    new_line = "> %s: %s" % (field, value)
    anchors = {
        "Status": ("Created", "Feature"),
        "Claimed-by": ("Status", "Created", "Feature"),
        "Superseded-by": ("Status", "Created", "Feature"),
    }.get(field, ())
    insert_idx = None
    for anchor in anchors:
        arx = _header_field_rx(anchor)
        for i, ln in enumerate(out):
            if arx.match(ln):
                insert_idx = i + 1
                break
        if insert_idx is not None:
            break
    if insert_idx is None:
        insert_idx = 1 if out else 0
    out.insert(insert_idx, new_line)
    return out


def _append_status_log(lines, entry):
    """Append `entry` under the append-only `## Status log` section, creating the
    section at end-of-file when absent. Returns the mutated list."""
    idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == _PLAN_STATUS_LOG_HEADING:
            idx = i
            break
    if idx is None:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append(_PLAN_STATUS_LOG_HEADING)
        lines.append("")
        lines.append(entry)
        return lines
    end = len(lines)
    for j in range(idx + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    k = end - 1
    while k > idx and lines[k].strip() == "":
        k -= 1
    lines.insert(k + 1, entry)
    return lines


# --- the state machine -------------------------------------------------------
# The SIX states and `_PLAN_DEFAULT_STATE` are defined one module over (see the
# re-export above); what lives here is everything that DECIDES and WRITES.

_PLAN_TERMINAL_STATES = ("done", "superseded", "cancelled")
_PLAN_CLAIMED_BY_RE = re.compile(r"^>\s*Claimed-by:\s*(.+?)\s*$", re.M)
_PLAN_SUPERSEDED_BY_RE = re.compile(r"^>\s*Superseded-by:\s*(.+?)\s*$", re.M)

# Re-claim staleness: a `running` plan whose `> Claimed-by:` timestamp is older
# than this many hours may be re-claimed by ANY actor (crash recovery). Shared by
# the set-state running->running edge AND dream's report-only drift detection.
STALLED_CLAIM_HOURS = 72

# The FULL legal transition matrix. Key = (from_state, to_state); value dict:
#   actors — allowed --actor values, or None for any actor.
#   needs  — required argv: 'reason' and/or 'superseded_by'.
# Non-argv gates (draft->ready readiness, running->done open-markers==0,
# running->running re-claim) are enforced in cmd_plan_set_state, not here. Any
# edge absent from this dict is REFUSED. `done` is terminal except done->superseded.
_PLAN_STATE_TRANSITIONS = {
    ("draft", "ready"): {"actors": None, "needs": ()},
    ("ready", "draft"): {"actors": None, "needs": ("reason",)},
    ("ready", "running"): {"actors": ("loop", "operator"), "needs": ()},
    ("running", "running"): {"actors": ("loop", "operator"), "needs": ()},
    ("running", "done"): {"actors": ("loop", "operator"), "needs": ()},
    ("draft", "superseded"): {"actors": None, "needs": ("superseded_by",)},
    ("ready", "superseded"): {"actors": None, "needs": ("superseded_by",)},
    ("done", "superseded"): {"actors": None, "needs": ("superseded_by",)},
    ("running", "superseded"): {"actors": ("operator",), "needs": ("superseded_by",)},
    ("draft", "cancelled"): {"actors": None, "needs": ("reason",)},
    ("ready", "cancelled"): {"actors": None, "needs": ("reason",)},
    ("running", "cancelled"): {"actors": ("operator",), "needs": ("reason",)},
    ("cancelled", "draft"): {"actors": ("operator",), "needs": ("reason",)},
}

# Merge precedence for a `> Status:` header CONFLICT (pitfall f): the
# legally-downstream state wins, and a terminal state beats a non-terminal one.
_PLAN_STATE_RANK = {
    "draft": 0, "ready": 1, "running": 2,
    "done": 3, "cancelled": 4, "superseded": 5,
}


def _short_host():
    """Short (unqualified) hostname for the claim stamp; 'unknown' on failure.

    Uses os.uname()/$HOSTNAME rather than the `socket` module — the toolkit is
    forbidden from importing network-capable modules (INV-1, test_invariants)."""
    try:
        node = os.uname().nodename            # POSIX
    except AttributeError:
        node = os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or ""
    node = (node or "unknown").split(".")[0]
    return node or "unknown"


def _claim_age_hours(ts, now_iso):
    """Age in hours between a claim timestamp and `now_iso`, or None if unparsable."""
    a = dates._iso_to_epoch(ts)
    b = dates._iso_to_epoch(now_iso)
    if a is None or b is None:
        return None
    return (b - a) / 3600.0


def _parse_claim(text):
    """Parse the first `> Claimed-by: <handle>@<host> <ISO>` line, or None."""
    m = _PLAN_CLAIMED_BY_RE.search(text or "")
    if not m:
        return None
    raw = m.group(1).strip()
    parts = raw.rsplit(" ", 1)
    who = parts[0].strip() if parts else raw
    ts = parts[1].strip() if len(parts) > 1 else ""
    return {"who": who, "ts": ts, "raw": raw}


def _merge_plan_status(a, b):
    """Resolve a `> Status:` merge conflict deterministically (pitfall f): the
    legally-downstream state wins; a terminal state beats a non-terminal one."""
    ra = _PLAN_STATE_RANK.get((a or "").lower(), -1)
    rb = _PLAN_STATE_RANK.get((b or "").lower(), -1)
    return a if ra >= rb else b


def _load_plan_tolerant(kdir, feature):
    """Read a plan for a STATE edit WITHOUT the lint gate (unlike
    _load_plan_for_edit): set-state must be able to label a not-yet-ready draft.
    Returns (text, None) or (None, error_message)."""
    path = planedit._plan_path(kdir, feature)
    if not os.path.isfile(path):
        return None, "no plan at %s (run `plan new %s` first)" % (path, feature)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read(), None
    except OSError as exc:
        return None, "cannot read plan %s: %s" % (path, exc)


def _setstate_resolve_edge(from_state, to_state, actor):
    """(rule, stderr_text) for the edge — the MATRIX lookup + the actor gate.

    Exactly one of the two is None. An edge absent from _PLAN_STATE_TRANSITIONS is
    refused, with the `done` case answered by name because "illegal transition" is
    a useless thing to read when the real answer is "create a new feature plan".
    """
    rule = _PLAN_STATE_TRANSITIONS.get((from_state, to_state))
    if rule is None:
        msg = "illegal transition %s -> %s (refused)" % (from_state, to_state)
        if from_state == "done":
            msg = ("`done` is terminal — the loop refuses to start a done plan; "
                   "create a NEW feature plan to extend a completed feature "
                   "(only done -> superseded is allowed)")
        return None, "agentware: %s\n" % msg
    if rule["actors"] is not None and actor not in rule["actors"]:
        return None, (
            "agentware: transition %s -> %s requires actor %s (got %r) — workers "
            "may never take this edge\n"
            % (from_state, to_state, "/".join(rule["actors"]), actor))
    return rule, None


def _setstate_argv_error(rule, from_state, to_state, reason, superseded_by):
    """The edge's ARGV contract (`needs`) -> stderr text, or None when satisfied."""
    if "reason" in rule["needs"] and not reason:
        return ("agentware: %s -> %s requires --reason\n"
                % (from_state, to_state))
    if "superseded_by" in rule["needs"] and not superseded_by:
        return ("agentware: %s -> %s requires --superseded-by <feature>\n"
                % (from_state, to_state))
    return None


def _setstate_reclaim_error(text, claim_who, now_iso):
    """running->running: refuse a re-claim of a LIVE claim held by someone else.

    Legal only for the SAME handle@host, or after STALLED_CLAIM_HOURS of silence
    (crash recovery) — an unparsable timestamp is NOT stale, so a corrupt stamp
    cannot be used to steal a live claim.
    """
    claim = _parse_claim(text)
    if claim is None:
        return None
    same = claim["who"] == claim_who
    stale = False
    age = _claim_age_hours(claim["ts"], now_iso)
    if age is not None and age >= STALLED_CLAIM_HOURS:
        stale = True
    if same or stale:
        return None
    return (
        "agentware: running -> running refused — plan is claimed by "
        "'%s'; re-claim only by the same handle@host or after %dh of "
        "staleness (crash recovery)\n" % (claim["raw"], STALLED_CLAIM_HOURS))


def _setstate_gate_error(edge, text, claim_who, now_iso):
    """The three NON-ARGV gates -> stderr text, or None when the edge may proceed.

    They are not expressible in the matrix because each reads the plan BODY:
    draft->ready needs the skeleton filled in, running->done needs every task
    marker closed, running->running needs the claim to be re-claimable.
    """
    if edge == ("draft", "ready"):
        ok, reasons = _planlint._plan_readiness(text)
        if not ok:
            return ("agentware: draft -> ready blocked — plan is not ready:\n"
                    + "".join("  - %s\n" % r for r in reasons))
    if edge == ("running", "done"):
        open_n = planmarkers._plan_open_marker_count(text)
        if open_n > 0:
            return ("agentware: running -> done blocked — %d open task marker(s) "
                    "remain (every task must be ✅)\n" % open_n)
    if edge == ("running", "running"):
        return _setstate_reclaim_error(text, claim_who, now_iso)
    return None


def _setstate_identity(args):
    """(now_iso, claim_who) — the claim stamp for this transition.

    Resolved only AFTER every refusal path has been taken, so a refused
    transition never reads the operator handle.
    """
    now_iso = getattr(args, "now", None) or dates._now_iso()
    handle = (getattr(args, "handle", None) or routing.resolve_user_handle()
              or "operator")
    host = getattr(args, "host", None) or _short_host()
    return now_iso, "%s@%s" % (handle, host)


def _setstate_apply(text, from_state, to_state, actor, claim_who, now_iso,
                    reason, superseded_by):
    """The resulting document: header flip + status-log append, as ONE string.

    `> Claimed-by:` is stamped on entering `running` and REMOVED on leaving it, so
    a claim can never outlive the run that took it. The trailing newline is
    preserved iff the input had one.
    """
    lines = text.splitlines()
    lines = _set_header_line(lines, "Status", to_state)
    if to_state == "running":
        lines = _set_header_line(lines, "Claimed-by", "%s %s" % (claim_who, now_iso))
    elif from_state == "running":
        lines = _set_header_line(lines, "Claimed-by", None)
    if to_state == "superseded":
        lines = _set_header_line(lines, "Superseded-by", superseded_by)
    log_reason = reason or (superseded_by if to_state == "superseded"
                            else ("claim %s" % claim_who if to_state == "running"
                                  else "-"))
    entry = "- %s %s -> %s by %s reason: %s" % (
        now_iso, from_state, to_state, actor, log_reason)
    lines = _append_status_log(lines, entry)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def cmd_plan_set_state(args):
    """Transition a plan's lifecycle state through the legal-transition matrix.

    The SOLE writer of the `> Status:` header + `## Status log`. Refuses every
    edge not in _PLAN_STATE_TRANSITIONS, enforces actor restrictions, the
    readiness / open-markers / re-claim gates, and the --reason / --superseded-by
    requirements; header flip + log append are written together atomically.
    """
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    feature = args.feature
    to_state = args.state
    actor = getattr(args, "actor", "operator") or "operator"
    as_json = getattr(args, "format", "text") == "json"

    if to_state not in _PLAN_STATES:
        sys.stderr.write("agentware: unknown state %r (one of: %s)\n"
                         % (to_state, ", ".join(_PLAN_STATES)))
        return 1

    text, err = _load_plan_tolerant(kdir, feature)
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1

    from_state, status_count = _read_plan_status(text)
    if status_count > 1:
        sys.stderr.write(
            "agentware: plan carries %d `> Status:` header lines — duplicate or "
            "hand-edited status; fix it so exactly one remains\n" % status_count)
        return 1

    rule, edge_err = _setstate_resolve_edge(from_state, to_state, actor)
    if edge_err is not None:
        sys.stderr.write(edge_err)
        return 1

    reason = plansanitize._sanitize_provenance(getattr(args, "reason", None))
    superseded_by = plansanitize._sanitize_provenance(
        getattr(args, "superseded_by", None))
    argv_err = _setstate_argv_error(rule, from_state, to_state, reason,
                                    superseded_by)
    if argv_err is not None:
        sys.stderr.write(argv_err)
        return 1

    now_iso, claim_who = _setstate_identity(args)
    gate_err = _setstate_gate_error((from_state, to_state), text, claim_who,
                                    now_iso)
    if gate_err is not None:
        sys.stderr.write(gate_err)
        return 1

    out = _setstate_apply(text, from_state, to_state, actor, claim_who, now_iso,
                          reason, superseded_by)
    planedit._atomic_write(planedit._plan_path(kdir, feature), out)

    if as_json:
        json.dump({"ok": True, "feature": feature, "from": from_state,
                   "to": to_state, "actor": actor}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("PLAN set-state '%s': %s -> %s (by %s)"
              % (feature, from_state, to_state, actor))
    return 0


def cmd_plan_claim(args):
    """Loop/operator START-edge orchestrator (Context policy). Brings a plan to
    `running`, or REFUSES: draft -> (readiness gate) ready -> running; ready ->
    running; running -> legal re-claim (same handle@host or stalled); done/
    superseded/cancelled -> refuse with the create-a-new-feature instruction. On
    any refusal returns non-zero and leaves state untouched (an aborted/failed run
    therefore honestly STAYS running)."""
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    feature = args.feature
    actor = getattr(args, "actor", "loop") or "loop"
    text, err = _load_plan_tolerant(kdir, feature)
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    state, cnt = _read_plan_status(text)
    if cnt > 1:
        sys.stderr.write("agentware: plan carries duplicate `> Status:` lines; "
                         "fix it before starting\n")
        return 1

    def _to(target):
        ns = argparse.Namespace(
            feature=feature, state=target, actor=actor,
            reason=None, superseded_by=None,
            handle=getattr(args, "handle", None),
            host=getattr(args, "host", None), now=getattr(args, "now", None),
            format=getattr(args, "format", "text"))
        return cmd_plan_set_state(ns)

    if state in ("done", "superseded", "cancelled"):
        sys.stderr.write(
            "agentware: plan is '%s' — the loop refuses to start it. Create a NEW "
            "feature plan to continue this work (only done->superseded is a legal "
            "edge).\n" % state)
        return 1
    if state == "draft":
        rc = _to("ready")            # prints the readiness message on failure
        if rc != 0:
            return rc
        return _to("running")
    if state in ("ready", "running"):
        return _to("running")        # ready->running, or a legal running re-claim
    sys.stderr.write("agentware: unrecognized plan state %r\n" % state)
    return 1


# Explicit facade export list (public AND underscore-private) — the entrypoint
# binds these names so SourceFileLoader-based tests still reach them as
# CLI.<name>. See the M3/_EXPORTS convention in the plan. `_PLAN_DEFAULT_STATE`
# is the one it does NOT re-bind: the monolith still defines it for its own
# unported state-machine call site, with a literal identical to this one, and the
# duplicate retires with the `plan` port (task 39).
_EXPORTS = [
    "_PLAN_DEFAULT_STATE",
    "_PLAN_STATUS_HEADER_RE",
    "_PLAN_STATUS_LOG_HEADING",
    "_read_plan_status",
    "_header_field_rx",
    "_set_header_line",
    "_append_status_log",
    "_PLAN_TERMINAL_STATES",
    "_PLAN_CLAIMED_BY_RE",
    "_PLAN_SUPERSEDED_BY_RE",
    "STALLED_CLAIM_HOURS",
    "_PLAN_STATE_TRANSITIONS",
    "_PLAN_STATE_RANK",
    "_short_host",
    "_claim_age_hours",
    "_parse_claim",
    "_merge_plan_status",
    "_load_plan_tolerant",
    "cmd_plan_set_state",
    "cmd_plan_claim",
]

__all__ = list(_EXPORTS)
