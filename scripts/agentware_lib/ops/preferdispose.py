"""agentware_lib.ops.preferdispose — APPROVAL and REJECTION (layer: ops).

Owns: the human disposition of a queued candidate — `cmd_prefer_approve`,
`cmd_prefer_reject` and the workorder canceller (`_prefer_cancel_workorder`).

REJECTION IS DURABLE, AND THAT IS THE POINT
Rejecting is not "do nothing". A rejected candidate is recorded and its
fingerprint ARCHIVED, so the miner does not propose it again tomorrow. Without
that the operator faces the same suggestion nightly, stops reading the queue, and
the whole review mechanism dies quietly — the failure is not a wrong suggestion,
it is a queue nobody opens. Rejection also CANCELS any workorder the candidate had
already produced, so a refused idea leaves nothing running behind it.

Approval is the only path from a candidate to durable knowledge, and it is
explicitly a HUMAN action: the miner and the classify drain propose, this module
is where a person disposes. That asymmetry is what makes a nightly automated
miner safe to run at all.

Does NOT own: the classify pass and workorder emission (ops.preferclassify); the
queue (ops.preferqueue); the capture write (ops.prefercapture); the `prefer`
verb's other subcommands (ops.prefercmd).
Layer deps: ops -> core + same-layer ops (preferqueue, prefercapture,
preferclassify, planauthor for plan state).
Fan-in: 1 at landing (regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith — byte-identical, comments and all.
"""

import argparse
import json
import os
import sys

import agentware_lib.core.config as config
from agentware_lib.core.config import require_knowledge_dir, resolve_knowledge_dir
from agentware_lib.core.dates import _now_iso
from agentware_lib.ops.planauthor.planedit import _atomic_write, _plan_path
from agentware_lib.ops.planauthor.planstate import (
    _PLAN_TERMINAL_STATES,
    _read_plan_status,
    cmd_plan_set_state,
)
from agentware_lib.core.capture import _dream_capture
from agentware_lib.ops.prefercapture import _prefer_capture
from agentware_lib.ops.preferqueue import (
    PREFER_QUEUE_ARCHIVE_REL,
    PREFER_QUEUE_REL,
    _prefer_cand_fp,
    _prefer_queue_lock,
    _prefer_queue_records,
)


def cmd_prefer_approve(args):
    """`prefer approve <workorder>` — record the operator's approval of a
    dream-proposed steering candidate by flipping its queue record proposed ->
    approved. Approval = operator-run the emitted workorder through the normal
    loop (Q3); this verb is the thin sugar that marks it approved (it never
    auto-activates a source=user pref, and never claims/advances the plan state
    for the loop). A dispatched STATE-MUTATING writer, so it is registered in
    `_kb_writer_funcs()` (the predispatch gate keys on the dispatched
    args.func)."""
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    target = (getattr(args, "id", None) or "").strip()
    if not target:
        sys.stderr.write("agentware: prefer approve requires a workorder id\n")
        return 1
    qpath = os.path.join(kdir, PREFER_QUEUE_REL)
    if not os.path.isfile(qpath):
        sys.stderr.write("agentware: no prefer-queue to approve from\n")
        return 1
    with _prefer_queue_lock(qpath) as lf:
        if lf is None:
            sys.stderr.write("agentware: cannot lock the prefer-queue\n")
            return 1
        recs = _prefer_queue_records(qpath)   # non-object queue lines skipped
        hit = next((r for r in recs
                    if r.get("status") == "proposed"
                    and r.get("workorder") == target), None)
        if hit is None:
            sys.stderr.write(
                "agentware: no proposed candidate with workorder '%s' — see "
                "`prefer list` or the classify report\n" % target)
            return 1
        hit["status"] = "approved"
        _atomic_write(qpath, "\n".join(
            json.dumps(r, ensure_ascii=False) for r in recs) + "\n")
    feat = target[len("auto/"):] if target.startswith("auto/") else target
    print("approved: %s — run it via the loop (e.g. ./agentware.sh %s) or claim "
          "the workorder." % (target, feat))
    return 0


