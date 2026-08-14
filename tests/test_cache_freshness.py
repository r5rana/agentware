"""The CACHE-FRESHNESS suite (plan task 24).

Two claims, kept apart so a green result can never be mistaken for the wrong one:

  1. POSITIVE — the tree under measurement keys its derived BM25 postings cache
     on CONTENT, couples the drop-on-write guard to the O(1) fast path, keeps the
     three freshness tiers in cheap-to-expensive order, and carries a cache
     version NEITHER pre-merge tree used.
  2. REFUTATION — each of those is broken, one at a time, against a synthetic
     mini-tree, and the gate must NAME the specific failure. A gate nobody has
     ever seen fail is a gate nobody knows works, and this whole class of defect
     is silent: a cache wrongly believed fresh raises nothing, warns nothing and
     changes no exit code — `recall` simply answers from yesterday's corpus.

The gate rides with the migration: before the modular core lands there is no
`agentware_lib` to measure, so the positive half is inert rather than failing.
The refutation half always runs — it builds its own tree and depends on nothing
in this checkout. The end-to-end behavioural proof (a pulled entry invalidating
the cache, a foreign-version cache degrading to the live scan, zero body reads on
both cheap tiers) lives with the core, in
tests/integration/test_cache_freshness.py; this file proves the STATIC gate that
keeps those properties true for every future change.

Stdlib only, deterministic, hermetic (`ast` over source text; no import of the
code under test, no subprocess, no network).
"""

import contextlib
import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests import _cachefreshness_gate as cfg
except ImportError:  # allow `python3 -m unittest tests.test_cache_freshness`
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _cachefreshness_gate as cfg

_NO_CORE = not cfg.lib_present()
_NO_CORE_REASON = "scripts/agentware_lib is not present in this tree yet"


# --- a minimal COMPLIANT synthetic tree ---------------------------------------
# Deliberately the smallest thing that satisfies every check: each refutation
# below then breaks exactly one property, so a red result is attributable.
_BM25 = '''\
import hashlib
import json
import os
import tempfile

from agentware_lib.retrieval import corpus as _corpus

BM25_CACHE_VERSION = 3


def _corpus_stat_signature(kdir, data):
    h = hashlib.sha256()
    for entry in data.get("entries") or []:
        st = os.stat(os.path.join(kdir, entry.get("path")))
        h.update(("%d:%d" % (st.st_size, st.st_mtime_ns)).encode("utf-8"))
    return h.hexdigest()


def build_bm25_cache(kdir, data):
    corpus = _corpus.build_corpus(kdir, data)
    return {
        "version": BM25_CACHE_VERSION,
        "fingerprint": _corpus.eval_corpus_fingerprint(corpus),
        "stat_signature": _corpus_stat_signature(kdir, data),
    }


def write_bm25_cache(kdir, cache):
    path = os.path.join(kdir, ".cache", "bm25-postings.json")
    fd, tmp = tempfile.mkstemp(prefix=".bm25.", dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cache, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def load_bm25_cache(kdir):
    path = os.path.join(kdir, ".cache", "bm25-postings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        return None
    if cache.get("version") != BM25_CACHE_VERSION:
        return None
    for key in ("fingerprint", "stat_signature"):
        if key not in cache:
            return None
    return cache


def bm25_cache_is_fresh(cache, data, kdir):
    cache_fp = cache.get("fingerprint")
    stored_fp = data.get("corpus_fingerprint")
    if stored_fp:
        return cache_fp == stored_fp
    if cache.get("stat_signature") == _corpus_stat_signature(kdir, data):
        return True
    live = _corpus.eval_corpus_fingerprint(_corpus.build_corpus(kdir, data))
    return cache_fp == live
'''

_INDEXWRITE = '''\
import json
import os
import tempfile

from agentware_lib.knowledge.index import indexread


def save_index(kdir, data, preserve_fingerprint=False):
    if not preserve_fingerprint and "corpus_fingerprint" in data:
        data = {k: v for k, v in data.items() if k != "corpus_fingerprint"}
    path = indexread.index_path(kdir)
    fd, tmp = tempfile.mkstemp(prefix=".index.", dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
'''

