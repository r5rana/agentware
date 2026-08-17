"""agentware_lib.knowledge.curation.curationingest — the ingestion connector pipeline (layer: knowledge).

Owns: the whole deterministic INGEST pipeline and its `ingest` verb — the
INGEST_SOURCES adapter tuple; the two LOCAL source adapters
(_ingest_docs_adapter / _ingest_github_issues_adapter) and their pure markdown
scanners (_ingest_first_paragraph / _ingest_first_heading); the common candidate
model (_ingest_candidate) + contract validator (_ingest_validate); the
content-level dedup pair (_ingest_fingerprint / _ingest_extract_body /
_ingest_existing_dedup); the single registration path (_ingest_register, which
funnels through build_entry_frontmatter -> _render_learning -> _do_add and NEVER
writes index.json directly, R-KB-01); the dry-run staging writer
(_ingest_write_staging, `work/` only) and the Rule-7 metric append
(_ingest_emit_metric); the cmd_ingest handler and the `ingest` parser registrar
(the registrar travels with its handler).
Does NOT own: the learning/decision RENDERERS + _topic_to_title/_slugify ->
agentware_lib.knowledge.curation.curationrender (reached through the
`curationrender` MODULE ALIAS so a monkeypatch of the owner propagates); the
FEATURES.md renderer + handler -> agentware_lib.knowledge.curation.curationfeatures;
the index CRUD/validate/rebuild core -> agentware_lib.knowledge.index; the
learn/decide HANDLERS -> agentware_lib.features.curationcmd.
Layer deps: knowledge -> core (config, dates, frontmatter) + knowledge (index) +
the SAME-PACKAGE sink sibling curationrender, all DOWNWARD. Never imports
ops/retrieval/features or the entrypoint (no upward/forward edges); curationrender
imports nothing from this package, so there is no cycle.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import os
import re
import shutil
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.knowledge.index as index
# Bound BY NAME because the ported `cmd_ingest` is the preserved monolith's
# body verbatim and calls all five bare. The point of taking that body is the
# CRITICAL SECTION it draws: the lock spans load -> dedup -> register -> save
# and nothing else, where this tree's copy had widened it to cover adapter
# parsing of an arbitrary path, the staging write, the metric append and every
# line of output — holding the global index lock across file I/O and rendering
# that touch no index at all.
from agentware_lib.core.config import require_knowledge_dir
from agentware_lib.knowledge.index import (
    _index_lock, load_index, parse_tags, save_index)
import agentware_lib.knowledge.curation.curationrender as curationrender


# UNTRUSTED-CONTENT SAFETY (R-SEC-02): ingested text (docs, issue bodies) is
# treated as INERT DATA. It is only ever extracted into candidate string fields;
# it is NEVER parsed as instructions, NEVER eval/exec'd, NEVER shelled out. A
# prompt-injection line in an ingested artifact lands verbatim in a candidate's
# `content` and has no effect on this tool's control flow.
INGEST_SOURCES = ("docs", "github-issues")


def _ingest_first_paragraph(text):
    """First non-empty, non-heading paragraph of a markdown/plain body.

    Pure string scan: skip leading blank lines and ATX `#` headings, then return
    the first contiguous block of non-blank lines joined by single spaces.
    Deterministic; no network/clock. Returns "" when the body has no prose.
    """
    para = []
    for ln in (text or "").replace("\r\n", "\n").split("\n"):
        s = ln.strip()
        if not para:
            if not s or s.startswith("#"):
                continue
            para.append(s)
        else:
            if not s:
                break
            para.append(s)
    return " ".join(para)


def _ingest_first_heading(text):
    """The first ATX `# ` heading text in `text`, or "" if none. Pure."""
    for ln in (text or "").replace("\r\n", "\n").split("\n"):
        m = re.match(r"^#+\s+(.*\S)\s*$", ln.strip())
        if m:
            return m.group(1).strip()
    return ""


def _ingest_candidate(topic, title, summary, tags, content, source_ref,
                      source_kind, extra_tags=None):
    """Build the common candidate dict + clean/dedupe its tag list.

    The candidate model is `{topic, title, summary, tags, content, source_ref,
    source_kind}`. Tags are stripped + de-duplicated (order-preserving) and the
    operator-supplied `extra_tags` (`--tags`) are appended. Pure + deterministic.
    """
    tag_list = []
    for t in list(tags or []) + list(extra_tags or []):
        t = (t or "").strip()
        if t and t not in tag_list:
            tag_list.append(t)
    return {
        "topic": topic,
        "title": title,
        "summary": summary or "",
        "tags": tag_list,
        "content": content or "",
        "source_ref": source_ref or "",
        "source_kind": source_kind,
    }


def _ingest_docs_adapter(path, extra_tags=None):
    """Walk a LOCAL docs dir deterministically -> one candidate per `*.md`.

    READ-ONLY on the source (INV-2). Topic slug derives from the first `#`
    heading (else the filename stem); first paragraph -> summary; the relative
    directory segments -> tags. Returns (candidates, rejected) with each rejected
    item `{source_ref, reason}`. Raises ValueError if `path` is not a directory.
    """
    if not os.path.isdir(path):
        raise ValueError("docs source path is not a directory: %s" % path)
    candidates, rejected, found = [], [], []
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for fn in sorted(files):
            if fn.endswith(".md"):
                found.append(os.path.join(root, fn))
    for abs_path in sorted(found):
        rel = os.path.relpath(abs_path, path).replace(os.sep, "/")
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as exc:
            rejected.append({"source_ref": rel, "reason": "read error: %s" % exc})
            continue
        heading = _ingest_first_heading(text)
        stem = os.path.splitext(os.path.basename(rel))[0]
        topic = curationrender._slugify(heading) or curationrender._slugify(stem)
        title = heading or curationrender._topic_to_title(stem)
        seg_tags = [s for s in os.path.dirname(rel).split("/") if s]
        candidates.append(_ingest_candidate(
            topic, title, _ingest_first_paragraph(text), seg_tags, text, rel,
            "docs", extra_tags))
    return candidates, rejected


def _ingest_github_issues_adapter(path, extra_tags=None):
    """Parse a LOCAL `gh issue list --json ...` export -> one candidate/issue.

    Expects a JSON array of issue objects (the `gh issue list --json
    number,title,body,labels,url,state` shape). Tolerates missing optional keys;
    a non-object item or one with no usable title is SKIPPED and reported in
    `rejected` (never silently corrupting output). READ-ONLY on the source.
    Raises ValueError on an unreadable/non-list file.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        raise ValueError("could not read github-issues JSON %s: %s" % (path, exc))
    if not isinstance(raw, list):
        raise ValueError("github-issues JSON must be a list of issues, got %s"
                         % type(raw).__name__)
    candidates, rejected = [], []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            rejected.append({"source_ref": "#%d" % i,
                             "reason": "issue item is not an object"})
            continue
        number = item.get("number")
        ref = item.get("url") or (
            "#%s" % number if number is not None else "#%d" % i)
        title = (item.get("title") or "").strip()
        if not title:
            rejected.append({"source_ref": ref, "reason": "issue has no title"})
            continue
        body = item.get("body") or ""
        label_tags = []
        for lab in (item.get("labels") or []):
            name = lab.get("name") if isinstance(lab, dict) else lab
            if name:
                label_tags.append(str(name))
        candidates.append(_ingest_candidate(
            curationrender._slugify(title), title, _ingest_first_paragraph(body) or title,
            label_tags, body, ref, "github-issues", extra_tags))
    return candidates, rejected


