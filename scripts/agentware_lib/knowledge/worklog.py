"""agentware_lib.knowledge.worklog — R-SI-03/R-AUTO-05 worklog promotion gate (layer: knowledge).

Owns: the completion-gate worklog scanner — the LEARNED/DECISION/PROMOTED marker
regexes (LEARNED_RE, DECISION_RE, PROMOTED_RE), the promotion-resolution closure
(promoted_ids, _real_ids, _category_stems -- the filtered UNION of its two
INDEPENDENT stem sources, _indexed_category_stems (what the index registered) and
_dir_md_stems (what is on disk) -- learning_stems, decision_stems,
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
# Bound BY NAME because the ported `cmd_worklog_scan` is the preserved
# monolith's body verbatim and calls both bare. Re-spelling them to the
# module-qualified form used elsewhere in this file would make a byte-
# comparable move into a hand transcription, which is the one thing this
# migration's verification cannot check.
from agentware_lib.core.config import require_knowledge_dir
from agentware_lib.knowledge.index import load_index
from agentware_lib.knowledge.curation import _slugify


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
    r"(?:\(\s*Promoted:\s*"
    r"|\[\s*promoted\s*(?:->|" "→" r")\s*"
    r"|\[\s*kb:\s*)"
    r"([a-z0-9][a-z0-9-]*)", re.I)


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


def _indexed_category_stems(data, category, id_prefix):
    """Stems the INDEX knows for `category`: each entry's filename stem and, when
    its id carries `id_prefix`, the id with that prefix stripped.

    Both are added — an entry filed as `learnings/a-thing.md` under the id
    `learn-a-thing` contributes one stem, and one whose id and filename DISAGREE
    contributes two, because a marker may name either. Lowercased, because a
    marker's slug is matched case-insensitively.
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
    return stems


def _dir_md_stems(kdir, subdir):
    """Stems ON DISK under `<kdir>/<subdir>`: every `*.md` except `index.md`.

    The second, INDEPENDENT source. An entry written but not yet registered is
    invisible to the index and visible here, which is why the two are unioned
    rather than one being derived from the other. An absent subdir — or one that
    is a FILE — contributes nothing rather than raising.
    """
    stems = set()
    d = os.path.join(kdir, subdir)
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.endswith(".md") and fn != "index.md":
                stems.add(fn[:-3].lower())
    return stems


def _category_stems(kdir, data, category, id_prefix, subdir):
    """Filename/id stems of every entry in `category` (for promotion checks).

    The UNION of the two independent sources above, minus the empty stem (an
    entry whose id is exactly the prefix, or a file named `.md`, would otherwise
    contribute a stem that matches every marker). Generic over the marker family
    so `> LEARNED:` (learnings) and `> DECISION:` (decisions) share one
    implementation.
    """
    stems = _indexed_category_stems(data, category, id_prefix)
    stems |= _dir_md_stems(kdir, subdir)
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


def _is_promoted(marker_text, stems, real_ids, all_ids=None):
    """A marker is promoted iff:

    * primary — it carries an explicit `(Promoted: <id>)` / `[promoted -> <id>]` /
      `[kb: <id>]` reference whose id resolves EXACTLY to a real entry. Resolution
      is against `real_ids` (same category) unioned with `all_ids` when supplied
      (any category, so skill/decision refs on a `> LEARNED:` line resolve); or
    * fallback (no reference) — its slug EQUALS a same-category stem exactly.

    The legacy substring branches (`stem in slug` / `slug in stem`) are gone:
    they produced false positives (short slug inside an unrelated stem) and could
    never be trusted. Pure string/set comparison over on-disk data -> INV-1
    deterministic, INV-2 read-only.
    """
    refs = promoted_ids(marker_text)
    if refs:
        universe = real_ids if all_ids is None else (real_ids | all_ids)
        return any(r in universe for r in refs)
    slug = _slugify(marker_text)
    if not slug:
        return False
    return any(slug == stem for stem in stems)


# A `> WAIVED: <reason>` line legitimately CLOSES the most recently-scanned
# still-open marker — an explicit "not worth promoting" decision (R-SI-03), so a
# waived marker is not counted unpromoted.
WAIVED_RE = re.compile(r"^\s*(?:[-*+]\s+)?>?\s*WAIVED:\s*(.*?)\s*$")


