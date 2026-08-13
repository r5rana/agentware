"""agentware_lib.ops.planauthor.workstamp -- work-artifact team/author stamping (layer: ops).

Owns: the work-artifact team/author STAMPING family (feature 260721 org provenance) --
the one regex that recognizes BOTH supported team-stamp layouts (_WORK_TEAM_STAMP_RE), the
derived-roster content detector (_is_derived_roster), the idempotent prose stamper
(_stamp_work_artifact), the machine-readable YAML frontmatter ensurer
(_ensure_work_frontmatter), the CLI handler cmd_work_stamp, and the `work` verb registrar
(register_work), moved WITH its handler.

Does NOT own: the plan-authoring GENERATION half (_plan_skeleton / _plan_promise /
_plan_path / _plan_renumber / _plan_marker_indices / _load_plan_for_edit / _atomic_write /
_PLAN_STATUS_GLYPH + the cmd_plan_* handlers + register_plan) ->
agentware_lib.ops.planauthor.planedit, a SIBLING submodule this module does not import (the
two halves share no symbol, so the seam is a clean cut); the plan LINT enforcement half ->
agentware_lib.ops.planlint; the frontmatter PARSER itself (split_frontmatter) ->
agentware_lib.core.frontmatter, reached DOWNWARD; the derived-roster RENDERER
(_render_roster, whose banner this module's detector keys off) ->
agentware_lib.knowledge.index.

Layer deps: ops.planauthor.workstamp -> core only (config resolvers,
routing.resolve_user_handle, frontmatter.split_frontmatter) -- all DOWNWARD. Cross-module
FUNCTIONS are reached by ATTRIBUTE through module aliases (config / routing / frontmatter)
so the bytecode global-resolve gate never cascades into them.

_EXPORTS is imported BY NAME onto the scripts/agentware facade because tests reach
_stamp_work_artifact / _ensure_work_frontmatter as CLI.<name> and the parser-golden
dispatch contract requires the `work stamp` leaf's func=cmd_work_stamp to be an attribute
of the entrypoint namespace. The `work` registrar is declared in THIS module's
`__verbs__` (the aggregator still re-imports it for pre-split call sites), NOT
re-exported.

Determinism: idempotent in-place stamping over the team's work/ tree, stdlib json/os/re/sys
only (INV-2, INV-6). Stamping is a pure function of (file bytes, team, author); no
wall-clock enters ranking (INV-1).

Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import os
import re
import sys

import agentware_lib.core.config as config
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.core.routing as routing


# A work-artifact team stamp in EITHER supported layout:
#   line-start blockquote:  > Team: <team>
#   inline mid-blockquote:   > Created: … · Team: <team> · Author: …
# One regex recognizes both so stamping is idempotent AND can rewrite a stale team
# (e.g. after a team rename) regardless of which layout the artifact used.
_WORK_TEAM_STAMP_RE = re.compile(r"(?m)^(>.*?\bTeam:\s*)([^\s·|]+)(.*)$")


def _is_derived_roster(path):
    """True iff `path` is a rebuild-generated `<sub>/index.md` roster (not an
    authored entry). Detected by CONTENT: derived rosters carry the fixed
    'Auto-generated roster — DO NOT edit' banner (see _render_roster) and never
    an authored `id:`/`category:` frontmatter block. Used so `work stamp` skips
    only genuinely-derived rosters, never a hand-authored index.md artifact.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            head = f.read(600)
    except OSError:
        return False
    return "Auto-generated roster" in head and "DO NOT edit" in head


