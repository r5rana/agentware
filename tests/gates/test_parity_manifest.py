"""Task-10 parity manifest: shape, derivation and coverage.

The gate's negative proofs live in `tests/gates/test_parity_gate.py`. This half asserts
the manifest is what it claims to be:

* it is a BUILD ARTIFACT — the committed copy must equal a fresh derivation, so a
  row can never be hand-edited into agreement with a capability that regressed;
* every capability the four binding fixtures know about carries a row, on the
  correct side, in both directions;
* the curated tier actually covers the plan's own named high-risk inventory —
  the four silent-loss classes and the seven impossible unions — rather than
  whatever happened to be easy to write.

Stdlib `unittest` only. Never touches the real knowledge base.
"""

from tests._paths import TESTS_DIR

import ast
import json
import os
import sys
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import _parity_manifest as pm  # noqa: E402
from _parity_curated import CURATED_ROWS  # noqa: E402
from _parity_schema import (  # noqa: E402
    DIMENSIONS, PROBE_KINDS, SIDE_VALUES, STATUSES, VERIFICATIONS)
from _test_layout import RELOCATIONS  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")

#: The plan's own highest-risk inventory, frozen by row id. Every one of these is
#: a capability the plan says no surface gate, manifest row-count or test node id
#: can see. The row-count ratchet would happily accept swapping one of these for
#: a cheaper row; this will not.
CRITICAL_ROW_IDS = (
    # the four silent-loss classes
    "retrieval:stage-1-skill-pin-floor",
    "retrieval:read-time-trust-and-freshness",
    "loop:worklog-marker-regexes-fail-closed",
    # The FROZEN collision key, not a live path: `tests-collision:` row ids are
    # derived from `preserved-surface.json`'s task-9 capture of the two trees as
    # they stood at freeze time, so relayering the corpus does NOT move it.
    "tests-collision:tests/test_skill_promote.py",
    # the seven unions that are logically impossible, as decided
    "kb:corpus-fingerprint-freshness-authority",
    "retrieval:token-budget-ladder",
    "sec:scoped-staging-allowlist",
    "surface-flag:recall:--all-teams",
    # the load-bearing knowledge-base invariants
    "kb:atomic-durable-write",
    "kb:reentrant-index-lock",
    "kb:quarantine-and-continue-rebuild",
    "kb:auto-rebuild-once-recovery",
    # the evaluation arm the facade itself breaks
    "eval:ab-gate-layout-aware-extraction",
)


def load(name):
    return pm.load_json(os.path.join(FIXTURES, name))


class DerivationTest(unittest.TestCase):

    def setUp(self):
        self.manifest = load("parity-manifest.json")

    def test_the_committed_manifest_equals_a_fresh_derivation(self):
        self.assertEqual(pm.derive(REPO), self.manifest)

    def test_derivation_is_deterministic(self):
        self.assertEqual(pm.derive(REPO), pm.derive(REPO))

    def test_the_committed_manifest_verifies_against_its_own_body(self):
        self.assertEqual(pm.verify(self.manifest), [])

    def test_rows_are_uniquely_identified_and_ordered(self):
        ids = [r["id"] for r in self.manifest["rows"]]
        self.assertEqual(len(ids), len(set(ids)))
        ordered = sorted(self.manifest["rows"], key=lambda r: (r["dimension"],
                                                              r["id"]))
        self.assertEqual(ids, [r["id"] for r in ordered])

    def test_the_header_publishes_the_schema_vocabularies(self):
        self.assertEqual(self.manifest["statuses"], list(STATUSES))
        self.assertEqual(self.manifest["verifications"], list(VERIFICATIONS))
        self.assertEqual(sorted(self.manifest["status_modes"]), sorted(STATUSES))

    def test_an_unadjudicated_exclusion_halts_the_derivation(self):
        union = load("surface-union.json")
        union["exclusions"] = [dict(union["exclusions"][0],
                                    option_strings=["--invented"])]
        with self.assertRaises(SystemExit):
            pm.surface_rows(union)


