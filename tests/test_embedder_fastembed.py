"""Tests for the REAL pinned fastembed backend + SETTINGS_AW (feature
260627-semantic-embedding-settings).

Two layers:
  * ALWAYS-ON (no fastembed required): the graceful-degrade path (missing
    fastembed -> load_embedder None -> Mode A byte-identical), the SETTINGS_AW
    get/set round-trip + post-hoc switch persistence, and the stdlib-only +
    no-network static-import guards re-asserted on the toolkit AND the backend.
  * SKIP-IF-ABSENT (real model): determinism on this machine (same input ->
    identical rounded vector), fixed-dim, vector-cache freshness/invalidation,
    and hybrid-fusion correctness (a paraphrase query with zero shared tokens
    ranks the semantically-correct entry that plain BM25 misses).

Stdlib-only (no pytest). The real-model assertions download/load a pinned model
the first time; they are skipped cleanly when fastembed is not installed.
"""

import ast
import json
import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests._fixtures import (load_cli, run_cli, build_synthetic_kb,
                                 CLI_PATH, REPO_ROOT, first_party_modules)
except ImportError:  # direct invocation
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _fixtures import (load_cli, run_cli, build_synthetic_kb,
                           CLI_PATH, REPO_ROOT, first_party_modules)

SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
FASTEMBED_BACKEND = "agentware_embedder_fastembed"
FASTEMBED_BACKEND_PATH = os.path.join(SCRIPTS_DIR, FASTEMBED_BACKEND + ".py")
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


def _fastembed_installed():
    try:
        import fastembed  # noqa: F401
        return True
    except ImportError:
        return False


def _imported_top_modules(source_path):
    """All imported top modules ANYWHERE in the file (incl. lazy in-function)."""
    with open(source_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=source_path)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split(".")[0])
    return mods


def _module_level_imports(source_path):
    """Only the MODULE-TOP-LEVEL imports (tree.body) — lazy in-method imports are
    intentionally excluded, mirroring the static-import-surface guard."""
    with open(source_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=source_path)
    mods = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split(".")[0])
    return mods


class _BackendTestBase(unittest.TestCase):
    """Isolates config + embedder env onto temp state; resets the lazy cache."""

    def setUp(self):
        self.cli = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="agentware-fastembed-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.data = build_synthetic_kb(self.kdir)

        self.cfg = os.path.join(self.kdir, ".agentware", "config.env")
        self._orig_home_config = self.cli.HOME_CONFIG
        self._orig_config_paths = self.cli.CONFIG_PATHS
        self.cli.HOME_CONFIG = self.cfg
        self.cli.CONFIG_PATHS = (self.cfg,)

        self._prev_env = {k: os.environ.pop(k, None) for k in (
            self.cli.EMBEDDER_BACKEND_KEY, self.cli.EMBED_MODEL_KEY,
            self.cli.RETRIEVAL_MODE_KEY)}
        self._added_path = False
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
            self._added_path = True
        self.cli._reset_embedder_cache()

        def _restore():
            self.cli.HOME_CONFIG = self._orig_home_config
            self.cli.CONFIG_PATHS = self._orig_config_paths
            for k, v in self._prev_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            if self._added_path and SCRIPTS_DIR in sys.path:
                sys.path.remove(SCRIPTS_DIR)
            self.cli._reset_embedder_cache()
        self.addCleanup(_restore)

    def _use_fastembed(self, model=DEFAULT_MODEL):
        os.environ[self.cli.EMBEDDER_BACKEND_KEY] = FASTEMBED_BACKEND
        os.environ[self.cli.EMBED_MODEL_KEY] = model
        self.cli._reset_embedder_cache()


