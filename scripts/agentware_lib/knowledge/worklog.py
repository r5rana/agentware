"""agentware_lib.knowledge.worklog — R-SI-03/R-AUTO-05 worklog promotion gate (layer: knowledge).

Owns: the completion-gate worklog scanner — the LEARNED/DECISION/PROMOTED marker
regexes (LEARNED_RE, DECISION_RE, PROMOTED_RE), the promotion-resolution closure
(promoted_ids, _real_ids, _category_stems, learning_stems, decision_stems,
skill_promotion_ids, _learned_real_ids, _is_promoted), the scanner itself
(scan_worklog), and the `worklog scan` command surface (cmd_worklog_scan +
register_worklog). Pure + read-only over the live index PLUS the on-disk
learnings/decisions/skills/skills-proposed dirs (INV-1 deterministic, INV-2
read-only); a `> LEARNED:`/`> DECISION:` marker clears iff an explicit
`(Promoted: <id>)` reference resolves EXACTLY to a real learning/decision/skill/
proposal entry, or (no reference) its slug equals a learning stem.
Does NOT own: the index CRUD/load or category-dir constants (see
agentware_lib.knowledge.index — read as index.load_index / index.LEARNINGS_REL /
index.DECISIONS_REL / index.SKILLS_PROPOSED_DIR); the slug canonicaliser (see
agentware_lib.knowledge.curation._slugify); the proposal-staging slug enumerator
(see agentware_lib.knowledge.proposals.existing_proposal_slugs); the promotion
WRITE path learn/decide (see agentware_lib.knowledge.curation /
agentware_lib.features.curationcmd); knowledge-dir resolution (see
agentware_lib.core.config.require_knowledge_dir).
Layer deps: knowledge -> core (agentware_lib.core.config) + knowledge siblings
(agentware_lib.knowledge.index / curation / proposals) + stdlib (json/os/re/sys).
Never imports ops/retrieval/features or the entrypoint (no upward/forward edges);
none of index/curation/proposals imports this module, so there is no cycle.
Fan-in: 4 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import os
import re
import sys

import agentware_lib.core.config as config
import agentware_lib.knowledge.curation as curation
import agentware_lib.knowledge.index as index
from agentware_lib.knowledge.proposals import existing_proposal_slugs


# The optional `[-*+]` LIST BULLET is load-bearing, not cosmetic (task 45). A
# marker written as a list item — `- > LEARNED: ...`, which is how they are often
# written inside a worklog's bulleted section — must still be SEEN by the
# completion gate. Without it the scanner reports zero unpromoted markers and the
# promise goes through: the gate FAILS OPEN, and an unpromoted learning is
# silently lost rather than blocking. Restored to the monolith's form.
LEARNED_RE = re.compile(r"^\s*(?:[-*+]\s+)?>?\s*LEARNED:\s*(.*?)\s*$")
DECISION_RE = re.compile(r"^\s*(?:[-*+]\s+)?>?\s*DECISION:\s*(.*?)\s*$")

# Explicit, deterministic promotion reference on a marker line. The canonical
# form is `(Promoted: <id>)`; the bracketed `[promoted -> <id>]` form is the
# decisions convention and is accepted too so both marker families share one
# matcher. The captured `<id>` is resolved EXACTLY against the live index/files
# (no substring fuzz) — this is the primary signal that a marker was promoted.
PROMOTED_RE = re.compile(
    r"(?:\(\s*Promoted:\s*|\[\s*promoted\s*->\s*)([a-z0-9][a-z0-9-]*)", re.I)


def all_entry_ids(kdir, data):
    """Every id a promotion reference may resolve to, lowercased: all indexed
    entry ids + their filename stems, plus skill stems on disk. This is the
    exact-match universe so a CROSS-category ref on a marker — e.g.
    `(Promoted: skill-x)` or `[kb: decide-y]` on a `> LEARNED:` line — resolves
    regardless of the marker's own kind. Read-only (INV-2), deterministic."""
    ids = set()
    for e in (data.get("entries") or []):
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        if eid:
            ids.add(eid.lower())
        base = os.path.basename(e.get("path") or "")
        if base.endswith(".md"):
            ids.add(base[:-3].lower())
    # Skills can live on disk without appearing in the user index; include their
    # stems (bare and `skill-`-prefixed) so skill-promotion refs resolve.
    sdir = os.path.join(kdir, "skills")
    if os.path.isdir(sdir):
        for fn in os.listdir(sdir):
            if fn.endswith(".md") and fn != "index.md":
                stem = fn[:-3].lower()
                ids.add(stem)
                ids.add("skill-" + stem)
    return ids