_INDEXREBUILD = '''\
from agentware_lib.knowledge.index import indexwrite
from agentware_lib.retrieval.bm25 import build_bm25_cache, write_bm25_cache
from agentware_lib.retrieval.corpus import build_corpus, eval_corpus_fingerprint


def rebuild_kb(kdir, data):
    data["corpus_fingerprint"] = eval_corpus_fingerprint(build_corpus(kdir, data))
    indexwrite.save_index(kdir, data, preserve_fingerprint=True)
    write_bm25_cache(kdir, build_bm25_cache(kdir, data))
'''

_INDEXREAD = '''\
import os


def index_path(kdir):
    return os.path.join(kdir, "index.json")
'''

_RECALLCMD = '''\
from agentware_lib.retrieval import bm25


def cmd_recall(kdir, data, query):
    cache = bm25.load_bm25_cache(kdir)
    if cache is not None and bm25.bm25_cache_is_fresh(cache, data, kdir):
        return cache
    return None
'''

_FILES = {
    "retrieval/bm25.py": _BM25,
    "retrieval/recallcmd.py": _RECALLCMD,
    "knowledge/index/indexread.py": _INDEXREAD,
    "knowledge/index/indexwrite.py": _INDEXWRITE,
    "knowledge/index/indexrebuild.py": _INDEXREBUILD,
}


