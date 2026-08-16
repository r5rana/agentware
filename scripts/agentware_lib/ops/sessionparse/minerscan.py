"""agentware_lib.ops.sessionparse.minerscan — the mining PASSES (layer: ops).

Owns: the passes that walk settled sessions and produce candidates — the reader
(`_transcript_mine_read`), the record builder (`_transcript_build_record`), the
recurrence pass (`_transcript_mine_recurrence`), the error-recovery pass
(`_transcript_mine_error_recovery`), the queue-fingerprint reader
(`_transcript_queue_fps`), the orchestrator (`_transcript_mine`) and its dream-step
adapter (`_dream_step_transcript_mine`).

WHY TWO PASSES AND NOT ONE
They answer different questions and have different evidence bars. RECURRENCE asks
"has the operator asked for this repeatedly across DISTINCT sessions?" and is
gated hard (see minerfolds) because a repeated phrasing is weak evidence on its
own. ERROR-RECOVERY asks "did something fail and then get fixed?", which is strong
evidence from a single occurrence. Collapsing them into one pass would force one
precision bar onto two different kinds of signal.

Everything here is OFFLINE and READ-ONLY with respect to the operator's work: it
reads transcripts and appends candidates to the prefer queue, and it never
promotes anything. A candidate is a PROPOSAL for a human; the miner has no path
to durable knowledge that does not pass through review.

Deduplication runs against the queue's existing fingerprints (`_transcript_queue_fps`)
as well as the miner's own emitted set, so a candidate the operator already
rejected does not return every night.

Does NOT own: the cursor/caps (minerstate); record extraction and redaction
(minerextract); error signatures (minererrors); the recurrence precision gate
(minerfolds); the queue itself (ops.preferqueue).
Layer deps: ops -> core + same-layer sessionparse siblings and ops.preferqueue.
Fan-in: 1 at landing (regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith — byte-identical, comments and all.
"""

import json
import os
import time

