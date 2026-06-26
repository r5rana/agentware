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

import json
import os
import shutil
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, run_cli
except ImportError:  # pragma: no cover - direct `pytest tests/test_fanout.py`
    from _fixtures import load_cli, run_cli


def _have_git():
    try:
        subprocess.run(["git", "--version"], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, check=True)
        return True
    except Exception:  # pragma: no cover
        return False


HAVE_GIT = _have_git()


def _run(cwd, *args):
    """Run a git command in `cwd`, raising on failure (test setup must succeed)."""
    return subprocess.run(["git", "-C", cwd] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=True)


def _entry(eid):
    """A frontmatter-ready entry dict (the rebuild_kb source of truth)."""
    return {
        "id": "learn-%s" % eid, "title": "Seed %s" % eid,
        "category": "learnings", "path": "learnings/%s.md" % eid,
        "tags": ["seed", eid], "created": "2026-01-01",
        "summary": "Seed entry %s." % eid,
        "body": "# Seed %s\n\nSeed knowledge body for %s.\n" % (eid, eid),
    }


def _write_fm_entry(cli, repo, eid):
    """Materialize one entry .md WITH frontmatter under `repo` (rebuild source)."""
    entry = _entry(eid)
    abs_path = os.path.join(repo, entry["path"])
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    fm = cli.build_entry_frontmatter(entry, repo, author="test")
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(cli.render_frontmatter(fm) + entry["body"])


