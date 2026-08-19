"""Hermetic end-to-end for the checkup meta-audit service (Task 9).

Runner: python3 -m unittest tests.e2e.test_checkup_e2e -v

Builds a hermetic fixture KB (a stalled plan; two reject-fixture plans; a dream
journal with an ALLOWLISTED metric frozen across >= MIN_FROZEN_CYCLES present
cycles alongside an EXCLUDED constant; a fired corpus-size trigger) and asserts,
with a STUBBED _review_invoke_agent (no real model):
  (a) the report is written and names the stalled plan, the frozen metric (and NOT
      the excluded constant), and the fired trigger;
  (b) a tier-2 workorder under work/auto/ carries the candidate fenced in Context
      only (no --target, no Verify/exec line);
  (c) the LLM narrative is REPORT-ONLY — its kernel-path observation appears in the
      report but produces NO workorder; and deterministic candidates naming a
      kernel path / worded to loosen a gate are DROPPED into Rejected;
  (d) direct-mutation ban — index.json content-hash + every EXISTING plan Status
      header byte-unchanged, no kernel path written;
  (e) a re-run is byte-identical (no double emit);
  (f) AGENTWARE_CHECKUP=0 -> total no-op, tick job SKIPS;
  (g) LLM degradation — the subprocess primitive raising TimeoutExpired / returning
      nonzero makes the real seam return '', the report OMITS the narrative, and
      deterministic emit STILL happens; argv routes through the resolved CLI;
  (h) hook-execution — CHECKUP=1 + fresh report surfaces the banner; OFF / no-fresh
      report is byte-identical and writes no .ack;
  (i) empty-KB safety + the watcher n=1/n>=MIN frozen boundaries.
"""

import glob
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

try:
    from tests._fixtures import load_cli, REPO_ROOT
except ImportError:
    from _fixtures import load_cli, REPO_ROOT


