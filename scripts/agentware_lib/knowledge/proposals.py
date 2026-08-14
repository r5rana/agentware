"""agentware_lib.knowledge.proposals — skill-proposal ledger reads + integrity (layer: knowledge).

Owns: the READ-ONLY skill-proposal staging surface — the append-only
references-ledger filename constant (PROPOSAL_LEDGER_NAME), the on-disk path
resolvers (proposal_dir, proposal_ledger_path), the ledger reader + derived
worthiness signal (read_proposal_ledger, proposal_count), the staged-slug
enumerator (existing_proposal_slugs), and the loop-run integrity guard folded
into `index validate` (proposal_integrity_errors, with its _malformed_ledger_lines
and _stored_proposal_count corruption helpers). Every function is read-only; a KB
with no `skills-proposed/` tier is entirely unaffected (INV-1).
Does NOT own: the proposal WRITE / lifecycle (create-or-increment ledger append,
near-duplicate slug dedup, ripe report, `skill promote`) — those reach
skills/fanout and stay in the proposal-lifecycle surface; the skills catalog (see
agentware_lib.features.skills); the index CRUD/validate/rebuild core (see
agentware_lib.knowledge.index) — this module READS index.SKILLS_PROPOSED_DIR only.
Layer deps: knowledge -> knowledge (agentware_lib.knowledge.index, for the
SKILLS_PROPOSED_DIR constant) + stdlib (os / re / json). Never imports
ops/retrieval/features or the entrypoint (no upward/forward edges); index does
NOT import this module, so there is no cycle.
Fan-in: 5 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import os
import re

import agentware_lib.knowledge.index as index


# Per-proposal append-only references ledger (feature 260722, Part 2). One JSONL
# event per proposal occurrence ({ref, author, date, run, worklog_line}); the
# proposal's worthiness signal is `proposal_count = count(unique refs)` with the
# uniqueness key = `ref`/feature, so re-running one feature N times does NOT
# manufacture ripeness. Stored as `.jsonl` (a frontmatter-less non-`.md` file)
# so `scan_entry_files`'s `.md` filter skips it and `index rebuild` never aborts.
PROPOSAL_LEDGER_NAME = "references.jsonl"


def _stored_proposal_count(kdir, slug):
    """Any `proposal_count:` value STORED in a proposal's SKILL.md frontmatter.

    Returns the parsed int, or None when no such line exists. The canonical
    count is DERIVED (`proposal_count` = unique refs in the `.jsonl` ledger) and
    is never persisted by the writer, so a clean proposal returns None. A stored
    value is therefore a corruption / hand-edit signal the integrity guard
    (Task 10) must FAIL on when it disagrees with the derived count. Read-only.
    """
    path = os.path.join(proposal_dir(kdir, slug), "SKILL.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        m = re.match(r"^proposal_count:\s*(-?\d+)\s*$", ln)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def _malformed_ledger_lines(kdir, slug):
    """List of (lineno, rawline) for non-blank ledger lines that are NOT a JSON object.

    The ledger is append-only `.jsonl`; every non-blank line MUST parse to a JSON
    object (dict). A line that is invalid JSON, or valid JSON that is not an
    object, is a corruption signal. Read-only; returns [] for a missing ledger
    (a proposal folder with no ledger is caught separately by the caller).
    """
    bad = []
    path = proposal_ledger_path(kdir, slug)
    try:
        with open(path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, start=1):
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except ValueError:
                    bad.append((i, s))
                    continue
                if not isinstance(obj, dict):
                    bad.append((i, s))
    except OSError:
        return []
    return bad


def proposal_integrity_errors(kdir, data):
    """Loop-run proposal-integrity guard (feature 260722, Task 10 / M2).

    Folded into `index validate` (which `run_pre_hooks` runs at the START of
    every loop iteration), alongside `roster_injection_errors`. Three FAIL
    conditions, all read-only:

      (a) LEAK — any `skills-proposed/` path is indexed as `category == "skills"`
          (which would route the proposal into the always-on skills roster /
          injection set). Proposals MUST stay `category == "references"`
          (recall-visible only) until human-gated `skill promote`.
      (b) MALFORMED LEDGER — a proposal's `.jsonl` references ledger carries a
          non-blank line that is not a JSON object, OR the ledger file is
          missing entirely (a staged proposal has no audit trail / frequency
          signal).
      (c) COUNT MISMATCH — a proposal SKILL.md stores a `proposal_count:` that
          disagrees with the DERIVED unique-ref count (the count is derived and
          never persisted by the writer, so a stored value that drifts is
          corruption).

    Returns a list of error strings (empty == clean). A KB with no
    `skills-proposed/` tier returns [] — entirely unaffected (INV-1).
    """
    errors = []
    entries = data.get("entries") if isinstance(data, dict) else None
    # (a) LEAK — a proposal indexed as an always-on skill.
    if isinstance(entries, list):
        prefix = index.SKILLS_PROPOSED_DIR + "/"
        for e in entries:
            if not isinstance(e, dict):
                continue
            path = (e.get("path") or "").replace(os.sep, "/")
            if path.startswith(prefix) and e.get("category") == "skills":
                errors.append(
                    "proposal '%s' (%s) is indexed as category 'skills' — a "
                    "`skills-proposed/` entry MUST stay category 'references' "
                    "until human-gated `skill promote`, else it leaks into the "
                    "always-on skills roster/injection set"
                    % (e.get("id") or "?", path))
    # (b)/(c) ledger + count integrity, per staged proposal.
    for slug in existing_proposal_slugs(kdir):
        if not os.path.isfile(proposal_ledger_path(kdir, slug)):
            errors.append(
                "proposal '%s' has no references ledger (%s) — a staged "
                "proposal must carry its append-only frequency audit trail"
                % (slug, PROPOSAL_LEDGER_NAME))
        else:
            for lineno, raw in _malformed_ledger_lines(kdir, slug):
                errors.append(
                    "proposal '%s' ledger line %d is malformed (not a JSON "
                    "object): %s" % (slug, lineno, raw[:80]))
        stored = _stored_proposal_count(kdir, slug)
        if stored is not None:
            derived = proposal_count(kdir, slug)
            if stored != derived:
                errors.append(
                    "proposal '%s' stores proposal_count=%d but the ledger "
                    "derives %d unique ref(s) — the count is DERIVED and must "
                    "not be persisted out of sync" % (slug, stored, derived))
    return errors


def proposal_dir(kdir, slug):
    """Absolute path to a proposal's staging folder `skills-proposed/<slug>`."""
    return os.path.join(kdir, index.SKILLS_PROPOSED_DIR, slug)


