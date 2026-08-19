"""agentware_lib.core.embedderapi — the EMBEDDER EXTENSION POINT (layer: core).

Owns: the embedder protocol contract (BACKEND_FACTORY_ATTR / REQUIRED_EMBEDDER_ATTRS / OPTIONAL_EMBEDDER_ATTRS), its named rejection error (EmbedderProtocolError), the two validators (validate_backend_module / validate_embedder), the registry of shipped backends (REGISTERED_BACKENDS / registered_names / registered_summary / resolve_registered) and the ONE definition of the embedding rounding constant (EMBED_ROUND_DP / round_vector).
Does NOT own: loading a backend, memoizing it, or falling back to Mode A — that is agentware_lib.retrieval.vectorcache (load_embedder); the backend/model config resolvers (agentware_lib.core.routing); the ranking that consumes the vectors (agentware_lib.retrieval.hybrid); the backends themselves (scripts/agentware_embedder_*.py, optional sidecars that import THIS module for the constant).
Layer deps: core -> stdlib only (imports nothing at all — a pure contract leaf, like agentware_lib._registration).
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Why this module exists
----------------------
Before it, the embedder was an **ad-hoc import string**. Three things were wrong
with that, and all three are failure modes rather than style complaints:

1. **The contract was stated three times and disagreed with itself.**
   `load_embedder`'s docstring said ``dim: int  # optional``; both shipped
   backends' docstrings said ``Embedder.dim: int`` with no such qualifier. A
   third-party author reading either one could not tell which was binding, and
   nothing anywhere executed the difference. The protocol is now declared ONCE,
   here, in data a validator actually reads.

2. **A contract violation was indistinguishable from an absent backend.** Both
   became ``None`` inside a bare ``except Exception``, and both then fell back to
   Mode A. Absence is the zero-install default and must stay silent; a backend
   the operator DID configure and DID install, which then fails to satisfy the
   protocol, is a misconfiguration whose only symptom was *ranking quietly got
   worse*. Those are now different outcomes: a violation is raised as a NAMED
   error that says which attribute is missing, and the loader surfaces it
   (`agentware_lib.retrieval.vectorcache.embedder_rejection`) instead of
   swallowing it.

3. **The backends were reachable only by typing a module path.** They are now
   registered under short names (``fastembed``, ``ollama``), so
   ``config --set-embedder fastembed`` works and the shipped set is
   enumerable — which is also the seam where an allowlist belongs, should the
   loader ever stop accepting arbitrary specs.

What attribute validation can and cannot prove
----------------------------------------------
It proves the object is SHAPED like an embedder. It does not prove the vectors
are any good, and it deliberately does NOT probe by embedding: ``load_embedder``
runs on the ``config --show`` path, and a probe there would trigger a real
model load (a first-run download, for fastembed) merely to answer "is one
configured?". Shape is checked here; the vectors are checked where they are
consumed (``build_vector_cache`` raises on dim drift, ``retrieve_bm25_embed``
falls back on any embed failure). Saying so is the point — a check that is
mistaken for a stronger one is worse than no check
(`learn-a-check-that-cannot-fail-is-not-passing`).

`dim` is OPTIONAL, and that is a decision, not an omission: nothing in the tree
reads ``embedder.dim`` (the cache derives its dim from the vectors themselves),
so requiring it would reject a WORKING backend for a field no code consults.
The shipped backends both expose it — as a property that is ``None`` until the
first embed — and their docstrings are corrected to say optional.
"""


#: Fixed decimal places every embedding component is rounded to. THE single
#: definition (it was duplicated in both shipped backends): a determinism guard
#: (INV-1, Mode B) so serialization jitter can never perturb the downstream
#: cosine ranking. Both backends round to this, so their vectors quantize
#: identically and a cache written by one is comparable with the other's.
EMBED_ROUND_DP = 6

#: The attribute a backend MODULE must expose: ``get_embedder(model=None)``.
BACKEND_FACTORY_ATTR = "get_embedder"

#: Attributes the object returned by the factory MUST expose, and which must be
#: callable. Missing one is a protocol violation, reported by NAME.
REQUIRED_EMBEDDER_ATTRS = ("embed",)

#: Declared but NOT required — see the module docstring. Present on both shipped
#: backends; absent on a perfectly usable minimal one.
OPTIONAL_EMBEDDER_ATTRS = ("dim",)

#: The shipped extension points, as ``(name, module, summary)`` — the same
#: shape, and the same "frozen tuple of registrations" discipline, as
#: ``agentware_lib._registration._PARSER_REGISTRATION``. A registered NAME is
#: what ``config --set-embedder`` should be given; the dotted module still
#: works, and so does a path, so nothing that already resolved stops resolving.
REGISTERED_BACKENDS = (
    ("fastembed", "agentware_embedder_fastembed",
     "on-device ONNX embeddings via fastembed==0.8.0 (default model "
     "BAAI/bge-small-en-v1.5); no PyTorch, no network after the one-time "
     "model download"),
    ("ollama", "agentware_embedder_ollama",
     "embeddings from a LOCAL Ollama instance (default model "
     "nomic-embed-text) over its loopback HTTP API"),
)


