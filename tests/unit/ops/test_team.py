"""Owning unit file for `agentware_lib.ops.team` — task 99 wave 17.

The module carried a `test-ownership` row. Its behaviour is reachable through the
`team` verb, and the pre-existing coverage drives that verb end to end
(`tests/e2e/test_org_team_mode_e2e.py`, `tests/integration/...`), which observes
a scaffolded shard and a registry row — so it reddens when the FEATURE breaks. It
cannot redden for the DISPATCH decisions, which is where every interesting choice
in this module lives and which no CLI invocation can even address: argparse
refuses an unknown subcommand before the handler runs, and a bare `agentware
team` dies in the dispatcher without a `func`, so the handler's own
unknown-subcommand tail is unreachable from the command line.

What is asserted here is the module's PROMISE, not its shape:

  * READING THE REGISTRY IS NOT ENTITLED TO AN IDENTITY. `list` renders without
    ever resolving a handle. The test proves it by making every handle source
    RAISE, which a test that merely leaves them unset cannot do.
  * THE ORG-ROOT RESOLUTION HAS AN ORDER, AND THE SECOND SOURCE IS REACHED. The
    explicit org key wins; the fleet walk from the caller's knowledge dir is the
    fallback, and each source is exercised with the other dry so the pair is
    SEPARATED rather than merely covered.
  * A HANDLE IS NORMALISED WHEREVER IT COMES FROM. `--handle`, the resolver and
    `$USER` all go through the same normaliser, so one person cannot produce two
    member rows. Each of the three sources is driven with the two above it dry.
  * `create` AND `register` DIFFER IN EXACTLY ONE DECISION. A team that does not
    exist yet is scaffolded by one and refused by the other; every other step is
    shared. Both directions are asserted, including `register` into an existing
    team NOT re-scaffolding.
  * A TOLERANT READ IS NOT A SILENT ONE. An unparseable registry renders the
    empty shape rather than raising — the read-only contract (INV-2) — and the
    scaffolder is never reached through it.

Hermetic: every org root is a fresh mkdtemp; no live KB, no config writes (the
config setter is captured, never called for real), no git.
"""

from tests._paths import TESTS_DIR

import io
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

import agentware_lib.core.config as config          # noqa: E402
import agentware_lib.ops.team as team               # noqa: E402


