"""agentware_lib.ops.preferqueue — the CANDIDATE QUEUE and its cursor (layer: ops).

Owns: the durable queue every preference candidate lands in before a human sees
it — the queue/cursor/archive paths and caps, the candidate fingerprint
(`_prefer_cand_fp`), the archive readers (`_prefer_archive_tail_records` /
`_prefer_archive_fingerprints`), the scanner (`_prefer_queue_scan` /
`_prefer_scan_queue`), the cursor seeder (`_prefer_seed_cursor` and its CLI).

WHY A QUEUE AND NOT DIRECT CAPTURE
Nothing the miner or the capture path produces is knowledge yet — it is a
PROPOSAL. The queue is the seam that keeps it that way: candidates accumulate,
a human approves or rejects, and only then does anything durable happen. That is
what makes an automated preference miner safe to run nightly.

The FINGERPRINT is what stops the queue becoming noise. A candidate the operator
already rejected must not reappear tomorrow, so fingerprints are checked against
the ARCHIVE as well as the live queue — rejection is remembered, not just
actioned. Without that the miner would re-propose the same rejected idea forever
and the operator would stop reading the queue, which is the real failure.

The CURSOR makes scanning incremental and bounded: a queue that is re-scanned from
the beginning every session gets slower forever, and a scan with no cap can stall
a session-start hook.

REDACTION AT THIS BOUNDARY
`_prefer_queue_scan` reads whatever the operator typed, and it used to store that
turn VERBATIM. Its sinks are NOT log-local: the classify drain writes the
candidate into `work/auto/pref-*/plan.md` and into that workorder's DIRECTORY
NAME (the slug is derived from the text), and `prefer capture --from-workorder`
promotes it into a durable `learnings/pref-*.md` entry. `logs/` is the only
gitignored side, so a preference-shaped prompt carrying a credential — "from now
on deploy with `-H 'Authorization: Bearer <token>'`" — put that credential into
the knowledge base's git history permanently, and a KB is the artifact agentware
is designed to SHARE. So the turn is redacted HERE, at the point it is first
read, and the cue match, the operator-residual gate, `matched_pattern`, the
fingerprint and the stored text all derive from that one redacted string. Fixing
it at the two sinks instead would leave every future sink one more thing to
remember, and this producer is precisely the one that forgot.

Does NOT own: the admission bar (ops.preferadmit); writing a capture
(ops.prefercapture); the `prefer mine` CLI handler, which needs the miner and so
lives with the other handlers (ops.prefercmd);
classification, approval and rejection (ops.preferclassify); the miner that feeds
this (ops.sessionparse.minerscan); the digest and CLI (ops.prefercmd).
Layer deps: ops -> core (config, dates) plus stdlib.
Fan-in: 3 at landing (regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith — byte-identical, comments and all.
"""

import hashlib
import json
import os
import time

import agentware_lib.core.config as config
from agentware_lib.core.config import require_knowledge_dir, resolve_knowledge_dir
from agentware_lib.core.dates import _now_iso
from agentware_lib.core.projectcontext import _canonical_project_key, _resolve_context
from agentware_lib.ops.planauthor.planedit import _atomic_write
from agentware_lib.ops.preferadmit import _pref_operator_text
from agentware_lib.ops.sessionparse.minerextract import _transcript_redact_secrets
from agentware_lib.ops.sessionparse.minerfolds import (
    _PREFER_QUEUE_RE,
    _parse_prompts_records,
)


# Layer-2 PRODUCER (Task 5): a CHEAP Stop-hook regex scan over origin=human turns
# in logs/prompts.log that appends INERT preference-shaped CANDIDATES to a
# producer-agnostic queue (logs/steering/prefer-queue.jsonl). Constraints:
#   * regex APPEND only — NO recall / learn / embedding / classify (offline-first
#     + the ~30s Stop budget). All heavy work is the OFFLINE dream drain (Task 7).
#   * records are DATA-ONLY: never auto-applied, never interpolated into a Verify
#     or any executable line (R-SEC-02 applies to our OWN logs, not just the web).
#   * atomic, flock-serialized append (concurrent sessions + the dream drain share
#     the file) + a per-session byte-offset WATERMARK so each human turn is queued
#     AT MOST ONCE and each Stop scans only NEW bytes, never re-walking the whole
#     cross-session prompts.log.
# The schema below is the SHARED CONTRACT: the P2c transcript miner drops in as a
# SECOND producer writing the same record shape, with zero drain rework (Q5).
PREFER_QUEUE_REL = "logs/steering/prefer-queue.jsonl"