def scan_worklog_markers(path, kdir, data):
    """Single source of truth for worklog marker scanning (INV-2 read-only).

    Recognizes `> LEARNED:` / `> DECISION:` markers in bare, blockquote, and
    bullet-prefixed (`- > LEARNED:`) forms; a marker is CLOSED when it carries an
    explicit promotion reference (`(Promoted: <id>)`, `[promoted -> <id>]`,
    unicode `[promoted -> <id>]`, or legacy `[kb: <id>]`) resolving EXACTLY to a
    real entry in ANY category (skills included), or when its slug equals a
    same-category entry stem. A `> WAIVED: <reason>` line closes the most
    recently-scanned still-open marker.

    Returns a dict:
      {"unpromoted": [{line,text,kind}], "total": int, "waived": int,
       "per_kind": {"learned": {"total","unpromoted"}, "decision": {...}}}
    Deterministic; opens the file leniently (never mutates the KB).
    """
    l_stems = learning_stems(kdir, data)
    d_stems = decision_stems(kdir, data)
    all_ids = all_entry_ids(kdir, data)
    kinds = (
        ("learned", LEARNED_RE, l_stems, _real_ids(l_stems, "learn-")),
        ("decision", DECISION_RE, d_stems, _real_ids(d_stems, "decide-")),
    )
    per_kind = {"learned": {"total": 0, "unpromoted": 0},
                "decision": {"total": 0, "unpromoted": 0}}
    empty = {"unpromoted": [], "total": 0, "waived": 0, "per_kind": per_kind}
    try:
        f = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return empty
    unpromoted, total, waived = [], 0, 0
    with f:
        for lineno, line in enumerate(f, start=1):
            # A `> WAIVED:` line closes the most recently-scanned still-open
            # marker (an explicit "not worth promoting" decision).
            if WAIVED_RE.match(line):
                if unpromoted:
                    closed = unpromoted.pop()
                    per_kind[closed["kind"]]["unpromoted"] -= 1
                    waived += 1
                continue
            for kind, rx, stems, real_ids in kinds:
                m = rx.match(line)
                if not m:
                    continue
                text = m.group(1).strip()
                if not text:
                    continue
                total += 1
                per_kind[kind]["total"] += 1
                if not _is_promoted(text, stems, real_ids, all_ids):
                    unpromoted.append({"line": lineno, "text": text,
                                       "kind": kind})
                    per_kind[kind]["unpromoted"] += 1
                break
    return {"unpromoted": unpromoted, "total": total, "waived": waived,
            "per_kind": per_kind}


def scan_worklog(path, kdir, data):
    """Scan a worklog for unpromoted `> LEARNED:` and `> DECISION:` markers.

    Both families gate completion (R-SI-03 / R-AUTO-05). Back-compat wrapper over
    `scan_worklog_markers`: returns `(unpromoted, total)`; the combined
    `unpromoted` list keeps the original exit semantics (non-empty -> fail) and
    each item carries a `kind` ("learned"|"decision") so callers can label it.
    """
    r = scan_worklog_markers(path, kdir, data)
    return r["unpromoted"], r["total"]


# Extracted-Knowledge 'Suggested ID' line in an assessment.md. Tolerates both
# markdown emphasis styles the POST prompt produces:
#   - **Suggested ID:** `learn-foo`
#   - **Suggested ID**: `learn-foo` *(already promoted)*
SUGGESTED_ID_RE = re.compile(r"Suggested\s+ID[*:\s]*`([a-z0-9][a-z0-9-]*)`", re.I)


def scan_assessment_ids(path, data):
    """Extracted-Knowledge 'Suggested ID' ids in `path` that do NOT resolve to a
    live index entry (same id semantics as `query --id`: exact match against the
    index `entries` ids — intentionally NOT the looser stem resolution used by
    _is_promoted). Read-only; each item is {"line", "id"}. A missing or
    unreadable assessment.md yields []."""
    if not path or not os.path.isfile(path):
        return []
    live_ids = {e.get("id") for e in (data.get("entries") or [])
                if isinstance(e, dict) and e.get("id")}
    unresolved = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            m = SUGGESTED_ID_RE.search(line)
            if not m:
                continue
            sid = m.group(1).lower()
            if sid not in live_ids:
                unresolved.append({"line": lineno, "id": sid})
    return unresolved


def cmd_worklog_scan(args):
    if not os.path.isfile(args.path):
        sys.stderr.write("agentware: worklog not found: %s\n" % args.path)
        return 1
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    data, err = load_index(kdir)
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1

    unpromoted, total = scan_worklog(args.path, kdir, data)
    ok = len(unpromoted) == 0

    # Advisory sidecar (opt-in --also-assessment): flag Extracted-Knowledge
    # 'Suggested ID' entries in the sibling assessment.md that never made it into
    # the live index (the POST-phase knowledge leak). Report-only — it NEVER
    # changes the exit code, so the run_post_hooks / kb-sync / pre-hook gates that
    # call `worklog scan` WITHOUT the flag stay byte-for-byte unaffected.
    assessment_unpromoted = None
    if getattr(args, "also_assessment", False):
        assessment_path = os.path.join(
            os.path.dirname(os.path.abspath(args.path)), "assessment.md")
        assessment_unpromoted = scan_assessment_ids(assessment_path, data)

    if getattr(args, "format", "text") == "json":
        payload = {"unpromoted": unpromoted, "total": total}
        if assessment_unpromoted is not None:
            payload["assessment_unpromoted"] = assessment_unpromoted
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
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
        if assessment_unpromoted:
            print("ASSESSMENT SIDECAR: %d Extracted-Knowledge 'Suggested ID'(s) "
                  "not in the live index (advisory):" % len(assessment_unpromoted))
            for item in assessment_unpromoted:
                print("  line %d: %s" % (item["line"], item["id"]))
    return 0 if ok else 1


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("worklog", 1500, "register_worklog"),
)


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
