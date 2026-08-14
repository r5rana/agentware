"""Tests for the hybrid `bm25+embed` Mode-B retrieval strategy (feature
260625-semantic-retrieval-benchmark, Phase 5.1).

Invariants under test:
  * Paraphrase recall: a query that shares NO surface tokens with the
    semantically-correct entry (so BM25 scores it 0 and misses it) is surfaced by
    `bm25+embed` via the embedding cosine ranking fused over RRF.
  * Determinism (INV-1, Mode B): given a pinned model + cached vectors,
    `bm25+embed` is byte-identical across runs (integer-rank RRF + rounded cosine
    + the canonical tie-break).
  * Mode A intact: `--strategy bm25` is byte-for-byte UNCHANGED whether or not a
    local embedder is present; `bm25+embed` with NO embedder falls back to `bm25`.
  * Strategy-agnostic scorers: `evaluate` (own gold set) and `evaluate_longmemeval`
    (synthetic session corpora, embedded IN-MEMORY) both score `bm25+embed`.
  * The moat: adding the strategy keeps the static-import surface stdlib-only.

Stdlib-only (no pytest). A deterministic, dependency-free CONCEPT-lexicon stub
embedder stands in for a local embedding model: it maps synonyms (distinct
surface tokens, same meaning) to the same vector dim, so a paraphrase query lands
in the target entry's concept dim with high cosine while BM25 sees no shared term.
"""

import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, build_synthetic_kb, CLI_PATH
    from tests import _security_policy as policy
except ImportError:  # direct invocation
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _fixtures import load_cli, build_synthetic_kb, CLI_PATH
    import _security_policy as policy


