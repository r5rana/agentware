"""agentware_lib.ops.plangraph — the plan document's DECLARATIVE BLOCK parsers (layer: ops).

Owns: every `plan.md` block that declares a FACT ABOUT THE PLAN AS A NODE rather
than about its tasks — the R11 target-packages contract
(`_PLAN_TARGET_SECTION_RE` / `_PLAN_TARGET_PATH_RE` / `plan_target_dirs`), the
`## Depends-on:` predecessor-edge grammar (`_PLAN_DEPENDS_SECTION_RE` /
`_PLAN_DEPENDS_KINDS` / `_PLAN_EDGE_MALFORMED` / `_PLAN_SLUG_RE` /
`plan_depends_on`), the `## Write set` declaration
(`_PLAN_WRITESET_SECTION_RE` / `_PLAN_SERIALIZE_ON_RE` / `plan_write_set`) with
its conservative INFERENCE fallback (`_WRITE_SET_WHOLE_REPO` /
`_write_token_norm` / `_KERNEL_PATH_TOKENS` / `_kernel_write_tokens` /
`_write_set_repo_rel` / `infer_write_set`), the `> Checkpoint:` review
barrier (`_CHECKPOINT_POLICIES` / `_PLAN_CHECKPOINT_RE` / `plan_checkpoint`), and
the `> Status:` lifecycle header (`_PLAN_STATES` / `_PLAN_DEFAULT_STATE` /
`_PLAN_STATUS_HEADER_RE` / `_read_plan_status`), re-exported by
`ops.planauthor.planstate` where it first landed.

Does NOT own: any RULE. Nothing here decides whether a plan is well-formed — R11
and R13 and R15/R16 are `ops.planlint`, which imports THESE parsers; the plan
STATE MACHINE (the legal-transition matrix and the set-state/claim WRITERS) is
`ops.planauthor.planstate`; the TASK-MARKER grammar (what a task line looks like,
as against what the plan-as-a-node declares) is `ops.planmarkers`; the graph MANIFEST
(the canonical hashed source these blocks merely SEED) and the DAG scheduler are
`plan graph` (still RESIDENT, task 48), which imports `_write_token_norm` for its
overlap test. Nor the `## Premises` block: that is a TRUST BOUNDARY with a closed
non-executing grammar and lives alone in `ops.planpremises` so "nothing a plan
declares is ever shell-executed" stays provable by reading one file.

WHY THE PARSERS SIT BELOW THE LINTER, NOT INSIDE IT
---------------------------------------------------
`lint_plan` needs all four blocks (R11 target dirs, R15 depends-on, R16
checkpoint, R14 premises), and `infer_write_set` needs `plan_target_dirs` plus
`_plan_is_self_extension`. Leaving `plan_target_dirs` in `ops.planlint` therefore
makes the ops edge run BOTH ways — `planlint -> plangraph` for R15/R16 and
`plangraph -> planlint` for the write-set inference — which is a genuine import
cycle, not a stylistic one (whichever module Python loads first hits a partially
initialised peer and raises). The direction is fixed here instead: the linter is
the only ENFORCER and it imports downward; every parser is a leaf. `ops.planlint`
re-exports the three R11 names it no longer defines, so `CLI.plan_target_dirs`
and the `planauthor` `_planlint` alias resolve exactly as before.

NOTHING HERE IS AUTHORITATIVE
-----------------------------
These blocks are an AUTHORING CONVENIENCE. The plan-graph manifest is the hashed
source of truth and always wins; these parsers exist so a node can be SEEDED from
the prose an author already writes. Consequences that matter:
  * A malformed `## Depends-on:` row is never silently dropped — it yields kind
    `__malformed__` so R15 can refuse it and NAME the offender.
  * `plan_checkpoint` may return an out-of-vocab token; refusing it is R16's job
    and the manifest validator's, not the parser's.
  * `infer_write_set` is deliberately CONSERVATIVE: when it cannot narrow a plan
    it returns the whole-repo sentinel `**`, which overlaps every repo-relative
    token, so two un-narrowable plans are forced to serialize rather than
    silently running concurrently against the same files.

Determinism: pure text parsing over plan/markdown strings plus `os.path`
normalisation; stdlib `os`/`re` only, no clock, no network, no writes (INV-2,
INV-6). `infer_write_set` reads no file — `repo` is used only as a prefix for
`os.path.relpath`.

Layer deps: ops.plangraph -> ops.planpremises (`_plan_is_self_extension`, the
load-bearing predicate `infer_write_set` uses to collapse an unnarrowed
self-extension plan onto the kernel sentinel) — a same-layer ops->ops edge that
runs one way only. Nothing else; no core, no knowledge, no I/O.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import os
import re

from agentware_lib.ops.planpremises import _plan_is_self_extension


# --- Workorders + emit-time governance (feature 260710-plan-state-machine) -----
# Background/autonomous work units are WORKORDERS under work/auto/<slug>/. Kernel
# paths a MACHINE-generated plan (`plan new --auto`) may NEVER target: it can never
# be born a self-extension (decide-self-correction-governance-tiers, guard g1).
# Human self-extensions still route through R-PKG-03..06; this only fences the
# emitter.
_KERNEL_PATH_TOKENS = (
    "scripts/agentware", "scripts/hooks", ".claude", "steering",
    "AGENTS.md", "CLAUDE.md", "agentware.sh",
    # Bare hook basenames: the path-form `scripts/hooks/<x>` is caught by the
    # `scripts/hooks` segment, but a BARE basename slips the segment matcher, so a
    # capture/emit naming a hook file without a slash would NOT be refused despite
    # R-CAP-03 fencing "hooks". These are exactly the files under scripts/hooks/.
    "session-start.sh", "log-stop.sh", "log-prompt.sh", "log-tool.sh",
    "log-subagent.sh", "retrieval-state.sh", "codex-stream.py",
    "render-transcript.py", "commit-msg",
)


# --- R11: Target packages contract -----------------------------------------
_PLAN_TARGET_SECTION_RE = re.compile(r"^##\s+Target\s+packages\s*$", re.IGNORECASE | re.MULTILINE)


_PLAN_TARGET_PATH_RE = re.compile(r"^\s*-\s+(~[/\\]|/)\S")


def plan_target_dirs(text):
    """Extract declared target-package directory paths from a plan.

    Parses the `## Target packages` section and returns a list of expanded
    directory paths. Expands `~` to $HOME and environment variables. Returns
    an empty list when the section is missing or has no path rows.
    """
    lines = text.splitlines()
    in_section = False
    dirs = []
    for ln in lines:
        if _PLAN_TARGET_SECTION_RE.match(ln):
            in_section = True
            continue
        if in_section:
            # End of section on next heading
            if re.match(r"^##\s+", ln):
                break
            if _PLAN_TARGET_PATH_RE.match(ln):
                # Extract the path (strip leading `- `)
                path = ln.strip().lstrip("-").strip()
                # Expand ~ and env vars
                path = os.path.expanduser(os.path.expandvars(path))
                dirs.append(path)
    return dirs


# `## Depends-on:` — one predecessor edge per bullet row. Grammar (closed):
#   - <slug>                logical (default)
#   - <slug> logical        logical
#   - <slug> resource       resource (mutual-exclusion / ordering only)
#   - <slug> (resource)     bracket/colon separators normalized to whitespace
# A row that names an out-of-vocab kind, carries extra tokens, or a slug that is
# not [A-Za-z0-9_-]+ yields kind `__malformed__` (never silently dropped) so the
# lint rule R15 can refuse it and name the offender.
_PLAN_DEPENDS_SECTION_RE = re.compile(r"^##\s+Depends-on:?\s*$", re.IGNORECASE)


_PLAN_DEPENDS_KINDS = ("logical", "resource")


_PLAN_EDGE_MALFORMED = "__malformed__"


_PLAN_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def plan_depends_on(text):
    """Parse the `## Depends-on:` section into a list of (pred_slug, kind).

    kind ∈ {logical, resource} (default logical). A malformed row yields
    (raw_slug, '__malformed__'). Returns [] when the section is absent. Pure/
    read-only twin of plan_target_dirs()."""
    edges = []
    in_section = False
    for ln in (text or "").splitlines():
        if _PLAN_DEPENDS_SECTION_RE.match(ln):
            in_section = True
            continue
        if not in_section:
            continue
        if re.match(r"^##\s+", ln):
            break
        stripped = ln.strip()
        if not stripped or not stripped.startswith("-"):
            continue
        body = stripped[1:].strip().replace("`", "").replace("*", "")
        # Normalize bracket/colon/comma separators to whitespace so `slug(resource)`
        # / `slug:resource` / `slug, resource` all tokenize the same way.
        norm = re.sub(r"[()\[\]:,]", " ", body)
        toks = norm.split()
        if not toks:
            continue
        slug = toks[0]
        kind = toks[1] if len(toks) > 1 else "logical"
        if (len(toks) > 2 or kind not in _PLAN_DEPENDS_KINDS
                or not _PLAN_SLUG_RE.match(slug)):
            edges.append((slug, _PLAN_EDGE_MALFORMED))
        else:
            edges.append((slug, kind))
    return edges


# `## Write set` — the repo-relative FILE paths a plan intends to modify. File
# granularity is deliberate: directory is too coarse (every self-ext plan lists
# the repo), function is too fine (git line-merge is untrustworthy across the
# 30k-line monolith). A `serialize_on:` prefix is accepted as a synonym for a
# resource-serialization declaration; the path is what follows.
_PLAN_WRITESET_SECTION_RE = re.compile(r"^##\s+Write set\s*$", re.IGNORECASE)


_PLAN_SERIALIZE_ON_RE = re.compile(r"(?i)^serialize_on\s*:?\s*")


def plan_write_set(text):
    """Parse an optional `## Write set` section into a sorted, de-duplicated list
    of repo-relative path tokens. Empty list when the section is absent. Pure/
    read-only."""
    paths = set()
    in_section = False
    for ln in (text or "").splitlines():
        if _PLAN_WRITESET_SECTION_RE.match(ln):
            in_section = True
            continue
        if not in_section:
            continue
        if re.match(r"^##\s+", ln):
            break
        stripped = ln.strip()
        if not stripped.startswith("-"):
            continue
        body = stripped[1:].strip().replace("`", "").replace("*", "")
        body = _PLAN_SERIALIZE_ON_RE.sub("", body).strip()
        if not body:
            continue
        paths.add(body.split()[0])
    return sorted(paths)


# The un-narrowable write-set sentinel: a whole-repo marker that overlaps EVERY
# repo-relative token (a plan whose write-set can't be narrowed must serialize
# against everything). NOT `<pkg>/**` (the repo basename never prefixes a
# repo-relative token, so that form overlapped nothing — audit finding).
_WRITE_SET_WHOLE_REPO = "**"


def _write_token_norm(tok):
    """Canonicalize a write-set token for CONSERVATIVE comparison: `\\`->`/`, drop
    a leading `./`, strip a trailing `/`, and casefold (macOS/Windows filesystems
    are case-insensitive — folding over-serializes at worst, never under). So
    `scripts/agentware`, `./scripts/agentware`, and `Scripts/agentware` all
    compare equal. Pure."""
    t = (tok or "").replace("\\", "/").strip()
    while t.startswith("./"):
        t = t[2:]
    t = t.rstrip("/") or t
    return t.casefold()


def _kernel_write_tokens(text):
    """The set of canonical `_KERNEL_PATH_TOKENS` that appear as BOUNDED path
    segments anywhere in `text` (segment-bounded, never a bare substring — the
    same discipline as `_has_kernel_path_segment`). Used to widen an inferred
    write-set with the exact kernel files a plan mentions so two plans touching
    the same kernel file are forced to serialize."""
    found = set()
    if not text:
        return found
    for raw_tok in text.split():
        cleaned = raw_tok.strip("`*_()[]<>,;'\"").replace("\\", "/")
        if not cleaned:
            continue
        segs = [s for s in cleaned.casefold().split("/") if s]
        if not segs:
            continue
        for tok in _KERNEL_PATH_TOKENS:
            parts = [p for p in tok.casefold().split("/") if p]
            n = len(parts)
            for i in range(len(segs) - n + 1):
                if segs[i:i + n] == parts:
                    found.add(tok)
                    break
    return found


def _write_set_repo_rel(path, repo_abs):
    """A target-package dir re-expressed repo-relative, or None when it escapes
    the repo / is unresolvable. '' or '.' (the repo root itself) returns '.'."""
    if not repo_abs:
        return None
    try:
        rel = os.path.relpath(os.path.abspath(path), repo_abs)
    except (ValueError, OSError):
        return None
    rel = rel.replace(os.sep, "/")
    if rel.startswith("../") or rel == "..":
        return None
    return rel


def infer_write_set(plan_text, repo):
    """Conservative write-set INFERENCE for a plan that declares no `## Write set`.

    Returns a sorted list of repo-relative path tokens. Deterministic/offline.
      - A self-extension plan that names no concrete kernel FILE collapses to the
        sentinel {scripts/agentware} (the monolith it most likely edits) so two
        such plans are forced to serialize.
      - Otherwise: the plan's kernel-path mentions ∪ its target-package dirs
        (repo-relative).
      - If nothing narrows it (whole-repo target, no kernel mentions), the
        un-narrowable whole-repo sentinel {_WRITE_SET_WHOLE_REPO} — conservative,
        overlaps EVERY repo-relative token. (An earlier `<pkg>/**` form used the
        repo BASENAME as the glob root, which — since every real token is
        repo-relative — overlapped nothing, silently defeating serialization;
        audit finding.)
    """
    repo_abs = os.path.abspath(repo) if repo else None
    kernel = set(_kernel_write_tokens(plan_text))
    narrowed = set(kernel)
    whole_repo = False
    for d in plan_target_dirs(plan_text):
        rel = _write_set_repo_rel(d, repo_abs)
        if rel is None:
            continue
        if rel in ("", "."):
            whole_repo = True
        else:
            narrowed.add(rel)
    if _plan_is_self_extension(plan_text) and not kernel:
        narrowed.add("scripts/agentware")
    if narrowed:
        return sorted(narrowed)
    # Un-narrowable: whole-repo (or empty) target with no kernel mention. The
    # whole-repo sentinel overlaps every repo-relative token (see
    # `_write_token_matches`), so two such plans are forced to serialize.
    _ = whole_repo
    return [_WRITE_SET_WHOLE_REPO]


# `> Checkpoint: <policy>` — the manual/automatic review barrier attached to a
# node. Vocab is CLOSED; `none` is the default. A self-extension plan MUST carry
# a non-none checkpoint (R16) — and, at the manifest layer (Task 2), specifically
# `manual` (a self-ext node is a mandatory manual checkpoint the operator runs).
_CHECKPOINT_POLICIES = ("none", "review", "manual")


_PLAN_CHECKPOINT_RE = re.compile(r"(?im)^>\s*Checkpoint:\s*([A-Za-z-]+)\s*$")


def plan_checkpoint(text):
    """The declared `> Checkpoint: <policy>` token, lowercased (default 'none').
    May be out-of-vocab — lint R16 / the manifest validator refuse that."""
    m = _PLAN_CHECKPOINT_RE.search(text or "")
    if not m:
        return "none"
    return m.group(1).strip().lower()


# --- `> Status:` — the plan's lifecycle state ---------------------------------
# The fifth declarative block, and the same story as the R11 parser above: it
# arrived in `ops.planauthor.planstate` because MIGRATION 001 needed it before
# `plan` was ported (task 37), but R13 makes the LINTER need it too — and
# `planauthor/__init__` imports `ops.planlint`, so a `planlint -> planstate` edge
# closes a real cycle (verified: importing planlint first raises `ImportError:
# cannot import name '_PLAN_TASK_LINE_RE' from partially initialized module`).
# The parser therefore moves DOWN to the leaf beside its four sibling blocks and
# `planstate` re-exports it, which keeps `is`-identity for all 26 resident call
# sites. The STATE MACHINE — the legal-transition matrix, claim parsing and the
# set-state/claim handlers — is NOT here: it is a WRITER and stays in planstate.
#
# Toolkit-owned plan-LEVEL lifecycle, team-visible through git: a `> Status:`
# header line in plan.md + an append-only `## Status log` section. `plan set-state`
# is the SOLE writer. The SIX states and the FULL legal-transition matrix below
# are the single source of truth — any (from, to) edge NOT present in
# _PLAN_STATE_TRANSITIONS is REFUSED with a clear error. A plan with no `> Status:`
# header reads as `draft` (legacy plans). Header parsing is FIRST-MATCH from the
# top of the file; a later/duplicate status-like line is ignored here and flagged
# by lint.
_PLAN_STATES = ("draft", "ready", "running", "done", "superseded", "cancelled")

_PLAN_DEFAULT_STATE = "draft"
_PLAN_STATUS_HEADER_RE = re.compile(r"^>\s*Status:\s*([A-Za-z-]+)\s*$", re.M)


def _read_plan_status(text):
    """FIRST-MATCH `> Status:` from the top of the file -> (state, count).

    `count` is how many status-like header lines exist (>1 => duplicate/
    hand-edited, an error for lint/set-state). Missing => (_PLAN_DEFAULT_STATE, 0)
    so every legacy plan reads as `draft`.
    """
    matches = _PLAN_STATUS_HEADER_RE.findall(text or "")
    if not matches:
        return (_PLAN_DEFAULT_STATE, 0)
    return (matches[0].strip().lower(), len(matches))


# Bound onto the entrypoint facade BY NAME so the resident callers that still
# resolve these bare (the plan-graph manifest/scheduler, the workorder emit
# guard) and every `CLI.<name>` oracle keep resolving ONE object. The three R11
# names are listed HERE, not in ops.planlint, because this module now defines
# them; planlint re-exports them so both spellings stay live through task 54.
_EXPORTS = [
    "_KERNEL_PATH_TOKENS",
    "_PLAN_TARGET_SECTION_RE",
    "_PLAN_TARGET_PATH_RE",
    "plan_target_dirs",
    "_PLAN_DEPENDS_SECTION_RE",
    "_PLAN_DEPENDS_KINDS",
    "_PLAN_EDGE_MALFORMED",
    "_PLAN_SLUG_RE",
    "plan_depends_on",
    "_PLAN_WRITESET_SECTION_RE",
    "_PLAN_SERIALIZE_ON_RE",
    "plan_write_set",
    "_WRITE_SET_WHOLE_REPO",
    "_write_token_norm",
    "_kernel_write_tokens",
    "_write_set_repo_rel",
    "infer_write_set",
    "_CHECKPOINT_POLICIES",
    "_PLAN_CHECKPOINT_RE",
    "plan_checkpoint",
    "_PLAN_STATES",
    "_PLAN_DEFAULT_STATE",
    "_PLAN_STATUS_HEADER_RE",
    "_read_plan_status",
]
__all__ = list(_EXPORTS)
