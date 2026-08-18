#!/usr/bin/env python3
"""The facade WRITE-seam mutation canary (feature 260813, task 28).

Two arms, and the second is the one that makes the first mean anything:

  * LIVE -- the shipped facade at HEAD passes the canary: rebinding a legacy
    symbol on the loaded CLI module changes what the dispatched command prints.
  * MUTATION -- the canary REDDENS on a facade whose passthrough is removed,
    and on the far more dangerous one whose passthrough is an ``import`` +
    ``globals().update(vars(...))`` namespace COPY. The copy keeps every symbol
    reachable, so the frozen CLI-symbol gate stays green while every
    patch-based test in the suite silently asserts nothing.

A gate nobody has ever seen fail is indistinguishable from a gate that cannot
fail, so the mutations are executed here rather than described.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _mutation_canary import (  # noqa: E402
    CANARY_SENTINEL, CANARY_SYMBOL, FACADE_EXEC_ANCHOR, FACADE_KINDS,
    probe, synthesize_facade, verdict, probe_verb_is_ported, facade_has_passthrough)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_OK = {"loaded": True, "reachable": True, "dispatched": True,
       "before": "/kb", "after": CANARY_SENTINEL, "error": None}


def _result(**over):
    row = dict(_OK)
    row.update(over)
    return row


class VerdictTest(unittest.TestCase):
    """PURE semantics: every failure mode, asserted without a real load."""

    def test_a_live_seam_has_no_violations(self):
        self.assertEqual(verdict(_result()), [])

    def test_a_module_that_fails_to_load_is_a_violation(self):
        out = verdict(_result(loaded=False, error="LOAD: SystemExit: 2"))
        self.assertEqual(len(out), 1)
        self.assertIn("failed to load", out[0])
        self.assertIn("SystemExit", out[0])

    def test_an_unreachable_symbol_is_a_violation(self):
        out = verdict(_result(reachable=False, dispatched=False))
        self.assertTrue(any("not reachable" in v and CANARY_SYMBOL in v
                            for v in out), out)

    def test_unchanged_output_is_the_dead_write_seam_violation(self):
        out = verdict(_result(after="/kb"))
        self.assertEqual(len(out), 1)
        self.assertIn("WRITE seam is dead", out[0])
        self.assertIn("vacuous", out[0])

    def test_empty_unpatched_output_is_a_vacuity_violation(self):
        """A probe that observes nothing cannot observe a change either."""
        out = verdict(_result(before="", after=CANARY_SENTINEL))
        self.assertTrue(any("printed nothing" in v for v in out), out)

    def test_output_changing_without_the_sentinel_is_a_violation(self):
        """Guards the probe itself: if the dispatch reads some OTHER symbol the
        output could differ for a reason having nothing to do with the patch."""
        out = verdict(_result(after="/somewhere/else"))
        self.assertEqual(len(out), 1)
        self.assertIn("never reached stdout", out[0])

    def test_a_dispatch_error_is_reported_verbatim(self):
        out = verdict(_result(dispatched=False, error="DISPATCH: KeyError: x"))
        self.assertTrue(any("KeyError" in v for v in out), out)


@unittest.skipUnless(facade_has_passthrough(REPO),
                     "task 56: the facade no longer execs the monolith, so the "
                     "probed symbol is not on it at all -- see FacadeRetiredTest")
class LiveCanaryTest(unittest.TestCase):
    """The real facade at HEAD, loaded and mutated for real."""

    @classmethod
    def setUpClass(cls):
        cls.result = probe(REPO)

    # RESIDUAL GAP, recorded rather than papered over (task 54 -> task 56):
    # with the probed verb ported, the LIVE arm can no longer distinguish "the
    # write seam is dead because `config` is ported" from "the write seam is dead
    # because someone replaced the passthrough with a namespace COPY". The
    # MUTATION arm below still proves the detection LOGIC bites (it runs against
    # synthetic trees with expect_live_seam=True), and the `reachable` check still
    # catches an outright removed passthrough — but the namespace-copy mutation is
    # no longer detectable against the real tree through this probe. No other
    # legacy-dispatched command prints deterministically (`mcp` is a stdio
    # server), so there is no replacement probe to move to. Task 56 deletes the
    # shim, which retires the question entirely.

    def test_the_shipped_facade_passes_the_canary(self):
        self.assertEqual(
            verdict(self.result, expect_live_seam=not probe_verb_is_ported()), [],
            "the facade write seam is not as expected: %r" % (self.result,))

    def test_the_probe_actually_observed_output_on_both_sides(self):
        """Non-vacuity: a canary comparing two empty strings 'passes' forever."""
        self.assertTrue(self.result["before"])
        if not probe_verb_is_ported():
            self.assertNotEqual(self.result["before"], self.result["after"])
            self.assertEqual(self.result["after"], CANARY_SENTINEL)
            return
        # TERMINAL (task 54): the probed verb is ported, so the rebind must NOT
        # reach the dispatch. Non-vacuity still has to be earned — the command
        # must have printed a real answer BOTH times, otherwise "unchanged"
        # would be two empty strings agreeing about nothing.
        self.assertEqual(self.result["before"], self.result["after"])
        self.assertNotIn(CANARY_SENTINEL, self.result["after"] or "")
        self.assertTrue(self.result["after"])

    def test_the_canary_probes_the_file_the_whole_suite_loads(self):
        """A canary aimed at a different entrypoint than `load_cli` uses would
        certify a seam nobody's tests actually go through."""
        from _fixtures import CLI_PATH
        self.assertEqual(os.path.realpath(CLI_PATH),
                         os.path.realpath(os.path.join(REPO, "scripts",
                                                       "agentware")))

    def test_the_probed_symbol_is_owned_by_the_legacy_body_not_the_facade(self):
        """If the facade itself defined the symbol the probe would prove
        nothing about the passthrough."""
        with open(os.path.join(REPO, "scripts", "agentware"),
                  encoding="utf-8") as fh:
            facade = fh.read()
        self.assertNotIn("def %s" % CANARY_SYMBOL, facade)
        with open(os.path.join(REPO, "scripts", "agentware_legacy.py"),
                  encoding="utf-8") as fh:
            self.assertIn("def %s" % CANARY_SYMBOL, fh.read())


