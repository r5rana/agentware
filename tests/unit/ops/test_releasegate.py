"""Owning unit file for `agentware_lib.ops.releasegate` — task 99 wave 17.

The module carried a `test-ownership` row, and the reason it did is worth stating
plainly: this is the harness the R-PKG-06 gate runs before anything ships, and
the only thing exercising it was `gate release` itself — a run that takes minutes,
reaches exactly one tier and one output mode, and can only be driven against a
tree whose real ledger and real corpus decide the answer. Nothing in the corpus
reddened for the ORCHESTRATION: which tiers run, in what order, what a SKIP
means, and whether the two renders agree.

What is asserted here is the module's PROMISE, not its shape:

  * A SKIP IS `passed: true`, AND THAT IS DELIBERATE. The gate makes no claim
    about a tree it did not measure; a caller treating "not run" as "failed"
    would block every environment the kill-switch exists for. Both skip paths are
    asserted to say so, in both modes.
  * THE TWO SKIPS DIFFER ONLY IN THEIR REASON. They were byte-identical
    copy-paste; the test compares the two payloads field by field with the reason
    removed, so a future divergence in what a skip MEANS on one path is caught.
  * THE KILL-SWITCH IS CHECKED BEFORE THE WORKSPACE. An operator who disabled the
    gate must not then be told their knowledge dir is missing, and vice versa —
    the precedence is pinned with BOTH conditions true at once.
  * A WHITESPACE KILL-SWITCH IS NOT SET. `.strip()` is the difference between
    "exported empty by a shell" and "deliberately disabled".
  * TIER B IS OPT-IN AND APPENDED, NOT SUBSTITUTED. `--full` runs six gates in a
    fixed order; the three tier-A gates are the same three either way.
  * A BROKEN INDEX DEGRADES TO AN EMPTY CORPUS AND STILL RUNS THE GATES, because
    Gate 1 is repo-only and is exactly the gate a broken corpus needs.
  * ONE PAYLOAD, TWO RENDERS. The json document and the text report are built
    from the same dict, so they cannot disagree about which gates ran.

Hermetic: every knowledge dir is a fresh mkdtemp and all five real gates are
stubbed — this file is about the orchestration, and a real gate run would make
the assertions depend on the ledger of whatever tree the suite happens to be on.
"""

from tests._paths import TESTS_DIR

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

import agentware_lib.core.config as config              # noqa: E402
import agentware_lib.ops.releasegate as releasegate     # noqa: E402

KILL = "AGENTWARE_DISABLE_RELEASE_GATE"


