"""THE SINGLE DEFINITION of "a bash call site" for the loop-wiring gate.

Task 33 splits the 1961-line `agentware.sh` into a <=250-line orchestrator plus
`scripts/loop/*.sh`. The risk that split carries is not a syntax error — bash
would surface that immediately — it is a call site that quietly fails to move.
A function whose DEFINITION lands in a lib but whose CALL was dropped from the
orchestrator leaves a gate that exists, tests green, and never runs. That is the
same fail-open class as `learnings_promoted()` returning PASS on a non-executable
entrypoint (plan task 25) and `run_selfheal_review` returning PROCEED when
`DOCS_DIR` and `KDIR` disagree (plan task 4): nothing turns red.

So the split is gated on COUNTED WIRING — for every capability, the number of
places it is actually invoked, at `aw-pre-merge` and in the merged tree.

Counting bash calls with a bare `grep -c <name>` does not work, and the failure
is not marginal. `log` appears 68 times under a naive word match over
`aw-pre-merge:agentware.sh`, but most of those are the English word inside a
comment ("the live log", "a log line"). A gate whose baseline is 68 is measuring
prose: reflowing a comment reddens it, and deleting a real `log` call while
adding a sentence keeps it green. The count has to mean "bash would execute
this", which means COMMAND POSITION, not lexical occurrence.

## What counts as a call site

A name in COMMAND POSITION on a line bash would execute:

  * at the start of a statement, after any number of `if` / `elif` / `while` /
    `until` / `!` / `{` / `(` prefixes  — `run_pre_hooks`, `if ! open_markers`
  * after a statement separator or connector — `;`  `&&`  `||`  `|`
  * inside a command substitution — `$(open_markers)`  `` `open_markers` ``
  * as a `trap` handler — `trap cleanup INT TERM`, `trap 'loop_on_exit' EXIT`

That last rule is not a nicety. A trap handler is an ARGUMENT to `trap`, never a
command word, so the command-position rules alone score `cleanup` and
`loop_on_exit` at ZERO — and a baseline of zero can never redden. Dropping the
EXIT trap in the split would silently disable the terminal-outcome event and the
live-stream reap while every other count stayed green, which is precisely the
fail-open this module exists to catch. So `trap` is counted explicitly.

What deliberately does NOT count:

  * anything on a comment line (`#` first non-space) — prose, not execution
  * the function's own `name()` definition line — a definition is not a call
  * a bare lexical occurrence anywhere else: inside a double-quoted string, as
    part of a path (`logs/`), or hyphenated/dotted into a larger token. The
    negative lookarounds reject `$`, `.`, `-`, `/` and word characters on both
    sides, so `$LOG`, `log-prompt.sh`, `logs/` and `_hash_stdin` inside
    `x_hash_stdin` are all rejected.

This is deliberately a LOWER BOUND on real invocations: a call built by `eval`
or reached through a variable is invisible here. That is the correct direction
for a ratchet — it can under-count a exotic construct the loop does not use, but
it cannot be fooled into over-counting by a comment.

Trailing comments are stripped only when the `#` is preceded by whitespace and
the line has no unbalanced quote before it, so a `#` inside a string (e.g. the
`'^\\s*[MADRCU]'` porcelain filter, or a `${var#prefix}` expansion) is left
alone. Over-keeping text is safe here — the command-position rules reject prose
anyway; wrongly stripping would hide a real call.

Stdlib only, read-only, deterministic, py3.7-safe.
"""

import re

__all__ = ["call_sites", "count_call_sites", "strip_comment", "shell_defs"]

_DEF_RE = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{?")

# Boundaries: a call site's name may not be glued to a word char, `$`, `.`, `-`
# or `/` on either side. `-`/`/` keep `log-prompt.sh` and `logs/foo` out; `$`
# keeps `$log` out; `.` keeps `x.log` out.
_L = r"(?<![A-Za-z0-9_$./-])"
_R = r"(?![A-Za-z0-9_./-])"

# Prefixes that may sit between a statement boundary and a command word.
_PREFIX = r"(?:(?:if|elif|while|until|then|do|else|!|\{|\()\s+|[!{(]\s*)*"


def strip_comment(line):
    """Drop a trailing `# ...` comment, leaving `#` inside quotes/expansions alone.

    Walks the line tracking single/double quote state and `${...}` depth. A `#`
    only starts a comment when unquoted, outside a `${}` expansion, and at the
    start of a word (preceded by whitespace or nothing) — which is bash's own
    rule and keeps `${var#prefix}` and `'[MADRCU]#'` intact.
    """
    out = []
    sq = dq = False
    brace = 0
    prev = ""
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\" and not sq and i + 1 < len(line):
            out.append(c)
            out.append(line[i + 1])
            prev = line[i + 1]
            i += 2
            continue
        if c == "'" and not dq:
            sq = not sq
        elif c == '"' and not sq:
            dq = not dq
        elif c == "{" and not sq and not dq and prev == "$":
            brace += 1
        elif c == "}" and not sq and not dq and brace:
            brace -= 1
        elif c == "#" and not sq and not dq and not brace and (prev == "" or prev.isspace()):
            break
        out.append(c)
        prev = c
        i += 1
    return "".join(out)


def shell_defs(text):
    """Return the ordered list of top-level function names defined in `text`."""
    names = []
    for line in text.splitlines():
        m = _DEF_RE.match(line)
        if m and m.group(1) not in names:
            names.append(m.group(1))
    return names


def call_sites(text, name):
    """Return the 1-indexed line numbers on which `name` is CALLED in `text`."""
    esc = re.escape(name)
    pats = [
        # command position at the start of a statement
        re.compile(r"^\s*" + _PREFIX + _L + esc + _R),
        # after a separator / connector
        re.compile(r"(?:;|&&|\|\||\|)\s*" + _PREFIX + _L + esc + _R),
        # command substitution
        re.compile(r"(?:\$\(|`)\s*" + _PREFIX + _L + esc + _R),
        # trap handler: `trap cleanup INT TERM` / `trap 'loop_on_exit' EXIT`
        re.compile(r"(?<![A-Za-z0-9_])trap\s+['\"]?\s*" + _L + esc + _R),
    ]
    hits = []
    for n, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith("#"):
            continue
        line = strip_comment(raw)
        if not line.strip():
            continue
        m = _DEF_RE.match(line)
        if m and m.group(1) == name:
            continue  # the definition itself is not a call
        for p in pats:
            if p.search(line):
                hits.append(n)
                break
    return hits


def count_call_sites(text, name):
    """Number of lines on which `name` is called (see `call_sites`)."""
    return len(call_sites(text, name))
