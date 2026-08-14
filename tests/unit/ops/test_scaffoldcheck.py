"""Owning unit file for `agentware_lib.ops.scaffoldcheck` (R-MOD-06).

Feature 260813-modular-core-adoption, task 73.

Each check is proven to BITE by planting exactly the gap it exists to find in a
synthetic tree, one gap at a time, and asserting the finding names that gap by
kind and by location. A check asserted only against the live tree — which is
whole — would pass whether or not it looked at anything.

The live tree gets one assertion of its own, and it is the CI claim: with every
gap either closed or DECLARED in a shrink-only ledger, `audit()` is green. That
is what makes `scaffold check` runnable in a pipeline rather than a report
somebody reads.
"""

from tests._paths import REPO_ROOT

import json
import os
import shutil
import sys
import tempfile
import unittest

if os.path.join(REPO_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import agentware_lib.ops.scaffoldcheck as sc  # noqa: E402
import agentware_lib.ops.scaffoldunit as su  # noqa: E402


def _module_entry(dotted, layer, stem, verbs=None):
    return {"name": dotted, "layer": layer,
            "path": "scripts/agentware_lib/%s/%s.py" % (layer, stem),
            "test_file": "tests/unit/%s/test_%s.py" % (layer, stem),
            "verbs": verbs or []}


class SyntheticTreeTestCase(unittest.TestCase):
    """A whole, wired tree of two modules — every planted gap starts from green."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-scaffold-check-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.entries = [
            _module_entry("agentware_lib.ops.widgetone", "ops", "widgetone"),
            _module_entry("agentware_lib.ops.widgettwo", "ops", "widgettwo",
                          [{"verb": "widget", "order": 100,
                            "attr": "register_widget"}]),
        ]
        self.declared = {("widget", "agentware_lib.ops.widgettwo")}
        for entry in self.entries:
            self._write(entry["path"], "# module\n")
            self._write(entry["test_file"], "# test\n")
        self._write_manifest()
        self._write_readme([e["name"] for e in self.entries])
        self._write_parity(["surface:widget"])
        self._write_json(sc.TEST_DEBT_REL, {"rows": []})
        self._write_json(sc.DOC_DEBT_REL, {"rows": []})

    # -- scratch-tree helpers -------------------------------------------------

    def _write(self, rel, text):
        dest = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(text)
        return dest

    def _write_json(self, rel, payload):
        return self._write(rel, json.dumps(payload, indent=1))

    def _write_manifest(self):
        self._write_json(su.CATALOG_REL, {"modules": self.entries,
                                          "layer_direction": {"ops": ["core"]}})

    def _write_readme(self, names):
        rows = "".join("| `%s` | ops | owns | `t` |\n" % n for n in names)
        self._write(su.README_REL,
                    "# router\n\n| Module | Layer | Owns | Test file |\n"
                    "|---|---|---|---|\n" + rows)

    def _write_parity(self, ids):
        self._write_json(sc.PARITY_REL,
                         {"rows": [{"id": i} for i in ids] or [{"id": "seed"}]})

    def _audit(self):
        return sc.audit(self.tmp, declared=self.declared)

    def _kinds(self, result):
        return sorted({f["kind"] for f in result["findings"]})

    def _locations(self, result, kind):
        return sorted(f["location"] for f in result["findings"]
                      if f["kind"] == kind)


class WholeTreeIsGreenTest(SyntheticTreeTestCase):

    def test_a_fully_wired_tree_has_no_findings(self):
        result = self._audit()
        self.assertTrue(result["ok"], result["findings"])
        self.assertEqual(result["modules"], 2)

    def test_every_finding_kind_is_counted_even_at_zero(self):
        counts = self._audit()["counts"]
        self.assertEqual(sorted(counts), sorted(sc.FINDING_KINDS))
        self.assertEqual(set(counts.values()), {0})


class ManifestEntryTest(SyntheticTreeTestCase):

    def test_a_module_with_no_manifest_entry_is_named(self):
        self._write("scripts/agentware_lib/ops/widgetthree.py", "# hand-written\n")
        result = self._audit()
        self.assertFalse(result["ok"])
        self.assertEqual(self._locations(result, "manifest_entry_missing"),
                         ["scripts/agentware_lib/ops/widgetthree.py"])

    def test_a_manifest_entry_with_no_module_is_named(self):
        os.remove(os.path.join(self.tmp, self.entries[0]["path"]))
        result = self._audit()
        self.assertEqual(self._locations(result, "manifest_entry_stale"),
                         [self.entries[0]["path"]])

    def test_a_package_init_is_not_a_module(self):
        self._write("scripts/agentware_lib/ops/__init__.py", "")
        self.assertNotIn("manifest_entry_missing", self._kinds(self._audit()))

    def test_a_missing_manifest_is_one_loud_finding_not_a_silent_pass(self):
        os.remove(os.path.join(self.tmp, su.CATALOG_REL))
        result = self._audit()
        self.assertFalse(result["ok"])
        self.assertEqual(self._locations(result, "manifest_entry_missing"),
                         [su.CATALOG_REL])


class DeclaredVerbTest(SyntheticTreeTestCase):
    """Two witnesses: discovery IMPORTS, the manifest reads AST."""

    def test_a_declaration_the_manifest_does_not_publish_is_named(self):
        self.declared = self.declared | {("gadget", "agentware_lib.ops.widgetone")}
        result = self._audit()
        self.assertEqual(self._locations(result, "manifest_verb_missing"),
                         ["agentware_lib.ops.widgetone"])

    def test_a_manifest_verb_nothing_declares_is_named(self):
        self.declared = set()
        result = self._audit()
        self.assertIn("manifest_verb_missing", self._kinds(result))

    def test_the_live_tree_agrees_with_its_own_manifest(self):
        """Non-vacuity for the real tree: discovery and the AST see one list."""
        with open(os.path.join(REPO_ROOT, su.CATALOG_REL),
                  encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertEqual(sc.check_verbs(REPO_ROOT, manifest), [])


class OwningTestTest(SyntheticTreeTestCase):

    def test_a_module_with_no_owning_test_is_named(self):
        os.remove(os.path.join(self.tmp, self.entries[0]["test_file"]))
        result = self._audit()
        self.assertEqual(self._locations(result, "owning_test_missing"),
                         [self.entries[0]["path"]])

    def test_a_declared_debt_row_is_honoured(self):
        os.remove(os.path.join(self.tmp, self.entries[0]["test_file"]))
        self._write_json(sc.TEST_DEBT_REL, {"rows": [
            {"kind": "module_without_granular_test",
             "location": self.entries[0]["path"],
             "value": self.entries[0]["test_file"]}]})
        self.assertTrue(self._audit()["ok"])

    def test_the_debt_row_only_covers_the_module_it_names(self):
        for entry in self.entries:
            os.remove(os.path.join(self.tmp, entry["test_file"]))
        self._write_json(sc.TEST_DEBT_REL, {"rows": [
            {"kind": "module_without_granular_test",
             "location": self.entries[0]["path"], "value": "x"}]})
        result = self._audit()
        self.assertEqual(self._locations(result, "owning_test_missing"),
                         [self.entries[1]["path"]])


class ParityRowTest(SyntheticTreeTestCase):

    def test_a_verb_with_no_parity_row_is_named(self):
        self._write_parity(["surface:something-else"])
        result = self._audit()
        self.assertEqual(self._locations(result, "parity_row_missing"), ["widget"])

    def test_a_missing_parity_manifest_is_named(self):
        os.remove(os.path.join(self.tmp, sc.PARITY_REL))
        result = self._audit()
        self.assertEqual(self._locations(result, "parity_row_missing"),
                         [sc.PARITY_REL])

    def test_every_live_verb_carries_a_row_in_the_real_tree(self):
        """The claim that makes this check CI-usable: 45 verbs, 45 rows — the 44
        inherited ones generated from the union fixture, `scaffold` curated
        because a post-merge verb is in no fixture."""
        self.assertEqual(sc.check_parity(REPO_ROOT), [])


class DocRowTest(SyntheticTreeTestCase):

    def test_a_module_with_no_readme_row_is_named(self):
        self._write_readme([self.entries[0]["name"]])
        result = self._audit()
        self.assertEqual(self._locations(result, "doc_row_missing"),
                         [self.entries[1]["path"]])

    def test_a_declared_debt_row_is_honoured(self):
        self._write_readme([self.entries[0]["name"]])
        self._write_json(sc.DOC_DEBT_REL, {"rows": [
            {"kind": "module_without_doc_row",
             "location": self.entries[1]["path"],
             "value": self.entries[1]["name"]}]})
        self.assertTrue(self._audit()["ok"])

    def test_a_debt_row_the_tree_no_longer_produces_FAILS(self):
        """Shrink-only in BOTH directions: once the row is written, its debt row
        must go, or the ledger rots into an allowlist."""
        self._write_json(sc.DOC_DEBT_REL, {"rows": [
            {"kind": "module_without_doc_row",
             "location": self.entries[1]["path"],
             "value": self.entries[1]["name"]}]})
        result = self._audit()
        self.assertEqual(self._locations(result, "debt_row_stale"),
                         [self.entries[1]["path"]])

    def test_the_committed_debt_ledger_is_neither_stale_nor_short(self):
        with open(os.path.join(REPO_ROOT, su.CATALOG_REL),
                  encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertEqual(sc.check_docs(REPO_ROOT, manifest), [])

    def test_the_committed_debt_ledger_declares_its_own_row_count(self):
        with open(os.path.join(REPO_ROOT, sc.DOC_DEBT_REL), encoding="utf-8") as fh:
            data = json.load(fh)
        rows = list(data["rows"])
        for group in data["groups"]:
            rows.extend(group["rows"])
        self.assertEqual(data["ratchet"]["max_rows"], len(rows))
        self.assertTrue(rows)
        self.assertTrue(all(r["kind"] == "module_without_doc_row" for r in rows))
        self.assertTrue(all(g["owner"].startswith("task ") for g in data["groups"]))

    def test_the_group_flattening_agrees_with_the_corpus_loader(self):
        """This module cannot import the test corpus, so it re-implements the
        flattening — and a second implementation is only safe while something
        compares them. Reading only the top-level `rows` array of any of these
        fixtures yields an EMPTY exemption set and turns every declared row back
        into a finding."""
        sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))
        import _test_ownership as to  # noqa: E402
        self.assertEqual(
            sc.debt_locations(REPO_ROOT, sc.TEST_DEBT_REL),
            {row["location"] for row in to.load_debt()["rows"]})
        self.assertTrue(sc.debt_locations(REPO_ROOT, sc.TEST_DEBT_REL))


class LiveTreeTest(unittest.TestCase):
    """The CI claim itself."""

    def test_the_live_tree_passes_its_own_audit(self):
        result = sc.audit()
        self.assertTrue(result["ok"],
                        "scaffold check would fail CI: %s"
                        % [(f["kind"], f["location"]) for f in result["findings"]])

    def test_the_audit_reports_every_module_it_walked(self):
        self.assertGreater(sc.audit()["modules"], 100)


if __name__ == "__main__":
    unittest.main()
