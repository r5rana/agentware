"""`fanout`'s own unit file — task 99 wave 7, landed with the paydown.

`agentware_lib.features.fanout` is the lane-registry half of the fanout family:
the registry primitives every other fanout module reads through, lane
provisioning, the merge-policy installer, outcome reporting, the single gate
runner, and `fanout spin-up`. Until this file the module had no owning unit
file — `tests/integration/test_fanout.py` drives the whole family against two
real git repos, which cannot reach a preflight that fails on the KB side only,
a registry that is malformed on disk, or the text renderer's decision to drop a
row.

This wave split `cmd_fanout_spin_up` (13 branches -> 7) into
`_spin_up_preflight` and `_print_spin_up_lanes`. The file also pins the registry
primitives underneath them, because every fanout verb in the package resolves
its state through those four functions and a change in any of them is invisible
at the command boundary until a lane is lost.

  DIR/OVERRIDE      — AGENTWARE_FANOUT_DIR wins over the ~/.agentware default,
                      and a blank/whitespace value is NOT an override. That is
                      what keeps a test run off the operator's real state.
  REG/MISSING-OK    — a missing registry is the EMPTY registry, not an error:
                      the first `spin-up` on a fresh machine must not fail.
  REG/MALFORMED     — unparseable or wrong-shaped JSON IS an error, named with
                      its path, because silently treating it as empty would
                      re-provision lanes that already exist.
  REG/CANONICAL     — `save_lanes` writes sorted, 2-space, newline-terminated
                      JSON, so two machines that provision the same lanes
                      produce byte-identical registries (INV-1).
  PATHS/DERIVED     — every per-lane path and branch name is derived from the
                      feature slug alone, so a lane can be located without
                      reading the registry.
  PREFLIGHT/WHOLE   — the repo/base resolution and the work-tree guard are
                      whole-COMMAND checks: they run once, before the lane loop,
                      because every failure they can find is fatal for every
                      feature named rather than for one of them.
  PREFLIGHT/FALLBACK— `--kb-base` falls back to `--base`, which falls back to
                      HEAD, so a caller who pins one side pins both.
  PRINT/ERROR-SKIP  — the text renderer drops `error` rows (stderr already
                      carried them, and they hold no paths to print) while the
                      json form keeps them.
  SPINUP/PARTIAL    — an invalid or unprovisionable feature sets rc without
                      stopping the features after it.

Hermetic: AGENTWARE_FANOUT_DIR is redirected into a scratch tree for every test
that touches the registry, and no test in this file runs git.
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


class ScratchStateTest(unittest.TestCase):
    """Pins AGENTWARE_FANOUT_DIR to a tempdir and restores every module seam.

    The env pin is not tidiness: `_fanout_dir` falls back to
    `~/.agentware/fanout` on a real machine, so a test that writes a registry
    without it writes into the OPERATOR's lane state.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-fanout-unit-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = os.path.join(self.tmp, "state")
        self._prev = os.environ.get("AGENTWARE_FANOUT_DIR")
        os.environ["AGENTWARE_FANOUT_DIR"] = self.state
        self.addCleanup(self._restore_env)
        self._saved = [
            (fanout, "_fanout_provision_lane", fanout._fanout_provision_lane),
            (fanout, "_spin_up_preflight", fanout._spin_up_preflight),
            (fanout, "load_lanes", fanout.load_lanes),
            (fanout, "_resolve_repo_arg", fanout._resolve_repo_arg),
            (kbgit, "git_is_work_tree", kbgit.git_is_work_tree),
            (config, "resolve_knowledge_dir", config.resolve_knowledge_dir),
        ]
        for mod, name, val in self._saved:
            self.addCleanup(setattr, mod, name, val)

    def _restore_env(self):
        if self._prev is None:
            os.environ.pop("AGENTWARE_FANOUT_DIR", None)
        else:
            os.environ["AGENTWARE_FANOUT_DIR"] = self._prev

    def patch(self, mod, name, value):
        setattr(mod, name, value)

    def write_registry(self, text):
        os.makedirs(self.state, exist_ok=True)
        with open(fanout._fanout_lanes_path(), "w", encoding="utf-8") as f:
            f.write(text)


# --- the registry primitives -------------------------------------------------

