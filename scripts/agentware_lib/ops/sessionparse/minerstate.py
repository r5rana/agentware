"""agentware_lib.ops.sessionparse.minerstate — miner CURSOR and caps (layer: ops).

Owns: the offline transcript miner's durable cursor and every bound it runs
under — the state file (MINER_STATE_REL) with its load/normalise/write cycle, the
Claude transcript root resolution, per-session settle detection, the truncation
helpers, and the caps (MINER_SESSION_CAP / MINER_TALLY_CAP / MINER_EMITTED_CAP /
MINER_SOURCE_SESSIONS_CAP / MINER_TRANSCRIPT_MAXLEN) plus the tally/emitted
pruners that enforce them.

WHY A CURSOR AND WHY CAPS
The miner is OFFLINE and incremental: it walks session transcripts the operator's
agent produced, and it must never re-mine what it has already emitted or the
prefer queue fills with duplicates. The cursor is that memory, and
`_transcript_session_settled` is what stops it mining a session that is still
being written — a half-written transcript would yield a truncated candidate that
looks final.

Every cap is a REFUSAL to grow without bound. State files that only ever append
are how a background job quietly consumes a machine, so the tally and emitted
sets are pruned on every write rather than trusted to stay small.

Does NOT own: the pure folds and the recurrence precision gate
(ops.sessionparse.minerfolds); reading a session (ops.sessionparse.sessionread);
the mining passes themselves (ops.sessionparse.minerscan); error-signature
derivation (ops.sessionparse.minererrors); the prefer queue the miner feeds
(ops.preferqueue).
Layer deps: ops -> core (config, dates) plus stdlib.
Fan-in: 2 at landing (regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith — byte-identical, comments and all.
"""

import json
import os
import time

import agentware_lib.core.config as config
from agentware_lib.core.config import _read_config_key, resolve_knowledge_dir
from agentware_lib.core.dates import _now_iso
from agentware_lib.ops.planauthor.planedit import _atomic_write
from agentware_lib.ops.sessionparse.minerfolds import _transcript_list_session_ids


# ============================================================================
# P2c (260713): the transcript miner — a SECOND, FULLY OFFLINE prefer-queue
# producer behind the Stop-hook regex producer above. It mines the operator's
# OWN corpus (logs/prompts.log, logs/sessions/*/live.jsonl, logs/metrics.jsonl)
# for two durable signals — RECURRENCE (the same ask across >=N distinct
# sessions) and ERROR-RECOVERY (a tool call that errored then succeeded) — and
# writes the IDENTICAL queue record schema so the producer-agnostic drain
# (_prefer_classify_drain) consumes mined candidates with ZERO rework and the
# EXISTING governance tiers govern them. Nothing mined ever auto-activates as a
# source=user preference (Q4). Extraction is FULLY DETERMINISTIC: normalized-
# text hashing + tabulation only — NO LLM, NO network, NO wall-clock in any
# mined decision (INV-1). The miner OWNS a per-corpus watermark + per-run caps
# (never re-walks all sessions unbounded) and a bounded persistent recurrence
# tally so cross-window recurrence is never lost. Best-effort (Q8): any fault
# leaves the queue + state untouched and returns 0.
MINER_STATE_REL = "logs/steering/.transcript-miner.state.json"  # miner's OWN state


PREFER_QUEUE_MINER_PRODUCER = "transcript-miner"          # never the stop-hook value


# RETIRED (260719-prefer-precision-and-reject; operator-decided 2026-07-17,
# decide-miner-recurrence-arm-retired-keep-error-recovery): the recurrence arm
# emits NOTHING to the prefer-queue. Measured 0 true positives across 1142 prompts —
# the exact-match `_transcript_norm_key` structurally cannot detect paraphrased
# preferences (humans reword). Error-recovery mining is a SEPARATE arm and is
# untouched. Reversible: set False to re-enable recurrence emission.
MINER_RECURRENCE_RETIRED = True


MINER_SESSION_CAP = 50        # sessions walked per run (incremental + --backfill)


MINER_TALLY_CAP = 2000        # persistent recurrence-tally keys (LRU-pruned)


MINER_EMITTED_CAP = 4000      # persistent emitted-key ledger (emit-once, F-A)


MINER_SOURCE_SESSIONS_CAP = 10  # bound on a candidate's source_sessions list


