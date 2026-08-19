"""Tests for bare-`recall` honoring `retrieval_mode` (A/B) — Phase 7.1.

The Phase 7.1 wiring makes the onboarding-chosen retrieval mode actually change
what bare `recall` (and the loop's `R-CTX-05` recall) ranks with:

  * Mode A (`deterministic`, the DEFAULT): the gated BM25 default
    (`active_default_strategy` -> bm25 / bm25+acr). Byte-identical to today.
  * Mode B (`semantic`) WITH a local embedding model present: the `bm25+embed`
    hybrid stack.
  * Mode B requested with NO local model: graceful fall back to Mode A's default
    + a one-line notice on stderr (stdout/JSON stays Mode-A byte-identical).
  * An explicit `--strategy` always wins over the mode.

Invariants under test (Phase 7.1 acceptance criteria):
  - `deterministic` bare recall == Mode A (unchanged, byte-identical stdout).
  - `semantic` (model present) routes bare recall through the hybrid stack.
  - `semantic` with NO model falls back to Mode A + emits the fallback notice.
  - `active_recall_strategy` resolves the strategy/notice deterministically.

Stdlib `unittest` only (no pytest, no new deps). Reuses the deterministic
concept-lexicon stub embedder pattern from `test_embedding` to stand in for a
local model so the semantic path is exercised WITHOUT installing anything.
"""

from tests._paths import TESTS_DIR

import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, build_synthetic_kb, run_cli
except ImportError:  # allow direct invocation
    sys.path.insert(0, TESTS_DIR)
    from _fixtures import load_cli, build_synthetic_kb, run_cli


# A deterministic concept-lexicon stub embedder: maps synonyms (distinct surface
# tokens, same meaning) to the same dim so a paraphrase query lands in the target
# entry's concept dim. Rounds floats like a real local backend (determinism).
_STUB_CONCEPT_BACKEND = '''\
import re

CONCEPTS = [
    ("geofence", "location", "boundary", "perimeter", "geofencing", "arrive"),
    ("timeout", "gtimeout", "coreutils", "deadline"),
    ("bm25", "saturation", "idf", "ranking"),
]
_TOK = re.compile(r"[0-9a-z]+")


class ConceptEmbedder(object):
    def __init__(self, model=None):
        self.model = model or "concept"
        self.dim = len(CONCEPTS) + 1

    def embed(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        out = []
        for t in texts:
            toks = _TOK.findall((t or "").lower())
            vec = [0.0] * (len(CONCEPTS) + 1)
            for i, group in enumerate(CONCEPTS):
                gset = set(group)
                vec[i] = float(sum(1 for tok in toks if tok in gset))
            vec[-1] = 0.5  # constant fallback dim => never a zero vector
            out.append([round(x, 6) for x in vec])
        return out


def get_embedder(model=None):
    return ConceptEmbedder(model=model)
'''


