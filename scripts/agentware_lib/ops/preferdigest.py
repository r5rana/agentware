"""agentware_lib.ops.preferdigest — the SESSION-START digest (layer: ops).

Owns: the read-only preference summary injected at session start —
`_pref_digest_entries`, `_render_pref_digest`, `_prefer_digest`, the project-tag
helper and the two budgets that bound them.

WHY IT HAS A CHARACTER BUDGET
This text is injected into EVERY session, so it is a permanent context cost paid
forever and growing. `_PREF_DIGEST_MAX` and `_PREF_DIGEST_CHAR_BUDGET` are what
stop an accumulating preference set from silently eating the window it exists to
improve. The budget is enforced by TRUNCATING the digest, never by refusing a
capture — so the capture path stays honest about what the operator asked for, and
the injection stays bounded regardless.

Split from `ops.prefercmd` at task 45 on the file ceiling, but the seam is real:
this is the only part of the prefer family that runs on every session and the only
part whose cost is paid by sessions that never touch `prefer` at all.

Does NOT own: the `prefer` verb and its subcommands (ops.prefercmd); the
admission bar (ops.preferadmit); the queue (ops.preferqueue).
Layer deps: ops -> core (config, frontmatter, projectcontext) + knowledge.index +
ops.preferadmit.
Fan-in: 1 at landing (regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith — byte-identical, comments and all.
"""

import os
import sys

import agentware_lib.core.config as config
from agentware_lib.core.config import require_knowledge_dir, resolve_knowledge_dir
from agentware_lib.core.frontmatter import _entry_list_field, read_entry_frontmatter
from agentware_lib.core.projectcontext import _canonical_project_key, _resolve_context
from agentware_lib.knowledge.index import load_index
from agentware_lib.ops.preferadmit import _pref_entry_is_user_source
from agentware_lib.ops.planauthor.plankernelpath import _pref_hits_kernel
from agentware_lib.ops.prefergate import _pref_loosens_gate


_PREF_DIGEST_MAX = 20          # count cap (never inject an unbounded list)


_PREF_DIGEST_CHAR_BUDGET = 1500  # soft total-char budget (mirrors recall discipline)


def _pref_project_tags(entry):
    """The `project-<slug>` scope slugs carried by a captured preference."""
    return [t[len("project-"):] for t in _entry_list_field(entry, "tags")
            if t.startswith("project-")]


def _pref_digest_entries(kdir, data, proj_slug):
    """Active `source=user`, `preference`-tagged entries in scope for `proj_slug`.

    Global captures (no `project-` tag) ALWAYS qualify; project-scoped captures
    qualify ONLY when the resolved `proj_slug` matches one of their scope slugs.
    When `proj_slug` is None (no project signal) only global captures qualify —
    another project's prefs are NEVER leaked. Recency-sorted (newest first),
    deterministic on (created, id).
    """
    out = []
    for e in data.get("entries") or []:
        if "preference" not in _entry_list_field(e, "tags"):
            continue
        if not _pref_entry_is_user_source(kdir, e):
            continue
        # Injection defense-in-depth (Task 4, audit LOW): re-apply the hardened
        # capture governance filter to each candidate summary and EXCLUDE any that
        # would be REFUSED at capture time. A `preference`/`source=user` entry
        # created OUTSIDE the verb — e.g. a raw `learn --source user --tag
        # preference "AGENTS.md is optional"` — bypasses the capture gate but still
        # renders here; the digest is the LAST line before fleet-wide amplification,
        # so it must not inject a kernel-path / gate-loosening summary either.
        summ = e.get("summary") or ""
        if _pref_hits_kernel(summ) or _pref_loosens_gate(summ):
            continue
        proj_tags = _pref_project_tags(e)
        if proj_tags:
            if proj_slug is None or proj_slug not in proj_tags:
                continue
        out.append(e)
    out.sort(key=lambda e: (e.get("created") or "", e.get("id") or ""),
             reverse=True)
    return out


def _render_pref_digest(entries):
    """One-line-per-pref digest block; count- and char-capped. '' if empty."""
    lines = []
    used = 0
    for e in entries:
        if len(lines) >= _PREF_DIGEST_MAX:
            break
        summ = (e.get("summary") or "").strip()
        summ = summ.splitlines()[0].strip() if summ else ""
        if not summ:
            continue
        proj_tags = _pref_project_tags(e)
        scope = proj_tags[0] if proj_tags else "global"
        line = "- [%s] %s" % (scope, summ)
        if lines and used + len(line) > _PREF_DIGEST_CHAR_BUDGET:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    header = ("----- active steering preferences (captured once via the "
              "steering-capture verb; in force this session) -----")
    return header + "\n" + "\n".join(lines) + "\n"


def _prefer_digest(args):
    """Render the active-preference digest for injection (Task 3).

    Read-only: never mutates the KB. Silent no-op (empty output, exit 0) when
    the KB is unreadable or has no active captures, so it can NEVER break session
    start. `--project` is the raw checkout signal (whereami project_name); it is
    normalized to the canonical KB slug via the Task-1 resolver, so an unknown
    checkout falls back to GLOBAL-only (never mis-scoped, never leaked)."""
    kdir = require_knowledge_dir()
    if not kdir:
        return 0
    # READ-ONLY (Task 3, audit MEDIUM): _allow_rebuild=False so a missing/corrupt
    # index at SessionStart returns a non-None err and yields a clean empty digest
    # WITHOUT triggering a rebuild WRITE (mirrors rebuild_kb's read-only load). The
    # legitimate self-heal still fires on the next real write verb or an explicit
    # `index rebuild` — SessionStart stays genuinely read-only + fast.
    data, err = load_index(kdir, _allow_rebuild=False)
    if err or not data:
        return 0
    signal = (getattr(args, "project", None) or "").strip()
    proj = _canonical_project_key(kdir, signal) if signal else None
    text = _render_pref_digest(_pref_digest_entries(kdir, data, proj))
    if text:
        sys.stdout.write(text)
    return 0


_EXPORTS = [
    "_PREF_DIGEST_MAX",
    "_PREF_DIGEST_CHAR_BUDGET",
    "_pref_project_tags",
    "_pref_digest_entries",
    "_render_pref_digest",
    "_prefer_digest",
]

__all__ = list(_EXPORTS)
