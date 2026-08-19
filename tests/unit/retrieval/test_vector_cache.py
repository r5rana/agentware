"""Tests for the derived Mode-B vector cache (feature
260625-semantic-retrieval-benchmark, Phase 4.1).

Invariants under test:
  * The cache is a DERIVED, gitignored artifact written via `index rebuild`
    (rebuild_vector_cache / build_vector_cache) — NEVER lazily inside recall.
  * Incremental: a rebuild re-embeds ONLY content-changed entries and reuses the
    rest byte-for-byte (keyed by content fingerprint + pinned model id).
  * Determinism (INV-1, Mode B): delete+rebuild yields a byte-identical cache
    file given a pinned model + identical corpus.
  * Graceful fallback (INV-2): no backend => no file written, Mode A intact;
    missing / corrupt / version- or model-mismatched cache => load returns None.

Stdlib-only (no pytest). A deterministic hash-based stub backend stands in for a
local embedding model so the tests need no network and no installed model.
"""

from tests._paths import TESTS_DIR

import json
import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests._fixtures import (load_cli, run_cli, build_synthetic_kb,
                                 CLI_PATH)
    from tests import _security_policy as policy
except ImportError:  # direct invocation
    sys.path.insert(0, TESTS_DIR)
    from _fixtures import (load_cli, run_cli, build_synthetic_kb,
                           CLI_PATH)
    import _security_policy as policy


# Deterministic, dependency-free stub embedder (fixed dim, same input -> same
# vector); pointed at via AGENTWARE_EMBEDDER_BACKEND.
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


