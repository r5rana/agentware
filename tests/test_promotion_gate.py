"""The SYMMETRIC PROMOTION GATE suite (plan task 32, gate 2 of 3).

Four claims, deliberately separated:

  1. GREEN — the gate passes over the live tree, and is NOT VACUOUS while doing
     it: all thirty-eight local verbs are checked on one arm or the other —
     unported against what `aw-pre-merge` served, ported against the union — so
     "0 violations" is a measurement rather than an empty loop.
  2. IT COMPARES VALUES, NOT NAMES — the plan names three probes and each gets
     its own test: delete a `team` flag, narrow a `choices` set, move a
     `default` off its decided value. All three keep the flag SPELLED the same,
     which is the whole point: a name-only diff passes every one of them.
  3. BOTH ARMS ARE LIVE — the ported arm (lib vs union) and the unported arm
     (effective CLI vs local fixture) each redden on their own. A gate with a
     decorative branch is a gate that proves nothing about the branch that
     matters. Since task 37 the ported arm is genuinely reachable (`migrate`),
     and the probes still SUBSTITUTE an allowlist so each can pick the verb that
     exercises it best — but they substitute `_live(...)`, the live allowlist
     PLUS that verb, never a bare literal. See `_live` for why a hardcoded
     `set()` silently stopped modelling this tree at the first port.
  4. THE DONOR-ONLY HOLE IS ACKNOWLEDGED — the six donor-only verbs are
     deliberately NOT checked here, because their local expectation is empty and
     superset-of-empty is satisfied by absence. This suite pins that fact so
     nobody "fixes" it by widening the unported arm; `test_presence_gate.py`
     owns them.

Mutations are made to captured node lists IN MEMORY. The seam
`promotion_violations(verb, candidate_nodes, expected_nodes)` takes both sides as
data precisely so a probe can be a one-field edit rather than a scratch checkout.

Stdlib only, deterministic.
"""

import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests import _promotion_gate as pg
    from tests import _surface_union as su
except ImportError:  # allow `python3 -m unittest tests.test_promotion_gate`
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _promotion_gate as pg  # type: ignore
    import _surface_union as su  # type: ignore

UNION = su.load_json(pg.UNION_FIXTURE)
LOCAL = su.load_json(pg.LOCAL_FIXTURE)


def _live(*also_ported):
    """The LIVE allowlist, optionally plus a verb this probe wants to simulate.

    Every ``evaluate`` call here used to pass a hardcoded ``set()``, which was a
    faithful model of the tree exactly while the allowlist was empty (task 29).
    It stopped being one at the FIRST port: promoting a verb DELETES its handler
    from the monolith, so an empty allowlist now sends a verb the legacy parser
    no longer carries down the unported arm, and every whole-row assertion picks
    up a violation that describes nothing but the counterfactual. Anchoring to
    the live allowlist instead keeps each probe measuring its own mutation, and
    keeps these tests from needing an edit — and therefore a weakening — at each
    of tasks 38..54. The same trap, and the same fix, as the loop-outcome
    divergence fixture (task 34) and the debt ratchet (task 35).
    """
    return pg.ported_verbs() | set(also_ported)


def _lib():
    return copy.deepcopy(pg.capture_lib())


def _cli():
    return copy.deepcopy(pg.capture_effective())


def _node(nodes, path):
    for node in nodes:
        if list(node["path"]) == list(path):
            return node
    raise AssertionError(f"no node {path} in capture")


def _action(node, dest):
    for act in node["actions"]:
        if act["dest"] == dest:
            return act
    raise AssertionError(f"no action {dest!r} in {node['path']}")


