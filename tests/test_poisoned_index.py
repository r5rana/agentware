"""Tests for poisoned-index list-field hardening (260718).

A poisoned/imported index.json — one whose list-typed entry field (`relates` or
`tags`) is a non-list, or a list carrying non-string elements — must NEVER crash
the recall hot path (CLI + MCP), `audit`, `query`, or `index migrate-frontmatter`.
Readers are LENIENT (fail-open: skip the bad field, keep ranking); the dedicated
integrity gates (`validate_index`, `audit --graph_integrity`) are STRICT
(reject/report). One shared `_entry_list_field` normalizer is adopted at every raw
read site. Byte-identical on all valid data.

Stdlib unittest only; never touches the real KB. Deterministic + read-only.
"""

import copy
import json
import os

try:
    from tests._fixtures import (SyntheticKBTestCase, build_synthetic_kb,
                                 load_cli, run_cli)
except ImportError:  # allow `python3 -m unittest tests.test_poisoned_index`
    from _fixtures import (SyntheticKBTestCase, build_synthetic_kb,
                           load_cli, run_cli)


# --- Task 2: the shared normalizer (pure function) ---------------------------
class EntryListFieldTests(SyntheticKBTestCase):
    def test_entry_list_field(self):
        mod = load_cli()
        f = mod._entry_list_field

        # Value-equal (==, NOT identity) to the raw field for a list of strings.
        e = {"tags": ["a", "b", "c"], "relates": ["depends-on:x"]}
        self.assertEqual(f(e, "tags"), e["tags"])
        self.assertEqual(f(e, "relates"), e["relates"])
        # Returns a FRESH list, never the same object (no aliasing / mutation risk).
        self.assertIsNot(f(e, "tags"), e["tags"])

        # [] for every scalar / dict / None / missing.
        for bad in (5, True, 1.5, "hello", {"a": 1}, None):
            self.assertEqual(f({"tags": bad}, "tags"), [])
        self.assertEqual(f({}, "tags"), [])

        # Non-string elements are DROPPED on a mixed list (kills R2's unhashable
        # case: a dict element never reaches set()).
        self.assertEqual(f({"tags": ["ok", 5, {"x": 1}, "yes", None]}, "tags"),
                         ["ok", "yes"])
        self.assertEqual(f({"relates": ["a:b", {"x": 1}, "c:d"]}, "relates"),
                         ["a:b", "c:d"])
        # A tuple is accepted too (defensive), string elements only.
        self.assertEqual(f({"tags": ("a", 2, "b")}, "tags"), ["a", "b"])


# --- Task 3: lenient `relates` readers ---------------------------------------
# Every poisoned shape the design must survive WITHOUT raising.
POISON_RELATES = [5, True, 1.5, "supersedes:x", {"a": 1},
                  ["a:b", {"x": 1}], ["a:b", 5]]
# Scalar / string / dict shapes normalize to [] (the poison entry is fully
# skipped — no edges at all).
SCALAR_POISON = [5, True, 1.5, "supersedes:x", {"a": 1}]
# Mixed-list shapes keep the well-formed STRING token and drop the non-string
# element (this is the lenient contract — and it kills R2's unhashable-dict case
# because the dict never reaches `set()`).
MIXED_LIST_POISON = [["a:b", {"x": 1}], ["a:b", 5]]


class RelatesReaderTests(SyntheticKBTestCase):
    def _data(self, relates):
        """A 2-entry index: one poisoned, one with a VALID edge to the poisoned."""
        return {
            "entries": [
                {"id": "poison", "relates": relates},
                {"id": "good", "relates": ["depends-on:poison"]},
            ],
            "tags": {},
        }

    def test_build_adjacency_never_raises_and_good_edge_survives(self):
        mod = load_cli()
        for r in POISON_RELATES:
            forward, reverse = mod.build_adjacency(self._data(r))
            # The GOOD entry's edge still builds regardless of the poison.
            self.assertEqual(forward.get("good"), [("depends-on", "poison")])
            self.assertEqual(reverse.get("poison"), [("depends-on", "good")])

    def test_scalar_poison_fully_skipped(self):
        mod = load_cli()
        for r in SCALAR_POISON:
            forward, _ = mod.build_adjacency(self._data(r))
            # A scalar/string/dict relates yields NO forward edges for 'poison'.
            self.assertNotIn("poison", forward)

    def test_mixed_list_drops_non_string_keeps_valid_token(self):
        mod = load_cli()
        for r in MIXED_LIST_POISON:
            # The well-formed "a:b" string survives; the dict/int is dropped and
            # never reaches set() (no `unhashable type` crash).
            forward, _ = mod.build_adjacency(self._data(r))
            self.assertEqual(forward.get("poison"), [("a", "b")])
            # graph_relation_errors must ALSO not raise on the dict element.
            dangling, unknown = mod.graph_relation_errors(self._data(r))
            self.assertIn(("poison", "b"), dangling)   # 'b' is not an entry id
            self.assertIn(("poison", "a:b"), unknown)  # 'a' is not a known type

    def test_graph_relation_errors_never_raises(self):
        mod = load_cli()
        for r in POISON_RELATES:
            mod.graph_relation_errors(self._data(r))  # must not raise

    def test_graph_health_never_raises(self):
        mod = load_cli()
        for r in POISON_RELATES:
            mod.graph_health(self._data(r))  # must not raise

    def test_none_relates_equals_empty(self):
        # Regression-lock: a missing/None relates behaves as [] (byte-identical).
        mod = load_cli()
        f, r = mod.build_adjacency(self._data(None))
        g, s = mod.build_adjacency(self._data([]))
        self.assertEqual(f, g)
        self.assertEqual(r, s)

    def test_valid_relates_byte_identical(self):
        # A valid list-of-strings produces the exact same adjacency as before.
        mod = load_cli()
        data = {"entries": [
            {"id": "a", "relates": ["depends-on:b", "relates-to:c"]},
            {"id": "b", "relates": []},
            {"id": "c", "relates": []},
        ], "tags": {}}
        forward, _ = mod.build_adjacency(data)
        self.assertEqual(forward["a"], [("depends-on", "b"), ("relates-to", "c")])


