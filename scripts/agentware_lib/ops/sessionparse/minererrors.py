"""agentware_lib.ops.sessionparse.minererrors — ERROR SIGNATURES from a transcript (layer: ops).

Owns: how a failure seen in a session transcript becomes a STABLE, comparable
signature — the frame/message recognisers (_TRANSCRIPT_ERR_FRAME_RE /
_TRANSCRIPT_ERR_MSG_RE), the signature builder (`_transcript_err_signature`), the
volatile-detail mask (`_transcript_mask_volatile`), the target classifiers
(`_transcript_err_target_class` / `_transcript_err_url_host_class` /
`_transcript_err_cmd_binary`), the dedup key and its gate
(`_transcript_errrec_key` / `_transcript_errrec_gate` / `_parse_miner_errrec_dedup`),
the recovery-text extractor and the reconciler that pairs an error with the fix
that followed it.

WHY MASKING IS THE WHOLE PROBLEM
"The same error happened again" is only useful if two occurrences of the same
underlying fault produce the SAME key. Raw error text never does: it carries
timestamps, pids, tmp paths, ports, hostnames, memory addresses and line numbers
that differ every run. `_transcript_mask_volatile` removes exactly those, and the
target classifiers collapse an incidental difference (a URL's host, a command's
absolute path) to its CLASS. Mask too little and every occurrence looks novel, so
nothing ever recurs; mask too much and unrelated failures collapse together and
the miner reports a phantom pattern. The classifiers are where that balance is
written down.

`_transcript_reconcile_errors` is the pairing pass: an error alone is noise, an
error FOLLOWED BY a recovery is a learning. It walks forward from each failure
looking for the step that resolved it, which is why the miner can propose
"this failed and here is what fixed it" rather than just "this failed".

Does NOT own: the cursor, caps and state (ops.sessionparse.minerstate); the
mining passes that call this (ops.sessionparse.minerscan); the recurrence
precision gate (ops.sessionparse.minerfolds).
Layer deps: ops -> core (config) and same-layer sessionparse siblings
(minerextract, minerfolds, minerstate) plus stdlib. One direction only —
minerscan depends on THIS module, never the reverse.
Fan-in: 1 at landing (regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith — byte-identical, comments and all.
"""

import json
import os
import re

import agentware_lib.core.config as config
from agentware_lib.core.config import _read_config_key
from agentware_lib.ops.sessionparse.minerextract import (
    _transcript_redact_secrets,
    _transcript_tool_target,
)
from agentware_lib.ops.sessionparse.minerfolds import _transcript_is_err_status
from agentware_lib.ops.sessionparse.minerstate import (
    _transcript_claude_path,
    _transcript_hook_trunc,
    _transcript_norm_ts,
    _transcript_resp_trunc,
)


# --- Error-recovery cross-session dedup gate (RCA F-E, 260722) ---------------
# The go-live safety prerequisite: error-recovery emission previously deduped
# ONLY via _prefer_cand_fp, whose fp EMBEDS sid — so the SAME failure mode
# recovered across N sessions yielded N distinct fps and evaded the belt
# (measured live: 153 candidates collapsing to 5 distinct texts). This gate
# dedups EMISSION on a session-INDEPENDENT key (tool + target-CLASS +
# normalized error signature) backed by a PERSISTENT cross-run ledger
# (err_emitted_keys in the miner state — the F-A lesson: an emit-once guard
# must outlive any evictable structure), so a mode already emitted in a PRIOR
# run is suppressed on the next. _prefer_cand_fp itself is UNTOUCHED — the
# legitimate cross-producer sid+turn+text dedup (stop-hook vs miner) stays.
# Modes: 'class' (default, conservative go-live: collapse by target class),
# 'exact' (finer: raw target + raw signature), 'off' (legacy, no cross-session
# dedup). Precedence per knob: env -> config.env -> module default.
MINER_ERRREC_DEDUP = "class"


MINER_ERRREC_DEDUP_KEY = "AGENTWARE_MINER_ERRREC_DEDUP"


