"""`recall --scope work` plan-status multipliers + work/auto walker (feature
260710, Task 5b/5c).

superseded/cancelled ×0.3, done ×0.7, draft/ready/running/MISSING ×1.0; siblings
inherit their feature's plan multiplier; the DEFAULT/curated ranking is untouched.

Test names are prefixed `test_scopework_`.

Runner: python3 -m unittest tests.unit.retrieval.test_scopework_recall -v
"""

import json
import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import run_cli as _raw_run_cli, build_synthetic_kb, load_cli
except ImportError:
    from _fixtures import run_cli as _raw_run_cli, build_synthetic_kb, load_cli


_BODY = ("widgetfrobnicator quuxinator widgetfrobnicator the widgetfrobnicator "
         "handles quuxinator flows for the widgetfrobnicator subsystem")


def _plan(feature, status):
    return ("# Plan — %s\n\n> Feature: `%s`\n> Created: 2026-01-01\n"
            "> Status: %s\n> Type: feature work\n\n---\n\n## Context\n\n%s\n\n"
            "---\n\n## Tasks\n\n- ⬜ **1** t\n  *Verify:* ok\n\n---\n\n"
            "## Acceptance criteria\n\n- [ ] x\n\n<promise>X_COMPLETE</promise>\n"
            % (feature, feature, status, _BODY))


class ScopeWorkTestCase(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-scopework-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = load_cli()

    def run_cli(self, argv):
        try:
            return _raw_run_cli(argv, self.kdir)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            return code, "", "SystemExit(%r)" % (exc.code,)

    def make(self, feature, status, sibling=False):
        d = os.path.join(self.kdir, "work", feature)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as f:
            f.write(_plan(feature, status))
        if sibling:
            with open(os.path.join(d, "worklog.md"), "w", encoding="utf-8") as f:
                f.write("# Worklog\n\n%s\n" % _BODY)

    def recall_work(self, query):
        code, out, err = self.run_cli(
            ["recall", query, "--scope", "work", "--format", "json",
             "--top-k", "20"])
        self.assertEqual(code, 0, err)
        return json.loads(out)["results"]

    def rank(self, results, needle):
        for i, r in enumerate(results):
            if needle in r["id"]:
                return i, r["score"]
        return None, None

    def test_scopework_cancelled_ranks_below_live(self):
        self.make("260101-live", "running")
        self.make("260102-dead", "cancelled")
        res = self.recall_work("widgetfrobnicator quuxinator")
        i_live, s_live = self.rank(res, "260101-live")
        i_dead, s_dead = self.rank(res, "260102-dead")
        self.assertIsNotNone(i_live)
        self.assertIsNotNone(i_dead)
        self.assertLess(i_live, i_dead, "cancelled must rank below the live plan")
        self.assertGreater(s_live, s_dead)

    def test_scopework_done_downranked_vs_draft(self):
        self.make("260101-draft", "draft")
        self.make("260102-done", "done")
        res = self.recall_work("widgetfrobnicator quuxinator")
        _i, s_draft = self.rank(res, "260101-draft")
        _j, s_done = self.rank(res, "260102-done")
        # done ×0.7 < draft ×1.0 for identical bodies
        self.assertGreater(s_draft, s_done)
        self.assertAlmostEqual(s_done / s_draft, 0.7, places=3)

    def test_scopework_superseded_multiplier(self):
        self.make("260101-live2", "running")
        self.make("260102-sup", "superseded")
        res = self.recall_work("widgetfrobnicator quuxinator")
        _i, s_live = self.rank(res, "260101-live2")
        _j, s_sup = self.rank(res, "260102-sup")
        self.assertAlmostEqual(s_sup / s_live, 0.3, places=3)

    def test_scopework_sibling_inherits_feature_multiplier(self):
        # a cancelled feature's worklog.md sibling inherits ×0.3
        self.make("260101-livesib", "running", sibling=True)
        self.make("260102-cansib", "cancelled", sibling=True)
        res = self.recall_work("widgetfrobnicator quuxinator")
        _i, s_live = self.rank(res, "260101-livesib/worklog")
        _j, s_can = self.rank(res, "260102-cansib/worklog")
        self.assertIsNotNone(s_live)
        self.assertIsNotNone(s_can)
        self.assertAlmostEqual(s_can / s_live, 0.3, places=3)

    def test_scopework_missing_status_is_neutral(self):
        # a legacy plan with NO `> Status:` header defaults to draft -> ×1.0.
        d = os.path.join(self.kdir, "work", "260101-legacy")
        os.makedirs(d)
        legacy = _plan("260101-legacy", "running").replace(
            "> Status: running\n", "")
        with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as f:
            f.write(legacy)
        corpus = self.mod.build_work_corpus(self.kdir)
        ent = next(e for (e, _t) in corpus
                   if e["id"].startswith("260101-legacy/plan"))
        self.assertEqual(ent["status"], "draft")     # missing -> draft default
        self.assertEqual(ent["status_mult"], 1.0)     # neutral, like running

    def test_scopework_build_corpus_includes_auto_lane(self):
        self.make(os.path.join("auto", "260101-wo"), "draft")
        corpus = self.mod.build_work_corpus(self.kdir)
        ids = [e["id"] for (e, _t) in corpus]
        self.assertTrue(any(i.startswith("auto/260101-wo/") for i in ids),
                        "work/auto/ features must enter the work corpus")

    def test_scopework_list_loop_features_includes_auto(self):
        self.make(os.path.join("auto", "260101-lf"), "draft")
        feats = self.mod.list_loop_features(self.kdir)
        self.assertIn("auto/260101-lf", feats)


if __name__ == "__main__":
    unittest.main()
