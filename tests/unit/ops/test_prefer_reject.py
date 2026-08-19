"""Tests for `prefer reject <workorder>` — the governed counterpart to `prefer
approve` (feature 260719-prefer-precision-and-reject, Task 1).

Reject flips a `proposed` candidate -> `rejected` AND cancels its emitted
`work/auto/pref-*` workorder, deliberately KEEPING the plan.md on disk as the
`_prefer_emit_workorder` re-emit idempotency sentinel — so a later classify drain
never resurrects a rejected candidate. This closes the proposed-noise dead end
that previously required an ungoverned hand-edit of the prefer-queue.
"""

import json
import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, build_synthetic_kb, run_cli
except ImportError:  # allow `python3 -m unittest tests.unit.ops.test_prefer_reject`
    from _fixtures import load_cli, build_synthetic_kb, run_cli


def _queue_rec(text, status="queued", sid="sess-AAAA", turn="1"):
    return {
        "ts": "2026-07-13T00:00:00Z", "sid": sid, "cwd": "", "project": None,
        "turn_id": turn, "text": text, "matched_pattern": "always",
        "status": status, "producer": "stop-hook-regex",
    }


class TestPreferReject(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_prefer_reject_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = load_cli()
        self.qpath = os.path.join(self.kdir, self.mod.PREFER_QUEUE_REL)
        # The drain + the reject's plan cancel resolve the kdir from the env.
        self._prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._prev is None:
            os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        else:
            os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self._prev

    # --- helpers -------------------------------------------------------------
    def _write_queue(self, recs):
        os.makedirs(os.path.dirname(self.qpath), exist_ok=True)
        with open(self.qpath, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def _read_queue(self):
        with open(self.qpath, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def _auto_plans(self):
        base = os.path.join(self.kdir, "work", "auto")
        if not os.path.isdir(base):
            return []
        return sorted(n for n in os.listdir(base)
                      if os.path.isfile(os.path.join(base, n, "plan.md")))

    def _plan_state(self, feature_basename):
        p = os.path.join(self.kdir, "work", "auto", feature_basename, "plan.md")
        with open(p, encoding="utf-8") as f:
            return self.mod._read_plan_status(f.read())[0]

    def _proposed_workorder(self, text="always pin dependency versions in lockfiles"):
        self._write_queue([_queue_rec(text)])
        self.assertEqual(self.mod._prefer_classify_drain(self.kdir).get("proposed"), 1)
        wo = self._read_queue()[0]["workorder"]
        self.assertTrue(wo, "a proposed record records its workorder")
        return wo

    def _reject(self, wo, reason="regex false-positive noise"):
        return run_cli(["prefer", "reject", wo, "--reason", reason,
                        "--format", "json"], self.kdir)

    # --- behavior ------------------------------------------------------------
    def test_reject_flips_status_and_cancels_workorder_keeping_sentinel(self):
        wo = self._proposed_workorder()
        code, out, err = self._reject(wo)
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "rejected")
        self.assertTrue(payload["workorder_cancelled"])
        self.assertEqual(self._read_queue()[0]["status"], "rejected")
        # the plan.md SURVIVES (re-emit sentinel) and is cancelled
        plans = self._auto_plans()
        self.assertEqual(len(plans), 1, "plan.md kept as the re-emit sentinel")
        self.assertEqual(self._plan_state(plans[0]), "cancelled")

    def test_reject_clears_the_prefer_review_open_set(self):
        wo = self._proposed_workorder()
        _c, o0, _e = run_cli(["prefer", "review", "--format", "json"], self.kdir)
        self.assertEqual(json.loads(o0)["count"], 1, "proposed shows before reject")
        self.assertEqual(self._reject(wo)[0], 0)
        _c, o1, _e = run_cli(["prefer", "review", "--format", "json"], self.kdir)
        self.assertEqual(json.loads(o1)["count"], 0, "review clean after reject")

    def test_reject_is_re_emit_safe(self):
        wo = self._proposed_workorder()
        self.assertEqual(self._reject(wo)[0], 0)
        before = self._auto_plans()
        self.mod._prefer_classify_drain(self.kdir)  # a second drain
        self.assertEqual(self._auto_plans(), before,
                         "rejected candidate must not re-emit a workorder")
        self.assertEqual(self._read_queue()[0]["status"], "rejected")

    def test_re_emit_safety_via_cancelled_sentinel(self):
        # audit #1: a rejected candidate that gets RE-QUEUED (e.g. a watermark reset)
        # must NOT resurrect — the drain's state-aware sentinel sees the cancelled
        # plan.md and re-marks it rejected, never proposed.
        wo = self._proposed_workorder()
        self.assertEqual(self._reject(wo)[0], 0)
        # simulate a re-queue of the SAME candidate (same text -> same slug/plan.md)
        q = self._read_queue()
        q[0]["status"] = "queued"
        q[0].pop("workorder", None)
        with open(self.qpath, "w", encoding="utf-8") as f:
            for r in q:
                f.write(json.dumps(r) + "\n")
        res = self.mod._prefer_classify_drain(self.kdir)
        self.assertEqual(res.get("proposed"), 0, "must NOT resurrect a rejected candidate")
        self.assertEqual(res.get("rejected"), 1)
        self.assertEqual(self._read_queue()[0]["status"], "rejected")
        _c, o, _e = run_cli(["prefer", "review", "--format", "json"], self.kdir)
        self.assertEqual(json.loads(o)["count"], 0, "review stays clean")

    def test_reject_only_touches_the_target_record(self):
        self._write_queue([
            _queue_rec("always pin dependency versions in lockfiles"),
            _queue_rec("always run the full suite before merge",
                       sid="sess-BBBB", turn="2")])
        self.mod._prefer_classify_drain(self.kdir)
        wos = [r["workorder"] for r in self._read_queue()]
        self.assertEqual(self._reject(wos[0])[0], 0)
        after = {r["workorder"]: r["status"] for r in self._read_queue()}
        self.assertEqual(after[wos[0]], "rejected")
        self.assertEqual(after[wos[1]], "proposed", "the other candidate is untouched")

    def test_reject_preserves_and_can_reject_unicode_line_separator_records(self):
        # round-2 #1/#3/#4: a record whose text contains a Unicode line separator
        # (U+2028) is ONE physical \n-terminated JSONL line the whole pipeline
        # leaves verbatim; reject must not shred it (the splitlines() bug), must not
        # destroy an UNRELATED record, and must be able to reject such a record.
        ls = "\u2028"
        self._write_queue([
            _queue_rec("always pin versions" + ls + "and never use ranges",
                       sid="sess-LS", turn="1"),
            _queue_rec("always run the linter each morning", sid="sess-B", turn="2")])
        self.assertEqual(self.mod._prefer_classify_drain(self.kdir).get("proposed"), 2)
        wo = {r["text"].split(ls)[0]: r["workorder"] for r in self._read_queue()}
        # reject the OTHER record — the U+2028 record must survive intact
        self.assertEqual(self._reject(wo["always run the linter each morning"])[0], 0)
        after = self._read_queue()
        self.assertEqual(len(after), 2, "no unrelated record shredded")
        ls_rec = next(r for r in after if ls in r.get("text", ""))
        self.assertIn("and never use ranges", ls_rec["text"], "U+2028 record intact")
        self.assertEqual(ls_rec["status"], "proposed")
        # and the U+2028 record itself IS rejectable
        self.assertEqual(self._reject(wo["always pin versions"])[0], 0)
        self.assertEqual(
            next(r for r in self._read_queue() if ls in r["text"])["status"], "rejected")

    def test_reject_unknown_id_refuses_cleanly(self):
        self._proposed_workorder()
        code, _out, err = self._reject("auto/pref-does-not-exist-xyz")
        self.assertNotEqual(code, 0)
        self.assertIn("no proposed candidate", err)
        # the real candidate is untouched by a failed reject
        self.assertEqual(self._read_queue()[0]["status"], "proposed")


if __name__ == "__main__":
    unittest.main()
