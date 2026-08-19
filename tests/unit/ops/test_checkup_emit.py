"""REPORT-ONLY LLM pass + DETERMINISTIC-ONLY workorder emit (Task 7).

Runner: python3 -m unittest tests.unit.ops.test_checkup_emit -v

Proven here: a DETERMINISTIC candidate emits a tier-2 workorder through the
EXISTING `plan new --auto --context` seam with the candidate fenced in Context
ONLY (never a --target kernel path, always a title); the tier-3 reject scan DROPS a
candidate that names a kernel path OR reads as gate-weakening and surfaces it as
`rejected:` (never emitted); emit is idempotent (no double emit); the bounded LLM
pass is REPORT-ONLY — a kernel-path OBSERVATION lands in the report but produces NO
workorder; and the seam degrades to '' on TimeoutExpired/nonzero so the narrative is
omitted while deterministic emit still happens. Hermetic: monkeypatched seam / a
patched subprocess primitive — no test spawns a real model.
"""

import glob
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

try:
    from tests._fixtures import load_cli
except ImportError:
    from _fixtures import load_cli


def _iso(ep):
    import datetime
    return datetime.datetime.fromtimestamp(
        ep, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EmitTests(unittest.TestCase):
    def setUp(self):
        self.m = load_cli()
        self.base = tempfile.mkdtemp(prefix="aw_ck_emit_")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.kb = os.path.join(self.base, "kb")
        os.makedirs(os.path.join(self.kb, "learnings"))
        os.makedirs(os.path.join(self.kb, "logs"))
        with open(os.path.join(self.kb, "index.json"), "w", encoding="utf-8") as f:
            json.dump({"entries": [], "tags": {}}, f)
        with open(os.path.join(self.kb, ".initialized"), "w") as f:
            f.write("t\n")
        with open(os.path.join(self.kb, "MAIN.md"), "w") as f:
            f.write("# KB\n**Handle**: tester\n")
        self.now = time.time()
        # config isolation + env
        self._cfg = (self.m.HOME_CONFIG, self.m.CONFIG_PATHS)
        self.m.HOME_CONFIG = os.path.join(self.base, "home", ".agentware",
                                          "config.env")
        self.m.CONFIG_PATHS = (self.m.HOME_CONFIG,)
        # 260722 spawn-resilience: the checkup caller passes retries=0 (single
        # attempt), so TimeoutExpired never traverses the retry path here; the
        # no-op REVIEW_SLEEP stays as defense-in-depth for the zero-real-sleep
        # invariant (module is cached across suites: restore in _restore).
        self._sleep = self.m.REVIEW_SLEEP
        self.m.REVIEW_SLEEP = lambda *_: None
        self._env = {k: os.environ.get(k) for k in
                     ("AGENTWARE_KNOWLEDGE_DIR", "AGENTWARE_CHECKUP",
                      "AGENTWARE_CHECKUP_LLM", "AGENTWARE_DREAM", "AGENTWARE_CLI")}
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kb
        os.environ["AGENTWARE_CHECKUP"] = "1"
        os.environ["AGENTWARE_CHECKUP_LLM"] = "0"   # no spawn unless a test opts in
        os.environ.pop("AGENTWARE_DREAM", None)
        self.addCleanup(self._restore)

    def _restore(self):
        self.m.HOME_CONFIG, self.m.CONFIG_PATHS = self._cfg
        self.m.REVIEW_SLEEP = self._sleep
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _stalled_plan(self, feature, title):
        d = os.path.join(self.kb, "work", feature)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as f:
            f.write("# %s\n\n> Status: running\n> Claimed-by: t@h %s\n\n- ⬜ 1 x\n"
                    % (title, _iso(self.now - 200 * 3600)))

    def _run(self):
        ns = self.m.argparse.Namespace(format="json", status_line=False)
        rc, out = self.m._dream_capture(self.m.cmd_checkup, ns)
        return rc, json.loads(out)

    def _workorders(self):
        return sorted(glob.glob(os.path.join(self.kb, "work", "auto", "*",
                                             "plan.md")))

    def _report(self):
        return open(os.path.join(self.kb, self.m.CHECKUP_REPORT_REL)).read()

    # --- deterministic emit ---------------------------------------------------
    def test_stalled_candidate_emits_tier2_context_only(self):
        self._stalled_plan("260713-widget", "Add a widget")
        rc, summary = self._run()
        self.assertEqual(rc, 0)
        wos = self._workorders()
        self.assertEqual(len(wos), 1)
        plan = open(wos[0]).read()
        self.assertIn("Review a checkup meta-audit finding", plan)   # title present
        self.assertIn("Add a widget", plan)                          # candidate in Context
        self.assertIn("> Status: draft", plan)                       # its own initial state
        # Target section defaults to the kdir, NEVER a kernel path / --target (the
        # [kb] task boilerplate legitimately names scripts/agentware, so scope the
        # kernel-path check to the Target section).
        target_block = plan.split("## Target packages")[1].split("---")[0]
        self.assertIn(self.kb, target_block)
        self.assertNotIn("scripts/agentware", target_block)
        # the candidate lands in the Context section (fenced), never on a Verify/
        # executable line
        ctx_block = plan.split("## Context")[1].split("## Target")[0]
        self.assertIn("Add a widget", ctx_block)
        for ln in plan.splitlines():
            if "Add a widget" in ln:
                self.assertNotIn("Verify:", ln)
                self.assertFalse(ln.lstrip().startswith("- ⬜"))

    def test_reject_kernel_path_candidate_dropped(self):
        self._stalled_plan("260713-k",
                           "Rewrite scripts/agentware and disable the steering lint gate")
        rc, summary = self._run()
        self.assertEqual(len(self._workorders()), 0, "kernel-path candidate not emitted")
        self.assertGreaterEqual(summary["rejected"], 1)
        self.assertIn("rejected: names a kernel path", self._report())

    def test_reject_gate_loosening_candidate_dropped(self):
        self._stalled_plan("260713-g",
                           "Disable the review gate and make the tests optional")
        rc, summary = self._run()
        self.assertEqual(len(self._workorders()), 0, "gate-loosening candidate not emitted")
        self.assertIn("rejected: reads as weakening a governance gate", self._report())

    def test_idempotent_no_double_emit(self):
        self._stalled_plan("260713-widget", "Add a widget")
        self._run()
        n1 = len(self._workorders())
        b1 = self._report()
        self._run()
        self.assertEqual(len(self._workorders()), n1, "no double emit")
        self.assertEqual(self._report(), b1, "byte-identical re-run")

    def test_reject_reason_helper(self):
        self.assertIn("kernel", self.m._checkup_reject_reason("edit scripts/agentware"))
        self.assertIn("gate", self.m._checkup_reject_reason(
            "make the review gate optional"))
        self.assertIsNone(self.m._checkup_reject_reason("investigate a stalled plan"))

    # --- REPORT-ONLY LLM ------------------------------------------------------
    def test_llm_observation_never_emits_a_workorder(self):
        self._stalled_plan("260713-widget", "Add a widget")
        os.environ["AGENTWARE_CHECKUP_LLM"] = "1"

        def stub(role, persona, prompt, cfg, timeout=None, cwd=None, **kwargs):
            return json.dumps({"narrative": "flat",
                               "observations": ["edit scripts/agentware to raise "
                                                "the tier-2 workorder cap"]})
        with mock.patch.object(self.m, "_review_invoke_agent", side_effect=stub):
            rc, summary = self._run()
        self.assertTrue(summary["narrative"])
        report = self._report()
        self.assertIn("scripts/agentware", report)          # observation in report
        # exactly ONE workorder (the deterministic stalled plan) — NOT the LLM obs
        wos = self._workorders()
        self.assertEqual(len(wos), 1)
        for w in wos:
            self.assertNotIn("tier-2 workorder cap", open(w).read())

    def test_seam_degrades_to_empty_on_timeout_but_emit_still_happens(self):
        self._stalled_plan("260713-widget", "Add a widget")
        os.environ["AGENTWARE_CHECKUP_LLM"] = "1"

        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=1)
        with mock.patch.object(self.m.subprocess, "run", side_effect=boom):
            rc, summary = self._run()
        self.assertFalse(summary["narrative"], "narrative omitted on timeout")
        self.assertEqual(len(self._workorders()), 1,
                         "deterministic emit still happens (audit L8-3)")
        self.assertNotIn("## LLM narrative", self._report())

    def test_seam_argv_routes_through_resolved_cli(self):
        # AGENTWARE_CLI=codex -> the seam's argv[0] must be codex, never a hardcoded
        # `claude -p` (audit L2-1). Capture the built command.
        os.environ["AGENTWARE_CLI"] = "codex"
        captured = {}

        class _CP:
            returncode = 0
            stdout = json.dumps({"narrative": "n", "observations": []})
            stderr = ""

        def fake_run(cmd, **k):
            captured["cmd"] = cmd
            return _CP()
        with mock.patch.object(self.m.subprocess, "run", side_effect=fake_run):
            self.m._checkup_llm_narrative({"flywheel": {}, "plan_health": {},
                                           "watcher": {}, "triggers": []}, {})
        self.assertEqual(captured["cmd"][0], "codex")


if __name__ == "__main__":
    unittest.main()
