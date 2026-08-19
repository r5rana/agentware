"""The INDEX WRITE-SAFETY suite (plan task 22).

Two claims, deliberately separated so a green result can never be mistaken for the
wrong one:

  1. POSITIVE — the tree under measurement satisfies every write-safety invariant:
     an atomic + durable writer, a reentrant cross-process lock, that lock actually
     WIRED around every read-modify-write, auto-rebuild-once recovery, and a
     fault-isolating rebuild.
  2. REFUTATION — each invariant is broken, one at a time, against a synthetic
     mini-tree, and the gate must name the specific failure. A gate nobody has ever
     seen fail is a gate nobody knows works, and this whole class of defect is
     silent: an unlocked mutation loses an entry, and NOTHING turns red anywhere
     else in the suite. The concurrency probe that proves it empirically lives with
     the core (`tests/integration/test_index_write_safety.py`); this file proves the
     STATIC gate that keeps it true for every future change.

The gate rides with the migration: before the modular core lands there is no
`agentware_lib` to measure, so the positive half is inert rather than failing. The
refutation half always runs — it builds its own tree and depends on nothing in this
checkout.

Stdlib only, deterministic, hermetic (`ast` over source text; no import of the code
under test, no subprocess, no network).
"""

from tests._paths import TESTS_DIR

import importlib
import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests import _indexsafety_gate as isg
except ImportError:  # allow `python3 -m unittest tests.unit.knowledge.test_index_write_safety`
    sys.path.insert(0, TESTS_DIR)
    import _indexsafety_gate as isg

_ENV = "AGENTWARE_INDEXSAFETY_ROOT"
_NO_CORE = not isg.lib_present()
_NO_CORE_REASON = "scripts/agentware_lib is not present in this tree yet"


# --- a minimal COMPLIANT synthetic tree, one file per invariant ---------------
_KBLAYOUT = '''\
INDEX_REL = "index.json"
KB_INDEX_LOCK_REL = ".index.lock"
KB_GITIGNORE_DIR_RULES = ("logs",)
KB_GITIGNORE_REQUIRED = KB_GITIGNORE_DIR_RULES + (KB_INDEX_LOCK_REL,)
'''

_INDEXLOCK = '''\
import contextlib
import os
import threading

from agentware_lib.core.kblayout import KB_INDEX_LOCK_REL

try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None

_INDEX_LOCK_GUARD = threading.RLock()
_INDEX_LOCK_STATE = {}


def _index_lock_path(kdir):
    return os.path.abspath(os.path.join(kdir, KB_INDEX_LOCK_REL))


def _lock_state(lockpath):
    with _INDEX_LOCK_GUARD:
        st = _INDEX_LOCK_STATE.get(lockpath)
        if st is None:
            st = {"rlock": threading.RLock(), "file": None, "depth": 0}
            _INDEX_LOCK_STATE[lockpath] = st
        return st


@contextlib.contextmanager
def _index_lock(kdir):
    lockpath = _index_lock_path(kdir)
    st = _lock_state(lockpath)
    st["rlock"].acquire()
    try:
        if st["depth"] == 0:
            f = open(lockpath, "a+")
            _fcntl.flock(f.fileno(), _fcntl.LOCK_EX)
            st["file"] = f
        st["depth"] += 1
        try:
            yield
        finally:
            st["depth"] -= 1
            if st["depth"] == 0:
                _fcntl.flock(st["file"].fileno(), _fcntl.LOCK_UN)
                st["file"] = None
    finally:
        st["rlock"].release()
'''

_INDEXWRITE = '''\
import json
import os
import tempfile


def save_index(kdir, data, preserve_fingerprint=False):
    path = os.path.join(kdir, "index.json")
    fd, tmp = tempfile.mkstemp(prefix=".index.", suffix=".tmp", dir=kdir)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
'''

_INDEXREAD = '''\
import json
import os


def _try_rebuild_missing_index(kdir):
    import agentware_lib.knowledge.index.indexrebuild as indexrebuild
    try:
        data, _r, errors = indexrebuild.rebuild_kb(kdir)
    except Exception:
        return False
    return not errors and data is not None


def load_index(kdir, _allow_rebuild=True):
    path = os.path.join(kdir, "index.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        if _allow_rebuild and _try_rebuild_missing_index(kdir):
            return load_index(kdir, _allow_rebuild=False)
        return None, "index file not found: %s" % path
'''

