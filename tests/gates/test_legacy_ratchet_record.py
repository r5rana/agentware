"""RecordCannotDisarmTests -- split from `test_legacy_ratchet` (task 58).

See `tests.gates._legacy_ratchet_support` for the
shared header.
"""

from tests.gates._legacy_ratchet_support import (  # noqa: F401
    LEGACY_STUB, _ScratchCase, lr)


class RecordCannotDisarmTests(_ScratchCase):

    def test_record_refuses_to_RAISE_a_ceiling(self):
        tree = self.tree()
        seeded = tree.seed_fixture()
        tree.set_legacy(LEGACY_STUB + "\n\ndef helper2():\n    return 0\n")
        self.assertEqual(1, tree.record())
        self.assertEqual(seeded["high_water"]["legacy_bytes"],
                         tree.read_fixture()["high_water"]["legacy_bytes"])

    def test_record_refuses_to_LOWER_the_ported_floor(self):
        tree = self.tree(ported=("alpha",))
        tree.seed_fixture()
        tree.set_ported([])
        self.assertEqual(1, tree.record())
        self.assertEqual(["alpha"], tree.read_fixture()["floor"]["ported_verbs"])

    def test_record_refuses_an_UNCOUPLED_handler_shrink(self):
        """The base case: a handler vanishes and nothing was ported at all."""
        tree = self.tree()
        tree.seed_fixture()
        tree.set_legacy(LEGACY_STUB.replace(
            "def cmd_alpha():\n    return 0\n\n\n", ""))
        self.assertEqual(1, tree.record())
        self.assertIn("cmd_alpha",
                      tree.read_fixture()["high_water"]["legacy_handlers"])

    def test_record_refuses_an_ORPHAN_handler_alongside_a_real_port(self):
        """One port must not license deleting UNRELATED handlers.

        The hole this closes: a commit-level check ("did anything get ported?")
        is satisfied by a single append, and task 39 legitimately removes NINE
        `cmd_plan_*` handlers for one verb — so N-for-1 is the normal shape and
        an extra, unrelated deletion rides along invisibly. The verb stays
        registered in the parser, dispatch reaches a name that no longer exists,
        and the promotion gate sees an unchanged surface.
        """
        tree = self.tree()
        tree.seed_fixture()
        tree.set_legacy('"""scratch monolith"""\n\n\ndef helper():\n    return 0\n')
        tree.set_ported(["beta"])            # pays for cmd_beta_sub only
        self.assertEqual(1, tree.record())
        problems = lr.record_errors(lr.measure_legacy(tree.legacy_path),
                                    lr.measure_ported(tree.root),
                                    tree.read_fixture(),
                                    lr.union_verbs(tree.union_path))
        self.assertTrue(any("cmd_alpha" in p for p in problems), problems)
        self.assertFalse(any("cmd_beta_sub" in p for p in problems), problems)

    def test_record_refuses_a_shrink_paid_for_by_a_BOGUS_verb(self):
        """A token outside the union is never routed, so it buys nothing.

        The realistic shape is a typo in a task-37..54 append — `'fleet_emit'`
        for `fleet-emit` — which unlocks the deletion while the real verb stays
        on the legacy arm.
        """
        tree = self.tree()
        tree.seed_fixture()
        tree.set_legacy(LEGACY_STUB.replace(
            "def cmd_alpha():\n    return 0\n\n\n", ""))
        tree.set_ported(["alpha-typo"])
        self.assertEqual(1, tree.record())

    def test_there_is_no_flag_that_relaxes_the_coupling(self):
        """Behavioural, not a source grep: no accepted flag makes it pass.

        An earlier version of this test asserted four literal flag names were
        absent from the source, which a maintainer adding a fifth would pass
        unchanged. This drives the CLI instead: an unknown flag must be a hard
        argparse rejection, and the refusal must survive every flag the CLI
        does accept.
        """
        tree = self.tree()
        tree.seed_fixture()
        tree.set_legacy(LEGACY_STUB.replace(
            "def cmd_alpha():\n    return 0\n\n\n", ""))
        base = ["--fixture", tree.fixture_path, "--root", tree.root,
                "--union", tree.union_path]
        self.assertEqual(1, lr.main(base + ["--record"]))
        self.assertEqual(1, lr.main(base + ["--record", "--strict"]))
        for unknown in ("--allow-uncoupled", "--force", "--no-coupling",
                        "--skip-coupling", "--relax", "--yes"):
            with self.subTest(flag=unknown):
                with self.assertRaises(SystemExit):
                    lr.main(base + ["--record", unknown])

    def test_record_ACCEPTS_a_shrink_paid_for_by_a_port(self):
        tree = self.tree()
        tree.seed_fixture()
        tree.set_legacy(LEGACY_STUB.replace(
            "def cmd_beta_sub():\n    return 0\n\n\n", ""))
        tree.set_ported(["beta"])
        self.assertEqual(0, tree.record())
        after = tree.read_fixture()
        self.assertNotIn("cmd_beta_sub", after["high_water"]["legacy_handlers"])
        self.assertEqual(["beta"], after["floor"]["ported_verbs"])
        self.assertEqual(0, tree.check(strict=True))

    def test_a_hyphenated_verb_credits_its_underscored_handler(self):
        tree = self.tree(legacy=LEGACY_STUB.replace(
            "cmd_beta_sub", "cmd_two_words_do"))
        tree.seed_fixture()
        self.assertEqual("two-words",
                         lr.credited_by("cmd_two_words_do", {"two-words"}))
        tree.set_legacy(LEGACY_STUB.replace(
            "cmd_beta_sub", "cmd_two_words_do").replace(
            "def cmd_two_words_do():\n    return 0\n\n\n", ""))
        tree.set_ported(["two-words"])
        self.assertEqual(0, tree.record())

    def test_a_non_handler_def_may_leave_without_a_port(self):
        """Helpers move under names that share no prefix with their verb.

        Deliberate and documented: the coupling binds handlers only. The
        departure is still RECORDED by name, so it is legible in the diff
        rather than invisible.
        """
        tree = self.tree()
        tree.seed_fixture()
        tree.set_legacy(LEGACY_STUB.replace(
            "def helper():\n    return 0\n", ""))
        self.assertEqual(0, tree.record())
        self.assertNotIn("helper",
                         tree.read_fixture()["high_water"]["legacy_defs"])

    def test_record_is_idempotent(self):
        tree = self.tree()
        tree.seed_fixture()
        self.assertEqual(0, tree.record())
        first = tree.read_fixture()
        self.assertEqual(0, tree.record())
        self.assertEqual(first, tree.read_fixture())

    def test_record_preserves_the_fixtures_prose_fields(self):
        tree = self.tree()
        tree.seed_fixture(notes="why this exists", retire_at_task=56)
        self.assertEqual(0, tree.record())
        after = tree.read_fixture()
        self.assertEqual("why this exists", after["notes"])
        self.assertEqual(56, after["retire_at_task"])
