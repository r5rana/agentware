"""agentware_lib.ops.sessionparse.minerscan — the mining PASSES (layer: ops).

Owns: the passes that turn a read window into candidates, and the orchestrator
that persists them — the record builder (`_transcript_build_record`), the
recurrence pass (`_transcript_mine_recurrence`, with `_recurrence_fold` and
`_recurrence_latest`), the error-recovery pass
(`_transcript_mine_error_recovery`, with the `_errrec_pairs` detector), the
queue-fingerprint belt (`_transcript_queue_fps`, `_mine_new_lines`), the state
advance (`_mine_advance_state`) and the orchestrator (`_transcript_mine`).

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

Does NOT own: the bounded corpus READ done outside the lock — the byte-offset
reader, the two log readers, the session selection and `_transcript_mine_read`
(ops.sessionparse.minerread); the cursor/caps (minerstate); record extraction and
redaction (minerextract); error signatures (minererrors); the recurrence precision
gate (minerfolds); the queue, its lock and its reader (ops.preferqueue).
Layer deps: ops -> core + same-layer sessionparse siblings (minerread,
minererrors, minerextract, minerfolds, minerstate), ops.preferqueue,
ops.premisecheck, ops.prefergate and ops.planauthor.plankernelpath.
Fan-in: 1 at landing (regen: python3 tests/_manifest_gen.py --write).
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
    _prefer_queue_lock,
    _prefer_queue_records,
)
from agentware_lib.ops.premisecheck import (
    _transcript_recurrence_gate,
    _transcript_recurrence_relevant,
)
from agentware_lib.ops.sessionparse.minererrors import (
    MINER_ERRREC_DEDUP,
    _transcript_errrec_gate,
    _transcript_errrec_key,
    _transcript_err_signature,
    _transcript_error_recovery_text,
)
from agentware_lib.ops.sessionparse.minerread import (
    _transcript_mine_read,
    _transcript_read_session,
)
from agentware_lib.ops.sessionparse.minerextract import (
    _transcript_redact_secrets,
    _transcript_tool_target,
)
from agentware_lib.ops.sessionparse.minerfolds import (
    MINER_RECURRENCE_N,
    _transcript_is_err_status,
    _transcript_norm_key,
)
from agentware_lib.ops.sessionparse.minerstate import (
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
    _transcript_write_state,
)


def _transcript_build_record(kdir, sid, turn_id, text, signal, cwd="",
                             recurrence_count=None, source_sessions=None):
    """Build a queue record with the IDENTICAL shared schema as the Stop-hook
    producer (ts/sid/cwd/project/turn_id/text/matched_pattern/status/producer)
    plus ADDITIVE fields (signal, recurrence_count, source_sessions). `text` is
    stored as inert data (R-SEC-02) — never interpolated/executed — and REDACTED
    first, at the boundary where a raw transcript record becomes a persisted
    record. The recurrence arm passes a raw prompt body straight through here, so
    without this the miner would reproduce the Stop-hook producer's leak the
    moment MINER_RECURRENCE_RETIRED is lifted: this record's `text` reaches
    `work/auto/pref-*/plan.md` and a durable preferences entry, both COMMITTED.
    `_transcript_redact_secrets` was already imported into this module and never
    called, which is what that gap looked like from the outside. Redaction is
    idempotent, so the error-recovery arm — whose text is composed from
    already-redacted parts — is unaffected. `ts` is append-time (_now_iso, like
    the producer) — NOT a mined clock-in-a-decision; determinism (INV-1) is
    scoped to the fp / text / tally, never ts."""
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
        "text": _transcript_redact_secrets(text) if isinstance(text, str) else "",
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


def _recurrence_fold(prompts, tally):
    """Fold this window's bodies INTO the persistent tally and return the keys
    touched, in first-touch order.

    Pure tabulation: each body normalises to a deterministic key whose value
    carries the DISTINCT-session set (a body asked twice in ONE session counts
    once, F4) and the most-recent occurrence. Set-based, so re-folding the same
    window is idempotent — the property that lets the whole mine be crash-safe
    without an abort. Mutates `tally` in place and LRU-moves each touched key to
    the end. Emits nothing; emission is the caller's, and separating them is what
    lets the fold keep running while emission is RETIRED.
    """
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
    return touched


def _recurrence_latest(entry):
    """A tally entry's most-recent occurrence as the record fields, every one of
    them coerced to its empty default.

    A tally entry can be legacy, hand-edited or half-written (the `latest` sub-dict
    is the newest of the three fields), and `_transcript_build_record` builds a
    record that is APPENDED to a shared, producer-agnostic queue — so a None
    reaching it becomes a null in another producer's reader. Coercing all of them
    in one place is what makes "the record schema is always complete" checkable.
    """
    latest = entry.get("latest") or {}
    return {"sid": latest.get("sid") or "",
            "turn_id": latest.get("turn_id") or "",
            "text": latest.get("text") or "",
            "cwd": latest.get("cwd") or "",
            "source_sessions": entry.get("sessions") or []}


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
    touched = _recurrence_fold(prompts, tally)
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
        latest = _recurrence_latest(entry)
        count = len(latest["source_sessions"])
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
            out.append(_transcript_build_record(
                kdir, sid=latest["sid"], turn_id=latest["turn_id"],
                text=latest["text"], signal="recurrence", cwd=latest["cwd"],
                recurrence_count=count,
                source_sessions=latest["source_sessions"]))
    return out


def _errrec_pairs(records):
    """The hit-and-fixed pairs in ONE session's timeline, as
    `[(tool, target, error_signature)]` in recovery order.

    DETECTION only, with no ledger and no emission: a tool call with an ERR status
    FOLLOWED by a later ok call of the SAME tool AND primary target. A BARE error
    with no later success yields nothing (conservative), an error with no target
    is unpairable and ignored, and only the FIRST error signature per pair is
    kept — a target that failed twice before recovering is one recovery, and its
    signature is the failure that started it.
    """
    err_sig = {}          # (tool, target) -> first error signature seen
    recovered = set()     # (tool, target) already paired (emit once)
    pairs = []
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
            pairs.append((tool, target, err_sig[keyt]))
    return pairs


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
    consults nor grows the ledger.

    Detection is `_errrec_pairs`; what is left here is the LEDGER, which is the
    only part that spans sessions and the only part a mode can change."""
    if emitted_keys is None:
        emitted_keys = []
    if gate is None:
        gate = _transcript_errrec_gate()
    mode = gate.get("mode") or MINER_ERRREC_DEDUP
    ledger = set(emitted_keys)
    out = []
    for sid, records, _settled in scanned:
        for tool, target, signature in _errrec_pairs(records):
            text = _transcript_error_recovery_text(tool, target, signature)
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
                fekey = _transcript_errrec_key(tool, target, signature, mode,
                                               tier3)
                if fekey in ledger:
                    continue          # F-E: mode already emitted (any session)
                ledger.add(fekey)
                emitted_keys.append(fekey)  # persistent, cross-run
            out.append(_transcript_build_record(
                kdir, sid=sid, turn_id="",
                text=text,
                signal="error-recovery", cwd="", source_sessions=[sid]))
    return out


