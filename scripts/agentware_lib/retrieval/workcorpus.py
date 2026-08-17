"""agentware_lib.retrieval.workcorpus -- the `recall --scope work` corpus, its cache and its plan-status weighting (layer: retrieval).

Owns: the work-corpus builder (`build_work_corpus` /
`_build_work_corpus_uncached`), the stat-digest cache that makes a warm recall
read ZERO bodies (`WORK_CORPUS_CACHE_REL` / `WORK_CORPUS_CACHE_VERSION` /
`_work_corpus_cache_path` / `_work_corpus_stat_digest` / `_load_work_corpus_cache`
/ `_write_work_corpus_cache`), the single guarded read (`_read_work_text`) and
the plan-status BM25 weighting (`_WORK_STATUS_MULT` / `_work_status_multiplier`).
Does NOT own: the curated corpus and the shared tokenizer -> retrieval.corpus;
BM25 itself -> retrieval.bm25; the `recall` verb that applies the multiplier ->
retrieval.recallcmd; the plan-status parse -> ops.plangraph.

WHY WORK DOCUMENTS ARE RANKED BY THEIR PLAN'S STATE
---------------------------------------------------
`work/` holds every feature's plan and worklog, live and finished alike, and BM25
alone cannot tell them apart — a superseded plan and the plan that replaced it
use the same vocabulary, so the superseded one competes on equal terms forever.
`_WORK_STATUS_MULT` downweights by the FEATURE's plan state: superseded and
cancelled hard (0.3), done moderately (0.7), and draft/ready/running/MISSING not
at all. The multiplier is confined to `--scope work`; the curated ranking is
byte-unchanged, which the gold-fixture gate holds.

SIBLINGS INHERIT, AND THE PLAN IS PARSED ONCE
---------------------------------------------
The state is read from the feature's `plan.md` ONCE per feature and stamped onto
every sibling document. A worklog is not separately "done" — it belongs to a
feature that is, and letting each file carry its own status would mean a
worklog outranking the plan it documents.

THE CACHE IS THE POINT, NOT AN OPTIMISATION
-------------------------------------------
`work/` grows without bound: every feature ever run leaves documents behind, so a
naive scope-work recall reads the entire history on every query. The cache keys
on a STAT DIGEST (paths, sizes, mtimes) rather than content, so deciding whether
it is valid costs a directory walk instead of a full read, and a warm recall
reads zero bodies. Keying on content would mean reading everything to find out
whether you needed to read everything.

Layer deps: retrieval -> retrieval (`corpus` for the tokenizer/estimator),
retrieval -> ops (`plangraph._read_plan_status`, imported lazily so the module
graph stays acyclic). Read-only apart from its own cache file.
Determinism: features and files are walked in sorted order and the digest is
order-stable, so the same tree yields the same corpus and the same cache hit
every run (INV-1).
"""

import json
import os
import re as _re

from agentware_lib.ops.plangraph import _PLAN_DEFAULT_STATE, _read_plan_status


# Derived, gitignored --scope work corpus cache (B8). Local-only (rebuilt per
# machine), so a (path,size,mtime) stat digest is a safe freshness key here —
# unlike the clone-surviving primary index. Keyed under the same `.cache/`.
WORK_CORPUS_CACHE_REL = os.path.join(".cache", "work-corpus.json")


WORK_CORPUS_CACHE_VERSION = 1