def _prefer_cancel_workorder(kdir, workorder, reason):
    """Cancel an emitted `work/auto/pref-*` workorder for `prefer reject`, called
    BEFORE the queue flip so a cancel FAILURE never orphans a draft workorder (it
    refuses the reject instead of leaving a rejected record pointing at a still-draft
    plan that inflates the g3 cap). Returns (cancelled, disposition):
      'cancelled' -> a live draft/ready workorder was cancelled;
      'absent'    -> no plan.md (nothing to cancel) -> proceed with the flip;
      'terminal'  -> already cancelled/superseded/done -> proceed with the flip;
      'refuse'    -> a LIVE/unreadable workorder that would NOT cancel -> the caller
                     must REFUSE (do not flip the queue) so the reject stays re-driveable."""
    plan_path = _plan_path(kdir, workorder)
    if not os.path.isfile(plan_path):
        return False, "absent"
    try:
        with open(plan_path, encoding="utf-8") as f:
            state, _cnt = _read_plan_status(f.read())
    except OSError:
        return False, "refuse"
    if state in _PLAN_TERMINAL_STATES:
        return False, "terminal"
    ns = argparse.Namespace(feature=workorder, state="cancelled", actor="operator",
                            reason="prefer reject: %s" % reason, format="json")
    rc, _out = _dream_capture(cmd_plan_set_state, ns)
    return (True, "cancelled") if rc == 0 else (False, "refuse")


def _reject_workorder_id(target):
    """The bare `auto/pref-<slug>` workorder this reject may act on.

    Returns `(wo, None)` or `(None, refusal)`. Defense in depth, and the reason
    is the SINK: the resolved id feeds `cmd_plan_set_state`'s feature path, so a
    traversal (`../…`), a nested path or any other namespace must be refused
    before it reaches a path join. A bare slug is accepted and normalised, since
    that is what `prefer review` prints beside every open item.
    """
    wo = target if target.startswith("auto/") else "auto/" + target
    if not wo.startswith("auto/pref-") or "/" in wo[len("auto/"):]:
        return None, ("agentware: prefer reject only targets an auto/pref-* "
                      "workorder (got %r)\n" % target)
    return wo, None


def _reject_find_target(raw, wo):
    """Locate the `proposed` record for workorder `wo` among RAW queue lines.

    Returns `(index, record)` or `(None, None)`. It walks the raw lines rather
    than parsed records precisely because the caller rewrites the file: every
    other line has to survive byte-for-byte, so the index into the ORIGINAL split
    is the thing the caller needs, not a position in a filtered list.

    A blank line, a non-JSON line and a non-object line are skipped rather than
    dropped — the queue is producer-agnostic, and a line this verb cannot read is
    a line it must not destroy.
    """
    for i, line in enumerate(raw):
        s = line.strip()
        if not s:
            continue
        try:
            rec = json.loads(s)
        except ValueError:
            continue
        if (isinstance(rec, dict) and rec.get("status") == "proposed"
                and rec.get("workorder") == wo):
            return i, rec
    return None, None


def _reject_report(args, wo, cancelled, disp):
    """Report a completed rejection on the caller's chosen surface.

    The prose arm names the workorder's fate explicitly (cancelled / absent /
    terminal) because "rejected" alone leaves the operator wondering whether
    something is still running, and it says the plan.md was KEPT — that file is
    the drain's re-emit sentinel, so its presence is deliberate rather than
    leftover.
    """
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": True, "workorder": wo, "status": "rejected",
                   "workorder_cancelled": cancelled, "workorder_state": disp},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return
    note = {"cancelled": "workorder cancelled", "absent": "no workorder on disk",
            "terminal": "workorder already terminal"}.get(disp, disp)
    print("rejected: %s — candidate marked rejected; %s (plan.md kept as the "
          "re-emit sentinel)." % (wo, note))