class GreenTests(unittest.TestCase):

    def test_promotiongate_passes_over_the_live_tree(self):
        self.assertEqual(0, pg.check())

    def test_gate_is_NOT_vacuous_every_local_verb_is_really_compared(self):
        """Every local verb is really compared, not skipped.

        Stated as ARITHMETIC over the live allowlist rather than as the day's
        literal 38, so it keeps measuring the same thing as tasks 38..54 move
        verbs from the unported arm to the ported one. What must never happen is
        a verb falling off BOTH arms — counted here, not assumed.
        """
        live = _live()
        rows = pg.evaluate(UNION, LOCAL, _lib(), _cli(), live)
        local_verbs = set(UNION["top_level_verbs"]) - set(UNION["donor_only_verbs"])
        unported = [v for v, arm, _ in rows if arm == pg.UNPORTED]
        ported = [v for v, arm, _ in rows if arm == pg.PORTED]
        self.assertEqual(len(local_verbs) - len(live & local_verbs),
                         len(unported))
        self.assertEqual(sorted(live & local_verbs), sorted(ported))
        self.assertEqual(38, len(unported) + len(ported),
                         "a local verb fell off both arms")
        self.assertEqual([], [v for v, _a, viol in rows if viol])

    def test_every_union_verb_is_assigned_an_arm(self):
        rows = pg.evaluate(UNION, LOCAL, _lib(), _cli(), _live())
        self.assertEqual(sorted(UNION["top_level_verbs"]),
                         sorted(v for v, _a, _x in rows))
        self.assertEqual(44, len(rows))

    def test_the_six_donor_only_verbs_get_their_own_arm(self):
        rows = pg.evaluate(UNION, LOCAL, _lib(), _cli(), _live())
        donor = sorted(v for v, arm, _ in rows if arm == pg.DONOR_ONLY)
        self.assertEqual(sorted(UNION["donor_only_verbs"]), donor)

    def test_the_donor_only_arm_is_NOT_vacuous(self):
        """It compares against the union, exactly as the ported arm does.

        The first draft returned `[]` here and left the six entirely to the
        presence gate — which made this task's own acceptance criterion
        ("deleting a team flag reddens the promotion gate naming it") FALSE of
        the shipped binary, since `team` is donor-only. A division of labour
        that leaves an arm returning a constant is not a division of labour.
        """
        lib = _lib()
        node = _node(lib, ["team", "create"])
        node["actions"] = [a for a in node["actions"] if a["dest"] != "handle"]
        rows = pg.evaluate(UNION, LOCAL, lib, _cli(), _live())
        offenders = [(v, x) for v, _a, x in rows if x]
        self.assertEqual(["team"], [v for v, _x in offenders])
        self.assertTrue(any("--handle" in v for v in offenders[0][1]),
                        offenders)

    def test_what_this_gate_can_and_cannot_say_about_a_donor_only_verb(self):
        """The exact boundary between gate 2 and gate 3, asserted not assumed.

        Deleting `team` outright DOES redden here, via the node-presence check
        — so gate 3 is not needed for that. What gate 3 adds and this
        comparison structurally cannot express is a verb that is PRESENT and
        covers nothing (the stub), and a verb that is present, complete, and
        still unreachable because nothing routes to it.
        """
        lib = [n for n in _lib() if n["path"][:1] != ["team"]]
        rows = pg.evaluate(UNION, LOCAL, lib, _cli(), _live())
        self.assertEqual(["team"], [v for v, _a, viol in rows if viol])
        # ...but a STUB satisfies every comparison this gate makes, because a
        # union node with no required action and no subcommand requires nothing.
        stub = [n for n in _lib() if list(n["path"])[:1] != ["fleet-emit"]]
        stub.append({"path": ["fleet-emit"], "depth": 1, "sides": "donor",
                     "subcommand_dest": None, "subcommands": [], "actions": []})
        empty_union = {"nodes": [{"path": ["fleet-emit"], "depth": 1,
                                  "subcommand_dest": None, "subcommands": [],
                                  "actions": []}]}
        self.assertEqual([], pg.promotion_violations(
            "fleet-emit", stub, empty_union["nodes"]))