def promoted_ids(marker_text):
    """Lowercased ids referenced by an explicit promotion marker, in order."""
    return [m.lower() for m in PROMOTED_RE.findall(marker_text or "")]


def _real_ids(stems, id_prefix):
    """Exact-match id set for a category: every stem in both its bare form and
    its prefixed-id form (`learn-<stem>` / `decide-<stem>`)."""
    ids = set()
    for s in stems:
        ids.add(s)
        ids.add(id_prefix + s)
    return ids


def _category_stems(kdir, data, category, id_prefix, subdir):
    """Filename/id stems of every entry in `category` (for promotion checks).

    Generic over the marker family so `> LEARNED:` (learnings) and `> DECISION:`
    (decisions) share one implementation.
    """
    stems = set()
    for e in (data.get("entries") or []):
        if not isinstance(e, dict) or e.get("category") != category:
            continue
        base = os.path.basename(e.get("path") or "")
        if base.endswith(".md"):
            stems.add(base[:-3].lower())
        eid = e.get("id") or ""
        if eid.startswith(id_prefix):
            stems.add(eid[len(id_prefix):].lower())
    d = os.path.join(kdir, subdir)
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.endswith(".md") and fn != "index.md":
                stems.add(fn[:-3].lower())
    return {s for s in stems if s}


def learning_stems(kdir, data):
    return _category_stems(kdir, data, "learnings", "learn-", index.LEARNINGS_REL)


def decision_stems(kdir, data):
    return _category_stems(kdir, data, "decisions", "decide-", index.DECISIONS_REL)


def skill_promotion_ids(kdir, data):
    """Ids that clear a `> LEARNED:` marker via a SKILL or PROPOSAL promotion.

    A reusable procedure discovered in a run may be promoted into an always-on
    skill (`category == "skills"`, id `skill-<slug>`) OR filed into the
    `skills-proposed/` staging tier as a proposal (indexed id `skill-<slug>`,
    category `references`). Either promotion satisfies the zero-knowledge-loss
    gate (R-SI-03) for that marker, so a `(Promoted: skill-<slug>)` or
    `(Promoted: proposal-<slug>)` reference resolving to a REAL skill/proposal
    entry MUST clear the marker. Before this, only `learn-` ids cleared a
    `> LEARNED:` marker -> a skill-candidate routed to a proposal NEVER cleared
    -> `worklog scan` failed -> a finished run died in `run_post_hooks`.

    Pure + read-only over the live index PLUS on-disk skill/proposal dirs (so a
    just-created proposal/skill clears even before `data` is reloaded). INV-2.
    """
    ids = set()
    for e in (data.get("entries") or []):
        if not isinstance(e, dict):
            continue
        eid = (e.get("id") or "").lower()
        if not eid:
            continue
        path = (e.get("path") or "").lower()
        if e.get("category") == "skills":
            ids.add(eid)                       # always-on skill (skill-<slug>)
        if path.startswith(index.SKILLS_PROPOSED_DIR.lower() + "/"):
            ids.add(eid)                       # proposal (indexed id skill-<slug>)
            if eid.startswith("skill-"):
                # `proposal-<slug>` alias so the plain proposal reference resolves
                ids.add("proposal-" + eid[len("skill-"):])
    for slug in existing_proposal_slugs(kdir):
        s = slug.lower()
        ids.add("skill-" + s)
        ids.add("proposal-" + s)
    skills_root = os.path.join(kdir, "skills")
    try:
        for name in os.listdir(skills_root):
            if os.path.isfile(os.path.join(skills_root, name, "SKILL.md")):
                ids.add("skill-" + name.lower())
    except OSError:
        pass
    return ids


def _learned_real_ids(kdir, data, l_stems):
    """Exact-match id set that clears a `> LEARNED:` marker: durable learnings
    (`learn-<stem>`) PLUS skill/proposal promotions. A skill-candidate promoted
    to a proposal (never a `learn-` id) must still clear R-SI-03."""
    return _real_ids(l_stems, "learn-") | skill_promotion_ids(kdir, data)


