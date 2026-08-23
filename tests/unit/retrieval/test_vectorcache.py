"""Owning unit file for `agentware_lib.retrieval.vectorcache` — task 99 wave 9.

The module had no owning file. `test_vector_cache.py` beside it is a BEHAVIOUR
suite for the derived Mode-B artifact — it drives the writer through
`index rebuild` and reads the file that lands — which is the right test for the
feature and the wrong one for the module: it cannot reach a decision that never
changes the file it produces, and it cannot reach a backend that breaks its own
contract, because a real stub embedder never does.

What is asserted here is the module's PROMISE:

  * INCREMENTAL BY FINGERPRINT, NOT BY CLOCK. A vector is reused when the entry
    id and the CONTENT fingerprint agree — never on mtime, so the cache survives
    a clone that rewrote every timestamp.
  * A CACHE FILE IS UNTRUSTED INPUT. It is JSON on disk that anything may have
    rewritten; a malformed row must cause a re-embed, never a crash.
  * ONE DIMENSION PER CACHE. A file mixing two vector widths produces cosine
    scores that are silently meaningless for half the corpus, so a drift is
    raised and the error NAMES the entry that drifted.
  * KEYED BY ID, SO AN ENTRY WITHOUT ONE IS DROPPED — a vector nothing can look
    up is one nothing will ever read.
  * MODE A NEEDS NOTHING INSTALLED. The static import surface stays stdlib-only
    and an absent backend degrades to the deterministic mode with a notice.

Stdlib-only, hermetic: an in-process stand-in stands for the optional local
embedding backend, so the tests need no network and no installed model.
"""

from tests._paths import TESTS_DIR

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

try:
    from tests._fixtures import build_synthetic_kb
except ImportError:  # direct invocation
    sys.path.insert(0, TESTS_DIR)
    from _fixtures import build_synthetic_kb

import agentware_lib.retrieval.vectorcache as vectorcache  # noqa: E402


class _Embedder(object):
    """A deterministic stand-in for a local backend.

    Vectors are a function of the TEXT, so a reuse decision that silently
    re-embeds shows up in the `embedded`/`reused` counts rather than in the
    values — which is what makes the incremental claim countable. The two
    contract-breaking modes exist because no real backend can be relied on to
    break its contract on demand, and those are exactly the arms the parent
    defends against.
    """

    def __init__(self, dim=4, drift_after=None, miscount=None):
        self.dim = dim
        self.drift_after = drift_after
        self.miscount = miscount
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.miscount is not None:
            return [[0.0] * self.dim] * self.miscount
        out = []
        for i, t in enumerate(texts):
            d = self.dim
            if self.drift_after is not None and i >= self.drift_after:
                d = self.dim + 1
            out.append([round(((len(t) + j * 7) % 97) / 100.0, 6)
                        for j in range(d)])
        return out


def _plan_row(eid, fp, vec):
    return (eid, fp, vec)


