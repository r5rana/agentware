"""The pre/post-hook ORG-LINT gate (scripts/loop/orglint.sh) — donor capability
re-injected by plan task 33.

This module is the named evidence for parity row `loop:org-lint-hook-gates`,
whose mutation is "remove either call site: an org-invalid artifact passes the
phase gate". Both halves of that claim are proven here: the CALL-SITE mutation
drives the real wiring gate, and the BEHAVIOURAL tests source the shipped bash
function with a stub toolkit so what is asserted is the gate's own control flow.

Two call sites, not one, is the substance of the row. A pre-hook-only gate cannot
see a cross-team write the run itself performed; a post-hook-only gate lets a run
start against an already-broken org tree and only discovers it after burning the
whole iteration budget.
"""

from tests._paths import TESTS_DIR

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import _loop_wiring as W  # noqa: E402

LOOP_DIR = os.path.join(REPO, "scripts", "loop")
ORGLINT = os.path.join(LOOP_DIR, "orglint.sh")


def run_bash(script, cwd=None):
    e = dict(os.environ)
    for k in ("AGENTWARE_ORG_ROOT", "AGENTWARE_DISABLE_ORG_LINT"):
        e.pop(k, None)
    p = subprocess.run(["bash", "-c", script], cwd=cwd or REPO, env=e,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, p.stdout.decode("utf-8", "replace")


class OrgLintHarness(unittest.TestCase):
    def harness(self, call, extra="", probe_ok=True, lint_rc=0):
        """Source orglint.sh with a stub `scripts/agentware` and run `call`.

        probe_ok controls whether `org lint --help` (the availability probe)
        resolves; lint_rc is the exit code of the REAL invocation.
        """
        d = tempfile.mkdtemp(prefix="aw-orglint-")
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "scripts"))
        stub = os.path.join(d, "scripts", "agentware")
        with open(stub, "w") as fh:
            fh.write("#!/bin/bash\n")
            fh.write('echo "STUB_CALLED: $*" >&2\n')
            if probe_ok:
                fh.write('for a in "$@"; do [[ "$a" == "--help" ]] && exit 0; done\n')
            else:
                fh.write("exit 2\n")
            fh.write("exit %d\n" % lint_rc)
        os.chmod(stub, 0o755)
        return run_bash(
            'set -e\nlog() { echo "[log] $1"; }\n%s\nsource "%s"\n%s\necho PROCEEDED\n'
            % (extra, ORGLINT, call), cwd=d)


class FlatKnowledgeBaseTest(OrgLintHarness):
    """R-ORG-04: with the org keys unset the loop must be byte-identical to today."""

    def test_flat_kb_is_a_silent_noop(self):
        rc, out = self.harness("run_org_lint_gate pre-hook")
        self.assertEqual(rc, 0, out)
        self.assertIn("PROCEEDED", out)
        self.assertNotIn("STUB_CALLED", out,
                         "a flat KB must not even spawn the toolkit")

    def test_flat_kb_logs_nothing(self):
        rc, out = self.harness("run_org_lint_gate post-hook")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("org lint", out)


