"""agentware_lib.retrieval.vectorcache — the lazy embedder loader + its cache and the
derived per-model vector cache (layer: retrieval).

Owns: the lazy LOCAL-embedder loader (_load_embedder_module dotted-name/path import,
load_embedder with its (spec, model)-keyed _EMBEDDER_CACHE + _EMBEDDER_SENTINEL
negative memo, _reset_embedder_cache), the Mode-B availability probe
(semantic_embedder_available), the EFFECTIVE-retrieval-mode resolver that falls back to
Mode A when no local model is present (resolve_effective_retrieval_mode), and the
DERIVED per-model vector cache — its path/version constants (VECTOR_CACHE_DIR_REL/
VECTOR_CACHE_VERSION), the model slug + path helpers (_model_slug/vector_cache_path),
the per-document content fingerprint (_corpus_fingerprint), the graceful loader
(load_vector_cache), the incremental embed+persist builder (build_vector_cache), and the
Mode-B half of `index rebuild` (rebuild_vector_cache — wave-6 seam 13: load the embedder,
resolve the pinned model, scaffold the KB `.gitignore` via core.kblayout, then drive
build_vector_cache; returns a status dict of skipped / error / built and NEVER raises on
a missing/broken backend so the index rebuild is never blocked by this optional cache).
Does NOT own: the RRF hybrid fusion that consumes these vectors (retrieval.hybrid, a
later wave-3 seam); the shared tokenizer + corpus assembly build_vector_cache embeds
over (retrieval.corpus, via the _corpus module alias); the config/routing resolvers it
reads (core.config/core.routing); the `.gitignore` scaffolder + the index
system-of-record rebuild_vector_cache calls DOWNWARD (core.kblayout / knowledge.index).
Layer deps: retrieval -> core (agentware_lib.core.config as config for the retrieval-mode
constants + resolver, agentware_lib.core.routing as routing for the embedder backend/model
specs, agentware_lib.core.kblayout as kblayout for the idempotent `.gitignore` scaffolder
rebuild_vector_cache calls) + retrieval -> retrieval (agentware_lib.retrieval.corpus as the
module alias _corpus, so the global-resolve gate never walks a foreign corpus function held
in this module's __dict__) + retrieval -> knowledge LAZILY (agentware_lib.knowledge.index,
imported FUNCTION-LOCAL inside rebuild_vector_cache's data-omitted path for load_index, so
retrieval -> knowledge stays lazy — the acr precedent) + stdlib json/os/re (+ lazy
importlib/hashlib). Imports nothing above retrieval; nothing in core/ imports from here.
Fan-in: 6 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

INV-1/INV-6: the embedder backend is loaded LAZILY via a runtime importlib string — NO
static import of any optional backend ever appears here, so the stdlib-only static-import
surface (C-dep guard) stays green and Mode A runs with nothing installed. A missing/broken
backend NEVER crashes: load_embedder returns None and the caller falls back to deterministic
Mode A. INV-2: the vector cache is READ-ONLY here — the SOLE writer is `index rebuild`
(rebuild_vector_cache); load_vector_cache returns None on missing/corrupt/version- or
model-mismatch so correctness never depends on it. Determinism (Mode B): a pinned model
over an identical corpus yields byte-identical vectors + a byte-identical cache file
(sorted keys, embedder-rounded floats).
"""

import json
import os
import re

import agentware_lib.core.config as config
import agentware_lib.core.routing as routing
import agentware_lib.core.kblayout as kblayout
from agentware_lib.retrieval import corpus as _corpus


# Derived Mode-B vector cache (Phase 4.1): gitignored, regenerable, NEVER source
# of truth — like index.json. Per pinned-model JSON under `.cache/vectors/`,
# keyed by content fingerprint, written ONLY by `index rebuild` (sole writer,
# INV-2). Absence => graceful fallback to Mode A.
VECTOR_CACHE_DIR_REL = os.path.join(".cache", "vectors")
VECTOR_CACHE_VERSION = 1


# Lazy cache of loaded embedder instances, keyed by (spec, model). A failed load
# is memoized as _EMBEDDER_SENTINEL so a broken/absent backend is probed once,
# not on every recall. _reset_embedder_cache() clears it (test/config-change seam).
_EMBEDDER_SENTINEL = object()
_EMBEDDER_CACHE = {}


