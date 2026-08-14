"""Task-10 parity manifest: THE GATE, proven by breaking it.

A gate that has only ever been observed passing is not evidence. Every check in
`_parity_manifest.check()` gets a mutation here that must redden it, and the
mutation is always the CHEAPEST one an author could make by accident.

Two of these deserve calling out because a weaker test would pass vacuously:

* the row-deletion proof re-summarises the manifest after deleting the row, so
  the counts header agrees with the body and ONLY the separately-committed
  ratchet can catch it. That is the whole reason the floors live in their own
  file;
* the absence-probe proof points the probe at a flag that really exists, so it
  fails for the right reason — the probe RAN and found the thing present, rather
  than passing because nothing ran.

Stdlib `unittest` only. Never touches the real knowledge base.
"""

import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import _parity_manifest as pm  # noqa: E402
from _parity_schema import PROBE_KINDS, run_probe  # noqa: E402


def load():
    return pm.load_json(pm.MANIFEST), pm.load_json(pm.RATCHET)


def resummarise(manifest):
    """Rewrite the header so only the external ratchet can see the change."""
    manifest["counts"] = pm.summarise(manifest["rows"])
    return manifest


def joined(violations):
    return "\n".join(violations)


class GatePassesTest(unittest.TestCase):

    def test_committed_manifest_and_ratchet_pass_the_gate(self):
        manifest, ratchet = load()
        self.assertEqual(pm.check(manifest, ratchet, REPO), [])

    def test_gate_runs_as_a_command_and_exits_zero(self):
        self.assertEqual(pm.main(["--check", "--root", REPO]), 0)

    def test_every_declared_dimension_owns_at_least_one_row(self):
        manifest, _ = load()
        counts = manifest["counts"]["by_dimension"]
        for dim in manifest["dimensions"]:
            self.assertGreater(counts.get(dim["name"], 0), 0, dim["name"])


class DimensionCoverageTest(unittest.TestCase):
    """*a dimension with zero rows fails it*"""

    def test_emptying_any_dimension_reddens_the_gate_naming_it(self):
        manifest, ratchet = load()
        for name in sorted(manifest["counts"]["by_dimension"]):
            broken = copy.deepcopy(manifest)
            broken["rows"] = [r for r in broken["rows"] if r["dimension"] != name]
            resummarise(broken)
            bad = pm.check(broken, ratchet, REPO, run_probes=False)
            self.assertIn("dimension %s has ZERO rows" % name, joined(bad))

    def test_a_row_in_an_undeclared_dimension_reddens(self):
        manifest, ratchet = load()
        broken = copy.deepcopy(manifest)
        broken["rows"][0]["dimension"] = "invented_dimension"
        resummarise(broken)
        bad = pm.check(broken, ratchet, REPO, run_probes=False)
        self.assertIn("undeclared dimension", joined(bad))


class DonorSideCoverageTest(unittest.TestCase):
    """*a dimension with a donor delta and zero donor-side rows fails it*"""

    def test_dropping_the_donor_side_of_a_dimension_reddens_the_gate(self):
        manifest, ratchet = load()
        deltas = [d["name"] for d in manifest["dimensions"] if d["donor_delta"]]
        self.assertTrue(deltas)
        for name in deltas:
            broken = copy.deepcopy(manifest)
            for row in broken["rows"]:
                if row["dimension"] == name:
                    row["sides"] = ["local"]
            resummarise(broken)
            bad = pm.check(broken, ratchet, REPO, run_probes=False)
            self.assertIn("dimension %s declares a donor delta" % name, joined(bad))

    def test_the_row_count_is_untouched_so_only_the_donor_check_can_see_it(self):
        manifest, ratchet = load()
        broken = copy.deepcopy(manifest)
        for row in broken["rows"]:
            if row["dimension"] == "org_team":
                row["sides"] = ["local"]
        resummarise(broken)
        self.assertEqual(broken["counts"]["by_dimension"],
                         manifest["counts"]["by_dimension"])
        self.assertEqual(pm.check_ratchet(broken, ratchet), [])
        self.assertTrue(pm.check_dimensions(broken))

    def test_a_dimension_may_not_declare_a_delta_its_fixture_denies(self):
        manifest, ratchet = load()
        broken = copy.deepcopy(manifest)
        for dim in broken["dimensions"]:
            if "donor_delta_derived" in dim:
                dim["donor_delta"] = not dim["donor_delta_derived"]
        bad = pm.check(broken, ratchet, REPO, run_probes=False)
        self.assertIn("but its source fixture derives", joined(bad))