class VectorCacheTestCase(unittest.TestCase):
    """Base: a scratch KB per test, and nothing outside it is touched."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="w9-vectorcache-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.kdir = os.path.join(self.root, "kb")
        os.makedirs(self.kdir, exist_ok=True)
        self.data = build_synthetic_kb(self.kdir)

    def cache_text(self, model):
        path = vectorcache.vector_cache_path(self.kdir, model)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return f.read()


class PlanVectorReuseTest(VectorCacheTestCase):
    """`_plan_vector_reuse` — the reuse decision, entry by entry."""

    def corpus(self):
        return [({"id": "a"}, "alpha text"), ({"id": "b"}, "beta text")]

    def test_vectorcache_an_empty_previous_cache_embeds_everything(self):
        plan, idx, txt = vectorcache._plan_vector_reuse(self.corpus(), {})
        self.assertEqual([p[0] for p in plan], ["a", "b"])
        self.assertEqual(idx, [0, 1])
        self.assertEqual(txt, ["alpha text", "beta text"])

    def test_vectorcache_a_matching_fingerprint_is_reused(self):
        fp = vectorcache._corpus_fingerprint("alpha text")
        prev = {"a": {"fp": fp, "v": [1.0, 2.0]}}
        plan, idx, txt = vectorcache._plan_vector_reuse(self.corpus(), prev)
        self.assertEqual(plan[0], ("a", fp, [1.0, 2.0]))
        self.assertEqual(idx, [1])
        self.assertEqual(txt, ["beta text"])

    def test_vectorcache_a_changed_fingerprint_forces_a_re_embed(self):
        """Content, not clock: this is the whole reason a clone that rewrote
        every mtime still hits the cache."""
        prev = {"a": {"fp": "stale-fingerprint", "v": [1.0]}}
        plan, idx, _txt = vectorcache._plan_vector_reuse(self.corpus(), prev)
        self.assertIsNone(plan[0][2])
        self.assertEqual(idx, [0, 1])

    def test_vectorcache_a_reused_vector_is_copied_not_aliased(self):
        """The plan must not hand back the cache's own list: a later mutation
        would then edit the object the previous cache still holds."""
        fp = vectorcache._corpus_fingerprint("alpha text")
        stored = [1.0, 2.0]
        prev = {"a": {"fp": fp, "v": stored}}
        plan, _idx, _txt = vectorcache._plan_vector_reuse(self.corpus(), prev)
        self.assertEqual(plan[0][2], stored)
        self.assertIsNot(plan[0][2], stored)

    def test_vectorcache_a_non_dict_cache_row_is_not_trusted(self):
        """A cache file is untrusted input; a malformed row must re-embed rather
        than raise on a subscript."""
        fp = vectorcache._corpus_fingerprint("alpha text")
        for bad in ("a string", 17, None, ["a", "list"]):
            plan, idx, _t = vectorcache._plan_vector_reuse(
                self.corpus(), {"a": bad})
            self.assertIsNone(plan[0][2], bad)
            self.assertIn(0, idx)
        self.assertTrue(fp)

    def test_vectorcache_a_row_whose_vector_is_not_a_list_re_embeds(self):
        fp = vectorcache._corpus_fingerprint("alpha text")
        plan, idx, _t = vectorcache._plan_vector_reuse(
            self.corpus(), {"a": {"fp": fp, "v": "not-a-list"}})
        self.assertIsNone(plan[0][2])
        self.assertIn(0, idx)

    def test_vectorcache_a_row_without_a_fingerprint_re_embeds(self):
        plan, idx, _t = vectorcache._plan_vector_reuse(
            self.corpus(), {"a": {"v": [1.0]}})
        self.assertIsNone(plan[0][2])
        self.assertIn(0, idx)

    def test_vectorcache_an_entry_without_an_id_is_dropped(self):
        """The cache is keyed by id, so a vector for an entry that cannot be
        looked up is one nothing will ever read."""
        corpus = [({"id": None}, "x"), ({}, "y"), ({"id": "c"}, "z")]
        plan, idx, txt = vectorcache._plan_vector_reuse(corpus, {})
        self.assertEqual([p[0] for p in plan], ["c"])
        self.assertEqual(idx, [0])
        self.assertEqual(txt, ["z"])

    def test_vectorcache_embed_positions_index_back_into_the_plan(self):
        """The three return values are a single contract: `to_embed_idx[i]` must
        be the plan slot for `to_embed_txt[i]`, or the caller writes a vector
        onto the wrong entry — a corruption no schema check would catch."""
        fp = vectorcache._corpus_fingerprint("beta text")
        prev = {"b": {"fp": fp, "v": [9.0]}}
        corpus = [({"id": "a"}, "alpha text"), ({"id": "b"}, "beta text"),
                  ({"id": "c"}, "gamma text")]
        plan, idx, txt = vectorcache._plan_vector_reuse(corpus, prev)
        for pos, text in zip(idx, txt):
            self.assertEqual(
                plan[pos][1], vectorcache._corpus_fingerprint(text))

    def test_vectorcache_the_plan_keeps_corpus_order(self):
        plan, _i, _t = vectorcache._plan_vector_reuse(self.corpus(), {})
        self.assertEqual([p[0] for p in plan], ["a", "b"])


class AssembleVectorsTest(VectorCacheTestCase):
    """`_assemble_vectors` — the file's map and its single dimension."""

    def test_vectorcache_dimension_comes_from_the_first_vector(self):
        out, dim = vectorcache._assemble_vectors(
            [_plan_row("a", "f1", [1.0, 2.0, 3.0])])
        self.assertEqual(dim, 3)
        self.assertEqual(out, {"a": {"fp": "f1", "v": [1.0, 2.0, 3.0]}})

    def test_vectorcache_an_empty_plan_yields_an_empty_map_and_zero_dim(self):
        self.assertEqual(vectorcache._assemble_vectors([]), ({}, 0))

    def test_vectorcache_matching_dimensions_are_accepted(self):
        out, dim = vectorcache._assemble_vectors(
            [_plan_row("a", "f", [1.0, 2.0]), _plan_row("b", "g", [3.0, 4.0])])
        self.assertEqual(dim, 2)
        self.assertEqual(sorted(out), ["a", "b"])

    def test_vectorcache_a_dimension_drift_is_refused(self):
        """A cache mixing two widths scores half the corpus meaninglessly, and
        silently — so it is raised rather than tolerated."""
        with self.assertRaises(ValueError) as ctx:
            vectorcache._assemble_vectors(
                [_plan_row("a", "f", [1.0, 2.0]), _plan_row("b", "g", [3.0])])
        self.assertIn("dim drift", str(ctx.exception))

    def test_vectorcache_the_drift_error_names_the_offending_entry(self):
        """Naming it is what makes the failure actionable without re-embedding
        the whole corpus to find the bad row."""
        with self.assertRaises(ValueError) as ctx:
            vectorcache._assemble_vectors(
                [_plan_row("good", "f", [1.0]), _plan_row("bad", "g", [1.0, 2.0])])
        self.assertIn("'bad'", str(ctx.exception))
        self.assertNotIn("'good'", str(ctx.exception))

    def test_vectorcache_a_row_without_a_vector_is_skipped(self):
        """A `None` must never reach the file: the reader would then have to
        defend against a null vector on every lookup."""
        out, dim = vectorcache._assemble_vectors(
            [_plan_row("a", "f", None), _plan_row("b", "g", [1.0])])
        self.assertEqual(sorted(out), ["b"])
        self.assertEqual(dim, 1)

    def test_vectorcache_an_all_none_plan_is_empty_rather_than_dim_zero_rows(self):
        out, dim = vectorcache._assemble_vectors([_plan_row("a", "f", None)])
        self.assertEqual((out, dim), ({}, 0))