def _transcript_scan_tool_pairs(path):
    """Read ONE durable transcript and return `(uses, results)`, or None if it
    could not be read at all.

    `uses` maps a tool_use_id to its `(name, input)`; `results` is the ordered
    list of `(tool_use_id, block, timestamp)` for every tool_result the harness
    marked `is_error: true`. Successful results are dropped here rather than
    downstream, because this pass exists only to reconstruct the FAILURES the
    PostToolUse hook never recorded.

    None and `({}, [])` are different facts and both callers care: an unreadable
    transcript is a degradation, an empty one is a codex/absent session. Tolerant
    of CONTENT throughout — a non-JSON line, a non-dict record, a message with no
    content list, a non-dict block are all skipped — and strict about ACCESS only.
    """
    uses, results = {}, []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                _transcript_collect_blocks(rec, uses, results)
    except OSError:
        return None
    return uses, results


def _transcript_collect_blocks(rec, uses, results):
    """Fold ONE transcript record's content blocks into the use/result tables.

    Pairing is by `tool_use_id` and NOT by position: a tool_use and its result sit
    in two different records (an assistant message, then a user message), and
    intervening records are common. A tool_use whose id is missing or not a string
    is dropped rather than keyed on a placeholder, or two unrelated calls would
    pair with each other.
    """
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return
    ts = rec.get("timestamp")
    for blk in content:
        if not isinstance(blk, dict):
            continue
        btype = blk.get("type")
        if btype == "tool_use":
            bid = blk.get("id")
            if isinstance(bid, str) and bid:
                uses[bid] = (blk.get("name"), blk.get("input"))
        elif btype == "tool_result" and blk.get("is_error") is True:
            results.append((blk.get("tool_use_id"), blk, ts))


def _transcript_live_err_keys(live):
    """The `(tool, target, ts)` keys of the ERR records the writer DID manage to
    record — a non-Bash tool that surfaced `tool_response.success=false`.

    Reconstructing one of those again would double-count the same failure. The key
    carries the TIMESTAMP so a genuine SECOND failure of the same target is still
    reconstructed: only the same failure at the same second is a duplicate.
    """
    keys = set()
    for r in live:
        if isinstance(r, dict) and _transcript_is_err_status(r.get("status")):
            keys.add((str(r.get("tool") or ""), _transcript_tool_target(r),
                      _transcript_norm_ts(r.get("ts"))))
    return keys


def _transcript_result_text(body):
    """A tool_result's `content` as TEXT with REAL newlines.

    Anthropic's `content` is FREQUENTLY a list of blocks
    (`[{"type":"text","text":"..."}]`) — the majority Claude shape. A raw
    `json.dumps()` escapes a traceback's newlines to literal "\n", so
    `_transcript_err_signature` sees ONE line, its frame/last-error logic is
    bypassed, and two genuinely-distinct deep failures collapse onto the constant
    JSON+preamble prefix -> one permanent F-E key (adversarial-review). Extracting
    the text blocks with real newlines is what keeps signature selection working
    on the actual error lines. A non-text block is serialised, a bare string kept,
    and an unserialisable body degrades to "" rather than raising.
    """
    if isinstance(body, list):
        parts = []
        for b in body:
            if isinstance(b, dict):
                t = b.get("text")
                if isinstance(t, str):
                    parts.append(t)
                else:
                    try:
                        parts.append(json.dumps(b, ensure_ascii=False))
                    except (TypeError, ValueError):
                        pass
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    if isinstance(body, str):
        return body
    try:
        return json.dumps(body, ensure_ascii=False)
    except (TypeError, ValueError):
        return ""


