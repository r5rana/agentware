"""The STRANGLER ROUTING gate (plan task 29) — legacy-default, and the argv scan.

Three claims, deliberately separated because they fail for different reasons:

  1. THE ALLOWLIST IS REAL. `PORTED_VERBS` exists, holds only genuine top-level
     union verbs, and is immutable. It started EMPTY at task 29 (empty by
     POLICY, not by accident) and by task 54 holds all 44, so the arm at risk of
     shipping untested has INVERTED: the LIB branch is now exercised by every
     live verb, and it is the LEGACY branch that no real verb reaches. Both are
     asserted — the lib arm by a `dispatch` probe with a substituted allowlist,
     the legacy arm by a token that is not a verb in either tree.

     Task 92 note: the tests here must hold in BOTH monolith states, because
     the retirement measures itself by deleting `scripts/agentware_legacy.py`
     in a throwaway worktree. Anything that can only be true while the monolith
     stands belongs in a terminal-state assertion, not in a passing test.

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
    """Claim 1 — the allowlist exists, is REAL, and its LIB branch is live.

    It said "is empty" until task 92. That was true at task 29 and false from
    task 37 on, and by task 54 it was the exact inverse of the tree: all 44
    union verbs are allowlisted and it is the LEGACY arm that no real verb
    reaches. A docstring stating the opposite of the tree is not cosmetic — the
    two harness bugs this same task fixed both survived for as long as they did
    because a stale rationale explained away the wrong answer.
    """

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

    def test_dispatch_really_CALLS_the_lib_for_an_allowlisted_verb(self):
        # Was `test_the_lib_branch_is_live_despite_the_empty_allowlist`, whose
        # rationale ("with an empty allowlist every other test exercises ONLY
        # the legacy arm") stopped being true at task 37. The test is kept
        # because what it proves was never the allowlist's emptiness: the live
        # routing tests assert `route`, which returns a LABEL, and only this one
        # asserts that `dispatch` then actually CALLS `agentware_lib.cli.main`
        # with the argv it routed. A router that labels correctly and calls the
        # wrong arm passes every other test in this file.
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
        unported = [v for v in self.union["top_level_verbs"]
                    if v not in SR.PORTED_VERBS]
        for verb in unported:
            self.assertEqual(SR.route([verb]), SR.LEGACY, verb)
        if unported:
            return
        # TERMINAL STATE (task 54 complete, task 92): every union verb is
        # allowlisted, so this loop has nothing left to walk. That is the goal,
        # not a gap — but "no verbs to check" must not be allowed to look like
        # "checked". Assert the condition that MAKES it empty, so the loop going
        # quiet still reddens if the allowlist ever shrinks back below the union.
        self.assertEqual(
            set(self.union["top_level_verbs"]) - set(SR.PORTED_VERBS), set(),
            "a union verb left the allowlist — this loop is empty for the "
            "wrong reason")
        # The LEGACY-DEFAULT rule itself is what survives the wave, and it is
        # still live: an unknown token has no allowlist row and falls to the
        # monolith arm. Proven here rather than only in DispatchTest so this
        # test never becomes a no-op.
        self.assertEqual(SR.route(["no-such-verb-x9"]), SR.LEGACY)

    def test_the_LEGACY_arm_can_never_serve_a_VERB(self):
        """UNREACHABLE, not merely unused — the distinction task 92 asks for.

        "Unused" would be a fact about today's traffic. The claim here is a
        PARTITION over the whole input space, so it cannot quietly become false:

          * every one of the 44 union verbs routes to the LIB, so no verb's
            behaviour can come from the monolith; and
          * the tokens that DO still select the legacy arm are exactly the
            tokens that are not union verbs — and argparse rejects those by
            name on whichever arm receives them, in both trees.

        So the legacy arm is reachable only by inputs that no implementation
        serves. That is what makes the file deletable at task 96, and it is a
        stronger statement than "PORTED_VERBS is full": it stays true by
        construction if a verb is ever ADDED to the union without being
        allowlisted, because this test reddens on that verb.
        """
        verbs = self.union["top_level_verbs"]
        self.assertEqual(len(verbs), UNION_VERBS)
        legacy_served = [v for v in verbs if SR.route([v]) is not SR.LIB]
        self.assertEqual(
            [], legacy_served,
            "these union verbs are still served by the monolith, so the legacy "
            "arm is reachable and the shim cannot be deleted: %s" % legacy_served)
        # ...and the arm itself is still WIRED — selected by a non-verb, which
        # is the only thing left that can reach it. A router that had lost the
        # legacy branch entirely would pass the assertion above vacuously.
        for token in ("no-such-verb-x9", "json", "--", "-"):
            self.assertNotIn(token, verbs)
            self.assertEqual(SR.route([token]), SR.LEGACY, token)

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

    def test_the_main_guard_CALLS_something_and_names_the_router(self):
        """The textual half of claim 3, stated so it survives the retirement.

        This used to require the literal token `dispatch` in the tail, on the
        rationale that "a tail that calls legacy main() directly makes every
        ported verb unreachable". That rationale is sound only while a legacy
        main EXISTS to be called by mistake. At the terminal state (task 96) the
        tail calls `agentware_lib.cli.main` directly and correctly, and the old
        assertion would red on the CORRECT tail while staying blind to the
        failure it was written to catch.

        So the text is asked only for what text can honestly answer — the guard
        is present and calls something — and the real claim moves next door, to
        the behavioural probe that runs a verb only the LIB can serve. The
        router import is still required by name: task 96 keeps `_strangler`
        precisely so the routing tests above have a module to address.
        """
        guard = self._main_guard()
        self.assertIsNotNone(guard, "the entrypoint lost its __main__ guard")
        body = ast.get_source_segment(self.source, guard) or ""
        self.assertTrue(
            any(isinstance(n, ast.Call) for n in ast.walk(guard)),
            "the __main__ tail calls nothing, so the entrypoint does nothing:\n"
            + body)
        self.assertIn("strangler", self.source,
                      "the entrypoint does not import the router at all")

    def test_the_entrypoint_really_reaches_the_LIB_for_a_verb_only_it_can_serve(self):
        """The behavioural half of claim 3 — one rule in BOTH monolith states.

        A donor-only verb is the exemplar because the monolith has no handler
        for it at all (`grep -c 'def cmd_team' agentware_legacy.py` is 0). If it
        exits 0 through the REAL entrypoint, the tail reached `agentware_lib` —
        whether via `_strangler.dispatch`'s LIB arm while the passthrough stands
        or via the direct call once it is gone. Nothing here depends on which
        state the tree is in, which is the property the AST assertion above lost
        and the reason it was demoted rather than deleted.
        """
        donor_only = _union()["donor_only_verbs"]
        self.assertTrue(donor_only, "the union fixture lost its donor-only set")
        verb = sorted(donor_only)[0]
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, AGENTWARE_KNOWLEDGE_DIR=tmp)
            proc = subprocess.run(
                [sys.executable, CLI_PATH, verb, "--help"],
                capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(
            proc.returncode, 0,
            "%r is donor-only, so a nonzero exit means the entrypoint never "
            "reached the lib: stdout=%r stderr=%r"
            % (verb, proc.stdout, proc.stderr))
        self.assertIn(verb, proc.stdout)

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

    def _run(self, argv):
        # whereami is read-only and touches no knowledge base; the scratch
        # AGENTWARE_KNOWLEDGE_DIR is belt-and-braces so nothing can reach the
        # operator's live KB.
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, AGENTWARE_KNOWLEDGE_DIR=tmp)
            proc = subprocess.run(
                [sys.executable, CLI_PATH] + argv,
                capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(proc.returncode, 0,
                         "argv=%r stdout=%r stderr=%r"
                         % (argv, proc.stdout, proc.stderr))
        return proc.stdout

    def test_a_global_flag_before_the_verb_REACHES_THE_RIGHT_VERB_end_to_end(self):
        """The routing claim through the REAL entrypoint: parse, route, run.

        The assertion used to be `assertIn("agentware", stdout)`, which is
        satisfied by the CHECKOUT DIRECTORY NAME appearing in whereami's output.
        It passes in a directory called `agentware` and reds in every other one
        — it went red the moment this suite was run in the monolith-free probe
        worktree — and it never once depended on the routing it was named for.
        So it asserts the resolution instead: the verb ran, and it resolved the
        directory it was ASKED about rather than some other one.
        """
        out = self._run(["--format", "json", "whereami", "--dir", REPO_ROOT])
        self.assertIn("repo_root", out)
        self.assertIn(REPO_ROOT, out)
        # ...and it is really `whereami` answering, not a verb the scan
        # misrouted to. `json` is the VALUE of the global option; a router that
        # read it as the verb would exit nonzero on an invalid choice.
        self.assertNotIn("invalid choice", out)

    def test_a_global_flag_before_the_verb_does_NOT_reach_it_AS_A_VALUE(self):
        """CHARACTERIZATION PIN — a real defect, deliberately not fixed here.

        `--format` is declared on the top-level parser AND, via the shared
        `fmt_parent`, on all 94 subparsers. argparse's `_SubParsersAction`
        parses the subcommand into a FRESH namespace and copies every key back
        over the parent's, so the subparser's `default="text"` OVERWRITES the
        `json` the top-level parser already resolved. `agentware --format json
        whereami` therefore prints TEXT, and so does every other verb.

        Scope: this is a pre-existing product defect, byte-identical in the
        monolith (`agentware_legacy.py` builds the same shared-parent parser at
        line ~15331) and in `agentware_lib.cli.assemble_parser`. It is not
        caused by the port, not caused by the shim retirement, and no call site
        in the repo hits it — `agentware.sh`, `scripts/loop/*`, `scripts/hooks/*`
        and every skill put `--format` AFTER the verb. Every clean fix either
        churns the binding union fixture (setting `default=SUPPRESS` on the
        shared action changes the recorded `default` of 94 nodes) or duplicates
        the router's arity scan in a second place, so it belongs to its own plan
        rather than to a gate-fixing task.

        This pin exists so the defect cannot be forgotten and cannot be
        "fixed" silently: it reddens the day the behaviour changes, in either
        direction, and points at the follow-up.
        """
        pre = self._run(["--format", "json", "whereami", "--dir", REPO_ROOT])
        post = self._run(["whereami", "--dir", REPO_ROOT, "--format", "json"])
        self.assertTrue(post.lstrip().startswith("{"),
                        "the POST-verb --format must still produce JSON: %r" % post)
        self.assertFalse(
            pre.lstrip().startswith("{"),
            "the PRE-verb --format now reaches the verb — the defect this pin "
            "characterizes is FIXED. Delete this test and record the follow-up "
            "as done; do not weaken it. Got: %r" % pre)


if __name__ == "__main__":
    unittest.main()
