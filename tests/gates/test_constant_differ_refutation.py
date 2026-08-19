"""ExhaustiveRefutationTest -- split from `test_constant_differ` (task 58).

See `tests.gates._constant_differ_support` for the
shared header.
"""

from tests.gates._constant_differ_support import (  # noqa: F401
    REPO, SENTINEL, TERMINAL, _named, adj, cd, cp, os, tempfile, unittest)


class ExhaustiveRefutationTest(unittest.TestCase):
    """Every one of the 265 committed rows must bite INDIVIDUALLY.

    Task 30 shipped a per-row gate in which 2 of 242 rows were inert, because
    the suite mutated one representative row instead of all of them. A gate is
    only proven for the rows you actually broke.
    """

    @classmethod
    def setUpClass(cls):
        cls.fx = cd.load_fixture()
        cls.legacy = cd.legacy_tree()
        cls.lib = cd.lib_tree()

    def _mutated(self, tree, name, literal):
        out = dict(tree)
        if name in tree:
            site = tree[name][0][0]
        else:
            # The name is TRACKED but no longer DEFINED in this tree, because a
            # port resolved it (row names are a ratchet — see _tracked). Synthesize
            # the definition at a real site in the same tree rather than skipping
            # the row: skipping would silently shrink the refutation set by one on
            # every successful port, which is the exact self-disarming this suite
            # exists to prevent. Synthesizing also asks the sharper question — if
            # someone RE-INTRODUCED a rival definition here after the port, would
            # the gate still answer?
            site = next(iter(tree.values()))[0][0]
        out[name] = [(site, literal)]
        return out

    def test_every_pending_row_reddens_when_its_monolith_value_is_edited(self):
        inert = []
        for row in self.fx["legacy_only"]:
            if row["status"] != "pending":
                continue
            legacy = self._mutated(self.legacy, row["name"], SENTINEL)
            report = cd.check(self.fx, legacy, self.lib, TERMINAL)
            if row["name"] not in _named(report, "legacy_drift"):
                inert.append(row["name"])
        self.assertEqual(inert, [], "rows the gate does not actually police")

    def test_every_pending_row_reddens_when_it_is_deleted(self):
        inert = []
        for row in self.fx["legacy_only"]:
            if row["status"] != "pending":
                continue
            legacy = {k: v for k, v in self.legacy.items() if k != row["name"]}
            if row["name"] not in _named(
                    cd.check(self.fx, legacy, self.lib, TERMINAL), "lost"):
                inert.append(row["name"])
        self.assertEqual(inert, [])

    def test_every_pending_row_reddens_when_ported_with_a_wrong_literal(self):
        inert = []
        for row in self.fx["legacy_only"]:
            if row["status"] != "pending":
                continue
            lib = dict(self.lib)
            lib[row["name"]] = [("scripts/agentware_lib/x.py", SENTINEL)]
            report = cd.check(self.fx, self.legacy, lib, TERMINAL)
            if row["name"] not in _named(report, "literal_mismatch"):
                inert.append(row["name"])
        self.assertEqual(inert, [])

    def test_every_divergent_row_reddens_when_the_lib_value_changes(self):
        inert = []
        for row in self.fx["divergent"]:
            lib = self._mutated(self.lib, row["name"], SENTINEL)
            report = cd.check(self.fx, self.legacy, lib, TERMINAL)
            if row["name"] not in _named(report, "decision_mismatch"):
                inert.append(row["name"])
        self.assertEqual(inert, [])

    def test_every_divergent_row_reddens_when_the_monolith_value_changes(self):
        # `lib`-decided rows are the exception BY DESIGN: the monolith's value
        # is the one being replaced, so editing it is not a regression. Assert
        # that the exception set is exactly the decisions that license it,
        # rather than letting rows drop out silently.
        if TERMINAL:
            # There is no monolith value left to edit, so this refutation has no
            # subject at all. Assert THAT -- the whole file is gone, not one row
            # missing from a capture -- so the disappearance is attributable, and
            # let the sibling lib-side refutation carry the policing (it bites
            # all 17 rows in BOTH states, which is the point of the narrowing in
            # `cd.check`).
            self.assertEqual(self.legacy, {})
            self.assertFalse(os.path.exists(os.path.join(REPO, cd.LEGACY_REL)))
            return
        unpoliced = []
        for row in self.fx["divergent"]:
            legacy = self._mutated(self.legacy, row["name"], SENTINEL)
            report = cd.check(self.fx, legacy, self.lib, TERMINAL)
            if row["name"] not in _named(report, "legacy_drift"):
                unpoliced.append(row["name"])
        expected = sorted(r["name"] for r in self.fx["divergent"]
                          if r["decision"] == "lib")
        self.assertEqual(sorted(unpoliced), expected)

    def test_no_row_is_satisfied_by_an_empty_tree(self):
        """Non-vacuity: EVERY row reddens against empty trees.

        Stated as "everything is lost", with no exception carved out. The
        terminal state suspends the monolith-SIDE obligations, not rows: an
        `equivalent` row whose lib side has vanished is still a loss whether or
        not the monolith is there to compare it to, and the earlier form of this
        test (total accounting, six rows discharged) was written around a guard
        that discharged the row wholesale.

        Passing `terminal=False` here is deliberate and is the second half of the
        claim: these empty trees stand for a monolith that was EMPTIED, not one
        that was deleted, and that must never read as the terminal state.
        """
        report = cd.check(self.fx, {}, {}, terminal=False)
        total = len(self.fx["legacy_only"]) + len(self.fx["divergent"])
        self.assertEqual(len(_named(report, "lost")), total)
        # And the loss is reported in the terminal state too, where only the
        # monolith side is excused: the lib side going empty is still a loss.
        terminal = cd.check(self.fx, {}, {}, terminal=True)
        self.assertEqual(len(_named(terminal, "lost")), total)
        # ...while the tree as it SHIPS is green in whichever state it is in, so
        # the guard cannot be used to silence a real loss.
        live = cd.check(self.fx, cd.legacy_tree(), cd.lib_tree(), TERMINAL)
        self.assertEqual(set(), _named(live, "lost"))

    def test_an_emptied_monolith_is_not_the_terminal_state(self):
        """A monolith that still STANDS but holds nothing must redden.

        Measured before the fix, with the terminal state inferred from the
        capture: truncating `scripts/agentware_legacy.py` to zero bytes scored
        `OK - no constant lost, drifted or overruled`, rc=0 — every one of the
        227 monolith constants gone, reported as success by the gate whose only
        job is to notice that. The two states are told apart by the FILE now, so
        the same capture reddens when the file is still there.
        """
        emptied = cd.check(self.fx, {}, self.lib, terminal=False)
        self.assertTrue(cd.violations(emptied),
                        "an emptied monolith reported nothing")
        self.assertTrue(_named(emptied, "lost") or _named(emptied, "multi_literal"))

    def test_the_terminal_state_is_keyed_on_the_file_not_on_the_capture(self):
        self.assertEqual(cd.legacy_absent(REPO),
                         not os.path.isfile(os.path.join(REPO, cd.LEGACY_REL)))
        with tempfile.TemporaryDirectory(prefix="aw-const-term-") as tmp:
            os.makedirs(os.path.join(tmp, "scripts"))
            self.assertTrue(cd.legacy_absent(tmp))
            open(os.path.join(tmp, cd.LEGACY_REL), "w").close()   # empty, present
            self.assertFalse(cd.legacy_absent(tmp))

    def test_the_lib_side_of_an_equivalence_is_policed_in_BOTH_states(self):
        """The narrowing that motivated it: terminal excuses the monolith only.

        The first terminal guard skipped an `equivalent` row entirely, which
        freed all six decided lib values the moment the monolith went — measured
        red with the monolith present and SILENT without it.
        """
        equivalences = [r for r in self.fx["divergent"]
                        if r.get("decision") == "equivalent"]
        self.assertTrue(equivalences)
        for row in equivalences:
            name = row["name"]
            lib = self._mutated(self.lib, name, SENTINEL)
            for terminal in (True, False):
                # The non-terminal leg needs a monolith side to be non-terminal
                # ABOUT; supply the pinned spelling when the real one is already
                # gone, so this test asks the same question in both checkouts.
                legacy = dict(self.legacy)
                if not terminal:
                    legacy.setdefault(name, [(cd.LEGACY_REL.replace(os.sep, "/"),
                                              row["legacy_literal"])])
                report = cd.check(self.fx, legacy, lib, terminal)
                self.assertIn(name, _named(report, "decision_mismatch"),
                              "equivalence unpoliced with terminal=%s" % terminal)

    def test_the_lib_side_of_a_multi_definition_is_policed_in_BOTH_states(self):
        """Same narrowing, second surface: a lib constant may not shed a spelling."""
        pinned = {n: v for n, v in self.fx["multi_literal"].items() if v[1] > 1}
        self.assertTrue(pinned, "no lib-side multi-definition row to prove")
        for name in pinned:
            lib = dict(self.lib)
            lib[name] = [("scripts/agentware_lib/x.py", "1")]   # collapsed to one
            for terminal in (True, False):
                report = cd.check(self.fx, self.legacy, lib, terminal)
                self.assertIn(name, _named(report, "multi_literal"),
                              "multi row unpoliced with terminal=%s" % terminal)