PREFER_QUEUE_CURSOR_REL = "logs/steering/.prefer-queue.cursor.json"


PREFER_QUEUE_ARCHIVE_REL = "logs/steering/prefer-queue.archive.jsonl"


PREFER_QUEUE_PRODUCER = "stop-hook-regex"


# Producer/drain bounds (Task 6, 260713): keep the LIVE queue + the per-session
# watermark dict from growing without limit. Below the cap the paths are
# byte-identical to the unbounded behavior (the common small-queue case).
_PREFER_QUEUE_CAP = 500      # live-queue rotation threshold: terminal records past


                             # this are archived to a file the producer never reads
_PREFER_CURSOR_CAP = 200     # per-session watermark-dict cap (oldest sids pruned)


def _prefer_cand_fp(sid, turn_id, text):
    """Stable candidate fingerprint for the dedup safety net (belt to the
    watermark's braces): session + turn id + a hash of the normalized text."""
    import hashlib
    txt = (text if isinstance(text, str) else "").strip()   # tolerate non-str text
    h = hashlib.sha1(txt.encode("utf-8")).hexdigest()[:12]
    return "%s\x1f%s\x1f%s" % (sid or "", turn_id or "", h)


def _prefer_archive_tail_records(apath, max_bytes=262144):
    """The dict records in a BOUNDED tail of the drain's archive, in file order.

    Reads only the last `max_bytes` (discarding a leading partial line), so it
    stays bounded regardless of archive size; empty list on absent/error. Shared
    by the producer's dedup belt (`_prefer_archive_fingerprints`) and the
    `--from-workorder` resolver, so both see the SAME bounded window."""
    try:
        size = os.path.getsize(apath)
    except OSError:
        return []
    try:
        with open(apath, "rb") as af:
            if size > max_bytes:
                af.seek(size - max_bytes)
                af.readline()          # discard the (possibly partial) first line
            blob = af.read()
    except OSError:
        return []
    out = []
    for line in blob.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _prefer_archive_fingerprints(apath, max_bytes=262144):
    """Fingerprints of RECENTLY archived candidates — a BOUNDED tail read of the
    drain's archive so the producer's dedup belt also suppresses a candidate that
    was drained + archived OUT of the live queue. This preserves at-most-once
    across a watermark reset (log rotation) AFTER archival, not only within the
    live queue. Reads only the last `max_bytes` (discarding a leading partial
    line), so it stays bounded regardless of archive size; empty on absent/error."""
    return set(_prefer_cand_fp(rec.get("sid"), rec.get("turn_id"),
                               rec.get("text"))
               for rec in _prefer_archive_tail_records(apath, max_bytes))


def _queue_read_since_cursor(cpath, logp, sid):
    """Read the prompts log from this session's watermark to EOF.

    Returns (cursors, start, end, chunk). Every read is tolerant by design: a
    corrupt or missing cursor file, a non-dict payload and a non-integer offset
    all degrade to "start from 0" rather than raising, because a Stop hook that
    crashes on a malformed cursor would take down the session's own shutdown path.

    An offset PAST end-of-file means the log was rotated or truncated, so the scan
    restarts from the top rather than silently reading nothing — the failure that
    would otherwise make the miner go quietly blind after a rotation.
    """
    cursors = {}
    try:
        with open(cpath, encoding="utf-8") as cf:
            cursors = json.load(cf) or {}
    except (OSError, ValueError):
        cursors = {}
    if not isinstance(cursors, dict):
        cursors = {}
    start = 0
    try:
        start = int(cursors.get(sid, 0) or 0)
    except (TypeError, ValueError):
        start = 0
    fsize = os.path.getsize(logp)
    if start < 0 or start > fsize:
        start = 0   # log rotated/truncated -> rescan from the top
    with open(logp, "rb") as lf2:
        lf2.seek(start)
        raw = lf2.read()
    return cursors, start, start + len(raw), raw.decode("utf-8", "replace")


