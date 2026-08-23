"""Shared header for the `test_kbsync` suite — task 99 wave 8.

Holds every import, stand-in, seam-restoring base class and helper the two
parts share, so neither part duplicates a definition and the two cannot drift.
Split out because the wave's owning unit file for `ops.kbsync` outgrew the
600-line corpus cap; the seam is the one the MODULE itself has — the PUSH
pipeline (`test_kbsync`: the nothing-lost gate, the four pipeline steps, the
order that joins them, and the bounded retry) and the two CLI HANDLERS
(`test_kbsync_handlers`: the shared precondition chain, the prose-merge
continuation, and the stream split each handler owns).

`SeamRestoringTest` lives here rather than in either part on purpose: it names
every module-level seam this suite injects into, and a second copy is exactly
the duplication that would let one part forget a seam the other restores — and
a forgotten seam does not fail its own file, it silently changes a later one.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import os
import sys
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.knowledge.index as index  # noqa: E402
import agentware_lib.ops.kbgit as kbgit  # noqa: E402
import agentware_lib.ops.kbsync as kbsync  # noqa: E402
import agentware_lib.ops.staging as staging  # noqa: E402


class _CP(object):
    """A CompletedProcess stand-in with exactly the three fields these bodies
    read. CONSTRUCTED rather than run, because the only way to separate the two
    stages of the shared fallback is a stderr that is truthy but blank, and no
    real git invocation can be relied on to produce one."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Args(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _raiser(label):
    """A seam that raises when reached — the only way to assert a SHORT-CIRCUIT.
    An arm that returns the same value whether or not the later work ran is not
    falsifiable by its return value alone."""
    def _fn(*_a, **_k):
        raise AssertionError("reached: %s" % label)
    return _fn


def _capture(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rv = fn(*a, **k)
    return rv, out.getvalue(), err.getvalue()


class SeamRestoringTest(unittest.TestCase):
    """Restores every module-level seam this file injects into.

    The originals are captured in `setUp` BEFORE any test patches anything, so
    a cleanup can never reinstall a mutant — a cleanup registered after its own
    patch captures the ALREADY-PATCHED value, passes in its own test, and
    reddens an innocent later one instead.
    """

    def setUp(self):
        self._saved = [
            (kbsync, "_git", kbsync._git),
            (kbsync, "_git_upstream_name", kbsync._git_upstream_name),
            (kbsync, "_git_noninteractive_env", kbsync._git_noninteractive_env),
            (kbsync, "kb_nothing_lost_gate", kbsync.kb_nothing_lost_gate),
            (kbsync, "kb_git_push_once", kbsync.kb_git_push_once),
            (kbsync, "kb_git_push_synced", kbsync.kb_git_push_synced),
            (kbsync, "kb_git_merge_continue", kbsync.kb_git_merge_continue),
            (kbsync, "_push_attempt", kbsync._push_attempt),
            (kbsync, "_push_after_clean_rebase",
             kbsync._push_after_clean_rebase),
            (kbsync, "_prose_conflict_disposition",
             kbsync._prose_conflict_disposition),
            (kbsync, "_resolve_derived_conflict",
             kbsync._resolve_derived_conflict),
            (kbsync, "_resolve_sync_target", kbsync._resolve_sync_target),
            # The MODULE-alias seams. A leaked one is silent: the next file's
            # push simply stops happening, or its index rebuild returns a stub.
            (kbsync, "staging", kbsync.staging),
            (kbsync, "index", kbsync.index),
            (kbsync, "kbgit", kbsync.kbgit),
            (kbsync, "config", kbsync.config),
            (staging, "git_push", staging.git_push),
            (staging, "_push_was_rejected", staging._push_was_rejected),
            (staging, "git_unmerged_paths", staging.git_unmerged_paths),
            (staging, "_is_derived_rel", staging._is_derived_rel),
            (staging, "kb_git_stage_scoped", staging.kb_git_stage_scoped),
            (staging, "git_rebase_in_progress", staging.git_rebase_in_progress),
            (staging, "file_has_conflict_markers",
             staging.file_has_conflict_markers),
            (staging, "git_entry_ids_at_ref", staging.git_entry_ids_at_ref),
            (staging, "_kb_push_backoff_delay", staging._kb_push_backoff_delay),
            (index, "rebuild_kb", index.rebuild_kb),
            (index, "build_index_from_frontmatter",
             index.build_index_from_frontmatter),
            (index, "validate_index", index.validate_index),
            (kbgit, "git_is_work_tree", kbgit.git_is_work_tree),
            (kbgit, "git_has_upstream", kbgit.git_has_upstream),
            (kbgit, "git_toplevel", kbgit.git_toplevel),
        ]
        for mod, name, val in self._saved:
            self.addCleanup(setattr, mod, name, val)

    def _derived_only(self):
        """Wire the seams so `kb_git_push_once` reaches the DERIVED-conflict
        resolution: a rejected push, a failed rebase, one unmerged path, and
        that path classified as derived."""
        staging.git_push = lambda t: _CP(returncode=1, stderr="! [rejected]")
        staging._push_was_rejected = lambda cp: True
        staging.git_unmerged_paths = lambda t: ["index.json"]
        staging._is_derived_rel = lambda p: True
        kbsync._git = lambda t, *a, **k: _CP(
            returncode=1 if a[:1] == ("pull",) else 0)
        kbsync._git_upstream_name = lambda t: "origin/main"
        index.rebuild_kb = lambda t: ({}, {}, [])
        staging.kb_git_stage_scoped = lambda t, **k: (0, 1, [])
        kbsync.kb_nothing_lost_gate = lambda t, refs: (True, "gate ok")