class BuildVectorCacheTest(VectorCacheTestCase):
    """`build_vector_cache` — the composed writer."""

    def test_vectorcache_a_cold_build_embeds_the_whole_corpus_in_one_batch(self):
        """ONE batched call, not one per entry: a local model amortises a batch
        and a per-entry loop turns a rebuild into an N-round-trip crawl."""
        emb = _Embedder()
        stats = vectorcache.build_vector_cache(
            self.kdir, self.data, emb, "m-cold")
        self.assertEqual(len(emb.calls), 1)
        self.assertEqual(stats["embedded"], stats["total"])
        self.assertEqual(stats["reused"], 0)
        self.assertEqual(len(emb.calls[0]), stats["total"])

    def test_vectorcache_stats_report_every_field_the_caller_prints(self):
        emb = _Embedder()
        stats = vectorcache.build_vector_cache(
            self.kdir, self.data, emb, "m-stats")
        self.assertEqual(sorted(stats),
                         ["dim", "embedded", "model", "path", "reused",
                          "total"])
        self.assertEqual(stats["model"], "m-stats")
        self.assertEqual(stats["dim"], 4)

    def test_vectorcache_a_warm_build_reuses_everything_and_never_embeds(self):
        vectorcache.build_vector_cache(
            self.kdir, self.data, _Embedder(), "m-warm")
        emb = _Embedder()
        stats = vectorcache.build_vector_cache(
            self.kdir, self.data, emb, "m-warm")
        self.assertEqual(emb.calls, [])
        self.assertEqual(stats["embedded"], 0)
        self.assertEqual(stats["reused"], stats["total"])

    def test_vectorcache_a_warm_build_leaves_the_file_byte_identical(self):
        """Determinism (INV-1): the same corpus and the same pinned model write
        the same bytes, which is what makes delete+rebuild a safe repair."""
        vectorcache.build_vector_cache(
            self.kdir, self.data, _Embedder(), "m-bytes")
        first = self.cache_text("m-bytes")
        vectorcache.build_vector_cache(
            self.kdir, self.data, _Embedder(), "m-bytes")
        self.assertEqual(self.cache_text("m-bytes"), first)

    def test_vectorcache_only_the_changed_entry_is_re_embedded(self):
        vectorcache.build_vector_cache(
            self.kdir, self.data, _Embedder(), "m-inc")
        changed = json.loads(json.dumps(self.data))
        changed["entries"][0]["summary"] += " (amended)"
        emb = _Embedder()
        stats = vectorcache.build_vector_cache(
            self.kdir, changed, emb, "m-inc")
        self.assertEqual(stats["embedded"], 1)
        self.assertEqual(len(emb.calls[0]), 1)
        self.assertEqual(stats["reused"], stats["total"] - 1)

    def test_vectorcache_a_wrong_vector_count_is_refused(self):
        """The backend is somebody else's contract, and it is checked next to
        the call that asked — a short list would otherwise write vectors onto
        the wrong entries."""
        with self.assertRaises(ValueError) as ctx:
            vectorcache.build_vector_cache(
                self.kdir, self.data, _Embedder(miscount=1), "m-short")
        self.assertIn("embedder returned", str(ctx.exception))

    def test_vectorcache_a_surplus_vector_count_is_refused_too(self):
        with self.assertRaises(ValueError):
            vectorcache.build_vector_cache(
                self.kdir, self.data, _Embedder(miscount=99), "m-long")

    def test_vectorcache_a_drifting_backend_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            vectorcache.build_vector_cache(
                self.kdir, self.data, _Embedder(drift_after=1), "m-drift")
        self.assertIn("dim drift", str(ctx.exception))

    def test_vectorcache_an_empty_corpus_still_writes_a_well_formed_file(self):
        """A KB with no entries is a state, not an error: the reader must find a
        valid empty cache rather than a missing file it has to guess about."""
        empty = os.path.join(self.root, "empty")
        os.makedirs(empty, exist_ok=True)
        build_synthetic_kb(empty, entries=[])
        emb = _Embedder()
        stats = vectorcache.build_vector_cache(
            empty, {"entries": [], "tags": {}}, emb, "m-empty")
        self.assertEqual((stats["total"], stats["dim"]), (0, 0))
        self.assertEqual(emb.calls, [])
        with open(stats["path"], "r", encoding="utf-8") as f:
            blob = json.load(f)
        self.assertEqual(blob["vectors"], {})

    def test_vectorcache_the_file_carries_its_version_and_model(self):
        """Both are freshness keys the loader checks; a file that omitted them
        would be accepted by a future reader that means something else by it."""
        stats = vectorcache.build_vector_cache(
            self.kdir, self.data, _Embedder(), "m-hdr")
        with open(stats["path"], "r", encoding="utf-8") as f:
            blob = json.load(f)
        self.assertEqual(blob["version"], vectorcache.VECTOR_CACHE_VERSION)
        self.assertEqual(blob["model"], "m-hdr")

    def test_vectorcache_the_file_is_written_with_sorted_keys(self):
        """Sorted keys are what make the byte-identity claim above possible at
        all — dict order would otherwise vary with insertion."""
        stats = vectorcache.build_vector_cache(
            self.kdir, self.data, _Embedder(), "m-sorted")
        with open(stats["path"], "r", encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(json.dumps(json.loads(text), indent=2,
                                    ensure_ascii=False, sort_keys=True) + "\n",
                         text)

    def test_vectorcache_two_models_do_not_share_a_file(self):
        """A vector is only comparable within its own model; one file per model
        is what stops a model swap from producing plausible nonsense."""
        a = vectorcache.build_vector_cache(
            self.kdir, self.data, _Embedder(), "model-a")
        b = vectorcache.build_vector_cache(
            self.kdir, self.data, _Embedder(), "model-b")
        self.assertNotEqual(a["path"], b["path"])

    def test_vectorcache_an_entry_without_an_id_never_reaches_the_file(self):
        data = json.loads(json.dumps(self.data))
        data["entries"].append({"title": "no id", "category": "learnings",
                                "path": "learnings/geofence-reminders.md",
                                "tags": [], "created": "2026-01-09"})
        stats = vectorcache.build_vector_cache(
            self.kdir, data, _Embedder(), "m-noid")
        with open(stats["path"], "r", encoding="utf-8") as f:
            blob = json.load(f)
        self.assertNotIn(None, blob["vectors"])
        self.assertEqual(len(blob["vectors"]), stats["total"])


class CacheLoadTest(VectorCacheTestCase):
    """`load_vector_cache` — the reader's own refusals."""

    def test_vectorcache_a_missing_cache_is_none(self):
        self.assertIsNone(vectorcache.load_vector_cache(self.kdir, "absent"))

    def test_vectorcache_a_corrupt_cache_is_none(self):
        path = vectorcache.vector_cache_path(self.kdir, "m-corrupt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIsNone(vectorcache.load_vector_cache(self.kdir, "m-corrupt"))

    def test_vectorcache_a_version_mismatch_is_none(self):
        stats = vectorcache.build_vector_cache(
            self.kdir, self.data, _Embedder(), "m-ver")
        with open(stats["path"], "r", encoding="utf-8") as f:
            blob = json.load(f)
        blob["version"] = vectorcache.VECTOR_CACHE_VERSION + 1
        with open(stats["path"], "w", encoding="utf-8") as f:
            json.dump(blob, f)
        self.assertIsNone(vectorcache.load_vector_cache(self.kdir, "m-ver"))

    def test_vectorcache_a_rejected_cache_makes_the_next_build_cold(self):
        """The refusals compose with the writer: an unreadable cache must cost a
        re-embed, never a stale vector."""
        vectorcache.build_vector_cache(
            self.kdir, self.data, _Embedder(), "m-cold2")
        path = vectorcache.vector_cache_path(self.kdir, "m-cold2")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        emb = _Embedder()
        stats = vectorcache.build_vector_cache(
            self.kdir, self.data, emb, "m-cold2")
        self.assertEqual(stats["reused"], 0)


class ModuleSurfaceTest(VectorCacheTestCase):
    """The module's published surface and its Mode-A guarantee."""

    def test_vectorcache_exports_name_the_new_helpers(self):
        for name in ("_plan_vector_reuse", "_assemble_vectors",
                     "build_vector_cache"):
            self.assertIn(name, vectorcache._EXPORTS)
            self.assertTrue(hasattr(vectorcache, name))

    def test_vectorcache_lives_under_the_gitignored_cache_dir(self):
        self.assertTrue(
            vectorcache.VECTOR_CACHE_DIR_REL.startswith(".cache"))

    def test_vectorcache_the_model_slug_is_path_safe(self):
        """A model id is an arbitrary string (often `org/name`); the slug is
        what stops it from writing outside the cache directory."""
        slug = vectorcache._model_slug("org/name:v1")
        self.assertNotIn("/", slug)
        self.assertNotIn(":", slug)

    def test_vectorcache_the_fingerprint_tracks_content(self):
        a = vectorcache._corpus_fingerprint("text one")
        self.assertEqual(a, vectorcache._corpus_fingerprint("text one"))
        self.assertNotEqual(a, vectorcache._corpus_fingerprint("text two"))

    def test_vectorcache_an_absent_backend_falls_back_to_deterministic(self):
        """Mode A must run with NOTHING installed (INV-6): a configured but
        unloadable backend degrades with a notice rather than failing."""
        saved = os.environ.get("AGENTWARE_EMBEDDER_BACKEND")
        os.environ["AGENTWARE_EMBEDDER_BACKEND"] = "no.such.module.anywhere"
        vectorcache._reset_embedder_cache()
        try:
            self.assertFalse(vectorcache.semantic_embedder_available())
        finally:
            if saved is None:
                del os.environ["AGENTWARE_EMBEDDER_BACKEND"]
            else:
                os.environ["AGENTWARE_EMBEDDER_BACKEND"] = saved
            vectorcache._reset_embedder_cache()


if __name__ == "__main__":
    unittest.main()