# --- ALWAYS-ON: graceful degradation + static-import guards -------------------
class FastEmbedGracefulDegradeTest(_BackendTestBase):
    @unittest.skipIf(sys.version_info < (3, 10),
                     "sys.stdlib_module_names requires Python 3.10+")
    def test_toolkit_static_import_surface_is_stdlib_only(self):
        mods = _imported_top_modules(CLI_PATH)
        # First-party in-repo modules are not DEPENDENCIES: `agentware_lib` is
        # the package's own decomposed body, shipped in the clone, installing
        # nothing (see _fixtures.first_party_modules). A real third-party import
        # still reddens this.
        stdlib = set(sys.stdlib_module_names) | set(sys.builtin_module_names)
        stdlib |= first_party_modules()
        offenders = sorted(m for m in mods if m not in stdlib)
        self.assertEqual(offenders, [], "non-stdlib static imports: %s" % offenders)
        # The optional backends + fastembed are loaded dynamically, never statically.
        self.assertNotIn(FASTEMBED_BACKEND, mods)
        self.assertNotIn("fastembed", mods)
        for net in ("urllib", "socket", "http", "requests", "httpx"):
            self.assertNotIn(net, mods, "%s must not be a static import" % net)

    def test_backend_file_does_not_statically_import_fastembed(self):
        # The backend may live off the toolkit surface, but it STILL must import
        # fastembed lazily (inside methods) so importlib-loading it is cheap and
        # never requires fastembed to be present at module load time.
        mods = _module_level_imports(FASTEMBED_BACKEND_PATH)
        self.assertNotIn("fastembed", mods,
                         "fastembed must be imported lazily inside methods, not "
                         "at backend module top level")

    def test_missing_fastembed_falls_back_to_bm25_byte_identical(self):
        # Capture the Mode-A (bm25) recall payload BEFORE configuring semantic.
        baseline = self.cli.build_recall_payload(
            self.kdir, self.data, "deterministic ranking bm25", strategy="bm25",
            as_of="2026-06-27")

        # Configure semantic + the fastembed backend, but simulate fastembed
        # UNINSTALLED so the backend's eager import check fails -> load None.
        prev = sys.modules.get("fastembed", "__ABSENT__")
        sys.modules["fastembed"] = None  # makes `import fastembed` raise ImportError
        try:
            self._use_fastembed()
            os.environ[self.cli.RETRIEVAL_MODE_KEY] = "semantic"
            self.assertIsNone(self.cli.load_embedder())
            self.assertFalse(self.cli.semantic_embedder_available())
            eff, fell_back, notice = self.cli.resolve_effective_retrieval_mode()
            self.assertEqual(eff, "deterministic")
            self.assertTrue(fell_back)
            # Bare-recall strategy resolves to the Mode-A default, NOT bm25+embed.
            strat, _notice = self.cli.active_recall_strategy(self.kdir)
            self.assertNotEqual(strat, "bm25+embed")
            # And the recall payload is byte-identical to the Mode-A baseline.
            same = self.cli.build_recall_payload(
                self.kdir, self.data, "deterministic ranking bm25",
                strategy="bm25", as_of="2026-06-27")
            self.assertEqual(
                json.dumps(same, sort_keys=True),
                json.dumps(baseline, sort_keys=True))
        finally:
            if prev == "__ABSENT__":
                sys.modules.pop("fastembed", None)
            else:
                sys.modules["fastembed"] = prev
            self.cli._reset_embedder_cache()

    def test_get_embedder_missing_dep_names_pin(self):
        import agentware_embedder_fastembed as fb
        prev = sys.modules.get("fastembed", "__ABSENT__")
        sys.modules["fastembed"] = None
        try:
            with self.assertRaises(ImportError) as ctx:
                fb.get_embedder()
            self.assertIn("fastembed==0.8.0", str(ctx.exception))
        finally:
            if prev == "__ABSENT__":
                sys.modules.pop("fastembed", None)
            else:
                sys.modules["fastembed"] = prev


