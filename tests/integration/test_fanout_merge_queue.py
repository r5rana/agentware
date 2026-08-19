"""FanoutMergeQueueTest -- split from `test_fanout` (task 58).

See `tests.integration._fanout_support` for the
shared header.
"""

from tests.integration._fanout_support import (  # noqa: F401
    FanoutTestBase, _run, json, os)


class FanoutMergeQueueTest(FanoutTestBase):

    def _mq(self, *extra):
        return self._fanout("fanout", "merge-queue", "--repo", self.repo,
                            "--kb-repo", self.kb_repo, *extra)

    def test_merge_queue_integrates_disjoint_lanes_never_drops(self):
        self.assertEqual(self._spin_up("alpha", "beta")[0], 0)
        self._finish_lane("alpha", "line_05 = 5", 'def cmd_alpha(): return "A"')
        self._finish_lane("beta", "line_30 = 30", 'def cmd_beta(): return "B"')
        code, out, err = self._mq("--gate", "true")
        self.assertEqual(code, 0, err + out)
        mono = self._read_integration_monolith()
        self.assertIn("def cmd_alpha()", mono)
        self.assertIn("def cmd_beta()", mono)   # never-drop: BOTH present
        lanes = self._lanes()["lanes"]
        self.assertTrue(lanes["alpha"]["merged"])
        self.assertTrue(lanes["beta"]["merged"])

    def test_merge_queue_resumable_skips_already_merged(self):
        self.assertEqual(self._spin_up("alpha")[0], 0)
        self._finish_lane("alpha", "line_05 = 5", 'def cmd_alpha(): pass')
        self.assertEqual(self._mq("--gate", "true")[0], 0)
        code, out, err = self._mq("--format", "json", "--gate", "true")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["lanes"][0]["status"], "skipped")
        self.assertIn("already merged", payload["lanes"][0]["detail"])

    def test_merge_queue_monolith_conflict_halts_clean(self):
        self.assertEqual(self._spin_up("alpha", "beta")[0], 0)
        # Both lanes edit the SAME monolith region -> the second conflicts.
        self._finish_lane("alpha", "line_20 = 20", 'def cmd_alpha(): pass')
        self._finish_lane("beta", "line_20 = 20", 'def cmd_beta(): pass')
        code, out, err = self._mq("--no-gates")
        self.assertNotEqual(code, 0)
        self.assertIn("HALTED", err)
        self.assertIn("beta", err)
        self.assertIn("scripts/agentware", err)
        # The repo is left CLEAN (merge aborted) — no half-applied state.
        self.assertEqual(self._porcelain(self.repo), "")
        # alpha integrated; beta NOT silently merged (never-drop the hard way).
        mono = self._read_integration_monolith()
        self.assertIn("def cmd_alpha()", mono)
        self.assertNotIn("def cmd_beta()", mono)

    def test_merge_queue_gate_failure_halts(self):
        self.assertEqual(self._spin_up("alpha", "beta", "gamma")[0], 0)
        self._finish_lane("alpha", "line_05 = 5", 'def cmd_alpha(): pass')
        self._finish_lane("beta", "line_30 = 30", 'def cmd_beta(): pass')
        self._finish_lane("gamma", "line_15 = 15",
                          'def cmd_gamma(): pass', '# FORBIDDEN_TOKEN here')
        gate = "! grep -q FORBIDDEN_TOKEN scripts/agentware"
        code, out, err = self._mq("--gate", gate)
        self.assertNotEqual(code, 0)
        self.assertIn("gamma", err)
        self.assertIn("gate", err.lower())
        # alpha + beta integrated and gate-passed before gamma's gate failed.
        mono = self._read_integration_monolith()
        self.assertIn("def cmd_alpha()", mono)
        self.assertIn("def cmd_beta()", mono)

    def test_merge_queue_kb_entries_merge_nothing_lost(self):
        self.assertEqual(self._spin_up("alpha", "beta")[0], 0)
        self._finish_lane("alpha", "line_05 = 5", 'def cmd_alpha(): pass',
                          kb_entry="alpha")
        self._finish_lane("beta", "line_30 = 30", 'def cmd_beta(): pass',
                          kb_entry="beta")
        code, out, err = self._mq("--gate", "true")
        self.assertEqual(code, 0, err + out)
        # Integration KB carries ALL entry ids (nothing-lost) + a valid index.
        self.assertEqual(self._integration_kb_ids(),
                         {"learn-seed-one", "learn-alpha", "learn-beta"})

    def test_merge_queue_resume_after_gate_halt(self):
        # Regression (review finding 1): a lane that gate-halts must be resumable —
        # the queue must NOT permanently skip it on package ancestry alone.
        self.assertEqual(self._spin_up("alpha")[0], 0)
        self._finish_lane("alpha", "line_05 = 5", "def cmd_alpha(): pass",
                          kb_entry="alpha")
        # First run: a gate that always fails -> halt; lane NOT marked merged.
        self.assertNotEqual(self._mq("--gate", "false")[0], 0)
        self.assertFalse(self._lanes()["lanes"]["alpha"].get("merged"))
        # Resume with a passing gate -> the lane completes (package no-ops, KB
        # no-ops, gate re-runs) and is finally marked merged.
        code, out, err = self._mq("--gate", "true")
        self.assertEqual(code, 0, err + out)
        self.assertTrue(self._lanes()["lanes"]["alpha"]["merged"])
        self.assertIn("def cmd_alpha()", self._read_integration_monolith())
        self.assertIn("learn-alpha", self._integration_kb_ids())

    def test_mq_integrate_kb_refuses_dirty_tree_no_silent_drop(self):
        # Regression (review finding 2a): a merge REFUSED on a dirty tree must
        # return "error" (halt), NEVER commit a non-merge as "merged" (silent drop).
        self.assertEqual(self._spin_up("alpha")[0], 0)
        self._finish_lane("alpha", "line_05 = 5", "def cmd_alpha(): pass",
                          kb_entry="alpha")
        _run(self.kb_repo, "branch", "integration", "main")
        _run(self.kb_repo, "checkout", "integration")
        # Dirty a tracked file the lane merge would touch -> git refuses the merge.
        with open(os.path.join(self.kb_repo, "index.json"), "a",
                  encoding="utf-8") as f:
            f.write("\n")
        status, _detail = self.cli._mq_integrate_kb(
            self.kb_repo, "integration", "kb/alpha")
        self.assertEqual(status, "error")
        self.assertFalse(self.cli._is_ancestor(
            self.kb_repo, "kb/alpha", "integration"))   # NOT silently merged

    def test_merge_queue_ledger_union_and_gate_artifacts(self):
        # Regression (review findings 2b + 3): append-only ledger conflicts union
        # (no false prose-conflict halt) AND gate-dirtied tree is committed so the
        # next lane merges onto a clean tree (no refusal / silent drop).
        self.assertEqual(self._spin_up("alpha", "beta")[0], 0)
        for f, anchor in (("alpha", "line_05 = 5"), ("beta", "line_30 = 30")):
            self._finish_lane(f, anchor, "def cmd_%s(): pass" % f, kb_entry=f)
            self._append_ledger(f, '{"lane": "%s"}' % f)
        # Gate mimics `eval --record`: appends a row to the INTEGRATION KB ledger.
        gate = ('printf \'{"gate": 1}\\n\' >> '
                '"$AGENTWARE_KNOWLEDGE_DIR/benchmarks/history.jsonl"')
        code, out, err = self._mq("--gate", gate)
        self.assertEqual(code, 0, err + out)
        self.assertTrue(self._lanes()["lanes"]["alpha"]["merged"])
        self.assertTrue(self._lanes()["lanes"]["beta"]["merged"])
        led = self._show(self.kb_repo, "integration:benchmarks/history.jsonl")
        self.assertIn('"lane": "alpha"', led)      # both lanes' rows survive (union)
        self.assertIn('"lane": "beta"', led)
        self.assertIn('"gate": 1', led)            # gate artifacts committed
        self.assertEqual(self._porcelain(self.kb_repo), "")   # clean tree at end

    def test_merge_queue_and_teardown_use_registry_repo_without_flags(self):
        # Regression (review finding 4): the registry is authoritative — merge-queue
        # and teardown work without re-passing --repo/--kb-repo (must NOT fall back
        # to REPO_ROOT for a non-default-repo lane).
        self.assertEqual(self._spin_up("alpha")[0], 0)
        self._finish_lane("alpha", "line_05 = 5", "def cmd_alpha(): pass",
                          kb_entry="alpha")
        code, out, err = self._fanout("fanout", "merge-queue", "--gate", "true")
        self.assertEqual(code, 0, err + out)
        self.assertIn("def cmd_alpha()", self._read_integration_monolith())
        code, out, err = self._fanout("fanout", "teardown", "alpha")
        self.assertEqual(code, 0, err)
        self.assertEqual(self._lanes()["lanes"], {})

    def test_merge_queue_dry_run_writes_nothing(self):
        self.assertEqual(self._spin_up("alpha")[0], 0)
        self._finish_lane("alpha", "line_05 = 5", 'def cmd_alpha(): pass')
        code, out, err = self._mq("--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("dry-run", out.lower())
        self.assertFalse(self._has_branch(self.repo, "integration"))