# --- Task 4: lenient `tags` readers ------------------------------------------
POISON_TAGS = [5, True, "hello", {"a": 1}, ["ok", 5], None]
# Scalar / string / dict / None normalize to [] -> empty tag segment.
SCALAR_TAGS = [5, True, "hello", {"a": 1}, None]


class TagsReaderTests(SyntheticKBTestCase):
    def test_entry_doc_text_never_raises(self):
        mod = load_cli()
        for t in POISON_TAGS:
            entry = {"id": "p", "title": "T", "summary": "S", "tags": t}
            mod._entry_doc_text(self.kdir, entry)  # must not raise

    def test_scalar_tags_yield_empty_segment_no_corruption(self):
        mod = load_cli()
        for t in SCALAR_TAGS:
            entry = {"id": "p", "title": "T", "summary": "S", "tags": t}
            doc = mod._entry_doc_text(self.kdir, entry)
            # A string `tags` ("hello") must NOT contribute per-char tokens; the
            # tag segment (3rd line) is EMPTY for every scalar/dict/None shape.
            self.assertNotIn(" h e l l o", doc)
            self.assertEqual(doc.split("\n")[2], "")

    def test_mixed_list_tags_keep_valid_string(self):
        mod = load_cli()
        entry = {"id": "p", "title": "T", "summary": "S", "tags": ["ok", 5]}
        doc = mod._entry_doc_text(self.kdir, entry)
        self.assertEqual(doc.split("\n")[2], "ok")  # int dropped, "ok" survives

    def test_entry_doc_text_byte_identical_for_valid_tags(self):
        mod = load_cli()
        entry = {"id": "p", "title": "Title", "summary": "Sum",
                 "tags": ["alpha", "beta", "gamma"]}
        got = mod._entry_doc_text(self.kdir, entry)
        expected = "\n".join([
            "Title", "Sum", "alpha beta gamma",
            mod._read_entry_body(self.kdir, entry),
        ])
        self.assertEqual(got, expected)

    def test_build_corpus_never_raises_on_poisoned_tags(self):
        mod = load_cli()
        data = {"entries": [
            {"id": "p", "title": "T", "summary": "S", "tags": 5},
            {"id": "q", "title": "T2", "summary": "S2", "tags": ["ok", {"x": 1}]},
        ], "tags": {}}
        corpus = mod.build_corpus(self.kdir, data)  # must not raise
        self.assertEqual(len(corpus), 2)

    def test_tag_consistency_errors_int_scalar_backref_no_crash(self):
        # The tag MAP references 'poison', whose tags is an INT scalar — this
        # exercises the :9303 back-ref membership site. Must REPORT, not crash.
        mod = load_cli()
        data = {
            "entries": [{"id": "poison", "tags": 5}],
            "tags": {"realtag": ["poison"]},
        }
        errors = mod.tag_consistency_errors(data)  # must not raise
        self.assertTrue(any("back-ref" in e for e in errors))

    def test_pref_project_tags_never_raises(self):
        mod = load_cli()
        for t in POISON_TAGS:
            self.assertEqual(mod._pref_project_tags({"tags": t}), [])
        self.assertEqual(
            mod._pref_project_tags({"tags": ["project-alpha", 5, "other"]}),
            ["alpha"])