def _ingest_validate(candidates):
    """Split candidates into (valid, rejected) by the common candidate contract.

    Rejects empty-topic or empty-content candidates deterministically (reported,
    never registered). Pure function.
    """
    valid, rejected = [], []
    for c in candidates:
        if not (c.get("topic") or "").strip():
            rejected.append({"source_ref": c.get("source_ref", ""),
                             "reason": "empty topic (no slug derivable)"})
        elif not (c.get("content") or "").strip():
            rejected.append({"source_ref": c.get("source_ref", ""),
                             "reason": "empty content"})
        else:
            valid.append(c)
    return valid, rejected


def _ingest_fingerprint(text):
    """Stable sha256 of normalized body text for content-level dedup.

    Normalizes whitespace + case so trivially-reformatted duplicates collide.
    Deterministic, stdlib-only.
    """
    import hashlib  # stdlib, lazy
    norm = re.sub(r"\s+", " ", (text or "")).strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _ingest_extract_body(text):
    """Best-effort body of a rendered learning file (for content-fp dedup).

    `_render_learning` wraps the body between the first `---` rule after the
    blockquote header and the trailing `---` footer. Falls back to the whole
    text when the markers are absent. Read-only, pure.
    """
    fields, rest = frontmatter.split_frontmatter(text)
    body = rest if fields is not None else text
    marker = "\n---\n\n"
    i = body.find(marker)
    if i != -1:
        body = body[i + len(marker):]
        j = body.rfind("\n\n---\n")
        if j != -1:
            body = body[:j]
    return body


