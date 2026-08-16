"""agentware_lib.ops.planauthor.plankernelpath -- g1 kernel-path REFUSAL (layer: ops).

Owns: the g1 guard family that decides whether a value names a path inside the
agentware PACKAGE, and therefore whether a MACHINE-generated plan (`plan new --auto`)
must be refused outright -- the de-obfuscating
normalizer (_strip_kernel_invisibles, _KERNEL_CONFUSABLE_TT, _KERNEL_TOK_EDGE_RE,
_KERNEL_HOME_RE, _norm_kernel_token, _kernel_subtokens), the two matchers
(_has_kernel_path_segment, _text_hits_kernel_path), the filesystem resolver
(_resolves_under_repo) and the `--target` entry point (_target_hits_kernel).

The obligation is REFUSAL, not rewriting: these functions answer "does this value name
a kernel path?" and a True answer rejects the WHOLE emit. A machine-generated plan can
never be born a self-extension (decide-self-correction-governance-tiers, guard g1);
human self-extensions still route through R-PKG-03..06, and this only fences the
emitter. The P2c miner must not launch behind a leaky gate.

The evasion surface is why this is more than a substring check. A kernel name can be
hidden with zero-width and combining codepoints (`AGENTS<ZWSP>.md`), fullwidth and
compatibility forms, Cyrillic/Greek homoglyphs (`АGENTS.md`), markdown emphasis and
quoting (`**AGENTS.md**`, `~~AGENTS.md~~`), possessives (`AGENTS.md's`), list glue
(`AGENTS.md,steering`), symlinks, `..` traversal, and -- on case-insensitive APFS --
plain case (`~/workspace/AGENTWARE`, which carries no kernel token at all). Each of
those is closed by a named step below rather than by one broad pattern.

Matching is PATH-SEGMENT-bounded, never substring, so a legitimate external path that
merely contains a kernel token is not over-refused: `/x/product-steering/n`,
`/x/.claude-backup` and `my-agents.md-notes` all pass.

Does NOT own: the g2 EMIT-SIDE sanitizers that REWRITE a value to be safe to render
(_sanitize_provenance / _sanitize_context_block / _sanitize_target) ->
ops.planauthor.plansanitize -- a different obligation (make this safe) from this
module's (reject this outright), reached from different verbs on different paths; the
plan GENERATION half that calls this guard -> ops.planauthor.planedit; the plan
STATE MACHINE -> ops.planauthor.planstate; the plan grammar -> ops.planmarkers.

WHY THIS IS A SEPARATE MODULE (feature 260813 task 39)
------------------------------------------------------
Same criterion as the planlint and plansanitize splits (see
`decide-modular-core-task39-split-the-enforcer-where-the-rules-change-obligation-not-where-the-line-count-hurts`):
split where the RULES change obligation, not where the line count hurts. The g1 and g2
families are named as one "sanitizer family" in the monolith only because they sit
adjacent in the file. A change to the kernel-path token list has nothing to say about
header forgery, and vice versa. Landing them together would also not fit: planedit is
already 408 of its 600 lines.

Does NOT own the KERNEL-PATH TOKEN LIST: `_KERNEL_PATH_TOKENS` has ONE definition
site, ops.plangraph, and is imported here. Two copies of that tuple would let a hook
file added to one copy leave the other guard blind -- a security hazard, not a style
question.

Layer deps: ops.planauthor.plankernelpath -> core.paths (REPO_ROOT), a same-layer
ops->ops edge to ops.plangraph (the single token-list definition), and stdlib
`os` / `re` / `unicodedata`. All downward or same-layer; ops.plangraph does not import
this module, so no cycle is reachable.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all --
which is what the body differ's length floor demands
(`learn-under-a-body-length-differ-a-move-must-be-a-pure-move`: tidying in transit
reads as a body that LOST something).

Deterministic apart from _resolves_under_repo, which touches the filesystem
(realpath) by necessity -- resolving symlinks IS the check.
"""

import os
import re
import unicodedata

from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.ops.plangraph import _KERNEL_PATH_TOKENS


# The kernel-path token list has exactly ONE definition site, in ops.plangraph,
# and is imported here rather than restated. Two copies of this tuple is a live
# security hazard, not a style question: `plangraph._kernel_write_tokens` and this
# module's `_has_kernel_path_segment` must answer "is this a kernel path?" from the
# SAME list, or adding a hook file to one copy silently leaves the other guard
# blind to it. Same reason the constant differ pins module-scope literals at all.


