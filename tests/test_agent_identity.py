"""Agent-identity model — PLAN_AW / WORK_AW / LOOP attribution (feature
260626-dashboard-loop-perf, identity redesign).

Pins the operator's identity model so a regression can't bring back the bugs
that prompted it:
  * a session is attributed to a feature it ACTUALLY acted on (edited a file /
    wrote its plan / ran its loop), NEVER to a feature merely MENTIONED in
    injected context (the "everything is tokto" bug — MAIN.md names a feature
    every session then inherited);
  * a PLANNER is a session that AUTHORED a plan (full Write of work/<f>/plan.md)
    or spawned the planner agent — NOT one that merely edited plan.md status
    markers (that is a worker), and NOT the polluted classify_stage 'plan' label;
  * a LOOP run (AGENTWARE_STAGE=loop-*) is always feature-named;
  * an ad-hoc worker with no determinable feature stays anonymous (provisional),
    backfilled only when an action reveals the feature;
  * empty 0-turn sessions are dropped as noise;
  * completeness: planner w/o a plan is 'incomplete'; a worker is 'complete' only
    on an emitted promise, else neutrally 'ended'.

Stdlib-only, hermetic.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _fixtures import load_cli  # noqa: E402

CLI = load_cli()


def _write_session(kdir, sid, blocks, day=26, user_text="go",
                   model="claude-opus-4-8"):
    """blocks: list of (content_list, in_tokens, out_tokens) assistant turns."""
    sroot = CLI.logs_sessions_dir(kdir)
    sdir = os.path.join(sroot, sid)
    os.makedirs(os.path.join(sdir, "subagents"), exist_ok=True)
    lines = [{"type": "user", "timestamp": "2026-06-%02dT09:00:00Z" % day,
              "message": {"content": user_text}}]
    for i, (content, intok, outtok) in enumerate(blocks):
        lines.append({
            "type": "assistant",
            "timestamp": "2026-06-%02dT09:%02d:00Z" % (day, i + 1),
            "message": {"model": model,
                        "usage": {"input_tokens": intok, "output_tokens": outtok,
                                  "cache_read_input_tokens": intok},
                        "content": content}})
    with open(os.path.join(sdir, "main.jsonl"), "w", encoding="utf-8") as f:
        for o in lines:
            f.write(json.dumps(o) + "\n")
    return sdir


def _edit(feat):
    return {"type": "tool_use", "name": "Edit",
            "input": {"file_path": "work/%s/src.py" % feat}}


def _write_plan(feat):
    return {"type": "tool_use", "name": "Write",
            "input": {"file_path": "work/%s/plan.md" % feat}}


def _edit_plan(feat):
    return {"type": "tool_use", "name": "Edit",
            "input": {"file_path": "work/%s/plan.md" % feat}}


def _promise(feat):
    return {"type": "text", "text": "<promise>%s_COMPLETE</promise>" % feat}


def _ident(kdir, sid):
    row = CLI.parse_session(os.path.join(CLI.logs_sessions_dir(kdir), sid))
    return row, CLI.resolve_session_identity(row)


class AgentIdentityTestCase(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-identity-")
        self.addCleanup(shutil.rmtree, self.kdir, True)

    # -- ACTION attribution beats injected MENTIONS (the tokto bug) ------------
    def test_action_feature_beats_injected_mentions(self):
        # The session EDITS feature A once, but injected context MENTIONS feature
        # B many times (as MAIN.md does for tokto). Attribution must be A.
        injected = "see work/260624-bbb-other " * 20  # heavy passive mention
        _write_session(self.kdir, "11111111-act", [
            ([{"type": "text", "text": injected}, _edit("260626-aaa-real")],
             5000, 1000)],
            user_text=injected)
        row, ident = _ident(self.kdir, "11111111-act")
        self.assertEqual(row["action_feature"], "260626-aaa-real")
        self.assertEqual(ident["feature"], "260626-aaa-real")
        self.assertEqual(ident["kind"], "work")
        self.assertEqual(ident["confidence"], "high")

    # -- PLANNER = authored a plan (Write), not edited status markers ----------
    def test_plan_authoring_is_write_only(self):
        _write_session(self.kdir, "22222222-author", [
            ([_write_plan("260626-feat")], 50000, 12000)])
        _, ident = _ident(self.kdir, "22222222-author")
        self.assertEqual(ident["kind"], "plan")
        self.assertEqual(ident["feature"], "260626-feat")
        self.assertEqual(ident["complete"], True)
        self.assertEqual(ident["terminal"], "complete")

    def test_editing_plan_markers_is_worker_not_planner(self):
        # A worker flips task status (Edit plan.md) + edits source — NOT a planner.
        _write_session(self.kdir, "33333333-marker", [
            ([_edit_plan("260626-feat"), _edit("260626-feat")], 60000, 15000)])
        _, ident = _ident(self.kdir, "33333333-marker")
        self.assertEqual(ident["kind"], "work")
        self.assertEqual(ident["feature"], "260626-feat")

    def test_authored_a_plan_then_built_it_is_worker(self):
        # The crux: a session that WRITES a plan.md AND then edits code (executes)
        # is a WORKER that authored its own plan — NOT a planner. A 668-turn build
        # that wrote its plan first must not pollute the planner pillar.
        _write_session(self.kdir, "aabb-mixed", [
            ([_write_plan("260626-feat"), _edit("260626-feat")], 200000, 50000)])
        row, ident = _ident(self.kdir, "aabb-mixed")
        self.assertGreater(row["code_edits"], 0)
        self.assertEqual(ident["kind"], "work")
        self.assertEqual(ident["feature"], "260626-feat")

    def test_pure_plan_authoring_no_code_is_planner(self):
        # Authoring a plan with NO code edits = a real planner.
        _write_session(self.kdir, "ccdd-pure", [
            ([{"type": "text", "text": "drafting"}, _write_plan("260626-feat")],
             80000, 20000)])
        row, ident = _ident(self.kdir, "ccdd-pure")
        self.assertEqual(row["code_edits"], 0)
        self.assertEqual(ident["kind"], "plan")

    # -- PLANNER incomplete: spawned the planner agent, produced no plan --------
    def test_planner_spawned_without_plan_is_incomplete(self):
        _write_session(self.kdir, "44444444-wip", [
            ([{"type": "tool_use", "name": "Task",
               "input": {"subagent_type": "agentware-planner",
                         "prompt": "plan it"}}], 30000, 8000)])
        _, ident = _ident(self.kdir, "44444444-wip")
        self.assertEqual(ident["kind"], "plan")
        self.assertIsNone(ident["feature"])
        self.assertTrue(ident["name"].startswith("planner "))
        self.assertEqual(ident["confidence"], "pending")
        self.assertEqual(ident["terminal"], "incomplete")

    # -- LOOP run: always feature-named ---------------------------------------
    def test_loop_session_is_loop_kind(self):
        _write_session(self.kdir, "55555555-loop", [
            ([_edit("260626-feat")], 40000, 10000)],
            user_text="AGENTWARE_STAGE=loop-main running work/260626-feat")
        _, ident = _ident(self.kdir, "55555555-loop")
        self.assertEqual(ident["kind"], "loop")
        self.assertEqual(ident["feature"], "260626-feat")

    # -- WORKER ad-hoc: no work/<feature> action => anonymous, provisional -----
    def test_adhoc_worker_with_no_feature_is_anonymous(self):
        _write_session(self.kdir, "66666666-adhoc", [
            ([{"type": "tool_use", "name": "Edit",
               "input": {"file_path": "scripts/agentware"}}], 20000, 5000)])
        _, ident = _ident(self.kdir, "66666666-adhoc")
        self.assertEqual(ident["kind"], "work")
        self.assertIsNone(ident["feature"])
        self.assertTrue(ident["name"].startswith("ad-hoc "))
        self.assertEqual(ident["confidence"], "pending")

    def test_worker_complete_only_on_promise(self):
        _write_session(self.kdir, "77777777-done", [
            ([_edit("260626-feat"), _promise("260626-FEAT")], 30000, 8000)])
        _, done = _ident(self.kdir, "77777777-done")
        self.assertEqual(done["terminal"], "complete")
        _write_session(self.kdir, "88888888-open", [
            ([_edit("260626-feat")], 30000, 8000)])
        _, open_ = _ident(self.kdir, "88888888-open")
        self.assertEqual(open_["terminal"], "ended")  # neutral, not alarmed

    # -- Noise filtering -------------------------------------------------------
    def test_empty_session_is_noise(self):
        # A SessionStart stub: a user line + an assistant turn with zero usage and
        # no tool calls => dropped from the pillars.
        _write_session(self.kdir, "99999999-empty", [
            ([{"type": "text", "text": "hi"}], 0, 0)])
        row = CLI.parse_session(
            os.path.join(CLI.logs_sessions_dir(self.kdir), "99999999-empty"))
        self.assertTrue(CLI._is_noise_session(row))
        ag = CLI.derive_agents(self.kdir)
        ids = [s["session_id"] for s in ag["work"]["sessions"]] + \
              [s["session_id"] for s in ag["plan"]["sessions"]]
        self.assertNotIn("99999999-empty", ids)

    # -- derive_agents segmentation + counts ----------------------------------
    def test_derive_agents_segments_and_counts(self):
        _write_session(self.kdir, "aaaa-plan", [([_write_plan("260626-x")],
                                                 50000, 12000)])
        _write_session(self.kdir, "bbbb-work", [([_edit("260626-x"),
                                                  _promise("260626-X")],
                                                 60000, 15000)])
        _write_session(self.kdir, "cccc-adhoc", [
            ([{"type": "tool_use", "name": "Edit",
               "input": {"file_path": "README.md"}}], 20000, 5000)])
        _write_session(self.kdir, "dddd-loop", [([_edit("260626-x")], 40000, 9000)],
                       user_text="AGENTWARE_STAGE=loop-main work/260626-x")
        ag = CLI.derive_agents(self.kdir)
        plan_ids = {s["session_id"] for s in ag["plan"]["sessions"]}
        work_ids = {s["session_id"] for s in ag["work"]["sessions"]}
        self.assertEqual(plan_ids, {"aaaa-plan"})
        self.assertEqual(work_ids, {"bbbb-work", "cccc-adhoc"})  # loop excluded
        self.assertEqual(ag["work"]["attributed_count"], 1)      # adhoc not attrib
        self.assertIn("260626-x", ag["work"]["features"])

    def test_derive_plan_authoring_surfaces_authored_plans(self):
        # A pure planner authors plan A; a worker authors plan B then builds it.
        # BOTH plans appear in the planner OUTPUT (attributed by the authoring
        # action), with task counts read from the live plan.md.
        _write_session(self.kdir, "ee-plan-a", [([_write_plan("260626-aaa")],
                                                 50000, 12000)])
        _write_session(self.kdir, "ff-plan-b", [([_write_plan("260626-bbb"),
                                                  _edit("260626-bbb")],
                                                 80000, 20000)])
        for feat, body in (("260626-aaa", "# Plan\n- ✅ **1** x\n- ⬜ **2** y\n"),
                           ("260626-bbb", "# Plan\n- ✅ **1** x\n")):
            d = os.path.join(self.kdir, "work", feat)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as f:
                f.write(body)
        ag = CLI.derive_agents(self.kdir)
        plans = {p["feature"]: p for p in ag["plan"]["plans"]}
        self.assertEqual(set(plans), {"260626-aaa", "260626-bbb"})
        self.assertEqual(plans["260626-aaa"]["tasks_total"], 2)
        self.assertEqual(plans["260626-aaa"]["tasks_done"], 1)
        self.assertEqual(plans["260626-aaa"]["status"], "in_progress")
        self.assertEqual(plans["260626-bbb"]["status"], "complete")
        # The worker that built bbb is in WORK_AW (its build effort, not planning).
        self.assertIn("ff-plan-b",
                      {s["session_id"] for s in ag["work"]["sessions"]})


if __name__ == "__main__":
    unittest.main()