class ExcludedRowTest(unittest.TestCase):
    """*an excluded row missing its absence probe fails it*"""

    def _excluded_ids(self, manifest):
        return [r["id"] for r in manifest["rows"] if r["status"] == "excluded"]

    def test_there_is_at_least_one_excluded_row_to_break(self):
        manifest, _ = load()
        self.assertTrue(self._excluded_ids(manifest))

    def test_removing_the_absence_probe_reddens_the_gate(self):
        manifest, ratchet = load()
        broken = copy.deepcopy(manifest)
        for row in broken["rows"]:
            if row["status"] == "excluded":
                row.pop("absence_probe")
        bad = pm.check(broken, ratchet, REPO, run_probes=False)
        self.assertIn("is missing absence_probe", joined(bad))

    def test_removing_reason_approver_or_follow_up_each_redden(self):
        manifest, ratchet = load()
        for field in ("reason", "approver", "follow_up"):
            broken = copy.deepcopy(manifest)
            for row in broken["rows"]:
                if row["status"] == "excluded":
                    row.pop(field)
            bad = pm.check(broken, ratchet, REPO, run_probes=False)
            self.assertIn("is missing %s" % field, joined(bad))

    def test_an_excluded_row_may_not_claim_another_verification_mode(self):
        manifest, ratchet = load()
        broken = copy.deepcopy(manifest)
        for row in broken["rows"]:
            if row["status"] == "excluded":
                row["verification"] = "curated"
        resummarise(broken)
        bad = pm.check(broken, ratchet, REPO, run_probes=False)
        self.assertIn("status excluded may not use verification curated",
                      joined(bad))

    def test_the_probe_must_actually_prove_absence_not_merely_run(self):
        manifest, ratchet = load()
        broken = copy.deepcopy(manifest)
        for row in broken["rows"]:
            if row["status"] == "excluded":
                # --scope really exists, so an honest probe must FAIL here.
                row["absence_probe"] = {
                    "kind": "cli-absent",
                    "argv": ["recall", "parity-probe", "--scope", "all"],
                    "expect": "unrecognized arguments",
                }
        bad = pm.check(broken, ratchet, REPO)
        self.assertIn("absence probe FAILED to prove absence", joined(bad))

    def test_an_unknown_probe_kind_is_rejected_by_the_closed_grammar(self):
        ok, detail = run_probe(REPO, {"kind": "vibes", "note": "trust me"})
        self.assertFalse(ok)
        self.assertIn("unknown probe kind", detail)

    def test_every_probe_kind_in_the_grammar_has_a_runner(self):
        for kind in PROBE_KINDS:
            ok, detail = run_probe(REPO, {"kind": kind})
            self.assertFalse(ok)
            self.assertIn("missing", detail)

    def test_grep_and_path_probes_bite_on_something_that_is_present(self):
        ok, _ = run_probe(REPO, {"kind": "path-absent", "path": "AGENTS.md"})
        self.assertFalse(ok)
        ok, _ = run_probe(REPO, {"kind": "path-absent", "path": "no/such/file"})
        self.assertTrue(ok)
        ok, detail = run_probe(REPO, {"kind": "grep-absent",
                                      "pattern": "R-PKG-01"})
        self.assertFalse(ok, detail)


