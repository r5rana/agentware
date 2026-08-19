"""Tests for `agentware fanout` — parallel-lane orchestration.

Hermetic: real git via subprocess for repo/worktree setup, the CLI driven
in-process (importlib) via run_cli. Each test pins AGENTWARE_FANOUT_DIR to a
tempdir so the operator's real ~/.agentware state is NEVER touched, and passes
--repo/--kb-repo explicitly so nothing resolves to the operator's real KB.

Where the real flow would spawn the loop (an LLM), the TEST stands in for the
agent — it stages the lane's finished end-state deterministically (commits on
feat/<f> + kb/<f>, writes the .loop/.done sentinel, flips plan markers) and then
exercises the deterministic CLI machinery. Zero claude spawned.
"""

from tests.integration._fanout_support import (  # noqa: F401
    FanoutTestBase, _run, json, os, subprocess)


class FanoutSpinUpTest(FanoutTestBase):

    def test_spin_up_creates_worktrees_branches_env_and_registry(self):
        code, out, err = self._spin_up("alpha", "beta")
        self.assertEqual(code, 0, err)
        for f in ("alpha", "beta"):
            pkg_wt = os.path.join(self.fanout_dir, "worktrees", f, "pkg")
            kb_wt = os.path.join(self.fanout_dir, "worktrees", f, "kb")
            self.assertTrue(os.path.isdir(pkg_wt), "pkg worktree for %s" % f)
            self.assertTrue(os.path.isdir(kb_wt), "kb worktree for %s" % f)
            # The worktree is a checkout (has a .git file/dir link).
            self.assertTrue(os.path.exists(os.path.join(pkg_wt, ".git")))
            self.assertTrue(self._has_branch(self.repo, "feat/%s" % f))
            self.assertTrue(self._has_branch(self.kb_repo, "kb/%s" % f))
            # Env file pins AGENTWARE_KNOWLEDGE_DIR to the KB worktree.
            env_file = os.path.join(self.fanout_dir, "env", "%s.env" % f)
            self.assertTrue(os.path.isfile(env_file))
            with open(env_file, encoding="utf-8") as fh:
                env_txt = fh.read()
            self.assertIn("AGENTWARE_KNOWLEDGE_DIR=%s" % kb_wt, env_txt)

        lanes = self._lanes()["lanes"]
        self.assertEqual(set(lanes), {"alpha", "beta"})
        self.assertEqual(lanes["alpha"]["feat_branch"], "feat/alpha")
        self.assertEqual(lanes["alpha"]["kb_branch"], "kb/alpha")
        self.assertEqual(lanes["alpha"]["merged"], False)

    def test_spin_up_dry_run_writes_nothing(self):
        code, out, err = self._fanout(
            "fanout", "spin-up", "--repo", self.repo, "--kb-repo", self.kb_repo,
            "--dry-run", "gamma")
        self.assertEqual(code, 0, err)
        self.assertIn("dry-run", out.lower())
        # Nothing was created: no registry, no worktree, no branch.
        self.assertFalse(os.path.exists(os.path.join(self.fanout_dir, "lanes.json")))
        self.assertFalse(os.path.exists(
            os.path.join(self.fanout_dir, "worktrees", "gamma")))
        self.assertFalse(self._has_branch(self.repo, "feat/gamma"))

    def test_spin_up_json_dry_run(self):
        code, out, err = self._fanout(
            "fanout", "spin-up", "--format", "json", "--repo", self.repo,
            "--kb-repo", self.kb_repo, "--dry-run", "delta")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["lanes"][0]["feature"], "delta")
        self.assertEqual(payload["lanes"][0]["action"], "would-create")

    def test_spin_up_is_idempotent(self):
        self.assertEqual(self._spin_up("alpha")[0], 0)
        code, out, err = self._spin_up("alpha")
        self.assertEqual(code, 0, err)
        self.assertIn("exists", out)
        # Still exactly one lane, one worktree.
        self.assertEqual(list(self._lanes()["lanes"]), ["alpha"])

    def test_spin_up_refuses_to_clobber_existing_branch(self):
        # A pre-existing feat/x branch this command does not own must be refused.
        _run(self.repo, "branch", "feat/epsilon")
        code, out, err = self._spin_up("epsilon")
        self.assertEqual(code, 1)
        self.assertIn("refusing to clobber", err)
        self.assertFalse(os.path.exists(os.path.join(self.fanout_dir, "lanes.json")))

    def test_spin_up_rejects_invalid_feature_name(self):
        code, out, err = self._spin_up("bad/name")
        self.assertEqual(code, 1)
        self.assertIn("invalid feature name", err)

