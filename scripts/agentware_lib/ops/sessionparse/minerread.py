"""agentware_lib.ops.sessionparse.minerread — the miner's bounded corpus READ (layer: ops).

Owns: everything the miner reads BEFORE it takes the queue lock — the forward-only
byte-offset reader (`_mine_read_since`), the two log readers built on it
(`_mine_read_prompts`, `_mine_read_metrics`), the bounded session selection
(`_mine_select_sessions`), the per-session record merge (`_transcript_read_session`)
and the pass that composes them into one window (`_transcript_mine_read`).

WHY THIS IS ITS OWN MODULE
The split is the LOCK. Everything here runs OUTSIDE the flock (F5 — it is the
expensive I/O) and ADVANCES NOTHING: it returns what to fold, and the caller
decides under the lock what to do with it. Everything in `minerscan` runs INSIDE
the lock and mutates the tally, the ledgers and the queue. Two different failure
domains — a slow read here delays a mine, a slow write there holds a lock every
session-start hook contends on — and the file was making both promises at once.

Read-only with respect to the operator's corpus: nothing here opens a file for
writing, and a fault on ANY source degrades that ONE source to empty while
leaving its watermark where it was, so the next window retries the same bytes
rather than skipping them.

Does NOT own: the mining PASSES, the record builder, the fp belt and the
orchestrator (ops.sessionparse.minerscan); the cursor/caps/state
(ops.sessionparse.minerstate); the prompts-record parser and the session lister
(ops.sessionparse.minerfolds); the ERR reconstruction (ops.sessionparse.minererrors);
the queue itself (ops.preferqueue).
Layer deps: ops -> same-layer sessionparse siblings (minererrors, minerfolds,
minerstate) and ops.premisecheck, plus stdlib. One direction only — `minerscan`
depends on THIS module, never the reverse, so the package graph stays acyclic.
Fan-in: 1 at landing (regen: python3 tests/_manifest_gen.py --write).
"""

import json
import os

from agentware_lib.ops.premisecheck import _transcript_read_live
from agentware_lib.ops.sessionparse.minererrors import (
    _transcript_reconcile_errors,
)
from agentware_lib.ops.sessionparse.minerfolds import (
    _parse_prompts_records,
    _transcript_list_session_ids,
)
from agentware_lib.ops.sessionparse.minerstate import (
    MINER_SESSION_CAP,
    _transcript_session_settled,
)


def _transcript_read_session(sdir, sid):
    """A session's tool-call records for the error-recovery detector: the hook's
    live.jsonl UNION the ERR records reconstructed from the durable transcript
    (F-B), merged onto one timeline. Stable-sorted by ts, so within an identical
    second the hook's own records keep their append order and a reconstruction
    lands after them — the conservative tie-break (an ERR and its fix are never
    the same second in practice, since a fix costs at least one model turn)."""
    live = _transcript_read_live(os.path.join(sdir, "live.jsonl"))
    errs = _transcript_reconcile_errors(sid, live)
    if not errs:
        return live
    merged = live + errs
    merged.sort(key=lambda r: str(r.get("ts") or ""))
    return merged


def _mine_read_since(path, offs, key):
    """Read a forward-only log from its stored byte offset. Returns
    `(text, new_offset)`, or `(None, stored_offset)` when the source could not be
    read at all.

    ONE helper because the twelve-line ladder was written out once per source and
    each line of it is load-bearing: a stored offset that is negative or PAST EOF
    (log rotation, truncation) resets to 0 rather than reading nothing forever —
    fp-dedup downstream is what makes the re-read safe — and an OSError degrades
    that ONE source while leaving its watermark exactly where it was, so the next
    window retries the same bytes instead of skipping them.

    None and "" are different facts: an unreadable log and an empty one both yield
    no records, but only the first must leave the watermark alone.
    """
    stored = int(offs.get(key) or 0)
    try:
        start = stored
        fsize = os.path.getsize(path)
        if start < 0 or start > fsize:
            start = 0
        with open(path, "rb") as f:
            f.seek(start)
            raw = f.read()
    except OSError:
        return None, stored
    return raw.decode("utf-8", "replace"), start + len(raw)


