"""`mqteardown`'s own unit file — task 99 wave 7, landed with the paydown.

`agentware_lib.features.fanoutmq.mqteardown` is the only place in this package
that deletes a branch. Everything else in the fanout family creates worktrees,
merges, or reports; this one runs `branch -D` and `worktree remove`, which is
why its whole design is REFUSAL-first (R-GIT-02) and why the refusal message,
not the return value, is the operator's contract.

Until this file the module had no owning test. `tests/integration/test_fanout.py`
drives teardown against real worktrees, which is the right test for "does a
worktree actually go away" and the wrong one for the decisions here: it cannot
make `_remove_worktree` fail on the KB side only, cannot make the `env_file`
unlink raise, and pays a real `git worktree add` per case.

This wave split `cmd_fanout_teardown` (16 branches -> 6) into
`_teardown_one_lane` and `_teardown_report`. The properties:

  BLOCK/REASONS     — the safety gate returns ONE reason per failing condition,
                      checked in a fixed order: dirty worktree, then a missing
                      integration branch, then an unmerged feat branch, then an
                      unmerged KB branch. Order matters because the later checks
                      would all pass on a lane whose worktree is simply dirty.
  BLOCK/EMPTY-PATH  — a record with no worktree path is not "clean"; the
                      `os.path.isdir("")` short-circuit means it is not even
                      looked at, and the merge checks decide.
  LANE/ORDER        — the safety gate is consulted BEFORE anything is removed,
                      --dry-run answers before that, worktrees go BEFORE their
                      branches, and the registry entry is dropped LAST. Each is
                      asserted on a fixture rigged to fail at two points, so a
                      reordering names a different one.
  LANE/FORCE        — --force skips the safety gate entirely and is threaded
                      through to `worktree remove --force`. It is the ONLY way
                      an unmerged lane is ever removed.
  LANE/STATUS       — the four statuses, and the rule that `detail` is None for
                      the two that succeeded so the caller omits the key. The
                      json shape depends on that omission.
  LANE/PARTIAL      — a failure leaves the lane REGISTERED, so the operator can
                      fix the cause and re-run.
  REPORT/TEXT-SCOPED— text drops `error`/`refused` rows because stderr already
                      carried them; json keeps every row because it has no
                      stderr to defer to.
  QUEUE/RC          — rc is 1 iff ANY lane failed, and one failing lane never
                      stops the ones after it.

Hermetic: no git, no subprocess, no network. Every collaborator is injected at
the module seam it is reached through, and the three places the product asks the
FILESYSTEM a question are answered with real scratch paths rather than a patched
stdlib predicate — patching `os.path.isdir` / `os.path.exists` / `os.remove` for
one assertion is how a restore ends up capturing the already-patched value and
leaking the mutant into the next test.
"""

from tests._paths import TESTS_DIR

import io
import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.core.config as config  # noqa: E402
import agentware_lib.features.fanout as fanout  # noqa: E402
import agentware_lib.ops.kbgit as kbgit  # noqa: E402
import agentware_lib.features.fanoutmq.mqbranch as mqbranch  # noqa: E402
import agentware_lib.features.fanoutmq.mqteardown as mqt  # noqa: E402


