"""Task 2 (P1.7) — sort-once rebuild (B4).

`build_index_from_frontmatter` sorts collected rows ONCE (O(n log n)) instead of
an insert-per-file linear scan (O(n^2)). Proves:
  (a) at >=2,000 entries the produced `entries` list is BYTE-IDENTICAL to a
      reference incremental-insert (`insert_entry_sorted`) build — same order,
      same content (generation-agnostic: build_index_from_frontmatter never adds
      the top-level generation field, which save_index owns);
  (b) a single sort pass — `_entry_key` is evaluated exactly once per entry (what
      list.sort guarantees) and `insert_entry_sorted` is NEVER called during a
      rebuild (no O(n^2) scan).

Stdlib-only unittest; throwaway temp KB (R-LOC-03).
"""

import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import load_cli
except ImportError:
    from _fixtures import load_cli


CLI = load_cli()

N_ENTRIES = 2200  # >= 2,000 per the plan


def _build_large_fm_kb(root, n=N_ENTRIES):
    """Materialize `n` entry files with canonical frontmatter across categories.

    IDs/categories are deliberately UNSORTED on disk (scan_entry_files yields a
    deterministic-but-unsorted-by-key order) so the sort actually has work to do.
    """
    cats = ("learnings", "references", "configurations", "projects")
    for sub in cats:
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    # Interleave categories + non-monotonic ids so neither the scan order nor the
    # id order is already sorted by (category, id).
    for i in range(n):
        cat = cats[i % len(cats)]
        # Reverse-ish id ordering within a category to force real reordering.
        eid = "%s-e%05d" % (cat[:4], (n - i))
        stem = "%s-%05d" % (cat[:4], (n - i))
        fields = {
            "id": eid,
            "title": "Entry %d" % i,
            "category": cat,
            "tags": ["scale", cat, "t%d" % (i % 7)],
            "created": "2026-01-%02d" % ((i % 28) + 1),
            "summary": "Synthetic entry %d for the sort-once guard." % i,
            "author": "test",
            "source": "agent",
            "last_verified": "2026-02-%02d" % ((i % 28) + 1),
        }
        path = os.path.join(root, cat, stem + ".md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(CLI.render_frontmatter(fields) + "# %s\n\nbody %d\n" % (eid, i))


def _reference_insert_order(kdir):
    """Rebuild `entries` the OLD way (insert_entry_sorted per file) for parity."""
    entries = []
    for rel in CLI.scan_entry_files(kdir):
        fm = CLI.read_entry_frontmatter(os.path.join(kdir, rel))
        eid = fm.get("id")
        if not eid:
            continue
        category = fm.get("category") or CLI._infer_category(rel)
        created = fm.get("created") or CLI._today()
        row = {
            "id": eid,
            "title": fm.get("title") or "",
            "category": category,
            "path": rel,
            "tags": list(fm.get("tags") or []),
            "created": created,
            "summary": fm.get("summary") or "",
            "last_verified": fm.get("last_verified") or created,
        }
        relates = list(fm.get("relates") or [])
        if relates:
            row["relates"] = relates
        CLI.insert_entry_sorted(entries, row)
    return entries


class SortOnceTest(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-sortonce-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        _build_large_fm_kb(self.kdir)

    def test_sortonce_entries_byte_identical_to_insert_order(self):
        data, errors = CLI.build_index_from_frontmatter(self.kdir)
        self.assertEqual(errors, [], errors)
        got = data["entries"]
        ref = _reference_insert_order(self.kdir)
        self.assertEqual(len(got), N_ENTRIES)
        # Byte-identical: same order AND same content, entry-by-entry.
        self.assertEqual(got, ref)
        # And it IS fully (category, id)-sorted (total order, unique keys).
        keys = [(e["category"], e["id"]) for e in got]
        self.assertEqual(keys, sorted(keys))

    def test_sortonce_tags_map_matches_insert_order(self):
        data, _errs = CLI.build_index_from_frontmatter(self.kdir)
        # Tags map is rebuilt from the sorted entries; reconstruct independently.
        ref_tags = {}
        for e in _reference_insert_order(self.kdir):
            CLI.add_tags_to_map(ref_tags, e["id"], e["tags"])
        self.assertEqual(data["tags"], ref_tags)

    def test_sortonce_single_sort_pass_no_quadratic(self):
        # A single list.sort evaluates the key EXACTLY once per entry; the old
        # insert path evaluated _entry_key O(n^2) times and called
        # insert_entry_sorted once per file.
        key_calls = [0]
        insert_calls = [0]
        real_key = CLI._entry_key
        real_insert = CLI.insert_entry_sorted

        def counting_key(entry):
            key_calls[0] += 1
            return real_key(entry)

        def counting_insert(entries, entry):
            insert_calls[0] += 1
            return real_insert(entries, entry)

        CLI._entry_key = counting_key
        CLI.insert_entry_sorted = counting_insert
        try:
            data, errors = CLI.build_index_from_frontmatter(self.kdir)
        finally:
            CLI._entry_key = real_key
            CLI.insert_entry_sorted = real_insert
        self.assertEqual(errors, [], errors)
        self.assertEqual(len(data["entries"]), N_ENTRIES)
        # list.sort decorates once → exactly one key eval per entry.
        self.assertEqual(key_calls[0], N_ENTRIES)
        # The quadratic insert path is never taken on a rebuild.
        self.assertEqual(insert_calls[0], 0)


if __name__ == "__main__":
    unittest.main()
