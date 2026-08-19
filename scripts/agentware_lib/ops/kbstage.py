"""agentware_lib.ops.kbstage -- WHAT MAY BE STAGED into a knowledge-base commit (layer: ops).

Owns: the team-scope prefix (`kb_rel_prefix`), the prefix strip
(`_strip_rel_prefix`), the derived-file predicates (`_is_derived_rel` /
`_is_derived_rel_scoped`), the committed-sink predicate (`_is_committed_sink`),
the allowlist itself (`kb_git_stage_allowlist`) and the ONE staging seam every KB
write goes through (`kb_git_stage_scoped`).
Does NOT own: the `kb-git` COMMANDS that call it (commit / pull / push) ->
ops.staging; the org constants it scopes against -> core.orgkeys; the git runner
-> core.gitio.

WHY THIS IS THE ONE SEAM
------------------------
A blanket `git add -A` stages whatever happens to be in the tree. In org mode
that is ANOTHER TEAM'S FILES committed as part of this team's merge — a
cross-team write that looks completely ordinary in the log, which is the worst
property a write can have. It also stages the derived set (gitignored and
rebuilt, so committing it guarantees a conflict on the next sync) and whatever
40MB artifact someone dropped in the directory.

The allowlist answers one question per pending path: MAY this be staged? Team
scope first (a path outside the caller's own folder is REFUSED, not silently
included), then the derived drop, then the artifact-extension and blob-size
guards, with an explicit allow for the committed sink files that legitimately
carry data (`teams/*/fleet/*.jsonl`, the canonical benchmark ledger
`benchmarks/history.jsonl` and its per-suite siblings `benchmarks/history-*.jsonl`,
`configurations/assets/*.png`) — see `_is_committed_sink`.

`kb_git_stage_scoped(allow_derived=True)` re-admits the derived set for the ONE
caller that needs it: the post-merge reconciliation, which just REGENERATED those
files from the merged entry union and must commit that regeneration rather than
leave the tree dirty. It is a parameter rather than a second function precisely
so every call site that does NOT pass it is visibly not asking for it.

WHY IT SPLIT FROM ops.staging
-----------------------------
`staging.py` crossed the 600-line file cap when the scoped stager landed. That
cap allows no debt, and the seam it forced is the honest one: this module answers
"what may be staged", `ops.staging` answers "what do the kb-git commands do".
Those are different questions with different reasons to change — the first tracks
the org layout and the gitignore contract, the second tracks the CLI surface.

Layer deps: ops -> core (`gitio` for the git runner, `orgkeys` for the org dir
names). Read-only apart from the single `git add` in `kb_git_stage_scoped`.
Determinism: the allowlist is a pure fold over `git_changed_paths` output, so the
same working tree stages the same set every run (INV-1).
"""

import os

import agentware_lib.core.orgkeys as orgkeys
import agentware_lib.knowledge.index as index
from agentware_lib.core.gitio import _git
from agentware_lib.ops.kbgit import git_changed_paths, git_toplevel


KB_BLOB_MAX_BYTES = 262144  # 256 KiB — reject an oversized blob from the KB commit
# The CANONICAL append-only benchmark ledger, KB-root-relative. This is
# `retrieval.ledger.HISTORY_REL` restated as a literal: ops must not import
# retrieval to know one filename, and the value is frozen by the ledger's own
# append-only contract. Named here because it is the ONE sink whose size grows
# monotonically with every `eval --record`, so it is guaranteed to cross
# KB_BLOB_MAX_BYTES eventually — and on the day it does, an unnamed ledger stops
# being committed while every gate that reads it keeps reporting the last
# committed baseline as current.
_KB_BENCH_LEDGER_REL = "benchmarks/history.jsonl"
# Artifact extensions never staged by the KB auto-commit (heavy/binary/derived).
# NOTE: fleet *.jsonl + benchmarks history.jsonl / history-*.jsonl + assets *.png
# are text/committed sink data and are ALLOWED explicitly below — this set is for
# stray heavy blobs.
_KB_STAGE_REJECT_EXTS = (".har", ".zip", ".tar", ".gz", ".mp4", ".mov", ".pdf",
                         ".trace", ".webm")