class DimensionTableTest(unittest.TestCase):

    def setUp(self):
        self.manifest = load("parity-manifest.json")

    def test_the_committed_table_matches_the_schema_module(self):
        self.assertEqual([d["name"] for d in self.manifest["dimensions"]],
                         [d["name"] for d in DIMENSIONS])

    def test_every_dimension_declares_its_tier_delta_and_plan_tasks(self):
        for dim in self.manifest["dimensions"]:
            self.assertIn(dim["tier"], ("generated", "curated"), dim["name"])
            self.assertIsInstance(dim["donor_delta"], bool)
            self.assertTrue(dim["donor_delta_evidence"])
            self.assertTrue(dim["plan_tasks"])

    def test_generated_dimensions_name_an_existing_source_and_gate(self):
        for dim in self.manifest["dimensions"]:
            if dim["tier"] != "generated":
                continue
            for field in ("source", "gate"):
                self.assertTrue(os.path.exists(os.path.join(REPO, dim[field])),
                                "%s %s" % (dim["name"], field))

    def test_the_four_generated_deltas_are_re_derived_not_merely_declared(self):
        derived = [d for d in self.manifest["dimensions"]
                   if "donor_delta_derived" in d]
        self.assertEqual(len(derived), 4)
        for dim in derived:
            self.assertEqual(dim["donor_delta"], dim["donor_delta_derived"])

    def test_every_dimension_owns_a_donor_side_and_a_local_side_row(self):
        donor = self.manifest["counts"]["donor_side_rows_by_dimension"]
        for dim in self.manifest["dimensions"]:
            self.assertGreater(donor.get(dim["name"], 0), 0, dim["name"])


class GeneratedTierCoverageTest(unittest.TestCase):
    """Nothing the four binding fixtures know about may be missing a row."""

    def setUp(self):
        self.manifest = load("parity-manifest.json")
        self.by_id = {r["id"]: r for r in self.manifest["rows"]}

    def test_every_union_parser_node_carries_a_row_on_the_right_side(self):
        union = load("surface-union.json")
        for node in union["nodes"]:
            rid = "surface:%s" % (".".join(node["path"]) or "<root>")
            self.assertIn(rid, self.by_id)
            self.assertEqual(self.by_id[rid]["sides"], pm._sides(node["sides"]))

    def test_all_44_top_level_verbs_and_both_only_sets_are_represented(self):
        union = load("surface-union.json")
        for verb in union["top_level_verbs"]:
            self.assertIn("surface:%s" % verb, self.by_id)
        for verb in union["donor_only_verbs"]:
            self.assertEqual(self.by_id["surface:%s" % verb]["sides"], ["donor"])
        for verb in union["local_only_verbs"]:
            self.assertEqual(self.by_id["surface:%s" % verb]["sides"], ["local"])

    def test_every_env_and_config_key_carries_a_row(self):
        env = load("env-config-parity.json")
        for key in env["keys"]:
            rid = "env:%s" % key["key"]
            self.assertIn(rid, self.by_id)
            self.assertEqual(self.by_id[rid]["sides"], sorted(key["sides"]))

    def test_every_test_module_on_either_side_carries_a_row(self):
        seen = {"local": set(), "donor": set()}
        for side in ("local", "donor"):
            for node_id in load("nodeids-%s.json" % side)["node_ids"]:
                seen[side].add(".".join(node_id.split(".")[:-2]))
        for side, modules in seen.items():
            for module in modules:
                row = self.by_id["tests:%s" % module]
                self.assertIn(side, row["sides"], module)

    def test_every_frozen_asset_carries_a_row(self):
        preserved = load("preserved-surface.json")
        for section, label in pm.ASSET_KINDS:
            for name in preserved[section]:
                self.assertIn("asset:%s:%s" % (label, name), self.by_id)

    def test_every_recorded_test_path_collision_carries_a_row(self):
        preserved = load("preserved-surface.json")
        rows = preserved["test_path_collisions"]["rows"]
        self.assertEqual(len(rows), 12)
        for path in rows:
            self.assertIn("tests-collision:%s" % path, self.by_id)

    def test_every_surface_adjudication_becomes_a_superseded_row(self):
        union = load("surface-union.json")
        superseded = [r for r in self.manifest["rows"]
                      if r["id"].startswith("surface-adjudication:")]
        self.assertEqual(len(superseded), len(union["adjudications"]))
        for row in superseded:
            self.assertEqual(row["status"], "superseded")
            self.assertIn(row["superseded_by"], self.by_id)

    def test_every_surface_exclusion_becomes_an_excluded_row_with_a_probe(self):
        # Scoped to the CLI-surface dimension: exclusions in other dimensions are
        # adjudicated deletions (a plan task retiring a capability), not flags the
        # union fixture dropped. Every excluded row, wherever it came from, still
        # owes the same four fields and a probe from the closed grammar.
        union = load("surface-union.json")
        excluded = [r for r in self.manifest["rows"] if r["status"] == "excluded"]
        surface = [r for r in excluded if r["dimension"] == "cli_surface"]
        self.assertEqual(len(surface), len(union["exclusions"]))
        for row in excluded:
            self.assertIn(row["absence_probe"]["kind"], PROBE_KINDS)
            self.assertTrue(row["reason"] and row["approver"] and row["follow_up"])