def _iso(ep):
    import datetime
    return datetime.datetime.fromtimestamp(
        ep, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stub_llm(role, persona, prompt, cfg, timeout=None, cwd=None, **kwargs):
    # a narrative whose observation NAMES a kernel path — must stay REPORT-ONLY.
    return json.dumps({"narrative": "Trends are flat across the window.",
                       "observations": ["consider editing scripts/agentware to "
                                        "raise the tier-2 workorder cap"]})


class CheckupE2ETests(unittest.TestCase):
    def setUp(self):
        self.m = load_cli()
        self.base = tempfile.mkdtemp(prefix="aw_ck_e2e_")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.home = os.path.join(self.base, "home")
        self.kb = os.path.join(self.base, "kb")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.kb, "learnings"))
        os.makedirs(os.path.join(self.kb, "logs"))
        self.now = time.time()
        # config isolation + a small corpus threshold so a handful of entries fires
        self._cfg = (self.m.HOME_CONFIG, self.m.CONFIG_PATHS,
                     self.m.CHECKUP_CORPUS_ENTRIES_MAX)
        self.m.HOME_CONFIG = os.path.join(self.home, ".agentware", "config.env")
        self.m.CONFIG_PATHS = (self.m.HOME_CONFIG,)
        self.m.CHECKUP_CORPUS_ENTRIES_MAX = 2
        # 260722 spawn-resilience: the checkup caller passes retries=0 (single
        # attempt — a background report-only job degrades cheaply), so the (g)
        # degradation tests that patch subprocess.run to raise TimeoutExpired
        # never traverse the retry path. The no-op REVIEW_SLEEP stays as
        # defense-in-depth for the zero-real-sleep-in-tests invariant
        # (load_cli() is CACHED: restore in _restore, no leaks).
        self._sleep = self.m.REVIEW_SLEEP
        self.m.REVIEW_SLEEP = lambda *_: None
        self._env = {k: os.environ.get(k) for k in
                     ("AGENTWARE_KNOWLEDGE_DIR", "AGENTWARE_CHECKUP",
                      "AGENTWARE_CHECKUP_LLM", "AGENTWARE_DREAM", "AGENTWARE_CLI")}
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kb
        os.environ["AGENTWARE_CHECKUP"] = "1"
        os.environ.pop("AGENTWARE_CHECKUP_LLM", None)   # LLM ON (stubbed)
        os.environ.pop("AGENTWARE_DREAM", None)
        os.environ.pop("AGENTWARE_CLI", None)
        self.addCleanup(self._restore)
        self._build_fixture()

    def _restore(self):
        (self.m.HOME_CONFIG, self.m.CONFIG_PATHS,
         self.m.CHECKUP_CORPUS_ENTRIES_MAX) = self._cfg
        self.m.REVIEW_SLEEP = self._sleep
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _build_fixture(self):
        # corpus OVER the (lowered) trigger threshold
        entries = [{"id": "learn-%d" % i, "category": "learnings",
                    "path": "learnings/l%d.md" % i, "created": "2020-01-01"}
                   for i in range(5)]
        with open(os.path.join(self.kb, "index.json"), "w", encoding="utf-8") as f:
            json.dump({"entries": entries, "tags": {}}, f)
        with open(os.path.join(self.kb, ".initialized"), "w") as f:
            f.write("t\n")
        with open(os.path.join(self.kb, "MAIN.md"), "w") as f:
            f.write("# KB\n**Handle**: tester\n")
        self._plan("260713-widget", "running", "Add a widget", claim_age_h=200)
        self._plan("260713-kernel", "running",
                   "Rewrite scripts/agentware and disable the steering lint gate",
                   claim_age_h=200)
        self._plan("260713-loosen", "running",
                   "Disable the review gate and make the tests optional",
                   claim_age_h=200)
        # dream journal: d.reliability FROZEN across MIN present cycles + an EXCLUDED
        # constant (e.unpromoted_markers=0) that must NOT flag
        jr = os.path.join(self.kb, self.m.DREAM_JOURNAL_REL)
        os.makedirs(os.path.dirname(jr), exist_ok=True)
        with open(jr, "w", encoding="utf-8") as f:
            f.write("# Dream Journal\n\n")
            for k in range(self.m.MIN_FROZEN_CYCLES):
                ts = _iso(self.now - (k + 1) * 24 * 3600)
                f.write("## dream %s\n\n- force: False\n" % ts)
                f.write("- step d (eval-record): ok  reliability=0.96 rows_added=1\n")
                f.write("- step e (detect-report): ok  unpromoted_markers=0 stale=0\n\n")

    def _plan(self, feature, state, title, claim_age_h=None):
        d = os.path.join(self.kb, "work", feature)
        os.makedirs(d, exist_ok=True)
        claim = ""
        if claim_age_h is not None:
            claim = "> Claimed-by: t@h %s\n" % _iso(self.now - claim_age_h * 3600)
        with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as f:
            f.write("# %s\n\n> Status: %s\n%s\n- ⬜ 1 x\n" % (title, state, claim))

    def _run(self):
        ns = self.m.argparse.Namespace(format="json", status_line=False)
        rc, out = self.m._dream_capture(self.m.cmd_checkup, ns)
        return rc, json.loads(out)

    def _report(self):
        return open(os.path.join(self.kb, self.m.CHECKUP_REPORT_REL)).read()

    def _workorders(self):
        return sorted(glob.glob(os.path.join(self.kb, "work", "auto", "*",
                                             "plan.md")))

    def _existing_status_headers(self):
        out = {}
        for p in glob.glob(os.path.join(self.kb, "work", "*", "plan.md")):
            if "/auto/" in p:
                continue
            for ln in open(p, encoding="utf-8"):
                if ln.startswith("> Status:"):
                    out[p] = ln
                    break
        return out

    # --- (a)-(e) main path ---------------------------------------------------
    def test_main_path_report_emit_and_bans(self):
        idxp = os.path.join(self.kb, "index.json")
        before_idx = hashlib.sha256(open(idxp, "rb").read()).hexdigest()
        before_hdrs = self._existing_status_headers()
        with mock.patch.object(self.m, "_review_invoke_agent", side_effect=_stub_llm):
            rc, summary = self._run()
            report = self._report()
            # (a) report names the stalled plan, the frozen metric, the fired trigger
            self.assertIn("work/260713-widget/plan.md", report)
            self.assertIn("FROZEN: d.reliability", report)
            self.assertNotIn("unpromoted_markers", report)   # excluded constant not flagged
            self.assertIn("corpus-size: 5 / 2 — FIRED", report)
            # (b) a tier-2 workorder exists with the candidate fenced in Context only
            wos = self._workorders()
            self.assertTrue(wos)
            widget = [w for w in wos if "widget" in w]
            self.assertTrue(widget)
            plan = open(widget[0]).read()
            self.assertIn("## Context", plan)
            self.assertIn("Review a checkup meta-audit finding", plan)
            tgt = plan.split("## Target packages")[1].split("---")[0]
            self.assertNotIn("scripts/agentware", tgt)
            # (c) LLM observation is report-only: in the report, NOT a workorder
            self.assertTrue(summary["narrative"])
            self.assertIn("scripts/agentware", report)   # via the observation
            for w in wos:
                self.assertNotIn("raise the tier-2 workorder cap", open(w).read())
            # deterministic reject: kernel-path AND gate-loosening candidates dropped
            self.assertIn("rejected: names a kernel path", report)
            self.assertIn("rejected: reads as weakening a governance gate", report)
            self.assertFalse([w for w in wos if "kernel" in w or "loosen" in w])
            # (d) direct-mutation ban
            self.assertEqual(hashlib.sha256(open(idxp, "rb").read()).hexdigest(),
                             before_idx, "index.json content-hash unchanged")
            self.assertEqual(self._existing_status_headers(), before_hdrs,
                             "existing plan Status headers byte-unchanged")
            # (e) re-run byte-identical, no double emit
            n1 = len(wos)
            self._run()
            self.assertEqual(self._report(), report, "re-run byte-identical")
            self.assertEqual(len(self._workorders()), n1, "no double emit")

    # --- (f) OFF -> total no-op ----------------------------------------------
    def test_off_is_total_noop(self):
        os.environ["AGENTWARE_CHECKUP"] = "0"
        by = {r["job"]: r for r in self.m._tick_dispatch(self.kb, now=self.now)}
        self.assertEqual(by["checkup"]["status"], "disabled")
        # status-line short-circuits: no output, no report, no .ack write
        rc = self.m._checkup_status_line(self.kb)
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(self.kb, self.m.CHECKUP_ACK_REL)))

    # --- (g) LLM degradation --------------------------------------------------
    def test_llm_degradation_deterministic_emit_survives(self):
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=1)
        with mock.patch.object(self.m.subprocess, "run", side_effect=boom):
            rc, summary = self._run()
        self.assertFalse(summary["narrative"], "narrative omitted on timeout")
        self.assertNotIn("## LLM narrative", self._report())
        self.assertTrue(self._workorders(), "deterministic emit still happens")

    def test_llm_nonzero_exit_returns_empty(self):
        class _CP:
            returncode = 1
            stdout = "boom"
            stderr = "err"
        with mock.patch.object(self.m.subprocess, "run", return_value=_CP()):
            got = self.m._review_invoke_agent("checkup", "p", "prompt", {})
        self.assertEqual(got, "", "a nonzero exit is a FAILED run -> '' (fail-closed)")

    def test_llm_argv_routes_through_resolved_cli(self):
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
        self.assertEqual(captured["cmd"][0], "codex",
                         "argv must route through the resolved CLI, not `claude`")

    # --- (h) hook-execution ---------------------------------------------------
    def _hook_env(self, checkup):
        env = dict(os.environ)
        env["HOME"] = self.home
        env["AGENTWARE_KNOWLEDGE_DIR"] = self.kb
        env["AGENTWARE_CHECKUP"] = checkup
        for k in ("AGENTWARE_KB_AUTOCOMMIT", "AGENTWARE_KB_PUSH",
                  "AGENTWARE_INVOKED_FROM", "AGENTWARE_DREAM", "AGENTWARE_UPDATE"):
            env.pop(k, None)
        return env

    def _run_hook(self, env):
        hook = os.path.join(REPO_ROOT, "scripts", "hooks", "session-start.sh")
        return subprocess.run(["bash", hook], input="{}", capture_output=True,
                              text=True, env=env, timeout=20)

    def test_hook_surfaces_banner_only_when_fresh(self):
        rpath = os.path.join(self.kb, self.m.CHECKUP_REPORT_REL)
        ackp = os.path.join(self.kb, self.m.CHECKUP_ACK_REL)
        # clean slate — no report yet
        for p in (rpath, ackp):
            if os.path.exists(p):
                os.remove(p)
        banner = "fresh self-exam report is ready"
        # OFF -> no banner, no .ack write
        off = self._run_hook(self._hook_env("0"))
        self.assertEqual(off.returncode, 0, off.stderr)
        self.assertNotIn(banner, off.stdout)
        self.assertFalse(os.path.exists(ackp))
        # ON but NO report -> byte-identical to OFF, still no .ack
        on_noreport = self._run_hook(self._hook_env("1"))
        self.assertNotIn(banner, on_noreport.stdout)
        self.assertFalse(os.path.exists(ackp))
        self.assertEqual(on_noreport.stdout, off.stdout,
                         "ON-without-fresh-report is byte-identical to OFF")
        # now a FRESH report exists -> banner appears (ON)
        os.makedirs(os.path.dirname(rpath), exist_ok=True)
        with open(rpath, "w", encoding="utf-8") as f:
            f.write("# checkup — periodic self-examination (latest)\n\nbody\n")
        on_fresh = self._run_hook(self._hook_env("1"))
        self.assertIn(banner, on_fresh.stdout, "banner surfaces when fresh")

    # --- (i) empty-KB safety + watcher boundaries ----------------------------
    def test_empty_kb_clean_and_zero_workorders(self):
        empty = os.path.join(self.base, "empty")
        os.makedirs(os.path.join(empty, "learnings"))
        with open(os.path.join(empty, "index.json"), "w") as f:
            json.dump({"entries": [], "tags": {}}, f)
        with open(os.path.join(empty, ".initialized"), "w") as f:
            f.write("t\n")
        with open(os.path.join(empty, "MAIN.md"), "w") as f:
            f.write("# KB\n")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = empty
        os.environ["AGENTWARE_CHECKUP_LLM"] = "0"
        rc, summary = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(summary["proposed"], [])
        self.assertEqual(summary["rejected"], 0)
        self.assertIn("insufficient data",
                      open(os.path.join(empty, self.m.CHECKUP_REPORT_REL)).read())
        self.assertEqual(
            glob.glob(os.path.join(empty, "work", "auto", "*", "plan.md")), [])

    def test_watcher_frozen_boundaries(self):
        mk = lambda vals: [{"ts": _iso(self.now - (i + 1) * 86400),
                            "metrics": {"d.reliability": v}}
                           for i, v in enumerate(vals)]
        # n=1 -> no flag
        self.assertEqual(self.m._checkup_frozen_metrics(mk([0.96])), [])
        # n>=MIN identical -> flag
        ident = mk([0.96] * self.m.MIN_FROZEN_CYCLES)
        self.assertEqual([f["metric"] for f in self.m._checkup_frozen_metrics(ident)],
                         ["d.reliability"])
        # n>=MIN differing -> no flag
        diff = mk([0.90, 0.94, 0.99][:self.m.MIN_FROZEN_CYCLES])
        self.assertEqual(self.m._checkup_frozen_metrics(diff), [])


if __name__ == "__main__":
    unittest.main()