def _stamp_work_artifact(path, team, author):
    """Idempotently ensure a work artifact's header carries `Team: <team>`.

    Three cases, in order:
      1. Already stamped with the CORRECT team (either `> Team:` line-start or the
         inline `· Team:` layout) → no-op, returns False.
      2. Stamped with a DIFFERENT/stale team (e.g. pre-rename) → rewrite the team
         value in place, preserving the surrounding layout. Returns True.
      3. No team stamp at all → insert a `> Team:` (+ `> Author:` if absent) line
         after the H1. Returns True.
    Preserves all other content. Used by `work stamp` to backfill/repair EVERY .md
    work artifact (operator direction: every artifact carries the right team).
    """
    if not team:
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    m = _WORK_TEAM_STAMP_RE.search(text)
    if m:
        if m.group(2) == team:
            return False  # already correct (either layout) — idempotent no-op
        # Case 2: rewrite the stale team value in place, keep the layout.
        new_text = text[:m.start(2)] + team + text[m.end(2):]
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_text)
        return True
    # Case 3: no stamp — insert a line-start `> Team:` after the H1.
    stamp = "> Team: %s\n" % team
    if author and re.search(r"(?m)^>\s*Author:\s*\S", text) is None:
        stamp += "> Author: %s\n" % author
    lines = text.split("\n")
    # Skip a leading machine frontmatter block so the prose stamp lands in the
    # BODY — never above or inside the `---…---` block (which would break
    # split_frontmatter). `work stamp --frontmatter` prepends that block in the
    # SAME pass BEFORE this stamping runs, so an artifact with any preamble
    # before its H1 would otherwise push the H1 past the 5-line window and
    # mis-insert the prose stamp at line 0, corrupting the frontmatter.
    body_start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                body_start = i + 1
                break
    insert_at = body_start
    for i in range(body_start, min(body_start + 5, len(lines))):
        if lines[i].startswith("# "):
            insert_at = i + 1
            if insert_at < len(lines) and lines[insert_at].strip() == "":
                insert_at += 1
            break
    new = lines[:insert_at] + [stamp.rstrip("\n")] + lines[insert_at:]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(new))
    return True


def _ensure_work_frontmatter(path, team, origin="team"):
    """Idempotently ensure a work artifact carries a MACHINE-READABLE YAML `team:`
    frontmatter block (distinct from the human `> Team:` prose line).

    WHY: the human `> Team:` stamp is prose the indexer does NOT read; only a real
    `---`…`---` YAML block is machine-readable. Adding it makes a work artifact
    FUTURE-RECALL-READY — the day work/ is promoted into the recall corpus, each
    artifact self-scopes by its own `team:` (and whole-org indexing is correct
    regardless of the KDIR path), with zero re-migration. Inert today (work/ is not
    indexed). Verified NON-breaking: a leading `---` block does not disturb plan
    lint, task-marker parsing, promise detection, or commit-title extraction.

    Three cases (mirrors _stamp_work_artifact):
      1. Has a leading YAML block with the CORRECT team → no-op (False).
      2. Has a leading YAML block with a stale/absent team → set/rewrite team+origin
         inside it (True).
      3. No leading YAML block → PREPEND one (True). split_frontmatter recognizes a
         block ONLY when line 1 is exactly '---'.
    Returns True iff changed. Preserves all body content.
    """
    if not team:
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    fields, body = frontmatter.split_frontmatter(text)
    if fields is not None:
        # Case 1/2: a block exists. Is team already correct?
        if fields.get("team") == team and fields.get("origin"):
            return False
        # Rewrite/insert team+origin lines inside the existing block, preserving
        # every other line + the body byte-for-byte.
        lines = text.split("\n")
        # locate the closing '---' (second one)
        close = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                close = i
                break
        if close is None:
            return False  # malformed; don't touch
        block = lines[1:close]
        block = [ln for ln in block
                 if ln.split(":", 1)[0].strip() not in ("team", "origin")]
        block += ["team: %s" % team, "origin: %s" % origin]
        new = ["---"] + block + ["---"] + lines[close + 1:]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(new))
        return True
    # Case 3: no block — prepend a minimal one.
    fm = "---\nteam: %s\norigin: %s\n---\n" % (team, origin)
    with open(path, "w", encoding="utf-8") as f:
        f.write(fm + text)
    return True