def _transcript_reconcile_errors(sid, live):
    """Reconstruct the ERR records log-tool.sh never wrote (review F-B).

    The PostToolUse hook does not fire on a FAILED Claude tool call (verified
    2026-07-14 — see the MINER_TRANSCRIPT_* note), so live.jsonl holds successes
    only and error-recovery was blind on the majority harness. The transcript
    pairs a tool_use (name + input) to its tool_result (is_error + content) by
    tool_use_id; every is_error:true pair becomes the record the hook would have
    appended: {ts, tool, status:'ERR', agent, input, response}.

    Returns [] for a codex/absent-transcript session, so that producer's own
    exit-code ERR path (codex-stream.py) is untouched. Best-effort (Q8): any
    parse/IO fault degrades to [] rather than failing the mine. Read-only,
    in-memory, no clock (INV-1) — ts comes from the transcript record itself.

    Three phases with no shared state, in three functions: SCAN the transcript
    into pairs, derive the SUPPRESSION keys from what the writer already recorded,
    and BUILD one record per surviving failure. The pairing is the only part that
    needs both halves at once, and it is the only part left here.
    """
    path = _transcript_claude_path(sid)
    if not path:
        return []
    pairs = _transcript_scan_tool_pairs(path)
    if pairs is None:
        return []
    uses, results = pairs
    seen = _transcript_live_err_keys(live)
    out = []
    for tuid, blk, ts in results:
        name, tinput = uses.get(tuid, (None, None))
        if not name:
            continue                       # unpaired result — nothing to target
        try:
            inp = json.dumps(tinput if tinput is not None else {},
                             ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            continue
        rec = {"ts": _transcript_norm_ts(ts), "tool": str(name), "status": "ERR",
               "agent": "main", "input": _transcript_hook_trunc(inp),
               "response": _transcript_resp_trunc(
                   _transcript_result_text(blk.get("content")))}
        key = (rec["tool"], _transcript_tool_target(rec), rec["ts"])
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


# FRAMING lines that are never the error message itself — a constant preamble
# (a Python `Traceback` header, a bash/codex bare `Exit code N`), a stack frame
# (`File "...", line N`, java/js `at pkg.Cls.m(...)`), or a constant trailing
# footer (`Run with --verbose ...`). Keying on any of these would derive ONE
# key for every genuinely-distinct failure and the PERSISTENT F-E ledger would
# suppress all but the first forever, so they are STRIPPED before signature
# selection. NB: a `Exception in thread ...` header is NOT here — the Java error
# IS on that line (adversarial-review round 4: the old `exception in thread.*`
# alternative swallowed the real error and keyed on a shared stack frame). A
# line is a frame ONLY when it is ENTIRELY framing — `Exit code 2: missing
# separator` (codex fuses the detail) and `error: <detail>` are kept whole.
_TRANSCRIPT_ERR_FRAME_RE = re.compile(
    r"^("
    r"traceback \(most recent call last\):?|"
    r"traceback:?|stack ?trace:?|"
    r"exit code \d+:?|"
    r"errors?:?|"
    r"during handling of the above exception.*|"
    r"the above exception was the direct cause.*|"
    r'file ".*", line \d+.*|'
    r"at [\w$][\w$.<>/-]* ?\(.*\)|"
    r"\.{3,}.*|"
    r"(hint|note|help):.*|"
    r".*failed with exit code \d+\.?|"
    r"run with [-\w][\w .-]* for (more|full|additional).*|"
    r"(for|see) (more|full|additional|further) (info|information|details|help).*"
    r")$", re.IGNORECASE)


# A line that POSITIVELY looks like the actual error/exception message. Used to
# pick the error out of the surviving core: a Python traceback's exception is
# the LAST such line, so we take the last match.
_TRANSCRIPT_ERR_MSG_RE = re.compile(
    r"error|exception|fail|fatal|panic|abort|denied|refus|"
    r"not found|no such|cannot|can't|could ?n'?t|unable|invalid|"
    r"missing|undefined|unexpected|timed? ?out|timeout|"
    r"\b[eE]\d{3,}\b", re.IGNORECASE)


def _transcript_err_signature(rec):
    """Deterministic, factual error signature of a failed call, whitespace-
    collapsed and bounded. The real error line is NOT positionally fixed across
    harnesses — a Python traceback puts it LAST, a bash `Exit code N` preamble
    puts it on line 2, a Java `Exception in thread ...` puts it on line 1 — so
    keying on a fixed position collapses genuinely-distinct failures onto one
    permanent F-E ledger key. Instead: drop the framing lines (constant
    preambles, stack frames, constant footers), then pick the LAST line that
    positively names an error/exception (the exception line of a traceback,
    which is last), falling back to the last surviving line. Inert data
    (R-SEC-02)."""
    resp = rec.get("response")
    if not isinstance(resp, str):
        resp = "" if resp is None else str(resp)
    lines = [l for l in (" ".join(ln.split()) for ln in resp.splitlines()) if l]
    if not lines:
        return ""
    core = [l for l in lines if not _TRANSCRIPT_ERR_FRAME_RE.match(l)]
    if not core:                       # everything looked like a frame
        core = lines
    # Pick the LAST line that positively names an error (a traceback's exception
    # is last), then APPEND the trailing NON-frame detail lines that belong to
    # it: a "generic header + specific detail" failure (`Error: invalid
    # arguments` / `at least one target required`) carries its discriminator in
    # that trailing detail, and dropping it collapses two genuinely-distinct
    # failures onto ONE permanent F-E ledger key (adversarial-review). Footers,
    # stack frames, and constant preambles were already stripped above, so the
    # join carries signal, not noise. Secrets in the selected detail are redacted
    # (R-SEC-02) before this text is persisted or keyed.
    err_idxs = [i for i, l in enumerate(core)
                if _TRANSCRIPT_ERR_MSG_RE.search(l)]
    pick = " ".join(core[err_idxs[-1]:]) if err_idxs else core[-1]
    return _transcript_redact_secrets(pick)[:120]


def _parse_miner_errrec_dedup(raw):
    """Strict parse for the F-E dedup mode -> 'class' | 'exact' | 'off', or None
    for an empty/invalid value (the resolver falls through, so a hand-edited
    config typo degrades to the conservative default rather than silently
    disabling the gate)."""
    if raw is None:
        return None
    v = str(raw).strip().strip('"').strip("'").lower()
    return v if v in ("class", "exact", "off") else None


def _transcript_errrec_gate():
    """Resolve the F-E error-recovery dedup gate ONCE per mine run (env ->
    config.env -> module default, mirroring _transcript_recurrence_gate) and
    thread it through the run, so a concurrent config edit can never make one
    batch's emission depend on WHEN a session was scanned (INV-1)."""
    mode = _parse_miner_errrec_dedup(os.environ.get(MINER_ERRREC_DEDUP_KEY))
    if mode is None:
        mode = _parse_miner_errrec_dedup(_read_config_key(MINER_ERRREC_DEDUP_KEY))
    if mode is None:
        mode = MINER_ERRREC_DEDUP
    return {"mode": mode}


def _transcript_mask_volatile(s):
    """Mask the volatile tokens that differ between two occurrences of the SAME
    failure mode — path-like strings, UUIDs, 0x/bare hex + sha/git-object ids,
    mixed alphanumeric random tokens (container/pod ids, temp suffixes) and
    digit runs — so the session-independent F-E key collapses cosmetic variance
    instead of leaking N distinct keys (adversarial-review F-E hardening: a
    signature or target whose volatility lives in an unmasked token — UUID,
    sha256 digest, `tmp_ab12cd` temp name — would otherwise re-create the exact
    153->N flood the gate exists to cure). Pure/deterministic (INV-1); inert
    data in -> inert data out (R-SEC-02)."""
    s = str(s or "").lower()
    s = re.sub(r"[a-z0-9_\-.~]*[/\\][a-z0-9_\-./~\\]*", "<path>", s)   # path-like
    # Formatted timestamps: the volatility of a RECURRING failure often lives in
    # a HUMAN-formatted timestamp whose digit runs are each < 5 long
    # (`2026-07-22 10:15:33`, `10:15:33`), so the `\d{5,}` mask below never
    # touches it and the SAME mode mints N distinct keys — the exact 153->N flood
    # the F-E gate exists to cure (adversarial-review). Mask them as whole tokens
    # FIRST (before the short-code-preserving digit rules), never touching the
    # short structured codes (`404`, `e0001`) that are discriminators.
    s = re.sub(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"
               r"(?:\s?z|\s?[+-]\d{2}:?\d{2})?", "<ts>", s)           # iso datetime
    s = re.sub(r"\d{4}-\d{2}-\d{2}", "<ts>", s)                       # iso date
    s = re.sub(r"\b\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?\b", "<ts>", s)     # clock hh:mm:ss
    s = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
               r"[0-9a-f]{12}\b", "<id>", s)                          # uuid
    s = re.sub(r"0x[0-9a-f]+", "<hex>", s)                            # 0x hex
    s = re.sub(r"\b[0-9a-f]{7,}\b", "<id>", s)                        # bare hex / sha
    # LONG mixed-alnum tokens ONLY. Random ids (temp names, container ids,
    # digest fragments) are long; SHORT structured codes (`e0001`, `exit 2`,
    # HTTP `404`, errno) are the DISCRIMINATOR between genuinely-distinct
    # failure modes, and masking them silently suppresses the second mode
    # forever (adversarial-review round 2). Same reason digit runs are only
    # masked from 5 digits up: timestamps/PIDs/offsets are long, codes are not.
    s = re.sub(r"(?<![a-z0-9_])(?=[a-z0-9_]*[a-z])(?=[a-z0-9_]*\d)"
               r"[a-z0-9_]{8,}(?![a-z0-9_])", "<id>", s)              # mixed-alnum id
    s = re.sub(r"\d{5,}", "<n>", s)                                   # long digit runs
    return s