class ValuesNotNamesTests(unittest.TestCase):
    """The plan's three named probes, each keeping the flag name intact."""

    def test_DELETING_A_TEAM_FLAG_reddens_naming_it(self):
        lib = _lib()
        node = _node(lib, ["team", "create"])
        node["actions"] = [a for a in node["actions"] if a["dest"] != "handle"]
        violations = pg.promotion_violations("team", lib, UNION["nodes"])
        self.assertTrue(violations)
        self.assertTrue(any("handle" in v and "--handle" in v
                            for v in violations), violations)

    def test_deleting_a_team_SUBCOMMAND_reddens_naming_it(self):
        lib = [n for n in _lib() if list(n["path"]) != ["team", "register"]]
        _node(lib, ["team"])["subcommands"] = ["create", "list"]
        violations = pg.promotion_violations("team", lib, UNION["nodes"])
        self.assertTrue(any("register" in v for v in violations), violations)

    def test_NARROWING_A_CHOICES_SET_reddens(self):
        lib = _lib()
        act = _action(_node(lib, ["org", "lint"]), "format")
        self.assertEqual(["text", "json"], act["choices"])
        act["choices"] = ["text"]
        violations = pg.promotion_violations("org", lib, UNION["nodes"])
        self.assertTrue(any("choices" in v and "does not cover" in v
                            for v in violations), violations)

    def test_widening_a_choices_set_is_ALLOWED(self):
        """A merged tree may accept more; choices is a superset comparison."""
        lib = _lib()
        _action(_node(lib, ["org", "lint"]), "format")["choices"] = [
            "text", "json", "yaml"]
        self.assertEqual([], pg.promotion_violations("org", lib,
                                                     UNION["nodes"]))

    def test_CHANGING_A_DEFAULT_off_the_decided_value_reddens(self):
        lib = _lib()
        act = _action(_node(lib, ["team", "create"]), "set_default")
        self.assertIs(True, act["default"])
        act["default"] = False
        violations = pg.promotion_violations("team", lib, UNION["nodes"])
        self.assertTrue(any("default" in v for v in violations), violations)

    def test_dropping_REQUIREDNESS_reddens(self):
        lib = _lib()
        act = _action(_node(lib, ["team", "create"]), "name")
        self.assertTrue(act["required"])
        act["required"] = False
        violations = pg.promotion_violations("team", lib, UNION["nodes"])
        self.assertTrue(any("required" in v for v in violations), violations)

    def test_changing_the_ACTION_KIND_reddens(self):
        lib = _lib()
        act = _action(_node(lib, ["team", "create"]), "set_default")
        act["kind"] = "_StoreTrueAction"
        violations = pg.promotion_violations("team", lib, UNION["nodes"])
        self.assertTrue(any("kind" in v for v in violations), violations)

    def test_changing_NARGS_reddens(self):
        lib = _lib()
        act = _action(_node(lib, ["team", "create"]), "name")
        act["nargs"] = "+"
        violations = pg.promotion_violations("team", lib, UNION["nodes"])
        self.assertTrue(any("nargs" in v for v in violations), violations)

    def test_changing_TYPE_reddens(self):
        """`audit --max-age-days` is typed `int` in the union; drop the coercion
        and the flag still parses — as a STRING. Nothing else in the tree sees
        that."""
        cli = _cli()
        act = _action(_node(cli, ["audit"]), "max_age_days")
        self.assertEqual("int", act["type"])
        act["type"] = None
        violations = pg.promotion_violations("audit", cli, LOCAL["nodes"])
        self.assertTrue(any("type" in v for v in violations), violations)

    def test_a_NAME_ONLY_diff_would_have_passed_all_of_these(self):
        """The control: every probe above leaves the option string untouched."""
        lib = _lib()
        act = _action(_node(lib, ["team", "create"]), "name")
        before = list(act["option_strings"])
        act["required"] = False
        act["default"] = "sentinel"
        self.assertEqual(before, act["option_strings"])
        self.assertTrue(pg.promotion_violations("team", lib, UNION["nodes"]))


