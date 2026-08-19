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

from tests._paths import TESTS_DIR

import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, CLI_PATH
    from tests import _security_policy as policy
except ImportError:  # direct invocation: `python3 tests/unit/retrieval/test_embedder.py`
    sys.path.insert(0, TESTS_DIR)
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

# A backend whose FACTORY raises ImportError because its own optional dependency
# is absent. This is the shipped fastembed backend's documented behaviour — it
# probes eagerly so `semantic_embedder_available()` is honest — and it is
# ABSENCE, not breakage, so it must stay silent.
_MISSING_DEP_BACKEND = '''\
def get_embedder(model=None):
    raise ImportError("thirdparty is not installed; pip install thirdparty==1.0")
'''

# A conforming backend that imports a lib module `vectorcache` does NOT pull in,
# so the import genuinely has to resolve through sys.path rather than being
# short-circuited by sys.modules. Stands in for any third-party backend reaching
# into agentware_lib.
_LIB_IMPORTING_BACKEND = '''\
import agentware_lib.core.gitio  # noqa: F401 - the point is that it resolves


class LibImportingEmbedder(object):
    def embed(self, texts):
        return [[0.0] for _ in texts]


def get_embedder(model=None):
    return LibImportingEmbedder()
'''

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

    def _load_capturing_stderr(self):
        """load_embedder() with stderr captured -> (embedder, stderr_text).

        The loudness half of the contract is an OBSERVABLE, so it is asserted
        the way the operator experiences it — bytes on stderr — rather than by
        reading the rejection dict, which would pass even if nothing was ever
        printed.
        """
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            emb = self.cli.load_embedder()
        return emb, buf.getvalue()

    # --- static-import surface stays stdlib-only -----------------------------
    @unittest.skipIf(sys.version_info < (3, 10),
                     "sys.stdlib_module_names requires Python 3.10+")
    def test_toolkit_does_not_statically_import_backend_or_network(self):
        # The WHOLE always-on toolkit, not just the entrypoint — since task 25
        # that path is a facade and scanning it alone made this guard vacuous.
        # Corpus and first-party allowance are defined once, in
        # tests/_security_policy.py; a real third-party import still reddens.
        # The subtraction lives in _security_policy too, not just the corpus
        # and the first-party set: a sibling import (scripts/hooks/codex-stream.py
        # -> codex_items) resolves per FILE via sys.path[0], which a flattened
        # union of module names cannot express.
        offenders = policy.guarded_import_offenders()
        mods = policy.guarded_import_surface()   # for the named-backend checks below
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
        """ABSENT, so SILENT: nothing is installed there to complain about."""
        self._use(os.path.join(self.tmp, "does-not-exist.py"))
        emb, err = self._load_capturing_stderr()
        self.assertIsNone(emb)
        self.assertEqual(err, "", "absence must not be reported as breakage")
        self.assertIsNone(self.cli.embedder_rejection())
        self.assertFalse(self.cli.semantic_embedder_available())

    def test_missing_dotted_module_is_none_no_crash(self):
        """ABSENT, so SILENT — the zero-install default, not a misconfiguration."""
        self._use("agentware_no_such_backend_xyz")
        emb, err = self._load_capturing_stderr()
        self.assertIsNone(emb)
        self.assertEqual(err, "")
        self.assertIsNone(self.cli.embedder_rejection())

    def test_backend_that_raises_on_import_is_none(self):
        """PRESENT and broken: it is installed, so the failure is named."""
        self._use(self._write_backend(_BOOM_BACKEND, "boom.py"))
        emb, err = self._load_capturing_stderr()
        self.assertIsNone(emb)
        self.assertIn("failed to load", err)
        self.assertIn("backend import blew up", err)
        self.assertIn("failed to load", self.cli.embedder_rejection() or "")

    def test_backend_whose_dependency_is_missing_is_silent(self):
        """A backend installed WITHOUT its own optional dependency is ABSENT.

        The shipped fastembed backend raises ImportError from `get_embedder()`
        by design, so `semantic_embedder_available()` is honest about a machine
        with nothing pip-installed — the default state. Warning there would put
        a line on every `config --show` in the zero-install case, which is how a
        diagnostic channel gets trained away. Found by running the suite and
        watching the real fastembed backend do exactly this.
        """
        self._use(self._write_backend(_MISSING_DEP_BACKEND, "nodep.py"))
        emb, err = self._load_capturing_stderr()
        self.assertIsNone(emb)
        self.assertEqual(err, "", "a missing dependency is absence, not breakage")
        self.assertIsNone(self.cli.embedder_rejection())

    def test_backend_without_factory_is_none(self):
        """PRESENT and non-conforming: rejected by the NAME of what is missing."""
        self._use(self._write_backend(_NO_FACTORY_BACKEND, "nofac.py"))
        emb, err = self._load_capturing_stderr()
        self.assertIsNone(emb)
        self.assertIn("get_embedder", err)
        self.assertIn("does not satisfy the embedder protocol", err)
        self.assertIn("get_embedder", self.cli.embedder_rejection() or "")

    def test_backend_returning_bad_object_is_none(self):
        """The plan's own criterion: a missing REQUIRED attribute is named.

        `embed` is the attribute; before the extension point this object became
        a bare None indistinguishable from "nothing installed", and the only
        symptom was that ranking quietly fell back to BM25.
        """
        self._use(self._write_backend(_BAD_OBJECT_BACKEND, "badobj.py"))
        emb, err = self._load_capturing_stderr()
        self.assertIsNone(emb)
        self.assertIn("missing embed", err)
        self.assertIn("does not satisfy the embedder protocol", err)
        reason = self.cli.embedder_rejection()
        self.assertIn("missing embed", reason or "")
        # And it reaches the operator through the mode resolver too, so the
        # advice is not the wrong one ("install a local model") for a backend
        # that IS installed.
        os.environ[self.cli.RETRIEVAL_MODE_KEY] = "semantic"
        eff, fell_back, notice = self.cli.resolve_effective_retrieval_mode()
        self.assertEqual(eff, "deterministic")
        self.assertTrue(fell_back)
        self.assertIn("rejected", notice)
        self.assertIn("missing embed", notice)

    def test_the_warning_is_emitted_once_per_spec_not_per_recall(self):
        """Loudness is bounded by the memo — a line per recall trains it away."""
        self._use(self._write_backend(_BAD_OBJECT_BACKEND, "badobj2.py"))
        _emb, first = self._load_capturing_stderr()
        self.assertIn("does not satisfy", first)
        for _ in range(3):
            _emb, again = self._load_capturing_stderr()
            self.assertEqual(again, "", "the memo must suppress repeats")
        # ... and a reset re-arms it, because the operator may have fixed it.
        self.cli._reset_embedder_cache()
        _emb, after_reset = self._load_capturing_stderr()
        self.assertIn("does not satisfy", after_reset)

    def test_a_registered_backend_name_resolves_to_its_shipped_module(self):
        """`--set-embedder ollama` works: the registry is the extension point."""
        from agentware_lib.core import embedderapi
        self.assertIn("ollama", embedderapi.registered_names())
        self._use("ollama")
        emb, err = self._load_capturing_stderr()
        self.assertIsNotNone(emb, err)
        self.assertTrue(callable(getattr(emb, "embed", None)))
        self.assertEqual(err, "")

    def test_an_unregistered_spec_still_resolves_the_way_it_always_did(self):
        """The registry ADDS a spelling; it takes none away."""
        self._use("agentware_embedder_ollama")
        emb, _err = self._load_capturing_stderr()
        self.assertIsNotNone(emb)

    def test_a_backend_may_import_a_lib_module_the_cli_has_not_loaded(self):
        """A path-loaded backend reaches `agentware_lib` with NO sys.path help.

        This pins the reason the loader carries no sys.path bootstrap — a guard
        for that was written, could not be made to fail, and was deleted. Python
        resolves a SUBMODULE through its parent package's `__path__` (absolute),
        not through `sys.path`, and the loader lives INSIDE `agentware_lib`, so
        the package is necessarily imported before any backend body runs. The
        test stays because the property is load-bearing and non-obvious: if it
        ever stopped holding, an ImportError would be classified as ABSENCE and
        a correctly installed backend would vanish into a silent Mode-A fallback
        with no diagnostic at all.

        Two details make it non-vacuous, and both were learned the hard way. It
        runs in a SUBPROCESS (the suite's own fixture imports every lib module,
        so nothing in-process is ever un-imported), and it imports a module the
        CLI does NOT pull in — `vectorcache` loads 19 lib modules and
        `core.gitio` is not one of them. The first attempt asserted in-process
        against `core.embedderapi` and passed with the guard reverted, because
        `sys.modules` short-circuits the import before `sys.path` is consulted.
        A test of "can it import?" is vacuous unless the module is genuinely not
        loaded yet.
        """
        import subprocess
        scripts_dir = os.path.dirname(os.path.abspath(CLI_PATH))
        backend = self._write_backend(_LIB_IMPORTING_BACKEND, "libimport.py")
        driver = (
            "import sys\n"
            "scripts, backend = sys.argv[1], sys.argv[2]\n"
            "sys.path.insert(0, scripts)\n"
            "import agentware_lib.retrieval.vectorcache as vc\n"
            "sys.path.remove(scripts)\n"   # the caller's bootstrap is gone
            "emb = vc.load_embedder(backend=backend, model='m')\n"
            "print('LOADED' if emb is not None else 'NONE')\n")
        proc = subprocess.run(
            [sys.executable, "-c", driver, scripts_dir, backend],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "LOADED", proc.stderr)

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
        # The backend imports `agentware_lib.core.embedderapi` for the shared
        # quantizer, so `scripts/` must be importable before its body runs. The
        # product guarantees this in `_load_embedder_module`; a raw
        # SourceFileLoader (this class, and any embedding caller) does not go
        # through it, so the sibling fastembed suite's discipline applies here.
        scripts_dir = os.path.dirname(os.path.abspath(CLI_PATH))
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from agentware_lib.core import embedderapi
        self.api = embedderapi
        path = os.path.join(scripts_dir, "agentware_embedder_ollama.py")
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
        # Rounded to EMBED_ROUND_DP places (determinism guard). The constant now
        # lives ONCE, in the extension point, and the backend reaches it through
        # the shared quantizer rather than restating the literal.
        self.assertEqual(v[0][0], round(0.1234567, self.api.EMBED_ROUND_DP))
        self.assertFalse(hasattr(self.mod, "EMBED_ROUND_DP"),
                         "the backend must not re-declare the rounding constant")
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
