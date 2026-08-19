"""The SHRINK-ONLY LEGACY RATCHET suite (plan task 32, gate 1 of 3).

Five claims, deliberately separated:

  1. GREEN — the ratchet passes over the live tree, and passes STRICTLY: the
     fixture is tight against the monolith as it stands, not merely loose enough
     to be satisfied by anything.
  2. IT BITES — every mark reddens independently: a lowered line ceiling, a
     lowered byte ceiling, a def or handler that appeared in legacy, a promotion
     removed from the allowlist, and an allowlist member that is not a verb.
  3. --record CANNOT DISARM IT — the maintenance tool is where a ratchet dies.
     Recording must refuse to raise a ceiling, refuse to lower the floor, and
     refuse the interesting one: a handler departure that no NEWLY PORTED verb
     accounts for BY NAME. Everything in this class was found by breaking an
     earlier draft, and each test names the hole it closed.
  4. IT CANNOT BE GAMED — the marks must move only when code actually leaves.
     Reflowing the file, or relocating the whole module body into a class, must
     not read as a shrink.
  5. TERMINAL STATE — the monolith AND the strangler both go away at task 56.
     Each absence, alone and together, must produce a verdict rather than a
     traceback.

Every mutation runs against a SCRATCH tree, never the repo: the ratchet measures
whatever `--root` points at, so the mutations are real files rather than patched
functions, and a refactor that stopped reading the tree would fail these. The
scratch trees carry their own union fixture too, so the verb vocabulary a test
credits is the test's, not the repo's.

Stdlib only, hermetic, deterministic.
"""

from tests.gates._legacy_ratchet_support import (  # noqa: F401
    LEGACY_STUB, _ScratchCase, _lib_def_names, _write, json, lr, unittest)


class LiveTreeTests(unittest.TestCase):

    def test_gate_passes_over_the_repo(self):
        self.assertEqual(0, lr.check())

    def test_gate_passes_STRICTLY(self):
        """Tight, not merely satisfied: no unrecorded slack in any mark."""
        self.assertEqual(0, lr.check(strict=True))

    def test_fixture_is_schema_valid(self):
        self.assertEqual([], lr.schema_errors(lr.load_fixture()))

    def test_marks_match_the_monolith_on_disk(self):
        current, high = lr.measure_legacy(), lr.load_fixture()["high_water"]
        for mark in lr.CEILINGS:
            self.assertEqual(current[mark], high[mark], mark)
        for mark in lr.NAME_SETS:
            self.assertEqual(sorted(current[mark]), sorted(high[mark]), mark)

    def test_floor_matches_the_live_allowlist(self):
        self.assertEqual(sorted(lr.measure_ported()),
                         sorted(lr.load_fixture()["floor"]["ported_verbs"]))

    def test_every_recorded_handler_maps_to_a_real_union_verb(self):
        """Otherwise the coupling rule could never be satisfied for it."""
        verbs = lr.union_verbs()
        orphans = [h for h in lr.load_fixture()["high_water"]["legacy_handlers"]
                   if lr.credited_by(h, verbs) is None]
        self.assertEqual([], orphans)

    #: The plan's named no-CLI-surface symbols — the "silent loss" class. Each has
    #: no verb, no flag and no distinguishing node id, so nothing but this check
    #: would notice it disappearing.
    SILENT_LOSS_CLASS = (
        "_apply_skill_pin",     # the 0.85 stage-1 skill pin
        "_freshness_fields",    # the read-time trust surface
        "trust_signals",
        "staleness_factor",     # the supersede/staleness downweight
        "scan_worklog",         # the R-SI-03 marker scanner
    )

    def test_the_silent_loss_class_helpers_still_EXIST_somewhere(self):
        """Each named symbol must be in the monolith OR ported into the lib.

        `high_water.legacy_defs` tracks the CURRENT monolith and only shrinks, so
        a symbol correctly leaves it the moment it is ported — asserting presence
        there would turn a successful port into a failure, and (worse) would
        push the next person to satisfy the check by NOT porting it.

        The guarantee that actually matters is that the symbol did not VANISH.
        So: still in the monolith, or resolvable in the lib. Both are fine; one
        of them must hold.
        """
        defs = set(lr.load_fixture()["high_water"]["legacy_defs"])
        lib_names = _lib_def_names()
        for name in self.SILENT_LOSS_CLASS:
            self.assertTrue(
                name in defs or name in lib_names,
                "%r is in NEITHER the monolith nor the lib — a silent-loss-class "
                "symbol disappeared, which is exactly what this ratchet exists "
                "to catch" % name)

