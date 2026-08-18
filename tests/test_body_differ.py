"""The BODY-DIFFER suite (plan task 30).

Three claims, kept apart so a green result can never be mistaken for the wrong
one:

  1. BASELINE — the committed fixture is well formed: one row per divergent
     top-level definition, every row carrying an adjudicated winner and a floor,
     no stale adjudication, and a deferred set pinned by name.
  2. POSITIVE — the merged tree clears every floor: both violation columns are
     empty, in either direction.
  3. REFUTATION — each way the gate is supposed to bite is made to bite, one at
     a time, against an injected tree. This is the half that matters. A body
     differ is a gate whose failure mode is silence: a port that lands a thinner
     implementation changes no flag, no verb, no manifest row and no test node
     id, so if the gate does not actually fire, nothing anywhere goes red and
     the capability is simply gone.

The refutation tests never touch the checkout. `_body_differ.check()` is a pure
function of (fixture, tree-capture), so a defect is injected by mutating a
captured dict — no temp trees, no subprocesses, and no chance of a test that
passes because it silently measured the real tree instead of the broken one.

Stdlib only, deterministic, hermetic.
"""

import ast
import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _body_differ as bd                                    # noqa: E402
from _body_adjudication import ADJUDICATIONS, DEFERRED_NAMES  # noqa: E402

REPO = os.path.dirname(HERE)

# The row count this normalisation actually yields over the two trees. The plan
# text says 243; five candidate normalisations (raw source, comment-stripped
# source, full source with signature, unparsed body, unparsed body without the
# docstring) and four independent capture constructions all yield 242, and the
# plan's own per-function char counts reproduce under none of them. See the
# worklog decision for task 30.
EXPECTED_ROWS = 242


def _fixture():
    return bd.load_fixture()


def _tree():
    return bd.merged_tree(REPO)


class BaselineShapeTest(unittest.TestCase):
    """Claim 1 — the committed baseline is well formed."""

    def setUp(self):
        self.fx = _fixture()
        self.rows = self.fx["rows"]

    def test_schema_and_normalisation_are_pinned(self):
        self.assertEqual(bd.SCHEMA, self.fx["schema"])
        self.assertEqual(bd.NORMALISATION, self.fx["normalisation"])

    def test_row_count_matches_the_recorded_divergence(self):
        self.assertEqual(EXPECTED_ROWS, len(self.rows))
        self.assertEqual(EXPECTED_ROWS, self.fx["counts"]["divergent"])

    def test_counts_are_self_consistent(self):
        counts = self.fx["counts"]
        self.assertEqual(counts["shared"],
                         counts["identical"] + counts["divergent"])

    def test_every_row_carries_an_adjudicated_winner(self):
        for row in self.rows:
            self.assertIn(row["winner"], bd.WINNERS, row["name"])
            self.assertGreater(row["winner_chars"], 0, row["name"])
            expected = (row["local_chars"] if row["winner"] == "local"
                        else row["donor_chars"])
            self.assertEqual(expected, row["winner_chars"], row["name"])

    def test_winner_is_the_longer_body(self):
        for row in self.rows:
            self.assertEqual(max(row["local_chars"], row["donor_chars"]),
                             row["winner_chars"], row["name"])

    def test_every_row_carries_a_live_floor(self):
        """No row is exempt: superseded and deferred ratchet on floor_chars."""
        for row in self.rows:
            self.assertGreater(bd.required_chars(row), 0, row["name"])

    def test_rows_are_sorted_and_unique(self):
        names = [row["name"] for row in self.rows]
        self.assertEqual(sorted(names), names)
        self.assertEqual(len(set(names)), len(names))

    def test_both_directions_are_represented(self):
        winners = {row["winner"] for row in self.rows}
        self.assertEqual({"local", "donor"}, winners)

    def test_no_stale_adjudication(self):
        """An adjudication naming a row that no longer diverges is dead weight."""
        names = {row["name"] for row in self.rows}
        self.assertEqual(set(), set(ADJUDICATIONS) - names)

    def test_decomposed_rows_name_a_host_receivers_and_a_host_floor(self):
        for row in self.rows:
            if row["adjudication"] != "decomposed":
                continue
            self.assertTrue(row.get("host"), row["name"])
            self.assertTrue(row.get("absorbs"), row["name"])
            self.assertGreater(row["floor_chars"], 0, row["name"])
            for ref in row["absorbs"]:
                self.assertIn("::", ref, row["name"])

    def test_superseded_and_deferred_rows_carry_a_reason(self):
        for row in self.rows:
            if row["adjudication"] not in ("superseded", "deferred"):
                continue
            self.assertGreater(row["floor_chars"], 0, row["name"])
            self.assertTrue(row.get("rationale"), row["name"])

    def test_the_deferred_set_is_pinned_by_name(self):
        """Deferrals must be argued for one at a time, never accumulated."""
        deferred = {row["name"] for row in self.rows
                    if row["adjudication"] == "deferred"}
        self.assertEqual(set(DEFERRED_NAMES), deferred)
        self.assertEqual({"get_cli"}, deferred)
        for row in self.rows:
            if row["adjudication"] == "deferred":
                self.assertTrue(row.get("follow_up"), row["name"])

    def test_fixture_is_valid_json_on_disk(self):
        with open(bd.FIXTURE, "r", encoding="utf-8") as fh:
            self.assertIsInstance(json.load(fh), dict)


