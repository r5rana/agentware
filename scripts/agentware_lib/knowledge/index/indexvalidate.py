"""agentware_lib.knowledge.index.indexvalidate — index validation + roster lint (layer: knowledge).

Owns: the index structural validator (validate_index + its per-entry/tag-map
sub-checks), the derived-roster rendering (_roster_link/_render_roster), the ONE
injection predicate the SessionStart hook and `index validate` share
(roster_is_injectable) and its durable lint (roster_injection_errors), plus the
knowledge validators tag_consistency_errors / template_conformance_errors /
orphaned_learning_errors and the rebuild-parity check _index_parity_errors.
Does NOT own: the layout constants + READ primitives ->
agentware_lib.knowledge.index.indexread; the write core (save_index/_do_add/the
tag-map mutators) -> agentware_lib.knowledge.index.indexwrite; the entry-file
scan/inference + the KB-rebuild orchestrators (rebuild_kb CALLS validate_index
and _render_roster from here) -> agentware_lib.knowledge.index.indexrebuild; the
`index validate` command surface -> agentware_lib.features.indexcmd.
Layer deps: knowledge -> core (module-level core.dates) + the SAME-PACKAGE sink
sibling agentware_lib.knowledge.index.indexread (module alias for functions, the
constants BY NAME so the moved bodies stay byte-verbatim), all DOWNWARD. NO
upward and NO cyclic import (indexread does not import this module).
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

validate_index's ERROR ORDERING is a hard BYTE CONTRACT relied on by the
equivalence gate, so the checks were split into named helpers that emit the SAME
strings in the SAME order: per-entry (required fields -> category -> path -> id ->
tags -> last_verified) for every entry in order, then entry->tag-map, then
tag-map->entry. No error string and no ordering changed in the split.
"""

import os
import re

import agentware_lib.core.dates as dates
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.knowledge.index.indexread as indexread
from agentware_lib.knowledge.index.indexread import (
    CATEGORIES, LEARNINGS_REL, REQUIRED_FIELDS)


# --- index structural validation ---------------------------------------------
def _entry_required_errors(entry, label):
    """Missing REQUIRED_FIELDS + an out-of-vocabulary category (in that order)."""
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in entry:
            errors.append("entry '%s' missing required field '%s'" % (label, field))

    cat = entry.get("category")
    if cat is not None and cat not in CATEGORIES:
        errors.append("entry '%s' has invalid category '%s'" % (label, cat))
    return errors


def _entry_identity_errors(kdir, entry, label, seen_ids, seen_paths):
    """Path existence + path/id uniqueness. MUTATES seen_paths/seen_ids (the
    first-wins registries validate_index threads through the entry loop)."""
    errors = []
    path = entry.get("path")
    if path:
        if not os.path.exists(os.path.join(kdir, path)):
            errors.append("entry '%s' path does not exist: %s" % (label, path))
        if path in seen_paths:
            errors.append("duplicate path '%s' (entries '%s' and '%s')"
                          % (path, seen_paths[path], label))
        else:
            seen_paths[path] = label

    eid = entry.get("id")
    if eid:
        if eid in seen_ids:
            errors.append("duplicate id '%s'" % eid)
        else:
            seen_ids[eid] = entry
    return errors


def _entry_optional_errors(entry, label):
    """Optional-field shape: `tags`/`relates` must be lists OF STRINGS;
    `last_verified`, when present, must be a YYYY-MM-DD date (absent is fine —
    treated as == created).

    Strict list-field gate (poisoned-index hardening). The lenient READERS DROP a
    bad `tags`/`relates` field or non-string element on the hot path so
    recall/audit/query never crash; `validate_index` is the DEDICATED strict gate
    that must REJECT the poison (exit 1) so a lenient reader can never silently
    MASK corruption forever. `list` (not `(list, tuple)`) matches JSON semantics
    (no tuples). Additive + byte-neutral on a real KB — all-string lists pass.
    """
    errors = []
    etags = entry.get("tags")
    if etags is not None and not isinstance(etags, list):
        errors.append("entry '%s' tags is not a list" % label)
    elif isinstance(etags, list) and not all(
            isinstance(x, str) for x in etags):
        errors.append("entry '%s' tags has a non-string element" % label)

    erel = entry.get("relates")
    if erel is not None and not isinstance(erel, list):
        errors.append("entry '%s' relates is not a list" % label)
    elif isinstance(erel, list) and not all(
            isinstance(x, str) for x in erel):
        errors.append("entry '%s' relates has a non-string element" % label)

    # Optional `last_verified` (freshness). Back-compat: absent is fine
    # (treated as == created). When present it MUST be a YYYY-MM-DD date.
    lv = entry.get("last_verified")
    if lv is not None and not dates._is_valid_date(lv):
        errors.append("entry '%s' last_verified is not YYYY-MM-DD: %r"
                      % (label, lv))
    return errors


