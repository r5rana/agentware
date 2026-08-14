"""Tests for the pluggable, lazily-imported LOCAL embedder backend (feature
260625-semantic-retrieval-benchmark, Phase 3.2).

Invariants under test:
  * The agentware toolkit NEVER statically imports an optional backend (or any
    network module) — the static-import surface stays stdlib-only (C-dep guard /
    INV-6), so Mode A runs with nothing installed.
  * A configured + loadable backend makes semantic_embedder_available() True and
    embed() returns fixed-dim, DETERMINISTIC vectors (same input -> same vector).
  * An unset/missing/broken backend returns None and NEVER crashes — Mode A
    (deterministic) stays intact and a 'semantic' config gracefully falls back.

Stdlib-only (no pytest). The backend is resolved from env/config, so each test
patches the env var + config paths onto temp state and resets the lazy cache.
"""

import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, CLI_PATH
    from tests import _security_policy as policy
except ImportError:  # direct invocation: `python3 tests/test_embedder.py`
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _fixtures import load_cli, CLI_PATH
    import _security_policy as policy


# A deterministic, dependency-free stub backend written to a temp file and
# pointed at via AGENTWARE_EMBEDDER_BACKEND. Hash-based so it needs no model and
# no network, yet honors the contract (fixed dim, same input -> same vector).
_STUB_BACKEND = '''\
import hashlib

DIM = 8


class StubEmbedder(object):
    def __init__(self, model=None):
        self.model = model or "stub"
        self.dim = DIM

    def embed(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        out = []
        for t in texts:
            h = hashlib.sha256((self.model + "::" + t).encode("utf-8")).digest()
            out.append([round(h[i] / 255.0, 6) for i in range(DIM)])
        return out


def get_embedder(model=None):
    return StubEmbedder(model=model)
'''

# A backend that raises at import time (exercises the graceful-None path).
_BOOM_BACKEND = 'raise RuntimeError("backend import blew up")\n'

# A backend with no get_embedder factory (contract violation -> None).
_NO_FACTORY_BACKEND = 'x = 1\n'

# A backend whose factory returns an object without .embed (contract -> None).
_BAD_OBJECT_BACKEND = '''\
class NotAnEmbedder(object):
    pass


def get_embedder(model=None):
    return NotAnEmbedder()
'''