# --- Claude failure-signal reconciliation (review F-B) -----------------------
# WHY this exists (spike, VERIFIED 2026-07-14 — supersedes the shipped premise):
# the PostToolUse hook does NOT merely read the wrong field, it NEVER RUNS for a
# failed Claude tool call. Three known-failed probes (`ls <missing>`, `false`,
# `exit 7`) produced ZERO live.jsonl records, while every successful call in the
# same session logged normally. So log-tool.sh's `.tool_response.success` read
# (:46/:81) is unreachable on the failure path and NO hook edit can capture it.
#
# The durable session transcript DOES record the signal: a tool_result block
# carries `is_error: true`, paired to its tool_use by `tool_use_id`. Measured on
# the live corpus: 977 is_error:true results across 496 transcripts, versus the
# 3 ERRs the writer captured corpus-wide (~326x recovery), with 506/509 knowledge
# -dir sessions having a transcript on disk — so this reads RETROACTIVELY too.
#
# The reconciler RECONSTRUCTS the record the hook would have written had it
# fired, byte-compatible with log-tool.sh's schema and truncation, so an
# error->ok pair derives one identical target from both sources. Read-only,
# in-memory, best-effort (Q8): any fault degrades to the live.jsonl-only view.
MINER_TRANSCRIPT_MAXLEN = 1500          # mirrors log-tool.sh MAXLEN (:67)


MINER_TRANSCRIPT_TRUNC_MARKER = " …[truncated]"   # mirrors log-tool.sh trunc()


MINER_TRANSCRIPT_ROOT_REL = (".claude", "projects")  # under $HOME (test-patchable)


def _transcript_is_ok_status(s):
    return str(s).strip().lower() == "ok"


def _transcript_file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _transcript_session_settled(sdir):
    """Deterministic 'session done being written' signal: main.jsonl present
    (Stop completed). A live.jsonl-only dir is an ACTIVE/orphan session -> NOT
    settled -> re-mined next cycle (fp-dedup guards double-emit), so an error/fix
    pair whose fix lands after this mine is never split across the watermark.

    NOTE (verified live, R-FAIL-08): the plan's original 'terminal event for its
    sid' signal is impossible — metrics.jsonl `terminal` events are FEATURE-keyed
    with no sid. main.jsonl presence is the deterministic per-session substitute."""
    return os.path.isfile(os.path.join(sdir, "main.jsonl"))


def _transcript_default_state():
    return {"offsets": {"prompts": 0, "metrics": 0},
            "processed_sessions": [], "backfill_pending": [], "tally": {},
            "emitted_keys": [], "err_emitted_keys": []}


def _transcript_norm_emitted(raw):
    """Normalize the persistent emitted-key ledger (F-A) to an ORDER-PRESERVING
    list of non-empty str keys, de-duplicated (first occurrence wins) and tail-
    capped at MINER_EMITTED_CAP. The list ORDER IS the LRU order (oldest first),
    mirroring the tally dict's ordering contract. A legacy state file written by
    a build without this key (the live state is exactly such a file) loads as []
    — back-compatible, never raises."""
    out, seen = [], set()
    if isinstance(raw, list):
        for k in raw:
            if not isinstance(k, str) or not k or k in seen:
                continue
            seen.add(k)
            out.append(k)
    return out[-MINER_EMITTED_CAP:]


def _transcript_load_state(kdir):
    """Load the miner's OWN state (DISTINCT from the Stop-hook cursor), or SEED a
    fresh one on first run: offsets -> current tail, existing sessions marked
    processed-for-incremental AND backfill-pending (so incremental mines only NEW
    material thereafter — Q3 — and an explicit `prefer mine --backfill` drains the
    history). Returns (state, fresh_bool); never raises. The session sets are
    pruned to on-disk sids so state stays bounded by the live corpus."""
    path = os.path.join(kdir, MINER_STATE_REL)
    on_disk = set(_transcript_list_session_ids(kdir))
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        if not isinstance(st, dict):
            raise ValueError("miner state is not an object")
        offs = st.get("offsets")
        if not isinstance(offs, dict):
            offs = {}
        st["offsets"] = {"prompts": int(offs.get("prompts") or 0),
                         "metrics": int(offs.get("metrics") or 0)}
        st["processed_sessions"] = sorted(
            set(st.get("processed_sessions") or []) & on_disk)
        st["backfill_pending"] = sorted(
            set(st.get("backfill_pending") or []) & on_disk)
        if not isinstance(st.get("tally"), dict):
            st["tally"] = {}
        st["emitted_keys"] = _transcript_norm_emitted(st.get("emitted_keys"))
        st["err_emitted_keys"] = _transcript_norm_emitted(
            st.get("err_emitted_keys"))   # F-E ledger; legacy state loads as []
        return st, False
    except (OSError, ValueError, TypeError):
        st = _transcript_default_state()
        st["offsets"]["prompts"] = _transcript_file_size(
            os.path.join(kdir, "logs", "prompts.log"))
        st["offsets"]["metrics"] = _transcript_file_size(
            os.path.join(kdir, "logs", "metrics.jsonl"))
        st["processed_sessions"] = sorted(on_disk)
        st["backfill_pending"] = sorted(on_disk)
        return st, True


def _transcript_write_state(kdir, state):
    """Persist the miner state atomically. NOT sort_keys'd: the tally dict order
    IS the LRU order the prune relies on (state is gitignored + machine-local, so
    byte-determinism is not required here — only mined candidates/reports are
    byte-stable, INV-1)."""
    _atomic_write(os.path.join(kdir, MINER_STATE_REL),
                  json.dumps(state, ensure_ascii=False))


