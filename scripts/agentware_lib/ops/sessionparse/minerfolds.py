"""agentware_lib.ops.sessionparse.minerfolds — the PURE transcript folds (layer: ops).

Owns: the deterministic, offline folds the transcript miner emits with, and the
recurrence PRECISION GATE that decides whether a recurring key may emit — the
`prompts.log` record parser (`_PROMPTS_HEADER_RE` / `_parse_prompts_records`), the
near-duplicate key normaliser (`_transcript_norm_key`), the ack test and its
stopword vocabulary (`_transcript_is_ack` / `MINER_RECURRENCE_STOPWORDS`), the
producer cue (`_PREFER_QUEUE_RE`), the gate resolver and its relevance predicate
(`_transcript_recurrence_gate` / `_transcript_recurrence_relevant`) with the three
`MINER_RECURRENCE_*` knobs and their strict parsers, and the `live.jsonl` readers
(`_transcript_list_session_ids` / `_transcript_read_live` / `_transcript_is_err_status`).

Does NOT own: the `prefer` VERB, the candidate ledger, the classify drain or any
WRITER over miner state — those are the miner's stateful half and are ported by
plan task 45. Nothing here writes: every symbol is a pure function of its inputs
plus read-only config resolution, which is exactly why it can land ahead of the
verb that mines with it.

WHY THESE FOLDS LAND WITH THE `plan` PORT (task 39) AND NOT WITH THE MINER (45)
------------------------------------------------------------------------------
`plan verify-premises` is one of the four subverbs task 39 ports, and its closed
check grammar includes `corpus-metric <recurrence-keys-at-n|claude-error-records>`.
The evaluator for that kind (`ops.planpremises._premise_corpus_metric_value`)
computes its answer by re-running THESE folds over the live corpus — deliberately
the same folds the miner emits with, so a premise grounded against "the miner
would flag N recurrences" cannot disagree with what the miner actually does.

That left three options and none of them is free. Leaving the evaluator in the
monolith would ship a ported verb that silently answers "uncheckable" for one of
its four check kinds — the exact silent-loss class this migration exists to
prevent, and invisible to every surface gate because the flag, the verb and the
manifest row are all unchanged. Reaching back into `agentware_legacy` from here
would invert the layer graph and would have to be undone at task 56 anyway.
Moving them is the third, and the transitive closure is what makes it the right
one: it is 20 symbols and ~240 lines that bottom out in `re`, `json`, `os` and
`core.config._read_config_key` — a bounded, PURE leaf, not a foothold into the
miner's stateful half. Task 45 therefore inherits these rather than re-porting
them, and imports this module instead of redefining the gate a second time.

The monolith re-imports every name below (see the `agentware_legacy` import
block), so the miner's existing call sites keep resolving unchanged until task 45
moves the verb itself. ONE definition, two callers — never a copy.

Layer deps: ops.sessionparse.minerfolds -> core.config only (`_read_config_key`,
for the three operator-tunable gate knobs). Imports nothing else from the lib.
Determinism: no clock, no network, no randomness (INV-1); read-only (INV-2). The
gate is resolved ONCE per run and threaded, so a concurrent config edit can never
make one window's emission depend on when a key happened to be folded.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import os
import re

from agentware_lib.core.config import _read_config_key


def _review_truthy_env(raw):
    """Interpret an env flag as a boolean the shell way."""
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


# The layer-2 producer cue — DELIBERATELY broader/cheaper than the capture-time
# `_PREF_CUE_RE`: a human turn matching any of these phrases is a CANDIDATE for
# offline classification, never an auto-capture. Case-insensitive, anchored.
_PREFER_QUEUE_RE = re.compile(
    r"\b(always|never|from now on|going forward|prefer|"
    r"do not ever|make sure to|by default)\b", re.IGNORECASE)


# prompts.log record header (see scripts/hooks/log-prompt.sh):
#   [ts] [session SID] [cwd CWD] [origin=O] [turn=T]
_PROMPTS_HEADER_RE = re.compile(
    r"^\[(?P<ts>[^\]]*)\] \[session (?P<sid>[^\]]*)\] \[cwd (?P<cwd>.*)\] "
    r"\[origin=(?P<origin>[^\]]*)\] \[turn=(?P<turn>[^\]]*)\]\s*$")


def _parse_prompts_records(text):
    """Yield (header_dict, body_text) records from a prompts.log chunk.

    A record STARTS at a header line; its body is every following line up to (not
    including) the NEXT header line — robust to blank lines INSIDE a prompt body
    (the log writes `prompt\n\n`, but a prompt may itself contain blank lines).
    """
    cur = None
    body = []
    for ln in (text or "").split("\n"):
        m = _PROMPTS_HEADER_RE.match(ln)
        if m:
            if cur is not None:
                yield cur, "\n".join(body).strip("\n")
            cur = m.groupdict()
            body = []
        elif cur is not None:
            body.append(ln)
    if cur is not None:
        yield cur, "\n".join(body).strip("\n")


MINER_RECURRENCE_N = 2        # distinct-session near-dup asks that flag a recurrence


# --- Recurrence precision gate (review F-C) ---------------------------------
# The tally COUNTS every occurrence; this gate only decides whether a key that
# reached N distinct sessions may EMIT. Emission-side only, so a re-tune (Task 6)
# re-considers every already-counted key — a gated key is never flagged emitted
# and never enters the emitted_keys ledger.
#
# Two complementary sub-tests, both deterministic (no clock, no network, INV-1):
#   (a) the normalized key is at least MINER_RECURRENCE_MIN_LEN chars, AND
#   (b) it carries a preference CUE (_PREFER_QUEUE_RE) OR is not a pure ACK
#       (every token in MINER_RECURRENCE_STOPWORDS).
# They catch different noise: (b) catches long polite acks ("continue please"),
# (a) catches short junk and typos that (b) cannot know ("oroceed thx").
#
# MIN_LEN default is EVIDENCE-BASED, measured on the live corpus (2026-07-14):
# the longest observed human ack is "continue please" (15 chars) while genuine
# preferences start ~18 and run p25=37 / median=116. 16 is the tightest bound
# that gates every observed ack yet still admits a short real preference such as
# "always pin versions" (19) — which this repo's own R-DEP-02 encodes as a rule.
# A higher bar (e.g. 24) is a silent FALSE NEGATIVE on such preferences, and
# precision-only tuning (Task 6) cannot detect a false negative — so the
# conservative default errs toward admitting. Task 6 tunes against the corpus.
MINER_RECURRENCE_MIN_LEN = 16   # min normalized-key chars to EMIT (0 = no gate)


MINER_RECURRENCE_CUE_REQUIRED = False  # True => (b) demands a cue, no ack escape


# Pure acks / continuations / politeness. A key is an ACK only when EVERY token
# is in this set, so a body mixing an ack with real content is NOT gated.
MINER_RECURRENCE_STOPWORDS = frozenset((
    "continue", "cont", "go", "ahead", "proceed", "resume", "next", "again",
    "now", "then", "keep", "going", "run", "it", "this", "that", "do", "done",
    "yes", "y", "yeah", "yep", "yup", "yea", "ok", "okay", "k", "sure",
    "please", "pls", "plz", "thanks", "thank", "thankyou", "thx", "ty",
    "good", "great", "cool", "nice", "perfect", "awesome", "lgtm", "fine",
    "works", "and", "the", "a", "an", "to", "up", "more", "all",
    "no", "nope", "stop", "wait",
))


MINER_RECURRENCE_MIN_LEN_KEY = "AGENTWARE_MINER_RECURRENCE_MIN_LEN"


MINER_RECURRENCE_CUE_REQUIRED_KEY = "AGENTWARE_MINER_RECURRENCE_CUE_REQUIRED"


MINER_RECURRENCE_STOPWORDS_KEY = "AGENTWARE_MINER_RECURRENCE_STOPWORDS"


def _transcript_norm_key(text):
    """Deterministic near-dup key: lowercase, collapse internal whitespace, strip
    surrounding punctuation. Empty for a non-str / blank body. No clock, no
    randomness — two bodies differing only in whitespace/case collapse to one
    key (Q2)."""
    if not isinstance(text, str):
        return ""
    t = " ".join(text.split()).lower()
    return t.strip(" \t\r\n.,;:!?-\"'`*_()[]{}<>")


def _parse_miner_min_len(raw):
    """Strict parse for the recurrence min-length gate -> non-negative int, or
    None for an empty/invalid value (the resolver falls through to the default,
    so a hand-edited config typo degrades to the default rather than silently
    disabling the gate). 0 is VALID and means 'no length gate'."""
    if raw is None:
        return None
    v = str(raw).strip().strip('"').strip("'")
    if not v:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _parse_miner_stopwords(raw):
    """Parse a comma/whitespace-separated stopword list -> frozenset of lowercase
    tokens, or None if unset. An EXPLICITLY empty value ("") yields an empty set
    (ack test disabled) rather than None, so the operator can turn the sub-test
    off without editing code."""
    if raw is None:
        return None
    v = str(raw).strip().strip('"').strip("'")
    toks = [t.strip().lower() for t in re.split(r"[,\s]+", v) if t.strip()]
    return frozenset(toks)


def _transcript_recurrence_gate():
    """Resolve the recurrence precision gate (review F-C) ONCE per mine run.

    Precedence per knob: env -> config.env -> module default (mirrors
    resolve_dream_max_runtime). Resolved once and threaded through the run so a
    concurrent config edit can never make one window's emission depend on WHEN a
    key was folded (INV-1: same corpus + same config => same candidates).
    """
    min_len = _parse_miner_min_len(os.environ.get(MINER_RECURRENCE_MIN_LEN_KEY))
    if min_len is None:
        min_len = _parse_miner_min_len(
            _read_config_key(MINER_RECURRENCE_MIN_LEN_KEY))
    if min_len is None:
        min_len = MINER_RECURRENCE_MIN_LEN

    cue_raw = os.environ.get(MINER_RECURRENCE_CUE_REQUIRED_KEY)
    if cue_raw is None or not str(cue_raw).strip():
        cue_raw = _read_config_key(MINER_RECURRENCE_CUE_REQUIRED_KEY)
    cue_required = (MINER_RECURRENCE_CUE_REQUIRED
                    if cue_raw is None or not str(cue_raw).strip()
                    else _review_truthy_env(cue_raw))

    stops = _parse_miner_stopwords(os.environ.get(MINER_RECURRENCE_STOPWORDS_KEY))
    if stops is None:
        stops = _parse_miner_stopwords(
            _read_config_key(MINER_RECURRENCE_STOPWORDS_KEY))
    if stops is None:
        stops = MINER_RECURRENCE_STOPWORDS
    return {"min_len": min_len, "cue_required": cue_required,
            "stopwords": stops}


def _transcript_is_ack(key, stopwords):
    """True iff EVERY token of the normalized key is a stopword — i.e. the body
    carries no substantive word at all ("continue please", "yes ok thanks").

    Token-level, NOT substring: "always continue on error" contains "continue"
    but also real content, so it is NOT an ack. An empty stopword set disables
    the test (nothing is an ack); a key with no tokens is not an ack (the
    length sub-test owns that case).
    """
    toks = [t for t in re.split(r"[^a-z0-9]+", key or "") if t]
    if not toks or not stopwords:
        return False
    return all(t in stopwords for t in toks)


def _transcript_recurrence_relevant(key, gate):
    """Review F-C: is this recurring key worth EMITTING as a candidate?

    (a) len(key) >= min_len  AND
    (b) carries a preference cue  OR  (cue not required AND not a pure ack)

    Deterministic and side-effect free — a pure function of (key, gate) — so the
    caller may re-tune and re-run over the SAME tally and get a consistent
    answer. Applies to RECURRENCE only; error-recovery emission is untouched.
    """
    if len(key or "") < gate["min_len"]:
        return False
    if _PREFER_QUEUE_RE.search(key or ""):
        return True
    if gate["cue_required"]:
        return False
    return not _transcript_is_ack(key, gate["stopwords"])


def _transcript_is_err_status(s):
    """The live.jsonl error signal. log-tool.sh writes 'ERR' (not 'error'); accept
    the real vocabulary AND common synonyms so the detector is forward-compatible
    across producers (codex-stream, a future hook)."""
    return str(s).strip().lower() in ("err", "error", "fail", "failed")


def _transcript_list_session_ids(kdir):
    """Session ids on disk (dirs under logs/sessions/), or [] on error."""
    sroot = os.path.join(kdir, "logs", "sessions")
    try:
        return [d for d in os.listdir(sroot)
                if os.path.isdir(os.path.join(sroot, d))]
    except OSError:
        return []


def _transcript_read_live(path):
    """Parse a session's live.jsonl into a list of record dicts (or [])."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return []
    return out


