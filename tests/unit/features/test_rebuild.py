"""Parity + idempotence guard tests for `index rebuild`.

Builds a synthetic KB, migrates frontmatter into it, then proves rebuild derives
the index from frontmatter deterministically: it reflects edits, is byte-stable,
drops deleted entries, and never invents or loses ids. Stdlib unittest only.
"""

import json
import os

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # allow `python3 -m unittest tests.unit.features.test_rebuild`
    from _fixtures import SyntheticKBTestCase, load_cli


class RebuildTests(SyntheticKBTestCase):
    def setUp(self):
        super().setUp()
        # `author` default resolves from MAIN.md; migrate so entry files carry
        # frontmatter (the rebuild source of truth).
        with open(os.path.join(self.kdir, "MAIN.md"), "w", encoding="utf-8") as f:
            f.write("# KB\n\n- **Handle**: testhandle\n")
        code, _, err = self.run_cli(["index", "migrate-frontmatter"])
        self.assertEqual(code, 0, err)
        # Normalize once so subsequent rebuilds are no-ops.
        code, _, err = self.run_cli(["index", "rebuild"])
        self.assertEqual(code, 0, err)

    @property
    def index_file(self):
        return os.path.join(self.kdir, "index.json")

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_rebuild_is_byte_identical_when_unchanged(self):
        # The `generation` token bumps on every save (B1, P1.7) and index.json is
        # now DERIVED + gitignored, so whole-file byte-identity no longer matters
        # for git. The RANKING invariant is entry ORDER + CONTENT, which stays
        # byte-identical (generation is the ONE tolerated top-level diff).
        first = json.loads(self._read(self.index_file))
        feats_first = self._read(os.path.join(self.kdir, "FEATURES.md"))
        code, _, err = self.run_cli(["index", "rebuild"])
        self.assertEqual(code, 0, err)
        after = json.loads(self._read(self.index_file))
        self.assertEqual(after["entries"], first["entries"])
        self.assertEqual(after["tags"], first["tags"])
        # FEATURES.md carries no generation, so it stays byte-identical.
        self.assertEqual(self._read(os.path.join(self.kdir, "FEATURES.md")), feats_first)

    def test_rebuild_reflects_frontmatter_mutation(self):
        mod = load_cli()
        path = os.path.join(self.kdir, "learnings", "macos-no-timeout.md")
        fields, body = mod.split_frontmatter(self._read(path))
        self.assertIsNotNone(fields)
        fields["summary"] = "MUTATED zzz summary token"
        with open(path, "w", encoding="utf-8") as f:
            f.write(mod.render_frontmatter(fields) + body)
        code, _, err = self.run_cli(["index", "rebuild"])
        self.assertEqual(code, 0, err)
        row = next(e for e in self.read_index()["entries"]
                   if e["id"] == "learn-macos-no-timeout")
        self.assertEqual(row["summary"], "MUTATED zzz summary token")

    def test_rebuild_reflects_tag_mutation_in_tags_map(self):
        mod = load_cli()
        path = os.path.join(self.kdir, "references", "bm25-ranking.md")
        fields, body = mod.split_frontmatter(self._read(path))
        fields["tags"] = ["brandnewtag"]
        with open(path, "w", encoding="utf-8") as f:
            f.write(mod.render_frontmatter(fields) + body)
        code, _, err = self.run_cli(["index", "rebuild"])
        self.assertEqual(code, 0, err)
        data = self.read_index()
        self.assertIn("brandnewtag", data["tags"])
        self.assertEqual(data["tags"]["brandnewtag"], ["ref-bm25-ranking"])
        # Old tags for that entry are gone (derived purely from frontmatter).
        self.assertNotIn("bm25", data["tags"])

    def test_rebuild_drops_exactly_the_deleted_entry(self):
        before = {e["id"] for e in self.read_index()["entries"]}
        os.remove(os.path.join(self.kdir, "configurations", "python-runtime.md"))
        code, _, err = self.run_cli(["index", "rebuild"])
        self.assertEqual(code, 0, err)
        after = {e["id"] for e in self.read_index()["entries"]}
        self.assertEqual(before - after, {"config-python-runtime"})

    def test_rebuild_never_invents_or_loses_ids(self):
        mod = load_cli()
        disk_ids = set()
        for sub in ("learnings", "projects", "configurations", "prompts",
                    "references", "skills"):
            d = os.path.join(self.kdir, sub)
            if not os.path.isdir(d):
                continue
            for root, _dirs, files in os.walk(d):
                for fn in files:
                    if not fn.endswith(".md"):
                        continue
                    rel = os.path.relpath(os.path.join(root, fn), self.kdir)
                    rel = rel.replace(os.sep, "/")
                    if not mod._is_sync_candidate(rel):
                        continue
                    fm = mod.read_entry_frontmatter(os.path.join(self.kdir, rel))
                    if fm.get("id"):
                        disk_ids.add(fm["id"])
        index_ids = {e["id"] for e in self.read_index()["entries"]}
        self.assertEqual(index_ids, disk_ids)

    def test_validate_passes_after_rebuild(self):
        code, _, err = self.run_cli(["index", "validate"])
        self.assertEqual(code, 0, err)

    # --- Regression lock: `decisions` must round-trip through rebuild ----------
    # (feature 260710-learning-repair-p1; KB learning
    # `learn-index-rebuild-drops-decision-entries`). Before the fix, `decisions`
    # was a valid CATEGORIES value that `decide` wrote to the index but was
    # ABSENT from KNOWLEDGE_SUBDIRS, so scan_entry_files never walked it and every
    # decision was silently evicted on the next `index rebuild`.

    def _write_decision(self, stem, eid):
        mod = load_cli()
        ddir = os.path.join(self.kdir, "decisions")
        os.makedirs(ddir, exist_ok=True)
        fm = {
            "id": eid,
            "title": stem.replace("-", " ").title(),
            "category": "decisions",
            "tags": ["decision", "test"],
            "created": "2026-07-10",
            "summary": "a decision that must survive index rebuild",
            "author": "testhandle",
            "source": "agent",
            "last_verified": "2026-07-10",
        }
        with open(os.path.join(ddir, stem + ".md"), "w", encoding="utf-8") as f:
            f.write(mod.render_frontmatter(fm) + "# %s\n\nbody\n" % stem)

    def test_decisions_is_an_indexed_knowledge_category(self):
        mod = load_cli()
        self.assertIn("decisions", mod.KNOWLEDGE_SUBDIRS)

    def test_rebuild_preserves_a_decision_entry(self):
        self._write_decision("rebuild-roundtrip", "decide-rebuild-roundtrip")
        code, _, err = self.run_cli(["index", "rebuild"])
        self.assertEqual(code, 0, err)
        ids = {e["id"] for e in self.read_index()["entries"]}
        self.assertIn("decide-rebuild-roundtrip", ids)

    def test_rebuild_is_entry_count_preserving_per_category(self):
        """`index rebuild` must derive exactly the on-disk entry set for EVERY
        KNOWLEDGE_SUBDIRS category — never drop a whole category (the decisions
        eviction bug). Locks per-category count parity between disk and index."""
        mod = load_cli()
        self._write_decision("count-parity", "decide-count-parity")
        code, _, err = self.run_cli(["index", "rebuild"])
        self.assertEqual(code, 0, err)
        disk = {}
        for sub in mod.KNOWLEDGE_SUBDIRS:
            d = os.path.join(self.kdir, sub)
            if not os.path.isdir(d):
                continue
            for root, _dirs, files in os.walk(d):
                for fn in files:
                    if not fn.endswith(".md"):
                        continue
                    rel = os.path.relpath(os.path.join(root, fn), self.kdir)
                    rel = rel.replace(os.sep, "/")
                    if not mod._is_sync_candidate(rel):
                        continue
                    fm = mod.read_entry_frontmatter(os.path.join(self.kdir, rel))
                    if fm.get("id"):
                        cat = fm.get("category") or mod._infer_category(rel)
                        disk[cat] = disk.get(cat, 0) + 1
        idx = {}
        for e in self.read_index()["entries"]:
            idx[e["category"]] = idx.get(e["category"], 0) + 1
        self.assertEqual(idx, disk)
        self.assertGreaterEqual(idx.get("decisions", 0), 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