def _load_embedder_module(spec):
    """Dynamically import the backend module from a dotted name OR a .py path.

    Uses importlib (stdlib) with a RUNTIME string, so NO static `import` of the
    optional backend ever appears in this file — the stdlib-only static-import
    guard (INV-6) stays green and Mode A keeps running with nothing installed.
    Returns the loaded module or raises (the caller converts any failure to a
    graceful None).
    """
    import importlib  # stdlib, lazy
    looks_like_path = (
        spec.endswith(".py") or (os.sep in spec) or spec.startswith(".")
        or spec.startswith("~") or os.path.isfile(os.path.expanduser(spec)))
    if looks_like_path:
        import importlib.util  # stdlib, lazy
        path = os.path.abspath(os.path.expanduser(spec))
        modname = "agentware_embedder_" + re.sub(
            r"[^0-9A-Za-z_]", "_", os.path.basename(path))
        loader_spec = importlib.util.spec_from_file_location(modname, path)
        if loader_spec is None or loader_spec.loader is None:
            raise ImportError("cannot load embedder backend from %r" % spec)
        module = importlib.util.module_from_spec(loader_spec)
        loader_spec.loader.exec_module(module)
        return module
    return importlib.import_module(spec)


def load_embedder(backend=None, model=None):
    """Lazily load the configured LOCAL embedder, or None if unavailable.

    Contract — the backend module MUST expose a factory::

        get_embedder(model=None) -> Embedder

    where Embedder satisfies::

        embed(texts: list[str]) -> list[list[float]]   # fixed-dim, deterministic
        dim: int                                       # optional

    Determinism (INV-1, Mode B): a backend must return the SAME vector for the
    SAME input (pinned model + rounded floats); RRF later fuses on integer ranks.

    NEVER hard-imports the backend: the spec is a runtime config string loaded
    via importlib, so this script's static-import surface stays stdlib-only
    (C-dep guard / INV-6) and Mode A runs with nothing installed. ANY failure
    (unset spec, missing module, bad contract, import/exec error) returns None —
    the caller gracefully falls back to deterministic Mode A and NEVER crashes.
    """
    spec = backend if backend is not None else routing.resolve_embedder_backend()
    if not spec:
        return None
    mdl = model if model is not None else routing.resolve_embed_model()
    key = (spec, mdl)
    if key in _EMBEDDER_CACHE:
        cached = _EMBEDDER_CACHE[key]
        return None if cached is _EMBEDDER_SENTINEL else cached
    embedder = None
    try:
        module = _load_embedder_module(spec)
        factory = getattr(module, "get_embedder", None)
        if not callable(factory):
            raise AttributeError(
                "embedder backend %r exposes no get_embedder(model=...) factory"
                % spec)
        candidate = factory(model=mdl)
        if candidate is None or not callable(getattr(candidate, "embed", None)):
            raise TypeError(
                "embedder backend %r did not return an object with .embed()"
                % spec)
        embedder = candidate
    except Exception:
        # Graceful: a broken/absent backend NEVER crashes the CLI; Mode A is
        # always available. (We intentionally swallow the cause here; callers
        # surface a user-facing fallback notice via resolve_effective_*.)
        embedder = None
    _EMBEDDER_CACHE[key] = embedder if embedder is not None else _EMBEDDER_SENTINEL
    return embedder


def _reset_embedder_cache():
    """Clear the lazy embedder cache (test/seam hook for config changes)."""
    _EMBEDDER_CACHE.clear()


# --- Derived vector cache (Phase 4.1) ----------------------------------------
# A regenerable, gitignored artifact (like index.json / postings) holding one
# embedding per indexed entry, keyed by a content fingerprint + pinned model id.
# Sole writer is `index rebuild` (cmd_index_rebuild -> rebuild_vector_cache);
# NEVER written lazily inside recall/eval (INV-2 read-only). Missing/corrupt/
# stale/model-mismatch => graceful fallback to Mode A — the cache is an
# optimization, never a correctness dependency. Determinism (INV-1, Mode B):
# given a pinned model + identical corpus, delete+rebuild yields byte-identical
# vectors and a byte-identical cache file (sorted keys, embedder-rounded floats).