def _resolves_under_repo(candidate, allow_relative=False):
    """True iff `candidate`, once ~-expanded and realpath-resolved, IS REPO_ROOT
    or lives inside it — compared case-INSENSITIVELY via .casefold().

    Why not os.path.normcase / os.path.abspath? On posix (os.path is posixpath,
    incl. case-insensitive APFS) BOTH normcase and realpath PRESERVE case — so the
    naive "normcase+realpath" fix would still let `--target ~/workspace/AGENTWARE`
    (the real package on APFS, carrying no kernel token) bypass. realpath closes
    the symlink + `..`-traversal class; .casefold() on BOTH sides closes the case
    class.

    A bare RELATIVE candidate is resolved against CWD ONLY when
    `allow_relative=True`. `--target` passes True: a relative `../agentware` / `.`
    reaches the package at loop-execution time (the loop runs from REPO_ROOT) and
    MUST be refused. The free-text scan passes the default False so a benign
    forward-relative token like `logs/a.log` in `--evidence` is not CWD-
    false-matched (the caller resolves only `..`-traversal text tokens explicitly)."""
    if not candidate:
        return False
    exp = os.path.expanduser(candidate.strip())
    if not exp:
        return False
    if not os.path.isabs(exp) and not allow_relative:
        return False
    try:
        rl = os.path.realpath(exp).casefold()
        rt = os.path.realpath(REPO_ROOT).casefold()
    except (OSError, ValueError):
        return False
    return rl == rt or rl.startswith(rt + os.sep)


# Edge punctuation stripped from BOTH ends of a token before a kernel match: ANY
# char that is NOT [alphanumeric . / ~]. This one class subsumes ASCII quotes/
# brackets/pipe, markdown emphasis (* _), leading sigils (@ # -), and UNICODE
# quotes/guillemets (smart quotes “ ” ‘ ’ « »), WITHOUT touching '.' (a leading
# '../' traversal + a '.claude' dotfile), '/' (path structure), or '~' (handled
# separately — a trailing/strikethrough strip vs a '~/…' home path).
_KERNEL_TOK_EDGE_RE = re.compile(r"^[^0-9A-Za-z./~]+|[^0-9A-Za-z./~]+$")
_KERNEL_HOME_RE = re.compile(r"~[^/]*/")   # a '~/…' or '~user/…' home-path prefix


def _strip_kernel_invisibles(s):
    """Remove zero-width / invisible / format / control / combining codepoints and
    NON-ASCII space separators (NBSP …) that hide a kernel basename inside a token
    (`AGENTS<ZWSP>.md`, `AGENTS<NBSP>.md`, `AGENTS<CGJ>.md`). CATEGORY-based so it
    covers the whole class (all Cc/Cf/Cs/Co/Cn + Mn/Me marks + Zs/Zl/Zp separators),
    not an enumerated few — e.g. combining grapheme joiner U+034F, invisible math
    operators U+2061-2064, LRM/RLM/ALM. Keeps ASCII whitespace (a legit token
    separator), so a real space still splits `foo AGENTS.md`."""
    out = []
    for ch in s:
        if ch in " \t\n\r\f\v":
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat[0] == "C" or cat in ("Mn", "Me", "Zs", "Zl", "Zp"):
            continue
        out.append(ch)
    return "".join(out)


# Common Cyrillic/Greek HOMOGLYPHS -> their Latin lookalike, so a homoglyph'd
# kernel name (e.g. `АGENTS.md` with a Cyrillic `А`) normalizes to the real token.
# A CURATED map (not full Unicode TR39) over the ASCII letters kernel basenames
# use; exotic confusables beyond it are a documented residual on the operator-
# typed, source=user capture path.
_KERNEL_CONFUSABLE_TT = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "к": "k", "м": "m", "т": "t", "н": "h", "в": "b", "і": "i", "ѕ": "s",
    "ԁ": "d", "ј": "j", "ԍ": "g", "ո": "n",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "У": "Y", "Х": "X",
    "К": "K", "М": "M", "Т": "T", "Н": "H", "В": "B", "І": "I", "Ѕ": "S",
    "Ј": "J",
    "α": "a", "ο": "o", "ρ": "p", "ϲ": "c", "ς": "s", "ν": "v", "κ": "k",
    "μ": "m", "τ": "t",
    "Α": "A", "Β": "B", "Ε": "E", "Ο": "O", "Ρ": "P", "Τ": "T", "Κ": "K",
    "Μ": "M", "Ν": "N", "Η": "H", "Ι": "I", "Χ": "X", "Υ": "Y", "Ζ": "Z",
    # SLASH lookalikes -> '/' (path-separator homoglyphs NFKC does NOT fold:
    # fraction/division/big solidus; fullwidth solidus folds via NFKC but listed
    # for safety).
    "⁄": "/", "∕": "/", "⧸": "/", "／": "/",
    # DOT lookalikes -> '.' (extension-separator homoglyphs: katakana/half-width
    # katakana middle dot, middle dot, hyphenation/one-dot-leader, ano teleia).
    "・": ".", "･": ".", "·": ".", "‧": ".", "․": ".",
    "·": ".",
})