class _SyntheticTree(object):
    """A throwaway tree the gate can measure."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="agentware-cachegate-")
        self.lib = os.path.join(self.root, "scripts", "agentware_lib")
        for rel, body in _FILES.items():
            self.write(rel, body)

    def write(self, rel, body):
        path = os.path.join(self.lib, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)

    def patch(self, rel, old, new):
        path = os.path.join(self.lib, *rel.split("/"))
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        assert old in src, "refutation needle not found in %s: %r" % (rel, old)
        with open(path, "w", encoding="utf-8") as f:
            f.write(src.replace(old, new))

    def remove(self, rel):
        os.unlink(os.path.join(self.lib, *rel.split("/")))

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


@contextlib.contextmanager
def _measuring(tree):
    """Point every root the gate derives its module paths from at `tree`."""
    saved = (cfg.REPO_ROOT, cfg.LIB_DIR, cfg.BM25_MODULE, cfg.WRITE_MODULE,
             cfg.REBUILD_MODULE)
    cfg.REPO_ROOT, cfg.LIB_DIR = tree.root, tree.lib
    cfg.BM25_MODULE = os.path.join(tree.lib, "retrieval", "bm25.py")
    cfg.WRITE_MODULE = os.path.join(tree.lib, "knowledge", "index",
                                    "indexwrite.py")
    cfg.REBUILD_MODULE = os.path.join(tree.lib, "knowledge", "index",
                                      "indexrebuild.py")
    try:
        yield
    finally:
        (cfg.REPO_ROOT, cfg.LIB_DIR, cfg.BM25_MODULE, cfg.WRITE_MODULE,
         cfg.REBUILD_MODULE) = saved


class _RefutationCase(unittest.TestCase):
    def setUp(self):
        self.tree = _SyntheticTree()
        self.addCleanup(self.tree.cleanup)

    def problems(self):
        with _measuring(self.tree):
            return cfg.problems()

    def assertNames(self, needle):
        found = self.problems()
        self.assertTrue(any(needle in p for p in found),
                        "no problem mentioned %r; got %r" % (needle, found))

    def assertGreen(self):
        self.assertEqual(self.problems(), [])


class SyntheticBaselineTest(_RefutationCase):
    """The unmodified synthetic tree must be GREEN, or every refutation below is
    proving nothing but its own scaffolding."""

    def test_the_compliant_tree_is_green(self):
        self.assertGreen()


class ForeignVersionRefutationTest(_RefutationCase):
    """Both pre-merge trees wrote to the SAME cache path under different schemas."""

    def test_reverting_to_the_donor_version_reddens(self):
        self.tree.patch("retrieval/bm25.py", "BM25_CACHE_VERSION = 3",
                        "BM25_CACHE_VERSION = 1")
        self.assertNames("RETIRED version")

    def test_reverting_to_this_trees_version_reddens(self):
        self.tree.patch("retrieval/bm25.py", "BM25_CACHE_VERSION = 3",
                        "BM25_CACHE_VERSION = 2")
        self.assertNames("RETIRED version")

    def test_a_non_integer_version_reddens(self):
        self.tree.patch("retrieval/bm25.py", "BM25_CACHE_VERSION = 3",
                        'BM25_CACHE_VERSION = "3"')
        self.assertNames("must be an int")

    def test_a_loader_that_stops_checking_the_version_reddens(self):
        self.tree.patch("retrieval/bm25.py",
                        '    if cache.get("version") != BM25_CACHE_VERSION:\n'
                        "        return None\n", "")
        self.assertNames("does not compare against BM25_CACHE_VERSION")


class TierRefutationTest(_RefutationCase):
    """Cheap before expensive, and every tier present."""

    def test_dropping_the_stat_short_circuit_reddens(self):
        self.tree.patch(
            "retrieval/bm25.py",
            '    if cache.get("stat_signature") == _corpus_stat_signature(kdir, data):\n'
            "        return True\n", "")
        self.assertNames("never calls _corpus_stat_signature")

    def test_dropping_the_hash_fallback_reddens(self):
        self.tree.patch(
            "retrieval/bm25.py",
            "    live = _corpus.eval_corpus_fingerprint(_corpus.build_corpus(kdir, data))\n"
            "    return cache_fp == live\n",
            "    return False\n")
        self.assertNames("never falls back to the whole-corpus hash")

    def test_hashing_before_the_stat_short_circuit_reddens(self):
        self.tree.patch(
            "retrieval/bm25.py",
            '    if cache.get("stat_signature") == _corpus_stat_signature(kdir, data):\n'
            "        return True\n"
            "    live = _corpus.eval_corpus_fingerprint(_corpus.build_corpus(kdir, data))\n"
            "    return cache_fp == live\n",
            "    live = _corpus.eval_corpus_fingerprint(_corpus.build_corpus(kdir, data))\n"
            '    if cache.get("stat_signature") == _corpus_stat_signature(kdir, data):\n'
            "        return True\n"
            "    return cache_fp == live\n")
        self.assertNames("before the cheaper stat-signature tier")

    def test_renaming_the_freshness_function_reddens_rather_than_passing_silently(self):
        self.tree.patch("retrieval/bm25.py", "def bm25_cache_is_fresh(",
                        "def bm25_cache_is_fresh_v2(")
        self.assertNames("bm25_cache_is_fresh is missing")


class AuthorityRefutationTest(_RefutationCase):
    """Content decides; the retired counter may not come back anywhere."""

    def test_a_counter_key_anywhere_in_the_lib_reddens(self):
        self.tree.patch(
            "retrieval/bm25.py",
            "    stored_fp = data.get(\"corpus_fingerprint\")\n",
            "    stored_fp = data.get(\"corpus_fingerprint\")\n"
            "    if cache.get(\"generation\") == data.get(\"generation\"):\n"
            "        return True\n")
        self.assertNames("RETIRED 'generation' index key")

    def test_a_counter_key_in_an_unrelated_module_reddens_too(self):
        self.tree.write("ops/somereport.py",
                        "def report(data):\n"
                        "    return data.get(\"generation\", 0)\n")
        self.assertNames("RETIRED 'generation' index key")

    def test_freshness_that_stops_reading_the_stored_fingerprint_reddens(self):
        self.tree.patch("retrieval/bm25.py",
                        '    stored_fp = data.get("corpus_fingerprint")\n',
                        "    stored_fp = None\n")
        self.assertNames("content is not the freshness authority")


class CouplingRefutationTest(_RefutationCase):
    """The fast path is only safe because every other write drops the key."""

    def test_removing_the_drop_guard_reddens(self):
        self.tree.patch(
            "knowledge/index/indexwrite.py",
            '    if not preserve_fingerprint and "corpus_fingerprint" in data:\n'
            '        data = {k: v for k, v in data.items() if k != "corpus_fingerprint"}\n',
            "")
        self.assertNames("never drops 'corpus_fingerprint'")

    def test_flipping_the_default_to_preserve_reddens(self):
        self.tree.patch("knowledge/index/indexwrite.py",
                        "preserve_fingerprint=False", "preserve_fingerprint=True")
        self.assertNames("must default to False")

    def test_a_second_preserving_caller_reddens(self):
        self.tree.write("features/somecmd.py",
                        "from agentware_lib.knowledge.index import indexwrite\n\n\n"
                        "def cmd_something(kdir, data):\n"
                        "    indexwrite.save_index(kdir, data, "
                        "preserve_fingerprint=True)\n")
        self.assertNames("sole-writer violated")

    def test_a_second_fingerprint_setter_reddens(self):
        self.tree.write("ops/someop.py",
                        "def stamp(data, fp):\n"
                        '    data["corpus_fingerprint"] = fp\n')
        self.assertNames("sole-writer violated")


class StatProxyRefutationTest(_RefutationCase):
    """The proxy must open nothing — that is the entire point of it."""

    def test_a_body_read_inside_the_proxy_reddens(self):
        self.tree.patch(
            "retrieval/bm25.py",
            "        st = os.stat(os.path.join(kdir, entry.get(\"path\")))\n",
            "        st = os.stat(os.path.join(kdir, entry.get(\"path\")))\n"
            "        with open(os.path.join(kdir, entry.get(\"path\"))) as fh:\n"
            "            h.update(fh.read().encode(\"utf-8\"))\n")
        self.assertNames("reads entry bodies")

    def test_a_proxy_that_never_stats_reddens(self):
        self.tree.patch(
            "retrieval/bm25.py",
            "        st = os.stat(os.path.join(kdir, entry.get(\"path\")))\n"
            "        h.update((\"%d:%d\" % (st.st_size, st.st_mtime_ns)).encode(\"utf-8\"))\n",
            "        h.update(str(entry).encode(\"utf-8\"))\n")
        self.assertNames("never calls os.stat")

    def test_a_missing_proxy_reddens(self):
        self.tree.patch("retrieval/bm25.py", "def _corpus_stat_signature(",
                        "def _unused_signature(")
        self.assertNames("_corpus_stat_signature is missing")


class CacheSchemaRefutationTest(_RefutationCase):
    """Stamped by the builder in the same call, and REQUIRED by the loader."""

    def test_a_builder_that_stops_stamping_the_signature_reddens(self):
        self.tree.patch("retrieval/bm25.py",
                        '        "stat_signature": _corpus_stat_signature(kdir, data),\n',
                        "")
        self.assertNames("does not stamp 'stat_signature'")

    def test_a_loader_that_stops_requiring_the_signature_reddens(self):
        self.tree.patch("retrieval/bm25.py",
                        '    for key in ("fingerprint", "stat_signature"):',
                        '    for key in ("fingerprint",):')
        self.assertNames("does not require 'stat_signature'")


class AtomicWriteRefutationTest(_RefutationCase):
    """A torn cache must never be readable as a fresh one."""

    def test_a_truncating_writer_reddens(self):
        self.tree.patch(
            "retrieval/bm25.py",
            '    fd, tmp = tempfile.mkstemp(prefix=".bm25.", dir=os.path.dirname(path))\n'
            '    with os.fdopen(fd, "w", encoding="utf-8") as f:\n'
            "        json.dump(cache, f)\n"
            "        f.flush()\n"
            "        os.fsync(f.fileno())\n"
            "    os.replace(tmp, path)\n",
            '    with open(path, "w", encoding="utf-8") as f:\n'
            "        json.dump(cache, f)\n")
        self.assertNames("does not call mkstemp()")

    def test_dropping_the_fsync_reddens(self):
        self.tree.patch("retrieval/bm25.py", "        os.fsync(f.fileno())\n", "")
        self.assertNames("does not call fsync()")


class VacuityRefutationTest(_RefutationCase):
    """A gate that passes by looking at nothing is worse than no gate."""

    def test_a_deleted_module_reddens(self):
        self.tree.remove("retrieval/bm25.py")
        self.assertNames("missing module")

    def test_a_deleted_write_module_reddens(self):
        self.tree.remove("knowledge/index/indexwrite.py")
        self.assertNames("missing module")


@unittest.skipIf(_NO_CORE, _NO_CORE_REASON)
class DestinationTest(unittest.TestCase):
    """The POSITIVE half: the real tree under measurement satisfies every check."""

    def test_the_tree_is_green(self):
        found = cfg.problems()
        self.assertEqual(found, [], "cache-freshness violations: %r" % (found,))

    def test_the_measurement_is_not_vacuous(self):
        # The three modules the checks read must actually exist, and the
        # tree-wide counter scan must have walked a real corpus of files.
        for path in (cfg.BM25_MODULE, cfg.WRITE_MODULE, cfg.REBUILD_MODULE):
            self.assertTrue(os.path.exists(path), path)
        self.assertGreater(len(cfg.lib_files()), 20)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
