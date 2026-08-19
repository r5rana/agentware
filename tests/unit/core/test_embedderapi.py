"""The EMBEDDER EXTENSION POINT — `agentware_lib.core.embedderapi` (task 62).

Three claims, kept separate because they fail for different reasons:

  1. **The protocol is DECLARED and VALIDATED in one place.** Before this
     module the contract was prose in three files that disagreed about whether
     `dim` was required, and the only enforcement was an inline
     `callable(getattr(candidate, "embed", None))` inside a bare
     `except Exception`.
  2. **The rounding constant is defined ONCE.** Two independent
     `EMBED_ROUND_DP = 6` literals sat in the two shipped backends. Two
     literals that agree today are not one constant, and the whole point of the
     determinism guard is that both backends quantize identically — so this is
     asserted over the TREE with `ast`, not over an import.
  3. **A missing required attribute is rejected by NAME.** The named error is
     the difference between "semantic retrieval is off because you installed
     nothing" and "semantic retrieval is off because what you installed is
     wrong" — which used to be the same `None`.

Method prefix `test_embedderapi_` is unique against every module filename and
every other suite's methods (see tests/README.md on `-k` collisions).

Stdlib only, hermetic: reads the tree with `ast` and drives pure functions. No
knowledge base, no subprocess, no network.
"""

from tests._paths import REPO_ROOT, TESTS_DIR

import ast
import os
import sys
import unittest

try:
    from tests._fixtures import load_cli
except ImportError:  # direct invocation
    sys.path.insert(0, TESTS_DIR)
    from _fixtures import load_cli


SCRIPTS = os.path.join(REPO_ROOT, "scripts")
API_REL = "agentware_lib/core/embedderapi.py"


def _api():
    """The module under test, imported the way the product imports it."""
    load_cli()   # puts scripts/ on sys.path
    from agentware_lib.core import embedderapi
    return embedderapi