def kb_rel_prefix(target):
    """The kdir's path RELATIVE to its git work-tree root ('' when kdir IS the root).

    Org mode: kdir == <repo>/teams/<team> → 'teams/<team>'. Flat mode: '' (INV-1).
    Returns '' on any resolution failure (degrade to today's behavior).
    """
    top = git_toplevel(target)
    if not top:
        return ""
    try:
        rel = os.path.relpath(os.path.realpath(target), top).replace(os.sep, "/")
    except ValueError:
        return ""
    return "" if rel == "." else rel


def _strip_rel_prefix(rel, rel_prefix):
    """Drop a leading 'rel_prefix/' from a repo-relative path (kdir-relative out).

    Returns None if `rel` is OUTSIDE rel_prefix (a different team / shared root /
    repo-root file) — the caller treats None as 'not this KB's business'.
    """
    rel = rel.strip().strip('"')
    if not rel_prefix:
        return rel
    pfx = rel_prefix.rstrip("/") + "/"
    if rel.startswith(pfx):
        return rel[len(pfx):]
    if rel == rel_prefix:
        return ""
    return None


def _is_derived_rel_scoped(rel, rel_prefix=""):
    """rel_prefix-aware _is_derived_rel: classify a REPO-relative path.

    Strips rel_prefix, then applies the flat-layout derived-file rule. A path
    outside rel_prefix is NOT this KB's derived file → False.
    """
    inner = _strip_rel_prefix(rel, rel_prefix)
    if inner is None:
        return False
    return _is_derived_rel(inner)


def _is_committed_sink(inner, base):
    """True iff this KB-root-relative path is a COMMITTED SINK.

    A sink is append-only data the KB is meant to carry: fleet terminal-event
    shards, the benchmark ledger (canonical + per-suite), and configuration
    assets. It is exempt from BOTH guards in `kb_git_stage_allowlist` -- the
    artifact-extension reject and the `KB_BLOB_MAX_BYTES` blob cap -- because a
    sink is expected to be large and to keep growing; that is what makes it a
    sink rather than a stray blob.

    Extracted from the caller (task 59), which was AT its declared branch debt:
    the sink rule is one decision, and inlining it as a 4-clause boolean charged
    every clause to a function whose real job is the per-path fold. Pure: no I/O,
    no clock -- `inner`/`base` in, bool out. `base` is passed in rather than
    re-split so caller and predicate can never disagree about what the basename
    is.
    """
    if ("/" + orgkeys.ORG_FLEET_DIR + "/") in ("/" + inner) and inner.endswith(".jsonl"):
        return True
    if inner == _KB_BENCH_LEDGER_REL:
        return True
    if base.startswith("history-") and inner.endswith(".jsonl"):
        return True
    if "/configurations/assets/" in ("/" + inner):
        return base.lower().endswith((".png", ".jpg", ".jpeg", ".svg", ".gif"))
    return False


def kb_git_stage_allowlist(target, rel_prefix=None):
    """Repo-relative paths this KB commit SHOULD stage — team-scoped + guarded.

    Replaces a blanket `git add -A`. Rules, applied to each pending
    path from `git_changed_paths`:
      - MUST be inside rel_prefix (the caller's team folder / KB root). A path in a
        DIFFERENT team folder or the shared root is REFUSED here (cross-team write
        isolation) — those are staged only via their own team's loop / the curator
        --to-shared path.
      - DROP derived files (index.json / FEATURES.md / <sub>/index.md rosters):
        they are gitignored + rebuilt, never committed.
      - DROP an artifact-ext blob or any blob > KB_BLOB_MAX_BYTES (except the
        committed sink files: teams/*/fleet/*.jsonl, the canonical benchmark
        ledger benchmarks/history.jsonl and its per-suite siblings
        benchmarks/history-*.jsonl, configurations/assets/*.png — those are
        allowed text/data).
    Returns (allow, rejected) where `rejected` is [(path, reason)] for observability.
    Deterministic, read-only. When rel_prefix == '' (flat KB) it still enforces the
    derived/artifact drops but never rejects for team scope.
    """
    if rel_prefix is None:
        rel_prefix = kb_rel_prefix(target)
    allow, rejected = [], []
    for rel in git_changed_paths(target):
        inner = _strip_rel_prefix(rel, rel_prefix)
        if inner is None:
            rejected.append((rel, "outside caller team folder (cross-team write refused)"))
            continue
        if _is_derived_rel(inner):
            continue  # derived → silently skip (regenerable)
        base = inner.rsplit("/", 1)[-1]
        is_sink = _is_committed_sink(inner, base)
        ext = os.path.splitext(base)[1].lower()
        if not is_sink and ext in _KB_STAGE_REJECT_EXTS:
            rejected.append((rel, "artifact extension %s not staged" % ext))
            continue
        # Blob-size guard (skip sink files; a HAR-size .md is still rejected).
        if not is_sink:
            try:
                sz = os.path.getsize(os.path.join(target, inner))
                if sz > KB_BLOB_MAX_BYTES:
                    rejected.append((rel, "blob %d bytes > %d cap" % (sz, KB_BLOB_MAX_BYTES)))
                    continue
            except OSError:
                pass
        allow.append(rel)
    return allow, rejected