class CuratedTierTest(unittest.TestCase):

    def setUp(self):
        self.manifest = load("parity-manifest.json")
        self.curated = [r for r in self.manifest["rows"]
                        if r["verification"] == "curated"]

    def test_the_committed_curated_rows_are_the_curated_table(self):
        # The hand-written table holds two shapes: `curated` rows (a named test +
        # its mutation) and hand-written `excluded` rows (a runnable absence
        # probe). Both must land in the manifest; neither may appear there
        # without appearing here.
        hand_written = {r["verification"]: [] for r in CURATED_ROWS}
        for row in CURATED_ROWS:
            hand_written[row["verification"]].append(row["id"])
        self.assertEqual(sorted(r["id"] for r in self.curated),
                         sorted(hand_written["curated"]))
        committed = {r["id"] for r in self.manifest["rows"]}
        for rid in hand_written.get("absence_probe", []):
            self.assertIn(rid, committed)

    #: Upper bound on a curated row's plan task. A SANITY bound only — it catches a
    #: typo or a placeholder, and the real check is the dimension-membership test
    #: below, which is strictly stronger (a task absent from every dimension's
    #: plan_tasks fails there whatever this bound says). Widened 88 -> 106 at task
    #: 90: the plan grew to 105 tasks, so the old literal — the task count on the
    #: day it was written — had started rejecting rows for existing.
    MAX_PLAN_TASK = 105

    def test_every_curated_row_names_a_plan_task_and_a_test_and_a_mutation(self):
        for row in self.curated:
            self.assertIn(row["task"], range(1, self.MAX_PLAN_TASK + 1), row["id"])
            self.assertTrue(row["evidence"]["test"].startswith("tests."))
            self.assertGreater(len(row["evidence"]["mutation"]), 20, row["id"])

    def test_a_curated_rows_task_belongs_to_its_dimension(self):
        by_name = {d["name"]: d for d in self.manifest["dimensions"]}
        for row in self.curated:
            self.assertIn(row["task"], by_name[row["dimension"]]["plan_tasks"],
                          row["id"])

    def test_every_curated_dimension_is_seeded_by_the_curated_table(self):
        seeded = {r["dimension"] for r in self.curated}
        for dim in self.manifest["dimensions"]:
            if dim["tier"] == "curated":
                self.assertIn(dim["name"], seeded)

    def test_the_plans_named_high_risk_capabilities_all_carry_a_row(self):
        ids = {r["id"] for r in self.manifest["rows"]}
        for rid in CRITICAL_ROW_IDS:
            self.assertIn(rid, ids)

    def test_a_resolving_curated_test_really_holds_collected_tests(self):
        frozen = set()
        for node_id in load("nodeids-local.json")["node_ids"]:
            frozen.add(".".join(node_id.split(".")[:-2]))
        # Task 58 relayered the corpus, so a module frozen as `tests.test_x`
        # now collects as `tests.<tier>.test_x`. A curated row naming the
        # successor IS naming a frozen module, not one authored after the
        # freeze — resolve it through the RECORDED ledger (never a guess: a
        # module absent from RELOCATIONS is not expanded, so a genuinely new
        # module still takes the strict branch below).
        frozen |= {succ for mod in tuple(frozen)
                   for succ in RELOCATIONS.get(mod, ())}
        ratchet = pm.load_json(pm.RATCHET)
        checked = 0
        for row in self.curated:
            if row["id"] in ratchet["pending_verification"]:
                continue
            dotted = row["evidence"]["test"]
            if dotted in frozen:
                checked += 1
                continue
            # A test module authored AFTER the task-9 freeze cannot be in the
            # frozen capture. It still has to hold real collected tests, so
            # assert that directly rather than exempting it.
            path = pm.test_module_path(REPO, dotted)
            self.assertTrue(os.path.exists(path), row["id"])
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            # Narrowed at task 79 from a substring search for the literal
            # "unittest.TestCase". That heuristic was WRONG about this corpus
            # rather than strict: most modules here subclass a shared fixture
            # base (`SyntheticKBTestCase`), which is a TestCase but never
            # spells the words, so the check demanded a particular authoring
            # style and would have rejected a perfectly collected module. The
            # claim is "this file defines collected test classes", so it is
            # read from the AST: at least one class whose base name ends in
            # `TestCase` or `Test`, and more than two `test_` methods.
            classes = [n for n in ast.walk(ast.parse(body))
                       if isinstance(n, ast.ClassDef)
                       and any((getattr(b, "attr", None) or getattr(b, "id", ""))
                               .endswith(("TestCase", "Test"))
                               for b in n.bases)]
            self.assertTrue(classes,
                            "%s: %s defines no TestCase subclass"
                            % (row["id"], dotted))
            self.assertGreater(body.count("    def test_"), 2, row["id"])
            checked += 1
        self.assertGreater(checked, 30)

    def test_most_curated_rows_are_verifiable_today(self):
        ratchet = pm.load_json(pm.RATCHET)
        pending = len(ratchet["pending_verification"])
        self.assertLess(pending, len(self.curated) / 2)