class MergedTreeTest(unittest.TestCase):
    """Claim 2 — the merged tree clears every floor."""

    def setUp(self):
        self.report = bd.check(_fixture(), _tree())

    def test_no_definition_is_thinner_than_its_winner(self):
        self.assertEqual([], self.report["local_longer"])
        self.assertEqual([], self.report["donor_longer"])

    def test_no_definition_went_missing(self):
        self.assertEqual([], self.report["missing"])

    def test_every_declared_absorber_still_exists(self):
        self.assertEqual([], self.report["absorber_missing"])

    def test_the_gate_exits_zero(self):
        self.assertEqual([], bd.violations(self.report))

    def test_every_row_was_actually_checked(self):
        self.assertEqual(EXPECTED_ROWS, self.report["checked"])


class RefutationTest(unittest.TestCase):
    """Claim 3 — every way the gate should bite, made to bite."""

    def setUp(self):
        self.fx = _fixture()
        self.tree = _tree()

    def _rows_by_winner(self, winner, adjudication="longer-body"):
        return [r for r in self.fx["rows"]
                if r["winner"] == winner and r["adjudication"] == adjudication]

    def _shrink(self, tree, name, keep=1):
        """Replace every body carrying `name` with a stub."""
        tree = copy.deepcopy(tree)
        tree[name] = [(rel, body[:keep]) for rel, body in tree[name]]
        return tree

    def _assert_named(self, report, column, name):
        named = [row["name"] for row in report[column]]
        self.assertIn(name, named)
        self.assertTrue(bd.violations(report))

    def test_a_thinner_body_reddens_the_local_column_by_name(self):
        row = self._rows_by_winner("local")[0]
        report = bd.check(self.fx, self._shrink(self.tree, row["name"]))
        self._assert_named(report, "local_longer", row["name"])

    def test_a_thinner_body_reddens_the_donor_column_by_name(self):
        row = self._rows_by_winner("donor")[0]
        report = bd.check(self.fx, self._shrink(self.tree, row["name"]))
        self._assert_named(report, "donor_longer", row["name"])

    def test_every_row_reddens_when_its_own_body_is_thinned(self):
        """Not one representative row -- ALL 242, so no row is inert."""
        inert = []
        for row in self.fx["rows"]:
            report = bd.check(self.fx, self._shrink(self.tree, row["name"]))
            if not bd.violations(report):
                inert.append(row["name"])
        self.assertEqual([], inert)

    def test_the_report_states_what_was_needed_and_what_was_found(self):
        row = self._rows_by_winner("donor")[0]
        report = bd.check(self.fx, self._shrink(self.tree, row["name"]))
        hit = [r for r in report["donor_longer"] if r["name"] == row["name"]][0]
        self.assertEqual(bd.required_chars(row), hit["need"])
        self.assertEqual(1, hit["have"])

    def test_a_deleted_definition_reddens_as_missing(self):
        row = self.fx["rows"][0]
        tree = copy.deepcopy(self.tree)
        del tree[row["name"]]
        report = bd.check(self.fx, tree)
        self.assertIn(row["name"], report["missing"])
        self.assertTrue(bd.violations(report))

    def test_a_decomposed_row_cannot_be_hollowed_out(self):
        """Losing a split's receiver reddens even though the parent is intact."""
        row = [r for r in self.fx["rows"]
               if r["adjudication"] == "decomposed"][0]
        ref = row["absorbs"][0]
        rel, _, absorber = ref.partition("::")
        tree = copy.deepcopy(self.tree)
        tree[absorber] = [(r, b) for r, b in tree[absorber] if r != rel]
        report = bd.check(tree and self.fx, tree)
        self.assertIn("%s -> %s" % (row["name"], ref),
                      report["absorber_missing"])
        self.assertTrue(bd.violations(report))

    def test_absorbers_pay_only_at_their_declared_host(self):
        """A legacy handler's bulk may not pay for a lib split it never had."""
        row = [r for r in self.fx["rows"]
               if r["adjudication"] == "decomposed"][0]
        tree = copy.deepcopy(self.tree)
        tree[row["name"]] = [(rel, body) for rel, body in tree[row["name"]]
                             if rel != row["host"]]
        report = bd.check(self.fx, tree)
        # The column a violation lands in is decided by the row's WINNER, not by
        # the mutation. This asserted "donor_longer" outright, which held only
        # while the first decomposed row happened to be donor-winning; task 56
        # added seventeen rows and the first is now local-winning, so the test
        # failed on the row ordering rather than on the invariant. Assert the
        # column the row's own winner implies.
        column = "local_longer" if row["winner"] == "local" else "donor_longer"
        self._assert_named(report, column, row["name"])

    def test_a_superseded_row_still_ratchets(self):
        """`superseded` lowers the floor to the decision -- it does not remove it."""
        row = [r for r in self.fx["rows"]
               if r["adjudication"] == "superseded"][0]
        report = bd.check(self.fx, self._shrink(self.tree, row["name"]))
        self.assertTrue(bd.violations(report))
        self.assertTrue(any(hit["name"] == row["name"]
                            for hit in report["donor_longer"]))

    def test_a_deferred_row_still_ratchets(self):
        """A deferral floors the loss at today's value; it may not get worse."""
        row = [r for r in self.fx["rows"]
               if r["adjudication"] == "deferred"][0]
        report = bd.check(self.fx, self._shrink(self.tree, row["name"]))
        self.assertTrue(bd.violations(report))

    def test_a_deferred_row_is_reported_rather_than_hidden(self):
        report = bd.check(self.fx, self.tree)
        self.assertEqual(sorted(DEFERRED_NAMES), sorted(report["deferred"]))
        self.assertIn("get_cli", bd._render(report))


