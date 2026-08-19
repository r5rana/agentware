"""CopyOverCollisionTest, DeterminismTest -- split from `test_preserved_gates` (task 58).

See `tests.gates._preserved_gates_support` for the
shared header.
"""

from tests.gates._preserved_gates_support import (  # noqa: F401
    COLLISION_DESTINATIONS, DONOR, FIXTURE, RELAYERING_COLLISIONS,
    RELOCATIONS, REPO, collect_node_ids, derive, diff_node_ids,
    donor_available, json, load_fixture, load_node_ids, os, relocate,
    resolves, shutil, tempfile, unittest, verify)


@unittest.skipUnless(donor_available(), "donor tree not staged")
class CopyOverCollisionTest(unittest.TestCase):
    """THE acceptance proof: copying the donor test file over the local path
    must redden the local frozen set, naming every destroyed test."""

    #: The path the donor file is copied ONTO -- the local side's pinned
    #: destination, which after task 58 is where the local file actually lives.
    #: Before task 58 both trees carried this file FLAT at the same path, so the
    #: hazard needed no construction; now it does, and constructing it is the
    #: point: the proof has to be that a blanket port onto the PIN still
    #: destroys 30 tests and the freeze still names every one.
    TARGET = COLLISION_DESTINATIONS["tests/test_skill_promote.py"]["local"]
    PREFIX = "tests.unit.features.test_skill_promote."

    #: How many of the frozen module's 30 ids still live AT the pin after the
    #: task-58 split; the other 10 moved to `test_skill_promote_cluster`.
    AT_THE_PIN = 20

    @classmethod
    def _mirror(cls, src, dst, rel, substitute):
        """Symlink-farm `src` into `dst`, with `rel` pointing at `substitute`.

        Every directory ON the `rel` path is made REAL and its siblings
        symlinked, so the substitution can never write through a symlink into
        the tree being mirrored -- which is not a hypothetical: the first
        version of this helper symlinked `tests/` wholesale and created the
        substitute inside the live repo.

        `rel` need not exist in `src`: a copy that ADDS a file is the same
        operation as one that replaces it.
        """
        os.makedirs(dst, exist_ok=True)
        head, _, tail = rel.partition("/")
        for name in sorted(os.listdir(src)):
            if name in ("__pycache__", ".git"):
                continue
            target = os.path.join(dst, name)
            if name != head:
                os.symlink(os.path.join(src, name), target)
            elif tail:
                cls._mirror(os.path.join(src, name), target, tail, substitute)
            else:
                os.symlink(substitute, target)
        if head and not os.path.lexists(os.path.join(dst, head)):
            if tail:
                os.makedirs(os.path.join(dst, head))
                cls._mirror(os.path.join(src, head) if os.path.isdir(
                    os.path.join(src, head)) else dst,
                    os.path.join(dst, head), tail, substitute)
            else:
                os.symlink(substitute, os.path.join(dst, head))

    @classmethod
    def setUpClass(cls):
        donor_file = os.path.join(DONOR, "tests", "test_skill_promote.py")
        # (1) the blanket copy onto the LOCAL pin -- destroys 30 local tests.
        cls.root = tempfile.mkdtemp(prefix="aw-collide-")
        cls._mirror(REPO, cls.root, cls.TARGET, donor_file)
        cls.live, cls.errors = collect_node_ids(cls.root)
        # (2) the donor file at the FLAT path both trees carried at freeze time.
        # Kept as a separate tree on purpose: it is the scenario the frozen
        # donor ids were captured from, so it is the only one in which "a
        # pending donor id now resolves" can be asserted at all. Merging the two
        # into one tree would silently retire that half of the proof.
        cls.flat_root = tempfile.mkdtemp(prefix="aw-collide-flat-")
        cls._mirror(REPO, cls.flat_root, "tests/test_skill_promote.py",
                    donor_file)
        cls.flat_live, cls.flat_errors = collect_node_ids(cls.flat_root)
        cls.base, _ = collect_node_ids(REPO)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)
        shutil.rmtree(cls.flat_root, ignore_errors=True)

    def test_the_copy_itself_collects_cleanly(self):
        """The donor file IMPORTS fine here -- the loss is silent, not a crash.

        Measured as a DELTA against the live tree rather than an absolute
        count, so later tasks adding tests cannot redden this proof.
        """
        self.assertEqual(self.errors, [])
        base = {n for n in self.base if not n.startswith(self.PREFIX)}
        self.assertEqual(len(self.base) - len(base), self.AT_THE_PIN)
        self.assertEqual(len(set(self.live) - base), 8)
        self.assertEqual(len(base - set(self.live)), 0)

    def test_the_frozen_local_set_reddens_naming_every_destroyed_test(self):
        """Every test the copy destroys is named, and only those.

        The frozen module owned 30 ids. Task 58 split it in two -- 20 stay at
        the pinned destination, 10 move to `test_skill_promote_cluster` -- so a
        copy onto the pin destroys 20 and the other 10 keep collecting at their
        recorded successor. Both halves are asserted: 20 LOST, 10 still
        resolving, and 20 + 10 back to the frozen 30. Asserting only "20 LOST"
        would also pass if the split had silently dropped the other 10.
        """
        fx = load_fixture()
        out = diff_node_ids(load_node_ids(FIXTURE, "local")["node_ids"],
                            load_node_ids(FIXTURE, "donor")["node_ids"],
                            fx["node_ids"]["pending_in_destination"], self.live)
        lost = [v for v in out if v.startswith("LOST node id:")]
        self.assertEqual(len(lost), self.AT_THE_PIN)
        for v in lost:
            self.assertIn("tests.test_skill_promote.", v)
        frozen = [n for n in load_node_ids(FIXTURE, "local")["node_ids"]
                  if n.startswith("tests.test_skill_promote.")]
        self.assertEqual(len(frozen), 30)
        survived = [n for n in frozen if resolves(n, set(self.live))]
        self.assertEqual(len(survived), 30 - self.AT_THE_PIN)
        for nid in survived:
            self.assertTrue(any(
                s.startswith("tests.unit.features.test_skill_promote_cluster.")
                for s in relocate(nid) if s in set(self.live)), nid)

    def test_the_incoming_donor_tests_trip_the_ratchet_too(self):
        """The donor file at the FLAT path both trees carried at freeze time.

        This is the tree the frozen donor ids were captured from, so its 8 ids
        resolve exactly and the shrink-only ratchet fires: "pending node id now
        resolves, drop it". Task 58 did not weaken this -- it moved the LOCAL
        file out of the way, so the scenario has to be built rather than found,
        and it is built here rather than retired.
        """
        fx = load_fixture()
        self.assertEqual(self.flat_errors, [])
        out = diff_node_ids([], load_node_ids(FIXTURE, "donor")["node_ids"],
                            fx["node_ids"]["pending_in_destination"],
                            self.flat_live)
        self.assertEqual(len([v for v in out if "RATCHET" in v]), 8)

    def test_a_copy_onto_the_local_pin_does_NOT_count_as_landed(self):
        """A copy-over is not a port, and the ratchet must not read it as one.

        Before task 58 both trees carried this file FLAT at one path, so copying
        the donor version over it landed all 8 donor tests at exactly their
        frozen ids and tripped the ratchet ("pending id now resolves, drop it").
        Layering moved the local side to its pinned destination, so the same
        blanket copy now lands those 8 under the LOCAL module path instead --
        `tests.unit.features.test_skill_promote.*`, not the frozen
        `tests.test_skill_promote.*` -- and not one frozen donor id resolves.

        Both halves are asserted, because the absence alone would also be
        satisfied by the copy failing to import: the 8 tests ARE collected,
        under a name that is not theirs. That is the pin doing its job -- the
        donor ledger is pinned to `test_skill_suggest.py`, and until a port puts
        it there the backlog correctly still lists all 8 as pending.
        """
        fx = load_fixture()
        out = diff_node_ids([], load_node_ids(FIXTURE, "donor")["node_ids"],
                            fx["node_ids"]["pending_in_destination"], self.live)
        self.assertEqual([v for v in out if "RATCHET" in v], [])
        arrived = {n for n in self.live if n.startswith(self.PREFIX)}
        self.assertEqual(len(arrived), 8)
        frozen_donor = {n for n in load_node_ids(FIXTURE, "donor")["node_ids"]
                        if n.startswith("tests.test_skill_promote.")}
        self.assertEqual(len(frozen_donor), 8)
        self.assertEqual(frozen_donor & set(self.live), set())

    def test_no_filename_check_could_have_seen_this(self):
        """The loss is invisible to filenames -- before AND after task 58.

        At freeze time both trees carried `tests/test_skill_promote.py`, so a
        same-path copy was a same-NAME copy. Layering moved the local side to
        its pinned destination, which does not make the hazard go away: the
        donor file still carries the same basename, so a port that puts it
        where the pin says the LOCAL file goes reads as a file arriving at its
        obvious home. Both spellings are asserted, because the pin is only
        meaningful while the donor file it re-homes still exists.
        """
        self.assertTrue(os.path.exists(os.path.join(REPO, self.TARGET)))
        self.assertTrue(os.path.exists(os.path.join(DONOR, "tests/test_skill_promote.py")))
        self.assertFalse(os.path.exists(os.path.join(REPO, "tests/test_skill_promote.py")))
        self.assertEqual(os.path.basename(self.TARGET), "test_skill_promote.py")