def _module_level_constant_sites(name, root=SCRIPTS):
    """Every module-scope assignment of `name` under `root`, as rel paths.

    AST rather than grep: a mention in a docstring, a comment or an f-string is
    not a definition, and counting those would make the "defined once" claim
    fail for the wrong reason (or, worse, pass because the grep was narrowed).
    """
    sites = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fname in sorted(filenames):
            if not fname.endswith(".py"):
                continue
            full = os.path.join(dirpath, fname)
            try:
                with open(full, "r", encoding="utf-8") as handle:
                    tree = ast.parse(handle.read())
            except (OSError, SyntaxError):       # pragma: no cover - defensive
                continue
            for node in tree.body:               # module scope ONLY
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    targets = [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        sites.append(
                            os.path.relpath(full, root).replace(os.sep, "/"))
    return sorted(sites)


def _calls_named(path, func_name):
    """True when `path` contains a call to `func_name(...)`."""
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == func_name:
            return True
    return False


class _StubEmbedder(object):
    def embed(self, texts):
        return [[0.0] for _ in texts]


class _NoEmbed(object):
    """Shaped like an embedder to a careless reader; missing the one required
    member. This is the object the plan's criterion is about."""
    dim = 8


class _EmbedNotCallable(object):
    embed = "not a function"


class _FakeModule(object):
    """A stand-in for a loaded backend module (getattr is all the API uses)."""

    def __init__(self, **attrs):
        for key, value in attrs.items():
            setattr(self, key, value)


class EmbedderApiModuleTest(unittest.TestCase):
    """Claim 1 — the extension point exists and declares the protocol."""

    def test_embedderapi_the_module_exists_at_its_layered_path(self):
        self.assertTrue(os.path.isfile(os.path.join(SCRIPTS, API_REL)),
                        "the embedder API module must exist at " + API_REL)

    def test_embedderapi_declares_the_protocol_as_data_not_prose(self):
        api = _api()
        self.assertEqual(api.BACKEND_FACTORY_ATTR, "get_embedder")
        self.assertEqual(api.REQUIRED_EMBEDDER_ATTRS, ("embed",))
        self.assertEqual(api.OPTIONAL_EMBEDDER_ATTRS, ("dim",))
        self.assertTrue(issubclass(api.EmbedderProtocolError, Exception))

    def test_embedderapi_is_a_pure_leaf_importing_nothing_from_the_package(self):
        """core imports nothing above it, and this module imports nothing at
        all — a contract leaf, so a backend can import it without dragging the
        rest of the lib into an optional sidecar's namespace."""
        with open(os.path.join(SCRIPTS, API_REL), "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertEqual(imported, [], "must import nothing: %s" % imported)


class EmbedderRoundingConstantTest(unittest.TestCase):
    """Claim 2 — the rounding constant is defined ONCE, in the tree."""

    def test_embedderapi_the_rounding_constant_is_defined_exactly_once(self):
        sites = _module_level_constant_sites("EMBED_ROUND_DP")
        self.assertEqual(sites, [API_REL],
                         "EMBED_ROUND_DP must be defined once, in the "
                         "extension point; found: %s" % sites)

    def test_embedderapi_the_shipped_backends_call_the_shared_quantizer(self):
        """Both backends must ROUND THROUGH the shared function.

        Asserted because "defined once" is satisfiable while a backend rounds
        with its own inline literal, which is the drift the constant exists to
        prevent.
        """
        for name in ("agentware_embedder_fastembed.py",
                     "agentware_embedder_ollama.py"):
            path = os.path.join(SCRIPTS, name)
            self.assertTrue(_calls_named(path, "round_vector"),
                            "%s must round via round_vector()" % name)

    def test_embedderapi_round_vector_quantizes_and_returns_plain_floats(self):
        api = _api()
        out = api.round_vector([0.1234567, 1, -0.98765432])
        self.assertEqual(out, [round(0.1234567, api.EMBED_ROUND_DP), 1.0,
                               round(-0.98765432, api.EMBED_ROUND_DP)])
        for value in out:
            # The vector cache is JSON: a numpy scalar would not serialize.
            self.assertIs(type(value), float)

    def test_embedderapi_round_vector_is_idempotent(self):
        """Rounding an already-rounded vector must not move it — the property
        the cache's byte-identity across a reuse/re-embed mix depends on."""
        api = _api()
        once = api.round_vector([0.123456789, 3.14159265358979])
        self.assertEqual(api.round_vector(once), once)


class EmbedderProtocolValidationTest(unittest.TestCase):
    """Claim 3 — a missing required attribute is rejected by NAME."""

    def setUp(self):
        self.api = _api()

    def test_embedderapi_rejects_an_embedder_missing_a_required_attribute(self):
        with self.assertRaises(self.api.EmbedderProtocolError) as ctx:
            self.api.validate_embedder(_NoEmbed(), "my.backend")
        self.assertEqual(ctx.exception.missing, "embed")
        self.assertEqual(ctx.exception.spec, "my.backend")
        message = str(ctx.exception)
        self.assertIn("embed", message)
        self.assertIn("my.backend", message)
        self.assertIn("does not satisfy the embedder protocol", message)

    def test_embedderapi_rejects_a_required_attribute_that_is_not_callable(self):
        """Presence is not the contract — `embed` has to be callable. A string
        attribute named `embed` passes every hasattr-shaped check."""
        with self.assertRaises(self.api.EmbedderProtocolError) as ctx:
            self.api.validate_embedder(_EmbedNotCallable(), "spec")
        self.assertEqual(ctx.exception.missing, "embed")
        self.assertIn("not callable", str(ctx.exception))

    def test_embedderapi_rejects_a_factory_that_returns_none(self):
        with self.assertRaises(self.api.EmbedderProtocolError) as ctx:
            self.api.validate_embedder(None, "spec")
        self.assertEqual(ctx.exception.missing, "get_embedder")
        self.assertIn("returned None", str(ctx.exception))

    def test_embedderapi_accepts_a_conforming_embedder_unchanged(self):
        stub = _StubEmbedder()
        self.assertIs(self.api.validate_embedder(stub, "spec"), stub)

    def test_embedderapi_accepts_an_embedder_without_the_optional_dim(self):
        """`dim` is OPTIONAL, and that is a DECISION, not an oversight: nothing
        in the tree reads it, so requiring it would reject a working backend.
        Pinned here so tightening it later is a deliberate, visible change."""
        stub = _StubEmbedder()
        self.assertFalse(hasattr(stub, "dim"))
        self.assertIs(self.api.validate_embedder(stub, "spec"), stub)

    def test_embedderapi_rejects_a_module_without_the_factory(self):
        with self.assertRaises(self.api.EmbedderProtocolError) as ctx:
            self.api.validate_backend_module(_FakeModule(x=1), "spec")
        self.assertEqual(ctx.exception.missing, "get_embedder")

    def test_embedderapi_rejects_a_module_whose_factory_is_not_callable(self):
        with self.assertRaises(self.api.EmbedderProtocolError) as ctx:
            self.api.validate_backend_module(
                _FakeModule(get_embedder="nope"), "spec")
        self.assertEqual(ctx.exception.missing, "get_embedder")
        self.assertIn("not callable", str(ctx.exception))

    def test_embedderapi_returns_the_factory_from_a_conforming_module(self):
        def factory(model=None):
            return _StubEmbedder()
        module = _FakeModule(get_embedder=factory)
        self.assertIs(self.api.validate_backend_module(module, "spec"), factory)

    def test_embedderapi_the_error_is_a_typeerror_for_pre_registry_callers(self):
        """The pre-registry loader raised TypeError for this exact condition;
        subclassing keeps any caller that catches the old type working."""
        self.assertTrue(
            issubclass(self.api.EmbedderProtocolError, TypeError))


class EmbedderRegistryTest(unittest.TestCase):
    """The registry half: shipped backends are REGISTERED, not just importable."""

    def setUp(self):
        self.api = _api()

    def test_embedderapi_registers_both_shipped_backends(self):
        self.assertEqual(self.api.registered_names(), ("fastembed", "ollama"))

    def test_embedderapi_every_registered_module_exists_on_disk(self):
        """A registry pointing at a module that is not there is the classic
        rot: `--set-embedder <name>` would resolve to an ImportError, which the
        loader reads as ABSENCE and reports silently."""
        for name, module, _summary in self.api.REGISTERED_BACKENDS:
            path = os.path.join(SCRIPTS, module + ".py")
            self.assertTrue(os.path.isfile(path),
                            "registered backend %r names a missing module %s"
                            % (name, module))

    def test_embedderapi_every_registered_backend_carries_a_summary(self):
        for name, _module, summary in self.api.REGISTERED_BACKENDS:
            self.assertTrue(summary and summary.strip(), name)
            self.assertEqual(self.api.registered_summary(name), summary)

    def test_embedderapi_resolves_a_registered_name_to_its_module(self):
        self.assertEqual(self.api.resolve_registered("ollama"),
                         "agentware_embedder_ollama")
        self.assertEqual(self.api.resolve_registered("fastembed"),
                         "agentware_embedder_fastembed")

    def test_embedderapi_an_unregistered_spec_resolves_to_none(self):
        """None means "not one of ours", NOT an error — the loader then treats
        the spec as the dotted name or path it always did, so the registry took
        no spelling away."""
        for spec in ("agentware_embedder_ollama", "/tmp/custom_backend.py",
                     "some.third.party", "", None):
            self.assertIsNone(self.api.resolve_registered(spec), repr(spec))

    def test_embedderapi_registered_summary_of_an_unknown_name_is_none(self):
        self.assertIsNone(self.api.registered_summary("no-such-backend"))


if __name__ == "__main__":
    unittest.main()