# Explicit facade export list (public AND underscore-private) — the entrypoint
# binds these onto its own namespace via the re-export loop so external consumers
# (tests/_fixtures.load_cli, the dashboard sidecar) keep reaching them as CLI.<name>.
# Order mirrors the module body, which is legacy source order, so a reviewer can
# diff this list against the monolith's own declaration order.
_EXPORTS = [
    "_review_truthy_env",
    "_PREFER_QUEUE_RE",
    "_PROMPTS_HEADER_RE",
    "_parse_prompts_records",
    "MINER_RECURRENCE_N",
    "MINER_RECURRENCE_MIN_LEN",
    "MINER_RECURRENCE_CUE_REQUIRED",
    "MINER_RECURRENCE_STOPWORDS",
    "MINER_RECURRENCE_MIN_LEN_KEY",
    "MINER_RECURRENCE_CUE_REQUIRED_KEY",
    "MINER_RECURRENCE_STOPWORDS_KEY",
    "_transcript_norm_key",
    "_parse_miner_min_len",
    "_parse_miner_stopwords",
    "_transcript_recurrence_gate",
    "_transcript_is_ack",
    "_transcript_recurrence_relevant",
    "_transcript_is_err_status",
    "_transcript_list_session_ids",
    "_transcript_read_live",
]
__all__ = list(_EXPORTS)