class BothArmsLiveTests(unittest.TestCase):
    """The PORTED arm, exercised with a verb the MONOLITH also serves.

    The exemplar is `trust` (local-only, ported at task 40), NOT `team`. `team`
    is DONOR-ONLY: the monolith has no handler for it, so it belongs to the
    donor-only arm whether or not it is allowlisted — and using it here would
    leave the PORTED arm untested while the assertions still passed, since the
    two arms share a comparator.
    """

    def test_the_PORTED_arm_is_reachable_and_green_for_a_ready_verb(self):
        rows = pg.evaluate(UNION, LOCAL, _lib(), _cli(), _live("trust"))
        arms = {v: arm for v, arm, _ in rows}
        self.assertEqual(pg.PORTED, arms["trust"])
        self.assertEqual([], dict((v, x) for v, _a, x in rows)["trust"])

    def test_the_PORTED_arm_reddens_on_a_lib_regression(self):
        lib = _lib()
        node = _node(lib, ["trust", "pin"])
        node["actions"] = [a for a in node["actions"] if a["dest"] != "principal"]
        rows = pg.evaluate(UNION, LOCAL, lib, _cli(), _live("trust"))
        offenders = [(v, x) for v, _a, x in rows if x]
        self.assertEqual(1, len(offenders))
        self.assertEqual("trust", offenders[0][0])

    def test_the_ported_arm_uses_the_UNION_not_the_local_fixture(self):
        """The union expectation is STRICTER, proven without leaning on a bug.

        `recall --token-budget` is the natural illustration — the lib still
        carries the donor's 10000 against the union's decided 1500 — but
        asserting that as-is would make this test pass only until task 54/55
        fixes it, and then INVERT into a failure for the right change. So the
        divergence is INTRODUCED here: the same mutation must redden the ported
        arm (union expectation) and stay invisible to the unported arm (local
        expectation, where the legacy parser is untouched).
        """
        lib = _lib()
        _action(_node(lib, ["recall"]), "token_budget")["default"] = 10000
        ported = dict((v, x) for v, _a, x in
                      pg.evaluate(UNION, LOCAL, lib, _cli(), _live("recall")))
        unported = dict((v, x) for v, _a, x in
                        pg.evaluate(UNION, LOCAL, lib, _cli(), _live()))
        self.assertTrue(any("token_budget" in v and "1500" in v
                            for v in ported["recall"]), ported["recall"])
        self.assertEqual([], unported["recall"])

    def test_the_UNPORTED_arm_reddens_when_the_effective_parser_regresses(self):
        cli = _cli()
        node = _node(cli, ["recall"])
        keep = [a for a in node["actions"] if a["dest"] != "token_budget"]
        node["actions"] = keep
        rows = pg.evaluate(UNION, LOCAL, _lib(), cli, _live())
        bad = [(v, x) for v, _a, x in rows if x]
        self.assertEqual(1, len(bad))
        self.assertEqual("recall", bad[0][0])
        self.assertTrue(any("token_budget" in v for v in bad[0][1]), bad)

    @unittest.skipUnless(os.path.isfile(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts", "agentware_legacy.py")), "monolith retired (task 56)")
    def test_the_unported_arm_measures_the_LIVE_parser_object(self):
        """The facade execs legacy and then REBINDS names into that namespace
        (task 26), so the file on disk can be byte-identical while the effective
        parser is not. The arm therefore builds the parser and walks the object.

        Asserted PER VERB over the verbs still on the unported arm, not as a
        whole-tree node/action count: tasks 37-54 each delete legacy code, so a
        total pinned to `surface-local.json` would fail on every port for a
        reason unrelated to the invariant it names.
        """
        cli, ported = _cli(), pg.ported_verbs()
        checked = 0
        for verb in sorted(pg.local_verbs_of(LOCAL) - ported):
            with self.subTest(verb=verb):
                self.assertEqual([], pg.promotion_violations(
                    verb, cli, LOCAL["nodes"]))
            checked += 1
        self.assertGreater(checked, 0, "no unported verb left to measure")


class NodePresenceTests(unittest.TestCase):
    """Node presence is asserted DIRECTLY, not inferred from the parent.

    `superset_violations` reports a missing node only when that node has a
    required action or a subcommand of its own. Two union nodes have neither —
    `bench scorecard` and `mcp serve` — so for those the ONLY thing that catches
    a deletion is the parent's `subcommands` list. That is one indirection more
    than the claim deserves: it makes "the node is there" a fact derived from
    the parent's bookkeeping rather than a fact checked, and it is exactly the
    shape a partially-migrated or hand-built capture gets wrong. So the gate
    asserts it outright, and this suite proves the two paths are independent.
    """

    EMPTY = (["bench", "scorecard"], ["mcp", "serve"])

    def test_the_shape_still_exists_in_the_union(self):
        empty = [n["path"] for n in UNION["nodes"]
                 if not n["subcommands"]
                 and not [a for a in n["actions"]
                          if a["disposition"] == "required"]]
        self.assertTrue(empty, "the hole this test guards has moved")
        for path in self.EMPTY:
            self.assertIn(path, empty)

    def test_the_shared_comparator_alone_MISSES_a_bare_node_deletion(self):
        """The control: node gone, parent's subcommand entry still there."""
        for path in self.EMPTY:
            with self.subTest(path=path):
                cli = [n for n in _cli() if list(n["path"]) != path]
                self.assertEqual([], su.superset_violations(
                    {"nodes": pg.subtree(cli, path[0])},
                    {"nodes": pg.as_expectation(
                        pg.subtree(LOCAL["nodes"], path[0]))}))

    def test_the_promotion_gate_CATCHES_the_same_deletion(self):
        for path in self.EMPTY:
            with self.subTest(path=path):
                cli = [n for n in _cli() if list(n["path"]) != path]
                violations = pg.promotion_violations(path[0], cli,
                                                     LOCAL["nodes"])
                self.assertTrue(any("/".join(path) in v for v in violations),
                                violations)

    def test_a_deleted_NON_empty_node_reddens_by_BOTH_paths(self):
        """A normal subverb: the parent's list and the presence check agree."""
        cli = [n for n in _cli() if list(n["path"]) != ["plan", "lint"]]
        _node(cli, ["plan"])["subcommands"] = [
            s for s in _node(cli, ["plan"])["subcommands"] if s != "lint"]
        violations = pg.promotion_violations("plan", cli, LOCAL["nodes"])
        self.assertTrue(any("missing node plan/lint" in v
                            for v in violations), violations)
        self.assertTrue(any("missing subcommand lint" in v
                            for v in violations), violations)


class SubcommandDestTests(unittest.TestCase):
    """The union carries `subcommand_dest`; nothing used to compare it.

    A modular `plan` that names its subparsers `plan_cmd` instead of
    `plan_command` parses byte-identically — same subverbs, same flags, same
    defaults — and then every `args.plan_command` read raises AttributeError at
    dispatch. Exactly the "changes no flag name" class the gate is for.
    """

    def test_a_RENAMED_subparsers_dest_reddens_the_unported_arm(self):
        cli = _cli()
        _node(cli, ["plan"])["subcommand_dest"] = "WRONG_DEST"
        violations = pg.promotion_violations("plan", cli, LOCAL["nodes"])
        self.assertTrue(any("subcommand dest" in v for v in violations),
                        violations)

    def test_a_RENAMED_subparsers_dest_reddens_the_donor_only_arm(self):
        lib = _lib()
        _node(lib, ["team"])["subcommand_dest"] = "WRONG_DEST"
        rows = pg.evaluate(UNION, LOCAL, lib, _cli(), _live())
        offenders = [(v, x) for v, _a, x in rows if x]
        self.assertEqual(["team"], [v for v, _x in offenders])
        self.assertTrue(any("team_cmd" in v for v in offenders[0][1]),
                        offenders)

    def test_a_LEAF_node_carrying_no_dest_is_not_compared(self):
        lib = _lib()
        _node(lib, ["fleet-emit"])["subcommand_dest"] = "anything"
        self.assertEqual([], pg.promotion_violations("fleet-emit", lib,
                                                     UNION["nodes"]))


class FixtureSelfDefenceTests(unittest.TestCase):
    """The gate's inputs are files, and files get edited."""

    def test_an_OUT_OF_VOCABULARY_disposition_is_refused(self):
        """`superset_violations` requires only rows marked 'required', so an
        unknown value silently requires NOTHING and the gate goes vacuous."""
        union = copy.deepcopy(UNION)
        for node in union["nodes"]:
            for act in node["actions"]:
                act["disposition"] = "optional"
        problems = pg.fixture_errors(union, set())
        self.assertTrue(any("disposition" in p for p in problems), problems)

    def test_the_committed_union_passes_its_own_validation(self):
        self.assertEqual([], pg.fixture_errors(UNION, pg.ported_verbs()))

    def test_an_allowlist_member_outside_the_union_is_refused(self):
        """It is never evaluated by this gate and never matched by the router.

        The realistic shape is a typo in a task-37..54 append (`'fleet_emit'`
        for `fleet-emit`), which leaves the real verb on the legacy arm while
        every gate reports it promoted.
        """
        problems = pg.fixture_errors(UNION, {"fleet_emit", "reccall"})
        self.assertEqual(2, len(problems), problems)
        self.assertTrue(any("fleet_emit" in p for p in problems), problems)

    def test_a_tampered_fixture_makes_check_FAIL_not_pass_vacuously(self):
        union = copy.deepcopy(UNION)
        union["nodes"][1]["actions"] = [
            dict(a, disposition="optional") for a in union["nodes"][1]["actions"]]
        path = os.path.join(tempfile.mkdtemp(prefix="aw-union-"), "union.json")
        self.addCleanup(shutil.rmtree, os.path.dirname(path),
                        ignore_errors=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(union, fh)
        self.assertEqual(1, pg.check(union_path=path))


class Task56SurvivalTests(unittest.TestCase):
    """The gate must outlive the passthrough it was built alongside."""

    def test_the_entrypoint_parser_is_captured_only_when_an_arm_needs_it(self):
        """Simulate task 56: every verb ported, so no unported arm remains.

        The entrypoint parser must then not be built at all — otherwise this
        gate's survival depends on the legacy passthrough the plan deletes.
        """
        calls = []
        original_capture, original_ported = pg.capture_effective, pg.ported_verbs
        pg.capture_effective = lambda: calls.append(1) or []
        pg.ported_verbs = lambda: set(UNION["top_level_verbs"])
        try:
            pg.check()
        finally:
            pg.capture_effective = original_capture
            pg.ported_verbs = original_ported
        self.assertEqual([], calls,
                         "no unported LOCAL verb, so nothing to capture")

    def test_the_entrypoint_parser_IS_captured_while_verbs_are_unported(self):
        calls = []
        original = pg.capture_effective
        pg.capture_effective = lambda: calls.append(1) or original()
        try:
            self.assertEqual(0, pg.check())
        finally:
            pg.capture_effective = original
        self.assertEqual([1], calls)


class ReadinessTests(unittest.TestCase):

    def test_readiness_is_green_for_a_verb_whose_lib_surface_is_complete(self):
        self.assertEqual(0, pg.readiness("team"))

    def test_readiness_rejects_a_verb_that_is_not_in_the_union(self):
        self.assertEqual(1, pg.readiness("no-such-verb"))

    def test_readiness_asks_the_UNION_question_not_merely_existence(self):
        """A verb that EXISTS in the lib but is short of the union is NOT ready.

        `recall` is the live proof and the reason this arm was worth building:
        it is registered in `agentware_lib` today, so an existence check calls
        it ready, while its `--token-budget` default still carries the donor's
        10000 against the union's decided 1500. Asserted as "either ready, or
        red for a reason that names a compared field" so the test survives the
        port that fixes it (task 54/55) instead of inverting into a failure.
        """
        lib = _lib()
        violations = pg.promotion_violations("recall", lib, UNION["nodes"])
        if violations:
            fields = ("default", "choices", "required", "nargs", "kind",
                      "type", "missing")
            self.assertTrue(
                all(any(f in v for f in fields) for v in violations),
                violations)
        else:
            self.assertEqual(0, pg.readiness("recall"))

    def test_readiness_is_green_for_the_leaf_donor_only_verb(self):
        self.assertEqual(0, pg.readiness("fleet-emit"))


class CliTests(unittest.TestCase):

    def test_promotiongate_main_is_green(self):
        self.assertEqual(0, pg.main([]))

    def test_main_readiness_requires_a_verb(self):
        with self.assertRaises(SystemExit):
            pg.main(["--readiness"])


if __name__ == "__main__":
    unittest.main()