def cmd_work_stamp(args):
    """`agentware work stamp` — backfill/repair team+author on work/ artifacts (Task 6/op).

    Stamps EVERY `.md` under the team's work/ tree with the caller's team (adding a
    `> Team:` header where absent, or rewriting a stale team value in place after a
    rename). Idempotent: an artifact already carrying the correct team is untouched.
    Covers ALL work artifact types (plan/worklog/assessment/design/notes/REPORT/…),
    not a fixed 3-file allowlist, so provenance stays complete + a team rename
    self-repairs. Skips a .md with no header/H1 (nothing to stamp). Team from the
    kdir path; author from the per-user handle. No-op on a flat KB. Excludes derived
    rosters (index.md) + gitignored dirs implicitly (walk stays under work/).
    """
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    team = config._infer_team_from_kdir(kdir)
    if not team:
        print("work stamp: no-op (flat KB — no team facet)")
        return 0
    author = getattr(args, "author", None) or routing.resolve_user_handle()
    do_fm = getattr(args, "frontmatter", False)
    work_dir = os.path.join(kdir, "work")
    stamped, scanned, fm_added = 0, 0, 0
    if os.path.isdir(work_dir):
        for root, _dirs, files in os.walk(work_dir):
            for fn in files:
                # ALL markdown work artifacts (not a fixed allowlist). Skip only a
                # genuinely DERIVED roster (rebuild artifact), detected by CONTENT
                # not filename: an authored `index.md` (e.g. a hand-written
                # artifacts index with real `id:`/`category:` frontmatter) must be
                # stamped like any other artifact. Rosters are only ever written at
                # <KNOWLEDGE_SUBDIR>/index.md, never under work/, so this is belt-
                # and-suspenders — but content detection is robust regardless.
                if not fn.endswith(".md"):
                    continue
                fpath = os.path.join(root, fn)
                if fn == "index.md" and _is_derived_roster(fpath):
                    continue
                scanned += 1
                # --frontmatter FIRST (prepends the machine-readable YAML block),
                # THEN the human `> Team:` prose stamp — so both layers carry the
                # team and the prose line lands after the block, in the body.
                if do_fm and _ensure_work_frontmatter(fpath, team):
                    fm_added += 1
                if _stamp_work_artifact(fpath, team, author):
                    stamped += 1
    if getattr(args, "format", "text") == "json":
        json.dump({"team": team, "scanned": scanned, "stamped": stamped,
                   "frontmatter_added": fm_added}, sys.stdout)
        sys.stdout.write("\n")
    else:
        print("work stamp: team=%s scanned=%d prose-stamped=%d frontmatter=%d"
              % (team, scanned, stamped, fm_added))
    return 0


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("work", 1600, "register_work"),
)


def register_work(sub, fmt_parent):
    # work -------------------------------------------------------------------
    # Work-artifact team provenance (operator direction): stamp
    # `> Team:`/`> Author:` into every plan/worklog/assessment under the team.
    p_work = sub.add_parser("work", help="work-artifact utilities (team provenance)")
    work_sub = p_work.add_subparsers(dest="work_command", metavar="<subcommand>")
    work_sub.required = True
    p_work_stamp = work_sub.add_parser(
        "stamp", parents=[fmt_parent],
        help="backfill team/author headers into work/ artifacts (idempotent)")
    p_work_stamp.add_argument("--author", default=None, help="author handle override")
    p_work_stamp.add_argument("--frontmatter", action="store_true",
                              help="ALSO prepend a machine-readable YAML team: block "
                                   "to each artifact (future-recall-ready; the prose "
                                   "'> Team:' line alone is not indexer-readable)")
    p_work_stamp.set_defaults(func=cmd_work_stamp)


_EXPORTS = [
    "_WORK_TEAM_STAMP_RE",
    "_is_derived_roster",
    "_stamp_work_artifact",
    "_ensure_work_frontmatter",
    "cmd_work_stamp",
]
__all__ = list(_EXPORTS)