class _Args(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _raiser(label):
    def _fn(*_a, **_k):
        raise AssertionError("reached: %s" % label)
    return _fn


def _capture(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rv = fn(*a, **k)
    return rv, out.getvalue(), err.getvalue()


def _rec(feature="alpha", **kw):
    rec = {"feature": feature, "feat_branch": "feat/%s" % feature,
           "kb_branch": "kb/%s" % feature,
           "worktree": "/wt/%s/pkg" % feature, "kb_path": "/wt/%s/kb" % feature,
           "env_file": "/env/%s.env" % feature, "repo": "/pkg", "kb_repo": "/kb"}
    rec.update(kw)
    return rec


def _reg(*features, **kw):
    return {"lanes": {f: _rec(f, **kw) for f in features}}


def _opts(**kw):
    o = {"fmt_json": False, "dry": False, "force": False,
         "integration": "integration"}
    o.update(kw)
    return o


class SeamRestoringTest(unittest.TestCase):
    def setUp(self):
        self._saved = [
            (mqt, "_teardown_blocker", mqt._teardown_blocker),
            (mqt, "_remove_worktree", mqt._remove_worktree),
            (mqt, "_teardown_one_lane", mqt._teardown_one_lane),
            (mqt, "_git", mqt._git),
            (fanout, "save_lanes", fanout.save_lanes),
            (fanout, "load_lanes", fanout.load_lanes),
            (fanout, "_branch_exists", fanout._branch_exists),
            (kbgit, "git_is_work_tree", kbgit.git_is_work_tree),
            (kbgit, "git_is_clean", kbgit.git_is_clean),
            (mqbranch, "_lane_repo_args", mqbranch._lane_repo_args),
            (mqbranch, "_is_ancestor", mqbranch._is_ancestor),
            (config, "resolve_knowledge_dir", config.resolve_knowledge_dir),
        ]
        for mod, name, val in self._saved:
            self.addCleanup(setattr, mod, name, val)

    def patch(self, mod, name, value):
        setattr(mod, name, value)


# --- BLOCKER ----------------------------------------------------------------

class BlockerTest(SeamRestoringTest):
    """BLOCK/REASONS, BLOCK/EMPTY-PATH."""

    def setUp(self):
        super(BlockerTest, self).setUp()
        # REAL directories rather than a patched `os.path.isdir`. Patching a
        # stdlib predicate leaks process-wide if a cleanup is registered even
        # one line late, which this file learned the hard way: the restore
        # captured the ALREADY-PATCHED value and the next test in the class ran
        # against the mutant.
        self.tmp = tempfile.mkdtemp(prefix="aw-blocker-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pkg_wt = os.path.join(self.tmp, "pkg")
        self.kb_wt = os.path.join(self.tmp, "kb")
        os.makedirs(self.pkg_wt)
        os.makedirs(self.kb_wt)
        self.patch(kbgit, "git_is_work_tree", lambda p: True)
        self.patch(kbgit, "git_is_clean", lambda p: True)
        self.patch(fanout, "_branch_exists", lambda repo, b: True)
        self.patch(mqbranch, "_is_ancestor", lambda repo, b, i: True)

    def _rec(self, **kw):
        return _rec(worktree=self.pkg_wt, kb_path=self.kb_wt, **kw)

    def _blocker(self, rec=None, have_kb=True):
        return mqt._teardown_blocker(rec if rec is not None else self._rec(),
                                     "/pkg", "/kb", "integration", have_kb)

    def test_a_fully_merged_clean_lane_has_no_blocker(self):
        self.assertIsNone(self._blocker())

    def test_a_dirty_package_worktree_blocks_and_names_itself(self):
        self.patch(kbgit, "git_is_clean", lambda p: p != self.pkg_wt)
        self.assertEqual(self._blocker(),
                         "package worktree has uncommitted changes")

    def test_a_dirty_kb_worktree_blocks_and_names_itself(self):
        self.patch(kbgit, "git_is_clean", lambda p: p != self.kb_wt)
        self.assertEqual(self._blocker(), "KB worktree has uncommitted changes")

    def test_a_dirty_worktree_is_reported_before_the_merge_checks(self):
        # Rigged to fail BOTH: a reordering would report "not merged" instead.
        self.patch(kbgit, "git_is_clean", lambda p: False)
        self.patch(fanout, "_branch_exists", lambda repo, b: False)
        self.assertEqual(self._blocker(),
                         "package worktree has uncommitted changes")

    def test_a_missing_integration_branch_blocks_before_the_ancestry_check(self):
        self.patch(fanout, "_branch_exists", lambda repo, b: False)
        self.patch(mqbranch, "_is_ancestor", _raiser("ancestry check"))
        self.assertIn("does not exist (nothing merged yet)", self._blocker())

    def test_an_unmerged_feat_branch_blocks_and_names_the_branch(self):
        self.patch(mqbranch, "_is_ancestor", lambda repo, b, i: False)
        self.assertEqual(self._blocker(),
                         "feat/alpha is not merged into integration")

    def test_an_unmerged_kb_branch_blocks_and_names_the_kb_branch(self):
        self.patch(mqbranch, "_is_ancestor",
                   lambda repo, b, i: not b.startswith("kb/"))
        self.assertEqual(self._blocker(),
                         "kb/alpha is not merged into integration")

    def test_the_kb_ancestry_check_is_skipped_without_a_kb(self):
        self.patch(mqbranch, "_is_ancestor",
                   lambda repo, b, i: not b.startswith("kb/"))
        self.assertIsNone(self._blocker(have_kb=False))

    def test_the_kb_ancestry_check_is_skipped_when_the_kb_has_no_such_branch(self):
        # A KB repo with no integration branch is not an unmerged lane: nothing
        # was ever merged there, so there is nothing for the lane to be behind.
        self.patch(fanout, "_branch_exists",
                   lambda repo, b: repo != "/kb")
        self.patch(mqbranch, "_is_ancestor",
                   lambda repo, b, i: not b.startswith("kb/"))
        self.assertIsNone(self._blocker())

    def test_an_empty_worktree_path_is_never_probed_for_dirtiness(self):
        self.patch(kbgit, "git_is_work_tree", _raiser("work tree probe"))
        self.assertIsNone(self._blocker(_rec(worktree="", kb_path="")))

    def test_a_record_missing_the_worktree_keys_entirely_is_tolerated(self):
        self.patch(kbgit, "git_is_work_tree", _raiser("work tree probe"))
        rec = _rec()
        del rec["worktree"]
        del rec["kb_path"]
        self.assertIsNone(self._blocker(rec))


# --- REMOVE-WORKTREE ---------------------------------------------------------

class RemoveWorktreeTest(SeamRestoringTest):
    """The remover's two shapes: prune-when-gone, and remove-with-force."""

    def setUp(self):
        super(RemoveWorktreeTest, self).setUp()
        self.calls = []
        self.tmp = tempfile.mkdtemp(prefix="aw-remove-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        #: A path that EXISTS, so the remover takes its remove arm for real.
        self.live = os.path.join(self.tmp, "wt")
        os.makedirs(self.live)
        #: A path under the same root that does NOT exist, so the prune arm is
        #: reached by the filesystem's own answer rather than a patched one.
        self.gone = os.path.join(self.tmp, "already-gone")

        class _CP(object):
            def __init__(self, rc=0, stderr=""):
                self.returncode = rc
                self.stderr = stderr
        self.rc = 0
        self.stderr = ""

        def _git(repo, *args, **kw):
            self.calls.append((repo,) + args)
            return _CP(self.rc, self.stderr)
        self.patch(mqt, "_git", _git)

    def test_an_absent_path_prunes_stale_metadata_and_succeeds(self):
        self.assertEqual(mqt._remove_worktree("/pkg", "", False), (True, None))
        self.assertEqual(self.calls, [("/pkg", "worktree", "prune")])

    def test_a_nonexistent_dir_prunes_rather_than_removing(self):
        self.assertEqual(mqt._remove_worktree("/pkg", self.gone, True),
                         (True, None))
        self.assertEqual(self.calls, [("/pkg", "worktree", "prune")])

    def test_force_is_threaded_into_the_git_argv(self):
        mqt._remove_worktree("/pkg", self.live, True)
        self.assertEqual(
            self.calls,
            [("/pkg", "worktree", "remove", "--force", self.live)])

    def test_without_force_the_flag_is_absent(self):
        mqt._remove_worktree("/pkg", self.live, False)
        self.assertEqual(self.calls,
                         [("/pkg", "worktree", "remove", self.live)])

    def test_a_failing_git_returns_its_stripped_stderr(self):
        self.rc, self.stderr = 1, "  is dirty  \n"
        self.assertEqual(mqt._remove_worktree("/pkg", self.live, False),
                         (False, "is dirty"))


# --- ONE LANE ----------------------------------------------------------------

class OneLaneTest(SeamRestoringTest):
    """LANE/ORDER, LANE/FORCE, LANE/STATUS, LANE/PARTIAL."""

    def setUp(self):
        super(OneLaneTest, self).setUp()
        self.order = []
        self.patch(mqbranch, "_lane_repo_args",
                   lambda rec, args, r, k: ("/pkg", "/kb"))
        self.patch(config, "resolve_knowledge_dir", lambda: "/kb")
        self.patch(kbgit, "git_is_work_tree", lambda p: True)
        self.patch(mqt, "_teardown_blocker",
                   lambda *a, **k: self.order.append("blocker") or None)
        self.patch(fanout, "save_lanes",
                   lambda reg: self.order.append("save"))
        self.patch(mqt, "_git",
                   lambda repo, *a, **k: self.order.append("branch -D %s" % a[-1]))
        # A REAL env file, so the unlink arm is exercised by the filesystem
        # rather than by a patched `os.remove`; its absence is what the
        # already-gone row uses, and that is a real OSError, not a fake one.
        self.tmp = tempfile.mkdtemp(prefix="aw-lane-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env_file = os.path.join(self.tmp, "alpha.env")
        with open(self.env_file, "w", encoding="utf-8") as f:
            f.write("AGENTWARE_KNOWLEDGE_DIR=/kb\n")
        self._remover({})

    def _remover(self, failures):
        def _fn(repo, path, force):
            self.order.append("remove %s" % repo)
            if repo in failures:
                return False, failures[repo]
            return True, None
        self.patch(mqt, "_remove_worktree", _fn)

    def _blocker(self, reason):
        self.patch(mqt, "_teardown_blocker",
                   lambda *a, **k: self.order.append("blocker") or reason)

    def _reg(self, *features, **kw):
        return {"lanes": {f: _rec(f, env_file=self.env_file, **kw)
                          for f in features}}

    def _run(self, reg, feature="alpha", **kw):
        return _capture(mqt._teardown_one_lane, feature, reg,
                        _Args(repo=None, kb_repo=None), _opts(**kw))

    # -- statuses ---------------------------------------------------------
    def test_an_unknown_lane_is_an_error_with_a_named_stderr_line(self):
        (status, detail), _o, err = self._run(self._reg("alpha"), feature="nope")
        self.assertEqual((status, detail), ("error", "unknown lane"))
        self.assertEqual(err, "fanout teardown: unknown lane: nope\n")

    def test_a_blocked_lane_is_refused_with_the_reason_and_the_escape(self):
        self._blocker("feat/alpha is not merged into integration")
        (status, detail), _o, err = self._run(self._reg("alpha"))
        self.assertEqual(status, "refused")
        self.assertEqual(detail, "feat/alpha is not merged into integration")
        self.assertIn("refusing alpha", err)
        self.assertIn("use --force to override", err)

    def test_a_dry_run_reports_would_remove_and_carries_no_detail(self):
        self.assertEqual(self._run(self._reg("alpha"), dry=True)[0],
                         ("would-remove", None))

    def test_a_removed_lane_carries_no_detail(self):
        self.assertEqual(self._run(self._reg("alpha"))[0], ("removed", None))

    # -- order ------------------------------------------------------------
    def test_the_blocker_is_consulted_before_anything_is_removed(self):
        self._blocker("not merged")
        self._run(self._reg("alpha"))
        self.assertEqual(self.order, ["blocker"])

    def test_a_dry_run_answers_after_the_blocker_and_before_the_removal(self):
        self._run(self._reg("alpha"), dry=True)
        self.assertEqual(self.order, ["blocker"])

    def test_a_dry_run_is_still_refused_by_the_blocker(self):
        # --dry-run is not an escape from the safety gate: it reports what a
        # real run WOULD do, and a real run would refuse.
        self._blocker("not merged")
        self.assertEqual(self._run(self._reg("alpha"), dry=True)[0][0], "refused")

    def test_worktrees_are_removed_before_their_branches(self):
        self._run(self._reg("alpha"))
        self.assertEqual(self.order,
                         ["blocker", "remove /pkg", "remove /kb",
                          "branch -D feat/alpha", "branch -D kb/alpha",
                          "save"])

    def test_the_registry_entry_is_dropped_only_after_everything_else(self):
        reg = self._reg("alpha")
        self._run(reg)
        self.assertEqual(reg["lanes"], {})
        self.assertEqual(self.order[-1], "save")

    # -- force ------------------------------------------------------------
    def test_force_skips_the_safety_gate_entirely(self):
        self.patch(mqt, "_teardown_blocker", _raiser("safety gate"))
        self.assertEqual(self._run(self._reg("alpha"), force=True)[0],
                         ("removed", None))

    def test_force_is_threaded_into_the_remover(self):
        seen = []
        self.patch(mqt, "_remove_worktree",
                   lambda repo, path, force: (seen.append(force), (True, None))[1])
        self._run(self._reg("alpha"), force=True)
        self.assertEqual(seen, [True, True])

    # -- partial failures -------------------------------------------------
    def test_a_failing_package_remove_stops_before_the_kb_and_the_branches(self):
        self._remover({"/pkg": "pkg busy"})
        (status, detail), _o, err = self._run(self._reg("alpha"))
        self.assertEqual((status, detail), ("error", "pkg busy"))
        self.assertEqual(self.order, ["blocker", "remove /pkg"])
        self.assertIn("package worktree remove failed: pkg busy", err)

    def test_a_failing_kb_remove_stops_before_the_branches(self):
        self._remover({"/kb": "kb busy"})
        (status, detail), _o, err = self._run(self._reg("alpha"))
        self.assertEqual((status, detail), ("error", "kb busy"))
        self.assertEqual(self.order, ["blocker", "remove /pkg", "remove /kb"])
        self.assertIn("KB worktree remove failed: kb busy", err)

    def test_a_failure_leaves_the_lane_registered_and_re_runnable(self):
        self._remover({"/pkg": "busy"})
        reg = self._reg("alpha")
        self._run(reg)
        self.assertIn("alpha", reg["lanes"])
        self.assertNotIn("save", self.order)

    def test_the_lane_env_file_is_unlinked(self):
        self._run(self._reg("alpha"))
        self.assertFalse(os.path.exists(self.env_file))

    def test_an_env_file_that_is_already_gone_is_not_an_error(self):
        # A REAL missing file, so the OSError is the one the product will
        # actually see rather than one this test invented.
        os.remove(self.env_file)
        self.assertEqual(self._run(self._reg("alpha"))[0], ("removed", None))
        self.assertIn("save", self.order)

    def test_a_record_with_no_env_file_key_is_tolerated(self):
        reg = self._reg("alpha")
        del reg["lanes"]["alpha"]["env_file"]
        self.assertEqual(self._run(reg)[0], ("removed", None))

    # -- no KB ------------------------------------------------------------
    def test_without_a_kb_work_tree_only_the_package_side_is_touched(self):
        self.patch(kbgit, "git_is_work_tree", lambda p: False)
        self._run(self._reg("alpha"))
        self.assertEqual(self.order, ["blocker", "remove /pkg",
                                      "branch -D feat/alpha", "save"])


# --- REPORT ------------------------------------------------------------------

class ReportTest(unittest.TestCase):
    """REPORT/TEXT-SCOPED."""

    ROWS = [{"feature": "a", "status": "removed"},
            {"feature": "b", "status": "refused", "detail": "not merged"},
            {"feature": "c", "status": "error", "detail": "unknown lane"},
            {"feature": "d", "status": "would-remove"}]

    def test_text_drops_the_rows_stderr_already_carried(self):
        _rv, out, _e = _capture(mqt._teardown_report, self.ROWS, False, False)
        self.assertEqual(out, "fanout teardown: a removed\n"
                              "fanout teardown: d would-remove\n")

    def test_json_keeps_every_row_including_the_failures(self):
        _rv, out, _e = _capture(mqt._teardown_report, self.ROWS, True, False)
        self.assertEqual(json.loads(out),
                         {"dry_run": False, "lanes": self.ROWS})

    def test_json_reports_the_dry_run_flag_it_was_given(self):
        _rv, out, _e = _capture(mqt._teardown_report, [], True, True)
        self.assertEqual(json.loads(out), {"dry_run": True, "lanes": []})

    def test_an_empty_result_set_prints_nothing_in_text(self):
        _rv, out, _e = _capture(mqt._teardown_report, [], False, False)
        self.assertEqual(out, "")


# --- THE COMMAND -------------------------------------------------------------

class TeardownCommandTest(SeamRestoringTest):
    """QUEUE/RC — the parent's own loop."""

    def setUp(self):
        super(TeardownCommandTest, self).setUp()
        self.seen = []
        self.patch(fanout, "load_lanes", lambda: (_reg("alpha", "beta"), None))

    def _lanes(self, table):
        def _one(feature, reg, args, opts):
            self.seen.append(feature)
            return table[feature]
        self.patch(mqt, "_teardown_one_lane", _one)

    def test_rc_is_zero_when_every_lane_succeeds(self):
        self._lanes({"alpha": ("removed", None), "beta": ("removed", None)})
        rc, _o, _e = _capture(mqt.cmd_fanout_teardown,
                              _Args(features=["alpha", "beta"]))
        self.assertEqual(rc, 0)

    def test_one_failing_lane_never_stops_the_ones_after_it(self):
        self._lanes({"alpha": ("error", "boom"), "beta": ("removed", None)})
        rc, _o, _e = _capture(mqt.cmd_fanout_teardown,
                              _Args(features=["alpha", "beta"]))
        self.assertEqual(rc, 1)
        self.assertEqual(self.seen, ["alpha", "beta"])

    def test_a_refusal_also_sets_the_exit_code(self):
        self._lanes({"alpha": ("refused", "not merged")})
        rc, _o, _e = _capture(mqt.cmd_fanout_teardown, _Args(features=["alpha"]))
        self.assertEqual(rc, 1)

    def test_a_would_remove_never_sets_the_exit_code(self):
        self._lanes({"alpha": ("would-remove", None)})
        rc, _o, _e = _capture(mqt.cmd_fanout_teardown, _Args(features=["alpha"]))
        self.assertEqual(rc, 0)

    def test_a_none_detail_is_omitted_from_the_result_row(self):
        self._lanes({"alpha": ("removed", None)})
        _rc, out, _e = _capture(mqt.cmd_fanout_teardown,
                                _Args(features=["alpha"], format="json"))
        self.assertEqual(json.loads(out)["lanes"],
                         [{"feature": "alpha", "status": "removed"}])

    def test_an_empty_detail_string_is_kept_rather_than_omitted(self):
        # `is not None`, not truthiness: a remover whose stderr was empty still
        # failed, and dropping the key would make the json row indistinguishable
        # from a successful one.
        self._lanes({"alpha": ("error", "")})
        _rc, out, _e = _capture(mqt.cmd_fanout_teardown,
                                _Args(features=["alpha"], format="json"))
        self.assertEqual(json.loads(out)["lanes"],
                         [{"feature": "alpha", "status": "error", "detail": ""}])

    def test_a_registry_error_refuses_before_any_lane_runs(self):
        self.patch(fanout, "load_lanes", lambda: (None, "invalid registry"))
        self.patch(mqt, "_teardown_one_lane", _raiser("lane teardown"))
        rc, _o, err = _capture(mqt.cmd_fanout_teardown, _Args(features=["alpha"]))
        self.assertEqual(rc, 1)
        self.assertEqual(err, "fanout teardown: invalid registry\n")

    def test_the_option_surface_takes_the_cli_defaults(self):
        seen = []

        def _one(feature, reg, args, opts):
            seen.append(dict(opts))
            return "removed", None
        self.patch(mqt, "_teardown_one_lane", _one)
        _capture(mqt.cmd_fanout_teardown, _Args(features=["alpha"]))
        self.assertEqual(seen, [{"fmt_json": False, "dry": False,
                                 "force": False, "integration": "integration"}])

    def test_an_explicit_integration_branch_reaches_the_lane(self):
        seen = []

        def _one(feature, reg, args, opts):
            seen.append(opts["integration"])
            return "removed", None
        self.patch(mqt, "_teardown_one_lane", _one)
        _capture(mqt.cmd_fanout_teardown,
                 _Args(features=["alpha"], integration_branch="release"))
        self.assertEqual(seen, ["release"])

    def test_lanes_are_processed_in_the_order_named_on_the_command_line(self):
        self._lanes({"alpha": ("removed", None), "beta": ("removed", None)})
        _capture(mqt.cmd_fanout_teardown, _Args(features=["beta", "alpha"]))
        self.assertEqual(self.seen, ["beta", "alpha"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