class FanoutDirTest(ScratchStateTest):
    """DIR/OVERRIDE."""

    def test_the_env_override_wins_and_is_absolute(self):
        self.assertEqual(fanout._fanout_dir(), os.path.abspath(self.state))

    def test_a_blank_override_is_not_an_override(self):
        for blank in ("", "   ", "\t\n"):
            os.environ["AGENTWARE_FANOUT_DIR"] = blank
            self.assertEqual(
                fanout._fanout_dir(),
                os.path.join(os.path.dirname(config.HOME_CONFIG), "fanout"))

    def test_an_unset_override_falls_back_beside_the_home_config(self):
        os.environ.pop("AGENTWARE_FANOUT_DIR", None)
        self.assertEqual(
            fanout._fanout_dir(),
            os.path.join(os.path.dirname(config.HOME_CONFIG), "fanout"))

    def test_the_override_is_expanded_and_stripped(self):
        os.environ["AGENTWARE_FANOUT_DIR"] = "  %s  " % self.state
        self.assertEqual(fanout._fanout_dir(), os.path.abspath(self.state))

    def test_the_lanes_path_sits_inside_the_resolved_dir(self):
        self.assertEqual(fanout._fanout_lanes_path(),
                         os.path.join(os.path.abspath(self.state), "lanes.json"))


