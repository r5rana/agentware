#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/hooks/common.sh — the primitives every agent hook calls.
#
# Sourced FIRST by every hook under scripts/hooks; never executed directly.
# Mirrors scripts/loop/common.sh for the loop libs: one definition site per
# primitive, so two hooks can never drift into disagreeing about where the
# knowledge dir is, what a timestamp looks like, or how a JSON field is read.
#
# Why this file exists (plan task 35). Six hooks each carried their own copy of
# the same four resolutions. Two of those copies had already diverged in ways
# that were invisible until they mattered:
#
#   * THE JSON-FIELD LADDER. session-start.sh read its field through a
#     jq -> python3 ladder; log-prompt.sh, log-stop.sh, log-subagent.sh,
#     log-tool.sh and retrieval-state.sh read theirs through jq ALONE, inside an
#     `if command -v jq` guard whose else-branch left every field EMPTY. On a
#     machine without jq the session banner still resolved its session id while
#     log-prompt.sh appended a header with an empty session, an empty cwd and an
#     EMPTY PROMPT — silent, total loss of the operator's prompt record, with no
#     error anywhere, because a logging hook may never fail loudly. The ladder is
#     now defined ONCE, here, so every hook degrades identically and the
#     jq-absent path is exercised by whichever hook runs first.
#
#   * THE KNOWLEDGE-DIR RESOLUTION. Five hooks resolved it by running
#     $REPO_ROOT/scripts/aw-knowledge-dir (an ABSOLUTE path, correct from any
#     cwd). retrieval-state.sh instead ran the RELATIVE `scripts/agentware config
#     --knowledge-dir-only`, so it resolved only when the agent's cwd happened to
#     be the repo root and silently no-opped everywhere else — the R-RET ladder
#     enforcement was off for every session started from another directory.
#
# CONTRACT — this file is sourced, so it must never:
#   * write to stdout (a SessionStart/PostToolUse hook's stdout is its payload,
#     and stray output is injected into the model's context);
#   * end on a failing command. `source` returns the status of its LAST command,
#     so a trailing `command -v jq && FLAG=1` idiom makes `. common.sh` a FAILED
#     command and a `set -e` hook (retrieval-state.sh) exits 1 having done
#     nothing. Every probe below is written as a full `if`, which always exits 0.
#
# Stdlib only: bash, plus OPTIONAL jq and python3, exactly as before. No install
# step, so the clone-and-go contract holds.

# Idempotent: a hook that sources this twice (or a lib that sources it again)
# must not re-run the probes.
if [ -z "${AW_HOOK_COMMON_LOADED:-}" ]; then
AW_HOOK_COMMON_LOADED=1

# --- Repo root ---------------------------------------------------------------
# Resolved ONCE, from this file's own location, so it is correct no matter what
# cwd the harness spawned the agent in. BASH_SOURCE[0] is this lib (not the
# sourcing hook), which is what makes the answer independent of the caller.
AW_HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AW_REPO_ROOT="$(cd "$AW_HOOK_DIR/../.." && pwd)"

# --- Engine probes -----------------------------------------------------------
AW_HAVE_JQ=""
if command -v jq >/dev/null 2>&1; then AW_HAVE_JQ=1; fi
AW_HAVE_PY=""
if command -v python3 >/dev/null 2>&1; then AW_HAVE_PY=1; fi

# The python3 rung of the JSON ladder. Held as ONE string so the fallback cannot
# drift between call sites the way the inline copies did. Reads the document on
# stdin and takes (jq-style dotted path, mode) as argv, so no untrusted value is
# ever interpolated into a program (R-SEC-01/R-SEC-02): hook payloads are DATA.
#
# Modes mirror jq exactly, which is the point — the two rungs must be
# indistinguishable to a caller:
#   raw      `jq -r '<path>'`            absent/null -> the string "null"
#   default  `jq -r '<path> // empty'`   absent/null/false -> ""  (jq's `//`
#                                        treats false as empty; so does this)
#   compact  `jq -c '<path>'`            absent/null/false -> ""; caller supplies
#                                        the literal default
# Single-quoted, so it contains no single quote of its own.
AW_JSON_PY='import sys, json

ERR = object()   # jq raises when a path indexes INTO a non-object; so do we.


def _get(doc, path):
    cur = doc
    for part in path.strip(".").split("."):
        if not part:
            continue
        if cur is None:
            return None
        if not isinstance(cur, dict):
            return ERR
        cur = cur.get(part)
    return cur