def _norm_kernel_token(raw_tok):
    """Normalize ONE token for a kernel-path/basename match, closing the audit's
    normalization-evasion shapes without over-refusing. First DE-OBFUSCATES unicode
    (drop zero-width/invisible chars, NFKC-fold fullwidth/compatibility forms, map
    common Cyrillic/Greek homoglyphs to Latin), then iterates to a FIXPOINT so
    STACKED shapes fully reduce (`AGENTS.md's.`, `**AGENTS.md**~`):
      * strip edge punctuation that is NOT `.`/`/`/`~` — ASCII + UNICODE quotes/
        brackets, markdown emphasis (`**`, `_`), and leading sigils (`@`, `#`, `-`);
      * strip a trailing possessive (`'s`/`’s`);
      * strip trailing sentence punctuation (a real `.claude` keeps its LEADING dot);
      * strip a TRAILING tilde run always, and a LEADING tilde run UNLESS it is a
        `~/…`/`~user/…` home path (which the resolve check needs intact) — so
        `~~AGENTS.md~~`/`~steering` reduce but `~/agentware` is preserved.
    Returns '' if nothing survives."""
    if not raw_tok:
        return ""
    t = _strip_kernel_invisibles(raw_tok)
    t = unicodedata.normalize("NFKC", t)
    t = t.translate(_KERNEL_CONFUSABLE_TT)
    for _ in range(8):                      # fixpoint (bounded; each pass shrinks t)
        prev = t
        t = _KERNEL_TOK_EDGE_RE.sub("", t)
        if t[-2:].lower() == "'s" or t[-2:] == "’s":
            t = t[:-2]
        t = t.rstrip(".,;:!?").rstrip("~")
        lead = len(t) - len(t.lstrip("~"))
        if lead >= 2 or (lead == 1 and not _KERNEL_HOME_RE.match(t)):
            t = t.lstrip("~")
        if t == prev:
            break
    return t


def _kernel_subtokens(raw_tok):
    """Yield normalized kernel-match sub-tokens from ONE whitespace token: re-split
    list/glue forms on ',', ';', '=', ':', '|' and em/en-dashes (`AGENTS.md,steering`,
    `AGENTS.md=optional`, `AGENTS.md:CLAUDE.md`, `scripts/agentware:AGENTS.md`) — but
    NOT a plain hyphen, so a benign compound (`product-steering`) is NOT split into a
    kernel segment — then normalize each piece via `_norm_kernel_token`. Empty pieces
    are dropped."""
    for piece in re.split(r"[,;=:|—–]+", raw_tok):
        tok = _norm_kernel_token(piece)
        if tok:
            yield tok


def _has_kernel_path_segment(raw):
    """True iff `raw` carries a kernel token as a PATH-BOUNDED match — a single-
    segment token (.claude, steering, AGENTS.md, CLAUDE.md, agentware.sh) EQUAL to
    a '/'-delimited path segment, or a multi-segment token (scripts/agentware,
    scripts/hooks) matched on segment boundaries.

    Segment-bounded (NOT a bare substring) so a legit EXTERNAL path/name that merely
    CONTAINS a kernel token as a substring is NOT over-refused: `/x/product-steering/n`,
    `/x/.claude-backup`, and `my-agents.md-notes` do not false-match (their segments
    are 'product-steering'/'.claude-backup'/'my-agents.md-notes', not the kernel
    tokens). A plain-hyphen GLUE like `AGENTS.md-advisory` is therefore admitted —
    the same pre-existing, consistent trade-off that lets `product-steering` pass;
    `,`/`;`/`=`/em-dash glue IS split by `_kernel_subtokens`."""
    if not raw:
        return False
    segs = [s for s in raw.casefold().replace("\\", "/").split("/") if s]
    if not segs:
        return False
    for tok in _KERNEL_PATH_TOKENS:
        parts = [p for p in tok.casefold().replace("\\", "/").split("/") if p]
        n = len(parts)
        for i in range(len(segs) - n + 1):
            if segs[i:i + n] == parts:
                return True
    return False