class RatchetBitesTests(_ScratchCase):

    def test_green_on_an_untouched_scratch_tree(self):
        tree = self.tree()
        tree.seed_fixture()
        self.assertEqual(0, tree.check(strict=True))

    def test_LOWERING_the_line_high_water_reddens(self):
        """The plan's named probe: lower the mark, the ratchet must redden."""
        tree = self.tree()
        seeded = tree.seed_fixture()
        tree.seed_fixture(high_water={
            "legacy_lines": seeded["high_water"]["legacy_lines"] - 1})
        self.assertEqual(1, tree.check())

    def test_lowering_the_byte_high_water_reddens(self):
        tree = self.tree()
        seeded = tree.seed_fixture()
        tree.seed_fixture(high_water={
            "legacy_bytes": seeded["high_water"]["legacy_bytes"] - 1})
        self.assertEqual(1, tree.check())

    def test_a_handler_ADDED_to_legacy_reddens_by_name(self):
        tree = self.tree()
        tree.seed_fixture()
        tree.set_legacy(LEGACY_STUB + "\n\ndef cmd_gamma():\n    return 0\n")
        self.assertTrue(any("cmd_gamma" in v for v in tree.violations()))
        self.assertEqual(1, tree.check())

    def test_a_plain_DEF_added_to_legacy_reddens_by_name(self):
        """Not just handlers: the silent-loss classes are ordinary helpers."""
        tree = self.tree()
        tree.seed_fixture()
        tree.set_legacy(LEGACY_STUB + "\n\ndef _new_helper():\n    return 0\n")
        self.assertTrue(any("_new_helper" in v for v in tree.violations()))
        self.assertEqual(1, tree.check())

    def test_a_handler_RENAMED_inside_legacy_reddens(self):
        """Count-neutral: one out, one in. A count ratchet would pass this."""
        tree = self.tree()
        tree.seed_fixture()
        tree.set_legacy(LEGACY_STUB.replace("cmd_alpha", "cmd_gamma"))
        current = lr.measure_legacy(tree.legacy_path)
        self.assertEqual(2, len(current["legacy_handlers"]))
        self.assertEqual(1, tree.check())

    def test_a_reverted_promotion_reddens_naming_the_verb(self):
        tree = self.tree(ported=("alpha", "beta"))
        tree.seed_fixture()
        tree.set_ported(["alpha"])
        self.assertTrue(any("'beta'" in v for v in tree.violations()))
        self.assertEqual(1, tree.check())

    def test_an_allowlist_member_that_is_NOT_A_VERB_reddens(self):
        """A bogus or misspelled token is never routed and never evaluated."""
        tree = self.tree()
        tree.seed_fixture()
        tree.set_ported(["alpha", "not-a-verb"])
        self.assertTrue(any("not-a-verb" in v for v in tree.violations()))
        self.assertEqual(1, tree.check())

    def test_slack_is_a_warning_by_default_and_a_failure_under_strict(self):
        tree = self.tree()
        tree.seed_fixture()
        tree.set_legacy(LEGACY_STUB.replace(
            "def cmd_beta_sub():\n    return 0\n\n\n", ""))
        self.assertEqual(0, tree.check())
        self.assertEqual(1, tree.check(strict=True))

    def test_a_broken_fixture_reddens_rather_than_raising(self):
        tree = self.tree()
        tree.seed_fixture(schema="agentware/legacy-ratchet@99")
        self.assertEqual(1, tree.check())

    def test_a_fixture_missing_the_floor_reddens(self):
        tree = self.tree()
        data = tree.seed_fixture()
        del data["floor"]
        _write(tree.fixture_path, json.dumps(data))
        self.assertEqual(1, tree.check())

    def test_a_fixture_whose_name_set_became_an_int_reddens(self):
        tree = self.tree()
        tree.seed_fixture(high_water={"legacy_defs": 3})
        self.assertEqual(1, tree.check())

class CannotBeGamedTests(_ScratchCase):

    def test_RELOCATING_the_body_into_a_class_is_not_a_shrink(self):
        """`tree.body` alone would report the whole module as having left."""
        tree = self.tree()
        tree.seed_fixture()
        tree.set_legacy('"""scratch monolith"""\n\n\nclass _Legacy:\n'
                        '    def cmd_alpha(self):\n        return 0\n\n'
                        '    def cmd_beta_sub(self):\n        return 0\n\n'
                        '    def helper(self):\n        return 0\n')
        current = lr.measure_legacy(tree.legacy_path)
        self.assertEqual(["cmd_alpha", "cmd_beta_sub"],
                         current["legacy_handlers"])
        self.assertEqual(1, tree.record())

    def test_REFLOWING_the_file_is_not_a_shrink(self):
        """Lines fall, bytes do not: the byte ceiling is what holds."""
        tree = self.tree()
        seeded = tree.seed_fixture()
        tree.set_legacy(LEGACY_STUB.replace("\n\n\n", "\n"))
        current = lr.measure_legacy(tree.legacy_path)
        self.assertLess(current["legacy_lines"],
                        seeded["high_water"]["legacy_lines"])
        self.assertEqual(current["legacy_bytes"],
                         seeded["high_water"]["legacy_bytes"])

class CliTests(unittest.TestCase):

    def test_main_check_is_green(self):
        self.assertEqual(0, lr.main([]))

    def test_main_strict_is_green(self):
        self.assertEqual(0, lr.main(["--strict"]))