_INDEXREBUILD = '''\
import sys

import agentware_lib.knowledge.index.indexlock as indexlock
import agentware_lib.knowledge.index.indexwrite as indexwrite


def build_index_from_frontmatter(kdir):
    entries, problems = [], []
    seen_ids = {}
    for rel in []:
        if rel in seen_ids:
            problems.append("duplicate id '%s'" % rel)
            continue
    return {"entries": entries, "tags": {}}, problems


def rebuild_kb(kdir):
    with indexlock._index_lock(kdir):
        return _rebuild_kb_locked(kdir)


def _rebuild_kb_locked(kdir):
    data, quarantined = build_index_from_frontmatter(kdir)
    for msg in quarantined:
        sys.stderr.write("QUARANTINED: %s\\n" % msg)
    indexwrite.save_index(kdir, data, preserve_fingerprint=True)
    return data, [], []
'''

_READONLY = '''\
import agentware_lib.knowledge.index as index


def attach_conformance_report(path):
    data, err = index.load_index(path, _allow_rebuild=False)
    return data, err
'''

_FEATURES = '''\
import agentware_lib.knowledge.index as index


def cmd_features(args):
    kdir = "/tmp/kb"
    data, err = index.load_index(kdir, _allow_rebuild=False)
    return 1 if err else 0
'''

_FLEETREPORT = '''\
import agentware_lib.knowledge.index as index


def _fleet_top_learning_tags(kdir, team, limit=15):
    data = {}
    if kdir:
        loaded, _err = index.load_index(kdir, _allow_rebuild=False)
        if isinstance(loaded, dict):
            data = loaded
    return sorted(data.get("entries") or [])[:limit]
'''

_HANDLER = '''\
import agentware_lib.knowledge.index as index


def cmd_thing(args):
    kdir = "/tmp/kb"
    with index._index_lock(kdir):
        data, err = index.load_index(kdir)
        if err is not None:
            return 1
        data["entries"].append({})
        index.save_index(kdir, data)
    return 0


def cmd_seed(args):
    kdir = "/tmp/kb"
    index.save_index(kdir, {"entries": [], "tags": {}})
    return 0
'''

_FILES = {
    "core/kblayout.py": _KBLAYOUT,
    "knowledge/index/indexlock.py": _INDEXLOCK,
    "knowledge/index/indexwrite.py": _INDEXWRITE,
    "knowledge/index/indexread.py": _INDEXREAD,
    "knowledge/index/indexrebuild.py": _INDEXREBUILD,
    "features/handler.py": _HANDLER,
    "knowledge/attach.py": _READONLY,
    "knowledge/curation/curationfeatures.py": _FEATURES,
    "ops/fleet/fleetreport.py": _FLEETREPORT,
}


