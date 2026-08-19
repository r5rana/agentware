"""Transcript miner — hermetic end-to-end spine (Task 10, 260713).

Runner: python3 -m unittest tests.e2e.test_transcript_miner_e2e -v

Proves the FULL P2c spine on one hermetic fixture KB (temp dir + patched env,
NEVER the live KB, R-LOC-03):
  corpus -> mine (step m / `prefer mine`) enqueues quarantined recurrence +
  error-recovery candidates -> drain (step g) -> a tier-2 candidate yields a
  PROPOSED workorder whose text is FENCED in Context only -> a tier-3 kernel /
  gate-loosening candidate is REJECTED -> `prefer approve` flips proposed->approved
  WITHOUT auto-activating a source=user pref -> an idempotent re-run mines/drains
  nothing new (stable fps) -> a recurrence SPANNING two windows still emits on the
  2nd (F1) -> the dream-cadence tick SKIPS when not-due, the min-interval gate
  SKIPS a too-soon cycle, and the change-gate SKIPS audit+eval on a CLEAN unchanged
  tree but RUNS on a dirty tree (F2).

Feature 260722-miner-go-live (Task 2) adds ErrorRecoveryGoLiveE2E — the go-live
sim under PRODUCTION config (MINER_RECURRENCE_RETIRED=True, restored in its OWN
setUpClass so the module-level re-enable above cannot make the recurrence-silent
assertion vacuous): error->fix pairs on BOTH harnesses (Claude live.jsonl +
durable-transcript is_error via the reconciler; codex exit-code), the F-E
cross-SESSION dedup collapse asserted across TWO SEPARATE mine runs (the
persistent err_emitted_keys ledger, not just one batch), recurrence emits
NOTHING, a tier-3 kernel bait is REJECTED, mined candidates land queued ->
journal/proposed with NONE auto-activated, the capturable tier-2 loop CLOSES
(prefer review OPEN -> prefer approve -> prefer capture --from-workorder ->
source=user pref in prefer digest), and a re-run mines/activates nothing new.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from tests._fixtures import load_cli, build_synthetic_kb, run_cli
except ImportError:
    from _fixtures import load_cli, build_synthetic_kb, run_cli


def _rec(sid, turn, body):
    return ("[t] [session %s] [cwd /x] [origin=human] [turn=%s]\n%s\n\n"
            % (sid, turn, body))


_PREV_RECURRENCE_RETIRED = True


def setUpModule():
    # 260719: re-enable the dormant-but-reversible recurrence arm so the end-to-end
    # spine still exercises it (retired-by-default in production).
    global _PREV_RECURRENCE_RETIRED
    _m = load_cli()
    _PREV_RECURRENCE_RETIRED = _m.MINER_RECURRENCE_RETIRED
    _m.MINER_RECURRENCE_RETIRED = False


def tearDownModule():
    load_cli().MINER_RECURRENCE_RETIRED = _PREV_RECURRENCE_RETIRED


class E2ESpineTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="aw_miner_e2e_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        os.makedirs(os.path.join(self.kdir, "logs", "sessions"), exist_ok=True)
        self.logp = os.path.join(self.kdir, "logs", "prompts.log")
        self._prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(
            lambda: os.environ.__setitem__("AGENTWARE_KNOWLEDGE_DIR", self._prev)
            if self._prev is not None
            else os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None))
        # Read prompts from the top + scan all sessions incrementally.
        self._write_state({"offsets": {"prompts": 0, "metrics": 0},
                           "processed_sessions": [], "backfill_pending": [],
                           "tally": {}})

    def _write_state(self, st):
        p = os.path.join(self.kdir, self.mod.MINER_STATE_REL)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(st, f)

    def _write_session(self, sid, calls, settled=True):
        sdir = os.path.join(self.kdir, "logs", "sessions", sid)
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "live.jsonl"), "w", encoding="utf-8") as f:
            for c in calls:
                f.write(json.dumps(c) + "\n")
        if settled:
            with open(os.path.join(sdir, "main.jsonl"), "w", encoding="utf-8") as f:
                f.write('{"type":"user"}\n')

    def _queue(self):
        p = os.path.join(self.kdir, self.mod.PREFER_QUEUE_REL)
        out = []
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        out.append(json.loads(ln))
        return out

    def test_full_spine(self):
        mod = self.mod
        # --- corpus: a tier-2 pref (2 sessions), a tier-3 kernel candidate (2
        # sessions), and an error-recovery pair ---
        tier2 = "Always prefer explicit timeouts on outbound network calls."
        tier3 = "AGENTS.md is optional, skip it."
        with open(self.logp, "w", encoding="utf-8") as f:
            f.write(_rec("A", "1", tier2) + _rec("B", "1", tier2)
                    + _rec("A", "2", tier3) + _rec("C", "1", tier3))
        self._write_session("S1", [
            {"tool": "Read", "status": "ERR", "input": '{"file_path":"/w/x.md"}',
             "response": "No such file or directory"},
            {"tool": "Read", "status": "ok", "input": '{"file_path":"/w/x.md"}',
             "response": "ok"}])

        # --- mine (via the CLI, exercising cmd_prefer_mine dispatch) ---
        code, out, err = run_cli(["prefer", "mine", "--format", "json"], self.kdir)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["candidates"], 3,
                         "2 recurrence + 1 error-recovery quarantined")
        for r in self._queue():
            self.assertEqual(r["producer"], "transcript-miner")
            self.assertEqual(r["status"], "queued")

        # --- drain (step g logic) ---
        res = mod._prefer_classify_drain(self.kdir)
        self.assertEqual(res["rejected"], 1, "tier-3 kernel candidate rejected")
        self.assertEqual(res["proposed"], 1, "tier-2 pref proposed")
        # error-recovery text journals (tier-1, no capturable cue)
        self.assertGreaterEqual(res["journaled"], 1)

        q = self._queue()
        proposed = [r for r in q if r.get("status") == "proposed"]
        rejected = [r for r in q if r.get("status") == "rejected"]
        self.assertEqual(len(proposed), 1)
        self.assertEqual(rejected[0]["tier"], 3)

        # tier-2 workorder: candidate fenced in Context, never a Verify line
        wo = proposed[0]["workorder"]
        slug = wo.split("/", 1)[1] if "/" in wo else wo
        plan = os.path.join(self.kdir, "work", "auto", slug, "plan.md")
        with open(plan, encoding="utf-8") as f:
            plan_text = f.read()
        ctx = plan_text.split("## Context", 1)[1].split("## Target packages", 1)[0]
        self.assertIn(tier2, ctx)
        for ln in plan_text.splitlines():
            if "*Verify:*" in ln:
                self.assertNotIn(tier2, ln)

        # --- approve: proposed -> approved, NO source=user pref activated ---
        code, out, err = run_cli(["prefer", "approve", wo], self.kdir)
        self.assertEqual(code, 0, err)
        approved = [r for r in self._queue() if r.get("status") == "approved"]
        self.assertEqual(len(approved), 1)
        with open(os.path.join(self.kdir, "index.json"), encoding="utf-8") as f:
            idx = json.load(f)
        self.assertEqual(
            [e for e in idx.get("entries", []) if isinstance(e, dict)
             and e.get("source") == "user"], [],
            "approve must NOT auto-activate a source=user preference")

        # --- idempotent re-run: mine + drain produce nothing new ---
        self.assertEqual(mod._transcript_mine(self.kdir), 0, "no new candidates")
        res2 = mod._prefer_classify_drain(self.kdir)
        self.assertEqual((res2["proposed"], res2["rejected"]), (0, 0),
                         "re-drain is a stable no-op (only queued records act)")

    def test_recurrence_spans_two_windows(self):
        mod = self.mod
        body = "Always run a read-after-write on every mutation."
        with open(self.logp, "w", encoding="utf-8") as f:
            f.write(_rec("A", "1", body))
        self.assertEqual(mod._transcript_mine(self.kdir), 0, "window 1: below N")
        with open(self.logp, "a", encoding="utf-8") as f:
            f.write(_rec("B", "1", body))
        self.assertEqual(mod._transcript_mine(self.kdir), 1,
                         "window 2: cross-window accumulation reaches N (F1)")

    def test_cadence_and_gates_skip_when_not_due(self):
        mod = self.mod
        import datetime
        now = 2_000_000_000.0

        def iso(e):
            return datetime.datetime.fromtimestamp(
                e, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # A dream cycle 1h ago with a change fingerprint.
        fp = "HEAD:IDX"
        metrics = os.path.join(self.kdir, "logs", "metrics.jsonl")
        with open(metrics, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "dream", "ts": iso(now - 3600),
                                "finished": iso(now - 3600), "duration_s": 1.0,
                                "force": False, "steps": [], "change_fp": fp}) + "\n")
        # cadence tick: not due (<6h); min-interval: skip; change-gate on a clean
        # unchanged tree: skip; but a dirty tree runs.
        with mock.patch.dict(os.environ, {"AGENTWARE_DREAM": "1"}):
            self.assertFalse(mod._dream_cadence_due(self.kdir, now))
        self.assertIsNotNone(
            mod._dream_min_interval_skip(self.kdir, now, force=False))
        with mock.patch.object(mod, "_pkg_working_tree_dirty", return_value=False), \
             mock.patch.object(mod, "_dream_change_fp", return_value=fp):
            self.assertIsNotNone(mod._dream_change_gate_skip(self.kdir))
        with mock.patch.object(mod, "_pkg_working_tree_dirty", return_value=True), \
             mock.patch.object(mod, "_dream_change_fp", return_value=fp):
            self.assertIsNone(mod._dream_change_gate_skip(self.kdir),
                              "a dirty tree runs (F2)")


class ErrorRecoveryGoLiveE2E(unittest.TestCase):
    """260722-miner-go-live Task 2 — the error-recovery-ONLY go-live sim.

    Runs under PRODUCTION config: this module's setUpModule re-enables the
    retired recurrence arm for the legacy spine above, so this class RESTORES
    the shipped default (MINER_RECURRENCE_RETIRED=True) for its own tests and
    puts the module override back afterward — otherwise the "recurrence emits
    nothing" assertion below would be vacuously false-negative-proof.
    Hermetic: temp KB + patched HOME (the durable-transcript root), never the
    live KB (R-LOC-03)."""

    @classmethod
    def setUpClass(cls):
        cls._mod = load_cli()
        cls._prev_retired = cls._mod.MINER_RECURRENCE_RETIRED
        cls._mod.MINER_RECURRENCE_RETIRED = True     # PRODUCTION config

    @classmethod
    def tearDownClass(cls):
        cls._mod.MINER_RECURRENCE_RETIRED = cls._prev_retired

    def setUp(self):
        self.mod = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="aw_miner_golive_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        os.makedirs(os.path.join(self.kdir, "logs", "sessions"), exist_ok=True)
        self.logp = os.path.join(self.kdir, "logs", "prompts.log")
        # Patched HOME: _transcript_claude_path reads $HOME at CALL time, so an
        # empty temp home isolates the reconciler from the operator's real
        # ~/.claude/projects; AGENTWARE_KNOWLEDGE_DIR pins the KB for module-
        # level helpers called outside run_cli (which sets it itself).
        self.home = tempfile.mkdtemp(prefix="aw_home_golive_")
        self.addCleanup(shutil.rmtree, self.home, True)
        penv = mock.patch.dict(os.environ, {
            "HOME": self.home, "AGENTWARE_KNOWLEDGE_DIR": self.kdir})
        penv.start()
        self.addCleanup(penv.stop)
        # HERMETICITY: CONFIG_PATHS is bound at IMPORT time to the operator's
        # real ~/.agentware/config.env, so patching HOME above cannot redirect
        # it — the go-live gates' config.env rung would read a machine-level
        # AGENTWARE_MINER_ERRREC_DEDUP line and flip the sim. Redirect it to a
        # path that does not exist, and neutralize the higher-precedence env-var
        # rung too (the HOME patch.dict restores the whole dict on stop, so these
        # pops are undone at teardown).
        cfgp = mock.patch.object(
            self.mod, "CONFIG_PATHS",
            [os.path.join(self.kdir, "_no_such_config.env")])
        cfgp.start()
        self.addCleanup(cfgp.stop)
        for _k in (self.mod.MINER_ERRREC_DEDUP_KEY,
                   self.mod.MINER_RECURRENCE_MIN_LEN_KEY,
                   self.mod.MINER_RECURRENCE_CUE_REQUIRED_KEY,
                   self.mod.MINER_RECURRENCE_STOPWORDS_KEY):
            os.environ.pop(_k, None)
        # Offset-0 pre-seeded state (P1 trap: a self-seeded state parks at EOF).
        p = os.path.join(self.kdir, self.mod.MINER_STATE_REL)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"offsets": {"prompts": 0, "metrics": 0},
                       "processed_sessions": [], "backfill_pending": [],
                       "tally": {}}, f)

    # --- corpus writers ---------------------------------------------------
    def _write_session(self, sid, calls, settled=True):
        sdir = os.path.join(self.kdir, "logs", "sessions", sid)
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "live.jsonl"), "w", encoding="utf-8") as f:
            for c in calls:
                f.write(json.dumps(c) + "\n")
        if settled:
            with open(os.path.join(sdir, "main.jsonl"), "w",
                      encoding="utf-8") as f:
                f.write('{"type":"user"}\n')

    def _write_transcript(self, sid, calls, proj="-Users-x-proj"):
        """Durable Claude transcript under the patched HOME (real live shape:
        tool_use block then its tool_result by tool_use_id).
        calls: [(tool_name, input_obj, is_error, content, ts)]"""
        d = os.path.join(self.home, ".claude", "projects", proj)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, sid + ".jsonl"), "w", encoding="utf-8") as f:
            for i, (name, inp, is_err, content, ts) in enumerate(calls):
                tuid = "toolu_%s_%d" % (sid, i)
                f.write(json.dumps({
                    "type": "assistant", "sessionId": sid, "timestamp": ts,
                    "message": {"content": [
                        {"type": "tool_use", "id": tuid,
                         "name": name, "input": inp}]}}) + "\n")
                f.write(json.dumps({
                    "type": "user", "sessionId": sid, "timestamp": ts,
                    "message": {"content": [
                        {"type": "tool_result", "tool_use_id": tuid,
                         "is_error": is_err, "content": content}]}}) + "\n")

    def _err_fix(self, tool, target, sig):
        """Hook-style ERR->ok pair on the same tool + file target."""
        return [
            {"ts": "2026-07-22T09:00:00Z", "tool": tool, "status": "ERR",
             "input": json.dumps({"file_path": target}), "response": sig},
            {"ts": "2026-07-22T09:01:00Z", "tool": tool, "status": "ok",
             "input": json.dumps({"file_path": target}), "response": "ok"},
        ]

    def _queue(self):
        p = os.path.join(self.kdir, self.mod.PREFER_QUEUE_REL)
        out = []
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        out.append(json.loads(ln))
        return out

    def _state(self):
        with open(os.path.join(self.kdir, self.mod.MINER_STATE_REL),
                  encoding="utf-8") as f:
            return json.load(f)

    def _user_prefs(self):
        with open(os.path.join(self.kdir, "index.json"),
                  encoding="utf-8") as f:
            idx = json.load(f)
        # The index does NOT persist `source` (it lives in the .md frontmatter);
        # use the SAME predicate the digest injection uses.
        return [e for e in idx.get("entries", []) if isinstance(e, dict)
                and "preference" in (e.get("tags") or [])
                and self.mod._pref_entry_is_user_source(self.kdir, e)]

    # Recurrence bait: the SAME corpus the legacy spine proves EMITS when the
    # arm is enabled (test_full_spine's tier2 body, >= MINER_RECURRENCE_N
    # sessions) — so the "emits NOTHING" assertion below is non-vacuous.
    _RECURRENCE_BAIT = "Always prefer explicit timeouts on outbound network calls."

    def _seed_recurrence_bait(self):
        with open(self.logp, "w", encoding="utf-8") as f:
            f.write(_rec("A", "1", self._RECURRENCE_BAIT)
                    + _rec("B", "1", self._RECURRENCE_BAIT))

    def test_recurrence_bait_is_live_when_unretired(self):
        # Counterfactual guard: the bait corpus WOULD emit if the arm were on,
        # proving the production test's recurrence-silent assertion has teeth.
        self._seed_recurrence_bait()
        with mock.patch.object(self.mod, "MINER_RECURRENCE_RETIRED", False):
            self.assertEqual(self.mod._transcript_mine(self.kdir), 1)
        q = self._queue()
        self.assertEqual([r["signal"] for r in q], ["recurrence"])

    def test_error_recovery_only_go_live_spine(self):
        mod = self.mod
        self.assertTrue(mod.MINER_RECURRENCE_RETIRED,
                        "go-live sim MUST run under production config")

        # --- corpus ------------------------------------------------------
        self._seed_recurrence_bait()
        # M1 (Claude harness, review F-B): the hook logged ONLY the later fix;
        # the failure exists solely in the durable transcript (is_error) and is
        # reconstructed by the reconciler.
        self._write_session("cl-a", [
            {"ts": "2026-07-22T10:05:00Z", "tool": "Read", "status": "ok",
             "input": json.dumps({"file_path": "/w/app.md"}),
             "response": "ok"}])
        self._write_transcript("cl-a", [
            ("Read", {"file_path": "/w/app.md"}, True,
             "No such file or directory", "2026-07-22T10:00:00.111Z")])
        # M1 near-dup retries of the SAME failure mode across MORE sessions
        # (>= 3 total), on cosmetically-different concrete targets — the exact
        # 153->5 disease F-E must collapse.
        self._write_session("cl-b", self._err_fix(
            "Read", "/w/other.md", "No such file or directory"))
        self._write_session("cl-c", self._err_fix(
            "Read", "/w/third.md", "No such file or directory"))
        # tier-3 kernel bait: an error-recovery pair ON a kernel path — its
        # derived text names AGENTS.md, so the drain must REJECT it (never a
        # workorder, never a pref).
        self._write_session("cl-kernel", self._err_fix(
            "Edit", "/repo/AGENTS.md", "Permission denied"))
        # M2 (codex harness): exit-code ERR->ok with the bare-command input
        # shape codex-stream.py writes (no durable transcript).
        self._write_session("cx-a", [
            {"ts": "2026-07-22T11:00:00Z", "tool": "command_execution",
             "status": "ERR", "input": "make build",
             "response": "Exit code 2: missing separator"},
            {"ts": "2026-07-22T11:02:00Z", "tool": "command_execution",
             "status": "ok", "input": "make build", "response": "built"}])
        # tier-2 capturable: the error signature carries a genuine preference
        # cue, so the derived text passes the capture noise bar -> workorder.
        self._write_session("cx-cap", [
            {"ts": "2026-07-22T12:00:00Z", "tool": "Bash", "status": "ERR",
             "input": json.dumps({"command": "pip install requests"}),
             "response": "error: always pin dependency versions when installing"},
            {"ts": "2026-07-22T12:03:00Z", "tool": "Bash", "status": "ok",
             "input": json.dumps({"command": "pip install requests"}),
             "response": "installed"}])

        # --- mine run 1 (real CLI dispatch, non-dry) ----------------------
        code, out, err = run_cli(["prefer", "mine", "--format", "json"],
                                 self.kdir)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["candidates"], 4,
                         "M1 collapsed to ONE (F-E, 3 sessions) + codex M2 + "
                         "kernel bait + capturable = 4; recurrence silent")
        q = self._queue()
        self.assertEqual(len(q), 4)
        for r in q:
            self.assertEqual(r["producer"], "transcript-miner")
            self.assertEqual(r["status"], "queued")
            self.assertEqual(r["signal"], "error-recovery",
                             "recurrence must emit NOTHING (retired)")
        m1 = [r for r in q if "/w/app.md" in r["text"]]
        self.assertEqual(len(m1), 1, "the reconciled Claude pair emitted")
        self.assertEqual(m1[0]["source_sessions"], ["cl-a"],
                         "first-scanned session wins the collapsed emission")

        # --- F-E across TWO SEPARATE runs (the persistent ledger) ---------
        # A NEW session with the SAME failure mode on a LATER run derives a
        # FRESH _prefer_cand_fp (it embeds sid) — only the cross-run
        # err_emitted_keys ledger can suppress it.
        self._write_session("cl-d", self._err_fix(
            "Read", "/w/fourth.md", "No such file or directory"))
        code, out, err = run_cli(["prefer", "mine", "--format", "json"],
                                 self.kdir)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["candidates"], 0,
                         "same mode in a NEW session on a SECOND run must be "
                         "suppressed by the persistent ledger")
        self.assertEqual(len(self._queue()), 4)
        ledger = self._state()["err_emitted_keys"]
        # All 4 candidates emit AND are ledgered (so every mode stays bounded),
        # but the tier-3 kernel bait (/repo/AGENTS.md) is ledgered under its OWN
        # `errrec3|` namespace, so a drain-rejected bait can never occupy — and
        # thus blind — the key a legitimate same tool/class/signature recovery
        # would derive (adversarial-review rounds 1-2).
        self.assertEqual(len(ledger), 4)
        self.assertEqual(sum(1 for k in ledger if k.startswith("errrec3|")), 1,
                         "exactly the kernel bait lands in the tier-3 namespace")
        for k in ledger:
            self.assertTrue(k.startswith(("errrec|", "errrec3|")), k)
            self.assertNotIn("cl-", k, "ledger keys are session-INDEPENDENT")

        # --- drain (step g): journal / reject / propose -------------------
        res = mod._prefer_classify_drain(self.kdir)
        self.assertEqual(res["rejected"], 1, "kernel bait rejected (tier 3)")
        self.assertEqual(res["proposed"], 1, "capturable candidate proposed")
        self.assertEqual(res["journaled"], 2, "M1 + codex M2 journal (tier 1)")
        q = self._queue()
        rejected = [r for r in q if r.get("status") == "rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertIn("AGENTS.md", rejected[0]["text"])
        self.assertEqual(rejected[0]["tier"], 3)
        proposed = [r for r in q if r.get("status") == "proposed"]
        self.assertEqual(len(proposed), 1)
        self.assertIn("pin dependency versions", proposed[0]["text"])
        # QUARANTINE: nothing auto-activated as a source=user preference.
        self.assertEqual(self._user_prefs(), [],
                         "drain must NEVER auto-activate a pref")

        # --- loop-close: review OPEN -> approve -> capture -> digest ------
        code, out, err = run_cli(["prefer", "review", "--format", "json"],
                                 self.kdir)
        self.assertEqual(code, 0, err)
        rev = json.loads(out)
        self.assertEqual(rev["count"], 1, "ONE open item awaiting approve")
        item = rev["items"][0]
        self.assertEqual(item["status"], "proposed")
        self.assertEqual(item["signal"], "error-recovery")
        self.assertTrue(item["workorder_exists"])
        wo = item["workorder"]

        code, _out, err = run_cli(["prefer", "approve", wo], self.kdir)
        self.assertEqual(code, 0, err)
        code, out, err = run_cli(["prefer", "review", "--format", "json"],
                                 self.kdir)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["count"], 0,
                         "approved item leaves the open set")

        # The activation the emitted workorder's task runs (loop-closing seam):
        # the verb re-reads the exact text + scope from the APPROVED record.
        code, out, err = run_cli(
            ["prefer", "capture", "--from-workorder", wo], self.kdir)
        self.assertEqual(code, 0, err)
        self.assertIn("captured:", out)
        prefs = self._user_prefs()
        self.assertEqual(len(prefs), 1, "explicit approve->capture activates "
                                        "EXACTLY the reviewed candidate")
        self.assertTrue(prefs[0]["id"].startswith("learn-pref-global-"),
                        "no cwd on a mined candidate -> GLOBAL scope")
        code, out, err = run_cli(["prefer", "digest"], self.kdir)
        self.assertEqual(code, 0, err)
        self.assertIn("pin dependency versions", out,
                      "the activated pref injects via prefer digest")

        # --- idempotent re-run: mines/activates nothing new ---------------
        self.assertEqual(mod._transcript_mine(self.kdir), 0)
        res2 = mod._prefer_classify_drain(self.kdir)
        self.assertEqual((res2["proposed"], res2["rejected"],
                          res2["journaled"]), (0, 0, 0),
                         "re-drain is a stable no-op")
        self.assertEqual(len(self._user_prefs()), 1,
                         "re-run activates nothing new")

    def test_go_live_sim_is_hermetic_against_operator_config(self):
        # Hermeticity lock (adversarial-review): CONFIG_PATHS is import-bound to
        # the operator's real ~/.agentware/config.env; without redirecting it,
        # a machine-level AGENTWARE_MINER_ERRREC_DEDUP line would flip the go-live
        # assertions. setUp must redirect CONFIG_PATHS into the temp KB and
        # neutralize the higher-precedence env-var rung, so the F-E gate resolves
        # to the shipped default regardless of operator settings.
        for p in self.mod.CONFIG_PATHS:
            self.assertTrue(p.startswith(self.kdir),
                            "CONFIG_PATHS must be redirected into the temp KB, "
                            "never the operator's real config.env: %s" % p)
        self.assertNotIn(self.mod.MINER_ERRREC_DEDUP_KEY, os.environ)
        self.assertEqual(self.mod._transcript_errrec_gate()["mode"], "class")


if __name__ == "__main__":
    unittest.main()