def _entry_shape_errors(kdir, entry, i, seen_ids, seen_paths):
    """Every per-entry error, in the frozen order the monolith emitted them."""
    eid = entry.get("id")
    label = eid if eid else "#%d" % i
    errors = _entry_required_errors(entry, label)
    errors.extend(_entry_identity_errors(kdir, entry, label, seen_ids, seen_paths))
    errors.extend(_entry_optional_errors(entry, label))
    return errors


def _validate_entry_tag_errors(entries, tags):
    """Tag map bidirectional consistency: entry.tags -> tags map."""
    errors = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        eid = entry.get("id")
        # Iterate STRING tags only: a list carrying a dict element would crash
        # `tags.get(<dict>)` (unhashable). The malformed element is separately
        # rejected by `_entry_optional_errors`, so skipping it here only keeps
        # this consistency scan crash-robust — it never masks the poison.
        for t in frontmatter._entry_list_field(entry, "tags"):
            ids = tags.get(t)
            if not isinstance(ids, list) or eid not in ids:
                errors.append("tag map missing id '%s' under tag '%s'" % (eid, t))
    return errors


def _validate_tag_map_errors(tags, seen_ids):
    """Tag map bidirectional consistency: tags map -> entry.tags."""
    errors = []
    for t, ids in tags.items():
        if not isinstance(ids, list):
            errors.append("tag '%s' is not a list" % t)
            continue
        if len(ids) == 0:
            errors.append("tag '%s' has an empty entry list" % t)
        for eid in ids:
            entry = seen_ids.get(eid)
            if entry is None:
                errors.append("tag '%s' references unknown id '%s'" % (t, eid))
            # `_entry_list_field` guards a poisoned (scalar/dict) entry `tags`
            # here so the map->entry back-ref REPORTS the mismatch instead of
            # crashing on `t not in <scalar>`; a scalar `tags` is separately
            # rejected by `_entry_optional_errors`.
            elif t not in frontmatter._entry_list_field(entry, "tags"):
                errors.append("entry '%s' does not list tag '%s' (back-ref mismatch)"
                              % (eid, t))
    return errors


def validate_index(kdir, data):
    """Return a list of error strings for the index data (empty == valid).

    All entry paths are interpreted RELATIVE to the knowledge dir.
    """
    errors = []
    if not isinstance(data, dict):
        return ["index root is not a JSON object"]

    entries = data.get("entries")
    tags = data.get("tags")
    if not isinstance(entries, list):
        errors.append("'entries' is missing or not a list")
        entries = []
    if not isinstance(tags, dict):
        errors.append("'tags' is missing or not an object")
        tags = {}

    seen_ids = {}
    seen_paths = {}

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append("entry #%d is not an object" % i)
            continue
        errors.extend(_entry_shape_errors(kdir, entry, i, seen_ids, seen_paths))

    errors.extend(_validate_entry_tag_errors(entries, tags))
    errors.extend(_validate_tag_map_errors(tags, seen_ids))
    return errors


# --- roster rendering + injection lint --------------------------------------
def _roster_link(sub, rel_path):
    """Subdir-relative link target for a roster row (e.g. projects/x/index.md -> x/index.md)."""
    prefix = sub + "/"
    return rel_path[len(prefix):] if rel_path.startswith(prefix) else rel_path


def _render_roster(sub, entries):
    """Deterministic, derived `<sub>/index.md` roster (regenerated by rebuild)."""
    rows = [e for e in entries if e.get("category") == sub]
    out = ["# %s\n" % sub.capitalize(),
           "> Auto-generated roster — DO NOT edit. Source of truth is each entry's "
           "frontmatter; regenerate with `scripts/agentware index rebuild`.\n"]
    if not rows:
        out.append("_No entries yet._\n")
    else:
        out.append("| Title | Path | Summary |")
        out.append("|-------|------|---------|")
        for e in rows:
            link = _roster_link(sub, e.get("path") or "")
            out.append("| **%s** | [%s](%s) | %s |"
                       % (e.get("title") or "", link, link, e.get("summary") or ""))
        out.append("")
    return "\n".join(out) + "\n"


