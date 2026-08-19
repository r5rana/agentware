"""The DONOR-ONLY VERB PRESENCE GATE suite (plan task 32, gate 3 of 3).

Four claims, deliberately separated:

  1. GREEN — all six donor-only verbs are present, complete against the union
     and non-stub in the modular tree today.
  2. EXHAUSTIVE ABSENCE — each of the six is deleted IN TURN and must redden on
     its own. Task 30 shipped a per-row gate in which 2 of 242 rows were inert
     and only the exhaustive pass found them; six is small enough that there is
     no excuse for sampling.
  3. STUBS — a verb registered with nothing behind it is the natural artifact of
     a half-finished port, and it satisfies "the verb exists". Every shape is
     probed: a container verb stripped of its subcommands, a leaf verb stripped
     of its arguments, and a verb carrying nothing but the inherited `--format`.
  4. THE GATE CANNOT BE DISARMED BY ITS OWN INPUT — an empty donor-only list in
     the fixture must FAIL rather than pass a zero-row loop, because the fixture
     is a file and files get edited.

Mutations are made to the captured node list IN MEMORY through the
`verb_violations(verb, lib_nodes, union_nodes)` seam.

Stdlib only, deterministic.
"""

from tests._paths import REPO_ROOT, TESTS_DIR

import copy
import os
import sys
import unittest

try:
    from tests import _presence_gate as prg
    from tests import _promotion_gate as pg
    from tests import _surface_union as su
except ImportError:  # allow `python3 -m unittest tests.gates.test_presence_gate`
    sys.path.insert(0, TESTS_DIR)
    import _presence_gate as prg  # type: ignore
    import _promotion_gate as pg  # type: ignore
    import _surface_union as su  # type: ignore

UNION = su.load_json(prg.UNION_FIXTURE)
DONOR_ONLY = list(UNION["donor_only_verbs"])


def _lib():
    return copy.deepcopy(pg.capture_lib())


def _without(nodes, verb):
    return [n for n in nodes if list(n["path"])[:1] != [verb]]


def _top(nodes, verb):
    return prg.top_node(nodes, verb)


def promo_module():
    """The `_promotion_gate` module object the presence gate really calls."""
    return prg.promo


class GreenTests(unittest.TestCase):

    def test_presencegate_passes_over_the_live_tree(self):
        self.assertEqual(0, prg.check())

    def test_the_six_verbs_are_exactly_the_plans_set(self):
        self.assertEqual(
            ["fleet", "fleet-emit", "org", "team", "templates", "work"],
            sorted(DONOR_ONLY))

    def test_every_donor_only_verb_is_present_in_the_MODULAR_parser(self):
        lib = _lib()
        for verb in DONOR_ONLY:
            self.assertIsNotNone(_top(lib, verb), verb)

    @unittest.skipUnless(os.path.isfile(os.path.join(
        REPO_ROOT,
        "scripts", "agentware_legacy.py")), "monolith retired (task 56)")
    def test_no_donor_only_verb_is_served_by_the_legacy_entrypoint(self):
        """The premise of the whole gate: legacy cannot serve these at all.

        Skipped once the monolith is gone — at task 56 the entrypoint parser IS
        the lib parser and serves all six, so this assertion inverts for the
        right reason rather than catching a regression.
        """
        cli = pg.capture_effective()
        for verb in DONOR_ONLY:
            self.assertIsNone(_top(cli, verb), verb)

    def test_a_subset_can_be_checked(self):
        self.assertEqual(0, prg.check(verbs=["team", "org"]))

    def test_a_non_donor_only_verb_is_rejected(self):
        self.assertEqual(1, prg.check(verbs=["recall"]))