class NormalisationTest(unittest.TestCase):
    """The measure itself: what it must ignore, and what it must not."""

    def _body(self, src):
        return bd.body_text(ast.parse(src).body[0])

    def test_comments_blank_lines_and_layout_are_ignored(self):
        plain = "def f():\n    x = 1\n    return x\n"
        noisy = ("def f():\n\n    # a comment nobody should be able to fail on\n"
                 "    x = (\n        1\n    )\n\n    return x\n")
        self.assertEqual(self._body(plain), self._body(noisy))

    def test_quote_style_is_ignored(self):
        self.assertEqual(self._body("def f():\n    return 'a'\n"),
                         self._body('def f():\n    return "a"\n'))

    def test_a_deleted_statement_is_not_ignored(self):
        self.assertNotEqual(self._body("def f():\n    a()\n    b()\n"),
                            self._body("def f():\n    a()\n"))

    def test_the_docstring_is_part_of_the_measure(self):
        """A body may not shed its contract text invisibly."""
        with_doc = self._body('def f():\n    """The contract."""\n    return 1\n')
        without = self._body("def f():\n    return 1\n")
        self.assertNotEqual(with_doc, without)
        self.assertIn("The contract.", with_doc)

    def test_only_top_level_definitions_are_collected(self):
        src = ("def free():\n    pass\n\n\n"
               "class K:\n    def method(self):\n        pass\n")
        names = bd.collect_defs([("m.py", src)])
        self.assertIn("free", names)
        self.assertNotIn("method", names)

    def test_async_definitions_are_collected(self):
        names = bd.collect_defs([("m.py", "async def go():\n    pass\n")])
        self.assertIn("go", names)

    def test_the_representative_is_the_longest_body_and_ties_are_stable(self):
        sites = [("b.py", "aa"), ("a.py", "bb"), ("c.py", "z")]
        self.assertEqual("bb", bd.representative(sites))
        self.assertEqual("bb", bd.representative(list(reversed(sites))))


class CaptureScopeTest(unittest.TestCase):
    """The scope is load-bearing: get it wrong and the diff is against itself."""

    def test_the_local_side_excludes_the_lib(self):
        rels = {rel for rel, _ in bd.iter_sources(
            REPO, excludes=bd.LOCAL_EXCLUDE_PREFIXES)}
        self.assertFalse([r for r in rels if r.startswith("scripts/agentware_lib")])
        self.assertIn("scripts/agentware_legacy.py", rels)

    def test_the_merged_side_includes_the_lib_and_the_facade(self):
        rels = {rel for rel, _ in bd.iter_sources(REPO, extra=(bd.FACADE,))}
        self.assertIn("scripts/agentware", rels)
        self.assertTrue([r for r in rels if r.startswith("scripts/agentware_lib/")])

    def test_capture_is_deterministic(self):
        self.assertEqual(bd.merged_tree(REPO), bd.merged_tree(REPO))


if __name__ == "__main__":
    unittest.main()