# Leading tokens that are NOT the real binary of a command target — keying on
# them would bucket unrelated tools into one class (adversarial-review round 2).
MINER_CMD_WRAPPERS = frozenset((
    "sudo", "env", "time", "nohup", "nice", "exec", "command", "builtin"))


# Shell chain/list operators. `cd <dir> && <real cmd>` is the DOMINANT real
# Bash target shape (adversarial-review round 4: ~30-35% of live Bash errors),
# so a chain operator is skipped and `cd <dir>` navigational prefixes consume
# their directory argument — otherwise every `cd ... && X` collapses to the
# single bogus class cmd:cd and buckets unrelated tools onto one permanent key.
MINER_CMD_CHAIN_OPS = frozenset(("&&", "||", ";", "|", "&"))


# The TOOL KIND is the authoritative cmd-vs-path signal. Inferring it from the
# target STRING is unreliable in BOTH directions (a path may contain spaces; a
# command may start with a path sigil and end in a file extension) — every
# purely-lexical heuristic has a dual failure mode, and each one collapses
# genuinely-distinct failures onto one permanent ledger key. The producers name
# the tool, so use it and fall back to the heuristic only for unknown tools
# (adversarial-review round 3).
MINER_CMD_TOOLS = frozenset((
    "bash", "shell", "terminal", "command_execution", "run_command",
    "execute_command", "local_shell", "exec_command"))


