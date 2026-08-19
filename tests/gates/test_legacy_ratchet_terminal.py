"""TerminalStateTests -- split from `test_legacy_ratchet` (task 58).

See `tests.gates._legacy_ratchet_support` for the
shared header.
"""

from tests.gates._legacy_ratchet_support import (  # noqa: F401
    LEGACY_STUB, _ScratchCase, lr)


class TerminalStateTests(_ScratchCase):

    def test_an_absent_monolith_measures_zero(self):
        tree = self.tree()
        tree.set_legacy(None)
        self.assertEqual({"legacy_lines": 0, "legacy_bytes": 0,
                          "legacy_defs": [], "legacy_handlers": []},
                         lr.measure_legacy(tree.legacy_path))

    def test_an_absent_strangler_measures_an_empty_allowlist(self):
        tree = self.tree(ported=("alpha",))
        tree.drop_strangler()
        self.assertEqual([], lr.measure_ported(tree.root))

    def test_BOTH_absent_reddens_only_on_the_FLOOR_never_on_a_traceback(self):
        """Half of task 56's end state: the strangler is gone with the marks."""
        tree = self.tree(ported=("alpha", "beta"))
        tree.seed_fixture()
        tree.set_legacy(None)
        tree.drop_strangler()
        self.assertEqual(1, tree.check())       # the floor lost alpha and beta
        self.assertTrue(all("left strangler.PORTED_VERBS" in v
                            for v in tree.violations()), tree.violations())

    def test_BOTH_absent_with_an_empty_floor_is_GREEN(self):
        tree = self.tree()
        tree.seed_fixture()
        tree.set_legacy(None)
        tree.drop_strangler()
        self.assertEqual(0, tree.check())

    def test_an_absent_monolith_is_GREEN_not_a_crash(self):
        tree = self.tree()
        tree.seed_fixture()
        tree.set_legacy(None)
        tree.set_ported(["alpha", "beta"])
        self.assertEqual(0, tree.check())

    def test_deleting_the_monolith_still_requires_the_ports(self):
        """Deletion is not a licence: the handlers still had to go somewhere."""
        tree = self.tree()
        tree.seed_fixture()
        tree.set_legacy(None)
        self.assertEqual(1, tree.record())

    # -- the TERMINAL record (task 92) ------------------------------------
    #
    # The incremental coupling rule asks which verb was appended to the
    # allowlist IN THIS COMMIT to pay for a handler's departure. The whole-file
    # deletion cannot answer it: the handlers still standing at the end are
    # precisely the ones whose verbs were promoted in an EARLIER commit and
    # whose dead bodies were never swept, so the delta is empty by construction
    # and the terminal state was unreachable — the gate could only ever refuse.
    # The claim is now asked of the LIVE allowlist instead, which is the same
    # claim (no handler leaves without a port to serve its verb) minus the
    # same-commit clause that the last commit cannot satisfy.

    def test_the_TERMINAL_deletion_RECORDS_when_every_handler_is_ALREADY_ported(self):
        """The exact shape that was unreachable: ports landed in EARLIER commits.

        alpha and beta are in the floor, so `newly ported this commit` is empty.
        Under the incremental rule alone this refuses forever; it is the state
        the real tree reached at task 54 with cmd_mcp and cmd_plan_state_backfill.
        """
        tree = self.tree(ported=("alpha", "beta"))
        tree.seed_fixture()
        self.assertEqual(["alpha", "beta"],
                         tree.read_fixture()["floor"]["ported_verbs"])
        tree.set_legacy(None)
        self.assertEqual(0, tree.record())
        high = tree.read_fixture()["high_water"]
        self.assertEqual(0, high["legacy_lines"])
        self.assertEqual(0, high["legacy_bytes"])
        self.assertEqual([], high["legacy_defs"])
        self.assertEqual([], high["legacy_handlers"])
        self.assertEqual(0, tree.check(strict=True))

    def test_the_TERMINAL_deletion_is_REFUSED_when_a_handler_verb_is_UNPORTED(self):
        """Relaxing the same-commit clause must not relax the CAPABILITY claim."""
        tree = self.tree(ported=("alpha",))
        tree.seed_fixture()
        tree.set_legacy(None)
        self.assertEqual(1, tree.record())
        problems = lr.record_errors(lr.measure_legacy(tree.legacy_path),
                                    lr.measure_ported(tree.root),
                                    tree.read_fixture(),
                                    lr.union_verbs(tree.union_path))
        self.assertTrue(any("cmd_beta_sub" in p for p in problems), problems)
        self.assertFalse(any("cmd_alpha" in p for p in problems), problems)
        # and the marks did not move
        self.assertNotEqual(0, tree.read_fixture()["high_water"]["legacy_lines"])

    def test_the_TERMINAL_refusal_is_DISTINGUISHABLE_from_the_incremental_one(self):
        """Two different diagnoses must not print the same sentence."""
        tree = self.tree(ported=("alpha",))
        tree.seed_fixture()
        tree.set_legacy(None)
        terminal = lr.record_errors(lr.measure_legacy(tree.legacy_path),
                                    lr.measure_ported(tree.root),
                                    tree.read_fixture(),
                                    lr.union_verbs(tree.union_path))
        self.assertTrue(any("TERMINAL" in p for p in terminal), terminal)
        self.assertFalse(any("SECOND HALF OF A PORT" in p for p in terminal),
                         terminal)

    def test_a_SHRINK_is_not_terminal_and_still_takes_the_incremental_rule(self):
        """The terminal branch must not become a back door for a partial sweep.

        One def left standing is not the terminal state, so deleting cmd_alpha
        here still has to be paid for in-commit even though `alpha` is ported.
        """
        tree = self.tree(ported=("alpha",))
        tree.seed_fixture()
        tree.set_legacy(LEGACY_STUB.replace(
            "def cmd_alpha():\n    return 0\n\n\n", ""))
        self.assertFalse(lr.is_terminal(lr.measure_legacy(tree.legacy_path)))
        problems = lr.record_errors(lr.measure_legacy(tree.legacy_path),
                                    lr.measure_ported(tree.root),
                                    tree.read_fixture(),
                                    lr.union_verbs(tree.union_path))
        self.assertTrue(any("SECOND HALF OF A PORT" in p for p in problems),
                        problems)
        self.assertEqual(1, tree.record())

    def test_after_the_TERMINAL_record_a_RESURRECTED_monolith_REDDENS(self):
        """The terminal record must not disarm the ratchet it retires.

        Recording zeros is the moment the gate could quietly become vacuous —
        an empty recorded name set trivially contains nothing, so the question
        is whether the CEILINGS still bite. They do, on all four marks, and the
        resurrected handler is one the fixture used to allow.
        """
        tree = self.tree(ported=("alpha", "beta"))
        tree.seed_fixture()
        tree.set_legacy(None)
        self.assertEqual(0, tree.record())
        tree.set_legacy("def cmd_alpha():\n    return 0\n")
        self.assertEqual(1, tree.check())
        marks = " ".join(tree.violations())
        for expected in ("legacy_lines rose", "legacy_bytes rose",
                         "defs cmd_alpha", "handlers cmd_alpha"):
            self.assertIn(expected, marks)

    def test_is_terminal_is_MEASUREMENT_derived_not_a_filesystem_probe(self):
        """A monolith emptied to nothing is the terminal state too.

        `measure_legacy` already scores an absent file as zero-and-empty, so the
        terminal question is asked of the MARKS. That keeps one answer to "is
        there any legacy code left" and lets --root reach the branch in a
        scratch tree.
        """
        tree = self.tree()
        self.assertFalse(lr.is_terminal(lr.measure_legacy(tree.legacy_path)))
        tree.set_legacy("")
        self.assertTrue(lr.is_terminal(lr.measure_legacy(tree.legacy_path)))
        tree.set_legacy(None)
        self.assertTrue(lr.is_terminal(lr.measure_legacy(tree.legacy_path)))
