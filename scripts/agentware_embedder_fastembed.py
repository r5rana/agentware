#!/usr/bin/env python3
"""Real pinned LOCAL embedder backend for agentware Mode B (semantic retrieval).

Feature 260627-semantic-embedding-settings. This delivers the ONE missing piece
of the prior 260625-semantic-retrieval-benchmark roadmap: a real, on-device
embedding model behind the existing `load_embedder` contract. It uses
`fastembed` (ONNX runtime, NO PyTorch, Python>=3.10) so embeddings are computed
fully on the operator's own machine after a one-time model download — no hosted
API, no cloud, no data leaves the host (the moat's "local only" line).

OPTIONAL + lazily loaded, exactly like the reference `agentware_embedder_ollama`:
the `scripts/agentware` toolkit NEVER imports this file (or `fastembed`)
statically. It is loaded via importlib ONLY when the operator points
`AGENTWARE_EMBEDDER_BACKEND` here, so the toolkit's static-import surface stays
stdlib-only (C-dep guard / INV-6) and Mode A (pure BM25, zero-install) runs with
nothing installed. `fastembed` is imported LAZILY inside this module's methods,
never at module top level, so even loading this file by importlib does not require
`fastembed` to be present until an embed actually happens.

Pinned dependency (R-DEP-02): `fastembed==0.8.0`. Install (operator-approved):

    python3 -m pip install fastembed==0.8.0

Default model `BAAI/bge-small-en-v1.5` (384-dim, fast); opt up to
`BAAI/bge-base-en-v1.5` (768-dim) via `AGENTWARE_EMBED_MODEL` / `config
--set-embed-model`. The model id is a pinned HuggingFace id; fastembed downloads
only model weights + tokenizer (ONNX) — NO code execution from model files
(R-SEC-02).

Contract — declared and validated in `agentware_lib.core.embedderapi`, which is
the extension point this file plugs into:

    get_embedder(model=None) -> Embedder                    # REQUIRED
    Embedder.embed(texts: list[str]) -> list[list[float]]   # REQUIRED, fixed-dim
    Embedder.dim: int | None                                # OPTIONAL

`dim` is optional and this file's earlier "Embedder.dim: int" overstated it:
nothing in agentware reads it, and it is None here until the first embed
anyway. A backend missing a REQUIRED member is rejected by name rather than
degrading ranking silently.

Determinism (INV-1, Mode B): a pinned model returns the SAME vector for the SAME
input ON A GIVEN MACHINE. Every component is ROUNDED to a fixed precision
(`embedderapi.EMBED_ROUND_DP`, via the shared `round_vector` both backends call)
so serialization jitter can never perturb the downstream cosine ranking; RRF
later fuses on INTEGER ranks, so Mode B is deterministic GIVEN a pinned model +
cached vectors. Reproducibility is
pinned to the model version + machine, stated honestly: ONNX float math can
differ ACROSS machines/CPUs, so the vector cache + benchmark numbers are
reproduced PER MACHINE — never claimed to be unconditionally cross-machine
byte-identical (unlike Mode A's pure-stdlib BM25, which IS unconditional).
"""

# The rounding precision + quantizer come from the extension point, so both
# Mode-B backends quantize identically BY CONSTRUCTION rather than by two
# literals that agree today. This resolves under a path spec as well as a dotted
# one with no sys.path help: a SUBMODULE is found through its parent package's
# `__path__`, and the loader lives inside `agentware_lib`, so the package is
# always imported before this body runs.
from agentware_lib.core.embedderapi import round_vector

# Pinned default model (small/fast, 384-dim). Opt up via AGENTWARE_EMBED_MODEL.
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
# Pinned dependency version (R-DEP-02) — surfaced in the missing-dep message.
REQUIRED_FASTEMBED = "fastembed==0.8.0"


class FastEmbedEmbedder(object):
    """Embeds text via a LOCAL fastembed (ONNX) model.

    Deterministic given a pinned model ON A GIVEN MACHINE: same input -> same
    rounded vector. The heavy `TextEmbedding` (which triggers the one-time model
    download) is constructed LAZILY on first embed, so merely loading/probing the
    backend (`load_embedder` / `semantic_embedder_available`) is cheap and does
    NOT require the model to be present yet.
    """

    def __init__(self, model=None):
        self.model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self._dim = None
        self._impl = None  # lazily-built fastembed.TextEmbedding

    # --- lazy model seam (fastembed imported HERE, never at module top) -------
    def _ensure_impl(self):
        """Build (and cache) the fastembed TextEmbedding on first use.

        `fastembed` is imported LAZILY here so this module never pulls a non-stdlib
        dependency at import time — the toolkit's static-import surface stays
        stdlib-only and Mode A keeps running with nothing installed. A missing
        dependency or a failed model download raises a CLEAR error naming the pin;
        `load_embedder` converts ANY load-time failure to None (graceful BM25
        fallback), and the recall/eval paths catch embed-time failures the same way.
        """
        if self._impl is not None:
            return self._impl
        try:
            from fastembed import TextEmbedding  # lazy, non-stdlib (optional)
        except ImportError as exc:
            raise ImportError(
                "fastembed is not installed; Mode B (semantic) needs it. "
                "Install the pinned version: python3 -m pip install %s "
                "(Mode A / BM25 keeps working with nothing installed)."
                % REQUIRED_FASTEMBED) from exc
        try:
            self._impl = TextEmbedding(model_name=self.model)
        except Exception as exc:  # download/verify/instantiation failure
            raise RuntimeError(
                "fastembed could not load model %r (one-time download/verify "
                "failed; check connectivity for the first run, then it is fully "
                "offline). Underlying error: %s" % (self.model, exc)) from exc
        return self._impl

    def _round(self, vec):
        return round_vector(vec)

    def embed(self, texts):
        """Return one fixed-dim, rounded vector per input text (order-preserving)."""
        if isinstance(texts, str):
            texts = [texts]
        texts = list(texts)
        if not texts:
            return []
        impl = self._ensure_impl()
        # fastembed yields one numpy array per input, in input order.
        raw = list(impl.embed(texts))
        if len(raw) != len(texts):
            raise ValueError(
                "fastembed returned %d vectors for %d inputs (model %r)"
                % (len(raw), len(texts), self.model))
        vectors = []
        for vec in raw:
            # numpy array (or any iterable) -> rounded python floats.
            rounded = self._round(list(vec))
            if not rounded:
                raise ValueError(
                    "fastembed returned an empty vector (model %r)" % self.model)
            if self._dim is None:
                self._dim = len(rounded)
            elif len(rounded) != self._dim:
                raise ValueError(
                    "embedding dim drift: expected %d, got %d (model %r)"
                    % (self._dim, len(rounded), self.model))
            vectors.append(rounded)
        return vectors

    @property
    def dim(self):
        return self._dim


def get_embedder(model=None):
    """Factory required by agentware's load_embedder() contract.

    Verifies `fastembed` is importable EAGERLY (so `semantic_embedder_available()`
    is honest: True iff the dependency is installed) but defers the heavier model
    download to the first `embed()` call. A missing dependency raises here and is
    caught by `load_embedder` -> None -> graceful Mode-A (BM25) fallback.
    """
    try:
        import fastembed  # noqa: F401  (lazy, presence check only)
    except ImportError as exc:
        raise ImportError(
            "fastembed is not installed; install the pinned version: "
            "python3 -m pip install %s" % REQUIRED_FASTEMBED) from exc
    return FastEmbedEmbedder(model=model)
