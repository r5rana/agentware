"""Task-9 preserved-surface freeze: shape, gate semantics and negative proofs.

Every claim this module makes about the frozen fixture is paired with a proof
that the corresponding check can actually REDDEN. A freeze nobody can make fail
is indistinguishable from no freeze at all, which is the exact failure mode the
plan's residual-risk note calls out.

Stdlib `unittest` only. Never touches the real knowledge base.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
DONOR = "/tmp/aw-scratch/Agentware"
sys.path.insert(0, HERE)

from _preserved_freeze import (  # noqa: E402
    COLLISION_DESTINATIONS, diff_node_ids, diff_symbols, load_node_ids, verify)
from _preserved_scan import (  # noqa: E402
    FIXTURE_FAMILIES, TEMPLATE_ANCHORS, module_of, tail_of)

FIXTURE = os.path.join(HERE, "fixtures", "preserved-surface.json")

# Frozen headline figures. These are DERIVED numbers, pinned here so a silent
# change to a scanner's inclusion rule cannot pass unnoticed.
LOCAL_NODE_IDS = 2624
DONOR_NODE_IDS = 2778
UNION_NODE_IDS = 4490
SHARED_NODE_IDS = 912
CLI_SYMBOLS = 364
COLLISION_ROWS = 12
IDS_DESTROYED_BY_BLANKET_COPY = 76
GLOBAL_TAIL_DUPLICATES = 37


def load_fixture():
    with open(FIXTURE) as fh:
        return json.load(fh)


def donor_available():
    return os.path.isdir(os.path.join(DONOR, "tests"))


class FixtureShapeTest(unittest.TestCase):
    """The frozen numbers, and that the fixture is internally consistent."""

    @classmethod
    def setUpClass(cls):
        cls.fx = load_fixture()

    def test_verify_passes_on_the_committed_fixture(self):
        self.assertEqual(verify(FIXTURE), [])

    def test_node_id_counts_are_frozen(self):
        nid = self.fx["node_ids"]
        self.assertEqual(nid["local"]["count"], LOCAL_NODE_IDS)
        self.assertEqual(nid["donor"]["count"], DONOR_NODE_IDS)
        self.assertEqual(nid["union"], UNION_NODE_IDS)
        self.assertEqual(nid["shared"], SHARED_NODE_IDS)

    def test_both_node_id_sets_are_separate_files_and_match_their_headers(self):
        for side, expected in (("local", LOCAL_NODE_IDS), ("donor", DONOR_NODE_IDS)):
            blob = load_node_ids(FIXTURE, side)
            self.assertEqual(blob["side"], side)
            self.assertEqual(len(blob["node_ids"]), expected)
            self.assertEqual(len(set(blob["node_ids"])), expected, "duplicate ids")
            self.assertEqual(self.fx["node_ids"][side]["file"],
                             "nodeids-%s.json" % side)

    def test_node_ids_are_frozen_by_id_not_by_filename(self):
        """Every frozen entry is module.Class.test_method, never a path."""
        for side in ("local", "donor"):
            for nid in load_node_ids(FIXTURE, side)["node_ids"][:200]:
                self.assertNotIn("/", nid)
                self.assertGreaterEqual(len(nid.split(".")), 3)

    def test_pending_node_ids_are_exactly_the_donor_only_ids(self):
        # PENDING + LANDED accounts for every donor-only id, with nothing in
        # both. The equality used to be against `pending` alone, which made the
        # backlog underivable the moment a port wave landed a donor test —
        # `diff_node_ids` demands the id be dropped from pending, and this
        # demanded it stay. See `_preserved_freeze._verify_backlog`. The name is
        # kept because it is a frozen node id; the accounting is what changed.
        local = set(load_node_ids(FIXTURE, "local")["node_ids"])
        donor = set(load_node_ids(FIXTURE, "donor")["node_ids"])
        pending = set(self.fx["node_ids"]["pending_in_destination"])
        landed = set(self.fx["node_ids"]["landed_from_donor"])
        self.assertEqual(pending & landed, set())
        self.assertEqual(pending | landed, donor - local)

    def test_cli_symbol_count_and_pending_set(self):
        syms = self.fx["cli_symbols"]
        self.assertEqual(syms["count"], CLI_SYMBOLS)
        self.assertEqual(len(syms["by_symbol"]), CLI_SYMBOLS)
        pending = set(syms["pending_in_destination"])
        self.assertTrue(pending)
        for name in pending:
            self.assertEqual(syms["by_symbol"][name]["sides"], ["donor"],
                             "%s is pending but locally read" % name)

    def test_every_cli_symbol_row_carries_sides_and_read_sites(self):
        for name, row in self.fx["cli_symbols"]["by_symbol"].items():
            self.assertTrue(row["sides"], name)
            self.assertTrue(row["sites"], name)

    def test_executables_include_the_commit_msg_hook(self):
        execs = self.fx["executables"]
        self.assertIn("scripts/hooks/commit-msg", execs)
        self.assertEqual(execs["scripts/hooks/commit-msg"]["sides"], ["local"])
        self.assertIn("scripts/agentware", execs)

    def test_every_template_declares_at_least_one_anchor(self):
        self.assertTrue(self.fx["templates"])
        for name, row in self.fx["templates"].items():
            self.assertTrue(row["anchors"], "%s has no anchor" % name)
            self.assertEqual(sorted(row["anchors"]),
                             sorted(TEMPLATE_ANCHORS[name]))

    def test_both_catalog_files_are_claimed_by_basename_without_a_count(self):
        catalogs = self.fx["catalogs"]
        self.assertEqual(set(catalogs), {"skills.json", "modules.json"})
        # PROVENANCE, not membership: modules.json is the donor's generated
        # module manifest and must stay claimed as a donor contribution for as
        # long as the freeze exists. Task 25 landed it HERE too (the lib README
        # links to it, so the two cannot land apart), which is why this is an
        # `in` and not an equality — a landing must not be able to erase the
        # record of which tree the capability came from.
        self.assertIn("donor", catalogs["modules.json"]["sides"])
        # A pinned file count would redden on the legitimate catalog growth
        # tasks 67 and 73 introduce; basenames cannot.
        self.assertNotIn("count", catalogs)
        for row in catalogs.values():
            self.assertNotIn("count", row)

    def test_the_three_fixture_families_are_claimed(self):
        families = self.fx["fixture_families"]
        self.assertEqual(set(families), {f for f, _r, _k in FIXTURE_FAMILIES})
        self.assertEqual(len(families), 3)
        self.assertEqual(families["help_golden"]["sides"], ["donor"])
        self.assertEqual(sorted(families["gold_fixture_suite"]["sides"]),
                         ["donor", "local"])

    def test_pending_assets_are_donor_only_and_resolve_to_real_rows(self):
        """SHRINK-ONLY. `catalog:modules.json` was dropped by task 25.

        The ratchet is one-directional by design: a pending asset that starts
        resolving in this tree must be REMOVED from the backlog (the gate emits
        "RATCHET: pending asset now satisfied, drop it"), never left parked. Task
        25 landed `scripts/agentware_lib/` and its generated module manifest
        `catalog/modules.json` together — the lib README links to it, so the two
        cannot land apart without a dangling link. Two donor assets remain: the
        help goldens (task 60, regenerated once the verb surface is final) and
        the decision template (task 51, which reconciles the template corpus).
        """
        pending = self.fx["assets"]["pending_in_destination"]
        self.assertEqual(set(pending), {"fixture_family:help_golden",
                                        "template:decision-template.md"})

    def test_fixture_carries_no_operator_home_path(self):
        with open(FIXTURE, encoding="utf-8") as fh:
            raw = fh.read()
        self.assertNotIn(os.path.expanduser("~"), raw)
        self.assertIn("~/workspace", raw)


class CollisionRecordTest(unittest.TestCase):
    """The test-file path collision, recorded and resolved BEFORE any copy."""

    @classmethod
    def setUpClass(cls):
        cls.coll = load_fixture()["test_path_collisions"]

    def test_all_shared_path_hazards_are_recorded_not_just_the_named_one(self):
        rows = self.coll["rows"]
        self.assertEqual(len(rows), COLLISION_ROWS)
        self.assertEqual(self.coll["total_local_ids_destroyed_by_blanket_copy"],
                         IDS_DESTROYED_BY_BLANKET_COPY)

    def test_the_named_collision_is_fully_disjoint(self):
        row = self.coll["rows"]["tests/test_skill_promote.py"]
        self.assertEqual(row["overlap"], 0)
        self.assertTrue(row["disjoint"])
        self.assertEqual(row["local_ids"], 30)
        self.assertEqual(row["donor_ids"], 8)
        self.assertEqual(row["resolution"], "split-two-destinations")

    def test_it_is_the_only_fully_disjoint_collision(self):
        disjoint = [r for r, v in self.coll["rows"].items() if v["disjoint"]]
        self.assertEqual(disjoint, ["tests/test_skill_promote.py"])

    def test_both_sides_have_a_pinned_distinct_destination(self):
        dests = self.coll["rows"]["tests/test_skill_promote.py"]["destinations"]
        self.assertEqual(set(dests), {"local", "donor"})
        self.assertNotEqual(dests["local"], dests["donor"])
        self.assertEqual(dests, COLLISION_DESTINATIONS["tests/test_skill_promote.py"])

    def test_pinned_destinations_are_free_in_BOTH_trees(self):
        for dest in COLLISION_DESTINATIONS["tests/test_skill_promote.py"].values():
            self.assertFalse(os.path.exists(os.path.join(REPO, dest)), dest)
            if donor_available():
                self.assertFalse(os.path.exists(os.path.join(DONOR, dest)), dest)

    def test_non_disjoint_rows_reconcile_in_place_and_pin_nothing(self):
        for rel, row in self.coll["rows"].items():
            if not row["disjoint"]:
                self.assertEqual(row["resolution"], "reconcile-in-place")
                self.assertEqual(row["destinations"], {})

    def test_no_destination_receives_one_class_test_method_twice(self):
        self.assertEqual(self.coll["destination_tail_collisions"], {})

    def test_global_tail_uniqueness_is_recorded_as_evidence_and_NOT_gated(self):
        """37 tails repeat across modules in the donor's contract-test style.

        They are benign -- the module path disambiguates them -- so the freeze
        records the number and gates the per-destination property instead.
        """
        self.assertEqual(self.coll["global_tail_duplicates_not_gated"],
                         GLOBAL_TAIL_DUPLICATES)


class GateSemanticsTest(unittest.TestCase):
    """The pure diff functions the gate is built from, each proven to redden."""

    def test_missing_frozen_local_id_is_LOST(self):
        out = diff_node_ids(["a.B.c"], [], [], [])
        self.assertEqual(out, ["LOST node id: a.B.c"])

    def test_nothing_lost_is_silent(self):
        self.assertEqual(diff_node_ids(["a.B.c"], ["a.B.c"], [], ["a.B.c"]), [])

    def test_missing_non_pending_donor_id_is_LOST(self):
        out = diff_node_ids([], ["d.E.f"], [], [])
        self.assertEqual(out, ["LOST donor node id: d.E.f"])

    def test_pending_donor_id_absent_is_allowed(self):
        self.assertEqual(diff_node_ids([], ["d.E.f"], ["d.E.f"], []), [])

    def test_pending_id_that_starts_resolving_trips_the_ratchet(self):
        out = diff_node_ids([], ["d.E.f"], ["d.E.f"], ["d.E.f"])
        self.assertEqual(len(out), 1)
        self.assertIn("RATCHET", out[0])

    def test_unreachable_required_symbol_reddens(self):
        out = diff_symbols({"save_index"}, [], [])
        self.assertEqual(out, ["UNREACHABLE CLI symbol: save_index"])

    def test_pending_symbol_that_starts_resolving_trips_the_ratchet(self):
        out = diff_symbols({"x"}, ["x"], ["x"])
        self.assertIn("RATCHET", out[0])

    def test_ratchet_is_shrink_only_in_both_directions(self):
        """Losing a resolving symbol AND keeping a resolved one both redden."""
        self.assertTrue(diff_symbols({"a", "b"}, ["b"], ["b"]))
        self.assertTrue(diff_symbols({"a", "b"}, ["b"], []))
        self.assertEqual(diff_symbols({"a", "b"}, ["b"], ["a"]), [])


class NodeIdHelperTest(unittest.TestCase):

    def test_module_and_tail_split(self):
        nid = "tests.unit.features.test_skills.ExportsTest.test_all"
        self.assertEqual(module_of(nid), "tests.unit.features.test_skills")
        self.assertEqual(tail_of(nid), "ExportsTest.test_all")


class VerifierNegativeTest(unittest.TestCase):
    """`verify` must refuse a fixture whose body contradicts its header."""

    def mutate(self, fn):
        root = tempfile.mkdtemp(prefix="aw-verify-")
        self.addCleanup(shutil.rmtree, root, True)
        fx = load_fixture()
        blobs = {s: load_node_ids(FIXTURE, s) for s in ("local", "donor")}
        fn(fx, blobs)
        for side, blob in blobs.items():
            with open(os.path.join(root, "nodeids-%s.json" % side), "w") as fh:
                json.dump(blob, fh)
        path = os.path.join(root, "preserved-surface.json")
        with open(path, "w") as fh:
            json.dump(fx, fh)
        return verify(path)

    def assertReddens(self, fn, needle):
        problems = self.mutate(fn)
        self.assertTrue(problems, "expected a failure mentioning %r" % needle)
        self.assertTrue(any(needle in p for p in problems),
                        "%r not in %r" % (needle, problems))

    def test_unmutated_copy_still_passes(self):
        self.assertEqual(self.mutate(lambda fx, blobs: None), [])

    def test_header_count_contradicting_the_body_reddens(self):
        self.assertReddens(
            lambda fx, b: fx["node_ids"]["local"].__setitem__("count", 1),
            "header count")

    def test_duplicate_node_id_reddens(self):
        def fn(fx, b):
            b["local"]["node_ids"].append(b["local"]["node_ids"][0])
            b["local"]["count"] += 1
            fx["node_ids"]["local"]["count"] += 1
        self.assertReddens(fn, "duplicates")

    def test_wrong_union_count_reddens(self):
        self.assertReddens(lambda fx, b: fx["node_ids"].__setitem__("union", 1),
                           "union")

    def test_wrong_shared_count_reddens(self):
        self.assertReddens(lambda fx, b: fx["node_ids"].__setitem__("shared", 7),
                           "shared")

    def test_truncated_pending_set_reddens(self):
        # Dropping an id from pending without LANDING it leaves a donor-only id
        # unaccounted for, which is exactly the silent-loss shape the backlog
        # exists to prevent.
        self.assertReddens(
            lambda fx, b: fx["node_ids"]["pending_in_destination"].pop(),
            "backlog")

    def test_an_id_both_pending_and_landed_reddens(self):
        self.assertReddens(
            lambda fx, b: fx["node_ids"]["landed_from_donor"].append(
                fx["node_ids"]["pending_in_destination"][0]),
            "both pending and landed")

    def test_landing_an_id_that_is_not_donor_only_reddens(self):
        # `landed_from_donor` is a record of donor capability arriving here, not
        # a free-text list: a local-only id parked in it would inflate the
        # apparent port progress and account for nothing.
        self.assertReddens(
            lambda fx, b: fx["node_ids"]["landed_from_donor"].append(
                "tests.test_made_up.MadeUpTest.test_nothing"),
            "not donor-only")

    def test_a_disjoint_collision_without_destinations_reddens(self):
        self.assertReddens(
            lambda fx, b: fx["test_path_collisions"]["rows"]
            ["tests/test_skill_promote.py"].__setitem__("destinations", {}),
            "no pinned destinations")

    def test_pinning_both_sides_to_one_destination_reddens(self):
        def fn(fx, b):
            row = fx["test_path_collisions"]["rows"]["tests/test_skill_promote.py"]
            row["destinations"]["donor"] = row["destinations"]["local"]
        self.assertReddens(fn, "same destination twice")

    def test_pinning_only_one_side_reddens(self):
        def fn(fx, b):
            row = fx["test_path_collisions"]["rows"]["tests/test_skill_promote.py"]
            del row["destinations"]["donor"]
        self.assertReddens(fn, "does not pin BOTH sides")

    def test_a_template_losing_its_anchors_reddens(self):
        self.assertReddens(
            lambda fx, b: fx["templates"]["skill-template.md"].__setitem__(
                "anchors", []),
            "anchors")

    def test_dropping_a_fixture_family_reddens(self):
        self.assertReddens(lambda fx, b: fx["fixture_families"].pop("swe_tasks"),
                           "fixture families")

    def test_dropping_every_catalog_reddens(self):
        self.assertReddens(lambda fx, b: fx["catalogs"].clear(), "catalog")

    def test_a_locally_read_symbol_parked_as_pending_reddens(self):
        def fn(fx, b):
            local_only = next(n for n, r in fx["cli_symbols"]["by_symbol"].items()
                              if r["sides"] == ["local"])
            fx["cli_symbols"]["pending_in_destination"].append(local_only)
        self.assertReddens(fn, "locally-read symbols")

    def test_a_local_asset_parked_as_pending_reddens(self):
        self.assertReddens(
            lambda fx, b: fx["assets"]["pending_in_destination"].append(
                "executable:scripts/hooks/commit-msg"),
            "not donor-only")

    def test_a_pending_asset_with_no_frozen_row_reddens(self):
        self.assertReddens(
            lambda fx, b: fx["assets"]["pending_in_destination"].append(
                "catalog:invented.json"),
            "claims no frozen row")

    def test_a_wrong_collision_total_reddens(self):
        self.assertReddens(
            lambda fx, b: fx["test_path_collisions"].__setitem__(
                "total_local_ids_destroyed_by_blanket_copy", 0),
            "collision total")

    def test_a_destination_tail_collision_reddens(self):
        self.assertReddens(
            lambda fx, b: fx["test_path_collisions"].__setitem__(
                "destination_tail_collisions", {"x.py": ["A.test_b"]}),
            "tail collisions")

    def test_an_empty_frozen_set_reddens(self):
        def fn(fx, b):
            b["donor"]["node_ids"] = []
            b["donor"]["count"] = 0
            fx["node_ids"]["donor"]["count"] = 0
            fx["node_ids"]["union"] = len(b["local"]["node_ids"])
            fx["node_ids"]["shared"] = 0
            fx["node_ids"]["pending_in_destination"] = []
        self.assertReddens(fn, "empty")




if __name__ == "__main__":
    unittest.main()