def roster_is_injectable(roster_text):
    """Shared predicate: does a rendered `<sub>/index.md` roster carry >=1 real
    always-on entry worth injecting into session context?

    ONE predicate the SessionStart hook (via `skill roster-injectable`) AND the
    durable `index validate` linter both call, so the two can never drift. The
    old hook grepped for Markdown LIST items (`- ` / `* `), but `_render_roster`
    emits a TABLE — so a populated roster never matched and was never injected
    (Bug 2). A roster is injectable iff it has >=1 table DATA row whose TITLE
    cell (first column) is non-empty after stripping bold markers. Header-only,
    `_No entries yet._`, and all-blank-title (`| **** | ... |`) rosters are NOT
    injectable — that all-blank guard is what stops a blank-cell row from
    injecting `****` fleet-wide (the F3 opposite-direction trap).
    """
    if not roster_text:
        return False
    for line in roster_text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells:
            continue
        title = cells[0]
        # Skip the header row ("Title") and its separator row (----- / :---:).
        if title.lower() == "title":
            continue
        if title and set(title) <= set("-: "):
            continue
        # Strip bold markers + whitespace; a real entry has a non-empty title.
        if title.replace("*", "").strip():
            return True
    return False


def _skill_field_errors(skills):
    """(a) every always-on skill must carry a non-empty title AND summary."""
    errors = []
    for e in skills:
        label = e.get("id") or e.get("path") or "?"
        if not (e.get("title") or "").strip():
            errors.append(
                "always-on skill '%s' renders an empty title — it would inject "
                "a blank `| **** |` roster row fleet-wide (field-capture "
                "regression; run `index rebuild`)" % label)
        if not (e.get("summary") or "").strip():
            errors.append(
                "always-on skill '%s' renders an empty summary "
                "(field-capture regression; run `index rebuild`)" % label)
    return errors


def roster_injection_errors(kdir, data):
    """Durable roster/injection lint (feature 260722, Task 4).

    Wired into `index validate`, which `run_pre_hooks` runs at the START of
    every loop iteration, so the Part-1 injection bugs can never silently
    regress. Two FAIL conditions, both sharing the SAME predicate the
    SessionStart hook calls (`roster_is_injectable`) so the gate and the hook
    can never drift:

      (a) any indexed ALWAYS-ON skill (`category == "skills"`) renders an empty
          title or summary — the field-CAPTURE bug regressing would land a
          blank `| **** |` roster row that injects `****` fleet-wide (F3);
      (b) a skills roster carrying >=1 real entry would NOT inject per the
          shared predicate — i.e. `_render_roster`'s output drifted away from
          what the hook detects as injectable.

    Returns a list of error strings (empty == clean). ONLY skill entries are
    inspected, so a FLAT / skill-free KB is entirely unaffected (INV-1). This
    lives in the validate GATE, not in `rebuild_kb`, so a rebuild still emits
    the deterministic roster byte-identically and only the gate flags a defect.
    """
    errors = []
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return errors
    skills = [e for e in entries
              if isinstance(e, dict) and e.get("category") == "skills"]
    if not skills:
        return errors
    # (a) every always-on skill must carry a non-empty title AND summary.
    errors.extend(_skill_field_errors(skills))
    # (b) the rendered skills roster with >=1 entry MUST be injectable per the
    # shared predicate — reuse the SAME function the hook calls so they cannot
    # drift.
    roster = _render_roster("skills", entries)
    if not roster_is_injectable(roster):
        errors.append(
            "skills roster carries %d entr%s but the shared injection predicate "
            "(`roster_is_injectable`) reports it NOT injectable — the roster "
            "format drifted from the SessionStart hook" % (
                len(skills), "y" if len(skills) == 1 else "ies"))
    return errors


# --- knowledge validators (tag/template/orphan + index parity) ---------------
def _index_parity_errors(committed, rebuilt):
    """Return [] iff `index rebuild` would not CHANGE any committed data.

    Parity is the no-op property: rebuilding from frontmatter must neither drop
    nor alter what the committed index.json already asserts. Rebuild ADDING a
    purely-defaulted field an entry lacked (e.g. `last_verified` == `created`,
    which a fresh `learn` entry omits until the first rebuild) is a benign no-op,
    NOT a divergence — so the check compares the committed entry set + the values
    committed actually carries, and ignores extra defaulted keys rebuild fills in.
    A different entry set, a dropped entry, or a changed committed value FAILS.
    """
    errs = []
    cmap = {e.get("id"): e for e in (committed.get("entries") or [])}
    rmap = {e.get("id"): e for e in (rebuilt.get("entries") or [])}
    only_c = sorted(set(cmap) - set(rmap))
    only_r = sorted(set(rmap) - set(cmap))
    if only_c:
        errs.append("entries in index.json not derivable from frontmatter: %s"
                    % ", ".join(only_c))
    if only_r:
        errs.append("entries derivable from frontmatter but absent from "
                    "index.json: %s" % ", ".join(only_r))
    for eid in sorted(set(cmap) & set(rmap)):
        c, r = cmap[eid], rmap[eid]
        for k, v in c.items():
            if r.get(k) != v:
                errs.append("entry %r field %r diverges: index.json=%r vs "
                            "frontmatter=>%r" % (eid, k, v, r.get(k)))
    return errs


