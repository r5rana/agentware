"""Dream audit+eval change-gate — affordability guard #2 (Task 8, 260713).

Runner: python3 -m unittest tests.unit.features.test_dream_change_gate -v

Steps c (audit ~150s) + d (eval-record ~76s) — ~98% of the cycle cost — SKIP on a
CLEAN tree that has NOT changed (pkg git HEAD + index.json CONTENT hash) since the
last cycle, and ALWAYS RUN otherwise. Proven here:
  * clean + unchanged fp -> both steps return status:skipped, touch nothing;
  * a changed pkg HEAD or index CONTENT -> both run;
  * a DIRTY working tree ALWAYS runs, and two CONSECUTIVE dirty cycles with
    DIFFERENT content BOTH run — dirtiness is NEVER collapsed into the fp (F2);
  * FAIL-OPEN: no prior cycle, a prior cycle with no fp, or a git/index error all
    RUN, so a real regression is never masked.
Hermetic; the fp/dirty probes are mocked so the test never depends on the real
package's git state.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from tests._fixtures import load_cli, build_synthetic_kb
except ImportError:
    from _fixtures import load_cli, build_synthetic_kb

_FP = "HEADSHA:INDEXHASH"


class ChangeGateTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        self.kdir = tempfile.mkdtemp(prefix="aw_change_gate_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.metrics = os.path.join(self.kdir, "logs", "metrics.jsonl")
        os.makedirs(os.path.dirname(self.metrics), exist_ok=True)
        self._prev = os.environ.get("AGENTWARE_NESTED_UNITTEST")
        os.environ["AGENTWARE_NESTED_UNITTEST"] = "1"
        self.addCleanup(
            lambda: os.environ.__setitem__("AGENTWARE_NESTED_UNITTEST", self._prev)
            if self._prev is not None
            else os.environ.pop("AGENTWARE_NESTED_UNITTEST", None))

    def _seed_cycle(self, change_fp=_FP):
        rec = {"event": "dream", "ts": "2026-07-13T00:00:00Z",
               "finished": "2026-07-13T00:00:00Z", "duration_s": 1.0,
               "force": False, "steps": []}
        if change_fp is not None:
            rec["change_fp"] = change_fp
        with open(self.metrics, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    # --- the gate decision -----------------------------------------------
    def test_clean_unchanged_skips(self):
        self._seed_cycle(_FP)
        with mock.patch.object(self.mod, "_pkg_working_tree_dirty", return_value=False), \
             mock.patch.object(self.mod, "_dream_change_fp", return_value=_FP):
            self.assertEqual(self.mod._dream_change_gate_skip(self.kdir),
                             "clean tree, no pkg/kb change since last cycle")

    def test_changed_fp_runs(self):
        self._seed_cycle(_FP)
        with mock.patch.object(self.mod, "_pkg_working_tree_dirty", return_value=False), \
             mock.patch.object(self.mod, "_dream_change_fp",
                               return_value="HEADSHA:DIFFERENT"):
            self.assertIsNone(self.mod._dream_change_gate_skip(self.kdir))

    def test_dirty_always_runs_even_when_fp_equal(self):
        self._seed_cycle(_FP)
        with mock.patch.object(self.mod, "_pkg_working_tree_dirty", return_value=True), \
             mock.patch.object(self.mod, "_dream_change_fp", return_value=_FP):
            self.assertIsNone(self.mod._dream_change_gate_skip(self.kdir),
                              "a dirty tree runs regardless of the fp")

    def test_two_consecutive_dirty_cycles_both_run(self):
        # F2 regression-mask guard: a 0/1 dirty bit folded into the fp would
        # collapse two distinct dirty states and skip the second. Dirtiness must
        # gate the DECISION, never the fp VALUE -> BOTH dirty cycles run.
        self._seed_cycle(_FP)
        with mock.patch.object(self.mod, "_pkg_working_tree_dirty", return_value=True), \
             mock.patch.object(self.mod, "_dream_change_fp", return_value=_FP):
            self.assertIsNone(self.mod._dream_change_gate_skip(self.kdir))
            self.assertIsNone(self.mod._dream_change_gate_skip(self.kdir))

    def test_no_prior_cycle_fails_open(self):
        with mock.patch.object(self.mod, "_pkg_working_tree_dirty", return_value=False), \
             mock.patch.object(self.mod, "_dream_change_fp", return_value=_FP):
            self.assertIsNone(self.mod._dream_change_gate_skip(self.kdir))

    def test_prior_cycle_without_fp_fails_open(self):
        self._seed_cycle(change_fp=None)
        with mock.patch.object(self.mod, "_pkg_working_tree_dirty", return_value=False), \
             mock.patch.object(self.mod, "_dream_change_fp", return_value=_FP):
            self.assertIsNone(self.mod._dream_change_gate_skip(self.kdir))

    def test_uncomputable_fp_fails_open(self):
        self._seed_cycle(_FP)
        with mock.patch.object(self.mod, "_pkg_working_tree_dirty", return_value=False), \
             mock.patch.object(self.mod, "_dream_change_fp", return_value=None):
            self.assertIsNone(self.mod._dream_change_gate_skip(self.kdir))

    def test_change_fp_uses_content_not_mtime(self):
        # _dream_change_fp binds the index CONTENT hash: rewriting index.json with
        # the SAME bytes (new mtime) leaves the fp UNCHANGED; different bytes move it.
        with mock.patch.object(self.mod, "_git_head", return_value="H"):
            fp1 = self.mod._dream_change_fp(self.kdir)
            idx = os.path.join(self.kdir, "index.json")
            with open(idx, encoding="utf-8") as _f:
                data = _f.read()
            os.utime(idx, (10 ** 9, 10 ** 9))   # bump mtime, same content
            with open(idx, "w") as f:
                f.write(data)
            self.assertEqual(fp1, self.mod._dream_change_fp(self.kdir),
                             "content-hash fp must ignore mtime")
            with open(idx, "w") as f:
                f.write(data + "\n// changed")
            self.assertNotEqual(fp1, self.mod._dream_change_fp(self.kdir))

    # --- step integration -------------------------------------------------
    def test_step_audit_skips_and_eval_skips_when_unchanged(self):
        self._seed_cycle(_FP)
        with mock.patch.object(self.mod, "_pkg_working_tree_dirty", return_value=False), \
             mock.patch.object(self.mod, "_dream_change_fp", return_value=_FP):
            c = self.mod._dream_step_audit(self.kdir, dry_run=False)
            d = self.mod._dream_step_eval_record(self.kdir, dry_run=False)
        self.assertEqual(c["status"], "skipped")
        self.assertIn("clean tree", c["reason"])
        self.assertEqual(d["status"], "skipped")
        self.assertIn("clean tree", d["reason"])

    def test_step_eval_runs_past_change_gate_when_changed(self):
        # A changed fp must let step d PAST the change-gate — proven by it reaching
        # the downstream 'no gold set' skip (a DIFFERENT reason), not the gate skip.
        self._seed_cycle(_FP)
        with mock.patch.object(self.mod, "_pkg_working_tree_dirty", return_value=True), \
             mock.patch.object(self.mod, "_dream_change_fp", return_value=_FP):
            d = self.mod._dream_step_eval_record(self.kdir, dry_run=False)
        self.assertEqual(d["status"], "skipped")
        self.assertIn("no gold set", d["reason"],
                      "dirty tree runs past the change-gate to the gold check")

    def test_the_gold_check_cannot_PRECEDE_the_change_gate(self):
        """The discriminator the probe above silently needs (task 94).

        `test_step_eval_runs_past_change_gate_when_changed` reads "no gold set"
        as evidence that the run got PAST the change gate. That inference is
        only valid while the gate is consulted FIRST -- and the lib port had the
        two checks the other way round, which made the probe unreachable-by-
        construction: "no gold set" came back whether the gate admitted the run
        or wrongly skipped it, so the test passed either way and the change
        gate had no coverage at all.

        Both orders skip, so no status assertion can separate them. The one
        observable that can is the REASON on the input where the two checks
        DISAGREE: a clean, unchanged tree that also has no gold set. Gate-first
        answers "clean tree"; gold-first answers "no gold set".
        """
        self._seed_cycle(_FP)
        gold = os.path.join(self.kdir, "benchmarks", "recall-gold.json")
        self.assertFalse(os.path.isfile(gold), "fixture must have no gold set")
        with mock.patch.object(self.mod, "_pkg_working_tree_dirty",
                               return_value=False), \
             mock.patch.object(self.mod, "_dream_change_fp", return_value=_FP):
            d = self.mod._dream_step_eval_record(self.kdir, dry_run=False)
        self.assertEqual(d["status"], "skipped")
        self.assertIn("clean tree", d["reason"],
                      "the change gate must be consulted BEFORE the gold check, "
                      "or 'no gold set' stops being evidence of passing it")
        self.assertNotIn("no gold set", d["reason"])


if __name__ == "__main__":
    unittest.main()