class Args(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class _TeamCase(unittest.TestCase):
    """Shared scratch org root + a stubbed identity/config environment."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-t99w17-team-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.org = os.path.join(self.root, "org")
        os.makedirs(self.org)
        self.config_writes = []
        self._patch(config, "resolve_org_root", lambda: self.org)
        self._patch(config, "require_knowledge_dir", lambda: "")
        self._patch(config, "_set_config_value",
                    lambda k, v: self.config_writes.append((k, v)))
        self._patch(team.routing, "resolve_user_handle", lambda: "alice")
        os.environ.pop("USER", None)

    def _patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def run_cmd(self, **kw):
        """Run cmd_team with sensible defaults; returns (rc, stdout, stderr)."""
        kw.setdefault("format", "text")
        kw.setdefault("name", None)
        kw.setdefault("handle", None)
        kw.setdefault("set_default", False)
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = team.cmd_team(Args(**kw))
        finally:
            sys.stdout, sys.stderr = old
        return rc, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------
# _team_org_root — two sources, one fixed order
# --------------------------------------------------------------------------

class TeamOrgRootTest(_TeamCase):

    def test_the_explicit_org_key_wins(self):
        self._patch(config, "resolve_org_root", lambda: "/explicit")
        self._patch(team._fleet, "_fleet_org_root", lambda k: "/walked")
        self.assertEqual(team._team_org_root(), "/explicit")

    def test_the_fleet_walk_is_the_fallback_and_is_reached(self):
        # The fallback is SEPARATED from the primary: the primary is dry and the
        # walk returns a different value, so a resolver that ignored the second
        # source would return None here and pass every test that only ever has
        # one source configured.
        self._patch(config, "resolve_org_root", lambda: None)
        self._patch(config, "require_knowledge_dir", lambda: "/kb")
        seen = []
        self._patch(team._fleet, "_fleet_org_root",
                    lambda k: seen.append(k) or "/walked")
        self.assertEqual(team._team_org_root(), "/walked")
        self.assertEqual(seen, ["/kb"])

    def test_an_unresolvable_knowledge_dir_is_walked_as_the_empty_string(self):
        # require_knowledge_dir returns None for an unconfigured workspace, and
        # the walker takes a path — the `or ""` is what keeps that a walk that
        # finds nothing instead of a TypeError on the very first `team` command.
        self._patch(config, "resolve_org_root", lambda: None)
        self._patch(config, "require_knowledge_dir", lambda: None)
        seen = []
        self._patch(team._fleet, "_fleet_org_root",
                    lambda k: seen.append(k) or None)
        self.assertIsNone(team._team_org_root())
        self.assertEqual(seen, [""])

    def test_no_org_root_refuses_before_anything_is_read(self):
        self._patch(config, "resolve_org_root", lambda: None)
        self._patch(team._fleet, "_fleet_org_root", lambda k: None)
        self._patch(team, "load_registry", _never("load_registry"))
        rc, out, err = self.run_cmd(team_cmd="list")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("no org root", err)


def _never(what):
    def _raise(*a, **kw):
        raise AssertionError("%s must not be reached" % what)
    return _raise


# --------------------------------------------------------------------------
# _team_caller_handle — three sources, one normaliser
# --------------------------------------------------------------------------

class TeamCallerHandleTest(_TeamCase):

    def test_the_explicit_flag_wins_over_both_other_sources(self):
        os.environ["USER"] = "env-user"
        self.addCleanup(os.environ.pop, "USER", None)
        self.assertEqual(team._team_caller_handle(Args(handle="flagged")),
                         "flagged")

    def test_the_resolver_is_used_when_the_flag_is_absent(self):
        os.environ["USER"] = "env-user"
        self.addCleanup(os.environ.pop, "USER", None)
        self.assertEqual(team._team_caller_handle(Args(handle=None)), "alice")

    def test_the_environment_is_the_last_resort(self):
        self._patch(team.routing, "resolve_user_handle", lambda: None)
        os.environ["USER"] = "env-user"
        self.addCleanup(os.environ.pop, "USER", None)
        self.assertEqual(team._team_caller_handle(Args(handle=None)), "env-user")

    def test_every_source_goes_through_the_same_normaliser(self):
        # The three sources must not be able to produce two member rows for one
        # person, so the normaliser is asserted on ALL THREE and not only on the
        # flag — which is the one a shortcut would apply it to.
        self._patch(team.routing, "resolve_user_handle", lambda: "Alice Smith")
        os.environ["USER"] = "Alice Smith"
        self.addCleanup(os.environ.pop, "USER", None)
        flagged = team._team_caller_handle(Args(handle="Alice Smith"))
        resolved = team._team_caller_handle(Args(handle=None))
        self._patch(team.routing, "resolve_user_handle", lambda: None)
        from_env = team._team_caller_handle(Args(handle=None))
        self.assertEqual(flagged, resolved)
        self.assertEqual(resolved, from_env)
        self.assertNotIn(" ", flagged)

    def test_no_source_at_all_is_none_and_not_the_empty_string(self):
        # None and "" are both falsy, but only None survives the normaliser call
        # being skipped — the guard downstream reports "no handle", and a "" that
        # reached the registry would write a member row with an empty user.
        self._patch(team.routing, "resolve_user_handle", lambda: None)
        self.assertIsNone(team._team_caller_handle(Args(handle=None)))


# --------------------------------------------------------------------------
# _team_list — the read arm
# --------------------------------------------------------------------------

class TeamListTest(_TeamCase):

    def _reg(self):
        return {"version": 1,
                "teams": [{"name": "platform", "path": "teams/platform"},
                          {"name": "research", "path": "teams/research"},
                          {"name": "empty", "path": "teams/empty"}],
                "members": [{"user": "carol", "team": "platform"},
                            {"user": "alice", "team": "platform"},
                            {"user": "bob", "team": "research"},
                            {"user": "dave", "team": "research"}]}

    def _render(self, as_json=False):
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            rc = team._team_list(self._reg(), as_json)
        finally:
            sys.stdout = old
        return rc, out.getvalue()

    def test_the_header_counts_teams_and_not_members(self):
        # The fixture carries THREE teams and FOUR members on purpose: with the
        # two counts equal, a header that counted the wrong collection would be
        # indistinguishable from one that counted the right one.
        rc, out = self._render()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self._reg()["teams"]), 3)
        self.assertEqual(len(self._reg()["members"]), 4)
        self.assertIn("teams (3):", out)

    def test_members_are_sorted_and_not_left_in_registry_order(self):
        # The fixture lists carol BEFORE alice on purpose: a renderer that
        # dropped the sort would still print both names, and only an
        # out-of-alphabetical fixture separates the two.
        _rc, out = self._render()
        self.assertIn("alice, carol", out)

    def test_a_team_with_no_members_says_so(self):
        _rc, out = self._render()
        self.assertIn("(no members)", out)

    def test_members_are_scoped_to_their_own_team(self):
        _rc, out = self._render()
        research = [ln for ln in out.split("\n") if "research" in ln][0]
        self.assertIn("bob", research)
        self.assertIn("dave", research)
        self.assertNotIn("alice", research)

    def test_the_json_mode_emits_the_registry_verbatim_and_sorted(self):
        rc, out = self._render(as_json=True)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), self._reg())
        self.assertTrue(out.endswith("\n"))
        # sort_keys is what makes two runs over one registry byte-comparable.
        self.assertLess(out.index('"members"'), out.index('"teams"'))

    def test_an_empty_registry_renders_zero_rather_than_raising(self):
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            rc = team._team_list({}, False)
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        self.assertIn("teams (0):", out.getvalue())

    def test_listing_never_resolves_a_handle(self):
        # Reading the registry is not entitled to an identity. Every handle
        # source RAISES here, which a test that merely leaves them unset cannot
        # distinguish from one that resolves and discards.
        self._patch(team.routing, "resolve_user_handle", _never("handle resolve"))
        self._patch(team.routing, "_normalize_user_handle", _never("normalise"))
        rc, _out, _err = self.run_cmd(team_cmd="list")
        self.assertEqual(rc, 0)


# --------------------------------------------------------------------------
# _team_join — the shared create/register arm
# --------------------------------------------------------------------------

class TeamJoinTest(_TeamCase):

    def test_create_scaffolds_a_team_that_does_not_exist(self):
        rc, out, err = self.run_cmd(team_cmd="create", name="platform")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("team create: alice -> platform", out)
        self.assertTrue(os.path.isfile(
            os.path.join(self.org, "teams", "platform", "MAIN.md")))

    def test_register_refuses_a_team_that_does_not_exist(self):
        self._patch(team, "_scaffold_team_dir", _never("_scaffold_team_dir"))
        rc, out, err = self.run_cmd(team_cmd="register", name="ghost")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("does not exist", err)

    def test_register_into_an_existing_team_never_re_scaffolds(self):
        self.run_cmd(team_cmd="create", name="platform")
        self._patch(team, "_scaffold_team_dir", _never("_scaffold_team_dir"))
        rc, out, _err = self.run_cmd(team_cmd="register", name="platform",
                                     handle="bob")
        self.assertEqual(rc, 0)
        self.assertIn("team register: bob -> platform", out)

    def test_create_on_an_existing_team_is_a_join_and_does_not_re_scaffold(self):
        # The one decision that differs between the two subcommands is
        # "team absent"; with the team PRESENT they must behave identically.
        self.run_cmd(team_cmd="create", name="platform")
        self._patch(team, "_scaffold_team_dir", _never("_scaffold_team_dir"))
        rc, _out, _err = self.run_cmd(team_cmd="create", name="platform",
                                      handle="bob")
        self.assertEqual(rc, 0)

    def test_a_missing_name_is_refused_before_the_registry_is_read(self):
        self._patch(team, "load_registry", _never("load_registry"))
        for sub in ("create", "register"):
            rc, out, err = self.run_cmd(team_cmd=sub)
            self.assertEqual((rc, out), (1, ""))
            self.assertIn("agentware team %s: --name required" % sub, err)

    def test_an_unresolvable_handle_is_refused_before_the_registry_is_read(self):
        self._patch(team.routing, "resolve_user_handle", lambda: None)
        self._patch(team, "load_registry", _never("load_registry"))
        rc, out, err = self.run_cmd(team_cmd="create", name="platform")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("no handle", err)

    def test_a_failed_registry_add_surfaces_its_note_and_exits_one(self):
        self._patch(team, "_registry_add_member",
                    lambda o, u, t: (False, "registry busy after 8 retries"))
        rc, out, err = self.run_cmd(team_cmd="create", name="platform")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("registry busy after 8 retries", err)

    def test_a_failed_registry_add_never_persists_the_config(self):
        self._patch(team, "_registry_add_member", lambda o, u, t: (False, "no"))
        self.run_cmd(team_cmd="create", name="platform", set_default=True)
        self.assertEqual(self.config_writes, [])

    def test_set_default_persists_both_the_team_and_the_root(self):
        # Both, not one: a persisted team with no root leaves the resolver
        # pointing at nothing, which is the state that looks configured and is not.
        self.run_cmd(team_cmd="create", name="platform", set_default=True)
        self.assertEqual(self.config_writes,
                         [(team.orgkeys.ORG_TEAM_KEY, "platform"),
                          (team.orgkeys.ORG_ROOT_KEY, self.org)])

    def test_no_set_default_persists_nothing(self):
        self.run_cmd(team_cmd="create", name="platform", set_default=False)
        self.assertEqual(self.config_writes, [])

    def test_set_default_defaults_to_true_when_the_attribute_is_absent(self):
        # The handler reads it with getattr(..., True), so a caller that never
        # set the flag persists — which is the behaviour `team create` documents.
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            team._team_join(Args(name="platform"), "create", self.org, "alice",
                            False)
        finally:
            sys.stdout = old
        self.assertEqual(len(self.config_writes), 2)

    def test_the_json_emit_carries_the_action_and_the_registry_note(self):
        rc, out, _err = self.run_cmd(team_cmd="create", name="platform",
                                     format="json")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["team"], "platform")
        self.assertEqual(payload["handle"], "alice")
        self.assertEqual(payload["action"], "create")
        self.assertTrue(payload["note"])

    def test_the_json_emit_reports_register_as_its_own_action(self):
        self.run_cmd(team_cmd="create", name="platform")
        rc, out, _err = self.run_cmd(team_cmd="register", name="platform",
                                     handle="bob", format="json")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["action"], "register")

    def test_the_handle_written_to_the_registry_is_the_normalised_one(self):
        self.run_cmd(team_cmd="create", name="platform", handle="Alice Smith")
        reg = json.loads(_read(os.path.join(self.org, "teams", "registry.json")))
        self.assertEqual(len(reg["members"]), 1)
        self.assertNotIn(" ", reg["members"][0]["user"])

    def test_a_second_create_is_idempotent_in_the_registry(self):
        self.run_cmd(team_cmd="create", name="platform")
        before = _read(os.path.join(self.org, "teams", "registry.json"))
        rc, out, _err = self.run_cmd(team_cmd="create", name="platform")
        self.assertEqual(rc, 0)
        self.assertEqual(
            before, _read(os.path.join(self.org, "teams", "registry.json")))


# --------------------------------------------------------------------------
# cmd_team — the dispatch, including the arm the CLI cannot reach
# --------------------------------------------------------------------------

class CmdTeamDispatchTest(_TeamCase):

    def test_no_subcommand_reports_the_vocabulary_and_exits_one(self):
        # UNREACHABLE from the CLI — argparse refuses an unknown choice, and a
        # bare `agentware team` dies in the dispatcher without a `func`. This is
        # the arm that only a function-boundary test can guard.
        rc, out, err = self.run_cmd(team_cmd=None)
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("unknown subcommand (list|create|register)", err)

    def test_an_unknown_subcommand_takes_the_same_arm(self):
        rc, _out, err = self.run_cmd(team_cmd="bogus")
        self.assertEqual(rc, 1)
        self.assertIn("unknown subcommand", err)

    def test_an_unknown_subcommand_never_scaffolds_or_mutates(self):
        self._patch(team, "_scaffold_team_dir", _never("_scaffold_team_dir"))
        self._patch(team, "_registry_add_member", _never("_registry_add_member"))
        self.run_cmd(team_cmd="bogus", name="platform")
        self.assertEqual(_manifest_names(self.org), [])

    def test_an_unparseable_registry_renders_the_empty_shape(self):
        _write(os.path.join(self.org, "teams", "registry.json"), "{not json\n")
        rc, out, err = self.run_cmd(team_cmd="list")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("teams (0):", out)

    def test_an_unparseable_registry_is_not_repaired_by_a_read(self):
        # INV-2: the read path is READ-ONLY. A tolerant parser that rewrote what
        # it could not parse would silently discard the operator's file.
        p = _write(os.path.join(self.org, "teams", "registry.json"), "{not json\n")
        self.run_cmd(team_cmd="list")
        self.assertEqual(_read(p), "{not json\n")


def _manifest_names(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            out.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return sorted(out)


# --------------------------------------------------------------------------
# the export surface
# --------------------------------------------------------------------------

class ExportSurfaceTest(unittest.TestCase):
    """The wave's new helpers are deliberately ABSENT from the facade surface.

    `_EXPORTS` here IS load-bearing — the resident org-lint consumer and the
    moved handler resolve these as bare names on the entrypoint — so the list is
    asserted exactly, and the four new helpers are asserted OUT of it: they have
    no monolith counterpart, and exporting them would widen the facade surface
    the parity machinery pins.
    """

    def test_exports_are_exactly_the_six_shipped_names(self):
        self.assertEqual(team._EXPORTS, [
            "registry_path", "load_registry", "registry_membership_errors",
            "_registry_add_member", "_scaffold_team_dir", "cmd_team"])

    def test_the_new_helpers_exist_but_are_not_exported(self):
        for name in ("_team_org_root", "_team_caller_handle", "_team_list",
                     "_team_join"):
            self.assertTrue(hasattr(team, name), name)
            self.assertNotIn(name, team._EXPORTS)


if __name__ == "__main__":
    unittest.main()
