#!/usr/bin/env python3
"""Reference LOCAL embedder backend for agentware Mode B (semantic retrieval).

This is an OPTIONAL, operator-controlled backend. The `scripts/agentware`
toolkit NEVER imports this file statically — it loads it lazily via importlib
ONLY when the operator sets `AGENTWARE_EMBEDDER_BACKEND` to point here (a dotted
name or a path to this file). That keeps the toolkit's static-import surface
stdlib-only (C-dep guard / INV-6) so Mode A runs with nothing installed.

It talks to a LOCAL Ollama instance (default http://127.0.0.1:11434), which the
operator runs on their own machine — no hosted/cloud API, no data leaves the
host (the moat's "local only" line). `urllib` is imported LAZILY inside the
request method so this module never reaches the network at import time.

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
input. We additionally ROUND every component to a fixed precision
(`embedderapi.EMBED_ROUND_DP`, via the shared `round_vector`) so tiny
serialization jitter can never perturb the downstream cosine ranking; RRF later
fuses on integer ranks, so Mode B is deterministic GIVEN a pinned model + cached
vectors. Reproducibility is pinned to the model version (stated honestly — not
unconditional like Mode A).

Setup (one-time, operator):
    1. Install Ollama (https://ollama.com) and `ollama pull nomic-embed-text`.
    2. `scripts/agentware config` env: AGENTWARE_EMBEDDER_BACKEND=<path to this file>
       (and optionally AGENTWARE_EMBED_MODEL=nomic-embed-text).
"""

# The rounding precision + quantizer come from the extension point, so the two
# shipped backends cannot drift apart on the determinism guard (they held two
# independent `EMBED_ROUND_DP = 6` literals before). This resolves under a path
# spec as well as a dotted one with no sys.path help: a SUBMODULE is found
# through its parent package's `__path__`, and the loader lives inside
# `agentware_lib`, so the package is always imported before this body runs.
from agentware_lib.core.embedderapi import round_vector

# Default local Ollama endpoint. Overridable via AGENTWARE_OLLAMA_HOST.
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "nomic-embed-text"


class OllamaEmbedder(object):
    """Embeds text via a LOCAL Ollama `/api/embeddings` endpoint.

    Deterministic given a pinned model: same input -> same rounded vector.
    """

    def __init__(self, model=None, host=None, timeout=60):
        self.model = (model or DEFAULT_MODEL).strip()
        import os  # stdlib
        self.host = (host or os.environ.get("AGENTWARE_OLLAMA_HOST")
                     or DEFAULT_OLLAMA_HOST).rstrip("/")
        self.timeout = timeout
        self._dim = None

    # --- network seam (lazy urllib; patchable in tests) ----------------------
    def _post_json(self, path, payload):
        """POST `payload` as JSON to `self.host + path`; return the parsed dict.

        urllib is imported HERE (lazily) so this module never touches the
        network at import time and the agentware toolkit — which loads this file
        dynamically — keeps a stdlib-only static-import surface.
        """
        import json  # stdlib
        import urllib.request  # stdlib, lazy (local endpoint only)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.host + path, data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _round(self, vec):
        return round_vector(vec)

    def embed(self, texts):
        """Return one fixed-dim, rounded vector per input text (order-preserving)."""
        if isinstance(texts, str):
            texts = [texts]
        vectors = []
        for text in texts:
            resp = self._post_json(
                "/api/embeddings", {"model": self.model, "prompt": text})
            vec = resp.get("embedding")
            if not isinstance(vec, list) or not vec:
                raise ValueError(
                    "Ollama returned no embedding for model %r" % self.model)
            rounded = self._round(vec)
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
    """Factory required by agentware's load_embedder() contract."""
    return OllamaEmbedder(model=model)