def _transcript_queue_fps(qpath):
    """Fingerprints already in the LIVE queue (any producer) — the dedup belt so
    a mined candidate matching an existing Stop-hook fp is suppressed.

    Reads through the queue's OWN reader (`_prefer_queue_records`) rather than a
    seventh hand-rolled JSONL loop, so a change to what the queue considers a
    record reaches the miner's dedup belt too. The reader is strict about ACCESS
    by design; here an unreadable queue degrades to an EMPTY belt, because the
    caller's next move is fp-dedup and a mine that cannot read the queue must
    still be able to run — the append is idempotent under the archive fp set.
    """
    try:
        records = _prefer_queue_records(qpath)
    except OSError:
        return set()
    return {_prefer_cand_fp(rec.get("sid"), rec.get("turn_id"), rec.get("text"))
            for rec in records}


def _mine_new_lines(cands, seen):
    """The JSONL lines for the candidates whose fp is not already in `seen`.

    Mutates `seen` so a duplicate WITHIN one batch is suppressed too, not only a
    duplicate against the queue and the archive: the two detectors run over the
    same window and can independently derive the same (sid, turn, text).
    """
    out_lines = []
    for rec in cands:
        fp = _prefer_cand_fp(rec.get("sid"), rec.get("turn_id"),
                             rec.get("text"))
        if fp in seen:
            continue
        seen.add(fp)
        out_lines.append(json.dumps(rec, ensure_ascii=False))
    return out_lines


