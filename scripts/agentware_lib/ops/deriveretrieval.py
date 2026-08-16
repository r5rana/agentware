"""agentware_lib.ops.deriveretrieval -- the RETRIEVAL slice of the derive family (layer: ops).

Owns: `derive_retrieval`, the two id/path recognisers it reads (`_RET_ID_RE`,
`_RET_PATH_RE`) and the tolerant `live.jsonl` reader they walk (`_iter_live_records`) -- the deriver that reads a session transcript and reports what
the agent actually RETRIEVED: which knowledge ids and which paths it opened.

WHY ITS OWN MODULE RATHER THAN INSIDE `ops.derive`
Purely a ceiling: `ops.derive` is already 587 of its 600 lines, so this would not
fit, and the FILE cap is satisfied outright in this tree (no file_over_cap row is
accepted anywhere). The seam is real enough to stand on its own regardless -- the
other derivers read PLAN and WORKLOG structure, this one reads a transcript and
recognises retrieval events, which is a different input and a different grammar.

Extracted at task 44 because `checkup` consumes it: the self-exam reports on
retrieval behaviour, so the deriver had to have a home before checkup could move.

Does NOT own: the other derive_* reporters (ops.derive); the retrieval ENGINE
itself -- ranking, recall, the BM25 index (agentware_lib.retrieval.*); the checkup
that consumes this (ops.checkup).
Layer deps: ops -> knowledge.index (load_index) plus stdlib. The record reader is
BEST-EFFORT by design: a malformed line in a live transcript is skipped rather than
failing the whole derivation, because a self-exam that crashes on one bad line
reports nothing at all.
Fan-in: 1 at landing (ops.checkup); regen: python3 tests/_manifest_gen.py --write.

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all.
"""

import glob
import json
import os
import re

from agentware_lib.knowledge.index import load_index


# Retrieval-utilization derivation (260710-learning-repair-p1 task 9). Read-only
# (INV-2): parses the per-tool-call log (logs/sessions/*/live.jsonl written by
# scripts/hooks/log-tool.sh) to measure whether recall is actually USED — the
# missing utilization signal (<10% of returned paths were ever read).
_RET_ID_RE = re.compile(
    r"\b((?:learn|decide|config|project|ref|skill)-[a-z0-9][a-z0-9-]*)")


_RET_PATH_RE = re.compile(
    r"\b((?:learnings|decisions|projects|configurations|references|skills|"
    r"prompts)/[A-Za-z0-9._/-]+\.md)")


def _iter_live_records(path):
    """Yield parsed JSON objects from a live.jsonl (best-effort; skip bad lines)."""
    try:
        f = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                yield rec


def _record_text(rec, field):
    """A record field as TEXT. Recorded input/response may be a str or a nested
    object depending on the tool, so non-strings are JSON-dumped rather than
    coerced — a `str()` of a dict would mangle the very ids being extracted."""
    val = rec.get(field) or ""
    return val if isinstance(val, str) else json.dumps(val)


def _is_recall_call(tool, agent, inp):
    """True for a REAL recall invocation by the main agent.

    Excludes calls that only appear inside a `--verify` assertion: a plan's own
    verify command frequently contains `agentware recall`, and counting those
    would report retrieval the agent never actually performed — inflating exactly
    the metric this deriver exists to measure honestly.
    """
    return (tool == "Bash" and "agentware recall" in inp and agent == "main"
            and "--verify" not in inp and "verify:" not in inp.lower())


def _scan_session(live):
    """Walk one session's live.jsonl -> (recall_calls, ids, returned_paths, read_paths).

    Ids and paths are regex-extracted from the (possibly TRUNCATED) recorded
    response, so every count here is a faithful LOWER BOUND rather than a claim of
    completeness.
    """
    recall_calls = 0
    returned_ids, returned_paths, read_paths = set(), set(), set()
    for rec in _iter_live_records(live):
        tool = rec.get("tool") or ""
        agent = rec.get("agent") or "main"
        inp = _record_text(rec, "input")
        resp = _record_text(rec, "response")
        if _is_recall_call(tool, agent, inp):
            recall_calls += 1
            returned_ids.update(_RET_ID_RE.findall(resp))
            returned_paths.update(_RET_PATH_RE.findall(resp))
        elif tool in ("Read", "Bash"):
            read_paths.update(_RET_PATH_RE.findall(inp))
    return recall_calls, returned_ids, returned_paths, read_paths


def derive_retrieval(kdir, session=None):
    """Per-session retrieval utilization over logs/sessions/*/live.jsonl.

    For each session: was `recall` invoked by the MAIN agent (EXCLUDING calls
    inside a plan/task `--verify` string and subagent-attributed calls), which
    entry ids it returned, and how many of the returned paths were later Read
    (read-through). Plus overall corpus coverage (distinct learning ids ever
    surfaced / learnings corpus size). Deterministic; ids/paths are regex-
    extracted from the (possibly truncated) recorded response, so counts are a
    faithful lower bound. Read-only (INV-2)."""
    data, _err = load_index(kdir)
    corpus_learnings = {
        e["id"] for e in (data.get("entries") or [])
        if isinstance(e, dict) and e.get("category") == "learnings" and e.get("id")}
    surfaced = set()
    out_sessions = []
    root = os.path.join(kdir, "logs", "sessions")
    for sdir in sorted(glob.glob(os.path.join(root, "*"))):
        sid = os.path.basename(sdir)
        if session and sid != session:
            continue
        live = os.path.join(sdir, "live.jsonl")
        if not os.path.isfile(live):
            continue
        recall_calls, returned_ids, returned_paths, read_paths = _scan_session(live)
        surfaced |= returned_ids
        read_hit = len(returned_paths & read_paths)
        out_sessions.append({
            "session": sid,
            "recall_invoked": recall_calls > 0,
            "recall_calls": recall_calls,
            "returned_ids": sorted(returned_ids),
            "read_through": {
                "returned_paths": len(returned_paths),
                "read": read_hit,
                "ratio": (round(read_hit / len(returned_paths), 4)
                          if returned_paths else None)},
        })
    corpus_size = len(corpus_learnings)
    covered = len(surfaced & corpus_learnings)
    return {
        "sessions_sampled": len(out_sessions),
        "corpus_coverage": {
            "learning_ids_surfaced": covered,
            "corpus_size": corpus_size,
            "ratio": round(covered / corpus_size, 4) if corpus_size else None},
        "sessions": out_sessions,
    }


_EXPORTS = [
    "_RET_ID_RE",
    "_RET_PATH_RE",
    "_iter_live_records",
    "derive_retrieval",
]

__all__ = list(_EXPORTS)