class FanoutListTest(FanoutTestBase):

    def _write_lane_plan(self, feature, statuses):
        """Write a plan.md with the given marker glyphs into the lane KB worktree."""
        work = os.path.join(self.fanout_dir, "worktrees", feature, "kb",
                            "work", feature)
        os.makedirs(work, exist_ok=True)
        lines = ["# Plan\n\n"]
        for i, g in enumerate(statuses, 1):
            lines.append("- %s **%d** task %d\n" % (g, i, i))
        with open(os.path.join(work, "plan.md"), "w", encoding="utf-8") as f:
            f.writelines(lines)
        return work

    def _list_json(self):
        code, out, err = self._fanout("fanout", "list", "--format", "json")
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_list_empty_is_clean(self):
        payload = self._list_json()
        self.assertEqual(payload, {"lanes": []})

    def test_list_reports_lanes_with_required_keys(self):
        self.assertEqual(self._spin_up("alpha", "beta")[0], 0)
        payload = self._list_json()
        feats = {l["feature"]: l for l in payload["lanes"]}
        self.assertEqual(set(feats), {"alpha", "beta"})
        a = feats["alpha"]
        # Acceptance: {feature, feat_branch, kb_path, outcome}.
        for key in ("feature", "feat_branch", "kb_path", "outcome"):
            self.assertIn(key, a)
        self.assertEqual(a["feat_branch"], "feat/alpha")
        self.assertTrue(a["kb_path"].endswith(os.path.join("alpha", "kb")))
        # Freshly spun-up (no plan.md seeded yet) → provisioned.
        self.assertEqual(a["outcome"], "provisioned")

    def test_list_outcome_in_progress(self):
        self.assertEqual(self._spin_up("alpha")[0], 0)
        self._write_lane_plan("alpha", ["⬜", "✅"])  # one open marker remains
        feats = {l["feature"]: l for l in self._list_json()["lanes"]}
        self.assertEqual(feats["alpha"]["outcome"], "in_progress")

    def test_list_outcome_completed(self):
        self.assertEqual(self._spin_up("alpha")[0], 0)
        self._write_lane_plan("alpha", ["✅", "✅"])  # all done, no worklog
        feats = {l["feature"]: l for l in self._list_json()["lanes"]}
        self.assertEqual(feats["alpha"]["outcome"], "completed")

    def test_list_outcome_blocked_on_unpromoted_marker(self):
        self.assertEqual(self._spin_up("alpha")[0], 0)
        work = self._write_lane_plan("alpha", ["✅"])
        with open(os.path.join(work, "worklog.md"), "w", encoding="utf-8") as f:
            f.write("> LEARNED: something not yet promoted to the KB\n")
        feats = {l["feature"]: l for l in self._list_json()["lanes"]}
        self.assertEqual(feats["alpha"]["outcome"], "blocked")

    def test_list_outcome_merged_flag(self):
        self.assertEqual(self._spin_up("alpha")[0], 0)
        # Flip the registry flag the merge-queue would set.
        reg = self._lanes()
        reg["lanes"]["alpha"]["merged"] = True
        with open(os.path.join(self.fanout_dir, "lanes.json"), "w",
                  encoding="utf-8") as f:
            json.dump(reg, f)
        feats = {l["feature"]: l for l in self._list_json()["lanes"]}
        self.assertEqual(feats["alpha"]["outcome"], "merged")