def _queue_seen_fingerprints(kdir, qpath):
    """Fingerprints that must NOT be re-queued: live queue plus recent archive.

    The archive half is what makes at-most-once hold ACROSS a watermark reset. A
    log rotation resets the cursor, so without it a candidate that was already
    drained and archived would be re-queued and re-shown to the operator — and a
    queue that resurfaces disposed items is one the operator stops reading.
    """
    seen = set()
    if os.path.isfile(qpath):
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
                    if not isinstance(rec, dict):
                        continue
                    seen.add(_prefer_cand_fp(
                        rec.get("sid"), rec.get("turn_id"), rec.get("text")))
        except OSError:
            seen = set()
    seen |= _prefer_archive_fingerprints(
        os.path.join(kdir, PREFER_QUEUE_ARCHIVE_REL))
    return seen


def _prefer_queue_scan(kdir, sid, cwd, prompts_path=None):
    """Layer-2 producer core. Append inert preference-shaped candidates for NEW
    origin=human turns of session `sid` from prompts.log to the queue. Returns the
    number of candidates appended. Best-effort and side-effect-safe: any error
    leaves the queue + cursor untouched and returns 0 (a Stop hook must never
    fail because of steering capture)."""
    if not kdir or not sid:
        return 0
    logp = prompts_path or os.path.join(kdir, "logs", "prompts.log")
    if not os.path.isfile(logp):
        return 0
    qpath = os.path.join(kdir, PREFER_QUEUE_REL)
    cpath = os.path.join(kdir, PREFER_QUEUE_CURSOR_REL)
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
        cursors, start, end, chunk = _queue_read_since_cursor(cpath, logp, sid)
        seen = _queue_seen_fingerprints(kdir, qpath)

        out_lines = []
        for hdr, bodytext in _parse_prompts_records(chunk):
            if hdr.get("sid") != sid:
                continue
            if hdr.get("origin") != "human":
                continue
            body = (bodytext or "").strip()
            if not body:
                continue
            # REDACT FIRST — see REDACTION AT THIS BOUNDARY in the module
            # docstring for why this is here and not at the sinks. Everything
            # below derives from the ONE redacted string.
            body = _transcript_redact_secrets(body)
            m = _PREFER_QUEUE_RE.search(body)
            if not m:
                continue
            # Operator-turn gate (260719): the cue must ALSO survive in the operator-
            # authored residual (blockquote quote-backs stripped), so a keyword only
            # inside quoted-back agent text never enqueues. STRICTLY tighten-only (the
            # original match above is unchanged); the full `body` is stored verbatim.
            authored = _pref_operator_text(body)
            if not authored or not _PREFER_QUEUE_RE.search(authored):
                continue
            turn_id = hdr.get("turn") or ""
            fp = _prefer_cand_fp(sid, turn_id, body)
            if fp in seen:
                continue
            seen.add(fp)
            cwd_val = hdr.get("cwd") or cwd or ""
            sig = None
            if cwd_val:
                try:
                    sig = _resolve_context(cwd_val).get("project_name") or None
                except Exception:
                    sig = None
            proj = _canonical_project_key(kdir, sig)
            rec = {
                "ts": _now_iso(),
                "sid": sid,
                "cwd": cwd_val,
                "project": proj,          # canonical KB slug (Task 1) or None
                "turn_id": turn_id,
                "text": body,             # INERT data — never executed/interpolated
                "matched_pattern": m.group(0).lower(),
                "status": "queued",
                "producer": PREFER_QUEUE_PRODUCER,
            }
            out_lines.append(json.dumps(rec, ensure_ascii=False))

        if out_lines:
            with open(qpath, "a", encoding="utf-8") as qf:
                qf.write("\n".join(out_lines) + "\n")
        # Advance the watermark to EOF unconditionally so scanned-but-unmatched
        # turns are never re-walked (idempotent across consecutive Stops). Re-insert
        # `sid` at the END so dict order tracks LAST-ACTIVITY (LRU): a plain
        # `cursors[sid] = end` update does NOT reorder, so a still-ACTIVE but
        # early-first-seen session could otherwise be evicted by the prune below and
        # then, on a re-seed to EOF, skip its own logged-but-unscanned turns.
        cursors.pop(sid, None)
        cursors[sid] = end
        # Bound the per-session watermark dict (Task 6): prune the LEAST-RECENTLY-
        # ACTIVE sessions past the cap. With the LRU re-insert above, eviction hits
        # only genuinely-idle sessions (never the current sid, always kept).
        if len(cursors) > _PREFER_CURSOR_CAP:
            keep = list(cursors)[-_PREFER_CURSOR_CAP:]
            if sid not in keep:
                keep = keep[1:] + [sid]
            cursors = {k: cursors[k] for k in keep}
        _atomic_write(cpath, json.dumps(cursors, ensure_ascii=False))
        return len(out_lines)
    except Exception:
        return 0
    finally:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lf.close()


