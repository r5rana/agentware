"""agentware_lib.knowledge.privacy — personal-data leak guard (R-LOC-03) (layer: knowledge).

Owns: the read-only personal-data scan that keeps operator identity out of the
package tree — operator_identity_strings (resolve the LIVE handle/email/HOME to
the literal needles a leak would contain), _operator_email (best-effort email
from MAIN.md), _package_files (enumerate exactly what the package ships via
`git ls-files`, with a pruned-walk fallback), and scan_personal_data (the needle
scan itself) — plus the PERSONAL_SCAN_EXCLUDE_DIRS / _GENERIC_HANDLES / _EMAIL_RE
constants those functions read.
Does NOT own: the operator handle PARSE (agentware_lib.core.frontmatter:
_operator_handle); the knowledge-dir resolution (agentware_lib.core.config:
resolve_knowledge_dir); the release-gate personal-data CHECK and the
redact_gold_path caller (still entrypoint-resident; join agentware_lib.ops /
retrieval in later waves and call privacy.* downward).
Layer deps: knowledge -> core (config, frontmatter); nothing above. Every op is
read-only (INV-2): it inspects structure and never writes, and resolves the
operator identity entirely at runtime so the package still ships ZERO personal
data. Output is a sorted, deterministic hit list given the same tree (INV-1).
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import os
import re
import subprocess

import agentware_lib.core.config as config
import agentware_lib.core.frontmatter as frontmatter

# Directories NEVER scanned for operator identity: VCS metadata, the machine-
# local config (it legitimately stores the KB path, which contains HOME),
# spawn/prompt logs, caches, and vendored deps. This mirrors .gitignore so the
# guard only ever inspects what the package actually ships/pushes.
PERSONAL_SCAN_EXCLUDE_DIRS = (
    ".git", ".agentware", ".agentware-logs", "__pycache__",
    ".venv", "node_modules", ".idea", ".vscode",
)
# Generic / fallback handles that are NOT personal data. Scanning for these
# would match framework prose everywhere; "operator" is also the _operator_handle
# fallback. Compared case-insensitively.
_GENERIC_HANDLES = ("operator", "user", "handle", "admin")
# Email-shaped token, used to lift an operator email out of MAIN.md if present.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _operator_email(kdir):
    """Best-effort operator email parsed from the KB MAIN.md, or None.

    Reads the LIVE profile at runtime (hardcodes nothing). Returns the first
    email-shaped token found in MAIN.md, else None. Tolerates a None/empty kdir.
    """
    if not kdir:
        return None
    try:
        with open(os.path.join(kdir, "MAIN.md"), "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    m = _EMAIL_RE.search(text)
    return m.group(0) if m else None


def operator_identity_strings(kdir):
    """Resolve the LIVE operator identity to the set of literal strings whose
    presence in the package tree would be a personal-data leak (R-LOC-03).

    Resolved entirely at runtime — hardcodes nothing — so the package still ships
    zero personal data. Sources: the OPERATOR handle (MAIN.md/config), the PER-USER
    handle (`user_handle` in config), the email (MAIN.md, if present), and the
    operator HOME path. Generic/fallback handles are dropped (they would match
    framework prose), as are tokens shorter than 3 chars.

    The per-user handle is a SECOND, INDEPENDENT identity and must be scanned in
    its own right: `routing.resolve_user_handle` reads `user_handle` from config
    while `frontmatter._operator_handle` reads MAIN.md, and the two routinely
    differ. Org/team mode stamps the PER-USER handle onto every entry and every
    committed work artifact (R-ORG-05), so a guard that only knew the operator
    handle would report a tree clean while the other identity sat committed in it —
    which is exactly how one reached `tests/baselines/baseline-local.json` and
    survived a redaction pass driven by this function.
    """
    strings = set()
    handle = frontmatter._operator_handle(kdir)
    if handle and handle.lower() not in _GENERIC_HANDLES:
        strings.add(handle)
    try:
        import agentware_lib.core.routing as routing
        user_handle = routing.resolve_user_handle()
    except Exception:
        user_handle = None
    if user_handle and user_handle.lower() not in _GENERIC_HANDLES:
        strings.add(user_handle)
    email = _operator_email(kdir)
    if email:
        strings.add(email)
    home = os.path.expanduser("~")
    if home and home not in ("", "/", "~"):
        strings.add(home)
    return {s for s in strings if len(s) >= 3}


def _package_files(root):
    """Yield (abspath, relpath) for the files that make up the package at `root`.

    Prefers `git ls-files` so the guard inspects EXACTLY what the package
    ships/pushes — which naturally excludes machine-local, gitignored files
    (e.g. `.claude/settings.local.json`, `.agentware/`, `.agentware-logs/`,
    caches). Falls back to a pruned filesystem walk when `root` is not a git work
    tree (e.g. the unit-test temp dirs).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", root, "ls-files", "-z"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode == 0:
            for rel in proc.stdout.decode("utf-8", "replace").split("\0"):
                if rel:
                    yield os.path.join(root, rel), rel
            return
    except OSError:
        pass
    kdir = config.resolve_knowledge_dir()
    kdir_abs = os.path.abspath(kdir) if kdir else None
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in PERSONAL_SCAN_EXCLUDE_DIRS
            and not (kdir_abs and os.path.abspath(
                os.path.join(dirpath, d)) == kdir_abs)
        ]
        for name in filenames:
            fpath = os.path.join(dirpath, name)
            yield fpath, os.path.relpath(fpath, root)