class RatchetTest(unittest.TestCase):
    """*the per-dimension row-count ratchet reddens when any row is deleted*"""

    def test_deleting_one_row_from_any_dimension_reddens_the_ratchet(self):
        manifest, ratchet = load()
        for name in sorted(manifest["counts"]["by_dimension"]):
            broken = copy.deepcopy(manifest)
            victim = next(r for r in broken["rows"] if r["dimension"] == name)
            broken["rows"] = [r for r in broken["rows"] if r["id"] != victim["id"]]
            resummarise(broken)
            bad = pm.check_ratchet(broken, ratchet)
            self.assertIn("RATCHET: dimension %s has" % name, joined(bad))

    def test_the_deletion_is_invisible_to_every_other_check(self):
        """The header agrees with the body, so only the external file catches it."""
        manifest, ratchet = load()
        broken = copy.deepcopy(manifest)
        victim = next(r for r in broken["rows"]
                      if r["dimension"] == "test_corpus")
        broken["rows"] = [r for r in broken["rows"] if r["id"] != victim["id"]]
        resummarise(broken)
        self.assertEqual(pm.verify(broken), [])
        self.assertEqual(pm.check_shape(broken), [])
        self.assertEqual(pm.check_dimensions(broken), [])
        self.assertTrue(pm.check_ratchet(broken, ratchet))

    def test_the_total_floor_also_reddens(self):
        manifest, ratchet = load()
        broken = copy.deepcopy(manifest)
        broken["rows"] = broken["rows"][:-1]
        resummarise(broken)
        self.assertIn("below the committed floor of",
                      joined(pm.check_ratchet(broken, ratchet)))

    def test_the_committed_floors_match_the_committed_manifest(self):
        manifest, ratchet = load()
        self.assertEqual(ratchet["row_floor"],
                         manifest["counts"]["by_dimension"])
        self.assertEqual(ratchet["total_floor"], manifest["counts"]["rows"])


class SupersededRowTest(unittest.TestCase):

    def test_there_is_at_least_one_superseded_row(self):
        manifest, _ = load()
        rows = [r for r in manifest["rows"] if r["status"] == "superseded"]
        self.assertTrue(rows)

    def test_a_dangling_successor_reddens(self):
        manifest, ratchet = load()
        broken = copy.deepcopy(manifest)
        for row in broken["rows"]:
            if row["status"] == "superseded":
                row["superseded_by"] = "no:such:row"
        bad = pm.check(broken, ratchet, REPO, run_probes=False)
        self.assertIn("which is not a row", joined(bad))

    def test_a_successor_that_is_not_required_reddens(self):
        manifest, ratchet = load()
        broken = copy.deepcopy(manifest)
        successors = {r["superseded_by"] for r in broken["rows"]
                      if r["status"] == "superseded"}
        for row in broken["rows"]:
            if row["id"] in successors:
                row["status"] = "excluded"
        bad = pm.check(broken, ratchet, REPO, run_probes=False)
        self.assertIn("not required", joined(bad))