class UnservedVerbTest(OrgLintHarness):
    """`org` is a donor-only verb, not yet in the strangler allowlist.

    The donor's own copy of this gate calls `org lint` UNCONDITIONALLY behind
    `if ! …; then exit 1`. Ported verbatim onto this tree that turns argparse's
    `invalid choice: 'org'` into a pre-hook abort — hard-blocking EVERY run until
    the donor-only verb wave lands. The gate must tell "the verb rejected the
    tree" apart from "the verb does not exist".
    """

    def test_unserved_verb_is_a_noop_not_an_abort(self):
        rc, out = self.harness("run_org_lint_gate pre-hook",
                               extra="export AGENTWARE_ORG_ROOT=/tmp/org\n",
                               probe_ok=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("not served by this build", out)
        self.assertIn("PROCEEDED", out)

    def test_unserved_verb_never_reaches_the_real_invocation(self):
        rc, out = self.harness("run_org_lint_gate pre-hook",
                               extra="export AGENTWARE_ORG_ROOT=/tmp/org\n",
                               probe_ok=False, lint_rc=1)
        self.assertEqual(rc, 0, out)
        self.assertIn("PROCEEDED", out)

    def test_org_verb_is_still_unserved_today(self):
        """Grounds the guard against LIVE state rather than an assumption.

        If a later port wave serves `org`, this test flips and the guard becomes
        dormant rather than wrong — which is the intended lifecycle, so it asserts
        the observation, not a permanent truth.
        """
        p = subprocess.run(["scripts/agentware", "org", "lint", "--help"],
                           cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode == 0:
            self.skipTest("`org` is now served — the availability guard is dormant")
        self.assertIn(b"invalid choice", p.stderr)


class RealViolationTest(OrgLintHarness):
    """When the verb IS served and rejects the tree, the gate must fail loud."""

    def test_pre_hook_violation_aborts(self):
        rc, out = self.harness("run_org_lint_gate pre-hook",
                               extra="export AGENTWARE_ORG_ROOT=/tmp/org\n",
                               probe_ok=True, lint_rc=1)
        self.assertEqual(rc, 1, out)
        self.assertNotIn("PROCEEDED", out)
        self.assertIn("org lint failed", out)
        self.assertIn("Fix it before the run", out)

    def test_post_hook_violation_aborts_before_the_commit(self):
        rc, out = self.harness("run_org_lint_gate post-hook",
                               extra="export AGENTWARE_ORG_ROOT=/tmp/org\n",
                               probe_ok=True, lint_rc=1)
        self.assertEqual(rc, 1, out)
        self.assertIn("drifted DURING the run", out)
        self.assertIn("nothing is committed", out)

    def test_served_and_clean_proceeds(self):
        rc, out = self.harness("run_org_lint_gate pre-hook",
                               extra="export AGENTWARE_ORG_ROOT=/tmp/org\n",
                               probe_ok=True, lint_rc=0)
        self.assertEqual(rc, 0, out)
        self.assertIn("PROCEEDED", out)


class KillSwitchTest(OrgLintHarness):
    def test_kill_switch_skips_but_never_silently(self):
        rc, out = self.harness("run_org_lint_gate pre-hook",
                               extra="export AGENTWARE_DISABLE_ORG_LINT=1\n"
                                     "export AGENTWARE_ORG_ROOT=/tmp/org\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("AGENTWARE_DISABLE_ORG_LINT", out)
        self.assertIn("PROCEEDED", out)
        self.assertNotIn("STUB_CALLED", out)


class CallSiteMutationTest(unittest.TestCase):
    """The parity row's mutation: remove either call site, the gate must redden.

    Driven through the wiring gate's real entry point (`evaluate`) over a mutated
    copy of the tree — not through an internal comparator.
    """

    def setUp(self):
        self.fixture = W.load_fixture()
        d = tempfile.mkdtemp(prefix="aw-orgmut-")
        self.addCleanup(shutil.rmtree, d, True)
        shutil.copy2(os.path.join(REPO, "agentware.sh"), os.path.join(d, "agentware.sh"))
        shutil.copytree(LOOP_DIR, os.path.join(d, "scripts", "loop"))
        # Copy the REAL hooks dir: the ceiling half of the gate measures it, so
        # an empty stand-in makes the declared codex-stream.py debt read as STALE
        # and every mutation test would then pass on the wrong violation.
        shutil.copytree(os.path.join(REPO, "scripts", "hooks"),
                        os.path.join(d, "scripts", "hooks"))
        self.tree = d

    def _hooks(self):
        return os.path.join(self.tree, "scripts", "loop", "hooks.sh")

    def _mutate(self, old, new):
        with open(self._hooks()) as fh:
            text = fh.read()
        self.assertIn(old, text)
        with open(self._hooks(), "w") as fh:
            fh.write(text.replace(old, new, 1))

    def test_baseline_tree_passes(self):
        violations, _ = W.evaluate(self.fixture, root=self.tree)
        self.assertEqual(violations, [], "\n".join(violations))

    def test_removing_the_pre_hook_call_site_reddens(self):
        self._mutate("run_org_lint_gate pre-hook", ":")
        violations, _ = W.evaluate(self.fixture, root=self.tree)
        self.assertTrue(any("run_org_lint_gate" in v for v in violations), violations)

    def test_removing_the_post_hook_call_site_reddens(self):
        self._mutate("run_org_lint_gate post-hook", ":")
        violations, _ = W.evaluate(self.fixture, root=self.tree)
        self.assertTrue(any("run_org_lint_gate" in v for v in violations), violations)

    def test_both_call_sites_present_in_the_shipped_tree(self):
        sources = W.loop_sources(REPO)
        self.assertGreaterEqual(W.total_call_sites(sources, "run_org_lint_gate"), 2)

    def test_gate_lives_in_its_own_lib_not_the_hooks_lib(self):
        """The plan requires the re-injected wiring in NEW libs."""
        where = W.defined_in(W.loop_sources(REPO), "run_org_lint_gate")
        self.assertEqual(where, [os.path.join("scripts", "loop", "orglint.sh")])


if __name__ == "__main__":
    unittest.main()
