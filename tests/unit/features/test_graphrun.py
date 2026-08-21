"""`graphrun`'s own unit file — task 99 wave 6, landed with the paydown.

`agentware_lib.features.graphdag.graphrun` owns the six subverbs that start,
observe, gate or stop a run. Until this file the module had no owning test:
`test_plan_graph_approval` drives `run`/`resume` through the approval gate, which
says nothing about `cancel` or `checkpoint` — the two verbs an operator reaches
for when a graph is already going wrong, and therefore the two whose refusals
matter most.

This wave paid down `cmd_graph_checkpoint` (22 branches -> 6) and
`cmd_graph_cancel` (15 -> 9). The seam that mattered was not the size: the cancel
LOOP was byte-duplicated in both verbs, so the two paths that write `cancelled`
into an operator's plans had two copies of the same three promises. They now share
`_graph_cancel_each`, and `_graph_checkpoint_resolve` / `_graph_cancel_emit` /
`_graph_checkpoint_emit` carry the rest.

  CANCEL/SOLE-WRITER   — every cancel goes through `cmd_plan_set_state` with
                         `actor="operator"`; the legal-edge matrix decides, and a
                         node with no legal edge (already `done`) is REPORTED as
                         not-cancelled rather than force-written.
  CANCEL/PARTITION     — accepted and refused land in the right list, and both
                         preserve the caller's order, so `not_cancelled` is a
                         usable answer rather than a set.
  CANCEL/QUIET         — the writer's own stdout/stderr are swallowed: an illegal
                         edge is an EXPECTED outcome here and its refusal text
                         would read to an operator as an error.
  CANCEL/RESILIENT     — a RAISING writer is a refusal, not a crash. One
                         un-cancellable node must not abandon the rest of the
                         subtree half-cancelled — which is worse than either
                         outcome, because the graph is then in neither state.
  CANCEL/SHARED        — checkpoint's `abort-subtree` arm and `cancel` are the
                         SAME routine. A second copy is how one of them silently
                         stops honouring the matrix.
  CP/NO-WRITE          — every one of the six refusals writes NOTHING. A
                         checkpoint that refused after stamping the plan would
                         leave an approval recorded for a verdict never given.
  CP/ORDER             — the two pure argument checks precede the manifest read
                         and the `manual` check precedes the plan read, so a typo
                         never touches disk and the barrier the loop can never
                         clear is refused before the plan is opened.
  CP/MANUAL            — a `manual` checkpoint accepts ONLY an operator verdict;
                         the loop can never clear it (R-PKG-03).
  EMIT/JSON-STABLE     — `not_cancelled` is always present in the json body even
                         when empty; a consumer cannot otherwise tell "no
                         refusals" from "this build does not report refusals".
  EMIT/TEXT-SCOPED     — the subtree lines appear only for `abort-subtree`, so a
                         `proceed` never prints a cancellation that was never
                         asked for.

Hermetic: a scratch knowledge dir per test, `AGENTWARE_KNOWLEDGE_DIR` pinned to
it, no subprocess, no network, stdlib unittest only.
"""

from tests._paths import TESTS_DIR

import argparse
import contextlib
import io
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

from agentware_lib.features.graphdag import graphrun  # noqa: E402
from agentware_lib.features.graphdag.graphmanifest import (  # noqa: E402
    _graph_save,
)
from agentware_lib.features.graphdag.graphrun import (  # noqa: E402
    _graph_cancel_each,
    _graph_cancel_emit,
    _graph_checkpoint_emit,
    _graph_checkpoint_resolve,
    cmd_graph_cancel,
    cmd_graph_checkpoint,
)

NOW = "2026-07-24T12:00:00Z"


def _plan(status="running"):
    claim = "> Claimed-by: lane@host %s\n" % NOW if status == "running" else ""
    return (
        "# Plan — fixture\n\n"
        "> Status: %s\n%s"
        "\n## Target packages\n\n- ~/workspace/agentware\n\n"
        "## Tasks\n\n- ✅ **1** done task\n  *Verify:* true\n\n"
        "## Acceptance criteria\n\n- [ ] it works\n" % (status, claim))


