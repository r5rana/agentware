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

import json
import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests import _legacy_ratchet as lr
except ImportError:  # allow `python3 -m unittest tests.test_legacy_ratchet`
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _legacy_ratchet as lr  # type: ignore

REPO = lr.REPO_ROOT

LEGACY_STUB = '''"""scratch monolith"""


def cmd_alpha():
    return 0


def cmd_beta_sub():
    return 0


def helper():
    return 0
'''

STRANGLER_STUB = "PORTED_VERBS = frozenset(%r)\n"

SCRATCH_VERBS = ["alpha", "beta", "gamma", "two-words"]


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class _ScratchTree:
    """A minimal tree the ratchet can measure: a monolith and an allowlist."""

    def __init__(self, root, legacy=LEGACY_STUB, ported=(), verbs=None):
        self.root = root
        self.legacy_path = os.path.join(root, "scripts", "agentware_legacy.py")
        self.fixture_path = os.path.join(root, "legacy-ratchet.json")
        self.union_path = os.path.join(root, "union.json")
        self.lib_dir = os.path.join(root, "scripts", "agentware_lib")
        if legacy is not None:
            _write(self.legacy_path, legacy)
        _write(os.path.join(self.lib_dir, "__init__.py"), "")
        self.set_ported(ported)
        _write(self.union_path, json.dumps(
            {"top_level_verbs": list(SCRATCH_VERBS if verbs is None else verbs)}))

    # -- mutations ---------------------------------------------------------

    def set_legacy(self, text):
        if text is None:
            os.remove(self.legacy_path)
        else:
            _write(self.legacy_path, text)

    def set_ported(self, verbs):
        _write(os.path.join(self.lib_dir, "strangler.py"),
               STRANGLER_STUB % (sorted(verbs),))

    def drop_strangler(self):
        os.remove(os.path.join(self.lib_dir, "strangler.py"))

    # -- fixture -----------------------------------------------------------

    def seed_fixture(self, **overrides):
        current = lr.measure_legacy(self.legacy_path)
        data = {
            "schema": lr.SCHEMA,
            "high_water": {m: current[m] for m in lr.CEILINGS},
            "floor": {"ported_verbs": list(lr.measure_ported(self.root))},
        }
        for mark in lr.NAME_SETS:
            data["high_water"][mark] = list(current[mark])
        data["high_water"].update(overrides.pop("high_water", {}))
        data["floor"].update(overrides.pop("floor", {}))
        data.update(overrides)
        _write(self.fixture_path, json.dumps(data))
        return data

    def read_fixture(self):
        with open(self.fixture_path, encoding="utf-8") as fh:
            return json.load(fh)

    # -- gate --------------------------------------------------------------

    def check(self, strict=False):
        return lr.check(self.fixture_path, self.root, strict, self.union_path)

    def record(self):
        return lr.record(self.fixture_path, self.root, self.union_path)

    def violations(self):
        return lr.compare(lr.measure_legacy(self.legacy_path),
                          lr.measure_ported(self.root), self.read_fixture(),
                          lr.union_verbs(self.union_path))


class _ScratchCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aw-ratchet-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def tree(self, **kw):
        return _ScratchTree(self.dir, **kw)


# ---------------------------------------------------------------------------
# 1. GREEN — the live tree
# ---------------------------------------------------------------------------

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

    def test_the_silent_loss_class_helpers_are_actually_recorded(self):
        """The plan's named no-CLI-surface symbols must be under the ratchet."""
        defs = set(lr.load_fixture()["high_water"]["legacy_defs"])
        for name in ("_apply_skill_pin",     # the 0.85 stage-1 skill pin
                     "_freshness_fields",    # the read-time trust surface
                     "trust_signals",
                     "staleness_factor",     # the supersede/staleness downweight
                     "scan_worklog"):        # the R-SI-03 marker scanner
            self.assertIn(name, defs)


# ---------------------------------------------------------------------------
# 2. IT BITES — every mark, independently
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 3. --record CANNOT DISARM IT
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 4. IT CANNOT BE GAMED
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 5. TERMINAL STATE — the monolith and the strangler are gone (task 56)
# ---------------------------------------------------------------------------

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


class CliTests(unittest.TestCase):

    def test_main_check_is_green(self):
        self.assertEqual(0, lr.main([]))

    def test_main_strict_is_green(self):
        self.assertEqual(0, lr.main(["--strict"]))


if __name__ == "__main__":
    unittest.main()