def _mine_read_prompts(logs, offs):
    """New origin=human prompt turns since the watermark, as
    `[(sid, turn_id, body, cwd)]` plus the new offset.

    Only `origin=human` turns are recurrence material: a loop-authored or
    hook-authored turn is the machine talking to itself, and counting it would let
    the miner propose its own output back to the operator. A whitespace-only body
    is dropped before it can normalise to an empty tally key.
    """
    text, new_off = _mine_read_since(os.path.join(logs, "prompts.log"),
                                     offs, "prompts")
    if text is None:
        return [], new_off
    prompts = []
    for hdr, body in _parse_prompts_records(text):
        if hdr.get("origin") != "human":
            continue
        b = (body or "").strip()
        if not b:
            continue
        prompts.append((hdr.get("sid") or "", hdr.get("turn") or "", b,
                        hdr.get("cwd") or ""))
    return prompts, new_off


def _mine_read_metrics(logs, offs):
    """New metrics events since the watermark, plus the new offset.

    Triage CONTEXT only — terminal events are feature-keyed, so corroboration is
    best-effort and a malformed or non-object line is skipped rather than
    reported. Nothing here gates an emission.
    """
    text, new_off = _mine_read_since(os.path.join(logs, "metrics.jsonl"),
                                     offs, "metrics")
    if text is None:
        return [], new_off
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events, new_off


def _mine_select_sessions(kdir, logs, state, backfill, session_cap):
    """The bounded session selection, as `[(sid, [live_records], settled)]`.

    The two modes select DISJOINT sets on purpose. Incremental takes only sessions
    the state has never seen (`on_disk - processed - pending`), so a steady-state
    mine is cheap; backfill drains the historical `pending & on_disk` set a capped
    slice at a time. Intersecting with what is on disk in both arms is what keeps
    the state bounded by the live corpus rather than by history.
    """
    on_disk = set(_transcript_list_session_ids(kdir))
    processed = set(state.get("processed_sessions") or [])
    pending = set(state.get("backfill_pending") or [])
    if backfill:
        cand_sids = sorted(pending & on_disk)          # historical drain
    else:
        cand_sids = sorted(on_disk - processed - pending)  # only NEW sessions
    scanned = []
    for sid in cand_sids[:session_cap]:
        sdir = os.path.join(logs, "sessions", sid)
        scanned.append((sid,
                        _transcript_read_session(sdir, sid),
                        _transcript_session_settled(sdir)))
    return scanned


def _transcript_mine_read(kdir, state, backfill, session_cap):
    """PURE, bounded corpus read done OUTSIDE the flock (F5 — the expensive I/O).
    Advances NOTHING; returns what to fold under the lock:
      prompts    : [(sid, turn_id, body, cwd)] new origin=human turns since offset
      metrics    : [event_dict] new metrics events since offset (triage context)
      scanned    : [(sid, [live_records], settled_bool)] bounded session selection
      new_offsets: {'prompts': int, 'metrics': int} EOF actually read up to
      mode       : 'backfill' | 'incremental'
    Best-effort: any OSError degrades that ONE source to empty. No clock/network.
    A stored offset past EOF (log rotation/truncation) resets to 0 — fp-dedup
    guards a re-emit.

    Three independent sources, three readers: nothing here is shared between them
    but the state they read their watermark from, which is why the body is a
    dispatch rather than three inlined ladders.
    """
    logs = os.path.join(kdir, "logs")
    offs = state.get("offsets") or {}
    session_cap = max(0, int(session_cap or MINER_SESSION_CAP))
    prompts, p_new = _mine_read_prompts(logs, offs)
    metrics, m_new = _mine_read_metrics(logs, offs)
    scanned = _mine_select_sessions(kdir, logs, state, backfill, session_cap)
    return {"prompts": prompts, "metrics": metrics, "scanned": scanned,
            "new_offsets": {"prompts": p_new, "metrics": m_new},
            "mode": "backfill" if backfill else "incremental"}


_EXPORTS = [
    "_transcript_read_session",
    "_mine_read_since",
    "_mine_read_prompts",
    "_mine_read_metrics",
    "_mine_select_sessions",
    "_transcript_mine_read",
]

__all__ = list(_EXPORTS)
