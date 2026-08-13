"""The committed R-RET-03/R-RET-04 retrieval ladder, pinned by its NUMBERS.

Feature 260813-modular-core-adoption, task 79 (parity row
`retrieval:token-budget-ladder`, dimension `retrieval_policy`).

WHY THIS FILE EXISTS
--------------------
The parity ledger has claimed since task 10 that this tree keeps a two-stage
recall ladder -- stage 1 at top-5 / 1500 tokens over curated knowledge, stage 2
widening to top-10 / 3000 -- rather than the donor's single collapsing 10000.
Task 79's mutation sweep ran that claim for the first time and it was vacuous:
replacing EVERY occurrence of `1500` in
`scripts/agentware_lib/retrieval/recallcmd.py` with `10000` left all 37 tests in
`tests.unit.retrieval.test_recall` green.

The reason is the same shape as the BM25 parameter gap this task also closed.
Every budget-sensitive test in that module passes `--token-budget` EXPLICITLY
(0, 60, 100, 1500, 100000), so none of them observes the default; the one
bare-default call asserts only that the key is present and which id ranks first,
and the four-entry synthetic KB fits under either budget. A ladder nothing
asserts is a ladder any refactor may quietly flatten -- and flattening it is not
a cosmetic change. It is the difference between injecting a focused,
token-budgeted set (R-CTX-05) and dumping the corpus.

WHAT IS PINNED, AND WHY THAT SET
--------------------------------
  A  THE TWO SURFACES AGREE. `build_recall_payload`'s signature defaults and the
     `--token-budget` / `--top-k` argparse defaults are ONE ladder. Pinned
     separately and then pinned EQUAL, because two independent defaults is how a
     ladder drifts: the MCP path and the CLI path would disagree and neither
     would be wrong on its own terms.
  B  THE STEERING AND THE CODE AGREE. `AGENTS.md` is the always-on rule text an
     agent obeys; the code is what actually runs. R-RET-03 states stage 1's two
     numbers in prose and R-RET-04 states stage 2's. If the code's default and
     the rule's number diverge, every session is told to do something the tool
     does not do -- and the always-on lint cannot see it, because to a linter
     "1500" is just a character sequence in a sentence.
  C  STAGE 2 IS STRICTLY WIDER. The ladder's whole content is that stage 2 sees
     MORE than stage 1. Equal numbers would satisfy A and B and still be a
     collapsed ladder, which is exactly the donor shape this row rejects.
  D  THE BUDGET IS BEHAVIOURAL, NOT DECORATIVE. A tight budget must actually
     truncate the returned set and report the truncation, so the numbers above
     are pinning something that happens rather than a value nobody reads.

Stdlib `unittest` only. Runs against the synthetic KB; never touches the real one.
"""

import inspect
import os
import re

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # allow `python3 -m unittest tests.unit.retrieval.test_recall_ladder`
    from _fixtures import SyntheticKBTestCase, load_cli

from tests._paths import REPO_ROOT

CLI = load_cli()

#: The committed ladder. These four numbers ARE the capability.
STAGE1_TOP_K = 5
STAGE1_TOKEN_BUDGET = 1500
STAGE2_TOP_K = 10
STAGE2_TOKEN_BUDGET = 3000

STEERING = os.path.join(REPO_ROOT, "AGENTS.md")


def _rule(rule_id):
    """The one always-on steering line carrying `[<rule_id>]`."""
    with open(STEERING, encoding="utf-8") as fh:
        for line in fh:
            if line.rstrip().endswith("[%s]" % rule_id):
                return line
    raise AssertionError("%s declares no rule %s" % (STEERING, rule_id))


def _parser_default(dest):
    """The `agentware recall` argparse default for one dest, read from the parser."""
    parser = CLI.build_parser()
    for action in parser._actions:                      # noqa: SLF001
        if action.dest in ("command", "cmd") and action.choices:
            recall = action.choices.get("recall")
            if recall is None:
                raise AssertionError("the parser has no `recall` verb")
            for sub in recall._actions:                 # noqa: SLF001
                if sub.dest == dest:
                    return sub.default
    raise AssertionError("`agentware recall` has no %r option" % dest)


