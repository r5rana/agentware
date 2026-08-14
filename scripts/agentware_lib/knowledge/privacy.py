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
    zero personal data. Sources: the handle (MAIN.md/config), the email (MAIN.md,
    if present), and the operator HOME path. Generic/fallback handles are dropped
    (they would match framework prose), as are tokens shorter than 3 chars.
    """
    strings = set()
    handle = frontmatter._operator_handle(kdir)
    if handle and handle.lower() not in _GENERIC_HANDLES:
        strings.add(handle)
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


_EXPORTS = [
    "PERSONAL_SCAN_EXCLUDE_DIRS",
    "_GENERIC_HANDLES",
    "_EMAIL_RE",
    "_operator_email",
    "operator_identity_strings",
    "_package_files",
    "scan_personal_data",
]