class VectorCacheTest(unittest.TestCase):
    MODEL = "test-model-v1"

    def setUp(self):
        self.cli = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="agentware-veccache-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.data = build_synthetic_kb(self.kdir)

        # Isolate config reads onto an empty temp file.
        self.cfg = os.path.join(self.kdir, ".agentware", "config.env")
        self._orig_home_config = self.cli.HOME_CONFIG
        self._orig_config_paths = self.cli.CONFIG_PATHS
        self.cli.HOME_CONFIG = self.cfg
        self.cli.CONFIG_PATHS = (self.cfg,)

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

    def _enable_stub(self, model=None):
        path = os.path.join(self.kdir, "stub_backend.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_STUB_BACKEND)
        os.environ[self.cli.EMBEDDER_BACKEND_KEY] = path
        os.environ[self.cli.EMBED_MODEL_KEY] = model or self.MODEL
        self.cli._reset_embedder_cache()

    def _entry_ids(self):
        return [e["id"] for e in self.data["entries"]]

    # --- build path ----------------------------------------------------------
    def test_build_writes_cache_with_all_entries(self):
        self._enable_stub()
        stats = self.cli.rebuild_vector_cache(self.kdir, self.data)
        self.assertEqual(stats["status"], "built")
        self.assertEqual(stats["embedded"], len(self._entry_ids()))
        self.assertEqual(stats["reused"], 0)
        self.assertEqual(stats["dim"], 8)

        path = self.cli.vector_cache_path(self.kdir, self.MODEL)
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
        self.assertEqual(cache["version"], self.cli.VECTOR_CACHE_VERSION)
        self.assertEqual(cache["model"], self.MODEL)
        self.assertEqual(set(cache["vectors"].keys()), set(self._entry_ids()))
        for rec in cache["vectors"].values():
            self.assertEqual(len(rec["v"]), 8)
            self.assertIn("fp", rec)

    def test_cache_lives_under_dot_cache(self):
        self._enable_stub()
        self.cli.rebuild_vector_cache(self.kdir, self.data)
        path = self.cli.vector_cache_path(self.kdir, self.MODEL)
        rel = os.path.relpath(path, self.kdir)
        self.assertTrue(rel.startswith(".cache" + os.sep),
                        "cache must live under .cache/: %s" % rel)

    # --- no backend => graceful skip, Mode A intact --------------------------
    def test_no_backend_skips_and_writes_nothing(self):
        stats = self.cli.rebuild_vector_cache(self.kdir, self.data)
        self.assertEqual(stats["status"], "skipped")
        self.assertFalse(os.path.exists(os.path.join(self.kdir, ".cache")))

    # --- incremental: only content-changed entries re-embed ------------------
    def test_incremental_reuses_unchanged_entries(self):
        self._enable_stub()
        self.cli.rebuild_vector_cache(self.kdir, self.data)

        # Mutate exactly one entry's body file.
        changed = self.data["entries"][0]
        with open(os.path.join(self.kdir, changed["path"]), "a",
                  encoding="utf-8") as f:
            f.write("\nA brand new paraphrase sentence with novel vocabulary.\n")

        stats = self.cli.rebuild_vector_cache(self.kdir, self.data)
        self.assertEqual(stats["status"], "built")
        self.assertEqual(stats["embedded"], 1, "only the changed entry re-embeds")
        self.assertEqual(stats["reused"], len(self._entry_ids()) - 1)

    def test_unchanged_corpus_reuses_everything(self):
        self._enable_stub()
        self.cli.rebuild_vector_cache(self.kdir, self.data)
        stats = self.cli.rebuild_vector_cache(self.kdir, self.data)
        self.assertEqual(stats["embedded"], 0)
        self.assertEqual(stats["reused"], len(self._entry_ids()))

    # --- determinism: delete + rebuild is byte-identical ---------------------
    def test_delete_rebuild_byte_identical(self):
        self._enable_stub()
        self.cli.rebuild_vector_cache(self.kdir, self.data)
        path = self.cli.vector_cache_path(self.kdir, self.MODEL)
        with open(path, "rb") as f:
            first = f.read()

        os.remove(path)
        self.assertFalse(os.path.exists(path))
        self.cli.rebuild_vector_cache(self.kdir, self.data)
        with open(path, "rb") as f:
            second = f.read()
        self.assertEqual(first, second, "delete+rebuild must be byte-identical")

    # --- graceful load -------------------------------------------------------
    def test_load_missing_cache_is_none(self):
        self.assertIsNone(self.cli.load_vector_cache(self.kdir, self.MODEL))

    def test_load_corrupt_cache_is_none(self):
        path = self.cli.vector_cache_path(self.kdir, self.MODEL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json ")
        self.assertIsNone(self.cli.load_vector_cache(self.kdir, self.MODEL))

    def test_load_version_mismatch_is_none(self):
        self._enable_stub()
        self.cli.rebuild_vector_cache(self.kdir, self.data)
        path = self.cli.vector_cache_path(self.kdir, self.MODEL)
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
        cache["version"] = self.cli.VECTOR_CACHE_VERSION + 99
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        self.assertIsNone(self.cli.load_vector_cache(self.kdir, self.MODEL))

    def test_load_model_mismatch_is_none(self):
        self._enable_stub()
        self.cli.rebuild_vector_cache(self.kdir, self.data)
        # Asking for a DIFFERENT model id finds no matching cache file.
        self.assertIsNone(self.cli.load_vector_cache(self.kdir, "other-model"))

    # --- gitignore: the cache is a local-only derived artifact ---------------
    def test_cache_is_in_required_gitignore(self):
        self.assertIn(".cache", self.cli.KB_GITIGNORE_REQUIRED)
        self.assertIn(".cache/", self.cli.KB_GITIGNORE_CONTENT)

    def test_ensure_gitignore_appends_cache_rule(self):
        # A KB scaffolded before the rule existed gets it appended.
        gi = os.path.join(self.kdir, self.cli.GITIGNORE_REL)
        with open(gi, "w", encoding="utf-8") as f:
            f.write("logs/\n.loop/\n")
        action = self.cli._ensure_kb_gitignore(self.kdir)
        self.assertEqual(action, "appended")
        with open(gi, encoding="utf-8") as f:
            present = set(ln.strip().rstrip("/") for ln in f.read().splitlines())
        self.assertIn(".cache", present)

    def test_build_self_heals_gitignore(self):
        # A Mode-B rebuild must never leave `.cache/` committable: building the
        # cache ensures the gitignore rule first.
        self._enable_stub()
        stats = self.cli.rebuild_vector_cache(self.kdir, self.data)
        self.assertEqual(stats["status"], "built")
        gi = os.path.join(self.kdir, self.cli.GITIGNORE_REL)
        self.assertTrue(os.path.isfile(gi))
        with open(gi, encoding="utf-8") as f:
            present = set(ln.strip().rstrip("/") for ln in f.read().splitlines())
        self.assertIn(".cache", present)

    # --- the cache build keeps the static-import surface stdlib-only ---------
    @unittest.skipIf(sys.version_info < (3, 10),
                     "sys.stdlib_module_names requires Python 3.10+")
    def test_static_import_surface_stays_stdlib_only(self):
        # The WHOLE always-on toolkit, not just the entrypoint — since task 25
        # that path is a facade and scanning it alone made this guard vacuous.
        # Corpus and first-party allowance are defined once, in
        # tests/_security_policy.py; a real third-party import still reddens.
        # The subtraction lives in _security_policy too, not just the corpus
        # and the first-party set: a sibling import (scripts/hooks/codex-stream.py
        # -> codex_items) resolves per FILE via sys.path[0], which a flattened
        # union of module names cannot express.
        offenders = policy.guarded_import_offenders()
        self.assertEqual(offenders, [], "non-stdlib static imports: %s" % offenders)


if __name__ == "__main__":
    unittest.main()