class FanoutMergePolicyTest(FanoutTestBase):

    def _check_attr_merge(self, repo, path):
        cp = subprocess.run(
            ["git", "-C", repo, "check-attr", "merge", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return cp.stdout.strip()

    def test_merge_policy_installs_union_attributes(self):
        code, out, err = self._fanout("fanout", "merge-policy", "--kb", self.kb_repo)
        self.assertEqual(code, 0, err)
        ga = os.path.join(self.kb_repo, ".gitattributes")
        self.assertTrue(os.path.isfile(ga))
        with open(ga, encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("benchmarks/history.jsonl merge=union", txt)
        self.assertIn("**/index.md merge=union", txt)
        # Acceptance: check-attr reports union for the append-only ledger.
        self.assertTrue(
            self._check_attr_merge(self.kb_repo, "benchmarks/history.jsonl")
            .endswith("merge: union"))
        self.assertTrue(
            self._check_attr_merge(self.kb_repo, "learnings/index.md")
            .endswith("merge: union"))

    def test_merge_policy_is_idempotent(self):
        self.assertEqual(self._fanout("fanout", "merge-policy", "--kb",
                                      self.kb_repo)[0], 0)
        ga = os.path.join(self.kb_repo, ".gitattributes")
        with open(ga, encoding="utf-8") as f:
            first = f.read()
        code, out, err = self._fanout("fanout", "merge-policy", "--kb", self.kb_repo)
        self.assertEqual(code, 0, err)
        self.assertIn("already current", out)
        with open(ga, encoding="utf-8") as f:
            second = f.read()
        self.assertEqual(first, second)
        # Exactly one managed block.
        self.assertEqual(second.count("agentware fanout merge-policy >>>"), 1)

    def test_merge_policy_preserves_existing_content(self):
        ga = os.path.join(self.kb_repo, ".gitattributes")
        with open(ga, "w", encoding="utf-8") as f:
            f.write("*.bin binary\n")
        self.assertEqual(self._fanout("fanout", "merge-policy", "--kb",
                                      self.kb_repo)[0], 0)
        with open(ga, encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("*.bin binary", txt)
        self.assertIn("index.json merge=union", txt)
        # Re-run stays idempotent even with foreign content present.
        self._fanout("fanout", "merge-policy", "--kb", self.kb_repo)
        with open(ga, encoding="utf-8") as f:
            txt2 = f.read()
        self.assertEqual(txt, txt2)

class FanoutTeardownTest(FanoutTestBase):

    def _mq(self, *extra):
        return self._fanout("fanout", "merge-queue", "--repo", self.repo,
                            "--kb-repo", self.kb_repo, *extra)

    def _teardown(self, *extra):
        return self._fanout("fanout", "teardown", "--repo", self.repo,
                            "--kb-repo", self.kb_repo, *extra)

    def _spin_finish_merge(self, feature, anchor):
        self.assertEqual(self._spin_up(feature)[0], 0)
        self._finish_lane(feature, anchor, "def cmd_%s(): pass" % feature)
        self.assertEqual(self._mq("--gate", "true")[0], 0)

    def test_teardown_removes_merged_lane(self):
        self._spin_finish_merge("alpha", "line_05 = 5")
        pkg = self._pkg_wt("alpha")
        kb = self._kb_wt("alpha")
        code, out, err = self._teardown("alpha")
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(pkg))            # worktree removed
        self.assertFalse(os.path.exists(kb))
        self.assertFalse(self._has_branch(self.repo, "feat/alpha"))  # branch deleted
        self.assertFalse(self._has_branch(self.kb_repo, "kb/alpha"))
        self.assertEqual(self._lanes()["lanes"], {})     # registry entry gone
        self.assertFalse(os.path.exists(
            os.path.join(self.fanout_dir, "env", "alpha.env")))

    def test_teardown_refuses_unmerged_lane(self):
        self.assertEqual(self._spin_up("alpha")[0], 0)   # never merged
        code, out, err = self._teardown("alpha")
        self.assertEqual(code, 1)
        self.assertIn("refusing alpha", err)   # deterministic refusal, no prompt
        # Nothing removed.
        self.assertTrue(os.path.exists(self._pkg_wt("alpha")))
        self.assertTrue(self._has_branch(self.repo, "feat/alpha"))
        self.assertIn("alpha", self._lanes()["lanes"])

    def test_teardown_refuses_lane_absent_from_integration(self):
        # alpha merges (so integration exists); beta stays out -> refused.
        self.assertEqual(self._spin_up("alpha", "beta")[0], 0)
        self._finish_lane("alpha", "line_05 = 5", "def cmd_alpha(): pass")
        self._finish_lane("beta", "line_30 = 30", "def cmd_beta(): pass")
        self.assertEqual(self._mq("alpha", "--gate", "true")[0], 0)  # only alpha
        code, out, err = self._teardown("beta")
        self.assertEqual(code, 1)
        self.assertIn("feat/beta is not merged", err)
        self.assertTrue(os.path.exists(self._pkg_wt("beta")))

    def test_teardown_refuses_dirty_lane(self):
        self.assertEqual(self._spin_up("alpha")[0], 0)
        # Dirty the package worktree (modify a tracked file, no commit).
        with open(os.path.join(self._pkg_wt("alpha"), "scripts", "agentware"),
                  "a", encoding="utf-8") as f:
            f.write("\n# uncommitted edit\n")
        code, out, err = self._teardown("alpha")
        self.assertEqual(code, 1)
        self.assertIn("uncommitted changes", err)
        self.assertTrue(os.path.exists(self._pkg_wt("alpha")))

    def test_teardown_force_removes_unmerged_lane(self):
        self.assertEqual(self._spin_up("alpha")[0], 0)   # unmerged
        code, out, err = self._teardown("alpha", "--force")
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(self._pkg_wt("alpha")))
        self.assertFalse(self._has_branch(self.repo, "feat/alpha"))
        self.assertEqual(self._lanes()["lanes"], {})

    def test_teardown_dry_run_removes_nothing(self):
        self._spin_finish_merge("alpha", "line_05 = 5")
        code, out, err = self._teardown("alpha", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("would-remove", out)
        self.assertTrue(os.path.exists(self._pkg_wt("alpha")))
        self.assertIn("alpha", self._lanes()["lanes"])

class FanoutEndToEndTest(FanoutTestBase):
    """The whole flow in a temp git sandbox: spin-up -> drive each lane's loop to
    .loop/.done (TEST stands in for the agent) -> merge-queue (never-drop + a
    gate-regressing lane HALTS) -> teardown cleans every worktree/branch."""

    def _fan(self, *argv):
        return self._fanout(*argv)

    def test_e2e_full_parallel_flow(self):
        # 1) Spin up THREE isolated lanes.
        self.assertEqual(self._spin_up("alpha", "beta", "gamma")[0], 0)

        # 2) Drive each lane's loop to .loop/.done — disjoint subcommands + KB
        #    entries; gamma deliberately regresses a gate (FORBIDDEN_TOKEN).
        self._finish_lane("alpha", "line_05 = 5",
                          'def cmd_alpha(): return "A"', kb_entry="alpha")
        self._finish_lane("beta", "line_30 = 30",
                          'def cmd_beta(): return "B"', kb_entry="beta")
        self._finish_lane("gamma", "line_15 = 15",
                          'def cmd_gamma(): pass', '# FORBIDDEN_TOKEN here',
                          kb_entry="gamma")

        # 3) Each lane reads as completed.
        outs = {l["feature"]: l["outcome"] for l in json.loads(
            self._fan("fanout", "list", "--format", "json")[1])["lanes"]}
        self.assertEqual(outs, {"alpha": "completed", "beta": "completed",
                                "gamma": "completed"})

        # 4) Merge queue with a gate that fails iff FORBIDDEN_TOKEN is present.
        gate = "! grep -q FORBIDDEN_TOKEN scripts/agentware"
        code, out, err = self._fan("fanout", "merge-queue", "--repo", self.repo,
                                   "--kb-repo", self.kb_repo, "--gate", gate)
        # gamma's gate FAILS -> the queue HALTS deterministically.
        self.assertNotEqual(code, 0)
        self.assertIn("gamma", err)
        self.assertIn("HALTED", err)

        # 5) never-drop: BOTH disjoint subcommands integrated before the halt.
        mono = self._read_integration_monolith()
        self.assertIn("def cmd_alpha()", mono)
        self.assertIn("def cmd_beta()", mono)
        # KB nothing-lost for the integrated lanes.
        ids = self._integration_kb_ids()
        self.assertIn("learn-alpha", ids)
        self.assertIn("learn-beta", ids)
        self.assertIn("learn-seed-one", ids)

        # 6) Teardown cleans every worktree + branch (all three are merged into
        #    integration — gamma's merge committed before its gate ran).
        td = self._fan("fanout", "teardown", "--repo", self.repo,
                       "--kb-repo", self.kb_repo, "alpha", "beta", "gamma")
        self.assertEqual(td[0], 0, td[2])
        for f in ("alpha", "beta", "gamma"):
            self.assertFalse(os.path.exists(self._pkg_wt(f)))
            self.assertFalse(os.path.exists(self._kb_wt(f)))
            self.assertFalse(self._has_branch(self.repo, "feat/%s" % f))
            self.assertFalse(self._has_branch(self.kb_repo, "kb/%s" % f))
        self.assertEqual(self._lanes()["lanes"], {})
