"""Shared header for the `test_mqintegrate` suite — task 99 wave 7.

Holds every import, fixture builder, seam-restoring base class and helper the
two parts share, so neither part duplicates a definition and the two cannot
drift. Split out because the wave's owning unit file for `mqintegrate` outgrew
the 600-line corpus cap; the seam is the one the module itself has — the QUEUE
(`test_mqintegrate`: the option surface, the dry run, the branch preflight, the
gate runner, the emitter and the parent loop) and the SINGLE LANE
(`test_mqintegrate_lane`: the ordered pipeline one lane runs through).

`SeamRestoringTest` lives here rather than in either part on purpose: it names
every module-level seam this suite injects into, and a second copy is exactly
the duplication that would let one part forget a seam the other restores — and
a forgotten seam does not fail its own file, it silently changes a later one.
"""

from tests._paths import TESTS_DIR

import io
import contextlib
import json
import os
import sys
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.features.fanout as fanout  # noqa: E402
import agentware_lib.ops.kbgit as kbgit  # noqa: E402
import agentware_lib.features.fanoutmq.mqbranch as mqbranch  # noqa: E402
import agentware_lib.features.fanoutmq.mqintegrate as mqi  # noqa: E402


class _Args(object):
    """An argparse.Namespace stand-in whose attributes are exactly the ones set."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _raiser(label):
    """A seam that raises when reached — the only way to assert a SHORT-CIRCUIT.
    A skip that returns the same row whether or not the work ran is not
    falsifiable by its return value."""
    def _fn(*_a, **_k):
        raise AssertionError("reached: %s" % label)
    return _fn


def _capture(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rv = fn(*a, **k)
    return rv, out.getvalue(), err.getvalue()


def _rec(feature="alpha", **kw):
    rec = {"feature": feature, "feat_branch": "feat/%s" % feature,
           "kb_branch": "kb/%s" % feature,
           "worktree": "/wt/%s/pkg" % feature, "kb_path": "/wt/%s/kb" % feature,
           "env_file": "/env/%s.env" % feature, "repo": "/pkg", "kb_repo": "/kb"}
    rec.update(kw)
    return rec


def _reg(*features, **kw):
    return {"lanes": {f: _rec(f, **kw) for f in features}}


OPTS = {"fmt_json": False, "dry": False, "integration": "integration",
        "base": None, "kb_base": None, "requested": [], "gates": []}


def _opts(**kw):
    o = dict(OPTS)
    o.update(kw)
    return o


class SeamRestoringTest(unittest.TestCase):
    """Restores every module-level seam this file injects into. The seams are
    re-read from the module on each call inside the product, so a leaked patch
    would silently change a LATER file's result rather than this one's."""

    def setUp(self):
        self._saved = [
            (mqi, "_mq_integrate_package", mqi._mq_integrate_package),
            (mqi, "_mq_integrate_kb", mqi._mq_integrate_kb),
            (mqi, "_mq_run_gates", mqi._mq_run_gates),
            (mqi, "_mq_process_lane", mqi._mq_process_lane),
            # The two MODULE-alias seams the lane processor reaches its side
            # effects through. They leak across files if not restored, and a
            # leaked `staging` spy is silent: the next file's commit simply
            # stops happening.
            (mqi, "staging", mqi.staging),
            (mqi, "_git", mqi._git),
            (fanout, "_run_gate", fanout._run_gate),
            (fanout, "_lane_outcome", fanout._lane_outcome),
            (fanout, "save_lanes", fanout.save_lanes),
            (fanout, "load_lanes", fanout.load_lanes),
            (fanout, "_resolve_repo_arg", fanout._resolve_repo_arg),
            (kbgit, "git_is_work_tree", kbgit.git_is_work_tree),
            (kbgit, "git_is_clean", kbgit.git_is_clean),
            (mqbranch, "_ensure_integration_branch",
             mqbranch._ensure_integration_branch),
            (mqbranch, "_lanes_common_repo", mqbranch._lanes_common_repo),
        ]
        for mod, name, val in self._saved:
            self.addCleanup(setattr, mod, name, val)

    def patch(self, mod, name, value):
        setattr(mod, name, value)