def cmd_prefer_reject(args):
    """`prefer reject <workorder> [--reason …]` — reject a dream-proposed steering
    candidate as NOISE: the governed counterpart to `prefer approve`. Under the queue
    lock it CANCELS the emitted `work/auto/pref-*` workorder FIRST (via the shipped
    `plan set-state … cancelled`), then flips the record proposed -> rejected — so a
    cancel failure never orphans a draft workorder, and only the target record's queue
    line is rewritten (every other line is byte-preserved). The plan.md is KEPT on
    disk; the classify drain's state-aware sentinel treats a cancelled workorder as
    RESOLVED, so a re-queued rejected candidate is re-marked rejected, never
    resurrected. Closes the proposed-noise dead end that previously forced an
    ungoverned hand-edit of the prefer-queue. A dispatched STATE-MUTATING writer,
    registered in `_kb_writer_funcs()` (the predispatch gate keys on args.func)."""
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    target = (getattr(args, "id", None) or "").strip()
    if not target:
        sys.stderr.write("agentware: prefer reject requires a workorder id\n")
        return 1
    # Namespace guard: only ever act on a bare `auto/pref-<slug>` workorder — the
    # target feeds cmd_plan_set_state's feature path, so refuse a traversal or any
    # other namespace (defense-in-depth).
    wo, refusal = _reject_workorder_id(target)
    if refusal is not None:
        sys.stderr.write(refusal)
        return 1
    qpath = os.path.join(kdir, PREFER_QUEUE_REL)
    if not os.path.isfile(qpath):
        sys.stderr.write("agentware: no prefer-queue to reject from\n")
        return 1
    reason = (getattr(args, "reason", None) or "").strip() \
        or "operator-rejected as noise via `prefer reject`"
    with _prefer_queue_lock(qpath) as lf:
        if lf is None:
            sys.stderr.write("agentware: cannot lock the prefer-queue\n")
            return 1
        # Read RAW lines and PRESERVE every one byte-for-byte; only the matched
        # target record's line is rewritten (never silently drop a malformed line).
        # Split on '\n' ONLY (NOT str.splitlines(), which also splits on U+2028/
        # U+2029/U+0085 that the producer + drain leave verbatim inside a record —
        # splitting there would shred an unrelated record and could make a candidate
        # unrejectable). `"\n".join(raw)` is then a byte-exact round-trip.
        with open(qpath, encoding="utf-8") as f:
            raw = f.read().split("\n")
        hit_idx, hit = _reject_find_target(raw, wo)
        if hit is None:
            sys.stderr.write(
                "agentware: no proposed candidate with workorder '%s' — see "
                "`prefer review` or the classify report\n" % wo)
            return 1
        # Cancel FIRST: a live workorder that will not cancel -> refuse without
        # touching the queue (stays re-driveable); absent/terminal -> proceed.
        cancelled, disp = _prefer_cancel_workorder(kdir, wo, reason)
        if disp == "refuse":
            sys.stderr.write(
                "agentware: could not cancel workorder '%s' (live or unreadable) — "
                "NOT rejecting; resolve the workorder and retry\n" % wo)
            return 1
        hit["status"] = "rejected"
        hit["rejected_reason"] = reason
        raw[hit_idx] = json.dumps(hit, ensure_ascii=False)
        # `split("\n")` captured the file's trailing newline as a trailing "" element,
        # so join alone reproduces the exact byte structure (no extra newline).
        _atomic_write(qpath, "\n".join(raw))
    _reject_report(args, wo, cancelled, disp)
    return 0


_EXPORTS = [
    "cmd_prefer_approve",
    "_prefer_cancel_workorder",
    "_reject_workorder_id",
    "_reject_find_target",
    "_reject_report",
    "cmd_prefer_reject",
]

__all__ = list(_EXPORTS)