class ExhaustiveAbsenceTests(unittest.TestCase):

    def test_deleting_EACH_donor_only_verb_reddens_naming_it(self):
        lib = _lib()
        for verb in DONOR_ONLY:
            with self.subTest(verb=verb):
                mutated = _without(lib, verb)
                violations = prg.verb_violations(verb, mutated, UNION["nodes"])
                self.assertTrue(violations, verb)
                self.assertTrue(any(verb in v and "ABSENT" in v
                                    for v in violations), violations)

    def test_deleting_one_verb_leaves_the_other_five_green(self):
        lib = _lib()
        for verb in DONOR_ONLY:
            with self.subTest(verb=verb):
                rows = prg.evaluate(UNION, _without(lib, verb))
                red = [v for v, viol in rows if viol]
                self.assertEqual([verb], red)

    def test_deleting_a_SUBNODE_reddens_even_though_the_verb_exists(self):
        lib = [n for n in _lib() if list(n["path"]) != ["team", "register"]]
        _top(lib, "team")["subcommands"] = ["create", "list"]
        violations = prg.verb_violations("team", lib, UNION["nodes"])
        self.assertTrue(any("register" in v for v in violations), violations)

    def test_a_narrowed_flag_on_a_donor_only_verb_reddens(self):
        """Presence is not existence: the union comparison rides along."""
        lib = _lib()
        node = _top(lib, "fleet-emit")
        node["actions"] = [a for a in node["actions"]
                           if a["dest"] != "steering_sha"]
        violations = prg.verb_violations("fleet-emit", lib, UNION["nodes"])
        self.assertTrue(any("steering_sha" in v for v in violations),
                        violations)


class StubTests(unittest.TestCase):

    def test_a_CONTAINER_verb_stripped_of_its_subverbs_reddens_as_a_STUB(self):
        """The plan's named probe: `a stub verb with no subverbs reddens it`."""
        lib = _without(_lib(), "team")
        lib.append({"path": ["team"], "depth": 1, "sides": "donor",
                    "subcommand_dest": None, "subcommands": [], "actions": []})
        violations = prg.verb_violations("team", lib, UNION["nodes"])
        self.assertTrue(any("STUB" in v for v in violations), violations)

    def test_a_stub_carrying_ONLY_the_inherited_format_flag_reddens(self):
        """`parents=[fmt_parent]` is the one-line way to build an empty verb."""
        lib = _without(_lib(), "org")
        lib.append({"path": ["org"], "depth": 1, "sides": "donor",
                    "subcommand_dest": None, "subcommands": [],
                    "actions": [{"kind": "_StoreAction", "dest": "format",
                                 "option_strings": ["--format"],
                                 "required": False, "nargs": None,
                                 "default": "text",
                                 "choices": ["text", "json"], "type": None}]})
        violations = prg.verb_violations("org", lib, UNION["nodes"])
        self.assertTrue(any("STUB" in v for v in violations), violations)

    def test_a_LEAF_verb_stripped_of_its_arguments_reddens_as_a_STUB(self):
        lib = _without(_lib(), "fleet-emit")
        lib.append({"path": ["fleet-emit"], "depth": 1, "sides": "donor",
                    "subcommand_dest": None, "subcommands": [], "actions": []})
        violations = prg.verb_violations("fleet-emit", lib, UNION["nodes"])
        self.assertTrue(any("STUB" in v for v in violations), violations)

    def test_a_verb_with_ONE_subcommand_is_NOT_a_stub(self):
        lib = _lib()
        for verb in DONOR_ONLY:
            with self.subTest(verb=verb):
                self.assertEqual([], prg.stub_violations(verb,
                                                         _top(lib, verb)))

    def test_substance_ignores_the_inherited_format_flag(self):
        node = {"path": ["x"], "subcommands": [],
                "actions": [{"dest": "format", "option_strings": ["--format"],
                             "disposition": "required"}]}
        self.assertEqual(([], []), prg.substance_of(node))


