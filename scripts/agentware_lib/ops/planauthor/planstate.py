"""agentware_lib.ops.planauthor.planstate — plan `> Status:` header primitives (layer: ops).

Owns: the three PURE plan-text primitives of the plan state machine — reading the
`> Status:` header (_read_plan_status), setting/replacing/removing a toolkit-owned
`> Field: value` header line at its anchor (_set_header_line), and appending to the
append-only `## Status log` section (_append_status_log) — plus the three constants
they read (_PLAN_DEFAULT_STATE, _PLAN_STATUS_HEADER_RE, _PLAN_STATUS_LOG_HEADING).
Does NOT own: the state MACHINE — the six states, the legal-transition matrix, the
claim/supersede parsing and the cmd_plan_set_state/claim/state_backfill handlers are
still resident in the entrypoint and arrive with the `plan` port (task 39); the plan
GENERATION half (_plan_skeleton/_plan_path/_atomic_write/cmd_plan_new/…) ->
agentware_lib.ops.planauthor.planedit; the ENFORCEMENT half -> agentware_lib.ops.planlint;
the migration that BACKFILLS a status onto every legacy plan ->
agentware_lib.features.migratecmd (it needs the live-session detector, which is a
features-layer symbol, so the payload cannot live down here — see that module).
Layer deps: ops -> stdlib only (this module imports `re` and nothing from agentware_lib) —
all DOWNWARD; nothing above ops is imported.
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

Deterministic and hermetic: pure text in, text out. No I/O, no clock, no KB access.
"""

import re


_PLAN_DEFAULT_STATE = "draft"
_PLAN_STATUS_HEADER_RE = re.compile(r"^>\s*Status:\s*([A-Za-z-]+)\s*$", re.M)
_PLAN_STATUS_LOG_HEADING = "## Status log"


def _read_plan_status(text):
    """FIRST-MATCH `> Status:` from the top of the file -> (state, count).

    `count` is how many status-like header lines exist (>1 => duplicate/
    hand-edited, an error for lint/set-state). Missing => (_PLAN_DEFAULT_STATE, 0)
    so every legacy plan reads as `draft`.
    """
    matches = _PLAN_STATUS_HEADER_RE.findall(text or "")
    if not matches:
        return (_PLAN_DEFAULT_STATE, 0)
    return (matches[0].strip().lower(), len(matches))


def _set_header_line(lines, field, value):
    """Set/replace/remove a toolkit-owned `> Field: value` header line (first
    match removed, new one inserted after its anchor). value=None removes it.
    Returns the mutated list."""
    field_rx = re.compile(r"^>\s*%s:\s" % re.escape(field))
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
        arx = re.compile(r"^>\s*%s:\s" % re.escape(anchor))
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
    "_set_header_line",
    "_append_status_log",
]

__all__ = list(_EXPORTS)