def scan_personal_data(root, needles):
    """Scan the package's files under `root` for any of the literal `needles`.

    Returns a sorted list of human-readable hit strings (empty list == clean).
    Inspects only what the package ships (see `_package_files`) and silently
    skips non-text/binary files (decode errors). Read-only (INV-2).
    """
    needles = [n for n in (needles or []) if n]
    if not needles:
        return []
    hits = []
    for fpath, rel in _package_files(root):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(lines, 1):
            for n in needles:
                if n in line:
                    hits.append(
                        "%s:%d leaks operator identity %r" % (rel, i, n))
    hits.sort()
    return hits


#: Placeholders substituted for each identity class. Deliberately BRACKETED so a
#: redacted artifact reads as obviously-synthetic rather than as some other
#: operator's real handle, and so a reviewer grepping for `<redacted-` finds every
#: site at once.
REDACTION_PLACEHOLDERS = (
    ("/", "<redacted-home>"),      # any needle containing a path separator
    ("", "<redacted-handle>"),     # everything else (handle, email, hostname)
)


def redact_operator_identity(text, needles):
    """Return `text` with every operator-identity needle replaced by a placeholder.

    The WRITE-SIDE counterpart to `scan_personal_data`: that function finds a leak
    once it is committed, this one stops a generator from committing it. Both read
    the SAME needle set from `operator_identity_strings`, so a redactor can never
    drift from the scanner that grades it — the failure mode where a generated
    artifact is "cleaned" against a narrower notion of identity than the gate uses.

    Needles are applied LONGEST-FIRST so a HOME path (`/Users/alice`) is redacted
    before the handle it contains (`alice`), which would otherwise leave a mangled
    `/Users/<redacted-handle>` behind and defeat the point.

    Pure text in, text out — no I/O, no filesystem access (INV-2). A falsy `text`
    or empty needle set returns the input unchanged.
    """
    if not text or not needles:
        return text
    for needle in sorted((n for n in needles if n), key=len, reverse=True):
        placeholder = next(ph for token, ph in REDACTION_PLACEHOLDERS
                           if token == "" or token in needle)
        text = text.replace(needle, placeholder)
    return text


_EXPORTS = [
    "PERSONAL_SCAN_EXCLUDE_DIRS",
    "_GENERIC_HANDLES",
    "_EMAIL_RE",
    "REDACTION_PLACEHOLDERS",
    "_operator_email",
    "operator_identity_strings",
    "redact_operator_identity",
    "_package_files",
    "scan_personal_data",
]