class SchemaMatrixTest(unittest.TestCase):

    def test_a_required_row_may_not_use_an_absence_probe(self):
        manifest, ratchet = load()
        broken = copy.deepcopy(manifest)
        broken["rows"][0]["verification"] = "absence_probe"
        resummarise(broken)
        bad = pm.check(broken, ratchet, REPO, run_probes=False)
        self.assertIn("may not use verification absence_probe", joined(bad))

    def test_an_unknown_status_or_verification_reddens(self):
        manifest, ratchet = load()
        for field, value in (("status", "probably-fine"),
                             ("verification", "eyeballed")):
            broken = copy.deepcopy(manifest)
            broken["rows"][0][field] = value
            bad = pm.check_shape(broken)
            self.assertIn("unknown %s" % field, joined(bad))

    def test_a_duplicate_row_id_reddens(self):
        manifest, ratchet = load()
        broken = copy.deepcopy(manifest)
        broken["rows"].append(copy.deepcopy(broken["rows"][0]))
        resummarise(broken)
        self.assertIn("duplicate row id", joined(pm.check_shape(broken)))

    def test_a_curated_row_without_a_mutation_is_prose_not_evidence(self):
        manifest, ratchet = load()
        for field in ("test", "mutation"):
            broken = copy.deepcopy(manifest)
            for row in broken["rows"]:
                if row["verification"] == "curated":
                    row["evidence"].pop(field, None)
            bad = pm.check_shape(broken)
            self.assertIn("curated evidence is missing %s" % field, joined(bad))

    def test_a_curated_row_must_name_the_plan_task_that_lands_it(self):
        manifest, ratchet = load()
        broken = copy.deepcopy(manifest)
        for row in broken["rows"]:
            if row["verification"] == "curated":
                row.pop("task")
        self.assertIn("is missing task", joined(pm.check_shape(broken)))

    def test_an_invalid_side_reddens(self):
        manifest, ratchet = load()
        broken = copy.deepcopy(manifest)
        broken["rows"][0]["sides"] = ["upstream"]
        self.assertIn("invalid sides", joined(pm.check_shape(broken)))

    def test_a_generated_row_whose_enforcing_gate_is_gone_reddens(self):
        manifest, ratchet = load()
        broken = copy.deepcopy(manifest)
        for dim in broken["dimensions"]:
            if dim.get("gate"):
                dim["gate"] = "tests/_deleted_by_a_refactor.py"
        bad = pm.check(broken, ratchet, REPO, run_probes=False)
        self.assertIn("names a missing gate", joined(bad))

    def test_a_header_count_contradicting_the_body_reddens(self):
        manifest, _ = load()
        broken = copy.deepcopy(manifest)
        broken["counts"]["rows"] += 1
        self.assertIn("contradicts the body", joined(pm.verify(broken)))


class PendingRatchetTest(unittest.TestCase):
    """The shrink-only backlog has to bite in BOTH directions."""

    def test_a_row_with_no_test_and_no_pending_entry_reddens(self):
        manifest, ratchet = load()
        thinned = dict(ratchet, pending_verification=[])
        bad = pm.check_curated(manifest, REPO, thinned)
        self.assertIn("does not exist and is not in pending_verification",
                      joined(bad))

    def test_a_pending_row_that_now_resolves_must_be_removed(self):
        manifest, ratchet = load()
        resolved = next(r for r in manifest["rows"]
                        if r["verification"] == "curated"
                        and r["id"] not in ratchet["pending_verification"])
        widened = dict(ratchet,
                       pending_verification=sorted(
                           ratchet["pending_verification"] + [resolved["id"]]))
        bad = pm.check_curated(manifest, REPO, widened)
        self.assertIn("remove it from pending_verification", joined(bad))

    def test_a_pending_entry_that_is_not_a_row_reddens(self):
        manifest, ratchet = load()
        widened = dict(ratchet,
                       pending_verification=ratchet["pending_verification"]
                       + ["ghost:row"])
        self.assertIn("which is not a row",
                      joined(pm.check_curated(manifest, REPO, widened)))

    def test_the_backlog_is_a_strict_subset_of_the_curated_rows(self):
        manifest, ratchet = load()
        curated = {r["id"] for r in manifest["rows"]
                   if r["verification"] == "curated"}
        self.assertTrue(set(ratchet["pending_verification"]) <= curated)
        self.assertLess(len(ratchet["pending_verification"]), len(curated))


class StampTest(unittest.TestCase):

    def test_stamping_the_ratchet_reproduces_the_committed_file(self):
        manifest, ratchet = load()
        stamped = pm.stamp_ratchet(manifest, REPO)
        for field in ("row_floor", "total_floor", "pending_verification"):
            self.assertEqual(stamped[field], ratchet[field], field)

    def test_the_ratchet_file_carries_no_absolute_home_path(self):
        with open(pm.RATCHET) as fh:
            body = fh.read()
        self.assertNotIn(os.path.expanduser("~"), body)
        json.loads(body)


if __name__ == "__main__":
    unittest.main()