import agentware_lib.core.config as config
from agentware_lib.core.config import resolve_knowledge_dir
from agentware_lib.core.dates import _now_iso
from agentware_lib.core.projectcontext import _canonical_project_key, _resolve_context
from agentware_lib.ops.planauthor.plankernelpath import _pref_hits_kernel
from agentware_lib.ops.prefergate import _pref_loosens_gate
from agentware_lib.ops.preferqueue import (
    PREFER_QUEUE_ARCHIVE_REL,
    PREFER_QUEUE_REL,
    _prefer_archive_fingerprints,
    _prefer_cand_fp,
)
from agentware_lib.ops.premisecheck import (
    _transcript_read_live,
    _transcript_recurrence_gate,
    _transcript_recurrence_relevant,
)
from agentware_lib.ops.sessionparse.minererrors import (
    MINER_ERRREC_DEDUP,
    _transcript_errrec_gate,
    _transcript_errrec_key,
    _transcript_err_signature,
    _transcript_error_recovery_text,
    _transcript_reconcile_errors,
)
from agentware_lib.ops.sessionparse.minerextract import (
    _transcript_redact_secrets,
    _transcript_tool_target,
)
from agentware_lib.ops.sessionparse.minerfolds import (
    MINER_RECURRENCE_N,
    _parse_prompts_records,
    _transcript_is_err_status,
    _transcript_list_session_ids,
    _transcript_norm_key,
)
from agentware_lib.ops.sessionparse.minerstate import (
    MINER_SESSION_CAP,
    MINER_SOURCE_SESSIONS_CAP,
    MINER_RECURRENCE_RETIRED,
    MINER_TALLY_CAP,
    PREFER_QUEUE_MINER_PRODUCER,
    _transcript_claude_path,
    _transcript_default_state,
    _transcript_load_state,
    _transcript_prune_emitted,
    _transcript_prune_tally,
    _transcript_is_ok_status,
    _transcript_session_settled,
    _transcript_write_state,
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
    guards a re-emit."""
    logs = os.path.join(kdir, "logs")
    offs = state.get("offsets") or {}
    session_cap = max(0, int(session_cap or MINER_SESSION_CAP))

    # --- prompts.log (recurrence source): forward byte offset, origin=human ---
    prompts = []
    logp = os.path.join(logs, "prompts.log")
    p_new = int(offs.get("prompts") or 0)
    try:
        start = int(offs.get("prompts") or 0)
        fsize = os.path.getsize(logp)
        if start < 0 or start > fsize:
            start = 0
        with open(logp, "rb") as f:
            f.seek(start)
            raw = f.read()
        p_new = start + len(raw)
        for hdr, body in _parse_prompts_records(raw.decode("utf-8", "replace")):
            if hdr.get("origin") != "human":
                continue
            b = (body or "").strip()
            if not b:
                continue
            prompts.append((hdr.get("sid") or "", hdr.get("turn") or "", b,
                            hdr.get("cwd") or ""))
    except OSError:
        prompts, p_new = [], int(offs.get("prompts") or 0)

    # --- metrics.jsonl (triage context): forward byte offset ---
    metrics = []
    mpath = os.path.join(logs, "metrics.jsonl")
    m_new = int(offs.get("metrics") or 0)
    try:
        start = int(offs.get("metrics") or 0)
        fsize = os.path.getsize(mpath)
        if start < 0 or start > fsize:
            start = 0
        with open(mpath, "rb") as f:
            f.seek(start)
            raw = f.read()
        m_new = start + len(raw)
        for line in raw.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict):
                metrics.append(ev)
    except OSError:
        metrics, m_new = [], int(offs.get("metrics") or 0)

    # --- sessions/<sid>/live.jsonl (error-recovery source): bounded selection ---
    on_disk = set(_transcript_list_session_ids(kdir))
    processed = set(state.get("processed_sessions") or [])
    pending = set(state.get("backfill_pending") or [])
    if backfill:
        cand_sids = sorted(pending & on_disk)          # historical drain
    else:
        cand_sids = sorted(on_disk - processed - pending)  # only NEW sessions
    cand_sids = cand_sids[:session_cap]
    scanned = []
    for sid in cand_sids:
        sdir = os.path.join(logs, "sessions", sid)
        scanned.append((sid,
                        _transcript_read_session(sdir, sid),
                        _transcript_session_settled(sdir)))
    return {"prompts": prompts, "metrics": metrics, "scanned": scanned,
            "new_offsets": {"prompts": p_new, "metrics": m_new},
            "mode": "backfill" if backfill else "incremental"}


def _transcript_build_record(kdir, sid, turn_id, text, signal, cwd="",
                             recurrence_count=None, source_sessions=None):
    """Build a queue record with the IDENTICAL shared schema as the Stop-hook
    producer (ts/sid/cwd/project/turn_id/text/matched_pattern/status/producer)
    plus ADDITIVE fields (signal, recurrence_count, source_sessions). `text` is
    stored VERBATIM as inert data (R-SEC-02) — never interpolated/executed. `ts`
    is append-time (_now_iso, like the producer) — NOT a mined clock-in-a-decision;
    determinism (INV-1) is scoped to the fp / text / tally, never ts."""
    proj = None
    if cwd:
        try:
            sig = _resolve_context(cwd).get("project_name") or None
        except Exception:
            sig = None
        proj = _canonical_project_key(kdir, sig)
    rec = {
        "ts": _now_iso(),
        "sid": sid or "",
        "cwd": cwd or "",
        "project": proj,
        "turn_id": turn_id or "",
        "text": text if isinstance(text, str) else "",
        "matched_pattern": signal,
        "status": "queued",
        "producer": PREFER_QUEUE_MINER_PRODUCER,
        "signal": signal,
    }
    if recurrence_count is not None:
        rec["recurrence_count"] = int(recurrence_count)
    if source_sessions is not None:
        rec["source_sessions"] = sorted(set(source_sessions))[:MINER_SOURCE_SESSIONS_CAP]
    return rec


def _transcript_mine_recurrence(prompts, tally, kdir, emitted_keys=None,
                                gate=None):
    """Recurrence detector (Q2). FOLD this window's origin=human bodies INTO the
    persistent tally (CUMULATIVE across mine windows, F1): normalize each body to
    a deterministic key and add its SESSION id to that key's DISTINCT-session set
    (a body asked twice in ONE session counts once, F4). Emit a candidate the
    FIRST time a key reaches MINER_RECURRENCE_N distinct sessions AND is not yet
    emitted; flip `emitted` so it fires EXACTLY ONCE even as the count climbs.
    Pure tabulation (sets + counts, order-independent). FOLDS the WHOLE window
    first, THEN emits with the FINAL distinct-session count — so N sessions asking
    in ONE window yield recurrence_count==N (not N stopping at the threshold).
    Mutates `tally` in place; LRU-moves each touched key to the end. Returns the
    new candidate records (identity = the most-recent occurrence, so a re-mine of
    the SAME window yields the same fp; emitted-once via the flag).

    F-A (emit-once under LRU eviction): the per-entry `emitted` flag dies with its
    tally entry when _transcript_prune_tally drops it, so an evicted-then-recurring
    key would re-emit a DUPLICATE whose fp (keyed on the NEWER occurrence) the
    archive/live-queue fp-dedup cannot catch. The bounded persistent `emitted_keys`
    ledger OUTLIVES the tally entry: a key present there is SUPPRESSED regardless
    of its (possibly restarted) tally count. Mutates `emitted_keys` in place,
    appending each newly-emitted key (LRU order). Bounded, so a key pruned past
    MINER_EMITTED_CAP MAY re-emit once — accepted, and far rarer than today's
    per-eviction duplication.

    F-C (precision): a key reaching N still only EMITS if it passes the
    _transcript_recurrence_relevant gate (min length + cue-or-not-an-ack), so
    high-frequency non-preference turns ("continue", "yes") never reach the
    queue. The gate is EMISSION-side only: the tally still COUNTS every
    occurrence, and a gated key is neither flagged `emitted` nor written to the
    ledger — so re-tuning the thresholds re-considers it on the next window."""
    if emitted_keys is None:
        emitted_keys = []
    if gate is None:
        gate = _transcript_recurrence_gate()
    ledger = set(emitted_keys)
    touched = []          # keys folded this window, first-touch order (dedup)
    for sid, turn_id, body, cwd in prompts:
        key = _transcript_norm_key(body)
        if not key:
            continue
        entry = tally.get(key)
        if not isinstance(entry, dict):
            entry = {"sessions": [], "latest": {}, "emitted": False}
        sids = set(entry.get("sessions") or [])
        sids.add(sid)
        entry["sessions"] = sorted(sids)
        entry["latest"] = {"sid": sid, "turn_id": turn_id, "text": body,
                           "cwd": cwd}
        tally.pop(key, None)          # LRU: re-insert at the end (most-recent)
        tally[key] = entry
        if key not in touched:
            touched.append(key)
    out = []
    # RETIRED (260719): the recurrence arm emits NOTHING. The tally FOLD above is
    # preserved (harmless cumulative counting; zero behavior change to persisted
    # state) — only EMISSION is disabled. On a future re-enable, emission resumes for
    # keys folded in SUBSEQUENT windows; a key that first reached N during retirement
    # (and never `emitted`) will emit the next time it is re-folded (i.e. when it next
    # recurs), NOT retroactively for windows already scanned. The SEPARATE error-
    # recovery arm is untouched.
    if MINER_RECURRENCE_RETIRED:
        return out
    for key in touched:
        entry = tally[key]
        count = len(entry.get("sessions") or [])
        if count >= MINER_RECURRENCE_N and not entry.get("emitted"):
            if not _transcript_recurrence_relevant(key, gate):
                continue              # F-C: noise. NOT flagged, NOT ledgered ->
                                      # a later re-tune reconsiders this key.
            if key in ledger:         # F-A: emitted BEFORE its tally entry was
                entry["emitted"] = True   # LRU-evicted -> suppress the duplicate
                continue                  # (re-flag so the live entry agrees)
            entry["emitted"] = True   # fires EXACTLY once, even as count climbs
            ledger.add(key)
            emitted_keys.append(key)  # persistent, survives tally eviction
            latest = entry.get("latest") or {}
            out.append(_transcript_build_record(
                kdir, sid=latest.get("sid") or "",
                turn_id=latest.get("turn_id") or "",
                text=latest.get("text") or "", signal="recurrence",
                cwd=latest.get("cwd") or "", recurrence_count=count,
                source_sessions=entry.get("sessions") or []))
    return out


def _transcript_mine_error_recovery(scanned, metrics, kdir, emitted_keys=None,
                                    gate=None):
    """Error-recovery detector (Task 3). Within each scanned session's live.jsonl,
    find a tool call with an ERR status FOLLOWED by a later ok call of the SAME
    tool AND primary target — a hit-and-fixed pair — and emit ONE candidate per
    (session, tool, target). A BARE error with no later success yields nothing
    (conservative). `metrics` is optional triage context (terminal events are
    feature-keyed, so corroboration is best-effort). No LLM/clock; deterministic
    string derivation only.

    F-E (cross-session dedup, 260722 go-live gate): the fp belt downstream keys
    on _prefer_cand_fp whose fp EMBEDS sid, so the SAME failure mode recovered
    in N sessions — in one batch OR across incremental runs — would emit N
    near-identical candidates. Emission is therefore ALSO deduped on the
    session-independent _transcript_errrec_key against the PERSISTENT
    `emitted_keys` ledger (the miner state's `err_emitted_keys`, mirroring
    recurrence's F-A ledger): a mode already emitted — this batch or any prior
    run — is suppressed; a genuinely-distinct signature still emits. Mutates
    `emitted_keys` in place (LRU append order; caller prunes + persists under
    the mine lock). Mode 'off' restores legacy per-session emission and neither
    consults nor grows the ledger."""
    if emitted_keys is None:
        emitted_keys = []
    if gate is None:
        gate = _transcript_errrec_gate()
    mode = gate.get("mode") or MINER_ERRREC_DEDUP
    ledger = set(emitted_keys)
    out = []
    for sid, records, _settled in scanned:
        err_sig = {}          # (tool, target) -> first error signature seen
        recovered = set()     # (tool, target) already paired (emit once)
        for r in records:
            if not isinstance(r, dict):
                continue
            tool = str(r.get("tool") or "")
            target = _transcript_tool_target(r)
            keyt = (tool, target)
            st = r.get("status")
            if _transcript_is_err_status(st):
                if target and keyt not in err_sig:
                    err_sig[keyt] = _transcript_err_signature(r)
            elif (_transcript_is_ok_status(st) and target
                  and keyt in err_sig and keyt not in recovered):
                recovered.add(keyt)
                text = _transcript_error_recovery_text(tool, target,
                                                       err_sig[keyt])
                # A tier-3 (kernel-path / gate-loosening) candidate is still
                # EMITTED so the downstream drain visibly REJECTS it, and is
                # still LEDGERED so it stays bounded — but under its OWN key
                # namespace, so a drain-rejected bait can never occupy (and thus
                # blind) the key a LEGITIMATE same tool/class/signature recovery
                # would derive. Exempting it from the ledger entirely would
                # reopen an ungated once-per-session flood on the kernel path
                # (adversarial-review rounds 1-2). Same tier-3 predicate the
                # drain uses, so classification stays consistent.
                tier3 = _pref_hits_kernel(text) or _pref_loosens_gate(text)
                if mode != "off":
                    fekey = _transcript_errrec_key(tool, target,
                                                   err_sig[keyt], mode, tier3)
                    if fekey in ledger:
                        continue      # F-E: mode already emitted (any session)
                    ledger.add(fekey)
                    emitted_keys.append(fekey)  # persistent, cross-run
                out.append(_transcript_build_record(
                    kdir, sid=sid, turn_id="",
                    text=text,
                    signal="error-recovery", cwd="", source_sessions=[sid]))
    return out


def _transcript_queue_fps(qpath):
    """Fingerprints already in the LIVE queue (any producer) — the dedup belt so
    a mined candidate matching an existing Stop-hook fp is suppressed."""
    fps = set()
    if not os.path.isfile(qpath):
        return fps
    try:
        with open(qpath, encoding="utf-8") as qf:
            for line in qf:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    fps.add(_prefer_cand_fp(rec.get("sid"), rec.get("turn_id"),
                                            rec.get("text")))
    except OSError:
        return set()
    return fps


def _transcript_mine(kdir, backfill=False, session_cap=None):
    """Compose the miner as a SECOND prefer-queue producer. The corpus READ +
    tabulation happens BEFORE taking the lock (F5); then, under the shared
    `qpath + '.lock'` (mirroring _prefer_queue_scan), the BRIEF critical section
    re-reads the AUTHORITATIVE state, folds recurrence + detects error-recovery,
    dedups every candidate's _prefer_cand_fp against BOTH the live queue AND the
    archive tail, appends new candidates with the identical record schema, and
    atomically persists the watermark + tally (emitted flags) + session sets — all
    under the ONE lock hold. Append happens BEFORE the state write (crash-safe: a
    crash re-mines and re-dedups, never double-emits). Best-effort + always returns
    an int; any fault returns 0 and leaves the queue + state untouched (Q8).

    Concurrency-safe WITHOUT an abort: the tally is set-based (idempotent re-fold),
    `emitted` fires once, offsets advance monotonically (max), and every append is
    fp-deduped — so even a re-read of overlapping bytes cannot double-emit."""
    if not kdir:
        return 0
    try:
        peek = _transcript_load_state(kdir)[0]     # offsets hint (seeds if absent)
        read = _transcript_mine_read(kdir, peek, backfill, session_cap)
    except Exception:
        return 0
    qpath = os.path.join(kdir, PREFER_QUEUE_REL)
    try:
        os.makedirs(os.path.dirname(qpath), exist_ok=True)
    except OSError:
        return 0
    import fcntl
    lockp = qpath + ".lock"
    try:
        lf = open(lockp, "a+")
    except OSError:
        return 0
    try:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
    except OSError:
        lf.close()
        return 0
    try:
        state = _transcript_load_state(kdir)[0]    # AUTHORITATIVE (sole writer)
        tally = state["tally"]
        emitted_keys = state["emitted_keys"]       # persistent emit-once ledger
        cands = _transcript_mine_recurrence(read["prompts"], tally, kdir,
                                            emitted_keys,
                                            _transcript_recurrence_gate())
        err_emitted = state["err_emitted_keys"]    # F-E cross-session ledger
        cands += _transcript_mine_error_recovery(read["scanned"],
                                                 read["metrics"], kdir,
                                                 err_emitted,
                                                 _transcript_errrec_gate())
        seen = _transcript_queue_fps(qpath)
        seen |= _prefer_archive_fingerprints(
            os.path.join(kdir, PREFER_QUEUE_ARCHIVE_REL))
        out_lines = []
        for rec in cands:
            fp = _prefer_cand_fp(rec.get("sid"), rec.get("turn_id"),
                                 rec.get("text"))
            if fp in seen:
                continue
            seen.add(fp)
            out_lines.append(json.dumps(rec, ensure_ascii=False))
        if out_lines:                              # APPEND before the state write
            with open(qpath, "a", encoding="utf-8") as qf:
                qf.write("\n".join(out_lines) + "\n")
        state["offsets"]["prompts"] = max(
            int(state["offsets"].get("prompts") or 0),
            int(read["new_offsets"]["prompts"]))
        state["offsets"]["metrics"] = max(
            int(state["offsets"].get("metrics") or 0),
            int(read["new_offsets"]["metrics"]))
        processed = set(state.get("processed_sessions") or [])
        pending = set(state.get("backfill_pending") or [])
        for sid, _recs, settled in read["scanned"]:
            if settled:                            # permanent skip once settled
                processed.add(sid)
                pending.discard(sid)
        state["processed_sessions"] = sorted(processed)
        state["backfill_pending"] = sorted(pending)
        _transcript_prune_tally(tally)
        _transcript_prune_emitted(emitted_keys)    # SAME lock + state write
        _transcript_prune_emitted(err_emitted)     # F-E ledger, same bound
        _transcript_write_state(kdir, state)
        return len(out_lines)
    except Exception:
        return 0
    finally:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lf.close()


_EXPORTS = [
    "_transcript_read_session",
    "_transcript_mine_read",
    "_transcript_build_record",
    "_transcript_mine_recurrence",
    "_transcript_mine_error_recovery",
    "_transcript_queue_fps",
    "_transcript_mine",
]

__all__ = list(_EXPORTS)