def _model_slug(model):
    """Filesystem-safe slug for a model id (used in the cache filename)."""
    return re.sub(r"[^0-9A-Za-z._-]", "_", (model or "default").strip()) or "default"


def vector_cache_path(kdir, model):
    """Absolute path to the per-model vector cache JSON under `.cache/vectors/`."""
    return os.path.join(kdir, VECTOR_CACHE_DIR_REL, _model_slug(model) + ".json")


def _corpus_fingerprint(doc_text):
    """Stable content fingerprint for one corpus document (sha256 hex).

    Keying vectors by this lets `index rebuild` re-embed ONLY content-changed
    entries (incremental) while reusing unchanged ones byte-for-byte.
    """
    import hashlib  # stdlib, lazy
    return hashlib.sha256((doc_text or "").encode("utf-8")).hexdigest()


def load_vector_cache(kdir, model):
    """Load the derived vector cache for `model`, or None if unusable.

    Graceful by contract: a missing file, corrupt JSON, version mismatch, or a
    model mismatch ALL return None so the caller falls back to Mode A. Never
    raises, never mutates. Returns a dict::

        {"version", "model", "dim", "vectors": {id: {"fp", "v": [floats]}}}
    """
    path = vector_cache_path(kdir, model)
    try:
        with open(path, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(cache, dict):
        return None
    if cache.get("version") != VECTOR_CACHE_VERSION:
        return None
    if cache.get("model") != model:
        return None
    if not isinstance(cache.get("vectors"), dict):
        return None
    return cache


def build_vector_cache(kdir, data, embedder, model):
    """Embed + persist corpus vectors for `model` (the `index rebuild` writer).

    Incremental: reuses any existing cached vector whose entry id + content
    fingerprint are unchanged, and embeds ONLY the changed/new entries (one
    batched `embedder.embed(...)` call). Writes the cache deterministically
    (sorted keys) so delete+rebuild is byte-identical given a pinned model.
    Returns a stats dict: {embedded, reused, total, dim, model, path}.
    """
    corpus = _corpus.build_corpus(kdir, data)
    prev = load_vector_cache(kdir, model) or {}
    prev_vectors = prev.get("vectors") or {}

    # Decide per entry: reuse (fingerprint unchanged) vs. (re)embed.
    plan = []          # [(id, fp, reused_vector_or_None)]
    to_embed_idx = []  # positions in `plan` needing embedding
    to_embed_txt = []
    for entry, doc_text in corpus:
        eid = entry.get("id")
        if eid is None:
            continue
        fp = _corpus_fingerprint(doc_text)
        cached = prev_vectors.get(eid)
        if isinstance(cached, dict) and cached.get("fp") == fp \
                and isinstance(cached.get("v"), list):
            plan.append((eid, fp, list(cached["v"])))
        else:
            plan.append((eid, fp, None))
            to_embed_idx.append(len(plan) - 1)
            to_embed_txt.append(doc_text)

    embedded = 0
    if to_embed_txt:
        vectors = embedder.embed(to_embed_txt)
        if len(vectors) != len(to_embed_txt):
            raise ValueError(
                "embedder returned %d vectors for %d inputs"
                % (len(vectors), len(to_embed_txt)))
        for pos, vec in zip(to_embed_idx, vectors):
            eid, fp, _ = plan[pos]
            plan[pos] = (eid, fp, list(vec))
            embedded += 1

    out_vectors = {}
    dim = 0
    for eid, fp, vec in plan:
        if vec is None:
            continue
        if dim == 0:
            dim = len(vec)
        elif len(vec) != dim:
            raise ValueError(
                "vector dim drift for %r: %d != %d" % (eid, len(vec), dim))
        out_vectors[eid] = {"fp": fp, "v": vec}

    cache = {
        "version": VECTOR_CACHE_VERSION,
        "model": model,
        "dim": dim,
        "vectors": out_vectors,
    }
    path = vector_cache_path(kdir, model)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Deterministic, byte-identical output: sorted keys + embedder-rounded floats.
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return {"embedded": embedded, "reused": len(plan) - embedded,
            "total": len(plan), "dim": dim, "model": model, "path": path}


def semantic_embedder_available():
    """True iff a LOCAL embedding backend is configured AND loadable (Mode B).

    Probes the optional, lazily-imported backend via load_embedder(): True only
    when AGENTWARE_EMBEDDER_BACKEND points at a module satisfying the embedder
    contract. Unset/missing/broken backend -> False, so a 'semantic'
    configuration gracefully falls back to 'deterministic' (Mode A). NEVER
    hard-imports an optional backend: the static-import surface of this script
    stays stdlib-only (C-dep guard) so Mode A runs with nothing installed.
    """
    return load_embedder() is not None


def resolve_effective_retrieval_mode():
    """Resolve the mode that will ACTUALLY run, honoring local-model availability.

    Returns (mode, fell_back, notice):
      - configured 'deterministic'            -> ('deterministic', False, '')
      - configured 'semantic' + model present -> ('semantic',      False, '')
      - configured 'semantic' + NO model      -> ('deterministic', True,  notice)

    Mode A is always available with nothing installed (the zero-install default
    invariant); Mode B is opt-in and inert until a local model exists.
    """
    configured = config.resolve_retrieval_mode()
    if configured == config.RETRIEVAL_MODE_SEMANTIC and not semantic_embedder_available():
        notice = (
            "retrieval_mode=semantic requested but no local embedding model is "
            "available; falling back to deterministic (Mode A / BM25). Install a "
            "local model and rebuild the vector cache to enable semantic retrieval.")
        return config.RETRIEVAL_MODE_DETERMINISTIC, True, notice
    return configured, False, ""


def rebuild_vector_cache(kdir, data=None):
    """Rebuild the derived vector cache iff a LOCAL embedder is configured.

    The Mode-B half of `index rebuild`. No backend configured/loadable => a
    no-op returning {"status": "skipped"} (nothing written; Mode A unchanged,
    zero-install default holds). Never raises on a missing backend; an embedder
    runtime error is surfaced as {"status": "error"} so the index rebuild itself
    is never blocked by an optional, regenerable cache.
    """
    embedder = load_embedder()
    if embedder is None:
        return {"status": "skipped",
                "reason": "no local embedding backend configured"}
    model = routing.resolve_embed_model()
    if data is None:
        # retrieval -> knowledge stays LAZY (function-local import, the acr
        # precedent): the index system-of-record loads here only on the
        # data-omitted path, so no module-level knowledge edge is created.
        from agentware_lib.knowledge import index
        data, err = index.load_index(kdir)
        if err is not None:
            return {"status": "error", "reason": err}
    # Guarantee the derived cache is gitignored BEFORE it is ever materialized,
    # so a Mode-B rebuild can never make `.cache/` committable (best-effort).
    try:
        kblayout._ensure_kb_gitignore(kdir)
    except OSError:
        pass
    try:
        stats = build_vector_cache(kdir, data, embedder, model)
    except Exception as exc:  # optional cache must never block index rebuild
        return {"status": "error", "reason": str(exc), "model": model}
    stats["status"] = "built"
    return stats


# Explicit export list (public AND underscore-private) — the M3/_EXPORTS convention:
# the manifest anchor-symbol set (tests/_manifest_gen.py) and the granular surface-floor
# denominator (tests/unit/retrieval/test_vectorcache.py). Like corpus/bm25 (and unlike
# the loop-bound core/knowledge modules), vectorcache is NOT in the entrypoint _reexport
# loop: these names reach the CLI facade (CLI.<name> for SourceFileLoader tests + the
# dashboard) via the by-name `from agentware_lib.retrieval.vectorcache import (...)` at
# the top of scripts/agentware. Keep THIS list and that import tuple in sync (both name
# all 15). rebuild_vector_cache moved here in wave-6 seam 13 (the Mode-B half of
# `index rebuild`); it joins this list + the entrypoint by-name import tuple.
_EXPORTS = [
    "VECTOR_CACHE_DIR_REL",
    "VECTOR_CACHE_VERSION",
    "_EMBEDDER_SENTINEL",
    "_EMBEDDER_CACHE",
    "_load_embedder_module",
    "load_embedder",
    "_reset_embedder_cache",
    "_model_slug",
    "vector_cache_path",
    "_corpus_fingerprint",
    "load_vector_cache",
    "build_vector_cache",
    "semantic_embedder_available",
    "resolve_effective_retrieval_mode",
    "rebuild_vector_cache",
]