class _SyntheticTree(object):
    """A throwaway lib tree the gate can be pointed at, plus a reloaded gate."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="aw-indexsafety-")
        self.lib = os.path.join(self.root, "scripts", "agentware_lib")
        for rel, src in _FILES.items():
            path = os.path.join(self.lib, rel)
            d = os.path.dirname(path)
            if not os.path.isdir(d):
                os.makedirs(d)
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
        self._saved_env = os.environ.get(_ENV)

    def edit(self, rel, old, new, count=1):
        path = os.path.join(self.lib, rel)
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        assert old in src, "anchor not found in %s: %r" % (rel, old)
        with open(path, "w", encoding="utf-8") as f:
            f.write(src.replace(old, new, count))

    def remove(self, rel):
        os.remove(os.path.join(self.lib, rel))

    def errors(self):
        os.environ[_ENV] = self.root
        try:
            gate = importlib.reload(isg)
            return gate.all_errors()
        finally:
            if self._saved_env is None:
                os.environ.pop(_ENV, None)
            else:
                os.environ[_ENV] = self._saved_env
            importlib.reload(isg)

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class _RefutationCase(unittest.TestCase):
    """Base: build a compliant tree, break ONE thing, assert the gate names it."""

    def setUp(self):
        self.tree = _SyntheticTree()
        self.addCleanup(self.tree.close)

    def assertGreen(self):
        errs = self.tree.errors()
        self.assertEqual(errs, [], "the compliant synthetic tree is not green")

    def assertNamed(self, needle):
        errs = self.tree.errors()
        self.assertTrue(errs, "the gate stayed GREEN after the invariant was broken")
        joined = "\n".join(errs)
        self.assertIn(needle, joined,
                      "the gate reddened but never named %r:\n%s" % (needle, joined))


class SyntheticBaselineTest(_RefutationCase):
    """The control. Without it, every refutation below could pass vacuously."""

    def test_the_compliant_tree_is_green(self):
        self.assertGreen()


class LockPrimitiveRefutationTest(_RefutationCase):

    def test_a_missing_lock_module_reddens(self):
        self.tree.remove("knowledge/index/indexlock.py")
        self.assertNamed("indexlock.py")

    def test_dropping_the_rlock_reddens(self):
        # flock is per-open-file-description: two THREADS in one process on two
        # fds both hold it. Without the RLock this passes every cross-process
        # test and still loses updates in the workflow harness.
        self.tree.edit("knowledge/index/indexlock.py",
                       "_INDEX_LOCK_GUARD = threading.RLock()",
                       "_INDEX_LOCK_GUARD = None")
        self.tree.edit("knowledge/index/indexlock.py",
                       '"rlock": threading.RLock(), ', '')
        self.assertNamed("threading.RLock")

    def test_dropping_the_depth_counter_reddens(self):
        # Reentrancy: load_index's auto-rebuild calls rebuild_kb, itself locked.
        # A second LOCK_EX on a new fd blocks the process on itself, forever.
        self.tree.edit("knowledge/index/indexlock.py", '"depth"', '"dpth"', count=99)
        self.assertNamed("depth counter")

    def test_an_unguarded_fcntl_import_reddens(self):
        self.tree.edit("knowledge/index/indexlock.py",
                       "try:\n    import fcntl as _fcntl\nexcept ImportError:\n"
                       "    _fcntl = None",
                       "import fcntl as _fcntl")
        self.assertNamed("fcntl import is not guarded")

    def test_losing_the_context_manager_reddens(self):
        self.tree.edit("knowledge/index/indexlock.py",
                       "@contextlib.contextmanager\n", "")
        self.assertNamed("not a context manager")

    def test_losing_the_finally_reddens(self):
        # A lock released only on the happy path hangs the NEXT writer forever.
        self.tree.edit("knowledge/index/indexlock.py",
                       '    st["rlock"].acquire()\n    try:\n',
                       '    st["rlock"].acquire()\n    if True:\n')
        self.tree.edit("knowledge/index/indexlock.py",
                       "    finally:\n        st[\"rlock\"].release()\n",
                       "    st[\"rlock\"].release()\n")
        self.tree.edit("knowledge/index/indexlock.py",
                       "        try:\n            yield\n        finally:\n",
                       "        if True:\n            yield\n        if True:\n")
        self.assertNamed("no finally")


class AtomicWriteRefutationTest(_RefutationCase):

    def test_writing_through_the_destination_reddens(self):
        self.tree.edit(
            "knowledge/index/indexwrite.py",
            '    fd, tmp = tempfile.mkstemp(prefix=".index.", suffix=".tmp", dir=kdir)\n'
            '    with os.fdopen(fd, "w", encoding="utf-8") as f:\n',
            '    with open(path, "w", encoding="utf-8") as f:\n')
        self.assertNamed("opens a path for writing directly")

    def test_dropping_fsync_reddens(self):
        self.tree.edit("knowledge/index/indexwrite.py",
                       "os.fsync(f.fileno())", "pass")
        self.assertNamed("os.fsync is not reached")

    def test_dropping_the_atomic_replace_reddens(self):
        self.tree.edit("knowledge/index/indexwrite.py",
                       "os.replace(tmp, path)", "shutil_move(tmp, path)")
        self.assertNamed("os.replace is not reached")

    def test_the_primitives_may_live_in_a_helper(self):
        # A decomposition must not disarm the gate — the task-21 failure mode.
        self.tree.edit(
            "knowledge/index/indexwrite.py",
            "    fd, tmp = tempfile.mkstemp(prefix=\".index.\", suffix=\".tmp\", dir=kdir)\n"
            "    with os.fdopen(fd, \"w\", encoding=\"utf-8\") as f:\n"
            "        json.dump(data, f, indent=2)\n"
            "        f.flush()\n"
            "        os.fsync(f.fileno())\n"
            "    os.replace(tmp, path)\n",
            "    _stage_and_replace(kdir, path, data)\n\n\n"
            "def _stage_and_replace(kdir, path, data):\n"
            "    fd, tmp = tempfile.mkstemp(prefix=\".index.\", suffix=\".tmp\", dir=kdir)\n"
            "    with os.fdopen(fd, \"w\", encoding=\"utf-8\") as f:\n"
            "        json.dump(data, f, indent=2)\n"
            "        f.flush()\n"
            "        os.fsync(f.fileno())\n"
            "    os.replace(tmp, path)\n")
        self.assertGreen()


class RecoveryRefutationTest(_RefutationCase):

    def test_dropping_the_rebuild_attempt_reddens(self):
        # A fresh clone carries no derived index. Without this it is bricked on
        # its very first command.
        self.tree.edit("knowledge/index/indexread.py",
                       "if _allow_rebuild and _try_rebuild_missing_index(kdir):",
                       "if False:")
        self.assertNamed("never attempts a rebuild")

    def test_dropping_the_single_attempt_guard_reddens(self):
        self.tree.edit("knowledge/index/indexread.py",
                       "def load_index(kdir, _allow_rebuild=True):",
                       "def load_index(kdir):")
        self.tree.edit("knowledge/index/indexread.py",
                       "if _allow_rebuild and _try_rebuild_missing_index(kdir):",
                       "if _try_rebuild_missing_index(kdir):")
        self.tree.edit("knowledge/index/indexread.py",
                       "return load_index(kdir, _allow_rebuild=False)",
                       "return load_index(kdir)")
        self.assertNamed("_allow_rebuild")

    def test_a_recovery_that_can_crash_the_caller_reddens(self):
        self.tree.edit("knowledge/index/indexread.py",
                       "    try:\n        data, _r, errors = indexrebuild.rebuild_kb(kdir)\n"
                       "    except Exception:\n        return False\n",
                       "    data, _r, errors = indexrebuild.rebuild_kb(kdir)\n")
        self.assertNamed("must never crash the caller")


class QuarantineRefutationTest(_RefutationCase):

    def test_dropping_the_duplicate_id_guard_reddens(self):
        self.tree.edit("knowledge/index/indexrebuild.py",
                       'problems.append("duplicate id \'%s\'" % rel)',
                       "pass")
        self.assertNamed("de-duplicate ids")

    def test_a_silent_skip_reddens(self):
        self.tree.edit("knowledge/index/indexrebuild.py",
                       'sys.stderr.write("QUARANTINED: %s\\n" % msg)', "pass")
        self.assertNamed("QUARANTINED report")

    def test_restoring_the_wholesale_abort_reddens(self):
        self.tree.edit(
            "knowledge/index/indexrebuild.py",
            "    data, quarantined = build_index_from_frontmatter(kdir)\n",
            "    data, errors = build_index_from_frontmatter(kdir)\n"
            "    if errors:\n        return None, [], errors\n"
            "    quarantined = []\n")
        self.assertNamed("aborts the whole rebuild")


class ReadOnlyLoadRefutationTest(_RefutationCase):
    """A read-only verb that self-repairs reports a pass over a finding."""

    def test_dropping_the_opt_out_in_attach_reddens(self):
        # This is not hypothetical: adding recovery WITHOUT this opt-out turned
        # `attach`'s corrupt-index refusal into an acceptance, and its documented
        # read-only report started writing into the directory it inspects.
        self.tree.edit("knowledge/attach.py",
                       "index.load_index(path, _allow_rebuild=False)",
                       "index.load_index(path)")
        self.assertNamed("WITHOUT _allow_rebuild=False")

    def test_dropping_the_opt_out_in_features_reddens(self):
        self.tree.edit("knowledge/curation/curationfeatures.py",
                       "index.load_index(kdir, _allow_rebuild=False)",
                       "index.load_index(kdir)")
        self.assertNamed("WITHOUT _allow_rebuild=False")

    def test_dropping_the_opt_out_in_the_fleet_report_reddens(self):
        # `fleet` declares itself read-only (INV-2) and reports on terminal
        # EVENTS; with the recovery armed its tag histogram would write
        # index.json as a side effect of a report.
        self.tree.edit("ops/fleet/fleetreport.py",
                       "index.load_index(kdir, _allow_rebuild=False)",
                       "index.load_index(kdir)")
        self.assertNamed("WITHOUT _allow_rebuild=False")

    def test_renaming_the_owning_function_reddens_rather_than_passing_silently(self):
        # A gate that pins a name must FAIL when the name goes, not go green by
        # looking at nothing — the exact failure task 21 recorded.
        self.tree.edit("knowledge/attach.py",
                       "def attach_conformance_report(path):",
                       "def attach_report(path):")
        self.assertNamed("re-point READONLY_LOAD_SITES")


class GitignoreRefutationTest(_RefutationCase):

    def test_an_untracked_lock_sidecar_reddens(self):
        self.tree.edit("core/kblayout.py",
                       "KB_GITIGNORE_REQUIRED = KB_GITIGNORE_DIR_RULES + "
                       "(KB_INDEX_LOCK_REL,)",
                       "KB_GITIGNORE_REQUIRED = KB_GITIGNORE_DIR_RULES")
        self.assertNamed("KB_GITIGNORE_REQUIRED")


class WiringRefutationTest(_RefutationCase):
    """The load-bearing check: a lock nobody takes is not a lock."""

    def test_an_unlocked_read_modify_write_reddens(self):
        self.tree.edit("features/handler.py",
                       "    with index._index_lock(kdir):\n", "")
        self.tree.edit("features/handler.py",
                       "        data, err = index.load_index(kdir)\n"
                       "        if err is not None:\n"
                       "            return 1\n"
                       '        data["entries"].append({})\n'
                       "        index.save_index(kdir, data)\n",
                       "    data, err = index.load_index(kdir)\n"
                       "    if err is not None:\n"
                       "        return 1\n"
                       '    data["entries"].append({})\n'
                       "    index.save_index(kdir, data)\n")
        self.assertNamed("mutates the index outside _index_lock()")

    def test_a_fresh_literal_create_stays_green(self):
        # `cmd_seed` saves a brand-new empty index without loading one. There is no
        # read to lose, so it is legitimately unlocked — and the gate must not cry
        # wolf about it, or the rule gets muted.
        self.assertGreen()

    def test_a_create_site_saving_a_NON_literal_reddens(self):
        # The loophole this closes: "no load_index call" must not become a way to
        # smuggle an unlocked mutation past the gate.
        self.tree.edit("features/handler.py",
                       "    index.save_index(kdir, {\"entries\": [], \"tags\": {}})\n",
                       "    index.save_index(kdir, args.payload)\n")
        self.assertNamed("mutates the index outside _index_lock()")

    def test_a_helper_split_out_of_a_locked_caller_stays_green(self):
        # Coverage is REACHABILITY, not a pinned function name. `_rebuild_kb_locked`
        # holds no lock of its own; its only caller wraps it. That must pass, or the
        # gate punishes exactly the decomposition this plan is performing.
        self.assertGreen()

    def test_an_orphan_helper_with_no_caller_reddens(self):
        # ...but "nobody calls it" is not the same as "it is safe". An unreferenced
        # mutation helper is dead code at best and an unlocked path at worst.
        self.tree.edit("knowledge/index/indexrebuild.py",
                       "    with indexlock._index_lock(kdir):\n"
                       "        return _rebuild_kb_locked(kdir)\n",
                       "    return None, [], []\n")
        self.assertNamed("mutates the index outside _index_lock()")


@unittest.skipIf(_NO_CORE, _NO_CORE_REASON)
class RealTreeTest(unittest.TestCase):
    """The positive claim, against the tree actually under measurement."""

    def test_the_gate_is_green(self):
        errs = isg.all_errors()
        self.assertEqual(errs, [], "\n".join(errs))

    def test_the_gate_measured_something(self):
        # A gate that walked zero files reports zero problems. Never let that read
        # as a pass.
        self.assertGreater(len(isg.lib_files()), 20)


if __name__ == "__main__":
    unittest.main()