# A deterministic concept-lexicon stub: each dim groups SYNONYMS that share
# meaning but NOT surface tokens, so a paraphrase query (no shared tokens with the
# target entry) lands in the same concept dim -> high cosine, while BM25 (purely
# lexical) scores 0. Rounds floats like a real local backend (determinism).
_STUB_CONCEPT_BACKEND = '''\
import re

CONCEPTS = [
    ("power", "charge", "voltage", "recharge", "energy", "battery", "drain"),
    ("geofence", "location", "boundary", "perimeter", "geofencing"),
    ("python", "stdlib", "interpreter", "runtime", "bytecode"),
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


# Custom corpus with TIGHTLY controlled vocabulary so a paraphrase query shares no
# surface token with its semantically-correct entry.
_ENTRIES = [
    {
        "id": "learn-power-charge",
        "title": "Power Charge Notes",
        "category": "learnings",
        "path": "learnings/power-charge.md",
        "tags": ["power", "charge", "voltage"],
        "created": "2026-02-01",
        "summary": "Managing voltage charge and recharge cycles for healthy energy.",
        "body": (
            "# Power Charge Notes\n\n"
            "Keep the voltage stable and recharge before the charge runs low; "
            "healthy power and energy habits.\n"
        ),
    },
    {
        "id": "ref-geofence-boundary",
        "title": "Geofence Boundary Notes",
        "category": "references",
        "path": "references/geofence-boundary.md",
        "tags": ["geofence", "location"],
        "created": "2026-02-02",
        "summary": "Defining a geofence boundary and perimeter for location alerts.",
        "body": (
            "# Geofence Boundary Notes\n\n"
            "A geofence draws a boundary perimeter around a location for "
            "geofencing alerts.\n"
        ),
    },
    {
        "id": "config-python-runtime",
        "title": "Python Runtime Notes",
        "category": "configurations",
        "path": "configurations/python-runtime.md",
        "tags": ["python", "runtime"],
        "created": "2026-02-03",
        "summary": "Pure python stdlib interpreter runtime conventions.",
        "body": (
            "# Python Runtime Notes\n\n"
            "The python interpreter runs stdlib bytecode in its runtime.\n"
        ),
    },
]

# A paraphrase of the power-charge entry that shares NO surface token with it
# (battery/drain are power-concept synonyms; saving/advice are generic).
_PARAPHRASE_QUERY = "battery drain saving advice"
_POWER_ID = "learn-power-charge"


class HybridEmbedTest(unittest.TestCase):
    MODEL = "concept-test-v1"

    def setUp(self):
        self.cli = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="agentware-embed-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.data = build_synthetic_kb(self.kdir, entries=_ENTRIES)

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

    def _enable_stub(self):
        path = os.path.join(self.kdir, "concept_backend.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_STUB_CONCEPT_BACKEND)
        os.environ[self.cli.EMBEDDER_BACKEND_KEY] = path
        os.environ[self.cli.EMBED_MODEL_KEY] = self.MODEL
        self.cli._reset_embedder_cache()

    def _disable_stub(self):
        os.environ.pop(self.cli.EMBEDDER_BACKEND_KEY, None)
        os.environ.pop(self.cli.EMBED_MODEL_KEY, None)
        self.cli._reset_embedder_cache()

    # --- 1. paraphrase recall: embed surfaces what BM25 misses ---------------
    def test_paraphrase_surfaced_by_embed_not_bm25(self):
        # Pure BM25 shares no token with the paraphrase => misses the entry.
        bm25 = self.cli.retrieve(self.kdir, self.data, _PARAPHRASE_QUERY, "bm25",
                                 top_k=5)
        self.assertNotIn(_POWER_ID, bm25,
                         "BM25 should miss the no-shared-token paraphrase")

        # Hybrid surfaces it via the embedding cosine ranking fused over RRF.
        self._enable_stub()
        hybrid = self.cli.retrieve(self.kdir, self.data, _PARAPHRASE_QUERY,
                                   "bm25+embed", top_k=5)
        self.assertIn(_POWER_ID, hybrid,
                      "bm25+embed should surface the semantic match BM25 missed")
        self.assertEqual(hybrid[0], _POWER_ID,
                         "the semantic match should rank first")

    # --- 2. determinism: byte-identical across runs --------------------------
    def test_bm25_embed_deterministic(self):
        self._enable_stub()
        first = self.cli.retrieve(self.kdir, self.data, _PARAPHRASE_QUERY,
                                  "bm25+embed", top_k=None)
        # Reset the embedder cache to force a fresh load -> still identical.
        self.cli._reset_embedder_cache()
        second = self.cli.retrieve(self.kdir, self.data, _PARAPHRASE_QUERY,
                                   "bm25+embed", top_k=None)
        self.assertEqual(first, second,
                         "bm25+embed must be byte-identical across runs")

    # --- 3. Mode A intact: bm25 unchanged whether or not embedder present -----
    def test_mode_a_bm25_unchanged_with_embedder_present(self):
        q = "voltage recharge"  # shares tokens with the power-charge entry
        without = self.cli.retrieve(self.kdir, self.data, q, "bm25", top_k=None)
        self._enable_stub()
        with_embed = self.cli.retrieve(self.kdir, self.data, q, "bm25", top_k=None)
        self.assertEqual(without, with_embed,
                         "embedder presence must NOT alter the bm25 ranking")

    def test_bm25_embed_falls_back_to_bm25_without_embedder(self):
        # No backend configured => bm25+embed degrades GRACEFULLY to plain bm25.
        q = "voltage recharge"
        bm25 = self.cli.retrieve(self.kdir, self.data, q, "bm25", top_k=None)
        hybrid = self.cli.retrieve(self.kdir, self.data, q, "bm25+embed",
                                   top_k=None)
        self.assertEqual(bm25, hybrid,
                         "no embedder => bm25+embed == bm25 (Mode A fallback)")

    def test_broken_embedder_falls_back_to_bm25(self):
        # A backend that errors on load must not crash recall.
        path = os.path.join(self.kdir, "broken_backend.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write("raise RuntimeError('boom at import')\n")
        os.environ[self.cli.EMBEDDER_BACKEND_KEY] = path
        os.environ[self.cli.EMBED_MODEL_KEY] = self.MODEL
        self.cli._reset_embedder_cache()
        q = "voltage recharge"
        bm25 = self.cli.retrieve(self.kdir, self.data, q, "bm25", top_k=None)
        hybrid = self.cli.retrieve(self.kdir, self.data, q, "bm25+embed",
                                   top_k=None)
        self.assertEqual(bm25, hybrid,
                         "broken embedder => graceful Mode-A fallback, no crash")

    # --- 4. strategy-agnostic scorers accept bm25+embed ----------------------
    def test_evaluate_scores_bm25_embed(self):
        self._enable_stub()
        gold = [{"query": _PARAPHRASE_QUERY, "expected_ids": [_POWER_ID]}]
        res = self.cli.evaluate(self.kdir, self.data, gold, "bm25+embed", 5)
        self.assertEqual(res["strategy"], "bm25+embed")
        # The paraphrase gold is recovered by the hybrid (BM25 alone would miss).
        self.assertEqual(res["metrics"]["recall_at_k"], 1.0)

    def test_longmemeval_scorer_strategy_agnostic_bm25_embed(self):
        # Synthetic session corpora have NO persisted vectors -> embedded
        # in-memory. Proves the scorer is strategy-agnostic for bm25+embed.
        self._enable_stub()
        dataset = [{
            "question_id": "q1",
            "question_type": "single-session-preference",
            "question": _PARAPHRASE_QUERY,
            "answer": "n/a",
            "haystack_session_ids": ["s-power", "s-geo", "s-py"],
            "haystack_sessions": [
                [{"role": "user", "content": "Keep voltage stable and recharge "
                                             "the charge; healthy power energy."}],
                [{"role": "user", "content": "Draw a geofence boundary perimeter "
                                             "around a location."}],
                [{"role": "user", "content": "The python interpreter runs stdlib "
                                             "bytecode in its runtime."}],
            ],
            "answer_session_ids": ["s-power"],
        }]
        path = os.path.join(self.kdir, "lme_tiny.json")
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dataset, f)
        res = self.cli.evaluate_longmemeval(self.kdir, path, "bm25+embed", 5,
                                            as_of="2026-06-25")
        self.assertEqual(res["strategy"], "bm25+embed")
        self.assertEqual(res["num_queries"], 1)
        self.assertEqual(res["metrics"]["recall_at_k"], 1.0,
                         "hybrid should recover the paraphrase session BM25 misses")
        self.assertTrue(res["determinism_ok"])

    # --- 5. RRF + cosine unit determinism ------------------------------------
    def test_rrf_fuse_is_deterministic_and_canonical(self):
        data = {"entries": [
            {"id": "a", "created": "2026-01-01"},
            {"id": "b", "created": "2026-01-02"},
            {"id": "c", "created": "2026-01-03"},
        ]}
        r1 = self.cli._rrf_fuse([["a", "b", "c"], ["c", "b", "a"]], data)
        r2 = self.cli._rrf_fuse([["a", "b", "c"], ["c", "b", "a"]], data)
        self.assertEqual(r1, r2)
        # RRF scores: a & c both = 1/61 + 1/63 (rank1+rank3) > b = 2/62
        # (rank2+rank2). a and c TIE -> canonical tie-break (created desc, id asc)
        # puts c (newer) before a; b ranks last.
        self.assertEqual(r1, ["c", "a", "b"])

    def test_cosine_bounds_and_zero(self):
        self.assertEqual(self.cli._cosine([1.0, 0.0], [1.0, 0.0]), 1.0)
        self.assertEqual(self.cli._cosine([1.0, 0.0], [0.0, 1.0]), 0.0)
        self.assertEqual(self.cli._cosine([0.0, 0.0], [1.0, 1.0]), 0.0)
        self.assertEqual(self.cli._cosine([], [1.0]), 0.0)

    # --- 6. the moat: static-import surface stays stdlib-only ----------------
    @unittest.skipIf(sys.version_info < (3, 10),
                     "sys.stdlib_module_names requires Python 3.10+")
    def test_static_import_surface_stays_stdlib_only(self):
        # The WHOLE always-on toolkit, not just the entrypoint: since task 25
        # `scripts/agentware` is a 110-line facade, so scanning that one path
        # made this guard vacuous. First-party in-repo modules are not
        # DEPENDENCIES — they ship in the clone and install nothing — and both
        # the corpus and that allowance are defined ONCE, in
        # tests/_security_policy.py. A real third-party import still reddens.
        # The subtraction lives in _security_policy too, not just the corpus
        # and the first-party set: a sibling import (scripts/hooks/codex-stream.py
        # -> codex_items) resolves per FILE via sys.path[0], which a flattened
        # union of module names cannot express.
        offenders = policy.guarded_import_offenders()
        self.assertEqual(offenders, [], "non-stdlib static imports: %s" % offenders)


if __name__ == "__main__":
    unittest.main()