def _capture(fn, *a, **kw):
    """Run `fn`, returning (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*a, **kw)
    return rc, out.getvalue(), err.getvalue()


class _KdirCase(unittest.TestCase):
    """A scratch KB with a graph manifest and one plan per node."""

    NODES = (("a", "none"), ("b", "none"), ("c", "none"))
    EDGES = (("a", "b"), ("b", "c"))
    STATES = {}

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-t99w6-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(self._restore, prev)
        os.makedirs(os.path.join(self.kdir, "work"))
        self.manifest = {
            "schema": 1, "name": "260722-g", "concurrency": 3,
            "nodes": [{"feature": f, "checkpoint": cp, "self_extension": False,
                       "write_set": []} for f, cp in self.NODES],
            "edges": [{"from": s, "to": d, "kind": "logical"}
                      for s, d in self.EDGES],
        }
        _graph_save(self.kdir, self.manifest)
        for f, _cp in self.NODES:
            self._write_plan(f, self.STATES.get(f, "running"))

    def _restore(self, prev):
        if prev is None:
            os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        else:
            os.environ["AGENTWARE_KNOWLEDGE_DIR"] = prev

    def _write_plan(self, feature, status):
        p = os.path.join(self.kdir, "work", feature, "plan.md")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(_plan(status))

    def _plan_bytes(self):
        out = {}
        wroot = os.path.join(self.kdir, "work")
        for feat in sorted(os.listdir(wroot)):
            p = os.path.join(wroot, feat, "plan.md")
            if os.path.isfile(p):
                out[feat] = open(p, encoding="utf-8").read()
        return out

    def _ns(self, **kw):
        base = dict(now=NOW, format="text", handle="tester", host="testhost")
        base.update(kw)
        return argparse.Namespace(**base)


class CancelEachTest(_KdirCase):
    """CANCEL/SOLE-WRITER, CANCEL/PARTITION, CANCEL/QUIET, CANCEL/RESILIENT."""

    def test_it_partitions_accepted_from_refused(self):
        # `b` is already done: the state machine has no legal edge done ->
        # cancelled, so it must land in `refused`, not be force-written.
        self._write_plan("b", "done")
        cancelled, refused = _graph_cancel_each(
            ["a", "b", "c"], "because", NOW)
        self.assertEqual(cancelled, ["a", "c"])
        self.assertEqual(refused, ["b"])

    def test_a_refused_node_is_left_byte_identical(self):
        self._write_plan("b", "done")
        before = self._plan_bytes()["b"]
        _graph_cancel_each(["b"], "because", NOW)
        self.assertEqual(self._plan_bytes()["b"], before)

    def test_an_accepted_node_actually_reaches_the_cancelled_state(self):
        _graph_cancel_each(["a"], "because", NOW)
        self.assertIn("> Status: cancelled", self._plan_bytes()["a"])

    def test_both_lists_preserve_the_callers_order(self):
        self._write_plan("b", "done")
        cancelled, refused = _graph_cancel_each(
            ["c", "b", "a"], "because", NOW)
        self.assertEqual(cancelled, ["c", "a"])
        self.assertEqual(refused, ["b"])

    def test_the_writers_own_output_is_swallowed(self):
        self._write_plan("b", "done")
        _rc, out, err = _capture(
            lambda: _graph_cancel_each(["a", "b"], "because", NOW))
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_a_raising_writer_is_a_refusal_and_the_rest_still_run(self):
        # One node whose write EXPLODES must not abandon the subtree: the
        # remaining nodes are still cancelled and the exploder is reported.
        calls = []
        real = graphrun.cmd_plan_set_state

        def boom(ns):
            calls.append(ns.feature)
            if ns.feature == "b":
                raise RuntimeError("disk on fire")
            return real(ns)

        graphrun.cmd_plan_set_state = boom
        self.addCleanup(setattr, graphrun, "cmd_plan_set_state", real)
        cancelled, refused = _graph_cancel_each(
            ["a", "b", "c"], "because", NOW)
        self.assertEqual(calls, ["a", "b", "c"],
                         "the loop stopped at the raising node")
        self.assertEqual(cancelled, ["a", "c"])
        self.assertEqual(refused, ["b"])

    def test_it_always_writes_as_the_operator(self):
        seen = []
        real = graphrun.cmd_plan_set_state

        def spy(ns):
            seen.append((ns.actor, ns.state, ns.reason, ns.now))
            return real(ns)

        graphrun.cmd_plan_set_state = spy
        self.addCleanup(setattr, graphrun, "cmd_plan_set_state", real)
        _graph_cancel_each(["a"], "my reason", NOW, handle="h", host="ho")
        self.assertEqual(seen, [("operator", "cancelled", "my reason", NOW)])

    def test_an_empty_feature_list_writes_nothing(self):
        before = self._plan_bytes()
        self.assertEqual(_graph_cancel_each([], "because", NOW), ([], []))
        self.assertEqual(self._plan_bytes(), before)


class CheckpointResolveTest(_KdirCase):
    """CP/NO-WRITE, CP/ORDER, CP/MANUAL."""

    NODES = (("a", "none"), ("b", "none"), ("m", "manual"))
    EDGES = (("a", "b"),)

    def test_an_unknown_verdict_refuses_and_names_the_legal_set(self):
        ctx, err = _graph_checkpoint_resolve(
            self.kdir, "260722-g", "a", "nope", "operator")
        self.assertIsNone(ctx)
        self.assertIn("unknown verdict", err)
        self.assertIn("proceed", err)
        self.assertTrue(err.endswith("\n"))

    def test_a_none_verdict_is_refused_too(self):
        ctx, err = _graph_checkpoint_resolve(
            self.kdir, "260722-g", "a", None, "operator")
        self.assertIsNone(ctx)
        self.assertIn("unknown verdict", err)

    def test_an_unknown_actor_refuses(self):
        ctx, err = _graph_checkpoint_resolve(
            self.kdir, "260722-g", "a", "proceed", "robot")
        self.assertIsNone(ctx)
        self.assertIn("unknown actor", err)

    def _spy(self, name):
        """Record calls to a module-level collaborator without changing what it
        does. Returns the call list."""
        calls = []
        real = getattr(graphrun, name)

        def spy(*a, **k):
            calls.append(a)
            return real(*a, **k)

        setattr(graphrun, name, spy)
        self.addCleanup(setattr, graphrun, name, real)
        return calls

    def test_a_bad_verdict_is_refused_before_the_manifest_is_even_read(self):
        # ORDER: the two pure checks come first, so a typo never touches disk.
        # The claim is about the READ, and it must be asserted on the READ.
        # Asserting only the MESSAGE is what the wave-6 non-vacuity sweep caught:
        # hoisting `_graph_load_manifest` above the verdict check leaves the
        # message untouched, because the `if err:` that consumes it stays put.
        reads = self._spy("_graph_load_manifest")
        ctx, err = _graph_checkpoint_resolve(
            self.kdir, "no-such-graph", "a", "nope", "operator")
        self.assertIsNone(ctx)
        self.assertIn("unknown verdict", err)
        self.assertEqual(reads, [], "the manifest was read despite a bad verdict")

    def test_a_bad_actor_is_refused_before_the_manifest_is_even_read(self):
        reads = self._spy("_graph_load_manifest")
        ctx, err = _graph_checkpoint_resolve(
            self.kdir, "260722-g", "a", "proceed", "robot")
        self.assertIsNone(ctx)
        self.assertIn("unknown actor", err)
        self.assertEqual(reads, [])

    def test_a_good_request_does_read_the_manifest(self):
        # The control for the two rows above: a spy that never fires proves
        # nothing unless it can fire.
        reads = self._spy("_graph_load_manifest")
        _ctx, err = _graph_checkpoint_resolve(
            self.kdir, "260722-g", "a", "proceed", "operator")
        self.assertIsNone(err)
        self.assertEqual(len(reads), 1)

    def test_an_unknown_graph_refuses(self):
        ctx, err = _graph_checkpoint_resolve(
            self.kdir, "no-such-graph", "a", "proceed", "operator")
        self.assertIsNone(ctx)
        self.assertIn("no such graph", err)

    def test_a_node_outside_the_graph_refuses(self):
        ctx, err = _graph_checkpoint_resolve(
            self.kdir, "260722-g", "zzz", "proceed", "operator")
        self.assertIsNone(ctx)
        self.assertIn("is not in graph", err)

    def test_a_manual_checkpoint_refuses_the_loop_actor(self):
        ctx, err = _graph_checkpoint_resolve(
            self.kdir, "260722-g", "m", "proceed", "loop")
        self.assertIsNone(ctx)
        self.assertIn("'manual' checkpoint", err)
        self.assertIn("can never", err)

    def test_a_manual_checkpoint_accepts_the_operator(self):
        ctx, err = _graph_checkpoint_resolve(
            self.kdir, "260722-g", "m", "proceed", "operator")
        self.assertIsNone(err)
        self.assertEqual(ctx["policy"], "manual")

    def test_the_manual_refusal_precedes_the_plan_read(self):
        # ORDER, asserted on the READ for the same reason as the manifest rows:
        # the message alone survives hoisting `_load_plan_tolerant` above the
        # manual check, because the `if terr is not None:` stays where it is.
        reads = self._spy("_load_plan_tolerant")
        ctx, err = _graph_checkpoint_resolve(
            self.kdir, "260722-g", "m", "proceed", "loop")
        self.assertIsNone(ctx)
        self.assertIn("'manual' checkpoint", err)
        self.assertEqual(reads, [], "the plan was read despite a manual barrier")

    def test_an_operator_on_a_manual_node_does_read_the_plan(self):
        reads = self._spy("_load_plan_tolerant")
        _ctx, err = _graph_checkpoint_resolve(
            self.kdir, "260722-g", "m", "proceed", "operator")
        self.assertIsNone(err)
        self.assertEqual(len(reads), 1)

    def test_a_missing_plan_refuses(self):
        os.remove(os.path.join(self.kdir, "work", "a", "plan.md"))
        ctx, err = _graph_checkpoint_resolve(
            self.kdir, "260722-g", "a", "proceed", "operator")
        self.assertIsNone(ctx)
        self.assertIn("no plan", err)

    def test_a_node_without_a_checkpoint_key_defaults_to_none(self):
        man = dict(self.manifest)
        man["nodes"] = [{"feature": "bare", "write_set": []}]
        man["edges"] = []
        _graph_save(self.kdir, man)
        self._write_plan("bare", "running")
        ctx, err = _graph_checkpoint_resolve(
            self.kdir, "260722-g", "bare", "proceed", "loop")
        self.assertIsNone(err)
        self.assertEqual(ctx["policy"], "none")

    def test_every_refusal_writes_nothing(self):
        before = self._plan_bytes()
        for name, node, verdict, actor in (
                ("260722-g", "a", "nope", "operator"),
                ("260722-g", "a", "proceed", "robot"),
                ("no-such", "a", "proceed", "operator"),
                ("260722-g", "zzz", "proceed", "operator"),
                ("260722-g", "m", "proceed", "loop")):
            ctx, err = _graph_checkpoint_resolve(
                self.kdir, name, node, verdict, actor)
            self.assertIsNone(ctx)
            self.assertIsNotNone(err)
        self.assertEqual(self._plan_bytes(), before,
                         "a refusal wrote to a plan")

    def test_a_resolved_context_carries_the_manifest_and_the_plan_text(self):
        ctx, err = _graph_checkpoint_resolve(
            self.kdir, "260722-g", "a", "proceed", "operator")
        self.assertIsNone(err)
        self.assertEqual(ctx["policy"], "none")
        self.assertIn("> Status: running", ctx["text"])
        self.assertEqual(ctx["manifest"]["name"], "260722-g")


class EmitTest(unittest.TestCase):
    """EMIT/JSON-STABLE, EMIT/TEXT-SCOPED."""

    def test_cancel_json_always_carries_not_cancelled(self):
        _rc, out, _err = _capture(
            _graph_cancel_emit, "g", None, ["a"], [], True)
        body = json.loads(out)
        self.assertEqual(body["not_cancelled"], [])
        self.assertEqual(body["cancelled"], ["a"])
        self.assertIsNone(body["node"])
        self.assertTrue(out.endswith("\n"))

    def test_cancel_text_prints_none_rather_than_an_empty_tail(self):
        _rc, out, _err = _capture(
            _graph_cancel_emit, "g", None, [], [], False)
        self.assertIn("cancelled (none)", out)
        self.assertNotIn("left as-is", out)

    def test_cancel_text_names_the_refused_nodes(self):
        _rc, out, _err = _capture(
            _graph_cancel_emit, "g", "a", ["a"], ["b", "c"], False)
        self.assertIn("cancelled a", out)
        self.assertIn("left as-is (no legal cancel edge): b, c", out)

    def test_checkpoint_text_omits_the_subtree_lines_for_proceed(self):
        _rc, out, _err = _capture(
            _graph_checkpoint_emit, "g", "a", "proceed", "operator", "none",
            [], [], False)
        self.assertIn("proceed (by operator)", out)
        self.assertNotIn("cancelled subtree", out)

    def test_checkpoint_text_prints_the_subtree_for_abort(self):
        _rc, out, _err = _capture(
            _graph_checkpoint_emit, "g", "a", "abort-subtree", "operator",
            "manual", ["b"], ["c"], False)
        self.assertIn("cancelled subtree: b", out)
        self.assertIn("left as-is (no legal cancel edge): c", out)

    def test_checkpoint_json_carries_the_policy_and_both_lists(self):
        _rc, out, _err = _capture(
            _graph_checkpoint_emit, "g", "a", "hold", "loop", "auto",
            [], [], True)
        body = json.loads(out)
        self.assertEqual(body["checkpoint"], "auto")
        self.assertEqual(body["actor"], "loop")
        self.assertEqual(body["cancelled"], [])
        self.assertEqual(body["not_cancelled"], [])


class CancelVerbTest(_KdirCase):
    """The verb end to end, including the two scopes and the refusals."""

    def test_no_node_cancels_every_node_sorted(self):
        rc, out, _err = _capture(cmd_graph_cancel,
                                 self._ns(name="260722-g", node=None))
        self.assertEqual(rc, 0)
        self.assertIn("cancelled a, b, c", out)

    def test_a_node_cancels_it_and_its_transitive_successors_only(self):
        rc, out, _err = _capture(cmd_graph_cancel,
                                 self._ns(name="260722-g", node="b"))
        self.assertEqual(rc, 0)
        self.assertIn("cancelled b, c", out)
        self.assertIn("> Status: running", self._plan_bytes()["a"])

    def test_an_unknown_node_refuses_and_writes_nothing(self):
        before = self._plan_bytes()
        rc, _out, err = _capture(cmd_graph_cancel,
                                 self._ns(name="260722-g", node="zzz"))
        self.assertEqual(rc, 1)
        self.assertIn("is not in graph", err)
        self.assertEqual(self._plan_bytes(), before)

    def test_an_unknown_graph_refuses(self):
        rc, _out, err = _capture(cmd_graph_cancel,
                                 self._ns(name="nope", node=None))
        self.assertEqual(rc, 1)
        self.assertIn("no such graph", err)

    def test_the_reason_names_the_scope(self):
        seen = []
        real = graphrun.cmd_plan_set_state
        graphrun.cmd_plan_set_state = lambda ns: seen.append(ns.reason) or 0
        self.addCleanup(setattr, graphrun, "cmd_plan_set_state", real)
        _capture(cmd_graph_cancel, self._ns(name="260722-g", node="b"))
        self.assertTrue(all("from b" in r for r in seen), seen)
        seen.clear()
        _capture(cmd_graph_cancel, self._ns(name="260722-g", node=None))
        self.assertTrue(all("from" not in r for r in seen), seen)


class CheckpointVerbTest(_KdirCase):
    """The verb end to end: the log entry, the abort-subtree arm, the refusals."""

    NODES = (("a", "none"), ("b", "none"), ("c", "none"))
    EDGES = (("a", "b"), ("b", "c"))

    def test_a_proceed_appends_one_log_entry_and_cancels_nothing(self):
        rc, out, _err = _capture(
            cmd_graph_checkpoint,
            self._ns(name="260722-g", node="a", verdict="proceed",
                     actor="operator"))
        self.assertEqual(rc, 0)
        self.assertIn("proceed (by operator)", out)
        plan_a = self._plan_bytes()["a"]
        self.assertIn("%s proceed by operator (graph 260722-g, checkpoint none)"
                      % NOW, plan_a)
        self.assertIn("> Status: running", self._plan_bytes()["b"])

    def test_abort_subtree_cancels_the_successors_not_the_node_itself(self):
        rc, out, _err = _capture(
            cmd_graph_checkpoint,
            self._ns(name="260722-g", node="a", verdict="abort-subtree",
                     actor="operator"))
        self.assertEqual(rc, 0)
        self.assertIn("cancelled subtree: b, c", out)
        plans = self._plan_bytes()
        self.assertIn("> Status: cancelled", plans["b"])
        self.assertIn("> Status: cancelled", plans["c"])
        self.assertIn("> Status: running", plans["a"])

    def test_abort_subtree_reports_a_successor_with_no_legal_edge(self):
        self._write_plan("b", "done")
        rc, out, _err = _capture(
            cmd_graph_checkpoint,
            self._ns(name="260722-g", node="a", verdict="abort-subtree",
                     actor="operator"))
        self.assertEqual(rc, 0)
        self.assertIn("cancelled subtree: c", out)
        self.assertIn("left as-is (no legal cancel edge): b", out)

    def test_abort_subtree_goes_through_the_shared_cancel_routine(self):
        # CANCEL/SHARED: the checkpoint arm and the cancel verb are ONE routine,
        # so replacing it is observable from the checkpoint side.
        seen = []
        real = graphrun._graph_cancel_each
        graphrun._graph_cancel_each = lambda *a, **k: (
            seen.append((list(a[0]), a[1])) or (["spy"], []))
        self.addCleanup(setattr, graphrun, "_graph_cancel_each", real)
        rc, out, _err = _capture(
            cmd_graph_checkpoint,
            self._ns(name="260722-g", node="a", verdict="abort-subtree",
                     actor="operator"))
        self.assertEqual(rc, 0)
        self.assertEqual(seen[0][0], ["b", "c"])
        self.assertIn("checkpoint abort-subtree from a", seen[0][1])
        self.assertIn("cancelled subtree: spy", out)

    def test_a_refusal_leaves_no_checkpoint_log_behind(self):
        before = self._plan_bytes()
        rc, _out, err = _capture(
            cmd_graph_checkpoint,
            self._ns(name="260722-g", node="a", verdict="bogus",
                     actor="operator"))
        self.assertEqual(rc, 1)
        self.assertIn("unknown verdict", err)
        self.assertEqual(self._plan_bytes(), before)

    def test_an_empty_actor_defaults_to_operator(self):
        rc, out, _err = _capture(
            cmd_graph_checkpoint,
            self._ns(name="260722-g", node="a", verdict="proceed", actor=""))
        self.assertEqual(rc, 0)
        self.assertIn("(by operator)", out)

    def test_json_format_reports_the_same_verdict(self):
        rc, out, _err = _capture(
            cmd_graph_checkpoint,
            self._ns(name="260722-g", node="a", verdict="hold",
                     actor="loop", format="json"))
        self.assertEqual(rc, 0)
        body = json.loads(out)
        self.assertEqual(body["verdict"], "hold")
        self.assertEqual(body["actor"], "loop")
        self.assertTrue(body["ok"])

    def test_the_plan_keeps_its_trailing_newline(self):
        _capture(cmd_graph_checkpoint,
                 self._ns(name="260722-g", node="a", verdict="proceed",
                          actor="operator"))
        self.assertTrue(self._plan_bytes()["a"].endswith("\n"))


if __name__ == "__main__":
    unittest.main()