class EmbedderProtocolError(TypeError):
    """A configured backend is PRESENT but does not satisfy the protocol.

    Named deliberately, and raised rather than returned: the caller has to
    choose between "nothing is installed" (silent, expected) and "what is
    installed is wrong" (loud), and it cannot make that choice from a ``None``.
    Subclasses ``TypeError`` because that is what the pre-registry loader raised
    for the same condition, so any caller catching the old type still catches
    this one.

    Attributes: ``spec`` (what the operator configured), ``missing`` (the
    attribute name, which is the actionable half) and ``detail``.
    """

    def __init__(self, spec, missing, detail):
        self.spec = spec
        self.missing = missing
        self.detail = detail
        super(EmbedderProtocolError, self).__init__(
            "embedder backend %r does not satisfy the embedder protocol: "
            "missing %s — %s" % (spec, missing, detail))


def registered_names():
    """The registered backend names, in registration order."""
    return tuple(name for (name, _module, _summary) in REGISTERED_BACKENDS)


def registered_summary(name):
    """The one-line summary for a registered backend name, or None."""
    for reg_name, _module, summary in REGISTERED_BACKENDS:
        if reg_name == name:
            return summary
    return None


def resolve_registered(spec):
    """Map a registered NAME to its dotted module, or None when unregistered.

    None means "not one of ours" and is not an error: the loader then treats the
    spec as the dotted name or path it always did, so promoting the registry
    took nothing away.
    """
    if not spec:
        return None
    for name, module, _summary in REGISTERED_BACKENDS:
        if spec == name:
            return module
    return None


def validate_backend_module(module, spec):
    """Return the backend module's factory, or raise EmbedderProtocolError.

    The module-side half of the protocol: it must expose a CALLABLE
    ``get_embedder``. A module that merely defines the name as data fails here
    rather than at call time, where the traceback would name the wrong thing.
    """
    factory = getattr(module, BACKEND_FACTORY_ATTR, None)
    if factory is None:
        raise EmbedderProtocolError(
            spec, BACKEND_FACTORY_ATTR,
            "a backend module must expose %s(model=None) -> Embedder"
            % BACKEND_FACTORY_ATTR)
    if not callable(factory):
        raise EmbedderProtocolError(
            spec, BACKEND_FACTORY_ATTR,
            "%s is present but not callable (found %s)"
            % (BACKEND_FACTORY_ATTR, type(factory).__name__))
    return factory


def validate_embedder(candidate, spec):
    """Return `candidate`, or raise EmbedderProtocolError naming what is absent.

    The instance-side half: every name in REQUIRED_EMBEDDER_ATTRS must be
    present AND callable. Nothing here calls ``embed`` — see the module
    docstring on why shape, not behaviour, is what this layer checks.
    """
    if candidate is None:
        raise EmbedderProtocolError(
            spec, BACKEND_FACTORY_ATTR,
            "%s(model=...) returned None instead of an embedder"
            % BACKEND_FACTORY_ATTR)
    for attr in REQUIRED_EMBEDDER_ATTRS:
        member = getattr(candidate, attr, None)
        if member is None:
            raise EmbedderProtocolError(
                spec, attr,
                "the object returned by %s() has no .%s(texts) -> "
                "list[list[float]]" % (BACKEND_FACTORY_ATTR, attr))
        if not callable(member):
            raise EmbedderProtocolError(
                spec, attr,
                ".%s is present but not callable (found %s)"
                % (attr, type(member).__name__))
    return candidate


def round_vector(vec):
    """Quantize one embedding to EMBED_ROUND_DP places (the determinism guard).

    Lives here so both shipped backends — and any third-party one — round
    identically without restating the constant. Accepts any iterable of numbers
    (numpy arrays included) and returns plain python floats, which is what the
    JSON vector cache must hold.
    """
    return [round(float(x), EMBED_ROUND_DP) for x in vec]


_EXPORTS = [
    "EMBED_ROUND_DP",
    "BACKEND_FACTORY_ATTR",
    "REQUIRED_EMBEDDER_ATTRS",
    "OPTIONAL_EMBEDDER_ATTRS",
    "REGISTERED_BACKENDS",
    "EmbedderProtocolError",
    "registered_names",
    "registered_summary",
    "resolve_registered",
    "validate_backend_module",
    "validate_embedder",
    "round_vector",
]

__all__ = list(_EXPORTS)