def proposal_ledger_path(kdir, slug):
    """Absolute path to a proposal's append-only `.jsonl` references ledger."""
    return os.path.join(proposal_dir(kdir, slug), PROPOSAL_LEDGER_NAME)


def read_proposal_ledger(kdir, slug):
    """Parse a proposal's `.jsonl` ledger -> list of event dicts (best-effort).

    Read-only. Skips blank / malformed lines silently here (the loop-run
    integrity guard in Task 10 is the place that FAILS on a malformed ledger);
    this reader just needs to compute a count for the create-or-increment path.
    """
    events = []
    path = proposal_ledger_path(kdir, slug)
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    events.append(obj)
    except OSError:
        return []
    return events


def proposal_count(kdir, slug):
    """Worthiness signal: number of UNIQUE refs in a proposal's ledger.

    Uniqueness key = `ref` (feature) so re-running one feature N times does NOT
    inflate the count — only DISTINCT features accumulate toward the threshold.
    """
    refs = set()
    for ev in read_proposal_ledger(kdir, slug):
        ref = ev.get("ref")
        if ref:
            refs.add(ref)
    return len(refs)


def existing_proposal_slugs(kdir):
    """Sorted list of proposal slugs currently staged under `skills-proposed/`."""
    base = os.path.join(kdir, index.SKILLS_PROPOSED_DIR)
    if not os.path.isdir(base):
        return []
    out = []
    for name in os.listdir(base):
        if os.path.isfile(os.path.join(base, name, "SKILL.md")):
            out.append(name)
    return sorted(out)


_EXPORTS = [
    "PROPOSAL_LEDGER_NAME",
    "proposal_dir",
    "proposal_ledger_path",
    "read_proposal_ledger",
    "proposal_count",
    "existing_proposal_slugs",
    "_stored_proposal_count",
    "_malformed_ledger_lines",
    "proposal_integrity_errors",
]
