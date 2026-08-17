"""The STRANGLER ROUTING gate (plan task 29) — legacy-default, and the argv scan.

Three claims, deliberately separated because they fail for different reasons:

  1. THE ALLOWLIST IS EMPTY AND REAL. `PORTED_VERBS` exists, is empty at this
     commit, and is immutable. An empty allowlist is a dangerous thing to ship
     untested — every routing test would pass against a router whose LIB branch
     was a syntax error nobody ever ran — so the LIB branch is exercised
     directly with a substituted allowlist. Empty by POLICY, not by accident.

  2. THE SCAN FINDS THE VERB, not the value of the global option in front of it.
     `agentware --format json recall "x"` has `--format` at argv[1] and `json`
     at argv[2]. While the allowlist is empty this misroute is INVISIBLE (`json`
     is in no allowlist, so it falls to legacy and works), which is why it is
     pinned now, before the first port turns it into a live
     wrong-implementation bug. The check runs over the WHOLE union surface —
     all 44 verbs and all 124 node paths — not a hand-picked verb or two.

  3. THE ENTRYPOINT IS ACTUALLY WIRED TO IT. A perfect router that nothing calls
     is the same as no router. The `if __name__ == "__main__"` tail is read with
     `ast` and must dispatch through the router, and the real entrypoint is run
     as a subprocess with a global flag ahead of the verb.

The option-arity table is the one piece of hardcoded knowledge in the router, so
it is diffed against the ROOT NODE of the union surface fixture: a global option
added on either side reddens this suite instead of silently misrouting.

Stdlib only, deterministic, hermetic (no network; the one subprocess runs the
repo's own entrypoint against a scratch AGENTWARE_KNOWLEDGE_DIR).
"""

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, CLI_PATH, REPO_ROOT
except ImportError:  # allow `python3 -m unittest tests.test_strangler_routing`
    from _fixtures import load_cli, CLI_PATH, REPO_ROOT

# load_cli() puts scripts/ on sys.path, so the router is importable BY NAME.
# Addressing the owning module directly (not only through the facade) is what
# lets these tests substitute an allowlist and prove the LIB branch is live.
CLI = load_cli()

from agentware_lib import strangler as SR  # noqa: E402

UNION_FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "surface-union.json")

#: Pinned from the union fixture header (plan task 7). Stated here so a fixture
#: swapped for a thinner one is a FAILURE rather than a quietly smaller loop.
UNION_VERBS = 44
UNION_NODES = 124

#: The facade's ceiling from the plan's "Ceilings" section. The router lives in
#: the lib precisely so this stays satisfiable.
FACADE_LINE_CAP = 120