# --- Task 5: strict validator (fail-loud) ------------------------------------
class ValidatorStrictTests(SyntheticKBTestCase):
    def _validate(self, entries, tags=None):
        mod = load_cli()
        data = {"entries": entries, "tags": tags or {}}
        return mod.validate_index(self.kdir, data)  # returns error list

    def test_scalar_relates_rejected(self):
        errs = self._validate([{"id": "a", "relates": 5}])
        self.assertTrue(any("relates is not a list" in e for e in errs))

    def test_scalar_tags_rejected(self):
        errs = self._validate([{"id": "a", "tags": 5}])
        self.assertTrue(any("tags is not a list" in e for e in errs))

    def test_malformed_element_relates_rejected(self):
        errs = self._validate([{"id": "a", "relates": ["a:b", {"x": 1}]}])
        self.assertTrue(any("relates has a non-string element" in e
                            for e in errs))

    def test_malformed_element_tags_rejected(self):
        errs = self._validate([{"id": "a", "tags": ["ok", 5]}])
        self.assertTrue(any("tags has a non-string element" in e for e in errs))

    def test_validator_never_crashes_on_scalar_tags_backref(self):
        # Tag MAP references an entry whose tags is a scalar — the map->entry
        # back-ref loop must REPORT, not crash. (twin of tag_consistency_errors)
        errs = self._validate([{"id": "poison", "tags": 5}],
                              tags={"realtag": ["poison"]})
        self.assertTrue(any("tags is not a list" in e for e in errs))

    def test_valid_index_still_passes(self):
        # An all-string valid entry produces NO list-field errors.
        errs = self._validate(
            [{"id": "a", "category": "learnings", "tags": ["x", "y"],
              "relates": ["depends-on:b"]}])
        self.assertFalse(any("is not a list" in e or "non-string element" in e
                             for e in errs))

    def test_validate_cli_rejects_poison_but_passes_clean(self):
        # End-to-end via the CLI: a poisoned index exits 1 with clear errors; a
        # clean synthetic KB exits 0.
        mod = load_cli()
        code, out, err = run_cli(["index", "validate"], self.kdir)
        self.assertEqual(code, 0, (out, err))  # clean synthetic KB passes

        # Poison the on-disk index and re-validate via the CLI.
        idx_path = os.path.join(self.kdir, "index.json")
        with open(idx_path) as f:
            data = json.load(f)
        data["entries"][0]["relates"] = 5
        data["entries"][1]["tags"] = ["ok", {"bad": 1}]
        with open(idx_path, "w") as f:
            json.dump(data, f)
        code, out, err = run_cli(["index", "validate"], self.kdir)
        self.assertEqual(code, 1)
        blob = out + err
        self.assertIn("relates is not a list", blob)
        self.assertIn("tags has a non-string element", blob)


# --- Task 6: hermetic end-to-end (poisoned KB, degrade + recover) ------------
class PoisonedKBEndToEndTests(SyntheticKBTestCase):
    """A poisoned index.json must degrade gracefully across every surface and
    recover via `index rebuild`. The poisoned entry KEEPS its id in the tags MAP
    so the map->entry back-ref loops (validate_index + tag_consistency_errors)
    are actually reached."""

    def _poison(self):
        mod = load_cli()
        idx_path = os.path.join(self.kdir, "index.json")
        with open(idx_path) as f:
            data = json.load(f)
        # Entry 0 stays referenced in the tags map (real tags), but its own
        # `tags` becomes an INT scalar (NOT a string — a string is iterable and
        # would not trip the back-ref membership) and `relates` a scalar.
        e0 = data["entries"][0]
        e0["tags"] = 5
        e0["relates"] = 7
        # Entry 1: a malformed-ELEMENT relates list. Strip its file frontmatter
        # so `index migrate-frontmatter` actually rebuilds it (exercises R4/T2).
        e1 = data["entries"][1]
        e1["relates"] = ["a:b", {"bad": 1}]
        with open(idx_path, "w") as f:
            json.dump(data, f, indent=2)
        p1 = os.path.join(self.kdir, e1["path"])
        with open(p1) as f:
            _fields, body = mod.split_frontmatter(f.read())
        with open(p1, "w") as f:
            f.write(body or "# macos\n")
        return e0["id"], e1["id"]

    def test_end_to_end_degrade_and_recover(self):
        mod = load_cli()
        poisoned_id, rel_id = self._poison()

        # (1) validate CATCHES the poison (exit 1, clear per-entry errors).
        code, out, err = run_cli(["index", "validate"], self.kdir)
        self.assertEqual(code, 1)
        blob = out + err
        self.assertIn("tags is not a list", blob)
        self.assertIn("relates is not a list", blob)
        self.assertIn("relates has a non-string element", blob)

        # (2) recall (CLI) returns results WITHOUT a traceback.
        code, out, err = run_cli(
            ["recall", "geofence macos python ranking", "--format", "json"],
            self.kdir)
        self.assertNotIn("Traceback", err)
        payload = json.loads(out)
        self.assertGreater(payload.get("count", 0), 0)

        # (3) MCP recall builds a payload without raising.
        data, lerr = mod.load_index(self.kdir)
        self.assertIsNone(lerr)
        mcp_out = mod._mcp_tool_recall(self.kdir, data,
                                       {"query": "geofence reminders"})
        self.assertIn("results", json.loads(mcp_out))

        # (4) audit completes and REPORTS the tag_consistency back-ref (no crash).
        code, out, err = run_cli(["audit", "--format", "json"], self.kdir)
        self.assertNotIn("Traceback", err)
        errs = mod.tag_consistency_errors(data)
        self.assertTrue(any("back-ref" in e for e in errs))

        # (5) query --relates completes without a traceback.
        code, out, err = run_cli(["query", "--relates", rel_id], self.kdir)
        self.assertNotIn("Traceback", err)

        # (6) index migrate-frontmatter completes (rebuilds the stripped file's
        # frontmatter from the poisoned relates list, dropping the dict — R4/T2).
        code, out, err = run_cli(["index", "migrate-frontmatter"], self.kdir)
        self.assertNotIn("Traceback", err)
        self.assertEqual(code, 0, (out, err))

        # (7) index rebuild RECOVERS — the derived index is valid again.
        code, out, err = run_cli(["index", "rebuild"], self.kdir)
        self.assertEqual(code, 0, (out, err))
        code, out, err = run_cli(["index", "validate"], self.kdir)
        self.assertEqual(code, 0, (out, err))

        # (8) recall on the recovered KB is clean.
        code, out, err = run_cli(
            ["recall", "geofence reminders", "--format", "json"], self.kdir)
        self.assertNotIn("Traceback", err)
        self.assertEqual(code, 0, (out, err))