class LadderDefaultsArePinnedTest(SyntheticKBTestCase):
    """Arm A -- the two surfaces, pinned and then pinned equal."""

    def test_the_shared_payload_builder_defaults_to_stage_one(self):
        sig = inspect.signature(CLI.build_recall_payload)
        self.assertEqual(sig.parameters["token_budget"].default,
                         STAGE1_TOKEN_BUDGET,
                         "the shared builder's default token budget moved; every "
                         "caller that does not pass one silently changed")
        self.assertEqual(sig.parameters["top_k"].default, STAGE1_TOP_K)

    def test_the_cli_defaults_to_stage_one(self):
        self.assertEqual(_parser_default("token_budget"), STAGE1_TOKEN_BUDGET)
        self.assertEqual(_parser_default("top_k"), STAGE1_TOP_K)

    def test_the_cli_and_the_shared_builder_are_ONE_ladder(self):
        sig = inspect.signature(CLI.build_recall_payload)
        self.assertEqual(
            (_parser_default("token_budget"), _parser_default("top_k")),
            (sig.parameters["token_budget"].default,
             sig.parameters["top_k"].default),
            "the CLI and the shared payload builder disagree on stage 1, so a "
            "recall through MCP and a recall through the terminal return "
            "different sets for the same query")


class SteeringAndCodeAgreeTest(SyntheticKBTestCase):
    """Arm B + C -- the rule an agent obeys names the numbers the tool uses."""

    def test_R_RET_03_states_stage_ones_committed_numbers(self):
        line = _rule("R-RET-03")
        self.assertIn("top-%d" % STAGE1_TOP_K, line)
        self.assertIn("token-budget-%d" % STAGE1_TOKEN_BUDGET, line,
                      "R-RET-03 no longer names the default the code uses: every "
                      "session is instructed to run a budget the tool will not")

    def test_R_RET_04_states_stage_twos_committed_numbers(self):
        line = _rule("R-RET-04")
        self.assertTrue(re.search(r"--top-k\s+%d\b" % STAGE2_TOP_K, line), line)
        self.assertTrue(
            re.search(r"--token-budget\s+%d\b" % STAGE2_TOKEN_BUDGET, line), line)

    def test_stage_two_is_STRICTLY_wider_than_stage_one(self):
        self.assertGreater(STAGE2_TOKEN_BUDGET, STAGE1_TOKEN_BUDGET,
                           "a ladder whose rungs are equal is not a ladder -- "
                           "that is the donor's single collapsing budget")
        self.assertGreater(STAGE2_TOP_K, STAGE1_TOP_K)

    def test_the_escalation_is_worth_making(self):
        """Stage 2 must widen by enough to be a different answer, not a rounding.

        Pinned as a ratio rather than a difference so the assertion survives a
        deliberate re-scaling of both rungs and still rejects a token gesture.
        """
        self.assertGreaterEqual(STAGE2_TOKEN_BUDGET, 2 * STAGE1_TOKEN_BUDGET - 1)


class TheBudgetIsBehaviouralTest(SyntheticKBTestCase):
    """Arm D -- the pinned number changes what comes back."""

    def _payload(self, **kwargs):
        # The builder returns (payload, mode_notice): the notice is a stderr
        # concern the CLI owns and the MCP tool drops, so it is discarded here
        # rather than asserted -- this arm is about the budget, not the notice.
        payload, _notice = CLI.build_recall_payload(
            self.kdir, self.index_data, "python ranking knowledge", **kwargs)
        return payload

    def test_a_tight_budget_returns_fewer_results_than_stage_one(self):
        tight = self._payload(token_budget=1)
        stage1 = self._payload()
        self.assertLess(len(tight["results"]), len(stage1["results"]),
                        "the token budget did not truncate anything, so pinning "
                        "its value pins nothing")

    def test_the_payload_reports_the_budget_it_actually_applied(self):
        self.assertEqual(self._payload()["token_budget"], STAGE1_TOKEN_BUDGET)
        self.assertEqual(self._payload(token_budget=STAGE2_TOKEN_BUDGET)
                         ["token_budget"], STAGE2_TOKEN_BUDGET)

    def test_stage_two_never_returns_less_than_stage_one(self):
        stage1 = self._payload(top_k=STAGE1_TOP_K,
                               token_budget=STAGE1_TOKEN_BUDGET)
        stage2 = self._payload(top_k=STAGE2_TOP_K,
                               token_budget=STAGE2_TOKEN_BUDGET)
        self.assertGreaterEqual(len(stage2["results"]), len(stage1["results"]))


if __name__ == "__main__":
    import unittest
    unittest.main()