MINER_PATH_TOOLS = frozenset((
    "read", "edit", "write", "multiedit", "notebookedit", "notebook_edit",
    "apply_patch", "str_replace_editor"))


def _transcript_err_url_host_class(host):
    """Class of a URL host: mask volatile SUBDOMAIN labels (where ephemeral
    per-tenant ids live, `run-3f9a12bc.workers.dev`) but keep the registrable
    domain + TLD verbatim, because THOSE labels are the stable identity —
    masking them would collapse two genuinely-distinct hosts onto one key
    (adversarial-review rounds 2-3, opposite directions of the same knob)."""
    labels = host.lower().split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    return ".".join([_transcript_mask_volatile(l) for l in labels[:-2]]
                    + labels[-2:])


def _transcript_err_cmd_binary(t):
    """The real binary of a command string: skip `FOO=bar` env assignments,
    wrapper prefixes (`sudo`, `env`, `time`, …), shell chain operators, and a
    leading `cd <dir>` navigational prefix (so `cd /repo && npm run build`
    classes as npm, the DOMINANT real shape — adversarial-review round 4), then
    reduce a path-shaped binary to its basename so `/usr/local/bin/npm` and
    `npm` share a class. Deterministic, lowercased; '' only for an empty
    command, `cd` only for a bare `cd`/`cd <dir>` with nothing after."""
    toks = t.split()
    i, n = 0, len(toks)
    while i < n:
        tok = toks[i]
        low = tok.lower()
        if low in MINER_CMD_CHAIN_OPS:
            i += 1
            continue                      # chain operator, not a binary
        if low == "cd":
            i += 2                         # skip `cd` AND its directory argument
            continue
        if "=" in tok and not tok.startswith("="):
            i += 1
            continue                      # FOO=bar env assignment
        if low in MINER_CMD_WRAPPERS:
            i += 1
            continue                      # wrapper prefix, not the binary
        if "/" in low or "\\" in low:
            low = low.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        if low:
            return low
        i += 1
    return toks[0].lower() if toks else ""