def _read_work_text(path):
    """Read a work/*.md file's full text (best-effort). None on any I/O error.

    The SINGLE point of work-body I/O so a WARM `--scope work` cache can prove it
    re-reads ZERO work bodies (the counter test monkeypatches this).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (IOError, UnicodeDecodeError):
        return None


def _work_corpus_stat_digest(kdir):
    """Freshness digest over the work/ *.md file SET: sorted (relpath, size,
    mtime_ns). NOT (path,size) alone (misses a same-size in-place edit) and NOT a
    body hash (that would re-read every body, defeating the cache). Local-only —
    the cache is gitignored + rebuilt per machine, so mtime is a safe key here."""
    import hashlib
    work_dir = os.path.join(kdir, "work")
    parts = []
    for root, _dirs, files in os.walk(work_dir):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            p = os.path.join(root, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            rel = os.path.relpath(p, kdir).replace(os.sep, "/")
            parts.append((rel, st.st_size, st.st_mtime_ns))
    parts.sort()
    h = hashlib.sha256()
    for rel, size, mtime in parts:
        h.update(("%s\x00%d\x00%d\x01" % (rel, size, mtime)).encode("utf-8"))
    return h.hexdigest()


def _work_corpus_cache_path(kdir):
    return os.path.join(kdir, WORK_CORPUS_CACHE_REL)


def _load_work_corpus_cache(kdir, digest):
    """Return the cached corpus [(entry, doc_text), ...] iff the cache is present,
    well-formed, the right version, AND its stat digest matches `digest`. Else None
    (caller rebuilds). Graceful: never raises, reads ZERO work bodies."""
    try:
        with open(_work_corpus_cache_path(kdir), "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(cache, dict):
        return None
    if cache.get("version") != WORK_CORPUS_CACHE_VERSION:
        return None
    if cache.get("digest") != digest:
        return None
    rows = cache.get("corpus")
    if not isinstance(rows, list):
        return None
    corpus = []
    for row in rows:
        if (not isinstance(row, list) or len(row) != 2
                or not isinstance(row[0], dict)):
            return None
        corpus.append((row[0], row[1]))
    return corpus


def _write_work_corpus_cache(kdir, digest, corpus):
    """Persist the work corpus under gitignored .cache/ (best-effort, never raises).

    `.cache/` is already in KB_GITIGNORE_REQUIRED, so this local-only cache is never
    committed. A write failure just means the next call rebuilds (correctness intact).
    """
    try:
        path = _work_corpus_cache_path(kdir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {"version": WORK_CORPUS_CACHE_VERSION, "digest": digest,
                   "corpus": [[e, t] for (e, t) in corpus]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.write("\n")
    except (OSError, TypeError):
        pass


def build_work_corpus(kdir):
    """Build (cached) a synthetic corpus from work/*.md files for --scope work.

    Returns [(pseudo_entry, doc_text), ...] where pseudo_entry has the same
    dict shape as index entries (id, title, summary, tags, path, category,
    created) but is EPHEMERAL — never written to index.json (work/ stays UNINDEXED).

    Determinism: `created` comes from the feature dir's YYMMDD prefix (stable),
    NEVER filesystem mtime. Ordering is id-sorted for reproducibility.

    CACHED (B8): a WARM `--scope work` recall re-reads ZERO work bodies — freshness
    is the (path,size,mtime) stat digest above, and a match returns the serialized
    corpus from a gitignored .cache/ file. A miss (or first call) builds + persists.
    Byte-identical results to the uncached path (the cache round-trips the exact
    corpus, and scoring recomputes over it deterministically).
    """
    work_dir = os.path.join(kdir, "work")
    if not os.path.isdir(work_dir):
        return []
    digest = _work_corpus_stat_digest(kdir)
    cached = _load_work_corpus_cache(kdir, digest)
    if cached is not None:
        return cached
    corpus = _build_work_corpus_uncached(kdir)
    _write_work_corpus_cache(kdir, digest, corpus)
    return corpus


def _build_work_corpus_uncached(kdir):
    """The live build (reads every work body). Called only on a cache miss."""
    import re as _re
    work_dir = os.path.join(kdir, "work")
    if not os.path.isdir(work_dir):
        return []
    # Enumerate features ONE level deeper for the auto/ lane (feature 260710):
    # (feature_id, feature_path). A top-level `auto` dir is a container, not a
    # feature — descend into work/auto/<slug>/ and namespace the id as auto/<slug>.
    feature_dirs = []
    for name in sorted(os.listdir(work_dir)):
        p = os.path.join(work_dir, name)
        if not os.path.isdir(p):
            continue
        if name == "auto":
            try:
                for sub in sorted(os.listdir(p)):
                    sp = os.path.join(p, sub)
                    if os.path.isdir(sp):
                        feature_dirs.append(("auto/" + sub, sp))
            except OSError:
                pass
        else:
            feature_dirs.append((name, p))
    corpus = []
    for feature_id, fpath in feature_dirs:
        slug = feature_id.split("/")[-1]
        # Extract created date from the slug's YYMMDD prefix (stable, not mtime).
        date_match = _re.match(r"(\d{6})", slug)
        created = ""
        if date_match:
            d = date_match.group(1)
            created = "20%s-%s-%s" % (d[:2], d[2:4], d[4:6])
        # Parse the feature's plan.md `> Status:` ONCE; every sibling doc inherits
        # the same feature-level BM25 multiplier (feature 260710).
        feat_state = _PLAN_DEFAULT_STATE
        plan_md = os.path.join(fpath, "plan.md")
        if os.path.isfile(plan_md):
            _ptext = _read_work_text(plan_md)
            if _ptext is not None:
                feat_state, _c = _read_plan_status(_ptext)
        status_mult = _work_status_multiplier(feat_state)
        for fname in sorted(os.listdir(fpath)):
            if not fname.endswith(".md"):
                continue
            md_path = os.path.join(fpath, fname)
            if not os.path.isfile(md_path):
                continue
            rel_path = "work/%s/%s" % (feature_id, fname)
            entry_id = "%s/%s" % (feature_id, fname.replace(".md", ""))
            # ONE read per file (routed through _read_work_text so a warm cache can
            # prove zero body reads); skip a file that cannot be read.
            body = _read_work_text(md_path)
            if body is None:
                continue
            # Extract title from the first heading line.
            title = ""
            for line in body.splitlines():
                line = line.strip()
                if line.startswith("#"):
                    title = _re.sub(r"^#+\s*", "", line)
                    break
            pseudo_entry = {
                "id": entry_id,
                "title": title,
                "summary": "",
                "tags": ["work"],
                "path": rel_path,
                "category": "work",
                "created": created,
                "status": feat_state,
                "status_mult": status_mult,
            }
            doc_text = "\n".join([title, "", body])
            corpus.append((pseudo_entry, doc_text))
    return corpus


# `recall --scope work` BM25 multipliers by plan state (feature 260710): a
# superseded/cancelled feature is strongly downranked, done moderately, and
# draft/ready/running/MISSING are neutral. Confined to --scope work — the default/
# curated ranking is byte-unchanged (gold-fixture gate).
_WORK_STATUS_MULT = {"superseded": 0.3, "cancelled": 0.3, "done": 0.7}


def _work_status_multiplier(state):
    """Multiplier for a plan state; draft/ready/running/MISSING -> 1.0 (neutral)."""
    return _WORK_STATUS_MULT.get((state or "").lower(), 1.0)


_EXPORTS = [
    "WORK_CORPUS_CACHE_REL",
    "WORK_CORPUS_CACHE_VERSION",
    "_WORK_STATUS_MULT",
    "_work_status_multiplier",
    "_work_corpus_cache_path",
    "_read_work_text",
    "_work_corpus_stat_digest",
    "_load_work_corpus_cache",
    "_write_work_corpus_cache",
    "_build_work_corpus_uncached",
    "build_work_corpus",
]

__all__ = list(_EXPORTS)