def _mine_advance_state(state, read):
    """Advance the authoritative state by what this window actually read.

    Offsets advance MONOTONICALLY (`max`), never by assignment: two mines racing
    on overlapping bytes must not be able to rewind the watermark, and a re-read
    is safe because every append is fp-deduped. A session is marked processed only
    once it has SETTLED (its Stop hook wrote main.jsonl) — an unsettled session is
    left pending so a later window re-scans it, which is why an in-flight session's
    recovery is never missed by being seen too early.
    """
    for key in ("prompts", "metrics"):
        state["offsets"][key] = max(int(state["offsets"].get(key) or 0),
                                    int(read["new_offsets"][key]))
    processed = set(state.get("processed_sessions") or [])
    pending = set(state.get("backfill_pending") or [])
    for sid, _recs, settled in read["scanned"]:
        if settled:                            # permanent skip once settled
            processed.add(sid)
            pending.discard(sid)
    state["processed_sessions"] = sorted(processed)
    state["backfill_pending"] = sorted(pending)


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
    fp-deduped — so even a re-read of overlapping bytes cannot double-emit.

    The lock is the queue's OWN `_prefer_queue_lock`, shared with the Stop-hook
    producer and the four disposition verbs: a miner holding a DIFFERENT lock on
    the same file would be no lock at all. It yields None when the lock cannot be
    taken, which is this caller's "queued nothing" — an offline maintenance step
    never raises into the cycle."""
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
    with _prefer_queue_lock(qpath) as lf:
        if lf is None:
            return 0
        try:
            state = _transcript_load_state(kdir)[0]  # AUTHORITATIVE (sole writer)
            tally = state["tally"]
            emitted_keys = state["emitted_keys"]     # persistent emit-once ledger
            cands = _transcript_mine_recurrence(read["prompts"], tally, kdir,
                                                emitted_keys,
                                                _transcript_recurrence_gate())
            err_emitted = state["err_emitted_keys"]  # F-E cross-session ledger
            cands += _transcript_mine_error_recovery(read["scanned"],
                                                     read["metrics"], kdir,
                                                     err_emitted,
                                                     _transcript_errrec_gate())
            seen = _transcript_queue_fps(qpath)
            seen |= _prefer_archive_fingerprints(
                os.path.join(kdir, PREFER_QUEUE_ARCHIVE_REL))
            out_lines = _mine_new_lines(cands, seen)
            if out_lines:                          # APPEND before the state write
                with open(qpath, "a", encoding="utf-8") as qf:
                    qf.write("\n".join(out_lines) + "\n")
            _mine_advance_state(state, read)
            _transcript_prune_tally(tally)
            _transcript_prune_emitted(emitted_keys)  # SAME lock + state write
            _transcript_prune_emitted(err_emitted)   # F-E ledger, same bound
            _transcript_write_state(kdir, state)
            return len(out_lines)
        except Exception:
            return 0


_EXPORTS = [
    "_transcript_build_record",
    "_recurrence_fold",
    "_recurrence_latest",
    "_transcript_mine_recurrence",
    "_errrec_pairs",
    "_transcript_mine_error_recovery",
    "_transcript_queue_fps",
    "_mine_new_lines",
    "_mine_advance_state",
    "_transcript_mine",
]

__all__ = list(_EXPORTS)
