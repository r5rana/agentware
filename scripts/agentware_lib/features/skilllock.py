"""agentware_lib.features.skilllock -- the per-machine skill EXECUTION CACHE lockfile (layer: features).

Owns: the lockfile schema and its location (`SKILL_LOCK_SCHEMA`,
`_skill_lock_path`), its readers and writer (`load_skill_lock`,
`save_skill_lock`, `_empty_skill_lock`), the per-skill record
(`_skill_entry`), the ownership test (`_skill_owned`), the content hash over a
skill TREE (`_skill_tree_sha256`, `_SkillHashError`, `_skill_is_binary`), the
cache location (`_skill_cache_dir`), and the name/display guards
(`_SKILL_NAME_RE`, `_SKILL_DISPLAY_RE`, `_skill_name_ok`,
`_skill_sanitize_display`, `_skill_tree_link`).
Does NOT own: the reconcile/verdict/install machinery that USES this ->
features.skillsync; the KB-side skill catalog and its authoring verbs ->
features.skills; the proposal lifecycle -> features.proposals.

WHY A LOCKFILE AT ALL
---------------------
A skill in the knowledge base is TEXT. A skill in the execution cache is
something a harness will RUN. The lockfile is the record of which of those two
an operator actually approved, and of the exact content they approved it at —
the tree hash. Without it, "is this skill installed?" and "is this the skill I
installed?" are the same question with no way to tell them apart, and a skill
whose body changed after approval executes on an approval that was never given.

OWNERSHIP IS RECORDED, NOT ASSUMED
----------------------------------
The cache is a shared directory a harness also writes (`~/.claude/skills`), so
this toolkit must be able to tell ITS copies from a foreign one. `_skill_owned`
answers that from the lockfile rather than from the filesystem, because a
directory's existence says nothing about who put it there — and removing
somebody else's skill because it shares a name is the kind of destructive
surprise that ends trust in a tool permanently.

HASHING A TREE, NOT A FILE
--------------------------
A skill is a directory. `_skill_tree_sha256` hashes the sorted relative paths
AND their contents, so a rename, an added file, or a one-byte edit all move the
hash. Binary members are hashed by bytes rather than decoded (`_skill_is_binary`)
— refusing to hash a PNG would make an illustrated skill unapprovable, and
decoding it would make the hash locale-dependent.

Layer deps: features -> core (`config.HOME_CONFIG`, `dates.utc_now_iso`,
`gitio._git` for the display-only author lookup), features -> ops
(`planauthor.planedit._atomic_write` — the durable write this toolkit already
uses everywhere, rather than a second one). Pure and read-only apart from
the lockfile write. Determinism: the tree hash sorts before hashing, so the same
tree hashes identically on every machine and the verdict is reproducible (INV-1).
"""

import hashlib
import json
import os
import re
import shutil

from agentware_lib.core.config import HOME_CONFIG
from agentware_lib.core.dates import utc_now_iso
from agentware_lib.core.gitio import _git
from agentware_lib.ops.planauthor.planedit import _atomic_write


SKILL_LOCK_SCHEMA = 1


class _SkillHashError(Exception):
    """Raised by _skill_tree_sha256 when a tree cannot be hashed safely
    (a symlink or unreadable content is present). Callers that vet treat this as
    BLOCK; the cache-integrity check treats it as divergence (drop + requarantine)."""


def _skill_lock_path():
    """Absolute path to the machine-local skills lockfile — the trust boundary.

    Default `~/.agentware/skills.lock.json` (beside HOME_CONFIG), NEVER inside a
    KB (a poisoned pull must not be able to edit its own allowlist). Honors the
    `AGENTWARE_SKILLS_LOCK_PATH` env override (operator/harness-set, NOT reachable
    over the sync channel) so hermetic tests can redirect it — HOME_CONFIG is
    bound at import time and cannot be redirected in-process.
    """
    override = os.environ.get("AGENTWARE_SKILLS_LOCK_PATH")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(os.path.dirname(HOME_CONFIG), "skills.lock.json")


def _empty_skill_lock():
    return {"schema": SKILL_LOCK_SCHEMA, "skills": {}}


def load_skill_lock(path=None):
    """Load the skills lockfile -> dict. Tolerant: a missing/corrupt/legacy file
    yields a fresh empty lock (never raises). Read-only."""
    p = path or _skill_lock_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _empty_skill_lock()
    if not isinstance(data, dict) or not isinstance(data.get("skills"), dict):
        return _empty_skill_lock()
    data.setdefault("schema", SKILL_LOCK_SCHEMA)
    return data