def _s(val):
    """A scalar rendering that can never contain a newline. Mirrors `jq -c -r`:
    a string is emitted verbatim; anything else is COMPACT json. The -c matters —
    jq pretty-prints an object by default, and a multi-line value spliced into a
    single-line sink header (prompts.log, activity.log) corrupts every downstream
    parser that reads one record per line."""
    return val if isinstance(val, str) else json.dumps(
        val, separators=(",", ":"), ensure_ascii=False)


try:
    doc = json.loads(sys.stdin.read())
except Exception:
    doc = None
path, mode = sys.argv[1], sys.argv[2]
val = _get(doc, path)
if val is ERR:
    # jq exits non-zero here and the shell rung maps that to "" in EVERY mode
    # (including raw). Matching it is what keeps the two rungs indistinguishable.
    sys.stdout.write("")
elif mode == "compact":
    out = "" if val is None or val is False else json.dumps(
        val, separators=(",", ":"), ensure_ascii=False)
elif mode == "raw":
    out = "null" if val is None else _s(val)
else:
    out = "" if val is None or val is False else _s(val)
sys.stdout.write(out)
'

# --- The JSON-field ladder (ONE definition, three modes) ---------------------
# aw_json <json> <jq-style-path> <mode>
# The primitive. jq when present, python3 otherwise, empty when neither exists.
# NEVER fails: a malformed payload yields "" (mode raw: "null") rather than an
# error, because a logging hook that aborts loses the very record it exists to
# keep. The document is passed on STDIN, never as an argv, so a large tool
# payload cannot hit ARG_MAX.
aw_json() {
  local _doc="$1" _path="$2" _mode="$3" _out=""
  if [ -n "$AW_HAVE_JQ" ]; then
    case "$_mode" in
      raw)     _out="$(printf '%s' "$_doc" | jq -c -r "$_path"          2>/dev/null)" || _out="" ;;
      compact) _out="$(printf '%s' "$_doc" | jq -c    "$_path // empty" 2>/dev/null)" || _out="" ;;
      *)       _out="$(printf '%s' "$_doc" | jq -c -r "$_path // empty" 2>/dev/null)" || _out="" ;;
    esac
  elif [ -n "$AW_HAVE_PY" ]; then
    _out="$(printf '%s' "$_doc" | python3 -c "$AW_JSON_PY" "$_path" "$_mode" 2>/dev/null)" || _out=""
  fi
  printf '%s' "$_out"
}

# aw_json_field <json> <path> [default] — a scalar, with an optional default
# applied when the field is absent, null or false (jq `//` semantics).
aw_json_field() {
  local _v
  _v="$(aw_json "$1" "$2" default)"
  if [ -z "$_v" ]; then _v="${3:-}"; fi
  printf '%s' "$_v"
}

# aw_json_raw <json> <path> — the field VERBATIM: "true" / "false" / "null".
# Required wherever `false` must stay distinguishable from absent; jq's `//`
# collapses the two, which would mask a tool failure as a success.
aw_json_raw() { aw_json "$1" "$2" raw; }

# aw_json_compact <json> <path> [default-literal] — the subtree as ONE line of
# JSON (never embeds a newline, so it is safe to append to a .jsonl sink).
aw_json_compact() {
  local _v
  _v="$(aw_json "$1" "$2" compact)"
  if [ -z "$_v" ]; then _v="${3:-}"; fi
  printf '%s' "$_v"
}

# --- Knowledge dir / log dir -------------------------------------------------
# aw_knowledge_dir — THE resolution, for every hook. Delegates to the one
# resolver script the bash and python sides share, by ABSOLUTE path so the answer
# does not depend on the agent's cwd. Empty (never an error) pre-onboarding.
# Memoised: hooks are on the per-tool-call hot path and this is a subprocess.
aw_knowledge_dir() {
  if [ -z "${AW_KDIR_RESOLVED:-}" ]; then
    AW_KDIR="$("$AW_REPO_ROOT/scripts/aw-knowledge-dir" 2>/dev/null || true)"
    AW_KDIR_RESOLVED=1
  fi
  printf '%s' "${AW_KDIR:-}"
}

# aw_log_dir — the external knowledge base's logs/ when one is configured, else
# a repo-local fallback that exists ONLY pre-onboarding.
aw_log_dir() {
  local _k
  _k="$(aw_knowledge_dir)"
  if [ -n "$_k" ]; then printf '%s' "$_k/logs"; else printf '%s' "$AW_REPO_ROOT/.agentware-logs"; fi
}

# --- Timestamp ---------------------------------------------------------------
# aw_ts — the ONE timestamp format every sink is parsed with (UTC, ISO-8601,
# second resolution). log-prompt/log-stop/log-tool/log-subagent all embedded this
# literal; a single stray %z or a local-time copy would desynchronise the sinks
# the transcript miner joins on.
aw_ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

fi
:
