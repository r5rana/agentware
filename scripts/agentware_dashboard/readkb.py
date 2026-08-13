"""agentware_dashboard.readkb — the KNOWLEDGE-BASE read model.

Owns: the payloads projected from the knowledge index — the aggregate
(`/api/kb`), the category drill-downs (`/api/kb/projects`,
`/api/kb/learnings`), one entry with its body (`/api/kb/learnings/<id>`), the
tag view (`/api/kb/tags/<tag>`), and the by-category feature map
(`/api/features`).
Does NOT own: the index itself (`agentware_lib.knowledge.index.indexread` is
the only reader called here), entry bodies
(`agentware_lib.retrieval.corpus._read_entry_body`), or any ranking — the
dashboard never re-orders what the toolkit returns.

READ-ONLY: this module adds NO writer. An index error is reported IN the payload
(`error`, plus the endpoint's empty shape) rather than raised, so one malformed
index cannot take the whole dashboard down.
"""

import urllib.parse
from http import HTTPStatus

from agentware_lib.knowledge.index import indexread
from agentware_lib.retrieval import corpus

from agentware_dashboard.reqparams import NO_KDIR, kdir_of


def _entries(kdir):
    """(data, err) from load_index. Read-only."""
    data, err = indexread.load_index(kdir)
    if err is not None:
        return None, err
    return data, None


def api_kb(request):
    """`/api/kb` — entry count, per-category tallies and the entry summaries."""
    kdir = kdir_of(request)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, entry_count=0, categories={})
    data, err = _entries(kdir)
    if err is not None:
        return HTTPStatus.OK, {"error": err, "entry_count": 0, "categories": {}}
    entries = data.get("entries", [])
    cats = {}
    for e in entries:
        cats[e.get("category", "uncategorized")] = \
            cats.get(e.get("category", "uncategorized"), 0) + 1
    tags = data.get("tags", {})
    return HTTPStatus.OK, {
        "entry_count": len(entries),
        "categories": cats,
        "category_count": len(cats),
        "tag_count": len(tags),
        "entries": [{
            "id": e.get("id"), "title": e.get("title"),
            "category": e.get("category"), "path": e.get("path"),
            "summary": e.get("summary"), "tags": e.get("tags", []),
        } for e in entries],
    }


def _kb_category(request, category):
    """Every indexed entry in one category — the shape both drill-downs serve."""
    kdir = kdir_of(request)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, category=category, entries=[])
    data, err = _entries(kdir)
    if err is not None:
        return HTTPStatus.OK, {"error": err, "category": category, "entries": []}
    rows = [e for e in data.get("entries", [])
            if e.get("category") == category]
    return HTTPStatus.OK, {"category": category, "count": len(rows),
                           "entries": rows}


def api_kb_projects(request):
    """`/api/kb/projects` — the projects category."""
    return _kb_category(request, "projects")


def api_kb_learnings(request):
    """`/api/kb/learnings` — the learnings category."""
    return _kb_category(request, "learnings")


def api_kb_learning_detail(request, entry_id):
    """`/api/kb/learnings/<id>` — one entry plus its file body, or 404."""
    kdir = kdir_of(request)
    entry_id = urllib.parse.unquote(entry_id)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, id=entry_id)
    data, err = _entries(kdir)
    if err is not None:
        return HTTPStatus.OK, {"error": err, "id": entry_id}
    entry = next((e for e in data.get("entries", [])
                  if e.get("id") == entry_id), None)
    if entry is None:
        return HTTPStatus.NOT_FOUND, {"error": "learning not found",
                                      "id": entry_id}
    return HTTPStatus.OK, {"entry": entry,
                           "body": corpus._read_entry_body(kdir, entry)}


def api_kb_tag(request, tag):
    """`/api/kb/tags/<tag>` — every entry carrying one tag."""
    kdir = kdir_of(request)
    tag = urllib.parse.unquote(tag)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, tag=tag, entries=[])
    data, err = _entries(kdir)
    if err is not None:
        return HTTPStatus.OK, {"error": err, "tag": tag, "entries": []}
    rows = [e for e in data.get("entries", [])
            if tag in (e.get("tags") or [])]
    return HTTPStatus.OK, {"tag": tag, "count": len(rows), "entries": rows}


def api_features(request):
    """`/api/features` — every entry grouped by category, for the feature map."""
    kdir = kdir_of(request)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, categories={}, entry_count=0)
    data, err = _entries(kdir)
    if err is not None:
        return HTTPStatus.OK, {"error": err, "categories": {}, "entry_count": 0}
    by_cat = {}
    for e in data.get("entries", []):
        by_cat.setdefault(e.get("category", "uncategorized"), []).append({
            "id": e.get("id"), "title": e.get("title"),
            "path": e.get("path"), "summary": e.get("summary"),
            "tags": e.get("tags", []),
        })
    return HTTPStatus.OK, {
        "categories": by_cat,
        "category_count": len(by_cat),
        "entry_count": sum(len(v) for v in by_cat.values()),
    }