# --- ALWAYS-ON: SETTINGS_AW round-trip + post-hoc switch ---------------------
class SettingsAwTest(_BackendTestBase):
    def _cfg(self, *argv):
        return run_cli(["config", *argv], self.kdir)

    def test_set_retrieval_persists_and_round_trips(self):
        code, _out, _err = self._cfg("--set-retrieval", "semantic")
        self.assertEqual(code, 0)
        self.assertEqual(
            self.cli._read_config_key(self.cli.RETRIEVAL_MODE_KEY), "semantic")
        # Switch back to bm25 (deterministic) — persistence overwrites in place.
        code, _o, _e = self._cfg("--set-retrieval", "bm25")
        self.assertEqual(code, 0)
        self.assertEqual(
            self.cli._read_config_key(self.cli.RETRIEVAL_MODE_KEY), "deterministic")

    def test_set_embedder_and_model_round_trip(self):
        code, out, _e = self._cfg("--set-embedder", FASTEMBED_BACKEND)
        self.assertEqual(code, 0)
        code, out, _e = self._cfg("--embedder-only")
        self.assertEqual(out.strip(), FASTEMBED_BACKEND)
        code, _o, _e = self._cfg("--set-embed-model", "BAAI/bge-base-en-v1.5")
        self.assertEqual(code, 0)
        code, out, _e = self._cfg("--embed-model-only")
        self.assertEqual(out.strip(), "BAAI/bge-base-en-v1.5")

    def test_invalid_setter_tokens_exit_nonzero(self):
        self.assertNotEqual(self._cfg("--set-retrieval", "bogus")[0], 0)
        self.assertNotEqual(self._cfg("--set-embedder", "")[0], 0)
        self.assertNotEqual(self._cfg("--set-embed-model", "a b")[0], 0)

    def test_post_hoc_switch_changes_next_recall_strategy(self):
        # Onboarding-choice persistence: set semantic + fastembed backend.
        self._cfg("--set-retrieval", "semantic")
        self._cfg("--set-embedder", FASTEMBED_BACKEND)
        self.cli._reset_embedder_cache()
        if _fastembed_installed():
            strat, _n = self.cli.active_recall_strategy(self.kdir)
            self.assertEqual(strat, "bm25+embed")
        # Switch to bm25 mid-session: the very next resolution uses Mode A.
        self._cfg("--set-retrieval", "bm25")
        self.cli._reset_embedder_cache()
        strat, _n = self.cli.active_recall_strategy(self.kdir)
        self.assertNotEqual(strat, "bm25+embed")

    def _audit_check(self, env=None):
        prev = {}
        for k, v in (env or {}).items():
            prev[k] = os.environ.get(k)
            os.environ[k] = v
        self.cli._reset_embedder_cache()
        try:
            code, out, _err = run_cli(["audit", "--format", "json"], self.kdir)
            payload = json.loads(out)
            checks = {c["name"]: c for c in payload["checks"]}
            return checks.get("mode_b_health")
        finally:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            self.cli._reset_embedder_cache()

    def test_audit_mode_b_health_inert_in_deterministic(self):
        chk = self._audit_check()  # default deterministic
        self.assertIsNotNone(chk)
        self.assertTrue(chk["ok"])
        self.assertIn("inert", chk["details"][0])

    def test_audit_mode_b_health_warns_on_missing_embedder_in_semantic(self):
        chk = self._audit_check({"AGENTWARE_RETRIEVAL_MODE": "semantic"})
        self.assertIsNotNone(chk)
        self.assertFalse(chk["ok"])

    def test_config_json_surfaces_all_settings_keys(self):
        self._cfg("--set-embedder", FASTEMBED_BACKEND)
        code, out, _e = run_cli(["config", "--format", "json"], self.kdir)
        payload = json.loads(out)
        for key in ("retrieval_mode", "effective_retrieval_mode",
                    "embedder_backend", "embed_model",
                    "semantic_embedder_available"):
            self.assertIn(key, payload)


# --- SKIP-IF-ABSENT: real fastembed model behavior ---------------------------
@unittest.skipUnless(_fastembed_installed(),
                     "fastembed not installed; real-model assertions skipped")
