"""Plan-health + declared-vs-derived DRIFT derivation (Task 3).

Runner: python3 -m unittest tests.unit.ops.test_checkup_drift -v

Proven here: a `running` plan whose claim aged past STALLED_CLAIM_HOURS is a
stalled finding; done-vs-rotting counts rot = stalled-running + idle-draft while
EXCLUDING cancelled/superseded/done (legitimate terminal/closed) and treating
`ready` as healthy-waiting (audit L5-2); drift surfaces dream-declared-ON-but-not-
fired by REFERENCING the watcher (never recomputing, audit L4-4), running-but-
stalled, and version-drift that stays INERT when the updater is OFF (audit L3-3);
and the whole derivation is STRICTLY read-only (index + every Status header byte-
unchanged — a stalled plan is a report, never a state flip).
"""

import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest

try:
    from tests._fixtures import load_cli
except ImportError:
    from _fixtures import load_cli


def _iso(m, ep):
    import datetime
    return datetime.datetime.fromtimestamp(
        ep, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DriftTests(unittest.TestCase):
    def setUp(self):
        self.m = load_cli()
        self.kb = tempfile.mkdtemp(prefix="aw_ck_drift_")
        self.addCleanup(shutil.rmtree, self.kb, True)
        os.makedirs(os.path.join(self.kb, "learnings"))
        with open(os.path.join(self.kb, "index.json"), "w", encoding="utf-8") as f:
            json.dump({"entries": [], "tags": {}}, f)
        self.now = time.time()
        # Isolate config so resolve_dream()/resolve_updater() never read the
        # operator's real ~/.agentware/config.env.
        self._cfg = (self.m.HOME_CONFIG, self.m.CONFIG_PATHS)
        self.m.HOME_CONFIG = os.path.join(self.kb, ".agentware", "config.env")
        self.m.CONFIG_PATHS = (self.m.HOME_CONFIG,)
        self.addCleanup(self._restore_cfg)
        self._env = {k: os.environ.get(k)
                     for k in ("AGENTWARE_DREAM", "AGENTWARE_UPDATE")}
        for k in self._env:
            os.environ.pop(k, None)

    def _restore_cfg(self):
        self.m.HOME_CONFIG, self.m.CONFIG_PATHS = self._cfg
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _plan(self, feature, state, title="A plan", claim_age_h=None,
              mtime_age_d=None):
        d = os.path.join(self.kb, "work", feature)
        os.makedirs(d, exist_ok=True)
        claim = ""
        if claim_age_h is not None:
            claim = "> Claimed-by: t@h %s\n" % _iso(self.m, self.now -
                                                    claim_age_h * 3600)
        p = os.path.join(d, "plan.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# %s\n\n> Status: %s\n%s\n- ⬜ 1 x\n"
                    % (title, state, claim))
        if mtime_age_d is not None:
            t = self.now - mtime_age_d * 86400
            os.utime(p, (t, t))

    def test_stalled_running_detected(self):
        self._plan("260713-a", "running", claim_age_h=100)   # > 72h
        ph = self.m._checkup_plan_health(self.kb, self.now)
        self.assertEqual([s["feature"] for s in ph["stalled"]], ["260713-a"])
        self.assertEqual(ph["rotting"], 1)

    def test_fresh_running_not_stalled(self):
        self._plan("260713-a", "running", claim_age_h=1)     # < 72h
        ph = self.m._checkup_plan_health(self.kb, self.now)
        self.assertEqual(ph["stalled"], [])
        self.assertEqual(ph["rotting"], 0)

    def test_rot_excludes_terminal_and_closed_states(self):
        self._plan("260713-done", "done")
        self._plan("260713-cancelled", "cancelled")
        self._plan("260713-superseded", "superseded")
        self._plan("260713-ready", "ready")
        self._plan("260713-stalled", "running", claim_age_h=200)
        ph = self.m._checkup_plan_health(self.kb, self.now)
        self.assertEqual(ph["rotting"], 1, "only the stalled running plan rots")
        self.assertEqual(ph["done"], 1)
        self.assertEqual(ph["ready"], 1)

    def test_stale_draft_rots_but_fresh_draft_does_not(self):
        self._plan("260713-stale", "draft", mtime_age_d=200)   # > operator TTL 90d
        self._plan("260713-fresh", "draft", mtime_age_d=1)
        ph = self.m._checkup_plan_health(self.kb, self.now)
        self.assertEqual(ph["rotting"], 1)

    def test_drift_dream_declared_not_fired_references_watcher(self):
        os.environ["AGENTWARE_DREAM"] = "1"
        self.addCleanup(os.environ.pop, "AGENTWARE_DREAM", None)
        ph = self.m._checkup_plan_health(self.kb, self.now)
        watcher = {"fired_ok": False, "verdict": "no fresh cycle"}
        drift = self.m._checkup_drift(self.kb, self.now, watcher, ph)
        kinds = [d["kind"] for d in drift]
        self.assertIn("dream-declared-not-fired", kinds)

    def test_drift_running_but_stalled(self):
        self._plan("260713-a", "running", claim_age_h=100)
        ph = self.m._checkup_plan_health(self.kb, self.now)
        drift = self.m._checkup_drift(self.kb, self.now,
                                      {"fired_ok": True}, ph)
        stalled = [d for d in drift if d["kind"] == "running-but-stalled"]
        self.assertEqual(len(stalled), 1)
        self.assertEqual(stalled[0]["feature"], "260713-a")

    def test_version_drift_inert_when_updater_off(self):
        os.environ["AGENTWARE_UPDATE"] = "0"
        self.addCleanup(os.environ.pop, "AGENTWARE_UPDATE", None)
        os.environ.pop("AGENTWARE_DREAM", None)
        ph = self.m._checkup_plan_health(self.kb, self.now)
        drift = self.m._checkup_drift(self.kb, self.now, {"fired_ok": True}, ph)
        self.assertFalse([d for d in drift if d["kind"] == "version-drift"],
                         "version drift is inert when the updater is OFF")

    def test_read_only_index_and_status_headers_unchanged(self):
        self._plan("260713-a", "running", claim_age_h=100)
        idx = os.path.join(self.kb, "index.json")
        before_idx = hashlib.sha256(open(idx, "rb").read()).hexdigest()
        pf = os.path.join(self.kb, "work", "260713-a", "plan.md")
        before_hdr = [l for l in open(pf) if l.startswith("> Status:")]
        ph = self.m._checkup_plan_health(self.kb, self.now)
        self.m._checkup_drift(self.kb, self.now, {"fired_ok": True}, ph)
        self.assertEqual(hashlib.sha256(open(idx, "rb").read()).hexdigest(),
                         before_idx)
        self.assertEqual([l for l in open(pf) if l.startswith("> Status:")],
                         before_hdr)


if __name__ == "__main__":
    unittest.main()