def _tag_map_forward_errors(entries, tags):
    """entry.tags -> tags map (the `tag_consistency_errors` half emitted first)."""
    errors = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        # STRING tags only — a dict element would crash `tags.get(<dict>)`.
        for t in frontmatter._entry_list_field(e, "tags"):
            ids = tags.get(t)
            if not isinstance(ids, list) or eid not in ids:
                errors.append("tag map missing id '%s' under tag '%s'" % (eid, t))
    return errors


def _tag_map_backref_errors(tags, by_id):
    """tags map -> entry.tags (the `tag_consistency_errors` half emitted second)."""
    errors = []
    for t, ids in tags.items():
        if not isinstance(ids, list):
            errors.append("tag '%s' is not a list" % t)
            continue
        if not ids:
            errors.append("tag '%s' has an empty entry list" % t)
        for eid in ids:
            entry = by_id.get(eid)
            if entry is None:
                errors.append("tag '%s' references unknown id '%s'" % (t, eid))
            # Poisoned-entry guard, as in `_validate_tag_map_errors`: REPORT the
            # mismatch rather than crash on `t not in <scalar>`.
            elif t not in frontmatter._entry_list_field(entry, "tags"):
                errors.append("entry '%s' missing back-ref for tag '%s'" % (eid, t))
    return errors


def tag_consistency_errors(data):
    entries = data.get("entries") or []
    tags = data.get("tags") or {}
    by_id = {e.get("id"): e for e in entries
             if isinstance(e, dict) and e.get("id")}
    errors = _tag_map_forward_errors(entries, tags)
    errors.extend(_tag_map_backref_errors(tags, by_id))
    return errors


def _learning_template_errors(kdir):
    """Every `learnings/*.md` (bar the roster) must carry an H1 title."""
    errors = []
    ldir = indexread.learnings_dir(kdir)
    if os.path.isdir(ldir):
        for fn in sorted(os.listdir(ldir)):
            if not fn.endswith(".md") or fn == "index.md":
                continue
            fp = os.path.join(ldir, fn)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError as exc:
                errors.append("cannot read %s: %s" % (fp, exc))
                continue
            if not re.search(r"(?m)^#\s+\S", text):
                errors.append("learning '%s' missing H1 title" % fp)
    return errors


def _project_template_errors(kdir):
    """Every `projects/<name>/` must carry an `index.md` with an H1 title."""
    errors = []
    projects_dir = os.path.join(kdir, "projects")
    if os.path.isdir(projects_dir):
        for name in sorted(os.listdir(projects_dir)):
            d = os.path.join(projects_dir, name)
            if not os.path.isdir(d):
                continue
            fp = os.path.join(d, "index.md")
            if not os.path.isfile(fp):
                errors.append("project '%s' missing index.md" % name)
                continue
            with open(fp, "r", encoding="utf-8") as f:
                text = f.read()
            if not re.search(r"(?m)^#\s+\S", text):
                errors.append("project index '%s' missing H1 title" % fp)
    return errors


def template_conformance_errors(kdir, data):
    errors = _learning_template_errors(kdir)
    errors.extend(_project_template_errors(kdir))
    return errors


def orphaned_learning_errors(kdir, data):
    errors = []
    have = indexread.indexed_paths(data)
    ldir = indexread.learnings_dir(kdir)
    if os.path.isdir(ldir):
        for fn in sorted(os.listdir(ldir)):
            if not fn.endswith(".md") or fn == "index.md":
                continue
            rel = "%s/%s" % (LEARNINGS_REL, fn)
            if rel not in have:
                errors.append("orphaned learning (on disk, not indexed): %s" % rel)
    return errors


# Explicit facade export list (public AND underscore-private) — the entrypoint
# binds these onto its own namespace via the re-export loop so external consumers
# (tests/_fixtures.load_cli, the dashboard sidecar) keep reaching them as CLI.<name>.
_EXPORTS = [
    "validate_index",
    "_roster_link",
    "_render_roster",
    "roster_is_injectable",
    "roster_injection_errors",
    "_index_parity_errors",
    "tag_consistency_errors",
    "template_conformance_errors",
    "orphaned_learning_errors",
]
