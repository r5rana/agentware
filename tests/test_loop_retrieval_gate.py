"""The post-hook RETRIEVAL-QUALITY gate (scripts/loop/retrieval.sh) — donor
capability re-injected by plan task 33.

Named evidence for parity row `loop:retrieval-quality-gate`, whose mutation is
"remove the post-hook call site: a run that degraded retrieval quality completes
clean". That is the whole point of the row: this is the ONLY gate in the chain
that reads RANKING. `index validate` proves each entry is well-formed and
`steering lint` proves the rules parse, but a run whose new entries are each
individually valid can still collectively confuse BM25 so an existing query stops
finding the entry it used to — and every other post-hook gate stays green.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import _loop_wiring as W  # noqa: E402

LOOP_DIR = os.path.join(REPO, "scripts", "loop")
RETRIEVAL = os.path.join(LOOP_DIR, "retrieval.sh")


def run_bash(script, cwd=None):
    e = dict(os.environ)
    e.pop("AGENTWARE_DISABLE_RECALL_GATE", None)
    p = subprocess.run(["bash", "-c", script], cwd=cwd or REPO, env=e,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, p.stdout.decode("utf-8", "replace")


class RetrievalGateHarness(unittest.TestCase):
    def harness(self, extra="", eval_rc=0):
        """Source retrieval.sh with a stub toolkit and run the gate."""
        d = tempfile.mkdtemp(prefix="aw-retr-")
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "scripts"))
        stub = os.path.join(d, "scripts", "agentware")
        with open(stub, "w") as fh:
            fh.write('#!/bin/bash\necho "STUB_CALLED: $*" >&2\nexit %d\n' % eval_rc)
        os.chmod(stub, 0o755)
        return run_bash(
            'set -e\nlog() { echo "[log] $1"; }\n%s\nsource "%s"\n'
            'run_retrieval_quality_gate\necho PROCEEDED\n' % (extra, RETRIEVAL),
            cwd=d)

    def kdir(self, with_gold):
        d = tempfile.mkdtemp(prefix="aw-kdir-")
        self.addCleanup(shutil.rmtree, d, True)
        if with_gold:
            os.makedirs(os.path.join(d, "benchmarks"))
            with open(os.path.join(d, "benchmarks", "recall-gold.json"), "w") as fh:
                fh.write("{}")
        return d


class AbsentGoldSetTest(RetrievalGateHarness):
    """A fresh or opted-out KB has no baseline to regress against."""

    def test_no_gold_set_is_a_silent_noop(self):
        rc, out = self.harness(extra='export KDIR="%s"\n' % self.kdir(False))
        self.assertEqual(rc, 0, out)
        self.assertIn("PROCEEDED", out)
        self.assertNotIn("STUB_CALLED", out,
                         "an absent gold set must not spawn eval")

    def test_unset_kdir_is_a_noop(self):
        rc, out = self.harness()
        self.assertEqual(rc, 0, out)
        self.assertIn("PROCEEDED", out)
        self.assertNotIn("STUB_CALLED", out)

    def test_no_gold_set_does_not_fail_even_if_eval_would(self):
        rc, out = self.harness(extra='export KDIR="%s"\n' % self.kdir(False),
                               eval_rc=1)
        self.assertEqual(rc, 0, out)
        self.assertIn("PROCEEDED", out)


class KillSwitchTest(RetrievalGateHarness):
    def test_kill_switch_skips_but_never_silently(self):
        rc, out = self.harness(
            extra='export AGENTWARE_DISABLE_RECALL_GATE=1\nexport KDIR="%s"\n'
                  % self.kdir(True), eval_rc=1)
        self.assertEqual(rc, 0, out)
        self.assertIn("AGENTWARE_DISABLE_RECALL_GATE", out)
        self.assertIn("PROCEEDED", out)
        self.assertNotIn("STUB_CALLED", out)


class RegressionDetectedTest(RetrievalGateHarness):
    def test_regression_fails_loud(self):
        rc, out = self.harness(extra='export KDIR="%s"\n' % self.kdir(True),
                               eval_rc=1)
        self.assertEqual(rc, 1, out)
        self.assertNotIn("PROCEEDED", out)
        self.assertIn("Recall@5 regressed", out)

    def test_regression_message_names_the_bypass(self):
        rc, out = self.harness(extra='export KDIR="%s"\n' % self.kdir(True),
                               eval_rc=1)
        self.assertIn("AGENTWARE_DISABLE_RECALL_GATE=1", out)

    def test_clean_eval_proceeds_and_did_run(self):
        rc, out = self.harness(extra='export KDIR="%s"\n' % self.kdir(True),
                               eval_rc=0)
        self.assertEqual(rc, 0, out)
        self.assertIn("PROCEEDED", out)
        self.assertIn("STUB_CALLED", out, "a present gold set MUST run eval")
        self.assertIn("--gate", out)

    def test_invocation_uses_the_acr_strategy(self):
        rc, out = self.harness(extra='export KDIR="%s"\n' % self.kdir(True))
        self.assertIn("bm25+acr", out)


class CallSiteMutationTest(unittest.TestCase):
    """The parity row's mutation, driven through the wiring gate's entry point."""

    def setUp(self):
        self.fixture = W.load_fixture()
        d = tempfile.mkdtemp(prefix="aw-retrmut-")
        self.addCleanup(shutil.rmtree, d, True)
        shutil.copy2(os.path.join(REPO, "agentware.sh"), os.path.join(d, "agentware.sh"))
        shutil.copytree(LOOP_DIR, os.path.join(d, "scripts", "loop"))
        # Copy the REAL hooks dir: the ceiling half of the gate measures it, so
        # an empty stand-in makes the declared codex-stream.py debt read as STALE
        # and every mutation test would then pass on the wrong violation.
        shutil.copytree(os.path.join(REPO, "scripts", "hooks"),
                        os.path.join(d, "scripts", "hooks"))
        self.tree = d

    def test_baseline_tree_passes(self):
        violations, _ = W.evaluate(self.fixture, root=self.tree)
        self.assertEqual(violations, [], "\n".join(violations))

    def test_removing_the_post_hook_call_site_reddens(self):
        p = os.path.join(self.tree, "scripts", "loop", "hooks.sh")
        with open(p) as fh:
            text = fh.read()
        self.assertIn("run_retrieval_quality_gate", text)
        with open(p, "w") as fh:
            fh.write(text.replace("run_retrieval_quality_gate", ":", 1))
        violations, _ = W.evaluate(self.fixture, root=self.tree)
        self.assertTrue(any("run_retrieval_quality_gate" in v for v in violations),
                        violations)

    def test_call_site_present_in_the_shipped_tree(self):
        sources = W.loop_sources(REPO)
        self.assertGreaterEqual(
            W.total_call_sites(sources, "run_retrieval_quality_gate"), 1)

    def test_gate_lives_in_its_own_lib_not_the_hooks_lib(self):
        where = W.defined_in(W.loop_sources(REPO), "run_retrieval_quality_gate")
        self.assertEqual(where, [os.path.join("scripts", "loop", "retrieval.sh")])

    def test_gate_runs_before_the_worklog_scan(self):
        """Order matters: a specific ranking failure must be reported rather than
        being masked by whatever the zero-knowledge-loss scan says."""
        with open(os.path.join(LOOP_DIR, "hooks.sh")) as fh:
            text = fh.read()
        self.assertLess(text.index("run_retrieval_quality_gate"),
                        text.index("zero-knowledge-loss gate"))


if __name__ == "__main__":
    unittest.main()