def _transcript_err_target_class(target, tool=None):
    """Session-independent CLASS of a tool target, so the SAME failure mode on
    cosmetically-different concrete targets (another checkout's path, another
    file of the same kind, another session's temp name) collapses to one F-E
    key. Deterministic, no clock:
      url  -> its host, credentials/port stripped and volatility-masked, so
              neither secrets nor ephemeral per-tenant hosts enter the ledger;
      cmd  -> its real binary (env assignments + wrapper prefixes skipped, a
              path-shaped binary reduced to its basename). cmd-vs-path CANNOT be
              inferred from a '/' — commands routinely embed path arguments —
              so a path SIGIL plus a trailing file extension decides it;
      path -> its EXTENSION verbatim (a discriminator: .py is not .md), or the
              volatility-masked bare basename when there is none (that is where
              per-session temp names live).
    Only genuinely-volatile components are masked — masking a binary or an
    extension would erase the very discriminator the key needs — and every
    component is length-bounded so the ledger key cannot grow unbounded
    (adversarial-review F-E hardening, rounds 1-2)."""
    t = " ".join(str(target or "").split())
    if not t:
        return ""
    # Strip ALL userinfo up to the LAST '@' of the authority (`[^/\s]*@` is
    # greedy and cannot cross '/'), and exclude '@' from the host capture, so a
    # password that itself contains an unencoded '@' (`user:p@w@h.io`) cannot
    # leak a credential fragment into the persistent ledger (adversarial-review).
    m = re.match(r"(?:https?|wss?|ftp)://(?:[^/\s]*@)?([^/:@\s]+)", t,
                 re.IGNORECASE)
    if m:
        return "url:" + _transcript_err_url_host_class(m.group(1))[:60]
    # The TOOL KIND is authoritative when we know it; the lexical heuristic
    # below is only the fallback for unknown tools (adversarial-review round 3).
    kind = str(tool or "").strip().lower()
    if kind in MINER_CMD_TOOLS:
        return "cmd:" + _transcript_err_cmd_binary(t)[:40]
    if kind not in MINER_PATH_TOOLS:
        # A PATH SIGIL wins over the space heuristic: an absolute/relative path
        # may legitimately contain spaces ("/tmp/my report.md") and must NOT be
        # read as a command. Conversely a command may carry a path argument, so
        # cmd-vs-path cannot be inferred from a '/' alone.
        looks_path = t[0] in "/~" or t[:2] == "./" or t[:3] == "../"
        if " " in t:
            # Ambiguous ONLY when a path sigil is present: `/tmp/my report.md`
            # is a PATH, `/usr/local/bin/npm run build` is a COMMAND with an
            # absolute binary. A space-bearing PATH ends in a file extension.
            if not (looks_path
                    and re.search(r"\.[a-z0-9]{1,8}$", t, re.IGNORECASE)):
                return "cmd:" + _transcript_err_cmd_binary(t)[:40]
        elif not (looks_path or "/" in t or "\\" in t):
            return "cmd:" + t.lower()[:40]
    base = t.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    # Peel trailing VOLATILE numeric suffixes (logrotate .1/.2, date-stamp
    # .20260720, PID/epoch .1690000001) so a rotated file keeps the class of its
    # REAL type (.log, .sql) instead of minting one UNBOUNDED class per rotation
    # — the exact N-not-bounded flood the F-E gate exists to cure (adversarial-
    # review). An all-digit final segment is a rotation/stamp, never a type.
    segs = base.split(".")
    while len(segs) > 1 and segs[-1].isdigit():
        segs.pop()
    ext = ("." + segs[-1]) if (len(segs) > 1 and segs[-1]) else ""
    # An EXTENSION is a DISCRIMINATOR (.py is not .md) and is kept verbatim; a
    # bare extensionless basename is where per-session temp names live, so only
    # THAT is volatility-masked.
    return "path:" + (ext.lower()[:40] if ext
                      else _transcript_mask_volatile(base)[:40])


