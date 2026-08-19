"""End-to-end composition proof for 260719-prefer-precision-and-reject (Task 4):
the operator-turn precision gate + the `prefer reject` verb compose across the
shipped code paths, in a TEMP KB (never the live KB, R-LOC-03).

Proves, in one flow: (a) a `>`-quoted noise turn does NOT enqueue while a genuine
operator preference DOES; (b) the classify drain surfaces the genuine one as a
`proposed` candidate + workorder; (c) `prefer reject` flips it to `rejected`,
clears the `prefer review` open set, and cancels the workorder (plan.md kept as the
re-emit sentinel); (d) a second drain does NOT resurrect it.
"""

import json
import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, build_synthetic_kb, run_cli
except ImportError:  # allow `python3 -m unittest tests.e2e.test_prefer_precision_e2e`
    from _fixtures import load_cli, build_synthetic_kb, run_cli


def _rec(ts, sid, cwd, origin, turn, prompt):
    return ("[%s] [session %s] [cwd %s] [origin=%s] [turn=%s]\n%s\n\n"
            % (ts, sid, cwd, origin, turn, prompt))


class PreferPrecisionE2E(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_prefer_precision_e2e_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = load_cli()
        os.makedirs(os.path.join(self.kdir, "logs"), exist_ok=True)
        self.logp = os.path.join(self.kdir, "logs", "prompts.log")
        self.qpath = os.path.join(self.kdir, self.mod.PREFER_QUEUE_REL)
        self._prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(self._restore)

    def _restore(self):
        if self._prev is None:
            os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        else:
            os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self._prev

    def _queue(self):
        if not os.path.isfile(self.qpath):
            return []
        with open(self.qpath, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def _review_count(self):
        _c, o, _e = run_cli(["prefer", "review", "--format", "json"], self.kdir)
        return json.loads(o)["count"]

    def test_precision_gate_and_reject_compose(self):
        # (a)+(b): a `>`-quoted noise turn does NOT enqueue; a genuine pref DOES.
        with open(self.logp, "w", encoding="utf-8") as f:
            f.write(_rec("t", "sess-A", "/tmp/x", "human", "1",
                         "> the agent said we should never use open ranges"))
            f.write(_rec("t", "sess-A", "/tmp/x", "human", "2",
                         "Always pin dependency versions in the lockfile."))
        n = self.mod._prefer_queue_scan(self.kdir, "sess-A", "",
                                        prompts_path=self.logp)
        self.assertEqual(n, 1, "only the genuine operator preference enqueues")
        q = self._queue()
        self.assertEqual(len(q), 1)
        self.assertIn("Always pin", q[0]["text"])

        # classify drain -> proposed candidate + one workorder
        self.assertEqual(self.mod._prefer_classify_drain(self.kdir).get("proposed"), 1)
        wo = self._queue()[0]["workorder"]
        self.assertTrue(wo)
        self.assertEqual(self._review_count(), 1, "proposed shows in review")

        # (c): prefer reject clears review + cancels the workorder (plan.md kept)
        rc, ro, rerr = run_cli(["prefer", "reject", wo, "--reason", "test noise",
                                "--format", "json"], self.kdir)
        self.assertEqual(rc, 0, rerr)
        self.assertTrue(json.loads(ro)["workorder_cancelled"])
        self.assertEqual(self._queue()[0]["status"], "rejected")
        self.assertEqual(self._review_count(), 0, "review clean after reject")
        wo_plan = os.path.join(self.kdir, "work", wo, "plan.md")
        self.assertTrue(os.path.isfile(wo_plan), "plan.md kept as re-emit sentinel")
        state, _ = self.mod._read_plan_status(open(wo_plan, encoding="utf-8").read())
        self.assertEqual(state, "cancelled")

        # (d): a second drain does NOT resurrect the rejected candidate
        auto_dir = os.path.join(self.kdir, "work", "auto")
        before = sorted(os.listdir(auto_dir))
        self.mod._prefer_classify_drain(self.kdir)
        self.assertEqual(sorted(os.listdir(auto_dir)), before,
                         "rejected candidate must not re-emit a workorder")
        self.assertEqual(self._review_count(), 0)


if __name__ == "__main__":
    unittest.main()