class FastEmbedRealModelTest(_BackendTestBase):
    def test_embed_is_fixed_dim_and_deterministic_on_this_machine(self):
        import agentware_embedder_fastembed as fb
        emb = fb.get_embedder(model=DEFAULT_MODEL)
        v1 = emb.embed(["alpha", "beta"])
        self.assertEqual(len(v1), 2)
        self.assertEqual(len(v1[0]), len(v1[1]))  # fixed dim
        self.assertEqual(emb.dim, len(v1[0]))
        # Same input -> identical ROUNDED vector across two independent calls.
        emb2 = fb.get_embedder(model=DEFAULT_MODEL)
        v2 = emb2.embed(["alpha", "beta"])
        self.assertEqual(v1, v2)
        # Distinct inputs -> distinct vectors (it is actually embedding).
        self.assertNotEqual(v1[0], v1[1])

    def test_vector_cache_freshness_and_invalidation(self):
        self._use_fastembed()
        model = DEFAULT_MODEL
        ids = [e["id"] for e in self.data["entries"]]

        stats = self.cli.rebuild_vector_cache(self.kdir, self.data)
        self.assertEqual(stats["status"], "built")
        self.assertEqual(stats["embedded"], len(ids))
        self.assertEqual(stats["reused"], 0)

        # Unchanged corpus -> a re-run reuses everything (0 re-embeds).
        stats2 = self.cli.rebuild_vector_cache(self.kdir, self.data)
        self.assertEqual(stats2["embedded"], 0)
        self.assertEqual(stats2["reused"], len(ids))

        # Mutate one entry body -> only that entry re-embeds.
        changed = self.data["entries"][0]
        with open(os.path.join(self.kdir, changed["path"]), "a",
                  encoding="utf-8") as f:
            f.write("\nA brand new paraphrase sentence with novel vocabulary.\n")
        stats3 = self.cli.rebuild_vector_cache(self.kdir, self.data)
        self.assertEqual(stats3["embedded"], 1)
        self.assertEqual(stats3["reused"], len(ids) - 1)

        # Delete + rebuild -> byte-identical cache file (determinism on machine).
        path = self.cli.vector_cache_path(self.kdir, model)
        with open(path, "rb") as f:
            before = f.read()
        os.remove(path)
        self.cli.rebuild_vector_cache(self.kdir, self.data)
        with open(path, "rb") as f:
            after = f.read()
        self.assertEqual(before, after)

    def test_hybrid_fusion_surfaces_paraphrase_bm25_misses(self):
        # A tiny KB: one entry about dogs (NO 'dog'/'pet'/'puppy' tokens), one
        # about an unrelated topic. A query with zero lexical overlap with the
        # dog entry must still rank it first via the embedding signal.
        entries = [
            {"id": "learn-canine", "title": "Loyal Four-Legged Companion",
             "category": "learnings", "path": "learnings/canine.md",
             "tags": ["animal"], "created": "2026-01-02",
             "summary": "A loyal four-legged companion that barks and fetches.",
             "body": ("# Loyal Four-Legged Companion\n\n"
                      "A loyal four-legged companion that barks, wags its tail, "
                      "fetches a ball, and guards the house. A faithful canine "
                      "friend.\n")},
            {"id": "learn-ledger", "title": "Quarterly Accounting Ledger",
             "category": "learnings", "path": "learnings/ledger.md",
             "tags": ["finance"], "created": "2026-01-03",
             "summary": "Reconciling debits and credits in a spreadsheet.",
             "body": ("# Quarterly Accounting Ledger\n\n"
                      "Reconcile debits and credits, balance the spreadsheet, "
                      "and file the quarterly tax report with the auditor.\n")},
        ]
        kdir = tempfile.mkdtemp(prefix="agentware-fastembed-para-")
        self.addCleanup(shutil.rmtree, kdir, True)
        data = build_synthetic_kb(kdir, entries=entries)
        os.environ[self.cli.EMBEDDER_BACKEND_KEY] = FASTEMBED_BACKEND
        os.environ[self.cli.EMBED_MODEL_KEY] = DEFAULT_MODEL
        self.cli._reset_embedder_cache()
        self.cli.rebuild_vector_cache(kdir, data)

        query = "pet dog puppy"  # zero shared tokens with the canine entry
        # Plain BM25 must NOT surface the canine entry first (no lexical overlap).
        bm25_ids = self.cli.retrieve_bm25(kdir, data, query)
        # Hybrid bm25+embed must rank the canine entry first.
        hybrid_ids = self.cli.retrieve_bm25_embed(kdir, data, query)
        self.assertEqual(hybrid_ids[0], "learn-canine",
                         "embedding signal should surface the paraphrase entry")
        self.assertNotEqual(
            bm25_ids[:1], ["learn-canine"],
            "BM25 alone should not rank the zero-overlap entry first")

    def test_bm25_strategies_unaffected_by_backend_presence(self):
        # With the fastembed backend configured, bm25 / bm25+acr stay byte-identical.
        baseline_bm25 = self.cli.build_recall_payload(
            self.kdir, self.data, "deterministic ranking", strategy="bm25",
            as_of="2026-06-27")
        baseline_acr = self.cli.build_recall_payload(
            self.kdir, self.data, "deterministic ranking", strategy="bm25+acr",
            as_of="2026-06-27")
        self._use_fastembed()
        after_bm25 = self.cli.build_recall_payload(
            self.kdir, self.data, "deterministic ranking", strategy="bm25",
            as_of="2026-06-27")
        after_acr = self.cli.build_recall_payload(
            self.kdir, self.data, "deterministic ranking", strategy="bm25+acr",
            as_of="2026-06-27")
        self.assertEqual(json.dumps(baseline_bm25, sort_keys=True),
                         json.dumps(after_bm25, sort_keys=True))
        self.assertEqual(json.dumps(baseline_acr, sort_keys=True),
                         json.dumps(after_acr, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