def save_skill_lock(data, path=None):
    """Atomically persist the skills lockfile (temp sibling + os.replace). The
    lockfile lives OUTSIDE any KB (asserted by _skill_lock_path)."""
    p = path or _skill_lock_path()
    if not isinstance(data, dict):
        data = _empty_skill_lock()
    data.setdefault("schema", SKILL_LOCK_SCHEMA)
    data.setdefault("skills", {})
    _atomic_write(p, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _skill_name_ok(name):
    """True iff `name` is a safe skill directory basename: strict
    `[a-z0-9._-]` allowlist, lowercase, bounded length, no leading dot / slash /
    traversal / newline. Enforced BEFORE any name reaches the lockfile / cache /
    roster so an attacker-crafted KB directory name can never inject."""
    if not isinstance(name, str) or not name or name in (".", ".."):
        return False
    return bool(_SKILL_NAME_RE.match(name))


def _skill_tree_link(skill_dir):
    """Return the first symlink relpath (a directory OR file) under `skill_dir`,
    else None. Walks with followlinks=False AND checks os.path.islink on every
    entry (dirs included), so a symlinked SUBDIR is detected — closing the
    os.walk(followlinks=False) vs copytree(symlinks=False) inconsistency."""
    if not os.path.isdir(skill_dir):
        return None
    if os.path.islink(skill_dir):
        return "."
    for root, dirs, files in os.walk(skill_dir, followlinks=False):
        for nm in sorted(dirs) + sorted(files):
            p = os.path.join(root, nm)
            if os.path.islink(p):
                return os.path.relpath(p, skill_dir)
    return None


def _skill_tree_sha256(skill_dir):
    """Deterministic, mode-aware, symlink-FREE content hash of a skill tree.

    One sha256 over the SORTED list of `(relpath, oct(mode & 0o777),
    sha256(file-bytes))` for every regular file under `skill_dir`. Order-
    independent (sorted); includes the executable mode bit (a 0644->0755 flip
    changes the hash); covers every file (a bundled-script swap changes the
    hash). RAISES _SkillHashError if any symlink is present (consistent with the
    copy/vet symlink refusal) or a file cannot be read. Read-only."""
    link = _skill_tree_link(skill_dir)
    if link is not None:
        raise _SkillHashError("symlink present in skill tree: %s" % link)
    if not os.path.isdir(skill_dir):
        raise _SkillHashError("skill tree not found: %s" % skill_dir)
    parts = []
    for root, _dirs, files in os.walk(skill_dir, followlinks=False):
        for fn in files:
            path = os.path.join(root, fn)
            if os.path.islink(path):
                raise _SkillHashError("symlink present in skill tree: %s"
                                      % os.path.relpath(path, skill_dir))
            rel = os.path.relpath(path, skill_dir)
            try:
                with open(path, "rb") as f:
                    fb = f.read()
            except OSError as exc:
                raise _SkillHashError("unreadable file %s: %s" % (rel, exc))
            mode = oct(os.stat(path).st_mode & 0o777)
            parts.append((rel, mode, hashlib.sha256(fb).hexdigest()))
    parts.sort()
    h = hashlib.sha256()
    for rel, mode, digest in parts:
        h.update(("%s\0%s\0%s\n" % (rel, mode, digest)).encode("utf-8"))
    return h.hexdigest()


def _skill_is_binary(path):
    """True iff `path` is non-text (undecidable to the vetter): contains a NUL
    byte OR fails a strict UTF-8 decode. Explicit — do NOT rely on
    vet_skill_dir's errors="replace" (which never reports a real file as
    unreadable, so a binary/obfuscated blob would otherwise PASS)."""
    try:
        with open(path, "rb") as f:
            chunk = f.read()
    except OSError:
        return True  # unreadable -> undecidable -> not text
    if b"\x00" in chunk:
        return True
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


_SKILL_DISPLAY_RE = re.compile(r"[^A-Za-z0-9 <>@._-]")


def _skill_sanitize_display(s):
    """Strip a display string to a safe charset ([A-Za-z0-9 <>@._-]) and truncate.
    Applied to the git author BEFORE it enters the lockfile so an attacker-
    controlled ident (settable via `git commit --author=` over the unsigned pull)
    carries no shell/markup metacharacters and is length-bounded (R-SEC-02). NOTE:
    the safe charset still permits words + spaces, so a sanitized value can still
    read as prose — therefore authored_by is NOT rendered to ANY agent-facing sink
    (neither --status-line nor --status render it; it is internal lockfile metadata
    only). This function is defense-in-depth on the stored value."""
    if not s:
        return ""
    cleaned = _SKILL_DISPLAY_RE.sub("", str(s)).strip()
    return cleaned[:80]


def _skill_author(kdir, name):
    """Best-effort, DISPLAY-ONLY, SANITIZED first-commit git author of
    skills/<name>/ (git %an <%ae>). Returns "" when unavailable. NEVER gates
    install and NEVER changes disposition — identity is not a security signal;
    the value is attacker-controllable over the unsigned pull, so it is sanitized
    here and shown only under an "(UNVERIFIED)" label. Read-only."""
    rel = os.path.join("skills", name)
    cp = _git(kdir, "log", "--reverse", "--format=%an <%ae>", "-1", "--", rel)
    if cp.returncode != 0:
        return ""
    line = (cp.stdout or "").splitlines()
    return _skill_sanitize_display(line[0] if line else "")


def _skill_cache_dir(args=None, lock=None):
    """Resolve the per-machine execution-cache dir. Precedence: --harness-path >
    AGENTWARE_SKILLS_HARNESS_DIR > the lockfile-recorded harness_dir (so the
    argless post-pull reconcile reconciles the SAME cache a flag-based --approve
    installed into — feature 260713-p4 S3) > ~/.claude/skills. Never synced."""
    if args is not None and getattr(args, "harness_path", None):
        return os.path.abspath(os.path.expanduser(args.harness_path))
    env = os.environ.get("AGENTWARE_SKILLS_HARNESS_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if lock and lock.get("harness_dir"):
        return os.path.abspath(os.path.expanduser(lock["harness_dir"]))
    return os.path.join(os.path.expanduser("~"), ".claude", "skills")


def _skill_rmtree(path):
    """Remove a cache entry if present. Returns True iff it existed and was removed.

    A SYMLINK is unlinked EXPLICITLY (feature 260713-p4): `shutil.rmtree` refuses a
    symbolic link and, with ignore_errors=True, would silently NO-OP — leaving a
    symlink-tampered owned cache dir invocable while the remediation paths (cache-
    integrity divergence, --reject/--uninstall/--reset/GC) falsely report removal.
    Use `lexists` so a broken symlink is also caught."""
    if os.path.islink(path):
        try:
            os.unlink(path)
        except OSError:
            return False
        return True
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
        return True
    if os.path.lexists(path):  # a non-dir, non-symlink file at the cache name
        try:
            os.unlink(path)
        except OSError:
            return False
        return True
    return False


def _skill_owned(lock, name):
    """OWNERSHIP BY PROVENANCE (D12): we own <name> iff a lockfile entry records
    status:'installed' — NEVER by whether the on-disk hash still matches (a
    divergent/tampered copy is still ours to remediate, R3-1/R3-2)."""
    e = lock.get("skills", {}).get(name)
    return bool(e) and e.get("status") == "installed"


def _skill_entry(kdir, name, tree_hash, status, verdict, findings):
    """Build a lockfile entry. `authored_by` is sanitized+display-only (D2);
    `reason` is stored for diagnostics but NEVER rendered to an agent-facing
    sink (roster/status render only enum status/verdict + sanitized name/author)."""
    return {
        "tree_sha256": tree_hash,
        "status": status,
        "verdict": verdict,
        "authored_by": _skill_author(kdir, name),
        "vetted_at": utc_now_iso(),
        "source_rel": os.path.join("skills", name),
        "reason": ("; ".join(findings)[:200] if findings else None),
    }


_EXPORTS = [
    "SKILL_LOCK_SCHEMA",
    "_SKILL_NAME_RE",
    "_SKILL_DISPLAY_RE",
    "_SkillHashError",
    "_empty_skill_lock",
    "_skill_lock_path",
    "load_skill_lock",
    "save_skill_lock",
    "_skill_entry",
    "_skill_owned",
    "_skill_cache_dir",
    "_skill_name_ok",
    "_skill_sanitize_display",
    "_skill_tree_link",
    "_skill_is_binary",
    "_skill_tree_sha256",
    "_skill_rmtree",
    "_skill_author",
]

__all__ = list(_EXPORTS)