def _prefer_scan_queue(args):
    """CLI wrapper for the layer-2 producer (invoked by the Stop hook). Prints a
    one-line summary; ALWAYS exits 0 (the Stop hook must not fail on capture)."""
    kdir = resolve_knowledge_dir()
    if not kdir or not os.path.isdir(kdir):
        return 0
    n = _prefer_queue_scan(
        kdir,
        getattr(args, "sid", None) or "",
        getattr(args, "cwd", None) or "",
        prompts_path=getattr(args, "prompts", None) or None)
    print("prefer scan-queue: %d candidate(s) queued" % n)
    return 0


def _prefer_seed_cursor(kdir, sid, prompts_path=None):
    """Seed a NEW session's watermark to the CURRENT prompts.log size (Task 6,
    audit LOW): called from session-start.sh so the FIRST Stop of a session scans
    only bytes written DURING this session, not the entire cross-session
    prompts.log from offset 0 (the part that contradicts the never-re-walking
    contract and grows with an unrotated log). At SessionStart none of this
    session's turns are logged yet, so the current EOF is a correct lower bound.

    Idempotent: a no-op when `sid` already has a cursor, so a resume never clobbers
    an advanced watermark. Best-effort + flock-serialized (shares the producer's
    lock so a concurrent Stop cannot race the cursor write). Returns True iff it
    seeded; never raises (session start must never fail on capture)."""
    if not kdir or not sid:
        return False
    logp = prompts_path or os.path.join(kdir, "logs", "prompts.log")
    qpath = os.path.join(kdir, PREFER_QUEUE_REL)
    cpath = os.path.join(kdir, PREFER_QUEUE_CURSOR_REL)
    try:
        os.makedirs(os.path.dirname(qpath), exist_ok=True)
    except OSError:
        return False
    import fcntl
    lockp = qpath + ".lock"
    try:
        lf = open(lockp, "a+")
    except OSError:
        return False
    try:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
    except OSError:
        lf.close()
        return False
    try:
        cursors = {}
        try:
            with open(cpath, encoding="utf-8") as cf:
                cursors = json.load(cf) or {}
        except (OSError, ValueError):
            cursors = {}
        if not isinstance(cursors, dict):
            cursors = {}
        if sid in cursors:
            return False        # idempotent: never clobber an advanced watermark
        try:
            fsize = os.path.getsize(logp)
        except OSError:
            fsize = 0
        cursors[sid] = fsize
        _atomic_write(cpath, json.dumps(cursors, ensure_ascii=False))
        return True
    except Exception:
        return False
    finally:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lf.close()


def _prefer_seed_cursor_cli(args):
    """CLI wrapper for the session-start cursor seed (invoked by session-start.sh).
    Emits NOTHING to stdout — session-start.sh's stdout IS the hook payload — and
    ALWAYS exits 0 (session start must never fail on capture)."""
    kdir = resolve_knowledge_dir()
    if not kdir or not os.path.isdir(kdir):
        return 0
    _prefer_seed_cursor(kdir, getattr(args, "sid", None) or "",
                        prompts_path=getattr(args, "prompts", None) or None)
    return 0


_EXPORTS = [
    "PREFER_QUEUE_REL",
    "PREFER_QUEUE_CURSOR_REL",
    "PREFER_QUEUE_ARCHIVE_REL",
    "PREFER_QUEUE_PRODUCER",
    "_PREFER_QUEUE_CAP",
    "_PREFER_CURSOR_CAP",
    "_prefer_cand_fp",
    "_prefer_archive_tail_records",
    "_prefer_archive_fingerprints",
    "_prefer_queue_scan",
    "_prefer_scan_queue",
    "_prefer_seed_cursor",
    "_prefer_seed_cursor_cli",
]

__all__ = list(_EXPORTS)