def _text_hits_kernel_path(text):
    """g1 text scan: True iff free-form text (task/trigger/evidence) contains a
    PATH-SHAPED token — one bearing a '/' separator — that is a kernel path:
    it carries a kernel token as a bounded path segment (e.g. 'scripts/agentware',
    'foo/AGENTS.md'), resolves under REPO_ROOT (absolute/~/symlink forms), or is a
    `..`-traversal token that resolves into the package. A BARE basename with no
    '/' (AGENTS.md, steering, CLAUDE.md) is NEVER matched, and a benign forward-
    relative token (logs/a.log) still emits, so prose still emits (pitfall b)."""
    if not text:
        return False
    for raw_tok in text.split():
        # Normalize each token (strip wrapping quotes/brackets/emphasis, a trailing
        # tilde, a possessive, trailing punctuation) and re-split list forms — the
        # SAME normalization the bare-basename scan uses, so a path-shaped evasion
        # (`**scripts/agentware**`, `foo/AGENTS.md,bar/steering`) is caught too. A
        # leading '.' / '~' survives so the '../' traversal + '~/…'-into-repo checks
        # still fire.
        for tok in _kernel_subtokens(raw_tok):
            if "/" not in tok:
                continue
            if _has_kernel_path_segment(tok):
                return True
            if _resolves_under_repo(tok):                   # absolute / ~ into repo
                return True
            # A `..`-traversal token is suspicious enough to resolve vs CWD; a
            # benign forward-relative token (logs/a.log) is NOT resolved, so it
            # still emits.
            if tok.startswith("..") and _resolves_under_repo(
                    tok, allow_relative=True):
                return True
    return False


def _pref_hits_kernel(text):
    """(b) kernel-path refusal — True iff `text` names any kernel/package path,
    INCLUDING a bare kernel basename (AGENTS.md, steering, agentware.sh) that the
    '/'-filtered `_text_hits_kernel_path` is deliberately blind to. Tokenize the
    text and run the per-token `_has_kernel_path_segment` in ADDITION to the
    path-shaped scan, over BOTH the raw text AND an invisible-stripped variant."""
    if not text or not isinstance(text, str):    # tolerate non-str queue text
        return False
    # Check TWO variants: (1) the raw text — where `str.split()` treats a unicode
    # space (NBSP) as a SEPARATOR, so `foo<NBSP>AGENTS.md` surfaces `AGENTS.md`; and
    # (2) an invisible-stripped variant, where an invisible space INSIDE a token
    # (`AGENTS<NBSP>.md` / `AGENTS<ZWSP>.md`) is REMOVED so the basename re-forms.
    # Together they close both the split-hiding and glue-hiding invisible evasions.
    variants = [text]
    stripped = _strip_kernel_invisibles(text)
    if stripped != text:
        variants.append(stripped)
    for variant in variants:
        if _text_hits_kernel_path(variant):
            return True
        # `_kernel_subtokens` closes the audit's normalization-evasion shapes
        # (markdown emphasis, tilde, possessive, comma/`=`/em-dash glue, unicode
        # quotes/sigils/homoglyphs) per token before `_has_kernel_path_segment`.
        for raw_tok in variant.split():
            for tok in _kernel_subtokens(raw_tok):
                if _has_kernel_path_segment(tok):
                    return True
    return False


def _target_hits_kernel(target):
    """g1: does a machine-plan `--target` resolve into the agentware package / a
    kernel path? True for the package root itself, anything inside it (symlink/
    case/traversal/RELATIVE-proof via _resolves_under_repo(allow_relative=True)),
    and any path carrying a kernel token as a bounded path SEGMENT. The security
    boundary for machine-generated plans (`plan new --auto`) — the P2c miner must
    not launch behind a leaky gate."""
    if not target:
        return False
    raw = target.strip()
    if not raw:
        return False
    if _has_kernel_path_segment(raw):
        return True
    # Targets ARE resolved relative to CWD (allow_relative=True): a relative
    # `../agentware` / `.` reaches the package at loop-execution time.
    return _resolves_under_repo(raw, allow_relative=True)


_EXPORTS = [
    "_KERNEL_PATH_TOKENS",
    "_KERNEL_TOK_EDGE_RE",
    "_KERNEL_HOME_RE",
    "_KERNEL_CONFUSABLE_TT",
    "_resolves_under_repo",
    "_strip_kernel_invisibles",
    "_norm_kernel_token",
    "_kernel_subtokens",
    "_has_kernel_path_segment",
    "_text_hits_kernel_path",
    "_pref_hits_kernel",
    "_target_hits_kernel",
]

__all__ = list(_EXPORTS)