@unittest.skipUnless(facade_has_passthrough(REPO),
                     "task 56: the facade no longer execs the monolith, so there "
                     "is no passthrough to mutate -- the whole MUTATION arm is a "
                     "migration-window instrument and its window has closed")
class MutatedFacadeTest(unittest.TestCase):
    """Proof the canary bites. Each mutation is one line off the shipped file."""

    def _probe_kind(self, kind):
        dest = tempfile.mkdtemp(prefix="aw-canary-tree-")
        self.addCleanup(_rmtree, dest)
        synthesize_facade(dest, kind, REPO)
        return probe(dest)

    def test_the_unmutated_synthetic_tree_is_a_passing_control(self):
        """Without this, a mutation could 'redden' because the scratch tree is
        broken rather than because the mutation was detected."""
        self.assertEqual(
            verdict(self._probe_kind("exec"),
                    expect_live_seam=not probe_verb_is_ported()), [])

    def test_removing_the_passthrough_reddens_the_canary(self):
        out = verdict(self._probe_kind("absent"))
        self.assertTrue(out)
        self.assertTrue(any("not reachable" in v for v in out), out)

    def test_an_import_copy_passthrough_reddens_the_canary(self):
        """The silent killer: reachable symbols, dead write seam."""
        result = self._probe_kind("import-copy")
        self.assertTrue(result["reachable"], "expected the READ seam to survive")
        self.assertEqual(result["before"], result["after"])
        out = verdict(result)
        self.assertTrue(any("WRITE seam is dead" in v for v in out), out)

    def test_the_synthesizer_refuses_a_facade_it_cannot_mutate(self):
        """The mutations are text edits; if the anchor drifts they become
        no-ops and both arms above would pass while testing the same file."""
        fake = tempfile.mkdtemp(prefix="aw-canary-fake-")
        self.addCleanup(_rmtree, fake)
        os.makedirs(os.path.join(fake, "scripts"))
        with open(os.path.join(fake, "scripts", "agentware"), "w",
                  encoding="utf-8") as fh:
            fh.write("# a facade with no exec anchor\n")
        with self.assertRaises(SystemExit):
            synthesize_facade(tempfile.mkdtemp(prefix="aw-canary-dest-"),
                              "import-copy", fake)

    def test_the_anchor_matches_the_shipped_facade_exactly_once(self):
        with open(os.path.join(REPO, "scripts", "agentware"),
                  encoding="utf-8") as fh:
            self.assertEqual(fh.read().count(FACADE_EXEC_ANCHOR), 1)

    def test_every_declared_kind_is_synthesizable(self):
        for kind in FACADE_KINDS:
            dest = tempfile.mkdtemp(prefix="aw-canary-kind-")
            self.addCleanup(_rmtree, dest)
            self.assertTrue(os.path.isfile(synthesize_facade(dest, kind, REPO)))
        with self.assertRaises(ValueError):
            synthesize_facade(tempfile.mkdtemp(prefix="aw-x-"), "nope", REPO)


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class FacadeRetiredTest(unittest.TestCase):
    """What must hold once the passthrough is GONE (task 56's terminal state).

    The canary above measures a WRITE SEAM that only exists while the monolith is
    `exec`'d into the facade's globals. Deleting it does not weaken anything —
    there is no seam to keep live, no namespace to copy, and no legacy body for a
    rebind to reach. But "the canary no longer applies" must not be the same
    observable as "the canary was quietly dropped", so the retired state gets its
    own assertions: the passthrough is really gone, the monolith is really absent,
    and the CLI still dispatches without either.
    """

    @unittest.skipIf(facade_has_passthrough(REPO),
                     "the passthrough is still present; LiveCanaryTest owns this")
    def test_the_passthrough_and_the_monolith_are_both_gone(self):
        with open(os.path.join(REPO, "scripts", "agentware"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn(FACADE_EXEC_ANCHOR, text)
        self.assertFalse(
            os.path.isfile(os.path.join(REPO, "scripts", "agentware_legacy.py")),
            "the facade stopped exec'ing the monolith but the file is still here")

    @unittest.skipIf(facade_has_passthrough(REPO),
                     "the passthrough is still present; LiveCanaryTest owns this")
    def test_the_cli_still_dispatches_without_the_shim(self):
        """A dead seam must not be confused with a dead CLI."""
        result = probe(REPO)
        self.assertTrue(result["loaded"], result.get("error"))
        self.assertTrue(result["dispatched"], result.get("error"))
        self.assertTrue(result["before"],
                        "the probed command printed nothing without the shim")
        self.assertNotIn(CANARY_SENTINEL, result["before"] or "")