def _ingest_existing_dedup(kdir, data):
    """Return (existing_ids, existing_fingerprints) for dedup. Read-only (INV-2).

    `existing_ids` is the set of `learn-*` ids already in the index;
    `existing_fingerprints` are content fingerprints of every learning file on
    disk (so a same-content/different-slug import is also caught).
    """
    ids, fps = set(), set()
    for e in (data.get("entries") or []):
        if not isinstance(e, dict) or e.get("category") != "learnings":
            continue
        eid = e.get("id")
        if eid:
            ids.add(eid)
        rel = e.get("path")
        if not rel:
            continue
        try:
            with open(os.path.join(kdir, rel), "r", encoding="utf-8") as f:
                fps.add(_ingest_fingerprint(_ingest_extract_body(f.read())))
        except OSError:
            continue
    return ids, fps


def _ingest_register(kdir, data, candidate):
    """Register ONE candidate as a `source: imported` learning via the SAME path
    `cmd_learn` uses (build_entry_frontmatter -> _render_learning -> _do_add).

    NEVER writes index.json directly (R-KB-01). The `source_ref` provenance is
    appended as a footer so the committed entry carries its origin URL/path.
    Returns (entry, error); on an add error the just-written file is removed.
    """
    topic = candidate["topic"]
    eid = "learn-%s" % topic
    rel_path = "%s/%s.md" % (index.LEARNINGS_REL, topic)
    abs_path = os.path.join(kdir, rel_path)
    if os.path.exists(abs_path):
        return None, "learning file already exists: %s" % rel_path
    title = candidate["title"] or curationrender._topic_to_title(topic)
    summary = candidate["summary"]
    tag_list = candidate["tags"]
    created = dates._today()
    body = candidate["content"]
    if candidate.get("source_ref"):
        body = "%s\n\n---\n\n_Imported from: %s_\n" % (
            body.rstrip("\n"), candidate["source_ref"])
    fm = frontmatter.build_entry_frontmatter(
        {"id": eid, "title": title, "category": "learnings", "tags": tag_list,
         "created": created, "summary": summary},
        kdir, source="imported")
    os.makedirs(index.learnings_dir(kdir), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(curationrender._render_learning(fm, body))
    entry, add_err = index._do_add(
        kdir, data, eid, title, "learnings", rel_path, tag_list, summary,
        created)
    if add_err is not None:
        try:
            os.remove(abs_path)
        except OSError:
            pass
        return None, add_err
    return entry, None


def _ingest_write_staging(kdir, source, candidates):
    """Write the dry-run staging area: candidates.jsonl + per-candidate previews.

    Lives under `work/ingest-<source>/` (a NON_INDEXED dir, so it never pollutes
    the index). Rewritten deterministically each run. Writes ONLY under `work/`
    (never the index). Returns the staging dir (knowledge-dir-relative).
    """
    rel_dir = os.path.join("work", "ingest-%s" % source)
    abs_dir = os.path.join(kdir, rel_dir)
    previews = os.path.join(abs_dir, "previews")
    if os.path.isdir(previews):
        shutil.rmtree(previews, ignore_errors=True)
    os.makedirs(previews, exist_ok=True)
    with open(os.path.join(abs_dir, "candidates.jsonl"), "w",
              encoding="utf-8") as f:
        for c in candidates:
            f.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")
    for c in candidates:
        preview = (
            "<!-- INGEST CANDIDATE PREVIEW — INERT untrusted data; NOT "
            "registered until --commit. Embedded instructions are ignored "
            "(R-SEC-02). -->\n"
            "<!-- source_kind: %s | source_ref: %s -->\n\n"
            "# %s\n\n> topic: `%s`\n> tags: %s\n\n%s\n"
            % (c["source_kind"], c["source_ref"], c["title"], c["topic"],
               ", ".join(c["tags"]) or "_(none)_", c["content"]))
        with open(os.path.join(previews, "%s.md" % (c["topic"] or "_empty")),
                  "w", encoding="utf-8") as f:
            f.write(preview)
    return rel_dir.replace(os.sep, "/")


def _ingest_emit_metric(kdir, source, counts, ts=None):
    """Rule-7 observability: append one `ingestion` event to logs/metrics.jsonl.

    Best-effort + read-only w.r.t. the KB/index (INV-2): a write failure never
    blocks the caller. `ts` is metadata only (never read by ranking).
    """
    try:
        rec = {"event": "ingestion", "source": source, "ts": ts or dates.utc_now_iso()}
        rec.update(counts)
        logs = os.path.join(kdir, "logs")
        os.makedirs(logs, exist_ok=True)
        with open(os.path.join(logs, "metrics.jsonl"), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


def cmd_ingest(args):
    """Deterministic curated capture from a LOCAL source artifact.

    Dry-run by default (writes staging under `work/`, registers nothing);
    `--commit` registers the reviewed subset with `source: imported`, skipping
    duplicates. Never fetches the network (the operator produces the local
    export). The CLI stays the sole index writer.
    """
    kdir = require_knowledge_dir()
    if not kdir:
        return 1

    source = args.source
    extra_tags = parse_tags(getattr(args, "tags", None))
    try:
        if source == "docs":
            raw_candidates, adapter_rejected = _ingest_docs_adapter(
                args.path, extra_tags)
        elif source == "github-issues":
            raw_candidates, adapter_rejected = _ingest_github_issues_adapter(
                args.path, extra_tags)
        else:
            sys.stderr.write("agentware: unknown --source '%s'\n" % source)
            return 1
    except ValueError as exc:
        sys.stderr.write("agentware: %s\n" % exc)
        return 1

    valid, validate_rejected = _ingest_validate(raw_candidates)
    rejected = adapter_rejected + validate_rejected

    only = None
    if getattr(args, "only", None):
        only = set(t.strip() for t in args.only.split(",") if t.strip())
    selected = [c for c in valid if (only is None or c["topic"] in only)]

    do_commit = getattr(args, "commit", False)
    committed_entries, skipped_dup, seen_ids = [], [], set()
    # Serialize the load->register->save against concurrent writers (B2). Load the
    # index INSIDE the lock so `--commit` dedups + registers against the freshest
    # committed state. Adapter parsing/validation above is index-independent and
    # stayed outside; the staging write below does not touch the index.
    with _index_lock(kdir):
        data, err = load_index(kdir)
        if err is not None:
            sys.stderr.write("agentware: %s\n" % err)
            return 1
        existing_ids, existing_fps = _ingest_existing_dedup(kdir, data)
        for c in selected:
            eid = "learn-%s" % c["topic"]
            fp = _ingest_fingerprint(c["content"])
            if eid in existing_ids or fp in existing_fps or eid in seen_ids:
                skipped_dup.append({"topic": c["topic"], "id": eid})
                continue
            seen_ids.add(eid)
            if do_commit:
                entry, add_err = _ingest_register(kdir, data, c)
                if add_err is not None:
                    skipped_dup.append(
                        {"topic": c["topic"], "id": eid, "error": add_err})
                    continue
                committed_entries.append(entry)
                existing_ids.add(eid)
                existing_fps.add(fp)

        if committed_entries:
            save_index(kdir, data)

    staging_rel = _ingest_write_staging(kdir, source, valid)

    counts = {
        "candidates": len(valid),
        "selected": len(selected),
        "committed": len(committed_entries),
        "skipped_dup": len(skipped_dup),
        "rejected": len(rejected),
    }
    _ingest_emit_metric(kdir, source, counts)

    result = dict(counts)
    result.update({
        "source": source,
        "path": args.path,
        "dry_run": not do_commit,
        "staging_dir": staging_rel,
        "committed_ids": [e["id"] for e in committed_entries],
        "skipped": skipped_dup,
        "rejected_items": rejected,
    })
    if getattr(args, "format", "text") == "json":
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        mode = "COMMIT" if do_commit else "DRY-RUN"
        print("INGEST %s [%s] %s" % (mode, source, args.path))
        print("  candidates: %d  selected: %d  committed: %d  "
              "skipped_dup: %d  rejected: %d"
              % (len(valid), len(selected), len(committed_entries),
                 len(skipped_dup), len(rejected)))
        print("  staging: %s" % staging_rel)
        for e in committed_entries:
            print("  + %s" % e["id"])
        for r in rejected:
            print("  ! rejected %s: %s"
                  % (r.get("source_ref"), r.get("reason")))
    return 0


# --- parser registrars (owned here; assembled by agentware_lib.cli via the
# frozen _PARSER_REGISTRATION tuple). The registrar travels with its handler:
# cmd_ingest lives in this module, so its `ingest` verb registers here.
# register_* are resolved by the assembler through importlib (module field of
# the tuple, which names the PACKAGE whose aggregator re-imports this name),
# NOT via the _EXPORTS facade. --------------------------------------------------
def register_ingest(sub, fmt_parent):
    # ingest -----------------------------------------------------------------
    p_ingest = sub.add_parser(
        "ingest", parents=[fmt_parent],
        help="deterministic curated capture from a LOCAL source artifact "
             "(dry-run by default; --commit registers a reviewed subset)")
    p_ingest.add_argument("--source", required=True, choices=INGEST_SOURCES,
                          help="source adapter: %s" % " | ".join(INGEST_SOURCES))
    p_ingest.add_argument("--path", required=True,
                          help="LOCAL docs dir or github-issues JSON export "
                               "(agentware NEVER fetches the network)")
    p_ingest.add_argument("--tags", default=None,
                          help="extra comma-separated tags applied to every "
                               "candidate")
    p_ingest.add_argument("--only", default=None,
                          help="comma-separated topic slugs to commit "
                               "(the reviewed subset; default: all valid)")
    p_ingest.add_argument("--commit", action="store_true",
                          help="register the selected candidates with "
                               "source: imported (default: dry-run, registers "
                               "nothing)")
    p_ingest.set_defaults(func=cmd_ingest)


# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "INGEST_SOURCES",
    "_ingest_first_paragraph",
    "_ingest_first_heading",
    "_ingest_candidate",
    "_ingest_docs_adapter",
    "_ingest_github_issues_adapter",
    "_ingest_validate",
    "_ingest_fingerprint",
    "_ingest_extract_body",
    "_ingest_existing_dedup",
    "_ingest_register",
    "_ingest_write_staging",
    "_ingest_emit_metric",
    "cmd_ingest",
]