class Args(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _gate(name, passed=True, details=("stub",)):
    return {"gate": name, "passed": passed, "details": list(details)}


class _GateCase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-t99w17-gate-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.kdir = os.path.join(self.root, "kb")
        os.makedirs(self.kdir)
        self.calls = []
        self._patch(config, "resolve_knowledge_dir", lambda: self.kdir)
        self._patch(releasegate.config, "resolve_knowledge_dir",
                    lambda: self.kdir)
        self._patch(releasegate.index, "load_index",
                    lambda k: ({"entries": []}, None))
        self._stub_gates()
        old = os.environ.pop(KILL, None)
        if old is not None:
            self.addCleanup(os.environ.__setitem__, KILL, old)

    def _patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def _stub_gates(self, tier_a_pass=True, tier_b_pass=True):
        def record(name, result):
            self.calls.append(name)
            return result
        self._patch(releasegate, "_gate1_content_lint",
                    lambda: record("g1", _gate("content-lint", tier_a_pass)))
        self._patch(releasegate, "_gate2_lite_gold_fixture",
                    lambda k, d, t: record("g2lite", _gate("gold-fixture-lite")))
        self._patch(releasegate, "_gate3_reliability_delta",
                    lambda k, t: record("g3", _gate("reliability-delta")))
        self._patch(releasegate, "_gate2_full",
                    lambda k, d, t: record("g2full", [
                        _gate("gold-fixture-full", tier_b_pass),
                        _gate("longmemeval", tier_b_pass)]))
        self._patch(releasegate, "_gate4_swe_task",
                    lambda: record("g4", _gate("swe-task")))

    def run_gate(self, **kw):
        kw.setdefault("format", "text")
        kw.setdefault("full", False)
        kw.setdefault("tolerance", 0.02)
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = releasegate.cmd_gate_release(Args(**kw))
        finally:
            sys.stdout, sys.stderr = old
        return rc, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------
# _emit_skip — the ONE emitter both no-ops use
# --------------------------------------------------------------------------

class EmitSkipTest(unittest.TestCase):

    def _emit(self, msg, as_json):
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            rc = releasegate._emit_skip(msg, as_json)
        finally:
            sys.stdout = old
        return rc, out.getvalue()

    def test_a_skip_returns_zero(self):
        self.assertEqual(self._emit("why", False)[0], 0)

    def test_a_skip_is_reported_as_PASSED_not_failed(self):
        # The gate makes no claim about a tree it did not measure. A caller that
        # read `passed: false` here would block every environment the
        # kill-switch exists for.
        _rc, out = self._emit("why", True)
        payload = json.loads(out)
        self.assertTrue(payload["passed"])
        self.assertTrue(payload["skipped"])

    def test_the_reason_is_carried_verbatim_in_both_modes(self):
        _rc, text = self._emit("release gate SKIPPED (because)", False)
        _rc2, js = self._emit("release gate SKIPPED (because)", True)
        self.assertEqual(text, "release gate SKIPPED (because)\n")
        self.assertEqual(json.loads(js)["reason"],
                         "release gate SKIPPED (because)")

    def test_the_json_document_carries_no_gates_key(self):
        # A skip has no gate results; emitting an empty list would let a reader
        # conclude the gates ran and found nothing.
        _rc, out = self._emit("why", True)
        self.assertEqual(sorted(json.loads(out)), ["passed", "reason", "skipped"])

    def test_the_json_document_ends_with_a_newline(self):
        self.assertTrue(self._emit("why", True)[1].endswith("\n"))


# --------------------------------------------------------------------------
# _print_gate_report
# --------------------------------------------------------------------------

class PrintGateReportTest(unittest.TestCase):

    def _render(self, payload):
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            releasegate._print_gate_report(payload)
        finally:
            sys.stdout = old
        return out.getvalue()

    def test_the_header_carries_the_overall_verdict(self):
        self.assertIn("RELEASE GATE PASSED",
                      self._render({"passed": True, "gates": []}))
        self.assertIn("RELEASE GATE FAILED",
                      self._render({"passed": False, "gates": []}))

    def test_each_gate_is_labelled_by_its_own_verdict(self):
        body = self._render({"passed": False, "gates": [
            _gate("a", True), _gate("b", False)]})
        self.assertIn("[PASS] a", body)
        self.assertIn("[FAIL] b", body)

    def test_a_PASSING_gate_still_prints_its_details(self):
        # Unlike `audit`, whose passing checks are silent: these details ARE the
        # measured numbers the release decision rests on, so suppressing them
        # would hide the evidence for a pass.
        body = self._render({"passed": True,
                             "gates": [_gate("a", True, ["Recall@5=1.0000"])]})
        self.assertIn("      Recall@5=1.0000", body)

    def test_every_detail_is_printed_not_just_the_first(self):
        body = self._render({"passed": True,
                             "gates": [_gate("a", True, ["one", "two"])]})
        self.assertIn("      one", body)
        self.assertIn("      two", body)

    def test_a_gate_with_no_details_key_at_all_renders_its_line(self):
        # The reader is `.get("details", [])`; a gate function that returned only
        # a verdict must not crash the report.
        body = self._render({"passed": True,
                             "gates": [{"gate": "a", "passed": True}]})
        self.assertIn("[PASS] a", body)


# --------------------------------------------------------------------------
# cmd_gate_release — the two skips
# --------------------------------------------------------------------------

class GateSkipTest(_GateCase):

    def test_the_kill_switch_skips_without_running_a_gate(self):
        os.environ[KILL] = "1"
        self.addCleanup(os.environ.pop, KILL, None)
        rc, out, _err = self.run_gate()
        self.assertEqual(rc, 0)
        self.assertIn(KILL, out)
        self.assertEqual(self.calls, [])

    def test_a_whitespace_kill_switch_is_not_set(self):
        # The difference between "exported empty by a shell" and "deliberately
        # disabled" — and the corpus has no other row that separates them.
        os.environ[KILL] = "   "
        self.addCleanup(os.environ.pop, KILL, None)
        rc, out, _err = self.run_gate()
        self.assertEqual(rc, 0)
        self.assertNotIn("SKIPPED", out)
        self.assertEqual(self.calls, ["g1", "g2lite", "g3"])

    def test_an_absent_knowledge_dir_skips(self):
        self._patch(releasegate.config, "resolve_knowledge_dir", lambda: None)
        rc, out, _err = self.run_gate()
        self.assertEqual(rc, 0)
        self.assertIn("no knowledge dir", out)
        self.assertEqual(self.calls, [])

    def test_a_knowledge_dir_that_is_a_FILE_skips(self):
        # The second half of the guard: a path that resolves but is not a
        # directory. "Resolved" and "usable" are different claims.
        f = os.path.join(self.root, "afile")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("x\n")
        self._patch(releasegate.config, "resolve_knowledge_dir", lambda: f)
        rc, out, _err = self.run_gate()
        self.assertEqual(rc, 0)
        self.assertIn("no knowledge dir", out)

    def test_the_kill_switch_is_checked_BEFORE_the_workspace(self):
        # Both conditions true at once: an operator who disabled the gate must
        # be told that, not that their knowledge dir is missing.
        os.environ[KILL] = "1"
        self.addCleanup(os.environ.pop, KILL, None)
        self._patch(releasegate.config, "resolve_knowledge_dir", lambda: None)
        _rc, out, _err = self.run_gate()
        self.assertIn(KILL, out)
        self.assertNotIn("no knowledge dir", out)

    def test_the_two_skips_differ_ONLY_in_their_reason(self):
        os.environ[KILL] = "1"
        self.addCleanup(os.environ.pop, KILL, None)
        _rc, killed, _e = self.run_gate(format="json")
        os.environ.pop(KILL, None)
        self._patch(releasegate.config, "resolve_knowledge_dir", lambda: None)
        _rc2, nokb, _e2 = self.run_gate(format="json")
        a, b = json.loads(killed), json.loads(nokb)
        self.assertNotEqual(a.pop("reason"), b.pop("reason"))
        self.assertEqual(a, b)


# --------------------------------------------------------------------------
# cmd_gate_release — the tiers
# --------------------------------------------------------------------------

class GateTiersTest(_GateCase):

    def test_tier_a_runs_exactly_three_gates_in_order(self):
        rc, out, _err = self.run_gate()
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, ["g1", "g2lite", "g3"])
        self.assertEqual(out.count("[PASS]"), 3)

    def test_full_appends_tier_b_and_keeps_tier_a_unchanged(self):
        rc, _out, _err = self.run_gate(full=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, ["g1", "g2lite", "g3", "g2full", "g4"])

    def test_full_reports_six_gates_in_a_fixed_order(self):
        _rc, out, _err = self.run_gate(format="json", full=True)
        self.assertEqual([g["gate"] for g in json.loads(out)["gates"]],
                         ["content-lint", "gold-fixture-lite",
                          "reliability-delta", "gold-fixture-full",
                          "longmemeval", "swe-task"])

    def test_one_failing_tier_a_gate_fails_the_run(self):
        self._stub_gates(tier_a_pass=False)
        rc, out, _err = self.run_gate()
        self.assertEqual(rc, 1)
        self.assertIn("RELEASE GATE FAILED", out)

    def test_one_failing_tier_b_gate_fails_the_run(self):
        self._stub_gates(tier_b_pass=False)
        self.assertEqual(self.run_gate(full=True)[0], 1)

    def test_a_failing_tier_b_gate_is_invisible_without_full(self):
        # Tier B is OPT-IN: the same tree must pass tier A alone. Without this
        # pair, "--full runs more gates" and "--full runs different gates" are
        # indistinguishable.
        self._stub_gates(tier_b_pass=False)
        self.assertEqual(self.run_gate()[0], 0)

    def test_the_tolerance_is_threaded_to_the_gates_that_take_one(self):
        seen = []
        self._patch(releasegate, "_gate2_lite_gold_fixture",
                    lambda k, d, t: seen.append(("g2", t)) or _gate("g2"))
        self._patch(releasegate, "_gate3_reliability_delta",
                    lambda k, t: seen.append(("g3", t)) or _gate("g3"))
        self.run_gate(tolerance=0.05)
        self.assertEqual(seen, [("g2", 0.05), ("g3", 0.05)])

    def test_a_broken_index_degrades_to_an_empty_corpus_and_still_runs(self):
        # Gate 1 is repo-only, and a corpus that will not load is exactly the
        # tree that needs it.
        seen = []
        self._patch(releasegate.index, "load_index", lambda k: (None, "broken"))
        self._patch(releasegate, "_gate2_lite_gold_fixture",
                    lambda k, d, t: seen.append(d) or _gate("g2"))
        rc, _out, _err = self.run_gate()
        self.assertEqual(rc, 0)
        self.assertEqual(seen, [[]])

    def test_a_loadable_index_is_passed_through_untouched(self):
        seen = []
        self._patch(releasegate, "_gate2_lite_gold_fixture",
                    lambda k, d, t: seen.append(d) or _gate("g2"))
        self.run_gate()
        self.assertEqual(seen, [{"entries": []}])

    def test_a_completed_run_is_never_marked_skipped(self):
        _rc, out, _err = self.run_gate(format="json")
        self.assertFalse(json.loads(out)["skipped"])

    def test_both_modes_render_the_same_gate_list(self):
        _rc, text, _e = self.run_gate(full=True)
        self._stub_gates()
        _rc2, js, _e2 = self.run_gate(format="json", full=True)
        for g in json.loads(js)["gates"]:
            self.assertIn(g["gate"], text)

    def test_a_bare_namespace_behaves_like_the_argparse_defaults(self):
        # Every read is a getattr with a default, which is what lets an internal
        # caller invoke the gate without building a parser.
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = releasegate.cmd_gate_release(Args())
        finally:
            sys.stdout, sys.stderr = old
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, ["g1", "g2lite", "g3"])
        self.assertIn("RELEASE GATE PASSED", out.getvalue())


# --------------------------------------------------------------------------
# the export surface
# --------------------------------------------------------------------------

class ExportSurfaceTest(unittest.TestCase):
    """The wave's two new helpers are deliberately ABSENT from the facade
    surface: they have no monolith counterpart, and exporting them would widen
    the surface the parity machinery pins."""

    def test_the_shipped_exports_are_unchanged_by_this_wave(self):
        self.assertEqual(releasegate._EXPORTS, [
            "_gate1_content_lint", "_gate2_lite_gold_fixture",
            "_gate3_reliability_delta", "_gate2_full", "_gate4_swe_task",
            "cmd_gate_release"])
        self.assertEqual(sorted(releasegate.__all__),
                         sorted(releasegate._EXPORTS))

    def test_the_new_helpers_exist_but_are_not_exported(self):
        for name in ("_emit_skip", "_print_gate_report"):
            self.assertTrue(hasattr(releasegate, name), name)
            self.assertNotIn(name, releasegate._EXPORTS)


if __name__ == "__main__":
    unittest.main()