@unittest.skipUnless(donor_available(), "donor tree not staged")
class DeterminismTest(unittest.TestCase):

    """Determinism is asserted against the FROZEN input (the read-only donor
    tree) and as a superset against the live one, so a later task adding a test
    cannot make a determinism check look like a determinism failure."""

    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="aw-redrv-")
        cls.path = os.path.join(cls.out, "preserved-surface.json")
        cls.fresh = derive(REPO, DONOR, cls.path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_the_donor_half_reproduces_byte_for_byte(self):
        with open(os.path.join(self.out, "nodeids-donor.json")) as fh:
            self.assertEqual(json.load(fh), load_node_ids(FIXTURE, "donor"))

    def test_the_local_half_is_a_superset_of_the_frozen_one(self):
        """Superset THROUGH the task-58 ledger -- see `LiveGateTest`."""
        with open(os.path.join(self.out, "nodeids-local.json")) as fh:
            fresh = set(json.load(fh)["node_ids"])
        frozen = set(load_node_ids(FIXTURE, "local")["node_ids"])
        self.assertEqual(sorted(n for n in frozen if not resolves(n, fresh)), [])

    def test_every_non_node_id_section_reproduces_exactly(self):
        committed = load_fixture()
        for section in ("executables", "templates", "catalogs",
                        "fixture_families", "assets"):
            self.assertEqual(self.fresh[section], committed[section], section)

    def test_the_pinned_collision_destinations_reproduce_exactly(self):
        """A pinned row that STILL collides must reproduce its pins exactly.

        Task 58 resolved the flat-path hazards by construction: with the local
        corpus layered, `tests/test_skill_promote.py` and the eleven other
        shared FLAT paths are no longer shared, so a fresh derivation has no row
        for them at all. That is the pin being honoured, not lost -- the local
        file now sits at the destination this map pinned for it -- so a row's
        absence is only acceptable when the local side actually moved away,
        which `test_the_flat_path_hazards_are_resolved_by_the_layering` asserts
        rather than assuming.
        """
        committed = load_fixture()["test_path_collisions"]["rows"]
        fresh = self.fresh["test_path_collisions"]["rows"]
        checked = 0
        for rel, row in committed.items():
            if rel in fresh:
                self.assertEqual(fresh[rel]["destinations"],
                                 row["destinations"], rel)
                checked += 1
        for rel in RELAYERING_COLLISIONS:
            self.assertIn(rel, fresh, "%s no longer collides -- drop its pin" % rel)
            self.assertEqual(fresh[rel]["destinations"],
                             COLLISION_DESTINATIONS[rel], rel)
            checked += 1
        self.assertGreaterEqual(checked, len(RELAYERING_COLLISIONS))

    def test_the_flat_path_hazards_are_resolved_by_the_layering(self):
        """Every committed FLAT collision row is gone because the local file
        moved, not because it vanished."""
        committed = load_fixture()["test_path_collisions"]["rows"]
        fresh = self.fresh["test_path_collisions"]["rows"]
        flat = [rel for rel in committed if rel.count("/") == 1]
        self.assertTrue(flat)
        for rel in flat:
            self.assertNotIn(rel, fresh, rel)
            self.assertFalse(os.path.exists(os.path.join(REPO, rel)), rel)
            module = "tests." + os.path.basename(rel)[:-3]
            self.assertIn(module, RELOCATIONS,
                          "%s is gone with no recorded successor" % rel)
            for successor in RELOCATIONS[module]:
                self.assertTrue(os.path.exists(os.path.join(
                    REPO, successor.replace(".", "/") + ".py")), successor)

    def test_every_relayering_collision_is_pinned_and_its_donor_side_is_free(self):
        """The hazard task 58 CREATED, gated live.

        Moving the local corpus onto the donor's layout puts seven local files
        on paths a pending donor file already occupies. Each must be pinned to
        two distinct destinations, the local side must be the file that is
        actually there, and the donor side must still be free in BOTH trees --
        an occupied donor destination means a port already landed on top of
        something.
        """
        self.assertEqual(len(RELAYERING_COLLISIONS), 7)
        for rel in RELAYERING_COLLISIONS:
            pins = COLLISION_DESTINATIONS[rel]
            self.assertEqual(set(pins), {"local", "donor"}, rel)
            self.assertNotEqual(pins["local"], pins["donor"], rel)
            self.assertEqual(pins["local"], rel, rel)
            self.assertTrue(os.path.exists(os.path.join(REPO, pins["local"])), rel)
            self.assertTrue(os.path.exists(os.path.join(DONOR, rel)), rel)
            self.assertFalse(os.path.exists(os.path.join(REPO, pins["donor"])), rel)
            self.assertFalse(os.path.exists(os.path.join(DONOR, pins["donor"])), rel)

    def test_a_freshly_derived_fixture_verifies(self):
        self.assertEqual(verify(self.path), [])
