"""agentware_lib.retrieval.vectorcache — the lazy embedder loader + its cache and the
derived per-model vector cache (layer: retrieval).

Owns: the lazy LOCAL-embedder loader (_load_embedder_module registered-name/dotted-name/
path import, load_embedder with its (spec, model)-keyed _EMBEDDER_CACHE +
_EMBEDDER_SENTINEL negative memo, the _EMBEDDER_REJECTIONS record of PROTOCOL
violations + its embedder_rejection accessor, _reset_embedder_cache), the Mode-B
availability probe
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
Does NOT own: the embedder PROTOCOL, its named rejection error, its validators or the
registry of shipped backends (core.embedderapi — the extension point; this module is its
only lib consumer); the RRF hybrid fusion that consumes these vectors (retrieval.hybrid, a
later wave-3 seam); the shared tokenizer + corpus assembly build_vector_cache embeds
over (retrieval.corpus, via the _corpus module alias); the config/routing resolvers it
reads (core.config/core.routing); the `.gitignore` scaffolder + the index
system-of-record rebuild_vector_cache calls DOWNWARD (core.kblayout / knowledge.index).
Layer deps: retrieval -> core (agentware_lib.core.config as config for the retrieval-mode
constants + resolver, agentware_lib.core.routing as routing for the embedder backend/model
specs, agentware_lib.core.embedderapi as embedderapi for the protocol + registry,
agentware_lib.core.kblayout as kblayout for the idempotent `.gitignore` scaffolder
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
Mode A. ABSENCE IS SILENT, BREAKAGE IS LOUD: an unset spec or an ImportError is the
zero-install default and says nothing, while a backend that IS present and fails the
core.embedderapi protocol is recorded in _EMBEDDER_REJECTIONS, named on stderr and folded
into the mode-fallback notice — because "the operator configured semantic retrieval and
got BM25" is otherwise indistinguishable from "semantic retrieval is working". INV-2: the vector cache is READ-ONLY here — the SOLE writer is `index rebuild`
(rebuild_vector_cache); load_vector_cache returns None on missing/corrupt/version- or
model-mismatch so correctness never depends on it. Determinism (Mode B): a pinned model
over an identical corpus yields byte-identical vectors + a byte-identical cache file
(sorted keys, embedder-rounded floats).
"""

import json
import os
import re
import sys

import agentware_lib.core.config as config
import agentware_lib.core.routing as routing
import agentware_lib.core.embedderapi as embedderapi
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

# PROTOCOL rejections, keyed exactly like _EMBEDDER_CACHE. Populated only when a
# backend is PRESENT and violates the core.embedderapi contract — never on mere
# absence, which is the zero-install default. Read back by embedder_rejection()
# so `config`/`recall` can say WHY Mode B is not running instead of quietly
# ranking worse. Cleared with the cache, so the warning is emitted once per
# (spec, model) per process rather than on every recall.
_EMBEDDER_REJECTIONS = {}

# What "the backend is ABSENT" looks like as an exception, and therefore what
# stays SILENT. ImportError (with ModuleNotFoundError under it) covers a dotted
# spec naming nothing installed and a backend whose own optional dependency is
# missing. The three OSError shapes cover a PATH spec naming nothing: a path
# backend is exec'd from disk, so a typo surfaces as FileNotFoundError from the
# loader, NOT as an ImportError — found by asserting the silence rather than
# assuming it. Every OTHER exception means the module was found and failed while
# running, which is installed-and-broken, and that is reported.
_ABSENT_BACKEND_ERRORS = (ImportError, FileNotFoundError, IsADirectoryError,
                          NotADirectoryError)


def _load_embedder_module(spec):
    """Import the backend module: a REGISTERED name, a dotted name, or a .py path.

    A registered name (`embedderapi.REGISTERED_BACKENDS` — `fastembed`,
    `ollama`) resolves to its dotted module first; anything else keeps the
    pre-registry behaviour verbatim, so every spec that resolved before still
    resolves.

    Uses importlib (stdlib) with a RUNTIME string, so NO static `import` of the
    optional backend ever appears in this file — the stdlib-only static-import
    guard (INV-6) stays green and Mode A keeps running with nothing installed.
    Returns the loaded module or raises (the caller decides whether the failure
    is an ABSENCE or a rejection).

    NO sys.path bootstrap is needed for the backend's own `import agentware_lib`
    — and a guard for it was written, could not be made to fail, and was removed
    rather than shipped. Python resolves a SUBMODULE through its parent
    package's `__path__` (an absolute path), not through `sys.path`, and this
    loader lives INSIDE `agentware_lib`, so the package is necessarily imported
    before any backend body runs. Reproduced directly: with `scripts/` taken off
    `sys.path` and `core.gitio` not yet in `sys.modules`, a path-loaded backend
    still imports it. `test_a_backend_may_import_a_lib_module_the_cli_has_not_loaded`
    pins that so the dead guard is not re-added.
    """
    import importlib  # stdlib, lazy
    registered = embedderapi.resolve_registered(spec)
    if registered is not None:
        return importlib.import_module(registered)
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