class LoadLanesTest(ScratchStateTest):
    """REG/MISSING-OK, REG/MALFORMED, REG/CANONICAL."""

    def test_a_missing_registry_is_the_empty_registry(self):
        self.assertEqual(fanout.load_lanes(), ({"lanes": {}}, None))

    def test_unparseable_json_is_an_error_naming_the_path(self):
        self.write_registry("{not json")
        data, err = fanout.load_lanes()
        self.assertIsNone(data)
        self.assertIn("invalid fanout registry", err)
        self.assertIn(fanout._fanout_lanes_path(), err)

    def test_a_top_level_non_object_is_a_malformed_registry(self):
        for text in ("[]", '"lanes"', "42", "null"):
            self.write_registry(text)
            data, err = fanout.load_lanes()
            self.assertIsNone(data, text)
            self.assertIn("malformed fanout registry", err)

    def test_a_lanes_key_that_is_not_an_object_is_malformed(self):
        self.write_registry('{"lanes": []}')
        data, err = fanout.load_lanes()
        self.assertIsNone(data)
        self.assertIn("malformed fanout registry", err)

    def test_a_missing_lanes_key_is_malformed_rather_than_empty(self):
        # Treating it as empty would re-provision every lane it forgot.
        self.write_registry('{"other": {}}')
        self.assertIsNone(fanout.load_lanes()[0])

    def test_a_valid_registry_round_trips(self):
        self.write_registry('{"lanes": {"alpha": {"feature": "alpha"}}}')
        data, err = fanout.load_lanes()
        self.assertIsNone(err)
        self.assertEqual(data["lanes"]["alpha"]["feature"], "alpha")

    def test_save_lanes_writes_sorted_two_space_json_with_a_newline(self):
        fanout.save_lanes({"lanes": {"b": {"z": 1, "a": 2}, "a": {}}})
        with open(fanout._fanout_lanes_path(), encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.endswith("}\n"))
        self.assertLess(text.index('"a"'), text.index('"b"'))
        self.assertIn('\n  "lanes"', text)

    def test_save_then_load_is_a_fixed_point(self):
        data = {"lanes": {"alpha": {"feature": "alpha", "merged": True}}}
        fanout.save_lanes(data)
        self.assertEqual(fanout.load_lanes(), (data, None))

    def test_non_ascii_survives_the_round_trip_unescaped(self):
        fanout.save_lanes({"lanes": {"ünï": {"feature": "ünï"}}})
        with open(fanout._fanout_lanes_path(), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("ünï", text)


class LanePathsTest(unittest.TestCase):
    """PATHS/DERIVED."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-fanout-paths-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._prev = os.environ.get("AGENTWARE_FANOUT_DIR")
        os.environ["AGENTWARE_FANOUT_DIR"] = self.tmp
        self.addCleanup(self._restore)

    def _restore(self):
        if self._prev is None:
            os.environ.pop("AGENTWARE_FANOUT_DIR", None)
        else:
            os.environ["AGENTWARE_FANOUT_DIR"] = self._prev

    def test_every_path_is_derived_from_the_slug_alone(self):
        lane = fanout._fanout_lane_paths("alpha", "/pkg", "/kb")
        self.assertEqual(lane["feat_branch"], "feat/alpha")
        self.assertEqual(lane["kb_branch"], "kb/alpha")
        self.assertEqual(lane["worktree"],
                         os.path.join(self.tmp, "worktrees", "alpha", "pkg"))
        self.assertEqual(lane["kb_path"],
                         os.path.join(self.tmp, "worktrees", "alpha", "kb"))
        self.assertEqual(lane["env_file"],
                         os.path.join(self.tmp, "env", "alpha.env"))

    def test_the_repos_are_carried_through_verbatim(self):
        lane = fanout._fanout_lane_paths("alpha", "/pkg", "/kb")
        self.assertEqual((lane["repo"], lane["kb_repo"]), ("/pkg", "/kb"))

    def test_two_lanes_never_share_a_worktree_root(self):
        a = fanout._fanout_lane_paths("alpha", "/pkg", "/kb")
        b = fanout._fanout_lane_paths("beta", "/pkg", "/kb")
        self.assertNotEqual(a["worktree"], b["worktree"])
        self.assertNotEqual(a["kb_path"], b["kb_path"])

    def test_the_feature_regex_admits_exactly_the_documented_alphabet(self):
        for ok in ("alpha", "260813-modular", "a_b-C9"):
            self.assertTrue(fanout.FANOUT_FEATURE_RE.match(ok), ok)
        for bad in ("bad name", "a/b", "../escape", "", "a.b", "a\nb"):
            self.assertFalse(fanout.FANOUT_FEATURE_RE.match(bad), repr(bad))


class ResolveRepoArgTest(unittest.TestCase):
    def test_an_explicit_value_is_absolutised(self):
        self.assertEqual(fanout._resolve_repo_arg("rel/path", "/default"),
                         os.path.abspath("rel/path"))

    def test_a_tilde_is_expanded(self):
        self.assertEqual(fanout._resolve_repo_arg("~/x", "/default"),
                         os.path.abspath(os.path.expanduser("~/x")))

    def test_an_empty_value_falls_back_to_the_default_unchanged(self):
        for empty in (None, ""):
            self.assertEqual(fanout._resolve_repo_arg(empty, "/default"),
                             "/default")


# --- PREFLIGHT ---------------------------------------------------------------

class SpinUpPreflightTest(ScratchStateTest):
    """PREFLIGHT/WHOLE, PREFLIGHT/FALLBACK."""

    def setUp(self):
        super(SpinUpPreflightTest, self).setUp()
        self.patch(config, "resolve_knowledge_dir", lambda: "/kbdefault")
        self.patch(kbgit, "git_is_work_tree", lambda p: True)

    def test_both_repos_default_and_both_bases_default_to_head(self):
        ctx, err = fanout._spin_up_preflight(_Args())
        self.assertIsNone(err)
        self.assertEqual(ctx["repo"], fanout.REPO_ROOT)
        self.assertEqual(ctx["kb_repo"], "/kbdefault")
        self.assertEqual(ctx["base"], "HEAD")
        self.assertEqual(ctx["kb_base"], "HEAD")

    def test_kb_base_falls_back_to_base_not_to_head(self):
        ctx, _err = fanout._spin_up_preflight(_Args(base="v1"))
        self.assertEqual((ctx["base"], ctx["kb_base"]), ("v1", "v1"))

    def test_an_explicit_kb_base_overrides_the_fallback(self):
        ctx, _err = fanout._spin_up_preflight(_Args(base="v1", kb_base="v2"))
        self.assertEqual((ctx["base"], ctx["kb_base"]), ("v1", "v2"))

    def test_explicit_repos_are_absolutised(self):
        ctx, _err = fanout._spin_up_preflight(
            _Args(repo="rel/pkg", kb_repo="rel/kb"))
        self.assertEqual(ctx["repo"], os.path.abspath("rel/pkg"))
        self.assertEqual(ctx["kb_repo"], os.path.abspath("rel/kb"))

    def test_no_resolvable_kb_repo_is_refused_before_the_work_tree_probe(self):
        self.patch(config, "resolve_knowledge_dir", lambda: "")
        self.patch(kbgit, "git_is_work_tree", _raiser("work tree probe"))
        ctx, err = fanout._spin_up_preflight(_Args())
        self.assertIsNone(ctx)
        self.assertEqual(
            err, "no KB repo (pass --kb-repo or set AGENTWARE_KNOWLEDGE_DIR).")

    def test_a_package_repo_that_is_not_a_work_tree_is_named(self):
        self.patch(kbgit, "git_is_work_tree", lambda p: p != "/pkg")
        ctx, err = fanout._spin_up_preflight(_Args(repo="/pkg"))
        self.assertIsNone(ctx)
        self.assertEqual(err, "package repo is not a git work tree: /pkg")

    def test_a_kb_repo_that_is_not_a_work_tree_is_named(self):
        self.patch(kbgit, "git_is_work_tree", lambda p: p != "/kb")
        ctx, err = fanout._spin_up_preflight(_Args(kb_repo="/kb"))
        self.assertIsNone(ctx)
        self.assertEqual(err, "KB repo is not a git work tree: /kb")

    def test_the_package_side_is_probed_before_the_kb_side(self):
        # Both sides fail; the message names the package, so the guard cannot
        # have been reordered to report whichever it happened to see first.
        self.patch(kbgit, "git_is_work_tree", lambda p: False)
        _ctx, err = fanout._spin_up_preflight(
            _Args(repo="/pkg", kb_repo="/kb"))
        self.assertTrue(err.startswith("package repo"), err)

    def test_the_error_carries_no_command_prefix(self):
        # The caller owns the "fanout spin-up: " prefix; a helper that added its
        # own would print it twice.
        self.patch(config, "resolve_knowledge_dir", lambda: "")
        _ctx, err = fanout._spin_up_preflight(_Args())
        self.assertNotIn("fanout spin-up", err)


# --- PRINT -------------------------------------------------------------------

class PrintLanesTest(unittest.TestCase):
    """PRINT/ERROR-SKIP."""

    ROWS = [
        {"feature": "alpha", "action": "created", "feat_branch": "feat/alpha",
         "kb_branch": "kb/alpha", "worktree": "/wt/a/pkg", "kb_path": "/wt/a/kb",
         "env_file": "/env/alpha.env"},
        {"feature": "bad", "action": "error", "error": "boom"},
    ]

    def test_error_rows_are_skipped_entirely(self):
        _rv, out, _e = _capture(fanout._print_spin_up_lanes, self.ROWS, False)
        self.assertNotIn("bad", out)
        self.assertNotIn("boom", out)

    def test_a_created_row_prints_four_lines_naming_every_path(self):
        _rv, out, _e = _capture(fanout._print_spin_up_lanes, self.ROWS[:1],
                                False)
        self.assertEqual(out.splitlines(), [
            "fanout spin-up: alpha created",
            "  feat worktree: /wt/a/pkg (feat/alpha)",
            "  kb   worktree: /wt/a/kb (kb/alpha)",
            "  env file:      /env/alpha.env",
        ])

    def test_a_dry_run_replaces_the_action_with_the_would_create_tag(self):
        _rv, out, _e = _capture(fanout._print_spin_up_lanes, self.ROWS[:1], True)
        self.assertIn("alpha [dry-run] would create", out)
        self.assertNotIn("alpha created", out)

    def test_the_dry_tag_replaces_every_action_not_just_would_create(self):
        rows = [dict(self.ROWS[0], action="exists")]
        _rv, out, _e = _capture(fanout._print_spin_up_lanes, rows, True)
        self.assertIn("[dry-run] would create", out)
        self.assertNotIn("exists", out)

    def test_an_empty_result_set_prints_nothing(self):
        _rv, out, _e = _capture(fanout._print_spin_up_lanes, [], False)
        self.assertEqual(out, "")


# --- THE COMMAND -------------------------------------------------------------

class SpinUpCommandTest(ScratchStateTest):
    """SPINUP/PARTIAL."""

    def setUp(self):
        super(SpinUpCommandTest, self).setUp()
        self.seen = []
        self.patch(fanout, "_spin_up_preflight",
                   lambda args: ({"repo": "/pkg", "kb_repo": "/kb",
                                  "base": "HEAD", "kb_base": "HEAD"}, None))
        self._provision({})

    def _provision(self, failures):
        def _fn(data, lane, base, kb_base, dry):
            self.seen.append((lane["feature"], base, kb_base, dry))
            if lane["feature"] in failures:
                return None, failures[lane["feature"]]
            return ("would-create" if dry else "created"), None
        self.patch(fanout, "_fanout_provision_lane", _fn)

    def test_a_preflight_failure_refuses_before_the_registry_is_read(self):
        self.patch(fanout, "_spin_up_preflight",
                   lambda args: (None, "no KB repo"))
        self.patch(fanout, "load_lanes", _raiser("registry load"))
        rc, _o, err = _capture(fanout.cmd_fanout_spin_up,
                               _Args(features=["alpha"]))
        self.assertEqual(rc, 1)
        self.assertEqual(err, "fanout spin-up: no KB repo\n")

    def test_a_registry_error_refuses_before_any_lane_is_provisioned(self):
        self.write_registry("{not json")
        self.patch(fanout, "_fanout_provision_lane", _raiser("provision"))
        rc, _o, err = _capture(fanout.cmd_fanout_spin_up,
                               _Args(features=["alpha"]))
        self.assertEqual(rc, 1)
        self.assertIn("invalid fanout registry", err)

    def test_every_valid_feature_is_provisioned_in_order(self):
        rc, out, _e = _capture(fanout.cmd_fanout_spin_up,
                               _Args(features=["beta", "alpha"]))
        self.assertEqual(rc, 0)
        self.assertEqual([f for f, _b, _k, _d in self.seen], ["beta", "alpha"])
        self.assertLess(out.index("beta"), out.index("alpha"))

    def test_an_invalid_name_is_refused_without_stopping_the_others(self):
        rc, out, err = _capture(fanout.cmd_fanout_spin_up,
                                _Args(features=["bad name", "alpha"]))
        self.assertEqual(rc, 1)
        self.assertIn("invalid feature name 'bad name'", err)
        self.assertEqual([f for f, _b, _k, _d in self.seen], ["alpha"])
        self.assertIn("alpha created", out)

    def test_a_provision_failure_is_recorded_without_stopping_the_others(self):
        self._provision({"alpha": "branch already exists"})
        rc, out, err = _capture(fanout.cmd_fanout_spin_up,
                                _Args(features=["alpha", "beta"]))
        self.assertEqual(rc, 1)
        self.assertIn("fanout spin-up: alpha: branch already exists", err)
        self.assertNotIn("alpha", out)
        self.assertIn("beta created", out)

    def test_json_keeps_the_error_rows_text_dropped(self):
        self._provision({"alpha": "boom"})
        _rc, out, _e = _capture(fanout.cmd_fanout_spin_up,
                                _Args(features=["alpha", "beta"], format="json"))
        data = json.loads(out)
        self.assertEqual(data["dry_run"], False)
        self.assertEqual([r["feature"] for r in data["lanes"]],
                         ["alpha", "beta"])
        self.assertEqual(data["lanes"][0],
                         {"feature": "alpha", "action": "error", "error": "boom"})

    def test_the_json_row_carries_every_lane_path(self):
        _rc, out, _e = _capture(fanout.cmd_fanout_spin_up,
                                _Args(features=["alpha"], format="json"))
        row = json.loads(out)["lanes"][0]
        self.assertEqual(sorted(row), ["action", "env_file", "feat_branch",
                                       "feature", "kb_branch", "kb_path",
                                       "worktree"])

    def test_the_dry_flag_reaches_both_the_provisioner_and_the_renderer(self):
        _rc, out, _e = _capture(fanout.cmd_fanout_spin_up,
                                _Args(features=["alpha"], dry_run=True))
        self.assertEqual([d for _f, _b, _k, d in self.seen], [True])
        self.assertIn("[dry-run] would create", out)

    def test_the_preflight_bases_reach_the_provisioner(self):
        self.patch(fanout, "_spin_up_preflight",
                   lambda args: ({"repo": "/pkg", "kb_repo": "/kb",
                                  "base": "v1", "kb_base": "v2"}, None))
        _capture(fanout.cmd_fanout_spin_up, _Args(features=["alpha"]))
        self.assertEqual(self.seen, [("alpha", "v1", "v2", False)])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