def _transcript_err_sig_norm(sig):
    """Session-independent normalization of an error signature: mask the volatile
    parts (paths, hex/uuid/sha/mixed-alnum ids, digit runs) that differ between
    two occurrences of the SAME failure mode, collapse whitespace, bound. Pure
    string function — deterministic (INV-1), inert data in, inert data out
    (R-SEC-02)."""
    return " ".join(_transcript_mask_volatile(sig).split())[:120]


def _transcript_errrec_key(tool, target, sig, mode, tier3=False):
    """The F-E session-independent dedup key. NO sid, NO turn — that is the
    whole point: the same failure mode recovered in N sessions derives ONE key.
    'class' collapses by target class + normalized signature (conservative
    default); 'exact' keys on the raw target + raw signature (finer).

    A tier-3 (kernel-path / gate-loosening) candidate keys into its OWN
    namespace. It is therefore still BOUNDED — a recurring kernel-path recovery
    dedups against its own prior emissions instead of re-emitting once per
    session forever — while never occupying the key a LEGITIMATE candidate
    sharing tool/class/signature would derive, so a drain-rejected bait can
    never blind a real learning (adversarial-review rounds 1-2)."""
    ns = "errrec3" if tier3 else "errrec"
    if mode == "exact":
        return "%s|%s|%s|%s" % (
            ns, str(tool or ""), " ".join(str(target or "").split())[:80],
            str(sig or "")[:120])
    return "%s|%s|%s|%s" % (
        ns, str(tool or ""), _transcript_err_target_class(target, tool),
        _transcript_err_sig_norm(sig))


def _transcript_error_recovery_text(tool, target, sig):
    """Deterministic, FACTUAL, single-line derivation stored VERBATIM as inert
    data (R-SEC-02) — byte-stable given (tool, target, signature). Rarely matches
    the capturable preference cue, so it journals (tier-1) — the conservative v1."""
    tgt = _transcript_redact_secrets(" ".join(str(target).split()))[:80]
    s = (" (%s)" % _transcript_redact_secrets(sig)) if sig else ""
    return ("error-recovery: '%s' failed then succeeded on %s%s"
            % (tool, tgt, s))[:200]


_EXPORTS = [
    "MINER_ERRREC_DEDUP",
    "MINER_ERRREC_DEDUP_KEY",
    "_TRANSCRIPT_ERR_FRAME_RE",
    "_TRANSCRIPT_ERR_MSG_RE",
    "_transcript_err_signature",
    "_parse_miner_errrec_dedup",
    "_transcript_errrec_gate",
    "_transcript_mask_volatile",
    "MINER_CMD_WRAPPERS",
    "MINER_CMD_CHAIN_OPS",
    "MINER_CMD_TOOLS",
    "MINER_PATH_TOOLS",
    "_transcript_err_url_host_class",
    "_transcript_err_cmd_binary",
    "_transcript_err_target_class",
    "_transcript_err_sig_norm",
    "_transcript_errrec_key",
    "_transcript_error_recovery_text",
    "_transcript_scan_tool_pairs",
    "_transcript_collect_blocks",
    "_transcript_live_err_keys",
    "_transcript_result_text",
    "_transcript_reconcile_errors",
]

__all__ = list(_EXPORTS)