@unittest.skipUnless(HAVE_GIT, "git not available")
class FanoutTestBase(unittest.TestCase):
    """Stands up two throwaway git repos (a toy package + a synthetic KB) and
    pins fanout state to a tempdir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-test-fanout-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # Pin fanout state to a tempdir — never touch ~/.agentware.
        self.fanout_dir = os.path.join(self.tmp, "fanout-home")
        self._prev_fanout = os.environ.get("AGENTWARE_FANOUT_DIR")
        os.environ["AGENTWARE_FANOUT_DIR"] = self.fanout_dir
        self.addCleanup(self._restore_fanout)
        self.cli = load_cli()
        self.repo = self._seed_pkg_repo("pkg")
        self.kb_repo = self._seed_kb_repo("kb")

    def _restore_fanout(self):
        if self._prev_fanout is None:
            os.environ.pop("AGENTWARE_FANOUT_DIR", None)
        else:
            os.environ["AGENTWARE_FANOUT_DIR"] = self._prev_fanout

    # -- helpers --------------------------------------------------------------
    def _mk(self, name):
        return os.path.join(self.tmp, name)

    def _seed_pkg_repo(self, name):
        """A toy 'package' repo whose `scripts/agentware` monolith carries widely
        separated anchor lines, so disjoint lane edits rebase cleanly while
        same-line edits conflict (the merge-queue's monolith chokepoint)."""
        path = self._mk(name)
        os.makedirs(os.path.join(path, "scripts"), exist_ok=True)
        lines = ["# toy agentware monolith (test stand-in)", ""]
        lines += ["line_%02d = %d" % (i, i) for i in range(40)]
        with open(os.path.join(path, "scripts", "agentware"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        _run(path, "init", "-q", "-b", "main")
        _run(path, "config", "user.email", "test@example.com")
        _run(path, "config", "user.name", "Test")
        _run(path, "add", "-A")
        _run(path, "commit", "-q", "-m", "seed pkg")
        return path

    def _seed_kb_repo(self, name):
        """A valid frontmatter KB committed as a git repo (so kb/<f> worktrees
        branch off it AND rebuild_kb stays valid through merges)."""
        path = self._mk(name)
        for sub in ("learnings", "projects", "configurations", "prompts",
                    "references", "skills"):
            os.makedirs(os.path.join(path, sub), exist_ok=True)
        _write_fm_entry(self.cli, path, "seed-one")
        data, _rosters, errors = self.cli.rebuild_kb(path)
        self.assertEqual(errors, [], "seed rebuild must be clean")
        # Mirror the real KB: loop state is gitignored, so a finished lane's KB
        # worktree reads CLEAN (the .loop/.done sentinel is not "dirty").
        with open(os.path.join(path, ".gitignore"), "w", encoding="utf-8") as f:
            f.write(".loop/\nlogs/\n")
        # An append-only ledger (like benchmarks/history.jsonl) so concurrent-lane
        # EOF-append conflicts can be exercised.
        os.makedirs(os.path.join(path, "benchmarks"), exist_ok=True)
        with open(os.path.join(path, "benchmarks", "history.jsonl"),
                  "w", encoding="utf-8") as f:
            f.write('{"seed": true}\n')
        _run(path, "init", "-q", "-b", "main")
        _run(path, "config", "user.email", "test@example.com")
        _run(path, "config", "user.name", "Test")
        _run(path, "add", "-A")
        _run(path, "commit", "-q", "-m", "seed kb")
        return path

    def _fanout(self, *argv):
        """Run the fanout CLI in-process; returns (rc, out, err)."""
        return run_cli(list(argv), self.kb_repo)

    def _spin_up(self, *features, base=None):
        argv = ["fanout", "spin-up", "--repo", self.repo,
                "--kb-repo", self.kb_repo]
        if base:
            argv += ["--base", base]
        argv += list(features)
        return self._fanout(*argv)

    def _lanes(self):
        path = os.path.join(self.fanout_dir, "lanes.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _has_branch(self, repo, branch):
        return subprocess.run(
            ["git", "-C", repo, "rev-parse", "--verify", "--quiet",
             "refs/heads/%s" % branch],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0

    # -- lane-finishing helpers (the TEST stands in for the loop/agent) --------
    def _pkg_wt(self, feature):
        return os.path.join(self.fanout_dir, "worktrees", feature, "pkg")

    def _kb_wt(self, feature):
        return os.path.join(self.fanout_dir, "worktrees", feature, "kb")

    def _insert_after(self, worktree, anchor, *newlines):
        """Insert lines after the first occurrence of `anchor` in the monolith."""
        p = os.path.join(worktree, "scripts", "agentware")
        with open(p, encoding="utf-8") as f:
            lines = f.read().splitlines()
        out = []
        for ln in lines:
            out.append(ln)
            if ln == anchor:
                out.extend(newlines)
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")

    def _finish_lane(self, feature, anchor, *subcmd_lines, kb_entry=None):
        """Stage a lane's FINISHED end-state deterministically (no loop/LLM):
        a disjoint subcommand committed on feat/<f>, an optional KB entry committed
        on kb/<f>, and the `.loop/.done` sentinel so the outcome reads completed."""
        pkg = self._pkg_wt(feature)
        self._insert_after(pkg, anchor, *subcmd_lines)
        _run(pkg, "add", "-A")
        _run(pkg, "commit", "-q", "-m", "feat %s" % feature)
        kb = self._kb_wt(feature)
        if kb_entry:
            _write_fm_entry(self.cli, kb, kb_entry)
            _data, _r, errs = self.cli.rebuild_kb(kb)
            self.assertEqual(errs, [], "lane kb rebuild must be clean")
            _run(kb, "add", "-A")
            _run(kb, "commit", "-q", "-m", "kb %s" % feature)
        loop = os.path.join(kb, "work", feature, ".loop")
        os.makedirs(loop, exist_ok=True)
        open(os.path.join(loop, ".done"), "w").close()

    def _read_integration_monolith(self, branch="integration"):
        cp = subprocess.run(
            ["git", "-C", self.repo, "show", "%s:scripts/agentware" % branch],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return cp.stdout

    def _integration_kb_ids(self, branch="integration"):
        cp = subprocess.run(
            ["git", "-C", self.kb_repo, "show", "%s:index.json" % branch],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        data = json.loads(cp.stdout)
        return {e["id"] for e in data["entries"]}

    def _porcelain(self, repo):
        return subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                              stdout=subprocess.PIPE, text=True).stdout.strip()

    def _append_ledger(self, feature, line):
        """Append a row to the lane KB worktree's append-only ledger + commit."""
        kb = self._kb_wt(feature)
        with open(os.path.join(kb, "benchmarks", "history.jsonl"),
                  "a", encoding="utf-8") as f:
            f.write(line + "\n")
        _run(kb, "add", "-A")
        _run(kb, "commit", "-q", "-m", "ledger %s" % feature)

    def _show(self, repo, ref_path):
        return subprocess.run(["git", "-C", repo, "show", ref_path],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True).stdout


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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
