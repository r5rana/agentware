"""Shared header for the `test_graphexec` suite — task 99 wave 6.

Holds every import, constant, fixture builder and base class the two parts share,
so neither part duplicates a definition and the two cannot drift. Split out
because the wave's owning unit file for `graphexec` outgrew the 600-line corpus
cap; the seam is the one the module itself has — the LOOP (`test_graphexec`) and
the individual ARMS (`test_graphexec_arms`).

`ARM_ORDER` lives here rather than in either part on purpose: it is the
scheduler's priority, both parts read it, and a second copy is exactly the
duplication that would let one part keep asserting an order the other no longer
holds.
"""

from tests._paths import TESTS_DIR

import ast
import inspect
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

from agentware_lib.features.graphdag import graphexec  # noqa: E402
from agentware_lib.features.graphdag import graphlane  # noqa: E402
from agentware_lib.features.graphdag.graphconst import (  # noqa: E402
    GRAPH_KILL_SWITCH_VARS,
)
from agentware_lib.features.graphdag.graphexec import (  # noqa: E402
    _graph_deadline_exceeded,
    _graph_deadline_start,
    _graph_execute,
    _graph_execute_preflight,
    _graph_execute_seams,
    _graph_execute_selfext,
    _graph_execute_setup,
    _graph_execute_topology,
    _graph_run_result,
    _graph_tick_halt_or_pause,
    _graph_tick_select,
    _graph_tick_spawn_wave,
    _graph_tick_stalled,
    _graph_tick_terminal,
)

NOW = "2026-07-24T12:00:00Z"

#: The six tick arms, in the order the scheduler's priority requires.
ARM_ORDER = ("_graph_tick_statuses", "_graph_tick_halt_or_pause",
             "_graph_tick_finalize_or_merge", "_graph_tick_terminal",
             "_graph_tick_select", "_graph_tick_spawn_wave")

#: What each arm must return for the tick to fall through to the next one.
ARM_PASSTHROUGH = {
    "_graph_tick_statuses": ({}, {}),
    "_graph_tick_halt_or_pause": None,
    "_graph_tick_finalize_or_merge": (None, False),
    "_graph_tick_terminal": None,
    "_graph_tick_select": ({"dispatch": [], "halt_selfext": []}, None),
    "_graph_tick_spawn_wave": None,
}


def _plan(status="running", *, open_markers=0, done_markers=1, self_ext=False,
          claim=None):
    typ = "> Type: **self-extension (package change)**\n" if self_ext else ""
    claim_line = "> Claimed-by: %s\n" % claim if claim else ""
    tasks = ["- ✅ **%d** done task\n  *Verify:* true\n" % i
             for i in range(1, done_markers + 1)]
    tasks += ["- ⬜ **%d** open task\n  *Verify:* true\n" % i
              for i in range(done_markers + 1, done_markers + open_markers + 1)]
    return (
        "# Plan — node fixture\n\n"
        "> Status: %s\n%s%s"
        "\n## Target packages\n\n- ~/workspace/agentware\n\n"
        "## Tasks\n\n%s\n"
        "## Acceptance criteria\n\n- [ ] it works\n"
        % (status, claim_line, typ, "".join(tasks) or "- ✅ **1** t\n"))


def _man(features=(), edges=(), *, concurrency=3, write_sets=None,
         checkpoints=None):
    ws = write_sets or {}
    cps = checkpoints or {}
    return {
        "schema": 1, "name": "260722-g", "concurrency": concurrency,
        "nodes": [{"feature": f, "checkpoint": cps.get(f, "none"),
                   "self_extension": False, "write_set": list(ws.get(f, []))}
                  for f in features],
        "edges": [{"from": s, "to": d, "kind": "logical"} for s, d in edges],
    }


class _KdirCase(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-t99w6x-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.fanout = os.path.join(self.kdir, "fanout")
        os.makedirs(os.path.join(self.kdir, "work"))
        os.makedirs(os.path.join(self.kdir, "logs"))
        os.makedirs(self.fanout)
        for key, val in (("AGENTWARE_KNOWLEDGE_DIR", self.kdir),
                         ("AGENTWARE_FANOUT_DIR", self.fanout)):
            prev = os.environ.get(key)
            os.environ[key] = val
            self.addCleanup(self._restore, key, prev)

    def _restore(self, key, prev):
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev

    def seed(self, feature, **kw):
        p = os.path.join(self.kdir, "work", feature, "plan.md")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(_plan(**kw))

    def terminal(self, feature, outcome):
        with open(os.path.join(self.kdir, "logs", "metrics.jsonl"), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps({"event": "terminal", "feature": feature,
                                "outcome": outcome, "ts": NOW}) + "\n")

    def lanes(self, mapping):
        with open(os.path.join(self.fanout, "lanes.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"lanes": {
                f: {"feature": f, "merged": merged, "kb_path": "/n",
                    "worktree": "/n", "feat_branch": "feat/%s" % f,
                    "kb_branch": "kb/%s" % f}
                for f, merged in mapping.items()}}, f)

    def execute(self, manifest, **kw):
        kw.setdefault("bootstrap", False)
        kw.setdefault("check_env", False)
        kw.setdefault("spawn", lambda rec, f: None)
        kw.setdefault("merge", lambda *a, **k: "merged")
        kw.setdefault("prepare", lambda *a, **k: ("created", None))
        return _graph_execute(self.kdir, manifest, NOW, repo=self.kdir,
                              kb_repo=self.kdir, **kw)