class RecallModeWiringTest(unittest.TestCase):
    """Bare `recall` routes by `retrieval_mode`; Mode A stays byte-identical."""

    MODEL = "concept-test-v1"

    def setUp(self):
        self.cli = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="agentware-recall-mode-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        # The default synthetic KB has distinctive per-entry vocabulary.
        build_synthetic_kb(self.kdir)

        # Isolate config reads onto an empty temp file so env precedence is clean.
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

    def _enable_stub(self):
        path = os.path.join(self.kdir, "concept_backend.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_STUB_CONCEPT_BACKEND)
        os.environ[self.cli.EMBEDDER_BACKEND_KEY] = path
        os.environ[self.cli.EMBED_MODEL_KEY] = self.MODEL
        self.cli._reset_embedder_cache()

    def _set_mode(self, mode):
        os.environ[self.cli.RETRIEVAL_MODE_KEY] = mode

    # --- helper-level resolution --------------------------------------------
    def test_deterministic_mode_resolves_mode_a_strategy(self):
        self._set_mode("deterministic")
        strat, notice = self.cli.active_recall_strategy(self.kdir)
        self.assertEqual(strat, self.cli.active_default_strategy(self.kdir))
        self.assertIn(strat, ("bm25", "bm25+acr"))
        self.assertEqual(notice, "")

    def test_semantic_with_model_resolves_hybrid(self):
        self._set_mode("semantic")
        self._enable_stub()
        strat, notice = self.cli.active_recall_strategy(self.kdir)
        self.assertEqual(strat, "bm25+embed")
        self.assertEqual(notice, "")

    def test_semantic_without_model_falls_back_with_notice(self):
        self._set_mode("semantic")  # no embedder configured
        strat, notice = self.cli.active_recall_strategy(self.kdir)
        self.assertEqual(strat, self.cli.active_default_strategy(self.kdir))
        self.assertIn(strat, ("bm25", "bm25+acr"))
        self.assertTrue(notice, "a fallback notice must be surfaced")
        self.assertIn("semantic", notice.lower())

    # --- end-to-end via the CLI command -------------------------------------
    def test_deterministic_bare_recall_byte_identical_to_mode_a(self):
        # Default (no mode set) and explicit deterministic must both equal the
        # explicit Mode-A strategy run, byte-for-byte on stdout.
        code_a, out_a, _ = run_cli(
            ["recall", "geofence arrive reminders", "--strategy", "bm25",
             "--as-of", "2026-06-25", "--format", "json"], self.kdir)
        self._set_mode("deterministic")
        code_b, out_b, err_b = run_cli(
            ["recall", "geofence arrive reminders",
             "--as-of", "2026-06-25", "--format", "json"], self.kdir)
        self.assertEqual(code_a, 0)
        self.assertEqual(code_b, 0)
        self.assertEqual(out_a, out_b, "Mode A bare recall must be byte-identical")
        self.assertEqual(err_b, "", "Mode A must emit no fallback notice")

    def test_semantic_no_model_stdout_is_mode_a_plus_stderr_notice(self):
        self._set_mode("semantic")  # no embedder -> fall back to A
        code_det, out_det, _ = run_cli(
            ["recall", "geofence arrive reminders", "--strategy", "bm25",
             "--as-of", "2026-06-25", "--format", "json"], self.kdir)
        code, out, err = run_cli(
            ["recall", "geofence arrive reminders",
             "--as-of", "2026-06-25", "--format", "json"], self.kdir)
        self.assertEqual(code, 0)
        # stdout identical to Mode A (the strategy field reads bm25/bm25+acr, NOT
        # bm25+embed, because no model is installed).
        self.assertEqual(out, out_det)
        self.assertIn("semantic", err.lower())
        self.assertIn("falling back", err.lower())

    def test_semantic_with_model_uses_hybrid_strategy(self):
        self._set_mode("semantic")
        self._enable_stub()
        code, out, err = run_cli(
            ["recall", "geofence arrive reminders",
             "--as-of", "2026-06-25", "--format", "json"], self.kdir)
        self.assertEqual(code, 0)
        self.assertIn('"strategy": "bm25+embed"', out)
        self.assertEqual(err, "", "no fallback notice when the model is present")

    def test_explicit_strategy_overrides_mode(self):
        # Even in semantic mode with a model present, an explicit --strategy wins.
        self._set_mode("semantic")
        self._enable_stub()
        code, out, err = run_cli(
            ["recall", "geofence arrive reminders", "--strategy", "bm25",
             "--as-of", "2026-06-25", "--format", "json"], self.kdir)
        self.assertEqual(code, 0)
        self.assertIn('"strategy": "bm25"', out)
        self.assertNotIn("bm25+embed", out)
        self.assertEqual(err, "")

    def test_recall_ranked_hybrid_returns_doc_text_tuples(self):
        # The recall_ranked bm25+embed branch must return (entry, score, doc_text)
        # so the token-budget walk works; doc_text must be non-empty for hits.
        self._enable_stub()
        data, _ = self.cli.load_index(self.kdir)
        ranked = self.cli.recall_ranked(
            self.kdir, data, "geofence arrive reminders",
            top_k=5, strategy="bm25+embed", as_of="2026-06-25")
        self.assertTrue(ranked)
        for entry, score, doc_text in ranked:
            self.assertIsInstance(entry, dict)
            self.assertIsInstance(doc_text, str)
            self.assertTrue(doc_text, "doc_text must be populated for budgeting")

    def test_hybrid_recall_deterministic_across_runs(self):
        self._set_mode("semantic")
        self._enable_stub()
        argv = ["recall", "geofence arrive reminders",
                "--as-of", "2026-06-25", "--format", "json"]
        _, out1, _ = run_cli(argv, self.kdir)
        self.cli._reset_embedder_cache()
        _, out2, _ = run_cli(argv, self.kdir)
        self.assertEqual(out1, out2, "Mode B recall must be byte-identical")


if __name__ == "__main__":
    unittest.main()