# --- Feature 260813 task 38: the ported `relate` WRITER on a poisoned index ---
class RelatePoisonTests(SyntheticKBTestCase):
    """`relate` is the first ported verb that both READS the index and WRITES it.

    Its raw `relates` reads are exempted in `_indexreader_gate.RAW_READ_EXEMPT`
    because they are frontmatter parser products, not loaded entries — and that
    exemption is FUNCTION-scoped, so it would also cover a future un-normalized
    entry read in the same function. This class is the other half of the residue
    argument the gate's docstring makes: feed the poison to the real CLI and prove
    the surface degrades rather than crashing.

    The contract is stronger than "does not crash": the `.md` frontmatter is the
    source of truth, so declaring an edge must RECONCILE the poisoned index row to
    the frontmatter-derived list — i.e. `relate` HEALS the poison it meets.
    """

    def _entry_ids(self):
        with open(os.path.join(self.kdir, "index.json")) as f:
            data = json.load(f)
        return [e["id"] for e in data["entries"]][:2]

    def _poison_relates(self, entry_id, value):
        path = os.path.join(self.kdir, "index.json")
        with open(path) as f:
            data = json.load(f)
        for e in data["entries"]:
            if e["id"] == entry_id:
                e["relates"] = value
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def _relates_of(self, entry_id):
        with open(os.path.join(self.kdir, "index.json")) as f:
            data = json.load(f)
        for e in data["entries"]:
            if e["id"] == entry_id:
                return e.get("relates")
        return None

    def test_relate_survives_every_poison_shape_and_heals_the_row(self):
        subject, target = self._entry_ids()
        token = "supersedes:%s" % target
        for poison in POISON_RELATES:
            with self.subTest(poison=poison):
                self._poison_relates(subject, poison)
                code, out, err = run_cli(
                    ["relate", subject, "--supersedes", target], self.kdir)
                self.assertNotIn("Traceback", err)
                self.assertEqual(code, 0, (out, err))
                # The poisoned row is reconciled to the frontmatter edge set: the
                # declared token is present and every element is a string again.
                relates = self._relates_of(subject)
                self.assertIsInstance(relates, list)
                self.assertTrue(all(isinstance(x, str) for x in relates))
                self.assertIn(token, relates)
                # Reset the .md so each shape starts from a clean edge set.
                run_cli(["index", "rebuild"], self.kdir)

    def test_relate_leaves_a_validatable_index_after_healing(self):
        subject, target = self._entry_ids()
        self._poison_relates(subject, {"bad": "mapping"})
        code, out, err = run_cli(
            ["relate", subject, "--supersedes", target], self.kdir)
        self.assertEqual(code, 0, (out, err))
        # validate must now PASS on the field relate rewrote (the strict gate is
        # what proves the leniency healed rather than merely masked).
        code, out, err = run_cli(["index", "validate"], self.kdir)
        blob = out + err
        self.assertNotIn("relates is not a list", blob)
        self.assertNotIn("relates has a non-string element", blob)


if __name__ == "__main__":
    import unittest
    unittest.main()