def kb_git_stage_scoped(target, rel_prefix=None, allow_derived=False):
    """Stage this KB's pending paths through the SCOPED ALLOWLIST.

    The one seam every KB write goes through, so `git add -A` appears nowhere. A
    blanket add stages whatever happens to be in the tree — another team's files
    in org mode, a 40MB HAR someone dropped in, a derived index.json that is
    gitignored and rebuilt — and the commit that results is not the commit anyone
    intended. Worse, in org mode it is a CROSS-TEAM WRITE that looks like a normal
    commit in the log.

    `allow_derived=True` re-admits the derived files the allowlist normally drops.
    Exactly one caller needs it — the post-merge reconciliation, which REGENERATES
    the derived set from the merged entry union and must commit that regeneration
    rather than leave the tree dirty. It is a parameter rather than a second
    function so the exception is visible at every call site that does NOT pass it.

    Returns (rc, staged_count, rejected). rc is nonzero only when `git add`
    itself fails; "nothing to stage" is rc 0 with a count of 0, because an empty
    allowlist is a legitimate outcome (only derived/out-of-scope changes pending)
    and not an error to abort a merge over.
    """
    if rel_prefix is None:
        rel_prefix = kb_rel_prefix(target)
    allow, rejected = kb_git_stage_allowlist(target, rel_prefix)
    if allow_derived:
        for rel in git_changed_paths(target):
            inner = _strip_rel_prefix(rel, rel_prefix)
            if inner is not None and _is_derived_rel(inner) and rel not in allow:
                allow.append(rel)
        rejected = [(p, r) for (p, r) in rejected]
    if not allow:
        return 0, 0, rejected
    # REPO-ROOT-relative -> ABSOLUTE, because `git -C <target> add` resolves a
    # pathspec relative to <target>; in org mode that would look for
    # `teams/<t>/teams/<t>/…` and fail. Absolute paths are unambiguous whatever
    # git's cwd is, and a flat KB (toplevel == target) is unaffected.
    top = git_toplevel(target) or target
    add = _git(target, "add", "--", *[os.path.join(top, p) for p in allow])
    return add.returncode, len(allow), rejected


def _is_derived_rel(rel):
    """True iff a KB-relative path is a DERIVED file regenerated by `index rebuild`.

    The derived set is exactly `index.json`, `FEATURES.md`, and each
    `<sub>/index.md` roster (sub in KNOWLEDGE_SUBDIRS). These are NEVER merged by
    hand or by an agent (C-1) — conflicts in them are resolved deterministically
    by rebuilding from the union of entry frontmatter.
    """
    rel = rel.strip().strip('"')
    if rel in ("index.json", "FEATURES.md"):
        return True
    parts = rel.split("/")
    return (len(parts) == 2 and parts[1] == "index.md"
            and parts[0] in index.KNOWLEDGE_SUBDIRS)


_EXPORTS = [
    "kb_rel_prefix",
    "_is_committed_sink",
    "_strip_rel_prefix",
    "_is_derived_rel",
    "_is_derived_rel_scoped",
    "kb_git_stage_allowlist",
    "kb_git_stage_scoped",
]

__all__ = list(_EXPORTS)