class RoutingTests(unittest.TestCase):
    """Present in the parser is not the same as reachable from the CLI.

    A donor-only verb also has to be in `strangler.PORTED_VERBS` for the router
    to send it to the lib. Before task 52 none of the six was, so all six were
    registered and unreachable — `agentware team --help` answered `invalid
    choice: 'team'`. This assertion named that pending state rather than printing
    an unqualified OK over it, precisely so it could not print the same OK after
    task 52 if the allowlist append were forgotten or misspelled.

    Task 52 landed all six, so the assertion INVERTS: every donor-only verb must
    now be allowlisted. It stays a bidirectional claim — a verb dropped from the
    allowlist reddens here, and so does a donor-only verb added to the union
    fixture without a routing row, which is the case that would otherwise ship
    another dead verb.
    """

    def test_every_donor_only_verb_is_ROUTED_not_merely_registered(self):
        missing = sorted(v for v in DONOR_ONLY if v not in pg.ported_verbs())
        self.assertEqual(missing, [],
                         "donor-only verb(s) registered but NOT in "
                         "strangler.PORTED_VERBS: the monolith has no handler "
                         "for them, so each is DEAD — `--help` exits nonzero: %r"
                         % (missing,))

    def test_an_allowlisted_verb_that_routes_correctly_is_green(self):
        self.assertEqual([], prg.routing_violations("team", {"team"}))

    def test_a_verb_NOT_in_the_allowlist_is_not_routing_checked(self):
        self.assertEqual([], prg.routing_violations("team", set()))

    def test_an_allowlisted_verb_that_FAILS_to_route_reddens(self):
        """A verb the argv scan cannot resolve is promoted in name only."""
        self.assertTrue(prg.routing_violations("--format", {"--format"}))

    def test_routing_is_wired_into_the_verb_verdict(self):
        lib = _lib()
        self.assertEqual([], prg.verb_violations("team", lib, UNION["nodes"],
                                                 {"team"}))
        bad = prg.verb_violations("--format", lib, UNION["nodes"], {"--format"})
        self.assertTrue(bad)


class CannotBeDisarmedTests(unittest.TestCase):

    def test_an_EMPTY_donor_only_list_FAILS_rather_than_passing_vacuously(self):
        self.assertEqual(1, prg.check(verbs=[]))

    def test_evaluate_returns_one_row_per_verb(self):
        rows = prg.evaluate(UNION, _lib())
        self.assertEqual(sorted(DONOR_ONLY), sorted(v for v, _ in rows))

    def test_the_gate_shares_the_promotion_gates_comparator(self):
        """One comparator, so the two gates cannot drift apart on `choices`,
        `default`, `required`, `nargs`, `kind` or `type`.

        Behavioural, not a source grep: substitute the comparator and the
        presence gate's verdict must change with it. An earlier version
        asserted the literal string `promo.promotion_violations` appeared in
        the file, which `getattr(promo, 'promotion_' + 'violations')` passes
        while calling something else entirely.
        """
        calls, original = [], promo_module().promotion_violations

        def spy(verb, candidate, expected):
            calls.append(verb)
            return ["synthetic violation"]

        promo_module().promotion_violations = spy
        try:
            self.assertEqual(1, prg.check(verbs=["team"]))
        finally:
            promo_module().promotion_violations = original
        self.assertEqual(["team"], calls)

    def test_the_gate_reads_the_MODULAR_parser_not_the_entrypoint(self):
        """Substitute the lib capture and the verdict must follow it."""
        original = promo_module().capture_lib
        promo_module().capture_lib = lambda: []
        try:
            self.assertEqual(1, prg.check(verbs=["team"]))
        finally:
            promo_module().capture_lib = original
        self.assertEqual(0, prg.check(verbs=["team"]))


class CliTests(unittest.TestCase):

    def test_presencegate_main_is_green(self):
        self.assertEqual(0, prg.main([]))

    def test_main_accepts_a_verb_subset(self):
        self.assertEqual(0, prg.main(["--verbs", "team"]))

    def test_main_rejects_an_unknown_verb(self):
        self.assertEqual(1, prg.main(["--verbs", "nope"]))


if __name__ == "__main__":
    unittest.main()