def _reject(key, reason):
    """Record a PROTOCOL rejection and say so ONCE, on stderr.

    The loud half of "absence is silent, breakage is loud". Loudness is bounded
    by the memo, not by a flag: the load that produced this rejection is
    memoized, so the line is printed once per (spec, model) per process — not on
    every recall, which would train the operator to ignore it.
    """
    _EMBEDDER_REJECTIONS[key] = reason
    sys.stderr.write("agentware: %s\n" % reason)


def load_embedder(backend=None, model=None):
    """Lazily load the configured LOCAL embedder, or None if unavailable.

    Contract — declared ONCE in `agentware_lib.core.embedderapi` and validated
    there, not restated here: the backend module exposes
    ``get_embedder(model=None) -> Embedder`` and the Embedder exposes
    ``embed(texts) -> list[list[float]]``. ``dim`` is OPTIONAL (see that
    module's docstring: nothing in the tree reads it).

    The spec may be a REGISTERED name (``fastembed``/``ollama``), a dotted module
    name, or a path to a ``.py`` backend.

    Determinism (INV-1, Mode B): a backend must return the SAME vector for the
    SAME input (pinned model + rounded floats); RRF later fuses on integer ranks.

    NEVER hard-imports the backend: the spec is a runtime config string loaded
    via importlib, so this script's static-import surface stays stdlib-only
    (C-dep guard / INV-6) and Mode A runs with nothing installed. Every failure
    still returns None and NEVER crashes — but the two KINDS of failure are no
    longer the same event:

      * ABSENT (no spec, or an ImportError: module not installed, path not
        there) — the zero-install default. Silent.
      * PRESENT AND WRONG (fails the protocol, or blows up while loading) — a
        misconfiguration whose only symptom would otherwise be that ranking
        quietly got worse. Named on stderr and readable back via
        `embedder_rejection()`.
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
    module = None
    try:
        module = _load_embedder_module(spec)
    except _ABSENT_BACKEND_ERRORS:
        # ABSENCE: nothing is installed at that spec. This is the zero-install
        # default and the single commonest state, so it stays silent.
        module = None
    except Exception as exc:
        # The backend EXISTS and raised while loading — it is installed and
        # broken, which is not the same as absent.
        _reject(key, "embedder backend %r failed to load: %s: %s"
                     % (spec, type(exc).__name__, exc))
    if module is not None:
        try:
            factory = embedderapi.validate_backend_module(module, spec)
            embedder = embedderapi.validate_embedder(factory(model=mdl), spec)
        except embedderapi.EmbedderProtocolError as exc:
            _reject(key, str(exc))
            embedder = None
        except _ABSENT_BACKEND_ERRORS:
            # The backend module is installed but ITS optional dependency is
            # not. Still ABSENCE, so still silent — and this is a contract, not
            # an accident: the fastembed backend raises ImportError eagerly from
            # get_embedder() precisely so semantic_embedder_available() answers
            # honestly on a machine where nothing is pip-installed. Reporting it
            # would print a warning on every `config --show` in the default,
            # zero-install state, which is the fastest way to teach an operator
            # to ignore this channel.
            embedder = None
        except Exception as exc:
            # The factory itself raised something else. Present-and-broken: the
            # operator installed something, so silence would hide their own bug.
            _reject(key, "embedder backend %r raised from %s(): %s: %s"
                         % (spec, embedderapi.BACKEND_FACTORY_ATTR,
                            type(exc).__name__, exc))
            embedder = None
    _EMBEDDER_CACHE[key] = embedder if embedder is not None else _EMBEDDER_SENTINEL
    return embedder


def embedder_rejection(backend=None, model=None):
    """The protocol rejection recorded for this backend, or None.

    None means either "loaded fine" or "not present" — deliberately the same
    answer, because neither is a misconfiguration to report. Call after
    `load_embedder()` (or anything that probes it); the record is populated by
    the load, not by this accessor.
    """
    spec = backend if backend is not None else routing.resolve_embedder_backend()
    if not spec:
        return None
    mdl = model if model is not None else routing.resolve_embed_model()
    return _EMBEDDER_REJECTIONS.get((spec, mdl))


def _reset_embedder_cache():
    """Clear the lazy embedder cache (test/seam hook for config changes).

    Clears the rejection record too: they are two halves of one memo, and a
    rejection outliving the cache would report a stale reason for a backend the
    operator has since fixed.
    """
    _EMBEDDER_CACHE.clear()
    _EMBEDDER_REJECTIONS.clear()


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


def _plan_vector_reuse(corpus, prev_vectors):
    """Decide, per corpus entry, whether its cached vector may be REUSED.

    Returns `(plan, to_embed_idx, to_embed_txt)` where `plan` is
    `[(id, fingerprint, vector_or_None), ...]` in corpus order, `to_embed_idx`
    holds the plan positions still needing a vector, and `to_embed_txt` the
    matching texts — so the caller can embed them in ONE batched call and write
    each result back to the position it came from.

    A cached vector is reused only when all three of the entry id, the content
    FINGERPRINT and the stored shape agree. Fingerprint rather than mtime,
    because the cache must survive a clone; the `isinstance` pair rather than a
    bare truth test, because a cache file is JSON on disk that anything may have
    rewritten, and a malformed row must cause a re-embed rather than a crash.

    An entry with no id is DROPPED: the cache is keyed by id, so a vector for an
    entry that cannot be looked up is one nothing will ever read.
    """
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
            continue
        plan.append((eid, fp, None))
        to_embed_idx.append(len(plan) - 1)
        to_embed_txt.append(doc_text)
    return plan, to_embed_idx, to_embed_txt


def _assemble_vectors(plan):
    """Fold a completed plan into `(vectors_map, dim)` for the cache file.

    The dimension is taken from the FIRST vector and every later one must match
    it. A drift is raised rather than tolerated: a cache mixing two dimensions
    produces cosine scores that are silently meaningless for half the corpus,
    and the error names the entry that drifted so the offending row can be found
    without re-embedding everything.

    A plan row with no vector cannot occur after a successful embed pass; it is
    skipped rather than trusted, so a partially-filled plan can never write a
    `null` into the cache that the reader would later have to defend against.
    """
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
    return out_vectors, dim


def build_vector_cache(kdir, data, embedder, model):
    """Embed + persist corpus vectors for `model` (the `index rebuild` writer).

    Incremental: reuses any existing cached vector whose entry id + content
    fingerprint are unchanged, and embeds ONLY the changed/new entries (one
    batched `embedder.embed(...)` call). Writes the cache deterministically
    (sorted keys) so delete+rebuild is byte-identical given a pinned model.
    Returns a stats dict: {embedded, reused, total, dim, model, path}.

    Three sequential phases with one artifact each — PLAN what to reuse, EMBED
    what is left, ASSEMBLE the file — and only the middle one calls out to the
    optional backend. The embed batch stays inline because it is the only phase
    that can fail on somebody ELSE's contract: an embedder returning the wrong
    NUMBER of vectors is caught here, next to the call that asked for them.
    """
    corpus = _corpus.build_corpus(kdir, data)
    prev = load_vector_cache(kdir, model) or {}
    prev_vectors = prev.get("vectors") or {}

    plan, to_embed_idx, to_embed_txt = _plan_vector_reuse(corpus, prev_vectors)

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

    out_vectors, dim = _assemble_vectors(plan)

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
        # A configured backend that was REJECTED is a different problem from an
        # absent one, and "install a local model" is the wrong advice for it —
        # so the named reason rides along instead of being swallowed.
        rejection = embedder_rejection()
        if rejection:
            notice = "%s Configured backend was rejected: %s" % (notice, rejection)
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
# dashboard) through the facade's owner-resolving __getattr__, which searches the lib for
# the name. rebuild_vector_cache moved here in wave-6 seam 13 (the Mode-B half of
# `index rebuild`); _EMBEDDER_REJECTIONS/_reject/embedder_rejection arrived with the
# task-62 extension point.
_EXPORTS = [
    "VECTOR_CACHE_DIR_REL",
    "VECTOR_CACHE_VERSION",
    "_EMBEDDER_SENTINEL",
    "_EMBEDDER_CACHE",
    "_EMBEDDER_REJECTIONS",
    "_ABSENT_BACKEND_ERRORS",
    "_load_embedder_module",
    "_reject",
    "load_embedder",
    "embedder_rejection",
    "_reset_embedder_cache",
    "_model_slug",
    "vector_cache_path",
    "_corpus_fingerprint",
    "load_vector_cache",
    "_plan_vector_reuse",
    "_assemble_vectors",
    "build_vector_cache",
    "semantic_embedder_available",
    "resolve_effective_retrieval_mode",
    "rebuild_vector_cache",
]