def _transcript_claude_path(sid):
    """Locate the durable Claude transcript for <sid> ($HOME/.claude/projects/
    <proj>/<sid>.jsonl), or '' if absent. The sid IS the transcript filename, so
    the project dir is searched rather than guessed. HOME is read at CALL time so
    a hermetic test (patched HOME) is naturally isolated. Path-traversal guarded:
    an sid carrying a separator is refused outright, never joined."""
    if not isinstance(sid, str) or not sid or sid in (".", ".."):
        return ""
    if os.sep in sid or "/" in sid or "\\" in sid:
        return ""
    root = os.path.join(os.path.expanduser("~"), *MINER_TRANSCRIPT_ROOT_REL)
    try:
        for proj in sorted(os.listdir(root)):     # sorted => deterministic
            p = os.path.join(root, proj, sid + ".jsonl")
            if os.path.isfile(p):
                return p
    except OSError:
        return ""
    return ""


def _transcript_hook_trunc(s):
    """Reproduce log-tool.sh's trunc() EXACTLY (:67-75) so a reconstructed record
    is byte-identical to what the hook would have written. Parity matters for
    correctness, not just tidiness: a >MAXLEN command must truncate the SAME way
    on the reconstructed ERR record as on the hook-written ok record, or the two
    derive different targets via _transcript_tool_target and never pair."""
    if len(s) > MINER_TRANSCRIPT_MAXLEN:
        return s[:MINER_TRANSCRIPT_MAXLEN] + MINER_TRANSCRIPT_TRUNC_MARKER
    return s


def _transcript_resp_trunc(s):
    """Bound a tool RESPONSE for storage while PRESERVING its TAIL. The error
    signature of a deep failure lives at the END — a Python traceback's
    exception line, an `Exit code` detail — exactly where log-tool.sh's head-only
    trunc() destroys it, collapsing two genuinely-distinct deep failures onto ONE
    truncation-boundary fragment so the persistent F-E ledger suppresses all but
    the first forever (adversarial-review). Keep a head + the discriminating tail
    around the SAME marker. Deterministic; used ONLY for the response (which
    never feeds target derivation or pairing — those use the command input, which
    keeps byte-parity via _transcript_hook_trunc)."""
    if len(s) <= MINER_TRANSCRIPT_MAXLEN:
        return s
    room = MINER_TRANSCRIPT_MAXLEN - len(MINER_TRANSCRIPT_TRUNC_MARKER)
    if room <= 1:
        return s[:MINER_TRANSCRIPT_MAXLEN] + MINER_TRANSCRIPT_TRUNC_MARKER
    tail = room // 2
    head = room - tail
    return s[:head] + MINER_TRANSCRIPT_TRUNC_MARKER + s[-tail:]


def _transcript_norm_ts(ts):
    """Normalize a transcript timestamp ('2026-07-14T17:57:13.304Z') to the
    second-granularity form log-tool.sh writes ('2026-07-14T17:57:13Z'), so
    reconstructed and hook-written records order on ONE comparable scale."""
    s = str(ts or "")
    if "." in s and s.endswith("Z"):
        s = s.split(".", 1)[0] + "Z"
    return s


def _transcript_prune_tally(tally):
    """Bound the persistent tally (LRU): the dict's activity order (folded keys
    moved to the end) means the FRONT is least-recently-active — drop it. A pruned
    never-emitted key simply restarts its count next time (conservative, Q2)."""
    over = len(tally) - MINER_TALLY_CAP
    if over <= 0:
        return
    for k in list(tally)[:over]:
        del tally[k]


def _transcript_prune_emitted(emitted_keys):
    """Bound the persistent emitted-key ledger (LRU): append order IS the LRU
    order, so drop from the FRONT (oldest-emitted). Mutates in place. A key pruned
    out MAY re-emit once if it later recurs — the accepted bound (F-A)."""
    over = len(emitted_keys) - MINER_EMITTED_CAP
    if over > 0:
        del emitted_keys[:over]


_EXPORTS = [
    "MINER_STATE_REL",
    "PREFER_QUEUE_MINER_PRODUCER",
    "MINER_RECURRENCE_RETIRED",
    "MINER_SESSION_CAP",
    "MINER_TALLY_CAP",
    "MINER_EMITTED_CAP",
    "MINER_SOURCE_SESSIONS_CAP",
    "MINER_TRANSCRIPT_MAXLEN",
    "MINER_TRANSCRIPT_TRUNC_MARKER",
    "MINER_TRANSCRIPT_ROOT_REL",
    "_transcript_is_ok_status",
    "_transcript_file_size",
    "_transcript_session_settled",
    "_transcript_default_state",
    "_transcript_norm_emitted",
    "_transcript_load_state",
    "_transcript_write_state",
    "_transcript_claude_path",
    "_transcript_hook_trunc",
    "_transcript_resp_trunc",
    "_transcript_norm_ts",
    "_transcript_prune_tally",
    "_transcript_prune_emitted",
]

__all__ = list(_EXPORTS)