def _union():
    with open(UNION_FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


class AllowlistTest(unittest.TestCase):
    """Claim 1 — the allowlist exists, is empty, and its LIB branch is live."""

    def test_the_allowlist_holds_only_PROMOTED_verbs(self):
        # Written as the INVARIANT, not as the day's value: this started EMPTY at
        # task 29 and grows by one verb per port (tasks 37..54), so an
        # `assertEqual(set(), ...)` would have had to be edited — and therefore
        # weakened — at every single port. What must hold FOREVER is that a
        # member is a REAL top-level verb of the union surface, so a typo
        # (`fleet_emit` for `fleet-emit`) or a subverb (`index add`) can never
        # silently route a whole verb to a modular handler that does not own it.
        union_verbs = set(_union()["top_level_verbs"])
        self.assertTrue(set(SR.PORTED_VERBS) <= union_verbs,
                        "allowlist members outside the union surface: %s"
                        % sorted(set(SR.PORTED_VERBS) - union_verbs))
        # ...and each one is registered in the modular parser, or the router
        # would hand it to a lib that cannot parse it.
        from agentware_lib._registration import _PARSER_REGISTRATION
        registered = {verb for verb, _mod, _attr in _PARSER_REGISTRATION}
        self.assertTrue(set(SR.PORTED_VERBS) <= registered,
                        "ported but unregistered in the lib parser: %s"
                        % sorted(set(SR.PORTED_VERBS) - registered))

    def test_the_first_ported_verb_routes_to_the_LIB(self):
        # Task 37 promoted `migrate` (the KB schema interlock). This is the
        # anti-vacuity pin for the LIVE allowlist — distinct from the
        # substituted-allowlist test below, which proves the mechanism.
        self.assertIn("migrate", SR.PORTED_VERBS)
        self.assertEqual(SR.route(["migrate", "--list"]), SR.LIB)
        self.assertEqual(SR.route(["--format", "json", "migrate"]), SR.LIB)

    def test_the_allowlist_is_immutable(self):
        # A mutable default would let any import-time caller append a verb and
        # promote an unproven implementation with no gate and no diff.
        self.assertIsInstance(SR.PORTED_VERBS, frozenset)

    def test_the_lib_branch_is_live_despite_the_empty_allowlist(self):
        # The anti-vacuity pin. With an empty allowlist every other test in this
        # file exercises ONLY the legacy arm, so the lib arm is asserted here by
        # substituting an allowlist — the same mechanism a real port uses.
        seen = {}

        def fake_lib_main(argv):
            seen["argv"] = argv
            return 7

        def fake_legacy_main(argv):  # pragma: no cover - must never be called
            raise AssertionError("routed to legacy for an allowlisted verb")

        import agentware_lib.cli as libcli
        real_main, real_allow = libcli.main, SR.PORTED_VERBS
        try:
            libcli.main = fake_lib_main
            SR.PORTED_VERBS = frozenset({"recall"})
            self.assertEqual(SR.route(["recall", "x"]), SR.LIB)
            self.assertEqual(SR.dispatch(["recall", "x"], fake_legacy_main), 7)
            self.assertEqual(seen["argv"], ["recall", "x"])
            # ...and a NON-allowlisted verb still falls through to legacy.
            self.assertEqual(SR.route(["query", "--id", "x"]), SR.LEGACY)
        finally:
            libcli.main, SR.PORTED_VERBS = real_main, real_allow

    def test_legacy_is_the_default_for_an_unknown_verb(self):
        # LEGACY-DEFAULT is about the DEFAULT, not about the known verb list:
        # anything not written down routes to the monolith, including a verb
        # neither tree has. Argparse there produces the error it always has.
        self.assertEqual(SR.route(["no-such-verb"]), SR.LEGACY)


class VerbScanTest(unittest.TestCase):
    """Claim 2 — the scan steps PAST global flags to the first positional."""

    def test_a_bare_verb_is_the_verb(self):
        self.assertEqual(SR.verb_of(["recall", "x"]), "recall")

    def test_a_global_flag_with_a_separate_value_does_not_misroute(self):
        self.assertEqual(SR.verb_of(["--format", "json", "recall", "x"]), "recall")

    def test_a_global_flag_in_equals_form_does_not_misroute(self):
        self.assertEqual(SR.verb_of(["--format=json", "query", "--id", "x"]), "query")

    def test_the_option_value_is_consumed_even_when_it_looks_like_a_verb(self):
        # The sharpest form of the misroute: without consuming `--format`'s
        # value the scan returns "recall" — a REAL verb — and the invocation is
        # served by the wrong implementation the moment recall is ported.
        self.assertEqual(SR.verb_of(["--format", "recall", "query"]), "query")

    def test_flags_after_the_verb_are_not_scanned(self):
        self.assertEqual(SR.verb_of(["plan", "lint", "--strict"]), "plan")

    def test_the_end_of_options_marker_yields_the_next_token(self):
        self.assertEqual(SR.verb_of(["--", "recall"]), "recall")
        self.assertIsNone(SR.verb_of(["--"]))
        # The case that actually needs the branch, and the reason it is not
        # dead code: after `--` EVERYTHING is positional, so a token SHAPED
        # like an option is the verb (argparse rejects it by name). Without the
        # branch `--format` reads as an option, swallows the rest, and the scan
        # reports no verb at all — a different answer, silently.
        self.assertEqual(SR.verb_of(["--", "--format"]), "--format")
        self.assertEqual(SR.verb_of(["--", "-h"]), "-h")

    def test_no_verb_yields_none(self):
        self.assertIsNone(SR.verb_of([]))
        self.assertIsNone(SR.verb_of(["-h"]))
        self.assertIsNone(SR.verb_of(["--help"]))
        # A trailing option whose value never arrived: no verb, and argparse in
        # legacy emits its usual "expected one argument" error.
        self.assertIsNone(SR.verb_of(["--format"]))

    def test_an_unknown_option_is_assumed_to_take_no_value(self):
        # Documented in verb_of's docstring: the safer of the two errors. The
        # unknown option's neighbour is promoted to "verb", where argparse
        # rejects it loudly, rather than swallowing a real verb.
        self.assertEqual(SR.verb_of(["--nope", "recall"]), "recall")

    def test_a_bare_dash_is_positional(self):
        self.assertEqual(SR.verb_of(["-"]), "-")


class UnionSurfaceRoutingTest(unittest.TestCase):
    """Claim 2, at SURFACE scale — every union verb and every union node."""

    def setUp(self):
        self.union = _union()

    def test_the_fixture_is_the_pinned_union(self):
        self.assertEqual(self.union["counts"]["top_level_verbs"], UNION_VERBS)
        self.assertEqual(self.union["counts"]["nodes"], UNION_NODES)

    def test_every_union_verb_resolves_bare_and_behind_a_global_flag(self):
        verbs = self.union["top_level_verbs"]
        self.assertEqual(len(verbs), UNION_VERBS)
        for verb in verbs:
            for argv in ([verb],
                         ["--format", "json", verb],
                         ["--format=text", verb],
                         ["--", verb]):
                self.assertEqual(SR.verb_of(argv), verb,
                                 "misrouted %r for argv %r" % (verb, argv))

    def test_every_union_node_path_resolves_to_its_top_level_verb(self):
        seen = 0
        for node in self.union["nodes"]:
            path = node["path"]
            seen += 1
            if not path:  # the root node has no verb to find
                self.assertIsNone(SR.verb_of(path))
                continue
            self.assertEqual(SR.verb_of(list(path)), path[0])
            self.assertEqual(SR.verb_of(["--format", "json"] + list(path)), path[0])
        self.assertEqual(seen, UNION_NODES)

    def test_every_UNPORTED_union_verb_still_routes_to_legacy(self):
        # LEGACY-DEFAULT, stated as the invariant it is: everything not written
        # down in the allowlist falls to the monolith.
        for verb in self.union["top_level_verbs"]:
            if verb in SR.PORTED_VERBS:
                continue
            self.assertEqual(SR.route([verb]), SR.LEGACY, verb)

    def test_every_DONOR_ONLY_verb_routes_to_the_LIB(self):
        """Task 52. While these six were unported they routed to legacy — a tree
        with no handler for any of them — so each was DEAD: `agentware team
        --help` answered `invalid choice: 'team'`.

        That is why the donor-only set gets its own assertion rather than being
        folded into the loop above. For a SHARED verb the allowlist row picks
        between two working implementations, and a missing row merely leaves the
        old one serving. For these six there is nothing on the other side, so a
        missing row is not a slower path — it is no path, and the failure is a
        nonzero exit on `--help` rather than a subtle behavioural difference.
        """
        for verb in self.union["donor_only_verbs"]:
            self.assertIn(verb, SR.PORTED_VERBS,
                          "%r is donor-only and NOT allowlisted: the monolith "
                          "has no handler for it, so it is dead" % verb)
            self.assertEqual(SR.route([verb]), SR.LIB, verb)
            self.assertEqual(SR.route(["--format", "json", verb]), SR.LIB, verb)


class GlobalOptionTableTest(unittest.TestCase):
    """The router's ONE piece of hardcoded knowledge, diffed against the fixture.

    The table cannot be derived by introspection: building the parser to decide
    whether to build the parser is the cost the router exists to avoid. So it is
    declared — and gated, so a global option added on either side reddens here
    instead of silently misrouting every invocation that uses it.
    """

    #: argparse adds these implicitly; the surface capture records DECLARED
    #: arguments only, so they cannot come from the fixture.
    IMPLICIT = {"-h", "--help"}

    def setUp(self):
        root = [n for n in _union()["nodes"] if not n["path"]]
        self.assertEqual(len(root), 1, "the union fixture must have one root node")
        self.root_actions = root[0]["actions"]

    def test_the_table_covers_every_declared_global_option(self):
        for action in self.root_actions:
            for opt in action["option_strings"]:
                self.assertIn(
                    opt, SR.GLOBAL_OPTIONS,
                    "global option %r is declared on the union root node but "
                    "the router's arity table does not know it — every "
                    "invocation using it would read its VALUE as the verb"
                    % opt)

    def test_the_table_declares_the_right_arity(self):
        flagish = ("_StoreTrueAction", "_StoreFalseAction", "_CountAction",
                   "_HelpAction", "_VersionAction")
        for action in self.root_actions:
            expected = 0 if action["kind"] in flagish else 1
            self.assertIsNone(
                action["nargs"],
                "the arity rule below assumes nargs=None; %r declares nargs=%r "
                "and needs an explicit rule" % (action["dest"], action["nargs"]))
            for opt in action["option_strings"]:
                self.assertEqual(SR.GLOBAL_OPTIONS[opt], expected, opt)

    def test_the_table_claims_nothing_the_surface_does_not_have(self):
        declared = {o for a in self.root_actions for o in a["option_strings"]}
        extra = set(SR.GLOBAL_OPTIONS) - declared - self.IMPLICIT
        self.assertEqual(
            extra, set(),
            "the router's arity table claims option(s) %s that are on neither "
            "the union root node nor argparse's implicit help action — a stale "
            "entry makes the scan skip a real verb" % sorted(extra))


class DispatchTest(unittest.TestCase):
    """dispatch() — one call deep, argv forwarded EXPLICITLY.

    The exemplar is a verb that DOES NOT EXIST, and that is the point. These
    probes need something the router sends to the legacy arm, and every earlier
    version borrowed whichever real verb happened to be unported that week —
    which works until the wave that promotes it, and by the end of task 54 there
    is no unported shared verb left to borrow. An unknown token is routed to
    legacy by the LEGACY-DEFAULT rule itself, so it exercises exactly the arm
    under test and keeps exercising it for as long as the arm exists (task 56
    deletes it, and then these tests go with it).
    """

    #: Not a verb, and never will be — LEGACY-DEFAULT routes it to the monolith.
    UNKNOWN = "no-such-verb-x9"

    def test_an_unknown_verb_routes_to_the_legacy_arm(self):
        """The premise the two probes below rest on, asserted rather than assumed."""
        self.assertEqual(SR.route([self.UNKNOWN]), SR.LEGACY)

    def test_the_legacy_arm_receives_the_exact_argv_that_was_routed(self):
        seen = {}

        def fake_legacy_main(argv):
            seen["argv"] = argv
            return 3

        argv = ["--format", "json", self.UNKNOWN, "--stale"]
        self.assertEqual(SR.dispatch(argv, fake_legacy_main), 3)
        # Never None: the tokens ROUTED must be the tokens PARSED, so a caller
        # cannot route one argv and have the callee default to another.
        self.assertEqual(seen["argv"], argv)

    def test_the_legacy_return_code_is_propagated_unchanged(self):
        for rc in (0, 1, 2, 42):
            self.assertEqual(SR.dispatch([self.UNKNOWN], lambda _a, r=rc: r), rc)


class FacadeWiringTest(unittest.TestCase):
    """Claim 3 — the entrypoint really dispatches through the router."""

    def setUp(self):
        with open(CLI_PATH, encoding="utf-8") as fh:
            self.source = fh.read()

    def _main_guard(self):
        for node in ast.walk(ast.parse(self.source)):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            if (isinstance(test, ast.Compare)
                    and isinstance(test.left, ast.Name)
                    and test.left.id == "__name__"):
                return node
        return None

    def test_the_main_guard_dispatches_through_the_router(self):
        guard = self._main_guard()
        self.assertIsNotNone(guard, "the entrypoint lost its __main__ guard")
        body = ast.get_source_segment(self.source, guard) or ""
        self.assertIn(
            "dispatch", body,
            "the __main__ tail no longer routes through the strangler router; "
            "a tail that calls legacy main() directly makes every ported verb "
            "unreachable from the real CLI while the suite stays green:\n"
            + body)
        self.assertIn("strangler", self.source,
                      "the entrypoint does not import the router at all")

    def test_the_facade_stays_within_its_ceiling(self):
        n = len(self.source.splitlines())
        self.assertLessEqual(
            n, FACADE_LINE_CAP,
            "the entrypoint facade is %d lines (cap %d) — routing logic belongs "
            "in agentware_lib.strangler, never here" % (n, FACADE_LINE_CAP))

    def test_the_entrypoint_is_executable(self):
        # agentware.sh guards eleven call sites on `[[ -x scripts/agentware ]]`
        # and every one FAILS OPEN, so a 0644 entrypoint disables the R-SI-03
        # knowledge-loss gate silently. Any rewrite must re-chmod.
        self.assertTrue(os.access(CLI_PATH, os.X_OK),
                        "scripts/agentware lost its executable bit")

    def test_a_global_flag_before_the_verb_reaches_the_right_verb_end_to_end(self):
        # The whole claim through the REAL entrypoint: parse, route, run.
        # whereami is read-only and touches no knowledge base; the scratch
        # AGENTWARE_KNOWLEDGE_DIR is belt-and-braces so nothing can reach the
        # operator's live KB.
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, AGENTWARE_KNOWLEDGE_DIR=tmp)
            proc = subprocess.run(
                [sys.executable, CLI_PATH, "--format", "json", "whereami",
                 "--dir", REPO_ROOT],
                capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(proc.returncode, 0,
                             "stdout=%r stderr=%r" % (proc.stdout, proc.stderr))
            self.assertIn("agentware", proc.stdout)


if __name__ == "__main__":
    unittest.main()