class HygieneTest(unittest.TestCase):

    ARTIFACTS = ("tests/fixtures/parity-manifest.json",
                 "tests/fixtures/parity-ratchet.json",
                 "tests/_parity_curated.py",
                 "tests/_parity_gate_checks.py",
                 "tests/_parity_manifest.py",
                 "tests/_parity_schema.py")

    #: The task-12 operator-directed banned-token class. agentware is vendor
    #: neutral; this ratchet protects a state that is already clean. The tokens
    #: are ASSEMBLED rather than written out, so this file does not itself become
    #: a hit that task 12's zero-tolerance, no-allowlist scan has to exempt.
    BANNED = tuple("".join(parts) for parts in
                   (("whats", "app"), ("wa", "bridge"), ("wa-", "bridge"),
                    ("wa_", "bridge")))

    def bodies(self):
        for rel in self.ARTIFACTS:
            with open(os.path.join(REPO, rel)) as fh:
                yield rel, fh.read()

    def test_no_artifact_carries_an_absolute_home_path(self):
        home = os.path.expanduser("~")
        for rel, body in self.bodies():
            self.assertNotIn(home, body, rel)

    def test_no_artifact_carries_a_banned_vendor_token(self):
        for rel, body in self.bodies():
            for token in self.BANNED:
                self.assertNotIn(token, body.lower(), "%s: %s" % (rel, token))

    def test_the_fixtures_are_valid_json(self):
        for name in ("parity-manifest.json", "parity-ratchet.json"):
            with open(os.path.join(FIXTURES, name)) as fh:
                json.load(fh)

    def test_every_side_value_used_is_in_the_vocabulary(self):
        manifest = load("parity-manifest.json")
        used = {s for row in manifest["rows"] for s in row["sides"]}
        self.assertEqual(used, set(SIDE_VALUES))

    def test_no_committed_file_exceeds_the_tests_ceiling(self):
        for rel in self.ARTIFACTS + ("tests/gates/test_parity_manifest.py",
                                     "tests/gates/test_parity_gate.py"):
            if rel.endswith(".json"):
                continue
            with open(os.path.join(REPO, rel)) as fh:
                self.assertLessEqual(len(fh.readlines()), 600, rel)


if __name__ == "__main__":
    unittest.main()