def _is_promoted(marker_text, stems, real_ids):
    """A marker is promoted iff:

    * primary — it carries an explicit `(Promoted: <id>)` / `[promoted -> <id>]`
      reference whose id resolves EXACTLY to a real entry (`real_ids`); or
    * fallback (no reference) — its slug EQUALS a learning stem exactly.

    The legacy substring branches (`stem in slug` / `slug in stem`) are gone:
    they produced false positives (short slug inside an unrelated stem) and could
    never be trusted. Pure string/set comparison over on-disk data -> INV-1
    deterministic, INV-2 read-only.
    """
    refs = promoted_ids(marker_text)
    if refs:
        return any(r in real_ids for r in refs)
    slug = curation._slugify(marker_text)
    if not slug:
        return False
    return any(slug == stem for stem in stems)


def scan_worklog(path, kdir, data):
    """Scan a worklog for unpromoted `> LEARNED:` and `> DECISION:` markers.

    Both families gate completion (R-SI-03 / R-AUTO-05). The combined top-level
    `unpromoted` list keeps the original exit semantics (non-empty -> fail);
    each item carries a `kind` ("learned"|"decision") so callers can label it.
    """
    l_stems = learning_stems(kdir, data)
    d_stems = decision_stems(kdir, data)
    kinds = (
        ("learned", LEARNED_RE, l_stems, _learned_real_ids(kdir, data, l_stems)),
        ("decision", DECISION_RE, d_stems, _real_ids(d_stems, "decide-")),
    )
    unpromoted, total = [], 0
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            for kind, rx, stems, real_ids in kinds:
                m = rx.match(line)
                if not m:
                    continue
                text = m.group(1).strip()
                if not text:
                    continue
                total += 1
                if not _is_promoted(text, stems, real_ids):
                    unpromoted.append({"line": lineno, "text": text,
                                       "kind": kind})
                break
    return unpromoted, total


def cmd_worklog_scan(args):
    if not os.path.isfile(args.path):
        sys.stderr.write("agentware: worklog not found: %s\n" % args.path)
        return 1
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    data, err = index.load_index(kdir)
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1

    unpromoted, total = scan_worklog(args.path, kdir, data)
    ok = len(unpromoted) == 0
    if getattr(args, "format", "text") == "json":
        json.dump({"unpromoted": unpromoted, "total": total},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        if ok:
            print("WORKLOG SCAN PASSED: %d marker(s), all promoted." % total)
        else:
            print("WORKLOG SCAN FAILED: %d of %d marker(s) unpromoted:"
                  % (len(unpromoted), total))
            for item in unpromoted:
                print("  line %d [%s]: %s"
                      % (item["line"], item.get("kind", "learned"), item["text"]))
    return 0 if ok else 1


def register_worklog(sub, fmt_parent):
    # worklog ----------------------------------------------------------------
    p_wl = sub.add_parser("worklog", help="worklog utilities")
    wl_sub = p_wl.add_subparsers(dest="worklog_command", metavar="<subcommand>")
    wl_sub.required = True
    p_scan = wl_sub.add_parser(
        "scan", parents=[fmt_parent],
        help="find unpromoted '> LEARNED:' markers in a worklog")
    p_scan.add_argument("--path", required=True, help="path to a worklog.md")
    p_scan.add_argument("--also-assessment", action="store_true",
                        dest="also_assessment",
                        help="ADVISORY sidecar: also flag Extracted-Knowledge "
                             "'Suggested ID' entries in the sibling "
                             "assessment.md that never reached the live index "
                             "(the POST-phase knowledge leak). Report-only — it "
                             "NEVER changes the exit code, so every gate that "
                             "calls `worklog scan` WITHOUT the flag is "
                             "byte-for-byte unaffected.")
    p_scan.set_defaults(func=cmd_worklog_scan)


_EXPORTS = [
    "LEARNED_RE",
    "DECISION_RE",
    "PROMOTED_RE",
    "promoted_ids",
    "_real_ids",
    "_category_stems",
    "learning_stems",
    "decision_stems",
    "skill_promotion_ids",
    "_learned_real_ids",
    "_is_promoted",
    "scan_worklog",
    "cmd_worklog_scan",
    "register_worklog",
]
