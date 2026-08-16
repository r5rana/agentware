"""agentware_lib.ops.sessionparse.minerextract — facts out of a RAW record (layer: ops).

Owns: the readers that turn one raw transcript record into structured facts —
SECRET REDACTION (`_transcript_redact_secrets`), tolerant JSON field extraction
(`_TRANSCRIPT_JSON_OBJ_RE` / `_transcript_extract_json_field`), and the tool-target
resolvers (`_transcript_tool_target` / `_transcript_salvage_target`).

REDACTION RUNS FIRST, AND THAT ORDER IS THE POINT
A session transcript is the highest-risk text in the system: it contains whatever
the operator typed and whatever their tools printed, which routinely includes
tokens, keys and connection strings. Everything the miner emits — queue
candidates, error signatures, workorders — derives from these records, so
redaction happens HERE, at the boundary where a record is first read, rather than
at each of the places output is written. Redacting at the sinks means every new
sink is a new chance to forget.

The extractors are deliberately TOLERANT. A transcript is written by a live agent
and can be truncated mid-record, so a strict parse would discard exactly the
sessions most worth mining (the ones that ended badly). `_transcript_salvage_target`
exists for that case: recover the tool target from a partial record rather than
drop it.

Does NOT own: the cursor and caps (ops.sessionparse.minerstate); error signatures
(ops.sessionparse.minererrors); the mining passes (ops.sessionparse.minerscan).
Layer deps: ops -> same-layer sessionparse siblings (minerfolds for the key
normaliser, minerstate for the truncation marker) plus stdlib json/re. Pure text in, structured facts out — so an
adversarial or truncated record can be tested directly.
Fan-in: 2 at landing (regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith — byte-identical, comments and all.
"""

import json
import re

from agentware_lib.ops.sessionparse.minerfolds import _transcript_norm_key
from agentware_lib.ops.sessionparse.minerstate import MINER_TRANSCRIPT_TRUNC_MARKER


_TRANSCRIPT_JSON_OBJ_RE = re.compile(r'\s*\{\s*"')   # F-D discriminator


def _transcript_extract_json_field(raw, key):
    """Pull ONE string field's value out of possibly-TRUNCATED JSON object text
    (review F-D). Returns the decoded value, or the longest decodable PREFIX when
    truncation cut the value's closing quote — both sides of an error->ok pair are
    truncated at the SAME MAXLEN by the same writer, so a prefix still pairs
    deterministically. '' when the field is absent or unrecoverable. No clock, no
    network; bounded by the input length (INV-1)."""
    if not isinstance(raw, str) or not key:
        return ""
    m = re.search(r'"%s"\s*:\s*"' % re.escape(key), raw)
    if not m:
        return ""
    i, out = m.end(), []
    while i < len(raw):
        c = raw[i]
        if c == "\\":
            if i + 1 >= len(raw):
                break                      # escape severed by truncation
            out.append(raw[i:i + 2])
            i += 2
            continue
        if c == '"':
            break                          # value closed normally
        out.append(c)
        i += 1
    frag = "".join(out)
    # Truncation can sever a \uXXXX escape mid-sequence; shave the mangled tail
    # back a BOUNDED number of chars (an escape is at most 6) rather than looping.
    for _ in range(8):
        try:
            return json.loads('"%s"' % frag)
        except ValueError:
            if not frag:
                return ""
            frag = frag[:-1]
    return ""


def _transcript_salvage_target(raw):
    """Recover the primary target from a TRUNCATED tool-input JSON blob (F-D).

    log-tool.sh bounds `input` at MAXLEN=1500 + a marker (:67), so a long call's
    stored input is INVALID JSON and json.loads raises. The pre-fix behaviour fell
    through to _transcript_norm_key over the WHOLE <=1500-char blob, which is both
    wrong (the target is the command, not the record) and collision-prone: two
    DIFFERENT commands sharing a 1500-char prefix collapse to one target, so an
    unrelated later success could 'recover' an earlier error. Salvage the field
    VALUE instead, mirroring the dict path's precise-then-command priority.
    Returns '' when nothing is recoverable (conservative: no target => no pair)."""
    if raw.endswith(MINER_TRANSCRIPT_TRUNC_MARKER):
        raw = raw[:-len(MINER_TRANSCRIPT_TRUNC_MARKER)]
    for k in ("file_path", "path", "notebook_path", "url"):
        v = _transcript_extract_json_field(raw, k)
        if v and v.strip():
            return v.strip()
    for k in ("command", "cmd", "pattern", "query"):
        v = _transcript_extract_json_field(raw, k)
        if v and v.strip():
            return _transcript_norm_key(v)
    return ""


def _transcript_tool_target(rec):
    """Deterministic 'primary target' of a tool call from its input, so an
    error->ok pair on the SAME target can be matched. Prefers a precise path/url
    target; falls back to a normalized command/pattern signature; '' if none."""
    raw = rec.get("input")
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            # F-D: a JSON-OBJECT-shaped blob that fails to decode is a TRUNCATED
            # hook record -> salvage the field. A plain non-JSON string is a
            # legitimate producer shape (codex writes the bare command) and keeps
            # the normalized-string path below. The discriminator demands a
            # QUOTED KEY ({"...), not a bare '{', so a shell brace group such as
            # `{ echo hi; }` is correctly read as a command, not as truncated JSON.
            if _TRANSCRIPT_JSON_OBJ_RE.match(raw):
                return _transcript_salvage_target(raw)
            data = raw
    if isinstance(data, dict):
        for k in ("file_path", "path", "notebook_path", "url"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for k in ("command", "cmd", "pattern", "query"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return _transcript_norm_key(v)
        return ""
    if isinstance(data, str) and data.strip():
        return _transcript_norm_key(data)
    return ""


def _transcript_redact_secrets(s):
    """Strip embedded credentials from an error signature/target BEFORE it is
    persisted as inert candidate text OR a dedup key. The error DETAIL line the
    signature selector now prefers is exactly where a leaked secret lives — a
    `https://user:pw@host` git/DB URL, a `token=...` / `password: ...`
    assignment — and _transcript_build_record persists that text to the
    git-syncable prefer-queue (promotable into durable KB), so it must NEVER
    carry a plaintext secret (adversarial-review security-governance: the diff
    made the miner SELECT the detail line, so the redaction it built for the key
    must also cover the stored text). Deterministic; inert data in -> inert data
    out (R-SEC-02)."""
    s = str(s or "")
    # scheme://user[:pass]@host -> scheme://<redacted>@host. Userinfo cannot
    # cross '/', so [^/\s@]* is a safe greedy-to-the-authority-'@' match; this
    # also covers a password that itself embeds ':'.
    s = re.sub(r"([a-zA-Z][\w+.\-]*://)[^/\s@]*@", r"\1<redacted>@", s)
    # scheme-less user:pass@host (embedded creds without a URL scheme).
    s = re.sub(r"(?<![\w@])[\w.\-]+:[^\s/@]+@([\w.\-]+)", r"<redacted>@\1", s)
    # key=secret / key: secret assignments for common credential key names (a
    # ':'/'=' delimiter is REQUIRED, so benign prose like "Password for '...'"
    # is left intact).
    s = re.sub(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|"
               r"access[_-]?key|client[_-]?secret|bearer|authorization)\b"
               r"(\s*[:=]\s*)\S+", r"\1\2<redacted>", s)
    return s


_EXPORTS = [
    "_TRANSCRIPT_JSON_OBJ_RE",
    "_transcript_extract_json_field",
    "_transcript_salvage_target",
    "_transcript_tool_target",
    "_transcript_redact_secrets",
]

__all__ = list(_EXPORTS)