class EmbedderBackendTest(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        self.tmp = tempfile.mkdtemp(prefix="agentware-embedder-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

        # Isolate config.env reads onto an empty temp file.
        self.cfg = os.path.join(self.tmp, ".agentware", "config.env")
        self._orig_home_config = self.cli.HOME_CONFIG
        self._orig_config_paths = self.cli.CONFIG_PATHS
        self.cli.HOME_CONFIG = self.cfg
        self.cli.CONFIG_PATHS = (self.cfg,)

        # Manage the backend/model/retrieval env vars + reset the lazy cache.
        self._prev_env = {k: os.environ.pop(k, None) for k in (
            self.cli.EMBEDDER_BACKEND_KEY, self.cli.EMBED_MODEL_KEY,
            self.cli.RETRIEVAL_MODE_KEY)}
        self.cli._reset_embedder_cache()

        def _restore():
            self.cli.HOME_CONFIG = self._orig_home_config
            self.cli.CONFIG_PATHS = self._orig_config_paths
            for k, v in self._prev_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            self.cli._reset_embedder_cache()
        self.addCleanup(_restore)

    def _write_backend(self, body, name="backend.py"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def _use(self, path):
        os.environ[self.cli.EMBEDDER_BACKEND_KEY] = path
        self.cli._reset_embedder_cache()

    # --- static-import surface stays stdlib-only -----------------------------
    @unittest.skipIf(sys.version_info < (3, 10),
                     "sys.stdlib_module_names requires Python 3.10+")
    def test_toolkit_does_not_statically_import_backend_or_network(self):
        # The WHOLE always-on toolkit, not just the entrypoint — since task 25
        # that path is a facade and scanning it alone made this guard vacuous.
        # Corpus and first-party allowance are defined once, in
        # tests/_security_policy.py; a real third-party import still reddens.
        mods = policy.guarded_import_surface()
        stdlib = set(sys.stdlib_module_names) | set(sys.builtin_module_names)
        stdlib |= policy.first_party_names()
        offenders = sorted(m for m in mods if m not in stdlib)
        self.assertEqual(offenders, [], "non-stdlib static imports: %s" % offenders)
        # The optional backend module is loaded dynamically, never statically.
        self.assertNotIn("agentware_embedder_ollama", mods)
        for net in ("urllib", "socket", "http", "requests", "httpx"):
            self.assertNotIn(net, mods, "%s must not be a static import" % net)

    # --- happy path: configured + loadable backend ---------------------------
    def test_loadable_backend_embeds_fixed_dim_deterministic(self):
        self._use(self._write_backend(_STUB_BACKEND))
        emb = self.cli.load_embedder()
        self.assertIsNotNone(emb)
        v1 = emb.embed(["alpha", "beta"])
        self.assertEqual(len(v1), 2)
        self.assertEqual(len(v1[0]), 8)
        self.assertEqual(len(v1[1]), 8)
        # Same input -> identical vectors (determinism).
        v2 = self.cli.load_embedder().embed(["alpha", "beta"])
        self.assertEqual(v1, v2)
        # Distinct inputs -> distinct vectors (it is actually embedding).
        self.assertNotEqual(v1[0], v1[1])

    def test_available_true_and_effective_semantic_when_backend_present(self):
        self._use(self._write_backend(_STUB_BACKEND))
        os.environ[self.cli.RETRIEVAL_MODE_KEY] = "semantic"
        self.assertTrue(self.cli.semantic_embedder_available())
        eff, fell_back, notice = self.cli.resolve_effective_retrieval_mode()
        self.assertEqual(eff, "semantic")
        self.assertFalse(fell_back)
        self.assertEqual(notice, "")

    def test_dotted_module_name_spec_loads(self):
        # The reference Ollama backend is importable by dotted name from scripts/.
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.abspath(CLI_PATH))))
        self.addCleanup(lambda: sys.path.remove(os.path.join(
            os.path.dirname(os.path.abspath(CLI_PATH)))))
        self._use("agentware_embedder_ollama")
        emb = self.cli.load_embedder()
        self.assertIsNotNone(emb)
        self.assertTrue(callable(getattr(emb, "embed", None)))

    # --- graceful fallback: unset / missing / broken backend -----------------
    def test_unset_backend_is_none_and_mode_a_intact(self):
        self.assertIsNone(self.cli.load_embedder())
        self.assertFalse(self.cli.semantic_embedder_available())
        os.environ[self.cli.RETRIEVAL_MODE_KEY] = "semantic"
        eff, fell_back, notice = self.cli.resolve_effective_retrieval_mode()
        self.assertEqual(eff, "deterministic")
        self.assertTrue(fell_back)
        self.assertIn("local", notice.lower())

    def test_missing_path_spec_is_none_no_crash(self):
        self._use(os.path.join(self.tmp, "does-not-exist.py"))
        self.assertIsNone(self.cli.load_embedder())
        self.assertFalse(self.cli.semantic_embedder_available())

    def test_missing_dotted_module_is_none_no_crash(self):
        self._use("agentware_no_such_backend_xyz")
        self.assertIsNone(self.cli.load_embedder())

    def test_backend_that_raises_on_import_is_none(self):
        self._use(self._write_backend(_BOOM_BACKEND, "boom.py"))
        self.assertIsNone(self.cli.load_embedder())

    def test_backend_without_factory_is_none(self):
        self._use(self._write_backend(_NO_FACTORY_BACKEND, "nofac.py"))
        self.assertIsNone(self.cli.load_embedder())

    def test_backend_returning_bad_object_is_none(self):
        self._use(self._write_backend(_BAD_OBJECT_BACKEND, "badobj.py"))
        self.assertIsNone(self.cli.load_embedder())

    # --- config.env precedence (not just env) --------------------------------
    def test_backend_resolves_from_config_file(self):
        path = self._write_backend(_STUB_BACKEND)
        os.makedirs(os.path.dirname(self.cfg), exist_ok=True)
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write("%s=%s\n" % (self.cli.EMBEDDER_BACKEND_KEY, path))
        self.cli._reset_embedder_cache()
        self.assertEqual(self.cli.resolve_embedder_backend(), path)
        self.assertIsNotNone(self.cli.load_embedder())

    def test_embed_model_default_and_override(self):
        self.assertEqual(self.cli.resolve_embed_model(), self.cli.EMBED_MODEL_DEFAULT)
        os.environ[self.cli.EMBED_MODEL_KEY] = "mxbai-embed-large"
        self.assertEqual(self.cli.resolve_embed_model(), "mxbai-embed-large")


class OllamaReferenceBackendTest(unittest.TestCase):
    """The reference Ollama backend embeds deterministically given a pinned
    model, with urllib reached ONLY lazily (patched here, never live)."""

    def setUp(self):
        import importlib.util
        from importlib.machinery import SourceFileLoader
        path = os.path.join(os.path.dirname(os.path.abspath(CLI_PATH)),
                            "agentware_embedder_ollama.py")
        loader = SourceFileLoader("agentware_embedder_ollama_test", path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.mod = importlib.util.module_from_spec(spec)
        loader.exec_module(self.mod)

    def test_get_embedder_rounds_and_is_deterministic(self):
        emb = self.mod.get_embedder(model="nomic-embed-text")

        # Patch the network seam: a fixed embedding per text (no live Ollama).
        canned = {"alpha": [0.1234567, 1.0, -0.5],
                  "beta": [0.7654321, 0.0, 0.25]}

        def _fake_post(path, payload):
            return {"embedding": canned[payload["prompt"]]}

        emb._post_json = _fake_post
        v = emb.embed(["alpha", "beta"])
        self.assertEqual(len(v), 2)
        self.assertEqual(emb.dim, 3)
        # Rounded to EMBED_ROUND_DP places (determinism guard).
        self.assertEqual(v[0][0], round(0.1234567, self.mod.EMBED_ROUND_DP))
        # Same input -> same vector.
        self.assertEqual(emb.embed(["alpha"])[0], v[0])

    def test_dim_drift_raises(self):
        emb = self.mod.get_embedder()
        seq = iter([{"embedding": [0.1, 0.2, 0.3]},
                    {"embedding": [0.1, 0.2]}])  # second has fewer dims

        emb._post_json = lambda path, payload: next(seq)
        with self.assertRaises(ValueError):
            emb.embed(["a", "b"])


if __name__ == "__main__":
    unittest.main()
